"""Download every registered source and merge the results into one pool.

Efficiency features
-------------------
* GitHub-hosted lists are fetched from ``raw.githubusercontent.com`` first; CDN
  mirrors are only tried when that fails (never when the file simply is gone).
* Paged APIs/sites are walked page by page and stop at the first empty page.
* ``aiohttp`` enforces a per-host connection limit so no host gets hammered.
* A health tracker backs off from sources that keep failing and reports the
  ones that should be pruned from the discovered registry.
* Memory-light candidate pool: one packed integer per ``ip:port``.
"""
from __future__ import annotations

import asyncio
import json
import os
import random
import time
from dataclasses import dataclass, field
from pathlib import Path

import aiohttp

from common import ALL_MASK, PROTOCOL_BITS, split_proxy
from parser import parse_payload
from sources import Source

USER_AGENT = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/124.0 Safari/537.36")
BIG_PAYLOAD = 256 * 1024  # parse bigger payloads in a worker thread


# ------------------------------------------------------------------ pool


class Pool:
    """Candidate set: ``ip:port`` -> packed ``(listings << 4) | unknown << 3 | mask``.

    ``mask`` is the union of protocols that sources explicitly claimed.
    ``unknown`` is set when at least one source listed the proxy without saying
    which protocol it speaks.
    """

    __slots__ = ("_data",)

    def __init__(self) -> None:
        self._data: dict[str, int] = {}

    def __len__(self) -> int:
        return len(self._data)

    def __contains__(self, proxy: str) -> bool:
        return proxy in self._data

    def add_many(self, found: dict[str, int]) -> None:
        data = self._data
        for proxy, mask in found.items():
            packed = data.get(proxy, 0)
            unknown = 8 if mask == 0 else packed & 8
            data[proxy] = (((packed >> 4) + 1) << 4) | unknown | (packed & 7) | mask

    def add_known(self, proxy: str) -> None:
        """Make sure a previously verified proxy is re-checked even when no
        source lists it any more."""
        packed = self._data.get(proxy, 0)
        if not packed >> 4:
            self._data[proxy] = packed | (1 << 4)

    def dump(self, path: str | Path) -> None:
        """Cache the pool (``--skip-scrape`` reuses it)."""
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._data, separators=(",", ":")))
        os.replace(tmp, target)

    @classmethod
    def load(cls, path: str | Path) -> "Pool":
        pool = cls()
        try:
            data = json.loads(Path(path).read_text())
            pool._data = {str(k): int(v) for k, v in data.items()}
        except (ValueError, OSError, AttributeError):
            pool._data = {}
        return pool

    def listings(self, proxy: str) -> int:
        return self._data.get(proxy, 0) >> 4

    def mask(self, proxy: str) -> int:
        return self._data.get(proxy, 0) & 7

    def ranked(self, previous: dict[str, int] | None = None, *, seed: int = 0,
               limit: int = 0) -> list[tuple[str, int, int]]:
        """Best candidates first as ``(proxy, test_mask, fallback_mask)``.

        * Proxies that were alive in the previous run go first.
        * Then proxies listed by many independent sources.
        * A proxy with an explicit protocol is tested for that protocol first;
          the other protocols are only tried if it does not understand it.
        """
        previous = previous or {}
        rng = random.Random(seed)
        scored: list[tuple[float, str, int, int]] = []
        for proxy, packed in self._data.items():
            known = previous.get(proxy, 0)
            claimed = (packed & 7) | known
            score = (1000 if known else 0) + min(packed >> 4, 10) * 10 + (5 if claimed else 0)
            score += rng.random() * 4  # shuffle ties so no source is systematically favoured
            test = claimed or ALL_MASK
            scored.append((-score, proxy, test, ALL_MASK & ~test))
        scored.sort()
        if limit:
            scored = scored[:limit]
        return [(proxy, test, fallback) for _, proxy, test, fallback in scored]


# ---------------------------------------------------------------- health


class Health:
    """Remember which sources keep failing so they can be skipped or pruned.

    Only bad sources are stored: a source that works leaves no trace.
    After ``SKIP_AFTER`` consecutive bad runs a source is skipped for an
    exponentially growing number of runs (capped at ``MAX_SKIP``). Discovered
    sources that stay bad for ``PRUNE_AFTER`` runs are reported for removal.
    """

    SKIP_AFTER = 3
    MAX_SKIP = 16
    PRUNE_AFTER = 12

    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path) if path else None
        self.runs = 0
        self._bad: dict[str, list[int]] = {}  # key -> [consecutive_bad, skip_left]
        if self.path and self.path.exists():
            try:
                raw = json.loads(self.path.read_text())
                self.runs = int(raw.get("runs", 0))
                self._bad = {k: [int(v[0]), int(v[1])] for k, v in raw.get("bad", {}).items()}
            except (ValueError, OSError, KeyError, IndexError, TypeError):
                self._bad = {}

    def should_skip(self, key: str) -> bool:
        entry = self._bad.get(key)
        if entry and entry[1] > 0:
            entry[1] -= 1
            return True
        return False

    def record(self, key: str, ok: bool) -> None:
        if ok:
            self._bad.pop(key, None)
            return
        entry = self._bad.setdefault(key, [0, 0])
        entry[0] += 1
        if entry[0] >= self.SKIP_AFTER:
            entry[1] = min(2 ** (entry[0] - self.SKIP_AFTER), self.MAX_SKIP)

    def prunable(self) -> set[str]:
        return {k for k, (bad, _) in self._bad.items() if bad >= self.PRUNE_AFTER}

    def forget(self, keys: set[str]) -> None:
        for key in keys:
            self._bad.pop(key, None)

    def bad_count(self) -> int:
        return len(self._bad)

    def save(self) -> None:
        if not self.path:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"runs": self.runs + 1, "bad": self._bad},
                                  separators=(",", ":")))
        os.replace(tmp, self.path)


# --------------------------------------------------------------- scraper


@dataclass(slots=True)
class Outcome:
    key: str
    state: str          # ok | empty | failed | skipped
    count: int = 0
    size: int = 0
    seconds: float = 0.0


@dataclass(slots=True)
class ScrapeReport:
    pool: Pool
    outcomes: list[Outcome] = field(default_factory=list)
    outage: bool = False
    stopped_by_budget: bool = False

    def count(self, state: str) -> int:
        return sum(1 for o in self.outcomes if o.state == state)

    def stats(self) -> dict[str, int]:
        return {
            "sources": len(self.outcomes),
            "sources_ok": self.count("ok"),
            "sources_empty": self.count("empty"),
            "sources_failed": self.count("failed"),
            "sources_skipped": self.count("skipped"),
            "candidates": len(self.pool),
        }


class Scraper:
    def __init__(self, sources: list[Source], *, concurrency: int = 200, per_host: int = 24,
                 timeout: float = 25.0, max_bytes: int = 24 * 1024 * 1024,
                 health: Health | None = None, deadline: float | None = None,
                 allow_private: bool = False, quiet: bool = False) -> None:
        self.sources = sources
        self.concurrency = concurrency
        self.per_host = per_host
        self.timeout = timeout
        self.max_bytes = max_bytes
        self.health = health
        self.deadline = deadline          # time.monotonic() value
        self.allow_private = allow_private
        self.quiet = quiet
        self._done = 0
        self._last_log = 0.0

    # -- network ---------------------------------------------------------

    async def _get(self, session: aiohttp.ClientSession, url: str, verify: bool) -> tuple[str, bytes]:
        """Return ``("ok", body)``, ``("gone", b"")`` or ``("retry", b"")``."""
        try:
            async with session.get(url, ssl=verify) as response:
                if response.status == 200:
                    body = bytearray()
                    async for chunk in response.content.iter_chunked(65536):
                        body += chunk
                        if len(body) >= self.max_bytes:
                            # cut exactly at the cap, on a line boundary, so a
                            # half-downloaded entry never becomes a candidate
                            cut = bytes(body[:self.max_bytes])
                            return "ok", cut.rpartition(b"\n")[0] or cut
                    return "ok", bytes(body)
                if response.status in (404, 410, 451):
                    return "gone", b""
                return "retry", b""
        except (aiohttp.ClientError, asyncio.TimeoutError, OSError, ValueError):
            return "retry", b""

    async def _parse(self, body: bytes, hint: str) -> dict[str, int]:
        if len(body) > BIG_PAYLOAD:
            return await asyncio.to_thread(parse_payload, body, hint,
                                           allow_private=self.allow_private)
        return parse_payload(body, hint, allow_private=self.allow_private)

    async def _single(self, session, source: Source) -> tuple[dict[str, int], int]:
        verify = source.kind != "web"
        for url in source.urls:
            status, body = await self._get(session, url, verify)
            if status == "ok":
                return await self._parse(body, source.hint), len(body)
            if status == "gone":
                break  # a mirror of a missing file is missing too
        return {}, 0

    async def _paged(self, session, source: Source) -> tuple[dict[str, int], int]:
        assert source.pages
        first, last = source.pages
        merged: dict[str, int] = {}
        total = 0
        for page in range(first, last + 1):
            url = (source.urls[0].replace("{page}", str(page))
                   .replace("{offset}", str((page - first) * source.step)))
            status, body = await self._get(session, url, False)
            if status != "ok":
                break
            found = await self._parse(body, source.hint)
            total += len(body)
            fresh = [p for p in found if p not in merged]
            for proxy, mask in found.items():
                merged[proxy] = merged.get(proxy, 0) | mask
            if not fresh:
                break  # page repeated or empty: the list is exhausted
        return merged, total

    # -- orchestration ---------------------------------------------------

    async def _worker(self, session, queue: asyncio.Queue, report: ScrapeReport) -> None:
        while True:
            source = await queue.get()
            try:
                if source is None:
                    return
                started = time.monotonic()
                if self.deadline and started > self.deadline:
                    report.stopped_by_budget = True
                    report.outcomes.append(Outcome(source.key, "skipped"))
                elif self.health and self.health.should_skip(source.key):
                    report.outcomes.append(Outcome(source.key, "skipped"))
                else:
                    found, size = await (self._paged(session, source) if source.paged
                                         else self._single(session, source))
                    state = "ok" if found else ("empty" if size else "failed")
                    report.pool.add_many(found)
                    report.outcomes.append(Outcome(source.key, state, len(found), size,
                                                   time.monotonic() - started))
                self._progress(report)
            finally:
                queue.task_done()

    def _progress(self, report: ScrapeReport) -> None:
        self._done += 1
        now = time.monotonic()
        if not self.quiet and (now - self._last_log > 20 or self._done == len(self.sources)):
            self._last_log = now
            print(f"  scraped {self._done}/{len(self.sources)} sources, "
                  f"{len(report.pool):,} candidates", flush=True)

    async def run(self) -> ScrapeReport:
        report = ScrapeReport(Pool())
        connector = aiohttp.TCPConnector(limit=self.concurrency, limit_per_host=self.per_host,
                                         ttl_dns_cache=600, enable_cleanup_closed=True)
        timeout = aiohttp.ClientTimeout(total=self.timeout, connect=10, sock_read=15)
        headers = {"User-Agent": USER_AGENT, "Accept": "text/plain,text/html,application/json,*/*;q=0.8"}
        queue: asyncio.Queue = asyncio.Queue()
        for source in self.sources:
            queue.put_nowait(source)
        workers = min(self.concurrency, max(1, len(self.sources)))
        for _ in range(workers):
            queue.put_nowait(None)
        async with aiohttp.ClientSession(connector=connector, timeout=timeout,
                                         headers=headers) as session:
            await asyncio.gather(*(self._worker(session, queue, report) for _ in range(workers)))

        total = len(report.outcomes) - report.count("skipped")
        good = report.count("ok")
        # A run where (almost) nothing worked means our network is down, not that
        # every source died at once: do not punish the sources for it.
        report.outage = total >= 50 and good / total < 0.02
        if self.health and not report.outage:
            for outcome in report.outcomes:
                if outcome.state != "skipped":
                    self.health.record(outcome.key, outcome.state == "ok")
        return report


def top_sources(report: ScrapeReport, n: int = 10) -> list[Outcome]:
    return sorted((o for o in report.outcomes if o.state == "ok"),
                  key=lambda o: -o.count)[:n]


def previous_masks(records: list[dict]) -> dict[str, int]:
    """``ip:port -> protocol mask`` of previously published proxies."""
    masks: dict[str, int] = {}
    for record in records:
        proxy, protocol = record.get("proxy"), record.get("protocol")
        if isinstance(proxy, str) and protocol in PROTOCOL_BITS:
            try:
                split_proxy(proxy)
            except ValueError:
                continue
            masks[proxy] = masks.get(proxy, 0) | PROTOCOL_BITS[protocol]
    return masks

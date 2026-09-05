from __future__ import annotations

import asyncio
import json
import re
import time
from collections import defaultdict

import aiohttp

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

PROXY_RE = re.compile(
    r"(?<![\d.])((?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)"
    r"(?:\.(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)){3})"
    r"\s*(?::|</td>\s*<td>|\s+|&nbsp;)\s*(\d{2,5})(?![\d.])"
)
SCHEME_RE = re.compile(r"(https?|socks4a?|socks5)://", re.I)

RESERVED_PREFIXES = (
    "0.", "10.", "127.", "169.254.", "192.168.", "255.",
    *[f"172.{octet}." for octet in range(16, 32)],
)


def _routable(ip: str) -> bool:
    if ip.startswith(RESERVED_PREFIXES):
        return False
    octets = ip.split(".")
    return octets[0] != "0" and octets[-1] != "0"


def _protocols_from_scheme(scheme: str) -> set[str]:
    scheme = scheme.lower()
    if scheme == "socks5":
        return {"socks5"}
    if scheme.startswith("socks4"):
        return {"socks4"}
    return {"http"}


def parse(payload: str, hint: str) -> list[tuple[str, set[str]]]:
    found: list[tuple[str, set[str]]] = []

    stripped = payload.lstrip()
    if stripped[:1] in "{[":
        try:
            data = json.loads(payload)
            rows = data.get("data", data) if isinstance(data, dict) else data
            if isinstance(rows, list):
                for row in rows:
                    if not isinstance(row, dict):
                        continue
                    ip = row.get("ip") or row.get("host") or row.get("addr")
                    port = row.get("port")
                    if not ip or not port or not _routable(str(ip)):
                        continue
                    raw = row.get("protocols") or row.get("protocol") or hint
                    if isinstance(raw, str):
                        raw = [raw]
                    protocols: set[str] = set()
                    for item in raw:
                        protocols |= _protocols_from_scheme(str(item))
                    found.append((f"{ip}:{port}", protocols))
                if found:
                    return found
        except (ValueError, AttributeError):
            pass

    for line in payload.splitlines():
        match = PROXY_RE.search(line)
        if not match:
            continue
        ip, port = match.group(1), match.group(2)
        if not _routable(ip) or not 0 < int(port) < 65536:
            continue
        scheme = SCHEME_RE.search(line)
        if scheme:
            protocols = _protocols_from_scheme(scheme.group(1))
        elif hint == "mixed":
            protocols = {"http", "socks4", "socks5"}
        else:
            protocols = {hint}
        found.append((f"{ip}:{port}", protocols))
    return found


class Scraper:
    def __init__(self, sources, concurrency: int = 150, timeout: float = 25.0):
        self.sources = list(sources)
        self.concurrency = concurrency
        self.timeout = timeout
        self.results: defaultdict[str, set[str]] = defaultdict(set)
        self.ok = self.empty = self.failed = 0
        self._lock = asyncio.Lock()

    async def _fetch(self, session, semaphore, hint, url):
        async with semaphore:
            try:
                async with session.get(
                    url,
                    timeout=aiohttp.ClientTimeout(total=self.timeout),
                    ssl=False,
                ) as response:
                    if response.status != 200:
                        async with self._lock:
                            self.failed += 1
                        return
                    payload = await response.text(errors="ignore")
            except Exception:
                async with self._lock:
                    self.failed += 1
                return

        items = parse(payload, hint)
        async with self._lock:
            if items:
                self.ok += 1
                for proxy, protocols in items:
                    self.results[proxy] |= protocols
            else:
                self.empty += 1
            done = self.ok + self.empty + self.failed
            if done % 250 == 0 or done == len(self.sources):
                print(
                    f"  {done}/{len(self.sources)} sources "
                    f"(ok={self.ok} empty={self.empty} failed={self.failed}) "
                    f"candidates={len(self.results)}",
                    flush=True,
                )

    async def run(self) -> dict[str, list[str]]:
        started = time.time()
        print(f"Scraping {len(self.sources)} sources", flush=True)
        semaphore = asyncio.Semaphore(self.concurrency)
        connector = aiohttp.TCPConnector(limit=250, ssl=False, ttl_dns_cache=300)
        async with aiohttp.ClientSession(
            connector=connector, headers={"User-Agent": USER_AGENT}
        ) as session:
            await asyncio.gather(
                *(self._fetch(session, semaphore, hint, url)
                  for hint, url in self.sources)
            )
        print(
            f"Scrape finished in {time.time() - started:.1f}s: "
            f"{len(self.results)} unique candidates from {self.ok} live sources",
            flush=True,
        )
        return {proxy: sorted(protocols) for proxy, protocols in self.results.items()}

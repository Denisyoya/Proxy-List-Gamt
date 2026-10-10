"""Persistent pool of *unvalidated* proxies.

Every scrape adds to it, nothing is thrown away until it expires:

* a proxy that is seen again is **overwritten** - newest protocol claims win
  (claims are unioned, so a proxy listed as both HTTP and SOCKS5 keeps both),
  ``last_seen`` is refreshed and its ``hits`` counter grows;
* a proxy that is *not* seen again stays in the pool until it is older than the
  TTL (``--raw-ttl-hours``), then it is pruned;
* the pool survives between runs on disk, so a 5-minute schedule keeps
  accumulating instead of starting from zero every time.

The pool is the source of the ``results/raw/`` lists (published without any
validation) and of the candidate set of the next validation run.

Entry layout (a list of five ints, kept small so millions fit in memory)::

    [mask, listings, hits, first_seen, last_seen]

``mask``      union of the protocols the sources claimed (0 = nobody said)
``listings``  how many sources listed it in its best run
``hits``      in how many scrape runs it was seen
``*_seen``    unix seconds (UTC)
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

from common import ALL_MASK, PROTOCOL_BITS, protocols_of

MASK, LISTINGS, HITS, FIRST, LAST = range(5)
FIELDS = ("mask", "listings", "hits", "first_seen", "last_seen")
DEFAULT_TTL_HOURS = 6.0
UNKNOWN = 8                       # same bit layout as scraper.Pool


class RawPool:
    """``ip:port`` -> ``[mask, listings, hits, first_seen, last_seen]``."""

    __slots__ = ("_data",)

    def __init__(self) -> None:
        self._data: dict[str, list[int]] = {}

    def __len__(self) -> int:
        return len(self._data)

    def __contains__(self, proxy: str) -> bool:
        return proxy in self._data

    def __iter__(self):
        return iter(self._data)

    # ------------------------------------------------------------------ writes

    def add_many(self, found: dict[str, int], *, listings: dict[str, int] | None = None,
                 now: int | None = None) -> int:
        """Upsert *found* (``ip:port -> protocol mask``); returns how many are new."""
        now = int(now if now is not None else time.time())
        listings = listings or {}
        fresh = 0
        for proxy, mask in found.items():
            seen = listings.get(proxy, 1)
            entry = self._data.get(proxy)
            if entry is None:
                self._data[proxy] = [mask, seen, 1, now, now]
                fresh += 1
            else:
                # overwrite: the newest claims win, but knowledge is never lost
                entry[MASK] |= mask
                entry[LISTINGS] = max(entry[LISTINGS], seen)
                entry[HITS] += 1
                entry[LAST] = now
        return fresh

    def add_pool(self, pool, *, now: int | None = None) -> int:
        """Upsert every candidate of a :class:`scraper.Pool` (packed integers)."""
        now = int(now if now is not None else time.time())
        fresh = 0
        for proxy, packed in pool.snapshot().items():
            mask, listings = packed & 7, max(1, packed >> 4)
            entry = self._data.get(proxy)
            if entry is None:
                self._data[proxy] = [mask, listings, 1, now, now]
                fresh += 1
            else:
                entry[MASK] |= mask
                entry[LISTINGS] = max(entry[LISTINGS], listings)
                entry[HITS] += 1
                entry[LAST] = now
        return fresh

    def prune(self, ttl_seconds: float, *, now: int | None = None) -> int:
        """Drop everything not seen for *ttl_seconds*; returns how many went."""
        if ttl_seconds <= 0:
            return 0
        now = int(now if now is not None else time.time())
        cutoff = now - ttl_seconds
        stale = [proxy for proxy, entry in self._data.items() if entry[LAST] < cutoff]
        for proxy in stale:
            del self._data[proxy]
        return len(stale)

    def cap(self, limit: int) -> int:
        """Keep at most *limit* entries, oldest and least-listed first to go."""
        if limit <= 0 or len(self._data) <= limit:
            return 0
        ordered = sorted(self._data.items(), key=lambda kv: (-kv[1][LAST], -kv[1][LISTINGS], kv[0]))
        for proxy, _ in ordered[limit:]:
            del self._data[proxy]
        return len(ordered) - limit

    # ------------------------------------------------------------------- reads

    def get(self, proxy: str) -> list[int] | None:
        return self._data.get(proxy)

    def protocols(self, proxy: str) -> list[str]:
        """Protocol names a proxy was seen with (empty when nobody claimed one)."""
        entry = self._data.get(proxy)
        return protocols_of(entry[MASK]) if entry else []

    def records(self, *, now: int | None = None) -> list[dict]:
        """Publication records, freshest and best-attested first."""
        now = int(now if now is not None else time.time())
        out = []
        for proxy, entry in self._data.items():
            out.append({
                "proxy": proxy,
                "protocols": protocols_of(entry[MASK]),
                "listings": entry[LISTINGS],
                "hits": entry[HITS],
                "first_seen": entry[FIRST],
                "last_seen": entry[LAST],
                "age_seconds": max(0, now - entry[LAST]),
            })
        out.sort(key=lambda r: (-r["last_seen"], -r["listings"], -r["hits"], r["proxy"]))
        return out

    def group(self, records: list[dict], name: str) -> list[dict]:
        """Records that claim *name* (``all`` keeps everything)."""
        if name == "all":
            return records
        if name == "socks":
            return [r for r in records if {"socks4", "socks5"} & set(r["protocols"])]
        return [r for r in records if name in r["protocols"]]

    def ranked(self, limit: int = 0) -> list[tuple[str, int, int]]:
        """Best candidates first as ``(proxy, test_mask, fallback_mask)``.

        Same contract as :meth:`scraper.Pool.ranked` but without the randomness:
        most recently seen, then most-listed. Used to seed a validation run from
        the accumulated pool.
        """
        ordered = sorted(self._data.items(),
                         key=lambda kv: (-kv[1][LAST], -min(kv[1][LISTINGS], 10), kv[0]))
        if limit:
            ordered = ordered[:limit]
        return [(proxy, (entry[MASK] & ALL_MASK) or ALL_MASK,
                 ALL_MASK & ~((entry[MASK] & ALL_MASK) or ALL_MASK))
                for proxy, entry in ordered]

    def to_pool(self):
        """A :class:`scraper.Pool` holding the accumulated candidates."""
        from scraper import Pool          # local import: avoids a circular dependency
        pool = Pool()
        packed = {}
        for proxy, entry in self._data.items():
            unknown = UNKNOWN if not entry[MASK] else 0
            packed[proxy] = ((entry[LISTINGS] & 0xFFFFF) << 4) | unknown | (entry[MASK] & 7)
        pool.restore(packed)
        return pool

    def stats(self, *, now: int | None = None) -> dict:
        now = int(now if now is not None else time.time())
        per_protocol = {name: 0 for name in PROTOCOL_BITS}
        unknown = 0
        for entry in self._data.values():
            if not entry[MASK]:
                unknown += 1
            for name, bit in PROTOCOL_BITS.items():
                if entry[MASK] & bit:
                    per_protocol[name] += 1
        ages = sorted(now - entry[LAST] for entry in self._data.values())
        return {
            "raw_total": len(self._data),
            "raw_http": per_protocol["http"],
            "raw_socks4": per_protocol["socks4"],
            "raw_socks5": per_protocol["socks5"],
            "raw_unspecified": unknown,
            "raw_multi_hits": sum(1 for e in self._data.values() if e[HITS] > 1),
            "raw_median_age_seconds": ages[len(ages) // 2] if ages else 0,
            "raw_newest_age_seconds": ages[0] if ages else 0,
        }

    # ------------------------------------------------------------- persistence

    def dump(self, path: str | Path) -> None:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._data, separators=(",", ":")))
        os.replace(tmp, target)

    @classmethod
    def load(cls, path: str | Path) -> "RawPool":
        pool = cls()
        try:
            raw = json.loads(Path(path).read_text())
        except (ValueError, OSError):
            return pool
        for proxy, entry in (raw or {}).items():
            try:
                values = [int(v) for v in entry]
            except (TypeError, ValueError):
                continue
            if len(values) == len(FIELDS) and all(v >= 0 for v in values):
                values[MASK] &= ALL_MASK
                pool._data[str(proxy)] = values
        return pool

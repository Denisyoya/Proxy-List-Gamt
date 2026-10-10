"""Publish verified proxies: ``results/{txt,json,pdf}`` plus the README blocks.

Layout (every format has the same six lists)::

    results/
      txt/   all.txt  http.txt  https.txt  socks.txt  socks4.txt  socks5.txt
      json/  all.json http.json https.json socks.json socks4.json socks5.json  stats.json
      pdf/   all.pdf  http.pdf  https.pdf  socks.pdf  socks4.pdf  socks5.pdf

``all``    every live proxy of every protocol
``http``   HTTP proxies that relay plain HTTP
``https``  HTTP proxies whose CONNECT tunnel passed a certificate-verified TLS check
``socks``  SOCKS4 + SOCKS5 together;  ``socks4`` / ``socks5`` each on their own

Mixed-protocol files (``all``, ``socks``) use ``protocol://ip:port`` so every line
is unambiguous; single-protocol files use bare ``ip:port``.
"""
from __future__ import annotations

import json
import os
import tempfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import pdfgen
from common import stamp_age_seconds

OUTPUT_DIR = "results"
RAW_DIR = "results/raw"
FORMATS = ("txt", "json", "pdf")
RAW_FORMATS = ("txt", "json")
GROUPS = ("all", "http", "https", "socks", "socks4", "socks5")
RAW_GROUPS = ("all", "http", "socks", "socks4", "socks5")
PROTOCOL_ORDER = {"http": 0, "socks5": 1, "socks4": 2}

GROUP_TITLES = {
    "all": "All live proxies (HTTP, HTTPS, SOCKS4, SOCKS5)",
    "http": "HTTP proxies",
    "https": "HTTPS proxies (CONNECT + verified TLS)",
    "socks": "SOCKS proxies (SOCKS4 + SOCKS5)",
    "socks4": "SOCKS4 proxies",
    "socks5": "SOCKS5 proxies",
}
GROUP_FILTERS: dict[str, Callable[[dict], bool]] = {
    "all": lambda r: True,
    "http": lambda r: r["protocol"] == "http" and r.get("http", True),
    "https": lambda r: r["protocol"] == "http" and r.get("https", False),
    "socks": lambda r: r["protocol"] in ("socks4", "socks5"),
    "socks4": lambda r: r["protocol"] == "socks4",
    "socks5": lambda r: r["protocol"] == "socks5",
}
SCHEME_GROUPS = {"all", "socks"}   # lists that mix protocols carry the scheme


def select(records: list[dict], group: str) -> list[dict]:
    """Records of *group*, fastest first."""
    chosen = [r for r in records if GROUP_FILTERS[group](r)]
    chosen.sort(key=lambda r: (r["latency_ms"], PROTOCOL_ORDER.get(r["protocol"], 9), r["proxy"]))
    return chosen


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(handle, "wb") as fh:
            fh.write(data)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


# ------------------------------------------------------- continuous publishing


def merge_published(previous: list[dict], fresh: list[dict], keep_minutes: float = 0.0,
                    now: datetime | None = None) -> tuple[list[dict], dict]:
    """Keep adding to what is already published instead of starting from zero.

    * a proxy + protocol that verified again **overwrites** the old record;
    * a record that was *not* re-checked this run survives while it is younger
      than ``keep_minutes`` (``0`` = publish only what this run verified), so a
      short validation pass never wipes a list built by earlier runs;
    * carried-over records are flagged ``carried_over`` and keep their own
      ``checked_at`` stamp, so they expire on their own.
    """
    now = now or datetime.now(timezone.utc)
    merged: dict[tuple, dict] = {}
    for record in fresh:
        if record.get("proxy"):
            merged[(record["proxy"], record.get("protocol"))] = dict(record)
    carried = 0
    if keep_minutes > 0:
        limit = keep_minutes * 60
        for record in previous:
            proxy = record.get("proxy")
            if not proxy:
                continue
            key = (proxy, record.get("protocol"))
            if key in merged:
                continue                                   # the new check wins
            if stamp_age_seconds(record.get("checked_at"), now) <= limit:
                kept = dict(record)
                kept["carried_over"] = True
                merged[key] = kept
                carried += 1
    stats = {"verified_now": len(fresh), "carried_over": carried, "published": len(merged)}
    return list(merged.values()), stats


def _url(record: dict) -> str:
    return record.get("url") or f"{record['protocol']}://{record['proxy']}"


def txt_lines(records: list[dict], group: str) -> list[str]:
    """``protocol://ip:port`` for mixed-protocol lists, bare ``ip:port`` otherwise."""
    return [_url(r) if group in SCHEME_GROUPS else r["proxy"] for r in records]


def pdf_rows(records: list[dict], group: str) -> list[str]:
    rows = []
    for r in records:
        tls = "tls" if r.get("https") else "   "
        rows.append(f"{r['protocol']:<6} {r['proxy']:<21} {r['latency_ms']:>5} {tls}")
    return rows


def compute_stats(records: list[dict], meta: dict) -> dict:
    latencies = sorted(r["latency_ms"] for r in records)
    counts = Counter(r["protocol"] for r in records)
    unique = {r["proxy"] for r in records}
    stats = {
        "generated_at": meta.get("generated_at") or datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
        "live": len(records),
        "unique_endpoints": len(unique),
        "http": sum(1 for r in records if GROUP_FILTERS["http"](r)),
        "https": sum(1 for r in records if GROUP_FILTERS["https"](r)),
        "socks4": counts.get("socks4", 0),
        "socks5": counts.get("socks5", 0),
        "stable": sum(1 for r in records if r.get("checks_passed", 1) == r.get("checks_total", 1)
                      and r.get("checks_total", 1) > 1),
        "median_latency_ms": latencies[len(latencies) // 2] if latencies else 0,
        "fastest_latency_ms": latencies[0] if latencies else 0,
        "under_1s": sum(1 for v in latencies if v < 1000),
    }
    for key, value in meta.items():
        stats.setdefault(key, value)
    return stats


def build(records: list[dict], meta: dict | None = None, output_dir: str = OUTPUT_DIR,
          formats: tuple[str, ...] = FORMATS, readme: str | None = "README.md") -> dict:
    """Write every list in every requested format; returns the statistics."""
    meta = dict(meta or {})
    meta.setdefault("generated_at", datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"))
    stats = compute_stats(records, meta)
    root = Path(output_dir)
    for group in GROUPS:
        chosen = select(records, group)
        if "txt" in formats:
            lines = txt_lines(chosen, group)
            _atomic_write(root / "txt" / f"{group}.txt",
                          ("\n".join(lines) + "\n" if lines else "").encode())
        if "json" in formats:
            _atomic_write(root / "json" / f"{group}.json",
                          (json.dumps(chosen, indent=1) + "\n").encode())
        if "pdf" in formats:
            subtitle = [
                f"{len(chosen):,} verified proxies  |  generated {stats['generated_at']}",
                "Each proxy answered a live judge request through itself in the final check.",
                ("Format: protocol  address  latency(ms)  tls = verified HTTPS tunnel"),
                "Free proxies are volatile - refresh from the repository for the newest list.",
            ]
            header = f"{'proto':<6} {'address':<21} {'ms':>5} tls"
            if not chosen:
                subtitle[0] = f"0 proxies in this category right now  |  generated {stats['generated_at']}"
            _atomic_write(root / "pdf" / f"{group}.pdf", pdfgen.render_table(
                f"{REPOSITORY} - {GROUP_TITLES[group]}", subtitle, header, pdf_rows(chosen, group),
                author=AUTHOR, footer=f"{REPOSITORY} by {AUTHOR}"))
    if "json" in formats:
        _atomic_write(root / "json" / "stats.json", (json.dumps(stats, indent=1) + "\n").encode())
    if readme:
        update_readme(stats, readme)
        update_footer(readme)
    return stats


# ------------------------------------------------------- raw (no validation)

RAW_TITLES = {
    "all": "Every scraped proxy, unvalidated",
    "http": "Scraped HTTP proxies, unvalidated",
    "socks": "Scraped SOCKS proxies (SOCKS4 + SOCKS5), unvalidated",
    "socks4": "Scraped SOCKS4 proxies, unvalidated",
    "socks5": "Scraped SOCKS5 proxies, unvalidated",
}


def raw_lines(records: list[dict], group: str) -> list[str]:
    """``protocol://ip:port`` for mixed lists, bare ``ip:port`` otherwise.

    A proxy whose sources never said which protocol it speaks appears in ``all``
    as a bare ``ip:port`` line and in no single-protocol list.
    """
    if group == "all":
        lines: list[str] = []
        for record in records:
            protocols = record.get("protocols") or []
            lines += [f"{name}://{record['proxy']}" for name in protocols] or [record["proxy"]]
        return lines
    if group == "socks":
        return [f"{name}://{record['proxy']}" for record in records
                for name in record.get("protocols", ()) if name.startswith("socks")]
    return [record["proxy"] for record in records if group in (record.get("protocols") or [])]


def select_raw(records: list[dict], group: str) -> list[dict]:
    """Records of *group* (already ordered freshest-first by the raw pool)."""
    if group == "all":
        return list(records)
    if group == "socks":
        return [r for r in records if {"socks4", "socks5"} & set(r.get("protocols") or ())]
    return [r for r in records if group in (r.get("protocols") or ())]


def compute_raw_stats(records: list[dict], meta: dict) -> dict:
    protocols = Counter(name for r in records for name in r.get("protocols") or [])
    stats = {
        "generated_at": meta.get("generated_at")
        or datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
        "raw_published": len(records),
        "raw_endpoints": len({r["proxy"] for r in records}),
        "raw_http": protocols.get("http", 0),
        "raw_socks4": protocols.get("socks4", 0),
        "raw_socks5": protocols.get("socks5", 0),
        "raw_unspecified": sum(1 for r in records if not r.get("protocols")),
        "raw_multi_run": sum(1 for r in records if r.get("hits", 1) > 1),
        "raw_median_listings": sorted(r.get("listings", 1) for r in records)[len(records) // 2]
        if records else 0,
        "raw_newest_age_seconds": min((r.get("age_seconds", 0) for r in records), default=0),
        "validated": False,
    }
    for key, value in meta.items():
        stats.setdefault(key, value)
    return stats


def build_raw(records: list[dict], meta: dict | None = None, output_dir: str = RAW_DIR,
              formats: tuple[str, ...] = RAW_FORMATS, limit: int = 0,
              readme: str | None = None) -> dict:
    """Publish the scraped-but-unvalidated pool; returns its statistics."""
    meta = dict(meta or {})
    meta.setdefault("generated_at", datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"))
    if limit and len(records) > limit:
        records = records[:limit]
    stats = compute_raw_stats(records, meta)
    root = Path(output_dir)
    for group in RAW_GROUPS:
        chosen = select_raw(records, group)
        if "txt" in formats:
            lines = raw_lines(chosen, group)
            _atomic_write(root / "txt" / f"{group}.txt",
                          ("\n".join(lines) + "\n" if lines else "").encode())
        if "json" in formats:
            _atomic_write(root / "json" / f"{group}.json",
                          (json.dumps(chosen, indent=1) + "\n").encode())
    if "json" in formats:
        _atomic_write(root / "json" / "stats.json", (json.dumps(stats, indent=1) + "\n").encode())
    if readme:
        update_readme_raw(stats, readme)
    return stats


# ------------------------------------------------------------- README blocks

STATS_START = "<!-- stats:start -->"
STATS_END = "<!-- stats:end -->"
RAW_START = "<!-- raw-stats:start -->"
RAW_END = "<!-- raw-stats:end -->"
FOOTER_START = "<!-- footer:start -->"
FOOTER_END = "<!-- footer:end -->"

AUTHOR = "Gametturxux"
COMMUNITY = "gametturxux community"
COMMUNITY_LINK = "https://whatsapp.com/channel/0029VbC6a5C7oQhUeajM3q1i"
REPOSITORY = "Proxy-List-Gamt"


def _replace_block(content: str, start: str, end: str, block: str) -> str | None:
    if start not in content or end not in content:
        return None
    head, _, rest = content.partition(start)
    _, _, tail = rest.partition(end)
    return f"{head}{start}\n\n{block}\n\n{end}{tail}"


def update_readme(stats: dict, path: str = "README.md") -> bool:
    if not os.path.exists(path):
        return False
    content = Path(path).read_text(encoding="utf-8")
    discovered = stats.get("sources_discovered", 0)
    table = "\n".join([
        f"Last run: `{stats['generated_at']}`",
        "",
        "| Metric | Value |",
        "| --- | --- |",
        f"| Live proxies | **{stats['live']:,}** |",
        f"| HTTP | {stats['http']:,} |",
        f"| HTTPS (verified TLS tunnel) | {stats['https']:,} |",
        f"| SOCKS4 | {stats['socks4']:,} |",
        f"| SOCKS5 | {stats['socks5']:,} |",
        f"| Passed every check (stable) | {stats['stable']:,} |",
        f"| Median latency | {stats['median_latency_ms']} ms |",
        f"| Fastest | {stats['fastest_latency_ms']} ms |",
        f"| Under 1 second | {stats['under_1s']:,} |",
        f"| Sources registered | {stats.get('sources', 0):,} "
        f"({stats.get('sources_curated', 0):,} curated + {discovered:,} discovered) |",
        f"| Sources that delivered proxies | {stats.get('sources_ok', 0):,} |",
        f"| Candidates scraped | {stats.get('candidates', 0):,} |",
        f"| Reachable on TCP | {stats.get('tcp_alive', 0):,} |",
        f"| Validation rounds | {stats.get('rounds', 1) + 1} (first pass + {stats.get('rounds', 1)} confirmation) |",
        f"| Runtime | {stats.get('runtime_seconds', 0)}s |",
    ])
    updated = _replace_block(content, STATS_START, STATS_END, table)
    if updated is None:
        return False
    Path(path).write_text(updated, encoding="utf-8")
    return True


def update_readme_raw(stats: dict, path: str = "README.md") -> bool:
    """Refresh the ``<!-- raw-stats -->`` block with the unvalidated numbers."""
    if not os.path.exists(path):
        return False
    content = Path(path).read_text(encoding="utf-8")
    table = "\n".join([
        f"Last scrape: `{stats['generated_at']}` - **no validation**, published as scraped",
        "",
        "| Metric | Value |",
        "| --- | --- |",
        f"| Raw proxies published | **{stats.get('raw_published', 0):,}** |",
        f"| Unique endpoints | {stats.get('raw_endpoints', 0):,} |",
        f"| HTTP | {stats.get('raw_http', 0):,} |",
        f"| SOCKS4 | {stats.get('raw_socks4', 0):,} |",
        f"| SOCKS5 | {stats.get('raw_socks5', 0):,} |",
        f"| Protocol not stated by any source | {stats.get('raw_unspecified', 0):,} |",
        f"| Seen in more than one scrape run | {stats.get('raw_multi_run', 0):,} |",
        f"| Sources registered | {stats.get('sources', 0):,} "
        f"({stats.get('sources_curated', 0):,} curated + {stats.get('sources_discovered', 0):,} discovered) |",
        f"| Sources that delivered proxies | {stats.get('sources_ok', 0):,} |",
        f"| Candidates scraped this run | {stats.get('candidates', 0):,} |",
        f"| Raw pool size (accumulated) | {stats.get('raw_total', 0):,} |",
        f"| Raw pool TTL | {stats.get('raw_ttl_hours', 0)} h |",
        f"| Scrape time | {stats.get('scrape_seconds', 0)}s |",
    ])
    updated = _replace_block(content, RAW_START, RAW_END, table)
    if updated is None:
        return False
    Path(path).write_text(updated, encoding="utf-8")
    return True


def update_footer(path: str = "README.md") -> bool:
    if not os.path.exists(path):
        return False
    content = Path(path).read_text(encoding="utf-8")
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    block = "\n".join([
        "---",
        "",
        f"**{REPOSITORY}** - author **{AUTHOR}** - {COMMUNITY}",
        "",
        f"Community channel: {COMMUNITY_LINK}",
        "",
        f"Script by {AUTHOR}. Automated build refreshed at {stamp}.",
    ])
    updated = _replace_block(content, FOOTER_START, FOOTER_END, block)
    if updated is None:
        return False
    Path(path).write_text(updated, encoding="utf-8")
    return True

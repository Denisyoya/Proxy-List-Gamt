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

OUTPUT_DIR = "results"
FORMATS = ("txt", "json", "pdf")
GROUPS = ("all", "http", "https", "socks", "socks4", "socks5")
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


# ------------------------------------------------------------- README blocks

STATS_START = "<!-- stats:start -->"
STATS_END = "<!-- stats:end -->"
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

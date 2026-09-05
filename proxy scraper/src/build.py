from __future__ import annotations

import json
import os
from collections import Counter
from datetime import datetime, timezone

OUTPUT_DIR = "proxy"

def scheme(record: dict) -> str:
    return f"{record['protocol']}://{record['proxy']}"


def plain(record: dict) -> str:
    return record["proxy"]


def _write_lines(path: str, rows, formatter) -> int:
    with open(path, "w") as fh:
        for row in rows:
            fh.write(formatter(row) + "\n")
    return len(rows)


def build(records: list[dict], meta: dict, output_dir: str = OUTPUT_DIR) -> dict:
    os.makedirs(output_dir, exist_ok=True)

    records.sort(key=lambda r: (PROTOCOL_ORDER.get(r["protocol"], 9),
                                r["latency_ms"]))

    by_protocol = {"http": [], "socks4": [], "socks5": []}
    for record in records:
        by_protocol.setdefault(record["protocol"], []).append(record)

    combined = sorted(records, key=lambda r: r["latency_ms"])

    _write_lines(f"{output_dir}/proxy.txt", combined, scheme)
    _write_lines(f"{output_dir}/all.txt", combined, plain)
    for protocol in ("http", "socks4", "socks5"):
        _write_lines(f"{output_dir}/{protocol}.txt",
                     by_protocol.get(protocol, []), plain)
    _write_lines(f"{output_dir}/https.txt",
                 [r for r in combined if r.get("https")], plain)
    _write_lines(f"{output_dir}/stable.txt",
                 [r for r in combined if r.get("checks_passed", 1) > 1], scheme)

    with open(f"{output_dir}/proxies.json", "w") as fh:
        json.dump(records, fh, indent=1)

    with open(f"{output_dir}/proxies.csv", "w") as fh:
        fh.write("proxy,protocol,latency_ms,https,exit_ip,checks_passed,checks_total\n")
        for r in records:
            fh.write(
                f"{r['proxy']},{r['protocol']},{r['latency_ms']},"
                f"{str(bool(r.get('https'))).lower()},{r['exit_ip']},"
                f"{r.get('checks_passed', 1)},{r.get('checks_total', 1)}\n"
            )

    latencies = sorted(r["latency_ms"] for r in records)
    counts = Counter(r["protocol"] for r in records)
    stats = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
        "sources": meta.get("sources", 0),
        "sources_responding": meta.get("sources_responding", 0),
        "candidates": meta.get("candidates", 0),
        "validation_rounds": meta.get("rounds", 1),
        "live": len(records),
        "http": counts.get("http", 0),
        "socks4": counts.get("socks4", 0),
        "socks5": counts.get("socks5", 0),
        "https_capable": sum(1 for r in records if r.get("https")),
        "stable": sum(1 for r in records if r.get("checks_passed", 1) > 1),
        "median_latency_ms": latencies[len(latencies) // 2] if latencies else 0,
        "fastest_latency_ms": latencies[0] if latencies else 0,
        "under_1s": sum(1 for value in latencies if value < 1000),
        "runtime_seconds": meta.get("runtime", 0),
    }
    with open(f"{output_dir}/stats.json", "w") as fh:
        json.dump(stats, fh, indent=1)
    update_readme(stats)
    update_footer()
    return stats


STATS_START = "<!-- stats:start -->"
STATS_END = "<!-- stats:end -->"
FOOTER_START = "<!-- footer:start -->"
FOOTER_END = "<!-- footer:end -->"

AUTHOR = "Gametturxux"
COMMUNITY = "gametturxux community"
COMMUNITY_LINK = "https://whatsapp.com/channel/0029VbC6a5C7oQhUeajM3q1i"
REPOSITORY = "Proxy-List-Gamt"


def update_readme(stats: dict, path: str = "README.md") -> bool:
    if not os.path.exists(path):
        return False
    with open(path) as fh:
        content = fh.read()
    if STATS_START not in content or STATS_END not in content:
        return False

    table = "\n".join([
        f"Last run: `{stats['generated_at']}`",
        "",
        "| Metric | Value |",
        "| --- | --- |",
        f"| Live proxies | **{stats['live']:,}** |",
        f"| HTTP / HTTPS | {stats['http']:,} |",
        f"| SOCKS4 | {stats['socks4']:,} |",
        f"| SOCKS5 | {stats['socks5']:,} |",
        f"| CONNECT (HTTPS) capable | {stats['https_capable']:,} |",
        f"| Re-confirmed (stable) | {stats.get('stable', 0):,} |",
        f"| Median latency | {stats['median_latency_ms']} ms |",
        f"| Fastest | {stats['fastest_latency_ms']} ms |",
        f"| Under 1 second | {stats['under_1s']:,} |",
        f"| Sources queried | {stats['sources']:,} "
        f"({stats['sources_responding']:,} responded) |",
        f"| Candidates scraped | {stats['candidates']:,} |",
        f"| Validation rounds | {stats['validation_rounds']} |",
        f"| Runtime | {stats['runtime_seconds']}s |",
    ])
    head, _, rest = content.partition(STATS_START)
    _, _, tail = rest.partition(STATS_END)
    with open(path, "w") as fh:
        fh.write(f"{head}{STATS_START}\n\n{table}\n\n{STATS_END}{tail}")
    return True


def update_footer(path: str = "README.md") -> bool:
    if not os.path.exists(path):
        return False
    with open(path) as fh:
        content = fh.read()
    if FOOTER_START not in content or FOOTER_END not in content:
        return False
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
    head, _, rest = content.partition(FOOTER_START)
    _, _, tail = rest.partition(FOOTER_END)
    with open(path, "w") as fh:
        fh.write(f"{head}{FOOTER_START}\n\n{block}\n\n{FOOTER_END}{tail}")
    return True


PROTOCOL_ORDER = {"http": 0, "socks5": 1, "socks4": 2}

#!/usr/bin/env python3
"""Audit the source registries: is every source reachable, and what does it yield?

    python tools/audit_sources.py                                  # every curated registry
    python tools/audit_sources.py --files websites.txt,indonesia.txt
    python tools/audit_sources.py --prune                          # write <file>.pruned copies
    python tools/audit_sources.py --sort yield                     # biggest sources first

One line per source, tab separated, so the report can be grepped or sorted::

    state   yield   bytes   seconds   registry:line   url

``state`` is ``ok`` (proxies found), ``empty`` (reachable, nothing to parse),
``failed`` (unreachable / no HTTP 200 from any mirror) or ``skipped`` (budget).

The audit never touches ``.cache/source_health.json``: it is a measuring tool,
not part of the pipeline, and a slow network on your machine must not make the
pipeline back off from healthy sources.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from scraper import Scraper  # noqa: E402
from sources import (DISCOVERED_FILE, GITHUB_FILE, INDONESIA_FILE, WEBSITES_FILE,  # noqa: E402
                     parse_any_line, parse_github_line, parse_web_line)

PARSERS = {
    GITHUB_FILE: parse_github_line,
    WEBSITES_FILE: parse_web_line,
    INDONESIA_FILE: parse_any_line,
    DISCOVERED_FILE: parse_github_line,
}
CURATED = (GITHUB_FILE, WEBSITES_FILE, INDONESIA_FILE)


def collect(root: Path, files: tuple[str, ...]) -> list[tuple[str, int, str, object]]:
    """``(registry, line number, raw line, Source)`` for every parsable source line."""
    entries: list[tuple[str, int, str, object]] = []
    seen: set[str] = set()
    for name in files:
        path = root / name
        if not path.exists():
            continue
        parser = PARSERS.get(name)
        if parser is None:
            print(f"unknown registry {name!r}", file=sys.stderr)
            continue
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            source = parser(line)
            if source is None or source.key in seen:
                continue
            seen.add(source.key)
            entries.append((name, number, line.rstrip(), source))
    return entries


async def audit(entries, *, concurrency: int, timeout: float, budget: float,
                quiet: bool) -> dict[str, object]:
    scraper = Scraper([e[3] for e in entries], concurrency=concurrency, timeout=timeout,
                      health=None, deadline=time.monotonic() + budget * 60 if budget else None,
                      quiet=quiet)
    report = await scraper.run()
    return {o.key: o for o in report.outcomes}


def prune_file(root: Path, name: str, doomed: set[str], *, suffix: str = ".pruned") -> int:
    """Write a copy of *name* without the lines whose source key is in *doomed*."""
    path = root / name
    parser = PARSERS[name]
    kept, removed = [], 0
    for line in path.read_text(encoding="utf-8").splitlines():
        source = None if (not line.strip() or line.lstrip().startswith("#")) else parser(line)
        if source is not None and source.key in doomed:
            removed += 1
            continue
        kept.append(line)
    target = path.with_name(path.name + suffix)
    target.write_text("\n".join(kept) + "\n", encoding="utf-8")
    return removed


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--sources", default=str(ROOT / "sources"), help="registry directory")
    p.add_argument("--files", default=",".join(CURATED),
                   help=f"comma separated registries to audit (any of {','.join(PARSERS)})")
    p.add_argument("--concurrency", type=int, default=60, help="simultaneous downloads")
    p.add_argument("--timeout", type=float, default=20.0, help="per-request timeout (seconds)")
    p.add_argument("--budget", type=float, default=10.0, help="stop after this many minutes (0 = no limit)")
    p.add_argument("--sort", choices=("registry", "yield", "state"), default="registry")
    p.add_argument("--report", default="", help="also write the report to this file")
    p.add_argument("--prune", action="store_true",
                   help="write <registry>.pruned copies without the unreachable sources")
    p.add_argument("--prune-empty", action="store_true",
                   help="with --prune: also drop sources that answered but yielded nothing")
    p.add_argument("--quiet", action="store_true", help="no progress lines while scraping")
    args = p.parse_args(argv)

    root = Path(args.sources)
    files = tuple(f.strip() for f in args.files.split(",") if f.strip())
    entries = collect(root, files)
    if not entries:
        print("nothing to audit", file=sys.stderr)
        return 1
    print(f"auditing {len(entries):,} sources from {', '.join(files)} "
          f"(concurrency {args.concurrency}, budget {args.budget:g} min)", file=sys.stderr)

    outcomes = asyncio.run(audit(entries, concurrency=args.concurrency, timeout=args.timeout,
                                 budget=args.budget, quiet=args.quiet))
    rows = []
    for name, number, line, source in entries:
        outcome = outcomes.get(source.key)
        state = outcome.state if outcome else "skipped"
        count = outcome.count if outcome else 0
        size = outcome.size if outcome else 0
        seconds = outcome.seconds if outcome else 0.0
        rows.append((state, count, size, seconds, f"{name}:{number}", source.key, line))
    if args.sort == "yield":
        rows.sort(key=lambda r: (-r[1], r[4]))
    elif args.sort == "state":
        rows.sort(key=lambda r: (r[0], -r[1], r[4]))

    report = [f"{state}\t{count}\t{size}\t{seconds:.1f}\t{where}\t{key}"
              for state, count, size, seconds, where, key, _ in rows]
    print("\n".join(report))

    doomed = {key for state, count, _, _, _, key, _ in rows
              if state == "failed" or (args.prune_empty and state in ("empty", "failed"))}
    summary = []
    for name in files:
        mine = [r for r in rows if r[4].startswith(f"{name}:")]
        per = {s: sum(1 for r in mine if r[0] == s) for s in ("ok", "empty", "failed", "skipped")}
        summary.append(f"{name}: {len(mine):,} sources -> {per['ok']:,} ok, {per['empty']:,} empty, "
                       f"{per['failed']:,} failed, {per['skipped']:,} skipped; "
                       f"{sum(r[1] for r in mine):,} proxies")
    print("\n".join(summary), file=sys.stderr)
    print(f"total: {len(rows):,} sources, {sum(r[1] for r in rows):,} proxies, "
          f"{len(doomed):,} unreachable", file=sys.stderr)

    if args.report:
        target = Path(args.report)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("\n".join(report) + "\n\n" + "\n".join(summary) + "\n", encoding="utf-8")
        print(f"report written to {target}", file=sys.stderr)
    if args.prune:
        for name in files:
            mine = {key for state, _, _, _, where, key, _ in rows
                    if where.startswith(f"{name}:") and key in doomed}
            if mine:
                removed = prune_file(root, name, mine)
                print(f"{name}: dropped {removed} lines -> {name}.pruned", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())

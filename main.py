#!/usr/bin/env python3
"""Scrape, verify and publish free proxies (Proxy-List-Gamt by Gametturxux).

    python main.py                       full run with the defaults
    python main.py --max-candidates 20000 --rounds 1      quick run
    python main.py --help                every option

Pipeline: discover sources -> scrape -> detect environment -> verify -> publish.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import ssl
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "src"))

import build as builder  # noqa: E402
import discover  # noqa: E402
from scraper import Health, Pool, Scraper, previous_masks, top_sources  # noqa: E402
from sources import MAX_DISCOVERED, load_sources  # noqa: E402
from validator import (CONTROL_TARGETS, DEFAULT_JUDGES, Checker, Judge, Validator,  # noqa: E402
                       detect_environment, raise_fd_limit)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Scrape, verify and publish free proxies",
                                formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    g = p.add_argument_group("verification")
    g.add_argument("--timeout", type=float, default=8.0, help="per-probe timeout (seconds)")
    g.add_argument("--connect-timeout", type=float, default=5.0, help="TCP connect timeout (seconds)")
    g.add_argument("--concurrency", type=int, default=800, help="simultaneous protocol probes")
    g.add_argument("--tcp-concurrency", type=int, default=3000, help="simultaneous TCP pre-filter connects")
    g.add_argument("--rounds", type=int, default=2, help="confirmation rounds after the first pass")
    g.add_argument("--max-candidates", type=int, default=0, help="test only the best N candidates (0 = all)")
    g.add_argument("--time-budget", type=float, default=0.0,
                   help="stop verifying after this many minutes (0 = no limit)")
    g.add_argument("--no-https-check", action="store_true", help="skip the CONNECT + TLS capability probe")
    g.add_argument("--no-controls", action="store_true", help="skip the network-interception control probes")
    g.add_argument("--judge", action="append", default=[], metavar="URL",
                   help="IP-echo endpoint (repeatable; http:// and https:// URLs). Replaces the built-in judges")
    s = p.add_argument_group("sources")
    s.add_argument("--sources", default="sources", help="directory with github.txt/websites.txt/discovered.txt")
    s.add_argument("--scrape-concurrency", type=int, default=200, help="simultaneous source downloads")
    s.add_argument("--scrape-budget", type=float, default=25.0, help="stop scraping after this many minutes")
    s.add_argument("--skip-scrape", action="store_true", help="reuse the candidate pool cached by the last run")
    s.add_argument("--discover", choices=("auto", "on", "off"), default="auto",
                   help="find new proxy-list repositories on GitHub (auto: only with GITHUB_TOKEN)")
    s.add_argument("--max-sources", type=int, default=MAX_DISCOVERED, help="cap for discovered sources")
    s.add_argument("--discover-calls", type=int, default=500, help="GitHub API call budget per run")
    s.add_argument("--discover-minutes", type=float, default=8.0, help="time budget for discovery")
    o = p.add_argument_group("output")
    o.add_argument("--output", default=builder.OUTPUT_DIR, help="results directory")
    o.add_argument("--formats", default=",".join(builder.FORMATS), help="comma separated: txt,json,pdf")
    o.add_argument("--readme", default="README.md", help="README to refresh ('' to leave it alone)")
    o.add_argument("--cache", default=".cache", help="directory for source health and the candidate cache")
    o.add_argument("--allow-empty", action="store_true", help="publish even when nothing verified")
    o.add_argument("--allow-private", action="store_true", help="accept private/loopback IPs (local testing)")
    return p.parse_args(argv)


def judges_from_urls(urls: list[str]) -> list[Judge]:
    """Build judges from ``--judge`` URLs; an http and an https URL of the same
    host and path become one judge with both endpoints."""
    merged: dict[tuple[str, str], dict] = {}
    for url in urls:
        parts = urlsplit(url)
        if parts.scheme not in ("http", "https") or not parts.hostname:
            raise SystemExit(f"--judge must be an http(s) URL, got {url!r}")
        path = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
        entry = merged.setdefault((parts.hostname, path), {"http_port": 0, "https_port": 0})
        port = parts.port or (443 if parts.scheme == "https" else 80)
        entry["https_port" if parts.scheme == "https" else "http_port"] = port
    return [Judge(host, path, http_port=ports["http_port"], https_port=ports["https_port"])
            for (host, path), ports in merged.items()]


def clamp_concurrency(tcp: int, probes: int, fd_limit: int) -> tuple[int, int]:
    """Keep ``tcp + probes`` (plus headroom) below the file-descriptor limit."""
    room = fd_limit - 400
    if tcp + probes <= room:
        return tcp, probes
    scale = max(room, 100) / (tcp + probes)
    return max(50, int(tcp * scale)), max(20, int(probes * scale))


def log_header(step: int, total: int, text: str) -> None:
    print(f"[{step}/{total}] {text}", flush=True)


async def run(args: argparse.Namespace) -> int:
    started = time.monotonic()
    cache = Path(args.cache)
    cache.mkdir(parents=True, exist_ok=True)
    formats = tuple(f.strip() for f in args.formats.split(",") if f.strip())
    if not formats or any(f not in builder.FORMATS for f in formats):
        print(f"--formats must be a subset of {','.join(builder.FORMATS)}", file=sys.stderr)
        return 2
    health = Health(cache / "source_health.json")
    registry = Path(args.sources) / "discovered.txt"
    meta: dict = {}

    # 1 ---------------------------------------------------------------- discovery
    log_header(1, 6, "Discovering sources")
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    stale = health.prunable()
    if args.discover == "on" or (args.discover == "auto" and token):
        report = await discover.discover(
            registry, token=token, max_sources=args.max_sources, max_calls=args.discover_calls,
            time_budget=args.discover_minutes * 60, prune_urls=stale)
        print(f"      registry: {report.files_total:,} discovered sources "
              f"(+{report.files_added:,} new, -{report.files_pruned:,} pruned) from "
              f"{report.repos_scanned:,} newly scanned repositories, {report.api_calls} API calls"
              + (f" [{report.stopped}]" if report.stopped else ""))
    else:
        removed = discover.prune_registry(registry, stale) if stale else 0
        print("      discovery skipped (set GITHUB_TOKEN or use --discover on)"
              + (f"; pruned {removed:,} dead sources" if removed else ""))
    health.forget(stale)

    # 2 ---------------------------------------------------------------- scraping
    log_header(2, 6, "Scraping candidates")
    entries, counts = load_sources(args.sources, limit_discovered=args.max_sources)
    meta.update(sources=len(entries), sources_curated=counts["github"] + counts["websites"],
                sources_discovered=counts["discovered"])
    print(f"      {len(entries):,} sources: {counts['github']:,} curated GitHub lists, "
          f"{counts['websites']:,} websites/APIs, {counts['discovered']:,} discovered")
    candidates_cache = cache / "candidates.json"
    scrape_started = time.monotonic()
    if args.skip_scrape and candidates_cache.exists():
        pool = Pool.load(candidates_cache)
        print(f"      reusing cached pool: {len(pool):,} candidates")
    else:
        scraper = Scraper(entries, concurrency=args.scrape_concurrency, health=health,
                          deadline=time.monotonic() + args.scrape_budget * 60 if args.scrape_budget else None,
                          allow_private=args.allow_private)
        report = await scraper.run()
        pool = report.pool
        meta.update(report.stats())
        print(f"      {report.count('ok'):,} sources delivered proxies, {report.count('empty'):,} empty, "
              f"{report.count('failed'):,} unreachable, {report.count('skipped'):,} skipped (backoff/budget)"
              f" -> {len(pool):,} unique candidates")
        if report.outage:
            print("      warning: almost every source failed: looks like a network outage, "
                  "source health was not updated", file=sys.stderr)
        for outcome in top_sources(report, 5):
            print(f"        {outcome.count:>8,}  {outcome.key}")
        health.save()
        pool.dump(candidates_cache)
    meta["scrape_seconds"] = round(time.monotonic() - scrape_started)

    # 3 ---------------------------------------------------------------- previous results
    log_header(3, 6, "Loading previous results")
    previous_file = Path(args.output) / "json" / "all.json"
    previous: dict[str, int] = {}
    if previous_file.exists():
        try:
            previous = previous_masks(json.loads(previous_file.read_text()))
        except (ValueError, OSError):
            previous = {}
    for proxy in previous:
        pool.add_known(proxy)
    print(f"      {len(previous):,} proxies from the last run are re-checked first")
    if not len(pool):
        print("No candidates available, aborting", file=sys.stderr)
        return 1
    candidates = pool.ranked(previous, seed=int(time.time()) // 3600, limit=args.max_candidates)
    if args.max_candidates and len(pool) > args.max_candidates:
        print(f"      testing the best {len(candidates):,} of {len(pool):,} candidates")
    meta["candidates"] = len(pool)

    # 4 ---------------------------------------------------------------- environment
    log_header(4, 6, "Detecting environment")
    fd_limit = raise_fd_limit(args.tcp_concurrency + args.concurrency + 1024)
    tcp_limit, probe_limit = clamp_concurrency(args.tcp_concurrency, args.concurrency, fd_limit)
    if (tcp_limit, probe_limit) != (args.tcp_concurrency, args.concurrency):
        print(f"      note: file descriptor limit {fd_limit} -> concurrency reduced to "
              f"{tcp_limit} connects / {probe_limit} probes")
    judges = judges_from_urls(args.judge) if args.judge else list(DEFAULT_JUDGES)
    env = await detect_environment(judges, timeout=args.timeout, log=lambda m: print(m, flush=True))
    print(f"      egress IPs: {sorted(env.egress) or 'unknown'} | judges reachable: "
          f"{len(env.plain)} plain, {len(env.tls)} TLS")
    tls_context = ssl.create_default_context()
    if not env.verify_tls:
        tls_context.check_hostname = False
        tls_context.verify_mode = ssl.CERT_NONE

    # 5 ---------------------------------------------------------------- verification
    log_header(5, 6, f"Verifying {len(candidates):,} candidates")
    checker = Checker(env.plain, [] if args.no_https_check else env.tls, egress=env.egress,
                      timeout=args.timeout, connect_timeout=args.connect_timeout,
                      ssl_context=tls_context, conn_limit=probe_limit, tcp_limit=tcp_limit)
    validator = Validator(checker, workers=tcp_limit)
    verify_started = time.monotonic()
    result = await validator.verify(
        candidates, rounds=args.rounds, time_budget=args.time_budget * 60,
        controls=() if args.no_controls else CONTROL_TARGETS)
    meta.update(tcp_alive=result.stats["tcp_alive"], first_pass=result.stats["first_pass"],
                rounds=args.rounds, verify_seconds=round(time.monotonic() - verify_started),
                budget_hit=result.stats.get("budget_hit", False))
    print(f"      {result.stats['tcp_alive']:,} reachable on TCP -> {result.stats['first_pass']:,} "
          f"passed the first pass -> {len(result.records):,} alive after confirmation")

    # 6 ---------------------------------------------------------------- publish
    log_header(6, 6, "Publishing")
    if not result.records and not args.allow_empty:
        print("Nothing verified: leaving the previous results untouched "
              "(use --allow-empty to publish an empty list)", file=sys.stderr)
        return 3
    meta["runtime_seconds"] = round(time.monotonic() - started)
    stats = builder.build([r.to_dict() for r in result.records], meta, args.output, formats,
                          readme=args.readme or None)
    print(json.dumps(stats, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(run(parse_args())))

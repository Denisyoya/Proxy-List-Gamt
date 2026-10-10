#!/usr/bin/env python3
"""Scrape, verify and publish free proxies (Proxy-List-Gamt by Gametturxux).

    python main.py                       full run with the defaults
    python main.py --scrape-only         scrape + publish raw lists, no validation
    python main.py --interval 5          run forever, every 5 minutes
    python main.py --max-candidates 20000 --rounds 1      quick run
    python main.py --help                every option

Pipeline: discover sources -> scrape -> accumulate -> detect environment ->
verify -> publish. ``--scrape-only`` stops after the accumulation step and
publishes everything it scraped, unvalidated, to ``results/raw/``.

Every pass *adds* to what is already there: the raw pool and the published lists
are merged, so a proxy that shows up again is overwritten with its newest data
instead of being duplicated, and a proxy that stops showing up expires.
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
import rawpool as rawstore  # noqa: E402
from scraper import Health, Pool, Scraper, previous_masks, top_sources  # noqa: E402
from sources import MAX_DISCOVERED, load_sources  # noqa: E402
from validator import (CONTROL_TARGETS, DEFAULT_JUDGES, Checker, Judge, Validator,  # noqa: E402
                       detect_environment, raise_fd_limit)

DEFAULT_INTERVAL_MINUTES = 5.0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Scrape, verify and publish free proxies",
                                formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    a = p.add_argument_group("automation")
    a.add_argument("--interval", type=float, default=0.0, metavar="MINUTES",
                   help="run forever, starting a new pass every N minutes (0 = one pass). "
                        f"{DEFAULT_INTERVAL_MINUTES:g} matches the GitHub Actions schedule")
    a.add_argument("--scrape-only", action="store_true",
                   help="scrape and publish the raw lists only: no validation, no judges")
    a.add_argument("--keep-published", type=float, default=0.0, metavar="MINUTES",
                   help="keep a verified proxy published for this long after its last "
                        "successful check, even when this pass did not re-check it "
                        "(0 = publish only what this pass verified)")
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
    s.add_argument("--sources", default="sources",
                   help="directory with github.txt/websites.txt/indonesia.txt/discovered.txt")
    s.add_argument("--scrape-concurrency", type=int, default=200, help="simultaneous source downloads")
    s.add_argument("--scrape-budget", type=float, default=25.0, help="stop scraping after this many minutes")
    s.add_argument("--skip-scrape", action="store_true", help="reuse the candidate pool cached by the last run")
    s.add_argument("--discover", choices=("auto", "on", "off"), default="auto",
                   help="find new proxy-list repositories on GitHub (auto: only with GITHUB_TOKEN)")
    s.add_argument("--max-sources", type=int, default=MAX_DISCOVERED, help="cap for discovered sources")
    s.add_argument("--discover-calls", type=int, default=500, help="GitHub API call budget per run")
    s.add_argument("--discover-minutes", type=float, default=8.0, help="time budget for discovery")
    r = p.add_argument_group("raw output (scraped, not validated)")
    r.add_argument("--publish-raw", action="store_true",
                   help="also publish the accumulated pool to --raw-dir (implied by --scrape-only)")
    r.add_argument("--raw-dir", default=builder.RAW_DIR, help="directory for the unvalidated lists")
    r.add_argument("--raw-formats", default=",".join(builder.RAW_FORMATS),
                   help=f"comma separated subset of {','.join(builder.RAW_FORMATS)}")
    r.add_argument("--raw-limit", type=int, default=10000,
                   help="publish at most this many raw entries, freshest first (0 = all)")
    r.add_argument("--raw-ttl-hours", type=float, default=rawstore.DEFAULT_TTL_HOURS,
                   help="a raw entry that is not seen again for this long expires")
    r.add_argument("--raw-pool-limit", type=int, default=250000,
                   help="hard cap for the accumulated pool: the oldest entries are dropped first (0 = no cap)")
    r.add_argument("--no-accumulate", action="store_true",
                   help="start from an empty pool every pass instead of adding to the previous one")
    o = p.add_argument_group("output")
    o.add_argument("--output", default=builder.OUTPUT_DIR, help="results directory")
    o.add_argument("--formats", default=",".join(builder.FORMATS), help="comma separated: txt,json,pdf")
    o.add_argument("--readme", default="README.md", help="README to refresh ('' to leave it alone)")
    o.add_argument("--cache", default=".cache", help="directory for source health and the pools")
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


# --------------------------------------------------------------------- steps


async def step_discover(args: argparse.Namespace, health: Health, meta: dict,
                        step: int, total: int) -> None:
    """Find new proxy-list repositories on GitHub and prune the dead ones."""
    log_header(step, total, "Discovering sources")
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    registry = Path(args.sources) / "discovered.txt"
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


async def step_scrape(args: argparse.Namespace, health: Health, cache: Path,
                      meta: dict, step: int, total: int) -> Pool:
    """Download every registered source into one candidate pool."""
    log_header(step, total, "Scraping candidates")
    entries, counts = load_sources(args.sources, limit_discovered=args.max_sources)
    meta.update(sources=len(entries),
                sources_curated=counts["github"] + counts["websites"] + counts["indonesia"],
                sources_github=counts["github"], sources_websites=counts["websites"],
                sources_indonesia=counts["indonesia"], sources_discovered=counts["discovered"])
    print(f"      {len(entries):,} sources: {counts['github']:,} curated GitHub lists, "
          f"{counts['websites']:,} websites/APIs, {counts['indonesia']:,} Indonesian, "
          f"{counts['discovered']:,} discovered")
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
    return pool


def step_accumulate(args: argparse.Namespace, pool: Pool, cache: Path, meta: dict,
                    step: int, total: int) -> rawstore.RawPool:
    """Add this pass to the persistent pool: new entries are appended, entries
    that are already there are overwritten with their newest data."""
    log_header(step, total, "Accumulating the raw pool")
    path = cache / "raw_pool.json"
    now = int(time.time())
    raw = rawstore.RawPool() if args.no_accumulate else rawstore.RawPool.load(path)
    before = len(raw)
    added = raw.add_pool(pool)
    expired = raw.prune(args.raw_ttl_hours * 3600, now=now)
    overflow = raw.cap(args.raw_pool_limit)
    if not args.no_accumulate:
        raw.dump(path)
    stats = raw.stats(now=now)
    meta.update(stats, raw_ttl_hours=args.raw_ttl_hours)
    print(f"      {before:,} entries before -> +{added:,} new, {len(pool) - added:,} overwritten, "
          f"-{expired:,} expired (>{args.raw_ttl_hours:g} h), -{overflow:,} over the cap "
          f"-> {len(raw):,} in the pool")
    print(f"      {stats['raw_http']:,} http, {stats['raw_socks4']:,} socks4, "
          f"{stats['raw_socks5']:,} socks5, {stats['raw_unspecified']:,} without a protocol claim")
    return raw


def step_publish_raw(args: argparse.Namespace, raw: rawstore.RawPool, meta: dict,
                     step: int, total: int) -> dict:
    """Publish the pool as scraped: thousands of proxies, no validation at all."""
    log_header(step, total, "Publishing raw (unvalidated) proxies")
    formats = tuple(f.strip() for f in args.raw_formats.split(",") if f.strip())
    if any(f not in builder.RAW_FORMATS for f in formats):
        print(f"--raw-formats must be a subset of {','.join(builder.RAW_FORMATS)}", file=sys.stderr)
        raise SystemExit(2)
    readme = (args.readme or None) if args.scrape_only else None
    stats = builder.build_raw(raw.records(), meta, args.raw_dir, formats or builder.RAW_FORMATS,
                              limit=args.raw_limit, readme=readme)
    print(f"      {stats['raw_published']:,} proxies published to {args.raw_dir}/ "
          f"({stats['raw_http']:,} http, {stats['raw_socks4']:,} socks4, "
          f"{stats['raw_socks5']:,} socks5, {stats['raw_unspecified']:,} unspecified)"
          + (f", capped at {args.raw_limit:,} of {stats['raw_total']:,}"
             if args.raw_limit and stats["raw_total"] > args.raw_limit else ""))
    return stats


def load_previous_records(path: Path) -> list[dict]:
    """Records published by an earlier pass (empty when unreadable)."""
    try:
        records = json.loads(path.read_text())
    except (ValueError, OSError):
        return []
    return [r for r in records if isinstance(r, dict) and isinstance(r.get("proxy"), str)]


# ------------------------------------------------------------------- the run


async def run_scrape_only(args: argparse.Namespace) -> int:
    """Scrape and publish, never validate: the fast 5-minute loop."""
    started = time.monotonic()
    cache = Path(args.cache)
    cache.mkdir(parents=True, exist_ok=True)
    health = Health(cache / "source_health.json")
    meta: dict = {"mode": "scrape-only", "validated": False}

    await step_discover(args, health, meta, 1, 4)
    pool = await step_scrape(args, health, cache, meta, 2, 4)
    raw = step_accumulate(args, pool, cache, meta, 3, 4)
    meta["runtime_seconds"] = round(time.monotonic() - started)
    stats = step_publish_raw(args, raw, meta, 4, 4)
    if not stats["raw_published"] and not args.allow_empty:
        print("Nothing scraped: leaving the previous raw lists untouched", file=sys.stderr)
        return 3
    print(json.dumps({k: v for k, v in stats.items() if k.startswith("raw_") or k in
                      ("sources", "sources_ok", "candidates", "scrape_seconds", "runtime_seconds")},
                     indent=1))
    return 0


async def run(args: argparse.Namespace) -> int:
    if args.scrape_only:
        return await run_scrape_only(args)

    started = time.monotonic()
    cache = Path(args.cache)
    cache.mkdir(parents=True, exist_ok=True)
    formats = tuple(f.strip() for f in args.formats.split(",") if f.strip())
    if not formats or any(f not in builder.FORMATS for f in formats):
        print(f"--formats must be a subset of {','.join(builder.FORMATS)}", file=sys.stderr)
        return 2
    health = Health(cache / "source_health.json")
    meta: dict = {"mode": "validated", "validated": True}

    # 1-3 ------------------------------------------------------------ sources
    await step_discover(args, health, meta, 1, 7)
    pool = await step_scrape(args, health, cache, meta, 2, 7)
    raw = step_accumulate(args, pool, cache, meta, 3, 7)
    if args.publish_raw:
        raw_stats = step_publish_raw(args, raw, meta, 3, 7)
        meta["raw_published"] = raw_stats["raw_published"]

    # 4 ---------------------------------------------------------------- previous results
    log_header(4, 7, "Loading previous results")
    previous_file = Path(args.output) / "json" / "all.json"
    previous_records = load_previous_records(previous_file)
    previous = previous_masks(previous_records)
    for proxy in previous:
        pool.add_known(proxy)
    print(f"      {len(previous):,} proxies from the last run are re-checked first"
          + (f"; up to {args.keep_published:g} min old results stay published"
             if args.keep_published else ""))
    if not len(pool):
        print("No candidates available, aborting", file=sys.stderr)
        return 1
    candidates = pool.ranked(previous, seed=int(time.time()) // 3600, limit=args.max_candidates)
    if args.max_candidates and len(pool) > args.max_candidates:
        print(f"      testing the best {len(candidates):,} of {len(pool):,} candidates")
    meta["candidates"] = len(pool)

    # 5 ---------------------------------------------------------------- environment
    log_header(5, 7, "Detecting environment")
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

    # 6 ---------------------------------------------------------------- verification
    log_header(6, 7, f"Verifying {len(candidates):,} candidates")
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
                budget_hit=result.stats.get("budget_hit", False),
                keep_published_minutes=args.keep_published)
    print(f"      {result.stats['tcp_alive']:,} reachable on TCP -> {result.stats['first_pass']:,} "
          f"passed the first pass -> {len(result.records):,} alive after confirmation")

    # 7 ---------------------------------------------------------------- publish
    log_header(7, 7, "Publishing")
    fresh = [r.to_dict() for r in result.records]
    records, merge_stats = builder.merge_published(previous_records, fresh, args.keep_published)
    meta.update(merge_stats)
    if not fresh and not args.allow_empty:
        print("Nothing verified: leaving the previous results untouched "
              "(use --allow-empty to publish an empty list)", file=sys.stderr)
        return 3
    if merge_stats["carried_over"]:
        print(f"      {merge_stats['carried_over']:,} proxies carried over from earlier passes "
              f"(<{args.keep_published:g} min old), {merge_stats['verified_now']:,} verified now")
    meta["runtime_seconds"] = round(time.monotonic() - started)
    stats = builder.build(records, meta, args.output, formats, readme=args.readme or None)
    print(json.dumps(stats, indent=1))
    return 0


async def run_forever(args: argparse.Namespace) -> int:
    """Run the pipeline every ``--interval`` minutes until interrupted."""
    period = max(1.0, args.interval * 60)
    passes = 0
    print(f"loop: a new pass starts every {args.interval:g} minutes "
          f"({'scrape only' if args.scrape_only else 'scrape + validate'}); Ctrl-C stops it",
          flush=True)
    while True:
        passes += 1
        began = time.monotonic()
        print(f"\n===== pass {passes} @ {time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())} =====",
              flush=True)
        try:
            code = await run(args)
        except KeyboardInterrupt:
            raise
        except Exception as error:                       # one bad pass must not kill the loop
            code = 1
            print(f"pass {passes} failed: {error.__class__.__name__}: {error}",
                  file=sys.stderr, flush=True)
        spent = time.monotonic() - began
        print(f"===== pass {passes} finished in {spent:.0f}s (exit {code}) =====", flush=True)
        wait = period - spent
        if wait > 0:
            try:
                await asyncio.sleep(wait)
            except asyncio.CancelledError:
                raise KeyboardInterrupt from None


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.interval > 0:
        try:
            return asyncio.run(run_forever(args))
        except KeyboardInterrupt:
            print("\nstopped", flush=True)
            return 130
    return asyncio.run(run(args))


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "src"))

import build as builder
import sources
from scraper import Scraper
from validator import Validator, detect_egress


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Scrape and validate free proxies")
    parser.add_argument("--timeout", type=float, default=8.0,
                        help="per-request timeout in seconds (default: 8)")
    parser.add_argument("--concurrency", type=int, default=1200,
                        help="parallel checks during validation (default: 1200)")
    parser.add_argument("--scrape-concurrency", type=int, default=120,
                        help="parallel source downloads (default: 120)")
    parser.add_argument("--rounds", type=int, default=2,
                        help="confirmation rounds over verified proxies (default: 2)")
    parser.add_argument("--max-candidates", type=int, default=0,
                        help="cap the candidate pool, 0 means no cap")
    parser.add_argument("--output", default="proxy", help="output directory")
    parser.add_argument("--cache", default=".cache",
                        help="directory for intermediate artefacts")
    parser.add_argument("--skip-scrape", action="store_true",
                        help="reuse the cached candidate pool")
    parser.add_argument("--no-https-check", action="store_true",
                        help="skip the HTTPS/CONNECT capability probe")
    return parser.parse_args()


async def run(args: argparse.Namespace) -> int:
    started = time.time()
    os.makedirs(args.cache, exist_ok=True)
    candidates_path = os.path.join(args.cache, "candidates.json")

    print("[1/5] Building source list")
    entries = sources.build()
    sources.write("sources.txt")
    print(f"      {len(entries)} sources registered")

    print("[2/5] Scraping candidates")
    meta_path = os.path.join(args.cache, "scrape_meta.json")
    if args.skip_scrape and os.path.exists(candidates_path):
        with open(candidates_path) as fh:
            candidates = json.load(fh)
        responding = 0
        if os.path.exists(meta_path):
            with open(meta_path) as fh:
                responding = json.load(fh).get("responding", 0)
        print(f"      reusing cache: {len(candidates)} candidates")
    else:
        scraper = Scraper(entries, concurrency=args.scrape_concurrency)
        candidates = await scraper.run()
        responding = scraper.ok
        with open(candidates_path, "w") as fh:
            json.dump(candidates, fh)
        with open(meta_path, "w") as fh:
            json.dump({"responding": responding}, fh)

    if not candidates:
        print("No candidates scraped, aborting", file=sys.stderr)
        return 1

    if args.max_candidates and len(candidates) > args.max_candidates:
        candidates = dict(list(candidates.items())[:args.max_candidates])
        print(f"      pool capped at {len(candidates)}")

    print("[3/5] Fingerprinting network egress")
    egress_ips, control_hits = await detect_egress()
    print(f"      egress IPs: {sorted(egress_ips) or 'unknown'}")
    if control_hits:
        print(f"      warning: {control_hits} control probes answered, this "
              f"network intercepts outbound traffic; those IPs are blacklisted")
    if not egress_ips:
        print("      warning: egress IP unknown, results cannot be "
              "de-duplicated against local traffic", file=sys.stderr)

    print("[4/5] Validating candidates")
    validator = Validator(
        egress_ips=egress_ips,
        timeout=args.timeout,
        concurrency=args.concurrency,
        check_https=not args.no_https_check,
    )
    verified = await validator.run(
        candidates, salt=0, sink_path=os.path.join(args.cache, "verified.jsonl")
    )

    for index in range(args.rounds):
        if not verified:
            break
        print(f"      confirmation round {index + 1}/{args.rounds}")
        pool = {proxy: record["protocols"] for proxy, record in verified.items()}
        confirmed = await validator.run(pool, salt=17 * (index + 1))
        for proxy, record in confirmed.items():
            entry = verified[proxy]
            entry["checks_passed"] = entry.get("checks_passed", 1) + 1
            entry["latency_ms"] = int(
                (entry["latency_ms"] + record["latency_ms"]) / 2
            )
        for proxy, entry in verified.items():
            entry["checks_total"] = entry.get("checks_total", 1) + 1
            entry["last_ok"] = proxy in confirmed

    records = list(verified.values())
    for record in records:
        record.setdefault("checks_passed", 1)
        record.setdefault("checks_total", 1)
        record.setdefault("last_ok", True)

    print("[5/5] Writing output")
    stats = builder.build(
        records,
        meta={
            "sources": len(entries),
            "sources_responding": responding,
            "candidates": len(candidates),
            "rounds": args.rounds + 1,
            "runtime": round(time.time() - started),
        },
        output_dir=args.output,
    )
    print(json.dumps(stats, indent=1))
    return 0


if __name__ == "__main__":
    try:
        import resource
        resource.setrlimit(resource.RLIMIT_NOFILE, (65535, 65535))
    except Exception:
        pass
    sys.exit(asyncio.run(run(parse_args())))

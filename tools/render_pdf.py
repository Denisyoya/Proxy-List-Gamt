#!/usr/bin/env python3
"""Re-render ``results/pdf/*.pdf`` from the published JSON.

The 5-minute loops publish ``txt`` and ``json`` only: PDFs are the same data in a
printable layout and re-generating them six times an hour would only grow the
repository. This script is what the slower ``Publish PDF`` schedule runs - no
scraping, no validation, no network::

    python tools/render_pdf.py                       # results/ -> results/pdf/
    python tools/render_pdf.py --results out --readme README.md
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import build as builder  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--results", default=builder.OUTPUT_DIR, help="results directory")
    p.add_argument("--readme", default="", help="README to refresh ('' = leave it alone)")
    args = p.parse_args(argv)

    root = Path(args.results)
    records_file = root / "json" / "all.json"
    if not records_file.exists():
        print(f"{records_file} does not exist: run the pipeline first", file=sys.stderr)
        return 1
    try:
        records = json.loads(records_file.read_text())
    except ValueError as error:
        print(f"{records_file} is not valid JSON: {error}", file=sys.stderr)
        return 1
    if not isinstance(records, list):
        print(f"{records_file} must hold a list of records", file=sys.stderr)
        return 1

    meta: dict = {}
    stats_file = root / "json" / "stats.json"
    if stats_file.exists():
        try:
            loaded = json.loads(stats_file.read_text())
            meta = loaded if isinstance(loaded, dict) else {}
        except ValueError:
            meta = {}
    # ``generated_at`` of the published run is kept so the PDFs carry the same stamp
    # as the lists they were rendered from.
    stats = builder.build(records, meta, str(root), formats=("pdf",), readme=args.readme or None)
    print(f"re-rendered {len(builder.GROUPS)} PDFs in {root / 'pdf'} from "
          f"{len(records):,} published records (generated {stats['generated_at']})")
    return 0


if __name__ == "__main__":
    sys.exit(main())

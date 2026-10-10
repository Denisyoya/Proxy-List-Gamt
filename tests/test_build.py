import io
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

import support  # noqa: F401
import build
import pdfgen
from common import utc_stamp


def rec(proxy, protocol, ms, http=True, https=False, passed=3, total=3):
    return {"proxy": proxy, "protocol": protocol, "url": f"{protocol}://{proxy}", "latency_ms": ms,
            "http": http, "https": https, "exit_ip": "9.9.9.9",
            "checks_passed": passed, "checks_total": total}


RECORDS = [
    rec("1.1.1.1:80", "http", 300, https=True),
    rec("2.2.2.2:8080", "http", 100),                      # plain HTTP only
    rec("3.3.3.3:3128", "http", 200, http=False, https=True),  # CONNECT only
    rec("4.4.4.4:1080", "socks5", 50, https=True),
    rec("5.5.5.5:1080", "socks4", 400, passed=2),
    rec("1.1.1.1:1080", "socks5", 250),                    # same IP, other port + protocol
]


class Lists(unittest.TestCase):
    def test_groups(self):
        names = lambda g: [r["proxy"] for r in build.select(RECORDS, g)]
        self.assertEqual(names("all"), ["4.4.4.4:1080", "2.2.2.2:8080", "3.3.3.3:3128", "1.1.1.1:1080",
                                        "1.1.1.1:80", "5.5.5.5:1080"])
        self.assertEqual(names("http"), ["2.2.2.2:8080", "1.1.1.1:80"])           # relays plain HTTP
        self.assertEqual(names("https"), ["3.3.3.3:3128", "1.1.1.1:80"])          # CONNECT + TLS
        self.assertEqual(names("socks"), ["4.4.4.4:1080", "1.1.1.1:1080", "5.5.5.5:1080"])
        self.assertEqual(names("socks5"), ["4.4.4.4:1080", "1.1.1.1:1080"])
        self.assertEqual(names("socks4"), ["5.5.5.5:1080"])

    def test_sorted_fastest_first(self):
        latencies = [r["latency_ms"] for r in build.select(RECORDS, "all")]
        self.assertEqual(latencies, sorted(latencies))

    def test_every_all_entry_is_in_exactly_the_right_protocol_list(self):
        all_urls = set(build.txt_lines(build.select(RECORDS, "all"), "all"))
        union = set()
        for group in ("http", "socks4", "socks5"):
            union |= {f"{r['protocol']}://{r['proxy']}" for r in build.select(RECORDS, group)}
        union |= {f"http://{r['proxy']}" for r in build.select(RECORDS, "https")}
        self.assertEqual(all_urls, union)   # nothing lost, nothing invented


class Writing(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.out = Path(self.tmp.name) / "results"

    def test_layout_and_content(self):
        stats = build.build(RECORDS, {"sources": 10, "candidates": 99, "rounds": 2}, str(self.out), readme=None)
        for fmt in ("txt", "json", "pdf"):
            for group in build.GROUPS:
                path = self.out / fmt / f"{group}.{fmt}"
                self.assertTrue(path.exists(), path)
        self.assertTrue((self.out / "json" / "stats.json").exists())
        self.assertEqual(sorted(p.name for p in (self.out / "txt").iterdir()),
                         sorted(f"{g}.txt" for g in build.GROUPS))
        # txt: scheme only where protocols are mixed
        self.assertEqual((self.out / "txt" / "all.txt").read_text().splitlines()[0], "socks5://4.4.4.4:1080")
        self.assertTrue(all("://" in l for l in (self.out / "txt" / "socks.txt").read_text().splitlines()))
        self.assertEqual((self.out / "txt" / "http.txt").read_text(), "2.2.2.2:8080\n1.1.1.1:80\n")
        self.assertEqual((self.out / "txt" / "socks4.txt").read_text(), "5.5.5.5:1080\n")
        # json
        data = json.loads((self.out / "json" / "all.json").read_text())
        self.assertEqual(len(data), 6)
        self.assertEqual(set(data[0]), {"proxy", "protocol", "url", "latency_ms", "http", "https",
                                        "exit_ip", "checks_passed", "checks_total"})
        saved = json.loads((self.out / "json" / "stats.json").read_text())
        self.assertEqual(saved, stats)
        self.assertEqual((stats["live"], stats["http"], stats["https"], stats["socks4"], stats["socks5"]),
                         (6, 2, 2, 1, 2))
        self.assertEqual(stats["unique_endpoints"], 6)
        self.assertEqual(stats["sources"], 10)

    def test_empty_results_still_produce_every_file(self):
        build.build([], {}, str(self.out), readme=None)
        self.assertEqual((self.out / "txt" / "all.txt").read_text(), "")
        self.assertEqual(json.loads((self.out / "json" / "all.json").read_text()), [])
        self.assertTrue((self.out / "pdf" / "all.pdf").read_bytes().startswith(b"%PDF-1.4"))

    def test_formats_can_be_limited(self):
        build.build(RECORDS, {}, str(self.out), formats=("txt",), readme=None)
        self.assertTrue((self.out / "txt").exists())
        self.assertFalse((self.out / "json").exists())
        self.assertFalse((self.out / "pdf").exists())

    def test_rewrite_is_atomic_and_idempotent(self):
        build.build(RECORDS, {"generated_at": "t"}, str(self.out), readme=None)
        first = (self.out / "txt" / "all.txt").read_bytes()
        build.build(RECORDS, {"generated_at": "t"}, str(self.out), readme=None)
        self.assertEqual(first, (self.out / "txt" / "all.txt").read_bytes())
        self.assertFalse([p for p in self.out.rglob("*") if p.name.startswith(".")])  # no temp leftovers

    def test_readme_blocks_are_updated_in_place(self):
        readme = Path(self.tmp.name) / "README.md"
        readme.write_text("intro\n<!-- stats:start -->\nold\n<!-- stats:end -->\nmiddle\n"
                          "<!-- footer:start -->\nold\n<!-- footer:end -->\nend\n")
        build.build(RECORDS, {"sources": 5, "sources_curated": 3, "sources_discovered": 2, "rounds": 2},
                    str(self.out), readme=str(readme))
        text = readme.read_text()
        self.assertTrue(text.startswith("intro\n<!-- stats:start -->"))
        self.assertIn("| Live proxies | **6** |", text)
        self.assertIn("(3 curated + 2 discovered)", text)
        self.assertIn("Script by Gametturxux.", text)
        self.assertIn("middle", text)
        self.assertNotIn("old", text)


RAW_RECORDS = [
    {"proxy": "9.9.9.9:3128", "protocols": ["http"], "listings": 2, "hits": 3,
     "first_seen": 10, "last_seen": 500, "age_seconds": 0},
    {"proxy": "1.1.1.1:1080", "protocols": ["socks5", "socks4"], "listings": 1, "hits": 1,
     "first_seen": 100, "last_seen": 900, "age_seconds": 0},
    {"proxy": "8.8.8.8:8080", "protocols": [], "listings": 5, "hits": 9,
     "first_seen": 1, "last_seen": 999, "age_seconds": 0},
]


def stamped(proxy, protocol, minutes_ago, now, **extra):
    """A record as it was published *minutes_ago* (``checked_at`` drives the keep window)."""
    record = rec(proxy, protocol, 200)
    record["checked_at"] = utc_stamp(now - timedelta(minutes=minutes_ago))
    record.update(extra)
    return record


class Merging(unittest.TestCase):
    """``--keep-published``: keep adding, overwrite what is already there."""

    NOW = datetime(2026, 10, 10, 12, 0, 0, tzinfo=timezone.utc)

    def test_a_fresh_check_overwrites_the_published_record(self):
        previous = [stamped("1.1.1.1:80", "http", 20, self.NOW, latency_ms=999)]
        fresh = [rec("1.1.1.1:80", "http", 120)]
        merged, stats = build.merge_published(previous, fresh, keep_minutes=60, now=self.NOW)
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["latency_ms"], 120)          # the new measurement wins
        self.assertNotIn("carried_over", merged[0])
        self.assertEqual(stats, {"verified_now": 1, "carried_over": 0, "published": 1})

    def test_records_that_were_not_rechecked_survive_the_keep_window(self):
        previous = [stamped("1.1.1.1:80", "http", 10, self.NOW),
                    stamped("2.2.2.2:1080", "socks5", 59, self.NOW)]
        merged, stats = build.merge_published(previous, [rec("3.3.3.3:80", "http", 100)],
                                              keep_minutes=60, now=self.NOW)
        self.assertEqual({r["proxy"] for r in merged}, {"1.1.1.1:80", "2.2.2.2:1080", "3.3.3.3:80"})
        self.assertEqual(stats["carried_over"], 2)
        self.assertTrue(all(r["carried_over"] for r in merged if r["proxy"] != "3.3.3.3:80"))

    def test_records_older_than_the_window_expire(self):
        previous = [stamped("1.1.1.1:80", "http", 61, self.NOW)]
        merged, stats = build.merge_published(previous, [], keep_minutes=60, now=self.NOW)
        self.assertEqual(merged, [])
        self.assertEqual(stats["carried_over"], 0)

    def test_the_same_endpoint_can_be_carried_for_one_protocol_only(self):
        previous = [stamped("1.1.1.1:8080", "http", 5, self.NOW),
                    stamped("1.1.1.1:8080", "socks5", 5, self.NOW)]
        fresh = [rec("1.1.1.1:8080", "socks5", 90)]            # only SOCKS5 verified again
        merged, _ = build.merge_published(previous, fresh, keep_minutes=60, now=self.NOW)
        by_protocol = {r["protocol"]: r for r in merged}
        self.assertEqual(set(by_protocol), {"http", "socks5"})
        self.assertTrue(by_protocol["http"]["carried_over"])
        self.assertNotIn("carried_over", by_protocol["socks5"])
        self.assertEqual(by_protocol["socks5"]["latency_ms"], 90)

    def test_keep_zero_publishes_only_what_this_run_verified(self):
        previous = [stamped("1.1.1.1:80", "http", 1, self.NOW)]
        merged, stats = build.merge_published(previous, [rec("2.2.2.2:80", "http", 100)],
                                              keep_minutes=0, now=self.NOW)
        self.assertEqual([r["proxy"] for r in merged], ["2.2.2.2:80"])
        self.assertEqual(stats, {"verified_now": 1, "carried_over": 0, "published": 1})

    def test_a_record_without_a_stamp_is_never_carried(self):
        old = rec("1.1.1.1:80", "http", 100)                    # published before stamps existed
        merged, _ = build.merge_published([old], [], keep_minutes=60, now=self.NOW)
        self.assertEqual(merged, [])

    def test_junk_in_the_previous_file_is_ignored(self):
        merged, _ = build.merge_published([{}, {"proxy": None}, "junk"], [rec("1.1.1.1:80", "http", 5)],
                                          keep_minutes=60, now=self.NOW)
        self.assertEqual([r["proxy"] for r in merged], ["1.1.1.1:80"])


class RawPublishing(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.out = Path(self.tmp.name) / "results" / "raw"

    def test_layout(self):
        stats = build.build_raw(RAW_RECORDS, {"sources": 4, "candidates": 3}, str(self.out))
        for group in build.RAW_GROUPS:
            self.assertTrue((self.out / "txt" / f"{group}.txt").exists(), group)
            self.assertTrue((self.out / "json" / f"{group}.json").exists(), group)
        self.assertFalse((self.out / "pdf").exists())            # bulk data: no PDF export
        self.assertEqual(stats["raw_published"], 3)
        self.assertEqual((stats["raw_http"], stats["raw_socks4"], stats["raw_socks5"]), (1, 1, 1))
        self.assertEqual(stats["raw_unspecified"], 1)
        self.assertFalse(stats["validated"])
        self.assertEqual(json.loads((self.out / "json" / "stats.json").read_text()), stats)

    def test_lines(self):
        build.build_raw(RAW_RECORDS, {}, str(self.out))
        read = lambda name: (self.out / "txt" / f"{name}.txt").read_text().splitlines()
        self.assertEqual(sorted(read("all")), sorted(["http://9.9.9.9:3128", "socks5://1.1.1.1:1080",
                                                      "socks4://1.1.1.1:1080", "8.8.8.8:8080"]))
        self.assertEqual(read("http"), ["9.9.9.9:3128"])         # bare ip:port in a single-protocol list
        self.assertEqual(read("socks4"), ["1.1.1.1:1080"])
        self.assertEqual(read("socks5"), ["1.1.1.1:1080"])
        self.assertEqual(sorted(read("socks")), ["socks4://1.1.1.1:1080", "socks5://1.1.1.1:1080"])

    def test_order_is_numeric_not_lexicographic(self):
        """9.9.9.9 sorts after 10.0.0.1: a stable order keeps the git diff small."""
        records = [{"proxy": p, "protocols": ["http"], "listings": 1, "hits": 1,
                    "first_seen": 1, "last_seen": 1, "age_seconds": 0}
                   for p in ("10.0.0.1:80", "9.9.9.9:80", "2.2.2.2:80", "100.1.1.1:80")]
        build.build_raw(records, {}, str(self.out))
        lines = (self.out / "txt" / "http.txt").read_text().splitlines()
        self.assertEqual(lines, ["2.2.2.2:80", "9.9.9.9:80", "10.0.0.1:80", "100.1.1.1:80"])

    def test_limit_keeps_the_freshest(self):
        records = sorted(RAW_RECORDS, key=lambda r: -r["last_seen"])     # pool order: freshest first
        stats = build.build_raw(records, {}, str(self.out), limit=2)
        self.assertEqual(stats["raw_published"], 2)
        lines = (self.out / "txt" / "all.txt").read_text().splitlines()
        self.assertNotIn("9.9.9.9:3128", "\n".join(lines))       # the oldest entry was cut
        self.assertIn("8.8.8.8:8080", lines)

    def test_json_is_compact_and_carries_the_pool_metadata(self):
        build.build_raw(RAW_RECORDS, {}, str(self.out))
        raw_text = (self.out / "json" / "all.json").read_text()
        self.assertNotIn("\n ", raw_text)                        # one line, no indentation
        data = json.loads(raw_text)
        self.assertEqual(set(data[0]), {"proxy", "protocols", "listings", "hits",
                                        "first_seen", "last_seen", "age_seconds"})

    def test_formats_can_be_limited(self):
        build.build_raw(RAW_RECORDS, {}, str(self.out), formats=("txt",))
        self.assertTrue((self.out / "txt").exists())
        self.assertFalse((self.out / "json").exists())

    def test_empty_pool_still_writes_every_file(self):
        build.build_raw([], {}, str(self.out))
        self.assertEqual((self.out / "txt" / "all.txt").read_text(), "")
        self.assertEqual(json.loads((self.out / "json" / "all.json").read_text()), [])

    def test_readme_raw_block(self):
        readme = Path(self.tmp.name) / "README.md"
        readme.write_text("intro\n<!-- raw-stats:start -->\nold\n<!-- raw-stats:end -->\nend\n")
        build.build_raw(RAW_RECORDS, {"sources": 4, "raw_total": 9, "raw_ttl_hours": 6},
                        str(self.out), readme=str(readme))
        text = readme.read_text()
        self.assertIn("| Raw proxies published | **3** |", text)
        self.assertIn("| Raw pool size (accumulated) | 9 |", text)
        self.assertIn("no validation", text)
        self.assertNotIn("old", text)
        self.assertIn("intro", text)

    def test_a_missing_raw_block_leaves_the_readme_alone(self):
        readme = Path(self.tmp.name) / "README.md"
        readme.write_text("no markers here\n")
        build.build_raw(RAW_RECORDS, {}, str(self.out), readme=str(readme))
        self.assertEqual(readme.read_text(), "no markers here\n")


class Pdf(unittest.TestCase):
    def read(self, data):
        from pypdf import PdfReader
        return PdfReader(io.BytesIO(data), strict=True)

    def test_round_trip_small(self):
        rows = [f"socks5 10.0.0.{i}:1080".replace("10.0.0", "8.8.8") + "   123 tls" for i in range(1, 30)]
        data = pdfgen.render_table("Title (with) parens \\ and ünïcode", ["line one", "line two"],
                                   "proto address", rows, footer="repo")
        reader = self.read(data)
        self.assertEqual(len(reader.pages), 1)
        text = reader.pages[0].extract_text()
        for row in rows:
            self.assertIn(row.strip(), text)
        self.assertIn("line one", text)
        self.assertIn("page 1 of 1", text)
        self.assertEqual(reader.metadata.title, "Title (with) parens \\ and ünïcode")

    def test_multi_page_every_row_survives_in_order_of_columns(self):
        rows = [f"http   8.8.{i // 250}.{i % 250 + 1}:8080 {i:>5} tls" for i in range(2000)]
        data = pdfgen.render_table("Big", ["x"], "proto address ms", rows)
        reader = self.read(data)
        self.assertGreater(len(reader.pages), 5)
        seen = []
        for page in reader.pages:
            seen += [l.strip() for l in page.extract_text().splitlines()]
        missing = [r for r in rows if r.strip() not in set(seen)]
        self.assertEqual(missing, [])
        for index, page in enumerate(reader.pages, 1):
            self.assertIn(f"page {index} of {len(reader.pages)}", page.extract_text())

    def test_page_capacity_is_respected(self):
        per_first, per_next = pdfgen.rows_per_page(True), pdfgen.rows_per_page(False)
        self.assertLess(per_first, per_next)
        rows = ["a" * 10] * (per_first * 3 + 1)            # exactly one row too many for page 1
        reader = self.read(pdfgen.render_table("t", [], "h", rows, columns=3))
        self.assertEqual(len(reader.pages), 2)

    def test_empty_table(self):
        reader = self.read(pdfgen.render_table("Empty", ["nothing here"], "h", []))
        self.assertEqual(len(reader.pages), 1)
        self.assertIn("nothing here", reader.pages[0].extract_text())

    def test_built_pdf_matches_the_txt_list(self):
        with tempfile.TemporaryDirectory() as tmp:
            build.build(RECORDS, {}, tmp, readme=None)
            text = "\n".join(p.extract_text() for p in self.read((Path(tmp) / "pdf" / "all.pdf").read_bytes()).pages)
            for record in RECORDS:
                self.assertIn(record["proxy"], text)


if __name__ == "__main__":
    unittest.main()

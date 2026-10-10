"""The persistent unvalidated pool: add continuously, overwrite, expire."""
import json
import tempfile
import time
import unittest
from pathlib import Path

import support  # noqa: F401

from rawpool import FIRST, HITS, LAST, LISTINGS, MASK, RawPool
from scraper import Pool


def pool_of(entries: dict[str, int]) -> Pool:
    """A scraper.Pool built from ``ip:port -> packed`` (listings << 4 | unknown << 3 | mask)."""
    pool = Pool()
    pool.restore(entries)
    return pool


def packed(listings: int, mask: int) -> int:
    return (listings << 4) | (8 if not mask else 0) | mask


class Upsert(unittest.TestCase):
    def test_new_entries_are_added(self):
        raw = RawPool()
        now = 1_000
        added = raw.add_pool(pool_of({"1.1.1.1:80": packed(2, 1), "2.2.2.2:1080": packed(1, 4)}), now=now)
        self.assertEqual(added, 2)
        self.assertEqual(len(raw), 2)
        self.assertIn("1.1.1.1:80", raw)
        entry = raw.get("1.1.1.1:80")
        self.assertEqual(entry, [1, 2, 1, now, now])       # mask, listings, hits, first, last

    def test_an_entry_that_is_there_again_is_overwritten_not_duplicated(self):
        raw = RawPool()
        raw.add_pool(pool_of({"1.1.1.1:80": packed(1, 1)}), now=1_000)
        added = raw.add_pool(pool_of({"1.1.1.1:80": packed(3, 1)}), now=2_000)
        self.assertEqual(added, 0)                         # nothing new
        self.assertEqual(len(raw), 1)                      # no duplicate
        mask, listings, hits, first, last = raw.get("1.1.1.1:80")
        self.assertEqual((listings, hits, first, last), (3, 2, 1_000, 2_000))
        self.assertEqual(mask, 1)

    def test_protocol_claims_are_unioned(self):
        """Listed as HTTP by one source and SOCKS5 by another -> it claims both."""
        raw = RawPool()
        raw.add_pool(pool_of({"1.1.1.1:8080": packed(1, 1)}), now=1_000)
        raw.add_pool(pool_of({"1.1.1.1:8080": packed(1, 4)}), now=1_100)
        self.assertEqual(raw.protocols("1.1.1.1:8080"), ["http", "socks5"])

    def test_add_many_takes_a_plain_found_mapping(self):
        raw = RawPool()
        self.assertEqual(raw.add_many({"3.3.3.3:3128": 1}, listings={"3.3.3.3:3128": 4}, now=7), 1)
        self.assertEqual(raw.get("3.3.3.3:3128"), [1, 4, 1, 7, 7])
        self.assertEqual(raw.add_many({"3.3.3.3:3128": 0}, now=9), 0)   # no claim: nothing is lost
        self.assertEqual(raw.get("3.3.3.3:3128")[MASK], 1)
        self.assertEqual(raw.get("3.3.3.3:3128")[LAST], 9)


class Expiry(unittest.TestCase):
    def test_entries_expire_after_the_ttl(self):
        raw = RawPool()
        raw.add_pool(pool_of({"1.1.1.1:80": packed(1, 1)}), now=1_000)
        raw.add_pool(pool_of({"2.2.2.2:80": packed(1, 1)}), now=1_000)
        raw.add_pool(pool_of({"2.2.2.2:80": packed(1, 1)}), now=5_000)   # seen again: refreshed
        self.assertEqual(raw.prune(3_600, now=5_000), 1)                 # 1.1.1.1 is 4000 s old
        self.assertNotIn("1.1.1.1:80", raw)
        self.assertIn("2.2.2.2:80", raw)

    def test_a_zero_ttl_never_expires(self):
        raw = RawPool()
        raw.add_pool(pool_of({"1.1.1.1:80": packed(1, 1)}), now=1_000)
        self.assertEqual(raw.prune(0, now=10 ** 9), 0)
        self.assertEqual(len(raw), 1)

    def test_the_cap_drops_the_oldest(self):
        raw = RawPool()
        for index in range(5):
            raw.add_pool(pool_of({f"10.0.0.{index}:80": packed(1, 1)}), now=1_000 + index)
        self.assertEqual(raw.cap(2), 3)
        self.assertEqual(sorted(raw), ["10.0.0.3:80", "10.0.0.4:80"])
        self.assertEqual(raw.cap(0), 0)                    # 0 = no cap
        self.assertEqual(raw.cap(50), 0)                   # below the cap: untouched


class Persistence(unittest.TestCase):
    def test_round_trip(self):
        raw = RawPool()
        raw.add_pool(pool_of({"1.1.1.1:80": packed(2, 1), "2.2.2.2:1080": packed(1, 6)}), now=42)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "nested" / "raw_pool.json"
            raw.dump(path)
            loaded = RawPool.load(path)
        self.assertEqual(len(loaded), 2)
        self.assertEqual(loaded.get("1.1.1.1:80"), raw.get("1.1.1.1:80"))
        self.assertEqual(loaded.get("2.2.2.2:1080"), raw.get("2.2.2.2:1080"))

    def test_missing_or_broken_file_is_an_empty_pool(self):
        self.assertEqual(len(RawPool.load("/nonexistent/raw_pool.json")), 0)
        with tempfile.TemporaryDirectory() as tmp:
            broken = Path(tmp) / "raw_pool.json"
            broken.write_text("{not json")
            self.assertEqual(len(RawPool.load(broken)), 0)
            broken.write_text(json.dumps({"1.1.1.1:80": [1, 2], "x": "junk", "2.2.2.2:80": [1, 1, 1, 5, 5]}))
            loaded = RawPool.load(broken)                  # junk entries are skipped, good ones kept
            self.assertEqual(sorted(loaded), ["2.2.2.2:80"])

    def test_no_temporary_file_is_left_behind(self):
        raw = RawPool()
        raw.add_pool(pool_of({"1.1.1.1:80": packed(1, 1)}), now=1)
        with tempfile.TemporaryDirectory() as tmp:
            raw.dump(Path(tmp) / "raw_pool.json")
            self.assertEqual(sorted(p.name for p in Path(tmp).iterdir()), ["raw_pool.json"])


class Publishing(unittest.TestCase):
    def setUp(self):
        self.raw = RawPool()
        self.raw.add_pool(pool_of({
            "1.1.1.1:80": packed(3, 1),          # http, three listings, oldest
            "2.2.2.2:1080": packed(1, 4),        # socks5
            "3.3.3.3:1080": packed(1, 6),        # socks4 + socks5
            "4.4.4.4:8080": packed(1, 0),        # nobody claimed a protocol
        }), now=1_000)
        self.raw.add_pool(pool_of({"1.1.1.1:80": packed(3, 1)}), now=2_000)   # seen again

    def test_records_are_freshest_first(self):
        self.assertEqual([r["proxy"] for r in self.raw.records(now=2_000)][0], "1.1.1.1:80")

    def test_record_fields(self):
        record = next(r for r in self.raw.records(now=2_500) if r["proxy"] == "1.1.1.1:80")
        self.assertEqual(record["protocols"], ["http"])
        self.assertEqual(record["listings"], 3)
        self.assertEqual(record["hits"], 2)
        self.assertEqual(record["first_seen"], 1_000)
        self.assertEqual(record["last_seen"], 2_000)
        self.assertEqual(record["age_seconds"], 500)

    def test_groups(self):
        records = self.raw.records(now=2_000)
        self.assertEqual(len(self.raw.group(records, "all")), 4)
        self.assertEqual([r["proxy"] for r in self.raw.group(records, "http")], ["1.1.1.1:80"])
        self.assertEqual(sorted(r["proxy"] for r in self.raw.group(records, "socks")),
                         ["2.2.2.2:1080", "3.3.3.3:1080"])
        self.assertEqual([r["proxy"] for r in self.raw.group(records, "socks4")], ["3.3.3.3:1080"])
        self.assertEqual(sorted(r["proxy"] for r in self.raw.group(records, "socks5")),
                         ["2.2.2.2:1080", "3.3.3.3:1080"])

    def test_stats(self):
        stats = self.raw.stats(now=2_000)
        self.assertEqual(stats["raw_total"], 4)
        self.assertEqual((stats["raw_http"], stats["raw_socks4"], stats["raw_socks5"]), (1, 1, 2))
        self.assertEqual(stats["raw_unspecified"], 1)
        self.assertEqual(stats["raw_multi_hits"], 1)

    def test_ranked_returns_the_scraper_contract(self):
        ranked = self.raw.ranked()
        self.assertEqual(ranked[0][0], "1.1.1.1:80")                 # freshest first
        self.assertEqual(ranked[0][1], 1)                            # test the claimed protocol
        self.assertEqual(ranked[0][2], 6)                            # fall back to the socks bits
        unknown = next(entry for entry in ranked if entry[0] == "4.4.4.4:8080")
        self.assertEqual(unknown[1], 7)                              # nothing claimed: test everything
        self.assertEqual(unknown[2], 0)
        self.assertEqual(len(self.raw.ranked(limit=2)), 2)

    def test_to_pool_feeds_the_validator(self):
        candidates = self.raw.to_pool()
        self.assertEqual(len(candidates), 4)
        self.assertEqual(candidates.mask("3.3.3.3:1080"), 6)
        self.assertEqual(candidates.listings("1.1.1.1:80"), 3)
        ranked = candidates.ranked(seed=1)
        self.assertEqual({proxy for proxy, _, _ in ranked}, set(self.raw))


class Clock(unittest.TestCase):
    def test_now_defaults_to_the_wall_clock(self):
        raw = RawPool()
        before = int(time.time())
        raw.add_pool(pool_of({"1.1.1.1:80": packed(1, 1)}))
        entry = raw.get("1.1.1.1:80")
        self.assertGreaterEqual(entry[LAST], before)
        self.assertLessEqual(entry[LAST], int(time.time()))
        self.assertEqual(entry[FIRST], entry[LAST])          # first seen == last seen on a new entry
        self.assertEqual(entry[HITS], 1)

    def test_field_order_is_stable(self):
        """The on-disk layout is a list, so the field order is part of the format."""
        self.assertEqual([MASK, LISTINGS, HITS, FIRST, LAST], [0, 1, 2, 3, 4])


if __name__ == "__main__":
    unittest.main()

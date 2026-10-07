import asyncio
import json
import tempfile
import time
import unittest
from collections import Counter
from pathlib import Path

import support  # noqa: F401
from aiohttp import web

from common import ALL_MASK, HTTP, SOCKS4, SOCKS5
from scraper import Health, Pool, Scraper, previous_masks
from sources import Source


class SourceServer:
    """A fake website serving proxy lists in many shapes."""

    def __init__(self):
        self.hits: Counter = Counter()
        self.page_log: list[int] = []
        app = web.Application()
        app.router.add_get("/plain", self.plain)
        app.router.add_get("/plain2", self.plain2)
        app.router.add_get("/table", self.table)
        app.router.add_get("/json", self.json)
        app.router.add_get("/gone", self.gone)
        app.router.add_get("/error", self.error)
        app.router.add_get("/empty", self.empty)
        app.router.add_get("/paged", self.paged)
        app.router.add_get("/repeat", self.repeat)
        app.router.add_get("/redirect", self.redirect)
        app.router.add_get("/slow", self.slow)
        app.router.add_get("/big", self.big)
        self.app = app

    async def start(self):
        self.runner = web.AppRunner(self.app)
        await self.runner.setup()
        site = web.TCPSite(self.runner, "127.0.0.1", 0)
        await site.start()
        self.port = site._server.sockets[0].getsockname()[1]
        return self

    async def close(self):
        await self.runner.cleanup()

    def url(self, path):
        return f"http://127.0.0.1:{self.port}{path}"

    # handlers --------------------------------------------------------
    async def plain(self, request):
        self.hits["plain"] += 1
        return web.Response(text="8.8.8.1:80\n8.8.8.2:81\n# c\n8.8.8.1:80\n")

    async def plain2(self, request):
        self.hits["plain2"] += 1
        return web.Response(text="8.8.8.9:90\n")

    async def table(self, request):
        self.hits["table"] += 1
        return web.Response(text="<table><tr><td>8.8.8.2</td><td>81</td><td>Socks5</td></tr>"
                                 "<tr><td>8.8.8.3</td><td>82</td><td>HTTP</td></tr></table>",
                            content_type="text/html")

    async def json(self, request):
        self.hits["json"] += 1
        return web.json_response([{"ip": "8.8.8.4", "port": "83", "protocols": ["socks4"]},
                                  {"ip": "8.8.8.1", "port": 80, "protocols": ["socks4"]}])

    async def gone(self, request):
        self.hits["gone"] += 1
        return web.Response(status=404)

    async def error(self, request):
        self.hits["error"] += 1
        return web.Response(status=503)

    async def empty(self, request):
        self.hits["empty"] += 1
        return web.Response(text="no proxies today")

    async def paged(self, request):
        page = int(request.query["page"])
        self.page_log.append(page)
        rows = {1: "8.8.1.1:80\n8.8.1.2:80", 2: "8.8.2.1:80\n8.8.2.2:80", 3: "8.8.3.1:80", 4: "nothing"}
        return web.Response(text=rows.get(page, "8.8.9.9:80"))

    async def repeat(self, request):
        self.page_log.append(int(request.query["page"]))
        return web.Response(text="8.8.7.1:80\n8.8.7.2:80")

    async def redirect(self, request):
        raise web.HTTPFound("/plain")

    async def slow(self, request):
        await asyncio.sleep(10)
        return web.Response(text="8.8.5.5:80")

    async def big(self, request):
        return web.Response(text="".join(f"8.8.{i // 250}.{i % 250 + 1}:80\n" for i in range(5000)))


class ScraperCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.server = await SourceServer().start()
        self.addAsyncCleanup(self.server.close)

    def scraper(self, sources, **kw):
        kw.setdefault("allow_private", False)
        kw.setdefault("quiet", True)
        kw.setdefault("timeout", 5)
        return Scraper(sources, **kw)

    def src(self, path, hint="mixed", **kw):
        return Source(hint, (self.server.url(path),), **kw)


class Fetching(ScraperCase):
    async def test_formats_merge_into_one_pool_with_masks_and_listing_counts(self):
        report = await self.scraper([self.src("/plain", "http"), self.src("/table"),
                                     self.src("/json")]).run()
        pool = report.pool
        self.assertEqual(len(pool), 4)
        self.assertEqual(pool.mask("8.8.8.1:80"), HTTP | SOCKS4)     # plain says http, json says socks4
        self.assertEqual(pool.listings("8.8.8.1:80"), 2)
        self.assertEqual(pool.mask("8.8.8.2:81"), HTTP | SOCKS5)
        self.assertEqual(pool.mask("8.8.8.3:82"), HTTP)
        self.assertEqual(report.stats()["sources_ok"], 3)
        self.assertEqual(report.stats()["candidates"], 4)

    async def test_states_ok_empty_failed(self):
        report = await self.scraper([self.src("/plain"), self.src("/empty"), self.src("/gone"),
                                     self.src("/error")]).run()
        states = {o.key.rsplit("/", 1)[1]: o.state for o in report.outcomes}
        self.assertEqual(states, {"plain": "ok", "empty": "empty", "gone": "failed", "error": "failed"})

    async def test_redirects_are_followed(self):
        report = await self.scraper([self.src("/redirect")]).run()
        self.assertEqual(len(report.pool), 2)

    async def test_mirror_is_used_after_a_server_error(self):
        source = Source("http", (self.server.url("/error"), self.server.url("/plain2")), "github")
        report = await self.scraper([source]).run()
        self.assertEqual(sorted(report.pool._data), ["8.8.8.9:90"])
        self.assertEqual((self.server.hits["error"], self.server.hits["plain2"]), (1, 1))

    async def test_mirror_is_not_tried_when_the_file_is_gone(self):
        source = Source("http", (self.server.url("/gone"), self.server.url("/plain2")), "github")
        report = await self.scraper([source]).run()
        self.assertEqual(report.outcomes[0].state, "failed")
        self.assertEqual(self.server.hits["plain2"], 0)               # a mirror of a missing file is missing too

    async def test_unreachable_host_fails_fast(self):
        source = Source("http", ("http://127.0.0.1:1/never",))
        report = await self.scraper([source]).run()
        self.assertEqual(report.outcomes[0].state, "failed")

    async def test_slow_source_times_out_without_blocking_others(self):
        started = time.monotonic()
        report = await self.scraper([self.src("/slow"), self.src("/plain")], timeout=1.0).run()
        self.assertLess(time.monotonic() - started, 4)
        states = {o.key.rsplit("/", 1)[1]: o.state for o in report.outcomes}
        self.assertEqual(states, {"slow": "failed", "plain": "ok"})

    async def test_download_is_capped_but_partial_data_is_still_used(self):
        report = await self.scraper([self.src("/big")], max_bytes=20_000).run()
        self.assertTrue(0 < len(report.pool) < 5000)

    async def test_private_addresses_are_dropped_unless_allowed(self):
        class Private(SourceServer):
            async def plain(self, request):
                return web.Response(text="127.0.0.1:80\n10.0.0.1:80\n8.8.8.8:80\n")
        server = await Private().start()
        self.addAsyncCleanup(server.close)
        src = Source("http", (server.url("/plain"),))
        self.assertEqual(len((await self.scraper([src]).run()).pool), 1)
        self.assertEqual(len((await self.scraper([src], allow_private=True).run()).pool), 3)


class Paging(ScraperCase):
    async def test_paged_source_stops_at_the_first_page_without_proxies(self):
        source = self.src("/paged?page={page}", "http", pages=(1, 20))
        report = await self.scraper([source]).run()
        self.assertEqual(self.server.page_log, [1, 2, 3, 4])           # never asks for page 5..20
        self.assertEqual(len(report.pool), 5)
        self.assertEqual(report.outcomes[0].state, "ok")

    async def test_paged_source_stops_when_a_page_adds_nothing_new(self):
        report = await self.scraper([self.src("/repeat?page={page}", pages=(1, 50))]).run()
        self.assertEqual(self.server.page_log, [1, 2])
        self.assertEqual(len(report.pool), 2)

    async def test_offset_template(self):
        class Offsets(SourceServer):
            def __init__(self):
                super().__init__()
                self.app.router.add_get("/off", self.off)
                self.offsets = []

            async def off(self, request):
                offset = int(request.query["start"])
                self.offsets.append(offset)
                return web.Response(text=f"8.8.4.{offset // 64 + 1}:80" if offset < 128 else "x")
        server = await Offsets().start()
        self.addAsyncCleanup(server.close)
        src = Source("http", (server.url("/off?start={offset}"),), "web", (1, 10), 64)
        report = await self.scraper([src]).run()
        self.assertEqual(server.offsets, [0, 64, 128])
        self.assertEqual(len(report.pool), 2)


class Budget(ScraperCase):
    async def test_deadline_skips_unstarted_sources(self):
        sources = [self.src(f"/plain?x={i}") for i in range(10)]
        report = await self.scraper(sources, deadline=time.monotonic() - 1).run()
        self.assertEqual(report.count("skipped"), 10)
        self.assertTrue(report.stopped_by_budget)
        self.assertEqual(self.server.hits["plain"], 0)


class HealthTracking(ScraperCase):
    async def test_failures_back_off_and_recovery_resets(self):
        health = Health()
        key = "http://x/y"
        health.record(key, False); health.record(key, False)
        self.assertFalse(health.should_skip(key))                      # not yet: only 2 bad runs
        health.record(key, False)                                      # 3rd bad run -> skip 1 run
        self.assertTrue(health.should_skip(key))
        self.assertFalse(health.should_skip(key))
        health.record(key, False)                                      # 4th -> skip 2 runs
        self.assertEqual([health.should_skip(key) for _ in range(3)], [True, True, False])
        health.record(key, True)                                       # recovered: forgotten
        self.assertEqual(health.bad_count(), 0)

    async def test_skip_is_capped_and_prune_threshold(self):
        health = Health()
        for _ in range(40):
            health.record("k", False)
        self.assertEqual(health._bad["k"][1], Health.MAX_SKIP)
        self.assertEqual(health.prunable(), {"k"})
        health.forget({"k"})
        self.assertEqual(health.prunable(), set())

    async def test_persistence_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sub" / "health.json"
            health = Health(path)
            for _ in range(5):
                health.record("a", False)
            health.save()
            again = Health(path)
            self.assertEqual(again._bad, health._bad)
            self.assertEqual(again.runs, 1)
            path.write_text("{broken")
            self.assertEqual(Health(path).bad_count(), 0)              # corrupt cache is not fatal

    async def test_scraper_skips_backed_off_sources_and_records_results(self):
        health = Health()
        dead = self.src("/gone")
        for _ in range(3):
            health.record(dead.key, False)
        report = await self.scraper([dead, self.src("/plain")], health=health).run()
        states = {o.key.rsplit("/", 1)[1]: o.state for o in report.outcomes}
        self.assertEqual(states, {"gone": "skipped", "plain": "ok"})
        self.assertEqual(self.server.hits["gone"], 0)                  # not even requested
        report = await self.scraper([self.src("/error"), self.src("/plain")], health=health).run()
        self.assertEqual(health._bad[self.server.url("/error")][0], 1)
        self.assertNotIn(self.server.url("/plain"), health._bad)

    async def test_network_outage_does_not_poison_health(self):
        health = Health()
        sources = [Source("http", (f"http://127.0.0.1:1/dead{i}",)) for i in range(60)]
        report = await self.scraper(sources, health=health, concurrency=60).run()
        self.assertTrue(report.outage)
        self.assertEqual(health.bad_count(), 0)


class PoolRanking(unittest.TestCase):
    def test_ranking_prefers_previous_then_multi_listed_and_sets_masks(self):
        pool = Pool()
        pool.add_many({"1.1.1.1:80": HTTP, "2.2.2.2:80": 0})
        pool.add_many({"1.1.1.1:80": HTTP, "3.3.3.3:80": SOCKS5})
        pool.add_many({"1.1.1.1:80": 0})
        pool.add_known("9.9.9.9:80")
        order = pool.ranked({"9.9.9.9:80": SOCKS5}, seed=1)
        names = [p for p, _, _ in order]
        self.assertEqual(names[0], "9.9.9.9:80")                      # alive last time
        self.assertEqual(names[1], "1.1.1.1:80")                      # listed by 3 sources
        by_name = {p: (t, f) for p, t, f in order}
        self.assertEqual(by_name["9.9.9.9:80"], (SOCKS5, ALL_MASK & ~SOCKS5))
        self.assertEqual(by_name["1.1.1.1:80"], (HTTP, ALL_MASK & ~HTTP))   # explicit beats "unknown"
        self.assertEqual(by_name["2.2.2.2:80"], (ALL_MASK, 0))              # nobody knows: try all
        self.assertEqual(by_name["3.3.3.3:80"], (SOCKS5, ALL_MASK & ~SOCKS5))

    def test_limit_keeps_the_best(self):
        pool = Pool()
        pool.add_many({f"8.8.8.{i}:80": 0 for i in range(1, 50)})
        pool.add_many({"8.8.8.7:80": 0})
        self.assertEqual(pool.ranked(limit=1)[0][0], "8.8.8.7:80")

    def test_dump_and_load(self):
        pool = Pool()
        pool.add_many({"8.8.8.8:80": SOCKS5, "8.8.4.4:80": 0})
        with tempfile.TemporaryDirectory() as tmp:
            pool.dump(Path(tmp) / "c" / "pool.json")
            loaded = Pool.load(Path(tmp) / "c" / "pool.json")
            self.assertEqual(loaded._data, pool._data)
            self.assertEqual(len(Pool.load(Path(tmp) / "missing.json")), 0)

    def test_previous_masks(self):
        records = [{"proxy": "1.1.1.1:80", "protocol": "http"}, {"proxy": "1.1.1.1:80", "protocol": "socks5"},
                   {"proxy": "bad", "protocol": "http"}, {"proxy": "2.2.2.2:1", "protocol": "weird"}, {}]
        self.assertEqual(previous_masks(records), {"1.1.1.1:80": HTTP | SOCKS5})


if __name__ == "__main__":
    unittest.main()

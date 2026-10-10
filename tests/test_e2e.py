"""End-to-end: run the real ``main.py`` against a fake internet and compare the published
files with the known ground truth (which proxies are good, and for which protocols)."""
import asyncio
import io
import json
import os
import sys
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

import support
from aiohttp import web
from common import utc_stamp

from mocknet import JudgeServer, MockProxy, make_pki

MAIN = support.ROOT / "main.py"
PKI = make_pki(with_ca_pem=True)


class FakeInternet:
    """Judges, a population of proxies with known behaviour, and websites listing them."""

    async def start(self, tmp: Path):
        server_ctx, _, rogue_ctx, ca_pem = PKI
        (tmp / "ca.pem").write_bytes(ca_pem)
        self.http_judge, self.tls_judge = JudgeServer(), JudgeServer(ssl_ctx=server_ctx)
        await self.http_judge.start()
        await self.tls_judge.start()
        self.mocks: list[MockProxy] = []

        async def make(kind, behavior="ok", **kw):
            mock = MockProxy(kind, behavior, rogue_ctx=rogue_ctx, **kw)
            await mock.start()
            self.mocks.append(mock)
            return mock

        # --- good proxies (ground truth) -------------------------------------------------
        self.good = {"http": [], "https": [], "socks4": [], "socks5": []}
        for mock in (await make("http"), await make("http")):
            self.good["http"].append(mock); self.good["https"].append(mock)
        connect_only = await make("http", "connect_only")           # alive, but only for HTTPS
        self.good["https"].append(connect_only)
        plain_only = await make("http", "no_connect")               # alive, but only plain HTTP
        self.good["http"].append(plain_only)
        mitm = await make("http", "mitm")                           # plain HTTP fine; its TLS certificate is
        self.good["http"].append(mitm)                              # untrusted -> must NOT be listed as https
        self.mitm_address = mitm.address
        s5a, s5b = await make("socks5"), await make("socks5")
        self.good["socks5"] += [s5a, s5b]
        self.mislabeled = await make("socks4")                       # a SOCKS4 proxy listed as "http"
        self.good["socks4"].append(self.mislabeled)
        multi = await make("multi")                                  # one port, three protocols
        self.good["http"].append(multi); self.good["https"].append(multi)
        self.good["socks5"].append(multi); self.good["socks4"].append(multi)
        self.multi = multi
        # --- bad proxies -----------------------------------------------------------------
        self.bad = [await make("http", "transparent"), await make("socks5", "transparent"),
                    await make("http", "garbage"), await make("http", "blackhole"),
                    await make("socks5", "auth"), await make("http", "auth"), await make("http", "html"),
                    await make("socks4", "refuse")]
        dead = await make("http")
        self.dead_address = dead.address
        await dead.close()
        self.mocks.remove(dead)
        self.bad_addresses = [m.address for m in self.bad] + [self.dead_address]

        # --- websites serving lists ------------------------------------------------------
        a = lambda ms: "\n".join(m.address if hasattr(m, "address") else m for m in ms)
        http_list = a([m for m in self.mocks if m not in (s5a, s5b, multi)] + [self.dead_address]) + \
                    "\n# comment\n" + self.good["http"][0].address + "\n999.9.9.9:80\ngarbage\n"
        socks5_list = a([s5a, s5b] + [m for m in self.bad if m.kind == "socks5"])
        # the multi-protocol port is listed WITHOUT a protocol column (-> all three get tested);
        # the garbage proxy claims HTTP
        table = ("<table>"
                 f"<tr><td>127.0.0.1</td><td>{multi.port}</td><td>elite</td></tr>"
                 f"<tr><td>127.0.0.1</td><td>{self.bad[2].port}</td><td>HTTP</td></tr></table>")
        pages = {1: a(self.good["http"][:1]), 2: a([connect_only]), 3: "no more"}
        self.pages = pages                     # tests may add proxies to a later pass
        app = web.Application()

        def serve(body=None, content_type="text/plain", pick=None):
            async def handler(request):
                return web.Response(text=pick(request) if pick else body, content_type=content_type)
            return handler

        app.router.add_get("/http.txt", serve(http_list))
        app.router.add_get("/socks5.txt", serve(socks5_list))
        app.router.add_get("/table.html", serve(table, "text/html"))
        app.router.add_get("/api", serve(pick=lambda r: pages.get(int(r.query["page"]), "")))
        self.runner = web.AppRunner(app)
        await self.runner.setup()
        site = web.TCPSite(self.runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        base = f"http://127.0.0.1:{port}"
        self.base = base
        sources = tmp / "sources"
        sources.mkdir()
        (sources / "github.txt").write_text("")
        (sources / "websites.txt").write_text(
            f"http {base}/http.txt\nsocks5 {base}/socks5.txt\nmixed {base}/table.html\n"
            f"mixed {base}/api?page={{page}} pages=1..9\nhttp {base}/missing.txt\n")
        return self

    async def close(self):
        for mock in self.mocks:
            await mock.close()
        await self.http_judge.close()
        await self.tls_judge.close()
        await self.runner.cleanup()


async def run_main(tmp: Path, net: FakeInternet, *extra: str, results: str = "results"):
    env = dict(os.environ, SSL_CERT_FILE=str(tmp / "ca.pem"))
    env.pop("GITHUB_TOKEN", None); env.pop("GH_TOKEN", None)
    process = await asyncio.create_subprocess_exec(
        sys.executable, str(MAIN), "--sources", str(tmp / "sources"), "--output", str(tmp / results),
        "--cache", str(tmp / "cache"), "--readme", str(tmp / "README.md"), "--discover", "off",
        "--judge", f"http://127.0.0.1:{net.http_judge.port}/ip",
        "--judge", f"https://127.0.0.1:{net.tls_judge.port}/ip",
        "--allow-private", "--no-controls", "--timeout", "3", "--connect-timeout", "1",
        "--scrape-concurrency", "10", *extra,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT, env=env, cwd=str(tmp))
    out, _ = await asyncio.wait_for(process.communicate(), 120)
    return process.returncode, out.decode()


def read_lines(path: Path) -> list[str]:
    return [line for line in path.read_text().splitlines() if line]


class EndToEnd(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        (self.dir / "README.md").write_text(
            "# demo\n<!-- stats:start -->\nold\n<!-- stats:end -->\n<!-- footer:start -->\nold\n<!-- footer:end -->\n")
        self.net = await FakeInternet().start(self.dir)
        self.addAsyncCleanup(self.net.close)

    def expected(self, key):
        return sorted({m.address for m in self.net.good[key]})

    async def test_full_pipeline_publishes_exactly_the_good_proxies(self):
        code, output = await run_main(self.dir, self.net, "--rounds", "2")
        self.assertEqual(code, 0, output)
        txt = self.dir / "results" / "txt"
        # --- the six lists, every format ----------------------------------------------------
        self.assertEqual(sorted(read_lines(txt / "http.txt")), self.expected("http"), output)
        self.assertEqual(sorted(read_lines(txt / "https.txt")), self.expected("https"), output)
        self.assertNotIn(self.net.mitm_address, (txt / "https.txt").read_text())      # TLS MITM never listed as https
        self.assertEqual(sorted(read_lines(txt / "socks5.txt")), self.expected("socks5"), output)
        self.assertEqual(sorted(read_lines(txt / "socks4.txt")), self.expected("socks4"), output)  # incl. the mislabeled one
        want_socks = sorted([f"socks5://{a}" for a in self.expected("socks5")] +
                            [f"socks4://{a}" for a in self.expected("socks4")])
        self.assertEqual(sorted(read_lines(txt / "socks.txt")), want_socks)
        want_all = sorted([f"http://{a}" for a in {m.address for m in self.net.good["http"] + self.net.good["https"]}]
                          + want_socks)
        self.assertEqual(sorted(read_lines(txt / "all.txt")), want_all)
        for address in self.net.bad_addresses:                     # nothing bad slipped through
            self.assertNotIn(address, (txt / "all.txt").read_text(), address)
        # --- json -----------------------------------------------------------------------------
        records = json.loads((self.dir / "results" / "json" / "all.json").read_text())
        self.assertEqual(len(records), len(want_all))
        for record in records:
            self.assertEqual((record["checks_passed"], record["checks_total"]), (3, 3))
            self.assertTrue(record["exit_ip"].startswith("127.0."))
        by_key = {(r["proxy"], r["protocol"]): r for r in records}
        self.assertTrue(by_key[(self.net.good["https"][2].address, "http")]["https"])  # connect-only: TLS yes
        self.assertFalse(by_key[(self.net.good["https"][2].address, "http")]["http"])  # ...plain HTTP no
        stats = json.loads((self.dir / "results" / "json" / "stats.json").read_text())
        self.assertEqual(stats["live"], len(want_all))
        self.assertEqual(stats["sources"], 5)
        self.assertEqual(stats["sources_ok"], 4)
        self.assertEqual(stats["sources_failed"], 1)               # /missing.txt
        self.assertGreaterEqual(stats["candidates"], len(self.net.mocks))
        self.assertLess(stats["tcp_alive"], stats["candidates"])    # the dead port was pre-filtered
        # --- pdf ------------------------------------------------------------------------------
        from pypdf import PdfReader
        text = "\n".join(p.extract_text() for p in
                         PdfReader(io.BytesIO((self.dir / "results" / "pdf" / "all.pdf").read_bytes())).pages)
        for address in self.expected("socks5"):
            self.assertIn(address, text)
        self.assertEqual(sorted(p.name for p in (self.dir / "results").iterdir()), ["json", "pdf", "txt"])
        # --- README and caches --------------------------------------------------------------------
        readme = (self.dir / "README.md").read_text()
        self.assertIn(f"| Live proxies | **{len(want_all)}** |", readme)
        self.assertIn("Script by Gametturxux.", readme)
        self.assertTrue((self.dir / "cache" / "source_health.json").exists())
        self.assertTrue((self.dir / "cache" / "candidates.json").exists())

    async def test_second_run_rechecks_previous_results_first_and_keeps_working(self):
        code, output = await run_main(self.dir, self.net, "--rounds", "1")
        self.assertEqual(code, 0, output)
        first = sorted(read_lines(self.dir / "results" / "txt" / "all.txt"))
        # a source disappears completely: previous proxies must still be re-verified and kept
        (self.dir / "sources" / "websites.txt").write_text("")
        (self.dir / "sources" / "github.txt").write_text(
            "mixed owner/repo/main/never-reachable.txt\n")      # unreachable mirror host: all fail
        code, output = await run_main(self.dir, self.net, "--rounds", "1", "--scrape-budget", "0.05")
        self.assertIn("re-checked first", output)
        self.assertEqual(code, 0, output)
        self.assertEqual(sorted(read_lines(self.dir / "results" / "txt" / "all.txt")), first)

    async def test_a_proxy_that_dies_between_runs_disappears(self):
        code, output = await run_main(self.dir, self.net, "--rounds", "1")
        self.assertEqual(code, 0, output)
        victim = self.net.good["socks5"][0]
        self.assertIn(victim.address, (self.dir / "results" / "txt" / "socks5.txt").read_text())
        await victim.close()
        code, output = await run_main(self.dir, self.net, "--rounds", "1")
        self.assertEqual(code, 0, output)
        self.assertNotIn(victim.address, (self.dir / "results" / "txt" / "socks5.txt").read_text())
        self.assertIn(self.net.good["socks5"][1].address, (self.dir / "results" / "txt" / "socks5.txt").read_text())

    async def test_nothing_verified_never_overwrites_previous_results(self):
        code, output = await run_main(self.dir, self.net, "--rounds", "0")
        self.assertEqual(code, 0, output)
        before = (self.dir / "results" / "txt" / "all.txt").read_text()
        self.assertTrue(before)
        for mock in self.net.mocks:
            await mock.close()                                     # the whole internet goes down
        code, output = await run_main(self.dir, self.net, "--rounds", "0")
        self.assertEqual(code, 3, output)
        self.assertIn("leaving the previous results untouched", output)
        self.assertEqual((self.dir / "results" / "txt" / "all.txt").read_text(), before)

    async def test_formats_and_candidate_cap(self):
        code, output = await run_main(self.dir, self.net, "--rounds", "0", "--formats", "txt,json",
                                      "--max-candidates", "5", results="out")
        self.assertEqual(code, 0, output)
        self.assertEqual(sorted(p.name for p in (self.dir / "out").iterdir()), ["json", "txt"])
        self.assertIn("testing the best 5 of", output)
        code, output = await run_main(self.dir, self.net, "--formats", "txt,bogus", results="out2")
        self.assertEqual(code, 2)

    async def test_https_probe_can_be_disabled(self):
        code, output = await run_main(self.dir, self.net, "--rounds", "0", "--no-https-check")
        self.assertEqual(code, 0, output)
        self.assertEqual(read_lines(self.dir / "results" / "txt" / "https.txt"), [])
        self.assertTrue(read_lines(self.dir / "results" / "txt" / "http.txt"))

    # ------------------------------------------------------------------ scrape only

    async def test_scrape_only_publishes_everything_without_validating(self):
        raw = self.dir / "raw"
        code, output = await run_main(self.dir, self.net, "--scrape-only", "--raw-dir", str(raw))
        self.assertEqual(code, 0, output)
        self.assertIn("Publishing raw (unvalidated) proxies", output)
        self.assertNotIn("Detecting environment", output)          # no judges, no probes at all
        self.assertFalse((self.dir / "results" / "txt").exists())   # nothing validated was published
        text = (raw / "txt" / "all.txt").read_text()
        for address in self.net.bad_addresses:                     # the rejects are in there too
            self.assertIn(address, text, address)
        stats = json.loads((raw / "json" / "stats.json").read_text())
        self.assertFalse(stats["validated"])
        self.assertEqual(stats["raw_published"], len(json.loads((raw / "json" / "all.json").read_text())))
        self.assertEqual(sorted(p.name for p in raw.iterdir()), ["json", "txt"])

    async def test_raw_passes_accumulate_and_overwrite_instead_of_duplicating(self):
        raw = self.dir / "raw"
        code, output = await run_main(self.dir, self.net, "--scrape-only", "--raw-dir", str(raw))
        self.assertEqual(code, 0, output)
        first = read_lines(raw / "txt" / "all.txt")
        self.assertIn("0 expired", output)
        code, output = await run_main(self.dir, self.net, "--scrape-only", "--raw-dir", str(raw))
        self.assertEqual(code, 0, output)
        self.assertIn("+0 new", output)                            # everything was already there
        self.assertIn("overwritten", output)
        self.assertEqual(read_lines(raw / "txt" / "all.txt"), first)   # stable order, no duplicates
        records = json.loads((raw / "json" / "all.json").read_text())
        self.assertTrue(records)
        self.assertTrue(all(r["hits"] >= 2 for r in records), [r for r in records if r["hits"] < 2][:3])

    async def test_a_new_source_adds_to_the_pool_and_the_old_entries_stay(self):
        raw = self.dir / "raw"
        code, _ = await run_main(self.dir, self.net, "--scrape-only", "--raw-dir", str(raw))
        self.assertEqual(code, 0)
        before = set(read_lines(raw / "txt" / "all.txt"))
        self.net.pages[3] = "7.7.7.7:80\n8.8.8.8:1080"                # a page nobody delivered before
        code, output = await run_main(self.dir, self.net, "--scrape-only", "--raw-dir", str(raw))
        self.assertEqual(code, 0, output)
        after = set(read_lines(raw / "txt" / "all.txt"))
        self.assertTrue(before <= after, sorted(before - after)[:3])   # nothing was dropped
        self.assertIn("7.7.7.7:80", after)                             # the /api hint is "mixed", so
        self.assertIn("8.8.8.8:1080", after)                           # the lines carry no scheme

    async def test_raw_entries_expire_when_no_source_lists_them_any_more(self):
        cache = self.dir / "cache"
        cache.mkdir(exist_ok=True)
        ancient = int(time.time()) - 7 * 3600
        (cache / "raw_pool.json").write_text(json.dumps({"7.7.7.7:80": [1, 1, 5, ancient, ancient]}))
        raw = self.dir / "raw"
        code, output = await run_main(self.dir, self.net, "--scrape-only", "--raw-dir", str(raw),
                                      "--raw-ttl-hours", "6")
        self.assertEqual(code, 0, output)
        self.assertIn("-1 expired", output)
        self.assertNotIn("7.7.7.7:80", (raw / "txt" / "all.txt").read_text())

    async def test_the_raw_pool_can_be_capped(self):
        raw = self.dir / "raw"
        code, output = await run_main(self.dir, self.net, "--scrape-only", "--raw-dir", str(raw),
                                      "--raw-pool-limit", "3", "--raw-limit", "0")
        self.assertEqual(code, 0, output)
        self.assertIn("over the cap", output)
        self.assertEqual(len(json.loads((raw / "json" / "all.json").read_text())), 3)

    async def test_scrape_only_without_accumulation_starts_from_zero(self):
        raw = self.dir / "raw"
        await run_main(self.dir, self.net, "--scrape-only", "--raw-dir", str(raw))
        code, output = await run_main(self.dir, self.net, "--scrape-only", "--raw-dir", str(raw),
                                      "--no-accumulate")
        self.assertEqual(code, 0, output)
        records = json.loads((raw / "json" / "all.json").read_text())
        self.assertTrue(all(r["hits"] == 1 for r in records))       # the previous pass was forgotten

    async def test_nothing_scraped_leaves_the_previous_raw_lists_alone(self):
        raw = self.dir / "raw"
        code, _ = await run_main(self.dir, self.net, "--scrape-only", "--raw-dir", str(raw))
        self.assertEqual(code, 0)
        before = (raw / "txt" / "all.txt").read_text()
        (self.dir / "sources" / "websites.txt").write_text("")       # every source is gone
        code, output = await run_main(self.dir, self.net, "--scrape-only", "--raw-dir", str(raw),
                                      "--raw-ttl-hours", "0")
        self.assertEqual(code, 0, output)                            # the pool still holds the old pass
        self.assertEqual((raw / "txt" / "all.txt").read_text(), before)
        code, output = await run_main(self.dir, self.net, "--scrape-only", "--raw-dir", str(raw),
                                      "--no-accumulate")
        self.assertEqual(code, 3, output)                            # nothing to publish at all
        self.assertIn("leaving the previous raw lists untouched", output)
        self.assertEqual((raw / "txt" / "all.txt").read_text(), before)

    async def test_unusable_raw_formats_are_rejected(self):
        code, output = await run_main(self.dir, self.net, "--scrape-only",
                                      "--raw-dir", str(self.dir / "raw"), "--raw-formats", "txt,pdf")
        self.assertEqual(code, 2, output)
        self.assertIn("--raw-formats must be a non-empty subset", output)
        self.assertFalse((self.dir / "raw").exists())                 # nothing was written

    # ------------------------------------------------------- continuous validated list

    async def test_keep_published_carries_a_proxy_this_pass_did_not_verify(self):
        code, output = await run_main(self.dir, self.net, "--rounds", "0")
        self.assertEqual(code, 0, output)
        first = sorted(read_lines(self.dir / "results" / "txt" / "all.txt"))
        victim = self.net.good["socks5"][0]
        await victim.close()                                          # it dies between passes
        code, output = await run_main(self.dir, self.net, "--rounds", "0", "--keep-published", "30")
        self.assertEqual(code, 0, output)
        self.assertIn("carried over from earlier passes", output)
        self.assertEqual(sorted(read_lines(self.dir / "results" / "txt" / "all.txt")), first)
        records = json.loads((self.dir / "results" / "json" / "all.json").read_text())
        carried = {r["proxy"] for r in records if r.get("carried_over")}
        self.assertIn(victim.address, carried)
        self.assertTrue(all(r["checked_at"] for r in records))         # every record is stamped
        stats = json.loads((self.dir / "results" / "json" / "stats.json").read_text())
        self.assertEqual(stats["carried_over"], len(carried))
        self.assertEqual(stats["live"], len(records))

    async def test_the_interval_loop_starts_a_new_pass_on_its_own(self):
        """``--interval 5`` is what the 5-minute schedule does; here it is 1.2 seconds."""
        raw = self.dir / "raw"
        env = dict(os.environ)
        env.pop("GITHUB_TOKEN", None); env.pop("GH_TOKEN", None)
        process = await asyncio.create_subprocess_exec(
            sys.executable, str(MAIN), "--sources", str(self.dir / "sources"),
            "--output", str(self.dir / "results"), "--cache", str(self.dir / "cache"),
            "--readme", "", "--discover", "off", "--allow-private", "--scrape-concurrency", "10",
            "--scrape-only", "--raw-dir", str(raw), "--interval", "0.02",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT, env=env, cwd=str(self.dir))
        await asyncio.sleep(6)
        process.terminate()
        out, _ = await asyncio.wait_for(process.communicate(), 30)
        text = out.decode()
        self.assertIn("loop: a new pass starts every 0.02 minutes", text)
        self.assertGreaterEqual(text.count("finished in"), 2, text[-800:])
        self.assertIn("pass 2", text)
        self.assertTrue(read_lines(raw / "txt" / "all.txt"))       # every pass published again

    async def test_carrying_over_stops_at_the_end_of_the_window(self):
        code, _ = await run_main(self.dir, self.net, "--rounds", "0")
        self.assertEqual(code, 0)
        published = self.dir / "results" / "json" / "all.json"
        records = json.loads(published.read_text())
        for record in records:                                        # pretend they are two hours old
            record["checked_at"] = utc_stamp(datetime.now(timezone.utc) - timedelta(hours=2))
        published.write_text(json.dumps(records))
        for mock in self.net.mocks:
            await mock.close()                                        # nothing verifies any more
        code, output = await run_main(self.dir, self.net, "--rounds", "0", "--keep-published", "30",
                                      "--allow-empty")
        self.assertEqual(code, 0, output)
        self.assertEqual(read_lines(self.dir / "results" / "txt" / "all.txt"), [])


if __name__ == "__main__":
    unittest.main()

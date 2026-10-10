import asyncio
import os
import tempfile
import time
import unittest
from datetime import date
from pathlib import Path

import support  # noqa: F401
from aiohttp import web

import discover
from discover import Registry, hint_for, looks_random, repo_score, score_file, select_files
from sources import (MAX_DISCOVERED, Source, github_urls, load_sources, parse_any_line,
                     parse_github_line, parse_web_line)


def blob(path, size=5000):
    return {"type": "blob", "path": path, "size": size}


class FileSelection(unittest.TestCase):
    def picks(self, paths):
        return select_files([blob(p) if isinstance(p, str) else blob(*p) for p in paths])

    def test_protocol_files_and_all_are_picked_with_hints(self):
        got = self.picks(["README.md", "http.txt", "socks4.txt", "socks5.txt", "all.txt", "LICENSE", ".gitignore"])
        self.assertEqual(dict((p, h) for h, p in got),
                         {"http.txt": "http", "socks4.txt": "socks4", "socks5.txt": "socks5", "all.txt": "mixed"})

    def test_txt_wins_over_csv_and_json_of_the_same_name(self):
        got = self.picks(["proxies/all/data.json", "proxies/all/data.csv", "proxies/all/data.txt"])
        self.assertEqual([p for _, p in got], ["proxies/all/data.txt"])

    def test_json_is_kept_when_there_is_no_txt(self):
        got = self.picks(["proxies.json"])
        self.assertEqual([p for _, p in got], ["proxies.json"])

    def test_json_next_to_several_txt_lists_is_redundant(self):
        got = self.picks(["proxies.json", "http.txt", "socks5.txt", "all.json"])
        self.assertEqual(sorted(p for _, p in got), ["http.txt", "socks5.txt"])

    def test_per_country_and_archive_slices_are_ignored(self):
        got = self.picks(["all.txt", "countries/US.txt", "by-country/DE/http.txt", "archive/2026-10-01/http.txt",
                          "proxies/countries/nl/data.txt", "history/socks5.txt"])
        self.assertEqual([p for _, p in got], ["all.txt"])

    def test_badges_tests_and_vendor_dirs_are_ignored(self):
        got = self.picks(["proxies/badges/http.json", "node_modules/x/proxy.txt", "tests/proxies.txt",
                          "docs/http.txt", ".github/proxies.txt", "src/socks5.txt"])
        self.assertEqual(got, [])

    def test_non_list_files_are_ignored(self):
        got = self.picks(["requirements.txt", "user-agents.txt", "passwords.txt", "domains.txt", "notes.txt",
                          "package.json", "stats.json", "config.json", "cookies.txt", "wordlist.txt", "robots.txt"])
        self.assertEqual(got, [])

    def test_size_window(self):
        self.assertEqual(self.picks([("http.txt", 10)]), [])                      # too small
        self.assertEqual(self.picks([("http.txt", 200 * 1024 * 1024)]), [])      # too big
        self.assertEqual(len(self.picks([("http.txt", 100)])), 1)

    def test_directories_and_non_blobs_are_skipped(self):
        got = select_files([{"type": "tree", "path": "proxies", "size": 0}, blob("http.txt")])
        self.assertEqual([p for _, p in got], ["http.txt"])

    def test_cap_per_repo(self):
        got = select_files([blob(f"proxy_list_{i}.txt") for i in range(40)], per_repo=7)
        self.assertEqual(len(got), 7)

    def test_nested_protocol_layout(self):
        got = self.picks(["proxies/protocols/http/data.txt", "proxies/protocols/socks5/data.txt",
                          "proxies/all/data.txt", "proxies/protocols/http/data.json"])
        self.assertEqual(sorted(got), [("http", "proxies/protocols/http/data.txt"),
                                       ("mixed", "proxies/all/data.txt"),
                                       ("socks5", "proxies/protocols/socks5/data.txt")])

    def test_hints(self):
        for path, hint in (("socks5/data.txt", "socks5"), ("SOCKS4_proxies.txt", "socks4"),
                           ("socks.txt", "socks"), ("https.txt", "http"), ("http_all.txt", "http"),
                           ("all_ssl.txt", "http"),        # "SSL proxies" are HTTP proxies with CONNECT
                           ("proxies.txt", "mixed"), ("All_proxies.txt", "mixed")):
            self.assertEqual(hint_for(path), hint, path)

    def test_score_is_none_for_foreign_extensions(self):
        self.assertIsNone(score_file("proxy.exe", 5000))
        self.assertIsNone(score_file("image.png", 5000))


class RepoChoice(unittest.TestCase):
    def test_scores(self):
        good = {"name": "free-proxy-list", "description": "Free proxy list updated every hour",
                "topics": ["proxy-list", "socks5"]}
        software = {"name": "proxy-server", "description": "A fast reverse proxy server", "topics": ["proxy"]}
        spam = {"name": "hypeproxies-isp-proxies", "description": "Buy premium residential proxies cheap",
                "topics": ["proxies"]}
        self.assertGreater(repo_score(good), repo_score(software))
        self.assertGreater(repo_score(software), repo_score(spam))
        self.assertLessEqual(repo_score(spam), -2)
        self.assertEqual(repo_score({"name": "foo.github.io", "description": "proxy list"}), -99)
        self.assertEqual(repo_score({"name": "LxDOwCAQ", "description": "proxy list"}), -99)

    def test_random_names(self):
        for name in ("LxDOwCAQ", "GvFpwhaS", "mfStEhxc"):
            self.assertTrue(looks_random(name), name)
        for name in ("ProxyHub", "ProxyScraper", "free-proxy-list", "proxylist", "KangProxy", "socks5_list"):
            self.assertFalse(looks_random(name), name)


class SourceFiles(unittest.TestCase):
    def test_github_line(self):
        s = parse_github_line("socks5 owner/repo/main/dir/socks5.txt  # comment")
        self.assertEqual(s.hint, "socks5")
        self.assertEqual(s.urls[0], "https://raw.githubusercontent.com/owner/repo/main/dir/socks5.txt")
        self.assertEqual(s.urls[1], "https://cdn.jsdelivr.net/gh/owner/repo@main/dir/socks5.txt")
        self.assertEqual(s.urls[2], "https://cdn.statically.io/gh/owner/repo/main/dir/socks5.txt")
        for bad in ("", "# only comment", "http", "weird owner/repo/main/x.txt", "http owner/repo/x.txt",
                    "http a b c"):
            self.assertIsNone(parse_github_line(bad), bad)

    def test_web_line(self):
        s = parse_web_line("mixed https://x.test/list?page={page}  pages=1..12  step=64")
        self.assertEqual((s.pages, s.step, s.paged, s.kind), ((1, 12), 64, True, "web"))
        self.assertEqual(parse_web_line("socks5 https://x.test/s5.txt").pages, None)
        for bad in ("http ftp://x.test/a", "http https://x.test/{page}", "http https://x/y pages=5..1",
                    "nonsense https://x/y", "http"):
            self.assertIsNone(parse_web_line(bad), bad)

    def test_any_line_accepts_both_formats(self):
        """indonesia.txt mixes domestic websites with GitHub-hosted lists."""
        web = parse_any_line("mixed https://x.test/proxy?id  pages=1..3")
        self.assertEqual((web.kind, web.paged), ("web", True))
        hub = parse_any_line("socks5 owner/repo/main/socks5.txt")
        self.assertEqual(hub.kind, "github")
        self.assertEqual(hub.urls[0], "https://raw.githubusercontent.com/owner/repo/main/socks5.txt")
        self.assertEqual(len(hub.urls), 3)                     # mirrors are added
        for bad in ("", "# comment", "mixed", "nonsense https://x/y", "weird a/b/c"):
            self.assertIsNone(parse_any_line(bad), bad)

    def test_load_sources_precedence_and_counts(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "github.txt").write_text("http a/b/main/http.txt\nbroken line\n# c\nsocks5 a/b/main/s5.txt\n")
            (root / "websites.txt").write_text("mixed https://x.test/list\nmixed https://x.test/list\n")
            (root / "indonesia.txt").write_text("mixed https://id.test/proxy\nmixed id/repo/main/list.txt\n")
            (root / "discovered.txt").write_text("# @ a/b 2026-10-01\nhttp a/b/main/http.txt\nhttp c/d/main/http.txt\n"
                                                 "# @ c/d 2026-10-01\nsocks4 c/d/main/s4.txt\n")
            sources, counts = load_sources(root)
            self.assertEqual(counts, {"github": 2, "websites": 1, "indonesia": 2, "discovered": 2})
            self.assertEqual(len(sources), 7)                  # duplicates collapse
            self.assertEqual(counts["github"], 2)              # a/b/main/http.txt beats discovered.txt
            _, counts = load_sources(root, include_discovered=False)
            self.assertEqual(counts["discovered"], 0)
            _, counts = load_sources(root, limit_discovered=1)
            self.assertEqual(counts["discovered"], 1)

    def test_missing_directory_is_empty_not_an_error(self):
        self.assertEqual(load_sources("/nonexistent/dir"),
                         ([], {"github": 0, "websites": 0, "indonesia": 0, "discovered": 0}))

    def test_shipped_source_files_are_clean(self):
        """Every non-comment line in the real data files must parse: no silent typos."""
        root = support.ROOT / "sources"
        for name, parser in (("github.txt", parse_github_line), ("websites.txt", parse_web_line),
                             ("indonesia.txt", parse_any_line)):
            path = root / name
            self.assertTrue(path.exists(), name)
            bad = [line for line in path.read_text().splitlines()
                   if line.strip() and not line.lstrip().startswith("#") and parser(line) is None]
            self.assertEqual(bad, [], f"{name}: unparsable lines")
        sources, counts = load_sources(root)
        keys = [s.key for s in sources]
        self.assertEqual(len(keys), len(set(keys)))
        self.assertLessEqual(counts["discovered"], MAX_DISCOVERED)
        self.assertGreater(counts["github"], 50)
        self.assertGreater(counts["websites"], 100)
        self.assertGreater(counts["indonesia"], 10)            # domestic coverage must stay real


class RegistryFile(unittest.TestCase):
    def test_round_trip_prune_and_markers(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "r" / "discovered.txt"
            registry = Registry()
            registry.add("a/b", "2026-10-01", [("http", "a/b/main/http.txt"), ("socks5", "a/b/main/s5.txt")])
            registry.add("c/d", "2026-10-02", [])                       # scanned, nothing useful
            registry.save(path)
            loaded = Registry.load(path)
            self.assertEqual(loaded.repos, registry.repos)
            self.assertEqual(loaded.files, registry.files)
            self.assertEqual(loaded.total(), 2)
            dead = {github_urls("a/b/main/http.txt")[0]}
            self.assertEqual(discover.prune_registry(path, dead), 1)
            after = Registry.load(path)
            self.assertEqual(after.files["a/b"], [("socks5", "a/b/main/s5.txt")])
            self.assertIn("c/d", after.repos)                           # marker survives: never re-scanned
            self.assertEqual(discover.prune_registry(path, dead), 0)

    def test_load_ignores_junk_and_orphans(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "r.txt"
            path.write_text("http x/y/main/orphan.txt\n# @ a/b 2026-10-01\nhttp a/b/main/ok.txt\nhttp other/repo/main/x.txt\n\njunk\n")
            registry = Registry.load(path)
            self.assertEqual(registry.files, {"a/b": [("http", "a/b/main/ok.txt")]})

    def test_windows_tile_the_period(self):
        windows = discover._windows(21, 7, date(2026, 10, 7))
        self.assertEqual(windows, [("2026-10-01", "2026-10-07"), ("2026-09-24", "2026-09-30"),
                                   ("2026-09-17", "2026-09-23")])
        self.assertEqual(discover._windows(5, 7, date(2026, 10, 7)), [("2026-10-02", "2026-10-07")])


# ------------------------------------------------------------ mock GitHub API


class MockGitHub:
    def __init__(self):
        self.repos: dict[str, list[dict]] = {}       # query keyword -> repos
        self.trees: dict[str, object] = {}           # full_name -> tree | int status
        self.search_log: list[str] = []
        self.tree_log: list[str] = []
        self.fail_after: int | None = None            # start rate limiting after N calls
        self.retry_after: str | None = None
        self.calls = 0
        app = web.Application()
        app.router.add_get("/search/repositories", self.search)
        app.router.add_get("/repos/{owner}/{repo}/git/trees/{branch}", self.tree)
        self.app = app

    async def start(self):
        self.runner = web.AppRunner(self.app)
        await self.runner.setup()
        site = web.TCPSite(self.runner, "127.0.0.1", 0)
        await site.start()
        self.base = f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}"
        return self

    async def close(self):
        await self.runner.cleanup()

    def limited(self):
        self.calls += 1
        if self.fail_after is not None and self.calls > self.fail_after:
            headers = {"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": str(int(time.time()) + 3600)}
            if self.retry_after:
                headers["Retry-After"] = self.retry_after
            return web.json_response({"message": "rate limit"}, status=403, headers=headers)
        return None

    async def search(self, request):
        if (resp := self.limited()) is not None:
            return resp
        q = request.query["q"]
        self.search_log.append(q)
        page = int(request.query.get("page", 1))
        items = []
        for keyword, repos in self.repos.items():
            if keyword in q:
                items = repos
        chunk = items[(page - 1) * 100: page * 100]
        return web.json_response({"total_count": len(items), "items": chunk})

    async def tree(self, request):
        if (resp := self.limited()) is not None:
            return resp
        name = f"{request.match_info['owner']}/{request.match_info['repo']}"
        self.tree_log.append(name)
        entry = self.trees.get(name)
        if isinstance(entry, int):
            return web.json_response({"message": "x"}, status=entry)
        if entry is None:
            return web.json_response({"message": "Not Found"}, status=404)
        return web.json_response({"tree": entry, "truncated": False})


def repo(name, pushed="2026-10-05T10:00:00Z", **kw):
    data = {"full_name": name, "name": name.split("/")[1], "default_branch": "main", "pushed_at": pushed,
            "size": 100, "fork": False, "archived": False, "description": "free proxy list", "topics": ["proxy-list"]}
    data.update(kw)
    return data


LIST_TREE = [blob("http.txt"), blob("socks5.txt"), blob("README.md"), blob("all.txt")]


class DiscoveryRun(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.api = await MockGitHub().start()
        self.addAsyncCleanup(self.api.close)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.registry = Path(self.tmp.name) / "sources" / "discovered.txt"

    async def run_discovery(self, **kw):
        defaults = dict(token="t", api=self.api.base, queries=("kw-a", "kw-b"), recent_days=14, window_days=7,
                        today=date(2026, 10, 7), search_interval=0.0, time_budget=30, log=lambda *_: None,
                        max_calls=100, max_search_calls=50)
        defaults.update(kw)
        return await discover.discover(self.registry, **defaults)

    async def test_finds_filters_and_registers(self):
        self.api.repos["kw-a"] = [
            repo("good/free-proxy-list"), repo("good2/proxies"),
            repo("forked/list", fork=True), repo("old/list", archived=True), repo("empty/list", size=0),
            repo("spam/hypeproxies-isp-proxies", description="Buy premium residential proxies cheap"),
            repo("user/user.github.io"), repo("rand/LxDOwCAQ"),
            repo("tools/proxy-server", description="reverse proxy server", topics=[]),
        ]
        self.api.repos["kw-b"] = [repo("good/free-proxy-list"), repo("third/socks-list")]  # one duplicate
        self.api.trees = {"good/free-proxy-list": LIST_TREE, "good2/proxies": [blob("README.md")],
                          "third/socks-list": [blob("socks5.txt")], "tools/proxy-server": 409,
                          "spam/hypeproxies-isp-proxies": LIST_TREE}
        report = await self.run_discovery()
        text = self.registry.read_text()
        self.assertEqual(report.files_total, 4)                       # 3 from good/... + 1 from third/...
        self.assertIn("http good/free-proxy-list/main/http.txt", text)
        self.assertIn("socks5 third/socks-list/main/socks5.txt", text)
        self.assertNotIn("forked", text); self.assertNotIn("old/list", text); self.assertNotIn("empty/list", text)
        self.assertNotIn("github.io", text); self.assertNotIn("LxDOwCAQ", text)
        self.assertNotIn("hypeproxies", text)
        self.assertNotIn("README", text)
        self.assertEqual(self.api.tree_log.count("good/free-proxy-list"), 1)   # duplicates scanned once
        self.assertNotIn("forked/list", self.api.tree_log)
        self.assertNotIn("user/user.github.io", self.api.tree_log)           # no API call wasted on spam
        self.assertNotIn("spam/hypeproxies-isp-proxies", self.api.tree_log)
        # repos without usable files are remembered so they are never scanned again
        self.assertIn("# @ good2/proxies", text)
        self.assertIn("# @ tools/proxy-server", text)
        # newest window first
        self.assertIn("pushed:2026-10-01..2026-10-07", self.api.search_log[0])

    async def test_second_run_only_scans_new_repositories(self):
        self.api.repos["kw-a"] = [repo("a/proxy-list")]
        self.api.trees = {"a/proxy-list": LIST_TREE, "b/proxy-list": [blob("http.txt")]}
        await self.run_discovery()
        self.assertEqual(self.api.tree_log, ["a/proxy-list"])
        self.api.repos["kw-a"] = [repo("a/proxy-list"), repo("b/proxy-list")]
        report = await self.run_discovery()
        self.assertEqual(self.api.tree_log, ["a/proxy-list", "b/proxy-list"])  # only the new one
        self.assertEqual(report.files_added, 1)
        self.assertEqual(report.files_total, 4)

    async def test_most_promising_repositories_are_scanned_first(self):
        self.api.repos["kw-a"] = [
            repo("meh/stuff", description="", topics=[]),
            repo("best/free-proxy-list", description="Free proxy list updated every hour"),
            repo("mid/proxy-thing", description="some proxy things", topics=[]),
        ]
        self.api.trees = {n: [blob("http.txt")] for n in ("meh/stuff", "best/free-proxy-list", "mid/proxy-thing")}
        await self.run_discovery(queries=("kw-a",), window_days=14)
        self.assertEqual(self.api.tree_log[0], "best/free-proxy-list")

    async def test_pagination_over_100_results(self):
        self.api.repos["kw-a"] = [repo(f"u{i}/proxy-list-{i}") for i in range(230)]
        self.api.trees = {f"u{i}/proxy-list-{i}": [blob("http.txt")] for i in range(230)}
        report = await self.run_discovery(queries=("kw-a",), max_calls=1000, window_days=14)
        self.assertEqual(report.repos_scanned, 230)
        self.assertEqual(report.files_total, 230)

    async def test_registry_cap_is_enforced(self):
        self.api.repos["kw-a"] = [repo(f"u{i}/proxy-list") for i in range(20)]
        self.api.trees = {f"u{i}/proxy-list": LIST_TREE for i in range(20)}
        report = await self.run_discovery(max_sources=7)
        self.assertEqual(report.files_total, 7)
        self.assertEqual(Registry.load(self.registry).total(), 7)
        again = await self.run_discovery(max_sources=7)                 # full: no further searching at all
        self.assertEqual(again.stopped, "registry full")
        self.assertEqual(again.files_total, 7)

    async def test_api_call_budget_is_respected(self):
        self.api.repos["kw-a"] = [repo(f"u{i}/proxy-list") for i in range(50)]
        self.api.trees = {f"u{i}/proxy-list": [blob("http.txt")] for i in range(50)}
        report = await self.run_discovery(max_calls=12, max_search_calls=2)
        self.assertLessEqual(report.api_calls, 12)
        self.assertLessEqual(self.api.calls, 12)
        self.assertEqual(report.stopped, "API call budget reached")
        self.assertEqual(report.repos_scanned, report.files_total)

    async def test_rate_limit_stops_gracefully_and_keeps_what_was_found(self):
        self.api.repos["kw-a"] = [repo(f"u{i}/proxy-list") for i in range(10)]
        self.api.trees = {f"u{i}/proxy-list": [blob("http.txt")] for i in range(10)}
        self.api.fail_after = 7                                         # 4 searches + 3 trees, then 403
        report = await self.run_discovery()
        self.assertIn("rate limited", report.stopped)
        self.assertGreater(report.files_total, 0)
        self.assertLess(report.files_total, 10)
        self.assertTrue(self.registry.exists())                         # partial progress is saved

    async def test_rate_limit_on_the_very_first_call_is_harmless(self):
        self.api.repos["kw-a"] = [repo("a/proxy-list")]
        self.api.fail_after = 0
        report = await self.run_discovery()
        self.assertIn("rate limited", report.stopped)
        self.assertEqual(report.files_total, 0)

    async def test_short_retry_after_is_waited_out(self):
        self.api.repos["kw-a"] = [repo("a/proxy-list")]
        self.api.trees = {"a/proxy-list": LIST_TREE}
        state = {"n": 0}

        def once():
            state["n"] += 1
            if state["n"] == 2:                                         # the tree call is throttled once ...
                return web.json_response({}, status=429, headers={"Retry-After": "0"})
            return None                                                 # ... and succeeds on retry
        self.api.limited = once
        report = await self.run_discovery(queries=("kw-a",), window_days=14)
        self.assertEqual(report.files_total, 3)

    async def test_prune_urls_are_removed_before_discovery(self):
        Registry(repos={"a/b": "2026-10-01"}, files={"a/b": [("http", "a/b/main/http.txt"),
                                                            ("http", "a/b/main/keep.txt")]}).save(self.registry)
        report = await self.run_discovery(prune_urls={github_urls("a/b/main/http.txt")[0]})
        self.assertEqual(report.files_pruned, 1)
        self.assertEqual(Registry.load(self.registry).files["a/b"], [("http", "a/b/main/keep.txt")])


@unittest.skipUnless(os.environ.get("LIVE_TESTS") and (os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")),
                     "set LIVE_TESTS=1 and GITHUB_TOKEN to run against the real GitHub API")
class LiveGitHub(unittest.IsolatedAsyncioTestCase):
    async def test_real_discovery_small_budget(self):
        token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "discovered.txt"
            report = await discover.discover(path, token=token, max_calls=40, max_search_calls=3,
                                             time_budget=120, queries=("topic:proxy-list",), log=lambda *_: None)
            self.assertGreater(report.repos_seen, 0)
            self.assertGreater(report.files_total, 0)
            lines = [l for l in path.read_text().splitlines() if l and not l.startswith("#")]
            self.assertEqual(len(lines), report.files_total)
            for line in lines:
                self.assertIsNotNone(parse_github_line(line, "discovered"), line)


if __name__ == "__main__":
    unittest.main()

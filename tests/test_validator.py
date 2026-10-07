import asyncio
import unittest

import support  # noqa: F401
from mocknet import JudgeServer, MockProxy, make_pki
from common import ALL_MASK, HTTP, SOCKS4, SOCKS5, stable_hash
from validator import (DEAD, FAILED, OK, UNRECOGNIZED, Checker, Judge, Record, Validator,
                       detect_environment, parse_ip)

PKI = make_pki()  # (trusted server ctx, client ctx, rogue server ctx)


class JudgeParsing(unittest.TestCase):
    def test_parse_ip(self):
        for body, expected in ((b"1.2.3.4\n", "1.2.3.4"), (b" 8.8.8.8 ", "8.8.8.8"),
                               (b'{"ip": "9.9.9.9"}', "9.9.9.9"), (b"IPv4,4.4.4.4,Remaining", "4.4.4.4"),
                               (b"2001:db8::1\n", "2001:db8::1"), (b'{"origin":"1.2.3.4, 5.6.7.8"}', "1.2.3.4")):
            self.assertEqual(parse_ip(body), expected, body)
        for body in (b"", b"hello world", b"<html><body>Your IP is 1.2.3.4</body></html>",
                     b"999.1.1.1", b"x" * 600, b"1.2.3.4.5"):
            self.assertIsNone(parse_ip(body), body)


class World(unittest.IsolatedAsyncioTestCase):
    """Judges (HTTP + HTTPS) and a factory for mock proxies."""

    judge_mode = "length"

    async def asyncSetUp(self):
        server_ctx, self.client_ctx, self.rogue_ctx = PKI
        self.http_judge = JudgeServer(mode=self.judge_mode)
        self.tls_judge = JudgeServer(ssl_ctx=server_ctx, mode=self.judge_mode)
        await self.http_judge.start()
        await self.tls_judge.start()
        self.judge = Judge("127.0.0.1", "/ip", http_port=self.http_judge.port,
                           https_port=self.tls_judge.port, ip="127.0.0.1")
        self.proxies: list[MockProxy] = []
        self.addAsyncCleanup(self._close)

    async def _close(self):
        for proxy in self.proxies:
            await proxy.close()
        await self.http_judge.close()
        await self.tls_judge.close()

    async def proxy(self, kind, behavior="ok", **kw) -> MockProxy:
        mock = MockProxy(kind, behavior, rogue_ctx=self.rogue_ctx, **kw)
        await mock.start()
        self.proxies.append(mock)
        return mock

    def checker(self, judges=None, tls_judges=None, **kw) -> Checker:
        judges = judges or [self.judge]
        defaults = dict(egress=frozenset({"127.0.0.1"}), timeout=3.0, connect_timeout=1.0,
                        handshake_timeout=1.5, ssl_context=self.client_ctx)
        defaults.update(kw)
        return Checker(judges, tls_judges if tls_judges is not None else judges, **defaults)

    async def probe(self, mock, protocol, checker=None):
        return await (checker or self.checker()).probe("127.0.0.1", mock.port, protocol)


class ProtocolHandshakes(World):
    async def test_good_proxies_of_every_protocol(self):
        for kind in ("http", "socks5", "socks4"):
            mock = await self.proxy(kind)
            result = await self.probe(mock, kind)
            self.assertEqual(result.status, OK, (kind, result))
            self.assertTrue(result.http and result.https, (kind, result))
            self.assertEqual(result.exit_ip, mock.exit_ip)  # the judge saw the proxy, not us
            self.assertGreaterEqual(result.latency_ms, 0)

    async def test_multi_protocol_port(self):
        mock = await self.proxy("multi")
        for protocol in ("http", "socks5", "socks4"):
            self.assertEqual((await self.probe(mock, protocol)).status, OK, protocol)

    async def test_dead_port(self):
        mock = await self.proxy("http")
        port = mock.port
        await mock.close()
        result = await self.checker().probe("127.0.0.1", port, "http")
        self.assertEqual(result.status, DEAD)

    async def test_transparent_proxy_is_rejected(self):
        for kind in ("http", "socks5", "socks4"):
            mock = await self.proxy(kind, "transparent")
            result = await self.probe(mock, kind)
            self.assertEqual(result.status, FAILED, kind)
            self.assertIn("transparent", result.detail)

    async def test_wrong_protocol_is_unrecognized_not_failed(self):
        http_proxy = await self.proxy("http")
        socks_proxy = await self.proxy("socks5")
        socks4_proxy = await self.proxy("socks4")
        self.assertEqual((await self.probe(http_proxy, "socks5")).status, UNRECOGNIZED)
        self.assertEqual((await self.probe(http_proxy, "socks4")).status, UNRECOGNIZED)
        self.assertEqual((await self.probe(socks_proxy, "http")).status, UNRECOGNIZED)
        self.assertEqual((await self.probe(socks_proxy, "socks4")).status, UNRECOGNIZED)
        self.assertEqual((await self.probe(socks4_proxy, "socks5")).status, UNRECOGNIZED)
        self.assertEqual((await self.probe(socks4_proxy, "http")).status, UNRECOGNIZED)

    async def test_misbehaving_proxies_are_rejected(self):
        for kind, behavior, expected in (
            ("http", "blackhole", UNRECOGNIZED), ("socks5", "blackhole", UNRECOGNIZED),
            ("http", "garbage", UNRECOGNIZED), ("socks5", "garbage", UNRECOGNIZED),
            ("http", "auth", FAILED), ("socks5", "auth", FAILED), ("socks4", "refuse", FAILED),
            ("socks5", "refuse", FAILED),
        ):
            mock = await self.proxy(kind, behavior)
            result = await self.probe(mock, kind)
            self.assertEqual(result.status, expected, (kind, behavior, result))
            self.assertFalse(result.ok)

    async def test_html_liar_is_rejected(self):
        mock = await self.proxy("http", "html")
        result = await self.probe(mock, "http")
        self.assertFalse(result.ok, result)

    async def test_slow_proxy_times_out(self):
        mock = await self.proxy("http", "slow", delay=30)
        result = await self.checker(timeout=1.5, handshake_timeout=1.0).probe("127.0.0.1", mock.port, "http")
        self.assertFalse(result.ok)

    async def test_https_capability_flags(self):
        connect_only = await self.proxy("http", "connect_only")
        r = await self.probe(connect_only, "http")
        self.assertTrue(r.ok and r.https and not r.http, r)       # alive for HTTPS only
        no_connect = await self.proxy("http", "no_connect")
        r = await self.probe(no_connect, "http")
        self.assertTrue(r.ok and r.http and not r.https, r)       # alive for plain HTTP only

    async def test_mitm_proxy_fails_certificate_verification(self):
        mock = await self.proxy("http", "mitm")
        r = await self.probe(mock, "http")
        self.assertTrue(r.ok and r.http and not r.https, r)       # plain works, TLS tunnel untrusted

    async def test_tls_checks_can_be_disabled(self):
        mock = await self.proxy("http")
        r = await self.checker(tls_judges=[]).probe("127.0.0.1", mock.port, "http")
        self.assertTrue(r.ok and r.http and not r.https)


class JudgeBehaviour(World):
    async def judges_with(self, mode):
        bad = JudgeServer(mode=mode)
        await bad.start()
        self.addAsyncCleanup(bad.close)
        return Judge("127.0.0.1", "/ip", http_port=bad.port, https_port=0, ip="127.0.0.1"), bad

    async def test_framing_variants_are_all_understood(self):
        for mode in ("length", "chunked", "eof"):
            judge, _ = await self.judges_with(mode)
            for kind in ("http", "socks5"):
                mock = await self.proxy(kind)
                r = await self.checker([judge], tls_judges=[]).probe("127.0.0.1", mock.port, kind)
                self.assertTrue(r.ok, (mode, kind, r))

    async def test_truncated_or_garbage_responses_are_not_accepted(self):
        for mode in ("truncated", "garbage", "html", "403"):
            judge, _ = await self.judges_with(mode)
            mock = await self.proxy("http")
            r = await self.checker([judge], tls_judges=[], timeout=2.0, handshake_timeout=1.0).probe(
                "127.0.0.1", mock.port, "http")
            self.assertFalse(r.ok, (mode, r))

    async def test_second_judge_is_tried_when_the_first_misbehaves(self):
        bad, bad_server = await self.judges_with("403")
        mock = await self.proxy("http")
        checker = self.checker([bad, self.judge], tls_judges=[])
        # choose the salt so that the broken judge is picked first
        first_is_bad = (stable_hash(f"127.0.0.1:{mock.port}")) % 2 == 0
        salt = 0 if first_is_bad else 1
        r = await checker.probe("127.0.0.1", mock.port, "http", salt)
        self.assertTrue(r.ok, r)
        self.assertGreaterEqual(bad_server.requests, 1)  # the bad judge really was asked first

    async def test_no_second_judge_when_proxy_is_unrecognized(self):
        judge, server = await self.judges_with("length")
        mock = await self.proxy("http", "garbage")
        await self.checker([judge, judge], tls_judges=[]).probe("127.0.0.1", mock.port, "http")
        self.assertEqual(server.requests, 0)


class CandidateLogic(World):
    async def test_specific_label_is_tried_first_and_fallback_fixes_wrong_labels(self):
        socks = await self.proxy("socks5")
        records, ran = await self.checker().check_candidate(socks.address, HTTP, ALL_MASK & ~HTTP)
        self.assertEqual([(r.protocol) for r in records], ["socks5"])   # mislabeled as http: found via fallback
        self.assertEqual(ran, 3)                                       # http + socks5 + socks4

    async def test_fallback_not_used_when_label_was_recognized(self):
        auth = await self.proxy("http", "auth")
        records, ran = await self.checker().check_candidate(auth.address, HTTP, ALL_MASK & ~HTTP)
        self.assertEqual(records, [])
        self.assertEqual(ran, 1)        # recognised as HTTP (407): other protocols not worth trying

    async def test_unknown_label_tries_everything_in_parallel(self):
        multi = await self.proxy("multi")
        records, ran = await self.checker().check_candidate(multi.address, ALL_MASK, 0)
        self.assertEqual(sorted(r.protocol for r in records), ["http", "socks4", "socks5"])
        self.assertEqual(ran, 3)

    async def test_record_update_and_serialisation(self):
        record = Record("1.2.3.4:80", "http", 100, "9.9.9.9", True, True)
        from validator import Probe
        record.update(Probe(OK, 300, "9.9.9.8", http=True, https=False))
        self.assertEqual((record.passed, record.total, record.latency_ms, record.https), (2, 2, 200, False))
        record.update(Probe(FAILED))
        self.assertEqual((record.passed, record.total, record.alive), (2, 3, False))
        self.assertEqual(record.to_dict()["url"], "http://1.2.3.4:80")


class FullPipeline(World):
    async def test_end_to_end_ground_truth(self):
        """A mixed population of proxies: exactly the good ones must come out."""
        good, bad = {}, []
        for kind in ("http", "http", "socks5", "socks5", "socks4"):
            mock = await self.proxy(kind)
            good[(mock.address, "socks5" if kind == "socks5" else kind)] = mock
        multi = await self.proxy("multi")
        for p in ("http", "socks5", "socks4"):
            good[(multi.address, p)] = multi
        for kind, behavior in (("http", "transparent"), ("socks5", "transparent"), ("http", "garbage"),
                               ("http", "blackhole"), ("socks5", "auth"), ("http", "auth"),
                               ("http", "html"), ("socks4", "refuse")):
            bad.append(await self.proxy(kind, behavior))
        dead = await self.proxy("http")
        dead_address = dead.address
        await dead.close()

        candidates = [(m.address, ALL_MASK, 0) for m in self.proxies if m.address != dead_address]
        candidates.append((dead_address, ALL_MASK, 0))
        validator = Validator(self.checker(), workers=64, quiet=True)
        result = await validator.verify(candidates, rounds=2, controls=())
        got = {(r.proxy, r.protocol) for r in result.records}
        self.assertEqual(got, set(good), f"unexpected: {got ^ set(good)}")
        for record in result.records:
            self.assertEqual((record.passed, record.total), (3, 3))   # first pass + 2 confirmations
            self.assertTrue(record.http and record.https)
            self.assertEqual(record.exit_ip, good[(record.proxy, record.protocol)].exit_ip)
        self.assertEqual(result.stats["candidates"], len(candidates))
        self.assertEqual(result.stats["live"], len(good))
        self.assertLess(result.stats["tcp_alive"], len(candidates))     # the closed port was filtered out

    async def test_confirmation_drops_a_proxy_that_dies_between_rounds(self):
        stable = await self.proxy("http")
        flaky = await self.proxy("http")
        validator = Validator(self.checker(), workers=8, quiet=True)
        records = await validator.first_pass([(stable.address, HTTP, 0), (flaky.address, HTTP, 0)])
        self.assertEqual(len(records), 2)
        await flaky.close()
        await validator.confirm(records, rounds=1)
        alive = {r.proxy for r in records if r.alive}
        self.assertEqual(alive, {stable.address})

    async def test_time_budget_stops_feeding_new_work(self):
        mocks = [await self.proxy("http", "blackhole") for _ in range(6)]
        validator = Validator(self.checker(timeout=1.0, connect_timeout=0.5, handshake_timeout=0.3),
                              workers=1, quiet=True)
        result = await validator.verify([(m.address, HTTP, 0) for m in mocks], rounds=0,
                                        time_budget=0.4, controls=())
        self.assertGreater(validator.stats["skipped_deadline"], 0)
        self.assertEqual(result.records, [])

    async def test_environment_detection_with_local_judges(self):
        env = await detect_environment([self.judge], timeout=3.0, ssl_context=self.client_ctx,
                                       log=lambda _: None)
        self.assertEqual(env.egress, frozenset({"127.0.0.1"}))
        self.assertEqual(len(env.plain), 1)
        self.assertEqual(len(env.tls), 1)
        self.assertTrue(env.verify_tls)
        self.assertEqual(env.plain[0].ip, "127.0.0.1")

    async def test_judges_that_redirect_plain_http_are_not_used_for_plain_probes(self):
        redirecting = JudgeServer(mode="redirect")
        await redirecting.start()
        self.addAsyncCleanup(redirecting.close)
        bad = Judge("127.0.0.1", "/ip", http_port=redirecting.port, https_port=0, ip="127.0.0.1")
        env = await detect_environment([bad, self.judge], timeout=3.0, ssl_context=self.client_ctx,
                                       log=lambda _: None)
        self.assertEqual([j.http_port for j in env.plain], [self.judge.http_port])   # redirector dropped
        self.assertEqual(env.egress, frozenset({"127.0.0.1"}))

    async def test_environment_without_judges_reachable_degrades_gracefully(self):
        dead_judge = Judge("127.0.0.1", "/ip", http_port=1, https_port=2, ip="127.0.0.1")
        env = await detect_environment([dead_judge], timeout=1.0, log=lambda _: None)
        self.assertEqual(env.egress, frozenset())
        self.assertTrue(env.notes)
        self.assertEqual(env.plain, [dead_judge])

    async def test_interception_is_detected_and_blacklisted(self):
        """A network that answers on 'impossible' addresses makes everything look alive."""
        interceptor = await self.proxy("http")          # stands in for the interceptor
        validator = Validator(self.checker(), workers=4, quiet=True)
        leaked = await validator.detect_interception((("127.0.0.1", interceptor.port),))
        self.assertEqual(leaked, {interceptor.exit_ip})
        again = await validator.checker.probe("127.0.0.1", interceptor.port, "http")
        self.assertFalse(again.ok)                       # now rejected as "our" address


if __name__ == "__main__":
    unittest.main()

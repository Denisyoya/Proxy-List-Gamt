"""Independent cross-checks against third-party implementations.

The mock proxies and the validator were written together, so on their own they could agree
on a *wrong* reading of a protocol. These tests break that circle:

* ``curl`` (an independent client) talks to the mock proxies  -> the mocks speak real
  HTTP / CONNECT / SOCKS4 / SOCKS5;
* ``pproxy`` and ``proxy.py`` (independent proxy servers) are probed by the validator
  -> the validator speaks real protocols.

Tests are skipped when the optional tool is not installed (see requirements-dev.txt).
"""
import asyncio
import importlib.util
import shutil
import socket
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import support  # noqa: F401
from mocknet import JudgeServer, MockProxy, make_pki
from validator import Checker, Judge

PKI = make_pki(with_ca_pem=True)
HAVE_CURL = shutil.which("curl") is not None
HAVE_PPROXY = importlib.util.find_spec("pproxy") is not None
HAVE_PROXYPY = importlib.util.find_spec("proxy") is not None


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class World(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        server_ctx, self.client_ctx, self.rogue, ca_pem = PKI
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.ca = Path(self.tmp.name) / "ca.pem"
        self.ca.write_bytes(ca_pem)
        self.http_judge, self.tls_judge = JudgeServer(), JudgeServer(ssl_ctx=server_ctx)
        await self.http_judge.start()
        await self.tls_judge.start()
        self.addAsyncCleanup(self.http_judge.close)
        self.addAsyncCleanup(self.tls_judge.close)
        self.procs: list[subprocess.Popen] = []
        self.addCleanup(self._stop_processes)

    def _stop_processes(self):
        for process in self.procs:
            process.terminate()
        for process in self.procs:
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()

    async def curl(self, *args):
        process = await asyncio.create_subprocess_exec(
            "curl", "-sS", "--max-time", "8", *args, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE)
        out, err = await process.communicate()
        return (out or err).decode().strip()


@unittest.skipUnless(HAVE_CURL, "curl not installed")
class CurlAgainstMocks(World):
    async def test_mock_proxies_are_understood_by_curl(self):
        for kind in ("http", "socks5", "socks4"):
            mock = MockProxy(kind)
            await mock.start()
            self.addAsyncCleanup(mock.close)
            plain_url = f"http://127.0.0.1:{self.http_judge.port}/ip"
            tls_url = f"https://127.0.0.1:{self.tls_judge.port}/ip"
            if kind == "http":
                plain = await self.curl("-x", f"http://{mock.address}", plain_url)
                tls = await self.curl("-x", f"http://{mock.address}", "--proxytunnel", "--cacert", str(self.ca), tls_url)
            else:
                flag = "--socks5" if kind == "socks5" else "--socks4"
                plain = await self.curl(flag, mock.address, plain_url)
                tls = await self.curl(flag, mock.address, "--cacert", str(self.ca), tls_url)
            self.assertEqual(plain, mock.exit_ip, kind)
            self.assertEqual(tls, mock.exit_ip, kind)


class ValidatorAgainstRealServers(World):
    def checker(self):
        judge = Judge("127.0.0.1", "/ip", http_port=self.http_judge.port,
                      https_port=self.tls_judge.port, ip="127.0.0.1")
        # these servers connect from 127.0.0.1 itself, so pretend our own address is another one
        return Checker([judge], [judge], egress=frozenset({"203.0.113.77"}), timeout=8,
                       connect_timeout=2, handshake_timeout=3, ssl_context=self.client_ctx)

    async def start(self, command: list[str]) -> int:
        port = free_port()
        self.procs.append(subprocess.Popen([c.replace("PORT", str(port)) for c in command],
                                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
        for _ in range(60):
            try:
                socket.create_connection(("127.0.0.1", port), 0.2).close()
                return port
            except OSError:
                await asyncio.sleep(0.2)
        self.fail(f"{command[0]} did not start")

    @unittest.skipUnless(HAVE_PPROXY, "pproxy not installed")
    async def test_pproxy_speaks_all_three_protocols_on_one_port(self):
        port = await self.start([sys.executable, "-m", "pproxy", "-l", "http+socks4+socks5://127.0.0.1:PORT"])
        for protocol in ("http", "socks5", "socks4"):
            result = await self.checker().probe("127.0.0.1", port, protocol)
            self.assertTrue(result.ok, (protocol, result))
            self.assertTrue(result.http and result.https, (protocol, result))
            self.assertEqual(result.exit_ip, "127.0.0.1")

    @unittest.skipUnless(HAVE_PPROXY, "pproxy not installed")
    async def test_wrong_protocol_against_a_real_http_proxy_is_unrecognized(self):
        port = await self.start([sys.executable, "-m", "pproxy", "-l", "http://127.0.0.1:PORT"])
        self.assertEqual((await self.checker().probe("127.0.0.1", port, "socks5")).status, "unrecognized")
        self.assertEqual((await self.checker().probe("127.0.0.1", port, "socks4")).status, "unrecognized")
        self.assertTrue((await self.checker().probe("127.0.0.1", port, "http")).ok)

    @unittest.skipUnless(HAVE_PROXYPY, "proxy.py not installed")
    async def test_proxy_py_http_and_connect(self):
        port = await self.start([sys.executable, "-m", "proxy", "--hostname", "127.0.0.1", "--port", "PORT",
                                 "--log-level", "ERROR"])
        result = await self.checker().probe("127.0.0.1", port, "http")
        self.assertTrue(result.ok and result.http and result.https, result)


if __name__ == "__main__":
    unittest.main()

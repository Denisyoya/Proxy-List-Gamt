"""A tiny fake internet on the loopback interface for testing the pipeline.

* ``JudgeServer``   IP-echo endpoint (HTTP or HTTPS), answers with the peer address
* ``MockProxy``     HTTP / SOCKS4 / SOCKS5 proxy with configurable (mis)behaviour
* ``make_pki``      throw-away CA + certificates for the HTTPS judge

Every healthy mock proxy connects to the judge from its *own* loopback address
(``127.0.x.y``). The judge therefore sees a different exit IP than our direct
``127.0.0.1``, exactly like a real proxy, and a "transparent" proxy (which
connects from ``127.0.0.1``) is distinguishable from a good one.
"""
from __future__ import annotations

import asyncio
import datetime
import ipaddress
import socket
import ssl
import struct
import tempfile
from pathlib import Path

_exit_ips = (f"127.0.{a}.{b}" for a in range(1, 250) for b in range(1, 250))


def next_exit_ip() -> str:
    return next(_exit_ips)


# ------------------------------------------------------------------- TLS


def make_pki(with_ca_pem: bool = False):
    """Return ``(trusted_server_ctx, client_ctx_trusting_it, rogue_server_ctx)``.

    With ``with_ca_pem`` a fourth item is returned: the trusted certificate (PEM)
    for use with external clients such as ``curl --cacert``.
    """
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    def build(common_name: str):
        key = ec.generate_private_key(ec.SECP256R1())
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)])
        now = datetime.datetime.now(datetime.timezone.utc)
        cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
                .public_key(key.public_key()).serial_number(x509.random_serial_number())
                .not_valid_before(now - datetime.timedelta(days=1))
                .not_valid_after(now + datetime.timedelta(days=2))
                .add_extension(x509.SubjectAlternativeName([
                    x509.DNSName("localhost"), x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]),
                    critical=False)
                .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
                .sign(key, hashes.SHA256()))
        return (cert.public_bytes(serialization.Encoding.PEM),
                key.private_bytes(serialization.Encoding.PEM,
                                  serialization.PrivateFormat.TraditionalOpenSSL,
                                  serialization.NoEncryption()))

    def server_ctx(cert_pem: bytes, key_pem: bytes) -> ssl.SSLContext:
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "c.pem").write_bytes(cert_pem)
            (Path(tmp) / "k.pem").write_bytes(key_pem)
            ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            ctx.load_cert_chain(Path(tmp) / "c.pem", Path(tmp) / "k.pem")
            return ctx

    good_cert, good_key = build("judge.test")
    rogue_cert, rogue_key = build("rogue.test")
    client = ssl.create_default_context()
    client.load_verify_locations(cadata=good_cert.decode())
    contexts = server_ctx(good_cert, good_key), client, server_ctx(rogue_cert, rogue_key)
    return contexts + (good_cert,) if with_ca_pem else contexts


# ----------------------------------------------------------------- judge


class JudgeServer:
    """Answers every request with the peer's IP address."""

    def __init__(self, ssl_ctx: ssl.SSLContext | None = None, mode: str = "length") -> None:
        self.ssl_ctx = ssl_ctx
        self.mode = mode          # length | chunked | eof | 403 | redirect | garbage | html | truncated
        self.requests = 0
        self.port = 0
        self._server: asyncio.AbstractServer | None = None

    async def start(self) -> int:
        self._server = await asyncio.start_server(self._handle, "127.0.0.1", 0, ssl=self.ssl_ctx)
        self.port = self._server.sockets[0].getsockname()[1]
        return self.port

    async def close(self) -> None:
        if self._server:
            self._server.close()
            await self._server.wait_closed()

    async def _handle(self, reader, writer) -> None:
        try:
            await reader.readuntil(b"\r\n\r\n")
            self.requests += 1
            peer = writer.get_extra_info("peername")[0]
            body = f"{peer}\n".encode()
            head = b"HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\n"
            if self.mode == "redirect":  # e.g. a judge that forces plain HTTP over to HTTPS
                writer.write(b"HTTP/1.1 301 Moved Permanently\r\nLocation: https://127.0.0.1:1/ip\r\n"
                             b"Content-Length: 0\r\nConnection: close\r\n\r\n")
            elif self.mode == "403":
                writer.write(b"HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
            elif self.mode == "garbage":
                writer.write(head + b"Content-Length: 11\r\nConnection: close\r\n\r\nhello world")
            elif self.mode == "html":
                page = b"<html><body>Your IP is " + peer.encode() + b"</body></html>"
                writer.write(head + b"Content-Length: %d\r\nConnection: close\r\n\r\n" % len(page) + page)
            elif self.mode == "chunked":
                half = len(body) // 2
                writer.write(head + b"Transfer-Encoding: chunked\r\nConnection: close\r\n\r\n"
                             + b"%x\r\n%s\r\n%x\r\n%s\r\n0\r\n\r\n" % (half, body[:half], len(body) - half, body[half:]))
            elif self.mode == "eof":
                writer.write(head + b"Connection: close\r\n\r\n" + body)
            elif self.mode == "truncated":  # promises more bytes than it sends
                writer.write(head + b"Content-Length: 100\r\nConnection: close\r\n\r\n" + body)
            else:
                writer.write(head + b"Content-Length: %d\r\nConnection: close\r\n\r\n" % len(body) + body)
            await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionError, ssl.SSLError, OSError):
            pass
        finally:
            writer.close()


# ----------------------------------------------------------------- proxy


class MockProxy:
    """One fake proxy.

    kind      http | socks4 | socks5 | multi (sniffs the first byte)
    behavior  ok            relay from its own loopback address (a good proxy)
              transparent   relay from 127.0.0.1 (leaks the client address)
              blackhole     accepts the connection, never answers
              garbage       answers with noise
              html          HTTP proxy answering 200 + an HTML page for everything
              auth          requires credentials (HTTP 407 / SOCKS5 method 0x02)
              connect_only  HTTP: refuses plain GET, allows CONNECT
              no_connect    HTTP: allows plain GET, refuses CONNECT
              slow          answers only after ``delay`` seconds
              mitm          plain GET fine, CONNECT presents an untrusted certificate
              refuse        SOCKS: replies "connection not allowed"
    """

    def __init__(self, kind: str, behavior: str = "ok", *, delay: float = 30.0,
                 exit_ip: str | None = None, rogue_ctx: ssl.SSLContext | None = None) -> None:
        self.kind = kind
        self.behavior = behavior
        self.delay = delay
        self.exit_ip = exit_ip or (None if behavior == "transparent" else next_exit_ip())
        self.rogue_ctx = rogue_ctx
        self.port = 0
        self.connections = 0
        self._server: asyncio.AbstractServer | None = None
        self._tasks: set[asyncio.Task] = set()

    @property
    def address(self) -> str:
        return f"127.0.0.1:{self.port}"

    async def start(self) -> int:
        self._server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
        self.port = self._server.sockets[0].getsockname()[1]
        return self.port

    async def close(self) -> None:
        if self._server:
            self._server.close()
        for task in list(self._tasks):
            task.cancel()
        if self._server:
            await self._server.wait_closed()

    # -- plumbing --------------------------------------------------------

    async def _upstream(self, host: str, port: int):
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setblocking(False)
        if self.exit_ip:
            sock.bind((self.exit_ip, 0))
        await asyncio.get_running_loop().sock_connect(sock, (host, port))
        return await asyncio.open_connection(sock=sock)

    @staticmethod
    async def _pipe(source, sink) -> None:
        try:
            while data := await source.read(65536):
                sink.write(data)
                await sink.drain()
        except (ConnectionError, OSError, asyncio.CancelledError):
            pass
        finally:
            try:
                sink.close()
            except Exception:
                pass

    async def _relay(self, reader, writer, host: str, port: int, preface: bytes = b"") -> None:
        up_reader, up_writer = await self._upstream(host, port)
        if preface:
            up_writer.write(preface)
        await asyncio.gather(self._pipe(reader, up_writer), self._pipe(up_reader, writer))

    async def _handle(self, reader, writer) -> None:
        task = asyncio.current_task()
        self._tasks.add(task)
        self.connections += 1
        try:
            await self._serve(reader, writer)
        except (asyncio.CancelledError, ConnectionError, OSError, asyncio.IncompleteReadError,
                ssl.SSLError):
            pass
        finally:
            self._tasks.discard(task)
            try:
                writer.close()
            except Exception:
                pass

    async def _serve(self, reader, writer) -> None:
        if self.behavior == "blackhole":
            await asyncio.sleep(3600)
            return
        first = await reader.readexactly(1)
        if self.behavior == "garbage":
            writer.write(b"\x00\xffNOT-A-PROXY\r\n")
            await writer.drain()
            return
        if self.behavior == "slow":
            await asyncio.sleep(self.delay)
        kind = self.kind
        if kind == "multi":
            kind = {b"\x05": "socks5", b"\x04": "socks4"}.get(first, "http")
        if kind == "socks5":
            await self._socks5(first, reader, writer)
        elif kind == "socks4":
            await self._socks4(first, reader, writer)
        else:
            await self._http(first, reader, writer)

    # -- protocols -------------------------------------------------------

    async def _socks5(self, first, reader, writer) -> None:
        if first != b"\x05":
            return
        count = (await reader.readexactly(1))[0]
        await reader.readexactly(count)
        if self.behavior == "auth":
            writer.write(b"\x05\x02")
            await writer.drain()
            return
        writer.write(b"\x05\x00")
        head = await reader.readexactly(4)
        atyp = head[3]
        if atyp == 1:
            host = socket.inet_ntoa(await reader.readexactly(4))
        elif atyp == 3:
            host = (await reader.readexactly((await reader.readexactly(1))[0])).decode()
        else:
            host = socket.inet_ntop(socket.AF_INET6, await reader.readexactly(16))
        (port,) = struct.unpack(">H", await reader.readexactly(2))
        if self.behavior == "refuse":
            writer.write(b"\x05\x02\x00\x01\x00\x00\x00\x00\x00\x00")
            await writer.drain()
            return
        writer.write(b"\x05\x00\x00\x01\x00\x00\x00\x00\x00\x00")
        await writer.drain()
        await self._relay(reader, writer, host, port)

    async def _socks4(self, first, reader, writer) -> None:
        if first != b"\x04":
            return
        await reader.readexactly(1)  # command
        (port,) = struct.unpack(">H", await reader.readexactly(2))
        host = socket.inet_ntoa(await reader.readexactly(4))
        await reader.readuntil(b"\x00")  # user id
        if self.behavior in ("refuse", "auth"):
            writer.write(b"\x00\x5b\x00\x00\x00\x00\x00\x00")
            await writer.drain()
            return
        writer.write(b"\x00\x5a\x00\x00\x00\x00\x00\x00")
        await writer.drain()
        await self._relay(reader, writer, host, port)

    async def _http(self, first, reader, writer) -> None:
        head = first + await reader.readuntil(b"\r\n\r\n")
        request_line, _, rest = head.partition(b"\r\n")
        method, target, version = request_line.split(b" ", 2)
        if self.behavior == "auth":
            writer.write(b"HTTP/1.1 407 Proxy Authentication Required\r\nContent-Length: 0\r\n\r\n")
            await writer.drain()
            return
        if self.behavior == "html":
            page = b"<html><body>Proxy says: welcome</body></html>"
            writer.write(b"HTTP/1.1 200 OK\r\nContent-Type: text/html\r\nContent-Length: %d\r\n\r\n"
                         % len(page) + page)
            await writer.drain()
            return
        if method == b"CONNECT":
            if self.behavior == "no_connect":
                writer.write(b"HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\n\r\n")
                await writer.drain()
                return
            host, _, port = target.decode().rpartition(":")
            if self.behavior == "mitm" and self.rogue_ctx is not None:
                writer.write(b"HTTP/1.1 200 Connection established\r\n\r\n")
                await writer.drain()
                await writer.start_tls(self.rogue_ctx)
                await reader.read(1)  # a verifying client aborts before sending anything
                return
            writer.write(b"HTTP/1.1 200 Connection established\r\n\r\n")
            await writer.drain()
            await self._relay(reader, writer, host, int(port))
            return
        if self.behavior == "connect_only":
            writer.write(b"HTTP/1.1 405 Method Not Allowed\r\nContent-Length: 0\r\n\r\n")
            await writer.drain()
            return
        # absolute-form request: GET http://host:port/path HTTP/1.1
        if not target.startswith(b"http://"):
            writer.write(b"HTTP/1.1 400 Bad Request\r\nContent-Length: 0\r\n\r\n")
            await writer.drain()
            return
        authority, _, path = target[len(b"http://"):].partition(b"/")
        host, _, port = authority.decode().partition(":")
        forwarded = b" ".join([method, b"/" + path, version]) + b"\r\n" + rest
        await self._relay(reader, writer, host, int(port or 80), preface=forwarded)

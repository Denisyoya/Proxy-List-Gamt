"""Proxy verification engine.

How a candidate is verified
---------------------------
1. **TCP pre-filter** - most listed proxies are dead. A bare TCP connect (cheap,
   thousands in parallel) removes them before any protocol work happens.
2. **Protocol probe** - the proxy is spoken to *directly* over asyncio streams
   (no HTTP client library, one connection per probe):

   * ``http``   absolute-URI ``GET`` to an IP-echo judge, plus ``CONNECT :443``
     followed by a certificate-verified TLS handshake and a second request;
   * ``socks5`` / ``socks4`` handshake, ``CONNECT`` to the judge on :80 and :443
     (TLS verified).

   A proxy is *alive* when the judge answered through it with a complete,
   well-formed response whose IP differs from ours (a transparent proxy that
   leaks our own address is rejected).
3. **Smart fallback** - a proxy listed as ``socks5`` is only tried as ``http`` /
   ``socks4`` when the first handshake was *not recognised* (wrong label), never
   when it was recognised but refused.
4. **Confirmation rounds** - survivors are re-probed against different judges;
   only proxies that pass the final check are published, together with how many
   checks they passed.

Everything runs through bounded worker pools, so memory stays flat no matter
how many candidates there are, and file descriptors are clamped to the OS limit.
"""
from __future__ import annotations

import asyncio
import ipaddress
import re
import socket
import ssl
import struct
import time
from dataclasses import dataclass, field, replace
from typing import Callable

import aiohttp

from common import ALL_MASK, probe_order, split_proxy, stable_hash

try:  # not available on Windows
    import resource
except ImportError:  # pragma: no cover
    resource = None

OK, DEAD, UNRECOGNIZED, FAILED = "ok", "dead", "unrecognized", "failed"
USER_AGENT = "curl/8.5.0"  # IP-echo services answer curl with the bare address
_STATUS_RE = re.compile(rb"^HTTP/\d(?:\.\d)?\s+(\d{3})")
_IPV4_SEARCH = re.compile(r"(?<![\d.])(?:(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)\.){3}"
                          r"(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)(?![\d.])")
_HTML_HINT = re.compile(r"<\s*(?:html|body|head|!doctype)", re.IGNORECASE)


# ------------------------------------------------------------------ judges


@dataclass(frozen=True, slots=True)
class Judge:
    """An endpoint that answers with the caller's IP address."""

    host: str
    path: str = "/"
    http_port: int = 80         # 0 = the judge has no plain-HTTP endpoint
    https_port: int = 443       # 0 = the judge has no TLS endpoint
    ip: str = ""                # resolved IPv4 (needed for SOCKS targets)

    def authority(self, port: int) -> str:
        return self.host if port in (80, 443) else f"{self.host}:{port}"


# Addresses reserved for documentation (RFC 5737): no real proxy can live here.
CONTROL_TARGETS = (("192.0.2.1", 8080), ("198.51.100.7", 3128), ("203.0.113.9", 1080))

DEFAULT_JUDGES = (
    Judge("api.ipify.org"),
    Judge("icanhazip.com"),
    Judge("checkip.amazonaws.com"),
    Judge("ipinfo.io", "/ip"),
    Judge("ifconfig.me", "/ip"),
    Judge("ifconfig.co", "/ip"),
    Judge("ident.me"),
    Judge("ipecho.net", "/plain"),
    Judge("wtfismyip.com", "/text"),
    Judge("api.my-ip.io", "/ip"),
    Judge("myexternalip.com", "/raw"),
    Judge("ip-api.com", "/line/?fields=query", https_port=0),  # TLS is a paid feature there
)


def parse_ip(body: bytes) -> str | None:
    """The IP address contained in a judge response, or None."""
    text = body.decode("ascii", "ignore").strip()
    if not text or len(text) > 512 or _HTML_HINT.search(text):
        return None
    try:
        return str(ipaddress.ip_address(text))
    except ValueError:
        pass
    found = _IPV4_SEARCH.search(text)
    return found.group(0) if found else None


# ----------------------------------------------------------------- results


@dataclass(slots=True)
class Probe:
    status: str
    latency_ms: int = 0
    exit_ip: str = ""
    http: bool = False     # plain HTTP through the proxy worked
    https: bool = False    # a certificate-verified TLS tunnel worked
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.status == OK


@dataclass(slots=True)
class Record:
    proxy: str
    protocol: str
    latency_ms: int
    exit_ip: str
    http: bool
    https: bool
    passed: int = 1
    total: int = 1
    latency_sum: int = 0
    alive: bool = True     # outcome of the most recent check

    def __post_init__(self) -> None:
        self.latency_sum = self.latency_sum or self.latency_ms

    def update(self, probe: Probe) -> None:
        self.total += 1
        self.alive = probe.ok
        if probe.ok:
            self.passed += 1
            self.latency_sum += probe.latency_ms
            self.latency_ms = round(self.latency_sum / self.passed)
            self.exit_ip, self.http, self.https = probe.exit_ip, probe.http, probe.https

    def to_dict(self) -> dict:
        return {
            "proxy": self.proxy, "protocol": self.protocol,
            "url": f"{self.protocol}://{self.proxy}", "latency_ms": self.latency_ms,
            "http": self.http, "https": self.https, "exit_ip": self.exit_ip,
            "checks_passed": self.passed, "checks_total": self.total,
        }


class _Unrecognized(Exception):
    """The peer did not speak the protocol we tried."""


class _Refused(Exception):
    """The protocol was recognised but the request failed."""

    def __init__(self, detail: str, retry: bool = False) -> None:
        super().__init__(detail)
        self.retry = retry


class _Dead(Exception):
    """TCP connection could not be established."""


# ---------------------------------------------------------------- the link


class _Link:
    """A connection to a proxy plus the bookkeeping the handshakes share."""

    __slots__ = ("reader", "writer", "recognised", "handshake_timeout")

    def __init__(self, reader, writer, handshake_timeout: float) -> None:
        self.reader = reader
        self.writer = writer
        self.recognised = False
        self.handshake_timeout = handshake_timeout

    def _fail(self, detail: str) -> Exception:
        return _Refused(detail) if self.recognised else _Unrecognized(detail)

    async def read_exact(self, n: int) -> bytes:
        try:
            async with asyncio.timeout(self.handshake_timeout):
                return await self.reader.readexactly(n)
        except (asyncio.IncompleteReadError, TimeoutError, ConnectionError, OSError):
            raise self._fail("short read") from None

    async def read_until(self, separator: bytes) -> bytes:
        try:
            async with asyncio.timeout(self.handshake_timeout):
                return await self.reader.readuntil(separator)
        except (asyncio.IncompleteReadError, asyncio.LimitOverrunError, TimeoutError,
                ConnectionError, OSError):
            raise self._fail("no reply") from None

    async def send(self, data: bytes) -> None:
        try:
            self.writer.write(data)
            await self.writer.drain()
        except (ConnectionError, OSError):
            raise self._fail("write failed") from None


async def _socks5(link: _Link, target_ip: str, target_host: str, port: int) -> None:
    await link.send(b"\x05\x01\x00")  # one method: no authentication
    reply = await link.read_exact(2)
    if reply[0] != 5:
        raise _Unrecognized("not socks5")
    link.recognised = True
    if reply[1] != 0:
        raise _Refused("socks5 authentication required")
    if target_ip:
        request = b"\x05\x01\x00\x01" + socket.inet_aton(target_ip) + struct.pack(">H", port)
    else:
        name = target_host.encode()
        request = b"\x05\x01\x00\x03" + bytes([len(name)]) + name + struct.pack(">H", port)
    await link.send(request)
    head = await link.read_exact(4)
    if head[0] != 5:
        raise _Refused("bad socks5 reply")
    if head[1] != 0:
        raise _Refused(f"socks5 reply {head[1]}")
    atyp = head[3]
    if atyp == 1:
        await link.read_exact(4 + 2)
    elif atyp == 4:
        await link.read_exact(16 + 2)
    elif atyp == 3:
        size = (await link.read_exact(1))[0]
        await link.read_exact(size + 2)
    else:
        raise _Refused("bad socks5 address type")


async def _socks4(link: _Link, target_ip: str, port: int) -> None:
    await link.send(b"\x04\x01" + struct.pack(">H", port) + socket.inet_aton(target_ip) + b"\x00")
    reply = await link.read_exact(8)
    if reply[0] != 0 or reply[1] not in (0x5A, 0x5B, 0x5C, 0x5D):
        raise _Unrecognized("not socks4")
    link.recognised = True
    if reply[1] != 0x5A:
        raise _Refused(f"socks4 rejected ({reply[1]:#x})")


async def _http_connect(link: _Link, authority: str) -> None:
    await link.send(f"CONNECT {authority} HTTP/1.1\r\nHost: {authority}\r\n"
                    f"User-Agent: {USER_AGENT}\r\nProxy-Connection: Keep-Alive\r\n\r\n".encode())
    head = await link.read_until(b"\r\n\r\n")
    match = _STATUS_RE.match(head)
    if not match:
        raise _Unrecognized("not http")
    link.recognised = True
    if match.group(1) != b"200":
        raise _Refused(f"connect {match.group(1).decode()}")


async def _read_response(link: _Link, limit: int = 2048) -> tuple[int, bytes]:
    """Read one complete HTTP response; strict about completeness."""
    head = await link.read_until(b"\r\n\r\n")
    match = _STATUS_RE.match(head)
    if not match:
        raise link._fail("not http")
    link.recognised = True
    status = int(match.group(1))
    headers: dict[str, str] = {}
    for line in head.split(b"\r\n")[1:]:
        name, _, value = line.partition(b":")
        if value:
            headers[name.strip().lower().decode("latin-1")] = value.strip().decode("latin-1")
    if status == 407:
        raise _Refused("proxy authentication required")
    if status != 200:
        raise _Refused(f"status {status}", retry=True)
    try:
        if "chunked" in headers.get("transfer-encoding", "").lower():
            body = bytearray()
            while True:
                size = int((await link.read_until(b"\r\n")).split(b";")[0].strip() or b"0", 16)
                if size == 0:
                    return status, bytes(body)
                body += await link.read_exact(size)
                await link.read_exact(2)
                if len(body) > limit:
                    raise _Refused("body too large", retry=True)
        if "content-length" in headers:
            length = int(headers["content-length"])
            if length > limit:
                raise _Refused("body too large", retry=True)
            return status, await link.read_exact(length)
        body = bytearray()  # no length: the body ends when the connection closes
        while len(body) <= limit:
            chunk = await link.reader.read(1024)
            if not chunk:
                return status, bytes(body)
            body += chunk
        raise _Refused("body too large", retry=True)
    except ValueError:
        raise _Refused("bad framing", retry=True) from None


# -------------------------------------------------------------------- checker


class Checker:
    """Talks to one proxy at a time. Holds no per-proxy state."""

    def __init__(self, plain_judges: list[Judge], tls_judges: list[Judge], *,
                 egress: frozenset[str] = frozenset(), timeout: float = 8.0,
                 connect_timeout: float = 5.0, handshake_timeout: float = 5.0,
                 ssl_context: ssl.SSLContext | None = None, conn_limit: int = 800,
                 tcp_limit: int = 3000) -> None:
        self.plain_judges = plain_judges
        self.tls_judges = tls_judges
        self.egress = set(egress)
        self.timeout = timeout
        self.connect_timeout = connect_timeout
        self.handshake_timeout = handshake_timeout
        self.ssl_context = ssl_context or ssl.create_default_context()
        self._conn_sem = asyncio.Semaphore(conn_limit)
        self._tcp_sem = asyncio.Semaphore(tcp_limit)

    # -- stage 1 ---------------------------------------------------------

    async def tcp_alive(self, host: str, port: int) -> bool:
        async with self._tcp_sem:
            try:
                async with asyncio.timeout(self.connect_timeout):
                    _, writer = await asyncio.open_connection(host, port)
            except (OSError, TimeoutError):
                return False
            writer.transport.abort()
            return True

    # -- stage 2 ---------------------------------------------------------

    async def probe(self, host: str, port: int, protocol: str, salt: int = 0) -> Probe:
        """Check *protocol* on host:port: plain HTTP first, then TLS."""
        plain = await self._attempt(host, port, protocol, False, salt)
        if plain.status in (DEAD, UNRECOGNIZED):
            return plain
        tls = await self._attempt(host, port, protocol, True, salt) if self.tls_judges else None
        if plain.ok or (tls and tls.ok):
            best = plain if plain.ok else tls
            return Probe(OK, best.latency_ms, best.exit_ip, plain.ok, bool(tls and tls.ok))
        return Probe(FAILED, detail=plain.detail or (tls.detail if tls else ""))

    async def _attempt(self, host: str, port: int, protocol: str, tls: bool, salt: int) -> Probe:
        judges = self.tls_judges if tls else self.plain_judges
        if protocol == "socks4":
            judges = [j for j in judges if j.ip]
        if not judges:
            return Probe(FAILED, detail="no judge")
        start = stable_hash(f"{host}:{port}") + salt
        result = Probe(FAILED)
        for i in range(min(2, len(judges))):
            result = await self._once(host, port, protocol, judges[(start + i) % len(judges)], tls)
            if result.status != FAILED or not result.detail.startswith("retry:"):
                break
        return result

    async def _once(self, host: str, port: int, protocol: str, judge: Judge, tls: bool) -> Probe:
        target_port = judge.https_port if tls else judge.http_port
        authority = judge.authority(target_port)
        link: _Link | None = None
        stage = "connect"
        started = time.monotonic()
        async with self._conn_sem:
            try:
                async with asyncio.timeout(self.timeout):
                    try:
                        async with asyncio.timeout(self.connect_timeout):
                            reader, writer = await asyncio.open_connection(host, port)
                    except (OSError, TimeoutError):
                        raise _Dead() from None
                    link = _Link(reader, writer, self.handshake_timeout)
                    stage = "handshake"
                    if protocol == "socks5":
                        await _socks5(link, judge.ip, judge.host, target_port)
                    elif protocol == "socks4":
                        await _socks4(link, judge.ip, target_port)
                    elif tls:
                        await _http_connect(link, authority)
                    if tls:
                        stage = "tls"
                        await writer.start_tls(self.ssl_context, server_hostname=judge.host)
                    stage = "request"
                    target = (f"http://{authority}{judge.path}"
                              if protocol == "http" and not tls else judge.path)
                    await link.send(
                        f"GET {target} HTTP/1.1\r\nHost: {authority}\r\nUser-Agent: {USER_AGENT}\r\n"
                        f"Accept: */*\r\nConnection: close\r\n\r\n".encode())
                    _, body = await _read_response(link)
                    ip = parse_ip(body)
                    if ip is None:
                        raise _Refused("judge answered with garbage", retry=True)
                    if ip in self.egress:
                        raise _Refused("transparent proxy (leaks our IP)")
                    elapsed = round((time.monotonic() - started) * 1000)
                    return Probe(OK, elapsed, ip, http=not tls, https=tls)
            except _Dead:
                return Probe(DEAD)
            except _Unrecognized as error:
                return Probe(UNRECOGNIZED, detail=str(error))
            except _Refused as error:
                return Probe(FAILED, detail=("retry: " if error.retry else "") + str(error))
            except ssl.SSLError as error:
                return Probe(FAILED, detail=f"tls: {error.__class__.__name__}")
            except (TimeoutError, asyncio.IncompleteReadError, ConnectionError, OSError) as error:
                if stage == "connect":
                    return Probe(DEAD)
                if stage == "handshake" and not (link and link.recognised):
                    return Probe(UNRECOGNIZED, detail="handshake timeout")
                # a proxy that hangs or resets is the one at fault: do not retry elsewhere
                return Probe(FAILED, detail=error.__class__.__name__)
            finally:
                if link is not None:
                    link.writer.transport.abort()

    # -- candidate level ---------------------------------------------------

    async def check_candidate(self, proxy: str, test_mask: int, fallback_mask: int,
                              salt: int = 0) -> tuple[list[Record], int]:
        """Verify one ``ip:port``; returns ``(records, probes_run)``."""
        host, port = split_proxy(proxy)
        names = probe_order(port, test_mask)
        probes = await asyncio.gather(*(self.probe(host, port, n, salt) for n in names))
        ran = len(names)
        if (not any(p.ok for p in probes) and fallback_mask
                and all(p.status in (UNRECOGNIZED, DEAD) for p in probes)):
            rest = probe_order(port, fallback_mask)
            names = names + rest
            probes = list(probes) + list(await asyncio.gather(
                *(self.probe(host, port, n, salt + 1) for n in rest)))
            ran += len(rest)
        records = [Record(proxy, name, p.latency_ms, p.exit_ip, p.http, p.https)
                   for name, p in zip(names, probes) if p.ok]
        return records, ran


# ------------------------------------------------------------- environment


@dataclass
class Environment:
    egress: frozenset[str]
    plain: list[Judge]
    tls: list[Judge]
    verify_tls: bool = True
    notes: list[str] = field(default_factory=list)


async def _direct(session: aiohttp.ClientSession, url: str, ssl_arg) -> tuple[str | None, bool]:
    """Fetch a judge without proxy. Returns ``(ip, certificate_problem)``."""
    try:
        async with session.get(url, ssl=ssl_arg, headers={"User-Agent": USER_AGENT}) as response:
            if response.status == 200:
                return parse_ip(await response.content.read(2048)), False
    except aiohttp.ClientConnectorCertificateError:
        return None, True
    except (aiohttp.ClientError, asyncio.TimeoutError, OSError, ssl.SSLError) as error:
        return None, isinstance(error, ssl.SSLCertVerificationError)
    return None, False


async def detect_environment(judges=DEFAULT_JUDGES, *, timeout: float = 8.0,
                             ssl_context: ssl.SSLContext | None = None,
                             log: Callable[[str], None] = print) -> Environment:
    """Learn our own egress IP and which judges are reachable (and usable).

    Also resolves the judges' IPv4 addresses (SOCKS targets) and checks that TLS
    verification works on this machine; if the OS has no CA bundle the TLS check
    falls back to *unverified* TLS (and says so) instead of rejecting everything.
    """
    loop = asyncio.get_running_loop()
    resolved: list[Judge] = []
    for judge in judges:
        ip = judge.ip
        if not ip:
            try:
                ipaddress.IPv4Address(judge.host)
                ip = judge.host
            except ValueError:
                try:
                    info = await loop.getaddrinfo(judge.host, None, family=socket.AF_INET,
                                                  type=socket.SOCK_STREAM)
                    ip = info[0][4][0]
                except OSError:
                    ip = ""
        resolved.append(replace(judge, ip=ip))

    ctx = ssl_context or ssl.create_default_context()
    notes: list[str] = []
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=timeout)) as session:
        async def plain(judge: Judge):
            if not judge.http_port:
                return judge, None
            url = f"http://{judge.authority(judge.http_port)}{judge.path}"
            return judge, (await _direct(session, url, False))[0]

        async def secure(judge: Judge, context):
            if not judge.https_port:
                return judge, None, False
            url = f"https://{judge.authority(judge.https_port)}{judge.path}"
            ip, cert_problem = await _direct(session, url, context)
            return judge, ip, cert_problem

        plain_results = await asyncio.gather(*(plain(j) for j in resolved))
        tls_results = await asyncio.gather(*(secure(j, ctx) for j in resolved))
        verify = True
        if not any(ip for _, ip, _ in tls_results) and any(c for _, _, c in tls_results):
            unverified = await asyncio.gather(*(secure(j, False) for j in resolved))
            if any(ip for _, ip, _ in unverified):
                verify, tls_results = False, unverified
                notes.append("TLS certificate verification is unavailable on this machine "
                             "(no CA bundle?): HTTPS support is checked without verification")

    egress = {ip for _, ip in plain_results if ip} | {ip for _, ip, _ in tls_results if ip}
    plain_ok = [j for j, ip in plain_results if ip]
    tls_ok = [j for j, ip, _ in tls_results if ip]
    if not plain_ok:
        notes.append("no judge reachable directly: using all judges unchecked, egress IP unknown")
        plain_ok = [j for j in resolved if j.http_port]
        tls_ok = [j for j in resolved if j.https_port]
    elif not tls_ok:
        notes.append("no HTTPS judge reachable directly: TLS tunnel checks are disabled")
    for note in notes:
        log(f"  note: {note}")
    return Environment(frozenset(egress), plain_ok, tls_ok, verify, notes)


# -------------------------------------------------------------------- engine


def raise_fd_limit(wanted: int) -> int:
    """Raise RLIMIT_NOFILE towards *wanted*; returns the limit now in effect."""
    if resource is None:  # pragma: no cover
        return wanted
    soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    target = wanted if hard == resource.RLIM_INFINITY else min(wanted, hard)
    if soft < target:
        try:
            resource.setrlimit(resource.RLIMIT_NOFILE, (target, hard))
            soft = target
        except (ValueError, OSError):  # pragma: no cover
            pass
    return soft


@dataclass
class VerifyResult:
    records: list[Record]
    stats: dict


class Validator:
    def __init__(self, checker: Checker, *, workers: int = 3000,
                 log: Callable[[str], None] = print, quiet: bool = False) -> None:
        self.checker = checker
        self.workers = workers
        self.log = log
        self.quiet = quiet
        self.stats = {"candidates": 0, "tcp_alive": 0, "probes": 0, "first_pass": 0,
                      "skipped_deadline": 0, "fallback_used": 0}

    async def _run(self, items, handler, deadline: float | None, label: str) -> None:
        """Feed *items* to a bounded pool of workers; stop feeding at *deadline*."""
        items = list(items)
        total = len(items)
        queue: asyncio.Queue = asyncio.Queue(maxsize=self.workers * 2)
        done = 0
        started = time.monotonic()

        async def feeder() -> None:
            for index, item in enumerate(items):
                if deadline and time.monotonic() > deadline:
                    self.stats["skipped_deadline"] += total - index
                    break
                await queue.put(item)
            for _ in range(workers):
                await queue.put(None)

        async def worker() -> None:
            nonlocal done
            while True:
                item = await queue.get()
                if item is None:
                    return
                try:
                    await handler(item)
                except Exception as error:  # a bug in one probe must not kill the run
                    if not self.quiet:
                        self.log(f"  internal error on {item!r}: {error!r}")
                done += 1

        async def ticker() -> None:
            while True:
                await asyncio.sleep(20)
                if not self.quiet:
                    elapsed = time.monotonic() - started
                    rate = done / elapsed if elapsed else 0
                    eta = (total - done) / rate if rate else 0
                    self.log(f"  {label}: {done:,}/{total:,} ({done / max(total, 1):.0%}) "
                             f"| tcp alive {self.stats['tcp_alive']:,} | first-pass ok "
                             f"{self.stats['first_pass']:,} | {rate:,.0f}/s | ~{eta / 60:.1f} min left")

        workers = min(self.workers, max(total, 1))
        tick = asyncio.create_task(ticker())
        try:
            await asyncio.gather(feeder(), *(worker() for _ in range(workers)))
        finally:
            tick.cancel()

    async def first_pass(self, candidates: list[tuple[str, int, int]], *,
                         deadline: float | None = None) -> list[Record]:
        found: list[Record] = []
        self.stats["candidates"] = len(candidates)

        async def handle(item) -> None:
            proxy, test_mask, fallback_mask = item
            host, port = split_proxy(proxy)
            if not await self.checker.tcp_alive(host, port):
                return
            self.stats["tcp_alive"] += 1
            records, ran = await self.checker.check_candidate(proxy, test_mask, fallback_mask)
            self.stats["probes"] += ran
            if records:
                self.stats["first_pass"] += 1
                found.extend(records)

        await self._run(candidates, handle, deadline, "verify")
        return found

    async def confirm(self, records: list[Record], rounds: int, *,
                      deadline: float | None = None) -> None:
        for number in range(1, rounds + 1):
            if deadline and time.monotonic() > deadline:
                break

            async def handle(record: Record, _salt=number * 7919) -> None:
                host, port = split_proxy(record.proxy)
                record.update(await self.checker.probe(host, port, record.protocol, _salt))

            await self._run(records, handle, deadline, f"confirm {number}/{rounds}")
            if not self.quiet:
                self.log(f"  confirmation round {number}: "
                         f"{sum(r.alive for r in records):,}/{len(records):,} still alive")

    async def detect_interception(self, targets=CONTROL_TARGETS) -> set[str]:
        """Probe addresses where no proxy can exist (RFC 5737 TEST-NET).

        If something answers, the network intercepts outgoing connections and
        would make *every* candidate look alive. The interceptor's IP is then
        added to the egress blacklist so those false positives are rejected.
        """
        probes = await asyncio.gather(*(
            self.checker.probe(host, port, protocol)
            for host, port in targets for protocol in ("http", "socks5", "socks4")))
        leaked = {p.exit_ip for p in probes if p.ok and p.exit_ip}
        if leaked:
            self.checker.egress |= leaked
            self.log(f"  warning: network intercepts connections (control probes answered via "
                     f"{', '.join(sorted(leaked))}); those exit IPs are now rejected")
        return leaked

    async def verify(self, candidates: list[tuple[str, int, int]], *, rounds: int = 2,
                     time_budget: float = 0.0, controls: tuple = CONTROL_TARGETS) -> VerifyResult:
        """Run the full pipeline. ``time_budget`` (seconds, 0 = unlimited)."""
        started = time.monotonic()
        if controls:
            self.stats["intercepted_by"] = sorted(await self.detect_interception(controls))
        first_deadline = started + 0.7 * time_budget if time_budget else None
        final_deadline = started + time_budget if time_budget else None
        records = await self.first_pass(candidates, deadline=first_deadline)
        await self.confirm(records, rounds, deadline=final_deadline)
        alive = [r for r in records if r.alive]
        self.stats.update(live=len(alive), rounds=rounds,
                          seconds=round(time.monotonic() - started),
                          budget_hit=bool(self.stats["skipped_deadline"]))
        return VerifyResult(alive, self.stats)

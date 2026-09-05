from __future__ import annotations

import asyncio
import json
import re
import ssl
import time

import aiohttp
from aiohttp_socks import ProxyConnector, ProxyType

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

IP_RE = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")

JUDGES = [
    "http://api.ipify.org/",
    "http://icanhazip.com/",
    "http://checkip.amazonaws.com/",
    "http://ipinfo.io/ip",
    "http://ifconfig.me/ip",
    "http://ip-api.com/line/?fields=query",
]
HTTPS_JUDGE = "https://api.ipify.org/"

CONTROL_TARGETS = ["192.0.2.1:9999", "198.51.100.7:8080", "203.0.113.9:3128"]

PROTOCOL_PRIORITY = ["http", "socks5", "socks4"]

_LAX_SSL = ssl.create_default_context()
_LAX_SSL.check_hostname = False
_LAX_SSL.verify_mode = ssl.CERT_NONE


def _connector(protocol: str, host: str, port: int, ssl_ctx):
    if protocol == "http":
        return aiohttp.TCPConnector(ssl=ssl_ctx, limit=2, force_close=True)
    proxy_type = ProxyType.SOCKS5 if protocol == "socks5" else ProxyType.SOCKS4
    return ProxyConnector(
        proxy_type=proxy_type, host=host, port=port, rdns=True,
        limit=2, force_close=True, ssl=ssl_ctx,
    )


async def probe(proxy: str, protocol: str, judge: str, timeout: float):
    host, _, port = proxy.rpartition(":")
    started = time.perf_counter()
    try:
        connector = _connector(protocol, host, int(port), False)
        kwargs = {"proxy": f"http://{proxy}"} if protocol == "http" else {}
        async with aiohttp.ClientSession(
            connector=connector, headers={"User-Agent": USER_AGENT}
        ) as session:
            async with session.get(
                judge, timeout=aiohttp.ClientTimeout(total=timeout), **kwargs
            ) as response:
                if response.status != 200:
                    return None
                body = (await response.text(errors="ignore")).strip()
    except Exception:
        return None

    if not body or len(body) > 200:
        return None
    match = IP_RE.search(body)
    if not match:
        return None
    return protocol, round((time.perf_counter() - started) * 1000), match.group(0)


async def supports_https(proxy: str, protocol: str, timeout: float = 10.0) -> bool:
    host, _, port = proxy.rpartition(":")
    try:
        connector = _connector(protocol, host, int(port), _LAX_SSL)
        kwargs = {"proxy": f"http://{proxy}"} if protocol == "http" else {}
        async with aiohttp.ClientSession(connector=connector) as session:
            async with session.get(
                HTTPS_JUDGE, timeout=aiohttp.ClientTimeout(total=timeout), **kwargs
            ) as response:
                return response.status == 200
    except Exception:
        return False


async def detect_egress() -> tuple[set[str], int]:
    ips: set[str] = set()

    async def direct(url, session):
        try:
            async with session.get(
                url, timeout=aiohttp.ClientTimeout(total=15)
            ) as response:
                match = IP_RE.search((await response.text(errors="ignore"))[:200])
                return match.group(0) if match else None
        except Exception:
            return None

    async with aiohttp.ClientSession() as session:
        for result in await asyncio.gather(*(direct(u, session) for u in JUDGES)):
            if result:
                ips.add(result)

    control_hits = 0
    probes = await asyncio.gather(
        *(probe(target, "http", JUDGES[i % len(JUDGES)], 10.0)
          for i, target in enumerate(CONTROL_TARGETS * 2))
    )
    for result in probes:
        if result:
            control_hits += 1
            ips.add(result[2])
    return ips, control_hits


class Validator:
    def __init__(self, egress_ips: set[str], timeout: float = 8.0,
                 concurrency: int = 1000, check_https: bool = True):
        self.egress_ips = set(egress_ips)
        self.timeout = timeout
        self.concurrency = concurrency
        self.check_https = check_https

    def _accept(self, result) -> bool:
        return result is not None and result[2] not in self.egress_ips

    async def _check(self, proxy, protocols, semaphore, salt, state, results, sink):
        async with semaphore:
            candidates = [p for p in PROTOCOL_PRIORITY if p in protocols] or ["http"]
            probes = await asyncio.gather(*(
                probe(proxy, protocol,
                      JUDGES[(hash(proxy) + salt + i) % len(JUDGES)], self.timeout)
                for i, protocol in enumerate(candidates)
            ))
            accepted = [r for r in probes if self._accept(r)]
            if accepted:
                accepted.sort(key=lambda r: PROTOCOL_PRIORITY.index(r[0]))
                protocol, latency, exit_ip = accepted[0]
                https = (await supports_https(proxy, protocol)
                         if self.check_https else False)
                record = {
                    "proxy": proxy,
                    "protocol": protocol,
                    "latency_ms": latency,
                    "exit_ip": exit_ip,
                    "https": https,
                    "protocols": sorted({r[0] for r in accepted}),
                }
                results[proxy] = record
                if sink:
                    sink.write(json.dumps(record) + "\n")
                    sink.flush()

        state["done"] += 1
        if state["done"] % 5000 == 0:
            elapsed = time.time() - state["started"]
            rate = state["done"] / max(elapsed, 0.1)
            remaining = (state["total"] - state["done"]) / max(rate, 0.1) / 60
            print(
                f"  {state['done']}/{state['total']} checked "
                f"| live={len(results)} | {rate:.0f}/s | eta {remaining:.1f}m",
                flush=True,
            )

    async def run(self, candidates: dict[str, list[str]], salt: int = 0,
                  sink_path: str | None = None) -> dict[str, dict]:
        state = {"done": 0, "total": len(candidates), "started": time.time()}
        semaphore = asyncio.Semaphore(self.concurrency)
        results: dict[str, dict] = {}
        sink = open(sink_path, "w") if sink_path else None
        try:
            await asyncio.gather(*(
                self._check(proxy, set(protocols), semaphore, salt,
                            state, results, sink)
                for proxy, protocols in candidates.items()
            ))
        finally:
            if sink:
                sink.close()
        print(
            f"  pass complete in {time.time() - state['started']:.0f}s: "
            f"{len(results)} verified",
            flush=True,
        )
        return results

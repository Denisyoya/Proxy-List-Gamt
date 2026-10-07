"""Small helpers shared by every stage of the pipeline."""
from __future__ import annotations

import ipaddress
import re
import zlib
from datetime import datetime, timezone
from functools import lru_cache

# Protocol bit-flags. A candidate carries a mask of the protocols it should be
# tested with; ``0`` means "the source did not say".
HTTP = 1
SOCKS4 = 2
SOCKS5 = 4
ALL_MASK = HTTP | SOCKS4 | SOCKS5

PROTOCOL_BITS = {"http": HTTP, "socks4": SOCKS4, "socks5": SOCKS5}
PROTOCOL_NAMES = {bit: name for name, bit in PROTOCOL_BITS.items()}

# Order used when publishing and when breaking ties.
PROTOCOL_ORDER = ("http", "socks5", "socks4")

# Ports where a SOCKS server is far more likely than an HTTP proxy. Used only
# to decide which protocol to probe first, never to decide the outcome.
SOCKS_PORTS = frozenset({
    1080, 1081, 1085, 1086, 4145, 4153, 5678, 9050, 9051, 9999, 10800, 10801,
    10808, 20000, 33080, 45454, 7000, 7777,
})

IPV4_OCTET = r"(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)"
IPV4 = rf"(?:{IPV4_OCTET}\.){{3}}{IPV4_OCTET}"
IPV4_RE = re.compile(rf"^{IPV4}$")


def mask_from_hint(hint: str) -> int:
    """Translate a source hint (``http``, ``socks``, ``mixed`` ...) to a mask."""
    hint = (hint or "").strip().lower()
    if hint in ("http", "https", "connect", "ssl"):
        return HTTP
    if hint in ("socks4", "socks4a"):
        return SOCKS4
    if hint in ("socks5", "socks5h"):
        return SOCKS5
    if hint == "socks":
        return SOCKS4 | SOCKS5
    return 0


def mask_from_token(token: str) -> int:
    """Translate a free-form protocol word found inside a payload."""
    token = (token or "").strip().lower().replace(" ", "").replace("_", "")
    if token in ("http", "https", "connect", "ssl", "web"):
        return HTTP
    if token in ("socks4", "socks4a", "socksv4", "socks4/4a"):
        return SOCKS4
    if token in ("socks5", "socks5h", "socksv5"):
        return SOCKS5
    if token in ("socks", "sock"):
        return SOCKS4 | SOCKS5
    return 0


def protocols_of(mask: int) -> list[str]:
    """Names for every bit set in *mask*, in publication order."""
    return [name for name in PROTOCOL_ORDER if mask & PROTOCOL_BITS[name]]


def probe_order(port: int, mask: int) -> list[str]:
    """Protocols to try for *mask*, most likely first for this port."""
    names = protocols_of(mask or ALL_MASK)
    if port in SOCKS_PORTS:
        names.sort(key=lambda n: 0 if n.startswith("socks") else 1)
    else:
        names.sort(key=lambda n: 0 if n == "http" else 1)
    return names


@lru_cache(maxsize=1 << 20)
def is_public_ipv4(ip: str) -> bool:
    """True when *ip* is a routable unicast IPv4 address.

    Rejects private, loopback, link-local, CGNAT, documentation, multicast and
    reserved ranges as well as ``x.x.x.0`` network addresses, which are never
    useful proxy endpoints.
    """
    try:
        address = ipaddress.IPv4Address(ip)
    except ValueError:
        return False
    # ``is_global`` alone still lets multicast (224/4) through.
    return (address.is_global and not address.is_multicast and not address.is_reserved
            and not ip.endswith(".0"))


def valid_port(port: int) -> bool:
    return 0 < port < 65536


def split_proxy(proxy: str) -> tuple[str, int]:
    host, _, port = proxy.rpartition(":")
    return host, int(port)


def stable_hash(text: str) -> int:
    """Process-independent hash (``hash()`` is salted per interpreter run)."""
    return zlib.crc32(text.encode())


def utc_stamp(moment: datetime | None = None) -> str:
    moment = moment or datetime.now(timezone.utc)
    return moment.strftime("%Y-%m-%d %H:%M:%S UTC")

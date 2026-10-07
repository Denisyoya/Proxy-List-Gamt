"""Turn whatever a proxy source returns into ``{"ip:port": protocol_mask}``.

Real-world sources publish proxies in many shapes: plain ``ip:port`` lists,
``scheme://ip:port`` lists, JSON arrays with different schemas, NDJSON, CSV
with a header, HTML tables, XML, and obfuscated web pages. ``parse_payload``
handles all of them with one entry point.

The returned mask says which protocols the source claims for the proxy
(``common.HTTP | SOCKS4 | SOCKS5``). ``0`` means the source did not say, and the
validator will try every protocol for it.
"""
from __future__ import annotations

import base64
import binascii
import csv
import html
import io
import json
import re
from urllib.parse import unquote

from common import (
    HTTP, SOCKS4, SOCKS5, IPV4, IPV4_RE, is_public_ipv4, mask_from_hint,
    mask_from_token, valid_port,
)

_SCHEME_MASK = {
    "http": HTTP, "https": HTTP, "socks4": SOCKS4, "socks4a": SOCKS4,
    "socks5": SOCKS5, "socks5h": SOCKS5,
}

# ``[scheme://]ip<sep>port`` where <sep> is ':' or whitespace/table-cell noise.
#  - not preceded by '@' or '/' ... : skips ``user:pass@ip:port`` entries and
#    IPs embedded in URLs (those proxies need credentials, so they are useless).
#  - not followed by ':word' : skips ``ip:port:user:pass`` entries.
_PROXY_RE = re.compile(
    rf"(?<![\w.@/\-])(?:(?P<scheme>https?|socks4a?|socks5h?)://)?"
    rf"(?P<ip>{IPV4})[\s:,;|\uff1a\uff0c\uff1b]{{1,6}}(?P<port>\d{{2,5}})"
    rf"(?![\d.]|:[^\s/])",
    re.IGNORECASE,
)
# Protocol word in the text that follows an entry (``... socks5 ...``).
_TOKEN_RE = re.compile(
    r"(?<![a-z0-9])(socks\s?5h?|socks\s?4a?|socks|https?)(?![a-z0-9])", re.IGNORECASE
)

_IP_KEYS = ("ip", "ip_address", "ipaddress", "address", "addr", "host",
            "hostname", "server", "proxy_host", "proxyhost")
_PORT_KEYS = ("port", "proxy_port", "proxyport", "port_number")
_COMBINED_KEYS = ("proxy", "url", "address", "ip_port", "ipport", "proxy_address",
                  "proxyaddress", "host_port", "endpoint", "server", "link")
_PROTOCOL_KEYS = ("protocol", "protocols", "type", "types", "scheme", "proxy_type",
                  "proxytype", "kind", "proto", "version")
_FLAG_KEYS = (("socks5", SOCKS5), ("socks4", SOCKS4), ("http", HTTP),
              ("https", HTTP), ("ssl", HTTP))
_CREDENTIAL_KEYS = ("username", "user", "login", "password", "pass")

# ---------------------------------------------------------------- helpers


def _to_text(payload: bytes | str) -> str:
    if isinstance(payload, str):
        return payload
    if payload.startswith(b"\xef\xbb\xbf"):
        payload = payload[3:]
    if payload.startswith((b"\xff\xfe", b"\xfe\xff")):
        return payload.decode("utf-16", "replace")
    # latin-1 never fails and keeps every ASCII byte (all we care about) intact.
    return payload.decode("latin-1")


def _add(found: dict[str, int], ip: str, port: int, mask: int, allow_private: bool) -> None:
    if not valid_port(port):
        return
    if not allow_private and not is_public_ipv4(ip):
        return
    key = f"{ip}:{port}"
    found[key] = found.get(key, 0) | mask


def _truthy(value: object) -> bool:
    if value is True:
        return True
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return value == 1
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "y")
    return False


def _mask_from_value(value: object) -> int:
    if isinstance(value, str):
        mask = mask_from_token(value)
        if not mask and ("," in value or "/" in value or ";" in value):
            for part in re.split(r"[,/;|]", value):
                mask |= mask_from_token(part)
        return mask
    if isinstance(value, (list, tuple)):
        mask = 0
        for item in value:
            mask |= _mask_from_value(item)
        return mask
    return 0


# ------------------------------------------------------- structured data


def _from_mapping(raw: dict, hint_mask: int) -> tuple[str, int, int] | None:
    """Extract ``(ip, port, mask)`` from one JSON/CSV record, if it is a proxy."""
    low = {k.lower(): v for k, v in raw.items() if isinstance(k, str)}
    if any(low.get(k) for k in _CREDENTIAL_KEYS):
        return None  # needs authentication

    ip = None
    for key in _IP_KEYS:
        value = low.get(key)
        if isinstance(value, str) and IPV4_RE.match(value.strip()):
            ip = value.strip()
            break
    port = None
    for key in _PORT_KEYS:
        value = low.get(key)
        if isinstance(value, bool):
            continue
        if isinstance(value, int):
            port = value
        elif isinstance(value, str) and value.strip().isdigit():
            port = int(value.strip())
        if port:
            break

    scheme_mask = 0
    if ip is None or port is None:
        for key in _COMBINED_KEYS:
            value = low.get(key)
            if isinstance(value, str):
                match = _PROXY_RE.search(value)
                if match:
                    ip, port = match.group("ip"), int(match.group("port"))
                    if match.group("scheme"):
                        scheme_mask = _SCHEME_MASK[match.group("scheme").lower()]
                    break
    if ip is None or port is None:
        return None

    mask = 0
    for key in _PROTOCOL_KEYS:
        mask |= _mask_from_value(low.get(key))
    mask = mask or scheme_mask
    if not mask:
        for key, bit in _FLAG_KEYS:
            if _truthy(low.get(key)):
                mask |= bit
    return ip, port, mask or hint_mask


def _loads_json(text: str) -> object | None:
    try:
        return json.loads(text)
    except ValueError:
        pass
    lines = [line for line in text.splitlines() if line.strip()]
    if len(lines) > 1 and lines[0].lstrip().startswith("{"):  # NDJSON
        records = []
        for line in lines:
            try:
                records.append(json.loads(line))
            except ValueError:
                continue
        return records or None
    return None


def _walk_json(root: object, hint_mask: int, found: dict[str, int],
               allow_private: bool, max_nodes: int = 3_000_000) -> None:
    stack = [root]
    seen = 0
    while stack:
        node = stack.pop()
        seen += 1
        if seen > max_nodes:
            return
        if isinstance(node, dict):
            record = _from_mapping(node, hint_mask)
            if record:
                _add(found, record[0], record[1], record[2], allow_private)
                continue  # nested dicts (geo, asn ...) are metadata
            for value in node.values():
                if isinstance(value, (dict, list)):
                    stack.append(value)
                elif isinstance(value, str) and 7 <= len(value) <= 5_000_000:
                    _scan_text(value, hint_mask, found, allow_private)
        elif isinstance(node, list):
            for item in node:
                if isinstance(item, (dict, list)):
                    stack.append(item)
                elif isinstance(item, str) and len(item) < 80:
                    match = _PROXY_RE.search(item)
                    if match:
                        mask = (_SCHEME_MASK[match.group("scheme").lower()]
                                if match.group("scheme") else hint_mask)
                        _add(found, match.group("ip"), int(match.group("port")),
                             mask, allow_private)


def _parse_csv(text: str, hint_mask: int, found: dict[str, int], allow_private: bool) -> bool:
    """Parse CSV/TSV/``;``-separated data that starts with a header row."""
    first = next((line for line in text.splitlines()[:5] if line.strip()), "")
    if not first or len(first) > 2000:
        return False
    delimiter = max(",;\t|", key=first.count)
    if first.count(delimiter) < 1:
        return False
    header = {h.strip().strip("\"'").lower() for h in first.split(delimiter)}
    if not (header & set(_IP_KEYS + _COMBINED_KEYS)):
        return False
    if not (header & ({"port"} | set(_COMBINED_KEYS))):
        return False
    reader = csv.DictReader(io.StringIO(text), delimiter=delimiter)
    try:
        for row in reader:
            record = _from_mapping(row, hint_mask)
            if record:
                _add(found, record[0], record[1], record[2], allow_private)
    except csv.Error:
        pass
    return True


# ------------------------------------------------------- text / html


_TAG_BREAK_RE = re.compile(r"(?i)</?(?:tr|li|p|div|br|table|tbody|thead|ul|ol|h[1-6]|pre)\b[^>]*>")
_TAG_RE = re.compile(r"<[^>]{0,500}>")
# Obfuscation used by popular proxy sites: base64 in script calls (free-proxy.cz,
# proxy-list.org) and percent-encoded markup (freeproxylists.net).
_B64_CALL_RE = re.compile(
    r"""(?:document\.write\(\s*)?(?:Base64\.decode|atob|window\.atob)\(\s*["']([A-Za-z0-9+/=]{8,})["']"""
    r"""\s*\)\s*\)?\s*;?""")
_PROXY_FN_RE = re.compile(r"""\bProxy\(\s*["']([A-Za-z0-9+/=]{8,})["']\s*\)""")
_IPDECODE_RE = re.compile(r"""IPDecode\(\s*["']([%\w.~\-]+)["']\s*\)""")
# ``"ip":"1.2.3.4","port":"80"`` fragments inside script blocks (kuaidaili ...)
_FRAG_IP_FIRST = re.compile(
    rf"""["']?(?:ip|host|address|ip_address)["']?\s*:\s*["'](?P<ip>{IPV4})["']\s*,[^{{}}]{{0,300}}?"""
    rf"""["']?port["']?\s*:\s*["']?(?P<port>\d{{2,5}})""", re.IGNORECASE)
_FRAG_PORT_FIRST = re.compile(
    rf"""["']?port["']?\s*:\s*["']?(?P<port>\d{{2,5}})["']?\s*,[^{{}}]{{0,300}}?"""
    rf"""["']?(?:ip|host|address|ip_address)["']?\s*:\s*["'](?P<ip>{IPV4})["']""", re.IGNORECASE)


def _decode_b64(blob: str) -> str | None:
    try:
        raw = base64.b64decode(blob + "=" * (-len(blob) % 4), validate=False)
    except (binascii.Error, ValueError):
        return None
    try:
        text = raw.decode("ascii")
    except UnicodeDecodeError:
        return None
    return text if text.isprintable() and len(text) <= 120 else None


def _deobfuscate(text: str) -> str:
    def b64(match: re.Match) -> str:
        decoded = _decode_b64(match.group(1))
        return f" {decoded} " if decoded else match.group(0)

    if "Base64" in text or "atob" in text:
        text = _B64_CALL_RE.sub(b64, text)
    if "Proxy(" in text:
        text = _PROXY_FN_RE.sub(b64, text)
    if "IPDecode" in text:
        text = _IPDECODE_RE.sub(lambda m: f" {unquote(m.group(1))} ", text)
    return text


def _preprocess(text: str) -> str:
    """Flatten markup so table cells of a row end up on one line."""
    if "&" in text and ("&#" in text or "&nbsp;" in text or "&colon;" in text):
        text = html.unescape(text)
    if "<" in text and ("</" in text or "<br" in text.lower()):
        text = _deobfuscate(text)
        text = _TAG_BREAK_RE.sub("\n", text)
        text = _TAG_RE.sub(" ", text)
    return text


def _scan_text(text: str, hint_mask: int, found: dict[str, int], allow_private: bool) -> None:
    previous = None
    for match in _PROXY_RE.finditer(text):
        if previous is not None:
            _emit(text, previous, match.start(), hint_mask, found, allow_private)
        previous = match
    if previous is not None:
        _emit(text, previous, len(text), hint_mask, found, allow_private)


def _emit(text: str, match: re.Match, context_end: int, hint_mask: int,
          found: dict[str, int], allow_private: bool) -> None:
    scheme = match.group("scheme")
    if scheme:
        mask = _SCHEME_MASK[scheme.lower()]
    else:
        mask = hint_mask
        context = text[match.end():min(context_end, match.end() + 100)]
        if context.strip():  # plain ``ip:port`` lists skip the regex entirely
            token = _TOKEN_RE.search(context)
            if token:
                mask = mask_from_token(token.group(1))
    _add(found, match.group("ip"), int(match.group("port")), mask, allow_private)


def _scan_fragments(text: str, hint_mask: int, found: dict[str, int], allow_private: bool) -> None:
    lowered = text if len(text) < 2_000_000 else text[:2_000_000]
    if "port" not in lowered.lower():
        return
    for regex in (_FRAG_IP_FIRST, _FRAG_PORT_FIRST):
        for match in regex.finditer(text):
            _add(found, match.group("ip"), int(match.group("port")), hint_mask, allow_private)


# ------------------------------------------------------------ entry point


def parse_payload(payload: bytes | str, hint: str = "mixed", *,
                  allow_private: bool = False) -> dict[str, int]:
    """Extract every unauthenticated ``ip:port`` from *payload*.

    ``hint`` is the protocol the source claims for entries that do not say
    anything themselves (``http``, ``socks4``, ``socks5``, ``socks``, ``mixed``).
    """
    hint_mask = mask_from_hint(hint)
    text = _to_text(payload)
    found: dict[str, int] = {}

    body = text.lstrip()
    if body[:1] in ("[", "{"):
        data = _loads_json(body)
        if data is not None:
            _walk_json(data, hint_mask, found, allow_private)
            if found:
                return found
    elif _parse_csv(body, hint_mask, found, allow_private) and found:
        return found

    text = _preprocess(text)
    _scan_text(text, hint_mask, found, allow_private)
    if '"' in text or "'" in text:
        _scan_fragments(text, hint_mask, found, allow_private)
    return found

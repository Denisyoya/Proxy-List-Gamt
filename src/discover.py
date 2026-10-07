"""GitHub source discovery.

Finds repositories that publish proxy lists, looks at their file tree (one API
call per repository) and registers the files that look like proxy lists in
``sources/discovered.txt``. The registry is capped at 50,000 sources and grows a
little on every run; sources that keep failing are pruned again by the health
tracker, so the registry converges on lists that actually produce proxies.

Efficiency rules
----------------
Only about one repository in ten that matches a proxy search really publishes
lists: the rest are proxy *software*, VPN configurations and promotional or
random-named spam. So

* repositories are scored from their metadata first and the best are scanned
  first; spam, ``*.github.io`` sites and software are skipped;
* modern list repositories publish the *same* data many times over (TXT, CSV and
  JSON, per country, per protocol), so per repository we prefer ``.txt`` over
  ``.csv``/``.json``, rank protocol / ``all`` / ``proxies`` files first, push
  per-country and archive slices to the bottom and keep at most ``per_repo``.

Usage: ``python src/discover.py --help``
"""
from __future__ import annotations

import argparse
import asyncio
import os
import re
import sys
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path, PurePosixPath

import aiohttp

from sources import MAX_DISCOVERED, github_urls

API = "https://api.github.com"

# ---------------------------------------------------------------- queries

QUERIES = (
    # topics used by list repositories
    "topic:proxy-list", "topic:free-proxy-list", "topic:free-proxy", "topic:proxylist",
    "topic:proxies", "topic:proxy-scraper", "topic:open-proxy", "topic:public-proxy",
    "topic:fresh-proxy", "topic:socks5-proxy", "topic:socks4", "topic:https-proxy",
    "topic:proxy-checker", "topic:proxy-pool",
    # keywords
    '"proxy list" in:name,description', "proxylist in:name", "proxies in:name",
    '"free proxy" in:name,description', '"socks5" list in:name,description',
    '"working proxies" in:name,description',
    # other languages
    "прокси in:name,description", "代理 in:name,description", "代理IP in:name,description",
    "プロキシ in:name,description", "프록시 in:name,description",
    "lista proxy in:name,description", "daftar proxy in:name,description",
    "proxy gratis in:name,description", "proxy grátis in:name,description",
    "proxy gratuit in:name,description", "kostenlose proxy in:name,description",
    "proxy liste in:name,description", "vekil in:name,description",
    "پروکسی in:name,description", "بروكسي in:name,description",
    "proxy miễn phí in:name,description",
)

# ------------------------------------------------------------ file choice

_EXT_SCORE = {"txt": 40, "list": 38, "lst": 38, "json": 38, "csv": 30, "": 18}
_BAD_DIRS = {"node_modules", "vendor", "test", "tests", "docs", "doc", ".github", "src", "lib",
             "scripts", "example", "examples", "venv", ".venv", "__pycache__", "dist", "build",
             "assets", "static", "images", "img", "css", "js", "fonts", "locales", "i18n",
             "badges", "badge", "shields"}
_SLICE_DIRS = {"countries", "country", "by-country", "bycountry", "by_country", "geo", "asn",
               "cities", "city", "regions", "region", "continents", "continent", "by-asn", "isp",
               "anonymity", "latency", "stability", "quality", "network", "ports", "port",
               "recent", "fresh", "elite", "anonymous", "stable", "fast"}
_ARCHIVE_DIRS = {"archive", "archives", "history", "old", "backup", "backups", "previous",
                 "snapshots", "daily", "dated", "past"}
_EXCLUDED_STEM = re.compile(
    r"(?:^|[^a-z])(readme|licen[sc]e|changelog|contributing|requirements?|package(?:-lock)?|"
    r"composer|pom|setup|manifest|robots|sitemap|user-?agents?|cookies?|passwords?|wordlists?|"
    r"combos?|domains?|subdomains?|blacklist|whitelist|blocklist|allowlist|dockerfile|makefile|"
    r"funding|security|stats|statistics|summary|metadata|index|config|settings|tsconfig|hosts|"
    r"adblock|filters?|dns|geoip|asn|cidr|ranges?|countries|country-?codes?|emails?|urls?|"
    r"links?|keywords?|names?|users?|accounts?)(?:$|[^a-z])")
_PROTO_WORDS = {"http", "https", "socks", "socks4", "socks5", "socks4a", "socks5h", "ssl", "connect"}
_GOOD_WORDS = {"all", "alive", "live", "working", "valid", "verified", "checked", "active", "good",
               "latest", "raw", "list", "lists", "ips", "ip", "data", "result", "results",
               "output", "fresh", "new", "mixed", "proxylist", "allproxy"}
MIN_SCORE = 56
MIN_FILE_BYTES = 60
MAX_FILE_BYTES = 30 * 1024 * 1024


def _tokens(text: str) -> list[str]:
    return [t for t in re.split(r"[^a-z0-9]+", text.lower()) if t]


def hint_for(path: str) -> str:
    """Protocol hint implied by a file path (``socks5/data.txt`` -> socks5)."""
    tokens = set(_tokens(path))
    if tokens & {"socks5", "socks5h"}:
        return "socks5"
    if tokens & {"socks4", "socks4a"}:
        return "socks4"
    if "socks" in tokens:
        return "socks"
    if tokens & {"http", "https", "ssl", "connect"}:
        return "http"
    return "mixed"


def score_file(path: str, size: int) -> int | None:
    """How likely *path* is a proxy list worth fetching (``None``: never)."""
    pure = PurePosixPath(path)
    name = pure.name.lower()
    stem, _, ext = name.rpartition(".") if "." in name else (name, "", "")
    if ext not in _EXT_SCORE:
        return None
    dirs = [d.lower() for d in pure.parts[:-1]]
    if any(d in _BAD_DIRS or d.startswith(".") for d in dirs):
        return None
    if _EXCLUDED_STEM.search(stem) or not (MIN_FILE_BYTES <= size <= MAX_FILE_BYTES):
        return None
    words = _tokens(stem)
    dir_words = [w for d in dirs for w in _tokens(d)]
    score = _EXT_SCORE[ext]
    if _PROTO_WORDS & set(words + dir_words):
        score += 22
    if any(w.startswith(("proxy", "proxies")) for w in words + dir_words):
        score += 18
    if _GOOD_WORDS & set(words):
        score += 10
    if "all" in words or "allproxy" in words:
        score += 8
    if any(d in _SLICE_DIRS for d in dirs) or (_SLICE_DIRS & set(words) and "all" not in words):
        score -= 45
    if any(d in _ARCHIVE_DIRS for d in dirs):
        score -= 60
    score -= min(len(dirs) * 4, 12)
    return score


def select_files(entries: list[dict], per_repo: int = 10) -> list[tuple[str, str]]:
    """Pick up to *per_repo* ``(hint, path)`` pairs from a git tree listing."""
    best: dict[tuple[str, str], tuple[int, str]] = {}
    for entry in entries:
        if entry.get("type") != "blob":
            continue
        path = entry["path"]
        score = score_file(path, int(entry.get("size") or 0))
        if score is None or score < MIN_SCORE:
            continue
        pure = PurePosixPath(path)
        stem = pure.name.lower().rpartition(".")[0] or pure.name.lower()
        slot = (str(pure.parent), stem)  # same dir + name = same data, other format
        if slot not in best or score > best[slot][0]:
            best[slot] = (score, path)
    ranked = sorted(best.values(), key=lambda item: (-item[0], item[1]))
    texts = [item for item in ranked if item[1].lower().endswith((".txt", ".list", ".lst"))]
    if len(texts) >= 2:  # JSON/CSV next to several TXT lists is the same data again
        ranked = texts
    return [(hint_for(path), path) for _, path in ranked[:per_repo]]


# ------------------------------------------------------------ repo choice

_LIST_TOPICS = {"proxy-list", "proxylist", "free-proxy-list", "free-proxy", "proxies",
                "public-proxy", "fresh-proxy", "open-proxy", "proxy-scraper", "socks5-proxy",
                "socks4", "socks5", "http-proxy", "https-proxy", "proxy-checker"}
_SPAM = re.compile(r"residential|datacenter|isp[- ]proxies|\bbuy\b|pricing|\bprice\b|cheap|premium|"
                   r"selection|rotating|v2ray|vmess|vless|clash|trojan|shadowsocks|subscription|"
                   r"airport|\bnodes?\b|mtproto|telegram", re.IGNORECASE)
_SOFTWARE = re.compile(r"\b(server|client|library|framework|sdk|wrapper|extension|plugin|bot|docker|"
                       r"nginx|golang|kubernetes|reverse proxy|load.?balanc|middleware|tunnel)\b",
                       re.IGNORECASE)
_LISTY = re.compile(r"\b(lists?|fresh|daily|hourly|updated?|every|checked|working|alive|live|"
                    r"scrap\w*|collection|free|public|verified)\b", re.IGNORECASE)


def looks_random(name: str) -> bool:
    """``LxDOwCAQ``-style spam repositories: 8 letters, jumbled case."""
    if not re.fullmatch(r"[A-Za-z]{6,10}", name):
        return False
    flips = sum(1 for a, b in zip(name, name[1:]) if a.islower() != b.islower())
    return flips >= 4


def repo_score(repo: dict) -> int:
    """How likely a repository is to publish proxy lists (higher is better)."""
    name = (repo.get("name") or repo.get("full_name", "").split("/")[-1]).lower()
    description = (repo.get("description") or "").lower()
    topics = set(repo.get("topics") or [])
    if name.endswith(".github.io") or looks_random(repo.get("name") or name):
        return -99
    text = f"{name} {description} {' '.join(topics)}"
    score = 0
    if re.search(r"prox(?:y|ies)|socks", name):
        score += 4
    if topics & _LIST_TOPICS:
        score += 3
    if _LISTY.search(description):
        score += 2
    if re.search(r"prox(?:y|ies)|socks", description):
        score += 1
    if _SPAM.search(text):
        score -= 12  # promotional / VPN-config repositories: practically never useful
    if _SOFTWARE.search(description):
        score -= 2
    return score


# --------------------------------------------------------------- registry

_HEADER = (
    "# Auto-discovered proxy list files (src/discover.py). Do not edit by hand:\n"
    "# entries are added by discovery and pruned when they keep failing.\n"
    "# '# @ owner/repo date' marks a repository that was already scanned.\n"
    "# format: <hint> <owner>/<repo>/<branch>/<path>\n"
)


@dataclass
class Registry:
    repos: dict[str, str] = field(default_factory=dict)                    # name -> pushed date
    files: dict[str, list[tuple[str, str]]] = field(default_factory=dict)  # name -> [(hint, path)]

    @classmethod
    def load(cls, path: str | Path) -> "Registry":
        registry = cls()
        try:
            lines = Path(path).read_text(encoding="utf-8").splitlines()
        except FileNotFoundError:
            return registry
        current = None
        for line in lines:
            line = line.strip()
            if line.startswith("# @ "):
                parts = line[4:].split()
                if parts:
                    current = parts[0]
                    registry.repos[current] = parts[1] if len(parts) > 1 else ""
                    registry.files.setdefault(current, [])
            elif line and not line.startswith("#"):
                parts = line.split()
                if len(parts) == 2 and current and parts[1].startswith(current + "/"):
                    registry.files[current].append((parts[0], parts[1]))
        return registry

    def total(self) -> int:
        return sum(len(v) for v in self.files.values())

    def add(self, full_name: str, pushed: str, files: list[tuple[str, str]]) -> None:
        self.repos[full_name] = pushed
        self.files[full_name] = files

    def prune(self, raw_urls: set[str]) -> int:
        """Drop files whose primary URL is in *raw_urls*; keep the repo marker."""
        removed = 0
        for name, items in self.files.items():
            keep = [(h, p) for h, p in items if github_urls(p)[0] not in raw_urls]
            removed += len(items) - len(keep)
            self.files[name] = keep
        return removed

    def save(self, path: str | Path) -> None:
        out = [_HEADER]
        for name, pushed in self.repos.items():
            out.append(f"# @ {name} {pushed}\n")
            out.extend(f"{hint} {path_}\n" for hint, path_ in self.files.get(name, []))
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(".tmp")
        tmp.write_text("".join(out), encoding="utf-8")
        os.replace(tmp, target)


def prune_registry(path: str | Path, raw_urls: set[str]) -> int:
    """Remove dead sources from the registry without touching the network."""
    registry = Registry.load(path)
    removed = registry.prune(raw_urls)
    if removed:
        registry.save(path)
    return removed


# --------------------------------------------------------------- GitHub API


class BudgetExhausted(Exception):
    """API budget, time budget or rate limit reached: stop discovering."""


class GitHub:
    def __init__(self, session: aiohttp.ClientSession, *, token: str | None = None,
                 api: str = API, max_calls: int = 600, max_wait: float = 90.0,
                 search_interval: float | None = None, deadline: float | None = None) -> None:
        self.session = session
        self.api = api.rstrip("/")
        self.headers = {"Accept": "application/vnd.github+json",
                        "X-GitHub-Api-Version": "2022-11-28",
                        "User-Agent": "Proxy-List-Gamt-discovery"}
        if token:
            self.headers["Authorization"] = f"Bearer {token}"
        self.max_calls = max_calls
        self.max_wait = max_wait
        # search API: 30 req/min with a token, 10 req/min without
        self.search_interval = search_interval if search_interval is not None else (2.2 if token else 6.5)
        self.deadline = deadline
        self.calls = 0
        self.search_calls = 0
        self._search_lock = asyncio.Lock()
        self._last_search = 0.0

    async def get(self, path: str, params: dict | None = None, *, search: bool = False):
        """GET *path*; returns ``(status, json_or_None)``. Raises BudgetExhausted."""
        for attempt in range(3):
            if self.calls >= self.max_calls:
                raise BudgetExhausted("API call budget reached")
            if self.deadline and time.monotonic() > self.deadline:
                raise BudgetExhausted("time budget reached")
            if search:
                async with self._search_lock:
                    wait = self.search_interval - (time.monotonic() - self._last_search)
                    if wait > 0:
                        await asyncio.sleep(wait)
                    self._last_search = time.monotonic()
                    self.search_calls += 1
            self.calls += 1
            try:
                async with self.session.get(self.api + path, params=params,
                                            headers=self.headers) as response:
                    status = response.status
                    if status == 200:
                        return 200, await response.json(content_type=None)
                    if status in (403, 429):
                        wait = self._rate_limit_wait(response.headers)
                        if wait is None or wait > self.max_wait or attempt == 2:
                            raise BudgetExhausted(f"rate limited (HTTP {status})")
                        await asyncio.sleep(wait)
                        continue
                    if status >= 500 and attempt < 2:
                        await asyncio.sleep(2)
                        continue
                    return status, None
            except (aiohttp.ClientError, asyncio.TimeoutError):
                if attempt == 2:
                    return 0, None
                await asyncio.sleep(1 + attempt)
        return 0, None

    @staticmethod
    def _rate_limit_wait(headers) -> float | None:
        retry_after = headers.get("Retry-After")
        if retry_after and retry_after.isdigit():
            return float(retry_after) + 1
        if headers.get("X-RateLimit-Remaining") == "0" and headers.get("X-RateLimit-Reset"):
            try:
                return max(0.0, float(headers["X-RateLimit-Reset"]) - time.time()) + 1
            except ValueError:
                return None
        return 5.0 if headers.get("X-RateLimit-Remaining") is None else None


# ---------------------------------------------------------------- discovery


@dataclass
class DiscoveryReport:
    repos_seen: int = 0
    repos_scanned: int = 0
    files_added: int = 0
    files_pruned: int = 0
    files_total: int = 0
    api_calls: int = 0
    search_calls: int = 0
    stopped: str = ""

    def stats(self) -> dict:
        return {"discovered_total": self.files_total, "discovered_added": self.files_added,
                "discovered_pruned": self.files_pruned, "repos_scanned": self.repos_scanned,
                "discovery_api_calls": self.api_calls}


def _windows(recent_days: int, window_days: int, today: date) -> list[tuple[str, str]]:
    """Date windows, newest first, that tile the last *recent_days* days."""
    windows, end = [], today
    while (today - end).days < recent_days:
        start = max(end - timedelta(days=window_days - 1), today - timedelta(days=recent_days))
        windows.append((start.isoformat(), end.isoformat()))
        end = start - timedelta(days=1)
    return windows


def _usable(repo: dict) -> bool:
    return not (repo.get("fork") or repo.get("archived") or repo.get("disabled")
                or not repo.get("size") or not repo.get("full_name")
                or not repo.get("default_branch") or repo_score(repo) <= -2)


async def discover(registry_path: str | Path, *, token: str | None = None,
                   max_sources: int = MAX_DISCOVERED, max_calls: int = 600,
                   max_search_calls: int = 100, recent_days: int = 21, window_days: int = 7,
                   per_repo: int = 10, time_budget: float = 600.0, concurrency: int = 6,
                   queries: tuple[str, ...] = QUERIES, prune_urls: set[str] | None = None,
                   api: str = API, today: date | None = None, log=print,
                   search_interval: float | None = None) -> DiscoveryReport:
    """Grow ``registry_path`` with newly found proxy-list files."""
    registry = Registry.load(registry_path)
    report = DiscoveryReport()
    if prune_urls:
        report.files_pruned = registry.prune(prune_urls)
    added_before = registry.total()
    deadline = time.monotonic() + time_budget
    today = today or datetime.now(timezone.utc).date()

    timeout = aiohttp.ClientTimeout(total=40, connect=10)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        gh = GitHub(session, token=token, api=api, max_calls=max_calls, deadline=deadline,
                    search_interval=search_interval)
        try:
            if registry.total() >= max_sources:
                report.stopped = "registry full"
            else:
                await _scan(gh, registry, report, queries, recent_days, window_days, today,
                            max_sources, max_search_calls, per_repo, concurrency, log)
        except BudgetExhausted as stop:
            report.stopped = str(stop)
        report.api_calls, report.search_calls = gh.calls, gh.search_calls

    registry.save(registry_path)
    report.files_total = registry.total()
    report.files_added = max(0, report.files_total - added_before)
    return report


async def _scan(gh: GitHub, registry: Registry, report: DiscoveryReport, queries, recent_days,
                window_days, today, max_sources, max_search_calls, per_repo, concurrency, log):
    # Phase A - collect repositories we have not scanned yet (search is rate limited,
    # so it runs sequentially; slices are ordered newest window first).
    fresh: dict[str, dict] = {}
    # Keep enough budget for the tree calls that follow the searches ...
    search_cap = min(max_search_calls, max(1, gh.max_calls // 4))
    # ... and enough time: searching may use at most ~55% of what is left.
    remaining = (gh.deadline - time.monotonic()) if gh.deadline else 1e9
    search_deadline = time.monotonic() + 0.55 * remaining
    search_done = False
    try:
        for since, until in _windows(recent_days, window_days, today):
            for query in queries:
                q = f"{query} fork:false archived:false pushed:{since}..{until}"
                for page in range(1, 11):
                    if gh.search_calls >= search_cap or time.monotonic() > search_deadline:
                        search_done = True
                        break
                    status, data = await gh.get(
                        "/search/repositories",
                        {"q": q, "sort": "updated", "order": "desc", "per_page": 100, "page": page},
                        search=True)
                    items = (data or {}).get("items") or []
                    if status != 200 or not items:
                        break
                    new = 0
                    for repo in items:
                        report.repos_seen += 1
                        name = repo.get("full_name")
                        if _usable(repo) and name not in registry.repos and name not in fresh:
                            fresh[name] = repo
                            new += 1
                    if len(items) < 100 or new == 0:
                        break  # slice exhausted, or everything on this page is known
                if search_done:
                    break
            if search_done:
                break
    except BudgetExhausted as stop:
        report.stopped = str(stop)
        if "rate limited" in report.stopped and not fresh:
            raise

    # Phase B - one git-tree call per new repository, most promising first.
    queue = sorted(fresh.values(), key=lambda r: (repo_score(r), r.get("pushed_at") or ""),
                   reverse=True)
    log(f"  discovery: {len(queue)} new repositories to scan "
        f"({gh.search_calls} search calls, {report.repos_seen} results seen)")
    semaphore = asyncio.Semaphore(concurrency)
    stop_event = asyncio.Event()

    async def scan_repo(repo: dict) -> None:
        if stop_event.is_set():
            return
        name, branch = repo["full_name"], repo["default_branch"]
        async with semaphore:
            if stop_event.is_set():
                return
            try:
                status, tree = await gh.get(f"/repos/{name}/git/trees/{branch}", {"recursive": "1"})
            except BudgetExhausted as stop:
                report.stopped = report.stopped or str(stop)
                stop_event.set()
                return
        if status in (404, 409, 451) or (status == 200 and not tree):
            registry.add(name, (repo.get("pushed_at") or "")[:10], [])  # nothing to see here
            report.repos_scanned += 1
            return
        if status != 200:
            return  # transient failure: try again next run
        room = max_sources - registry.total()
        if room <= 0:
            stop_event.set()
            report.stopped = report.stopped or "registry full"
            return
        picked = select_files(tree.get("tree", []), per_repo)[:room]
        files = [(hint, f"{name}/{branch}/{path}") for hint, path in picked]
        registry.add(name, (repo.get("pushed_at") or "")[:10], files)
        report.repos_scanned += 1

    await asyncio.gather(*(scan_repo(repo) for repo in queue))


# --------------------------------------------------------------------- CLI


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Discover proxy-list files on GitHub.")
    parser.add_argument("--registry", default="sources/discovered.txt")
    parser.add_argument("--max-sources", type=int, default=MAX_DISCOVERED)
    parser.add_argument("--max-calls", type=int, default=600, help="total GitHub API calls")
    parser.add_argument("--max-search-calls", type=int, default=100)
    parser.add_argument("--recent-days", type=int, default=21,
                        help="only repositories pushed within this many days")
    parser.add_argument("--per-repo", type=int, default=10)
    parser.add_argument("--time-budget", type=float, default=600.0, help="seconds")
    args = parser.parse_args(argv)
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    report = asyncio.run(discover(
        args.registry, token=token, max_sources=args.max_sources, max_calls=args.max_calls,
        max_search_calls=args.max_search_calls, recent_days=args.recent_days,
        per_repo=args.per_repo, time_budget=args.time_budget))
    print(f"discovery: {report}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

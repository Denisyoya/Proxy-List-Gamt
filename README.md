# Proxy-List-Gamt

Automated proxy scraper and validator by **Gametturxux**. The pipeline collects candidates from
hundreds of curated lists, websites and APIs (English, Chinese, Russian and more) plus every proxy-list
repository it can discover on GitHub, verifies each candidate against live judge endpoints, and
publishes only the proxies that actually route traffic. Everything runs on GitHub Actions, no server
required.

[![Proxy Scraper Auto Update](https://github.com/Denisyoya/Proxy-List-Gamt/actions/workflows/update-proxies.yml/badge.svg)](https://github.com/Denisyoya/Proxy-List-Gamt/actions/workflows/update-proxies.yml)
[![Tests](https://github.com/Denisyoya/Proxy-List-Gamt/actions/workflows/tests.yml/badge.svg)](https://github.com/Denisyoya/Proxy-List-Gamt/actions/workflows/tests.yml)
![Python](https://img.shields.io/badge/python-3.11%2B-blue)
![License](https://img.shields.io/badge/license-MIT-green)
![Schedule](https://img.shields.io/badge/refresh-every%203%20hours-orange)
![Author](https://img.shields.io/badge/author-Gametturxux-black)
![Community](https://img.shields.io/badge/community-gametturxux-25D366)

## Status

<!-- stats:start -->

Last run: `2026-10-10 01:27:56 UTC`

| Metric | Value |
| --- | --- |
| Live proxies | **1,092** |
| HTTP | 298 |
| HTTPS (verified TLS tunnel) | 49 |
| SOCKS4 | 169 |
| SOCKS5 | 608 |
| Passed every check (stable) | 772 |
| Median latency | 3718 ms |
| Fastest | 301 ms |
| Under 1 second | 146 |
| Sources registered | 694 (415 curated + 279 discovered) |
| Sources that delivered proxies | 501 |
| Candidates scraped | 1,657,215 |
| Reachable on TCP | 167,889 |
| Validation rounds | 3 (first pass + 2 confirmation) |
| Runtime | 2859s |

<!-- stats:end -->

## Results

Everything verified lands in one place, [`results/`](results), in three formats. Every format has
the same six lists:

```
results/
├── txt/    all.txt   http.txt   https.txt   socks.txt   socks4.txt   socks5.txt
├── json/   all.json  http.json  https.json  socks.json  socks4.json  socks5.json   stats.json
└── pdf/    all.pdf   http.pdf   https.pdf   socks.pdf   socks4.pdf   socks5.pdf
```

| List | Contents | `txt` line format |
| --- | --- | --- |
| `all` | Every live proxy of every kind (HTTP, HTTPS, SOCKS4, SOCKS5) | `protocol://ip:port` |
| `http` | HTTP proxies that relay plain HTTP | `ip:port` |
| `https` | HTTP proxies whose `CONNECT` tunnel passed a **certificate-verified** TLS check | `ip:port` |
| `socks` | SOCKS4 + SOCKS5 together | `protocol://ip:port` |
| `socks4` | SOCKS4 only | `ip:port` |
| `socks5` | SOCKS5 only | `ip:port` |

* Lists that mix protocols (`all`, `socks`) carry the scheme so every line is unambiguous; the
  single-protocol lists are bare `ip:port`.
* Every list is sorted by measured latency, fastest first.
* `json/*.json` are arrays of records:
  `{"proxy", "protocol", "url", "latency_ms", "http", "https", "exit_ip", "checks_passed", "checks_total"}`.
  `http` / `https` say what the proxy was observed to do in its last check, `checks_passed` of
  `checks_total` shows how reliable it was across the confirmation rounds.
* `json/stats.json` holds the metrics of the last run (counts, latency, source health, timings).
* `pdf/*.pdf` is a searchable text export of the same list for reading or printing.

A proxy that only supports `CONNECT` appears in `https` (and `all`) but not in `http`; a proxy
that works over plain HTTP but presents an untrusted certificate for HTTPS appears in `http` only.

### Direct links

```
https://raw.githubusercontent.com/Denisyoya/Proxy-List-Gamt/main/results/txt/all.txt
https://raw.githubusercontent.com/Denisyoya/Proxy-List-Gamt/main/results/txt/http.txt
https://raw.githubusercontent.com/Denisyoya/Proxy-List-Gamt/main/results/txt/https.txt
https://raw.githubusercontent.com/Denisyoya/Proxy-List-Gamt/main/results/txt/socks.txt
https://raw.githubusercontent.com/Denisyoya/Proxy-List-Gamt/main/results/txt/socks4.txt
https://raw.githubusercontent.com/Denisyoya/Proxy-List-Gamt/main/results/txt/socks5.txt
https://raw.githubusercontent.com/Denisyoya/Proxy-List-Gamt/main/results/json/all.json
```

Replace `txt/all.txt` with `json/<list>.json` or `pdf/<list>.pdf` for the other formats.

> **Migrating from the old layout.** The previous `proxy/` directory (`proxy.txt`, `stable.txt`,
> `proxies.csv`, `proxies.json` ...) was replaced by `results/`. `proxy.txt` is now
> `results/txt/all.txt`, `proxies.json` is `results/json/all.json`, and "stable" is
> `checks_passed == checks_total` in the JSON.

## Usage

Shell:

```bash
curl -x http://IP:PORT    https://api.ipify.org     # HTTP proxy
curl -x socks5h://IP:PORT https://api.ipify.org     # SOCKS5 proxy
```

Python:

```python
import requests

proxies = {"http": "socks5://IP:PORT", "https": "socks5://IP:PORT"}   # needs requests[socks]
print(requests.get("https://api.ipify.org", proxies=proxies, timeout=10).text)
```

Pull the list programmatically:

```python
import requests

url = "https://raw.githubusercontent.com/Denisyoya/Proxy-List-Gamt/main/results/json/https.json"
fast_https = [r["url"] for r in requests.get(url, timeout=15).json() if r["latency_ms"] < 1000]
```

## How validation works

1. **Discover** - `src/discover.py` searches GitHub for repositories that publish proxy lists (topics
   plus keywords in a dozen languages), reads each repository's file tree with **one API call**, and
   registers the files that look like proxy lists in `sources/discovered.txt` (hard cap: 50,000).
2. **Scrape** - every source is downloaded concurrently (per-host limits, mirror fallback only when a
   host fails, paged APIs walked until the first empty page, dead sources backed off). JSON, NDJSON,
   CSV, XML, HTML tables, Base64/percent-encoded pages and plain lists are parsed into one pool.
   Entries that need credentials (`user:pass@ip:port`) are dropped.
3. **Rank** - proxies that were alive in the last run are re-checked first, then proxies listed by
   many independent sources; an explicit protocol label is tested first.
4. **Environment check** - the runner learns its own egress IP and which judges answer, verifies that
   TLS certificate verification works, and sends control probes to RFC 5737 addresses that cannot
   host a proxy. If something answers, the network intercepts traffic and that exit IP is blacklisted.
5. **TCP pre-filter** - a bare connect (thousands in parallel) removes the dead majority before any
   protocol work happens.
6. **Protocol probe** - the proxy is spoken to directly over asyncio streams (no HTTP client library):
   absolute-URI `GET` for HTTP, `CONNECT` + TLS handshake with certificate verification for HTTPS, and
   the SOCKS4/SOCKS5 handshakes with a `CONNECT` to the judge on ports 80 and 443. A proxy is alive only
   when a rotating IP-echo judge answers through it with a **complete, well-formed** response whose IP
   differs from the runner's (transparent proxies that leak the runner's address are rejected). A proxy
   labelled `socks5` is tried as HTTP/SOCKS4 only when its first handshake was *not recognised*.
7. **Confirmation rounds** - survivors are probed again against different judges; only proxies that
   pass the final check are published, together with how many checks they passed.
8. **Publish** - `results/` is rewritten atomically. If nothing verified (network outage) the previous
   results are left untouched.

### Why it is faster and finds more

Measured on a fake internet on the loopback interface (150 live proxies hidden among 20,000
candidates: accept-and-hang servers, SYN-drop hosts and closed ports; 2 vCPU):

| | previous validator (default concurrency 1200) | previous validator (concurrency 300) | this validator |
| --- | --- | --- | --- |
| Wall time | 31.1 s | 40.0 s | **9.4 s** |
| CPU time | 22.1 s | 15.4 s | **3.6 s** |
| Peak memory | 154 MB | 84 MB | **83 MB** |
| Live proxies found | 131 / 150 | 150 / 150 | **150 / 150** |

The previous design built an HTTP client session per probe and one task per candidate. At its default
concurrency the event loop fell behind and spurious timeouts dropped 12-40 % of the good proxies
(run to run); lowering the concurrency found them all but was four times slower than the new pipeline.
The new pipeline uses bounded worker pools, a TCP pre-filter and raw handshakes. Real-world numbers
depend on your network.

## Sources

| File | Contents |
| --- | --- |
| [`sources/github.txt`](sources/github.txt) | Hand-picked proxy lists hosted on GitHub, verified alive and recently updated |
| [`sources/websites.txt`](sources/websites.txt) | Websites, APIs and pages in many languages (English, Chinese, Russian, Japanese, ...) |
| [`sources/discovered.txt`](sources/discovered.txt) | Found automatically by `src/discover.py` (capped at 50,000 sources) |

Add a source by appending one line (the test-suite fails on a malformed line):

```
http   owner/repo/main/path/http.txt                    # sources/github.txt
mixed  https://example.com/list?page={page}  pages=1..10 # sources/websites.txt
```

`hint` is the protocol the source claims for entries that do not say (`http`, `socks4`, `socks5`,
`socks`, `mixed`). GitHub files automatically get CDN mirrors (jsDelivr, statically) that are only
used when `raw.githubusercontent.com` fails.

**Source health.** A source that fails or delivers nothing for three runs in a row is skipped for an
exponentially growing number of runs (capped at 16), so dead sources cost almost nothing. A discovered
source that stays bad for 12 runs is removed from `sources/discovered.txt`. The state lives in
`.cache/source_health.json` (kept between workflow runs with `actions/cache`).

The number of discovered sources is bounded by what exists: a registry of 50,000 mostly dead lists
would only waste requests, so discovery only registers repositories pushed in the last 21 days and
skips spam, software and per-country/format duplicates.

## Running locally

```bash
git clone https://github.com/Denisyoya/Proxy-List-Gamt.git
cd Proxy-List-Gamt
pip install -r requirements.txt
python main.py
```

Set `GITHUB_TOKEN` (any token, no scopes needed) to let discovery use the GitHub API; without it
discovery is skipped and the committed registry is used as is.

| Flag | Default | Description |
| --- | --- | --- |
| `--timeout` | `8` | Per-probe timeout in seconds |
| `--connect-timeout` | `5` | TCP connect timeout in seconds |
| `--concurrency` | `800` | Simultaneous protocol probes |
| `--tcp-concurrency` | `3000` | Simultaneous TCP pre-filter connects |
| `--rounds` | `2` | Confirmation rounds after the first pass |
| `--max-candidates` | `0` | Test only the best N candidates (`0` = all) |
| `--time-budget` | `0` | Stop verifying after N minutes (`0` = no limit) |
| `--no-https-check` | off | Skip the `CONNECT` + TLS capability probe |
| `--no-controls` | off | Skip the network-interception control probes |
| `--judge URL` | built-in | Own IP-echo endpoint, repeatable (`http://` and `https://`) |
| `--sources` | `sources` | Directory with the source files |
| `--scrape-concurrency` | `200` | Simultaneous source downloads |
| `--scrape-budget` | `25` | Stop scraping after N minutes |
| `--skip-scrape` | off | Reuse the candidate pool cached by the last run |
| `--discover` | `auto` | `auto` (only with a token), `on` or `off` |
| `--max-sources` | `50000` | Cap for discovered sources |
| `--discover-calls` | `500` | GitHub API call budget per run |
| `--output` | `results` | Results directory |
| `--formats` | `txt,json,pdf` | Which formats to write |
| `--readme` | `README.md` | README to refresh (`''` leaves it alone) |
| `--cache` | `.cache` | Directory for source health and the candidate cache |
| `--allow-empty` | off | Publish even when nothing verified |

Quick run:

```bash
python main.py --max-candidates 20000 --rounds 1 --discover off
```

## Tests

The suite needs no internet: it builds a fake one on the loopback interface (mock HTTP / SOCKS4 /
SOCKS5 proxies in every flavour of good and bad behaviour, HTTP and HTTPS judges, websites and a
GitHub API) and runs the real `main.py` against it, comparing the published files with the known
ground truth.

```bash
pip install -r requirements-dev.txt
python -m unittest discover -s tests -v
```

Optional extras: with `curl`, `pproxy` and `proxy.py` installed the suite also cross-checks the mocks
and the validator against those independent implementations, and `LIVE_TESTS=1 GITHUB_TOKEN=...`
runs discovery against the real GitHub API.

## Automation

[`.github/workflows/update-proxies.yml`](.github/workflows/update-proxies.yml) runs the pipeline every
three hours and on manual dispatch, then commits the refreshed `results/`, `sources/` and this README's
statistics. [`.github/workflows/tests.yml`](.github/workflows/tests.yml) runs the test-suite on every
push and pull request.

The workflow carries an author lock. `PROJECT_AUTHOR`, `PROJECT_COMMUNITY` and `PROJECT_REPOSITORY` are
pinned in the YAML, and the `authorization` job compares the live repository name against
`Proxy-List-Gamt`. Renaming the repository fails that job, the `update` job is skipped, and the
automation stops running until the original name and author block are restored.

Setup:

1. Fork or push this repository to your account.
2. Open **Settings - Actions - General - Workflow permissions** and select
   **Read and write permissions** so the workflow can commit results.
3. Open the **Actions** tab, enable workflows, and trigger **Proxy Scraper Auto Update** once with
   **Run workflow**.

Change the cadence by editing the `cron` expression; the dispatch form exposes the timeouts,
concurrency, rounds, time budget, candidate cap and discovery mode.

## Project layout

```
.
├── .github/workflows/
│   ├── update-proxies.yml      scheduled pipeline + author lock
│   └── tests.yml               test-suite on every push / pull request
├── main.py                     pipeline entry point
├── requirements.txt            runtime dependency (aiohttp)
├── requirements-dev.txt        test dependencies
├── sources/                    github.txt, websites.txt, discovered.txt
├── results/                    published lists: txt/  json/  pdf/
├── src/
│   ├── common.py               protocol masks, IP rules
│   ├── parser.py               every payload format -> ip:port
│   ├── sources.py              source files and mirrors
│   ├── discover.py             GitHub discovery
│   ├── scraper.py              downloading, candidate pool, source health
│   ├── validator.py            TCP pre-filter, protocol probes, confirmation
│   ├── build.py                results writer and README blocks
│   └── pdfgen.py               dependency-free PDF writer
└── tests/                      unit, integration and end-to-end tests + fake internet
```

## Notes

- Free proxies are volatile. Entries verified at build time can die minutes later, so keep a
  client-side retry and refresh the list regularly.
- Traffic through a public proxy is visible to its operator. Never send credentials, payment data or
  other sensitive information over one, and prefer the `https` list: a verified TLS tunnel stops the
  operator from reading or rewriting the traffic.
- All proxies come from openly published lists. Use them in line with the laws and terms that apply
  to you.

## Credits

| Role | Value |
| --- | --- |
| Author | **Gametturxux** |
| Community | **gametturxux community** |
| Channel | https://whatsapp.com/channel/0029VbC6a5C7oQhUeajM3q1i |
| Repository | `Proxy-List-Gamt` |

## License

Released under the [MIT License](LICENSE).

<!-- footer:start -->

---

**Proxy-List-Gamt** - author **Gametturxux** - gametturxux community

Community channel: https://whatsapp.com/channel/0029VbC6a5C7oQhUeajM3q1i

Script by Gametturxux. Automated build refreshed at 2026-10-10 01:27:56 UTC.

<!-- footer:end -->

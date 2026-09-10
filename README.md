# Proxy-List-Gamt

Automated proxy scraper and validator by **Gametturxux**. The pipeline collects candidates from
**2,795 public sources**, verifies each one against live judge endpoints, and
publishes only the proxies that actually route traffic. Everything runs on
GitHub Actions, no server required.

[![Proxy Scraper Auto Update](https://github.com/Denisyoya/Proxy-List-Gamt/actions/workflows/update-proxies.yml/badge.svg)](https://github.com/Denisyoya/Proxy-List-Gamt/actions/workflows/update-proxies.yml)
![Python](https://img.shields.io/badge/python-3.11%2B-blue)
![License](https://img.shields.io/badge/license-MIT-green)
![Schedule](https://img.shields.io/badge/refresh-every%203%20hours-orange)
![Author](https://img.shields.io/badge/author-Gametturxux-black)
![Community](https://img.shields.io/badge/community-gametturxux-25D366)

## Status

<!-- stats:start -->

Last run: `2026-09-10 11:21:27 UTC`

| Metric | Value |
| --- | --- |
| Live proxies | **2,705** |
| HTTP / HTTPS | 1,904 |
| SOCKS4 | 280 |
| SOCKS5 | 521 |
| CONNECT (HTTPS) capable | 662 |
| Re-confirmed (stable) | 1,017 |
| Median latency | 1154 ms |
| Fastest | 4 ms |
| Under 1 second | 1,271 |
| Sources queried | 2,795 (653 responded) |
| Candidates scraped | 182,329 |
| Validation rounds | 3 |
| Runtime | 994s |

<!-- stats:end -->

## Output files

All results live in the [`proxy/`](proxy) directory.

| File | Contents | Format |
| --- | --- | --- |
| [`proxy/proxy.txt`](proxy/proxy.txt) | Every live proxy, all protocols combined | `protocol://ip:port` |
| [`proxy/all.txt`](proxy/all.txt) | Same set without the scheme prefix | `ip:port` |
| [`proxy/http.txt`](proxy/http.txt) | HTTP proxies only | `ip:port` |
| [`proxy/socks4.txt`](proxy/socks4.txt) | SOCKS4 proxies only | `ip:port` |
| [`proxy/socks5.txt`](proxy/socks5.txt) | SOCKS5 proxies only | `ip:port` |
| [`proxy/https.txt`](proxy/https.txt) | Proxies that passed the TLS `CONNECT` probe | `ip:port` |
| [`proxy/stable.txt`](proxy/stable.txt) | Proxies re-confirmed in a later round | `protocol://ip:port` |
| [`proxy/proxies.json`](proxy/proxies.json) | Full records: protocol, latency, exit IP, check counters | JSON |
| [`proxy/proxies.csv`](proxy/proxies.csv) | Same records for spreadsheets | CSV |
| [`proxy/stats.json`](proxy/stats.json) | Metrics of the last run | JSON |

`proxy.txt` is the superset: every entry from `http.txt`, `socks4.txt` and
`socks5.txt` also appears there, prefixed with its protocol so a single file
stays unambiguous. Lists are sorted by measured latency, fastest first.

## Direct links

```
https://raw.githubusercontent.com/Gametturxux/Proxy-List-Gamt/main/proxy/proxy.txt
https://raw.githubusercontent.com/Gametturxux/Proxy-List-Gamt/main/proxy/http.txt
https://raw.githubusercontent.com/Gametturxux/Proxy-List-Gamt/main/proxy/socks4.txt
https://raw.githubusercontent.com/Gametturxux/Proxy-List-Gamt/main/proxy/socks5.txt
```

## Usage

Shell:

```bash
curl -x http://IP:PORT   https://api.ipify.org      # HTTP proxy
curl -x socks5h://IP:PORT https://api.ipify.org     # SOCKS5 proxy
```

Python:

```python
import requests

proxies = {"http": "socks5://IP:PORT", "https": "socks5://IP:PORT"}
print(requests.get("https://api.ipify.org", proxies=proxies, timeout=10).text)
```

Pull the list programmatically:

```python
import requests

url = "https://raw.githubusercontent.com/Gametturxux/Proxy-List-Gamt/main/proxy/proxy.txt"
pool = requests.get(url, timeout=15).text.split()
```

## How validation works

1. **Source collection** - 2,795 endpoints are assembled: community raw lists on
   GitHub, ProxyScrape and Geonode APIs split per country and protocol,
   Proxifly country files, and HTML listing pages.
2. **Scraping** - every endpoint is fetched concurrently; JSON, plain text and
   HTML tables are parsed into a deduplicated `ip:port` pool with protocol
   hints.
3. **Egress fingerprinting** - the runner queries the judge endpoints directly
   to learn its own outbound IP, then sends control probes to RFC 5737
   addresses that cannot host a proxy. If a control probe answers, the network
   is intercepting traffic and those IPs are blacklisted for the run.
4. **Validation** - each candidate is tested against a rotating pool of judges
   (`api.ipify.org`, `icanhazip.com`, `checkip.amazonaws.com`, `ipinfo.io`,
   `ifconfig.me`, `ip-api.com`). Candidate protocols are probed in parallel and
   the fastest working one is recorded. A proxy is accepted only when the judge
   reports an IP that differs from the runner's own egress IP, which rules out
   transparent interception and false positives.
5. **Confirmation rounds** - verified proxies are re-tested with different
   judges. Latency is averaged and the pass counters are stored in
   `proxies.json`.
6. **Publishing** - `proxy/` is rewritten, the statistics block in this README
   is refreshed, and the workflow commits the changes.

## Running locally

```bash
git clone https://github.com/Gametturxux/Proxy-List-Gamt.git
cd proxy-list
pip install -r requirements.txt
python main.py
```

Options:

| Flag | Default | Description |
| --- | --- | --- |
| `--timeout` | `8` | Per-request timeout in seconds |
| `--concurrency` | `1200` | Parallel checks during validation |
| `--scrape-concurrency` | `120` | Parallel source downloads |
| `--rounds` | `2` | Confirmation rounds over verified proxies |
| `--max-candidates` | `0` | Cap the candidate pool (`0` = no cap) |
| `--skip-scrape` | off | Reuse the cached candidate pool in `.cache/` |
| `--no-https-check` | off | Skip the TLS `CONNECT` capability probe |
| `--output` | `proxy` | Output directory |

Example short run:

```bash
python main.py --max-candidates 20000 --rounds 1 --concurrency 800
```

## Automation

[`.github/workflows/update-proxies.yml`](.github/workflows/update-proxies.yml)
runs the pipeline every three hours and on manual dispatch, then commits the
refreshed `proxy/` directory.

The workflow carries an author lock. `PROJECT_AUTHOR`, `PROJECT_COMMUNITY` and
`PROJECT_REPOSITORY` are pinned in the YAML, and the `authorization` job
compares the live repository name against `Proxy-List-Gamt`. Renaming the
repository fails that job, the `update` job is skipped, and the automation
stops running until the original name and author block are restored.

Setup:

1. Fork or push this repository to your account.
2. Open **Settings - Actions - General - Workflow permissions** and select
   **Read and write permissions** so the workflow can commit results.
3. Open the **Actions** tab, enable workflows, and trigger
   **Update Proxy List** once with **Run workflow**.

Change the cadence by editing the `cron` expression, and tune `timeout`,
`concurrency` and `rounds` through the workflow dispatch inputs.

## Project layout

```
.
├── .github/workflows/update-proxies.yml   scheduled automation + author lock
├── main.py                                pipeline entry point
├── requirements.txt                       runtime dependencies
├── sources.txt                            generated source registry
├── proxy/                                 published lists
└── src/
    ├── sources.py                         source definitions
    ├── scraper.py                         async fetching and parsing
    ├── validator.py                       judge probing and egress guard
    └── build.py                           output writer and README stats
```

## Notes

- Free proxies are volatile. Entries verified at build time can die minutes
  later, so keep a client-side retry and refresh the list regularly.
- Traffic through a public proxy is visible to its operator. Never send
  credentials, payment data or other sensitive information over one.
- All proxies come from openly published lists. Use them in line with the laws
  and terms that apply to you.

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

Script by Gametturxux. Automated build refreshed at 2026-09-10 11:21:27 UTC.

<!-- footer:end -->

# results

Everything the pipeline publishes lives here. The validated lists are rewritten every five minutes by
[`update-proxies.yml`](../.github/workflows/update-proxies.yml), the unvalidated ones by
[`scrape-raw.yml`](../.github/workflows/scrape-raw.yml), and the PDFs are re-rendered from the
published JSON every three hours by [`publish-pdf.yml`](../.github/workflows/publish-pdf.yml).

```
results/
├── txt/    all.txt   http.txt   https.txt   socks.txt   socks4.txt   socks5.txt
├── json/   all.json  http.json  https.json  socks.json  socks4.json  socks5.json   stats.json
├── pdf/    all.pdf   http.pdf   https.pdf   socks.pdf   socks4.pdf   socks5.pdf
└── raw/    scraped only, never validated
    ├── txt/    all.txt   http.txt   socks.txt   socks4.txt   socks5.txt
    └── json/   all.json  http.json  socks.json  socks4.json  socks5.json   stats.json
```

| List | Contents | `txt` line format |
| --- | --- | --- |
| `all` | every live proxy of every kind | `protocol://ip:port` |
| `http` | HTTP proxies that relay plain HTTP | `ip:port` |
| `https` | HTTP proxies whose `CONNECT` tunnel passed a certificate-verified TLS check | `ip:port` |
| `socks` | SOCKS4 + SOCKS5 | `protocol://ip:port` |
| `socks4` | SOCKS4 only | `ip:port` |
| `socks5` | SOCKS5 only | `ip:port` |

All validated lists are sorted by latency, fastest first. JSON records:
`{"proxy", "protocol", "url", "latency_ms", "http", "https", "exit_ip", "checks_passed",
"checks_total", "checked_at"}`, plus `"carried_over": true` when an earlier pass verified the proxy
and this one kept it published.

`raw/` holds what the sources listed, with **no validation at all**: bigger, dirtier, and sorted by
address instead of latency. Its JSON records are
`{"proxy", "protocols", "listings", "hits", "first_seen", "last_seen", "age_seconds"}`, where
`listings` is how many sources listed the proxy in its best pass and `hits` in how many passes it was
seen. A proxy whose sources never claimed a protocol is listed bare (`ip:port`) in `raw/txt/all.txt`.

Both sets keep growing: every pass adds what it scraped and overwrites the entries that are already
there; entries nobody lists any more expire. See the [main README](../README.md#results) for details.

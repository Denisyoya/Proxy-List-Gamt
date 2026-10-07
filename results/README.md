# results

Everything the pipeline verified lives here, in three formats with the same six lists.
The files are rewritten by every run of
[`update-proxies.yml`](../.github/workflows/update-proxies.yml) (every three hours); they appear
after the first run.

```
results/
├── txt/    all.txt   http.txt   https.txt   socks.txt   socks4.txt   socks5.txt
├── json/   all.json  http.json  https.json  socks.json  socks4.json  socks5.json   stats.json
└── pdf/    all.pdf   http.pdf   https.pdf   socks.pdf   socks4.pdf   socks5.pdf
```

| List | Contents | `txt` line format |
| --- | --- | --- |
| `all` | every live proxy of every kind | `protocol://ip:port` |
| `http` | HTTP proxies that relay plain HTTP | `ip:port` |
| `https` | HTTP proxies whose `CONNECT` tunnel passed a certificate-verified TLS check | `ip:port` |
| `socks` | SOCKS4 + SOCKS5 | `protocol://ip:port` |
| `socks4` | SOCKS4 only | `ip:port` |
| `socks5` | SOCKS5 only | `ip:port` |

All lists are sorted by latency, fastest first. JSON records:
`{"proxy", "protocol", "url", "latency_ms", "http", "https", "exit_ip", "checks_passed", "checks_total"}`.
See the [main README](../README.md#results) for details.

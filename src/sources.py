from __future__ import annotations

import datetime

RAW = "https://raw.githubusercontent.com/"
JSDELIVR = "https://cdn.jsdelivr.net/gh/"
STATICALLY = "https://cdn.statically.io/gh/"

GITHUB_PATHS = [
    ("http", "TheSpeedX/PROXY-List/master/http.txt"),
    ("socks4", "TheSpeedX/PROXY-List/master/socks4.txt"),
    ("socks5", "TheSpeedX/PROXY-List/master/socks5.txt"),
    ("http", "TheSpeedX/SOCKS-List/master/http.txt"),
    ("socks4", "TheSpeedX/SOCKS-List/master/socks4.txt"),
    ("socks5", "TheSpeedX/SOCKS-List/master/socks5.txt"),
    ("http", "monosans/proxy-list/main/proxies/http.txt"),
    ("socks4", "monosans/proxy-list/main/proxies/socks4.txt"),
    ("socks5", "monosans/proxy-list/main/proxies/socks5.txt"),
    ("mixed", "monosans/proxy-list/main/proxies_anonymous/http.txt"),
    ("socks4", "monosans/proxy-list/main/proxies_anonymous/socks4.txt"),
    ("socks5", "monosans/proxy-list/main/proxies_anonymous/socks5.txt"),
    ("http", "ShiftyTR/Proxy-List/master/http.txt"),
    ("http", "ShiftyTR/Proxy-List/master/https.txt"),
    ("socks4", "ShiftyTR/Proxy-List/master/socks4.txt"),
    ("socks5", "ShiftyTR/Proxy-List/master/socks5.txt"),
    ("mixed", "ShiftyTR/Proxy-List/master/proxy.txt"),
    ("http", "jetkai/proxy-list/main/online-proxies/txt/proxies-http.txt"),
    ("http", "jetkai/proxy-list/main/online-proxies/txt/proxies-https.txt"),
    ("socks4", "jetkai/proxy-list/main/online-proxies/txt/proxies-socks4.txt"),
    ("socks5", "jetkai/proxy-list/main/online-proxies/txt/proxies-socks5.txt"),
    ("mixed", "jetkai/proxy-list/main/online-proxies/txt/proxies.txt"),
    ("http", "mmpx12/proxy-list/master/http.txt"),
    ("http", "mmpx12/proxy-list/master/https.txt"),
    ("socks4", "mmpx12/proxy-list/master/socks4.txt"),
    ("socks5", "mmpx12/proxy-list/master/socks5.txt"),
    ("mixed", "mmpx12/proxy-list/master/proxies.txt"),
    ("http", "roosterkid/openproxylist/main/HTTPS_RAW.txt"),
    ("socks4", "roosterkid/openproxylist/main/SOCKS4_RAW.txt"),
    ("socks5", "roosterkid/openproxylist/main/SOCKS5_RAW.txt"),
    ("socks5", "hookzof/socks5_list/master/proxy.txt"),
    ("http", "clarketm/proxy-list/master/proxy-list-raw.txt"),
    ("mixed", "sunny9577/proxy-scraper/master/proxies.txt"),
    ("http", "sunny9577/proxy-scraper/master/generated/http_proxies.txt"),
    ("socks4", "sunny9577/proxy-scraper/master/generated/socks4_proxies.txt"),
    ("socks5", "sunny9577/proxy-scraper/master/generated/socks5_proxies.txt"),
    ("http", "opsxcq/proxy-list/master/list.txt"),
    ("http", "rdavydov/proxy-list/main/proxies/http.txt"),
    ("socks4", "rdavydov/proxy-list/main/proxies/socks4.txt"),
    ("socks5", "rdavydov/proxy-list/main/proxies/socks5.txt"),
    ("http", "rdavydov/proxy-list/main/proxies_anonymous/http.txt"),
    ("socks4", "rdavydov/proxy-list/main/proxies_anonymous/socks4.txt"),
    ("socks5", "rdavydov/proxy-list/main/proxies_anonymous/socks5.txt"),
    ("http", "UptimerBot/proxy-list/main/proxies/http.txt"),
    ("socks4", "UptimerBot/proxy-list/main/proxies/socks4.txt"),
    ("socks5", "UptimerBot/proxy-list/main/proxies/socks5.txt"),
    ("http", "MuRongPIG/Proxy-Master/main/http.txt"),
    ("socks4", "MuRongPIG/Proxy-Master/main/socks4.txt"),
    ("socks5", "MuRongPIG/Proxy-Master/main/socks5.txt"),
    ("http", "MuRongPIG/Proxy-Master/main/http_checked.txt"),
    ("socks4", "MuRongPIG/Proxy-Master/main/socks4_checked.txt"),
    ("socks5", "MuRongPIG/Proxy-Master/main/socks5_checked.txt"),
    ("mixed", "zloi-user/hideip.me/main/http.txt"),
    ("mixed", "zloi-user/hideip.me/main/https.txt"),
    ("socks4", "zloi-user/hideip.me/main/socks4.txt"),
    ("socks5", "zloi-user/hideip.me/main/socks5.txt"),
    ("mixed", "proxifly/free-proxy-list/main/proxies/all/data.txt"),
    ("http", "proxifly/free-proxy-list/main/proxies/protocols/http/data.txt"),
    ("socks4", "proxifly/free-proxy-list/main/proxies/protocols/socks4/data.txt"),
    ("socks5", "proxifly/free-proxy-list/main/proxies/protocols/socks5/data.txt"),
    ("http", "vakhov/fresh-proxy-list/master/http.txt"),
    ("http", "vakhov/fresh-proxy-list/master/https.txt"),
    ("socks4", "vakhov/fresh-proxy-list/master/socks4.txt"),
    ("socks5", "vakhov/fresh-proxy-list/master/socks5.txt"),
    ("mixed", "vakhov/fresh-proxy-list/master/proxylist.txt"),
    ("http", "Zaeem20/FREE_PROXIES_LIST/master/http.txt"),
    ("http", "Zaeem20/FREE_PROXIES_LIST/master/https.txt"),
    ("socks4", "Zaeem20/FREE_PROXIES_LIST/master/socks4.txt"),
    ("socks5", "Zaeem20/FREE_PROXIES_LIST/master/socks5.txt"),
    ("http", "prxchk/proxy-list/main/http.txt"),
    ("socks4", "prxchk/proxy-list/main/socks4.txt"),
    ("socks5", "prxchk/proxy-list/main/socks5.txt"),
    ("mixed", "prxchk/proxy-list/main/all.txt"),
    ("http", "elliottophellia/yakumo/master/results/http/global/http_checked.txt"),
    ("socks4", "elliottophellia/yakumo/master/results/socks4/global/socks4_checked.txt"),
    ("socks5", "elliottophellia/yakumo/master/results/socks5/global/socks5_checked.txt"),
    ("http", "ErcinDedeoglu/proxies/main/proxies/http.txt"),
    ("http", "ErcinDedeoglu/proxies/main/proxies/https.txt"),
    ("socks4", "ErcinDedeoglu/proxies/main/proxies/socks4.txt"),
    ("socks5", "ErcinDedeoglu/proxies/main/proxies/socks5.txt"),
    ("mixed", "officialputuid/KangProxy/KangProxy/http/http.txt"),
    ("mixed", "officialputuid/KangProxy/KangProxy/https/https.txt"),
    ("socks4", "officialputuid/KangProxy/KangProxy/socks4/socks4.txt"),
    ("socks5", "officialputuid/KangProxy/KangProxy/socks5/socks5.txt"),
    ("mixed", "officialputuid/KangProxy/KangProxy/RAW.txt"),
    ("http", "yemixzy/proxy-list/main/proxies/http.txt"),
    ("socks4", "yemixzy/proxy-list/main/proxies/socks4.txt"),
    ("socks5", "yemixzy/proxy-list/main/proxies/socks5.txt"),
    ("mixed", "yemixzy/proxy-list/main/proxy-list/data.txt"),
    ("http", "zebbern/Proxy-Scraper/main/http.txt"),
    ("socks4", "zebbern/Proxy-Scraper/main/socks4.txt"),
    ("socks5", "zebbern/Proxy-Scraper/main/socks5.txt"),
    ("http", "dpangestuw/Free-Proxy/main/http_proxies.txt"),
    ("socks4", "dpangestuw/Free-Proxy/main/socks4_proxies.txt"),
    ("socks5", "dpangestuw/Free-Proxy/main/socks5_proxies.txt"),
    ("mixed", "dpangestuw/Free-Proxy/main/All_proxies.txt"),
    ("http", "Anonym0usWork1221/Free-Proxies/main/proxy_files/http_proxies.txt"),
    ("http", "Anonym0usWork1221/Free-Proxies/main/proxy_files/https_proxies.txt"),
    ("socks4", "Anonym0usWork1221/Free-Proxies/main/proxy_files/socks4_proxies.txt"),
    ("socks5", "Anonym0usWork1221/Free-Proxies/main/proxy_files/socks5_proxies.txt"),
    ("mixed", "B4RC0DE-TM/proxy-list/main/HTTP.txt"),
    ("socks4", "B4RC0DE-TM/proxy-list/main/SOCKS4.txt"),
    ("socks5", "B4RC0DE-TM/proxy-list/main/SOCKS5.txt"),
    ("http", "saschazesiger/Free-Proxies/master/proxies/http.txt"),
    ("socks4", "saschazesiger/Free-Proxies/master/proxies/socks4.txt"),
    ("socks5", "saschazesiger/Free-Proxies/master/proxies/socks5.txt"),
    ("http", "HyperBeats/proxy-list/main/http.txt"),
    ("socks4", "HyperBeats/proxy-list/main/socks4.txt"),
    ("socks5", "HyperBeats/proxy-list/main/socks5.txt"),
    ("mixed", "HyperBeats/proxy-list/main/all.txt"),
    ("http", "aslisk/proxyhttps/main/https.txt"),
    ("mixed", "proxy4parsing/proxy-list/main/http.txt"),
    ("http", "mishakorzik/Free-Proxy/main/http.txt"),
    ("socks4", "mishakorzik/Free-Proxy/main/socks4.txt"),
    ("socks5", "mishakorzik/Free-Proxy/main/socks5.txt"),
    ("http", "ObcbO/proxy_list/main/http.txt"),
    ("socks4", "ObcbO/proxy_list/main/socks4.txt"),
    ("socks5", "ObcbO/proxy_list/main/socks5.txt"),
    ("mixed", "ALIILAPRO/Proxy/main/http.txt"),
    ("socks4", "ALIILAPRO/Proxy/main/socks4.txt"),
    ("socks5", "ALIILAPRO/Proxy/main/socks5.txt"),
    ("http", "rx443/proxy-list/main/online/http.txt"),
    ("socks5", "rx443/proxy-list/main/online/socks5.txt"),
    ("mixed", "casals-ar/proxy-list/main/http"),
    ("socks4", "casals-ar/proxy-list/main/socks4"),
    ("socks5", "casals-ar/proxy-list/main/socks5"),
    ("http", "im-razvan/proxy_list/main/http.txt"),
    ("socks4", "im-razvan/proxy_list/main/socks4.txt"),
    ("socks5", "im-razvan/proxy_list/main/socks5.txt"),
    ("http", "Tsprnay/Proxy-lists/main/proxies/http.txt"),
    ("socks4", "Tsprnay/Proxy-lists/main/proxies/socks4.txt"),
    ("socks5", "Tsprnay/Proxy-lists/main/proxies/socks5.txt"),
    ("mixed", "Tsprnay/Proxy-lists/main/proxies/all.txt"),
    ("http", "gfpcom/free-proxy-list/main/list/http.txt"),
    ("socks4", "gfpcom/free-proxy-list/main/list/socks4.txt"),
    ("socks5", "gfpcom/free-proxy-list/main/list/socks5.txt"),
    ("http", "themiralay/Proxy-List-World/master/data.txt"),
    ("mixed", "andigwandi/free-proxy/main/proxy_list.txt"),
    ("http", "hendrikbgr/Free-Proxy-Repo/master/proxy_list.txt"),
    ("http", "almroot/proxylist/master/list.txt"),
    ("http", "fahimscirex/proxybd/master/proxylist/http.txt"),
    ("socks4", "fahimscirex/proxybd/master/proxylist/socks4.txt"),
    ("socks5", "fahimscirex/proxybd/master/proxylist/socks5.txt"),
    ("http", "proxylist-to/proxy-list/main/http.txt"),
    ("socks5", "proxylist-to/proxy-list/main/socks5.txt"),
    ("mixed", "zevtyardt/proxy-list/main/all.txt"),
    ("http", "zevtyardt/proxy-list/main/http.txt"),
    ("socks4", "zevtyardt/proxy-list/main/socks4.txt"),
    ("socks5", "zevtyardt/proxy-list/main/socks5.txt"),
    ("http", "tuanminpay/live-proxy/master/http.txt"),
    ("socks4", "tuanminpay/live-proxy/master/socks4.txt"),
    ("socks5", "tuanminpay/live-proxy/master/socks5.txt"),
    ("mixed", "tuanminpay/live-proxy/master/all.txt"),
    ("http", "SevenworksDev/proxy-list/main/proxies/http.txt"),
    ("socks4", "SevenworksDev/proxy-list/main/proxies/socks4.txt"),
    ("socks5", "SevenworksDev/proxy-list/main/proxies/socks5.txt"),
    ("mixed", "SevenworksDev/proxy-list/main/proxies/all.txt"),
    ("http", "databay-labs/free-proxy-list/master/http.txt"),
    ("socks5", "databay-labs/free-proxy-list/master/socks5.txt"),
    ("mixed", "databay-labs/free-proxy-list/master/all.txt"),
    ("http", "r00tee/Proxy-List/main/Https.txt"),
    ("socks4", "r00tee/Proxy-List/main/Socks4.txt"),
    ("socks5", "r00tee/Proxy-List/main/Socks5.txt"),
    ("http", "Vann-Dev/proxy-list/main/proxies/http.txt"),
    ("socks4", "Vann-Dev/proxy-list/main/proxies/socks4.txt"),
    ("socks5", "Vann-Dev/proxy-list/main/proxies/socks5.txt"),
    ("mixed", "Vann-Dev/proxy-list/main/proxies/all.txt"),
    ("http", "ryanhaticus/superiorproxy.com/master/proxies.txt"),
    ("mixed", "Anonymousfeed/anonymous_proxy_list/main/proxy_list.txt"),
    ("http", "BlackSnowDot/proxylist-update-every-minute/main/http.txt"),
    ("http", "BlackSnowDot/proxylist-update-every-minute/main/https.txt"),
    ("socks4", "BlackSnowDot/proxylist-update-every-minute/main/socks4.txt"),
    ("socks5", "BlackSnowDot/proxylist-update-every-minute/main/socks5.txt"),
    ("http", "zaeem20/FREE_PROXIES_LIST/master/http.txt"),
    ("mixed", "proxy-scraper/proxy-list/main/proxies.txt"),
    ("http", "Skiddle-ID/proxybase/main/http.txt"),
    ("socks4", "Skiddle-ID/proxybase/main/socks4.txt"),
    ("socks5", "Skiddle-ID/proxybase/main/socks5.txt"),
    ("mixed", "Skiddle-ID/proxylist/main/proxy.txt"),
    ("http", "miroslavpejic85/proxy-list/main/http.txt"),
    ("http", "proxyhub-io/free-proxy-list/main/http.txt"),
    ("socks4", "proxyhub-io/free-proxy-list/main/socks4.txt"),
    ("socks5", "proxyhub-io/free-proxy-list/main/socks5.txt"),
    ("http", "TuanMinPay/live-proxy/master/http.txt"),
    ("mixed", "proxylist4all/proxy-list/main/proxies.txt"),
    ("http", "Chill-Astro/Free-Proxy-List/main/http.txt"),
    ("socks5", "Chill-Astro/Free-Proxy-List/main/socks5.txt"),
    ("mixed", "Uptime-Checker/Proxy-List/main/proxies.txt"),
    ("http", "marcelo-oli/free-proxy-list/main/http.txt"),
    ("socks4", "marcelo-oli/free-proxy-list/main/socks4.txt"),
    ("socks5", "marcelo-oli/free-proxy-list/main/socks5.txt"),
    ("http", "parserpp/ip_ports/main/proxies.txt"),
    ("mixed", "PurpleLungs/proxy-list/main/proxies.txt"),
    ("http", "RaikaWebPro/Proxy-list/main/http.txt"),
    ("socks5", "RaikaWebPro/Proxy-list/main/socks5.txt"),
    ("mixed", "proxyscan-io/proxy-list/main/all.txt"),
    ("http", "eldadfux/proxy-list/main/http.txt"),
    ("http", "manuGMG/proxy-365/main/SOCKS5.txt"),
    ("mixed", "lalifeier/proxy-list/main/proxy.txt"),
    ("http", "proxy-daily/proxy-list/main/http.txt"),
    ("mixed", "hanwarudo/proxy-list/main/proxies.txt"),
    ("http", "getpp/proxy-list/main/http.txt"),
    ("socks5", "getpp/proxy-list/main/socks5.txt"),
    ("mixed", "ptechgithub/ProxyCollector/main/proxy.txt"),
    ("mixed", "MrMarble/proxy-list/main/all.txt"),
    ("http", "Ruberuby/proxy-list/main/http.txt"),
    ("mixed", "proxy-list-cc/proxy/main/proxy.txt"),
    ("http", "LeakedProxies/Proxy-List/main/http.txt"),
    ("socks5", "LeakedProxies/Proxy-List/main/socks5.txt"),
    ("mixed", "proxifly/free-proxy-list/main/proxies/countries/US/data.txt"),
    ("mixed", "proxifly/free-proxy-list/main/proxies/countries/ID/data.txt"),
    ("mixed", "proxifly/free-proxy-list/main/proxies/countries/IN/data.txt"),
    ("mixed", "proxifly/free-proxy-list/main/proxies/countries/BR/data.txt"),
    ("mixed", "proxifly/free-proxy-list/main/proxies/countries/RU/data.txt"),
    ("mixed", "proxifly/free-proxy-list/main/proxies/countries/CN/data.txt"),
    ("mixed", "proxifly/free-proxy-list/main/proxies/countries/DE/data.txt"),
    ("mixed", "proxifly/free-proxy-list/main/proxies/countries/FR/data.txt"),
    ("mixed", "proxifly/free-proxy-list/main/proxies/countries/GB/data.txt"),
    ("mixed", "proxifly/free-proxy-list/main/proxies/countries/JP/data.txt"),
    ("mixed", "proxifly/free-proxy-list/main/proxies/countries/KR/data.txt"),
    ("mixed", "proxifly/free-proxy-list/main/proxies/countries/CA/data.txt"),
    ("mixed", "proxifly/free-proxy-list/main/proxies/countries/NL/data.txt"),
    ("mixed", "proxifly/free-proxy-list/main/proxies/countries/SG/data.txt"),
    ("mixed", "proxifly/free-proxy-list/main/proxies/countries/TH/data.txt"),
    ("mixed", "proxifly/free-proxy-list/main/proxies/countries/VN/data.txt"),
    ("mixed", "proxifly/free-proxy-list/main/proxies/countries/PH/data.txt"),
    ("mixed", "proxifly/free-proxy-list/main/proxies/countries/MY/data.txt"),
    ("mixed", "proxifly/free-proxy-list/main/proxies/countries/TR/data.txt"),
    ("mixed", "proxifly/free-proxy-list/main/proxies/countries/UA/data.txt"),
    ("mixed", "proxifly/free-proxy-list/main/proxies/countries/PL/data.txt"),
    ("mixed", "proxifly/free-proxy-list/main/proxies/countries/IT/data.txt"),
    ("mixed", "proxifly/free-proxy-list/main/proxies/countries/ES/data.txt"),
    ("mixed", "proxifly/free-proxy-list/main/proxies/countries/MX/data.txt"),
    ("mixed", "proxifly/free-proxy-list/main/proxies/countries/AR/data.txt"),
    ("mixed", "proxifly/free-proxy-list/main/proxies/countries/CO/data.txt"),
    ("mixed", "proxifly/free-proxy-list/main/proxies/countries/CL/data.txt"),
    ("mixed", "proxifly/free-proxy-list/main/proxies/countries/PE/data.txt"),
    ("mixed", "proxifly/free-proxy-list/main/proxies/countries/EC/data.txt"),
    ("mixed", "proxifly/free-proxy-list/main/proxies/countries/BD/data.txt"),
    ("mixed", "proxifly/free-proxy-list/main/proxies/countries/PK/data.txt"),
    ("mixed", "proxifly/free-proxy-list/main/proxies/countries/IR/data.txt"),
    ("mixed", "proxifly/free-proxy-list/main/proxies/countries/IQ/data.txt"),
    ("mixed", "proxifly/free-proxy-list/main/proxies/countries/EG/data.txt"),
    ("mixed", "proxifly/free-proxy-list/main/proxies/countries/ZA/data.txt"),
    ("mixed", "proxifly/free-proxy-list/main/proxies/countries/NG/data.txt"),
    ("mixed", "proxifly/free-proxy-list/main/proxies/countries/KE/data.txt"),
    ("mixed", "proxifly/free-proxy-list/main/proxies/countries/MA/data.txt"),
    ("mixed", "proxifly/free-proxy-list/main/proxies/countries/DZ/data.txt"),
    ("mixed", "proxifly/free-proxy-list/main/proxies/countries/TN/data.txt"),
    ("mixed", "proxifly/free-proxy-list/main/proxies/countries/SA/data.txt"),
    ("mixed", "proxifly/free-proxy-list/main/proxies/countries/AE/data.txt"),
    ("mixed", "proxifly/free-proxy-list/main/proxies/countries/IL/data.txt"),
    ("mixed", "proxifly/free-proxy-list/main/proxies/countries/KZ/data.txt"),
    ("mixed", "proxifly/free-proxy-list/main/proxies/countries/UZ/data.txt"),
    ("mixed", "proxifly/free-proxy-list/main/proxies/countries/AZ/data.txt"),
    ("mixed", "proxifly/free-proxy-list/main/proxies/countries/GE/data.txt"),
    ("mixed", "proxifly/free-proxy-list/main/proxies/countries/AM/data.txt"),
    ("mixed", "proxifly/free-proxy-list/main/proxies/countries/RO/data.txt"),
    ("mixed", "proxifly/free-proxy-list/main/proxies/countries/BG/data.txt"),
    ("mixed", "proxifly/free-proxy-list/main/proxies/countries/CZ/data.txt"),
    ("mixed", "proxifly/free-proxy-list/main/proxies/countries/SK/data.txt"),
    ("mixed", "proxifly/free-proxy-list/main/proxies/countries/HU/data.txt"),
    ("mixed", "proxifly/free-proxy-list/main/proxies/countries/AT/data.txt"),
    ("mixed", "proxifly/free-proxy-list/main/proxies/countries/CH/data.txt"),
    ("mixed", "proxifly/free-proxy-list/main/proxies/countries/BE/data.txt"),
    ("mixed", "proxifly/free-proxy-list/main/proxies/countries/SE/data.txt"),
    ("mixed", "proxifly/free-proxy-list/main/proxies/countries/NO/data.txt"),
    ("mixed", "proxifly/free-proxy-list/main/proxies/countries/DK/data.txt"),
    ("mixed", "proxifly/free-proxy-list/main/proxies/countries/FI/data.txt"),
    ("mixed", "proxifly/free-proxy-list/main/proxies/countries/PT/data.txt"),
    ("mixed", "proxifly/free-proxy-list/main/proxies/countries/GR/data.txt"),
    ("mixed", "proxifly/free-proxy-list/main/proxies/countries/IE/data.txt"),
    ("mixed", "proxifly/free-proxy-list/main/proxies/countries/HR/data.txt"),
    ("mixed", "proxifly/free-proxy-list/main/proxies/countries/RS/data.txt"),
    ("mixed", "proxifly/free-proxy-list/main/proxies/countries/SI/data.txt"),
    ("mixed", "proxifly/free-proxy-list/main/proxies/countries/LT/data.txt"),
    ("mixed", "proxifly/free-proxy-list/main/proxies/countries/LV/data.txt"),
    ("mixed", "proxifly/free-proxy-list/main/proxies/countries/EE/data.txt"),
    ("mixed", "proxifly/free-proxy-list/main/proxies/countries/MD/data.txt"),
    ("mixed", "zloi-user/hideip.me/main/archive/2026-09-05/http.txt"),
    ("socks5", "zloi-user/hideip.me/main/archive/2026-09-05/socks5.txt"),
    ("mixed", "jetkai/proxy-list/main/archive/2026-09-05/proxies.txt"),
    ("mixed", "zloi-user/hideip.me/main/archive/2026-09-04/http.txt"),
    ("socks5", "zloi-user/hideip.me/main/archive/2026-09-04/socks5.txt"),
    ("mixed", "jetkai/proxy-list/main/archive/2026-09-04/proxies.txt"),
    ("mixed", "zloi-user/hideip.me/main/archive/2026-09-03/http.txt"),
    ("socks5", "zloi-user/hideip.me/main/archive/2026-09-03/socks5.txt"),
    ("mixed", "jetkai/proxy-list/main/archive/2026-09-03/proxies.txt"),
    ("mixed", "zloi-user/hideip.me/main/archive/2026-09-02/http.txt"),
    ("socks5", "zloi-user/hideip.me/main/archive/2026-09-02/socks5.txt"),
    ("mixed", "jetkai/proxy-list/main/archive/2026-09-02/proxies.txt"),
    ("mixed", "zloi-user/hideip.me/main/archive/2026-09-01/http.txt"),
    ("socks5", "zloi-user/hideip.me/main/archive/2026-09-01/socks5.txt"),
    ("mixed", "jetkai/proxy-list/main/archive/2026-09-01/proxies.txt"),
    ("mixed", "zloi-user/hideip.me/main/archive/2026-08-31/http.txt"),
    ("socks5", "zloi-user/hideip.me/main/archive/2026-08-31/socks5.txt"),
    ("mixed", "jetkai/proxy-list/main/archive/2026-08-31/proxies.txt"),
    ("mixed", "zloi-user/hideip.me/main/archive/2026-08-30/http.txt"),
    ("socks5", "zloi-user/hideip.me/main/archive/2026-08-30/socks5.txt"),
    ("mixed", "jetkai/proxy-list/main/archive/2026-08-30/proxies.txt"),
    ("mixed", "zloi-user/hideip.me/main/archive/2026-08-29/http.txt"),
    ("socks5", "zloi-user/hideip.me/main/archive/2026-08-29/socks5.txt"),
    ("mixed", "jetkai/proxy-list/main/archive/2026-08-29/proxies.txt"),
    ("mixed", "zloi-user/hideip.me/main/archive/2026-08-28/http.txt"),
    ("socks5", "zloi-user/hideip.me/main/archive/2026-08-28/socks5.txt"),
    ("mixed", "jetkai/proxy-list/main/archive/2026-08-28/proxies.txt"),
    ("mixed", "zloi-user/hideip.me/main/archive/2026-08-27/http.txt"),
    ("socks5", "zloi-user/hideip.me/main/archive/2026-08-27/socks5.txt"),
    ("mixed", "jetkai/proxy-list/main/archive/2026-08-27/proxies.txt"),
    ("http", "monosans/proxy-list/main/proxies_geolocation/http.txt"),
    ("socks4", "monosans/proxy-list/main/proxies_geolocation/socks4.txt"),
    ("socks5", "monosans/proxy-list/main/proxies_geolocation/socks5.txt"),
    ("http", "ProxyScraper/ProxyScraper/main/http.txt"),
    ("socks4", "ProxyScraper/ProxyScraper/main/socks4.txt"),
    ("socks5", "ProxyScraper/ProxyScraper/main/socks5.txt"),
    ("http", "TheSpeedX/PROXY-List/master/proxy.txt"),
    ("mixed", "sunny9577/proxy-scraper/master/generated/proxies.txt"),
    ("http", "Noctiro/getproxy/master/file/http.txt"),
    ("socks4", "Noctiro/getproxy/master/file/socks4.txt"),
    ("socks5", "Noctiro/getproxy/master/file/socks5.txt"),
    ("http", "kangproxy/KangProxy/main/http/http.txt"),
    ("http", "MrLunar-1/Proxy-List/main/http.txt"),
    ("socks5", "MrLunar-1/Proxy-List/main/socks5.txt"),
    ("http", "iplocate/free-proxy-list/main/protocols/http.txt"),
    ("socks4", "iplocate/free-proxy-list/main/protocols/socks4.txt"),
    ("socks5", "iplocate/free-proxy-list/main/protocols/socks5.txt"),
    ("mixed", "iplocate/free-proxy-list/main/all.txt"),
    ("http", "zenjahid/FreeProxy4u/master/http.txt"),
    ("socks4", "zenjahid/FreeProxy4u/master/socks4.txt"),
    ("socks5", "zenjahid/FreeProxy4u/master/socks5.txt"),
    ("http", "Fyrelysia/ProxyList/main/http.txt"),
    ("socks5", "Fyrelysia/ProxyList/main/socks5.txt"),
    ("http", "hanwarudo/proxies/main/http.txt"),
    ("socks5", "hanwarudo/proxies/main/socks5.txt"),
    ("http", "MrAlpha0/Free-Proxies/main/http.txt"),
    ("socks5", "MrAlpha0/Free-Proxies/main/socks5.txt"),
    ("http", "Mikhail-Deynekin/proxy-list/main/http.txt"),
    ("socks5", "Mikhail-Deynekin/proxy-list/main/socks5.txt"),
    ("mixed", "proxy4free/proxy-list/main/proxies.txt"),
    ("http", "Bardiafa/Free-Proxy-List/main/Http.txt"),
    ("socks4", "Bardiafa/Free-Proxy-List/main/Socks4.txt"),
    ("socks5", "Bardiafa/Free-Proxy-List/main/Socks5.txt"),
    ("mixed", "Bardiafa/Free-Proxy-List/main/Proxies.txt"),
    ("http", "TheDevs-Network/free-proxy-list/master/http.txt"),
    ("socks5", "TheDevs-Network/free-proxy-list/master/socks5.txt"),
    ("http", "sabrinask22/proxy-list/main/http.txt"),
    ("socks5", "sabrinask22/proxy-list/main/socks5.txt"),
    ("http", "hookzof/socks5_list/master/tg/socks.json"),
    ("http", "ProxyKingdom/proxy-list/main/http.txt"),
    ("socks5", "ProxyKingdom/proxy-list/main/socks5.txt"),
    ("mixed", "ProxyKingdom/proxy-list/main/all.txt"),
    ("http", "arandomguyhere/Proxy-Hub/main/http.txt"),
    ("socks5", "arandomguyhere/Proxy-Hub/main/socks5.txt"),
    ("http", "Firdoxx/proxy-list/main/http.txt"),
    ("socks5", "Firdoxx/proxy-list/main/socks5.txt"),
    ("http", "0xVend/Proxies/main/proxies/http.txt"),
    ("socks5", "0xVend/Proxies/main/proxies/socks5.txt"),
    ("http", "MrDinos/proxy/main/http.txt"),
    ("http", "Tanveerbaba/proxy-list/main/http.txt"),
    ("http", "lyzxsc/free-proxy/main/http.txt"),
    ("socks5", "lyzxsc/free-proxy/main/socks5.txt"),
    ("http", "ObcbO/proxy_list/main/https.txt"),
    ("http", "mertguvencli/http-proxy-list/main/proxy-list/data.txt"),
    ("http", "sh1nu11bi/proxy-list/main/http.txt"),
    ("http", "gitrecon1455/ProxyScraper/main/http.txt"),
    ("socks4", "gitrecon1455/ProxyScraper/main/socks4.txt"),
    ("socks5", "gitrecon1455/ProxyScraper/main/socks5.txt"),
    ("http", "sherlockdev-xyz/proxy-list/main/http.txt"),
    ("http", "AmirHoseinSalimi/proxy-list/main/http.txt"),
    ("socks5", "AmirHoseinSalimi/proxy-list/main/socks5.txt"),
    ("http", "Vikramaditya-Sharma/proxy-list/main/http.txt"),
    ("http", "kkeyzz/proxy-list/main/http.txt"),
    ("http", "gnbaron/proxy-list/main/http.txt"),
    ("http", "berkay-digital/Proxy-Scraper/main/proxies.txt"),
    ("http", "Sanjulikhitha/proxylist/main/http.txt"),
    ("http", "Zoro-chi/proxy-list/main/http.txt"),
    ("mixed", "hyperion-cs/proxy-list/main/proxies.txt"),
    ("http", "Karthik-HR0/Proxy-List/main/http.txt"),
    ("socks5", "Karthik-HR0/Proxy-List/main/socks5.txt"),
    ("http", "ercindedeoglu/proxies/main/proxies/http.txt"),
    ("http", "SoliSpirit/proxy-list/main/http.txt"),
    ("socks4", "SoliSpirit/proxy-list/main/socks4.txt"),
    ("socks5", "SoliSpirit/proxy-list/main/socks5.txt"),
    ("mixed", "SoliSpirit/proxy-list/main/all.txt"),
    ("http", "gozdegurdogan/proxy-list/main/http.txt"),
    ("http", "MDX-Studio/proxy-list/main/http.txt"),
    ("http", "vmheaven/VMHeaven-Free-Proxy-Updated/main/http.txt"),
    ("socks4", "vmheaven/VMHeaven-Free-Proxy-Updated/main/socks4.txt"),
    ("socks5", "vmheaven/VMHeaven-Free-Proxy-Updated/main/socks5.txt"),
    ("http", "Shivam-Rathore/proxy-list/main/http.txt"),
    ("http", "AliDev-ir/Proxy/main/http.txt"),
    ("socks5", "AliDev-ir/Proxy/main/socks5.txt"),
    ("http", "wolfssl-proxy/list/main/http.txt"),
    ("http", "Xewdy444/Proxy-List/main/http.txt"),
    ("socks5", "Xewdy444/Proxy-List/main/socks5.txt"),
    ("http", "Fresh-Proxy-List/Fresh-Proxy-List/master/proxylist.txt"),
    ("http", "Fresh-Proxy-List/Fresh-Proxy-List/master/http.txt"),
    ("http", "Fresh-Proxy-List/Fresh-Proxy-List/master/https.txt"),
    ("socks4", "Fresh-Proxy-List/Fresh-Proxy-List/master/socks4.txt"),
    ("socks5", "Fresh-Proxy-List/Fresh-Proxy-List/master/socks5.txt"),
    ("http", "proxylist-daily/proxy/main/http.txt"),
    ("socks5", "proxylist-daily/proxy/main/socks5.txt"),
    ("http", "NotSooShariff/proxy-list/main/http.txt"),
    ("http", "Egoist-Sama/proxy-list/main/http.txt"),
    ("http", "gogeta-projects/proxy/main/http.txt"),
    ("socks5", "gogeta-projects/proxy/main/socks5.txt"),
]

COUNTRIES = [
    "US", "ID", "IN", "BR", "RU", "CN", "DE", "FR", "GB", "JP", "KR", "CA",
    "NL", "SG", "TH", "VN", "PH", "MY", "TR", "UA", "PL", "IT", "ES", "MX",
    "AR", "CO", "CL", "PE", "EC", "BD", "PK", "IR", "IQ", "EG", "ZA", "NG",
    "KE", "MA", "DZ", "TN", "SA", "AE", "IL", "KZ", "UZ", "AZ", "GE", "AM",
    "RO", "BG", "CZ", "SK", "HU", "AT", "CH", "BE", "SE", "NO", "DK", "FI",
    "PT", "GR", "IE", "HR", "RS", "SI", "LT", "LV", "EE", "MD", "BY", "MN",
    "NP", "LK", "MM", "KH", "LA", "TW", "HK", "AU", "NZ", "VE", "BO", "PY",
    "UY", "CR", "PA", "GT", "HN", "SV", "NI", "DO", "JM", "TT", "CU", "PR",
    "AL", "MK", "BA", "ME", "XK", "CY", "MT", "IS", "LU", "LI", "AD", "MC",
    "QA", "KW", "BH", "OM", "JO", "LB", "SY", "YE", "AF", "TJ", "KG", "TM",
    "GH", "CI", "SN", "CM", "UG", "TZ", "ZM", "ZW", "MZ", "AO", "ET", "SD",
]

PROTOCOLS = ["http", "socks4", "socks5"]
ANONYMITY = ["all", "anonymous", "elite", "transparent"]


def _github(entries):
    out = []
    for proto, path in entries:
        out.append((proto, RAW + path))
    for proto, path in entries:
        parts = path.split("/")
        if len(parts) < 4:
            continue
        owner, repo, branch = parts[0], parts[1], parts[2]
        rest = "/".join(parts[3:])
        out.append((proto, f"{JSDELIVR}{owner}/{repo}@{branch}/{rest}"))
        out.append((proto, f"{STATICALLY}{owner}/{repo}/{branch}/{rest}"))
    return out


def _proxyscrape():
    out = []
    base4 = ("https://api.proxyscrape.com/v4/free-proxy-list/get"
             "?request=display_proxies&proxy_format=ipport&format=text")
    for proto in PROTOCOLS:
        out.append((proto, f"{base4}&protocol={proto}"))
        out.append((proto, f"{base4}&protocol={proto}&timeout=20000"))
        for country in COUNTRIES:
            out.append((proto, f"{base4}&protocol={proto}&country={country}"))
    base2 = "https://api.proxyscrape.com/v2/?request=displayproxies&timeout=10000"
    for proto in PROTOCOLS:
        for anon in ANONYMITY:
            out.append((proto, f"{base2}&protocol={proto}&country=all&ssl=all"
                               f"&anonymity={anon}"))
        for country in COUNTRIES[:80]:
            out.append((proto, f"{base2}&protocol={proto}&country={country}"
                               f"&ssl=all&anonymity=all"))
    return out


def _geonode():
    out = []
    for sort_by in ("lastChecked", "speed", "upTime"):
        for page in range(1, 31):
            out.append(("mixed",
                        "https://proxylist.geonode.com/api/proxy-list"
                        f"?limit=500&page={page}&sort_by={sort_by}&sort_type=desc"))
    for proto in PROTOCOLS:
        for page in range(1, 11):
            out.append((proto,
                        "https://proxylist.geonode.com/api/proxy-list"
                        f"?limit=500&page={page}&sort_by=lastChecked"
                        f"&sort_type=desc&protocols={proto}"))
    return out


def _proxifly():
    out = [("mixed", RAW + "proxifly/free-proxy-list/main/proxies/all/data.txt")]
    for proto in PROTOCOLS:
        out.append((proto, RAW + f"proxifly/free-proxy-list/main/proxies/"
                                 f"protocols/{proto}/data.txt"))
    for country in COUNTRIES:
        out.append(("mixed", RAW + f"proxifly/free-proxy-list/main/proxies/"
                                   f"countries/{country}/data.txt"))
    return out


def _proxy_list_download():
    out = []
    for kind in ("http", "https", "socks4", "socks5"):
        proto = "http" if kind in ("http", "https") else kind
        out.append((proto, f"https://www.proxy-list.download/api/v1/get?type={kind}"))
        for anon in ("elite", "anonymous", "transparent"):
            out.append((proto, f"https://www.proxy-list.download/api/v1/get"
                               f"?type={kind}&anon={anon}"))
        for country in COUNTRIES[:40]:
            out.append((proto, f"https://www.proxy-list.download/api/v1/get"
                               f"?type={kind}&country={country}"))
    return out


def _html_pages():
    out = []
    for page in range(1, 51):
        out.append(("mixed", "https://www.freeproxy.world/"
                             f"?type=&anonymity=&country=&speed=&port=&page={page}"))
    for proto in ("http", "https", "socks4", "socks5"):
        for page in range(1, 11):
            out.append(("mixed", f"https://www.freeproxy.world/?type={proto}"
                                 f"&anonymity=&country=&speed=&port=&page={page}"))
    for page in range(1, 21):
        out.append(("mixed", f"https://proxy-list.org/english/index.php?p={page}"))
    for page in range(1, 21):
        out.append(("mixed", f"https://premproxy.com/list/{page:02d}.htm"))
        out.append(("socks5", f"https://premproxy.com/socks-list/{page:02d}.htm"))
    for page in range(1, 21):
        out.append(("mixed", f"https://advanced.name/freeproxy?page={page}"))
    for country in COUNTRIES[:60]:
        out.append(("mixed", "https://www.proxynova.com/proxy-server-list/"
                             f"country-{country.lower()}/"))
    for page in range(1, 21):
        out.append(("mixed", f"https://www.ipaddress.com/proxy-list/?page={page}"))
    for offset in range(0, 40):
        out.append(("mixed", f"http://proxydb.net/?offset={offset * 15}"))
    for proto in ("http", "socks4", "socks5"):
        for offset in range(0, 15):
            out.append((proto, f"http://proxydb.net/?protocol={proto}"
                               f"&offset={offset * 15}"))
    for page in range(1, 21):
        out.append(("mixed", f"http://free-proxy.cz/en/proxylist/main/{page}"))
        out.append(("http", f"http://free-proxy.cz/en/proxylist/country/all/"
                            f"http/ping/all/{page}"))
        out.append(("socks5", f"http://free-proxy.cz/en/proxylist/country/all/"
                              f"socks5/ping/all/{page}"))
    for start in range(0, 20):
        out.append(("mixed", f"https://hidemy.io/en/proxy-list/?start={start * 64}"))
    for page in range(1, 16):
        out.append(("mixed", f"https://openproxylist.xyz/http.txt?p={page}"))
    for page in range(1, 11):
        out.append(("mixed", f"https://proxyservers.pro/proxy/list/page/{page}"))
        out.append(("mixed", f"https://www.proxyrack.com/free-proxy-list/?page={page}"))
    static = [
        ("http", "https://openproxylist.xyz/http.txt"),
        ("socks4", "https://openproxylist.xyz/socks4.txt"),
        ("socks5", "https://openproxylist.xyz/socks5.txt"),
        ("http", "https://api.openproxylist.xyz/http.txt"),
        ("socks4", "https://api.openproxylist.xyz/socks4.txt"),
        ("socks5", "https://api.openproxylist.xyz/socks5.txt"),
        ("http", "https://proxyspace.pro/http.txt"),
        ("http", "https://proxyspace.pro/https.txt"),
        ("socks4", "https://proxyspace.pro/socks4.txt"),
        ("socks5", "https://proxyspace.pro/socks5.txt"),
        ("mixed", "https://spys.me/proxy.txt"),
        ("socks5", "https://spys.me/socks.txt"),
        ("http", "https://multiproxy.org/txt_all/proxy.txt"),
        ("http", "https://multiproxy.org/txt_anon/proxy.txt"),
        ("mixed", "https://free-proxy-list.net/"),
        ("mixed", "https://free-proxy-list.net/anonymous-proxy.html"),
        ("mixed", "https://free-proxy-list.net/uk-proxy.html"),
        ("mixed", "https://free-proxy-list.net/web-proxy.html"),
        ("mixed", "https://www.sslproxies.org/"),
        ("mixed", "https://www.us-proxy.org/"),
        ("mixed", "https://socks-proxy.net/"),
        ("mixed", "https://www.socks-proxy.net/"),
        ("mixed", "https://proxy-daily.com/"),
        ("mixed", "https://api.proxy-list.download/v1/get?type=http"),
        ("mixed", "https://proxylist.geonode.com/api/proxy-list?limit=500&page=1"),
    ]
    out.extend(static)
    return out


def _archives():
    out = []
    today = datetime.date.today()
    for delta in range(0, 30):
        stamp = (today - datetime.timedelta(days=delta)).strftime("%Y-%m-%d")
        out.append(("mixed", RAW + f"zloi-user/hideip.me/main/archive/{stamp}/http.txt"))
        out.append(("socks5", RAW + f"zloi-user/hideip.me/main/archive/{stamp}/socks5.txt"))
        out.append(("mixed", RAW + f"jetkai/proxy-list/main/archive/{stamp}/proxies.txt"))
        out.append(("mixed", RAW + f"monosans/proxy-list/main/archive/{stamp}/http.txt"))
    return out


def build() -> list[tuple[str, str]]:
    collected = []
    collected += _github(GITHUB_PATHS)
    collected += _proxyscrape()
    collected += _geonode()
    collected += _proxifly()
    collected += _proxy_list_download()
    collected += _html_pages()
    collected += _archives()

    seen = set()
    unique = []
    for proto, url in collected:
        if url in seen:
            continue
        seen.add(url)
        unique.append((proto, url))
    return unique


def write(path: str = "sources.txt") -> int:
    entries = build()
    with open(path, "w") as fh:
        fh.write("\n".join(f"{p}|{u}" for p, u in entries) + "\n")
    return len(entries)


if __name__ == "__main__":
    print(write())

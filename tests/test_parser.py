import json
import unittest

import support  # noqa: F401  (sets sys.path)
from common import HTTP, SOCKS4, SOCKS5, is_public_ipv4
from parser import parse_payload


class PlainText(unittest.TestCase):
    def test_plain_list_with_noise(self):
        text = (
            "# comment line 1.2.3.4:80\r\n"
            "8.8.8.8:8080\r\n"
            "\r\n"
            "8.8.4.4:3128\n"
            "8.8.8.8:8080\n"            # duplicate
            "10.0.0.1:8080\n"           # private
            "127.0.0.1:1080\n"          # loopback
            "0.0.0.0:80\n"              # unspecified
            "1.2.3.0:80\n"              # network address
            "999.1.1.1:80\n"            # bad octet
            "8.8.8.8:99999\n"           # port out of range
            "garbage line\n"
        )
        self.assertEqual(parse_payload(text, "http"),
                         {"8.8.8.8:8080": HTTP, "8.8.4.4:3128": HTTP, "1.2.3.4:80": HTTP})

    def test_hint_masks(self):
        for hint, mask in (("http", HTTP), ("https", HTTP), ("socks4", SOCKS4),
                           ("socks5", SOCKS5), ("socks", SOCKS4 | SOCKS5), ("mixed", 0)):
            self.assertEqual(parse_payload("8.8.8.8:80", hint), {"8.8.8.8:80": mask}, hint)

    def test_scheme_in_line_beats_hint(self):
        text = "socks5://8.8.8.8:1080\nhttp://8.8.4.4:80\nhttps://1.1.1.1:443\nsocks4://9.9.9.9:4145\nsocks5h://4.4.4.4:9050\n"
        self.assertEqual(parse_payload(text, "http"), {
            "8.8.8.8:1080": SOCKS5, "8.8.4.4:80": HTTP, "1.1.1.1:443": HTTP,
            "9.9.9.9:4145": SOCKS4, "4.4.4.4:9050": SOCKS5})

    def test_same_proxy_with_two_protocols_merges_masks(self):
        found = parse_payload("http://8.8.8.8:80\nsocks5://8.8.8.8:80\n", "mixed")
        self.assertEqual(found, {"8.8.8.8:80": HTTP | SOCKS5})

    def test_credentials_are_skipped(self):
        text = ("http://user:pass@8.8.8.8:80\n"
                "socks5://1234:1234@8.8.4.4:1080\n"
                "user:pass@1.1.1.1:3128\n"
                "9.9.9.9:8080:user:password\n"
                "4.4.4.4:8080\n")
        self.assertEqual(parse_payload(text, "http"), {"4.4.4.4:8080": HTTP})

    def test_ip_inside_url_is_not_a_proxy(self):
        self.assertEqual(parse_payload("see https://example.com/8.8.8.8:80 now", "http"), {})

    def test_separators(self):
        text = "8.8.8.8 80\n8.8.4.4,3128\n1.1.1.1;8080\n9.9.9.9|1080\n4.4.4.4\t8888\n3.3.3.3\uff1a7777\n"
        self.assertEqual(sorted(parse_payload(text, "http")),
                         sorted(["8.8.8.8:80", "8.8.4.4:3128", "1.1.1.1:8080", "9.9.9.9:1080",
                                 "4.4.4.4:8888", "3.3.3.3:7777"]))

    def test_ip_octets_are_not_ports(self):
        self.assertEqual(parse_payload("8.8.8.8\n10.20.30.40\n5.6.7.8\n", "http"), {})

    def test_bom_and_utf16(self):
        self.assertEqual(parse_payload(b"\xef\xbb\xbf8.8.8.8:80\n", "http"), {"8.8.8.8:80": HTTP})
        self.assertEqual(parse_payload("8.8.8.8:80\n".encode("utf-16"), "http"), {"8.8.8.8:80": HTTP})

    def test_allow_private_for_local_testing(self):
        self.assertEqual(parse_payload("127.0.0.1:1080", "socks5", allow_private=True),
                         {"127.0.0.1:1080": SOCKS5})

    def test_public_ip_rules(self):
        for ip in ("8.8.8.8", "103.152.119.155", "1.1.1.1"):
            self.assertTrue(is_public_ipv4(ip), ip)
        for ip in ("10.1.1.1", "192.168.0.5", "172.16.0.1", "100.64.0.1", "169.254.1.1",
                   "224.0.0.1", "240.0.0.1", "192.0.2.1", "198.51.100.7", "1.2.3.0", "x"):
            self.assertFalse(is_public_ipv4(ip), ip)


class Markup(unittest.TestCase):
    def test_html_table_cells_on_separate_lines(self):
        html = ("<table><tr>\n<td>\n\t8.8.8.8\n</td>\n<td>\n\t8080\n</td>\n<td>HTTP</td></tr>\n"
                "<tr><td>8.8.4.4</td><td>1080</td><td>SOCKS5</td></tr></table>")
        self.assertEqual(parse_payload(html, "mixed"), {"8.8.8.8:8080": HTTP, "8.8.4.4:1080": SOCKS5})

    def test_protocol_word_after_entry_overrides_hint(self):
        rows = ("<tr><td>1.1.1.1</td><td>80</td><td>Socks4</td></tr>"
                "<tr><td>2.2.2.2</td><td>81</td><td>HTTPS</td></tr>"
                "<tr><td>3.3.3.3</td><td>82</td><td>elite proxy</td></tr>")
        self.assertEqual(parse_payload(rows, "socks5"),
                         {"1.1.1.1:80": SOCKS4, "2.2.2.2:81": HTTP, "3.3.3.3:82": SOCKS5})

    def test_one_line_rows_do_not_leak_protocol_between_entries(self):
        text = ("199.229.254.129:4145\uff0c\u9ad8\u533f\uff0csocks5\uff0cUS\uff1b"
                "136.228.234.29:8082\uff0c\u672a\u77e5\uff0chttps\uff0cUS\uff1b"
                "1.1.1.1:80\uff0c\u9ad8\u533f\uff0cUS")
        self.assertEqual(parse_payload(text, "mixed"),
                         {"199.229.254.129:4145": SOCKS5, "136.228.234.29:8082": HTTP, "1.1.1.1:80": 0})

    def test_markdown_table(self):
        md = "| IP | PORT | Type |\n| --- | --- | --- |\n| 219.133.31.120 | 8888 | HTTP | GD |\n| 221.180.147.30 | 80 | HTTP | LN |\n"
        self.assertEqual(parse_payload(md, "mixed"),
                         {"219.133.31.120:8888": HTTP, "221.180.147.30:80": HTTP})

    def test_html_entities(self):
        self.assertEqual(parse_payload("<p>8.8.8.8&#58;8080</p><p>8.8.4.4&nbsp;3128</p>", "http"),
                         {"8.8.8.8:8080": HTTP, "8.8.4.4:3128": HTTP})

    def test_xml(self):
        xml = ("<?xml version='1.0'?><response><item><host>h.example</host><ip>72.195.101.99</ip>"
               "<port>4145</port><delay>1080</delay></item><item><ip>45.131.6.46</ip><port>80</port></item></response>")
        self.assertEqual(parse_payload(xml, "mixed"), {"72.195.101.99:4145": 0, "45.131.6.46:80": 0})


class Obfuscated(unittest.TestCase):
    def test_free_proxy_cz_base64(self):
        import base64
        enc = base64.b64encode(b"8.8.8.8").decode()
        html = (f'<tr><td style="text-align:center"><script type="text/javascript">'
                f'document.write(Base64.decode("{enc}"))</script></td>'
                f'<td style=""><span class="fport" style=\'\'>8080</span></td><td><small>HTTP</small></td></tr>')
        self.assertEqual(parse_payload(html, "mixed"), {"8.8.8.8:8080": HTTP})

    def test_proxy_list_org_base64(self):
        import base64
        enc = base64.b64encode(b"8.8.4.4:3128").decode()
        html = f"<ul><li class='proxy'><script>Proxy('{enc}')</script></li></ul>"
        self.assertEqual(parse_payload(html, "http"), {"8.8.4.4:3128": HTTP})

    def test_freeproxylists_ipdecode(self):
        from urllib.parse import quote
        inner = quote('<a href="/zh/8.8.8.8.html">8.8.8.8</a>', safe="")
        html = f'<tr><td><script>IPDecode("{inner}")</script></td><td>8080</td><td>HTTPS</td></tr>'
        self.assertEqual(parse_payload(html, "mixed"), {"8.8.8.8:8080": HTTP})

    def test_json_inside_script(self):
        html = ('<script>const fpsList = [{"ip":"8.8.8.8","last_check_time":"x","port":"8080","speed":"1"},'
                '{"port":"3128","a":1,"ip":"8.8.4.4"}];</script>')
        self.assertEqual(parse_payload(html, "http"), {"8.8.8.8:8080": HTTP, "8.8.4.4:3128": HTTP})


class Structured(unittest.TestCase):
    def test_monosans_like(self):
        data = [{"protocol": "http", "username": None, "password": None, "host": "34.88.38.81",
                 "port": 9443, "exit_ip": "34.88.38.81", "geolocation": {"country": {"iso": "FI"}}},
                {"protocol": "socks5", "username": None, "password": None, "host": "8.8.8.8", "port": 1080},
                {"protocol": "http", "username": "u", "password": "p", "host": "9.9.9.9", "port": 80}]
        self.assertEqual(parse_payload(json.dumps(data), "mixed"),
                         {"34.88.38.81:9443": HTTP, "8.8.8.8:1080": SOCKS5})

    def test_vakhov_flags_and_hostname(self):
        data = [{"host": "ip72.cox.net", "ip": "72.195.101.99", "port": "4145", "http": "0",
                 "ssl": "0", "socks4": "1", "socks5": "0"},
                {"host": "45.131.6.46", "ip": "45.131.6.46", "port": "80", "http": "1", "ssl": "0",
                 "socks4": "0", "socks5": "0"},
                {"host": "x.net", "ip": "8.8.8.8", "port": "3128", "http": "0", "ssl": "0",
                 "socks4": "0", "socks5": "0"}]
        self.assertEqual(parse_payload(json.dumps(data), "mixed"),
                         {"72.195.101.99:4145": SOCKS4, "45.131.6.46:80": HTTP, "8.8.8.8:3128": 0})

    def test_proxifly_like_proxy_url(self):
        data = [{"proxy": "socks5://208.102.51.6:58208", "protocol": "socks5", "ip": "208.102.51.6",
                 "port": 58208, "https": True},
                {"proxy": "http://8.8.8.8:80", "https": False}]
        self.assertEqual(parse_payload(json.dumps(data), "mixed"),
                         {"208.102.51.6:58208": SOCKS5, "8.8.8.8:80": HTTP})

    def test_geonode_wrapper_and_protocols_list(self):
        data = {"data": [{"ip": "78.142.61.74", "port": "3128", "protocols": ["https"]},
                         {"ip": "8.8.8.8", "port": "1080", "protocols": ["socks4", "socks5"]}],
                "total": 2, "page": 1}
        self.assertEqual(parse_payload(json.dumps(data), "mixed"),
                         {"78.142.61.74:3128": HTTP, "8.8.8.8:1080": SOCKS4 | SOCKS5})

    def test_proxyscrape_v4_wrapper(self):
        data = {"shown_records": 1, "proxies": [{"protocol": "socks4", "proxy": "socks4://212.39.114.139:5678",
                                                "ip": "212.39.114.139", "port": 5678, "ssl": True}]}
        self.assertEqual(parse_payload(json.dumps(data), "http"), {"212.39.114.139:5678": SOCKS4})

    def test_ndjson(self):
        lines = '{"host":"8.8.8.8","port":80,"type":"http"}\n{"host":"8.8.4.4","port":1080,"type":"socks5"}\nnot json\n'
        self.assertEqual(parse_payload(lines, "mixed"), {"8.8.8.8:80": HTTP, "8.8.4.4:1080": SOCKS5})

    def test_list_of_strings_and_embedded_text(self):
        self.assertEqual(parse_payload('["8.8.8.8:80","socks5://8.8.4.4:1080"]', "http"),
                         {"8.8.8.8:80": HTTP, "8.8.4.4:1080": SOCKS5})
        self.assertEqual(parse_payload('{"list": "8.8.8.8:80\\n8.8.4.4:81"}', "socks4"),
                         {"8.8.8.8:80": SOCKS4, "8.8.4.4:81": SOCKS4})

    def test_broken_json_falls_back_to_text(self):
        self.assertEqual(parse_payload('[{"ip":"8.8.8.8","port":"80"},{"ip":"8.8.4.4","port":"81"', "http"),
                         {"8.8.8.8:80": HTTP, "8.8.4.4:81": HTTP})

    def test_csv_with_semicolons_and_flags(self):
        csv_text = ("host;ip;port;lastseen;http;ssl;socks4;socks5\r\n"
                    "h.example;72.195.101.99;4145;167;0;0;1;0\r\n"
                    "45.131.6.46;45.131.6.46;80;174;1;0;0;0\r\n")
        self.assertEqual(parse_payload(csv_text, "mixed"),
                         {"72.195.101.99:4145": SOCKS4, "45.131.6.46:80": HTTP})

    def test_csv_with_protocols_column(self):
        csv_text = "ip,port,protocols,anonymity\n159.203.61.169,8080,http,anonymous\n8.8.8.8,1080,socks5,elite\n"
        self.assertEqual(parse_payload(csv_text, "mixed"),
                         {"159.203.61.169:8080": HTTP, "8.8.8.8:1080": SOCKS5})

    def test_json_without_proxies_is_empty(self):
        self.assertEqual(parse_payload('{"schemaVersion":1,"label":"HTTP","message":"1,234"}', "http"), {})
        self.assertEqual(parse_payload("https://mtpro.xyz/socks5\n", "socks5"), {})


if __name__ == "__main__":
    unittest.main()

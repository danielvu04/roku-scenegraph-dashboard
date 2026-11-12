"""End-to-end tests: the real bridge on an ephemeral port, talking to a fake ECP."""

from __future__ import annotations

import http.client
import json
import logging
import os
import socket
import sys
import tempfile
import threading
import unittest

BRIDGE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BRIDGE_DIR not in sys.path:
    sys.path.insert(0, BRIDGE_DIR)

import couch_bridge as cb  # noqa: E402
from tests.fake_ecp import ICON_PNG, FakeECP  # noqa: E402

logging.getLogger("couch_bridge").setLevel(logging.CRITICAL)


def _closed_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class BridgeHarness:
    def __init__(self, roku_ip: str, favorites=None, locator=None):
        config = dict(cb.DEFAULT_CONFIG)
        config["roku_ip"] = roku_ip
        config["favorites"] = favorites or []
        self.server = cb.make_server(config, host="127.0.0.1", port=0, locator=locator)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def stop(self):
        self.server.shutdown()
        self.server.server_close()

    def request(self, method, path, body=None, raw_body=None, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        hdrs = dict(headers or {})
        data = raw_body
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            hdrs.setdefault("Content-Type", "application/json")
        try:
            conn.request(method, path, body=data, headers=hdrs)
            resp = conn.getresponse()
            payload = resp.read()
            ctype = resp.getheader("Content-Type", "")
            parsed = None
            if ctype.startswith("application/json"):
                parsed = json.loads(payload.decode("utf-8"))
            return resp.status, resp, payload, parsed
        finally:
            conn.close()


class BridgeTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fake = FakeECP().start()
        cls.bridge = BridgeHarness(cls.fake.address, favorites=["837", 2285, "999999"])

    @classmethod
    def tearDownClass(cls):
        cls.bridge.stop()
        cls.fake.stop()

    def setUp(self):
        self.fake.reset()
        self.bridge.server.icon_cache.clear()

    def req(self, *args, **kwargs):
        return self.bridge.request(*args, **kwargs)


class StatusTests(BridgeTestCase):
    def test_status_reports_device(self):
        status, _, _, data = self.req("GET", "/api/status")
        self.assertEqual(status, 200)
        self.assertTrue(data["reachable"])
        self.assertEqual(data["roku_ip"], self.fake.address)
        self.assertEqual(data["device"], {
            "name": "Living Room Roku",
            "model": "Roku Express 4K+",
            "power_mode": "PowerOn",
        })
        self.assertEqual(self.fake.paths(), ["/query/device-info"])

    def test_active_app(self):
        status, _, _, data = self.req("GET", "/api/active-app")
        self.assertEqual(status, 200)
        self.assertEqual(data["app"]["id"], "837")
        self.assertEqual(data["app"]["name"], "YouTube")
        self.assertEqual(data["screensaver"]["type"], "ssvr")
        self.assertEqual(self.fake.paths(), ["/query/active-app"])


class KeyTests(BridgeTestCase):
    def test_every_whitelisted_key(self):
        for key in cb.ALLOWED_KEYS:
            with self.subTest(key=key):
                self.fake.reset()
                status, _, _, data = self.req("POST", f"/api/key/{key}")
                self.assertEqual(status, 200, data)
                self.assertEqual(data, {"ok": True, "key": key})
                self.assertEqual(self.fake.requests, [("POST", f"/keypress/{key}", b"")])

    def test_key_case_insensitive_is_canonicalised(self):
        status, _, _, data = self.req("POST", "/api/key/volumeup")
        self.assertEqual(status, 200)
        self.assertEqual(self.fake.paths("POST"), ["/keypress/VolumeUp"])

    def test_non_whitelisted_keys_rejected(self):
        for key in ("Lit_a", "Reboot", "Home%2F..%2Flaunch%2F12", "InputHDMI1", "Find%20Remote"):
            with self.subTest(key=key):
                status, _, _, data = self.req("POST", f"/api/key/{key}")
                self.assertEqual(status, 400)
                self.assertIn("not allowed", data["error"])
        self.assertEqual(self.fake.requests, [])

    def test_get_on_post_route_is_405(self):
        status, resp, _, data = self.req("GET", "/api/key/Home")
        self.assertEqual(status, 405)
        self.assertEqual(resp.getheader("Allow"), "POST")
        self.assertEqual(self.fake.requests, [])


class TextTests(BridgeTestCase):
    def test_text_is_sent_as_literal_keypresses(self):
        status, _, _, data = self.req("POST", "/api/text", {"text": "hi there!&é"})
        self.assertEqual(status, 200)
        self.assertEqual(data, {"ok": True, "sent": 11})
        expected = ["h", "i", "%20", "t", "h", "e", "r", "e", "%21", "%26", "%C3%A9"]
        self.assertEqual(self.fake.paths("POST"), [f"/keypress/Lit_{c}" for c in expected])

    def test_slash_and_plus_are_escaped(self):
        self.req("POST", "/api/text", {"text": "a/+?"})
        self.assertEqual(self.fake.paths("POST"),
                         ["/keypress/Lit_a", "/keypress/Lit_%2F", "/keypress/Lit_%2B", "/keypress/Lit_%3F"])

    def test_text_validation(self):
        cases = [
            (None, b'{"text": ""}'),
            (None, b'{"text": 5}'),
            (None, b'{}'),
            (None, b'not json'),
            (None, b'["x"]'),
            (None, b''),
            ({"text": "x" * (cb.MAX_TEXT_CHARS + 1)}, None),
        ]
        for body, raw in cases:
            with self.subTest(body=body, raw=raw):
                status, _, _, data = self.req("POST", "/api/text", body=body, raw_body=raw)
                self.assertEqual(status, 400)
                self.assertIn("error", data)
        self.assertEqual(self.fake.requests, [])


class AppsTests(BridgeTestCase):
    def test_apps_favorites_first(self):
        status, _, _, data = self.req("GET", "/api/apps")
        self.assertEqual(status, 200)
        ids = [a["id"] for a in data["apps"]]
        # Favorites in config order (missing favorite 999999 skipped), then Roku order.
        self.assertEqual(ids, ["837", "2285", "31012", "12", "tvinput.hdmi1"])
        self.assertEqual([a["favorite"] for a in data["apps"]], [True, True, False, False, False])
        first = data["apps"][2]
        self.assertEqual(first["name"], "FandangoNOW Movies & TV")
        self.assertEqual(first["type"], "menu")
        self.assertEqual(self.fake.paths(), ["/query/apps"])

    def test_icon_passthrough_and_cache(self):
        for _ in range(3):
            status, resp, payload, _ = self.req("GET", "/api/apps/12/icon")
            self.assertEqual(status, 200)
            self.assertEqual(resp.getheader("Content-Type"), "image/png")
            self.assertEqual(payload, ICON_PNG)
        self.assertEqual(self.fake.paths(), ["/query/icon/12"])  # fetched once
        self.req("GET", "/api/apps/837/icon")
        self.assertEqual(self.fake.paths(), ["/query/icon/12", "/query/icon/837"])

    def test_icon_invalid_id(self):
        status, _, _, _ = self.req("GET", "/api/apps/bad%20id/icon")
        self.assertEqual(status, 400)
        self.assertEqual(self.fake.requests, [])

    def test_launch_without_body(self):
        status, _, _, data = self.req("POST", "/api/launch/12")
        self.assertEqual(status, 200)
        self.assertTrue(data["ok"])
        self.assertEqual(self.fake.requests, [("POST", "/launch/12", b"")])

    def test_launch_with_content_id(self):
        status, _, _, _ = self.req("POST", "/api/launch/12",
                                   {"contentId": "abc 123&x", "mediaType": "movie"})
        self.assertEqual(status, 200)
        self.assertEqual(self.fake.paths("POST"),
                         ["/launch/12?contentId=abc%20123%26x&mediaType=movie"])

    def test_launch_validation(self):
        status, _, _, _ = self.req("POST", "/api/launch/12", {"contentId": {"a": 1}})
        self.assertEqual(status, 400)
        status, _, _, _ = self.req("POST", "/api/launch/a%2Fb")
        self.assertEqual(status, 400)
        self.assertEqual(self.fake.requests, [])

    def test_dashboard_launches_dev_channel(self):
        status, _, _, data = self.req("POST", "/api/dashboard")
        self.assertEqual(status, 200)
        self.assertEqual(data["id"], "dev")
        self.assertEqual(self.fake.requests, [("POST", "/launch/dev", b"")])


class SearchTests(BridgeTestCase):
    def test_search_encoding(self):
        status, _, _, data = self.req("POST", "/api/search", {"keyword": "star wars & co/é"})
        self.assertEqual(status, 200)
        self.assertEqual(data["keyword"], "star wars & co/é")
        self.assertEqual(self.fake.paths("POST"),
                         ["/search/browse?keyword=star%20wars%20%26%20co%2F%C3%A9"])

    def test_search_with_provider_and_launch(self):
        self.req("POST", "/api/search", {"keyword": "Stranger Things", "providerId": 12, "launch": True})
        self.assertEqual(self.fake.paths("POST"),
                         ["/search/browse?keyword=Stranger%20Things&provider-id=12&launch=true"])

    def test_search_requires_keyword(self):
        for body in ({}, {"keyword": "   "}, {"keyword": 3}):
            with self.subTest(body=body):
                status, _, _, _ = self.req("POST", "/api/search", body)
                self.assertEqual(status, 400)
        self.assertEqual(self.fake.requests, [])


class EcpForbiddenTests(BridgeTestCase):
    def test_403_maps_to_502_with_permissive_hint(self):
        self.fake.force_status = 403
        for method, path in (("POST", "/api/key/Home"), ("GET", "/api/apps"),
                             ("POST", "/api/dashboard"), ("GET", "/api/active-app")):
            with self.subTest(path=path):
                status, _, _, data = self.req(method, path)
                self.assertEqual(status, 502)
                self.assertEqual(data["ecp_status"], 403)
                self.assertIn("Permissive", data["hint"])
                self.assertIn("Control by mobile apps", data["hint"])

    def test_status_reports_403_as_unreachable_with_hint(self):
        self.fake.force_status = 403
        status, _, _, data = self.req("GET", "/api/status")
        self.assertEqual(status, 200)
        self.assertFalse(data["reachable"])
        self.assertIn("Permissive", data["hint"])

    def test_other_ecp_errors_are_502(self):
        self.fake.force_status = 404
        status, _, _, data = self.req("POST", "/api/launch/424242")
        self.assertEqual(status, 502)
        self.assertEqual(data["ecp_status"], 404)
        self.assertIn("hint", data)


class StaticAndRoutingTests(BridgeTestCase):
    def test_index_served_at_root(self):
        status, resp, payload, _ = self.req("GET", "/")
        self.assertEqual(status, 200)
        self.assertEqual(resp.getheader("Content-Type"), "text/html; charset=utf-8")
        self.assertIn(b"<!DOCTYPE html>", payload)

    def test_mime_types(self):
        expected = {
            "/app.js": "text/javascript; charset=utf-8",
            "/style.css": "text/css; charset=utf-8",
            "/manifest.json": "application/manifest+json; charset=utf-8",
            "/icons/icon-192.png": "image/png",
            "/icons/icon.svg": "image/svg+xml",
        }
        for path, ctype in expected.items():
            with self.subTest(path=path):
                status, resp, payload, _ = self.req("GET", path)
                self.assertEqual(status, 200)
                self.assertEqual(resp.getheader("Content-Type"), ctype)
                self.assertTrue(payload)

    def test_head_request(self):
        status, resp, payload, _ = self.req("HEAD", "/style.css")
        self.assertEqual(status, 200)
        self.assertEqual(payload, b"")
        self.assertGreater(int(resp.getheader("Content-Length")), 0)

    def test_path_traversal_blocked(self):
        attempts = [
            "/../couch_bridge.py",
            "/%2e%2e/couch_bridge.py",
            "/icons/../../couch_bridge.py",
            "/..%2f..%2fSPEC.md",
            "/%2e%2e%5ccouch_bridge.py",
            "/.hidden",
            "//etc/passwd",
        ]
        for path in attempts:
            with self.subTest(path=path):
                status, _, payload, _ = self.req("GET", path)
                self.assertIn(status, (403, 404))
                self.assertNotIn(b"import", payload)
                self.assertNotIn(b"root:", payload)

    def test_unknown_routes_404(self):
        for method, path in (("GET", "/api/nope"), ("POST", "/api/keys/Home"),
                             ("GET", "/api"), ("GET", "/nope.html")):
            with self.subTest(path=path):
                status, _, _, data = self.req(method, path)
                self.assertEqual(status, 404)
                self.assertIn("error", data)

    def test_post_to_static_is_405(self):
        status, _, _, _ = self.req("POST", "/index.html", raw_body=b"")
        self.assertEqual(status, 405)


class UnreachableTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.address = f"127.0.0.1:{_closed_port()}"
        cls.bridge = BridgeHarness(cls.address)

    @classmethod
    def tearDownClass(cls):
        cls.bridge.stop()

    def test_commands_return_502_with_hint(self):
        for method, path, body in (("POST", "/api/key/Home", None), ("GET", "/api/apps", None),
                                   ("POST", "/api/text", {"text": "ab"}),
                                   ("GET", "/api/apps/12/icon", None),
                                   ("POST", "/api/search", {"keyword": "x"})):
            with self.subTest(path=path):
                status, resp, _, data = self.bridge.request(method, path, body)
                self.assertEqual(status, 502)
                self.assertTrue(resp.getheader("Content-Type").startswith("application/json"))
                self.assertIn("unreachable", data["error"])
                self.assertIn(self.address, data["hint"])
                self.assertEqual(data["roku_ip"], self.address)

    def test_status_reports_unreachable(self):
        status, _, _, data = self.bridge.request("GET", "/api/status")
        self.assertEqual(status, 200)
        self.assertFalse(data["reachable"])
        self.assertIsNone(data["device"])
        self.assertIn("hint", data)


class DiscoveryTests(unittest.TestCase):
    def test_parse_ssdp_response(self):
        reply = (b"HTTP/1.1 200 OK\r\nCache-Control: max-age=3600\r\nST: roku:ecp\r\n"
                 b"USN: uuid:roku:ecp:X00000000000\r\nLOCATION: http://192.168.1.134:8060/\r\n\r\n")
        self.assertEqual(cb.parse_ssdp_response(reply), ("192.168.1.134", 8060))
        self.assertIsNone(cb.parse_ssdp_response(b"HTTP/1.1 200 OK\r\nST: upnp:rootdevice\r\n"
                                                 b"LOCATION: http://10.0.0.2:1400/\r\n\r\n"))
        self.assertIsNone(cb.parse_ssdp_response(b"HTTP/1.1 200 OK\r\nST: roku:ecp\r\n\r\n"))

    def test_parse_host_port(self):
        self.assertEqual(cb.parse_host_port("10.0.0.5"), ("10.0.0.5", 8060))
        self.assertEqual(cb.parse_host_port("10.0.0.5:9000"), ("10.0.0.5", 9000))
        self.assertEqual(cb.parse_host_port("http://10.0.0.5:8060/"), ("10.0.0.5", 8060))

    def test_discovery_used_cached_and_rediscovered_when_unreachable(self):
        fake = FakeECP().start()
        dead = ("127.0.0.1", _closed_port())
        live = ("127.0.0.1", fake.port)
        answers = [dead, live]
        calls = []

        def discover(timeout):
            calls.append(timeout)
            return answers.pop(0) if answers else None

        locator = cb.RokuLocator(None, discover=discover)
        bridge = BridgeHarness("", locator=locator)
        try:
            status, _, _, data = bridge.request("GET", "/api/status")
            self.assertEqual(status, 200)
            self.assertTrue(data["reachable"], data)
            self.assertEqual(data["roku_ip"], fake.address)
            self.assertEqual(len(calls), 2)  # first answer was stale -> re-discovered
            bridge.request("POST", "/api/key/Home")
            self.assertEqual(len(calls), 2)  # cached
            self.assertEqual(fake.paths("POST"), ["/keypress/Home"])
        finally:
            bridge.stop()
            fake.stop()

    def test_no_roku_found_gives_hint(self):
        locator = cb.RokuLocator(None, discover=lambda timeout: None)
        bridge = BridgeHarness("", locator=locator)
        try:
            status, _, _, data = bridge.request("POST", "/api/key/Home")
            self.assertEqual(status, 502)
            self.assertIn("roku_ip", data["hint"])
            status, _, _, data = bridge.request("GET", "/api/status")
            self.assertFalse(data["reachable"])
            self.assertIsNone(data["roku_ip"])
        finally:
            bridge.stop()


class ConfigTests(unittest.TestCase):
    def test_explicit_config_merges_over_defaults(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
            json.dump({"roku_ip": "10.1.2.3", "favorites": ["12"], "location": {"name": "Duluth"}}, fh)
        try:
            config, source = cb.load_config(fh.name)
            self.assertEqual(source, fh.name)
            self.assertEqual(config["roku_ip"], "10.1.2.3")
            self.assertEqual(config["bridge_port"], 8787)
            self.assertEqual(config["location"]["name"], "Duluth")
            self.assertEqual(config["location"]["lat"], 44.98)
        finally:
            os.unlink(fh.name)

    def test_missing_explicit_config_errors(self):
        with self.assertRaises(cb.ConfigError):
            cb.load_config("/nonexistent/config.json")

    def test_default_lookup_falls_back(self):
        config, source = cb.load_config()
        self.assertIn("bridge_port", config)
        self.assertTrue(source.endswith(("config.json", "config.example.json")) or source == "built-in defaults")


if __name__ == "__main__":
    unittest.main()

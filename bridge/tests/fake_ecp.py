"""A tiny fake Roku ECP server for tests (http.server on an ephemeral port)."""

from __future__ import annotations

import threading
import zlib
import struct
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def _make_png() -> bytes:
    """A valid 1x1 PNG, built by hand so tests need no third-party libraries."""
    def chunk(kind: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + kind + data
                + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF))
    ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    idat = zlib.compress(b"\x00\x6c\x3c\xd8")
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", idat) + chunk(b"IEND", b"")


ICON_PNG = _make_png()

DEVICE_INFO_XML = b"""<?xml version="1.0" encoding="UTF-8" ?>
<device-info>
  <udn>29380000-0000-1000-8000-000000000000</udn>
  <serial-number>X00000000000</serial-number>
  <vendor-name>Roku</vendor-name>
  <model-name>Roku Express 4K+</model-name>
  <model-number>3941X</model-number>
  <friendly-device-name>Living Room Roku</friendly-device-name>
  <user-device-name>Living Room Roku</user-device-name>
  <default-device-name>Roku Express 4K+ - X00000000000</default-device-name>
  <power-mode>PowerOn</power-mode>
  <developer-enabled>true</developer-enabled>
</device-info>
"""

APPS_XML = b"""<?xml version="1.0" encoding="UTF-8" ?>
<apps>
  <app id="31012" type="menu" version="2.0.0">FandangoNOW Movies &amp; TV</app>
  <app id="12" type="appl" version="5.1.0">Netflix</app>
  <app id="837" type="appl" version="2.21.0">YouTube</app>
  <app id="2285" type="appl" version="6.10.0">Hulu</app>
  <app id="tvinput.hdmi1" type="tvin" version="1.0.0">HDMI 1</app>
</apps>
"""

ACTIVE_APP_XML = b"""<?xml version="1.0" encoding="UTF-8" ?>
<active-app>
  <app id="837" type="appl" version="2.21.0">YouTube</app>
  <screensaver id="55545" type="ssvr" version="2.0.1">Default screensaver</screensaver>
</active-app>
"""


class FakeECP:
    """Records every request; serves canned ECP responses.

    Set `force_status` (e.g. 403) to make every request fail with that code.
    """

    def __init__(self) -> None:
        self.requests: list[tuple[str, str, bytes]] = []
        self.force_status: int | None = None
        self._lock = threading.Lock()
        fake = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args):
                pass

            def _reply(self, status: int, body: bytes = b"", ctype: str = "text/xml; charset=utf-8"):
                self.send_response(status)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _record(self) -> None:
                length = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(length) if length else b""
                with fake._lock:
                    fake.requests.append((self.command, self.path, body))

            def do_GET(self):
                self._record()
                if fake.force_status:
                    return self._reply(fake.force_status, b"")
                if self.path == "/query/device-info":
                    return self._reply(200, DEVICE_INFO_XML)
                if self.path == "/query/apps":
                    return self._reply(200, APPS_XML)
                if self.path == "/query/active-app":
                    return self._reply(200, ACTIVE_APP_XML)
                if self.path.startswith("/query/icon/"):
                    return self._reply(200, ICON_PNG, "image/png")
                return self._reply(404)

            def do_POST(self):
                self._record()
                if fake.force_status:
                    return self._reply(fake.force_status, b"")
                if (self.path.startswith("/keypress/") or self.path.startswith("/launch/")
                        or self.path.startswith("/search/browse")):
                    return self._reply(200, b"", "text/plain")
                return self._reply(404)

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.httpd.daemon_threads = True
        self.port = self.httpd.server_address[1]
        self._thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    @property
    def address(self) -> str:
        return f"127.0.0.1:{self.port}"

    def start(self) -> "FakeECP":
        self._thread.start()
        return self

    def stop(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()

    def reset(self) -> None:
        with self._lock:
            self.requests.clear()
        self.force_status = None

    def paths(self, method: str | None = None) -> list[str]:
        with self._lock:
            return [p for m, p, _ in self.requests if method is None or m == method]

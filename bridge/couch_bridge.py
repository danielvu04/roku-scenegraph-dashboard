#!/usr/bin/env python3
"""roku-couch bridge: serves the phone remote and proxies to Roku ECP.

Python 3.11+, standard library only.

    python3 couch_bridge.py                 # auto-discover the Roku via SSDP
    python3 couch_bridge.py --roku-ip 192.168.1.50
    python3 couch_bridge.py --port 8787 --config ../config.json

Browsers can't talk to Roku ECP (http://<roku>:8060) directly -- there is no
CORS and it would be mixed content -- so this small server sits on an
always-on machine on the same LAN and exposes a JSON API plus the static
remote UI in ./static.

SECURITY: there is no authentication. Run it on a trusted LAN only and never
port-forward it to the internet.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import socket
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from collections import OrderedDict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable

__version__ = "1.0.0"

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(HERE)
STATIC_DIR = os.path.join(HERE, "static")

ECP_PORT = 8060
ECP_TIMEOUT = 3.0
SSDP_ADDR = ("239.255.255.250", 1900)
SSDP_TIMEOUT = 3.0
DISCOVERY_RETRY_SECONDS = 15.0
MAX_BODY_BYTES = 64 * 1024
MAX_TEXT_CHARS = 500
MAX_KEYWORD_CHARS = 200
ICON_CACHE_ENTRIES = 256

DEFAULT_CONFIG: dict[str, Any] = {
    "roku_ip": "",
    "bridge_port": 8787,
    "location": {"name": "Minneapolis", "lat": 44.98, "lon": -93.27},
    "units": "fahrenheit",
    "clock_24h": False,
    "background_seconds": 45,
    "favorites": [],
}

# ECP keys the remote is allowed to send. Literal characters go through
# /api/text instead (Lit_<char>), so they are deliberately not listed here.
ALLOWED_KEYS = (
    "Home", "Back", "Select", "Up", "Down", "Left", "Right",
    "Play", "Rev", "Fwd", "InstantReplay", "Info", "Search",
    "Enter", "Backspace", "VolumeUp", "VolumeDown", "VolumeMute",
    "PowerOff", "PowerOn", "Power",
)
_KEY_LOOKUP = {k.lower(): k for k in ALLOWED_KEYS}

APP_ID_RE = re.compile(r"^[A-Za-z0-9._:-]{1,64}$")

HINT_PERMISSIVE = (
    "The Roku refused the request (HTTP 403). On the Roku open Settings › System › "
    "Advanced system settings › Control by mobile apps › Network access and set it "
    "to Permissive."
)
HINT_UNREACHABLE = (
    "Could not reach the Roku at {ip}. Make sure it is powered on and on the same "
    "network as the bridge. If its IP changed, set roku_ip in config.json or pass "
    "--roku-ip (a DHCP reservation on your router keeps it stable)."
)
HINT_NOT_FOUND = (
    "No Roku found via SSDP discovery. Some routers/mesh systems block multicast: set "
    "roku_ip in config.json or start the bridge with --roku-ip <ip> (the Roku shows its "
    "IP under Settings › Network › About)."
)
HINT_ECP_ERROR = "The Roku rejected the command (HTTP {status}). Check the app id / parameters."

log = logging.getLogger("couch_bridge")


# --------------------------------------------------------------------------
# Config
# --------------------------------------------------------------------------

class ConfigError(Exception):
    pass


def _strip_json_comments(text: str) -> str:
    """Drop // line comments outside strings (the SPEC example uses them, and the
    channel's tools/gen_config.py accepts them too)."""
    out: list[str] = []
    i, n, in_str = 0, len(text), False
    while i < n:
        c = text[i]
        if in_str:
            out.append(c)
            if c == "\\" and i + 1 < n:
                out.append(text[i + 1])
                i += 1
            elif c == '"':
                in_str = False
        elif c == '"':
            in_str = True
            out.append(c)
        elif text.startswith("//", i):
            while i < n and text[i] != "\n":
                i += 1
            continue
        else:
            out.append(c)
        i += 1
    return "".join(out)


def load_config(path: str | None = None) -> tuple[dict[str, Any], str]:
    """Return (config, source). An explicit path must exist; otherwise fall back
    from ../config.json to ../config.example.json to built-in defaults."""
    if path:
        candidates = [path]
    else:
        candidates = [
            os.path.join(PROJECT_DIR, "config.json"),
            os.path.join(PROJECT_DIR, "config.example.json"),
        ]
    config = json.loads(json.dumps(DEFAULT_CONFIG))  # deep copy
    for candidate in candidates:
        if not os.path.isfile(candidate):
            continue
        try:
            with open(candidate, "r", encoding="utf-8") as fh:
                data = json.loads(_strip_json_comments(fh.read()))
        except (OSError, ValueError) as exc:
            raise ConfigError(f"could not read config {candidate}: {exc}") from exc
        if not isinstance(data, dict):
            raise ConfigError(f"config {candidate} must contain a JSON object")
        for key, value in data.items():
            if isinstance(value, dict) and isinstance(config.get(key), dict):
                config[key].update(value)
            else:
                config[key] = value
        return config, candidate
    if path:
        raise ConfigError(f"config file not found: {path}")
    return config, "built-in defaults"


# --------------------------------------------------------------------------
# Roku discovery + ECP client
# --------------------------------------------------------------------------

Target = tuple[str, int]


class RokuError(Exception):
    """ECP failure. Rendered to the phone as a 502 JSON body with a hint."""

    def __init__(self, message: str, hint: str | None = None, ecp_status: int | None = None):
        super().__init__(message)
        self.message = message
        self.hint = hint
        self.ecp_status = ecp_status


class RokuNotFound(RokuError):
    pass


class RokuUnreachable(RokuError):
    pass


class RokuForbidden(RokuError):
    pass


def parse_host_port(value: str, default_port: int = ECP_PORT) -> Target:
    """Accept '192.168.1.5', '192.168.1.5:8060' or 'http://192.168.1.5:8060/'."""
    value = value.strip()
    if "//" not in value:
        value = "//" + value
    parts = urllib.parse.urlsplit(value)
    if not parts.hostname:
        raise ValueError(f"invalid Roku address: {value!r}")
    try:
        port = parts.port or default_port
    except ValueError as exc:
        raise ValueError(f"invalid Roku address: {value!r}") from exc
    return parts.hostname, port


def format_target(target: Target | None) -> str | None:
    if target is None:
        return None
    host, port = target
    host_s = f"[{host}]" if ":" in host else host
    return host_s if port == ECP_PORT else f"{host_s}:{port}"


def parse_ssdp_response(data: bytes) -> Target | None:
    """Extract (host, port) from the LOCATION header of a roku:ecp SSDP reply."""
    text = data.decode("latin-1", errors="replace")
    lines = text.replace("\r\n", "\n").split("\n")
    headers: dict[str, str] = {}
    for line in lines[1:]:
        if ":" in line:
            key, val = line.split(":", 1)
            headers[key.strip().lower()] = val.strip()
    st = headers.get("st", "")
    if st and "roku:ecp" not in st.lower():
        return None
    location = headers.get("location")
    if not location:
        return None
    parts = urllib.parse.urlsplit(location)
    if not parts.hostname:
        return None
    try:
        port = parts.port or ECP_PORT
    except ValueError:
        return None
    return parts.hostname, port


def ssdp_discover(timeout: float = SSDP_TIMEOUT) -> Target | None:
    """Send an SSDP M-SEARCH for roku:ecp and return the first responder."""
    message = (
        "M-SEARCH * HTTP/1.1\r\n"
        f"Host: {SSDP_ADDR[0]}:{SSDP_ADDR[1]}\r\n"
        'Man: "ssdp:discover"\r\n'
        "ST: roku:ecp\r\n"
        "MX: 2\r\n"
        "\r\n"
    ).encode("ascii")
    sock = None
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
        # UDP is lossy; send twice.
        sock.sendto(message, SSDP_ADDR)
        sock.sendto(message, SSDP_ADDR)
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            sock.settimeout(remaining)
            try:
                data, _addr = sock.recvfrom(4096)
            except socket.timeout:
                return None
            found = parse_ssdp_response(data)
            if found:
                return found
    except OSError as exc:
        log.warning("SSDP discovery failed: %s", exc)
        return None
    finally:
        if sock is not None:
            sock.close()


class RokuLocator:
    """Resolves the Roku's address: a fixed configured one, or SSDP-discovered
    and cached (re-discovered when the cached address stops answering)."""

    def __init__(
        self,
        configured: str | None = None,
        discover: Callable[[float], Target | None] = ssdp_discover,
        discovery_timeout: float = SSDP_TIMEOUT,
        retry_after: float = DISCOVERY_RETRY_SECONDS,
    ):
        self.configured: Target | None = parse_host_port(configured) if configured else None
        self._discover = discover
        self._discovery_timeout = discovery_timeout
        self._retry_after = retry_after
        self._lock = threading.Lock()
        self._cached: Target | None = None
        self._last_failure = 0.0

    @property
    def uses_discovery(self) -> bool:
        return self.configured is None

    def current(self) -> Target | None:
        """Best known address without triggering discovery."""
        return self.configured or self._cached

    def get(self) -> Target:
        if self.configured:
            return self.configured
        with self._lock:
            if self._cached:
                return self._cached
            if self._last_failure and time.monotonic() - self._last_failure < self._retry_after:
                raise RokuNotFound("No Roku found on the network", hint=HINT_NOT_FOUND)
            return self._run_discovery()

    def rediscover(self, stale: Target) -> Target | None:
        """Called when `stale` stopped answering. Returns a new address or None."""
        if self.configured:
            return None
        with self._lock:
            if self._cached and self._cached != stale:
                return self._cached  # another thread already found a new one
            self._cached = None
            try:
                return self._run_discovery()
            except RokuNotFound:
                return None

    def _run_discovery(self) -> Target:
        log.info("discovering Roku via SSDP (ST: roku:ecp)...")
        found = self._discover(self._discovery_timeout)
        if found:
            self._cached = found
            self._last_failure = 0.0
            log.info("found Roku at %s", format_target(found))
            return found
        self._last_failure = time.monotonic()
        raise RokuNotFound("No Roku found on the network", hint=HINT_NOT_FOUND)


def _xml_text(root: ET.Element, tag: str) -> str | None:
    el = root.find(tag)
    if el is None or el.text is None:
        return None
    text = el.text.strip()
    return text or None


def _app_from_element(el: ET.Element) -> dict[str, Any]:
    return {
        "id": el.get("id"),
        "name": (el.text or "").strip(),
        "type": el.get("type"),
        "version": el.get("version"),
    }


class RokuClient:
    """Thin wrapper over Roku External Control Protocol (ECP)."""

    def __init__(self, locator: RokuLocator, timeout: float = ECP_TIMEOUT):
        self.locator = locator
        self.timeout = timeout
        # Never route LAN ECP traffic through an HTTP(S)_PROXY from the environment.
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    # -- transport ---------------------------------------------------------

    def request(self, method: str, path: str, query: str = "") -> tuple[int, Any, bytes]:
        target = self.locator.get()
        try:
            return self._do(target, method, path, query)
        except RokuUnreachable:
            if not self.locator.uses_discovery:
                raise
            fresh = self.locator.rediscover(target)
            if fresh is None or fresh == target:
                raise
            return self._do(fresh, method, path, query)

    def _do(self, target: Target, method: str, path: str, query: str) -> tuple[int, Any, bytes]:
        host, port = target
        host_s = f"[{host}]" if ":" in host else host
        url = f"http://{host_s}:{port}{path}"
        if query:
            url += "?" + query
        # ECP POSTs (keypress, launch, search) take an empty body.
        data = b"" if method == "POST" else None
        req = urllib.request.Request(url, data=data, method=method)
        try:
            with self._opener.open(req, timeout=self.timeout) as resp:
                return resp.status, resp.headers, resp.read()
        except urllib.error.HTTPError as exc:
            exc.close()
            if exc.code == 403:
                raise RokuForbidden(
                    f"Roku at {format_target(target)} returned 403 Forbidden for {method} {path}",
                    hint=HINT_PERMISSIVE,
                    ecp_status=403,
                ) from None
            raise RokuError(
                f"Roku at {format_target(target)} returned HTTP {exc.code} for {method} {path}",
                hint=HINT_ECP_ERROR.format(status=exc.code),
                ecp_status=exc.code,
            ) from None
        except (urllib.error.URLError, OSError) as exc:
            reason = getattr(exc, "reason", exc)
            raise RokuUnreachable(
                f"Roku at {format_target(target)} is unreachable ({reason})",
                hint=HINT_UNREACHABLE.format(ip=format_target(target)),
            ) from None

    def _xml(self, path: str) -> ET.Element:
        _status, _headers, body = self.request("GET", path)
        try:
            return ET.fromstring(body)
        except ET.ParseError as exc:
            raise RokuError(f"Roku returned invalid XML for {path}: {exc}") from None

    # -- queries -----------------------------------------------------------

    def device_info(self) -> dict[str, Any]:
        root = self._xml("/query/device-info")
        name = (
            _xml_text(root, "user-device-name")
            or _xml_text(root, "friendly-device-name")
            or _xml_text(root, "default-device-name")
            or _xml_text(root, "model-name")
            or "Roku"
        )
        return {
            "name": name,
            "model": _xml_text(root, "model-name"),
            "power_mode": _xml_text(root, "power-mode"),
        }

    def apps(self) -> list[dict[str, Any]]:
        root = self._xml("/query/apps")
        return [_app_from_element(el) for el in root.iter("app")]

    def active_app(self) -> dict[str, Any]:
        root = self._xml("/query/active-app")
        app_el = root.find("app")
        saver_el = root.find("screensaver")
        return {
            "app": _app_from_element(app_el) if app_el is not None else None,
            "screensaver": _app_from_element(saver_el) if saver_el is not None else None,
        }

    def icon(self, app_id: str) -> tuple[str, bytes]:
        _status, headers, body = self.request(
            "GET", "/query/icon/" + urllib.parse.quote(app_id, safe="")
        )
        ctype = headers.get("Content-Type") or "application/octet-stream"
        return ctype, body

    # -- commands ----------------------------------------------------------

    def keypress(self, key: str) -> None:
        self.request("POST", "/keypress/" + key)

    def literal(self, char: str) -> None:
        self.request("POST", "/keypress/Lit_" + urllib.parse.quote(char, safe=""))

    def launch(self, app_id: str, params: dict[str, str] | None = None) -> None:
        query = urllib.parse.urlencode(params or {}, quote_via=urllib.parse.quote)
        self.request("POST", "/launch/" + urllib.parse.quote(app_id, safe=""), query)

    def search(self, params: dict[str, str]) -> None:
        query = urllib.parse.urlencode(params, quote_via=urllib.parse.quote)
        self.request("POST", "/search/browse", query)


# --------------------------------------------------------------------------
# HTTP server
# --------------------------------------------------------------------------

MIME_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".htm": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".mjs": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".webmanifest": "application/manifest+json; charset=utf-8",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".svg": "image/svg+xml",
    ".ico": "image/x-icon",
    ".txt": "text/plain; charset=utf-8",
}


class BadRequest(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.message = message
        self.status = status


class IconCache:
    """Small thread-safe LRU of app icons (content-type, bytes)."""

    def __init__(self, max_entries: int = ICON_CACHE_ENTRIES):
        self._max = max_entries
        self._data: OrderedDict[str, tuple[str, bytes]] = OrderedDict()
        self._lock = threading.Lock()

    def get(self, key: str) -> tuple[str, bytes] | None:
        with self._lock:
            item = self._data.get(key)
            if item is not None:
                self._data.move_to_end(key)
            return item

    def put(self, key: str, value: tuple[str, bytes]) -> None:
        with self._lock:
            self._data[key] = value
            self._data.move_to_end(key)
            while len(self._data) > self._max:
                self._data.popitem(last=False)

    def clear(self) -> None:
        with self._lock:
            self._data.clear()


class BridgeServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, config: dict[str, Any], roku: RokuClient, static_dir: str = STATIC_DIR):
        self.config = config
        self.roku = roku
        self.static_dir = os.path.realpath(static_dir)
        self.icon_cache = IconCache()
        self.favorites = [str(f) for f in (config.get("favorites") or [])]
        super().__init__(address, BridgeHandler)


ROUTES: list[tuple[str, re.Pattern[str], str]] = [
    ("GET", re.compile(r"^/api/status$"), "api_status"),
    ("POST", re.compile(r"^/api/key/([^/]+)$"), "api_key"),
    ("POST", re.compile(r"^/api/text$"), "api_text"),
    ("GET", re.compile(r"^/api/apps$"), "api_apps"),
    ("GET", re.compile(r"^/api/apps/([^/]+)/icon$"), "api_icon"),
    ("POST", re.compile(r"^/api/launch/([^/]+)$"), "api_launch"),
    ("POST", re.compile(r"^/api/dashboard$"), "api_dashboard"),
    ("POST", re.compile(r"^/api/search$"), "api_search"),
    ("GET", re.compile(r"^/api/active-app$"), "api_active_app"),
]


class BridgeHandler(BaseHTTPRequestHandler):
    server: BridgeServer
    server_version = f"couch-bridge/{__version__}"
    protocol_version = "HTTP/1.1"

    # -- plumbing ----------------------------------------------------------

    def log_request(self, code="-", size="-"):  # replaced by our own one-liner
        pass

    def log_message(self, format, *args):
        log.debug("%s - %s", self.address_string(), format % args)

    def do_GET(self):
        self._handle("GET")

    def do_HEAD(self):
        self._handle("HEAD")

    def do_POST(self):
        self._handle("POST")

    def do_PUT(self):
        self._handle("PUT")

    def do_DELETE(self):
        self._handle("DELETE")

    def do_PATCH(self):
        self._handle("PATCH")

    def _handle(self, method: str) -> None:
        start = time.monotonic()
        self._status = 0
        path = urllib.parse.urlsplit(self.path).path
        try:
            if path == "/api" or path.startswith("/api/"):
                self._dispatch_api(method, path)
            elif method in ("GET", "HEAD"):
                self._serve_static(path)
            else:
                self._drain_body()  # keep the keep-alive stream in sync
                self._json(405, {"error": f"method {method} not allowed"}, {"Allow": "GET, HEAD"})
        except BadRequest as exc:
            self._json(exc.status, {"error": exc.message})
        except RokuError as exc:
            body: dict[str, Any] = {
                "error": exc.message,
                "roku_ip": format_target(self.server.roku.locator.current()),
            }
            if exc.hint:
                body["hint"] = exc.hint
            if exc.ecp_status is not None:
                body["ecp_status"] = exc.ecp_status
            self._json(502, body)
        except (BrokenPipeError, ConnectionResetError):
            self.close_connection = True
        except Exception:  # pragma: no cover - defensive
            log.exception("unhandled error for %s %s", method, self.path)
            try:
                self._json(500, {"error": "internal server error"})
            except OSError:
                self.close_connection = True
        finally:
            elapsed_ms = (time.monotonic() - start) * 1000
            log.info("%s %s %s %s %.0fms", self.client_address[0], method, self.path,
                     self._status or "-", elapsed_ms)

    def _send(self, status: int, body: bytes, ctype: str, headers: dict[str, str] | None = None) -> None:
        self._status = status
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, status: int, payload: Any, headers: dict[str, str] | None = None) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        hdrs = {"Cache-Control": "no-store"}
        hdrs.update(headers or {})
        self._send(status, body, "application/json; charset=utf-8", hdrs)

    def _read_json(self, required: bool) -> dict[str, Any]:
        length_s = self.headers.get("Content-Length")
        try:
            length = int(length_s) if length_s else 0
        except ValueError:
            raise BadRequest("invalid Content-Length") from None
        if length < 0:
            raise BadRequest("invalid Content-Length")
        if length > MAX_BODY_BYTES:
            self.close_connection = True
            raise BadRequest("request body too large", status=413)
        raw = self.rfile.read(length) if length else b""
        if not raw.strip():
            if required:
                raise BadRequest("JSON body required")
            return {}
        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            raise BadRequest("body must be valid JSON") from None
        if not isinstance(data, dict):
            raise BadRequest("JSON body must be an object")
        return data

    def _dispatch_api(self, method: str, path: str) -> None:
        path_matched = False
        for route_method, pattern, handler_name in ROUTES:
            match = pattern.match(path)
            if not match:
                continue
            path_matched = True
            if route_method == method or (method == "HEAD" and route_method == "GET"):
                args = [urllib.parse.unquote(g) for g in match.groups()]
                getattr(self, handler_name)(*args)
                return
        if path_matched:
            allowed = sorted({m for m, p, _ in ROUTES if p.match(path)})
            # Drain any body so keep-alive stays in sync.
            self._drain_body()
            self._json(405, {"error": f"method {method} not allowed"}, {"Allow": ", ".join(allowed)})
            return
        self._drain_body()
        self._json(404, {"error": f"unknown API route: {path}"})

    def _drain_body(self) -> None:
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if 0 < length <= MAX_BODY_BYTES:
            self.rfile.read(length)
        elif length > MAX_BODY_BYTES:
            self.close_connection = True

    # -- API handlers ------------------------------------------------------

    def api_status(self) -> None:
        roku = self.server.roku
        try:
            device = roku.device_info()
        except RokuError as exc:
            payload: dict[str, Any] = {
                "roku_ip": format_target(roku.locator.current()),
                "reachable": False,
                "device": None,
                "error": exc.message,
            }
            if exc.hint:
                payload["hint"] = exc.hint
            if exc.ecp_status is not None:
                payload["ecp_status"] = exc.ecp_status
            self._json(200, payload)
            return
        self._json(200, {
            "roku_ip": format_target(roku.locator.current()),
            "reachable": True,
            "device": device,
        })

    def api_key(self, key: str) -> None:
        self._drain_body()
        canonical = _KEY_LOOKUP.get(key.lower())
        if canonical is None:
            raise BadRequest(f"key {key!r} is not allowed; valid keys: {', '.join(ALLOWED_KEYS)}")
        self.server.roku.keypress(canonical)
        self._json(200, {"ok": True, "key": canonical})

    def api_text(self) -> None:
        data = self._read_json(required=True)
        text = data.get("text")
        if not isinstance(text, str) or text == "":
            raise BadRequest("'text' must be a non-empty string")
        if len(text) > MAX_TEXT_CHARS:
            raise BadRequest(f"'text' is limited to {MAX_TEXT_CHARS} characters")
        sent = 0
        try:
            for char in text:
                self.server.roku.literal(char)
                sent += 1
        except RokuError as exc:
            exc.message += f" (sent {sent} of {len(text)} characters)"
            raise
        self._json(200, {"ok": True, "sent": sent})

    def api_apps(self) -> None:
        apps = self.server.roku.apps()
        favorites = self.server.favorites
        rank = {app_id: i for i, app_id in enumerate(favorites)}
        for app in apps:
            app["favorite"] = app["id"] in rank
        favs = sorted((a for a in apps if a["favorite"]), key=lambda a: rank[a["id"]])
        rest = [a for a in apps if not a["favorite"]]
        self._json(200, {"apps": favs + rest})

    def api_icon(self, app_id: str) -> None:
        if not APP_ID_RE.match(app_id):
            raise BadRequest("invalid app id")
        cache = self.server.icon_cache
        cached = cache.get(app_id)
        if cached is None:
            cached = self.server.roku.icon(app_id)
            cache.put(app_id, cached)
        ctype, body = cached
        self._send(200, body, ctype, {"Cache-Control": "public, max-age=86400"})

    def api_launch(self, app_id: str) -> None:
        data = self._read_json(required=False)
        if not APP_ID_RE.match(app_id):
            raise BadRequest("invalid app id")
        params: dict[str, str] = {}
        for field in ("contentId", "mediaType"):
            value = data.get(field)
            if value is None or value == "":
                continue
            if not isinstance(value, (str, int)) or isinstance(value, bool):
                raise BadRequest(f"'{field}' must be a string")
            params[field] = str(value)
        self.server.roku.launch(app_id, params)
        self._json(200, {"ok": True, "id": app_id, "params": params})

    def api_dashboard(self) -> None:
        self._drain_body()
        self.server.roku.launch("dev")
        self._json(200, {"ok": True, "id": "dev"})

    def api_search(self) -> None:
        data = self._read_json(required=True)
        keyword = data.get("keyword")
        if not isinstance(keyword, str) or not keyword.strip():
            raise BadRequest("'keyword' must be a non-empty string")
        keyword = keyword.strip()
        if len(keyword) > MAX_KEYWORD_CHARS:
            raise BadRequest(f"'keyword' is limited to {MAX_KEYWORD_CHARS} characters")
        params = {"keyword": keyword}
        provider = data.get("providerId", data.get("provider_id"))
        if provider not in (None, ""):
            params["provider-id"] = str(provider)
        if data.get("launch") is True:
            params["launch"] = "true"
        self.server.roku.search(params)
        self._json(200, {"ok": True, "keyword": keyword})

    def api_active_app(self) -> None:
        self._json(200, self.server.roku.active_app())

    # -- static files ------------------------------------------------------

    def _serve_static(self, url_path: str) -> None:
        path = urllib.parse.unquote(url_path)
        if path.endswith("/"):
            path += "index.html"
        parts = [p for p in path.split("/") if p]
        if "\x00" in path or any(p in (".", "..") or p.startswith(".") or "\\" in p for p in parts):
            self._json(403, {"error": "forbidden"})
            return
        root = self.server.static_dir
        full = os.path.realpath(os.path.join(root, *parts))
        if os.path.commonpath([root, full]) != root:
            self._json(403, {"error": "forbidden"})
            return
        if os.path.isdir(full):
            full = os.path.join(full, "index.html")
        if not os.path.isfile(full):
            self._json(404, {"error": f"not found: {url_path}"})
            return
        name = os.path.basename(full)
        ext = os.path.splitext(name)[1].lower()
        if name == "manifest.json":
            ctype = "application/manifest+json; charset=utf-8"
        else:
            ctype = MIME_TYPES.get(ext, "application/octet-stream")
        with open(full, "rb") as fh:
            body = fh.read()
        self._send(200, body, ctype, {"Cache-Control": "no-cache", "X-Content-Type-Options": "nosniff"})


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------

def make_server(config: dict[str, Any], host: str = "0.0.0.0", port: int | None = None,
                locator: RokuLocator | None = None, static_dir: str = STATIC_DIR) -> BridgeServer:
    if locator is None:
        locator = RokuLocator(config.get("roku_ip") or None)
    roku = RokuClient(locator)
    if port is None:
        port = int(config.get("bridge_port") or DEFAULT_CONFIG["bridge_port"])
    return BridgeServer((host, port), config, roku, static_dir)


def local_ips() -> list[str]:
    """LAN address(es) of this machine (UDP 'connect' trick sends no packets)."""
    ips: list[str] = []
    for probe in ("10.255.255.255", "8.8.8.8"):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                s.connect((probe, 1))
                ip = s.getsockname()[0]
            if ip and not ip.startswith("127.") and ip not in ips:
                ips.append(ip)
        except OSError:
            pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if not ip.startswith("127.") and ip not in ips:
                ips.append(ip)
    except OSError:
        pass
    return ips or ["127.0.0.1"]


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="roku-couch bridge + phone remote server")
    parser.add_argument("--config", help="path to config.json (default: ../config.json, "
                        "falling back to ../config.example.json, then built-in defaults)")
    parser.add_argument("--roku-ip", help="Roku address (ip or ip:port); skips SSDP discovery")
    parser.add_argument("--port", type=int, help="port to listen on (default: bridge_port, 8787)")
    parser.add_argument("--host", default="0.0.0.0", help="address to bind (default: 0.0.0.0)")
    parser.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(message)s",
        datefmt="%H:%M:%S",
    )
    try:
        config, source = load_config(args.config)
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if args.roku_ip:
        config["roku_ip"] = args.roku_ip
    try:
        locator = RokuLocator(config.get("roku_ip") or None)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    try:
        server = make_server(config, args.host, args.port, locator=locator)
    except OSError as exc:
        print(f"error: could not listen on {args.host}:{args.port or config.get('bridge_port')}: {exc}",
              file=sys.stderr)
        return 1

    port = server.server_address[1]
    print(f"roku-couch bridge {__version__} — config: {source}")
    print(f"Listening on http://{args.host}:{port}/")
    print("Open the remote on your phone (same Wi-Fi):")
    for ip in local_ips():
        print(f"    http://{ip}:{port}/")
    print("No authentication: keep this on your LAN, never port-forward it.")
    if locator.configured:
        print(f"Roku: {format_target(locator.configured)} (configured)")
    else:
        print("Roku: discovering via SSDP...")

        def _initial_discovery() -> None:
            try:
                locator.get()
            except RokuNotFound:
                log.warning("no Roku answered SSDP. %s", HINT_NOT_FOUND)

        discovery_thread = threading.Thread(target=_initial_discovery, daemon=True)
    print("Press Ctrl+C to stop.", flush=True)
    if not locator.configured:
        discovery_thread.start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())

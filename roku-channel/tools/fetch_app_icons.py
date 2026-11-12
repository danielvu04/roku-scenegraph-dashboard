#!/usr/bin/env python3
"""Save the installed app icons from the Roku's ECP service to local-icons/.

These icons belong to their publishers. They're for private use on your own
TV: local-icons/ is git-ignored, so the icons are never committed or published.

Usage (run on the same network as the Roku):
    python roku-channel/tools/fetch_app_icons.py [--ip 192.168.1.124] [app ids...]
"""
import argparse
import json
import os
import sys
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
CHANNEL = os.path.dirname(HERE)
PROJECT = os.path.dirname(CHANNEL)
OUT = os.path.join(CHANNEL, "local-icons")
DEFAULT_IDS = ["12", "837", "13", "2285"]  # Netflix, YouTube, Prime Video, Hulu


def roku_ip_from_config():
    try:
        with open(os.path.join(PROJECT, "config.json"), encoding="utf-8") as f:
            return json.load(f).get("roku_ip") or ""
    except (OSError, ValueError):
        return ""


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--ip", default="", help="Roku address (defaults to roku_ip in config.json)")
    ap.add_argument("ids", nargs="*", default=DEFAULT_IDS)
    args = ap.parse_args()

    ip = args.ip or roku_ip_from_config()
    if not ip:
        print("fetch_app_icons: no Roku address (use --ip or set roku_ip in config.json)")
        return 1
    os.makedirs(OUT, exist_ok=True)
    saved = 0
    for app_id in args.ids:
        url = "http://%s:8060/query/icon/%s" % (ip, app_id)
        try:
            with urllib.request.urlopen(url, timeout=5) as resp:
                data = resp.read()
                ctype = resp.headers.get_content_type()
        except OSError as exc:
            print("fetch_app_icons: skipped %s (%s)" % (app_id, exc))
            continue
        ext = ".jpg" if ctype == "image/jpeg" else ".png"
        path = os.path.join(OUT, "app_icon_%s%s" % (app_id, ext))
        with open(path, "wb") as f:
            f.write(data)
        print("fetch_app_icons: saved %s (%d KB)" % (os.path.basename(path), len(data) // 1024))
        saved += 1
    return 0 if saved else 1


if __name__ == "__main__":
    sys.exit(main())

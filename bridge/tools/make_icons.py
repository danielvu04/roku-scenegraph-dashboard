#!/usr/bin/env python3
"""Generate the phone-remote PWA icons into ../static/icons/.

Requires Pillow (dev-time only; the bridge itself is stdlib-only):
    python3 -m pip install Pillow
    python3 tools/make_icons.py
"""

from __future__ import annotations

import os

from PIL import Image, ImageDraw

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(os.path.dirname(HERE), "static", "icons")

BG_TOP = (44, 22, 84)
BG_BOTTOM = (14, 12, 28)
RING = (167, 139, 250)
ACCENT = (236, 233, 255)


def draw_icon(size: int, maskable: bool = False) -> Image.Image:
    scale = 4  # supersample for smooth edges
    s = size * scale
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))

    # Vertical gradient background.
    grad = Image.new("RGBA", (s, s))
    gd = ImageDraw.Draw(grad)
    for y in range(s):
        t = y / (s - 1)
        color = tuple(round(BG_TOP[i] + (BG_BOTTOM[i] - BG_TOP[i]) * t) for i in range(3)) + (255,)
        gd.line([(0, y), (s, y)], fill=color)

    mask = Image.new("L", (s, s), 0)
    md = ImageDraw.Draw(mask)
    if maskable:
        md.rectangle([0, 0, s, s], fill=255)  # full bleed; OS applies its own mask
    else:
        md.rounded_rectangle([0, 0, s - 1, s - 1], radius=int(s * 0.22), fill=255)
    img.paste(grad, (0, 0), mask)

    d = ImageDraw.Draw(img)
    # Maskable icons keep content inside the central 80% safe zone.
    content = 0.62 if maskable else 0.78
    cx = cy = s / 2
    r = s * content / 2
    ring_w = max(2, int(r * 0.16))
    d.ellipse([cx - r, cy - r, cx + r, cy + r], outline=RING, width=ring_w)

    # D-pad chevrons (up/down/left/right triangles).
    tri = r * 0.22
    off = r * 0.62
    for dx, dy in ((0, -1), (0, 1), (-1, 0), (1, 0)):
        px, py = cx + dx * off, cy + dy * off
        # Apex points outward from the centre.
        if dy:
            pts = [(px, py + dy * tri * 0.6), (px - tri, py - dy * tri * 0.4), (px + tri, py - dy * tri * 0.4)]
        else:
            pts = [(px + dx * tri * 0.6, py), (px - dx * tri * 0.4, py - tri), (px - dx * tri * 0.4, py + tri)]
        d.polygon(pts, fill=ACCENT)

    # OK button.
    ok = r * 0.3
    d.ellipse([cx - ok, cy - ok, cx + ok, cy + ok], fill=RING)

    return img.resize((size, size), Image.LANCZOS)


def main() -> None:
    os.makedirs(OUT, exist_ok=True)
    outputs = {
        "icon-192.png": draw_icon(192),
        "icon-512.png": draw_icon(512),
        "icon-maskable-512.png": draw_icon(512, maskable=True),
        "apple-touch-icon.png": draw_icon(180, maskable=True).convert("RGB"),
        "favicon-32.png": draw_icon(32),
    }
    for name, img in outputs.items():
        path = os.path.join(OUT, name)
        img.save(path, optimize=True)
        print("wrote", os.path.relpath(path, os.path.dirname(HERE)))


if __name__ == "__main__":
    main()

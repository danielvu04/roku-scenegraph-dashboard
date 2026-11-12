#!/usr/bin/env python3
"""Turn personal photos in local-bg/ into 1920x1080 channel backgrounds.

Writes images/local_bg_<name>.jpg. Both local-bg/ and the generated
local_bg_*.jpg files are git-ignored, so private or copyrighted images never
reach the repo or a Channel Store upload. build.sh runs this before packaging.
"""
import glob
import os
import sys

from PIL import Image, ImageDraw, ImageEnhance

HERE = os.path.dirname(os.path.abspath(__file__))
CHANNEL = os.path.dirname(HERE)
SRC = os.path.join(CHANNEL, "local-bg")
OUT = os.path.join(CHANNEL, "images")
W, H = 1920, 1080
DIM = 0.6  # keeps the white clock and date readable on bright photos


def cover(im):
    """Scale to fill 1920x1080, then center-crop the overflow."""
    scale = max(W / im.width, H / im.height)
    im = im.resize((round(im.width * scale), round(im.height * scale)), Image.LANCZOS)
    left = (im.width - W) // 2
    top = (im.height - H) // 2
    return im.crop((left, top, left + W, top + H))


def main():
    convert_icons()
    files = sorted(
        p for ext in ("png", "jpg", "jpeg") for p in glob.glob(os.path.join(SRC, "*." + ext))
    )
    if not files:
        print("prep_local_bg: no images in local-bg/ (nothing to do)")
        return 0
    os.makedirs(OUT, exist_ok=True)
    for path in files:
        im = ImageEnhance.Brightness(cover(Image.open(path).convert("RGB"))).enhance(DIM)
        name = os.path.splitext(os.path.basename(path))[0]
        dest = os.path.join(OUT, "local_bg_%s.jpg" % name)
        im.save(dest, "JPEG", quality=84, optimize=True)  # baseline: Roku cannot decode progressive JPEG
        print("prep_local_bg: %s -> %s (%d KB)"
              % (os.path.basename(path), os.path.basename(dest), os.path.getsize(dest) // 1024))
    return 0


DARK_BG = (28, 28, 30)     # Apple dark surface
LIGHT_TEXT = (242, 242, 247)


def dark_adapt(im):
    """Light-background icons: swap white to a dark surface and black wordmarks to white.
    Colored pixels (logo reds, blues, greens) are left alone. Dark icons are returned as-is."""
    px = im.load()
    corner = px[2, 2][:3]
    if sum(corner) / 3 < 200:
        return im
    w, h = im.size
    for y in range(h):
        for x in range(w):
            r, g, bl, al = px[x, y]
            lum = 0.2126 * r + 0.7152 * g + 0.0722 * bl
            sat = max(r, g, bl) - min(r, g, bl)
            if sat < 40 and lum > 200:
                px[x, y] = DARK_BG + (al,)
            elif sat < 40 and lum < 90:
                px[x, y] = LIGHT_TEXT + (al,)
    return im


def convert_icons():
    """local-icons/app_icon_<id>.* -> images/app_icon_<id>.png at 384x288.

    One rounded tile per app, like Apple TV: the logo fills the tile, dark-adapted
    for light backgrounds, with a hairline edge so the shape reads on a black backdrop.
    """
    w, h, r, ss = 384, 288, 40, 3
    for path in sorted(glob.glob(os.path.join(CHANNEL, "local-icons", "app_icon_*"))):
        name = os.path.splitext(os.path.basename(path))[0]
        im = dark_adapt(Image.open(path).convert("RGBA").resize((w, h), Image.LANCZOS))
        mask = Image.new("L", (w * ss, h * ss), 0)
        ImageDraw.Draw(mask).rounded_rectangle((0, 0, w * ss - 1, h * ss - 1), r * ss, fill=255)
        im.putalpha(mask.resize((w, h), Image.LANCZOS))
        edge = Image.new("RGBA", (w * ss, h * ss), (0, 0, 0, 0))
        ImageDraw.Draw(edge).rounded_rectangle((0, 0, w * ss - 1, h * ss - 1), r * ss, outline=(255, 255, 255, 46), width=2 * ss)
        im = Image.alpha_composite(im, edge.resize((w, h), Image.LANCZOS))
        im.save(os.path.join(OUT, name + ".png"), optimize=True)
        print("prep_local_bg: %s -> %s.png" % (os.path.basename(path), name))


if __name__ == "__main__":
    sys.exit(main())

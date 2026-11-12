#!/usr/bin/env python3
"""Generate the channel's artwork: backgrounds, icons and splash screen.

Everything is procedural (gradients + blurred light blobs) so the package
ships no third-party images. Requires Pillow (pip install Pillow).

    python3 tools/gen_assets.py            # writes into ../images
    python3 tools/gen_assets.py --out DIR

Backgrounds are written as images/bg_01.jpg .. bg_NN.jpg; build.sh lists
whatever bg_*.jpg files exist into generated_config.brs, so adding or
removing palettes here needs no other change.
"""

import argparse
import math
import os
import random
import sys

try:
    from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont
except ImportError:  # pragma: no cover
    sys.exit("gen_assets.py needs Pillow: python3 -m pip install Pillow")

W, H = 1920, 1080
# Work at quarter resolution: everything is soft, so upscaling is invisible
# and generation stays fast.
SW, SH = W // 4, H // 4

# Each palette: vertical gradient stops (position, rgb) and light blobs
# (rgb, cx, cy, radius, strength) in 0..1 coordinates of the small canvas.
PALETTES = [
    {   # deep dusk: indigo into ember
        "stops": [(0.0, (14, 16, 44)), (0.55, (40, 26, 72)), (1.0, (92, 40, 58))],
        "blobs": [((236, 120, 92), 0.78, 0.92, 0.45, 0.45),
                  ((120, 80, 200), 0.15, 0.25, 0.40, 0.35)],
    },
    {   # northern lights over navy
        "stops": [(0.0, (6, 12, 30)), (0.6, (10, 30, 52)), (1.0, (8, 20, 34))],
        "blobs": [((40, 210, 160), 0.30, 0.35, 0.30, 0.45),
                  ((60, 150, 220), 0.62, 0.28, 0.28, 0.35),
                  ((150, 90, 220), 0.85, 0.45, 0.25, 0.30)],
        "aurora": True,
    },
    {   # plum night
        "stops": [(0.0, (22, 10, 34)), (0.5, (48, 18, 60)), (1.0, (18, 10, 30))],
        "blobs": [((210, 80, 160), 0.70, 0.30, 0.40, 0.35),
                  ((80, 60, 190), 0.20, 0.80, 0.45, 0.35)],
    },
    {   # teal ocean depth
        "stops": [(0.0, (6, 26, 36)), (0.5, (8, 46, 58)), (1.0, (4, 18, 28))],
        "blobs": [((40, 170, 180), 0.25, 0.30, 0.38, 0.35),
                  ((30, 90, 160), 0.80, 0.75, 0.45, 0.35)],
    },
    {   # last light: slate to amber horizon
        "stops": [(0.0, (16, 22, 40)), (0.62, (42, 44, 70)), (1.0, (110, 66, 46))],
        "dim": 0.25,
        "blobs": [((250, 170, 90), 0.50, 1.00, 0.55, 0.40),
                  ((90, 110, 170), 0.10, 0.15, 0.35, 0.25)],
    },
    {   # forest night
        "stops": [(0.0, (8, 20, 18)), (0.55, (16, 40, 32)), (1.0, (10, 24, 22))],
        "blobs": [((80, 180, 120), 0.75, 0.25, 0.35, 0.30),
                  ((30, 110, 120), 0.20, 0.70, 0.45, 0.30)],
    },
    {   # midnight rose
        "stops": [(0.0, (18, 14, 30)), (0.5, (40, 22, 46)), (1.0, (70, 30, 50))],
        "blobs": [((240, 110, 130), 0.30, 0.85, 0.40, 0.35),
                  ((120, 90, 200), 0.80, 0.20, 0.35, 0.30)],
    },
    {   # blue hour
        "stops": [(0.0, (8, 14, 40)), (0.5, (20, 40, 90)), (1.0, (30, 50, 96))],
        "dim": 0.28,
        "blobs": [((120, 170, 255), 0.65, 0.75, 0.45, 0.35),
                  ((200, 140, 230), 0.15, 0.35, 0.30, 0.25)],
        "aurora": True,
    },
]


def lerp(a, b, t):
    return a + (b - a) * t


def gradient(w, h, stops):
    """Vertical multi-stop gradient with a slight diagonal tilt."""
    col = Image.new("RGB", (1, 256))
    px = col.load()
    for y in range(256):
        t = y / 255.0
        for i in range(len(stops) - 1):
            p0, c0 = stops[i]
            p1, c1 = stops[i + 1]
            if p0 <= t <= p1:
                f = (t - p0) / (p1 - p0) if p1 > p0 else 0
                f = f * f * (3 - 2 * f)  # smoothstep between stops
                px[0, y] = tuple(int(lerp(c0[k], c1[k], f)) for k in range(3))
                break
    img = col.resize((w + h // 3, h + h // 3), Image.BICUBIC)
    img = img.rotate(-8, resample=Image.BICUBIC)
    left = (img.width - w) // 2
    top = (img.height - h) // 2
    return img.crop((left, top, left + w, top + h))


def add_blob(base, rgb, cx, cy, r, strength):
    w, h = base.size
    mask = Image.new("L", (w, h), 0)
    d = ImageDraw.Draw(mask)
    rx, ry = r * w, r * w * 0.75
    d.ellipse((cx * w - rx, cy * h - ry, cx * w + rx, cy * h + ry),
              fill=int(255 * strength))
    mask = mask.filter(ImageFilter.GaussianBlur(r * w * 0.5))
    layer = Image.new("RGB", (w, h), rgb)
    lit = ImageChops.screen(base, layer)
    return Image.composite(lit, base, mask)


def add_aurora(base, rng, colors):
    """Soft wavy ribbons, blurred heavily so they read as aurora curtains."""
    w, h = base.size
    out = base
    for i, rgb in enumerate(colors):
        mask = Image.new("L", (w, h), 0)
        d = ImageDraw.Draw(mask)
        phase = rng.uniform(0, math.tau)
        amp = h * rng.uniform(0.06, 0.12)
        y0 = h * (0.22 + 0.10 * i)
        pts = []
        for x in range(0, w + 8, 4):
            y = y0 + amp * math.sin(x / w * math.tau * 1.3 + phase)
            pts.append((x, y))
        d.line(pts, fill=150, width=max(6, h // 14))
        mask = mask.filter(ImageFilter.GaussianBlur(h / 18))
        layer = Image.new("RGB", (w, h), rgb)
        out = Image.composite(ImageChops.screen(out, layer), out, mask)
    return out


def vignette(img, amount=0.55):
    w, h = img.size
    mask = Image.new("L", (w, h), 0)
    d = ImageDraw.Draw(mask)
    d.ellipse((-w * 0.25, -h * 0.35, w * 1.25, h * 1.35), fill=255)
    mask = mask.filter(ImageFilter.GaussianBlur(w / 8))
    dark = Image.new("RGB", (w, h), (0, 0, 0))
    faded = Image.blend(img, dark, amount)
    return Image.composite(img, faded, mask)


def dither(img, rng, sigma=2.2):
    """Fine grain (about +/-2 levels) hides 8-bit banding on TV panels."""
    random.seed(rng.random())
    noise = Image.effect_noise(img.size, sigma * 4).point(
        lambda v: max(0, min(255, int(128 + (v - 128) / 4)))).convert("RGB")
    return ImageChops.add(img, noise, scale=1.0, offset=-128)


def make_background(pal, seed):
    rng = random.Random(seed)
    img = gradient(SW, SH, pal["stops"])
    for rgb, cx, cy, r, s in pal["blobs"]:
        img = add_blob(img, rgb, cx, cy, r, s)
    if pal.get("aurora"):
        img = add_aurora(img, rng, [b[0] for b in pal["blobs"][:2]])
    img = vignette(img)
    # Global dim so white text always has contrast, whatever the palette.
    img = Image.blend(img, Image.new("RGB", img.size, (0, 0, 0)), pal.get("dim", 0.15))
    img = img.resize((W, H), Image.BICUBIC)
    img = img.filter(ImageFilter.GaussianBlur(2))
    return dither(img, rng)


def find_font(size, bold=True):
    candidates = [
        "/usr/share/fonts/opentype/inter/Inter-Bold.otf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/Library/Fonts/Arial Bold.ttf",
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
        "C:/Windows/Fonts/arialbd.ttf",
        "DejaVuSans-Bold.ttf",
        "Arial Bold.ttf",
    ] if bold else [
        "/usr/share/fonts/opentype/inter/Inter-Regular.otf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/System/Library/Fonts/Supplemental/Arial.ttf",
        "C:/Windows/Fonts/arial.ttf",
        "DejaVuSans.ttf",
    ]
    for c in candidates:
        try:
            return ImageFont.truetype(c, size)
        except OSError:
            continue
    try:
        return ImageFont.load_default(size=size)  # Pillow >= 10.1
    except TypeError:
        return ImageFont.load_default()


def fit_font(d, text, max_w, size):
    font = find_font(size)
    while size > 8 and d.textlength(text, font=font) > max_w:
        size -= 2
        font = find_font(size)
    return font


def draw_clock_glyph(d, cx, cy, r, width, color):
    d.ellipse((cx - r, cy - r, cx + r, cy + r), outline=color, width=width)
    # hands at ~10:10
    for ang, ln in ((-60, 0.55), (60, 0.78)):
        a = math.radians(ang - 90)
        d.line((cx, cy, cx + math.cos(a) * r * ln, cy + math.sin(a) * r * ln),
               fill=color, width=width)
    d.ellipse((cx - width, cy - width, cx + width, cy + width), fill=color)


def make_icon(w, h, pal):
    ss = 3  # supersample for clean edges
    bg = make_background(pal, 7).resize((w * ss, h * ss), Image.LANCZOS)
    d = ImageDraw.Draw(bg)
    white = (245, 245, 250)
    r = int(h * ss * 0.22)
    draw_clock_glyph(d, w * ss // 2, int(h * ss * 0.38), r, max(3, int(r * 0.09)), white)
    text = "Couch Dashboard"
    font = fit_font(d, text, w * ss * 0.86, int(h * ss * 0.13))
    tw = d.textlength(text, font=font)
    d.text(((w * ss - tw) / 2, h * ss * 0.70), text, font=font, fill=white)
    return bg.resize((w, h), Image.LANCZOS)


def make_splash(pal):
    img = make_background(pal, 11)
    d = ImageDraw.Draw(img)
    white = (240, 240, 248)
    draw_clock_glyph(d, W // 2, int(H * 0.42), 120, 10, white)
    font = find_font(72)
    text = "Couch Dashboard"
    tw = d.textlength(text, font=font)
    d.text(((W - tw) / 2, H * 0.60), text, font=font, fill=white)
    return img


# ---- weather condition icons (white line art on transparent) -------------

def _sun(d, cx, cy, r, lw, col):
    d.ellipse((cx - r, cy - r, cx + r, cy + r), outline=col, width=lw)
    for k in range(8):
        a = k * math.pi / 4
        d.line((cx + math.cos(a) * r * 1.45, cy + math.sin(a) * r * 1.45,
                cx + math.cos(a) * r * 1.9, cy + math.sin(a) * r * 1.9), fill=col, width=lw)


def _moon(img, cx, cy, r, col):
    m = Image.new("L", img.size, 0)
    md = ImageDraw.Draw(m)
    md.ellipse((cx - r, cy - r, cx + r, cy + r), fill=255)
    md.ellipse((cx - r * 0.35, cy - r * 1.15, cx + r * 1.45, cy + r * 0.65), fill=0)
    img.paste(Image.new("RGBA", img.size, col), (0, 0), m)


def _cloud(img, x, y, s, col, cut=None):
    """Filled cloud at (x, y) top-left, width ~ 2.2*s."""
    m = Image.new("L", img.size, 0)
    md = ImageDraw.Draw(m)
    md.ellipse((x, y + s * 0.45, x + s * 0.9, y + s * 1.25), fill=255)
    md.ellipse((x + s * 0.45, y, x + s * 1.55, y + s * 1.1), fill=255)
    md.ellipse((x + s * 1.2, y + s * 0.35, x + s * 2.1, y + s * 1.25), fill=255)
    md.rectangle((x + s * 0.45, y + s * 0.8, x + s * 1.65, y + s * 1.25), fill=255)
    if cut:  # punch an outline gap so overlapping shapes stay readable
        cm = m.filter(ImageFilter.MaxFilter(cut))
        img.paste(Image.new("RGBA", img.size, (0, 0, 0, 0)), (0, 0), cm)
    img.paste(Image.new("RGBA", img.size, col), (0, 0), m)


def make_weather_icons(out, size=128):
    ss = 4
    S = size * ss
    col = (255, 255, 255, 255)
    lw = int(S * 0.045)

    def canvas():
        return Image.new("RGBA", (S, S), (0, 0, 0, 0))

    icons = {}
    im = canvas(); _sun(ImageDraw.Draw(im), S / 2, S / 2, S * 0.2, lw, col); icons["clear_day"] = im
    im = canvas(); _moon(im, S / 2, S / 2, S * 0.32, col); icons["clear_night"] = im
    im = canvas(); _sun(ImageDraw.Draw(im), S * 0.38, S * 0.36, S * 0.14, lw, col)
    _cloud(im, S * 0.22, S * 0.42, S * 0.33, col, cut=lw * 2 + 1); icons["partly_day"] = im
    im = canvas(); _moon(im, S * 0.40, S * 0.36, S * 0.2, col)
    _cloud(im, S * 0.22, S * 0.42, S * 0.33, col, cut=lw * 2 + 1); icons["partly_night"] = im
    im = canvas(); _cloud(im, S * 0.13, S * 0.25, S * 0.34, col); icons["cloudy"] = im
    im = canvas(); d = ImageDraw.Draw(im)
    for k, (a, b) in enumerate(((0.18, 0.82), (0.12, 0.70), (0.26, 0.88), (0.16, 0.76))):
        y = S * (0.30 + k * 0.14)
        d.line((S * a, y, S * b, y), fill=col, width=lw)
    icons["fog"] = im

    def precip(kind):
        im = canvas()
        _cloud(im, S * 0.13, S * 0.12, S * 0.34, col)
        d = ImageDraw.Draw(im)
        xs = (0.32, 0.50, 0.68)
        for i, x in enumerate(xs):
            x *= S
            y = S * (0.66 + (0.05 if i == 1 else 0))
            if kind == "rain":
                d.line((x, y, x - S * 0.05, y + S * 0.16), fill=col, width=lw)
            elif kind == "drizzle":
                d.line((x, y, x - S * 0.025, y + S * 0.08), fill=col, width=lw)
            elif kind == "snow":
                r = S * 0.035
                d.ellipse((x - r, y + S * 0.05 - r, x + r, y + S * 0.05 + r), fill=col)
        if kind == "storm":
            d.polygon([(S * 0.54, S * 0.58), (S * 0.40, S * 0.78), (S * 0.50, S * 0.78),
                       (S * 0.44, S * 0.95), (S * 0.62, S * 0.71), (S * 0.52, S * 0.71),
                       (S * 0.58, S * 0.58)], fill=col)
        return im

    for kind in ("drizzle", "rain", "snow", "storm"):
        icons[kind] = precip(kind)

    for name, im in icons.items():
        im.resize((size, size), Image.LANCZOS).save(
            os.path.join(out, "wx_%s.png" % name), optimize=True)


def make_scrim(out):
    """Soft dark vignette that sits between backgrounds and text."""
    w, h = 480, 270
    m = Image.new("L", (w, h), 0)
    d = ImageDraw.Draw(m)
    # darker toward the bottom/left where the clock lives, light elsewhere
    for y in range(h):
        d.line((0, y, w, y), fill=int(40 + 70 * (y / h) ** 1.5))
    edge = Image.new("L", (w, h), 0)
    ImageDraw.Draw(edge).ellipse((-w * 0.2, -h * 0.3, w * 1.2, h * 1.3), fill=255)
    edge = edge.filter(ImageFilter.GaussianBlur(40))
    m = ImageChops.lighter(m, ImageChops.invert(edge).point(lambda v: v * 0.6))
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    img.putalpha(m)
    img.save(os.path.join(out, "scrim.png"), optimize=True)


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", default=os.path.join(here, "..", "images"))
    args = ap.parse_args()
    out = os.path.abspath(args.out)
    os.makedirs(out, exist_ok=True)

    for old in os.listdir(out):
        if old.startswith("bg_") and old.endswith(".jpg"):
            os.remove(os.path.join(out, old))

    for i, pal in enumerate(PALETTES, start=1):
        path = os.path.join(out, "bg_%02d.jpg" % i)
        make_background(pal, i).save(path, "JPEG", quality=85, optimize=True,
                                     subsampling=0)
        print("wrote", os.path.relpath(path))

    icon_pal = PALETTES[1]
    make_icon(540, 405, icon_pal).save(os.path.join(out, "icon_focus_fhd.png"), optimize=True)
    make_icon(290, 218, icon_pal).save(os.path.join(out, "icon_focus_hd.png"), optimize=True)
    make_splash(PALETTES[0]).save(os.path.join(out, "splash_fhd.jpg"), "JPEG",
                                  quality=85, optimize=True)
    make_weather_icons(out)
    make_scrim(out)
    print("wrote icons, splash, weather icons and scrim")

    total = sum(os.path.getsize(os.path.join(out, f)) for f in os.listdir(out))
    print("images/ total: %.1f KB" % (total / 1024))


if __name__ == "__main__":
    main()

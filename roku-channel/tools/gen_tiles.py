#!/usr/bin/env python3
"""Make the soft focus shadow for the TV app tiles (images/tile_shadow.png)."""
import os
from PIL import Image, ImageDraw, ImageFilter

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(os.path.dirname(HERE), "images")
TW, TH, PAD = 192, 144, 32  # on-screen tile size and blur margin


def main():
    s = 2
    w, h = (TW + PAD * 2) * s, (TH + PAD * 2) * s
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    ImageDraw.Draw(img).rounded_rectangle((PAD * s, PAD * s, w - PAD * s, h - PAD * s), 20 * s, fill=(0, 0, 0, 170))
    img = img.filter(ImageFilter.GaussianBlur(14 * s))
    img.save(os.path.join(OUT, "tile_shadow.png"), optimize=True)
    print("gen_tiles: wrote tile_shadow.png")


if __name__ == "__main__":
    main()

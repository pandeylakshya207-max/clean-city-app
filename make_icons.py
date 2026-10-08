"""Draws the app icons. Run once: python make_icons.py"""
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

OUT = Path(__file__).parent / "static"
GREEN = (31, 122, 77, 255)
WHITE = (255, 255, 255, 255)


def draw_icon(size, name, full_bleed):
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    if full_bleed:
        draw.rectangle((0, 0, size, size), fill=GREEN)
        text_size = int(size * 0.34)
    else:
        draw.rounded_rectangle((0, 0, size - 1, size - 1), radius=int(size * 0.22), fill=GREEN)
        text_size = int(size * 0.44)
    font = ImageFont.load_default(size=text_size)
    box = draw.textbbox((0, 0), "CC", font=font)
    x = (size - (box[2] - box[0])) / 2 - box[0]
    y = (size - (box[3] - box[1])) / 2 - box[1]
    draw.text((x, y), "CC", font=font, fill=WHITE)
    img.save(OUT / name)
    print("made", name)


draw_icon(192, "icon-192.png", False)
draw_icon(512, "icon-512.png", False)
draw_icon(512, "icon-maskable-512.png", True)

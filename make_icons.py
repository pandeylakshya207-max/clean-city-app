"""Draws the app icons. Run once: python make_icons.py

The mark: the lime scan-frame corners used across the app, around a leaf. Drawn at 4x size
and scaled down, so the edges stay smooth.
"""
import math
from pathlib import Path

from PIL import Image, ImageDraw

OUT = Path(__file__).parent / "static"
ASPHALT = (23, 33, 27, 255)
LIME = (200, 241, 105, 255)
LEAF = (47, 125, 74, 255)
SCALE = 4


def leaf_points(cx, cy, length, width, angle_deg, steps=80):
    """A pointed leaf shape centred on (cx, cy), tilted by angle_deg."""
    pts = []
    a = math.radians(angle_deg)
    for side in (1, -1):
        rng = range(steps + 1) if side == 1 else range(steps, -1, -1)
        for i in rng:
            t = i / steps
            x = (t - 0.5) * length
            y = side * (width / 2) * math.sin(math.pi * t) ** 0.85
            pts.append((cx + x * math.cos(a) - y * math.sin(a), cy + x * math.sin(a) + y * math.cos(a)))
    return pts


def corner(draw, x, y, arm, radius, width, dx, dy, color):
    """One rounded scan-frame corner with its arms pointing along dx and dy."""
    r = radius
    box = (x if dx > 0 else x - 2 * r, y if dy > 0 else y - 2 * r,
           x + 2 * r if dx > 0 else x, y + 2 * r if dy > 0 else y)
    start = {(1, 1): 180, (-1, 1): 270, (-1, -1): 0, (1, -1): 90}[(dx, dy)]
    draw.arc(box, start, start + 90, fill=color, width=width)
    cxr = x + dx * r
    cyr = y + dy * r
    ends = [((cxr, y + dy * width / 2), (x + dx * arm, y + dy * width / 2)),
            ((x + dx * width / 2, cyr), (x + dx * width / 2, y + dy * arm))]
    for (x1, y1), (x2, y2) in ends:
        draw.line((x1, y1, x2, y2), fill=color, width=width)
        draw.ellipse((x2 - width / 2, y2 - width / 2, x2 + width / 2, y2 + width / 2), fill=color)


def draw_mark(draw, cx, cy, span, bg_dark):
    """The scan frame and leaf, fitted in a square of side `span` centred on (cx, cy)."""
    frame = LIME if bg_dark else ASPHALT
    half = span / 2
    width = round(span * 0.085)
    arm = span * 0.30
    radius = span * 0.16
    x0, y0, x1, y1 = cx - half, cy - half, cx + half, cy + half
    corner(draw, x0, y0, arm, radius, width, 1, 1, frame)
    corner(draw, x1, y0, arm, radius, width, -1, 1, frame)
    corner(draw, x1, y1, arm, radius, width, -1, -1, frame)
    corner(draw, x0, y1, arm, radius, width, 1, -1, frame)
    leaf = LIME if bg_dark else LEAF
    length = span * 0.62
    draw.polygon(leaf_points(cx, cy, length, span * 0.36, -45), fill=leaf)
    vein = ASPHALT if bg_dark else LIME
    a = math.radians(-45)
    vw = max(2, round(span * 0.03))
    p1 = (cx - 0.36 * length * math.cos(a), cy - 0.36 * length * math.sin(a))
    p2 = (cx + 0.30 * length * math.cos(a), cy + 0.30 * length * math.sin(a))
    draw.line((p1, p2), fill=vein, width=vw)
    for p in (p1, p2):
        draw.ellipse((p[0] - vw / 2, p[1] - vw / 2, p[0] + vw / 2, p[1] + vw / 2), fill=vein)
    stem_end = (cx - 0.47 * length * math.cos(a), cy - 0.47 * length * math.sin(a))
    draw.line((p1, stem_end), fill=leaf, width=round(vw * 1.4))
    draw.ellipse((stem_end[0] - vw * 0.7, stem_end[1] - vw * 0.7, stem_end[0] + vw * 0.7, stem_end[1] + vw * 0.7), fill=leaf)


def draw_icon(size, name, full_bleed):
    big = size * SCALE
    img = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    if full_bleed:
        # Android crops maskable icons to a circle or squircle: keep the mark inside the middle 60%.
        draw.rectangle((0, 0, big, big), fill=ASPHALT)
        span = big * 0.50
    else:
        draw.rounded_rectangle((0, 0, big - 1, big - 1), radius=int(big * 0.23), fill=ASPHALT)
        span = big * 0.60
    draw_mark(draw, big / 2, big / 2, span, bg_dark=True)
    img = img.resize((size, size), Image.Resampling.LANCZOS)
    img.save(OUT / name, optimize=True)
    print("made", name)


if __name__ == "__main__":
    draw_icon(192, "icon-192.png", False)
    draw_icon(512, "icon-512.png", False)
    draw_icon(512, "icon-maskable-512.png", True)

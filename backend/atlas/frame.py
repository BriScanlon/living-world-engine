"""The furniture of a drawn map (epic #58, phase 3 #56): labels that keep out of each other's way, a ruled frame with a
graticule border, a compass rose, a scroll cartouche and a scale bar in miles and kilometres."""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from PIL import Image, ImageDraw, ImageFont

INK = (40, 30, 22, 255)
PAPER = (238, 228, 198, 255)
RED = (140, 40, 30, 255)
NICE = (1, 2, 5, 10, 20, 25, 50, 100, 200, 250, 500, 1000, 2000, 5000)


# --------------------------------------------------------------------------- labels
@dataclass
class Label:
    text: str
    font: ImageFont.ImageFont
    xy: tuple[float, float]
    anchor: str
    fill: tuple
    halo: tuple
    box: tuple[float, float, float, float]


@dataclass
class Labels:
    """Places labels so that none overlaps another (or a reserved box), trying each candidate position in turn."""
    bounds: tuple[float, float, float, float]
    pad: float = 4.0
    placed: list[Label] = field(default_factory=list)
    reserved: list[tuple[float, float, float, float]] = field(default_factory=list)

    def __post_init__(self):
        self._d = ImageDraw.Draw(Image.new("L", (1, 1)))

    def box(self, text, font, xy, anchor, stroke) -> tuple[float, float, float, float]:
        return self._d.textbbox(xy, text, font=font, anchor=anchor, stroke_width=stroke)

    def free(self, b) -> bool:
        x0, y0, x1, y1 = self.bounds
        if b[0] < x0 or b[1] < y0 or b[2] > x1 or b[3] > y1:
            return False
        p = self.pad
        return not any(b[0] < o[2] + p and b[2] + p > o[0] and b[1] < o[3] + p and b[3] + p > o[1]
                       for o in [*(l.box for l in self.placed), *self.reserved])

    def add(self, text: str, font, candidates: list[tuple[float, float, str]], fill, halo) -> Label | None:
        """The first candidate (x, y, anchor) where ``text`` fits, or None (then it is left off)."""
        stroke = max(2, getattr(font, "size", 16) // 8)
        for x, y, anchor in candidates:
            b = self.box(text, font, (x, y), anchor, stroke)
            if self.free(b):
                lab = Label(text, font, (x, y), anchor, fill, halo, b)
                self.placed.append(lab)
                return lab
        return None

    def reserve(self, b) -> None:
        self.reserved.append(b)

    def draw(self, img: Image.Image) -> None:
        d = ImageDraw.Draw(img, "RGBA")
        for l in self.placed:
            d.text(l.xy, l.text, font=l.font, fill=l.fill, anchor=l.anchor,
                   stroke_width=max(2, getattr(l.font, "size", 16) // 8), stroke_fill=l.halo)


def spaced(text: str, gap: int = 1) -> str:
    """Letter-spaced capitals, as realm names are lettered across their land."""
    return ("   " if gap > 1 else "  ").join(" ".join(word) for word in text.upper().split())


# --------------------------------------------------------------------------- the frame
def _nice_step(span: float, parts: int) -> float:
    raw = span / parts
    for n in (0.1, 0.2, 0.25, 0.5, 1, 2, 2.5, 5, 10, 15, 20, 30):
        if n >= raw:
            return n
    return 30


def neatline(img: Image.Image, inverse, m: float, band: float, scale: float) -> tuple[float, float, float, float]:
    """A double-ruled frame with a graticule border (alternate degrees inked), the margin outside it in paper.
    ``inverse(x, y)`` gives (lon, lat) for a pixel. Returns the inner rectangle (the map's own area)."""
    W, H = img.size
    d = ImageDraw.Draw(img, "RGBA")
    ix0, iy0, ix1, iy1 = m + band, m + band, W - m - band, H - m - band
    for r in ((0, 0, W, iy0), (0, iy1, W, H), (0, 0, ix0, H), (ix1, 0, W, H)):
        d.rectangle(r, fill=PAPER)
    lon0, lat0 = inverse(ix0, iy1)
    lon1, lat1 = inverse(ix1, iy0)
    for axis, (a0, a1) in (("lon", (lon0, lon1)), ("lat", (lat0, lat1))):
        step = _nice_step(abs(a1 - a0), 10)
        v = math.floor(min(a0, a1) / step) * step
        k = int(round(v / step))
        while v < max(a0, a1):
            nxt = v + step
            if k % 2 == 0:
                if axis == "lon":
                    xa = ix0 + (max(v, a0) - a0) / (a1 - a0) * (ix1 - ix0)
                    xb = ix0 + (min(nxt, a1) - a0) / (a1 - a0) * (ix1 - ix0)
                    d.rectangle([xa, m + band * 0.35, xb, m + band * 0.65], fill=INK)
                    d.rectangle([xa, H - m - band * 0.65, xb, H - m - band * 0.35], fill=INK)
                else:
                    ya = iy1 - (max(v, a0) - a0) / (a1 - a0) * (iy1 - iy0)
                    yb = iy1 - (min(nxt, a1) - a0) / (a1 - a0) * (iy1 - iy0)
                    d.rectangle([m + band * 0.35, yb, m + band * 0.65, ya], fill=INK)
                    d.rectangle([W - m - band * 0.65, yb, W - m - band * 0.35, ya], fill=INK)
            v, k = nxt, k + 1
    for box, w in (((m, m, W - m, H - m), 3), ((m + band * 0.35, m + band * 0.35, W - m - band * 0.35, H - m - band * 0.35), 1),
                   ((m + band * 0.65, m + band * 0.65, W - m - band * 0.65, H - m - band * 0.65), 1),
                   ((ix0, iy0, ix1, iy1), 2)):
        d.rectangle(box, outline=INK, width=max(1, round(w * scale)))
    return ix0, iy0, ix1, iy1


def compass(img: Image.Image, cx: float, cy: float, r: float, font) -> tuple[float, float, float, float]:
    """An eight-pointed compass rose, each point half inked; returns its box."""
    d = ImageDraw.Draw(img, "RGBA")
    lw = max(1, round(r / 40))
    d.ellipse([cx - r * 0.62, cy - r * 0.62, cx + r * 0.62, cy + r * 0.62], outline=INK, width=lw)
    d.ellipse([cx - r * 0.56, cy - r * 0.56, cx + r * 0.56, cy + r * 0.56], outline=INK, width=lw)
    for ang, length, wid in [(a, 0.55, 0.11) for a in (45, 135, 225, 315)] + [(a, 1.0, 0.15) for a in (0, 90, 180, 270)]:
        t = math.radians(ang - 90)
        tip = (cx + math.cos(t) * r * length, cy + math.sin(t) * r * length)
        side_l = (cx + math.cos(t - math.pi / 2) * r * wid, cy + math.sin(t - math.pi / 2) * r * wid)
        side_r = (cx + math.cos(t + math.pi / 2) * r * wid, cy + math.sin(t + math.pi / 2) * r * wid)
        north = ang == 0
        d.polygon([tip, side_l, (cx, cy)], fill=RED if north else INK, outline=INK)
        d.polygon([tip, side_r, (cx, cy)], fill=PAPER, outline=INK)
    d.text((cx, cy - r * 1.04), "N", font=font, fill=INK, anchor="ms", stroke_width=2, stroke_fill=PAPER)
    return cx - r, cy - r * 1.04 - font.size, cx + r, cy + r


def cartouche(img: Image.Image, x: float, y: float, title: str, sub: str, title_font, sub_font, scale: float):
    """A scroll with rolled ends, lower-left at (x, y); returns its box."""
    d = ImageDraw.Draw(img, "RGBA")
    tw = max(d.textlength(title, font=title_font), d.textlength(sub, font=sub_font))
    h = title_font.size + sub_font.size + 40 * scale
    x0, y0, x1, y1 = x + 26 * scale, y - h, x + 26 * scale + tw + 60 * scale, y
    lw = max(1, round(2 * scale))
    for ex, sgn in ((x0, -1), (x1, 1)):  # the rolled ends, behind the scroll
        rr = h * 0.32
        cy_ = y0 + h * 0.62
        d.ellipse([ex + sgn * rr * 0.2 - rr, cy_ - rr, ex + sgn * rr * 0.2 + rr, cy_ + rr], fill=PAPER, outline=INK, width=lw)
        d.ellipse([ex + sgn * rr * 0.35 - rr * 0.45, cy_ - rr * 0.45, ex + sgn * rr * 0.35 + rr * 0.45, cy_ + rr * 0.45],
                  outline=INK, width=lw)
    d.rectangle([x0, y0, x1, y1 - h * 0.08], fill=PAPER, outline=INK, width=lw)
    d.rectangle([x0 + 6 * scale, y0 + 6 * scale, x1 - 6 * scale, y1 - h * 0.08 - 6 * scale], outline=INK, width=max(1, lw // 2))
    cx = (x0 + x1) / 2
    d.text((cx, y0 + 14 * scale), title, font=title_font, fill=INK, anchor="mt")
    d.text((cx, y0 + 20 * scale + title_font.size), sub, font=sub_font, fill=(90, 70, 45, 255), anchor="mt")
    return x0 - h * 0.4, y0, x1 + h * 0.4, y1


def scale_bar(img: Image.Image, right: float, bottom: float, km_per_px: float, width_px: float, font, scale: float):
    """Bars in kilometres and miles, segments alternately inked; returns the box."""
    d = ImageDraw.Draw(img, "RGBA")
    rows = []
    for unit, per_km in (("km", 1.0), ("miles", 1 / 1.609344)):
        want = km_per_px * width_px * per_km
        nice = next((n for n in NICE if n >= want * 0.6), NICE[-1])
        rows.append((unit, nice, nice / per_km / km_per_px))
    bar_w = max(r[2] for r in rows)
    x0 = right - bar_w - 20 * scale
    top = bottom - 2 * (font.size + 26 * scale) - 10 * scale
    d.rectangle([x0 - 16 * scale, top, right, bottom], fill=PAPER, outline=INK, width=max(1, round(scale)))
    y = top + font.size + 12 * scale
    for unit, nice, px in rows:
        seg = px / 4
        for i in range(4):
            d.rectangle([x0 + i * seg, y, x0 + (i + 1) * seg, y + 6 * scale], fill=INK if i % 2 == 0 else PAPER,
                        outline=INK, width=max(1, round(scale / 2)))
        d.text((x0, y - 4 * scale), "0", font=font, fill=INK, anchor="ms")
        d.text((x0 + px, y - 4 * scale), f"{nice:g} {unit}", font=font, fill=INK, anchor="ms")
        y += font.size + 26 * scale
    return x0 - 16 * scale, top, right, bottom

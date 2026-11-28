"""Drawing helpers that make a map look drawn by a cartographer rather than plotted (epic #58, phase 1 #54).

Everything works in the renderer's (supersampled) pixel space and is seeded, so a map redraws identically:

* :func:`smooth` rounds off cell corners (Chaikin) after a gentle wobble. The wobble is a function of *position*, not
  of the shape, so a border shared by two provinces, or by a province and the coast, bends the same way in both.
* :func:`ripples` and :func:`shallows` give coasts the echo lines and pale band of a hand-drawn sea.
* :func:`taper` draws a river thin at its source and broad at its mouth.
* :func:`parchment` ages a finished picture: paper grain, faint blotches and a darker rim.
* :func:`serif` is the map's typeface (DejaVu Serif, falling back to Pillow's own font).
"""
from __future__ import annotations

import hashlib
import math
from functools import lru_cache
from typing import Callable

import numpy as np
import shapely
from PIL import Image, ImageDraw, ImageFilter, ImageFont
from shapely.geometry import LineString, MultiPolygon, Polygon

SERIF = ("/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf", "DejaVuSerif.ttf")
SERIF_BOLD = ("/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf", "DejaVuSerif-Bold.ttf")


def seed_of(*parts: object) -> int:
    return int(hashlib.sha1("|".join(map(str, parts)).encode()).hexdigest()[:8], 16)


@lru_cache(maxsize=64)
def serif(px: int, bold: bool = False) -> ImageFont.ImageFont:
    for path in (SERIF_BOLD if bold else SERIF):
        try:
            return ImageFont.truetype(path, px)
        except OSError:
            continue
    try:
        return ImageFont.load_default(size=px)
    except TypeError:  # Pillow without FreeType
        return ImageFont.load_default()


def to_px(geom, project: Callable[[np.ndarray], np.ndarray]):
    """A lon/lat geometry in pixel space (``project`` maps an (N, 2) array of lon/lat to pixels)."""
    return shapely.transform(geom, project)


# --------------------------------------------------------------------------- smoothing
class Wobble:
    """A smooth displacement field: a few seeded sine waves at two scales, ``amp`` pixels at most."""

    def __init__(self, seed: int, amp: float, scale: float):
        rng = np.random.default_rng(seed)
        self.amp = amp
        self.waves = [(rng.uniform(0, 2 * math.pi), rng.uniform(0, 2 * math.pi),
                       rng.uniform(0.7, 1.3) / scale * k, rng.uniform(0, 2 * math.pi), w)
                      for k, w in ((1.0, 0.6), (2.7, 0.3), (6.1, 0.1)) for _ in range(2)]

    def __call__(self, xy: np.ndarray) -> np.ndarray:
        if not self.amp:
            return xy
        d = np.zeros_like(xy)
        for ang, ang2, f, ph, w in self.waves:
            t = (xy[:, 0] * math.cos(ang) + xy[:, 1] * math.sin(ang)) * f + ph
            d[:, 0] += w * np.sin(t)
            d[:, 1] += w * np.sin(t + ang2)
        return xy + d * (self.amp / 2)


def _chaikin(pts: np.ndarray, closed: bool, rounds: int) -> np.ndarray:
    for _ in range(rounds):
        if len(pts) < 3:
            return pts
        a = pts if closed else pts[:-1]
        b = np.roll(pts, -1, axis=0) if closed else pts[1:]
        q, r = 0.75 * a + 0.25 * b, 0.25 * a + 0.75 * b
        out = np.empty((2 * len(a), 2))
        out[0::2], out[1::2] = q, r
        pts = out if closed else np.vstack([pts[:1], out, pts[-1:]])
    return pts


def _ring(coords, wobble: Wobble, step: float, rounds: int, tol: float) -> np.ndarray:
    # simplify, then split long edges so rounding stays local (Chaikin cuts a quarter of every edge per round)
    line = shapely.segmentize(LineString(coords).simplify(tol), step * 3)
    pts = np.asarray(line.coords)[:-1]  # closed ring: drop the repeated last point
    pts = _chaikin(pts, True, rounds)
    line = shapely.segmentize(LineString(np.vstack([pts, pts[:1]])), step)
    return wobble(np.asarray(line.coords)[:-1])


def smooth(geom, wobble: Wobble, cell: float, rounds: int = 3):
    """Polygons with the cell zigzag taken out and rounded, gently wobbling outlines (pixel space).

    ``cell`` is the map's cell size in pixels: notches and spurs narrower than about a third of it are closed and
    trimmed (a morphological close and open), then the outline is simplified, rounded (Chaikin) and wobbled."""
    r = cell * 0.35
    g = geom.buffer(r, quad_segs=2).buffer(-2 * r, quad_segs=2).buffer(r, quad_segs=2)
    out = []
    for p in polys(g):
        ext = _ring(p.exterior.coords, wobble, cell / 4, rounds, r / 2)
        if len(ext) < 3:
            continue
        holes = [h for h in (_ring(i.coords, wobble, cell / 4, rounds, r / 2) for i in p.interiors) if len(h) >= 3]
        out.append(Polygon(ext, holes).buffer(0))
    return MultiPolygon([q for g in out for q in polys(g)]) if out else MultiPolygon()


def smooth_line(coords, wobble: Wobble, step: float, rounds: int = 3, tol: float = 0.0) -> np.ndarray:
    line = LineString(coords).simplify(tol) if tol else LineString(coords)
    pts = _chaikin(np.asarray(shapely.segmentize(line, step * 3).coords), False, rounds)
    pts = np.asarray(shapely.segmentize(LineString(pts), step).coords)
    if len(pts) > 2:
        pts = np.vstack([pts[:1], wobble(pts[1:-1]), pts[-1:]])
    return pts


def wash(size: tuple[int, int], shapes: list[tuple[object, tuple]], blur: float) -> Image.Image:
    """Colour laid on like paint: the shapes filled, then softened so neighbouring colours bleed into each other."""
    layer = Image.new("RGBA", size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    for geom, colour in shapes:
        fill(d, geom, colour, hole=colour)
    return layer.filter(ImageFilter.GaussianBlur(blur))


def borders(geoms: list, wobble: Wobble, cell: float, clip=None) -> list[np.ndarray]:
    """The lines between neighbouring shapes, each drawn once, rounded like the coast."""
    lines = shapely.line_merge(shapely.unary_union([g.boundary for g in geoms if not g.is_empty]))
    if clip is not None:
        lines = lines.intersection(clip)
    parts = [lines] if lines.geom_type == "LineString" else [g for g in getattr(lines, "geoms", []) if g.geom_type == "LineString"]
    return [smooth_line(ln.coords, wobble, cell / 4, tol=cell * 0.17) for ln in parts if ln.length > cell * 0.5]


# --------------------------------------------------------------------------- drawing
def polys(geom) -> list[Polygon]:
    if geom.is_empty:
        return []
    return [geom] if geom.geom_type == "Polygon" else [g for g in getattr(geom, "geoms", []) if g.geom_type == "Polygon"]


def fill(draw: ImageDraw.ImageDraw, geom, colour, hole=None) -> None:
    for p in polys(geom):
        draw.polygon(list(p.exterior.coords), fill=colour)
        for i in p.interiors:
            draw.polygon(list(i.coords), fill=hole)


def outline(draw: ImageDraw.ImageDraw, geom, colour, width: int, holes: bool = True) -> None:
    for p in polys(geom):
        for ring in [p.exterior, *(p.interiors if holes else [])]:
            draw.line(list(ring.coords), fill=colour, width=width, joint="curve")


def shallows(size: tuple[int, int], coast, width: float, colour) -> Image.Image:
    """A pale band of shallow water along the coast, fading out to sea."""
    band = Image.new("L", size, 0)
    fill(ImageDraw.Draw(band), coast.buffer(width, quad_segs=4), 255, hole=255)
    band = band.filter(ImageFilter.GaussianBlur(width / 2))
    layer = Image.new("RGBA", size, colour[:3] + (0,))
    layer.putalpha(band.point(lambda v: v * colour[3] // 255))
    return layer


def ripples(draw: ImageDraw.ImageDraw, coast, gaps: list[float], colour, width: int) -> None:
    """Lines echoing the coast out to sea, each fainter than the last (pixel distances in ``gaps``)."""
    r, g, b, a = colour
    for k, d in enumerate(gaps):
        ring = coast.buffer(d, quad_segs=6).simplify(d / 8)
        fade = int(a * (1 - k / (len(gaps) + 0.5)))
        for p in polys(ring):
            draw.line(list(p.exterior.coords), fill=(r, g, b, fade), width=width, joint="curve")


def taper(draw: ImageDraw.ImageDraw, pts: np.ndarray, w0: float, w1: float, colour) -> None:
    """A line from ``w0`` wide at its first point to ``w1`` at its last, with round joints."""
    if len(pts) < 2:
        return
    seg = np.hypot(*np.diff(pts, axis=0).T)
    along = np.concatenate([[0.0], np.cumsum(seg)]) / max(float(seg.sum()), 1e-9)
    for (x0, y0), (x1, y1), t in zip(pts[:-1], pts[1:], along[1:]):
        w = w0 + (w1 - w0) * t
        draw.line([(x0, y0), (x1, y1)], fill=colour, width=max(1, round(w)))
        if w >= 3:
            r = w / 2
            draw.ellipse([x1 - r, y1 - r, x1 + r, y1 + r], fill=colour)


def parchment(img: Image.Image, seed: int, strength: float = 1.0) -> Image.Image:
    """Age a finished RGBA picture: blotches, fine grain and a darker rim (multiplied in)."""
    w, h = img.size
    rng = np.random.default_rng(seed)
    blot = Image.fromarray((rng.random((max(2, h // 160), max(2, w // 160))) * 255).astype(np.uint8))
    blot = np.asarray(blot.resize((w, h), Image.BICUBIC).filter(ImageFilter.GaussianBlur(max(w, h) / 200)),
                      dtype=np.float32) / 255
    grain = np.asarray(Image.fromarray((rng.random((h // 2, w // 2)) * 255).astype(np.uint8)).resize((w, h)),
                       dtype=np.float32) / 255
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    rim = np.maximum(np.abs(xx / w - 0.5), np.abs(yy / h - 0.5)) * 2  # 0 at the centre, 1 at the edge
    shade = 1 - strength * (0.07 * (blot - 0.5) * 2 + 0.035 * (grain - 0.5) * 2 + 0.16 * np.clip(rim - 0.72, 0, 1) / 0.28)
    rgb = np.asarray(img.convert("RGB"), dtype=np.float32) * np.clip(shade, 0.6, 1.1)[..., None]
    warm = np.array([1.0, 0.985, 0.95], dtype=np.float32)  # a touch of age-yellow
    out = Image.fromarray(np.clip(rgb * warm, 0, 255).astype(np.uint8), "RGB").convert("RGBA")
    return out

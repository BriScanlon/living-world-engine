"""Terrain drawn with symbols (epic #58, phase 2 #55), in the hand-inked tradition of classic fantasy maps.

Mountains with hatched shadow flanks, hills, downs, broadleaf and pine forests, jungle and palms, marsh reeds, grass
tufts, savanna trees, heath, desert stipple and dunes, cracked wastes, ice, cliffs along high coasts, and bridges and
fords where roads cross rivers. Every symbol is drawn in code (no image assets) at a fixed size in pixels, so density
follows area rather than zoom, and every choice is seeded by place, so the map redraws identically.

:func:`place` turns relief cells into symbols (kind, x, y, size); :func:`draw` inks them back to front;
:func:`cliffs` and :func:`crossings` add the coast and river marks.
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass

import numpy as np
from PIL import Image, ImageDraw

INK = (58, 42, 28, 255)
PAPER = (238, 228, 198, 255)
SNOW = (246, 244, 238, 255)
LEAF = (196, 210, 156, 255)
PINE = (150, 174, 128, 255)
JUNGLE = (150, 186, 118, 255)
WATER_INK = (74, 108, 128, 220)

# relief thresholds (Azgaar heights: 20 = sea level, 100 = highest)
MOUNTAIN, HILL, DOWNS = 66, 50, 40
OPEN = {3, 4, 10}  # savanna, grassland, tundra: where low rises show as downs
FOREST = {5: "jungle", 6: "broadleaf", 7: "jungle", 8: "mixed", 9: "pine"}
GROUND = {1: "desert", 2: "waste", 3: "savanna", 4: "plains", 10: "heath", 11: "ice", 12: "marsh"}

# per kind: symbol size and spacing (both in base pixels, scaled by the renderer's supersampling)
SIZE = {"mountain": 26, "hill": 18, "downs": 16, "broadleaf": 11, "pine": 12, "jungle": 11, "palm": 12, "mixed": 11,
        "marsh": 9, "plains": 8, "savanna": 10, "heath": 7, "desert": 8, "dune": 10, "waste": 9, "ice": 10}
SPACING = {"mountain": 0.85, "hill": 0.85, "downs": 1.4, "broadleaf": 0.62, "pine": 0.6, "jungle": 0.55,
           "mixed": 0.6, "marsh": 1.5, "plains": 2.6, "savanna": 2.4, "heath": 2.2, "desert": 2.0, "waste": 2.2,
           "ice": 1.8}


@dataclass
class Symbol:
    kind: str
    x: float
    y: float
    s: float
    seed: int


# --------------------------------------------------------------------------- the glyphs
def _w(s: float) -> int:
    return max(1, round(s / 20))


def _hatch(s: float) -> int:
    return max(1, round(s / 34))


def mountain(d: ImageDraw.ImageDraw, x: float, y: float, s: float, r: random.Random, snow: bool = False) -> None:
    h, w = s * r.uniform(0.85, 1.15), s * r.uniform(0.62, 0.8)
    px = x + r.uniform(-0.12, 0.12) * w
    peak, left, right = (px, y - h), (x - w, y), (x + w, y)
    d.polygon([left, peak, right], fill=SNOW if snow else PAPER)
    lw = _w(s)
    d.line([left, peak, right], fill=INK, width=lw, joint="curve")
    n = 5
    for i in range(1, n):  # light hatching down the shadowed (east) flank
        t = i / n
        a = (peak[0] + (right[0] - peak[0]) * t, peak[1] + (right[1] - peak[1]) * t)
        d.line([a, (a[0] - w * 0.22, min(y, a[1] + h * 0.22))], fill=INK, width=_hatch(s))
    d.line([(px, y - h), (px - w * 0.06, y - h * 0.6)], fill=INK, width=_hatch(s))  # a ridge line


def hill(d: ImageDraw.ImageDraw, x: float, y: float, s: float, r: random.Random) -> None:
    w, h = s * r.uniform(0.6, 0.78), s * r.uniform(0.32, 0.42)
    pts = [(x + w * math.cos(a), y - h * math.sin(a)) for a in np.linspace(math.pi, 0, 14)]
    d.polygon(pts + [(x + w, y)], fill=PAPER)
    d.line(pts, fill=INK, width=_w(s), joint="curve")
    for k in (0.35, 0.6):
        a = math.pi * (0.5 - k * 0.45)
        p = (x + w * math.cos(a) * 0.9, y - h * math.sin(a) * 0.9)
        d.line([p, (p[0] - s * 0.08, p[1] + h * 0.45)], fill=INK, width=_hatch(s))


def downs(d: ImageDraw.ImageDraw, x: float, y: float, s: float, r: random.Random) -> None:
    w, h = s * r.uniform(0.7, 1.0), s * r.uniform(0.14, 0.2)
    pts = [(x + w * math.cos(a), y - h * math.sin(a)) for a in np.linspace(math.pi * 0.95, math.pi * 0.05, 10)]
    d.line(pts, fill=INK, width=max(1, _w(s) - 1), joint="curve")


def broadleaf(d: ImageDraw.ImageDraw, x: float, y: float, s: float, r: random.Random, fill=LEAF) -> None:
    rr = s * r.uniform(0.3, 0.38)
    cx, cy = x + r.uniform(-0.05, 0.05) * s, y - s * 0.5
    d.line([(x, y), (x, cy + rr * 0.6)], fill=INK, width=_w(s))
    for dx, dy, k in ((-0.45, 0.15, 0.75), (0.45, 0.15, 0.75), (0, -0.25, 0.85), (0, 0.1, 1.0)):
        q = rr * k
        bx, by = cx + dx * rr, cy + dy * rr
        d.ellipse([bx - q, by - q, bx + q, by + q], fill=fill, outline=INK, width=max(1, _w(s) - 1))


def pine(d: ImageDraw.ImageDraw, x: float, y: float, s: float, r: random.Random) -> None:
    h, w = s * r.uniform(0.85, 1.05), s * r.uniform(0.2, 0.26)
    top = y - h
    d.line([(x, y), (x, y - h * 0.2)], fill=INK, width=_w(s))
    for k, (t0, t1) in enumerate(((0.0, 0.5), (0.25, 0.75), (0.5, 0.82))):  # three tiers, widest at the bottom
        ww = w * (1.0 - k * 0.22)
        a, b = top + h * t0, top + h * t1
        d.polygon([(x, a), (x + ww, b), (x - ww, b)], fill=PINE, outline=INK, width=max(1, _w(s) - 1))


def palm(d: ImageDraw.ImageDraw, x: float, y: float, s: float, r: random.Random) -> None:
    lean = r.uniform(-0.25, 0.25) * s
    top = (x + lean, y - s * 0.95)
    d.line([(x, y), (x + lean * 0.4, y - s * 0.5), top], fill=INK, width=_w(s), joint="curve")
    for ang in (-160, -120, -60, -20, -90):
        a = math.radians(ang)
        tip = (top[0] + math.cos(a) * s * 0.42, top[1] + math.sin(a) * s * 0.25 + s * 0.12)
        d.line([top, tip], fill=INK, width=_w(s))


def tuft(d: ImageDraw.ImageDraw, x: float, y: float, s: float, r: random.Random, n: int = 3) -> None:
    for i in range(n):
        a = math.radians(-90 + (i - (n - 1) / 2) * 24 + r.uniform(-6, 6))
        d.line([(x, y), (x + math.cos(a) * s * 0.55, y + math.sin(a) * s * 0.6)], fill=INK, width=max(1, _w(s) - 1))


def reeds(d: ImageDraw.ImageDraw, x: float, y: float, s: float, r: random.Random) -> None:
    tuft(d, x, y, s * 1.15, r, n=4)
    for dy, w in ((s * 0.18, 0.55), (s * 0.36, 0.35)):  # standing water below
        d.line([(x - s * w, y + dy), (x + s * w, y + dy)], fill=WATER_INK, width=max(1, _w(s) - 1))


def acacia(d: ImageDraw.ImageDraw, x: float, y: float, s: float, r: random.Random) -> None:
    d.line([(x, y), (x, y - s * 0.6)], fill=INK, width=_w(s))
    w, h = s * 0.5, s * 0.16
    d.ellipse([x - w, y - s * 0.75 - h, x + w, y - s * 0.75 + h], fill=LEAF, outline=INK, width=max(1, _w(s) - 1))


def stipple(d: ImageDraw.ImageDraw, x: float, y: float, s: float, r: random.Random) -> None:
    rr = max(1.0, s / 16)
    for _ in range(5):
        px, py = x + r.uniform(-0.5, 0.5) * s, y + r.uniform(-0.3, 0.3) * s
        d.ellipse([px - rr, py - rr, px + rr, py + rr], fill=INK)


def dune(d: ImageDraw.ImageDraw, x: float, y: float, s: float, r: random.Random) -> None:
    w, h = s * 0.6, s * 0.22
    pts = [(x + w * math.cos(a), y - h * math.sin(a)) for a in np.linspace(math.pi, math.pi * 0.25, 8)]
    d.line(pts, fill=INK, width=max(1, _w(s) - 1), joint="curve")
    d.line([pts[-1], (pts[-1][0] + w * 0.35, y)], fill=INK, width=max(1, _w(s) - 1))  # the steep lee face


def crack(d: ImageDraw.ImageDraw, x: float, y: float, s: float, r: random.Random) -> None:
    pts, px = [], x - s * 0.5
    for i in range(4):
        pts.append((px + i * s * 0.33, y + (s * 0.12 if i % 2 else -s * 0.12) + r.uniform(-0.05, 0.05) * s))
    d.line(pts, fill=INK, width=max(1, _w(s) - 1))
    stipple(d, x, y + s * 0.35, s * 0.6, r)


def ice(d: ImageDraw.ImageDraw, x: float, y: float, s: float, r: random.Random) -> None:
    for i in range(3):
        o = (i - 1) * s * 0.28
        d.line([(x + o - s * 0.2, y), (x + o + s * 0.2, y - s * 0.3)], fill=(110, 130, 150, 255), width=max(1, _w(s) - 1))
    if r.random() < 0.4:  # a crevasse
        d.arc([x - s * 0.5, y - s * 0.1, x + s * 0.5, y + s * 0.3], 200, 340, fill=INK, width=max(1, _w(s) - 1))


def heath(d: ImageDraw.ImageDraw, x: float, y: float, s: float, r: random.Random) -> None:
    tuft(d, x, y, s * 0.8, r, n=3)
    rr = max(1.0, s / 10)
    d.ellipse([x + s * 0.5 - rr, y - rr, x + s * 0.5 + rr, y + rr], fill=INK)


def _draw(d: ImageDraw.ImageDraw, sym: Symbol) -> None:
    r = random.Random(sym.seed)
    k, x, y, s = sym.kind, sym.x, sym.y, sym.s
    if k == "mountain" or k == "snowpeak":
        mountain(d, x, y, s, r, snow=k == "snowpeak")
    elif k == "hill":
        hill(d, x, y, s, r)
    elif k == "downs":
        downs(d, x, y, s, r)
    elif k == "broadleaf":
        broadleaf(d, x, y, s, r)
    elif k == "jungle":
        broadleaf(d, x, y, s, r, fill=JUNGLE)
    elif k == "pine":
        pine(d, x, y, s, r)
    elif k == "palm":
        palm(d, x, y, s, r)
    elif k == "marsh":
        reeds(d, x, y, s, r)
    elif k == "plains":
        tuft(d, x, y, s, r)
    elif k == "savanna":
        (acacia if r.random() < 0.3 else tuft)(d, x, y, s, r)
    elif k == "heath":
        heath(d, x, y, s, r)
    elif k == "desert":
        stipple(d, x, y, s, r)
    elif k == "dune":
        dune(d, x, y, s, r)
    elif k == "waste":
        (crack if r.random() < 0.45 else stipple)(d, x, y, s, r)
    elif k == "ice":
        ice(d, x, y, s, r)


GLYPHS = ("mountain", "snowpeak", "hill", "downs", "broadleaf", "jungle", "pine", "palm", "marsh", "plains",
          "savanna", "heath", "desert", "dune", "waste", "ice")


# --------------------------------------------------------------------------- placement
def kinds_for(h: int, b: int) -> list[tuple[str, float]]:
    """What a cell of height ``h`` and biome ``b`` shows, and how densely (1 = the kind's full density)."""
    if h >= MOUNTAIN:
        return [("snowpeak" if b == 11 or h >= 88 else "mountain", 1.0)]
    out: list[tuple[str, float]] = []
    forest = FOREST.get(b)
    if h >= HILL:
        out.append(("hill", 1.0))
        if forest:
            out.append(("pine" if forest in ("pine", "mixed") else forest, 0.35))
        return out
    if forest == "mixed":
        return [("broadleaf", 0.6), ("pine", 0.45)]
    if forest == "jungle":
        return [("jungle", 1.0 if b == 7 else 0.55), ("palm", 0.12 if b == 7 else 0.2)]
    if forest:
        return [(forest, 1.0)]
    if h >= DOWNS and b in OPEN:
        out.append(("downs", 1.0))
    ground = GROUND.get(b)
    if ground == "desert":
        out += [("desert", 1.0), ("dune", 0.35)]
    elif ground:
        out.append((ground, 1.0))
    return out


def place(cells: np.ndarray, cell_px: float, land: Image.Image, avoid: Image.Image, scale: float, seed: int,
          budget: int = 45000) -> list[Symbol]:
    """Symbols for relief cells ``cells`` (rows of x, y, h, b in pixels), ``cell_px`` apart, at ``scale`` x their base
    size. A symbol is kept only where ``land`` is set and ``avoid`` (roads, rivers, towns) is not, at its foot and
    beside it. Back to front (by y)."""
    W, H = land.size
    lp, ap = land.load(), avoid.load()
    area = cell_px * cell_px
    plan = []
    for i, (x, y, h, b) in enumerate(cells):
        if not (-cell_px <= x <= W + cell_px and -cell_px <= y <= H + cell_px):
            continue
        for kind, dens in kinds_for(int(h), int(b)):
            base = kind if kind in SPACING else ("mountain" if kind == "snowpeak" else "jungle")
            step = SIZE[base] * SPACING[base] * scale
            plan.append((i, x, y, h, kind, dens * area / (step * step)))
    want = sum(p[5] for p in plan)
    thin = min(1.0, budget / want) if want else 1.0
    out: list[Symbol] = []
    for i, x, y, h, kind, n in plan:
        r = random.Random(seed * 1_000_003 + i * 31 + GLYPHS.index(kind))
        n *= thin
        count = int(n) + (1 if r.random() < n - int(n) else 0)
        base = "mountain" if kind == "snowpeak" else kind
        size = SIZE.get(base, 11) * scale
        if kind in ("mountain", "snowpeak"):
            size *= 1.0 + 0.6 * min(1.0, max(0.0, (h - MOUNTAIN) / 30))
        for _ in range(count):
            sx, sy = x + r.uniform(-0.6, 0.6) * cell_px, y + r.uniform(-0.6, 0.6) * cell_px
            ix, iy = int(sx), int(sy)
            reach = int(size * 0.45)
            ok = True
            for qx, qy in ((ix, iy), (ix - reach, iy), (ix + reach, iy), (ix, iy - int(size * 0.5))):
                if not (0 <= qx < W and 0 <= qy < H) or lp[qx, qy] < 128 or ap[qx, qy]:
                    ok = False
                    break
            if ok:
                out.append(Symbol(kind, sx, sy, size, r.getrandbits(32)))
    out.sort(key=lambda s: (s.y, s.x))
    return out


def draw(img: Image.Image, symbols: list[Symbol]) -> None:
    d = ImageDraw.Draw(img, "RGBA")
    for s in symbols:
        _draw(d, s)


# --------------------------------------------------------------------------- coast and river marks
def cliffs(img: Image.Image, coast, land: Image.Image, height_at, scale: float, high: int = 52) -> int:
    """Hachure ticks pointing inland along stretches of coast where the land is high. Returns how many."""
    import shapely
    d = ImageDraw.Draw(img, "RGBA")
    lp = land.load()
    W, H = land.size
    step, tick = 5 * scale, 5 * scale
    n = 0
    rings = []
    for p in (coast.geoms if hasattr(coast, "geoms") else [coast]):
        if p.geom_type == "Polygon":
            rings.append(p.exterior)
    for ring in rings:
        pts = np.asarray(shapely.segmentize(ring, step).coords)
        if len(pts) < 3:
            continue
        hs = height_at(pts)
        for k in range(1, len(pts) - 1):
            if hs[k] < high:
                continue
            (x0, y0), (x1, y1) = pts[k - 1], pts[k + 1]
            tx, ty = x1 - x0, y1 - y0
            norm = math.hypot(tx, ty) or 1.0
            nx, ny = -ty / norm, tx / norm
            px, py = pts[k]
            probe = (int(px + nx * 3 * scale), int(py + ny * 3 * scale))
            if not (0 <= probe[0] < W and 0 <= probe[1] < H) or lp[probe] < 128:
                nx, ny = -nx, -ny  # point the tick inland
            d.line([(px, py), (px + nx * tick, py + ny * tick)], fill=INK, width=max(1, round(scale)))
            n += 1
    return n


def crossings(img: Image.Image, roads: list[tuple[str, object]], rivers, scale: float) -> int:
    """A bridge where a road or highway crosses a river, a ford where a trail does. Returns how many."""
    d = ImageDraw.Draw(img, "RGBA")
    n = 0
    if rivers is None or rivers.is_empty:
        return 0
    for group, line in roads:
        if group == "searoutes":
            continue
        hit = line.intersection(rivers)
        pts = [hit] if hit.geom_type == "Point" else [g for g in getattr(hit, "geoms", []) if g.geom_type == "Point"]
        for p in pts:
            t = line.project(p)
            a, b = line.interpolate(max(0.0, t - 2 * scale)), line.interpolate(min(line.length, t + 2 * scale))
            ang = math.atan2(b.y - a.y, b.x - a.x)
            ux, uy, nx, ny = math.cos(ang), math.sin(ang), -math.sin(ang), math.cos(ang)
            L, o = 5 * scale, 2.2 * scale
            if group == "trails":  # a ford: stepping stones
                for k in (-1, 0, 1):
                    cx, cy = p.x + ux * k * 2.4 * scale, p.y + uy * k * 2.4 * scale
                    rr = 0.9 * scale
                    d.ellipse([cx - rr, cy - rr, cx + rr, cy + rr], fill=INK)
            else:  # a bridge: deck, two parapets with splayed ends
                deck = [(p.x - ux * L + nx * o, p.y - uy * L + ny * o), (p.x + ux * L + nx * o, p.y + uy * L + ny * o),
                        (p.x + ux * L - nx * o, p.y + uy * L - ny * o), (p.x - ux * L - nx * o, p.y - uy * L - ny * o)]
                d.polygon(deck, fill=PAPER)
                for sgn in (1, -1):
                    a0 = (p.x - ux * L + nx * o * sgn, p.y - uy * L + ny * o * sgn)
                    a1 = (p.x + ux * L + nx * o * sgn, p.y + uy * L + ny * o * sgn)
                    d.line([(a0[0] - ux * scale + nx * scale * sgn, a0[1] - uy * scale + ny * scale * sgn), a0, a1,
                            (a1[0] + ux * scale + nx * scale * sgn, a1[1] + uy * scale + ny * scale * sgn)],
                           fill=INK, width=max(1, round(scale)))
            n += 1
    return n

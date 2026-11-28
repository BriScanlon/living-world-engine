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
from shapely.geometry import LineString, MultiPolygon, Point, Polygon

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


# --------------------------------------------------------------------------- coastline detail (#62)
def _noise(xy: np.ndarray, seed: int) -> np.ndarray:
    """A value in [-1, 1] for each point, from its position (to the metre) and a seed: the same spot, the same value."""
    q = np.round(xy * 1000.0)
    v = np.sin(q[:, 0] * 12.9898 + q[:, 1] * 78.233 + seed * 0.618) * 43758.5453
    return (v - np.floor(v)) * 2 - 1


def _fractal_ring(pts: np.ndarray, seed: int, rough, min_len: float) -> np.ndarray:
    """Midpoint displacement on a closed ring (km): split each segment longer than ``min_len`` at its middle and push
    the middle sideways by ``rough`` x its length, again and again. ``rough`` is a number or a function of position."""
    for _ in range(24):
        nxt = np.roll(pts, -1, axis=0)
        d = nxt - pts
        length = np.hypot(d[:, 0], d[:, 1])
        split = length > min_len
        if not split.any():
            break
        mid = (pts + nxt)[split] / 2
        normal = np.column_stack([-d[split, 1], d[split, 0]]) / np.maximum(length[split, None], 1e-12)
        r = rough(mid) if callable(rough) else rough
        mid = mid + normal * (length[split] * r * _noise(mid, seed))[:, None]
        out = np.empty((len(pts) + int(split.sum()), 2))
        pos = np.arange(len(pts)) + np.concatenate([[0], np.cumsum(split)[:-1]])
        out[pos] = pts
        out[pos[split] + 1] = mid
        pts = out
    return pts


def fractal(geom, seed: int, rough=0.22, min_len: float = 0.1, keep=None):
    """Polygons (in km) with fractal detail added to every outline, down to segments of ``min_len`` km.

    ``keep`` (points or shapes, km) must stay inside: any detail that would cut one off is undone by unioning a small
    disc around it back in. Self-crossings from the displacement are mended with ``make_valid``."""
    out = []
    for p in polys(geom):
        ext = _fractal_ring(np.asarray(p.exterior.coords)[:-1], seed, rough, min_len)
        holes = [_fractal_ring(np.asarray(i.coords)[:-1], seed, rough, min_len) for i in p.interiors]
        g = shapely.make_valid(Polygon(ext, [h for h in holes if len(h) >= 3]))
        out += [q for q in polys(g) if q.area > min_len * min_len]
    g = shapely.unary_union(out) if out else MultiPolygon()
    if keep is not None and not g.is_empty:
        g = g.union(shapely.unary_union([shapely.buffer(k, min_len * 5) for k in keep]).intersection(geom.buffer(min_len * 5)))
    return g


def harbours(land, ports: list, seed: int = 0, reach: float = 30.0, stop: float = 0.25):
    """Carve an estuary from the sea to each port (km) the coast left inland: narrow at the town (which stays on
    land, ``stop`` km back from the water) and opening out towards the sea, with a fractal shore like the coast's."""
    from shapely.ops import nearest_points
    edge = land.boundary
    cuts = []
    for p in ports:
        if not land.contains(p):
            continue
        q = nearest_points(edge, p)[0]
        dist = q.distance(p)
        if dist > reach or dist < stop * 2:
            continue
        ux, uy = (q.x - p.x) / dist, (q.y - p.y) / dist  # towards the sea
        nx, ny = -uy, ux
        head = (p.x + ux * stop, p.y + uy * stop)
        out = dist + max(1.0, 0.3 * dist)  # past the coast, so the mouth opens cleanly
        w0, w1 = 0.15, max(0.6, 0.35 * dist)
        mouth = (p.x + ux * out, p.y + uy * out)
        wedge = Polygon([(head[0] + nx * w0, head[1] + ny * w0), (mouth[0] + nx * w1, mouth[1] + ny * w1),
                         (mouth[0] - nx * w1, mouth[1] - ny * w1), (head[0] - nx * w0, head[1] - ny * w0)])
        cuts.append(fractal(wedge, seed + 7, rough=0.16, min_len=0.05).buffer(0))
    if not cuts:
        return land
    g = land.difference(shapely.unary_union(cuts))
    return g.union(shapely.unary_union([p.buffer(stop * 0.8) for p in ports if land.contains(p)]).intersection(land))


# --------------------------------------------------------------------------- the detailed coast at import (#62)
MAX_COAST_POINTS = 400_000  # a whole-planet Azgaar map gets coarser detail rather than millions of points
POINTS_PER_CELL = 70  # and a coarse map (few, big cells) no more detail than it can use


class KmFrame:
    """A local equirectangular frame in km, so detail is measured in real distances."""

    def __init__(self, lat0: float):
        self.kx, self.ky = math.cos(math.radians(lat0)) * 111.32, 111.32

    def to_km(self, geom):
        return shapely.transform(geom, lambda a: np.column_stack([a[:, 0] * self.kx, a[:, 1] * self.ky]))

    def to_ll(self, geom):
        return shapely.transform(geom, lambda a: np.column_stack([a[:, 0] / self.kx, a[:, 1] / self.ky]))


def _roughness(relief_km: list[tuple[float, float, int]], cell_km: float):
    """Rocky where the coast is high, gentler where it's low: rough = 0.18 (flats, marsh) .. 0.33 (cliffs)."""
    if not relief_km:
        return 0.24
    g = 2 * cell_km
    xs = np.array([p[0] for p in relief_km]); ys = np.array([p[1] for p in relief_km])
    x0, y0 = xs.min(), ys.min()
    nx, ny = int((xs.max() - x0) / g) + 2, int((ys.max() - y0) / g) + 2
    grid = np.full((nx, ny), 20.0)
    ix, iy = ((xs - x0) / g).astype(int), ((ys - y0) / g).astype(int)
    np.maximum.at(grid, (ix, iy), np.array([p[2] for p in relief_km], dtype=float))

    def rough(xy: np.ndarray) -> np.ndarray:
        i = np.clip(((xy[:, 0] - x0) / g).astype(int), 0, nx - 1)
        j = np.clip(((xy[:, 1] - y0) / g).astype(int), 0, ny - 1)
        return 0.18 + 0.15 * np.clip((grid[i, j] - 22) / 40, 0, 1)
    return rough


def detailed_coast(land_ll, *, seed: int, cell_km: float, relief_ll: list[tuple[float, float, int]] = (),
                   towns_ll: list[tuple[float, float]] = (), ports_ll: list[tuple[float, float]] = (),
                   land_cells: int = 0):
    """The land (lon/lat, cell outlines) with a coast a cartographer would draw: the cell zigzag smoothed away, then
    fractal coves and headlands down to ~100 m (coarser on a very large map), rougher on high ground; towns kept on
    land and an estuary carved to each port the smoothing left inland. Lakes (holes) get the same treatment."""
    minx, miny, maxx, maxy = land_ll.bounds
    km = KmFrame((miny + maxy) / 2)
    land = km.to_km(land_ll)
    base = smooth(land, Wobble(seed, 0, 1), cell_km).simplify(cell_km * 0.2)
    length = sum(p.exterior.length + sum(i.length for i in p.interiors) for p in polys(base))
    budget = min(MAX_COAST_POINTS, max(50_000, POINTS_PER_CELL * land_cells)) if land_cells else MAX_COAST_POINTS
    min_len = max(0.1, length * 1.4 / budget)
    pt = lambda c: Point(c[0] * km.kx, c[1] * km.ky)  # noqa: E731
    towns = [pt(c) for c in towns_ll]
    rough = _roughness([(x * km.kx, y * km.ky, h) for x, y, h in relief_ll], cell_km)
    g = fractal(base, seed, rough=rough, min_len=min_len, keep=[t for t in towns if land.buffer(cell_km * 0.1).contains(t)])
    g = harbours(g, [pt(c) for c in ports_ll], seed=seed, reach=cell_km * 1.5, stop=max(0.25, min_len * 2.5))
    g = shapely.make_valid(shapely.set_precision(km.to_ll(g), 1e-5))
    return MultiPolygon([p for p in polys(g) if p.area > 0])


def allocate(shapes: dict, land_detail, land_cells) -> dict:
    """Fit shapes made of cells (provinces, biomes) to the detailed coast: clip each to it, then give every piece of
    land none of them covers (new headlands, slivers) to the nearest shape, so they tile the land exactly."""
    out = {k: g.intersection(land_detail) for k, g in shapes.items()}
    keys = [k for k, g in out.items() if not g.is_empty]
    if not keys:
        return out
    covered = shapely.unary_union([out[k] for k in keys])
    gaps = [p for p in polys(land_detail.difference(covered)) if p.area > 0]
    if gaps:
        # nearest by the plain cell shapes (few vertices), from a point inside each gap: same owner, far quicker
        tree = shapely.STRtree([shapes[k] for k in keys])
        near = tree.query_nearest([g.representative_point() for g in gaps], return_distance=False, all_matches=False)
        extra: dict = {}
        for gi, ki in zip(near[0], near[1]):
            extra.setdefault(keys[ki], []).append(gaps[gi])
        for k, ps in extra.items():
            out[k] = shapely.unary_union([out[k], *ps])
    return out


def river_to_coast(line_ll, land_detail):
    """A river on the detailed coast: cut where it first leaves the land (from its source), or carried on to the
    nearest shore if the new coast left its mouth inland."""
    from shapely.ops import nearest_points
    inside = line_ll.intersection(land_detail)
    parts = [inside] if inside.geom_type == "LineString" else [p for p in getattr(inside, "geoms", []) if p.geom_type == "LineString"]
    src = Point(line_ll.coords[0])
    first = min(parts, key=lambda p: p.distance(src), default=None)
    if first is None or first.is_empty:
        return line_ll
    coords = list(first.coords)
    end = Point(coords[-1])
    if not land_detail.contains(Point(line_ll.coords[-1])) and land_detail.boundary.distance(end) > 1e-6:
        q = nearest_points(land_detail.boundary, end)[0]
        coords.append((q.x, q.y))
    return LineString(coords)

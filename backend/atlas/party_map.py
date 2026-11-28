"""The party's map: the atlas as the player characters know it, drawn as an overland map for the players' table.

Rendered on the server from the atlas's GeoJSON layers (no browser needed), so it can refresh itself whenever the
party learns something. Fog follows :mod:`atlas.party`: clear where the party has been, thinned where it has only
heard of a place, near-black elsewhere, with soft edges. The fog covers the sea too, so unknown coastlines don't
give themselves away. Labels are drawn only for what the party knows.
"""
from __future__ import annotations

import io
import json
import math
from typing import Any

from PIL import Image, ImageDraw, ImageFilter, ImageFont
import shapely
import shapely.prepared
from shapely.geometry import Point, box, shape
from shapely.ops import unary_union

from atlas import cartography as carto
from atlas import emblems, frame, symbols
from atlas.travel import haversine_km
from atlas.service import atlas_path, atlas_state
from graph.store import GraphStore

SIZE = (2560, 1440)  # 16:9 like the table; the VTT shows it whole ("contain")
SS = 2  # draw at twice the size, then shrink: cheap anti-aliasing for polygons and lines
SEA, SHALLOW, RIPPLE = (128, 164, 176), (176, 204, 204), (86, 122, 134)
LAND, BORDER, COAST, LANE = (233, 218, 180), (104, 74, 96), (78, 58, 38), (236, 244, 246)
RIVER, ROAD, INK = (79, 127, 166), (122, 90, 51), (40, 30, 20)
FOG = (222, 207, 170)  # unexplored: blank, faded parchment
# the two looks: "colour" (washes of biome and realm colour) and "ink" (paper and ink only, as on Tolkien's maps)
STYLES = {
    "colour": {"sea": SEA, "shallow": SHALLOW, "ripple": RIPPLE, "land": LAND, "border": BORDER, "coast": COAST,
               "river": RIVER, "road": ROAD, "lane": LANE, "washes": True, "water_ink": (52, 86, 110, 255),
               "water_halo": (200, 220, 224, 170)},
    "ink": {"sea": (228, 220, 198), "shallow": None, "ripple": (70, 58, 44), "land": (241, 233, 210),
            "border": (70, 55, 45), "coast": (36, 28, 20), "river": (50, 56, 66), "road": (62, 46, 30),
            "lane": (120, 108, 92), "washes": False, "water_ink": (46, 40, 34, 255),
            "water_halo": (228, 220, 198, 200)},
}
SYMBOL_SCALE = 0.6  # terrain symbols at 60% of their base size: fine detail, like a printed map
FOG_UNKNOWN, FOG_HEARD = 253, 150  # fog alpha over the unknown (opaque enough that no ink shows) and the heard-of
FADED, HALO = (128, 104, 76), (236, 226, 196)  # lettering for what the party has only heard of; the paper halo
SETTLEMENT_KM = 25  # a building or district this close to a town is part of it (as travel treats it)
MARGIN, BAND = 14, 12  # the frame: margin outside it and the graticule band, in base pixels


def _cell_px(proj: "Projection", relief: list[dict[str, Any]], land) -> float | None:
    """The map's cell size in pixels, from how many relief points fall on the land drawn."""
    if not relief or land.is_empty:
        return None
    import numpy as np
    xy = proj.arr(np.array([f["geometry"]["coordinates"] for f in relief], dtype=float))
    minx, miny, maxx, maxy = land.bounds
    inside = int(((xy[:, 0] >= minx) & (xy[:, 0] <= maxx) & (xy[:, 1] >= miny) & (xy[:, 1] <= maxy)).sum())
    return math.sqrt(land.area / inside) if inside else None


def _relief_px(proj: "Projection", relief: list[dict[str, Any]]):
    """Relief cells as rows of (x, y, height, biome) in pixels."""
    import numpy as np
    if not relief:
        return np.zeros((0, 4))
    xy = proj.arr(np.array([f["geometry"]["coordinates"] for f in relief], dtype=float))
    hb = np.array([(f["properties"]["h"], f["properties"]["b"]) for f in relief], dtype=float)
    return np.column_stack([xy, hb])


def _height_at(cells, cell: float):
    """Height lookup for any pixel: the highest cell within about a cell of it (0 where there's none)."""
    import numpy as np
    x0, y0 = cells[:, 0].min(), cells[:, 1].min()
    nx, ny = int((cells[:, 0].max() - x0) / cell) + 3, int((cells[:, 1].max() - y0) / cell) + 3
    grid = np.zeros((nx, ny))
    np.maximum.at(grid, (((cells[:, 0] - x0) / cell).astype(int) + 1, ((cells[:, 1] - y0) / cell).astype(int) + 1), cells[:, 2])
    pad = np.pad(grid, 1)
    grid = np.max([pad[1 + dx:nx + 1 + dx, 1 + dy:ny + 1 + dy] for dx in (-1, 0, 1) for dy in (-1, 0, 1)], axis=0)

    def at(pts):
        i = np.clip(((pts[:, 0] - x0) / cell).astype(int) + 1, 0, nx - 1)
        j = np.clip(((pts[:, 1] - y0) / cell).astype(int) + 1, 0, ny - 1)
        return grid[i, j]
    return at


def _lay_out_places(layout: "frame.Labels", proj: "Projection", places: list[dict[str, Any]], party: dict[str, Any],
                    events: list[dict[str, Any]] = (), near: float = 1.0) -> list[dict[str, Any]]:
    """Where each known place's symbol goes and where its name fits; returns the symbols (marks) to draw."""
    marks: list[dict[str, Any]] = []
    # every settlement gets its own symbol; buildings and districts are listed under the settlement they sit in
    towns = [f for f in places if f["properties"].get("kind") in emblems.SETTLEMENTS]
    groups = _clusters(towns, proj, 8 * SS)
    for f in places:
        if f["properties"].get("kind") in emblems.SETTLEMENTS or f["properties"].get("kind") == "continent":
            continue
        c = f["geometry"]["coordinates"]
        host = min(groups, default=None, key=lambda g: haversine_km(c, g[0]["geometry"]["coordinates"]))
        if host and haversine_km(c, host[0]["geometry"]["coordinates"]) < SETTLEMENT_KM:  # in that town
            host.append(f)
        else:
            groups.append([f])
    groups.sort(key=lambda g: (not any(p["properties"].get("here") for p in g), RANK.get(g[0]["properties"].get("kind"), 9)))
    # crowded maps draw smaller symbols: scaled to how far apart the settlements typically are
    pts = [proj(*g[0]["geometry"]["coordinates"]) for g in groups]
    gaps = sorted(min((math.dist(a, b) for b in pts if b is not a), default=1e9) for a in pts)
    k = min(1.0, max(0.45, (gaps[len(gaps) // 2] if gaps else 1e9) / (40 * SS)))
    for group in groups:  # emblems first, so labels avoid the ones that matter
        head = group[0]["properties"]
        x, y = proj(*group[0]["geometry"]["coordinates"])
        emblem = emblems.emblem_for(head.get("kind"), head.get("name"))
        major = head.get("kind") in ("capital", "city") or (emblem and head.get("kind") not in emblems.SETTLEMENTS)
        here = any(g["properties"].get("here") for g in group)
        if not (major or here) and near < SHOW_TOWN_SYMBOLS:
            emblem = None  # far out, a lesser town is just a dot
        scale = max(k, 0.7) if major or here else k
        half = emblems.SIZE.get(emblem, 22) * SS * 0.5 * scale if emblem else (4 if near >= SHOW_TOWN_SYMBOLS else 2.5) * SS
        state = "visited" if any(g["properties"]["known"] == "visited" for g in group) else "heard"
        box = (x - half * 1.1, y - half * 1.3, x + half * 1.1 + (22 * SS * scale if head.get("port") else 0), y + half * 1.1)
        marks.append({"x": x, "y": y, "emblem": emblem, "half": half, "state": state, "box": box, "group": group,
                      "port": head.get("port") and (major or here or near >= SHOW_TOWN_SYMBOLS), "here": here,
                      "scale": scale, "major": bool(major)})
        if major or here:  # lesser settlements' symbols may sit under a label's halo
            layout.reserve(box)
    for e in events:  # battles the party knows of
        if (e.get("properties") or {}).get("event_type") != "conflict":
            continue
        x, y = proj(*e["geometry"]["coordinates"])
        x += 16 * SS
        y -= 16 * SS
        half = emblems.SIZE["battle"] * SS * 0.5
        box = (x - half, y - half, x + half, y + half)
        marks.append({"x": x, "y": y, "emblem": "battle", "half": half, "state": "visited", "box": box, "group": []})
        layout.reserve(box)
    for m in marks:
        if not m["group"]:
            continue
        head = m["group"][0]["properties"]
        rest = [g["properties"] for g in m["group"][1:]]
        if not (m.get("major") or m["here"] or head.get("start")) and near < SHOW_TOWN_NAMES:
            continue  # far out: only the main names
        if near < SHOW_TOWN_NAMES and not m["here"]:
            rest = []  # and no lists of buildings
        x, y, half = m["x"], m["y"], m["half"]
        bx0, by0, bx1, by1 = m["box"]  # labels go just outside the symbol's own box
        seen = m["state"] == "visited"
        big = head.get("kind") in ("capital", "city")
        sk = max(m.get("scale", 1.0), 0.7 if big or m["here"] else 0.55)  # lettering shrinks on a crowded map too
        font = _font(int((26 if big else 22 if seen else 19) * SS * sk), bold=big)
        fill, gap = (INK if seen else FADED), 11 * SS  # clear of the symbol's box, halo and descenders included
        layout.add(head["name"], font, [(x, by0 - gap, "ms"), (bx1 + gap, y, "lm"), (bx0 - gap, y, "rm"),
                                        (x, by1 + gap, "mt")], fill, HALO)
        below = by1
        if rest:
            names = [p["name"] + ("" if p["known"] == "visited" else " (heard of)") for p in rest[:3]]
            text = " \u00b7 ".join(names) + (f" \u00b7 +{len(rest) - 3}" if len(rest) > 3 else "")
            lab = layout.add(text, _font(int(16 * SS * max(sk, 0.75))), [(x, below + 20 * SS, "ms"), (bx1 + gap, y + 20 * SS, "lm"),
                                                     (bx0 - gap, y + 20 * SS, "rm")], INK if seen else FADED, HALO)
            if lab:
                below = lab.box[3]
        if m["here"]:
            where = next(p["name"] for p in [head, *rest] if p.get("here"))
            text = f"{party['name']} is here" + (f" ({where})" if where != head["name"] else "")
            layout.add(text, _font(18 * SS, bold=True), [(x, below + 24 * SS, "ms"), (x, by0 - 34 * SS, "ms"),
                                                         (bx1 + gap, y + 44 * SS, "lm")], (150, 88, 20), HALO)
    return marks


WATER_INK, WATER_HALO = (52, 86, 110, 255), (200, 220, 224, 170)

# What is shown at which closeness (median town spacing / 40 base px): far out only the main names; more as you zoom in
SHOW_TOWN_NAMES, SHOW_TOWN_SYMBOLS, SHOW_PROVINCES, SHOW_BAYS, SHOW_LAKES = 0.75, 0.5, 0.6, 0.6, 0.75


def closeness(places: list[dict[str, Any]], proj: "Projection") -> float:
    """How far in this map is: the median spacing of its settlements over the spacing that letters them comfortably
    (1 = room for every town's name; a whole region seen at once is about 0.3)."""
    pts = [proj(*f["geometry"]["coordinates"]) for f in places if f["properties"].get("kind") in emblems.SETTLEMENTS]
    if len(pts) < 2:
        return 1.0
    gaps = sorted(min(math.dist(a, b) for b in pts if b is not a) for a in pts)
    return gaps[len(gaps) // 2] / (40 * SS)


def polylabel_px(g) -> tuple[float, float]:
    """A point well inside a (pixel) shape: the pole of inaccessibility of its largest part."""
    from shapely.ops import polylabel
    big = max(carto.polys(g), key=lambda q: q.area)
    q = polylabel(big, tolerance=max(1.0, math.sqrt(big.area) / 50))
    return q.x, q.y


def known_waters(waters: list[dict[str, Any]], known_area, min_share: float = 0.002) -> list[tuple[dict[str, Any], Any]]:
    """The named waters the party can name, each with the part of it the party knows (seen or heard of): a cartographer
    letters the stretch of sea you know, not its far middle."""
    if known_area is None or known_area.is_empty:
        return []
    out = []
    for w in waters:
        g = shape(w["geometry"]).buffer(0)
        if g.geom_type == "Point":  # older layers kept only the label point
            if known_area.contains(g):
                out.append((w, g))
            continue
        part = g.intersection(known_area)
        if not part.is_empty and part.area >= min_share * known_area.area:
            out.append((w, part))
    return out


def _lay_out_waters(layout: "frame.Labels", proj: "Projection", waters: list[dict[str, Any]], known_area,
                    ink=WATER_INK, halo=WATER_HALO, near: float = 1.0) -> None:
    """Sea, gulf, bay and strait names in slanted, spaced sea-blue capitals (lakes in plain slanted letters), sized
    to the room the water has, biggest first."""
    from shapely.ops import polylabel
    for w, part in sorted(known_waters(waters, known_area), key=lambda wp: -(wp[0]["properties"].get("area_km2") or 0)):
        p = w["properties"]
        kind = p.get("kind")
        if (kind in ("gulf", "bay", "strait") and near < SHOW_BAYS) or (kind == "lake" and near < SHOW_LAKES):
            continue  # far out: only the seas
        room_deg = None
        if part.geom_type == "Point":
            at = part
        else:
            big = max(carto.polys(part), key=lambda q: q.area)
            at = polylabel(big, tolerance=0.01)
            # the room the known stretch gives: across it, at most a few times its depth at the label point
            room_deg = min(big.bounds[2] - big.bounds[0], 5 * at.distance(big.exterior))
        x, y = proj(at.x, at.y)
        lake = p.get("kind") == "lake"
        text = p["name"] if lake else frame.spaced(p["name"])
        room = (p.get("width_km") or 50) / proj.km_per_px()
        if room_deg is not None:
            room = min(room, proj(at.x + room_deg, at.y)[0] - x)
        cap = (30 if p.get("kind") == "sea" else 22) * SS
        size = int(min(cap, room / max(1, len(text)) * 1.5))
        if size < 12 * SS:  # no room to letter it legibly here
            continue
        layout.add(text, _font(size), [(x, y + dy * SS, "mm") for dy in (0, -36, 36, -72, 72)], ink, halo,
                   italic=True)


def _lay_out_regions(layout: "frame.Labels", proj: "Projection", known: dict[str, Any], provinces: dict[str, Any],
                     realms_only: bool = False, near: float = 1.0) -> None:
    """Realm names, large and spaced across the provinces of theirs the party has been in (``realms_only``); or the
    province names, each kept inside its province."""
    realms: dict[str, list] = {}
    for uid, p in known.items():
        g = provinces.get(uid)
        if g is None or not p.get("name"):
            continue
        seen = p["known"] == "visited"
        if realms_only:
            if seen and p.get("controller_name"):
                realms.setdefault(p["controller_name"], []).append(g)
            continue
        if near < (SHOW_PROVINCES if seen else SHOW_TOWN_NAMES):
            continue  # far out the realms are named, not their provinces
        x, y = proj(*g.representative_point().coords[0])
        cands = [(x, y + dy * SS, "mm") for dy in (0, -30, 30, -60, 60)]
        cands = [c for c in cands if g.contains(Point(*proj.inverse(c[0], c[1])))] or cands[:1]
        layout.add(frame.spaced(p["name"]) if seen else p["name"], _font((17 if seen else 16) * SS, bold=seen), cands,
                   (92, 66, 40) if seen else FADED, HALO)
    for name, gs in realms.items():
        g = shapely.make_valid(carto.to_px(unary_union(gs), proj.arr))
        if g.is_empty or not carto.polys(g):  # nothing with an area to letter across
            continue
        g = unary_union(carto.polys(g))
        rect = g.minimum_rotated_rectangle
        rc = list(rect.exterior.coords)[:4] if rect.geom_type == "Polygon" else []
        if len(rc) < 4:
            continue
        e1, e2 = (rc[1][0] - rc[0][0], rc[1][1] - rc[0][1]), (rc[2][0] - rc[1][0], rc[2][1] - rc[1][1])
        long_e = e1 if math.hypot(*e1) >= math.hypot(*e2) else e2
        length = math.hypot(*long_e)
        ang = math.degrees(math.atan2(long_e[1], long_e[0]))
        ang = (ang + 90) % 180 - 90  # read left to right
        ang = max(-35.0, min(35.0, ang))  # and never steeper than old maps letter a realm
        text = frame.spaced(name, gap=2)
        unit = _font(100).getlength(text) / 100  # width per pixel of type size
        size = min(30 * SS, 0.8 * length / max(unit, 1e-6))
        if size < 13 * SS:  # too small on this map to letter across
            continue
        c = polylabel_px(g)
        land = shapely.prepared.prep(g)
        for k in (1.0, 0.8, 0.65):  # the biggest lettering that fits on the realm's own land
            if size * k < 13 * SS:
                break
            font = _font(int(size * k))
            half = font.getlength(text) / 2 * 1.08
            paths = []
            for a_ in dict.fromkeys((ang, 0.0, 15.0, -15.0)):
                ux, uy = math.cos(math.radians(a_)), math.sin(math.radians(a_))
                nx, ny = uy, -ux  # "up" from the baseline
                for shift in (0, -50, 50, -100, 100):
                    for bow in (1, -1):
                        pts = []
                        for i in range(25):
                            tt = i / 24 * 2 - 1
                            off = bow * 0.1 * half * (1 - tt * tt) + shift * SS
                            pts.append((c[0] + ux * half * tt + nx * off, c[1] + uy * half * tt + ny * off))
                        paths.append(pts)
            if layout.add_along(text, font, paths, (118, 84, 54, 150), (236, 226, 196, 90),
                                inside=lambda x, y: land.contains(Point(x, y)), share=0.85):
                break



class NothingKnown(ValueError):
    pass


def _features(slug: str, layer: str) -> list[dict[str, Any]]:
    p = atlas_path(slug) / f"{layer}.geojson"
    if not p.exists():
        return []
    return json.loads(p.read_text(encoding="utf-8")).get("features", [])


def _font(px: int, bold: bool = False) -> ImageFont.ImageFont:
    return carto.serif(px, bold)


def _hex(c: str | None, default=(154, 163, 173)) -> tuple[int, int, int]:
    if c and c.startswith("#") and len(c) == 7:
        return tuple(int(c[i:i + 2], 16) for i in (1, 3, 5))  # type: ignore[return-value]
    if c and c.startswith("hsl("):
        h, s, l = (float(x.strip(" %")) for x in c[4:-1].split(","))
        import colorsys
        r, g, b = colorsys.hls_to_rgb(h / 360, l / 100, s / 100)
        return int(r * 255), int(g * 255), int(b * 255)
    return default


class Projection:
    """Local equirectangular projection fitted to a lon/lat box, in supersampled pixels."""

    def __init__(self, bounds: tuple[float, float, float, float], size: tuple[int, int]):
        minx, miny, maxx, maxy = bounds
        self.k = max(0.2, math.cos(math.radians((miny + maxy) / 2)))
        self.w, self.h = size
        span_x = max(1e-6, (maxx - minx) * self.k)
        span_y = max(1e-6, maxy - miny)
        self.s = min(self.w / span_x, self.h / span_y)  # px per projected degree
        self.cx, self.cy = (minx + maxx) / 2 * self.k, (miny + maxy) / 2

    def __call__(self, lon: float, lat: float) -> tuple[float, float]:
        return (lon * self.k - self.cx) * self.s + self.w / 2, self.h / 2 - (lat - self.cy) * self.s

    def inverse(self, x: float, y: float) -> tuple[float, float]:
        """Pixels back to (lon, lat)."""
        return ((x - self.w / 2) / self.s + self.cx) / self.k, self.cy - (y - self.h / 2) / self.s

    def arr(self, a):
        """Many (lon, lat) at once: an (N, 2) array to pixels."""
        import numpy as np
        return np.column_stack([(a[:, 0] * self.k - self.cx) * self.s + self.w / 2, self.h / 2 - (a[:, 1] - self.cy) * self.s])

    def km_per_px(self) -> float:
        return 111.32 / self.s


def _rings(geom) -> list[tuple[list, list[list]]]:
    polys = [geom] if geom.geom_type == "Polygon" else list(getattr(geom, "geoms", []))
    return [(list(p.exterior.coords), [list(i.coords) for i in p.interiors]) for p in polys if p.geom_type == "Polygon"]


def _fill(draw: ImageDraw.ImageDraw, proj: Projection, geom, fill, hole=None, outline=None, width=0) -> None:
    for ext, holes in _rings(geom):
        if len(ext) >= 3:
            draw.polygon([proj(*c[:2]) for c in ext], fill=fill, outline=outline, width=width)
        for h in holes:
            if hole is not None and len(h) >= 3:
                draw.polygon([proj(*c[:2]) for c in h], fill=hole)


def _dash(draw: ImageDraw.ImageDraw, pts, fill, width: int, dash: int, gap: int | None = None) -> None:
    """A dashed line through pixel points, the dashes running on across corners."""
    pts = [tuple(p) for p in pts]
    gap = dash if gap is None else gap
    on, left = True, dash
    for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
        seg = math.hypot(x1 - x0, y1 - y0)
        t = 0.0
        while t < seg:
            step = min(left, seg - t)
            if on:
                a, b = t / seg, (t + step) / seg
                draw.line([(x0 + (x1 - x0) * a, y0 + (y1 - y0) * a), (x0 + (x1 - x0) * b, y0 + (y1 - y0) * b)],
                          fill=fill, width=width)
            t += step
            left -= step
            if left <= 0:
                on = not on
                left = dash if on else gap


def _label(draw: ImageDraw.ImageDraw, xy, text: str, px: int, fill=INK, halo=(236, 226, 196), anchor="ms",
           bold: bool = False) -> None:
    draw.text(xy, text, font=_font(px, bold), fill=fill, anchor=anchor, stroke_width=max(2, px // 8), stroke_fill=halo)


RANK = {"capital": 0, "city": 1, "town": 2, "village": 3, "district": 4}


def _clusters(features: list[dict[str, Any]], proj: Projection, radius: float) -> list[list[dict[str, Any]]]:
    """Places within ``radius`` px of each other share one marker: the biggest settlement first, then the rest."""
    groups: list[list[dict[str, Any]]] = []
    for f in sorted(features, key=lambda f: (RANK.get(f["properties"].get("kind"), 9), f["properties"]["name"])):
        x, y = proj(*f["geometry"]["coordinates"])
        for g in groups:
            gx, gy = proj(*g[0]["geometry"]["coordinates"])
            if math.hypot(x - gx, y - gy) < radius:
                g.append(f)
                break
        else:
            groups.append([f])
    return groups


def draw_world(proj, W: int, H: int, slug: str, pal: dict[str, Any], seed: int, ss: float, *,
               provinces: dict[str, Any] | None = None, realm_tints: list | None = None, avoid_boxes=(),
               cell_px: float | None = None, budget: int = 80000, wobble: bool = True, keep: tuple | None = None,
               density: float = 1.0):
    """The world as a cartographer draws it, on a W x H canvas through ``proj`` (anything with ``__call__``, ``arr``,
    ``inverse``): sea, shallows and ripples, land, biome and realm washes, borders, terrain symbols, rivers, roads,
    bridges, the inked coast and cliffs. ``ss`` scales every line and symbol. Returns (image, land in pixels, land
    mask, relief cells in pixels, cell size in pixels).

    For tiles that must meet seamlessly (#57): pass ``cell_px`` (the same for every tile of a zoom), ``wobble=False``
    and a large ``budget`` (so nothing depends on what else is in the frame), and ``keep`` = the (x0, y0, x1, y1) a
    symbol's foot must be in (the tile plus a margin both neighbours draw alike)."""
    view = box(-0.1 * W, -0.1 * H, 1.1 * W, 1.1 * H)
    wob = carto.Wobble(seed, amp=1.6 * ss if wobble else 0, scale=40 * ss)
    (ax, ay), (bx_, by_) = proj.inverse(-0.15 * W, 1.15 * H), proj.inverse(1.15 * W, -0.15 * H)

    def px(geom):
        rect = (min(ax, bx_), min(ay, by_), max(ax, bx_), max(ay, by_))
        try:
            geom = shapely.clip_by_rect(geom, *rect)
        except shapely.errors.GEOSException:  # a degenerate ring: the slower, sturdier way
            geom = shapely.make_valid(geom).intersection(box(*rect))
        g = carto.to_px(geom, proj.arr)
        if not g.is_valid:  # projecting can pinch a ring; mend it rather than fail
            g = shapely.make_valid(g)
        return g.intersection(view)

    land_feats = _features(slug, "land")
    raw_land = unary_union([px(shape(f["geometry"]).buffer(0)) for f in land_feats])
    relief = [f for f in _features(slug, "relief")]
    cell = cell_px or _cell_px(proj, relief, raw_land) or 30.0 * ss
    if any((f.get("properties") or {}).get("detail") for f in land_feats):  # already drawn at import (#62)
        land = raw_land.simplify(0.5 * ss)
    else:  # an older import: cell outlines, smoothed here
        land = carto.smooth(raw_land, wob, cell)
    img = Image.new("RGBA", (W, H), pal["sea"] + (255,))
    if pal["shallow"]:
        img.alpha_composite(carto.shallows((W, H), land, 22 * ss / 2 * 2, pal["shallow"] + (170,)))
    draw = ImageDraw.Draw(img, "RGBA")
    carto.ripples(draw, land, [9 * ss, 18 * ss, 29 * ss], pal["ripple"] + (150,), max(1, round(ss / 2 * 2)))
    carto.fill(draw, land, pal["land"] + (255,), hole=pal["sea"] + (255,))
    mask = Image.new("L", (W, H), 0)
    carto.fill(ImageDraw.Draw(mask), land, 255, hole=0)
    if pal["washes"]:
        paint = []
        for f in _features(slug, "biomes"):  # biomes, muted into the parchment
            c = _hex(f["properties"].get("color"))
            paint.append((px(shape(f["geometry"]).buffer(0)),
                          tuple(int(0.65 * c[i] + 0.35 * pal["land"][i]) for i in range(3)) + (110,)))
        tint = carto.wash((W, H), paint, cell * 0.45)
        if realm_tints:  # who holds the provinces: a soft wash of the realm's colour
            tint.alpha_composite(carto.wash((W, H), [(px(g.buffer(0)), c) for g, c in realm_tints], cell * 0.8))
        base = img.copy()
        base.alpha_composite(tint)
        img = Image.composite(base, img, mask)  # colour stays inside the coast
    draw = ImageDraw.Draw(img, "RGBA")
    if provinces:
        prov_px = [px(g.buffer(0)) for g in provinces.values()]
        for pts in carto.borders(prov_px, wob, cell, clip=land.buffer(-1.5 * ss)):
            _dash(draw, pts, pal["border"] + (150,), max(1, round(ss)), int(1.5 * ss), gap=int(3 * ss))  # dotted
    river_lines, road_lines = [], []
    for f in _features(slug, "rivers"):
        g = px(shape(f["geometry"]))
        for ln in ([g] if g.geom_type == "LineString" else list(getattr(g, "geoms", []))):
            if ln.geom_type == "LineString" and ln.length >= 4 * ss:
                river_lines.append((ln, carto.smooth_line(ln.coords, carto.Wobble(seed + 1, 1.5 * ss if wobble else 0,
                                                                                  30 * ss), 4 * ss)))
    for f in _features(slug, "roads"):
        group = (f.get("properties") or {}).get("group")
        g = px(shape(f["geometry"]))
        for ln in ([g] if g.geom_type == "LineString" else list(getattr(g, "geoms", []))):
            if ln.geom_type == "LineString":
                road_lines.append((group, carto.smooth_line(ln.coords, carto.Wobble(seed + 2, 0, 1), 6 * ss)))

    # terrain symbols (#55): kept off rivers, roads, towns and lettering, inside the coast, back to front
    avoid = Image.new("L", (W, H), 0)
    adraw = ImageDraw.Draw(avoid)
    for _, pts in river_lines:
        adraw.line([tuple(q) for q in pts], fill=255, width=int(7 * ss))
    for group, pts in road_lines:
        if group != "searoutes":
            adraw.line([tuple(q) for q in pts], fill=255, width=int(6 * ss))
    for bx in avoid_boxes:
        adraw.rectangle(bx, fill=255)
    cells = _relief_px(proj, relief)
    if len(cells):
        placed = symbols.place(cells, cell, mask, avoid, ss * SYMBOL_SCALE, seed, budget=budget, density=density)
        if keep:
            placed = [s for s in placed if keep[0] <= s.x < keep[2] and keep[1] <= s.y < keep[3]]
        symbols.draw(img, placed)
    draw = ImageDraw.Draw(img, "RGBA")
    for ln, pts in river_lines:
        carto.taper(draw, pts, 0.8 * ss / 2 * 2, min(5.0, 1.6 + ln.length / (350 * ss)) * ss, pal["river"] + (255,))
    for group, pts in road_lines:
        if group == "searoutes":  # sea lanes: sailed
            _dash(draw, pts, pal["lane"] + (210,), int(2 * ss / 2 * 2), int(5 * ss))
        else:
            _dash(draw, pts, pal["road"] + (225,), int((3 if group == "highways" else 2) * ss), int(9 * ss))
    from shapely.geometry import LineString as _Line
    symbols.crossings(img, [(g, _Line(pts)) for g, pts in road_lines if len(pts) > 1],
                      unary_union([_Line(pts) for _, pts in river_lines if len(pts) > 1]) if river_lines else None, ss)
    draw = ImageDraw.Draw(img, "RGBA")
    carto.outline(draw, land, pal["coast"] + (255,), int(2 * ss))  # the inked coast, lakes too
    if len(cells):
        symbols.cliffs(img, land, mask, _height_at(cells, cell), ss)
    return img, land, mask, cells, cell


def render(store: GraphStore, branch: str = "main", day: int | None = None,
           size: tuple[int, int] = SIZE, style: str | None = None) -> tuple[bytes, dict[str, Any]]:
    """The party's overland map as a WebP image, plus what it shows. ``style``: "colour" or "ink" (default: the
    ``PARTY_MAP_STYLE`` setting)."""
    from config import get_settings
    style = style if style in STYLES else (get_settings().party_map_style if get_settings().party_map_style in STYLES
                                            else "colour")
    pal = STYLES[style]
    st = atlas_state(store, branch, day, None, "party")
    if not st.get("atlas"):
        raise NothingKnown("There's no atlas in this world yet")
    party = st.get("party") or {}
    if not party.get("name"):
        raise NothingKnown("This world has no party (a Faction with is_party)")
    slug = st["atlas"]["slug"]
    provinces = {f["properties"].get("pid"): shape(f["geometry"]) for f in _features(slug, "provinces")}
    known = {uid: p for uid, p in st["provinces"].items() if p["known"]}
    seen = [provinces[u] for u, p in known.items() if p["known"] == "visited" and u in provinces]
    heard = [provinces[u] for u, p in known.items() if p["known"] == "heard" and u in provinces]
    places = [f for f in st["places"]["features"] if f["properties"].get("known")]
    if not seen and not heard and not places:
        raise NothingKnown(f"{party['name']} doesn't know anywhere yet")

    # ---- the region: everything known, with a margin of fog around it
    focus = unary_union(seen + heard + [Point(f["geometry"]["coordinates"]) for f in places])
    minx, miny, maxx, maxy = focus.bounds
    pad = max(1.5, 0.35 * max(maxx - minx, maxy - miny))
    bounds = (minx - pad, max(-85, miny - pad), maxx + pad, min(85, maxy + pad))
    W, H = size[0] * SS, size[1] * SS
    proj = Projection(bounds, (W, H))

    # ---- lettering and furniture, laid out first so the terrain symbols keep clear of them (#56)
    inner = ((MARGIN + BAND) * SS, (MARGIN + BAND) * SS, W - (MARGIN + BAND) * SS, H - (MARGIN + BAND) * SS)
    layout = frame.Labels((inner[0] + 6 * SS, inner[1] + 6 * SS, inner[2] - 6 * SS, inner[3] - 6 * SS), pad=3 * SS)
    deco = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    layout.reserve(frame.compass(deco, inner[2] - 90 * SS, inner[1] + 100 * SS, 62 * SS, _font(28 * SS, True)))
    layout.reserve(frame.cartouche(deco, inner[0] + 20 * SS, inner[3] - 24 * SS, st["atlas"]["name"],
                                   f"As known to {party['name']} \u00b7 day {st['day']}", _font(40 * SS, True),
                                   _font(20 * SS), SS))
    layout.reserve(frame.scale_bar(deco, inner[2] - 24 * SS, inner[3] - 24 * SS, proj.km_per_px(), W * 0.12,
                                   _font(16 * SS), SS))
    near = closeness(places, proj)
    marks = _lay_out_places(layout, proj, places, party, events=(st.get("events") or {}).get("features", []), near=near)
    n_place_labels = len(layout.placed)
    _lay_out_regions(layout, proj, known, provinces, realms_only=False, near=near)
    known_area = unary_union(seen + heard).buffer(0.25 * pad) if (seen or heard) else None
    _lay_out_waters(layout, proj, _features(slug, "waters"), known_area, ink=pal["water_ink"],
                    halo=pal["water_halo"], near=near)
    # realm names: their own layer, large and faint under the rest; they avoid each other, the furniture and the
    # province names, but may run under a town's name, as on old maps
    realm_layout = frame.Labels(layout.bounds, pad=6 * SS,
                                reserved=list(layout.reserved) + [l.box for l in layout.placed[n_place_labels:]])
    _lay_out_regions(realm_layout, proj, known, provinces, realms_only=True)

    # ---- the world as drawn by a cartographer (atlas/cartography.py)
    seed = carto.seed_of(slug)
    realm_tints = [(provinces[uid], _hex(p.get("color")) + (70,)) for uid, p in known.items()
                   if p["known"] == "visited" and p.get("controller") and uid in provinces]
    img, land, mask, cells, cell = draw_world(
        proj, W, H, slug, pal, seed, SS, provinces=provinces, realm_tints=realm_tints,
        avoid_boxes=[m["box"] for m in marks] + [lab.box for lab in layout.placed],
        density=min(1.0, max(0.45, near)))  # far out, thinner terrain too

    # ---- fog of war: soft-edged, over land and sea alike
    mask = Image.new("L", (W, H), FOG_UNKNOWN)
    mdraw = ImageDraw.Draw(mask)
    margin = 0.25 * pad
    if heard:
        _fill(mdraw, proj, unary_union(heard).buffer(margin * 0.6), FOG_HEARD, hole=FOG_HEARD)
    for f in places:
        if f["properties"]["known"] == "heard":
            x, y = proj(*f["geometry"]["coordinates"])
            r = 40 * SS
            mdraw.ellipse([x - r, y - r, x + r, y + r], fill=FOG_HEARD)
    clear = unary_union(seen).buffer(margin) if seen else None
    if clear is not None and not clear.is_empty:
        _fill(mdraw, proj, clear, 0, hole=0)
    for f in places:
        if f["properties"]["known"] == "visited":
            x, y = proj(*f["geometry"]["coordinates"])
            r = 90 * SS
            mdraw.ellipse([x - r, y - r, x + r, y + r], fill=0)
    mask = mask.filter(ImageFilter.GaussianBlur(18 * SS))
    fog = Image.new("RGBA", (W, H), FOG + (255,))
    fog.putalpha(mask)
    img.alpha_composite(fog)
    img = carto.parchment(img, seed)  # aged together: the unexplored is just more of the same old paper

    # ---- the places the party knows, as symbols (faded if only heard of), then every label
    for state, alpha in (("visited", 255), ("heard", 140)):
        layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        ld = ImageDraw.Draw(layer, "RGBA")
        for m in marks:
            if m["state"] != state:
                continue
            if m["emblem"]:
                emblems.draw(ld, m["emblem"], m["x"], m["y"], SS * m.get("scale", 1.0))
            else:
                r = m["half"]
                ld.ellipse([m["x"] - r, m["y"] - r, m["x"] + r, m["y"] + r], fill=emblems.INK if r < 4 * SS else emblems.PAPER,
                           outline=emblems.INK, width=SS)
            if m.get("port"):
                emblems.draw(ld, "anchor", m["x"] + m["half"] + 8 * SS * m.get("scale", 1.0), m["y"] + m["half"] * 0.4,
                             SS * m.get("scale", 1.0))
        if alpha < 255:
            layer.putalpha(layer.getchannel("A").point(lambda v: v * alpha // 255))
        img.alpha_composite(layer)
    draw = ImageDraw.Draw(img, "RGBA")
    for m in marks:
        if m.get("here"):
            R = m["half"] + 12 * SS
            draw.ellipse([m["x"] - R, m["y"] - R, m["x"] + R, m["y"] + R], outline=(176, 110, 30, 255), width=3 * SS)
    realm_layout.draw(img)
    layout.draw(img)
    frame.neatline(img, proj.inverse, MARGIN * SS, BAND * SS, SS)
    img.alpha_composite(deco)

    out = img.resize(size, Image.LANCZOS).convert("RGB")
    buf = io.BytesIO()
    out.save(buf, "WEBP", quality=88, method=4)
    meta = {"title": f"{party['name']}'s map, day {st['day']}", "party": party["name"], "day": st["day"],
            "branch": branch, "bounds": [round(v, 3) for v in bounds],
            "provinces": {"visited": len(seen), "heard": len(heard)},
            "places": {"visited": sum(f["properties"]["known"] == "visited" for f in places),
                       "heard": sum(f["properties"]["known"] == "heard" for f in places)},
            "where": party.get("where"), "style": style}
    return buf.getvalue(), meta


# --------------------------------------------------------------------------- the table
def _tag(store: GraphStore) -> str:
    return f"lwe-overland:{store.db}"


def to_table(store: GraphStore, branch: str = "main", day: int | None = None, style: str | None = None) -> dict[str, Any]:
    """Draw the party's map and show it on the table, replacing this world's older party maps in the VTT library."""
    from vtt_link import client
    data, meta = render(store, branch, day, style=style)
    tag = _tag(store)
    asset = client.upload_map(data, "party-map.webp", title=meta["title"],
                              tags=[tag, f"lwe-day:{meta['day']}", f"lwe-branch:{branch}", f"lwe-style:{meta['style']}"],
                              push=True)
    replaced = 0
    for m in client.maps():
        if m["id"] != asset["id"] and tag in (m.get("tags") or []):
            client.delete_map(m["id"])
            replaced += 1
    return {**meta, "vtt_map_id": asset["id"], "replaced": replaced}


def refresh_if_shown(store: GraphStore, branch: str = "main", day: int | None = None) -> bool:
    """If this world's party map is on the table now, redraw it (after the party learned something)."""
    from vtt_link import client
    if not client.configured():
        return False
    try:
        current = (client.table_state() or {}).get("map") or {}
        tags = current.get("tags") or []
        if _tag(store) not in tags:
            return False
        style = next((x.split(":", 1)[1] for x in tags if x.startswith("lwe-style:")), None)
        to_table(store, branch, day, style=style)  # redrawn in the style it was put up in
        return True
    except (client.VTTError, NothingKnown):
        return False

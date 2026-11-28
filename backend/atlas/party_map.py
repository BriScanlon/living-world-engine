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
from shapely.geometry import Point, box, shape
from shapely.ops import unary_union

from atlas import cartography as carto
from atlas.service import atlas_path, atlas_state
from graph.store import GraphStore

SIZE = (2560, 1440)  # 16:9 like the table; the VTT shows it whole ("contain")
SS = 2  # draw at twice the size, then shrink: cheap anti-aliasing for polygons and lines
SEA, SHALLOW, RIPPLE = (128, 164, 176), (176, 204, 204), (86, 122, 134)
LAND, BORDER, COAST, LANE = (233, 218, 180), (104, 74, 96), (78, 58, 38), (236, 244, 246)
RIVER, ROAD, INK = (79, 127, 166), (122, 90, 51), (40, 30, 20)
FOG = (16, 20, 28)
FOG_UNKNOWN, FOG_HEARD = 238, 120  # fog alpha over the unknown and the heard-of


def _cell_px(proj: "Projection", relief: list[dict[str, Any]], land) -> float | None:
    """The map's cell size in pixels, from how many relief points fall on the land drawn."""
    if not relief or land.is_empty:
        return None
    import numpy as np
    xy = proj.arr(np.array([f["geometry"]["coordinates"] for f in relief], dtype=float))
    minx, miny, maxx, maxy = land.bounds
    inside = int(((xy[:, 0] >= minx) & (xy[:, 0] <= maxx) & (xy[:, 1] >= miny) & (xy[:, 1] <= maxy)).sum())
    return math.sqrt(land.area / inside) if inside else None


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


def render(store: GraphStore, branch: str = "main", day: int | None = None,
           size: tuple[int, int] = SIZE) -> tuple[bytes, dict[str, Any]]:
    """The party's overland map as a WebP image, plus what it shows."""
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

    # ---- the world as drawn by a cartographer (atlas/cartography.py)
    seed = carto.seed_of(slug)
    view = box(-0.1 * W, -0.1 * H, 1.1 * W, 1.1 * H)
    wob = carto.Wobble(seed, amp=1.6 * SS, scale=40 * SS)

    lon0, lat0 = bounds[0], bounds[1]
    span = (bounds[2] - bounds[0] + bounds[3] - bounds[1]) * 0.1

    def px(geom):
        geom = shapely.clip_by_rect(geom, lon0 - span, lat0 - span, bounds[2] + span, bounds[3] + span)
        return carto.to_px(geom, proj.arr).intersection(view)

    land_feats = _features(slug, "land")
    raw_land = unary_union([px(shape(f["geometry"]).buffer(0)) for f in land_feats])
    relief = [f for f in _features(slug, "relief")]
    cell = _cell_px(proj, relief, raw_land) or 30.0 * SS
    if any((f.get("properties") or {}).get("detail") for f in land_feats):  # already drawn at import (#62)
        land = raw_land.simplify(0.5 * SS)
    else:  # an older import: cell outlines, smoothed here
        land = carto.smooth(raw_land, wob, cell)
    img = Image.new("RGBA", (W, H), SEA + (255,))
    img.alpha_composite(carto.shallows((W, H), land, 22 * SS, SHALLOW + (170,)))
    draw = ImageDraw.Draw(img, "RGBA")
    carto.ripples(draw, land, [9 * SS, 18 * SS, 29 * SS], RIPPLE + (150,), max(1, SS))
    carto.fill(draw, land, LAND + (255,), hole=SEA + (255,))
    mask = Image.new("L", (W, H), 0)
    carto.fill(ImageDraw.Draw(mask), land, 255, hole=0)
    paint = []
    for f in _features(slug, "biomes"):  # biomes, muted into the parchment
        c = _hex(f["properties"].get("color"))
        paint.append((px(shape(f["geometry"]).buffer(0)), tuple(int(0.65 * c[i] + 0.35 * LAND[i]) for i in range(3)) + (110,)))
    prov_px = {uid: px(g.buffer(0)) for uid, g in provinces.items()}
    tint = carto.wash((W, H), paint, cell * 0.45)
    realms = [(prov_px[uid], _hex(p.get("color")) + (70,)) for uid, p in known.items()
              if p["known"] == "visited" and p.get("controller") and uid in prov_px]
    if realms:  # who holds the provinces the party has seen: a soft wash of the realm's colour
        tint.alpha_composite(carto.wash((W, H), realms, cell * 0.8))
    base = img.copy()
    base.alpha_composite(tint)
    img = Image.composite(base, img, mask)  # colour stays inside the smoothed coast
    draw = ImageDraw.Draw(img, "RGBA")
    for pts in carto.borders(list(prov_px.values()), wob, cell, clip=land.buffer(-1.5 * SS)):
        _dash(draw, pts, BORDER + (150,), max(1, SS), int(1.5 * SS), gap=3 * SS)  # dotted, unlike roads
    for f in _features(slug, "rivers"):
        g = px(shape(f["geometry"]))
        for ln in ([g] if g.geom_type == "LineString" else list(getattr(g, "geoms", []))):
            if ln.geom_type != "LineString" or ln.length < 4 * SS:
                continue
            pts = carto.smooth_line(ln.coords, carto.Wobble(seed + 1, 1.5 * SS, 30 * SS), 4 * SS)
            carto.taper(draw, pts, 0.8 * SS, min(5.0, 1.6 + ln.length / (350 * SS)) * SS, RIVER + (255,))
    for f in _features(slug, "roads"):
        group = (f.get("properties") or {}).get("group")
        g = px(shape(f["geometry"]))
        for ln in ([g] if g.geom_type == "LineString" else list(getattr(g, "geoms", []))):
            if ln.geom_type != "LineString":
                continue
            pts = carto.smooth_line(ln.coords, carto.Wobble(seed + 2, 0, 1), 6 * SS)
            if group == "searoutes":  # sea lanes: sailed, drawn in pale sea blue
                _dash(draw, pts, LANE + (210,), 2 * SS, 5 * SS)
            else:
                _dash(draw, pts, ROAD + (225,), (3 if group == "highways" else 2) * SS, 9 * SS)
    carto.outline(draw, land, COAST + (255,), 2 * SS)  # the inked coast, lakes too
    img = carto.parchment(img, seed)

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

    # ---- what the party knows, by name
    draw = ImageDraw.Draw(img)
    for uid, p in known.items():
        g = provinces.get(uid)
        if g is None or not p.get("name"):
            continue
        x, y = proj(*g.representative_point().coords[0])
        if p["known"] == "visited":
            _label(draw, (x, y), " ".join(p["name"].upper()), 24 * SS, fill=(92, 66, 40), bold=True)
        else:
            _label(draw, (x, y), p["name"], 22 * SS, fill=(200, 190, 170), halo=(30, 34, 44))
    for group in _clusters([f for f in places if f["properties"].get("kind") != "continent"], proj, 40 * SS):
        head, rest = group[0]["properties"], [g["properties"] for g in group[1:]]
        x, y = proj(*group[0]["geometry"]["coordinates"])
        visited = any(p["known"] == "visited" for p in [head, *rest])
        r = (9 if head.get("kind") in ("capital", "city") else 7) * SS
        if visited:
            draw.ellipse([x - r, y - r, x + r, y + r], fill=(60, 40, 25), outline=(236, 226, 196), width=2 * SS)
        else:
            draw.ellipse([x - r, y - r, x + r, y + r], outline=(200, 190, 170), width=2 * SS)
        _label(draw, (x, y - r - 6 * SS), head["name"], (24 if visited else 20) * SS,
               fill=INK if visited else (210, 200, 180), halo=(236, 226, 196) if visited else (30, 34, 44))
        ly = y + r + 26 * SS
        for p in rest:  # buildings and districts in the same spot, listed under the town
            seen_it = p["known"] == "visited"
            _label(draw, (x, ly), p["name"] + ("" if seen_it else " (heard of)"), 18 * SS,
                   fill=(70, 52, 32) if seen_it else (210, 200, 180), halo=(236, 226, 196) if seen_it else (30, 34, 44))
            ly += 24 * SS
        if any(p.get("here") for p in [head, *rest]):
            R = 26 * SS
            draw.ellipse([x - R, y - R, x + R, y + R], outline=(245, 199, 126), width=4 * SS)
            where = next(p["name"] for p in [head, *rest] if p.get("here"))
            _label(draw, (x, ly + 8 * SS), f"{party['name']} is here" + (f" ({where})" if where != head["name"] else ""),
                   20 * SS, fill=(120, 70, 10))

    # ---- cartouche, compass, scale bar
    title = st["atlas"]["name"]
    sub = f"As known to {party['name']} · day {st['day']}"
    tw = max(draw.textlength(title, font=_font(40 * SS)), draw.textlength(sub, font=_font(22 * SS)))
    bx, by = 40 * SS, H - 150 * SS
    draw.rounded_rectangle([bx, by, bx + tw + 60 * SS, H - 40 * SS], radius=14 * SS, fill=(236, 226, 196, 235),
                           outline=(120, 90, 50), width=3 * SS)
    draw.text((bx + 30 * SS, by + 18 * SS), title, font=_font(40 * SS, True), fill=INK)
    draw.text((bx + 30 * SS, by + 70 * SS), sub, font=_font(22 * SS), fill=(90, 70, 45))
    cx, cy, cr = W - 110 * SS, 120 * SS, 50 * SS
    draw.polygon([(cx, cy - cr), (cx + cr * 0.32, cy), (cx, cy + cr * 0.25), (cx - cr * 0.32, cy)],
                 fill=(236, 226, 196), outline=(60, 45, 30), width=2 * SS)
    draw.polygon([(cx, cy - cr), (cx + cr * 0.32, cy), (cx, cy + cr * 0.25)], fill=(140, 40, 30))
    _label(draw, (cx, cy - cr - 10 * SS), "N", 30 * SS)
    km = proj.km_per_px() * W * 0.14
    nice = next((n for n in (1, 2, 5, 10, 20, 25, 50, 100, 200, 250, 500, 1000, 2000, 5000) if n >= km * 0.6), 5000)
    bar = nice / proj.km_per_px()
    x0, y0 = W - 60 * SS - bar, H - 70 * SS
    draw.rectangle([x0 - 16 * SS, y0 - 50 * SS, x0 + bar + 16 * SS, y0 + 24 * SS], fill=(236, 226, 196, 220))
    draw.rectangle([x0, y0, x0 + bar / 2, y0 + 8 * SS], fill=INK)
    draw.rectangle([x0 + bar / 2, y0, x0 + bar, y0 + 8 * SS], fill=(236, 226, 196), outline=INK, width=SS)
    _label(draw, (x0 + bar / 2, y0 - 12 * SS), f"{nice:g} km", 20 * SS)

    out = img.resize(size, Image.LANCZOS).convert("RGB")
    buf = io.BytesIO()
    out.save(buf, "WEBP", quality=88, method=4)
    meta = {"title": f"{party['name']}'s map, day {st['day']}", "party": party["name"], "day": st["day"],
            "branch": branch, "bounds": [round(v, 3) for v in bounds],
            "provinces": {"visited": len(seen), "heard": len(heard)},
            "places": {"visited": sum(f["properties"]["known"] == "visited" for f in places),
                       "heard": sum(f["properties"]["known"] == "heard" for f in places)},
            "where": party.get("where")}
    return buf.getvalue(), meta


# --------------------------------------------------------------------------- the table
def _tag(store: GraphStore) -> str:
    return f"lwe-overland:{store.db}"


def to_table(store: GraphStore, branch: str = "main", day: int | None = None) -> dict[str, Any]:
    """Draw the party's map and show it on the table, replacing this world's older party maps in the VTT library."""
    from vtt_link import client
    data, meta = render(store, branch, day)
    tag = _tag(store)
    asset = client.upload_map(data, "party-map.webp", title=meta["title"],
                              tags=[tag, f"lwe-day:{meta['day']}", f"lwe-branch:{branch}"], push=True)
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
        if _tag(store) not in (current.get("tags") or []):
            return False
        to_table(store, branch, day)
        return True
    except (client.VTTError, NothingKnown):
        return False

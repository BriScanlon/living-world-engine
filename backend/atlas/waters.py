"""Named waters (epic #58, phase 4a #57): the open sea, its gulfs, bays and straits, and lakes, found from the coast
and named from the land around them, as a cartographer would letter them.

In a km frame, the sea is the map's box minus the land. A morphological opening (shrink, then grow back, by a
radius ``R`` that scales with the map) keeps the **open sea** and drops the water that only reaches in narrowly: each
dropped piece big enough is a **bay**, a **gulf** if it's large, or a **strait** if it joins the open sea in two
places. Holes in the land are **lakes**. Names come from the nearest town (bays, straits, lakes), the province around
(gulfs), or the map itself (the largest open sea). Each water gets a label point well inside it (``polylabel``).
"""
from __future__ import annotations

import math
from typing import Any

import shapely
from shapely.geometry import Point, Polygon, box, mapping
from shapely.ops import polylabel, unary_union

from atlas.cartography import KmFrame, polys


def _first_word(name: str) -> str:
    words = [w for w in name.replace("-", " ").split() if w.lower() not in ("the", "of", "province", "march", "shire",
                                                                              "county", "barony", "duchy", "reach")]
    return words[0] if words else name


def find_waters(land_ll, bbox: list[float], *, towns: list[tuple[str, float, float, bool]],
                provinces: list[tuple[str, Any]], map_name: str) -> list[dict[str, Any]]:
    """Named waters as GeoJSON features: the water's (simplified) shape, with ``name``, ``kind`` (sea, gulf, bay,
    strait, lake), ``label`` (a point well inside it) and ``width_km`` (how much room the name has). ``towns`` are (name, lon, lat, is_capital); ``provinces``
    are (name, lon/lat geometry)."""
    if land_ll.is_empty:
        return []
    lon0, lat0, lon1, lat1 = bbox
    km = KmFrame((lat0 + lat1) / 2)
    frame_km = km.to_km(box(lon0, lat0, lon1, lat1))
    land = km.to_km(land_ll)
    filled = unary_union([Polygon(p.exterior) for p in polys(land)])
    sea = frame_km.difference(filled)
    short = min(frame_km.bounds[2] - frame_km.bounds[0], frame_km.bounds[3] - frame_km.bounds[1])
    R = 0.04 * short
    town_pts = [(n, km.to_km(Point(x, y)), cap) for n, x, y, cap in towns]
    prov_km = [(n, km.to_km(g)) for n, g in provinces]

    def nearest_town(g, prefer_capital: bool = False) -> str | None:
        if not town_pts:
            return None
        ranked = sorted(town_pts, key=lambda t: t[1].distance(g) * (0.6 if prefer_capital and t[2] else 1.0))
        return ranked[0][0]

    def province_of(g) -> str | None:
        if not prov_km:
            return None
        return min(prov_km, key=lambda p: p[1].distance(g))[0]

    out: list[tuple[str, str, Any]] = []
    open_sea = sea.buffer(-R, quad_segs=4).buffer(R, quad_segs=4)
    open_parts = sorted(polys(open_sea), key=lambda p: -p.area)
    for i, part in enumerate(open_parts):
        if part.area < 20 * R * R:
            continue
        if i == 0:
            name = f"The {_first_word(map_name)} Sea"
        else:
            name = f"The Sea of {_first_word(province_of(part) or map_name)}"
        out.append(("sea", name, part))
    inlets = sea.difference(open_sea.buffer(R * 0.05))
    for piece in sorted(polys(inlets), key=lambda p: -p.area):
        if piece.area < 1.5 * R * R:
            continue
        touching = [p for p in polys(piece.buffer(R * 0.15).intersection(open_sea)) if p.area > R * R * 0.01]
        if len(touching) >= 2:
            kind, name = "strait", f"The {nearest_town(piece) or 'Narrow'} Strait"
        elif piece.area > 6 * R * R:
            kind, name = "gulf", f"The Gulf of {_first_word(province_of(piece) or map_name)}"
        else:
            kind, name = "bay", f"The Bay of {nearest_town(piece, prefer_capital=True) or 'Storms'}"
        out.append((kind, name, piece))
    for p in polys(land):
        for hole in p.interiors:
            lake = Polygon(hole)
            if lake.area >= R * R * 0.15:
                out.append(("lake", f"Lake {nearest_town(lake) or 'Mere'}", lake))
    # one name each: a repeat takes its province instead (or a compass word)
    seen: dict[str, int] = {}
    feats = []
    for kind, name, g in out:
        if name in seen:
            alt = _first_word(province_of(g) or "")
            name = name.replace(nearest_town(g) or "\0", alt) if alt and alt not in name else f"{name} ({seen[name] + 1})"
        seen[name] = seen.get(name, 0) + 1
        try:
            at = polylabel(g, tolerance=max(1.0, R * 0.05))
        except Exception:  # noqa: BLE001 - odd shapes: any point inside will do
            at = g.representative_point()
        width = min(g.bounds[2] - g.bounds[0], 3 * at.distance(g.exterior) if g.geom_type == "Polygon" else 1e9)
        shape_ll = km.to_ll(g.simplify(R * 0.1))
        lab = km.to_ll(at)
        feats.append({"type": "Feature", "geometry": mapping(shapely.set_precision(shape_ll, 1e-4)),
                      "properties": {"name": name, "kind": kind, "width_km": round(width, 1), "area_km2": round(g.area),
                                     "label": [round(lab.x, 5), round(lab.y, 5)]}})
    return feats

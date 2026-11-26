"""Travel times on the campaign planet: how long it takes people, armies and news to get somewhere.

Distances are great-circle kilometres between places' lon/lat (a building shares its town's position). A route may
follow the atlas's roads: walk off-road to the nearest road vertex, along the road network, then off-road to the
destination, or go straight across country, whichever is quicker. Off-road travel counts as difficult terrain
(half pace) over a path 25% longer than the straight line.

Paces follow the SRD's overland travel (normal pace 24 miles a day on foot, fast 30): an army marches at half that,
and news travels with merchants and riders. Stretches over water go by ship at a sailing ship's 2 mph, around the
clock (48 miles a day), whoever is travelling.
"""
from __future__ import annotations

import heapq
import json
import math
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

from graph.world import WorldView

EARTH_KM = 6371.0
MILE_KM = 1.609344
# km per day on a road; off-road is half pace (difficult terrain)
PACE_KM_PER_DAY = {"foot": 24 * MILE_KM, "horse": 30 * MILE_KM, "army": 12 * MILE_KM, "rumor": 30 * MILE_KM}
SHIP_KM_PER_DAY = 48 * MILE_KM
OFFROAD_SLOWDOWN = 2.0
OFFROAD_WINDING = 1.25
SNAP_KM = 5.0  # road ends this close together are one junction


def haversine_km(a: tuple[float, float], b: tuple[float, float]) -> float:
    (lon1, lat1), (lon2, lat2) = a, b
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_KM * math.asin(min(1.0, math.sqrt(h)))


@dataclass
class RoadNet:
    nodes: list[tuple[float, float]] = field(default_factory=list)
    edges: dict[int, list[tuple[int, float]]] = field(default_factory=dict)

    def nearest(self, p: tuple[float, float]) -> tuple[int, float] | None:
        if not self.nodes:
            return None
        i = min(range(len(self.nodes)), key=lambda k: haversine_km(p, self.nodes[k]))
        return i, haversine_km(p, self.nodes[i])

    def shortest(self, src: int, dst: int) -> tuple[float, list[int]] | None:
        dist, prev, seen = {src: 0.0}, {}, set()
        heap = [(0.0, src)]
        while heap:
            d, u = heapq.heappop(heap)
            if u in seen:
                continue
            seen.add(u)
            if u == dst:
                path = [u]
                while path[-1] in prev:
                    path.append(prev[path[-1]])
                return d, path[::-1]
            for v, w in self.edges.get(u, []):
                nd = d + w
                if nd < dist.get(v, math.inf):
                    dist[v], prev[v] = nd, u
                    heapq.heappush(heap, (nd, v))
        return None


@lru_cache(maxsize=8)
def _roads(path: str, mtime: float) -> RoadNet:  # mtime: reload when the atlas is re-imported
    net = RoadNet()
    index: dict[tuple[float, float], int] = {}

    def node(c: list[float]) -> int:
        p = (round(c[0], 4), round(c[1], 4))
        if p not in index:
            for q, i in index.items():  # snap to a junction already there
                if haversine_km(p, q) < SNAP_KM:
                    index[p] = i
                    return i
            index[p] = len(net.nodes)
            net.nodes.append(p)
        return index[p]

    for f in json.loads(Path(path).read_text(encoding="utf-8")).get("features", []):
        g = f.get("geometry") or {}
        lines = [g["coordinates"]] if g.get("type") == "LineString" else g.get("coordinates", []) if g.get("type") == "MultiLineString" else []
        for line in lines:
            ids = [node(c) for c in line]
            for a, b in zip(ids, ids[1:]):
                if a == b:
                    continue
                w = haversine_km(net.nodes[a], net.nodes[b])
                net.edges.setdefault(a, []).append((b, w))
                net.edges.setdefault(b, []).append((a, w))
    return net


def road_net(atlas_dir: Path | None) -> RoadNet:
    p = atlas_dir / "roads.geojson" if atlas_dir else None
    if not p or not p.exists():
        return RoadNet()
    return _roads(str(p), p.stat().st_mtime)


@lru_cache(maxsize=8)
def _land(path: str, mtime: float):
    from shapely.geometry import shape
    from shapely.ops import unary_union
    from shapely.prepared import prep
    feats = json.loads(Path(path).read_text(encoding="utf-8")).get("features", [])
    geoms = [shape(f["geometry"]) for f in feats if f.get("geometry")]
    return prep(unary_union(geoms)) if geoms else None


def land(atlas_dir: Path | None):
    p = atlas_dir / "land.geojson" if atlas_dir else None
    if not p or not p.exists():
        return None
    return _land(str(p), p.stat().st_mtime)


def sea_fraction(a: tuple[float, float], b: tuple[float, float], land_geom, samples: int = 48) -> float:
    """How much of the straight line from a to b is over water (by sampling points along it)."""
    if land_geom is None:
        return 0.0
    from shapely.geometry import Point
    dlon = b[0] - a[0]
    if abs(dlon) > 180:  # the short way round crosses the antimeridian
        dlon -= math.copysign(360, dlon)

    def at(t: float) -> Point:
        lon = (a[0] + dlon * t + 180) % 360 - 180
        return Point(lon, a[1] + (b[1] - a[1]) * t)

    wet = sum(not land_geom.contains(at(t / samples)) for t in range(1, samples))
    return wet / max(1, samples - 1)


@dataclass
class Route:
    km: float  # as travelled
    road_km: float  # of which on roads
    straight_km: float
    path: list[tuple[float, float]]
    sea_km: float = 0.0  # of which by ship

    def days(self, pace: str = "foot") -> float:
        v = PACE_KM_PER_DAY[pace]
        overland = self.km - self.road_km - self.sea_km
        return self.road_km / v + overland * OFFROAD_SLOWDOWN / v + self.sea_km / SHIP_KM_PER_DAY

    def summary(self) -> dict[str, Any]:
        return {"km": round(self.km), "road_km": round(self.road_km), "sea_km": round(self.sea_km),
                "straight_km": round(self.straight_km),
                "days": {p: round(self.days(p), 1) for p in PACE_KM_PER_DAY},
                "path": [[round(x, 4), round(y, 4)] for x, y in self.path]}


def route(a: tuple[float, float], b: tuple[float, float], net: RoadNet | None = None, land_geom=None) -> Route:
    """The quicker of straight across country (taking ship over water) or via the roads (judged at foot pace)."""
    straight = haversine_km(a, b)
    sea = straight * sea_fraction(a, b, land_geom) if straight > 1 else 0.0
    best = Route((straight - sea) * OFFROAD_WINDING + sea, 0.0, straight, [a, b], sea_km=sea)
    if net and net.nodes and straight > 1:
        na, nb = net.nearest(a), net.nearest(b)
        if na and nb:
            found = net.shortest(na[0], nb[0])
            if found:
                road_km, ids = found
                off = (na[1] + nb[1]) * OFFROAD_WINDING
                via = Route(road_km + off, road_km, straight, [a, *[net.nodes[i] for i in ids], b])
                if via.days() < best.days():
                    best = via
    return best


# --------------------------------------------------------------------------- places and people
def coords(w: WorldView, doc_id: str | None, depth: int = 0) -> tuple[float, float] | None:
    """Where something is: its own lon/lat, else its parent's, location's or province's."""
    d = w.all.get(doc_id) if doc_id else None
    if not d or depth > 5:
        return None
    if d.get("lon") is not None and d.get("lat") is not None:
        return d["lon"], d["lat"]
    for k in ("parent", "location", "headquarters", "province"):
        c = coords(w, d.get(k), depth + 1)
        if c:
            return c
    if d.get("@type") == "Faction":  # no seat recorded: where it holds land, else where its people are
        for e in w.of_type("ControlEdge"):
            if e["from_id"] == d["@id"]:
                c = coords(w, e["target_id"], depth + 1)
                if c:
                    return c
        for c_ in w.members_of(d["@id"]):
            c = coords(w, c_["@id"], depth + 1)
            if c:
                return c
    return None


class Travel:
    """Routes between things in one world view, with the atlas's roads."""

    def __init__(self, w: WorldView, atlas_dir: Path | None = None):
        self.w = w
        self.net = road_net(atlas_dir) if atlas_dir else RoadNet()
        self.land = land(atlas_dir)

    def settlement(self, doc_id: str | None) -> str | None:
        """The outermost place containing this one (a tavern -> its town). Within one, everything is close by:
        buildings are spread a little on the atlas only so their markers don't overlap."""
        d = self.w.get(doc_id) if doc_id else None
        if d and d.get("@type") != "Location":
            d = self.w.get(d.get("location") or d.get("headquarters"))
        seen = set()
        while d and d.get("parent") and d["parent"] not in seen and self.w.get(d["parent"]):
            seen.add(d["parent"])
            d = self.w.get(d["parent"])
        return d["@id"] if d and d.get("@type") == "Location" else None

    def between(self, a_id: str | None, b_id: str | None) -> Route | None:
        a, b = coords(self.w, a_id), coords(self.w, b_id)
        if not a or not b:
            return None
        sa = self.settlement(a_id)
        if sa and sa == self.settlement(b_id):
            return Route(0.0, 0.0, 0.0, [a, b])  # across town: no journey
        return route(a, b, self.net, self.land)

    def days(self, a_id: str | None, b_id: str | None, pace: str) -> int:
        """Whole days to get from a to b (0 if the same place or unknown)."""
        r = self.between(a_id, b_id)
        return math.ceil(r.days(pace) - 1e-9) if r and r.km > 0.5 else 0


def for_world(w: WorldView) -> Travel:
    """A Travel for this world, using the most recently imported atlas's roads."""
    from atlas.service import atlas_path
    atlases = [d for d in w.all.values() if d["@type"] == "WorldAtlas"]
    if not atlases:
        return Travel(w)
    a = max(atlases, key=lambda d: d.get("imported_at") or "")
    return Travel(w, atlas_path(a["uid"]))

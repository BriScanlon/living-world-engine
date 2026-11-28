"""Roads, trails and sea lanes (phase 5, #43), in the shape ``atlas/travel.py`` reads (``pack.routes``).

* **Road network:** candidate links join neighbouring towns on the same landmass (a Delaunay graph of the towns, up
  to ``max_link_px`` long). Each link's road is a least-cost path over the cells: easy on plains, dear over high
  ground and at river crossings, so roads keep to valleys and passes. A minimum spanning tree of the links (per
  landmass) becomes the **roads**; links that would save a long detour on it become **trails**. Cells already on a
  road cost less, so later roads merge into trunks. Neighbouring capitals are joined by **highways**.
* **Sea lanes** (group ``searoutes``) link ports across water: every landmass with a port joins the others through a
  spanning tree of the shortest crossings, plus short hops between nearby coasts. Each lane runs over water cells
  from port to port; ``atlas/travel.py`` sails them at ship speed. A landmass with towns but no harbour still gets a
  landing at its best coastal town.
"""
from __future__ import annotations

import heapq
import math

import numpy as np
import shapely
from shapely.geometry import MultiPoint

SEA_LEVEL = 20
BIOME_COST = {1: 6, 2: 5, 3: 2, 4: 1, 5: 2, 6: 2, 7: 4, 8: 3, 9: 4, 10: 6, 11: 12, 12: 5}
ROAD_DISCOUNT = 0.4  # a cell already on a road costs this much of its usual price


def _links(xy: np.ndarray, pts: list[int], max_len: float) -> list[tuple[int, int]]:
    """Delaunay neighbours among the cells ``pts`` (by position), no longer than ``max_len``."""
    if len(pts) < 2:
        return []
    if len(pts) == 2:
        a, b = pts
        return [(a, b)] if math.dist(xy[a], xy[b]) <= max_len else []
    where = {(round(float(xy[c][0]), 3), round(float(xy[c][1]), 3)): c for c in pts}
    edges = shapely.get_parts(shapely.delaunay_triangles(MultiPoint([xy[c] for c in pts]), only_edges=True))
    out = set()
    for e in edges:
        (x1, y1), (x2, y2) = e.coords
        a, b = where.get((round(x1, 3), round(y1, 3))), where.get((round(x2, 3), round(y2, 3)))
        if a is not None and b is not None and a != b and math.dist(xy[a], xy[b]) <= max_len:
            out.add((min(a, b), max(a, b)))
    return sorted(out)


def _path(src: int, dst: int, nbrs: list[list[int]], step, xy: np.ndarray, reach: float) -> tuple[float, list[int]]:
    """Least-cost path src -> dst (Dijkstra), staying within ``reach`` of the two ends. (inf, []) if none."""
    mid = (xy[src] + xy[dst]) / 2
    radius = math.dist(xy[src], xy[dst]) / 2 + reach
    dist, prev = {src: 0.0}, {}
    pq = [(0.0, src)]
    while pq:
        d, u = heapq.heappop(pq)
        if u == dst:
            out = [u]
            while out[-1] != src:
                out.append(prev[out[-1]])
            return d, out[::-1]
        if d > dist[u]:
            continue
        for v in nbrs[u]:
            c = step(u, v)
            if c is None or math.dist(xy[v], mid) > radius:
                continue
            nd = d + c
            if nd < dist.get(v, math.inf):
                dist[v], prev[v] = nd, u
                heapq.heappush(pq, (nd, v))
    return math.inf, []


def plan(xy: np.ndarray, nbrs: list[list[int]], h: list[int], biome: list[int], feature: list[int],
         flux: list[float], towns: list[int], capital_pairs: list[tuple[int, int]],
         ports: list[int], *, max_link_px: float, max_sea_px: float, extra_share: float = 0.3) -> list[dict]:
    """Routes as ``{"group": ..., "cells": [...]}``, highways first, then roads, trails and sea lanes."""
    land = [x >= SEA_LEVEL for x in h]
    f = np.asarray(flux, dtype=float)
    river = f >= (float(np.quantile(f[np.asarray(land)], 0.9)) if any(land) else math.inf)
    on_road: set[int] = set()

    def land_step(u: int, v: int) -> float | None:
        if not land[v]:
            return None
        c = BIOME_COST.get(biome[v], 3) + max(0.0, (h[v] - 50) / 4) + (2.5 if river[v] and not river[u] else 0.0)
        return c * (ROAD_DISCOUNT if v in on_road else 1.0)

    routes: list[dict] = []

    def lay(group: str, cells: list[int]) -> None:
        if len(cells) > 1:
            routes.append({"group": group, "cells": cells})
            on_road.update(cells)

    reach = max_link_px * 0.6
    # highways between neighbouring capitals (overland)
    for a, b in capital_pairs:
        cost, cells = _path(a, b, nbrs, land_step, xy, max_link_px * 2)
        lay("highways", cells)
    # roads: a spanning tree per landmass over the town links, cheapest first
    links = [(a, b) for a, b in _links(xy, towns, max_link_px) if feature[a] == feature[b]]
    priced = sorted(((_path(a, b, nbrs, land_step, xy, reach)[0], a, b) for a, b in links), key=lambda t: t[0])
    parent = {t: t for t in towns}

    def root(t: int) -> int:
        while parent[t] != t:
            parent[t] = parent[parent[t]]
            t = parent[t]
        return t

    spare = []
    for cost, a, b in priced:
        if cost == math.inf:
            continue
        ra, rb = root(a), root(b)
        if ra != rb:
            parent[ra] = rb
            lay("roads", _path(a, b, nbrs, land_step, xy, reach)[1])
        else:
            spare.append((cost, a, b))
    # one network per landmass: where a ridge (or ice) cut every short link, join the pieces around it
    by_mass: dict[int, list[int]] = {}
    for t in towns:
        by_mass.setdefault(feature[t], []).append(t)
    for members in by_mass.values():
        while True:
            comps: dict[int, list[int]] = {}
            for t in members:
                comps.setdefault(root(t), []).append(t)
            if len(comps) < 2:
                break
            main = max(comps.values(), key=len)
            rest = [t for t in members if root(t) != root(main[0])]
            a, b = min(((x, y) for x in main for y in rest), key=lambda p: math.dist(xy[p[0]], xy[p[1]]))
            cost, cells = _path(a, b, nbrs, land_step, xy, max_link_px * 4)
            if cost == math.inf:
                cost, cells = _path(a, b, nbrs, land_step, xy, math.inf)
            if cost == math.inf:
                break  # truly cut off (no land path): sea lanes will have to do
            parent[root(b)] = root(a)
            lay("roads", cells)
    # trails: links that save a long detour on the network built so far
    graph: dict[int, list[tuple[int, float]]] = {}
    for r in routes:
        cs = r["cells"]
        for u, v in zip(cs, cs[1:]):
            w = math.dist(xy[u], xy[v])
            graph.setdefault(u, []).append((v, w))
            graph.setdefault(v, []).append((u, w))

    def network_km(a: int, b: int) -> float:
        dist = {a: 0.0}
        pq = [(0.0, a)]
        while pq:
            d, u = heapq.heappop(pq)
            if u == b:
                return d
            if d > dist[u]:
                continue
            for v, w in graph.get(u, []):
                if d + w < dist.get(v, math.inf):
                    dist[v] = d + w
                    heapq.heappush(pq, (d + w, v))
        return math.inf

    budget = int(extra_share * sum(1 for r in routes if r["group"] == "roads"))
    detours = sorted(((network_km(a, b) / max(math.dist(xy[a], xy[b]), 1e-9), a, b) for _, a, b in spare), reverse=True)
    for ratio, a, b in detours[:budget]:
        if ratio > 1.8:
            cells = _path(a, b, nbrs, land_step, xy, reach)[1]
            lay("trails", cells)
            for u, v in zip(cells, cells[1:]):
                w = math.dist(xy[u], xy[v])
                graph.setdefault(u, []).append((v, w))
                graph.setdefault(v, []).append((u, w))
    # sea lanes: a landing on every landmass with towns, then a spanning tree of crossings plus short hops
    ports = list(dict.fromkeys(ports))
    has_port = {feature[p] for p in ports}
    for t in towns:
        if feature[t] not in has_port and any(not land[j] for j in nbrs[t]):
            ports.append(t)
            has_port.add(feature[t])

    def sea_step(u: int, v: int) -> float | None:
        return 1.0 if not land[v] else None

    def lane(a: int, b: int) -> tuple[float, list[int]]:
        wa = [j for j in nbrs[a] if not land[j]]
        wb = set(j for j in nbrs[b] if not land[j])
        best: tuple[float, list[int]] = (math.inf, [])
        for s in wa[:2]:
            for t in list(wb)[:2]:
                cost, cells = _path(s, t, nbrs, sea_step, xy, max_sea_px)
                if cost < best[0]:
                    best = (cost, [a, *cells, b])
        return best

    crossings = []
    for a, b in _links(xy, ports, max_sea_px):
        if feature[a] != feature[b]:
            km = math.dist(xy[a], xy[b])
            crossings.append((km, a, b))
    crossings.sort()
    mass_parent = {m: m for m in has_port}

    def mroot(m: int) -> int:
        while mass_parent[m] != m:
            mass_parent[m] = mass_parent[mass_parent[m]]
            m = mass_parent[m]
        return m

    hops = 0
    for km, a, b in crossings:
        ra, rb = mroot(feature[a]), mroot(feature[b])
        joins = ra != rb
        if joins or (km <= max_sea_px * 0.5 and hops < max(3, len(has_port))):
            cost, cells = lane(a, b)
            if cost < math.inf:
                routes.append({"group": "searoutes", "cells": cells})
                if joins:
                    mass_parent[ra] = rb
                else:
                    hops += 1
    return routes

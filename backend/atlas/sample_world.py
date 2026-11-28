"""Deterministic sample planet written in Azgaar's *Full JSON* export format.

Azgaar's Fantasy Map Generator (https://github.com/Azgaar/Fantasy-Map-Generator, MIT) is the
intended source of worlds. Its repository only ships `.map` saves, which do not store cell
polygons, so for tests, mock mode and first-run demos we generate a small world here that uses
exactly the fields the importer reads from a real "Export > Export to JSON > Full" file:

    info{width,height,seed,mapName}, mapCoordinates{latN,latT,lonW,lonT,...},
    pack{cells:[{i,v,c,p,h,biome,state,province,culture,religion,burg,pop}],
         vertices:[{i,p,c}], biomes[{i,name,color}], states[], provinces[], burgs[],
         cultures[], religions[], routes[{i,group,points[[x,y,cell]]}], rivers[{i,name,cells}]}

Cells are a hex grid instead of Azgaar's Voronoi mesh; the importer doesn't care.
"""
from __future__ import annotations

import heapq
import json
import math
import random
from collections import deque
from pathlib import Path
from typing import Any

BIOMES = [
    (0, "Marine", "#466eab"), (1, "Hot desert", "#fbe79f"), (2, "Cold desert", "#b5b887"),
    (3, "Savanna", "#d2d082"), (4, "Grassland", "#c8d68f"), (5, "Tropical seasonal forest", "#b6d95d"),
    (6, "Temperate deciduous forest", "#29bc56"), (7, "Tropical rainforest", "#7dcb35"),
    (8, "Temperate rainforest", "#409c43"), (9, "Taiga", "#4b6b32"), (10, "Tundra", "#96784b"),
    (11, "Glacier", "#d5e7eb"), (12, "Wetland", "#0b9131"),
]
BIOME_COST = {1: 6, 2: 5, 3: 2, 4: 1, 5: 2, 6: 2, 7: 4, 8: 3, 9: 4, 10: 6, 11: 12, 12: 5}
STATE_COLORS = ["#c45a4e", "#4e79a7", "#e0a33a", "#6f9e4c", "#9c6ab8", "#3fa7a3", "#d47f9b",
                "#8a6a4a", "#5b7f3a", "#a85d2c", "#3f5fa0", "#b0913a"]
FORMS = [("Kingdom", "Kingdom of {}"), ("Duchy", "Duchy of {}"), ("Republic", "Republic of {}"),
         ("Theocracy", "Holy Realm of {}"), ("Empire", "{} Empire"), ("Principality", "Principality of {}")]
SYLLABLES = [
    ["al", "dor", "mar", "eth", "vin", "gal", "or", "ath", "ren", "tor"],
    ["ka", "zu", "ri", "ma", "to", "na", "sha", "ki", "ro", "ya"],
    ["brin", "hal", "wick", "stead", "mere", "ford", "holm", "by", "thorp", "wyn"],
    ["vel", "sar", "ion", "dra", "kel", "ith", "mor", "an", "eli", "quen"],
    ["grom", "bar", "dun", "krag", "ruk", "tor", "gar", "zul", "mok", "har"],
]
CULTURE_NAMES = ["Aldmeri", "Kazuri", "Brinmark", "Velsari", "Grombar"]
RELIGIONS = [("Old Tide", "Folk"), ("Church of the Lantern", "Organized"), ("Ashen Path", "Cult"),
             ("Sky Father", "Organized")]


def _name(rng: random.Random, culture: int, parts: int | None = None) -> str:
    syl = SYLLABLES[culture % len(SYLLABLES)]
    n = parts or rng.choice([2, 2, 3])
    return "".join(rng.choice(syl) for _ in range(n)).capitalize()


# Land shapes (cf. Azgaar's heightmap templates): blob count, blob radius (fraction of width), sea share
LAND_SHAPES = {
    "continents": (6, (0.10, 0.22), 0.60),
    "pangea": (3, (0.20, 0.32), 0.55),
    "archipelago": (14, (0.04, 0.09), 0.72),
    "islands": (9, (0.06, 0.12), 0.68),
    "old-world": (8, (0.08, 0.18), 0.58),
}
# Climate bands (cf. Azgaar's Configure World presets) -> map coordinates
CLIMATES = {
    "whole-world": {"latT": 140, "latN": 70, "latS": -70},
    "northern": {"latT": 60, "latN": 70, "latS": 10},
    "temperate": {"latT": 40, "latN": 60, "latS": 20},
    "tropical": {"latT": 50, "latN": 25, "latS": -25},
    "southern": {"latT": 60, "latN": -10, "latS": -70},
}
# Region mode: where a generated campaign region sits, by climate answer (centre latitude, degrees)
REGION_LATITUDE = {"whole-world": 40.0, "northern": 58.0, "temperate": 45.0, "tropical": 12.0, "southern": -42.0}
KM_PER_DEG_LAT = 111.2
PROVINCE_KM2 = 22_000   # target province area in a region (10-40k km2)
TOWN_SPACING_KM = 45    # minimum distance between towns: about a day or two's walk
HOSTILITY_POOLS = {
    "peaceful": (["Friendly", "Ally", "Neutral", "Suspicion", "Friendly", "Rival"], ["Neutral", "Friendly", "Unknown"]),
    "uneasy": (["Rival", "Rival", "Enemy", "Suspicion", "Friendly", "Ally"], ["Neutral", "Neutral", "Unknown", "Friendly", "Suspicion"]),
    "warring": (["Enemy", "Enemy", "Rival", "Rival", "Suspicion", "Ally"], ["Suspicion", "Rival", "Neutral", "Unknown"]),
}


def generate(seed: int = 1795, *, width: int = 1500, height: int = 700, radius: float = 12.0,
             n_states: int = 9, name: str = "Thessaly Reach", land: str = "continents",
             climate: str = "whole-world", n_cultures: int = 5, n_religions: int = 4,
             hostility: str = "uneasy", culture_names: list[str] | None = None,
             religion_names: list[tuple[str, str]] | None = None,
             region_km: tuple[float, float] | None = None, cell_km: float = 20.0,
             max_towns: int = 200, mesh: str = "hex") -> dict[str, Any]:
    """A world in Azgaar Full JSON shape.

    Planet mode (default): the hex grid spans most of an Earth-sized planet (the demo and the tests).
    Region mode (``region_km=(width, height)``): a campaign region of that size in km, ``cell_km`` per cell, placed at
    a latitude that fits ``climate``; provinces about ``PROVINCE_KM2``, towns at least ``TOWN_SPACING_KM`` apart (up
    to ``max_towns``), so journeys take days to weeks instead of months to years.

    ``mesh="voronoi"`` (the wizard's regions) builds cells and terrain with ``atlas.worldgen`` (Voronoi mesh, fBm
    heightmap with mountain chains, cleaned coasts) instead of the hex grid and blob heightmap."""
    rng = random.Random(seed)
    region = region_km is not None
    km_px = 0.0
    if region:
        wkm, hkm = region_km
        height = int(round(width * hkm / wkm))
        km_px = wkm / width
        radius = cell_km / (math.sqrt(3) * km_px)
    n_blobs, blob_r, sea_share = LAND_SHAPES.get(land, LAND_SHAPES["continents"])
    culture_list = (culture_names or CULTURE_NAMES)[:max(1, n_cultures)]
    while len(culture_list) < n_cultures:
        culture_list = culture_list + [_name(rng, len(culture_list), 2) + "i"]
    religion_list = (religion_names or RELIGIONS)[:max(1, n_religions)]
    while len(religion_list) < n_religions:
        religion_list = religion_list + [(f"Cult of {_name(rng, len(religion_list), 2)}", "Cult")]

    if region:
        lat0 = REGION_LATITUDE.get(climate, REGION_LATITUDE["whole-world"])
        lat_t = hkm / KM_PER_DEG_LAT
        lon_t = wkm / (KM_PER_DEG_LAT * math.cos(math.radians(lat0)))
        coords = {"latT": round(lat_t, 4), "latN": round(lat0 + lat_t / 2, 4), "latS": round(lat0 - lat_t / 2, 4),
                  "lonT": round(lon_t, 4), "lonW": round(-lon_t / 2, 4), "lonE": round(lon_t / 2, 4)}
    else:
        coords = {**CLIMATES.get(climate, CLIMATES["whole-world"]), "lonT": 300, "lonW": -150, "lonE": 150}

    if mesh == "voronoi":
        import numpy as np

        from atlas.worldgen import mesh as wg_mesh
        from atlas.worldgen import terrain as wg_terrain
        nrng = np.random.default_rng(seed)
        spacing = cell_km / km_px if region else math.sqrt(3) * radius
        m = wg_mesh.voronoi(width, height, spacing, nrng)
        centers, cell_v, vpos, vcells, nbrs = m.centers, m.cell_v, m.vpos, m.vcells, m.nbrs
        n = len(centers)
    else:
        # ------------------------------------------------------------------ hex mesh
        w = math.sqrt(3) * radius
        rows = int((height - radius) // (1.5 * radius))
        cols = int((width - w / 2) // w)
        centers: list[tuple[float, float]] = []
        for r in range(rows):
            for c in range(cols):
                centers.append((round(c * w + (r % 2) * w / 2 + w / 2, 2), round(r * 1.5 * radius + radius, 2)))
        vkey: dict[tuple[float, float], int] = {}
        vpos: list[list[float]] = []
        vcells: list[list[int]] = []
        cell_v: list[list[int]] = []
        for i, (cx, cy) in enumerate(centers):
            ring = []
            for k in range(6):
                a = math.radians(-90 + 60 * k)
                pt = (round(cx + radius * math.cos(a), 1), round(cy + radius * math.sin(a), 1))
                if pt not in vkey:
                    vkey[pt] = len(vpos)
                    vpos.append([pt[0], pt[1]])
                    vcells.append([])
                vid = vkey[pt]
                vcells[vid].append(i)
                ring.append(vid)
            cell_v.append(ring)
        nbrs: list[list[int]] = []
        for i, ring in enumerate(cell_v):
            count: dict[int, int] = {}
            for v in ring:
                for j in vcells[v]:
                    if j != i:
                        count[j] = count.get(j, 0) + 1
            nbrs.append(sorted(j for j, k in count.items() if k >= 2))
        n = len(centers)

    if mesh != "voronoi":  # hex: the blobs are drawn first, as they always were (same planet for the same seed)
        blobs = [(rng.uniform(0.08, 0.92) * width, rng.uniform(0.2, 0.8) * height,
                  rng.uniform(*blob_r) * width, rng.uniform(0.7, 1.0)) for _ in range(n_blobs)]
    grid = [[rng.random() for _ in range(17)] for _ in range(9)]  # smooth noise for moisture (and hex heights)

    def noise(x: float, y: float) -> float:
        gx, gy = x / width * 16, y / height * 8
        x0, y0 = min(int(gx), 15), min(int(gy), 7)
        fx, fy = gx - x0, gy - y0
        a, b = grid[y0][x0], grid[y0][x0 + 1]
        c, d = grid[y0 + 1][x0], grid[y0 + 1][x0 + 1]
        return (a * (1 - fx) + b * fx) * (1 - fy) + (c * (1 - fx) + d * fx) * fy

    if mesh == "voronoi":
        h = wg_terrain.heightmap(centers, nbrs, width, height, nrng, n_blobs=n_blobs, blob_r=blob_r,
                                 sea_share=sea_share, shape=land,
                                 edge_falloff=0.15 if land in ("continents", "pangea", "old-world") else 0.45)
        # water: depressions filled or turned into lakes, drainage, rivers with tributaries, harbours (#40)
        from atlas.worldgen import water as wg_water
        frame = lambda x, y: x <= 0.01 or y <= 0.01 or x >= width - 0.01 or y >= height - 0.01  # noqa: E731
        edge = [any(frame(*vpos[v]) for v in cell_v[i]) for i in range(n)]
        # climate: temperature from latitude and height, rain carried by prevailing winds, rain shadows (#41)
        from atlas.worldgen import climate as wg_climate
        cell_lat = wg_climate.latitude_of(centers, height, coords["latN"], coords["latT"])
        rain = wg_climate.precipitation(centers, nbrs, h, cell_lat)
        hydro = wg_water.hydrology(h, nbrs, edge, precip=[float(x) for x in rain])
        h = hydro.h
        temp = wg_climate.temperature(cell_lat, np.asarray(h, dtype=float))
    else:
        # ------------------------------------------------------------------ heightmap
        raw = []
        for (x, y) in centers:
            v = sum(amp * math.exp(-(((x - bx) ** 2 + (y - by) ** 2) / (rad ** 2))) for bx, by, rad, amp in blobs)
            v += 0.45 * noise(x, y) - 0.1 * abs(y / height - 0.5)
            raw.append(v)
        thr = sorted(raw)[int(n * sea_share)]
        lo, hi = min(raw), max(raw)
        h = [int(20 + (v - thr) / (hi - thr) * 80) if v >= thr else int(19 * (v - lo) / (thr - lo)) for v in raw]
    land = [x >= 20 for x in h]

    # ------------------------------------------------------------------ biomes
    # degrees of latitude per degree of mean annual cooling: a region at 45 degrees is temperate, not taiga
    lat_cooling = 0.45 if region else 0.6
    moist = [noise(width - x, height - y) for (x, y) in centers]
    biome = wg_climate.biomes(temp, rain, h, hydro.flux) if mesh == "voronoi" else []
    for i, (x, y) in enumerate(centers if mesh != "voronoi" else []):
        if not land[i]:
            biome.append(0)
            continue
        lat = coords["latN"] - y / height * coords["latT"]
        t = 30 - abs(lat) * lat_cooling - max(0, h[i] - 50) * 0.25
        m = moist[i]
        if t < -6:
            b = 11
        elif t < 0:
            b = 10
        elif t < 6:
            b = 9
        elif t < 16:
            b = 8 if m > 0.62 else 6 if m > 0.3 else 4 if m > 0.18 else 2
        else:
            b = 1 if m < 0.22 else 3 if m < 0.38 else 5 if m < 0.6 else 7
        if h[i] < 24 and m > 0.72 and t > 0:
            b = 12
        biome.append(b)

    # ------------------------------------------------------------------ features (landmasses)
    feature = [0] * n
    features: list[Any] = [0, {"i": 1, "type": "ocean", "land": False, "border": True}]
    for i in range(n):
        if not land[i]:
            feature[i] = 1
    for i in range(n):
        if land[i] and feature[i] == 0:
            fid = len(features)
            features.append({"i": fid, "type": "island", "land": True, "border": False})
            q = deque([i])
            feature[i] = fid
            while q:
                a = q.popleft()
                for b2 in nbrs[a]:
                    if land[b2] and feature[b2] == 0:
                        feature[b2] = fid
                        q.append(b2)
    if mesh == "voronoi":
        for lake in hydro.lakes:
            fid = len(features)
            features.append({"i": fid, "type": "lake", "land": False, "border": False, "cells": len(lake)})
            for c in lake:
                feature[c] = fid
    sizes = {f["i"]: sum(1 for x in feature if x == f["i"]) for f in features[2:] if f["land"]}

    # ------------------------------------------------------------------ cultures (spatial)
    def farthest(cands: list[int], k: int, avoid: list[int] | None = None) -> list[int]:
        chosen = list(avoid or [])
        out: list[int] = []
        pool = sorted(cands, key=lambda c: rng.random())
        for _ in range(k):
            best = max(pool, key=lambda c: min(((centers[c][0] - centers[o][0]) ** 2 +
                                                (centers[c][1] - centers[o][1]) ** 2 for o in chosen),
                                               default=1e12) if c not in chosen else -1)
            chosen.append(best)
            out.append(best)
        return out

    def grow(seeds: dict[int, int], allowed, cost=lambda c: 1.0) -> list[int]:
        owner = [0] * n
        dist = [math.inf] * n
        pq: list[tuple[float, int, int]] = []
        for cell, oid in seeds.items():
            owner[cell], dist[cell] = oid, 0.0
            heapq.heappush(pq, (0.0, cell, oid))
        while pq:
            d, a, oid = heapq.heappop(pq)
            if d > dist[a]:
                continue
            for b2 in nbrs[a]:
                if not allowed(b2):
                    continue
                nd = d + cost(b2)
                if nd < dist[b2]:
                    dist[b2], owner[b2] = nd, oid
                    heapq.heappush(pq, (nd, b2, oid))
        return owner

    land_cells = [i for i in range(n) if land[i]]
    habitable = [i for i in land_cells if biome[i] not in (10, 11) and sizes.get(feature[i], 0) >= 30]
    cult_centers = farthest(habitable, len(culture_list))
    culture = grow({c: k + 1 for k, c in enumerate(cult_centers)}, lambda c: land[c], lambda c: 1.0)
    for f in sizes:  # islands unreached by any culture
        pass
    cultures = [{"i": 0, "name": "Wildlands", "base": 0}] + [
        {"i": k + 1, "name": nm, "base": k, "center": cult_centers[k], "color": STATE_COLORS[(k * 3) % 12]}
        for k, nm in enumerate(culture_list)]

    # ------------------------------------------------------------------ states
    big = [f for f, s in sizes.items() if s >= 30]
    caps: list[int] = []
    for f in sorted(big, key=lambda f: -sizes[f]):
        share = max(1, round(n_states * sizes[f] / sum(sizes[g] for g in big)))
        cands = [i for i in habitable if feature[i] == f]
        caps += farthest(cands, min(share, len(cands)), caps)
    caps = caps[:n_states]
    state = grow({c: k + 1 for k, c in enumerate(caps)}, lambda c: land[c], lambda c: BIOME_COST.get(biome[c], 3))
    states: list[dict[str, Any]] = [{"i": 0, "name": "Neutrals", "diplomacy": [], "removed": False}]
    for k, cap in enumerate(caps):
        cul = culture[cap] or 1
        base = _name(rng, cul - 1)
        form, full = rng.choice(FORMS)
        states.append({"i": k + 1, "name": base, "form": form, "formName": form, "fullName": full.format(base),
                       "capital": 0, "center": cap, "culture": cul, "color": STATE_COLORS[k % 12],
                       "type": "Naval" if any(not land[b2] for b2 in nbrs[cap]) else "Generic",
                       "expansionism": round(rng.uniform(0.8, 2.2), 2)})

    # neighbours + diplomacy
    neighbours = {s["i"]: set() for s in states[1:]}
    for a in land_cells:
        for b2 in nbrs[a]:
            if land[b2] and state[a] and state[b2] and state[a] != state[b2]:
                neighbours[state[a]].add(state[b2])
    ns = len(states)
    rel = [["x"] * ns for _ in range(ns)]
    for a in range(1, ns):
        for b2 in range(a + 1, ns):
            near, far = HOSTILITY_POOLS.get(hostility, HOSTILITY_POOLS["uneasy"])
            pool = near if b2 in neighbours[a] else far
            rel[a][b2] = rel[b2][a] = rng.choice(pool)
    for s in states[1:]:
        s["diplomacy"] = rel[s["i"]]
        s["neighbors"] = sorted(neighbours[s["i"]])

    # ------------------------------------------------------------------ provinces
    province = [0] * n
    provinces: list[Any] = [0]
    for s in states[1:]:
        cells = [i for i in land_cells if state[i] == s["i"]]
        if region:  # provinces by area, not a fixed few per realm
            cell_km2 = math.sqrt(3) / 2 * cell_km ** 2
            k = max(1, min(40, round(len(cells) * cell_km2 / PROVINCE_KM2)))
        else:
            k = max(2, min(6, len(cells) // 55))
        pcs = farthest([s["center"]] + [c for c in cells if biome[c] != 11], k, [])
        if s["center"] not in pcs:
            pcs[0] = s["center"]
        local = grow({c: len(provinces) + j for j, c in enumerate(pcs)},
                     lambda c, sid=s["i"]: land[c] and state[c] == sid)
        for c in cells:
            province[c] = local[c]
        s["provinces"] = []
        for j, c in enumerate(pcs):
            pid = len(provinces)
            pname = _name(rng, (s["culture"] - 1))
            form = rng.choice(["Province", "County", "March", "Barony", "Shire"])
            provinces.append({"i": pid, "state": s["i"], "center": c, "burg": 0, "name": pname,
                              "formName": form, "fullName": f"{pname} {form}", "color": s["color"]})
            s["provinces"].append(pid)

    # ------------------------------------------------------------------ burgs
    burg_of = [0] * n
    burgs: list[Any] = [0]

    def add_burg(cell: int, capital: bool) -> int:
        bid = len(burgs)
        port = 1 if any(not land[b2] for b2 in nbrs[cell]) else 0
        burgs.append({"i": bid, "cell": cell, "x": centers[cell][0], "y": centers[cell][1],
                      "name": _name(rng, (culture[cell] or 1) - 1), "state": state[cell],
                      "culture": culture[cell], "capital": 1 if capital else 0, "port": port,
                      "population": round(rng.uniform(8, 40) if capital else rng.uniform(0.5, 9), 2),
                      "type": "Naval" if port else "Generic", "feature": feature[cell]})
        burg_of[cell] = bid
        return bid

    for s in states[1:]:
        s["capital"] = add_burg(s["center"], True)
    for p in provinces[1:]:
        p["burg"] = burg_of[p["center"]] or add_burg(p["center"], False)
    extra = [i for i in habitable if not burg_of[i] and state[i]]
    cap_towns = max_towns if region else 18 + len(provinces)
    spacing = TOWN_SPACING_KM / km_px if region else 5 * radius
    for c in sorted(extra, key=lambda c: rng.random()):
        if len(burgs) > cap_towns:
            break
        x, y = centers[c]
        if all((x - b["x"]) ** 2 + (y - b["y"]) ** 2 > spacing ** 2 for b in burgs[1:]):
            add_burg(c, False)

    # ------------------------------------------------------------------ religions
    religions = [{"i": 0, "name": "No religion"}] + [
        {"i": k + 1, "name": nm, "type": tp, "color": STATE_COLORS[(k * 5) % 12]} for k, (nm, tp) in enumerate(religion_list)]
    religion = [0 if not land[i] else 1 + (culture[i] + state[i]) % len(religion_list) for i in range(n)]

    # ------------------------------------------------------------------ routes (roads inside each state)
    def tree(a: int) -> dict[int, int]:
        """Least-cost paths over land from ``a`` to everywhere: cell -> previous cell."""
        prev = {a: -1}
        dist = {a: 0.0}
        pq = [(0.0, a)]
        while pq:
            d, u = heapq.heappop(pq)
            if d > dist[u]:
                continue
            for v in nbrs[u]:
                if not land[v]:
                    continue
                nd = d + BIOME_COST.get(biome[v], 3)
                if nd < dist.get(v, math.inf):
                    dist[v], prev[v] = nd, u
                    heapq.heappush(pq, (nd, v))
        return prev

    def path(prev: dict[int, int], a: int, b2: int) -> list[int]:
        if b2 not in prev:
            return []
        out = [b2]
        while out[-1] != a:
            out.append(prev[out[-1]])
        return out[::-1]

    routes = []
    for s in states[1:]:
        cap = burgs[s["capital"]]
        prev = tree(cap["cell"])
        for b in burgs[1:]:
            if b["state"] == s["i"] and b["i"] != cap["i"]:
                cells = path(prev, cap["cell"], b["cell"])
                if len(cells) > 1:
                    routes.append({"i": len(routes), "group": "roads",
                                   "points": [[centers[c][0], centers[c][1], c] for c in cells]})
        if region:  # highways to the capitals of neighbouring realms (overland), so capitals are days, not months, apart
            for other in s.get("neighbors", []):
                if other > s["i"]:
                    cells = path(prev, cap["cell"], burgs[states[other]["capital"]]["cell"])
                    if len(cells) > 1:
                        routes.append({"i": len(routes), "group": "highways",
                                       "points": [[centers[c][0], centers[c][1], c] for c in cells]})

    # ------------------------------------------------------------------ rivers
    rivers = []
    if mesh == "voronoi":
        for k, r in enumerate(hydro.rivers):
            src = r["source"]
            kind = "River" if r["discharge"] >= 3 * min(x["discharge"] for x in hydro.rivers) else "Stream"
            rivers.append({"i": k + 1, "name": f"{_name(rng, (culture[src] or 1) - 1)} {kind}", "type": kind,
                           "source": src, "mouth": r["mouth"], "cells": r["cells"], "discharge": r["discharge"],
                           "parent": (r["parent"] + 1) if r["parent"] is not None else 0})
    springs = [] if mesh == "voronoi" else sorted((i for i in land_cells if h[i] > 55), key=lambda c: rng.random())
    used: set[int] = set()
    max_rivers, max_len = (max(14, n // 350), max(60, n // 40)) if region else (14, 60)
    for src in springs:
        if len(rivers) >= max_rivers or src in used:
            continue
        cells = [src]
        while land[cells[-1]] and len(cells) < max_len:
            nxt = min(nbrs[cells[-1]], key=lambda c: h[c])
            if h[nxt] >= h[cells[-1]] and land[nxt] or nxt in cells:
                break
            cells.append(nxt)
        if len(cells) >= 5 and not land[cells[-1]]:
            used.update(cells)
            rivers.append({"i": len(rivers) + 1, "name": _name(rng, (culture[src] or 1) - 1) + " River",
                           "type": "River", "source": src, "mouth": cells[-1], "cells": cells})

    river_cell = {c: r["i"] for r in rivers for c in r["cells"][:-1]} if mesh == "voronoi" else {}
    pop = [round(rng.uniform(0.5, 6) * (1 if biome[i] in (4, 5, 6, 8) else 0.4), 2) if land[i] else 0 for i in range(n)]
    cells_out = [{"i": i, "v": cell_v[i], "c": nbrs[i], "p": list(centers[i]), "h": h[i], "f": feature[i],
                  "biome": biome[i], "state": state[i], "province": province[i], "culture": culture[i] if land[i] else 0,
                  "religion": religion[i], "burg": burg_of[i], "pop": pop[i],
                  **({"fl": round(hydro.flux[i], 1), "r": river_cell.get(i, 0), "harbor": hydro.harbor[i]}
                     if mesh == "voronoi" else {})} for i in range(n)]
    return {
        "info": {"version": "sample-1", "description": "Living World Engine "
                 + (f"sample region ({region_km[0]:.0f} x {region_km[1]:.0f} km)" if region else "sample planet")
                 + " in Azgaar Full JSON shape", "mesh": mesh,
                 "mapName": name, "width": width, "height": height, "seed": str(seed)},
        "settings": {"mapName": name, "distanceUnit": "mi"},
        "mapCoordinates": coords,
        "pack": {
            "cells": cells_out,
            "vertices": [{"i": i, "p": p, "c": vcells[i]} for i, p in enumerate(vpos)],
            "features": features, "biomes": [{"i": i, "name": nm, "color": col} for i, nm, col in BIOMES],
            "cultures": cultures, "burgs": burgs, "states": states, "provinces": provinces,
            "religions": religions, "routes": routes, "rivers": rivers,
        },
    }


def write_sample(path: str | Path, seed: int = 1795) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(generate(seed)), encoding="utf-8")
    return p

"""Deterministic sample planet written in Azgaar's *Full JSON* export format.

Azgaar's Fantasy Map Generator (https://github.com/Azgaar/Fantasy-Map-Generator, MIT) is the
intended source of worlds. Its repository only ships `.map` saves, which do not store cell
polygons, so for tests, mock mode and first-run demos we generate a small world here that uses
exactly the fields the importer reads from a real "Save > JSON > Full" export:

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


def generate(seed: int = 1795, *, width: int = 1500, height: int = 700, radius: float = 12.0,
             n_states: int = 9, name: str = "Thessaly Reach") -> dict[str, Any]:
    rng = random.Random(seed)

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

    # ------------------------------------------------------------------ heightmap
    blobs = [(rng.uniform(0.12, 0.88) * width, rng.uniform(0.25, 0.75) * height,
              rng.uniform(0.10, 0.22) * width, rng.uniform(0.7, 1.0)) for _ in range(6)]
    grid = [[rng.random() for _ in range(17)] for _ in range(9)]

    def noise(x: float, y: float) -> float:
        gx, gy = x / width * 16, y / height * 8
        x0, y0 = int(gx), int(gy)
        fx, fy = gx - x0, gy - y0
        a, b = grid[y0][x0], grid[y0][x0 + 1]
        c, d = grid[y0 + 1][x0], grid[y0 + 1][x0 + 1]
        return (a * (1 - fx) + b * fx) * (1 - fy) + (c * (1 - fx) + d * fx) * fy

    raw = []
    for (x, y) in centers:
        v = sum(amp * math.exp(-(((x - bx) ** 2 + (y - by) ** 2) / (rad ** 2))) for bx, by, rad, amp in blobs)
        v += 0.45 * noise(x, y) - 0.1 * abs(y / height - 0.5)
        raw.append(v)
    thr = sorted(raw)[int(n * 0.6)]
    lo, hi = min(raw), max(raw)
    h = [int(20 + (v - thr) / (hi - thr) * 80) if v >= thr else int(19 * (v - lo) / (thr - lo)) for v in raw]
    land = [x >= 20 for x in h]

    # ------------------------------------------------------------------ biomes
    coords = {"latT": 140, "latN": 70, "latS": -70, "lonT": 300, "lonW": -150, "lonE": 150}
    moist = [noise(width - x, height - y) for (x, y) in centers]
    biome = []
    for i, (x, y) in enumerate(centers):
        if not land[i]:
            biome.append(0)
            continue
        lat = coords["latN"] - y / height * coords["latT"]
        t = 30 - abs(lat) * 0.6 - max(0, h[i] - 50) * 0.25
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
    sizes = {f["i"]: sum(1 for x in feature if x == f["i"]) for f in features[2:]}

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
    cult_centers = farthest(habitable, len(CULTURE_NAMES))
    culture = grow({c: k + 1 for k, c in enumerate(cult_centers)}, lambda c: land[c], lambda c: 1.0)
    for f in sizes:  # islands unreached by any culture
        pass
    cultures = [{"i": 0, "name": "Wildlands", "base": 0}] + [
        {"i": k + 1, "name": nm, "base": k, "center": cult_centers[k], "color": STATE_COLORS[(k * 3) % 12]}
        for k, nm in enumerate(CULTURE_NAMES)]

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
            pool = (["Rival", "Rival", "Enemy", "Suspicion", "Friendly", "Ally"] if b2 in neighbours[a]
                    else ["Neutral", "Neutral", "Unknown", "Friendly", "Suspicion"])
            rel[a][b2] = rel[b2][a] = rng.choice(pool)
    for s in states[1:]:
        s["diplomacy"] = rel[s["i"]]
        s["neighbors"] = sorted(neighbours[s["i"]])

    # ------------------------------------------------------------------ provinces
    province = [0] * n
    provinces: list[Any] = [0]
    for s in states[1:]:
        cells = [i for i in land_cells if state[i] == s["i"]]
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
    for c in sorted(extra, key=lambda c: rng.random()):
        if len(burgs) > 18 + len(provinces):
            break
        x, y = centers[c]
        if all((x - b["x"]) ** 2 + (y - b["y"]) ** 2 > (5 * radius) ** 2 for b in burgs[1:]):
            add_burg(c, False)

    # ------------------------------------------------------------------ religions
    religions = [{"i": 0, "name": "No religion"}] + [
        {"i": k + 1, "name": nm, "type": tp, "color": STATE_COLORS[(k * 5) % 12]} for k, (nm, tp) in enumerate(RELIGIONS)]
    religion = [0 if not land[i] else 1 + (culture[i] + state[i]) % len(RELIGIONS) for i in range(n)]

    # ------------------------------------------------------------------ routes (roads inside each state)
    def path(a: int, b2: int) -> list[int]:
        prev = {a: -1}
        dist = {a: 0.0}
        pq = [(0.0, a)]
        while pq:
            d, u = heapq.heappop(pq)
            if u == b2:
                break
            if d > dist[u]:
                continue
            for v in nbrs[u]:
                if not land[v]:
                    continue
                nd = d + BIOME_COST.get(biome[v], 3)
                if nd < dist.get(v, math.inf):
                    dist[v], prev[v] = nd, u
                    heapq.heappush(pq, (nd, v))
        if b2 not in prev:
            return []
        out = [b2]
        while out[-1] != a:
            out.append(prev[out[-1]])
        return out[::-1]

    routes = []
    for s in states[1:]:
        cap = burgs[s["capital"]]
        for b in burgs[1:]:
            if b["state"] == s["i"] and b["i"] != cap["i"]:
                cells = path(cap["cell"], b["cell"])
                if len(cells) > 1:
                    routes.append({"i": len(routes), "group": "roads",
                                   "points": [[centers[c][0], centers[c][1], c] for c in cells]})

    # ------------------------------------------------------------------ rivers
    rivers = []
    springs = sorted((i for i in land_cells if h[i] > 55), key=lambda c: rng.random())
    used: set[int] = set()
    for src in springs:
        if len(rivers) >= 14 or src in used:
            continue
        cells = [src]
        while land[cells[-1]] and len(cells) < 60:
            nxt = min(nbrs[cells[-1]], key=lambda c: h[c])
            if h[nxt] >= h[cells[-1]] and land[nxt] or nxt in cells:
                break
            cells.append(nxt)
        if len(cells) >= 5 and not land[cells[-1]]:
            used.update(cells)
            rivers.append({"i": len(rivers) + 1, "name": _name(rng, (culture[src] or 1) - 1) + " River",
                           "type": "River", "source": src, "mouth": cells[-1], "cells": cells})

    pop = [round(rng.uniform(0.5, 6) * (1 if biome[i] in (4, 5, 6, 8) else 0.4), 2) if land[i] else 0 for i in range(n)]
    cells_out = [{"i": i, "v": cell_v[i], "c": nbrs[i], "p": list(centers[i]), "h": h[i], "f": feature[i],
                  "biome": biome[i], "state": state[i], "province": province[i], "culture": culture[i] if land[i] else 0,
                  "religion": religion[i], "burg": burg_of[i], "pop": pop[i]} for i in range(n)]
    return {
        "info": {"version": "sample-1", "description": "Living World Engine sample planet in Azgaar Full JSON shape",
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

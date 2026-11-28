"""Water that behaves: depressions filled or turned into lakes, drainage, rivers with tributaries, harbours.

* **Priority-flood** from the sea and the map frame gives every land cell a way out. A basin big enough becomes a
  **lake** (its cells drop below sea level, so the importer draws them as water); a small one is levelled up to its
  spill height, so nothing drains uphill.
* **Flow:** each land cell drains to its lowest neighbour (ties broken toward the spill path). Flux is the
  precipitation it collects plus everything upstream of it (uniform rain until phase 3 provides climate).
* **Rivers:** cells whose flux reaches ``threshold``. Each river runs from a source (no upstream river cell) down to
  the sea, a lake, the map edge, or the river it joins (a tributary, with ``parent`` set). Every river ends on water or
  on another river, never climbs, and is at least ``min_len`` cells long.
* **Harbours:** a coastal land cell with one or two sea neighbours is a sheltered harbour; a river mouth is better.
  ``harbor`` (0 = not coastal) feeds town placement in phase 4.
"""
from __future__ import annotations

import heapq
from dataclasses import dataclass, field

SEA_LEVEL = 20


@dataclass
class Water:
    h: list[int]                       # heights after levelling small depressions and sinking lakes
    lakes: list[list[int]]             # cells of each lake
    downhill: list[int]                # cell -> the cell it drains to (-1: water or map edge)
    flux: list[float]                  # water passing through each cell
    rivers: list[dict] = field(default_factory=list)  # {"cells": [...], "source", "mouth", "parent", "discharge"}
    harbor: list[int] = field(default_factory=list)   # 0 none, 1 good, 2 river mouth (best), 3 exposed coast


def hydrology(h: list[int], nbrs: list[list[int]], edge: list[bool], *, precip: list[float] | None = None,
              lake_min_cells: int = 6, lake_min_depth: int = 3, threshold: float | None = None,
              min_len: int = 4) -> Water:
    """Fill, drain and channel the heightmap ``h`` (Azgaar scale: < 20 is water). ``edge`` marks frame cells."""
    n = len(h)
    land = [x >= SEA_LEVEL for x in h]
    rain = precip or [1.0] * n
    # ---------------------------------------------------------------- priority-flood: spill heights
    spill = [float(x) for x in h]
    done = [False] * n
    pq: list[tuple[float, int]] = []
    for i in range(n):
        if not land[i] or edge[i]:
            done[i] = True
            heapq.heappush(pq, (spill[i], i))
    while pq:
        level, a = heapq.heappop(pq)
        for b in nbrs[a]:
            if not done[b]:
                done[b] = True
                spill[b] = max(float(h[b]), level + 1e-3)  # a hair above, so filled flats still drain
                heapq.heappush(pq, (spill[b], b))
    # ---------------------------------------------------------------- basins: lakes or levelled
    out = list(h)
    lakes: list[list[int]] = []
    seen = [False] * n
    for i in range(n):
        if land[i] and not seen[i] and spill[i] - h[i] >= 1:
            basin, stack = [], [i]
            seen[i] = True
            while stack:
                a = stack.pop()
                basin.append(a)
                for b in nbrs[a]:
                    if land[b] and not seen[b] and spill[b] - h[b] >= 1:
                        seen[b] = True
                        stack.append(b)
            depth = max(spill[c] - h[c] for c in basin)
            if len(basin) >= lake_min_cells and depth >= lake_min_depth:
                lakes.append(sorted(basin))
                for c in basin:
                    out[c] = SEA_LEVEL - 1  # water
            else:
                for c in basin:
                    out[c] = max(out[c], int(spill[c]))  # levelled: flat, draining over its rim
    water = [x < SEA_LEVEL for x in out]
    # ---------------------------------------------------------------- drainage: lowest neighbour by spill height
    downhill = [-1] * n
    for i in range(n):
        if water[i] or edge[i]:
            continue
        best = min(nbrs[i], key=lambda j: (spill[j] if not water[j] else -1.0, j))
        if water[best] or spill[best] < spill[i]:
            downhill[i] = best
    # ---------------------------------------------------------------- flux, from the highest cells down
    flux = [rain[i] if not water[i] else 0.0 for i in range(n)]
    for i in sorted((i for i in range(n) if not water[i]), key=lambda i: -spill[i]):
        d = downhill[i]
        if d >= 0:
            flux[d] += flux[i]
    land_cells = sum(1 for w in water if not w)
    thr = threshold if threshold is not None else max(12.0, land_cells / 250)
    # ---------------------------------------------------------------- rivers: from each source down to water or a river
    is_river = [not water[i] and flux[i] >= thr for i in range(n)]
    feeds = [0] * n
    for i in range(n):
        if is_river[i] and downhill[i] >= 0 and is_river[downhill[i]]:
            feeds[downhill[i]] += 1
    river_of = [-1] * n
    rivers: list[dict] = []
    for src in sorted((i for i in range(n) if is_river[i] and feeds[i] == 0), key=lambda i: (-flux[i], i)):
        cells = [src]
        parent = None
        while True:
            d = downhill[cells[-1]]
            if d < 0:  # the map edge: the river leaves the region
                break
            if water[d]:
                cells.append(d)  # the mouth: sea or lake
                break
            if river_of[d] >= 0:  # joins a river already traced: a tributary
                cells.append(d)
                parent = river_of[d]
                break
            cells.append(d)
        trunk = [c for c in cells if not water[c] and river_of[c] < 0]
        if len(trunk) < min_len:
            continue
        rid = len(rivers)
        for c in trunk:
            river_of[c] = rid
        rivers.append({"cells": cells, "source": src, "mouth": cells[-1], "parent": parent,
                       "discharge": round(flux[trunk[-1]], 1)})
    # ---------------------------------------------------------------- harbours
    harbor = [0] * n
    mouths = {r["cells"][-2] for r in rivers if len(r["cells"]) >= 2 and water[r["cells"][-1]]}
    for i in range(n):
        if water[i]:
            continue
        sea = sum(1 for j in nbrs[i] if water[j])
        if sea:
            harbor[i] = 2 if i in mouths else 1 if sea <= 2 else 3
    return Water(h=out, lakes=lakes, downhill=downhill, flux=flux, rivers=rivers, harbor=harbor)

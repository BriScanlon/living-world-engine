"""Peoples and polities that make geographic sense (phase 4, #42): cultures, realms, towns and provinces.

* **Habitability** (0-1 per land cell): the biome (temperate forest and grassland best; ice and desert worst), a
  bonus for river flow and for harbours (river mouths best), less as the land rises.
* **Cultures** start at well-spread, habitable spots and grow at terrain cost; mountains slow them and the sea all
  but stops them, so ranges and straits divide peoples.
* **Realms** expand from capitals (the most habitable spots, spread apart) at a cost that rises over mountains,
  across rivers and into another people's land, scaled by each realm's expansionism, so borders tend to follow
  ridges, rivers and cultures. Glaciers stay wild.
* **Towns** go where habitability is highest, at least ``spacing_px`` apart, up to the budget; harbour cells rank
  high, so ports sit at good harbours. Population follows habitability.
* **Provinces** grow inside each realm from its largest towns (the capital first), one per ``province_cells`` of
  land, so every province has a town.
"""
from __future__ import annotations

import heapq
import math
from dataclasses import dataclass

import numpy as np

SEA_LEVEL = 20
# biome id -> how well people can live there
BIOME_HAB = {1: 0.15, 2: 0.2, 3: 0.6, 4: 0.9, 5: 0.8, 6: 1.0, 7: 0.5, 8: 0.75, 9: 0.45, 10: 0.12, 11: 0.0, 12: 0.35}
BIOME_COST = {1: 6, 2: 5, 3: 2, 4: 1, 5: 2, 6: 2, 7: 4, 8: 3, 9: 4, 10: 6, 11: 12, 12: 5}
SEA_COST = 25.0  # crossing a sea cell while expanding: islands near a coast join, far ones only if nobody nearer


def habitability(biome: list[int], h: list[int], flux: list[float], harbor: list[int]) -> np.ndarray:
    hb = np.array([BIOME_HAB.get(b, 0.0) for b in biome])
    hh = np.asarray(h, dtype=float)
    f = np.asarray(flux, dtype=float)
    land = hh >= SEA_LEVEL
    fmax = float(np.quantile(f[land], 0.99)) if land.any() else 1.0
    river = 0.5 * np.clip(np.log1p(f) / max(np.log1p(fmax), 1e-9), 0, 1) ** 1.5  # big rivers draw towns inland
    port = np.array([{1: 0.12, 2: 0.25, 3: 0.03}.get(x, 0.0) for x in harbor])  # a good harbour helps; coast alone barely
    high = 1 - np.clip((hh - 45) / 55, 0, 0.85)
    return np.where(land, np.clip(hb * high + river * (hb > 0) + port * (hb > 0), 0, 1.6), 0.0)


def spread(scores: np.ndarray, xy: np.ndarray, k: int, min_dist: float, allowed=None) -> list[int]:
    """Up to ``k`` cells, best first, at least ``min_dist`` apart (relaxing the distance if too few fit)."""
    order = [int(i) for i in np.argsort(-scores, kind="stable") if scores[i] > 0 and (allowed is None or allowed(int(i)))]
    picked: list[int] = []
    d = min_dist
    while len(picked) < k and d > min_dist * 0.2:
        for i in order:
            if len(picked) >= k:
                break
            if i not in picked and all(math.dist(xy[i], xy[j]) >= d for j in picked):
                picked.append(i)
        d *= 0.8
    return picked


def expand(seeds: dict[int, int], nbrs: list[list[int]], step, land: list[bool]) -> list[int]:
    """Owner per cell by least total cost from its seed (Dijkstra); ``step(a, b, owner)`` is the cost of a -> b,
    or None where an owner can't go. Sea cells carry expansion but are never owned."""
    n = len(nbrs)
    owner = [0] * n
    best = [math.inf] * n
    pq: list[tuple[float, int, int]] = []
    for cell, o in seeds.items():
        best[cell] = 0.0
        owner[cell] = o
        heapq.heappush(pq, (0.0, cell, o))
    while pq:
        d, a, o = heapq.heappop(pq)
        if d > best[a] or owner[a] != o:
            continue
        for b in nbrs[a]:
            c = step(a, b, o)
            if c is None:
                continue
            nd = d + c
            if nd < best[b]:
                best[b] = nd
                owner[b] = o
                heapq.heappush(pq, (nd, b, o))
    return [o if land[i] else 0 for i, o in enumerate(owner)]


def _balanced_seeds(cells: list[int], cap: int, k: int, xy: np.ndarray, hab: np.ndarray, towns: list[int],
                    town_rank: dict[int, int], all_towns: list[int] | None = None, min_gap: float = 0.0,
                    rounds: int = 6) -> list[int]:
    """``k`` province seeds that split a realm's ``cells`` into near-equal patches: Lloyd's relaxation (k-means on
    cell positions) from spread-out starts, then each patch's seed snaps to its best town (the capital keeps its own
    patch), or, if the patch has no town, to its most habitable cell (where a town is founded)."""
    if k <= 1 or len(cells) <= k:
        return [cap]
    pts = xy[cells]
    # farthest-point starts from the capital: always spread out, whatever the realm's shape
    first = cells.index(cap) if cap in cells else 0
    chosen = [first]
    dmin = ((pts - pts[first]) ** 2).sum(-1)
    while len(chosen) < k:
        nxt = int(np.argmax(dmin))
        chosen.append(nxt)
        dmin = np.minimum(dmin, ((pts - pts[nxt]) ** 2).sum(-1))
    centres = pts[chosen].astype(float)
    for _ in range(rounds):
        assign = np.argmin(((pts[:, None, :] - centres[None, :, :]) ** 2).sum(-1), axis=1)
        for j in range(k):
            mine = pts[assign == j]
            if len(mine):
                centres[j] = mine.mean(axis=0)
    assign = np.argmin(((pts[:, None, :] - centres[None, :, :]) ** 2).sum(-1), axis=1)
    patch_of = {c: int(a) for c, a in zip(cells, assign)}
    seeds: list[int | None] = [None] * k
    seeds[patch_of[cap]] = cap
    town_set = set(towns)
    for j in range(k):
        if seeds[j] is not None:
            continue
        members = [c for c in cells if patch_of[c] == j]
        if not members:
            continue
        here = [t for t in members if t in town_set]
        if here:  # the patch's best town, nearest the patch centre among equals
            seeds[j] = min(here, key=lambda t: (town_rank.get(t, 1e9) > 30, math.dist(xy[t], centres[j])))
        else:  # found a town at the patch's best cell that keeps its distance from every town (else merge)
            placed = [*(all_towns or towns), *(s for s in seeds if s is not None)]
            room = [c for c in members if all(math.dist(xy[c], xy[t]) >= min_gap for t in placed)]
            if room:
                seeds[j] = max(room, key=lambda c: (float(hab[c]), -math.dist(xy[c], centres[j])))
    return [s for s in seeds if s is not None]


@dataclass
class Polities:
    culture: list[int]
    culture_centers: list[int]
    capitals: list[int]
    state: list[int]
    towns: list[int]          # cells, capitals first (in realm order)
    population: list[float]   # per town
    port: list[int]           # per town: 1 at a real harbour
    province: list[int]
    province_seeds: list[list[int]]  # per realm (1-based index - 1): the town cells its provinces grew from


def build(xy: np.ndarray, nbrs: list[list[int]], h: list[int], biome: list[int], flux: list[float],
          harbor: list[int], rng, *, n_cultures: int, n_states: int, expansionism: list[float],
          spacing_px: float, max_towns: int, province_cells: float) -> Polities:
    n = len(h)
    land = [x >= SEA_LEVEL for x in h]
    hab = habitability(biome, h, flux, harbor)
    span = math.sqrt(max(1.0, float(np.ptp(xy[:, 0])) * float(np.ptp(xy[:, 1]))))
    f = np.asarray(flux, dtype=float)
    river_line = f >= (float(np.quantile(f[np.asarray(land)], 0.9)) if any(land) else 1.0)

    def terrain(b: int) -> float | None:
        if not land[b]:
            return SEA_COST
        return BIOME_COST.get(biome[b], 3) + max(0.0, (h[b] - 55) / 6)

    # cultures
    jitter = np.array([rng.random() * 0.15 for _ in range(n)]) * (hab > 0)  # land only: nobody settles the sea
    c_seeds = spread(hab + jitter, xy, n_cultures, span / math.sqrt(n_cultures) * 0.8)
    culture = expand({c: k + 1 for k, c in enumerate(c_seeds)}, nbrs, lambda a, b, o: terrain(b), land)
    # capitals and realms
    caps = spread(hab + jitter * 0.5, xy, n_states, span / math.sqrt(n_states) * 0.7, allowed=lambda i: biome[i] != 11)
    own_culture = {k + 1: culture[c] for k, c in enumerate(caps)}

    def realm_step(a: int, b: int, o: int) -> float | None:
        if land[b] and biome[b] == 11:
            return None  # glaciers stay wild
        c = terrain(b)
        if land[b] and river_line[b] and not river_line[a]:
            c += 4.0  # crossing onto a river: borders like to follow them
        if land[b] and culture[b] != own_culture[o]:
            c += 3.0
        return c / expansionism[o - 1]

    state = expand({c: k + 1 for k, c in enumerate(caps)}, nbrs, realm_step, land)
    # towns: capitals, then the most habitable spots in the realms, spaced apart; harbours rank high
    towns = list(caps)
    taken = set(towns)
    order = [int(i) for i in np.argsort(-(hab + jitter * 0.3), kind="stable") if state[i] and hab[i] > 0.1]
    for i in order:
        if len(towns) >= max_towns:
            break
        if i not in taken and all(math.dist(xy[i], xy[j]) >= spacing_px for j in towns):
            towns.append(i)
            taken.add(i)
    # provinces: inside each realm, from its largest towns (capital first), one per province_cells of land
    province = [0] * n
    seeds_per_realm: list[list[int]] = []
    town_rank = {t: r for r, t in enumerate(towns)}
    next_id = 1
    for k, cap in enumerate(caps):
        sid = k + 1
        cells = [i for i in range(n) if state[i] == sid]
        want = max(1, round(len(cells) / province_cells))
        seeds = _balanced_seeds(cells, cap, want, xy, hab, [t for t in towns if state[t] == sid], town_rank,
                                all_towns=towns, min_gap=0.6 * spacing_px)
        for s_ in seeds:  # a patch with no town of its own founds one at its seed
            if s_ not in taken:
                towns.append(s_)
                taken.add(s_)
        local = expand({c: next_id + j for j, c in enumerate(seeds)}, nbrs,
                       # a flat step plus a little terrain: provinces come out even-sized, still bounded by rivers
                       lambda a, b, o, s=sid: (2.0 + 0.25 * terrain(b) + (3.0 if river_line[b] and not river_line[a] else 0.0))
                       if land[b] and state[b] == s else None, land)
        for i in cells:
            province[i] = local[i] or next_id
        next_id += len(seeds)
        seeds_per_realm.append(seeds)
    # back within the budget: patches that founded a town may have pushed it over, so the least habitable towns
    # that are neither a capital nor a province seat give way
    if len(towns) > max_towns:
        keep = set(caps) | {s for seeds in seeds_per_realm for s in seeds}
        spare = sorted((t for t in towns if t not in keep), key=lambda t: float(hab[t]))
        drop = set(spare[:len(towns) - max_towns])
        towns = [t for t in towns if t not in drop]
    population = [round((8 + 32 * min(1.0, hab[t])) if t in set(caps) else (0.5 + 8.5 * min(1.0, hab[t] / 1.2)), 2)
                  for t in towns]
    port = [1 if harbor[t] in (1, 2) else 0 for t in towns]
    return Polities(culture=culture, culture_centers=c_seeds, capitals=caps, state=state, towns=towns,
                    population=population, port=port, province=province, province_seeds=seeds_per_realm)

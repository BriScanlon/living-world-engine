"""Heightmap, sea level and coastline for a cell mesh, on Azgaar's scale: 0-19 sea (by depth), 20-100 land.

* **Land** comes from Gaussian bumps (the land-shape answer: count, radius, sea share) mixed with fBm value noise
  (numpy, several octaves). Island shapes lean on the noise, so they break into many islands; continent shapes lean
  on the bumps, with noise for capes, bays and offshore islands.
* **Mountain chains** follow the boundaries of tectonic plates: irregular regions grown from random seeds. Some
  boundaries are convergent, and land near them rises in ranges that fade with distance and are broken up by ridged
  noise, so mountains run across continents as chains instead of sitting in their middles as domes.
* **Sea level** is the height quantile that gives the shape's sea share. A fall-off near the map frame keeps island
  worlds surrounded by sea, while letting continents run off the edge of a regional map.
* **Coastline clean-up:** one-cell islets sink and one-cell inlets fill.
"""
from __future__ import annotations

from collections import deque

import numpy as np

SEA_LEVEL = 20
ISLAND_SHAPES = {"archipelago", "islands"}
# shapes made of several landmasses: how many clusters the land bumps gather into (min, max)
CLUSTERS = {"continents": (2, 3), "islands": (4, 6), "pangea": (1, 1)}


def value_noise(xy: np.ndarray, width: float, height: float, cells: int, rng: np.random.Generator) -> np.ndarray:
    """Smooth noise in [0, 1]: random values on a lattice ``cells`` across, interpolated with a smoothstep."""
    gx = xy[:, 0] / width * cells
    gy = xy[:, 1] / height * max(1, round(cells * height / width))
    rows, cols = int(gy.max()) + 2, int(gx.max()) + 2
    lat = rng.random((rows, cols))
    x0, y0 = np.floor(gx).astype(int), np.floor(gy).astype(int)
    fx, fy = gx - x0, gy - y0
    sx, sy = fx * fx * (3 - 2 * fx), fy * fy * (3 - 2 * fy)
    a, b = lat[y0, x0], lat[y0, x0 + 1]
    c, d = lat[y0 + 1, x0], lat[y0 + 1, x0 + 1]
    return (a * (1 - sx) + b * sx) * (1 - sy) + (c * (1 - sx) + d * sx) * sy


def fbm(xy: np.ndarray, width: float, height: float, rng: np.random.Generator, *, octaves: int = 6, base: int = 3,
        gain: float = 0.55) -> np.ndarray:
    """Fractal sum of value noise, rescaled to [0, 1]."""
    total = np.zeros(len(xy))
    amp = 1.0
    for o in range(octaves):
        total += amp * value_noise(xy, width, height, base * 2 ** o, rng)
        amp *= gain
    lo, hi = total.min(), total.max()
    return (total - lo) / max(hi - lo, 1e-9)


def plates(xy: np.ndarray, nbrs: list[list[int]], width: float, height: float, rng: np.random.Generator,
           k: int) -> tuple[np.ndarray, np.ndarray]:
    """Tectonic plates: (plate id per cell, mountain-building strength per cell in [0, 1]).

    Cells go to the nearest of ``k`` random seeds, measured in noise-warped coordinates (irregular plates). About 60%
    of the plate pairs that meet are convergent. Strength is 1 on a convergent boundary and fades over a few cells."""
    warp = np.column_stack([fbm(xy, width, height, rng, octaves=3, base=2), fbm(xy, width, height, rng, octaves=3, base=2)])
    wxy = xy + (warp - 0.5) * 0.35 * np.array([width, height])
    seeds = np.column_stack([rng.uniform(0, width, k), rng.uniform(0, height, k)])
    plate = np.argmin(((wxy[:, None, :] - seeds[None, :, :]) ** 2).sum(-1), axis=1)
    converge = rng.random((k, k)) < 0.6
    converge = converge | converge.T
    n = len(xy)
    dist = np.full(n, np.inf)
    q: deque[int] = deque()
    for i in range(n):
        if any(plate[j] != plate[i] and converge[plate[i], plate[j]] for j in nbrs[i]):
            dist[i] = 0
            q.append(i)
    while q:  # hop distance from the nearest convergent boundary
        a = q.popleft()
        for b in nbrs[a]:
            if dist[b] > dist[a] + 1:
                dist[b] = dist[a] + 1
                q.append(b)
    reach = max(2.0, np.sqrt(n) / 28)  # chain half-width in cells: wider ranges on finer meshes
    return plate, np.exp(-(dist / reach) ** 2)


def heightmap(centers: list[tuple[float, float]], nbrs: list[list[int]], width: float, height: float,
              rng: np.random.Generator, *, n_blobs: int, blob_r: tuple[float, float], sea_share: float,
              shape: str = "continents", edge_falloff: float = 0.35) -> list[int]:
    """Heights 0-100 per cell (``SEA_LEVEL`` and up is land), with about ``sea_share`` of the cells under the sea."""
    xy = np.asarray(centers, dtype=float)
    n = len(xy)
    islands = shape in ISLAND_SHAPES
    # land: a few big bumps (the land-shape answer) mixed with fractal noise
    bumps = np.zeros(n)
    groups = CLUSTERS.get(shape)
    if groups:  # several landmasses: bumps gather round cluster centres spread across the map, with sea between
        k = int(rng.integers(groups[0], groups[1] + 1))
        lo, hi = (0.4, 0.6) if k == 1 else (0.15, 0.85)  # one landmass sits near the middle
        cands = np.column_stack([rng.uniform(lo, hi, 64) * width, rng.uniform(lo + 0.05, hi - 0.05, 64) * height])
        hubs = [cands[0]]
        while len(hubs) < k:
            far = np.min([np.hypot(*(cands - h).T) for h in hubs], axis=0)
            hubs.append(cands[int(np.argmax(far))])
        per_hub = np.zeros((len(hubs), n))
    for b in range(n_blobs):
        if groups:
            hx, hy = hubs[b % len(hubs)]
            bx = float(np.clip(hx + rng.normal(0, 0.07) * width, 0.05 * width, 0.95 * width))
            by = float(np.clip(hy + rng.normal(0, 0.07) * height, 0.08 * height, 0.92 * height))
            r = rng.uniform(*blob_r) * width * 0.75
            per_hub[b % len(hubs)] += rng.uniform(0.7, 1.0) * np.exp(-((xy[:, 0] - bx) ** 2 + (xy[:, 1] - by) ** 2) / (r * r))
        else:
            bx, by = rng.uniform(0.08, 0.92) * width, rng.uniform(0.15, 0.85) * height
            r = rng.uniform(*blob_r) * width
            bumps += rng.uniform(0.7, 1.0) * np.exp(-((xy[:, 0] - bx) ** 2 + (xy[:, 1] - by) ** 2) / (r * r))
    if groups:  # each landmass on its own: overlapping tails don't add up into land bridges between them
        bumps = per_hub.max(axis=0)
    bumps = bumps / max(bumps.max(), 1e-9)
    # how much the noise (not the bumps) draws the coast, and how fine it is: an archipelago is mostly noise
    mix, base = {"archipelago": (0.8, 9), "islands": (0.65, 6), "continents": (0.42, 4)}.get(shape, (0.4, 3))
    detail = fbm(xy, width, height, rng, base=base)
    v = (1 - mix) * bumps + mix * detail
    # the map frame: island worlds stay surrounded by sea; continents may run off a regional map's edge
    d = np.minimum.reduce([xy[:, 0], width - xy[:, 0], xy[:, 1], height - xy[:, 1]]) / (0.12 * min(width, height))
    v = v * (1 - edge_falloff + edge_falloff * np.clip(d, 0, 1))
    thr = float(np.quantile(v, sea_share))
    land = _clean_coast(v >= thr, nbrs)
    # relief: gentle rise inland, ranges along convergent plate boundaries, broken up by ridged noise
    _, chain = plates(xy, nbrs, width, height, rng, k=max(6, n_blobs + 4))
    ridge = 1 - np.abs(2 * fbm(xy, width, height, rng, octaves=4, base=8) - 1)
    inland = np.clip((v - thr) / max(np.quantile(v[land], 0.98) - thr, 1e-9), 0, 1) if land.any() else np.zeros(n)
    relief = 0.30 * np.sqrt(inland) + 0.70 * chain * (0.35 + 0.65 * ridge ** 2) * np.clip(inland * 4, 0, 1)
    relief = np.clip(relief / max(np.quantile(relief[land], 0.995), 1e-9), 0, 1) if land.any() else relief
    sea_depth = np.clip((thr - v) / max(thr - v.min(), 1e-9), 0, 1)
    h = np.where(land, SEA_LEVEL + np.round(relief * 80), np.round((1 - sea_depth) * (SEA_LEVEL - 1)))
    return [int(min(100, max(0, x))) for x in h]


def _clean_coast(land: np.ndarray, nbrs: list[list[int]], passes: int = 2) -> np.ndarray:
    """Sink one-cell islets and fill one-cell inlets (a cell is flipped when at most one neighbour agrees with it)."""
    land = land.copy()
    for _ in range(passes):
        flip = [i for i, ns in enumerate(nbrs) if len(ns) >= 3 and sum(land[j] == land[i] for j in ns) <= 1]
        if not flip:
            break
        land[flip] = ~land[flip]
    return land

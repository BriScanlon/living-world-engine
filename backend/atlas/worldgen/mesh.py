"""Voronoi cell mesh in the shape Azgaar's Full JSON uses (``pack.cells{i,v,c,p}``, ``pack.vertices{i,p,c}``).

Points are a jittered hexagonal lattice (even spacing, no grid look), cells are their Voronoi polygons clipped to the
map frame (``shapely.voronoi_polygons``, GEOS), each cell's vertex ring is kept in outline order (the importer draws
polygons from it), shared corners get one vertex id, and two cells are neighbours when they share an edge.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import shapely
from shapely.geometry import MultiPoint, box


@dataclass
class Mesh:
    centers: list[tuple[float, float]]  # cell -> site (x, y) in map px
    cell_v: list[list[int]]             # cell -> vertex ids around its outline
    vpos: list[list[float]]             # vertex -> [x, y]
    vcells: list[list[int]]             # vertex -> cells that share it
    nbrs: list[list[int]]               # cell -> neighbouring cells (shared edge), sorted

    def __len__(self) -> int:
        return len(self.centers)


def lattice(width: float, height: float, spacing: float, rng: np.random.Generator, jitter: float = 0.35) -> np.ndarray:
    """Sites on a hexagonal lattice ``spacing`` px apart, each nudged by up to ``jitter`` x spacing."""
    dy = spacing * math.sqrt(3) / 2
    rows = max(2, int(height / dy))
    cols = max(2, int(width / spacing))
    ys = (np.arange(rows) + 0.5) * (height / rows)
    xs = (np.arange(cols) + 0.5) * (width / cols)
    gx, gy = np.meshgrid(xs, ys)
    gx = gx + (np.arange(rows)[:, None] % 2) * (width / cols) / 2 - (width / cols) / 4
    pts = np.column_stack([gx.ravel(), gy.ravel()])
    pts += rng.uniform(-jitter, jitter, size=pts.shape) * spacing
    pts[:, 0] = np.clip(pts[:, 0], 0.5, width - 0.5)
    pts[:, 1] = np.clip(pts[:, 1], 0.5, height - 0.5)
    return np.round(pts, 2)


def voronoi(width: float, height: float, spacing: float, rng: np.random.Generator) -> Mesh:
    """A Voronoi mesh over ``width`` x ``height`` px with cells about ``spacing`` px across."""
    pts = lattice(width, height, spacing, rng)
    frame = box(0, 0, width, height)
    cells = shapely.get_parts(shapely.voronoi_polygons(MultiPoint(pts), extend_to=frame, ordered=True))
    cells = shapely.intersection(cells, frame)
    vkey: dict[tuple[float, float], int] = {}
    vpos: list[list[float]] = []
    vcells: list[list[int]] = []
    cell_v: list[list[int]] = []
    edges: dict[tuple[int, int], list[int]] = {}
    for i, poly in enumerate(cells):
        if poly.geom_type != "Polygon":  # a sliver clipped into pieces (rare): keep its largest part
            poly = max(shapely.get_parts(poly), key=lambda g: g.area)
        ring: list[int] = []
        for x, y in shapely.orient_polygons(poly).exterior.coords[:-1]:
            k = (round(x, 2), round(y, 2))
            vid = vkey.get(k)
            if vid is None:
                vid = vkey[k] = len(vpos)
                vpos.append([k[0], k[1]])
                vcells.append([])
            if not ring or ring[-1] != vid:
                ring.append(vid)
                vcells[vid].append(i)
        if len(ring) > 1 and ring[0] == ring[-1]:
            ring.pop()
        cell_v.append(ring)
        for a, b in zip(ring, ring[1:] + ring[:1]):
            edges.setdefault((min(a, b), max(a, b)), []).append(i)
    nbrs: list[set[int]] = [set() for _ in cell_v]
    for owners in edges.values():
        if len(owners) == 2:
            a, b = owners
            nbrs[a].add(b)
            nbrs[b].add(a)
    centers = [(float(x), float(y)) for x, y in pts]
    return Mesh(centers=centers, cell_v=cell_v, vpos=vpos, vcells=vcells, nbrs=[sorted(s) for s in nbrs])

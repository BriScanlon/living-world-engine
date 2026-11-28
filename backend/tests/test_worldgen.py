"""Native world generation, phase 1 (#39): a Voronoi mesh, a heightmap with mountain chains, and clean coasts."""
import json
import time
from collections import deque

import numpy as np
import pytest

from atlas.sample_world import LAND_SHAPES, generate
from atlas.worldgen import mesh as wg_mesh
from wizard.builder import REGION_SIZES

MEDIUM = REGION_SIZES["medium"]


def _world(seed=11, shape="continents", **kw):
    return generate(seed, land=shape, climate="temperate", n_states=7, **{**MEDIUM, **kw})


def _landmasses(cells):
    land = [c["h"] >= 20 for c in cells]
    seen, sizes = [False] * len(cells), []
    for i in range(len(cells)):
        if land[i] and not seen[i]:
            q, seen[i], size = deque([i]), True, 0
            while q:
                a = q.popleft()
                size += 1
                for b in cells[a]["c"]:
                    if land[b] and not seen[b]:
                        seen[b] = True
                        q.append(b)
            sizes.append(size)
    return sorted(sizes, reverse=True)


def test_the_mesh_tiles_the_map_and_neighbours_share_edges():
    from shapely.geometry import Polygon
    m = wg_mesh.voronoi(1000, 600, 20, np.random.default_rng(3))
    assert 1300 <= len(m) <= 1700  # (1000 / 20) x (600 / 17.3) = 50 x 34 cells
    area = 0.0
    for i, ring in enumerate(m.cell_v):
        assert len(ring) >= 3 and len(set(ring)) == len(ring)
        poly = Polygon([m.vpos[v] for v in ring])
        assert poly.is_valid
        area += poly.area
        for v in ring:
            assert i in m.vcells[v]
        for j in m.nbrs[i]:
            assert i in m.nbrs[j] and len(set(ring) & set(m.cell_v[j])) >= 2  # a shared edge, both ways
    assert area == pytest.approx(1000 * 600, rel=0.002)  # no gaps, no overlaps


def test_same_seed_same_world_and_hex_worlds_are_untouched():
    assert json.dumps(_world(5)) == json.dumps(_world(5))
    assert json.dumps(_world(5)) != json.dumps(_world(6))
    hexed = generate(5, **{**MEDIUM, "mesh": "hex"})
    assert hexed["info"]["mesh"] == "hex" and _world(5)["info"]["mesh"] == "voronoi"
    assert generate(3)["info"]["mesh"] == "hex"  # the demo planet and every older test stay on the hex grid


@pytest.mark.parametrize("shape", list(LAND_SHAPES))
def test_land_share_matches_the_shape(shape):
    cells = _world(shape=shape)["pack"]["cells"]
    land = sum(c["h"] >= 20 for c in cells) / len(cells)
    assert land == pytest.approx(1 - LAND_SHAPES[shape][2], abs=0.05)


def test_shapes_look_like_their_names():
    for seed in (11, 12):
        arch = _landmasses(_world(seed, "archipelago")["pack"]["cells"])
        assert len(arch) >= 5 and arch[0] / sum(arch) < 0.7  # many islands, none dominating
        pangea = _landmasses(_world(seed, "pangea")["pack"]["cells"])
        assert pangea[0] / sum(pangea) >= 0.84  # one great landmass
    # "continents" is a tendency, not a guarantee: most seeds give two or three separate landmasses
    multi = 0
    for seed in range(11, 17):
        cont = _landmasses(_world(seed, "continents")["pack"]["cells"])
        multi += sum(1 for s in cont if s >= 0.05 * sum(cont)) >= 2
    assert multi >= 4


def test_mountains_form_chains_not_everywhere():
    for shape in ("continents", "pangea", "old-world"):
        cells = _world(shape=shape)["pack"]["cells"]
        land = [c for c in cells if c["h"] >= 20]
        peaks = [c for c in land if c["h"] >= 67]
        assert 0.04 <= len(peaks) / len(land) <= 0.30
        # a chain: most peaks touch another peak (ranges), not isolated cones
        peak_ids = {c["i"] for c in peaks}
        joined = sum(1 for c in peaks if any(j in peak_ids for j in c["c"]))
        assert joined / len(peaks) >= 0.8
        # no one-cell islets or inlets survive the coast clean-up
        land_ids = {c["i"] for c in land}
        lone = [c for c in cells if len(c["c"]) >= 3 and sum((j in land_ids) == (c["i"] in land_ids) for j in c["c"]) == 0]
        assert not lone


def test_a_medium_region_is_quick():
    t = time.perf_counter()
    _world()
    assert time.perf_counter() - t < 3.0

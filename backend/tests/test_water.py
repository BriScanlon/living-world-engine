"""Native world generation, phase 2 (#40): rivers that flow downhill to water, lakes in basins, harbours."""
import pytest

from atlas.sample_world import generate
from atlas.worldgen.water import hydrology
from wizard.builder import REGION_SIZES

SMALL = REGION_SIZES["small"]


def _world(seed, shape="continents", size=SMALL):
    return generate(seed, land=shape, climate="temperate", n_states=5, **size)


def _near_edge(w, cell):
    x, y = cell["p"]
    m = 3 * SMALL["cell_km"] / (SMALL["region_km"][0] / w["info"]["width"])  # ~3 cells from the frame
    return x < m or y < m or x > w["info"]["width"] - m or y > w["info"]["height"] - m


@pytest.mark.parametrize("seed", range(1, 21))
def test_rivers_run_downhill_and_end_on_water(seed):
    shape = ("continents", "pangea", "islands", "archipelago", "old-world")[seed % 5]
    w = _world(seed, shape)
    cells, rivers = w["pack"]["cells"], w["pack"]["rivers"]
    by_id = {r["i"]: r for r in rivers}
    for r in rivers:
        cs = r["cells"]
        land = [c for c in cs if cells[c]["h"] >= 20]
        assert len(land) >= 4  # no stubs
        for a, b in zip(cs, cs[1:]):
            assert b in cells[a]["c"]  # one continuous channel, cell to neighbouring cell
            if cells[b]["h"] >= 20:
                assert cells[b]["h"] <= cells[a]["h"]  # never climbs
        last = cells[cs[-1]]
        if r["parent"]:
            assert r["parent"] in by_id and r["parent"] < r["i"]  # joins a river traced before it
            assert cs[-1] in by_id[r["parent"]]["cells"]
        else:
            assert last["h"] < 20 or _near_edge(w, last)  # reaches the sea, a lake, or leaves the map


def test_lakes_fill_basins_and_are_water():
    found = 0
    for seed in range(1, 9):
        w = _world(seed, "pangea", REGION_SIZES["medium"])
        feats = w["pack"]["features"]
        for c in w["pack"]["cells"]:
            f = feats[c["f"]] if isinstance(feats[c["f"]], dict) else {}
            if f.get("type") == "lake":
                found += 1
                assert c["h"] < 20 and c["biome"] == 0
    assert found > 0  # some worlds have lakes


def test_harbours_are_on_the_coast_and_river_mouths_rank_best():
    w = _world(4)
    cells = w["pack"]["cells"]
    mouths = {r["cells"][-2] for r in w["pack"]["rivers"] if not r["parent"] and cells[r["cells"][-1]]["h"] < 20}
    for c in cells:
        coastal = c["h"] >= 20 and any(cells[j]["h"] < 20 for j in c["c"])
        assert (c["harbor"] > 0) == coastal
        if c["i"] in mouths:
            assert c["harbor"] == 2
    assert sum(c["harbor"] in (1, 2) for c in cells) > 20


def test_a_medium_region_is_well_watered():
    w = _world(3, "pangea", REGION_SIZES["medium"])
    rivers = w["pack"]["rivers"]
    assert 20 <= len(rivers) <= 150 and sum(1 for r in rivers if r["parent"]) >= 3  # networks, with tributaries
    assert max(len(r["cells"]) for r in rivers) >= 15


def test_a_basin_becomes_a_lake_and_a_dimple_is_levelled():
    # a strip of 9 cells: sea | low coast | a 3-wide basin (30) behind a 35 rim | rising ground up to the frame (60)
    h = [10, 22, 35, 30, 30, 30, 36, 50, 60]
    nbrs = [[1]] + [[i - 1, i + 1] for i in range(1, 8)] + [[7]]
    edge = [False] * 8 + [True]
    deep = hydrology(h, nbrs, edge, lake_min_cells=3, lake_min_depth=3)
    assert deep.lakes == [[3, 4, 5]] and all(deep.h[c] < 20 for c in (3, 4, 5))
    shallow = hydrology(h, nbrs, edge, lake_min_cells=4, lake_min_depth=3)  # too small to be a lake: levelled
    assert shallow.lakes == [] and shallow.h[3] == shallow.h[4] == shallow.h[5] >= 35
    for i in range(1, 8):  # every land cell drains, and never uphill
        d = shallow.downhill[i]
        assert d >= 0 and (shallow.h[d] < 20 or shallow.h[d] <= shallow.h[i])


def test_the_importer_draws_the_rivers(tmp_path):
    import json

    from atlas.azgaar import AzgaarWorld
    w = _world(3, "pangea")
    p = tmp_path / "w.json"
    p.write_text(json.dumps(w), encoding="utf-8")
    layers = AzgaarWorld.load(p, "water-test").layers()
    named = [f for f in layers["rivers"]["features"] if f["properties"]["name"]]
    assert len(named) == len(w["pack"]["rivers"])

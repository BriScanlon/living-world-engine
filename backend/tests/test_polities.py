"""Native world generation, phase 4 (#42): cultures, realms, towns and provinces placed by geography."""
import math
import random
import statistics

import numpy as np
import pytest

from atlas.sample_world import PROVINCE_KM2, TOWN_SPACING_KM, generate
from atlas.worldgen import polities as pol
from wizard.builder import REGION_SIZES

MEDIUM = REGION_SIZES["medium"]
CASES = [("continents", 3), ("pangea", 5), ("archipelago", 11), ("islands", 7)]


def _world(shape, seed, **kw):
    return generate(seed, land=shape, climate="temperate", n_states=7, **{**MEDIUM, **kw})


@pytest.mark.parametrize("shape,seed", CASES)
def test_every_land_cell_has_a_realm_and_province_and_every_province_a_town(shape, seed):
    w = _world(shape, seed)
    pk = w["pack"]
    cells, burgs = pk["cells"], pk["burgs"]
    for c in cells:
        if c["h"] >= 20 and c["biome"] != 11:
            assert c["state"] and c["province"], c["i"]  # only glaciers stay wild
    for p in pk["provinces"][1:]:
        town = burgs[p["burg"]]
        assert town and town["cell"] == p["center"] and cells[p["center"]]["province"] == p["i"]
        assert cells[p["center"]]["state"] == p["state"]
    for s in pk["states"][1:]:
        assert burgs[s["capital"]]["capital"] == 1 and cells[burgs[s["capital"]]["cell"]]["state"] == s["i"]


@pytest.mark.parametrize("shape,seed", CASES)
def test_provinces_are_county_sized_and_towns_spaced_within_budget(shape, seed):
    w = _world(shape, seed)
    pk = w["pack"]
    cell_km2 = MEDIUM["region_km"][0] * MEDIUM["region_km"][1] / len(pk["cells"])
    count: dict[int, int] = {}
    for c in pk["cells"]:
        if c["province"]:
            count[c["province"]] = count.get(c["province"], 0) + 1
    areas = [v * cell_km2 for v in count.values()]
    assert 0.5 * PROVINCE_KM2 <= statistics.median(areas) <= 1.4 * PROVINCE_KM2
    assert sum(10_000 <= a <= 40_000 for a in areas) / len(areas) >= 0.65
    burgs = pk["burgs"][1:]
    assert len(burgs) <= MEDIUM["max_towns"] + 5  # a realm short of towns may found one for a province
    km_px = MEDIUM["region_km"][0] / w["info"]["width"]
    xy = [(b["x"], b["y"]) for b in burgs]
    closest = min(math.dist(a, b) for i, a in enumerate(xy) for b in xy[i + 1:]) * km_px
    assert closest >= 0.55 * TOWN_SPACING_KM


def test_towns_sit_where_people_would_live_and_ports_at_harbours():
    w = _world("continents", 3)
    pk = w["pack"]
    cells = pk["cells"]
    land = [c for c in cells if c["h"] >= 20]
    hab = pol.habitability([c["biome"] for c in cells], [c["h"] for c in cells], [c["fl"] for c in cells],
                           [c["harbor"] for c in cells])
    towns = [b["cell"] for b in pk["burgs"][1:]]
    assert np.mean(hab[towns]) > 1.4 * np.mean(hab[[c["i"] for c in land]])
    ports = [b for b in pk["burgs"][1:] if b["port"]]
    landings = {r["points"][i][2] for r in pk["routes"] if r["group"] == "searoutes" for i in (0, -1)}
    # sheltered coves and river mouths only; a landmass with no harbour may get a sea-lane landing on an open coast
    assert ports and all(cells[b["cell"]]["harbor"] in (1, 2) or b["cell"] in landings for b in ports)
    assert all(cells[b["cell"]]["harbor"] for b in ports)  # and every port is on the coast
    assert len(ports) / len(towns) < 0.5  # not every town is on the coast: rivers draw them inland too
    # a people keeps its faith across realm borders
    faith = {}
    for c in land:
        if c["culture"]:
            assert faith.setdefault(c["culture"], c["religion"]) == c["religion"]


def test_borders_cross_rivers_less_than_they_would_by_chance():
    w = _world("pangea", 5)
    cells = w["pack"]["cells"]
    land = [c for c in cells if c["h"] >= 20 and c["state"]]
    fl = np.array([c["fl"] for c in land])
    river = {c["i"] for c in land if c["fl"] >= np.quantile(fl, 0.9)}
    border = [c for c in land if any(cells[j]["h"] >= 20 and cells[j]["state"] and cells[j]["state"] != c["state"]
                                     for j in c["c"])]
    # border cells lie along rivers more often than land cells do in general
    assert sum(c["i"] in river for c in border) / len(border) > 1.2 * len(river) / len(land)


def test_spread_keeps_its_distance_and_expand_respects_walls():
    xy = np.array([[0, 0], [1, 0], [10, 0], [20, 0]], dtype=float)
    assert pol.spread(np.array([1.0, 0.9, 0.8, 0.7]), xy, 3, 5.0) == [0, 2, 3]
    nbrs = [[1], [0, 2], [1, 3], [2]]
    land = [True] * 4
    owner = pol.expand({0: 1, 3: 2}, nbrs, lambda a, b, o: None if b == 2 and o == 1 else 1.0, land)
    assert owner == [1, 1, 2, 2]  # realm 1 can't step onto cell 2, so realm 2 takes it


def test_a_seafaring_world_starts_in_a_real_port(tmp_path, monkeypatch):
    from config import get_settings
    from graph import store as store_mod
    from wizard.builder import build_world
    monkeypatch.setenv("ATLAS_DIR", str(tmp_path / "atlas"))
    monkeypatch.setenv("MOCK_LLM", "true")
    get_settings.cache_clear()
    store_mod._store = None
    root = store_mod.get_store()
    try:
        root.ping()
    except Exception:
        pytest.skip("TerminusDB not reachable")
    res = build_world({"focus": "seafaring", "world": {"name": "Port Test", "seed": random.randrange(10**6), "size": "small"},
                       "history": {"prose": False}})
    try:
        docs = store_mod.GraphStore(root.s, res["campaign"]).all_documents("main")
        start = next(d for d in docs if "campaign-start" in d.get("tags", []))
        assert "port" in start.get("tags", [])
    finally:
        root.delete_campaign(res["campaign"])
        store_mod._store = None
        get_settings.cache_clear()

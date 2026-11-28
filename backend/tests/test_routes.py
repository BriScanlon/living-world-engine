"""Native world generation, phase 5 (#43): roads, trails and sea lanes, and travel that sails the lanes."""
from collections import defaultdict, deque

import pytest

from atlas.sample_world import generate
from atlas.travel import PACE_KM_PER_DAY, SHIP_KM_PER_DAY, RoadNet, haversine_km, route
from wizard.builder import REGION_SIZES

MEDIUM = REGION_SIZES["medium"]
CASES = [("continents", 3), ("pangea", 5), ("archipelago", 11), ("islands", 7)]


def _world(shape, seed):
    return generate(seed, land=shape, climate="temperate", n_states=7, **MEDIUM)


def _network(pk):
    g = defaultdict(set)
    for r in pk["routes"]:
        cs = [p[2] for p in r["points"]]
        for u, v in zip(cs, cs[1:]):
            g[u].add(v)
            g[v].add(u)
    return g


@pytest.mark.parametrize("shape,seed", CASES)
def test_every_town_is_reachable_by_road_or_sea(shape, seed):
    pk = _world(shape, seed)["pack"]
    g = _network(pk)
    towns = [b["cell"] for b in pk["burgs"][1:]]
    seen, q = {towns[0]}, deque([towns[0]])
    while q:
        u = q.popleft()
        for v in g[u]:
            if v not in seen:
                seen.add(v)
                q.append(v)
    assert all(t in seen for t in towns)


@pytest.mark.parametrize("shape,seed", CASES)
def test_roads_keep_to_valleys_and_lanes_to_the_sea(shape, seed):
    pk = _world(shape, seed)["pack"]
    cells = pk["cells"]
    road = {p[2] for r in pk["routes"] if r["group"] != "searoutes" for p in r["points"]}
    land = [c["h"] for c in cells if c["h"] >= 20]
    assert all(cells[c]["h"] >= 20 for c in road)  # roads stay on land
    assert sum(cells[c]["h"] for c in road) / len(road) < sum(land) / len(land)  # lower than the land around them
    assert sum(cells[c]["h"] >= 67 for c in road) / len(road) <= 0.03  # and off the peaks
    groups = {r["group"] for r in pk["routes"]}
    assert {"roads", "trails"} <= groups
    for r in pk["routes"]:
        if r["group"] == "searoutes":
            cs = [p[2] for p in r["points"]]
            assert cells[cs[0]]["h"] >= 20 and cells[cs[-1]]["h"] >= 20  # from port...
            assert all(cells[c]["h"] < 20 for c in cs[1:-1])  # ...over water...
            assert pk["burgs"][cells[cs[0]]["burg"]]["port"] and pk["burgs"][cells[cs[-1]]["burg"]]["port"]  # ...to port


def test_island_worlds_are_tied_together_by_sea():
    pk = _world("archipelago", 11)["pack"]
    cells = pk["cells"]
    lanes = [r for r in pk["routes"] if r["group"] == "searoutes"]
    masses = {cells[b["cell"]]["f"] for b in pk["burgs"][1:]}
    linked = {cells[r["points"][0][2]]["f"] for r in lanes} | {cells[r["points"][-1][2]]["f"] for r in lanes}
    assert len(masses) > 1 and masses <= linked  # every inhabited island has a sea lane


def test_travel_sails_sea_lanes_at_ship_speed():
    # a port, 300 km of sea lane, another port; and a 900 km road round the bay
    net = RoadNet()
    net.nodes = [(0.0, 0.0), (2.7, 0.0), (1.35, 4.0)]
    sea = haversine_km(net.nodes[0], net.nodes[1])
    by_land = haversine_km(net.nodes[0], net.nodes[2]) + haversine_km(net.nodes[2], net.nodes[1])
    net.edges = {0: [(1, sea, True), (2, by_land / 2, False)], 1: [(0, sea, True), (2, by_land / 2, False)],
                 2: [(0, by_land / 2, False), (1, by_land / 2, False)]}
    r = route((0.0, 0.0), (2.7, 0.0), net)
    assert r.sea_km == pytest.approx(sea) and r.road_km == 0
    assert r.days("foot") == pytest.approx(sea / SHIP_KM_PER_DAY, rel=0.01)  # sailing, not walking
    assert r.days("foot") < by_land / PACE_KM_PER_DAY["foot"]
    plain = RoadNet()  # older two-element edges still work (all land)
    plain.nodes, plain.edges = net.nodes, {0: [(1, sea)], 1: [(0, sea)]}
    assert route((0.0, 0.0), (2.7, 0.0), plain).road_km == pytest.approx(sea)

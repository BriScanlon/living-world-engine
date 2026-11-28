"""Generated worlds are a campaign region on the globe, not a planet (issue #38): journeys take days to weeks."""
import json
import math
import statistics
import uuid

import pytest

from atlas.sample_world import PROVINCE_KM2, REGION_LATITUDE, generate
from atlas.travel import haversine_km
from wizard.builder import REGION_SIZES


def _lonlat(w, x, y):
    mc, info = w["mapCoordinates"], w["info"]
    return (mc["lonW"] + x / info["width"] * mc["lonT"], mc["latN"] - y / info["height"] * mc["latT"])


def _towns(w):
    return [_lonlat(w, b["x"], b["y"]) for b in w["pack"]["burgs"][1:]]


@pytest.mark.parametrize("size", ["small", "medium", "large"])
def test_a_region_is_the_size_it_says_and_sits_at_its_latitude(size):
    kw = REGION_SIZES[size]
    w = generate(5, climate="temperate", n_states=7, **kw)
    mc = w["mapCoordinates"]
    wkm, hkm = kw["region_km"]
    # the map's corners are as far apart as the region is wide and tall (within a few percent: a sphere, not a plane)
    assert haversine_km((mc["lonW"], 45), (mc["lonE"], 45)) == pytest.approx(wkm, rel=0.05)
    assert haversine_km((0, mc["latS"]), (0, mc["latN"])) == pytest.approx(hkm, rel=0.01)
    assert (mc["latN"] + mc["latS"]) / 2 == pytest.approx(REGION_LATITUDE["temperate"])
    # provinces are counties, not subcontinents: most within 10-40k km2
    cell_km2 = wkm * hkm / len(w["pack"]["cells"])
    count: dict[int, int] = {}
    for c in w["pack"]["cells"]:
        if c["province"]:
            count[c["province"]] = count.get(c["province"], 0) + 1
    areas = [n * cell_km2 for n in count.values()]
    assert statistics.median(areas) == pytest.approx(PROVINCE_KM2, rel=0.3)
    assert sum(10_000 <= a <= 40_000 for a in areas) / len(areas) >= 0.7  # mostly: provinces on irregular cells vary
    # towns: within the budget, a day or two's walk from each other
    towns = _towns(w)
    assert 0.5 * kw["max_towns"] <= len(towns) <= kw["max_towns"] + 1
    nearest = [min(haversine_km(a, b) for b in towns if b is not a) for a in towns]
    assert 35 <= statistics.median(nearest) <= 90


def test_the_climate_answer_moves_the_region_and_its_biomes():
    north = generate(5, climate="northern", **REGION_SIZES["small"])
    tropic = generate(5, climate="tropical", **REGION_SIZES["small"])
    assert north["mapCoordinates"]["latS"] > 40 and abs(tropic["mapCoordinates"]["latN"]) < 25
    biomes = lambda w: {c["biome"] for c in w["pack"]["cells"] if c["h"] >= 20}  # noqa: E731
    assert biomes(north) & {9, 10, 11} and not biomes(north) & {1, 3, 5, 7}   # taiga, tundra; no desert or jungle
    assert biomes(tropic) & {1, 3, 5, 7} and not biomes(tropic) & {9, 10, 11}


def test_planet_mode_is_unchanged():
    w = generate(3)
    assert w["mapCoordinates"]["lonT"] == 300 and w["mapCoordinates"]["latT"] == 140
    assert len(w["pack"]["cells"]) == 2698 and "planet" in w["info"]["description"]


def test_same_seed_same_region():
    a = generate(9, **REGION_SIZES["small"])
    b = generate(9, **REGION_SIZES["small"])
    assert json.dumps(a) == json.dumps(b)


# ---------------------------------------------------------------------------- journeys, on the real travel model
def test_journeys_in_a_medium_region_take_days_not_months(tmp_path, monkeypatch):
    """The issue's exit test: neighbouring towns 1-4 days on foot, capitals 5-20 days apart (by the atlas's roads)."""
    from atlas.service import import_world
    from atlas.travel import for_world
    from config import Settings, get_settings
    from graph.store import GraphStore
    from graph.world import WorldView
    monkeypatch.setenv("ATLAS_DIR", str(tmp_path / "atlas"))
    get_settings.cache_clear()
    st = GraphStore(Settings(terminusdb_db=f"lwe_test_{uuid.uuid4().hex[:8]}", atlas_dir=str(tmp_path / "atlas"),
                             mock_llm=True))
    try:
        st.ping()
    except Exception:
        pytest.skip("TerminusDB not reachable")
    try:
        st.ensure_database()
        st.apply_schema()
        p = tmp_path / "region.json"
        p.write_text(json.dumps(generate(21, climate="temperate", n_states=7, **REGION_SIZES["medium"])), encoding="utf-8")
        import_world(st, p, slug="region-test")
        w = WorldView(st.all_documents("main"), 0)
        t = for_world(w)
        towns = [l for l in w.of_type("Location") if l.get("lon") is not None and l.get("location_type") in
                 ("town", "capital", "city")]
        caps = [l for l in towns if l.get("location_type") == "capital"]
        assert len(towns) > 100 and len(caps) == 7

        def nearest_days(group, pool, pace="foot"):
            """For each place in group: whole days to its nearest neighbour in pool."""
            out = []
            for a in group:
                b = min((x for x in pool if x is not a), key=lambda x: haversine_km((a["lon"], a["lat"]), (x["lon"], x["lat"])))
                out.append(t.days(a["@id"], b["@id"], pace))
            return out

        sample = towns[::5]  # 40-odd towns is plenty, and keeps the test quick; neighbours come from all towns
        assert 1 <= statistics.median(nearest_days(sample, towns)) <= 4
        cap_days = nearest_days(caps, caps)
        # 5-20 days on one landmass; up to ~25 when neighbouring capitals sit across a strait (no sea lanes until
        # phase 5, #43, so those trips go cross-country or by sea without a route)
        assert 5 <= statistics.median(cap_days) <= 25 and max(cap_days) <= 40
        # an army marching between neighbouring capitals: weeks, not the year-long marches of the old planet
        assert statistics.median(nearest_days(caps, caps, "army")) <= 50  # tighten with sea lanes (phase 5, #43)
    finally:
        st._root().delete_database(st.db, team=st.team, force=True)
        get_settings.cache_clear()

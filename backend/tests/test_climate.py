"""Native world generation, phase 3 (#41): climate and biomes from latitude, height and the prevailing wind."""
import random

import numpy as np
import pytest

from atlas.sample_world import generate
from atlas.worldgen import climate as cl
from wizard.builder import REGION_SIZES

MEDIUM = REGION_SIZES["medium"]
TROPICAL_BIOMES = {1, 3, 5, 7}        # hot desert, savanna, tropical seasonal forest, tropical rainforest
COLD_BIOMES = {9, 10, 11}             # taiga, tundra, glacier


def test_it_is_colder_toward_the_poles_and_up_mountains():
    lat = np.array([0.0, 20.0, 45.0, 70.0])
    sea = cl.temperature(lat, np.full(4, 20.0))
    assert list(sea) == sorted(sea, reverse=True) and sea[0] == pytest.approx(30)
    assert cl.temperature(np.array([45.0]), np.array([20.0]))[0] > cl.temperature(np.array([45.0]), np.array([67.0]))[0]
    # most land is low: the height curve is squared
    assert cl.altitude_km(np.array([45.0]))[0] < 1 and cl.altitude_km(np.array([100.0]))[0] == pytest.approx(5)


def _strip(heights, lat):
    """A west-to-east row of cells at one latitude."""
    centers = [(float(i * 10), 50.0) for i in range(len(heights))]
    nbrs = [[j for j in (i - 1, i + 1) if 0 <= j < len(heights)] for i in range(len(heights))]
    return cl.precipitation(centers, nbrs, list(heights), np.full(len(heights), lat))


def test_mountains_cast_a_rain_shadow_downwind():
    # sea | lowland | a range | lowland, in the westerlies (45 deg): the wind comes from the west
    h = [5, 5, 25, 25, 25, 60, 90, 60, 25, 25, 25, 25]
    rain = _strip(h, 45.0)
    windward, leeward = rain[2:5].mean(), rain[8:12].mean()
    assert rain[5] > windward and windward > leeward * 1.5  # rain on the climb, little beyond the crest
    # in the trade winds (15 deg) the wind comes from the east, so the same range shadows the west instead
    trades = _strip(list(reversed(h)), 15.0)
    assert trades[7:10].mean() > trades[0:4].mean() * 1.5


def test_latitude_shapes_the_rain():
    lat = np.array([0.0, 25.0, 50.0, 80.0])
    r = cl.rain_by_latitude(lat)
    assert r[0] > r[1] < r[2] and r[3] < r[2]  # wet equator, dry subtropics, wet mid-latitudes, dry poles


@pytest.mark.parametrize("seed", [3, 7])
def test_biomes_fit_the_climate(seed):
    def land_biomes(climate, low_only=False):
        cells = generate(seed, land="pangea", climate=climate, n_states=6, **MEDIUM)["pack"]["cells"]
        return {c["biome"] for c in cells if c["h"] >= 20 and (not low_only or c["h"] < 40)}

    north = land_biomes("northern")
    assert north & COLD_BIOMES and not north & TROPICAL_BIOMES  # no jungle or savanna in the north
    tropic = land_biomes("tropical", low_only=True)
    assert tropic & TROPICAL_BIOMES and not tropic & COLD_BIOMES  # lowlands: no taiga or tundra (high peaks may ice)
    temperate = land_biomes("temperate")
    assert temperate & {4, 6, 8}  # grassland and temperate forests


def test_the_rain_reaches_the_rivers():
    w = generate(3, land="pangea", climate="temperate", n_states=6, **MEDIUM)
    cells = w["pack"]["cells"]
    land = [c for c in cells if c["h"] >= 20]
    # with uniform rain every land cell would carry at least 1; dry cells now carry less, wet ones more
    assert min(c["fl"] for c in land) < 0.6 and max(c["fl"] for c in land) > 50 and len(w["pack"]["rivers"]) >= 20


def test_every_biome_has_creatures_for_the_wilds():
    from srd import catalogue
    if not catalogue.monster("Troll"):
        pytest.skip("SRD catalogue not built")
    rng = random.Random(1)
    for biome in range(1, 13):
        assert catalogue.monsters_for_biome(biome, rng, k=2, max_cr=4, min_cr=0), biome

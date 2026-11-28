"""Cartographic maps, phase 1b (#62): coastlines with fractal detail that appears as you zoom in."""
import json

import pytest
import shapely
from shapely.geometry import Point, Polygon, box, shape
from shapely.ops import unary_union

from atlas import cartography as carto
from atlas.azgaar import AzgaarWorld
from atlas.sample_world import generate
from wizard.builder import REGION_SIZES


@pytest.fixture(scope="module")
def region(tmp_path_factory):
    p = tmp_path_factory.mktemp("coast") / "w.json"
    p.write_text(json.dumps(generate(5, land="continents", climate="temperate", n_states=5, **REGION_SIZES["small"])),
                 encoding="utf-8")
    w = AzgaarWorld.load(p, "w")
    return w, w.layers(), w.layers(detail=False)


def test_the_coast_gets_detail_but_keeps_its_shape(region):
    _, detailed, plain = region
    land = shape(detailed["land"]["features"][0]["geometry"])
    old = shape(plain["land"]["features"][0]["geometry"])
    assert detailed["land"]["features"][0]["properties"] == {"detail": "fractal"}
    assert plain["land"]["features"][0]["properties"] == {}
    assert shapely.get_num_coordinates(land) > 20 * shapely.get_num_coordinates(old)  # far more detail...
    assert abs(land.area - old.area) / old.area < 0.03  # ...on the same land
    assert land.symmetric_difference(old).area / old.area < 0.15


def test_towns_stay_on_land_and_ports_reach_the_water(region):
    _, detailed, _ = region
    land = shape(detailed["land"]["features"][0]["geometry"])
    shapely.prepare(land)
    burgs = detailed["burgs"]["features"]
    assert all(land.contains(shape(f["geometry"])) for f in burgs)
    ports = [shape(f["geometry"]) for f in burgs if f["properties"]["port"]]
    assert ports and all(land.boundary.distance(p) * 111 < 0.6 for p in ports)  # within 600 m of the water


def test_provinces_and_biomes_tile_the_detailed_land(region):
    _, detailed, _ = region
    land = shape(detailed["land"]["features"][0]["geometry"])
    for layer in ("provinces", "biomes"):
        cover = unary_union([shape(f["geometry"]) for f in detailed[layer]["features"]])
        assert cover.area == pytest.approx(land.area, rel=0.002)
        assert cover.difference(land).area / land.area < 0.001


def test_rivers_end_on_the_coast(region):
    _, detailed, _ = region
    land = shape(detailed["land"]["features"][0]["geometry"])
    near = land.buffer(1e-4)
    rivers = [shape(f["geometry"]) for f in detailed["rivers"]["features"]]
    assert rivers and all(near.contains(Point(r.coords[-1])) for r in rivers)


def test_detail_is_seeded_by_place():
    sq = box(0, 0, 50, 30)
    a = carto.fractal(sq, seed=4, rough=0.25, min_len=0.5)
    assert a.equals(carto.fractal(sq, seed=4, rough=0.25, min_len=0.5))
    assert not a.equals(carto.fractal(sq, seed=5, rough=0.25, min_len=0.5))
    seg = shapely.get_coordinates(a.boundary)
    assert len(seg) > 200


def test_an_estuary_reaches_an_inland_port_and_stops_short_of_it():
    land = Polygon([(0, 0), (40, 0), (40, 20), (0, 20)])  # the sea is everything south of y = 0
    port = Point(20, 5)
    g = carto.harbours(land, [port], seed=1)
    assert g.contains(port) and g.boundary.distance(port) < 0.6
    assert land.area - g.area < 30  # a narrow inlet, not a bay

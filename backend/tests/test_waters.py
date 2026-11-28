"""Cartographic maps, phase 4a (#57): named seas, gulfs, bays, straits and lakes."""
import json

from shapely.geometry import Point, Polygon, box, shape

from atlas.azgaar import AzgaarWorld
from atlas.party_map import known_waters
from atlas.sample_world import generate
from atlas.waters import find_waters
from wizard.builder import REGION_SIZES


def _land():
    """An island (degrees) with a deep bay cut into its south coast and a lake inside."""
    island = Polygon([(2, 2), (8, 2), (8, 8), (2, 8)])
    bay = Polygon([(4.7, 1.5), (5.3, 1.5), (5.3, 5.0), (4.7, 5.0)])  # narrower than the opening (2R ~ 88 km)
    lake = Polygon([(6.5, 6.5), (7.4, 6.5), (7.4, 7.4), (6.5, 7.4)])
    g = island.difference(bay)
    return Polygon(g.exterior, [lake.exterior])


def test_the_sea_its_bays_and_lakes_are_found_and_named():
    towns = [("Bayford", 5.0, 5.3, False), ("Lakeby", 7.0, 6.2, True), ("Northend", 5.0, 7.8, False)]
    feats = find_waters(_land(), [0, 0, 10, 10], towns=towns, provinces=[("Rizu March", box(2, 2, 8, 8))],
                        map_name="Thessaly Reach")
    by_kind = {}
    for f in feats:
        by_kind.setdefault(f["properties"]["kind"], []).append(f["properties"]["name"])
    assert by_kind["sea"][0] == "The Thessaly Sea"
    assert any(k in by_kind for k in ("bay", "gulf"))
    assert any("Bayford" in n or "Rizu" in n for n in by_kind.get("bay", []) + by_kind.get("gulf", []))
    assert by_kind["lake"] == ["Lake Lakeby"]
    names = [f["properties"]["name"] for f in feats]
    assert len(names) == len(set(names))  # one name each
    land = _land()
    for f in feats:  # every label point is on the water it names, not on land
        at = Point(f["properties"]["label"])
        assert shape(f["geometry"]).buffer(0.05).contains(at) and not land.contains(at)


def test_a_generated_region_has_named_waters(tmp_path):
    p = tmp_path / "w.json"
    p.write_text(json.dumps(generate(5, land="continents", climate="temperate", n_states=5, **REGION_SIZES["small"])),
                 encoding="utf-8")
    layers = AzgaarWorld.load(p, "w").layers()
    feats = layers["waters"]["features"]
    land = shape(layers["land"]["features"][0]["geometry"])
    assert any(f["properties"]["kind"] == "sea" for f in feats)
    assert all(not land.contains(Point(f["properties"]["label"])) for f in feats if f["properties"]["kind"] != "lake")


def test_the_party_names_only_the_waters_it_knows():
    near = {"type": "Feature", "geometry": box(0, 0, 4, 4).__geo_interface__, "properties": {"name": "Near Bay"}}
    far = {"type": "Feature", "geometry": box(20, 20, 24, 24).__geo_interface__, "properties": {"name": "Far Sea"}}
    known = box(3, 3, 8, 8)
    got = known_waters([near, far], known)
    assert [w["properties"]["name"] for w, _ in got] == ["Near Bay"]
    assert got[0][1].within(known.buffer(1e-9))  # lettered on the part the party knows
    assert known_waters([near, far], None) == []

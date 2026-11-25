"""World Atlas: Azgaar import, layers, graph docs, and live map state."""
import json
import uuid

import pytest
from shapely.geometry import shape

from atlas.azgaar import AzgaarWorld
from atlas.sample_world import generate


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    path = tmp_path_factory.mktemp("atlas") / "sample.full.json"
    path.write_text(json.dumps(generate(7)), encoding="utf-8")
    return AzgaarWorld.load(path)


def test_sample_matches_azgaar_full_json_shape(world):
    pack = world.raw["pack"]
    assert isinstance(pack["cells"], list) and isinstance(pack["vertices"], list)
    for key in ("states", "provinces", "burgs", "cultures", "religions", "biomes", "routes", "rivers"):
        assert key in pack
    assert pack["provinces"][0] == 0 and pack["burgs"][0] == 0  # Azgaar keeps index 0 as a placeholder
    cell = pack["cells"][0]
    assert {"v", "c", "p", "h", "biome", "state", "province"} <= set(cell)


def test_projection_matches_azgaar_formula(world):
    mc, info = world.raw["mapCoordinates"], world.raw["info"]
    assert world.lonlat(0, 0) == (mc["lonW"], mc["latN"])
    lon, lat = world.lonlat(info["width"], info["height"])
    assert lon == pytest.approx(mc["lonW"] + mc["lonT"]) and lat == pytest.approx(mc["latN"] - mc["latT"])


def test_layers_dissolve_cells_into_valid_polygons(world):
    layers = world.layers()
    provinces = layers["provinces"]["features"]
    assert len(provinces) == len(world.live(world.raw["pack"]["provinces"]))
    for f in provinces:
        assert shape(f["geometry"]).is_valid
        assert f["properties"]["pid"].startswith(world.slug)
    assert layers["rivers"]["features"] and layers["roads"]["features"] and layers["burgs"]["features"]


def test_graph_docs_make_realms_into_agents(world):
    docs = world.graph_docs(world.layers())
    by_type = {}
    for d in docs:
        by_type.setdefault(d["@type"], []).append(d)
    states = world.live(world.raw["pack"]["states"])
    assert len(by_type["Faction"]) == len(states)
    # every province is controlled by its realm from day 0
    controls = [d for d in by_type["ControlEdge"] if d["target_id"].startswith("Province/")]
    assert len(controls) == len(by_type["Province"])
    # diplomacy became trust in both directions, and rivals want each other's land
    assert by_type["RelationshipEdge"]
    assert all(d["target_id"].startswith(("Province/", "Faction/")) or d.get("target_text")
               for d in by_type["DesireEdge"])
    # places carry coordinates for the globe
    assert all(d.get("lon") is not None and d.get("lat") is not None for d in by_type["Location"])


def test_rejects_non_azgaar_files(tmp_path):
    p = tmp_path / "x.json"
    p.write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError):
        AzgaarWorld.load(p)


# ---------------------------------------------------------------------------- live state (needs TerminusDB)
@pytest.fixture(scope="module")
def store(tmp_path_factory):
    from config import Settings, get_settings
    from graph.seed import seed
    from graph.store import GraphStore

    s = Settings(terminusdb_db=f"lwe_test_{uuid.uuid4().hex[:8]}", mock_llm=True,
                 atlas_dir=str(tmp_path_factory.mktemp("atlasdir")))
    st = GraphStore(s)
    try:
        st.ping()
    except Exception:
        pytest.skip("TerminusDB not reachable")
    get_settings.cache_clear()
    import os
    os.environ["ATLAS_DIR"] = s.atlas_dir
    seed(st, reset=True)
    yield st
    st._root().delete_database(st.db, team=st.team, force=True)
    os.environ.pop("ATLAS_DIR", None)
    get_settings.cache_clear()


def test_import_places_greywater_and_borders_follow_time(store, tmp_path):
    from atlas.service import atlas_state, import_world
    from graph import builders as b

    path = tmp_path / "w.json"
    path.write_text(json.dumps(generate(11)), encoding="utf-8")
    res = import_world(store, path, slug="test-world")
    assert res["greywater"] and res["greywater"]["realm"]

    st = atlas_state(store, "main")
    assert st["atlas"]["slug"] == "test-world"
    town = next(f for f in st["places"]["features"] if f["properties"]["id"] == "Location/greywater")
    assert town["properties"]["campaign"] and len(town["geometry"]["coordinates"]) == 2

    # Hand a province to another realm on a branch, from day 15: the map must follow valid time
    uid, prov = next(iter(st["provinces"].items()))
    other = next(f["id"] for f in st["factions"] if f["id"] != prov["controller"] and f["provinces"])
    store.create_branch("atlas-test")
    edge = next(d for d in store.all_documents("atlas-test", doc_type="ControlEdge")
                if d["target_id"] == prov["id"] and d.get("in_world_end") is None)
    store.upsert([b.end(edge, 15), b.control(other, "CONTROLS", prov["id"], start=15)], "atlas-test", "test: border move")

    before = atlas_state(store, "atlas-test", day=14, compare="main")
    after = atlas_state(store, "atlas-test", day=16, compare="main")
    assert before["provinces"][uid]["controller"] == prov["controller"] and not before["provinces"][uid]["changed"]
    assert after["provinces"][uid]["controller"] == other and after["provinces"][uid]["changed"]
    assert after["changed_provinces"] == 1

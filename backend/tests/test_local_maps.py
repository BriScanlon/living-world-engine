"""Zoom-in maps: a spot on the globe described from the world, painted, and linked where it is."""
import json
import uuid

import pytest


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    import os

    from atlas.sample_world import generate
    from config import Settings, get_settings
    from graph.seed import seed
    from graph.store import GraphStore

    atlas_dir = tmp_path_factory.mktemp("atlas")
    os.environ["ATLAS_DIR"] = str(atlas_dir)
    get_settings.cache_clear()
    st = GraphStore(Settings(terminusdb_db=f"lwe_test_{uuid.uuid4().hex[:8]}", atlas_dir=str(atlas_dir)))
    try:
        st.ping()
    except Exception:
        pytest.skip("TerminusDB not reachable")
    seed(st, reset=True)
    from atlas.service import import_world
    p = atlas_dir / "w.json"
    p.write_text(json.dumps(generate(3)), encoding="utf-8")
    import_world(st, p, slug="local-test")
    yield st
    st._root().delete_database(st.db, team=st.team, force=True)
    os.environ.pop("ATLAS_DIR", None)
    get_settings.cache_clear()


@pytest.fixture
def vtt(monkeypatch):
    from vtt_link import client
    jobs = {}

    def generate_map(description, *, title=None, tags=None, style="", animate=False):
        jid = f"job{len(jobs)}"
        jobs[jid] = {"id": jid, "status": "done", "result_asset_id": f"map-{jid}",
                     "input": {"description": description, "title": title, "tags": tags or []}}
        return jobs[jid]

    monkeypatch.setattr(client, "generate_map", generate_map)
    monkeypatch.setattr(client, "job", lambda j: jobs[j])
    monkeypatch.setattr(client, "get_map", lambda m: {"id": m, "title": "Painted", "width": 3840, "height": 2160,
                                                     "media_type": "image", "thumb_url": f"/media/thumbs/{m}.webp"})
    monkeypatch.setattr(client, "map_view", lambda m: {"metresPerPx": 1.524 / (3840 / 28)})
    return jobs


def _greywater(world):
    from graph.world import WorldView
    w = WorldView(world.all_documents("main"), 10)
    g = w.get("Location/greywater")
    return w, g["lon"], g["lat"]


def test_a_spot_is_described_from_the_world_around_it(world):
    from atlas.local import bearing, brief
    _, lon, lat = _greywater(world)
    near = brief(world, lon, lat - 0.3)  # about 33 km south of Greywater
    assert near["name"] == f"{near['name'].split()[0]} south of Greywater"
    assert "km south of Greywater" in near["description"]
    assert f"In {near['look']['province']['name']}" in near["description"]
    assert near["look"]["on_land"] and near["look"]["province"]
    outskirts = brief(world, lon + 0.005, lat)
    assert outskirts["name"] == "Outskirts of Greywater"
    assert "Lately:" in outskirts["description"]  # the demo's recent events in town
    assert bearing((0, 0), (1, 1)) == "north-east" and bearing((0, 0), (0, -1)) == "south"


def test_painting_a_spot_makes_a_place_and_links_the_map_there(world, vtt):
    from atlas.local import paint_here
    from atlas.service import atlas_state
    from graph.world import WorldView
    from vtt_link.session import collect
    _, lon, lat = _greywater(world)
    spot = (lon + 0.4, lat + 0.2)
    first = paint_here(world, *spot, day=10, name="The Old Watchtower")
    assert "The Old Watchtower" in vtt[first["job"]]["input"]["title"]
    assert "Top-down battle map of The Old Watchtower, a wilderness" in vtt[first["job"]]["input"]["description"]
    linked = collect(world, first["job"])["linked"]
    w = WorldView(world.all_documents("main"), 10)
    place = w.get(first["place"])
    assert place["location_type"] == "wilderness" and "local-map" in place["tags"] and place["province"]
    fp = next(m for m in atlas_state(world, day=10)["maps"] if m["place"] == first["place"])
    cx = sum(c[0] for c in fp["corners"]) / 4
    assert abs(cx - spot[0]) < 1e-3 and 42 < fp["width_m"] < 43.5  # a 140 ft map, right where we zoomed in
    assert linked["day"] == 10

    again = paint_here(world, spot[0] + 0.001, spot[1], day=12)  # the same spot, a few days later: same place
    assert again["place"] == first["place"]
    collect(world, again["job"])
    w12 = WorldView(world.all_documents("main"), 12)
    assert [m["vtt_map_id"] for m in w12.of_type("Map") if m["place"] == first["place"]] == [f"map-{again['job']}"]

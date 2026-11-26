"""World engine <-> virtual tabletop link (with a fake VTT standing in for vtt/)."""
import json
import uuid

import pytest

from atlas.sample_world import generate


class FakeVTT:
    def __init__(self):
        self.pushed: list[str] = []
        self.scenes: list[dict] = []

    def get_map(self, map_id):
        return {"id": map_id, "title": "Tavern Common Room", "width": 3840, "height": 2160, "media_type": "image",
                "thumb_url": f"/media/thumbs/{map_id}.webp", "source": "generated"}

    def map_view(self, map_id):
        return {"mode": "scale", "pxPerSquare": 3840 / 28, "metresPerPx": 1.524 / (3840 / 28)}

    def push_map(self, map_id):
        self.pushed.append(map_id)

    def send_scene(self, scene):
        json.dumps(scene)  # must be serialisable to cross the wire
        self.scenes.append(scene)


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    import os

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
    import_world(st, p, slug="vtt-test")  # places Greywater on the planet
    yield st
    st._root().delete_database(st.db, team=st.team, force=True)
    os.environ.pop("ATLAS_DIR", None)
    get_settings.cache_clear()


@pytest.fixture
def vtt(monkeypatch):
    from vtt_link import client
    fake = FakeVTT()
    for name in ("get_map", "map_view", "push_map", "send_scene"):
        monkeypatch.setattr(client, name, getattr(fake, name))
    return fake


def test_link_map_is_bitemporal_and_true_scale(world, vtt):
    from graph.world import WorldView
    from vtt_link.scene import footprints, link_map, maps_for

    place = "Location/the-drowned-lantern"
    first = link_map(world, place, "map-a", day=10)
    assert abs(first["metres_per_px"] - 1.524 / (3840 / 28)) < 1e-6
    link_map(world, place, "map-b", day=15)  # the tavern is rebuilt: a new map from day 15
    assert [m["vtt_map_id"] for m in maps_for(world, place, day=12)] == ["map-a"]
    assert [m["vtt_map_id"] for m in maps_for(world, place, day=20)] == ["map-b"]
    fp = footprints(WorldView(world.all_documents("main"), 20))
    assert len(fp) == 1 and 42 < fp[0]["width_m"] < 43.5 and len(fp[0]["corners"]) == 4


def test_to_table_pushes_the_current_map_and_a_dm_scene(world, vtt):
    from vtt_link.scene import to_table

    out = to_table(world, "Location/the-drowned-lantern", day=20)
    assert vtt.pushed[-1] == "map-b"
    scene = vtt.scenes[-1]
    assert scene["place"]["name"] == "The Drowned Lantern" and scene["holder"] == "Wenna Tull"
    assert scene["realm"]  # the imported realm whose province holds Greywater
    assert any(p["name"] == "Wenna Tull" and p["wants"] for p in scene["people"])
    assert scene["rumors"] and out["scene"]["people"] >= 1


def test_scene_without_a_map_still_reaches_the_dm(world, vtt):
    from vtt_link.scene import to_table

    out = to_table(world, "Location/vane-manor", day=20)
    assert out["map"] is None and not vtt.pushed
    assert vtt.scenes[-1]["people"][0]["name"] == "Lord Aldric Vane"

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

    # phase 5: painting, tokens and the session log
    jobs: dict
    token_assets: list
    log: list

    def generate_map(self, description, *, title=None, tags=None, style="", animate=False):
        jid = f"job-{len(self.jobs)}"
        self.jobs[jid] = {"id": jid, "kind": "generate", "status": "running", "progress": 0.4,
                          "input": {"description": description, "title": title, "tags": tags or []},
                          "result_asset_id": None}
        return self.jobs[jid]

    def make_token(self, name, description, tags=None):
        jid = f"job-{len(self.jobs)}"
        self.jobs[jid] = {"id": jid, "kind": "token", "status": "queued", "input": {"name": name, "description": description,
                                                                                     "tags": tags or []}}
        self.token_assets.append({"id": f"tok-{jid}", "title": name, "tags": tags or [], "created_at": len(self.jobs),
                                  "thumb_url": f"/media/thumbs/tok-{jid}.webp"})
        return self.jobs[jid]

    def job(self, job_id):
        return self.jobs[job_id]

    def tokens(self):
        return list(self.token_assets)

    def session_log(self):
        return list(self.log)


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
    fake.jobs, fake.token_assets, fake.log = {}, [], []
    for name in ("get_map", "map_view", "push_map", "send_scene", "generate_map", "make_token", "job", "tokens",
                 "session_log"):
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


def test_map_brief_reflects_the_world_on_that_day(world, vtt):
    from graph import builders as b
    from vtt_link.session import map_brief

    place = "Location/the-drowned-lantern"
    world.upsert([b.event("Fire in the cellar", "A fire gutted the Drowned Lantern's cellar", day=21,
                          event_type="npc_action", location=place),
                  b.event("Hidden stash", "Smugglers hid crates under the floor", day=21, event_type="npc_action",
                          location=place, visibility="secret")], "main", "test events")
    before, after = map_brief(world, place, day=20), map_brief(world, place, day=22)
    assert "fire" not in before["description"].lower()
    assert "A fire gutted the Drowned Lantern's cellar" in after["description"]
    assert "Smugglers" not in after["description"]  # secrets never reach the players' table
    assert after["title"] == "The Drowned Lantern, day 22" and len(after["description"]) <= 600
    assert after["tags"] == [f"lwe:{place}", "lwe-day:22", "lwe-branch:main"]


def test_painted_map_links_from_its_day_once(world, vtt):
    from vtt_link.scene import maps_for
    from vtt_link.session import collect, paint_map

    place = "Location/the-drowned-lantern"
    out = paint_map(world, place, day=22)
    assert collect(world, out["job"])["linked"] is None  # still painting
    vtt.jobs[out["job"]].update(status="done", result_asset_id="map-burned")
    first = collect(world, out["job"])
    assert first["linked"]["day"] == 22
    again = collect(world, out["job"])
    assert again["linked"]["map"] == first["linked"]["map"]  # polling twice doesn't link twice
    assert [m["vtt_map_id"] for m in maps_for(world, place, day=21)] == ["map-b"]  # the old map still holds before
    assert [m["vtt_map_id"] for m in maps_for(world, place, day=22)] == ["map-burned"]


def test_tokens_once_per_person_and_portraits_in_the_scene(world, vtt):
    from vtt_link.scene import to_table
    from vtt_link.session import paint_tokens

    place = "Location/the-drowned-lantern"
    first = paint_tokens(world, place, day=22)
    assert any(q["name"] == "Wenna Tull" for q in first["queued"])
    assert "Wenna Tull" in paint_tokens(world, place, day=22)["already"]
    to_table(world, place, day=22)
    wenna = next(p for p in vtt.scenes[-1]["people"] if p["name"] == "Wenna Tull")
    assert wenna["portrait"].startswith("/media/thumbs/tok-") and wenna["portrait_id"].startswith("tok-")


def test_session_log_becomes_the_next_forecast_summary(vtt):
    from vtt_link.session import from_table

    assert from_table()["summary"] == ""
    vtt.log.extend([
        {"id": 1, "kind": "map", "text": "Map on the table: Docks", "meta": {}},
        {"id": 2, "kind": "scene", "text": "Scene: The Docks (day 14)", "meta": {"day": 14}},
        {"id": 3, "kind": "note", "text": "The party torched the Guild warehouse.", "meta": {}},
        {"id": 4, "kind": "scene", "text": "Scene: Vane Manor (day 15, forecast--x)", "meta": {"day": 15}},
    ])
    out = from_table()
    assert out["summary"].splitlines()[1:] == ["- The party went to The Docks.",
                                               "- The party torched the Guild warehouse.",
                                               "- The party went to Vane Manor."]
    assert (out["start_day"], out["end_day"], out["notes"]) == (14, 15, 1)


def test_open_in_vtt_from_the_briefing(world, vtt):
    """Each briefing event knows where it happened and whether a battle map is linked there; opening it puts that
    place on the VTT as it stands on the forecast branch, without marking the party's map."""
    from graph import builders as b
    from graph.briefing import build_briefing
    from vtt_link.scene import link_map, to_table

    br = f"forecast/vtt-brief-{uuid.uuid4().hex[:6]}"
    world.create_branch(br)
    link_map(world, "Location/greywater-docks", "map-docks", branch=br, day=10)
    world.upsert([
        b.event("Smugglers at the docks", "Crates go missing.", day=11, event_type="npc_action",
                actors=["Character/sela-marrow"], location="Location/greywater-docks"),
        b.event("A threat delivered", "A note under the door.", day=12, event_type="npc_action",
                actors=["Character/sela-marrow"], targets=["Character/lord-aldric-vane"]),  # no location: where he is
    ], br, "[forecast] test events")
    brief = build_briefing(world, "main", br)
    by = {e["title"]: e["place"] for e in brief["offscreen"]}
    assert by["Smugglers at the docks"] == {"id": "Location/greywater-docks", "name": "Greywater Docks", "maps": 1}
    assert by["A threat delivered"]["id"] == "Location/vane-manor" and by["A threat delivered"]["maps"] == 0

    before = len(world.all_documents(br))
    out = to_table(world, "Location/greywater-docks", br, 11, mark_visited=False)
    assert out["map"]["vtt_map_id"] == "map-docks" and vtt.pushed[-1] == "map-docks"
    assert vtt.scenes[-1]["branch"] == br and vtt.scenes[-1]["day"] == 11
    assert not out["visited"] and len(world.all_documents(br)) == before  # a preview writes nothing
    world.delete_branch(br)

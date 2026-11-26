"""The atlas's party view: fog of war from what the party knows."""
import json
import uuid

import pytest

from atlas.sample_world import generate


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
    import_world(st, p, slug="party-test")  # places Greywater on the planet
    yield st
    st._root().delete_database(st.db, team=st.team, force=True)
    os.environ.pop("ATLAS_DIR", None)
    get_settings.cache_clear()


def _w(store, day):
    from graph.world import WorldView
    return WorldView(store.all_documents("main"), day)


def _far_place(store):
    """An imported town the party has never heard of."""
    from atlas import party
    w = _w(store, 10)
    pv = party.view(w)
    return next(l for l in w.of_type("Location") if l.get("source_session", "").startswith("import/")
                and l.get("location_type") not in ("continent",) and l["@id"] not in pv.places and l.get("province")
                and l["province"] not in pv.provinces)


def test_the_party_sees_where_it_has_been_and_little_else(world):
    from atlas import party
    w = _w(world, 10)
    pv = party.view(w)
    assert pv.party["name"] == "The Lantern Company"
    assert pv.where == "Location/the-drowned-lantern"  # they arrived at the tavern
    town = w.get("Location/the-drowned-lantern")["parent"]
    assert pv.places["Location/the-drowned-lantern"] == pv.places[town] == party.VISITED
    assert "Event/evt-warehouse-raid" not in pv.events  # the Watch's raid: the party never learned of it
    assert "Event/evt-party-arrives" in pv.events


def test_party_atlas_hides_holders_secrets_and_rumor_accuracy(world):
    from atlas.service import atlas_state
    dm = atlas_state(world, day=10)
    pa = atlas_state(world, day=10, view="party")
    assert pa["view"] == "party" and pa["party"]["where"] == "The Drowned Lantern" and pa["compare"] is None
    seen = [p for p in pa["provinces"].values() if p["known"] == "visited"]
    fog = [p for p in pa["provinces"].values() if p["known"] is None]
    assert len(seen) >= 1 and seen[0]["controller"] and len(fog) > len(seen)
    assert all(p["name"] is None and p["controller"] is None for p in fog)
    assert len(pa["places"]["features"]) < len(dm["places"]["features"])
    assert all(f["properties"]["known"] for f in pa["places"]["features"]
               if f["properties"]["kind"] != "continent")
    assert all(f["properties"]["accuracy"] is None for f in pa["rumors"]["features"])
    assert {f["properties"]["id"] for f in pa["events"]["features"]} < {f["properties"]["id"] for f in dm["events"]["features"]}


def test_marks_are_bitemporal(world):
    from atlas import party
    from atlas.service import place_detail
    far = _far_place(world)
    assert place_detail(world, far["@id"], day=10, view="party") == {}

    assert party.mark(world, far["@id"], "heard", day=11)["changed"]
    assert not party.mark(world, far["@id"], "heard", day=11)["changed"]  # same mark twice: no new commit
    heard = place_detail(world, far["@id"], day=11, view="party")
    assert heard["name"] == far["name"] and heard["description"] is None and heard["controller"] is None
    assert party.view(_w(world, 11)).provinces[far["province"]] == party.HEARD

    party.mark(world, far["@id"], "visited", day=12)
    pv = party.view(_w(world, 12))
    assert pv.places[far["@id"]] == party.VISITED and pv.provinces[far["province"]] == party.VISITED
    assert pv.where == far["@id"]  # the latest place they went

    party.mark(world, far["@id"], "unknown", day=13)
    assert far["@id"] not in party.view(_w(world, 13)).places
    assert far["@id"] not in party.view(_w(world, 10)).places  # before any mark: unknown
    assert party.view(_w(world, 11)).places[far["@id"]] == party.HEARD  # history keeps the earlier fog
    assert party.view(_w(world, 12)).places[far["@id"]] == party.VISITED

    dm = place_detail(world, far["@id"], day=12)
    assert dm["party"]["state"] == party.VISITED and dm["party"]["marked"] == "KNOWS"


def test_sending_a_scene_to_the_table_marks_the_place_visited(world, monkeypatch):
    from atlas import party
    from vtt_link import client
    from vtt_link.scene import to_table
    for name in ("push_map", "send_scene"):
        monkeypatch.setattr(client, name, lambda *a, **k: None)
    monkeypatch.setattr(client, "tokens", lambda: [])
    assert "Location/vane-manor" not in {k for k, v in party.view(_w(world, 14)).places.items() if v == party.VISITED}
    out = to_table(world, "Location/vane-manor", day=14)
    assert out["visited"] is True
    assert party.view(_w(world, 14)).places["Location/vane-manor"] == party.VISITED
    assert to_table(world, "Location/vane-manor", day=14)["visited"] is False  # already been

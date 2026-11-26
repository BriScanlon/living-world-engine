"""Travel times: routes on the planet, and the simulation waiting for people and news to arrive."""
import json
import uuid

import pytest

from atlas.travel import PACE_KM_PER_DAY, RoadNet, Route, haversine_km, route


def test_distances_and_paces():
    assert 110 < haversine_km((0, 0), (0, 1)) < 112  # a degree of latitude
    r = Route(km=PACE_KM_PER_DAY["foot"], road_km=PACE_KM_PER_DAY["foot"], straight_km=30, path=[])
    assert r.days("foot") == pytest.approx(1) and r.days("army") == pytest.approx(2)
    assert r.days("horse") < 1


def test_roads_win_when_they_are_quicker():
    net = RoadNet()
    net.nodes = [(0.0, 0.0), (5.0, 0.0), (10.0, 0.0)]
    net.edges = {0: [(1, haversine_km((0, 0), (5, 0)))], 1: [(0, haversine_km((0, 0), (5, 0))), (2, haversine_km((5, 0), (10, 0)))],
                 2: [(1, haversine_km((5, 0), (10, 0)))]}
    on_road = route((0.0, 0.1), (10.0, 0.1), net)
    assert on_road.road_km > 1000 and len(on_road.path) == 5
    cross = route((0.0, 0.1), (10.0, 0.1), RoadNet())
    assert on_road.days("foot") < cross.days("foot") / 1.5  # off-road is half pace over a winding path
    assert route((0.0, 0.0), (0.0, 0.0), net).km == 0


# --------------------------------------------------------------------------- in the simulation
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
    st = GraphStore(Settings(terminusdb_db=f"lwe_test_{uuid.uuid4().hex[:8]}", atlas_dir=str(atlas_dir), mock_llm=True))
    try:
        st.ping()
    except Exception:
        pytest.skip("TerminusDB not reachable")
    seed(st, reset=True)
    from atlas.service import import_world
    p = atlas_dir / "w.json"
    p.write_text(json.dumps(generate(3)), encoding="utf-8")
    import_world(st, p, slug="travel-test")
    yield st
    st._root().delete_database(st.db, team=st.team, force=True)
    os.environ.pop("ATLAS_DIR", None)
    get_settings.cache_clear()


def _far_town(w, home: str):
    from atlas.travel import for_world
    t = for_world(w)
    towns = [l for l in w.of_type("Location") if l.get("source_session", "").startswith("import/")
             and l.get("location_type") in ("town", "capital", "city")]
    far = [l for l in towns if t.days(l["@id"], home, "foot") >= 8]  # far, but not the other side of the planet
    return min(far, key=lambda l: t.days(l["@id"], home, "foot"))


def test_colocated_places_are_no_journey(world):
    from atlas.travel import for_world
    from graph.world import WorldView
    w = WorldView(world.all_documents("main"), 10)
    assert for_world(w).days("Location/the-drowned-lantern", "Location/vane-manor", "foot") == 0


def test_far_actors_and_far_taverns_wait_for_the_road(world, monkeypatch):
    from graph import builders as b
    from graph.world import WorldView
    from llm import mock
    from simulation.engine import branch_name, run_sync
    from simulation.models import ArbiterResult, ForecastRequest, RumorBatch, RumorDraft, WorldEventDraft
    from simulation.runs import registry

    w = WorldView(world.all_documents("main"), 10)
    far = _far_town(w, "Location/greywater")
    brand = next(c for c in w.of_type("Character") if c["name"] == "Captain Hollis Brand")
    world.upsert([{**brand, "location": far["@id"]}], "main", "test: Brand is far away")

    def arbiter(intents, world_view, *, day, seed, round_no):
        if round_no:
            return ArbiterResult(events=[])
        sela = next(c for c in world_view.of_type("Character") if c["name"] == "Sela Marrow")
        return ArbiterResult(events=[WorldEventDraft(
            title="Fire at the Guild warehouse", summary="A warehouse on Greywater's docks burns.",
            event_type="npc_action", day=day, actor_ids=[sela["@id"]], target_ids=["Location/greywater-docks"],
            location_id="Location/greywater-docks", visibility="public", witnesses=[sela["@id"]]), WorldEventDraft(
            title="Brand storms the docks", summary="Captain Brand leads the watch against the Guild's docks.",
            event_type="npc_action", day=day, actor_ids=[brand["@id"]], target_ids=["Location/greywater-docks"],
            location_id="Location/greywater-docks", visibility="public", witnesses=[brand["@id"]])])

    def rumors(events, world_view, *, seed, round_no):
        return RumorBatch(rumors=[RumorDraft(about_event=e["@id"], location_id=far["@id"], text="Trouble at Greywater.",
                                             dialogue="\"Trouble down at Greywater, they say.\"", accuracy="True",
                                             speaker=None) for e in events if e["name"] == "Fire at the Guild warehouse"])

    monkeypatch.setattr(mock, "arbiter", arbiter)
    monkeypatch.setattr(mock, "rumors", rumors)
    req = ForecastRequest(session_number=21, start_day=10, end_day=11, forecast_days=7, session_summary="The party rested.")
    run = registry.create(branch_name(21), req.model_dump())
    final = run_sync(run, store=world, settings=world.s)
    branch = final["branch"]

    fw = WorldView(world.all_documents(branch), 10_000)
    from atlas.travel import for_world
    trip = for_world(fw).days(far["@id"], "Location/greywater-docks", "foot")
    assert trip > 7  # the far town really is far
    storm = next(e for e in fw.of_type("Event") if e["name"] == "Brand storms the docks")
    assert storm["in_world_start"] >= 11 + trip  # set out at the start of the round, then walked
    assert "journey of" in storm["summary"]
    # news of a fire on the docks reaches the far town only after riders could carry it there
    fire = next(e for e in fw.of_type("Event") if e["name"] == "Fire at the Guild warehouse")
    assert "journey" not in fire["summary"]  # Sela was already at the docks
    news = next(r for r in fw.of_type("Rumor") if r.get("about_event") == fire["@id"])
    lag = for_world(fw).days("Location/greywater-docks", far["@id"], "rumor")
    assert lag >= 5 and news["in_world_start"] == fire["in_world_start"] + lag
    # Brand is in the far town: he believes it only once the rumor gets there
    heard = [e for e in fw.of_type("EpistemicEdge") if e["agent_id"] == brand["@id"] and e["fact_or_event_id"] == fire["@id"]]
    assert heard and min(e["in_world_start"] for e in heard) == news["in_world_start"]


def test_water_is_crossed_by_ship():
    from shapely.geometry import box
    from shapely.prepared import prep
    two_islands = prep(box(0, -1, 2, 1).union(box(18, -1, 20, 1)))
    r = route((1.0, 0.0), (19.0, 0.0), None, two_islands)
    assert 1500 < r.sea_km < 1900
    from atlas.travel import SHIP_KM_PER_DAY
    land_legs = r.days("foot") - r.sea_km / SHIP_KM_PER_DAY
    assert r.days("army") == pytest.approx(r.sea_km / SHIP_KM_PER_DAY + 2 * land_legs)  # same ship, slower on land
    walking = route((1.0, 0.0), (19.0, 0.0))  # no coastline known: all overland
    assert r.days("foot") < walking.days("foot") / 2

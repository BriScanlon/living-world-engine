"""What the arbiter's events may change: goal clocks can't hand over what wasn't won (#30)."""
import uuid

import pytest
from graph import builders as b
from graph.world import WorldView
from simulation.engine import capped_goal_progress, side_of
from simulation.models import ControlChange, WorldEventDraft
from simulation.mutations import Mutator


def _world():
    keep = b.entity("Location", "Elivel Keep", location_type="town")
    home = b.entity("Location", "Torori Hold", location_type="capital")
    torori = b.entity("Faction", "Torori Empire", resources=60, influence=40, headquarters=home["@id"])
    ithion = b.entity("Faction", "Principality of Ithion", resources=40, influence=30, headquarters=keep["@id"])
    general = b.entity("Character", "General Vash", kind="npc", faction=torori["@id"], location=home["@id"])
    conquer = b.goal(torori["@id"], "Conquer Elivel", priority=0.9, segments=4, progress=2, target=keep["@id"])
    gossip = b.goal(torori["@id"], "Shame Ithion", priority=0.9, segments=4, progress=2, target=ithion["@id"])
    docs = [keep, home, torori, ithion, general, conquer, gossip,
            b.control(ithion["@id"], "CONTROLS", keep["@id"]), b.control(torori["@id"], "CONTROLS", home["@id"])]
    return WorldView(docs, 10), {d["name"]: d["@id"] for d in docs if d.get("name")}


def _draft(actor, *, goal=None, amount=0, target=None, event_type="npc_action", wins_for=None, **kw):
    return WorldEventDraft(title="t", summary="s", event_type=event_type, day=12, actor_ids=[actor],
                           target_ids=[target] if target else [], goal_progress={goal: amount} if goal else {},
                           control_changes=[ControlChange(target_id=target, new_controller_id=wins_for)] if wins_for else [],
                           **kw)


# ------------------------------------------------------------------------------------------------ #30 goal clocks
def test_time_alone_never_finishes_a_conquest():
    w, ids = _world()
    m = Mutator(w, "test", "s")
    tick = _draft(ids["Torori Empire"], goal=ids["Conquer Elivel"], amount=5, target=ids["Elivel Keep"],
                  event_type="goal_progress")
    assert capped_goal_progress(w, m, tick) == {ids["Conquer Elivel"]: 1}  # 2/4 -> 3/4, never 4/4
    m.bump_goal(ids["Conquer Elivel"], 1, 12, "Event/x")
    assert capped_goal_progress(w, m, tick) == {}  # one step short: only a victory can finish it


def test_a_lost_battle_cannot_finish_it_but_a_won_one_does():
    w, ids = _world()
    m = Mutator(w, "test", "s")
    goal, keep, torori = ids["Conquer Elivel"], ids["Elivel Keep"], ids["Torori Empire"]
    stalemate = _draft(torori, goal=goal, amount=2, target=keep)  # the model awarded progress but no control change
    assert capped_goal_progress(w, m, stalemate) == {goal: 1}
    won = _draft(torori, goal=goal, amount=2, target=keep, wins_for=torori)
    assert capped_goal_progress(w, m, won) == {goal: 2}
    # a member's win counts for the faction
    by_general = _draft(ids["General Vash"], goal=goal, amount=2, target=keep, wins_for=ids["General Vash"])
    assert capped_goal_progress(w, m, by_general) == {goal: 2}


def test_held_targets_and_other_goals_are_not_capped():
    w, ids = _world()
    m = Mutator(w, "test", "s")
    torori, keep = ids["Torori Empire"], ids["Elivel Keep"]
    m.set_control(keep, torori, "CONTROLS", 11, "Event/taken")  # won earlier this round
    tick = _draft(torori, goal=ids["Conquer Elivel"], amount=5, target=keep, event_type="goal_progress")
    assert capped_goal_progress(w, m, tick) == {ids["Conquer Elivel"]: 5}
    shame = _draft(torori, goal=ids["Shame Ithion"], amount=5, target=ids["Principality of Ithion"])
    assert capped_goal_progress(w, m, shame) == {ids["Shame Ithion"]: 5}  # not a place or item: time may finish it


def test_side_of():
    w, ids = _world()
    assert side_of(w, ids["General Vash"]) == ids["Torori Empire"]
    assert side_of(w, ids["Torori Empire"]) == ids["Torori Empire"]
    assert side_of(w, None) is None


# ------------------------------------------------------------------------------------------------ whole forecasts
@pytest.fixture
def store(tmp_path, monkeypatch):
    from config import Settings
    from graph.seed import seed
    from graph.store import GraphStore
    from simulation.runs import registry
    st = GraphStore(Settings(terminusdb_db=f"lwe_test_{uuid.uuid4().hex[:8]}", mock_llm=True))
    try:
        st.ping()
    except Exception:
        pytest.skip("TerminusDB not reachable")
    seed(st, reset=True)
    monkeypatch.setattr(registry, "_dir", tmp_path)
    registry.reload()
    yield st
    st._root().delete_database(st.db, team=st.team, force=True)


def _forecast(st, monkeypatch, events_for_round):
    """Run a mock forecast whose arbiter returns ``events_for_round(day)`` in round 0 only, and return the branch's
    documents. 'Seize the Docks' (House Vane; the Ashen Guild holds the docks) starts one segment short."""
    from llm import mock
    from simulation.engine import branch_name, run_sync
    from simulation.models import ArbiterResult, ForecastRequest
    from simulation.runs import registry
    g = WorldView(st.all_documents("main"), 10).get("Goal/seize-the-docks")
    st.upsert([{**g, "clock_progress": g["clock_segments"] - 1, "ticks_every_days": 1}], "main", "test: nearly there")
    monkeypatch.setattr(mock, "arbiter", lambda intents, wv, *, day, seed, round_no:
                        ArbiterResult(events=events_for_round(day) if round_no == 0 else []))
    req = ForecastRequest(session_number=31, start_day=10, end_day=11, forecast_days=3, session_summary="Quiet.")
    final = run_sync(registry.create(branch_name(31), req.model_dump()), store=st, settings=st.s)
    return st.all_documents(final["branch"])


def _goal(docs):
    return next(d for d in docs if d["@id"] == "Goal/seize-the-docks")


def test_a_forecast_never_hands_over_the_docks_by_clock_alone(store, monkeypatch):
    """#30's Elivel case on the demo world: the clock fills with time, nobody wins a fight, the docks stay put."""
    docs = _forecast(store, monkeypatch, lambda day: [])
    assert WorldView(docs, 10_000).controller_of("Location/greywater-docks") == "Faction/the-ashen-guild"
    g = _goal(docs)
    assert g["status"] == "active" and g["clock_progress"] == g["clock_segments"] - 1
    assert not [d for d in docs if d.get("@type") == "Event" and d["event_type"] == "goal_achieved"
                and "Seize the Docks" in d["name"]]
    # and no empty "quietly moves it forward" events for a clock that can't move
    assert not [d for d in docs if d.get("@type") == "Event" and d["event_type"] == "goal_progress"
                and "Seize the Docks" in d["name"] and (d.get("in_world_start") or 0) > 10]


def test_a_won_seizure_finishes_the_goal_and_holds_the_docks(store, monkeypatch):
    docs = _forecast(store, monkeypatch, lambda day: [WorldEventDraft(
        title="House Vane seizes the docks", summary="Vane's men drive the Guild off the quays.", event_type="conflict",
        day=day, actor_ids=["Faction/house-vane"], target_ids=["Location/greywater-docks"],
        location_id="Location/greywater-docks", goal_progress={"Goal/seize-the-docks": 1},
        control_changes=[ControlChange(target_id="Location/greywater-docks", new_controller_id="Faction/house-vane")])])
    assert WorldView(docs, 10_000).controller_of("Location/greywater-docks") == "Faction/house-vane"
    assert _goal(docs)["status"] == "achieved"
    assert any(d.get("@type") == "Event" and d["event_type"] == "goal_achieved" and "Seize the Docks" in d["name"]
               for d in docs)
    # the docks changed hands once, in the battle, not a second time when the goal completed
    taken = [d for d in docs if d.get("@type") == "ControlEdge" and d["target_id"] == "Location/greywater-docks"
             and d["from_id"] == "Faction/house-vane"]
    assert len(taken) == 1

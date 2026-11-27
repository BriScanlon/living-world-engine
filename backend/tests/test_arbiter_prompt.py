"""The arbiter fits a 16K context: compact prompts, lean intents, related intents grouped into small calls."""
import json

from simulation import prompts


class StubWorld:
    """Just enough WorldView for grouping and the arbiter prompt."""

    def __init__(self, controllers=None, docs=None):
        self.controllers = controllers or {}
        self.docs = docs or {}

    def controller_of(self, i):
        return self.controllers.get(i)

    def of_type(self, t):
        return []

    def get(self, i):
        return self.docs.get(i)

    def members_of(self, faction_id):
        return []

    @property
    def all(self):
        return self.docs


def _intent(agent, target=None, reasoning="Because the docks are mine by right.", **kw):
    return {"agent_id": agent, "agent_name": agent.split("/")[-1], "action": "seize", "target_id": target,
            "target_name": target and target.split("/")[-1], "resources_spent": 10, "reasoning": reasoning,
            "public_or_secret": "secret", "responding_to": None, "advances_goal_id": None, "round": 0, **kw}


def test_payloads_are_compact_and_intents_lean():
    w = StubWorld(docs={"Character/vane": {"@id": "Character/vane", "@type": "Character", "name": "Vane",
                                           "faction": None, "role": "Baron"}})
    long = "x" * 900
    text = prompts.arbiter_user([_intent("Character/vane", "Location/docks", reasoning=long)], w, 13)
    assert "\n" not in text and ": " not in text  # no indentation, no spaces after separators
    sent = json.loads(text)["intents"][0]
    assert "responding_to" not in sent and "advances_goal_id" not in sent and "round" not in sent
    assert len(sent["reasoning"]) == prompts.REASONING_CHARS and sent["reasoning"].endswith("…")
    entity = json.loads(text)["world"]["entities"][0]
    assert "@type" not in entity and "faction" not in entity  # implied by the id / empty


def test_related_intents_share_a_call_and_calls_stay_small():
    w = StubWorld(controllers={"Location/warehouse": "Faction/guild"})
    clash = [_intent("Faction/vane", "Location/docks"), _intent("Faction/guild-thieves", "Location/docks")]
    owner = [_intent("Character/thief", "Location/warehouse"), _intent("Faction/guild", "Location/market")]
    loners = [_intent(f"Character/npc{i}", f"Location/spot{i}") for i in range(7)]
    calls = prompts.arbiter_groups(clash + owner + loners, w)
    assert all(len(c) <= prompts.MAX_GROUP for c in calls)
    assert sum(len(c) for c in calls) == 11 and len(calls) == 2  # 11 intents, packed into two calls
    where = {i["agent_id"]: n for n, c in enumerate(calls) for i in c}
    assert where["Faction/vane"] == where["Faction/guild-thieves"]  # same target
    assert where["Character/thief"] == where["Faction/guild"]  # the target's controller


def test_a_crowded_clash_is_chunked():
    w = StubWorld()
    calls = prompts.arbiter_groups([_intent(f"Character/a{i}", "Location/docks") for i in range(8)], w)
    assert sorted(len(c) for c in calls) == [2, 6]


def test_schema_outline_replaces_the_schema_in_json_schema_mode(monkeypatch):
    from types import SimpleNamespace

    from config import get_settings
    from llm.client import LLMClient, schema_outline
    from simulation.models import ArbiterResult

    outline = schema_outline(ArbiterResult.model_json_schema())
    assert outline.startswith("{events: [{title: string") and "faction_move|npc_action" in outline
    assert len(outline) < len(json.dumps(ArbiterResult.model_json_schema())) / 3

    seen = {}

    def create(**kw):
        seen["system"] = kw["messages"][0]["content"]
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content='{"events": []}'),
                                                        finish_reason="stop")],
                               usage=SimpleNamespace(completion_tokens=5, prompt_tokens=50))

    for mode, expect_schema in (("json_schema", False), ("json_object", True)):
        monkeypatch.setenv("LLM_JSON_MODE", mode)
        get_settings.cache_clear()
        c = LLMClient()
        c.client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
        c.structured("Resolve.", "{}", ArbiterResult)
        assert ("$defs" in seen["system"]) is expect_schema
        assert ("shaped: {events:" in seen["system"]) is not expect_schema
    get_settings.cache_clear()


def test_a_real_model_forecast_calls_the_arbiter_per_group(tmp_path, monkeypatch):
    """A whole non-mock forecast with a fake model: the arbiter is called once per group, never with more than
    MAX_GROUP intents, and every intent is resolved."""
    import uuid

    import pytest

    from config import Settings
    from graph.seed import DEMO_SESSION_SUMMARY, seed
    from graph.store import GraphStore
    from llm import client as llm_client
    from simulation.engine import branch_name, run_sync
    from simulation.models import (AgentIntentLLM, ArbiterResult, ForecastRequest, RumorBatch, SessionDelta,
                                   WorldEventDraft)
    from simulation.runs import registry

    st = GraphStore(Settings(terminusdb_db=f"lwe_test_{uuid.uuid4().hex[:8]}", mock_llm=False, llm_concurrency=2))
    try:
        st.ping()
    except Exception:
        pytest.skip("TerminusDB not reachable")
    seed(st, reset=True)
    monkeypatch.setattr(registry, "_dir", tmp_path)
    registry.reload()
    arbiter_calls = []

    class FakeLLM:
        def structured(self, system, user, schema, **kw):
            if schema is SessionDelta:
                return SessionDelta()
            if schema is AgentIntentLLM:
                you = json.loads(user)["you"]
                target = next((k["id"] for k in you.get("known_world", []) if k["id"].startswith("Location/")), None)
                return AgentIntentLLM(action="observe", target_id=target, reasoning="Watching the town.")
            if schema is ArbiterResult:
                group = json.loads(user)["intents"]
                arbiter_calls.append(len(group))
                return ArbiterResult(events=[WorldEventDraft(title=f"{i['agent_name']} watches", summary="Quietly.",
                                                             day=json.loads(user)["day"], actor_ids=[i["agent_id"]],
                                                             visibility="secret") for i in group])
            return RumorBatch(rumors=[])

    monkeypatch.setattr(llm_client, "get_llm", lambda: FakeLLM())
    monkeypatch.setattr(prompts, "MAX_GROUP", 1)  # an empty session wakes few NPCs: one intent per call shows the split
    try:
        req = ForecastRequest(session_number=50, start_day=10, end_day=11, forecast_days=3,
                              session_summary=DEMO_SESSION_SUMMARY)
        run = registry.create(branch_name(50), req.model_dump(), campaign=st.db)
        final = run_sync(run, store=st, settings=st.s)
        intents = len(final["all_intents"])
        assert run.status == "complete" and intents >= 2
        assert len(arbiter_calls) == intents and max(arbiter_calls) == 1  # one call per (one-intent) group
        assert sum(arbiter_calls) == intents  # every intent reached the arbiter exactly once
        assert sum(1 for r in final["event_reports"] if r["title"].endswith("watches")) == intents
    finally:
        st._root().delete_database(st.db, team=st.team, force=True)
        registry.reload()

"""End-to-end against a live TerminusDB (skipped if unreachable).

Uses a throwaway database so it never touches your campaign.
"""
import uuid

import pytest

from config import Settings
from graph.briefing import build_briefing
from graph.seed import DEMO_SESSION_SUMMARY, seed
from graph.store import GraphStore
from graph.world import WorldView
from ingestion.pipeline import Source, ingest_source
from simulation.engine import branch_name, run_sync
from simulation.models import ForecastRequest
from simulation.runs import registry


@pytest.fixture(scope="module")
def store():
    s = Settings(terminusdb_db=f"lwe_test_{uuid.uuid4().hex[:8]}", mock_llm=True)
    st = GraphStore(s)
    try:
        st.ping()
    except Exception:
        pytest.skip("TerminusDB not reachable")
    seed(st, reset=True)
    yield st
    st._root().delete_database(st.db, team=st.team, force=True)


def _forecast(store, session=14):
    req = ForecastRequest(session_number=session, start_day=10, end_day=11, forecast_days=7,
                          session_summary=DEMO_SESSION_SUMMARY)
    run = registry.create(branch_name(session), req.model_dump())
    final = run_sync(run, store=store, settings=store.s)
    return run, final


def test_forecast_branch_diff_cherry_pick_merge(store):
    run, final = _forecast(store)
    assert run.status == "complete"
    assert store.branch_exists(run.branch)
    # main untouched by the simulation
    main_before = {d["@id"] for d in store.all_documents("main")}
    b = build_briefing(store, "main", run.branch)
    assert len(b["ripples"]) == 4  # the four sentences of the demo summary
    assert b["offscreen"] and b["rumors"]
    vane = next(r for r in b["shifts"]["relationships"]
                if r["from"]["id"] == "Character/lord-aldric-vane" and r["to"]["id"] == "Faction/the-lantern-company")
    assert vane["after"] < vane["before"]
    # bi-temporal: superseded edges are ended, not deleted
    fw = WorldView(store.all_documents(run.branch))
    ended = [d for d in fw.all.values() if d["@type"] == "RelationshipEdge" and d.get("in_world_end") is not None]
    assert ended and all(d.get("ended_by_event") for d in ended)
    # time travel: day 10 on the forecast branch has no forecast events
    early = WorldView(store.all_documents(run.branch), day=9)
    assert not [e for e in early.of_type("Event") if e.get("source_session", "").startswith("forecast/")]

    # cherry-pick one event
    ev = b["offscreen"][0]["id"]
    res = store.cherry_pick(run.branch, [ev])
    assert ev in res["picked"]
    assert ev in {d["@id"] for d in store.all_documents("main")}

    # merge the rest (main has diverged -> 3-way apply)
    out = store.merge(run.branch, "main", base_commit=b["run"]["base_commit"])
    assert out["strategy"] in ("apply-patch", "document-3way")
    after = build_briefing(store, "main", run.branch)
    assert after["stats"]["added"] == 0 and after["stats"]["changed"] == 0
    assert main_before < {d["@id"] for d in store.all_documents("main")}


def test_forecast_is_deterministic_in_mock_mode(store):
    _, a = _forecast(store, session=20)
    _, b = _forecast(store, session=20)
    assert [e["title"] for e in a["event_reports"]] == [e["title"] for e in b["event_reports"]]


def test_markdown_ingestion_with_entity_resolution(store):
    store.create_branch("ingest-test")
    note = ("---\ntype: npc\nfaction: \"[[The Ashen Guild]]\"\nwants: [\"[[Customs Seal of Greywater]]\"]\n---\n"
            "Tamsin Reed fears The Baron. Tamsin Reed knows about [[The Sunken Ledger]].")
    r = ingest_source(store, Source(name="Tamsin Reed.md", kind="markdown", text=note), "ingest-test", None)
    assert any(c["id"] == "Character/tamsin-reed" for c in r["created"])
    w = WorldView(store.all_documents("ingest-test"))
    fears = [d for d in w.of_type("DesireEdge") if d["from_id"] == "Character/tamsin-reed" and d["kind"] == "FEARS"]
    assert fears and fears[0]["target_id"] == "Character/lord-aldric-vane"  # "The Baron" resolved
    assert w.get("Character/tamsin-reed")["faction"] == "Faction/the-ashen-guild"

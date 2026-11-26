"""Forecast runs survive a restart: records and checkpoints on disk, interrupted runs resume where they stopped."""
import json
import uuid

import pytest

from config import Settings
from graph.seed import DEMO_SESSION_SUMMARY, seed
from graph.store import GraphStore
from graph.world import WorldView
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


@pytest.fixture
def runs_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(registry, "_dir", tmp_path)
    registry.reload()
    yield tmp_path
    registry.reload()


def _req(session):
    return ForecastRequest(session_number=session, start_day=10, end_day=11, forecast_days=7,
                           session_summary=DEMO_SESSION_SUMMARY, seed=7)


def _counts(store, branch):
    w = WorldView(store.all_documents(branch))
    return len(w.of_type("Event")), len(w.of_type("Rumor"))


def test_runs_are_saved_and_reloaded(store, runs_dir):
    run = registry.create(branch_name(31), _req(31).model_dump(), campaign=store.db)
    run_sync(run, store=store, settings=store.s)
    saved = json.loads((runs_dir / f"{run.id}.json").read_text(encoding="utf-8"))
    assert saved["status"] == "complete" and saved["campaign"] == store.db
    assert saved["checkpoint"]["next"] is None  # finalize was the last step
    registry.reload()  # as a new process would
    again = registry.get(run.id)
    assert again.status == "complete" and again.result == run.result and not again.resumable
    assert [e["kind"] for e in again.events][-1] == "complete"


def test_an_interrupted_run_resumes_where_it_stopped(store, runs_dir, monkeypatch):
    from llm import mock
    # the reference: the same request, straight through
    ref = registry.create(branch_name(32), _req(32).model_dump(), campaign=store.db)
    run_sync(ref, store=store, settings=store.s)

    real_rumors = mock.rumors
    crashed = {"done": False}

    def flaky(events, world, *, seed, round_no):
        if round_no == 1 and not crashed["done"]:
            crashed["done"] = True
            raise RuntimeError("power cut")
        return real_rumors(events, world, seed=seed, round_no=round_no)

    monkeypatch.setattr(mock, "rumors", flaky)
    run = registry.create(branch_name(33), _req(32).model_dump(), campaign=store.db)
    with pytest.raises(RuntimeError):
        run_sync(run, store=store, settings=store.s)
    assert run.status == "failed" and run.next_step == "propagate_rumors" and run.resumable

    # the process dies with the run marked running; a new process finds it interrupted
    path = runs_dir / f"{run.id}.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["status"] = "running"
    path.write_text(json.dumps(data), encoding="utf-8")
    registry.reload()
    run = registry.get(run.id)
    assert run.status == "interrupted" and run.resumable and "propagate_rumors" in run.events[-1]["message"]

    final = run_sync(run, store=store, settings=store.s, resume=True)
    assert run.status == "complete" and any(e["kind"] == "resume" for e in run.events)
    assert run.result["events"] == ref.result["events"] and run.result["rumors"] == ref.result["rumors"]
    assert _counts(store, run.branch) == _counts(store, ref.branch)  # nothing written twice
    assert final["round"] == len(final["rounds"])


def test_finished_runs_cannot_be_resumed(store, runs_dir):
    run = registry.create(branch_name(34), _req(34).model_dump(), campaign=store.db)
    run_sync(run, store=store, settings=store.s)
    from simulation.engine import resume_forecast
    with pytest.raises(ValueError):
        resume_forecast(run.id)


def test_resume_through_the_api_path_runs_in_the_background(store, runs_dir, monkeypatch):
    """resume_forecast() queues the run and resumes it on a thread, as POST /api/forecasts/{id}/resume does."""
    import time

    from llm import mock
    from simulation import engine
    real_rumors = mock.rumors
    crashed = {"done": False}

    def flaky(events, world, *, seed, round_no):
        if round_no == 0 and not crashed["done"]:
            crashed["done"] = True
            raise RuntimeError("power cut")
        return real_rumors(events, world, seed=seed, round_no=round_no)

    monkeypatch.setattr(mock, "rumors", flaky)
    monkeypatch.setattr(engine, "get_store", lambda: store)
    run = registry.create(branch_name(35), _req(35).model_dump(), campaign=store.db)
    with pytest.raises(RuntimeError):
        run_sync(run, store=store, settings=store.s)
    assert run.status == "failed" and run.resumable

    engine.resume_forecast(run.id)
    deadline = time.time() + 120
    while run.status in ("queued", "running") and time.time() < deadline:
        time.sleep(0.2)
    assert run.status == "complete", run.error
    assert "after" not in (run.error or "")

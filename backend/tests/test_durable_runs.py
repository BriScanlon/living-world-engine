"""Forecast runs survive a restart: records and checkpoints on disk, interrupted runs resume where they stopped."""
import json
import time
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
    data.update(status="running", owner={"host": "old-backend", "pid": 1, "boot": "deadbeef"},
                heartbeat_at=time.time() - 3600)  # its process went quiet an hour ago
    path.write_text(json.dumps(data), encoding="utf-8")
    registry.reload()
    run = registry.get(run.id)
    assert run.status == "interrupted" and run.resumable and "propagate_rumors" in run.events[-1]["message"]
    assert "old-backend:1" in run.error

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



def _foreign(runs_dir, run, *, heartbeat_age, status="running"):
    """Rewrite a run's file as if another process (a script in `docker compose exec`) owned it."""
    path = runs_dir / f"{run.id}.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data.update(status=status, owner={"host": "backend-container", "pid": 4242, "boot": "otherproc"},
                heartbeat_at=time.time() - heartbeat_age)
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_a_run_another_live_process_owns_is_left_alone(store, runs_dir):
    """The bug in #23: the backend marked a script's live run interrupted and let the dashboard resume it."""
    from simulation.engine import resume_forecast
    from simulation.runs import RunBusy
    run = registry.create(branch_name(36), _req(36).model_dump(), campaign=store.db)
    run.checkpoint = {"next": "world_arbiter", "state": {}, "at": time.time()}
    registry.save(run)
    path = _foreign(runs_dir, run, heartbeat_age=5)
    before = path.read_text(encoding="utf-8")

    registry.reload()  # the backend starts, or first touches the registry, while the script is mid-forecast
    seen = registry.get(run.id)
    assert seen.status == "running" and seen.running_elsewhere and not seen.resumable
    assert path.read_text(encoding="utf-8") == before  # nothing written to the other process's record
    with pytest.raises(RunBusy, match="backend-container:4242"):
        resume_forecast(run.id)
    assert registry.active_on_branch(seen.branch.replace("/", "--")) is seen

    # the other process makes progress: the registry shows it (SSE streams hold the same object)
    data = json.loads(path.read_text(encoding="utf-8"))
    data["events"].append({"seq": 0, "ts": time.time(), "step": "world_arbiter", "kind": "start", "message": "x"})
    path.write_text(json.dumps(data), encoding="utf-8")
    assert registry.get(run.id) is seen and seen.events[-1]["step"] == "world_arbiter"


def test_a_run_whose_process_went_quiet_becomes_resumable(store, runs_dir):
    run = registry.create(branch_name(37), _req(37).model_dump(), campaign=store.db)
    run.checkpoint = {"next": "world_arbiter", "state": {}, "at": time.time()}
    registry.save(run)
    _foreign(runs_dir, run, heartbeat_age=600)
    registry.reload()
    seen = registry.get(run.id)
    assert seen.status == "interrupted" and seen.resumable and not seen.active
    claimed = registry.claim(run.id)
    assert claimed.mine and claimed.status == "queued"
    from simulation.runs import RunBusy
    with pytest.raises(RunBusy, match="already running here"):
        registry.claim(run.id)


def test_branch_controls_refuse_a_running_forecast(store, runs_dir):
    from fastapi import HTTPException

    from api.routes import _not_while_running
    run = registry.create(branch_name(38), _req(38).model_dump(), campaign=store.db)
    _foreign(runs_dir, run, heartbeat_age=3)
    registry.reload()
    with pytest.raises(HTTPException) as e:
        _not_while_running(run.branch, "merge")
    assert e.value.status_code == 409 and "another process" in e.value.detail
    _foreign(runs_dir, run, heartbeat_age=3, status="complete")
    registry.reload()
    _not_while_running(run.branch, "merge")  # finished: fine to merge


def test_heartbeat_keeps_a_long_step_alive(monkeypatch, runs_dir):
    from simulation import runs as runs_mod
    monkeypatch.setattr(runs_mod, "HEARTBEAT_EVERY", 0.05)
    run = registry.create("forecast/hb", {"session_number": 1})
    registry.set_status(run, "running")
    run.heartbeat_at = time.time() - 1000
    stop = registry.heartbeat(run)
    time.sleep(0.3)
    stop.set()
    assert run.heartbeat_age < 1
    saved = json.loads((runs_dir / f"{run.id}.json").read_text(encoding="utf-8"))
    assert time.time() - saved["heartbeat_at"] < 1


def test_run_files_from_a_newer_version_still_load(runs_dir):
    run = registry.create("forecast/newer", {"session_number": 1})
    registry.set_status(run, "complete")
    path = runs_dir / f"{run.id}.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["field_from_the_future"] = {"x": 1}
    path.write_text(json.dumps(data), encoding="utf-8")
    registry.reload()
    assert registry.get(run.id).status == "complete"

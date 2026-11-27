"""Registry of forecast runs and their progress events (for SSE), kept on disk so runs survive a restart.

Each run is a JSON file in ``RUNS_DIR`` (default: ``runs/`` next to the atlas folder, i.e. in the data volume):
its request, campaign, status, every progress event, the result and the latest **checkpoint**, which is the
simulation state after the last finished LangGraph node plus the node to run next.

When the backend starts, runs left ``queued``/``running`` by a previous process become ``interrupted``. An
interrupted or failed run with a checkpoint can be resumed from the next node (``simulation.engine.resume_forecast``):
every node commits its work to the forecast branch with deterministic ids, so re-running the node that was cut off
replaces rather than duplicates what it had written.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

ACTIVE = ("queued", "running")
FINISHED = ("complete", "failed", "interrupted")
KEEP = 200  # newest run files loaded at start-up


@dataclass
class Run:
    id: str
    branch: str
    request: dict[str, Any]
    status: str = "queued"  # queued | running | complete | failed | interrupted
    error: str | None = None
    events: list[dict[str, Any]] = field(default_factory=list)
    result: dict[str, Any] | None = None
    created_at: float = field(default_factory=time.time)
    campaign: str | None = None  # the database the run writes to
    checkpoint: dict[str, Any] | None = None  # {"next": node name | None, "state": SimState, "at": ts}
    updated_at: float = field(default_factory=time.time)

    @property
    def next_step(self) -> str | None:
        return (self.checkpoint or {}).get("next")

    @property
    def resumable(self) -> bool:
        return self.status in ("interrupted", "failed") and bool(self.next_step)

    def summary(self) -> dict[str, Any]:
        return {"id": self.id, "branch": self.branch, "status": self.status, "error": self.error,
                "request": self.request, "created_at": self.created_at, "updated_at": self.updated_at,
                "campaign": self.campaign, "resumable": self.resumable, "next_step": self.next_step,
                "steps": [e for e in self.events if e["kind"] in ("start", "done")]}


def _default_dir() -> Path:
    from config import get_settings
    s = get_settings()
    return Path(s.runs_dir) if s.runs_dir else Path(s.atlas_dir).parent / "runs"


class RunRegistry:
    def __init__(self, directory: Path | None = None) -> None:
        self._dir = directory
        self._runs: dict[str, Run] = {}
        self._lock = threading.RLock()
        self._loaded = False

    # ------------------------------------------------------------------ disk
    @property
    def dir(self) -> Path:
        return self._dir or _default_dir()

    def _path(self, run_id: str) -> Path:
        return self.dir / f"{run_id}.json"

    def save(self, run: Run) -> None:
        run.updated_at = time.time()
        try:
            self.dir.mkdir(parents=True, exist_ok=True)
            tmp = self._path(run.id).with_suffix(".json.tmp")
            tmp.write_text(json.dumps(asdict(run), default=str), encoding="utf-8")
            os.replace(tmp, self._path(run.id))
        except OSError:
            log.exception("Could not save forecast run %s", run.id)

    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        with self._lock:
            if self._loaded:
                return
            self._loaded = True
            if not self.dir.exists():
                return
            files = sorted(self.dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)[:KEEP]
            for p in files:
                try:
                    run = Run(**json.loads(p.read_text(encoding="utf-8")))
                except (OSError, ValueError, TypeError):
                    log.warning("Skipping unreadable run file %s", p.name)
                    continue
                self._runs[run.id] = run
                if run.status in ACTIVE:  # its process is gone
                    last = next((e for e in reversed(run.events) if e["kind"] in ("start", "done")), None)
                    where = (f" during {last['step']}" if last["kind"] == "start" else f" after {last['step']}") if last else ""
                    run.status = "interrupted"
                    run.error = f"The backend stopped{where}."
                    self._append(run, "interrupted", "interrupted",
                                 run.error + (f" Resume to continue from {run.next_step}." if run.next_step else ""),
                                 {"next": run.next_step})
                    self.save(run)

    def reload(self) -> None:
        """Forget what's in memory and read the run files again (as a fresh process would)."""
        with self._lock:
            self._runs.clear()
            self._loaded = False
        self._ensure_loaded()

    # ------------------------------------------------------------------ API
    def create(self, branch: str, request: dict[str, Any], campaign: str | None = None) -> Run:
        self._ensure_loaded()
        run = Run(id=uuid.uuid4().hex[:12], branch=branch, request=request, campaign=campaign)
        with self._lock:
            self._runs[run.id] = run
            self.save(run)
        return run

    def get(self, run_id: str) -> Run | None:
        self._ensure_loaded()
        return self._runs.get(run_id)

    def all(self) -> list[Run]:
        self._ensure_loaded()
        return sorted(self._runs.values(), key=lambda r: -r.created_at)

    def _append(self, run: Run, step: str, kind: str, message: str, data: Any = None) -> None:
        run.events.append({"seq": len(run.events), "ts": time.time(), "step": step, "kind": kind,
                           "message": message, "data": data})

    def emit(self, run_id: str, step: str, kind: str, message: str, data: Any = None) -> None:
        run = self.get(run_id)
        if not run:
            return
        with self._lock:
            self._append(run, step, kind, message, data)
            self.save(run)

    def set_status(self, run: Run, status: str, *, error: str | None = None,
                   result: dict[str, Any] | None = None) -> None:
        with self._lock:
            run.status = status
            run.error = error
            if result is not None:
                run.result = result
            self.save(run)

    def checkpoint(self, run_id: str, next_step: str | None, state: dict[str, Any]) -> None:
        run = self.get(run_id)
        if not run:
            return
        with self._lock:
            run.checkpoint = {"next": next_step, "state": state, "at": time.time()}
            self.save(run)


registry = RunRegistry()

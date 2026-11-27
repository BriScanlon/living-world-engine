"""Registry of forecast runs and their progress events (for SSE), kept on disk so runs survive a restart.

Each run is a JSON file in ``RUNS_DIR`` (default: ``runs/`` next to the atlas folder, i.e. in the data volume):
its request, campaign, status, every progress event, the result and the latest **checkpoint**, which is the
simulation state after the last finished LangGraph node plus the node to run next.

**Ownership.** A run belongs to the process running it (``owner``: host, pid and a per-process boot nonce, since a
restarted container can reuse pid 1). While it runs, a background thread refreshes ``heartbeat_at`` every
``HEARTBEAT_EVERY`` seconds, also during long model calls. Several processes share the run files (the backend
server, ``scripts/forecast_check.py`` in ``docker compose exec``), so a process never writes to a run another live
process owns. A run left ``queued``/``running`` by someone else is re-read from disk whenever it is looked at, and
is declared ``interrupted`` only once its heartbeat is older than ``STALE_AFTER`` seconds.

An interrupted or failed run with a checkpoint can be resumed from the next node (``simulation.engine.
resume_forecast``), which first **claims** it under a lock file. Every node commits its work to the forecast branch
with deterministic ids, so re-running the node that was cut off replaces rather than duplicates what it had written.
"""
from __future__ import annotations

import json
import logging
import os
import socket
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
HEARTBEAT_EVERY = 15.0  # seconds between heartbeats of a running run
STALE_AFTER = 90.0  # a run whose heartbeat is older than this has lost its process
LOCK_STALE_AFTER = 30.0  # a claim lock older than this was left by a crash

PROCESS = {"host": socket.gethostname(), "pid": os.getpid(), "boot": uuid.uuid4().hex[:12]}


class RunBusy(RuntimeError):
    """The run is being run (or claimed) by another live process."""


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
    owner: dict[str, Any] | None = None  # {"host", "pid", "boot"} of the process running it
    heartbeat_at: float | None = None

    @property
    def next_step(self) -> str | None:
        return (self.checkpoint or {}).get("next")

    @property
    def mine(self) -> bool:
        return bool(self.owner) and self.owner.get("boot") == PROCESS["boot"]

    @property
    def heartbeat_age(self) -> float:
        return time.time() - (self.heartbeat_at or self.updated_at or 0)

    @property
    def running_elsewhere(self) -> bool:
        """Active in another process that is still alive (its heartbeat is fresh)."""
        return self.status in ACTIVE and not self.mine and self.heartbeat_age < STALE_AFTER

    @property
    def active(self) -> bool:
        """Running here, or running in another live process."""
        return self.status in ACTIVE and (self.mine or self.heartbeat_age < STALE_AFTER)

    @property
    def resumable(self) -> bool:
        return self.status in ("interrupted", "failed") and bool(self.next_step)

    def summary(self) -> dict[str, Any]:
        return {"id": self.id, "branch": self.branch, "status": self.status, "error": self.error,
                "request": self.request, "created_at": self.created_at, "updated_at": self.updated_at,
                "campaign": self.campaign, "resumable": self.resumable, "next_step": self.next_step,
                "running_elsewhere": self.running_elsewhere,
                "owner": f"{self.owner['host']}:{self.owner['pid']}" if self.owner else None,
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
            tmp = self._path(run.id).with_suffix(f".json.{PROCESS['boot']}.tmp")
            tmp.write_text(json.dumps(asdict(run), default=str), encoding="utf-8")
            os.replace(tmp, self._path(run.id))
        except OSError:
            log.exception("Could not save forecast run %s", run.id)

    def _read(self, run_id: str) -> Run | None:
        try:
            return Run(**json.loads(self._path(run_id).read_text(encoding="utf-8")))
        except (OSError, ValueError, TypeError):
            return None

    def _refresh(self, run: Run) -> Run:
        """Bring another process's active run up to date from disk; if that process has gone quiet, mark the run
        interrupted (the only time this process writes to a run it doesn't own)."""
        if run.mine or run.status not in ACTIVE:
            return run
        fresh = self._read(run.id)
        if fresh:
            run.__dict__.update(fresh.__dict__)  # in place: SSE streams hold this object
        if run.status in ACTIVE and not run.mine and run.heartbeat_age >= STALE_AFTER:
            last = next((e for e in reversed(run.events) if e["kind"] in ("start", "done")), None)
            where = (f" during {last['step']}" if last["kind"] == "start" else f" after {last['step']}") if last else ""
            who = f" ({run.owner['host']}:{run.owner['pid']})" if run.owner else ""
            run.status = "interrupted"
            run.error = f"The process running it{who} stopped{where}."
            self._append(run, "interrupted", "interrupted",
                         run.error + (f" Resume to continue from {run.next_step}." if run.next_step else ""),
                         {"next": run.next_step})
            self.save(run)
        return run

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
                run = self._read(p.stem)
                if not run:
                    log.warning("Skipping unreadable run file %s", p.name)
                    continue
                self._runs[run.id] = self._refresh(run)

    def reload(self) -> None:
        """Forget what's in memory and read the run files again (as a fresh process would)."""
        with self._lock:
            self._runs.clear()
            self._loaded = False
        self._ensure_loaded()

    # ------------------------------------------------------------------ API
    def create(self, branch: str, request: dict[str, Any], campaign: str | None = None) -> Run:
        self._ensure_loaded()
        run = Run(id=uuid.uuid4().hex[:12], branch=branch, request=request, campaign=campaign,
                  owner=dict(PROCESS), heartbeat_at=time.time())
        with self._lock:
            self._runs[run.id] = run
            self.save(run)
        return run

    def get(self, run_id: str) -> Run | None:
        self._ensure_loaded()
        run = self._runs.get(run_id)
        if run is None:  # started by another process since we loaded
            run = self._read(run_id)
            if run:
                self._runs[run.id] = run
        return self._refresh(run) if run else None

    def all(self) -> list[Run]:
        self._ensure_loaded()
        for p in self.dir.glob("*.json") if self.dir.exists() else []:
            if p.stem not in self._runs:  # started by another process since we loaded
                run = self._read(p.stem)
                if run:
                    self._runs[run.id] = run
        return sorted((self._refresh(r) for r in list(self._runs.values())), key=lambda r: -r.created_at)

    def active_on_branch(self, branch_id: str) -> Run | None:
        """A forecast still being written to this branch (here or by another live process), if any."""
        from graph.store import to_branch_id
        return next((r for r in self.all() if r.active and to_branch_id(r.branch) == branch_id), None)

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
            if status in ACTIVE:
                run.heartbeat_at = time.time()
            self.save(run)

    def checkpoint(self, run_id: str, next_step: str | None, state: dict[str, Any]) -> None:
        run = self.get(run_id)
        if not run:
            return
        with self._lock:
            run.checkpoint = {"next": next_step, "state": state, "at": time.time()}
            run.heartbeat_at = time.time()
            self.save(run)

    # ------------------------------------------------------------------ ownership
    def heartbeat(self, run: Run) -> threading.Event:
        """Keep this run's heartbeat fresh until the returned event is set (call ``.set()`` when the run ends)."""
        stop = threading.Event()

        def beat() -> None:
            while not stop.wait(HEARTBEAT_EVERY):
                with self._lock:
                    if not run.mine or run.status not in ACTIVE:
                        return
                    run.heartbeat_at = time.time()
                    self.save(run)

        threading.Thread(target=beat, name=f"heartbeat-{run.id}", daemon=True).start()
        return stop

    def claim(self, run_id: str) -> Run:
        """Take over an interrupted or failed run so this process can resume it. Raises ``RunBusy`` if another live
        process is running or claiming it, ``KeyError`` if unknown and ``ValueError`` if it can't be resumed."""
        self._ensure_loaded()
        lock = self.dir / f"{run_id}.lock"
        self.dir.mkdir(parents=True, exist_ok=True)
        try:
            if lock.exists() and time.time() - lock.stat().st_mtime > LOCK_STALE_AFTER:
                lock.unlink(missing_ok=True)
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError as e:
            raise RunBusy("Another process is resuming this run right now") from e
        try:
            os.write(fd, json.dumps(PROCESS).encode())
            os.close(fd)
            with self._lock:
                run = self._runs.get(run_id) or self._read(run_id)
                if not run:
                    raise KeyError(run_id)
                self._runs[run.id] = run
                if not run.mine:
                    fresh = self._read(run_id)  # the file is the truth for another process's run
                    if fresh:
                        run.__dict__.update(fresh.__dict__)
                if run.running_elsewhere:
                    who = f"{run.owner['host']}:{run.owner['pid']}" if run.owner else "another process"
                    raise RunBusy(f"This run is still running in {who} "
                                  f"(heartbeat {run.heartbeat_age:.0f} s ago)")
                if run.status in ACTIVE and run.mine:
                    raise RunBusy("This run is already running here")
                if not run.resumable:
                    raise ValueError(f"This run is {run.status}"
                                     + ("" if run.next_step else " and has no checkpoint to resume from"))
                run.owner = dict(PROCESS)
                run.status = "queued"
                run.error = None
                run.heartbeat_at = time.time()
                self.save(run)
                return run
        finally:
            lock.unlink(missing_ok=True)


registry = RunRegistry()

"""In-process registry of forecast runs and their live progress events (for SSE)."""
from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Run:
    id: str
    branch: str
    request: dict[str, Any]
    status: str = "queued"  # queued | running | complete | failed
    error: str | None = None
    events: list[dict[str, Any]] = field(default_factory=list)
    result: dict[str, Any] | None = None
    created_at: float = field(default_factory=time.time)

    def summary(self) -> dict[str, Any]:
        return {"id": self.id, "branch": self.branch, "status": self.status, "error": self.error,
                "request": self.request, "created_at": self.created_at,
                "steps": [e for e in self.events if e["kind"] in ("start", "done")]}


class RunRegistry:
    def __init__(self) -> None:
        self._runs: dict[str, Run] = {}
        self._lock = threading.Lock()

    def create(self, branch: str, request: dict[str, Any]) -> Run:
        run = Run(id=uuid.uuid4().hex[:12], branch=branch, request=request)
        with self._lock:
            self._runs[run.id] = run
        return run

    def get(self, run_id: str) -> Run | None:
        return self._runs.get(run_id)

    def all(self) -> list[Run]:
        return sorted(self._runs.values(), key=lambda r: -r.created_at)

    def emit(self, run_id: str, step: str, kind: str, message: str, data: Any = None) -> None:
        run = self._runs.get(run_id)
        if not run:
            return
        with self._lock:
            run.events.append({"seq": len(run.events), "ts": time.time(), "step": step, "kind": kind,
                               "message": message, "data": data})


registry = RunRegistry()

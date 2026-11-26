"""Single-worker async job queue (one GPU job at a time) with live WebSocket progress."""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path
from typing import Any

from ..db import Database
from ..hub import Hub
from . import flows

log = logging.getLogger("vtt.jobs")

STAGE_LABELS = {
    "queued": "Queued",
    "prompting": "Prompting",
    "base_map": "Generating Base Map",
    "animating": "Animating Loop",
    "saving": "Saving",
    "done": "Done",
    "error": "Failed",
}


class JobManager:
    def __init__(self, db: Database, hub: Hub, on_asset=None):
        self.db = db
        self.hub = hub
        self.on_asset = on_asset  # async callback(asset, job_input) e.g. auto-push to table
        self.queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._worker: asyncio.Task | None = None

    def start(self) -> None:
        self._worker = asyncio.create_task(self._run_forever())

    async def stop(self) -> None:
        if self._worker:
            self._worker.cancel()

    async def submit(self, kind: str, payload: dict[str, Any]) -> dict[str, Any]:
        now = time.time()
        job = {
            "id": flows.new_id(),
            "kind": kind,
            "status": "queued",
            "stage": "queued",
            "progress": 0.0,
            "message": f"Waiting for GPU ({self.queue.qsize()} ahead)",
            "error": None,
            "input": payload,
            "result_asset_id": None,
            "created_at": now,
            "updated_at": now,
        }
        await self._publish(job)
        await self.queue.put(job)
        return job

    async def _publish(self, job: dict[str, Any]) -> None:
        job["updated_at"] = time.time()
        job["stage_label"] = STAGE_LABELS.get(job["stage"], job["stage"])
        self.db.upsert_job(job)
        await self.hub.broadcast({"type": "job", "job": job}, role="dm")

    async def _run_forever(self) -> None:
        while True:
            job = await self.queue.get()
            try:
                await self._run(job)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                log.exception("Job %s failed", job["id"])
                job.update(status="error", stage="error", error=str(exc) or exc.__class__.__name__,
                           message="Failed")
                await self._publish(job)
            finally:
                self.queue.task_done()

    async def _run(self, job: dict[str, Any]) -> None:
        job["status"] = "running"
        last_sent = 0.0

        async def report(stage: str, progress: float, message: str) -> None:
            nonlocal last_sent
            changed_stage = stage != job["stage"]
            job.update(stage=stage, progress=round(min(max(progress, 0), 1), 3), message=message)
            now = time.monotonic()
            if changed_stage or now - last_sent > 0.2:
                last_sent = now
                await self._publish(job)

        p = job["input"]
        if job["kind"] == "generate":
            asset = await flows.generate_map(
                self.db, description=p["description"], style=p.get("style", ""), report=report,
                animate=p.get("animate", True),
            )
        elif job["kind"] == "animate":
            asset = await flows.animate_image(
                self.db, image_path=Path(p["path"]), report=report, source=p.get("source", "inbox"),
                title=p.get("title"),
            )
        else:
            raise ValueError(f"Unknown job kind {job['kind']}")

        notes = asset.pop("_notes", [])
        job.update(status="done", stage="done", progress=1.0, result_asset_id=asset["id"],
                   message="; ".join(notes) if notes else f"Added '{asset['title']}' to library")
        await self._publish(job)
        await self.hub.broadcast({"type": "asset_added", "asset": asset}, role="dm")
        if self.on_asset:
            await self.on_asset(asset, p)

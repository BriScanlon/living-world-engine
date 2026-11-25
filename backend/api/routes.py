"""HTTP API for the DM dashboard."""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any

from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field

from atlas.service import atlas_path, atlas_state, bootstrap_sample, import_world, place_detail
from config import get_settings
from graph.briefing import build_briefing, load_run
from graph.seed import DEMO_SESSION_SUMMARY, seed
from graph.store import MAIN, GraphStoreError, get_store, to_branch_id
from graph.world import WorldView
from ingestion import audio, documents
from ingestion.pipeline import JOBS, Source, start_job
from simulation.engine import start_forecast
from simulation.models import ForecastRequest
from simulation.runs import registry

router = APIRouter(prefix="/api")


def _store():
    return get_store()


def _err(e: Exception) -> HTTPException:
    return HTTPException(status_code=400, detail=str(e))


# =========================================================================== health / meta
@router.get("/health")
def health() -> dict[str, Any]:
    s = get_settings()
    try:
        info = _store().ping()
        tdb = {"ok": True, "version": info.get("terminusdb", {}).get("version")}
    except Exception as e:  # noqa: BLE001
        tdb = {"ok": False, "error": str(e)}
    return {"status": "ok", "terminusdb": tdb, "mock_llm": s.mock_llm,
            "llm_model": None if s.mock_llm else s.llm_model, "database": s.terminusdb_db,
            "docling": documents.docling_available(), "whisper": audio.whisper_available()}


@router.get("/meta")
def meta(branch: str = MAIN) -> dict[str, Any]:
    w = WorldView.load(_store(), branch)
    sessions = sorted({d.get("source_session") for d in w.all.values() if d.get("source_session")})
    runs = [d for d in w.all.values() if d["@type"] == "ForecastRun"]
    last_session = max([r["session_number"] for r in runs] or [0])
    for s in sessions:
        digits = "".join(ch for ch in s.split("/")[-1] if ch.isdigit())
        if digits:
            last_session = max(last_session, int(digits))
    return {"branch": branch, "current_day": w.max_day(), "last_session": last_session,
            "sessions": sessions, "demo_summary": DEMO_SESSION_SUMMARY,
            "counts": WorldView(w.all.values()).snapshot()["counts"]}


@router.post("/admin/seed")
def admin_seed(reset: bool = True) -> dict[str, Any]:
    result = seed(_store(), reset=reset)
    if get_settings().atlas_sample_on_startup:
        result["atlas"] = bootstrap_sample(_store())
    return result


# =========================================================================== branches
@router.get("/branches")
def branches() -> list[dict[str, Any]]:
    store = _store()
    out = []
    for b in store.list_branches():
        info: dict[str, Any] = {**b, "is_forecast": b["id"].startswith("forecast--")}
        if info["is_forecast"]:
            doc = store.get(f"ForecastRun/{b['id']}", b["name"])
            if doc:
                info["run"] = {k: doc.get(k) for k in ("session_number", "start_day", "end_day", "created_at",
                                                       "status", "mock_llm", "base_commit", "summary")}
        live = next((r for r in registry.all() if to_branch_id(r.branch) == b["id"]), None)
        if live:
            info["live"] = {"id": live.id, "status": live.status}
        out.append(info)
    return out


class MergeBody(BaseModel):
    target: str = MAIN
    delete_after: bool = False


@router.post("/branches/{name:path}/merge")
def merge(name: str, body: MergeBody | None = None) -> dict[str, Any]:
    body = body or MergeBody()
    store = _store()
    try:
        run = load_run(WorldView(store.all_documents(name)), name)
        res = store.merge(name, body.target, base_commit=(run or {}).get("base_commit"))
        if body.delete_after:
            store.delete_branch(name)
        return {"merged": name, "into": body.target, **res}
    except Exception as e:  # noqa: BLE001
        raise _err(e) from e


class CherryPickBody(BaseModel):
    event_ids: list[str] = Field(min_length=1)
    target: str = MAIN


@router.post("/branches/{name:path}/cherry-pick")
def cherry_pick(name: str, body: CherryPickBody) -> dict[str, Any]:
    try:
        return _store().cherry_pick(name, body.event_ids, body.target)
    except Exception as e:  # noqa: BLE001
        raise _err(e) from e


@router.delete("/branches/{name:path}")
def discard(name: str) -> dict[str, Any]:
    try:
        _store().delete_branch(name)
    except (GraphStoreError, Exception) as e:  # noqa: BLE001
        raise _err(e) from e
    return {"deleted": name}


@router.post("/branches/{name:path}/rerun")
def rerun(name: str) -> dict[str, Any]:
    """Discard a forecast branch and run a fresh forecast with the same inputs."""
    store = _store()
    run = load_run(WorldView(store.all_documents(name)), name)
    live = next((r for r in registry.all() if to_branch_id(r.branch) == to_branch_id(name)), None)
    if run and run["report"].get("request"):
        req = ForecastRequest(**run["report"]["request"])
    elif live:
        req = ForecastRequest(**live.request)
    else:
        raise HTTPException(404, f"No forecast inputs recorded for {name}")
    # A fresh seed so the re-run explores a different future
    req.seed = (req.seed if req.seed is not None else req.session_number) + int(os.urandom(2).hex(), 16)
    try:
        store.delete_branch(name)
    except Exception:  # noqa: BLE001
        pass
    new = start_forecast(req)
    return {"run_id": new.id, "branch": new.branch, "discarded": name}


@router.get("/branches/{name:path}/log")
def branch_log(name: str, limit: int = 100) -> list[dict[str, Any]]:
    try:
        return _store().log(name, limit)
    except Exception as e:  # noqa: BLE001
        raise _err(e) from e


# =========================================================================== diff / briefing
@router.get("/diff")
def diff(base: str = MAIN, compare: str = Query(...)) -> dict[str, Any]:
    try:
        return build_briefing(_store(), base, compare)
    except Exception as e:  # noqa: BLE001
        raise _err(e) from e


@router.get("/diff/raw")
def raw_diff(base: str = MAIN, compare: str = Query(...)) -> list[dict[str, Any]]:
    """TerminusDB's native document patch between two branches."""
    try:
        return _store().native_diff(base, compare)
    except Exception as e:  # noqa: BLE001
        raise _err(e) from e


# =========================================================================== world / time travel
@router.get("/world/snapshot")
def snapshot(branch: str = MAIN, day: int | None = None, commit: str | None = None) -> dict[str, Any]:
    """What was true on ``day`` (valid time) on ``branch``, optionally as recorded at ``commit`` (transaction time)."""
    try:
        w = WorldView.load(_store(), branch, day=day, commit=commit)
    except Exception as e:  # noqa: BLE001
        raise _err(e) from e
    snap = w.snapshot()
    snap["names"] = {i: d.get("name", i) for i, d in w.all.items() if d.get("name")}
    snap["max_day"] = w.max_day()
    snap["branch"] = branch
    snap["commit"] = commit
    return snap


@router.get("/world/agent/{agent_id:path}")
def agent_view(agent_id: str, branch: str = MAIN, day: int | None = None) -> dict[str, Any]:
    """The exact epistemic slice an agent's prompt would receive."""
    w = WorldView.load(_store(), branch, day=day)
    if agent_id not in w.docs:
        raise HTTPException(404, f"{agent_id} not active on {branch} at day {day}")
    return w.agent_context(agent_id)


# =========================================================================== atlas
ATLAS_FILES = {"land.geojson", "biomes.geojson", "provinces.geojson", "rivers.geojson", "roads.geojson",
               "burgs.geojson", "tiles.pmtiles"}


@router.get("/atlas")
def atlas(branch: str = MAIN, day: int | None = None, compare: str | None = None) -> dict[str, Any]:
    """Live map state: province controllers, places, events and rumors on ``day`` of ``branch``."""
    try:
        return atlas_state(_store(), branch, day, compare)
    except Exception as e:  # noqa: BLE001
        raise _err(e) from e


@router.get("/atlas/place/{doc_id:path}")
def atlas_place(doc_id: str, branch: str = MAIN, day: int | None = None) -> dict[str, Any]:
    out = place_detail(_store(), doc_id, branch, day)
    if not out:
        raise HTTPException(404, f"{doc_id} not found on {branch}")
    return out


@router.get("/atlas/files/{slug}/{name}")
def atlas_file(slug: str, name: str) -> FileResponse:
    """Static layers: GeoJSON, or PMTiles (served with HTTP range support for the pmtiles client)."""
    if name not in ATLAS_FILES or not slug.replace("-", "").isalnum():
        raise HTTPException(404, "Unknown atlas file")
    path = atlas_path(slug) / name
    if not path.exists():
        raise HTTPException(404, "Not built")
    media = "application/geo+json" if name.endswith(".geojson") else "application/vnd.pmtiles"
    return FileResponse(path, media_type=media, headers={"Cache-Control": "public, max-age=300"})


@router.post("/atlas/import")
def atlas_import(file: UploadFile = File(...), branch: str = Form(MAIN),
                 place_greywater: bool = Form(False)) -> dict[str, Any]:
    """Import an Azgaar 'Save > JSON > Full' export as a new atlas."""
    path = _save_upload(file)
    try:
        return import_world(_store(), path, branch=branch, place_greywater=place_greywater)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


# =========================================================================== forecasts
@router.post("/forecasts")
def create_forecast(req: ForecastRequest) -> dict[str, Any]:
    if not _store().branch_exists(req.base_branch):
        raise HTTPException(400, f"Unknown base branch {req.base_branch}")
    run = start_forecast(req)
    return {"run_id": run.id, "branch": run.branch}


@router.get("/forecasts")
def list_forecasts() -> list[dict[str, Any]]:
    return [r.summary() for r in registry.all()]


@router.get("/forecasts/{run_id}")
def get_forecast(run_id: str) -> dict[str, Any]:
    run = registry.get(run_id)
    if not run:
        raise HTTPException(404, "Unknown run")
    return {**run.summary(), "events": run.events, "result": run.result}


@router.get("/forecasts/{run_id}/events")
async def forecast_events(run_id: str) -> StreamingResponse:
    """Server-sent events: live LangGraph step progress."""
    run = registry.get(run_id)
    if not run:
        raise HTTPException(404, "Unknown run")

    async def gen():
        sent = 0
        while True:
            while sent < len(run.events):
                yield f"data: {json.dumps(run.events[sent], default=str)}\n\n"
                sent += 1
            if run.status in ("complete", "failed") and sent >= len(run.events):
                yield f"event: end\ndata: {json.dumps({'status': run.status, 'branch': run.branch})}\n\n"
                return
            await asyncio.sleep(0.25)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# =========================================================================== ingestion
class TextIngest(BaseModel):
    title: str = "pasted-notes.md"
    text: str = Field(min_length=1)
    branch: str = MAIN
    is_session: bool = False
    in_world_day: int | None = None


@router.post("/ingest/text")
def ingest_text(body: TextIngest) -> dict[str, Any]:
    job = start_job([Source(name=body.title, kind="markdown", text=body.text, is_session=body.is_session)],
                    body.branch, body.in_world_day)
    return job.dump()


def _save_upload(f: UploadFile) -> str:
    d = Path(get_settings().upload_dir)
    d.mkdir(parents=True, exist_ok=True)
    fd, path = tempfile.mkstemp(suffix=Path(f.filename or "upload").suffix, dir=d)
    with os.fdopen(fd, "wb") as out:
        shutil.copyfileobj(f.file, out)
    return path


@router.post("/ingest/files")
def ingest_files(files: list[UploadFile] = File(...), branch: str = Form(MAIN),
                 is_session: bool = Form(False), in_world_day: int | None = Form(None)) -> dict[str, Any]:
    sources = []
    for f in files:
        ext = Path(f.filename or "").suffix.lower()
        if ext in documents.TEXT_EXT:
            kind = "markdown"
        elif ext in documents.AUDIO_EXT:
            if not audio.whisper_available():
                raise HTTPException(400, "Audio needs faster-whisper (INSTALL_WHISPER=true)")
            kind = "audio"
        elif ext in documents.DOC_EXT:
            kind = "document"
        else:
            raise HTTPException(400, f"Unsupported file type: {f.filename}")
        sources.append(Source(name=f.filename or "upload", kind=kind, path=_save_upload(f),
                              is_session=is_session or kind == "audio"))
    return start_job(sources, branch, in_world_day).dump()


@router.post("/ingest/audio")
def ingest_audio(file: UploadFile = File(...), branch: str = Form(MAIN),
                 in_world_day: int | None = Form(None)) -> dict[str, Any]:
    if not audio.whisper_available():
        raise HTTPException(400, "faster-whisper is not installed (set INSTALL_WHISPER=true and rebuild)")
    src = Source(name=file.filename or "session-audio", kind="audio", path=_save_upload(file), is_session=True)
    return start_job([src], branch, in_world_day).dump()


@router.post("/ingest/transcribe")
async def transcribe_only(file: UploadFile = File(...)) -> dict[str, Any]:
    """Transcribe audio and return text (lets the DM edit it before running a forecast)."""
    if not audio.whisper_available():
        raise HTTPException(400, "faster-whisper is not installed (set INSTALL_WHISPER=true and rebuild)")
    path = _save_upload(file)
    return {"text": await run_in_threadpool(audio.transcribe, path)}


@router.get("/ingest/jobs/{job_id}")
def ingest_job(job_id: str) -> dict[str, Any]:
    job = JOBS.get(job_id)
    if not job:
        raise HTTPException(404, "Unknown job")
    return job.dump()


@router.get("/ingest/jobs")
def ingest_jobs() -> list[dict[str, Any]]:
    return [j.dump() for j in sorted(JOBS.values(), key=lambda j: -j.created_at)]

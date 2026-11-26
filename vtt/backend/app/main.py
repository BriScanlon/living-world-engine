"""FastAPI entrypoint: REST API, WebSocket hub, media files and the built React SPA."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import shutil
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import __version__
from .config import settings
from .db import Database
from .hub import Hub
from .pipeline import flows
from .pipeline.comfy import IMAGE_EXTS, VIDEO_EXTS
from .pipeline.jobs import JobManager
from .state import TableService
from .watcher import InboxWatcher

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("vtt")

STYLES = [
    "Painterly fantasy",
    "Hand-drawn ink & watercolor",
    "Gritty dark fantasy",
    "Photoreal",
    "Bright storybook",
    "Pixel art",
]

settings.ensure_dirs()


class Services:
    db: Database
    hub: Hub
    table: TableService
    jobs: JobManager
    watcher: InboxWatcher | None = None


svc = Services()


async def _after_asset(asset: dict[str, Any], job_input: dict[str, Any]) -> None:
    if job_input.get("push") and asset.get("asset_type") == "map":
        await svc.table.push_map(asset)


async def _inbox_file(path: Path) -> None:
    dest = settings.uploads_dir / f"{flows.new_id()}{path.suffix.lower()}"
    shutil.move(str(path), str(dest))
    log.info("Inbox: queued %s for animation", path.name)
    await svc.jobs.submit("animate", {"path": str(dest), "source": "inbox", "title": flows.nice_title(path.stem)})


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    svc.db = Database(settings.db_path)
    svc.db.fail_stale_jobs()
    svc.hub = Hub()
    svc.table = TableService(svc.db, svc.hub, settings.default_hex_px)
    svc.jobs = JobManager(svc.db, svc.hub, on_asset=_after_asset)
    svc.jobs.start()
    if settings.watch_inbox:
        svc.watcher = InboxWatcher(settings.inbox_dir, _inbox_file, polling=settings.inbox_polling)
        svc.watcher.start(asyncio.get_running_loop())
    log.info("VTT ready. Data dir: %s  mock pipeline: %s", settings.data_dir, settings.mock)
    yield
    if svc.watcher:
        svc.watcher.stop()
    await svc.jobs.stop()
    svc.table.flush()
    svc.db.close()


app = FastAPI(title="Local-AI Tabletop VTT", version=__version__, lifespan=lifespan)


# --------------------------------------------------------------------------- REST
class GenerateRequest(BaseModel):
    description: str = Field(min_length=3, max_length=600)
    style: str = Field(default="", max_length=80)
    animate: bool = True
    push: bool = False
    title: str | None = Field(default=None, max_length=80)
    # e.g. "lwe:Location/the-docks", so the world engine can find what it asked for
    tags: list[str] = Field(default_factory=list, max_length=8)


class TokenRequest(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    description: str = Field(min_length=3, max_length=600)
    tags: list[str] = Field(default_factory=list, max_length=8)


class TokenPlace(BaseModel):
    asset_id: str | None = None
    name: str | None = Field(default=None, max_length=60)
    x: float | None = None
    y: float | None = None
    size: float | None = None
    hidden: bool = False
    color: str | None = None


class LogNote(BaseModel):
    text: str = Field(min_length=1, max_length=2000)


class AssetPatch(BaseModel):
    title: str = Field(min_length=1, max_length=80)


@app.get("/api/health")
async def health() -> dict:
    return {"ok": True, "version": __version__}


@app.get("/api/config")
async def config() -> dict:
    return {
        "version": __version__,
        "mock": settings.mock,
        "styles": STYLES,
        "animationEnabled": settings.mock or bool(settings.comfy_video_url),
        "watchInbox": settings.watch_inbox,
        "defaultHexPx": settings.default_hex_px,
    }


@app.get("/api/state")
async def get_state() -> dict:
    return svc.table.snapshot()


@app.get("/api/maps")
async def list_maps(type: str = "map") -> list[dict]:
    if type not in ("map", "token"):
        raise HTTPException(400, "type must be map or token")
    return svc.db.list_assets(type)


def _asset_or_404(asset_id: str) -> dict:
    asset = svc.db.get_asset(asset_id)
    if not asset:
        raise HTTPException(404, "Asset not found")
    return asset


@app.get("/api/maps/{asset_id}")
async def get_map(asset_id: str) -> dict:
    return _asset_or_404(asset_id)


@app.patch("/api/maps/{asset_id}")
async def rename_map(asset_id: str, patch: AssetPatch) -> dict:
    asset = _asset_or_404(asset_id)
    asset["title"] = patch.title.strip()
    saved = svc.db.insert_asset(asset)
    await svc.hub.broadcast({"type": "library_changed"}, role="dm")
    return saved


@app.delete("/api/maps/{asset_id}")
async def delete_map(asset_id: str) -> dict:
    asset = _asset_or_404(asset_id)
    svc.db.delete_asset(asset_id)
    for key in ("media_path", "still_path", "thumb_path"):
        rel = asset.get(key)
        if not rel:
            continue
        p = (settings.data_dir / rel).resolve()
        if settings.data_dir in p.parents and p.is_file():
            p.unlink(missing_ok=True)
    await svc.table.asset_deleted(asset_id)
    await svc.hub.broadcast({"type": "library_changed"}, role="dm")
    return {"ok": True}


@app.post("/api/maps/{asset_id}/push")
async def push_map(asset_id: str) -> dict:
    asset = _asset_or_404(asset_id)
    if asset["asset_type"] != "map":
        raise HTTPException(400, "Only maps can be pushed to the table")
    await svc.table.push_map(asset)
    return {"ok": True}


@app.post("/api/table/clear")
async def clear_table() -> dict:
    await svc.table.push_map(None)
    return {"ok": True}


@app.get("/api/table/tokens")
async def table_tokens() -> dict:
    """Every token on the current map, hidden ones included (DM view)."""
    return {"tokens": svc.table.tokens_for("dm")}


@app.post("/api/table/tokens")
async def place_token(body: TokenPlace) -> dict:
    """Put a token on the current map (at the centre of the view unless x/y are given)."""
    token = await svc.table.add_token(body.model_dump(exclude_none=True))
    if not token:
        raise HTTPException(400, "Not a token in the library, or the map has too many tokens")
    return token


@app.delete("/api/table/tokens")
async def clear_tokens() -> dict:
    await svc.table.clear_tokens()
    return {"ok": True}


@app.get("/api/maps/{asset_id}/view")
async def map_view(asset_id: str) -> dict:
    """How a map sits on the table: mode, pxPerSquare (image px per 5 ft square), centre.
    The world engine uses pxPerSquare to place battle maps on its globe at true scale."""
    asset = _asset_or_404(asset_id)
    view = svc.table.view_for(asset)
    return {**view, "width": asset.get("width"), "height": asset.get("height"),
            "metresPerPx": round(1.524 / view["pxPerSquare"], 6) if view.get("pxPerSquare") else None}


# ------------------------------------------------------------------ world scene
MAX_SCENE_BYTES = 200_000


@app.get("/api/scene")
async def get_scene() -> dict:
    return {"scene": svc.table.scene}


@app.post("/api/scene")
async def set_scene(scene: dict[str, Any]) -> dict:
    """World context for the place on the table, pushed by the Living World Engine:
    who is here, who holds it, rumors to read aloud, creatures with SRD stats. Shown on /dm only."""
    import json as _json

    if len(_json.dumps(scene)) > MAX_SCENE_BYTES:
        raise HTTPException(413, "Scene too large")
    if not isinstance(scene.get("place"), dict) or not scene["place"].get("name"):
        raise HTTPException(400, "A scene needs place.name")
    await svc.table.set_scene(scene)
    return {"ok": True}


@app.delete("/api/scene")
async def clear_scene() -> dict:
    await svc.table.set_scene(None)
    return {"ok": True}


def _check_tags(tags: list[str]) -> None:
    if any(len(t) > 120 for t in tags):
        raise HTTPException(400, "Tags must be at most 120 characters")


@app.post("/api/generate")
async def generate(req: GenerateRequest) -> dict:
    _check_tags(req.tags)
    return await svc.jobs.submit("generate", req.model_dump())


@app.post("/api/tokens")
async def generate_token(req: TokenRequest) -> dict:
    """Paint a round top-down token (portrait) for a character or creature."""
    _check_tags(req.tags)
    return await svc.jobs.submit("token", req.model_dump())


@app.post("/api/upload")
async def upload(
    file: UploadFile = File(...),
    mode: str = Form("display"),      # display | animate
    push: bool = Form(False),
    title: str = Form(""),
    tags: str = Form(""),             # comma-separated, e.g. "lwe-overland:living_world" from the world engine
) -> dict:
    name = Path(file.filename or "upload.png").name
    ext = Path(name).suffix.lower()
    if ext not in IMAGE_EXTS + VIDEO_EXTS:
        raise HTTPException(400, f"Unsupported file type {ext!r}")
    if mode == "animate" and ext not in IMAGE_EXTS:
        raise HTTPException(400, "Only images can be queued for animation")
    dest = settings.uploads_dir / f"{flows.new_id()}{ext}"
    with dest.open("wb") as fh:
        await asyncio.to_thread(shutil.copyfileobj, file.file, fh, 1024 * 1024)
    stem_title = title.strip() or flows.nice_title(Path(name).stem)

    if mode == "animate":
        job = await svc.jobs.submit("animate", {"path": str(dest), "source": "imported", "title": stem_title, "push": push})
        return {"queued": True, "job": job}

    tag_list = [t.strip()[:120] for t in tags.split(",") if t.strip()][:8]
    asset = await asyncio.to_thread(flows.import_static, svc.db, file_path=dest, title=stem_title, tags=tag_list)
    await svc.hub.broadcast({"type": "asset_added", "asset": asset}, role="dm")
    if push:
        await svc.table.push_map(asset)
    return {"queued": False, "asset": asset}


@app.get("/api/jobs")
async def list_jobs() -> list[dict]:
    return svc.db.list_jobs()


@app.get("/api/jobs/{job_id}")
async def get_job(job_id: str) -> dict:
    job = svc.db.get_job(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    return job


# ------------------------------------------------------------------ session log
@app.get("/api/session-log")
async def get_session_log() -> dict:
    """What happened at the table this session: DM notes, maps shown, world scenes visited.
    The world engine reads it to pre-fill the next forecast's session summary."""
    return {"entries": svc.db.list_log()}


@app.post("/api/session-log")
async def add_session_note(note: LogNote) -> dict:
    return await svc.table.log("note", note.text.strip())


@app.delete("/api/session-log/{entry_id}")
async def delete_session_entry(entry_id: int) -> dict:
    svc.db.delete_log(entry_id)
    await svc.hub.broadcast({"type": "log_full", "entries": svc.db.list_log()}, role="dm")
    return {"ok": True}


@app.delete("/api/session-log")
async def clear_session_log() -> dict:
    """Start a new session."""
    svc.db.clear_log()
    await svc.hub.broadcast({"type": "log_full", "entries": []}, role="dm")
    return {"ok": True}


@app.post("/api/library/changed")
async def library_changed() -> dict:
    """Lets out-of-process tools (the seeder CLI) tell open /dm panels to refresh."""
    await svc.hub.broadcast({"type": "library_changed"}, role="dm")
    return {"ok": True}


# ------------------------------------------------------------------------ WebSocket
@app.websocket("/ws")
async def websocket(ws: WebSocket) -> None:
    role = ws.query_params.get("role", "dm")
    if role not in ("table", "dm"):
        await ws.close(code=1008)
        return
    await ws.accept()
    svc.hub.add(ws, role)
    try:
        await svc.hub.send(ws, {"type": "state", "state": svc.table.snapshot()})
        await svc.hub.send(ws, {"type": "tokens", "tokens": svc.table.tokens_for(role)})
        if role == "dm":
            await svc.hub.send(ws, {"type": "jobs", "jobs": svc.db.list_jobs()})
            await svc.hub.send(ws, {"type": "scene", "scene": svc.table.scene})
            await svc.hub.send(ws, {"type": "log_full", "entries": svc.db.list_log()})
        await svc.table.broadcast_clients()
        while True:
            msg = await ws.receive_json()
            if isinstance(msg, dict):
                await svc.table.handle(ws, role, msg)
    except (WebSocketDisconnect, RuntimeError):
        pass
    except Exception:  # noqa: BLE001
        log.exception("websocket error")
    finally:
        svc.hub.remove(ws)
        await svc.table.broadcast_clients()


# ------------------------------------------------------------------- media + SPA
# Only expose media sub-folders (never the DB folder).
for sub in ("maps", "thumbs", "starter_pack"):
    (settings.data_dir / sub).mkdir(parents=True, exist_ok=True)
    app.mount(f"/media/{sub}", StaticFiles(directory=settings.data_dir / sub), name=f"media-{sub}")

DIST = settings.frontend_dist
if (DIST / "index.html").is_file():
    if (DIST / "assets").is_dir():
        app.mount("/assets", StaticFiles(directory=DIST / "assets"), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa(full_path: str):
        if full_path.startswith(("api/", "media/", "ws")):
            raise HTTPException(404)
        candidate = (DIST / full_path).resolve()
        if full_path and DIST.resolve() in candidate.parents and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(DIST / "index.html", headers={"Cache-Control": "no-cache"})
else:
    @app.get("/", include_in_schema=False)
    async def no_frontend():
        return JSONResponse({
            "message": "Frontend not built. Run `npm run build` in ./frontend, or use the Vite dev server on :5173.",
            "table": "/table", "dm": "/dm", "api": "/docs",
        })

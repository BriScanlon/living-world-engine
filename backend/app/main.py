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


@app.post("/api/generate")
async def generate(req: GenerateRequest) -> dict:
    return await svc.jobs.submit("generate", req.model_dump())


@app.post("/api/upload")
async def upload(
    file: UploadFile = File(...),
    mode: str = Form("display"),      # display | animate
    push: bool = Form(False),
    title: str = Form(""),
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

    asset = await asyncio.to_thread(flows.import_static, svc.db, file_path=dest, title=stem_title)
    await svc.hub.broadcast({"type": "asset_added", "asset": asset}, role="dm")
    if push:
        await svc.table.push_map(asset)
    return {"queued": False, "asset": asset}


@app.get("/api/jobs")
async def list_jobs() -> list[dict]:
    return svc.db.list_jobs()


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
        if role == "dm":
            await svc.hub.send(ws, {"type": "jobs", "jobs": svc.db.list_jobs()})
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

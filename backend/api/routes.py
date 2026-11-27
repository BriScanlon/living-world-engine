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
from simulation.engine import resume_forecast, start_forecast
from simulation.models import ForecastRequest
from simulation.runs import RunBusy, registry

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
    return {"status": "ok", "terminusdb": tdb, "mock_llm": s.mock_llm, "campaign": _store().db,
            "default_campaign": s.terminusdb_db,
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


def _not_while_running(name: str, what: str) -> None:
    """Refuse to touch a forecast branch that is still being written (here or by another process)."""
    run = registry.active_on_branch(to_branch_id(name))
    if run:
        where = "in another process" if run.running_elsewhere else "here"
        raise HTTPException(409, f"Can't {what} {name}: its forecast is still running {where} "
                                 f"(run {run.id}); wait for it to finish")


@router.post("/branches/{name:path}/merge")
def merge(name: str, body: MergeBody | None = None) -> dict[str, Any]:
    body = body or MergeBody()
    _not_while_running(name, "merge")
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
    _not_while_running(name, "cherry-pick from")
    try:
        return _store().cherry_pick(name, body.event_ids, body.target)
    except Exception as e:  # noqa: BLE001
        raise _err(e) from e


@router.delete("/branches/{name:path}")
def discard(name: str) -> dict[str, Any]:
    _not_while_running(name, "discard")
    try:
        _store().delete_branch(name)
    except (GraphStoreError, Exception) as e:  # noqa: BLE001
        raise _err(e) from e
    return {"deleted": name}


@router.post("/branches/{name:path}/rerun")
def rerun(name: str) -> dict[str, Any]:
    """Discard a forecast branch and run a fresh forecast with the same inputs."""
    _not_while_running(name, "re-run")
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
def atlas(branch: str = MAIN, day: int | None = None, compare: str | None = None,
          view: str = "dm") -> dict[str, Any]:
    """Live map state: province controllers, places, events and rumors on ``day`` of ``branch``.
    ``view=party`` shows only what the party knows (fog of war)."""
    if view not in ("dm", "party"):
        raise HTTPException(400, "view must be dm or party")
    try:
        return atlas_state(_store(), branch, day, compare, view)
    except Exception as e:  # noqa: BLE001
        raise _err(e) from e


@router.get("/atlas/place/{doc_id:path}")
def atlas_place(doc_id: str, branch: str = MAIN, day: int | None = None, view: str = "dm") -> dict[str, Any]:
    out = place_detail(_store(), doc_id, branch, day, "party" if view == "party" else "dm")
    if not out:
        raise HTTPException(404, "The party doesn't know this place" if view == "party" else f"{doc_id} not found on {branch}")
    return out


@router.get("/travel")
def travel_between(a: str, b: str, branch: str = MAIN, day: int | None = None) -> dict[str, Any]:
    """Route and travel days between two things (places, people, factions): on foot, horseback, an army, news."""
    from atlas.travel import for_world
    docs = _store().all_documents(branch)
    w = WorldView(docs, day if day is not None else WorldView(docs).max_day())
    r = for_world(w).between(a, b)
    if not r:
        raise HTTPException(404, "One of those has no position on the map")
    return {"from": w.name(a), "to": w.name(b), **r.summary()}


@router.get("/atlas/local-brief")
def atlas_local_brief(lon: float, lat: float, branch: str = MAIN, day: int | None = None) -> dict[str, Any]:
    """What is at a spot on the globe: biome, province and holder, nearest town, road/river/coast, creatures, events."""
    from atlas.local import brief
    try:
        return brief(_store(), lon, lat, branch, day)
    except ValueError as e:
        raise HTTPException(404, str(e)) from e


class LocalMapBody(BaseModel):
    lon: float
    lat: float
    branch: str = MAIN
    day: int | None = None
    name: str | None = None
    description: str | None = None
    style: str = ""
    animate: bool = False


@router.post("/atlas/local-map")
def atlas_local_map(body: LocalMapBody) -> dict[str, Any]:
    """Paint a battle map of a spot on the globe; poll /vtt/jobs/{job}/collect to link it where it belongs."""
    from atlas.local import paint_here
    from vtt_link import client
    if not (-180 <= body.lon <= 180 and -90 <= body.lat <= 90):
        raise HTTPException(400, "lon/lat out of range")
    try:
        return paint_here(_store(), body.lon, body.lat, branch=body.branch, day=body.day, name=body.name,
                          description=body.description, style=body.style, animate=body.animate)
    except client.VTTError as e:
        raise HTTPException(502, str(e)) from e
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


class PartyMarkBody(BaseModel):
    place: str
    state: str  # visited | heard | unknown
    branch: str = MAIN
    day: int | None = None


@router.post("/party/knowledge")
def party_knowledge(body: PartyMarkBody) -> dict[str, Any]:
    """Mark what the party knows of a place from a day: been there, heard of it, or (from now) unknown."""
    from atlas.party import mark
    from atlas.party_map import refresh_if_shown
    try:
        out = mark(_store(), body.place, body.state, branch=body.branch, day=body.day)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    # if the players are looking at the party's map, redraw it with what they now know
    out["table_refreshed"] = bool(out.get("changed")) and refresh_if_shown(_store(), body.branch, out["day"])
    return out


@router.get("/party/map.webp")
def party_map_image(branch: str = MAIN, day: int | None = None):
    """The party's overland map: the atlas as the party knows it, fogged, as an image."""
    from fastapi.responses import Response

    from atlas.party_map import NothingKnown, render
    try:
        data, _ = render(_store(), branch, day)
    except NothingKnown as e:
        raise HTTPException(404, str(e)) from e
    return Response(data, media_type="image/webp", headers={"Cache-Control": "no-store"})


class PartyMapBody(BaseModel):
    branch: str = MAIN
    day: int | None = None


@router.post("/party/map-to-table")
def party_map_to_table(body: PartyMapBody) -> dict[str, Any]:
    """Put the party's overland map on the table (it redraws itself while it is there and the party learns more)."""
    from atlas.party_map import NothingKnown, to_table
    from vtt_link import client
    try:
        return to_table(_store(), body.branch, body.day)
    except NothingKnown as e:
        raise HTTPException(400, str(e)) from e
    except client.VTTError as e:
        raise HTTPException(502, str(e)) from e


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
    """Import an Azgaar 'Export > Export to JSON > Full' file as a new atlas."""
    path = _save_upload(file)
    store = _store()
    if not store.branch_exists(branch):
        store.create_branch(branch, MAIN)  # review a new world on its own branch before merging
    try:
        return import_world(store, path, branch=branch, place_greywater=place_greywater)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


# =========================================================================== virtual tabletop link
@router.get("/vtt/status")
def vtt_status() -> dict[str, Any]:
    from vtt_link import client
    return {**client.status(), "url": client.base_url() or None}


@router.get("/vtt/maps")
def vtt_maps() -> list[dict[str, Any]]:
    """The VTT's map library, with thumbnails re-served through this API (same origin)."""
    from vtt_link import client
    try:
        maps = client.maps()
    except client.VTTError as e:
        raise HTTPException(502, str(e)) from e
    return [{"id": m["id"], "title": m["title"], "source": m.get("source"), "media_type": m.get("media_type"),
             "width": m.get("width"), "height": m.get("height"),
             "thumb": f"/api/vtt/media/{m['thumb_url'].removeprefix('/media/')}" if m.get("thumb_url") else None}
            for m in maps]


@router.get("/vtt/media/{path:path}")
def vtt_media(path: str):
    from fastapi.responses import Response

    from vtt_link import client
    try:
        r = client.media(path)
    except client.VTTError as e:
        raise HTTPException(404, str(e)) from e
    return Response(r.content, media_type=r.headers.get("content-type", "application/octet-stream"),
                    headers={"Cache-Control": "public, max-age=3600"})


class LinkMapBody(BaseModel):
    vtt_map_id: str
    branch: str = MAIN
    day: int | None = None
    kind: str = "battle"
    rotation_deg: float = 0.0
    replace: bool = True


@router.get("/places/{place_id:path}/maps")
def place_maps(place_id: str, branch: str = MAIN, day: int | None = None) -> list[dict[str, Any]]:
    from vtt_link.scene import maps_for
    return maps_for(_store(), place_id, branch, day)


@router.post("/places/{place_id:path}/maps")
def place_link_map(place_id: str, body: LinkMapBody) -> dict[str, Any]:
    from vtt_link import client
    from vtt_link.scene import link_map
    day = body.day if body.day is not None else WorldView(_store().all_documents(body.branch)).max_day()
    try:
        return link_map(_store(), place_id, body.vtt_map_id, branch=body.branch, day=day, kind=body.kind,
                        rotation_deg=body.rotation_deg, replace=body.replace)
    except client.VTTError as e:
        raise HTTPException(502, str(e)) from e
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@router.delete("/maps/{map_doc_id:path}")
def unlink(map_doc_id: str, branch: str = MAIN, day: int | None = None) -> dict[str, Any]:
    from vtt_link.scene import unlink_map
    try:
        unlink_map(_store(), map_doc_id, branch=branch, day=day)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return {"unlinked": map_doc_id}


@router.get("/places/{place_id:path}/scene")
def place_scene(place_id: str, branch: str = MAIN, day: int | None = None) -> dict[str, Any]:
    """Preview what the VTT's DM screen would receive for this place."""
    from vtt_link.scene import build_scene
    try:
        return build_scene(_store(), place_id, branch, day)
    except ValueError as e:
        raise HTTPException(404, str(e)) from e


class ToTableBody(BaseModel):
    branch: str = MAIN
    day: int | None = None
    map_doc_id: str | None = None
    mark_visited: bool = True  # the table is where the party is


@router.post("/places/{place_id:path}/to-table")
def place_to_table(place_id: str, body: ToTableBody) -> dict[str, Any]:
    """Show the place on the table: its linked battle map, and its scene on the VTT's DM screen."""
    from vtt_link import client
    from vtt_link.scene import to_table
    try:
        return to_table(_store(), place_id, body.branch, body.day, body.map_doc_id, body.mark_visited)
    except client.VTTError as e:
        raise HTTPException(502, str(e)) from e
    except ValueError as e:
        raise HTTPException(404, str(e)) from e


class PaintMapBody(BaseModel):
    branch: str = MAIN
    day: int | None = None
    style: str = ""
    animate: bool = False
    description: str | None = None  # override the generated brief


@router.get("/places/{place_id:path}/map-brief")
def place_map_brief(place_id: str, branch: str = MAIN, day: int | None = None) -> dict[str, Any]:
    """The description the VTT would paint this place from, as it stands on that branch and day."""
    from vtt_link.session import map_brief
    try:
        return map_brief(_store(), place_id, branch, day)
    except ValueError as e:
        raise HTTPException(404, str(e)) from e


@router.post("/places/{place_id:path}/paint-map")
def place_paint_map(place_id: str, body: PaintMapBody) -> dict[str, Any]:
    """Ask the VTT to paint this place as it is now; poll /vtt/jobs/{job}/collect to link the result."""
    from vtt_link import client
    from vtt_link.session import paint_map
    try:
        return paint_map(_store(), place_id, body.branch, body.day, style=body.style, animate=body.animate,
                         description=body.description)
    except client.VTTError as e:
        raise HTTPException(502, str(e)) from e
    except ValueError as e:
        raise HTTPException(404, str(e)) from e


@router.get("/vtt/jobs/{job_id}/collect")
def vtt_collect(job_id: str) -> dict[str, Any]:
    """A paint job's progress; once it's done, its map is linked to the place from the day it was painted for."""
    from vtt_link import client
    from vtt_link.session import collect
    try:
        return collect(_store(), job_id)
    except client.VTTError as e:
        raise HTTPException(502, str(e)) from e


class TokensBody(BaseModel):
    branch: str = MAIN
    day: int | None = None
    redo: bool = False


@router.post("/places/{place_id:path}/tokens")
def place_tokens(place_id: str, body: TokensBody) -> dict[str, Any]:
    """Queue portrait tokens on the VTT for the people at a place who haven't got one."""
    from vtt_link import client
    from vtt_link.session import paint_tokens
    try:
        return paint_tokens(_store(), place_id, body.branch, body.day, body.redo)
    except client.VTTError as e:
        raise HTTPException(502, str(e)) from e
    except ValueError as e:
        raise HTTPException(404, str(e)) from e


@router.get("/vtt/session-log")
def vtt_session_log() -> dict[str, Any]:
    """The table's session log as a summary for the next forecast."""
    from vtt_link import client
    from vtt_link.session import from_table
    try:
        return from_table()
    except client.VTTError as e:
        raise HTTPException(502, str(e)) from e


# =========================================================================== campaigns (one database per world)
@router.get("/campaigns")
def campaigns() -> list[dict[str, Any]]:
    return _store().list_campaigns()


class CampaignBody(BaseModel):
    id: str


@router.post("/campaigns/active")
def campaign_activate(body: CampaignBody) -> dict[str, Any]:
    from graph.store import set_active_campaign
    try:
        set_active_campaign(body.id)
    except GraphStoreError as e:
        raise _err(e) from e
    return {"active": body.id}


@router.delete("/campaigns/{campaign_id}")
def campaign_delete(campaign_id: str) -> dict[str, Any]:
    store = _store()
    if campaign_id == store.db:
        raise HTTPException(400, "Switch to another campaign before deleting this one")
    try:
        store.delete_campaign(campaign_id)
    except GraphStoreError as e:
        raise _err(e) from e
    return {"deleted": campaign_id}


# =========================================================================== new-world wizard
@router.get("/wizard/spec")
def wizard_spec() -> dict[str, Any]:
    from wizard.spec import spec
    return spec()


@router.post("/wizard/upload")
def wizard_upload(file: UploadFile = File(...)) -> dict[str, Any]:
    """Stash an Azgaar Full JSON export for the wizard's 'use my Azgaar export' option."""
    import uuid as _uuid

    from atlas.azgaar import AzgaarWorld
    from wizard.builder import upload_dir
    uid = _uuid.uuid4().hex[:12]
    path = upload_dir() / f"{uid}.json"
    with open(path, "wb") as out:
        shutil.copyfileobj(file.file, out)
    try:
        w = AzgaarWorld.load(path)
    except (ValueError, json.JSONDecodeError) as e:
        path.unlink(missing_ok=True)
        raise HTTPException(400, str(e)) from e
    return {"upload_id": uid, "name": w.name, "states": len(w.live(w.raw["pack"].get("states", [])))}


@router.post("/wizard/build")
def wizard_build(answers: dict[str, Any]) -> dict[str, Any]:
    from wizard.builder import start_build
    return start_build(answers).dump()


@router.get("/wizard/jobs/{job_id}")
def wizard_job(job_id: str) -> dict[str, Any]:
    from wizard.builder import JOBS
    job = JOBS.get(job_id)
    if not job:
        raise HTTPException(404, "Unknown job")
    return job.dump()


# =========================================================================== SRD 5.2.1 reference
@router.get("/srd")
def srd_info() -> dict[str, Any]:
    from srd import catalogue
    c = catalogue.load()
    return {"attribution": c.get("attribution"), "license": c.get("license"),
            "counts": {k: len(c.get(k, [])) for k in ("monsters", "magic_items", "species", "classes", "backgrounds")}}


@router.get("/srd/entry/{entry_id:path}")
def srd_entry(entry_id: str) -> dict[str, Any]:
    from srd import catalogue
    e = catalogue.by_id(entry_id)
    if not e:
        raise HTTPException(404, "Not in the SRD catalogue")
    return e


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


@router.post("/forecasts/{run_id}/resume")
def resume_run(run_id: str) -> dict[str, Any]:
    """Continue an interrupted (backend restarted) or failed run from its last checkpoint."""
    try:
        run = resume_forecast(run_id)
    except KeyError as e:
        raise HTTPException(404, "Unknown run") from e
    except (RunBusy, ValueError) as e:
        raise HTTPException(409, str(e)) from e
    return {"run_id": run.id, "branch": run.branch}


@router.get("/forecasts/{run_id}/events")
async def forecast_events(run_id: str) -> StreamingResponse:
    """Server-sent events: live LangGraph step progress."""
    run = registry.get(run_id)
    if not run:
        raise HTTPException(404, "Unknown run")

    async def gen():
        sent = 0
        while True:
            registry.get(run_id)  # a run in another process is re-read from disk (refreshed in place)
            while sent < len(run.events):
                yield f"data: {json.dumps(run.events[sent], default=str)}\n\n"
                sent += 1
            if run.status in ("complete", "failed", "interrupted") and sent >= len(run.events):
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

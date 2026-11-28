"""Living World Engine — FastAPI entrypoint (`uvicorn main:app`)."""
from __future__ import annotations

import logging
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.routes import router
from atlas.service import bootstrap_sample
from config import get_settings
from graph.seed import seed
from graph.store import get_store

settings = get_settings()
logging.basicConfig(level=settings.log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("lwe")


def _bootstrap() -> None:
    store = get_store()
    for attempt in range(30):
        try:
            store.ping()
            break
        except Exception:  # noqa: BLE001
            log.info("Waiting for TerminusDB at %s (%s)…", settings.terminusdb_url, attempt + 1)
            time.sleep(2)
    created = store.ensure_database()
    if store.db != settings.terminusdb_db:  # a wizard-made campaign: only keep its schema current
        log.info("Active campaign: %s", store.db)
        _warm_parchment(store)
        return
    if settings.seed_on_startup and (created or not store.all_documents("main", doc_type="Character")):
        result = seed(store)
        log.info("Seeded demo world: %s documents", result["documents"])
    if settings.atlas_sample_on_startup:
        try:
            placed = bootstrap_sample(store)
            if placed:
                log.info("Imported sample atlas %s (%s documents)", placed["name"], placed["documents"])
        except Exception:  # noqa: BLE001 - the dashboard still works without a map
            log.exception("Sample atlas import failed")
    _warm_parchment(store)


def _warm_parchment(store) -> None:
    """Draw the active campaign's parchment tiles ahead (a no-op for those already cached)."""
    try:
        from atlas.parchment import warm_in_background
        for a in store.all_documents("main", doc_type="WorldAtlas"):
            warm_in_background(a.get("uid") or a["@id"].split("/", 1)[1])
    except Exception:  # noqa: BLE001 - tiles are still drawn when asked for
        log.exception("Could not start drawing parchment tiles ahead")


@asynccontextmanager
async def lifespan(_: FastAPI):
    _bootstrap()
    log.info("Living World Engine ready (MOCK_LLM=%s)", settings.mock_llm)
    yield


app = FastAPI(title="Living World Engine", version="0.1.0", lifespan=lifespan,
              description="Bi-temporal knowledge graph + multi-agent world simulator for tabletop RPGs.")
app.add_middleware(CORSMiddleware, allow_origins=[o.strip() for o in settings.cors_origins.split(",")],
                   allow_methods=["*"], allow_headers=["*"])
app.include_router(router)

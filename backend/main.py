"""Living World Engine — FastAPI entrypoint (`uvicorn main:app`)."""
from __future__ import annotations

import logging
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.routes import router
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
    if settings.seed_on_startup and (created or not store.all_documents("main", doc_type="Character")):
        result = seed(store)
        log.info("Seeded demo world: %s documents", result["documents"])


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

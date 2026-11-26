# 🕯️ Living World Engine

An open-source (MIT) **living world engine for tabletop RPGs**. It ingests your campaign lore into a
version-controlled, **bi-temporal knowledge graph** (TerminusDB), forks the world at the end of each
session, lets NPCs and factions act on their own desires, needs, fears and *beliefs* in a
**LangGraph multi-agent simulation**, and hands the DM a 4-part forecast briefing with full
branching, diffing, cherry-picking, merging and time-travel.

```
 Session notes ──► fork main ──► apply party actions ──► ┌─ activation radius (2 hops + goal clocks) ─┐
                     (branch)       (events, trust,       │  agent turns (BDI, KNOWS-only context)     │ × rounds
                                     knowledge)           │  world arbiter (resolve clashes → events)  │
                                                          └─ rumors (public/leaked → taverns)  ────────┘
                                                                          │
              DM Briefing ◄── diff main vs forecast/session-N ◄── finalize report
     merge to canon · cherry-pick events · discard & re-run · time-travel any branch/day/commit
```

Everything runs on a laptop with **`MOCK_LLM=true`** (deterministic, rule-based BDI agents — no GPU),
or against any **OpenAI-compatible** endpoint (Ollama, vLLM, LM Studio, OpenAI…) on your LAN.

---

## Quick start

Requirements: Docker with Compose v2.

```bash
cp .env.example .env          # defaults: MOCK_LLM=true, demo world seeded on first start
docker compose up --build
```

| What | URL |
|---|---|
| DM dashboard | http://localhost:5173 |
| Backend API (OpenAPI docs) | http://localhost:8000/docs |
| Virtual tabletop: DM screen · player table | http://localhost:8080/dm · http://localhost:8080/table |
| TerminusDB dashboard (admin / `root`) | http://localhost:6363 |

On first start the backend creates the `living_world` database, applies the schema and seeds
**Greywater**, a small harbour town (see [Demo world](#demo-world)). Then:

1. **Session Forecaster** → *Use demo summary* → **Run Forecast**. Watch each LangGraph node run live.
2. **Open DM Briefing** → read the four sections; tick events to **Cherry-Pick**, or **Merge to Canon**.
3. **World Atlas** → spin the globe; pick the forecast branch, tick *Compare with main* and press ▶
   to watch provinces change hands day by day.
4. **Time Travel** → pick a branch, drag the in-world day slider, or pick a historic commit.
5. **Ingest** → drop Obsidian notes / PDFs / Word docs (try the files in `sample_data/`).
6. **✦ New World** → answer (or roll) five Guidebook-style steps and build a fresh campaign world; switch between
   worlds from the campaign menu in the header.
7. **On the table** → open the VTT's DM screen, generate or import a battle map, then in the atlas open a place,
   link the map under *On the table* and press **Send scene to the VTT**: the map goes on the table and the DM screen's
   **World** panel shows who is there, tensions, rumors and SRD stat lines.

Ports already in use? Override `FRONTEND_PORT`, `BACKEND_PORT`, `TERMINUSDB_PORT` in `.env`.

Re-seed from scratch (deletes all branches):

```bash
docker compose exec backend python /app/scripts/seed_demo_world.py --reset
```

### Using a real LLM

```ini
# .env
MOCK_LLM=false
LLM_BASE_URL=http://host.docker.internal:11434/v1   # Ollama on the Docker host (or http://<lan-ip>:11434/v1)
LLM_API_KEY=ollama
LLM_MODEL=llama3.1:8b                               # anything that follows JSON instructions well
LLM_JSON_MODE=json_object                           # json_schema for vLLM/OpenAI strict schemas; none to disable
```

`docker compose up -d --build backend` to apply. Every LLM call returns a validated Pydantic object;
invalid JSON is re-prompted with the validation error (`LLM_MAX_RETRIES`). Agent turns run in
parallel (`LLM_CONCURRENCY`).

### Optional heavy ingestion extras

| Feature | Enable | Without it |
|---|---|---|
| `docling` (high-fidelity PDF/DOCX/PPTX → Markdown) | `INSTALL_DOCLING=true` | PDF via `pypdf`, DOCX via `python-docx` |
| `faster-whisper` (local audio transcription) | `INSTALL_WHISPER=true`, `WHISPER_MODEL=base` | audio uploads are rejected |

These are build args (docling pulls in PyTorch, ~2 GB): set them in `.env`, then `docker compose build backend`.

---

## How it works

### 1. Bi-temporal, motivational graph (`backend/graph/schema.py`)

Two independent time axes:

* **Transaction time** — TerminusDB commits. Every write (seed, ingest, each simulation step, merge,
  cherry-pick) is a commit on a branch, so you can diff, branch, merge and read the DB *as it was
  recorded* at any commit.
* **Valid (in-world) time** — every node and edge has `in_world_start` / `in_world_end` (campaign
  day integers) plus `source_session`. Facts are **never deleted**: when Vane's trust in the party
  drops, the old `RelationshipEdge` gets `in_world_end` + `ended_by_event` and a new edge starts.
  A snapshot at day *D* keeps docs with `start ≤ D < end`.

| Kind | Types |
|---|---|
| Nodes | `Character`, `Faction`, `Location`, `Item`, `Monster`, `Event`, `Rumor`, `Goal` (all inherit `Entity`) |
| `DesireEdge` | `from_id` → `target_id` / `target_text`, `kind` WANTS·NEEDS·FEARS, `priority` 0–1 |
| `ControlEdge` | `from_id` → `target_id`, `kind` CONTROLS·OWNS |
| `RelationshipEdge` | `from_id` → `to_id`, `trust_score` −100…+100, `label` (ally, rival, debt…) |
| `EpistemicEdge` | `agent_id` → `fact_or_event_id`, `kind` KNOWS·BELIEVES, `confidence` 0–1 |
| Meta | `Alias` (entity-resolution table), `ForecastRun` (inputs + report of each forecast) |

Edges carry `source_event` / `ended_by_event` provenance links, which is what makes cherry-picking an
event *together with its consequences* possible. `Goal`s are progress clocks
(`clock_progress/clock_segments`, ticking every `ticks_every_days`).

**Epistemic rule.** An agent's prompt is built by `WorldView.agent_context()` and contains only:
itself, its desires, goals, holdings, relationships, and nodes it is linked to via `KNOWS`/`BELIEVES`
(own or its faction's). Intents that target anything else are downgraded to `observe` and reported as
*epistemic violations* in the briefing. Secret acts are only *suspected* (`BELIEVES 0.5`) by victims
until they investigate; rumors spread `BELIEVES` edges to locals.

### 2. Ingestion (`backend/ingestion/`)

| Input | Path |
|---|---|
| Obsidian / Markdown | YAML frontmatter → node properties (`type`, `aliases`, `faction`, `location`, `wants`, `needs`, `fears`, `controls`, `owns`, `allies`, `rivals`, `enemies`, `knows`…); `[[Wikilinks]]` → baseline edges (an agent's note linking `[[X]]` ⇒ it KNOWS X; unknown links become stub nodes) |
| PDF / Word / HTML | `docling` → Markdown (fallback `pypdf` / `python-docx`), chunked by heading |
| Session notes / audio | text, or `faster-whisper` transcript; facts stamped with the in-world day |
| Extraction | LLM → `{nodes, edges}` (incl. inferred WANTS/NEEDS and KNOWS). Mock mode uses pattern heuristics ("X fears Y", "X owes Y"…) |
| Entity resolution | alias table + honorific stripping + `rapidfuzz` — "The Baron", "Lord Vane", "Baron Vane" → `Character/lord-aldric-vane`; new surface forms are saved as `Alias` docs |

Each file is one commit. Ingest into a scratch branch (e.g. `ingest-session-15`), review it in
Branches / Time Travel, then merge.

### 3. Simulation (`backend/simulation/engine.py`)

A compiled LangGraph `StateGraph`:

1. **`fork_canon`** — branch `main` → `forecast/session-{N}-{timestamp}` (records the fork commit).
2. **`apply_session_delta`** — the summary becomes party-action `Event`s, item transfers, deaths,
   trust shifts and `KNOWS`/`BELIEVES` for participants, victims and witnesses.
3. **`select_active_agents`** — wakes Characters/Factions within **2 hops** (`ACTIVATION_HOPS`) of
   affected nodes (via relationship/control/desire/membership/location links), plus any agent whose
   high-priority goal clock ticks during the round.
4. **`run_agent_turns`** — BDI: each agent sees only its epistemic context and returns an
   `AgentIntent {action, target_id, resources_spent, reasoning, public_or_secret}`.
5. **`world_arbiter`** — resolves simultaneous intents (contests by resources/influence,
   relationships), emits `WorldEvent`s with relationship shifts, control changes, goal progress,
   new goals/fears, deaths, and knowledge gained; completed goal clocks fire consequences.
6. **`propagate_rumors`** — public/leaked events become `Rumor`s in taverns/settlements with
   `accuracy` (True / Exaggerated / False) and a ready-to-read line of dialogue.

Steps 3–6 loop once per round (`ROUND_LENGTH_DAYS`, max `MAX_ROUNDS`) across the forecast window,
so round-1 events become round-2 knowledge and motives: genuine ripples. Each step commits to the
forecast branch, so the branch log is an audit trail of the simulation.

### 4. World Atlas (`backend/atlas/`, `frontend/src/views/Atlas.tsx`)

A MapLibre GL **globe** of the campaign planet that shows the simulation for any branch and in-world day.

* **World source:** [Azgaar's Fantasy Map Generator](https://github.com/Azgaar/Fantasy-Map-Generator) (MIT). Step-by-step: [docs/azgaar-field-guide.md](docs/azgaar-field-guide.md).
  Export with *Export → Export to JSON → **Full*** (Save keeps a separate `.map` for editing in Azgaar), then
  `python scripts/import_azgaar.py MyWorld_Full.json [--branch import-1] [--place-greywater]`
  or `POST /api/atlas/import`. Coordinates use Azgaar's own projection (`lonW + x/width·lonT`, `latN − y/height·latT`).
* **What the import creates:** realms → `Faction`s (colour, treasury, influence from size), provinces → `Province`
  + a bi-temporal `CONTROLS` edge, burgs → `Location`s with `lon`/`lat`, diplomacy → trust, plus border claims
  (WANTS), war goals with clocks and knowledge of neighbours — so imported realms act in forecasts and **borders move**.
* **Static layers** (land, biomes, provinces, rivers, roads, burgs) are dissolved from Azgaar's cells with Shapely and
  written as GeoJSON; inside Docker **tippecanoe** packs them into one **PMTiles** file served with HTTP range requests.
  Without tippecanoe the atlas loads the GeoJSON directly.
* **Live layers** come from `GET /api/atlas?branch=&day=&compare=`: province controllers (applied with MapLibre
  feature-state, so dragging the day slider recolours borders without re-tiling), places with who is there,
  events of the last 30 days (ringed when new on the branch), and tavern rumors. `GET /api/atlas/place/{id}` gives
  a place's holder, people, history and rumors.
* **First run:** with `ATLAS_SAMPLE_ON_STARTUP=true` the backend generates a small sample planet *in Azgaar's Full JSON
  format* (`backend/atlas/sample_world.py`; hex cells instead of Voronoi), imports it, and places Greywater on a port
  town as a vassal of the local realm. Azgaar's repo only ships `.map` saves, which don't store cell polygons.

### 5. New-world wizard and campaigns (`backend/wizard/`, `frontend/src/views/NewWorld.tsx`)

**✦ New World** builds a complete campaign world in a few seconds, asking questions in the decision order of the
AD&D *World Builder's Guidebook* (the order only; wording, options and tables are the engine's own):

| Step | You choose | The engine creates (one `[setup]` commit each) |
| --- | --- | --- |
| Approach & focus | Planet-in, Kingdom-first, Local-out or Literary; a campaign focus (seafaring, court intrigue, frontier, dying world, holy war…) that pre-fills every step | — |
| 1 World | name, seed, climate band, detail, or *use my Azgaar export* | the planet and its atlas layers (PMTiles in Docker) |
| 2 Continents | shape of the land | named continents and islands |
| 3 Kingdoms | realms, how hostile, cultures and naming style, peoples (SRD species), religions | realms, provinces, towns, diplomacy, border claims and war goals; a ruler per realm; churches, cults and their gods |
| 4 Local area | starting town, tavern, the local power struggle, notable NPCs, party name, rarity of a contested magic item | two rival local factions, NPCs with conflicting WANTS / NEEDS / FEARS, debts and secrets, goal clocks, an SRD magic item both sides want, the party, opening rumors |
| 5 History | years of history, ages, a defining catastrophe, a hero's saga, monster density, party level band | founding and wars (negative in-world days), the catastrophe and its lair, a legendary hero, SRD monsters in every province's wilds |

Every 🎲 rolls a field; *Surprise me* rolls a whole step. The approach changes the order you answer in; the world is
always built planet-first because a town needs a planet to sit on.

**Campaigns.** Each world is its own TerminusDB database, so worlds never mix. Switch between them from the menu in the
header (`GET /api/campaigns`, `POST /api/campaigns/active`); the choice is remembered across restarts, and running
forecasts and imports keep writing to the campaign they started in. The Greywater demo stays the default campaign.

### 6. SRD 5.2.1 content (`backend/srd/`)

A read-only catalogue built from the System Reference Document 5.2.1 by `scripts/build_srd_catalogue.py`: 328 monster
and animal stat blocks, 259 magic items, 9 species, 12 classes, 4 backgrounds and price lines. The wizard uses it for
NPC species, classes and stat-block templates (`srd_ref`), each biome's bestiary within a challenge-rating window, and
the contested magic item. See [backend/srd/README.md](backend/srd/README.md).

### 7. DM dashboard (`frontend/`)

* **Session Forecaster** — session #, start/end day, days until next session, summary (paste,
  upload, or transcribe audio); live per-node progress via server-sent events.
* **DM Briefing** (`main` vs forecast diff):
  1. *Direct Ripple Effects* — Party action → affected nodes → NPC reaction trees, with reasoning.
  2. *Off-Screen Events* — chronological log; tick to cherry-pick.
  3. *Relationship & Motivation Shifts* — trust before → after (with causes), goal clocks, new
     WANTS/NEEDS/FEARS, control changes, treasury, casualties, what each agent learned.
  4. *Tavern Rumors & Hooks* — filter by location and accuracy; copy dialogue.
  Plus agent intents/activation trace and the raw TerminusDB JSON patch.
* **Branch controls** — Merge to Canon, Cherry-Pick, Discard & Re-run (fresh seed), Discard.
* **Time Travel** — any branch × any in-world day × any commit; force-directed graph,
  click an agent to see exactly what its prompt would contain.

### 8. Virtual tabletop (`vtt/`)

The tabletop half of the project: a FastAPI + SQLite service with a DM screen and a player table synced over
WebSockets, AI or imported battle maps, fog and tokens. See [vtt/README.md](vtt/README.md). It runs as the `vtt`
service in compose (port `VTT_PORT`, default 8080; its settings are the `VTT_*` variables in `.env.example`).

The world engine talks to it server-side through `VTT_URL` (`http://vtt:8000` in compose; leave empty to switch the
link off). The link is push-only, so the VTT never needs to know about TerminusDB:

* **Maps are world facts.** Linking a VTT map to a place writes a bi-temporal `Map` document (from an in-world day,
  replacing the place's previous map) at true scale, read from the VTT's grid (AI maps are 140 ft across). The atlas
  draws each linked map's footprint on the globe, georeferenced to its place, when you zoom in close.
* **Scenes are DM-only.** *Send scene to the VTT* pushes the place's current map to the table and a self-contained
  scene to the DM screen for the chosen branch and day: holder, province and realm; who is there with what they want
  and fear; tensions; rumors to read aloud; creatures and NPC stat lines from the SRD; recent non-secret events; and
  plans in motion. Players never receive it.

**Merging.** If `main` hasn't moved since the fork, merge is a fast-forward rebase. Otherwise the
fork-point→forecast patch is applied with TerminusDB's `apply`; if that conflicts (e.g. you
cherry-picked some events first), it falls back to a document-level 3-way merge in one commit,
with the forecast winning on documents it changed.

---

## API

Interactive docs at `/docs`. Highlights:

| Method | Path | |
|---|---|---|
| GET | `/api/health` | TerminusDB status, mock mode, optional extras |
| POST | `/api/forecasts` | `{session_number, start_day, end_day, forecast_days, session_summary, base_branch}` → `{run_id, branch}` |
| GET | `/api/forecasts/{id}/events` | SSE stream of LangGraph progress |
| GET | `/api/diff?base=main&compare=…` | the 4-part briefing |
| GET | `/api/diff/raw?base=…&compare=…` | TerminusDB native diff |
| POST | `/api/branches/{name}/merge` · `/cherry-pick` · `/rerun` | branch controls |
| DELETE | `/api/branches/{name}` | discard |
| GET | `/api/branches/{name}/log` | commit history |
| GET | `/api/world/snapshot?branch=&day=&commit=` | time travel |
| GET | `/api/world/agent/{id}?branch=&day=` | an agent's epistemic context |
| GET | `/api/atlas?branch=&day=&compare=` | live map state: territory, places, events, rumors |
| GET | `/api/atlas/place/{id}` · `/api/atlas/files/{slug}/{file}` | place detail · static layers (GeoJSON / PMTiles) |
| POST | `/api/atlas/import` | import an Azgaar Full JSON export |
| GET · POST | `/api/wizard/spec` · `/api/wizard/build` · `/api/wizard/jobs/{id}` · `/api/wizard/upload` | new-world wizard |
| GET · POST · DELETE | `/api/campaigns` · `/api/campaigns/active` · `/api/campaigns/{id}` | campaigns (one database per world) |
| GET | `/api/srd` · `/api/srd/entry/{id}` | SRD catalogue info and entries |
| GET | `/api/vtt/status` · `/api/vtt/maps` · `/api/vtt/media/{path}` | tabletop link status, its maps, proxied thumbnails |
| GET · POST | `/api/places/{id}/maps` | maps linked to a place · link one `{vtt_map_id, day, branch, kind, rotation_deg}` |
| DELETE | `/api/maps/{id}` | unlink (ends the map on that day) |
| GET · POST | `/api/places/{id}/scene` · `/api/places/{id}/to-table` | preview the DM scene · push map + scene to the VTT |
| POST | `/api/ingest/files` · `/ingest/text` · `/ingest/audio` · `/ingest/transcribe` | ingestion |
| POST | `/api/admin/seed?reset=true` | reset to the demo world |

---

## Demo world

Greywater, end of session 13, day 10. **House Vane** (Lord Aldric Vane, Captain Hollis Brand) —
broke, wants the docks, fears the Sunken Ledger surfacing — vs **the Ashen Guild** (Sela Marrow) —
holds the docks, wants the ledger as blackmail. Wenna Tull keeps the Drowned Lantern neutral;
Brother Callum wants the Baron exposed; Brand is secretly in debt to the Guild. Four locations plus
the tavern, two items, a marsh hag, three goal clocks, rumors already circulating, and the party
(**the Lantern Company**). `sample_data/` has Obsidian notes and session notes to ingest on top.

---

## Development without Docker

```bash
# TerminusDB
docker run -d -p 6363:6363 -e TERMINUSDB_ADMIN_PASS=root terminusdb/terminusdb-server:v11.1.14

# Backend (Python 3.11+)
python -m venv .venv && . .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r backend/requirements.txt pytest
python scripts/seed_demo_world.py --reset
cd backend && uvicorn main:app --reload --port 8000

# Frontend
cd frontend && npm install && npm run dev            # http://localhost:5173 (proxies /api → :8000; override with VITE_API_PROXY)

# Virtual tabletop (optional; then set VTT_URL=http://127.0.0.1:8090 for the backend)
cd vtt/backend && pip install -r requirements.txt && MOCK_AI_PIPELINE=true uvicorn app.main:app --port 8090
cd vtt/frontend && npm install && VTT_BACKEND=http://localhost:8090 npx vite --port 5175

# Tests (unit + integration against a throwaway TerminusDB database)
cd backend && pytest
cd vtt/backend && pytest
```

Layout:

```
backend/
  graph/        schema.py · store.py (TerminusDB: branches, diff, merge, cherry-pick) · world.py (temporal/epistemic views)
  atlas/        azgaar.py (Azgaar Full JSON importer) · sample_world.py · service.py (tiles, live map state)
  wizard/       spec.py (steps, choices, focuses) · builder.py (answers -> campaign, one commit per step)
  srd/          srd_5_2_1.json (generated) · catalogue.py (lookups, biome bestiary, NPC templates)
                builders.py · briefing.py (4-part diff) · seed.py
  simulation/   engine.py (LangGraph) · models.py (AgentIntent, WorldEvent…) · mutations.py · prompts.py · runs.py
  ingestion/    markdown.py · documents.py · audio.py · extract.py · resolution.py · pipeline.py
  llm/          client.py (OpenAI-compatible, validated JSON) · mock.py (deterministic BDI)
  vtt_link/     client.py (VTT HTTP client) · scene.py (map links, footprints, DM scenes)
  api/routes.py · main.py · config.py · tests/
frontend/src/   App.tsx · api.ts · ui.tsx · views/{Forecaster,Briefing,Atlas,Branches,TimeTravel,Ingest,NewWorld}.tsx
scripts/seed_demo_world.py · scripts/import_azgaar.py · scripts/build_srd_catalogue.py
vtt/            the virtual tabletop (backend/ FastAPI + SQLite, frontend/ React DM screen and table)
```

## Notes & limitations

* TerminusDB branch ids can't contain `/`, so `forecast/session-14-…` is stored as
  `forecast--session-14-…`; the API and UI accept and show the slash form.
* Worlds are loaded into memory per operation (fine for hundreds–low thousands of docs). The
  `GraphStore`/`WorldView` split keeps a move to WOQL queries local if your campaign outgrows that.
* In-world time is an integer campaign day. Character location and faction treasury are updated in
  place (history kept by commits, not by valid time).
* The forecast run registry (live progress) is in-memory; finished forecasts persist as
  `ForecastRun` docs on their branch, so briefings survive restarts.
* Mock mode is a deterministic rule-based model, not an LLM: great for testing the pipeline and UI,
  less inventive than a real model.

## SRD attribution

This work includes material from the System Reference Document 5.2.1 ("SRD 5.2.1") by Wizards of the Coast LLC,
available at https://www.dndbeyond.com/srd. The SRD 5.2.1 is licensed under the Creative Commons Attribution 4.0
International License, available at https://creativecommons.org/licenses/by/4.0/legalcode.

The engine is compatible with fifth edition.

## License

MIT — see [LICENSE](LICENSE).

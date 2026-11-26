# CLAUDE.md: Living World Engine

Read this first; it is meant to save you reading the codebase. `README.md` is the user-facing manual (features, API
table, demo); this file is what a developer needs to change the system safely.

## What it is

A D&D "living world" simulator plus a virtual tabletop (VTT), MIT, at `github.com/BriScanlon/living-world-engine`
(public). Between sessions the DM pastes what the party did; the engine forks canon into a branch, lets NPCs and
factions act for N in-world days (LangGraph, BDI agents that only reason over what they *know*), and shows a 4-part
DM briefing diff to merge or cherry-pick. A globe atlas (MapLibre, Azgaar worlds) shows territory over time; the VTT
(`vtt/`, formerly the separate repo `BriScanlon/vtt`, now archived) puts battle maps, tokens and fog on a table
screen, with a DM-only screen fed by the engine. Everything runs in **mock mode** (deterministic, no LLM/GPU).

## Services (docker compose, repo root)

| Service | Port (host) | What |
|---|---|---|
| `terminusdb` | 6363 | TerminusDB v11.1.14, the graph store (admin / `root`) |
| `backend` | 8000 | FastAPI world engine (`backend/`), `/docs` for OpenAPI |
| `frontend` | 5173 | React DM dashboard (`frontend/`), proxies `/api` to backend |
| `vtt` | 8080 | VTT: FastAPI + SQLite + WebSockets, serves its built React app (`/dm`, `/table`) |

The backend reaches the VTT at `VTT_URL=http://vtt:8000` and host services (Ollama, ComfyUI) via
`host.docker.internal` (mapped with `host-gateway`). Settings live in `.env` (copy `.env.example`); VTT settings are
prefixed `VTT_` there. Never commit `.env`.

## Repository map

```
backend/
  main.py, config.py (pydantic Settings from env), api/routes.py (all REST routes, one file)
  graph/    schema.py (TerminusDB classes), store.py (GraphStore: branches, diff, merge, cherry-pick, campaigns),
            world.py (WorldView: temporal + epistemic views), builders.py (doc constructors: entity, event, knows,
            relationship, end), briefing.py (4-part diff), seed.py (Greywater demo)
  simulation/ engine.py (LangGraph graph + nodes), models.py (Pydantic I/O for every LLM call), prompts.py,
            mutations.py (Mutator: applies WorldEventDrafts as docs), runs.py (in-memory run registry, SSE)
  llm/      client.py (OpenAI-compatible structured client), mock.py (deterministic stand-ins for every call)
  ingestion/ markdown (Obsidian), documents (pypdf/docling), audio (whisper), extract.py (LLM -> graph), resolution
  atlas/    azgaar.py (Full JSON importer -> GeoJSON + PMTiles), sample_world.py (generated planet),
            service.py (atlas_state, place_detail), party.py (party knowledge/fog), party_map.py (overland image),
            travel.py (routes, roads, sea, paces), local.py (zoom-in maps of any spot)
  wizard/   spec.py, builder.py (World Builder's Guidebook-style new-world wizard, one commit per step)
  srd/      srd_5_2_1.json (generated, CC-BY-4.0) + catalogue.py (lookups, biome bestiary, NPC templates)
  vtt_link/ client.py (HTTP to the VTT), scene.py (Map links, footprints, DM scenes), session.py (map briefs,
            paint jobs, portrait tokens, session log -> forecast)
  tests/    pytest; integration tests need a reachable TerminusDB (skip otherwise)
frontend/src/ App.tsx, api.ts (all types + calls), ui.tsx, views/{Forecaster,Briefing,Atlas,Branches,TimeTravel,
            Ingest,NewWorld}.tsx  (Atlas.tsx is the big one: globe, layers, place panel, party view, paint flows)
vtt/        backend/app/{main.py (routes + /ws), state.py (TableService: table state, fog, view, tokens, scene,
            session log), tokens.py, db.py (SQLite), hub.py (WS fan-out by role), pipeline/ (jobs, flows, comfy, llm)}
            frontend/src/{table/TablePage.tsx, dm/*.tsx panels, components/*Layer|Canvas, lib/sync.ts (WS state)}
scripts/    seed_demo_world.py, import_azgaar.py, build_srd_catalogue.py, llm_smoke.py (real-model check)
docs/       azgaar-field-guide.md (how to make/export an Azgaar world)
```

## Core model and invariants (break these and things go subtly wrong)

- **Bi-temporal documents.** Every node/edge has `in_world_start` / `in_world_end` (valid time, in-world days) plus
  TerminusDB commit history (transaction time). Never delete facts: end them with `builders.end(doc, day)` and add a
  new doc. `WorldView(docs, day)` filters to what is active on that day; `w.max_day()` is "now" on a branch.
- **Branches.** TerminusDB branch ids can't contain `/`: `forecast/session-14-…` is stored as
  `forecast--session-14-…` (`to_branch_id`); API and UI use the slash form. `main` is canon.
- **Campaigns** = one TerminusDB database each (`campaign_<slug>_<n>`); the active one is remembered in
  `<data>/active_campaign.txt`; use `graph.store.get_store()` in request code. The Greywater demo is `living_world`.
- **Writes** go through `store.upsert(docs, branch, message)`; commit messages are prefixed (`[forecast]`,
  `[vtt]`, `[party]`, `[atlas]`, `[setup]`). Ids are deterministic: `stable_uid(prefix, *parts)` and
  `builders.doc_id(type, uid)` (`"Location/the-drowned-lantern"`).
- **Epistemic rule.** Agents (Characters, Factions) may only reason over `WorldView.known_ids(agent)`: their
  KNOWS/BELIEVES `EpistemicEdge`s plus location, controls, relationships, goals. `agent_context()` is the only
  slice an agent prompt sees. Knowledge spreads via event witnesses and rumors.
- **The party** is the Faction with `is_party=True` (+ member Characters). The atlas **Party view** applies the same
  rule to the players: visited (KNOWS marks on Locations, places of events the party acted in, members' locations,
  and their parents) / heard of (anything else known) / fog. DM marks are EpistemicEdges from the slider's day.
- **Geography.** Locations/Provinces carry lon/lat on the campaign planet (Earth-sized radius). Buildings sit a few
  km off their town only so markers don't overlap: **travel treats places in one settlement as no journey**.
  `atlas.travel.coords()` resolves anything to a position (parent, location, headquarters, province, faction land).
- **Maps.** A VTT battle map linked to a place is a bi-temporal `Map` doc (`vtt_map_id`, `metres_per_px`,
  anchor lon/lat); a new link ends the previous one that day. AI maps are 140 ft = 28 five-foot squares across, so
  footprints are ~43 m and only visible at globe zoom 11+ (raster layers `fp-<id>`).
- **Engine -> VTT is push-only.** The VTT never calls the engine. Scenes (`POST /api/scene`) and the session log
  are **DM-socket only**; hidden tokens never reach `role=table` sockets (per-role `tokens` message). Token pictures
  are looked up from the VTT asset library, never trusted from clients.
- **Overland party map** (`atlas/party_map.py`) is rendered server-side with Pillow/shapely, uploaded to the VTT
  tagged `lwe-overland:<db>` (shown "contain", no battle grid), replaces older ones, and redraws itself after a
  party-knowledge mark while it is on the table.

## The forecast pipeline (simulation/engine.py)

`fork_canon -> apply_session_delta -> [select_active_agents -> run_agent_turns -> world_arbiter -> propagate_rumors]
x rounds -> finalize`. Rounds cover `ROUND_LENGTH_DAYS` each up to `MAX_ROUNDS`; activation is goal clocks plus
`ACTIVATION_HOPS` hops from what changed. Each node commits to the forecast branch (the branch log is the audit
trail). Travel: an actor acting on a far place arrives `lo + travel days` (army pace for factions in
conflict/faction_move; summary gets "(A journey of N days.)"); rumors land at `event day + news travel days`, and
locals believe from then. Events/rumors may land after the window (future-dated); out-of-window events spawn no
rumors.

## LLM

- `llm/client.py`: OpenAI SDK against `LLM_BASE_URL` (Ollama/vLLM/LM Studio/OpenAI). `structured(system, user,
  PydanticModel)` validates and re-prompts on errors (`LLM_MAX_RETRIES`). `LLM_JSON_MODE`: `json_object` |
  `json_schema` (use this with Ollama: constrained decoding) | `none`.
- Five call sites, each with a mock twin in `llm/mock.py`: session delta, agent intent, arbiter, rumors
  (`simulation/engine.py`), lore extraction (`ingestion/extract.py`). The VTT has its own optional LLM (prompt
  expansion) behind `VTT_MOCK_AI_PIPELINE`.
- **Status: never yet run end to end on a real model.** `scripts/llm_smoke.py` checks all five calls
  (`docker compose exec backend python /app/scripts/llm_smoke.py [json_schema|json_object|none] [calls…]`). A 4B
  model on CPU was too slow; the next attempt is a ROCm Ollama container on a PC with an AMD 7900 XT (20 GB),
  starting with `qwen2.5:14b-instruct`, `LLM_JSON_MODE=json_schema`, `LLM_CONCURRENCY=2`. A native Anthropic SDK
  backend was discussed as an alternative but not built (it would be a second client chosen by a setting, not a
  change to the OpenAI-compatible one).

## Running and testing

```bash
docker compose up --build                       # everything, mock mode, Greywater + sample planet on first start
cd backend && python -m pytest -q               # 44 tests; needs TerminusDB on :6363 (integration ones skip otherwise)
cd vtt/backend && python -m pytest -q           # 13 tests, self-contained (temp DATA_DIR, mock pipeline)
cd frontend && npx tsc --noEmit -p .            # and the same in vtt/frontend; `npm run build` for the VTT dist
```

- Tests that need a world use a module fixture: a throwaway `lwe_test_<hex>` DB, `seed(st, reset=True)`, then
  `import_world()` of `atlas.sample_world.generate(3)` into a tmp `ATLAS_DIR` (places Greywater on the planet);
  VTT calls are faked by monkeypatching functions on `vtt_link.client`. Copy an existing test file's fixture.
- Local dev without Docker on the original Windows PC used: backend `uvicorn main:app --port 8200` with
  `TERMINUSDB_DB=lwe_dev ATLAS_DIR=../.data-dev/atlas`, VTT backend on 8090 (`DATA_DIR` in a temp dir), frontend
  via `.claude/launch.json` on 5174 with `VITE_API_PROXY=http://127.0.0.1:8200` (8000/5173 were taken there).
- Demo world ids worth knowing: `Location/greywater`, `Location/the-drowned-lantern`, `Location/vane-manor`,
  `Location/greywater-docks`, `Location/blackfen-marsh`; party "The Lantern Company"; NPCs Lord Aldric Vane,
  Sela Marrow (Ashen Guild), Captain Hollis Brand, Wenna Tull, Brother Callum. Canon is day 10.

## Gotchas learned the hard way

- On the original Windows PC, application control blocks `.exe` launchers in the venv and node_modules: run
  `python -m pytest`, not `pytest`; `gh` and Docker live in WSL. Git Bash mangles `/mnt/...` paths and some long
  heredocs: write patch scripts to a file and run them.
- Browser-pane testing of the atlas: a hidden pane has no `requestAnimationFrame`, so inject
  `window.requestAnimationFrame = cb => setTimeout(() => cb(performance.now()), 16)` before opening the atlas, and
  open it with the nav button (the app reads the hash only on load; navigating to a new hash does not reload).
  `window.__atlasMap` is the MapLibre map in dev.
- React StrictMode runs effects twice: a "mounted" ref must be set to true inside the effect, not only cleared in
  cleanup (this broke the paint-polling loop once). Atlas hot reload can leave MapLibre in a "Style is not done
  loading" crash; reload the page.
- MapLibre GeoJSON sources fetch in a worker: use absolute URLs (`location.origin + …`).
- The sample planet is coarse (22 huge provinces), so trips take months; Azgaar worlds are finer.
- `atlas_state(day=None)` means "max day on the branch", so ended facts drop out.

## Conventions

- Python 3.11, type hints, Pydantic for every LLM I/O and API body; routes stay in `api/routes.py` with lazy
  imports of heavier modules. Frontend: React + TypeScript + Tailwind, all API types and calls in `api.ts`.
- Each feature: tests, README section (+ API table row + layout line), a feature branch, one PR into `main` on
  `BriScanlon/living-world-engine`. Commit messages end with a `Co-Authored-By:` line for the assistant; PR bodies
  end with the Claude Code attribution line.
- Keep mock mode working for every new LLM call (add a deterministic twin in `llm/mock.py`).
- The design doc with phases, status and future epics is a Claude artifact:
  `https://claude.ai/artifact/AbrrzUUhNba6zc2wuk8MwU` (World Atlas: Living World Engine × VTT, Design).

## Status (2026-11-26)

Done and merged: engine core (forecast, briefing, merge/cherry-pick, time travel, ingestion), World Atlas (globe,
Azgaar import, PMTiles), new-world wizard + SRD 5.2.1, VTT link (map footprints, DM scenes), world-aware AI maps,
portrait tokens, session log -> forecast, tokens on the table, party fog of war, party overland map on the table,
travel times (roads, sea, SRD paces) in forecasts and atlas, zoom-in maps of any spot. All six design phases done.

Open: PR #10 (`scripts/llm_smoke.py` + README "Running with a real model"). Next: run it on the GPU PC and fix
whatever the real model breaks (prompt clarity, schema size, timeouts, concurrency).

Backlog: real map labels on the globe (needs bundled fonts/glyphs), battle strength from SRD challenge ratings,
forecast runs surviving a backend restart (registry is in memory), "Open in VTT" from the briefing, LLM-written
wizard prose; epics: homebrew catalogue (custom creatures/species/items beside the SRD), native world generation
instead of Azgaar.

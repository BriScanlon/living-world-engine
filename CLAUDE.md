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
| `terminusdb` | 127.0.0.1:6363 | TerminusDB v11.1.14, the graph store (admin / `root`); localhost only, not on the LAN |
| `backend` | 8000 | FastAPI world engine (`backend/`), `/docs` for OpenAPI |
| `frontend` | 5173 | React DM dashboard (`frontend/`), proxies `/api` to backend |
| `vtt` | 8080 | VTT: FastAPI + SQLite + WebSockets, serves its built React app (`/dm`, `/table`) |
| `fmg` | 8070 | Azgaar's Fantasy Map Generator (MIT), self-hosted: `fmg/Dockerfile` builds upstream tag `FMG_REF` |

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
            mutations.py (Mutator: applies WorldEventDrafts as docs), runs.py (run registry on disk, SSE),
            strength.py (battle strength from challenge ratings: Forces.side/lair/contest)
  llm/      client.py (OpenAI-compatible structured client), mock.py (deterministic stand-ins for every call)
  ingestion/ markdown (Obsidian), documents (pypdf/docling), audio (whisper), extract.py (LLM -> graph), resolution,
            catalogue_link.py (mentions -> SRD/homebrew srd_ref, challenge, species)
  atlas/    azgaar.py (Full JSON importer -> GeoJSON + PMTiles), sample_world.py (generated planet or region; `mesh="voronoi"` uses `atlas/worldgen/`),
            worldgen/ (native generation, epic #45: mesh.py Voronoi cells, terrain.py heightmap + plates,
            water.py priority-flood lakes, drainage, rivers + tributaries, harbours, climate.py temperature,
            wind-borne rain with rain shadows, Whittaker biomes, polities.py habitability, cultures, realms,
            towns + ports, balanced provinces, routes.py roads/trails/highways and sea lanes),
            service.py (atlas_state, place_detail), party.py (party knowledge/fog), party_map.py (overland image),
            travel.py (routes, roads, sea, paces), local.py (zoom-in maps of any spot)
  wizard/   spec.py, builder.py (World Builder's Guidebook-style new-world wizard, one commit per step),
            prose.py (real model rewrites template descriptions in a 6th `[setup] prose` commit; public facts only)
  srd/      srd_5_2_1.json (generated, CC-BY-4.0) + catalogue.py (lookups, biome bestiary, NPC templates,
            homebrew layer) + homebrew.py (DM's own creatures/peoples/items: library, import/export, world copies)
  vtt_link/ client.py (HTTP to the VTT), scene.py (Map links, footprints, DM scenes), session.py (map briefs,
            paint jobs, portrait tokens, session log -> forecast)
  tests/    pytest; integration tests need a reachable TerminusDB (skip otherwise)
frontend/src/ App.tsx, api.ts (all types + calls), ui.tsx, views/{Forecaster,Briefing,Atlas,Branches,TimeTravel,
            Ingest,NewWorld}.tsx  (Atlas.tsx is the big one: globe, layers, place panel, party view, paint flows)
vtt/        backend/app/{main.py (routes + /ws), state.py (TableService: table state, fog, view, tokens, scene,
            session log), tokens.py, db.py (SQLite), hub.py (WS fan-out by role), pipeline/ (jobs, flows, comfy, llm)}
            frontend/src/{table/TablePage.tsx, dm/*.tsx panels, components/*Layer|Canvas, lib/sync.ts (WS state)}
scripts/    seed_demo_world.py, import_azgaar.py, build_srd_catalogue.py, llm_smoke.py (each LLM call once),
            forecast_check.py (a whole forecast on the real model, timed per step and per call type),
            move_demo_to_region.py (move an existing demo off the old planet map)
docs/       azgaar-field-guide.md (how to make/export an Azgaar world), worldgen.md (native generation, knobs)
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
  `[vtt]`, `[party]`, `[atlas]`, `[setup]`). Ids are deterministic: `graph.schema.stable_uid(prefix, *parts)` and
  `graph.schema.doc_id(type, uid)` (`"Location/the-drowned-lantern"`).
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
- **Homebrew** entries are `CatalogueEntry` docs (meta type; `home:<uid>`, SRD-shaped JSON in `data`). The library
  is its own database (`HOMEBREW_DB`, not listed as a campaign); the wizard copies it into each new world, and
  `homebrew.overlay_entries(store)` = the campaign's copies, then the library. Code that resolves `srd_ref` or picks
  from the catalogue must run inside `with catalogue.homebrew(entries):` (a ContextVar; set it inside worker
  threads): `data()` then puts homebrew first and drops SRD entries it `overrides`. `srd_ref` holds `home:` ids too
  (the design doc's rename to `catalogue_ref` was not done).
- **Overland party map** (`atlas/party_map.py`) is rendered server-side with Pillow/shapely, uploaded to the VTT
  tagged `lwe-overland:<db>` (shown "contain", no battle grid), replaces older ones, and redraws itself after a
  party-knowledge mark while it is on the table.

## The forecast pipeline (simulation/engine.py)

`fork_canon -> apply_session_delta -> [select_active_agents -> run_agent_turns -> world_arbiter -> propagate_rumors]
x rounds -> finalize`. Rounds cover `ROUND_LENGTH_DAYS` each up to `MAX_ROUNDS`; activation is goal clocks plus
`ACTIVATION_HOPS` hops from what changed. Each node commits to the forecast branch (the branch log is the audit
trail).

**Durable runs** (`simulation/runs.py`): every run is a JSON file in `RUNS_DIR` (default `runs/` beside the atlas
folder) holding its request, campaign, status, events, result and a checkpoint: the merged `SimState` after the last
finished node plus the next node (`node()` wrapper writes it; `ADDITIVE` keys are appended like LangGraph's
reducers). On start-up, `queued`/`running` runs become `interrupted`. `resume_forecast(id)` (`POST
/api/forecasts/{id}/resume`) restarts the graph at the checkpoint's next node through a conditional START edge
(`resume_at`), on the run's own campaign. Re-running a cut-off node is safe because writes use deterministic ids.
Several processes share the run files (backend, `forecast_check.py` via `docker compose exec`), so runs have an
`owner` (host, pid, boot nonce) and a `heartbeat_at` refreshed every 15 s by a thread while running: a process never
writes to another live process's run, re-reads it from disk when looked at, and marks it `interrupted` only after
90 s without a heartbeat. `registry.claim()` (lock file) takes a run over before resuming; `RunBusy` -> HTTP 409.
Branch controls (merge, cherry-pick, discard, re-run) refuse a branch with an active run (`_not_while_running`).
A new node must be added to `NEXT`/`_next_node`, or resumes will route wrongly. Travel: an actor acting on a far place arrives `lo + travel days` (`journey_pace`: chosen
from the intent's action, not the event type: people walk, factions march only to attack/seize/fortify abroad,
ride otherwise, and need no journey at home; summary gets "(A march|ride|journey of N days.)"); rumors land at `event day + news travel days`, and
locals believe from then. Events/rumors may land after the window (future-dated); out-of-window events spawn no
rumors. Goals that take a place or item (`takes_control`) are finished only by an event that wins the owner the
target, or once it's held (`capped_goal_progress`): off-screen ticks stop one segment short.

## LLM

- `llm/client.py`: OpenAI SDK against `LLM_BASE_URL` (Ollama/vLLM/LM Studio/OpenAI). `structured(system, user,
  PydanticModel)` validates against the model. `LLM_MAX_RETRIES` is the only retry policy (SDK `max_retries=0`):
  invalid JSON is corrected in conversation, a reply cut off at `LLM_MAX_TOKENS` is retried fresh, timeouts/5xx
  retry, other 4xx raise; every attempt is logged (time, finish reason, tokens). `LLM_JSON_MODE`: `json_object` |
  `json_schema` (use this with Ollama: constrained decoding) | `none`. `LLM_REASONING_EFFORT` (`none`..`high`) is
  sent as `reasoning_effort` when set; `none` stops a reasoning model (qwen3.8) thinking before its JSON. In
  `json_schema` mode the system prompt carries `schema_outline()` (~1K chars) instead of the full schema (~5K). A
  `length` finish well short of `LLM_MAX_TOKENS` means the **context window** ran out: it raises at once, naming the
  prompt size (`LLM_CONTEXT_TOKENS`, if set, also warns before sending).
- **Prompts** (`simulation/prompts.py`) are compact JSON (`_dump`). The arbiter is called per group from
  `arbiter_groups()`: intents sharing an agent, target or the target's controller are resolved together, unrelated
  groups are packed into calls of at most `MAX_GROUP` (6) intents, run in parallel up to `LLM_CONCURRENCY`; intents
  reach it through `brief_intent()` (no empty fields, reasoning capped at 200 chars). Mock mode calls
  `mock.arbiter` once per round as before.
- **Battle strength** (`simulation/strength.py`): hostile intents (seize/steal/sabotage/attack) get attacker and
  defender forces, strength = `10*log2(1+XP/100)` over SRD encounter XP by CR (own `challenge` > `srd_ref` >
  Monster `threat` > NPC role via `ROLE_TEMPLATES`/`ROLE_WORDS` > level/2 > commoner). The mock arbiter adds it to
  `_power`; `arbiter_user` adds a `forces` list. `world_arbiter` builds prompts on the main thread inside the
  homebrew layer (ContextVars don't reach the pool's threads), so homebrew CRs count.
- Five call sites, each with a mock twin in `llm/mock.py`: session delta, agent intent, arbiter, rumors
  (`simulation/engine.py`), lore extraction (`ingestion/extract.py`). The VTT has its own optional LLM (prompt
  expansion) behind `VTT_MOCK_AI_PIPELINE`.
- **Status: whole forecasts run on the real model** (24K context, before the #26 arbiter split; the split is
  not yet checked on 16K). `scripts/llm_smoke.py` checks all five calls
  (`docker compose exec backend python /app/scripts/llm_smoke.py [json_schema|json_object|none] [calls…]`). First
  run on a 7900 XT (20 GB) with native Windows ROCm Ollama, `qwen2.5:14b-instruct`, 32K context, `json_schema`:
  4/5 pass in 3 to 8 s each; extraction copies the KNOWN ENTITIES list (issue #13). The user has since moved that
  PC to `qwen3.8:27b-16k` (qwen3.8:27b at a 16K context, since 32K won't fit in 20 GB; reasoning model: set
  `LLM_REASONING_EFFORT=none`). A native
  Anthropic SDK backend was discussed but not built (it would be a second client chosen by a setting).
- First full forecasts (#24): before the arbiter split, its single prompt was ~13K tokens and failed a 16K
  context; `OLLAMA_KV_CACHE_TYPE=q8_0` breaks Ollama's JSON-schema grammar (empty replies, HTTP 200).
- **Ollama setup that matters:** `OLLAMA_CONTEXT_LENGTH` of 8192 or more (the default 4096 silently truncates the
  ~4.8K session-notes prompt); `ollama ps` must say `100% GPU` (another resident model drops it to <1 tok/s); on
  Windows + AMD run Ollama natively, since Docker Desktop/WSL2 exposes only `/dev/dxg`, which `ollama:rocm` can't use.

## Running and testing

```bash
docker compose up --build                       # everything, mock mode, Greywater + the demo region on first start
cd backend && python -m pytest -q               # 113 tests; needs TerminusDB on :6363 (integration ones skip otherwise)
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

- Running the tests **inside the backend container** inherits `.env` through compose's `env_file`: `MOCK_LLM=false`,
  `LLM_MAX_TOKENS` etc. make `test_llm_client` and `test_durable_runs` fail (and call the real model). Clear them:
  `env -u MOCK_LLM -u LLM_MAX_TOKENS -u LLM_MODEL -u LLM_REASONING_EFFORT -u LLM_CONCURRENCY -u LLM_JSON_MODE python -m pytest -q`
  (pytest isn't in the image: `pip install pytest` first).
- On the original Windows PC, application control blocks `.exe` launchers in the venv and node_modules: run
  `python -m pytest`, not `pytest`; `gh` and Docker live in WSL. Git Bash mangles `/mnt/...` paths and some long
  heredocs: write patch scripts to a file and run them.
- **Self-hosted Azgaar (`fmg/`)**: built from the upstream repo at a pinned tag with `vite build --mode electron`
  (relative base, no Google Analytics; upstream's own Dockerfile misses `scripts/` and `docs/`). Its CSP needs
  `'unsafe-eval'` (the generation step "goods" evaluates code). v1.157's Full JSON has every field the importer
  reads; its default map is the whole globe with ~900 burgs, so ask for smaller maps for campaigns.
- **Atlas labels** are symbol layers (`label-*` in `Atlas.tsx`) using glyphs from `/fonts/{fontstack}/{range}.pbf`
  (`frontend/public/fonts`, 3 Noto Sans faces x 3 ranges). A name using characters outside those ranges needs that range
  copied from the pinned commit in `public/fonts/README.md`; nginx returns 404 (not index.html) for a missing one.
  Don't go back to HTML `Marker`s for labels: on the globe they fade to `opacityWhenCovered` (0.2) instead of hiding.
- The Chrome extension may drive a browser on **another machine**: `localhost` then fails; use the LAN IP
  (`http://192.168.4.99:5173`). Screenshots of the WebGL globe can time out; `javascript_tool` still works.
- Browser-pane testing of the atlas: a hidden pane has no `requestAnimationFrame`, so inject
  `window.requestAnimationFrame = cb => setTimeout(() => cb(performance.now()), 16)` before opening the atlas, and
  open it with the nav button (the app reads the hash only on load; navigating to a new hash does not reload).
  `window.__atlasMap` is the MapLibre map in dev.
- React StrictMode runs effects twice: a "mounted" ref must be set to true inside the effect, not only cleared in
  cleanup (this broke the paint-polling loop once). Atlas hot reload can leave MapLibre in a "Style is not done
  loading" crash; reload the page.
- MapLibre GeoJSON sources fetch in a worker: use absolute URLs (`location.origin + …`).
- Git Bash rewrites `/app/...` in `docker compose exec backend python /app/...`: prefix `MSYS_NO_PATHCONV=1`.
- Generated worlds (wizard and the demo, `DEMO_REGION`) are **regions** (`generate(..., region_km=, cell_km=,
  max_towns=, mesh="voronoi")`): ~20k km2 provinces, towns a day or two apart. `generate(seed)` with no region is the
  old hex **planet** (coarse, India-sized provinces), kept only for older tests; its output must stay byte-identical.
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

## Status (2026-11-27)

Done and merged: engine core (forecast, briefing, merge/cherry-pick, time travel, ingestion), World Atlas (globe,
Azgaar import, PMTiles), new-world wizard + SRD 5.2.1, VTT link (map footprints, DM scenes), world-aware AI maps,
portrait tokens, session log -> forecast, tokens on the table, party fog of war, party overland map on the table,
travel times (roads, sea, SRD paces) in forecasts and atlas, zoom-in maps of any spot, durable forecast runs
(survive restarts, resume). All six design phases done.

Real model: forecasts run end to end on the GPU PC (`qwen3.8:27b-24k`, flash attention, `LLM_MAX_TOKENS=8192`,
`forecast_check.py --days 9` passes in ~10 min). Fixed from those runs: #12–#16 (client, extraction, docs), #23
(a live run resumed twice), #24 (arbiter context; grouped arbiter calls), #30 (goal clocks handing over lost
conquests), #31 (diplomacy timed as army marches).

Native world generation (epic #45) is done: phases #38-#44, `backend/atlas/worldgen/`, `docs/worldgen.md`. Wizard
worlds and the first-run demo are generated regions; the demo moved there with `scripts/move_demo_to_region.py`.

Backlog: none open. Ideas: villages as an atlas-only tile layer, a coarse rest-of-world around the region.
Done since: the homebrew catalogue epic (library, overlay, wizard, editor); battle strength from challenge ratings;
"Open in VTT" from the briefing (`place` on each event/rumor, `to-table` with `mark_visited: false`); LLM-written
wizard prose (`wizard/prose.py`); globe labels as symbol layers with bundled glyphs (`frontend/public/fonts`); ingestion links to the catalogue
(`ingestion/catalogue_link.py`: SRD + homebrew `srd_ref`, challenge/threat, species).

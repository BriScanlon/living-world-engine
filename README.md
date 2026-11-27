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
| TerminusDB dashboard (admin / `root`; this machine only) | http://localhost:6363 |

On first start the backend creates the `living_world` database, applies the schema and seeds
**Greywater**, a small harbour town (see [Demo world](#demo-world)). Then:

1. **Session Forecaster** → *Use demo summary* → **Run Forecast**. Watch each LangGraph node run live.
2. **Open DM Briefing** → read the four sections; tick events to **Cherry-Pick**, or **Merge to Canon**.
3. **World Atlas** → spin the globe; pick the forecast branch, tick *Compare with main* and press ▶
   to watch provinces change hands day by day. Switch to **Party view** to see only what the party knows.
4. **Time Travel** → pick a branch, drag the in-world day slider, or pick a historic commit.
5. **Ingest** → drop Obsidian notes / PDFs / Word docs (try the files in `sample_data/`).
6. **✦ New World** → answer (or roll) five Guidebook-style steps and build a fresh campaign world; switch between
   worlds from the campaign menu in the header.
7. **On the table** → open the VTT's DM screen, generate or import a battle map, then in the atlas open a place,
   link the map under *On the table* and press **Send scene to the VTT**: the map goes on the table and the DM screen's
   **World** panel shows who is there, tensions, rumors and SRD stat lines. **✦ Paint it as it is** has the VTT
   paint the place as it stands on that day; **Portraits** paints a token for everyone there.
8. **After play** → the VTT's **Session Log** has your notes plus the places you visited; in the Session Forecaster
   press **From the table** to pull it into the next forecast's summary.

Ports already in use? Override `FRONTEND_PORT`, `BACKEND_PORT`, `TERMINUSDB_PORT` in `.env`.

**Across the LAN:** the dashboard, API and VTT listen on every interface, so other devices use this machine's
address (`http://<lan-ip>:5173`, `http://<lan-ip>:8080/dm` · `/table`). There is no login, so do this on a trusted
network only. TerminusDB is bound to `127.0.0.1`: it holds the whole world behind a default password, and only
the backend (over the compose network) and local tools need it.

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
5. **`world_arbiter`** — resolves simultaneous intents (contests by resources/influence, fighting
   strength, relationships), emits `WorldEvent`s with relationship shifts, control changes, goal progress,
   new goals/fears, deaths, and knowledge gained; completed goal clocks fire consequences.

   **Battle strength.** Attacks, seizures, thefts and sabotage weigh who actually fights
   (`simulation/strength.py`). Each side's people and creatures get a challenge rating from their own
   `challenge`, their `srd_ref` stat block (SRD or homebrew), a creature's `threat`, an NPC's role
   ("Captain of the Watch" fights as a Guard Captain, a "Baron" as a Noble) or level, else a commoner. A side's
   strength is `10 × log2(1 + XP/100)` over the SRD encounter XP of its units: a goblin is about 6, an ogre 25, a
   troll 42, an adult dragon 75. The attacker brings its faction's people in the same settlement (a faction acting as a whole
   musters everyone); the defender is the holder or rival with its people there, plus every creature at the place:
   a lair always defends itself. The mock arbiter adds strength to its dice ("…and was beaten back. (Old Tam
   (CR 0) against Troll (CR 5), Bog Rats x8 (CR 1/8).)"); the real arbiter gets the same `forces` in its prompt
   and is told to weigh them heavily.
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

**Homebrew** (`backend/srd/homebrew.py`, the **Homebrew** tab): your own creatures, peoples and treasures, shaped like
SRD entries (ids `home:<name>`). Make one from blank or start from an SRD entry (search, pick, rename, change);
export and import the library as JSON to share it. Entries live in a homebrew library database (`HOMEBREW_DB`) and are
layered over the SRD: every lookup sees them first, and one can **replace** an SRD entry outright. The wizard builds
with them and gives each new world its own copy:

* a creature appears in the wilds of the biomes it lists (any biome if none), ahead of SRD fauna;
* every homebrew people joins at least one culture, and lives in the starting town (unless the world is human-led);
* a treasure can become the contested item (homebrew first, at the chosen rarity).

Worlds keep the homebrew they were built with; *Copy the library into this world* refreshes the current campaign's
copies. Scenes sent to the VTT show homebrew stat lines, and zoom-in map briefs name homebrew fauna. Entries started
from an SRD entry record it (`derived_from`) and keep its CC-BY attribution; your own entries need none.

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
* **Unfinished forecasts** — runs are kept on disk, so a forecast survives a backend restart: one cut off by a
  restart shows as *interrupted* (one stopped by an error, such as a model timeout, as *failed*) and **▶ Resume**
  continues it after its last finished step, on the same branch. Real models make forecasts take minutes, so this
  matters once `MOCK_LLM=false`. A forecast another process is running (such as `scripts/forecast_check.py`) shows
  as *running elsewhere* with **Watch** instead: each run has an owner and a heartbeat, and only a run whose process
  has gone quiet (no heartbeat for 90 s) can be resumed. Merge, cherry-pick, discard and re-run refuse a branch whose
  forecast is still running.
* **Time Travel** — any branch × any in-world day × any commit; force-directed graph,
  click an agent to see exactly what its prompt would contain.

### 8. Party view: fog of war on the atlas (`backend/atlas/party.py`)

The atlas's **Party view** applies the agents' epistemic rule to the players: the party faction and its members see
only what they know, on that branch and day.

* **Visited** places: KNOWS marks, places where the party took part in an event, where a member is, and everything
  that contains them. Their province is clear and shows who holds it.
* **Heard-of** places: anything else the party legally knows (BELIEVES marks, events it knows of, goals and
  relationships). Name only; its province is half-fogged with no holder.
* Everything else is fog. Events are the ones the party knows or took part in; rumors are the ones it knows plus
  tavern talk where it is now, never with their accuracy. Comparing branches is off.
* In the DM view each place has **Party knowledge: Been there / Heard of / Unknown**. Marks are bi-temporal
  EpistemicEdges from the slider's day, so earlier days keep their fog. *Send scene to the VTT* marks the place
  visited (the table is where the party is).
* **Party map on the table** (`backend/atlas/party_map.py`): in Party view, *▶ Party map on the table* draws the
  party's view as an overland map (parchment, rivers, roads, soft fog over land and sea, names of what they know, a
  "you are here" ring) and shows it on the players' table, fitted to the screen with the battle grid hidden. While
  it is up, every *Party knowledge* mark redraws it; older party maps of the same world are removed from the VTT
  library. *preview* opens the image.

### 9. Travel times (`backend/atlas/travel.py`)

Distance now matters in the world:

* **Routes** are the quicker of across country or along the atlas's roads (Dijkstra over the road network). Overland
  stretches count as difficult terrain (half pace, 25% longer than the straight line); stretches over water go by
  ship. Places in the same town are no journey.
* **Paces** follow the SRD's overland travel: 24 miles a day on foot, 30 on horseback, 12 for an army, news at 30 with
  riders and merchants; ships make 48 miles a day.
* **In forecasts**, an actor acting on a far-off place sets out at the start of the round and acts on arrival
  (factions on the move march at army pace); the event's summary says how long it took, and late arrivals land after
  the round. Rumors reach a far tavern only once news could get there, and the locals believe them from that day.
* **In the atlas**, each place shows how far it is from where the party is (on foot, horseback, for an army, and how
  long news takes), and the route is drawn on the globe. `GET /api/travel?a=&b=` gives the same between any two
  things.

### 10. Zoom-in maps (`backend/atlas/local.py`)

Zoom close on the globe (zoom 9 or more) and a crosshair offers **✦ Paint a local map here** for any spot, not just
existing places. The engine describes the spot from the world around it: the biome underfoot, the province and who
holds it, the nearest settlement (how far, which way), a road, river or coast close by, creatures known in that
province's wilds (or the SRD's fauna for the biome) and recent public events nearby. You can edit the name and brief;
the spot becomes a `wilderness` place (painting the same spot again repaints it), the VTT paints it, and the map is
linked at true scale, so its footprint appears on the globe exactly where you zoomed in.

### 11. Virtual tabletop (`vtt/`)

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
* **Open in VTT from the briefing.** Every forecast event and rumor carries the place where it happened (its
  location, else a place it targets, else where its target is: a person's location, a faction's headquarters). Its
  **Open in VTT** button (▦ when a battle map is linked there, ▢ when not) shows that place as it stands on the
  forecast branch on the event's day: the map on the table and the scene on the DM screen. It's a preview, so the
  place is not marked visited (`mark_visited: false`) and nothing is written to the branch. The buttons appear only
  when a VTT is configured.
* **Maps follow the world.** *Paint it as it is* builds a map brief from world state on that branch and day (what
  the place is, who holds it, what has happened there lately; secret events are left out because the map goes on
  the players' table). You can edit the brief before the VTT paints it, and the finished map is linked from that day,
  so the map of a burned warehouse replaces the old one only from the day it burned.
* **Portrait tokens.** *Portraits* asks the VTT for a round top-down token for each person at the place who hasn't
  got one (tagged with their character id); the World panel shows them next to each name, with **◉ Place** (or
  *Put everyone on the table*) to drop them onto the map, where the DM moves them with the preview's Tokens tool.
* **The session loop.** The VTT keeps a session log: DM notes typed during play, plus every map shown and scene
  opened. *From the table* in the Session Forecaster turns it into the summary and sets the session's days.

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
| POST | `/api/forecasts/{id}/resume` | continue an interrupted or failed run from its last checkpoint |
| GET | `/api/diff?base=main&compare=…` | the 4-part briefing |
| GET | `/api/diff/raw?base=…&compare=…` | TerminusDB native diff |
| POST | `/api/branches/{name}/merge` · `/cherry-pick` · `/rerun` | branch controls |
| DELETE | `/api/branches/{name}` | discard |
| GET | `/api/branches/{name}/log` | commit history |
| GET | `/api/world/snapshot?branch=&day=&commit=` | time travel |
| GET | `/api/world/agent/{id}?branch=&day=` | an agent's epistemic context |
| GET | `/api/atlas?branch=&day=&compare=&view=dm\|party` | live map state: territory, places, events, rumors (party view: fog of war) |
| POST | `/api/party/knowledge` | `{place, state: visited\|heard\|unknown, branch, day}`: what the party knows of a place |
| GET · POST | `/api/party/map.webp?branch=&day=` · `/api/party/map-to-table` | the party's overland map · show it on the table |
| GET | `/api/travel?a=&b=&branch=&day=` | route and days between two things: on foot, horseback, an army, news |
| GET · POST | `/api/atlas/local-brief?lon=&lat=` · `/api/atlas/local-map` | describe a spot on the globe · paint it (poll `/api/vtt/jobs/{job}/collect`) |
| GET | `/api/atlas/place/{id}` · `/api/atlas/files/{slug}/{file}` | place detail · static layers (GeoJSON / PMTiles) |
| POST | `/api/atlas/import` | import an Azgaar Full JSON export |
| GET · POST | `/api/wizard/spec` · `/api/wizard/build` · `/api/wizard/jobs/{id}` · `/api/wizard/upload` | new-world wizard |
| GET · POST · DELETE | `/api/campaigns` · `/api/campaigns/active` · `/api/campaigns/{id}` | campaigns (one database per world) |
| GET | `/api/srd` · `/api/srd/entry/{id}` · `/api/srd/search?q=&kind=` | SRD catalogue info, entries (also `home:` ids), search |
| GET · POST · DELETE | `/api/homebrew` · `/api/homebrew/{uid}` | the homebrew library: list (with this world's copies), save `{entry, replace_uid?}`, remove |
| GET · POST | `/api/homebrew/from-srd/{srd_id}` · `/export` · `/import` · `/copy-to-world` | start from an SRD entry · JSON export/import · copy into the current campaign |
| GET | `/api/vtt/status` · `/api/vtt/maps` · `/api/vtt/media/{path}` | tabletop link status, its maps, proxied thumbnails |
| GET · POST | `/api/places/{id}/maps` | maps linked to a place · link one `{vtt_map_id, day, branch, kind, rotation_deg}` |
| DELETE | `/api/maps/{id}` | unlink (ends the map on that day) |
| GET · POST | `/api/places/{id}/scene` · `/api/places/{id}/to-table` | preview the DM scene · push map + scene to the VTT (`mark_visited: false` for a preview) |
| GET · POST | `/api/places/{id}/map-brief` · `/api/places/{id}/paint-map` | map brief from world state · have the VTT paint it |
| GET | `/api/vtt/jobs/{job}/collect` | paint progress; links the finished map from its day |
| POST | `/api/places/{id}/tokens` | portrait tokens for the people at a place |
| GET | `/api/vtt/session-log` | the table's session log as a forecast summary |
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

## Running with a real model

Everything works in mock mode; to use a real model, point the engine at any OpenAI-compatible endpoint in `.env`.
The tested setup is **Ollama on the Docker host** with an AMD 7900 XT (20 GB):

```bash
MOCK_LLM=false
LLM_BASE_URL=http://host.docker.internal:11434/v1   # the backend container reaches the host's Ollama here
LLM_MODEL=qwen3.8:27b-16k           # qwen3.8:27b at a 16K context (32K won't fit beside it in 20 GB); or qwen2.5:14b-instruct
LLM_REASONING_EFFORT=none           # reasoning models: skip the thinking before the JSON (empty for instruct models)
LLM_JSON_MODE=json_schema           # Ollama constrains decoding to the schema: far fewer invalid replies
LLM_MAX_TOKENS=4096                 # a looping model stops here instead of running to the timeout
LLM_CONCURRENCY=1                   # one GPU
LLM_TIMEOUT_SECONDS=300
```

**Set up Ollama first:**

* **Context length.** Ollama defaults to 4,096 tokens and *silently truncates* longer prompts
  (`truncating input prompt limit=2050 prompt=4795` in its log). The session-notes prompt with its schema is about
  4.8K tokens, so set `OLLAMA_CONTEXT_LENGTH` to at least 8192 on the Ollama side (32768 for a 14B model; about
  16384 is what fits beside a 27B model's weights in 20 GB) and restart Ollama.
* **VRAM.** `ollama ps` must show `100% GPU`. Another model resident on the same card (a second Ollama, a long
  keep-alive) pushes the model into shared system memory: under 1 token/s instead of 50 to 64, and large contexts
  fail to allocate. Windows Task Manager shows dedicated VRAM.
* **Don't quantize the KV cache for JSON.** `OLLAMA_KV_CACHE_TYPE=q8_0` would fit a longer context, but it breaks
  Ollama's JSON-schema grammar for qwen3.8 (every request fails on its first token, `Unexpected empty grammar stack`,
  and comes back as HTTP 200 with empty content). `OLLAMA_FLASH_ATTENTION=1` is fine.
* **AMD on Windows: run Ollama natively, not in a container.** Under Docker Desktop / WSL2 the GPU is exposed only
  as `/dev/dxg` (no `/dev/kfd`), so the `ollama/ollama:rocm` image can't use it; the native Windows build (ROCm)
  detects the card (gfx1100) and the backend container reaches it through `host.docker.internal`.

Then check every model call before running a forecast. `scripts/llm_smoke.py` makes each of the engine's five
structured calls once on the current campaign (session notes, an NPC's decision, the arbiter, rumors, lore
extraction), read-only, and reports whether each reply validated and how long it took:

```bash
docker compose up -d --build backend
docker compose exec backend python /app/scripts/llm_smoke.py                 # all five, JSON mode from .env
docker compose exec backend python /app/scripts/llm_smoke.py json_object agent rumor   # compare a mode, some calls
```

In Git Bash on Windows, prefix with `MSYS_NO_PATHCONV=1` or it rewrites `/app/...` into a Windows path. Each attempt
is logged (`docker compose logs backend`) with its wall time, finish reason and output tokens.

First run, `qwen2.5:14b-instruct` at 32K context (15 GB VRAM, 50 to 64 tokens/s), `json_schema`: session notes,
agent, arbiter and rumors each validated in 3 to 8 s; lore extraction ran away, copying the KNOWN ENTITIES list
instead of reading the text (issue #13). A 4B model on CPU was too slow to finish a single call.

The biggest prompt is the world arbiter's, which resolves every NPC's intent for a round. It is kept small: payloads
are compact JSON, intents are trimmed, and in `json_schema` mode the prompt carries a one-line outline of the reply
instead of the whole schema (the server enforces it). Intents that touch the same agents, targets or controllers are
resolved together; unrelated ones are packed into calls of at most six intents, so 16 intents take three calls of
about 6K characters each instead of one prompt of 13K tokens. If a reply is cut off by the **context window** (it
stops well short of `LLM_MAX_TOKENS`), the error says so, with the prompt's size, and doesn't waste more attempts.

Once every call passes, run a whole forecast. `scripts/forecast_check.py` forecasts the current campaign on the
real model (one round by default), printing each step as it runs, then a report: every model call type with its
count, failures and mean/max seconds; the events, intents and rumors that came out, plus samples to read; and the
forecast branch, which stays in the campaign so you can open its DM Briefing (discard it when done).

```bash
docker compose exec backend python /app/scripts/forecast_check.py              # 1 round, the demo session notes
docker compose exec backend python /app/scripts/forecast_check.py --days 9     # 3 rounds of 3 days
docker compose exec backend python /app/scripts/forecast_check.py --summary /app/sample_data/session-15-notes.md
```

It is an ordinary durable run: if it stops, it shows under *Unfinished forecasts* and can be resumed there.

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
                party.py (party view: what the party knows, DM marks) · party_map.py (the party's overland map)
                travel.py (routes, roads, sea legs, travel days) · local.py (zoom-in maps of any spot)
  wizard/       spec.py (steps, choices, focuses) · builder.py (answers -> campaign, one commit per step)
  srd/          srd_5_2_1.json (generated) · catalogue.py (lookups, biome bestiary, NPC templates, homebrew layer)
                homebrew.py (the DM's own entries: library, validation, import/export, copies into worlds)
                builders.py · briefing.py (4-part diff) · seed.py
  simulation/   engine.py (LangGraph) · models.py (AgentIntent, WorldEvent…) · mutations.py · prompts.py
                runs.py (run records + checkpoints on disk, resume)
  ingestion/    markdown.py · documents.py · audio.py · extract.py · resolution.py · pipeline.py
  llm/          client.py (OpenAI-compatible, validated JSON) · mock.py (deterministic BDI)
  vtt_link/     client.py (VTT HTTP client) · scene.py (map links, footprints, DM scenes)
                session.py (world-aware map briefs, portrait tokens, session log -> forecast)
  api/routes.py · main.py · config.py · tests/
frontend/src/   App.tsx · api.ts · ui.tsx · views/{Forecaster,Briefing,Atlas,Branches,TimeTravel,Ingest,NewWorld}.tsx
scripts/seed_demo_world.py · scripts/import_azgaar.py · scripts/build_srd_catalogue.py
scripts/llm_smoke.py (each model call once) · scripts/forecast_check.py (a whole forecast on the real model)
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

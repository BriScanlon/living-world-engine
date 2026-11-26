# Local-AI Tabletop VTT

An open-source (MIT) virtual tabletop engine for a **physical 32" 4K monitor laid flat on the table**.
It runs entirely on your LAN, draws a **calibrated 1-inch hex grid** that matches real miniature
bases, and turns a DM's one-line description into an **animated, seamlessly looping battle map**
using your own GPU box (Ollama/vLLM + ComfyUI with Flux and Wan 2.1).

- **`/table`**: fullscreen kiosk for the tabletop monitor, built from 4 layers: looping video or 4K still → ambient FX → 1" grid → fog of war
- **`/dm`**: control panel for a laptop or tablet: map library, AI map generator, Inkarnate importer, grid/FX/calibration controls, and a live fog-of-war brush that mirrors the table

This is the tabletop half of the [Living World Engine](../README.md): it runs as the `vtt` service of the
repo's `docker compose`, and the world engine's atlas can send it places to show (see *World link* below).

Works out of the box with **`VTT_MOCK_AI_PIPELINE=true`**: no GPU needed. Mock mode simulates the
pipeline stages and paints procedural 4K test maps, so you can try the whole thing on any machine.

---

## Quick start (Docker)

From the **repository root** (the VTT starts with the rest of the stack):

```bash
cp .env.example .env          # defaults to mock mode
docker compose up --build -d
```

Then, from any device on the LAN (replace `<host>` with the server's IP):

| Device | URL |
|---|---|
| Tabletop monitor (Chrome/Edge) | `http://<host>:8080/table`. Click once for fullscreen. |
| DM laptop / tablet | `http://<host>:8080/dm` |

Seed the starter pack (10 maps + 10 tokens):

```bash
docker compose exec vtt python /app/scripts/seed_starter_assets.py
```

Everything persists in the `vtt_data` Docker volume (SQLite DB, maps, thumbnails, inbox, starter pack); local runs use `vtt/data/`.

### Kiosk tips for the table PC

```bash
# Windows
"C:\Program Files\Google\Chrome\Application\chrome.exe" --kiosk --start-fullscreen --autoplay-policy=no-user-gesture-required http://<host>:8080/table
# Linux
chromium --kiosk --autoplay-policy=no-user-gesture-required --noerrdialogs http://<host>:8080/table
```

The cursor auto-hides after 2.5 s. The page requests a screen wake lock where the browser allows it
(secure contexts only), so also turn off display sleep in the OS.

---

## Calibrating the 1-inch grid

OS display scaling (typically **150 %** on a 32" 4K panel) changes how many CSS pixels make an inch,
so the VTT doesn't trust a hard-coded PPI. Calibrate it once:

1. On `/dm`, open **1-Inch Calibration** and click **Show ruler on table**.
2. The table shows 10 high-contrast cells, a yellow inch scale, and a dashed 1" base circle.
3. Lay a physical ruler on the glass and use the **−10 / −1 / −0.1 / +0.1 / +1 / +10 px** buttons
   (or type a value) until **0" to 10" measures exactly 10 inches (254 mm)**.
4. Turn the ruler off. The value (`hexFlatToFlatPx`) is saved in SQLite and sent to every table on connect.

Reference values: a 32" 3840×2160 panel is ~137.7 physical PPI, which is **~91.8 CSS px per inch at
150 %** and 137.7 at 100 %. The **32" 4K preset** button computes this from the table's reported
`devicePixelRatio`. Pointy-top hexes and squares are measured horizontally. Flat-top hexes are
measured vertically, because that's their flat-to-flat axis.

Grid options: pointy-top / flat-top hex / square, colour, opacity, line thickness (device px),
X/Y offset to line up with painted features, and optional coordinates (A1, B1…).

## Matching a map's scale (1" = 5 ft)

Calibration makes the *grid* exactly 1". Each *map* then needs its painted 5 ft squares to match that
inch. This is set per map in the **Map Scale** panel and saved with the map.

- **AI-generated and starter maps** are painted 140 ft wide, so they open at true scale automatically.
- **Imported maps** (Inkarnate, Dungeondraft, etc.) open as *Fill screen*. To fix their scale:
  1. Push the map to the table and pick **📏 Measure** in the DM's Table Preview.
  2. Drag across a known distance. Use as many painted squares as you can, since a longer line is more accurate.
  3. Type how many feet that is and click **Set 1" = 5 ft scale**.
  4. On a square grid, start the line on a painted grid corner and the map also snaps so its squares
     sit on the table grid. Fine-tune with the ⅒-square arrows or the ±0.1% zoom buttons.

To zoom by eye, scroll the **mouse wheel** over the Table Preview, use the on-screen **+ / −** buttons in
its corner, or pinch with two fingers on a tablet. Hold **Shift** for fine steps. The wheel and pinch
zoom around the cursor or fingers, and zooming always switches the map to true-scale mode, starting
from its current size.

The panel also takes the size directly as **squares across** (e.g. an Inkarnate export made 40
squares wide) or **image px per square**, and has an *AI map preset* (140 ft wide). **✥ Pan** in the
preview slides the map under the grid. Fog of war is anchored to the map image, so revealed areas
move and scale with it.

## Arranging the DM screen

Click **⊞ Edit layout** in the `/dm` header to rearrange the panels. In edit mode each panel shows as
a compact card that you can move in two ways:

- **Drag** it to reorder, or onto the other column to move it between the main area and the sidebar.
- Use the card's **↑ / ↓ / To sidebar → / ← To main area** buttons, which also work on a tablet.

**Save layout** keeps the changes and **Cancel** (or **Esc**) discards them. **Reset to default**
restores the original arrangement once you save. The layout is stored in that browser, so a laptop
and a tablet can each keep their own.

---

## The AI pipeline

```
DM text ──► LLM (OpenAI-compatible)        ──► { title, image_prompt, motion_prompt }
              enforced: "strictly 90-degree top-down orthographic battle map, zero perspective
              distortion, no grid, covering a 140ft by 80ft area (doors 5ft wide, corridors 10ft wide)"
        ──► ComfyUI  workflows/flux_battlemap.json   ──► 3840×2160 PNG still
        ──► ComfyUI  workflows/wan21_flf2v_loop.json ──► still = FIRST and LAST frame,
              locked-camera motion prompt              ──► seamless WebM loop
        ──► data/maps/ + SQLite + WebSocket notify /dm (optionally auto-push to /table)

Inkarnate PNG/WebP ──► data/inbox/ (watchdog) or /dm upload "Queue AI animation"
        ──► vision LLM writes a natural-motion-only prompt ──► Wan 2.1 FLF2V loop ──► library
```

At 140 ft × 80 ft on a 16:9 frame, one 5 ft square is 1/28 of the width. On a 32" panel that is
almost exactly 1 inch, so AI maps line up with the calibrated grid without any rescaling.

Jobs run one at a time (a single GPU queue) and stream their stage and progress to `/dm`. If the LLM
is down, a template prompt is used. If animation fails, or `COMFY_VIDEO_URL` is empty, the still map
is kept and the job message says so.

### Configuration (root `.env`)

In the shared `.env` every VTT setting carries a `VTT_` prefix (the world engine has its own `LLM_*` settings);
`docker-compose.yml` maps them to the names below. Running the VTT on its own (local development), use the
unprefixed names.

| Variable (root `.env`) | Default | Purpose |
|---|---|---|
| `VTT_PORT` | `8080` | host port for `/table` and `/dm` |
| `VTT_MOCK_AI_PIPELINE` | `true` | `false` to use the GPU services below |
| `VTT_LLM_API_URL` | `http://host.docker.internal:11434/v1` | OpenAI-compatible base URL (Ollama, vLLM, LM Studio…) |
| `VTT_LLM_MODEL` / `VTT_LLM_VISION_MODEL` | `qwen2.5:14b-instruct` / `qwen2.5vl:7b` | text and vision models |
| `VTT_COMFY_IMAGE_URL` | `http://host.docker.internal:8188` | ComfyUI running Flux |
| `VTT_COMFY_VIDEO_URL` | `http://host.docker.internal:8188` | ComfyUI running Wan 2.1 FLF2V (empty = no animation) |
| `VTT_COMFY_IMAGE_WORKFLOW` / `VTT_COMFY_VIDEO_WORKFLOW` | the two JSONs in `workflows/` | swap in your own graphs |
| `VTT_COMFY_TIMEOUT_S` | `1800` | per-job timeout |
| `VTT_WATCH_INBOX` / `VTT_INBOX_POLLING` | `true` / `true` | watched folder; polling is required on Docker Desktop bind mounts |
| `VTT_DEFAULT_HEX_PX` | `91.8` | starting grid size before calibration |

Model files and node IDs are documented in [`workflows/README.md`](workflows/README.md).

---

## World link

The world engine drives the VTT over its REST API (server to server; the VTT never calls back):

| Method | Path | |
|---|---|---|
| GET | `/api/maps/{id}/view` | how a map sits on the table, with `metresPerPx`, so the atlas can place it at true scale |
| GET · POST · DELETE | `/api/scene` | the DM-only *World* panel: who is at the place on the table, tensions, rumors, SRD stats |
| POST | `/api/generate` | also takes `title` and `tags` (e.g. `lwe:Location/the-docks`), so the engine can find its paint jobs |
| POST | `/api/tokens` | `{name, description, tags}`: paint a round portrait token |
| GET | `/api/jobs/{id}` | one job's progress and result |
| GET · POST · DELETE | `/api/session-log` (`DELETE /api/session-log/{id}` for one entry) | the session log |
| POST | `/api/upload` | also takes `tags` (comma-separated); maps tagged `lwe-overland:…` open fitted to the screen with no battle grid |
| GET · POST · DELETE | `/api/table/tokens` | tokens on the current map: list (hidden ones too), place `{asset_id?, name?, x?, y?, size?, hidden?}`, clear |

The **Session Log** panel on `/dm` records every map pushed and scene opened automatically; type notes as play goes.
*New session* clears it, so pull it into the world engine's Session Forecaster (*From the table*) first.
Scenes and the log are only ever sent to `dm` sockets.

---

## Tokens

Portraits and markers sit on the map, above the grid and below the fog, so fogged areas hide them too.

* **Place** a token from the Library's *Tokens* tab, from the World panel (a scene's people with portraits), or with
  *+ Marker* for a plain coloured disc.
* In **Table Preview → ◉ Tokens**, drag tokens to move them; click one to rename it, set its size (½ to 6 squares),
  hide it from players or remove it (Delete key).
* Positions are stored per map in map-image coordinates, like fog, so tokens stay put when you rescale or pan, and
  each map keeps its own tokens. A size of 1 is one calibrated inch on the table at true scale.
* **Hidden tokens never reach the table**: tokens travel in their own `tokens` message, filtered per role.

---

## Starter asset pack

`scripts/seed_starter_assets.py` reads [`scripts/starter_manifest.json`](scripts/starter_manifest.json)
(10 environments: tavern, forest road ambush, dungeon corridor, underground cavern, volcano boss
arena, docks at night, flooded goblin cavern, ruined temple, frozen pass, crypt, plus 10 tokens),
generates each one through the configured pipeline, and writes them to `data/starter_pack/` with an
`index.json`. Each asset is indexed in SQLite, so it shows up in the `/dm` library.

```bash
python scripts/seed_starter_assets.py --dry-run            # list what would be generated
python scripts/seed_starter_assets.py --mock               # force mock mode
python scripts/seed_starter_assets.py --real --no-animate  # GPU stills only (fast)
python scripts/seed_starter_assets.py --keys docks-at-night --force
```

Re-runs skip assets that already exist. Fixed per-asset seeds make real-mode results reproducible.

---

## Local development (no Docker)

```bash
# backend (Python 3.11+)
cd backend
python -m venv .venv && .venv/Scripts/activate      # Windows  (source .venv/bin/activate elsewhere)
pip install -r requirements.txt pytest
uvicorn app.main:app --reload --port 8000

# frontend (Node 20+), in a second terminal
cd frontend
npm install
npm run dev            # http://localhost:5173/table and /dm, proxied to :8000
```

`npm run build` writes `frontend/dist/`, and FastAPI serves it at `:8000` directly.
Without Docker, `DATA_DIR` defaults to `./data`.

Tests:

```bash
cd backend && python -m pytest -q
```

The tests cover the mock pipeline end to end (generate, WebSocket sync, upload, inbox watcher), plus
the **real-mode** pipeline against a fake OpenAI and ComfyUI server. That run checks both workflow
templates (every node link resolves, every `_params` target exists) and the prompt, seed and frame injection.

---

## Repository layout

```
backend/
  app/
    main.py        FastAPI app: REST, /ws, /media, SPA
    state.py       live table state, per-map fog of war, settings persistence
    hub.py         WebSocket fan-out (table / dm roles)
    db.py          SQLite: assets, settings, jobs, fog, tokens, session log
    tokens.py      token validation, per-role visibility
    watcher.py     watchdog inbox watcher
    media.py       thumbnails + procedural mock maps/tokens
    pipeline/
      llm.py       OpenAI-compatible text + vision prompting
      comfy.py     ComfyUI client + workflow param injection
      flows.py     text→animated map, image→loop, tokens
      jobs.py      single-GPU async job queue
  tests/
frontend/src/
  table/           /table page + calibration ruler
  dm/              /dm panels (fog editor, controls, generator, importer, jobs, library, world, session log)
  components/      shared layers (map media, FX, grid, tokens, fog)
  lib/             hex math, fog store/renderer, FX engine, WebSocket sync
workflows/         ComfyUI API workflows (Flux, Wan 2.1 FLF2V) with documented node IDs
scripts/           starter pack seeder + manifest
```

### WebSocket protocol (`/ws?role=table|dm`)

- **Server → client:** `state` (full snapshot on connect), `state_part {key, value}`, `fog_op`, `fog_full`, `fog_meta`, `job`, `jobs`, `asset_added`, `library_changed`, `toast`, `tokens {tokens}` (the table's copy leaves out hidden ones); DM only: `scene`, `log_full {entries}`, `log_entry {entry}`
- **Table → server:** `viewport {w, h, dpr}`, which drives DM preview geometry and brush sizes in inches
- **DM → server:** `grid` / `display` / `fx` / `view` `{patch}`, `calibration {patch | nudge}`, `fog_op`, `fog_reset {base}`, `fog_enabled`, `fog_undo {count}`, `token_add {token}`, `token_move {id, x, y}`, `token_update {id, patch}`, `token_remove {id}`, `tokens_clear`

`view` is the current map's placement: `{mode: cover|contain|scale, pxPerSquare, cx, cy}`. It is
stored per map. Fog ops use coordinates normalised to the map image, so the same op list renders
identically on the 4K table and on the DM's small preview, and follows the map when it is rescaled
or panned. Each map keeps its own fog and view.

## License

MIT. See [LICENSE](LICENSE). Assets you generate with your own models are yours.

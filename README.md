# Local-AI Tabletop VTT

An open-source (MIT) virtual tabletop engine for a **physical 32" 4K monitor laid flat on the table**.
It runs entirely on your LAN, draws a **calibrated 1-inch hex grid** that matches real miniature
bases, and turns a DM's one-line description into an **animated, seamlessly looping battle map**
using your own GPU box (Ollama/vLLM + ComfyUI with Flux and Wan 2.1).

- **`/table`**: fullscreen kiosk for the tabletop monitor, built from 4 layers: looping video or 4K still → ambient FX → 1" grid → fog of war
- **`/dm`**: control panel for a laptop or tablet: map library, AI map generator, Inkarnate importer, grid/FX/calibration controls, and a live fog-of-war brush that mirrors the table

Works out of the box with **`MOCK_AI_PIPELINE=true`**: no GPU needed. Mock mode simulates the
pipeline stages and paints procedural 4K test maps, so you can try the whole thing on any machine.

---

## Quick start (Docker)

```bash
cp .env.example .env          # defaults to mock mode
docker compose up --build -d
```

Then, from any device on the LAN (replace `<host>` with the server's IP):

| Device | URL |
|---|---|
| Tabletop monitor (Chrome/Edge) | `http://<host>:8000/table`. Click once for fullscreen. |
| DM laptop / tablet | `http://<host>:8000/dm` |

Seed the starter pack (10 maps + 10 tokens):

```bash
docker compose exec vtt python /app/scripts/seed_starter_assets.py
```

Everything persists in `./data/` (SQLite DB, maps, thumbnails, inbox, starter pack).

### Kiosk tips for the table PC

```bash
# Windows
"C:\Program Files\Google\Chrome\Application\chrome.exe" --kiosk --start-fullscreen --autoplay-policy=no-user-gesture-required http://<host>:8000/table
# Linux
chromium --kiosk --autoplay-policy=no-user-gesture-required --noerrdialogs http://<host>:8000/table
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

The panel also takes the size directly as **squares across** (e.g. an Inkarnate export made 40
squares wide) or **image px per square**, and has an *AI map preset* (140 ft wide). **✥ Pan** in the
preview slides the map under the grid. Fog of war is anchored to the map image, so revealed areas
move and scale with it.

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

### Configuration (`.env`)

| Variable | Default | Purpose |
|---|---|---|
| `MOCK_AI_PIPELINE` | `true` | `false` to use the GPU services below |
| `LLM_API_URL` | `http://host.docker.internal:11434/v1` | OpenAI-compatible base URL (Ollama, vLLM, LM Studio…) |
| `LLM_MODEL` / `LLM_VISION_MODEL` | `qwen2.5:14b-instruct` / `qwen2.5vl:7b` | text and vision models |
| `COMFY_IMAGE_URL` | `http://host.docker.internal:8188` | ComfyUI running Flux |
| `COMFY_VIDEO_URL` | `http://host.docker.internal:8188` | ComfyUI running Wan 2.1 FLF2V (empty = no animation) |
| `COMFY_IMAGE_WORKFLOW` / `COMFY_VIDEO_WORKFLOW` | the two JSONs in `workflows/` | swap in your own graphs |
| `COMFY_TIMEOUT_S` | `1800` | per-job timeout |
| `WATCH_INBOX` / `INBOX_POLLING` | `true` / `true` | watched folder; polling is required on Docker Desktop bind mounts |
| `DEFAULT_HEX_PX` | `91.8` | starting grid size before calibration |

Model files and node IDs are documented in [`workflows/README.md`](workflows/README.md).

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
    db.py          SQLite: assets, settings, jobs, fog
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
  dm/              /dm panels (fog editor, controls, generator, importer, jobs, library)
  components/      shared canvas layers (map media, FX, grid, fog)
  lib/             hex math, fog store/renderer, FX engine, WebSocket sync
workflows/         ComfyUI API workflows (Flux, Wan 2.1 FLF2V) with documented node IDs
scripts/           starter pack seeder + manifest
```

### WebSocket protocol (`/ws?role=table|dm`)

- **Server → client:** `state` (full snapshot on connect), `state_part {key, value}`, `fog_op`, `fog_full`, `fog_meta`, `job`, `jobs`, `asset_added`, `library_changed`, `toast`
- **Table → server:** `viewport {w, h, dpr}`, which drives DM preview geometry and brush sizes in inches
- **DM → server:** `grid` / `display` / `fx` / `view` `{patch}`, `calibration {patch | nudge}`, `fog_op`, `fog_reset {base}`, `fog_enabled`, `fog_undo {count}`

`view` is the current map's placement: `{mode: cover|contain|scale, pxPerSquare, cx, cy}`. It is
stored per map. Fog ops use coordinates normalised to the map image, so the same op list renders
identically on the 4K table and on the DM's small preview, and follows the map when it is rescaled
or panned. Each map keeps its own fog and view.

## License

MIT. See [LICENSE](LICENSE). Assets you generate with your own models are yours.

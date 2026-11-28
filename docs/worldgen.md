# Native world generation

How **✦ New World → Map source: Generate a region** (and the first-run demo, *Thessaly Reach*) makes a world, stage by
stage, and where to turn each knob. Built in epic #45 (phases #38–#44). For hand-made maps, use the self-hosted Azgaar
generator instead (`fmg/`, `docs/azgaar-field-guide.md`).

## The pipeline

`atlas.sample_world.generate(seed, ..., region_km=, cell_km=, max_towns=, mesh="voronoi")` builds one dict in
**Azgaar's Full JSON shape**, so the unchanged importer (`atlas/azgaar.py`) turns it into atlas layers, PMTiles and
graph documents exactly as it does for an Azgaar export. Every stage is a module in `backend/atlas/worldgen/`, pure
numpy + shapely, deterministic per seed. A medium region generates in about a second; a whole wizard build (mock
mode, with import and tiles) takes a few seconds.

| # | stage | module | what it makes |
|---|---|---|---|
| 0 | scale and placement | `sample_world.generate` | a region of `region_km` (not a planet), centred at a latitude for the climate answer; `mapCoordinates` so 1 px is a known number of km |
| 1 | mesh and terrain | `worldgen/mesh.py`, `worldgen/terrain.py` | Voronoi cells from a jittered lattice; land from shape bumps + fBm noise; mountain ranges on convergent tectonic-plate boundaries; a ragged sea margin at the frame (no coast cut straight by the region's edge); sea level from the shape's sea share; clean coasts |
| 3 | climate | `worldgen/climate.py` | temperature from latitude and height; rain carried by prevailing winds, with rain shadows |
| 2 | water | `worldgen/water.py` | priority-flood: lakes or levelled basins; drainage and flux (fed by the rain); rivers with tributaries; harbour ratings |
| 3 | biomes | `worldgen/climate.py` | a Whittaker-style table from temperature and rain to Azgaar's 13 biomes; low, wet ground becomes wetland |
| 4 | peoples and polities | `worldgen/polities.py` | habitability; cultures; realms with borders along ridges, rivers and peoples; towns and ports; even, county-sized provinces |
| 5 | routes | `worldgen/routes.py` | roads (spanning tree of least-cost paths), trails, highways between capitals, sea lanes between ports |

Climate runs before water (so rain feeds the rivers) and biomes after it (so lakes and wetlands are known).

## Knobs

**Size** (`wizard/builder.py: REGION_SIZES`, the wizard's *Detail*):

| detail | `region_km` | `cell_km` | cells | `max_towns` |
|---|---|---|---|---|
| small | 1,500 × 1,000 | 18 | ~5k | 100 |
| medium | 2,500 × 1,600 | 20 | ~11k | 200 |
| large | 4,000 × 2,500 | 24 | ~20k | 320 |

A world adds at most about 1,500 graph documents (`WorldView` loads all of them per operation): towns are the budget,
wilds are capped at 60 (`MAX_WILDS`), villages are not graph nodes.

**Placement and scale** (`sample_world.py`): `REGION_LATITUDE` (centre latitude per climate answer), `PROVINCE_KM2`
(22,000), `TOWN_SPACING_KM` (45).

**Land** (`sample_world.LAND_SHAPES`, `terrain.py`): per shape the number and radius of bumps and the sea share;
`CLUSTERS` (how many separate landmasses *continents*, *islands* and *pangea* gather into); the noise mix per shape
(island shapes lean on finer noise); `plates(k=...)` and its ~60% convergent boundaries for mountain chains;
`frame_margin(share=0.06)` (the band of sea at the frame, measured through a noise warp of 5% of the short side).

**Water** (`water.py: hydrology`): `lake_min_cells` (6) and `lake_min_depth` (3) for a basin to become a lake;
`threshold` (default: land cells / 250) for a river; `min_len` (4 land cells).

**Climate** (`climate.py`): `temperature` (30 °C at the equator, −0.45 °C per degree, −6.5 °C per km on a squared
height curve), `wind_east` (westerlies at 30–60°), `rain_by_latitude`, the rain-out and uplift factors in
`precipitation`, and the thresholds in `biome_for`.

**Polities** (`polities.py`): `BIOME_HAB` (how liveable each biome is) and the river and harbour bonuses in
`habitability`; the realm step costs (+4 onto a river, +3 into another people's land, divided by expansionism);
glaciers stay wild.

**Routes** (`routes.py`): `max_link_px` (3.5 × town spacing), `ROAD_DISCOUNT` (0.4: roads merge into trunks), trails
for detours over 1.8× (up to 30% more links), sea lanes up to 450 km (`max_sea_px`). Travel (`atlas/travel.py`)
sails `searoutes` at ship speed.

## Moving an existing demo

Fresh installs get the region on first start. An existing demo campaign moves with
`docker compose exec backend python /app/scripts/move_demo_to_region.py [--dry-run]`: the old map's documents and
whatever points at them go in one commit (kept in TerminusDB's history), then the region is imported and Greywater
placed on one of its ports. Forecast branches made on the old map are left as they were.

## Tests

`tests/test_region.py` (scale, journeys on the real travel model), `test_worldgen.py` (mesh, terrain, shapes),
`test_water.py`, `test_climate.py`, `test_polities.py`, `test_routes.py`, `test_demo_region.py`. The hex grid (planet
mode) stays for older tests and is byte-identical to before the epic.

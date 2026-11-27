# Azgaar Field Guide for the Living World Engine

*Last checked against Azgaar's Fantasy Map Generator v1.158 on 26 Nov 2026.*

How to build a campaign world in [Azgaar's Fantasy Map Generator](https://azgaar.github.io/Fantasy-Map-Generator/) and bring it into the engine's World Atlas, mapped from a walkthrough of every menu.

**Self-hosted:** the stack runs its own copy at `http://<this-machine>:8070` (the `fmg` service, pinned to
v1.157.0; see [fmg/README.md](../fmg/README.md)), and the wizard's *Use my Azgaar export* links to it.

## Workflow at a glance

Nine steps take a world from blank settings to the engine's globe; steps 1–3 decide the planet, 4–6 shape it, 7–9 hand it over.

1. **Open the menu** with the ▶ arrow, top left. Close the release notes first.
2. **Options tab**: set heightmap template, cultures (number and name set), states, burgs and religions. These apply to the *next* new map.
3. **Configure World** (*Climate*): for a campaign, keep the map **regional** (a few realms to a continent, lower *Map size*) rather than pressing **Whole world**: a planet-sized map makes journeys months long and gives the engine far more burgs than it should hold (keep *Burgs number* to a few hundred). Then tune equator temperature, poles and precipitation.
4. **New Map** (bottom of the menu). Repeat until the coastlines feel right; the **Map seed** reproduces any map you like.
5. **Lore**: name the map, set the year and era, add a description. The map name becomes the atlas name in the engine.
6. **Shape it in Tools**: edit states, provinces, cultures, religions, burgs, rivers and diplomacy; lock anything you want kept before regenerating.
7. **Save → machine** to keep a `.map` file you can reload in Azgaar.
8. **Export → Export to JSON → Full** to produce the file the engine imports.
9. **Import onto a review branch**: upload to `POST /api/atlas/import` with `branch=import-1`, check it in the World Atlas tab, then merge.

```powershell
curl.exe -F "file=@$HOME\Downloads\YourMap Full.json" -F "branch=import-1" http://localhost:8000/api/atlas/import
```

(Use your `BACKEND_PORT` if you changed it. Running `scripts/import_azgaar.py` instead needs the project's virtual environment, and outside Docker it writes the atlas layers to the local `data/atlas` folder.)

## Menu map

The ▶ arrow opens one panel with five tabs and six buttons along its foot; everything else opens as a floating dialog.

| Where | What it holds | When we use it |
| --- | --- | --- |
| **Layers** tab | ~30 layer toggles (states, provinces, cultures, religions, biomes, rivers, routes, borders, labels, markers, military…), 14 layer presets (Political, Cultural, Religions, Provinces, Biomes, Heightmap, Physical, Places of interest, Goods and production, Trade, Military, Emblems, Pure landmass, Custom), and view modes **Standard / 3D scene / Globe** | Checking the world from each angle; *Globe* previews how it will wrap on our atlas |
| **Style** tab | Look of every layer, plus style presets (ancient, atlas, cinderwood, clean, cyberpunk, darkSeas, frostbite, gloom, ink, light, monochrome, night, pale, watercolor) | Picking a look for player handouts; doesn't affect the export |
| **Options** tab | World generation settings for the next map, interface settings, emblem shapes | Steps 2–3 of the workflow |
| **Tools** tab | *Configure* (Climate, Lore, Advanced, Reset), *Edit* (24 editors), *Regenerate* (16), *Add* (burg, label, marker, river, route), *Show* (cells, charts, minimap), *Create* (submap, transform, wrap) | Shaping the world after generation |
| **About** tab | Version, links, help | — |
| **New Map** | Generates a new world from the Options settings | Each attempt at a world |
| **Export** | Images (.svg, .png, .jpeg, tiles), GeoJSON (cells, routes, rivers, markers, zones), JSON (**Full**, minimal, pack cells, grid cells) | *JSON → Full* for the engine; PNG tiles later for the VTT |
| **Save** / **Load** | Azgaar's own `.map` file to machine, Dropbox or browser storage | Keeping an editable master copy |
| **Zoom Out**, **Search** | Reset the view; find a burg, state or feature by name | Navigation |

## World settings in Options

These settings only apply when you press **New Map**; they never change the map already on screen. "Seen" values are from the random map that loaded during the walkthrough.

| Setting | Seen | What it controls | Suggestion for a campaign world |
| --- | --- | --- | --- |
| Map seed | 806773474 | Reproduces a world exactly | Note it down for any map you like |
| Points number | 10K | Cell count (detail vs speed) | 10K–30K; more cells = finer borders and more provinces |
| Heightmap | Old World | Land/sea template (next section) | Continents or Old World for a varied world; Archipelago for seafaring campaigns |
| Cultures number | 14 | How many peoples | 6–12 so each feels distinct |
| Cultures set | European (options: All-world, European, Oriental, English, Antique, High Fantasy, Dark Fantasy, Random) | Name languages and naming styles | High Fantasy or Dark Fantasy for D&D flavour |
| States number | 18 | Realms on the map | 8–15; each becomes a faction agent in the engine |
| Number of burgs | Auto (1000) | Towns and cities | Auto; every burg becomes a place on the atlas |
| Religions number | 2 | Organised faiths | 4–8; these can seed churches and gods in wizard step 5 |
| Provinces ratio | 26 | Share of each state split into provinces | 40–60 gives more provinces, so more borders that can move |
| Size variety | 2.4 | How unequal states are | 2–3 for a mix of empires and minor realms |
| Growth rate | 1.8 | How far states expand into wilderness | Lower leaves more unclaimed land to explore |
| Lattice / Sampling / Jitter | Default / Square grid / 0.9 | Cell shape (organic, hex boards, square boards) | Default for a natural look |
| Map size | 1536 × 770 | Canvas pixels | Leave as is; real scale comes from Configure World |

Interface settings in the same tab: autosave interval (15 min), *On load* (random map or last saved), speaker voice, emblem shape (48 shield styles), and debug toggles for economy, temperature and cloud storage.

## Configure World, Lore and Advanced

**Configure World is the most important dialog for us**: it decides where the map sits on the planet. The random map in the walkthrough covered only 19°S–46°S and 27°W–27°E, so on our globe it would be a thin strip.

| Dialog (Tools → Configure) | Fields | Notes |
| --- | --- | --- |
| **Climate** (title: *Configure World*) | Equator temperature (26), North Pole (−31), South Pole (−4), Map size (15), Latitudes (71), Longitudes (50), Precipitation (88), wind directions by latitude band | Presets: **Whole world**, Northern, Tropical, Southern. Shows the map's real size (7,680 × 3,850 mi here) and its corner coordinates |
| **Lore** (*Setup Lore*) | Map name, year, era name and short form (e.g. *Winter Era / WE*), description | The map name becomes the atlas name; year and era can anchor in-world day 0 |
| **Advanced** | Map size, lattice, sampling, jitter, provinces ratio, size variety, growth rate, interface settings, **New Map** button | Same mesh settings as Options, gathered in one dialog |
| **Reset** | Restores all options to defaults | Avoid unless you mean it |

Two things to watch in Configure World:

- **Auto-apply changes** is ticked by default, so dragging a slider immediately changes the current world. Untick it while experimenting, then press **Update world**.
- **Whole world** should be pressed *before* New Map when the map is meant for the globe, so latitudes run pole to pole and longitudes wrap all the way round.

## Heightmap

The heightmap decides the shape of land and sea; pick a template in Options, or edit it afterwards with **Tools → Edit → Heightmap**.

| Template | Chance when random | Gives you |
| --- | --- | --- |
| High Island | 19% | One large mountainous island |
| Archipelago | 18% | Many islands; naval campaigns |
| Continents | 16% | Two or more large landmasses |
| Low Island | 9% | One flat island |
| Old World | 8% | Large irregular continent with inland seas |
| Shattered | 7% | Broken land, many straits |
| Mediterranean | 5% | Land around a central sea |
| Pangea | 5% | One supercontinent |
| Volcano | 3% | Single volcanic island |
| Peninsula | 3% | Land jutting into the sea |
| Fractious | 3% | Fragmented coastlines |
| Isthmus | 2% | Two masses joined by a land bridge |
| Atoll | 1% | Ring islands |
| Taklamakan | 1% | Big inland desert |

Azgaar also ships real-world shapes as starting heightmaps (Arabia, Atlantics, Britain, Caribbean, Eurasia, Europe, Greenland, Hellenica, Iceland and more), handy for an Earth-like feel.

**Heightmap editor** (enters a separate *customisation mode*; choose to erase, keep or risk the existing political layers when you enter):

- **Brushes**: raise, depress, align (flatten to a picked height), smooth, disrupt (roughen), line.
- **Template Editor**: build a heightmap step by step from operations such as hill, pit, range, trough, strait, mask, invert, add and multiply.
- **Image Converter**: upload any image (a sketch or a painted map) and assign a height to each colour, so a hand-drawn continent can become the world.
- Lake threshold: how deep a depression must be to form a lake.

## Tools, mapped to the Guidebook wizard

The Tools tab is where the world gets its character; each group lines up with a step of the new-world setup wizard in the World Atlas design.

| Wizard step | Azgaar tools (Tools tab; shortcut) | What carries into the engine |
| --- | --- | --- |
| 1. World | Configure → Climate; Edit → Biomes (Shift+B) | Atlas extent, biomes layer |
| 2. Continents | Edit → Heightmap, Coastlines, Features (Shift+F), Rivers (Shift+V); Regenerate → Rivers, Ice, Relief; Add → River | Land, rivers and coast layers |
| 3. Kingdoms | Edit → States (Shift+S), Provinces (Shift+P), Cultures (Shift+C), Religions (Shift+R), Diplomacy, Emblems (Shift+Y), Military, Namesbase (Shift+N); Regenerate → States, Provinces, Cultures, Religions, Military | Factions, provinces and who controls them, trust from diplomacy, border claims and war goals |
| 4. Local area | Edit → Burgs (Shift+T), Routes (Shift+U), Markers (Shift+K), Zones (Shift+Z), Labels (Shift+L); Add → Burg, Route, Marker, Label; Create → **Submap** | Places with coordinates, roads; a submap regenerates the current view at higher detail for a local campaign area |
| 5. History | Edit → Notes (Shift+O), Journeys (Shift+J), Markers | Not imported yet; notes and markers are good candidates for past events and sites of interest |
| (economy) | Edit → Goods (Shift+G), Markets, Development (Shift+X), Trade; Regenerate → Economy, Population | Not imported yet; could feed faction resources later |

Other tools: Measurers (Shift+=) for distances, Units (Shift+Q) for distance and population units, Show → Cells / Charts / Minimap, Create → Transform (rotate, scale, shift the map) and Wrap (reshape cells with a brush).

**Locks matter**: most editors let you lock an item (a state, culture, burg, route). *Regenerate* only replaces unlocked items, so lock what you've hand-tuned before regenerating anything.

## Save vs Export

Keep two files per world: a `.map` to carry on editing in Azgaar, and a Full JSON export for the engine.

| File | How | Used by | Notes |
| --- | --- | --- | --- |
| `.map` | **Save → machine** (or Dropbox, or browser) | Azgaar's **Load** | Each save makes a new file, so older saves stay as backups. Browser storage is only a backup and can be cleared |
| Full JSON | **Export → Export to JSON → Full** | The engine's importer | One file with cells, vertices, states, provinces, burgs, cultures, religions, rivers, routes and the map's coordinates |
| GeoJSON (cells, routes, rivers, markers, zones) | Export → Export to GeoJSON | QGIS and other GIS tools | Not needed by the engine; the Full JSON already holds it |
| PNG / JPEG / SVG / tiles | Export → Download image | Handouts; later, VTT region maps | PNG/JPEG scale slider sets resolution |

After changing the world in Azgaar, export Full JSON again and import it onto a new branch; the World Atlas tab and the briefing diff then show what changed.

## Gotchas

- **New Map replaces the current world.** Save a `.map` first if you might want it back.
- **Auto-apply in Configure World** changes the live map as you drag; untick it to experiment.
- **Regenerate overwrites unlocked items**, and regenerating states or provinces resets borders you drew by hand.
- **Pop-ups**: Azgaar downloads files through a pop-up window, so allow pop-ups for the site or exports silently fail.
- **Exports are blocked in edit mode**: leave heightmap or other customisation mode before exporting.
- **On load** defaults to a fresh random map; switch it to *Open last saved map* in Options if you want Azgaar to reopen your world.
- **Re-importing the same world** onto `main` keeps province and realm ids stable only if Azgaar keeps its numbering; import edits onto a branch and check the diff first.
- **Sources:** walkthrough of the live app (v1.158.0); template odds and editor tool names from [Azgaar's source on GitHub](https://github.com/Azgaar/Fantasy-Map-Generator) (MIT).

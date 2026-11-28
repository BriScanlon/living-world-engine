"""Import an Azgaar Fantasy Map Generator *Full JSON* export.

Two outputs:

* **Static layers** (written to disk once): land by biome, provinces, rivers, roads, burgs —
  GeoJSON, plus PMTiles when tippecanoe is available.
* **World graph documents**: states -> ``Faction``, provinces -> ``Province`` + a CONTROLS edge,
  burgs -> ``Location`` with lon/lat, diplomacy -> trust, plus desires, conquest goals and
  knowledge so imported realms act in forecasts like any other agent.

Coordinates use Azgaar's own projection (``getCoordinates`` in its source):
``lon = lonW + x / width * lonT`` and ``lat = latN - y / height * latT``.
"""
from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from shapely.geometry import LineString, Point, Polygon, mapping, shape
from shapely.ops import unary_union

from graph import builders as b
from graph.schema import slugify

log = logging.getLogger(__name__)

# Azgaar diplomacy words -> (trust, label)
DIPLOMACY = {
    "Ally": (60, "ally"), "Friendly": (30, "friendly"), "Neutral": (0, "neutral"),
    "Suspicion": (-20, "suspicion"), "Rival": (-45, "rival"), "Enemy": (-75, "enemy"),
    "Vassal": (40, "vassal"), "Suzerain": (30, "suzerain"),
}


@dataclass
class AzgaarWorld:
    raw: dict[str, Any]
    slug: str

    @classmethod
    def load(cls, path: str | Path, slug: str | None = None) -> "AzgaarWorld":
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        if "pack" not in raw or "mapCoordinates" not in raw:
            raise ValueError("Not an Azgaar Full JSON export (needs 'pack' and 'mapCoordinates'): "
                             "use Export > Export to JSON > Full in Azgaar's Fantasy Map Generator")
        name = raw.get("info", {}).get("mapName") or Path(path).stem
        return cls(raw, slug or slugify(name))

    # ------------------------------------------------------------------ basics
    @property
    def name(self) -> str:
        return self.raw.get("info", {}).get("mapName") or self.slug

    @property
    def pack(self) -> dict[str, Any]:
        return self.raw["pack"]

    @property
    def cells(self) -> list[dict[str, Any]]:
        c = self.pack["cells"]
        return c["cells"] if isinstance(c, dict) else c  # tolerate an older nested shape

    @property
    def vertices(self) -> list[dict[str, Any]]:
        return self.pack.get("vertices") or self.pack["cells"]["vertices"]

    def lonlat(self, x: float, y: float) -> tuple[float, float]:
        mc = self.raw["mapCoordinates"]
        info = self.raw.get("info", {})
        w, h = info.get("width") or 1, info.get("height") or 1
        return round(mc["lonW"] + x / w * mc["lonT"], 5), round(mc["latN"] - y / h * mc["latT"], 5)

    def live(self, items: list[Any]) -> list[dict[str, Any]]:
        """Azgaar lists keep index 0 as a placeholder and mark deletions with removed=true."""
        return [x for x in items if isinstance(x, dict) and x.get("i") and not x.get("removed")]

    def cell_polygon(self, cell: dict[str, Any]) -> Polygon:
        verts = self.vertices
        return Polygon([self.lonlat(*verts[v]["p"]) for v in cell["v"]])

    # ------------------------------------------------------------------ uids
    def uid(self, kind: str, i: int, name: str = "") -> str:
        return f"{self.slug}-{kind}{i}" + (f"-{slugify(name)}" if name else "")

    def province_uid(self, i: int) -> str:
        p = next((x for x in self.live(self.pack.get("provinces", [])) if x["i"] == i), {})
        return self.uid("p", i, p.get("name", ""))

    def state_uid(self, i: int) -> str:
        s = next((x for x in self.live(self.pack.get("states", [])) if x["i"] == i), {})
        return self.uid("s", i, s.get("name", ""))

    # ------------------------------------------------------------------ layers
    def layers(self, detail: bool = True) -> dict[str, dict[str, Any]]:
        """The atlas layers. With ``detail`` (the default) the coast gets fractal detail down to ~100 m (#62), and
        provinces, biomes and rivers are fitted to it; without, coasts are the cell outlines (simplified)."""
        cells = self.cells
        land = [c for c in cells if c.get("h", 0) >= 20]
        biomes = {bm["i"]: bm for bm in self.pack.get("biomes", []) if isinstance(bm, dict)}
        if not biomes and isinstance(self.pack.get("biomes"), dict):  # legacy column form
            bd = self.pack["biomes"]
            biomes = {i: {"i": i, "name": n, "color": c} for i, (n, c) in enumerate(zip(bd["name"], bd["color"]))}

        def dissolve(groups: dict[Any, list[dict[str, Any]]]) -> dict[Any, Any]:
            out = {}
            for k, cs in groups.items():
                geom = unary_union([self.cell_polygon(c).buffer(0) for c in cs])
                out[k] = geom.simplify(0.02, preserve_topology=True)
            return out

        by_biome: dict[int, list] = {}
        by_prov: dict[int, list] = {}
        for c in land:
            by_biome.setdefault(c.get("biome", 0), []).append(c)
            if c.get("province"):
                by_prov.setdefault(c["province"], []).append(c)

        biome_geom = dissolve(by_biome)
        prov_geom = dissolve(by_prov)
        land_cells = unary_union([self.cell_polygon(c).buffer(0) for c in land])
        burg_list = self.live(self.pack.get("burgs", []))
        river_lines = []
        for r in self.pack.get("rivers", []):
            if not isinstance(r, dict) or len(r.get("cells") or []) < 2:
                continue
            pts = [self.lonlat(*cells[c]["p"]) for c in r["cells"] if 0 <= c < len(cells)]
            if len(pts) >= 2:
                river_lines.append((r, LineString(pts)))
        land_props: dict[str, Any] = {}
        if detail and not land_cells.is_empty:
            from atlas import cartography as carto
            kmf = carto.KmFrame((land_cells.bounds[1] + land_cells.bounds[3]) / 2)
            cell_km = math.sqrt(kmf.to_km(land_cells).area / max(1, len(land)))
            info = self.raw.get("info", {})
            coast = carto.detailed_coast(
                land_cells, seed=carto.seed_of(info.get("seed"), info.get("mapName")), cell_km=cell_km,
                relief_ll=[(*self.lonlat(*c["p"]), int(c.get("h", 0))) for c in land],
                towns_ll=[self.lonlat(bg["x"], bg["y"]) for bg in burg_list],
                ports_ll=[self.lonlat(bg["x"], bg["y"]) for bg in burg_list if bg.get("port")], land_cells=len(land))
            prov_geom = carto.allocate(prov_geom, coast, land_cells)
            biome_geom = carto.allocate(biome_geom, coast, land_cells)
            river_lines = [(r, carto.river_to_coast(g, coast)) for r, g in river_lines]
            land_props = {"detail": "fractal"}
        else:
            coast = land_cells.simplify(0.02, preserve_topology=True)

        biome_feats = [{"type": "Feature", "geometry": mapping(g),
                        "properties": {"biome": k, "name": biomes.get(k, {}).get("name", str(k)),
                                       "color": biomes.get(k, {}).get("color", "#999999")}}
                       for k, g in biome_geom.items() if not g.is_empty]
        provs = {p["i"]: p for p in self.live(self.pack.get("provinces", []))}
        prov_feats = []
        for k, g in prov_geom.items():
            if g.is_empty:
                continue
            p = provs.get(k, {})
            prov_feats.append({"type": "Feature", "geometry": mapping(g),
                               "properties": {"pid": self.province_uid(k), "name": p.get("fullName") or p.get("name", ""),
                                              "azgaar_id": k}})

        def line(points: list[tuple[float, float]]) -> dict[str, Any]:
            return mapping(LineString(points))

        rivers = [{"type": "Feature", "geometry": mapping(g), "properties": {"name": r.get("name", ""), "id": r.get("i")}}
                  for r, g in river_lines if not g.is_empty]
        roads = []
        for r in self.pack.get("routes", []):
            pts = [self.lonlat(p[0], p[1]) for p in (r.get("points") or [])]
            if len(pts) >= 2:
                roads.append({"type": "Feature", "geometry": line(pts),
                              "properties": {"group": r.get("group", "roads"), "id": r.get("i")}})
        burgs = []
        for bg in burg_list:
            lon, lat = self.lonlat(bg["x"], bg["y"])
            burgs.append({"type": "Feature", "geometry": mapping(Point(lon, lat)),
                          "properties": {"name": bg.get("name", ""), "capital": bool(bg.get("capital")),
                                         "port": bool(bg.get("port")), "uid": self.uid("b", bg["i"], bg.get("name", ""))}})
        # relief: one point per land cell (height, biome), for maps drawn with terrain symbols (#54)
        relief = []
        for c in land:
            lon, lat = self.lonlat(*c["p"])
            relief.append({"type": "Feature", "geometry": {"type": "Point", "coordinates": [round(lon, 4), round(lat, 4)]},
                           "properties": {"h": int(c.get("h", 0)), "b": int(c.get("biome", 0))}})
        fc = lambda feats: {"type": "FeatureCollection", "features": feats}  # noqa: E731
        return {
            "relief": fc(relief),
            "land": fc([{"type": "Feature", "geometry": mapping(coast), "properties": land_props}]),
            "biomes": fc(biome_feats), "provinces": fc(prov_feats),
            "rivers": fc(rivers), "roads": fc(roads), "burgs": fc(burgs),
        }

    def bbox(self) -> list[float]:
        mc = self.raw["mapCoordinates"]
        return [mc["lonW"], mc["latN"] - mc["latT"], mc["lonW"] + mc["lonT"], mc["latN"]]

    # ------------------------------------------------------------------ graph documents
    def graph_docs(self, layers: dict[str, Any], *, day: int = 0) -> list[dict[str, Any]]:
        session = f"import/azgaar-{self.slug}"
        cells = self.cells
        docs: list[dict[str, Any]] = []
        states = self.live(self.pack.get("states", []))
        provinces = self.live(self.pack.get("provinces", []))
        burgs = {bg["i"]: bg for bg in self.live(self.pack.get("burgs", []))}
        cultures = {c["i"]: c.get("name") for c in self.pack.get("cultures", []) if isinstance(c, dict)}
        religions = {r["i"]: r.get("name") for r in self.pack.get("religions", []) if isinstance(r, dict)}
        prov_geom = {f["properties"]["azgaar_id"]: f for f in layers["provinces"]["features"]}

        land_by_state: dict[int, int] = {}
        pop_by_prov: dict[int, float] = {}
        cells_by_prov: dict[int, int] = {}
        for c in cells:
            if c.get("h", 0) >= 20:
                land_by_state[c.get("state", 0)] = land_by_state.get(c.get("state", 0), 0) + 1
                pid = c.get("province", 0)
                cells_by_prov[pid] = cells_by_prov.get(pid, 0) + 1
                pop_by_prov[pid] = pop_by_prov.get(pid, 0) + float(c.get("pop") or 0)

        S = {s["i"]: f"Faction/{self.state_uid(s['i'])}" for s in states}
        P = {p["i"]: f"Province/{self.province_uid(p['i'])}" for p in provinces}
        B = {bg_i: f"Location/{self.uid('b', bg_i, bg.get('name', ''))}" for bg_i, bg in burgs.items()}

        # Provinces (+ who controls them)
        for p in provinces:
            g = prov_geom.get(p["i"])
            centre = shape(g["geometry"]).representative_point() if g else None
            docs.append(b.entity("Province", p.get("fullName") or p.get("name", f"Province {p['i']}"),
                                 uid=self.province_uid(p["i"]), session=session,
                                 lon=round(centre.x, 4) if centre else None, lat=round(centre.y, 4) if centre else None,
                                 area_cells=cells_by_prov.get(p["i"], 0), population=int(pop_by_prov.get(p["i"], 0) * 1000),
                                 atlas=self.slug, aliases=[p["name"]] if p.get("fullName") and p.get("name") else [],
                                 description=f"Held by {next((s.get('fullName') or s['name'] for s in states if s['i'] == p.get('state')), 'no realm')} when the map was drawn."))
            if p.get("state") in S:
                docs.append({**b.control(S[p["state"]], "CONTROLS", P[p["i"]], start=day), "source_session": session})

        # Burgs -> places
        for bg_i, bg in burgs.items():
            lon, lat = self.lonlat(bg["x"], bg["y"])
            cell = cells[bg["cell"]] if 0 <= bg.get("cell", -1) < len(cells) else {}
            kind = "capital" if bg.get("capital") else ("city" if (bg.get("population") or 0) >= 10 else "town")
            tags = [t for t in (cultures.get(bg.get("culture")), religions.get(cell.get("religion")),
                                "port" if bg.get("port") else None) if t]
            docs.append(b.entity("Location", bg.get("name") or f"Burg {bg_i}", uid=self.uid("b", bg_i, bg.get("name", "")),
                                 session=session, location_type=kind, lon=lon, lat=lat,
                                 province=P.get(cell.get("province")), population=int((bg.get("population") or 0) * 1000),
                                 tags=tags))

        # States -> factions, with territory, relations, motives and knowledge
        for s in states:
            sid = S[s["i"]]
            size = land_by_state.get(s["i"], 0)
            docs.append(b.entity("Faction", s.get("fullName") or s["name"], uid=self.state_uid(s["i"]), session=session,
                                 aliases=[s["name"]] if s.get("fullName") else [], color=s.get("color"),
                                 is_major=True, resources=min(90, 20 + size // 6), influence=min(95, 25 + size // 5),
                                 headquarters=B.get(s.get("capital")),
                                 tags=[t for t in (s.get("form"), cultures.get(s.get("culture"))) if t],
                                 description=f"{s.get('form', 'Realm')} of the {cultures.get(s.get('culture'), 'unknown')} people."))
            own = [p for p in provinces if p.get("state") == s["i"]]
            for p in own:
                docs.append({**b.knows(sid, P[p["i"]], start=day), "source_session": session})
            if own:
                heart = max(own, key=lambda p: cells_by_prov.get(p["i"], 0))
                docs.append({**b.desire(sid, "NEEDS", target=P[heart["i"]], priority=0.8,
                                        note="Hold the heartland."), "source_session": session})
            dip = s.get("diplomacy") or []
            for other in states:
                if other["i"] == s["i"] or other["i"] >= len(dip):
                    continue
                word = dip[other["i"]]
                if word not in DIPLOMACY:
                    continue
                trust, label = DIPLOMACY[word]
                oid = S[other["i"]]
                docs.append({**b.relationship(sid, oid, trust, label, start=day), "source_session": session})
                docs.append({**b.knows(sid, oid, start=day), "source_session": session})
                if word in ("Rival", "Enemy") and other["i"] in (s.get("neighbors") or [other["i"]]):
                    border = [p for p in provinces if p.get("state") == other["i"]
                              and self._touches(p["i"], s["i"])] or [p for p in provinces if p.get("state") == other["i"]]
                    if not border:
                        continue
                    prize = max(border, key=lambda p: pop_by_prov.get(p["i"], 0))
                    docs.append({**b.knows(sid, P[prize["i"]], start=day), "source_session": session})
                    docs.append({**b.desire(sid, "WANTS", target=P[prize["i"]], priority=0.75 if word == "Enemy" else 0.6,
                                            note=f"Border claim against {other['name']}."), "source_session": session})
                    if word == "Enemy":
                        docs.append(b.goal(sid, f"Conquer {prize.get('name', 'the border')}", priority=0.75, segments=4,
                                           progress=1, every=3, target=P[prize["i"]], start=day, session=session,
                                           uid=self.uid("g", s["i"], f"conquer-{prize['i']}"),
                                           description=f"Take {prize.get('fullName') or prize.get('name')} from {other['name']}."))
                        docs.append({**b.desire(sid, "FEARS", target=oid, priority=0.55,
                                                note=f"At war footing with {other['name']}."), "source_session": session})
        return docs

    def _touches(self, province_i: int, state_i: int) -> bool:
        cache = getattr(self, "_touch_cache", None)
        if cache is None:
            cache = {}
            cells = self.cells
            for c in cells:
                if c.get("h", 0) < 20 or not c.get("province"):
                    continue
                for n in c.get("c") or []:
                    if 0 <= n < len(cells) and cells[n].get("h", 0) >= 20:
                        cache.setdefault(c["province"], set()).add(cells[n].get("state", 0))
            self._touch_cache = cache
        return state_i in cache.get(province_i, set())

    def atlas_doc(self, layer_names: list[str], has_tiles: bool, centre: tuple[float, float] | None) -> dict[str, Any]:
        return {"@type": "WorldAtlas", "@id": f"WorldAtlas/{self.slug}", "uid": self.slug, "name": self.name,
                "source": self.raw.get("info", {}).get("description", "azgaar")[:200],
                "seed": str(self.raw.get("info", {}).get("seed", "")), "bbox": json.dumps(self.bbox()),
                "centre": json.dumps(list(centre)) if centre else None,
                "imported_at": datetime.now(timezone.utc).isoformat(), "layers": json.dumps(layer_names),
                "has_tiles": has_tiles}


def distance_km(a: tuple[float, float], b2: tuple[float, float], radius_km: float = 6371.0) -> float:
    lon1, lat1, lon2, lat2 = map(math.radians, (*a, *b2))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 2 * radius_km * math.asin(math.sqrt(h))

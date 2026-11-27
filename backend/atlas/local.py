"""Zoom-in maps: paint a battle map of any spot on the globe, described from the world around it.

:func:`look` reads the world at a lon/lat: the biome underfoot, the province and who holds it, the nearest
settlement (how far, which way), a road, river or coast close by, creatures known in that province's wilds (or the
SRD's fauna for the biome), and recent public events nearby. :func:`paint_here` turns that into a place (a
``wilderness`` Location tagged ``local-map``, reused if you paint the same spot again) and asks the VTT to paint it;
the finished map is linked at true scale, so its footprint appears on the globe exactly where you zoomed in.
"""
from __future__ import annotations

import json
import math
import random
from functools import lru_cache
from pathlib import Path
from typing import Any

from atlas.travel import coords, haversine_km
from graph import builders as b
from graph.schema import stable_uid
from graph.store import GraphStore
from graph.world import WorldView
from srd import catalogue as srd

NEAR_KM = {"road": 3.0, "river": 3.0, "coast": 5.0, "event": 25.0, "same_spot": 0.5, "outskirts": 2.0}
NOUN = {"marine": "open water", "hot desert": "desert", "cold desert": "cold desert", "savanna": "savanna",
        "grassland": "grassland", "tropical seasonal forest": "forest", "temperate deciduous forest": "forest",
        "tropical rainforest": "jungle", "temperate rainforest": "rainforest", "taiga": "taiga", "tundra": "tundra",
        "glacier": "ice field", "wetland": "marsh"}
DIRS = ["north", "north-east", "east", "south-east", "south", "south-west", "west", "north-west"]


@lru_cache(maxsize=16)
def _layer(path: str, mtime: float) -> list[tuple[dict[str, Any], Any]]:
    from shapely.geometry import shape
    from shapely.prepared import prep
    out = []
    for f in json.loads(Path(path).read_text(encoding="utf-8")).get("features", []):
        if f.get("geometry"):
            g = shape(f["geometry"])
            out.append((f.get("properties") or {}, g, prep(g)))
    return out


def _features(atlas_dir: Path, layer: str):
    p = atlas_dir / f"{layer}.geojson"
    return _layer(str(p), p.stat().st_mtime) if p.exists() else []


def _km(geom, lon: float, lat: float) -> float:
    """Approximate km from a point to a geometry (degrees scaled for latitude)."""
    from shapely.geometry import Point
    d = geom.distance(Point(lon, lat))
    return d * 111.32 * (1 + math.cos(math.radians(lat))) / 2


def bearing(a: tuple[float, float], b_: tuple[float, float]) -> str:
    """Which way b lies from a, as a compass word."""
    dx = (b_[0] - a[0]) * math.cos(math.radians((a[1] + b_[1]) / 2))
    dy = b_[1] - a[1]
    ang = (math.degrees(math.atan2(dx, dy)) + 360) % 360
    return DIRS[int((ang + 22.5) // 45) % 8]


def look(w: WorldView, atlas_dir: Path, lon: float, lat: float) -> dict[str, Any]:
    """What is at this spot, from the atlas layers and the world."""
    from shapely.geometry import Point
    pt = Point(lon, lat)
    out: dict[str, Any] = {"lon": round(lon, 6), "lat": round(lat, 6)}

    biome = next((p for p, _, pg in _features(atlas_dir, "biomes") if pg.contains(pt)), None)
    on_land = any(pg.contains(pt) for _, _, pg in _features(atlas_dir, "land"))
    out["biome"] = {"index": int(biome.get("biome") or 0), "name": biome.get("name")} if biome else None
    out["on_land"] = on_land
    pid = next((p.get("pid") for p, _, pg in _features(atlas_dir, "provinces") if pg.contains(pt)), None)
    prov = w.get(f"Province/{pid}") if pid else None
    out["province"] = {"id": prov["@id"], "name": prov["name"]} if prov else None
    holder = w.controller_of(prov["@id"]) if prov else None
    out["holder"] = {"id": holder, "name": w.name(holder)} if holder else None

    best = None
    for loc in w.of_type("Location"):
        if loc.get("location_type") not in ("capital", "city", "town", "village", "district") or loc.get("parent"):
            continue
        c = coords(w, loc["@id"])
        if c:
            km = haversine_km((lon, lat), c)
            if not best or km < best[0]:
                best = (km, loc, c)
    if best:
        out["nearest"] = {"id": best[1]["@id"], "name": best[1]["name"], "km": round(best[0], 1),
                          "direction": bearing(best[2], (lon, lat))}  # which way the spot lies from the town

    road = min((_km(g, lon, lat) for _, g, _ in _features(atlas_dir, "roads")), default=None)
    out["road_km"] = round(road, 1) if road is not None else None
    rivers = [(_km(g, lon, lat), p.get("name")) for p, g, _ in _features(atlas_dir, "rivers")]
    river = min(rivers, default=None)
    out["river"] = {"name": river[1], "km": round(river[0], 1)} if river else None
    coast = min((_km(g.boundary, lon, lat) for _, g, _ in _features(atlas_dir, "land")), default=None)
    out["coast_km"] = round(coast, 1) if coast is not None else None

    creatures = []
    if prov:
        wilds = {l["@id"] for l in w.of_type("Location") if l.get("province") == prov["@id"]}
        creatures = [m["name"] for m in w.of_type("Monster") if m.get("location") in wilds][:3]
    if not creatures and out["biome"]:
        rng = random.Random(f"{round(lon, 3)},{round(lat, 3)}")
        creatures = [m["name"] for m in srd.monsters_for_biome(out["biome"]["index"], rng, k=2, max_cr=4)]
    out["creatures"] = creatures

    events = []
    for e in w.of_type("Event"):
        if e.get("visibility") == "secret" or (e.get("in_world_start") or 0) < w.day - 30:
            continue
        c = coords(w, e.get("location")) or next((coords(w, t) for t in e.get("targets", []) if coords(w, t)), None)
        if c and haversine_km((lon, lat), c) <= NEAR_KM["event"]:
            events.append(e.get("summary") or e["name"])
    out["events"] = events[:3]
    return out


def describe(seen: dict[str, Any]) -> tuple[str, str]:
    """A name and a short description of the spot for the map brief."""
    biome = (seen.get("biome") or {}).get("name") or ("open water" if not seen.get("on_land") else "wild country")
    noun = NOUN.get(biome.lower(), biome.lower())
    near = seen.get("nearest")
    if near and near["km"] <= NEAR_KM["outskirts"]:
        name = f"Outskirts of {near['name']}"
    elif near:
        name = f"{noun.capitalize()} {near['direction']} of {near['name']}"
    else:
        name = noun.capitalize()
    bits = [f"{biome.capitalize()}" + (f", {near['km']:g} km {near['direction']} of {near['name']}" if near else "")]
    if seen.get("road_km") is not None and seen["road_km"] <= NEAR_KM["road"]:
        bits.append("beside a road")
    river = seen.get("river")
    if river and river["km"] <= NEAR_KM["river"]:
        bits.append(f"on the banks of the {river['name']}" if river.get("name") else "on a riverbank")
    if seen.get("on_land") and seen.get("coast_km") is not None and seen["coast_km"] <= NEAR_KM["coast"]:
        bits.append("near the coast")
    text = ", ".join(bits) + "."
    if seen.get("province"):
        text += f" In {seen['province']['name']}" + (f", held by {seen['holder']['name']}." if seen.get("holder") else ".")
    if seen.get("creatures"):
        text += f" Known in these wilds: {', '.join(seen['creatures'])}."
    if seen.get("events"):
        text += " Lately: " + "; ".join(x.rstrip(".") for x in seen["events"]) + "."
    return name[:80], text


def _atlas_dir(w: WorldView) -> Path:
    from atlas.service import atlas_path
    atlases = [d for d in w.all.values() if d["@type"] == "WorldAtlas"]
    if not atlases:
        raise ValueError("There's no atlas in this world yet")
    return atlas_path(max(atlases, key=lambda d: d.get("imported_at") or "")["uid"])


def brief(store: GraphStore, lon: float, lat: float, branch: str = "main", day: int | None = None) -> dict[str, Any]:
    from srd import homebrew
    docs = store.all_documents(branch)
    w = WorldView(docs, day if day is not None else WorldView(docs).max_day())
    with srd.homebrew(homebrew.overlay_entries(store)):  # the DM's own fauna for the biome too
        seen = look(w, _atlas_dir(w), lon, lat)
    name, text = describe(seen)
    existing = _existing_spot(w, lon, lat)
    return {"name": existing["name"] if existing else name, "description": text, "day": w.day, "look": seen,
            "existing": existing["@id"] if existing else None}


def _existing_spot(w: WorldView, lon: float, lat: float) -> dict[str, Any] | None:
    for l in w.of_type("Location"):
        if "local-map" in (l.get("tags") or []) and l.get("lon") is not None \
                and haversine_km((lon, lat), (l["lon"], l["lat"])) <= NEAR_KM["same_spot"]:
            return l
    return None


def paint_here(store: GraphStore, lon: float, lat: float, *, branch: str = "main", day: int | None = None,
               name: str | None = None, description: str | None = None, style: str = "",
               animate: bool = False) -> dict[str, Any]:
    """Make (or reuse) a place at this spot and have the VTT paint it; poll the job with ``vtt_link.session.collect``."""
    from vtt_link.session import paint_map
    info = brief(store, lon, lat, branch, day)
    place_id = info["existing"]
    if not place_id:
        seen = info["look"]
        uid = stable_uid("spot", round(lon, 4), round(lat, 4))
        doc = b.entity("Location", (name or info["name"]).strip()[:80], uid=uid, start=info["day"], session=f"dm/{branch}",
                       location_type="wilderness", lon=round(lon, 6), lat=round(lat, 6),
                       province=(seen.get("province") or {}).get("id"), tags=["local-map"],
                       description=(description or info["description"]).strip()[:600])
        store.upsert([doc], branch, f"[atlas] mark '{doc['name']}' for a local map from day {info['day']}")
        place_id = doc["@id"]
    out = paint_map(store, place_id, branch, info["day"], style=style, animate=animate)
    return {"place": place_id, "name": name or info["name"], **out}

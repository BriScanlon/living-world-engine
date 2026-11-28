"""World Atlas service: import a planet, build its tiles, and answer "what is the map on day D of branch B?"."""
from __future__ import annotations

import hashlib
import json
import logging
import shutil
import subprocess
from pathlib import Path
from typing import Any

from atlas import party
from atlas.azgaar import AzgaarWorld
from atlas.sample_world import write_demo_region
from config import get_settings
from graph import builders as b
from graph.store import GraphStore
from graph.world import WorldView

log = logging.getLogger(__name__)
LAYERS = ["land", "biomes", "provinces", "rivers", "roads", "burgs"]
EVENT_WINDOW_DAYS = 30


def atlas_root() -> Path:
    return Path(get_settings().atlas_dir)


def atlas_path(slug: str) -> Path:
    return atlas_root() / slug


# =========================================================================== import
def build_tiles(folder: Path) -> bool:
    """GeoJSON layers -> one PMTiles archive (tippecanoe >= 2.17 writes .pmtiles directly)."""
    exe = shutil.which(get_settings().tippecanoe_bin)
    if not exe:
        log.info("tippecanoe not found: the atlas will serve GeoJSON layers instead of PMTiles")
        return False
    out = folder / "tiles.pmtiles"
    cmd = [exe, "-o", str(out), "--force", "-Z0", "-z9", "--no-tile-size-limit", "--no-feature-limit",
           "--quiet"]
    for name in LAYERS:
        cmd += ["-L", f"{name}:{folder / f'{name}.geojson'}"]
    try:
        subprocess.run(cmd, check=True, capture_output=True, timeout=600)
        return out.exists()
    except (subprocess.SubprocessError, OSError) as e:
        log.warning("tippecanoe failed, falling back to GeoJSON: %s", getattr(e, "stderr", e))
        return False


def import_world(store: GraphStore, path: str | Path, *, branch: str = "main", slug: str | None = None,
                 place_greywater: bool = True) -> dict[str, Any]:
    world = AzgaarWorld.load(path, slug)
    folder = atlas_path(world.slug)
    folder.mkdir(parents=True, exist_ok=True)
    layers = world.layers()
    for name, fc in layers.items():
        (folder / f"{name}.geojson").write_text(json.dumps(fc), encoding="utf-8")
    has_tiles = build_tiles(folder)

    docs = world.graph_docs(layers)
    placed = None
    if place_greywater:
        docs, placed = _place_greywater(store, branch, world, docs)
    centre = placed["centre"] if placed else None
    if centre is None:
        bb = world.bbox()
        centre = ((bb[0] + bb[2]) / 2, (bb[1] + bb[3]) / 2)
    docs.append(world.atlas_doc([n for n in layers if n in LAYERS], has_tiles, centre))  # relief: renderers only
    store.upsert(docs, branch, f"[atlas] import {world.name} from Azgaar: "
                               f"{sum(d['@type'] == 'Faction' for d in docs)} realms, "
                               f"{sum(d['@type'] == 'Province' for d in docs)} provinces, "
                               f"{sum(d['@type'] == 'Location' for d in docs)} places")
    return {"slug": world.slug, "name": world.name, "documents": len(docs), "tiles": has_tiles,
            "greywater": placed}


def _place_greywater(store: GraphStore, branch: str, world: AzgaarWorld,
                     docs: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    """Local-out: put the hand-built Greywater demo on a coastal town of the imported planet.

    Greywater takes over one port burg (its coordinates and province); House Vane becomes a
    vassal of the realm that holds that province, so the two layers of the world talk.
    """
    existing = {d["@id"]: d for d in store.all_documents(branch)}
    town = existing.get("Location/greywater")
    if not town:
        return docs, None
    by_id = {d["@id"]: d for d in docs}
    rivalrous = {d["from_id"] for d in docs if d["@type"] == "RelationshipEdge" and d["label"] in ("rival", "enemy")}
    holder = {d["target_id"]: d["from_id"] for d in docs if d["@type"] == "ControlEdge"}
    ports = [d for d in docs if d["@type"] == "Location" and "port" in d.get("tags", [])
             and d.get("location_type") != "capital" and abs(d.get("lat") or 90) < 50 and d.get("province")]
    ports.sort(key=lambda d: (holder.get(d["province"]) not in rivalrous, d["uid"]))
    if not ports:
        return docs, None
    host = ports[0]
    realm = holder.get(host["province"])
    docs = [d for d in docs if d["@id"] != host["@id"]]
    lon, lat = host["lon"], host["lat"]
    offsets = {"Location/greywater": (0, 0), "Location/greywater-docks": (0.18, -0.06),
               "Location/vane-manor": (-0.12, 0.1), "Location/the-drowned-lantern": (0.08, 0.04),
               "Location/blackfen-marsh": (0.35, -0.7)}
    for lid, (dx, dy) in offsets.items():
        if lid in existing:
            d = dict(existing[lid])
            d.update(lon=round(lon + dx, 4), lat=round(lat + dy, 4), province=host["province"])
            if lid == "Location/greywater":
                d["aliases"] = sorted(set(d.get("aliases", [])) | {host["name"]})
                d["population"] = host.get("population")
            docs.append(d)
    if realm and "Faction/house-vane" in existing:
        docs += [b.relationship("Faction/house-vane", realm, 45, "vassal"),
                 b.relationship(realm, "Faction/house-vane", 25, "liege"),
                 b.knows("Faction/house-vane", realm), b.knows(realm, "Faction/house-vane"),
                 b.knows("Character/lord-aldric-vane", realm), b.knows("Faction/house-vane", host["province"])]
    return docs, {"host_burg": host["name"], "province": host["province"], "realm": realm,
                  "realm_name": (by_id.get(realm) or {}).get("name"), "centre": (lon, lat)}


DEMO_SLUG = "thessaly-reach"
GREYWATER_PLACES = {"Location/greywater", "Location/greywater-docks", "Location/vane-manor",
                    "Location/the-drowned-lantern", "Location/blackfen-marsh"}


def bootstrap_sample(store: GraphStore, seed: int = 1795) -> dict[str, Any] | None:
    """First-run demo: generate the demo region and import it into main once (Greywater goes on one of its ports)."""
    if store.all_documents("main", doc_type="WorldAtlas"):
        return None
    path = atlas_root() / "sample" / "demo-region.full.json"
    if not path.exists():
        write_demo_region(path, seed)
    return import_world(store, path, slug=DEMO_SLUG)


def map_documents(docs: list[dict[str, Any]], slugs: list[str], keep: set[str] = GREYWATER_PLACES) -> set[str]:
    """Everything an imported map brought (its realms, provinces, towns, people, the WorldAtlas doc), and everything
    that points at those (edges, marks, a zoom-in spot and its battle-map link...), except the places in ``keep``,
    which a new map re-places."""
    drop = {d["@id"] for d in docs if d.get("source_session") in {f"import/azgaar-{s}" for s in slugs}}
    drop |= {d["@id"] for d in docs if d["@type"] == "WorldAtlas"}

    def refs(d: dict[str, Any]) -> bool:
        for k, v in d.items():
            if k in ("@id", "@type"):
                continue
            if isinstance(v, str) and v in drop:
                return True
            if isinstance(v, list) and any(isinstance(x, str) and x in drop for x in v):
                return True
        return False

    while True:
        more = {d["@id"] for d in docs if d["@id"] not in drop and d["@id"] not in keep and refs(d)}
        if not more:
            return drop
        drop |= more


def move_demo_atlas(store: GraphStore, *, branch: str = "main", seed: int = 1795, dry_run: bool = False) -> dict[str, Any]:
    """Move Greywater from whatever map it sits on to a freshly generated demo region (#44).

    The old map's documents, and whatever points at them, are removed in one commit (TerminusDB keeps them in its
    history; forecast branches made on the old map are left as they are), then the region is imported and Greywater
    placed on one of its ports. ``dry_run`` only counts what would go."""
    docs = store.all_documents(branch)
    slugs = sorted({d.get("slug") or d["@id"].split("/", 1)[1] for d in docs if d["@type"] == "WorldAtlas"})
    drop = map_documents(docs, slugs)
    counts: dict[str, int] = {}
    for d in docs:
        if d["@id"] in drop:
            counts[d["@type"]] = counts.get(d["@type"], 0) + 1
    out: dict[str, Any] = {"old_maps": slugs, "remove": len(drop), "by_type": counts}
    if dry_run:
        return out
    if drop:
        store.delete(sorted(drop), branch, f"[atlas] remove the old map ({', '.join(slugs)}): Greywater moves to a region")
    path = atlas_root() / "sample" / "demo-region.full.json"
    write_demo_region(path, seed)
    out["imported"] = import_world(store, path, branch=branch, slug=DEMO_SLUG)
    return out


# =========================================================================== live state
def _colour(fid: str) -> str:
    h = int(hashlib.sha1(fid.encode()).hexdigest()[:6], 16)
    return f"hsl({h % 360}, 45%, 52%)"


def _coords(w: WorldView, doc_id: str | None, depth: int = 0) -> tuple[float, float] | None:
    d = w.all.get(doc_id) if doc_id else None
    if not d or depth > 4:
        return None
    if d.get("lon") is not None and d.get("lat") is not None:
        return d["lon"], d["lat"]
    for k in ("location", "parent", "headquarters", "province"):
        c = _coords(w, d.get(k), depth + 1)
        if c:
            return c
    return None


def atlas_state(store: GraphStore, branch: str = "main", day: int | None = None,
                compare: str | None = None, view: str = "dm") -> dict[str, Any]:
    """Live map state. ``view="party"`` shows only what the party knows (see :mod:`atlas.party`)."""
    docs = store.all_documents(branch)
    full = WorldView(docs)
    atlases = [d for d in full.all.values() if d["@type"] == "WorldAtlas"]
    if not atlases:
        return {"atlas": None}
    a = max(atlases, key=lambda d: d.get("imported_at") or "")  # the most recently imported world
    # "No day" means "now on this branch": the latest day it knows about, so ended facts drop out.
    the_day = day if day is not None else full.max_day()
    w = WorldView(docs, the_day)
    cw = WorldView(store.all_documents(compare), the_day) if compare and compare != branch else None

    pv = party.view(w) if view == "party" else None
    if pv:
        cw = None  # the party can't compare timelines

    factions = {f["@id"]: f for f in w.of_type("Faction") if not pv or f["@id"] in pv.factions}
    provinces = {}
    for p in w.of_type("Province"):
        ctl = w.controller_of(p["@id"])
        before = cw.controller_of(p["@id"]) if cw else None
        known = pv.provinces.get(p["@id"]) if pv else party.VISITED
        if known != party.VISITED:
            ctl = None  # heard of, or unknown: who holds it isn't something the party can see
        provinces[p["uid"]] = {
            "id": p["@id"], "name": p["name"] if known else None, "lon": p.get("lon"), "lat": p.get("lat"),
            "controller": ctl, "controller_name": w.name(ctl) if ctl else None,
            "color": (factions.get(ctl) or {}).get("color") or (_colour(ctl) if ctl else "#9aa3ad"),
            "changed": bool(cw) and before != ctl, "previous": w.name(before) if cw and before != ctl else None,
            "known": known,
        }
    held: dict[str, int] = {}
    for p in provinces.values():
        if p["controller"]:
            held[p["controller"]] = held.get(p["controller"], 0) + 1

    people: dict[str, list[str]] = {}
    for c in w.of_type("Character"):
        if c.get("location"):
            people.setdefault(c["location"], []).append(c["name"])
    places = []
    for loc in w.of_type("Location"):
        if pv and loc["@id"] not in pv.places and loc.get("location_type") != "continent":
            continue
        xy = _coords(w, loc["@id"])
        if not xy:
            continue
        places.append({"type": "Feature", "geometry": {"type": "Point", "coordinates": list(xy)},
                       "properties": {"id": loc["@id"], "name": loc["name"], "kind": loc.get("location_type") or "place",
                                      "people": [] if pv else people.get(loc["@id"], []),
                                      "known": pv.places.get(loc["@id"]) if pv else None,
                                      "here": bool(pv) and pv.where == loc["@id"],
                                      "campaign": not loc.get("source_session", "").startswith("import/"),
                                      # where play begins: the wizard's start town, or the hand-built demo's town
                                      "start": "campaign-start" in loc.get("tags", [])
                                      or (loc.get("location_type") == "town"
                                          and loc.get("source_session", "").startswith("session-"))}})

    base_events = {e["@id"] for e in cw.of_type("Event")} if cw else set()
    events = []
    for e in w.of_type("Event"):
        start = e.get("in_world_start") or 0
        if start < the_day - EVENT_WINDOW_DAYS or (pv and e["@id"] not in pv.events):
            continue
        xy = _coords(w, e.get("location"))
        for t in e.get("targets", []) + e.get("actors", []):
            xy = xy or _coords(w, t)
        if not xy:
            continue
        events.append({"type": "Feature", "geometry": {"type": "Point", "coordinates": list(xy)},
                       "properties": {"id": e["@id"], "title": e["name"], "summary": e.get("summary"), "day": start,
                                      "event_type": e.get("event_type"), "visibility": e.get("visibility"),
                                      "age": the_day - start, "new": bool(cw) and e["@id"] not in base_events}})
    rumors = []
    for r in w.of_type("Rumor"):
        if pv and r["@id"] not in pv.rumors:
            continue
        xy = _coords(w, r.get("location"))
        if xy:
            rumors.append({"type": "Feature", "geometry": {"type": "Point", "coordinates": list(xy)},
                           "properties": {"id": r["@id"], "dialogue": r.get("dialogue"),
                                          "accuracy": None if pv else r.get("accuracy"),
                                          "speaker": r.get("speaker"), "day": r.get("in_world_start")}})
    fc = lambda feats: {"type": "FeatureCollection", "features": feats}  # noqa: E731
    return {
        "atlas": {"slug": a["uid"], "name": a["name"], "bbox": json.loads(a["bbox"]),
                  "centre": json.loads(a["centre"]) if a.get("centre") else None,
                  "layers": json.loads(a["layers"]), "has_tiles": a["has_tiles"]},
        "branch": branch, "compare": None if pv else compare, "day": the_day, "max_day": full.max_day(),
        "view": "party" if pv else "dm",
        "party": _party_summary(w, pv) if view == "party" else None,
        "factions": sorted(({"id": fid, "name": f["name"], "color": f.get("color") or _colour(fid),
                             "provinces": held.get(fid, 0), "party": bool(f.get("is_party"))}
                            for fid, f in factions.items()), key=lambda f: (-f["provinces"], f["name"])),
        "provinces": provinces,
        "places": fc(places), "events": fc(events), "rumors": fc(rumors),
        "changed_provinces": sum(p["changed"] for p in provinces.values()),
        "maps": [m for m in _footprints(w) if not pv or pv.places.get(m["place"]) == party.VISITED],
    }


def _party_summary(w: WorldView, pv: party.PartyView | None) -> dict[str, Any]:
    if not pv or not pv.party:
        return {"name": None, "where": None, "visited": 0, "heard": 0, "provinces": 0}
    return {"name": pv.party["name"], "where": w.name(pv.where) if pv.where else None, "where_id": pv.where,
            "visited": sum(v == party.VISITED for v in pv.places.values()),
            "heard": sum(v == party.HEARD for v in pv.places.values()),
            "provinces": len(pv.provinces)}


def place_detail(store: GraphStore, doc_id: str, branch: str = "main", day: int | None = None,
                 view: str = "dm") -> dict[str, Any]:
    """Everything the engine knows about one place: who holds it, who's there, what happened, what's said.

    With ``view="party"`` only what the party knows; an unknown place returns ``{}``.
    """
    docs = store.all_documents(branch)
    w = WorldView(docs, day if day is not None else WorldView(docs).max_day())
    d = w.get(doc_id)
    if not d:
        return {}
    pv = party.view(w) if view == "party" else None
    known_state = pv.place_state(doc_id) if pv else None
    if pv and not known_state:
        return {}
    if pv:
        return {**_party_place(w, d, pv, known_state), "travel": _travel(w, pv, doc_id)}
    marks = party.view(w)
    inside = {doc_id} | {l["@id"] for l in w.of_type("Location") if l.get("parent") == doc_id or l.get("province") == doc_id}
    ctl = w.controller_of(doc_id) or (w.controller_of(d["province"]) if d.get("province") else None)
    return {
        "id": doc_id, "type": d["@type"], "name": d["name"], "description": d.get("description"),
        "kind": d.get("location_type"), "population": d.get("population"),
        "controller": {"id": ctl, "name": w.name(ctl)} if ctl else None,
        "province": {"id": d["province"], "name": w.name(d["province"])} if d.get("province") else None,
        "people": [{"id": c["@id"], "name": c["name"], "role": c.get("role"), "species": c.get("species"),
                    "char_class": c.get("char_class"), "srd_ref": c.get("srd_ref")} for c in w.of_type("Character")
                   if c.get("location") in inside],
        "monsters": [{"id": m["@id"], "name": m["name"], "challenge": m.get("challenge"), "srd_ref": m.get("srd_ref")}
                     for m in w.of_type("Monster") if m.get("location") in inside],
        "events": sorted(({"id": e["@id"], "title": e["name"], "day": e.get("in_world_start"), "summary": e.get("summary")}
                          for e in w.of_type("Event") if e.get("location") in inside or set(e.get("targets", [])) & inside),
                         key=lambda e: -(e["day"] or 0))[:12],
        "rumors": [{"dialogue": r.get("dialogue"), "accuracy": r.get("accuracy"), "speaker": r.get("speaker")}
                   for r in w.of_type("Rumor") if r.get("location") in inside][:8],
        "places": [{"id": l["@id"], "name": l["name"], "kind": l.get("location_type")} for l in w.of_type("Location")
                   if l["@id"] in inside and l["@id"] != doc_id][:20],
        "maps": [{"id": m["@id"], "name": m["name"], "kind": m.get("kind"), "vtt_map_id": m["vtt_map_id"],
                  "from_day": m.get("in_world_start"),
                  "thumb": f"/api/vtt/media/{m['thumb_path']}" if m.get("thumb_path") else None}
                 for m in w.of_type("Map") if m.get("place") == doc_id],
        "travel": _travel(w, marks, doc_id),
        "party": {"state": marks.place_state(doc_id), "here": marks.where == doc_id,
                  "marked": next((e["kind"] for e in w.of_type("EpistemicEdge") if marks.party
                                  and e["agent_id"] == marks.party["@id"] and e["fact_or_event_id"] == doc_id), None)}
        if marks.party else None,
    }


def _travel(w: WorldView, pv: party.PartyView, doc_id: str) -> dict[str, Any] | None:
    """How far this place is from where the party is now, by the quickest route."""
    if not pv.where or pv.where == doc_id:
        return None
    from atlas.travel import for_world
    r = for_world(w).between(pv.where, doc_id)
    if not r or r.km < 0.5:
        return None
    return {"from": w.name(pv.where), "from_id": pv.where, **r.summary()}


def _party_place(w: WorldView, d: dict[str, Any], pv: party.PartyView, state: str) -> dict[str, Any]:
    """A place as the party knows it: first-hand if visited, by name and hearsay if not."""
    doc_id = d["@id"]
    inside = {doc_id} | {l["@id"] for l in w.of_type("Location") if l.get("parent") == doc_id or l.get("province") == doc_id}
    seen = state == party.VISITED
    ctl = (w.controller_of(doc_id) or (w.controller_of(d["province"]) if d.get("province") else None)) if seen else None
    return {
        "id": doc_id, "type": d["@type"], "name": d["name"], "description": d.get("description") if seen else None,
        "kind": d.get("location_type"), "population": d.get("population") if seen else None,
        "controller": {"id": ctl, "name": w.name(ctl)} if ctl else None,
        "province": {"id": d["province"], "name": w.name(d["province"])}
        if d.get("province") and d["province"] in pv.provinces else None,
        "people": [{"id": c["@id"], "name": c["name"], "role": c.get("role"), "species": c.get("species"),
                    "char_class": None, "srd_ref": None} for c in w.of_type("Character")
                   if c.get("location") in inside and c["@id"] in pv.known and c["@id"] not in pv.agents],
        "monsters": [],
        "events": sorted(({"id": e["@id"], "title": e["name"], "day": e.get("in_world_start"), "summary": e.get("summary")}
                          for e in w.of_type("Event") if e["@id"] in pv.events
                          and (e.get("location") in inside or set(e.get("targets", [])) & inside)),
                         key=lambda e: -(e["day"] or 0))[:12],
        "rumors": [{"dialogue": r.get("dialogue"), "accuracy": None, "speaker": r.get("speaker")}
                   for r in w.of_type("Rumor") if r["@id"] in pv.rumors and r.get("location") in inside][:8],
        "places": [{"id": l["@id"], "name": l["name"], "kind": l.get("location_type")} for l in w.of_type("Location")
                   if l["@id"] in inside and l["@id"] != doc_id and l["@id"] in pv.places][:20],
        "maps": [],
        "party": {"state": state, "here": pv.where == doc_id, "marked": None},
        "view": "party",
    }


def _footprints(w: WorldView) -> list[dict[str, Any]]:
    from vtt_link.scene import footprints
    return footprints(w)

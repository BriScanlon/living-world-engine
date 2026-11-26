"""Link places to VTT battle maps, and build the DM-only "scene" the VTT shows for a place.

A scene is a self-contained snapshot pushed to the VTT (it never calls back): who is at the place,
who holds it, what they want and fear, tensions between them, rumors to read aloud, creatures with
their SRD stat lines, recent events and plans in motion, for one branch and in-world day.
"""
from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any

from graph import builders as b
from graph.schema import stable_uid
from graph.store import GraphStore
from graph.world import WorldView
from srd import catalogue as srd
from vtt_link import client

AI_MAP_FEET_ACROSS = 140  # the VTT paints AI maps 140 ft wide


def _world(store: GraphStore, branch: str, day: int | None) -> WorldView:
    docs = store.all_documents(branch)
    return WorldView(docs, day if day is not None else WorldView(docs).max_day())


def _coords(w: WorldView, doc_id: str | None, depth: int = 0) -> tuple[float, float] | None:
    d = w.all.get(doc_id) if doc_id else None
    if not d or depth > 4:
        return None
    if d.get("lon") is not None and d.get("lat") is not None:
        return d["lon"], d["lat"]
    for k in ("parent", "location", "province"):
        c = _coords(w, d.get(k), depth + 1)
        if c:
            return c
    return None


def _stats(ref: str | None) -> dict[str, Any] | None:
    e = srd.by_id(ref) if ref else None
    if not e or "ac" not in e:
        return None
    return {"name": e["name"], "ac": e["ac"], "hp": e["hp"], "cr": e.get("cr"), "type": e.get("type")}


# --------------------------------------------------------------------------- maps
def maps_for(store: GraphStore, place_id: str, branch: str = "main", day: int | None = None) -> list[dict[str, Any]]:
    w = _world(store, branch, day)
    out = []
    for m in w.of_type("Map"):
        if m.get("place") == place_id:
            out.append({"id": m["@id"], "name": m["name"], "kind": m.get("kind"), "vtt_map_id": m["vtt_map_id"],
                        "from_day": m.get("in_world_start"), "thumb_path": m.get("thumb_path"),
                        "metres_per_px": m.get("metres_per_px"), "width_px": m.get("width_px")})
    return sorted(out, key=lambda m: -(m["from_day"] or 0))


def link_map(store: GraphStore, place_id: str, vtt_map_id: str, *, branch: str = "main", day: int = 0,
             kind: str = "battle", rotation_deg: float = 0.0, replace: bool = True) -> dict[str, Any]:
    """Attach a VTT map to a place from ``day``. With ``replace`` the place's current map ends that day."""
    w = _world(store, branch, day)
    place = w.all.get(place_id)
    if not place or place["@type"] != "Location":
        raise ValueError(f"{place_id} is not a place")
    asset = client.get_map(vtt_map_id)
    try:
        view = client.map_view(vtt_map_id)
        mpp = view.get("metresPerPx")
    except client.VTTError:
        mpp = None
    if not mpp and asset.get("width"):
        mpp = AI_MAP_FEET_ACROSS * 0.3048 / asset["width"]
    xy = _coords(w, place_id) or (None, None)
    docs = []
    if replace:
        for m in w.of_type("Map"):
            if m.get("place") == place_id and m.get("in_world_end") is None:
                docs.append(b.end(m, day))
    uid = stable_uid("map", place_id, vtt_map_id, day)
    doc = b.entity("Map", asset.get("title") or "Battle map", uid=uid, start=day, session=f"vtt/{branch}",
                   place=place_id, kind=kind, vtt_map_id=vtt_map_id, width_px=asset.get("width"),
                   height_px=asset.get("height"), metres_per_px=round(mpp, 6) if mpp else None,
                   rotation_deg=rotation_deg, anchor_lon=xy[0], anchor_lat=xy[1],
                   thumb_path=(asset.get("thumb_url") or "").removeprefix("/media/") or None,
                   media_type=asset.get("media_type"),
                   description=asset.get("description"))
    docs.append(doc)
    store.upsert(docs, branch, f"[vtt] link '{doc['name']}' to {place['name']} from day {day}")
    return {"map": doc["@id"], "place": place_id, "metres_per_px": doc.get("metres_per_px")}


def unlink_map(store: GraphStore, map_doc_id: str, *, branch: str = "main", day: int | None = None) -> None:
    w = _world(store, branch, None)
    m = w.all.get(map_doc_id)
    if not m or m["@type"] != "Map":
        raise ValueError(f"{map_doc_id} is not a linked map")
    store.upsert([b.end(m, day if day is not None else w.max_day())], branch, f"[vtt] unlink '{m['name']}'")


def footprints(w: WorldView) -> list[dict[str, Any]]:
    """Each linked map as a georeferenced rectangle (lon/lat corners) for the atlas."""
    out = []
    for m in w.of_type("Map"):
        lon, lat, mpp = m.get("anchor_lon"), m.get("anchor_lat"), m.get("metres_per_px")
        wpx, hpx = m.get("width_px"), m.get("height_px")
        if None in (lon, lat, mpp, wpx, hpx):
            continue
        half_w, half_h = wpx * mpp / 2, hpx * mpp / 2
        rot = math.radians(m.get("rotation_deg") or 0)
        m_per_deg_lat = 110_540.0
        m_per_deg_lon = 111_320.0 * max(0.05, math.cos(math.radians(lat)))
        corners = []
        for sx, sy in ((-1, 1), (1, 1), (1, -1), (-1, -1)):  # top-left, top-right, bottom-right, bottom-left
            x, y = sx * half_w, sy * half_h
            xr, yr = x * math.cos(rot) - y * math.sin(rot), x * math.sin(rot) + y * math.cos(rot)
            corners.append([round(lon + xr / m_per_deg_lon, 7), round(lat + yr / m_per_deg_lat, 7)])
        out.append({"id": m["@id"], "name": m["name"], "place": m["place"], "vtt_map_id": m["vtt_map_id"],
                    "thumb_path": m.get("thumb_path"), "corners": corners,
                    "width_m": round(wpx * mpp, 1), "height_m": round(hpx * mpp, 1)})
    return out


# --------------------------------------------------------------------------- scene
def build_scene(store: GraphStore, place_id: str, branch: str = "main", day: int | None = None,
                map_title: str | None = None, vtt_map_id: str | None = None) -> dict[str, Any]:
    w = _world(store, branch, day)
    place = w.get(place_id)
    if not place:
        raise ValueError(f"{place_id} doesn't exist on {branch} at day {w.day}")
    inside = {place_id} | {l["@id"] for l in w.of_type("Location") if l.get("parent") == place_id}
    ctl = w.controller_of(place_id) or (w.controller_of(place.get("parent")) if place.get("parent") else None) \
        or (w.controller_of(place["province"]) if place.get("province") else None)
    province = place.get("province") or (w.get(place.get("parent")) or {}).get("province")
    realm = w.controller_of(province) if province else None

    def desires(agent: str, kinds: tuple[str, ...]) -> list[str]:
        out = []
        for d in w.desires_of(agent):
            if d["kind"] in kinds:
                out.append(w.name(d.get("target_id")) if d.get("target_id") else (d.get("target_text") or "?"))
        return out[:3]

    people = [c for c in w.of_type("Character") if c.get("location") in inside and c.get("kind") != "deity"]
    pids = {c["@id"] for c in people}
    tensions = []
    for e in w.of_type("RelationshipEdge"):
        if e["from_id"] in pids and (e["to_id"] in pids or e["to_id"] == ctl) and (e["trust_score"] <= -20 or e["label"] in ("debt", "owes")):
            tensions.append({"between": w.name(e["from_id"]), "and": w.name(e["to_id"]), "label": e["label"],
                             "trust": e["trust_score"]})
    creatures = [m for m in w.of_type("Monster") if m.get("location") in inside]
    events = sorted((e for e in w.of_type("Event")
                     if (e.get("location") in inside or set(e.get("targets", [])) & (inside | pids))
                     and e.get("visibility") != "secret"),
                    key=lambda e: -(e.get("in_world_start") or 0))[:8]
    owners = pids | ({ctl} if ctl else set()) | {c.get("faction") for c in people if c.get("faction")}
    goals = [g for g in w.of_type("Goal") if g.get("status") == "active" and g["owner"] in owners]
    campaign = next((a["name"] for a in w.all.values() if a["@type"] == "WorldAtlas"), store.db)
    return {
        "source": "living-world-engine", "campaign": campaign, "branch": branch, "day": w.day,
        "sent_at": datetime.now(timezone.utc).isoformat(),
        "place": {"id": place_id, "name": place["name"], "kind": place.get("location_type"),
                  "description": place.get("description")},
        "holder": w.name(ctl) if ctl else None,
        "province": w.name(province) if province else None,
        "realm": w.name(realm) if realm else None,
        "map": {"title": map_title, "vtt_map_id": vtt_map_id} if vtt_map_id else None,
        "people": [{"id": c["@id"], "name": c["name"], "role": c.get("role"), "species": c.get("species"),
                    "char_class": c.get("char_class"), "faction": w.name(c["faction"]) if c.get("faction") else None,
                    "stats": _stats(c.get("srd_ref")), "wants": desires(c["@id"], ("WANTS", "NEEDS")),
                    "fears": desires(c["@id"], ("FEARS",))} for c in people],
        "creatures": [{"name": m["name"], "count": m.get("count"), "stats": _stats(m.get("srd_ref"))} for m in creatures],
        "tensions": tensions[:10],
        "rumors": [{"dialogue": r.get("dialogue"), "accuracy": r.get("accuracy"), "speaker": r.get("speaker")}
                   for r in w.of_type("Rumor") if r.get("location") in inside][:8],
        "events": [{"day": e.get("in_world_start"), "title": e["name"], "summary": e.get("summary")} for e in events],
        "goals": [{"name": g["name"], "owner": w.name(g["owner"]), "clock": f"{g['clock_progress']}/{g['clock_segments']}"}
                  for g in goals][:6],
        "attribution": srd.attribution() if any(c.get("srd_ref") for c in people) or creatures else None,
    }


def to_table(store: GraphStore, place_id: str, branch: str = "main", day: int | None = None,
             map_doc_id: str | None = None) -> dict[str, Any]:
    """Put a place on the table: its (chosen or current) linked map, plus the scene for the DM."""
    linked = maps_for(store, place_id, branch, day)
    chosen = next((m for m in linked if m["id"] == map_doc_id), None) if map_doc_id else (linked[0] if linked else None)
    if chosen:
        client.push_map(chosen["vtt_map_id"])
    scene = build_scene(store, place_id, branch, day, chosen["name"] if chosen else None,
                        chosen["vtt_map_id"] if chosen else None)
    from vtt_link.session import portraits
    try:
        faces = portraits() if scene["people"] else {}
    except client.VTTError:
        faces = {}
    for p in scene["people"]:
        p["portrait"] = faces.get(p["id"])
    client.send_scene(scene)
    return {"map": chosen, "scene": {"place": scene["place"]["name"], "people": len(scene["people"]),
                                     "rumors": len(scene["rumors"]), "creatures": len(scene["creatures"])}}

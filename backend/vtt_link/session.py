"""Phase 5: world-aware maps, NPC portrait tokens and the session loop.

* :func:`map_brief` describes a place as it is on a branch and in-world day (who holds it, what has happened
  there lately), and :func:`paint_map` asks the VTT to paint it. :func:`collect` links the finished map to the
  place from that day, so a burned warehouse's map replaces the old one only from the day it burned.
* :func:`paint_tokens` asks the VTT for a portrait token for everyone at a place who hasn't got one; the
  scene then carries each person's portrait.
* :func:`from_table` turns the VTT's session log into a summary for the next forecast.
"""
from __future__ import annotations

from typing import Any

from graph.store import GraphStore
from vtt_link import client
from vtt_link.scene import _world, link_map

TAG = "lwe:"  # tags on VTT assets: "lwe:<doc id>", plus "lwe-day:<n>" and "lwe-branch:<name>" on maps
RECENT_DAYS = 30


def _tag(tags: list[str], prefix: str) -> str | None:
    return next((t.removeprefix(prefix) for t in tags or [] if t.startswith(prefix)), None)


# --------------------------------------------------------------------------- world-aware maps
def map_brief(store: GraphStore, place_id: str, branch: str = "main", day: int | None = None) -> dict[str, Any]:
    """A map description for this place as it stands on ``branch`` at ``day``.

    Secret events stay out: the map goes on the players' table.
    """
    w = _world(store, branch, day)
    place = w.get(place_id)
    if not place or place["@type"] != "Location":
        raise ValueError(f"{place_id} isn't a place on {branch} at day {w.day}")
    inside = {place_id} | {l["@id"] for l in w.of_type("Location") if l.get("parent") == place_id}
    kind = place.get("location_type") or "place"
    parent = w.get(place.get("parent")) if place.get("parent") else None
    holder = w.controller_of(place_id) or (w.controller_of(place.get("parent")) if place.get("parent") else None)

    recent = sorted((e for e in w.of_type("Event")
                     if (e.get("location") in inside or set(e.get("targets", [])) & inside)
                     and e.get("visibility") != "secret"
                     and (e.get("in_world_start") or 0) >= w.day - RECENT_DAYS),
                    key=lambda e: -(e.get("in_world_start") or 0))[:3]
    creatures = [m for m in w.of_type("Monster") if m.get("location") in inside]

    parts = [f"Top-down battle map of {place['name']}, a {kind}" + (f" in {parent['name']}" if parent else "") + "."]
    if place.get("description"):
        parts.append(place["description"].strip().rstrip(".") + ".")
    if holder:
        parts.append(f"Held by {w.name(holder)}.")
    if recent:
        parts.append("As it is now, after: " + "; ".join(
            (e.get("summary") or e["name"]).strip().rstrip(".") for e in recent) + ".")
    if creatures:
        parts.append("Signs of " + ", ".join(m["name"] for m in creatures[:3]) + ".")
    description = " ".join(parts)
    if len(description) > 600:
        description = description[:597].rsplit(" ", 1)[0] + "..."
    return {
        "place": place_id, "branch": branch, "day": w.day,
        "title": f"{place['name']}, day {w.day}"[:80],
        "description": description,
        "tags": [f"{TAG}{place_id}", f"lwe-day:{w.day}", f"lwe-branch:{branch}"],
        "events": [e["name"] for e in recent],
    }


def paint_map(store: GraphStore, place_id: str, branch: str = "main", day: int | None = None, *,
              style: str = "", animate: bool = False, description: str | None = None) -> dict[str, Any]:
    """Ask the VTT to paint this place as it is on ``day``. ``description`` overrides the generated brief."""
    brief = map_brief(store, place_id, branch, day)
    if description and description.strip():
        brief["description"] = description.strip()[:600]
    job = client.generate_map(brief["description"], title=brief["title"], tags=brief["tags"], style=style,
                              animate=animate)
    return {"job": job["id"], "brief": brief}


def collect(store: GraphStore, job_id: str) -> dict[str, Any]:
    """Check a paint job; once done, link its map to the place from the day it was painted for (once)."""
    job = client.job(job_id)
    out = {"job": job_id, "status": job["status"], "progress": job.get("progress"),
           "message": job.get("message"), "error": job.get("error"), "linked": None}
    if job["status"] != "done" or not job.get("result_asset_id"):
        return out
    tags = (job.get("input") or {}).get("tags") or []
    place_id = _tag(tags, TAG)
    if not place_id:
        return out  # not one of ours
    day = int(_tag(tags, "lwe-day:") or 0)
    branch = _tag(tags, "lwe-branch:") or "main"
    w = _world(store, branch, None)
    existing = next((m for m in w.of_type("Map") if m.get("vtt_map_id") == job["result_asset_id"]
                     and m.get("place") == place_id), None)
    if existing:
        out["linked"] = {"map": existing["@id"], "place": place_id, "day": existing.get("in_world_start")}
        return out
    linked = link_map(store, place_id, job["result_asset_id"], branch=branch, day=day)
    out["linked"] = {**linked, "day": day}
    return out


# --------------------------------------------------------------------------- portrait tokens
def _portrait_brief(w, c: dict[str, Any]) -> str:
    who = " ".join(x for x in (c.get("species"), c.get("char_class")) if x) or "person"
    bits = [f"{c['name']}, a {who}"]
    if c.get("role"):
        bits.append(c["role"])
    if c.get("faction"):
        bits.append(f"of {w.name(c['faction'])}")
    text = ", ".join(bits)
    if c.get("personality"):
        text += f"; {c['personality']}"
    elif c.get("description"):
        text += f"; {c['description']}"
    return text[:600]


def portraits() -> dict[str, dict[str, str]]:
    """Character id -> its newest portrait token on the VTT: ``{"id": asset id, "url": thumbnail URL}``."""
    out: dict[str, dict[str, str]] = {}
    for t in sorted(client.tokens(), key=lambda t: t.get("created_at") or 0):
        cid = _tag(t.get("tags") or [], TAG)
        if cid and (t.get("thumb_url") or t.get("media_url")):
            out[cid] = {"id": t["id"], "url": t.get("thumb_url") or t["media_url"]}
    return out


def paint_tokens(store: GraphStore, place_id: str, branch: str = "main", day: int | None = None,
                 redo: bool = False) -> dict[str, Any]:
    """Queue a portrait token for each person at the place who hasn't got one yet."""
    w = _world(store, branch, day)
    if not w.get(place_id):
        raise ValueError(f"{place_id} doesn't exist on {branch} at day {w.day}")
    inside = {place_id} | {l["@id"] for l in w.of_type("Location") if l.get("parent") == place_id}
    people = [c for c in w.of_type("Character") if c.get("location") in inside and c.get("kind") != "deity"]
    have = {} if redo else portraits()
    jobs, skipped = [], []
    for c in people:
        if c["@id"] in have:
            skipped.append(c["name"])
            continue
        jobs.append({"name": c["name"], "job": client.make_token(c["name"], _portrait_brief(w, c), [f"{TAG}{c['@id']}"])["id"]})
    return {"queued": jobs, "already": skipped}


# --------------------------------------------------------------------------- session loop
def from_table() -> dict[str, Any]:
    """The VTT's session log as a forecast summary: DM notes in order, with the places visited."""
    entries = client.session_log()
    lines, days, places = [], [], []
    for e in entries:
        meta = e.get("meta") or {}
        if e["kind"] == "note":
            lines.append(f"- {e['text'].strip()}")
        elif e["kind"] == "scene":
            name = e["text"].removeprefix("Scene: ").split(" (")[0]
            if meta.get("day") is not None:
                days.append(int(meta["day"]))
            if name and name not in places:
                places.append(name)
            lines.append(f"- The party went to {name}.")
    notes = sum(1 for e in entries if e["kind"] == "note")
    summary = ""
    if lines:
        summary = "From the table's session log:\n" + "\n".join(lines)
    return {"summary": summary, "entries": len(entries), "notes": notes, "places": places,
            "start_day": min(days) if days else None, "end_day": max(days) if days else None}

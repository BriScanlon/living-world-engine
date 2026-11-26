"""The party's view of the world: what the player characters know, for the atlas's party fog of war.

The same epistemic rule the agents live by: the party may only see what it knows. "Party agents" are the party
faction (``is_party``) and its member characters.

* **Visited** places: a KNOWS mark from a party agent on a Location, the location of any event a party agent took
  part in (as actor), or where a member is now; plus every place that contains one (you can't be in the tavern
  without being in the town).
* **Heard-of** places: any other Location the party legally knows (``WorldView.known_ids``: BELIEVES marks, goals,
  relationships, the locations of events it knows about).
* **Provinces**: *seen* if a visited place lies in them (who holds them is shown), *heard of* if a heard-of place does
  or the party knows the province itself (name only), otherwise fog.
* **Events**: ones the party knows about or took part in. **Rumors**: the ones it knows, plus tavern talk where it
  is now. Rumor accuracy is never shown; the party can't tell.

Marks are bi-temporal EpistemicEdges like everything else (:func:`mark`), so the fog moves with the day slider
and differs per branch.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from graph import builders as b
from graph.store import GraphStore
from graph.world import WorldView

VISITED, HEARD = "visited", "heard"


@dataclass
class PartyView:
    party: dict[str, Any] | None
    agents: set[str] = field(default_factory=set)
    known: set[str] = field(default_factory=set)
    places: dict[str, str] = field(default_factory=dict)  # Location id -> visited | heard
    provinces: dict[str, str] = field(default_factory=dict)  # Province id -> visited | heard
    events: set[str] = field(default_factory=set)
    rumors: set[str] = field(default_factory=set)
    factions: set[str] = field(default_factory=set)
    where: str | None = None  # the party's current location, if the world says

    def place_state(self, doc_id: str) -> str | None:
        return self.places.get(doc_id) or self.provinces.get(doc_id)


def _ancestors(w: WorldView, loc_id: str) -> list[str]:
    out, seen = [], set()
    cur = w.get(loc_id)
    while cur and cur.get("parent") and cur["parent"] not in seen:
        seen.add(cur["parent"])
        out.append(cur["parent"])
        cur = w.get(cur["parent"])
    return out


def _province_of(w: WorldView, loc_id: str) -> str | None:
    for i in [loc_id, *_ancestors(w, loc_id)]:
        d = w.get(i) or {}
        if d.get("province"):
            return d["province"]
    return None


def view(w: WorldView) -> PartyView:
    party = w.party
    if not party:
        return PartyView(None)
    agents = {party["@id"]} | {c["@id"] for c in w.members_of(party["@id"])}
    known: set[str] = set()
    for a in agents:
        known |= w.known_ids(a)

    # where the party has been, with the day it was there (latest wins for "where it is now")
    been: dict[str, int] = {}
    for e in w.of_type("EpistemicEdge"):
        if e["agent_id"] in agents and e["kind"] == "KNOWS" and (w.get(e["fact_or_event_id"]) or {}).get("@type") == "Location":
            been[e["fact_or_event_id"]] = max(been.get(e["fact_or_event_id"], -1), e.get("in_world_start") or 0)
    took_part = set()
    for e in w.of_type("Event"):
        if set(e.get("actors", [])) & agents:
            took_part.add(e["@id"])
            if e.get("location"):
                been[e["location"]] = max(been.get(e["location"], -1), e.get("in_world_start") or 0)
    for c in w.members_of(party["@id"]):
        if c.get("location"):
            been[c["location"]] = max(been.get(c["location"], -1), c.get("in_world_start") or 0)
    where = max(been, key=lambda k: (been[k], k)) if been else None

    places: dict[str, str] = {}
    for loc in been:
        for i in [loc, *_ancestors(w, loc)]:
            places[i] = VISITED
    events = {i for i in known if (w.get(i) or {}).get("@type") == "Event"} | {
        e["@id"] for e in w.of_type("Event") if set(e.get("targets", [])) & agents} | took_part
    for i in known | {w.get(e).get("location") for e in events if w.get(e) and w.get(e).get("location")}:
        if (w.get(i) or {}).get("@type") == "Location":
            for j in [i, *_ancestors(w, i)]:
                places.setdefault(j, HEARD)

    provinces: dict[str, str] = {}
    for loc, state in places.items():
        p = _province_of(w, loc)
        if p and (provinces.get(p) != VISITED):
            provinces[p] = VISITED if state == VISITED else provinces.get(p, HEARD)
    for i in known:
        if (w.get(i) or {}).get("@type") == "Province":
            provinces.setdefault(i, HEARD)

    here = {where, *(_ancestors(w, where) if where else [])} - {None}
    here |= {l["@id"] for l in w.of_type("Location") if l.get("parent") in here}
    rumors = {i for i in known if (w.get(i) or {}).get("@type") == "Rumor"} | {
        r["@id"] for r in w.of_type("Rumor") if r.get("location") in here}

    factions = {party["@id"]} | {i for i in known if (w.get(i) or {}).get("@type") == "Faction"}
    for p, state in provinces.items():
        if state == VISITED and w.controller_of(p):
            factions.add(w.controller_of(p))
    return PartyView(party, agents, known, places, provinces, events, rumors, factions, where)


# --------------------------------------------------------------------------- DM marks
def mark(store: GraphStore, place_id: str, state: str, *, branch: str = "main", day: int | None = None) -> dict[str, Any]:
    """Record what the party knows of a place from ``day``: ``visited`` (KNOWS), ``heard`` (BELIEVES) or ``unknown``.

    Earlier marks on the place end that day; nothing is deleted, so earlier days keep their fog.
    """
    if state not in (VISITED, HEARD, "unknown"):
        raise ValueError("state must be visited, heard or unknown")
    docs = store.all_documents(branch)
    w = WorldView(docs, day if day is not None else WorldView(docs).max_day())
    place = w.get(place_id)
    if not place or place["@type"] not in ("Location", "Province"):
        raise ValueError(f"{place_id} isn't a place on {branch} at day {w.day}")
    party = w.party
    if not party:
        raise ValueError("This world has no party (a Faction with is_party)")
    current = [e for e in w.of_type("EpistemicEdge") if e["agent_id"] == party["@id"] and e["fact_or_event_id"] == place_id]
    want = {VISITED: "KNOWS", HEARD: "BELIEVES"}.get(state)
    if [e["kind"] for e in current] == ([want] if want else []):
        return {"place": place_id, "state": state, "day": w.day, "changed": False}
    out = [b.end(e, w.day) for e in current]
    if state != "unknown":
        out.append(b.knows(party["@id"], place_id, kind="KNOWS" if state == VISITED else "BELIEVES",
                           start=w.day, session=f"dm/{branch}",
                           note="The DM marked this on the atlas" if state == HEARD else "The party has been here"))
    if out:
        verb = {"visited": "has been to", "heard": "has heard of", "unknown": "forgets"}[state]
        store.upsert(out, branch, f"[party] {party['name']} {verb} {place['name']} from day {w.day}")
    return {"place": place_id, "state": state, "day": w.day, "changed": True}


def visit(store: GraphStore, place_id: str, *, branch: str = "main", day: int | None = None) -> bool:
    """Mark a place visited unless the party already knows it first-hand (used when a scene goes to the table)."""
    docs = store.all_documents(branch)
    w = WorldView(docs, day if day is not None else WorldView(docs).max_day())
    if not w.party or view(w).places.get(place_id) == VISITED:
        return False
    mark(store, place_id, VISITED, branch=branch, day=w.day)
    return True

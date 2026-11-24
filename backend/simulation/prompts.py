"""Prompt builders for the real-LLM path (OpenAI-compatible)."""
from __future__ import annotations

import json
from typing import Any

from graph.world import WorldView

SESSION_DELTA_SYSTEM = """You are the record-keeper for a tabletop RPG campaign.
Turn the DM's session summary into a list of discrete PARTY ACTIONS.
Only use node ids from the provided catalogue; never invent ids. If an entity is not in the catalogue, omit it.
action_type: steal | kill | help | harm | bribe | discover | travel | other.
trust_delta: how the affected NPCs/factions now feel about the party (-100..100).
visibility: "secret" if the party acted unseen, else "public"."""

AGENT_SYSTEM = """You are role-playing ONE non-player agent in a living fantasy world, deciding what it does
off-screen during the next few in-world days.
STRICT EPISTEMIC RULE: you may only reference ids that appear in your context (self, desires, goals,
controls, relationships, known_world). You do not know anything else exists.
Choose the single most in-character action that serves your strongest desires/goals or reacts to recent
events you know about. Be concrete. resources_spent is drawn from your faction's treasury (0-100).
Valid actions: seize, sabotage, negotiate, threaten, bribe, ally, investigate, fortify, steal, attack,
recruit, spread_rumor, hide, flee, observe, trade."""

ARBITER_SYSTEM = """You are the impartial World Arbiter of a living fantasy world.
You receive every agent's simultaneous intent for this round plus the relevant world state.
Resolve clashes (two agents targeting the same thing, an intent against a controlled asset) using faction
resources, influence, resources_spent, and relationships. Not every plan succeeds. Produce concrete
WorldEvents with their consequences: relationship_shifts (trust deltas), control_changes, killed_ids
(rarely), goal_progress (goal id -> segments), resource_changes (faction id -> delta), witnesses (agent ids
who learn of it), new_goals / new_desires (motivation shifts), knowledge_gained.
Use ONLY ids that appear in the input. Use the round's day for every event. Keep summaries to 1-2 sentences.
visibility: public (everyone nearby sees), secret (only participants), leaked (secret but got out)."""

RUMOR_SYSTEM = """You write tavern rumors for a tabletop RPG game master.
For each public or leaked event, write 1 rumor heard at the given venue. Mix accuracy: roughly half
"True", some "Exaggerated" (embellished), some "False" (wrong culprit or wrong outcome).
`dialogue` is a ready-to-read line in an NPC's voice (in quotes); `speaker` is who says it."""


def catalogue(world: WorldView) -> list[dict[str, Any]]:
    return [{"id": d["@id"], "type": d["@type"], "name": d["name"], "aliases": d.get("aliases", [])}
            for d in world.nodes() if d["@type"] not in ("Rumor", "Event")]


def session_delta_user(summary: str, world: WorldView, start_day: int, end_day: int) -> str:
    return json.dumps({
        "session_days": [start_day, end_day],
        "party_id": (world.party or {}).get("@id"),
        "catalogue": catalogue(world),
        "session_summary": summary,
    }, indent=1)


def agent_user(ctx: dict[str, Any], day: int, round_days: tuple[int, int]) -> str:
    return json.dumps({"today": day, "round_covers_days": list(round_days), "you": ctx}, indent=1, default=str)


def arbiter_user(intents: list[dict], world: WorldView, day: int) -> str:
    involved: set[str] = set()
    for it in intents:
        involved |= {it["agent_id"], it.get("target_id")} - {None}
    for i in list(involved):
        ctl = world.controller_of(i)
        if ctl:
            involved.add(ctl)
    state = {
        "entities": [{k: v for k, v in world.docs[i].items() if k in
                      ("@id", "@type", "name", "resources", "influence", "faction", "location", "role")}
                     for i in sorted(involved) if i in world.docs],
        "controls": [{"from": e["from_id"], "kind": e["kind"], "target": e["target_id"]}
                     for e in world.of_type("ControlEdge") if e["target_id"] in involved or e["from_id"] in involved],
        "relationships": [{"from": e["from_id"], "to": e["to_id"], "trust": e["trust_score"], "label": e["label"]}
                          for e in world.of_type("RelationshipEdge")
                          if e["from_id"] in involved and e["to_id"] in involved],
        "active_goals": [{"id": g["@id"], "owner": g["owner"], "name": g["name"], "target": g.get("target"),
                          "clock": f"{g['clock_progress']}/{g['clock_segments']}"}
                         for g in world.of_type("Goal") if g.get("status") == "active" and g["owner"] in involved],
    }
    return json.dumps({"day": day, "intents": intents, "world": state}, indent=1, default=str)


def rumor_user(events: list[dict], world: WorldView) -> str:
    venues = [{"id": l["@id"], "name": l["name"], "type": l.get("location_type")}
              for l in world.of_type("Location") if l.get("location_type") in ("tavern", "town", "district")]
    locals_ = [{"name": c["name"], "location": c.get("location")} for c in world.of_type("Character")]
    evs = [{"id": e["@id"], "title": e["name"], "summary": e.get("summary"), "visibility": e.get("visibility"),
            "location": e.get("location"), "actors": [world.name(a) for a in e.get("actors", [])]} for e in events]
    return json.dumps({"events": evs, "venues": venues, "locals": locals_}, indent=1)

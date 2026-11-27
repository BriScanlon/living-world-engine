"""LLM-written prose for a new world: the wizard's last pass rewrites its one-line template descriptions.

The five build steps make every fact deterministically (names, places, who rules what, what happened when) and give
each thing a short template description. This pass hands the most table-relevant of them to the model in small
batches and keeps what comes back only if it is usable; anything that fails keeps its template, so a world always
builds. It runs only on a real model (``MOCK_LLM=false``) and when the DM leaves *Have the model write descriptions*
ticked; the mock twin (``llm.mock.wizard_prose``) changes nothing.

Descriptions and event summaries are part of what an agent sees about what it knows (``WorldView.agent_context``), so
the prose must only say what is **commonly known**: the model is shown public fields only (type, name, role,
faction, place, tags, the template text), never desires, relationships or goals, and is told not to invent secrets.
"""
from __future__ import annotations

import json
import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable

from pydantic import BaseModel, Field

log = logging.getLogger(__name__)

BATCH = 12          # entries per model call
MAX_ENTRIES = 96    # per world: the local area first, then realms, faiths, rulers and history
MIN_CHARS, MAX_CHARS = 20, 700

SYSTEM = """You write short, evocative descriptions for a tabletop RPG campaign world that a game master is about to
run. You get the world's pitch and a list of entries, each with its type, name, a few public facts and a placeholder
description. Write a new description for every entry.

Rules:
- 1 to 3 sentences, at most about 60 words. Concrete, sensory, playable details a GM can read aloud or use.
- Keep every name exactly as given. Do not rename anything, and do not invent new named people, places, factions or
  items: refer only to names that appear in the entries or the world brief.
- Only what is commonly known or plainly visible. No secret plans, hidden loyalties, crimes, debts or anything a
  character would keep to themselves.
- Stay consistent with the facts given (roles, who rules what, where things are, when events happened).
- For an Event, write what happened (its summary), in the past tense. Don't say how long ago it was ("six
  centuries ago"): the date is kept separately and the campaign calendar moves on.
- Match the world's pitch, focus and tone, and weave the pitch's imagery in where it fits naturally (not into
  every entry). Never name genres or labels ("high fantasy", "tropical"): show them instead.
- No game statistics, no meta commentary."""


class ProseEntry(BaseModel):
    id: str = Field(description="The entry's id, exactly as given")
    text: str = Field(description="The new description (or, for an Event, its summary)")


class ProseBatch(BaseModel):
    entries: list[ProseEntry]


# ---------------------------------------------------------------------------------------------------- what to write
def _rank(d: dict[str, Any]) -> int | None:
    """Priority of a document for prose (lower first), or None to leave it as it is."""
    t, s = d.get("@type"), d.get("source_session") or ""
    local = s == "setup/local" or "campaign-start" in d.get("tags", [])
    if t == "Location" and local:
        return 0
    if t in ("Faction", "Character", "Item") and local:
        return 1
    if t == "Faction" and not d.get("is_party"):
        return 2                     # realms and faiths
    if t == "Character" and (d.get("is_major") or d.get("kind") == "deity" or d.get("role") == "legendary hero"):
        return 3                     # rulers, faith leaders, gods, the hero
    if t == "Event" and s == "setup/history":
        return 4
    if t == "Location" and d.get("location_type") == "ruin":
        return 4
    return None


def candidates(docs: list[dict[str, Any]], limit: int = MAX_ENTRIES) -> list[dict[str, Any]]:
    """The documents worth writing prose for, most table-relevant first."""
    ranked = [(r, d["@id"], d) for d in docs if (r := _rank(d)) is not None]
    return [d for _, _, d in sorted(ranked, key=lambda x: (x[0], x[1]))][:limit]


def field_of(doc: dict[str, Any]) -> str:
    return "summary" if doc.get("@type") == "Event" else "description"


def public_facts(doc: dict[str, Any], by_id: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Only what anyone could know about a document: never desires, relationships or goals."""
    name = lambda i: (by_id.get(i) or {}).get("name")  # noqa: E731
    facts: dict[str, Any] = {"id": doc["@id"], "type": doc["@type"], "name": doc["name"]}
    for k in ("role", "location_type", "item_type", "kind", "rarity"):
        if doc.get(k):
            facts[k] = doc[k]
    for k in ("faction", "location", "parent", "province", "headquarters"):
        if doc.get(k) and name(doc[k]):
            facts[k] = name(doc[k])
    tags = [t for t in doc.get("tags", []) if t not in ("campaign-start",)]
    if tags:
        facts["tags"] = tags[:6]
    if doc["@type"] == "Event":
        day = doc.get("in_world_start") or 0
        if day < 0:
            facts["years_before_the_campaign"] = max(1, round(-day / 365))  # for scale, not for the text
        for k in ("actors", "targets"):
            names = [n for n in (name(i) for i in doc.get(k) or []) if n]
            if names:
                facts[k] = names
    facts["placeholder"] = doc.get(field_of(doc)) or ""
    return facts


def world_brief(answers: dict[str, Any], world_name: str) -> dict[str, Any]:
    from wizard import spec as S
    w, k, h = answers.get("world", {}), answers.get("kingdoms", {}), answers.get("history", {})
    cat = S.CATACLYSMS.get(h.get("cataclysm") or "none")
    brief = {"world": world_name, "pitch": (w.get("lore") or "").strip() or None,
             "focus": S.FOCUSES.get(answers.get("focus") or "", {}).get("label"),
             "climate": (w.get("climate") or "").replace("-", " ") or None,
             "culture_style": (k.get("culture_style") or "").replace("-", " ") or None,
             "peoples": S.SPECIES_MIX.get(k.get("species_mix") or ""),
             "catastrophe": cat[0] if cat else None, "years_of_history": h.get("years_back")}
    return {k: v for k, v in brief.items() if v}


# ---------------------------------------------------------------------------------------------------- the pass
def usable(text: str) -> str | None:
    text = " ".join((text or "").split()).strip().strip('"').strip()
    return text if MIN_CHARS <= len(text) <= MAX_CHARS else None


def write_prose(docs: list[dict[str, Any]], answers: dict[str, Any], world_name: str, *,
                resolve: Callable[[str, str], ProseBatch], concurrency: int = 1,
                progress: Callable[[int, int], None] | None = None) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Rewrite the candidates' descriptions with ``resolve(system, user) -> ProseBatch``.

    Returns the changed documents (copies) and counts. A batch that raises, or entries with an unknown id or
    unusable text, keep their template text."""
    by_id = {d["@id"]: d for d in docs}
    todo = candidates(docs)
    brief = world_brief(answers, world_name)
    batches = [todo[i:i + BATCH] for i in range(0, len(todo), BATCH)]
    stats = {"candidates": len(todo), "batches": len(batches), "written": 0, "kept": 0, "failed_batches": 0}
    done = [0]

    def one(batch: list[dict[str, Any]]) -> tuple[dict[str, str], bool]:
        user = json.dumps({"world": brief, "entries": [public_facts(d, by_id) for d in batch]},
                          separators=(",", ":"), ensure_ascii=False)
        try:
            out = resolve(SYSTEM, user)
        except Exception:  # noqa: BLE001 - a failed batch keeps its templates; the world still builds
            log.warning("Prose batch of %s failed; keeping the template descriptions", len(batch), exc_info=True)
            return {}, True
        finally:
            done[0] += 1
            if progress:
                progress(done[0], len(batches))
        ids = {d["@id"] for d in batch}
        return {e.id: t for e in out.entries if e.id in ids and (t := usable(e.text))}, False

    texts: dict[str, str] = {}
    with ThreadPoolExecutor(max_workers=max(1, min(concurrency, len(batches) or 1))) as pool:
        for got, failed in pool.map(one, batches):
            texts.update(got)
            stats["failed_batches"] += failed
    changed = []
    for d in todo:
        new = texts.get(d["@id"])
        if new and new != d.get(field_of(d)):
            changed.append({**d, field_of(d): new})
    stats["written"] = len(changed)
    stats["kept"] = len(todo) - len(changed)
    return changed, stats

"""TerminusDB schema for the Living World Engine.

Two independent time axes make the graph *bi-temporal*:

* **Transaction time** — every write is a TerminusDB commit on a branch, so the
  full history of *what we recorded and when* is preserved, branchable, diffable.
* **Valid (in-world) time** — every node and edge carries ``in_world_start`` /
  ``in_world_end`` (campaign day integers). Facts are never deleted when they stop
  being true; their ``in_world_end`` is set instead, so you can ask
  "what was true on day 42?" on any branch, at any commit.

Node types: Character, Faction, Location, Item, Monster, Event, Rumor, Goal.
Edge types (first-class documents so they can carry temporal properties):
DesireEdge (WANTS/NEEDS/FEARS), ControlEdge (CONTROLS/OWNS),
RelationshipEdge (trust -100..+100), EpistemicEdge (KNOWS/BELIEVES).

Epistemic rule: an agent may only reason over nodes linked to it through
KNOWS/BELIEVES edges (plus itself and its own desires/goals/holdings/relationships).
"""
from __future__ import annotations

import hashlib
import re
import unicodedata
from typing import Any

# ---------------------------------------------------------------------------
# Vocabulary
# ---------------------------------------------------------------------------
NODE_TYPES = ["Character", "Faction", "Location", "Province", "Item", "Monster", "Event", "Rumor", "Goal"]
AGENT_TYPES = ["Character", "Faction"]
EDGE_TYPES = ["DesireEdge", "ControlEdge", "RelationshipEdge", "EpistemicEdge"]
META_TYPES = ["ForecastRun", "Alias", "WorldAtlas"]

DESIRE_KINDS = ["WANTS", "NEEDS", "FEARS"]
CONTROL_KINDS = ["CONTROLS", "OWNS"]
EPISTEMIC_KINDS = ["KNOWS", "BELIEVES"]
ACCURACY = ["True", "Exaggerated", "False"]
VISIBILITY = ["public", "secret", "leaked"]


def _opt(cls: str) -> dict:
    return {"@type": "Optional", "@class": cls}


def _set(cls: str) -> dict:
    return {"@type": "Set", "@class": cls}


_LEX_UID = {"@type": "Lexical", "@fields": ["uid"]}

TEMPORAL_FIELDS = {
    "in_world_start": _opt("xsd:integer"),
    "in_world_end": _opt("xsd:integer"),
    "source_session": _opt("xsd:string"),
    # Provenance links that make cherry-picking an event (and its consequences) possible
    "source_event": _opt("Event"),
    "ended_by_event": _opt("Event"),
}

SCHEMA: list[dict[str, Any]] = [
    {
        "@type": "@context",
        "@base": "terminusdb:///lwe/data/",
        "@schema": "terminusdb:///lwe/schema#",
        "@documentation": {
            "@title": "Living World Engine",
            "@description": "Bi-temporal, motivational and epistemic world graph for tabletop RPGs.",
        },
    },
    {"@type": "Enum", "@id": "DesireKind", "@value": DESIRE_KINDS},
    {"@type": "Enum", "@id": "ControlKind", "@value": CONTROL_KINDS},
    {"@type": "Enum", "@id": "EpistemicKind", "@value": EPISTEMIC_KINDS},
    {"@type": "Enum", "@id": "Accuracy", "@value": ACCURACY},
    {"@type": "Enum", "@id": "Visibility", "@value": VISIBILITY},
    # ---------------------------------------------------------------- nodes
    {
        "@type": "Class",
        "@id": "Entity",
        "@abstract": [],
        "uid": "xsd:string",
        "name": "xsd:string",
        "aliases": _set("xsd:string"),
        "description": _opt("xsd:string"),
        "tags": _set("xsd:string"),
        **TEMPORAL_FIELDS,
    },
    {
        "@type": "Class",
        "@id": "Character",
        "@inherits": ["Entity"],
        "@key": _LEX_UID,
        "role": _opt("xsd:string"),
        "kind": _opt("xsd:string"),  # npc | pc
        "is_major": _opt("xsd:boolean"),
        "location": _opt("Location"),
        "faction": _opt("Faction"),
        "personality": _opt("xsd:string"),
        # SRD 5.2.1-backed details (see backend/srd)
        "species": _opt("xsd:string"),
        "char_class": _opt("xsd:string"),
        "background": _opt("xsd:string"),
        "level": _opt("xsd:integer"),
        "srd_ref": _opt("xsd:string"),
    },
    {
        "@type": "Class",
        "@id": "Faction",
        "@inherits": ["Entity"],
        "@key": _LEX_UID,
        "resources": _opt("xsd:integer"),
        "influence": _opt("xsd:integer"),
        "is_party": _opt("xsd:boolean"),
        "is_major": _opt("xsd:boolean"),
        "headquarters": _opt("Location"),
        "color": _opt("xsd:string"),  # map colour for its territory
    },
    {
        "@type": "Class",
        "@id": "Location",
        "@inherits": ["Entity"],
        "@key": _LEX_UID,
        "location_type": _opt("xsd:string"),  # capital | city | town | district | tavern | wilderness | building
        "parent": _opt("Location"),
        # Geography (atlas): WGS84-style lon/lat on the campaign planet
        "lon": _opt("xsd:double"),
        "lat": _opt("xsd:double"),
        "province": _opt("Province"),
        "population": _opt("xsd:integer"),
    },
    {
        "@type": "Class",
        "@id": "Province",
        "@inherits": ["Entity"],
        "@key": _LEX_UID,
        "@documentation": {"@comment": "A territory polygon. The geometry lives in the atlas tiles (feature id = uid); "
                                       "who holds it is a bi-temporal ControlEdge, so borders move with time and branch."},
        "lon": _opt("xsd:double"),
        "lat": _opt("xsd:double"),
        "area_cells": _opt("xsd:integer"),
        "population": _opt("xsd:integer"),
        "atlas": _opt("xsd:string"),
    },
    {
        "@type": "Class",
        "@id": "Item",
        "@inherits": ["Entity"],
        "@key": _LEX_UID,
        "item_type": _opt("xsd:string"),
        "value": _opt("xsd:integer"),
        "location": _opt("Location"),
        "rarity": _opt("xsd:string"),
        "srd_ref": _opt("xsd:string"),
    },
    {
        "@type": "Class",
        "@id": "Monster",
        "@inherits": ["Entity"],
        "@key": _LEX_UID,
        "threat": _opt("xsd:integer"),
        "location": _opt("Location"),
        "challenge": _opt("xsd:string"),  # SRD challenge rating, e.g. "1/4", "17"
        "srd_ref": _opt("xsd:string"),
        "count": _opt("xsd:integer"),
    },
    {
        "@type": "Class",
        "@id": "Goal",
        "@inherits": ["Entity"],
        "@key": _LEX_UID,
        "owner": "Entity",
        "target": _opt("Entity"),
        "priority": "xsd:double",
        # Progress clock (Blades-in-the-Dark style): ticks every N in-world days
        "clock_segments": "xsd:integer",
        "clock_progress": "xsd:integer",
        "ticks_every_days": "xsd:integer",
        "status": "xsd:string",  # active | achieved | failed | abandoned
    },
    {
        "@type": "Class",
        "@id": "Event",
        "@inherits": ["Entity"],
        "@key": _LEX_UID,
        "summary": "xsd:string",
        "event_type": "xsd:string",  # party_action | faction_move | npc_action | goal_progress | ...
        "actors": _set("Entity"),
        "targets": _set("Entity"),
        "location": _opt("Location"),
        "visibility": "Visibility",
        "caused_by": _opt("Event"),
        "consequences": _opt("xsd:string"),
    },
    {
        "@type": "Class",
        "@id": "Rumor",
        "@inherits": ["Entity"],
        "@key": _LEX_UID,
        "text": "xsd:string",
        "dialogue": "xsd:string",
        "speaker": _opt("xsd:string"),
        "accuracy": "Accuracy",
        "about_event": _opt("Event"),
        "location": "Location",
    },
    # ---------------------------------------------------------------- edges
    {
        "@type": "Class",
        "@id": "TemporalEdge",
        "@abstract": [],
        "uid": "xsd:string",
        "note": _opt("xsd:string"),
        **TEMPORAL_FIELDS,
    },
    {
        "@type": "Class",
        "@id": "DesireEdge",
        "@inherits": ["TemporalEdge"],
        "@key": _LEX_UID,
        "from_id": "Entity",
        "kind": "DesireKind",
        "target_id": _opt("Entity"),
        "target_text": _opt("xsd:string"),
        "priority": "xsd:double",
    },
    {
        "@type": "Class",
        "@id": "ControlEdge",
        "@inherits": ["TemporalEdge"],
        "@key": _LEX_UID,
        "from_id": "Entity",
        "kind": "ControlKind",
        "target_id": "Entity",
    },
    {
        "@type": "Class",
        "@id": "RelationshipEdge",
        "@inherits": ["TemporalEdge"],
        "@key": _LEX_UID,
        "from_id": "Entity",
        "to_id": "Entity",
        "trust_score": "xsd:integer",
        "label": "xsd:string",
    },
    {
        "@type": "Class",
        "@id": "EpistemicEdge",
        "@inherits": ["TemporalEdge"],
        "@key": _LEX_UID,
        "agent_id": "Entity",
        "kind": "EpistemicKind",
        "fact_or_event_id": "Entity",
        "confidence": "xsd:double",
    },
    # ---------------------------------------------------------------- meta
    {
        "@type": "Class",
        "@id": "Alias",
        "@key": _LEX_UID,
        "@documentation": {"@comment": "Entity-resolution alias table: surface form -> canonical node."},
        "uid": "xsd:string",
        "surface": "xsd:string",
        "canonical": "Entity",
    },
    {
        "@type": "Class",
        "@id": "WorldAtlas",
        "@key": _LEX_UID,
        "@documentation": {"@comment": "A generated or imported planet whose static layers live on disk (GeoJSON / PMTiles)."},
        "uid": "xsd:string",
        "name": "xsd:string",
        "source": "xsd:string",  # e.g. azgaar-full-json, sample
        "seed": _opt("xsd:string"),
        "bbox": "xsd:string",  # JSON [west, south, east, north]
        "centre": _opt("xsd:string"),  # JSON [lon, lat] to open the map on
        "imported_at": "xsd:string",
        "layers": "xsd:string",  # JSON list of layer names available on disk
        "has_tiles": "xsd:boolean",
    },
    {
        "@type": "Class",
        "@id": "ForecastRun",
        "@key": _LEX_UID,
        "uid": "xsd:string",
        "branch": "xsd:string",
        "base_branch": "xsd:string",
        "base_commit": _opt("xsd:string"),
        "session_label": "xsd:string",
        "session_number": "xsd:integer",
        "start_day": "xsd:integer",
        "end_day": "xsd:integer",
        "summary": "xsd:string",
        "created_at": "xsd:string",
        "status": "xsd:string",
        "mock_llm": "xsd:boolean",
        "report_json": _opt("xsd:string"),
    },
]

# Fields TerminusDB returns as strings that we want as ints (xsd:integer is arbitrary precision).
INT_FIELDS = {
    "in_world_start", "in_world_end", "resources", "influence", "value", "threat",
    "clock_segments", "clock_progress", "ticks_every_days", "trust_score",
    "session_number", "start_day", "end_day", "population", "area_cells", "level", "count",
}
# Set-valued fields that TerminusDB omits when empty.
SET_FIELDS = {"aliases", "tags", "actors", "targets"}
# Fields holding document links (used for dependency closure / integrity).
LINK_FIELDS = {
    "location", "faction", "headquarters", "parent", "owner", "target", "caused_by",
    "about_event", "from_id", "to_id", "target_id", "agent_id", "fact_or_event_id",
    "source_event", "ended_by_event", "canonical", "actors", "targets", "province",
}
# Provenance links that can be dropped when cherry-picking if their target is absent.
SOFT_LINK_FIELDS = {"caused_by", "source_event", "ended_by_event", "about_event"}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def slugify(text: str) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    text = re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-").lower()
    return text or "unnamed"


def doc_id(doc_type: str, uid: str) -> str:
    return f"{doc_type}/{uid}"


def type_of(doc_id_: str) -> str:
    return doc_id_.split("/", 1)[0]


def stable_uid(prefix: str, *parts: Any, length: int = 10) -> str:
    """Deterministic short uid — keeps mock runs reproducible and diffs readable."""
    h = hashlib.sha1("|".join(str(p) for p in parts).encode()).hexdigest()[:length]
    return f"{prefix}-{h}"


def normalize(doc: dict[str, Any]) -> dict[str, Any]:
    """Coerce TerminusDB JSON into predictable Python types."""
    out = dict(doc)
    for k in INT_FIELDS:
        if k in out and out[k] is not None and not isinstance(out[k], int):
            try:
                out[k] = int(out[k])
            except (TypeError, ValueError):
                pass
    for k in SET_FIELDS:
        if k in TYPE_FIELDS.get(out.get("@type", ""), set()):
            out.setdefault(k, [])
    return out


def _collect_type_fields() -> dict[str, set[str]]:
    classes = {c["@id"]: c for c in SCHEMA if c.get("@type") == "Class"}

    def fields(cid: str) -> set[str]:
        c = classes[cid]
        own = {k for k in c if not k.startswith("@")}
        for parent in c.get("@inherits", []):
            own |= fields(parent)
        return own

    return {cid: fields(cid) for cid in classes}


TYPE_FIELDS: dict[str, set[str]] = _collect_type_fields()


def clean_for_write(doc: dict[str, Any]) -> dict[str, Any]:
    """Strip None values / unknown fields so the document validates against the schema."""
    allowed = TYPE_FIELDS.get(doc["@type"], set())
    out: dict[str, Any] = {"@type": doc["@type"]}
    for k, v in doc.items():
        if k.startswith("@") or k not in allowed or v is None:
            continue
        if isinstance(v, (list, set, tuple)):
            v = sorted(set(v)) if k in SET_FIELDS else list(v)
        out[k] = v
    return out

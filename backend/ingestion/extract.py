"""LLM (or mock heuristic) extraction of nodes + motivational / epistemic edges from prose."""
from __future__ import annotations

import json
import re
from typing import Literal

from pydantic import BaseModel, Field

from config import get_settings
from ingestion.resolution import EntityResolver

NodeType = Literal["Character", "Faction", "Location", "Item", "Monster", "Goal", "Event"]
EdgeKind = Literal["WANTS", "NEEDS", "FEARS", "CONTROLS", "OWNS", "RELATIONSHIP", "KNOWS", "BELIEVES", "MEMBER_OF", "LOCATED_IN"]


class ExtractedNode(BaseModel):
    name: str
    type: NodeType
    aliases: list[str] = Field(default_factory=list)
    description: str | None = None
    role: str | None = None
    location_type: str | None = None


class ExtractedEdge(BaseModel):
    kind: EdgeKind
    source: str = Field(description="Name of the source node (agent)")
    target: str | None = Field(default=None, description="Name of the target node")
    target_text: str | None = Field(default=None, description="Abstract target if no node, e.g. 'wealth'")
    priority: float | None = Field(default=None, ge=0, le=1)
    trust: int | None = Field(default=None, ge=-100, le=100)
    label: str | None = None
    confidence: float | None = Field(default=None, ge=0, le=1)


class ExtractionResult(BaseModel):
    nodes: list[ExtractedNode] = Field(default_factory=list)
    edges: list[ExtractedEdge] = Field(default_factory=list)


SYSTEM = """You extract a knowledge graph from tabletop RPG campaign lore.
Return nodes (Character, Faction, Location, Item, Monster, Goal, Event) and edges:
- WANTS / NEEDS / FEARS (source agent -> target node or target_text) with priority 0..1
- CONTROLS / OWNS (source -> territory/resource/item)
- RELATIONSHIP (source -> target) with trust -100..100 and a short label (ally, rival, debt, family...)
- KNOWS / BELIEVES (agent -> node it is aware of) with confidence 0..1
- MEMBER_OF (character -> faction), LOCATED_IN (character/item/location -> location)
Infer plausible WANTS/NEEDS from motives described in the text. Use the exact names from KNOWN ENTITIES
when the text refers to them (including by title or nickname) and list new nicknames as aliases."""


def llm_extract(chunk: str, known: list[str]) -> ExtractionResult:
    from llm.client import get_llm

    user = json.dumps({"KNOWN ENTITIES": known[:300], "TEXT": chunk})
    return get_llm().structured(SYSTEM, user, ExtractionResult, temperature=0.1)


# --------------------------------------------------------------------------- mock heuristic
FACTION_WORDS = r"(?:Guild|House|Order|Company|Brotherhood|Sisterhood|Cult|Clan|Syndicate|Circle|Legion|Watch|Church|Council)"
PLACE_WORDS = r"(?:Tavern|Inn|Keep|Manor|Docks|Village|City|Town|Tower|Temple|Shrine|Marsh|Forest|Castle|Harbour|Harbor|Market|Quarter|Hall|Mine|Caves?)"
ITEM_WORDS = r"(?:Sword|Blade|Ledger|Crown|Ring|Amulet|Seal|Tome|Book|Staff|Key|Map|Orb|Chalice|Dagger|Shield|Bell)"
MONSTER_WORDS = r"(?:Dragon|Hag|Troll|Wyrm|Beast|Ghoul|Lich|Wraith|Ogre|Giant)"
ADV = r"(?:,?\s+(?:now|still|secretly|openly|deeply|also|quietly|desperately))?"
PROPER = r"((?:[A-Z][\w'’-]+)(?:\s+(?:of|the|de|von)?\s*[A-Z][\w'’-]+){0,4})"

PATTERNS: list[tuple[str, str, dict]] = [
    (rf"{PROPER}{ADV},?\s+(?:desperately\s+)?(?:wants|desires|covets|seeks|craves)\s+(?:to\s+\w+\s+)?(?:the\s+)?{PROPER}", "WANTS", {"priority": 0.7}),
    (rf"{PROPER}{ADV},?\s+(?:needs|requires|must have)\s+(?:to\s+\w+\s+)?(?:the\s+)?{PROPER}", "NEEDS", {"priority": 0.75}),
    (rf"{PROPER}{ADV},?\s+(?:fears|dreads|is afraid of|is terrified of)\s+(?:the\s+)?{PROPER}", "FEARS", {"priority": 0.6}),
    (rf"{PROPER}{ADV},?\s+(?:controls|rules|runs|governs|holds)\s+(?:the\s+)?{PROPER}", "CONTROLS", {}),
    (rf"{PROPER}{ADV},?\s+(?:owns|possesses|carries|keeps)\s+(?:the\s+)?{PROPER}", "OWNS", {}),
    (rf"{PROPER}{ADV},?\s+(?:hates|despises|loathes|distrusts)\s+(?:the\s+)?{PROPER}", "RELATIONSHIP", {"trust": -50, "label": "rival"}),
    (rf"{PROPER}{ADV},?\s+(?:trusts|loves|admires|is allied with|serves)\s+(?:the\s+)?{PROPER}", "RELATIONSHIP", {"trust": 45, "label": "ally"}),
    (rf"{PROPER}\s+owes\s+(?:a\s+debt\s+to\s+)?(?:the\s+)?{PROPER}", "RELATIONSHIP", {"trust": -10, "label": "debt"}),
    (rf"{PROPER}{ADV},?\s+(?:knows about|knows of|knows that|has learned of|discovered)\s+(?:the\s+)?{PROPER}", "KNOWS", {"confidence": 1.0}),
    (rf"{PROPER}{ADV},?\s+(?:believes|suspects|has heard)\s+(?:that\s+)?(?:the\s+)?{PROPER}", "BELIEVES", {"confidence": 0.6}),
    (rf"{PROPER},?\s+(?:a|an|the)?\s*(?:member|agent|leader|head|captain|guildmistress|guildmaster) of\s+(?:the\s+)?{PROPER}", "MEMBER_OF", {}),
]
STOP = {"The", "A", "An", "He", "She", "They", "It", "His", "Her", "Their", "When", "If", "But", "And", "In", "On"}


def guess_type(name: str) -> str:
    if re.search(rf"\b{FACTION_WORDS}\b", name):
        return "Faction"
    # Judge by the head noun: "Customs Seal of Greywater" -> "Seal", "Crown Envoy" -> "Envoy"
    words = re.sub(r"\s+of\s+.*$", "", name).split()
    head = words[-1] if words else name
    if re.fullmatch(PLACE_WORDS, head):
        return "Location"
    if re.fullmatch(ITEM_WORDS, head):
        return "Item"
    if re.fullmatch(MONSTER_WORDS, head):
        return "Monster"
    return "Character"


def mock_extract(chunk: str, resolver: EntityResolver) -> ExtractionResult:
    nodes: dict[str, ExtractedNode] = {}
    edges: list[ExtractedEdge] = []

    def ensure(surface: str) -> str | None:
        surface = surface.strip(" .,;:'\"")
        if not surface or surface in STOP:
            return None
        hit = resolver.resolve(surface)
        if hit:
            return resolver.names.get(hit, surface)
        if surface not in nodes:
            nodes[surface] = ExtractedNode(name=surface, type=guess_type(surface))  # type: ignore[arg-type]
        return surface

    for sentence in re.split(r"(?<=[.!?])\s+|\n+", chunk):
        for pattern, kind, extra in PATTERNS:
            for m in re.finditer(pattern, sentence):
                a, b = ensure(m.group(1)), ensure(m.group(2))
                if a and b and a != b:
                    edges.append(ExtractedEdge(kind=kind, source=a, target=b, **extra))  # type: ignore[arg-type]
    return ExtractionResult(nodes=list(nodes.values()), edges=edges)


def extract(chunk: str, resolver: EntityResolver) -> ExtractionResult:
    if get_settings().mock_llm:
        return mock_extract(chunk, resolver)
    return llm_extract(chunk, sorted(set(resolver.names.values())))

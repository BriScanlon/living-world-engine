"""Obsidian / Markdown parsing: YAML frontmatter -> node properties, [[Wikilinks]] -> baseline edges."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import PurePath
from typing import Any

import frontmatter

WIKILINK = re.compile(r"\[\[([^\]|#]+)(?:#[^\]|]*)?(?:\|([^\]]+))?\]\]")

TYPE_ALIASES = {
    "character": "Character", "npc": "Character", "pc": "Character", "person": "Character",
    "faction": "Faction", "organization": "Faction", "organisation": "Faction", "guild": "Faction",
    "location": "Location", "place": "Location", "settlement": "Location", "tavern": "Location",
    "town": "Location", "city": "Location", "region": "Location", "building": "Location",
    "item": "Item", "artifact": "Item", "artefact": "Item", "object": "Item",
    "monster": "Monster", "creature": "Monster", "beast": "Monster",
    "goal": "Goal", "event": "Event", "session": "Session", "session-notes": "Session", "rumor": "Rumor",
}

# frontmatter key -> (edge kind, default priority/trust)
EDGE_KEYS: dict[str, tuple[str, Any]] = {
    "wants": ("WANTS", 0.7), "needs": ("NEEDS", 0.7), "fears": ("FEARS", 0.6),
    "controls": ("CONTROLS", None), "owns": ("OWNS", None),
    "knows": ("KNOWS", 1.0), "believes": ("BELIEVES", 0.6),
    "allies": ("RELATIONSHIP", (40, "ally")), "ally": ("RELATIONSHIP", (40, "ally")),
    "friends": ("RELATIONSHIP", (30, "friend")), "rivals": ("RELATIONSHIP", (-40, "rival")),
    "enemies": ("RELATIONSHIP", (-70, "enemy")), "enemy": ("RELATIONSHIP", (-70, "enemy")),
    "debts": ("RELATIONSHIP", (-10, "debt")), "serves": ("RELATIONSHIP", (40, "sworn")),
}
LINK_PROPS = {"location": "Location", "faction": "Faction", "headquarters": "Location", "parent": "Location",
              "owner": None, "target": None}
SCALAR_PROPS = {"role", "kind", "personality", "description", "location_type", "item_type", "resources",
                "influence", "value", "threat", "is_major", "is_party", "priority", "clock_segments",
                "clock_progress", "ticks_every_days", "status"}


@dataclass
class ParsedNote:
    title: str
    node_type: str | None
    aliases: list[str]
    tags: list[str]
    props: dict[str, Any]
    link_props: dict[str, str]
    typed_edges: list[tuple[str, str, Any]]   # (kind, target surface, default)
    body: str
    wikilinks: list[str] = field(default_factory=list)


def _links_in(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        out: list[str] = []
        for v in value:
            out += _links_in(v)
        return out
    s = str(value)
    found = [m.group(1).strip() for m in WIKILINK.finditer(s)]
    return found or [s.strip()] if s.strip() else []


def parse_markdown(text: str, filename: str = "note.md") -> ParsedNote:
    post = frontmatter.loads(text)
    meta = {str(k).lower(): v for k, v in (post.metadata or {}).items()}
    title = str(meta.get("name") or meta.get("title") or PurePath(filename).stem).strip()
    raw_type = str(meta.get("type") or meta.get("category") or "").lower().strip()
    tags = [str(t).lstrip("#") for t in (meta.get("tags") or [])] if isinstance(meta.get("tags"), list) \
        else ([str(meta["tags"]).lstrip("#")] if meta.get("tags") else [])
    node_type = TYPE_ALIASES.get(raw_type) or next((TYPE_ALIASES[t.lower()] for t in tags if t.lower() in TYPE_ALIASES), None)
    if raw_type in ("tavern", "town", "city", "settlement", "building", "region"):
        meta.setdefault("location_type", "tavern" if raw_type == "tavern" else raw_type)
    aliases = meta.get("aliases") or meta.get("alias") or []
    if isinstance(aliases, str):
        aliases = [a.strip() for a in aliases.split(",")]

    props = {k: meta[k] for k in SCALAR_PROPS if k in meta and meta[k] is not None}
    link_props = {}
    for k in LINK_PROPS:
        links = _links_in(meta.get(k))
        if links:
            link_props[k] = links[0]
    typed: list[tuple[str, str, Any]] = []
    for k, (kind, default) in EDGE_KEYS.items():
        for target in _links_in(meta.get(k)):
            typed.append((kind, target, default))
    body = post.content
    links = list(dict.fromkeys(m.group(1).strip() for m in WIKILINK.finditer(body)))
    return ParsedNote(title=title, node_type=node_type, aliases=[str(a) for a in aliases], tags=tags,
                      props=props, link_props=link_props, typed_edges=typed, body=body, wikilinks=links)


def strip_wikilinks(text: str) -> str:
    return WIKILINK.sub(lambda m: m.group(2) or m.group(1), text)


def chunk_markdown(text: str, max_chars: int = 4000) -> list[str]:
    """Split on headings, then pack sections up to ``max_chars``."""
    sections = re.split(r"(?m)^(?=#{1,3}\s)", text)
    chunks: list[str] = []
    buf = ""
    for s in sections:
        if len(buf) + len(s) > max_chars and buf:
            chunks.append(buf)
            buf = ""
        while len(s) > max_chars:
            cut = s.rfind("\n\n", 0, max_chars)
            cut = cut if cut > max_chars // 2 else max_chars
            chunks.append(s[:cut])
            s = s[cut:]
        buf += s
    if buf.strip():
        chunks.append(buf)
    return [c.strip() for c in chunks if c.strip()]

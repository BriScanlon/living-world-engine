"""Lightweight entity resolution: alias table + fuzzy matching.

"The Baron", "Lord Vane" and "Baron Vane" all resolve to ``Character/lord-aldric-vane``:
1. exact (case-insensitive) hit in the alias table (node names, ``aliases`` fields and
   ``Alias`` documents);
2. the same after stripping honorifics / articles ("Lord", "Captain", "the" ...);
3. rapidfuzz token-sort ratio over all surface forms, above a threshold.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

from rapidfuzz import fuzz, process

HONORIFICS = {
    "the", "lord", "lady", "sir", "dame", "baron", "baroness", "captain", "capt", "brother",
    "sister", "father", "mother", "master", "mistress", "old", "king", "queen", "prince",
    "princess", "duke", "duchess", "count", "countess", "guildmistress", "guildmaster", "high",
}
RESOLVABLE = ("Character", "Faction", "Location", "Item", "Monster")


def strip_honorifics(s: str) -> str:
    words = [w for w in re.split(r"\s+", s.strip()) if w]
    while len(words) > 1 and words[0].lower().strip(".,") in HONORIFICS:
        words = words[1:]
    return " ".join(words)


def norm(s: str) -> str:
    return re.sub(r"[^a-z0-9 ]+", "", s.lower()).strip()


@dataclass
class Mention:
    doc_id: str
    surface: str
    start: int
    end: int


class EntityResolver:
    def __init__(self, docs: Iterable[dict], threshold: int = 86) -> None:
        self.threshold = threshold
        self.surfaces: dict[str, str] = {}   # normalized surface -> doc id
        self.types: dict[str, str] = {}
        self.names: dict[str, str] = {}
        aliases = []
        for d in docs:
            if d["@type"] in RESOLVABLE:
                self.types[d["@id"]] = d["@type"]
                self.names[d["@id"]] = d.get("name", d["@id"])
                for s in [d.get("name", ""), *d.get("aliases", [])]:
                    self._add(s, d["@id"])
            elif d["@type"] == "Alias":
                aliases.append(d)
        for a in aliases:
            self._add(a["surface"], a["canonical"])

    def _add(self, surface: str, doc_id: str) -> None:
        if not surface:
            return
        self.surfaces.setdefault(norm(surface), doc_id)
        stripped = norm(strip_honorifics(surface))
        if stripped and len(stripped) > 2:
            self.surfaces.setdefault(stripped, doc_id)

    def add_entity(self, doc_id: str, name: str, doc_type: str, aliases: Iterable[str] = ()) -> None:
        self.types[doc_id] = doc_type
        self.names[doc_id] = name
        for s in [name, *aliases]:
            self._add(s, doc_id)

    # ------------------------------------------------------------------ resolve
    def resolve(self, surface: str, doc_type: str | None = None) -> str | None:
        for key in (norm(surface), norm(strip_honorifics(surface))):
            hit = self.surfaces.get(key)
            if hit and (doc_type is None or self.types.get(hit) == doc_type):
                return hit
        choices = {k: v for k, v in self.surfaces.items()
                   if doc_type is None or self.types.get(v) == doc_type}
        if not choices:
            return None
        best = process.extractOne(norm(strip_honorifics(surface)), list(choices), scorer=fuzz.token_sort_ratio)
        if best and best[1] >= self.threshold:
            return choices[best[0]]
        return None

    def find_mentions(self, text: str) -> list[Mention]:
        """Longest-first, non-overlapping mentions of known surface forms in free text."""
        lowered = text.lower()
        taken: list[tuple[int, int]] = []
        out: list[Mention] = []
        for surface in sorted(self.surfaces, key=len, reverse=True):
            if len(surface) < 3:
                continue
            for m in re.finditer(rf"(?<![a-z0-9]){re.escape(surface)}(?![a-z0-9])", lowered):
                s, e = m.span()
                if any(s < te and e > ts for ts, te in taken):
                    continue
                taken.append((s, e))
                out.append(Mention(self.surfaces[surface], text[s:e], s, e))
        return sorted(out, key=lambda m: m.start)

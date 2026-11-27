"""Link what session notes and lore mention to the catalogue: the SRD 5.2.1 plus the campaign's homebrew.

Run inside ``srd.catalogue.homebrew(srd.homebrew.overlay_entries(store))`` so the campaign's own creatures, peoples
and treasures are found first (and replace what they override). A ``Monster`` or ``Item`` whose name matches an entry
gets its ``srd_ref`` (and a monster its ``challenge``/``threat``, so it counts in battle strength); a character whose
people is named gets the canonical ``species``. Matching forgives case, articles, counts and plurals ("three bog
ghouls", "a pack of Marsh Wights", "Bog Ghoul pack (5)").

Mock mode has no model to recognise a name like "Marsh Wight" as a creature, so ``mentions`` finds the campaign's
homebrew names in the text and ``peoples`` finds "Name, a Mirefolk smuggler" patterns.
"""
from __future__ import annotations

import re
from typing import Any

from srd import catalogue

_LEAD = re.compile(r"^(?:(?:a|an|the|some|several|many|few|two|three|four|five|six|seven|eight|nine|ten|\d+)\s+)+"
                   r"(?:(?:pack|band|swarm|horde|flock|troop|company)\s+of\s+)?", re.I)
_TAIL = re.compile(r"\s+(?:pack|band|swarm|horde|flock|troop)?\s*(?:\(\d+\))?$", re.I)


def _words(s: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9' -]+", " ", s.lower()).replace("-", " ").split())


def singular(word: str) -> str:
    """Rough English singular of a last word: ghouls -> ghoul, wolves -> wolf, harpies -> harpy, boxes -> box."""
    w = word
    if len(w) > 4 and w.endswith("ves"):
        return w[:-3] + "f"
    if len(w) > 4 and w.endswith("ies"):
        return w[:-3] + "y"
    if len(w) > 4 and re.search(r"(ses|xes|zes|ches|shes)$", w):
        return w[:-2]
    if len(w) > 3 and w.endswith("s") and not w.endswith("ss"):
        return w[:-1]
    return w


def key(name: str) -> str:
    """The match key of a name: lower-case words, no leading article/count, no trailing count, last word singular."""
    s = _TAIL.sub("", _LEAD.sub("", name.strip()))
    words = _words(s).split()
    if words:
        words[-1] = singular(words[-1])
    return " ".join(words)


class CatalogueLinker:
    """Lookups over the catalogue as it stands (built once per ingest, inside the homebrew layer)."""

    def __init__(self) -> None:
        data = catalogue.data()
        self._by = {kind: {} for kind in ("monster", "item", "species")}
        for kind, k in (("monster", "monsters"), ("item", "magic_items"), ("species", "species")):
            for e in data.get(k, []):
                self._by[kind].setdefault(key(e["name"]), e)  # homebrew comes first in data(), so it wins
        self.home = {kind: [n for n in catalogue.homebrew_names(kind)] for kind in ("monster", "item", "species")}

    def find(self, kind: str, name: str | None) -> dict[str, Any] | None:
        return self._by[kind].get(key(name)) if name else None

    def props_for(self, node_type: str, name: str, doc: dict[str, Any] | None = None) -> dict[str, Any]:
        """Catalogue fields to add to a node (never overwriting what it already has)."""
        doc = doc or {}
        if doc.get("srd_ref") or node_type not in ("Monster", "Item"):
            return {}
        e = self.find("monster" if node_type == "Monster" else "item", name)
        if not e:
            return {}
        out: dict[str, Any] = {"srd_ref": e["id"]}
        if node_type == "Monster":
            if e.get("cr") not in (None, "") and not doc.get("challenge"):
                out["challenge"] = str(e["cr"])
            cr = e.get("cr_value")
            if cr is None:
                from srd.homebrew import cr_value
                cr = cr_value(e.get("cr"))
            if cr is not None and doc.get("threat") is None:
                out["threat"] = int(cr)
        elif e.get("rarity") and not doc.get("rarity"):
            out["rarity"] = e["rarity"]
        return out

    def species(self, text: str | None) -> str | None:
        """The canonical name of a people ("mirefolk" -> "Mirefolk", "Elves" -> "Elf")."""
        e = self.find("species", text)
        return e["name"] if e else None

    # ------------------------------------------------------------------ text scans (mock mode, prompt hints)
    @staticmethod
    def _pattern(name: str) -> re.Pattern[str]:
        words = [re.escape(w) for w in _words(name).split()]
        last = words[-1] + r"(?:s|es)?" if words else ""
        return re.compile(r"(?<![\w'])" + r"[\s-]+".join(words[:-1] + [last]) + r"(?![\w'])", re.I)

    def mentions(self, text: str) -> list[tuple[str, str]]:
        """Homebrew creatures and treasures named in ``text``: (catalogue name, node type)."""
        found = []
        for kind, node_type in (("monster", "Monster"), ("item", "Item")):
            for n in self.home[kind]:
                if self._pattern(n).search(text):
                    found.append((n, node_type))
        return found

    def peoples(self, text: str) -> list[tuple[str, str]]:
        """'Ysolde Fenwright, a Mirefolk smuggler' -> [("Ysolde Fenwright", "Mirefolk")], for any catalogued people."""
        names = sorted({e["name"] for e in self._by["species"].values()}, key=len, reverse=True)
        if not names:
            return []
        people = "|".join(re.escape(n) + r"(?:s|es)?" for n in names)
        proper = r"((?:[A-Z][\w'’-]+)(?:\s+(?:of|the|de|von)?\s*[A-Z][\w'’-]+){0,3})"
        out = []
        for m in re.finditer(proper + r",?\s+(?:is\s+)?(?:a|an|the)\s+(?:\w+\s+)?(" + people + r")\b", text):
            sp = self.species(m.group(2))
            if sp:
                out.append((m.group(1), sp))
        return out

    def hints(self, text: str) -> list[dict[str, str]]:
        """The campaign's own catalogue entries named in ``text``, for the extraction prompt."""
        out = [{"name": n, "kind": {"Monster": "creature", "Item": "treasure"}[t]} for n, t in self.mentions(text)]
        out += [{"name": n, "kind": "people"} for n in self.home["species"] if self._pattern(n).search(text)]
        return out

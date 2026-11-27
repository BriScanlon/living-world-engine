"""Read-only SRD 5.2.1 reference catalogue (CC-BY-4.0, see ATTRIBUTION).

Built once from the PDF by ``scripts/build_srd_catalogue.py`` into ``srd_5_2_1.json``.
World documents point at entries with ``srd_ref`` (e.g. ``srd:ghoul``) instead of copying stats.

**Homebrew** (``srd.homebrew``) layers the DM's own creatures, peoples and items over the SRD: inside
``with catalogue.homebrew(entries):`` every lookup here (``by_id``, ``monster``, ``monsters_for_biome``,
``species_names``, ``magic_items``...) sees those entries first (ids ``home:<uid>``), and an entry with
``overrides: "srd:..."`` (or the same name) replaces that SRD entry. Outside the block it is the plain SRD.

The biome / culture mappings below are the engine's own choices, not SRD content.
"""
from __future__ import annotations

import json
import random
from contextlib import contextmanager
from contextvars import ContextVar
from functools import lru_cache
from pathlib import Path
from typing import Any

PATH = Path(__file__).with_name("srd_5_2_1.json")

# Azgaar biome ids -> monster names / types that plausibly live there (engine's own mapping)
BIOME_FAUNA: dict[int, dict[str, list[str]]] = {
    1: {"names": ["Blue Dragon", "Brass Dragon", "Lamia", "Mummy", "Giant Scorpion", "Camel", "Vulture", "Gnoll"]},
    2: {"names": ["Brass Dragon", "Gnoll", "Hyena", "Mummy", "Vulture"]},
    3: {"names": ["Gnoll", "Hyena", "Lion", "Elephant", "Giraffe", "Ankheg", "Centaur"]},
    4: {"names": ["Centaur", "Ankheg", "Wolf", "Bulette", "Hippogriff", "Gold Dragon", "Axe Beak"]},
    5: {"names": ["Green Dragon", "Ape", "Giant Snake", "Couatl", "Panther", "Lizardfolk"]},
    6: {"names": ["Green Dragon", "Dryad", "Owlbear", "Wolf", "Brown Bear", "Ettercap", "Goblin", "Bugbear", "Pixie", "Sprite"]},
    7: {"names": ["Green Dragon", "Giant Snake", "Ape", "Dinosaur", "Allosaurus", "Tyrannosaurus", "Couatl"]},
    8: {"names": ["Green Dragon", "Dryad", "Shambling Mound", "Owlbear", "Giant Spider", "Ettercap"]},
    9: {"names": ["Wolf", "Dire Wolf", "Werewolf", "Brown Bear", "Frost Giant", "Winter Wolf"]},
    10: {"names": ["White Dragon", "Frost Giant", "Winter Wolf", "Yeti", "Mammoth", "Polar Bear"]},
    11: {"names": ["White Dragon", "Remorhaz", "Frost Giant", "Ice Mephit", "Mammoth"]},
    12: {"names": ["Black Dragon", "Lizardfolk", "Crocodile", "Shambling Mound", "Will-o'-Wisp", "Hag", "Giant Frog"]},
}
# Rough "civilised" hazards that turn up anywhere people live
COMMON_THREATS = ["Bandit", "Bandit Captain", "Cultist", "Cultist Fanatic", "Tough", "Ghoul", "Skeleton", "Zombie", "Ghost"]

# NPC role -> SRD humanoid stat block used as a template
ROLE_TEMPLATES = {
    "ruler": "Noble", "noble": "Noble", "captain": "Guard Captain", "guard": "Guard", "priest": "Priest",
    "acolyte": "Priest Acolyte", "spy": "Spy", "thug": "Tough", "boss": "Tough Boss", "mage": "Mage",
    "bandit": "Bandit Captain", "cultist": "Cultist Fanatic", "commoner": "Commoner", "pirate": "Pirate Captain",
    "scout": "Scout", "veteran": "Warrior Veteran", "assassin": "Assassin", "druid": "Druid",
}
ROLE_CLASSES = {
    "ruler": ["Paladin", "Fighter", "Bard"], "captain": ["Fighter", "Paladin"], "priest": ["Cleric"],
    "spy": ["Rogue", "Bard"], "boss": ["Rogue", "Fighter"], "mage": ["Wizard", "Sorcerer", "Warlock"],
    "merchant": ["Bard", "Rogue"], "innkeeper": [], "scholar": ["Wizard"], "hunter": ["Ranger", "Druid"],
}
ROLE_BACKGROUNDS = {"priest": "Acolyte", "spy": "Criminal", "boss": "Criminal", "scholar": "Sage",
                    "captain": "Soldier", "ruler": "Soldier", "mage": "Sage"}


@lru_cache
def load() -> dict[str, Any]:
    if not PATH.exists():
        return {"monsters": [], "magic_items": [], "species": [], "classes": [], "backgrounds": [], "prices": [],
                "attribution": "", "missing": True}
    return json.loads(PATH.read_text(encoding="utf-8"))


# ------------------------------------------------------------------------------------------------ homebrew layer
KIND_KEYS = {"monster": "monsters", "species": "species", "item": "magic_items"}
_homebrew: ContextVar[dict[str, Any] | None] = ContextVar("homebrew", default=None)


@contextmanager
def homebrew(entries: list[dict[str, Any]] | None):
    """Layer homebrew entries (``srd.homebrew.as_catalogue_entries`` shape) over the SRD for the block."""
    token = _homebrew.set({"entries": list(entries or []), "merged": None})
    try:
        yield
    finally:
        _homebrew.reset(token)


def data() -> dict[str, Any]:
    """The catalogue as lookups see it: the SRD, with the active homebrew first and replacing what it overrides."""
    base = load()
    layer = _homebrew.get()
    if not layer or not layer["entries"]:
        return base
    if layer["merged"] is None:
        merged = dict(base)
        for kind, key in KIND_KEYS.items():
            mine = [e for e in layer["entries"] if e.get("kind") == kind]
            gone = {e.get("overrides") for e in mine} | {e["name"].lower() for e in mine}
            merged[key] = mine + [e for e in base.get(key, []) if e["id"] not in gone and e["name"].lower() not in gone]
        layer["merged"] = merged
    return layer["merged"]


def homebrew_names(kind: str) -> list[str]:
    """Names of the active homebrew entries of one kind (monster | species | item)."""
    layer = _homebrew.get()
    return [e["name"] for e in (layer or {}).get("entries", []) if e.get("kind") == kind]


def attribution() -> str:
    return load().get("attribution", "")


def by_id(entry_id: str) -> dict[str, Any] | None:
    cat = data()
    for key in ("monsters", "magic_items", "species", "classes", "backgrounds"):
        for e in cat.get(key, []):
            if e["id"] == entry_id:
                return e
    return None


def monster(name: str) -> dict[str, Any] | None:
    n = name.lower()
    return next((m for m in data()["monsters"] if m["name"].lower() == n), None)


def monsters_for_biome(biome: int, rng: random.Random, *, k: int = 2, max_cr: float = 30.0,
                       min_cr: float = 0.0, used: dict[str, int] | None = None) -> list[dict[str, Any]]:
    """Pick plausible monsters for an Azgaar biome, matching SRD names loosely ("Green Dragon" -> any green dragon
    age band) within a challenge-rating window. Homebrew monsters join the pool for the biomes they list (or any
    biome if they list none) and are picked before SRD ones of the same rarity of use."""
    wanted = BIOME_FAUNA.get(biome, {}).get("names", []) + COMMON_THREATS[:3]
    pool = []
    for m in data()["monsters"]:
        if m.get("cr_value") is None or not (min_cr <= m["cr_value"] <= max_cr):
            continue
        if m.get("homebrew"):
            if not m.get("biomes") or biome in m["biomes"]:
                pool.append(m)
        elif any(w.lower() in m["name"].lower() for w in wanted):
            pool.append(m)
    rng.shuffle(pool)
    # spread the bestiary around (least-used kinds first); among equals, the DM's own creatures first
    pool.sort(key=lambda m: ((used or {}).get(m["name"].split()[-1], 0), not m.get("homebrew")))
    seen, out = set(), []
    for m in pool:
        key = m["name"].split()[-1]
        if key not in seen:
            seen.add(key)
            out.append(m)
            if used is not None:
                used[key] = used.get(key, 0) + 1
        if len(out) >= k:
            break
    return out


def magic_items(rng: random.Random, *, rarities: tuple[str, ...] = ("Rare", "Very Rare", "Legendary"),
                k: int = 3) -> list[dict[str, Any]]:
    pool = [i for i in data()["magic_items"] if i.get("rarity") in rarities and "," not in i["name"]]
    rng.shuffle(pool)
    pool.sort(key=lambda i: not i.get("homebrew"))  # the DM's own treasures first
    return pool[:k]


def species_names() -> list[str]:
    return [s["name"] for s in data()["species"]] or ["Human", "Elf", "Dwarf", "Halfling"]


def class_for_role(role: str, rng: random.Random) -> str | None:
    opts = ROLE_CLASSES.get(role, [])
    return rng.choice(opts) if opts else None


def template_for_role(role: str) -> dict[str, Any] | None:
    name = ROLE_TEMPLATES.get(role)
    return monster(name) if name else None


def lifestyle_gp(level: str) -> float | None:
    p = next((x for x in load()["prices"] if x["item"].lower() == level.lower()), None)
    return p["gp"] if p else None

"""New-world wizard: steps, choices and defaults.

The *order of decisions* follows the AD&D World Builder's Guidebook (approach, then world,
continents, kingdoms, local area, history). The wording, options and tables here are the
engine's own; nothing is copied from the Guidebook.
"""
from __future__ import annotations

from typing import Any

STEP_IDS = ["world", "continents", "kingdoms", "local", "history"]

APPROACHES: dict[str, dict[str, Any]] = {
    "planet-in": {"label": "Planet-in (top down)", "order": ["world", "continents", "kingdoms", "local", "history"],
                  "blurb": "Start with the whole planet and zoom in. Suits exploration and travel campaigns."},
    "kingdom-first": {"label": "Kingdom-first", "order": ["kingdoms", "local", "history", "continents", "world"],
                      "blurb": "Start with realms and their politics. Suits intrigue and war campaigns."},
    "local-out": {"label": "Local-out (start with one town)", "order": ["local", "kingdoms", "history", "continents", "world"],
                  "blurb": "Start with the town the party begins in, then build outwards. Fastest to the table."},
    "literary": {"label": "Literary (bring your own map)", "order": ["world", "kingdoms", "local", "history", "continents"],
                 "blurb": "Start from an Azgaar export or notes you already have, then fill the gaps."},
}

FOCUSES: dict[str, dict[str, Any]] = {
    "classic-realms": {"label": "Old kingdoms and wild borders", "defaults": {
        "land": "continents", "climate": "whole-world", "hostility": "uneasy", "n_states": 9,
        "local_factions": "house-vs-guild", "cataclysm": "war", "monster_density": "normal"}},
    "seafaring": {"label": "Islands, ports and pirates", "defaults": {
        "land": "archipelago", "climate": "tropical", "hostility": "uneasy", "n_states": 8,
        "local_factions": "navy-vs-pirates", "cataclysm": "sundering", "monster_density": "normal"}},
    "court-intrigue": {"label": "Court intrigue", "defaults": {
        "land": "old-world", "climate": "temperate", "hostility": "peaceful", "n_states": 6,
        "local_factions": "house-vs-house", "cataclysm": "none", "monster_density": "low"}},
    "frontier": {"label": "The untamed frontier", "defaults": {
        "land": "pangea", "climate": "whole-world", "hostility": "peaceful", "n_states": 5,
        "local_factions": "house-vs-guild", "cataclysm": "dragon", "monster_density": "high"}},
    "dying-world": {"label": "A dying world", "defaults": {
        "land": "islands", "climate": "northern", "hostility": "warring", "n_states": 7,
        "local_factions": "temple-vs-cult", "cataclysm": "undead", "monster_density": "high"}},
    "holy-war": {"label": "Faith and holy war", "defaults": {
        "land": "continents", "climate": "temperate", "hostility": "warring", "n_states": 8,
        "local_factions": "temple-vs-cult", "cataclysm": "plague", "monster_density": "normal"}},
}

CULTURE_STYLES: dict[str, list[str]] = {
    "high-fantasy": ["Aelvari", "Durnholt", "Sylvane", "Korrath", "Elandri", "Morwen", "Thalassi", "Ithrend", "Calimar", "Varnost"],
    "dark-fantasy": ["Grimvald", "Ashkari", "Morrowin", "Vrakken", "Sorrowmere", "Nethril", "Cinderwake", "Haldane", "Murkholt", "Veyl"],
    "classical": ["Aurelian", "Corvian", "Lydian", "Tarquin", "Numidan", "Thessali", "Ostrogan", "Phrygan", "Iberan", "Sabine"],
    "nordic": ["Skaldi", "Jarnvik", "Frostheim", "Ulfmark", "Rimeholt", "Haldor", "Svarn", "Kveldr", "Isgard", "Brennvik"],
}
RELIGION_PATTERNS = [("Church of the {}", "Organized"), ("Old Faith of the {}", "Folk"), ("Cult of the {}", "Cult"),
                     ("Temple of the {}", "Organized"), ("Circle of the {}", "Folk"), ("Brotherhood of the {}", "Cult")]
RELIGION_SYMBOLS = ["Dawn", "Tide", "Lantern", "Forge", "Pale Moon", "Hollow Star", "Green Man", "Ashen Crown",
                    "Silver Stag", "Drowned God", "Eternal Flame", "Iron Oath"]
DEITY_DOMAINS = ["light", "the sea", "the forge", "death", "harvest", "war", "knowledge", "trickery", "storms", "the hunt"]

SPECIES_MIX = {
    "human-led": "Humans everywhere, other peoples as minorities",
    "mixed": "Each culture has its own people",
    "many-peoples": "Every culture blends two peoples",
}

LOCAL_FACTIONS: dict[str, dict[str, Any]] = {
    "house-vs-guild": {"label": "Noble house vs merchants' guild", "a": ("House {surname}", "noble house", "ruler"),
                       "b": ("The {adj} Guild", "merchants' guild that smuggles on the side", "boss"),
                       "prize": ("{town} Docks", "district"), "seat": ("{surname} Hall", "building")},
    "house-vs-house": {"label": "Two noble houses", "a": ("House {surname}", "ruling noble house", "ruler"),
                       "b": ("House {surname2}", "ambitious rival house", "ruler"),
                       "prize": ("{town} Market", "district"), "seat": ("{surname} Keep", "building")},
    "temple-vs-cult": {"label": "Temple vs hidden cult", "a": ("Temple of the {symbol}", "established temple", "priest"),
                       "b": ("The {adj} Circle", "cult hidden in the old town", "cultist"),
                       "prize": ("{town} Undercroft", "district"), "seat": ("The {symbol} Temple", "building")},
    "navy-vs-pirates": {"label": "Harbour watch vs pirate brotherhood", "a": ("The {town} Watch", "harbour watch", "captain"),
                        "b": ("The {adj} Brotherhood", "pirate brotherhood", "boss"),
                        "prize": ("{town} Harbour", "district"), "seat": ("{town} Watchtower", "building")},
}
ADJECTIVES = ["Ashen", "Crimson", "Silent", "Gilded", "Black", "Salt", "Iron", "Grey", "Hollow", "Velvet"]
TAVERN_PATTERNS = ["The {} {}", "The {} and {}"]
TAVERN_WORDS = (["Drowned", "Rusty", "Gilded", "Laughing", "Sleeping", "Crooked", "Wandering", "Silver"],
                ["Lantern", "Anchor", "Griffon", "Goose", "Barrel", "Crown", "Stag", "Kettle"])

CATACLYSMS = {
    "none": None,
    "war": ("The Long War", "A war between two realms that redrew the borders and emptied the old fortresses."),
    "plague": ("The Grey Plague", "A sickness that halved the cities; the faithful still argue over whose sin caused it."),
    "dragon": ("The Ashfall", "A great dragon burned the old capital and claimed its ruins as a lair."),
    "sundering": ("The Sundering", "The sea rose and broke the land into islands; drowned towns lie under the shallows."),
    "undead": ("The Hollow Winter", "The dead rose for a year and a day; barrow-fields still stir at midwinter."),
}
MONSTER_DENSITY = {"low": 1, "normal": 2, "high": 3}
LEVEL_BANDS = {"1-4": (0, 4), "5-10": (1, 10), "11-16": (3, 16), "17-20": (5, 24)}

PERSONALITIES = ["Proud and paranoid", "Patient and transactional", "Tired but decent", "Warm, nosy, fiercely neutral",
                 "Idealistic and stubborn", "Charming in public, ruthless in private", "Cold and exacting",
                 "Generous to a fault", "Superstitious and quick to anger", "Curious, reckless, loyal"]


def defaults(focus: str = "classic-realms") -> dict[str, Any]:
    f = FOCUSES.get(focus, FOCUSES["classic-realms"])["defaults"]
    return {
        "approach": "planet-in", "focus": focus,
        "world": {"name": "", "seed": None, "climate": f["climate"], "size": "medium", "source": "generate", "upload_id": None,
                  "lore": ""},
        "continents": {"land": f["land"]},
        "kingdoms": {"n_states": f["n_states"], "hostility": f["hostility"], "n_cultures": 6, "culture_style": "high-fantasy",
                     "species_mix": "mixed", "n_religions": 4},
        "local": {"town_name": "", "tavern_name": "", "local_factions": f["local_factions"], "n_notables": 5,
                  "party_name": "The Company", "macguffin_rarity": "Very Rare"},
        "history": {"years_back": 600, "eras": 3, "cataclysm": f["cataclysm"], "saga": True, "prose": True,
                    "monster_density": f["monster_density"], "level_band": "1-4"},
    }


def spec() -> dict[str, Any]:
    """Everything the UI needs to render the wizard."""
    return {
        "approaches": APPROACHES,
        "focuses": {k: {"label": v["label"], "defaults": defaults(k)} for k, v in FOCUSES.items()},
        "steps": {
            "world": {"title": "World", "guide": "Worlds and planetology", "fields": [
                {"key": "name", "label": "World name", "type": "text", "roll": "world_name"},
                {"key": "seed", "label": "Seed", "type": "number", "hint": "Same seed and answers = same world"},
                {"key": "climate", "label": "Climate band", "type": "select",
                 "options": {"whole-world": "Whole world (pole to pole)", "northern": "Northern", "temperate": "Temperate belt",
                             "tropical": "Tropical", "southern": "Southern"}},
                {"key": "size", "label": "Detail", "type": "select",
                 "options": {"small": "Small region (~1,500 km across)", "medium": "Medium region (~2,500 km)",
                             "large": "Large region (~4,000 km, a continent)"}},
                {"key": "source", "label": "Map source", "type": "select",
                 "options": {"generate": "Generate a planet", "azgaar": "Use my Azgaar export (Full JSON)"}},
                {"key": "lore", "label": "One-line pitch", "type": "text"},
            ]},
            "continents": {"title": "Continents", "guide": "Continents and geography", "fields": [
                {"key": "land", "label": "Shape of the land", "type": "select",
                 "options": {"continents": "Continents", "old-world": "Old World (inland seas)", "pangea": "One supercontinent",
                             "islands": "Large islands", "archipelago": "Archipelago"}},
            ]},
            "kingdoms": {"title": "Kingdoms", "guide": "Kingdoms and sociology", "fields": [
                {"key": "n_states", "label": "Realms", "type": "number", "min": 2, "max": 14},
                {"key": "hostility", "label": "Relations between realms", "type": "select",
                 "options": {"peaceful": "Mostly peaceful", "uneasy": "Uneasy rivals", "warring": "At war"}},
                {"key": "n_cultures", "label": "Cultures", "type": "number", "min": 1, "max": 10},
                {"key": "culture_style", "label": "Naming style", "type": "select",
                 "options": {k: k.replace("-", " ").title() for k in CULTURE_STYLES}},
                {"key": "species_mix", "label": "Peoples (SRD species)", "type": "select", "options": SPECIES_MIX},
                {"key": "n_religions", "label": "Religions", "type": "number", "min": 1, "max": 8},
            ]},
            "local": {"title": "Local area", "guide": "Cities and provinces", "fields": [
                {"key": "town_name", "label": "Starting town", "type": "text", "hint": "Blank = pick a port town", "roll": "town_name"},
                {"key": "tavern_name", "label": "Tavern", "type": "text", "roll": "tavern_name"},
                {"key": "local_factions", "label": "Local power struggle", "type": "select",
                 "options": {k: v["label"] for k, v in LOCAL_FACTIONS.items()}},
                {"key": "n_notables", "label": "Notable NPCs", "type": "number", "min": 3, "max": 8},
                {"key": "party_name", "label": "Party name", "type": "text"},
                {"key": "macguffin_rarity", "label": "Contested magic item (SRD)", "type": "select",
                 "options": {"Rare": "Rare", "Very Rare": "Very rare", "Legendary": "Legendary"}},
            ]},
            "history": {"title": "History", "guide": "History and mythology", "fields": [
                {"key": "years_back", "label": "Years of recorded history", "type": "number", "min": 50, "max": 5000},
                {"key": "eras", "label": "Ages", "type": "number", "min": 1, "max": 6},
                {"key": "cataclysm", "label": "A defining catastrophe", "type": "select",
                 "options": {k: (v[0] if v else "None") for k, v in CATACLYSMS.items()}},
                {"key": "saga", "label": "A legendary hero's saga", "type": "checkbox"},
                {"key": "monster_density", "label": "Monsters in the wilds", "type": "select",
                 "options": {"low": "Few", "normal": "Normal", "high": "Many"}},
                {"key": "level_band", "label": "Party level band (sets monster challenge)", "type": "select",
                 "options": {k: f"Levels {k}" for k in LEVEL_BANDS}},
                {"key": "prose", "label": "Have the model write descriptions (real model only; a few minutes)",
                 "type": "checkbox"},
            ]},
        },
        "rolls": {"world_name": ["The Shattered Reach", "Aldmere", "The Hollow Crown", "Vael", "Thessaly Reach",
                                 "The Ember Isles", "Kaldren", "The Grey Marches", "Solmere", "The Drowned Kingdoms"],
                  "town_name": ["Greyhaven", "Saltmere", "Kettleford", "Brightwater", "Ravensgate", "Duskholm",
                                "Marrowby", "Wrenfield", "Stonereach", "Fogmouth"],
                  "tavern_name": [f"The {a} {b}" for a, b in zip(TAVERN_WORDS[0], TAVERN_WORDS[1])]},
    }

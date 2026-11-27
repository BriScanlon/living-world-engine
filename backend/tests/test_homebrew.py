"""Homebrew: the DM's own creatures, peoples and items, layered over the SRD and used by the wizard."""
import random
import uuid

import pytest

from srd import catalogue
from srd import homebrew as hb

WIGHT = {"kind": "monster", "name": "Marsh Wight", "size": "Medium", "type": "Undead", "ac": 13, "hp": 45,
         "cr": "2", "speed": "30 ft.", "traits": "Bog Stride\nCold Touch", "actions": ["Grasping Claw"],
         "biomes": [12], "description": "A drowned soldier that walks the fens."}
MIREFOLK = {"kind": "species", "name": "Mirefolk", "size": "Small", "speed": "30 feet", "creature_type": "Humanoid",
            "traits": ["Hold Breath", "Reed Step"]}
LANTERN = {"kind": "item", "name": "Drowned Lantern", "rarity": "Very Rare", "category": "Wondrous Item"}
RED_GOBLIN = {"kind": "monster", "name": "Red-cap Goblin", "ac": 15, "hp": 7, "cr": "1/4", "overrides": "srd:goblin-warrior"}


def _entries(*raw):
    return [hb.from_doc(hb.to_doc(hb.clean(r))) for r in raw]


def test_homebrew_layers_over_the_srd_only_inside_the_block():
    entries = _entries(WIGHT, MIREFOLK, LANTERN)
    assert catalogue.by_id("home:marsh-wight") is None
    with catalogue.homebrew(entries):
        w = catalogue.by_id("home:marsh-wight")
        assert w["name"] == "Marsh Wight" and w["cr_value"] == 2.0 and w["traits"] == ["Bog Stride", "Cold Touch"]
        assert "Mirefolk" in catalogue.species_names()
        assert catalogue.magic_items(random.Random(1), rarities=("Very Rare",), k=1)[0]["name"] == "Drowned Lantern"
        marsh = catalogue.monsters_for_biome(12, random.Random(2), k=2, max_cr=4, used={})
        assert marsh[0]["name"] == "Marsh Wight"  # the DM's own creature comes first where it lives
        desert = catalogue.monsters_for_biome(1, random.Random(2), k=5, max_cr=30, used={})
        assert all(m["name"] != "Marsh Wight" for m in desert)  # and nowhere it doesn't
        assert catalogue.homebrew_names("species") == ["Mirefolk"]
    assert catalogue.by_id("home:marsh-wight") is None and "Mirefolk" not in catalogue.species_names()


def test_an_override_replaces_the_srd_entry():
    if not catalogue.monster("Goblin Warrior"):
        pytest.skip("SRD catalogue not built")
    with catalogue.homebrew(_entries(RED_GOBLIN)):
        assert catalogue.by_id("srd:goblin-warrior") is None
        assert catalogue.monster("Red-cap Goblin")["ac"] == 15
        assert not any(m["id"] == "srd:goblin-warrior" for m in catalogue.data()["monsters"])
    assert catalogue.by_id("srd:goblin-warrior")


def test_entries_are_validated_and_started_from_the_srd():
    with pytest.raises(hb.HomebrewError, match="AC"):
        hb.clean({"kind": "monster", "name": "No Armour", "hp": 5})
    with pytest.raises(hb.HomebrewError, match="Challenge rating"):
        hb.clean({**WIGHT, "cr": "lots"})
    with pytest.raises(hb.HomebrewError, match="overrides"):
        hb.clean({**WIGHT, "overrides": "srd:not-a-thing"})
    with pytest.raises(hb.HomebrewError, match="kind"):
        hb.clean({"kind": "spell", "name": "Fireball"})
    assert hb.clean(RED_GOBLIN)["cr_value"] == 0.25
    if catalogue.monster("Goblin Warrior"):
        draft = hb.from_srd("srd:goblin-warrior")
        assert draft["kind"] == "monster" and draft["name"] == "Goblin Warrior (homebrew)" and draft["derived_from"] == "srd:goblin-warrior"
        assert hb.clean(draft)["ac"] == catalogue.monster("Goblin Warrior")["ac"]
        assert any(r["id"] == "srd:goblin-warrior" for r in hb.search_srd("gobl", "monster"))


# ---------------------------------------------------------------------------- needs TerminusDB
@pytest.fixture
def library(monkeypatch, tmp_path):
    from config import get_settings
    from graph import store as store_mod
    monkeypatch.setenv("HOMEBREW_DB", f"lwe_test_hb_{uuid.uuid4().hex[:8]}")
    monkeypatch.setenv("ATLAS_DIR", str(tmp_path / "atlas"))
    get_settings.cache_clear()
    store_mod._store = None
    try:
        store_mod.get_store().ping()
    except Exception:
        pytest.skip("TerminusDB not reachable")
    lib = hb.library()
    yield lib
    lib._root().delete_database(lib.db, team=lib.team, force=True)
    hb._READY.discard(lib.db)
    store_mod._store = None
    get_settings.cache_clear()


def test_the_library_saves_renames_exports_and_imports(library):
    hb.save(library, WIGHT)
    hb.save(library, MIREFOLK)
    assert [e["name"] for e in hb.entries(library)] == ["Marsh Wight", "Mirefolk"]
    hb.save(library, {**WIGHT, "name": "Fen Wight"}, replace_uid="marsh-wight")  # a rename
    assert [e["id"] for e in hb.entries(library)] == ["home:fen-wight", "home:mirefolk"]
    exported = hb.export(library)
    hb.remove(library, "fen-wight")
    hb.remove(library, "home:mirefolk")
    assert hb.entries(library) == []
    res = hb.import_entries(library, exported + [{"kind": "monster", "name": "Broken"}])
    assert res["imported"] == 2 and res["rejected"][0]["name"] == "Broken"
    assert {e["name"] for e in hb.entries(library)} == {"Fen Wight", "Mirefolk"}
    # the library is not a campaign
    assert library.db not in [c["id"] for c in library.list_campaigns()]


def test_exit_test_a_homebrew_creature_and_people_turn_up_in_a_new_world(library):
    """The design doc's exit test: add a homebrew creature and a homebrew people, build a world with the wizard,
    and see both appear in the wilds and among the NPCs."""
    from graph.store import GraphStore, get_store
    from wizard.builder import build_world
    hb.save(library, {**WIGHT, "biomes": []})  # lives anywhere, so it can't miss this world's biomes
    hb.save(library, MIREFOLK)
    res = build_world({"focus": "dying-world", "world": {"name": "Homebrew Vale", "seed": random.randrange(10**6, 10**7),
                                                         "size": "small"},
                       "kingdoms": {"species_mix": "many-peoples"}, "local": {"town_name": "Reedmarket"}})
    world = GraphStore(get_store().s, res["campaign"])
    try:
        docs = world.all_documents("main")
        monsters = [d for d in docs if d["@type"] == "Monster"]
        people = [d for d in docs if d["@type"] == "Character" and d.get("kind") == "npc"]
        assert any(m.get("srd_ref") == "home:marsh-wight" for m in monsters), [m["name"] for m in monsters]
        assert any(c.get("species") == "Mirefolk" for c in people), sorted({str(c.get("species")) for c in people})
        # the world keeps its own copy of the homebrew it was made with
        assert {e["uid"] for e in hb.entries(world)} == {"marsh-wight", "mirefolk"} and res["homebrew"] == 2
        with catalogue.homebrew(hb.overlay_entries(world)):
            assert catalogue.by_id("home:marsh-wight")["type"] == "Undead"
    finally:
        get_store().delete_campaign(res["campaign"])

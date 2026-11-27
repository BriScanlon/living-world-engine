"""Session notes and lore link what they mention to the catalogue: the campaign's homebrew first, then the SRD."""
import json
import uuid

import pytest
from ingestion.catalogue_link import CatalogueLinker, key
from ingestion.extract import _in_text, llm_extract, mock_extract
from ingestion.resolution import EntityResolver
from srd import catalogue
from srd import homebrew as hb

WIGHT = {"kind": "monster", "name": "Marsh Wight", "size": "Medium", "type": "Undead", "ac": 13, "hp": 45, "cr": "2"}
MIREFOLK = {"kind": "species", "name": "Mirefolk", "size": "Small", "speed": "30 feet", "creature_type": "Humanoid"}
LANTERN = {"kind": "item", "name": "Saltglass Lantern", "rarity": "Very Rare", "category": "Wondrous Item"}
NOTES = ("Ysolde Fenwright, a Mirefolk smuggler, swears she saw two Marsh Wights on the causeway. "
         "She wants the Saltglass Lantern before the Ashen Guild finds it.")
srd_built = pytest.mark.skipif(not catalogue.monster("Troll"), reason="SRD catalogue not built")


def _entries(*raw):
    return [hb.from_doc(hb.to_doc(hb.clean(r))) for r in raw]


def test_match_keys_forgive_articles_counts_and_plurals():
    assert key("three Bog Ghouls") == key("Bog Ghoul") == "bog ghoul"
    assert key("Marsh Wight pack (5)") == key("a pack of Marsh Wights") == "marsh wight"
    assert key("Wolves") == "wolf" and key("the Harpies") == "harpy" and key("Boxes") == "box"
    assert key("Moss") == "moss"  # not "mos"


def test_homebrew_entries_link_with_their_stats():
    with catalogue.homebrew(_entries(WIGHT, MIREFOLK, LANTERN)):
        linker = CatalogueLinker()
        assert linker.props_for("Monster", "two Marsh Wights") == {"srd_ref": "home:marsh-wight", "challenge": "2",
                                                                    "threat": 2}
        assert linker.props_for("Item", "the Saltglass Lantern") == {"srd_ref": "home:saltglass-lantern",
                                                                   "rarity": "Very Rare"}
        assert linker.species("mirefolk") == "Mirefolk"
        # never overwrite what a node already has, and only monsters and items get catalogue refs
        assert linker.props_for("Monster", "Marsh Wight", {"srd_ref": "srd:ghoul"}) == {}
        assert "threat" not in linker.props_for("Monster", "Marsh Wight", {"threat": 5})  # a set threat stays
        assert linker.props_for("Character", "Marsh Wight") == {}
        assert linker.props_for("Monster", "Something Unheard Of") == {}
    with catalogue.homebrew([]):
        assert CatalogueLinker().props_for("Monster", "Marsh Wight") == {}  # outside the campaign's layer: unknown


@srd_built
def test_srd_creatures_link_too_and_homebrew_wins():
    with catalogue.homebrew([]):
        assert CatalogueLinker().props_for("Monster", "Trolls")["srd_ref"] == "srd:troll"
    red = {"kind": "monster", "name": "Troll", "ac": 12, "hp": 60, "cr": "3", "overrides": "srd:troll"}
    with catalogue.homebrew(_entries(red)):
        assert CatalogueLinker().props_for("Monster", "a Troll") == {"srd_ref": "home:troll", "challenge": "3", "threat": 3}


def test_mock_mode_finds_homebrew_names_and_peoples():
    with catalogue.homebrew(_entries(WIGHT, MIREFOLK, LANTERN)):
        linker = CatalogueLinker()
        assert sorted(linker.mentions(NOTES)) == [("Marsh Wight", "Monster"), ("Saltglass Lantern", "Item")]
        assert linker.peoples(NOTES) == [("Ysolde Fenwright", "Mirefolk")]
        assert {h["name"]: h["kind"] for h in linker.hints(NOTES)} == {
            "Marsh Wight": "creature", "Saltglass Lantern": "treasure", "Mirefolk": "people"}
        res = mock_extract(NOTES, EntityResolver([]), linker)
    by = {n.name: n for n in res.nodes}
    assert by["Marsh Wight"].type == "Monster" and by["Saltglass Lantern"].type == "Item"
    assert by["Ysolde Fenwright"].species == "Mirefolk" and by["Ysolde Fenwright"].type == "Character"
    assert "Mirefolk" not in by  # a people is a species, not a node


def test_plurals_ground_and_the_model_gets_catalogue_hints(monkeypatch):
    assert _in_text("Marsh Wight", "two marsh wights on the causeway")
    assert not _in_text("Marsh Wight", "the marsh is quiet")
    seen = {}

    class Fake:
        def structured(self, system, user, schema, **kw):
            seen["system"], seen["user"] = system, json.loads(user)
            return schema()

    from llm import client
    monkeypatch.setattr(client, "get_llm", lambda: Fake())
    llm_extract(NOTES, [], [{"name": "Marsh Wight", "kind": "creature"}])
    assert seen["user"]["CATALOGUE"] == [{"name": "Marsh Wight", "kind": "creature"}] and "CATALOGUE" in seen["system"]
    llm_extract(NOTES, [])
    assert "CATALOGUE" not in seen["user"]  # nothing to hint: the prompt stays as it was


# ---------------------------------------------------------------------------- a whole ingest (needs TerminusDB)
def test_session_notes_link_to_the_campaigns_homebrew(tmp_path, monkeypatch):
    from config import Settings, get_settings
    from graph.seed import seed
    from graph.store import GraphStore
    from graph.world import WorldView
    from ingestion.pipeline import Source, ingest_source
    monkeypatch.setenv("MOCK_LLM", "true")
    monkeypatch.setenv("HOMEBREW_DB", f"lwe_test_hb_{uuid.uuid4().hex[:8]}")  # an empty library: only the campaign's copy
    get_settings.cache_clear()
    st = GraphStore(Settings(terminusdb_db=f"lwe_test_{uuid.uuid4().hex[:8]}", mock_llm=True))
    try:
        st.ping()
    except Exception:
        pytest.skip("TerminusDB not reachable")
    try:
        seed(st, reset=True)
        hb.copy_into(st, [WIGHT, MIREFOLK, LANTERN])
        out = ingest_source(st, Source(name="session-16.md", kind="markdown", text=NOTES, is_session=True), "main", 12)
        w = WorldView(st.all_documents("main"), 12)
        wight = next(d for d in w.of_type("Monster") if d["name"] == "Marsh Wight")
        assert wight["srd_ref"] == "home:marsh-wight" and wight["challenge"] == "2" and wight["threat"] == 2
        lantern = next(d for d in w.of_type("Item") if d["name"] == "Saltglass Lantern")
        assert lantern["srd_ref"] == "home:saltglass-lantern" and lantern["rarity"] == "Very Rare"
        ysolde = next(d for d in w.of_type("Character") if d["name"] == "Ysolde Fenwright")
        assert ysolde["species"] == "Mirefolk"
        assert out["catalogue_links"] >= 3
    finally:
        st._root().delete_database(st.db, team=st.team, force=True)
        get_settings.cache_clear()

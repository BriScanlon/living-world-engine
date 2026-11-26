"""Lore extraction on a real model: send only the names a chunk refers to, and keep only what the chunk says."""
import pytest

from ingestion.extract import ExtractedEdge, ExtractedNode, ExtractionResult, extract, ground, relevant_names
from ingestion.resolution import EntityResolver

TEXT = """The Baron now fears the Lantern Company. Brother Callum trusts the Lantern Company.
Sela Marrow suspects the Sunken Ledger is hidden at the shrine.
The Crown Envoy, a cold woman named Ysolde Fenwright, wants the Customs Seal of Greywater.
Ysolde Fenwright distrusts Lord Vane."""


def _doc(t, name, *aliases):
    return {"@type": t, "@id": f"{t}/{name.lower().replace(' ', '-')}", "name": name, "aliases": list(aliases)}


@pytest.fixture
def resolver():
    return EntityResolver([
        _doc("Character", "Lord Aldric Vane", "the Baron", "Lord Vane"),
        _doc("Faction", "The Lantern Company", "the party"),
        _doc("Character", "Brother Callum Ashby", "Brother Callum"),
        _doc("Character", "Sela Marrow"),
        _doc("Item", "The Sunken Ledger"),
        # the kind of names qwen2.5 copied out of the old 300-name list
        _doc("Location", "Alal Shire"), _doc("Location", "Athgal Barony"), _doc("Faction", "Duchy of Zulmok"),
    ])


def test_only_names_the_chunk_refers_to_are_sent(resolver):
    names = relevant_names(TEXT, resolver)
    assert {"Lord Aldric Vane", "The Lantern Company", "Brother Callum Ashby", "Sela Marrow",
            "The Sunken Ledger"} <= set(names)
    assert not {"Alal Shire", "Athgal Barony", "Duchy of Zulmok"} & set(names)


def test_grounding_drops_echoed_entities_and_their_edges(resolver):
    echoed = ExtractionResult(
        nodes=[ExtractedNode(name="Ysolde Fenwright", type="Character", aliases=["the Crown Envoy"]),
               ExtractedNode(name="Customs Seal of Greywater", type="Item"),
               ExtractedNode(name="Lord Aldric Vane", type="Character"),  # known, mentioned as "the Baron"
               ExtractedNode(name="Alal Shire", type="Location"),          # copied from NAMES
               ExtractedNode(name="Conquer Dortor", type="Goal")],         # invented
        edges=[ExtractedEdge(kind="WANTS", source="Ysolde Fenwright", target="Customs Seal of Greywater"),
               ExtractedEdge(kind="RELATIONSHIP", source="Ysolde Fenwright", target="Lord Aldric Vane", trust=-40),
               ExtractedEdge(kind="FEARS", source="Lord Aldric Vane", target="The Lantern Company"),
               ExtractedEdge(kind="WANTS", source="Sela Marrow", target_text="leverage over the Baron"),
               ExtractedEdge(kind="CONTROLS", source="Alal Shire", target="Athgal Barony"),
               ExtractedEdge(kind="WANTS", source="Duchy of Zulmok", target="Conquer Dortor")])
    out = ground(echoed, TEXT, resolver)
    assert [n.name for n in out.nodes] == ["Ysolde Fenwright", "Customs Seal of Greywater", "Lord Aldric Vane"]
    assert [(e.kind, e.source) for e in out.edges] == [("WANTS", "Ysolde Fenwright"), ("RELATIONSHIP", "Ysolde Fenwright"),
                                                     ("FEARS", "Lord Aldric Vane"), ("WANTS", "Sela Marrow")]


def test_the_real_path_sends_few_names_and_grounds_the_reply(resolver, monkeypatch):
    from config import get_settings
    from ingestion import extract as mod
    sent = {}

    def fake_llm(chunk, known):
        sent["known"] = known
        return ExtractionResult(nodes=[ExtractedNode(name="Ysolde Fenwright", type="Character"),
                                       ExtractedNode(name="Athgal Barony", type="Location")])

    monkeypatch.setenv("MOCK_LLM", "false")
    get_settings.cache_clear()
    monkeypatch.setattr(mod, "llm_extract", fake_llm)
    try:
        out = extract(TEXT, resolver)
    finally:
        get_settings.cache_clear()
    assert "Athgal Barony" not in sent["known"] and len(sent["known"]) <= 6
    assert [n.name for n in out.nodes] == ["Ysolde Fenwright"]


def test_mock_extraction_is_unchanged(resolver):
    out = extract(TEXT, resolver)  # MOCK_LLM defaults to true in tests
    assert any(e.kind == "FEARS" and e.source == "Lord Aldric Vane" for e in out.edges)
    assert any(n.name == "Ysolde Fenwright" for n in out.nodes)

"""Fast tests: no TerminusDB needed."""
from graph.schema import clean_for_write, normalize, slugify
from graph.seed import DEMO_SESSION_SUMMARY, demo_world
from graph.world import WorldView, active_at
from ingestion.markdown import chunk_markdown, parse_markdown
from ingestion.resolution import EntityResolver
from llm import mock


def world(day=None):
    docs = demo_world()
    for d in docs:  # mimic TerminusDB round-trip (no Nones, ints)
        for k in [k for k, v in d.items() if v is None]:
            d.pop(k)
    return WorldView([normalize(d) for d in docs], day)


def test_active_at_is_half_open():
    d = {"in_world_start": 5, "in_world_end": 10}
    assert not active_at(d, 4)
    assert active_at(d, 5) and active_at(d, 9)
    assert not active_at(d, 10)
    assert active_at({}, 3)


def test_clean_for_write_drops_unknown_and_none():
    out = clean_for_write({"@type": "Character", "@id": "Character/x", "uid": "x", "name": "X",
                           "bogus": 1, "role": None, "tags": ["b", "a", "a"]})
    assert out == {"@type": "Character", "uid": "x", "name": "X", "tags": ["a", "b"]}


def test_resolver_merges_titles_and_aliases():
    r = EntityResolver(demo_world())
    assert r.resolve("The Baron") == "Character/lord-aldric-vane"
    assert r.resolve("Lord Vane") == "Character/lord-aldric-vane"
    assert r.resolve("Baron Vane") == "Character/lord-aldric-vane"
    assert r.resolve("Captain Brand") == "Character/captain-hollis-brand"
    assert r.resolve("Sela Marow") == "Character/sela-marrow"  # fuzzy
    assert r.resolve("Totally Unknown Person") is None


def test_markdown_frontmatter_and_wikilinks():
    note = parse_markdown(
        "---\ntype: npc\naliases: [The Gull]\nfaction: \"[[The Ashen Guild]]\"\nwants: [\"[[Greywater Docks]]\"]\n"
        "rivals: [\"[[Captain Brand]]\"]\n---\nWorks for [[Sela Marrow|Sela]] at [[The Gilded Gull]].",
        "Mira Quell.md",
    )
    assert note.title == "Mira Quell" and note.node_type == "Character"
    assert note.link_props["faction"] == "The Ashen Guild"
    assert ("WANTS", "Greywater Docks", 0.7) in note.typed_edges
    assert note.wikilinks == ["Sela Marrow", "The Gilded Gull"]


def test_chunking_respects_limit():
    text = "\n\n".join(f"## H{i}\n" + "word " * 300 for i in range(10))
    chunks = chunk_markdown(text, max_chars=2000)
    assert len(chunks) > 1 and all(len(c) <= 2000 for c in chunks)


def test_activation_radius_two_hops():
    w = world(10)
    reach = w.within_hops(["Item/the-sunken-ledger"], 2)
    assert reach["Character/lord-aldric-vane"][0] == 1
    assert reach["Character/captain-hollis-brand"][0] == 2
    assert "Location/blackfen-marsh" not in reach


def test_epistemic_context_excludes_unknown_nodes():
    w = world(10)
    known = w.known_ids("Character/brother-callum-ashby")
    assert "Item/the-sunken-ledger" in known
    assert "Monster/the-blackfen-hag" not in known
    ctx = w.agent_context("Character/brother-callum-ashby")
    assert all(k["id"] in known for k in ctx["known_world"])


def test_mock_session_delta_parses_party_actions():
    delta = mock.session_delta(DEMO_SESSION_SUMMARY, world(11))
    kinds = [a.action_type for a in delta.party_actions]
    assert kinds == ["steal", "bribe", "help", "harm"]
    steal = delta.party_actions[0]
    assert steal.visibility == "secret" and steal.item_transferred_id == "Item/the-sunken-ledger"
    assert delta.party_actions[3].visibility == "public"


def test_mock_agent_intents_are_deterministic_and_epistemically_valid():
    w = world(12)
    for agent in w.agents():
        ctx = w.agent_context(agent["@id"])
        a = mock.agent_intent(agent["@id"], w, ctx, day=12, since_day=10, seed=1, round_no=0)
        b = mock.agent_intent(agent["@id"], w, ctx, day=12, since_day=10, seed=1, round_no=0)
        assert a == b
        assert a.target_id is None or a.target_id in w.known_ids(agent["@id"])


def test_slugify():
    assert slugify("Lord Aldric Vane") == "lord-aldric-vane"
    assert slugify("Café ‘Noir’!") == "cafe-noir"


def test_forecast_branch_names_never_collide_within_a_second():
    import re
    from datetime import datetime, timezone

    from graph.store import to_branch_id
    from simulation.engine import branch_name
    now = datetime(2026, 11, 27, 13, 49, 25, tzinfo=timezone.utc)
    names = {branch_name(14, now) for _ in range(200)}       # a double-clicked Run, a re-run, a fast test loop
    assert len(names) == 200
    name = names.pop()
    assert re.fullmatch(r"forecast/session-14-20261127t134925-[0-9a-f]{4}", name)  # readable, sorts by time
    assert "/" not in to_branch_id(name)


def test_session_numbers_come_from_session_names_only():
    from api.routes import session_number
    assert session_number("session-13") == 13 and session_number("forecast/session-14") == 14
    assert session_number("ingest/session-15-notes") == 15 and session_number("ingest/recap-16") == 16
    # an imported world's slug ends in its seed; setup steps and dates are not sessions
    assert session_number("import/azgaar-region-trial-3433") is None
    assert session_number("setup/kingdoms") is None and session_number("vtt/main") is None
    assert session_number("ingest/2026-11-27-recap") is None


def test_the_wizard_links_to_the_self_hosted_map_generator(monkeypatch):
    from api.routes import wizard_spec
    from config import get_settings
    monkeypatch.setenv("FMG_PORT", "8070")
    get_settings.cache_clear()
    try:
        assert wizard_spec()["fmg_port"] == 8070
        monkeypatch.delenv("FMG_PORT")
        get_settings.cache_clear()
        assert wizard_spec()["fmg_port"] is None  # not running: no link
    finally:
        get_settings.cache_clear()

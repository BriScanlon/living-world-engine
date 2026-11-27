"""The wizard's prose pass: which descriptions the model rewrites, what it is shown, and that nothing it does can
break a build."""
import json
import random

import pytest
from graph import builders as b
from wizard import prose
from wizard.prose import ProseBatch, ProseEntry


def _docs():
    town = b.entity("Location", "Fogmouth", location_type="town", session="setup/local", tags=["campaign-start", "port"],
                    description="A harbour town where the campaign begins.")
    tavern = b.entity("Location", "The Gull", location_type="tavern", parent=town["@id"], session="setup/local",
                      description="Where the town trades news, ale and lies.")
    guild = b.entity("Faction", "The Salt Guild", session="setup/local", headquarters=tavern["@id"],
                     description="The town's merchants.")
    keeper = b.entity("Character", "Mags Brine", kind="npc", role="keeper of The Gull", faction=guild["@id"],
                      location=tavern["@id"], session="setup/local", description="")
    realm = b.entity("Faction", "Kingdom of Aldmere", session="setup/kingdoms", description="A realm.")
    party = b.entity("Faction", "The Company", is_party=True, session="setup/local", description="The player characters' company.")
    king = b.entity("Character", "King Odo", kind="npc", is_major=True, role="King of Aldmere", faction=realm["@id"],
                    session="setup/kingdoms", description="")
    war = b.event("The Long War", "A bitter border war; neither side forgot it.", day=-365 * 40, event_type="conflict",
                  actors=[realm["@id"]], session="setup/history")
    province = b.entity("Province", "Aldmere March", session="setup/kingdoms", description="A province.")
    wolves = b.entity("Monster", "Wolf pack (5)", session="setup/history", description="Medium beast, CR 1/4 (SRD).")
    secret = b.desire(keeper["@id"], "WANTS", text="to poison the Guildmaster", priority=0.9)
    return [town, tavern, guild, keeper, realm, party, king, war, province, wolves, secret]


def test_the_local_area_comes_first_and_bulk_stays_template():
    picked = [d["name"] for d in prose.candidates(_docs())]
    assert picked[:2] == ["Fogmouth", "The Gull"]                  # places in the starting town
    assert set(picked[2:5]) == {"The Salt Guild", "Mags Brine", "The Company"}
    assert picked[5:] == ["Kingdom of Aldmere", "King Odo", "The Long War"]
    assert "Aldmere March" not in picked and "Wolf pack (5)" not in picked  # provinces and monsters keep templates


def test_the_model_sees_public_facts_only():
    docs = _docs()
    by_id = {d["@id"]: d for d in docs if d.get("@id")}
    keeper = next(d for d in docs if d.get("name") == "Mags Brine")
    facts = prose.public_facts(keeper, by_id)
    assert facts["faction"] == "The Salt Guild" and facts["location"] == "The Gull"
    assert "poison" not in json.dumps(facts)                       # desires never reach the prompt
    war = next(d for d in docs if d.get("name") == "The Long War")
    wf = prose.public_facts(war, by_id)
    assert wf["years_before_the_campaign"] == 40 and wf["actors"] == ["Kingdom of Aldmere"] and wf["placeholder"].startswith("A bitter")
    sent = []
    prose.write_prose(docs, {"world": {"lore": "Fog and salt"}}, "Aldmere",
                      resolve=lambda system, user: sent.append(user) or ProseBatch(entries=[]))
    assert sent and all("poison" not in u for u in sent) and '"pitch":"Fog and salt"' in sent[0]


def test_good_text_is_kept_and_bad_text_is_dropped():
    docs = _docs()

    def resolve(system, user):
        entries = json.loads(user)["entries"]
        out = [ProseEntry(id=e["id"], text=f"  \"{e['name']} smells of brine, smoke and old rope; everyone knows it.\" ")
               for e in entries if e["name"] != "The Gull"]
        out += [ProseEntry(id=next(e["id"] for e in entries if e["name"] == "The Gull"), text="Short."),  # too short
                ProseEntry(id="Location/somewhere-else", text="A place the model made up, which nobody asked for.")]
        return ProseBatch(entries=out)

    changed, stats = prose.write_prose(docs, {}, "Aldmere", resolve=resolve)
    by = {d["name"]: d for d in changed}
    assert "The Gull" not in by and all(d["@id"] != "Location/somewhere-else" for d in changed)
    assert by["Fogmouth"]["description"] == "Fogmouth smells of brine, smoke and old rope; everyone knows it."
    assert by["The Long War"]["summary"].startswith("The Long War smells") and "description" not in by["The Long War"]
    assert by["King Odo"]["name"] == "King Odo"                    # names are never touched
    assert stats == {"candidates": 8, "batches": 1, "written": 7, "kept": 1, "failed_batches": 0}


def test_a_failing_batch_keeps_its_templates(monkeypatch):
    monkeypatch.setattr(prose, "BATCH", 3)
    calls = []

    def resolve(system, user):
        calls.append(user)
        if len(calls) == 2:
            raise TimeoutError("model went away")
        return ProseBatch(entries=[ProseEntry(id=e["id"], text=f"{e['name']} is written anew, plainly and well.")
                                   for e in json.loads(user)["entries"]])

    changed, stats = prose.write_prose(_docs(), {}, "Aldmere", resolve=resolve)
    assert stats["batches"] == 3 and stats["failed_batches"] == 1 and stats["written"] == 5 and stats["kept"] == 3


def test_the_mock_twin_changes_nothing():
    from llm import mock
    changed, stats = prose.write_prose(_docs(), {}, "Aldmere", resolve=lambda system, user: mock.wizard_prose(user))
    assert changed == [] and stats["written"] == 0


# ---------------------------------------------------------------------------- whole builds (needs TerminusDB)
def _build(tmp_path, monkeypatch, structured):
    from config import get_settings
    from graph import store as store_mod
    from llm import client as llm_client
    from wizard.builder import build_world
    monkeypatch.setenv("ATLAS_DIR", str(tmp_path / "atlas"))
    monkeypatch.setenv("MOCK_LLM", "false")
    get_settings.cache_clear()
    store_mod._store = None
    root = store_mod.get_store()
    try:
        root.ping()
    except Exception:
        pytest.skip("TerminusDB not reachable")

    class FakeLLM:
        def structured(self, system, user, schema, **kw):
            assert schema is ProseBatch
            return structured(user)

    monkeypatch.setattr(llm_client, "get_llm", lambda: FakeLLM())
    events = []
    res = build_world({"focus": "seafaring", "world": {"name": "Prose Test", "seed": random.randrange(10**6, 10**7),
                                                       "size": "small", "lore": "Drowned bells and salt saints"},
                       "local": {"town_name": "Fogmouth"}}, lambda *a, **k: events.append(a))
    return root, store_mod.GraphStore(root.s, res["campaign"]), res, events


@pytest.fixture
def cleanup():
    made = []
    yield made
    from config import get_settings
    from graph import store as store_mod
    for root, campaign in made:
        root.delete_campaign(campaign)
    store_mod._store = None
    get_settings.cache_clear()


def test_a_real_model_build_writes_prose_in_one_commit(tmp_path, monkeypatch, cleanup):
    root, st, res, events = _build(tmp_path, monkeypatch, lambda user: ProseBatch(entries=[
        ProseEntry(id=e["id"], text=f"Under drowned bells, {e['name']} keeps its salt-stained dignity.")
        for e in json.loads(user)["entries"]]))
    cleanup.append((root, res["campaign"]))
    log = [c["message"] for c in st.log("main") if c["message"].startswith("[setup]")]
    assert len(log) == 6 and log[0].startswith("[setup] prose: ") and "written by" in log[0]
    town = next(d for d in st.all_documents("main") if "campaign-start" in d.get("tags", []))
    assert town["name"] == "Fogmouth" and town["description"].startswith("Under drowned bells, Fogmouth")
    assert res["prose"]["written"] > 10 and res["prose"]["failed_batches"] == 0
    assert any(e[0] == "prose" and e[1] == "done" for e in events)
    assert any(e[0] == "prose" and e[1] == "progress" for e in events)


def test_a_model_that_fails_still_builds_the_world(tmp_path, monkeypatch, cleanup):
    def broken(user):
        raise ConnectionError("Ollama is down")

    root, st, res, events = _build(tmp_path, monkeypatch, broken)
    cleanup.append((root, res["campaign"]))
    log = [c["message"] for c in st.log("main") if c["message"].startswith("[setup]")]
    assert len(log) == 5                                            # no prose commit
    assert res["prose"]["written"] == 0 and res["prose"]["failed_batches"] == res["prose"]["batches"] > 0
    assert any(e[0] == "prose" and e[1] == "done" and "kept the templates" in e[2] for e in events)

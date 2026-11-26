"""SRD catalogue + new-world wizard + campaigns."""
import random

import pytest

from srd import catalogue
from wizard import spec as S
from wizard.builder import merge_answers


def test_srd_catalogue_is_complete_and_attributed():
    c = catalogue.load()
    assert "Creative Commons Attribution 4.0" in c["attribution"]
    assert len(c["monsters"]) >= 320 and len(c["magic_items"]) >= 250
    assert len(c["species"]) == 9 and len(c["classes"]) == 12 and len(c["backgrounds"]) == 4
    ghoul = catalogue.monster("Ghoul")
    assert ghoul["cr"] == "1" and ghoul["hp"] == 22 and ghoul["abilities"]["dex"] == 15
    assert catalogue.by_id("srd:ghoul") == ghoul


def test_every_npc_role_template_exists_in_the_srd():
    for role, name in catalogue.ROLE_TEMPLATES.items():
        assert catalogue.monster(name), f"{role} -> {name} missing from SRD"


def test_biome_bestiary_respects_challenge_window():
    rng = random.Random(1)
    for biome in range(1, 13):
        for m in catalogue.monsters_for_biome(biome, rng, k=3, max_cr=4):
            assert m["cr_value"] <= 4


def test_answers_merge_with_focus_defaults():
    a = merge_answers({"focus": "seafaring", "kingdoms": {"n_states": 5}})
    assert a["continents"]["land"] == "archipelago" and a["kingdoms"]["n_states"] == 5
    assert a["kingdoms"]["hostility"] == S.FOCUSES["seafaring"]["defaults"]["hostility"]
    assert set(S.spec()["steps"]) == set(S.STEP_IDS)
    for ap in S.APPROACHES.values():
        assert sorted(ap["order"]) == sorted(S.STEP_IDS)


# ---------------------------------------------------------------------------- needs TerminusDB
@pytest.fixture
def built(tmp_path, monkeypatch):
    from config import get_settings
    from graph import store as store_mod

    monkeypatch.setenv("ATLAS_DIR", str(tmp_path / "atlas"))
    get_settings.cache_clear()
    store_mod._store = None
    root = store_mod.get_store()
    try:
        root.ping()
    except Exception:
        pytest.skip("TerminusDB not reachable")
    from wizard.builder import build_world
    seed = random.randrange(10**6, 10**7)
    events = []
    res = build_world({"focus": "dying-world", "world": {"name": "Pytest Realm", "seed": seed, "size": "small"},
                       "local": {"town_name": "Testholm"}}, lambda *a, **k: events.append(a))
    yield res, store_mod.GraphStore(root.s, res["campaign"]), events
    root.delete_campaign(res["campaign"])
    store_mod._store = None
    get_settings.cache_clear()


def test_wizard_builds_a_separate_campaign_one_commit_per_step(built):
    res, store, events = built
    assert res["campaign"].startswith("campaign_pytest_realm_")
    setup = [c["message"] for c in store.log("main") if c["message"].startswith("[setup]")]
    assert len(setup) == 5 and setup[-1].startswith("[setup] 1/5 World")
    assert [e[0] for e in events if e[1] == "done"] == S.STEP_IDS
    docs = store.all_documents("main")
    by = {}
    for d in docs:
        by.setdefault(d["@type"], []).append(d)
    town = next(d for d in by["Location"] if "campaign-start" in d.get("tags", []))
    assert town["name"] == "Testholm" and town.get("lon") is not None
    assert any(d.get("is_party") for d in by["Faction"])
    assert any(d.get("kind") == "deity" for d in by["Character"])
    # SRD references resolve
    refs = [d["srd_ref"] for d in by["Character"] + by.get("Monster", []) + by["Item"] if d.get("srd_ref")]
    assert refs and all(catalogue.by_id(r) for r in refs)
    # history lies in the past; the catastrophe of a dying world happened
    assert any((e.get("in_world_start") or 0) < 0 for e in by["Event"])
    assert any(e["name"] == "The Hollow Winter" for e in by["Event"])
    # the new campaign is listed, and the default campaign is untouched
    ids = [c["id"] for c in store.list_campaigns()]
    assert res["campaign"] in ids

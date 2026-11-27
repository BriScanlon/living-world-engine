"""Battle strength from challenge ratings: who fights on each side, and what they count for."""
import json

import pytest
from graph import builders as b
from graph.world import WorldView
from simulation.strength import Forces, force, unit

srd_built = pytest.mark.skipif(not __import__("srd.catalogue", fromlist=["x"]).monster("Troll"),
                               reason="SRD catalogue not built")


def _world(veterans: int = 0):
    town = b.entity("Location", "Brindle", location_type="town")
    den = b.entity("Location", "Troll Den", location_type="wilderness", parent=None)
    serfs = b.entity("Faction", "The Hungry Serfs", resources=10, influence=10)
    company = b.entity("Faction", "The Iron Company", resources=10, influence=10)
    watch = b.entity("Faction", "The Brindle Watch", resources=20, influence=20)
    docs = [town, den, serfs, company, watch,
            b.entity("Character", "Old Tam", role="Farmer", kind="npc", faction=serfs["@id"], location=town["@id"]),
            b.entity("Character", "Captain Orla", role="Captain of the Watch", kind="npc", faction=watch["@id"],
                     location=town["@id"]),
            b.entity("Character", "Baroness Ivel", role="Baroness of Brindle", kind="npc", location=town["@id"]),
            b.entity("Character", "Sell-sword", role="Hireling", kind="npc", level=6, location=town["@id"]),
            b.entity("Monster", "Troll", srd_ref="srd:troll", location=den["@id"], count=1),
            b.entity("Monster", "Bog Rats", challenge="1/8", location=den["@id"], count=8),
            b.entity("Monster", "Marsh Thing", threat=3, location=town["@id"]),
            b.control(watch["@id"], "CONTROLS", town["@id"])]
    docs += [b.entity("Character", f"Veteran {i}", role="Knight of the company", kind="npc", faction=company["@id"],
                      location=town["@id"]) for i in range(veterans)]
    return WorldView(docs, 10)


@srd_built
def test_challenge_ratings_come_from_the_best_source_available():
    w = _world()
    by = {d["name"]: unit(w, d) for d in w.all.values() if d["@type"] in ("Character", "Monster")}
    assert by["Troll"]["cr"] == "5" and by["Troll"]["xp"] == 1800  # its SRD stat block
    assert by["Bog Rats"]["cr"] == "1/8" and by["Bog Rats"]["xp"] == 8 * 25  # its own challenge, times the pack
    assert by["Marsh Thing"]["cr"] == "3"  # a creature's threat
    assert by["Captain Orla"]["cr"] == "4"  # role -> Guard Captain
    assert by["Baroness Ivel"]["cr"] == "1/8"  # title -> Noble
    assert by["Sell-sword"]["cr"] == "3"  # level 6 -> CR 3
    assert by["Old Tam"]["cr"] == "0"  # nobody special: a commoner


def test_strength_is_log_scaled_xp():
    assert force([])["strength"] == 0
    assert force([{"name": "Goblin", "cr": "1/4", "count": 1, "xp": 50}])["strength"] == pytest.approx(5.8, abs=0.1)
    assert force([{"name": "Dragon", "cr": "17", "count": 1, "xp": 18000}])["strength"] == pytest.approx(75.1, abs=0.1)


@srd_built
def test_a_lair_defends_itself_and_allies_do_not_fight():
    w = _world()
    f = Forces(w)
    serfs = next(d for d in w.of_type("Faction") if d["name"] == "The Hungry Serfs")
    c = f.contest({"agent_id": serfs["@id"], "action": "attack", "target_id": "Location/troll-den"})
    assert c["defender"]["id"] is None and c["defender"]["units"][0] == "Troll (CR 5)"
    assert "Bog Rats x8 (CR 1/8)" in c["defender"]["units"]
    watch = next(d for d in w.of_type("Faction") if d["name"] == "The Brindle Watch")
    c = f.contest({"agent_id": watch["@id"], "action": "seize", "target_id": "Location/brindle"})
    assert c["defender"]["id"] is None  # the watch already holds Brindle: no fight with your own side
    assert f.contest({"agent_id": watch["@id"], "action": "negotiate", "target_id": "Location/brindle"}) is None


@srd_built
def test_the_mock_arbiter_weighs_who_actually_fights():
    """Farmers can't take a troll's den however the dice fall; a company of veterans can."""
    from llm import mock
    for seed in range(5):
        w = _world()
        serfs = next(d for d in w.of_type("Faction") if d["name"] == "The Hungry Serfs")
        ev = mock.arbiter([{"agent_id": serfs["@id"], "action": "attack", "target_id": "Location/troll-den",
                            "resources_spent": 10, "reasoning": "Hunger."}], w, day=11, seed=seed, round_no=0).events[0]
        assert "beaten back" in ev.summary and "against Troll (CR 5)" in ev.summary

        w = _world(veterans=5)
        company = next(d for d in w.of_type("Faction") if d["name"] == "The Iron Company")
        ev = mock.arbiter([{"agent_id": company["@id"], "action": "attack", "target_id": "Location/troll-den",
                            "resources_spent": 10, "reasoning": "Glory."}], w, day=11, seed=seed, round_no=0).events[0]
        assert "came off better" in ev.summary and "Veteran" in ev.summary


@srd_built
def test_the_real_arbiter_is_told_the_forces():
    from simulation import prompts
    w = _world()
    serfs = next(d for d in w.of_type("Faction") if d["name"] == "The Hungry Serfs")
    sent = json.loads(prompts.arbiter_user([{"agent_id": serfs["@id"], "action": "attack", "target_id": "Location/troll-den",
                                             "resources_spent": 10, "reasoning": "Hunger."}], w, 11))
    (c,) = sent["forces"]
    assert c["defender"]["strength"] > 40 and c["attacker"]["strength"] < 5 and "xp" not in c["attacker"]
    assert "weak band does not overrun" in prompts.ARBITER_SYSTEM


def test_homebrew_creatures_count_in_the_overlay():
    from srd import catalogue
    from srd import homebrew as hb
    w = WorldView([b.entity("Monster", "Fen Horror", srd_ref="home:fen-horror", location="Location/x")], 10)
    m = next(iter(w.all.values()))
    assert unit(w, m)["cr"] == "0"  # unknown outside the layer
    horror = hb.from_doc(hb.to_doc(hb.clean({"kind": "monster", "name": "Fen Horror", "ac": 15, "hp": 90, "cr": "7"})))
    with catalogue.homebrew([horror]):
        assert unit(w, m)["cr"] == "7" and unit(w, m)["xp"] == 2900

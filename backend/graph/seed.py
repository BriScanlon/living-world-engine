"""Starter world: the harbour town of Greywater.

Two rival factions (House Vane vs the Ashen Guild), five NPCs with conflicting
WANTS / NEEDS / FEARS, four locations (town, docks, manor, marsh) and one tavern,
plus the party (the Lantern Company). Canon state is "end of session 13, day 10".
"""
from __future__ import annotations

from typing import Any

from graph import builders as b
from graph.schema import slugify

SESSION = "session-13"
CURRENT_DAY = 10

DEMO_SESSION_SUMMARY = (
    "The Lantern Company secretly broke into Vane Manor and stole the Sunken Ledger from Lord Vane's study. "
    "Fleeing to Greywater Docks, they were cornered by Captain Brand, and bribed him to look the other way. "
    "Later that night the party helped Brother Callum hide the ledger's copied pages in the shrine. "
    "At the Drowned Lantern, Kestrel publicly insulted the Baron in front of half the town."
)


def demo_world() -> list[dict[str, Any]]:
    L, C, F, I, M = "Location/", "Character/", "Faction/", "Item/", "Monster/"
    docs: list[dict[str, Any]] = []
    add = docs.append

    # ---------------------------------------------------------------- locations
    add(b.entity("Location", "Greywater", location_type="town", session=SESSION,
                 description="A fog-bound harbour town where the old nobility and the smuggling guilds share the same tide.",
                 aliases=["the town", "Greywater Harbour"]))
    add(b.entity("Location", "Greywater Docks", location_type="district", parent=L + "greywater", session=SESSION,
                 description="Warehouses, net-sheds and the customs house. Whoever holds the docks holds Greywater's coin.",
                 aliases=["the docks", "the harbour"]))
    add(b.entity("Location", "Vane Manor", location_type="building", parent=L + "greywater", session=SESSION,
                 description="The crumbling seat of House Vane, overlooking the harbour.",
                 aliases=["the manor"]))
    add(b.entity("Location", "The Drowned Lantern", location_type="tavern", parent=L + "greywater", session=SESSION,
                 description="Low-beamed dockside tavern; the best place in town to hear anything.",
                 aliases=["Drowned Lantern", "the Lantern", "the tavern"], tags=["tavern"]))
    add(b.entity("Location", "Blackfen Marsh", location_type="wilderness", session=SESSION,
                 description="Reed-choked marsh south of town. Smugglers' paths; something hungry.",
                 aliases=["the fen", "Blackfen"]))

    # ---------------------------------------------------------------- factions
    add(b.entity("Faction", "House Vane", resources=55, influence=70, is_major=True, headquarters=L + "vane-manor",
                 session=SESSION, description="Old noble house holding the town charter, drowning in crown debt.",
                 aliases=["the Vanes", "Vane household"]))
    add(b.entity("Faction", "The Ashen Guild", resources=65, influence=55, is_major=True,
                 headquarters=L + "greywater-docks", session=SESSION,
                 description="Smugglers' guild posing as a dockworkers' brotherhood.",
                 aliases=["Ashen Guild", "the Guild", "the Ash"]))
    add(b.entity("Faction", "The Lantern Company", is_party=True, resources=20, session=SESSION,
                 description="The player characters' adventuring company.",
                 aliases=["the party", "Lantern Company", "the Company", "Kestrel", "the adventurers"]))

    # ---------------------------------------------------------------- NPCs
    add(b.entity("Character", "Lord Aldric Vane", role="Baron of Greywater", kind="npc", is_major=True,
                 faction=F + "house-vane", location=L + "vane-manor", session=SESSION,
                 personality="Proud, paranoid, charming in public, ruthless in private.",
                 description="Head of House Vane. Owes the crown more than the town is worth.",
                 aliases=["The Baron", "Lord Vane", "Baron Vane", "Aldric"]))
    add(b.entity("Character", "Sela Marrow", role="Guildmistress of the Ashen Guild", kind="npc", is_major=True,
                 faction=F + "the-ashen-guild", location=L + "greywater-docks", session=SESSION,
                 personality="Patient, transactional, never forgets a debt.",
                 description="Runs the Ashen Guild from a net-shed office on the docks.",
                 aliases=["The Ash Queen", "Mistress Marrow", "Sela"]))
    add(b.entity("Character", "Captain Hollis Brand", role="Captain of the Watch", kind="npc", is_major=False,
                 faction=F + "house-vane", location=L + "greywater-docks", session=SESSION,
                 personality="Tired, pragmatic, quietly ashamed.",
                 description="Sworn to House Vane, secretly in debt to the Ashen Guild.",
                 aliases=["Captain Brand", "Brand", "Hollis"]))
    add(b.entity("Character", "Wenna Tull", role="Keeper of the Drowned Lantern", kind="npc",
                 location=L + "the-drowned-lantern", session=SESSION,
                 personality="Warm, nosy, fiercely neutral.",
                 description="Hears everything; sells some of it.",
                 aliases=["Old Wenna", "Wenna"]))
    add(b.entity("Character", "Brother Callum Ashby", role="Priest of the Tide Shrine", kind="npc",
                 location=L + "greywater", session=SESSION,
                 personality="Idealistic, stubborn, braver than is wise.",
                 description="Wants the Baron's corruption dragged into the light.",
                 aliases=["Brother Callum", "Callum", "the priest"]))

    # ---------------------------------------------------------------- items / monsters
    add(b.entity("Item", "The Sunken Ledger", item_type="document", value=500, location=L + "vane-manor",
                 session=SESSION, description="House Vane's secret ledger of crown debts and bribes paid.",
                 aliases=["the ledger", "Sunken Ledger", "Vane's ledger"]))
    add(b.entity("Item", "Customs Seal of Greywater", item_type="seal", value=200, location=L + "greywater-docks",
                 session=SESSION, description="Whoever holds the seal sets the harbour tariffs.",
                 aliases=["the customs seal", "harbour seal"]))
    add(b.entity("Monster", "The Blackfen Hag", threat=6, location=L + "blackfen-marsh", session=SESSION,
                 description="Trades favours for teeth and names. The Guild pays her for safe passage.",
                 aliases=["the hag", "Mother Reed"]))

    V, S, BR, W, CA = C + "lord-aldric-vane", C + "sela-marrow", C + "captain-hollis-brand", C + "wenna-tull", C + "brother-callum-ashby"
    HV, AG, PARTY = F + "house-vane", F + "the-ashen-guild", F + "the-lantern-company"
    LEDGER, SEAL = I + "the-sunken-ledger", I + "customs-seal-of-greywater"
    DOCKS, MANOR, TAVERN, TOWN, FEN = (L + "greywater-docks", L + "vane-manor", L + "the-drowned-lantern",
                                       L + "greywater", L + "blackfen-marsh")

    # ---------------------------------------------------------------- control
    for d in [
        b.control(HV, "CONTROLS", TOWN), b.control(HV, "CONTROLS", MANOR),
        b.control(AG, "CONTROLS", DOCKS), b.control(AG, "OWNS", SEAL),
        b.control(V, "OWNS", LEDGER), b.control(W, "OWNS", TAVERN),
    ]:
        add({**d, "source_session": SESSION})

    # ---------------------------------------------------------------- desires
    for d in [
        b.desire(V, "WANTS", target=DOCKS, priority=0.9, note="Docks revenue would clear the crown debt."),
        b.desire(V, "NEEDS", text="gold to repay the crown debt", priority=0.75),
        b.desire(V, "FEARS", target=LEDGER, priority=0.8, note="If the ledger surfaces, the crown takes his title."),
        b.desire(HV, "WANTS", target=SEAL, priority=0.8),
        b.desire(S, "WANTS", target=LEDGER, priority=0.85, note="Leverage to own the Baron outright."),
        b.desire(S, "NEEDS", target=DOCKS, priority=0.9, note="Losing the docks ends the Guild."),
        b.desire(AG, "FEARS", target=HV, priority=0.5),
        b.desire(BR, "NEEDS", text="to clear his gambling debt to the Guild", priority=0.8),
        b.desire(BR, "FEARS", target=V, priority=0.6, note="Exposure as a Guild debtor."),
        b.desire(W, "WANTS", target=TAVERN, priority=0.7, note="Keep the Lantern open and neutral."),
        b.desire(W, "FEARS", text="a street war on the docks", priority=0.6),
        b.desire(CA, "WANTS", target=V, priority=0.75, note="See the Baron answer for his crimes."),
        b.desire(CA, "NEEDS", target=LEDGER, priority=0.6, note="Proof."),
    ]:
        add({**d, "source_session": SESSION})

    # ---------------------------------------------------------------- relationships (directed)
    for a, c, t, label in [
        (V, S, -60, "rival"), (S, V, -55, "rival"), (HV, AG, -50, "rival"), (AG, HV, -50, "rival"),
        (V, BR, 35, "vassal"), (BR, V, 40, "sworn"), (BR, S, 10, "debt"), (S, BR, 20, "asset"),
        (W, S, 15, "supplier"), (W, V, -20, "taxed"), (CA, V, -40, "suspicion"),
        (CA, W, 30, "friend"), (W, CA, 30, "friend"), (V, PARTY, 5, "hired help"),
        (W, PARTY, 20, "regulars"), (BR, PARTY, -5, "wary"), (S, PARTY, 0, "unknown quantity"),
        (CA, PARTY, 25, "hopeful"),
    ]:
        add({**b.relationship(a, c, t, label), "source_session": SESSION})

    # ---------------------------------------------------------------- goals
    add(b.goal(HV, "Seize the Docks", priority=0.85, segments=6, progress=2, every=3, target=DOCKS, session=SESSION,
               description="Revoke the Guild's harbour licence and install Vane customs officers."))
    add(b.goal(AG, "Blackmail the Baron", priority=0.75, segments=4, progress=1, every=4, target=LEDGER, session=SESSION,
               description="Obtain the Sunken Ledger and force Vane to sign the docks over."))
    add(b.goal(CA, "Expose the Baron's Debts", priority=0.6, segments=6, progress=0, every=5, target=V, session=SESSION,
               description="Bring proof of Vane's bribes before the town assembly."))

    # ---------------------------------------------------------------- history events
    envoy = b.event("Crown tax envoy arrives", "A crown envoy arrived demanding Greywater's overdue levy within the month.",
                    day=4, event_type="world", actors=[], targets=[HV], location=TOWN, session=SESSION, uid="evt-crown-envoy")
    raid = b.event("Watch raid on Guild warehouse", "Captain Brand's watch seized smuggled brandy from a Guild warehouse on Vane's orders.",
                   day=8, event_type="faction_move", actors=[HV, BR], targets=[AG], location=DOCKS, session=SESSION,
                   uid="evt-warehouse-raid")
    arrival = b.event("The Lantern Company arrives in Greywater",
                      "Lord Vane hired the party to 'look into' Guild smuggling; Wenna gave them rooms at the Lantern.",
                      day=CURRENT_DAY, event_type="world", actors=[PARTY], targets=[V], location=TAVERN,
                      session=SESSION, uid="evt-party-arrives")
    add(envoy)
    add(raid)
    add(arrival)

    # ---------------------------------------------------------------- knowledge
    for d in [
        b.knows(V, LEDGER), b.knows(V, envoy["@id"]), b.knows(V, raid["@id"]), b.knows(V, AG),
        b.knows(V, SEAL), b.knows(HV, DOCKS), b.knows(HV, SEAL),
        b.knows(S, LEDGER, kind="BELIEVES", confidence=0.7, note="Heard the Baron keeps a second book."),
        b.knows(S, raid["@id"]), b.knows(S, envoy["@id"]), b.knows(S, HV), b.knows(S, FEN),
        b.knows(AG, raid["@id"]),
        b.knows(BR, LEDGER, confidence=0.9), b.knows(BR, raid["@id"]), b.knows(BR, AG),
        b.knows(W, envoy["@id"]), b.knows(W, raid["@id"]), b.knows(W, S), b.knows(W, V), b.knows(W, CA),
        b.knows(V, arrival["@id"]), b.knows(W, arrival["@id"]), b.knows(PARTY, arrival["@id"]),
        b.knows(CA, LEDGER, kind="BELIEVES", confidence=0.4), b.knows(CA, envoy["@id"]), b.knows(CA, TAVERN),
    ]:
        add({**d, "source_session": SESSION})

    # ---------------------------------------------------------------- rumors already circulating
    add(b.rumor("The crown will strip Vane of his title if the levy isn't paid.",
                "\"Crown man was asking after the Baron's books. Mark me, there'll be a new lord by spring.\"",
                location=TAVERN, accuracy="Exaggerated", day=5, about_event=envoy["@id"], speaker="Wenna Tull",
                session=SESSION))

    # ---------------------------------------------------------------- alias table (entity resolution)
    seen: set[str] = set()
    for d in list(docs):
        if d["@type"] in ("Character", "Faction", "Location", "Item", "Monster"):
            for surface in [d["name"], *d.get("aliases", [])]:
                uid = "alias-" + slugify(surface)
                if uid not in seen:
                    seen.add(uid)
                    add({"@type": "Alias", "@id": f"Alias/{uid}", "uid": uid, "surface": surface,
                         "canonical": d["@id"]})
    return docs


def seed(store, reset: bool = False) -> dict[str, Any]:
    if reset:
        store.reset_database()
    else:
        store.ensure_database()
    docs = demo_world()
    store.upsert(docs, "main", "[seed] Greywater demo world (canon through session 13, day 10)")
    return {"documents": len(docs), "current_day": CURRENT_DAY}

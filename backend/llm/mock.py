"""Deterministic stand-ins for every LLM call (``MOCK_LLM=true``).

These are not random filler: they implement a small rule-based BDI model over the same
inputs the real prompts receive (the agent's epistemic context, intents, events), so the
full fork -> simulate -> diff -> merge loop behaves plausibly and reproducibly on a laptop.
"""
from __future__ import annotations

import hashlib
import random
import re
from typing import Any

from graph.world import WorldView
from ingestion.resolution import EntityResolver
from simulation.models import (
    AgentIntentLLM,
    ArbiterResult,
    ControlChange,
    KnowledgeGain,
    NewDesire,
    NewGoal,
    PartyAction,
    RelationshipShift,
    RumorBatch,
    RumorDraft,
    SessionDelta,
    WorldEventDraft,
)


def rng_for(*parts: Any) -> random.Random:
    return random.Random(int(hashlib.sha1("|".join(map(str, parts)).encode()).hexdigest()[:12], 16))


# =========================================================================== session delta
VERBS: dict[str, tuple[str, int]] = {}
for words, kind, delta in [
    ("stole steal stolen took snatched pilfered looted swiped lifted", "steal", -40),
    ("killed slew slain murdered assassinated executed", "kill", -70),
    ("helped saved rescued aided protected healed defended freed sheltered", "help", 25),
    ("attacked insulted humiliated threatened betrayed robbed assaulted burned mocked ambushed", "harm", -30),
    ("bribed paid gifted tipped", "bribe", 15),
    ("discovered learned uncovered revealed exposed found investigated", "discover", 0),
    ("travelled traveled fled sailed rode journeyed", "travel", 0),
]:
    for w in words.split():
        VERBS[w] = (kind, delta)

SECRET_MARKERS = ("secretly", "in secret", "quietly", "under cover", "unseen", "disguised", "at night", "stealthily")


def session_delta(summary: str, world: WorldView) -> SessionDelta:
    resolver = EntityResolver(world.all.values())
    party = world.party
    actions: list[PartyAction] = []
    for sentence in re.split(r"(?<=[.!?])\s+", summary.strip()):
        if not sentence:
            continue
        mentions = [m for m in resolver.find_mentions(sentence) if m.doc_id != (party or {}).get("@id")]
        words = re.findall(r"[a-z]+", sentence.lower())
        kind, delta = next((VERBS[w] for w in words if w in VERBS), ("other", 0))
        loc = next((m.doc_id for m in mentions if m.doc_id.startswith("Location/")), None)
        targets = list(dict.fromkeys(m.doc_id for m in mentions if not m.doc_id.startswith("Location/")))
        if not targets and loc:
            targets = [loc]
        if not targets:
            continue
        item = next((t for t in targets if t.startswith("Item/")), None) if kind == "steal" else None
        killed = [t for t in targets if t.startswith(("Character/", "Monster/"))][:1] if kind == "kill" else []
        if kind == "bribe" or "look the other way" in sentence.lower():
            kind, delta = "bribe", 15
        secret = any(s in sentence.lower() for s in SECRET_MARKERS) and "publicly" not in sentence.lower()
        actions.append(PartyAction(
            summary=sentence.strip(), action_type=kind, target_ids=targets, location_id=loc,
            visibility="secret" if secret else "public", trust_delta=delta,
            item_transferred_id=item, killed_ids=killed,
        ))
    return SessionDelta(party_actions=actions)


# =========================================================================== agent intents
HOSTILE = {"seize", "sabotage", "threaten", "steal", "attack", "spread_rumor"}
SECRET_ACTIONS = {"sabotage", "steal", "hide", "investigate", "spread_rumor", "bribe", "observe"}


def _knows_holder(agent_id: str, allies: set[str], item: str, holder: str | None, world: WorldView,
                  known: set[str]) -> bool:
    """Does the agent know who holds ``item``? (Long-standing control is common knowledge;
    a recent change is only known through an event the agent knows about.)"""
    if holder is None or holder in allies:
        return True
    if holder not in known:
        return False
    edge = next((e for e in world.of_type("ControlEdge") if e["target_id"] == item and e["from_id"] == holder), None)
    if edge and not edge.get("source_event"):
        return True
    return any(holder in (world.get(k) or {}).get("actors", []) and item in (world.get(k) or {}).get("targets", [])
               for k in known if k.startswith("Event/"))


def agent_intent(agent_id: str, world: WorldView, ctx: dict[str, Any], *, day: int, since_day: int,
                 seed: Any, round_no: int, history: list[tuple[str, str | None]] | None = None) -> AgentIntentLLM:
    rng = rng_for(seed, round_no, agent_id)
    history = history or []
    me = world.get(agent_id) or {}
    my_faction = me.get("faction")
    allies = {agent_id, my_faction} - {None}
    known = {k["id"] for k in ctx["known_world"]} | {agent_id}
    trust = {r["to_id"]: r["trust"] for r in ctx["relationships"]}
    goals_by_target = {g["target_id"]: g for g in ctx["goals"] if g.get("target_id")}
    resources = (world.get(my_faction) or {}).get("resources") if me.get("@type") == "Character" else me.get("resources")
    resources = resources or 0
    options: list[tuple[float, AgentIntentLLM]] = []

    def owner(t: str) -> str | None:
        return world.controller_of(t)

    def opt(score: float, action: str, target: str | None, why: str, *, responding_to: str | None = None,
            spend: int | None = None) -> None:
        if target is not None and target not in known:
            return  # epistemic rule: cannot act on what you don't know about
        g = goals_by_target.get(target)
        cost = spend if spend is not None else (0 if action in ("observe", "investigate") else
                                                min(resources, 8 + int(score * 15)))
        score -= 0.4 * history.count((action, target))  # agents don't repeat themselves forever
        options.append((score + rng.random() * 0.15, AgentIntentLLM(
            action=action, target_id=target, resources_spent=max(0, cost), reasoning=why,
            public_or_secret="secret" if action in SECRET_ACTIONS else "public",
            responding_to=responding_to, advances_goal_id=g["id"] if g else None,
        )))

    name = me.get("name", agent_id)

    # --- 1. React to fresh events this agent knows about (ripple effects)
    for k in ctx["known_world"]:
        if k["type"] != "Event" or max(k.get("day") or 0, k.get("learned_on") or 0) < since_day:
            continue
        ev = world.get(k["id"])
        if not ev or agent_id in ev.get("actors", []):
            continue
        lost = {e["target_id"] for e in world.all.values() if e["@type"] == "ControlEdge"
                and e.get("ended_by_event") == ev["@id"] and e["from_id"] in allies}
        hit = [t for t in ev.get("targets", []) if t in allies or owner(t) in allies or t in lost]
        actor = next((a for a in ev.get("actors", []) if a not in allies), None)
        if not hit or not actor or actor not in known:
            continue
        t_actor = trust.get(actor, 0)
        hurt_ours = next((t for t in hit if t in lost), None)
        if hurt_ours and k.get("epistemic") == "BELIEVES":
            opt(1.2, "investigate", actor, f"{name} discovered {world.name(hurt_ours)} is gone and suspects "
                f"{world.name(actor)}. Before acting, {name} needs proof.", responding_to=ev["@id"])
        elif hurt_ours:
            opt(1.15, "investigate", actor, f"{name} learned that {world.name(actor)} took {world.name(hurt_ours)} "
                f"and wants to know where it is now before anyone else does.", responding_to=ev["@id"])
            opt(1.05, "threaten", actor, f"{world.name(hurt_ours)} was taken from {name}; {world.name(actor)} must be "
                f"made to return it.", responding_to=ev["@id"])
        elif t_actor < 0 or ev.get("event_type") in ("conflict", "faction_move"):
            opt(0.95, "threaten" if resources < 30 else "sabotage", actor,
                f"{name} will not let '{ev['name']}' go unanswered (trust {t_actor}).", responding_to=ev["@id"])
        else:
            opt(0.8, "negotiate", actor, f"{name} sees an opening after '{ev['name']}' and approaches "
                f"{world.name(actor)}.", responding_to=ev["@id"])

    # --- 2. Pursue desires / goals
    for d in ctx["desires"]:
        t, p, kind = d.get("target_id"), d.get("priority") or 0.5, d["kind"]
        if t is None:
            opt(p * 0.8, "trade", None, f"{name} {kind.lower()} {d.get('target_text') or 'resources'} and works "
                f"their contacts for coin.")
            continue
        if t not in world.docs:
            continue
        ttype = t.split("/")[0]
        ctl = owner(t)
        if ttype == "Item" and not _knows_holder(agent_id, allies, t, ctl, world, known):
            opt(p * 1.05, "investigate", t, f"{name} {kind.lower()} {world.name(t)} but no longer knows who has it.")
            continue
        if ttype in ("Location", "Province", "Item"):
            if ctl in allies:
                if kind == "FEARS":
                    opt(p, "hide", t, f"{name} fears exposure through {world.name(t)} and moves to bury it.")
                else:
                    opt(p * 0.75, "fortify", t, f"{name} holds {world.name(t)} and means to keep it.")
            elif ctl is None:
                opt(p, "seize", t, f"{name} {kind.lower()} {world.name(t)} and it is up for grabs.")
            else:
                fac_trust = (world.relationship(my_faction, ctl) or {}).get("trust_score", 0) if my_faction else 0
                hostile = min(trust.get(ctl, 0), fac_trust) < 0 or ctl == (world.party or {}).get("@id")
                if kind == "FEARS":
                    opt(p * 1.05, "investigate", ctl if ctl in known else t,
                        f"{name} fears what {world.name(ctl)} could do with {world.name(t)}.")
                elif ttype == "Item":
                    opt(p, "steal" if hostile else "trade", t,
                        f"{name} {kind.lower()} {world.name(t)}, currently held by {world.name(ctl)}.")
                elif hostile:
                    opt(p, "seize" if resources >= 25 else "sabotage", t,
                        f"{name} {kind.lower()} {world.name(t)}; {world.name(ctl)} stands in the way.")
                else:
                    opt(p * 0.9, "negotiate", ctl, f"{name} {kind.lower()} {world.name(t)} and opens talks "
                        f"with {world.name(ctl)}, who holds it.")
        else:  # Character / Faction / Monster
            tr = trust.get(t, 0)
            if kind == "FEARS":
                home = me.get("headquarters") or me.get("location")
                if me.get("@type") == "Faction" and home:
                    opt(p * 0.9, "fortify", home, f"{name} fears {world.name(t)} and hardens {world.name(home)}.")
                else:
                    opt(p * 0.9, "investigate", t, f"{name} fears {world.name(t)} and keeps a careful eye on them.")
            elif tr <= -20:
                opt(p, "spread_rumor" if tr > -50 else "threaten", t,
                    f"{name} {kind.lower()} leverage over {world.name(t)} (trust {tr}).")
            else:
                opt(p * 0.85, "ally", t, f"{name} {kind.lower()} {world.name(t)} as a closer ally.")

    for g in ctx["goals"]:
        if g.get("target_id"):
            continue
        opt(g["priority"], "recruit", None, f"{name} works toward '{g['name']}'.")

    if not options:
        return AgentIntentLLM(action="observe", target_id=None, resources_spent=0,
                              reasoning=f"{name} has nothing pressing and watches how things develop.",
                              public_or_secret="secret")
    options.sort(key=lambda o: -o[0])
    return options[0][1]


# =========================================================================== arbiter
OPPOSED = {"seize": {"fortify", "seize"}, "steal": {"hide", "fortify"}, "sabotage": {"fortify"},
           "attack": {"attack", "fortify"}}


def _power(world: WorldView, intent: dict, rng: random.Random) -> float:
    a = world.get(intent["agent_id"]) or {}
    fac = world.get(a.get("faction")) if a.get("@type") == "Character" else a
    base = (fac or {}).get("influence", 20) * 0.3 + (fac or {}).get("resources", 10) * 0.2
    return base + intent.get("resources_spent", 0) + rng.random() * 25


def _odds(attacker: dict, defender: dict) -> str:
    """" (Captain Brand (CR 4), Lord Vane (CR 1/8) against The Blackfen Hag (CR 6).)" for the event text."""
    if not defender["units"]:
        return ""
    return f" ({', '.join(attacker['units'][:3]) or 'a handful'} against {', '.join(defender['units'][:3])}.)"


def arbiter(intents: list[dict], world: WorldView, *, day: int, seed: Any, round_no: int) -> ArbiterResult:
    from simulation.strength import Forces, force
    rng = rng_for(seed, round_no, "arbiter")
    forces = Forces(world)
    events: list[WorldEventDraft] = []
    by_target: dict[str, list[dict]] = {}
    for it in intents:
        if it.get("target_id"):
            by_target.setdefault(it["target_id"], []).append(it)
    party_id = (world.party or {}).get("@id")

    def faction_of(aid: str) -> str:
        d = world.get(aid) or {}
        return d.get("faction") or aid if d.get("@type") == "Character" else aid

    def witnesses(*ids: str | None, location: str | None = None, public: bool = True) -> list[str]:
        out = {i for i in ids if i and i in world.docs and i.split("/")[0] in ("Character", "Faction")}
        for i in list(out):
            if world.get(i) and world.get(i).get("faction"):
                out.add(world.get(i)["faction"])
        if public and location:
            out |= {c["@id"] for c in world.of_type("Character") if c.get("location") == location}
        return sorted(out - {party_id})

    handled: set[int] = set()
    for idx, it in enumerate(intents):
        if idx in handled:
            continue
        actor, action, target = it["agent_id"], it["action"], it.get("target_id")
        aname, tname = world.name(actor), world.name(target)
        tdoc = world.get(target) or {}
        loc = tdoc.get("location") or tdoc.get("headquarters") or (target if target and target.startswith("Location/") else None) \
            or (world.get(actor) or {}).get("location")
        visibility = it.get("public_or_secret", "public")
        if visibility == "secret" and rng.random() < 0.25:
            visibility = "leaked"
        base = dict(day=day, actor_ids=[actor], target_ids=[target] if target else [], location_id=loc,
                    visibility=visibility, caused_by=it.get("responding_to"), from_intents=[actor])
        rival = [o for j, o in enumerate(intents) if j != idx and o.get("target_id") == target
                 and o["action"] in OPPOSED.get(action, set())]
        ctl = world.controller_of(target) if target else None
        defender = rival[0]["agent_id"] if rival else (ctl if ctl and faction_of(ctl) != faction_of(actor) else None)
        goal = it.get("advances_goal_id")
        gp = {goal: 1} if goal else {}

        if action in ("seize", "steal", "sabotage", "attack") and target:
            # who fights: the actor's people against the holder's people there plus any creature in the place
            place = target if target.startswith("Location/") else loc
            atk_force = force(forces.side(actor, place))
            def_force = force(forces.side(defender, place) + forces.lair(place))
            atk = _power(world, it, rng) + atk_force["strength"]
            dfn = (_power(world, rival[0], rng) if rival else (
                _power(world, {"agent_id": defender, "resources_spent": 5}, rng) if defender else 0)) + def_force["strength"]
            success = atk >= dfn
            odds = _odds(atk_force, def_force)
            for j, o in enumerate(intents):
                if o in rival:
                    handled.add(j)
            spend = {faction_of(actor): -it.get("resources_spent", 0)}
            shifts = []
            if defender:
                shifts.append(RelationshipShift(from_id=defender, to_id=actor, delta=-20 if success else -10,
                                                reason=f"{aname} moved against {tname}"))
            if action == "seize":
                title = f"{aname} seizes {tname}" if success else f"{aname}'s grab for {tname} is repelled"
                events.append(WorldEventDraft(
                    title=title, event_type="conflict" if defender else "faction_move",
                    summary=(f"{aname} moved on {tname}" + (f" against {world.name(defender)}" if defender else "") +
                             (" and took control." if success else ", but was driven back.") + odds),
                    control_changes=[ControlChange(target_id=target, new_controller_id=faction_of(actor))] if success else [],
                    relationship_shifts=shifts, goal_progress={goal: 2} if (goal and success) else {},
                    resource_changes=spend, witnesses=witnesses(actor, defender, location=loc), **base))
            elif action == "steal":
                title = f"{tname} stolen from {world.name(ctl)}" if success else f"Theft of {tname} foiled"
                events.append(WorldEventDraft(
                    title=title, event_type="npc_action",
                    summary=(f"Agents of {aname} lifted {tname}." if success else
                             f"{aname}'s people were caught trying to lift {tname}."),
                    control_changes=[ControlChange(target_id=target, new_controller_id=actor, kind="OWNS")] if success else [],
                    relationship_shifts=shifts if (not success or visibility != "secret") else [],
                    goal_progress=gp if success else {}, resource_changes=spend,
                    witnesses=witnesses(actor, *([] if success and visibility == "secret" else [ctl]), public=False),
                    **{**base, "visibility": "public" if not success else base["visibility"]}))
            elif action == "sabotage":
                victim = faction_of(ctl or target)
                events.append(WorldEventDraft(
                    title=f"Sabotage at {tname}" if success else f"Sabotage plot at {tname} uncovered",
                    event_type="npc_action",
                    summary=(f"Someone working for {aname} sabotaged {tname}, costing {world.name(victim)} dearly."
                             if success else f"{world.name(defender or victim)} caught {aname}'s saboteurs at {tname}."),
                    resource_changes={**spend, victim: -10} if success else spend,
                    relationship_shifts=shifts if visibility != "secret" or not success else [],
                    goal_progress=gp if success else {}, witnesses=witnesses(actor, defender, location=loc, public=not success),
                    **{**base, "visibility": base["visibility"] if success else "public"}))
            else:  # attack
                killed = [target] if success and target.startswith("Character/") and rng.random() < 0.2 else []
                events.append(WorldEventDraft(
                    title=f"{aname} attacks {tname}", event_type="death" if killed else "conflict",
                    summary=f"{aname} struck at {tname}" + (", killing them." if killed else
                                                           (" and came off better." if success else " and was beaten back.")) + odds,
                    killed_ids=killed, relationship_shifts=shifts + [RelationshipShift(
                        from_id=target, to_id=actor, delta=-35, reason="violence")] if target in world.docs else shifts,
                    resource_changes=spend, witnesses=witnesses(actor, target, location=loc), **base))
            continue

        if action in ("negotiate", "ally", "trade", "bribe", "recruit") and target:
            tr = (world.relationship(target, actor) or {}).get("trust_score", 0)
            ok = tr > -40 or action == "bribe" or rng.random() < 0.3
            delta = {"ally": 20, "negotiate": 12, "trade": 8, "bribe": 15, "recruit": 15}[action]
            label = {"ally": "ally", "recruit": "recruited", "bribe": "bought"}.get(action)
            events.append(WorldEventDraft(
                title=f"{aname} {'courts' if ok else 'is rebuffed by'} {tname}",
                summary=(f"{aname} {action}s with {tname}" + (" — and finds them receptive." if ok else
                                                              ", but old grudges run too deep.")),
                event_type="npc_action",
                relationship_shifts=[
                    RelationshipShift(from_id=target, to_id=actor, delta=delta if ok else -5, label=label if ok else None,
                                      reason=action),
                    RelationshipShift(from_id=actor, to_id=target, delta=delta // 2 if ok else -5, reason=action),
                ] if target.split("/")[0] in ("Character", "Faction") else [],
                goal_progress=gp if ok else {}, resource_changes={faction_of(actor): -it.get("resources_spent", 0)},
                witnesses=witnesses(actor, target, location=loc, public=visibility != "secret"), **base))
            continue

        if action == "threaten" and target:
            events.append(WorldEventDraft(
                title=f"{aname} threatens {tname}",
                summary=f"{aname} sent a clear warning to {tname}: {it.get('reasoning', '')[:140]}",
                event_type="npc_action",
                relationship_shifts=[RelationshipShift(from_id=target, to_id=actor, delta=-15, reason="threatened"),
                                     RelationshipShift(from_id=actor, to_id=target, delta=-10, label="enemy",
                                                       reason="grievance")],
                new_desires=[NewDesire(from_id=target, kind="FEARS", target_id=actor, priority=0.55,
                                       reason=f"Threatened by {aname}")] if target.split("/")[0] in ("Character", "Faction") and target != party_id else [],
                new_goals=[NewGoal(owner_id=actor, title=f"Make {tname} pay", target_id=target, priority=0.7,
                                   description=f"Raised after {world.name(it.get('responding_to'))}.")]
                if it.get("responding_to") and not any(g.get("target") == target for g in world.goals_of(actor)) else [],
                witnesses=witnesses(actor, target, location=loc), **{**base, "visibility": "public"}))
            continue

        if action == "investigate":
            gains = []
            subject = target
            for e in world.of_type("Event"):
                if subject and (subject in e.get("actors", []) or subject in e.get("targets", [])) and e["in_world_start"] >= day - 14:
                    gains.append(KnowledgeGain(agent_id=actor, fact_id=e["@id"], kind="KNOWS", confidence=0.9))
            for c in world.of_type("ControlEdge"):
                if subject and c["from_id"] == subject:
                    gains.append(KnowledgeGain(agent_id=actor, fact_id=c["target_id"], kind="KNOWS", confidence=0.8))
            events.append(WorldEventDraft(
                title=f"{aname} makes inquiries about {tname or 'recent events'}",
                summary=f"{aname}'s people asked quiet questions about {tname or 'the town'}" +
                        (f" and learned {len(gains)} new things." if gains else ", but learned little."),
                event_type="npc_action", knowledge_gained=gains[:6], goal_progress=gp,
                witnesses=witnesses(actor, public=False), **base))
            continue

        if action == "spread_rumor" and target:
            events.append(WorldEventDraft(
                title=f"Whispers against {tname}", event_type="npc_action",
                summary=f"Unflattering stories about {tname} began circulating — started, quietly, by {aname}.",
                relationship_shifts=[RelationshipShift(from_id=target, to_id=actor, delta=-10, reason="slander")]
                if visibility == "leaked" else [],
                witnesses=witnesses(actor, public=False), goal_progress=gp, **{**base, "visibility": "leaked"}))
            continue

        if action in ("fortify", "hide"):
            events.append(WorldEventDraft(
                title=f"{aname} {'fortifies' if action == 'fortify' else 'hides'} {tname or 'their holdings'}",
                summary=(f"{aname} doubled the guard on {tname}." if action == "fortify"
                         else f"{aname} quietly moved {tname} somewhere safer."),
                event_type="faction_move" if action == "fortify" else "npc_action",
                resource_changes={faction_of(actor): -it.get("resources_spent", 0)}, goal_progress=gp,
                witnesses=witnesses(actor, location=loc, public=action == "fortify"), **base))
            continue

        if action in ("trade",):
            events.append(WorldEventDraft(
                title=f"{aname} raises coin", summary=f"{aname} called in favours and sold what could be spared.",
                event_type="npc_action", resource_changes={faction_of(actor): +6},
                witnesses=witnesses(actor, public=False), **{**base, "visibility": "secret"}))
            continue
        # observe / flee / unknown: no world change
    return ArbiterResult(events=events)


# =========================================================================== rumors
DISTORT = [
    (r"\bseized\b|\bseizes\b", "burned and seized"), (r"\bthreatens\b", "swore a blood-oath against"),
    (r"\bcourts\b", "is secretly in bed with"), (r"\bsabotage", "a bloody massacre"),
    (r"\bsteals\b", "stole a king's ransom, including"), (r"\bbribes\b", "bought, body and soul,"),
    (r"\bcrosses\b", "drew steel on"), (r"\bhelps\b", "swore an oath to"),
]
SPEAKERS = ["a one-eyed net-mender", "a drunk dockhand", "a gossiping fishwife", "a nervous customs clerk",
            "a travelling tinker", "a watchman off duty"]


def _sentence_tail(text: str) -> str:
    """Lower-case a sentence's first word only when it's a plain word ("The", "Someone"), never a name."""
    first = text.split(" ", 1)[0]
    return text[0].lower() + text[1:] if first in ("The", "A", "An", "Someone", "Unflattering", "Agents", "Off-screen,") else text


def rumors(events: list[dict], world: WorldView, *, seed: Any, round_no: int) -> RumorBatch:
    rng = rng_for(seed, round_no, "rumors")
    taverns = [l for l in world.of_type("Location") if l.get("location_type") == "tavern"]
    settlements = [l for l in world.of_type("Location") if l.get("location_type") in ("town", "district")]
    keepers = {c.get("location"): c["name"] for c in world.of_type("Character")}
    out: list[RumorDraft] = []
    agents = [a for a in world.agents()]
    for ev in events:
        if ev.get("visibility") == "secret":
            continue
        venues = taverns[:1] + ([world.get(ev.get("location"))] if world.get(ev.get("location")) and
                                world.get(ev["location"]).get("location_type") in ("town", "district", "tavern") else [])
        if not venues:
            venues = (taverns or settlements)[:1]
        for venue in {v["@id"]: v for v in venues if v}.values():
            roll = rng.random()
            accuracy = "True" if roll < 0.5 else "Exaggerated" if roll < 0.8 else "False"
            # Party-action summaries are written from the DM's seat ("they..."); gossip uses the headline.
            summary = (ev["name"] + ".") if ev.get("event_type") == "party_action" else (ev.get("summary") or ev["name"])
            actors = [world.name(a) for a in ev.get("actors", [])]
            if accuracy == "True":
                text = summary
            elif accuracy == "Exaggerated":
                text = summary
                for pat, rep in DISTORT:
                    text = re.sub(pat, rep, text)
                if text == summary:
                    text = summary.rstrip(".") + " — and they say it's only the beginning."
            else:
                involved = set(actors) | {world.name(t) for t in ev.get("targets", [])}
                wrong = rng.choice([a["name"] for a in agents if a["name"] not in involved] or ["the crown"])
                text = summary
                for a in actors:
                    text = text.replace(a, wrong)
                if text == summary:
                    text = f"{wrong} is behind it: " + summary[0].lower() + summary[1:]
            speaker = keepers.get(venue["@id"]) or rng.choice(SPEAKERS)
            opener = rng.choice(["Did you hear?", "Keep this under your hat —", "Mark my words:",
                                 "Swear on the tide, it's true:", "Word on the docks is"])
            tail = text if opener.endswith("?") else _sentence_tail(text)
            out.append(RumorDraft(about_event=ev["@id"], location_id=venue["@id"], accuracy=accuracy,
                                  text=text, dialogue=f"\"{opener} {tail}\"", speaker=speaker))
    return RumorBatch(rumors=out)

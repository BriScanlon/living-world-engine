"""LangGraph state machine: fork -> apply session -> [activate -> act -> arbitrate -> rumors]* -> finalize.

Each simulated *round* covers ``ROUND_LENGTH_DAYS`` in-world days of the forecast window, so
consequences ripple: events from round 1 become knowledge that wakes and motivates agents in
round 2. Every node commits to the forecast branch, so the branch history doubles as an audit
log (transaction time) of the simulation.
"""
from __future__ import annotations

import json
import logging
import math
import threading
import traceback
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import wraps
from operator import add
from typing import Annotated, Any, Callable, TypedDict

from langgraph.graph import END, START, StateGraph

from atlas import travel
from config import Settings, get_settings
from graph import builders as b
from graph.store import GraphStore, get_store, to_branch_id
from graph.world import WorldView
from llm import mock
from simulation import prompts
from simulation.models import (
    AgentIntent,
    AgentIntentLLM,
    ArbiterResult,
    ControlChange,
    ForecastRequest,
    RelationshipShift,
    RumorBatch,
    SessionDelta,
    WorldEventDraft,
)
from simulation.mutations import AGENTISH, Mutator
from simulation.runs import Run, registry

log = logging.getLogger(__name__)

STEPS = ["fork_canon", "apply_session_delta", "select_active_agents", "run_agent_turns",
         "world_arbiter", "propagate_rumors", "finalize"]

PARTY_VERBS = {"steal": "steals", "kill": "kills", "help": "helps", "harm": "crosses", "bribe": "bribes",
               "discover": "uncovers the truth about", "travel": "travels to", "other": "deals with"}


class SimState(TypedDict, total=False):
    run_id: str
    request: dict[str, Any]
    branch: str
    base_commit: str | None
    session_label: str
    seed: Any
    rounds: list[list[int]]
    round: int
    affected_ids: list[str]
    party_reports: list[dict[str, Any]]
    active_agents: list[dict[str, Any]]
    intents: list[dict[str, Any]]
    round_event_ids: list[str]
    activations: Annotated[list, add]
    all_intents: Annotated[list, add]
    event_reports: Annotated[list, add]
    rumor_ids: Annotated[list, add]
    violations: Annotated[list, add]


@dataclass
class Ctx:
    store: GraphStore
    settings: Settings

    @property
    def mock(self) -> bool:
        return self.settings.mock_llm

    def llm(self):
        from llm.client import get_llm
        return get_llm()


def _ctx(config) -> Ctx:
    return config["configurable"]["ctx"]


def node(name: str) -> Callable:
    """Wrap a node with start/done progress events."""
    def deco(fn: Callable) -> Callable:
        @wraps(fn)
        def inner(state: SimState, config) -> dict[str, Any]:
            run_id = state["run_id"]
            rnd = state.get("round", 0)
            label = name if name in ("fork_canon", "apply_session_delta", "finalize") else f"{name} (round {rnd + 1})"
            registry.emit(run_id, name, "start", f"{label} …", {"round": rnd})
            out = fn(state, config)
            registry.emit(run_id, name, "done", out.pop("_message", label), {"round": rnd, **out.pop("_data", {})})
            return out
        return inner
    return deco


# =========================================================================== nodes
@node("fork_canon")
def fork_canon(state: SimState, config) -> dict[str, Any]:
    c = _ctx(config)
    base = state["request"]["base_branch"]
    base_commit = c.store.head(base)
    c.store.create_branch(state["branch"], origin=base)
    return {"base_commit": base_commit,
            "_message": f"Forked {state['branch']} from {base} @ {str(base_commit)[:8]}",
            "_data": {"base_commit": base_commit}}


@node("apply_session_delta")
def apply_session_delta(state: SimState, config) -> dict[str, Any]:
    c = _ctx(config)
    req = ForecastRequest(**state["request"])
    world = WorldView.load(c.store, state["branch"], day=req.end_day)
    party = world.party
    if party is None:
        raise RuntimeError("World has no party faction (a Faction with is_party=true)")
    pid = party["@id"]

    if c.mock:
        delta = mock.session_delta(req.session_summary, world)
    else:
        delta = c.llm().structured(prompts.SESSION_DELTA_SYSTEM,
                                   prompts.session_delta_user(req.session_summary, world, req.start_day, req.end_day),
                                   SessionDelta, temperature=0.1)

    m = Mutator(world, state["session_label"], (state["seed"], "delta"))
    n = len(delta.party_actions)
    affected: set[str] = set()
    reports = []
    for i, act in enumerate(delta.party_actions):
        targets = [t for t in act.target_ids if t in world.docs and t != pid]
        if not targets:
            continue
        day = req.start_day + ((req.end_day - req.start_day) * i // (n - 1) if n > 1 else req.end_day - req.start_day)
        loc = act.location_id if act.location_id in world.docs else None
        item = act.item_transferred_id if act.item_transferred_id in world.docs else None
        prev_owner = world.controller_of(item) if item else None
        if item:
            title = f"{party['name']} steals {world.name(item)}" + (f" from {world.name(prev_owner)}" if prev_owner else "")
        else:
            people = [t for t in targets if t.startswith(AGENTISH)] or targets
            title = f"{party['name']} {PARTY_VERBS.get(act.action_type, 'deals with')} " + \
                    " and ".join(world.name(t) for t in people[:3])
        ev = b.event(title, act.summary,
                     day=day, event_type="party_action", actors=[pid], targets=targets, location=loc,
                     visibility=act.visibility, session=state["session_label"], salt=(state["seed"], i))
        evt = ev["@id"]
        m.put(ev)
        rep = {"event": evt, "title": ev["name"], "summary": act.summary, "day": day, "targets": targets,
               "visibility": act.visibility, "action_type": act.action_type, "shifts": []}

        m.learn(pid, evt, day, evt)
        participants = [t for t in targets if t.startswith(AGENTISH)
                        and act.action_type in ("help", "bribe", "harm", "kill", "other", "discover")]
        victims: list[str] = []
        if item:
            if prev_owner and prev_owner != pid:
                victims.append(prev_owner)
            m.set_control(item, pid, "OWNS", day, evt)
            affected.add(item)
        for k in act.killed_ids:
            d = m.cur(k)
            if d and d.get("in_world_end") is None:
                m.put(b.end(d, day, by_event=evt))

        for who in dict.fromkeys(participants + victims):
            knows_it = act.visibility == "public" or who in participants
            if knows_it:
                m.learn(who, evt, day, evt)
                if act.trust_delta:
                    r = m.shift_trust(who, pid, act.trust_delta, day, evt, note=act.summary)
                    if r:
                        rep["shifts"].append(r)
                    fac = (world.get(who) or {}).get("faction")
                    if fac and act.visibility == "public":
                        r = m.shift_trust(fac, pid, act.trust_delta // 2, day, evt, note=f"Via {world.name(who)}")
                        if r:
                            rep["shifts"].append(r)
            else:
                # Secret act against you: you notice the loss, but only *suspect* who did it.
                m.learn(who, evt, day, evt, kind="BELIEVES", conf=0.5)
            fac = (world.get(who) or {}).get("faction")
            if fac:
                m.learn(fac, evt, day, evt, kind="KNOWS" if knows_it else "BELIEVES", conf=1.0 if knows_it else 0.5)
        if act.visibility == "public" and loc:
            for ch in world.of_type("Character"):
                if ch.get("location") == loc:
                    m.learn(ch["@id"], evt, day, evt)
        affected |= set(targets) | set(victims) | ({loc} if loc else set())
        reports.append(rep)

    c.store.upsert(m.docs(), state["branch"],
                   f"[forecast] apply_session_delta: {len(reports)} party actions from {state['session_label']}")
    return {"affected_ids": sorted(affected), "party_reports": reports, "round": 0,
            "_message": f"Applied {len(reports)} party actions; {len(affected)} nodes directly affected",
            "_data": {"party_actions": [r["title"] for r in reports]}}


@node("select_active_agents")
def select_active_agents(state: SimState, config) -> dict[str, Any]:
    c = _ctx(config)
    r = state.get("round", 0)
    lo, hi = state["rounds"][r]
    world = WorldView.load(c.store, state["branch"], day=hi)
    seeds = set(state.get("affected_ids", []))
    if r > 0:
        for rep in state.get("event_reports", []):
            if rep.get("round") == r - 1:
                ev = world.get(rep["event"]) or {}
                seeds |= set(ev.get("actors", [])) | set(ev.get("targets", []))
    reach = world.within_hops(seeds, c.settings.activation_hops)
    chosen: dict[str, dict[str, Any]] = {}
    for a in world.agents():
        if a["@id"] in reach:
            dist, path = reach[a["@id"]]
            chosen[a["@id"]] = {"id": a["@id"], "name": a["name"], "reason": "radius", "distance": dist,
                                "path": [world.name(p) for p in path], "goal_ticks": {}}
    for g in world.of_type("Goal"):
        owner = world.get(g["owner"])
        if g.get("status") != "active" or not owner or owner.get("is_party"):
            continue
        if g["priority"] < c.settings.goal_priority_threshold:
            continue
        every = max(1, g["ticks_every_days"])
        ticks = hi // every - lo // every
        if ticks <= 0:
            continue
        entry = chosen.setdefault(owner["@id"], {"id": owner["@id"], "name": owner["name"], "reason": "goal",
                                                 "distance": None, "path": [], "goal_ticks": {}})
        if entry["reason"] == "radius":
            entry["reason"] = "radius+goal"
        entry["goal_ticks"][g["@id"]] = ticks
    agents = sorted(chosen.values(), key=lambda a: (a["distance"] is None, a["distance"] or 0, a["name"]))
    for a in agents:
        a["round"] = r
    return {"active_agents": agents, "activations": agents,
            "_message": f"Woke {len(agents)} agents for days {lo}–{hi}",
            "_data": {"agents": [f"{a['name']} ({a['reason']})" for a in agents]}}


def _validate_intent(raw: AgentIntentLLM, agent_id: str, world: WorldView, resources: int) -> tuple[AgentIntent, str | None]:
    known = world.known_ids(agent_id)
    violation = None
    data = raw.model_dump()
    if data.get("target_id") and data["target_id"] not in known:
        violation = f"{world.name(agent_id)} targeted unknown node {data['target_id']}"
        data.update(action="observe", target_id=None, resources_spent=0,
                    reasoning=data["reasoning"] + " [arbiter: target outside agent's knowledge — downgraded to observe]")
    if data.get("responding_to") and data["responding_to"] not in known:
        data["responding_to"] = None
    if data.get("advances_goal_id") and data["advances_goal_id"] not in {g["@id"] for g in world.goals_of(agent_id)}:
        data["advances_goal_id"] = None
    data["resources_spent"] = max(0, min(int(data.get("resources_spent") or 0), resources))
    return AgentIntent(agent_id=agent_id, **data), violation


@node("run_agent_turns")
def run_agent_turns(state: SimState, config) -> dict[str, Any]:
    c = _ctx(config)
    r = state["round"]
    lo, hi = state["rounds"][r]
    req = state["request"]
    # React to anything that happened (or was learned) since the previous round began
    since = req["start_day"] if r == 0 else state["rounds"][r - 1][0] + 1
    world = WorldView.load(c.store, state["branch"], day=hi)

    def resources_of(aid: str) -> int:
        a = world.get(aid) or {}
        fac = world.get(a.get("faction")) if a.get("@type") == "Character" else a
        return int((fac or {}).get("resources") or 0)

    def turn(agent: dict[str, Any]) -> tuple[AgentIntent, str | None]:
        aid = agent["id"]
        ctx = world.agent_context(aid)
        if c.mock:
            history = [(i["action"], i.get("target_id")) for i in state.get("all_intents", []) if i["agent_id"] == aid]
            raw = mock.agent_intent(aid, world, ctx, day=hi, since_day=since, seed=state["seed"], round_no=r,
                                    history=history)
        else:
            raw = c.llm().structured(prompts.AGENT_SYSTEM, prompts.agent_user(ctx, hi, (lo, hi)), AgentIntentLLM)
        return _validate_intent(raw, aid, world, resources_of(aid))

    agents = [a for a in state.get("active_agents", []) if a["id"] in world.docs]
    if c.mock:
        results = [turn(a) for a in agents]
    else:
        with ThreadPoolExecutor(max_workers=max(1, c.settings.llm_concurrency)) as pool:
            results = list(pool.map(turn, agents))
    intents = [{**i.model_dump(), "round": r, "agent_name": world.name(i.agent_id),
                "target_name": world.name(i.target_id) if i.target_id else None} for i, _ in results]
    violations = [{"round": r, "message": v} for _, v in results if v]
    return {"intents": intents, "all_intents": intents, "violations": violations,
            "_message": f"{len(intents)} agents declared intents" + (f" ({len(violations)} epistemic violations blocked)" if violations else ""),
            "_data": {"intents": [f"{i['agent_name']}: {i['action']} → {i['target_name'] or '—'}" for i in intents]}}


@node("world_arbiter")
def world_arbiter(state: SimState, config) -> dict[str, Any]:
    c = _ctx(config)
    r = state["round"]
    lo, hi = state["rounds"][r]
    world = WorldView.load(c.store, state["branch"], day=hi)
    intents = state.get("intents", [])
    if not intents:
        result = ArbiterResult(events=[])
    elif c.mock:
        result = mock.arbiter(intents, world, day=hi, seed=state["seed"], round_no=r)
    else:
        payload = [{k: v for k, v in i.items() if k not in ("round",)} for i in intents]
        result = c.llm().structured(prompts.ARBITER_SYSTEM, prompts.arbiter_user(payload, world, hi), ArbiterResult,
                                    temperature=0.4)

    drafts = list(result.events)
    # Time-driven goal clocks (the faction keeps working off-screen)
    for a in state.get("active_agents", []):
        for gid, ticks in a.get("goal_ticks", {}).items():
            g = world.get(gid)
            if not g:
                continue
            drafts.append(WorldEventDraft(
                title=f"{world.name(g['owner'])} advances '{g['name']}'",
                summary=f"Off-screen, {world.name(g['owner'])} quietly moves {g['name'].lower()} forward.",
                event_type="goal_progress", day=hi, actor_ids=[g["owner"]],
                target_ids=[g["target"]] if g.get("target") else [], visibility="secret",
                goal_progress={gid: ticks}, witnesses=[g["owner"]]))

    span = max(1, hi - lo)
    for i, d in enumerate(drafts):
        d.day = min(hi, max(lo + 1, lo + 1 + (i * span) // max(1, len(drafts))))
    # Nobody acts on a far-off place before they could get there: set out at the round's start, arrive, then act.
    # Factions on the move march at army pace; everyone else walks. Late arrivals land after this round.
    trav = travel.for_world(world)
    journeys: dict[int, dict[str, Any]] = {}
    for i, d in enumerate(drafts):
        if d.event_type in ("goal_progress", "goal_achieved") or not d.actor_ids:
            continue
        dest = d.location_id if world.get(d.location_id) else next((t for t in d.target_ids if world.get(t)), None)
        actor = world.get(d.actor_ids[0]) or {}
        pace = "army" if actor.get("@type") == "Faction" and d.event_type in ("conflict", "faction_move") else "foot"
        days = trav.days(d.actor_ids[0], dest, pace) if dest else 0
        if days and lo + days > d.day:
            d.day = lo + days
            d.summary = f"{d.summary.rstrip()} ({'A march' if pace == 'army' else 'A journey'} of {days} day{'s' if days != 1 else ''}.)"
            journeys[i] = {"days": days, "pace": pace}
    m = Mutator(world, state["session_label"], (state["seed"], "round", r))
    reports = []
    known_events = {e["@id"] for e in world.of_type("Event")}
    for i, d in enumerate(drafts):
        rep = m.apply_event(d, i, caused_by_ok=known_events)
        reports.append({**rep, "round": r, "day": d.day, "summary": d.summary, "event_type": d.event_type,
                        "visibility": d.visibility, "caused_by": d.caused_by, "actors": d.actor_ids,
                        "targets": d.target_ids, "travel": journeys.get(i)})
    # Completed clocks trigger concrete consequences
    for rep in list(reports):
        for gr in rep["goals"]:
            if gr["status"] != "achieved":
                continue
            g = m.cur(gr["goal"])
            tgt = g.get("target")
            cc = []
            shifts = []
            if tgt and tgt.startswith(("Location/", "Province/", "Item/")):
                cc = [ControlChange(target_id=tgt, new_controller_id=g["owner"],
                                    kind="OWNS" if tgt.startswith("Item/") else "CONTROLS")]
            elif tgt and tgt.startswith(AGENTISH):
                shifts = [RelationshipShift(from_id=tgt, to_id=g["owner"], delta=-25, reason=g["name"])]
            fin = WorldEventDraft(
                title=f"{world.name(g['owner'])} achieves '{g['name']}'",
                summary=g.get("description") or g["name"], event_type="goal_achieved", day=rep["day"],
                actor_ids=[g["owner"]], target_ids=[tgt] if tgt else [], visibility="public",
                caused_by=rep["event"], control_changes=cc, relationship_shifts=shifts,
                witnesses=[g["owner"]] + ([tgt] if tgt and tgt.startswith(AGENTISH) else []))
            frep = m.apply_event(fin, 1000 + len(reports), caused_by_ok=known_events | {rep["event"]})
            reports.append({**frep, "round": r, "day": fin.day, "summary": fin.summary, "event_type": fin.event_type,
                            "visibility": "public", "caused_by": rep["event"], "actors": fin.actor_ids,
                            "targets": fin.target_ids})

    c.store.upsert(m.docs(), state["branch"],
                   f"[forecast] world_arbiter round {r + 1} (days {lo}–{hi}): {len(intents)} intents → {len(reports)} events")
    return {"event_reports": reports, "round_event_ids": [x["event"] for x in reports],
            "_message": f"Resolved {len(intents)} intents into {len(reports)} world events",
            "_data": {"events": [x["title"] for x in reports]}}


@node("propagate_rumors")
def propagate_rumors(state: SimState, config) -> dict[str, Any]:
    c = _ctx(config)
    r = state["round"]
    lo, hi = state["rounds"][r]
    world = WorldView.load(c.store, state["branch"], day=hi)
    ids = list(state.get("round_event_ids", []))
    if r == 0:
        ids = [p["event"] for p in state.get("party_reports", [])] + ids
    events = [world.get(i) for i in ids if world.get(i)]
    events = [e for e in events if e.get("visibility") in ("public", "leaked")]
    if not events:
        batch = RumorBatch(rumors=[])
    elif c.mock:
        batch = mock.rumors(events, world, seed=state["seed"], round_no=r)
    else:
        batch = c.llm().structured(prompts.RUMOR_SYSTEM, prompts.rumor_user(events, world), RumorBatch)

    m = Mutator(world, state["session_label"], (state["seed"], "rumor", r))
    trav = travel.for_world(world)
    out = []
    for i, rd in enumerate(batch.rumors):
        ev = world.get(rd.about_event)
        venue = world.get(rd.location_id)
        if not ev or not venue or venue["@type"] != "Location":
            continue
        # News travels with merchants and riders: a far tavern hears of it days later (maybe after this round)
        origin = ev.get("location") or next(iter(ev.get("targets") or ev.get("actors") or []), None)
        day = (ev.get("in_world_start") or lo) + max(1, trav.days(origin, venue["@id"], "rumor"))
        doc = b.rumor(rd.text, rd.dialogue, location=venue["@id"], accuracy=rd.accuracy, day=day,
                      about_event=ev["@id"], speaker=rd.speaker, session=state["session_label"],
                      salt=(state["seed"], r, i))
        doc["source_event"] = ev["@id"]
        m.put(doc)
        out.append(doc["@id"])
        # Locals now believe the rumor — and therefore believe the event happened (for next round)
        for ch in world.of_type("Character"):
            if ch.get("location") == venue["@id"] and ch["@id"] not in ev.get("actors", []):
                m.learn(ch["@id"], doc["@id"], day, ev["@id"], kind="BELIEVES", conf=0.6)
                m.learn(ch["@id"], ev["@id"], day, ev["@id"], kind="BELIEVES",
                        conf=0.7 if rd.accuracy == "True" else 0.4)
    c.store.upsert(m.docs(), state["branch"], f"[forecast] propagate_rumors round {r + 1}: {len(out)} rumors")
    return {"rumor_ids": out, "round": r + 1,
            "_message": f"{len(out)} rumors spread to taverns and settlements",
            "_data": {"rumors": len(out)}}


def _more_rounds(state: SimState) -> str:
    return "select_active_agents" if state.get("round", 0) < len(state["rounds"]) else "finalize"


@node("finalize")
def finalize(state: SimState, config) -> dict[str, Any]:
    c = _ctx(config)
    req = state["request"]
    report = {
        "request": req,
        "party_actions": state.get("party_reports", []),
        "activations": state.get("activations", []),
        "intents": state.get("all_intents", []),
        "events": state.get("event_reports", []),
        "rumors": state.get("rumor_ids", []),
        "violations": state.get("violations", []),
        "rounds": state["rounds"],
    }
    run_doc = {
        "@type": "ForecastRun", "@id": f"ForecastRun/{to_branch_id(state['branch'])}",
        "uid": to_branch_id(state["branch"]), "branch": state["branch"], "base_branch": req["base_branch"],
        "base_commit": state.get("base_commit"), "session_label": state["session_label"],
        "session_number": req["session_number"], "start_day": req["start_day"],
        "end_day": req["end_day"] + req.get("forecast_days", 7), "summary": req["session_summary"],
        "created_at": datetime.now(timezone.utc).isoformat(), "status": "complete",
        "mock_llm": c.mock, "report_json": json.dumps(report, default=str),
    }
    c.store.upsert([run_doc], state["branch"], "[forecast] finalize: forecast report")
    return {"_message": f"Forecast complete: {len(report['events'])} off-screen events, {len(report['rumors'])} rumors",
            "_data": {"events": len(report["events"]), "rumors": len(report["rumors"])}}


# =========================================================================== graph
def build_graph():
    g = StateGraph(SimState)
    for fn in (fork_canon, apply_session_delta, select_active_agents, run_agent_turns, world_arbiter,
               propagate_rumors, finalize):
        g.add_node(fn.__name__, fn)
    g.add_edge(START, "fork_canon")
    g.add_edge("fork_canon", "apply_session_delta")
    g.add_edge("apply_session_delta", "select_active_agents")
    g.add_edge("select_active_agents", "run_agent_turns")
    g.add_edge("run_agent_turns", "world_arbiter")
    g.add_edge("world_arbiter", "propagate_rumors")
    g.add_conditional_edges("propagate_rumors", _more_rounds, ["select_active_agents", "finalize"])
    g.add_edge("finalize", END)
    return g.compile()


GRAPH = build_graph()


def plan_rounds(end_day: int, forecast_days: int, s: Settings) -> list[list[int]]:
    n = max(1, min(s.max_rounds, math.ceil(forecast_days / max(1, s.round_length_days))))
    step = forecast_days / n
    bounds = [end_day + round(step * i) for i in range(n + 1)]
    return [[bounds[i], bounds[i + 1]] for i in range(n)]


def branch_name(session_number: int, now: datetime | None = None) -> str:
    now = now or datetime.now(timezone.utc)
    return f"forecast/session-{session_number}-{now.strftime('%Y%m%dt%H%M%S')}"


def run_sync(run: Run, store: GraphStore | None = None, settings: Settings | None = None) -> dict[str, Any]:
    s = settings or get_settings()
    store = store or get_store()
    req = ForecastRequest(**run.request)
    state: SimState = {
        "run_id": run.id, "request": req.model_dump(), "branch": run.branch,
        "session_label": f"forecast/session-{req.session_number}",
        "seed": req.seed if req.seed is not None else req.session_number,
        "rounds": plan_rounds(req.end_day, req.forecast_days, s), "round": 0,
    }
    run.status = "running"
    try:
        final = GRAPH.invoke(state, {"configurable": {"ctx": Ctx(store, s)}, "recursion_limit": 200})
        run.status = "complete"
        run.result = {"branch": run.branch, "events": len(final.get("event_reports", [])),
                      "rumors": len(final.get("rumor_ids", []))}
        registry.emit(run.id, "complete", "complete", "Forecast ready", run.result)
        return final
    except Exception as e:  # surface to UI, keep branch for inspection
        log.exception("Forecast %s failed", run.id)
        run.status = "failed"
        run.error = f"{type(e).__name__}: {e}"
        registry.emit(run.id, "error", "error", run.error, {"trace": traceback.format_exc()[-2000:]})
        raise


def start_forecast(req: ForecastRequest) -> Run:
    run = registry.create(branch_name(req.session_number), req.model_dump())
    store = get_store().bound()  # keep writing to this campaign even if the user switches
    t = threading.Thread(target=lambda: _safe(run, store), name=f"forecast-{run.id}", daemon=True)
    t.start()
    return run


def _safe(run: Run, store: GraphStore | None = None) -> None:
    try:
        run_sync(run, store)
    except Exception:
        pass

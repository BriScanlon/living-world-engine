"""The 4-part DM briefing: a semantic diff of ``base`` (canon) vs a forecast branch.

1. Direct ripple effects   — Party action -> affected node -> NPC reaction chains
2. Off-screen events        — chronological log of autonomous moves
3. Relationship & motivation shifts — trust before/after, goals, desires, control, knowledge
4. Tavern rumors & hooks    — filterable by location / accuracy
"""
from __future__ import annotations

import json
from collections import defaultdict
from typing import Any

from graph.store import GraphStore, to_branch_id
from graph.world import WorldView

IGNORE_FIELDS = {"@id", "@type"}


def _strip(d: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in d.items() if k not in IGNORE_FIELDS}


def load_run(world: WorldView, branch: str) -> dict[str, Any] | None:
    doc = world.all.get(f"ForecastRun/{to_branch_id(branch)}")
    if not doc:
        return None
    out = dict(doc)
    out["report"] = json.loads(doc.get("report_json") or "{}")
    out.pop("report_json", None)
    return out


def build_briefing(store: GraphStore, base: str, compare: str) -> dict[str, Any]:
    bw = WorldView(store.all_documents(base))
    cw = WorldView(store.all_documents(compare))
    run = load_run(cw, compare)
    report = (run or {}).get("report", {})
    name = cw.name

    added = [cw.all[i] for i in cw.all.keys() - bw.all.keys()]
    changed = [cw.all[i] for i in cw.all.keys() & bw.all.keys() if _strip(cw.all[i]) != _strip(bw.all[i])]
    removed = [bw.all[i] for i in bw.all.keys() - cw.all.keys()]

    intents_by_event: dict[tuple[str, str | None], dict] = {}
    for it in report.get("intents", []):
        intents_by_event[(it["agent_id"], it.get("responding_to"))] = it

    def reasoning_for(ev: dict) -> str | None:
        for a in ev.get("actors", []):
            it = intents_by_event.get((a, ev.get("caused_by")))
            if it:
                return it.get("reasoning")
        for it in report.get("intents", []):
            if it["agent_id"] in ev.get("actors", []) and it.get("target_id") in ev.get("targets", []):
                return it.get("reasoning")
        return None

    def ev_brief(ev: dict) -> dict[str, Any]:
        return {
            "id": ev["@id"], "title": ev["name"], "summary": ev.get("summary"), "day": ev.get("in_world_start"),
            "event_type": ev.get("event_type"), "visibility": ev.get("visibility"),
            "actors": [{"id": a, "name": name(a)} for a in ev.get("actors", [])],
            "targets": [{"id": t, "name": name(t)} for t in ev.get("targets", [])],
            "location": {"id": ev["location"], "name": name(ev["location"])} if ev.get("location") else None,
            "caused_by": {"id": ev["caused_by"], "title": name(ev["caused_by"])} if ev.get("caused_by") else None,
            "reasoning": reasoning_for(ev),
        }

    new_events = sorted((d for d in added if d["@type"] == "Event"),
                        key=lambda d: (d.get("in_world_start") or 0, d["name"]))
    children: dict[str, list[dict]] = defaultdict(list)
    for ev in new_events:
        if ev.get("caused_by"):
            children[ev["caused_by"]].append(ev)

    def tree(ev: dict, depth: int = 0) -> dict[str, Any]:
        node = ev_brief(ev)
        node["reactions"] = [tree(c, depth + 1) for c in children.get(ev["@id"], [])] if depth < 4 else []
        return node

    # ---------------------------------------------------------------- 1. ripples
    activations = report.get("activations", [])
    ripples = []
    for pa in [e for e in new_events if e.get("event_type") == "party_action"]:
        node = tree(pa)
        targets = set(pa.get("targets", [])) | ({pa["location"]} if pa.get("location") else set())
        woken = [a for a in activations if a.get("round") == 0 and a.get("path") and
                 any(name(t) == a["path"][0] for t in targets)]
        node["affected"] = [{"id": t, "name": name(t), "type": t.split("/")[0]} for t in sorted(targets)]
        node["woken"] = [{"id": a["id"], "name": a["name"], "distance": a["distance"], "path": a["path"]}
                         for a in woken]
        ripples.append(node)

    # ---------------------------------------------------------------- 2. off-screen log
    offscreen = [ev_brief(e) for e in new_events if e.get("event_type") != "party_action"]

    # ---------------------------------------------------------------- 3. shifts
    def active_rels(w: WorldView) -> dict[tuple[str, str], dict]:
        return {(e["from_id"], e["to_id"]): e for e in w.all.values()
                if e["@type"] == "RelationshipEdge" and e.get("in_world_end") is None}

    br, cr = active_rels(bw), active_rels(cw)
    reasons: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for e in cw.all.values():
        if e["@type"] == "RelationshipEdge" and e["@id"] not in bw.all and e.get("source_event"):
            reasons[(e["from_id"], e["to_id"])].append({
                "day": e.get("in_world_start"), "trust": e["trust_score"], "event": e["source_event"],
                "event_title": name(e["source_event"]), "note": e.get("note")})
    rel_shifts = []
    for pair in sorted(set(br) | set(cr)):
        before, after = br.get(pair), cr.get(pair)
        bt = before["trust_score"] if before else None
        at = after["trust_score"] if after else None
        if bt == at and (before or {}).get("label") == (after or {}).get("label"):
            continue
        rel_shifts.append({
            "from": {"id": pair[0], "name": name(pair[0])}, "to": {"id": pair[1], "name": name(pair[1])},
            "before": bt, "after": at, "delta": (at or 0) - (bt or 0),
            "label_before": (before or {}).get("label"), "label_after": (after or {}).get("label"),
            "history": sorted(reasons.get(pair, []), key=lambda r: r["day"] or 0),
        })
    rel_shifts.sort(key=lambda r: -abs(r["delta"]))

    goals = []
    for g in cw.of_type("Goal") + [cw.all[i] for i in cw.all if cw.all[i]["@type"] == "Goal" and i not in cw.docs]:
        old = bw.all.get(g["@id"])
        if old and _strip(old) == _strip(g):
            continue
        goals.append({
            "id": g["@id"], "name": g["name"], "owner": {"id": g["owner"], "name": name(g["owner"])},
            "is_new": old is None, "clock_before": old["clock_progress"] if old else None,
            "clock_after": g["clock_progress"], "segments": g["clock_segments"],
            "status_before": old.get("status") if old else None, "status_after": g.get("status"),
            "target": {"id": g["target"], "name": name(g["target"])} if g.get("target") else None,
            "description": g.get("description"),
        })
    goals = list({g["id"]: g for g in goals}.values())

    desires = []
    for d in cw.all.values():
        if d["@type"] != "DesireEdge":
            continue
        old = bw.all.get(d["@id"])
        if old is None or (old.get("in_world_end") is None and d.get("in_world_end") is not None):
            desires.append({"id": d["@id"], "agent": {"id": d["from_id"], "name": name(d["from_id"])},
                            "kind": d["kind"], "target": name(d.get("target_id")) if d.get("target_id") else d.get("target_text"),
                            "priority": d.get("priority"), "change": "added" if old is None else "ended",
                            "note": d.get("note")})

    controls = []
    for c in cw.all.values():
        if c["@type"] != "ControlEdge":
            continue
        old = bw.all.get(c["@id"])
        if old is None:
            controls.append({"change": "gained", "holder": name(c["from_id"]), "kind": c["kind"],
                             "target": name(c["target_id"]), "day": c.get("in_world_start"),
                             "event": name(c.get("source_event")) if c.get("source_event") else None})
        elif old.get("in_world_end") is None and c.get("in_world_end") is not None:
            controls.append({"change": "lost", "holder": name(c["from_id"]), "kind": c["kind"],
                             "target": name(c["target_id"]), "day": c.get("in_world_end"),
                             "event": name(c.get("ended_by_event")) if c.get("ended_by_event") else None})
    controls.sort(key=lambda c: (c["day"] or 0))

    knowledge: dict[str, list[dict]] = defaultdict(list)
    for k in cw.all.values():
        if k["@type"] == "EpistemicEdge" and k["@id"] not in bw.all:
            fact = cw.all.get(k["fact_or_event_id"], {})
            if fact.get("@type") == "Rumor":
                continue
            knowledge[k["agent_id"]].append({"fact": name(k["fact_or_event_id"]), "kind": k["kind"],
                                             "confidence": k.get("confidence"), "day": k.get("in_world_start")})
    knowledge_list = [{"agent": {"id": a, "name": name(a)}, "learned": sorted(v, key=lambda x: x["day"] or 0)}
                      for a, v in sorted(knowledge.items(), key=lambda kv: name(kv[0]))
                      if not (cw.get(a) or {}).get("is_party")]

    resources = []
    for f in cw.of_type("Faction"):
        old = bw.all.get(f["@id"])
        if old and old.get("resources") != f.get("resources"):
            resources.append({"faction": f["name"], "before": old.get("resources"), "after": f.get("resources")})

    deaths = [{"id": d["@id"], "name": d["name"], "day": d["in_world_end"], "event": name(d.get("ended_by_event"))}
              for d in changed if d["@type"] in ("Character", "Monster") and d.get("in_world_end") is not None
              and bw.all[d["@id"]].get("in_world_end") is None]

    # ---------------------------------------------------------------- 4. rumors
    rumors = sorted(({
        "id": r["@id"], "text": r.get("text"), "dialogue": r.get("dialogue"), "speaker": r.get("speaker"),
        "accuracy": r.get("accuracy"), "day": r.get("in_world_start"),
        "location": {"id": r["location"], "name": name(r["location"])},
        "about_event": {"id": r["about_event"], "title": name(r["about_event"])} if r.get("about_event") else None,
    } for r in added if r["@type"] == "Rumor"), key=lambda r: (r["day"] or 0, r["location"]["name"]))

    return {
        "base": base, "compare": compare, "run": {k: v for k, v in (run or {}).items() if k != "report"} or None,
        "stats": {"added": len(added), "changed": len(changed), "removed": len(removed),
                  "events": len(new_events), "rumors": len(rumors),
                  "violations": len(report.get("violations", []))},
        "ripples": ripples,
        "offscreen": offscreen,
        "shifts": {"relationships": rel_shifts, "goals": goals, "desires": desires, "controls": controls,
                   "knowledge": knowledge_list, "resources": resources, "deaths": deaths},
        "rumors": rumors,
        "activations": activations,
        "intents": report.get("intents", []),
        "violations": report.get("violations", []),
        "rounds": report.get("rounds", []),
    }

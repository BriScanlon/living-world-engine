"""Apply WorldEventDrafts to a branch as bi-temporal writes.

Nothing is deleted: superseded edges get ``in_world_end`` + ``ended_by_event``; the
replacement edge gets ``in_world_start`` + ``source_event``. One TerminusDB commit per batch.
"""
from __future__ import annotations

from typing import Any

from graph import builders as b
from graph.schema import stable_uid
from graph.world import WorldView, active_at
from simulation.models import WorldEventDraft

AGENTISH = ("Character/", "Faction/")


class Mutator:
    def __init__(self, world: WorldView, session: str, salt: Any) -> None:
        self.w = world
        self.session = session
        self.salt = salt
        self.pending: dict[str, dict[str, Any]] = {}

    # ------------------------------------------------------------------ state access
    def cur(self, doc_id: str) -> dict[str, Any] | None:
        return self.pending.get(doc_id) or self.w.all.get(doc_id)

    def put(self, doc: dict[str, Any]) -> None:
        self.pending[doc["@id"]] = doc

    def _active(self, doc_type: str, day: int, pred) -> list[dict[str, Any]]:
        ids = {d["@id"] for d in self.w.all.values() if d["@type"] == doc_type} | \
              {i for i, d in self.pending.items() if d["@type"] == doc_type}
        return [d for d in (self.cur(i) for i in ids) if d and active_at(d, day) and pred(d)]

    def exists(self, doc_id: str | None) -> bool:
        return bool(doc_id) and self.cur(doc_id) is not None

    # ------------------------------------------------------------------ primitives
    def shift_trust(self, a: str, c: str, delta: int, day: int, evt: str, label: str | None = None,
                    note: str = "") -> dict[str, Any] | None:
        if not (self.exists(a) and self.exists(c)) or a == c or not a.startswith(AGENTISH):
            return None
        if (self.cur(a) or {}).get("is_party"):
            return None  # the players decide how the party feels — never simulate it
        current = self._active("RelationshipEdge", day, lambda d: d["from_id"] == a and d["to_id"] == c)
        old = current[0] if current else None
        before = old["trust_score"] if old else 0
        after = max(-100, min(100, before + delta))
        if old:
            self.put(b.end(old, day, by_event=evt))
        new = b.relationship(a, c, after, label or (old or {}).get("label") or ("rival" if after < -30 else "acquaintance"),
                             start=day, session=self.session, source_event=evt, note=note[:200] or None)
        self.put(new)
        return {"from_id": a, "to_id": c, "before": before, "after": after, "label": new["label"],
                "reason": note, "event": evt}

    def set_control(self, target: str, new_owner: str | None, kind: str, day: int, evt: str) -> None:
        if not self.exists(target):
            return
        for e in self._active("ControlEdge", day, lambda d: d["target_id"] == target and d["kind"] == kind):
            self.put(b.end(e, day, by_event=evt))
        if new_owner and self.exists(new_owner):
            self.put(b.control(new_owner, kind, target, start=day, session=self.session, source_event=evt))

    def learn(self, agent: str, fact: str, day: int, evt: str | None, kind: str = "KNOWS", conf: float = 1.0) -> None:
        if not (self.exists(agent) and self.exists(fact)) or agent == fact:
            return
        existing = self._active("EpistemicEdge", day, lambda d: d["agent_id"] == agent and d["fact_or_event_id"] == fact)
        if any(d["kind"] == "KNOWS" or kind == "BELIEVES" for d in existing):
            return
        for old in existing:  # suspicion confirmed: BELIEVES -> KNOWS
            self.put(b.end(old, day, by_event=evt))
        self.put(b.knows(agent, fact, kind=kind, confidence=conf, start=day, session=self.session, source_event=evt))

    def bump_goal(self, goal_id: str, amount: int, day: int, evt: str) -> dict[str, Any] | None:
        g = self.cur(goal_id)
        if not g or g.get("status") != "active" or amount <= 0:
            return None
        g = dict(g)
        before = g["clock_progress"]
        g["clock_progress"] = min(g["clock_segments"], before + amount)
        g["source_event"] = evt
        if g["clock_progress"] >= g["clock_segments"]:
            g["status"] = "achieved"
            g["in_world_end"] = day
            g["ended_by_event"] = evt
        self.put(g)
        return {"goal": goal_id, "name": g["name"], "owner": g["owner"], "before": before,
                "after": g["clock_progress"], "segments": g["clock_segments"], "status": g["status"]}

    # ------------------------------------------------------------------ high level
    def apply_event(self, draft: WorldEventDraft, idx: int, *, caused_by_ok: set[str]) -> dict[str, Any]:
        day = draft.day
        actors = [a for a in draft.actor_ids if self.exists(a)]
        targets = [t for t in draft.target_ids if self.exists(t)]
        ev = b.event(draft.title, draft.summary, day=day, event_type=draft.event_type, actors=actors,
                     targets=targets, location=draft.location_id if self.exists(draft.location_id) else None,
                     visibility=draft.visibility,
                     caused_by=draft.caused_by if draft.caused_by in caused_by_ok or self.exists(draft.caused_by) else None,
                     session=self.session, salt=(self.salt, idx))
        evt = ev["@id"]
        self.put(ev)
        report: dict[str, Any] = {"event": evt, "title": draft.title, "shifts": [], "goals": [], "controls": [],
                                  "deaths": [], "new_goals": [], "new_desires": []}

        for s in draft.relationship_shifts:
            r = self.shift_trust(s.from_id, s.to_id, s.delta, day, evt, s.label, s.reason)
            if r:
                report["shifts"].append(r)
        for cc in draft.control_changes:
            if self.exists(cc.target_id):
                self.set_control(cc.target_id, cc.new_controller_id, cc.kind, day, evt)
                report["controls"].append(cc.model_dump())
        for k in draft.killed_ids:
            d = self.cur(k)
            if d and d.get("in_world_end") is None:
                self.put(b.end(d, day, by_event=evt))
                report["deaths"].append(k)
        for fac, delta in draft.resource_changes.items():
            d = self.cur(fac)
            if d and d["@type"] == "Faction" and delta:
                d = dict(d)
                d["resources"] = max(0, (d.get("resources") or 0) + int(delta))
                self.put(d)
        for gid, amt in draft.goal_progress.items():
            r = self.bump_goal(gid, amt, day, evt)
            if r:
                report["goals"].append(r)
        for ng in draft.new_goals:
            if not self.exists(ng.owner_id):
                continue
            g = b.goal(ng.owner_id, ng.title, priority=ng.priority, segments=ng.clock_segments, progress=0, every=3,
                       target=ng.target_id if self.exists(ng.target_id) else None, start=day, session=self.session,
                       description=ng.description, uid=stable_uid("goal", ng.owner_id, ng.title, self.salt))
            g["source_event"] = evt
            self.put(g)
            report["new_goals"].append({"goal": g["@id"], "owner": ng.owner_id, "title": ng.title})
        for nd in draft.new_desires:
            if not self.exists(nd.from_id):
                continue
            self.put(b.desire(nd.from_id, nd.kind, priority=nd.priority,
                              target=nd.target_id if self.exists(nd.target_id) else None, text=nd.target_text,
                              start=day, session=self.session, source_event=evt, note=nd.reason or None))
            report["new_desires"].append(nd.model_dump())
        for w in set(draft.witnesses) | set(a for a in actors if a.startswith(AGENTISH)):
            self.learn(w, evt, day, evt)
        for kg in draft.knowledge_gained:
            self.learn(kg.agent_id, kg.fact_id, day, evt, kg.kind, kg.confidence)
        return report

    def docs(self) -> list[dict[str, Any]]:
        return list(self.pending.values())

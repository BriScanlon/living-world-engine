"""In-memory view of a branch (optionally at a commit), filtered to an in-world day.

Campaign graphs are small (hundreds to low thousands of docs), so we load a branch once
per operation and answer neighbourhood / epistemic questions in Python. The store layer
is the only thing that talks to TerminusDB, so swapping these for WOQL queries later is
a local change.
"""
from __future__ import annotations

from collections import defaultdict, deque
from typing import Any, Iterable

from graph.schema import AGENT_TYPES, EDGE_TYPES, NODE_TYPES, type_of


def active_at(doc: dict[str, Any], day: int | None) -> bool:
    """Valid-time filter: start <= day < end (missing bounds are open)."""
    if day is None:
        return True
    start = doc.get("in_world_start")
    end = doc.get("in_world_end")
    if start is not None and start > day:
        return False
    if end is not None and end <= day:
        return False
    return True


class WorldView:
    def __init__(self, docs: Iterable[dict[str, Any]], day: int | None = None) -> None:
        self.day = day
        self.all: dict[str, dict[str, Any]] = {d["@id"]: d for d in docs}
        self.docs: dict[str, dict[str, Any]] = {
            i: d for i, d in self.all.items()
            if d["@type"] in ("ForecastRun", "Alias") or active_at(d, day)
            # Events/Rumors are point-in-time facts: visible once they've happened
            or (d["@type"] in ("Event", "Rumor") and (day is None or (d.get("in_world_start") or 0) <= day))
        }
        self._adj: dict[str, set[str]] | None = None

    # ------------------------------------------------------------------ lookup
    @classmethod
    def load(cls, store, branch: str = "main", day: int | None = None, commit: str | None = None) -> "WorldView":
        return cls(store.all_documents(branch, commit=commit), day)

    def of_type(self, *types: str) -> list[dict[str, Any]]:
        return [d for d in self.docs.values() if d["@type"] in types]

    def nodes(self) -> list[dict[str, Any]]:
        return self.of_type(*NODE_TYPES)

    def edges(self) -> list[dict[str, Any]]:
        return self.of_type(*EDGE_TYPES)

    def get(self, doc_id: str | None) -> dict[str, Any] | None:
        return self.docs.get(doc_id) if doc_id else None

    def name(self, doc_id: str | None) -> str:
        if not doc_id:
            return "—"
        d = self.all.get(doc_id)
        return d.get("name", doc_id) if d else doc_id

    def max_day(self) -> int:
        days = [d.get("in_world_start") or 0 for d in self.all.values()] + \
               [d.get("in_world_end") or 0 for d in self.all.values()]
        return max(days or [0])

    @property
    def party(self) -> dict[str, Any] | None:
        for d in self.of_type("Faction"):
            if d.get("is_party"):
                return d
        return None

    # ------------------------------------------------------------------ adjacency
    def adjacency(self) -> dict[str, set[str]]:
        """Undirected entity graph used for the activation radius.

        Epistemic edges are deliberately excluded: *knowing about* something does not put
        you in its blast radius; KNOWS gates what you can reason about, not who wakes up.
        """
        if self._adj is not None:
            return self._adj
        adj: dict[str, set[str]] = defaultdict(set)

        def link(a: str | None, b: str | None) -> None:
            if a and b and a != b and a in self.docs and b in self.docs:
                adj[a].add(b)
                adj[b].add(a)

        for d in self.docs.values():
            t = d["@type"]
            if t == "RelationshipEdge":
                link(d["from_id"], d["to_id"])
            elif t in ("ControlEdge", "DesireEdge"):
                link(d["from_id"], d.get("target_id"))
            elif t == "Character":
                link(d["@id"], d.get("faction"))
                link(d["@id"], d.get("location"))
            elif t == "Faction":
                link(d["@id"], d.get("headquarters"))
            elif t in ("Item", "Monster"):
                link(d["@id"], d.get("location"))
            elif t == "Location":
                link(d["@id"], d.get("parent"))
                link(d["@id"], d.get("province"))
            elif t == "Goal":
                link(d["owner"], d.get("target"))
        self._adj = adj
        return adj

    def within_hops(self, seeds: Iterable[str], hops: int) -> dict[str, tuple[int, list[str]]]:
        """BFS: node -> (distance, path of ids from the nearest seed)."""
        adj = self.adjacency()
        found: dict[str, tuple[int, list[str]]] = {}
        q: deque[tuple[str, int, list[str]]] = deque()
        for s in seeds:
            if s in self.docs and s not in found:
                found[s] = (0, [s])
                q.append((s, 0, [s]))
        while q:
            node, dist, path = q.popleft()
            if dist >= hops:
                continue
            for nb in sorted(adj.get(node, ())):
                if nb not in found:
                    found[nb] = (dist + 1, path + [nb])
                    q.append((nb, dist + 1, path + [nb]))
        return found

    # ------------------------------------------------------------------ agent views
    def agents(self) -> list[dict[str, Any]]:
        return [d for d in self.of_type(*AGENT_TYPES) if not d.get("is_party") and d.get("kind") != "pc"]

    def desires_of(self, agent_id: str) -> list[dict[str, Any]]:
        return sorted((e for e in self.of_type("DesireEdge") if e["from_id"] == agent_id),
                      key=lambda e: -e.get("priority", 0))

    def goals_of(self, agent_id: str) -> list[dict[str, Any]]:
        return [g for g in self.of_type("Goal") if g["owner"] == agent_id and g.get("status") == "active"]

    def controls_of(self, agent_id: str) -> list[dict[str, Any]]:
        return [e for e in self.of_type("ControlEdge") if e["from_id"] == agent_id]

    def controller_of(self, target_id: str) -> str | None:
        for e in self.of_type("ControlEdge"):
            if e["target_id"] == target_id and e["kind"] == "CONTROLS":
                return e["from_id"]
        for e in self.of_type("ControlEdge"):
            if e["target_id"] == target_id:
                return e["from_id"]
        return None

    def relationships_of(self, agent_id: str) -> list[dict[str, Any]]:
        return [e for e in self.of_type("RelationshipEdge") if e["from_id"] == agent_id]

    def relationship(self, a: str, b: str) -> dict[str, Any] | None:
        for e in self.of_type("RelationshipEdge"):
            if e["from_id"] == a and e["to_id"] == b:
                return e
        return None

    def members_of(self, faction_id: str) -> list[dict[str, Any]]:
        return [c for c in self.of_type("Character") if c.get("faction") == faction_id]

    def epistemic_of(self, agent_id: str) -> list[dict[str, Any]]:
        """KNOWS/BELIEVES edges for an agent, including what its faction knows."""
        me = self.get(agent_id) or {}
        holders = {agent_id}
        if me.get("faction"):
            holders.add(me["faction"])
        return [e for e in self.of_type("EpistemicEdge") if e["agent_id"] in holders]

    def known_ids(self, agent_id: str) -> set[str]:
        """Everything this agent may legally reason over."""
        me = self.get(agent_id) or {}
        known = {agent_id}
        if me.get("faction"):
            known.add(me["faction"])
        if me.get("location"):
            known.add(me["location"])
        if me.get("headquarters"):
            known.add(me["headquarters"])
        for e in self.epistemic_of(agent_id):
            known.add(e["fact_or_event_id"])
        for e in self.desires_of(agent_id):
            if e.get("target_id"):
                known.add(e["target_id"])
        for e in self.controls_of(agent_id):
            known.add(e["target_id"])
        for e in self.relationships_of(agent_id):
            known.add(e["to_id"])
        for g in self.goals_of(agent_id):
            known.add(g["@id"])
            if g.get("target"):
                known.add(g["target"])
        return {k for k in known if k in self.docs}

    def agent_context(self, agent_id: str) -> dict[str, Any]:
        """The *only* slice of the world an agent's LLM prompt may see."""
        me = self.get(agent_id)
        known = self.known_ids(agent_id)
        beliefs = {e["fact_or_event_id"]: e for e in self.epistemic_of(agent_id)}

        def brief(i: str) -> dict[str, Any]:
            d = self.docs[i]
            out = {"id": i, "type": d["@type"], "name": d.get("name")}
            for k in ("description", "summary", "role", "location_type", "text", "status"):
                if d.get(k):
                    out[k] = d[k]
            if d["@type"] == "Event":
                out["day"] = d.get("in_world_start")
            if i in beliefs:
                out["epistemic"] = beliefs[i]["kind"]
                out["confidence"] = beliefs[i].get("confidence")
                out["learned_on"] = beliefs[i].get("in_world_start")
            return out

        return {
            "self": {k: v for k, v in (me or {}).items() if not k.startswith("@") or k == "@id"},
            "desires": [
                {"kind": e["kind"], "target_id": e.get("target_id"), "target_name": self.name(e.get("target_id")),
                 "target_text": e.get("target_text"), "priority": e.get("priority")}
                for e in self.desires_of(agent_id)
            ],
            "goals": [
                {"id": g["@id"], "name": g["name"], "description": g.get("description"),
                 "target_id": g.get("target"), "priority": g["priority"],
                 "clock": f"{g['clock_progress']}/{g['clock_segments']}"}
                for g in self.goals_of(agent_id)
            ],
            "controls": [
                {"kind": e["kind"], "target_id": e["target_id"], "target_name": self.name(e["target_id"])}
                for e in self.controls_of(agent_id)
            ],
            "relationships": [
                {"to_id": e["to_id"], "to_name": self.name(e["to_id"]), "trust": e["trust_score"], "label": e["label"]}
                for e in self.relationships_of(agent_id)
            ],
            "known_world": [brief(i) for i in sorted(known) if i != agent_id],
        }

    # ------------------------------------------------------------------ serialisation
    def snapshot(self) -> dict[str, Any]:
        nodes = []
        for d in self.nodes():
            nodes.append({**d, "alive": d.get("in_world_end") is None or (self.day is not None and d["in_world_end"] > self.day)})
        return {
            "day": self.day,
            "nodes": sorted(nodes, key=lambda d: (d["@type"], d.get("name", ""))),
            "edges": sorted(self.edges(), key=lambda d: (d["@type"], d["@id"])),
            "counts": {t: len(self.of_type(t)) for t in NODE_TYPES + EDGE_TYPES},
        }


def type_label(doc_id: str) -> str:
    return type_of(doc_id)

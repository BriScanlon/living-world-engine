"""Small constructors for schema documents (keeps seed / simulation code readable)."""
from __future__ import annotations

from typing import Any

from graph.schema import doc_id, slugify, stable_uid


def entity(doc_type: str, name: str, *, uid: str | None = None, start: int | None = 0,
           session: str | None = None, **fields: Any) -> dict[str, Any]:
    uid = uid or slugify(name)
    return {"@type": doc_type, "@id": doc_id(doc_type, uid), "uid": uid, "name": name,
            "in_world_start": start, "source_session": session, **fields}


def goal(owner: str, name: str, *, priority: float, segments: int, progress: int = 0,
         every: int = 3, target: str | None = None, start: int = 0, session: str | None = None,
         description: str | None = None, uid: str | None = None) -> dict[str, Any]:
    return entity("Goal", name, uid=uid, owner=owner, target=target, priority=priority,
                  clock_segments=segments, clock_progress=progress, ticks_every_days=every,
                  status="active", start=start, session=session, description=description)


def event(name: str, summary: str, *, day: int, event_type: str, actors: list[str] | None = None,
          targets: list[str] | None = None, location: str | None = None, visibility: str = "public",
          caused_by: str | None = None, session: str | None = None, uid: str | None = None,
          consequences: str | None = None, salt: Any = "") -> dict[str, Any]:
    uid = uid or stable_uid("evt", name, day, salt)
    return entity("Event", name, uid=uid, start=day, session=session, summary=summary,
                  event_type=event_type, actors=actors or [], targets=targets or [],
                  location=location, visibility=visibility, caused_by=caused_by,
                  consequences=consequences)


def rumor(text: str, dialogue: str, *, location: str, accuracy: str, day: int,
          about_event: str | None = None, speaker: str | None = None,
          session: str | None = None, salt: Any = "") -> dict[str, Any]:
    uid = stable_uid("rumor", text, location, day, salt)
    name = text if len(text) <= 60 else text[:57] + "..."
    return entity("Rumor", name, uid=uid, start=day, session=session, text=text, dialogue=dialogue,
                  location=location, accuracy=accuracy, about_event=about_event, speaker=speaker)


def _edge(doc_type: str, prefix: str, key: tuple, *, start: int, session: str | None,
          source_event: str | None, note: str | None, **fields: Any) -> dict[str, Any]:
    uid = stable_uid(prefix, *key, start, session or "")
    return {"@type": doc_type, "@id": doc_id(doc_type, uid), "uid": uid, "in_world_start": start,
            "source_session": session, "source_event": source_event, "note": note, **fields}


def relationship(a: str, b: str, trust: int, label: str, *, start: int = 0, session: str | None = None,
                 source_event: str | None = None, note: str | None = None) -> dict[str, Any]:
    trust = max(-100, min(100, int(trust)))
    return _edge("RelationshipEdge", "rel", (a, b, trust, label), start=start, session=session,
                 source_event=source_event, note=note, from_id=a, to_id=b, trust_score=trust, label=label)


def desire(a: str, kind: str, *, priority: float, target: str | None = None, text: str | None = None,
           start: int = 0, session: str | None = None, source_event: str | None = None,
           note: str | None = None) -> dict[str, Any]:
    return _edge("DesireEdge", "desire", (a, kind, target, text), start=start, session=session,
                 source_event=source_event, note=note, from_id=a, kind=kind, target_id=target,
                 target_text=text, priority=round(float(priority), 3))


def control(a: str, kind: str, target: str, *, start: int = 0, session: str | None = None,
            source_event: str | None = None, note: str | None = None) -> dict[str, Any]:
    return _edge("ControlEdge", "ctrl", (a, kind, target), start=start, session=session,
                 source_event=source_event, note=note, from_id=a, kind=kind, target_id=target)


def knows(agent: str, fact: str, *, kind: str = "KNOWS", confidence: float = 1.0, start: int = 0,
          session: str | None = None, source_event: str | None = None, note: str | None = None) -> dict[str, Any]:
    return _edge("EpistemicEdge", "know", (agent, kind, fact), start=start, session=session,
                 source_event=source_event, note=note, agent_id=agent, kind=kind,
                 fact_or_event_id=fact, confidence=round(float(confidence), 3))


def end(doc: dict[str, Any], day: int, *, by_event: str | None = None) -> dict[str, Any]:
    """Expire a fact instead of deleting it (valid-time close)."""
    out = dict(doc)
    out["in_world_end"] = day
    if by_event:
        out["ended_by_event"] = by_event
    return out

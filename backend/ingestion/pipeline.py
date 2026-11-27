"""Ingestion orchestration: raw files -> Markdown -> (frontmatter + wikilinks + extraction) -> graph.

Every source file becomes one TerminusDB commit on the target branch, so a bad import can be
inspected with the diff view and simply discarded by ingesting into a scratch branch first.
"""
from __future__ import annotations

import logging
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from graph import builders as b
from graph.schema import slugify
from graph.store import GraphStore, get_store
from graph.world import WorldView
from ingestion import audio, documents
from ingestion.catalogue_link import CatalogueLinker
from ingestion.extract import ExtractedEdge, extract, guess_type
from ingestion.markdown import chunk_markdown, parse_markdown, strip_wikilinks
from ingestion.resolution import EntityResolver

log = logging.getLogger(__name__)
AGENTISH = ("Character", "Faction")


@dataclass
class Source:
    name: str
    kind: str  # markdown | text | document | audio
    path: str | None = None
    text: str | None = None
    is_session: bool = False


@dataclass
class Job:
    id: str
    branch: str
    status: str = "queued"
    sources: list[str] = field(default_factory=list)
    results: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None
    created_at: float = field(default_factory=time.time)

    def dump(self) -> dict[str, Any]:
        return self.__dict__.copy()


JOBS: dict[str, Job] = {}


class GraphBuilder:
    """Accumulates node/edge upserts with entity resolution against an existing branch."""

    def __init__(self, world: WorldView, day: int, session: str | None) -> None:
        self.w = world
        self.day = day
        self.session = session
        self.resolver = EntityResolver(world.all.values())
        self.pending: dict[str, dict[str, Any]] = {}
        self.created: list[str] = []
        self.merged: list[dict[str, str]] = []
        self.edges = 0
        self.linked = 0  # nodes given catalogue fields (srd_ref, challenge, species...)

    def cur(self, i: str) -> dict[str, Any] | None:
        return self.pending.get(i) or self.w.all.get(i)

    def put(self, d: dict[str, Any]) -> None:
        self.pending[d["@id"]] = d

    # ------------------------------------------------------------------ nodes
    def node(self, name: str, node_type: str | None = None, *, aliases: list[str] | None = None,
             description: str | None = None, props: dict[str, Any] | None = None, stub: bool = False) -> str | None:
        name = name.strip()
        if not name:
            return None
        hit = self.resolver.resolve(name)
        if hit:
            d = dict(self.cur(hit) or {})
            if not d:
                return None
            changed = False
            new_aliases = [a for a in [name, *(aliases or [])] if a and a != d.get("name") and a not in d.get("aliases", [])]
            if new_aliases:
                d["aliases"] = sorted(set(d.get("aliases", [])) | set(new_aliases))
                changed = True
                for a in new_aliases:
                    uid = "alias-" + slugify(a)
                    self.put({"@type": "Alias", "@id": f"Alias/{uid}", "uid": uid, "surface": a, "canonical": hit})
                    self.merged.append({"surface": a, "canonical": d.get("name", hit)})
                self.resolver.add_entity(hit, d["name"], d["@type"], new_aliases)
            if description and not d.get("description"):
                d["description"] = description
                changed = True
            for k, v in (props or {}).items():
                if v is not None and d.get(k) != v:
                    d[k] = v
                    changed = True
            if changed:
                self.put(d)
            return hit
        node_type = node_type or guess_type(name)
        if node_type in ("Event", "Goal", "Rumor", "Session"):
            return None  # free-floating events/goals need richer structure than lore notes give
        d = b.entity(node_type, name, start=self.day if self.session else 0, session=self.session,
                     aliases=aliases or [], description=description, tags=["stub"] if stub else [], **(props or {}))
        self.put(d)
        self.created.append(d["@id"])
        self.resolver.add_entity(d["@id"], name, node_type, aliases or [])
        return d["@id"]

    # ------------------------------------------------------------------ edges
    def _exists(self, doc_type: str, pred) -> list[dict[str, Any]]:
        ids = {i for i, d in {**self.w.all, **self.pending}.items() if d["@type"] == doc_type}
        return [d for d in (self.cur(i) for i in ids) if d and d.get("in_world_end") is None and pred(d)]

    def edge(self, kind: str, src: str | None, tgt: str | None, *, priority: float | None = None,
             trust: int | None = None, label: str | None = None, confidence: float | None = None,
             target_text: str | None = None) -> None:
        if not src or (not tgt and not target_text) or src == tgt:
            return
        stype = src.split("/")[0]
        day, sess = self.day if self.session else 0, self.session
        if kind in ("WANTS", "NEEDS", "FEARS"):
            if self._exists("DesireEdge", lambda d: d["from_id"] == src and d["kind"] == kind and d.get("target_id") == tgt
                            and d.get("target_text") == target_text):
                return
            self.put(b.desire(src, kind, priority=priority or 0.6, target=tgt, text=target_text, start=day, session=sess))
        elif kind in ("CONTROLS", "OWNS") and tgt:
            if self._exists("ControlEdge", lambda d: d["target_id"] == tgt and d["kind"] == kind and d["from_id"] == src):
                return
            for old in self._exists("ControlEdge", lambda d: d["target_id"] == tgt and d["kind"] == kind):
                self.put(b.end(old, day))
            self.put(b.control(src, kind, tgt, start=day, session=sess))
        elif kind == "RELATIONSHIP" and tgt and stype in AGENTISH and tgt.split("/")[0] in AGENTISH:
            old = self._exists("RelationshipEdge", lambda d: d["from_id"] == src and d["to_id"] == tgt)
            t = trust if trust is not None else 0
            if old and old[0]["trust_score"] == t and old[0]["label"] == (label or old[0]["label"]):
                return
            for o in old:
                self.put(b.end(o, day))
            self.put(b.relationship(src, tgt, t, label or "acquaintance", start=day, session=sess))
        elif kind in ("KNOWS", "BELIEVES") and tgt and stype in AGENTISH:
            if self._exists("EpistemicEdge", lambda d: d["agent_id"] == src and d["fact_or_event_id"] == tgt):
                return
            self.put(b.knows(src, tgt, kind=kind, confidence=confidence or (1.0 if kind == "KNOWS" else 0.6),
                             start=day, session=sess))
        elif kind == "MEMBER_OF" and tgt and stype == "Character" and tgt.startswith("Faction/"):
            d = dict(self.cur(src))
            d["faction"] = tgt
            self.put(d)
        elif kind == "LOCATED_IN" and tgt and tgt.startswith("Location/"):
            d = dict(self.cur(src))
            d["parent" if stype == "Location" else "location"] = tgt
            self.put(d)
        else:
            return
        self.edges += 1

    def apply_extracted(self, edges: list[ExtractedEdge]) -> None:
        for e in edges:
            src = self.node(e.source)
            tgt = self.node(e.target) if e.target else None
            self.edge(e.kind, src, tgt, priority=e.priority, trust=e.trust, label=e.label,
                      confidence=e.confidence, target_text=e.target_text)


def _text_for(src: Source) -> tuple[str, str]:
    if src.text is not None:
        return src.text, "inline"
    assert src.path
    if src.kind == "audio":
        return audio.transcribe(src.path), "faster-whisper"
    return documents.to_markdown(src.path)


def ingest_source(store: GraphStore, src: Source, branch: str, day: int | None) -> dict[str, Any]:
    """Ingest one source inside the campaign's catalogue layer (SRD + its homebrew), so what the notes mention is
    linked to catalogue entries (``ingestion.catalogue_link``)."""
    from srd import catalogue, homebrew
    with catalogue.homebrew(homebrew.overlay_entries(store)):
        return _ingest_source(store, src, branch, day)


def _link(gb: "GraphBuilder", linker: CatalogueLinker, doc_id: str | None, names: list[str],
          species: str | None = None) -> None:
    """Add catalogue fields a node lacks: a Monster's or Item's srd_ref (+ challenge/threat, rarity), a character's
    species (canonical when it is a catalogued people). Never overwrites what the node already has."""
    d = gb.cur(doc_id) if doc_id else None
    if not d:
        return
    extra: dict[str, Any] = {}
    for n in [d.get("name"), *names, *d.get("aliases", [])]:
        extra = linker.props_for(d["@type"], n, d) if n else {}
        if extra:
            break
    if species and d["@type"] == "Character" and not d.get("species"):
        extra["species"] = linker.species(species) or species
    if extra:
        gb.put({**d, **extra})
        gb.linked += 1


def _ingest_source(store: GraphStore, src: Source, branch: str, day: int | None) -> dict[str, Any]:
    world = WorldView.load(store, branch)
    linker = CatalogueLinker()
    the_day = day if day is not None else world.max_day()
    text, converter = _text_for(src)
    note = parse_markdown(text, src.name) if src.kind in ("markdown", "text") else None
    is_session = src.is_session or (note is not None and note.node_type == "Session")
    # Lore is timeless (valid from day 0); session notes are stamped with the in-world day.
    gb = GraphBuilder(world, the_day, f"ingest/{slugify(Path(src.name).stem)}" if is_session else None)
    body = text

    if note and note.node_type and note.node_type not in ("Session", "Event", "Goal", "Rumor"):
        props = dict(note.props)
        props.pop("description", None)
        me = gb.node(note.title, note.node_type, aliases=note.aliases,
                     description=note.props.get("description") or _first_paragraph(note.body), props=props)
        for k, surface in note.link_props.items():
            tgt = gb.node(surface, {"location": "Location", "headquarters": "Location", "parent": "Location",
                                    "faction": "Faction"}.get(k), stub=True)
            if me and tgt:
                d = dict(gb.cur(me))
                d[k] = tgt
                gb.put(d)
        for kind, surface, default in note.typed_edges:
            tgt = gb.node(surface, stub=True)
            if kind == "RELATIONSHIP":
                gb.edge(kind, me, tgt, trust=default[0], label=default[1])
            elif kind in ("KNOWS", "BELIEVES"):
                gb.edge(kind, me, tgt, confidence=default)
            else:
                gb.edge(kind, me, tgt, priority=default)
        # Body wikilinks are baseline edges: an agent's note referencing X means it knows about X
        for surface in note.wikilinks:
            tgt = gb.node(surface, stub=True)
            if me and me.split("/")[0] in AGENTISH:
                gb.edge("KNOWS", me, tgt)
        body = note.body
    elif note:
        for surface in note.wikilinks:
            gb.node(surface, stub=True)
        body = note.body

    chunks = chunk_markdown(strip_wikilinks(body)) if body.strip() else []
    for chunk in chunks:
        result = extract(chunk, gb.resolver, linker)
        for n in result.nodes:
            nid = gb.node(n.name, n.type, aliases=n.aliases, description=n.description,
                          props={k: v for k, v in {"role": n.role, "location_type": n.location_type}.items() if v})
            _link(gb, linker, nid, [n.name, *n.aliases], n.species)
        gb.apply_extracted(result.edges)

    # anything else written from this source (a note's own entity, wikilinked stubs) gets linked too
    for d in list(gb.pending.values()):
        if d.get("@type") in ("Monster", "Item") and not d.get("srd_ref"):
            _link(gb, linker, d["@id"], [])
    docs = list(gb.pending.values())
    if docs:
        store.upsert(docs, branch, f"[ingest] {src.name}: +{len(gb.created)} nodes, +{gb.edges} edges"
                                   f"{', ' + str(len(gb.merged)) + ' aliases merged' if gb.merged else ''}")
    return {"source": src.name, "converter": converter, "chunks": len(chunks), "documents_written": len(docs),
            "created": [{"id": i, "name": (gb.cur(i) or {}).get("name")} for i in gb.created],
            "aliases_merged": gb.merged, "edges": gb.edges, "catalogue_links": gb.linked, "in_world_day": the_day}


def _first_paragraph(body: str) -> str | None:
    for para in body.split("\n\n"):
        p = strip_wikilinks(para.strip())
        if p and not p.startswith("#"):
            return p[:400]
    return None


def start_job(sources: list[Source], branch: str, day: int | None, store: GraphStore | None = None) -> Job:
    job = Job(id=uuid.uuid4().hex[:12], branch=branch, sources=[s.name for s in sources])
    JOBS[job.id] = job
    store = (store or get_store()).bound()  # pinned to the campaign the job started in

    def work() -> None:
        job.status = "running"
        try:
            if not store.branch_exists(branch):
                store.create_branch(branch, "main")
            for s in sources:
                job.results.append(ingest_source(store, s, branch, day))
            job.status = "complete"
        except Exception as e:  # noqa: BLE001
            log.exception("Ingest job failed")
            job.status = "failed"
            job.error = f"{type(e).__name__}: {e}"

    threading.Thread(target=work, daemon=True, name=f"ingest-{job.id}").start()
    return job

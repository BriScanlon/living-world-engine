"""Thin service over TerminusDB: branches, documents, commits, diff, merge, cherry-pick.

TerminusDB branch ids may not contain "/", so the *display* name
``forecast/session-14-20261124T1530`` maps to the branch id
``forecast--session-14-20261124T1530`` (see :func:`to_branch_id`).
"""
from __future__ import annotations

import logging
import threading
from typing import Any, Iterable

import requests
from terminusdb_client import Client
from terminusdb_client.errors import DatabaseError

from config import Settings, get_settings
from graph.schema import (
    LINK_FIELDS,
    SCHEMA,
    SOFT_LINK_FIELDS,
    clean_for_write,
    normalize,
)

log = logging.getLogger(__name__)

MAIN = "main"
AUTHOR = "living-world-engine"


def to_branch_id(name: str) -> str:
    return name.replace("/", "--")


def to_display(branch_id: str) -> str:
    return branch_id.replace("--", "/", 1) if branch_id.startswith("forecast--") else branch_id


class GraphStoreError(RuntimeError):
    pass


class GraphStore:
    """One instance per process; every call opens a cheap client copy bound to a branch."""

    _lock = threading.RLock()

    def __init__(self, settings: Settings | None = None) -> None:
        self.s = settings or get_settings()
        self.db = self.s.terminusdb_db
        self.team = self.s.terminusdb_team
        self._base: Client | None = None

    # ------------------------------------------------------------------ clients
    def _root(self) -> Client:
        if self._base is None:
            c = Client(self.s.terminusdb_url)
            c.connect(user=self.s.terminusdb_user, key=self.s.terminusdb_pass, team=self.team)
            self._base = c
        return self._base

    def client(self, branch: str = MAIN, ref: str | None = None) -> Client:
        c = self._root().copy()
        c.db = self.db
        c.branch = to_branch_id(branch)
        c.ref = ref
        return c

    @property
    def _http_auth(self) -> tuple[str, str]:
        return (self.s.terminusdb_user, self.s.terminusdb_pass)

    def _api(self, path: str) -> str:
        return self.s.terminusdb_url.rstrip("/") + "/api/" + path.lstrip("/")

    # ------------------------------------------------------------------ database
    def ping(self) -> dict[str, Any]:
        r = requests.get(self._api("info"), auth=self._http_auth, timeout=5)
        r.raise_for_status()
        return r.json().get("api:info", {})

    def db_exists(self) -> bool:
        r = requests.get(self._api(f"db/{self.team}/{self.db}"), auth=self._http_auth, timeout=10)
        return r.status_code == 200

    def ensure_database(self) -> bool:
        """Create the database + schema if missing. Returns True if it was created."""
        with self._lock:
            created = False
            if not self.db_exists():
                root = self._root()
                root.create_database(
                    self.db, team=self.team, label="Living World",
                    description="Living World Engine campaign graph",
                )
                created = True
            self.apply_schema()
            return created

    def apply_schema(self) -> None:
        c = self.client(MAIN)
        current = c.get_all_documents(graph_type="schema", as_list=True)
        wanted = {d.get("@id", "@context"): d for d in SCHEMA}
        have = {d.get("@id", "@context"): d for d in current}
        if set(wanted) == set(have) and all(_schema_eq(wanted[k], have[k]) for k in wanted if k != "@context"):
            return
        c.insert_document(SCHEMA, graph_type="schema", full_replace=True,
                          commit_msg="[schema] apply Living World schema")
        log.info("Applied TerminusDB schema")

    def reset_database(self) -> None:
        with self._lock:
            if self.db_exists():
                self._root().delete_database(self.db, team=self.team, force=True)
            self.ensure_database()

    # ------------------------------------------------------------------ branches
    def list_branches(self) -> list[dict[str, Any]]:
        c = self.client(MAIN)
        out = []
        for b in c.get_all_branches():
            out.append({
                "id": b["name"],
                "name": to_display(b["name"]),
                "head": (b.get("head") or "").split("/")[-1] or None,
            })
        return sorted(out, key=lambda b: (b["id"] != MAIN, b["id"]))

    def branch_exists(self, name: str) -> bool:
        bid = to_branch_id(name)
        return any(b["id"] == bid for b in self.list_branches())

    def head(self, name: str) -> str | None:
        bid = to_branch_id(name)
        for b in self.list_branches():
            if b["id"] == bid:
                return b["head"]
        raise GraphStoreError(f"Unknown branch {name}")

    def create_branch(self, name: str, origin: str = MAIN) -> str:
        c = self.client(origin)
        c.create_branch(to_branch_id(name))
        return name

    def delete_branch(self, name: str) -> None:
        if to_branch_id(name) == MAIN:
            raise GraphStoreError("Refusing to delete main")
        self.client(MAIN).delete_branch(to_branch_id(name))

    def log(self, branch: str = MAIN, limit: int = 100) -> list[dict[str, Any]]:
        url = self._api(f"log/{self.team}/{self.db}/local/branch/{to_branch_id(branch)}")
        r = requests.get(url, auth=self._http_auth, params={"count": limit}, timeout=15)
        if r.status_code != 200:
            raise GraphStoreError(r.text)
        return [
            {
                "commit": e.get("identifier"),
                "author": e.get("author"),
                "message": e.get("message"),
                "timestamp": e.get("timestamp"),
            }
            for e in r.json()
        ]

    # ------------------------------------------------------------------ documents
    def all_documents(self, branch: str = MAIN, commit: str | None = None,
                      doc_type: str | None = None) -> list[dict[str, Any]]:
        c = self.client(branch, ref=commit)
        kwargs: dict[str, Any] = {"as_list": True}
        if doc_type:
            kwargs["doc_type"] = doc_type
        return [normalize(d) for d in c.get_all_documents(**kwargs)]

    def get(self, doc_id: str, branch: str = MAIN) -> dict[str, Any] | None:
        c = self.client(branch)
        try:
            return normalize(c.get_document(doc_id))
        except DatabaseError:
            return None

    def upsert(self, docs: Iterable[dict[str, Any]], branch: str, message: str) -> int:
        payload = [clean_for_write(d) for d in docs]
        if not payload:
            return 0
        with self._lock:
            self.client(branch).replace_document(payload, create=True, commit_msg=message)
        return len(payload)

    def delete(self, ids: list[str], branch: str, message: str) -> None:
        if ids:
            with self._lock:
                self.client(branch).delete_document(ids, commit_msg=message)

    # ------------------------------------------------------------------ diff / merge
    def native_diff(self, before: str, after: str) -> list[dict[str, Any]]:
        """TerminusDB's own JSON patch between two branches (or commits)."""
        c = self.client(MAIN)
        return c.diff_version(_version(before), _version(after))

    def merge(self, source: str, target: str = MAIN, base_commit: str | None = None,
              message: str | None = None) -> dict[str, Any]:
        """Merge ``source`` into ``target``.

        If ``target`` has not moved since the fork we fast-forward via rebase; otherwise we
        apply the 3-way patch (fork-point -> source head) onto ``target``.
        """
        with self._lock:
            msg = message or f"[merge] {to_display(to_branch_id(source))} -> {target}"
            target_head = self.head(target)
            c = self.client(target)
            if base_commit is None or base_commit == target_head:
                res = c.rebase(branch=to_branch_id(source), message=msg, author=AUTHOR)
                return {"strategy": "fast-forward", "result": res}
            try:
                res = c.apply(base_commit, self.head(source), branch=to_branch_id(target),
                              message=msg, author=AUTHOR)
                return {"strategy": "apply-patch", "result": res}
            except DatabaseError as e:
                conflicts = _conflict_ids(e)
                if not conflicts:
                    raise
            # Native patch conflicted (typically docs already cherry-picked into target in a slightly
            # different shape). Fall back to a document-level 3-way merge — forecast wins on any doc
            # it changed since the fork — written as ONE commit so all references resolve together.
            base = {d["@id"]: d for d in self.all_documents(target, commit=base_commit)}
            src = {d["@id"]: d for d in self.all_documents(source)}
            changed = [d for i, d in src.items() if base.get(i) != d]
            removed = [i for i in base if i not in src]
            self.upsert(changed, target, msg + f" (3-way, {len(conflicts)} conflicts resolved forecast-wins)")
            if removed:
                self.delete(removed, target, msg + " (removals)")
            return {"strategy": "document-3way", "conflicts": conflicts, "written": len(changed),
                    "removed": len(removed)}

    def cherry_pick(self, source: str, event_ids: list[str], target: str = MAIN) -> dict[str, Any]:
        """Copy forecasted events plus everything they caused into ``target``.

        Picks the Event docs, every doc whose ``source_event`` / ``ended_by_event`` is one of
        them, rumors about them, and the hard-link dependency closure of all of those.
        Provenance links whose target is absent in ``target`` are dropped.
        """
        src = {d["@id"]: d for d in self.all_documents(source)}
        dst_ids = {d["@id"] for d in self.all_documents(target)}
        picked: dict[str, dict[str, Any]] = {}
        wanted = set(event_ids)
        for d in src.values():
            if d["@id"] in wanted or d.get("source_event") in wanted \
                    or d.get("ended_by_event") in wanted or d.get("about_event") in wanted:
                picked[d["@id"]] = d
        missing = [e for e in event_ids if e not in src]
        if missing:
            raise GraphStoreError(f"Events not on {source}: {missing}")

        # Hard-link closure (e.g. a brand-new NPC introduced by the event)
        queue = list(picked.values())
        while queue:
            d = queue.pop()
            for f in LINK_FIELDS - SOFT_LINK_FIELDS:
                vals = d.get(f)
                for v in vals if isinstance(vals, list) else [vals]:
                    if isinstance(v, str) and v in src and v not in picked and v not in dst_ids:
                        picked[v] = src[v]
                        queue.append(src[v])

        final_ids = dst_ids | set(picked)
        out = []
        for d in picked.values():
            d = dict(d)
            for f in SOFT_LINK_FIELDS:
                if isinstance(d.get(f), str) and d[f] not in final_ids:
                    d.pop(f)
            out.append(d)
        names = ", ".join(src[e].get("name", e) for e in event_ids)
        self.upsert(out, target, f"[cherry-pick] {names} from {to_display(to_branch_id(source))}")
        return {"picked": sorted(picked), "count": len(out)}


def _conflict_ids(err: DatabaseError) -> list[str]:
    """Pull document ids out of an api:ApplyError conflict witness list."""
    import json
    import re

    text = getattr(err, "message", None) or str(err)
    m = re.search(r"\{.*\}", text, re.S)
    try:
        body = json.loads(m.group(0)) if m else {}
    except ValueError:
        body = {}
    ids: list[str] = []
    for w in body.get("api:witnesses", []) or []:
        for v in w.values() if isinstance(w, dict) else []:
            if isinstance(v, str) and "/data/" in v:
                ids.append(v.split("/data/", 1)[1])
            elif isinstance(v, dict) and isinstance(v.get("@id"), str):
                ids.append(v["@id"].split("/data/", 1)[-1])
        if isinstance(w, dict) and isinstance(w.get("@id"), str):
            ids.append(w["@id"].split("/data/", 1)[-1])
    return sorted(set(ids))


def _version(name: str) -> str:
    # Accept a branch name (display or id) or a raw commit id.
    return to_branch_id(name)


def _schema_eq(a: dict, b: dict) -> bool:
    def norm(x: Any) -> Any:
        if isinstance(x, dict):
            return {k: norm(v) for k, v in x.items() if k != "@documentation"}
        if isinstance(x, list):
            return sorted((norm(i) for i in x), key=repr)
        return x
    return norm(a) == norm(b)


_store: GraphStore | None = None


def get_store() -> GraphStore:
    global _store
    if _store is None:
        _store = GraphStore()
    return _store

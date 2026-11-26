"""SQLite persistence: asset (map/token) metadata, table settings, jobs, per-map fog of war.

A single connection guarded by a lock is plenty for a LAN VTT. WAL mode lets the
standalone seeder script write while the server is running.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS assets (
    id            TEXT PRIMARY KEY,
    asset_type    TEXT NOT NULL DEFAULT 'map',   -- map | token
    title         TEXT NOT NULL,
    source        TEXT NOT NULL,                 -- generated | imported | inbox | starter
    media_type    TEXT NOT NULL,                 -- image | video
    media_path    TEXT NOT NULL,                 -- relative to DATA_DIR
    still_path    TEXT,                          -- base still frame (relative)
    thumb_path    TEXT,                          -- small preview (relative)
    width         INTEGER,
    height        INTEGER,
    description   TEXT,
    style         TEXT,
    image_prompt  TEXT,
    motion_prompt TEXT,
    pack          TEXT,                          -- e.g. 'starter'
    tags          TEXT NOT NULL DEFAULT '[]',
    mock          INTEGER NOT NULL DEFAULT 0,
    created_at    REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_assets_type ON assets(asset_type, created_at);

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS jobs (
    id              TEXT PRIMARY KEY,
    kind            TEXT NOT NULL,
    status          TEXT NOT NULL,     -- queued | running | done | error
    stage           TEXT NOT NULL,
    progress        REAL NOT NULL DEFAULT 0,
    message         TEXT NOT NULL DEFAULT '',
    error           TEXT,
    input           TEXT NOT NULL DEFAULT '{}',
    result_asset_id TEXT,
    created_at      REAL NOT NULL,
    updated_at      REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS fog (
    map_id TEXT PRIMARY KEY,
    value  TEXT NOT NULL
);

-- tokens on each map: [{id, name, asset_id, x, y, size, hidden, color}]
CREATE TABLE IF NOT EXISTS tokens (
    map_id TEXT PRIMARY KEY,
    value  TEXT NOT NULL
);

-- what happened at the table, in order: DM notes plus maps shown and world scenes visited
CREATE TABLE IF NOT EXISTS session_log (
    id    INTEGER PRIMARY KEY AUTOINCREMENT,
    at    REAL NOT NULL,
    kind  TEXT NOT NULL,               -- note | map | scene
    text  TEXT NOT NULL,
    meta  TEXT NOT NULL DEFAULT '{}'
);

-- per-map placement on the table: fill / fit / true scale, px per 5 ft square, pan centre
CREATE TABLE IF NOT EXISTS map_view (
    map_id TEXT PRIMARY KEY,
    value  TEXT NOT NULL
);
"""

ASSET_COLUMNS = (
    "id", "asset_type", "title", "source", "media_type", "media_path", "still_path",
    "thumb_path", "width", "height", "description", "style", "image_prompt",
    "motion_prompt", "pack", "tags", "mock", "created_at",
)


class Database:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(path), check_same_thread=False, timeout=30)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.executescript(SCHEMA)
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def _exec(self, sql: str, params: tuple | dict = ()) -> None:
        with self._lock:
            self._conn.execute(sql, params)
            self._conn.commit()

    def _all(self, sql: str, params: tuple | dict = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(sql, params).fetchall()

    def _one(self, sql: str, params: tuple | dict = ()) -> sqlite3.Row | None:
        with self._lock:
            return self._conn.execute(sql, params).fetchone()

    # ---------------------------------------------------------------- assets
    def insert_asset(self, asset: dict[str, Any]) -> dict[str, Any]:
        row = {c: asset.get(c) for c in ASSET_COLUMNS}
        row["tags"] = json.dumps(asset.get("tags") or [])
        row["mock"] = 1 if asset.get("mock") else 0
        row["created_at"] = asset.get("created_at") or time.time()
        cols = ", ".join(ASSET_COLUMNS)
        marks = ", ".join(f":{c}" for c in ASSET_COLUMNS)
        self._exec(f"INSERT OR REPLACE INTO assets ({cols}) VALUES ({marks})", row)
        return self.get_asset(row["id"])  # type: ignore[return-value]

    def get_asset(self, asset_id: str) -> dict[str, Any] | None:
        row = self._one("SELECT * FROM assets WHERE id = ?", (asset_id,))
        return _asset_row(row) if row else None

    def list_assets(self, asset_type: str = "map") -> list[dict[str, Any]]:
        rows = self._all(
            "SELECT * FROM assets WHERE asset_type = ? ORDER BY created_at DESC", (asset_type,)
        )
        return [_asset_row(r) for r in rows]

    def find_asset_by_pack_key(self, pack: str, title: str, asset_type: str) -> dict | None:
        row = self._one(
            "SELECT * FROM assets WHERE pack = ? AND title = ? AND asset_type = ?",
            (pack, title, asset_type),
        )
        return _asset_row(row) if row else None

    def delete_asset(self, asset_id: str) -> None:
        self._exec("DELETE FROM assets WHERE id = ?", (asset_id,))
        self._exec("DELETE FROM fog WHERE map_id = ?", (asset_id,))
        self._exec("DELETE FROM map_view WHERE map_id = ?", (asset_id,))
        self._exec("DELETE FROM tokens WHERE map_id = ?", (asset_id,))

    # -------------------------------------------------------------- settings
    def get_setting(self, key: str, default: Any = None) -> Any:
        row = self._one("SELECT value FROM settings WHERE key = ?", (key,))
        return json.loads(row["value"]) if row else default

    def set_setting(self, key: str, value: Any) -> None:
        self._exec(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, json.dumps(value)),
        )

    # ------------------------------------------------------------------- fog
    def get_fog(self, map_id: str) -> dict | None:
        row = self._one("SELECT value FROM fog WHERE map_id = ?", (map_id,))
        return json.loads(row["value"]) if row else None

    def set_fog(self, map_id: str, fog: dict) -> None:
        self._exec(
            "INSERT INTO fog (map_id, value) VALUES (?, ?) "
            "ON CONFLICT(map_id) DO UPDATE SET value = excluded.value",
            (map_id, json.dumps(fog, separators=(",", ":"))),
        )

    # ---------------------------------------------------------------- tokens
    def get_tokens(self, map_id: str) -> list | None:
        row = self._one("SELECT value FROM tokens WHERE map_id = ?", (map_id,))
        return json.loads(row["value"]) if row else None

    def set_tokens(self, map_id: str, tokens: list) -> None:
        self._exec(
            "INSERT INTO tokens (map_id, value) VALUES (?, ?) "
            "ON CONFLICT(map_id) DO UPDATE SET value = excluded.value",
            (map_id, json.dumps(tokens, separators=(",", ":"))),
        )

    # -------------------------------------------------------------- map view
    def get_view(self, map_id: str) -> dict | None:
        row = self._one("SELECT value FROM map_view WHERE map_id = ?", (map_id,))
        return json.loads(row["value"]) if row else None

    def set_view(self, map_id: str, view: dict) -> None:
        self._exec(
            "INSERT INTO map_view (map_id, value) VALUES (?, ?) "
            "ON CONFLICT(map_id) DO UPDATE SET value = excluded.value",
            (map_id, json.dumps(view)),
        )

    # ------------------------------------------------------------------ jobs
    def upsert_job(self, job: dict[str, Any]) -> None:
        self._exec(
            """INSERT INTO jobs (id, kind, status, stage, progress, message, error, input,
                                 result_asset_id, created_at, updated_at)
               VALUES (:id, :kind, :status, :stage, :progress, :message, :error, :input,
                       :result_asset_id, :created_at, :updated_at)
               ON CONFLICT(id) DO UPDATE SET status=excluded.status, stage=excluded.stage,
                   progress=excluded.progress, message=excluded.message, error=excluded.error,
                   result_asset_id=excluded.result_asset_id, updated_at=excluded.updated_at""",
            {**job, "input": json.dumps(job.get("input") or {})},
        )

    def list_jobs(self, limit: int = 30) -> list[dict[str, Any]]:
        rows = self._all("SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?", (limit,))
        out = []
        for r in rows:
            d = dict(r)
            d["input"] = json.loads(d["input"] or "{}")
            out.append(d)
        return out

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        row = self._one("SELECT * FROM jobs WHERE id = ?", (job_id,))
        if not row:
            return None
        d = dict(row)
        d["input"] = json.loads(d["input"] or "{}")
        return d

    def fail_stale_jobs(self) -> None:
        """Jobs left running by a previous process can never finish."""
        self._exec(
            "UPDATE jobs SET status='error', error='Server restarted', updated_at=? "
            "WHERE status IN ('queued', 'running')",
            (time.time(),),
        )

    # ----------------------------------------------------------- session log
    def add_log(self, kind: str, text: str, meta: dict | None = None) -> dict[str, Any]:
        at = time.time()
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO session_log (at, kind, text, meta) VALUES (?, ?, ?, ?)",
                (at, kind, text, json.dumps(meta or {})),
            )
            self._conn.commit()
            new_id = cur.lastrowid
        return {"id": new_id, "at": at, "kind": kind, "text": text, "meta": meta or {}}

    def list_log(self, limit: int = 1000) -> list[dict[str, Any]]:
        rows = self._all("SELECT * FROM session_log ORDER BY id DESC LIMIT ?", (limit,))
        return [{**dict(r), "meta": json.loads(r["meta"] or "{}")} for r in reversed(rows)]

    def delete_log(self, entry_id: int) -> None:
        self._exec("DELETE FROM session_log WHERE id = ?", (entry_id,))

    def clear_log(self) -> None:
        self._exec("DELETE FROM session_log")


def _asset_row(row: sqlite3.Row) -> dict[str, Any]:
    d = dict(row)
    d["tags"] = json.loads(d.get("tags") or "[]")
    d["mock"] = bool(d.get("mock"))
    for key in ("media", "still", "thumb"):
        rel = d.get(f"{key}_path")
        d[f"{key}_url"] = f"/media/{rel}" if rel else None
    return d

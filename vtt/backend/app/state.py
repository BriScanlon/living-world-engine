"""Authoritative live table state, shared by /table and /dm over WebSockets.

Settings changes are broadcast as small ``state_part`` messages; fog-of-war brush
strokes are broadcast incrementally as ``fog_op`` messages so a long session never
re-sends the whole mask. Fog and the map view (scale / pan) are stored per map, so switching maps keeps
each map's reveal history and its 1" = 5 ft alignment.

Fog ops are normalised to the *map image* (not the screen), so revealed areas stay
glued to the map when the DM rescales or pans it.
"""

from __future__ import annotations

import asyncio
import copy
import logging
import math
from typing import Any

from fastapi import WebSocket

from .db import Database
from .hub import Hub

log = logging.getLogger("vtt.state")

NO_MAP = "__none__"
MAX_FOG_OPS = 50_000

GRID_TYPES = {"hex-pointy", "hex-flat", "square"}
VIEW_MODES = {"cover", "contain", "scale"}
# AI-generated and starter maps are painted at 140 ft across = 28 five-foot squares.
AI_MAP_SQUARES_ACROSS = 28
AI_SOURCES = {"generated", "starter"}


def default_state(default_hex_px: float) -> dict[str, Any]:
    return {
        "map": None,
        "display": {"playbackRate": 1.0},
        "view": default_view(None),
        "grid": {
            "visible": True,
            "type": "hex-pointy",
            "color": "#ffffff",
            "opacity": 0.35,
            "lineWidth": 1.5,      # device pixels
            "showCoords": False,
            "offsetX": 0.0,        # CSS px
            "offsetY": 0.0,
        },
        "calibration": {"hexFlatToFlatPx": default_hex_px, "active": False},
        "fx": {
            "fog": {"enabled": False, "intensity": 0.5},
            "rain": {"enabled": False, "intensity": 0.5},
            "embers": {"enabled": False, "intensity": 0.5},
            "torch": {"enabled": False, "intensity": 0.5},
        },
        "fog": default_fog(),
        "tableViewport": {"w": 2560, "h": 1440, "dpr": 1.5},
        "clients": {"table": 0, "dm": 0},
    }


def default_fog() -> dict[str, Any]:
    return {"enabled": False, "base": "hidden", "ops": []}


def default_view(asset: dict[str, Any] | None) -> dict[str, Any]:
    """How the map image sits on the table.

    mode   cover = fill the screen (crop), contain = letterbox,
           scale = true scale: ``pxPerSquare`` image pixels = one 5 ft square = 1 calibrated inch
    cx, cy map point (0..1 of the image) shown at the centre of the table
    """
    width = (asset or {}).get("width") or 0
    if asset and asset.get("source") in AI_SOURCES and width:
        # Known 140 ft-wide paintings: true scale out of the box.
        return {"mode": "scale", "pxPerSquare": round(width / AI_MAP_SQUARES_ACROSS, 3), "cx": 0.5, "cy": 0.5}
    guess = round(width / AI_MAP_SQUARES_ACROSS, 3) if width else 100.0
    return {"mode": "cover", "pxPerSquare": guess, "cx": 0.5, "cy": 0.5}


PERSISTED_KEYS = ("display", "grid", "calibration", "fx", "tableViewport")


def merge_known(dst: dict[str, Any], patch: Any) -> bool:
    """Merge ``patch`` into ``dst`` accepting only existing keys with compatible types."""
    if not isinstance(patch, dict):
        return False
    changed = False
    for key, value in patch.items():
        if key not in dst:
            continue
        cur = dst[key]
        if isinstance(cur, dict):
            changed |= merge_known(cur, value)
        elif isinstance(cur, bool):
            if isinstance(value, bool) and value != cur:
                dst[key], changed = value, True
        elif isinstance(cur, (int, float)):
            if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
                if value != cur:
                    dst[key], changed = float(value), True
        elif isinstance(cur, str):
            if isinstance(value, str) and value != cur and len(value) < 64:
                dst[key], changed = value, True
    return changed


def _clamp(v: Any, lo: float, hi: float) -> float:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return lo
    if not math.isfinite(f):
        return lo
    return max(lo, min(hi, f))


def sanitize_fog_op(op: Any) -> dict[str, Any] | None:
    """Fog ops use coordinates normalised to the table viewport: x,y in [0,1]; r relative to width."""
    if not isinstance(op, dict):
        return None
    mode = op.get("mode")
    shape = op.get("shape")
    if mode not in ("reveal", "hide"):
        return None
    if shape == "circle":
        return {
            "mode": mode,
            "shape": "circle",
            "x": round(_clamp(op.get("x"), -0.5, 1.5), 5),
            "y": round(_clamp(op.get("y"), -0.5, 1.5), 5),
            "r": round(_clamp(op.get("r"), 0.0005, 2.0), 5),
        }
    if shape == "poly":
        pts = op.get("pts")
        if not isinstance(pts, list) or not 3 <= len(pts) <= 500:
            return None
        clean = []
        for p in pts:
            if not isinstance(p, (list, tuple)) or len(p) != 2:
                return None
            clean.append([round(_clamp(p[0], -0.5, 1.5), 5), round(_clamp(p[1], -0.5, 1.5), 5)])
        return {"mode": mode, "shape": "poly", "pts": clean}
    return None


class TableService:
    def __init__(self, db: Database, hub: Hub, default_hex_px: float):
        self.db = db
        self.hub = hub
        self.state = default_state(default_hex_px)
        self._fog_save_task: asyncio.Task | None = None
        self._view_save_task: asyncio.Task | None = None
        self._load()

    # ------------------------------------------------------------ persistence
    def _load(self) -> None:
        for key in PERSISTED_KEYS:
            saved = self.db.get_setting(key)
            if saved is not None:
                merge_known(self.state[key], saved)
        map_id = self.db.get_setting("current_map_id")
        if map_id:
            self.state["map"] = self.db.get_asset(map_id)
        self.state["fog"] = self._load_fog(self._map_key())
        self.state["view"] = self._load_view(self.state["map"])

    def _map_key(self) -> str:
        m = self.state.get("map")
        return m["id"] if m else NO_MAP

    def _load_fog(self, key: str) -> dict[str, Any]:
        fog = default_fog()
        saved = self.db.get_fog(key)
        if saved:
            fog["enabled"] = bool(saved.get("enabled", False))
            fog["base"] = saved.get("base") if saved.get("base") in ("hidden", "revealed") else "hidden"
            fog["ops"] = [o for o in (sanitize_fog_op(x) for x in saved.get("ops", [])) if o]
        return fog

    def _load_view(self, asset: dict[str, Any] | None) -> dict[str, Any]:
        view = default_view(asset)
        if asset:
            saved = self.db.get_view(asset["id"])
            if saved:
                merge_known(view, saved)
                if view["mode"] not in VIEW_MODES:
                    view["mode"] = "cover"
        return view

    def _save_view_now(self) -> None:
        if self.state.get("map"):
            self.db.set_view(self.state["map"]["id"], self.state["view"])

    def _schedule_view_save(self) -> None:
        if self._view_save_task and not self._view_save_task.done():
            return

        async def _later() -> None:
            await asyncio.sleep(0.8)
            self._save_view_now()

        self._view_save_task = asyncio.create_task(_later())

    def _save_fog_now(self) -> None:
        self.db.set_fog(self._map_key(), self.state["fog"])

    def _schedule_fog_save(self) -> None:
        if self._fog_save_task and not self._fog_save_task.done():
            return

        async def _later() -> None:
            await asyncio.sleep(1.5)
            self._save_fog_now()

        self._fog_save_task = asyncio.create_task(_later())

    def flush(self) -> None:
        self._save_fog_now()
        self._save_view_now()

    # ------------------------------------------------------------------ API
    def snapshot(self) -> dict[str, Any]:
        snap = copy.deepcopy(self.state)
        snap["clients"] = self.hub.counts()
        return snap

    async def set_part(self, key: str, *, exclude: WebSocket | None = None) -> None:
        if key in PERSISTED_KEYS:
            self.db.set_setting(key, self.state[key])
        await self.hub.broadcast(
            {"type": "state_part", "key": key, "value": self.state[key]}, exclude=exclude
        )

    async def broadcast_clients(self) -> None:
        await self.hub.broadcast({"type": "state_part", "key": "clients", "value": self.hub.counts()})

    async def push_map(self, asset: dict[str, Any] | None) -> None:
        """Show a map on the table (or clear it with None). Swaps in that map's fog."""
        self._save_fog_now()
        self._save_view_now()
        self.state["map"] = asset
        self.db.set_setting("current_map_id", asset["id"] if asset else None)
        self.state["fog"] = self._load_fog(self._map_key())
        self.state["view"] = self._load_view(asset)
        await self.set_part("view")
        await self.set_part("map")
        await self.hub.broadcast({"type": "fog_full", "fog": self.state["fog"]})

    async def asset_deleted(self, asset_id: str) -> None:
        if self.state.get("map") and self.state["map"]["id"] == asset_id:
            self.state["map"] = None
            self.db.set_setting("current_map_id", None)
            self.state["fog"] = self._load_fog(NO_MAP)
            self.state["view"] = default_view(None)
            await self.set_part("view")
            await self.set_part("map")
            await self.hub.broadcast({"type": "fog_full", "fog": self.state["fog"]})

    async def handle(self, ws: WebSocket, role: str, msg: dict[str, Any]) -> None:
        kind = msg.get("type")
        if kind == "ping":
            await self.hub.send(ws, {"type": "pong"})
            return
        if kind == "viewport" and role == "table":
            vp = msg.get("viewport") or {}
            patch = {
                "w": _clamp(vp.get("w"), 100, 20000),
                "h": _clamp(vp.get("h"), 100, 20000),
                "dpr": _clamp(vp.get("dpr"), 0.25, 8),
            }
            if merge_known(self.state["tableViewport"], patch):
                await self.set_part("tableViewport")
            return

        # Everything below mutates the table and is DM-only.
        if role != "dm":
            return

        if kind in ("grid", "display", "fx", "view"):
            patch = msg.get("patch")
            if kind == "grid" and isinstance(patch, dict) and patch.get("type") not in (None, *GRID_TYPES):
                patch.pop("type")
            if kind == "view" and isinstance(patch, dict) and patch.get("mode") not in (None, *VIEW_MODES):
                patch.pop("mode")
            if merge_known(self.state[kind], patch):
                self._clamp_settings()
                if kind == "view":
                    self._schedule_view_save()
                # The sender already applied this optimistically; echoing it back
                # mid-drag would make sliders and map panning jitter.
                await self.set_part(kind, exclude=ws)
        elif kind == "calibration":
            cal = self.state["calibration"]
            changed = merge_known(cal, msg.get("patch"))
            nudge = msg.get("nudge")
            if isinstance(nudge, (int, float)) and math.isfinite(nudge):
                cal["hexFlatToFlatPx"] = round(cal["hexFlatToFlatPx"] + float(nudge), 2)
                changed = True
            if changed:
                cal["hexFlatToFlatPx"] = round(_clamp(cal["hexFlatToFlatPx"], 10, 1000), 2)
                await self.set_part("calibration")
        elif kind == "fog_op":
            op = sanitize_fog_op(msg.get("op"))
            if op is None:
                return
            ops = self.state["fog"]["ops"]
            if len(ops) >= MAX_FOG_OPS:
                await self.hub.send(ws, {"type": "toast", "level": "warn",
                                         "message": "Fog history full - use Reveal All / Hide All to reset."})
                return
            ops.append(op)
            self._schedule_fog_save()
            await self.hub.broadcast({"type": "fog_op", "op": op}, exclude=ws)
        elif kind == "fog_reset":
            base = msg.get("base")
            if base in ("hidden", "revealed"):
                fog = self.state["fog"]
                fog["base"], fog["ops"] = base, []
                fog["enabled"] = True
                self._save_fog_now()
                await self.hub.broadcast({"type": "fog_full", "fog": fog})
        elif kind == "fog_enabled":
            # Only the flag changes: never resend ops here, or in-flight brush ops
            # from the sender could be overwritten on its own screen.
            self.state["fog"]["enabled"] = bool(msg.get("enabled"))
            self._save_fog_now()
            await self.hub.broadcast({"type": "fog_meta", "enabled": self.state["fog"]["enabled"]})
        elif kind == "fog_undo":
            ops = self.state["fog"]["ops"]
            n = int(_clamp(msg.get("count", 1), 1, len(ops) or 1))
            if ops:
                del ops[-n:]
                self._save_fog_now()
                await self.hub.broadcast({"type": "fog_full", "fog": self.state["fog"]})

    def _clamp_settings(self) -> None:
        g = self.state["grid"]
        g["opacity"] = _clamp(g["opacity"], 0, 1)
        g["lineWidth"] = _clamp(g["lineWidth"], 0.25, 12)
        g["offsetX"] = _clamp(g["offsetX"], -2000, 2000)
        g["offsetY"] = _clamp(g["offsetY"], -2000, 2000)
        if not isinstance(g["color"], str) or not g["color"].startswith("#"):
            g["color"] = "#ffffff"
        for fx in self.state["fx"].values():
            fx["intensity"] = _clamp(fx["intensity"], 0, 1)
        d = self.state["display"]
        d["playbackRate"] = _clamp(d["playbackRate"], 0.25, 2)
        v = self.state["view"]
        v["pxPerSquare"] = round(_clamp(v["pxPerSquare"], 1, 20000), 4)
        v["cx"] = _clamp(v["cx"], -1, 2)
        v["cy"] = _clamp(v["cy"], -1, 2)

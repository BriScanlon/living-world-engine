"""WebSocket connection hub: tracks /table and /dm clients and fans out messages."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from fastapi import WebSocket

log = logging.getLogger("vtt.hub")


class Hub:
    def __init__(self) -> None:
        self._clients: dict[WebSocket, str] = {}

    def add(self, ws: WebSocket, role: str) -> None:
        self._clients[ws] = role

    def remove(self, ws: WebSocket) -> None:
        self._clients.pop(ws, None)

    def counts(self) -> dict[str, int]:
        out = {"table": 0, "dm": 0}
        for role in self._clients.values():
            out[role] = out.get(role, 0) + 1
        return out

    async def send(self, ws: WebSocket, msg: dict[str, Any]) -> None:
        try:
            await ws.send_text(json.dumps(msg, separators=(",", ":")))
        except Exception:  # noqa: BLE001 - client went away
            self.remove(ws)

    async def broadcast(
        self,
        msg: dict[str, Any],
        *,
        exclude: WebSocket | None = None,
        role: str | None = None,
    ) -> None:
        text = json.dumps(msg, separators=(",", ":"))
        targets = [
            ws for ws, r in list(self._clients.items())
            if ws is not exclude and (role is None or r == role)
        ]
        if not targets:
            return
        results = await asyncio.gather(
            *(ws.send_text(text) for ws in targets), return_exceptions=True
        )
        for ws, res in zip(targets, results):
            if isinstance(res, Exception):
                log.debug("dropping dead websocket: %s", res)
                self.remove(ws)

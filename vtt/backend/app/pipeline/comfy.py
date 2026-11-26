"""Minimal async ComfyUI API client + parameterised workflow templates.

Workflow template format (see workflows/README.md):

    {
      "_comment": ["free text, ignored"],
      "_params": {
        "prompt": {"node": "6", "input": "text", "comment": "..."},
        ...
      },
      "6": {"class_type": "CLIPTextEncode", "inputs": {...}, "_meta": {"title": "..."}},
      ...
    }

Top-level keys starting with "_" are stripped before the graph is queued.
"""

from __future__ import annotations

import asyncio
import copy
import json
import time
import uuid
from pathlib import Path
from typing import Any, Awaitable, Callable

import httpx

ProgressCb = Callable[[float, str], Awaitable[None]]


class ComfyError(RuntimeError):
    pass


def load_workflow(path: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    params = data.get("_params", {})
    graph = {k: v for k, v in data.items() if not k.startswith("_")}
    return graph, params


def apply_params(graph: dict[str, Any], params: dict[str, Any], values: dict[str, Any]) -> dict[str, Any]:
    g = copy.deepcopy(graph)
    for name, value in values.items():
        if value is None:
            continue
        spec = params.get(name)
        if not spec:
            continue
        targets = spec if isinstance(spec, list) else [spec]
        for t in targets:
            node = g.get(str(t["node"]))
            if node is None:
                raise ComfyError(f"Workflow param '{name}' points at missing node {t['node']}")
            node.setdefault("inputs", {})[t["input"]] = value
    return g


class ComfyClient:
    def __init__(self, base_url: str, timeout_s: float = 1800.0):
        if not base_url:
            raise ComfyError("ComfyUI URL is not configured")
        self.base = base_url.rstrip("/")
        self.timeout_s = timeout_s
        self.client_id = uuid.uuid4().hex

    async def upload_image(self, data: bytes, filename: str) -> str:
        async with httpx.AsyncClient(timeout=120) as c:
            resp = await c.post(
                f"{self.base}/upload/image",
                files={"image": (filename, data, "image/png")},
                data={"overwrite": "true", "type": "input"},
            )
        if resp.status_code >= 400:
            raise ComfyError(f"ComfyUI upload failed ({resp.status_code}): {resp.text[:300]}")
        info = resp.json()
        sub = info.get("subfolder") or ""
        return f"{sub}/{info['name']}" if sub else info["name"]

    async def queue(self, graph: dict[str, Any]) -> str:
        async with httpx.AsyncClient(timeout=60) as c:
            resp = await c.post(f"{self.base}/prompt", json={"prompt": graph, "client_id": self.client_id})
        if resp.status_code >= 400:
            raise ComfyError(f"ComfyUI rejected workflow ({resp.status_code}): {resp.text[:800]}")
        return resp.json()["prompt_id"]

    async def wait(self, prompt_id: str, on_progress: ProgressCb | None = None, expected_s: float = 90) -> dict:
        start = time.monotonic()
        async with httpx.AsyncClient(timeout=30) as c:
            while True:
                elapsed = time.monotonic() - start
                if elapsed > self.timeout_s:
                    raise ComfyError(f"ComfyUI job {prompt_id} timed out after {int(elapsed)}s")
                try:
                    resp = await c.get(f"{self.base}/history/{prompt_id}")
                    entry = resp.json().get(prompt_id) if resp.status_code == 200 else None
                except (httpx.HTTPError, ValueError):
                    entry = None
                if entry:
                    status = entry.get("status", {})
                    if status.get("status_str") == "error":
                        msgs = [m for m in status.get("messages", []) if m and m[0] == "execution_error"]
                        detail = msgs[0][1].get("exception_message") if msgs else "unknown error"
                        raise ComfyError(f"ComfyUI execution error: {detail}")
                    if status.get("completed") or entry.get("outputs"):
                        return entry
                if on_progress:
                    # ComfyUI's /history has no step progress; estimate on an asymptotic curve.
                    est = 1 - pow(2.718, -elapsed / max(expected_s, 1))
                    await on_progress(min(est, 0.97), f"{int(elapsed)}s elapsed")
                await asyncio.sleep(1.5)

    async def download_outputs(self, entry: dict) -> list[tuple[str, bytes]]:
        files: list[tuple[str, bytes]] = []
        async with httpx.AsyncClient(timeout=300) as c:
            for node_out in entry.get("outputs", {}).values():
                for key in ("videos", "gifs", "images"):
                    for f in node_out.get(key, []) or []:
                        if not isinstance(f, dict) or f.get("type") not in (None, "output"):
                            continue
                        resp = await c.get(
                            f"{self.base}/view",
                            params={"filename": f["filename"], "subfolder": f.get("subfolder", ""), "type": "output"},
                        )
                        if resp.status_code == 200:
                            files.append((f["filename"], resp.content))
        if not files:
            raise ComfyError("ComfyUI finished but produced no output files")
        return files

    async def run(
        self, graph: dict[str, Any], on_progress: ProgressCb | None = None, expected_s: float = 90
    ) -> list[tuple[str, bytes]]:
        prompt_id = await self.queue(graph)
        entry = await self.wait(prompt_id, on_progress, expected_s)
        return await self.download_outputs(entry)


VIDEO_EXTS = (".mp4", ".webm", ".mov", ".mkv")
IMAGE_EXTS = (".png", ".webp", ".jpg", ".jpeg")


def pick_output(files: list[tuple[str, bytes]], exts: tuple[str, ...]) -> tuple[str, bytes] | None:
    for name, data in files:
        if name.lower().endswith(exts):
            return name, data
    return None

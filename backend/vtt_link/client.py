"""Thin HTTP client for the virtual tabletop (``vtt/``), called server-side.

Inside docker compose the VTT is ``http://vtt:8000``; set ``VTT_URL`` when running elsewhere.
"""
from __future__ import annotations

from typing import Any

import httpx

from config import get_settings


class VTTError(RuntimeError):
    pass


def base_url() -> str:
    return get_settings().vtt_url.rstrip("/")


def configured() -> bool:
    return bool(base_url())


def _call(method: str, path: str, **kw) -> Any:
    if not configured():
        raise VTTError("The virtual tabletop isn't linked (set VTT_URL)")
    try:
        r = httpx.request(method, base_url() + path, timeout=get_settings().vtt_timeout_seconds, **kw)
    except httpx.HTTPError as e:
        raise VTTError(f"Can't reach the virtual tabletop at {base_url()}: {e}") from e
    if r.status_code >= 400:
        try:
            detail = r.json().get("detail")
        except ValueError:
            detail = r.text[:200]
        raise VTTError(f"VTT said {r.status_code}: {detail}")
    return r.json() if r.content else None


def status() -> dict[str, Any]:
    if not configured():
        return {"configured": False, "reachable": False}
    try:
        info = _call("GET", "/api/config")
        return {"configured": True, "reachable": True, "mock": info.get("mock"), "version": info.get("version")}
    except VTTError as e:
        return {"configured": True, "reachable": False, "error": str(e)}


def maps() -> list[dict[str, Any]]:
    return _call("GET", "/api/maps", params={"type": "map"})


def get_map(map_id: str) -> dict[str, Any]:
    return _call("GET", f"/api/maps/{map_id}")


def map_view(map_id: str) -> dict[str, Any]:
    return _call("GET", f"/api/maps/{map_id}/view")


def push_map(map_id: str) -> None:
    _call("POST", f"/api/maps/{map_id}/push")


def send_scene(scene: dict[str, Any]) -> None:
    _call("POST", "/api/scene", json=scene)


def clear_scene() -> None:
    _call("DELETE", "/api/scene")


def media(path: str) -> httpx.Response:
    """Fetch a VTT media file (thumbnail / still / video) to re-serve same-origin to the dashboard."""
    if not configured():
        raise VTTError("The virtual tabletop isn't linked (set VTT_URL)")
    if not path.startswith(("maps/", "thumbs/", "starter_pack/")) or ".." in path:
        raise VTTError("Not a VTT media path")
    try:
        r = httpx.get(f"{base_url()}/media/{path}", timeout=30)
    except httpx.HTTPError as e:
        raise VTTError(str(e)) from e
    if r.status_code != 200:
        raise VTTError(f"VTT media {r.status_code}")
    return r


def generate_map(description: str, *, title: str | None = None, tags: list[str] | None = None,
                 style: str = "", animate: bool = False) -> dict[str, Any]:
    """Queue an AI battle map on the VTT. Returns the job (poll with :func:`job`)."""
    return _call("POST", "/api/generate", json={"description": description[:600], "title": title, "style": style,
                                                "tags": tags or [], "animate": animate, "push": False})


def make_token(name: str, description: str, tags: list[str] | None = None) -> dict[str, Any]:
    """Queue a round top-down portrait token for a character."""
    return _call("POST", "/api/tokens", json={"name": name[:80], "description": description[:600], "tags": tags or []})


def job(job_id: str) -> dict[str, Any]:
    return _call("GET", f"/api/jobs/{job_id}")


def tokens() -> list[dict[str, Any]]:
    return _call("GET", "/api/maps", params={"type": "token"})


def session_log() -> list[dict[str, Any]]:
    return _call("GET", "/api/session-log")["entries"]

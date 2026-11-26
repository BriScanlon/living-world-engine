"""Tokens on the table: portraits and markers that sit on the map, per map.

Positions are normalised to the map image (0..1), like fog, so tokens stay put when the DM rescales or pans
the map. ``size`` is in 5 ft squares. Hidden tokens are the DM's alone: the table socket never receives them.
The token's picture is always looked up from the asset library, never taken from a client.
"""

from __future__ import annotations

import math
import re
import uuid
from typing import Any, Callable

MAX_TOKENS = 200
SIZES = (0.5, 1, 2, 3, 4, 6)  # tiny, small/medium, large, huge, gargantuan, colossal-ish
PALETTE = ("#f59e0b", "#38bdf8", "#f472b6", "#a3e635", "#c084fc", "#fb7185", "#2dd4bf", "#facc15")
_COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")


def _num(v: Any, lo: float, hi: float, default: float) -> float:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(f):
        return default
    return min(hi, max(lo, f))


def _size(v: Any) -> float:
    s = _num(v, 0.5, 6, 1)
    return min(SIZES, key=lambda x: abs(x - s))


def clean_patch(raw: Any) -> dict[str, Any]:
    """The editable fields of a token, validated. Unknown keys are dropped."""
    if not isinstance(raw, dict):
        return {}
    out: dict[str, Any] = {}
    if "x" in raw:
        out["x"] = round(_num(raw["x"], 0, 1, 0.5), 5)
    if "y" in raw:
        out["y"] = round(_num(raw["y"], 0, 1, 0.5), 5)
    if "size" in raw:
        out["size"] = _size(raw["size"])
    if "hidden" in raw:
        out["hidden"] = bool(raw["hidden"])
    if isinstance(raw.get("name"), str):
        out["name"] = raw["name"].strip()[:60] or "Token"
    if isinstance(raw.get("color"), str) and _COLOR.match(raw["color"]):
        out["color"] = raw["color"].lower()
    return out


def new_token(raw: Any, *, count: int, asset_lookup: Callable[[str], dict | None],
              centre: tuple[float, float] = (0.5, 0.5)) -> dict[str, Any] | None:
    """Build a token from a client request. ``asset_id`` must name a token in the library (or be absent)."""
    if not isinstance(raw, dict):
        return None
    asset = None
    if raw.get("asset_id"):
        asset = asset_lookup(str(raw["asset_id"]))
        if not asset or asset.get("asset_type") != "token":
            return None
    # spread new tokens a little so a batch doesn't land in one pile
    ring = count % 8
    dx, dy = (0.03 * math.cos(ring * math.pi / 4), 0.03 * math.sin(ring * math.pi / 4)) if count else (0, 0)
    token = {
        "id": uuid.uuid4().hex[:10],
        "name": (asset or {}).get("title") or "Token",
        "asset_id": asset["id"] if asset else None,
        "x": round(_num(centre[0] + dx, 0, 1, 0.5), 5),
        "y": round(_num(centre[1] + dy, 0, 1, 0.5), 5),
        "size": 1,
        "hidden": False,
        "color": PALETTE[count % len(PALETTE)],
    }
    token.update(clean_patch(raw))
    return with_image(token, asset_lookup)


def with_image(token: dict[str, Any], asset_lookup: Callable[[str], dict | None]) -> dict[str, Any]:
    asset = asset_lookup(token["asset_id"]) if token.get("asset_id") else None
    token["img"] = (asset.get("media_url") or asset.get("thumb_url")) if asset else None
    if token.get("asset_id") and not asset:
        token["asset_id"] = None  # the picture was deleted: keep the token as a coloured disc
    return token


def load(saved: Any, asset_lookup: Callable[[str], dict | None]) -> list[dict[str, Any]]:
    out = []
    for t in saved if isinstance(saved, list) else []:
        if not isinstance(t, dict) or not isinstance(t.get("id"), str):
            continue
        base = {"id": t["id"][:20], "name": "Token", "asset_id": t.get("asset_id"), "x": 0.5, "y": 0.5,
                "size": 1, "hidden": False, "color": PALETTE[0]}
        base.update(clean_patch(t))
        out.append(with_image(base, asset_lookup))
    return out[:MAX_TOKENS]


def stored(tokens: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{k: v for k, v in t.items() if k != "img"} for t in tokens]


def visible(tokens: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [t for t in tokens if not t.get("hidden")]

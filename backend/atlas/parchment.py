"""Parchment tiles for the DM atlas (epic #58, phase 4c #57): the party map's drawn look, as Web Mercator raster
tiles the globe lays over its own layers.

Each tile is drawn by :func:`atlas.party_map.draw_world` (coast, ripples, washes, borders, terrain symbols, rivers,
roads, bridges, cliffs) plus settlement symbols, with everything that could differ between neighbouring tiles held
fixed: the cell size is set per zoom, nothing is wobbled or thinned, and each tile draws a margin around itself and
keeps only the symbols whose foot falls in its own square plus a band both neighbours draw alike. Lettering stays
with the atlas (MapLibre places it, with its own collisions). Tiles are cached under the atlas folder; a re-import
clears them.

Tiles are drawn the first time they are asked for (about a second each), and also ahead of time:
:func:`warm_in_background` draws every tile over land up to ``parchment_warm_zoom`` in one background thread per look (the
Parchment look after an import and at start-up; either look when a world's tiles in it are first asked for), so the regional zooms are ready before anyone
zooms in. It steps aside while a viewer's own tiles are drawing.
"""
from __future__ import annotations

import io
import json
import logging
import math
import os
import shutil
import threading
import time
from functools import lru_cache
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageDraw
from shapely.geometry import box, shape

from atlas import cartography as carto
from atlas import emblems
from atlas.service import atlas_path

log = logging.getLogger(__name__)

TILE = 512  # pixels served per tile (shown as 256 css px: crisp on high-density screens)
RENDER = 2  # drawn at twice that, then scaled down: anti-aliasing
SS = 4.0  # line and symbol scale while drawing (= the party map's look at the size shown)
MIN_ZOOM, MAX_ZOOM = 3, 11
# The globe asks for tiles about a zoom above the one it shows (high-density screens, the globe's curve), so tile
# zooms here run one ahead of the atlas's label zooms.
TOWN_SYMBOLS_FROM = 8  # towns drawn from this tile zoom (about zoom 7 on the globe, with their names); capitals always
VERSION = "v3"  # bump when the drawing changes: older cached tiles are left behind


def terrain_density(z: int) -> float:
    """Fewer terrain symbols far out, the full density from tile zoom 9 (about zoom 8 on the globe)."""
    return min(1.0, max(0.3, 0.3 + 0.175 * (z - 5)))


class MercatorTile:
    """Web Mercator pixels of one tile drawn with a margin: (0, 0) is the margin's top-left corner."""

    def __init__(self, z: int, x: int, y: int, size: int, margin: int):
        self.world = size * (2 ** z)
        self.ox, self.oy = x * size - margin, y * size - margin
        self.size, self.margin = size, margin
        lat = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * (y + 0.5) / 2 ** z))))
        self._km_px = 40075.016 * math.cos(math.radians(lat)) / self.world

    def arr(self, a: np.ndarray) -> np.ndarray:
        lat = np.clip(a[:, 1], -85.0, 85.0)
        wx = (a[:, 0] + 180.0) / 360.0 * self.world
        wy = (1 - np.arcsinh(np.tan(np.radians(lat))) / math.pi) / 2 * self.world
        return np.column_stack([wx - self.ox, wy - self.oy])

    def __call__(self, lon: float, lat: float) -> tuple[float, float]:
        x, y = self.arr(np.array([[lon, lat]], dtype=float))[0]
        return float(x), float(y)

    def inverse(self, px: float, py: float) -> tuple[float, float]:
        wx, wy = px + self.ox, py + self.oy
        lon = wx / self.world * 360 - 180
        lat = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * wy / self.world))))
        return lon, lat

    def km_per_px(self) -> float:
        return self._km_px


def _layer(slug: str, name: str) -> list[dict[str, Any]]:
    p = atlas_path(slug) / f"{name}.geojson"
    return json.loads(p.read_text(encoding="utf-8")).get("features", []) if p.exists() else []


@lru_cache(maxsize=16)
def _region(slug: str, stamp: float) -> dict[str, Any]:
    """What every tile of a world shares: its box, cell size (km), provinces and settlements."""
    from shapely.ops import unary_union
    land = unary_union([shape(f["geometry"]).buffer(0) for f in _layer(slug, "land")])
    relief = _layer(slug, "relief")
    kmf = carto.KmFrame((land.bounds[1] + land.bounds[3]) / 2) if not land.is_empty else carto.KmFrame(0)
    cell_km = math.sqrt(kmf.to_km(land).area / max(1, len(relief))) if relief and not land.is_empty else 20.0
    minx, miny, maxx, maxy = land.bounds if not land.is_empty else (0, 0, 0, 0)
    pad = 2.0
    provinces = {f["properties"].get("pid"): shape(f["geometry"]) for f in _layer(slug, "provinces")}
    burgs = [(f["geometry"]["coordinates"], f["properties"]) for f in _layer(slug, "burgs")]
    return {"bbox": (minx - pad, miny - pad, maxx + pad, maxy + pad), "cell_km": cell_km, "provinces": provinces,
            "burgs": burgs}


def cache_dir(slug: str) -> Path:
    return atlas_path(slug) / "parchment"


_asked = 0  # viewers' tiles being drawn right now: the warmer waits for them
_asked_lock = threading.Lock()


def tile_file(slug: str, z: int, x: int, y: int, style: str) -> Path:
    return cache_dir(slug) / VERSION / style / str(z) / str(x) / f"{y}.webp"


def render_tile(slug: str, z: int, x: int, y: int, style: str = "colour", *, background: bool = False) -> bytes:
    """One parchment tile as WebP (transparent outside the world's region), cached on disk. ``background`` is the
    warmer drawing ahead; otherwise a viewer is waiting, and the warmer pauses until this one is done."""
    from atlas.party_map import STYLES
    style = style if style in STYLES else "colour"
    out = tile_file(slug, z, x, y, style)
    if out.exists():
        return out.read_bytes()
    global _asked
    if not background:
        with _asked_lock:
            _asked += 1
    try:
        data = _draw(slug, z, x, y, style)
    finally:
        if not background:
            with _asked_lock:
                _asked -= 1
    out.parent.mkdir(parents=True, exist_ok=True)
    part = out.with_name(f"{out.name}.{os.getpid()}.{threading.get_ident()}.part")
    part.write_bytes(data)
    os.replace(part, out)  # whole files only: the warmer and a viewer may draw the same tile at once
    return data


def _draw(slug: str, z: int, x: int, y: int, style: str) -> bytes:
    from atlas.party_map import STYLES, draw_world
    land_file = atlas_path(slug) / "land.geojson"
    reg = _region(slug, land_file.stat().st_mtime if land_file.exists() else 0.0)
    T = TILE * RENDER
    probe = MercatorTile(z, x, y, T, 0)
    lon0, lat1 = probe.inverse(0, 0)
    lon1, lat0 = probe.inverse(T, T)
    bx0, by0, bx1, by1 = reg["bbox"]
    if z < MIN_ZOOM or z > MAX_ZOOM or lon1 < bx0 or lon0 > bx1 or lat1 < by0 or lat0 > by1:
        data = _blank()
    else:
        cell_px = reg["cell_km"] / probe.km_per_px()
        margin = int(min(700, max(160, 3 * 0.8 * cell_px if STYLES[style]["washes"] else 0)))
        proj = MercatorTile(z, x, y, T, margin)
        W = H = T + 2 * margin
        band = int(60 * SS)
        towns = [(c, p) for c, p in reg["burgs"] if p.get("capital") or z >= TOWN_SYMBOLS_FROM]
        avoid = []
        marks = []
        for c, p in towns:
            px_, py_ = proj(c[0], c[1])
            if -band <= px_ - margin <= T + band and -band <= py_ - margin <= T + band:
                emblem = "castle" if p.get("capital") else "town"
                k = 1.0 if p.get("capital") else 0.8
                half = emblems.SIZE[emblem] * SS * k * 0.5
                avoid.append((px_ - half * 1.3, py_ - half * 1.4, px_ + half * 1.3, py_ + half * 1.2))
                marks.append((emblem, px_, py_, k, p.get("port")))
        img, *_ = draw_world(proj, W, H, slug, STYLES[style], carto.seed_of(slug), SS,
                             provinces=reg["provinces"], avoid_boxes=avoid, cell_px=cell_px, budget=10 ** 7,
                             wobble=False, keep=(margin - band, margin - band, margin + T + band, margin + T + band),
                             density=terrain_density(z))
        d = ImageDraw.Draw(img, "RGBA")
        for emblem, px_, py_, k, port in marks:
            emblems.draw(d, emblem, px_, py_, SS * k)
            if port:
                emblems.draw(d, "anchor", px_ + emblems.SIZE[emblem] * SS * k * 0.5 + 8 * SS * k, py_, SS * k)
        tile = img.crop((margin, margin, margin + T, margin + T))
        # outside the region: transparent, so the globe's own sea shows
        alpha = Image.new("L", (T, T), 0)
        rx0, ry1 = proj(bx0, by0)
        rx1, ry0 = proj(bx1, by1)
        ImageDraw.Draw(alpha).rectangle((rx0 - margin, ry0 - margin, rx1 - margin, ry1 - margin), fill=255)
        tile.putalpha(alpha)
        tile = tile.resize((TILE, TILE), Image.LANCZOS)
        buf = io.BytesIO()
        tile.save(buf, "WEBP", quality=86, method=4)
        data = buf.getvalue()
    return data


@lru_cache(maxsize=1)
def _blank() -> bytes:
    buf = io.BytesIO()
    Image.new("RGBA", (TILE, TILE), (0, 0, 0, 0)).save(buf, "WEBP", lossless=True)
    return buf.getvalue()


# --- drawing ahead -------------------------------------------------------------------------------------------------

_warming: dict[str, dict[str, Any]] = {}  # atlas folder + version + look -> {"running", "done", "total", "finished"}
_warm_lock = threading.Lock()


@lru_cache(maxsize=16)
def _land(slug: str, stamp: float):
    """The world's land, simplified for quick tests of which tiles touch it: (prepared geometry, bounds)."""
    from shapely.ops import unary_union
    from shapely.prepared import prep
    g = unary_union([shape(f["geometry"]).buffer(0) for f in _layer(slug, "land")]).simplify(0.05)
    return (prep(g), g.bounds) if not g.is_empty else (None, None)


def tiles_over_land(slug: str, z: int) -> list[tuple[int, int]]:
    """The tiles at zoom ``z`` with land on them (or within a quarter tile), nearest the land's middle first."""
    land_file = atlas_path(slug) / "land.geojson"
    if not land_file.exists() or z < MIN_ZOOM or z > MAX_ZOOM:
        return []
    land, bounds = _land(slug, land_file.stat().st_mtime)
    if land is None:
        return []
    probe = MercatorTile(z, 0, 0, 1, 0)
    (ax, ay), (bx, by) = probe(bounds[0], bounds[3]), probe(bounds[2], bounds[1])
    out = []
    for tx in range(max(0, int(ax) - 1), min(2 ** z, int(bx) + 2)):
        for ty in range(max(0, int(ay) - 1), min(2 ** z, int(by) + 2)):
            w, n = probe.inverse(tx - 0.25, ty - 0.25)
            e, s = probe.inverse(tx + 1.25, ty + 1.25)
            if land.intersects(box(w, s, e, n)):
                out.append((tx, ty))
    mx, my = (ax + bx) / 2, (ay + by) / 2
    return sorted(out, key=lambda c: (c[0] + 0.5 - mx) ** 2 + (c[1] + 0.5 - my) ** 2)


def warm_plan(slug: str, max_zoom: int, cap: int) -> list[tuple[int, int, int]]:
    """The tiles (z, x, y) to draw ahead, in order: zoom by zoom (the far views first), at most ``cap``, whole
    zooms only."""
    plan: list[tuple[int, int, int]] = []
    for z in range(MIN_ZOOM, min(max_zoom, MAX_ZOOM) + 1):
        tiles = tiles_over_land(slug, z)
        if len(plan) + len(tiles) > cap:
            break  # half a zoom drawn ahead helps nobody: the view would still wait
        plan += [(z, x, y) for x, y in tiles]
    return plan


def warm(slug: str, max_zoom: int, cap: int, style: str = "colour", *, status: dict[str, Any] | None = None) -> int:
    """Draw ahead, in one look, every tile of :func:`warm_plan` not yet cached; returns how many were drawn."""
    status = status if status is not None else {}
    for d in cache_dir(slug).glob("*"):
        if d.is_dir() and d.name != VERSION:
            shutil.rmtree(d, ignore_errors=True)  # older drawings: nobody asks for them any more
    plan = warm_plan(slug, max_zoom, cap)
    status.update(total=len(plan), done=0)
    drawn = 0
    for z, x, y in plan:
        if not tile_file(slug, z, x, y, style).exists():
            while _asked:
                time.sleep(0.05)
            if not (atlas_path(slug) / "land.geojson").exists():
                break  # the world went away
            render_tile(slug, z, x, y, style, background=True)
            drawn += 1
        status["done"] += 1
    return drawn


def _key(slug: str, style: str) -> str:
    return f"{atlas_path(slug)}|{VERSION}|{style}"


def warm_in_background(slug: str, style: str = "colour") -> bool:
    """Start drawing ``slug``'s tiles ahead in one look, in a background thread, unless that is turned off
    (``parchment_warm_zoom`` below 3), already running, or done for this drawing. Returns whether it started.
    Imports and start-up draw ``colour`` (the Parchment look); ink is drawn ahead once an ink tile is asked for."""
    from atlas.party_map import STYLES
    from config import get_settings
    s = get_settings()
    if style not in STYLES or s.parchment_warm_zoom < MIN_ZOOM or not (atlas_path(slug) / "land.geojson").exists():
        return False
    with _warm_lock:
        st = _warming.get(_key(slug, style))
        if st and (st["running"] or st["finished"]):
            return False
        st = _warming[_key(slug, style)] = {"running": True, "finished": False, "done": 0, "total": 0}

    def run() -> None:
        t0 = time.monotonic()
        try:
            n = warm(slug, s.parchment_warm_zoom, s.parchment_warm_tiles, style, status=st)
            log.info("Parchment (%s) tiles for %s drawn ahead: %s new, %s in all, %.0f s", style, slug, n,
                     st["total"], time.monotonic() - t0)
            st["finished"] = True
        except Exception:  # noqa: BLE001 - tiles are still drawn when asked for
            log.exception("Drawing parchment (%s) tiles ahead for %s failed", style, slug)
        finally:
            st["running"] = False

    threading.Thread(target=run, name=f"parchment-warm-{slug}-{style}", daemon=True).start()
    return True


def forget_warm(slug: str) -> None:
    """A re-import: the next :func:`warm_in_background` starts over."""
    with _warm_lock:
        for k in [k for k in _warming if k.startswith(f"{atlas_path(slug)}|")]:
            _warming.pop(k)


def warm_status(slug: str) -> dict[str, dict[str, Any]]:
    """How far drawing ahead has got, per look started: ``{look: {running, done, total}}``."""
    pre = f"{atlas_path(slug)}|{VERSION}|"
    return {k[len(pre):]: {f: st[f] for f in ("running", "done", "total")}
            for k, st in list(_warming.items()) if k.startswith(pre)}

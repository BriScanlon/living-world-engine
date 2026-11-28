"""Parchment tiles for the DM atlas (epic #58, phase 4c #57): the party map's drawn look, as Web Mercator raster
tiles the globe lays over its own layers.

Each tile is drawn by :func:`atlas.party_map.draw_world` (coast, ripples, washes, borders, terrain symbols, rivers,
roads, bridges, cliffs) plus settlement symbols, with everything that could differ between neighbouring tiles held
fixed: the cell size is set per zoom, nothing is wobbled or thinned, and each tile draws a margin around itself and
keeps only the symbols whose foot falls in its own square plus a band both neighbours draw alike. Lettering stays
with the atlas (MapLibre places it, with its own collisions). Tiles are cached under the atlas folder; a re-import
clears them.
"""
from __future__ import annotations

import io
import json
import math
from functools import lru_cache
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageDraw
from shapely.geometry import box, shape

from atlas import cartography as carto
from atlas import emblems
from atlas.service import atlas_path

TILE = 512  # pixels served per tile (shown as 256 css px: crisp on high-density screens)
RENDER = 2  # drawn at twice that, then scaled down: anti-aliasing
SS = 4.0  # line and symbol scale while drawing (= the party map's look at the size shown)
MIN_ZOOM, MAX_ZOOM = 3, 11
TOWN_SYMBOLS_FROM = 7  # towns drawn from this zoom; capitals always
VERSION = "v2"  # bump when the drawing changes: older cached tiles are left behind


def terrain_density(z: int) -> float:
    """Fewer terrain symbols far out (a whole region at z5 shows about half), the full density from z8."""
    return min(1.0, max(0.3, 0.3 + 0.175 * (z - 4)))


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


def render_tile(slug: str, z: int, x: int, y: int, style: str = "colour") -> bytes:
    """One parchment tile as WebP (transparent outside the world's region), cached on disk."""
    from atlas.party_map import STYLES, draw_world
    style = style if style in STYLES else "colour"
    out = cache_dir(slug) / VERSION / style / str(z) / str(x) / f"{y}.webp"
    if out.exists():
        return out.read_bytes()
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
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(data)
    return data


@lru_cache(maxsize=1)
def _blank() -> bytes:
    buf = io.BytesIO()
    Image.new("RGBA", (TILE, TILE), (0, 0, 0, 0)).save(buf, "WEBP", lossless=True)
    return buf.getvalue()

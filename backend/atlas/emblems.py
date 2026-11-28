"""Settlement and landmark symbols (epic #58, phase 3 #56), inked like the terrain symbols (:mod:`atlas.symbols`).

Settlements by rank: a castle for a capital, a walled city, a cluster of houses for a town, a cottage or two for a
village, and an anchor beside a port. Landmarks from a place's type, then its name: ruins, towers, temples and
abbeys, barrows and tombs, standing stones, caves and mines, lighthouses, inns, manors, and crossed swords where a
battle was fought. All drawn in code, centred on the place.
"""
from __future__ import annotations

import math
import re

from PIL import ImageDraw

INK = (40, 30, 22, 255)
PAPER = (238, 228, 198, 255)
DARK = (40, 30, 22, 255)

SETTLEMENTS = {"capital": "castle", "city": "city", "town": "town", "village": "village"}
LANDMARK_WORDS = [  # (pattern on the name, emblem), first match wins
    (r"\b(ruin|ruins|ruined)\b", "ruin"),
    (r"\b(lighthouse|beacon)\b", "lighthouse"),
    (r"\b(tower|keep|spire|watchtower|citadel)\b", "tower"),
    (r"\b(temple|abbey|monastery|priory|shrine|chapel|church|cathedral|sanctum|sanctuary|friary|convent)\b", "temple"),
    (r"\b(barrow|barrows|tomb|tombs|cairn|mound|crypt|grave|graves|tumulus|necropolis)\b", "barrow"),
    (r"\b(stones|stone circle|henge|menhir|monolith|standing stone|dolmen)\b", "stones"),
    (r"\b(cave|caves|cavern|caverns|mine|mines|delve|dungeon|lair|grotto|pit|hollow|den)\b", "cave"),
    (r"\b(inn|tavern|alehouse)\b", "inn"),
    (r"\b(manor|hall|lodge|estate|villa)\b", "manor"),
]
KIND_EMBLEM = {"ruin": "ruin", "tavern": "inn", "dungeon": "cave", "lair": "cave", "temple": "temple",
               "shrine": "temple", "tower": "tower", "manor": "manor"}


def emblem_for(kind: str | None, name: str | None) -> str | None:
    """Which symbol a place gets: settlements by rank, landmarks by type then by name; None for plain places."""
    kind = (kind or "").lower()
    if kind in SETTLEMENTS:
        return SETTLEMENTS[kind]
    if kind in KIND_EMBLEM:
        return KIND_EMBLEM[kind]
    low = (name or "").lower()
    for pat, emblem in LANDMARK_WORDS:
        if re.search(pat, low):
            return emblem
    return None


def _w(s: float) -> int:
    return max(1, round(s / 14))


def _house(d: ImageDraw.ImageDraw, x: float, y: float, w: float, h: float, lw: int) -> None:
    """A house: walls and a pitched roof, its foot at (x, y)."""
    d.rectangle([x - w / 2, y - h, x + w / 2, y], fill=PAPER, outline=INK, width=lw)
    d.polygon([(x - w * 0.62, y - h), (x, y - h * 1.75), (x + w * 0.62, y - h)], fill=PAPER, outline=INK, width=lw)


def _crenels(d: ImageDraw.ImageDraw, x0: float, x1: float, y: float, n: int, h: float, lw: int) -> None:
    step = (x1 - x0) / (2 * n - 1)
    for i in range(n):
        a = x0 + 2 * i * step
        d.rectangle([a, y - h, a + step, y], fill=PAPER, outline=INK, width=lw)


def castle(d, x, y, s):
    lw = _w(s)
    body = (x - s * 0.32, y - s * 0.2, x + s * 0.32, y + s * 0.32)
    d.rectangle(body, fill=PAPER, outline=INK, width=lw)
    _crenels(d, body[0], body[2], body[1], 4, s * 0.09, lw)
    for sx in (-1, 1):  # two flanking towers
        tx = x + sx * s * 0.38
        d.rectangle([tx - s * 0.13, y - s * 0.45, tx + s * 0.13, y + s * 0.32], fill=PAPER, outline=INK, width=lw)
        _crenels(d, tx - s * 0.13, tx + s * 0.13, y - s * 0.45, 2, s * 0.08, lw)
    d.line([(x + s * 0.38, y - s * 0.53), (x + s * 0.38, y - s * 0.72)], fill=INK, width=lw)  # a pennant
    d.polygon([(x + s * 0.38, y - s * 0.72), (x + s * 0.56, y - s * 0.66), (x + s * 0.38, y - s * 0.6)], fill=INK)
    d.chord([x - s * 0.09, y + s * 0.08, x + s * 0.09, y + s * 0.44], 180, 360, fill=DARK)  # the gate
    d.rectangle([x - s * 0.09, y + s * 0.26, x + s * 0.09, y + s * 0.32], fill=DARK)


def city(d, x, y, s):
    lw = _w(s)
    r = s * 0.42
    d.ellipse([x - r, y - r * 0.75, x + r, y + r * 0.75], fill=PAPER, outline=INK, width=lw * 2)
    for a in range(0, 360, 60):  # towers on the wall
        tx, ty = x + r * math.cos(math.radians(a)), y + r * 0.75 * math.sin(math.radians(a))
        q = s * 0.07
        d.rectangle([tx - q, ty - q, tx + q, ty + q], fill=PAPER, outline=INK, width=lw)
    for dx, dy, k in ((-0.14, 0.12, 1.0), (0.12, 0.14, 0.9), (0.0, -0.06, 1.15)):
        _house(d, x + dx * s, y + dy * s, s * 0.14 * k, s * 0.12 * k, lw)


def town(d, x, y, s):
    lw = _w(s)
    for dx, dy, k in ((-0.2, 0.18, 0.9), (0.2, 0.2, 0.85), (0.0, 0.06, 1.1)):
        _house(d, x + dx * s, y + dy * s, s * 0.22 * k, s * 0.18 * k, lw)


def village(d, x, y, s):
    lw = _w(s)
    _house(d, x - s * 0.1, y + s * 0.15, s * 0.2, s * 0.16, lw)
    _house(d, x + s * 0.14, y + s * 0.18, s * 0.15, s * 0.12, lw)


def anchor(d, x, y, s):
    lw = _w(s)
    r = s * 0.07
    d.ellipse([x - r, y - s * 0.32 - r, x + r, y - s * 0.32 + r], outline=INK, width=lw)
    d.line([(x, y - s * 0.25), (x, y + s * 0.28)], fill=INK, width=lw)
    d.line([(x - s * 0.15, y - s * 0.16), (x + s * 0.15, y - s * 0.16)], fill=INK, width=lw)
    d.arc([x - s * 0.24, y - s * 0.05, x + s * 0.24, y + s * 0.3], 20, 160, fill=INK, width=lw)


def ruin(d, x, y, s):
    lw = _w(s)
    for dx, h in ((-0.26, 0.42), (-0.06, 0.28), (0.16, 0.5)):  # broken columns and walls
        a, b = x + dx * s, x + (dx + 0.12) * s
        top = y + s * 0.25 - h * s
        d.polygon([(a, y + s * 0.25), (a, top), (a + s * 0.04, top + s * 0.06), (b - s * 0.03, top - s * 0.04),
                   (b, top + s * 0.05), (b, y + s * 0.25)], fill=PAPER, outline=INK)
    d.rectangle([x + s * 0.3, y + s * 0.16, x + s * 0.44, y + s * 0.25], fill=PAPER, outline=INK, width=lw)  # a block
    d.line([(x - s * 0.36, y + s * 0.25), (x + s * 0.46, y + s * 0.25)], fill=INK, width=lw)


def tower(d, x, y, s):
    lw = _w(s)
    d.polygon([(x - s * 0.13, y + s * 0.32), (x - s * 0.1, y - s * 0.36), (x + s * 0.1, y - s * 0.36),
               (x + s * 0.13, y + s * 0.32)], fill=PAPER, outline=INK)
    _crenels(d, x - s * 0.13, x + s * 0.13, y - s * 0.36, 3, s * 0.08, lw)
    d.rectangle([x - s * 0.02, y - s * 0.16, x + s * 0.02, y - s * 0.05], fill=DARK)  # a slit window


def temple(d, x, y, s):
    lw = _w(s)
    d.rectangle([x - s * 0.3, y - s * 0.05, x + s * 0.1, y + s * 0.3], fill=PAPER, outline=INK, width=lw)  # the nave
    d.polygon([(x - s * 0.36, y - s * 0.05), (x - s * 0.1, y - s * 0.24), (x + s * 0.16, y - s * 0.05)],
              fill=PAPER, outline=INK)
    d.rectangle([x + s * 0.1, y - s * 0.22, x + s * 0.26, y + s * 0.3], fill=PAPER, outline=INK, width=lw)  # tower
    d.polygon([(x + s * 0.08, y - s * 0.22), (x + s * 0.18, y - s * 0.5), (x + s * 0.28, y - s * 0.22)],
              fill=PAPER, outline=INK)
    d.chord([x - s * 0.16, y + s * 0.06, x - s * 0.04, y + s * 0.3], 180, 360, fill=DARK)


def barrow(d, x, y, s):
    lw = _w(s)
    d.chord([x - s * 0.42, y - s * 0.2, x + s * 0.42, y + s * 0.5], 180, 360, fill=PAPER, outline=INK, width=lw)
    for k in (0.12, 0.24):
        d.arc([x - s * (0.42 - k), y - s * (0.2 - k * 0.6), x + s * (0.42 - k), y + s * 0.5], 200, 250, fill=INK, width=lw)
    d.rectangle([x - s * 0.05, y + s * 0.05, x + s * 0.05, y + s * 0.15], fill=DARK)  # the entrance


def stones(d, x, y, s):
    lw = _w(s)
    for a in (200, 240, 280, 320, 0, 160):  # a ring of stones, seen from the side
        sx, sy = x + s * 0.36 * math.cos(math.radians(a)), y + s * 0.14 * math.sin(math.radians(a))
        h = s * (0.26 if 180 < a < 360 else 0.32)
        d.rectangle([sx - s * 0.05, sy - h, sx + s * 0.05, sy], fill=PAPER, outline=INK, width=lw)


def cave(d, x, y, s):
    lw = _w(s)
    d.chord([x - s * 0.42, y - s * 0.3, x + s * 0.42, y + s * 0.6], 180, 360, fill=PAPER, outline=INK, width=lw)
    d.chord([x - s * 0.14, y - s * 0.02, x + s * 0.14, y + s * 0.46], 180, 360, fill=DARK)  # the mouth
    for k in (-0.3, 0.26):
        d.line([(x + k * s, y + s * 0.02), (x + (k - 0.06) * s, y + s * 0.12)], fill=INK, width=lw)


def lighthouse(d, x, y, s):
    lw = _w(s)
    d.polygon([(x - s * 0.12, y + s * 0.32), (x - s * 0.07, y - s * 0.24), (x + s * 0.07, y - s * 0.24),
               (x + s * 0.12, y + s * 0.32)], fill=PAPER, outline=INK)
    d.rectangle([x - s * 0.09, y - s * 0.38, x + s * 0.09, y - s * 0.24], fill=DARK)
    for a in (-160, -120, -60, -20):  # the light
        ca, sa = math.cos(math.radians(a)), math.sin(math.radians(a))
        d.line([(x + ca * s * 0.16, y - s * 0.31 + sa * s * 0.16), (x + ca * s * 0.34, y - s * 0.31 + sa * s * 0.34)],
               fill=INK, width=lw)


def inn(d, x, y, s):
    lw = _w(s)
    _house(d, x - s * 0.05, y + s * 0.28, s * 0.36, s * 0.26, lw)
    d.line([(x + s * 0.14, y - s * 0.06), (x + s * 0.34, y - s * 0.06)], fill=INK, width=lw)  # a bracket
    d.rectangle([x + s * 0.24, y - s * 0.04, x + s * 0.36, y + s * 0.1], fill=PAPER, outline=INK, width=lw)  # its sign


def manor(d, x, y, s):
    lw = _w(s)
    d.rectangle([x - s * 0.36, y - s * 0.02, x + s * 0.36, y + s * 0.3], fill=PAPER, outline=INK, width=lw)
    d.polygon([(x - s * 0.42, y - s * 0.02), (x - s * 0.3, y - s * 0.24), (x + s * 0.3, y - s * 0.24),
               (x + s * 0.42, y - s * 0.02)], fill=PAPER, outline=INK)
    for cx in (-0.2, 0.2):  # chimneys
        d.rectangle([x + cx * s - s * 0.03, y - s * 0.34, x + cx * s + s * 0.03, y - s * 0.2], fill=PAPER, outline=INK)
    d.rectangle([x - s * 0.05, y + s * 0.12, x + s * 0.05, y + s * 0.3], fill=DARK)


def battle(d, x, y, s):
    lw = _w(s) + 1
    for sgn in (-1, 1):  # crossed swords
        a, b = (x - sgn * s * 0.3, y + s * 0.3), (x + sgn * s * 0.3, y - s * 0.3)
        d.line([a, b], fill=INK, width=lw)
        gx, gy = x - sgn * s * 0.18, y + s * 0.18  # the crossguard, near the hilt
        d.line([(gx - s * 0.08, gy - sgn * s * 0.08 * -1), (gx + s * 0.08, gy + sgn * s * 0.08 * -1)], fill=INK, width=lw)


DRAW = {"castle": castle, "city": city, "town": town, "village": village, "anchor": anchor, "ruin": ruin,
        "tower": tower, "temple": temple, "barrow": barrow, "stones": stones, "cave": cave, "lighthouse": lighthouse,
        "inn": inn, "manor": manor, "battle": battle}
SIZE = {"castle": 30, "city": 28, "town": 24, "village": 20, "anchor": 18, "battle": 18}


def draw(d: ImageDraw.ImageDraw, emblem: str, x: float, y: float, scale: float) -> float:
    """Draw ``emblem`` centred on (x, y); returns its half-height in pixels (to place a label clear of it)."""
    s = SIZE.get(emblem, 22) * scale
    DRAW[emblem](d, x, y, s)
    return s * 0.5

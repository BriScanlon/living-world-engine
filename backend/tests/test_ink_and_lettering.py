"""Cartographic maps, phase 4b (#57): lettering along a curve, and the ink-only style."""
import math

from PIL import Image, ImageFont

from atlas import frame
from atlas.party_map import STYLES


def _arc(cx, cy, half, n=25, bow=20):
    return [(cx + half * t, cy - bow * (1 - t * t)) for t in (i / (n - 1) * 2 - 1 for i in range(n))]


def test_lettering_follows_a_curve_letter_by_letter():
    font = ImageFont.load_default()
    lay = frame.Labels((0, 0, 800, 400))
    lab = lay.add_along("R E A L M", font, [_arc(400, 200, 200)], (0, 0, 0), (255, 255, 255))
    assert lab and lab.glyphs and len(lab.glyphs) == len("R E A L M")
    angles = [g[3] for g in lab.glyphs if g[0].strip()]
    assert angles[0] < 0 < angles[-1]  # rising, then falling: the letters turn with the arc
    xs = [g[1] for g in lab.glyphs]
    assert xs == sorted(xs)  # left to right
    img = Image.new("RGBA", (800, 400), (255, 255, 255, 255))
    lay.draw(img)
    assert img.convert("L").getextrema()[0] < 100


def test_curved_lettering_gives_way_and_stays_on_its_land():
    font = ImageFont.load_default()
    lay = frame.Labels((0, 0, 800, 400))
    assert lay.add_along("TOO LONG FOR THIS", font, [_arc(400, 200, 10)], (0, 0, 0), (255, 255, 255)) is None
    lay.reserve((300, 150, 500, 250))  # something already lettered in the middle
    assert lay.add_along("REALM", font, [_arc(400, 200, 60, bow=5)], (0, 0, 0), (255, 255, 255)) is None
    # a second path clear of it is taken, unless its letters fall off the realm's land
    path = _arc(400, 320, 60, bow=5)
    assert lay.add_along("REALM", font, [path], (0, 0, 0), (255, 255, 255), inside=lambda x, y: y < 300) is None
    assert lay.add_along("REALM", font, [path], (0, 0, 0), (255, 255, 255), inside=lambda x, y: y > 300)


def test_the_ink_style_has_no_colour():
    ink = STYLES["ink"]
    assert not ink["washes"] and ink["shallow"] is None
    for key in ("sea", "land", "ripple", "border", "coast", "river", "road", "water_ink"):
        r, g, b = ink[key][:3]
        assert max(r, g, b) - min(r, g, b) <= 40, key  # paper and ink tones, never blue or green
    assert STYLES["colour"]["washes"]

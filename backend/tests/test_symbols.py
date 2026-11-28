"""Cartographic maps, phase 2 (#55): terrain drawn with symbols."""
import numpy as np
import pytest
from PIL import Image, ImageDraw
from shapely.geometry import LineString, Polygon

from atlas import symbols as sy


def _canvas(w=600, h=400, land_box=(50, 50, 550, 350)):
    land = Image.new("L", (w, h), 0)
    ImageDraw.Draw(land).rectangle(land_box, fill=255)
    return land, Image.new("L", (w, h), 0)


def _cells(h, b, step=40, box=(70, 70, 530, 330)):
    xs, ys = np.meshgrid(np.arange(box[0], box[2], step), np.arange(box[1], box[3], step))
    n = xs.size
    return np.column_stack([xs.ravel(), ys.ravel(), np.full(n, h), np.full(n, b)]).astype(float)


@pytest.mark.parametrize("h,b,kind", [
    (80, 6, "mountain"), (95, 9, "snowpeak"), (70, 11, "snowpeak"), (55, 4, "hill"), (45, 4, "downs"),
    (30, 6, "broadleaf"), (30, 9, "pine"), (30, 7, "jungle"), (30, 5, "palm"), (30, 8, "pine"),
    (25, 12, "marsh"), (25, 4, "plains"), (25, 3, "savanna"), (25, 10, "heath"), (25, 1, "desert"), (25, 1, "dune"),
    (25, 2, "waste"), (30, 11, "ice"),
])
def test_every_terrain_shows_its_symbol(h, b, kind):
    assert kind in [k for k, _ in sy.kinds_for(h, b)]
    land, avoid = _canvas()
    syms = sy.place(_cells(h, b), 40, land, avoid, 1.0, seed=3)
    assert syms and kind in {s.kind for s in syms}


def test_every_glyph_draws_something():
    for k in sy.GLYPHS:
        img = Image.new("RGBA", (80, 80), (255, 255, 255, 255))
        sy.draw(img, [sy.Symbol(k, 40, 60, 24, 5)])
        assert np.asarray(img.convert("L")).min() < 200, k


def test_density_follows_the_terrain():
    land, avoid = _canvas()
    count = lambda h, b: len(sy.place(_cells(h, b), 40, land, avoid, 1.0, seed=1))  # noqa: E731
    assert count(30, 6) > 3 * count(25, 4)  # a forest is thick with trees, a plain has the odd tuft
    assert count(80, 6) < count(30, 6)  # mountains are big, so fewer of them


def test_symbols_keep_off_water_roads_and_rivers():
    land, avoid = _canvas()
    ImageDraw.Draw(avoid).line([(300, 0), (300, 400)], fill=255, width=40)  # a river with its margin
    syms = sy.place(_cells(30, 6, step=20, box=(0, 0, 600, 400)), 20, land, avoid, 1.0, seed=2)
    assert syms
    assert all(49 <= s.x <= 551 and 49 <= s.y <= 351 for s in syms)  # on land (to the pixel)
    assert not any(280 <= s.x <= 320 for s in syms)  # not on the river
    assert [s.y for s in syms] == sorted(s.y for s in syms)  # drawn back to front


def test_placement_is_seeded_and_capped():
    land, avoid = _canvas()
    cells = _cells(30, 6, step=20)
    a = sy.place(cells, 20, land, avoid, 1.0, seed=9)
    assert [(s.kind, s.x, s.y) for s in a] == [(s.kind, s.x, s.y) for s in sy.place(cells, 20, land, avoid, 1.0, seed=9)]
    assert len(sy.place(cells, 20, land, avoid, 1.0, seed=9, budget=50)) <= 60


def test_cliffs_only_where_the_coast_is_high_and_bridges_where_roads_cross_rivers():
    land_poly = Polygon([(50, 50), (550, 50), (550, 350), (50, 350)])
    land, _ = _canvas()
    img = Image.new("RGBA", land.size, (233, 218, 180, 255))
    assert sy.cliffs(img, land_poly, land, lambda pts: np.where(pts[:, 0] < 300, 80, 25), 1.0) > 0
    assert sy.cliffs(img, land_poly, land, lambda pts: np.full(len(pts), 25), 1.0) == 0
    river = LineString([(200, 0), (200, 400)])
    roads = [("roads", LineString([(0, 100), (600, 100)])), ("trails", LineString([(0, 300), (600, 300)])),
             ("searoutes", LineString([(0, 200), (600, 200)]))]
    assert sy.crossings(img, roads, river, 1.0) == 2  # a bridge and a ford; sea lanes don't cross rivers


def test_density_thins_the_terrain_far_out():
    land, avoid = _canvas()
    cells = _cells(30, 6, step=20)
    full = len(sy.place(cells, 20, land, avoid, 1.0, seed=4))
    thin = len(sy.place(cells, 20, land, avoid, 1.0, seed=4, density=0.4))
    assert 0.25 * full < thin < 0.55 * full

"""Cartographic maps, phase 1 (#54): relief data, smoothed coasts, water and parchment."""
import io
import json

import numpy as np
from PIL import Image, ImageDraw
from shapely.geometry import Polygon, box

from atlas import cartography as carto
from atlas.azgaar import AzgaarWorld
from atlas.sample_world import generate
from wizard.builder import REGION_SIZES


def _zigzag(cell=30.0):
    """A square island whose coast is a staircase of cells, like a generated coastline."""
    pts = []
    for i in range(10):
        pts += [(i * cell, (i % 2) * cell / 3), ((i + 1) * cell, (i % 2) * cell / 3)]
    pts += [(300, 300), (0, 300)]
    return Polygon(pts)


def test_the_importer_keeps_a_relief_point_per_land_cell(tmp_path):
    raw = generate(5, land="continents", climate="temperate", n_states=5, **REGION_SIZES["small"])
    p = tmp_path / "w.json"
    p.write_text(json.dumps(raw), encoding="utf-8")
    layers = AzgaarWorld.load(p, "w").layers()
    relief = layers["relief"]["features"]
    land = [c for c in raw["pack"]["cells"] if c.get("h", 0) >= 20]
    assert len(relief) == len(land)
    assert all(f["properties"]["h"] >= 20 and "b" in f["properties"] for f in relief)


def test_smoothing_takes_out_the_zigzag_but_keeps_the_coast():
    raw = _zigzag()
    out = carto.smooth(raw, carto.Wobble(1, amp=2, scale=40), cell=30)
    assert out.hausdorff_distance(raw) < 30  # stays within about a cell of the original
    assert abs(out.area - raw.area) / raw.area < 0.08
    # rounder: no corner sharper than ~60 degrees on the smoothed outline
    ring = np.asarray(out.geoms[0].exterior.coords)[:-1]
    a, b, c = np.roll(ring, 1, 0), ring, np.roll(ring, -1, 0)
    u, v = a - b, c - b
    cos = (u * v).sum(1) / (np.linalg.norm(u, axis=1) * np.linalg.norm(v, axis=1) + 1e-9)
    assert cos.max() < 0.5


def test_a_shared_border_bends_the_same_way_on_both_sides():
    left, right = box(0, 0, 100, 100), box(100, 0, 200, 100)
    lines = carto.borders([left, right], carto.Wobble(3, amp=2, scale=30), cell=20, clip=box(1, 1, 199, 99))
    assert len(lines) == 1  # the shared edge, once
    xs = lines[0][:, 0]
    assert abs(xs.mean() - 100) < 3


def test_drawing_is_seeded():
    def picture():
        img = Image.new("RGBA", (400, 300), (128, 164, 176, 255))
        land = carto.smooth(_zigzag(), carto.Wobble(9, 2, 40), 30)
        d = ImageDraw.Draw(img, "RGBA")
        carto.ripples(d, land, [6, 12], (80, 120, 130, 150), 1)
        carto.fill(d, land, (233, 218, 180, 255))
        carto.taper(d, np.array([[20.0, 20.0], [200.0, 150.0], [280.0, 280.0]]), 1, 6, (79, 127, 166, 255))
        buf = io.BytesIO()
        carto.parchment(img, 9).save(buf, "PNG")
        return buf.getvalue()
    assert picture() == picture()


def test_the_serif_face_is_a_real_font():
    f = carto.serif(24)
    assert f.getbbox("Thessaly Reach")[2] > 100

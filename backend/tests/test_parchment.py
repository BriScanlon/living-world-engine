"""Cartographic maps, phase 4c (#57): parchment raster tiles for the DM atlas."""
import io
import json
import math

import numpy as np
import pytest
from PIL import Image

from atlas.azgaar import AzgaarWorld
from atlas.sample_world import generate
from wizard.builder import REGION_SIZES


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    """A small generated region's atlas layers in a throwaway atlas folder (no database needed)."""
    mp = pytest.MonkeyPatch()
    root = tmp_path_factory.mktemp("atlas")
    mp.setenv("ATLAS_DIR", str(root))
    from config import get_settings
    get_settings.cache_clear()
    raw = generate(5, land="continents", climate="temperate", n_states=5, **REGION_SIZES["small"])
    p = root / "w.json"
    p.write_text(json.dumps(raw), encoding="utf-8")
    from atlas.service import atlas_path
    w = AzgaarWorld.load(p, "tile-test")
    folder = atlas_path("tile-test")
    folder.mkdir(parents=True, exist_ok=True)
    for name, fc in w.layers().items():
        (folder / f"{name}.geojson").write_text(json.dumps(fc), encoding="utf-8")
    land = json.loads((folder / "land.geojson").read_text())["features"][0]["geometry"]
    from shapely.geometry import shape
    c = shape(land).representative_point()
    yield "tile-test", (c.x, c.y)
    mp.undo()
    get_settings.cache_clear()


def _tile_at(lon, lat, z):
    n = 2 ** z
    return int((lon + 180) / 360 * n), int((1 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2 * n)


def _img(data):
    return np.asarray(Image.open(io.BytesIO(data)).convert("RGBA"), dtype=float)


def test_a_tile_draws_the_land_and_is_cached(world):
    from atlas import parchment
    from atlas.parchment import cache_dir, render_tile
    slug, (lon, lat) = world
    x, y = _tile_at(lon, lat, 6)
    data = render_tile(slug, 6, x, y)
    img = _img(data)
    assert img.shape == (512, 512, 4) and img[..., 3].mean() > 200  # drawn, opaque inside the region
    assert img[..., :3].std() > 10  # coast, symbols: not a flat fill
    assert (cache_dir(slug) / parchment.VERSION / "colour" / "6" / str(x) / f"{y}.webp").read_bytes() == data
    assert render_tile(slug, 6, x, y) == data


def test_tiles_outside_the_region_are_transparent(world):
    from atlas.parchment import render_tile
    slug, _ = world
    assert _img(render_tile(slug, 6, 0, 0))[..., 3].max() == 0  # far from the region
    assert _img(render_tile(slug, 2, 0, 0))[..., 3].max() == 0  # below the parchment's zooms


def test_neighbouring_tiles_meet_without_a_seam(world):
    from atlas.parchment import render_tile
    slug, (lon, lat) = world
    x, y = _tile_at(lon, lat, 7)
    left, right = _img(render_tile(slug, 7, x, y)), _img(render_tile(slug, 7, x + 1, y))
    seam = np.abs(left[:, -1, :3] - right[:, 0, :3]).mean()  # the two columns either side of the join
    # what two neighbouring columns differ by anywhere inside a tile (busy with symbols, so not small)
    inside = np.mean([np.abs(t[:, i, :3] - t[:, i + 1, :3]).mean() for t in (left, right) for i in range(0, 511, 7)])
    assert seam < 1.5 * inside  # the join looks like any other step across the picture: no seam


def test_ink_tiles_have_no_colour(world):
    from atlas.parchment import render_tile
    slug, (lon, lat) = world
    x, y = _tile_at(lon, lat, 6)
    rgb = _img(render_tile(slug, 6, x, y, "ink"))[..., :3]
    assert ((rgb.max(axis=2) - rgb.min(axis=2)) > 70).mean() < 0.01


def test_far_out_tiles_are_sparser():
    from atlas.parchment import terrain_density
    assert terrain_density(4) < terrain_density(6) < terrain_density(8) == 1.0

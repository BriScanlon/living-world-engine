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
    assert terrain_density(5) < terrain_density(7) < terrain_density(9) == 1.0


def test_only_tiles_over_land_are_drawn_ahead(world):
    from atlas.parchment import tiles_over_land
    slug, (lon, lat) = world
    tiles = tiles_over_land(slug, 6)
    assert _tile_at(lon, lat, 6) in tiles  # the land's own tile
    assert (0, 0) not in tiles and len(set(tiles)) == len(tiles)
    assert len(tiles_over_land(slug, 7)) > len(tiles)
    assert tiles_over_land(slug, 2) == [] and tiles_over_land("no-such-world", 6) == []


def test_drawing_ahead_takes_whole_zooms_up_to_the_cap(world):
    from atlas.parchment import tiles_over_land, warm_plan
    slug, _ = world
    n = {z: len(tiles_over_land(slug, z)) for z in (3, 4, 5, 6)}
    plan = warm_plan(slug, 6, 10 ** 6)
    assert len(plan) == sum(n.values())
    assert [z for z, *_ in plan] == sorted(z for z, *_ in plan)  # the far views first
    capped = warm_plan(slug, 6, n[3] + n[4] + n[5] + n[6] - 1)
    assert {z for z, *_ in capped} == {3, 4, 5}  # zoom 6 didn't fit: none of it, not half


def test_warm_draws_the_plan_once_and_clears_older_drawings(world):
    from atlas import parchment
    slug, _ = world
    old = parchment.cache_dir(slug) / "v0" / "colour"
    old.mkdir(parents=True, exist_ok=True)
    status = {}
    first = parchment.warm(slug, 5, 10 ** 6, "ink", status=status)
    plan = parchment.warm_plan(slug, 5, 10 ** 6)
    assert status == {"total": len(plan), "done": len(plan)}
    assert all(parchment.tile_file(slug, z, x, y, "ink").exists() for z, x, y in plan)
    assert first <= len(plan) and parchment.warm(slug, 5, 10 ** 6, "ink") == 0  # all cached now
    assert not old.parent.exists()
    assert not list(parchment.cache_dir(slug).rglob("*.part"))  # whole files only


def test_drawing_ahead_can_be_turned_off(world):
    from atlas import parchment
    slug, _ = world
    assert parchment.warm_in_background(slug) is False  # tests run with PARCHMENT_WARM_ZOOM=0 (conftest)
    assert parchment.warm_in_background(slug, "ink") is False
    assert parchment.warm_status(slug) == {}

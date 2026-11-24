"""Image helpers: thumbnails plus procedural 4K test maps and tokens for MOCK_AI_PIPELINE.

The mock maps are deliberately "real enough" to test the whole stack: they are
3840x2160, use the same 140ft x 80ft scale the AI prompt enforces (1 inch = 5 ft
= 1/28 of the width), and pick a theme from keywords in the description.
"""

from __future__ import annotations

import math
import random
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont, ImageOps

MAP_W, MAP_H = 3840, 2160
FEET_W = 140  # map width in feet -> 28 five-foot squares
THUMB_SIZE = (640, 360)

Color = tuple[int, int, int]

# ---------------------------------------------------------------------------
# Themes: keywords -> palette + layout generator
# ---------------------------------------------------------------------------
THEMES: dict[str, dict] = {
    "tavern": dict(
        keywords=("tavern", "inn", "common room", "pub", "house", "hall", "manor", "shop"),
        layout="building",
        wall=((18, 14, 12), (42, 34, 28), (66, 56, 48)),
        floor=((86, 56, 32), (128, 86, 50), (168, 122, 76)),
    ),
    "volcano": dict(
        keywords=("volcano", "lava", "magma", "fire", "inferno", "forge", "boss arena"),
        layout="cave",
        wall=((12, 10, 10), (34, 28, 28), (58, 50, 48)),
        floor=((40, 34, 32), (70, 60, 56), (102, 90, 82)),
        liquid="lava",
    ),
    "docks": dict(
        keywords=("dock", "harbor", "harbour", "pier", "port", "wharf", "ship", "boat"),
        layout="docks",
        wall=((8, 22, 36), (16, 48, 70), (40, 86, 110)),
        floor=((72, 50, 30), (112, 82, 52), (150, 116, 80)),
    ),
    "snow": dict(
        keywords=("snow", "ice", "frozen", "arctic", "glacier", "winter", "tundra"),
        layout="outdoor",
        wall=((170, 182, 196), (210, 220, 230), (240, 245, 250)),
        floor=((120, 130, 140), (150, 160, 168), (182, 190, 196)),
    ),
    "desert": dict(
        keywords=("desert", "sand", "dune", "oasis", "ruin", "tomb"),
        layout="outdoor",
        wall=((150, 112, 62), (192, 152, 94), (224, 192, 136)),
        floor=((120, 92, 60), (150, 118, 80), (176, 146, 106)),
    ),
    "cave": dict(
        keywords=("cave", "cavern", "underground", "grotto", "mine", "tunnel", "underdark"),
        layout="cave",
        wall=((14, 13, 14), (40, 36, 36), (66, 60, 58)),
        floor=((70, 62, 54), (104, 94, 82), (136, 124, 108)),
    ),
    "forest": dict(
        keywords=("forest", "wood", "road", "ambush", "glade", "grove", "jungle", "swamp", "meadow", "camp"),
        layout="outdoor",
        wall=((22, 40, 18), (46, 74, 34), (82, 110, 52)),
        floor=((92, 72, 46), (128, 104, 70), (160, 136, 98)),
    ),
    "dungeon": dict(
        keywords=("dungeon", "corridor", "crypt", "prison", "keep", "castle", "temple", "sewer", "vault"),
        layout="rooms",
        wall=((14, 14, 18), (34, 34, 40), (58, 58, 64)),
        floor=((74, 70, 64), (106, 100, 92), (140, 134, 124)),
    ),
}

WATER = ((10, 34, 52), (24, 70, 96), (70, 130, 150))
LAVA = ((120, 18, 0), (230, 90, 10), (255, 214, 90))
GRASS_TREE = ((10, 30, 10), (26, 60, 22), (60, 100, 40))


def pick_theme(text: str) -> tuple[str, dict]:
    t = text.lower()
    for name, theme in THEMES.items():
        if any(k in t for k in theme["keywords"]):
            return name, theme
    return "dungeon", THEMES["dungeon"]


# ---------------------------------------------------------------------------
# Noise + texture primitives (all Pillow, no numpy)
# ---------------------------------------------------------------------------
def _value_noise(rng: random.Random, w: int, h: int, base_cells: int = 6, octaves: int = 4) -> Image.Image:
    acc: Image.Image | None = None
    for o in range(octaves):
        cx = base_cells * (2 ** o)
        cy = max(2, round(cx * h / w))
        small = Image.frombytes("L", (cx, cy), bytes(rng.randrange(256) for _ in range(cx * cy)))
        layer = small.resize((w, h), Image.BICUBIC)
        acc = layer if acc is None else Image.blend(acc, layer, 0.5 / (o + 0.5))
    assert acc is not None
    return ImageOps.autocontrast(acc)


def _texture(rng: random.Random, size: tuple[int, int], palette: tuple[Color, Color, Color], cells: int = 8) -> Image.Image:
    n = _value_noise(rng, size[0], size[1], cells)
    grain = Image.effect_noise(size, 40).convert("L")
    n = Image.blend(n, grain, 0.12)
    return ImageOps.colorize(n, black=palette[0], mid=palette[1], white=palette[2]).convert("RGB")


def _threshold(img: Image.Image, cut: int) -> Image.Image:
    return img.point(lambda v: 255 if v >= cut else 0).convert("L")


def _composite(base: Image.Image, top: Image.Image, mask: Image.Image, shadow: int = 0) -> Image.Image:
    if shadow:
        sh = mask.filter(ImageFilter.GaussianBlur(shadow))
        sh = ImageChops.offset(sh, shadow // 2, shadow // 2)
        base = Image.composite(Image.new("RGB", base.size, (0, 0, 0)), base, sh.point(lambda v: v * 0.55))
    return Image.composite(top, base, mask)


def _polyline(rng: random.Random, w: int, h: int, horizontal: bool, segments: int = 7) -> list[tuple[float, float]]:
    pts = []
    for i in range(segments + 1):
        t = i / segments
        if horizontal:
            pts.append((t * w * 1.1 - w * 0.05, h * (0.5 + 0.28 * math.sin(t * 3.1 + rng.random() * 3)) + rng.uniform(-h * 0.06, h * 0.06)))
        else:
            pts.append((w * (0.5 + 0.25 * math.sin(t * 2.7 + rng.random() * 3)) + rng.uniform(-w * 0.05, w * 0.05), t * h * 1.1 - h * 0.05))
    return pts


def _thick_line(draw: ImageDraw.ImageDraw, pts, width: int, fill=255) -> None:
    draw.line(pts, fill=fill, width=width, joint="curve")
    r = width / 2
    for x, y in pts:
        draw.ellipse((x - r, y - r, x + r, y + r), fill=fill)


# ---------------------------------------------------------------------------
# Layouts: each returns (image, liquid_mask or None, light_points)
# ---------------------------------------------------------------------------
def _layout_rooms(rng, w, h, theme, cell):
    wall = _texture(rng, (w, h), theme["wall"], 10)
    floor = _texture(rng, (w, h), theme["floor"], 14)
    fd = ImageDraw.Draw(floor)
    # flagstones on the 5ft grid, slightly jittered
    for gx in range(0, int(w / cell) + 2):
        x = gx * cell + rng.uniform(-2, 2)
        fd.line([(x, 0), (x, h)], fill=(40, 38, 36), width=2)
    for gy in range(0, int(h / cell) + 2):
        y = gy * cell + rng.uniform(-2, 2)
        fd.line([(0, y), (w, y)], fill=(40, 38, 36), width=2)
    mask = Image.new("L", (w, h), 0)
    md = ImageDraw.Draw(mask)
    cols, rows = int(w / cell), int(h / cell)
    rooms = []
    for _ in range(rng.randint(6, 9)):
        rw, rh = rng.randint(4, 8), rng.randint(3, 6)
        rx, ry = rng.randint(1, cols - rw - 1), rng.randint(1, rows - rh - 1)
        rooms.append((rx, ry, rw, rh))
        md.rectangle((rx * cell, ry * cell, (rx + rw) * cell, (ry + rh) * cell), fill=255)
    rooms.sort()
    for a, b in zip(rooms, rooms[1:]):  # 10ft (2-square) corridors between rooms
        ax, ay = a[0] + a[2] // 2, a[1] + a[3] // 2
        bx, by = b[0] + b[2] // 2, b[1] + b[3] // 2
        md.rectangle((min(ax, bx) * cell, ay * cell, (max(ax, bx) + 2) * cell, (ay + 2) * cell), fill=255)
        md.rectangle((bx * cell, min(ay, by) * cell, (bx + 2) * cell, (max(ay, by) + 2) * cell), fill=255)
    img = _composite(wall, floor, mask, shadow=int(cell * 0.25))
    lights = [((r[0] + r[2] / 2) * cell, (r[1] + r[3] / 2) * cell) for r in rooms[:4]]
    return img, None, lights


def _layout_cave(rng, w, h, theme, cell):
    wall = _texture(rng, (w, h), theme["wall"], 8)
    floor = _texture(rng, (w, h), theme["floor"], 12)
    n = _value_noise(rng, w, h, 5, 3)
    mask = _threshold(n, 105).filter(ImageFilter.GaussianBlur(6))
    md = ImageDraw.Draw(mask)
    _thick_line(md, _polyline(rng, w, h, True), int(cell * 2.2))  # guarantee a passable route
    mask = mask.filter(ImageFilter.GaussianBlur(3))
    img = _composite(wall, floor, mask, shadow=int(cell * 0.35))
    liquid = None
    if theme.get("liquid") == "lava":
        liquid = Image.new("L", (w, h), 0)
        _thick_line(ImageDraw.Draw(liquid), _polyline(rng, w, h, False), int(cell * 1.6))
        liquid = ImageChops.lighter(liquid, _threshold(_value_noise(rng, w, h, 4, 2), 215))
    return img, liquid, []


def _layout_outdoor(rng, w, h, theme, cell):
    ground = _texture(rng, (w, h), theme["wall"], 10)
    road = _texture(rng, (w, h), theme["floor"], 16)
    mask = Image.new("L", (w, h), 0)
    path = _polyline(rng, w, h, True)
    _thick_line(ImageDraw.Draw(mask), path, int(cell * 2.0))
    img = _composite(ground, road, mask.filter(ImageFilter.GaussianBlur(cell * 0.2)))
    # trees / rocks avoiding the path
    canopy = Image.new("L", (w, h), 0)
    cd = ImageDraw.Draw(canopy)
    is_snow_or_sand = theme["wall"][2][0] > 200
    for _ in range(rng.randint(55, 85)):
        x, y = rng.uniform(0, w), rng.uniform(0, h)
        if mask.getpixel((int(min(x, w - 1)), int(min(y, h - 1)))) > 0:
            continue
        r = rng.uniform(0.5, 1.4) * cell
        cd.ellipse((x - r, y - r, x + r, y + r), fill=255)
    canopy = canopy.filter(ImageFilter.GaussianBlur(3))
    tree_palette = ((60, 64, 70), (110, 114, 120), (160, 162, 168)) if is_snow_or_sand else GRASS_TREE
    trees = _texture(rng, (w, h), tree_palette, 30)
    img = _composite(img, trees, canopy, shadow=int(cell * 0.4))
    return img, None, []


def _layout_building(rng, w, h, theme, cell):
    outside = _texture(rng, (w, h), theme["wall"], 10)
    planks = _texture(rng, (w, h), theme["floor"], 20)
    pd = ImageDraw.Draw(planks)
    step = cell / 3
    for i in range(int(h / step) + 1):
        y = i * step
        pd.line([(0, y), (w, y)], fill=(54, 34, 20), width=2)
        off = rng.uniform(0, cell * 2)
        for x in range(int(off), w, int(cell * 2)):
            pd.line([(x, y), (x, y + step)], fill=(60, 38, 22), width=2)
    mask = Image.new("L", (w, h), 0)
    md = ImageDraw.Draw(mask)
    md.rectangle((2 * cell, 2 * cell, w - 2 * cell, h - 2 * cell), fill=255)
    img = _composite(outside, planks, mask, shadow=int(cell * 0.3))
    d = ImageDraw.Draw(img)
    # interior wall with a 5ft door, bar counter, hearth, round tables
    wx = int(w * 0.68)
    d.rectangle((wx, 2 * cell, wx + cell * 0.35, h * 0.45), fill=(30, 22, 18))
    d.rectangle((wx, h * 0.45 + cell, wx + cell * 0.35, h - 2 * cell), fill=(30, 22, 18))
    d.rectangle((3 * cell, 2.6 * cell, w * 0.45, 3.4 * cell), fill=(70, 40, 22), outline=(30, 18, 10), width=4)
    hx, hy = w * 0.55, 2.4 * cell
    d.rectangle((hx - cell, hy - cell * 0.4, hx + cell, hy + cell * 0.5), fill=(60, 56, 52))
    lights = [(hx, hy + cell * 0.2)]
    for _ in range(9):
        x, y = rng.uniform(3.5 * cell, wx - 1.5 * cell), rng.uniform(5 * cell, h - 3.5 * cell)
        r = cell * 0.6
        d.ellipse((x - r + 6, y - r + 6, x + r + 6, y + r + 6), fill=(40, 26, 16))
        d.ellipse((x - r, y - r, x + r, y + r), fill=(110, 72, 40), outline=(50, 30, 16), width=4)
        for k in range(4):
            a = k * math.pi / 2 + rng.random()
            sx, sy = x + math.cos(a) * r * 1.5, y + math.sin(a) * r * 1.5
            d.rectangle((sx - cell * 0.22, sy - cell * 0.22, sx + cell * 0.22, sy + cell * 0.22), fill=(92, 60, 34))
        lights.append((x, y))
    return img, None, lights


def _layout_docks(rng, w, h, theme, cell):
    water = _texture(rng, (w, h), theme["wall"], 12)
    planks = _texture(rng, (w, h), theme["floor"], 20)
    pd = ImageDraw.Draw(planks)
    for i in range(int(w / (cell / 3)) + 1):
        x = i * cell / 3
        pd.line([(x, 0), (x, h)], fill=(50, 34, 20), width=2)
    mask = Image.new("L", (w, h), 0)
    md = ImageDraw.Draw(mask)
    md.rectangle((0, 0, w, 3.5 * cell), fill=255)  # quay
    lights = []
    for k in range(4):
        x = (k + 0.6) * w / 4.2
        pw = cell * rng.choice((2, 3))
        md.rectangle((x, 3 * cell, x + pw, h * rng.uniform(0.55, 0.85)), fill=255)
        lights.append((x + pw / 2, 3.2 * cell))
    img = _composite(water, planks, mask, shadow=int(cell * 0.3))
    d = ImageDraw.Draw(img)
    for k in range(3):  # moored boats
        x = (k + 1.1) * w / 4.2 + cell * 0.6
        y = h * rng.uniform(0.45, 0.7)
        d.ellipse((x - cell * 0.9, y - cell * 2.4, x + cell * 0.9, y + cell * 2.4), fill=(84, 56, 32), outline=(40, 26, 14), width=5)
    return img, None, lights


LAYOUTS = {
    "rooms": _layout_rooms,
    "cave": _layout_cave,
    "outdoor": _layout_outdoor,
    "building": _layout_building,
    "docks": _layout_docks,
}


def _font(size: int) -> ImageFont.ImageFont:
    for name in ("DejaVuSans-Bold.ttf", "arialbd.ttf", "Arial Bold.ttf", "arial.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # Pillow < 10.1
        return ImageFont.load_default()


def generate_mock_map(description: str, style: str, seed: int, out_path: Path) -> dict:
    """Render a themed 3840x2160 battle map PNG. Returns metadata."""
    rng = random.Random(seed)
    text = f"{description} {style}"
    theme_name, theme = pick_theme(text)
    lower = text.lower()
    w, h = MAP_W // 2, MAP_H // 2  # draw at half-res, upscale at the end (fast)
    cell = w / (FEET_W / 5)

    img, liquid, lights = LAYOUTS[theme["layout"]](rng, w, h, theme, cell)

    # optional river / flooding + bridge
    wants_water = any(k in lower for k in ("river", "stream", "flood", "water", "bridge", "lake", "pool"))
    if wants_water and liquid is None and theme["layout"] != "docks":
        liquid = Image.new("L", (w, h), 0)
        river = _polyline(rng, w, h, False)
        _thick_line(ImageDraw.Draw(liquid), river, int(cell * 1.8))
        if "flood" in lower:
            liquid = ImageChops.lighter(liquid, _threshold(_value_noise(rng, w, h, 4, 3), 170))
    if liquid is not None:
        is_lava = theme.get("liquid") == "lava" or "lava" in lower
        tex = _texture(rng, (w, h), LAVA if is_lava else WATER, 18)
        soft = liquid.filter(ImageFilter.GaussianBlur(4))
        img = Image.composite(tex, img, soft)
        if is_lava:
            glow = liquid.filter(ImageFilter.GaussianBlur(cell * 0.6)).point(lambda v: v * 0.6)
            img = Image.composite(Image.new("RGB", (w, h), (255, 110, 20)), img, glow)
        if "bridge" in lower:
            d = ImageDraw.Draw(img)
            y = h * rng.uniform(0.35, 0.65)
            bridge_x0, bridge_x1 = w * 0.3, w * 0.7
            for x in range(int(bridge_x0), int(bridge_x1), int(cell / 3)):
                d.rectangle((x, y - cell, x + cell / 3 - 3, y + cell), fill=(118, 84, 50), outline=(52, 34, 18), width=2)
            d.line([(bridge_x0, y - cell), (bridge_x1, y - cell)], fill=(60, 40, 22), width=5)
            d.line([(bridge_x0, y + cell), (bridge_x1, y + cell)], fill=(60, 40, 22), width=5)

    # lighting: warm pools of light, optional night grade
    night = any(k in lower for k in ("night", "dark", "moon", "underground", "cavern", "dungeon", "crypt"))
    if lights or night:
        light = Image.new("L", (w, h), 115 if night else 150)
        ld = ImageDraw.Draw(light)
        for x, y in lights:
            for r, v in ((cell * 3.5, 170), (cell * 2.2, 220), (cell * 1.2, 255)):
                ld.ellipse((x - r, y - r, x + r, y + r), fill=v)
        light = light.filter(ImageFilter.GaussianBlur(cell * 1.2))
        tint = (255, 214, 160) if lights else (170, 190, 255)
        img = ImageChops.multiply(img, ImageOps.colorize(light, black=(0, 0, 0), white=tint).convert("RGB"))
        img = ImageChops.add(img, ImageChops.multiply(img, ImageOps.colorize(light, black=(0, 0, 0), white=(40, 30, 20)).convert("RGB")))

    # vignette
    vig = Image.new("L", (w, h), 0)
    ImageDraw.Draw(vig).ellipse((-w * 0.15, -h * 0.25, w * 1.15, h * 1.25), fill=255)
    vig = vig.filter(ImageFilter.GaussianBlur(w * 0.06))
    img = Image.composite(img, ImageChops.multiply(img, Image.new("RGB", (w, h), (110, 110, 120))), vig)

    img = img.resize((MAP_W, MAP_H), Image.LANCZOS)
    d = ImageDraw.Draw(img)
    label = f"MOCK  {theme_name.upper()}  seed {seed}"
    d.text((40, MAP_H - 70), label, fill=(255, 255, 255), font=_font(34), stroke_width=3, stroke_fill=(0, 0, 0))

    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(out_path, "PNG", optimize=False, compress_level=3)
    return {"width": MAP_W, "height": MAP_H, "theme": theme_name}


def generate_mock_token(name: str, description: str, seed: int, out_path: Path, size: int = 512) -> dict:
    """Round top-down token: textured disc, coloured ring, initials. Transparent background."""
    rng = random.Random(seed)
    hue = rng.random()
    import colorsys

    def hsv(hh, s, v):
        r, g, b = colorsys.hsv_to_rgb(hh % 1, s, v)
        return int(r * 255), int(g * 255), int(b * 255)

    s4 = size * 2
    tex = _texture(rng, (s4, s4), (hsv(hue, 0.6, 0.18), hsv(hue, 0.55, 0.42), hsv(hue, 0.4, 0.75)), 6)
    token = Image.new("RGBA", (s4, s4), (0, 0, 0, 0))
    disc = Image.new("L", (s4, s4), 0)
    ImageDraw.Draw(disc).ellipse((8, 8, s4 - 8, s4 - 8), fill=255)
    token.paste(tex, (0, 0), disc)
    d = ImageDraw.Draw(token)
    ring = hsv(hue + 0.5, 0.7, 0.9)
    d.ellipse((8, 8, s4 - 8, s4 - 8), outline=(20, 16, 12), width=s4 // 18)
    d.ellipse((s4 // 28, s4 // 28, s4 - s4 // 28, s4 - s4 // 28), outline=ring, width=s4 // 36)
    initials = "".join(p[0] for p in name.split()[:2]).upper() or "?"
    font = _font(int(s4 * 0.36))
    d.text((s4 / 2, s4 / 2), initials, fill=(255, 255, 255), font=font, anchor="mm",
           stroke_width=s4 // 60, stroke_fill=(0, 0, 0))
    token = token.resize((size, size), Image.LANCZOS)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    token.save(out_path, "PNG")
    return {"width": size, "height": size}


# ---------------------------------------------------------------------------
def make_thumbnail(src: Path, dst: Path, size: tuple[int, int] = THUMB_SIZE) -> Path:
    with Image.open(src) as im:
        im = im.convert("RGBA" if im.mode in ("RGBA", "LA", "P") else "RGB")
        im = ImageOps.fit(im, size, Image.LANCZOS) if size[0] != size[1] else ImageOps.contain(im, size, Image.LANCZOS)
        dst.parent.mkdir(parents=True, exist_ok=True)
        im.save(dst, "WEBP", quality=82)
    return dst


def image_size(path: Path) -> tuple[int, int]:
    with Image.open(path) as im:
        return im.size

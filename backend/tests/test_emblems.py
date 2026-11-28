"""Cartographic maps, phase 3 (#56): settlement and landmark symbols, label layout and the map's furniture."""
import numpy as np
import pytest
from PIL import Image, ImageDraw, ImageFont

from atlas import emblems, frame


@pytest.mark.parametrize("kind,name,emblem", [
    ("capital", "Marethor", "castle"), ("city", "Roki", "city"), ("town", "Greywater", "town"),
    ("village", "Halby", "village"), ("ruin", "The Ruins of Ost", "ruin"), ("tavern", "The Drowned Lantern", "inn"),
    ("building", "The Old Watchtower", "tower"), ("building", "Abbey of St Brin", "temple"),
    ("wilderness", "The Barrow Downs", "barrow"), ("wilderness", "The King's Stones", "stones"),
    ("wilderness", "Deepdelve Mine", "cave"), ("building", "Harbour Beacon", "lighthouse"),
    ("building", "Vane Manor", "manor"), ("building", "The Crooked Inn", "inn"),
    ("wilderness", "Blackfen Marsh", None), ("district", "Greywater Docks", None),
])
def test_every_kind_of_place_gets_its_symbol(kind, name, emblem):
    assert emblems.emblem_for(kind, name) == emblem


def test_every_symbol_draws_something():
    for e in emblems.DRAW:
        img = Image.new("RGBA", (120, 120), (255, 255, 255, 255))
        emblems.draw(ImageDraw.Draw(img, "RGBA"), e, 60, 60, 2.0)
        assert np.asarray(img.convert("L")).min() < 120, e


def test_labels_never_overlap_and_give_way_when_there_is_no_room():
    font = ImageFont.load_default()
    lay = frame.Labels((0, 0, 400, 200), pad=2)
    lay.reserve((150, 80, 250, 120))  # a castle in the middle
    placed = [lay.add(f"Town {i}", font, [(200, 100, "mm"), (200, 60, "mm"), (200, 140, "mm"), (60, 100, "mm")],
                      (0, 0, 0), (255, 255, 255)) for i in range(6)]
    boxes = [p.box for p in placed if p]
    assert 1 <= len(boxes) < 6  # some fitted, the rest were left off rather than piled up
    for i, a in enumerate(boxes):
        assert not (a[0] < 250 and a[2] > 150 and a[1] < 120 and a[3] > 80)  # off the castle
        for b in boxes[i + 1:]:
            assert not (a[0] < b[2] and a[2] > b[0] and a[1] < b[3] and a[3] > b[1])


def test_the_frame_compass_cartouche_and_scale():
    img = Image.new("RGBA", (800, 500), (200, 220, 230, 255))
    inner = frame.neatline(img, lambda x, y: (x / 100, -y / 100), 14, 12, 1.0)
    assert inner == (26, 26, 774, 474)
    assert img.getpixel((5, 250))[:3] == frame.PAPER[:3]  # the margin is paper
    font = ImageFont.load_default()
    for box in (frame.compass(img, 700, 100, 60, font), frame.cartouche(img, 40, 460, "Thessaly", "day 10", font, font, 1.0),
                frame.scale_bar(img, 760, 460, 2.0, 100, font, 1.0)):
        assert box[0] < box[2] and box[1] < box[3]
    assert frame.spaced("Duchy of Steadmere") == "D U C H Y  O F  S T E A D M E R E"

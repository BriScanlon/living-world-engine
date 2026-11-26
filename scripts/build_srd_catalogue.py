"""Build the SRD 5.2.1 reference catalogue (JSON) from the official PDF.

The System Reference Document 5.2.1 is © Wizards of the Coast LLC, licensed CC-BY-4.0
(https://creativecommons.org/licenses/by/4.0/legalcode). The generated JSON carries the
required attribution statement.

Usage:
    python scripts/build_srd_catalogue.py path/to/SRD_CC_v5.2.1.pdf
    -> backend/srd/srd_5_2_1.json

PDF text extraction is noisy; the parser extracts the structured parts (stat-block headers,
item headers, price lines) and reports counts so the result can be spot-checked.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from pypdf import PdfReader

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "backend" / "srd" / "srd_5_2_1.json"

ATTRIBUTION = (
    "This work includes material from the System Reference Document 5.2.1 (\"SRD 5.2.1\") by Wizards of "
    "the Coast LLC, available at https://www.dndbeyond.com/srd. The SRD 5.2.1 is licensed under the Creative "
    "Commons Attribution 4.0 International License, available at "
    "https://creativecommons.org/licenses/by/4.0/legalcode."
)

SIZES = "Tiny|Small|Medium|Large|Huge|Gargantuan"
RARITIES = ["Common", "Uncommon", "Rare", "Very Rare", "Legendary", "Artifact"]
ITEM_CATS = "Armor|Weapon|Wondrous Item|Potion|Ring|Rod|Scroll|Staff|Wand"


def clean(s: str) -> str:
    return s.replace("−", "-").replace("’", "'").replace("–", "-").replace("—", "-")


def outline_sections(reader: PdfReader) -> dict[str, tuple[int, int]]:
    """Top-level outline titles -> (first page, last page), 1-based."""
    top = []
    root = reader.outline[1] if len(reader.outline) > 1 and isinstance(reader.outline[1], list) else reader.outline
    for x in root:
        if not isinstance(x, list):
            top.append((clean(x.title), reader.get_destination_page_number(x) + 1))
    out = {}
    for i, (title, start) in enumerate(top):
        end = top[i + 1][1] if i + 1 < len(top) else len(reader.pages)
        out[title] = (start, end)
    return out


def text_of(reader: PdfReader, first: int, last: int) -> str:
    lines = []
    for p in range(first - 1, last):
        t = clean(reader.pages[p].extract_text() or "")
        # drop running headers: page number + "System Reference Document 5.2.1"
        t = re.sub(r"^\s*\d+\s*\nSystem Reference Document 5\.2\.1\s*\n", "", t)
        t = re.sub(r"^\s*System Reference Document 5\.2\.1\s*\n\d+\s*\n", "", t)
        lines.append(t)
    return "\n".join(lines)


# --------------------------------------------------------------------------- monsters
HEADER = re.compile(
    rf"(?m)^(?P<name>[A-Z][^\n]{{1,60}})\n(?P<size>(?:{SIZES})(?: or (?:{SIZES}))?) (?P<type>[A-Z][a-z]+(?: Swarm of [A-Za-z ]+)?)"
    rf"(?: \((?P<tags>[^)]+)\))?, (?P<align>[^\n]+)\nAC (?P<ac>\d+)")


def parse_monsters(text: str, group: str) -> list[dict]:
    out = []
    heads = list(HEADER.finditer(text))
    for i, m in enumerate(heads):
        block = text[m.start(): heads[i + 1].start() if i + 1 < len(heads) else len(text)]
        def grab(pat: str, default=None):
            mm = re.search(pat, block)
            return mm.group(1).strip() if mm else default
        abilities = {}
        for ab in ("Str", "Dex", "Con", "Int", "Wis", "Cha"):
            mm = re.search(rf"(?i)\b{ab}\s+(\d+)\s+([+-]\d+)", block)
            if mm:
                abilities[ab.lower()] = int(mm.group(1))
        cr = grab(r"\nCR ([\d/]+)")
        xp = grab(r"\nCR [\d/]+ \(XP ([\d,]+)")
        traits = re.findall(r"(?m)^([A-Z][A-Za-z' -]{2,40}(?: \([^)]{1,30}\))?)\. ", block.split("\nActions")[0])
        actions_txt = block.split("\nActions", 1)[1] if "\nActions" in block else ""
        actions = re.findall(r"(?m)^([A-Z][A-Za-z' -]{2,40}(?: \([^)]{1,30}\))?)\. ", actions_txt.split("\nBonus Actions")[0].split("\nReactions")[0].split("\nLegendary Actions")[0])
        out.append({
            "id": "srd:" + re.sub(r"[^a-z0-9]+", "-", m.group("name").lower()).strip("-"),
            "name": m.group("name").strip(),
            "group": group,
            "size": m.group("size"),
            "type": m.group("type"),
            "tags": [t.strip() for t in (m.group("tags") or "").split(",") if t.strip()],
            "alignment": m.group("align").strip(),
            "ac": int(m.group("ac")),
            "hp": int(grab(r"\nHP (\d+)", "0")),
            "hit_dice": grab(r"\nHP \d+ \(([^)]+)\)"),
            "speed": grab(r"\nSpeed ([^\n]+)"),
            "abilities": abilities,
            "senses": grab(r"\nSenses ([^\n]+)"),
            "languages": grab(r"\nLanguages ([^\n]+)"),
            "cr": cr,
            "cr_value": (eval_cr(cr) if cr else None),
            "xp": int(xp.replace(",", "")) if xp else None,
            "traits": [t for t in traits if t not in ("Traits", "Actions")][:12],
            "actions": [a for a in actions if a not in ("Multiattack",) and not a.endswith("damage")][:12],
        })
    # the PDF prints each name twice (bookmark + heading); de-duplicate by id
    seen, uniq = set(), []
    for mon in out:
        if mon["id"] not in seen:
            seen.add(mon["id"])
            uniq.append(mon)
    return uniq


def eval_cr(cr: str) -> float:
    if "/" in cr:
        a, b = cr.split("/")
        return int(a) / int(b)
    return float(cr)


# --------------------------------------------------------------------------- magic items
def parse_items(text: str) -> list[dict]:
    lines = [ln.rstrip() for ln in text.split("\n")]
    out = []
    i = 0
    while i < len(lines) - 1:
        name, nxt = lines[i].strip(), lines[i + 1].strip()
        header = nxt
        if re.match(rf"^({ITEM_CATS})\b", nxt):
            j = i + 1
            while header.endswith(",") and j + 1 < len(lines):  # category wrapped onto the next line
                j += 1
                header += " " + lines[j].strip()
            rar = [r for r in RARITIES if re.search(rf"\b{r}\b", header)]
            if re.search(r"Rarity Varies", header):
                rar = ["Varies"]
            if rar and name and len(name) < 60 and not name.endswith((".", ",")) and name[0].isupper():
                cat = re.match(rf"^({ITEM_CATS})", header).group(1)
                out.append({
                    "id": "srd:" + re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-"),
                    "name": name, "category": cat,
                    "rarity": "Very Rare" if "Very Rare" in rar else rar[0] if len(rar) == 1 else rar[0],
                    "rarities": rar,
                    "attunement": "Requires Attunement" in header,
                    "header": header[:160],
                })
                i = j + 1
                continue
        i += 1
    seen, uniq = set(), []
    for it in out:
        if it["id"] not in seen:
            seen.add(it["id"])
            uniq.append(it)
    return uniq


# --------------------------------------------------------------------------- origins / classes / prices
SPECIES = ["Dragonborn", "Dwarf", "Elf", "Gnome", "Goliath", "Halfling", "Human", "Orc", "Tiefling"]
BACKGROUNDS = ["Acolyte", "Criminal", "Sage", "Soldier"]
CLASSES = ["Barbarian", "Bard", "Cleric", "Druid", "Fighter", "Monk", "Paladin", "Ranger", "Rogue", "Sorcerer",
           "Warlock", "Wizard"]


def parse_species(text: str) -> list[dict]:
    out = []
    for i, sp in enumerate(SPECIES):
        nxt = SPECIES[i + 1] if i + 1 < len(SPECIES) else None
        m = re.search(rf"\n{sp}\nCreature Type: ([^\n]+)\nSize: (.+?)\nSpeed: ([^\n]+)", text, re.S)  # size may wrap
        if not m:
            continue
        body = text[m.end(): text.find(f"\n{nxt}\nCreature Type", m.end()) if nxt else m.end() + 4000]
        traits = re.findall(r"(?m)^ ?([A-Z][A-Za-z' -]{2,30})\. ", body)
        out.append({"id": f"srd:species-{sp.lower()}", "name": sp, "creature_type": m.group(1).strip(),
                    "size": " ".join(m.group(2).split()), "speed": m.group(3).strip(), "traits": traits[:8]})
    return out


def parse_classes(text: str) -> list[dict]:
    out = []
    for cls in CLASSES:
        m = re.search(rf"Core {cls} Traits(.{{0,1200}})", text, re.S)
        block = m.group(1) if m else ""
        prim = re.search(r"Primary Ability ([^\n]+)", block)
        hd = re.search(r"Hit Point Die (D\d+)", block)
        out.append({"id": f"srd:class-{cls.lower()}", "name": cls,
                    "primary_ability": prim.group(1).strip() if prim else None,
                    "hit_die": hd.group(1) if hd else None})
    return out


def parse_backgrounds(text: str) -> list[dict]:
    out = []
    for bg in BACKGROUNDS:
        m = re.search(rf"\n{bg}\nAbility Scores: ([^\n]+)\nFeat: ([^\n]+)\nSkill Proficiencies: ([^\n]+)", text)
        out.append({"id": f"srd:background-{bg.lower()}", "name": bg,
                    "ability_scores": m.group(1).strip() if m else None,
                    "feat": m.group(2).strip() if m else None,
                    "skills": m.group(3).strip() if m else None})
    return out


def parse_prices(text: str) -> list[dict]:
    out = []
    for ln in text.split("\n"):
        m = re.match(r"^([A-Z][A-Za-z ,'()/-]{2,45}?) (\d[\d,]*) (CP|SP|EP|GP|PP)(?: per (day|mile|week))?$", ln.strip())
        if m and not m.group(1).startswith(("Item", "Service")):
            unit = {"CP": 0.01, "SP": 0.1, "EP": 0.5, "GP": 1, "PP": 10}[m.group(3)]
            out.append({"item": m.group(1).strip().rstrip(" -"), "cost": f"{m.group(2)} {m.group(3)}" + (f" per {m.group(4)}" if m.group(4) else ""),
                        "gp": round(int(m.group(2).replace(",", "")) * unit, 3)})
    seen, uniq = set(), []
    for p in out:
        if p["item"] not in seen:
            seen.add(p["item"])
            uniq.append(p)
    return uniq


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("pdf")
    ap.add_argument("--out", default=str(OUT))
    args = ap.parse_args()
    reader = PdfReader(args.pdf)
    sec = outline_sections(reader)
    need = ["Monsters A-Z", "Animals", "Magic Items", "Character Origins", "Classes", "Equipment"]
    missing = [n for n in need if n not in sec]
    if missing:
        sys.exit(f"Unexpected PDF structure; missing sections {missing}. Found: {list(sec)}")
    monsters = parse_monsters(text_of(reader, *sec["Monsters A-Z"]), "monster") + \
        parse_monsters(text_of(reader, *sec["Animals"]), "animal")
    items = parse_items(text_of(reader, sec["Magic Items"][0], sec["Magic Items"][1]))
    origins = text_of(reader, *sec["Character Origins"])
    catalogue = {
        "source": "System Reference Document 5.2.1",
        "license": "CC-BY-4.0",
        "attribution": ATTRIBUTION,
        "monsters": monsters,
        "magic_items": items,
        "species": parse_species(origins),
        "backgrounds": parse_backgrounds(origins),
        "classes": parse_classes(text_of(reader, *sec["Classes"])),
        "prices": parse_prices(text_of(reader, *sec["Equipment"])),
    }
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(catalogue, indent=1, ensure_ascii=False), encoding="utf-8")
    for k in ("monsters", "magic_items", "species", "backgrounds", "classes", "prices"):
        print(f"{k:12} {len(catalogue[k])}")
    print(f"-> {args.out}")


if __name__ == "__main__":
    main()

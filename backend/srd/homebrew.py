"""Homebrew: the DM's own creatures, peoples and items, beside the SRD.

Entries are ``CatalogueEntry`` documents shaped like SRD entries (ids ``home:<uid>``) and live in TerminusDB, so
they are versioned like everything else:

* the **library** (database ``HOMEBREW_DB``) holds the DM's collection, shared by every world;
* the wizard **copies** the library into each world it builds, so a world keeps the homebrew it was made with;
* lookups for a campaign (``overlay_entries``) use its own copies first, then the library, then the SRD.

``kind`` is ``monster`` (stat block: size, type, ac, hp, speed, cr, traits, actions, ``biomes`` = Azgaar biome
ids it lives in, none meaning anywhere), ``species`` (a playable people: size, speed, creature_type, traits) or
``item`` (a treasure: category, rarity, attunement). Entries started from an SRD entry keep its CC-BY attribution.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any

from config import Settings, get_settings
from graph.schema import doc_id
from graph.store import GraphStore
from srd import catalogue

KINDS = ("monster", "species", "item")
# Azgaar's biome ids, for a creature's "lives in" list
BIOMES = {1: "Hot desert", 2: "Cold desert", 3: "Savanna", 4: "Grassland", 5: "Tropical seasonal forest",
          6: "Temperate deciduous forest", 7: "Tropical rainforest", 8: "Temperate rainforest", 9: "Taiga",
          10: "Tundra", 11: "Glacier", 12: "Wetland"}
SIZES = ("Tiny", "Small", "Medium", "Large", "Huge", "Gargantuan")
RARITIES = ("Common", "Uncommon", "Rare", "Very Rare", "Legendary", "Artifact")
FIELDS = {  # editable fields per kind, beyond name/kind/tags/overrides/description
    "monster": ("size", "type", "alignment", "ac", "hp", "speed", "cr", "traits", "actions", "biomes"),
    "species": ("size", "speed", "creature_type", "traits"),
    "item": ("category", "rarity", "attunement"),
}
_CR_FRACTIONS = {"1/8": 0.125, "1/4": 0.25, "1/2": 0.5}


class HomebrewError(ValueError):
    pass


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:60] or "entry"


def cr_value(cr: Any) -> float | None:
    if cr is None or cr == "":
        return None
    if str(cr) in _CR_FRACTIONS:
        return _CR_FRACTIONS[str(cr)]
    try:
        return float(cr)
    except ValueError as e:
        raise HomebrewError(f"Challenge rating {cr!r} isn't a number or 1/8, 1/4, 1/2") from e


# --------------------------------------------------------------------------- shape
def clean(entry: dict[str, Any]) -> dict[str, Any]:
    """Validate an entry from the editor or an import and normalise it to the catalogue shape."""
    kind = entry.get("kind")
    if kind not in KINDS:
        raise HomebrewError(f"kind must be one of {', '.join(KINDS)}")
    name = (entry.get("name") or "").strip()
    if not name or len(name) > 80:
        raise HomebrewError("A name (up to 80 characters) is required")
    out: dict[str, Any] = {"kind": kind, "name": name,
                           "uid": _slug(entry.get("uid") or entry.get("id", "").removeprefix("home:") or name)}
    for f in FIELDS[kind]:
        if entry.get(f) not in (None, ""):
            out[f] = entry[f]
    for f in ("traits", "actions"):
        if f in out:
            v = out[f]
            out[f] = [t.strip() for t in (v.splitlines() if isinstance(v, str) else v) if str(t).strip()][:30]
    if kind == "monster":
        for f, lo, hi in (("ac", 1, 40), ("hp", 1, 2000)):
            if f not in out:
                raise HomebrewError(f"A creature needs {f.upper()}")
            try:
                out[f] = int(out[f])
            except (TypeError, ValueError) as e:
                raise HomebrewError(f"{f.upper()} must be a whole number") from e
            if not lo <= out[f] <= hi:
                raise HomebrewError(f"{f.upper()} must be between {lo} and {hi}")
        out["cr"] = str(out.get("cr") or "0")
        out["cr_value"] = cr_value(out["cr"])
        out["size"] = out.get("size") or "Medium"
        out["type"] = out.get("type") or "Monstrosity"
        out["biomes"] = sorted({int(b) for b in out.get("biomes") or []})
    if kind == "item" and out.get("rarity") and out["rarity"] not in RARITIES:
        raise HomebrewError(f"rarity must be one of {', '.join(RARITIES)}")
    if kind == "item":
        out["rarity"] = out.get("rarity") or "Uncommon"
        out["attunement"] = bool(out.get("attunement"))
    if out.get("size") and out["size"].split()[0] not in SIZES:
        raise HomebrewError(f"size must start with one of {', '.join(SIZES)}")
    out["description"] = (entry.get("description") or "").strip()[:2000] or None
    out["tags"] = sorted({str(t).strip() for t in entry.get("tags") or [] if str(t).strip()})[:20]
    ov = entry.get("overrides") or None
    if ov and not (str(ov).startswith("srd:") and _srd_entry(ov)):
        raise HomebrewError(f"overrides must name an SRD entry (got {ov!r})")
    out["overrides"] = ov
    out["derived_from"] = entry.get("derived_from") or None
    return out


def search_srd(q: str = "", kind: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
    """SRD entries to start a homebrew entry from."""
    base = catalogue.load()
    q = q.strip().lower()
    out = []
    for k, key in catalogue.KIND_KEYS.items():
        if kind and kind != k:
            continue
        for e in base.get(key, []):
            if q in e["name"].lower():
                detail = (f"CR {e.get('cr')} {e.get('size', '')} {e.get('type', '')}" if k == "monster"
                          else e.get("rarity") or e.get("size") or "")
                out.append({"id": e["id"], "name": e["name"], "kind": k, "detail": detail.strip()})
    return sorted(out, key=lambda e: (not e["name"].lower().startswith(q), e["name"]))[:limit]


def _srd_entry(srd_id: str) -> dict[str, Any] | None:
    base = catalogue.load()
    for key in ("monsters", "magic_items", "species"):
        for e in base.get(key, []):
            if e["id"] == srd_id:
                return e
    return None


def to_doc(entry: dict[str, Any]) -> dict[str, Any]:
    data = {k: v for k, v in entry.items() if k not in ("uid", "name", "kind", "tags", "overrides", "derived_from")}
    return {"@type": "CatalogueEntry", "@id": doc_id("CatalogueEntry", entry["uid"]), "uid": entry["uid"],
            "name": entry["name"], "kind": entry["kind"], "data": json.dumps(data, sort_keys=True),
            "tags": entry.get("tags", []), "overrides": entry.get("overrides"),
            "derived_from": entry.get("derived_from"),
            "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}


def from_doc(doc: dict[str, Any]) -> dict[str, Any]:
    """A CatalogueEntry document as a catalogue entry (the shape SRD entries have, plus ``homebrew: True``)."""
    data = json.loads(doc.get("data") or "{}")
    return {**data, "id": f"home:{doc['uid']}", "uid": doc["uid"], "name": doc["name"], "kind": doc["kind"],
            "tags": doc.get("tags", []), "overrides": doc.get("overrides"), "derived_from": doc.get("derived_from"),
            "updated_at": doc.get("updated_at"), "homebrew": True,
            "group": "homebrew" if doc["kind"] == "monster" else data.get("group")}


def from_srd(srd_id: str) -> dict[str, Any]:
    """A starting point for a new entry copied from an SRD entry (the DM renames and edits it)."""
    e = _srd_entry(srd_id)
    if not e:
        raise HomebrewError(f"No SRD entry {srd_id}")
    kind = "monster" if srd_id in {m["id"] for m in catalogue.load()["monsters"]} else \
        "species" if srd_id in {s["id"] for s in catalogue.load()["species"]} else "item"
    draft = {f: e.get(f) for f in FIELDS[kind] if e.get(f) is not None}
    if kind == "species":
        draft["size"] = (e.get("size") or "Medium").split(" (")[0]
    return {"kind": kind, "name": f"{e['name']} (homebrew)", **draft, "derived_from": srd_id, "tags": [],
            "description": None, "overrides": None}


# --------------------------------------------------------------------------- storage
_READY: set[str] = set()  # library databases already created / schema-checked by this process


def library(settings: Settings | None = None) -> GraphStore:
    """The homebrew library database (created with the schema on first use)."""
    s = settings or get_settings()
    st = GraphStore(s, s.homebrew_db)
    if s.homebrew_db not in _READY:
        if not st.db_exists():
            # its own description, so the campaign list (which looks for the campaign marker) leaves it out
            st._root().create_database(st.db, team=st.team, label="Homebrew library",
                                       description="Living World Engine homebrew library")
        st.apply_schema()
        _READY.add(s.homebrew_db)
    return st


def entries(store: GraphStore, branch: str = "main") -> list[dict[str, Any]]:
    try:
        docs = store.all_documents(branch, doc_type="CatalogueEntry")
    except Exception:  # noqa: BLE001 - a campaign made before homebrew existed has no such class yet
        return []
    return sorted((from_doc(d) for d in docs if d.get("@type") == "CatalogueEntry"),
                  key=lambda e: (e["kind"], e["name"].lower()))


def save(store: GraphStore, entry: dict[str, Any], *, replace_uid: str | None = None) -> dict[str, Any]:
    """Create or update an entry (``replace_uid``: the uid it had, when a rename changes it)."""
    e = clean(entry)
    store.apply_schema()
    store.upsert([to_doc(e)], "main", f"[homebrew] save {e['kind']} '{e['name']}'")
    if replace_uid and _slug(replace_uid) != e["uid"]:
        remove(store, replace_uid)
    return from_doc(to_doc(e))


def remove(store: GraphStore, uid: str) -> None:
    uid = uid.removeprefix("home:")
    store.delete([doc_id("CatalogueEntry", uid)], "main", f"[homebrew] remove {uid}")


def import_entries(store: GraphStore, items: list[dict[str, Any]]) -> dict[str, Any]:
    """Add or update several entries (an exported JSON file); invalid ones are reported, not fatal."""
    good, bad = [], []
    for i, raw in enumerate(items):
        try:
            good.append(clean(raw))
        except HomebrewError as e:
            bad.append({"index": i, "name": (raw or {}).get("name"), "error": str(e)})
    if good:
        store.apply_schema()
        store.upsert([to_doc(e) for e in good], "main", f"[homebrew] import {len(good)} entries")
    return {"imported": len(good), "rejected": bad}


def export(store: GraphStore) -> list[dict[str, Any]]:
    keep = {"kind", "name", "uid", "tags", "overrides", "derived_from", "description", *sum(FIELDS.values(), ())}
    return [{k: v for k, v in e.items() if k in keep and v not in (None, [], "")} for e in entries(store)]


def copy_into(campaign: GraphStore, items: list[dict[str, Any]]) -> int:
    """Give a world its own copy of homebrew entries (the wizard does this, so the world keeps what it was made with)."""
    if not items:
        return 0
    campaign.apply_schema()
    campaign.upsert([to_doc(clean(e)) for e in items], "main", f"[homebrew] {len(items)} entries from the library")
    return len(items)


def overlay_entries(campaign: GraphStore | None, settings: Settings | None = None) -> list[dict[str, Any]]:
    """What lookups for this campaign layer over the SRD: its own copies, then library entries it lacks."""
    own = entries(campaign) if campaign else []
    try:
        lib = entries(library(settings or (campaign.s if campaign else None)))
    except Exception:  # noqa: BLE001 - no library yet (or TerminusDB down): just the campaign's own
        lib = []
    have = {e["uid"] for e in own}
    return own + [e for e in lib if e["uid"] not in have]

"""Build a new campaign world from wizard answers, one `[setup]` commit per Guidebook step.

    1 World      planet (generated, or the user's Azgaar Full JSON) -> atlas layers
    2 Continents named landmasses
    3 Kingdoms   realms, provinces, towns, diplomacy (Azgaar importer) + rulers, faiths, gods
    4 Local area starting town: two rival local factions, notable NPCs with conflicting
                 WANTS / NEEDS / FEARS, a contested SRD magic item, the party, tavern rumors
    5 History    founding, wars, a catastrophe, a hero's saga (negative in-world days),
                 SRD monsters in every province's wilds

Each world lives in its own TerminusDB database (a "campaign"), so worlds never mix.
"""
from __future__ import annotations

import json
import logging
import random
import threading
import time
import uuid
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from atlas.azgaar import AzgaarWorld
from atlas.sample_world import SYLLABLES, generate
from atlas.service import atlas_root, build_tiles
from config import get_settings
from graph import builders as b
from graph.schema import slugify, stable_uid
from graph.store import GraphStore, get_store
from srd import catalogue as srd
from wizard import spec as S

log = logging.getLogger(__name__)
TITLES = {"Kingdom": ("King", "Queen"), "Duchy": ("Duke", "Duchess"), "Republic": ("First Consul", "First Consul"),
          "Theocracy": ("High Priest", "High Priestess"), "Empire": ("Emperor", "Empress"),
          "Principality": ("Prince", "Princess")}
SIZE_RADIUS = {"small": 16.0, "medium": 12.0, "large": 9.5}


# =========================================================================== jobs
@dataclass
class WizardJob:
    id: str
    status: str = "queued"
    events: list[dict[str, Any]] = field(default_factory=list)
    result: dict[str, Any] | None = None
    error: str | None = None
    created_at: float = field(default_factory=time.time)

    def emit(self, step: str, kind: str, message: str, data: Any = None) -> None:
        self.events.append({"seq": len(self.events), "ts": time.time(), "step": step, "kind": kind,
                            "message": message, "data": data})

    def dump(self) -> dict[str, Any]:
        return {"id": self.id, "status": self.status, "events": self.events, "result": self.result, "error": self.error}


JOBS: dict[str, WizardJob] = {}


def start_build(answers: dict[str, Any]) -> WizardJob:
    job = WizardJob(id=uuid.uuid4().hex[:12])
    JOBS[job.id] = job

    def work() -> None:
        job.status = "running"
        try:
            job.result = build_world(answers, job.emit)
            job.status = "complete"
        except Exception as e:  # noqa: BLE001 - shown in the UI
            log.exception("World build failed")
            job.status = "failed"
            job.error = f"{type(e).__name__}: {e}"
            job.emit("error", "error", job.error)

    threading.Thread(target=work, daemon=True, name=f"wizard-{job.id}").start()
    return job


def merge_answers(answers: dict[str, Any]) -> dict[str, Any]:
    base = S.defaults(answers.get("focus") or "classic-realms")
    out = {**base, **{k: v for k, v in answers.items() if not isinstance(v, dict)}}
    for step in S.STEP_IDS:
        out[step] = {**base[step], **{k: v for k, v in (answers.get(step) or {}).items() if v not in (None, "")}}
    return out


# =========================================================================== naming helpers
class Namer:
    def __init__(self, rng: random.Random) -> None:
        self.rng = rng
        self.used: set[str] = set()

    def word(self, culture: int, parts: int | None = None) -> str:
        syl = SYLLABLES[max(0, culture - 1) % len(SYLLABLES)]
        for _ in range(20):
            w = "".join(self.rng.choice(syl) for _ in range(parts or self.rng.choice([2, 2, 3]))).capitalize()
            if w not in self.used:
                self.used.add(w)
                return w
        return w

    def person(self, culture: int) -> str:
        return f"{self.word(culture, 2)} {self.word(culture, self.rng.choice([2, 3]))}"


def upload_dir() -> Path:
    p = atlas_root() / "uploads"
    p.mkdir(parents=True, exist_ok=True)
    return p


# =========================================================================== the build
def build_world(answers: dict[str, Any], emit: Callable[..., None] | None = None) -> dict[str, Any]:
    """Build a new campaign world. The DM's homebrew library is layered over the SRD for the whole build (creatures in
    the wilds, peoples among the cultures and NPCs, treasures) and copied into the new world."""
    from srd import homebrew
    try:
        hb_entries = homebrew.entries(homebrew.library())
    except Exception:  # noqa: BLE001 - no library (or it can't be reached): build from the SRD alone
        log.warning("Homebrew library unavailable; building from the SRD only", exc_info=True)
        hb_entries = []
    with srd.homebrew(hb_entries):
        return _build_world(answers, emit, hb_entries)


def _build_world(answers: dict[str, Any], emit: Callable[..., None] | None, hb_entries: list[dict[str, Any]]
                 ) -> dict[str, Any]:
    emit = emit or (lambda *a, **k: None)
    a = merge_answers(answers)
    seed = int(a["world"].get("seed") or random.randrange(1, 10**9))
    rng = random.Random(seed)
    namer = Namer(rng)
    name = (a["world"].get("name") or "").strip() or rng.choice(S.spec()["rolls"]["world_name"])
    slug = f"{slugify(name)}-{seed % 10000}"
    db = ("campaign_" + slug.replace("-", "_"))[:60]
    root_store = get_store()
    root_store.create_campaign(db, name)
    store = GraphStore(root_store.s, db)
    summary: dict[str, Any] = {"campaign": db, "name": name, "seed": seed, "atlas": slug, "steps": {}}
    if hb_entries:
        from srd import homebrew
        summary["homebrew"] = homebrew.copy_into(store, hb_entries)

    def commit(step_no: int, step: str, docs: list[dict[str, Any]], message: str) -> None:
        store.upsert(docs, "main", f"[setup] {step_no}/5 {message}")
        summary["steps"][step] = {"documents": len(docs), "message": message}
        emit(step, "done", message, {"documents": len(docs)})

    # ------------------------------------------------------------------ 1. world
    emit("world", "start", "Shaping the planet")
    folder = atlas_root() / slug
    folder.mkdir(parents=True, exist_ok=True)
    k = a["kingdoms"]
    style = S.CULTURE_STYLES.get(k["culture_style"], S.CULTURE_STYLES["high-fantasy"])[:]
    rng.shuffle(style)
    religions = []
    symbols = S.RELIGION_SYMBOLS[:]
    rng.shuffle(symbols)
    for i in range(int(k["n_religions"])):
        pat, kind = S.RELIGION_PATTERNS[i % len(S.RELIGION_PATTERNS)]
        religions.append((pat.format(symbols[i % len(symbols)]), kind))
    if a["world"].get("source") == "azgaar" and a["world"].get("upload_id"):
        src = upload_dir() / f"{a['world']['upload_id']}.json"
        if not src.exists():
            raise ValueError("Uploaded Azgaar file not found; upload it again")
    else:
        raw = generate(seed, name=name, radius=SIZE_RADIUS.get(a["world"]["size"], 12.0),
                       n_states=int(k["n_states"]), land=a["continents"]["land"], climate=a["world"]["climate"],
                       n_cultures=int(k["n_cultures"]), n_religions=int(k["n_religions"]), hostility=k["hostility"],
                       culture_names=style, religion_names=religions)
        src = folder / "source.full.json"
        src.write_text(json.dumps(raw), encoding="utf-8")
    world = AzgaarWorld.load(src, slug)
    layers = world.layers()
    for lname, fc in layers.items():
        (folder / f"{lname}.geojson").write_text(json.dumps(fc), encoding="utf-8")
    has_tiles = build_tiles(folder)
    bb = world.bbox()
    atlas = world.atlas_doc(list(layers), has_tiles, ((bb[0] + bb[2]) / 2, (bb[1] + bb[3]) / 2))
    atlas["name"] = name
    commit(1, "world", [atlas], f"World: {name} ({a['world']['climate'].replace('-', ' ')}, seed {seed})"
           + (f" — {a['world']['lore']}" if a["world"].get("lore") else ""))

    # ------------------------------------------------------------------ 2. continents
    emit("continents", "start", "Naming the continents")
    cells = world.cells
    by_feature: dict[int, list[dict]] = defaultdict(list)
    for c in cells:
        if c.get("h", 0) >= 20:
            by_feature[c.get("f", 0)].append(c)
    cont_docs = []
    for fid, cs in sorted(by_feature.items(), key=lambda kv: -len(kv[1])):
        if len(cs) < 25 or len(cont_docs) >= 8:
            continue
        xs = [c["p"][0] for c in cs]
        ys = [c["p"][1] for c in cs]
        lon, lat = world.lonlat(sum(xs) / len(xs), sum(ys) / len(ys))
        culture = Counter(c.get("culture", 1) for c in cs).most_common(1)[0][0] or 1
        kind = "continent" if len(cs) > 150 else "island"
        cont_docs.append(b.entity("Location", namer.word(culture) + (" Isle" if kind == "island" else ""),
                                  uid=f"land-{fid}", location_type=kind, lon=round(lon, 3), lat=round(lat, 3),
                                  session="setup/continents", population=None,
                                  description=f"A {kind} of {len(cs)} leagues of settled and wild land."))
    commit(2, "continents", cont_docs, f"Continents: {len(cont_docs)} landmasses ({a['continents']['land'].replace('-', ' ')})")

    # ------------------------------------------------------------------ 3. kingdoms
    emit("kingdoms", "start", "Founding realms, faiths and rulers")
    docs = world.graph_docs(layers)
    pack = world.raw["pack"]
    species_all = srd.species_names()
    culture_species: dict[int, list[str]] = {}
    for cul in [c for c in pack.get("cultures", []) if isinstance(c, dict) and c.get("i")]:
        if k["species_mix"] == "human-led":
            culture_species[cul["i"]] = ["Human"]
        elif k["species_mix"] == "many-peoples":
            culture_species[cul["i"]] = rng.sample(species_all, 2)
        else:
            culture_species[cul["i"]] = [rng.choice(species_all)]
    # The DM's own peoples always find a home: each homebrew species joins at least one culture
    # (a human-led world stays human-led).
    hb_species = srd.homebrew_names("species")
    if hb_species and k["species_mix"] != "human-led" and culture_species:
        cultures = sorted(culture_species)
        for n, sp in enumerate(hb_species):
            if not any(sp in v for v in culture_species.values()):
                culture_species[cultures[n % len(cultures)]].append(sp)
    states = world.live(pack.get("states", []))
    sid = {s["i"]: f"Faction/{world.state_uid(s['i'])}" for s in states}
    burg_loc = {bg["i"]: f"Location/{world.uid('b', bg['i'], bg.get('name', ''))}" for bg in world.live(pack.get("burgs", []))}
    religion_by_state: dict[int, Counter] = defaultdict(Counter)
    for c in cells:
        if c.get("h", 0) >= 20 and c.get("state"):
            religion_by_state[c["state"]][c.get("religion", 0)] += 1
    rulers = 0
    for s in states:
        cul = s.get("culture") or 1
        sp = rng.choice(culture_species.get(cul, ["Human"]))
        title = rng.choice(TITLES.get(s.get("form", "Kingdom"), ("Lord", "Lady")))
        person = namer.person(cul)
        cid = f"ruler-{s['i']}-{slugify(person)}"
        tmpl = srd.template_for_role("ruler")
        docs.append(b.entity("Character", f"{title} {person}", uid=cid, kind="npc", is_major=True, session="setup/kingdoms",
                             role=f"{title} of {s.get('fullName') or s['name']}", faction=sid[s["i"]],
                             location=burg_loc.get(s.get("capital")), species=sp,
                             char_class=srd.class_for_role("ruler", rng), background="Soldier", level=rng.randint(5, 12),
                             srd_ref=tmpl["id"] if tmpl else None, personality=rng.choice(S.PERSONALITIES),
                             aliases=[person, f"the {title}"], tags=[sp]))
        docs += [b.relationship(f"Character/{cid}", sid[s["i"]], 70, "rules"),
                 b.knows(f"Character/{cid}", sid[s["i"]])]
        if burg_loc.get(s.get("capital")):
            docs.append(b.knows(f"Character/{cid}", burg_loc[s["capital"]]))
        rulers += 1
    # faiths: organised religions get a church faction with a leader; every religion gets a god
    faiths = 0
    for rel in [r for r in pack.get("religions", []) if isinstance(r, dict) and r.get("i")]:
        domain = rng.choice(S.DEITY_DOMAINS)
        god = b.entity("Character", f"{namer.word(1, 2)}, god of {domain}", uid=f"deity-{rel['i']}", kind="deity",
                       session="setup/kingdoms", role=f"Deity worshipped by the {rel['name']}",
                       description=f"Patron of {domain}, honoured by the {rel['name']}.")
        docs.append(god)
        home_state = max(states, key=lambda s: religion_by_state[s["i"]].get(rel["i"], 0), default=None)
        if rel.get("type") in ("Organized", "Cult") and home_state:
            church = b.entity("Faction", rel["name"], uid=f"faith-{rel['i']}-{slugify(rel['name'])}", is_major=True,
                              session="setup/kingdoms", resources=30 if rel.get("type") == "Organized" else 15,
                              influence=45 if rel.get("type") == "Organized" else 20,
                              headquarters=burg_loc.get(home_state.get("capital")), tags=["faith", rel.get("type", "")],
                              description=f"{'A hidden' if rel.get('type') == 'Cult' else 'An established'} faith devoted to {god['name']}.")
            docs.append(church)
            leader_name = namer.person(home_state.get("culture") or 1)
            lid = f"faith-leader-{rel['i']}-{slugify(leader_name)}"
            tmpl = srd.template_for_role("priest" if rel.get("type") == "Organized" else "cultist")
            docs.append(b.entity("Character", leader_name, uid=lid, kind="npc", is_major=True, session="setup/kingdoms",
                                 role=f"{'High Priest' if rel.get('type') == 'Organized' else 'Hierophant'} of the {rel['name']}",
                                 faction=church["@id"], location=church.get("headquarters"),
                                 species=rng.choice(culture_species.get(home_state.get("culture") or 1, ["Human"])),
                                 char_class="Cleric", background="Acolyte", level=rng.randint(4, 10),
                                 srd_ref=tmpl["id"] if tmpl else None, personality=rng.choice(S.PERSONALITIES)))
            docs += [b.knows(church["@id"], god["@id"]), b.knows(f"Character/{lid}", god["@id"]),
                     b.desire(church["@id"], "WANTS", text="more faithful across the realms", priority=0.6)]
            for s in states:
                share = religion_by_state[s["i"]].get(rel["i"], 0)
                if share:
                    trust = 40 if rel.get("type") == "Organized" else -30
                    docs += [b.relationship(sid[s["i"]], church["@id"], trust, "faithful" if trust > 0 else "heretics"),
                             b.knows(sid[s["i"]], church["@id"]), b.knows(church["@id"], sid[s["i"]])]
            faiths += 1
    for d in docs:
        d.setdefault("source_session", "setup/kingdoms")
    n_prov = sum(d["@type"] == "Province" for d in docs)
    commit(3, "kingdoms", docs, f"Kingdoms: {len(states)} realms, {n_prov} provinces, {faiths} faiths, {rulers} rulers")
    kingdom_docs = {d["@id"]: d for d in docs}

    # ------------------------------------------------------------------ 4. local area
    emit("local", "start", "Building the starting town")
    local_docs, town = build_local(a["local"], kingdom_docs, rng, namer, culture_species)
    host = next(d for d in local_docs if d["@id"] == town["id"])
    local_docs.append({**atlas, "centre": json.dumps([host["lon"], host["lat"]])})  # open the globe on the start town
    commit(4, "local", local_docs, f"Local area: {town['name']} — {town['factions']}, {town['notables']} notables")

    # ------------------------------------------------------------------ 5. history
    emit("history", "start", "Writing history and stocking the wilds")
    hist_docs, hist = build_history(a["history"], world, kingdom_docs, town, rng, namer)
    commit(5, "history", hist_docs, f"History: {hist['events']} events over {a['history']['years_back']} years, "
                                     f"{hist['monsters']} monster groups in {hist['wilds']} wilds")
    summary["town"] = town["name"]

    # ------------------------------------------------------------------ prose (real model only)
    if a["history"].get("prose", True):
        summary["prose"] = write_world_prose(store, a, name, emit)
    return summary


def write_world_prose(store: GraphStore, answers: dict[str, Any], world_name: str,
                      emit: Callable[..., None]) -> dict[str, Any]:
    """The model rewrites the template descriptions (``wizard.prose``); mock mode keeps them. One ``[setup] prose``
    commit when anything changed. Never fails the build: a model that is down or replies badly keeps the templates."""
    from llm import mock
    from wizard import prose
    s = get_settings()
    if s.mock_llm:
        resolve = lambda system, user: mock.wizard_prose(user)  # noqa: E731
    else:
        from llm.client import get_llm
        resolve = lambda system, user: get_llm().structured(system, user, prose.ProseBatch, temperature=0.8)  # noqa: E731
        emit("prose", "start", "Writing descriptions")
    try:
        changed, stats = prose.write_prose(
            store.all_documents("main"), answers, world_name, resolve=resolve, concurrency=s.llm_concurrency,
            progress=None if s.mock_llm else lambda n, of: emit("prose", "progress", f"Writing descriptions ({n}/{of})"))
    except Exception as e:  # noqa: BLE001
        log.warning("Prose pass failed; the world keeps its template descriptions", exc_info=True)
        if not s.mock_llm:
            emit("prose", "done", f"Descriptions: kept the templates ({type(e).__name__})")
        return {"written": 0, "error": f"{type(e).__name__}: {e}"}
    if changed:
        message = (f"Prose: {stats['written']} descriptions written by {s.llm_model}"
                   + (f" ({stats['failed_batches']} of {stats['batches']} batches kept their templates)"
                      if stats["failed_batches"] else ""))
        store.upsert(changed, "main", f"[setup] prose: {message[len('Prose: '):]}")
        if not s.mock_llm:
            emit("prose", "done", message, {"documents": len(changed)})
    elif not s.mock_llm:
        emit("prose", "done", "Descriptions: kept the templates")
    return stats


# =========================================================================== step 4
def build_local(ans: dict[str, Any], kd: dict[str, dict[str, Any]], rng: random.Random, namer: Namer,
                culture_species: dict[int, list[str]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    session = "setup/local"
    holder = {d["target_id"]: d["from_id"] for d in kd.values() if d["@type"] == "ControlEdge"}
    rivalrous = {d["from_id"] for d in kd.values() if d["@type"] == "RelationshipEdge" and d["label"] in ("rival", "enemy")}
    towns = [d for d in kd.values() if d["@type"] == "Location" and d.get("location_type") in ("town", "city")
             and d.get("province") and abs(d.get("lat") or 90) < 55]
    towns.sort(key=lambda d: ("port" not in d.get("tags", []), holder.get(d["province"]) not in rivalrous, d["uid"]))
    if not towns:
        towns = [d for d in kd.values() if d["@type"] == "Location" and d.get("province")]
    host = dict(towns[0])
    realm = holder.get(host["province"])
    realm_doc = kd.get(realm, {})
    culture = 1 + (sum(map(ord, host["uid"])) % 5)
    species = culture_species.get(culture) or [rng.choice(srd.species_names())]
    # homebrew peoples living anywhere in this world also live in the starting town
    placed = {sp for v in culture_species.values() for sp in v}
    species = species + [sp for sp in srd.homebrew_names("species") if sp in placed and sp not in species]
    docs: list[dict[str, Any]] = []
    if ans.get("town_name"):
        host["aliases"] = sorted(set(host.get("aliases", [])) | {host["name"]})
        host["name"] = ans["town_name"]
    host["tags"] = sorted(set(host.get("tags", [])) | {"campaign-start"})
    host["description"] = host.get("description") or f"A {'harbour ' if 'port' in host.get('tags', []) else ''}town where the campaign begins."
    docs.append(host)
    T, lon, lat = host["@id"], host["lon"], host["lat"]
    tmpl = S.LOCAL_FACTIONS.get(ans["local_factions"], S.LOCAL_FACTIONS["house-vs-guild"])
    fill = {"surname": namer.word(culture, 2), "surname2": namer.word(culture, 2), "adj": rng.choice(S.ADJECTIVES),
            "symbol": rng.choice(S.RELIGION_SYMBOLS), "town": host["name"]}

    def place(name: str, kind: str, dx: float, dy: float, **kw) -> dict[str, Any]:
        d = b.entity("Location", name, uid=f"{slugify(name)}-{host['uid'][-6:]}", location_type=kind, parent=T,
                     lon=round(lon + dx, 4), lat=round(lat + dy, 4), province=host["province"], session=session, **kw)
        docs.append(d)
        return d

    tavern_name = ans.get("tavern_name") or rng.choice(S.TAVERN_PATTERNS).format(
        rng.choice(S.TAVERN_WORDS[0]), rng.choice(S.TAVERN_WORDS[1]))
    tavern = place(tavern_name, "tavern", 0.05, 0.03, tags=["tavern"], description="Where the town trades news, ale and lies.")
    prize = place(tmpl["prize"][0].format(**fill), tmpl["prize"][1], 0.12, -0.04,
                  description="Whoever holds it holds the town's coin.")
    seat = place(tmpl["seat"][0].format(**fill), tmpl["seat"][1], -0.08, 0.07, description="Seat of the town's rulers.")

    def faction(name: str, desc: str, res: int, hq: str) -> dict[str, Any]:
        d = b.entity("Faction", name, uid=f"{slugify(name)}-{host['uid'][-6:]}", is_major=True, resources=res,
                     influence=res + 10, headquarters=hq, session=session, description=f"The town's {desc}.")
        docs.append(d)
        return d

    fa = faction(tmpl["a"][0].format(**fill), tmpl["a"][1], 55, seat["@id"])
    fb = faction(tmpl["b"][0].format(**fill), tmpl["b"][1], 50, prize["@id"])
    party = b.entity("Faction", ans.get("party_name") or "The Company", uid=f"party-{host['uid'][-6:]}", is_party=True,
                     resources=20, session=session, description="The player characters' company.",
                     aliases=["the party", "the adventurers"])
    docs.append(party)
    item_src = srd.magic_items(rng, rarities=(ans.get("macguffin_rarity") or "Very Rare",), k=1)
    item_src = item_src[0] if item_src else {"name": "The Sealed Reliquary", "id": None, "rarity": "Very Rare"}
    item = b.entity("Item", item_src["name"], uid=f"{slugify(item_src['name'])}-{host['uid'][-6:]}", item_type="magic item",
                    rarity=item_src["rarity"], srd_ref=item_src.get("id"), location=seat["@id"], value=5000,
                    session=session, description=f"A {item_src['rarity'].lower()} treasure that both factions want.")
    docs.append(item)

    roles = [("a_lead", tmpl["a"][2]), ("b_lead", tmpl["b"][2]), ("captain", "captain"), ("innkeeper", "innkeeper"),
             ("idealist", "priest"), ("spy", "spy"), ("mage", "mage"), ("hunter", "hunter")]
    roles = roles[:max(3, min(8, int(ans.get("n_notables") or 5)))]
    npc: dict[str, dict[str, Any]] = {}
    role_titles = {"a_lead": f"head of {fa['name']}", "b_lead": f"master of {fb['name']}", "captain": "captain of the watch",
                   "innkeeper": f"keeper of {tavern_name}", "idealist": "town priest", "spy": "information broker",
                   "mage": "hedge wizard", "hunter": "warden of the wilds"}
    homes = {"a_lead": seat, "b_lead": prize, "captain": prize, "innkeeper": tavern, "idealist": host,
             "spy": tavern, "mage": host, "hunter": host}
    for key, role in roles:
        person = namer.person(culture)
        t = srd.template_for_role(role)
        d = b.entity("Character", person, uid=f"{slugify(person)}-{host['uid'][-6:]}", kind="npc",
                     is_major=key in ("a_lead", "b_lead"), session=session, role=role_titles[key],
                     faction=fa["@id"] if key in ("a_lead", "captain") else fb["@id"] if key == "b_lead" else None,
                     location=homes[key]["@id"], species=rng.choice(species), char_class=srd.class_for_role(role, rng),
                     background=srd.ROLE_BACKGROUNDS.get(role), level=rng.randint(1, 8),
                     srd_ref=t["id"] if t else None, personality=rng.choice(S.PERSONALITIES),
                     aliases=[person.split()[0]])
        docs.append(d)
        npc[key] = d
    A, B, P = npc["a_lead"]["@id"], npc["b_lead"]["@id"], party["@id"]
    FA, FB, I = fa["@id"], fb["@id"], item["@id"]

    def add(*ds: dict[str, Any]) -> None:
        for d in ds:
            d["source_session"] = session
            docs.append(d)

    add(b.control(FA, "CONTROLS", T), b.control(FA, "CONTROLS", seat["@id"]), b.control(FB, "CONTROLS", prize["@id"]),
        b.control(A, "OWNS", I))
    add(b.desire(A, "WANTS", target=prize["@id"], priority=0.9, note="Its revenue would secure the house."),
        b.desire(A, "FEARS", target=I, priority=0.7, note="Losing it would cost them everything."),
        b.desire(FA, "WANTS", target=prize["@id"], priority=0.8),
        b.desire(B, "WANTS", target=I, priority=0.85, note="Leverage over the town's rulers."),
        b.desire(B, "NEEDS", target=prize["@id"], priority=0.9, note="Losing it ends them."),
        b.desire(FB, "FEARS", target=FA, priority=0.5))
    add(b.relationship(A, B, -60, "rival"), b.relationship(B, A, -55, "rival"), b.relationship(FA, FB, -50, "rival"),
        b.relationship(FB, FA, -50, "rival"), b.relationship(A, P, 5, "hired help"))
    add(b.goal(FA, f"Seize {prize['name']}", priority=0.85, segments=6, progress=1, every=3, target=prize["@id"],
               session=session, uid=f"goal-seize-{host['uid'][-6:]}"),
        b.goal(FB, f"Take {item['name']}", priority=0.75, segments=4, progress=1, every=4, target=I, session=session,
               uid=f"goal-take-{host['uid'][-6:]}"))
    if "captain" in npc:
        C = npc["captain"]["@id"]
        add(b.relationship(C, A, 40, "sworn"), b.relationship(A, C, 35, "vassal"), b.relationship(C, B, 10, "debt"),
            b.relationship(B, C, 20, "asset"), b.desire(C, "NEEDS", text="to clear a debt to the rival faction", priority=0.8),
            b.desire(C, "FEARS", target=A, priority=0.6, note="Being exposed as compromised."), b.knows(C, I, confidence=0.9),
            b.relationship(C, P, -5, "wary"))
    if "innkeeper" in npc:
        K = npc["innkeeper"]["@id"]
        add(b.control(K, "OWNS", tavern["@id"]), b.desire(K, "WANTS", target=tavern["@id"], priority=0.7,
                                                         note="Keep the tavern open and neutral."),
            b.desire(K, "FEARS", text="open war in the streets", priority=0.6), b.relationship(K, P, 20, "regulars"),
            b.relationship(K, B, 15, "supplier"), b.relationship(K, A, -20, "taxed"), b.knows(K, A), b.knows(K, B))
    if "idealist" in npc:
        J = npc["idealist"]["@id"]
        add(b.desire(J, "WANTS", target=A, priority=0.75, note="See the town's rulers answer for their crimes."),
            b.desire(J, "NEEDS", target=I, priority=0.6, note="Proof."), b.relationship(J, A, -40, "suspicion"),
            b.relationship(J, P, 25, "hopeful"), b.knows(J, I, kind="BELIEVES", confidence=0.4),
            b.goal(J, f"Expose {npc['a_lead']['name']}", priority=0.6, segments=6, progress=0, every=5, target=A,
                   session=session, uid=f"goal-expose-{host['uid'][-6:]}"))
    for key in ("spy", "mage", "hunter"):
        if key in npc:
            X = npc[key]["@id"]
            other = rng.choice([A, B])
            add(b.relationship(X, other, rng.choice([-25, 20]), rng.choice(["owes", "informant", "old friend"])),
                b.desire(X, rng.choice(["WANTS", "NEEDS"]), text=rng.choice(["a way out of town", "a patron", "revenge for an old slight",
                                                                            "a lost map to the wilds"]), priority=0.6),
                b.knows(X, other))
    for d in npc.values():
        add(b.knows(d["@id"], T), b.knows(d["@id"], tavern["@id"]), b.knows(d["@id"], prize["@id"]))
    add(b.knows(A, I), b.knows(A, FB), b.knows(B, I, kind="BELIEVES", confidence=0.7), b.knows(B, FA), b.knows(FA, FB),
        b.knows(FB, FA))
    if realm:
        add(b.relationship(FA, realm, 45, "vassal"), b.relationship(realm, FA, 25, "liege"), b.knows(FA, realm),
            b.knows(realm, FA), b.knows(A, realm))
    arrival = b.event(f"{party['name']} arrives in {host['name']}",
                      f"{npc['a_lead']['name']} hired the party; {tavern_name} gave them rooms.",
                      day=0, event_type="world", actors=[P], targets=[A], location=tavern["@id"], session=session,
                      uid=f"evt-arrival-{host['uid'][-6:]}")
    add(arrival, b.knows(P, arrival["@id"]), b.knows(A, arrival["@id"]))
    if "innkeeper" in npc:
        add(b.knows(npc["innkeeper"]["@id"], arrival["@id"]))
    add(b.rumor(f"{fa['name']} would give anything to keep {item['name']} out of the wrong hands.",
                f"\"They say {npc['a_lead']['name']} sleeps with {item['name']} under the pillow. Can't blame them, with "
                f"{fb['name']} sniffing about.\"", location=tavern["@id"], accuracy="Exaggerated", day=0,
                speaker=npc.get("innkeeper", npc["a_lead"])["name"], session=session),
        b.rumor(f"{fa['name']} and {fb['name']} both want {prize['name']}.",
                f"\"Mark my words: there'll be blood over {prize['name']} before the season turns.\"",
                location=tavern["@id"], accuracy="True", day=0, speaker="a dockhand", session=session))
    return docs, {"name": host["name"], "id": T, "tavern": tavern["@id"], "item": I, "a": FA, "b": FB, "party": P,
                  "realm": realm, "factions": f"{fa['name']} vs {fb['name']}", "notables": len(npc),
                  "locals": [d["@id"] for d in npc.values() if d["location"] in (tavern["@id"], T)]}


# =========================================================================== step 5
def build_history(ans: dict[str, Any], world: AzgaarWorld, kd: dict[str, dict[str, Any]], town: dict[str, Any],
                  rng: random.Random, namer: Namer) -> tuple[list[dict[str, Any]], dict[str, int]]:
    session = "setup/history"
    docs: list[dict[str, Any]] = []
    years = max(50, int(ans.get("years_back") or 600))
    total = years * 365
    realms = [d for d in kd.values() if d["@type"] == "Faction" and not d.get("is_party") and "faith" not in d.get("tags", [])]
    events = 0

    def ev(title: str, summary: str, day: int, **kw) -> dict[str, Any]:
        nonlocal events
        e = b.event(title, summary, day=day, session=session, salt=(rng.random(),), **kw)
        docs.append(e)
        events += 1
        return e

    for r in realms:
        e = ev(f"Founding of {r['name']}", f"{r['name']} raised its banners and claimed its first lands.",
               -int(rng.uniform(0.5, 1.0) * total), event_type="world", actors=[r["@id"]], location=r.get("headquarters"))
        docs.append(b.knows(r["@id"], e["@id"], start=e["in_world_start"]))
    seen = set()
    for d in kd.values():
        if d["@type"] == "RelationshipEdge" and d["label"] == "enemy" and d["from_id"] in kd and d["to_id"] in kd:
            pair = tuple(sorted((d["from_id"], d["to_id"])))
            if pair in seen or kd[pair[0]]["@type"] != "Faction" or kd[pair[1]]["@type"] != "Faction":
                continue
            seen.add(pair)
            e = ev(f"The {kd[pair[0]]['name']}–{kd[pair[1]]['name']} War",
                   f"A bitter border war; neither side forgot it.", -int(rng.uniform(0.02, 0.3) * total),
                   event_type="conflict", actors=list(pair), targets=list(pair))
            docs += [b.knows(pair[0], e["@id"], start=e["in_world_start"]), b.knows(pair[1], e["@id"], start=e["in_world_start"])]

    cat = S.CATACLYSMS.get(ans.get("cataclysm") or "none")
    cataclysm = None
    # province -> dominant biome and mean height, for monsters and lairs
    stats: dict[int, dict[str, Any]] = defaultdict(lambda: {"biomes": Counter(), "h": []})
    for c in world.cells:
        if c.get("h", 0) >= 20 and c.get("province"):
            stats[c["province"]]["biomes"][c.get("biome", 0)] += 1
            stats[c["province"]]["h"].append(c["h"])
    provinces = [d for d in kd.values() if d["@type"] == "Province"]
    pid_of = {d["uid"]: d for d in provinces}
    prov_by_azgaar = {}
    for p in world.live(world.raw["pack"].get("provinces", [])):
        uid = world.province_uid(p["i"])
        if uid in pid_of:
            prov_by_azgaar[p["i"]] = pid_of[uid]
    if cat:
        title, blurb = cat
        cataclysm = ev(title, blurb, -int(rng.uniform(0.3, 0.6) * total), event_type="world", visibility="public")
        if ans.get("cataclysm") in ("dragon", "undead") and prov_by_azgaar:
            hi = max(prov_by_azgaar, key=lambda i: sum(stats[i]["h"]) / max(1, len(stats[i]["h"])))
            prov = prov_by_azgaar[hi]
            lair = b.entity("Location", f"The Ruins of {namer.word(1, 2)}", uid=f"lair-{prov['uid']}", location_type="ruin",
                            lon=prov.get("lon"), lat=prov.get("lat"), province=prov["@id"], session=session,
                            description="Where the catastrophe began; nothing sensible goes there.")
            docs.append(lair)
            boss = srd.monster("Adult Red Dragon" if ans.get("cataclysm") == "dragon" else "Mummy Lord")
            if boss:
                docs.append(b.entity("Monster", boss["name"], uid=f"boss-{prov['uid']}", session=session,
                                     challenge=boss["cr"], threat=int(float(boss["cr_value"] or 0)), srd_ref=boss["id"],
                                     location=lair["@id"], count=1, description=f"The power behind {title}."))
            cataclysm["location"] = lair["@id"]

    if ans.get("saga", True):
        hero_name = namer.person(1)
        died = (cataclysm["in_world_start"] if cataclysm else -int(rng.uniform(0.2, 0.5) * total)) + 1
        hero = b.entity("Character", hero_name, uid=f"hero-{slugify(hero_name)}", kind="npc", session=session,
                        role="legendary hero", start=died - 365 * rng.randint(20, 40),
                        description=f"Carried {kd.get(town['item'], {}).get('name', 'a relic')} in the old songs.")
        hero["in_world_end"] = died
        docs.append(hero)
        last = ev(f"The Last Stand of {hero_name}",
                  f"{hero_name} fell holding {kd.get(town['item'], {}).get('name', 'the relic')}; it was lost for an age.",
                  died, event_type="world", actors=[hero["@id"]], targets=[town["item"]],
                  caused_by=cataclysm["@id"] if cataclysm else None)
        for who in town["locals"][:3]:
            docs.append(b.knows(who, last["@id"], kind="BELIEVES", confidence=0.6))
        docs.append(b.rumor(f"{hero_name} still guards {kd.get(town['item'], {}).get('name', 'the relic')} from beyond the grave.",
                            f"\"My gran swore {hero_name}'s ghost walks the wilds whenever that treasure changes hands.\"",
                            location=town["tavern"], accuracy="False", day=0, about_event=last["@id"],
                            speaker="an old drinker", session=session))

    # wilds and monsters
    lo, hi_cr = S.LEVEL_BANDS.get(ans.get("level_band") or "1-4", (0, 4))
    per = S.MONSTER_DENSITY.get(ans.get("monster_density") or "normal", 2)
    monsters = wilds = 0
    used: dict[str, int] = {}
    for ai, prov in prov_by_azgaar.items():
        if prov.get("lon") is None:
            continue
        biome = stats[ai]["biomes"].most_common(1)[0][0] if stats[ai]["biomes"] else 6
        picks = srd.monsters_for_biome(biome, rng, k=per, max_cr=hi_cr, min_cr=0, used=used)
        if not picks:
            continue
        wild = b.entity("Location", f"Wilds of {prov['name'].split()[0]}", uid=f"wilds-{prov['uid']}", location_type="wilderness",
                        lon=round(prov["lon"] + 0.2, 4), lat=round(prov["lat"] - 0.15, 4), province=prov["@id"],
                        session=session, description="Untamed country beyond the roads.")
        docs.append(wild)
        wilds += 1
        for m in picks:
            n = 1 if (m["cr_value"] or 0) >= 3 else rng.randint(2, 8)
            docs.append(b.entity("Monster", m["name"] + (f" pack ({n})" if n > 1 else ""),
                                 uid=f"{slugify(m['name'])}-{prov['uid']}", session=session, challenge=m["cr"],
                                 threat=max(0, int(m["cr_value"] or 0)), srd_ref=m["id"], location=wild["@id"], count=n,
                                 description=f"{m['size']} {m['type'].lower()}, CR {m['cr']} (SRD)."))
            monsters += 1
    for d in docs:
        d.setdefault("source_session", session)
    return docs, {"events": events, "monsters": monsters, "wilds": wilds}

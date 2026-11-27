"""Fighting strength from SRD (and homebrew) challenge ratings: who is on each side of a clash, and how much they count.

A unit's weight is the SRD's encounter XP for its challenge rating, times how many there are; a side's **strength**
is ``10 * log2(1 + xp / 100)``. Logarithmic, so it adds to the arbiter's treasury-and-influence power instead of
drowning it: a goblin is ~6, a CR 2 creature ~25, a pack of six wolves ~20, an adult dragon ~75.

Who fights:

* **attacker** - the acting character and the members of its faction in the same settlement; a faction acting as a
  whole musters all its members;
* **defender** - the target's holder (or a rival intent's agent, or the target itself if it is a person) with its
  faction's members present at the target's settlement, plus every creature at the target place: a lair always
  defends itself.

Challenge ratings come from a unit's own ``challenge``, else its ``srd_ref`` stat block (SRD or ``home:``: run inside
``catalogue.homebrew(...)``), else a creature's ``threat``, an NPC's role ("Captain of the Watch" -> Guard Captain)
or level; an unknown person counts as a commoner.
"""
from __future__ import annotations

import math
import re
from typing import Any

from graph.world import WorldView
from srd import catalogue

XP_BY_CR = {0: 10, 0.125: 25, 0.25: 50, 0.5: 100, 1: 200, 2: 450, 3: 700, 4: 1100, 5: 1800, 6: 2300, 7: 2900,
            8: 3900, 9: 5000, 10: 5900, 11: 7200, 12: 8400, 13: 10000, 14: 11500, 15: 13000, 16: 15000, 17: 18000,
            18: 20000, 19: 22000, 20: 25000, 21: 33000, 22: 41000, 23: 50000, 24: 62000, 25: 75000, 26: 90000,
            27: 105000, 28: 120000, 29: 135000, 30: 155000}
HOSTILE = {"seize", "steal", "sabotage", "attack"}
# Titles in NPC roles -> a catalogue.ROLE_TEMPLATES key ("Baron of Greywater" -> noble -> Noble)
ROLE_WORDS = {"baron": "noble", "baroness": "noble", "lord": "noble", "lady": "noble", "duke": "noble",
              "duchess": "noble", "count": "noble", "countess": "noble", "king": "ruler", "queen": "ruler",
              "prince": "ruler", "princess": "ruler", "emperor": "ruler", "empress": "ruler", "consul": "noble",
              "guildmistress": "boss", "guildmaster": "boss", "chief": "boss", "warlord": "boss",
              "knight": "veteran", "sergeant": "veteran", "soldier": "guard", "watchman": "guard",
              "witch": "mage", "wizard": "mage", "sorcerer": "mage", "warlock": "mage", "archmage": "mage",
              "thief": "spy", "smuggler": "bandit", "brigand": "bandit", "high priest": "priest",
              "priestess": "priest", "cleric": "priest", "warden": "scout", "hunter": "scout", "ranger": "scout"}
_FRACTIONS = {"1/8": 0.125, "1/4": 0.25, "1/2": 0.5}


def cr_number(cr: Any) -> float | None:
    if cr is None or cr == "":
        return None
    if str(cr) in _FRACTIONS:
        return _FRACTIONS[str(cr)]
    try:
        return max(0.0, float(cr))
    except ValueError:
        return None


def xp_for(cr: float) -> int:
    """SRD encounter XP for a challenge rating (nearest listed rating at or below)."""
    keys = [k for k in XP_BY_CR if k <= cr] or [0]
    return XP_BY_CR[max(keys)]


def _cr_label(cr: float) -> str:
    return {0.125: "1/8", 0.25: "1/4", 0.5: "1/2"}.get(cr, f"{cr:g}")


def unit(w: WorldView, doc: dict[str, Any]) -> dict[str, Any]:
    """One combatant: name, challenge rating, how many, and their XP weight."""
    cr = cr_number(doc.get("challenge"))
    if cr is None and doc.get("srd_ref"):
        e = catalogue.by_id(doc["srd_ref"])
        cr = cr_number((e or {}).get("cr_value", (e or {}).get("cr")))
    if cr is None and doc["@type"] == "Monster" and doc.get("threat") is not None:
        cr = float(doc["threat"])
    if cr is None and doc["@type"] == "Character":
        role = (doc.get("role") or "").lower()
        key = next((k for k in catalogue.ROLE_TEMPLATES if re.search(rf"\b{k}\b", role)), None) \
            or next((v for k, v in ROLE_WORDS.items() if re.search(rf"\b{k}\b", role)), None)
        tmpl = catalogue.template_for_role(key) if key else None
        if tmpl:
            cr = cr_number(tmpl.get("cr_value", tmpl.get("cr")))
        elif doc.get("level"):
            cr = max(0.125, int(doc["level"]) / 2)
    cr = 0.0 if cr is None else cr
    count = max(1, int(doc.get("count") or 1))
    return {"id": doc["@id"], "name": doc["name"], "cr": _cr_label(cr), "count": count, "xp": xp_for(cr) * count}


def _label(u: dict[str, Any]) -> str:
    many = f" x{u['count']}" if u["count"] > 1 else ""
    return f"{u['name']}{many} (CR {u['cr']})"


def force(units: list[dict[str, Any]]) -> dict[str, Any]:
    """A side's strength (log-scaled XP) and its strongest units, for the arbiter and the event text."""
    xp = sum(u["xp"] for u in units)
    return {"strength": round(10 * math.log2(1 + xp / 100), 1), "xp": xp,
            "units": [_label(u) for u in sorted(units, key=lambda u: -u["xp"])][:6]}


class Forces:
    """Strength lookups for one world view (settlements cached)."""

    def __init__(self, w: WorldView):
        from atlas.travel import Travel
        self.w = w
        self._travel = Travel(w)
        self._settlement: dict[str | None, str | None] = {}

    def settlement(self, doc_id: str | None) -> str | None:
        if doc_id not in self._settlement:
            self._settlement[doc_id] = self._travel.settlement(doc_id) if doc_id else None
        return self._settlement[doc_id]

    def _faction(self, agent_id: str | None) -> str | None:
        d = self.w.get(agent_id) or {}
        return d.get("faction") if d.get("@type") == "Character" else (agent_id if d.get("@type") == "Faction" else None)

    def side(self, agent_id: str | None, where: str | None) -> list[dict[str, Any]]:
        """An agent and its faction's people at ``where``'s settlement (a whole faction musters everyone)."""
        if not agent_id or not self.w.get(agent_id):
            return []
        me = self.w.get(agent_id)
        fac = self._faction(agent_id)
        people = [c for c in self.w.members_of(fac) if c.get("kind") not in ("deity", "pc")] if fac else []
        if me["@type"] == "Character":
            here = self.settlement(where or me.get("location"))
            out = [me] + [c for c in people if c["@id"] != agent_id and here and self.settlement(c.get("location")) == here]
        else:
            out = people
        return [unit(self.w, d) for d in out]

    def lair(self, place_id: str | None) -> list[dict[str, Any]]:
        if not place_id:
            return []
        inside = {place_id} | {l["@id"] for l in self.w.of_type("Location") if l.get("parent") == place_id}
        return [unit(self.w, m) for m in self.w.of_type("Monster") if m.get("location") in inside]

    def contest(self, intent: dict[str, Any], intents: list[dict[str, Any]] | None = None) -> dict[str, Any] | None:
        """Attacker and defender forces for a hostile intent (None if it isn't one, or has no target)."""
        target = intent.get("target_id")
        if intent.get("action") not in HOSTILE or not target:
            return None
        tdoc = self.w.get(target) or {}
        place = target if target.startswith("Location/") else tdoc.get("location") or tdoc.get("headquarters")
        rival = next((o for o in intents or [] if o is not intent and o.get("target_id") == target
                      and o.get("agent_id") != intent["agent_id"]), None)
        ctl = self.w.controller_of(target)
        defender = (rival or {}).get("agent_id") or (target if tdoc.get("@type") in ("Character", "Faction") else ctl)
        if defender and self._faction(defender) == self._faction(intent["agent_id"]) and self._faction(defender):
            defender = None  # no fight with your own side
        attacker = force(self.side(intent["agent_id"], place))
        defenders = self.side(defender, place) + self.lair(place)
        return {"intent": intent["agent_id"], "target": target, "attacker": attacker,
                "defender": {"id": defender, **force(defenders)}}

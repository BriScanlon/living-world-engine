"""Smoke-test every LLM call the world engine makes, against the configured real model.

Runs each of the five structured calls once on the current campaign (read-only: nothing is written), and reports
for each whether the reply validated and how long it took:

    delta    session summary -> party actions        (SessionDelta)
    agent    one NPC decides what to do              (AgentIntentLLM)
    arbiter  intents -> world events                 (ArbiterResult)
    rumor    events -> tavern rumors                 (RumorBatch)
    extract  lore text -> knowledge graph            (ExtractionResult)

Usage (in Docker):   docker compose exec backend python /app/scripts/llm_smoke.py [json_object|json_schema|none] [calls...]
Usage (local):       cd backend && python ../scripts/llm_smoke.py json_schema agent rumor

It uses LLM_BASE_URL / LLM_MODEL / LLM_API_KEY / LLM_TIMEOUT_SECONDS from the environment (.env), forces
MOCK_LLM=false, and takes the JSON mode from the first argument (default: LLM_JSON_MODE). Exit code 1 if any call
failed.
"""
from __future__ import annotations

import json
import os
import sys
import time
import traceback
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1] / "backend"
sys.path.insert(0, str(BACKEND if BACKEND.exists() else Path.cwd()))

CALLS = ("delta", "agent", "arbiter", "rumor", "extract")
args = sys.argv[1:]
if args and args[0] in ("json_object", "json_schema", "none"):
    os.environ["LLM_JSON_MODE"] = args.pop(0)
wanted = [a for a in args if a in CALLS] or list(CALLS)
os.environ["MOCK_LLM"] = "false"

from config import get_settings  # noqa: E402

get_settings.cache_clear()
from graph.seed import DEMO_SESSION_SUMMARY  # noqa: E402
from graph.store import get_store  # noqa: E402
from graph.world import WorldView  # noqa: E402
from llm.client import get_llm  # noqa: E402
from simulation import prompts  # noqa: E402
from simulation.models import AgentIntentLLM, ArbiterResult, RumorBatch, SessionDelta  # noqa: E402

s = get_settings()
store = get_store()
docs = store.all_documents("main")
w = WorldView(docs, WorldView(docs).max_day())
print(f"model={s.llm_model}  json_mode={s.llm_json_mode}  url={s.llm_base_url}  campaign={store.db}  day={w.day}",
      flush=True)
llm = get_llm()
results: dict[str, dict] = {}


def run(name: str, fn):
    if name not in wanted:
        return None
    print(f"\n--- {name}: calling the model...", flush=True)
    t = time.time()
    try:
        out = fn()
    except Exception as e:  # noqa: BLE001
        results[name] = {"ok": False, "seconds": round(time.time() - t, 1), "error": str(e)[:300]}
        print(f"=== {name}: FAILED after {time.time() - t:.1f}s: {e}", flush=True)
        traceback.print_exc()
        return None
    results[name] = {"ok": True, "seconds": round(time.time() - t, 1)}
    print(f"=== {name}: OK in {time.time() - t:.1f}s", flush=True)
    print(json.dumps(out.model_dump(), indent=1)[:1500], flush=True)
    return out


agents = [c for c in w.of_type("Character") if c.get("kind") == "npc" and c.get("is_major")] or w.of_type("Character")
agent = agents[0] if agents else None

run("delta", lambda: llm.structured(prompts.SESSION_DELTA_SYSTEM,
                                    prompts.session_delta_user(DEMO_SESSION_SUMMARY, w, w.day, w.day + 1), SessionDelta))
intent = run("agent", lambda: llm.structured(prompts.AGENT_SYSTEM,
                                            prompts.agent_user(w.agent_context(agent["@id"]), w.day + 3, (w.day + 1, w.day + 3)),
                                            AgentIntentLLM)) if agent else None
if "arbiter" in wanted and agent:
    sample = [{"agent_id": agent["@id"], "agent": agent["name"], **intent.model_dump()}] if intent else [
        {"agent_id": agent["@id"], "agent": agent["name"], "action": "investigate", "target_id": agent.get("location"),
         "reasoning": "Something is wrong here.", "resources_committed": 1}]
    run("arbiter", lambda: llm.structured(prompts.ARBITER_SYSTEM, prompts.arbiter_user(sample, w, w.day + 3),
                                          ArbiterResult, temperature=0.4))
events = [e for e in w.of_type("Event") if e.get("visibility") in ("public", "leaked")][:2]
if events:
    run("rumor", lambda: llm.structured(prompts.RUMOR_SYSTEM, prompts.rumor_user(events, w), RumorBatch))
if "extract" in wanted:
    from ingestion.extract import llm_extract
    notes = next((p for p in (Path("/app/sample_data"), BACKEND.parent / "sample_data") if p.exists()), None)
    text = (notes / "session-15-notes.md").read_text(encoding="utf-8")[:1800] if notes else DEMO_SESSION_SUMMARY
    run("extract", lambda: llm_extract(text, sorted({d["name"] for d in w.all.values() if d.get("name")})[:120]))

print("\nSUMMARY " + json.dumps(results), flush=True)
ok = sum(r["ok"] for r in results.values())
print(f"{ok}/{len(results)} calls validated; total {sum(r['seconds'] for r in results.values()):.0f}s", flush=True)
sys.exit(0 if results and ok == len(results) else 1)

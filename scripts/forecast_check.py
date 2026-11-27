"""Run one whole forecast on the configured real model and report how it went.

The step after ``llm_smoke.py``: a real forecast on the current campaign, end to end, with every step and every
model call timed. Prints each step as it happens, then a report:

* per LLM call type (SessionDelta, AgentIntentLLM, ArbiterResult, RumorBatch): calls, failures, mean/max seconds;
* what came out: events by type, rumors, epistemic-rule violations, a few sample events and rumors to read;
* the forecast branch, which stays in the campaign so you can open its DM briefing (or discard it).

Usage (in Docker; Git Bash on Windows needs MSYS_NO_PATHCONV=1 in front):
    docker compose exec backend python /app/scripts/forecast_check.py                 # 1 round, demo session notes
    docker compose exec backend python /app/scripts/forecast_check.py --days 9        # 3 rounds of 3 days
    docker compose exec backend python /app/scripts/forecast_check.py --summary /app/sample_data/session-15-notes.md

It forces MOCK_LLM=false (``--mock`` to try the script itself without a model) and uses LLM_* settings from .env.
The run is a normal durable run: if it stops, it shows in the dashboard's Unfinished forecasts and can be resumed.
Exit code 1 if the forecast failed.
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1] / "backend"
sys.path.insert(0, str(BACKEND if BACKEND.exists() else Path.cwd()))

ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
ap.add_argument("--days", type=int, default=None, help="in-world days to simulate (default: one round)")
ap.add_argument("--summary", help="file with the session summary (default: the Greywater demo summary)")
ap.add_argument("--session", type=int, help="session number (default: the campaign's last + 1)")
ap.add_argument("--mock", action="store_true", help="use the mock model (to check the script itself)")
args = ap.parse_args()
os.environ["MOCK_LLM"] = "true" if args.mock else "false"

from config import get_settings  # noqa: E402

get_settings.cache_clear()
from graph.seed import DEMO_SESSION_SUMMARY  # noqa: E402
from graph.store import get_store  # noqa: E402
from graph.world import WorldView  # noqa: E402
from llm import client as llm_client  # noqa: E402
from simulation import engine  # noqa: E402
from simulation.models import ForecastRequest  # noqa: E402
from simulation.runs import registry  # noqa: E402

s = get_settings()
store = get_store().bound()
docs = store.all_documents("main")
w = WorldView(docs, WorldView(docs).max_day())
from api.routes import meta  # noqa: E402 - the dashboard's own "what session is next" logic

session = args.session or meta("main")["last_session"] + 1
days = args.days or s.round_length_days
summary = Path(args.summary).read_text(encoding="utf-8") if args.summary else DEMO_SESSION_SUMMARY

print(f"model={'mock' if s.mock_llm else s.llm_model}  json_mode={s.llm_json_mode}  "
      f"reasoning_effort={s.llm_reasoning_effort or '-'}  concurrency={s.llm_concurrency}  "
      f"max_tokens={s.llm_max_tokens}  timeout={s.llm_timeout_seconds}s", flush=True)
print(f"campaign={store.db}  day={w.day}  session={session}  forecast_days={days} "
      f"(~{-(-days // s.round_length_days)} round(s) of {s.round_length_days} days)\n", flush=True)

# ---- time every model call, by the schema it asks for
calls: dict[str, list[dict]] = defaultdict(list)
real_structured = llm_client.LLMClient.structured


def timed_structured(self, system, user, schema, **kw):
    t = time.monotonic()
    try:
        out = real_structured(self, system, user, schema, **kw)
        calls[schema.__name__].append({"ok": True, "s": time.monotonic() - t})
        return out
    except Exception as e:
        calls[schema.__name__].append({"ok": False, "s": time.monotonic() - t, "error": f"{type(e).__name__}: {e}"})
        raise


llm_client.LLMClient.structured = timed_structured

# ---- print progress as it happens
t0 = time.monotonic()
real_emit = registry.emit


def live_emit(run_id, step, kind, message, data=None):
    real_emit(run_id, step, kind, message, data)
    mark = {"start": "  ..", "done": "  ok", "error": "  !!", "complete": "  ==", "resume": "  >>"}.get(kind, "  --")
    print(f"[{time.monotonic() - t0:7.1f}s]{mark} {message}", flush=True)


registry.emit = live_emit

req = ForecastRequest(session_number=session, start_day=w.day, end_day=w.day + 1, forecast_days=days,
                      session_summary=summary, base_branch="main")
run = registry.create(engine.branch_name(session), req.model_dump(), campaign=store.db)
final: dict = {}
try:
    final = engine.run_sync(run, store, s)
except Exception as e:  # noqa: BLE001 - reported below
    print(f"\nFORECAST FAILED: {type(e).__name__}: {e}", flush=True)
total = time.monotonic() - t0

# ---- report
print("\n=== Steps (seconds)")
starts: dict[tuple, float] = {}
for e in run.events:
    key = (e["step"], (e.get("data") or {}).get("round") if isinstance(e.get("data"), dict) else None)
    if e["kind"] == "start":
        starts[key] = e["ts"]
    elif e["kind"] == "done" and key in starts:
        rnd = f" r{key[1] + 1}" if key[1] is not None and key[0] not in ("fork_canon", "apply_session_delta", "finalize") else ""
        print(f"  {key[0] + rnd:<26} {e['ts'] - starts[key]:7.1f}")

print("\n=== Model calls")
print(f"  {'call':<16} {'n':>4} {'failed':>6} {'mean s':>7} {'max s':>7}")
for name, cs in sorted(calls.items()):
    secs = [c["s"] for c in cs]
    print(f"  {name:<16} {len(cs):>4} {sum(not c['ok'] for c in cs):>6} {sum(secs) / len(secs):>7.1f} {max(secs):>7.1f}")
    for c in [c for c in cs if not c["ok"]][:3]:
        print(f"      ! {c['error'][:160]}")

if final:
    fw = WorldView(store.all_documents(run.branch), 10_000)
    ids = set(final.get("event_reports") and [r["event"] for r in final["event_reports"]] or [])
    events = [fw.get(i) for i in ids if fw.get(i)]
    rumors = [fw.get(i) for i in final.get("rumor_ids", []) if fw.get(i)]
    print("\n=== Outcome")
    print(f"  events: {len(events)}  " + ", ".join(f"{k} {v}" for k, v in Counter(e.get('event_type') for e in events).most_common()))
    print(f"  intents: {len(final.get('all_intents', []))}  "
          + ", ".join(f"{k} {v}" for k, v in Counter(i.get('action') for i in final.get('all_intents', [])).most_common(6)))
    print(f"  rumors: {len(rumors)}  violations (epistemic rule): {len(final.get('violations', []))}")
    for v in final.get("violations", [])[:3]:
        print(f"      ! {str(v)[:160]}")
    print("\n  sample events:")
    for e in sorted(events, key=lambda e: e.get("in_world_start") or 0)[:5]:
        print(f"    d{e.get('in_world_start')} {e['name']}: {(e.get('summary') or '')[:140]}")
    print("  sample rumors:")
    for r in rumors[:3]:
        print(f"    [{r.get('accuracy')}] {(r.get('dialogue') or '')[:140]}")

print(f"\nstatus={run.status}  total={total:.0f}s  branch={run.branch}  run={run.id}")
if run.status != "complete":
    print("The run is saved: resume it from the dashboard (Unfinished forecasts) once the problem is fixed.")
else:
    print("Open the DM Briefing for this branch in the dashboard to read the forecast; discard the branch when done.")
sys.exit(0 if run.status == "complete" else 1)

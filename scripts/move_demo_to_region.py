"""Move the Greywater demo from its old map to a natively generated region (issue #44).

Fresh installs get the region on first start; this moves an existing demo campaign. The old map's documents (and
whatever points at them: House Vane's vassal ties, party marks, zoom-in spots on it) go in one commit, which
TerminusDB keeps in its history; then the region is imported and Greywater placed on one of its ports. Forecast
branches made on the old map are left as they are.

Usage (in Docker; Git Bash on Windows needs MSYS_NO_PATHCONV=1 in front):
    docker compose exec backend python /app/scripts/move_demo_to_region.py --dry-run   # what would go
    docker compose exec backend python /app/scripts/move_demo_to_region.py             # do it
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1] / "backend"
sys.path.insert(0, str(BACKEND if BACKEND.exists() else Path.cwd()))

from atlas.service import move_demo_atlas  # noqa: E402
from graph.store import get_store  # noqa: E402

ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
ap.add_argument("--dry-run", action="store_true", help="only count what would be removed")
ap.add_argument("--seed", type=int, default=1795, help="seed of the demo region")
args = ap.parse_args()
store = get_store()
if store.db != "living_world" and not store.all_documents("main", doc_type="Character"):
    sys.exit(f"The active campaign is {store.db}, not the Greywater demo: switch to it first")
print(json.dumps(move_demo_atlas(store, seed=args.seed, dry_run=args.dry_run), indent=1, default=str))

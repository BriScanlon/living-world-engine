"""Populate `main` with the Greywater demo world.

Usage (from repo root):
    python scripts/seed_demo_world.py            # upsert demo docs into main
    python scripts/seed_demo_world.py --reset    # drop the database first (deletes all branches!)

Inside Docker:
    docker compose exec backend python /app/scripts/seed_demo_world.py --reset
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from graph.seed import seed  # noqa: E402
from graph.store import GraphStore  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--reset", action="store_true", help="delete and recreate the database first")
    args = ap.parse_args()
    store = GraphStore()
    result = seed(store, reset=args.reset)
    print(f"Seeded {result['documents']} documents into {store.db}@main "
          f"(canon current through in-world day {result['current_day']}).")
    for b in store.list_branches():
        print(f"  branch {b['name']:<40} head={b['head']}")


if __name__ == "__main__":
    main()

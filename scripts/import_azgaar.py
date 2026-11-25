"""Import an Azgaar Fantasy Map Generator world into the Living World Engine.

In Azgaar (https://azgaar.github.io/Fantasy-Map-Generator/): Menu > Save > Export > JSON > **Full**.

Usage (from repo root):
    python scripts/import_azgaar.py MyWorld_Full.json                   # into main
    python scripts/import_azgaar.py MyWorld_Full.json --branch import-1 # review on a branch first
    python scripts/import_azgaar.py MyWorld_Full.json --place-greywater # put the demo town on a port
    python scripts/import_azgaar.py --sample                            # (re)generate the sample planet

Inside Docker (copy the file into the container or a mounted folder first):
    docker compose exec backend python /app/scripts/import_azgaar.py /app/data/MyWorld_Full.json
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from atlas.sample_world import write_sample  # noqa: E402
from atlas.service import atlas_root, import_world  # noqa: E402
from graph.store import GraphStore  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("file", nargs="?", help="Azgaar Full JSON export")
    ap.add_argument("--branch", default="main")
    ap.add_argument("--slug", help="atlas id (defaults to the map name)")
    ap.add_argument("--place-greywater", action="store_true", help="place the Greywater demo on a port town")
    ap.add_argument("--sample", action="store_true", help="generate and import the built-in sample planet")
    args = ap.parse_args()
    store = GraphStore()
    store.ensure_database()
    if args.sample:
        path = write_sample(atlas_root() / "sample" / "sample-world.full.json")
        slug = args.slug or "thessaly-reach"
    elif args.file:
        path, slug = Path(args.file), args.slug
    else:
        ap.error("give an Azgaar Full JSON file or --sample")
    if args.branch != "main" and not store.branch_exists(args.branch):
        store.create_branch(args.branch, "main")
    res = import_world(store, path, branch=args.branch, slug=slug,
                       place_greywater=args.place_greywater or args.sample)
    print(f"Imported atlas '{res['name']}' ({res['slug']}) into {args.branch}: {res['documents']} documents, "
          f"{'PMTiles' if res['tiles'] else 'GeoJSON layers (install tippecanoe for PMTiles)'}.")
    if res.get("greywater"):
        g = res["greywater"]
        print(f"Greywater placed at {g['host_burg']} in {g['province']}, vassal of {g['realm_name']}.")


if __name__ == "__main__":
    main()

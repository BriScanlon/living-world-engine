#!/usr/bin/env python3
"""Seed the open-source starter asset pack (10 maps + 10 tokens) through the local AI pipeline.

Uses the same pipeline code as the server, so it honours .env:
  MOCK_AI_PIPELINE=true   -> procedural 4K maps/tokens in seconds (no GPU)
  MOCK_AI_PIPELINE=false  -> LLM_API_URL + COMFY_IMAGE_URL (+ COMFY_VIDEO_URL for animated loops)

Output goes to DATA_DIR/starter_pack/{maps,tokens}/ and is indexed in the VTT's SQLite DB,
plus a portable DATA_DIR/starter_pack/index.json. Re-running skips assets that already exist.

Examples
  python scripts/seed_starter_assets.py                    # everything, per .env
  python scripts/seed_starter_assets.py --mock             # force mock mode
  python scripts/seed_starter_assets.py --only maps --keys tavern-common-room,docks-at-night
  python scripts/seed_starter_assets.py --force --no-animate
  docker compose exec vtt python /app/scripts/seed_starter_assets.py
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--manifest", type=Path, default=ROOT / "scripts" / "starter_manifest.json")
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--mock", action="store_true", help="force MOCK_AI_PIPELINE=true")
    mode.add_argument("--real", action="store_true", help="force MOCK_AI_PIPELINE=false (needs GPU services)")
    p.add_argument("--only", choices=("maps", "tokens"), help="seed just one kind")
    p.add_argument("--keys", help="comma-separated manifest keys to (re)generate")
    p.add_argument("--force", action="store_true", help="regenerate assets that already exist")
    p.add_argument("--no-animate", action="store_true", help="skip the Wan 2.1 loop step for maps")
    p.add_argument("--dry-run", action="store_true", help="list what would be generated and exit")
    p.add_argument(
        "--server",
        default=os.environ.get("VTT_SERVER", "http://localhost:8000"),
        help="running VTT to notify so open /dm panels refresh (best effort)",
    )
    return p.parse_args()


class Printer:
    def __init__(self, label: str):
        self.label = label
        self.start = time.monotonic()
        self.tty = sys.stdout.isatty()

    async def __call__(self, stage: str, progress: float, message: str) -> None:
        line = f"  [{progress * 100:5.1f}%] {stage:<10} {message}"[:110]
        if self.tty:
            sys.stdout.write("\r" + line.ljust(110))
            sys.stdout.flush()

    def done(self, note: str) -> None:
        if self.tty:
            sys.stdout.write("\r" + " " * 110 + "\r")
        print(f"  ✓ {self.label} ({time.monotonic() - self.start:.1f}s){' - ' + note if note else ''}")


def remove_asset(db, settings, asset: dict) -> None:
    db.delete_asset(asset["id"])
    for key in ("media_path", "still_path", "thumb_path"):
        rel = asset.get(key)
        if rel:
            (settings.data_dir / rel).unlink(missing_ok=True)


async def main() -> int:
    args = parse_args()
    try:  # Windows consoles default to cp1252; keep the ✓/✗ output from crashing
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    except (AttributeError, ValueError):
        pass
    if args.mock:
        os.environ["MOCK_AI_PIPELINE"] = "true"
    if args.real:
        os.environ["MOCK_AI_PIPELINE"] = "false"

    # Import after env tweaks so config picks them up.
    from app.config import settings
    from app.db import Database
    from app.pipeline import flows

    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    pack = manifest.get("name", "starter")
    style = manifest.get("style", "")
    keys = {k.strip() for k in args.keys.split(",")} if args.keys else None
    maps = [] if args.only == "tokens" else manifest.get("maps", [])
    tokens = [] if args.only == "maps" else manifest.get("tokens", [])
    if keys:
        maps = [m for m in maps if m["key"] in keys]
        tokens = [t for t in tokens if t["key"] in keys]

    print(f"Starter pack '{pack}': {len(maps)} maps, {len(tokens)} tokens")
    print(f"  pipeline : {'MOCK (procedural, no GPU)' if settings.mock else 'LOCAL GPU'}")
    if not settings.mock:
        print(f"  llm      : {settings.llm_api_url} ({settings.llm_model})")
        print(f"  flux     : {settings.comfy_image_url}")
        print(f"  wan 2.1  : {settings.comfy_video_url or '(disabled - static maps)'}")
    print(f"  output   : {settings.starter_dir}")
    if args.dry_run:
        for m in maps:
            print(f"  map   {m['key']:<26} {m['title']}")
        for t in tokens:
            print(f"  token {t['key']:<26} {t['title']}")
        return 0

    settings.ensure_dirs()
    db = Database(settings.db_path)
    created, skipped, failed = 0, 0, 0

    async def run(kind: str, item: dict, coro_factory) -> None:
        nonlocal created, skipped, failed
        existing = db.find_asset_by_pack_key(pack, item["title"], kind)
        if existing and not args.force:
            print(f"  · {item['title']} (exists, skipping)")
            skipped += 1
            return
        if existing:
            remove_asset(db, settings, existing)
        printer = Printer(item["title"])
        try:
            asset = await coro_factory(printer)
            notes = asset.pop("_notes", []) if isinstance(asset, dict) else []
            printer.done("; ".join(notes))
            created += 1
        except Exception as exc:  # noqa: BLE001 - keep going with the rest of the pack
            print(f"\n  ✗ {item['title']}: {exc}")
            failed += 1

    if maps:
        print("\nMaps")
    for m in maps:
        await run("map", m, lambda rep, m=m: flows.generate_map(
            db, description=m["description"], style=m.get("style", style), report=rep, source="starter",
            pack=pack, out_dir=settings.starter_dir / "maps", title=m["title"], seed=m.get("seed"),
            animate=not args.no_animate, tags=m.get("tags"),
        ))

    if tokens:
        print("\nTokens")
    for t in tokens:
        await run("token", t, lambda rep, t=t: flows.generate_token(
            db, name=t["title"], description=t["description"], report=rep, pack=pack,
            out_dir=settings.starter_dir / "tokens", seed=t.get("seed"), tags=t.get("tags"),
        ))

    # Portable index of the whole pack (paths relative to DATA_DIR).
    index = {
        "name": pack,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "mock": settings.mock,
        "maps": [a for a in db.list_assets("map") if a.get("pack") == pack],
        "tokens": [a for a in db.list_assets("token") if a.get("pack") == pack],
    }
    (settings.starter_dir / "index.json").write_text(json.dumps(index, indent=2), encoding="utf-8")
    db.close()

    print(f"\nDone: {created} created, {skipped} skipped, {failed} failed  ->  {settings.starter_dir / 'index.json'}")
    try:
        req = urllib.request.Request(f"{args.server.rstrip('/')}/api/library/changed", method="POST")
        urllib.request.urlopen(req, timeout=3)
        print(f"Notified VTT at {args.server} - open /dm panels will refresh.")
    except Exception:  # noqa: BLE001
        print(f"(VTT server not reachable at {args.server}; refresh /dm manually.)")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

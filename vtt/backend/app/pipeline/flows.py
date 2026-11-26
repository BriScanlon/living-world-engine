"""The two automated flows (plus token generation), shared by the server and the seeder CLI.

1. Text -> animated map:  LLM expand -> ComfyUI Flux still -> ComfyUI Wan 2.1 FLF2V loop
2. Image -> animated map: (inbox / upload) vision LLM motion prompt -> Wan 2.1 FLF2V loop

Every flow reports ``(stage, progress 0..1, message)`` through an async callback and
returns the registered asset row. With MOCK_AI_PIPELINE=true the same stages run with
simulated timing and procedural Pillow output, so the whole UI works without GPUs.
"""

from __future__ import annotations

import asyncio
import io
import logging
import random
import re
import shutil
import time
import uuid
from pathlib import Path
from typing import Any, Awaitable, Callable

from PIL import Image

from ..config import settings
from ..db import Database
from .. import media
from . import llm
from .comfy import IMAGE_EXTS, VIDEO_EXTS, ComfyClient, ComfyError, apply_params, load_workflow, pick_output

log = logging.getLogger("vtt.pipeline")

Report = Callable[[str, float, str], Awaitable[None]]


async def _noop_report(stage: str, progress: float, message: str) -> None:  # pragma: no cover
    return None


def new_id() -> str:
    return uuid.uuid4().hex[:12]


def rel(path: Path) -> str:
    return path.resolve().relative_to(settings.data_dir).as_posix()


def nice_title(stem: str) -> str:
    words = re.sub(r"[_\-]+", " ", stem).strip()
    words = re.sub(r"\s+", " ", words)
    return words.title()[:60] or "Imported Map"


async def _ticks(report: Report, stage: str, lo: float, hi: float, seconds: float, message: str, steps: int = 6) -> None:
    for i in range(steps):
        await report(stage, lo + (hi - lo) * (i + 1) / steps, message)
        await asyncio.sleep(seconds / steps)


def _video_dims(src: Path, longest: int = 1280) -> tuple[int, int]:
    w, h = media.image_size(src)
    scale = longest / max(w, h)
    return max(16, round(w * scale / 16) * 16), max(16, round(h * scale / 16) * 16)


def _resized_png(src: Path, size: tuple[int, int]) -> bytes:
    with Image.open(src) as im:
        im = im.convert("RGB").resize(size, Image.LANCZOS)
        buf = io.BytesIO()
        im.save(buf, "PNG")
        return buf.getvalue()


def _vision_jpeg(src: Path, longest: int = 1344) -> bytes:
    with Image.open(src) as im:
        im = im.convert("RGB")
        im.thumbnail((longest, longest), Image.LANCZOS)
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=88)
        return buf.getvalue()


# ---------------------------------------------------------------------------
# ComfyUI stages (real mode)
# ---------------------------------------------------------------------------
async def comfy_still(
    prompt: str, seed: int, out_path: Path, report: Report, lo: float, hi: float,
    latent: tuple[int, int] = (1920, 1088), target: tuple[int, int] = (3840, 2160), stage: str = "base_map",
) -> Path:
    graph, params = load_workflow(settings.workflows_dir / settings.comfy_image_workflow)
    graph = apply_params(graph, params, {
        "prompt": prompt,
        "seed": seed,
        "width": latent[0],
        "height": latent[1],
        "target_width": target[0],
        "target_height": target[1],
        "filename_prefix": f"vtt/{out_path.stem}",
    })
    client = ComfyClient(settings.comfy_image_url, settings.comfy_timeout_s)

    async def prog(p: float, msg: str) -> None:
        await report(stage, lo + (hi - lo) * p, f"Flux: {msg}")

    files = await client.run(graph, prog, expected_s=60)
    picked = pick_output(files, IMAGE_EXTS)
    if not picked:
        raise ComfyError("Flux workflow produced no image")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(io.BytesIO(picked[1])) as im:
        im.convert("RGBA" if im.mode in ("RGBA", "LA") else "RGB").save(out_path, "PNG")
    return out_path


async def comfy_loop(still: Path, motion_prompt: str, seed: int, out_stem: Path, report: Report, lo: float, hi: float) -> Path | None:
    """Animate ``still`` (used as BOTH first and last frame) into a seamless loop."""
    if not settings.comfy_video_url:
        return None
    graph, params = load_workflow(settings.workflows_dir / settings.comfy_video_workflow)
    dims = _video_dims(still)
    client = ComfyClient(settings.comfy_video_url, settings.comfy_timeout_s)
    uploaded = await client.upload_image(_resized_png(still, dims), f"vtt_{out_stem.name}.png")
    graph = apply_params(graph, params, {
        "first_frame": uploaded,
        "last_frame": uploaded,
        "motion_prompt": motion_prompt,
        "seed": seed,
        "width": dims[0],
        "height": dims[1],
        "filename_prefix": f"vtt/{out_stem.name}_loop",
    })

    async def prog(p: float, msg: str) -> None:
        await report("animating", lo + (hi - lo) * p, f"Wan 2.1 FLF2V: {msg}")

    files = await client.run(graph, prog, expected_s=420)
    picked = pick_output(files, VIDEO_EXTS) or pick_output(files, (".webp", ".gif"))
    if not picked:
        raise ComfyError("Wan workflow produced no video")
    ext = Path(picked[0]).suffix.lower()
    out = out_stem.with_name(f"{out_stem.name}_loop{ext}")
    out.write_bytes(picked[1])
    return out


# ---------------------------------------------------------------------------
# Asset registration
# ---------------------------------------------------------------------------
def register_asset(
    db: Database, *, asset_id: str, still: Path | None, media_file: Path, title: str, source: str,
    asset_type: str = "map", description: str = "", style: str = "", image_prompt: str = "",
    motion_prompt: str = "", pack: str | None = None, tags: list[str] | None = None, mock: bool = False,
) -> dict[str, Any]:
    is_video = media_file.suffix.lower() in VIDEO_EXTS
    thumb_src = still or (None if is_video else media_file)
    thumb_rel = None
    width = height = None
    if thumb_src:
        thumb_size = (256, 256) if asset_type == "token" else media.THUMB_SIZE
        thumb = media.make_thumbnail(thumb_src, settings.thumbs_dir / f"{asset_id}.webp", thumb_size)
        thumb_rel = rel(thumb)
        width, height = media.image_size(thumb_src)
    return db.insert_asset({
        "id": asset_id,
        "asset_type": asset_type,
        "title": title,
        "source": source,
        "media_type": "video" if is_video else "image",
        "media_path": rel(media_file),
        "still_path": rel(still) if still else None,
        "thumb_path": thumb_rel,
        "width": width,
        "height": height,
        "description": description,
        "style": style,
        "image_prompt": image_prompt,
        "motion_prompt": motion_prompt,
        "pack": pack,
        "tags": tags or [],
        "mock": mock,
        "created_at": time.time(),
    })


# ---------------------------------------------------------------------------
# Flow 1: text -> animated map
# ---------------------------------------------------------------------------
async def generate_map(
    db: Database, *, description: str, style: str = "", report: Report = _noop_report,
    source: str = "generated", pack: str | None = None, out_dir: Path | None = None,
    title: str | None = None, seed: int | None = None, animate: bool = True,
    tags: list[str] | None = None,
) -> dict[str, Any]:
    out_dir = out_dir or settings.maps_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    asset_id = new_id()
    seed = seed if seed is not None else random.randint(1, 2**31 - 1)
    still = out_dir / f"{asset_id}.png"
    notes: list[str] = []

    # -- Stage 1: prompting ------------------------------------------------
    await report("prompting", 0.02, "Expanding description into image + motion prompts")
    if settings.mock:
        await _ticks(report, "prompting", 0.02, 0.15, 1.0, "Mock LLM: writing prompts")
        prompts = llm.template_prompts(description, style)
    else:
        try:
            prompts = await llm.expand_description(description, style)
        except Exception as exc:  # noqa: BLE001 - LLM optional; fall back to template
            log.warning("LLM prompt expansion failed, using template: %s", exc)
            notes.append(f"LLM unavailable ({exc.__class__.__name__}), used template prompt")
            prompts = llm.template_prompts(description, style)
    final_title = title or prompts.get("title") or description.title()[:60]

    # -- Stage 2: base map ---------------------------------------------------
    await report("base_map", 0.16, "Generating 4K base map")
    if settings.mock:
        gen = asyncio.create_task(asyncio.to_thread(media.generate_mock_map, description, style, seed, still))
        await _ticks(report, "base_map", 0.16, 0.5, 1.4, "Mock Flux: painting procedural 4K map")
        await gen
    else:
        await comfy_still(prompts["image_prompt"], seed, still, report, 0.16, 0.55)

    # -- Stage 3: animate loop ---------------------------------------------
    media_file = still
    if animate:
        await report("animating", 0.56, "Animating seamless loop")
        if settings.mock:
            await _ticks(report, "animating", 0.56, 0.97, 1.2, "Mock Wan 2.1: (static map + ambient FX in mock mode)")
        else:
            try:
                video = await comfy_loop(still, prompts["motion_prompt"], seed, out_dir / asset_id, report, 0.56, 0.97)
                if video:
                    media_file = video
                else:
                    notes.append("COMFY_VIDEO_URL not set: saved as static map")
            except Exception as exc:  # noqa: BLE001 - keep the still if animation fails
                log.exception("Animation failed")
                notes.append(f"Animation failed, kept static map: {exc}")

    await report("saving", 0.98, "Registering in library")
    asset = register_asset(
        db, asset_id=asset_id, still=still, media_file=media_file, title=final_title, source=source,
        description=description, style=style, image_prompt=prompts["image_prompt"],
        motion_prompt=prompts["motion_prompt"], pack=pack, tags=tags, mock=settings.mock,
    )
    asset["_notes"] = notes
    return asset


# ---------------------------------------------------------------------------
# Flow 2: image (Inkarnate export / upload / inbox) -> animated map
# ---------------------------------------------------------------------------
async def animate_image(
    db: Database, *, image_path: Path, report: Report = _noop_report, source: str = "inbox",
    title: str | None = None, pack: str | None = None, out_dir: Path | None = None,
    move: bool = True,
) -> dict[str, Any]:
    out_dir = out_dir or settings.maps_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    asset_id = new_id()
    ext = image_path.suffix.lower() if image_path.suffix.lower() in IMAGE_EXTS else ".png"
    still = out_dir / f"{asset_id}{ext}"
    (shutil.move if move else shutil.copy2)(str(image_path), str(still))
    final_title = title or nice_title(image_path.stem)
    notes: list[str] = []
    seed = random.randint(1, 2**31 - 1)

    await report("prompting", 0.03, "Vision model: describing natural motion")
    if settings.mock:
        await _ticks(report, "prompting", 0.03, 0.3, 1.2, "Mock vision LLM: writing motion prompt")
        motion = llm.template_prompts(final_title, "")["motion_prompt"]
    else:
        try:
            mime = "image/jpeg"
            res = await llm.describe_motion(await asyncio.to_thread(_vision_jpeg, still), mime)
            motion = res["motion_prompt"]
            if res.get("title") and not title:
                final_title = res["title"]
        except Exception as exc:  # noqa: BLE001
            log.warning("Vision LLM failed, using generic motion prompt: %s", exc)
            notes.append(f"Vision LLM unavailable ({exc.__class__.__name__}), used generic motion prompt")
            motion = llm.template_prompts(final_title, "")["motion_prompt"]

    media_file = still
    await report("animating", 0.32, "Animating seamless loop")
    if settings.mock:
        await _ticks(report, "animating", 0.32, 0.97, 2.0, "Mock Wan 2.1: (static map + ambient FX in mock mode)")
    else:
        try:
            video = await comfy_loop(still, motion, seed, out_dir / asset_id, report, 0.32, 0.97)
            if video:
                media_file = video
            else:
                notes.append("COMFY_VIDEO_URL not set: imported as static map")
        except Exception as exc:  # noqa: BLE001
            log.exception("Animation failed")
            notes.append(f"Animation failed, kept static map: {exc}")

    await report("saving", 0.98, "Registering in library")
    asset = register_asset(
        db, asset_id=asset_id, still=still, media_file=media_file, title=final_title,
        source=source, motion_prompt=motion, pack=pack, mock=settings.mock,
    )
    asset["_notes"] = notes
    return asset


def import_static(db: Database, *, file_path: Path, title: str | None = None, source: str = "imported") -> dict[str, Any]:
    """Register an uploaded image or video as-is (no AI)."""
    asset_id = new_id()
    dest = settings.maps_dir / f"{asset_id}{file_path.suffix.lower()}"
    shutil.move(str(file_path), str(dest))
    is_video = dest.suffix.lower() in VIDEO_EXTS
    return register_asset(
        db, asset_id=asset_id, still=None if is_video else dest, media_file=dest,
        title=title or nice_title(file_path.stem), source=source,
    )


# ---------------------------------------------------------------------------
# Tokens (used by the starter pack seeder)
# ---------------------------------------------------------------------------
async def generate_token(
    db: Database, *, name: str, description: str, report: Report = _noop_report,
    pack: str | None = "starter", out_dir: Path | None = None, seed: int | None = None,
    tags: list[str] | None = None,
) -> dict[str, Any]:
    out_dir = out_dir or (settings.starter_dir / "tokens")
    asset_id = new_id()
    seed = seed if seed is not None else random.randint(1, 2**31 - 1)
    out = out_dir / f"{asset_id}.png"
    prompt = (
        f"Top-down tabletop RPG token of {description}, seen from directly above, centered in a round "
        "token frame with a thin metallic rim, plain flat dark background outside the circle, "
        "crisp painterly detail, no text"
    )
    await report("base_map", 0.1, f"Generating token: {name}")
    if settings.mock:
        await asyncio.to_thread(media.generate_mock_token, name, description, seed, out)
        await _ticks(report, "base_map", 0.1, 0.95, 0.3, "Mock Flux: token", steps=3)
    else:
        await comfy_still(prompt, seed, out, report, 0.1, 0.95, latent=(1024, 1024), target=(512, 512))
    return register_asset(
        db, asset_id=asset_id, still=out, media_file=out, title=name, source="starter" if pack else "generated",
        asset_type="token", description=description, image_prompt=prompt, pack=pack, tags=tags,
        mock=settings.mock,
    )

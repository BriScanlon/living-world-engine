"""OpenAI-compatible chat client (Ollama / vLLM / LM Studio / llama.cpp server)."""

from __future__ import annotations

import base64
import json
import re

import httpx

from ..config import settings

# Scale/camera rules are enforced twice: in the system prompt, and appended in code
# so a chatty model can never drop them.
SCALE_RULES = (
    "strictly 90-degree top-down orthographic battle map, zero perspective distortion, "
    "no grid, covering a 140ft by 80ft area (doors 5ft wide, corridors 10ft wide)"
)
CAMERA_LOCK = (
    "Completely locked static camera: no pan, no zoom, no tilt, no rotation, no camera shake. "
    "Top-down view stays fixed; only ambient elements move, in a seamless cyclic loop."
)

MAP_SYSTEM_PROMPT = f"""You write prompts for an AI that paints tabletop RPG battle maps and then animates them.
Given a short area description from a Dungeon Master, reply with ONLY a JSON object:
{{"title": "...", "image_prompt": "...", "motion_prompt": "..."}}

title: 2-5 word name for the map.

image_prompt (for a Flux text-to-image model, 60-120 words):
- It MUST be a {SCALE_RULES}.
- Describe floor materials, terrain, props, lighting and colour palette as seen from directly above.
- Fill the whole frame edge to edge with playable space; no borders, no text, no labels, no characters, no UI.
- Keep a consistent scale: a 5ft square is 1/28 of the image width.

motion_prompt (for a Wan 2.1 first-last-frame video model, 30-60 words):
- {CAMERA_LOCK}
- Describe only subtle natural ambient motion that suits the scene: water ripples, flowing lava, flickering
  torch and candle flames, drifting smoke or mist, swaying grass and leaves, falling rain or snow.
- No people, creatures or objects appear, move or change. Nothing is added or removed.
"""

VISION_SYSTEM_PROMPT = f"""You are given a top-down tabletop battle map image.
Write a motion prompt (30-60 words) for a video model that will gently animate it as a seamless loop.
- {CAMERA_LOCK}
- Mention ONLY natural ambient motion that clearly fits elements visible in the image (water ripples,
  flickering fire, drifting fog, swaying trees and grass, flowing lava, falling rain/snow).
- Never add new objects, people or creatures. Never change the layout.
Reply with ONLY a JSON object: {{"title": "2-5 word map name", "motion_prompt": "..."}}"""


class LLMError(RuntimeError):
    pass


def extract_json(text: str) -> dict:
    """Pull the first JSON object out of a model reply (handles ```json fences and chatter)."""
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    if fence:
        text = fence.group(1)
    start = text.find("{")
    if start < 0:
        raise LLMError(f"No JSON object in LLM reply: {text[:200]!r}")
    depth = 0
    in_str = False
    esc = False
    for i, ch in enumerate(text[start:], start):
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return json.loads(text[start : i + 1])
    raise LLMError(f"Unterminated JSON in LLM reply: {text[:200]!r}")


async def _chat(model: str, messages: list[dict], timeout: float = 180.0) -> str:
    url = f"{settings.llm_api_url}/chat/completions"
    payload = {"model": model, "messages": messages, "temperature": 0.7, "stream": False}
    headers = {"Authorization": f"Bearer {settings.llm_api_key}"} if settings.llm_api_key else {}
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(url, json=payload, headers=headers)
        if resp.status_code >= 400:
            raise LLMError(f"LLM HTTP {resp.status_code}: {resp.text[:300]}")
        data = resp.json()
    try:
        return data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise LLMError(f"Unexpected LLM response: {str(data)[:300]}") from exc


def enforce_image_prompt(prompt: str) -> str:
    prompt = prompt.strip().rstrip(".")
    return f"{prompt}. {SCALE_RULES}, highly detailed, crisp textures, even lighting."


def enforce_motion_prompt(prompt: str) -> str:
    prompt = prompt.strip()
    return f"{CAMERA_LOCK} {prompt}"


def template_prompts(description: str, style: str) -> dict:
    """Fallback used when the LLM is unreachable, and the basis of mock prompts."""
    style_txt = f"{style} style, " if style else ""
    return {
        "title": description.strip().title()[:60] or "Untitled Map",
        "image_prompt": enforce_image_prompt(
            f"{style_txt}fantasy tabletop battle map of {description.strip()}, rich materials, "
            "atmospheric lighting, painterly detail"
        ),
        "motion_prompt": enforce_motion_prompt(
            "Subtle ambient motion only: gentle flicker of any flames, soft ripples on any water, "
            "faint drifting mist."
        ),
    }


async def expand_description(description: str, style: str) -> dict:
    user = f"Area description: {description.strip()}\nArt style: {style or 'painterly fantasy'}"
    reply = await _chat(
        settings.llm_model,
        [{"role": "system", "content": MAP_SYSTEM_PROMPT}, {"role": "user", "content": user}],
    )
    data = extract_json(reply)
    if not data.get("image_prompt"):
        raise LLMError("LLM reply missing image_prompt")
    return {
        "title": str(data.get("title") or description.title())[:60],
        "image_prompt": enforce_image_prompt(str(data["image_prompt"])),
        "motion_prompt": enforce_motion_prompt(str(data.get("motion_prompt") or "")),
    }


async def describe_motion(image_bytes: bytes, mime: str = "image/png") -> dict:
    b64 = base64.b64encode(image_bytes).decode("ascii")
    reply = await _chat(
        settings.llm_vision_model,
        [
            {"role": "system", "content": VISION_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Write the motion prompt for this battle map."},
                    {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}},
                ],
            },
        ],
    )
    data = extract_json(reply)
    return {
        "title": str(data.get("title") or "")[:60],
        "motion_prompt": enforce_motion_prompt(str(data.get("motion_prompt") or "")),
    }

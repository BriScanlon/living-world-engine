"""Exercise the non-mock pipeline against fake OpenAI-compatible + ComfyUI servers.

Validates the HTTP contract (chat/completions, /upload/image, /prompt, /history, /view),
that the workflow templates are well-formed (every link points at an existing node), and
that the VTT injects prompts/seeds/frames into the documented node IDs.
"""

from __future__ import annotations

import asyncio
import dataclasses
import io
import json
import socket
import threading
import time
import uuid
from pathlib import Path

import pytest
import uvicorn
from fastapi import FastAPI, File, Request, UploadFile
from fastapi.responses import Response
from PIL import Image

from app import config as config_mod
from app.db import Database
from app.pipeline import flows, llm
from app.pipeline.comfy import apply_params, load_workflow

WORKFLOWS = Path(__file__).resolve().parents[2] / "workflows"


def _png(size=(64, 36)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, (90, 60, 30)).save(buf, "PNG")
    return buf.getvalue()


def make_fake() -> tuple[FastAPI, dict]:
    app = FastAPI()
    seen: dict = {"prompts": {}, "uploads": [], "chats": []}

    @app.post("/v1/chat/completions")
    async def chat(req: Request):
        body = await req.json()
        seen["chats"].append(body)
        user = body["messages"][-1]["content"]
        if isinstance(user, list):  # vision request
            content = '```json\n{"title": "Misty Lake", "motion_prompt": "gentle ripples on the lake"}\n```'
        else:
            content = 'Here you go: {"title": "Goblin Cavern", "image_prompt": "a flooded cavern", "motion_prompt": "water ripples"}'
        return {"choices": [{"message": {"role": "assistant", "content": content}}]}

    @app.post("/upload/image")
    async def upload(image: UploadFile = File(...)):
        seen["uploads"].append(image.filename)
        return {"name": image.filename, "subfolder": "", "type": "input"}

    @app.post("/prompt")
    async def prompt(req: Request):
        graph = (await req.json())["prompt"]
        for nid, node in graph.items():
            assert not nid.startswith("_"), "doc keys must be stripped"
            assert "class_type" in node, nid
            for v in node["inputs"].values():
                if isinstance(v, list) and len(v) == 2 and isinstance(v[0], str):
                    assert v[0] in graph, f"node {nid} links to missing node {v[0]}"
        pid = uuid.uuid4().hex
        seen["prompts"][pid] = graph
        return {"prompt_id": pid, "number": 1}

    @app.get("/history/{pid}")
    async def history(pid: str):
        graph = seen["prompts"][pid]
        outputs = {}
        for nid, node in graph.items():
            if node["class_type"] == "SaveImage":
                outputs[nid] = {"images": [{"filename": "out.png", "subfolder": "vtt", "type": "output"}]}
            if node["class_type"] in ("SaveWEBM", "SaveVideo"):
                outputs[nid] = {"images": [{"filename": "loop.webm", "subfolder": "vtt", "type": "output"}], "animated": [True]}
        return {pid: {"status": {"status_str": "success", "completed": True}, "outputs": outputs}}

    @app.get("/view")
    async def view(filename: str):
        if filename.endswith(".webm"):
            return Response(b"\x1a\x45\xdf\xa3fake-webm", media_type="video/webm")
        return Response(_png((192, 108)), media_type="image/png")

    return app, seen


@pytest.fixture(scope="module")
def fake_server():
    app, seen = make_fake()
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    t = threading.Thread(target=server.run, daemon=True)
    t.start()
    deadline = time.time() + 10
    while not server.started and time.time() < deadline:
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}", seen
    server.should_exit = True
    t.join(timeout=5)


@pytest.fixture()
def real_settings(fake_server, tmp_path, monkeypatch):
    base, seen = fake_server
    s = dataclasses.replace(
        config_mod.settings,
        data_dir=tmp_path,
        mock=False,
        llm_api_url=f"{base}/v1",
        comfy_image_url=base,
        comfy_video_url=base,
        workflows_dir=WORKFLOWS,
    )
    s.ensure_dirs()
    for mod in (config_mod, flows, llm):
        monkeypatch.setattr(mod, "settings", s)
    return s, seen


def test_templates_are_well_formed():
    for name in ("flux_battlemap.json", "wan21_flf2v_loop.json"):
        graph, params = load_workflow(WORKFLOWS / name)
        for pname, spec in params.items():
            assert spec["node"] in graph, f"{name}: param {pname} -> missing node {spec['node']}"
            assert spec["input"] in graph[spec["node"]]["inputs"], f"{name}: {pname} input missing"
        g = apply_params(graph, params, {"seed": 7})
        assert json.dumps(g)  # serialisable


def test_text_to_animated_map_real_mode(real_settings):
    s, seen = real_settings
    db = Database(s.db_path)
    stages = []

    async def report(stage, progress, message):
        stages.append(stage)

    asset = asyncio.run(flows.generate_map(db, description="flooded goblin cavern", style="Painterly", report=report, seed=123))
    assert asset["media_type"] == "video" and asset["media_path"].endswith("_loop.webm")
    assert asset["title"] == "Goblin Cavern"
    assert "140ft by 80ft" in asset["image_prompt"]
    assert asset["motion_prompt"].startswith("Completely locked static camera")
    assert {"prompting", "base_map", "animating", "saving"} <= set(stages)

    flux, wan = list(seen["prompts"].values())[-2:]
    assert flux["6"]["inputs"]["text"] == asset["image_prompt"]
    assert flux["9"]["inputs"]["noise_seed"] == 123
    assert (flux["17"]["inputs"]["width"], flux["17"]["inputs"]["height"]) == (3840, 2160)
    assert wan["5"]["inputs"]["image"] == wan["6"]["inputs"]["image"] == seen["uploads"][-1]
    assert wan["10"]["inputs"]["text"] == asset["motion_prompt"]
    assert wan["12"]["inputs"]["width"] % 16 == 0 and wan["12"]["inputs"]["height"] % 16 == 0
    db.close()


def test_inbox_image_to_loop_real_mode(real_settings):
    s, seen = real_settings
    db = Database(s.db_path)
    src = s.uploads_dir / "misty_lake_export.png"
    src.write_bytes(_png((320, 180)))
    asset = asyncio.run(flows.animate_image(db, image_path=src, source="inbox"))
    assert asset["media_type"] == "video"
    assert asset["title"] == "Misty Lake"  # from the vision model
    assert "ripples" in asset["motion_prompt"]
    vision_req = seen["chats"][-1]
    assert vision_req["messages"][-1]["content"][1]["image_url"]["url"].startswith("data:image/jpeg;base64,")
    db.close()

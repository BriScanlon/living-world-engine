"""Smoke tests for the backend in mock mode.  Run:  cd backend && python -m pytest -q"""

from __future__ import annotations

import io
import os
import sys
import tempfile
import time
from pathlib import Path

TMP = tempfile.mkdtemp(prefix="vtt-test-")
os.environ.update(DATA_DIR=TMP, MOCK_AI_PIPELINE="true", WATCH_INBOX="true", INBOX_POLLING="true")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402
from PIL import Image  # noqa: E402

from app.main import app  # noqa: E402
from app.pipeline.llm import extract_json  # noqa: E402
from app.state import sanitize_fog_op  # noqa: E402


def _png_bytes(size=(320, 180)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, (40, 90, 140)).save(buf, "PNG")
    return buf.getvalue()


def _wait_job(client: TestClient, job_id: str, timeout: float = 30) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = next(j for j in client.get("/api/jobs").json() if j["id"] == job_id)
        if job["status"] in ("done", "error"):
            return job
        time.sleep(0.3)
    raise AssertionError("job did not finish")


def test_extract_json_handles_fences_and_chatter():
    assert extract_json('Sure!\n```json\n{"a": "x}"}\n```') == {"a": "x}"}
    assert extract_json('blah {"image_prompt": "p", "n": {"k": 1}} trailing') == {"image_prompt": "p", "n": {"k": 1}}


def test_fog_op_sanitising():
    assert sanitize_fog_op({"mode": "reveal", "shape": "circle", "x": 2, "y": 0.5, "r": 0.1})["x"] == 1.5
    assert sanitize_fog_op({"mode": "nuke", "shape": "circle"}) is None
    assert sanitize_fog_op({"mode": "hide", "shape": "poly", "pts": [[0, 0], [1, 0]]}) is None


def test_full_mock_flow():
    with TestClient(app) as client:
        assert client.get("/api/health").json()["ok"]
        assert client.get("/api/config").json()["mock"] is True

        # text -> map
        job = client.post("/api/generate", json={"description": "flooded goblin cavern with a rickety wooden bridge"}).json()
        done = _wait_job(client, job["id"])
        assert done["status"] == "done", done
        maps = client.get("/api/maps").json()
        assert len(maps) == 1 and maps[0]["width"] == 3840
        assert client.get(maps[0]["thumb_url"]).status_code == 200

        # websocket sync: DM changes grid + fog, table receives it
        with client.websocket_connect("/ws?role=table") as table, client.websocket_connect("/ws?role=dm") as dm:
            assert table.receive_json()["type"] == "state"
            assert dm.receive_json()["type"] == "state"
            client.post(f"/api/maps/{maps[0]['id']}/push")
            dm.send_json({"type": "grid", "patch": {"type": "hex-flat", "opacity": 5}})
            dm.send_json({"type": "calibration", "nudge": 1.5})
            dm.send_json({"type": "fog_op", "op": {"mode": "reveal", "shape": "circle", "x": 0.5, "y": 0.5, "r": 0.05}})
            seen = {}
            for _ in range(20):
                m = table.receive_json()
                seen.setdefault(m["type"] + ":" + m.get("key", ""), m)
                if "fog_op:" in seen and "state_part:calibration" in seen and "state_part:grid" in seen:
                    break
            assert seen["state_part:grid"]["value"]["type"] == "hex-flat"
            assert seen["state_part:grid"]["value"]["opacity"] == 1.0  # clamped
            assert seen["state_part:calibration"]["value"]["hexFlatToFlatPx"] == 93.3
            assert seen["fog_op:"]["op"]["shape"] == "circle"

        # upload + display
        r = client.post("/api/upload", files={"file": ("my_inkarnate_map.png", _png_bytes(), "image/png")},
                        data={"mode": "display", "push": "true"}).json()
        assert r["asset"]["title"] == "My Inkarnate Map"
        assert client.get("/api/state").json()["map"]["id"] == r["asset"]["id"]

        # inbox watcher -> animate job
        (Path(TMP) / "inbox" / "dropped_map.png").write_bytes(_png_bytes())
        deadline = time.time() + 30
        while time.time() < deadline:
            titles = [m["title"] for m in client.get("/api/maps").json()]
            if "Dropped Map" in titles:
                break
            time.sleep(0.5)
        assert "Dropped Map" in titles

        # delete
        assert client.delete(f"/api/maps/{r['asset']['id']}").json()["ok"]
        assert client.get("/api/state").json()["map"] is None


def test_per_map_view_scale_persists():
    with TestClient(app) as client:
        up = lambda name: client.post(  # noqa: E731
            "/api/upload", files={"file": (name, _png_bytes((1000, 800)), "image/png")}, data={"mode": "display", "push": "true"}
        ).json()["asset"]
        a, b = up("map_a.png"), up("map_b.png")
        state = client.get("/api/state").json()
        assert state["map"]["id"] == b["id"]
        assert state["view"]["mode"] == "cover"  # imported maps start as fill-screen

        with client.websocket_connect("/ws?role=dm") as dm, client.websocket_connect("/ws?role=table") as table:
            dm.receive_json(); table.receive_json()
            dm.send_json({"type": "view", "patch": {"mode": "scale", "pxPerSquare": 50, "cx": 0.25}})
            dm.send_json({"type": "view", "patch": {"mode": "bogus", "pxPerSquare": -3}})  # rejected / clamped
            for _ in range(20):
                m = table.receive_json()
                if m.get("key") == "view" and m["value"]["mode"] == "scale" and m["value"]["pxPerSquare"] == 1:
                    break
            assert m["value"] == {"mode": "scale", "pxPerSquare": 1, "cx": 0.25, "cy": 0.5}
            dm.send_json({"type": "view", "patch": {"pxPerSquare": 50}})
            time.sleep(0.2)

        client.post(f"/api/maps/{a['id']}/push")
        assert client.get("/api/state").json()["view"]["mode"] == "cover"
        client.post(f"/api/maps/{b['id']}/push")
        assert client.get("/api/state").json()["view"] == {"mode": "scale", "pxPerSquare": 50, "cx": 0.25, "cy": 0.5}

        # AI maps (140 ft wide) default to true scale: 3840 px / 28 squares
        job = client.post("/api/generate", json={"description": "tavern common room"}).json()
        gen = client.get(f"/api/maps/{_wait_job(client, job['id'])['result_asset_id']}").json()
        client.post(f"/api/maps/{gen['id']}/push")
        v = client.get("/api/state").json()["view"]
        assert v["mode"] == "scale" and abs(v["pxPerSquare"] - 3840 / 28) < 0.01


def test_world_scene_is_dm_only_and_persists():
    scene = {"source": "living-world-engine", "day": 12, "place": {"name": "The Drowned Lantern", "kind": "tavern"},
             "holder": "Wenna Tull", "people": [{"name": "Wenna Tull", "role": "keeper"}],
             "rumors": [{"dialogue": "\"Mark my words...\"", "accuracy": "Exaggerated"}]}
    with TestClient(app) as client:
        assert client.post("/api/scene", json={"place": {}}).status_code == 400
        with client.websocket_connect("/ws?role=dm") as dm, client.websocket_connect("/ws?role=table") as table:
            first_dm = [dm.receive_json() for _ in range(3)]
            assert any(m["type"] == "scene" for m in first_dm)
            table.receive_json()
            assert client.post("/api/scene", json=scene).json()["ok"]
            for _ in range(10):
                m = dm.receive_json()
                if m["type"] == "scene":
                    break
            assert m["scene"]["place"]["name"] == "The Drowned Lantern"
            # the table snapshot never carries world secrets
            assert "scene" not in client.get("/api/state").json()
        assert client.get("/api/scene").json()["scene"]["holder"] == "Wenna Tull"
    with TestClient(app) as client:  # survives a restart
        assert client.get("/api/scene").json()["scene"]["day"] == 12
        assert client.delete("/api/scene").json()["ok"]
        assert client.get("/api/scene").json()["scene"] is None


def test_map_view_reports_true_scale_for_the_world_engine():
    with TestClient(app) as client:
        job = client.post("/api/generate", json={"description": "smugglers' cove"}).json()
        gen = client.get(f"/api/maps/{_wait_job(client, job['id'])['result_asset_id']}").json()
        v = client.get(f"/api/maps/{gen['id']}/view").json()
        assert v["mode"] == "scale" and v["width"] == 3840
        assert abs(v["metresPerPx"] - 1.524 / (3840 / 28)) < 1e-6  # 140 ft across

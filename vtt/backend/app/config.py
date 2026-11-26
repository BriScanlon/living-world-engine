"""Runtime configuration, read from environment variables (and ./.env when run locally)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def _load_dotenv(path: Path) -> None:
    """Minimal .env loader for local (non-Docker) runs. Never overrides real env vars."""
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def _bool(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None or value == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    frontend_dist: Path
    workflows_dir: Path
    mock: bool
    llm_api_url: str
    llm_api_key: str
    llm_model: str
    llm_vision_model: str
    comfy_image_url: str
    comfy_video_url: str
    comfy_image_workflow: str
    comfy_video_workflow: str
    comfy_timeout_s: float
    watch_inbox: bool
    inbox_polling: bool
    default_hex_px: float

    # --- derived folders (all under DATA_DIR) ---
    @property
    def maps_dir(self) -> Path:
        return self.data_dir / "maps"

    @property
    def thumbs_dir(self) -> Path:
        return self.data_dir / "thumbs"

    @property
    def uploads_dir(self) -> Path:
        return self.data_dir / "uploads"

    @property
    def inbox_dir(self) -> Path:
        return self.data_dir / "inbox"

    @property
    def starter_dir(self) -> Path:
        return self.data_dir / "starter_pack"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "db" / "vtt.sqlite3"

    def ensure_dirs(self) -> None:
        for d in (
            self.maps_dir,
            self.thumbs_dir,
            self.uploads_dir,
            self.inbox_dir,
            self.starter_dir / "maps",
            self.starter_dir / "tokens",
            self.db_path.parent,
        ):
            d.mkdir(parents=True, exist_ok=True)


def load_settings() -> Settings:
    _load_dotenv(REPO_ROOT / ".env")
    data_dir = Path(os.environ.get("DATA_DIR") or (REPO_ROOT / "data"))
    # A Docker-style "/data" in a local .env should not break local runs on Windows.
    if str(data_dir) in {"/data", "\\data"} and not Path("/data").exists() and os.name == "nt":
        data_dir = REPO_ROOT / "data"
    return Settings(
        data_dir=data_dir.resolve(),
        frontend_dist=Path(os.environ.get("FRONTEND_DIST") or (REPO_ROOT / "frontend" / "dist")),
        workflows_dir=Path(os.environ.get("WORKFLOWS_DIR") or (REPO_ROOT / "workflows")),
        mock=_bool("MOCK_AI_PIPELINE", True),
        llm_api_url=os.environ.get("LLM_API_URL", "http://localhost:11434/v1").rstrip("/"),
        llm_api_key=os.environ.get("LLM_API_KEY", "ollama"),
        llm_model=os.environ.get("LLM_MODEL", "qwen2.5:14b-instruct"),
        llm_vision_model=os.environ.get("LLM_VISION_MODEL", "qwen2.5vl:7b"),
        comfy_image_url=os.environ.get("COMFY_IMAGE_URL", "http://localhost:8188").rstrip("/"),
        comfy_video_url=os.environ.get("COMFY_VIDEO_URL", "").rstrip("/"),
        comfy_image_workflow=os.environ.get("COMFY_IMAGE_WORKFLOW", "flux_battlemap.json"),
        comfy_video_workflow=os.environ.get("COMFY_VIDEO_WORKFLOW", "wan21_flf2v_loop.json"),
        comfy_timeout_s=_float("COMFY_TIMEOUT_S", 1800.0),
        watch_inbox=_bool("WATCH_INBOX", True),
        inbox_polling=_bool("INBOX_POLLING", False),
        default_hex_px=_float("DEFAULT_HEX_PX", 91.8),
    )


settings = load_settings()

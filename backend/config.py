"""Runtime configuration (environment variables / .env)."""
from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=(".env", "../.env"), extra="ignore")

    # TerminusDB
    terminusdb_url: str = "http://127.0.0.1:6363/"
    terminusdb_user: str = "admin"
    terminusdb_pass: str = "root"
    terminusdb_team: str = "admin"
    terminusdb_db: str = "living_world"

    # App
    seed_on_startup: bool = True
    log_level: str = "INFO"
    cors_origins: str = "*"

    # LLM
    mock_llm: bool = True
    llm_base_url: str = "http://localhost:11434/v1"
    llm_api_key: str = "ollama"
    llm_model: str = "llama3.1:8b"
    llm_temperature: float = 0.7
    llm_json_mode: Literal["json_object", "json_schema", "none"] = "json_object"
    llm_concurrency: int = 4
    llm_timeout_seconds: float = 120.0
    llm_max_retries: int = 2
    # Cap on each reply, so a model that starts looping stops instead of running until the timeout.
    # The largest legitimate reply seen so far is about 1.5K tokens (lore extraction).
    llm_max_tokens: int = 4096
    # Sent as reasoning_effort when set: none | low | medium | high ("none" turns a reasoning model's thinking off).
    llm_reasoning_effort: Literal["", "none", "minimal", "low", "medium", "high"] = ""

    # Simulation
    round_length_days: int = 3
    max_rounds: int = 4
    activation_hops: int = 2
    goal_priority_threshold: float = 0.6

    # Ingestion
    whisper_model: str = "base"
    upload_dir: str = "/tmp/lwe_uploads"

    # World Atlas
    atlas_dir: str = "./data/atlas"
    # Forecast run records and checkpoints (default: runs/ next to the atlas folder, in the data volume)
    runs_dir: str = ""
    # On first start, generate the sample planet, import it and place the Greywater demo on it
    atlas_sample_on_startup: bool = True
    tippecanoe_bin: str = "tippecanoe"

    # Virtual tabletop (vtt/): where the world engine sends maps and scenes. Empty = not linked.
    vtt_url: str = ""
    vtt_timeout_seconds: float = 10.0


@lru_cache
def get_settings() -> Settings:
    return Settings()

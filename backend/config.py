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
    # The model's context window in tokens, if known (0 = unknown): only used to warn before sending a prompt that
    # likely won't fit with room for LLM_MAX_TOKENS of reply.
    llm_context_tokens: int = 0
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
    # TerminusDB database holding the homebrew library (custom creatures, peoples, items shared by every world)
    homebrew_db: str = "homebrew_library"
    # On first start, generate the sample planet, import it and place the Greywater demo on it
    atlas_sample_on_startup: bool = True
    tippecanoe_bin: str = "tippecanoe"
    # Imported coasts get fractal detail down to ~100 m (coves and headlands that appear as you zoom in, #62)
    atlas_coast_detail: bool = True
    # The party map's look: "colour" (biome and realm washes) or "ink" (paper and ink only, like Tolkien's maps)
    party_map_style: str = "colour"
    # Parchment atlas tiles over land are drawn ahead in the background up to this tile zoom (below 3 = off), at most
    # this many per look: zoom 8 is about zoom 7 on the globe, a few hundred tiles for a medium region
    parchment_warm_zoom: int = 8
    parchment_warm_tiles: int = 1500

    # Virtual tabletop (vtt/): where the world engine sends maps and scenes. Empty = not linked.
    vtt_url: str = ""
    # The self-hosted Azgaar Fantasy Map Generator's port (fmg/), linked from the wizard; empty = not running
    fmg_port: int | None = None
    vtt_timeout_seconds: float = 10.0


@lru_cache
def get_settings() -> Settings:
    return Settings()

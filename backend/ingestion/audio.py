"""Optional local audio transcription with faster-whisper (CPU int8 by default)."""
from __future__ import annotations

from functools import lru_cache

from config import get_settings


def whisper_available() -> bool:
    try:
        import faster_whisper  # noqa: F401
        return True
    except ImportError:
        return False


@lru_cache
def _model():
    from faster_whisper import WhisperModel

    return WhisperModel(get_settings().whisper_model, device="auto", compute_type="int8")


def transcribe(path: str) -> str:
    if not whisper_available():
        raise RuntimeError("faster-whisper is not installed (set INSTALL_WHISPER=true and rebuild the backend)")
    segments, _info = _model().transcribe(path, vad_filter=True)
    return "\n".join(s.text.strip() for s in segments)

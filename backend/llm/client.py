"""OpenAI-compatible structured-output client (Ollama, vLLM, LM Studio, OpenAI...).

Every call returns a validated Pydantic object. On invalid JSON / schema errors the
model is re-prompted with the validation error (``llm_max_retries`` times).
With ``MOCK_LLM=true`` callers use the deterministic generators in ``llm.mock`` instead.
"""
from __future__ import annotations

import json
import logging
import re
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from config import get_settings

log = logging.getLogger(__name__)
T = TypeVar("T", bound=BaseModel)


class LLMError(RuntimeError):
    pass


def _extract_json(text: str) -> str:
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fence:
        text = fence.group(1).strip()
    if not text.startswith(("{", "[")):
        start = min([i for i in (text.find("{"), text.find("[")) if i >= 0], default=-1)
        if start >= 0:
            text = text[start:]
    return text


class LLMClient:
    def __init__(self) -> None:
        from openai import OpenAI

        self.s = get_settings()
        self.client = OpenAI(base_url=self.s.llm_base_url, api_key=self.s.llm_api_key,
                             timeout=self.s.llm_timeout_seconds)

    def structured(self, system: str, user: str, schema: type[T], *, temperature: float | None = None) -> T:
        json_schema = schema.model_json_schema()
        sys_prompt = (
            f"{system}\n\nRespond with a single JSON object only — no prose, no markdown — "
            f"that validates against this JSON Schema:\n{json.dumps(json_schema)}"
        )
        messages = [{"role": "system", "content": sys_prompt}, {"role": "user", "content": user}]
        kwargs: dict = {}
        if self.s.llm_json_mode == "json_object":
            kwargs["response_format"] = {"type": "json_object"}
        elif self.s.llm_json_mode == "json_schema":
            kwargs["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": schema.__name__, "schema": json_schema},
            }
        last_err: Exception | None = None
        for attempt in range(self.s.llm_max_retries + 1):
            resp = self.client.chat.completions.create(
                model=self.s.llm_model,
                messages=messages,
                temperature=self.s.llm_temperature if temperature is None else temperature,
                **kwargs,
            )
            content = resp.choices[0].message.content or ""
            try:
                return schema.model_validate_json(_extract_json(content))
            except (ValidationError, ValueError) as e:
                last_err = e
                log.warning("LLM output failed validation (attempt %s): %s", attempt + 1, str(e)[:300])
                messages += [
                    {"role": "assistant", "content": content},
                    {"role": "user", "content": f"That did not validate: {str(e)[:800]}\nReturn corrected JSON only."},
                ]
        raise LLMError(f"LLM failed to produce valid {schema.__name__}: {last_err}")


_client: LLMClient | None = None


def get_llm() -> LLMClient:
    global _client
    if _client is None:
        _client = LLMClient()
    return _client

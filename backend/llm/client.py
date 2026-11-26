"""OpenAI-compatible structured-output client (Ollama, vLLM, LM Studio, OpenAI...).

Every call returns a validated Pydantic object. ``LLM_MAX_RETRIES`` is the only retry policy (the SDK's own hidden
retries are off), and every attempt is logged with its wall time and finish reason:

* invalid JSON / schema errors: the model is re-prompted with the validation error;
* a reply cut off at ``LLM_MAX_TOKENS``: the original request is retried fresh (feeding truncated JSON back only
  grows the prompt), and if every attempt is cut off the error names ``LLM_MAX_TOKENS``;
* timeouts, connection errors and 5xx responses: retried as they are; other 4xx errors raise at once.

``LLM_REASONING_EFFORT`` (``none``/``low``/``medium``/``high``) is sent as ``reasoning_effort`` when set, so a
reasoning model can be told not to think before its JSON. With ``MOCK_LLM=true`` callers use ``llm.mock`` instead.
"""
from __future__ import annotations

import json
import logging
import re
import time
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


def _retryable(err: Exception) -> bool:
    """Transport trouble or a server error is worth another attempt; a 4xx (bad request, auth, unknown model) isn't."""
    import openai
    if isinstance(err, (openai.APITimeoutError, openai.APIConnectionError)):
        return True
    return isinstance(err, openai.APIStatusError) and err.status_code >= 500


class LLMClient:
    def __init__(self) -> None:
        from openai import OpenAI

        self.s = get_settings()
        # max_retries=0: LLM_MAX_RETRIES is the only retry policy, so no attempt goes unlogged
        self.client = OpenAI(base_url=self.s.llm_base_url, api_key=self.s.llm_api_key,
                             timeout=self.s.llm_timeout_seconds, max_retries=0)

    def _request_options(self, schema: type[BaseModel], json_schema: dict) -> dict:
        kwargs: dict = {"max_tokens": self.s.llm_max_tokens}
        if self.s.llm_reasoning_effort:
            kwargs["reasoning_effort"] = self.s.llm_reasoning_effort
        if self.s.llm_json_mode == "json_object":
            kwargs["response_format"] = {"type": "json_object"}
        elif self.s.llm_json_mode == "json_schema":
            kwargs["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": schema.__name__, "schema": json_schema},
            }
        return kwargs

    def structured(self, system: str, user: str, schema: type[T], *, temperature: float | None = None) -> T:
        json_schema = schema.model_json_schema()
        sys_prompt = (
            f"{system}\n\nRespond with a single JSON object only — no prose, no markdown — "
            f"that validates against this JSON Schema:\n{json.dumps(json_schema)}"
        )
        messages = [{"role": "system", "content": sys_prompt}, {"role": "user", "content": user}]
        kwargs = self._request_options(schema, json_schema)
        attempts = self.s.llm_max_retries + 1
        last_err: Exception | None = None
        cut_off = 0
        for attempt in range(1, attempts + 1):
            t0 = time.monotonic()
            try:
                resp = self.client.chat.completions.create(
                    model=self.s.llm_model,
                    messages=messages,
                    temperature=self.s.llm_temperature if temperature is None else temperature,
                    **kwargs,
                )
            except Exception as e:  # noqa: BLE001 - classified below
                dt = time.monotonic() - t0
                if not _retryable(e):
                    log.error("LLM %s %s attempt %s/%s failed after %.1fs: %s",
                              self.s.llm_model, schema.__name__, attempt, attempts, dt, e)
                    raise
                last_err = e
                log.warning("LLM %s %s attempt %s/%s: %s after %.1fs",
                            self.s.llm_model, schema.__name__, attempt, attempts, type(e).__name__, dt)
                continue
            dt = time.monotonic() - t0
            choice = resp.choices[0]
            content = choice.message.content or ""
            finish = choice.finish_reason
            used = getattr(resp.usage, "completion_tokens", None) if resp.usage else None
            log.info("LLM %s %s attempt %s/%s: %.1fs, finish=%s, %s output tokens",
                     self.s.llm_model, schema.__name__, attempt, attempts, dt, finish, used)
            if finish == "length":
                # Cut off: the JSON is incomplete. Feeding it back as a "correction" only grows the prompt,
                # so ask again from scratch.
                cut_off += 1
                last_err = LLMError(f"reply cut off at LLM_MAX_TOKENS={self.s.llm_max_tokens}")
                continue
            try:
                return schema.model_validate_json(_extract_json(content))
            except (ValidationError, ValueError) as e:
                last_err = e
                log.warning("LLM output failed validation (attempt %s): %s", attempt, str(e)[:300])
                messages += [
                    {"role": "assistant", "content": content},
                    {"role": "user", "content": f"That did not validate: {str(e)[:800]}\nReturn corrected JSON only."},
                ]
        if cut_off == attempts:
            raise LLMError(f"Every reply for {schema.__name__} was cut off at LLM_MAX_TOKENS={self.s.llm_max_tokens} "
                           "tokens: the model may be looping, or the limit is too low for this call")
        raise LLMError(f"LLM failed to produce valid {schema.__name__}: {last_err}")


_client: LLMClient | None = None


def get_llm() -> LLMClient:
    global _client
    if _client is None:
        _client = LLMClient()
    return _client

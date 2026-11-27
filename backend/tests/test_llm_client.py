"""The OpenAI-compatible client: length caps, reasoning effort, and the one retry policy (no network)."""
from types import SimpleNamespace

import pytest
from pydantic import BaseModel


class Reply(BaseModel):
    answer: str


def _resp(content: str, finish: str = "stop", tokens: int = 10):
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content), finish_reason=finish)],
                           usage=SimpleNamespace(completion_tokens=tokens))


class FakeCompletions:
    def __init__(self, script):
        self.script = list(script)
        self.calls = []

    def create(self, **kw):
        self.calls.append({**kw, "messages": list(kw["messages"])})
        step = self.script.pop(0)
        if isinstance(step, Exception):
            raise step
        return step


@pytest.fixture
def make_client(monkeypatch):
    from config import get_settings
    from llm import client as mod

    def build(script, **env):
        for k, v in {"LLM_MAX_RETRIES": "2", **env}.items():
            monkeypatch.setenv(k, v)
        get_settings.cache_clear()
        c = mod.LLMClient()
        fake = FakeCompletions(script)
        c.client = SimpleNamespace(chat=SimpleNamespace(completions=fake))
        return c, fake

    yield build
    get_settings.cache_clear()


def test_sdk_retries_are_off_and_max_tokens_is_sent(make_client):
    c, fake = make_client([_resp('{"answer": "yes"}')], LLM_MAX_TOKENS="2048")
    from llm.client import LLMClient
    assert LLMClient().client.max_retries == 0  # the SDK never resends behind our back
    assert c.structured("sys", "user", Reply).answer == "yes"
    assert fake.calls[0]["max_tokens"] == 2048
    assert "reasoning_effort" not in fake.calls[0]  # not sent unless configured


def test_reasoning_effort_is_sent_when_set(make_client):
    c, fake = make_client([_resp('{"answer": "ok"}')], LLM_REASONING_EFFORT="none")
    c.structured("sys", "user", Reply)
    assert fake.calls[0]["reasoning_effort"] == "none"


def test_a_cut_off_reply_is_retried_fresh_not_appended(make_client):
    c, fake = make_client([_resp('{"answer": "loo', finish="length", tokens=4096), _resp('{"answer": "done"}')])
    assert c.structured("sys", "user", Reply).answer == "done"
    assert len(fake.calls[1]["messages"]) == 2  # same prompt again, the truncated JSON wasn't fed back


def test_every_reply_cut_off_names_the_setting(make_client):
    from llm.client import LLMError
    c, _ = make_client([_resp("{", finish="length", tokens=64)] * 3, LLM_MAX_TOKENS="64")
    with pytest.raises(LLMError, match="LLM_MAX_TOKENS=64"):
        c.structured("sys", "user", Reply)


def test_invalid_json_is_corrected_in_conversation(make_client):
    c, fake = make_client([_resp('{"wrong": 1}'), _resp('{"answer": "fixed"}')])
    assert c.structured("sys", "user", Reply).answer == "fixed"
    assert len(fake.calls[1]["messages"]) == 4 and "did not validate" in fake.calls[1]["messages"][-1]["content"]


def test_timeouts_and_server_errors_retry_but_client_errors_do_not(make_client):
    import httpx
    import openai
    req = httpx.Request("POST", "http://x/v1/chat/completions")
    c, fake = make_client([openai.APITimeoutError(request=req),
                           openai.InternalServerError("boom", response=httpx.Response(500, request=req), body=None),
                           _resp('{"answer": "third time"}')])
    assert c.structured("sys", "user", Reply).answer == "third time" and len(fake.calls) == 3

    c, fake = make_client([openai.NotFoundError("no such model", response=httpx.Response(404, request=req), body=None),
                           _resp('{"answer": "never"}')])
    with pytest.raises(openai.NotFoundError):
        c.structured("sys", "user", Reply)
    assert len(fake.calls) == 1


def test_a_context_window_cut_off_says_so_and_stops(make_client):
    """Stopped well short of max_tokens: the context ran out. Retrying the same prompt can't help (#24)."""
    from llm.client import LLMError
    resp = _resp('{"answer": "lo', finish="length", tokens=3308)
    resp.usage.prompt_tokens = 13075
    c, fake = make_client([resp, _resp('{"answer": "never"}')], LLM_MAX_TOKENS="8192")
    with pytest.raises(LLMError, match=r"context window, not LLM_MAX_TOKENS: the prompt \(13075 tokens\) "
                                       r"left room for only 3308"):
        c.structured("sys", "user", Reply)
    assert len(fake.calls) == 1

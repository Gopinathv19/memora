"""NvidiaLLMClient against a stubbed OpenAI SDK -- request shape and parsing."""

from types import SimpleNamespace

import httpx
import openai
import pytest

from app.core.config import Settings
from app.llm.client import LLMError, NvidiaLLMClient, parse_json_object


def _response(content=None, tool_arguments=None, prompt=12, completion=3):
    calls = (
        [SimpleNamespace(function=SimpleNamespace(arguments=tool_arguments))]
        if tool_arguments is not None
        else None
    )
    message = SimpleNamespace(content=content, tool_calls=calls)
    return SimpleNamespace(
        choices=[SimpleNamespace(message=message)],
        usage=SimpleNamespace(prompt_tokens=prompt, completion_tokens=completion),
    )


class StubCompletions:
    def __init__(self, replies):
        self.replies = list(replies)
        self.requests: list[dict] = []

    def create(self, **kwargs):
        self.requests.append(kwargs)
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


def _client(replies, **settings):
    client = NvidiaLLMClient(
        Settings(database_url="postgresql+psycopg://x/y", nvidia_api_key="nvapi-test", **settings)
    )
    stub = StubCompletions(replies)
    client._client = SimpleNamespace(chat=SimpleNamespace(completions=stub))
    return client, stub


def _bad_request():
    request = httpx.Request("POST", "https://integrate.api.nvidia.com/v1/chat/completions")
    return openai.BadRequestError(
        "unsupported parameter", response=httpx.Response(400, request=request), body=None
    )


def test_chat_json_parses_and_counts_tokens():
    client, stub = _client([_response('<think>hmm</think>```json\n{"a": 1}\n```')])
    value, usage = client.chat_json("nvidia/x", "sys", "user")
    assert value == {"a": 1}
    assert usage.prompt_tokens == 12 and usage.completion_tokens == 3
    request = stub.requests[0]
    assert request["temperature"] == 0
    assert request["response_format"] == {"type": "json_object"}


def test_rejected_optional_parameters_are_shed_one_by_one():
    client, stub = _client([_bad_request(), _bad_request(), _response('{"ok": true}')])
    value, _ = client.chat_json("nvidia/x", "sys", "user")
    assert value == {"ok": True}
    assert "response_format" in stub.requests[0]
    assert "response_format" not in stub.requests[1] and "extra_body" in stub.requests[1]
    assert "extra_body" not in stub.requests[2]


def test_invalid_json_gets_one_repair_attempt():
    client, stub = _client([_response("Sure! Here you go."), _response('{"fixed": 1}')])
    value, usage = client.chat_json("nvidia/x", "sys", "user")
    assert value == {"fixed": 1}
    assert usage.prompt_tokens == 24  # both calls counted
    assert stub.requests[1]["messages"][-2]["role"] == "assistant"


def test_still_invalid_json_raises_with_usage():
    client, _ = _client([_response("nope"), _response("still nope")])
    with pytest.raises(LLMError) as caught:
        client.chat_json("nvidia/x", "sys", "user")
    assert caught.value.usage.prompt_tokens == 24


def test_nemotron_parse_is_called_with_its_tool_and_read_from_tool_calls():
    arguments = '[[{"text": "# Invoice"}, {"text": "| a | b |"}]]'
    client, stub = _client([_response(tool_arguments=arguments)])
    text, _ = client.read_image("nvidia/nemotron-parse", b"png", "image/png", "ignored")
    assert text == "# Invoice\n\n| a | b |"
    request = stub.requests[0]
    assert request["tools"][0]["function"]["name"] == "markdown_no_bbox"
    [part] = request["messages"][0]["content"]  # image only, no text prompt
    assert part["image_url"]["url"].startswith("data:image/png;base64,")


def test_vision_models_get_the_prompt_and_the_image():
    client, stub = _client([_response("A red square.")])
    text, _ = client.read_image("nvidia/nemotron-3-nano-omni-30b-a3b-reasoning", b"png", "image/png", "Describe")
    assert text == "A red square."
    content = stub.requests[0]["messages"][0]["content"]
    assert content[0] == {"type": "text", "text": "Describe"}
    assert content[1]["type"] == "image_url"


def test_missing_api_key_is_a_clear_error():
    client = NvidiaLLMClient(
        Settings(database_url="postgresql+psycopg://x/y", llm_provider="nebius", nebius_api_key="")
    )
    with pytest.raises(LLMError, match="NEBIUS_API_KEY is not set"):
        client.chat_json("nvidia/x", "sys", "user")


def test_parse_json_object_finds_the_object_in_chatter():
    assert parse_json_object('Result:\n{"k": "v"}\nThanks') == {"k": "v"}
    with pytest.raises(ValueError):
        parse_json_object("[1, 2]")

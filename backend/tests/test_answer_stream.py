"""Streaming answers: the think-block filter and the client's stream protocol."""

from types import SimpleNamespace as NS

import pytest

from app.llm.client import LLMError, NvidiaLLMClient, _strip_think_stream


def test_think_block_is_dropped_across_piece_boundaries():
    pieces = ["<th", "ink>reasoning</th", "ink>\n\nHel", "lo"]
    assert "".join(_strip_think_stream(pieces)) == "Hello"


def test_plain_text_passes_through_untouched():
    assert list(_strip_think_stream(["Hel", "lo"])) == ["Hel", "lo"]
    assert "".join(_strip_think_stream(["<t"])) == "<t"


def _event(text=None, usage=None):
    choices = [NS(delta=NS(content=text))] if text is not None else []
    return NS(choices=choices, usage=usage)


def _client(events):
    client = NvidiaLLMClient.__new__(NvidiaLLMClient)
    client.settings = NS(llm_max_output_tokens=100)
    client._create = lambda optional, **kw: iter(events)  # type: ignore[method-assign]
    return client


def test_stream_yields_deltas_then_usage():
    events = [
        _event("Hel"),
        _event("lo"),
        _event(usage=NS(prompt_tokens=7, completion_tokens=2)),
    ]
    out = list(_client(events).chat_text_stream("m", "sys", "user"))
    assert [v for k, v in out if k == "delta"] == ["Hel", "lo"]
    kind, usage = out[-1]
    assert kind == "usage" and usage.prompt_tokens == 7 and usage.completion_tokens == 2


def test_empty_stream_is_an_error():
    with pytest.raises(LLMError):
        list(_client([_event(usage=NS(prompt_tokens=1, completion_tokens=0))]).chat_text_stream("m", "s", "u"))

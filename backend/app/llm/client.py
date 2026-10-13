"""The one place Memora talks to a model provider.

Both supported providers -- build.nvidia.com and Nebius Token Factory -- expose
the OpenAI chat-completions API, so one SDK client per provider serves both.
Text calls go to the environment's provider; image calls always go to
build.nvidia.com, because Nebius serves no NVIDIA vision or parse model. Nothing outside `app/llm` imports the `openai` SDK; the
Extraction Agent depends on the small `LLMClient` protocol below, which is also
what tests replace with a fake so the suite never spends credits.
"""

import base64
import json
import logging
import re
import threading
import time

log = logging.getLogger(__name__)
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Protocol

from app.core.config import Settings, get_settings
from app.llm import limiter, retry
from app.llm.retry import call_with_retry


class LLMError(Exception):
    """A model call failed. `usage` holds whatever the provider still billed.

    `category` (see app/llm/retry.py) tells the caller whether the failure is
    retryable on the same model: a timeout is not -- the agent falls through
    to the next model in its chain instead of paying for the same call twice.
    """

    def __init__(
        self,
        message: str,
        usage: "LLMUsage | None" = None,
        category: str | None = None,
    ):
        super().__init__(message)
        self.message = message
        self.usage = usage
        self.category = category


@dataclass
class LLMUsage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    latency_ms: int = 0
    # Optional parameters a provider rejected (400) and the client shed --
    # recorded so a slow call is explainable from the usage row alone.
    shed_params: list[str] = field(default_factory=list)

    def __add__(self, other: "LLMUsage") -> "LLMUsage":
        return LLMUsage(
            self.prompt_tokens + other.prompt_tokens,
            self.completion_tokens + other.completion_tokens,
            self.latency_ms + other.latency_ms,
        )


class LLMClient(Protocol):
    provider: str

    def chat_json(self, model: str, system: str, user: str) -> tuple[dict, LLMUsage]:
        """Ask for a single JSON object and return it parsed."""
        ...

    def chat_text(
        self, model: str, system: str, user: str, interactive: bool = False
    ) -> tuple[str, LLMUsage]:
        """Ask for free text and return it cleaned."""
        ...

    def chat_text_stream(
        self, model: str, system: str, user: str, interactive: bool = False
    ):
        """Stream free text: yields ("delta", str) pieces, then ("usage", LLMUsage)."""
        ...

    def read_image(
        self,
        model: str,
        image: bytes,
        mime: str,
        prompt: str,
        *,
        strict: bool = False,
        max_tokens: int | None = None,
        disable_thinking: bool | None = None,
        timeout: float | None = None,
    ) -> tuple[str, LLMUsage]:
        """Send one image with an instruction and return the model's text.

        `strict` asks for tighter decoding (no repetition, exact output):
        used for the one retry after a reading fails the cleaning gate.
        `max_tokens` scales the output budget to the page; `disable_thinking`
        turns off the reasoning preamble for transcription-style calls.
        """
        ...


_THINK = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
_FENCE = re.compile(r"^```(?:json|markdown|md)?\s*|\s*```$", re.IGNORECASE)


def clean_text(text: str | None) -> str:
    """Drop reasoning blocks and code fences some models wrap answers in."""
    text = _THINK.sub("", text or "").strip()
    return _FENCE.sub("", text).strip()


def _strip_think_stream(pieces):
    """Drop a leading <think>...</think> block from a stream of text pieces."""
    open_tag, close_tag = "<think>", "</think>"
    buffer = ""
    state = "start"  # start -> thinking | pass
    for piece in pieces:
        if state == "pass":
            yield piece
            continue
        buffer += piece
        if state == "start":
            head = buffer.lstrip()
            if head.startswith(open_tag):
                state = "thinking"
                buffer = head[len(open_tag):]
            elif open_tag.startswith(head):
                continue  # could still turn out to be the opening tag
            else:
                state = "pass"
                yield buffer
                buffer = ""
                continue
        if state == "thinking" and close_tag in buffer:
            rest = buffer.split(close_tag, 1)[1].lstrip()
            state = "pass"
            buffer = ""
            if rest:
                yield rest
    if state == "start" and buffer:
        yield buffer


def parse_json_object(text: str | None) -> dict:
    """Pull the JSON object out of a model reply, tolerating chatter around it."""
    cleaned = clean_text(text)
    try:
        value = json.loads(cleaned)
    except json.JSONDecodeError:
        start, end = cleaned.find("{"), cleaned.rfind("}")
        if start == -1 or end <= start:
            raise ValueError("reply contains no JSON object") from None
        value = json.loads(cleaned[start : end + 1])
    if not isinstance(value, dict):
        raise ValueError("reply JSON is not an object")
    return value


def is_nemotron_parse(model: str) -> bool:
    return "nemotron-parse" in model.lower()


def _parse_tool_output(arguments: str) -> str:
    """Nemotron-Parse answers through a tool call whose arguments are JSON.

    The shape differs between tool modes and versions (a list of elements, a
    list of lists, or an object), so collect every `text` it contains in order.
    """
    try:
        value = json.loads(arguments)
    except (json.JSONDecodeError, TypeError):
        return arguments or ""
    texts: list[str] = []

    def walk(node) -> None:
        if isinstance(node, dict):
            if isinstance(node.get("text"), str):
                texts.append(node["text"])
            else:
                for child in node.values():
                    walk(child)
        elif isinstance(node, list):
            for child in node:
                walk(child)
        elif isinstance(node, str):
            texts.append(node)

    walk(value)
    return "\n\n".join(t for t in texts if t.strip())


class NvidiaLLMClient:
    """OpenAI-compatible client for NVIDIA models on build.nvidia.com or Nebius."""

    def __init__(self, settings: Settings):
        self.settings = settings
        # The text-model provider; image calls may go elsewhere (see module doc).
        self.provider = settings.llm_provider
        self._clients: dict[str, object] = {}
        self._lock = threading.Lock()

    def _sdk(self, provider: str):
        # Built lazily so a missing key fails the run that needs it with a
        # clear message, instead of failing app startup or unrelated requests.
        if provider not in self._clients:
            with self._lock:
                if provider not in self._clients:
                    base_url, api_key = self.settings.llm_endpoint(provider)
                    if not api_key:
                        env = "NEBIUS_API_KEY" if provider == "nebius" else "NVIDIA_API_KEY"
                        raise LLMError(f"{env} is not set; add it to backend/.env")
                    import openai

                    self._clients[provider] = openai.OpenAI(
                        base_url=base_url,
                        api_key=api_key,
                        timeout=self.settings.llm_timeout_seconds,
                        # Retrying is Memora's own policy (app/llm/retry.py):
                        # the SDK's backoff multiplied with the agent's
                        # fallback chain, stacking timeouts into 10+ minute
                        # runs. The SDK never retries.
                        max_retries=self.settings.llm_sdk_max_retries,
                    )
        return self._clients[provider]

    def _create(
        self,
        optional: dict,
        provider: str | None = None,
        interactive: bool = False,
        **kwargs,
    ):
        """Call chat.completions, dropping optional parameters a model rejects.

        Providers differ in what they accept (JSON mode, thinking switches), so
        each optional parameter is tried and shed on a 400 rather than guessed.
        """
        import openai

        sdk = self._sdk(provider or self.provider)
        extras = dict(optional)
        shed: list[str] = []
        while True:
            try:
                # Retry policy (timeouts never retried, 429 honors
                # Retry-After) is applied per call, in one place. The
                # shared limiter caps in-flight calls per provider bucket.
                with limiter.limited(provider or self.provider, interactive):
                    response = call_with_retry(
                        lambda: sdk.chat.completions.create(**kwargs, **extras),
                        self.settings,
                    )
                if shed:
                    # The call succeeded only after shedding: log it and
                    # carry the names on the usage so it is recorded.
                    log.warning(
                        "provider rejected optional parameters; shed: %s", shed
                    )
                    response._memora_shed_params = shed  # carried to _usage
                return response
            except openai.BadRequestError as exc:
                if not extras:
                    raise LLMError(
                        f"provider rejected the request: {exc}",
                        category=retry.CLIENT_ERROR,
                    ) from exc
                shed.append(next(iter(extras)))
                extras.pop(next(iter(extras)))

    @staticmethod
    def _usage(response, started: float) -> LLMUsage:
        usage = getattr(response, "usage", None)
        return LLMUsage(
            prompt_tokens=getattr(usage, "prompt_tokens", 0) or 0,
            completion_tokens=getattr(usage, "completion_tokens", 0) or 0,
            latency_ms=int((time.monotonic() - started) * 1000),
            shed_params=getattr(response, "_memora_shed_params", []) or [],
        )

    def chat_json(self, model: str, system: str, user: str) -> tuple[dict, LLMUsage]:
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        optional = {
            "response_format": {"type": "json_object"},
            # Nemotron reasoning models: answer directly, no <think> preamble.
            "extra_body": {"chat_template_kwargs": {"enable_thinking": False}},
        }
        started = time.monotonic()
        response = self._create(
            optional,
            model=model,
            messages=messages,
            temperature=0,
            max_tokens=self.settings.llm_max_output_tokens,
        )
        total = self._usage(response, started)
        reply = response.choices[0].message.content or ""
        try:
            return parse_json_object(reply), total
        except (ValueError, json.JSONDecodeError):
            pass

        # One repair attempt: show the model its own reply and ask for JSON only.
        started = time.monotonic()
        response = self._create(
            {},
            model=model,
            messages=messages
            + [
                {"role": "assistant", "content": reply[:20000]},
                {
                    "role": "user",
                    "content": "That reply was not a valid JSON object. Return only "
                    "the JSON object, with no other text.",
                },
            ],
            temperature=0,
            max_tokens=self.settings.llm_max_output_tokens,
        )
        total = total + self._usage(response, started)
        try:
            return parse_json_object(response.choices[0].message.content), total
        except (ValueError, json.JSONDecodeError) as exc:
            raise LLMError(f"model did not return valid JSON: {exc}", usage=total) from exc

    def chat_text(
        self, model: str, system: str, user: str, interactive: bool = False
    ) -> tuple[str, LLMUsage]:
        """Free-text generation: the grounded-answer half of the retrieval
        pipeline. Same optional-parameter shedding as every other call."""
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        optional = {
            # Nemotron reasoning models: answer directly, no </think> preamble.
            "extra_body": {"chat_template_kwargs": {"enable_thinking": False}},
        }
        started = time.monotonic()
        response = self._create(
            optional,
            interactive=interactive,
            model=model,
            messages=messages,
            temperature=0.2,
            max_tokens=self.settings.llm_max_output_tokens,
        )
        usage = self._usage(response, started)
        text = clean_text(response.choices[0].message.content)
        if not text:
            raise LLMError("model returned an empty answer", usage=usage)
        return text, usage

    def chat_text_stream(
        self, model: str, system: str, user: str, interactive: bool = False
    ):
        """Streamed free-text generation: yields ("delta", str) pieces as the
        model produces them, then one ("usage", LLMUsage) at the end.

        Same prompt and sampling as `chat_text`, so the answer is the same;
        only the delivery differs. Reasoning blocks are filtered out of the
        stream. A failure before the first token raises like `chat_text`; one
        mid-stream surfaces as an LLMError after the deltas already yielded.
        """
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        optional = {
            "extra_body": {"chat_template_kwargs": {"enable_thinking": False}},
            "stream_options": {"include_usage": True},
        }
        started = time.monotonic()
        stream = self._create(
            optional,
            interactive=interactive,
            model=model,
            messages=messages,
            temperature=0.2,
            max_tokens=self.settings.llm_max_output_tokens,
            stream=True,
        )
        usage_obj = None
        emitted = False

        def deltas():
            nonlocal usage_obj
            for event in stream:
                if getattr(event, "usage", None) is not None:
                    usage_obj = event
                if event.choices:
                    piece = event.choices[0].delta.content
                    if piece:
                        yield piece

        try:
            for piece in _strip_think_stream(deltas()):
                emitted = True
                yield ("delta", piece)
        except Exception as exc:  # noqa: BLE001 - mid-stream transport errors
            raise LLMError(f"answer stream failed: {exc}") from exc
        if not emitted:
            raise LLMError("model returned an empty answer")
        usage = self._usage(usage_obj, started) if usage_obj else LLMUsage(
            latency_ms=int((time.monotonic() - started) * 1000)
        )
        usage.shed_params = getattr(stream, "_memora_shed_params", []) or []
        yield ("usage", usage)

    def read_image(
        self,
        model: str,
        image: bytes,
        mime: str,
        prompt: str,
        *,
        strict: bool = False,
        max_tokens: int | None = None,
        disable_thinking: bool | None = None,
        timeout: float | None = None,
    ) -> tuple[str, LLMUsage]:
        data_url = f"data:{mime};base64,{base64.b64encode(image).decode('ascii')}"
        request_timeout = timeout or self.settings.llm_timeout_seconds
        image_part = {"type": "image_url", "image_url": {"url": data_url}}
        started = time.monotonic()
        provider = self.settings.llm_provider_for("vision")
        # Stricter decoding for the post-cleaning-gate retry: no repetition,
        # no thinking. Optional parameters are shed on a 400 (see _create).
        strict_optional = (
            {"frequency_penalty": 0.0, "presence_penalty": 0.0}
            if strict
            else {}
        )
        tokens = max_tokens or self.settings.llm_max_output_tokens
        if disable_thinking is None:
            disable_thinking = self.settings.vision_disable_thinking
        thinking_optional = (
            {"extra_body": {"chat_template_kwargs": {"enable_thinking": False}}}
            if disable_thinking
            else {}
        )

        if is_nemotron_parse(model):
            # Nemotron-Parse takes only the image and answers via a tool call.
            response = self._create(
                {},
                provider,
                model=model,
                messages=[{"role": "user", "content": [image_part]}],
                tools=[{"type": "function", "function": {"name": "markdown_no_bbox"}}],
                temperature=0,
                max_tokens=self.settings.llm_max_output_tokens,
                timeout=request_timeout,
            )
            message = response.choices[0].message
            calls = getattr(message, "tool_calls", None) or []
            text = (
                _parse_tool_output(calls[0].function.arguments)
                if calls
                else clean_text(message.content)
            )
        else:
            response = self._create(
                {**thinking_optional, **strict_optional},
                provider,
                model=model,
                messages=[
                    {
                        "role": "user",
                        "content": [{"type": "text", "text": prompt}, image_part],
                    }
                ],
                temperature=0,
                max_tokens=tokens,
                timeout=request_timeout,
            )
            text = clean_text(response.choices[0].message.content)

        usage = self._usage(response, started)
        if not text.strip():
            raise LLMError("model returned no text for the image", usage=usage)
        return text, usage


@lru_cache
def get_llm_client() -> LLMClient:
    return NvidiaLLMClient(get_settings())

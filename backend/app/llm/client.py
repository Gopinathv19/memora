"""The one place Memora talks to a model provider.

Both supported providers -- build.nvidia.com and Nebius Token Factory -- expose
the OpenAI chat-completions API, so a single client pointed at a configurable
`base_url` serves both. Nothing outside `app/llm` imports the `openai` SDK; the
Extraction Agent depends on the small `LLMClient` protocol below, which is also
what tests replace with a fake so the suite never spends credits.
"""

import base64
import json
import re
import threading
import time
from dataclasses import dataclass
from functools import lru_cache
from typing import Protocol

from app.core.config import Settings, get_settings


class LLMError(Exception):
    """A model call failed. `usage` holds whatever the provider still billed."""

    def __init__(self, message: str, usage: "LLMUsage | None" = None):
        super().__init__(message)
        self.message = message
        self.usage = usage


@dataclass
class LLMUsage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    latency_ms: int = 0

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

    def read_image(
        self, model: str, image: bytes, mime: str, prompt: str
    ) -> tuple[str, LLMUsage]:
        """Send one image with an instruction and return the model's text."""
        ...


_THINK = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
_FENCE = re.compile(r"^```(?:json|markdown|md)?\s*|\s*```$", re.IGNORECASE)


def clean_text(text: str | None) -> str:
    """Drop reasoning blocks and code fences some models wrap answers in."""
    text = _THINK.sub("", text or "").strip()
    return _FENCE.sub("", text).strip()


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
        self.provider = settings.llm_provider
        self._client = None
        self._lock = threading.Lock()

    def _sdk(self):
        # Built lazily so a missing key fails the run that needs it with a
        # clear message, instead of failing app startup or unrelated requests.
        if self._client is None:
            with self._lock:
                if self._client is None:
                    if not self.settings.llm_api_key:
                        env = "NEBIUS_API_KEY" if self.provider == "nebius" else "NVIDIA_API_KEY"
                        raise LLMError(f"{env} is not set; add it to backend/.env")
                    import openai

                    self._client = openai.OpenAI(
                        base_url=self.settings.llm_base_url,
                        api_key=self.settings.llm_api_key,
                        timeout=self.settings.llm_timeout_seconds,
                        # The SDK retries 408/409/429/5xx with exponential backoff.
                        max_retries=self.settings.llm_max_retries,
                    )
        return self._client

    def _create(self, optional: dict, **kwargs):
        """Call chat.completions, dropping optional parameters a model rejects.

        Providers differ in what they accept (JSON mode, thinking switches), so
        each optional parameter is tried and shed on a 400 rather than guessed.
        """
        import openai

        sdk = self._sdk()
        extras = dict(optional)
        while True:
            try:
                return sdk.chat.completions.create(**kwargs, **extras)
            except openai.BadRequestError as exc:
                if not extras:
                    raise LLMError(f"provider rejected the request: {exc}") from exc
                extras.pop(next(iter(extras)))
            except openai.APIError as exc:
                raise LLMError(f"{type(exc).__name__}: {exc}") from exc

    @staticmethod
    def _usage(response, started: float) -> LLMUsage:
        usage = getattr(response, "usage", None)
        return LLMUsage(
            prompt_tokens=getattr(usage, "prompt_tokens", 0) or 0,
            completion_tokens=getattr(usage, "completion_tokens", 0) or 0,
            latency_ms=int((time.monotonic() - started) * 1000),
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

    def read_image(
        self, model: str, image: bytes, mime: str, prompt: str
    ) -> tuple[str, LLMUsage]:
        data_url = f"data:{mime};base64,{base64.b64encode(image).decode('ascii')}"
        image_part = {"type": "image_url", "image_url": {"url": data_url}}
        started = time.monotonic()

        if is_nemotron_parse(model):
            # Nemotron-Parse takes only the image and answers via a tool call.
            response = self._create(
                {},
                model=model,
                messages=[{"role": "user", "content": [image_part]}],
                tools=[{"type": "function", "function": {"name": "markdown_no_bbox"}}],
                temperature=0,
                max_tokens=self.settings.llm_max_output_tokens,
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
                {"extra_body": {"chat_template_kwargs": {"enable_thinking": False}}},
                model=model,
                messages=[
                    {
                        "role": "user",
                        "content": [{"type": "text", "text": prompt}, image_part],
                    }
                ],
                temperature=0,
                max_tokens=self.settings.llm_max_output_tokens,
            )
            text = clean_text(response.choices[0].message.content)

        usage = self._usage(response, started)
        if not text.strip():
            raise LLMError("model returned no text for the image", usage=usage)
        return text, usage


@lru_cache
def get_llm_client() -> LLMClient:
    return NvidiaLLMClient(get_settings())

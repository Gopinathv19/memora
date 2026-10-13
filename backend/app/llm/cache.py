"""Content-addressed cache for temperature-0 JSON model calls.

The window-extract, document-metadata and graph-chunk calls all send a prompt
to a model at temperature 0 and parse a JSON object from the reply. The same
(model, system prompt, user message) is the same request, so a re-extract or a
graph rebuild of unchanged text should not pay for it again. The key hashes
all three, so any prompt, ontology or model change is a miss by construction.

Only well-formed JSON objects are stored, in the database (shared across
workers and restarts). A cache failure is never a call failure.
"""

import hashlib
import logging

from app.core.config import get_settings
from app.llm.client import LLMClient, LLMUsage

log = logging.getLogger(__name__)

_KEY_VERSION = "memora-llm-json-1"


def cache_key(model: str, system: str, user: str) -> str:
    digest = hashlib.sha256()
    for part in (_KEY_VERSION, model, system, user):
        digest.update(hashlib.sha256(part.encode("utf-8")).digest())
    return digest.hexdigest()


def _get(key: str) -> dict | None:
    from app.db.database import SessionLocal
    from app.db.models import LlmCacheEntry

    try:
        with SessionLocal() as db:
            row = db.get(LlmCacheEntry, key)
            return row.value if row else None
    except Exception:
        log.warning("llm cache read failed", exc_info=True)
        return None


def _put(key: str, value: dict) -> None:
    from app.db.database import SessionLocal
    from app.db.models import LlmCacheEntry

    try:
        with SessionLocal() as db:
            db.merge(LlmCacheEntry(key=key, value=value))
            db.commit()
    except Exception:
        log.warning("llm cache write failed", exc_info=True)


def cached_chat_json(
    client: LLMClient, model: str, system: str, user: str
) -> tuple[dict, LLMUsage | None, bool]:
    """chat_json through the cache. Returns (reply, usage, hit); on a hit the
    usage is None (no model call was made, nothing to bill)."""
    if not get_settings().llm_cache_enabled:
        raw, usage = client.chat_json(model, system, user)
        return raw, usage, False
    key = cache_key(model, system, user)
    hit = _get(key)
    if hit is not None:
        return hit, None, True
    raw, usage = client.chat_json(model, system, user)
    if isinstance(raw, dict) and raw:
        _put(key, raw)
    return raw, usage, False

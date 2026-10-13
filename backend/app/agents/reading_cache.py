"""Cache of successful, validated model readings.

Two layers: a small in-process LRU (bounded, thread-safe) in front of a
database-backed store, so a validated reading survives restarts, worker
changes and in-memory eviction. Keyed by sha256(image bytes) + model +
prompt version + render settings + prompt -- the same key for both layers.
Only readings that passed the cleaning gate are cached; failures and
damaged readings are not.
"""

import hashlib
import threading
from collections import OrderedDict


class ReadingCache:
    """A small thread-safe LRU. Bounded so memory stays predictable."""

    def __init__(self, max_entries: int = 256, store: "ReadingStore | None" = None):
        self._entries: OrderedDict[str, str] = OrderedDict()
        self._lock = threading.Lock()
        self._max = max(1, max_entries)
        self._store = store
        self.hits = 0
        self.misses = 0

    @staticmethod
    def key(
        image: bytes,
        model: str,
        prompt_version: str,
        render_long_side: int,
        prompt: str,
    ) -> str:
        digest = hashlib.sha256()
        digest.update(image)
        digest.update(model.encode("utf-8"))
        digest.update(prompt_version.encode("utf-8"))
        digest.update(str(render_long_side).encode("ascii"))
        digest.update(prompt.encode("utf-8"))
        return digest.hexdigest()

    def get(self, key: str) -> str | None:
        with self._lock:
            if key in self._entries:
                self._entries.move_to_end(key)
                self.hits += 1
                return self._entries[key]
        # Not in memory: the persistent store may have it (another process,
        # an earlier run before a restart, an evicted entry).
        if self._store is not None:
            text = self._store.get(key)
            if text is not None:
                self.put(key, text)  # refill the in-memory layer
                self.hits += 1
                return text
        self.misses += 1
        return None

    def put(self, key: str, text: str) -> None:
        with self._lock:
            self._entries[key] = text
            self._entries.move_to_end(key)
            while len(self._entries) > self._max:
                self._entries.popitem(last=False)
        if self._store is not None:
            self._store.put(key, text)


class ReadingStore:
    """What a persistent reading-cache backend must do."""

    def get(self, key: str) -> str | None: ...
    def put(self, key: str, text: str) -> None: ...


class DbReadingStore(ReadingStore):
    """The database-backed layer: one row per validated reading.

    Each operation opens its own short session, because the agent's worker
    threads call this while the run's own session/transaction is busy.
    """

    def __init__(self):
        from app.db.database import SessionLocal

        self._SessionLocal = SessionLocal

    def get(self, key: str) -> str | None:
        from app.db.models import ReadingCacheEntry

        db = self._SessionLocal()
        try:
            row = db.get(ReadingCacheEntry, key)
            return row.text if row else None
        except Exception:
            return None  # a cache miss must never fail a run
        finally:
            db.close()

    def put(self, key: str, text: str) -> None:
        from app.db.models import ReadingCacheEntry

        db = self._SessionLocal()
        try:
            db.merge(ReadingCacheEntry(key=key, text=text))
            db.commit()
        except Exception:
            db.rollback()  # a cache write must never fail a run
        finally:
            db.close()
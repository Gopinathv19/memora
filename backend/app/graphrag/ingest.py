"""Build the unified graph for a set of sources: the whole of plan Steps 1-4 in one call.

    windows (blocks packed from the active chunks)      windows.py
      -> one extraction call each, a few at a time       extract.py
      -> entities resolved against each other and the    resolve.py
         tenant's existing entities
      -> written under the chunks' own ids               graph_write.py
      -> entities and atomic facts embedded              here

`build_windows` takes windows, so it runs on fakes in tests; `build_sources` loads them
from the database (read-only) first. Nothing here touches the vector pipeline: it only reads
`retrieval_chunks`. Embedding and the entity judge are optional; without them resolution is
exact and acronym matching only.

The report says what was done and what it cost, so a build can be compared with another.
"""

from __future__ import annotations

import time
import uuid
from collections import Counter
from dataclasses import dataclass, field
from typing import Callable

from app.graphrag import graph_write
from app.graphrag.build import entity_text
from app.graphrag.extract import WindowExtractor, WindowResult, extract_windows
from app.graphrag.resolve import PairJudge, candidates_from_extractions, resolve
from app.graphrag.store import GraphRagStore
from app.graphrag.windows import Window, load_windows

EMBED_BATCH = 200

# (texts) -> (vectors, tokens used)
EmbedFn = Callable[[list[str]], tuple[list[list[float]], int]]


@dataclass
class BuildReport:
    windows: int = 0
    failed_windows: int = 0
    gleaned_windows: int = 0
    extraction: Counter = field(default_factory=Counter)
    resolution: Counter = field(default_factory=Counter)
    written: Counter = field(default_factory=Counter)
    embedded: Counter = field(default_factory=Counter)
    usage: list = field(default_factory=list)            # UsageRecord per extraction call
    judge_calls: int = 0
    proposed_relations: Counter = field(default_factory=Counter)
    proposed_types: Counter = field(default_factory=Counter)
    errors: list[str] = field(default_factory=list)
    timings_s: dict = field(default_factory=dict)


def embed_atomic_facts(store: GraphRagStore, tenant_id, source_ids: list[str], embed: EmbedFn, out: Counter) -> None:
    """Embed atomic facts that have no vector yet (idempotent: embedded ones are skipped)."""
    while True:
        rows = store.atoms_missing_embedding(tenant_id, source_ids, EMBED_BATCH)
        if not rows:
            return
        vectors, tokens = embed([r["text"] for r in rows])
        store.set_atom_embeddings(tenant_id, [{"id": r["id"], "embedding": v} for r, v in zip(rows, vectors)])
        out["atomic_facts"] += len(rows)
        out["tokens"] += tokens


def embed_entities(store: GraphRagStore, tenant_id, source_ids: list[str], embed: EmbedFn, out: Counter) -> None:
    while True:
        rows = store.entities_missing_embedding(tenant_id, source_ids, EMBED_BATCH)
        if not rows:
            return
        texts = [entity_text(r) for r in rows]
        vectors, tokens = embed(texts)
        store.set_entity_embeddings(
            tenant_id, [{"id": r["id"], "embedding": v, "text": t} for r, v, t in zip(rows, vectors, texts)]
        )
        out["entities"] += len(rows)
        out["tokens"] += tokens


def build_windows(
    store: GraphRagStore, tenant_id, windows: list[Window], *, extractor: WindowExtractor,
    embed: EmbedFn | None = None, dimension: int | None = None, judge: PairJudge | None = None,
    concurrency: int = 4, build_id: str | None = None, replace: bool = True,
    on_progress: Callable[[int, int], None] | None = None, signature: str | None = None,
) -> BuildReport:
    """Extract, resolve, write and embed. `embed` and `dimension` go together (the vector index size)."""
    report = BuildReport(windows=len(windows))
    if not windows:
        return report
    source_ids = sorted({w.source_id for w in windows})
    build_id = build_id or str(uuid.uuid4())
    clock = time.monotonic()

    def lap(name: str) -> None:
        nonlocal clock
        now = time.monotonic()
        report.timings_s[name] = round(now - clock, 2)
        clock = now

    if dimension is not None:
        store.ensure(tenant_id, dimension)

    results: list[WindowResult] = extract_windows(extractor, windows, concurrency, on_progress)
    lap("extract")
    for r in results:
        report.usage += r.usage
        if not r.ok:
            report.failed_windows += 1
            report.errors.append(f"{r.window.id}: {r.error}")
            continue
        e = r.extraction
        report.gleaned_windows += e.gleaned
        report.extraction.update(e.stats)
        report.proposed_relations.update(e.proposed_relations)
        report.proposed_types.update(e.proposed_types)
    if report.failed_windows == len(windows):
        report.errors.append("every window failed: nothing was written")
        return report

    cands = candidates_from_extractions(tenant_id, results, graph_write.known_candidates(store, tenant_id))
    resolution = resolve(cands, embed=(lambda texts: embed(texts)[0]) if embed else None, judge=judge)
    report.resolution = resolution.stats
    report.judge_calls = getattr(judge, "calls", 0)
    lap("resolve")

    report.written = graph_write.write_batch(
        store, tenant_id, results, resolution, build_id=build_id, replace=replace, signature=signature
    )
    lap("write")

    if embed is not None:
        embed_entities(store, tenant_id, source_ids, embed, report.embedded)
        embed_atomic_facts(store, tenant_id, source_ids, embed, report.embedded)
        lap("embed")
    return report


def build_sources(
    session_factory, store: GraphRagStore, tenant_id, source_ids: list[uuid.UUID] | None = None, **kwargs
) -> BuildReport:
    """Load the sources' windows (read-only) and build them. See `build_windows`.

    Takes a session *factory*, not a session: the windows are loaded in a short session that is
    closed before any model call. A build runs for minutes, and a connection left idle inside a
    transaction that long is killed by the database (it was, on the first end-to-end run).
    """
    with session_factory() as db:
        windows = load_windows(db, tenant_id, source_ids)
    return build_windows(store, tenant_id, windows, **kwargs)

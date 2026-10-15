"""Run the pipelines on one question, side by side.

Each pipeline runs in its own thread with its own database session (a SQLAlchemy
session is not thread safe), and each result is stored in `rag_runs` as it
finishes. One pipeline failing never hides another's answer: its slot carries
`status="error"` and the message.
"""

import logging
import queue
import threading
import uuid
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor, as_completed

from app.db.database import SessionLocal
from app.graphrag import pipelines
from app.graphrag.bridge import MIN_CONTAINMENT, containment, shingles
from app.graphrag.params import Pipeline, RetrievalParams
from app.graphrag.store import GraphRagStore
from app.services.scope import Scope

log = logging.getLogger(__name__)


def evidence_overlap(chunk_ids: dict[str, list[str]]) -> dict:
    """Which final chunks each pipeline cited, and which they share.

    For every pair of pipelines: ids only in the first, only in the second,
    in both, and the Jaccard overlap. A low overlap with a good graph answer is
    the graph finding evidence the vector search could not.
    """
    keys = [k for k, v in chunk_ids.items() if v is not None]
    pairs = []
    for i, a in enumerate(keys):
        for b in keys[i + 1 :]:
            sa, sb = set(chunk_ids[a]), set(chunk_ids[b])
            union = sa | sb
            pairs.append(
                {
                    "a": a, "b": b,
                    "only_a": sorted(sa - sb), "only_b": sorted(sb - sa), "both": sorted(sa & sb),
                    "jaccard": round(len(sa & sb) / len(union), 4) if union else 0.0,
                }
            )
    return {"pairs": pairs}


def vector_lens(
    rag_store: GraphRagStore, tenant_id, source_ids: list[str],
    vector_chunks: list[dict], entity_ids: list[str],
) -> list[str]:
    """Which of `entity_ids` the vector pipeline's chunks cover.

    A vector chunk "covers" an entity when most of its text lies inside a graph
    text unit that mentions that entity (the reverse of `bridge`: retrieval
    chunk -> graph unit -> entities). Overlaying these on the graph plot shows
    what each method saw: the entities the graph reached that no vector chunk
    touches are where graph evidence is genuinely new.
    """
    if not vector_chunks or not entity_ids or not source_ids:
        return []
    pairs = rag_store.entity_chunks(tenant_id, entity_ids, source_ids)
    unit_ids = sorted({p["chunk_id"] for p in pairs})
    units = {u["id"]: u for u in rag_store.base.chunks(tenant_id, unit_ids, source_ids)}
    unit_shingles = {uid: shingles(u["text"]) for uid, u in units.items()}
    chunk_shingles = [(c["source_id"], shingles(c["content"])) for c in vector_chunks]
    seen: set[str] = set()
    for pair in pairs:
        unit = units.get(pair["chunk_id"])
        if unit is None:
            continue
        for source_id, cs in chunk_shingles:
            if unit["source_id"] == source_id and containment(cs, unit_shingles[unit["id"]]) >= MIN_CONTAINMENT:
                seen.add(pair["entity_id"])
                break
    return sorted(seen)


def _lens_for(
    rag_store: GraphRagStore | None, tenant_id, source_ids: list[str] | None,
    results: dict[str, "pipelines.PipelineResult"],
) -> dict:
    vector = results.get("vector")
    graph = next((r for k, r in results.items() if k != "vector" and r.status == "ok"), None)
    if not (rag_store and source_ids and vector and vector.status == "ok" and graph):
        return {"entity_ids": []}
    nodes = [n["id"] for n in (graph.trace or {}).get("nodes", [])]
    try:
        return {"entity_ids": vector_lens(rag_store, tenant_id, source_ids, vector.chunks, nodes)}
    except Exception:  # noqa: BLE001 - an overlay must never fail a comparison
        log.exception("vector lens failed")
        return {"entity_ids": []}


def _run_one(
    pipeline: Pipeline, scope: Scope, rag_store: GraphRagStore, query: str, subject_id,
    tenant_id, params: RetrievalParams, compare_id: uuid.UUID,
) -> pipelines.PipelineResult:
    technique = None if pipeline == Pipeline.VECTOR else str(params.technique)
    with SessionLocal() as db:
        try:
            if pipeline == Pipeline.VECTOR:
                result = pipelines.run_vector(db, scope, query, subject_id)
            else:
                result = pipelines.run_graph(
                    db, scope, rag_store, query, subject_id, params,
                    hybrid=pipeline == Pipeline.HYBRID,
                )
        except Exception as exc:  # noqa: BLE001 - reported in the slot, not raised
            db.rollback()
            result = pipelines.failed(pipeline, technique, query, exc)
        try:
            pipelines.record_run(
                db, result, tenant_id=tenant_id, subject_id=subject_id, compare_id=compare_id
            )
        except Exception:  # noqa: BLE001 - logging a run must never lose the answer
            log.exception("could not record rag run")
            db.rollback()
    return result


def compare(
    scope: Scope, rag_store: GraphRagStore, query: str, subject_id, tenant_id,
    params: RetrievalParams, which: list[Pipeline],
    on_result: Callable[[str, pipelines.PipelineResult], None] | None = None,
    source_ids: list[str] | None = None,
) -> dict:
    compare_id = uuid.uuid4()
    results: dict[str, pipelines.PipelineResult] = {}
    with ThreadPoolExecutor(max_workers=max(1, len(which))) as pool:
        futures = {
            pool.submit(
                _run_one, p, scope, rag_store, query, subject_id, tenant_id, params, compare_id
            ): p
            for p in which
        }
        for future in as_completed(futures):
            pipeline = futures[future]
            results[str(pipeline)] = future.result()
            if on_result:
                on_result(str(pipeline), results[str(pipeline)])
    return {
        "compare_id": str(compare_id),
        "query": query,
        "results": {k: v.to_dict() for k, v in results.items()},
        "overlap": evidence_overlap(
            {k: [c["chunk_id"] for c in v.chunks] for k, v in results.items() if v.status == "ok"}
        ),
        "lens": _lens_for(rag_store, tenant_id, source_ids, results),
    }


def stream_compare(
    scope: Scope, rag_store: GraphRagStore, query: str, subject_id, tenant_id,
    params: RetrievalParams, which: list[Pipeline], source_ids: list[str] | None = None,
) -> Iterator[tuple[str, dict]]:
    """Yields ("start"|"result"|"done", payload) as the pipelines finish."""
    events: queue.Queue = queue.Queue()
    done = object()

    def work() -> None:
        try:
            final = compare(
                scope, rag_store, query, subject_id, tenant_id, params, which,
                on_result=lambda k, r: events.put(("result", {"pipeline": k, "result": r.to_dict()})),
                source_ids=source_ids,
            )
            events.put(("done", {"compare_id": final["compare_id"], "overlap": final["overlap"],
                                 "lens": final["lens"]}))
        except Exception as exc:  # noqa: BLE001
            log.exception("compare failed")
            events.put(("error", {"message": str(exc)[:300]}))
        finally:
            events.put(done)

    threading.Thread(target=work, daemon=True).start()
    yield "start", {"pipelines": [str(p) for p in which]}
    while True:
        item = events.get()
        if item is done:
            return
        yield item

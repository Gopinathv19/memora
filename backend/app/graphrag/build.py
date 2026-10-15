"""Build the Graph RAG index for a subject, on top of an existing knowledge graph.

The knowledge graph (entities, chunks, fact edges) already comes from
`app.graph.ingestion`. This adds what the five techniques need that it does not
have, deriving everything from what is already stored -- no document is read
again:

1. embeddings on every in-scope `Entity`
2. a `Fact` node (text + embedding) for every fact edge, with stale ones removed
3. communities: `algo.labelPropagation`, an LLM summary per community, embedded

It is idempotent and incremental: entities that already have an embedding and
facts that already have a node are skipped, so re-running after a new source is
built only pays for what is new.
"""

import logging
import time
from collections import defaultdict

from app.core.config import get_settings
from app.graphrag import embedder
from app.graphrag.store import GraphRagStore, fact_id, fact_text
from app.llm.client import LLMError, get_llm_client

log = logging.getLogger(__name__)

ENTITY_BATCH = 200
MIN_COMMUNITY_SIZE = 3
MAX_COMMUNITIES = 12
MAX_MEMBERS_IN_PROMPT = 25
MAX_FACTS_IN_PROMPT = 30

_COMMUNITY_SYSTEM = (
    "You summarize a cluster of related entities from a knowledge graph. "
    "Return JSON: {\"title\": \"3-6 words\", \"summary\": \"2-4 sentences\"}. "
    "Use only the entities and facts given. Say what the cluster is about, who or what is "
    "involved and how they relate. Do not invent anything."
)


def entity_text(row: dict) -> str:
    descs = [d for d in (row.get("descs") or []) if d]
    parts = [f"{row['name']} ({row['entity_type']})"]
    aliases = [a for a in (row.get("aliases") or []) if a and a != row["name"]][:3]
    if aliases:
        parts.append("also known as " + ", ".join(aliases))
    if descs:
        parts.append(descs[0])
    return ". ".join(parts)


def build_entities(store: GraphRagStore, tenant_id, source_ids, cfg, stats: dict) -> None:
    while True:
        rows = store.entities_missing_embedding(tenant_id, source_ids, ENTITY_BATCH)
        if not rows:
            return
        texts = [entity_text(r) for r in rows]
        vectors, tokens = embedder.embed_documents(cfg, texts)
        store.set_entity_embeddings(
            tenant_id,
            [{"id": r["id"], "embedding": v, "text": t} for r, v, t in zip(rows, vectors, texts)],
        )
        stats["entities_embedded"] += len(rows)
        stats["embedding_tokens"] += tokens


def build_facts(store: GraphRagStore, tenant_id, source_ids, cfg, stats: dict) -> None:
    edges = store.scope_facts(tenant_id, source_ids)
    wanted: dict[str, dict] = {}
    for e in edges:
        fid = fact_id(e["source_id"], e["source_entity_id"], e["relation"], e["target_entity_id"])
        wanted[fid] = {
            "id": fid, "source_id": e["source_id"], "relation": e["relation"],
            "a": e["source_entity_id"], "b": e["target_entity_id"],
            "text": fact_text(e["source_name"], e["relation"], e["target_name"], e.get("description")),
            "chunk_ids": list(e.get("chunk_ids") or []), "confidence": e.get("confidence"),
        }
    stats["facts_removed"] += store.prune_orphan_facts(tenant_id)
    existing = store.existing_fact_ids(tenant_id, source_ids)
    stats["facts_removed"] += store.delete_facts(tenant_id, sorted(existing - set(wanted)))
    fresh = [wanted[f] for f in wanted if f not in existing]
    for start in range(0, len(fresh), ENTITY_BATCH):
        batch = fresh[start : start + ENTITY_BATCH]
        vectors, tokens = embedder.embed_documents(cfg, [r["text"] for r in batch])
        store.upsert_facts(tenant_id, [{**r, "embedding": v} for r, v in zip(batch, vectors)])
        stats["facts_embedded"] += len(batch)
        stats["embedding_tokens"] += tokens


def summarize_community(members: list[dict], facts: list[dict]) -> tuple[dict | None, dict | None]:
    model = get_settings().llm_graph_model
    lines = [f"- {m['name']} ({m['entity_type']})" for m in members[:MAX_MEMBERS_IN_PROMPT]]
    fact_lines = [
        f"- {f['source_name']} {f['relation']} {f['target_name']}"
        + (f": {f['description']}" if f.get("description") else "")
        for f in facts[:MAX_FACTS_IN_PROMPT]
    ]
    user = "Entities:\n" + "\n".join(lines) + "\n\nFacts:\n" + ("\n".join(fact_lines) or "(none)")
    try:
        data, usage = get_llm_client().chat_json(model, _COMMUNITY_SYSTEM, user)
    except LLMError as exc:
        log.warning("community summary failed: %s", exc)
        return None, None
    title, summary = str(data.get("title") or "").strip(), str(data.get("summary") or "").strip()
    record = {
        "model": model, "prompt": usage.prompt_tokens, "completion": usage.completion_tokens,
        "purpose": "community summary",
    }
    return ({"title": title, "summary": summary} if title and summary else None), record


def build_communities(store: GraphRagStore, tenant_id, subject_id, source_ids, cfg, stats: dict) -> None:
    in_scope = set(store.scope_entity_ids(tenant_id, source_ids))
    groups: dict[int, list[str]] = defaultdict(list)
    for row in store.label_propagation(tenant_id):
        if row["id"] in in_scope:
            groups[row["community"]].append(row["id"])
    big = sorted(
        (ids for ids in groups.values() if len(ids) >= MIN_COMMUNITY_SIZE),
        key=lambda ids: -len(ids),
    )[:MAX_COMMUNITIES]
    rows: list[dict] = []
    for ids in big:
        info = store.entities_info(tenant_id, ids)
        members = [info[i] for i in ids if i in info]
        facts = store.facts_among(tenant_id, ids, source_ids, MAX_FACTS_IN_PROMPT)
        summary, usage = summarize_community(members, facts)
        if usage:
            stats["llm_usage"].append(usage)
        if summary is None:
            continue
        rows.append(
            {
                "id": f"{subject_id}:{min(ids)}", "title": summary["title"],
                "summary": summary["summary"], "size": len(ids), "members": ids,
            }
        )
    if rows:
        vectors, tokens = embedder.embed_documents(
            cfg, [f"{r['title']}. {r['summary']}" for r in rows]
        )
        stats["embedding_tokens"] += tokens
        for r, v in zip(rows, vectors):
            r["embedding"] = v
    store.replace_communities(tenant_id, str(subject_id), rows)
    stats["communities"] = len(rows)


def build_index(
    store: GraphRagStore, tenant_id, subject_id, source_ids: list[str], cfg,
    *, communities: bool = True,
) -> dict:
    """Returns counts, timings and the LLM calls made (for the build-cost card)."""
    stats: dict = {
        "entities_embedded": 0, "facts_embedded": 0, "facts_removed": 0,
        "communities": 0, "embedding_tokens": 0, "llm_usage": [], "timings_ms": {},
    }

    def timed(name, fn, *args):
        t0 = time.monotonic()
        fn(*args)
        stats["timings_ms"][name] = int((time.monotonic() - t0) * 1000)

    store.ensure(tenant_id, cfg.dimension)
    timed("entities", build_entities, store, tenant_id, source_ids, cfg, stats)
    timed("facts", build_facts, store, tenant_id, source_ids, cfg, stats)
    if communities:
        timed("communities", build_communities, store, tenant_id, subject_id, source_ids, cfg, stats)
    stats["coverage"] = store.stats(tenant_id, source_ids)
    return stats

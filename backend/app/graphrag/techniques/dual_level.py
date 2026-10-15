"""Technique 3: LightRAG-style dual-level retrieval.

An LLM splits the question into two kinds of keywords:

* **local**  entity names ("Acme Corp", "Q3 audit")  -> matched against
             `Entity.embedding` plus exact keys: *specific* knowledge
* **global** themes and relations ("supplier risk", "who approves budgets")
             -> matched against `Fact.embedding`, the reified relationship
             text: *abstract* knowledge that no single entity name captures

Entities from the local level are expanded one hop; facts from the global level
bring their endpoint entities. Both feed the same evidence builder. If the
keyword call fails the question itself is used for both levels, so the
technique degrades to plain entity + fact vector search instead of failing.
"""

import logging

from app.core.config import get_settings
from app.graphrag import embedder
from app.graphrag.seeds import gather_seeds
from app.graphrag.techniques.common import Ctx, Timer, evidence_from_scores
from app.graphrag.types import GraphEvidence, ScoredFact
from app.llm.client import LLMError, get_llm_client

log = logging.getLogger(__name__)

LOCAL_K = 4
GLOBAL_K = 10
EXPAND_FACTS = 30

_SYSTEM = (
    "You split a question into search keywords for a knowledge graph. "
    "Return JSON: {\"local\": [...], \"global\": [...]}. "
    "\"local\" holds specific entity names (people, organizations, documents, products, "
    "identifiers) that appear in the question, at most 5. "
    "\"global\" holds 1-3 short phrases naming the relationship or theme the question is about "
    "(for example \"supplier relationships\" or \"compliance findings\"). "
    "Use words from the question; do not answer it."
)


def extract_keywords(query: str) -> tuple[list[str], list[str], dict | None]:
    settings = get_settings()
    model = settings.llm_graph_model
    try:
        data, usage = get_llm_client().chat_json(model, _SYSTEM, f"Question: {query}")
    except LLMError as exc:
        log.warning("dual-level keyword extraction failed: %s", exc)
        return [query], [query], None

    def clean(value) -> list[str]:
        if not isinstance(value, list):
            return []
        return [str(v).strip() for v in value if str(v).strip()][:5]

    local, global_ = clean(data.get("local")), clean(data.get("global"))
    record = {
        "model": model, "prompt": usage.prompt_tokens, "completion": usage.completion_tokens,
        "purpose": "keyword extraction",
    }
    return local or [query], global_ or [query], record


def run(ctx: Ctx) -> GraphEvidence:
    p = ctx.params
    evidence = GraphEvidence(technique="dual_level")
    timer = Timer(evidence)
    store, tenant, sids = ctx.store, ctx.tenant_id, ctx.source_ids

    with timer("keywords"):
        local, global_, usage = extract_keywords(ctx.query)
    if usage:
        evidence.llm_usage.append(usage)
    evidence.notes.append(f"local keywords: {local}; global keywords: {global_}")

    entity_scores: dict[str, float] = {}
    names: dict[str, dict] = {}
    hops: dict[str, int] = {}

    with timer("local_level"):
        seeds, counts = gather_seeds(
            store, tenant, sids, " ".join(local),
            embedder.embed_query(ctx.embedding_cfg, " ".join(local)) if ctx.embedding_cfg else ctx.query_vector,
            p, extra_keys=local,
        )
    evidence.seeds = seeds
    evidence.seed_counts = counts
    evidence.notes.append(f"local seed signals: {counts}")
    for s in seeds:
        entity_scores[s.id] = s.score
        names[s.id] = {"name": s.name, "entity_type": s.entity_type}
        hops[s.id] = 0

    extra_facts: list[ScoredFact] = []
    with timer("expand"):
        if seeds:
            for row in store.neighbourhood(tenant, [s.id for s in seeds], sids, EXPAND_FACTS):
                for end in ("source_entity_id", "target_entity_id"):
                    other = row[end]
                    if other in entity_scores:
                        continue
                    seed_weight = max(
                        entity_scores.get(row["source_entity_id"], 0.0),
                        entity_scores.get(row["target_entity_id"], 0.0),
                    )
                    conf = row.get("confidence")
                    entity_scores[other] = seed_weight * p.decay * (conf if conf is not None else 0.6)
                    names[other] = {
                        "name": row["source_name"] if end == "source_entity_id" else row["target_name"],
                        "entity_type": "CONCEPT",
                    }
                    hops[other] = 1

    with timer("global_level"):
        gvec = embedder.embed_query(ctx.embedding_cfg, " ".join(global_)) if ctx.embedding_cfg else ctx.query_vector
        fact_rows = store.vector_facts(tenant, gvec, sids, GLOBAL_K) if gvec else []
    for row in fact_rows:
        sim = float(row["similarity"])
        for eid, name in ((row["a_id"], row["a_name"]), (row["b_id"], row["b_name"])):
            entity_scores[eid] = max(entity_scores.get(eid, 0.0), sim * 0.8)
            names.setdefault(eid, {"name": name, "entity_type": "CONCEPT"})
            hops.setdefault(eid, 1)
        extra_facts.append(
            ScoredFact(
                source_entity_id=row["a_id"], source_name=row["a_name"],
                relation=row["relation"],
                target_entity_id=row["b_id"], target_name=row["b_name"],
                description=row["text"], confidence=row.get("confidence"),
                source_id=row["source_id"], chunk_ids=list(row.get("chunk_ids") or []),
                score=round(sim, 5),
            )
        )
    if not entity_scores:
        evidence.notes.append("neither level matched anything")
        return evidence

    info = store.entities_info(tenant, [e for e, n in names.items() if n["entity_type"] == "CONCEPT"])
    for eid, row in info.items():
        names[eid] = {"name": row["name"], "entity_type": row["entity_type"]}
    return evidence_from_scores(ctx, evidence, entity_scores, hops, names, extra_facts=extra_facts)

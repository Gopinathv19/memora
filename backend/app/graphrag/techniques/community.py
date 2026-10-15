"""Technique 5: community retrieval for broad, cross-document questions
(Microsoft GraphRAG / ArchRAG idea).

At build time (`app.graphrag.build`) FalkorDB's `algo.labelPropagation` groups
entities into communities, an LLM writes a short summary of each, and the
summary is embedded on a `Community` node. At query time the question is
matched against the summaries, the matching communities' members and facts are
fetched, and the summaries themselves go into the answer context -- so a
question like "what are the main themes across these documents?" is answered
from synthesized knowledge instead of from whichever 6 chunks scored highest.

It is the weakest technique for exact facts (a summary blurs them), which is
what the Benchmarks page is for. With no communities built it falls back to
spreading activation and says so.
"""

from app.graphrag.techniques import spreading
from app.graphrag.techniques.common import Ctx, Timer, evidence_from_scores
from app.graphrag.types import GraphEvidence

COMMUNITY_K = 3


def run(ctx: Ctx) -> GraphEvidence:
    p = ctx.params
    evidence = GraphEvidence(technique="community")
    timer = Timer(evidence)
    store, tenant = ctx.store, ctx.tenant_id

    with timer("community_search"):
        rows = (
            store.vector_communities(tenant, ctx.query_vector, [ctx.subject_id], COMMUNITY_K)
            if ctx.query_vector
            else []
        )
    if not rows:
        evidence.notes.append("no communities built for this subject: used spreading activation")
        fallback = spreading.run(ctx)
        fallback.technique = "community"
        fallback.notes = evidence.notes + fallback.notes
        return fallback

    members = store.community_members(tenant, [r["id"] for r in rows])
    by_community: dict[str, list[dict]] = {}
    for m in members:
        by_community.setdefault(m["community_id"], []).append(m)

    entity_scores: dict[str, float] = {}
    names: dict[str, dict] = {}
    hops: dict[str, int] = {}
    for row in rows:
        sim = float(row["similarity"])
        member_rows = by_community.get(row["id"], [])
        evidence.communities.append(
            {
                "id": row["id"], "title": row["title"], "summary": row["summary"],
                "size": row["size"], "similarity": round(sim, 4),
                "member_ids": [m["id"] for m in member_rows],
            }
        )
        evidence.context.append(f"Community \"{row['title']}\": {row['summary']}")
        for m in member_rows:
            entity_scores[m["id"]] = max(entity_scores.get(m["id"], 0.0), sim)
            names[m["id"]] = {"name": m["name"], "entity_type": m["entity_type"]}
            hops[m["id"]] = 0
    evidence.notes.append(f"matched {len(rows)} communities, {len(entity_scores)} member entities")
    return evidence_from_scores(ctx, evidence, entity_scores, hops, names)



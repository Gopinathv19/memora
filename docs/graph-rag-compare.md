# Graph RAG service and the Compare dashboard

Graph RAG runs as its own service next to the vector pipeline (`POST /query`), and the
console's **Graph RAG** section measures the two on the same questions so a user can see
which one suits their workload. Code: `backend/app/graphrag/`, routes in
`backend/app/api/routes/graphrag.py`, UI in `console/src/app/compare/`.

## Pipelines

| key      | what it does                                                                          |
|----------|---------------------------------------------------------------------------------------|
| `vector` | the existing pipeline, unchanged (dense + lexical, RRF, MMR, reranker, LLM)           |
| `graph`  | one technique finds evidence in FalkorDB, then the same reranker and answer LLM      |
| `hybrid` | vector and graph candidates fused by RRF, then the same MMR, reranker and LLM         |

All three return `retrieval_chunks`, so Recall@k against an eval set's `supporting_chunk_ids`
means the same for each. Graph text units (`{source}:{version}:{index}`, 3000 chars) are
mapped onto retrieval chunks by text containment in `graphrag/bridge.py`.
`graph` and `hybrid` put a short list of knowledge-graph facts (and community summaries)
before the chunks in the answer prompt; `graph` skips MMR.

## Techniques (`graphrag/techniques/`)

1. **spreading**: multi-seed spreading activation with hop decay, scored in one Cypher statement.
2. **ppr**: personalized PageRank over the question's neighbourhood and the text that mentions it.
3. **dual_level**: LightRAG style; entity keywords against `Entity.embedding`, themes against `Fact.embedding`.
4. **paths**: `allShortestPaths` between seed entities over fact edges, returned as explainable chains.
5. **community**: question against LLM summaries of `algo.labelPropagation` communities.

Seeds (`seeds.py`) come from the entity vector index, exact keys and the full-text index.
Paths run through entities only; `MENTIONS` edges are excluded or two entities that merely
share a text unit would count as connected.

## Index

The graph itself comes from `app.graph` (extraction -> entities, chunks, fact edges). Graph RAG
adds, derived from what is stored (no document is read again): embeddings on `Entity`, a
`Fact` node per fact edge (relationship types are dynamic, and FalkorDB's relationship vector
index needs one fixed type), `Community` nodes, vector indexes, and uniqueness constraints.

    POST /subjects/{id}/graph-rag/index     idempotent and incremental
    GET  /subjects/{id}/graph-rag/status

## API

    POST /graph-rag/query            POST /compare/query       POST /compare/stream (SSE)
    GET  /compare/runs               POST /graph-rag/expand
    POST|GET /compare/benchmarks[/{id}]

Runs are stored in `rag_runs` and benchmarks in `benchmark_runs` (migration `9c3e5a7b1d20`).

## Knowing when nothing relevant was found

Every result carries `evidence`: `best_rerank` (the reranker's score for its best chunk),
`weak` (below `WEAK_EVIDENCE_LOGIT`, -5.0, or nothing retrieved) and, for graph pipelines,
`anchored` (a name in the question matched an entity by exact key or full-text). The
threshold is measured on this project's corpus: questions the documents cannot answer score
-5.7 or lower, ones they can mostly score above -3. Embedding cosine cannot make this call
(0.58-0.70 for no match, 0.63-0.87 for a match), so entity-vector seeds are capped at half the
weight of a name match and never scaled up to a perfect score. The Arena shows a "Weak
evidence" callout and an "unanchored" note, and `GET /subjects/{id}/graph-rag/status` lists
`files_without_graph`: Graph RAG only knows files that have a knowledge graph, so a file
without one is invisible to it while vector RAG still answers from it.

## Source inspector

`GET /sources/{id}/graph/inspect` and the console's **Graph RAG > Source inspector** show one
file's own graph beside the passages it was read from: every passage with the entities and
facts it produced (marked in its text and outlined in the graph), an entity panel (what was
said about it, which passages mention it), the latest build's stats, and checks for a weak
extraction (passages that produced nothing, entities with no relationships, facts below 50%
confidence, passages the model failed to read). It only shows what that file contributed;
entities other files also mention appear from this file's side only. Each file's page links
to it from the Knowledge graph panel.

## Tests

They need only a FalkorDB (no Postgres, network or LLM):

    docker run -d -p 6390:6379 falkordb/falkordb:latest
    cd backend && TEST_FALKORDB_URL=redis://localhost:6390 \
      .venv/bin/python -m pytest --noconftest tests/test_graphrag_techniques.py tests/test_graphrag_pipeline.py

## Known limits

- `allShortestPaths` is resolved before the scope filter, so a shortest path crossing an
  out-of-scope edge is dropped instead of replaced by a longer in-scope one.
- Communities come from a tenant-wide `algo.labelPropagation` filtered to the subject, and
  label propagation can oscillate on very small graphs.
- Index build progress is kept per process (the index itself lives in FalkorDB).
- "Explainability" in the Advisor is a property of the method, not a measurement.

## Running it locally

FalkorDB Cloud listens on a custom port, which some networks block (a corporate egress
firewall that only lets 443 and 587 out times out silently). For development use a local
container and point `FALKORDB_URL` at it:

    docker run -d --name memora-falkordb-dev -p 6390:6379 falkordb/falkordb:latest
    # backend/.env
    FALKORDB_URL=redis://localhost:6390

The knowledge graph is built per source (`POST /sources/{id}/graph`), then the Graph RAG
index per subject (`POST /subjects/{id}/graph-rag/index`, or the **Build graph index**
button on any Graph RAG page). Graphs live in whichever FalkorDB `FALKORDB_URL` names, so
switching back to the cloud instance means rebuilding them there.

"""Graph RAG as its own service, next to the vector pipeline.

`app.services.retrieval_service` answers a question from Postgres (dense +
lexical, RRF, MMR, reranker). This package answers the same question from the
FalkorDB knowledge graph, using one of five techniques (`techniques/`), and
`compare` runs both on one question so their evidence, latency and cost can be
put side by side.

Everything that scores, ranks or traverses runs inside FalkorDB (Cypher,
vector indexes, `algo.*`); Python only orchestrates and, for personalized
PageRank, iterates over the small subgraph FalkorDB returns.
"""

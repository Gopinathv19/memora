# Unified Graph RAG: build, retrieval, answers, operations

Status: **built and tested, not committed or switched on** (2026-10-17). It sits beside the first Graph RAG
([graph-rag.md](graph-rag.md), [graph-rag-compare.md](graph-rag-compare.md)), which is unchanged and still the
fallback. Nothing in the vector pipeline was edited. All new code is in `backend/app/graphrag/`.

The point of the rebuild: the first graph was built from its own 3000-character text units and linked back to
retrieval chunks by fuzzy text overlap. The unified build reads the **same chunks retrieval uses**, so every fact
points at a real `retrieval_chunks` row, and an answer can cite it exactly.

---

## 1. The build

```text
retrieval_chunks / semantic_blocks   (read-only)
   │  windows.py        pack consecutive blocks of one document into ~1000-token windows
   ▼
extract.py              one model call per window: entities, relations, atomic facts, each with a
                        verbatim evidence quote; items whose quote is not in the text are dropped
   ▼
resolve.py              exact, acronym, then embedding-found pairs judged by a model and confirmed by a
                        second stricter reviewer; guards on digits, variants and person names
   ▼
graph_write.py          documents, chunks (their own ids), entities, typed edges, atomic facts, merge records
   ▼
ingest.py               embeds entities and atomic facts          community_build.py   hierarchical communities
```

**Windows.** On the measured corpus the median semantic block was 60 tokens (79% under 100), so one call per block
would see a heading and a sentence. Blocks are packed into windows; a block over 1800 tokens is split at chunk
boundaries; a window never crosses documents. Each window keeps its exact chunk ids.

**Extraction.** The ontology (`ontology.json`) still constrains types; a model may propose `OTHER:<LABEL>`, which is
stored under the fallback and counted so frequent ones can be promoted. A relation or fact needs a quote found
verbatim (case, whitespace, markdown marks, bullets and hyphens are ignored) in one chunk or two adjacent ones. A
second "what did you miss" call runs only when a window has many entities but few relations and facts.

**Resolution.** Never merges without a reason. Guards run first and cannot be overridden: names with different
digits (`Project Alpha 1` / `2`), different variant words (`GPT-4o` / `GPT-4o-mini`), incompatible person names.
Union-find refuses a union that would put a guarded pair in one node. Every merge is recorded as a `Merge` node
with the sources that justified it.

**Graph additions** (the old labels and edges are unchanged, so the five old techniques still read the new data):

| Node / edge | Meaning |
|---|---|
| `Chunk {id = retrieval chunk id, kind: 'retrieval'}` | the chunk itself, not a copy of its text units |
| `Fact {kind: 'atom'}` `-[:SUPPORTED_BY]->` `Chunk`, `-[:ABOUT]->` `Entity` | one self-contained sentence with its evidence quote and qualifiers |
| `Merge {merged_id, survivor_id, stage, source_ids, names, keys}` | why two names became one node |
| `Community {kind: 'hier', level, parent_id, stale}` `-[:HAS_MEMBER]->` `Entity` | hierarchical communities (pure-Python Louvain, connected, stable ids) |
| `Document {signature, chunk_fingerprint}` | what built it, and a hash of its active chunk ids |

FalkorDB 4.22 has no Leiden, and the backend has no numpy/networkx/igraph, so `communities.py` is a small
deterministic Louvain with a connectedness repair. Summaries are written eagerly for the top levels and lazily
(`ensure_summary`) for the rest; a community whose members overlap an old one by 0.8 or more keeps its summary.

## 2. Retrieval: the `unified` pipeline

Reached only through `/compare/*` and `POST /graph-rag/query` with `pipeline: "unified"`; `/query` is untouched.

1. The vector pipeline's own candidate search runs (read-only reuse).
2. The question analyser (`analyse.py`, one cached model call, rules fallback) gives a kind (lookup, relational,
   corpus) and the names mentioned. It only sets channel **weights**.
3. Three channels, fused by weighted reciprocal rank fusion (`retrieve.py`): the vector list; the chunks supporting
   the nearest **atomic facts**; a personalized PageRank walk over entities *and* chunks, restarting at the entities
   those facts are about, the names in the question, and the chunks dense search liked.
4. The shared reranker picks the final chunks. If the best score is below the weak-evidence line (-5.0) it widens
   3x once; if still weak it **abstains** and lists the closest passages instead of answering.

Seeding from atomic facts, not entity names, is deliberate: on the measured corpus the nearest *entities* to a
question were poor (cosine about 0.70, off topic) while the nearest *facts* were on topic.

## 3. The answer step (`answer.py`)

The model sees numbered evidence: `[C1]` passages (document, section path, page), `[F1]` facts (each shown only with
a passage that states it; at most two passages are pulled in to back facts), `[G1]` community overviews ("context
only, not proof"). It must put a marker after each sentence. The reply is parsed into `citations`, and checked,
never rewritten: unknown markers, uncited sentences, sentences supported only by an overview, and numbers or dates
the cited evidence does not contain. An optional second model call (`answer_llm_check`) checks each claim.

## 4. Operations

* **Runs.** `POST /subjects/{id}/graph-rag/build` plans what is new, changed or removed and rebuilds only that, then
  rebuilds communities. State is in Postgres (`graph_rag_index_runs`), not in process memory. `GET .../runs`,
  `GET /graph-rag/runs/{id}`.
* **One run per subject.** A partial unique index on active runs is the lease, so it holds across workers and behind a
  transaction pooler (a session advisory lock would not). A run that stops reporting for
  `GRAPHRAG_RUN_LEASE_SECONDS` stops blocking its subject.
* **Plan.** per source: `new`, `changed` (the graph's chunk-id fingerprint differs: re-extract, re-chunk, re-import),
  `outdated` (built with other schema/prompt/ontology/judge versions; rebuilt only with `rebuild_outdated`),
  `orphan` (in the graph, gone from Postgres: removed), `requested`.
* **Job.** Kind `graph_rag_index` in `job_service` (durable mode) or a background task.
* **Deleting a source** reaches the graph through hooks on `FalkorGraphStore.delete_source`: atomic facts are removed,
  merges only that source justified are unwound (record and aliases, and the display name falls back to a name a
  remaining source uses), touched communities are marked stale (never served, not reused) and communities left empty
  are pruned. A rebuild goes through the same path.
* **Cost cap.** `GRAPHRAG_TENANT_DAILY_COST_CAP_USD` (0 = off). It meters extraction calls only: community summaries
  and the entity judge are not metered.
* **Drive.** `drive_resync.py` detects imported files that changed or vanished in Drive. It does not import or delete:
  a re-import creates a **new** source and leaves the old one, so the subject then holds both versions.

## 5. Settings added

`GRAPHRAG_JUDGE_MODEL` (benchmark judge; empty = the answer model, flagged `judge_same_as_answer`),
`GRAPHRAG_RUN_LEASE_SECONDS`, `GRAPHRAG_TENANT_DAILY_COST_CAP_USD`, `GRAPHRAG_BUILD_CONCURRENCY`. Per-request knobs
are in `GraphRagParams` (`unified_analyser`, `unified_weights`, `unified_widen`, `unified_multi_hop`, `atom_k`,
`walk_passage_weight`, `answer_llm_check`).

## 6. Evaluating it

* `evals/scripts/generate_graph_eval_set.py` (lookup and relational), `generate_graph_eval_extras.py` (corpus-wide and
  unanswerable). **Always pass `--out`**: the first script's default path is a file you may already have.
* `evals/scripts/lexical_solvability.py`: how many questions plain BM25 already solves.
* `evals/scripts/offline_graph_benchmark.py`: vector against unified, retrieval only, records nothing in Postgres.
* `POST /compare/benchmarks` with `include_unified` adds the unified pipeline and the citation, verified and
  unsupported-number rates (this one writes run rows).
* `evals/scripts/judge_agreement.py`: score the benchmark's judge against your own grades.

## 7. Testing

Throwaway containers, never the configured databases:

```bash
docker run -d --name memora-falkordb-test -p 6390:6379 falkordb/falkordb
docker run -d --name memora-postgres-test -e POSTGRES_USER=memora -e POSTGRES_PASSWORD=memora \
  -e POSTGRES_DB=memora -p 55432:5432 pgvector/pgvector:pg16
cd backend && DATABASE_URL=postgresql+psycopg://memora:memora@localhost:55432/memora .venv/bin/alembic upgrade head
TEST_FALKORDB_URL=redis://localhost:6390 .venv/bin/python -m pytest tests -q
```

With all of this in place: 654 tests pass and 5 fail. The same 5 (`test_chunking.py` x4,
`test_graph_api.py::test_upload_with_build_graph_extracts_then_builds`) fail on the last commit without any of it.
Without `--noconftest` the repo's fixtures isolate the database; with `--noconftest`, set `LLM_CACHE_ENABLED=false`
or the model cache writes to whatever `DATABASE_URL` is.

## 8. Measured so far, and limits

Measured on one small corpus (15 sources, 231 chunks, 60 windows); treat as indications.

* Extraction: 79-88% of relation and fact items passed the verbatim-quote check; about 15% run-to-run spread in what
  is kept, because the model is not repeatable at temperature 0.
* Resolution: the first version made absurd merges (YAML with ZCQL); a name-affinity prefilter, a variant guard and a
  second reviewer removed them. No labelled set exists, so precision and recall are unmeasured.
* Retrieval: on lookup-style questions the unified pipeline returned almost the same chunks as vector search. Whether
  it helps on relational and corpus-wide questions is what the benchmark is for.
* Answers: all five answerable test questions passed the checks in the last run, but 3 of 5 in the run before:
  citation compliance varies.
* Not metered: community summaries and the entity judge. Not built: automatic handling of superseded Drive sources.

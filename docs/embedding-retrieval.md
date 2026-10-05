# Embeddings & retrieval — how stored knowledge becomes answers

The vector pipeline: what happens between *chunks exist* and *a question is
answered*. Written for developers and clients using Memora — the concepts, the
strategy, and the reasoning, without requiring you to read the code. For the
tables each stage writes see [data-flow.md](data-flow.md); for the HTTP
surface see [api-reference.md](api-reference.md).

---

## 1. The big picture

Two phases, one direction:

```text
INGESTION (per source, runs in the background)
  chunks ──► embedding model ──► vectors in PostgreSQL (pgvector, HNSW index)

QUERY (per question, synchronous)
  question ──► vector search ──► diversify ──► rerank ──► grounded answer
```

Embedding turns each chunk into a vector — a long list of numbers that
captures the *meaning* of the text. Retrieval turns a question into a vector
too, finds the chunks whose vectors are closest to it, and hands the best ones
to a language model that writes the answer using only that evidence.

The whole chain is scoped: a query can only ever see chunks the caller owns.

---

## 2. Embeddings, in plain terms

**What an embedding is.** An embedding model reads a piece of text and outputs
a fixed-length vector (2048 numbers for the current model). Texts with similar
meaning land close together in that vector space, so *"invoice due date"* and
*"payment deadline"* are near each other even though they share no words.
Comparing vectors — a cheap arithmetic operation — then stands in for
comparing meaning.

**The provider.** Memora talks to an NVIDIA NIM embeddings endpoint through
the standard OpenAI-compatible `/v1/embeddings` API. The same code serves
build.nvidia.com (development), a Nebius-hosted NIM (production), or a local
NIM container — switching between them is a settings change
(`NVIDIA_NIM_BASE_URL`), never a code change.

**A model is not a strategy.** This distinction is the heart of the design:

| Concept | What it is | Example |
|---|---|---|
| **Model** | An actual embedding model that exists at a provider, with its physical properties (dimension, normalization, similarity metric). | `nvidia/nemotron-3-embed-1b` — 2048 dimensions, L2-normalized, cosine similarity |
| **Strategy** | How Memora *uses* a model: which chunk field is the input, what text templates wrap it, and the metric. | "Memora Dense v1" — embed `embedding_text` as-is, no template |

The default strategy ("Memora Dense v1") embeds the chunk's
`embedding_text` — the chunk content prefixed with its section path, which
the chunking stage already prepared — with no additional template. A future
model that needs `query: …` / `passage: …` prefixes would get a strategy whose
templates add them; the code never changes.

**Why strategies are immutable.** Everything that determines the vector —
model, templates, normalization, metric — is fixed at creation. Only the
description and the active flag can be edited afterwards. A materially
different configuration is a *new strategy row*, never an edit of an existing
one. This is what keeps history honest: every stored vector knows exactly
which strategy produced it, and a model change can never silently corrupt
existing results.

**Idempotency and cost.** Before embedding, the worker computes a hash of
*strategy + model + the exact text sent to the model*. Re-running over
unchanged content produces the same hash and is a no-op — no model call, no
credits. Changed content, a changed strategy, or a changed model version
produces a new hash, and therefore a new embedding row. Re-embedding a corpus
only ever pays for what actually changed.

**Statuses.** Each chunk's embedding row moves through
`pending → processing → completed` (or `failed`, with the error kept and a
bounded retry count). Coverage stats and per-source health are exposed by the
API so you can see what is searchable at any moment.

---

## 3. Retrieval, in plain terms

One question travels through four stages. Each stage exists to fix a specific
weakness of the one before it:

```text
question
  │ 1. embed the question (same strategy as the stored vectors)
  ▼
HNSW vector search ──► top 50 candidates        fast, approximate, broad
  │ 2. Maximal Marginal Relevance
  ▼
MMR diversification ──► top 10 candidates      removes near-duplicates
  │ 3. cross-encoder
  ▼
Reranking ──► top 5 chunks                     precise, query-aware scoring
  │ 4. language model
  ▼
Grounded answer + evidence                     cites the chunks it used
```

**Stage 1 — vector search (HNSW).** The question is embedded under the same
strategy as the stored vectors (a question and its answer must live in the
same vector space). An HNSW index in pgvector then finds the ~50 nearest
chunks by cosine similarity. This search is *approximate* — it trades a tiny
amount of accuracy for speed that stays constant as the corpus grows. That is
deliberate: the next stages refine the pool, so the first stage only needs to
be fast and broad, not perfect. The search is scoped inside the SQL itself:
only chunks in the caller's tenant (and application, subject or source if
narrowed) are ever candidates.

**Stage 2 — diversification (MMR).** Vector search has a known failure mode:
the top results are often five copies of the same paragraph. Maximal Marginal
Relevance re-selects from the 50 by balancing two terms — *relevance to the
question* against *similarity to what is already selected*. A chunk that says
the same thing as an already-picked chunk loses ground even when it is
individually relevant. The result is 10 candidates that are both relevant and
*different*. This stage is pure arithmetic over the stored vectors — no model
calls, no cost.

**Stage 3 — reranking (cross-encoder).** Cosine similarity compares two
vectors that were computed *independently* — the question never saw the chunk.
A cross-encoder reads the question and the chunk *together* and scores how
well the chunk actually answers it, which is strictly more accurate. A
dedicated reranking model (NVIDIA's NeMo Retriever) scores every (question,
chunk) pair from stage 2, and the final top 5 comes from *this* score, never
from the cosine similarity. If the reranking service is down, the pipeline
falls back to the MMR order rather than failing the question — the reranker is
an enhancement, not a dependency.

**Stage 4 — grounded answer.** The 5 surviving chunks become the context for
the answering model. It is instructed to use *only* that context: every claim
must be supported by a chunk, and if the knowledge does not contain the
answer it must say so rather than invent one. Chunks are treated as evidence,
never as instructions — a document that says "ignore previous instructions"
cannot hijack the answer. Each chunk carries its section path and page range,
so answers can point back to where they came from.

**When nothing is embedded yet**, the response says so plainly instead of
guessing: "I could not find any information about that in the available
knowledge."

---

## 4. Scope and isolation

Retrieval is read-only — a query writes nothing. What it *reads* is decided
by the caller's scope, enforced in the search SQL itself:

- **A console user** queries every tenant they own.
- **An API credential** queries its own application only.
- Optional `subject_id` / `source_id` narrow the search further; a subject or
  source outside the scope is a 404, not an empty result — the same
  "outside scope does not exist" rule the rest of the API follows.

There is no way to ask the retrieval pipeline an unscoped question.

---

## 5. Pluggability: swapping models and providers

Nothing in the pipeline is hard-wired to a single vendor or model. The design
separates *what a stage needs* (a small interface) from *who provides it* (a
registered implementation), so future changes are configuration or data, not
surgery. Five extension points follow this pattern:

| Extension point | What it decides | How to swap |
|---|---|---|
| **Embedding strategy** | Which model, which input, which templates, which metric | Data: create a new strategy row (see below) |
| **Embedding provider** | Which service serves the vectors | Settings: `EMBEDDING_PROVIDER` + base URL; a new provider is one class implementing the interface |
| **Reranker** | Which model re-scores the candidates | Settings: model + base URL; a new reranker is one class |
| **LLM client** | Which models extract, answer, and build the graph | Settings: per-role model names; providers are registered per role |
| **Storage backend** | Where source bytes live | One line in the backend factory; local disk and S3 are both implemented |

### Multiple embedding strategies, and switching between them

The embedding strategy is the most flexible of these, because **multiple
strategies can coexist**:

- Every strategy is a row, and the same chunk may carry embeddings under
  several strategies at once — the vector is a *derived, strategy-scoped*
  representation of the chunk, not a property of it.
- Exactly one strategy is **active** at a time; it is the default for new
  embeddings and for queries that do not name a strategy.
- Any embed or query request can pass an explicit `strategy_id` to use a
  non-active strategy. This is what makes A/B evaluation possible: embed the
  corpus under both, query under both, compare the evidence and scores, and
  only then commit.

**Introducing a new embedding model in the future** is therefore a data
operation, not a migration:

1. **Register the model** — a row in the model catalog (provider, identifier,
   dimension, normalization, metric). Adding a model is an `INSERT`, not a
   schema change; the vector column and index are typed per model.
2. **Create a strategy over it** — `POST /embedding/strategies` with the
   model, input field and templates. The old strategy stays untouched.
3. **Embed under it** — `POST /embedding/strategies/{id}/rebuild` (or embed a
   single source/subject) to produce the new vectors. The old embeddings are
   not overwritten; both sets coexist.
4. **Evaluate** — query with `strategy_id` set to the new strategy and
   compare answers, similarities and rerank scores against the old one.
5. **Switch** — flip the active flag (`PATCH /embedding/strategies/{id}`).
   From then on, new embeds and default queries use the new strategy. The
   old strategy's vectors remain, with their provenance, for comparison or
   rollback.

The same logic applies to a *new way of using the same model* (different
templates, for instance): that is a second strategy over the same model row,
and the two can be A/B-tested the same way.

---

## 6. Tunables

The pipeline's shape is fixed; its numbers are settings, not constants:

| Setting | Default | What it controls |
|---|---|---|
| `RETRIEVAL_HNSW_TOP_K` | 50 | Candidates the vector search returns |
| `RETRIEVAL_HNSW_EF_SEARCH` | 100 | HNSW search effort — higher is more accurate, slower |
| `RETRIEVAL_MMR_TOP_K` | 10 | Candidates MMR keeps after diversification |
| `RETRIEVAL_MMR_LAMBDA` | 0.7 | Relevance vs. diversity balance (1.0 = pure relevance) |
| `RETRIEVAL_FINAL_TOP_K` | 5 | Chunks that reach the answering model |
| `EMBED_ON_CHUNK` | true | Embed automatically after chunking, or only on demand |
| `EMBEDDING_MAX_RETRIES` | 3 | Provider retry bound per embedding batch |
| `RERANKER_MODEL` | `nvidia/llama-nemotron-rerank-vl-1b-v2` | The reranking model |
| `RERANKER_TIMEOUT_SECONDS` | 30 | Reranking call timeout (falls back to MMR order past it) |

---

## 7. Observability

Every query response carries its own trace, so quality is measurable without
guesswork:

- **`retrieval`** — how many candidates each stage produced
  (`hnsw_candidates`, `mmr_candidates`, `final_chunks`) and whether the
  reranker was used or the fallback kicked in.
- **Per-chunk scores** — each returned chunk carries its `similarity` (cosine,
  from the vector search) and its `rerank_score` (the cross-encoder's score),
  so you can see both what the broad search liked and what the precise stage
  kept.
- **`usage`** — the answering model's prompt/completion tokens and latency,
  for cost attribution.

Embedding health is equally visible: scope-wide stats and per-source coverage
(`embedded / pending / failed / stale` percentages) are exposed by the API, so
"why doesn't my query find X" usually has a one-request answer — X was never
embedded, or its embedding failed.

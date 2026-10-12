# Evaluation Set

Memora's evaluation package: dataset schemas, metric functions, a
retrieval/end-to-end runner, and report generation. Everything here is
separate from the application: no file in `app/`, `migrations/` or
`tests/` is modified by evaluation code, and the database is only ever
read (SELECT only).

## Layout

```
backend/evals/
  README.md                        this file
  schemas.py                     EvalExample schema + validation + legacy mapping
  dataset.py                     JSONL loading, coverage, checksums
  metrics.py                     pure metric functions (retrieval/context/grounding/abstention/operational)
  adapter.py                     runs the real pipeline stages for one query
  reports.py                     JSON + Markdown report writing
  runner.py                      the CLI (python -m evals.runner ...)
  chunking_check.py              wraps scripts/verify_chunking.py
  datasets/
    example_synthetic.jsonl      synthetic example dataset (no real data)
  scripts/
    generate_eval_set.py           sample chunks -> LLM questions -> JSONL draft
    review_eval_set.py             interactive approve/reject/edit review
    run_eval.py                   run approved questions through the real pipeline
    compare_reports.py            diff two report JSONs (baseline vs new)
  data/                            gitignored: real document content, may be sensitive
    eval_set_draft.jsonl          written by generate_eval_set.py
    eval_set_reviewed.jsonl       written by review_eval_set.py (never overwrites the draft)
  reports/                         gitignored: per-run JSON + Markdown reports
    report_<label>_<timestamp>.json
    report_<label>_<timestamp>.md
```

## Dataset schema

One JSONL line = one example. Minimum contract:

```json
{
  "query": "What is the contract renewal date?",
  "tenant_id": "tenant-id",
  "expected_answer": "The contract renews on 15 March 2026.",
  "supporting_chunk_ids": ["chunk-id-1"],
  "supporting_pages": [3, 4],
  "query_type": "factoid",
  "difficulty": "easy"
}
```

Rules (enforced by `evals.schemas`):

- `query` and `tenant_id` are required, non-empty.
- `expected_answer` is required for answerable examples; unanswerable
  examples must set `is_unanswerable: true` and have no expected answer
  and no supporting chunks.
- `query_type` ∈ factoid, exact_lookup, table, comparison,
  multi_document, multi_hop, unanswerable, conversational, image,
  temporal.
- `difficulty` ∈ easy, medium, hard.
- Optional: `source_ids`, `application_id`, `subject_id`,
  `expected_citations`, `as_of_date`, `notes`.
- Invalid lines are reported with their line number, never silently
  repaired.

The older `evals/data` schema (from `generate_eval_set.py`) is supported
via `--legacy`: `question`→`query`, `type`→`query_type`
(semantic→factoid, multi_part→multi_document), `expected_chunks`→
`supporting_chunk_ids`/`supporting_pages`/`source_ids`,
`expected_behavior: "insufficient"`→`is_unanswerable: true`.

## The CLI

```bash
.venv/bin/python -m evals.runner validate  --dataset evals/datasets/example_synthetic.jsonl
.venv/bin/python -m evals.runner coverage  --dataset evals/data/eval_set_reviewed.jsonl --legacy
.venv/bin/python -m evals.runner retrieval --dataset <eval.jsonl> --tenant-id <uuid> --output evals/reports/
.venv/bin/python -m evals.runner full      --dataset <eval.jsonl> --tenant-id <uuid> --output evals/reports/
.venv/bin/python -m evals.runner chunking  --source-id <uuid>
```

- `validate` — check every line; prints line-numbered errors.
- `coverage` — counts by query type / difficulty / tenant, plus
  annotation coverage.
- `retrieval` — retrieval-only evaluation (no answer generation):
  Recall@5, Recall@10, MRR, nDCG@10, hit rate, context precision/recall,
  latency, cost. Unanswerable examples are excluded from ranking metrics
  (no ground truth) and included in hit rate with expected 0.
- `full` — end-to-end (retrieval + answer): everything above plus
  refusal rate / false-answer rate on unanswerable examples.
- `chunking` — the chunking QA report (wraps `scripts/verify_chunking.py`;
  validates the chunking stage, not answer quality).
- `--limit N` smoke test, `--label` run label, `--parallel N` opt-in
  bounded parallelism (default sequential).

Reports: `report_<label>_<timestamp>.json` (full per-example detail) and
`.md` (overall table, by type/difficulty/tenant, abstention,
operational, annotation coverage, warnings, failures). Run metadata
records the dataset SHA256, git commit, models and retrieval settings.

## Metrics: implemented vs N/A

| Metric | Status |
|---|---|
| Recall@5 / Recall@10 / MRR / nDCG@10 / hit rate | implemented (pure, tested) |
| Context precision / recall | implemented (chunk-ID based; identity, not semantic) |
| Redundancy rate | implemented (lexical Jaccard > 0.85); N/A in runner output until chunk texts are recorded per example |
| Citation precision | implemented (deterministic); N/A when no citations |
| Refusal rate / false-answer rate | implemented (substring patterns) |
| Latency avg/p50/p95, throughput, cost | implemented |
| TTFT | N/A — the LLM client does not expose streaming timings |
| Correctness / completeness / relevance / faithfulness | judge-based, optional (`--judge` in `scripts/run_eval.py`); labeled as LLM-judged, never ground truth |
| RAGAS / ARES | not integrated; optional future adapters, no new dependency |

## Chunking validation

`scripts/verify_chunking.py` remains the standalone, read-only chunking
QA report (token targets, section prefixes, content coverage, ordering,
page attribution). `evals.runner chunking` wraps it for programmatic
use. It validates the chunking stage only — it is not an answer-quality
evaluation.

## Prerequisites

- `backend/.env` with `DATABASE_URL` and `NVIDIA_API_KEY` (the scripts reuse
  the app's settings and LLM client; no new dependencies).
- Run from the `backend/` directory (or with the backend's venv on PATH):

```bash
cd backend
.venv/bin/python evals/scripts/generate_eval_set.py --help
```

## 1. Dry run (no LLM calls)

Preview what would be sampled and what it would cost:

```bash
.venv/bin/python evals/scripts/generate_eval_set.py \
    --tenant-id <uuid> \
    --dry-run
```

Optional: `--subject-id <uuid>` to narrow to one subject, `--n 50` for the
number of questions, `--seed 42` for reproducible sampling.

The dry run prints: eligible chunk count, the sample breakdown by source and
content_type, the planned question-type mix (semantic / exact_lookup /
table / multi_part / unanswerable), and an estimated LLM call count and cost.
**No LLM calls are made.**

## 2. Real run

```bash
.venv/bin/python evals/scripts/generate_eval_set.py \
    --tenant-id <uuid> \
    --output evals/data/eval_set_draft.jsonl
```

Each sampled chunk is sent to the answer model
(`nvidia/nemotron-3-super-120b-a12b`) with instructions to write ONE
self-contained, paraphrased question plus a short reference answer.
Unanswerable questions are generated on the same topic but with the answer
absent from the text. Questions are checked for copying (>60% word overlap),
duplicates, length and reference leakage; failures are retried then dropped,
and the drop counts are reported.

The output is JSONL, one question per line:

```json
{
  "id": "q001",
  "question": "...",
  "type": "semantic | exact_lookup | table | multi_part | unanswerable",
  "reference_answer": "...",
  "expected_behavior": "answer | insufficient",
  "expected_chunks": [
    {
      "chunk_id": "...",
      "source_id": "...",
      "page_start": 1,
      "page_end": 1,
      "section_path": ["..."],
      "text_snippet": "first ~200 chars of the chunk"
    }
  ],
  "tenant_id": "...",
  "subject_id": "...",
  "review_status": "draft",
  "reviewer_notes": ""
}
```

`expected_chunks` also stores `source_id`, page range, `section_path` and a
text snippet because chunk ids are deactivated and recreated when a document
is re-extracted; the extra fields let a chunk be re-matched later.

## 3. Review

```bash
.venv/bin/python evals/scripts/review_eval_set.py
```

Each question is shown with its reference answer and the source chunk text.
Keys:

- `a` — approve
- `r` — reject
- `e` — edit (type a replacement question)
- `s` — skip (leave as `pending`)
- `q` — quit (progress is saved)

Decisions are appended to `evals/data/eval_set_reviewed.jsonl`; the draft file
is never modified. Re-running resumes where you left off.

## 4. Run the eval (baseline)

Runs every `approved`/`edited` question through the **real** pipeline stages —
query embedding -> HNSW top-50 -> MMR top-10 -> reranker top-5 -> LLM answer —
by importing the app's own stage functions, so it always measures the current
configuration and settings.

```bash
cd backend
.venv/bin/python evals/scripts/run_eval.py \
    --tenant-id <uuid> \
    --label baseline
```

Options:

- `--input` — question set (default `evals/data/eval_set_reviewed.jsonl`;
  only `approved`/`edited` rows are used)
- `--limit N` — smoke test on the first N questions
- `--judge` — also score answers with the LLM (correctness + faithfulness
  vs the reference answer and retrieved chunks); judge cost is reported
  separately
- `--label` — run label used in report filenames (e.g. `baseline`)
- `--output-dir` — default `evals/reports/`

Per question the runner records: chunk IDs at each stage (top-50 HNSW,
after MMR, final 5), the answer text, per-stage and total latency, token
usage and cost, and retrieval metrics:

- **Recall at each stage** — expected chunk matched by `chunk_id`, with a
  fallback to `source_id` + overlapping page range + text snippet (fallback
  matches are counted and reported).
- **MRR** — rank of the first correct chunk in the final list.
- **Lost at stage** — right chunk was in the top-50 but lost at MMR, or
  survived MMR but lost at the reranker.
- **Refusal rate** on unanswerable questions — detected via the pipeline's
  canned no-context messages plus "insufficient" / "not mentioned" /
  "no information" wording (case-insensitive substring match).
- **Top reranker score** — recorded for a future cutoff threshold.

Two files are written per run: `report_<label>_<timestamp>.json` (full
per-question detail) and `.md` (readable summary: overall table, per-type
table, lost-at-stage counts, worst failures, run info with git commit,
models and retrieval settings).

## 5. Compare two runs

After changing retrieval settings or models, re-run with a new label and
diff against the baseline:

```bash
.venv/bin/python evals/scripts/run_eval.py --tenant-id <uuid> --label new-settings
.venv/bin/python evals/scripts/compare_reports.py \
    evals/reports/report_baseline_<ts>.json \
    evals/reports/report_new-settings_<ts>.json
```

Prints overall metric deltas (recall per stage, MRR, refusal rate, latency,
cost, lost-at-stage), per-question-type deltas, and the list of questions
that got better and worse.

## Cost

The generator and the judge use the app's price list (`backend/pricing.json`)
to estimate and report the real cost of the LLM calls they make. A
50-question run is typically a few cents; the runner's pipeline cost (embed +
answer per question) is reported per run and in the summary.

#!/usr/bin/env python3
"""Add the question types `generate_graph_eval_set.py` does not make.

* **corpus_wide**: an overview question that needs at least three passages from at
  least two files ("what are the main risks across our supplier contracts?").
  Ground truth is the group of passages it was written from, so Recall@k on these
  is a partial measure: other passages may also be relevant.
* **unanswerable**: a plausible question the corpus does not answer, in two kinds,
  `absent` (a real entity, a detail the passages never state) and `counterfactual`
  (a real entity with one detail changed so the premise is false). Each is checked
  against the five best BM25 matches in the whole corpus and dropped if a verifier
  finds the answer there.

Excluded filenames are never sampled (same `--exclude` as the other generator).
`--dry-run` prints the pool and the corpus-wide groups and makes no LLM call.
READ-ONLY against the database. Output is JSONL in the eval package's schema,
every record validated, marked `review_status: draft`: review it with
`review_eval_set.py` before using it as a frozen set.

    .venv/bin/python evals/scripts/generate_graph_eval_extras.py --tenant-id <uuid> --dry-run
    .venv/bin/python evals/scripts/generate_graph_eval_extras.py --tenant-id <uuid>
"""

from __future__ import annotations

import argparse
import json
import os
import random
import re
import sys
import uuid
from collections import Counter
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(Path(__file__).resolve().parent))
os.environ.setdefault("API_SECRET", "eval-set-generator")

import generate_eval_set as base  # noqa: E402
import generate_graph_eval_set as gg  # noqa: E402
from app.core.config import get_settings  # noqa: E402
from app.core.pricing import price_call  # noqa: E402
from app.db.database import SessionLocal  # noqa: E402
from app.llm.client import LLMError, get_llm_client  # noqa: E402
from evals.graph_questions import answerable_from, theme_groups  # noqa: E402
from evals.schemas import RecordError, validate_record  # noqa: E402
from evals.solvability import BM25  # noqa: E402

OUT_DEFAULT = BACKEND / "evals" / "data" / "graph_eval_extras_draft.jsonl"

_COMMON = (
    "You write evaluation questions for a retrieval system. Never invent facts. The question must be "
    "fully self-contained, paraphrased rather than copied, and must not say 'the document', 'the text', "
    "'the passages', 'above' or 'this section'."
)
_CORPUS = (
    _COMMON + " You are given several passages from different files about a common theme. Write ONE "
    "overview question that can only be answered well by combining information from at least three of "
    "them (for example: what are the main X, how do the Y compare, which Z are involved). Return JSON "
    '{"question": "...", "reference_answer": "..."}; reference_answer is a short summary or list that '
    "uses only the passages."
)
_ABSENT = (
    _COMMON + " You are given one passage. Write ONE question about a specific entity the passage names, "
    "asking for a detail the passage does NOT state (a figure, date, person or term that would normally "
    'live in another record). Return JSON {"question": "..."}.'
)
_COUNTERFACTUAL = (
    _COMMON + " You are given one passage. Write ONE question about a specific entity the passage names, "
    "but change one key detail (a number, name, date or place) so the question's premise is false. "
    'Return JSON {"question": "..."}.'
)
_VERIFY_CORPUS = (
    "You check an evaluation question meant to need at least THREE of the given passages. Return JSON "
    '{"coherent": bool, "answerable_from_one": bool, "passages_needed": int}. coherent: the question is '
    "sensible and the reference answer is supported by the passages. answerable_from_one: ONE passage "
    "alone answers it completely. passages_needed: how many passages a complete answer uses. Be strict."
)
_VERIFY_ABSENT = (
    "You check whether a question can be answered from the given passages. Return JSON "
    '{"answerable": bool}. answerable is true only if the passages state the specific fact the question '
    "asks for. Be strict: a related fact is not the answer."
)


def ask_question(client, model, system, user, usage_log) -> dict | None:
    for _ in range(base.MAX_RETRIES + 1):
        try:
            data, usage = client.chat_json(model, system, user)
        except LLMError:
            continue
        usage_log.append(usage)
        q = str(data.get("question") or "").strip()
        if q:
            return {"question": q, "answer": str(data.get("reference_answer") or "").strip()}
    return None


def verify_corpus(client, model, question, answer, group, usage_log) -> str | None:
    passages = "\n\n".join(f"Passage {i}:\n{c['content']}" for i, c in enumerate(group, 1))
    try:
        data, usage = client.chat_json(
            model, _VERIFY_CORPUS, f"Question: {question}\nReference answer: {answer}\n\n{passages}"
        )
    except LLMError:
        return "verifier failed"
    usage_log.append(usage)
    if not data.get("coherent"):
        return "not coherent"
    if data.get("answerable_from_one"):
        return "answerable from one passage"
    try:
        needed = int(data.get("passages_needed"))
    except (TypeError, ValueError):
        return "verifier gave no passage count"
    return None if needed >= 3 else "needs fewer than three passages"


def verify_absent(client, model, question, hits, usage_log) -> str | None:
    passages = "\n\n".join(f"Passage {i}:\n{t}" for i, (_, t) in enumerate(hits, 1))
    try:
        data, usage = client.chat_json(model, _VERIFY_ABSENT, f"Question: {question}\n\n{passages}")
    except LLMError:
        return "verifier failed"
    usage_log.append(usage)
    return "answerable from the corpus" if data.get("answerable") else None


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--tenant-id", required=True)
    p.add_argument("--subject-id")
    p.add_argument("--n-corpus", type=int, default=10)
    p.add_argument("--n-unanswerable", type=int, default=10)
    p.add_argument("--exclude", default=gg.DEFAULT_EXCLUDE, help="regex on filenames to never sample")
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--out", default=str(OUT_DEFAULT))
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    rng = random.Random(args.seed)
    tenant_id = uuid.UUID(args.tenant_id)
    subject_id = uuid.UUID(args.subject_id) if args.subject_id else None
    with SessionLocal() as db:
        chunks, skipped = gg.load_chunks(db, tenant_id, subject_id, re.compile(args.exclude, re.IGNORECASE))
    files = Counter(c["filename"] for c in chunks)
    groups = theme_groups(chunks, args.n_corpus * 3, rng)  # spare groups: some will be rejected
    print(f"eligible chunks: {len(chunks)} from {len(files)} files; excluded: {sorted(skipped)}")
    print(f"corpus-wide candidate groups: {len(groups)}")
    for g in groups[:5]:
        print("  " + " | ".join(f"{c['filename']}#{c['chunk_index']}" for c in g))
    if args.dry_run:
        return 0
    if not chunks:
        print("nothing to sample")
        return 1

    settings = get_settings()
    client, model = get_llm_client(), settings.llm_answer_model
    usage_log: list = []
    rejected: Counter[str] = Counter()
    accepted: list[str] = []
    out: list[dict] = []

    def emit(record: dict) -> None:
        try:
            validate_record(record, line_no=len(out) + 1)
        except RecordError as exc:
            rejected[f"invalid record: {exc.reason[:60]}"] += 1
            return
        out.append(record)
        accepted.append(record["query"])

    made = 0
    for group in groups:
        if made >= args.n_corpus:
            break
        user = "\n\n".join(f"Passage {i}:\n{c['content']}" for i, c in enumerate(group, 1))
        r = ask_question(client, model, _CORPUS, user, usage_log)
        if not r or not r["answer"]:
            rejected["no question returned"] += 1
            continue
        why = gg.acceptable(r["question"], group, accepted) or verify_corpus(
            client, model, r["question"], r["answer"], group, usage_log
        )
        if why:
            rejected[f"corpus_wide: {why}"] += 1
            continue
        emit({
            "query": r["question"], "tenant_id": str(tenant_id), "subject_id": group[0]["subject_id"],
            "expected_answer": r["answer"], "supporting_chunk_ids": [c["id"] for c in group],
            "supporting_pages": sorted({c["page"] for c in group if c["page"] is not None}),
            "query_type": "corpus_wide", "difficulty": "hard",
            "source_ids": sorted({c["source_id"] for c in group}),
            "notes": "recall@k is partial: other passages may also be relevant",
            "review_status": "draft",
        })
        made += 1

    index = BM25({c["id"]: c["content"] for c in chunks})
    texts = {c["id"]: c["content"] for c in chunks}
    pool = list(chunks)
    rng.shuffle(pool)
    made = 0
    for n, seed_chunk in enumerate(pool):
        if made >= args.n_unanswerable:
            break
        kind = "absent" if n % 2 == 0 else "counterfactual"
        r = ask_question(
            client, model, _ABSENT if kind == "absent" else _COUNTERFACTUAL,
            f"Passage:\n{seed_chunk['content']}", usage_log,
        )
        if not r:
            rejected["no question returned"] += 1
            continue
        why = gg.acceptable(r["question"], [seed_chunk], accepted) or verify_absent(
            client, model, r["question"], answerable_from(index, texts, r["question"]), usage_log
        )
        if why:
            rejected[f"unanswerable/{kind}: {why}"] += 1
            continue
        emit({
            "query": r["question"], "tenant_id": str(tenant_id), "subject_id": seed_chunk["subject_id"],
            "is_unanswerable": True, "query_type": "unanswerable", "difficulty": "medium",
            "source_ids": [seed_chunk["source_id"]], "notes": kind, "review_status": "draft",
        })
        made += 1

    cost = sum(
        price_call(settings.llm_provider_for("answer"), model, "answer", u.prompt_tokens, u.completion_tokens)[0]
        for u in usage_log
    )
    path = Path(args.out)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(o, ensure_ascii=False) + "\n" for o in out))
    print(f"\nwrote {len(out)} questions to {path}: {dict(Counter(o['query_type'] for o in out))}")
    print(f"rejected: {dict(rejected)}  LLM calls: {len(usage_log)}  cost: ${cost:.4f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

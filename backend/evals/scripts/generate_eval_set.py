#!/usr/bin/env python3
"""Generate a draft evaluation question set for Memora's RAG pipeline.

Samples active, embedded retrieval chunks (stratified by source, content_type
and position), asks the answer model to write one question per chunk, runs
quality checks, and writes a JSONL draft for human review.

READ-ONLY against the database: SELECT only, never INSERT/UPDATE/DELETE.

Run:
    .venv/bin/python evals/scripts/generate_eval_set.py --tenant-id <uuid> --dry-run
    .venv/bin/python evals/scripts/generate_eval_set.py --tenant-id <uuid>

Everything lives under backend/evals/; no file in app/, migrations/ or tests/
is touched by this script.
"""

from __future__ import annotations

import argparse
import json
import math
import random
import re
import sys
import uuid
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

# --- Make `app.*` importable (same pattern as scripts/verify_chunking.py) ----
BACKEND = Path(__file__).resolve().parents[2]  # backend/
sys.path.insert(0, str(BACKEND))
os_env_placeholder = None  # keep linters quiet about import order

import os  # noqa: E402

os.environ.setdefault("API_SECRET", "eval-set-generator")

from sqlalchemy import select  # noqa: E402

from app.core.config import get_settings  # noqa: E402
from app.core.pricing import price_call  # noqa: E402
from app.db.database import SessionLocal  # noqa: E402
from app.db.models import ChunkEmbedding, RetrievalChunk  # noqa: E402
from app.llm.client import LLMError, get_llm_client  # noqa: E402

# --- Constants -----------------------------------------------------------------

MIN_TOKENS = 40          # too short to hold a real fact
SNIPPET_CHARS = 200      # text snippet stored for later re-matching
MAX_OVERLAP = 0.60       # question words appearing in the chunk (copying)
MAX_DUP_JACCARD = 0.70   # similarity to another accepted question
MIN_QUESTION_WORDS = 5
MAX_RETRIES = 2          # regeneration attempts per question

# Target question-type mix (fractions of n; last one absorbs rounding).
TYPE_MIX = [
    ("semantic", 0.35),
    ("exact_lookup", 0.25),
    ("table", 0.15),
    ("multi_part", 0.10),
    ("unanswerable", 0.15),
]

# Regexes for reference leakage / vague phrasing in questions.
_REFERENCE_LEAK = re.compile(
    r"\b(this section|the section|the document|this document|"
    r"the (?:above|below)|above|below|herein|as mentioned|"
    r"the text|the passage|the chunk|the table above|the figure)\b",
    re.IGNORECASE,
)
_WORD = re.compile(r"[a-z0-9]+")


def words(text: str) -> set[str]:
    return set(_WORD.findall((text or "").casefold()))


def word_list(text: str) -> list[str]:
    return _WORD.findall((text or "").casefold())


def jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


# --- Prompts -------------------------------------------------------------------


def _system_prompt(qtype: str) -> str:
    base = (
        "You write evaluation questions for a retrieval system. You receive "
        "document text and must return ONE question as a single JSON object. "
        "You never invent facts: everything must be supported by the text.\n\n"
        "Rules:\n"
        "- The question must be fully self-contained: understandable by someone "
        "who has NOT seen the document or this text.\n"
        "- Paraphrase. Do NOT copy phrases from the text; ask the way a real "
        "user would, with different wording where possible.\n"
        "- No vague questions like 'What does this section say?'.\n"
        "- No references to 'the document', 'this section', 'above', 'the text'.\n"
        "- reference_answer: a short factual answer taken from the text.\n\n"
        "Return exactly this JSON shape and nothing else:\n"
        '{"question": "...", "reference_answer": "..."}'
    )
    if qtype == "exact_lookup":
        return base + (
            "\n\nThis question must be an EXACT LOOKUP: it asks for one "
            "specific name, ID, number, date, amount or code stated in the "
            "text (e.g. 'What is the invoice number issued to ...?')."
        )
    if qtype == "table":
        return base + (
            "\n\nThis question must be a TABLE question: the answer is a "
            "value inside a table row of the given text. Ask about one row "
            "or cell naturally (e.g. 'How much did ... cost in March?')."
        )
    if qtype == "multi_part":
        return base + (
            "\n\nThis question must be MULTI-PART: it needs facts from BOTH "
            "text passages given (e.g. it compares or combines one fact from "
            "each). Both passages are from the same document."
        )
    if qtype == "unanswerable":
        return (
            "You write evaluation questions for a retrieval system. You "
            "receive document text and must return ONE question as a single "
            "JSON object.\n\n"
            "The question must be UNANSWERABLE from this text: a plausible, "
            "realistic question on the SAME topic and in the SAME domain and "
            "vocabulary as the text, whose answer is NOT stated in the text. "
            "It must look like a genuine user question someone might ask "
            "about this subject -- not absurd, not off-topic.\n\n"
            "Rules:\n"
            "- Self-contained, understandable without the text.\n"
            "- Same domain/vocabulary as the text, but the specific answer "
            "must NOT appear anywhere in the text.\n"
            "- reference_answer: empty string.\n\n"
            'Return exactly this JSON shape and nothing else:\n'
            '{"question": "...", "reference_answer": ""}'
        )
    return base  # semantic


def _user_prompt(qtype: str, chunks: list[dict]) -> str:
    parts = []
    for i, c in enumerate(chunks, start=1):
        section = " > ".join(c["section_path"]) if c["section_path"] else ""
        header = f"<text_{i}>"
        if section:
            header += f" Section: {section}"
        if c.get("page_start") is not None:
            header += f" (page {c['page_start']})"
        parts.append(f"{header}\n{c['content']}\n</text_{i}>")
    return "\n\n".join(parts) + "\n\nReturn the JSON object now."


# --- Data structures -----------------------------------------------------------


@dataclass
class SampledChunk:
    chunk_id: str
    source_id: str
    subject_id: str
    tenant_id: str
    content: str
    content_type: str
    token_count: int
    page_start: int | None
    page_end: int | None
    section_path: list
    chunk_index: int

    def snippet(self) -> str:
        return self.content[:SNIPPET_CHARS]

    def expected(self) -> dict:
        return {
            "chunk_id": self.chunk_id,
            "source_id": self.source_id,
            "page_start": self.page_start,
            "page_end": self.page_end,
            "section_path": self.section_path,
            "text_snippet": self.snippet(),
        }


@dataclass
class Plan:
    qtype: str
    chunks: list[SampledChunk] = field(default_factory=list)


# --- Step 1: sampling -----------------------------------------------------------


def load_eligible_chunks(db, tenant_id: uuid.UUID, subject_id: uuid.UUID | None):
    """READ-ONLY: active chunks that have a completed embedding."""
    stmt = (
        select(RetrievalChunk)
        .join(ChunkEmbedding, ChunkEmbedding.chunk_id == RetrievalChunk.id)
        .where(
            RetrievalChunk.is_active == True,  # noqa: E712
            RetrievalChunk.tenant_id == tenant_id,
            ChunkEmbedding.embedding_status == "completed",
        )
    )
    if subject_id is not None:
        stmt = stmt.where(RetrievalChunk.subject_id == subject_id)
    return list(db.execute(stmt).scalars())


def filter_chunks(chunks: list[RetrievalChunk]) -> tuple[list[RetrievalChunk], int]:
    """Drop too-short chunks and pure headings."""
    kept, dropped = [], 0
    for c in chunks:
        if c.token_count < MIN_TOKENS:
            dropped += 1
            continue
        # A pure heading is a short line with no sentence punctuation.
        stripped = (c.content or "").strip()
        if len(stripped) < 120 and not re.search(r"[.!?:;]", stripped):
            dropped += 1
            continue
        kept.append(c)
    return kept, dropped


def stratified_sample(
    rng: random.Random, chunks: list[RetrievalChunk], n: int
) -> list[SampledChunk]:
    """Spread the sample across sources, content types and positions."""
    by_source: dict[str, list[RetrievalChunk]] = {}
    for c in chunks:
        by_source.setdefault(str(c.source_id), []).append(c)

    num_sources = len(by_source)
    if num_sources == 0:
        return []

    # Cap per source so one document cannot dominate.
    cap = max(1, math.ceil(n / num_sources))
    cap = min(cap, max(1, n // 3 + 1))

    picked: list[RetrievalChunk] = []
    for sid, group in sorted(by_source.items()):
        group = sorted(group, key=lambda c: c.chunk_index)
        # Split into thirds by position; sample evenly across them and
        # across content types.
        thirds = max(1, len(group) // 3)
        buckets = [group[:thirds], group[thirds : 2 * thirds], group[2 * thirds :] or group[:1]]
        take = min(cap, len(group))
        # Round-robin over (third, content_type) cells.
        cells: list[tuple[int, str, list[RetrievalChunk]]] = []
        for bi, bucket in enumerate(buckets):
            ctypes: dict[str, list[RetrievalChunk]] = {}
            for c in bucket:
                ctypes.setdefault(c.content_type, []).append(c)
            for ct, items in sorted(ctypes.items()):
                rng.shuffle(items)
                cells.append((bi, ct, items))
        chosen: list[RetrievalChunk] = []
        while len(chosen) < take and cells:
            for i, (_, _, items) in enumerate(cells):
                if items and len(chosen) < take:
                    chosen.append(items.pop())
            cells = [(b, ct, items) for b, ct, items in cells if items]
        picked.extend(chosen)

    rng.shuffle(picked)
    return [
        SampledChunk(
            chunk_id=str(c.id),
            source_id=str(c.source_id),
            subject_id=str(c.subject_id),
            tenant_id=str(c.tenant_id),
            content=c.content,
            content_type=c.content_type,
            token_count=c.token_count,
            page_start=c.page_start,
            page_end=c.page_end,
            section_path=list(c.section_path or []),
            chunk_index=c.chunk_index,
        )
        for c in picked[:n]
    ]


def build_plans(rng: random.Random, sample: list[SampledChunk], n: int) -> list[Plan]:
    """Assign each sampled chunk a question type per the target mix."""
    counts = {t: max(1, round(f * n)) for t, f in TYPE_MIX}
    # Scale to the actual sample size.
    total = sum(counts.values())
    scale = len(sample) / total if total else 0
    counts = {t: max(0, int(round(c * scale))) for t, c in counts.items()}

    table_chunks = [c for c in sample if c.content_type == "table"]
    counts["table"] = min(counts["table"], len(table_chunks))

    by_source: dict[str, list[SampledChunk]] = {}
    for c in sample:
        by_source.setdefault(c.source_id, []).append(c)
    pairable = {s for s, group in by_source.items() if len(group) >= 2}
    counts["multi_part"] = min(counts["multi_part"], len(pairable))

    # Assign greedily: table first (needs table chunks), then multi_part
    # (needs same-source pairs), then the rest.
    plans: list[Plan] = []
    used: set[str] = set()
    pool = list(sample)
    rng.shuffle(pool)

    def assign(qtype, predicate, count):
        assigned = 0
        for c in pool:
            if assigned >= count:
                break
            if c.chunk_id in used or not predicate(c):
                continue
            used.add(c.chunk_id)
            plans.append(Plan(qtype, [c]))
            assigned += 1
        return assigned

    assign("table", lambda c: c.content_type == "table", counts["table"])

    # multi_part: pair two unused chunks from the same source.
    mp = 0
    for c in pool:
        if mp >= counts["multi_part"]:
            break
        if c.chunk_id in used or c.source_id not in pairable:
            continue
        partner = next(
            (
                o
                for o in by_source[c.source_id]
                if o.chunk_id != c.chunk_id and o.chunk_id not in used
            ),
            None,
        )
        if partner is None:
            continue
        used.add(c.chunk_id)
        used.add(partner.chunk_id)
        plans.append(Plan("multi_part", [c, partner]))
        mp += 1

    # unanswerable, exact_lookup, semantic fill the rest.
    assign("unanswerable", lambda c: True, counts["unanswerable"])
    assign("exact_lookup", lambda c: True, counts["exact_lookup"])
    assign("semantic", lambda c: True, counts["semantic"])

    # Any unused chunks become semantic questions (don't waste the sample).
    for c in pool:
        if c.chunk_id not in used:
            used.add(c.chunk_id)
            plans.append(Plan("semantic", [c]))

    rng.shuffle(plans)
    return plans


# --- Step 2: generation ---------------------------------------------------------


def generate_question(client, model: str, plan: Plan) -> tuple[dict | None, object]:
    """One LLM call. Returns (parsed_question_or_None, usage)."""
    system = _system_prompt(plan.qtype)
    user = _user_prompt(plan.qtype, [{"content": c.content, "section_path": c.section_path, "page_start": c.page_start} for c in plan.chunks])
    try:
        raw, usage = client.chat_json(model, system, user)
    except LLMError:
        return None, None
    q = (raw.get("question") or "").strip()
    a = (raw.get("reference_answer") or "").strip()
    if not q:
        return None, usage
    if plan.qtype == "unanswerable":
        a = ""
    return {"question": q, "reference_answer": a}, usage


# --- Step 4: quality checks ------------------------------------------------------


def check_question(
    question: str, plan: Plan, accepted: list[dict]
) -> str | None:
    """Return a rejection reason, or None if the question passes."""
    qwords = word_list(question)
    if len(qwords) < MIN_QUESTION_WORDS:
        return "too_short"
    if _REFERENCE_LEAK.search(question):
        return "reference_leak"
    chunk_words: set[str] = set()
    for c in plan.chunks:
        chunk_words |= words(c.content)
    qset = set(qwords)
    if qset and len(qset & chunk_words) / len(qset) > MAX_OVERLAP:
        return "overlap"
    for other in accepted:
        if jaccard(qset, words(other["question"])) > MAX_DUP_JACCARD:
            return "duplicate"
    return None


# --- Main -----------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tenant-id", required=True)
    parser.add_argument("--subject-id", default=None)
    parser.add_argument("--n", type=int, default=50)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--output",
        default=str(BACKEND / "evals" / "data" / "eval_set_draft.jsonl"),
    )
    args = parser.parse_args()

    settings = get_settings()
    tenant_id = uuid.UUID(args.tenant_id)
    subject_id = uuid.UUID(args.subject_id) if args.subject_id else None
    rng = random.Random(args.seed)

    db = SessionLocal()
    try:
        all_chunks = load_eligible_chunks(db, tenant_id, subject_id)
    finally:
        db.close()

    eligible, filtered_out = filter_chunks(all_chunks)
    sample = stratified_sample(rng, eligible, args.n)
    plans = build_plans(rng, sample, args.n)

    by_source = Counter(str(c.source_id) for c in sample)
    by_type = Counter(c.content_type for c in sample)
    planned_types = Counter(p.qtype for p in plans)

    print("=== DRY RUN ===" if args.dry_run else "=== GENERATING ===")
    print(f"Tenant: {tenant_id}" + (f"  Subject: {subject_id}" if subject_id else ""))
    print(f"Eligible chunks (active + embedded, after filters): {len(eligible)}")
    print(f"  (filtered out: {filtered_out} too-short or heading-only)")
    print(f"Would sample: {len(sample)} of --n {args.n}")
    print()
    print(f"By source ({len(by_source)} sources):")
    for sid, count in by_source.most_common():
        print(f"  {sid}: {count}")
    print()
    print("By content_type:")
    for ct, count in by_type.most_common():
        print(f"  {ct}: {count}")
    print()
    print("Planned question type mix:")
    for t, _ in TYPE_MIX:
        print(f"  {t:14s}: {planned_types.get(t, 0)}")
    print()

    if args.dry_run:
        est_calls = len(plans) * (1 + 0)  # base calls
        est_with_retries = int(len(plans) * 1.3)
        est_prompt = 500 * est_with_retries
        est_completion = 100 * est_with_retries
        cost, _ = price_call(
            settings.llm_provider,
            settings.llm_answer_model,
            "answer",
            est_prompt,
            est_completion,
        )
        print(f"Estimated LLM calls: ~{est_calls} (with quality retries: ~{est_with_retries})")
        print(f"Estimated tokens: prompt ~{est_prompt:,}  completion ~{est_completion:,}")
        print(f"Estimated cost: ${cost:.4f}")
        print()
        print("No LLM calls made. Re-run without --dry-run to generate.")
        return 0

    # --- Real run ---------------------------------------------------------------
    client = get_llm_client()
    model = settings.llm_answer_model
    accepted: list[dict] = []
    drop_reasons: Counter = Counter()
    total_prompt = total_completion = 0
    total_cost = 0.0
    llm_calls = 0

    for i, plan in enumerate(plans, start=1):
        question = None
        for attempt in range(1 + MAX_RETRIES):
            parsed, usage = generate_question(client, model, plan)
            if usage is not None:
                llm_calls += 1
                total_prompt += usage.prompt_tokens
                total_completion += usage.completion_tokens
                cost, _ = price_call(
                    settings.llm_provider, model, "answer",
                    usage.prompt_tokens, usage.completion_tokens,
                )
                total_cost += cost
            if parsed is None:
                drop_reasons["llm_failed"] += 1
                continue
            reason = check_question(parsed["question"], plan, accepted)
            if reason is None:
                question = parsed
                break
            drop_reasons[reason] += 1
        if question is None:
            continue
        qid = f"q{len(accepted) + 1:03d}"
        record = {
            "id": qid,
            "question": question["question"],
            "type": plan.qtype,
            "reference_answer": question["reference_answer"],
            "expected_behavior": "insufficient" if plan.qtype == "unanswerable" else "answer",
            "expected_chunks": [c.expected() for c in plan.chunks],
            "tenant_id": str(tenant_id),
            "subject_id": str(plan.chunks[0].subject_id),
            "review_status": "draft",
            "reviewer_notes": "",
        }
        accepted.append(record)
        print(f"  [{i}/{len(plans)}] {qid} ({plan.qtype}) {question['question'][:80]}")

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8") as fh:
        for record in accepted:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")

    print()
    print("=== COMPLETE ===")
    print(f"Questions generated: {len(accepted)}")
    print(f"Dropped: {sum(drop_reasons.values())} ({dict(drop_reasons)})")
    print(f"LLM calls: {llm_calls}")
    print(f"Tokens: prompt={total_prompt:,} completion={total_completion:,}")
    print(f"Cost: ${total_cost:.4f}")
    print(f"Written to: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
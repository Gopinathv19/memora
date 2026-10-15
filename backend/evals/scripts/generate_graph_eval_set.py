#!/usr/bin/env python3
"""Generate a benchmark question set that can tell vector RAG from Graph RAG.

`generate_eval_set.py` writes one question per chunk, so every question is a
lookup that vector search is built for. A graph benchmark also needs questions
whose answer lives in *two* passages joined by something they both mention.
This script writes both kinds:

* **lookup** (`factoid` / `exact_lookup`): one chunk, one fact
* **relational** (`multi_document` when the two passages come from different
  files, `multi_hop` when they come from the same file): two chunks that share
  rare terms (names, identifiers, product words that occur in few chunks), and a
  question that needs both

Sources whose filename matches `--exclude` are never sampled, so personal
documents stay out of the question set and out of LLM prompts. `--dry-run` shows
the pool and the candidate pairs and makes no LLM call. READ-ONLY against the
database. Output: JSONL in the eval package's schema, ready for the console's
Benchmarks page or `python -m evals.runner`.

    .venv/bin/python evals/scripts/generate_graph_eval_set.py --tenant-id <uuid> --dry-run
    .venv/bin/python evals/scripts/generate_graph_eval_set.py --tenant-id <uuid>
"""

from __future__ import annotations

import argparse
import json
import os
import random
import re
import sys
import uuid
from collections import Counter, defaultdict
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(Path(__file__).resolve().parent))
os.environ.setdefault("API_SECRET", "eval-set-generator")

from sqlalchemy import select  # noqa: E402

import generate_eval_set as base  # noqa: E402
from app.core.config import get_settings  # noqa: E402
from app.core.pricing import price_call  # noqa: E402
from app.db.database import SessionLocal  # noqa: E402
from app.db.models import ChunkEmbedding, RetrievalChunk, Source  # noqa: E402
from app.llm.client import LLMError, get_llm_client  # noqa: E402

# Filenames never sampled: ID documents, legal documents, a CV, screenshots.
DEFAULT_EXCLUDE = r"aadhaar|voter|deed|pranav|screenshot|\.png$|\.jpe?g$"
MIN_SHARED_RARE_TERMS = 2
MAX_DF = 4  # a term in more chunks than this is not a link, it is vocabulary
MIN_TERM_LEN = 5
OUT_DEFAULT = BACKEND / "evals" / "data" / "graph_eval_set_draft.jsonl"

_STOP = frozenset(
    """about above after again against because before below between could during every
    first following from further having should their there these thing things this those
    through under until using where which while whose would other other's shall within
    without please provide provided include includes including system systems""".split()
)


def rare_terms(chunks: list[dict]) -> dict[str, set[str]]:
    """chunk id -> the terms of it that occur in at most MAX_DF chunks overall."""
    doc_freq: Counter[str] = Counter()
    per_chunk: dict[str, set[str]] = {}
    for c in chunks:
        terms = {w for w in base.word_list(c["content"]) if len(w) >= MIN_TERM_LEN and w not in _STOP and not w.isdigit()}
        per_chunk[c["id"]] = terms
        doc_freq.update(terms)
    return {cid: {t for t in terms if 2 <= doc_freq[t] <= MAX_DF} for cid, terms in per_chunk.items()}


def candidate_pairs(chunks: list[dict]) -> list[tuple[dict, dict, set[str]]]:
    terms = rare_terms(chunks)
    by_id = {c["id"]: c for c in chunks}
    index: dict[str, list[str]] = defaultdict(list)
    for cid, ts in terms.items():
        for t in ts:
            index[t].append(cid)
    shared: dict[tuple[str, str], set[str]] = defaultdict(set)
    for t, cids in index.items():
        for i, a in enumerate(cids):
            for b in cids[i + 1 :]:
                if a != b:
                    shared[tuple(sorted((a, b)))].add(t)
    pairs = [
        (by_id[a], by_id[b], ts)
        for (a, b), ts in shared.items()
        if len(ts) >= MIN_SHARED_RARE_TERMS
        # two chunks of one file that are neighbours are one passage, not two hops
        and (by_id[a]["source_id"] != by_id[b]["source_id"] or abs(by_id[a]["chunk_index"] - by_id[b]["chunk_index"]) > 2)
    ]
    pairs.sort(key=lambda p: (-len(p[2]), p[0]["id"], p[1]["id"]))
    # Interleave cross-file and same-file pairs: sorted by shared terms alone the
    # same-file pairs crowd out the cross-document ones Graph RAG is for.
    cross = [p for p in pairs if p[0]["source_id"] != p[1]["source_id"]]
    same = [p for p in pairs if p[0]["source_id"] == p[1]["source_id"]]
    merged = []
    for i in range(max(len(cross), len(same))):
        merged += cross[i : i + 1] + same[i : i + 1]
    return merged


def load_chunks(db, tenant_id, subject_id, exclude: re.Pattern) -> tuple[list[dict], Counter]:
    stmt = (
        select(RetrievalChunk, Source.filename)
        .join(Source, Source.id == RetrievalChunk.source_id)
        .join(ChunkEmbedding, ChunkEmbedding.chunk_id == RetrievalChunk.id)
        .where(RetrievalChunk.tenant_id == tenant_id, RetrievalChunk.is_active.is_(True),
               ChunkEmbedding.embedding_status == "completed")
        .distinct()
    )
    if subject_id:
        stmt = stmt.where(RetrievalChunk.subject_id == subject_id)
    kept, skipped = [], Counter()
    for chunk, filename in db.execute(stmt).all():
        if exclude.search(filename or ""):
            skipped[filename] += 1
            continue
        if chunk.token_count < base.MIN_TOKENS:
            continue
        kept.append({
            "id": str(chunk.id), "source_id": str(chunk.source_id), "filename": filename,
            "chunk_index": chunk.chunk_index, "content": chunk.content, "page": chunk.page_start,
            "subject_id": str(chunk.subject_id), "content_type": chunk.content_type,
        })
    return kept, skipped


_JSON = '{"question": "...", "reference_answer": "..."}'
_COMMON = (
    "You write evaluation questions for a retrieval system and return ONE JSON object: " + _JSON + ". "
    "Never invent facts. The question must be fully self-contained (understandable by someone who has not "
    "seen the text), paraphrased rather than copied, and must not say 'the document', 'the text', 'above' "
    "or 'this section'. reference_answer is short and stated in the text."
)
_LOOKUP = _COMMON + " Ask about one specific fact in the passage."
_EXACT = _COMMON + " Ask for one exact name, number, date, identifier or code stated in the passage."
_RELATIONAL = (
    _COMMON + " You are given TWO passages that mention something in common. Write a question that can only "
    "be answered by combining a fact from EACH passage (for example: follow the shared thing from one to "
    "the other). A question answerable from either passage alone is wrong. Do not name which passage a fact "
    "comes from."
)


def ask(client, model, system, user, usage_log) -> dict | None:
    for _ in range(base.MAX_RETRIES + 1):
        try:
            data, usage = client.chat_json(model, system, user)
        except LLMError:
            continue
        usage_log.append(usage)
        q, a = str(data.get("question") or "").strip(), str(data.get("reference_answer") or "").strip()
        if q and a:
            return {"question": q, "answer": a}
    return None


_VERIFY = (
    "You check an evaluation question that is meant to need TWO passages. Return JSON "
    '{"from_passage_1": bool, "from_passage_2": bool, "needs_both": bool, "coherent": bool}. '
    "from_passage_N: the question's COMPLETE answer can be found using passage N alone. "
    "needs_both: fully answering requires a fact from each passage. "
    "coherent: the question is sensible and the reference answer correctly answers it using the passages "
    "(not a vague or generic answer, and the two passages are genuinely about the same thing). "
    "Be strict."
)


def verified(client, model, question, answer, a, b, usage_log) -> str | None:
    """None when the question really needs both passages, else the reason it does not."""
    user = (f"Question: {question}\nReference answer: {answer}\n\n"
            f"Passage 1:\n{a['content']}\n\nPassage 2:\n{b['content']}")
    try:
        data, usage = client.chat_json(model, _VERIFY, user)
    except LLMError:
        return "verifier failed"
    usage_log.append(usage)
    if not data.get("coherent"):
        return "not coherent"
    if data.get("from_passage_1") or data.get("from_passage_2"):
        return "answerable from one passage"
    return None if data.get("needs_both") else "does not need both passages"


def acceptable(question: str, chunks: list[dict], accepted: list[str]) -> str | None:
    if len(question.split()) < base.MIN_QUESTION_WORDS:
        return "too short"
    if base._REFERENCE_LEAK.search(question):
        return "refers to the document"
    qw = base.words(question)
    for c in chunks:
        if qw and len(qw & base.words(c["content"])) / len(qw) > base.MAX_OVERLAP:
            return "copies the text"
    if any(base.jaccard(qw, base.words(a)) > base.MAX_DUP_JACCARD for a in accepted):
        return "duplicate"
    return None


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--tenant-id", required=True)
    p.add_argument("--subject-id")
    p.add_argument("--n-lookup", type=int, default=20)
    p.add_argument("--n-relational", type=int, default=20)
    p.add_argument("--exclude", default=DEFAULT_EXCLUDE, help="regex on filenames to never sample")
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--out", default=str(OUT_DEFAULT))
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    rng = random.Random(args.seed)
    tenant_id = uuid.UUID(args.tenant_id)
    subject_id = uuid.UUID(args.subject_id) if args.subject_id else None
    with SessionLocal() as db:
        chunks, skipped = load_chunks(db, tenant_id, subject_id, re.compile(args.exclude, re.IGNORECASE))
    pairs = candidate_pairs(chunks)
    by_source = Counter(c["filename"] for c in chunks)
    print(f"eligible chunks: {len(chunks)} from {len(by_source)} files")
    for name, n in by_source.most_common():
        print(f"  {n:4d}  {name}")
    print(f"excluded by --exclude ({sum(skipped.values())} chunks): {sorted(skipped)}")
    cross = sum(1 for a, b, _ in pairs if a["source_id"] != b["source_id"])
    print(f"candidate relational pairs: {len(pairs)} ({cross} across files, {len(pairs) - cross} within one file)")
    for a, b, ts in pairs[:5]:
        print(f"  {a['filename']}#{a['chunk_index']} <-> {b['filename']}#{b['chunk_index']}  shared: {sorted(ts)[:6]}")
    if args.dry_run:
        return 0
    if not chunks:
        print("nothing to sample")
        return 1

    settings = get_settings()
    client, model = get_llm_client(), settings.llm_answer_model
    usage_log: list = []
    out: list[dict] = []
    accepted: list[str] = []
    rejected: Counter[str] = Counter()

    def emit(qtype, difficulty, question, answer, used):
        out.append({
            "query": question, "tenant_id": str(tenant_id), "subject_id": used[0]["subject_id"],
            "expected_answer": answer, "supporting_chunk_ids": [c["id"] for c in used],
            "supporting_pages": sorted({c["page"] for c in used if c["page"] is not None}),
            "query_type": qtype, "difficulty": difficulty, "source_ids": sorted({c["source_id"] for c in used}),
            "source_files": sorted({c["filename"] for c in used}),
            "snippets": [c["content"][: base.SNIPPET_CHARS] for c in used],
        })
        accepted.append(question)

    # lookups: spread across files so one large file does not dominate
    pools: dict[str, list[dict]] = defaultdict(list)
    for c in chunks:
        pools[c["source_id"]].append(c)
    for pool in pools.values():
        rng.shuffle(pool)
    order = [s for s in pools]
    i = 0
    while sum(1 for o in out if o["query_type"] in ("factoid", "exact_lookup")) < args.n_lookup and any(pools.values()):
        sid = order[i % len(order)]
        i += 1
        if not pools[sid]:
            continue
        chunk = pools[sid].pop()
        exact = len(out) % 3 == 0
        r = ask(client, model, _EXACT if exact else _LOOKUP, f"Passage:\n{chunk['content']}", usage_log)
        if not r:
            rejected["no question returned"] += 1
            continue
        why = acceptable(r["question"], [chunk], accepted)
        if why:
            rejected[why] += 1
            continue
        emit("exact_lookup" if exact else "factoid", "easy", r["question"], r["answer"], [chunk])

    used_chunks: set[str] = set()
    made = 0
    for a, b, shared in pairs:
        if made >= args.n_relational:
            break
        if a["id"] in used_chunks or b["id"] in used_chunks:
            continue
        user = f"Passage 1:\n{a['content']}\n\nPassage 2:\n{b['content']}\n\nShared terms: {', '.join(sorted(shared)[:8])}"
        r = ask(client, model, _RELATIONAL, user, usage_log)
        if not r:
            rejected["no question returned"] += 1
            continue
        why = acceptable(r["question"], [a, b], accepted) or verified(
            client, model, r["question"], r["answer"], a, b, usage_log
        )
        if why:
            rejected[why] += 1
            continue
        same = a["source_id"] == b["source_id"]
        emit("multi_hop" if same else "multi_document", "hard", r["question"], r["answer"], [a, b])
        used_chunks.update((a["id"], b["id"]))
        made += 1

    cost = sum(price_call(settings.llm_provider_for("answer"), model, "answer", u.prompt_tokens, u.completion_tokens)[0] for u in usage_log)
    path = Path(args.out)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(o, ensure_ascii=False) + "\n" for o in out))
    kinds = Counter(o["query_type"] for o in out)
    print(f"\nwrote {len(out)} questions to {path}: {dict(kinds)}")
    print(f"rejected: {dict(rejected)}  LLM calls: {len(usage_log)}  cost: ${cost:.4f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

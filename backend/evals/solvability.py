"""How many eval questions does plain keyword search already solve?

A relational question that BM25 answers by itself does not test Graph RAG: a
graph win on it proves nothing. This ranks every active chunk of the corpus for
each question with a small in-memory BM25 and reports, per query type, how often
the question's supporting chunks are in the top k.

* ``any``  at least one supporting chunk is in the top k
* ``all``  every supporting chunk is in the top k (what a multi-passage question
           needs; a high share here means the question is not hard for lexical search)

Pure functions only, no database: `scripts/lexical_solvability.py` loads chunks.
Unanswerable questions have no supporting chunks and are skipped.
"""

from __future__ import annotations

import math
import re
from collections import Counter, defaultdict

_TOKEN = re.compile(r"\w{2,}", re.UNICODE)
K1 = 1.5
B = 0.75


def tokenize(text: str) -> list[str]:
    return _TOKEN.findall((text or "").lower())


class BM25:
    def __init__(self, docs: dict[str, str]):
        self.ids = list(docs)
        self.tf = [Counter(tokenize(docs[i])) for i in self.ids]
        self.len = [sum(c.values()) for c in self.tf]
        self.avg = (sum(self.len) / len(self.len)) if self.len else 0.0
        df: Counter[str] = Counter()
        for c in self.tf:
            df.update(c.keys())
        n = len(self.ids)
        self.idf = {t: math.log(1 + (n - d + 0.5) / (d + 0.5)) for t, d in df.items()}

    def rank(self, query: str, k: int) -> list[str]:
        terms = [t for t in dict.fromkeys(tokenize(query)) if t in self.idf]
        scored: list[tuple[float, str]] = []
        for doc_id, tf, length in zip(self.ids, self.tf, self.len):
            score = 0.0
            for t in terms:
                f = tf.get(t, 0)
                if f:
                    norm = f + K1 * (1 - B + B * length / self.avg) if self.avg else f
                    score += self.idf[t] * f * (K1 + 1) / norm
            if score > 0:
                scored.append((-score, doc_id))
        scored.sort()
        return [doc_id for _, doc_id in scored[:k]]


def solvability(questions: list[dict], chunks: dict[str, str], k: int = 5) -> dict:
    """`questions` need `query`, `supporting_chunk_ids`, `query_type`; `chunks` maps id -> text.

    Supporting ids missing from `chunks` (deleted or re-chunked) are counted in
    `stale_support` and the question is skipped, not scored as a miss.
    """
    index = BM25(chunks)
    rows = []
    stale = 0
    for q in questions:
        support = list(q.get("supporting_chunk_ids") or [])
        if not support or q.get("is_unanswerable"):
            continue
        if any(s not in chunks for s in support):
            stale += 1
            continue
        top = set(index.rank(q["query"], k))
        hits = [s in top for s in support]
        rows.append(
            {"query": q["query"], "query_type": q.get("query_type", "factoid"),
             "any": any(hits), "all": all(hits), "supporting": len(support)}
        )
    by_type: dict[str, dict] = defaultdict(lambda: {"n": 0, "any": 0, "all": 0})
    for r in rows:
        t = by_type[r["query_type"]]
        t["n"] += 1
        t["any"] += r["any"]
        t["all"] += r["all"]
    summary = {
        t: {"n": v["n"], "any": round(v["any"] / v["n"], 4), "all": round(v["all"] / v["n"], 4)}
        for t, v in sorted(by_type.items())
    }
    return {"k": k, "scored": len(rows), "stale_support": stale, "by_type": summary, "rows": rows}

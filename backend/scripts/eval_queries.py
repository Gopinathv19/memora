"""Snapshot and compare query results, to prove a speed change did not change
retrieval quality.

Run once before a change (writes a baseline), again after it (compares):

    python scripts/eval_queries.py questions.json --token memora_xxx --out baseline.json
    python scripts/eval_queries.py questions.json --token memora_xxx --compare baseline.json

`questions.json` is a list of strings, or of {"query": ..., "subject_id": ...,
"source_id": ...} objects. For each question the script records the final
chunk ids (in order), the answer text, the end-to-end latency and the server's
per-stage `timings_ms`. With --compare it reports any question whose chunk ids
changed, how similar the answers are, and the p50/p95 latency before and after.

Chunk ids are exact; answers come from a language model, so a wording
difference is expected and only a large drop in similarity is flagged.
"""

import argparse
import json
import statistics
import sys
import time
from difflib import SequenceMatcher
from pathlib import Path

import httpx


def run(base_url: str, token: str, questions: list, mode: str | None, stream: bool) -> list[dict]:
    headers = {"Authorization": f"Bearer {token}"}
    results = []
    with httpx.Client(base_url=base_url, headers=headers, timeout=180) as client:
        for item in questions:
            body = {"query": item} if isinstance(item, str) else dict(item)
            if mode:
                body["retrieval_mode"] = mode
            started = time.monotonic()
            first_token_ms = None
            if stream:
                answer, chunks, timings = "", [], {}
                with client.stream("POST", "/api/v1/query/stream", json=body) as resp:
                    resp.raise_for_status()
                    event = None
                    for line in resp.iter_lines():
                        if line.startswith("event:"):
                            event = line[6:].strip()
                        elif line.startswith("data:"):
                            data = json.loads(line[5:])
                            if event == "start":
                                chunks = [c["chunk_id"] for c in data["chunks"]]
                                timings = data["retrieval"].get("timings_ms", {})
                            elif event == "delta":
                                if first_token_ms is None:
                                    first_token_ms = int((time.monotonic() - started) * 1000)
                                answer += data["text"]
            else:
                resp = client.post("/api/v1/query", json=body)
                resp.raise_for_status()
                data = resp.json()
                answer = data["answer"]
                chunks = [c["chunk_id"] for c in data["chunks"]]
                timings = data["retrieval"].get("timings_ms", {})
            results.append(
                {
                    "query": body["query"],
                    "chunk_ids": chunks,
                    "answer": answer,
                    "latency_ms": int((time.monotonic() - started) * 1000),
                    "first_token_ms": first_token_ms,
                    "timings_ms": timings,
                }
            )
            print(f"{results[-1]['latency_ms']:>6} ms  {body['query'][:70]}")
    return results


def percentile(values: list[int], p: float) -> int:
    values = sorted(values)
    return values[min(len(values) - 1, int(round(p * (len(values) - 1))))] if values else 0


def compare(before: list[dict], after: list[dict]) -> int:
    old = {r["query"]: r for r in before}
    problems = 0
    for r in after:
        b = old.get(r["query"])
        if b is None:
            continue
        same_chunks = b["chunk_ids"] == r["chunk_ids"]
        similarity = SequenceMatcher(None, b["answer"], r["answer"]).ratio()
        flag = "ok " if same_chunks and similarity >= 0.6 else "DIFF"
        if flag == "DIFF":
            problems += 1
        print(f"{flag} chunks_same={same_chunks} answer_similarity={similarity:.2f}  {r['query'][:60]}")
    for label, rows in (("before", before), ("after", after)):
        lat = [r["latency_ms"] for r in rows]
        print(f"{label}: p50={percentile(lat, .5)} ms  p95={percentile(lat, .95)} ms  mean={int(statistics.mean(lat))} ms")
    print(f"{problems} question(s) changed")
    return 1 if problems else 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("questions")
    parser.add_argument("--token", required=True, help="an API credential (memora_...)")
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--mode", choices=["hybrid", "dense"])
    parser.add_argument("--stream", action="store_true", help="use /query/stream")
    parser.add_argument("--out", help="write the results here (a baseline)")
    parser.add_argument("--compare", help="a baseline written earlier with --out")
    args = parser.parse_args()

    questions = json.loads(Path(args.questions).read_text())
    results = run(args.base_url, args.token, questions, args.mode, args.stream)
    if args.out:
        Path(args.out).write_text(json.dumps(results, indent=2, ensure_ascii=False))
        print(f"wrote {args.out}")
    if args.compare:
        sys.exit(compare(json.loads(Path(args.compare).read_text()), results))


if __name__ == "__main__":
    main()

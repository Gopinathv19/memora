#!/usr/bin/env python3
"""Interactive review of a draft evaluation set.

Shows each question with its reference answer and source chunk text, and
lets you approve / reject / edit / skip each one. Results are written to a
separate reviewed file; the draft file is never modified.

Run:
    .venv/bin/python evals/scripts/review_eval_set.py
    .venv/bin/python evals/scripts/review_eval_set.py --input <draft.jsonl> --output <reviewed.jsonl>

Keys: [a]pprove  [r]eject  [e]dit  [s]kip  [q]uit
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2]
DEFAULT_INPUT = BACKEND / "evals" / "data" / "eval_set_draft.jsonl"
DEFAULT_OUTPUT = BACKEND / "evals" / "data" / "eval_set_reviewed.jsonl"


def load_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        print(f"No input file at {path}")
        return []
    records = []
    with path.open(encoding="utf-8") as fh:
        for line_no, line in enumerate(fh, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError as exc:
                print(f"Skipping malformed line {line_no}: {exc}")
    return records


def load_reviewed_ids(path: Path) -> set[str]:
    """Ids already reviewed in a previous session (resume support)."""
    if not path.exists():
        return set()
    ids = set()
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                ids.add(json.loads(line)["id"])
            except (json.JSONDecodeError, KeyError):
                continue
    return ids


def show(record: dict, index: int, total: int) -> None:
    print()
    print(f"=== Question {index}/{total}  ({record.get('id', '?')}) ===")
    print()
    print(f"  Type:            {record.get('type', '?')}")
    print(f"  Expected:        {record.get('expected_behavior', '?')}")
    print(f"  Question:        {record.get('question', '')}")
    print(f"  Reference:       {record.get('reference_answer', '') or '(none - unanswerable)'}")
    print()
    chunks = record.get("expected_chunks") or []
    if not chunks:
        print("  Expected chunks:  (none - unanswerable question)")
    for i, c in enumerate(chunks, start=1):
        section = " > ".join(c.get("section_path") or [])
        pages = ""
        if c.get("page_start") is not None:
            pages = f" (page {c['page_start']}"
            if c.get("page_end") and c["page_end"] != c["page_start"]:
                pages += f"-{c['page_end']}"
            pages += ")"
        print(f"  Source chunk {i}:  {c.get('source_id', '?')}{pages}")
        if section:
            print(f"    Section: {section}")
        print(f"    Text: {c.get('text_snippet', '')}")
        print()
    print("  [a]pprove  [r]eject  [e]dit  [s]kip  [q]uit")


def prompt_action() -> str:
    while True:
        try:
            choice = input("> ").strip().lower()
        except EOFError:
            return "q"
        if choice in ("a", "r", "e", "s", "q"):
            return choice
        print("Please press a, r, e, s or q.")


def prompt_replacement(original: str) -> str:
    print()
    print("Type the replacement question (empty line cancels):")
    print(f"  original: {original}")
    try:
        new = input("new question> ").strip()
    except EOFError:
        return ""
    return new


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", default=str(DEFAULT_INPUT))
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    args = parser.parse_args()

    in_path = Path(args.input)
    out_path = Path(args.output)

    records = load_jsonl(in_path)
    if not records:
        print(f"Nothing to review in {in_path}")
        return 1

    if in_path.resolve() == out_path.resolve():
        print("Output must differ from the input; the draft is never overwritten.")
        return 1

    already = load_reviewed_ids(out_path)
    if already:
        print(f"Resuming: {len(already)} question(s) already reviewed in {out_path.name}.")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    # Append mode: previous review decisions are preserved.
    with out_path.open("a", encoding="utf-8") as out:
        for i, record in enumerate(records, start=1):
            if record.get("id") in already:
                continue
            show(record, i, len(records))
            action = prompt_action()
            if action == "q":
                print("Quitting; progress saved.")
                break
            if action == "a":
                record["review_status"] = "approved"
            elif action == "r":
                record["review_status"] = "rejected"
            elif action == "s":
                record["review_status"] = "pending"
            elif action == "e":
                new_q = prompt_replacement(record.get("question", ""))
                if new_q:
                    record["question"] = new_q
                    record["review_status"] = "edited"
                else:
                    record["review_status"] = "pending"
            out.write(json.dumps(record, ensure_ascii=False) + "\n")
            out.flush()

    print()
    print(f"Review saved to: {out_path}")
    print(f"The draft file was not modified: {in_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
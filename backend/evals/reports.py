"""Report writing: one JSON (machine) + one Markdown (human) per run.

The JSON carries everything (per-example results included); the Markdown
is a readable summary. Both are written to a configurable output
directory; nothing is ever written to production tables.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path


def _fmt(v) -> str:
    if v is None:
        return "n/a"
    if isinstance(v, float):
        return f"{v:.4f}".rstrip("0").rstrip(".") or "0"
    return str(v)


def build_payload(
    *,
    label: str,
    summary: dict,
    per_example: list[dict],
    run_info: dict,
) -> dict:
    """The full JSON report payload."""
    return {
        "run_info": {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "label": label,
            **run_info,
        },
        "summary": summary,
        "examples": per_example,
    }


def write_json(path: Path, payload: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def write_markdown(path: Path, payload: dict) -> Path:
    """The human-readable summary of a run payload."""
    s = payload["summary"]
    info = payload["run_info"]
    lines = [f"# Eval report: {info['label']}", ""]

    lines += ["## Overall", "", "| Metric | Value |", "|---|---|"]
    overall = s.get("overall") or {}
    for key, val in overall.items():
        lines.append(f"| {key.replace('_', ' ')} | {_fmt(val)} |")

    for group, title in (
        ("by_query_type", "By query type"),
        ("by_difficulty", "By difficulty"),
        ("by_tenant", "By tenant"),
    ):
        rows = s.get(group) or {}
        if not rows:
            continue
        lines += ["", f"## {title}", "", "| Group | n | Recall@5 | Recall@10 | MRR | nDCG@10 | Hit |", "|---|---|---|---|---|---|---|"]
        for g, row in rows.items():
            lines.append(
                f"| {g} | {row.get('n', '?')} | {_fmt(row.get('recall_at_5'))} "
                f"| {_fmt(row.get('recall_at_10'))} | {_fmt(row.get('mrr'))} "
                f"| {_fmt(row.get('ndcg_at_10'))} | {_fmt(row.get('hit_rate'))} |"
            )

    # Answer / grounding / abstention (full runs only).
    for section, keys in (
        ("answer_quality", ["correctness", "completeness", "relevance"]),
        ("grounding", ["faithfulness", "unsupported_claim_rate", "citation_precision"]),
        ("abstention", ["refusal_rate", "false_answer_rate"]),
    ):
        vals = s.get(section) or {}
        if not vals:
            continue
        lines += ["", f"## {section.replace('_', ' ').title()}", "", "| Metric | Value |", "|---|---|"]
        for k in keys:
            if k in vals:
                lines.append(f"| {k.replace('_', ' ')} | {_fmt(vals[k])} |")

    ops = s.get("operational") or {}
    if ops:
        lines += ["", "## Operational", "", "| Metric | Value |", "|---|---|"]
        for k, v in ops.items():
            lines.append(f"| {k.replace('_', ' ')} | {_fmt(v)} |")

    cov = s.get("annotation_coverage") or {}
    if cov:
        lines += ["", "## Dataset annotation coverage", "", "| Annotation | Examples |", "|---|---|"]
        for k, v in cov.items():
            lines.append(f"| {k.replace('_', ' ')} | {v} |")

    warnings = s.get("warnings") or []
    if warnings:
        lines += ["", "## Warnings", ""]
        for w in warnings:
            lines.append(f"- {w}")

    # Per-example failures.
    failures = [e for e in payload["examples"] if e.get("error")]
    if failures:
        lines += ["", f"## Failures ({len(failures)})", ""]
        for e in failures[:20]:
            lines.append(f"- **{e.get('id') or e.get('query', '?')[:60]}**: {e['error']}")

    lines += ["", "## Run info", ""]
    for k, v in info.items():
        if k not in ("label", "timestamp"):
            lines.append(f"- {k}: `{v}`")

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def write_reports(out_dir: Path, label: str, payload: dict) -> tuple[Path, Path]:
    """Write the timestamped JSON + Markdown pair. Returns both paths."""
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    json_path = write_json(out_dir / f"report_{label}_{ts}.json", payload)
    md_path = write_markdown(out_dir / f"report_{label}_{ts}.md", payload)
    return json_path, md_path

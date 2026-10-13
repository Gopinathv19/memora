"""Windowed map-reduce extraction: build windows, merge window results.

Windows are ~15-20k characters built from whole units (a unit is never
split), with one unit of overlap so a table spanning a page boundary is seen
whole by at least one window. The reduce is code, not a model: tables are
concatenated and continuations merged, fields deduped by (key, normalized
value), conflicts resolved by confidence with the loser recorded.
"""

from dataclasses import dataclass, field


@dataclass
class Window:
    index: int
    text: str
    unit_indices: list[int]  # 1-based unit numbers covered (incl. overlap)


def build_windows(
    sections: list[tuple[str, int]],  # (text, unit_index) per unit, in order
    max_chars: int = 18_000,
    overlap_units: int = 1,
) -> list[Window]:
    """Pack whole units into windows of at most `max_chars` characters.

    A single unit larger than the limit becomes its own window (never split).
    Consecutive windows share `overlap_units` trailing/leading units.
    """
    windows: list[Window] = []
    current: list[tuple[str, int]] = []
    current_chars = 0
    for text, unit_index in sections:
        added = len(text) + 2  # the "\n\n" join
        if current and current_chars + added > max_chars:
            windows.append(current)
            # Keep the last `overlap_units` units as the next window's prefix.
            current = current[-overlap_units:] if overlap_units > 0 else []
            current_chars = sum(len(t) + 2 for t, _ in current)
        current.append((text, unit_index))
        current_chars += added
    if current:
        windows.append(current)

    return [
        Window(
            index=i,
            text="\n\n".join(t for t, _ in w),
            unit_indices=[u for _, u in w],
        )
        for i, w in enumerate(windows)
    ]


# --- reduce ------------------------------------------------------------------------


def _norm_value(value: str) -> str:
    return " ".join(str(value or "").lower().split())


@dataclass
class FieldConflict:
    key: str
    kept: str
    dropped: str
    kept_confidence: float | None
    dropped_confidence: float | None


@dataclass
class ReduceResult:
    fields: list[dict] = field(default_factory=list)
    tables: list[dict] = field(default_factory=list)
    conflicts: list[FieldConflict] = field(default_factory=list)
    duplicate_fields: int = 0
    merged_tables: int = 0


def merge_fields(all_fields: list[dict]) -> tuple[list[dict], list[FieldConflict], int]:
    """Dedupe fields by (key, normalized value); on same key with different
    values keep the higher-confidence one and record the conflict."""
    kept: list[dict] = []
    by_key: dict[str, dict] = {}
    conflicts: list[FieldConflict] = []
    duplicates = 0
    for item in all_fields:
        if not isinstance(item, dict):
            continue  # lenient, like _build_result: never crash on junk
        key = (item.get("key") or "").strip()
        value = item.get("value")
        if not key or value is None:
            continue
        norm = _norm_value(str(value))
        existing = by_key.get(key)
        if existing is None:
            entry = dict(item)
            entry["_norm"] = norm
            by_key[key] = entry
            kept.append(entry)
            continue
        if existing["_norm"] == norm:
            duplicates += 1  # same fact seen in an overlapping window
            # Keep the better citation/confidence.
            if (item.get("confidence") or 0) > (existing.get("confidence") or 0):
                existing.update(
                    {k: item.get(k) for k in ("page", "confidence")}
                )
            continue
        # Same key, different value: keep the higher-confidence one.
        new_c = item.get("confidence") or 0
        old_c = existing.get("confidence") or 0
        if new_c > old_c:
            conflicts.append(
                FieldConflict(key, str(value), str(existing.get("value")),
                              new_c, old_c)
            )
            existing.update({"value": value, "_norm": norm,
                            "confidence": item.get("confidence"),
                            "page": item.get("page")})
        else:
            conflicts.append(
                FieldConflict(key, str(existing.get("value")), str(value),
                              old_c, new_c)
            )
    for entry in kept:
        entry.pop("_norm", None)
    return kept, conflicts, duplicates


def _same_columns(a: list, b: list) -> bool:
    return [str(c).strip().lower() for c in (a or [])] == [
        str(c).strip().lower() for c in (b or [])
    ]


def merge_tables(all_tables: list[dict]) -> tuple[list[dict], int]:
    """Concatenate tables; merge continuations.

    A table continues the previous one when its columns match (a repeated
    header on the next page) or it has no header of its own (headerless
    continuation rows). Rows already seen (overlap windows) are not repeated.
    """
    merged: list[dict] = []
    merged_count = 0
    for item in all_tables:
        if not isinstance(item, dict):
            continue  # lenient: never crash on junk
        columns = [c for c in (item.get("columns") or [])]
        rows = [r for r in (item.get("rows") or [])]
        if not merged:
            merged.append(dict(item))
            continue
        prev = merged[-1]
        prev_rows = [tuple(str(c) for c in r) for r in (prev.get("rows") or [])]
        is_continuation = (
            (columns and _same_columns(columns, prev.get("columns") or []))
            or not columns
        )
        if is_continuation:
            new_rows = [
                r for r in rows
                if tuple(str(c) for c in r) not in set(prev_rows)
            ]
            prev["rows"] = list(prev.get("rows") or []) + new_rows
            if columns and not (prev.get("columns")):
                prev["columns"] = columns
            merged_count += 1
            continue
        merged.append(dict(item))
    return merged, merged_count


def reduce_windows(window_results: list[dict]) -> ReduceResult:
    """Merge every window's raw JSON into one document-level result."""
    fields, conflicts, duplicates = merge_fields(
        [f for r in window_results for f in (r.get("fields") or [])]
    )
    tables, merged_tables = merge_tables(
        [t for r in window_results for t in (r.get("tables") or [])]
    )
    return ReduceResult(
        fields=fields, tables=tables, conflicts=conflicts,
        duplicate_fields=duplicates, merged_tables=merged_tables,
    )

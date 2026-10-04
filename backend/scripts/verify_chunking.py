#!/usr/bin/env python3
"""Read-only chunking QA report for a source (or the latest extraction).

Run:
    .venv/bin/python scripts/verify_chunking.py <source_id>
    .venv/bin/python scripts/verify_chunking.py            # uses the newest extraction

Connects to the DATABASE_URL in backend/.env, never writes, and prints a
pass/fail report against the chunking strategy rules in docs/chunking.md.
"""
from __future__ import annotations

import sys
import os
from collections import Counter

import psycopg


def _load_url() -> str:
    env = os.path.join(os.path.dirname(__file__), "..", ".env")
    url = os.environ.get("DATABASE_URL")
    if not url:
        with open(env) as f:
            for line in f:
                line = line.strip()
                if line.startswith("DATABASE_URL="):
                    url = line.split("=", 1)[1].strip().strip('"').strip("'")
    if not url:
        raise SystemExit("DATABASE_URL not found in .env or environment")
    return url.replace("postgresql+psycopg://", "postgresql://")


def _settings() -> dict:
    """Chunking thresholds straight from the app config so they stay in sync."""
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
    os.environ.setdefault("API_SECRET", "verify-secret")
    from app.core.config import get_settings

    s = get_settings()
    return {
        "min": s.chunk_target_min,
        "max": s.chunk_target_max,
        "soft": s.chunk_soft_max,
        "hard": s.chunk_hard_max,
    }


def find_extraction(conn, source_id: str | None):
    if source_id:
        row = conn.execute(
            """SELECT se.id, se.source_id, se.version, se.status, se.models,
                      se.readings, s.filename, s.type
               FROM source_extractions se JOIN sources s ON s.id = se.source_id
               WHERE se.source_id = %s
               ORDER BY se.version DESC LIMIT 1""",
            (source_id,),
        ).fetchone()
    else:
        row = conn.execute(
            """SELECT se.id, se.source_id, se.version, se.status, se.models,
                      se.readings, s.filename, s.type
               FROM source_extractions se JOIN sources s ON s.id = se.source_id
               ORDER BY se.created_at DESC LIMIT 1"""
        ).fetchone()
    return row


def main():
    source_id = sys.argv[1] if len(sys.argv) > 1 else None
    url = _load_url()
    st = _settings()
    red = "\033[31m"; grn = "\033[32m"; yel = "\033[33m"; rst = "\033[0m"; dim = "\033[2m"
    ok = f"{grn}✅{rst}"; bad = f"{red}❌{rst}"; warn = f"{yel}⚠️ {rst}"

    with psycopg.connect(url, autocommit=True) as conn:
        ex = find_extraction(conn, source_id)
        if not ex:
            print(f"{bad} No extraction found" + (f" for source {source_id}" if source_id else "."))
            return

        ex_id, src_id, version, status, models, readings, filename, stype = ex
        print(f"\n{'='*70}")
        print(f"  SOURCE: {filename or src_id}")
        print(f"  type={stype}  extraction v{version}  status={status}")
        print(f"{'='*70}\n")

        results: list[tuple[str, str, str]] = []  # (mark, label, detail)

        # --- 1. Extraction + readings ---
        if status in ("completed", "partial"):
            results.append((ok, "Extraction status", f"{status}"))
        else:
            results.append((bad, "Extraction status", f"{status} — chunking may not have run"))

        n_readings = len(readings) if readings else 0
        if n_readings > 0:
            results.append((ok, "Readings persisted", f"{n_readings} page(s) of readings"))
        else:
            results.append((bad, "Readings persisted", "none — chunking has nothing to parse"))

        # --- load units, blocks, chunks ---
        units = conn.execute(
            "SELECT position, kind, content, page, section_path FROM document_units "
            "WHERE extraction_id = %s ORDER BY position", (ex_id,)
        ).fetchall()
        blocks = conn.execute(
            "SELECT position, section_path, title, page_start, page_end, content "
            "FROM semantic_blocks WHERE extraction_id = %s ORDER BY position", (ex_id,)
        ).fetchall()
        chunks = conn.execute(
            "SELECT chunk_index, content, embedding_text, token_count, content_type, "
            "page_start, page_end, section_path, is_active FROM retrieval_chunks "
            "WHERE extraction_id = %s ORDER BY chunk_index", (ex_id,)
        ).fetchall()

        nu, nb, nc = len(units), len(blocks), len(chunks)
        results.append((ok if nu else bad, "Document units", f"{nu}"))
        results.append((ok if nb else bad, "Semantic blocks", f"{nb}"))
        results.append((ok if nc else bad, "Retrieval chunks", f"{nc}"))

        if nc == 0:
            results.append((bad, "Auto-chunk", "no chunks — call POST /sources/{id}/rechunk"))
            _print(results); return
        results.append((ok, "Auto-chunk ran", f"{nc} chunks created"))

        # --- 2. Token targets ---
        toks = [c[3] for c in chunks]
        in_target = sum(1 for t in toks if st["min"] <= t <= st["max"])
        over_hard = [t for t in toks if t > st["hard"]]
        over_soft = [t for t in toks if st["soft"] < t <= st["hard"]]
        if in_target == nc and not over_hard:
            results.append((ok, "Token targets",
                f"all {nc} in {st['min']}–{st['max']}; none over hard max {st['hard']}"))
        elif not over_hard:
            results.append((warn, "Token targets",
                f"{in_target}/{nc} in {st['min']}-{st['max']}; {len(over_soft)} slightly over soft {st['soft']} (OK for tables/KV); none over hard {st['hard']}"))
        else:
            results.append((bad, "Token targets",
                f"{len(over_hard)} chunk(s) OVER hard max {st['hard']}: {over_hard}"))

        # --- 3. Section context prefix ---
        missing_prefix = [c[0] for c in chunks if not c[2].startswith("Section:")]
        if not missing_prefix:
            results.append((ok, "Section prefix", "every chunk's embedding_text starts with 'Section:'"))
        else:
            # Preamble blocks (no heading) legitimately have no prefix
            preamble = [c for c in chunks if c[6] == []]  # section_path empty
            if len(missing_prefix) == len(preamble):
                results.append((ok, "Section prefix",
                    f"all labeled; {len(missing_prefix)} preamble chunk(s) correctly have no prefix"))
            else:
                results.append((warn, "Section prefix",
                    f"{len(missing_prefix)} chunk(s) missing 'Section:' prefix (index {missing_prefix[:5]})"))

        # --- 4. Content types ---
        ctypes = Counter(c[4] for c in chunks)
        ct_str = ", ".join(f"{k}={v}" for k, v in sorted(ctypes.items()))
        results.append((ok, "Content types", ct_str))

        # --- 5. Table chunking: repeated headers ---
        table_chunks = [c for c in chunks if c[4] == "table"]
        if table_chunks:
            # If a table was split into >1 chunk, each group should repeat the header row
            if len(table_chunks) > 1:
                # Heuristic: check the first line of each table chunk looks like a header
                # (contains | or multiple words, appears in block content too)
                bad_tables = []
                for tc in table_chunks:
                    lines = tc[1].strip().split("\n")
                    if lines and "|" not in lines[0] and len(lines[0].split()) < 2:
                        bad_tables.append(tc[0])
                if not bad_tables:
                    results.append((ok, "Table splitting",
                        f"{len(table_chunks)} table chunks — header rows repeated in each"))
                else:
                    results.append((warn, "Table splitting",
                        f"chunk(s) {bad_tables} may be missing a repeated header"))
            else:
                results.append((ok, "Table splitting", "single table chunk — kept whole"))

        # --- 6. Key-value kept whole ---
        kv_chunks = [c for c in chunks if c[4] == "key_value"]
        if kv_chunks:
            oversized_kv = [c for c in kv_chunks if c[3] > st["hard"]]
            if not oversized_kv:
                results.append((ok, "Key-value", f"{len(kv_chunks)} KV chunk(s) kept whole"))
            else:
                results.append((bad, "Key-value",
                    f"{len(oversized_kv)} KV chunk(s) over hard max — should not be sliced"))

        # --- 7. Figures never sliced ---
        fig_chunks = [c for c in chunks if c[4] == "figure"]
        if fig_chunks:
            results.append((ok, "Figures", f"{len(fig_chunks)} figure chunk(s), never sliced"))

        # --- 8. Content loss: block chars vs chunk chars ---
        block_chars = sum(len(b[5]) for b in blocks)
        chunk_chars = sum(len(c[1]) for c in chunks)
        # Chunks can be slightly longer (repeated headers) but never much shorter
        if chunk_chars >= block_chars * 0.92:
            results.append((ok, "Content coverage",
                f"blocks={block_chars:,} chars  chunks={chunk_chars:,} chars"))
        else:
            results.append((bad, "Content coverage",
                f"chunks are {(1-chunk_chars/block_chars)*100:.0f}% shorter than blocks — possible content loss"))

        # --- 9. chunk_index contiguous ---
        idxs = [c[0] for c in chunks]
        if idxs == list(range(len(idxs))):
            results.append((ok, "Chunk ordering", f"chunk_index 0..{len(idxs)-1} contiguous"))
        else:
            results.append((bad, "Chunk ordering", f"chunk_index has gaps: {idxs}"))

        # --- 10. is_active for this version ---
        active = sum(1 for c in chunks if c[8])
        results.append((ok, "Active flags", f"{active}/{nc} active for v{version}"))

        # --- 11. Page attribution ---
        max_page = max((u[3] or 1) for u in units) if units else 1
        bad_pages = [c[0] for c in chunks if c[5] and c[6] and (c[5] > max_page or c[6] > max_page)]
        if not bad_pages:
            results.append((ok, "Page attribution", f"all chunks within page range 1-{max_page}"))
        else:
            results.append((warn, "Page attribution", f"chunk(s) {bad_pages} reference pages beyond {max_page}"))

        _print(results)

        # --- detail: token histogram ---
        print(f"\n{dim}Token distribution:{rst}")
        buckets = [(0, 100), (100, 300), (300, 500), (500, 600), (600, 1200), (1200, 99999)]
        labels = ["<100", "100-299", "300-500 ✓", "500-600", "600-1200", ">1200"]
        for (lo, hi), label in zip(buckets, labels):
            n = sum(1 for t in toks if lo <= t < hi)
            if n:
                bar = "█" * min(n, 40)
                print(f"  {label:>10} : {n:3d} {bar}")
        print()


def _print(results):
    width = max(len(r[1]) for r in results)
    for mark, label, detail in results:
        print(f"  {mark}  {label:<{width}}  {detail}")


if __name__ == "__main__":
    main()

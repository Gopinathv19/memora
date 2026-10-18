"""Write extracted, resolved knowledge into the tenant graph (plan Steps 1-4).

Takes what `windows`, `extract` and `resolve` produced and lays it down in the shapes
the existing techniques already read, plus the new ones:

    (:Document)-[:CONTAINS]->(:Chunk {id: the retrieval chunk's own id})
    (:Chunk)-[:MENTIONS {description}]->(:Entity)                 entity under its survivor id
    (:Entity)-[:USES {source_id, chunk_ids, description}]->(:Entity)   typed edge, as before
    (:Fact {kind: 'atom'})-[:SUPPORTED_BY]->(:Chunk)              an atomic fact and its evidence
    (:Fact {kind: 'atom'})-[:ABOUT]->(:Entity)
    (:Merge {merged_id, survivor_id, stage, score, source_ids})   why two names became one node

Chunk ids are real `retrieval_chunks` ids, so a citation needs no bridge. A typed edge's
`description` holds its evidence quote and its `confidence` is 1.0: it was only written
because its quote was found verbatim in the text (see `extract`), which is a stronger
statement than a model's own score.

**Replacing a source.** Writing a source first removes what an earlier build of it left
(`delete_source` and its atomic facts), so a rebuilt document never keeps stale chunks. A
source whose windows all failed is skipped and left as it was: a failed rebuild never wipes
a working graph.

Everything is MERGE on deterministic ids, so writing the same batch twice changes nothing.
`GraphRagStore` is the only thing that touches FalkorDB.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict

from app.graph.ontology import Ontology
from app.graphrag import extract as _extract
from app.graphrag import resolve as _resolve
from app.graphrag.extract import WindowResult, fact_key, name_key
from app.graphrag.resolve import Cand, Resolution, make_cand
from app.graphrag.store import GraphRagStore

# Bump when the graph's shape changes (labels, properties, edge meaning).
SCHEMA_VERSION = 1


def current_signature(ontology: Ontology) -> str:
    """What a document was built with. A change in any part makes already built documents "outdated"
    (rebuilt only on request, because a changed prompt means paying for the extraction again)."""
    return f"schema{SCHEMA_VERSION}|{_extract.PROMPT_VERSION}|ontology{ontology.version}|{_resolve.JUDGE_VERSION}"


def chunk_fingerprint(chunk_ids) -> str:
    """Identifies a source's set of active chunks. A new extraction or a re-chunk changes it; so a graph document
    holding another fingerprint than Postgres has chunk ids that no longer exist."""
    return hashlib.sha1("|".join(sorted(str(c) for c in chunk_ids)).encode()).hexdigest()


def atom_id(source_id: str, text: str) -> str:
    """The same fact stated twice in one source is one node; its support accumulates."""
    return hashlib.sha1(f"atom|{source_id}|{fact_key(text)}".encode()).hexdigest()


def merge_id(merged_id: str, survivor_id: str) -> str:
    return hashlib.sha1(f"merge|{merged_id}|{survivor_id}".encode()).hexdigest()


def known_candidates(store: GraphRagStore, tenant_id, ontology: Ontology | None = None) -> list[Cand]:
    """Entities already in the tenant graph, as resolution candidates (their ids stay stable)."""
    ontology = ontology or store.base.ontology
    out = []
    for row in store.base.load_entities(tenant_id, list(ontology.entity_types)):
        keys = {row["canonical_key"], *(row.get("alias_keys") or [])} - {None, ""}
        out.append(Cand(
            id=row["id"], name=row["display_name"], entity_type=row["entity_type"], keys=keys,
            names=[row["display_name"]], known=True, mentions=1,
        ))
    return out


def _resolved_ids(result: WindowResult, tenant_id, id_map: dict[str, str]) -> dict[str, str]:
    """name (or alias) as the extractor wrote it -> the entity's survivor id."""
    out: dict[str, str] = {}
    for e in result.extraction.entities:
        cand = make_cand(tenant_id, e.name, e.entity_type, e.aliases)
        if cand is None:
            continue
        rid = id_map.get(cand.id, cand.id)
        out[name_key(e.name)] = rid
        for alias in e.aliases:
            out.setdefault(name_key(alias), rid)
    return out


def write_batch(
    store: GraphRagStore, tenant_id, results: list[WindowResult], resolution: Resolution, *,
    build_id: str | None = None, replace: bool = True, signature: str | None = None,
) -> Counter:
    """Write every source that has at least one successful window. Returns counts."""
    stats: Counter = Counter()
    ontology = store.base.ontology
    signature = signature or current_signature(ontology)
    by_source: dict[str, list[WindowResult]] = defaultdict(list)
    for r in results:
        by_source[r.window.source_id].append(r)

    writable: dict[str, list[WindowResult]] = {}
    for source_id, rs in by_source.items():
        if any(r.ok for r in rs):
            writable[source_id] = rs
        else:
            stats["sources_skipped_all_windows_failed"] += 1
    if not writable:
        return stats

    # 1. documents and chunks (replacing what an earlier build left). The document is NOT yet marked as built:
    # that happens last, so a failure in any later step leaves it looking out of date and it is retried.
    fingerprints: dict[str, str] = {}
    for source_id, rs in writable.items():
        w0 = rs[0].window
        if replace:
            # the store's delete hook also removes the source's atomic facts and unwinds its merges
            stats["replaced_nodes"] += sum(store.base.delete_source(tenant_id, source_id).values())
        store.base.upsert_document(tenant_id, {
            "id": source_id, "application_id": w0.application_id, "subject_id": w0.subject_id,
            "filename": w0.filename, "extraction_id": w0.extraction_id,
            "extraction_version": w0.source_version, "build_id": build_id,
        })
        chunk_rows = []
        for r in rs:
            for seg in r.window.segments:
                for c in seg.chunks:
                    chunk_rows.append({
                        "id": c.id, "index": c.index, "text": c.text, "page_start": c.page_start,
                        "page_end": c.page_end, "extraction_id": w0.extraction_id,
                        "source_version": w0.source_version, "subject_id": w0.subject_id,
                        "section_path": seg.section_path,
                    })
        store.upsert_retrieval_chunks(tenant_id, source_id, chunk_rows)
        stats["chunks"] += len(chunk_rows)
        fingerprints[source_id] = chunk_fingerprint(r["id"] for r in chunk_rows)
        stats["sources_written"] += 1

    # 2. entities under their survivor ids
    entity_rows = [{
        "id": c.id, "entity_type": c.entity_type, "canonical_key": c.key, "display_name": c.name,
        "aliases": [n for n in c.names if n != c.name],
        "alias_keys": sorted(k for k in c.keys if k != c.key), "raw_types": [],
    } for c in resolution.entities]
    store.base.upsert_entities(tenant_id, entity_rows)
    stats["entities"] = len(entity_rows)

    # 3. per source: mentions, typed edges, atomic facts
    for source_id, rs in writable.items():
        mentions: dict[tuple[str, str], dict] = {}
        edges: dict[tuple[str, str, str], dict] = {}
        atoms: dict[str, dict] = {}
        for r in (x for x in rs if x.ok):
            ids = _resolved_ids(r, tenant_id, resolution.id_map)
            ext = r.extraction
            for e in ext.entities:
                cand = make_cand(tenant_id, e.name, e.entity_type, e.aliases)
                if cand is None:
                    continue
                rid = resolution.id_map.get(cand.id, cand.id)
                for chunk_id in e.chunk_ids:
                    mentions.setdefault((chunk_id, rid), {"chunk_id": chunk_id, "entity_id": rid,
                                                          "description": e.description})
            for rel in ext.relations:
                a, b = ids.get(name_key(rel.source)), ids.get(name_key(rel.target))
                if a is None or b is None:
                    stats["relations_skipped_unresolved"] += 1
                    continue
                if a == b:
                    stats["relations_skipped_self_after_merge"] += 1
                    continue
                if not ontology.is_relation(rel.relation):
                    stats["relations_skipped_not_in_ontology"] += 1
                    continue
                row = edges.setdefault((rel.relation, a, b), {
                    "source": a, "target": b, "chunk_ids": [], "description": rel.quote,
                    "raw_relation": rel.raw_relation, "confidence": 1.0, "build_id": build_id,
                })
                row["chunk_ids"] += [c for c in rel.chunk_ids if c not in row["chunk_ids"]]
            for fact in ext.facts:
                aid = atom_id(source_id, fact.text)
                entity_ids = [ids[name_key(n)] for n in fact.entities if name_key(n) in ids]
                row = atoms.setdefault(aid, {
                    "id": aid, "text": fact.text, "evidence": fact.quote,
                    "qualifiers": json.dumps(fact.qualifiers, sort_keys=True) if fact.qualifiers else "",
                    "entity_ids": [], "chunk_ids": [],
                })
                row["entity_ids"] += [e for e in entity_ids if e not in row["entity_ids"]]
                row["chunk_ids"] += [c for c in fact.chunk_ids if c not in row["chunk_ids"]]

        store.base.upsert_mentions(tenant_id, source_id, list(mentions.values()))
        by_relation: dict[str, list[dict]] = defaultdict(list)
        for (relation, _a, _b), row in edges.items():
            by_relation[relation].append(row)
        for relation, rows in by_relation.items():
            store.base.upsert_relationships(tenant_id, source_id, relation, rows)
        store.upsert_atomic_facts(tenant_id, source_id, list(atoms.values()), build_id)
        stats["mentions"] += len(mentions)
        stats["relations"] += len(edges)
        stats["atomic_facts"] += len(atoms)
        if all(x.ok for x in rs):
            store.set_document_state(tenant_id, source_id, signature, fingerprints[source_id])
        else:
            # some windows failed: what was extracted is kept, but the document is not marked as built, so the
            # next index run sees a changed source and tries again instead of treating the gaps as final
            stats["sources_partial"] += 1

    # 4. the merge record
    store.upsert_merges(tenant_id, [{
        "id": merge_id(m.merged_id, m.survivor_id), "merged_id": m.merged_id,
        "survivor_id": m.survivor_id, "stage": m.stage, "score": float(m.score), "note": m.note,
        "source_ids": m.source_ids, "names": m.names, "keys": m.keys,
    } for m in resolution.merges])
    stats["merges"] = len(resolution.merges)
    return stats

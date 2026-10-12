"""Lexical (tsvector) search over retrieval_chunks -- the second branch of
hybrid retrieval (docs/embedding-retrieval.md).

Dense (HNSW) retrieval handles paraphrases and semantic similarity; this
branch handles exact identifiers, error codes, product names, acronyms and
rare proper nouns, where embeddings can dilute low-frequency tokens.

`websearch_to_tsquery` (not `to_tsquery`) converts raw user input: it never
raises on operators like AND / OR / quotes, and it maps quoted phrases to
phrase matching. The tsvector is generated with the 'simple' config (see the
lexical-search migration): no stemming, so technical tokens match exactly.

Ownership is enforced with the same predicates as the HNSW scan, so a query
can only ever see chunks the caller already owns.
"""

import logging
import uuid

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.stopwords import strip_stopwords
from app.services.scope import Scope

log = logging.getLogger(__name__)

# Optional scope filters are appended only when they have a value, exactly
# like the HNSW scan: an ``(:param IS NULL OR ...)`` clause fails with "could
# not determine data type of parameter" because psycopg sends NULL untyped.
def build_lexical_tsquery(query: str) -> str:
    """Build a 'simple'-config tsquery literal with OR semantics.

    websearch_to_tsquery ANDs every term ('total & amount & ...'), so a
    natural question matches nothing unless one chunk contains *all* the
    words -- the lexical branch then contributes zero candidates. Instead:
    strip stopwords, then OR the remaining terms ('total | amount | ...'),
    so any matching term scores a chunk and ts_rank_cd orders by term
    density. Each term is single-quoted with ' and \\ escaped, so raw user
    input can never inject tsquery syntax.

    Returns "" when nothing survives stopword stripping; the caller falls
    back to the raw query.
    """
    terms = [t for t in strip_stopwords(query).split() if t]
    if not terms:
        return ""
    quoted = [
        "'" + t.replace("\\", "\\\\").replace("'", "\\'") + "'"
        for t in terms
    ]
    return " | ".join(quoted)


_LEXICAL_SELECT = """
    SELECT rc.id AS chunk_id,
           rc.content,
           rc.embedding_text,
           rc.token_count,
           rc.content_type,
           rc.section_path,
           rc.page_start,
           rc.page_end,
           rc.source_id,
           rc.subject_id,
           rc.extraction_id,
           ts_rank_cd(
               rc.search_vector,
               to_tsquery('simple', :tsquery)
           ) AS lexical_score
    FROM retrieval_chunks rc
    JOIN chunk_embeddings ce ON ce.chunk_id = rc.id
    WHERE {filters}
    ORDER BY lexical_score DESC
    LIMIT :limit
"""


def lexical_search(
    db: Session,
    query: str,
    strategy_id: uuid.UUID,
    scope: Scope,
    subject_id: uuid.UUID | None = None,
    source_id: uuid.UUID | None = None,
    limit: int = 40,
) -> list[dict]:
    """Lexical branch of hybrid retrieval: top `limit` chunks by ts_rank_cd.

    Returns row mappings with the same chunk columns as the dense branch
    plus `lexical_score`. Only chunks with a completed embedding under the
    given strategy are considered, so both branches see the same corpus.
    """
    from app.services.retrieval_service import _scope_tenant_ids

    # OR-semantics tsquery from the stopword-stripped query (see
    # build_lexical_tsquery). Falls back to the raw query when nothing
    # survives stripping, so the branch never hard-fails.
    tsquery = build_lexical_tsquery(query) or "'" + query.replace("\\", "\\\\").replace("'", "\\'") + "'"

    filters = [
        "ce.embedding_status = 'completed'",
        "rc.is_active = TRUE",
        "ce.strategy_id = :strategy_id",
        "rc.tenant_id = ANY(:tenant_ids)",
        "rc.search_vector @@ to_tsquery('simple', :tsquery)",
    ]
    params = {
        "tsquery": tsquery,
        "strategy_id": strategy_id,
        "tenant_ids": _scope_tenant_ids(db, scope),
        "limit": limit,
    }
    application_id = None if scope.is_console else scope.application_id
    if application_id is not None:
        filters.append("rc.application_id = :application_id")
        params["application_id"] = application_id
    if subject_id is not None:
        filters.append("rc.subject_id = :subject_id")
        params["subject_id"] = subject_id
    if source_id is not None:
        filters.append("rc.source_id = :source_id")
        params["source_id"] = source_id

    rows = db.execute(
        text(_LEXICAL_SELECT.format(filters="\n      AND ".join(filters))),
        params,
    ).mappings()
    results = [dict(row) for row in rows]
    log.info(
        "lexical_search: query=%r tsquery=%r matches=%d",
        query[:80], tsquery[:200], len(results),
    )
    return results

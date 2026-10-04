"""Keep the graph in step with Postgres deletes and moves.

Postgres cascades; FalkorDB does not. So after a source or subject delete
commits, the deleted sources' documents, chunks and facts are removed from the
tenant graph, and after a file move its Document node's `subject_id` follows.

These run *after* the commit, like stored-bytes cleanup, and never raise: an
unreachable graph must not fail a delete that already happened. Anything missed
is caught by the startup sweep (`graph_service.sweep_orphaned_documents`).
Retrieval scopes by source ids resolved in Postgres, so a leftover node is
never returned in the meantime -- it is only wasted space.
"""

import logging
import uuid

from app.graph import store as graph_store

log = logging.getLogger(__name__)


def forget_sources(tenant_id: uuid.UUID, source_ids: list[uuid.UUID]) -> None:
    store = graph_store.get_graph_store()
    if store is None or not source_ids:
        return
    for source_id in source_ids:
        try:
            store.delete_source(tenant_id, str(source_id))
        except Exception:
            log.exception("could not remove source %s from the graph", source_id)


def move_sources(
    tenant_id: uuid.UUID, source_ids: list[uuid.UUID], subject_id: uuid.UUID
) -> None:
    store = graph_store.get_graph_store()
    if store is None or not source_ids:
        return
    try:
        store.set_document_subject(tenant_id, [str(s) for s in source_ids], str(subject_id))
    except Exception:
        log.exception("could not update the graph after moving %s", source_ids)

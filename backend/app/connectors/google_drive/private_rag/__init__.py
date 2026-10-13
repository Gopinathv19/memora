"""Private mode: zero-retention Q&A over Google Drive PDFs.

The customer picks Drive files and asks a question. The files are downloaded
into memory, parsed, read by the Extraction Agent, chunked, embedded and
answered from -- all inside the request, all in this process's memory. When
the request ends, every byte is garbage-collected.

The zero-retention contract, enforced by construction:

* the original files never leave the customer's Drive (no copy is made --
  not in object storage, not in a temp file, not in the database);
* no `sources`, `source_extractions`, `document_units`, `semantic_blocks`,
  `retrieval_chunks`, `chunk_embeddings`, `extraction_usage` or
  `drive_imports` row is ever written (the API test asserts exactly that);
* the only database rows this path ever touches are the console user's own
  Drive connection (read) and its token refresh (the connector's existing
  behaviour, unrelated to document content);
* there is no cache and no index: the next query for the same files reads
  them again. Reproducibility beats speed here by design.

The existing parse -> read -> chunk -> embed -> MMR -> rerank -> answer code
is reused unchanged; the only new things are where the bytes come from
(Drive, in memory) and where the chunks live (a Python list).
"""

from app.connectors.google_drive.private_rag import corpus, retrieval
from app.connectors.google_drive.private_rag.corpus import Corpus

__all__ = ["Corpus", "corpus", "retrieval", "run_private_query"]


def run_private_query(
    access_token: str,
    query: str,
    drive_file_ids: list[str],
    agent,
    settings=None,
) -> tuple[Corpus, dict]:
    """Build the ephemeral corpus, then answer from it. Nothing persists."""
    built = corpus.build_corpus(access_token, drive_file_ids, agent, settings)
    result = retrieval.answer_private_query(query, built.chunks, settings)
    return built, result

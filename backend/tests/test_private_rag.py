"""Private (zero-retention) Drive RAG.

Two layers of tests:

* Corpus/retrieval unit tests -- DB-free in themselves: Drive, embeddings,
  reranker and the answer LLM are faked; the Document Processor, the
  chunking pipeline and the ranking stages run for real.
* The API test -- the cardinal rule: a private query writes nothing,
  anywhere. It needs the throwaway Postgres like the rest of the suite and
  asserts every content table stays empty.
"""

from datetime import UTC, datetime, timedelta

import pytest

from app.agents.extraction_agent import AgentOutput, UnitReading
from app.connectors.google_drive import drive_client, file_discovery, oauth
from app.connectors.google_drive.private_rag import corpus, retrieval
from app.core.config import get_settings
from app.embeddings.provider import EmbeddingUsage
from app.llm.client import LLMUsage
from app.retrieval import answer as answer_module
from app.retrieval.reranker import RerankResult
from tests.extraction_fakes import LOREM, make_pdf, text_page

GARDENING = [
    "Completely different content about gardening and plants.",
    "Tomatoes need full sun and regular deep watering.",
    "Prune the roses in late winter before new growth.",
    "No invoices, totals or amounts appear on this page.",
    "Compost improves every soil type over time.",
]


# --- fakes --------------------------------------------------------------------


class FakeDrive:
    """Stands in for the Drive API: file metadata + bytes, no network."""

    def __init__(self, files: dict[str, tuple[str, bytes]]):
        self.files = files  # drive_file_id -> (file_name, pdf_bytes)

    def install(self, monkeypatch):
        monkeypatch.setattr(file_discovery, "get_pdf", self.get_pdf)
        monkeypatch.setattr(drive_client, "download_file", self.download_file)

    def get_pdf(self, access_token, drive_file_id):
        if drive_file_id not in self.files:
            raise drive_client.DriveAPIError("file not found", 404)
        name, data = self.files[drive_file_id]
        return drive_client.DriveFile(
            drive_file_id=drive_file_id,
            file_name=name,
            mime_type="application/pdf",
            modified_time="2026-01-01T00:00:00Z",
            size_bytes=len(data),
            downloadable=True,
        )

    def download_file(self, access_token, drive_file_id, dest, *, max_bytes):
        data = self.files[drive_file_id][1]
        dest.write(data)
        return len(data)


class FakeAgent:
    """Reads the document locally: one reading per unit, no model calls."""

    def extract(self, document, *, source_id, instructions=None):
        readings = [
            UnitReading(
                page=u.index,
                kind=u.kind,
                difficulty=u.difficulty.value,
                route=u.route.value,
                model=None,
                status="ok",
                text=u.text,
            )
            for u in document.units
        ]
        return AgentOutput(result=None, readings=readings)


class FakeEmbeddingProvider:
    """Deterministic bag-of-words vectors: shared words => shared dimensions."""

    provider = "fake"

    def __init__(self, dims: int = 128):
        self.dims = dims

    def _vec(self, text: str) -> list[float]:
        vector = [0.0] * self.dims
        for token in text.lower().split():
            vector[hash(token) % self.dims] += 1.0
        return vector

    def embed_documents(self, model, texts, dimensions):
        return [self._vec(t) for t in texts], EmbeddingUsage()

    def embed_query(self, model, text, dimensions):
        return self._vec(text), EmbeddingUsage()


class FakeReranker:
    model = "fake"

    def rerank(self, query, passages):
        return [
            RerankResult(index=i, score=float(len(passages) - i))
            for i in range(len(passages))
        ]


class FakeAnswerLLM:
    def chat_text(self, model, system, user):
        return "fake grounded answer", LLMUsage(
            prompt_tokens=10, completion_tokens=5, latency_ms=1
        )


@pytest.fixture
def private_fakes(monkeypatch):
    """Every network boundary faked: Drive, embeddings, reranker, answer LLM."""
    drive = FakeDrive(
        {
            "file-a": ("alpha.pdf", make_pdf([text_page(LOREM)])),
            "file-b": ("beta.pdf", make_pdf([text_page(GARDENING)])),
        }
    )
    drive.install(monkeypatch)
    monkeypatch.setattr(
        retrieval, "get_embedding_provider", lambda: FakeEmbeddingProvider()
    )
    monkeypatch.setattr(retrieval, "get_reranker", lambda: FakeReranker())
    monkeypatch.setattr(answer_module, "get_llm_client", lambda: FakeAnswerLLM())
    return drive


# --- corpus (DB-free) -----------------------------------------------------------


class TestCorpus:
    def test_chunks_carry_drive_provenance(self, private_fakes):
        built = corpus.build_corpus("token", ["file-a"], FakeAgent())
        assert built.files[0].status == "processed"
        assert built.chunks, "expected chunks from the PDF text"
        assert {c.drive_file_id for c in built.chunks} == {"file-a"}
        assert {c.file_name for c in built.chunks} == {"alpha.pdf"}
        assert built.pages_processed == 1

    def test_missing_file_is_reported_not_raised(self, private_fakes):
        built = corpus.build_corpus("token", ["nope", "file-a"], FakeAgent())
        assert built.files[0].status == "failed"
        assert built.files[1].status == "processed"

    def test_page_budget_skips_whole_files(self, private_fakes, monkeypatch):
        settings = get_settings()
        monkeypatch.setattr(settings, "private_rag_max_pages", 1)
        built = corpus.build_corpus(
            "token", ["file-a", "file-b"], FakeAgent(), settings
        )
        assert built.files[0].status == "processed"
        assert built.files[1].status == "skipped"
        assert "budget" in (built.files[1].detail or "")
        assert built.pages_processed == 1


# --- retrieval (DB-free) ----------------------------------------------------------


class TestRetrieval:
    def test_relevant_file_ranks_first(self, private_fakes):
        built = corpus.build_corpus("token", ["file-a", "file-b"], FakeAgent())
        result = retrieval.answer_private_query(
            "invoice total amount due", built.chunks
        )
        assert result["answer"] == "fake grounded answer"
        assert result["evidence"], "expected evidence chunks"
        assert result["evidence"][0].file_name == "alpha.pdf"
        assert result["usage"]["prompt_tokens"] == 10

    def test_empty_corpus_still_answers_honestly(self, private_fakes):
        result = retrieval.answer_private_query("anything", [])
        assert "does not contain" in result["answer"]
        assert result["evidence"] == []
        assert result["usage"]["prompt_tokens"] == 0


# --- the API: zero persistence (needs the throwaway Postgres) ---------------------


class TestPrivateQueryApi:
    CONTENT_TABLES = (
        "sources",
        "source_extractions",
        "document_units",
        "semantic_blocks",
        "retrieval_chunks",
        "chunk_embeddings",
        "extraction_usage",
        "drive_imports",
    )

    def test_private_query_writes_nothing(self, client, private_fakes):
        from sqlalchemy import text

        from app.agents.extraction_agent import get_extraction_agent
        from app.connectors.google_drive import service
        from app.db.database import SessionLocal
        from app.db.models.users import Users
        from app.main import app

        app.dependency_overrides[get_extraction_agent] = lambda: FakeAgent()
        connection_id = None
        try:
            with SessionLocal() as db:
                user = db.query(Users).filter_by(email="console@example.com").one()
                connection = service.save_connection(
                    db,
                    user_id=user.id,
                    tokens=oauth.DriveTokens(
                        access_token="fake-access-token",
                        refresh_token="fake-refresh-token",
                        expires_at=datetime.now(UTC) + timedelta(hours=1),
                        granted_scopes=oauth.DRIVE_SCOPE,
                    ),
                    account=oauth.DriveAccount(
                        google_user_id="g-123", email="drive@example.com"
                    ),
                )
                connection_id = connection.id

            response = client.post(
                "/api/v1/gdrive/query",
                json={
                    "query": "what is the invoice total?",
                    "drive_file_ids": ["file-a", "file-b"],
                },
            )
            assert response.status_code == 200, response.text
            body = response.json()
            assert body["answer"] == "fake grounded answer"
            assert [f["status"] for f in body["files"]] == [
                "processed",
                "processed",
            ]
            assert body["evidence"], "expected evidence chunks"

            # The cardinal rule: nothing was persisted, anywhere.
            with SessionLocal() as db:
                for table in self.CONTENT_TABLES:
                    count = db.execute(
                        text(f"SELECT count(*) FROM {table}")
                    ).scalar()
                    assert count == 0, f"private query wrote to {table}"
        finally:
            app.dependency_overrides.pop(get_extraction_agent, None)
            if connection_id is not None:
                with SessionLocal() as db:
                    service.disconnect(db, connection_id, user.id)

    def test_query_requires_connection(self, client, private_fakes):
        response = client.post(
            "/api/v1/gdrive/query",
            json={"query": "anything", "drive_file_ids": ["file-a"]},
        )
        assert response.status_code == 404

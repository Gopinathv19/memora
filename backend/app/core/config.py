from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application configuration, read from the environment / backend/.env."""

    model_config = SettingsConfigDict(
        env_file=(Path(__file__).resolve().parents[2] / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # local      : build.nvidia.com for every model call, files on local disk.
    # production : Nebius Token Factory for text models, files in Cloudflare R2.
    # Nebius serves no NVIDIA image, embedding or rerank model, so those calls
    # stay on build.nvidia.com in both environments (see llm_provider_for).
    environment: Literal["local", "production"] = "local"

    database_url: str = "postgresql+psycopg://user:password@localhost:5432/memora"
    #secret key used to hash every api credentials in the db
    api_secret: str = "change-me-in-production"
    # Local environment: where uploaded source files are written.
    storage_dir: str = "./var/storage"
    # Production environment: the Cloudflare R2 bucket (S3-compatible API).
    r2_endpoint_url: str = ""
    r2_access_key_id: str = ""
    r2_secret_access_key: str = ""
    r2_bucket: str = "memora-documents"
    cors_origins: str = "http://localhost:3000,http://127.0.0.1:3000"

    # Prefix carried by every raw API token Memora issues.
    token_prefix: str = "memora_"


    # console session stamping
    auth_jwt_secret: str = "change-me-in-production"
    auth_jwt_issuer: str = "memora"
    auth_token_ttl_minutes: int = 60 * 24 * 7  # one week

    auth_cookie_name: str = "memora_session"
    auth_cookie_secure: bool = False
    auth_cookie_samesite: str = "lax"

    # Google sign-in is the only console login. Required: the backend checks
    # every Google ID token was minted for this client id. No client secret
    # is needed for the ID-token flow.
    google_client_id: str = ""

    # --- Extraction Agent (docs/extraction-agent.md) -------------------------
    # Two OpenAI-compatible providers, both serving NVIDIA open models.
    # Which one a call goes to follows from `environment` (llm_provider_for).
    nvidia_api_key: str = ""
    nvidia_base_url: str = "https://integrate.api.nvidia.com/v1"
    nebius_api_key: str = ""
    nebius_base_url: str = "https://api.tokenfactory.nebius.com/v1/"

    # One model per role. Every one must be an NVIDIA model (`nvidia/...`).
    llm_extract_model: str = "nvidia/nemotron-3-super-120b-a12b"
    llm_vision_model: str = "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning"
    llm_layout_model: str = "nvidia/nemotron-parse"
    # Grounded-answer generation for the retrieval pipeline (POST /query).
    llm_answer_model: str = "nvidia/nemotron-3-super-120b-a12b"

    llm_timeout_seconds: float = 120.0
    llm_max_retries: int = 3
    llm_max_output_tokens: int = 8192
    # The operator's versioned price list (see app/core/pricing.py). A file in
    # the backend, not a setting any console user or credential can reach.
    pricing_file: str = str(Path(__file__).resolve().parents[2] / "pricing.json")

    extraction_max_pages: int = 30
    extraction_max_images: int = 20
    extraction_concurrency: int = 4
    extraction_max_input_chars: int = 200_000
    # Long side, in pixels, of a rendered PDF page. 1800 keeps a Letter/A4 page
    # inside Nemotron-Parse's 1024x1280 .. 1648x2048 window.
    extraction_render_long_side: int = 1800

    # Page triage thresholds (EASY / MEDIUM / HARD).
    triage_min_text_chars: int = 200
    triage_image_ratio: float = 0.15
    triage_max_junk_ratio: float = 0.05
    triage_min_ruled_lines: int = 8
    triage_max_medium_images: int = 2

    # --- Knowledge graph (docs/graph-rag.md) ---------------------------------
    # FalkorDB connection, e.g. falkors://user:password@host:port for FalkorDB
    # Cloud (TLS) or falkor://localhost:6379 locally. Empty = graph disabled:
    # every graph endpoint answers 503 and nothing else is affected.
    falkordb_url: str = ""
    # Every graph Memora creates is named "{prefix}_tenant_{tenant uuid}". Tests
    # use their own random prefix, so they never touch real graphs.
    falkordb_graph_prefix: str = "memora"
    falkordb_timeout_ms: int = 30_000
    # The model that reads each chunk for entities and relationships.
    llm_graph_model: str = "nvidia/nemotron-3-super-120b-a12b"
    # The operator's entity / relationship types (see app/graph/ontology.py).
    graph_ontology_file: str = str(Path(__file__).resolve().parents[2] / "ontology.json")
    graph_chunk_chars: int = 3000
    graph_chunk_overlap: int = 300
    graph_concurrency: int = 4
    # Chunks read per build; the rest are recorded as failed, so a retry
    # picks them up rather than them being silently dropped.
    graph_max_chunks: int = 200
    # 0-100 similarity an unseen name needs to join an existing entity of the
    # same type. Deliberately strict: a false merge is worse than a duplicate.
    graph_fuzzy_threshold: float = 92.0

    # --- Chunking (docs/chunking.md) ------------------------------------------
    # Token targets for retrieval chunks. These are targets, not minimums: a
    # coherent 120-token section stays 120 tokens (RULE 6).
    chunk_target_min: int = 300
    chunk_target_max: int = 500
    chunk_soft_max: int = 600
    chunk_hard_max: int = 1200  # implementation safety limit
    chunk_overlap: int = 0      # selective; prefer context expansion later
    # Auto-chunk after a successful extraction. Can be disabled to chunk
    # only on explicit request (POST /sources/{id}/rechunk).
    chunk_on_extract: bool = True

    # --- Embeddings (docs/embeddings.md) ---------------------------------------
    # The provider is a label resolved by app/embeddings/providers.py; the
    # endpoint and key are what actually change between local NIM, Nebius-hosted
    # NIM and build.nvidia.com. Nothing here is ever sent to the frontend.
    embedding_provider: str = "nvidia_nim"
    embedding_model: str = "nvidia/nemotron-3-embed-1b"
    # The OpenAI-compatible embeddings endpoint. build.nvidia.com by default;
    # point NVIDIA_NIM_BASE_URL at a Nebius-hosted NIM or a local NIM for
    # production without touching application code.
    nvidia_nim_base_url: str = "https://integrate.api.nvidia.com/v1"
    embedding_batch_size: int = 64
    embedding_max_retries: int = 3
    # How often the background worker sweeps for pending embedding work.
    embedding_worker_interval_seconds: float = 5.0
    # How many pending rows one worker sweep claims (FOR UPDATE SKIP LOCKED).
    embedding_worker_claim_size: int = 500
    # HNSW index parameters, kept in settings so tuning is a config change.
    embedding_hnsw_m: int = 16
    embedding_hnsw_ef_construction: int = 64
    # Auto-embed after a successful chunking (the same way chunking follows
    # extraction). Can be disabled to embed only on explicit request.
    embed_on_chunk: bool = True

    # --- Retrieval (docs/retrieval.md) ------------------------------------------
    # The first-version pipeline, exactly as specified:
    #   query embedding -> HNSW top 50 -> MMR top 10 -> reranker top 5 -> LLM.
    # Every knob is a setting so tuning is a config change, never a code change.
    # HNSW candidate pool size (stage 1).
    retrieval_hnsw_top_k: int = 50
    # pgvector ef_search for the ANN scan; must be >= top_k for good recall.
    retrieval_hnsw_ef_search: int = 100
    # MMR: relevance/diversity trade-off and target size (stage 2).
    retrieval_mmr_lambda: float = 0.7
    retrieval_mmr_top_k: int = 10
    # Final context size after reranking (stage 3).
    retrieval_final_top_k: int = 5
    # NeMo Retriever reranking NIM. Hosted rerankers live under
    # {reranker_base_url}/{model}/reranking (a different host and path from
    # the chat/embedding APIs). The reranker is independent of the embedding
    # similarity by design; llama-nemotron-rerank-vl-1b-v2 is the current
    # hosted model (the older nv-rerankqa-* models are end-of-life).
    reranker_model: str = "nvidia/llama-nemotron-rerank-vl-1b-v2"
    reranker_base_url: str = "https://ai.api.nvidia.com/v1/retrieval/nvidia"
    reranker_max_passage_chars: int = 4000
    reranker_timeout_seconds: float = 30.0
    reranker_max_retries: int = 3

    @field_validator(
        "llm_extract_model",
        "llm_vision_model",
        "llm_layout_model",
        "llm_graph_model",
        "llm_answer_model",
    )
    @classmethod
    def _nvidia_models_only(cls, value: str) -> str:
        # The hackathon rule is NVIDIA open models only, on either platform.
        # Failing at startup is the only way to make that impossible to miss.
        value = value.strip()
        if not value.lower().startswith("nvidia/"):
            raise ValueError(
                f"{value!r} is not an NVIDIA model; model ids must start with 'nvidia/'"
            )
        return value

    @property
    def llm_provider(self) -> str:
        """The provider for text models (extract, graph, answer)."""
        return "nebius" if self.environment == "production" else "build-nvidia"

    def llm_provider_for(self, role: str) -> str:
        """The provider that serves a model role.

        Nebius has no NVIDIA vision or parse model, so the image roles stay on
        build.nvidia.com even in production. Every text role follows
        `llm_provider`.
        """
        return "build-nvidia" if role in ("layout", "vision") else self.llm_provider

    def llm_endpoint(self, provider: str) -> tuple[str, str]:
        """(base_url, api_key) for a provider."""
        if provider == "nebius":
            return self.nebius_base_url, self.nebius_api_key
        return self.nvidia_base_url, self.nvidia_api_key

    @property
    def llm_base_url(self) -> str:
        return self.llm_endpoint(self.llm_provider)[0]

    @property
    def llm_api_key(self) -> str:
        return self.llm_endpoint(self.llm_provider)[1]

    @property
    def llm_models(self) -> dict[str, str]:
        return {
            "layout": self.llm_layout_model,
            "vision": self.llm_vision_model,
            "extract": self.llm_extract_model,
        }

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()

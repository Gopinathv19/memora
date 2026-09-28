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

    database_url: str = "postgresql+psycopg://user:password@localhost:5432/memora"
    #secret key used to hash every api credentials in the db
    api_secret: str = "change-me-in-production"
    storage_dir: str = "./var/storage"
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

    google_client_id: str = ""

    # --- Extraction Agent (docs/extraction-agent.md) -------------------------
    # Two OpenAI-compatible providers, both serving NVIDIA open models.
    # build.nvidia.com is free and is the default for development; Nebius
    # Token Factory is what the demo runs on.
    llm_provider: Literal["build-nvidia", "nebius"] = "build-nvidia"
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
    # USD per 1M tokens, keyed by model id:
    #   {"nvidia/nemotron-3-super-120b-a12b": {"input": 0.3, "output": 0.9}}
    # Only applied on Nebius; build.nvidia.com calls are recorded as $0.
    llm_prices: dict[str, dict[str, float]] = {}

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
        "llm_extract_model", "llm_vision_model", "llm_layout_model", "llm_answer_model"
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
    def llm_base_url(self) -> str:
        return self.nebius_base_url if self.llm_provider == "nebius" else self.nvidia_base_url

    @property
    def llm_api_key(self) -> str:
        return self.nebius_api_key if self.llm_provider == "nebius" else self.nvidia_api_key

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

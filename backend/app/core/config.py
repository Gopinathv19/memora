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

    @field_validator("llm_extract_model", "llm_vision_model", "llm_layout_model")
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

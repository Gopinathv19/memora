from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application configuration, read from the environment / backend/.env."""

    model_config = SettingsConfigDict(
        env_file=(Path(__file__).resolve().parents[2] / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    database_url: str = "postgresql+psycopg://user:password@localhost:5432/memora"
    api_secret: str = "change-me-in-production"
    admin_api_key: str = ""
    storage_dir: str = "./var/storage"
    cors_origins: str = "http://localhost:3000,http://127.0.0.1:3000"

    # Prefix carried by every raw API token Memora issues.
    token_prefix: str = "memora_"

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()

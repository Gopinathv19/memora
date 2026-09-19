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

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()

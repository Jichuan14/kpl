from functools import lru_cache
from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SQLITE = f"sqlite:///{BACKEND_ROOT / 'data' / 'kpl_bp.db'}"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(BACKEND_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    database_url: str = DEFAULT_SQLITE
    comp_base_url: str = "https://prod.comp.smoba.qq.com"
    tga_base_url: str = "https://tga-openapi.tga.qq.com"
    sync_request_delay: float = 0.2
    cors_origins: str = "http://localhost:5173,http://localhost:3000"

    @field_validator("database_url")
    @classmethod
    def sqlite_only(cls, value: str) -> str:
        if not value.startswith("sqlite:"):
            raise ValueError("Only SQLite database URLs are supported")
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()

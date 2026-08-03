from functools import lru_cache
from pathlib import Path

from pydantic import SecretStr, field_validator
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
    moonshot_api_key: SecretStr | None = None
    kimi_base_url: str = "https://api.moonshot.ai/v1"
    kimi_model: str = "kimi-k2.6"
    kimi_timeout_seconds: float = 45.0
    kimi_max_tool_rounds: int = 3
    kimi_max_tool_calls: int = 8
    kimi_max_output_tokens: int = 600

    @field_validator("database_url")
    @classmethod
    def sqlite_only(cls, value: str) -> str:
        if not value.startswith("sqlite:"):
            raise ValueError("Only SQLite database URLs are supported")
        return value

    @field_validator("kimi_base_url")
    @classmethod
    def kimi_https_only(cls, value: str) -> str:
        if not value.startswith("https://"):
            raise ValueError("KIMI_BASE_URL must use HTTPS")
        return value.rstrip("/")

    @field_validator("kimi_timeout_seconds")
    @classmethod
    def valid_kimi_timeout(cls, value: float) -> float:
        if not 1 <= value <= 180:
            raise ValueError("KIMI_TIMEOUT_SECONDS must be between 1 and 180")
        return value

    @field_validator(
        "kimi_max_tool_rounds",
        "kimi_max_tool_calls",
        "kimi_max_output_tokens",
    )
    @classmethod
    def positive_kimi_limits(cls, value: int) -> int:
        if value < 1:
            raise ValueError("Kimi limits must be positive")
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()

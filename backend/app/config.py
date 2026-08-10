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
    coach_ip_requests_per_minute: int = 5
    coach_ip_requests_per_day: int = 50
    coach_server_requests_per_minute: int = 30
    coach_server_requests_per_day: int = 500
    coach_ip_max_active_requests: int = 1
    coach_server_max_active_requests: int = 4
    coach_trust_proxy_headers: bool = False
    simulation_ip_requests_per_minute: int = 30
    simulation_ip_requests_per_day: int = 500
    simulation_server_requests_per_minute: int = 60
    simulation_server_requests_per_day: int = 5_000
    simulation_ip_max_active_requests: int = 1
    simulation_server_max_active_requests: int = 2
    simulation_trust_proxy_headers: bool = False
    # Dedicated credential for the read-only macOS visitor widget.  It is
    # deliberately separate from management Basic Auth and must never be put
    # in frontend code.
    analytics_widget_token: SecretStr | None = None

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

    @field_validator("analytics_widget_token", mode="before")
    @classmethod
    def blank_analytics_widget_token_is_disabled(cls, value: object) -> object:
        # Environment files commonly represent an optional secret as an empty
        # assignment. Treat that as disabled instead of blocking app startup.
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("analytics_widget_token")
    @classmethod
    def valid_analytics_widget_token(cls, value: SecretStr | None) -> SecretStr | None:
        if value is not None and len(value.get_secret_value()) < 32:
            raise ValueError("ANALYTICS_WIDGET_TOKEN must be at least 32 characters")
        return value

    @field_validator(
        "kimi_max_tool_rounds",
        "kimi_max_tool_calls",
        "kimi_max_output_tokens",
        "coach_ip_requests_per_minute",
        "coach_ip_requests_per_day",
        "coach_server_requests_per_minute",
        "coach_server_requests_per_day",
        "coach_ip_max_active_requests",
        "coach_server_max_active_requests",
        "simulation_ip_requests_per_minute",
        "simulation_ip_requests_per_day",
        "simulation_server_requests_per_minute",
        "simulation_server_requests_per_day",
        "simulation_ip_max_active_requests",
        "simulation_server_max_active_requests",
    )
    @classmethod
    def positive_kimi_limits(cls, value: int) -> int:
        if value < 1:
            raise ValueError("Kimi limits must be positive")
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()

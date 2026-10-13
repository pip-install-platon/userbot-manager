import base64
from functools import lru_cache
from urllib.parse import quote_plus

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from core.constants import (
    DEFAULT_HEARTBEAT_INTERVAL_SECONDS,
    DEFAULT_HEARTBEAT_TTL_SECONDS,
    DEFAULT_STATUS_TTL_SECONDS,
    MtprotoTransport,
)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    postgres_host: str = "postgres"
    postgres_port: int = 5432
    postgres_user: str
    postgres_password: SecretStr
    postgres_db: str

    redis_url: str
    master_key: SecretStr

    api_id: int | None = None
    api_hash: SecretStr | None = None
    session_string: SecretStr | None = None
    bot_token: SecretStr | None = None
    mtproto_transport: MtprotoTransport = MtprotoTransport.DIRECT
    device_model: str | None = None
    system_version: str | None = None
    app_version: str | None = None
    lang_code: str = "en"

    ai_replies: bool = False
    openrouter_api_key: SecretStr | None = None
    openrouter_model: str = "openai/gpt-4o-mini"
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    ai_timeout_seconds: float = Field(default=30, gt=0)

    all_operators_busy_text: str = "Все операторы заняты, попробуйте позже."
    auto_assign: bool = True
    superadmin_telegram_id: int | None = None
    superadmin_display_name: str = "Superadmin"
    log_level: str = "INFO"
    media_max_bytes: int = Field(default=20 * 1024 * 1024, gt=0)
    status_ttl_seconds: int = Field(default=DEFAULT_STATUS_TTL_SECONDS, gt=0)
    heartbeat_ttl_seconds: int = Field(default=DEFAULT_HEARTBEAT_TTL_SECONDS, gt=0)
    heartbeat_interval_seconds: int = Field(default=DEFAULT_HEARTBEAT_INTERVAL_SECONDS, gt=0)

    @field_validator("device_model", "system_version", "app_version", mode="before")
    @classmethod
    def blank_optional_text(cls, value: object) -> object:
        if value is None:
            return None
        if isinstance(value, str):
            stripped = value.strip()
            return stripped or None
        return value

    @field_validator("openrouter_model")
    @classmethod
    def model_is_present(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("OPENROUTER_MODEL must not be empty")
        return stripped

    @field_validator("openrouter_base_url")
    @classmethod
    def openrouter_https(cls, value: str) -> str:
        stripped = value.strip().rstrip("/")
        if not stripped.startswith("https://"):
            raise ValueError("OPENROUTER_BASE_URL must use https")
        return stripped

    @field_validator("lang_code")
    @classmethod
    def normalize_lang_code(cls, value: str) -> str:
        normalized = value.strip().lower()
        if not normalized:
            raise ValueError("LANG_CODE must not be empty")
        return normalized

    @field_validator("log_level")
    @classmethod
    def normalize_log_level(cls, value: str) -> str:
        normalized = value.upper()
        if normalized not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            raise ValueError("LOG_LEVEL must be a standard logging level")
        return normalized

    @field_validator("master_key")
    @classmethod
    def master_key_is_32_bytes(cls, value: SecretStr) -> SecretStr:
        decode_master_key(value.get_secret_value())
        return value

    @property
    def database_url(self) -> str:
        user = quote_plus(self.postgres_user)
        password = quote_plus(self.postgres_password.get_secret_value())
        return (
            f"postgresql+asyncpg://{user}:{password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    def master_key_bytes(self) -> bytes:
        return decode_master_key(self.master_key.get_secret_value())


def decode_master_key(encoded: str) -> bytes:
    try:
        raw = base64.b64decode(encoded, validate=True)
    except Exception as exc:
        raise ValueError("MASTER_KEY must be valid base64") from exc
    if len(raw) != 32:
        raise ValueError("MASTER_KEY must decode to 32 bytes")
    return raw


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]

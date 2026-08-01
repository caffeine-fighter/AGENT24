"""Environment-backed settings for the API/runtime boundary."""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class RuntimeSettings(BaseSettings):
    """Load process variables and the local, untracked ``.env`` when present."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore", case_sensitive=False)

    openai_api_key: str | None = None
    openai_model: str | None = None
    github_token: str | None = None
    run_timeout_seconds: float = Field(default=45.0, validation_alias="AGENT24_RUN_TIMEOUT_SECONDS")
    raw_event_log_dir: str = Field(
        default="artifacts/raw-streams", validation_alias="RAW_EVENT_LOG_DIR"
    )
    web_origin: str = Field(default="http://localhost:5173", validation_alias="WEB_ORIGIN")


__all__ = ["RuntimeSettings"]

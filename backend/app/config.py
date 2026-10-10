"""Application configuration loaded from environment variables."""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


import os

# On Vercel, the local directory is read-only. We must write to /tmp.
_default_db = "sqlite:////tmp/finance_controller.db" if os.environ.get("VERCEL") else "sqlite:///./data/finance_controller.db"

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Database
    database_url: str = _default_db

    # Auth
    jwt_secret: str = "dev-secret-change-me"
    jwt_expires_minutes: int = 480

    # Reconciliation rules
    high_value_threshold: float = 50_000.0
    settlement_delay_window_days: int = 3
    bank_delay_window_days: int = 3

    # AI (optional — deterministic analyzer is used when no key is configured)
    ai_api_key: str | None = None
    ai_base_url: str = "https://api.openai.com/v1"
    ai_model: str = "gpt-4o-mini"
    ai_timeout_seconds: int = 30

    # Dev
    cors_origins: str = "*"


settings = Settings()
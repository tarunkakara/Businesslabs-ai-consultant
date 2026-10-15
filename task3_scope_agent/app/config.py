"""Configuration for Task 3: Meeting-to-Scope Agent."""

from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict


class ScopeSettings(BaseSettings):
    """Configuration settings for Meeting-to-Scope Agent."""

    base_dir: Path = Path(__file__).resolve().parent.parent.parent
    data_dir: Path = base_dir / "data"

    default_hourly_rate: float = 120.0
    require_human_sign_off: bool = True
    knowledge_brain_api_url: str = "http://127.0.0.1:8000"
    sqlite_db_path: str = str(base_dir / "data" / "scope_agent.db")

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )


settings = ScopeSettings()

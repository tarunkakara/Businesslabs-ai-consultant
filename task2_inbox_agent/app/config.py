"""Configuration for Task 2: Inbox Agent."""

from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict
from task2_inbox_agent.app.models import CompanyVoice


class InboxSettings(BaseSettings):
    """Configuration settings for Inbox Agent."""

    base_dir: Path = Path(__file__).resolve().parent.parent.parent
    data_dir: Path = base_dir / "data"

    # Autonomy boundaries
    autonomous_approval_streak: int = 20  # Exactly 20 required per category
    autonomous_sending_enabled: bool = False  # Defaults to False for safety
    quality_similarity_threshold: float = 0.95  # Threshold below which edit is marked changed

    # Tone / Voice default
    default_voice: CompanyVoice = CompanyVoice.PROFESSIONAL

    # Knowledge Brain API integration
    knowledge_brain_api_url: str = "http://127.0.0.1:8000"

    # SQLite DB for email threads & audit trail
    sqlite_db_path: str = str(base_dir / "data" / "inbox_audit.db")

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )


settings = InboxSettings()

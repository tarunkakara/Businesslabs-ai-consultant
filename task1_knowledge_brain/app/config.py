"""Configuration settings for Task 1: Python Knowledge Brain.

Uses Pydantic BaseSettings to read environment variables with sensible defaults
and explicit offline mock mode support.
"""

from pathlib import Path
from typing import Optional
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings with environment variable fallback."""

    # Project directories
    base_dir: Path = Path(__file__).resolve().parent.parent.parent
    data_dir: Path = base_dir / "data"

    # AI Provider configuration
    provider_mode: str = "mock"  # "mock" or "anthropic"
    anthropic_api_key: Optional[str] = None
    anthropic_model: str = "claude-3-5-sonnet-20241022"
    anthropic_max_tokens: int = 2048

    # Task 1 Knowledge Brain API
    knowledge_brain_host: str = "127.0.0.1"
    knowledge_brain_port: int = 8000
    knowledge_brain_api_url: str = "http://127.0.0.1:8000"

    # Crawler configuration
    crawl_sitemap_url: str = "https://support.brilliantdirectories.com/support/sitemap.xml"
    crawl_delay_seconds: float = 0.5
    crawl_batch_size: int = 10
    crawl_max_retries: int = 3

    # Storage paths
    chroma_persist_directory: str = str(base_dir / "data" / "chroma")
    sqlite_db_path: str = str(base_dir / "data" / "knowledge_brain.db")

    # RAG Grounding thresholds
    min_grounding_similarity: float = 0.35  # Threshold below which to refuse

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )

    @property
    def is_mock_mode(self) -> bool:
        """Returns True if explicitly set to mock or if Anthropic API key is absent."""
        return self.provider_mode.lower() == "mock" or not self.anthropic_api_key


# Singleton instance
settings = Settings()

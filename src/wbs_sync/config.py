"""Application settings loaded from environment variables (.env or container env)."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration. All values come from env vars (case-insensitive)."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- WBS API (source) ---
    wbs_base_url: str = Field(..., description="Base URL of the WBS API, e.g. http://10.0.0.1:8080")
    wbs_api_key: str = Field(..., description="x-api-key for the WBS API")
    wbs_page_size: int = Field(500, ge=1, le=10000)
    wbs_departments_path: str = Field("/api/departments", description="API 1: list parts")
    wbs_works_path: str = Field("/api/works/search", description="Default target (centralized) work codes")
    wbs_work_profiles_path: str = Field("/api/work-profiles", description="API 2: per-part work codes")

    # --- LangFlow API (destination) ---
    langflow_base_url: str = Field(..., description="Base URL of LangFlow, e.g. http://langflow:7860")
    langflow_api_key: str = Field(..., description="x-api-key for LangFlow v2 files API")
    langflow_file_name: str = Field(
        "wbs_agent_knowledge",
        description="Base name for uploads (no extension). Default file = this; "
        "per-part file = this + '_' + slug(part name).",
    )

    # --- Service behaviour ---
    sync_interval_hours: float = Field(6.0, gt=0)
    sync_run_on_start: bool = True
    sync_default_enabled: bool = Field(True, description="Also push the centralized default file")
    sync_max_retries: int = Field(3, ge=1)
    sync_retry_backoff: float = Field(5.0, ge=0)
    state_dir: str = "./data"
    log_level: str = "INFO"
    http_timeout: int = Field(30, ge=1)

    @field_validator("wbs_base_url", "langflow_base_url")
    @classmethod
    def _strip_trailing_slash(cls, v: str) -> str:
        return v.rstrip("/")

    @property
    def state_file(self) -> Path:
        return Path(self.state_dir) / "state.json"

    @property
    def changelog_file(self) -> Path:
        """Append-only JSONL changelog of real changes + upload outcomes."""
        return Path(self.state_dir) / "changelog.jsonl"

    def data_path_for(self, langflow_name: str) -> Path:
        """The 'newest' data file for a given target base name."""
        return Path(self.state_dir) / f"{langflow_name}.json"

    def temp_path_for(self, langflow_name: str) -> Path:
        """Transient candidate file for a given target base name."""
        return Path(self.state_dir) / f"{langflow_name}.tmp.json"


@lru_cache
def get_settings() -> Settings:
    """Cached settings accessor (constructed once per process)."""
    return Settings()

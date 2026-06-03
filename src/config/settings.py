"""Application settings, loaded from environment / ``.env``.

Uses ``pydantic-settings`` so every value is typed and validated once at
startup. Settings are injected (not imported as globals) wherever practical; a
cached accessor is provided for the composition root.
"""

from __future__ import annotations

from enum import Enum
from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class AdapterKind(str, Enum):
    """Which ERP transport adapter to use."""

    MOCK = "mock"
    REAL = "real"


class LogFormat(str, Enum):
    """Console (human) or JSON (machine) log rendering."""

    CONSOLE = "console"
    JSON = "json"


class Settings(BaseSettings):
    """Strongly-typed application configuration."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- ERP access ---------------------------------------------------------
    erp_adapter: AdapterKind = AdapterKind.MOCK
    erp_base_url: str = "http://127.0.0.1:8000"
    erp_username: str | None = "demo"
    erp_password: str | None = "demo"
    erp_auth_login_path: str = "/auth/login"
    erp_auth_logout_path: str = "/auth/logout"
    erp_timeout_seconds: float = 15.0
    erp_max_retries: int = 3

    # --- LLM (Ollama) -------------------------------------------------------
    ollama_model: str = "llama3.1:latest"
    ollama_base_url: str = "http://127.0.0.1:11434"
    ollama_temperature: float = 0.1
    # Generous timeout so a slow cold-start (model load) doesn't drop the
    # natural-language answer to the deterministic fallback.
    ollama_timeout_seconds: float = 300.0
    # Number of model layers to offload to the GPU. ``None`` lets Ollama decide.
    # Set to ``0`` to force CPU-only inference - required on old/unstable GPUs
    # (e.g. Maxwell CC 5.0) where the CUDA backend crashes during model load.
    ollama_num_gpu: int | None = None

    # --- web search (Tavily) ------------------------------------------------
    # Optional: when a key is present the assistant can search the web to answer
    # general-knowledge or real-time questions it cannot otherwise answer.
    tavily_api_key: str | None = None
    web_search_max_results: int = Field(default=5, ge=1, le=20)
    web_search_depth: str = "advanced"  # "basic" | "advanced"
    web_search_timeout_seconds: float = 20.0
    web_search_max_retries: int = Field(default=2, ge=0, le=5)

    # --- storage ------------------------------------------------------------
    sqlite_path: Path = Path("./data/agent.db")

    # --- cache --------------------------------------------------------------
    cache_ttl_seconds: float = 300.0
    cache_max_entries: int = 2048

    # --- relationship resolution -------------------------------------------
    max_rel_depth: int = Field(default=3, ge=1, le=10)

    # --- config file paths --------------------------------------------------
    api_registry_path: Path = Path("./config/api_registry.json")
    facets_path: Path = Path("./config/facets.yaml")
    semantic_catalog_path: Path = Path("./schema/semantic_catalog.yaml")

    # --- logging ------------------------------------------------------------
    log_level: str = "INFO"
    log_format: LogFormat = LogFormat.CONSOLE

    @property
    def auth_enabled(self) -> bool:
        """Authentication is attempted only when credentials are present."""
        return bool(self.erp_username and self.erp_password)

    @property
    def web_search_enabled(self) -> bool:
        """Web search is available only when a Tavily API key is configured."""
        return bool(self.tavily_api_key)

    def ensure_dirs(self) -> None:
        """Create any directories the app writes to (e.g. the SQLite folder)."""
        self.sqlite_path.parent.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    """Return a process-wide cached ``Settings`` instance."""
    settings = Settings()
    settings.ensure_dirs()
    return settings

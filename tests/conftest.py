"""Shared test fixtures and helpers."""

from __future__ import annotations

import pytest

from src.config.registry import Registry
from src.config.settings import Settings


@pytest.fixture
def settings() -> Settings:
    """Settings with auth disabled and a test base URL (no .env dependency)."""
    return Settings(
        erp_username=None,
        erp_password=None,
        erp_base_url="http://erp.test",
        erp_max_retries=1,
    )


@pytest.fixture
def registry(settings: Settings) -> Registry:
    """Registry loaded from the project's config files."""
    return Registry.from_settings(settings)

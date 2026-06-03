"""Dependency container shared by the pipeline nodes.

Holds the already-constructed collaborators (registry, services, planner, LLM,
memory). Nodes receive this container by constructor injection rather than
importing globals, keeping them testable and composable.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from src.config.registry import Registry
from src.config.settings import Settings
from src.llm.ollama_client import OllamaLLM
from src.memory.repository import MemoryRepository
from src.planner.llm_planner import LLMPlanner
from src.services.analytics_service import AnalyticsService
from src.services.api_client import ApiClient
from src.services.facet_service import FacetService
from src.services.web_search_service import WebSearchService


@dataclass
class PipelineDeps:
    """All collaborators a node might need, wired once at startup."""

    settings: Settings
    registry: Registry
    client: ApiClient
    facets: FacetService
    planner: LLMPlanner
    llm: OllamaLLM
    memory: MemoryRepository
    # Analytics ("SQL mode") engine; cheap to build, always present.
    analytics: AnalyticsService = field(default_factory=AnalyticsService)
    # Optional: present only when a Tavily API key is configured.
    web_search: WebSearchService | None = None

    @property
    def max_rel_depth(self) -> int:
        """Maximum hop depth for relationship resolution."""
        return self.settings.max_rel_depth

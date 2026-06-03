"""Dependency container shared by the pipeline nodes.

Holds the already-constructed collaborators (registry, services, planner, LLM,
memory). Nodes receive this container by constructor injection rather than
importing globals, keeping them testable and composable.
"""

from __future__ import annotations

from dataclasses import dataclass

from src.config.registry import Registry
from src.config.settings import Settings
from src.llm.ollama_client import OllamaLLM
from src.memory.repository import MemoryRepository
from src.planner.llm_planner import LLMPlanner
from src.services.api_client import ApiClient
from src.services.facet_service import FacetService


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

    @property
    def max_rel_depth(self) -> int:
        """Maximum hop depth for relationship resolution."""
        return self.settings.max_rel_depth

"""Graph assembly and composition root.

Wires the dependency container, builds the LangGraph ``StateGraph`` from the nine
pipeline nodes, and exposes a small :class:`AgentRuntime` async context manager
that owns the SQLite checkpointer, HTTP client, and memory connection.

The pipeline is mostly linear:

    clarification_resolver -> planner -> entity_resolver -> api_selector ->
    executor -> relationship_resolver -> join -> analytics -> context_builder ->
    response_validator -> response_generator -> memory_manager

with one branch: if entity resolution is ambiguous, the graph skips execution
and goes straight to ``context_builder`` to ask the user which entity they meant.
Each node degrades gracefully (no exceptions escape into the graph).
"""

from __future__ import annotations

from types import TracebackType
from typing import Any

import httpx
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.graph import END, START, StateGraph

from src.adapters.factory import build_adapter, build_http_client
from src.cache.memory_cache import TTLCache
from src.config.registry import Registry
from src.config.settings import Settings, get_settings
from src.graph.dependencies import PipelineDeps
from src.llm.ollama_client import OllamaLLM
from src.memory.sqlite_repository import SqliteMemoryRepository
from src.models.state import AgentState
from src.nodes.analytics import AnalyticsNode
from src.nodes.api_selector import ApiSelectorNode
from src.nodes.clarification import ClarificationResolverNode
from src.nodes.context_builder import ContextBuilderNode
from src.nodes.join import JoinNode
from src.nodes.entity_resolver import EntityResolverNode
from src.nodes.executor import ExecutorNode
from src.nodes.memory_manager import MemoryManagerNode
from src.nodes.planner import PlannerNode
from src.nodes.relationship_resolver import RelationshipResolverNode
from src.nodes.response_generator import ResponseGeneratorNode
from src.nodes.response_validator import ResponseValidatorNode
from src.observability.logging import configure_logging, get_logger
from src.planner.llm_planner import LLMPlanner
from src.services.api_client import ApiClient
from src.services.facet_service import FacetService
from src.services.web_search_service import WebSearchService

_log = get_logger("graph")


def _needs_clarification(state: AgentState) -> str:
    """Route to a clarification question when entity resolution is ambiguous."""
    if (state.get("clarification") or {}).get("needed"):
        return "clarify"
    return "continue"


def build_graph(deps: PipelineDeps) -> StateGraph:
    """Construct the (uncompiled) pipeline graph from the node set."""
    graph = StateGraph(AgentState)

    graph.add_node("clarification_resolver", ClarificationResolverNode(deps))
    graph.add_node("planner", PlannerNode(deps))
    graph.add_node("entity_resolver", EntityResolverNode(deps))
    graph.add_node("api_selector", ApiSelectorNode(deps))
    graph.add_node("executor", ExecutorNode(deps))
    graph.add_node("relationship_resolver", RelationshipResolverNode(deps))
    graph.add_node("join", JoinNode(deps))
    graph.add_node("analytics", AnalyticsNode(deps))
    graph.add_node("context_builder", ContextBuilderNode(deps))
    graph.add_node("response_validator", ResponseValidatorNode(deps))
    graph.add_node("response_generator", ResponseGeneratorNode(deps))
    graph.add_node("memory_manager", MemoryManagerNode(deps))

    graph.add_edge(START, "clarification_resolver")
    graph.add_edge("clarification_resolver", "planner")
    graph.add_edge("planner", "entity_resolver")
    # Ambiguous entity -> skip execution and ask the user; else continue.
    graph.add_conditional_edges(
        "entity_resolver",
        _needs_clarification,
        {"clarify": "context_builder", "continue": "api_selector"},
    )
    graph.add_edge("api_selector", "executor")
    graph.add_edge("executor", "relationship_resolver")
    graph.add_edge("relationship_resolver", "join")
    graph.add_edge("join", "analytics")
    graph.add_edge("analytics", "context_builder")
    graph.add_edge("context_builder", "response_validator")
    graph.add_edge("response_validator", "response_generator")
    graph.add_edge("response_generator", "memory_manager")
    graph.add_edge("memory_manager", END)
    return graph


def build_deps(
    settings: Settings,
    *,
    http_client: httpx.AsyncClient | None = None,
    memory: SqliteMemoryRepository | None = None,
) -> PipelineDeps:
    """Build the dependency container (without opening external resources).

    Useful in tests with an injected HTTP client / memory.
    """
    registry = Registry.from_settings(settings)
    client = http_client or build_http_client(settings)
    adapter = build_adapter(settings, client)
    cache = TTLCache(settings.cache_ttl_seconds, settings.cache_max_entries)
    api_client = ApiClient(registry, adapter, cache)
    facet_service = FacetService(registry, api_client)
    llm = OllamaLLM(settings)
    planner = LLMPlanner(registry, llm)
    memory_repo = memory or SqliteMemoryRepository(settings.sqlite_path)
    web_search = _build_web_search(settings)
    return PipelineDeps(
        settings=settings,
        registry=registry,
        client=api_client,
        facets=facet_service,
        planner=planner,
        llm=llm,
        memory=memory_repo,
        web_search=web_search,
    )


def _build_web_search(settings: Settings) -> WebSearchService | None:
    """Construct the web-search service when a Tavily key is configured."""
    if not settings.web_search_enabled or settings.tavily_api_key is None:
        return None
    return WebSearchService.from_api_key(
        settings.tavily_api_key,
        max_results=settings.web_search_max_results,
        search_depth=settings.web_search_depth,
        timeout_seconds=settings.web_search_timeout_seconds,
        max_retries=settings.web_search_max_retries,
    )


class AgentRuntime:
    """Owns process-lifetime resources and runs the compiled agent graph."""

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        configure_logging(self._settings)
        self._http: httpx.AsyncClient | None = None
        self._memory: SqliteMemoryRepository | None = None
        self._checkpointer_cm: Any = None
        self._app: Any = None

    async def __aenter__(self) -> "AgentRuntime":
        """Open resources and compile the graph with a SQLite checkpointer."""
        self._http = build_http_client(self._settings)
        self._memory = SqliteMemoryRepository(self._settings.sqlite_path)
        await self._memory.initialize()

        deps = build_deps(self._settings, http_client=self._http, memory=self._memory)

        self._checkpointer_cm = AsyncSqliteSaver.from_conn_string(str(self._settings.sqlite_path))
        checkpointer = await self._checkpointer_cm.__aenter__()
        self._app = build_graph(deps).compile(checkpointer=checkpointer)
        _log.info("runtime_ready", adapter=self._settings.erp_adapter.value, model=self._settings.ollama_model)
        return self

    async def ask(self, question: str, thread_id: str = "cli") -> dict[str, Any]:
        """Run one turn through the graph and return the final state."""
        if self._app is None:
            raise RuntimeError("runtime not started; use 'async with AgentRuntime()'")
        initial: AgentState = {"user_input": question, "thread_id": thread_id}
        config = {"configurable": {"thread_id": thread_id}}
        return await self._app.ainvoke(initial, config=config)

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        """Close all owned resources in reverse order."""
        if self._checkpointer_cm is not None:
            await self._checkpointer_cm.__aexit__(exc_type, exc, tb)
        if self._memory is not None:
            await self._memory.close()
        if self._http is not None:
            await self._http.aclose()

# Dynamic ERP Agent (LangGraph + Ollama)

A production-oriented, **terminal-based** AI agent that answers natural-language questions over a **dynamic ERP API catalog**. It plans structured execution steps, resolves named entities to IDs, calls APIs through a swappable adapter (auth + caching), resolves foreign keys into readable names (multi-hop, cycle-safe), compacts results into focused context, validates outcomes, and replies in the user's language.

No frontend. The LLM runs locally via [Ollama](https://ollama.com). Optional [Tavily](https://tavily.com) web search backs general-knowledge and real-time questions when configured.

**Repository:** https://github.com/Elmezo/AI-agent-for-ERP-System

For a full Arabic guide to project structure and which file to edit for each change, see **[PROJECT_GUIDE.md](PROJECT_GUIDE.md)**.

---

## Tech stack

| Layer | Choice |
|-------|--------|
| Runtime | Python 3.12+ |
| Orchestration | LangGraph 1.x + LangChain |
| LLM | Ollama (`langchain-ollama`) |
| ERP transport | `httpx` + adapter pattern (`mock` / `real`) |
| Mock backend | FastAPI + Uvicorn |
| Config / validation | Pydantic v2, `pydantic-settings`, PyYAML |
| Checkpointing | `langgraph-checkpoint-sqlite` |
| Long-term memory | SQLite (keyword search; pgvector-ready interface) |
| Web search (optional) | Tavily (`tavily-python`) |
| Resilience | `tenacity` retries on HTTP and web search |
| Logging | `structlog` (console or JSON) |
| Tests | `pytest`, `pytest-asyncio`, `respx` |

---

## Pipeline

Linear LangGraph flow (nine nodes). Each stage degrades gracefully so a single failure does not crash the REPL.

```
User
 -> Planner                 (long-term memory retrieval + structured ExecutionPlan;
                            LLM with rule-based fallback)
 -> Entity Resolver         (names -> ids via search APIs)
 -> API Selector            (registry + semantic catalog)
 -> Executor                (ERPAdapter, authenticated + cached)
 -> Relationship Resolver   (FK ids -> readable names; cycle-safe; MAX_REL_DEPTH hops)
 -> Context Builder         (compress; RECALL answers from conversation history)
 -> Response Validator      (empty / error / ok)
 -> Response Generator      (DATA summary | CHAT conversation | RECALL verbatim | web-grounded chat)
 -> Memory Manager           (persist useful long-term memories)
```

### Plan intents

The planner classifies every turn (structured JSON, never free text):

| Intent | Behaviour |
|--------|-----------|
| `data` | Execute API steps against the ERP catalog (default). |
| `recall` | Answer from conversation history (e.g. "what did I ask before?"). |
| `chat` | General assistant turn (greetings, knowledge); may use Tavily web search when enabled. |

---

## Key properties

- **Config-driven** — APIs in `config/api_registry.json`, facets and FK maps in `config/facets.yaml`, business concepts in `schema/semantic_catalog.yaml`. Swap ERPs by editing config, not agent code.
- **Adapter pattern** — `MockERPAdapter` / `RealERPAdapter` behind `ERPAdapter`. Switch with `ERP_ADAPTER` in `.env`.
- **API discovery** — Regenerate the registry from OpenAPI, Postman, or a manual JSON file.
- **Resilient planning** — Structured LLM output with JSON repair loop; rule-based `fallback_planner` when the model fails.
- **Entity + relationship resolution** — e.g. `ownerId: 7` → `owner: "Ahmed Mohamed"` using `config/facets.yaml`.
- **Conversation threads** — LangGraph SQLite checkpointer + stable CLI thread (`cli-default`); `/new` starts an isolated thread.
- **Optional web search** — Tavily for `CHAT` turns that need current or external knowledge (`TAVILY_API_KEY`).
- **Auth, cache, observability** — Bearer token lifecycle (`AuthManager`), TTL entity cache, per-stage `/trace` timings.

---

## Quick start

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

### 2. Install Ollama and pull a model

Default in `.env.example` is `llama3.1:latest` (good balance of quality and size):

```bash
ollama pull llama3.1:latest
```

Alternatives (set `OLLAMA_MODEL` in `.env`):

| Model | Notes |
|-------|--------|
| `llama2:7b` | Faster, lighter; weaker planning |
| `qwen3:8b` | Strong reasoning; thinking-model output is sanitized before JSON parse |
| `command-r7b-arabic:latest` | Better Arabic phrasing |

### 3. Configure environment

```bash
cp .env.example .env
```

Edit `.env` as needed. **Never commit `.env`** — it is gitignored.

Create the data directory (SQLite checkpoints + memory):

```bash
mkdir -p data
```

### 4. (Optional) Regenerate the API registry

```bash
python -m src.discovery.generator --source openapi config/sources/openapi.json
python -m src.discovery.generator --source postman config/sources/postman_collection.json
python -m src.discovery.generator --source manual config/api_registry.json
```

### 5. Start the mock ERP (terminal A)

```bash
python -m mock_erp.server
```

Serves `http://127.0.0.1:8000` with dummy JSON under `mock_erp/data/` (people, org units, systems, datasets, stakeholders, interfaces).

### 6. Run the agent (terminal B)

```bash
python -m src.cli
```

**CLI commands**

| Command | Description |
|---------|-------------|
| `/help` | Show commands |
| `/trace` | Pipeline trace and timings for the last answer |
| `/new` | New conversation thread (fresh checkpoint + memory scope) |
| `/exit` | Quit |

Example ERP questions: *Who owns the CRM system?*, *List datasets in the Finance org unit*, *Search for Ahmed*.

---

## Configuration reference

All settings load from `.env` via `src/config/settings.py`. Highlights:

### ERP

```env
ERP_ADAPTER=mock          # mock | real
ERP_BASE_URL=http://127.0.0.1:8000
ERP_USERNAME=demo
ERP_PASSWORD=demo
ERP_TIMEOUT_SECONDS=15
ERP_MAX_RETRIES=3
```

Leave username/password blank to skip authentication (mock allows anonymous reads).

### LLM (Ollama)

```env
OLLAMA_MODEL=llama3.1:latest
OLLAMA_BASE_URL=http://127.0.0.1:11434
OLLAMA_TEMPERATURE=0.1
OLLAMA_TIMEOUT_SECONDS=300
# OLLAMA_NUM_GPU=0    # force CPU on old/unstable GPUs (CUDA crash on load)
```

### Web search (optional)

```env
TAVILY_API_KEY=tvly-...
WEB_SEARCH_MAX_RESULTS=5
WEB_SEARCH_DEPTH=advanced   # basic | advanced
```

When `TAVILY_API_KEY` is empty, web search is disabled.

### Storage, cache, resolution

```env
SQLITE_PATH=./data/agent.db
CACHE_TTL_SECONDS=300
MAX_REL_DEPTH=3
LOG_LEVEL=INFO
LOG_FORMAT=console          # console | json
```

Paths to config files: `API_REGISTRY_PATH`, `FACETS_PATH`, `SEMANTIC_CATALOG_PATH`.

See `.env.example` for the full list.

---

## Switching to your real ERP

1. Set in `.env`:

```env
ERP_ADAPTER=real
ERP_BASE_URL=https://erp.yourcompany.com
ERP_USERNAME=...
ERP_PASSWORD=...
```

2. Import your ERP's OpenAPI or Postman export into `config/sources/`.
3. Regenerate `config/api_registry.json` with the discovery generator.
4. Align `config/facets.yaml` and `schema/semantic_catalog.yaml` with your domain.

No changes to LangGraph nodes are required.

---

## Mock data and facets

| Path | Role |
|------|------|
| `mock_erp/data/*.json` | Dummy ERP records |
| `config/sources/*` | OpenAPI / Postman samples for discovery |
| `config/api_registry.json` | Endpoint catalog (method, URL, params, facet) |
| `config/facets.yaml` | Facets: search/get/list APIs + FK relationships |
| `schema/semantic_catalog.yaml` | Business concepts mapped to APIs |

Configured facets today: **people**, **org_units**, **systems**, **datasets**. Additional endpoints (e.g. `systems.stakeholders`, `systems.interfaces`) live in the registry for nested API calls.

Replace dummy files with your exports; regenerate or hand-edit the registry and facet map as your ERP evolves.

---

## Tests

```bash
pytest
```

Coverage includes planners, executor, discovery, cache, facets, relationship resolver, response generator, conversation/recall, and web search (mocked).

---

## Project layout

```
.
├── config/
│   ├── api_registry.json      # API catalog consumed by the agent
│   ├── facets.yaml            # Facets + FK relationship map
│   └── sources/               # OpenAPI / Postman inputs for discovery
├── schema/
│   └── semantic_catalog.yaml  # Business concepts -> APIs
├── mock_erp/
│   ├── data/                  # Dummy JSON backends
│   └── server.py              # FastAPI mock ERP (:8000)
├── src/
│   ├── cli.py                 # Terminal REPL
│   ├── graph/                 # LangGraph builder + AgentRuntime
│   ├── nodes/                 # Pipeline nodes (planner … memory_manager)
│   ├── planner/               # LLM planner + fallback_planner
│   ├── adapters/              # mock / real ERP adapters
│   ├── auth/                  # Token lifecycle
│   ├── cache/                 # TTL in-memory cache
│   ├── config/                # Settings + registry loader
│   ├── discovery/             # OpenAPI / Postman / manual -> registry
│   ├── llm/                   # Ollama client + structured JSON repair
│   ├── memory/                # SQLite long-term memory
│   ├── models/                # Pydantic state, plan, API models
│   ├── observability/         # structlog + trace helpers
│   ├── prompts/               # Versioned prompt templates (*.md)
│   ├── services/              # ApiClient, FacetService, WebSearchService
│   └── tools/                 # Tool factory (extensibility)
├── tests/
├── .env.example
├── requirements.txt
└── pyproject.toml
```

---

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| Ollama `exit code 2` / GPU crash on model load | Set `OLLAMA_NUM_GPU=0` in `.env` for CPU-only inference |
| `Connection refused` on port 8000 | Start `python -m mock_erp.server` before the CLI |
| Empty or fallback-only plans | Try a larger model (`llama3.1`, `qwen3:8b`); check `OLLAMA_TIMEOUT_SECONDS` |
| Arabic garbled in Windows terminal | CLI calls UTF-8 reconfigure on stdout; use a UTF-8 font |
| Web answers stay generic | Add `TAVILY_API_KEY`; restart the CLI |

---

## License

Add a `LICENSE` file in the repository if you intend to open-source the project publicly.

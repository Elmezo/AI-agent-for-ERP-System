# Dynamic ERP Agent (LangGraph + Ollama)

A production-grade, terminal-based AI agent that answers natural-language questions over
a **dynamic ERP API catalog**. It plans, resolves named entities to IDs, selects and
executes APIs through a swappable adapter (with auth + caching), resolves foreign-key
relationships into readable names (multi-hop, cycle-safe), compacts the data into relevant
context, validates results, and replies in the user's language.

No frontend. Runs entirely from the terminal. LLM via [Ollama](https://ollama.com).

---

## Pipeline

```
User
 -> Planner            (LLM plan, rule-based fallback)
 -> Entity Resolver    (names -> ids via search APIs)
 -> API Selector       (registry + semantic catalog)
 -> Executor           (calls APIs via ERPAdapter, cached + authenticated)
 -> Relationship Resolver  (FK ids -> readable names, cycle-safe, max depth 3)
 -> Context Builder    (compress + keep only relevant data)
 -> Response Validator (empty vs error vs data)
 -> Response Generator (concise answer, user language)
 -> Memory Manager     (persist useful memories)
```

## Key properties

- **Dynamic / config-driven** – APIs live in `config/api_registry.json`, facets and
  relationships in `config/facets.yaml`, business concepts in
  `schema/semantic_catalog.yaml`. Add/replace APIs (or point at a different ERP)
  by editing config, not code.
- **Adapter pattern** – `MockERPAdapter` / `RealERPAdapter` behind an `ERPAdapter`
  protocol. Switch with a single `.env` value.
- **API Discovery** – import endpoints from OpenAPI/Swagger, a Postman collection, or a
  manual file and auto-generate the registry.
- **Resilient planning** – structured LLM output with a rule-based fallback so the
  pipeline never stalls on a bad model response.
- **Entity resolution & relationship resolution** – turns `ownerId: 7` into
  `owner: "Ahmed Mohamed"` across facets.
- **Caching, auth, observability** built in.

---

## Quick start

1. **Install dependencies** (Python 3.12+):

```bash
pip install -r requirements.txt
```

2. **Install + pull a model** with Ollama. The default is `llama2:7b`:

```bash
ollama pull llama2:7b
```

For noticeably better planning / entity-resolution quality, set
`OLLAMA_MODEL=qwen3:8b` (or `llama3.1:8b`) in `.env` once pulled.

3. **Configure**:

```bash
cp .env.example .env   # then edit if needed
```

4. **(Optional) Regenerate the API registry** from a discovery source:

```bash
python -m src.discovery.generator --source openapi config/sources/openapi.json
python -m src.discovery.generator --source postman config/sources/postman_collection.json
python -m src.discovery.generator --source manual  config/api_registry.json
```

5. **Run the mock ERP server** (terminal A):

```bash
python -m mock_erp.server
```

6. **Chat with the agent** (terminal B):

```bash
python -m src.cli
```

---

## Switching to your real ERP

Edit `.env`:

```env
ERP_ADAPTER=real
ERP_BASE_URL=https://erp.yourcompany.com
ERP_USERNAME=...
ERP_PASSWORD=...
```

Then replace the dummy data / discovery sources with your real ERP's OpenAPI or Postman
export and regenerate the registry. No agent code changes required.

---

## Replacing the dummy data

Everything under `mock_erp/data/*.json` and `config/sources/*` is **dummy placeholder
data**. Replace those files with your real exports. The registry (`config/api_registry.json`),
facet map (`config/facets.yaml`), and semantic catalog (`schema/semantic_catalog.yaml`)
describe the shape of your APIs and can be edited or regenerated at any time.

---

## Tests

```bash
pytest
```

---

## Project layout

See `config/`, `schema/`, `mock_erp/`, and `src/` for the full module breakdown. Each
sub-package (`discovery/`, `adapters/`, `auth/`, `cache/`, `observability/`, `services/`,
`tools/`, `llm/`, `planner/`, `nodes/`, `memory/`, `graph/`) has a single responsibility.

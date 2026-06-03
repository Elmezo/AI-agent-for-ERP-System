# دليل المشروع الشامل — مرجع تقني كامل

> **الغرض:** مرجع تقني شامل للمشروع — يغطي المعمارية، تدفق البيانات، كل وحدة برمجية (كلاسات/دوال/نماذج)، ملفات الإعداد، الخوارزميات، وأمثلة تدفق كاملة.
>
> للتشغيل السريع راجع `README.md`. هذا الملف هو المرجع العميق عند التعديل أو التصحيح.

## فهرس المحتويات

1. [نظرة عامة والمعمارية](#1-نظرة-عامة-والمعمارية)
2. [مخطط التدفق LangGraph](#2-مخطط-التدفق-langgraph)
3. [حالة الوكيل AgentState](#3-حالة-الوكيل-agentstate)
4. [نماذج البيانات (Pydantic)](#4-نماذج-البيانات-pydantic)
5. [مرجع العُقد (Nodes) بالتفصيل](#5-مرجع-العقد-nodes-بالتفصيل)
6. [طبقة التخطيط (Planner)](#6-طبقة-التخطيط-planner)
7. [طبقة الخدمات (Services)](#7-طبقة-الخدمات-services)
8. [طبقة النقل: Adapters + Auth + Cache + LLM](#8-طبقة-النقل-adapters--auth--cache--llm)
9. [الذاكرة (ثلاث مستويات)](#9-الذاكرة-ثلاث-مستويات)
10. [الاكتشاف (Discovery)](#10-الاكتشاف-discovery)
11. [الإعدادات Settings والمتغيرات البيئية](#11-الإعدادات-settings-والمتغيرات-البيئية)
12. [ملفات الإعداد بالتفصيل](#12-ملفات-الإعداد-بالتفصيل)
13. [Mock ERP](#13-mock-erp)
14. [CLI والمراقبة (Observability)](#14-cli-والمراقبة-observability)
15. [أمثلة تدفق كاملة لكل نوع سؤال](#15-أمثلة-تدفق-كاملة-لكل-نوع-سؤال)
16. [ماذا تعدّل؟ (دليل سريع)](#16-ماذا-تعدل-دليل-سريع)
17. [شجرة الملفات](#17-شجرة-الملفات)
18. [الاصطلاحات والقواعد المعمارية](#18-الاصطلاحات-والقواعد-المعمارية)
19. [الاختبارات](#19-الاختبارات)

---

## 1. نظرة عامة والمعمارية

المشروع **وكيل ذكاء اصطناعي طرفي (Terminal)** يجيب على أسئلة بالعربية أو الإنجليزية عن بيانات ERP. النمط المعماري:

```
Plan → Route → Execute → Respond
(تخطيط → توجيه → تنفيذ → إجابة)
```

دورة الحياة لكل سؤال:

1. **تخطيط** (`ExecutionPlan` بصيغة JSON منظمة — ليس نصًا حرًا).
2. **توجيه/تنفيذ APIs** من كتالوج ديناميكي (`api_registry.json`).
3. **معالجة** (حل المفاتيح الأجنبية → أسماء، joins، تحليلات).
4. **إجابة** موجزة مع تحقق من عدم الهلوسة، ثم تقييم الذاكرة.

لا توجد واجهة ويب. الـ LLM محلي عبر **Ollama**. الـ ERP إما **Mock** (`mock_erp`) أو **حقيقي** عبر `.env`.

### المكدّس التقني

| الطبقة | الاختيار |
|--------|----------|
| التشغيل | Python 3.12+ |
| التنسيق | LangGraph 1.x + LangChain |
| LLM | Ollama (`langchain-ollama`) |
| نقل ERP | `httpx` + نمط Adapter (`mock` / `real`) |
| Backend وهمي | FastAPI + Uvicorn |
| الإعداد/التحقق | Pydantic v2, `pydantic-settings`, PyYAML |
| Checkpointing | `langgraph-checkpoint-sqlite` |
| ذاكرة طويلة | SQLite (`aiosqlite`، بحث keyword) |
| بحث ويب (اختياري) | Tavily (`tavily-python`) |
| المرونة | `tenacity` (إعادة محاولة HTTP والبحث) |
| السجلات | `structlog` (console أو JSON) |
| الاختبارات | `pytest`, `pytest-asyncio`, `respx` |

### الطبقات (من الأعلى للأسفل)

```
┌─────────────────────────────────────────────────────────┐
│  cli.py              واجهة المحادثة في الطرفية           │
├─────────────────────────────────────────────────────────┤
│  graph/builder.py    تجميع LangGraph + AgentRuntime      │
│  nodes/*             مراحل الـ pipeline (بدون منطق ERP)  │
├─────────────────────────────────────────────────────────┤
│  planner/            إنتاج ExecutionPlan                 │
│  services/           منطق الأعمال (API، joins، تحليل)    │
├─────────────────────────────────────────────────────────┤
│  adapters/ + auth/   HTTP + توكنات ERP                   │
│  llm/                Ollama + JSON structured            │
├─────────────────────────────────────────────────────────┤
│  config/ + schema/   كتالوج APIs، facets، مفاهيم        │
│  mock_erp/           خادم تجريبي + JSON وهمي             │
└─────────────────────────────────────────────────────────┘
```

**قواعد ذهبية:**

- عُقد LangGraph (`nodes/`) **لا تستدعي APIs مباشرة** — تستدعي `services/` فقط.
- الـ adapters للنقل فقط (HTTP/Auth)، ولا تحتوي منطق أعمال.
- الخدمات **لا تُرجع استجابات HTTP خام** أبدًا — بل نماذج Pydantic (`ApiResult` …).
- **حقن التبعيات (DI):** كل ما تحتاجه العقد مُجمّع في `PipelineDeps` (`src/graph/dependencies.py`) ويُمرَّر للمُنشئ — لا globals.

---

## 2. مخطط التدفق LangGraph

التجميع في `src/graph/builder.py` عبر `build_graph(deps)`. التدفق خطي مع فرع واحد للتوضيح:

```mermaid
flowchart TD
    START([بداية]) --> CR[clarification_resolver]
    CR --> PL[planner]
    PL --> ER[entity_resolver]
    ER -->|ambiguous| CB[context_builder]
    ER -->|ok| AS[api_selector]
    AS --> EX[executor]
    EX --> RR[relationship_resolver]
    RR --> JN[join]
    JN --> AN[analytics]
    AN --> CB[context_builder]
    CB --> RV[response_validator]
    RV --> RG[response_generator]
    RG --> MM[memory_manager]
    MM --> END([نهاية])
```

### جدول العُقد: المدخلات والمخرجات من/إلى `AgentState`

| العقدة | الملف / الكلاس | يقرأ من الحالة | يكتب في الحالة | الوظيفة |
|--------|----------------|----------------|----------------|---------|
| `clarification_resolver` | `nodes/clarification.py` · `ClarificationResolverNode` | `clarification`, `user_input` | `user_input` (مُعاد صياغته), `clarification` | إن كان الرد اختيارًا لسؤال توضيح سابق («الأول»/«2»)، يعيد كتابة السؤال الأصلي بالاسم المختار |
| `planner` | `nodes/planner.py` · `PlannerNode` | `user_input`, `thread_id` | `plan`, `language`, `retrieved_memories`, `messages`, **يصفّر**: `errors`, `trace`, `resolved_entities`, `selected_apis`, `execution_results`, `resolved_results`, `analytics`, `context`, `validation` | يجلب الذاكرة طويلة المدى + يبني `ExecutionPlan` |
| `entity_resolver` | `nodes/entity_resolver.py` · `EntityResolverNode` | `plan`, `user_input` | `resolved_entities`, `clarification?`, `errors?` | ينفّذ خطوات `search` ويحوّل الأسماء → IDs، أو يطلب توضيحًا عند الغموض |
| `api_selector` | `nodes/api_selector.py` · `ApiSelectorNode` | `plan`, `resolved_entities` | `selected_apis`, `errors?` | يحوّل الخطوات (عدا search) إلى استدعاءات API ملموسة أو «focus markers» للمفاهيم الحقلية |
| `executor` | `nodes/executor.py` · `ExecutorNode` | `selected_apis` | `execution_results`, `errors?` | ينفّذ الاستدعاءات عبر `ApiClient`؛ يمرّر الـ focus markers بدون تنفيذ |
| `relationship_resolver` | `nodes/relationship_resolver.py` · `RelationshipResolverNode` | `execution_results` | `resolved_results` | يحوّل FK ids → أسماء مقروءة (متعدد القفزات، آمن ضد الدورات) |
| `join` | `nodes/join.py` · `JoinNode` | `plan`, `resolved_results` | `resolved_results` (يُلحق صفوف الربط), `errors?` | يربط صفوف خطوتين (cross-entity). يُرجع `{}` إن لم توجد خطوات join |
| `analytics` | `nodes/analytics.py` · `AnalyticsNode` | `plan`, `resolved_results` | `analytics` | count/sum/avg/min/max/group/top-N على صفوف list. يُرجع `{}` إن لم توجد aggregate |
| `context_builder` | `nodes/context_builder.py` · `ContextBuilderNode` | `plan`, `resolved_results`, `analytics`, `clarification`, `messages`, `retrieved_memories` | `context` | يضغط النتائج، أو يبني إجابة توضيح/recall/analytics حتمية |
| `response_validator` | `nodes/response_validator.py` · `ResponseValidatorNode` | `context`, `language` | `validation` | يصنّف: `ok` / `empty` / `error` / `no_plan` ويُعرّب الرسالة |
| `response_generator` | `nodes/response_generator.py` · `ResponseGeneratorNode` | `plan`, `validation`, `context`, `messages`, `language` | `final_response`, `messages` | الإجابة النهائية: LLM للبيانات/المحادثة، أو إجابة حتمية لـ recall/analytics/clarification |
| `memory_manager` | `nodes/memory_manager.py` · `MemoryManagerNode` | `validation`, `final_response`, `user_input`, `context`, `thread_id` | (يحفظ في الذاكرة؛ لا يعدّل الحالة عدا `trace`) | يحفظ حقائق مفيدة فقط حسب سياسة الأهمية |

**الفرع الشرطي** (`_needs_clarification` في `builder.py`): إذا `clarification.needed == true` بعد `entity_resolver` → يذهب مباشرة إلى `context_builder` (يتخطى `api_selector` … `analytics`).

**نقطة الدخول للتشغيل:** `AgentRuntime.ask(question, thread_id)` في `builder.py`:

```python
initial = {"user_input": question, "thread_id": thread_id}
config = {"configurable": {"thread_id": thread_id}}
return await self._app.ainvoke(initial, config=config)
```

`AgentRuntime` (مدير سياق async) يملك: `httpx.AsyncClient`، `SqliteMemoryRepository`، `AsyncSqliteSaver` (checkpointer)، والـ graph المُجمّع.

---

## 3. حالة الوكيل AgentState

الملف: `src/models/state.py` — `TypedDict(total=False)` ليتمكّن LangGraph من عمل checkpoint.

| الحقل | النوع | من يكتبه | المعنى |
|-------|-------|----------|--------|
| `user_input` | `str` | CLI / clarification_resolver | سؤال المستخدم الحالي (قد يُعاد كتابته بعد التوضيح) |
| `messages` | `list[Message]` (مُدمج `operator.add`) | planner, response_generator | تاريخ قصير المدى. **هذا الحقل الوحيد الذي يُلحَق (append) بدل الاستبدال** |
| `language` | `str` | planner | `ar` / `en` … |
| `trace_id` | `str` | — | معرّف تتبّع اختياري |
| `thread_id` | `str` | CLI | معرّف المحادثة (checkpoint + نطاق الذاكرة) |
| `retrieved_memories` | `list[dict]` | planner | ذاكرة طويلة المدى ذات صلة `{content, kind}` |
| `plan` | `dict` (`ExecutionPlan.model_dump`) | planner | خطة التنفيذ |
| `resolved_entities` | `dict[str, dict]` | entity_resolver | `step_id → {facet, id, label, record, [empty/ambiguous]}` |
| `clarification` | `dict` | entity_resolver / clarification_resolver | حالة التوضيح بين الجولات. **يُحفَظ عبر الجولات** (لا يُصفَّر بواسطة planner) |
| `selected_apis` | `list[dict]` | api_selector | الاستدعاءات/الـ markers المختارة |
| `execution_results` | `list[dict]` | executor | نتائج خام مُطبّعة (`ApiResult.model_dump`) |
| `resolved_results` | `list[dict]` | relationship_resolver, join | بعد حل العلاقات / الربط |
| `analytics` | `list[dict]` (`AnalyticsResult`) | analytics | نتائج aggregate |
| `context` | `dict` | context_builder | بيانات مضغوطة للإجابة (انظر أدناه) |
| `validation` | `dict` | response_validator | `{status, message, has_data}` |
| `final_response` | `str` | response_generator | النص المعروض للمستخدم |
| `errors` | `list[str]` | عدة عُقد | أخطاء غير قاتلة (تُصفَّر كل جولة) |
| `trace` | `list[TraceEntry]` | كل عقدة | `{stage, elapsed_ms, detail}` لكل مرحلة (`/trace`) |

### دلالات إعادة الضبط (مهمة جدًا)

LangGraph يحفظ الحالة لكل `thread_id` (checkpointer)، فأي حقل لا يُكتَب يبقى من الجولة السابقة. لذلك:

- **planner يصفّر** كل الحقول المشتقة في بداية كل جولة (`resolved_entities`, `selected_apis`, `execution_results`, `resolved_results`, `analytics`, `context`, `validation`, `errors`, `trace`). بدون ذلك تتسرّب نتائج جولة سابقة (مثل `analytics`) إلى سؤال متابعة غير متعلّق.
- `clarification` **لا يُصفَّر** في planner لأنه يُستهلَك/يُمسَح بواسطة `clarification_resolver` الذي يسبقه.
- `messages` يُلحَق عبر `operator.add` فلا يُمسَح أبدًا داخل نفس الـ thread (يُمسح فقط بـ `/new` الذي ينشئ thread جديدًا).

### شكل `context` (مخرج context_builder)

```python
{
  "goal": str,                # هدف الخطة
  "question": str,            # سؤال المستخدم
  "language": str,
  "focus": [                  # القيم المُركّزة التي طلبها المستخدم
    {"concept": "owner", "field": "ownerId", "value": "Ahmed Mohamed"},
    # أو {"concept": "analytics"|"recall"|"clarification", "field": ..., "value": "..."}
  ],
  "results": [ ... ],         # كتل نتائج مضغوطة (facet/api/status/count/items/item)
  "analytics": [ ... ],       # موجود فقط في مسار analytics
  "memories": [str, ...],     # محتوى الذاكرة المسترجعة
}
```

---

## 4. نماذج البيانات (Pydantic)

### `src/models/plan.py` — الخطة

**`PlanIntent`** (Enum): نيّة الجولة.

| القيمة | متى | أين تُجاب |
|--------|-----|-----------|
| `data` | سؤال عن ERP | الـ pipeline كامل |
| `recall` | سؤال عن المحادثة نفسها | `conversation.py` + context_builder (حتمي) |
| `chat` | تحية / معرفة عامة | response_generator + `chat.md` (+ Tavily اختياري) |

**`StepKind`** (Enum): `search` · `get_by_id` · `list` · `api` · `concept` · `aggregate` · `join`.

**`PlanStep`** — خطوة واحدة:

| الحقل | النوع | الوصف |
|-------|-------|-------|
| `id` | `int` | معرّف ثابت يبدأ من 1 (للاعتماديات) |
| `kind` | `StepKind` | نوع الخطوة |
| `facet` | `str \| None` | الكيان الهدف |
| `action` | `str \| None` | اسم API صريح أو اسم مفهوم |
| `query` | `str \| None` | نص البحث لخطوات `search` |
| `params` | `dict` | معاملات صريحة لخطوات `api` |
| `depends_on` | `list[int]` | معرّفات خطوات يعتمد عليها |
| `description` | `str` | شرح بشري |
| `aggregate` | `AggregateSpec \| None` | لخطوات `aggregate` فقط |
| `join` | `JoinSpec \| None` | لخطوات `join` فقط |

**`ExecutionPlan`**: `goal`, `steps`, `language="en"`, `intent=DATA`, `recall_topic`, `used_fallback`. الخاصية `is_empty` = `True` فقط حين `steps==[]` و`intent==DATA` (recall/chat يحملان إجابتهما في النيّة لا في الخطوات).

**`AggregateSpec`** — طلب تحليل تصريحي:

| الحقل | النوع | الوصف |
|-------|-------|-------|
| `op` | `AggregateOp` | `count`/`sum`/`avg`/`min`/`max` |
| `metric` | `str \| None` | الحقل الرقمي (لـ sum/avg/min/max ومفتاح ترتيب top-N) |
| `group_by` | `str \| None` | حقل التجميع (قيمة لكل مجموعة) |
| `filters` | `list[FilterClause]` | فلاتر قبل التجميع |
| `sort_desc` | `bool=True` | تنازلي للترتيب/المجموعات |
| `limit` | `int \| None` | N لـ top-N / bottom-N |

**`FilterClause`**: `{field, op: FilterOp, value}`؛ `FilterOp` = `eq`/`ne`/`gt`/`gte`/`lt`/`lte`/`contains`.

**`JoinSpec`** — ربط cross-entity:

| الحقل | الوصف |
|-------|-------|
| `left_step` / `left_key` | معرّف الخطوة اليسرى والحقل المفتاح |
| `right_step` / `right_key` | معرّف الخطوة اليمنى والحقل المفتاح |
| `how` | `JoinType`: `inner` (افتراضي) / `left` |
| `emit` | أي جانب يُرجَع: `right` (افتراضي) / `left` / `both` |
| `strategy` | تجاوز خوارزمية الربط (افتراضيًا `nested_loop`) |

### `src/models/api.py` — كتالوج API والنتائج

- **`HttpMethod`**: `GET`/`POST`/`PUT`/`PATCH`/`DELETE`.
- **`ApiEndpoint`** (frozen): `name`, `url`, `method`, `path_params`, `query_params`, `body_params`, `facet`, `description`. الدالة `required_params()` تُرجع `path_params`.
- **`RelationshipDef`** (frozen): `field`, `target_facet`, `target_field="id"`, `as_name`. الدالة `resolved_name()` تُنتج اسم القيمة المقروءة (مثل `ownerId → owner` عبر `as_name`، أو `createdBy → createdByName`).
- **`FacetDef`**: `name`, `business_name`, `primary_key="id"`, `display_fields`, `search_api`, `get_by_id_api`, `list_api`, `relationships`. الدالة `display_label(record)` تبني اسمًا مقروءًا من `display_fields` (أو `business_name #pk`).
- **`ApiStatus`**: `success`/`empty`/`error` (التمييز بين «نجح بلا بيانات» و«نجح ببيانات»).
- **`ApiResult`**: `api_name`, `status`, `data`, `error`, `status_code`, `elapsed_ms`, `from_cache`. خصائص `ok`/`is_empty`/`is_error`. مُنشِئات: `ApiResult.success(...)` (يصنّف الفراغ تلقائيًا) و`ApiResult.failure(...)`.

### `src/models/semantic.py` — الفهم الدلالي

- **`ConceptDef`** (frozen): `name`, `field`, `api`, `target`, `description`. مُتحقّق `_check_one_of`: يجب تعريف `field` **أو** `api`.
- **`FacetSemantics`**: `facet`, `business_name`, `concepts`. الدالة `find_concept(term)` بحث غير حسّاس لحالة الأحرف.
- **`SemanticCatalog`**: `facets`؛ الدوال `get(facet)`، `all_concept_terms()`.

### `src/models/analytics.py` — نتائج التحليل

- **`AnalyticsGroup`**: `{key, value, count}` (دلو واحد من تجميع/ترتيب).
- **`AnalyticsResult`**: `facet`, `op`, `metric`, `group_by`, `matched_rows`, `total_rows`, `value` (سُلّمي) أو `groups` (مُجمَّع/top-N)، `error`. الخاصية `ok`. **حارس الأمانة:** عند فشل (حقل مفقود/غير رقمي) يُضبَط `error` بدل اختلاق رقم.

### `src/models/web.py` — البحث على الويب

- **`WebResult`**: `{title, url, content, score}`.
- **`WebSearchResult`**: `query`, `answer`, `results`, `error`؛ خاصية `ok`.
- **`WebSearchDecision`**: `{needs_search: bool, query: str}` (مخرج LLM منظم).

### `src/memory/repository.py` — سجل الذاكرة

- **`MemoryRecord`**: `thread_id`, `content`, `kind` (`fact`/`preference`/`profile`/`insight`), `importance=0.5`, `metadata`, `created_at`, `id`.

---

## 5. مرجع العُقد (Nodes) بالتفصيل

كل عقدة كلاس قابل للاستدعاء `async def __call__(self, state) -> dict` يُرجع التعديلات فقط. كلها تتدهور برشاقة (لا ترفع استثناءات داخل الـ graph).

### `clarification_resolver` (`nodes/clarification.py`)

يحتوي أيضًا دوالًا نقية مشتركة:
- `disambiguate(facet, query, records, namer) -> (record|None, candidates|None)`: يُرجع سجلًا واحدًا عند التطابق الواضح، أو قائمة مرشحين عند الغموض. مطابقة دقيقة عبر `_is_exact`.
- `build_clarification(...)`: يبني payload التوضيح.
- `format_clarification(clarification, language)`: سؤال مُرقّم مُعرّب.
- `parse_selection(user_input, candidates)`: يفسّر رد المستخدم (ترتيبي `1`/`#2`/`first`/`الأول`، أو جزء اسم فريد).
- `rewrite_question(original, query, chosen_label)`: يستبدل المصطلح الغامض بالاسم المختار.

`ClarificationResolverNode.__call__`: إن لم يكن هناك توضيح معلّق → `{}`. إن كان الرد اختيارًا صالحًا → يعيد كتابة `user_input` ويمسح التوضيح. وإلا → يتجاهل التوضيح القديم ويُعامل الرسالة كسؤال جديد. (حدّ: 6 مرشحين `MAX_CANDIDATES`.)

### `planner` (`nodes/planner.py`)

يستدعي `_retrieve_memories` (best-effort) ثم `deps.planner.plan(question)`. **يصفّر الحالة المشتقة** ويُسجّل `messages` بدور المستخدم. لا يستدعي APIs.

### `entity_resolver` (`nodes/entity_resolver.py`)

لكل خطوة `search`: ينادي `facets.search`. يصنّف النتيجة: خطأ / فارغ / مرشّح واحد / غموض. عند الغموض يبني `clarification` ويتوقّف (`break` — توضيح واحد في كل مرة). يستخرج `id` عبر `primary_key` من الـ facet.

### `api_selector` (`nodes/api_selector.py`)

يحوّل كل خطوة (عدا search) إلى «إدخال اختيار»:
- `get_by_id`/`list`/`api`: استدعاء ملموس مع ربط معامل الـ id (`_resolve_id` يفضّل `depends_on`، وإلا الكيان الوحيد المحلول).
- `concept`: إن كان مدعومًا بـ `api` → استدعاء؛ إن كان مدعومًا بـ `field` → **focus marker** (`kind="concept_field"`) بلا استدعاء، يحمل `focus_field` و`target_facet`. الأخطاء تُجمَّع لا تُرفَع.

### `executor` (`nodes/executor.py`)

ينفّذ كل إدخال له `api_name` عبر `deps.client.call(...)`؛ الإدخالات بلا `api_name` (focus markers) تُمرَّر بـ `result=None`. يُطبّع كل نتيجة إلى `ApiResult.model_dump`. يجمع الأخطاء.

### `relationship_resolver` (`nodes/relationship_resolver.py`)

الدالة النقية `resolve_relationships(facets, registry, facet, data, max_depth)`:
- نسخ عميق (لا يعدّل المدخل). لكل سجل علوي مجموعة `visited` خاصة.
- `_resolve_record(...)`: عمق-أولًا، يحرس ضد الدورات بمفتاح `(facet, pk_value)` وبعمق أقصى `MAX_REL_DEPTH` (افتراضي 3). لكل علاقة: يجلب السجل الهدف ويضيف `resolved_name → display_name`، ثم يتعمّق.

مثال: `{"ownerId": 7}` → `{"ownerId": 7, "owner": "Ahmed Mohamed"}`.

### `join` (`nodes/join.py`)

يُرجع `{}` إن لم توجد خطوات join. `_index_rows`: يفهرس صفوف كل خطوة ناجحة (dict → قائمة من عنصر). `_run_join`: يجلب الجانبين، يستدعي `deps.joins.join(left, right, spec)`، ثم `_project` حسب `emit`. مخرجات الربط تُتاح للخطوات اللاحقة (joins/aggregates) في نفس الجولة. ينتج إدخال نتيجة بشكل مماثل للـ executor (`api_name="join"`).

### `analytics` (`nodes/analytics.py`)

يُرجع `{}` إن لم توجد خطوات aggregate. `_index_list_rows`: يفهرس صفوف خطوات list الناجحة. `_source_rows`: يفضّل `depends_on`، وإلا أي نتيجة list لنفس الـ facet. `_label_field`: أول `display_fields` (أو `name`). يستدعي `deps.analytics.aggregate(...)` ويخزّن `AnalyticsResult.model_dump`.

### `context_builder` (`nodes/context_builder.py`)

ترتيب القرار (مهم):
1. **clarification** معلّق → `_build_clarification_context` (focus حتمي).
2. `intent == RECALL` → `_build_recall_context` (يستدعي `answer_recall`).
3. `analytics` موجودة → `_build_analytics_context` (يُنسّق `AnalyticsResult` نصًا مُعرّبًا ويضعه كـ focus value).
4. وإلا: يضغط النتائج (`_summarise`/`_trim`)، يحجب الحقول `password/secret/token`، يحذف الـ FK الخام الذي له اسم محلول، ويبني `focus` لقيم المفاهيم الحقلية (`_build_field_focus`). حد `_MAX_ITEMS=25`.

تنسيق الأرقام `_fmt_num` (فواصل آلاف، بلا كسور للأعداد الصحيحة). تنسيق المجموعات/top-N في `_format_groups`.

### `response_validator` (`nodes/response_validator.py`)

يحسب `has_data` / `has_focus_value` / `has_error` / `has_any_call`. القرار:
- `ok` إن وُجدت بيانات أو focus value.
- `no_plan` إن لا استدعاءات ولا focus.
- `error` إن وُجد خطأ.
- `empty` غير ذلك.

رسائل معرّبة في `_MESSAGES` (ar/en) لكل حالة غير `ok`.

### `response_generator` (`nodes/response_generator.py`)

التوجيه:
- `intent == CHAT` → `_chat` (محادثة عامة عبر `chat.md` + سجل، مع بحث ويب اختياري `_maybe_search_web` → `_decide_web_search` عبر `web_decision.md`).
- وإلا، `_deterministic_answer` يُرجع قيمة `focus` حرفيًا لـ **recall/analytics/clarification** (لضمان الأرقام الدقيقة بلا إعادة صياغة من LLM).
- `status != ok` → رسالة المُحقّق المعرّبة.
- وإلا → `_generate` (LLM يلخّص `context` عبر `response.md`).

عند فشل الـ LLM: `_fallback_answer` (إجابة حتمية مقروءة من focus/حقول/عدّ/ملخّص — لا JSON خام).

### `memory_manager` (`nodes/memory_manager.py`)

`_should_save`: فقط إن `status==ok` وطول الإجابة ≥ 8 أحرف. الأهمية `_importance`: 0.7 إن وُجد focus وإلا 0.4. الحفظ best-effort (لا يفشل الجولة).

### `conversation.py` (ليس عقدة graph — منطق recall حتمي)

`detect_recall_topic(question)` يطابق عبارات (عربي/إنجليزي) ويُرجع أحد المواضيع:

| الموضوع | أمثلة محفّزات |
|---------|----------------|
| `explanation` | «حسبتها ازاي»، «كيف حسبت»، «how did you calculate»، «where did you get that» |
| `previous_answer` | «ماذا قلت»، «what did you say» |
| `previous_question` | «آخر سؤال»، «what did I ask» |
| `history` | «ماذا تحدثنا»، «what did we talk about» |

`answer_recall(messages, topic, language)` يبني إجابة حتمية من السجل القصير (يستبعد الجولة الحالية). موضوع `explanation` يعيد ذكر الإجابة السابقة ويوضّح أنها محسوبة من سجلات النظام مباشرةً (لا اختلاق). هذا المسار يمنع البحث على الويب والهلوسة لأسئلة المتابعة عن «كيف حسبت».

---

## 6. طبقة التخطيط (Planner)

### `src/planner/llm_planner.py` — `LLMPlanner`

`plan(question) -> (ExecutionPlan, usage)`:

1. **مسار recall حتمي:** إن `detect_recall_topic(question) is not None` → يفوّض لـ `fallback.plan` (الذي يضبط `intent=RECALL` مع `recall_topic`) دون استدعاء الـ LLM إطلاقًا.
2. يكتشف اللغة، يبني prompt من `planner.md` (مع `catalog_summary()` و`_format_concepts()`)، ويطلب مخرجًا منظمًا (`max_repair=1`).
3. إن `intent == CHAT` → `_chat_plan` (خطة بلا خطوات).
4. إن `is_empty` (DATA بلا خطوات) → يجرّب «شبكة الكلمات» (`fallback`) لاستعادة سؤال بيانات واضح؛ وإلا يعامله محادثة.
5. عند فشل الـ LLM (`LLMError`/اتصال) → `_degraded_fallback` (شبكة الكلمات وإلا محادثة).

### `src/planner/fallback_planner.py` — `FallbackPlanner`

مخطّط قواعدي حتمي (لا LLM). يبني مرادفات الـ facets من الـ registry + مدمجة. أهم الخرائط:

- `_AGG_AVG/_SUM/_MIN/_MAX` و`_TOP_WORDS/_BOTTOM_WORDS/_GROUP_WORDS` لاكتشاف التحليل.
- `_METRIC_SYNONYMS` (مثل `ميزانية/budget → budget`)، `_FIELD_SYNONYMS` (`قسم → orgUnit` …)، `_METRIC_FACET` (`budget → projects`).
- `_STATUS_VALUES` (`نشط → Active` …)، `_CONCEPT_WORDS` (محفّزات owner/manager/creator/stakeholders/interfaces).

التدفق: recall؟ → join؟ (`_detect_join`) → facet → aggregate؟ (`_detect_aggregate`) → concept/search/list. `detect_language(text)` = عربي إن وُجدت أحرف عربية وإلا إنجليزي.

### القوالب `src/prompts/*.md`

| الملف | الغرض | المتغيرات |
|-------|-------|-----------|
| `planner.md` | تصنيف النيّة وبناء الخطة | `${catalog}`, `${concepts}`, `${question}`, `${language}` |
| `response.md` | تلخيص البيانات في إجابة | `${language}`, `${question}`, `${context}` |
| `chat.md` | محادثة عامة (يمنع اختلاق بيانات النظام) | `${language}`, `${capabilities}`, `${web_context}` |
| `web_decision.md` | قرار «هل أبحث في الويب؟» منظم | `${question}` |

التحميل عبر `src/prompts/__init__.py`: `render(name, **values)` يستخدم `string.Template.safe_substitute` مع `lru_cache`.

---

## 7. طبقة الخدمات (Services)

### `ApiClient` (`services/api_client.py`)

`call(api_name, *, path_params, query_params, body, use_cache=True) -> ApiResult`:
- يحلّ الاسم عبر الـ registry؛ اسم مجهول → `ApiResult.failure` (لا رفع).
- يخزّن مؤقتًا فقط استدعاءات `GET` الناجحة/الفارغة (لا الأخطاء). مفتاح الكاش JSON من `(api, path, query)` مُرتّب.
- يضيف `from_cache=True` على نسخة من النتيجة المخزّنة.

### `FacetService` (`services/facet_service.py`)

عمليات مدفوعة بالإعداد: `search(facet, term)` (عبر `?q=`)، `get_by_id(facet, id)` (يستنتج اسم معامل الـ path)، `list_all(facet)`، `call_facet_api(api_name, id)`، `resolve_record(facet, id) -> dict|None`، `display_name(facet, record)`. كلها تستخدم `facets.yaml` لمعرفة الـ endpoint والمفتاح الأساسي.

### `AnalyticsService` (`services/analytics_service.py`)

`aggregate(facet, rows, spec, *, label_field="name") -> AnalyticsResult`. نقي وحتمي:
1. `_validate`: يتحقّق من وجود الحقول (metric رقمي، group_by، حقول الفلاتر) — وإلا `error` صادق.
2. `_matches`: تطبيق الفلاتر (مقارنة رقمية عند الإمكان، وإلا نصية غير حسّاسة).
3. التفرّع: `group_by` → `_grouped`؛ top-N (`limit`+`metric` بلا group) → `_top_rows`؛ وإلا `_scalar`.
4. `_scalar`: count/sum/avg(round 4)/min/max. `_as_number` يرفض `bool` ويقبل الأرقام النصية.

### محرّك الربط `services/joins/`

- `base.py`: `JoinedRow(left, right)`، `JoinStrategy` (ABC، `name`, `join(...)`)، والدالة المشتركة `keys_match` (تطابق `7` مع `"7"`).
- `nested_loop.py`: `NestedLoopJoin` (الاسم `"nested_loop"`)، O(n·m) — كافٍ لأحجام ERP الصغيرة، يدعم inner/left.
- `engine.py`: `JoinEngine` يسجّل استراتيجيات باسم ويختار حسب `spec.strategy` أو الافتراضي. `build_default_engine()` يسجّل nested-loop. إضافة Hash/SortMerge join لاحقًا = سطر `register` واحد.

### `WebSearchService` (`services/web_search_service.py`)

غلاف Tavily مرن ومُختبَر بالحقن. `from_api_key(...)` (استيراد كسول لـ `AsyncTavilyClient`). `search(query) -> WebSearchResult` لا يرفع أبدًا (الفشل في `error`). إعادة محاولة أُسّية عبر tenacity. يُطبّع الناتج (`_normalise`) إلى نماذج.

---

## 8. طبقة النقل: Adapters + Auth + Cache + LLM

### Adapters (`src/adapters/`)

- `base.py`: بروتوكول `ERPAdapter` (`call(...)`, `aclose()`) — الوكيل يعتمد عليه فقط.
- `http_adapter.py`: `HttpERPAdapter` (التنفيذ المشترك):
  - `_build_url`: يملأ معاملات الـ path.
  - `_filter_query`: يبقي المعاملات المعلنة وغير الفارغة.
  - `_send_with_retry`: tenacity (`stop_after_attempt(erp_max_retries)`, backoff أُسّي)؛ يعيد المصادقة مرة عند 401 (يبطل التوكن)، ويعيد المحاولة عند 5xx.
  - `_to_result`: **404 → نتيجة فارغة (success/empty)** لا خطأ؛ 4xx أخرى → خطأ بتفاصيل؛ وإلا JSON (أو نص).
- `mock_adapter.py` / `real_adapter.py`: يرثان `HttpERPAdapter` ويختلفان فقط في `label` والإعداد.
- `factory.py`: `build_http_client(settings)` و`build_adapter(settings, client)` (المكان الوحيد الذي يعرف الـ adapter الملموس).

### Auth (`src/auth/auth_manager.py`)

`AuthManager` يدير دورة حياة Bearer token:
- `auth_headers()` → `{}` إن المصادقة معطّلة (لا اعتماد)، وإلا `Authorization: Bearer ...`.
- `_get_valid_token`: كاش + قفل async (double-check) لمشاركة طلب login واحد.
- `_login`: POST لـ `erp_auth_login_path`؛ يقرأ `access_token`/`token` و`expires_in`؛ يطرح هامش `_EXPIRY_SKEW_SECONDS=30`.
- `invalidate()` / `logout()` (best-effort).

### Cache (`src/cache/memory_cache.py`)

`TTLCache` async آمن (`OrderedDict` + قفل): `get`/`set`/`get_or_set`/`clear`/`stats`. إخلاء FIFO عند تجاوز `max_entries`. عدّادات `hits`/`misses`.

### LLM (`src/llm/ollama_client.py`)

`OllamaLLM` غلاف `ChatOllama`:
- مثيلان: نصّي و`format="json"`. يضبط `num_gpu` فقط عند تحديده (`0` = CPU فقط لتفادي انهيار CUDA على GPU قديمة).
- `complete(system, user)` و`chat(system, history)` — يجرّدان كتل `<think>...</think>`.
- `structured(system, user, schema, max_repair=2)`: **حلقة إصلاح JSON** — عند فشل التحقق يُعاد الخطأ للنموذج ويُطلب تصحيح؛ يرفع `LLMError` بعد استنفاد الميزانية.
- `extract_json(text)`: يجرّد `<think>` وأسوار الكود ```` ```json ````، ثم يأخذ من أول `{` لآخر `}`.

---

## 9. الذاكرة (ثلاث مستويات)

| المستوى | أين | الغرض |
|---------|-----|--------|
| قصيرة المدى | `state["messages"]` | آخر أسئلة/أجوبة في نفس الـ thread (مُدمجة `operator.add`) |
| Checkpoint | `data/agent.db` عبر `AsyncSqliteSaver` | استئناف المحادثة بعد إعادة التشغيل + حفظ كامل الحالة لكل thread |
| طويلة المدى | جدول `memories` في نفس SQLite | حقائق تُسترجع في `planner` (بحث keyword) |

- البروتوكول `MemoryRepository` (`memory/repository.py`): `initialize`/`add`/`search`/`recent`/`close` — قابل للاستبدال بـ PostgreSQL/pgvector دون تغيير العُقد.
- التنفيذ `SqliteMemoryRepository` (`memory/sqlite_repository.py`): مخطّط `memories(id, thread_id, content, kind, importance, metadata, created_at)` + فهرس على `thread_id`. البحث: تطابق LIKE مُسجَّل حسب تداخل الكلمات (طول > 2) ثم الأهمية.

**سياسة الحفظ:** لا تُحفَظ كل محادثة — `memory_manager` يحفظ فقط الإجابات الناجحة غير التافهة (انظر القسم 5).

---

## 10. الاكتشاف (Discovery)

توليد `config/api_registry.json` من مصادر خارجية بدل كتابته يدويًا.

- `discovery/base.py`: `ApiSource` (ABC، `name`, `load() -> list[ApiEndpoint]`، و`_facet_from_operation_id`).
- المصادر: `openapi_source.py`، `postman_source.py`، `manual_source.py`.
- `discovery/generator.py`: CLI. `SOURCES` يربط الاسم بالكلاس. `generate(kind, source_path, out_path)` → يكتب JSON. `endpoints_to_registry` يُسلسِل.

التشغيل:

```bash
python -m src.discovery.generator --source openapi config/sources/openapi.json
python -m src.discovery.generator --source postman config/sources/postman_collection.json
python -m src.discovery.generator --source manual  config/api_registry.json --out config/api_registry.json
```

### `src/tools/tool_factory.py` (للتوسّع)

`Tool` (غلاف قابل للاستدعاء حول endpoint مع تحقّق من معاملات الـ path) و`ToolFactory.build()` (يبني `api_name → Tool` لكل endpoint) و`catalog()` (توقيعات للـ prompting). تمكّن من توليد أدوات ديناميكية من الـ registry دون كود لكل API.

---

## 11. الإعدادات Settings والمتغيرات البيئية

الملف: `src/config/settings.py` — `Settings(BaseSettings)` يقرأ `.env` (UTF-8، `extra=ignore`، غير حسّاس لحالة الأحرف). `get_settings()` مُخزّن (`lru_cache`) وينشئ المجلدات.

| المتغير | الافتراضي | الوصف |
|---------|-----------|-------|
| `ERP_ADAPTER` | `mock` | `mock` / `real` |
| `ERP_BASE_URL` | `http://127.0.0.1:8000` | عنوان الـ ERP |
| `ERP_USERNAME` / `ERP_PASSWORD` | `demo` / `demo` | فارغ = بلا مصادقة |
| `ERP_AUTH_LOGIN_PATH` / `ERP_AUTH_LOGOUT_PATH` | `/auth/login` / `/auth/logout` | مسارات المصادقة |
| `ERP_TIMEOUT_SECONDS` | `15` | مهلة HTTP |
| `ERP_MAX_RETRIES` | `3` | عدد المحاولات |
| `OLLAMA_MODEL` | `llama3.1:latest` | النموذج |
| `OLLAMA_BASE_URL` | `http://127.0.0.1:11434` | خادم Ollama |
| `OLLAMA_TEMPERATURE` | `0.1` | الحرارة |
| `OLLAMA_TIMEOUT_SECONDS` | `300` | مهلة (تحميل بارد) |
| `OLLAMA_NUM_GPU` | `None` | `0` = CPU فقط |
| `TAVILY_API_KEY` | `None` | تفعيل البحث على الويب |
| `WEB_SEARCH_MAX_RESULTS` | `5` (1–20) | عدد النتائج |
| `WEB_SEARCH_DEPTH` | `advanced` | `basic`/`advanced` |
| `WEB_SEARCH_TIMEOUT_SECONDS` | `20` | مهلة البحث |
| `WEB_SEARCH_MAX_RETRIES` | `2` (0–5) | محاولات البحث |
| `SQLITE_PATH` | `./data/agent.db` | checkpoint + ذاكرة |
| `CACHE_TTL_SECONDS` | `300` | عمر الكاش |
| `CACHE_MAX_ENTRIES` | `2048` | سعة الكاش |
| `MAX_REL_DEPTH` | `3` (1–10) | عمق حل العلاقات |
| `API_REGISTRY_PATH` / `FACETS_PATH` / `SEMANTIC_CATALOG_PATH` | مسارات config/schema | ملفات الإعداد |
| `LOG_LEVEL` | `INFO` | مستوى السجل |
| `LOG_FORMAT` | `console` | `console`/`json` |

خصائص مشتقّة: `auth_enabled` (وُجدت بيانات اعتماد)، `web_search_enabled` (وُجد مفتاح Tavily).

---

## 12. ملفات الإعداد بالتفصيل

### `config/api_registry.json`

كل مفتاح = اسم API منطقي (مثل `people.search`):

```json
"projects.get_by_id": {
  "url": "/api/projects/{id}",
  "method": "GET",
  "path_params": ["id"],
  "facet": "projects",
  "description": "Get a single project by id (includes budget, spent, status, ownerId, orgUnitId)."
}
```

يُحمّل في `config/registry.py` → `load_endpoints` → `Registry.endpoints`. **الـ endpoints الحالية:** people/org_units/systems/datasets/projects بـ search/get_by_id/list، إضافةً إلى `systems.stakeholders` و`systems.interfaces` (APIs متداخلة).

### `config/facets.yaml`

لكل facet: `business_name`, `primary_key`, `display_fields`, `search_api`, `get_by_id_api`, `list_api`, `relationships`. اختصار العلاقة `field: "target_facet.field"` أو الصيغة الطويلة `{target, target_field, as_name}`.

```yaml
projects:
  business_name: Projects
  primary_key: id
  display_fields: [name]
  search_api: projects.search
  get_by_id_api: projects.get_by_id
  list_api: projects.list
  relationships:
    ownerId: { target: people, as_name: owner }
    orgUnitId: { target: org_units, as_name: orgUnit }
```

**Facets الحالية:** `people`, `org_units`, `systems`, `datasets`, `projects`.
**العلاقات:** `people.orgUnitId→org_units`; `org_units.managerId→people`, `parentId→org_units`; `systems.ownerId→people`; `datasets.createdBy→people`, `orgUnitId→org_units`; `projects.ownerId→people`, `orgUnitId→org_units`.

### `schema/semantic_catalog.yaml`

يربط **مفاهيم** (الكلمات التي يقولها المستخدم) بـ `field`+`target` (علاقة) أو `api` (endpoint). يُستخدم في خطوات `concept` و`api_selector`.

```yaml
systems:
  concepts:
    owner:        { field: ownerId, target: people, description: ... }
    responsible:  { field: ownerId, target: people, description: synonym for owner }
    stakeholders: { api: systems.stakeholders, description: ... }
    interfaces:   { api: systems.interfaces, description: ... }
```

**المفاهيم الحالية:** `people.department`; `org_units.manager/parent`; `systems.owner/responsible/stakeholders/interfaces`; `datasets.creator/owner_unit`.

يُحمّل في `config/registry.py` → `load_semantic_catalog` (الملف المفقود يُتسامَح معه → كتالوج فارغ).

### `Registry` (`config/registry.py`)

الواجهة الموحّدة: `endpoints`/`get_endpoint`/`require_endpoint`/`endpoints_for_facet`، `facets`/`get_facet`/`require_facet`، `semantic`. ومساعد `catalog_summary()` (ملخّص للـ prompt). يُبنى مرّة عبر `Registry.from_settings(settings)` ويُحقَن.

---

## 13. Mock ERP

| ملف | الدور |
|-----|------|
| `mock_erp/server.py` | FastAPI على `:8000` يطابق الـ registry؛ يحمّل JSON مرّة عند البدء |
| `mock_erp/data/*.json` | قواعد البيانات الوهمية |

البيانات: `people.json`, `org_units.json`, `systems.json`, `datasets.json`, `projects.json` (9 مشاريع)، و`stakeholders.json`/`interfaces.json` (مُفهرسة بـ `systemId` كنص).

المسارات: `/auth/login` (يقبل أي بيانات غير فارغة، يُصدر `mock-token-...`)، `/auth/logout`، و`/api/<facet>` + `/search?q=` + `/{id}` لكل facet، و`/api/systems/{id}/stakeholders|interfaces` (مُثرَّاة بأسماء الأشخاص/الأنظمة)، و`/health`. `_search` بحث substring غير حسّاس عبر حقول محددة. السجل المفقود → `404` (يترجمه الـ adapter إلى «فارغ»).

عند إضافة facet جديد: أضف JSON + routes في `server.py` + سطر في `facets.yaml` + entries في `api_registry.json` (+ مفاهيم في `semantic_catalog.yaml` إن لزم).

التشغيل: `python -m mock_erp.server`.

---

## 14. CLI والمراقبة (Observability)

### `src/cli.py`

REPL async. الأوامر: `/help`، `/trace` (يطبع توقيت كل مرحلة + الأخطاء)، `/new` (thread جديد معزول `cli-<hex>`)، `/exit`. الـ thread الافتراضي `cli-default` (يحفظ التاريخ والذاكرة عبر إعادات التشغيل). يضبط stdout/stderr إلى UTF-8 لعرض العربية على Windows. يبقى حيًّا عند أي خطأ.

### `src/observability/logging.py`

`configure_logging(settings)` (مرّة، idempotent): يضبط `structlog` + stdlib، يُسكِت `httpx/httpcore/ollama/urllib3` إلى WARNING، ويختار renderer (console/JSON). `get_logger(name)` يُرجع logger مربوط. كل عقدة تُسجّل حدثًا بنيويًا (مثل `planned`, `executed`, `analytics_computed`).

### `src/observability/traces.py`

`Trace`/`Span` (storage-agnostic): `span(name)` (context manager يقيس الزمن)، `record_api_call`، `record_error`، `add_tokens`، `summary()`، `log_summary()`. ملاحظة: العُقد تكتب التتبّع فعليًا في `state["trace"]` عبر `_helpers.append_trace`؛ `Trace` متاح كأداة تجميع أغنى.

### `src/nodes/_helpers.py`

`append_trace(state, stage, elapsed_ms, detail)` و`append_errors(state, new_errors)` — قراءة القيمة الحالية والإلحاق ثم الإرجاع (دلالة استبدال؛ planner يصفّرها كل جولة).

---

## 15. أمثلة تدفق كاملة لكل نوع سؤال

### (أ) سؤال بيانات بسيط — «مين مالك نظام CRM؟»

1. `planner` → `intent=data`، خطوات: `search systems "CRM"` → `get_by_id` → `concept owner`.
2. `entity_resolver` → id النظام.
3. `api_selector` → استدعاء `systems.get_by_id` + focus marker للمفهوم `owner` (field `ownerId`).
4. `executor` → سجل النظام مع `ownerId`.
5. `relationship_resolver` → يضيف `owner: "Ahmed ..."`.
6. `context_builder` → focus value = اسم المالك.
7. `response_validator` → `ok`. → `response_generator` → جملة عربية.

### (ب) تحليل (SQL mode) — «كم متوسط budget المشاريع؟»

1. `planner` → `list projects` → `aggregate {op: avg, metric: budget}`.
2. … `executor` يجلب 9 مشاريع → `analytics` يحسب: `(1,680,000 / 9) = 186,666.67`.
3. `context_builder` (`_build_analytics_context`) → focus value نصّي حتمي.
4. `response_generator` يعيده **حرفيًا** (لا إعادة صياغة LLM للأرقام).

### (ج) ربط cross-entity — «ما المشاريع التي يملكها مالك نظام CRM؟»

`search systems` → `get_by_id` (به `ownerId`) → `list projects` → `join {left_key: ownerId, right_key: ownerId, emit: right}`. يمكن إضافة `aggregate` يعتمد على خطوة الـ join.

### (د) توضيح (تعدّد التطابق) — «من هو أحمد؟» (3 نتائج)

`entity_resolver` يجد غموضًا → `clarification.needed=true` → الفرع يذهب لـ `context_builder` الذي يبني سؤالًا مُرقّمًا. الجولة التالية: `clarification_resolver` يفسّر الاختيار («2»/«الأول») ويعيد كتابة السؤال.

### (هـ) recall — «إيه آخر سؤال سألته؟»

`detect_recall_topic` → `previous_question` → `fallback.plan` (RECALL) بلا LLM → `answer_recall` يبني الإجابة من السجل.

### (و) محادثة + ويب — «إيه أخبار الذكاء الاصطناعي النهارده؟»

`intent=chat` → `_maybe_search_web` → `web_decision` يقرّر `needs_search=true` → Tavily → `chat.md` يجيب مستندًا للنتائج (إن كان `TAVILY_API_KEY` مضبوطًا).

### (ز) شرح حساب سابق — «حسبتها ازاي؟» (إصلاح حديث)

`detect_recall_topic` → `explanation` → RECALL حتمي بلا LLM/ويب. `answer_recall` يعيد ذكر الرقم السابق ويوضّح أنه محسوب من سجلات النظام مباشرةً — **لا بحث ويب ولا اختلاق بيانات**.

---

## 16. ماذا تعدّل؟ (دليل سريع)

| تريد أن… | عدّل |
|----------|------|
| تضيف API جديد للـ ERP | `config/api_registry.json` أو discovery من `config/sources/` |
| تضيف كيان (facet) جديد | `config/facets.yaml` + بيانات mock أو API حقيقي |
| تربط حقل FK باسم مقروء | `relationships` داخل `config/facets.yaml` |
| تضيف مفهوم أعمال («مالك»، «عقود نشطة») | `schema/semantic_catalog.yaml` |
| تغيّر بيانات التجربة | `mock_erp/data/*.json` + endpoints في `mock_erp/server.py` |
| تربط ERP حقيقي | `.env` (`ERP_ADAPTER=real`) + registry من OpenAPI |
| تغيّر نموذج Ollama / GPU | `.env` (`OLLAMA_MODEL`, `OLLAMA_NUM_GPU`) |
| تفعّل بحث الويب | `.env` (`TAVILY_API_KEY`) + `services/web_search_service.py` |
| تغيّر صياغة التخطيط | `src/prompts/planner.md` ثم منطق `planner/` |
| تغيّر صياغة الإجابة على بيانات | `src/prompts/response.md` |
| تغيّر المحادثة العامة | `src/prompts/chat.md` |
| تغيّر قرار «هل أبحث في الويب؟» | `src/prompts/web_decision.md` |
| تضيف عبارات recall (أسئلة عن المحادثة) | `src/nodes/conversation.py` (`_TRIGGERS`) |
| تضيف عقدة pipeline | `src/nodes/` + تسجيلها في `src/graph/builder.py` |
| تضيف منطق HTTP / retry | `src/adapters/http_adapter.py` |
| تضيف خوارزمية join | `src/services/joins/` + `register` في `engine.py` |
| تضيف عملية تحليل | `src/models/plan.py` (`AggregateOp`) + `services/analytics_service.py` |
| توضيح اسم مزدوج | `nodes/clarification.py`, `entity_resolver.py`, `context_builder.py` |
| ذاكرة طويلة المدى | `src/memory/sqlite_repository.py` (أو تنفيذ جديد للبروتوكول) |
| checkpoint محادثة | `SQLITE_PATH` + `graph/builder.py` (`AsyncSqliteSaver`) |
| إعدادات عامة | `src/config/settings.py` + `.env.example` |
| واجهة الطرفية | `src/cli.py` |
| اختبارات | `tests/test_*.py` |

---

## 17. شجرة الملفات

```
ai-agent-for-api/
│
├── .env / .env.example      # إعدادات (سري / قالب)
├── requirements.txt · pyproject.toml · README.md · PROJECT_GUIDE.md
│
├── config/
│   ├── api_registry.json    # كتالوج endpoints (اسم، URL، method، facet)
│   ├── facets.yaml          # كيانات ERP: search/get/list + علاقات FK
│   └── sources/             # openapi.json · postman_collection.json (لـ discovery)
│
├── schema/
│   └── semantic_catalog.yaml # مفاهيم أعمال → APIs/fields
│
├── mock_erp/
│   ├── server.py            # FastAPI :8000
│   └── data/                # people/org_units/systems/datasets/projects/stakeholders/interfaces .json
│
├── data/                    # يُنشأ محليًا: agent.db (checkpoint + ذاكرة)
│
├── src/
│   ├── cli.py               # REPL: /help /trace /new /exit
│   ├── graph/               # builder.py (Graph + AgentRuntime) · dependencies.py (PipelineDeps)
│   ├── nodes/               # 12 عقدة + conversation.py (recall) + _helpers.py
│   ├── planner/             # llm_planner.py · fallback_planner.py
│   ├── services/            # api_client · facet_service · analytics_service · web_search_service · joins/
│   ├── adapters/            # base · http_adapter · mock_adapter · real_adapter · factory
│   ├── auth/                # auth_manager.py
│   ├── cache/               # memory_cache.py (TTLCache)
│   ├── config/              # settings.py · registry.py
│   ├── discovery/           # generator.py + base + openapi/postman/manual sources
│   ├── llm/                 # ollama_client.py (ChatOllama + JSON repair)
│   ├── memory/              # repository.py (protocol) · sqlite_repository.py
│   ├── models/              # state · plan · api · semantic · analytics · web
│   ├── prompts/             # __init__.py (render) + planner/response/chat/web_decision .md
│   ├── observability/       # logging.py · traces.py
│   └── tools/               # tool_factory.py (أدوات ديناميكية)
│
└── tests/                   # انظر القسم 19
```

---

## 18. الاصطلاحات والقواعد المعمارية

1. **Plan → Route → Execute → Respond** — التخطيط منفصل عن التنفيذ تمامًا.
2. **لا بيانات وهمية من LLM** لأسئلة ERP — إما API/أداة/ذاكرة أو «لا توجد بيانات». التحليل يُرجِع `error` صادقًا بدل رقم مختلَق.
3. **Structured output** — الخطة والقرارات دائمًا Pydantic JSON (مع حلقة إصلاح، لا تحليل نص طبيعي).
4. **Graceful degradation** — فشل ذاكرة/API/LLM لا يوقف الـ REPL؛ كل عقدة تتدهور برشاقة.
5. **Config-driven** — تبديل ERP/إضافة facet = تعديل إعداد لا كود.
6. **Adapter pattern + DI** — العُقد تعتمد على بروتوكولات (`ERPAdapter`, `MemoryRepository`) محقونة عبر `PipelineDeps`.
7. **Prompts في ملفات** — لا نصوص طويلة داخل `nodes/`؛ نسخ القوالب عند التعديل.
8. **العُقد رفيعة** — تنسيق فقط؛ منطق الأعمال في `services/`؛ النقل في `adapters/`.
9. **type hints + docstrings** في كل مكان؛ دوال صغيرة؛ تجنّب globals والأرقام السحرية.
10. **الأمان** — لا أسرار في الكود؛ كلها عبر `.env`/متغيرات بيئية.

---

## 19. الاختبارات

```bash
pytest
```

`tests/conftest.py` يوفّر تركيبات (fixtures) مشتركة (مثل `registry`). كل ملف يغطّي وحدة:

| الملف | يغطّي |
|-------|-------|
| `test_registry.py` | تحميل الـ registry/facets/semantic |
| `test_discovery.py` | مصادر الاكتشاف + المولّد |
| `test_fallback_planner.py` | المخطّط القواعدي (heuristics) |
| `test_llm_planner.py` | توجيه data/chat/recall + الشبكة الكلمية |
| `test_entity_resolver.py` | حل الكيانات والغموض |
| `test_clarification.py` · `test_clarification_graph.py` | منطق التوضيح + تكامله في الـ graph |
| `test_executor.py` | التنفيذ والتطبيع |
| `test_facet_service.py` | عمليات الـ facet |
| `test_relationship_resolver.py` | حل العلاقات متعدد القفزات + الدورات |
| `test_joins.py` · `test_join_node.py` | استراتيجيات الربط + عقدة الربط |
| `test_analytics_service.py` · `test_analytics_node.py` · `test_context_builder_analytics.py` | التحليل + بناء سياقه |
| `test_validator.py` | تصنيف ok/empty/error/no_plan |
| `test_response_generator.py` | توليد الإجابة (حتمي/LLM) |
| `test_conversation.py` | اكتشاف recall (بما فيه `explanation`) + إجاباته |
| `test_web_search_service.py` | البحث على الويب (محاكى) |
| `test_cache.py` | TTLCache |

الأهداف المعمارية: تغطية ≥ 80٪، ومحاكاة كل استدعاءات API/الويب (لا شبكة في الاختبارات).

---

*آخر تحديث: مرجع شامل يعكس الـ pipeline الكامل (clarification، join، analytics، recall بما فيه `explanation`)، الخدمات، النقل، الذاكرة، الاكتشاف، والإعداد.*

# Codebase Structure

**Analysis Date:** 2026-02-23

## Directory Layout

```
lead-enrichment-agent/
├── main.py                     # FastAPI app entry point
├── config.py                   # All env var loading (single source of truth)
├── test_agent.py               # CLI test runner
├── requirements.txt            # Python dependencies
├── CLAUDE.md                   # Project instructions for AI assistants
├── DESIGN.md                   # Design notes
├── README.md                   # Project documentation
│
├── agent/                      # Core pipeline logic
│   ├── __init__.py
│   ├── orchestrator.py         # Entry point: semantic cache + graph dispatch
│   ├── orchestrator_legacy.py  # Pre-LangGraph imperative implementation (reference)
│   ├── graph.py                # LangGraph StateGraph: nodes + edge wiring
│   ├── graph_state.py          # AgentState TypedDict definition
│   ├── planner.py              # LLM planner: decides tools + queries
│   ├── extractor.py            # LLM extractor: raw text → structured profile
│   ├── schemas.py              # All Pydantic models
│   ├── tool_protocol.py        # Tool Protocol + ToolRegistry singleton
│   ├── cache.py                # In-memory TTL cache (InMemoryCache)
│   ├── semantic_cache.py       # Qdrant + OpenAI vector semantic cache
│   ├── observe.py              # Langfuse tracing (no-op when keys absent)
│   └── utils.py                # retry_with_backoff decorator + llm_create()
│
├── tools/                      # Tool implementations
│   ├── __init__.py             # Registers all planner-visible tools in registry
│   ├── github_tool.py          # GitHubTool — GitHub API profile + repos + events
│   ├── serper_tool.py          # SerperSearchTool — Google search via Serper
│   ├── news_tool.py            # SerperNewsTool — Google News via Serper
│   ├── community_tool.py       # CommunityActivityTool — HN Algolia + Reddit/Serper
│   ├── playwright_tool.py      # PlaywrightTool — headless browser scraping
│   ├── email_pipeline.py       # EmailPipeline — 4-layer waterfall email finder
│   ├── hunter_tool.py          # HunterIoTool — called only inside email_pipeline
│   └── proxy.py                # ProxyManager — rotating proxy support for browser
│
├── benchmarks/                 # Latency benchmarking
│   ├── __init__.py
│   ├── benchmark.py            # Multi-run benchmark with phase timing breakdown
│   └── results.json            # Saved benchmark results (generated, committed)
│
├── evals/                      # Quality evaluation framework
│   ├── __init__.py
│   ├── evaluator.py            # Scoring functions (exact_match, contains_any, etc.)
│   ├── run_eval.py             # Eval runner: calls API, scores against ground truth
│   ├── ground_truth.json       # Expected field values per test case
│   └── results.json            # Saved eval results (generated, committed)
│
├── .planning/                  # GSD planning artifacts
│   └── codebase/               # Codebase analysis documents
│
└── venv/                       # Python virtual environment (not committed)
```

## Directory Purposes

**`agent/`:**
- Purpose: All core pipeline logic — no HTTP, no external API calls (those are in `tools/`)
- Contains: LangGraph graph, planner/extractor LLM calls, schemas, caching, observability
- Key files: `orchestrator.py` (entry), `graph.py` (pipeline), `schemas.py` (all models)

**`tools/`:**
- Purpose: Data-fetching adapters that implement the `Tool` protocol
- Contains: One class per external data source; each has `name`, `description`, `async run()`
- Key files: `__init__.py` (registration), `email_pipeline.py` (orchestrator-managed, not registered)

**`benchmarks/`:**
- Purpose: Latency measurement and performance tracking
- Contains: `benchmark.py` (runner), `results.json` (output)
- Key files: `benchmark.py` — run with `python -m benchmarks.benchmark`

**`evals/`:**
- Purpose: Quality scoring against known-good ground truth cases
- Contains: `evaluator.py` (scoring logic), `run_eval.py` (runner), `ground_truth.json` (test cases)
- Key files: `ground_truth.json` — add new eval cases here

## Key File Locations

**Entry Points:**
- `main.py`: FastAPI HTTP server — `POST /enrich`, `GET /health`
- `test_agent.py`: CLI: `python test_agent.py [name] [company] [location] [use_case]`
- `benchmarks/benchmark.py`: `python -m benchmarks.benchmark --name X --company Y --runs N`
- `evals/run_eval.py`: `python -m evals.run_eval`

**Configuration:**
- `config.py`: All env var loading — edit here to add new config variables
- `.env`: API keys (not committed; see `.env.example`)
- `.env.example`: Template showing all required env vars

**Core Logic:**
- `agent/graph.py`: Pipeline DAG — add/remove/reorder nodes here
- `agent/graph_state.py`: `AgentState` — add new state fields here
- `agent/planner.py`: Planner system prompt + fallback plan — edit to change tool selection behavior
- `agent/extractor.py`: Extractor system prompt + JSON schema — edit to change extracted fields
- `agent/schemas.py`: All Pydantic models — edit to add/change API request/response/profile fields

**Tool Registration:**
- `tools/__init__.py`: Add `registry.register(NewTool())` here to make a tool planner-visible

**Testing:**
- `evals/ground_truth.json`: Add eval cases (name, company, expected field values)
- `evals/evaluator.py`: Add scoring functions for new field types

## Naming Conventions

**Files:**
- Tool implementations: `{source}_tool.py` (e.g., `github_tool.py`, `serper_tool.py`)
- Agent modules: lowercase, no suffix (e.g., `planner.py`, `extractor.py`, `schemas.py`)
- Pipeline state: `graph_state.py` (separate from graph logic in `graph.py`)

**Classes:**
- Tool classes: `{Source}Tool` (e.g., `GitHubTool`, `SerperSearchTool`, `PlaywrightTool`)
- Special tools (not protocol-conforming): descriptive name (e.g., `EmailPipeline`)
- Pydantic models: PascalCase noun (e.g., `EnrichRequest`, `EnrichedProfile`, `ToolResult`)

**Tool `name` attribute:**
- Must match the string key used in planner prompts and registry lookup
- Examples: `"github"`, `"web_search"`, `"news"`, `"community"`, `"browser"`

**Cache keys:**
- Format: `{tool_name}:{identifier}` (e.g., `github:{name}:{company}`, `search:{query}`, `browser:{url}`)
- Hunter (inside email_pipeline): `hunter:{name}:{company}`

**Functions:**
- Async node functions in `graph.py`: `{phase}_node` (e.g., `planner_node`, `extractor_node`)
- Private helpers: underscore prefix (e.g., `_fallback_plan`, `_build_combined`, `_extract_domains`)
- Private layer methods in `EmailPipeline`: `_layer_{name}` (e.g., `_layer_github`, `_layer_smtp`)

## Where to Add New Code

**New planner-visible tool:**
1. Create `tools/{source}_tool.py` with a class implementing `Tool` protocol: `name`, `description`, `async run(name, company, **kwargs) -> ToolResult`
2. Add `registry.register(NewTool())` in `tools/__init__.py`
3. Tool is automatically available to the planner; update planner system prompt in `agent/planner.py` if needed

**New internal (orchestrator-managed) tool:**
1. Create `tools/{name}_tool.py` or add to `tools/email_pipeline.py`
2. Do NOT register in `tools/__init__.py`
3. Add a new LangGraph node in `agent/graph.py` and wire it into the graph with `builder.add_edge()`

**New pipeline phase (new node):**
1. Add node function to `agent/graph.py` (e.g., `async def my_node(state: AgentState) -> dict`)
2. Wrap with `traced_node()`: `builder.add_node("my_node", traced_node(my_node))`
3. Add state fields to `agent/graph_state.py` if the node produces new output
4. Wire with `builder.add_edge()` calls in `build_graph()`

**New profile field:**
1. Add field to `EnrichedProfile` in `agent/schemas.py`
2. Add to extractor JSON schema in the system prompt in `agent/extractor.py`
3. Add scoring logic to `evals/evaluator.py` and test cases in `evals/ground_truth.json`

**New response field:**
1. Add to `EnrichResponse` in `agent/schemas.py`
2. Populate in `agent/orchestrator.py` (from `final` graph state) or in a new/existing node in `agent/graph.py`
3. Return in the state dict from the relevant node

**New environment variable:**
1. Add to `config.py` with `os.getenv("VAR_NAME", default)`
2. Add to `.env.example` with description
3. Import from `config.py` in the module that needs it

**New utility (shared helper):**
- Shared helpers: `agent/utils.py` (currently: retry decorator, `llm_create()`)

## Special Directories

**`venv/`:**
- Purpose: Python virtual environment
- Generated: Yes (via `python -m venv venv` + `pip install -r requirements.txt`)
- Committed: No (listed in `.gitignore`)

**`.planning/`:**
- Purpose: GSD planning artifacts — phase plans, codebase analysis
- Generated: Yes (by GSD tooling)
- Committed: Yes (planning history is valuable)

**`benchmarks/`:**
- Purpose: Performance tracking; `results.json` persists across runs
- Generated: Partially (`results.json` is generated output)
- Committed: Yes (results tracked in git for trend comparison)

**`evals/`:**
- Purpose: Quality evaluation; `results.json` persists across eval runs
- Generated: Partially (`results.json` is generated, `ground_truth.json` is hand-authored)
- Committed: Yes

---

*Structure analysis: 2026-02-23*

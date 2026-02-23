# Architecture

**Analysis Date:** 2026-02-23

## Pattern Overview

**Overall:** LangGraph-orchestrated multi-phase agentic pipeline with concurrent tool execution

**Key Characteristics:**
- Directed acyclic graph (DAG) of async nodes compiled via LangGraph `StateGraph`
- Two LLM roles: planner (tool selection) and extractor (structured output)
- All external I/O wrapped in `ToolResult` envelopes — tools never throw, always return
- In-memory cache per tool call, optional semantic cache (Qdrant + OpenAI embeddings) for full responses
- Observability via optional Langfuse tracing; all instrumentation is no-op when keys absent

## Layers

**HTTP API Layer:**
- Purpose: Expose the enrichment pipeline over HTTP
- Location: `main.py`
- Contains: FastAPI app, CORS middleware, `/enrich` POST endpoint, `/health` GET endpoint
- Depends on: `agent/orchestrator.py`, `agent/schemas.py`
- Used by: External clients, `test_agent.py`, `benchmarks/benchmark.py`

**Orchestrator Layer:**
- Purpose: Entry point for pipeline execution; handles semantic cache check and delegates to LangGraph
- Location: `agent/orchestrator.py`
- Contains: `enrich_lead()` function, trace lifecycle management, semantic cache lookup/store
- Depends on: `agent/graph.py`, `agent/semantic_cache.py`, `agent/observe.py`, `agent/schemas.py`
- Used by: `main.py`, `test_agent.py`, `benchmarks/benchmark.py`

**Graph/Pipeline Layer:**
- Purpose: Define and execute the multi-phase pipeline as a LangGraph StateGraph
- Location: `agent/graph.py`
- Contains: Node functions (`planner_node`, `deterministic_tools_node`, `planner_dependent_node`, `email_pipeline_node`, `extractor_node`, `output_node`), graph construction and compilation
- Depends on: `agent/planner.py`, `agent/extractor.py`, `agent/tool_protocol.py`, `tools/email_pipeline.py`
- Used by: `agent/orchestrator.py`

**Planner Layer:**
- Purpose: LLM decides which tools to run and which search queries/URLs to use
- Location: `agent/planner.py`
- Contains: `plan()` async function, `_fallback_plan()` deterministic fallback, system prompt
- Depends on: `agent/tool_protocol.py` (registry for tool descriptions), `agent/utils.py` (retrying LLM call)
- Used by: `agent/graph.py` (planner_node)

**Extractor Layer:**
- Purpose: LLM extracts structured `EnrichedProfile` from raw tool output text
- Location: `agent/extractor.py`
- Contains: `extract()`, `generate_talking_points()`, `generate_narrative()`, JSON repair helpers
- Depends on: `agent/schemas.py`, `agent/utils.py`, `agent/observe.py`
- Used by: `agent/graph.py` (extractor_node, output_node)

**Tool Protocol Layer:**
- Purpose: Defines the `Tool` protocol (structural typing) and `ToolRegistry`
- Location: `agent/tool_protocol.py`
- Contains: `Tool` Protocol class, `ToolRegistry` class, module-level `registry` singleton
- Depends on: `agent/schemas.py`
- Used by: `agent/planner.py`, `agent/graph.py`, `tools/__init__.py`

**Tools Layer (planner-visible):**
- Purpose: Individual data-fetching tools registered in the tool registry
- Location: `tools/github_tool.py`, `tools/serper_tool.py`, `tools/news_tool.py`, `tools/community_tool.py`, `tools/playwright_tool.py`
- Contains: Tool classes with `name`, `description`, and `async run()` method
- Depends on: `agent/cache.py`, `agent/schemas.py`, `agent/utils.py`, `config.py`
- Used by: `tools/__init__.py` (registration), `agent/graph.py` (via registry)

**Internal Tools (orchestrator-managed):**
- Purpose: Tools called directly by the pipeline, not exposed to the planner LLM
- Location: `tools/email_pipeline.py`, `tools/hunter_tool.py`, `tools/proxy.py`
- Contains: `EmailPipeline` (4-layer waterfall), `HunterIoTool` (called inside email_pipeline only)
- Depends on: `agent/cache.py`, `agent/schemas.py`, `config.py`
- Used by: `agent/graph.py` (email_pipeline_node directly, not via registry)

**Schema Layer:**
- Purpose: Pydantic models for all data flowing through the system
- Location: `agent/schemas.py`
- Contains: `EnrichRequest`, `EnrichResponse`, `EnrichedProfile`, `ToolResult`, `PlannerDecision`, `GitHubProfile`, `ConfidenceScores`, `FieldFreshness`, `Finding`
- Depends on: Nothing internal
- Used by: All layers

**Cache Layer:**
- Purpose: Per-tool in-memory TTL cache and optional vector-based semantic cache
- Location: `agent/cache.py` (in-memory), `agent/semantic_cache.py` (Qdrant + OpenAI)
- Contains: `InMemoryCache` (TTL dict), `lookup()`/`store()` for Qdrant semantic search
- Depends on: `config.py`; lazy-imports `qdrant_client`, `openai` when keys are present
- Used by: All tool classes (InMemoryCache), `agent/orchestrator.py` (semantic_cache)

**Observability Layer:**
- Purpose: Langfuse tracing for LangGraph nodes and LLM calls; graceful no-op when keys absent
- Location: `agent/observe.py`
- Contains: `traced_node()` decorator, `get_or_create_trace()`, `log_generation()`, `flush()`
- Depends on: `config.py`; lazy-imports `langfuse` when keys present
- Used by: `agent/graph.py` (wraps all nodes), `agent/planner.py`, `agent/extractor.py`

**Config Layer:**
- Purpose: All environment variable loading in one place
- Location: `config.py`
- Contains: All API key constants, timeouts, model names, proxy config, semantic cache threshold
- Depends on: `python-dotenv`
- Used by: All layers that need external config

## Data Flow

**Standard Enrichment Request:**

1. HTTP POST `/enrich` received by `main.py` → calls `enrich_lead(request)` in `agent/orchestrator.py`
2. Orchestrator generates `trace_id`, checks semantic cache (Qdrant); returns cached `EnrichResponse` if hit
3. Orchestrator builds `initial_state` dict and calls `graph.ainvoke(initial_state)` (LangGraph)
4. **Phase A (concurrent fan-out):** `planner_node` and `deterministic_tools_node` run simultaneously via LangGraph parallel edges from START
   - `planner_node` calls Claude (`plan()`) → returns `PlannerDecision` (tools list, search queries, URLs)
   - `deterministic_tools_node` runs `github`, `news`, `community` concurrently via `asyncio.gather()`
5. **Phase B (fan-in):** `planner_dependent_node` waits for both Phase A branches; runs `web_search` and `browser` (per URL, up to 3) concurrently based on planner decision
6. **Phase B.5:** `email_pipeline_node` runs `EmailPipeline` waterfall: GitHub email → regex scan → SMTP verify → Hunter.io
7. **Phase C:** `extractor_node` runs `extract()` and `generate_talking_points()` concurrently via `asyncio.gather()`
8. **Output:** `output_node` optionally generates narrative (if `output_format` is "narrative" or "both"), calculates final latency
9. Orchestrator collects final state, builds `EnrichResponse`, stores to semantic cache if successful

**Tool Result Flow:**
- Each tool returns `ToolResult(tool_name, raw_data, urls, success, error, latency_ms)`
- LangGraph `AgentState.tool_results` field uses `Annotated[list[ToolResult], operator.add]` for parallel-safe accumulation
- Extractor receives only successful `ToolResult` objects; concatenates `raw_data` with 3000-char per-tool cap, 20000-char total cap

**Email Waterfall:**
- Layer 1: Scan GitHub `ToolResult.raw_data` for public email matching person's name
- Layer 2: Regex scan all tool results raw text for personal email pattern
- Layer 3: SMTP RCPT-TO verification against company domain MX records (8s hard timeout)
- Layer 4: Hunter.io API call (only if `HUNTER_API_KEY` set and free methods failed)

**State Management:**
- LangGraph `AgentState` (TypedDict) carries all pipeline state across nodes
- `tool_results` and `errors` fields use `operator.add` reducer for parallel-safe list merging
- No shared mutable state outside the graph's typed state dict

## Key Abstractions

**ToolResult Envelope:**
- Purpose: Universal return type for all tool executions; never raises exceptions
- Examples: `agent/schemas.py` (`ToolResult` class), used in all `tools/*.py`
- Pattern: Every tool catches all exceptions in `run()` and returns `ToolResult(success=False, error=str(e))`

**Tool Protocol:**
- Purpose: Structural (duck-type) interface for registry-compatible tools
- Examples: `agent/tool_protocol.py` (`Tool` Protocol), satisfied by `GitHubTool`, `SerperSearchTool`, etc.
- Pattern: Any class with `name: str`, `description: str`, and `async run(name, company, **kwargs) -> ToolResult` is a valid tool

**ToolRegistry:**
- Purpose: Global registry that maps tool names to tool instances; used by planner for descriptions and by graph for dispatch
- Examples: `agent/tool_protocol.py` (`ToolRegistry`, `registry` singleton), populated in `tools/__init__.py`
- Pattern: `registry.register(ToolInstance())` at import time; `registry.get("tool_name")` at dispatch time

**AgentState (LangGraph):**
- Purpose: Typed state dict that flows through all graph nodes; reducers handle parallel writes
- Examples: `agent/graph_state.py` (`AgentState` TypedDict)
- Pattern: Input fields set once; `tool_results` and `errors` use `operator.add` for parallel-safe append

**InMemoryCache:**
- Purpose: TTL-based dict cache for tool results to avoid redundant API calls within a session
- Examples: `agent/cache.py` (`InMemoryCache`, `cache` singleton)
- Pattern: Each tool builds its own cache key (e.g., `github:{name}:{company}`), uses `await cache.get(key)` / `await cache.set(key, value, ttl=300)`

**SemanticCache:**
- Purpose: Vector similarity cache for full enrichment responses (30-day TTL, 0.92 cosine threshold)
- Examples: `agent/semantic_cache.py` (module-level functions `lookup()`, `store()`)
- Pattern: Disabled gracefully when `QDRANT_URL`/`QDRANT_API_KEY`/`OPENAI_API_KEY` are absent

## Entry Points

**HTTP Server:**
- Location: `main.py`
- Triggers: `uvicorn main:app` or `python main.py`
- Responsibilities: FastAPI app setup, CORS, route `/enrich` to `enrich_lead()`, `/health` check

**CLI Test:**
- Location: `test_agent.py`
- Triggers: `python test_agent.py [name] [company] [location] [use_case]`
- Responsibilities: Single enrichment run, prints full JSON response

**Benchmark Runner:**
- Location: `benchmarks/benchmark.py`
- Triggers: `python -m benchmarks.benchmark [--name X] [--company Y] [--runs N]`
- Responsibilities: Multi-run latency measurement, phase timing breakdown, saves to `benchmarks/results.json`

**Eval Runner:**
- Location: `evals/run_eval.py`
- Triggers: `python -m evals.run_eval`
- Responsibilities: Score enrichment responses against `evals/ground_truth.json`, saves to `evals/results.json`

**Tool Registration:**
- Location: `tools/__init__.py`
- Triggers: `import tools` (side-effect import in `agent/graph.py`)
- Responsibilities: Instantiates and registers all planner-visible tools in `registry`

## Error Handling

**Strategy:** Fail-safe — every layer catches exceptions and returns graceful defaults; the orchestrator never crashes

**Patterns:**
- Tools: `try/except Exception` in every `run()` method → returns `ToolResult(success=False, error=str(e))`
- Planner: Exception caught in `planner_node` → uses `_fallback_plan()` with deterministic queries
- Extractor: JSON parse failure → attempts `_repair_truncated_json_object()` → falls back to minimal `EnrichedProfile(name, company)`
- LangGraph nodes: `asyncio.gather(return_exceptions=True)` for concurrent tool runs → exceptions converted to failed `ToolResult`
- Retry: `retry_with_backoff()` decorator in `agent/utils.py` retries 5xx / 429 / timeout errors with exponential backoff (1s, 2s, 4s); 4xx errors not retried

## Cross-Cutting Concerns

**Logging:** `logging.basicConfig` in `main.py`; all modules use `logger = logging.getLogger(__name__)`; structured log lines include `[trace_id]` and `[+Xs]` wall-clock markers

**Validation:** Pydantic v2 (`model_validate`, `field_validator`) throughout `agent/schemas.py`; planner output validated against `registry.tool_names` to filter hallucinated tool names

**Authentication:** No user-level auth on the API (CORS open to `*`); all downstream auth via API keys in environment variables loaded by `config.py`

**Concurrency:** All I/O is async; sync calls (DNS, `dnspython`) use `loop.run_in_executor(None, ...)` to avoid blocking the event loop

---

*Architecture analysis: 2026-02-23*

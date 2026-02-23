# Coding Conventions

**Analysis Date:** 2026-02-23

## Naming Patterns

**Files:**
- `snake_case` for all Python files: `github_tool.py`, `serper_tool.py`, `email_pipeline.py`
- `_tool.py` suffix for tool implementations in `tools/`: `github_tool.py`, `playwright_tool.py`
- `_legacy.py` suffix for deprecated/replaced files: `orchestrator_legacy.py`
- Module `__init__.py` used in `agent/`, `tools/`, `evals/`, `benchmarks/`

**Classes:**
- `PascalCase` throughout: `GitHubTool`, `SerperSearchTool`, `EmailPipeline`, `ToolRegistry`, `InMemoryCache`
- Protocol classes follow same convention: `Tool`, `Cache`
- Tool class names describe their purpose, not their tool name: `CommunityActivityTool`, `PlaywrightTool`

**Functions:**
- `snake_case` for all functions: `enrich_lead`, `plan`, `extract`, `score_case`
- Private helpers prefixed with `_`: `_fallback_plan`, `_repair_truncated_json_object`, `_build_combined`, `_ts`
- Node functions (LangGraph) use `_node` suffix: `planner_node`, `extractor_node`, `email_pipeline_node`
- Layer methods in `EmailPipeline` use `_layer_` prefix: `_layer_github`, `_layer_regex`, `_layer_smtp`, `_layer_hunter`

**Variables:**
- `snake_case` for locals and module-level vars: `trace_id`, `tool_results`, `cache_key`
- Constants in `UPPER_SNAKE_CASE`: `GITHUB_API`, `MAX_QUERIES`, `SERPER_SEARCH_URL`, `MAX_CONTEXT`, `SMTP_LAYER_TIMEOUT`
- Module-level singletons use short names: `cache`, `registry`, `logger`
- Timing variable consistently named `t0` across all tools and nodes
- `trace_id` passed as 12-char hex string: `uuid.uuid4().hex[:12]`

**Types:**
- `PascalCase` for Pydantic models: `EnrichRequest`, `EnrichResponse`, `ToolResult`, `PlannerDecision`
- `PascalCase` for TypedDicts: `AgentState`
- `PascalCase` for Protocol classes: `Tool`, `Cache`

## Code Style

**Formatting:**
- No formatter config file found (no `.ruff.toml`, `pyproject.toml`, or `setup.cfg`)
- Consistent 4-space indentation throughout
- One blank line between methods, two blank lines between top-level definitions
- Line length roughly follows PEP 8 (80-100 chars)
- Visual alignment sections in files using `# ── Section Name ───` banner comments

**Linting:**
- No linting config found (no `.flake8`, `pyproject.toml[tool.ruff]`)
- `# noqa: F401` used sparingly for intentional side-effect imports:
  - `import tools  # noqa: F401 — triggers tool registration` in `agent/graph.py`
- `from __future__ import annotations` used in nearly every module (deferred type evaluation)

## Import Organization

**Pattern used consistently:**
1. `from __future__ import annotations` (always first when present)
2. Standard library (`asyncio`, `json`, `logging`, `re`, `time`)
3. Third-party packages (`httpx`, `anthropic`, `pydantic`, `langgraph`)
4. Internal `agent.*` imports
5. Internal `tools.*` imports
6. Internal `config` imports

**Path Aliases:**
- None — absolute imports throughout, no path aliases or `__init__` re-exports in `agent/`
- `tools/__init__.py` used as a registration module (side-effect import pattern)

**Lazy imports:**
- Heavy optional dependencies imported inside functions to defer cost: `dns.resolver` in `email_pipeline.py`, `qdrant_client` and `openai` in `semantic_cache.py`

## Error Handling

**The golden rule: tools NEVER raise uncaught exceptions.** Every public `run()` method wraps its body in `try/except Exception` and returns `ToolResult(success=False, error=str(e))`.

**Pattern — tool method:**
```python
async def run(self, name: str, company: str, **kwargs) -> ToolResult:
    t0 = time.time()
    try:
        # ... logic ...
        return ToolResult(tool_name=self.name, raw_data=..., success=True,
                          latency_ms=(time.time() - t0) * 1000)
    except Exception as e:
        logger.error(f"ToolName error: {e}")
        return ToolResult(tool_name=self.name, success=False, error=str(e),
                          latency_ms=(time.time() - t0) * 1000)
```

**Pattern — LangGraph nodes:**
Nodes catch exceptions and append to `errors` list rather than raising:
```python
errors: list[str] = []
try:
    decision = await plan(...)
except Exception as e:
    logger.error(f"[{trace_id}] Planner exception: {e}, using fallback")
    decision = _fallback_plan(...)
    errors.append(f"planner: {e}")
return {"decision": decision, "errors": errors}
```

**Pattern — LLM extractors:**
Graceful fallback to minimal object:
```python
except json.JSONDecodeError:
    data = _repair_truncated_json_object(raw)
    if data is None:
        return EnrichedProfile(name=name, company=company)
```

**Retry logic:**
Used via `@retry_with_backoff()` decorator (defined in `agent/utils.py`) for external API calls. Only retries on: 5xx, 429, timeout, connection errors. 4xx errors are NOT retried.

```python
@retry_with_backoff()
async def _try_search(self, client: httpx.AsyncClient, query: str) -> str | None:
    resp = await self._request(...)
    resp.raise_for_status()  # raises httpx.HTTPStatusError on non-2xx
    ...
```

**Optional integrations:**
Services like Langfuse and semantic cache use guard checks at the top of every function:
```python
if not _enabled():
    return None
```

## Logging

**Framework:** Python stdlib `logging`

**Setup pattern (every module):**
```python
logger = logging.getLogger(__name__)
```

**Root logger config:** `logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")` in `main.py`

**Structured log format with trace_id:** All agent-level log messages include `[{trace_id}]` prefix. Tools log without trace_id (they don't receive it in their `run()` interface):
```python
logger.info(f"[{trace_id}] [{_ts(t0)}] PHASE A — planner START")
logger.error(f"GitHubTool error: {e}")
```

**Log levels:**
- `INFO`: Phase start/finish, cache hits, tool success, results summary
- `WARNING`: Non-fatal failures, fallbacks triggered, cache misses for optional services
- `ERROR`: Exceptions caught in top-level handlers, tool failures

**Wall-clock timing in logs:** Helper `_ts(t0)` formats elapsed seconds: `f"+{time.time() - t0:.1f}s"`, used in all phase/tool log lines.

## Concurrency Patterns

**`asyncio.gather()` for parallel execution:**
```python
profile, repos, starred, events = await asyncio.gather(
    self._get_profile(client, login),
    self._get_repos(client, login),
    ...
)
```

**`asyncio.gather(*tasks, return_exceptions=True)` with explicit error inspection:**
```python
results_raw = await asyncio.gather(*tasks, return_exceptions=True)
for i, result in enumerate(results_raw):
    if isinstance(result, Exception):
        errors.append(f"{name}: {result}")
```

**Sync-in-async with `run_in_executor`:**
DNS and CPU-bound calls offloaded:
```python
await loop.run_in_executor(None, socket.getaddrinfo, hostname, None)
```

**Timeout wrapping:**
```python
return await asyncio.wait_for(
    self._layer_smtp_inner(first, last, domains),
    timeout=self.SMTP_LAYER_TIMEOUT,
)
```

## Type Annotations

**Used pervasively.** All function signatures annotated with parameter types and return types:
```python
async def plan(name: str, company: str, trace_id: str, location: str = "") -> PlannerDecision:
```

**Union types use `X | Y` syntax** (Python 3.10+):
```python
profile: EnrichedProfile | None = None
def get(self, name: str) -> Tool | None:
```

**TypedDict for LangGraph state:**
```python
class AgentState(TypedDict, total=False):
    tool_results: Annotated[list[ToolResult], operator.add]  # parallel-safe
```

**`from __future__ import annotations`** used in nearly every file to allow forward references and modern union syntax on Python 3.9.

## Pydantic Models

**Default values are always explicit** (never rely on Pydantic defaults without stating them):
```python
class EnrichedProfile(BaseModel):
    name: str = ""
    skills: list[str] = Field(default_factory=list)
    github: GitHubProfile | None = None
```

**`Field()` used for:**
- `default_factory` for mutable defaults (lists)
- Constraints: `max_length`, `pattern`, `ge`, `le`
- UUIDs: `Field(default_factory=lambda: uuid.uuid4().hex[:12])`

**Validators use `@field_validator` with `mode="before"`** for coercion, `@classmethod` decorator required:
```python
@field_validator("conflicts", "recent_news", ..., mode="before")
@classmethod
def _coerce_str_list(cls, v): ...
```

**`model_validate()` / `model_validate_json()`** used (not deprecated `.parse_*()` methods).

## Constants and Configuration

**Module-level constants in UPPER_SNAKE_CASE** at top of file:
```python
GITHUB_API = "https://api.github.com"
MAX_QUERIES = 5
MAX_CHARS = 15000
SMTP_LAYER_TIMEOUT = 8
```

**All config loaded from `config.py`** (never `os.getenv()` directly in tool files):
```python
from config import GITHUB_TOKEN, HTTP_TIMEOUT
```

**Feature flags via env var emptiness:**
```python
self._use_auth = bool(GITHUB_TOKEN)
if not SERPER_API_KEY:
    return ToolResult(..., success=False, error="SERPER_API_KEY not configured")
```

## Comments

**Docstrings on:**
- Module-level (brief purpose statement): `"""Retry decorator with exponential backoff for API calls."""`
- Public class methods: `"""Check if domain has MX records. Returns (has_mx, mx_host)."""`
- Complex private helpers: `"""Try to repair a truncated JSON object by closing open brackets/braces."""`

**Inline comments for non-obvious logic:**
```python
raw = re.sub(r"^```json\s*", "", raw)  # Strip markdown json fences if present
```

**Section banners** to organize long files:
```python
# ── Helpers ──────────────────────────────────────────────────────────────
# ── Node functions ───────────────────────────────────────────────────────
# ── Graph construction ──────────────────────────────────────────────────
```

**Decision rationale documented in `tools/__init__.py`:**
```python
# EmailPipeline NOT registered — orchestrator calls it directly
# HunterIoTool NOT registered — called internally by EmailPipeline
```

## Function Design

**Size:** Short, single-purpose methods. Long orchestration functions (e.g. `build_graph`, phase nodes) are split by phase.

**Parameters:** `name: str, company: str` as the standard tool interface, with `**kwargs` for tool-specific extras. Trace/observability context passed explicitly as `trace_id: str`.

**Return values:**
- Tools always return `ToolResult` (never raise)
- LangGraph nodes return `dict` of state updates
- LLM helpers return the result type directly, fallback to empty/minimal on failure

## Module Design

**Exports:** No explicit `__all__` defined. Consumers import directly from module paths.

**Barrel Files:** `tools/__init__.py` is used as a side-effect registration module, not a barrel export file. `agent/__init__.py` exposes `observe` and `semantic_cache` as importable submodules.

**Singleton pattern:** Module-level singletons for shared resources: `cache = InMemoryCache()`, `registry = ToolRegistry()`, `graph = build_graph()`.

---

*Convention analysis: 2026-02-23*

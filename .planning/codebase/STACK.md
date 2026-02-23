# Technology Stack

**Analysis Date:** 2026-02-23

## Languages

**Primary:**
- Python 3.12 - All application code (agent, tools, API, config)

**Secondary:**
- None - Pure Python project

## Runtime

**Environment:**
- CPython 3.12.3

**Package Manager:**
- pip (pip 24.0 installed in venv)
- Lockfile: Not present (requirements.txt without pinned versions)

## Frameworks

**Core:**
- FastAPI - HTTP API server (`main.py`); provides POST /enrich and GET /health endpoints
- Pydantic v2 - Data validation and serialization (`agent/schemas.py`); all request/response models use BaseModel
- LangGraph - Agentic pipeline orchestration (`agent/graph.py`); StateGraph with parallel fan-out/fan-in nodes
- uvicorn - ASGI server (`main.py`); launched with `uvicorn.run(app, host="0.0.0.0", port=8000)`

**Testing / Evaluation:**
- Custom eval framework (`evals/run_eval.py`, `evals/evaluator.py`) — no pytest; ground truth in `evals/ground_truth.json`
- Custom benchmark runner (`benchmarks/benchmark.py`)

**Build/Dev:**
- python-dotenv - `.env` file loading (`config.py`)
- Standard library `asyncio` - All concurrency (gather, create_task, wait_for)

## Key Dependencies

**Critical:**
- `anthropic` - Claude API client; used in `agent/planner.py` and `agent/extractor.py` via `AsyncAnthropic`
- `langgraph` - StateGraph pipeline engine; `agent/graph.py` builds and compiles the graph
- `pydantic` + `pydantic-settings` - All schema validation; `agent/schemas.py` contains all models
- `httpx` - Async HTTP client used by all tools (GitHub, Serper, Hunter)
- `playwright` 1.58.0 - Headless Chromium browser scraping (`tools/playwright_tool.py`)

**Infrastructure:**
- `dnspython` - MX record resolution for SMTP email verification (`tools/email_pipeline.py`)
- `qdrant-client` - Async vector store client for semantic cache (`agent/semantic_cache.py`)
- `openai` - OpenAI embeddings client (`text-embedding-3-small`) used only by semantic cache (`agent/semantic_cache.py`)
- `langfuse` - LLM observability/tracing (`agent/observe.py`); no-op when keys absent
- `tenacity` - (installed, not directly imported in app code — may be used by langfuse)

**Resilience:**
- Custom `retry_with_backoff` decorator (`agent/utils.py`) — exponential backoff (1s, 2s, 4s) with jitter; retries on 5xx, 429, timeouts, connection errors only; does NOT retry 4xx

## Configuration

**Environment:**
- All config loaded from `.env` via `python-dotenv` in `config.py`
- Graceful fallback: missing keys yield empty string (tools check and return ToolResult(success=False))
- Semantic cache is feature-flagged: only activates when `QDRANT_URL`, `QDRANT_API_KEY`, and `OPENAI_API_KEY` are all set

**Key configuration variables (from `config.py`):**
- `ANTHROPIC_API_KEY` - Required for all LLM calls
- `ANTHROPIC_MODEL` - Planner + narrative model (default: `claude-sonnet-4-20250514`)
- `EXTRACTOR_MODEL` - Extraction model (default: `claude-haiku-4-5-20251001`)
- `GITHUB_TOKEN` - GitHub API auth (optional; falls back to unauthenticated at 10 req/min)
- `SERPER_API_KEY` - Web search + news + Reddit search
- `HUNTER_API_KEY` - Email finder (last resort, 25/month limit)
- `SCRAPERAPI_KEY` - Optional rotating proxy for Playwright
- `PROXY_LIST` - Comma-separated additional proxies
- `QDRANT_URL` + `QDRANT_API_KEY` - Vector DB for semantic cache
- `OPENAI_API_KEY` - Embeddings for semantic cache only
- `SEMANTIC_CACHE_THRESHOLD` - Cosine similarity threshold (default: 0.92)
- `PLAYWRIGHT_TIMEOUT` - Browser timeout ms (default: 15000)
- `HTTP_TIMEOUT` - httpx request timeout seconds (default: 15)
- `LANGFUSE_PUBLIC_KEY` + `LANGFUSE_SECRET_KEY` + `LANGFUSE_HOST` - Optional observability

**Build:**
- No build step required; run directly with `python main.py` or `uvicorn main:app`
- Playwright browsers must be installed separately: `playwright install chromium`

## Caching Architecture

**In-process cache:** `agent/cache.py` — `InMemoryCache` singleton with TTL support
- Cache keys: `github:{name}:{company}`, `search:{query}`, `browser:{url}`, `news:{query}`, `community:{name}:{company}`, `hunter:{name}:{company}`
- TTL: 300s for most tools, 600s for GitHub
- Not shared across processes; resets on restart

**Semantic cache:** `agent/semantic_cache.py` — Qdrant vector store
- Uses OpenAI `text-embedding-3-small` (1536-dim) to embed `{name} {company}`
- Cosine similarity threshold: 0.92 (configurable)
- TTL: 30 days
- Collection name: `lead_cache`
- Only caches successful responses; no-op if any of three required env vars missing

## Platform Requirements

**Development:**
- Python 3.12+
- Chromium (via `playwright install chromium`)
- `.env` file with at minimum `ANTHROPIC_API_KEY` and `SERPER_API_KEY`

**Production:**
- No containerization config detected (no Dockerfile, docker-compose.yml)
- ASGI server: uvicorn (standard extras: uvloop, httptools, websockets, watchfiles)
- Port: 8000

---

*Stack analysis: 2026-02-23*

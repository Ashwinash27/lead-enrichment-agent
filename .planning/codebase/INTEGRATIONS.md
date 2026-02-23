# External Integrations

**Analysis Date:** 2026-02-23

## APIs & External Services

**LLM / AI:**
- Anthropic Claude — Core intelligence: planning, extraction, narrative, talking points
  - SDK/Client: `anthropic` (`AsyncAnthropic`) in `agent/planner.py` and `agent/extractor.py`
  - Auth: `ANTHROPIC_API_KEY` env var
  - Models: `ANTHROPIC_MODEL` (planner, narrative, talking points; default `claude-sonnet-4-20250514`), `EXTRACTOR_MODEL` (extraction; default `claude-haiku-4-5-20251001`)
  - Call pattern: wrapped in `llm_create()` (`agent/utils.py`) with `retry_with_backoff` (3 attempts, exponential backoff)
  - Rate limit handling: retries on 429 and 5xx

- OpenAI — Embeddings only (for semantic cache)
  - SDK/Client: `openai` (`AsyncOpenAI`) in `agent/semantic_cache.py`
  - Auth: `OPENAI_API_KEY` env var
  - Model: `text-embedding-3-small` (1536-dim)
  - Only used when `QDRANT_URL`, `QDRANT_API_KEY`, and `OPENAI_API_KEY` are all present

**Web Search:**
- Serper.dev (Google Search API) — Primary web search
  - Endpoint: `https://google.serper.dev/search` (web), `https://google.serper.dev/news` (news)
  - Used by: `tools/serper_tool.py` (`SerperSearchTool`) and `tools/news_tool.py` (`SerperNewsTool`) and `tools/community_tool.py` (Reddit search)
  - Auth: `X-API-KEY: {SERPER_API_KEY}` header
  - Limits: Max 5 search queries per request (enforced in `tools/serper_tool.py`)
  - Cache: in-memory, 300s TTL per query (`agent/cache.py`)

**Developer Platforms:**
- GitHub API (REST v3) — Developer profile, repos, stars, events
  - Endpoint: `https://api.github.com`
  - Used by: `tools/github_tool.py` (`GitHubTool`)
  - Auth: `Authorization: Bearer {GITHUB_TOKEN}` (optional; unauthenticated falls back to 10 req/min)
  - Endpoints used: `/search/users`, `/users/{login}`, `/users/{login}/repos`, `/users/{login}/starred`, `/users/{login}/events/public`
  - Fallback: if token rejected (401), automatically retries unauthenticated
  - Cache: in-memory, 600s TTL per `github:{name}:{company}` key

**Community Platforms:**
- Hacker News (Algolia API) — HN story and comment search
  - Endpoint: `https://hn.algolia.com/api/v1/search`
  - Used by: `tools/community_tool.py` (`CommunityActivityTool._search_hn`)
  - Auth: None (public API)
  - No cache key of its own; result combined with Reddit and cached as `community:{name}:{company}`

- Reddit — Searched via Serper (`site:reddit.com {query}`)
  - Used by: `tools/community_tool.py` (`CommunityActivityTool._search_reddit`)
  - Auth: `SERPER_API_KEY` (Serper proxies the search)
  - Not a direct Reddit API call; no Reddit credentials required

**Email Discovery:**
- Hunter.io — Last-resort email finder (Layer 4 of waterfall)
  - Endpoint: `https://api.hunter.io/v2/email-finder`
  - Used by: `tools/email_pipeline.py` (`EmailPipeline._layer_hunter`)
  - Auth: `api_key={HUNTER_API_KEY}` query parameter
  - Limit: 25 lookups/month (free plan) — called only after 3 free layers fail
  - Gating: only called if `HUNTER_API_KEY` is set AND confirmed company domains exist
  - Cache: in-memory, 300s TTL per `hunter:{name}:{company}` key

**Web Scraping Proxies:**
- ScraperAPI — Optional rotating proxy for Playwright
  - Endpoint: `http://scraperapi:{SCRAPERAPI_KEY}@proxy-server.scraperapi.com:8001`
  - Used by: `tools/proxy.py` (`ProxyManager`) → `tools/playwright_tool.py`
  - Auth: `SCRAPERAPI_KEY` env var
  - Behavior: first attempts direct connection; falls back to proxy if scrape fails

- Custom proxy list — Additional rotating proxies
  - Config: `PROXY_LIST` env var (comma-separated)
  - Used by: `tools/proxy.py` (`ProxyManager`)
  - Cycled round-robin alongside ScraperAPI

## Data Storage

**Databases:**
- Qdrant (vector database) — Semantic cache for enrichment responses
  - Connection: `QDRANT_URL` + `QDRANT_API_KEY` env vars
  - Client: `qdrant-client` (`AsyncQdrantClient`) in `agent/semantic_cache.py`
  - Collection: `lead_cache` (auto-created if missing)
  - Vector spec: 1536-dim cosine similarity
  - TTL: 30 days (enforced in application logic, not by Qdrant)
  - Feature-flagged: entirely disabled when any of the three required env vars is missing

**In-Process Cache:**
- `InMemoryCache` (`agent/cache.py`) — TTL-based dict cache
  - No external dependency; stores tool results during a process lifetime
  - Resets on restart; not shared across workers

**File Storage:**
- Local filesystem only — eval ground truth at `evals/ground_truth.json`, benchmark/eval results at `benchmarks/results.json` and `evals/results.json`
- No cloud storage integration

**Caching:**
- Two-tier: in-memory (`agent/cache.py`) for short-lived tool results + Qdrant semantic cache for full enrichment responses

## Authentication & Identity

**Auth Provider:**
- None — the service itself has no user authentication
- No API keys, JWT, OAuth for the FastAPI endpoints
- CORS is fully open: `allow_origins=["*"]` in `main.py`

## Monitoring & Observability

**LLM Tracing:**
- Langfuse — Full LLM pipeline observability
  - SDK: `langfuse` (`Langfuse` client) in `agent/observe.py`
  - Auth: `LANGFUSE_PUBLIC_KEY` + `LANGFUSE_SECRET_KEY` env vars
  - Host: `LANGFUSE_HOST` (default: `https://cloud.langfuse.com`)
  - Features used: traces per request, spans per LangGraph node (`traced_node` wrapper), generation logs per LLM call (planner, extractor, narrative, talking_points)
  - Graceful no-op: all observability calls are try/except; missing keys disable it entirely
  - Flush called after each request in `agent/orchestrator.py`

**Logs:**
- Python stdlib `logging` module
- Format: `%(asctime)s %(levelname)s %(name)s %(message)s` (configured in `main.py`)
- Level: INFO by default
- No external log aggregation detected

**Error Tracking:**
- None (no Sentry or equivalent)

## CI/CD & Deployment

**Hosting:**
- Not detected — no Dockerfile, Procfile, Railway/Heroku config, or cloud deployment manifests

**CI Pipeline:**
- Not detected — no `.github/workflows/`, `.gitlab-ci.yml`, or equivalent

## Email Discovery Waterfall

The email pipeline (`tools/email_pipeline.py`) calls external services in a specific order to conserve rate-limited credits:

1. **Layer 1 — GitHub public email:** Parse `email:` field from GitHub tool result (no new API call)
2. **Layer 2 — Regex scan:** Search all tool result text for `name@domain` pattern (no new API call)
3. **Layer 3 — SMTP verification:** DNS MX lookup (`dnspython`) + raw SMTP RCPT TO check on port 25 (no external API; direct network calls; 8s hard timeout for entire layer)
4. **Layer 4 — Hunter.io:** REST API call to `hunter.io/v2/email-finder`; only called if `HUNTER_API_KEY` is set and domains are confirmed

## Webhooks & Callbacks

**Incoming:**
- None — no webhook endpoints on the service

**Outgoing:**
- None — all integrations are request/response (no event-driven callbacks)

## Environment Configuration

**Required env vars (service won't function without these):**
- `ANTHROPIC_API_KEY` — all LLM calls fail without it
- `SERPER_API_KEY` — web search, news, and Reddit search disabled without it

**Optional env vars (degrade gracefully when absent):**
- `GITHUB_TOKEN` — runs unauthenticated (10 req/min vs 30 req/min)
- `HUNTER_API_KEY` — Layer 4 email discovery skipped
- `SCRAPERAPI_KEY` + `PROXY_LIST` — Playwright scrapes directly without proxy
- `QDRANT_URL` + `QDRANT_API_KEY` + `OPENAI_API_KEY` — semantic cache disabled (all three required together)
- `LANGFUSE_PUBLIC_KEY` + `LANGFUSE_SECRET_KEY` — observability disabled
- `EXTRACTOR_MODEL` — defaults to `claude-haiku-4-5-20251001`
- `ANTHROPIC_MODEL` — defaults to `claude-sonnet-4-20250514`

**Secrets location:**
- `.env` file at project root (listed in `.gitignore`)
- `.env.example` at project root shows all variable names with placeholder values

---

*Integration audit: 2026-02-23*

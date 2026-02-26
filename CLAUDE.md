# Lead Research Agent — Project Instructions

## Project Status: PRODUCTION (Pipeline + Homepage + Extension + Rate Limiting)

## What This Project Is
An AI-powered lead enrichment agent that takes a person's name + company, researches them across public sources (Serper search, Serper news, GitHub, HN/Reddit community, Playwright browser, waterfall email pipeline), and returns a structured JSON profile with conversation-ready talking points. Uses Claude as both planner and extractor.

## Architecture
- Core loop: Plan → Execute (parallel phases) → Extract → Talking Points → Return
- Two LLM calls per request: Planner (~500 tokens) + Extractor (~3000 tokens)
- Three execution phases: A (concurrent: planner + deterministic tools), B (planner-dependent: search + browser), B.5 (email waterfall)
- All tools return ToolResult envelopes (never throw uncaught exceptions)
- Serper replaces DuckDuckGo for web search (faster, more reliable, supports news endpoint)
- Email discovery: 5-layer waterfall (GitHub email → regex scan → SMTP verify → Prospeo → Hunter.io fallback)
- Apollo is dead — free plan has zero API access. Removed entirely.

## Key Architecture Decisions
- **Apollo removed**: Free plan returns 403 "API_INACCESSIBLE" on all endpoints. Not worth integrating.
- **Prospeo as Layer 4**: 75 free credits/month, verified emails only, header-based auth (X-KEY). Called before Hunter.
- **Hunter demoted to Layer 5**: Fallback after Prospeo. 25 free lookups/month, API key in query params.
- **Email waterfall**: Try 3 free methods before burning Prospeo/Hunter credits. Orchestrator-managed, not planner-controlled.
- **Parallel planner**: Planner runs concurrently with deterministic tools (GitHub, news, community) in Phase A.
- **use_case framing**: Request includes `use_case` (sales/recruiting/job_search) to customize talking points.
- **Rate limiting**: In-memory sliding window per API key (50/hour default). `RATE_LIMIT_PER_HOUR=0` disables.
- **Concurrency semaphore**: `asyncio.Semaphore(MAX_CONCURRENT_ENRICHMENTS)` wraps `graph.ainvoke()`. Requests queue, not reject.
- **Homepage**: Jinja2 template at GET `/` with playground form + architecture diagram. No SSE — uses POST `/enrich`.
- **GitHub scoring**: Weighted candidate scoring (+3 name, +2 company, +1 username, +0.5 bio, -5 name mismatch) replaces first-match.

## Pipeline Phases
```
Phase A (concurrent):  planner(name, company) | github(name, company) | news(name, company) | community(name, company)
Phase B (planner-dependent): web_search(queries) | browser(urls)
Phase B.5 (email):    email_pipeline(name, company, all_results)
Phase C (extract):    extract(all_results) → talking_points(profile, use_case)
```

## Cache Keys
- `github:{name}:{company}`, `search:{query}`, `browser:{url}`
- `news:{query}`, `community:{name}:{company}`
- `prospeo:{name}:{company}`, `hunter:{name}:{company}` (inside email pipeline)

## Rules — What To Do
- Always run tools concurrently via asyncio.gather()
- Every tool must catch ALL exceptions and return ToolResult(success=False)
- Validate planner output against registry (filter hallucinated tool names)
- Fallback gracefully: planner fails → deterministic plan, extractor fails → minimal profile
- Cache tool results (not planner/extractor calls)
- Truncate LLM context to 20k chars normal / 30k on retry / 15k for talking points
- Email pipeline runs after all other tools (needs their results for layers 1-2)
- Hunter.io only called with confirmed domain (never guess domains)

## Rules — What NOT To Do
- Never let a tool exception crash the orchestrator
- Never block the event loop (use run_in_executor for sync code)
- Never cache planner or extractor results
- Never trust planner tool names without validation
- Never send more than 20k chars to the extractor (30k on retry)
- Never scrape more than 3 URLs per request
- Never run more than 5 search queries per request
- Never call Prospeo/Hunter without exhausting free email methods first
- Never register EmailPipeline in the tool registry (orchestrator calls it directly)

## Key Files
- `main.py` — FastAPI entry point (GET /, POST /enrich, GET /enrich/stream, GET /health)
- `middleware.py` — Rate limiting middleware (sliding window, 429 + Retry-After)
- `agent/orchestrator.py` — Main pipeline (Phase A/B/B.5/C dispatch) + concurrency semaphore
- `agent/planner.py` — Claude decides which tools to use + search queries
- `agent/extractor.py` — Claude extracts structured profile from raw data
- `agent/schemas.py` — All Pydantic data models
- `tools/` — GitHub, Serper search, Serper news, community (HN+Reddit), Playwright, email pipeline, Hunter.io
- `tools/email_pipeline.py` — Waterfall email: GitHub → regex → SMTP → Prospeo → Hunter
- `config.py` — Environment variable loading
- `test_agent.py` — CLI test script
- `templates/index.html` — Homepage template (playground + architecture)
- `static/css/style.css` — Homepage styles (dark theme matching extension)
- `static/js/app.js` — Homepage JS (form submit, profile rendering, error states)
- `extension/` — Chrome Manifest V3 extension for LinkedIn enrichment

## Tool Registry (planner-visible)
- `github` — GitHub profile, repos, activity
- `web_search` — Serper Google search
- `news` — Serper news search
- `community` — HN Algolia + Reddit via Serper
- `browser` — Playwright headless browser

## Internal Tools (not planner-visible)
- `email_pipeline` — Orchestrator-managed waterfall email finder
- `prospeo` — Called inside email_pipeline Layer 4 (75 free/month)
- `hunter` — Called inside email_pipeline Layer 5 (25 free/month)

## API Keys Required
- `ANTHROPIC_API_KEY` — Claude API
- `GITHUB_TOKEN` — GitHub API (30 req/min vs 10 unauthenticated)
- `SERPER_API_KEY` — Serper.dev (search + news)
- `PROSPEO_API_KEY` — Prospeo (75 free credits/month)
- `HUNTER_API_KEY` — Hunter.io (25 free lookups/month, fallback)
- `SCRAPERAPI_KEY` — Optional proxy for browser tool

# Design Document — Lead Research Agent

## The Core Insight: LLM as Parser, Not as Browser

The fundamental design choice in this system is using Claude in two narrow, well-defined roles — **planner** and **extractor** — rather than giving it tools and letting it loop (ReAct-style).

Why this matters: a ReAct agent would make 5-10 LLM calls per request, each one deciding what to do next. That's 30-60 seconds of latency and $0.10+ per enrichment. We make exactly **two calls**: one to plan (~500 tokens, fast), one to extract (~2000 tokens, thorough). Everything in between is deterministic code running tools concurrently.

The LLM never touches the network directly. It never sees HTML. It reads **pre-processed text summaries** from tools and extracts structure. This is the same pattern that works at scale: the LLM is a parser, not an actor.

```
Traditional agent:  LLM → tool → LLM → tool → LLM → tool → LLM → done
Our approach:       LLM → [tool, tool, tool] → LLM → done
```

The tradeoff: we lose adaptivity. A ReAct agent could say "that search didn't work, let me try a different query." We can't. But for lead enrichment, the first search almost always works — the marginal value of a second LLM-guided attempt doesn't justify doubling latency.

---

## Why the Tool Protocol Matters

Every tool implements the same interface:

```python
class Tool(Protocol):
    name: str
    description: str
    async def run(self, name: str, company: str, **kwargs) -> ToolResult
```

This isn't just clean code. It's the **extensibility seam** for the entire system. Adding a new data source (Crunchbase, Twitter/X, HackerNews, Patent databases) means writing one class, registering it, and updating the planner prompt. The orchestrator, extractor, cache, and API layer don't change.

The `ToolResult` envelope is equally important. Every tool returns `ToolResult(success=False, error=...)` on failure — **never throws**. This means `asyncio.gather()` always completes, the orchestrator always has results to work with, and one flaky API doesn't take down the whole request.

This is a deliberate choice against the common pattern of letting exceptions propagate and catching them at the boundary. When you run 3 tools concurrently and one throws, the exception handling gets messy. Envelope returns keep it simple.

---

## What's Hard About LinkedIn (and Why We Don't Scrape It)

LinkedIn is the single most valuable data source for lead enrichment, and we intentionally don't scrape it directly. Here's why:

1. **Authentication wall**: LinkedIn serves different content to logged-in vs logged-out users. Profile pages are heavily gated. Playwright can render the page, but you get a "sign in to view" wall for most fields.

2. **Anti-bot detection**: LinkedIn uses advanced fingerprinting beyond User-Agent — canvas fingerprinting, WebGL hashes, mouse movement analysis, request timing patterns. Rotating proxies alone won't beat it consistently.

3. **Legal risk**: LinkedIn v. hiQ Labs established some precedent for public data scraping, but LinkedIn's ToS explicitly prohibits automated access. For a production product, this is a legal conversation, not an engineering one.

4. **What we do instead**: Our web search queries include "{name} LinkedIn" — DuckDuckGo often returns LinkedIn profile snippets (title, headline, location) in search results. The extractor pulls what it can from these snippets. It's less data but zero legal risk.

**At scale**, the right approach is LinkedIn's official APIs (Sales Navigator, Marketing API) or third-party enrichment providers (Clearbit, Apollo) who have data partnerships. These are paid but legal and reliable.

---

## The GitHub Search Problem

GitHub's user search API is surprisingly bad at matching real names to accounts. Searching "Linus Torvalds Linux Foundation" returns zero results on unauthenticated access, even though `torvalds` is one of the most-followed accounts on the platform.

Why: GitHub search matches against username, email, and full name fields. Adding "Linux Foundation" to the query makes it look for users where all three terms appear, and GitHub's search doesn't do fuzzy matching well across fields.

Our fix: **progressive query broadening**. Search "name company" first, then fall back to "name" alone. This catches the Linus Torvalds case without losing precision for cases where the company narrows the results (e.g., "John Smith Google" vs "John Smith").

Authenticated access helps — GitHub gives you 30 requests/minute with a token vs 10 without, and search results are slightly better. But the broadening strategy is the real fix.

---

## What Changes at Scale

This MVP is single-process, in-memory, one-request-at-a-time-ish. Here's what changes when you go from demo to product:

### Caching: In-Memory → Redis
The current `InMemoryCache` loses everything on restart and can't share across workers. The async interface (`async get/set/delete`) was designed for this exact swap — Redis via `aioredis` is a drop-in replacement. Cache GitHub profiles for 24h, search results for 1h, browser scrapes for 30m.

### Concurrency: Process → Worker Pool
Uvicorn with `--workers 4` gives you multiprocessing, but each worker has its own cache and browser instances. At real scale: Celery/Dramatiq for background task processing, Redis for shared state, and a request queue so you can return a job ID immediately instead of blocking for 15 seconds.

### Browser: Per-Request → Pool
Launching a new Chromium instance per URL is ~2 seconds of overhead. A browser pool (3-5 persistent browsers, contexts rotated) drops this to ~200ms. The tradeoff is state leaks and zombie processes — needs a health-check loop.

### Proxy Strategy
Playwright routes through ScraperAPI (or any rotating proxy) when configured. The proxy manager parses credentials into Playwright's required format (separate `server`, `username`, `password` fields) and Chromium launches with `--ignore-certificate-errors` since proxy services perform TLS interception. Each URL gets exactly one attempt — no retries. If a URL times out through a proxy, it's dead; retrying wastes time. The planner compensates by trying multiple TLD variants (.com, .ai, .io) so at least one is likely to resolve.

### The Two-LLM-Call Budget
This actually scales well. The planner call is small (~500 tokens in, ~200 out). The extractor is larger but bounded by our 30k char truncation. At $3/M input tokens for Sonnet, each enrichment costs ~$0.01. The real cost driver is tool execution time, not LLM spend.

### Rate Limiting
No rate limiting in the MVP. In production: per-API-key limits (token bucket), per-IP limits (sliding window), and circuit breakers on downstream APIs (GitHub, DuckDuckGo) to avoid cascade failures.

### What I'd Add First
1. **Redis cache** — biggest bang for buck. Second request for the same person is instant.
2. **Async job queue** — return a job ID, let the client poll. Eliminates timeout issues.
3. **LinkedIn API integration** — the most impactful data source, but requires a partnership or Sales Navigator license.
4. **Browser pool** — persistent Chromium instances with context rotation instead of launching per-request.

---

## Why Not LangChain / CrewAI / AutoGen

These frameworks solve a real problem — orchestrating multi-step LLM workflows with tool use. But they add abstraction layers that hurt more than they help at this scale:

- **LangChain**: Would wrap our two Claude calls in chains, our tools in LangChain tools, our prompts in prompt templates. For two calls, that's pure overhead. Debugging means reading LangChain internals, not our code.

- **CrewAI/AutoGen**: Multi-agent frameworks where agents talk to each other. We have one planner and one extractor — they don't need to negotiate. The "crew" would be two agents that run sequentially, which is just... two function calls.

- **What we actually need**: `anthropic.AsyncAnthropic`, `asyncio.gather`, and Pydantic. The 50 lines of orchestrator code are simpler to debug, modify, and explain than any framework abstraction.

The right time to adopt a framework is when you have >5 LLM calls per request with branching logic, or when you need features like memory/conversation history across calls. We have neither.

---

## Design Principles

1. **Fail partially, not completely.** If GitHub is down, return web search results. If the extractor can't parse JSON, return a minimal profile with just the name. Never return a 500 for bad tool data.

2. **The LLM sees text, not HTML.** Every tool pre-processes its output into clean text summaries. The extractor prompt is designed for text parsing, not HTML parsing. This makes the LLM's job easier and cheaper.

3. **Cache at the tool layer, not the LLM layer.** Tool results are deterministic-ish (same GitHub profile today and tomorrow). LLM outputs vary. Cache the stable layer.

4. **Extensibility through the protocol, not through configuration.** Adding a new tool is "write a class, register it." Not "add an entry to a YAML file and hope the framework routes it correctly."

5. **Two calls is the budget.** Every design decision filters through: does this add a third LLM call? If yes, it needs to be worth 5+ seconds of latency. Almost nothing is.

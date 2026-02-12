# Lead Research Agent

## What Is This?

Sales teams, recruiters, and founders spend hours manually researching leads — clicking through LinkedIn, Googling names, checking GitHub profiles, reading company pages. This project automates that entire workflow.

Give it a person's name and company, and it returns a comprehensive, structured profile in seconds. It searches across multiple public sources simultaneously, pulls together everything it finds, and returns clean JSON with confidence scores so you know how much to trust each piece of data.

**The problem it solves:** Manual lead research takes 10-15 minutes per person. This agent does it in under 40 seconds by running searches in parallel and using an LLM to extract structured data from raw search results.

**How it works at a high level:**
1. An LLM plans which sources to search (GitHub, web search, company websites, email finder)
2. All searches run concurrently via asyncio
3. A second LLM call extracts a structured profile from the combined raw results
4. The output is a Pydantic-validated JSON profile with 15+ fields, confidence scores, and source-attributed findings

```
POST /enrich {"name": "Guillermo Rauc", "company": "Vercel"}

→ Role, bio, education, skills, GitHub, LinkedIn, website,
  confidence scores, source-attributed findings — all in structured JSON.
```

## Architecture

```
Client (POST /enrich) → {"name": "Guillermo Rauc", "company": "Vercel"}
    │
    ▼
FastAPI Server (main.py)
    │  Started with: source venv/bin/activate && python3 main.py
    │  Swagger UI: http://localhost:8000/docs
    ▼
Orchestrator (orchestrator.py)
    │
    ├── Step 1: PLANNER (Claude LLM call #1) 3-5s
    │   └── Input: name + company + tool descriptions
    │   └── Output: which tools, what queries, which URLs
    │   └── Fallback: hardcoded plan if Claude fails
    │
    ├── Step 2: PARALLEL EXECUTION (asyncio.gather)  18-25s total
    │
    │   ┌─── Event Loop (main thread) ──────────────────────────┐
    │   │                                                        │
    │   │   GitHubTool ──── GitHub REST API ──── httpx (async)   │
    │   │    1-2s                                              │
    │   │                                                        │
    │   │   HunterTool ──── Hunter.io API ───── httpx (async)    │
    │   │    1-2s  Extracts domains from planner's URLs        │
    │   │            sixtyfour.com → no email → try next          │
    │   │            sixtyfour.ai → saarth@sixtyfour.ai → stop   │
    │   │                                                        │
    │   │   PlaywrightTool ─── DNS check first ─── then scrape   │
    │   │    14-25s  sixtyfour.ai → DNS  → scrape            │
    │   │              sixtyfour.io → DNS  → skip instantly     │
    │   │              sixtyfour.com → DNS  → scrape            │
    │   │                                                        │
    │   └────────────────────────────────────────────────────────┘
    │   ┌─── Thread Pool (background threads) ──────────────────┐
    │   │                                                        │
    │   │   WebSearchTool ── DuckDuckGo ── run_in_executor()     │
    │   │    3-4s  (sync library, blocks → runs in thread)     │
    │   │                                                        │
    │   │   DNS Check ────── socket.getaddrinfo() ── in thread   │
    │   │    <0.1s  (blocking call → runs in thread)            │
    │   │                                                        │
    │   └────────────────────────────────────────────────────────┘
    │
    │   Event Loop + Thread Pool run SIMULTANEOUSLY
    │   Total time = slowest tool (browser ~20s), not sum of all
    │
    ├── Step 3: EXTRACTOR (Claude LLM call #2)  13-15s
    │   └── Input: all raw data from tools (truncated to 30k chars)
    │   └── Output: structured JSON validated by Pydantic
    │   └── Fallback: minimal profile (name + company) if fails
    │
    └── Response: EnrichResponse
            ├── success: bool
            ├── profile: EnrichedProfile (15+ fields)
            ├── confidence: 0.0-1.0 per field
            ├── findings: fact + source URL pairs
            ├── sources_searched: ["github", "web_search", "browser", "hunter"]
            ├── errors: [] (any tool failures listed)
            └── latency_ms: ~27000-45000

Total: ~27-45 seconds | Cost: ~$0.01 | LLM calls: exactly 2
```

See [DESIGN.md](DESIGN.md) for architecture decisions and tradeoffs.

## Quick Start

```bash
pip install -r requirements.txt
playwright install chromium

cp .env.example .env
# Set ANTHROPIC_API_KEY in .env
```

**CLI:**
```bash
python test_agent.py "Guillermo Rauch" "Vercel"
```

**API server:**
```bash
uvicorn main:app --reload

curl -X POST http://localhost:8000/enrich \
  -H "Content-Type: application/json" \
  -d '{"name": "Guillermo Rauch", "company": "Vercel"}'
```

## Example Output

```json
{
  "success": true,
  "trace_id": "b7f3e2a91c04",
  "profile": {
    "name": "Guillermo Rauch",
    "role": "CEO",
    "company": "Vercel",
    "location": "San Francisco, CA",
    "bio": "CEO of Vercel, creator of Next.js and Socket.IO. Building the frontend cloud.",
    "education": [],
    "previous_companies": ["LearnBoost", "Cloudup"],
    "github": {
      "username": "rauchg",
      "public_repos": 267,
      "top_languages": ["JavaScript", "TypeScript", "Shell"]
    },
    "linkedin_url": "https://www.linkedin.com/in/guillermo-rauch",
    "website": "https://rauchg.com",
    "confidence": {
      "name": 1.0,
      "company": 1.0,
      "role": 1.0,
      "location": 0.9,
      "email": 0.0,
      "github": 1.0
    },
    "findings": [
      {"fact": "Creator of Next.js, the React framework", "source": "github.com/rauchg"},
      {"fact": "Vercel has raised over $300M in funding", "source": "crunchbase.com/organization/vercel"},
      {"fact": "Created Socket.IO, one of the most popular real-time libraries", "source": "github.com/rauchg"}
    ],
    "sources": ["linkedin.com/in/guillermo-rauch", "github.com/rauchg", "vercel.com", "...5 more"]
  },
  "latency_ms": 32100
}
```

## Project Structure

```
├── main.py                  # FastAPI app
├── config.py                # Env-based settings
├── agent/
│   ├── schemas.py           # Pydantic models
│   ├── tool_protocol.py     # Tool Protocol + registry
│   ├── cache.py             # In-memory cache with TTL
│   ├── planner.py           # LLM-driven tool selection
│   ├── extractor.py         # LLM-driven structured extraction
│   └── orchestrator.py      # Plan → Execute → Extract loop
└── tools/
    ├── github_tool.py       # GitHub REST API
    ├── search_tool.py       # DuckDuckGo web search
    ├── hunter_tool.py       # Hunter.io email finder
    ├── playwright_tool.py   # Headless browser scraper
    └── proxy.py             # Rotating proxy manager
```

## Configuration

| Variable | Required | Description |
|----------|----------|-------------|
| `ANTHROPIC_API_KEY` | Yes | Claude API key for planner + extractor |
| `GITHUB_TOKEN` | Yes | GitHub API — raises rate limit from 10 to 30 req/min |
| `HUNTER_API_KEY` | No | Hunter.io API key for email finding |
| `SCRAPERAPI_KEY` | Yes | Rotating proxy for Playwright browser scraping |

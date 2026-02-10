# Lead Research Agent

AI-powered lead enrichment agent. Give it a name + company → get back a structured JSON profile built from public sources.

```
POST /enrich {"name": "Saarth Shah", "company": "Sixtyfour"}

→ Role, bio, education, skills, GitHub, LinkedIn, website,
  confidence scores, source-attributed findings — all in structured JSON.
```

## Architecture

```
Input: name + company
         │
    ┌────▼─────┐
    │ Planner  │  LLM decides which sources to hit
    └────┬─────┘
         │
    ┌────▼──────────────────────────────┐
    │  Parallel Execution (asyncio)     │
    │  ├─ GitHub REST API               │
    │  ├─ Web Search (DuckDuckGo ×4)    │
    │  └─ Playwright (headless Chrome)  │
    │     └─ via rotating proxies       │
    └────┬──────────────────────────────┘
         │
    ┌────▼──────┐
    │ Extractor │  LLM → structured JSON (Pydantic-validated)
    └────┬──────┘
         │
    EnrichedProfile + confidence scores + findings
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
python test_agent.py "Saarth Shah" "Sixtyfour"
```

**API server:**
```bash
uvicorn main:app --reload

curl -X POST http://localhost:8000/enrich \
  -H "Content-Type: application/json" \
  -d '{"name": "Saarth Shah", "company": "Sixtyfour"}'
```

## Example Output

```json
{
  "success": true,
  "trace_id": "a4ada7b897a8",
  "profile": {
    "name": "Saarth Shah",
    "role": "Co-Founder & CEO",
    "company": "Sixtyfour",
    "location": "San Francisco, CA",
    "bio": "Co-founder and CEO of Sixtyfour (YC X25), developing AI agents for people and company data enrichment. Previously at Whatnot, Deepgram, and Stanford.",
    "education": ["UC Berkeley Data Science"],
    "previous_companies": ["Whatnot", "Deepgram", "Stanford Snyder Lab", "SDSC", "Internalize"],
    "github": {
      "username": "SaarthShah",
      "public_repos": 48,
      "top_languages": ["Swift", "TypeScript", "Python"]
    },
    "linkedin_url": "https://www.linkedin.com/in/saarthshah",
    "website": "https://www.saarthshah.com/",
    "confidence": {
      "name": 1.0,
      "company": 1.0,
      "role": 1.0,
      "location": 0.9,
      "email": 0.0,
      "github": 1.0
    },
    "findings": [
      {"fact": "Sixtyfour is part of Y Combinator X25 cohort", "source": "ycombinator.com/companies/sixtyfour"},
      {"fact": "Previously sold a company called Internalize", "source": "ycombinator.com/companies/sixtyfour"},
      {"fact": "Sixtyfour hit $330K revenue with a 3 person team", "source": "getlatka.com/companies/sixtyfour.ai"}
    ],
    "sources": ["linkedin.com/in/saarthshah", "github.com/SaarthShah", "sixtyfour.ai", "...5 more"]
  },
  "latency_ms": 27400
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
    ├── playwright_tool.py   # Headless browser scraper
    └── proxy.py             # Rotating proxy manager
```

## Configuration

| Variable | Required | Description |
|----------|----------|-------------|
| `ANTHROPIC_API_KEY` | Yes | Claude API key |
| `GITHUB_TOKEN` | No | Raises GitHub rate limit to 5,000 req/hr |
| `SCRAPERAPI_KEY` | No | Rotating proxy network for Playwright |

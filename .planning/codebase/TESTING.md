# Testing Patterns

**Analysis Date:** 2026-02-23

## Overview

This project has no unit or integration test suite (no pytest, unittest, or test runner configuration). Testing is done via two custom runners:

1. **Eval suite** (`evals/`) — End-to-end correctness scoring against known people (ground truth JSON)
2. **Benchmark runner** (`benchmarks/`) — Latency and throughput measurement
3. **Manual CLI** (`test_agent.py`) — Ad-hoc single-request testing

There are NO `.test.py` or `.spec.py` files. There is no `pytest.ini`, `setup.cfg[tool:pytest]`, or `pyproject.toml`.

## Test Framework

**Runner:**
- No automated test framework. End-to-end testing only.
- Eval suite is a standalone asyncio runner: `evals/run_eval.py`
- Benchmark is a standalone asyncio runner: `benchmarks/benchmark.py`

**Assertion Library:**
- Custom scoring functions in `evals/evaluator.py` (no assertions — returns float 0.0–1.0 scores)

**Run Commands:**
```bash
# Manual ad-hoc test (single lead)
python test_agent.py "Guillermo Rauch" "Vercel"
python test_agent.py "Name" "Company" "Location" "sales|recruiting|job_search"

# Eval suite (all cases)
python -m evals.run_eval

# Eval suite (single case by ID)
python -m evals.run_eval --case dhh

# Eval suite (skip LLM judge)
python -m evals.run_eval --no-judge

# Benchmark (latency measurement)
python -m benchmarks.benchmark
python -m benchmarks.benchmark --name "Guillermo Rauch" --company "Vercel" --runs 3
```

## Eval Test Organization

**Ground truth location:**
- `evals/ground_truth.json` — All eval cases with expected values

**Case structure:**
```json
{
  "id": "guillermo-rauch",
  "name": "Guillermo Rauch",
  "company": "Vercel",
  "use_case": "sales",
  "expected": {
    "exact": { "github_username": "rauchg" },
    "contains": { "role": ["CEO", "Founder"], "company": ["Vercel"] },
    "non_empty": ["bio", "skills", "talking_points", "name"],
    "list_min_length": { "skills": 2, "talking_points": 3, "sources": 1 }
  }
}
```

**Current eval cases (10 total):**
- `guillermo-rauch` — Vercel CEO, sales use case
- `dhh` — 37signals CTO, sales use case
- `mitchell-hashimoto` — Ghostty founder, recruiting use case
- `satya-nadella` — Microsoft CEO, sales use case
- `sarah-drasner` — Google engineering leader, recruiting use case
- `kelsey-hightower` — Google DevRel, job_search use case
- `theo-browne` — Ping.gg founder, sales use case
- `fictional-person` — Non-existent person (graceful failure test)
- `julia-evans` — Technical writer/developer, recruiting use case
- `arvid-kahl` — Independent developer (no company), sales use case

**Eval results output:** `evals/results.json`

## Scoring System

**Scoring functions** (in `evals/evaluator.py`):

```python
# Exact string match (case-insensitive)
exact_match(actual: str, expected: str) -> float  # 1.0 or 0.0

# Substring match against candidates list
contains_any(actual: str, candidates: list[str]) -> float  # 1.0 or 0.0

# Truthy check for strings and lists
non_empty(value) -> float  # 1.0 or 0.0

# List length with partial credit
list_min_length(value: list, min_len: int) -> float  # 1.0, partial, or 0.0
```

**Case scoring entry point:**
```python
result = score_case(response: EnrichResponse, expected: dict) -> dict
# Returns: {"checks": {"exact:github_username": 1.0, ...}, "overall": 0.85}
```

**Pass threshold:** 80% overall score (`>= 0.8`)

**Failure modes handled:**
- `profile is None` → all checks score 0.0
- Exception during enrichment → case scored as crashed with `overall_score: 0.0`

## Test Types

**End-to-End Tests (Eval Suite):**
- Scope: Full pipeline (planner → tools → extractor → talking points)
- Real API calls: Serper, GitHub, Hunter.io, Anthropic Claude, Playwright
- Semantic cache disabled for cold eval runs: `os.environ["QDRANT_URL"] = ""`
- Checks: field correctness (exact match, contains, non-empty), list lengths
- Output: scores + `evals/results.json`

**Graceful Failure Test:**
- `fictional-person` case tests the pipeline with a non-existent person
- Expected: `name` field non-empty, no GitHub/email required, pipeline doesn't crash
- Validates that `ToolResult(success=False)` propagates correctly

**Benchmark Tests:**
- Scope: Full pipeline, measuring wall-clock latency per phase
- Cold run (run 1): semantic cache disabled
- Warm runs (run 2+): semantic cache enabled
- Phase timings extracted by parsing log output via regex in `benchmarks/benchmark.py`
- Output: `benchmarks/results.json`

## Mocking

**No mocking framework used.** All tests make real API calls to live services.

**Cache bypass pattern** (used in evals and benchmarks):
```python
# In evals/run_eval.py — disable Qdrant semantic cache before imports
os.environ["QDRANT_URL"] = ""

# In benchmarks/benchmark.py — monkey-patch cache enable check
import agent.semantic_cache as sc
sc._enabled = lambda: False
```

**What gets skipped, not mocked:**
- Tools return `ToolResult(success=False)` on API key absence (checked at runtime)
- Missing `SERPER_API_KEY` → `SerperSearchTool` returns failure result, not an error
- Missing `HUNTER_API_KEY` → `EmailPipeline` skips Layer 4 entirely

## Fixtures and Factories

**Test Data:**
- Ground truth defined in `evals/ground_truth.json` (static JSON file)
- No Python-based factories or fixtures
- `EnrichRequest` created inline in each test runner:
  ```python
  request = EnrichRequest(name=name, company=company, use_case=use_case)
  ```

**Location:**
- All test case data lives in `evals/ground_truth.json`
- Eval results written to `evals/results.json`
- Benchmark results written to `benchmarks/results.json`

## Coverage

**Requirements:** None enforced (no coverage tool configured)

**Gaps:**
- No unit tests for individual tool methods
- No unit tests for `_repair_truncated_json_object` or `_repair_truncated_json_array` in `agent/extractor.py`
- No tests for `InMemoryCache` TTL behavior in `agent/cache.py`
- No tests for `ToolRegistry` in `agent/tool_protocol.py`
- No tests for scoring functions in `evals/evaluator.py`
- No tests for `retry_with_backoff` in `agent/utils.py`
- No tests for email pipeline layers in `tools/email_pipeline.py`
- No mock-based tests for LLM fallback paths (planner fallback, extractor retry)

## Common Patterns

**Running a single enrichment (ad-hoc):**
```python
import asyncio
from agent.orchestrator import enrich_lead
from agent.schemas import EnrichRequest

async def main():
    request = EnrichRequest(name="Name", company="Company", use_case="sales")
    response = await enrich_lead(request)
    print(response.model_dump_json(indent=2))

asyncio.run(main())
```

**Log capture for testing/benchmarking:**
```python
import io, logging
stream = io.StringIO()
handler = logging.StreamHandler(stream)
handler.setLevel(logging.INFO)
logging.getLogger().addHandler(handler)

# ... run code ...

log_text = stream.getvalue()
logging.getLogger().removeHandler(handler)
```

**Exception containment in eval runner:**
```python
try:
    result = await run_single_case(case)
except Exception as e:
    logger.error(f"  UNHANDLED CRASH in {case['id']}: {e}")
    result = {
        "overall_score": 0.0,
        "success": False,
        "error": str(e),
        ...
    }
```

## Adding New Eval Cases

To add a new eval case, add an entry to `evals/ground_truth.json`:
```json
{
  "id": "unique-case-id",
  "name": "Person Name",
  "company": "Company",
  "use_case": "sales",
  "expected": {
    "exact": {},
    "contains": { "role": ["Expected Role"] },
    "non_empty": ["bio", "name"],
    "list_min_length": { "talking_points": 3, "sources": 1 }
  }
}
```

Run with: `python -m evals.run_eval --case unique-case-id`

## Adding Unit Tests (If Introduced)

If a unit test framework is added, the recommended approach based on codebase patterns:

**Framework to use:** `pytest` with `pytest-asyncio` (all testable code is async)

**What to mock:**
- `httpx.AsyncClient` for tool HTTP calls (avoid live API calls)
- `anthropic.AsyncAnthropic` for LLM calls
- `agent.cache.cache` (replace `InMemoryCache` instance)

**What NOT to mock:**
- `ToolResult` schema construction — test real Pydantic validation
- `InMemoryCache` — it's already a lightweight in-memory implementation
- Orchestrator pipeline logic — use eval suite for this

---

*Testing analysis: 2026-02-23*

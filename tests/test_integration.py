"""Integration tests for the enrichment pipeline.

Mocks all external I/O (LLM calls, HTTP requests, DNS) and tests
pipeline wiring, error handling, and response construction.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from agent.orchestrator import enrich_lead, _inflight, _inflight_results
from agent.schemas import (
    EnrichRequest,
    EnrichedProfile,
    GitHubProfile,
    PlannerDecision,
    ToolResult,
)


# ── Mock data ────────────────────────────────────────────────────────────

MOCK_DECISION = PlannerDecision(
    tools_to_run=["web_search", "github", "news", "community", "browser"],
    search_queries=["John Doe Acme Corp"],
    urls_to_scrape=["https://acmecorp.com"],
    reasoning="test plan",
)

MOCK_PROFILE = EnrichedProfile(
    name="John Doe",
    company="Acme Corp",
    role="VP Engineering",
    bio="Experienced engineer building distributed systems",
    skills=["Python", "Go", "Distributed Systems"],
    sources=["https://github.com/johndoe", "https://acmecorp.com"],
    github=GitHubProfile(username="johndoe", url="https://github.com/johndoe"),
)

MOCK_TALKING_POINTS = [
    "They recently starred 3 Rust repos — mention your Rust migration plans",
    "Company raised Series B last month — likely scaling infra",
    "Active Go contributor — ask about their microservices architecture",
]


def _tool_result(name, success=True, data="mock data"):
    return ToolResult(
        tool_name=name,
        success=success,
        raw_data=data if success else "",
        error="" if success else "mock error",
        latency_ms=100,
    )


def _mock_tool(name, success=True):
    tool = MagicMock()
    tool.name = name
    tool.run = AsyncMock(return_value=_tool_result(name, success=success))
    return tool


# ── Fixtures ──────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def cleanup_dedup():
    """Clean up orchestrator dedup state between tests."""
    _inflight.clear()
    _inflight_results.clear()
    yield
    _inflight.clear()
    _inflight_results.clear()


@pytest.fixture
def mock_tools():
    return {
        "github": _mock_tool("github"),
        "news": _mock_tool("news"),
        "community": _mock_tool("community"),
        "web_search": _mock_tool("web_search"),
        "browser": _mock_tool("browser"),
    }


@pytest.fixture
def pipeline(mock_tools):
    """Patch all external I/O for integration tests."""
    mocks = {
        "plan": AsyncMock(return_value=MOCK_DECISION),
        "extract": AsyncMock(return_value=MOCK_PROFILE),
        "talking_points": AsyncMock(return_value=MOCK_TALKING_POINTS),
        "narrative": AsyncMock(return_value=""),
        "email_run": AsyncMock(
            return_value=_tool_result("email_pipeline", success=False)
        ),
        "cache_lookup": AsyncMock(return_value=None),
        "cache_store": AsyncMock(),
    }

    with (
        patch("agent.graph.plan", mocks["plan"]),
        patch("agent.graph.extract", mocks["extract"]),
        patch("agent.graph.generate_talking_points", mocks["talking_points"]),
        patch("agent.graph.generate_narrative", mocks["narrative"]),
        patch("agent.graph.email_pipeline.run", mocks["email_run"]),
        patch(
            "agent.graph.registry.get",
            side_effect=lambda name: mock_tools.get(name),
        ),
        patch("agent.semantic_cache.lookup", mocks["cache_lookup"]),
        patch("agent.semantic_cache.store", mocks["cache_store"]),
    ):
        yield mocks


# ── Tests ─────────────────────────────────────────────────────────────────


class TestHappyPath:
    async def test_returns_successful_response(self, pipeline, mock_tools):
        req = EnrichRequest(name="John Doe", company="Acme Corp")
        resp = await enrich_lead(req)

        assert resp.success is True
        assert resp.profile is not None
        assert resp.profile.name == "John Doe"
        assert resp.profile.role == "VP Engineering"
        assert len(resp.talking_points) == 3

    async def test_calls_planner(self, pipeline, mock_tools):
        req = EnrichRequest(name="John Doe", company="Acme Corp")
        await enrich_lead(req)

        pipeline["plan"].assert_called_once()
        args = pipeline["plan"].call_args
        assert args[0][0] == "John Doe"  # name
        assert args[0][1] == "Acme Corp"  # company

    async def test_calls_extractor(self, pipeline, mock_tools):
        req = EnrichRequest(name="John Doe", company="Acme Corp")
        await enrich_lead(req)

        pipeline["extract"].assert_called_once()

    async def test_sources_searched_populated(self, pipeline, mock_tools):
        req = EnrichRequest(name="John Doe", company="Acme Corp")
        resp = await enrich_lead(req)

        # Should include tools that ran
        assert len(resp.sources_searched) > 0


class TestPlannerFailure:
    async def test_fallback_plan_used(self, pipeline, mock_tools):
        pipeline["plan"].side_effect = Exception("LLM timeout")

        req = EnrichRequest(name="John Doe", company="Acme Corp")
        resp = await enrich_lead(req)

        # Pipeline should still complete using fallback plan
        assert resp.profile is not None
        assert resp.profile.name == "John Doe"


class TestToolFailures:
    async def test_partial_tool_failure(self, pipeline, mock_tools):
        # GitHub fails, others succeed
        mock_tools["github"].run = AsyncMock(
            return_value=_tool_result("github", success=False)
        )

        req = EnrichRequest(name="Jane Doe", company="Beta Inc")
        resp = await enrich_lead(req)

        # Pipeline should still succeed with remaining tools
        assert resp.profile is not None

    async def test_all_tools_fail_graceful(self, pipeline, mock_tools):
        # Make every tool fail
        for name, tool in mock_tools.items():
            tool.run = AsyncMock(
                return_value=_tool_result(name, success=False)
            )
        pipeline["email_run"].return_value = _tool_result(
            "email_pipeline", success=False
        )

        req = EnrichRequest(name="Nobody Special", company="Nowhere Corp")
        resp = await enrich_lead(req)

        # Pipeline should not crash
        assert resp is not None
        assert isinstance(resp.success, bool)
        assert resp.trace_id  # trace ID always present


class TestEmailPipeline:
    async def test_email_result_in_sources(self, pipeline, mock_tools):
        pipeline["email_run"].return_value = _tool_result(
            "email_pipeline",
            success=True,
            data="Email: john@acme.com\nConfidence: 0.95\nSource: github_public",
        )

        req = EnrichRequest(name="John Doe", company="Acme Corp")
        resp = await enrich_lead(req)

        assert "email_pipeline" in resp.sources_searched


class TestSparseData:
    async def test_skips_talking_points_with_few_results(self, pipeline, mock_tools):
        # Make all tools fail → < 2 successful results
        for name, tool in mock_tools.items():
            tool.run = AsyncMock(
                return_value=_tool_result(name, success=False)
            )
        pipeline["email_run"].return_value = _tool_result(
            "email_pipeline", success=False
        )

        req = EnrichRequest(name="Sparse Person", company="Unknown Co")
        resp = await enrich_lead(req)

        # generate_talking_points should NOT have been called
        pipeline["talking_points"].assert_not_called()
        assert resp.talking_points == []


class TestResponseFormat:
    async def test_all_fields_present(self, pipeline, mock_tools):
        req = EnrichRequest(name="John Doe", company="Acme Corp")
        resp = await enrich_lead(req)

        assert resp.trace_id  # non-empty
        assert resp.latency_ms > 0
        assert isinstance(resp.sources_searched, list)
        assert isinstance(resp.errors, list)
        assert isinstance(resp.talking_points, list)
        assert isinstance(resp.narrative, str)

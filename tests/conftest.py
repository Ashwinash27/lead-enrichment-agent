import pytest

from agent.schemas import ToolResult


@pytest.fixture
def make_tool_result():
    """Factory returning a ToolResult with sensible defaults."""

    def _factory(**kwargs):
        defaults = {
            "tool_name": "test",
            "raw_data": "test data",
            "success": True,
            "latency_ms": 100.0,
        }
        defaults.update(kwargs)
        return ToolResult(**defaults)

    return _factory


@pytest.fixture
def github_tool_result(make_tool_result):
    return make_tool_result(
        tool_name="github",
        raw_data="GitHub Profile: johndoe\nEmail: john@example.com\nBio: Software engineer",
    )


@pytest.fixture
def failed_tool_result(make_tool_result):
    return make_tool_result(
        tool_name="test",
        success=False,
        raw_data="",
        error="Something went wrong",
    )

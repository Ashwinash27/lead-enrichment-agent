"""Tests for agent.extractor pure functions."""

import json

import pytest

from agent.extractor import (
    _build_combined,
    _has_github_data,
    _needs_retry,
    _repair_truncated_json_array,
    _repair_truncated_json_object,
)
from agent.schemas import EnrichedProfile, GitHubProfile, ToolResult


# ── _repair_truncated_json_object ────────────────────────────────────────


class TestRepairTruncatedJsonObject:
    def test_valid_json_single_field(self):
        raw = '{"name": "John"}'
        result = _repair_truncated_json_object(raw)
        assert result == {"name": "John"}

    def test_valid_json_returns_subset(self):
        # Repair function trims at last comma, so multi-field valid JSON
        # returns a valid subset — this is expected for a repair function
        raw = '{"name": "John", "age": 30}'
        result = _repair_truncated_json_object(raw)
        assert result is not None
        assert "name" in result

    def test_truncated_mid_value(self):
        raw = '{"name": "John", "role": "Eng'
        result = _repair_truncated_json_object(raw)
        assert result is not None
        assert result["name"] == "John"

    def test_does_not_start_with_brace(self):
        assert _repair_truncated_json_object('["a", "b"]') is None

    def test_empty_string(self):
        assert _repair_truncated_json_object("") is None

    def test_single_brace(self):
        assert _repair_truncated_json_object("{") is None

    def test_corrupted(self):
        assert _repair_truncated_json_object("{invalid json garbage") is None

    def test_array_input(self):
        assert _repair_truncated_json_object("[1, 2, 3]") is None


# ── _repair_truncated_json_array ─────────────────────────────────────────


class TestRepairTruncatedJsonArray:
    def test_valid_array(self):
        raw = '["one", "two", "three"]'
        result = _repair_truncated_json_array(raw)
        assert result == ["one", "two", "three"]

    def test_truncated_mid_string_with_two_plus_complete(self):
        raw = '["point one", "point two", "point thr'
        result = _repair_truncated_json_array(raw)
        assert result is not None
        assert len(result) >= 2
        assert "point one" in result
        assert "point two" in result

    def test_truncated_with_only_one_complete(self):
        raw = '["only one", "trunc'
        result = _repair_truncated_json_array(raw)
        assert result is None

    def test_does_not_start_with_bracket(self):
        assert _repair_truncated_json_array('{"key": "val"}') is None

    def test_empty_string(self):
        assert _repair_truncated_json_array("") is None

    def test_single_bracket(self):
        assert _repair_truncated_json_array("[") is None

    def test_minimum_two_items_required(self):
        raw = '["one"]'
        assert _repair_truncated_json_array(raw) is None


# ── _build_combined ──────────────────────────────────────────────────────


class TestBuildCombined:
    def test_empty_list(self):
        assert _build_combined([]) == ""

    def test_filters_failed(self):
        results = [
            ToolResult(tool_name="fail", success=False, raw_data="bad data"),
            ToolResult(tool_name="ok", success=True, raw_data="good data"),
        ]
        combined = _build_combined(results)
        assert "good data" in combined
        assert "bad data" not in combined

    def test_filters_empty_raw_data(self):
        results = [
            ToolResult(tool_name="empty", success=True, raw_data=""),
        ]
        assert _build_combined(results) == ""

    def test_per_tool_truncation(self):
        long_data = "a" * 5000
        results = [
            ToolResult(tool_name="test", success=True, raw_data=long_data),
        ]
        combined = _build_combined(results, per_tool_max=100)
        # Section header "=== test ===" plus truncated data
        assert len(combined) < 200

    def test_total_truncation(self):
        results = [
            ToolResult(tool_name=f"t{i}", success=True, raw_data="x" * 500)
            for i in range(10)
        ]
        combined = _build_combined(results, max_context=1000)
        assert len(combined) <= 1000


# ── _has_github_data ─────────────────────────────────────────────────────


class TestHasGithubData:
    def test_true_with_github_result(self):
        results = [
            ToolResult(tool_name="github", success=True, raw_data="profile data"),
        ]
        assert _has_github_data(results) is True

    def test_false_with_non_github(self):
        results = [
            ToolResult(tool_name="web_search", success=True, raw_data="search data"),
        ]
        assert _has_github_data(results) is False


# ── _needs_retry ─────────────────────────────────────────────────────────


def _make_successful_results(count, include_github=False):
    """Helper to make N successful ToolResults."""
    results = []
    for i in range(count):
        name = "github" if (i == 0 and include_github) else f"tool_{i}"
        results.append(
            ToolResult(tool_name=name, success=True, raw_data=f"data {i}")
        )
    return results


class TestNeedsRetry:
    def test_fewer_than_two_successful_returns_false(self):
        profile = EnrichedProfile(name="Test")
        results = _make_successful_results(1)
        assert _needs_retry(profile, results) is False

    def test_full_profile_returns_false(self):
        profile = EnrichedProfile(
            name="John",
            role="Engineer",
            bio="A developer",
            skills=["Python"],
            sources=["https://github.com/john"],
        )
        results = _make_successful_results(3)
        assert _needs_retry(profile, results) is False

    def test_three_plus_empty_critical_returns_true(self):
        # role, bio, skills, sources all empty = 4 empty critical > 2
        profile = EnrichedProfile(name="John")
        results = _make_successful_results(3)
        assert _needs_retry(profile, results) is True

    def test_exactly_two_empty_returns_false(self):
        # role and bio empty (2), skills and sources filled
        profile = EnrichedProfile(
            name="John",
            skills=["Python"],
            sources=["https://github.com/john"],
        )
        results = _make_successful_results(3)
        assert _needs_retry(profile, results) is False

    def test_github_conditional_field(self):
        # With github data in results but no github in profile,
        # github counts as an additional empty critical field
        profile = EnrichedProfile(
            name="John",
            role="",
            bio="",
            skills=["Python"],
            sources=["src"],
        )
        # Without github data → empty_critical = 2 (role, bio) → False
        results_no_gh = _make_successful_results(3, include_github=False)
        assert _needs_retry(profile, results_no_gh) is False

        # With github data → empty_critical = 3 (role, bio, github) → True
        results_with_gh = _make_successful_results(3, include_github=True)
        assert _needs_retry(profile, results_with_gh) is True

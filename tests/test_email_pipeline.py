"""Tests for tools.email_pipeline pure functions and layer methods."""

import pytest

from agent.schemas import ToolResult
from tools.email_pipeline import EmailPipeline, _is_person_email, _JUNK_PREFIXES, _name_parts


# ── _name_parts ─────────────────────────────────────────────────────────


class TestNameParts:
    def test_two_part_name(self):
        assert _name_parts("John Doe") == ("john", "doe")

    def test_single_word(self):
        assert _name_parts("Madonna") == ("madonna", "")

    def test_three_part_name(self):
        first, last = _name_parts("Mary Jane Watson")
        assert first == "mary"
        assert last == "watson"

    def test_extra_whitespace(self):
        assert _name_parts("  John   Doe  ") == ("john", "doe")


# ── _is_person_email ────────────────────────────────────────────────────


class TestIsPersonEmail:
    def test_rejects_noreply(self):
        assert _is_person_email("noreply@example.com", "john", "doe") is False

    def test_rejects_support(self):
        assert _is_person_email("support@example.com", "john", "doe") is False

    def test_rejects_info(self):
        assert _is_person_email("info@example.com", "john", "doe") is False

    def test_rejects_sales(self):
        assert _is_person_email("sales@example.com", "john", "doe") is False

    def test_first_name_match(self):
        assert _is_person_email("john.smith@example.com", "john", "doe") is True

    def test_last_name_match(self):
        assert _is_person_email("jdoe@example.com", "john", "doe") is True

    def test_no_match(self):
        assert _is_person_email("alice@example.com", "john", "doe") is False

    def test_empty_names(self):
        # Both first and last empty — can't match any name part
        assert _is_person_email("anything@example.com", "", "") is False

    def test_case_insensitive(self):
        assert _is_person_email("John.Doe@example.com", "john", "doe") is True

    def test_junk_beats_name_match(self):
        # "sales" is both a junk prefix AND matches first="sales"
        # Junk check runs first (line 41-42), so this returns False
        assert _is_person_email("sales@co.com", "sales", "person") is False

    def test_all_junk_prefixes_rejected(self):
        for prefix in _JUNK_PREFIXES:
            assert _is_person_email(f"{prefix}@example.com", "john", "doe") is False

    def test_first_name_only(self):
        # last is empty, but first matches
        assert _is_person_email("john@example.com", "john", "") is True


# ── EmailPipeline._layer_github ─────────────────────────────────────────


class TestLayerGithub:
    def test_finds_person_email_in_github_result(self):
        pipeline = EmailPipeline()
        results = [
            ToolResult(
                tool_name="github",
                success=True,
                raw_data="GitHub Profile: johndoe\nEmail: john@example.com\nFollowers: 50",
            )
        ]
        email, confidence, source = pipeline._layer_github(results, "john", "doe")
        assert email == "john@example.com"
        assert confidence == 0.95
        assert source == "github_public"

    def test_skips_failed_results(self):
        pipeline = EmailPipeline()
        results = [
            ToolResult(
                tool_name="github",
                success=False,
                raw_data="Email: john@example.com",
            )
        ]
        email, confidence, source = pipeline._layer_github(results, "john", "doe")
        assert email == ""
        assert confidence == 0.0


# ── EmailPipeline._layer_regex ───────────────────────────────────────────


class TestLayerRegex:
    def test_finds_email_across_non_github_results(self):
        pipeline = EmailPipeline()
        results = [
            ToolResult(
                tool_name="web_search",
                success=True,
                raw_data="Contact Jane at jane.doe@company.com for inquiries.",
            )
        ]
        email, confidence, source = pipeline._layer_regex(results, "jane", "doe")
        assert email == "jane.doe@company.com"
        assert confidence == 0.6
        assert source == "regex_scan:web_search"

    def test_returns_empty_on_no_match(self):
        pipeline = EmailPipeline()
        results = [
            ToolResult(
                tool_name="web_search",
                success=True,
                raw_data="No contact information available for this person.",
            )
        ]
        email, confidence, source = pipeline._layer_regex(results, "john", "doe")
        assert email == ""
        assert confidence == 0.0

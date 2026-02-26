"""Tests for agent.schemas validation and coercion."""

import pytest
from pydantic import ValidationError

from agent.schemas import EnrichRequest, EnrichedProfile, ToolResult


# ── EnrichRequest ────────────────────────────────────────────────────────


class TestEnrichRequest:
    def test_minimal_valid(self):
        req = EnrichRequest(name="John Doe")
        assert req.name == "John Doe"
        assert req.company == ""
        assert req.use_case == "sales"
        assert req.output_format == "structured"

    def test_invalid_use_case(self):
        with pytest.raises(ValidationError):
            EnrichRequest(name="John", use_case="invalid")

    def test_valid_use_cases(self):
        for uc in ("sales", "recruiting", "job_search"):
            req = EnrichRequest(name="John", use_case=uc)
            assert req.use_case == uc

    def test_invalid_output_format(self):
        with pytest.raises(ValidationError):
            EnrichRequest(name="John", output_format="xml")

    def test_name_max_length(self):
        with pytest.raises(ValidationError):
            EnrichRequest(name="x" * 201)


# ── _coerce_str_list ────────────────────────────────────────────────────


class TestCoerceStrList:
    def test_strings_unchanged(self):
        profile = EnrichedProfile(interests=["coding", "music"])
        assert profile.interests == ["coding", "music"]

    def test_dict_coerced_to_str(self):
        profile = EnrichedProfile(interests=[{"key": "value"}])
        assert len(profile.interests) == 1
        assert isinstance(profile.interests[0], str)

    def test_int_coerced_to_str(self):
        profile = EnrichedProfile(interests=[42])
        assert profile.interests == ["42"]

    def test_empty_list(self):
        profile = EnrichedProfile(interests=[])
        assert profile.interests == []

    def test_mixed_types(self):
        profile = EnrichedProfile(interests=["normal", 123, {"k": "v"}])
        assert len(profile.interests) == 3
        assert profile.interests[0] == "normal"
        assert isinstance(profile.interests[1], str)
        assert isinstance(profile.interests[2], str)


# ── Field constraints ────────────────────────────────────────────────────


class TestFieldConstraints:
    def test_disambiguation_confidence_above_one_rejected(self):
        with pytest.raises(ValidationError):
            EnrichedProfile(disambiguation_confidence=1.5)

    def test_candidates_found_negative_rejected(self):
        with pytest.raises(ValidationError):
            EnrichedProfile(candidates_found=-1)

"""Tests for tools.github_tool scoring and matching functions."""

import pytest

from tools.github_tool import GitHubTool


# ── _username_matches_name ───────────────────────────────────────────────


class TestUsernameMatchesName:
    def test_firstlast(self):
        assert GitHubTool._username_matches_name("johndoe", "John Doe") is True

    def test_hyphenated(self):
        assert GitHubTool._username_matches_name("john-doe", "John Doe") is True

    def test_initials(self):
        assert GitHubTool._username_matches_name("jdoe", "John Doe") is True

    def test_first_only(self):
        assert GitHubTool._username_matches_name("john", "John Doe") is True

    def test_last_only(self):
        assert GitHubTool._username_matches_name("doe", "John Doe") is True

    def test_no_match(self):
        assert GitHubTool._username_matches_name("smith", "John Doe") is False

    def test_single_word_name_returns_false(self):
        # Single-word names have len(parts) < 2 → always False
        assert GitHubTool._username_matches_name("madonna", "Madonna") is False


# ── _name_matches ────────────────────────────────────────────────────────


class TestNameMatches:
    def test_exact_match(self):
        profile = {"name": "John Doe"}
        assert GitHubTool._name_matches(profile, "John Doe") is True

    def test_empty_profile_name_is_neutral(self):
        profile = {"name": ""}
        assert GitHubTool._name_matches(profile, "John Doe") is True

    def test_first_name_match(self):
        profile = {"name": "John Smith"}
        assert GitHubTool._name_matches(profile, "John Doe") is True

    def test_different_first_name(self):
        profile = {"name": "Jane Doe"}
        assert GitHubTool._name_matches(profile, "John Doe") is False

    def test_single_word_overlap(self):
        # Single-word profile name: uses set intersection
        profile = {"name": "John"}
        assert GitHubTool._name_matches(profile, "John Doe") is True


# ── _company_matches ─────────────────────────────────────────────────────


class TestCompanyMatches:
    def test_exact(self):
        profile = {"company": "Acme Corp"}
        assert GitHubTool._company_matches(profile, "Acme Corp") is True

    def test_empty_is_neutral(self):
        profile = {"company": ""}
        assert GitHubTool._company_matches(profile, "Acme Corp") is True

    def test_expected_in_profile(self):
        # expected "Vercel" found in company field "Vercel Inc."
        profile = {"company": "Vercel Inc."}
        assert GitHubTool._company_matches(profile, "Vercel") is True

    def test_profile_in_expected(self):
        profile = {"company": "Vercel"}
        assert GitHubTool._company_matches(profile, "Vercel Inc.") is True

    def test_bio_fallback(self):
        profile = {"company": "Other Corp", "bio": "Working at Acme Corp"}
        assert GitHubTool._company_matches(profile, "Acme Corp") is True

    def test_mismatch(self):
        profile = {"company": "Other Corp", "bio": "No mention"}
        assert GitHubTool._company_matches(profile, "Acme Corp") is False


# ── _score_candidate ─────────────────────────────────────────────────────


class TestScoreCandidate:
    def test_perfect_match(self):
        profile = {"name": "John Doe", "company": "Acme Corp", "bio": "Engineer"}
        score = GitHubTool._score_candidate(profile, "johndoe", "John Doe", "Acme Corp")
        # name(+3) + company(+2) + username(+1) + bio(+0.5) = 6.5
        assert score == 6.5

    def test_name_mismatch_hard_reject(self):
        profile = {"name": "Jane Smith", "company": "Acme Corp", "bio": ""}
        score = GitHubTool._score_candidate(profile, "janesmith", "John Doe", "Acme Corp")
        assert score == -5.0

    def test_no_name_neutral(self):
        # Empty profile name: no name bonus or penalty
        profile = {"name": "", "company": "Acme Corp", "bio": ""}
        score = GitHubTool._score_candidate(profile, "johndoe", "John Doe", "Acme Corp")
        # company(+2) + username(+1) = 3.0
        assert score == 3.0

    def test_company_mismatch_penalty(self):
        profile = {"name": "John Doe", "company": "Other Corp", "bio": ""}
        score = GitHubTool._score_candidate(profile, "johndoe", "John Doe", "Other Corp Company")
        # name(+3), company: "other corp" in "other corp company" → match(+2), username(+1) = 6.0
        # But if mismatch: name(+3) + company(-1) + username(+1) = 3.0
        # Let's check: expected="Other Corp Company", profile_company="Other Corp"
        # "other corp" in "other corp company" → True → match +2
        assert score == 6.0

    def test_company_mismatch_actual_penalty(self):
        profile = {"name": "John Doe", "company": "Totally Different", "bio": ""}
        score = GitHubTool._score_candidate(profile, "johndoe", "John Doe", "Acme Corp")
        # name(+3) + company(-1) + username(+1) = 3.0
        assert score == 3.0

    def test_username_only(self):
        # No name, no company, username matches, no bio
        profile = {"name": "", "company": "", "bio": ""}
        score = GitHubTool._score_candidate(profile, "johndoe", "John Doe", "")
        # username(+1) = 1.0
        assert score == 1.0

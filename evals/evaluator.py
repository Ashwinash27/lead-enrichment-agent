"""Scoring functions for lead enrichment eval cases."""

from __future__ import annotations

from agent.schemas import EnrichResponse


def exact_match(actual: str, expected: str) -> float:
    """1.0 if actual == expected (case-insensitive), else 0.0."""
    if not expected:
        return 1.0
    return 1.0 if actual.strip().lower() == expected.strip().lower() else 0.0


def contains_any(actual: str, candidates: list[str]) -> float:
    """1.0 if actual contains any of the candidate substrings."""
    if not candidates:
        return 1.0
    actual_lower = actual.strip().lower()
    for c in candidates:
        if c.lower() in actual_lower:
            return 1.0
    return 0.0


def non_empty(value) -> float:
    """1.0 if value is truthy and non-empty."""
    if isinstance(value, list):
        return 1.0 if len(value) > 0 else 0.0
    if isinstance(value, str):
        return 1.0 if value.strip() else 0.0
    return 1.0 if value else 0.0


def list_min_length(value: list, min_len: int) -> float:
    """1.0 if list has at least min_len items, else partial credit."""
    if not isinstance(value, list):
        return 0.0
    if len(value) >= min_len:
        return 1.0
    if min_len == 0:
        return 1.0
    return len(value) / min_len


def score_case(response: EnrichResponse, expected: dict) -> dict:
    """Score a single eval case against expected values.

    Returns dict with per-check scores and overall average.
    """
    checks: dict[str, float] = {}
    profile = response.profile

    if profile is None:
        # Total failure — everything scores 0
        for field in expected.get("non_empty", []):
            checks[f"non_empty:{field}"] = 0.0
        for field in expected.get("exact", {}):
            checks[f"exact:{field}"] = 0.0
        for field in expected.get("contains", {}):
            checks[f"contains:{field}"] = 0.0
        for field, min_len in expected.get("list_min_length", {}).items():
            checks[f"list_min:{field}"] = 0.0
        if not checks:
            checks["profile_exists"] = 0.0
        overall = 0.0
        return {"checks": checks, "overall": overall}

    # Exact matches
    for field, exp_val in expected.get("exact", {}).items():
        if field == "github_username":
            actual = profile.github.username if profile.github else ""
        else:
            actual = getattr(profile, field, "")
        checks[f"exact:{field}"] = exact_match(actual, exp_val)

    # Contains matches
    for field, candidates in expected.get("contains", {}).items():
        actual = getattr(profile, field, "")
        checks[f"contains:{field}"] = contains_any(actual, candidates)

    # Non-empty checks
    for field in expected.get("non_empty", []):
        if field == "talking_points":
            value = response.talking_points
        elif field == "narrative":
            value = response.narrative
        else:
            value = getattr(profile, field, "")
        checks[f"non_empty:{field}"] = non_empty(value)

    # List minimum length checks
    for field, min_len in expected.get("list_min_length", {}).items():
        if field == "talking_points":
            value = response.talking_points
        else:
            value = getattr(profile, field, [])
        checks[f"list_min:{field}"] = list_min_length(value, min_len)

    total = sum(checks.values())
    count = len(checks) if checks else 1
    overall = round(total / count, 4)

    return {"checks": checks, "overall": overall}

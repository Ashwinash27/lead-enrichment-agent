from __future__ import annotations

import json
import logging
import re
import time as _time
from datetime import date

from anthropic import AsyncAnthropic

from agent.observe import log_generation
from agent.schemas import EnrichedProfile, ToolResult
from agent.utils import llm_create
from config import ANTHROPIC_API_KEY, ANTHROPIC_MODEL, EXTRACTOR_MODEL

logger = logging.getLogger(__name__)

client = AsyncAnthropic(api_key=ANTHROPIC_API_KEY)

MAX_CONTEXT = 20000
PER_TOOL_MAX = 3000
INITIAL_MAX_TOKENS = 4000
RETRY_MAX_TOKENS = 6000

_system_prompt_cache: dict[str, str] = {}


def _build_system_prompt() -> str:
    today = date.today().isoformat()
    cached = _system_prompt_cache.get(today)
    if cached is not None:
        return cached
    # Clear stale date keys
    for key in list(_system_prompt_cache):
        if key != today:
            del _system_prompt_cache[key]
    prompt = f"""You are a precision data extraction specialist. Given raw research data about a person, extract structured information into a JSON profile.

Rules:
- NEVER fabricate information. Only extract what is explicitly stated in the data.
- If a GitHub profile is found, fill the github object. If not, set github to null.
- Use empty string "" for unknown text fields, empty list [] for unknown list fields.
- For education, extract university/school names and degrees if mentioned.
- For skills, extract programming languages, frameworks, and tools mentioned.
- Collect all source URLs into the sources list.

- CONFIDENCE RUBRIC — use these specific rules for confidence scores:
  - Email: GitHub public = 0.95, SMTP-verified = 0.7, regex-found = 0.6, pattern-guessed = 0.4, Hunter verified = 0.85, none = 0.0
  - Name/Company/Role: multi-source confirmed = 0.9, single source = 0.7, inferred = 0.4, none = 0.0
  - GitHub: API data present = 0.95, link only = 0.7, none = 0.0
  - Location: multi-source confirmed = 0.9, single source = 0.7, inferred = 0.4, none = 0.0
  - Bio/LinkedIn: direct source = 0.9, inferred = 0.5, none = 0.0
  - Twitter handle: verified profile found = 0.9, mentioned in text = 0.6, none = 0.0
  - Recent news: articles found with clear match = 0.9, tangential mentions = 0.5, none = 0.0

- DISAMBIGUATION: Check if raw data contains multiple distinct people with the same or similar names. If so:
  1. Pick the best-matching candidate (company match, location match, cross-references between sources)
  2. Extract ONLY for that candidate — do not mix data from different people
  3. Set disambiguation_confidence (0.0-1.0), candidates_found (total people detected), disambiguation_signals (list of signals used to pick the right one, e.g. "company match", "GitHub bio matches role")
  If only one candidate found: disambiguation_confidence=1.0, candidates_found=1, disambiguation_signals=[]

- Today's date is {today}.
- FRESHNESS: Only for "email" and "role" fields (the two that actually go stale):
  - If from a live API (GitHub) → last_confirmed = today's date, source_type = "live_api", freshness_score = 1.0
  - If from a scraped page with a visible date → use that date, source_type = "live_scrape"
  - If from a search snippet → last_confirmed = "unknown", source_type = "search_snippet", freshness_score = 0.7
  - If no date indicators → freshness_score = 0.5
  - Return exactly 2 freshness entries (email + role), no more.

- CONFLICTS: If different sources provide contradictory information for the same field, list each conflict in the conflicts array.
  Only flag genuine contradictions, not missing data. If no conflicts, return an empty array.

- NEW FIELDS:
  - recent_news: Extract 3-5 recent headlines or news items about the person or their company. Include date and source if available.
  - twitter_handle: Extract their Twitter/X handle (without @) if found.
  - twitter_bio: Their Twitter/X bio text if found.
  - community_highlights: 3-5 notable community contributions (HN comments, Reddit posts, conference talks, blog posts).
  - media_appearances: Podcasts, interviews, conference talks, articles they authored.
  - interests: Topics, technologies, or hobbies they show interest in (from stars, comments, blog topics, etc.).

- For findings: extract 5-15 specific, factual claims as bullet points. Each must have the URL of the source it came from. Only include facts that are directly stated in the data.

Return ONLY a JSON object matching this exact schema (no markdown fences):
{{
  "name": "string",
  "company": "string",
  "role": "string",
  "location": "string",
  "email": "string",
  "bio": "string",
  "education": ["string"],
  "previous_companies": ["string"],
  "skills": ["string"],
  "github": {{
    "username": "string",
    "url": "string",
    "bio": "string",
    "location": "string",
    "public_repos": 0,
    "followers": 0,
    "top_languages": ["string"],
    "notable_repos": ["string"],
    "recent_stars": ["string"],
    "recent_activity_summary": "string",
    "activity_level": "string"
  }},
  "linkedin_url": "string",
  "linkedin_summary": "string",
  "website": "string",
  "notable_achievements": ["string"],
  "sources": ["string"],
  "confidence": {{
    "name": 0.0,
    "company": 0.0,
    "role": 0.0,
    "location": 0.0,
    "email": 0.0,
    "bio": 0.0,
    "github": 0.0,
    "linkedin_url": 0.0,
    "twitter_handle": 0.0,
    "recent_news": 0.0
  }},
  "findings": [
    {{"fact": "string", "source": "string"}}
  ],
  "disambiguation_confidence": 1.0,
  "candidates_found": 1,
  "disambiguation_signals": [],
  "freshness": [
    {{"field": "string", "last_confirmed": "string", "source_type": "string", "freshness_score": 1.0}}
  ],
  "conflicts": [],
  "recent_news": ["string"],
  "twitter_handle": "string",
  "twitter_bio": "string",
  "community_highlights": ["string"],
  "media_appearances": ["string"],
  "interests": ["string"]
}}

If no GitHub data is found, set "github": null."""
    _system_prompt_cache[today] = prompt
    return prompt


def _repair_truncated_json_object(raw: str) -> dict | None:
    """Try to repair a truncated JSON object by closing open brackets/braces.

    Works by progressively trimming from the end and trying to close the JSON.
    """
    raw = raw.strip()
    if not raw.startswith("{"):
        return None

    # Try trimming from the end to find the last valid cut point
    for end in range(len(raw), max(len(raw) - 2000, 0), -1):
        chunk = raw[:end]
        # Count open/close brackets
        open_braces = chunk.count("{") - chunk.count("}")
        open_brackets = chunk.count("[") - chunk.count("]")

        # Try to close with the right number of brackets
        if open_braces >= 0 and open_brackets >= 0:
            # Trim to last complete value (last comma, colon+value, or bracket)
            # Find last clean break point
            for trim_char in [",", "}", "]"]:
                idx = chunk.rfind(trim_char)
                if idx > 0:
                    attempt = chunk[:idx + 1] if trim_char in ("}", "]") else chunk[:idx]
                    # Recount after trim
                    ob = attempt.count("{") - attempt.count("}")
                    obrk = attempt.count("[") - attempt.count("]")
                    if ob >= 0 and obrk >= 0:
                        suffix = "]" * obrk + "}" * ob
                        try:
                            result = json.loads(attempt + suffix)
                            if isinstance(result, dict):
                                return result
                        except json.JSONDecodeError:
                            continue

    return None


RETRY_PER_TOOL_MAX = 4500
RETRY_MAX_CONTEXT = 30000


def _build_combined(
    tool_results: list[ToolResult],
    per_tool_max: int = PER_TOOL_MAX,
    max_context: int = MAX_CONTEXT,
) -> str:
    """Build truncated combined text from tool results."""
    sections: list[str] = []
    for tr in tool_results:
        if tr.success and tr.raw_data:
            data = tr.raw_data[:per_tool_max] if len(tr.raw_data) > per_tool_max else tr.raw_data
            sections.append(f"=== {tr.tool_name} ===\n{data}")

    combined = "\n\n".join(sections)
    if len(combined) > max_context:
        logger.warning(
            f"Extractor context truncated: {len(combined)} -> {max_context} chars"
        )
        combined = combined[:max_context]
    return combined


def _has_github_data(tool_results: list[ToolResult]) -> bool:
    """Check if any github tool result returned data."""
    return any(tr.tool_name == "github" and tr.success and tr.raw_data for tr in tool_results)


def _needs_retry(profile: EnrichedProfile, tool_results: list[ToolResult]) -> bool:
    """Approach 2: Check if critical fields are empty despite having tool data."""
    successful = sum(1 for tr in tool_results if tr.success and tr.raw_data)
    if successful < 2:
        return False

    empty_critical = 0
    if not profile.role:
        empty_critical += 1
    if not profile.bio:
        empty_critical += 1
    if not profile.skills:
        empty_critical += 1
    if _has_github_data(tool_results) and (profile.github is None or not profile.github.username):
        empty_critical += 1
    if not profile.sources:
        empty_critical += 1

    return empty_critical > 2


async def _extract_once(
    name: str, company: str, combined: str, trace_id: str,
    location: str, max_tokens: int,
) -> EnrichedProfile:
    """Single extraction attempt with the given max_tokens."""
    target = f"{name} at {company}"
    if location:
        target += f" in {location}"
    user_msg = (
        f"Extract a structured profile for: {target}\n\n"
        f"Raw research data:\n{combined}"
    )

    system_prompt = _build_system_prompt()

    logger.info(
        f"[{trace_id}] Extractor context: system={len(system_prompt)} chars, "
        f"user={len(user_msg)} chars, total={len(system_prompt) + len(user_msg)} chars, "
        f"max_tokens={max_tokens}"
    )

    t0_llm = _time.time()
    response = await llm_create(
        client,
        model=EXTRACTOR_MODEL,
        max_tokens=max_tokens,
        system=system_prompt,
        messages=[{"role": "user", "content": user_msg}],
    )
    log_generation(
        trace_id, "extractor", EXTRACTOR_MODEL,
        response.usage.input_tokens, response.usage.output_tokens,
        (_time.time() - t0_llm) * 1000,
    )

    raw = response.content[0].text.strip()
    raw = re.sub(r"^```json\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning(f"[{trace_id}] Extractor JSON truncated, attempting repair")
        data = _repair_truncated_json_object(raw)
        if data is None:
            logger.error(f"[{trace_id}] Extractor JSON repair failed")
            return EnrichedProfile(name=name, company=company)

    try:
        profile = EnrichedProfile.model_validate(data)
    except Exception as e:
        logger.warning(f"[{trace_id}] Extractor validation error: {e}")
        return EnrichedProfile(name=name, company=company)
    return profile


async def extract(
    name: str,
    company: str,
    tool_results: list[ToolResult],
    trace_id: str,
    location: str = "",
) -> EnrichedProfile:
    try:
        combined = _build_combined(tool_results)

        if not combined.strip():
            logger.warning(f"[{trace_id}] No tool data to extract from")
            return EnrichedProfile(name=name, company=company)

        # First attempt
        profile = await _extract_once(
            name, company, combined, trace_id, location, INITIAL_MAX_TOKENS,
        )
        logger.info(f"[{trace_id}] Extraction complete")

        # Approach 2: Retry with higher max_tokens and relaxed context if critical fields empty
        if _needs_retry(profile, tool_results):
            combined = _build_combined(
                tool_results,
                per_tool_max=RETRY_PER_TOOL_MAX,
                max_context=RETRY_MAX_CONTEXT,
            )
            logger.warning(
                f"[{trace_id}] Extraction retry — critical fields empty "
                f"(role={bool(profile.role)}, bio={bool(profile.bio)}, "
                f"skills={len(profile.skills)}, github={profile.github is not None and bool(profile.github.username)}, "
                f"sources={len(profile.sources)}). Retrying with max_tokens={RETRY_MAX_TOKENS}, "
                f"context={len(combined)} chars"
            )
            profile = await _extract_once(
                name, company, combined, trace_id, location, RETRY_MAX_TOKENS,
            )
            logger.info(f"[{trace_id}] Extraction retry complete")

        return profile

    except Exception as e:
        logger.error(f"[{trace_id}] Extractor failed: {e}")
        return EnrichedProfile(name=name, company=company)


async def generate_narrative(profile: EnrichedProfile, trace_id: str) -> str:
    try:
        t0_llm = _time.time()
        response = await llm_create(
            client,
            model=ANTHROPIC_MODEL,
            max_tokens=500,
            system=(
                "Write a 2-3 paragraph analyst briefing about this person. "
                "Professional third-person style. 150-250 words. "
                "Cover their role, background, technical profile, and notable signals. "
                "Do not fabricate — only use information from the provided profile data."
            ),
            messages=[{"role": "user", "content": profile.model_dump_json()}],
        )
        log_generation(
            trace_id, "narrative", ANTHROPIC_MODEL,
            response.usage.input_tokens, response.usage.output_tokens,
            (_time.time() - t0_llm) * 1000,
        )
        return response.content[0].text.strip()
    except Exception as e:
        logger.error(f"[{trace_id}] Narrative generation failed: {e}")
        return ""


_TALKING_POINTS_SYSTEM = {
    "sales": (
        "You are given raw research data about a person. Generate 5-7 actionable, specific "
        "conversation starters for a SALES outreach to this person. "
        "Focus on: recent company news, funding signals, tech stack alignment, pain points "
        "you can infer from their role/company stage, and shared interests. "
        "Base each on concrete signals from the raw data. "
        "Format as a JSON array of strings. "
        "Examples: "
        "'They starred 3 Rust repos this month — mention your Rust migration plans', "
        "'Company raised Series A — they are likely scaling infra'. "
        "Do NOT generate generic advice. Return ONLY a JSON array of strings, no markdown fences."
    ),
    "recruiting": (
        "You are given raw research data about a person. Generate 5-7 actionable, specific "
        "conversation starters for a RECRUITING outreach to this person. "
        "Focus on: their open-source work, technical interests, career trajectory, "
        "community engagement, and what kind of role/culture would appeal to them. "
        "Base each on concrete signals from the raw data. "
        "Format as a JSON array of strings. "
        "Do NOT generate generic advice. Return ONLY a JSON array of strings, no markdown fences."
    ),
    "job_search": (
        "You are given raw research data about a person. Generate exactly 7 actionable, specific "
        "conversation starters for someone doing a JOB SEARCH reaching out to this person "
        "(who may be a hiring manager or influential contact at their company). "
        "Draw from these 5 categories:\n"
        "1. SHARED TECHNICAL INTERESTS — Reference their GitHub stars, repos, languages, or "
        "frameworks and connect them to your own experience. E.g. 'You starred 4 Rust repos "
        "last month — I just shipped a Rust CLI tool for X, happy to share learnings.'\n"
        "2. COMPANY-SPECIFIC HOOKS — Cite recent news, funding rounds, product launches, or "
        "hiring signals. E.g. 'Saw the Series B announcement — scaling the data team must be "
        "a priority, I led a similar buildout at Y.'\n"
        "3. CAREER JOURNEY — Note their moves, tenure, or trajectory and relate it to your "
        "own path. E.g. 'Your move from IC to engineering lead at Z mirrors the transition "
        "I am making — would love to hear what surprised you.'\n"
        "4. SMART QUESTIONS — Ask about their recent work, tech choices, or team challenges. "
        "At least 2 of the 7 points MUST be questions. E.g. 'What drove the migration from "
        "REST to GraphQL that showed up in your recent commits?'\n"
        "5. PERSONAL CONNECTION ANGLES — Reference their HN comments, Reddit posts, blog "
        "articles, conference talks, or community activity. E.g. 'Your HN comment on build-vs-buy "
        "resonated — I wrote a post expanding on that tradeoff.'\n\n"
        "Rules:\n"
        "- Every point MUST reference specific data from the enrichment (names, numbers, dates, URLs).\n"
        "- Zero generic filler like 'I admire your work' or 'Great company.'\n"
        "- If a category has no data, skip it entirely and add more points from categories that do.\n"
        "- At least 2 of 7 must be questions (category 4).\n"
        "Return ONLY a JSON array of 7 strings, no markdown fences."
    ),
}


def _repair_truncated_json_array(raw: str) -> list[str] | None:
    """Try to salvage a truncated JSON array of strings.

    If the LLM output was cut off mid-string (e.g. max_tokens hit), find the
    last complete string entry and close the array.
    """
    raw = raw.strip()
    if not raw.startswith("["):
        return None
    # Find last complete string: look for last '",\n' or '"\n]'
    # Strategy: try closing at each '"' from the end until valid JSON
    last = raw.rfind('"')
    while last > 0:
        attempt = raw[:last + 1] + "]"
        try:
            result = json.loads(attempt)
            if isinstance(result, list) and len(result) >= 2:
                return [str(p) for p in result]
        except json.JSONDecodeError:
            pass
        last = raw.rfind('"', 0, last)
    return None


async def generate_talking_points(
    name: str,
    company: str,
    tool_results: list[ToolResult],
    trace_id: str,
    use_case: str = "sales",
) -> list[str]:
    try:
        # Build condensed raw data summary for talking points
        sections: list[str] = []
        for tr in tool_results:
            if tr.success and tr.raw_data:
                data = tr.raw_data[:3000] if len(tr.raw_data) > 3000 else tr.raw_data
                sections.append(f"=== {tr.tool_name} ===\n{data}")
        combined = "\n\n".join(sections)
        if len(combined) > 15000:
            combined = combined[:15000]

        user_msg = f"Person: {name} at {company}\n\nRaw research data:\n{combined}"

        system = _TALKING_POINTS_SYSTEM.get(use_case, _TALKING_POINTS_SYSTEM["sales"])
        t0_llm = _time.time()
        response = await llm_create(
            client,
            model=ANTHROPIC_MODEL,
            max_tokens=700,
            system=system,
            messages=[{"role": "user", "content": user_msg}],
        )
        log_generation(
            trace_id, "talking_points", ANTHROPIC_MODEL,
            response.usage.input_tokens, response.usage.output_tokens,
            (_time.time() - t0_llm) * 1000,
        )
        raw = response.content[0].text.strip()
        raw = re.sub(r"^```json\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)

        try:
            points = json.loads(raw)
        except json.JSONDecodeError:
            # LLM likely hit max_tokens mid-string — try to repair
            logger.warning(f"[{trace_id}] Talking points JSON truncated, attempting repair")
            points = _repair_truncated_json_array(raw)
            if points is None:
                logger.error(f"[{trace_id}] Talking points repair failed")
                return []
            return points

        if isinstance(points, list):
            return [str(p) for p in points]
        return []
    except Exception as e:
        logger.error(f"[{trace_id}] Talking points generation failed: {e}")
        return []

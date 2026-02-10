from __future__ import annotations

import json
import logging
import re

from anthropic import AsyncAnthropic

from agent.schemas import EnrichedProfile, ToolResult
from config import ANTHROPIC_API_KEY, ANTHROPIC_MODEL

logger = logging.getLogger(__name__)

client = AsyncAnthropic(api_key=ANTHROPIC_API_KEY)

MAX_CONTEXT = 30000

SYSTEM_PROMPT = """You are a precision data extraction specialist. Given raw research data about a person, extract structured information into a JSON profile.

Rules:
- NEVER fabricate information. Only extract what is explicitly stated in the data.
- If a GitHub profile is found, fill the github object. If not, set github to null.
- Use empty string "" for unknown text fields, empty list [] for unknown list fields.
- For education, extract university/school names and degrees if mentioned.
- For skills, extract programming languages, frameworks, and tools mentioned.
- Collect all source URLs into the sources list.
- For each key field, rate your confidence from 0.0 to 1.0 based on:
  - How many independent sources confirm it
  - How explicit the evidence is (directly stated vs inferred)
  - 0.9+ = multiple sources explicitly confirm
  - 0.6-0.8 = one source explicitly states, or multiple sources imply
  - 0.3-0.5 = inferred or only mentioned indirectly
  - 0.0 = no evidence found (field is empty/unknown)
- For findings: extract 5-15 specific, factual claims as bullet points. Each must have the URL of the source it came from. Only include facts that are directly stated in the data.

Return ONLY a JSON object matching this exact schema (no markdown fences):
{
  "name": "string",
  "company": "string",
  "role": "string",
  "location": "string",
  "email": "string",
  "bio": "string",
  "education": ["string"],
  "previous_companies": ["string"],
  "skills": ["string"],
  "github": {
    "username": "string",
    "url": "string",
    "bio": "string",
    "location": "string",
    "public_repos": 0,
    "followers": 0,
    "top_languages": ["string"],
    "notable_repos": ["string"]
  },
  "linkedin_url": "string",
  "linkedin_summary": "string",
  "website": "string",
  "notable_achievements": ["string"],
  "sources": ["string"],
  "confidence": {
    "name": 0.0,
    "company": 0.0,
    "role": 0.0,
    "location": 0.0,
    "email": 0.0,
    "bio": 0.0,
    "github": 0.0,
    "linkedin_url": 0.0
  },
  "findings": [
    {"fact": "string", "source": "string"}
  ]
}

If no GitHub data is found, set "github": null."""


async def extract(
    name: str,
    company: str,
    tool_results: list[ToolResult],
    trace_id: str,
) -> EnrichedProfile:
    try:
        sections: list[str] = []
        for tr in tool_results:
            if tr.success and tr.raw_data:
                data = tr.raw_data[:4000] if len(tr.raw_data) > 4000 else tr.raw_data
                sections.append(f"=== {tr.tool_name} ===\n{data}")

        combined = "\n\n".join(sections)
        if len(combined) > MAX_CONTEXT:
            combined = combined[:MAX_CONTEXT]

        if not combined.strip():
            logger.warning(f"[{trace_id}] No tool data to extract from")
            return EnrichedProfile(name=name, company=company)

        user_msg = (
            f"Extract a structured profile for: {name} at {company}\n\n"
            f"Raw research data:\n{combined}"
        )

        logger.info(
            f"[{trace_id}] Extractor context: system={len(SYSTEM_PROMPT)} chars, "
            f"user={len(user_msg)} chars, total={len(SYSTEM_PROMPT) + len(user_msg)} chars"
        )

        response = await client.messages.create(
            model=ANTHROPIC_MODEL,
            max_tokens=2000,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_msg}],
        )

        raw = response.content[0].text.strip()
        raw = re.sub(r"^```json\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)

        data = json.loads(raw)
        profile = EnrichedProfile.model_validate(data)
        logger.info(f"[{trace_id}] Extraction complete")
        return profile

    except Exception as e:
        logger.error(f"[{trace_id}] Extractor failed: {e}")
        return EnrichedProfile(name=name, company=company)

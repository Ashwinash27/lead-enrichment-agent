from __future__ import annotations

import json
import logging
import re

from anthropic import AsyncAnthropic

from agent.schemas import PlannerDecision
from agent.tool_protocol import registry
from config import ANTHROPIC_API_KEY, ANTHROPIC_MODEL

logger = logging.getLogger(__name__)

client = AsyncAnthropic(api_key=ANTHROPIC_API_KEY)

SYSTEM_PROMPT = """You are a research planning agent. Given a person's name and company, decide which tools to use and what queries to run to gather comprehensive information about them.

Available tools:
{tool_descriptions}

Rules:
- ALWAYS include "web_search" — it's the most reliable source.
- ALWAYS include "news" — recent news is the highest-value conversation signal.
- Include "github" if the person is likely technical (engineer, developer, CTO, founder of a tech company).
- Include "community" if the person is likely technical or active in public forums (engineers, founders, DevRel).
- When a company name is provided, ALWAYS include "browser" and add the company website to urls_to_scrape. Include both .com and .ai variants (e.g., "https://companyname.com" and "https://companyname.ai") since many tech startups use non-traditional TLDs.
- If the person likely has a personal website or blog, add those URLs too.
- If you can guess their Twitter/X handle, add "https://x.com/{{handle}}" to urls_to_scrape.
- Provide 3-5 search queries for maximum coverage:
  1. General: "{{name}} {{company}}"
  2. Social: "site:x.com {{name}} {{company}}" or "site:linkedin.com {{name}} {{company}}"
  3. Content: "{{name}} podcast interview" or "{{name}} blog post" or "{{name}} conference talk"
  4. Company signals: "{{company}} funding" or "site:producthunt.com {{company}}"
  5. Role-specific: "{{company}} careers" or "{{name}} {{role}}" if role is known

Respond with ONLY a JSON object (no markdown fences, no explanation):
{{
  "tools_to_run": ["web_search", "github", "browser", "news", "community"],
  "search_queries": ["query1", "query2", "query3"],
  "urls_to_scrape": ["https://company.com"],
  "reasoning": "brief explanation"
}}"""


async def plan(name: str, company: str, trace_id: str, location: str = "") -> PlannerDecision:
    try:
        tool_desc = registry.tool_descriptions()
        prompt = SYSTEM_PROMPT.format(tool_descriptions=tool_desc)

        user_msg = f"Research this person:\nName: {name}\nCompany: {company}"
        if location:
            user_msg += f"\nLocation: {location}"

        response = await client.messages.create(
            model=ANTHROPIC_MODEL,
            max_tokens=500,
            system=prompt,
            messages=[{"role": "user", "content": user_msg}],
        )

        raw = response.content[0].text.strip()
        # Strip markdown json fences if present
        raw = re.sub(r"^```json\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)

        data = json.loads(raw)

        # Validate tool names against registry
        valid_tools = set(registry.tool_names)
        data["tools_to_run"] = [
            t for t in data.get("tools_to_run", []) if t in valid_tools
        ]

        decision = PlannerDecision.model_validate(data)
        logger.info(f"[{trace_id}] Planner decision: {decision.tools_to_run}")
        return decision

    except Exception as e:
        logger.error(f"[{trace_id}] Planner failed: {e}, using fallback")
        return _fallback_plan(name, company, location)


def _fallback_plan(name: str, company: str, location: str = "") -> PlannerDecision:
    base = f"{name} {company}".strip()
    if location:
        base = f"{name} {company} {location}".strip()

    queries = [
        base,
        f"site:linkedin.com {base}",
        f"{name} interview OR podcast OR talk",
    ]
    if company:
        queries.append(f"{company} funding OR launch OR announcement")

    tools = ["web_search", "github", "news", "community"]
    urls: list[str] = []
    if company:
        slug = company.lower().replace(" ", "")
        tools.append("browser")
        urls.append(f"https://{slug}.com")
        urls.append(f"https://{slug}.ai")

    return PlannerDecision(
        tools_to_run=tools,
        search_queries=queries,
        urls_to_scrape=urls,
        reasoning="Fallback plan due to planner error",
    )

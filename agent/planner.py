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
- Include "github" if the person is likely technical (engineer, developer, CTO, founder of a tech company).
- When a company name is provided, ALWAYS include "browser" and add the company website to urls_to_scrape. ALWAYS include both .com and .ai variants (e.g., "https://companyname.com" and "https://companyname.ai") since many tech startups use non-traditional TLDs. Dead domains are detected and skipped instantly.
- If the person likely has a personal website or blog, add those URLs too.
- Include "hunter" when you need to find the person's email address. It works best with a full name and company.
- Provide 2-4 search queries that would find relevant information.

Respond with ONLY a JSON object (no markdown fences, no explanation):
{{
  "tools_to_run": ["web_search", "github", "browser"],
  "search_queries": ["query1", "query2"],
  "urls_to_scrape": ["https://company.com"],
  "reasoning": "brief explanation"
}}"""


async def plan(name: str, company: str, trace_id: str) -> PlannerDecision:
    try:
        tool_desc = registry.tool_descriptions()
        prompt = SYSTEM_PROMPT.format(tool_descriptions=tool_desc)

        user_msg = f"Research this person:\nName: {name}\nCompany: {company}"

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
        return _fallback_plan(name, company)


def _fallback_plan(name: str, company: str) -> PlannerDecision:
    queries = [f"{name} {company}".strip()]
    if company:
        queries.append(f"{name} {company} LinkedIn")
    queries.append(f"{name} software engineer")

    tools = ["web_search", "github"]
    urls: list[str] = []
    if company:
        slug = company.lower().replace(" ", "")
        tools.append("browser")
        tools.append("hunter")
        urls.append(f"https://{slug}.com")

    return PlannerDecision(
        tools_to_run=tools,
        search_queries=queries,
        urls_to_scrape=urls,
        reasoning="Fallback plan due to planner error",
    )

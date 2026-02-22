from __future__ import annotations

import asyncio
import logging
import re
import time

import httpx

from agent.cache import cache
from agent.schemas import ToolResult
from config import SERPER_API_KEY, HTTP_TIMEOUT

logger = logging.getLogger(__name__)

HN_API = "https://hn.algolia.com/api/v1/search"
SERPER_SEARCH_URL = "https://google.serper.dev/search"
MAX_CHARS = 4000


class CommunityActivityTool:
    name = "community"
    description = (
        "Search Hacker News and Reddit for a person's community activity — comments, "
        "posts, discussions. Reveals interests, opinions, and engagement style."
    )

    async def run(self, name: str, company: str, **kwargs) -> ToolResult:
        t0 = time.time()

        cache_key = f"community:{name}:{company}"
        cached = await cache.get(cache_key)
        if cached is not None:
            return ToolResult(
                tool_name=self.name,
                raw_data=cached,
                success=True,
                latency_ms=(time.time() - t0) * 1000,
            )

        hn_task = self._search_hn(name, company)
        reddit_task = self._search_reddit(name, company)

        hn_text, reddit_text = await asyncio.gather(hn_task, reddit_task)

        sections: list[str] = []
        if hn_text:
            sections.append(f"=== Hacker News ===\n{hn_text}")
        if reddit_text:
            sections.append(f"=== Reddit ===\n{reddit_text}")

        combined = "\n\n".join(sections)
        if len(combined) > MAX_CHARS:
            combined = combined[:MAX_CHARS]

        if not combined.strip():
            return ToolResult(
                tool_name=self.name,
                success=False,
                error="No community activity found",
                latency_ms=(time.time() - t0) * 1000,
            )

        await cache.set(cache_key, combined, ttl=300)

        return ToolResult(
            tool_name=self.name,
            raw_data=combined,
            success=True,
            latency_ms=(time.time() - t0) * 1000,
        )

    async def _search_hn(self, name: str, company: str) -> str:
        """Search HN Algolia for comments and stories by this person."""
        try:
            query = f"{name} {company}".strip()

            async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
                comments_resp, stories_resp = await asyncio.gather(
                    client.get(HN_API, params={
                        "query": query, "tags": "comment", "hitsPerPage": 10,
                    }),
                    client.get(HN_API, params={
                        "query": query, "tags": "story", "hitsPerPage": 5,
                    }),
                )

            lines: list[str] = []

            # Stories
            if stories_resp.status_code == 200:
                stories = stories_resp.json().get("hits", [])
                if stories:
                    lines.append("Stories:")
                    for s in stories[:5]:
                        title = s.get("title", "")
                        author = s.get("author", "")
                        points = s.get("points", 0)
                        date = s.get("created_at", "")[:10]
                        lines.append(f"  - [{date}] {title} (by {author}, {points} pts)")

            # Comments
            if comments_resp.status_code == 200:
                comments = comments_resp.json().get("hits", [])
                if comments:
                    lines.append("\nComments:")
                    for c in comments[:10]:
                        author = c.get("author", "")
                        story = c.get("story_title", "")
                        date = c.get("created_at", "")[:10]
                        text = c.get("comment_text", "")
                        # Strip HTML tags
                        text = re.sub(r"<[^>]+>", " ", text)
                        text = re.sub(r"\s+", " ", text).strip()
                        if len(text) > 200:
                            text = text[:200] + "..."
                        lines.append(f"  - [{date}] on '{story}' by {author}:")
                        lines.append(f"    {text}")

            return "\n".join(lines)

        except Exception as e:
            logger.error(f"HN search error: {e}")
            return ""

    async def _search_reddit(self, name: str, company: str) -> str:
        """Search Reddit via Serper for mentions of person/company."""
        if not SERPER_API_KEY:
            return ""

        try:
            query = f"site:reddit.com {name} {company}".strip()
            headers = {
                "X-API-KEY": SERPER_API_KEY,
                "Content-Type": "application/json",
            }

            async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
                resp = await client.post(
                    SERPER_SEARCH_URL,
                    json={"q": query, "num": 5},
                    headers=headers,
                )
                resp.raise_for_status()
                data = resp.json()

            lines: list[str] = []
            organic = data.get("organic", [])
            for r in organic[:5]:
                title = r.get("title", "")
                snippet = r.get("snippet", "")
                link = r.get("link", "")
                lines.append(f"  - {title}")
                if snippet:
                    lines.append(f"    {snippet}")
                if link:
                    lines.append(f"    URL: {link}")

            return "\n".join(lines)

        except Exception as e:
            logger.error(f"Reddit search error: {e}")
            return ""

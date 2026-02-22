from __future__ import annotations

import asyncio
import logging
import time

import httpx

from agent.cache import cache
from agent.schemas import ToolResult
from config import SERPER_API_KEY, HTTP_TIMEOUT

logger = logging.getLogger(__name__)

SERPER_SEARCH_URL = "https://google.serper.dev/search"
MAX_QUERIES = 5
MAX_CHARS = 15000


class SerperSearchTool:
    name = "web_search"
    description = (
        "Search the web using Google via Serper. Returns titles, URLs, and snippets. "
        "Use for general information about a person and their company."
    )

    async def run(self, name: str, company: str, **kwargs) -> ToolResult:
        t0 = time.time()

        if not SERPER_API_KEY:
            return ToolResult(
                tool_name=self.name,
                success=False,
                error="SERPER_API_KEY not configured",
                latency_ms=(time.time() - t0) * 1000,
            )

        queries: list[str] = kwargs.get("search_queries", [])
        if not queries:
            queries = [f"{name} {company}".strip()]
        queries = queries[:MAX_QUERIES]

        all_results: list[str] = []
        all_urls: list[str] = []
        errors: list[str] = []

        query_results = await asyncio.gather(
            *(self._run_single_query(q) for q in queries)
        )
        for text, urls, error in query_results:
            if text is not None:
                all_results.append(text)
            if urls:
                all_urls.extend(urls)
            if error is not None:
                errors.append(error)

        combined = "\n\n".join(all_results)
        if len(combined) > MAX_CHARS:
            combined = combined[:MAX_CHARS]

        if not combined and errors:
            return ToolResult(
                tool_name=self.name,
                success=False,
                error="; ".join(errors),
                latency_ms=(time.time() - t0) * 1000,
            )

        return ToolResult(
            tool_name=self.name,
            raw_data=combined,
            urls=all_urls,
            success=True,
            latency_ms=(time.time() - t0) * 1000,
        )

    async def _run_single_query(
        self, query: str
    ) -> tuple[str | None, list[str], str | None]:
        """Returns (result_text, urls, error_string)."""
        cache_key = f"search:{query}"
        cached = await cache.get(cache_key)
        if cached is not None:
            urls = self._extract_urls(cached)
            return cached, urls, None
        try:
            text, urls = await self._search(query)
            await cache.set(cache_key, text, ttl=300)
            return text, urls, None
        except Exception as e:
            logger.error(f"Serper error for '{query}': {e}")
            return None, [], f"Query '{query}': {e}"

    async def _search(self, query: str) -> tuple[str, list[str]]:
        """Execute a single Serper search and return formatted text + urls."""
        headers = {
            "X-API-KEY": SERPER_API_KEY,
            "Content-Type": "application/json",
        }
        payload = {"q": query, "num": 10}

        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
            resp = await client.post(
                SERPER_SEARCH_URL, json=payload, headers=headers
            )
            resp.raise_for_status()
            data = resp.json()

        return self._format_results(query, data)

    def _format_results(self, query: str, data: dict) -> tuple[str, list[str]]:
        """Format Serper response into text summary + url list."""
        lines: list[str] = [f"Search results for: {query}", ""]
        urls: list[str] = []

        # Knowledge graph (rich entity data Google surfaces)
        kg = data.get("knowledgeGraph")
        if kg:
            lines.append(f"Knowledge Graph: {kg.get('title', '')}")
            if kg.get("type"):
                lines.append(f"   Type: {kg['type']}")
            if kg.get("description"):
                lines.append(f"   {kg['description']}")
            attrs = kg.get("attributes", {})
            for k, v in list(attrs.items())[:6]:
                lines.append(f"   {k}: {v}")
            if kg.get("website"):
                lines.append(f"   Website: {kg['website']}")
            lines.append("")

        # Answer box (featured snippet)
        ab = data.get("answerBox")
        if ab:
            answer = ab.get("answer") or ab.get("snippet", "")
            if answer:
                lines.append(f"Featured Answer: {answer}")
                lines.append("")

        # Organic results
        organic = data.get("organic", [])
        for i, r in enumerate(organic, 1):
            link = r.get("link", "")
            lines.append(f"{i}. {r.get('title', '')}")
            lines.append(f"   URL: {link}")
            snippet = r.get("snippet", "")
            if snippet:
                lines.append(f"   {snippet}")
            date = r.get("date")
            if date:
                lines.append(f"   Date: {date}")
            lines.append("")
            if link:
                urls.append(link)

        return "\n".join(lines), urls

    def _extract_urls(self, cached_text: str) -> list[str]:
        """Pull URLs from cached formatted text."""
        urls = []
        for line in cached_text.split("\n"):
            if line.strip().startswith("URL: "):
                urls.append(line.strip()[5:])
        return urls

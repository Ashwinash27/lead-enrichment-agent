from __future__ import annotations

import asyncio
import logging
import time

try:
    from ddgs import DDGS
except ImportError:
    from duckduckgo_search import DDGS

from agent.cache import cache
from agent.schemas import ToolResult

logger = logging.getLogger(__name__)

MAX_QUERIES = 5
MAX_CHARS = 15000


class WebSearchTool:
    name = "web_search"
    description = (
        "Search the web using DuckDuckGo. Returns titles, URLs, and snippets. "
        "Use for general information about a person and their company."
    )

    async def run(self, name: str, company: str, **kwargs) -> ToolResult:
        t0 = time.time()
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
        for text, error in query_results:
            if text is not None:
                all_results.append(text)
            if error is not None:
                errors.append(error)

        combined = "\n\n".join(all_results)
        if len(combined) > MAX_CHARS:
            combined = combined[:MAX_CHARS]

        for line in combined.split("\n"):
            if line.startswith("URL: "):
                all_urls.append(line[5:].strip())

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

    async def _run_single_query(self, query: str) -> tuple[str | None, str | None]:
        """Returns (result_text, error_string). One will be None."""
        cache_key = f"search:{query}"
        cached = await cache.get(cache_key)
        if cached is not None:
            return cached, None
        try:
            text = await self._search_with_retry(query)
            await cache.set(cache_key, text, ttl=300)
            return text, None
        except Exception as e:
            logger.error(f"Search error for '{query}': {e}")
            return None, f"Query '{query}': {e}"

    async def _search_with_retry(self, query: str) -> str:
        loop = asyncio.get_event_loop()
        for attempt in range(3):
            try:
                results = await loop.run_in_executor(
                    None, self._sync_search, query
                )
                return self._format_results(query, results)
            except Exception as e:
                if attempt == 2:
                    raise
                wait = 2**attempt
                logger.warning(f"Search retry {attempt+1} for '{query}': {e}")
                await asyncio.sleep(wait)
        return ""

    def _sync_search(self, query: str) -> list[dict]:
        with DDGS() as ddgs:
            return list(ddgs.text(query, max_results=8))

    def _format_results(self, query: str, results: list[dict]) -> str:
        lines = [f"Search results for: {query}", ""]
        for i, r in enumerate(results, 1):
            lines.append(f"{i}. {r.get('title', '')}")
            lines.append(f"   URL: {r.get('href', '')}")
            lines.append(f"   {r.get('body', '')}")
            lines.append("")
        return "\n".join(lines)

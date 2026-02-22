from __future__ import annotations

import asyncio
import logging
import time

import httpx

from agent.cache import cache
from agent.schemas import ToolResult
from config import SERPER_API_KEY, HTTP_TIMEOUT

logger = logging.getLogger(__name__)

SERPER_NEWS_URL = "https://google.serper.dev/news"
MAX_CHARS = 4000


class SerperNewsTool:
    name = "news"
    description = (
        "Search recent news articles about a person or company using Google News via Serper. "
        "Returns headlines, dates, sources, and snippets. High-value for conversation starters."
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

        queries = [f"{name} {company}".strip()]
        if company:
            queries.append(company)
        if name and company:
            queries.append(name)
        queries = queries[:3]

        all_results: list[str] = []
        all_urls: list[str] = []

        results = await asyncio.gather(
            *(self._search_news(q) for q in queries)
        )
        for text, urls in results:
            if text:
                all_results.append(text)
            all_urls.extend(urls)

        combined = "\n\n".join(all_results)
        if len(combined) > MAX_CHARS:
            combined = combined[:MAX_CHARS]

        if not combined.strip():
            return ToolResult(
                tool_name=self.name,
                success=False,
                error="No news results found",
                latency_ms=(time.time() - t0) * 1000,
            )

        return ToolResult(
            tool_name=self.name,
            raw_data=combined,
            urls=all_urls,
            success=True,
            latency_ms=(time.time() - t0) * 1000,
        )

    async def _search_news(self, query: str) -> tuple[str, list[str]]:
        cache_key = f"news:{query}"
        cached = await cache.get(cache_key)
        if cached is not None:
            return cached, []

        try:
            headers = {
                "X-API-KEY": SERPER_API_KEY,
                "Content-Type": "application/json",
            }
            async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
                resp = await client.post(
                    SERPER_NEWS_URL,
                    json={"q": query, "num": 10},
                    headers=headers,
                )
                resp.raise_for_status()
                data = resp.json()

            text, urls = self._format_results(query, data)
            await cache.set(cache_key, text, ttl=300)
            return text, urls

        except Exception as e:
            logger.error(f"News search error for '{query}': {e}")
            return "", []

    def _format_results(self, query: str, data: dict) -> tuple[str, list[str]]:
        lines: list[str] = [f"News results for: {query}", ""]
        urls: list[str] = []

        articles = data.get("news", [])
        for i, article in enumerate(articles, 1):
            title = article.get("title", "")
            link = article.get("link", "")
            snippet = article.get("snippet", "")
            date = article.get("date", "")
            source = article.get("source", "")

            lines.append(f"{i}. {title}")
            if source or date:
                lines.append(f"   {source} — {date}")
            if snippet:
                lines.append(f"   {snippet}")
            if link:
                lines.append(f"   URL: {link}")
                urls.append(link)
            lines.append("")

        return "\n".join(lines), urls

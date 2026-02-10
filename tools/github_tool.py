from __future__ import annotations

import asyncio
import logging
import time

import httpx

from agent.cache import cache
from agent.schemas import ToolResult
from config import GITHUB_TOKEN, HTTP_TIMEOUT

logger = logging.getLogger(__name__)

GITHUB_API = "https://api.github.com"


class GitHubTool:
    name = "github"
    description = (
        "Search GitHub for a user profile, repositories, and languages. "
        "Best for technical people with public GitHub accounts."
    )

    def __init__(self) -> None:
        self._use_auth = bool(GITHUB_TOKEN)

    def _headers(self) -> dict[str, str]:
        h = {"Accept": "application/vnd.github+json"}
        if self._use_auth:
            h["Authorization"] = f"Bearer {GITHUB_TOKEN}"
        return h

    async def run(self, name: str, company: str, **kwargs) -> ToolResult:
        t0 = time.time()
        cache_key = f"github:{name}:{company}"
        cached = await cache.get(cache_key)
        if cached is not None:
            return ToolResult(
                tool_name=self.name,
                raw_data=cached,
                success=True,
                latency_ms=(time.time() - t0) * 1000,
            )

        try:
            async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
                login = await self._search_user(client, name, company)
                if not login:
                    return ToolResult(
                        tool_name=self.name,
                        success=False,
                        error="No GitHub user found",
                        latency_ms=(time.time() - t0) * 1000,
                    )

                profile = await self._get_profile(client, login)
                repos = await self._get_repos(client, login)
                summary = self._format_summary(profile, repos)

                await cache.set(cache_key, summary, ttl=600)
                return ToolResult(
                    tool_name=self.name,
                    raw_data=summary,
                    urls=[profile.get("html_url", "")],
                    success=True,
                    latency_ms=(time.time() - t0) * 1000,
                )
        except Exception as e:
            logger.error(f"GitHubTool error: {e}")
            return ToolResult(
                tool_name=self.name,
                success=False,
                error=str(e),
                latency_ms=(time.time() - t0) * 1000,
            )

    async def _request(self, client: httpx.AsyncClient, url: str, **kwargs) -> httpx.Response:
        resp = await client.get(url, headers=self._headers(), **kwargs)
        if resp.status_code == 401 and self._use_auth:
            logger.warning("GitHub token rejected, falling back to unauthenticated")
            self._use_auth = False
            resp = await client.get(url, headers=self._headers(), **kwargs)
        return resp

    async def _search_user(
        self, client: httpx.AsyncClient, name: str, company: str
    ) -> str | None:
        # Try progressively broader queries: "name company" → "name"
        queries = [f"{name} {company}".strip()]
        if company:
            queries.append(name)

        for query in queries:
            login = await self._try_search(client, query)
            if login:
                return login
        return None

    async def _try_search(
        self, client: httpx.AsyncClient, query: str
    ) -> str | None:
        for attempt in range(3):
            try:
                resp = await self._request(
                    client,
                    f"{GITHUB_API}/search/users",
                    params={"q": query, "per_page": 3},
                )
                resp.raise_for_status()
                items = resp.json().get("items", [])
                return items[0]["login"] if items else None
            except Exception as e:
                if attempt == 2:
                    raise
                wait = 2**attempt
                logger.warning(f"GitHub search retry {attempt+1}: {e}")
                await asyncio.sleep(wait)
        return None

    async def _get_profile(
        self, client: httpx.AsyncClient, login: str
    ) -> dict:
        resp = await self._request(client, f"{GITHUB_API}/users/{login}")
        resp.raise_for_status()
        return resp.json()

    async def _get_repos(
        self, client: httpx.AsyncClient, login: str
    ) -> list[dict]:
        resp = await self._request(
            client,
            f"{GITHUB_API}/users/{login}/repos",
            params={"sort": "stars", "direction": "desc", "per_page": 10},
        )
        resp.raise_for_status()
        return resp.json()

    def _format_summary(self, profile: dict, repos: list[dict]) -> str:
        languages: dict[str, int] = {}
        notable: list[str] = []
        for repo in repos:
            lang = repo.get("language")
            if lang:
                languages[lang] = languages.get(lang, 0) + repo.get("stargazers_count", 0)
            stars = repo.get("stargazers_count", 0)
            if stars > 0 or repo.get("fork") is False:
                notable.append(f"{repo['name']} ({stars} stars)")

        top_langs = sorted(languages, key=languages.get, reverse=True)[:5]

        lines = [
            f"GitHub Profile: {profile.get('login', '')}",
            f"URL: {profile.get('html_url', '')}",
            f"Name: {profile.get('name', '')}",
            f"Bio: {profile.get('bio', '')}",
            f"Company: {profile.get('company', '')}",
            f"Location: {profile.get('location', '')}",
            f"Public Repos: {profile.get('public_repos', 0)}",
            f"Followers: {profile.get('followers', 0)}",
            f"Top Languages: {', '.join(top_langs)}",
            f"Notable Repos: {', '.join(notable[:10])}",
        ]
        return "\n".join(lines)

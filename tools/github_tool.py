from __future__ import annotations

import asyncio
import logging
import time
from collections import Counter
from datetime import datetime, timezone

import httpx

from agent.cache import cache
from agent.schemas import ToolResult
from agent.utils import retry_with_backoff
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
        self._prefetched_profile: dict | None = None

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

                # Use prefetched profile from credibility check if available
                if self._prefetched_profile and self._prefetched_profile.get("login") == login:
                    profile = self._prefetched_profile
                    self._prefetched_profile = None
                    repos, starred, events = await asyncio.gather(
                        self._get_repos(client, login),
                        self._get_starred(client, login),
                        self._get_events(client, login),
                    )
                else:
                    profile, repos, starred, events = await asyncio.gather(
                        self._get_profile(client, login),
                        self._get_repos(client, login),
                        self._get_starred(client, login),
                        self._get_events(client, login),
                    )

                activity_level, activity_summary = self._analyze_events(events)
                summary = self._format_summary(
                    profile, repos, starred, activity_level, activity_summary
                )

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

    # Minimum followers to consider a GitHub account credible.
    # Repos alone are too easy to game (empty repos, forks, etc).
    _MIN_FOLLOWERS = 50

    async def _search_user(
        self, client: httpx.AsyncClient, name: str, company: str
    ) -> str | None:
        # Build search queries: full name+company, name only, username guesses
        queries = [f"{name} {company}".strip()]
        if company:
            queries.append(name)

        # Generate username guesses: "firstlast", "first-last", "flast"
        parts = name.strip().lower().split()
        if len(parts) >= 2:
            first, last = parts[0], parts[-1]
            queries.extend([
                f"{first}{last}",
                f"{first}-{last}",
                f"{first[0]}{last}",
            ])

        seen: set[str] = set()
        for query in queries:
            login = await self._try_search_credible(
                client, query, seen,
                expected_name=name, expected_company=company,
            )
            if login:
                return login
        return None

    @retry_with_backoff()
    async def _try_search_credible(
        self, client: httpx.AsyncClient, query: str, seen: set[str],
        expected_name: str = "", expected_company: str = "",
    ) -> str | None:
        resp = await self._request(
            client,
            f"{GITHUB_API}/search/users",
            params={"q": query, "per_page": 5},
        )
        resp.raise_for_status()
        items = resp.json().get("items", [])

        for item in items:
            login = item["login"]
            if login in seen:
                continue
            seen.add(login)

            # Check credibility from profile data
            profile = await self._get_profile(client, login)
            followers = profile.get("followers", 0)
            public_repos = profile.get("public_repos", 0)

            if followers < self._MIN_FOLLOWERS:
                logger.info(
                    f"GitHub skipping low-credibility account: {login} "
                    f"({followers} followers, {public_repos} repos)"
                )
                continue

            # Name cross-validation: profile name must match the search name
            if expected_name and not self._name_matches(profile, expected_name):
                logger.info(
                    f"GitHub skipping name mismatch: {login} "
                    f"(profile name='{profile.get('name', '')}', "
                    f"expected='{expected_name}')"
                )
                continue

            # Company cross-validation: if we searched with a company, check
            # that the profile doesn't list a DIFFERENT company
            if expected_company and not self._company_matches(profile, expected_company):
                logger.info(
                    f"GitHub skipping company mismatch: {login} "
                    f"(profile company='{profile.get('company', '')}', "
                    f"expected='{expected_company}')"
                )
                continue

            logger.info(
                f"GitHub credible match: {login} "
                f"({followers} followers, {public_repos} repos)"
            )
            self._prefetched_profile = profile
            return login

        return None

    @staticmethod
    def _name_matches(profile: dict, expected: str) -> bool:
        """Check if profile name matches the expected person name."""
        profile_name = (profile.get("name") or "").strip().lower()
        if not profile_name:
            return True  # Empty name is neutral, not a mismatch
        expected_lower = expected.strip().lower()
        expected_parts = set(expected_lower.split())
        profile_parts = set(profile_name.split())
        # At least one name part must overlap (first or last name)
        return bool(expected_parts & profile_parts)

    @staticmethod
    def _company_matches(profile: dict, expected: str) -> bool:
        """Check if a profile's company/bio is compatible with the expected company."""
        expected_lower = expected.strip().lower()
        # Check company field
        company_field = (profile.get("company") or "").strip().lower()
        if not company_field:
            return True  # Empty company is neutral, not a mismatch
        # Match if company field contains expected, or expected contains company
        # Also match URLs like "https://vercel.com" for company "Vercel"
        if expected_lower in company_field or company_field in expected_lower:
            return True
        # Check bio as fallback
        bio = (profile.get("bio") or "").lower()
        if expected_lower in bio:
            return True
        # Company field is set but doesn't match — mismatch
        return False

    @retry_with_backoff()
    async def _get_profile(
        self, client: httpx.AsyncClient, login: str
    ) -> dict:
        resp = await self._request(client, f"{GITHUB_API}/users/{login}")
        resp.raise_for_status()
        return resp.json()

    @retry_with_backoff()
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

    @retry_with_backoff()
    async def _fetch_starred(
        self, client: httpx.AsyncClient, login: str
    ) -> list[dict]:
        resp = await self._request(
            client,
            f"{GITHUB_API}/users/{login}/starred",
            params={"sort": "created", "per_page": 20},
        )
        resp.raise_for_status()
        return resp.json()

    async def _get_starred(
        self, client: httpx.AsyncClient, login: str
    ) -> list[dict]:
        try:
            return await self._fetch_starred(client, login)
        except Exception as e:
            logger.warning(f"GitHub starred fetch failed: {e}")
            return []

    @retry_with_backoff()
    async def _fetch_events(
        self, client: httpx.AsyncClient, login: str
    ) -> list[dict]:
        resp = await self._request(
            client,
            f"{GITHUB_API}/users/{login}/events/public",
            params={"per_page": 30},
        )
        resp.raise_for_status()
        return resp.json()

    async def _get_events(
        self, client: httpx.AsyncClient, login: str
    ) -> list[dict]:
        try:
            return await self._fetch_events(client, login)
        except Exception as e:
            logger.warning(f"GitHub events fetch failed: {e}")
            return []

    def _analyze_events(self, events: list[dict]) -> tuple[str, str]:
        if not events:
            return "inactive", "No recent public activity"

        now = datetime.now(timezone.utc)
        recent_count = 0
        event_types: Counter[str] = Counter()
        repos_touched: set[str] = set()

        for event in events:
            created = event.get("created_at", "")
            if created:
                try:
                    dt = datetime.fromisoformat(created.replace("Z", "+00:00"))
                    if (now - dt).days <= 30:
                        recent_count += 1
                except (ValueError, TypeError):
                    pass
            etype = event.get("type", "").replace("Event", "")
            event_types[etype] += 1
            repo = event.get("repo", {}).get("name", "")
            if repo:
                repos_touched.add(repo)

        if recent_count >= 15:
            level = "highly_active"
        elif recent_count >= 5:
            level = "active"
        elif recent_count >= 1:
            level = "occasional"
        else:
            level = "inactive"

        top_types = ", ".join(f"{t}({c})" for t, c in event_types.most_common(3))
        top_repos = ", ".join(list(repos_touched)[:5])
        summary = f"{recent_count} events in last 30 days. Types: {top_types}. Repos: {top_repos}"
        return level, summary

    def _format_summary(
        self,
        profile: dict,
        repos: list[dict],
        starred: list[dict],
        activity_level: str,
        activity_summary: str,
    ) -> str:
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

        recent_stars = [r.get("full_name", "") for r in starred[:10] if r.get("full_name")]

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
            f"Recent Stars: {', '.join(recent_stars) if recent_stars else 'None'}",
            f"Activity Level: {activity_level}",
            f"Recent Activity: {activity_summary}",
        ]
        return "\n".join(lines)

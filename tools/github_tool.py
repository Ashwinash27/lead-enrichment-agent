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

        # Collect all candidates across queries, then pick the best one
        seen: set[str] = set()
        candidates: list[tuple[float, str, dict]] = []  # (score, login, profile)
        for query in queries:
            new_candidates = await self._collect_candidates(
                client, query, seen,
                expected_name=name, expected_company=company,
            )
            candidates.extend(new_candidates)

        if not candidates:
            return None

        # Pick highest-scoring candidate
        candidates.sort(key=lambda c: c[0], reverse=True)
        best_score, best_login, best_profile = candidates[0]

        # Require a minimum score to avoid pure garbage matches
        if best_score < 1.0:
            logger.info(
                f"GitHub no confident match — best was {best_login} "
                f"(score={best_score:.1f})"
            )
            return None

        logger.info(f"GitHub matched: {best_login} (score={best_score:.1f})")
        self._prefetched_profile = best_profile
        return best_login

    @retry_with_backoff()
    async def _collect_candidates(
        self, client: httpx.AsyncClient, query: str, seen: set[str],
        expected_name: str = "", expected_company: str = "",
    ) -> list[tuple[float, str, dict]]:
        """Search GitHub and return scored candidates instead of first-match."""
        resp = await self._request(
            client,
            f"{GITHUB_API}/search/users",
            params={"q": query, "per_page": 5},
        )
        resp.raise_for_status()
        items = resp.json().get("items", [])

        candidates: list[tuple[float, str, dict]] = []
        for item in items:
            login = item["login"]
            if login in seen:
                continue
            seen.add(login)

            profile = await self._get_profile(client, login)
            score = self._score_candidate(profile, login, expected_name, expected_company)

            if score <= 0:
                logger.info(
                    f"GitHub skipping {login} (score={score:.1f}, "
                    f"name='{profile.get('name', '')}', "
                    f"company='{profile.get('company', '')}')"
                )
                continue

            logger.info(
                f"GitHub candidate: {login} (score={score:.1f}, "
                f"name='{profile.get('name', '')}', "
                f"company='{profile.get('company', '')}')"
            )
            candidates.append((score, login, profile))

        return candidates

    @staticmethod
    def _username_matches_name(login: str, expected_name: str) -> bool:
        """Check if a GitHub username is a plausible derivation of the person's name."""
        login_lower = login.lower().replace("-", "").replace("_", "")
        parts = expected_name.strip().lower().split()
        if len(parts) < 2:
            return False
        first, last = parts[0], parts[-1]
        # Match patterns: firstlast, lastfirst, first-last, flast
        return (
            f"{first}{last}" in login_lower
            or f"{last}{first}" in login_lower
            or f"{first[0]}{last}" == login_lower
            or login_lower == first
            or login_lower == last
        )

    @classmethod
    def _score_candidate(
        cls, profile: dict, login: str,
        expected_name: str, expected_company: str,
    ) -> float:
        """Score a GitHub profile candidate. Higher = better match.

        Scoring:
          +3  name field matches expected name
          +2  company field matches expected company
          +1  username looks like a derivation of the name
          +0.5 profile has a bio set
          -1  company field is set but doesn't match (different person risk)
          -5  name field is set but doesn't match (hard reject)
        """
        score = 0.0

        profile_name = (profile.get("name") or "").strip()
        profile_company = (profile.get("company") or "").strip()
        bio = (profile.get("bio") or "").strip()

        # Name matching
        if expected_name:
            if profile_name:
                if cls._name_matches(profile, expected_name):
                    score += 3.0
                else:
                    # Name is set but doesn't match — almost certainly wrong person
                    return -5.0
            # Empty name: no evidence either way, rely on other signals

        # Company matching
        if expected_company:
            if profile_company:
                if cls._company_matches(profile, expected_company):
                    score += 2.0
                else:
                    # Company mismatch: penalty but not a hard reject
                    # (people change jobs)
                    score -= 1.0

        # Username resembles the expected name
        if expected_name and cls._username_matches_name(login, expected_name):
            score += 1.0

        # Bio exists — mild positive signal
        if bio:
            score += 0.5

        return score

    @staticmethod
    def _name_matches(profile: dict, expected: str) -> bool:
        """Check if profile name matches the expected person name."""
        profile_name = (profile.get("name") or "").strip().lower()
        if not profile_name:
            return True  # Empty name is neutral, not a mismatch
        expected_lower = expected.strip().lower()
        expected_parts = expected_lower.split()
        profile_parts = profile_name.split()
        if len(expected_parts) >= 2 and len(profile_parts) >= 2:
            # Both have first+last: require first name match (last names like
            # "Shah", "Smith", "Lee" are too common to match alone)
            return expected_parts[0] == profile_parts[0] or expected_parts[0] in profile_parts
        # Single-word name or single-word profile: any overlap is fine
        return bool(set(expected_parts) & set(profile_parts))

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

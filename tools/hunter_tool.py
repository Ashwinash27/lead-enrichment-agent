from __future__ import annotations

import logging
import time
from urllib.parse import urlparse

import httpx

from agent.cache import cache
from agent.schemas import ToolResult
from config import HUNTER_API_KEY, HTTP_TIMEOUT

logger = logging.getLogger(__name__)

HUNTER_API = "https://api.hunter.io/v2/email-finder"


COMMON_TLDS = [".com", ".ai", ".io", ".co"]


def _domains_from_urls(urls: list[str], company: str) -> list[str]:
    """Extract domains from planner URLs + common TLD variants, deduplicated."""
    seen: set[str] = set()
    domains: list[str] = []

    # Start with domains the planner already identified
    for url in urls:
        host = urlparse(url).hostname or ""
        if host.startswith("www."):
            host = host[4:]
        if host and host not in seen:
            seen.add(host)
            domains.append(host)

    # Add common TLD variants the planner may have missed
    slug = company.lower().replace(" ", "")
    for tld in COMMON_TLDS:
        candidate = slug + tld
        if candidate not in seen:
            seen.add(candidate)
            domains.append(candidate)

    return domains


class HunterIoTool:
    name = "hunter"
    description = (
        "Find a person's professional email address using their name and company domain. "
        "Works best with a full name and company name."
    )

    async def run(self, name: str, company: str, **kwargs) -> ToolResult:
        t0 = time.time()

        if not HUNTER_API_KEY:
            return ToolResult(
                tool_name=self.name,
                success=False,
                error="HUNTER_API_KEY not configured",
                latency_ms=(time.time() - t0) * 1000,
            )

        cache_key = f"hunter:{name}:{company}"
        cached = await cache.get(cache_key)
        if cached is not None:
            return ToolResult(
                tool_name=self.name,
                raw_data=cached,
                success=True,
                latency_ms=(time.time() - t0) * 1000,
            )

        try:
            parts = name.strip().split()
            first_name = parts[0] if parts else name
            last_name = parts[-1] if len(parts) > 1 else ""

            urls_to_scrape = kwargs.get("urls_to_scrape", [])
            domains = _domains_from_urls(urls_to_scrape, company)

            async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
                for domain in domains:
                    resp = await client.get(
                        HUNTER_API,
                        params={
                            "domain": domain,
                            "first_name": first_name,
                            "last_name": last_name,
                            "api_key": HUNTER_API_KEY,
                        },
                    )
                    resp.raise_for_status()
                    data = resp.json().get("data", {})
                    email = data.get("email", "")

                    if email:
                        score = data.get("score", 0)
                        summary = (
                            f"Email: {email}\n"
                            f"Confidence: {score}%\n"
                            f"Type: {data.get('type', 'unknown')}\n"
                            f"Domain: {domain}"
                        )
                        await cache.set(cache_key, summary, ttl=300)
                        return ToolResult(
                            tool_name=self.name,
                            raw_data=summary,
                            success=True,
                            latency_ms=(time.time() - t0) * 1000,
                        )

            return ToolResult(
                tool_name=self.name,
                success=False,
                error=f"No email found (tried {', '.join(domains)})",
                latency_ms=(time.time() - t0) * 1000,
            )

        except Exception as e:
            logger.error(f"HunterIoTool error: {e}")
            return ToolResult(
                tool_name=self.name,
                success=False,
                error=str(e),
                latency_ms=(time.time() - t0) * 1000,
            )

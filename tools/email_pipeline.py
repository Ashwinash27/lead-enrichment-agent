from __future__ import annotations

import logging
import re
import time

import httpx

from agent.cache import cache
from agent.schemas import ToolResult
from agent.utils import retry_with_backoff
from config import HUNTER_API_KEY, PROSPEO_API_KEY, HTTP_TIMEOUT

logger = logging.getLogger(__name__)

HUNTER_API = "https://api.hunter.io/v2/email-finder"
PROSPEO_API = "https://api.prospeo.io/enrich-person"

# Patterns that look like real emails (not noreply, support, etc.)
_JUNK_PREFIXES = {"noreply", "no-reply", "support", "info", "hello", "admin",
                  "contact", "help", "sales", "team", "press", "jobs", "hr",
                  "privacy", "security", "abuse", "webmaster", "postmaster"}

EMAIL_RE = re.compile(
    r"[a-zA-Z0-9][a-zA-Z0-9._%+-]*@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}"
)


def _name_parts(name: str) -> tuple[str, str]:
    parts = name.strip().split()
    first = parts[0].lower() if parts else ""
    last = parts[-1].lower() if len(parts) > 1 else ""
    return first, last


def _is_person_email(email: str, first: str, last: str) -> bool:
    """Check if an email looks like it belongs to the target person."""
    local = email.split("@")[0].lower()
    if local in _JUNK_PREFIXES:
        return False
    # Must contain part of the person's name
    if first and first in local:
        return True
    if last and last in local:
        return True
    return False


def _extract_domains(tool_results: list[ToolResult], company: str) -> list[str]:
    """Extract likely company domains from browser tool results and company slug.

    ONLY uses browser results (planner-selected company sites) + company slug
    variants. Never pulls domains from search/news URLs (those are news sites,
    blogs, etc. — not the person's company).
    """
    from urllib.parse import urlparse

    seen: set[str] = set()
    domains: list[str] = []

    # Company slug variants go FIRST (highest confidence)
    if company:
        slug = company.lower().replace(" ", "").replace("-", "")
        for tld in (".com", ".io", ".ai", ".co"):
            candidate = slug + tld
            if candidate not in seen:
                seen.add(candidate)
                domains.append(candidate)

    # Only pull domains from browser tool results (planner-selected URLs)
    for tr in tool_results:
        if not tr.tool_name.startswith("browser") or not tr.success:
            continue
        for url in tr.urls:
            try:
                host = urlparse(url).hostname or ""
                if host.startswith("www."):
                    host = host[4:]
                # Skip common non-company domains
                if host and not any(host.endswith(s) for s in (
                    "github.com", "google.com", "linkedin.com", "twitter.com",
                    "x.com", "reddit.com", "youtube.com", "facebook.com",
                    "medium.com", "wikipedia.org", "ycombinator.com",
                    "bloomberg.com", "forbes.com", "techcrunch.com",
                    "businessinsider.com", "cnbc.com", "bbc.com",
                )):
                    if host not in seen:
                        seen.add(host)
                        domains.append(host)
            except Exception:
                pass

    return domains


class EmailPipeline:
    name = "email_pipeline"
    description = "Waterfall email finder: GitHub -> regex scan -> SMTP verify -> Prospeo -> Hunter.io"

    async def run(self, name: str, company: str, **kwargs) -> ToolResult:
        t0 = time.time()
        tool_results: list[ToolResult] = kwargs.get("tool_results", [])
        first, last = _name_parts(name)

        # Layer 1: GitHub public email
        email, confidence, source = self._layer_github(tool_results, first, last)
        if email:
            logger.info(f"EmailPipeline: Layer 1 (GitHub) found {email}")
            return self._result(email, confidence, source, t0)

        # Layer 2: Regex scan all raw text
        email, confidence, source = self._layer_regex(tool_results, first, last)
        if email:
            logger.info(f"EmailPipeline: Layer 2 (regex) found {email}")
            return self._result(email, confidence, source, t0)

        # Layer 3: SMTP pattern verification
        domains = _extract_domains(tool_results, company)
        email, confidence, source = await self._layer_smtp(first, last, domains)
        if email:
            logger.info(f"EmailPipeline: Layer 3 (SMTP) found {email}")
            return self._result(email, confidence, source, t0)

        # Layer 4: Prospeo (75 free credits/month, verified emails)
        if PROSPEO_API_KEY and domains:
            email, confidence, source = await self._layer_prospeo(
                first, last, name, company, domains
            )
            if email:
                logger.info(f"EmailPipeline: Layer 4 (Prospeo) found {email}")
                return self._result(email, confidence, source, t0)

        # Layer 5: Hunter.io (fallback, 25/month limit)
        if HUNTER_API_KEY and domains:
            email, confidence, source = await self._layer_hunter(
                first, last, name, company, domains
            )
            if email:
                logger.info(f"EmailPipeline: Layer 5 (Hunter) found {email}")
                return self._result(email, confidence, source, t0)

        return ToolResult(
            tool_name=self.name,
            success=False,
            error="No email found across all layers",
            latency_ms=(time.time() - t0) * 1000,
        )

    def _result(self, email: str, confidence: float, source: str, t0: float) -> ToolResult:
        raw = f"Email: {email}\nConfidence: {confidence}\nSource: {source}"
        return ToolResult(
            tool_name=self.name,
            raw_data=raw,
            success=True,
            latency_ms=(time.time() - t0) * 1000,
        )

    # -- Layer 1: GitHub public email --
    def _layer_github(
        self, tool_results: list[ToolResult], first: str, last: str
    ) -> tuple[str, float, str]:
        for tr in tool_results:
            if tr.tool_name == "github" and tr.success and tr.raw_data:
                emails = EMAIL_RE.findall(tr.raw_data)
                for email in emails:
                    if _is_person_email(email, first, last):
                        return email, 0.95, "github_public"
        return "", 0.0, ""

    # -- Layer 2: Regex scan all raw data --
    def _layer_regex(
        self, tool_results: list[ToolResult], first: str, last: str
    ) -> tuple[str, float, str]:
        for tr in tool_results:
            if tr.success and tr.raw_data:
                emails = EMAIL_RE.findall(tr.raw_data)
                for email in emails:
                    if _is_person_email(email, first, last):
                        return email, 0.6, f"regex_scan:{tr.tool_name}"
        return "", 0.0, ""

    # -- Layer 3: SMTP pattern verification --
    # Hard cap on entire SMTP layer to prevent port 25 timeout cascades
    SMTP_LAYER_TIMEOUT = 8  # seconds

    async def _layer_smtp(
        self, first: str, last: str, domains: list[str]
    ) -> tuple[str, float, str]:
        if not first or not domains:
            return "", 0.0, ""

        try:
            import asyncio
            return await asyncio.wait_for(
                self._layer_smtp_inner(first, last, domains),
                timeout=self.SMTP_LAYER_TIMEOUT,
            )
        except asyncio.TimeoutError:
            logger.warning("EmailPipeline: SMTP layer timed out")
            return "", 0.0, ""

    async def _layer_smtp_inner(
        self, first: str, last: str, domains: list[str]
    ) -> tuple[str, float, str]:
        import asyncio

        patterns = [first]
        if last:
            patterns.extend([
                f"{first}.{last}",
                f"{first}{last}",
                f"{first[0]}.{last}",
                f"{first[0]}{last}",
            ])

        for domain in domains[:2]:  # Max 2 domains
            has_mx, mx_host = await self._check_mx(domain)
            if not has_mx or not mx_host:
                continue

            # Check all patterns concurrently for this domain
            candidates = [f"{local}@{domain}" for local in patterns]
            tasks = [self._smtp_verify(c, mx_host) for c in candidates]
            results = await asyncio.gather(*tasks, return_exceptions=True)

            for i, result in enumerate(results):
                if result is True:
                    return candidates[i], 0.7, f"smtp_verified:{domain}"

        return "", 0.0, ""

    async def _check_mx(self, domain: str) -> tuple[bool, str]:
        """Check if domain has MX records. Returns (has_mx, mx_host)."""
        try:
            import asyncio
            import dns.resolver

            loop = asyncio.get_event_loop()
            answers = await loop.run_in_executor(
                None, lambda: dns.resolver.resolve(domain, "MX")
            )
            if answers:
                mx_host = str(sorted(answers, key=lambda r: r.preference)[0].exchange).rstrip(".")
                return True, mx_host
            return False, ""
        except Exception:
            return False, ""

    async def _smtp_verify(self, email: str, mx_host: str) -> bool:
        """SMTP RCPT TO verification. 3s timeout per connection."""
        try:
            import asyncio

            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(mx_host, 25), timeout=3
            )
            try:
                await asyncio.wait_for(reader.readline(), timeout=3)  # banner
                writer.write(b"EHLO verify.local\r\n")
                await writer.drain()
                await asyncio.wait_for(reader.readline(), timeout=3)

                writer.write(b"MAIL FROM:<verify@verify.local>\r\n")
                await writer.drain()
                await asyncio.wait_for(reader.readline(), timeout=3)

                writer.write(f"RCPT TO:<{email}>\r\n".encode())
                await writer.drain()
                response = await asyncio.wait_for(reader.readline(), timeout=3)

                writer.write(b"QUIT\r\n")
                await writer.drain()

                return response.startswith(b"250")
            finally:
                writer.close()
        except Exception:
            return False

    # -- Layer 4: Prospeo --
    @retry_with_backoff()
    async def _fetch_prospeo(
        self, client: httpx.AsyncClient, first: str, last: str, domain: str,
    ) -> dict:
        """Single Prospeo API call — retried on transient errors."""
        resp = await client.post(
            PROSPEO_API,
            headers={"X-KEY": PROSPEO_API_KEY, "Content-Type": "application/json"},
            json={
                "only_verified_email": True,
                "data": {
                    "first_name": first,
                    "last_name": last,
                    "company_website": domain,
                },
            },
        )
        if resp.status_code in (401, 403):
            logger.warning("Prospeo API key invalid or quota exhausted — skipping")
            return {}
        resp.raise_for_status()
        return resp.json()

    async def _layer_prospeo(
        self, first: str, last: str, full_name: str, company: str,
        domains: list[str],
    ) -> tuple[str, float, str]:
        cache_key = f"prospeo:{full_name}:{company}"
        cached = await cache.get(cache_key)
        if cached is not None:
            for line in cached.split("\n"):
                if line.startswith("Email: "):
                    return line[7:], 0.9, "prospeo_cached"
            return "", 0.0, ""

        try:
            async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
                for domain in domains[:2]:  # Conserve credits
                    try:
                        data = await self._fetch_prospeo(client, first, last, domain)
                    except Exception:
                        continue

                    person = data.get("person") or {}
                    email_obj = person.get("email") or {}
                    email = email_obj.get("email", "")
                    status = email_obj.get("status", "")

                    if email and status == "verified":
                        summary = (
                            f"Email: {email}\n"
                            f"Status: {status}\n"
                            f"Domain: {domain}"
                        )
                        await cache.set(cache_key, summary, ttl=300)
                        return email, 0.9, f"prospeo:{domain}"
        except Exception as e:
            logger.error(f"EmailPipeline Prospeo error: {e}")

        return "", 0.0, ""

    # -- Layer 5: Hunter.io --
    @retry_with_backoff()
    async def _fetch_hunter_domain(
        self, client: httpx.AsyncClient, domain: str, first: str, last: str,
    ) -> dict:
        """Single Hunter.io API call — retried on transient errors."""
        resp = await client.get(
            HUNTER_API,
            params={
                "domain": domain,
                "first_name": first,
                "last_name": last,
                "api_key": HUNTER_API_KEY,
            },
        )
        if resp.status_code in (401, 403):
            logger.warning("Hunter API key invalid or expired — skipping")
            return {"data": {}}
        resp.raise_for_status()
        return resp.json()

    async def _layer_hunter(
        self, first: str, last: str, full_name: str, company: str,
        domains: list[str],
    ) -> tuple[str, float, str]:
        cache_key = f"hunter:{full_name}:{company}"
        cached = await cache.get(cache_key)
        if cached is not None:
            # Parse cached result
            for line in cached.split("\n"):
                if line.startswith("Email: "):
                    return line[7:], 0.85, "hunter_cached"
            return "", 0.0, ""

        try:
            async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
                for domain in domains[:2]:  # Conserve credits
                    try:
                        data = await self._fetch_hunter_domain(
                            client, domain, first, last,
                        )
                    except Exception:
                        continue
                    email_data = data.get("data", {})
                    email = email_data.get("email", "")

                    if email:
                        score = email_data.get("score", 0)
                        confidence = 0.95 if score >= 90 else 0.8
                        summary = (
                            f"Email: {email}\n"
                            f"Confidence: {score}%\n"
                            f"Type: {email_data.get('type', 'unknown')}\n"
                            f"Domain: {domain}"
                        )
                        await cache.set(cache_key, summary, ttl=300)
                        return email, confidence, f"hunter:{domain}"

        except Exception as e:
            logger.error(f"EmailPipeline Hunter error: {e}")

        return "", 0.0, ""

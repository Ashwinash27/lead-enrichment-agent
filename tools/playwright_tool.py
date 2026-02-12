from __future__ import annotations

import asyncio
import logging
import socket
import time
from urllib.parse import urlparse

from playwright.async_api import async_playwright

from agent.cache import cache
from agent.schemas import ToolResult
from config import PLAYWRIGHT_TIMEOUT
from tools.proxy import proxy_manager

logger = logging.getLogger(__name__)

MAX_URLS = 2
MAX_CHARS = 12000
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)


class PlaywrightTool:
    name = "browser"
    description = (
        "Scrape web pages using a headless browser. "
        "Use when you have specific URLs to visit (LinkedIn, personal sites, etc)."
    )

    async def run(self, name: str, company: str, **kwargs) -> ToolResult:
        """Scrape a single URL. Orchestrator spawns one task per URL."""
        t0 = time.time()
        url: str = kwargs.get("url", "")
        if not url:
            return ToolResult(
                tool_name=self.name,
                success=False,
                error="No URL provided to scrape",
                latency_ms=(time.time() - t0) * 1000,
            )

        cache_key = f"browser:{url}"
        cached = await cache.get(cache_key)
        if cached is not None:
            return ToolResult(
                tool_name=self.name,
                raw_data=cached,
                urls=[url],
                success=True,
                latency_ms=(time.time() - t0) * 1000,
            )

        if not await self._domain_exists(url):
            logger.info(f"Skipping {url} — domain does not resolve")
            return ToolResult(
                tool_name=self.name,
                success=False,
                error=f"Domain does not resolve: {url}",
                latency_ms=(time.time() - t0) * 1000,
            )

        text = await self._scrape_url(url, use_proxy=False)
        if not text and proxy_manager.has_proxies:
            logger.info(f"Retrying {url} with rotating proxy")
            text = await self._scrape_url(url, use_proxy=True)
        if text:
            await cache.set(cache_key, text, ttl=300)
            return ToolResult(
                tool_name=self.name,
                raw_data=text,
                urls=[url],
                success=True,
                latency_ms=(time.time() - t0) * 1000,
            )

        return ToolResult(
            tool_name=self.name,
            success=False,
            error=f"Failed to scrape {url}",
            latency_ms=(time.time() - t0) * 1000,
        )

    async def _domain_exists(self, url: str) -> bool:
        """Quick DNS check — returns False if domain doesn't resolve."""
        hostname = urlparse(url).hostname or ""
        if not hostname:
            return False
        loop = asyncio.get_event_loop()
        try:
            await loop.run_in_executor(None, socket.getaddrinfo, hostname, None)
            return True
        except socket.gaierror:
            return False

    async def _scrape_url(self, url: str, use_proxy: bool = True) -> str | None:
        browser = None
        try:
            pw = await async_playwright().start()
            launch_args: dict = {"headless": True}
            proxy_cfg = proxy_manager.get_playwright_proxy() if use_proxy else None
            if proxy_cfg:
                launch_args["proxy"] = proxy_cfg
                launch_args["args"] = ["--ignore-certificate-errors"]
                logger.info(f"Using proxy: {proxy_cfg['server']}")
            else:
                logger.info("Connecting directly (no proxy)")

            browser = await pw.chromium.launch(**launch_args)
            context = await browser.new_context(
                user_agent=USER_AGENT,
                ignore_https_errors=True,
            )
            page = await context.new_page()
            await page.goto(
                url,
                wait_until="domcontentloaded",
                timeout=PLAYWRIGHT_TIMEOUT,
            )
            try:
                await page.wait_for_load_state("networkidle", timeout=5000)
            except Exception:
                pass  # timeout is fine, page may have long-polling

            text = await page.inner_text("body")
            text = text.strip()
            if len(text) > MAX_CHARS:
                text = text[:MAX_CHARS]

            header = f"Content from: {url}\n\n"
            return header + text

        except Exception as e:
            logger.warning(f"Browser failed for {url}: {e}")
            return None
        finally:
            if browser:
                await browser.close()
            try:
                await pw.stop()
            except Exception:
                pass

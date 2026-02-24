"""Retry decorator with exponential backoff for API calls."""

from __future__ import annotations

import asyncio
import functools
import logging
import random

logger = logging.getLogger(__name__)


_QUOTA_DOMAINS = {"api.hunter.io", "api.prospeo.io"}


def _is_retryable(exc: Exception) -> bool:
    """Return True for 5xx, 429, timeout, and connection errors only.

    Skips retry on 429 from quota-based APIs (Hunter, Prospeo) where
    retrying a monthly-limit 429 just wastes credits.
    """
    import httpx

    if isinstance(exc, httpx.HTTPStatusError):
        code = exc.response.status_code
        if code == 429:
            host = exc.request.url.host
            if host in _QUOTA_DOMAINS:
                return False
            return True
        return code >= 500
    if isinstance(exc, (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout,
                        httpx.WriteTimeout, httpx.PoolTimeout)):
        return True

    try:
        from anthropic import APIStatusError, APIConnectionError, APITimeoutError
        if isinstance(exc, APIStatusError):
            return exc.status_code >= 500 or exc.status_code == 429
        if isinstance(exc, (APIConnectionError, APITimeoutError)):
            return True
    except ImportError:
        pass

    if isinstance(exc, (asyncio.TimeoutError, ConnectionError, OSError)):
        return True

    return False


def retry_with_backoff(max_attempts: int = 3, base_delay: float = 1.0, jitter: float = 0.5):
    """Async retry decorator: exponential backoff (1s, 2s, 4s) with jitter.

    Only retries on 5xx, 429, timeout, and connection errors.
    4xx errors are NOT retried.
    """
    def decorator(fn):
        @functools.wraps(fn)
        async def wrapper(*args, **kwargs):
            for attempt in range(1, max_attempts + 1):
                try:
                    return await fn(*args, **kwargs)
                except Exception as e:
                    if attempt == max_attempts or not _is_retryable(e):
                        raise
                    delay = base_delay * (2 ** (attempt - 1)) + random.uniform(-jitter, jitter)
                    delay = max(0.1, delay)
                    logger.warning(
                        f"Retry {attempt}/{max_attempts} for {fn.__qualname__}: "
                        f"{type(e).__name__}: {e}. Waiting {delay:.1f}s"
                    )
                    await asyncio.sleep(delay)
        return wrapper
    return decorator


@retry_with_backoff()
async def llm_create(client, **kwargs):
    """Retrying wrapper for Anthropic client.messages.create()."""
    return await client.messages.create(**kwargs)

from __future__ import annotations

import time

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from config import RATE_LIMIT_PER_HOUR

# Sliding window: API key → list of request timestamps
_request_log: dict[str, list[float]] = {}

_WINDOW = 3600  # 1 hour in seconds
_EXEMPT_PREFIXES = ("/health", "/docs", "/openapi.json", "/static")


class RateLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if RATE_LIMIT_PER_HOUR <= 0:
            return await call_next(request)

        path = request.url.path
        if path == "/" or any(path.startswith(p) for p in _EXEMPT_PREFIXES):
            return await call_next(request)

        key = request.headers.get("X-API-Key") or "anonymous"
        now = time.time()
        cutoff = now - _WINDOW

        # Prune expired timestamps
        timestamps = _request_log.get(key, [])
        timestamps = [t for t in timestamps if t > cutoff]

        if len(timestamps) >= RATE_LIMIT_PER_HOUR:
            oldest = min(timestamps)
            retry_after = int(oldest + _WINDOW - now) + 1
            return JSONResponse(
                status_code=429,
                content={"detail": "Rate limit exceeded", "retry_after": retry_after},
                headers={"Retry-After": str(retry_after)},
            )

        timestamps.append(now)
        _request_log[key] = timestamps

        return await call_next(request)

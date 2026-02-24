from __future__ import annotations

import time
from typing import Any, Protocol


class Cache(Protocol):
    async def get(self, key: str) -> Any | None: ...
    async def set(self, key: str, value: Any, ttl: int = 300) -> None: ...
    async def delete(self, key: str) -> None: ...


MAX_CACHE_SIZE = 1024
_SWEEP_INTERVAL = 128


class InMemoryCache:
    def __init__(self) -> None:
        self._store: dict[str, tuple[Any, float]] = {}
        self._writes_since_sweep: int = 0

    async def get(self, key: str) -> Any | None:
        entry = self._store.get(key)
        if entry is None:
            return None
        value, expires_at = entry
        if time.time() > expires_at:
            del self._store[key]
            return None
        return value

    async def set(self, key: str, value: Any, ttl: int = 300) -> None:
        self._writes_since_sweep += 1
        if self._writes_since_sweep >= _SWEEP_INTERVAL:
            self._sweep_expired()
            self._writes_since_sweep = 0
        if len(self._store) >= MAX_CACHE_SIZE and key not in self._store:
            self._evict_oldest()
        self._store[key] = (value, time.time() + ttl)

    async def delete(self, key: str) -> None:
        self._store.pop(key, None)

    def _sweep_expired(self) -> None:
        now = time.time()
        expired = [k for k, (_, exp) in self._store.items() if now > exp]
        for k in expired:
            del self._store[k]

    def _evict_oldest(self) -> None:
        if not self._store:
            return
        oldest_key = min(self._store, key=lambda k: self._store[k][1])
        del self._store[oldest_key]


cache = InMemoryCache()

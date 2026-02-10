from __future__ import annotations

import itertools
from config import SCRAPERAPI_KEY, PROXY_LIST


class ProxyManager:
    def __init__(self) -> None:
        self._proxies: list[str] = []
        if SCRAPERAPI_KEY:
            self._proxies.append(
                f"http://scraperapi:{SCRAPERAPI_KEY}@proxy-server.scraperapi.com:8001"
            )
        self._proxies.extend(PROXY_LIST)
        self._cycle = itertools.cycle(self._proxies) if self._proxies else None

    @property
    def has_proxies(self) -> bool:
        return bool(self._proxies)

    def get_proxy(self) -> str | None:
        if self._cycle is None:
            return None
        return next(self._cycle)

    def get_playwright_proxy(self) -> dict[str, str] | None:
        proxy = self.get_proxy()
        if proxy is None:
            return None
        # Playwright needs credentials as separate fields, not embedded in URL
        from urllib.parse import urlparse
        parsed = urlparse(proxy)
        result: dict[str, str] = {
            "server": f"{parsed.scheme}://{parsed.hostname}:{parsed.port}",
        }
        if parsed.username:
            result["username"] = parsed.username
        if parsed.password:
            result["password"] = parsed.password
        return result


proxy_manager = ProxyManager()

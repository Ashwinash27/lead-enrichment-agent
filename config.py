import os
from dotenv import load_dotenv

load_dotenv(override=True)

ANTHROPIC_API_KEY: str = os.getenv("ANTHROPIC_API_KEY", "")
ANTHROPIC_MODEL: str = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-20250514")
EXTRACTOR_MODEL: str = os.getenv("EXTRACTOR_MODEL", "claude-haiku-4-5-20251001")
_raw_github_token = os.getenv("GITHUB_TOKEN", "")
GITHUB_TOKEN: str = "" if _raw_github_token.startswith("ghp_...") else _raw_github_token
HUNTER_API_KEY: str = os.getenv("HUNTER_API_KEY", "")
SERPER_API_KEY: str = os.getenv("SERPER_API_KEY", "")
SCRAPERAPI_KEY: str = os.getenv("SCRAPERAPI_KEY", "")
PROXY_LIST: list[str] = [
    p.strip() for p in os.getenv("PROXY_LIST", "").split(",") if p.strip()
]
PLAYWRIGHT_TIMEOUT: int = int(os.getenv("PLAYWRIGHT_TIMEOUT", "15000"))
HTTP_TIMEOUT: int = int(os.getenv("HTTP_TIMEOUT", "15"))

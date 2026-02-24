import os
from dotenv import load_dotenv

load_dotenv(override=True)

ANTHROPIC_API_KEY: str = os.getenv("ANTHROPIC_API_KEY", "")
ANTHROPIC_MODEL: str = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-20250514")
EXTRACTOR_MODEL: str = os.getenv("EXTRACTOR_MODEL", "claude-haiku-4-5-20251001")
_raw_github_token = os.getenv("GITHUB_TOKEN", "")
GITHUB_TOKEN: str = "" if _raw_github_token.startswith("ghp_...") else _raw_github_token
HUNTER_API_KEY: str = os.getenv("HUNTER_API_KEY", "")
PROSPEO_API_KEY: str = os.getenv("PROSPEO_API_KEY", "")
SERPER_API_KEY: str = os.getenv("SERPER_API_KEY", "")
SCRAPERAPI_KEY: str = os.getenv("SCRAPERAPI_KEY", "")
QDRANT_URL: str = os.getenv("QDRANT_URL", "")
QDRANT_API_KEY: str = os.getenv("QDRANT_API_KEY", "")
OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")
SEMANTIC_CACHE_THRESHOLD: float = float(os.getenv("SEMANTIC_CACHE_THRESHOLD", "0.92"))
PROXY_LIST: list[str] = [
    p.strip() for p in os.getenv("PROXY_LIST", "").split(",") if p.strip()
]
PLAYWRIGHT_TIMEOUT: int = int(os.getenv("PLAYWRIGHT_TIMEOUT", "15000"))
HTTP_TIMEOUT: int = int(os.getenv("HTTP_TIMEOUT", "15"))
LANGFUSE_ENABLED: bool = os.getenv("LANGFUSE_ENABLED", "false").lower() == "true"
LANGFUSE_PUBLIC_KEY: str = os.getenv("LANGFUSE_PUBLIC_KEY", "") if LANGFUSE_ENABLED else ""
LANGFUSE_SECRET_KEY: str = os.getenv("LANGFUSE_SECRET_KEY", "") if LANGFUSE_ENABLED else ""
LANGFUSE_HOST: str = os.getenv("LANGFUSE_HOST", "https://cloud.langfuse.com")
ENRICHMENT_API_KEY: str = os.getenv("ENRICHMENT_API_KEY", "")
CORS_ORIGINS: list[str] = [
    o.strip() for o in os.getenv("CORS_ORIGINS", "").split(",") if o.strip()
]

from __future__ import annotations

import logging
import time
import uuid

from agent.schemas import EnrichRequest, EnrichResponse
from config import (
    OPENAI_API_KEY,
    QDRANT_API_KEY,
    QDRANT_URL,
    SEMANTIC_CACHE_THRESHOLD,
)

logger = logging.getLogger(__name__)

COLLECTION = "lead_cache"
EMBEDDING_MODEL = "text-embedding-3-small"
EMBEDDING_DIM = 1536
TTL_SECONDS = 30 * 86400  # 30 days

# ── Clients (lazy-initialized, None if disabled) ────────────────────────

_qdrant = None
_openai = None
_collection_ready = False


def _enabled() -> bool:
    return bool(QDRANT_URL and QDRANT_API_KEY and OPENAI_API_KEY)


async def _clients():
    global _qdrant, _openai
    if _qdrant is not None:
        return _qdrant, _openai

    from qdrant_client import AsyncQdrantClient
    from openai import AsyncOpenAI

    _qdrant = AsyncQdrantClient(url=QDRANT_URL, api_key=QDRANT_API_KEY)
    _openai = AsyncOpenAI(api_key=OPENAI_API_KEY)
    return _qdrant, _openai


async def _ensure_collection() -> None:
    global _collection_ready
    if _collection_ready:
        return

    from qdrant_client.models import Distance, PayloadSchemaType, VectorParams

    qdrant, _ = await _clients()
    collections = await qdrant.get_collections()
    existing = [c.name for c in collections.collections]
    if COLLECTION not in existing:
        await qdrant.create_collection(
            collection_name=COLLECTION,
            vectors_config=VectorParams(size=EMBEDDING_DIM, distance=Distance.COSINE),
        )
        logger.info(f"Created Qdrant collection '{COLLECTION}'")

    # Ensure payload index exists for use_case filtering
    try:
        collection_info = await qdrant.get_collection(COLLECTION)
        indexed_fields = collection_info.payload_schema or {}
        if "use_case" not in indexed_fields:
            await qdrant.create_payload_index(
                collection_name=COLLECTION,
                field_name="use_case",
                field_schema=PayloadSchemaType.KEYWORD,
            )
            logger.info("Created payload index on 'use_case'")
    except Exception as e:
        logger.warning(f"Failed to ensure use_case index: {e}")

    _collection_ready = True


# ── Helpers ──────────────────────────────────────────────────────────────


def _normalize(name: str, company: str) -> str:
    return f"{name.strip().lower()} {company.strip().lower()}"


def _point_id(name: str, company: str, use_case: str = "") -> str:
    normalized = f"{name.strip().lower()}:{company.strip().lower()}:{use_case.strip().lower()}"
    return str(uuid.uuid5(uuid.NAMESPACE_URL, normalized))


async def _embed(text: str) -> list[float]:
    _, openai = await _clients()
    resp = await openai.embeddings.create(model=EMBEDDING_MODEL, input=text)
    return resp.data[0].embedding


# ── Public API ───────────────────────────────────────────────────────────


async def lookup(
    name: str, company: str, trace_id: str, use_case: str = "sales"
) -> EnrichResponse | None:
    if not _enabled():
        return None

    try:
        from qdrant_client.models import FieldCondition, Filter, MatchValue

        await _ensure_collection()
        query_text = _normalize(name, company)
        embedding = await _embed(query_text)

        qdrant, _ = await _clients()
        response = await qdrant.query_points(
            collection_name=COLLECTION,
            query=embedding,
            limit=1,
            score_threshold=SEMANTIC_CACHE_THRESHOLD,
            query_filter=Filter(
                must=[
                    FieldCondition(
                        key="use_case",
                        match=MatchValue(value=use_case),
                    )
                ]
            ),
        )
        results = response.points

        if not results:
            logger.info(f"[{trace_id}] Semantic cache MISS for '{query_text}'")
            return None

        hit = results[0]

        # TTL check
        cached_at = hit.payload.get("cached_at", 0)
        if time.time() - cached_at > TTL_SECONDS:
            logger.info(
                f"[{trace_id}] Semantic cache EXPIRED (score={hit.score:.4f}) "
                f"for '{query_text}'"
            )
            return None

        logger.info(
            f"[{trace_id}] Semantic cache HIT (score={hit.score:.4f}) "
            f"for '{query_text}' → cached '{hit.payload.get('name', '')} @ {hit.payload.get('company', '')}'"
        )
        response_json = hit.payload.get("response_json")
        if response_json:
            return EnrichResponse.model_validate_json(response_json)
        return None

    except Exception as e:
        logger.warning(f"[{trace_id}] Semantic cache lookup failed: {e}")
        return None


async def store(
    request: EnrichRequest, response: EnrichResponse, trace_id: str
) -> None:
    if not _enabled():
        return

    try:
        from qdrant_client.models import PointStruct

        await _ensure_collection()
        query_text = _normalize(request.name, request.company)
        embedding = await _embed(query_text)
        point_id = _point_id(request.name, request.company, request.use_case)

        point = PointStruct(
            id=point_id,
            vector=embedding,
            payload={
                "name": request.name,
                "company": request.company,
                "response_json": response.model_dump_json(),
                "cached_at": time.time(),
                "use_case": request.use_case,
            },
        )

        qdrant, _ = await _clients()
        await qdrant.upsert(collection_name=COLLECTION, points=[point])
        logger.info(f"[{trace_id}] Cached response for '{query_text}'")

    except Exception as e:
        logger.warning(f"[{trace_id}] Semantic cache store failed: {e}")

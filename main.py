import logging
import uvicorn
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from agent.orchestrator import enrich_lead, enrich_lead_streaming
from agent.schemas import EnrichRequest, EnrichResponse
from agent import semantic_cache
from config import CORS_ORIGINS, ENRICHMENT_API_KEY

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)

app = FastAPI(title="Lead Research Agent", version="0.2.0")

# CORS: explicit origins + chrome-extension:// via regex
_allowed_origins = [
    "http://localhost:3000",
    "http://localhost:8000",
] + CORS_ORIGINS

app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins,
    allow_origin_regex=r"^chrome-extension://.*$",
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)


# ── API Key Auth ────────────────────────────────────────────────────────


def _check_api_key(request: Request) -> None:
    """Validate X-API-Key header. No-op if ENRICHMENT_API_KEY is not set."""
    if not ENRICHMENT_API_KEY:
        return
    key = request.headers.get("X-API-Key", "")
    if key != ENRICHMENT_API_KEY:
        raise HTTPException(status_code=401, detail="Invalid or missing API key")


# ── Endpoints ───────────────────────────────────────────────────────────


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/enrich", response_model=EnrichResponse)
async def enrich(request_body: EnrichRequest, request: Request):
    _check_api_key(request)
    return await enrich_lead(request_body)


@app.get("/enrich/stream")
async def enrich_stream(
    request: Request,
    name: str = Query(..., max_length=200),
    company: str = Query("", max_length=200),
    use_case: str = Query("sales", pattern="^(sales|recruiting|job_search)$"),
    location: str = Query("", max_length=200),
):
    """SSE streaming endpoint. Yields events as each pipeline phase completes."""
    _check_api_key(request)

    return StreamingResponse(
        enrich_lead_streaming(
            name=name,
            company=company,
            use_case=use_case,
            location=location,
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.delete("/cache")
async def delete_cache(
    request: Request,
    name: str = Query(..., max_length=200),
    company: str = Query(..., max_length=200),
    use_case: str = Query("", max_length=50),
):
    """Delete a cached enrichment result."""
    _check_api_key(request)
    deleted = await semantic_cache.delete(name, company, use_case)
    if not deleted:
        raise HTTPException(status_code=404, detail="Cache entry not found or cache disabled")
    return {"deleted": True, "name": name, "company": company, "use_case": use_case or "all"}


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)

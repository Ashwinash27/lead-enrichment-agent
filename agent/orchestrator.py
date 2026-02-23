from __future__ import annotations

import logging
import time
import uuid

from agent import observe, semantic_cache
from agent.graph import graph
from agent.schemas import EnrichRequest, EnrichResponse

logger = logging.getLogger(__name__)


async def enrich_lead(request: EnrichRequest) -> EnrichResponse:
    trace_id = uuid.uuid4().hex[:12]
    t0 = time.time()

    # ── Langfuse trace ────────────────────────────────────────────────
    observe.get_or_create_trace(trace_id, {
        "name": request.name,
        "company": request.company,
        "use_case": request.use_case,
    })

    # ── Semantic cache check ─────────────────────────────────────────
    cached = await semantic_cache.lookup(request.name, request.company, trace_id)
    if cached is not None:
        cached.trace_id = trace_id
        cached.latency_ms = round((time.time() - t0) * 1000, 1)
        logger.info(
            f"[{trace_id}] Returning cached response in {cached.latency_ms:.0f}ms"
        )
        return cached

    # ── Full pipeline ────────────────────────────────────────────────
    initial_state = {
        "name": request.name,
        "company": request.company,
        "location": request.location,
        "use_case": request.use_case,
        "output_format": request.output_format,
        "trace_id": trace_id,
        "t0": t0,
        "tool_results": [],
        "errors": [],
    }

    final = await graph.ainvoke(initial_state)

    tool_results = final.get("tool_results", [])
    sources_searched = [tr.tool_name for tr in tool_results]
    successful = [tr for tr in tool_results if tr.success]

    response = EnrichResponse(
        success=len(successful) > 0,
        trace_id=trace_id,
        profile=final.get("profile"),
        sources_searched=sources_searched,
        errors=final.get("errors", []),
        latency_ms=final.get("latency_ms", 0.0),
        narrative=final.get("narrative", ""),
        talking_points=final.get("talking_points", []),
    )

    # ── Cache successful responses ───────────────────────────────────
    if response.success:
        await semantic_cache.store(request, response, trace_id)

    observe.cleanup_trace(trace_id)
    observe.flush()

    return response

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from typing import AsyncGenerator

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


# ── SSE Streaming ────────────────────────────────────────────────────────


def _sse_event(event_type: str, data: dict) -> str:
    """Format a single SSE event string."""
    json_str = json.dumps(data, default=str)
    return f"event: {event_type}\ndata: {json_str}\n\n"


async def enrich_lead_streaming(
    name: str,
    company: str,
    use_case: str = "sales",
    location: str = "",
) -> AsyncGenerator[str, None]:
    """Run the enrichment pipeline, yielding SSE events as phases complete."""
    trace_id = uuid.uuid4().hex[:12]
    t0 = time.time()

    observe.get_or_create_trace(trace_id, {
        "name": name, "company": company, "use_case": use_case,
    })

    # ── Semantic cache check ─────────────────────────────────────────
    cached = await semantic_cache.lookup(name, company, trace_id)
    if cached is not None:
        cached.trace_id = trace_id
        cached.latency_ms = round((time.time() - t0) * 1000, 1)
        logger.info(f"[{trace_id}] SSE: cache hit in {cached.latency_ms:.0f}ms")
        yield _sse_event("cache_hit", cached.model_dump())
        yield _sse_event("complete", cached.model_dump())
        return

    # ── Event queue for streaming ────────────────────────────────────
    queue: asyncio.Queue[dict | None] = asyncio.Queue()

    async def emit_event(event: dict) -> None:
        await queue.put(event)

    initial_state = {
        "name": name,
        "company": company,
        "location": location,
        "use_case": use_case,
        "output_format": "structured",
        "trace_id": trace_id,
        "t0": t0,
        "tool_results": [],
        "errors": [],
        "event_callback": emit_event,
    }

    # ── Background pipeline task ─────────────────────────────────────
    async def run_pipeline() -> None:
        try:
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

            if response.success:
                request = EnrichRequest(
                    name=name, company=company,
                    location=location, use_case=use_case,
                )
                await semantic_cache.store(request, response, trace_id)

            await queue.put({"type": "complete", "data": response.model_dump()})
        except Exception as e:
            logger.error(f"[{trace_id}] SSE pipeline error: {e}")
            await queue.put({"type": "error", "data": {"message": str(e)}})
        finally:
            observe.cleanup_trace(trace_id)
            observe.flush()
            await queue.put(None)  # sentinel to end stream

    # ── Heartbeat to keep service worker alive ───────────────────────
    async def heartbeat() -> None:
        while True:
            await asyncio.sleep(15)
            await queue.put({"type": "heartbeat", "data": {}})

    pipeline_task = asyncio.create_task(run_pipeline())
    heartbeat_task = asyncio.create_task(heartbeat())

    # ── Yield SSE events ─────────────────────────────────────────────
    try:
        while True:
            event = await queue.get()
            if event is None:
                break
            yield _sse_event(event["type"], event.get("data", {}))
    except asyncio.CancelledError:
        pipeline_task.cancel()
        raise
    finally:
        heartbeat_task.cancel()

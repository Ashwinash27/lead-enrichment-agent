from __future__ import annotations

import asyncio
import logging
import time
import uuid

import tools  # noqa: F401 — triggers tool registration

from agent.planner import plan
from agent.extractor import extract
from agent.schemas import EnrichRequest, EnrichResponse, ToolResult
from agent.tool_protocol import registry

logger = logging.getLogger(__name__)

MAX_BROWSER_URLS = 2


def _ts(t0: float) -> str:
    """Wall-clock seconds since t0, formatted for logging."""
    return f"+{time.time() - t0:.1f}s"


async def _run_tool_timed(tool, tool_label: str, t0: float, trace_id: str, tool_kwargs: dict) -> ToolResult:
    """Wrapper that logs wall-clock start/finish for a tool."""
    logger.info(f"[{trace_id}] [{_ts(t0)}] {tool_label} START")
    result = await tool.run(**tool_kwargs)
    logger.info(f"[{trace_id}] [{_ts(t0)}] {tool_label} FINISH ({result.latency_ms:.0f}ms, ok={result.success})")
    return result


async def enrich_lead(request: EnrichRequest) -> EnrichResponse:
    trace_id = uuid.uuid4().hex[:12]
    t0 = time.time()
    errors: list[str] = []

    logger.info(f"[{trace_id}] [{_ts(t0)}] Starting enrichment: {request.name} @ {request.company}")

    # Step 1: Plan
    logger.info(f"[{trace_id}] [{_ts(t0)}] PLANNER START")
    decision = await plan(request.name, request.company, trace_id)
    logger.info(f"[{trace_id}] [{_ts(t0)}] PLANNER FINISH — {decision.tools_to_run} | queries={len(decision.search_queries)}")

    # Step 2: Execute tools concurrently (browser URLs fan out as individual tasks)
    tasks: list[asyncio.Task] = []
    for tool_name in decision.tools_to_run:
        tool = registry.get(tool_name)
        if tool is None:
            errors.append(f"Unknown tool: {tool_name}")
            continue

        if tool_name == "browser":
            for url in decision.urls_to_scrape[:MAX_BROWSER_URLS]:
                kwargs = {"name": request.name, "company": request.company, "url": url}
                label = f"browser:{url}"
                tasks.append(
                    asyncio.create_task(
                        _run_tool_timed(tool, label, t0, trace_id, kwargs),
                        name=label,
                    )
                )
        else:
            kwargs: dict = {"name": request.name, "company": request.company}
            if tool_name == "web_search":
                kwargs["search_queries"] = decision.search_queries
            tasks.append(
                asyncio.create_task(
                    _run_tool_timed(tool, tool_name, t0, trace_id, kwargs),
                    name=tool_name,
                )
            )

    logger.info(f"[{trace_id}] [{_ts(t0)}] TOOLS DISPATCH ({len(tasks)} tasks)")

    tool_results: list[ToolResult] = []
    if tasks:
        results = await asyncio.gather(*tasks, return_exceptions=True)
        logger.info(f"[{trace_id}] [{_ts(t0)}] ALL TOOLS DONE")
        for i, result in enumerate(results):
            task_name = tasks[i].get_name()
            if isinstance(result, Exception):
                logger.error(f"[{trace_id}] {task_name} exception: {result}")
                errors.append(f"{task_name}: {result}")
                tool_results.append(
                    ToolResult(tool_name=task_name, success=False, error=str(result))
                )
            else:
                tr: ToolResult = result
                if tr.success:
                    logger.info(
                        f"[{trace_id}] {tr.tool_name} OK — "
                        f"{len(tr.raw_data)} chars, {tr.latency_ms:.0f}ms"
                    )
                else:
                    logger.warning(f"[{trace_id}] {tr.tool_name} failed: {tr.error}")
                    errors.append(f"{tr.tool_name}: {tr.error}")
                tool_results.append(tr)

    # Step 3: Extract
    successful_results = [tr for tr in tool_results if tr.success]
    logger.info(f"[{trace_id}] [{_ts(t0)}] EXTRACTOR START")
    profile = await extract(
        request.name, request.company, successful_results, trace_id
    )
    logger.info(f"[{trace_id}] [{_ts(t0)}] EXTRACTOR FINISH")

    latency = (time.time() - t0) * 1000
    sources_searched = [tr.tool_name for tr in tool_results]

    logger.info(
        f"[{trace_id}] [{_ts(t0)}] Enrichment complete in {latency:.0f}ms — "
        f"{len(successful_results)}/{len(tool_results)} tools succeeded"
    )

    return EnrichResponse(
        success=len(successful_results) > 0,
        trace_id=trace_id,
        profile=profile,
        sources_searched=sources_searched,
        errors=errors,
        latency_ms=round(latency, 1),
    )

from __future__ import annotations

import asyncio
import logging
import time
import uuid

import tools  # noqa: F401 — triggers tool registration

from agent.planner import plan
from agent.extractor import extract, generate_narrative, generate_talking_points
from agent.schemas import EnrichRequest, EnrichResponse, ToolResult
from agent.tool_protocol import registry
from tools.email_pipeline import EmailPipeline

logger = logging.getLogger(__name__)

MAX_BROWSER_URLS = 3

email_pipeline = EmailPipeline()


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

    # ── Phase A: Planner + deterministic tools (concurrent) ──
    logger.info(f"[{trace_id}] [{_ts(t0)}] PHASE A START (planner + deterministic tools)")

    deterministic_tools = ["github", "news", "community"]
    phase_a_tasks: list[asyncio.Task] = []

    # Planner runs concurrently with deterministic tools
    planner_task = asyncio.create_task(
        plan(request.name, request.company, trace_id, location=request.location),
        name="planner",
    )

    for tool_name in deterministic_tools:
        tool = registry.get(tool_name)
        if tool is None:
            continue
        kwargs = {"name": request.name, "company": request.company}
        phase_a_tasks.append(
            asyncio.create_task(
                _run_tool_timed(tool, tool_name, t0, trace_id, kwargs),
                name=tool_name,
            )
        )

    # Wait for planner + deterministic tools
    phase_a_results_raw = await asyncio.gather(
        planner_task, *phase_a_tasks, return_exceptions=True,
    )

    # Extract planner decision
    planner_result = phase_a_results_raw[0]
    if isinstance(planner_result, Exception):
        logger.error(f"[{trace_id}] Planner exception: {planner_result}, using fallback")
        from agent.planner import _fallback_plan
        decision = _fallback_plan(request.name, request.company, request.location)
        errors.append(f"planner: {planner_result}")
    else:
        decision = planner_result

    logger.info(
        f"[{trace_id}] [{_ts(t0)}] PHASE A DONE — planner: {decision.tools_to_run} | "
        f"queries={len(decision.search_queries)}"
    )

    # Collect Phase A tool results
    tool_results: list[ToolResult] = []
    for i, result in enumerate(phase_a_results_raw[1:]):
        task_name = phase_a_tasks[i].get_name()
        if isinstance(result, Exception):
            logger.error(f"[{trace_id}] {task_name} exception: {result}")
            errors.append(f"{task_name}: {result}")
            tool_results.append(ToolResult(tool_name=task_name, success=False, error=str(result)))
        else:
            tr: ToolResult = result
            if tr.success:
                logger.info(f"[{trace_id}] {tr.tool_name} OK — {len(tr.raw_data)} chars")
            else:
                logger.warning(f"[{trace_id}] {tr.tool_name} failed: {tr.error}")
                errors.append(f"{tr.tool_name}: {tr.error}")
            tool_results.append(tr)

    # ── Phase B: Planner-dependent tools (search + browser) ──
    logger.info(f"[{trace_id}] [{_ts(t0)}] PHASE B START (planner-dependent tools)")

    phase_b_tasks: list[asyncio.Task] = []

    # Web search (always run if planner requested it, or if it's in the decision)
    if "web_search" in decision.tools_to_run:
        tool = registry.get("web_search")
        if tool:
            kwargs = {
                "name": request.name,
                "company": request.company,
                "search_queries": decision.search_queries,
            }
            phase_b_tasks.append(
                asyncio.create_task(
                    _run_tool_timed(tool, "web_search", t0, trace_id, kwargs),
                    name="web_search",
                )
            )

    # Browser (fan out individual URLs)
    if "browser" in decision.tools_to_run:
        tool = registry.get("browser")
        if tool:
            for url in decision.urls_to_scrape[:MAX_BROWSER_URLS]:
                kwargs = {"name": request.name, "company": request.company, "url": url}
                label = f"browser:{url}"
                phase_b_tasks.append(
                    asyncio.create_task(
                        _run_tool_timed(tool, label, t0, trace_id, kwargs),
                        name=label,
                    )
                )

    if phase_b_tasks:
        phase_b_results = await asyncio.gather(*phase_b_tasks, return_exceptions=True)
        for i, result in enumerate(phase_b_results):
            task_name = phase_b_tasks[i].get_name()
            if isinstance(result, Exception):
                logger.error(f"[{trace_id}] {task_name} exception: {result}")
                errors.append(f"{task_name}: {result}")
                tool_results.append(ToolResult(tool_name=task_name, success=False, error=str(result)))
            else:
                tr = result
                if tr.success:
                    logger.info(f"[{trace_id}] {tr.tool_name} OK — {len(tr.raw_data)} chars")
                else:
                    logger.warning(f"[{trace_id}] {tr.tool_name} failed: {tr.error}")
                    errors.append(f"{tr.tool_name}: {tr.error}")
                tool_results.append(tr)

    logger.info(f"[{trace_id}] [{_ts(t0)}] PHASE B DONE")

    # ── Phase B.5: Email waterfall (needs all tool results) ──
    logger.info(f"[{trace_id}] [{_ts(t0)}] PHASE B.5 START (email pipeline)")

    email_result = await _run_tool_timed(
        email_pipeline, "email_pipeline", t0, trace_id,
        {"name": request.name, "company": request.company, "tool_results": tool_results},
    )
    tool_results.append(email_result)
    if not email_result.success:
        errors.append(f"email_pipeline: {email_result.error}")

    logger.info(f"[{trace_id}] [{_ts(t0)}] PHASE B.5 DONE")

    # ── Phase C: Extract + talking points (CONCURRENT) ──
    successful_results = [tr for tr in tool_results if tr.success]
    logger.info(f"[{trace_id}] [{_ts(t0)}] PHASE C START (extract + talking points concurrent)")

    # Extractor and talking points both read raw tool_results — no dependency
    profile, talking_points = await asyncio.gather(
        extract(
            request.name, request.company, successful_results, trace_id,
            location=request.location,
        ),
        generate_talking_points(
            request.name, request.company, successful_results, trace_id,
            use_case=request.use_case,
        ),
    )

    # Narrative (rare) still needs the extracted profile — run after if requested
    narrative = ""
    if request.output_format in ("narrative", "both") and profile:
        logger.info(f"[{trace_id}] [{_ts(t0)}] NARRATIVE START")
        narrative = await generate_narrative(profile, trace_id)

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
        narrative=narrative,
        talking_points=talking_points,
    )

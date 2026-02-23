from __future__ import annotations

import asyncio
import logging
import time

import tools  # noqa: F401 — triggers tool registration

from langgraph.graph import END, START, StateGraph

from agent.extractor import extract, generate_narrative, generate_talking_points
from agent.graph_state import AgentState
from agent.observe import traced_node
from agent.planner import plan, _fallback_plan
from agent.schemas import ToolResult
from agent.tool_protocol import registry
from tools.email_pipeline import EmailPipeline

logger = logging.getLogger(__name__)

MAX_BROWSER_URLS = 3

email_pipeline = EmailPipeline()


# ── Helpers ──────────────────────────────────────────────────────────────


def _ts(t0: float) -> str:
    return f"+{time.time() - t0:.1f}s"


async def _run_tool_timed(
    tool, tool_label: str, t0: float, trace_id: str, tool_kwargs: dict
) -> ToolResult:
    logger.info(f"[{trace_id}] [{_ts(t0)}] {tool_label} START")
    result = await tool.run(**tool_kwargs)
    logger.info(
        f"[{trace_id}] [{_ts(t0)}] {tool_label} FINISH "
        f"({result.latency_ms:.0f}ms, ok={result.success})"
    )
    return result


# ── Node functions ───────────────────────────────────────────────────────


async def planner_node(state: AgentState) -> dict:
    """Phase A (branch 1): Run the LLM planner."""
    t0, trace_id = state["t0"], state["trace_id"]
    logger.info(f"[{trace_id}] [{_ts(t0)}] PHASE A — planner START")

    errors: list[str] = []
    try:
        decision = await plan(
            state["name"], state["company"], trace_id,
            location=state.get("location", ""),
        )
    except Exception as e:
        logger.error(f"[{trace_id}] Planner exception: {e}, using fallback")
        decision = _fallback_plan(
            state["name"], state["company"], state.get("location", ""),
        )
        errors.append(f"planner: {e}")

    logger.info(
        f"[{trace_id}] [{_ts(t0)}] PHASE A — planner DONE: "
        f"{decision.tools_to_run} | queries={len(decision.search_queries)}"
    )
    return {"decision": decision, "errors": errors}


async def deterministic_tools_node(state: AgentState) -> dict:
    """Phase A (branch 2): Run github + news + community concurrently."""
    t0, trace_id = state["t0"], state["trace_id"]
    logger.info(f"[{trace_id}] [{_ts(t0)}] PHASE A — deterministic tools START")

    deterministic_names = ["github", "news", "community"]
    tasks: list[asyncio.Task] = []
    task_names: list[str] = []

    for tool_name in deterministic_names:
        tool = registry.get(tool_name)
        if tool is None:
            continue
        kwargs = {"name": state["name"], "company": state["company"]}
        tasks.append(
            asyncio.create_task(
                _run_tool_timed(tool, tool_name, t0, trace_id, kwargs),
                name=tool_name,
            )
        )
        task_names.append(tool_name)

    results_raw = await asyncio.gather(*tasks, return_exceptions=True)

    tool_results: list[ToolResult] = []
    errors: list[str] = []

    for i, result in enumerate(results_raw):
        name = task_names[i]
        if isinstance(result, Exception):
            logger.error(f"[{trace_id}] {name} exception: {result}")
            errors.append(f"{name}: {result}")
            tool_results.append(
                ToolResult(tool_name=name, success=False, error=str(result))
            )
        else:
            tr: ToolResult = result
            if tr.success:
                logger.info(f"[{trace_id}] {tr.tool_name} OK — {len(tr.raw_data)} chars")
            else:
                logger.warning(f"[{trace_id}] {tr.tool_name} failed: {tr.error}")
                errors.append(f"{tr.tool_name}: {tr.error}")
            tool_results.append(tr)

    logger.info(f"[{trace_id}] [{_ts(t0)}] PHASE A — deterministic tools DONE")
    return {"tool_results": tool_results, "errors": errors}


async def planner_dependent_node(state: AgentState) -> dict:
    """Phase B: Run web_search + browser based on planner decision."""
    t0, trace_id = state["t0"], state["trace_id"]
    logger.info(f"[{trace_id}] [{_ts(t0)}] PHASE B START (planner-dependent tools)")

    decision = state.get("decision")
    if decision is None:
        logger.warning(f"[{trace_id}] No planner decision found, using fallback")
        decision = _fallback_plan(
            state["name"], state["company"], state.get("location", ""),
        )

    tasks: list[asyncio.Task] = []
    task_names: list[str] = []

    if "web_search" in decision.tools_to_run:
        tool = registry.get("web_search")
        if tool:
            kwargs = {
                "name": state["name"],
                "company": state["company"],
                "search_queries": decision.search_queries,
            }
            tasks.append(
                asyncio.create_task(
                    _run_tool_timed(tool, "web_search", t0, trace_id, kwargs),
                    name="web_search",
                )
            )
            task_names.append("web_search")

    if "browser" in decision.tools_to_run:
        tool = registry.get("browser")
        if tool:
            for url in decision.urls_to_scrape[:MAX_BROWSER_URLS]:
                label = f"browser:{url}"
                kwargs = {
                    "name": state["name"],
                    "company": state["company"],
                    "url": url,
                }
                tasks.append(
                    asyncio.create_task(
                        _run_tool_timed(tool, label, t0, trace_id, kwargs),
                        name=label,
                    )
                )
                task_names.append(label)

    tool_results: list[ToolResult] = []
    errors: list[str] = []

    if tasks:
        results_raw = await asyncio.gather(*tasks, return_exceptions=True)
        for i, result in enumerate(results_raw):
            name = task_names[i]
            if isinstance(result, Exception):
                logger.error(f"[{trace_id}] {name} exception: {result}")
                errors.append(f"{name}: {result}")
                tool_results.append(
                    ToolResult(tool_name=name, success=False, error=str(result))
                )
            else:
                tr: ToolResult = result
                if tr.success:
                    logger.info(f"[{trace_id}] {tr.tool_name} OK — {len(tr.raw_data)} chars")
                else:
                    logger.warning(f"[{trace_id}] {tr.tool_name} failed: {tr.error}")
                    errors.append(f"{tr.tool_name}: {tr.error}")
                tool_results.append(tr)

    logger.info(f"[{trace_id}] [{_ts(t0)}] PHASE B DONE")
    return {"tool_results": tool_results, "errors": errors}


async def email_pipeline_node(state: AgentState) -> dict:
    """Phase B.5: Run email waterfall with all prior tool results."""
    t0, trace_id = state["t0"], state["trace_id"]
    logger.info(f"[{trace_id}] [{_ts(t0)}] PHASE B.5 START (email pipeline)")

    all_results = state.get("tool_results", [])
    result = await _run_tool_timed(
        email_pipeline, "email_pipeline", t0, trace_id,
        {"name": state["name"], "company": state["company"], "tool_results": all_results},
    )

    errors: list[str] = []
    if not result.success:
        errors.append(f"email_pipeline: {result.error}")

    logger.info(f"[{trace_id}] [{_ts(t0)}] PHASE B.5 DONE")
    return {"tool_results": [result], "email_result": result, "errors": errors}


async def extractor_node(state: AgentState) -> dict:
    """Phase C: Extract profile + generate talking points concurrently."""
    t0, trace_id = state["t0"], state["trace_id"]
    logger.info(f"[{trace_id}] [{_ts(t0)}] PHASE C START (extract + talking points concurrent)")

    successful_results = [tr for tr in state.get("tool_results", []) if tr.success]

    profile, talking_points = await asyncio.gather(
        extract(
            state["name"], state["company"], successful_results, trace_id,
            location=state.get("location", ""),
        ),
        generate_talking_points(
            state["name"], state["company"], successful_results, trace_id,
            use_case=state.get("use_case", "sales"),
        ),
    )

    logger.info(f"[{trace_id}] [{_ts(t0)}] PHASE C DONE")
    return {"profile": profile, "talking_points": talking_points}


async def output_node(state: AgentState) -> dict:
    """Final node: optional narrative + latency calculation."""
    t0, trace_id = state["t0"], state["trace_id"]

    narrative = ""
    output_format = state.get("output_format", "structured")
    profile = state.get("profile")

    if output_format in ("narrative", "both") and profile:
        logger.info(f"[{trace_id}] [{_ts(t0)}] NARRATIVE START")
        narrative = await generate_narrative(profile, trace_id)

    latency_ms = (time.time() - t0) * 1000
    tool_results = state.get("tool_results", [])
    successful = sum(1 for tr in tool_results if tr.success)

    logger.info(
        f"[{trace_id}] [{_ts(t0)}] Enrichment complete in {latency_ms:.0f}ms — "
        f"{successful}/{len(tool_results)} tools succeeded"
    )

    return {"narrative": narrative, "latency_ms": round(latency_ms, 1)}


# ── Graph construction ──────────────────────────────────────────────────


def build_graph() -> StateGraph:
    builder = StateGraph(AgentState)

    builder.add_node("planner_node", traced_node(planner_node))
    builder.add_node("deterministic_tools_node", traced_node(deterministic_tools_node))
    builder.add_node("planner_dependent_node", traced_node(planner_dependent_node))
    builder.add_node("email_pipeline_node", traced_node(email_pipeline_node))
    builder.add_node("extractor_node", traced_node(extractor_node))
    builder.add_node("output_node", traced_node(output_node))

    # Phase A: fan-out from START to planner + deterministic tools
    builder.add_edge(START, "planner_node")
    builder.add_edge(START, "deterministic_tools_node")

    # Phase B: fan-in — both Phase A branches must complete
    builder.add_edge("planner_node", "planner_dependent_node")
    builder.add_edge("deterministic_tools_node", "planner_dependent_node")

    # Phase B.5 → C → output → END (sequential)
    builder.add_edge("planner_dependent_node", "email_pipeline_node")
    builder.add_edge("email_pipeline_node", "extractor_node")
    builder.add_edge("extractor_node", "output_node")
    builder.add_edge("output_node", END)

    return builder.compile()


graph = build_graph()

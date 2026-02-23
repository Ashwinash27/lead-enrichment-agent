from __future__ import annotations

import operator
from typing import Annotated, Awaitable, Callable, TypedDict

from agent.schemas import EnrichedProfile, PlannerDecision, ToolResult

# Optional callback for SSE event emission (None = no streaming)
EventCallback = Callable[[dict], Awaitable[None]] | None


class AgentState(TypedDict, total=False):
    # Inputs (set once at invocation)
    name: str
    company: str
    location: str
    use_case: str
    trace_id: str
    output_format: str
    t0: float

    # Pipeline state (set by nodes)
    decision: PlannerDecision
    tool_results: Annotated[list[ToolResult], operator.add]  # parallel-safe
    email_result: ToolResult
    profile: EnrichedProfile
    talking_points: list[str]
    narrative: str

    # Observability
    errors: Annotated[list[str], operator.add]  # parallel-safe
    latency_ms: float

    # SSE streaming (set once at invocation, read-only by nodes)
    event_callback: EventCallback

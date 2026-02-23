"""Langfuse observability for LangGraph nodes and LLM calls.

Graceful no-op when LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY are empty.
"""

from __future__ import annotations

import functools
import logging
import time
from typing import Any

from config import LANGFUSE_HOST, LANGFUSE_PUBLIC_KEY, LANGFUSE_SECRET_KEY

logger = logging.getLogger(__name__)

_langfuse = None
_enabled = False
_traces: dict[str, Any] = {}


def _init():
    global _langfuse, _enabled
    if not (LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY):
        return
    try:
        from langfuse import Langfuse
        _langfuse = Langfuse(
            public_key=LANGFUSE_PUBLIC_KEY,
            secret_key=LANGFUSE_SECRET_KEY,
            host=LANGFUSE_HOST or "https://cloud.langfuse.com",
        )
        _enabled = True
        logger.info("Langfuse observability enabled")
    except Exception as e:
        logger.warning(f"Langfuse init failed: {e}")


_init()


# ── Trace management ─────────────────────────────────────────────────────


def get_or_create_trace(trace_id: str, metadata: dict | None = None):
    """Get existing or create new Langfuse trace for an enrichment request."""
    if not _enabled:
        return None
    if trace_id in _traces:
        return _traces[trace_id]
    try:
        trace = _langfuse.trace(
            id=trace_id,
            name="enrichment",
            metadata=metadata or {},
        )
        _traces[trace_id] = trace
        return trace
    except Exception as e:
        logger.warning(f"Langfuse trace creation failed: {e}")
        return None


def cleanup_trace(trace_id: str):
    _traces.pop(trace_id, None)


def flush():
    if _enabled and _langfuse:
        try:
            _langfuse.flush()
        except Exception:
            pass


# ── LLM generation logging ───────────────────────────────────────────────


def log_generation(
    trace_id: str,
    name: str,
    model: str,
    input_tokens: int,
    output_tokens: int,
    latency_ms: float,
):
    """Log an LLM call (planner, extractor, talking_points) to Langfuse."""
    if not _enabled:
        return
    trace = get_or_create_trace(trace_id)
    if trace is None:
        return
    try:
        trace.generation(
            name=name,
            model=model,
            usage={"input": input_tokens, "output": output_tokens},
            metadata={"latency_ms": round(latency_ms, 1)},
        )
    except Exception as e:
        logger.warning(f"Langfuse generation log failed: {e}")


# ── Node instrumentation ─────────────────────────────────────────────────


def traced_node(node_fn):
    """Wrap a LangGraph node function with Langfuse span instrumentation.

    Logs: node name, input size, output size, latency, errors.
    No-op if Langfuse is disabled.
    """
    @functools.wraps(node_fn)
    async def wrapper(state):
        trace_id = state.get("trace_id", "")
        trace = get_or_create_trace(trace_id)
        if trace is None:
            return await node_fn(state)

        t0 = time.time()
        span = None
        try:
            input_size = sum(
                len(str(state.get(k, "")))
                for k in state if k not in ("t0",)
            )
            span = trace.span(
                name=node_fn.__name__,
                input={"input_size_chars": input_size},
            )

            result = await node_fn(state)

            output_size = sum(len(str(v)) for v in result.values()) if result else 0
            errors = result.get("errors", []) if result else []
            span.end(
                output={"output_size_chars": output_size},
                metadata={
                    "latency_ms": round((time.time() - t0) * 1000, 1),
                    "errors": errors,
                },
            )
            return result
        except Exception as e:
            if span:
                span.end(
                    level="ERROR",
                    status_message=str(e),
                    metadata={"latency_ms": round((time.time() - t0) * 1000, 1)},
                )
            raise
    return wrapper

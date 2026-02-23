"""Benchmark runner for lead enrichment agent.

Usage:
    python -m benchmarks.benchmark                     # 1 run, default target
    python -m benchmarks.benchmark --runs 3            # 3 runs
    python -m benchmarks.benchmark --name "X" --company "Y" --runs 2
"""

from __future__ import annotations

import argparse
import asyncio
import io
import json
import logging
import os
import re
import sys
import time

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)

from agent.orchestrator import enrich_lead
from agent.schemas import EnrichRequest


def _parse_phase_timings(log_text: str) -> dict:
    """Extract per-phase timings from log output."""
    timings: dict[str, float] = {}

    # Match patterns like "[trace] [+1.2s] PHASE A — planner START" etc.
    phase_starts: dict[str, float] = {}
    phase_ends: dict[str, float] = {}

    for line in log_text.split("\n"):
        m = re.search(r"\[\+(\d+\.?\d*)s\]", line)
        if not m:
            continue
        ts = float(m.group(1))

        if "PHASE A" in line and "START" in line:
            phase_starts.setdefault("phase_a", ts)
        elif "PHASE A" in line and "DONE" in line:
            phase_ends["phase_a"] = ts

        if "PHASE B START" in line:
            phase_starts.setdefault("phase_b", ts)
        elif "PHASE B DONE" in line:
            phase_ends["phase_b"] = ts

        if "PHASE B.5 START" in line:
            phase_starts.setdefault("phase_b5", ts)
        elif "PHASE B.5 DONE" in line:
            phase_ends["phase_b5"] = ts

        if "PHASE C START" in line:
            phase_starts.setdefault("phase_c", ts)
        elif "PHASE C DONE" in line:
            phase_ends["phase_c"] = ts

    for phase in ["phase_a", "phase_b", "phase_b5", "phase_c"]:
        if phase in phase_starts and phase in phase_ends:
            timings[phase] = round(phase_ends[phase] - phase_starts[phase], 2)

    return timings


async def run_single(
    name: str, company: str, use_case: str, run_idx: int, disable_cache: bool
) -> dict:
    """Run one enrichment and capture timing + logs."""
    if disable_cache:
        import agent.semantic_cache as sc
        sc._enabled = lambda: False

    # Capture logs
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setLevel(logging.INFO)
    root_logger = logging.getLogger()
    root_logger.addHandler(handler)

    t0 = time.time()
    try:
        request = EnrichRequest(name=name, company=company, use_case=use_case)
        response = await enrich_lead(request)
        elapsed = time.time() - t0
        success = response.success
        latency_ms = response.latency_ms
    except Exception as e:
        elapsed = time.time() - t0
        success = False
        latency_ms = elapsed * 1000
        logger.error(f"  Run {run_idx} crashed: {e}")
    finally:
        root_logger.removeHandler(handler)

    log_text = stream.getvalue()
    phase_timings = _parse_phase_timings(log_text)

    result = {
        "run": run_idx,
        "elapsed_s": round(elapsed, 2),
        "latency_ms": round(latency_ms, 1),
        "success": success,
        "phase_timings": phase_timings,
        "cache_disabled": disable_cache,
    }

    label = "COLD" if disable_cache else "WARM"
    print(f"  Run {run_idx} ({label}): {elapsed:.2f}s | phases: {phase_timings}")

    return result


async def main():
    parser = argparse.ArgumentParser(description="Lead enrichment benchmark")
    parser.add_argument("--name", default="Guillermo Rauch", help="Target name")
    parser.add_argument("--company", default="Vercel", help="Target company")
    parser.add_argument("--use-case", default="sales", help="Use case")
    parser.add_argument("--runs", type=int, default=1, help="Number of runs")
    args = parser.parse_args()

    print(f"{'=' * 60}")
    print(f"  LEAD ENRICHMENT BENCHMARK")
    print(f"  Target: {args.name} @ {args.company}")
    print(f"  Runs: {args.runs}")
    print(f"{'=' * 60}")

    wall_start = time.time()
    results: list[dict] = []

    for i in range(args.runs):
        # First run is always cold (no semantic cache)
        disable_cache = (i == 0)
        result = await run_single(
            args.name, args.company, args.use_case, i + 1, disable_cache
        )
        results.append(result)

    wall_elapsed = time.time() - wall_start

    # ── Summary ──────────────────────────────────────────────────────
    latencies = [r["elapsed_s"] for r in results]
    cold_runs = [r for r in results if r["cache_disabled"]]
    warm_runs = [r for r in results if not r["cache_disabled"]]

    print(f"\n{'=' * 60}")
    print(f"  BENCHMARK RESULTS")
    print(f"{'=' * 60}")

    if cold_runs:
        cold_latencies = [r["elapsed_s"] for r in cold_runs]
        print(f"  Cold latency (no cache):")
        print(f"    Mean:   {sum(cold_latencies) / len(cold_latencies):.2f}s")
        print(f"    Min:    {min(cold_latencies):.2f}s")
        print(f"    Max:    {max(cold_latencies):.2f}s")

        # Phase breakdown from first cold run
        if cold_runs[0]["phase_timings"]:
            print(f"  Phase breakdown (cold run 1):")
            for phase, dur in cold_runs[0]["phase_timings"].items():
                print(f"    {phase}: {dur:.2f}s")

    if warm_runs:
        warm_latencies = [r["elapsed_s"] for r in warm_runs]
        print(f"  Warm latency (with cache):")
        print(f"    Mean:   {sum(warm_latencies) / len(warm_latencies):.2f}s")
        print(f"    Min:    {min(warm_latencies):.2f}s")
        print(f"    Max:    {max(warm_latencies):.2f}s")

    print(f"\n  All runs: {[r['elapsed_s'] for r in results]}")
    print(f"  Total wall clock: {wall_elapsed:.1f}s")
    print(f"  Success rate: {sum(1 for r in results if r['success'])}/{len(results)}")
    print(f"{'=' * 60}")

    # Save results
    results_path = os.path.join(os.path.dirname(__file__), "results.json")
    with open(results_path, "w") as f:
        json.dump(
            {
                "target": f"{args.name} @ {args.company}",
                "runs": args.runs,
                "wall_clock_s": round(wall_elapsed, 1),
                "cold_mean_s": round(sum(r["elapsed_s"] for r in cold_runs) / len(cold_runs), 2) if cold_runs else None,
                "warm_mean_s": round(sum(r["elapsed_s"] for r in warm_runs) / len(warm_runs), 2) if warm_runs else None,
                "results": results,
            },
            f,
            indent=2,
        )
    print(f"  Results saved to: {results_path}")


if __name__ == "__main__":
    asyncio.run(main())

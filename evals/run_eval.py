"""CLI runner for lead enrichment evals.

Usage:
    python -m evals.run_eval                  # all cases, with LLM judge
    python -m evals.run_eval --no-judge       # all cases, no LLM judge
    python -m evals.run_eval --case dhh       # single case by ID
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
import time

# Disable Qdrant semantic cache for cold runs BEFORE importing agent modules
os.environ["QDRANT_URL"] = ""

logging.basicConfig(
    level=logging.INFO,
    format="%(message)s",
)
logger = logging.getLogger(__name__)

from agent.orchestrator import enrich_lead
from agent.schemas import EnrichRequest, EnrichResponse
from evals.evaluator import score_case

GROUND_TRUTH = os.path.join(os.path.dirname(__file__), "ground_truth.json")


def _load_cases(case_filter: str | None = None) -> list[dict]:
    with open(GROUND_TRUTH) as f:
        data = json.load(f)
    cases = data["cases"]
    if case_filter:
        cases = [c for c in cases if c["id"] == case_filter]
    return cases


def _count_hunter_hits(log_output: str) -> int:
    return log_output.count("Layer 4 (Hunter)")


async def run_single_case(case: dict) -> dict:
    """Run a single eval case. Returns result dict with score and metadata."""
    case_id = case["id"]
    name = case["name"]
    company = case.get("company", "")
    use_case = case.get("use_case", "sales")

    print(f"\n{'─' * 60}")
    print(f"  CASE: {case_id}")
    print(f"  Name: {name} | Company: {company} | Use case: {use_case}")
    print(f"{'─' * 60}")

    t0 = time.time()
    response: EnrichResponse | None = None
    error_msg = ""

    try:
        request = EnrichRequest(
            name=name,
            company=company,
            use_case=use_case,
        )
        response = await enrich_lead(request)
    except Exception as e:
        error_msg = f"{type(e).__name__}: {e}"
        logger.error(f"  CRASH: {error_msg}")

    elapsed = time.time() - t0

    if response is None:
        # Crashed — score as 0
        return {
            "case_id": case_id,
            "name": name,
            "company": company,
            "elapsed_s": round(elapsed, 1),
            "success": False,
            "error": error_msg,
            "overall_score": 0.0,
            "checks": {},
            "response_success": False,
            "email_found": "",
            "github_found": "",
            "talking_points_count": 0,
        }

    expected = case["expected"]
    result = score_case(response, expected)

    profile = response.profile
    email = profile.email if profile else ""
    gh_user = profile.github.username if profile and profile.github else ""

    print(f"  Latency: {elapsed:.1f}s | Response success: {response.success}")
    print(f"  Email: {email or '(none)'} | GitHub: {gh_user or '(none)'}")
    print(f"  Talking points: {len(response.talking_points)}")
    print(f"  Score: {result['overall']:.2%}")

    # Per-check detail
    for check_name, check_score in result["checks"].items():
        status = "PASS" if check_score >= 1.0 else ("PARTIAL" if check_score > 0 else "FAIL")
        print(f"    {status:7s} {check_name}: {check_score:.2f}")

    return {
        "case_id": case_id,
        "name": name,
        "company": company,
        "elapsed_s": round(elapsed, 1),
        "success": True,
        "error": "",
        "overall_score": result["overall"],
        "checks": result["checks"],
        "response_success": response.success,
        "email_found": email,
        "github_found": gh_user,
        "talking_points_count": len(response.talking_points),
    }


async def main():
    parser = argparse.ArgumentParser(description="Lead enrichment eval runner")
    parser.add_argument("--case", type=str, default=None, help="Run a single case by ID")
    parser.add_argument("--no-judge", action="store_true", help="Skip LLM-as-judge scoring")
    args = parser.parse_args()

    cases = _load_cases(args.case)
    if not cases:
        print(f"No cases found (filter: {args.case})")
        sys.exit(1)

    print(f"{'=' * 60}")
    print(f"  LEAD ENRICHMENT EVAL SUITE")
    print(f"  Cases: {len(cases)} | LLM judge: {'ON' if not args.no_judge else 'OFF'}")
    print(f"  Semantic cache: DISABLED (cold runs)")
    print(f"{'=' * 60}")

    # Capture logs for Hunter.io counting
    log_capture = logging.StreamHandler(stream := __import__("io").StringIO())
    log_capture.setLevel(logging.INFO)
    logging.getLogger().addHandler(log_capture)

    wall_start = time.time()
    results: list[dict] = []

    for case in cases:
        try:
            result = await run_single_case(case)
        except Exception as e:
            logger.error(f"  UNHANDLED CRASH in {case['id']}: {e}")
            result = {
                "case_id": case["id"],
                "name": case["name"],
                "company": case.get("company", ""),
                "elapsed_s": 0.0,
                "success": False,
                "error": str(e),
                "overall_score": 0.0,
                "checks": {},
                "response_success": False,
                "email_found": "",
                "github_found": "",
                "talking_points_count": 0,
            }
        results.append(result)

    wall_elapsed = time.time() - wall_start

    # Count Hunter L4 hits from captured logs
    captured_logs = stream.getvalue()
    hunter_hits = _count_hunter_hits(captured_logs)

    # ── Summary ──────────────────────────────────────────────────────
    print(f"\n{'=' * 60}")
    print(f"  EVAL RESULTS SUMMARY")
    print(f"{'=' * 60}")
    print(f"{'Case':<25} {'Score':>7} {'Time':>7} {'Email':>6} {'GitHub':>8} {'TPs':>4} {'Status':>8}")
    print(f"{'─' * 25} {'─' * 7} {'─' * 7} {'─' * 6} {'─' * 8} {'─' * 4} {'─' * 8}")

    scores = []
    for r in results:
        email_icon = "yes" if r["email_found"] else "no"
        gh_icon = "yes" if r["github_found"] else "no"
        status = "OK" if r["success"] and r["response_success"] else "CRASH" if not r["success"] else "FAIL"
        print(
            f"{r['case_id']:<25} {r['overall_score']:>6.0%} {r['elapsed_s']:>6.1f}s "
            f"{email_icon:>6} {gh_icon:>8} {r['talking_points_count']:>4} {status:>8}"
        )
        scores.append(r["overall_score"])

    avg_score = sum(scores) / len(scores) if scores else 0.0
    total_time = sum(r["elapsed_s"] for r in results)

    print(f"{'─' * 25} {'─' * 7} {'─' * 7} {'─' * 6} {'─' * 8} {'─' * 4} {'─' * 8}")
    print(f"{'AVERAGE':<25} {avg_score:>6.0%} {total_time:>6.1f}s")
    print()
    print(f"  Total wall clock: {wall_elapsed:.1f}s")
    print(f"  Hunter.io L4 hits: {hunter_hits}")
    print(f"  Cases passed (>= 80%): {sum(1 for s in scores if s >= 0.8)}/{len(scores)}")
    print(f"  Cases failed (0%): {sum(1 for s in scores if s == 0.0)}/{len(scores)}")
    print(f"{'=' * 60}")

    # Write raw results JSON
    results_path = os.path.join(os.path.dirname(__file__), "results.json")
    with open(results_path, "w") as f:
        json.dump(
            {
                "wall_clock_s": round(wall_elapsed, 1),
                "avg_score": round(avg_score, 4),
                "hunter_l4_hits": hunter_hits,
                "cases": results,
            },
            f,
            indent=2,
        )
    print(f"  Results saved to: {results_path}")


if __name__ == "__main__":
    asyncio.run(main())

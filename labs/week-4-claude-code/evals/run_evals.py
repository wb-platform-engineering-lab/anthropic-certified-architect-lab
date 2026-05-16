"""
evals/run_evals.py — Evaluation harness for the Resolve agent pipeline.

Runs the coordinator against a set of synthetic tickets and validates each
output against output_schema.json. Writes per-ticket JSON results to
evals/results/.

Usage:
    python -m evals.run_evals                  # run all fixtures
    python -m evals.run_evals --ticket 001     # run one fixture by number
    python -m evals.run_evals --limit 10       # run first 10 fixtures

WARNING (from evals/CLAUDE.md):
  Do not modify this file without first running it to establish a baseline.
  Changes to the harness logic must be compared against a baseline run.
  The eval harness is the ground truth for system behaviour.
"""

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

from agents.coordinator import run_coordinator

FIXTURES_DIR = Path(__file__).parent / "fixtures"
RESULTS_DIR = Path(__file__).parent / "results"
SCHEMA_PATH = Path(__file__).parent.parent / "output_schema.json"

VALID_EXIT_REASONS = {"success", "budget_exhausted", "truncated", "error"}
VALID_CLASSIFICATIONS = {"billing", "account", "incident", "mixed", "unknown"}


def load_schema() -> dict:
    with open(SCHEMA_PATH) as f:
        return json.load(f)


def load_fixtures(limit: Optional[int] = None, ticket_num: Optional[str] = None) -> list[dict]:
    fixtures = []
    pattern = f"ticket_{ticket_num}.json" if ticket_num else "ticket_*.json"
    paths = sorted(FIXTURES_DIR.glob(pattern))

    for path in paths[:limit] if limit else paths:
        with open(path) as f:
            fixtures.append(json.load(f))
    return fixtures


def validate_result(result: dict, expected: dict, schema: dict) -> list[str]:
    """
    Validate a coordinator result against the output schema and expected values.
    Returns a list of failure reasons (empty = pass).
    """
    failures = []

    # Required fields
    for field in schema["required"]:
        if field not in result:
            failures.append(f"Missing required field: {field}")

    # exit_reason must be a valid value
    exit_reason = result.get("exit_reason", "")
    if exit_reason not in VALID_EXIT_REASONS:
        failures.append(f"Invalid exit_reason: '{exit_reason}'")

    # classification must be a valid value
    classification = result.get("classification", "")
    if classification not in VALID_CLASSIFICATIONS:
        failures.append(f"Invalid classification: '{classification}'")

    # Check expected values from the fixture
    if "expected_exit_reason" in expected:
        if exit_reason != expected["expected_exit_reason"]:
            failures.append(
                f"exit_reason mismatch: got '{exit_reason}', expected '{expected['expected_exit_reason']}'"
            )

    if "expected_classification" in expected:
        if classification != expected["expected_classification"]:
            failures.append(
                f"classification mismatch: got '{classification}', expected '{expected['expected_classification']}'"
            )

    if "expected_agents" in expected:
        dispatched = result.get("dispatched_agents", [])
        for agent in expected["expected_agents"]:
            if agent not in dispatched:
                failures.append(f"Expected {agent} to be dispatched, but it was not.")

    # Violations should be empty for clean tickets (unless expected)
    violations = result.get("violations", [])
    if violations and not expected.get("allow_violations", False):
        failures.append(f"Unexpected violations: {violations}")

    return failures


def run_evals(limit: Optional[int] = None, ticket_num: Optional[str] = None) -> int:
    """Run the eval harness. Returns exit code (0 = all pass, 1 = failures)."""
    schema = load_schema()
    fixtures = load_fixtures(limit=limit, ticket_num=ticket_num)

    if not fixtures:
        print("No fixtures found. Add ticket_*.json files to evals/fixtures/.")
        return 1

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_results_dir = RESULTS_DIR / f"run_{timestamp}"
    run_results_dir.mkdir()

    total = len(fixtures)
    passed = 0
    failed = 0

    print("=" * 60)
    print(f"Resolve Eval Harness — {total} fixture(s)")
    print(f"Timestamp: {timestamp}")
    print("=" * 60)
    print()

    all_results = []

    for fixture in fixtures:
        ticket_id = fixture["ticket_id"]
        customer_id = fixture["customer_id"]
        ticket_text = fixture["ticket_text"]

        print(f"[{ticket_id}] Running... ", end="", flush=True)

        try:
            result = run_coordinator(customer_id, ticket_text, ticket_id=ticket_id)
        except Exception as e:
            result = {
                "ticket_id": ticket_id,
                "exit_reason": "error",
                "error": str(e),
            }

        failures = validate_result(result, fixture, schema)
        status = "PASS" if not failures else "FAIL"

        if not failures:
            passed += 1
            print(f"PASS  (exit={result.get('exit_reason', '?')}, "
                  f"agents={result.get('dispatched_agents', [])})")
        else:
            failed += 1
            print(f"FAIL")
            for reason in failures:
                print(f"       ✗ {reason}")

        # Write per-ticket result
        per_ticket = {
            "fixture": fixture,
            "result": result,
            "failures": failures,
            "status": status,
        }
        all_results.append(per_ticket)
        result_path = run_results_dir / f"{ticket_id}.json"
        with open(result_path, "w") as f:
            json.dump(per_ticket, f, indent=2)

    print()
    print("=" * 60)
    print(f"Results: {passed}/{total} passed, {failed}/{total} failed")
    print(f"Results written to: {run_results_dir}")
    print("=" * 60)

    # Write summary
    summary = {
        "timestamp": timestamp,
        "total": total,
        "passed": passed,
        "failed": failed,
        "pass_rate": round(passed / total, 3) if total > 0 else 0,
        "tickets": [
            {"ticket_id": r["fixture"]["ticket_id"], "status": r["status"], "failures": r["failures"]}
            for r in all_results
        ],
    }
    with open(run_results_dir / "summary.json", "w") as f:
        json.dump(summary, f, indent=2)

    return 0 if failed == 0 else 1


def main() -> None:
    parser = argparse.ArgumentParser(description="Resolve eval harness")
    parser.add_argument("--ticket", help="Run a single fixture by number (e.g. 001)")
    parser.add_argument("--limit", type=int, help="Run at most N fixtures")
    args = parser.parse_args()

    sys.exit(run_evals(limit=args.limit, ticket_num=args.ticket))


if __name__ == "__main__":
    main()

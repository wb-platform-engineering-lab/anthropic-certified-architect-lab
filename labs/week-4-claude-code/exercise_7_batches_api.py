"""
exercise_7_batches_api.py — Message Batches API: Right Tool, Right Workflow

Exam Scenario 5 Q11 asks whether to switch BOTH of these workflows to the Batches API:
  (A) Pre-merge security check  — developers wait for this before merging
  (B) Overnight technical debt report — generated at 2 AM, reviewed next morning

The answer is: switch B only. The 50% cost saving is real, but the Batches API has no
latency guarantee. "Often completes in minutes" is not an SLA you can give a developer
who is blocking a merge on the result.

This file demonstrates both workflows and explains why A is wrong and B is correct.
"""

import time
import json
import anthropic

from dotenv import load_dotenv
from typing import Optional

load_dotenv()

client = anthropic.Anthropic()

# =============================================================================
# WORKFLOW A — WRONG: Pre-merge security check via Batches API
# =============================================================================
#
# A developer opens a PR. CI runs a security check. The developer waits for
# the result before merging. This is a blocking, human-facing workflow.
#
# The code below works — the Batches API accepts the request just fine.
# The problem is operational: there is no guarantee the result arrives in
# time for the developer's workflow. "Often completes in minutes" is not a
# service level agreement.
#

def wrong_workflow_a_pre_merge_check(pr_diff: str) -> None:
    """
    WRONG for this use case. Demonstrates what you could do — and why you shouldn't.

    The Batches API is asynchronous. Results are available within 24 hours.
    A developer waiting for a pre-merge check cannot wait 24 hours (or even
    an unpredictable number of minutes). Use the standard Messages API here.
    """
    print("=" * 60)
    print("WORKFLOW A — Pre-merge security check (WRONG approach)")
    print("=" * 60)
    print()
    print("Submitting batch for pre-merge security check...")
    print()

    # This code runs without error. That is not the point.
    # The problem is that the developer is waiting for this result.
    batch = client.beta.messages.batches.create(
        requests=[
            {
                "custom_id": "pre-merge-security-check",
                "params": {
                    "model": "claude-opus-4-5",
                    "max_tokens": 512,
                    "messages": [
                        {
                            "role": "user",
                            "content": (
                                f"Review this PR diff for security issues.\n\n"
                                f"```diff\n{pr_diff}\n```\n\n"
                                "Output: PASS if no issues, or FAIL: <description> for each issue."
                            ),
                        }
                    ],
                },
            }
        ]
    )

    print(f"Batch submitted: {batch.id}")
    print(f"Status: {batch.processing_status}")
    print()
    print("WHY THIS IS WRONG:")
    print("-" * 50)
    print("  - The developer is blocked on this result before merging.")
    print("  - Batches API has no guaranteed latency SLA.")
    print("  - 'Often completes in minutes' is not an SLA.")
    print("  - If the batch takes 2 hours, the developer's PR is blocked for 2 hours.")
    print("  - If the batch takes 23 hours, the developer may merge stale code anyway.")
    print()
    print("CORRECT APPROACH for pre-merge checks:")
    print("  Use client.messages.create() — synchronous, returns in seconds,")
    print("  developer gets a result before their attention drifts.")
    print()

    # We will not poll for this result — it is not a pattern to demonstrate.
    # In a real pre-merge check, you would not use the Batches API at all.


# =============================================================================
# WORKFLOW B — CORRECT: Overnight technical debt report via Batches API
# =============================================================================
#
# At 2 AM, the CI system queues analysis requests for every file that changed
# in the past week. Engineers review the report when they arrive in the morning.
# The 24-hour result window fits comfortably within the overnight schedule.
# 50% cost reduction applies to all requests in the batch.
#

# Files to analyse — simulated as the "changed this week" set
FILES_TO_ANALYSE: list[dict] = [
    {
        "custom_id": "file-agents-coordinator",
        "path": "agents/coordinator.py",
        "description": "Coordinator dispatch logic and iteration budget",
    },
    {
        "custom_id": "file-agents-subagents",
        "path": "agents/subagents.py",
        "description": "Subagent runner and tool call contract",
    },
    {
        "custom_id": "file-agents-session-state",
        "path": "agents/session_state.py",
        "description": "SessionState dataclass and field definitions",
    },
    {
        "custom_id": "file-agents-hooks",
        "path": "agents/hooks.py",
        "description": "PreCallHook and PostCallHook implementations",
    },
    {
        "custom_id": "file-evals-run-evals",
        "path": "evals/run_evals.py",
        "description": "Evaluation harness — 50 synthetic tickets",
    },
]


def build_analysis_prompt(file_info: dict) -> str:
    """Build the technical debt analysis prompt for a single file."""
    return (
        f"Analyse {file_info['path']} ({file_info['description']}) for technical debt.\n\n"
        "Look for:\n"
        "  - Functions longer than 40 lines that should be split\n"
        "  - Missing error handling (bare except, unchecked return values)\n"
        "  - Hard-coded values that should be configuration\n"
        "  - Missing type annotations on public functions\n"
        "  - Duplicated logic that belongs in a shared utility\n\n"
        "Output a JSON object with these fields:\n"
        "  file: the file path\n"
        "  debt_items: array of {category, line_hint, description}\n"
        "  severity: 'low' | 'medium' | 'high'\n"
        "  estimated_effort_hours: integer\n\n"
        "If no debt found, output: {\"file\": \"<path>\", \"debt_items\": [], "
        "\"severity\": \"low\", \"estimated_effort_hours\": 0}\n\n"
        "Output only valid JSON. No markdown fences. No prose."
    )


def submit_overnight_batch() -> str:
    """
    Submit the overnight technical debt analysis batch.

    Called at 2 AM by a scheduled CI job. Returns the batch ID for the
    morning retrieval job to use. The batch ID is stored in a shared file
    or environment variable between the submission and retrieval jobs.
    """
    print("=" * 60)
    print("WORKFLOW B — Overnight technical debt report (CORRECT approach)")
    print("=" * 60)
    print()
    print(f"[2 AM] Submitting batch of {len(FILES_TO_ANALYSE)} file analyses...")
    print()

    requests = [
        {
            "custom_id": file_info["custom_id"],
            "params": {
                "model": "claude-opus-4-5",
                "max_tokens": 1024,
                "messages": [
                    {
                        "role": "user",
                        "content": build_analysis_prompt(file_info),
                    }
                ],
            },
        }
        for file_info in FILES_TO_ANALYSE
    ]

    batch = client.beta.messages.batches.create(requests=requests)

    print(f"Batch submitted successfully.")
    print(f"  Batch ID        : {batch.id}")
    print(f"  Requests        : {len(requests)}")
    print(f"  Status          : {batch.processing_status}")
    print(f"  Expected by     : morning (within 24 hours)")
    print()
    print("WHY THIS IS CORRECT:")
    print("-" * 50)
    print("  - Engineers are asleep. No one is waiting for this result.")
    print("  - The 24-hour result window fits within the overnight schedule.")
    print("  - 50% cost reduction applies to all 5 analysis requests.")
    print("  - Results will be ready when engineers arrive in the morning.")
    print("  - custom_id lets us correlate results back to specific files.")
    print()

    return batch.id


def poll_until_complete(batch_id: str, poll_interval_seconds: int = 5) -> None:
    """
    Poll the batch until processing_status == "ended".

    In the overnight job, this runs in the morning retrieval step.
    In this demo, we poll with a short interval to show the mechanism.
    In production, the morning job might just check once — the batch
    will have completed during the night.
    """
    print(f"[Morning retrieval] Polling batch {batch_id} for completion...")
    print()

    attempt = 0
    while True:
        attempt += 1
        batch = client.beta.messages.batches.retrieve(batch_id)

        print(f"  Poll attempt {attempt}: status={batch.processing_status}")

        if batch.processing_status == "ended":
            print(f"  Batch complete after {attempt} poll(s).")
            print()
            break

        # In production: poll every few minutes in the morning retrieval job.
        # In this demo: short sleep to show the polling loop without blocking.
        time.sleep(poll_interval_seconds)


def retrieve_and_print_results(batch_id: str) -> list[dict]:
    """
    Retrieve batch results and correlate them back to source files using custom_id.

    The custom_id field is the mechanism for correlation. It is not optional —
    without it, you have no way to know which result corresponds to which file.
    Batch results are not guaranteed to arrive in submission order.
    """
    print("Retrieving results and correlating by custom_id...")
    print()

    # Build a lookup from custom_id to file info for correlation
    file_lookup: dict[str, dict] = {f["custom_id"]: f for f in FILES_TO_ANALYSE}

    results: list[dict] = []
    error_count: int = 0

    for result in client.beta.messages.batches.results(batch_id):
        custom_id: str = result.custom_id
        file_info: Optional[dict] = file_lookup.get(custom_id)

        if file_info is None:
            print(f"  WARNING: Received result for unknown custom_id: {custom_id}")
            continue

        if result.result.type == "succeeded":
            raw_content: str = result.result.message.content[0].text
            try:
                parsed: dict = json.loads(raw_content)
                results.append(
                    {
                        "custom_id": custom_id,
                        "file": file_info["path"],
                        "analysis": parsed,
                        "status": "success",
                    }
                )
            except json.JSONDecodeError:
                results.append(
                    {
                        "custom_id": custom_id,
                        "file": file_info["path"],
                        "analysis": None,
                        "status": "parse_error",
                        "raw": raw_content,
                    }
                )
                error_count += 1

        elif result.result.type == "errored":
            error_count += 1
            results.append(
                {
                    "custom_id": custom_id,
                    "file": file_info["path"],
                    "analysis": None,
                    "status": "api_error",
                    "error": str(result.result.error),
                }
            )

    return results


def print_summary_table(results: list[dict]) -> None:
    """Print a formatted summary table of the technical debt report."""
    print("=" * 60)
    print("OVERNIGHT TECHNICAL DEBT REPORT — Summary")
    print("=" * 60)
    print()

    # Header
    print(f"{'File':<35} {'Severity':<10} {'Items':<6} {'Effort (h)'}")
    print("-" * 65)

    total_hours: int = 0
    total_items: int = 0

    for r in results:
        file_path: str = r["file"]
        if r["status"] != "success" or r["analysis"] is None:
            print(f"{file_path:<35} {'ERROR':<10} {'—':<6} —")
            continue

        analysis: dict = r["analysis"]
        severity: str = analysis.get("severity", "unknown")
        items: int = len(analysis.get("debt_items", []))
        effort: int = analysis.get("estimated_effort_hours", 0)

        total_hours += effort
        total_items += items

        print(f"{file_path:<35} {severity:<10} {items:<6} {effort}")

    print("-" * 65)
    print(f"{'TOTAL':<35} {'':<10} {total_items:<6} {total_hours}")
    print()

    if total_items == 0:
        print("No technical debt found. Codebase is clean.")
    else:
        print(f"Total: {total_items} debt item(s) across {len(results)} file(s).")
        print(f"Estimated remediation effort: {total_hours} hours.")
    print()


# =============================================================================
# Batch API rules — printed at the end of every run as a reminder
# =============================================================================

BATCH_API_RULES = """
============================================================
MESSAGE BATCHES API — WHEN TO USE AND WHEN NOT TO USE
============================================================

CORRECT USE CASES (24h turnaround is acceptable):
  - Overnight technical debt reports
  - Weekly codebase quality analysis
  - Background classification of large ticket archives
  - Nightly test coverage analysis
  - Any job that runs while engineers are not waiting for it
  - Cost-sensitive batch jobs where 50% savings outweigh latency flexibility

WRONG USE CASES (a human or system is waiting for the response):
  - Pre-merge security checks (developer is blocking on result)
  - Interactive agent loops (user is in an active conversation)
  - Synchronous CI pipelines where a deploy is gated on the result
  - Any workflow where "often completes in minutes" is not an acceptable SLA
  - Real-time tool calls within an agentic loop

THE custom_id FIELD:
  custom_id is how you correlate batch results back to the requests that
  produced them. Batch results are NOT guaranteed to arrive in submission
  order. Without custom_id, you cannot know which result belongs to which
  request. It is not optional — treat it as a required field.

COST:
  The Batches API offers approximately 50% cost reduction compared to the
  standard Messages API. This saving is real. The trade-off is no guaranteed
  latency SLA. Accept the trade-off only when the 24h window is acceptable.

EXAM NOTE (Scenario 5 Q11):
  The question presents BOTH a pre-merge check AND an overnight report and
  asks whether to switch both to Batches. The correct answer is: switch the
  overnight report only. The pre-merge check requires real-time latency that
  the Batches API cannot guarantee.
============================================================
"""


# =============================================================================
# Main
# =============================================================================

def main() -> None:
    # --- Workflow A: demonstrate the wrong approach ---
    sample_diff = (
        "diff --git a/agents/coordinator.py b/agents/coordinator.py\n"
        "--- a/agents/coordinator.py\n"
        "+++ b/agents/coordinator.py\n"
        "@@ -42,6 +42,8 @@ def dispatch(state: SessionState) -> str:\n"
        "+    # Temporary: bypass hook check for performance testing\n"
        "+    if os.environ.get('SKIP_HOOKS'):\n"
        "+        return run_subagent(agent_name, state, tools, skip_hooks=True)\n"
    )
    wrong_workflow_a_pre_merge_check(sample_diff)

    print()
    print("-" * 60)
    print()

    # --- Workflow B: demonstrate the correct approach ---
    batch_id = submit_overnight_batch()

    # Poll until complete (short interval for demo purposes)
    poll_until_complete(batch_id, poll_interval_seconds=3)

    # Retrieve and correlate results
    results = retrieve_and_print_results(batch_id)

    # Print the summary table
    print_summary_table(results)

    # Always print the rules at the end
    print(BATCH_API_RULES)


if __name__ == "__main__":
    main()

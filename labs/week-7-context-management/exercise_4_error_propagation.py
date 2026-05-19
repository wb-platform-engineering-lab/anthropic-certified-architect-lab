"""
exercise_4_error_propagation.py — Error Propagation in Multi-Agent Chains

A failure at step N should not silently corrupt steps N+1, N+2...

Problem: When step 3 (incident lookup) fails and returns {}, step 4
(billing check) sees an empty incident lookup and proceeds as if no
incidents exist. Step 5 drafts a reply ignoring an active incident.

Fix: Every step checks context health before running. If a required
previous step has status="failed", the current step aborts rather than
proceeding with corrupted input.

Pipeline:
  Step 1: account_lookup     (required)
  Step 2: billing_lookup     (required)
  Step 3: incident_lookup    (optional — failure does not block the pipeline)
  Step 4: classify_ticket    (required)
  Step 5: draft_reply        (required)
"""

import json
from typing import Optional
import anthropic
from dotenv import load_dotenv

load_dotenv()
client = anthropic.Anthropic()

MODEL = "claude-haiku-4-5-20251001"

# ---------------------------------------------------------------------------
# Pipeline configuration — which steps are required vs optional
# ---------------------------------------------------------------------------

PIPELINE_STEPS = [
    {"name": "account_lookup",  "required": True},
    {"name": "billing_lookup",  "required": True},
    {"name": "incident_lookup", "required": False},   # optional — failure is tolerated
    {"name": "classify_ticket", "required": True},
    {"name": "draft_reply",     "required": True},
]

# ---------------------------------------------------------------------------
# Simulated step implementations
# ---------------------------------------------------------------------------

def run_account_lookup(customer_id: str, scenario: str = "success") -> dict:
    if scenario == "fail":
        return {"status": "failed", "step": "account_lookup", "reason": "CRM timeout"}
    return {"status": "success", "step": "account_lookup",
            "plan": "Pro", "active": True}


def run_billing_lookup(customer_id: str, scenario: str = "success") -> dict:
    if scenario == "fail":
        return {"status": "failed", "step": "billing_lookup", "reason": "Billing system unavailable"}
    return {"status": "success", "step": "billing_lookup",
            "invoices": [{"id": "INV-001", "amount": 89.0}], "duplicate": True}


def run_incident_lookup(customer_id: str, scenario: str = "success") -> dict:
    if scenario == "fail":
        return {"status": "failed", "step": "incident_lookup", "reason": "Incident API timeout"}
    return {"status": "success", "step": "incident_lookup", "active_incidents": []}


def run_classify_ticket(ticket: str, context: dict) -> dict:
    """Use the model to classify based on gathered context."""
    summary = json.dumps({k: v for k, v in context.items() if v.get("status") == "success"}, indent=2)
    response = client.messages.create(
        model=MODEL,
        max_tokens=128,
        messages=[{
            "role": "user",
            "content": (
                f"Ticket: {ticket}\n"
                f"Context gathered:\n{summary}\n\n"
                "Classify this ticket as: escalate, auto_resolve, or needs_info. "
                "Reply with just the classification and one sentence reason."
            ),
        }],
    )
    return {"status": "success", "step": "classify_ticket", "classification": response.content[0].text}


def run_draft_reply(ticket: str, context: dict) -> dict:
    """Draft a reply based on gathered context."""
    classification = context.get("classify_ticket", {}).get("classification", "unknown")
    response = client.messages.create(
        model=MODEL,
        max_tokens=200,
        messages=[{
            "role": "user",
            "content": (
                f"Ticket: {ticket}\n"
                f"Classification: {classification}\n\n"
                "Draft a brief customer-facing reply (2-3 sentences)."
            ),
        }],
    )
    return {"status": "success", "step": "draft_reply", "reply": response.content[0].text}


STEP_RUNNERS = {
    "account_lookup":  run_account_lookup,
    "billing_lookup":  run_billing_lookup,
    "incident_lookup": run_incident_lookup,
    "classify_ticket": run_classify_ticket,
    "draft_reply":     run_draft_reply,
}


# ---------------------------------------------------------------------------
# Context health check
# ---------------------------------------------------------------------------

def check_context_health(context: dict) -> Optional[str]:
    """
    Before running a step, check if any required previous step has failed.
    Returns an error message if a required step failed, None if healthy.
    """
    for step_config in PIPELINE_STEPS:
        step_name = step_config["name"]
        if step_name not in context:
            continue
        result = context[step_name]
        if result.get("status") == "failed" and step_config["required"]:
            return f"Required step '{step_name}' failed: {result.get('reason')}"
    return None


# ---------------------------------------------------------------------------
# Pipeline runner
# ---------------------------------------------------------------------------

def run_pipeline(customer_id: str, ticket: str, failure_at: Optional[str] = None) -> dict:
    """
    Run the 5-step pipeline. If failure_at is set, that step will fail.

    Each step:
      1. Checks context health — if a required step failed, abort this step too
      2. Runs the step
      3. Appends result to context

    Returns the final context with all step results.
    """
    context = {}
    aborted = []

    for step_config in PIPELINE_STEPS:
        step_name = step_config["name"]

        # Health check before running
        health_error = check_context_health(context)
        if health_error:
            result = {
                "status": "aborted",
                "step": step_name,
                "reason": f"Context health check failed: {health_error}",
            }
            context[step_name] = result
            aborted.append(step_name)
            print(f"  {step_name:<20} ABORTED  ({result['reason'][:60]})")
            continue

        # Run the step
        scenario = "fail" if step_name == failure_at else "success"
        runner = STEP_RUNNERS[step_name]

        try:
            if step_name in ("classify_ticket", "draft_reply"):
                result = runner(ticket, context)
            else:
                result = runner(customer_id, scenario)
        except Exception as e:
            result = {"status": "failed", "step": step_name, "reason": str(e)}

        context[step_name] = result

        # Print step result
        status = result.get("status", "unknown")
        if status == "success":
            print(f"  {step_name:<20} OK")
        elif status == "failed":
            optional_label = " (optional)" if not step_config["required"] else " (required)"
            print(f"  {step_name:<20} FAILED{optional_label}  reason: {result.get('reason')}")
        else:
            print(f"  {step_name:<20} {status.upper()}")

    return context


# ---------------------------------------------------------------------------
# Test scenarios
# ---------------------------------------------------------------------------

TICKET = "I was charged twice this month and need a refund. Invoice INV-001 for €89."
CUSTOMER = "cust_001"

def main() -> None:
    print("=" * 65)
    print("Exercise 4: Error Propagation in Multi-Agent Chains")
    print("=" * 65)
    print()

    # Scenario A: no failures — happy path
    print("--- Scenario A: No failures ---")
    ctx_a = run_pipeline(CUSTOMER, TICKET, failure_at=None)
    reply = ctx_a.get("draft_reply", {}).get("reply", "(no reply)")
    print(f"  Final reply: \"{reply[:150]}\"")
    print()

    # Scenario B: required step fails (billing_lookup) — subsequent steps abort
    print("--- Scenario B: Required step fails (billing_lookup) ---")
    ctx_b = run_pipeline(CUSTOMER, TICKET, failure_at="billing_lookup")
    aborted = [s for s, v in ctx_b.items() if v.get("status") == "aborted"]
    print(f"  Aborted steps: {aborted}")
    reply = ctx_b.get("draft_reply", {}).get("reply", "(no reply — aborted)")
    print(f"  Final reply:   \"{reply[:150]}\"")
    print()

    # Scenario C: optional step fails (incident_lookup) — pipeline continues
    print("--- Scenario C: Optional step fails (incident_lookup) ---")
    ctx_c = run_pipeline(CUSTOMER, TICKET, failure_at="incident_lookup")
    aborted = [s for s, v in ctx_c.items() if v.get("status") == "aborted"]
    reply = ctx_c.get("draft_reply", {}).get("reply", "(no reply)")
    print(f"  Aborted steps: {aborted or '(none)'}")
    print(f"  Final reply:   \"{reply[:150]}\"")
    print()

    print("=" * 65)
    print("Key takeaway:")
    print("  A step that proceeds with a failed predecessor's output is")
    print("  worse than a step that aborts — it generates confident-sounding")
    print("  incorrect output.")
    print()
    print("  Required step fails  → all downstream steps abort")
    print("  Optional step fails  → pipeline continues, step is skipped")
    print()
    print("  The context health check runs BEFORE each step, not after.")
    print("  Aborting early is safer than propagating corrupt context.")
    print("=" * 65)


if __name__ == "__main__":
    main()

"""
exercise_2_three_response_shapes.py — The Three-Response-Shape Pattern

Every tool in Resolve returns exactly one of three shapes.
The agent checks status first — always — before inspecting the payload.

The three shapes:
  status="success"        — system responded, payload has meaningful data
  status="access_failure" — system was unavailable, do not proceed
  status="empty"          — system responded but found nothing

Why this matters:
  The original tool returned {} on CRM timeout. The model saw an empty dict,
  found no error key, and told customers their account was fine. 43 wrong replies.

The fix: every tool must return a status field. The model routes on status first.
"""

import json
import anthropic
from dotenv import load_dotenv

load_dotenv()

client = anthropic.Anthropic()
MODEL = "claude-haiku-4-5-20251001"


# ---------------------------------------------------------------------------
# Tool implementations — each returns one of the three shapes
# ---------------------------------------------------------------------------

def get_account_status(customer_id: str, scenario: str = "success") -> dict:
    """
    scenario: "success" | "empty" | "access_failure"
    In production, scenario is determined by the CRM's response.
    Here it is passed explicitly to demonstrate each shape.
    """
    if scenario == "access_failure":
        return {
            "status": "access_failure",
            "code": "CRM_TIMEOUT",
            "message": "CRM system did not respond. Do not proceed without account verification.",
        }
    if scenario == "empty":
        return {
            "status": "empty",
            "message": f"No CRM record found for {customer_id}. Ask customer to verify account email.",
        }
    return {
        "status": "success",
        "customer_id": customer_id,
        "plan": "Pro",
        "active": True,
        "auth_state": "verified",
    }


TOOL_REGISTRY = {"get_account_status": get_account_status}

TOOLS = [
    {
        "name": "get_account_status",
        "description": (
            "WHAT: Retrieves account status, plan, and auth state from the CRM.\n"
            "WHEN: Call this before any account-related action.\n"
            "SHAPES:\n"
            "  status=success: account found, payload has plan/active/auth_state.\n"
            "  status=empty: no CRM record. Ask the customer to verify their email.\n"
            "  status=access_failure: CRM unavailable. ESCALATE. Do not proceed.\n"
            "ON FAILURE: If access_failure, stop and escalate. Never tell a customer "
            "their account is fine when you could not verify it."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "customer_id": {"type": "string"},
                "scenario": {
                    "type": "string",
                    "enum": ["success", "empty", "access_failure"],
                    "description": "For demonstration: force a specific response shape.",
                },
            },
            "required": ["customer_id"],
        },
    }
]


# ---------------------------------------------------------------------------
# Demo tickets — one per shape
# ---------------------------------------------------------------------------

DEMO_TICKETS = [
    {
        "id": "demo_success",
        "customer_id": "cust_001",
        "scenario": "success",
        "text": "What plan am I on?",
        "expected": "Reply with account details.",
    },
    {
        "id": "demo_empty",
        "customer_id": "cust_unknown",
        "scenario": "empty",
        "text": "I need help with my account.",
        "expected": "Ask customer to verify account email.",
    },
    {
        "id": "demo_access_failure",
        "customer_id": "cust_001",
        "scenario": "access_failure",
        "text": "Is my account active?",
        "expected": "Escalate — cannot verify account.",
    },
]


# ---------------------------------------------------------------------------
# Agent runner
# ---------------------------------------------------------------------------

def run_demo(ticket: dict) -> dict:
    """Run a single agentic loop (max 4 iterations) for a demo ticket."""
    system = (
        "You are a Resolve support agent.\n\n"
        "ROUTING RULES:\n"
        "  status=success      → use the data, continue.\n"
        "  status=empty        → ask the customer for more information.\n"
        "  status=access_failure → STOP. Reply 'ESCALATE: <reason>' only.\n\n"
        "Never tell a customer their account is fine if you received access_failure."
    )
    messages = [{
        "role": "user",
        "content": (
            f"Ticket: {ticket['id']}  Customer: {ticket['customer_id']}\n"
            f"Scenario (demo): {ticket['scenario']}\n"
            f"Message: {ticket['text']}"
        ),
    }]

    reply = ""
    tool_calls = []

    for iteration in range(4):
        response = client.messages.create(
            model=MODEL, max_tokens=512, system=system, tools=TOOLS, messages=messages,
        )
        for block in response.content:
            if hasattr(block, "text"):
                reply = block.text
        if response.stop_reason == "end_turn":
            break
        if response.stop_reason != "tool_use":
            break

        results = []
        for block in response.content:
            if block.type != "tool_use":
                continue
            inp = block.input
            scenario_arg = inp.get("scenario", ticket["scenario"])
            customer_arg = inp.get("customer_id", ticket["customer_id"])
            result = TOOL_REGISTRY["get_account_status"](customer_arg, scenario_arg)
            tool_calls.append({"tool": block.name, "status": result["status"]})
            print(f"    [iter {iteration+1}] {block.name} → status={result['status']!r}")
            results.append({"type": "tool_result", "tool_use_id": block.id, "content": json.dumps(result)})

        messages.append({"role": "assistant", "content": response.content})
        messages.append({"role": "user", "content": results})

    return {
        "ticket_id": ticket["id"],
        "shape": ticket["scenario"],
        "escalated": "escalate" in reply.lower(),
        "reply": reply,
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    print("=" * 65)
    print("Exercise 2: The Three-Response-Shape Pattern")
    print("=" * 65)
    print()
    print("Three shapes: success / empty / access_failure")
    print("The agent routes on status first — always.")
    print()

    for ticket in DEMO_TICKETS:
        print(f"--- {ticket['id']} ---")
        print(f"  Scenario:  {ticket['scenario']}")
        print(f"  Message:   {ticket['text']}")
        print(f"  Expected:  {ticket['expected']}")
        print()

        result = run_demo(ticket)

        print(f"  Escalated: {result['escalated']}")
        print(f"  Reply:     {result['reply'][:150]}{'...' if len(result['reply']) > 150 else ''}")
        print()

    print("=" * 65)
    print("Routing rule:")
    print('  status="success"        → use the data, continue')
    print('  status="empty"          → handle the absence (ask or note "none found")')
    print('  status="access_failure" → stop, escalate, NEVER proceed')
    print()
    print("Why {} is dangerous:")
    print("  An empty dict has no status field. The model cannot route on it.")
    print("  It finds no error, assumes everything is fine, and proceeds.")
    print("  Result: 43 customers told their billing was fine during a CRM outage.")
    print("=" * 65)


if __name__ == "__main__":
    main()

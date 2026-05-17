"""
exercise_2_three_response_shapes.py — The Three-Response-Shape Pattern

Every integration tool in Resolve returns exactly one of three typed shapes.
The agent checks status first — always — before inspecting the payload.

The three shapes:
  SuccessResponse: status="success", data contains meaningful results
  AccessFailureResponse: status="access_failure", system was unavailable
  EmptyResultResponse: status="empty", system responded but found nothing

The model's routing is:
  success      → use the data, continue processing
  access_failure → stop, escalate, never proceed with stale or missing data
  empty         → handle the absence (ask for more info, or note "none found")

Why this matters: The original tool returned {} on CRM timeout. The model
saw an empty dict, found no error key, and proceeded as if the account was
clean. 43 customers received incorrect replies.
"""

import json
from dataclasses import dataclass
from typing import Optional, TypeVar, Generic
import anthropic
from dotenv import load_dotenv

load_dotenv()

client = anthropic.Anthropic()

MODEL = "claude-haiku-4-5-20251001"

T = TypeVar("T")

# ---------------------------------------------------------------------------
# Three typed response shapes
# ---------------------------------------------------------------------------

@dataclass
class SuccessResponse:
    status: str = "success"
    data: dict = None

    def __post_init__(self):
        if self.data is None:
            self.data = {}

    def to_dict(self) -> dict:
        return {"status": self.status, **self.data}


@dataclass
class AccessFailureResponse:
    code: str
    message: str
    status: str = "access_failure"

    def to_dict(self) -> dict:
        return {"status": self.status, "code": self.code, "message": self.message}


@dataclass
class EmptyResultResponse:
    message: str
    status: str = "empty"

    def to_dict(self) -> dict:
        return {"status": self.status, "message": self.message}


# ---------------------------------------------------------------------------
# The partial_result question (from Exercise 2 step 4)
# ---------------------------------------------------------------------------
#
# Some codebases define a fourth response type: partial_result.
# Example: a billing query that times out after returning 3 of 10 invoices.
#
# DECISION: Merge partial_result into access_failure.
#
# RATIONALE:
#   - Partial data is worse than no data for routing decisions. If the agent
#     acts on 3 of 10 invoices, it may miss the duplicate charge in invoice 7.
#   - The distinction between "no data" and "incomplete data" is rarely useful
#     to the model — both require the same action: escalate or retry.
#   - Adding a fourth shape increases the probability that the model encounters
#     a shape it handles incorrectly (the model has only 3 patterns to learn).
#
# EXCEPTION: If the system can reliably tell you which N% of the data is
# present, and the downstream action is safe on partial data, then partial_result
# is justified. In Resolve's billing system, this condition is never met.
#
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Tool implementations — each returning one of the three shapes
# ---------------------------------------------------------------------------

def get_account_status(customer_id: str, scenario: str = "success") -> dict:
    """
    scenario: "success" | "empty" | "access_failure"
    In production, scenario is determined by the CRM's response.
    Here it is passed explicitly to demonstrate each shape.
    """
    if scenario == "access_failure":
        return AccessFailureResponse(
            code="CRM_TIMEOUT",
            message="CRM system did not respond. Do not proceed without account verification.",
        ).to_dict()

    if scenario == "empty":
        return EmptyResultResponse(
            message=f"No CRM record found for customer_id={customer_id}. Ask customer to verify account email."
        ).to_dict()

    return SuccessResponse(data={
        "customer_id": customer_id,
        "plan": "Pro",
        "active": True,
        "auth_state": "verified",
    }).to_dict()


def get_billing_history(customer_id: str, scenario: str = "success") -> dict:
    """
    scenario: "success" | "empty" | "access_failure"
    In production, scenario is determined by the billing system's response.
    Here it is passed explicitly to demonstrate each shape.
    """
    if scenario == "access_failure":
        return AccessFailureResponse(
            code="BILLING_UNAVAILABLE",
            message="Billing system unavailable. Escalate to billing team.",
        ).to_dict()
    if scenario == "empty":
        return EmptyResultResponse(
            message="No billing records found. Customer has not been charged."
        ).to_dict()
    return SuccessResponse(data={
        "invoices": [{"amount": 49.0, "date": "2026-05-01", "id": "inv_001"}],
        "duplicate_charge": False,
    }).to_dict()


# ---------------------------------------------------------------------------
# Tool registry
# ---------------------------------------------------------------------------

TOOL_REGISTRY = {
    "get_account_status": get_account_status,
    "get_billing_history": get_billing_history,
}

# ---------------------------------------------------------------------------
# Tool schemas — include scenario parameter for demonstration
# ---------------------------------------------------------------------------

TOOLS = [
    {
        "name": "get_account_status",
        "description": (
            "WHAT: Retrieves the current account status, plan tier, and authentication "
            "state for a customer from the CRM system.\n"
            "WHEN: Call this first before any account-related action. Do NOT call this "
            "more than once per ticket — the result is cached for the session.\n"
            "SHAPES:\n"
            "  status=success: account found, payload contains plan, active, auth_state.\n"
            "  status=empty: no CRM record for this customer_id. Do NOT proceed — ask "
            "    the customer to verify their account email before continuing.\n"
            "  status=access_failure: CRM system is unavailable. ESCALATE immediately "
            "    to the technical team. Do NOT draft a reply without this data.\n"
            "ON FAILURE: If status is access_failure, stop and escalate. Never tell "
            "a customer their account is fine when you could not verify it."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "customer_id": {
                    "type": "string",
                    "description": "The customer account identifier."
                },
                "scenario": {
                    "type": "string",
                    "enum": ["success", "empty", "access_failure"],
                    "description": "For demonstration only: force a specific response shape."
                }
            },
            "required": ["customer_id"]
        }
    },
    {
        "name": "get_billing_history",
        "description": (
            "WHAT: Retrieves the most recent invoices and charge history for a customer.\n"
            "WHEN: Call this only for billing-related tickets. Requires get_account_status "
            "to have returned status=success first — billing data is unreliable without "
            "a confirmed account.\n"
            "SHAPES:\n"
            "  status=success: charges found, payload contains invoices list and "
            "    duplicate_charge flag.\n"
            "  status=empty: no billing records exist. Tell customer no charges are on file.\n"
            "  status=access_failure: billing system unavailable. Escalate to billing team.\n"
            "ON FAILURE: Do not tell a customer their billing is fine if you received "
            "access_failure. Escalate."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "customer_id": {
                    "type": "string",
                    "description": "The customer account identifier."
                },
                "scenario": {
                    "type": "string",
                    "enum": ["success", "empty", "access_failure"],
                    "description": "For demonstration only: force a specific response shape."
                }
            },
            "required": ["customer_id"]
        }
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
        "expected_action": "Reply with account details.",
    },
    {
        "id": "demo_empty",
        "customer_id": "cust_unknown",
        "scenario": "empty",
        "text": "I need help with my account.",
        "expected_action": "Ask customer to verify account email — no record found.",
    },
    {
        "id": "demo_access_failure",
        "customer_id": "cust_001",
        "scenario": "access_failure",
        "text": "Is my account active?",
        "expected_action": "Escalate — cannot verify account without CRM.",
    },
]

# ---------------------------------------------------------------------------
# Agent runner for demo tickets
# ---------------------------------------------------------------------------

def run_demo(ticket: dict) -> dict:
    """
    Run a single agentic loop (max 4 iterations) for a demo ticket.

    After each tool call, prints what the model does next based on the
    status it received.

    Returns a result dict with ticket_id, shape observed, final reply,
    tool_calls, and whether the agent escalated.
    """
    system_prompt = (
        "You are a Resolve support agent. Process the customer ticket using the tools "
        "provided. Always check account status first.\n\n"
        "ROUTING RULES — follow these exactly:\n"
        "  If tool returns status=success: use the data and continue.\n"
        "  If tool returns status=empty: handle the absence — ask the customer for "
        "more information or note that nothing was found.\n"
        "  If tool returns status=access_failure: STOP. Respond with "
        "'ESCALATE: <reason>' and do not proceed further.\n\n"
        "Never tell a customer their account or billing is fine if you received "
        "access_failure from the system."
    )

    messages = [
        {
            "role": "user",
            "content": (
                f"Ticket ID: {ticket['id']}\n"
                f"Customer ID: {ticket['customer_id']}\n"
                f"Scenario (for demo): {ticket['scenario']}\n"
                f"Message: {ticket['text']}"
            )
        }
    ]

    tool_calls_made = []
    reply_text = ""
    shape_observed = None
    exit_reason = "max_iterations"

    for iteration in range(4):
        response = client.messages.create(
            model=MODEL,
            max_tokens=512,
            system=system_prompt,
            tools=TOOLS,
            messages=messages,
        )

        # Collect any text from this response turn
        for block in response.content:
            if hasattr(block, "text"):
                reply_text = block.text

        if response.stop_reason == "end_turn":
            exit_reason = "end_turn"
            break

        if response.stop_reason != "tool_use":
            exit_reason = response.stop_reason
            break

        # Process tool calls
        tool_results = []
        for block in response.content:
            if block.type != "tool_use":
                continue

            tool_name = block.name
            tool_input = block.input
            scenario_arg = tool_input.get("scenario", ticket["scenario"])
            customer_id_arg = tool_input.get("customer_id", ticket["customer_id"])

            if tool_name in TOOL_REGISTRY:
                result = TOOL_REGISTRY[tool_name](
                    customer_id=customer_id_arg,
                    scenario=scenario_arg,
                )
            else:
                result = {
                    "status": "access_failure",
                    "code": "UNKNOWN_TOOL",
                    "message": f"Tool {tool_name} not found."
                }

            result_status = result.get("status", "unknown")
            if shape_observed is None:
                shape_observed = result_status

            tool_calls_made.append({
                "tool": tool_name,
                "result_status": result_status,
            })

            print(
                f"    [iter {iteration+1}] Tool '{tool_name}' returned "
                f"status='{result_status}'"
            )

            tool_results.append({
                "type": "tool_result",
                "tool_use_id": block.id,
                "content": json.dumps(result),
            })

        messages.append({"role": "assistant", "content": response.content})
        messages.append({"role": "user", "content": tool_results})

    escalated = "ESCALATE" in reply_text.upper() or "escalate" in reply_text.lower()

    return {
        "ticket_id": ticket["id"],
        "shape_observed": shape_observed,
        "exit_reason": exit_reason,
        "tool_calls": tool_calls_made,
        "reply": reply_text,
        "escalated": escalated,
    }

# ---------------------------------------------------------------------------
# Routing verifier
# ---------------------------------------------------------------------------

def verify_routing(result: dict, ticket: dict) -> bool:
    """
    Verify that the model routed correctly for each shape:

      success      → model produced a reply (not escalation)
      empty        → model asked for more information
                     (reply contains "verify", "email", or "account")
      access_failure → model escalated (reply contains "escalate")
    """
    scenario = ticket["scenario"]
    reply = result["reply"].lower()

    if scenario == "success":
        # Should reply with data, should NOT escalate
        return not result["escalated"]

    elif scenario == "empty":
        # Should ask for more info — look for keywords indicating the model
        # told the customer to verify their account or provide more details
        keywords = ["verify", "email", "account", "unable to find", "no record", "cannot find", "couldn't find"]
        return any(kw in reply for kw in keywords)

    elif scenario == "access_failure":
        # Should escalate
        return result["escalated"]

    return False

# ---------------------------------------------------------------------------
# Main — run all three demos and report
# ---------------------------------------------------------------------------

def main() -> None:
    print("=" * 70)
    print("EXERCISE 2: The Three-Response-Shape Pattern")
    print("=" * 70)
    print()
    print("Running three demo tickets — one per response shape...")
    print()

    results = []

    for ticket in DEMO_TICKETS:
        print(f"--- Ticket: {ticket['id']} ---")
        print(f"  Customer: {ticket['customer_id']}")
        print(f"  Scenario: {ticket['scenario']}")
        print(f"  Message:  {ticket['text']}")
        print(f"  Expected: {ticket['expected_action']}")
        print()

        result = run_demo(ticket)
        results.append((result, ticket))

        print(f"  Shape observed: {result['shape_observed']}")
        print(f"  Escalated:      {result['escalated']}")
        print(f"  Exit reason:    {result['exit_reason']}")
        print(f"  Model reply:    {result['reply'][:200]}{'...' if len(result['reply']) > 200 else ''}")
        print()

    # Routing verification summary
    print("=" * 70)
    print("ROUTING VERIFICATION")
    print("=" * 70)
    print(f"{'Ticket':<22} {'Shape':<16} {'Expected action':<42} {'Result':<6}")
    print("-" * 88)

    all_pass = True
    for result, ticket in results:
        passed = verify_routing(result, ticket)
        all_pass = all_pass and passed
        verdict = "PASS" if passed else "FAIL"
        shape = ticket["scenario"]
        expected = ticket["expected_action"]
        print(f"{ticket['id']:<22} {shape:<16} {expected:<42} {verdict:<6}")

    print()
    overall = "ALL PASS" if all_pass else "SOME FAILED"
    print(f"Overall: {overall}")
    print()

    # Boxed summary
    print("=" * 70)
    print("""
THE ROUTING RULE:
  status = "success"        → use the data, continue
  status = "empty"          → handle the absence (ask or note "none found")
  status = "access_failure" → stop, escalate, NEVER proceed

WHY {} IS DANGEROUS:
  An empty dict has no status field. The model cannot route on it.
  It inspects the payload, finds nothing alarming, and continues.
  Result: 43 customers told their billing was fine during a CRM outage.
""")
    print("=" * 70)


if __name__ == "__main__":
    main()

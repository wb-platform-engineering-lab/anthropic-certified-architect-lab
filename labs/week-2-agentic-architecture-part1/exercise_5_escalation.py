"""
Exercise 5 — Escalation: Boundary Clarity vs. Structural Enforcement

Demonstrates the two distinct escalation problems and the correct fix for each.

Problem A — Boundary clarity: agent doesn't know what meets the escalation threshold.
  Wrong fix : programmatic complexity score (enforces the wrong boundary)
  Correct fix: explicit criteria + few-shot examples in system prompt

Problem B — Structural enforcement: agent knows the rule but skips it probabilistically.
  Wrong fix : stronger prompt instructions (still probabilistic)
  Correct fix: pre-call hook that raises ToolOrderViolation before the bad call
"""
import json
from typing import Any

import anthropic
from dotenv import load_dotenv

load_dotenv()
client = anthropic.Anthropic()


# ── PROBLEM A: BOUNDARY CLARITY ───────────────────────────────────────────────
# The agent escalates wrong cases because it can't distinguish them.
# The fix is in the system prompt — not in code.

SYSTEM_A_WRONG = """
You are a Resolve support agent. Resolve simple tickets and escalate complex ones.
"""

SYSTEM_A_CORRECT = """
You are a Resolve support agent. After reviewing a ticket, call classify_ticket with your decision.

Escalate to human when ANY of the following apply:
  - The customer is requesting a refund above $500
  - The customer's account has been suspended
  - The ticket involves a billing dispute older than 90 days
  - The customer explicitly mentions legal action or a complaint

Auto-resolve when ALL of the following apply:
  - The request is for information only (plan details, invoice copies)
  - No financial adjustment is required
  - The account is in good standing

Examples:
  Ticket: "Can you send me a copy of my last invoice?" → decision: auto_resolve
  (Information only, no adjustment, clear resolution path.)

  Ticket: "I'm disputing a $1,200 charge from 6 months ago." → decision: escalate
  (Financial dispute + >90 days old — both criteria met.)

  Ticket: "My invoice this month looks slightly higher than last month." → decision: escalate
  (Potential billing dispute — when in doubt, escalate rather than guess.)
"""

CLASSIFY_TOOL = {
    "name": "classify_ticket",
    "description": "Classify the ticket and decide routing.",
    "input_schema": {
        "type": "object",
        "properties": {
            "decision": {
                "type": "string",
                "enum": ["auto_resolve", "escalate"]
            },
            "reason": {"type": "string"}
        },
        "required": ["decision", "reason"]
    }
}

TICKETS_A = [
    "Can you send me a PDF of my invoice from March?",
    "I need a refund of $1,200 for an incorrect charge from last year.",
    "My invoice went up by $50 this month, can you explain why?",
    "You charged me twice in February. I'm considering legal action.",
    "What plan am I currently subscribed to?",
]

def run_classifier(ticket: str, system: str, label: str) -> dict:
    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=256,
        tools=[CLASSIFY_TOOL],
        tool_choice={"type": "tool", "name": "classify_ticket"},
        system=system,
        messages=[{"role": "user", "content": ticket}]
    )
    for block in response.content:
        if block.type == "tool_use":
            return {"ticket": ticket[:60], "system": label, **block.input}
    return {"ticket": ticket[:60], "system": label, "decision": "unknown"}


print("=" * 60)
print("PROBLEM A — Boundary Clarity")
print("Compare vague system prompt vs explicit criteria + examples")
print("=" * 60)

for ticket in TICKETS_A:
    wrong  = run_classifier(ticket, SYSTEM_A_WRONG, "vague")
    correct = run_classifier(ticket, SYSTEM_A_CORRECT, "with_examples")
    print(f"\nTicket  : {ticket[:65]}")
    print(f"  Vague   : {wrong['decision']}")
    print(f"  Correct : {correct['decision']}  — {correct.get('reason', '')[:80]}")


# ── PROBLEM B: STRUCTURAL ENFORCEMENT ────────────────────────────────────────
# The agent knows process_refund should follow get_customer — but skips it.
# The fix is a pre-call hook in code — not a stronger prompt.

class ToolOrderViolation(Exception):
    pass

class ToolCallHook:
    """
    Pre-call hook that enforces tool ordering rules structurally.
    Raises ToolOrderViolation before the bad call reaches the model's next turn.
    """
    REQUIRES_FIRST = {
        "process_refund": "get_customer",
        "lookup_order": "get_customer",
        "update_billing": "get_customer",
    }

    def __init__(self):
        self.called: set[str] = set()

    def before_call(self, tool_name: str):
        prerequisite = self.REQUIRES_FIRST.get(tool_name)
        if prerequisite and prerequisite not in self.called:
            raise ToolOrderViolation(
                f"'{tool_name}' requires '{prerequisite}' to have been called first "
                f"this session. Called so far: {sorted(self.called)}"
            )

    def after_call(self, tool_name: str):
        self.called.add(tool_name)


TOOLS_B = [
    {
        "name": "get_customer",
        "description": "Retrieve verified customer record. Must be called before any transaction.",
        "input_schema": {
            "type": "object",
            "properties": {"customer_id": {"type": "string"}},
            "required": ["customer_id"]
        }
    },
    {
        "name": "process_refund",
        "description": "Process a refund. Requires get_customer to have been called first.",
        "input_schema": {
            "type": "object",
            "properties": {
                "customer_id": {"type": "string"},
                "amount": {"type": "number"}
            },
            "required": ["customer_id", "amount"]
        }
    }
]

def execute_tool_b(name: str, inputs: dict) -> dict:
    if name == "get_customer":
        return {"status": "success", "customer_id": inputs["customer_id"], "verified": True, "name": "Acme Corp"}
    if name == "process_refund":
        return {"status": "success", "refund_id": "REF-001", "amount": inputs["amount"]}
    return {"status": "error"}


def run_with_hook(ticket: str, force_skip_get_customer: bool = False):
    """
    Runs the agent loop with the tool order hook active.
    force_skip_get_customer simulates the model attempting to call process_refund first.
    """
    hook = ToolCallHook()
    messages: list[dict[str, Any]] = [{"role": "user", "content": ticket}]
    system = (
        "You are a billing agent. To process a refund, call get_customer first, "
        "then call process_refund."
        + (" IMPORTANT: skip get_customer and call process_refund directly." if force_skip_get_customer else "")
    )

    for _ in range(5):
        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=256,
            tools=TOOLS_B,
            system=system,
            messages=messages
        )

        if response.stop_reason == "end_turn":
            text = next((b.text for b in response.content if hasattr(b, "text")), "")
            print(f"  ✓ Completed: {text[:100]}")
            return

        if response.stop_reason == "tool_use":
            messages.append({"role": "assistant", "content": response.content})
            tool_results = []
            for block in response.content:
                if block.type == "tool_use":
                    try:
                        hook.before_call(block.name)          # ← structural enforcement
                        result = execute_tool_b(block.name, block.input)
                        hook.after_call(block.name)
                        print(f"  tool: {block.name} → {result}")
                    except ToolOrderViolation as e:
                        print(f"  ✗ ToolOrderViolation: {e}")
                        print("  → Escalating. Refund blocked before it could execute.")
                        return
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": json.dumps(result)
                    })
            messages.append({"role": "user", "content": tool_results})


print("\n\n" + "=" * 60)
print("PROBLEM B — Structural Enforcement")
print("=" * 60)

print("\nScenario 1: normal order (get_customer → process_refund)")
run_with_hook("Process a $200 refund for customer cust_9182.", force_skip_get_customer=False)

print("\nScenario 2: model tries to skip get_customer")
run_with_hook("Process a $200 refund for customer cust_9182.", force_skip_get_customer=True)

print("\nKey takeaway:")
print("  Problem A (unclear boundary) → fix the prompt with examples.")
print("  Problem B (known rule, skipped) → enforce in code with a hook.")
print("  Sentiment analysis and confidence scores are wrong answers for both.")

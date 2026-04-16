"""
Exercise 4 — Task Decomposition

Breaks a complex ticket into four bounded sub-tasks, each with a completion
criterion evaluated in code — not by asking the model.

Sub-task chain:
  1. verify_account    → confirms account_id + plan
  2. check_incidents   → returns incident list (may be empty)
  3. lookup_billing    → returns invoice history
  4. draft_resolution  → produces the reply text

If any sub-task fails, the chain short-circuits to escalation.
Sub-tasks 3 and 4 do not run if sub-task 2 fails.
"""
import json
from dataclasses import dataclass
from typing import Any

import anthropic
from dotenv import load_dotenv

load_dotenv()
client = anthropic.Anthropic()


@dataclass
class SubTaskResult:
    name: str
    status: str          # success | failed | escalated
    output: dict
    iterations: int


def run_subtask(
    name: str,
    system: str,
    user_message: str,
    tools: list[dict],
    tool_fn,
    completion_check,   # callable(output: dict) -> bool
    max_iterations: int = 4
) -> SubTaskResult:
    """
    Generic sub-task runner. Terminates when:
      - completion_check(output) is True
      - stop_reason is end_turn
      - budget is exhausted (→ escalated)
    """
    messages: list[dict[str, Any]] = [{"role": "user", "content": user_message}]
    output: dict = {}

    for iteration in range(1, max_iterations + 1):
        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=256,
            tools=tools,
            system=system,
            messages=messages
        )

        if response.stop_reason == "end_turn":
            text = next((b.text for b in response.content if hasattr(b, "text")), "")
            output["text"] = text
            if completion_check(output):
                return SubTaskResult(name=name, status="success", output=output, iterations=iteration)
            return SubTaskResult(name=name, status="failed", output=output, iterations=iteration)

        elif response.stop_reason == "tool_use":
            messages.append({"role": "assistant", "content": response.content})
            tool_results = []
            for block in response.content:
                if block.type == "tool_use":
                    result = tool_fn(block.name, block.input)
                    output.update(result)  # accumulate tool output
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": json.dumps(result)
                    })
            messages.append({"role": "user", "content": tool_results})

        else:
            return SubTaskResult(name=name, status="failed", output=output, iterations=iteration)

    return SubTaskResult(name=name, status="escalated", output=output, iterations=max_iterations)


# ── SUB-TASK DEFINITIONS ──────────────────────────────────────────────────────

def tool_verify_account(name: str, inputs: dict) -> dict:
    if name == "get_account_status":
        return {
            "status": "success",
            "customer_id": inputs.get("customer_id"),
            "plan": "enterprise",
            "account_status": "active"
        }
    return {"status": "error"}

def tool_check_incidents(name: str, inputs: dict) -> dict:
    if name == "check_known_incidents":
        return {"status": "success", "active_incidents": []}
    return {"status": "error"}

def tool_lookup_billing(name: str, inputs: dict) -> dict:
    if name == "list_invoices":
        return {"status": "success", "invoices": ["INV-001", "INV-002"]}
    if name == "get_invoice_detail":
        return {
            "status": "success",
            "invoice_id": inputs.get("invoice_id"),
            "amount": 4200.00,
            "paid": False
        }
    return {"status": "error"}


def run_ticket(customer_id: str, ticket_text: str):
    print(f"\n{'='*60}")
    print(f"Ticket: {ticket_text[:80]}")
    print(f"Customer: {customer_id}")

    results: list[SubTaskResult] = []

    # ── Sub-task 1: Verify account ────────────────────────────────────────────
    print("\n[Sub-task 1] verify_account")
    r1 = run_subtask(
        name="verify_account",
        system="Your only job is to call get_account_status for the customer and confirm their plan.",
        user_message=f"Verify account for customer ID {customer_id}.",
        tools=[{
            "name": "get_account_status",
            "description": "Get account status from CRM.",
            "input_schema": {
                "type": "object",
                "properties": {"customer_id": {"type": "string"}},
                "required": ["customer_id"]
            }
        }],
        tool_fn=tool_verify_account,
        # Completion criterion: status and plan must be present in output
        completion_check=lambda o: o.get("status") == "success" and "plan" in o
    )
    results.append(r1)
    print(f"  status={r1.status}, output={r1.output}")

    if r1.status != "success":
        print("\n✗ Sub-task 1 failed — escalating. Sub-tasks 2–4 will NOT run.")
        return {"status": "escalated", "failed_at": "verify_account", "results": results}

    # ── Sub-task 2: Check incidents ───────────────────────────────────────────
    print("\n[Sub-task 2] check_incidents")
    r2 = run_subtask(
        name="check_incidents",
        system="Your only job is to check for known billing incidents.",
        user_message="Are there any active billing incidents right now?",
        tools=[{
            "name": "check_known_incidents",
            "description": "Returns list of active billing incidents.",
            "input_schema": {"type": "object", "properties": {}, "required": []}
        }],
        tool_fn=tool_check_incidents,
        completion_check=lambda o: "active_incidents" in o
    )
    results.append(r2)
    print(f"  status={r2.status}, incidents={r2.output.get('active_incidents', [])}")

    if r2.status != "success":
        print("\n✗ Sub-task 2 failed — escalating. Sub-tasks 3–4 will NOT run.")
        return {"status": "escalated", "failed_at": "check_incidents", "results": results}

    # ── Sub-task 3: Lookup billing ────────────────────────────────────────────
    print("\n[Sub-task 3] lookup_billing")
    r3 = run_subtask(
        name="lookup_billing",
        system="Your only job is to retrieve the customer's invoice list.",
        user_message=f"List all invoices for customer ID {customer_id}.",
        tools=[
            {
                "name": "list_invoices",
                "description": "List all invoices for a customer.",
                "input_schema": {
                    "type": "object",
                    "properties": {"customer_id": {"type": "string"}},
                    "required": ["customer_id"]
                }
            }
        ],
        tool_fn=tool_lookup_billing,
        completion_check=lambda o: "invoices" in o
    )
    results.append(r3)
    print(f"  status={r3.status}, invoices={r3.output.get('invoices', [])}")

    if r3.status != "success":
        print("\n✗ Sub-task 3 failed — escalating. Sub-task 4 will NOT run.")
        return {"status": "escalated", "failed_at": "lookup_billing", "results": results}

    # ── Sub-task 4: Draft resolution ──────────────────────────────────────────
    print("\n[Sub-task 4] draft_resolution")
    context = (
        f"Account plan: {r1.output.get('plan')}\n"
        f"Active incidents: {r2.output.get('active_incidents')}\n"
        f"Invoices: {r3.output.get('invoices')}\n"
        f"Original ticket: {ticket_text}"
    )
    r4 = run_subtask(
        name="draft_resolution",
        system="Draft a concise, accurate reply to the customer based on the verified facts provided.",
        user_message=context,
        tools=[],  # no tools needed — this sub-task only generates text
        tool_fn=lambda n, i: {},
        completion_check=lambda o: bool(o.get("text", "").strip())
    )
    results.append(r4)
    print(f"  status={r4.status}")

    if r4.status == "success":
        print(f"\n✓ Resolution drafted:\n{r4.output.get('text', '')[:300]}")
        return {"status": "success", "reply": r4.output.get("text"), "results": results}

    return {"status": "escalated", "failed_at": "draft_resolution", "results": results}


# ── DEMO ──────────────────────────────────────────────────────────────────────
run_ticket(
    customer_id="cust_9182",
    ticket_text=(
        "I was charged $4,200 this month but I'm on the enterprise plan. "
        "Invoice INV-001 looks wrong. Can you investigate?"
    )
)

print("\nKey takeaway:")
print("  Completion criteria are evaluated in code — not by the model.")
print("  Sub-task failure short-circuits the chain immediately.")
print("  Each sub-task has a single, bounded responsibility.")

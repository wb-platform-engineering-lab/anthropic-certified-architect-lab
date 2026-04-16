"""
Exercise 3 — Modelling Session State

Session state is an explicit data structure maintained in code alongside the
messages array. It tracks what has been established — not just what was said.

Key behaviours demonstrated:
  - Redundant tool calls are blocked using state.tools_called
  - Confirmed facts persist across the loop and are returned in the audit log
  - State is serialised to JSON at the end of each session
"""
import json
from dataclasses import dataclass, field
from typing import Any

import anthropic
from dotenv import load_dotenv

load_dotenv()
client = anthropic.Anthropic()


@dataclass
class SessionState:
    ticket_id: str
    tools_called: set = field(default_factory=set)        # names of tools already executed
    confirmed_facts: dict = field(default_factory=dict)   # key facts extracted from tool results
    current_decision: str | None = None                   # latest routing decision
    iteration_count: int = 0

    def record_tool(self, name: str, result: dict):
        self.tools_called.add(name)
        # Extract salient facts by tool name
        if name == "get_account_status" and result.get("status") == "success":
            self.confirmed_facts["plan"] = result.get("plan")
            self.confirmed_facts["open_invoices"] = result.get("open_invoices")
            self.confirmed_facts["account_verified"] = True
        elif name == "lookup_invoice" and result.get("status") == "success":
            self.confirmed_facts[f"invoice_{result['invoice_id']}"] = {
                "amount": result.get("amount"),
                "paid": result.get("paid")
            }
        elif name == "check_known_incidents":
            self.confirmed_facts["incidents_checked"] = True
            self.confirmed_facts["active_incidents"] = result.get("active_incidents", [])

    def to_dict(self) -> dict:
        return {
            "ticket_id": self.ticket_id,
            "tools_called": sorted(self.tools_called),
            "confirmed_facts": self.confirmed_facts,
            "current_decision": self.current_decision,
            "iteration_count": self.iteration_count
        }


TOOLS = [
    {
        "name": "get_account_status",
        "description": "Get account status. Call once per session — result is cached in state.",
        "input_schema": {
            "type": "object",
            "properties": {"customer_id": {"type": "string"}},
            "required": ["customer_id"]
        }
    },
    {
        "name": "lookup_invoice",
        "description": "Look up a specific invoice by ID.",
        "input_schema": {
            "type": "object",
            "properties": {"invoice_id": {"type": "string"}},
            "required": ["invoice_id"]
        }
    },
    {
        "name": "check_known_incidents",
        "description": "Check for active billing incidents. Call once per session.",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": []
        }
    }
]


def execute_tool(name: str, inputs: dict, state: SessionState) -> dict:
    """
    Execute a tool, guarding against redundant calls for idempotent lookups.
    Returns a cached-result notice if the tool was already called this session.
    """
    # Guard: block redundant calls for tools that should only run once
    if name in ("get_account_status", "check_known_incidents") and name in state.tools_called:
        print(f"  [guard] {name} already called this session — returning cached facts")
        return {
            "status": "cached",
            "message": f"{name} was already called this session. Use the confirmed facts.",
            "confirmed_facts": state.confirmed_facts
        }

    print(f"  [tool]  {name}({inputs})")

    if name == "get_account_status":
        return {"status": "success", "plan": "enterprise", "open_invoices": 2}
    if name == "lookup_invoice":
        return {
            "status": "success",
            "invoice_id": inputs.get("invoice_id", "INV-001"),
            "amount": 4200.00,
            "paid": False,
            "due_date": "2026-05-01"
        }
    if name == "check_known_incidents":
        return {"status": "success", "active_incidents": []}
    return {"status": "error", "message": f"Unknown tool: {name}"}


def run_session(ticket_id: str, ticket_text: str, max_iterations: int = 8) -> dict:
    state = SessionState(ticket_id=ticket_id)
    messages: list[dict[str, Any]] = [{"role": "user", "content": ticket_text}]

    print(f"\n{'='*60}")
    print(f"Ticket {ticket_id}: {ticket_text[:80]}...")

    for _ in range(max_iterations):
        state.iteration_count += 1
        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=512,
            tools=TOOLS,
            system=(
                "You are a Resolve support agent. "
                "Check account status and incidents before responding to billing questions. "
                "Do not call the same lookup tool twice."
            ),
            messages=messages
        )

        if response.stop_reason == "end_turn":
            text = next((b.text for b in response.content if hasattr(b, "text")), "")
            state.current_decision = "resolved"
            break

        elif response.stop_reason == "tool_use":
            messages.append({"role": "assistant", "content": response.content})
            tool_results = []
            for block in response.content:
                if block.type == "tool_use":
                    result = execute_tool(block.name, block.input, state)
                    state.record_tool(block.name, result)  # update state BEFORE next call
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": json.dumps(result)
                    })
            messages.append({"role": "user", "content": tool_results})

        else:
            state.current_decision = "escalated"
            break
    else:
        state.current_decision = "budget_exhausted"

    audit = state.to_dict()
    print(f"\nSession audit log:")
    print(json.dumps(audit, indent=2))
    return audit


# ── DEMO ──────────────────────────────────────────────────────────────────────
# This ticket is designed to make the model want to call get_account_status twice
# The state guard will intercept the second call.
run_session(
    ticket_id="TKT-0042",
    ticket_text=(
        "Hi, I have an issue with invoice INV-0042. My account balance seems wrong. "
        "Can you check my account status and then look at the invoice? "
        "Also double-check my account status one more time to be sure. "
        "Customer ID is cust_9182."
    )
)

print("\nKey takeaway:")
print("  state.tools_called prevents duplicate CRM calls in the same session.")
print("  confirmed_facts accumulates verified data across all iterations.")
print("  The audit log is serialisable — ready for compliance storage.")

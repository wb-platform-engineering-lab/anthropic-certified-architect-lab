"""
Exercise 2 — The Iteration Budget

Implements a principled iteration budget with three distinct exit paths:
  - success         → model reached end_turn within budget
  - budget_exhausted → iteration limit hit before completion
  - truncated        → max_tokens fired (output incomplete)
  - error            → unexpected stop_reason

The exit status is a typed dataclass, not a string. The caller branches on it.
"""
import json
import logging
from dataclasses import dataclass, field
from typing import Any

import anthropic
from dotenv import load_dotenv

load_dotenv()
client = anthropic.Anthropic()

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger(__name__)


@dataclass
class LoopResult:
    status: str          # success | budget_exhausted | truncated | error
    reply: str = ""
    iteration_count: int = 0
    tools_called: list[dict] = field(default_factory=list)
    stop_reason: str = ""

    def __str__(self):
        base = f"status={self.status}, iterations={self.iteration_count}"
        if self.reply:
            base += f", reply_length={len(self.reply)}"
        if self.tools_called:
            names = [t["name"] for t in self.tools_called]
            base += f", tools={names}"
        return base


TOOLS = [
    {
        "name": "get_account_status",
        "description": "Get account and billing status from CRM.",
        "input_schema": {
            "type": "object",
            "properties": {"customer_id": {"type": "string"}},
            "required": ["customer_id"]
        }
    },
    {
        "name": "list_invoices",
        "description": "List all invoices for a customer.",
        "input_schema": {
            "type": "object",
            "properties": {"customer_id": {"type": "string"}},
            "required": ["customer_id"]
        }
    },
    {
        "name": "get_invoice_detail",
        "description": "Get full detail of a specific invoice.",
        "input_schema": {
            "type": "object",
            "properties": {"invoice_id": {"type": "string"}},
            "required": ["invoice_id"]
        }
    },
    {
        "name": "check_known_incidents",
        "description": "Check if there are any known billing system incidents.",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": []
        }
    }
]

# Simulated tool responses keyed by call count to vary depth
_call_counts: dict[str, int] = {}

def execute_tool(name: str, inputs: dict) -> dict:
    _call_counts[name] = _call_counts.get(name, 0) + 1
    if name == "get_account_status":
        return {"status": "success", "plan": "enterprise", "open_invoices": 3}
    if name == "list_invoices":
        return {"status": "success", "invoices": ["INV-001", "INV-002", "INV-003"]}
    if name == "get_invoice_detail":
        iid = inputs.get("invoice_id", "INV-001")
        return {"status": "success", "invoice_id": iid, "amount": 1400.00, "paid": False}
    if name == "check_known_incidents":
        return {"status": "success", "active_incidents": []}
    return {"status": "error", "message": f"Unknown tool: {name}"}


def run_agent(ticket: str, max_iterations: int = 5) -> LoopResult:
    """
    Agentic loop with explicit iteration budget.
    Returns a LoopResult — never raises on expected failure modes.
    """
    global _call_counts
    _call_counts = {}

    messages: list[dict[str, Any]] = [{"role": "user", "content": ticket}]
    tools_called: list[dict] = []

    for iteration in range(1, max_iterations + 1):
        log.info(f"  [iter {iteration}/{max_iterations}]")

        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=512,
            tools=TOOLS,
            system=(
                "You are a Resolve support agent. "
                "Always verify account status and invoice details before responding to billing questions. "
                "Check for known incidents if the customer reports unexpected charges."
            ),
            messages=messages
        )

        log.info(f"  stop_reason: {response.stop_reason}")

        if response.stop_reason == "end_turn":
            text = next((b.text for b in response.content if hasattr(b, "text")), "")
            return LoopResult(
                status="success",
                reply=text,
                iteration_count=iteration,
                tools_called=tools_called,
                stop_reason="end_turn"
            )

        elif response.stop_reason == "tool_use":
            messages.append({"role": "assistant", "content": response.content})
            tool_results = []

            for block in response.content:
                if block.type == "tool_use":
                    log.info(f"  tool: {block.name}({block.input})")
                    result = execute_tool(block.name, block.input)
                    log.info(f"  result: {result}")
                    tools_called.append({"name": block.name, "input": block.input})
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": json.dumps(result)
                    })

            messages.append({"role": "user", "content": tool_results})

            # Budget check after tool execution — not before
            if iteration == max_iterations:
                return LoopResult(
                    status="budget_exhausted",
                    iteration_count=iteration,
                    tools_called=tools_called,
                    stop_reason="tool_use"
                )

        elif response.stop_reason == "max_tokens":
            return LoopResult(
                status="truncated",
                iteration_count=iteration,
                tools_called=tools_called,
                stop_reason="max_tokens"
            )

        else:
            return LoopResult(
                status="error",
                iteration_count=iteration,
                tools_called=tools_called,
                stop_reason=response.stop_reason
            )

    # Should not be reached, but is a valid defensive path
    return LoopResult(status="budget_exhausted", iteration_count=max_iterations)


# ── DEMO ──────────────────────────────────────────────────────────────────────
scenarios = [
    {
        "label": "Simple question (expect 1 tool call)",
        "ticket": "What plan am I on? Customer ID: cust_0001.",
        "max_iterations": 5
    },
    {
        "label": "Billing dispute (expect ~3 tool calls)",
        "ticket": (
            "I was charged $4,200 last month but my plan is starter. "
            "My customer ID is cust_9182. Invoice INV-001 looks wrong."
        ),
        "max_iterations": 5
    },
    {
        "label": "Tight budget — forces budget_exhausted",
        "ticket": (
            "I was charged $4,200 last month but my plan is starter. "
            "Customer ID cust_9182. Please check my account, all invoices, "
            "invoice detail, and any known incidents."
        ),
        "max_iterations": 2   # deliberately too low
    }
]

for s in scenarios:
    print(f"\n{'='*60}")
    print(f"Scenario : {s['label']}")
    print(f"Budget   : {s['max_iterations']} iterations")
    result = run_agent(s["ticket"], max_iterations=s["max_iterations"])
    print(f"\nResult   : {result}")
    if result.status == "success":
        print(f"Reply    : {result.reply[:150]}...")
    elif result.status == "budget_exhausted":
        print("Action   : escalate to human — loop hit budget before completing")
    elif result.status == "truncated":
        print("Action   : escalate — output was cut short, do not present as answer")

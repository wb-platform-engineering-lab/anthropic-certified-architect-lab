"""
Exercise 1 — Coordinator / Subagent Architecture

Builds a two-level agent hierarchy for the Resolve support product:

  Coordinator
    ├── AccountAgent   (get_account_status)
    ├── BillingAgent   (list_invoices, get_invoice_detail)
    ├── IncidentAgent  (check_status_page)
    └── DraftAgent     (text only — no tools)

Key design rules demonstrated:
  - Each subagent runs its OWN isolated agentic loop
  - Subagents see ONLY their task string — never the coordinator's history
  - The coordinator collects typed SubAgentResult dataclasses, not plain strings
  - Critical agent failure (Account or Billing) triggers immediate escalation
  - The DraftAgent receives only assembled facts, not raw API responses
"""
import json
from dataclasses import dataclass
from typing import Any, Optional

import anthropic
from dotenv import load_dotenv

load_dotenv()
client = anthropic.Anthropic()


# ── TYPED RESULT ──────────────────────────────────────────────────────────────

@dataclass
class SubAgentResult:
    agent: str
    status: str          # success | failed | escalated | budget_exhausted
    output: dict
    iterations: int

    def __str__(self):
        return (
            f"SubAgentResult(agent={self.agent}, status={self.status}, "
            f"iterations={self.iterations}, output_keys={list(self.output.keys())})"
        )


# ── GENERIC SUBAGENT RUNNER ───────────────────────────────────────────────────

def run_subagent(
    name: str,
    system: str,
    task: str,
    tools: list[dict],
    tool_fn,
    max_iterations: int = 5
) -> SubAgentResult:
    """
    Runs a bounded agentic loop for a single specialist agent.

    The subagent sees ONLY the task string — it has no access to the
    coordinator's message history or any other agent's output.

    Returns a SubAgentResult — never raises on expected failure modes.
    """
    # Fresh, isolated message history for this subagent
    messages: list[dict[str, Any]] = [{"role": "user", "content": task}]
    accumulated_output: dict = {}

    print(f"\n  [{name}] starting (max_iterations={max_iterations})")

    for iteration in range(1, max_iterations + 1):
        print(f"  [{name}] iteration {iteration}")

        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=512,
            tools=tools,
            system=system,
            messages=messages
        )

        print(f"  [{name}] stop_reason={response.stop_reason}")

        if response.stop_reason == "end_turn":
            # Model finished — extract any text reply
            text = next((b.text for b in response.content if hasattr(b, "text")), "")
            accumulated_output["reply"] = text
            return SubAgentResult(
                agent=name,
                status="success",
                output=accumulated_output,
                iterations=iteration
            )

        elif response.stop_reason == "tool_use":
            messages.append({"role": "assistant", "content": response.content})
            tool_results = []

            for block in response.content:
                if block.type == "tool_use":
                    print(f"  [{name}] tool call: {block.name}({block.input})")
                    result = tool_fn(block.name, block.input)
                    print(f"  [{name}] tool result: {result}")
                    # Accumulate tool results into output dict
                    accumulated_output.update(result)
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": json.dumps(result)
                    })

            messages.append({"role": "user", "content": tool_results})

        elif response.stop_reason == "max_tokens":
            # Output was cut off — treat as failure, not success
            return SubAgentResult(
                agent=name,
                status="failed",
                output={"error": "output truncated"},
                iterations=iteration
            )

        else:
            # Unknown stop_reason — fail safe
            return SubAgentResult(
                agent=name,
                status="failed",
                output={"error": f"unexpected stop_reason={response.stop_reason}"},
                iterations=iteration
            )

    # Iteration budget exhausted before end_turn
    return SubAgentResult(
        agent=name,
        status="budget_exhausted",
        output=accumulated_output,
        iterations=max_iterations
    )


# ── TOOL DEFINITIONS ──────────────────────────────────────────────────────────

ACCOUNT_TOOLS = [
    {
        "name": "get_account_status",
        "description": "Retrieve account and plan status from the CRM.",
        "input_schema": {
            "type": "object",
            "properties": {"customer_id": {"type": "string"}},
            "required": ["customer_id"]
        }
    }
]

BILLING_TOOLS = [
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
        "description": "Get full detail for a specific invoice.",
        "input_schema": {
            "type": "object",
            "properties": {"invoice_id": {"type": "string"}},
            "required": ["invoice_id"]
        }
    }
]

INCIDENT_TOOLS = [
    {
        "name": "check_status_page",
        "description": "Check the public status page for active incidents.",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": []
        }
    }
]


# ── SIMULATED TOOL HANDLERS ───────────────────────────────────────────────────

def account_tool_fn(name: str, inputs: dict) -> dict:
    if name == "get_account_status":
        return {
            "status": "success",
            "customer_id": inputs.get("customer_id"),
            "plan": "enterprise",
            "account_status": "active",
            "open_invoices": 2
        }
    return {"status": "error", "message": f"Unknown tool: {name}"}


def billing_tool_fn(name: str, inputs: dict) -> dict:
    if name == "list_invoices":
        return {
            "status": "success",
            "customer_id": inputs.get("customer_id"),
            "invoices": ["INV-2026-0041", "INV-2026-0042"]
        }
    if name == "get_invoice_detail":
        iid = inputs.get("invoice_id", "INV-2026-0042")
        return {
            "status": "success",
            "invoice_id": iid,
            "amount": 4200.00,
            "currency": "USD",
            "paid": False,
            "due_date": "2026-05-01",
            "line_items": [
                {"description": "Enterprise Plan — Annual", "amount": 4200.00}
            ]
        }
    return {"status": "error", "message": f"Unknown tool: {name}"}


def incident_tool_fn(name: str, inputs: dict) -> dict:
    if name == "check_status_page":
        return {
            "status": "success",
            "overall_status": "operational",
            "active_incidents": []
        }
    return {"status": "error", "message": f"Unknown tool: {name}"}


# ── COORDINATOR ───────────────────────────────────────────────────────────────

def run_coordinator(ticket: str, customer_id: str) -> dict:
    """
    Two-level coordinator that:
      1. Dispatches to three specialist subagents sequentially
      2. Escalates immediately if any critical agent fails
      3. Passes collected facts to a DraftAgent for the final reply

    Returns a typed dict with status, reply, and agent_results.
    """
    print(f"\n{'='*60}")
    print(f"COORDINATOR — ticket: {ticket[:80]}")
    print(f"Customer: {customer_id}")
    print("=" * 60)

    agent_results: list[SubAgentResult] = []

    # ── Step 1: AccountAgent ──────────────────────────────────────────────────
    print("\n[Step 1] Dispatching AccountAgent")
    account_result = run_subagent(
        name="AccountAgent",
        system=(
            "You are the account specialist for Resolve support. "
            "Your only job is to retrieve the account status for the given customer ID. "
            "Call get_account_status and confirm the plan and account standing."
        ),
        task=f"Retrieve account status for customer ID {customer_id}.",
        tools=ACCOUNT_TOOLS,
        tool_fn=account_tool_fn,
        max_iterations=4
    )
    agent_results.append(account_result)
    print(f"  AccountAgent result: {account_result}")

    # Critical agent failure → escalate immediately, skip remaining agents
    if account_result.status != "success":
        print("\n  ESCALATING: AccountAgent failed — cannot proceed without account data.")
        return {
            "status": "escalated",
            "reason": "AccountAgent failed",
            "reply": None,
            "agent_results": agent_results
        }

    # ── Step 2: BillingAgent ──────────────────────────────────────────────────
    print("\n[Step 2] Dispatching BillingAgent")
    billing_result = run_subagent(
        name="BillingAgent",
        system=(
            "You are the billing specialist for Resolve support. "
            "Your job is to retrieve the customer's invoice list and, if relevant, "
            "get detail on specific invoices. Confirm amounts and payment status."
        ),
        task=(
            f"List all invoices for customer ID {customer_id}. "
            "Then retrieve the detail for the most recent invoice."
        ),
        tools=BILLING_TOOLS,
        tool_fn=billing_tool_fn,
        max_iterations=5
    )
    agent_results.append(billing_result)
    print(f"  BillingAgent result: {billing_result}")

    # Critical agent failure → escalate immediately
    if billing_result.status != "success":
        print("\n  ESCALATING: BillingAgent failed — cannot proceed without billing data.")
        return {
            "status": "escalated",
            "reason": "BillingAgent failed",
            "reply": None,
            "agent_results": agent_results
        }

    # ── Step 3: IncidentAgent ─────────────────────────────────────────────────
    # Non-critical: failure here is tolerated — we proceed with a note
    print("\n[Step 3] Dispatching IncidentAgent")
    incident_result = run_subagent(
        name="IncidentAgent",
        system=(
            "You are the incident specialist for Resolve support. "
            "Your only job is to check the status page for active incidents. "
            "Report what you find — empty list is a valid and important result."
        ),
        task="Check the Resolve status page for any active incidents right now.",
        tools=INCIDENT_TOOLS,
        tool_fn=incident_tool_fn,
        max_iterations=3
    )
    agent_results.append(incident_result)
    print(f"  IncidentAgent result: {incident_result}")

    if incident_result.status != "success":
        print("  [note] IncidentAgent failed — proceeding without incident data.")

    # ── Step 4: Assemble facts for DraftAgent ─────────────────────────────────
    # The coordinator summarises facts — the DraftAgent never sees raw agent results
    account_facts = account_result.output
    billing_facts = billing_result.output
    incident_facts = incident_result.output if incident_result.status == "success" else {}

    assembled_facts = (
        f"Customer ID: {customer_id}\n"
        f"Account plan: {account_facts.get('plan', 'unknown')}\n"
        f"Account status: {account_facts.get('account_status', 'unknown')}\n"
        f"Open invoices: {account_facts.get('open_invoices', 'unknown')}\n"
        f"Invoice list: {billing_facts.get('invoices', [])}\n"
        f"Most recent invoice amount: {billing_facts.get('amount', 'unknown')}\n"
        f"Invoice paid: {billing_facts.get('paid', 'unknown')}\n"
        f"Active incidents: {incident_facts.get('active_incidents', 'unknown — check failed')}\n"
        f"\nOriginal customer ticket:\n{ticket}"
    )

    print("\n[Step 4] Dispatching DraftAgent with assembled facts")
    draft_result = run_subagent(
        name="DraftAgent",
        system=(
            "You are a senior Resolve support specialist. "
            "Write a clear, professional reply to the customer based ONLY on the "
            "verified facts provided. Do not invent or assume any information. "
            "Be concise, empathetic, and actionable."
        ),
        task=assembled_facts,
        tools=[],                              # DraftAgent has no tools
        tool_fn=lambda n, i: {},
        max_iterations=2
    )
    agent_results.append(draft_result)
    print(f"  DraftAgent result: {draft_result}")

    if draft_result.status == "success":
        reply = draft_result.output.get("reply", "")
        print(f"\n{'='*60}")
        print("FINAL REPLY:")
        print("=" * 60)
        print(reply)
        return {
            "status": "success",
            "reply": reply,
            "agent_results": agent_results
        }

    return {
        "status": "escalated",
        "reason": "DraftAgent failed to produce a reply",
        "reply": None,
        "agent_results": agent_results
    }


# ── DEMO ──────────────────────────────────────────────────────────────────────

TICKET = (
    "Hi, my invoice INV-2026-0042 shows a charge of $4,200 but I thought "
    "I was on the starter plan. Customer ID is cust_9182. Can you investigate?"
)

result = run_coordinator(ticket=TICKET, customer_id="cust_9182")

print(f"\n{'='*60}")
print("COORDINATOR SUMMARY")
print("=" * 60)
print(f"Status        : {result['status']}")
print(f"Agents run    : {[r.agent for r in result['agent_results']]}")
print(f"Agent statuses: {[r.status for r in result['agent_results']]}")
total_iterations = sum(r.iterations for r in result["agent_results"])
print(f"Total iterations across all agents: {total_iterations}")

print("\nKey takeaway:")
print("  Each subagent runs an isolated loop — it sees only its own task.")
print("  The coordinator assembles facts before drafting — no raw results leak.")
print("  Critical agent failure short-circuits the entire pipeline.")
print("  SubAgentResult is a typed dataclass — never a plain string.")

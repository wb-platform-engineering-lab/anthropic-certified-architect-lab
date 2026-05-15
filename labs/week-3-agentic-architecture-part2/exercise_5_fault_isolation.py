"""
Exercise 5 — Fault Isolation and Graceful Degradation

Builds fault isolation into the multi-agent coordinator so that a failing
subagent never propagates an exception — it returns a typed failure result.

Failure simulation:
  IncidentAgent  — FAILURE_RATE = 0.6 (returns typed failure dict, not exception)
  BillingAgent   — FAILURE_RATE = 0.3
  AccountAgent   — always reliable

Three possible outcomes depending on how many agents succeed:
  full resolution   — all 3 agents succeeded
  degraded          — >= min_successes but < 3; reply notes missing context
  escalated         — < min_successes; coordinator cannot draft a safe reply

The demo runs 5 times on the same ticket so learners can observe different
outcomes caused by the random failure simulation.
"""
import json
import random
from concurrent.futures import ThreadPoolExecutor, as_completed, Future
from dataclasses import dataclass, field
from typing import Any, Optional

import anthropic
from dotenv import load_dotenv

load_dotenv()
client = anthropic.Anthropic()

# Failure rates for simulated agents
INCIDENT_FAILURE_RATE = 0.6
BILLING_FAILURE_RATE  = 0.3


# ── TYPED RESULTS ─────────────────────────────────────────────────────────────

@dataclass
class SubAgentResult:
    agent: str
    status: str          # success | access_failure | budget_exhausted | failed
    output: dict
    iterations: int

    @property
    def succeeded(self) -> bool:
        return self.status == "success"

    def __str__(self):
        return (
            f"SubAgentResult(agent={self.agent}, status={self.status}, "
            f"output_keys={list(self.output.keys())})"
        )


@dataclass
class CoordinatorResult:
    status: str             # full_resolution | degraded | insufficient_data
    reply: str
    agent_statuses: dict    # {agent_name: status}
    degraded: bool
    missing_context: list[str]   # names of agents that failed

    def __str__(self):
        parts = [
            f"status={self.status}",
            f"degraded={self.degraded}",
        ]
        if self.missing_context:
            parts.append(f"missing={self.missing_context}")
        return "CoordinatorResult(" + ", ".join(parts) + ")"


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
    Isolated agentic loop. Returns a typed SubAgentResult — never raises.
    Safe to call from a ThreadPoolExecutor worker thread.
    """
    messages: list[dict[str, Any]] = [{"role": "user", "content": task}]
    accumulated_output: dict = {}

    for iteration in range(1, max_iterations + 1):
        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=512,
            tools=tools,
            system=system,
            messages=messages
        )

        if response.stop_reason == "end_turn":
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
                    result = tool_fn(block.name, block.input)

                    # If the tool itself signals a failure, propagate it as a
                    # typed failure result — do not silently continue
                    if result.get("status") == "access_failure":
                        return SubAgentResult(
                            agent=name,
                            status="access_failure",
                            output=result,
                            iterations=iteration
                        )

                    accumulated_output.update(result)
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": json.dumps(result)
                    })

            messages.append({"role": "user", "content": tool_results})

        else:
            return SubAgentResult(
                agent=name,
                status="failed",
                output={"error": f"stop_reason={response.stop_reason}"},
                iterations=iteration
            )

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


# ── SIMULATED TOOL HANDLERS WITH FAULT INJECTION ──────────────────────────────

def account_tool_fn(name: str, inputs: dict) -> dict:
    """AccountAgent is always reliable — no failure simulation."""
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
    """
    BillingAgent fails with probability BILLING_FAILURE_RATE.
    Returns a typed failure dict — never raises an exception.
    """
    if random.random() < BILLING_FAILURE_RATE:
        return {
            "status": "access_failure",
            "code": "BILLING_DB_TIMEOUT",
            "message": "Billing database did not respond within the timeout window."
        }

    if name == "list_invoices":
        return {
            "status": "success",
            "invoices": ["INV-2026-0041", "INV-2026-0042"]
        }
    if name == "get_invoice_detail":
        return {
            "status": "success",
            "invoice_id": inputs.get("invoice_id", "INV-2026-0042"),
            "amount": 4200.00,
            "paid": False,
            "due_date": "2026-05-01"
        }
    return {"status": "error", "message": f"Unknown tool: {name}"}


def incident_tool_fn(name: str, inputs: dict) -> dict:
    """
    IncidentAgent fails with probability INCIDENT_FAILURE_RATE.
    Returns a typed failure dict — never raises an exception.
    """
    if random.random() < INCIDENT_FAILURE_RATE:
        return {
            "status": "access_failure",
            "code": "STATUS_PAGE_TIMEOUT",
            "message": "Status page API did not respond within the timeout window."
        }

    if name == "check_status_page":
        return {
            "status": "success",
            "overall_status": "operational",
            "active_incidents": []
        }
    return {"status": "error", "message": f"Unknown tool: {name}"}


# ── COORDINATOR WITH FAULT ISOLATION ─────────────────────────────────────────

def run_coordinator_with_isolation(
    ticket: str,
    customer_id: str,
    min_successes: int = 2
) -> CoordinatorResult:
    """
    Runs all three agents in parallel. Isolates faults so that one agent's
    failure does not crash the coordinator or other agents.

    Outcome logic:
      < min_successes succeeded → insufficient_data (escalate)
      >= min_successes but < 3  → degraded (reply with caveat about missing data)
      all 3 succeeded           → full_resolution
    """
    # Define agent specs as (name, system, task, tools, tool_fn)
    specs = [
        (
            "AccountAgent",
            "You are the account specialist for Resolve. Retrieve account status.",
            f"Retrieve account status for customer ID {customer_id}.",
            ACCOUNT_TOOLS,
            account_tool_fn
        ),
        (
            "BillingAgent",
            "You are the billing specialist. Retrieve invoice list and recent invoice detail.",
            f"List invoices for customer ID {customer_id}, then get detail for the most recent.",
            BILLING_TOOLS,
            billing_tool_fn
        ),
        (
            "IncidentAgent",
            "You are the incident specialist. Check the status page for active incidents.",
            "Check the Resolve status page for active incidents right now.",
            INCIDENT_TOOLS,
            incident_tool_fn
        )
    ]

    results: list[SubAgentResult] = []
    future_to_name: dict[Future, str] = {}

    # Run all three agents in parallel
    with ThreadPoolExecutor(max_workers=3) as executor:
        for name, system, task, tools, tool_fn in specs:
            future = executor.submit(run_subagent, name, system, task, tools, tool_fn)
            future_to_name[future] = name

        # Collect typed results — convert any unexpected exception to a typed failure
        for future in as_completed(future_to_name):
            agent_name = future_to_name[future]
            try:
                result = future.result()
            except Exception as exc:
                # Unexpected worker exception → typed failure (never propagate)
                result = SubAgentResult(
                    agent=agent_name,
                    status="access_failure",
                    output={"error": str(exc), "code": "WORKER_EXCEPTION"},
                    iterations=0
                )
            results.append(result)

    # Separate successes from failures
    successes = [r for r in results if r.succeeded]
    failures  = [r for r in results if not r.succeeded]
    missing_context = [r.agent for r in failures]

    agent_statuses = {r.agent: r.status for r in results}

    print(f"  successes={len(successes)}, failures={[r.agent for r in failures]}, "
          f"threshold={min_successes}")

    # ── Insufficient data — escalate ──────────────────────────────────────────
    if len(successes) < min_successes:
        print("  OUTCOME: insufficient_data → escalating to human")
        return CoordinatorResult(
            status="insufficient_data",
            reply=(
                "We were unable to retrieve enough information to safely respond to your ticket. "
                "A support specialist will review this and follow up shortly."
            ),
            agent_statuses=agent_statuses,
            degraded=True,
            missing_context=missing_context
        )

    # ── Assemble facts from successful agents only ─────────────────────────────
    facts_parts = [f"Customer ID: {customer_id}\n", f"Original ticket: {ticket}\n"]
    for r in successes:
        facts_parts.append(f"\n{r.agent} data (verified):")
        for k, v in r.output.items():
            if k != "reply":
                facts_parts.append(f"  {k}: {v}")

    # Note missing context so DraftAgent can caveat its reply
    if missing_context:
        facts_parts.append(
            f"\nNOTE: The following agents failed and their data is unavailable: "
            f"{missing_context}. "
            "Your reply MUST explicitly tell the customer that some information could not "
            "be retrieved and that you will follow up with a complete answer."
        )

    assembled_facts = "\n".join(facts_parts)

    # Draft the reply with available context
    draft_result = run_subagent(
        name="DraftAgent",
        system=(
            "You are a senior Resolve support specialist. "
            "Draft a professional reply based only on the verified facts provided. "
            "If the note says some data is unavailable, acknowledge this in your reply."
        ),
        task=assembled_facts,
        tools=[],
        tool_fn=lambda n, i: {},
        max_iterations=2
    )

    is_degraded = len(failures) > 0

    if is_degraded:
        print(f"  OUTCOME: degraded — missing context from {missing_context}")
    else:
        print("  OUTCOME: full_resolution — all agents succeeded")

    return CoordinatorResult(
        status="degraded" if is_degraded else "full_resolution",
        reply=draft_result.output.get("reply", ""),
        agent_statuses=agent_statuses,
        degraded=is_degraded,
        missing_context=missing_context
    )


# ── DEMO — 5 RUNS ─────────────────────────────────────────────────────────────

TICKET = (
    "Hi, my invoice INV-2026-0042 shows a charge of $4,200 but I thought "
    "I was on the starter plan. Customer ID is cust_9182. Can you investigate?"
)
CUSTOMER_ID = "cust_9182"
NUM_RUNS = 5

run_outcomes: list[CoordinatorResult] = []

for run_num in range(1, NUM_RUNS + 1):
    print(f"\n{'='*60}")
    print(f"RUN {run_num} / {NUM_RUNS}")
    print(f"  IncidentAgent failure rate : {INCIDENT_FAILURE_RATE:.0%}")
    print(f"  BillingAgent failure rate  : {BILLING_FAILURE_RATE:.0%}")
    print(f"  AccountAgent failure rate  : 0%")
    print("=" * 60)

    result = run_coordinator_with_isolation(
        ticket=TICKET,
        customer_id=CUSTOMER_ID,
        min_successes=2
    )
    run_outcomes.append(result)

    print(f"\n  Result     : {result}")
    print(f"  Agent statuses: {result.agent_statuses}")
    if result.reply:
        print(f"  Reply      : {result.reply[:200]}{'...' if len(result.reply) > 200 else ''}")

# ── RUN SUMMARY ───────────────────────────────────────────────────────────────
print("\n" + "=" * 60)
print("5-RUN SUMMARY")
print("=" * 60)

outcome_counts: dict[str, int] = {}
for outcome in run_outcomes:
    outcome_counts[outcome.status] = outcome_counts.get(outcome.status, 0) + 1

for run_num, outcome in enumerate(run_outcomes, 1):
    missing_str = f"missing={outcome.missing_context}" if outcome.missing_context else "all data available"
    print(f"  Run {run_num}: {outcome.status:<22}  {missing_str}")

print("\nOutcome distribution:")
for status, count in sorted(outcome_counts.items()):
    print(f"  {status:<22} {count} / {NUM_RUNS}")

print("\nKey takeaway:")
print("  Typed failure returns (not exceptions) keep the coordinator in control.")
print("  access_failure from a tool is returned through run_subagent as SubAgentResult.")
print("  Future.result() is wrapped in try/except as a final safety net.")
print("  The coordinator degrades gracefully: full → degraded → insufficient_data.")
print("  missing_context tells downstream consumers exactly which data is absent.")

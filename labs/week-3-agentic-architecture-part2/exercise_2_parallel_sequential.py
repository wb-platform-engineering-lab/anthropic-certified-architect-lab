"""
Exercise 2 — Sequential vs Parallel Subagent Execution

Contrasts two execution strategies for the same three specialist subagents:

  Sequential : AccountAgent → BillingAgent → IncidentAgent (one at a time)
  Parallel   : all three simultaneously via ThreadPoolExecutor

Key concepts demonstrated:
  - ThreadPoolExecutor + as_completed for parallel subagent dispatch
  - future.result() wrapped in try/except to convert exceptions to typed results
  - Minimum-success threshold: need >= 2 of 3 agents to proceed
  - Timing comparison with time.perf_counter()
  - Results collected as list[SubAgentResult] — successes separated from failures
"""
import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed, Future
from dataclasses import dataclass
from typing import Any, Optional

import anthropic
from dotenv import load_dotenv

load_dotenv()
client = anthropic.Anthropic()

MIN_SUCCESSES = 2    # threshold: need at least this many agents to succeed


# ── TYPED RESULT ──────────────────────────────────────────────────────────────

@dataclass
class SubAgentResult:
    agent: str
    status: str          # success | failed | budget_exhausted
    output: dict
    iterations: int
    elapsed_seconds: Optional[float] = None

    def __str__(self):
        elapsed = f", elapsed={self.elapsed_seconds:.2f}s" if self.elapsed_seconds else ""
        return (
            f"SubAgentResult(agent={self.agent}, status={self.status}, "
            f"iterations={self.iterations}{elapsed})"
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
    Runs a bounded agentic loop for one specialist subagent.

    Each subagent sees only its own task string — never the coordinator's
    history or any other agent's output. Safe to run from a worker thread.
    """
    messages: list[dict[str, Any]] = [{"role": "user", "content": task}]
    accumulated_output: dict = {}
    t0 = time.perf_counter()

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
                iterations=iteration,
                elapsed_seconds=time.perf_counter() - t0
            )

        elif response.stop_reason == "tool_use":
            messages.append({"role": "assistant", "content": response.content})
            tool_results = []

            for block in response.content:
                if block.type == "tool_use":
                    result = tool_fn(block.name, block.input)
                    accumulated_output.update(result)
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": json.dumps(result)
                    })

            messages.append({"role": "user", "content": tool_results})

        elif response.stop_reason == "max_tokens":
            return SubAgentResult(
                agent=name,
                status="failed",
                output={"error": "output truncated"},
                iterations=iteration,
                elapsed_seconds=time.perf_counter() - t0
            )

        else:
            return SubAgentResult(
                agent=name,
                status="failed",
                output={"error": f"unexpected stop_reason={response.stop_reason}"},
                iterations=iteration,
                elapsed_seconds=time.perf_counter() - t0
            )

    return SubAgentResult(
        agent=name,
        status="budget_exhausted",
        output=accumulated_output,
        iterations=max_iterations,
        elapsed_seconds=time.perf_counter() - t0
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
    if name == "check_status_page":
        return {
            "status": "success",
            "overall_status": "operational",
            "active_incidents": []
        }
    return {"status": "error", "message": f"Unknown tool: {name}"}


# ── AGENT SPECS ───────────────────────────────────────────────────────────────
# Each spec is a tuple: (name, system, task, tools, tool_fn)
# Building them as a list keeps both execution paths DRY.

def build_agent_specs(customer_id: str) -> list[tuple]:
    return [
        (
            "AccountAgent",
            "You are the account specialist for Resolve. Retrieve account status for the customer.",
            f"Retrieve account status for customer ID {customer_id}.",
            ACCOUNT_TOOLS,
            account_tool_fn
        ),
        (
            "BillingAgent",
            "You are the billing specialist for Resolve. Retrieve invoice list and detail.",
            f"List invoices for customer ID {customer_id}, then get detail for the most recent one.",
            BILLING_TOOLS,
            billing_tool_fn
        ),
        (
            "IncidentAgent",
            "You are the incident specialist for Resolve. Check the status page.",
            "Check the Resolve status page for active incidents.",
            INCIDENT_TOOLS,
            incident_tool_fn
        )
    ]


# ── SEQUENTIAL EXECUTION ──────────────────────────────────────────────────────

def run_sequential(customer_id: str) -> tuple[list[SubAgentResult], float]:
    """
    Runs all three agents one after the other.
    Returns (results, total_elapsed_seconds).
    """
    specs = build_agent_specs(customer_id)
    results: list[SubAgentResult] = []

    t_start = time.perf_counter()
    for name, system, task, tools, tool_fn in specs:
        print(f"  [sequential] starting {name}")
        result = run_subagent(name, system, task, tools, tool_fn)
        print(f"  [sequential] {name} done — status={result.status}, "
              f"elapsed={result.elapsed_seconds:.2f}s")
        results.append(result)
    total = time.perf_counter() - t_start

    return results, total


# ── PARALLEL EXECUTION ────────────────────────────────────────────────────────

def run_parallel(customer_id: str) -> tuple[list[SubAgentResult], float]:
    """
    Runs all three agents simultaneously using ThreadPoolExecutor.

    future.result() is wrapped in try/except so that any unexpected exception
    from a worker thread is converted to a typed SubAgentResult with status=failed,
    rather than propagating as an unhandled exception.

    Results arrive via as_completed in whichever order they finish.
    """
    specs = build_agent_specs(customer_id)
    results: list[SubAgentResult] = []

    # Map Future → agent name so we can label results as they arrive
    future_to_name: dict[Future, str] = {}

    t_start = time.perf_counter()

    with ThreadPoolExecutor(max_workers=3) as executor:
        for name, system, task, tools, tool_fn in specs:
            print(f"  [parallel] submitting {name}")
            future = executor.submit(run_subagent, name, system, task, tools, tool_fn)
            future_to_name[future] = name

        # Collect results as each thread completes (not in submission order)
        for future in as_completed(future_to_name):
            agent_name = future_to_name[future]
            try:
                result = future.result()
                print(f"  [parallel] {agent_name} completed — status={result.status}, "
                      f"elapsed={result.elapsed_seconds:.2f}s")
                results.append(result)
            except Exception as exc:
                # Convert unexpected worker exception to a typed failure result
                print(f"  [parallel] {agent_name} raised exception: {exc}")
                results.append(SubAgentResult(
                    agent=agent_name,
                    status="failed",
                    output={"error": str(exc)},
                    iterations=0
                ))

    total = time.perf_counter() - t_start
    return results, total


# ── THRESHOLD CHECK + DRAFT ───────────────────────────────────────────────────

def check_threshold_and_draft(
    results: list[SubAgentResult],
    ticket: str,
    mode: str
) -> dict:
    """
    Applies the minimum-success threshold.
    If met, passes collected facts to a DraftAgent.
    Returns a typed result dict.
    """
    successes = [r for r in results if r.status == "success"]
    failures  = [r for r in results if r.status != "success"]

    print(f"\n  [{mode}] successes={len(successes)}, failures={len(failures)}, "
          f"threshold={MIN_SUCCESSES}")

    if len(successes) < MIN_SUCCESSES:
        print(f"  [{mode}] below threshold — escalating")
        return {
            "status": "escalated",
            "mode": mode,
            "successes": len(successes),
            "failures": [r.agent for r in failures],
            "reply": None
        }

    # Assemble facts from successful agents only
    facts_parts = [f"Original ticket: {ticket}"]
    for r in successes:
        facts_parts.append(f"\n{r.agent} findings:")
        for k, v in r.output.items():
            if k != "reply":
                facts_parts.append(f"  {k}: {v}")

    assembled_facts = "\n".join(facts_parts)

    print(f"  [{mode}] threshold met — dispatching DraftAgent")
    draft = run_subagent(
        name="DraftAgent",
        system=(
            "You are a senior Resolve support specialist. "
            "Write a concise, professional reply based only on the verified facts provided."
        ),
        task=assembled_facts,
        tools=[],
        tool_fn=lambda n, i: {},
        max_iterations=2
    )

    return {
        "status": "success" if draft.status == "success" else "escalated",
        "mode": mode,
        "successes": len(successes),
        "failures": [r.agent for r in failures],
        "reply": draft.output.get("reply", "")
    }


# ── DEMO ──────────────────────────────────────────────────────────────────────

TICKET = (
    "Hi, my invoice INV-2026-0042 shows a charge of $4,200 but I thought "
    "I was on the starter plan. Customer ID is cust_9182. Can you investigate?"
)
CUSTOMER_ID = "cust_9182"

print("=" * 60)
print("RUN 1 — SEQUENTIAL")
print("=" * 60)
seq_results, seq_total = run_sequential(CUSTOMER_ID)
seq_outcome = check_threshold_and_draft(seq_results, TICKET, mode="sequential")

print("\n" + "=" * 60)
print("RUN 2 — PARALLEL")
print("=" * 60)
par_results, par_total = run_parallel(CUSTOMER_ID)
par_outcome = check_threshold_and_draft(par_results, TICKET, mode="parallel")

# ── TIMING COMPARISON TABLE ───────────────────────────────────────────────────
print("\n" + "=" * 60)
print("TIMING COMPARISON")
print("=" * 60)
print(f"{'Mode':<14} {'Total time':>12}  {'Agent results'}")
print("-" * 60)

seq_labels = "  ".join(f"{r.agent}({r.elapsed_seconds:.2f}s)" for r in seq_results)
print(f"{'Sequential':<14} {seq_total:>10.2f}s  {seq_labels}")

par_labels = "  ".join(f"{r.agent}({r.elapsed_seconds:.2f}s)" for r in par_results)
print(f"{'Parallel':<14} {par_total:>10.2f}s  {par_labels}")

if seq_total > 0 and par_total > 0:
    speedup = seq_total / par_total
    print(f"\nSpeedup factor: {speedup:.2f}x")

print(f"\nSequential outcome: {seq_outcome['status']}")
print(f"Parallel   outcome: {par_outcome['status']}")

print("\nKey takeaway:")
print("  Sequential is simpler but wall-clock time = sum of all agent times.")
print("  Parallel wall-clock time ≈ slowest single agent (not the sum).")
print("  as_completed + try/except converts worker exceptions to typed failures.")
print("  The minimum-success threshold is enforced in code — not by the model.")

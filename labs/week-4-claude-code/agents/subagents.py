"""
agents/subagents.py — Shared subagent runner for the Resolve pipeline.

run_subagent() is the single entry point for all subagent loops. Each subagent
runs with its own tool set and iteration budget. Results are written into the
shared SessionState.

Iteration budgets (from agents/CLAUDE.md — do not change without running evals):
  AccountAgent  : max_iterations = 5
  BillingAgent  : max_iterations = 5
  IncidentAgent : max_iterations = 3
"""

import json
from typing import Callable, Optional

import anthropic
from dotenv import load_dotenv

from agents.session_state import SessionState
from agents.hooks import PreCallHook, PostCallHook, HookViolation

load_dotenv()

client = anthropic.Anthropic()

# Iteration budgets — see agents/CLAUDE.md for the rationale behind each value.
AGENT_BUDGETS: dict[str, int] = {
    "AccountAgent": 5,
    "BillingAgent": 5,
    "IncidentAgent": 3,
}


# =============================================================================
# Tool implementations
# Each tool returns a dict with a "status" field. Valid values:
#   "success"        — tool executed and returned meaningful data
#   "access_failure" — could not access the resource
#   "empty"          — succeeded but the result set is empty
# Never raise an exception out of a tool.
# =============================================================================

def get_account_details(customer_id: str) -> dict:
    """Look up account status, plan tier, and auth state for a customer."""
    # Simulated account data — in production this would call an internal API
    accounts = {
        "cust_4471": {"status": "success", "plan": "Pro", "active": True, "auth_state": "verified"},
        "cust_9182": {"status": "success", "plan": "Starter", "active": True, "auth_state": "verified"},
        "cust_0019": {"status": "success", "plan": "Enterprise", "active": True, "auth_state": "verified"},
    }
    result = accounts.get(customer_id)
    if result is None:
        return {"status": "access_failure", "code": "NOT_FOUND", "message": f"No account found for {customer_id}"}
    return result


def get_billing_summary(customer_id: str) -> dict:
    """Retrieve the most recent invoices and charge history for a customer."""
    billing = {
        "cust_4471": {
            "status": "success",
            "last_invoice": {"amount": 49.00, "date": "2026-05-01", "paid": True},
            "duplicate_charge": {"amount": 49.00, "date": "2026-05-01", "charge_id": "ch_dup_001"},
            "open_refund_eligible": True,
        },
        "cust_9182": {
            "status": "success",
            "last_invoice": {"amount": 9.00, "date": "2026-05-01", "paid": True},
            "duplicate_charge": None,
            "open_refund_eligible": False,
        },
        "cust_0019": {
            "status": "success",
            "last_invoice": {"amount": 299.00, "date": "2026-05-01", "paid": True},
            "duplicate_charge": None,
            "open_refund_eligible": False,
        },
    }
    result = billing.get(customer_id)
    if result is None:
        return {"status": "empty", "message": f"No billing records for {customer_id}"}
    return result


def issue_refund(customer_id: str, charge_id: str, amount: float) -> dict:
    """Issue a refund for a specific charge. Requires get_billing_summary to have run first."""
    if amount <= 0 or amount > 500.0:
        return {"status": "access_failure", "code": "INVALID_AMOUNT", "message": f"Refund amount {amount} is out of bounds"}
    return {
        "status": "success",
        "refund_id": f"ref_{charge_id}",
        "amount": amount,
        "eta_days": "5-7",
        "message": f"Refund of ${amount:.2f} initiated. Will appear within 5-7 business days.",
    }


def get_incident_status(region: str = "us-east-1") -> dict:
    """Check for active incidents or degraded service in a region."""
    # No active incidents in simulation
    return {
        "status": "empty",
        "active_incidents": [],
        "region": region,
        "service_health": "operational",
    }


def escalate_incident(incident_id: str, priority: str = "P2") -> dict:
    """Escalate an incident to the on-call team. Requires get_incident_status first."""
    return {
        "status": "success",
        "escalation_id": f"esc_{incident_id}",
        "priority": priority,
        "message": f"Incident {incident_id} escalated as {priority}.",
    }


# Tool registry — maps tool name to function
TOOL_REGISTRY: dict[str, Callable] = {
    "get_account_details": get_account_details,
    "get_billing_summary": get_billing_summary,
    "issue_refund": issue_refund,
    "get_incident_status": get_incident_status,
    "escalate_incident": escalate_incident,
}

# Tool schemas — used in client.messages.create(tools=[...])
ALL_TOOL_SCHEMAS: list[dict] = [
    {
        "name": "get_account_details",
        "description": "Look up account status, plan tier, and authentication state for a customer.",
        "input_schema": {
            "type": "object",
            "properties": {
                "customer_id": {"type": "string", "description": "The customer account identifier"},
            },
            "required": ["customer_id"],
        },
    },
    {
        "name": "get_billing_summary",
        "description": "Retrieve the most recent invoices and charge history for a customer.",
        "input_schema": {
            "type": "object",
            "properties": {
                "customer_id": {"type": "string", "description": "The customer account identifier"},
            },
            "required": ["customer_id"],
        },
    },
    {
        "name": "issue_refund",
        "description": "Issue a refund for a specific charge. Requires get_billing_summary to have run first.",
        "input_schema": {
            "type": "object",
            "properties": {
                "customer_id": {"type": "string"},
                "charge_id": {"type": "string", "description": "The charge ID from get_billing_summary"},
                "amount": {"type": "number", "description": "Refund amount in dollars"},
            },
            "required": ["customer_id", "charge_id", "amount"],
        },
    },
    {
        "name": "get_incident_status",
        "description": "Check for active incidents or degraded service in a region.",
        "input_schema": {
            "type": "object",
            "properties": {
                "region": {"type": "string", "description": "AWS region (default: us-east-1)"},
            },
            "required": [],
        },
    },
    {
        "name": "escalate_incident",
        "description": "Escalate an incident to the on-call team. Requires get_incident_status first.",
        "input_schema": {
            "type": "object",
            "properties": {
                "incident_id": {"type": "string"},
                "priority": {"type": "string", "enum": ["P1", "P2", "P3"]},
            },
            "required": ["incident_id"],
        },
    },
]

# Per-agent tool sets
AGENT_TOOLS: dict[str, list[str]] = {
    "AccountAgent": ["get_account_details"],
    "BillingAgent": ["get_billing_summary", "issue_refund"],
    "IncidentAgent": ["get_incident_status", "escalate_incident"],
}


def run_subagent(
    agent_name: str,
    state: SessionState,
    task: str,
) -> dict:
    """
    Run a single subagent loop and write results into SessionState.

    Returns a dict with:
      agent      -- agent name
      status     -- "success" | "budget_exhausted" | "truncated" | "error"
      output     -- the agent's final text reply (or None)
      iterations -- number of iterations used
      violations -- list of HookViolation messages (empty if none)
    """
    max_iterations = AGENT_BUDGETS.get(agent_name, 5)
    tool_names = AGENT_TOOLS.get(agent_name, [])
    tool_schemas = [t for t in ALL_TOOL_SCHEMAS if t["name"] in tool_names]

    pre_hook = PreCallHook()
    post_hook = PostCallHook()

    system_prompt = (
        f"You are {agent_name}, a specialised support agent for Resolve. "
        f"Your task: {task}\n\n"
        "Use the available tools to gather facts. When you have enough information "
        "to answer, reply with a concise summary of what you found and what action "
        "(if any) was taken. Be direct — this is an internal system, not a customer reply."
    )

    messages: list[dict] = [{"role": "user", "content": task}]
    iterations = 0
    exit_status = "error"
    final_output: Optional[str] = None
    violations: list[str] = []

    while iterations < max_iterations:
        iterations += 1

        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=1024,
            system=system_prompt,
            tools=tool_schemas,
            messages=messages,
        )

        stop_reason = response.stop_reason

        if stop_reason == "end_turn":
            # Collect all text blocks as the final output
            text_parts = [b.text for b in response.content if hasattr(b, "text")]
            final_output = " ".join(text_parts).strip()
            exit_status = "success"
            break

        elif stop_reason == "max_tokens":
            text_parts = [b.text for b in response.content if hasattr(b, "text")]
            final_output = " ".join(text_parts).strip()
            exit_status = "truncated"
            break

        elif stop_reason == "stop_sequence":
            text_parts = [b.text for b in response.content if hasattr(b, "text")]
            final_output = " ".join(text_parts).strip()
            exit_status = "success"
            break

        elif stop_reason == "tool_use":
            # Collect tool use blocks
            tool_blocks = [b for b in response.content if b.type == "tool_use"]

            # Append assistant message
            messages.append({"role": "assistant", "content": response.content})

            # Execute each tool call
            tool_results = []
            hook_violation_occurred = False

            for tool_block in tool_blocks:
                tool_name = tool_block.name
                tool_args = tool_block.input
                tool_use_id = tool_block.id

                try:
                    pre_hook.before_call(tool_name, tool_args)
                except HookViolation as hv:
                    violation_msg = f"PreCallHook: {hv}"
                    violations.append(violation_msg)
                    state.violations.append(violation_msg)
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": tool_use_id,
                        "content": json.dumps({"status": "access_failure", "message": str(hv)}),
                    })
                    hook_violation_occurred = True
                    break

                # Execute the tool
                tool_fn = TOOL_REGISTRY.get(tool_name)
                if tool_fn is None:
                    result = {"status": "access_failure", "message": f"Tool {tool_name} is not registered."}
                else:
                    try:
                        result = tool_fn(**tool_args)
                    except Exception as e:
                        result = {"status": "access_failure", "message": f"Tool raised exception: {e}"}

                try:
                    post_hook.validate_output(tool_name, tool_args, result)
                except HookViolation as hv:
                    violation_msg = f"PostCallHook: {hv}"
                    violations.append(violation_msg)
                    state.violations.append(violation_msg)
                    hook_violation_occurred = True

                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": tool_use_id,
                    "content": json.dumps(result),
                })

            if hook_violation_occurred:
                exit_status = "error"
                messages.append({"role": "user", "content": tool_results})
                break

            messages.append({"role": "user", "content": tool_results})

        else:
            # Unexpected stop_reason
            exit_status = "error"
            break

    else:
        # While loop exhausted without break
        exit_status = "budget_exhausted"

    # Write results into SessionState
    state.iteration_counts[agent_name] = iterations
    state.exit_reason = exit_status

    if agent_name == "AccountAgent" and final_output:
        state.account_facts["summary"] = final_output
    elif agent_name == "BillingAgent" and final_output:
        state.billing_facts["summary"] = final_output
    elif agent_name == "IncidentAgent" and final_output:
        state.incident_facts["summary"] = final_output

    return {
        "agent": agent_name,
        "status": exit_status,
        "output": final_output,
        "iterations": iterations,
        "violations": violations,
    }

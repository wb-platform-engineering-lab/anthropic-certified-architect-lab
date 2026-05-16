"""
agents/coordinator.py — Coordinator for the Resolve support ticket pipeline.

The coordinator:
  1. Receives an inbound ticket and classifies it
  2. Dispatches the appropriate subagent(s) based on classification
  3. Assembles the final customer-facing reply from subagent outputs
  4. Returns a typed result dict

max_iterations = 8 (set after measuring the eval suite — do not change without running evals)

Exit reasons written to SessionState:
  success          — coordinator produced a final reply
  budget_exhausted — hit max_iterations without end_turn
  truncated        — a response was cut off by max_tokens
  error            — unexpected exception or unrecoverable tool failure
"""

import json
from typing import Optional

import anthropic
from dotenv import load_dotenv

from agents.session_state import SessionState
from agents.subagents import run_subagent

load_dotenv()

client = anthropic.Anthropic()

# Do not change this value without running the full eval suite.
MAX_ITERATIONS = 8

# Classification → agents to dispatch
CLASSIFICATION_DISPATCH: dict[str, list[str]] = {
    "billing":  ["BillingAgent"],
    "account":  ["AccountAgent"],
    "incident": ["IncidentAgent"],
    "mixed":    ["AccountAgent", "BillingAgent"],
    "unknown":  [],
}


def _classify_ticket(ticket_text: str) -> str:
    """
    Use a lightweight Claude call to classify the ticket into a category.
    Returns one of: "billing" | "account" | "incident" | "mixed" | "unknown"
    """
    response = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=16,
        system=(
            "Classify the support ticket into exactly one category.\n"
            "Output ONLY one word: billing, account, incident, mixed, or unknown.\n"
            "No explanation. No punctuation."
        ),
        messages=[{"role": "user", "content": ticket_text}],
    )
    raw = response.content[0].text.strip().lower()
    if raw in CLASSIFICATION_DISPATCH:
        return raw
    return "unknown"


def _build_final_reply(state: SessionState) -> str:
    """
    Use Claude to assemble a customer-facing reply from the subagent outputs
    collected in SessionState.
    """
    context_parts = []

    if state.account_facts:
        context_parts.append(f"Account information:\n{json.dumps(state.account_facts, indent=2)}")
    if state.billing_facts:
        context_parts.append(f"Billing information:\n{json.dumps(state.billing_facts, indent=2)}")
    if state.incident_facts:
        context_parts.append(f"Incident information:\n{json.dumps(state.incident_facts, indent=2)}")

    if not context_parts:
        return (
            "Thank you for contacting Resolve support. We were unable to retrieve "
            "the information needed to address your ticket. A support engineer will "
            "follow up within one business day."
        )

    context = "\n\n".join(context_parts)

    response = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=512,
        system=(
            "You are a support agent writing a customer-facing reply. "
            "Use the internal facts provided to write a clear, concise, empathetic reply. "
            "Do not reveal internal system details, charge IDs, or agent names. "
            "If a refund was issued, mention the amount and ETA. "
            "Keep the reply under 100 words."
        ),
        messages=[{
            "role": "user",
            "content": (
                f"Customer ticket: {state.raw_ticket}\n\n"
                f"Internal facts gathered:\n{context}\n\n"
                "Write the customer reply."
            ),
        }],
    )
    return response.content[0].text.strip()


def run_coordinator(customer_id: str, ticket_text: str, ticket_id: Optional[str] = None) -> dict:
    """
    Run the full Resolve coordinator pipeline for a single ticket.

    Args:
        customer_id  -- customer account identifier (e.g. "cust_4471")
        ticket_text  -- the raw ticket text from the customer
        ticket_id    -- optional ticket ID for tracking; generated if not provided

    Returns a dict with all fields from SessionState, serialisable for the eval harness.
    """
    import uuid
    state = SessionState(
        ticket_id=ticket_id or f"ticket_{uuid.uuid4().hex[:8]}",
        customer_id=customer_id,
        raw_ticket=ticket_text,
    )

    # Step 1: Classify
    classification = _classify_ticket(ticket_text)
    state.classification = classification

    # Step 2: Determine which agents to dispatch
    agents_to_run = CLASSIFICATION_DISPATCH.get(classification, [])
    state.dispatched_agents = agents_to_run

    if not agents_to_run:
        # Unknown ticket type — reply without subagent dispatch
        state.exit_reason = "success"
        state.final_reply = (
            "Thank you for contacting Resolve support. Your ticket has been received "
            "and will be reviewed by a support engineer within one business day."
        )
        return _state_to_dict(state)

    # Step 3: Run each agent and collect results
    agent_results = {}
    coordinator_status = "success"

    for agent_name in agents_to_run:
        task = _build_agent_task(agent_name, customer_id, ticket_text)
        result = run_subagent(agent_name, state, task)
        agent_results[agent_name] = result

        if result["status"] not in ("success", "empty"):
            # Non-critical degradation: continue but note the issue
            if result["status"] == "budget_exhausted":
                coordinator_status = "budget_exhausted" if coordinator_status == "success" else coordinator_status
            else:
                coordinator_status = "error" if result["status"] == "error" else coordinator_status

    # Step 4: Assemble final reply
    state.final_reply = _build_final_reply(state)
    state.exit_reason = coordinator_status

    return _state_to_dict(state)


def _build_agent_task(agent_name: str, customer_id: str, ticket_text: str) -> str:
    """Build the task prompt for a specific subagent."""
    base = f"Customer ID: {customer_id}\nTicket: {ticket_text}\n\n"

    if agent_name == "AccountAgent":
        return base + (
            "Look up the account details for this customer. "
            "Report the plan tier, account status, and authentication state."
        )
    elif agent_name == "BillingAgent":
        return base + (
            "Look up the billing history for this customer. "
            "Check for duplicate charges or refund-eligible transactions. "
            "If a duplicate charge is found and the customer is complaining about it, issue a refund."
        )
    elif agent_name == "IncidentAgent":
        return base + (
            "Check for active incidents or service degradation. "
            "Report the current service health and any active incidents."
        )
    return base + "Gather relevant information to help resolve this ticket."


def _state_to_dict(state: SessionState) -> dict:
    """Serialise SessionState to a plain dict for the eval harness."""
    return {
        "ticket_id": state.ticket_id,
        "customer_id": state.customer_id,
        "raw_ticket": state.raw_ticket,
        "classification": state.classification,
        "dispatched_agents": state.dispatched_agents,
        "account_facts": state.account_facts,
        "billing_facts": state.billing_facts,
        "incident_facts": state.incident_facts,
        "violations": state.violations,
        "iteration_counts": state.iteration_counts,
        "exit_reason": state.exit_reason,
        "final_reply": state.final_reply,
    }

"""
Exercise 6 — The Claude Agent SDK

Shows what the Agent SDK provides on top of the raw API and rebuilds the
Resolve ticket resolution loop using SDK-style abstractions.

The actual claude_agent_sdk package may not be available in all environments.
This file implements the same patterns using the raw anthropic client and
clearly annotates which parts the SDK would replace — so you understand
both layers, which is what the exam tests.

Run this file as-is (raw API with SDK-pattern annotations).
If you have the Agent SDK installed, see the inline comments to swap in
the SDK equivalents.
"""
import json
from dataclasses import dataclass, field
from typing import Any, Callable

import anthropic
from dotenv import load_dotenv

load_dotenv()
client = anthropic.Anthropic()


# ── TOOL REGISTRY ─────────────────────────────────────────────────────────────
# SDK equivalent: @agent.tool decorator registers tools and their handlers.
# Raw API: we maintain a dict mapping tool name → handler function.

_tool_registry: dict[str, Callable] = {}
_tool_schemas: list[dict] = []

def register_tool(schema: dict):
    """Decorator — registers a function as a tool handler."""
    def decorator(fn: Callable):
        _tool_registry[schema["name"]] = fn
        _tool_schemas.append(schema)
        return fn
    return decorator


@register_tool({
    "name": "get_customer",
    "description": "Retrieve verified customer record from the CRM.",
    "input_schema": {
        "type": "object",
        "properties": {"customer_id": {"type": "string"}},
        "required": ["customer_id"]
    }
})
def get_customer(customer_id: str) -> dict:
    # Simulated CRM lookup
    db = {
        "cust_9182": {"name": "Acme Corp", "plan": "enterprise", "status": "active"},
        "cust_0001": {"name": "New Ltd", "plan": "starter", "status": "active"},
    }
    record = db.get(customer_id)
    if not record:
        return {"status": "empty", "message": "No customer found for this ID."}
    return {"status": "success", **record}


@register_tool({
    "name": "lookup_order",
    "description": "Look up an order or invoice by ID.",
    "input_schema": {
        "type": "object",
        "properties": {"order_id": {"type": "string"}},
        "required": ["order_id"]
    }
})
def lookup_order(order_id: str) -> dict:
    return {
        "status": "success",
        "order_id": order_id,
        "amount": 4200.00,
        "paid": False,
        "due_date": "2026-05-01"
    }


@register_tool({
    "name": "process_refund",
    "description": "Process a refund. Requires get_customer to have been called first.",
    "input_schema": {
        "type": "object",
        "properties": {
            "customer_id": {"type": "string"},
            "amount": {"type": "number"},
            "reason": {"type": "string"}
        },
        "required": ["customer_id", "amount", "reason"]
    }
})
def process_refund(customer_id: str, amount: float, reason: str) -> dict:
    return {
        "status": "success",
        "refund_id": f"REF-{abs(hash(customer_id + reason)) % 10000:04d}",
        "amount": amount,
        "message": "Refund queued for processing."
    }


@register_tool({
    "name": "escalate_to_human",
    "description": "Escalate the ticket to the human support queue.",
    "input_schema": {
        "type": "object",
        "properties": {
            "reason": {"type": "string"},
            "priority": {"type": "string", "enum": ["low", "normal", "high", "urgent"]}
        },
        "required": ["reason", "priority"]
    }
})
def escalate_to_human(reason: str, priority: str) -> dict:
    return {
        "status": "success",
        "ticket_id": "ESC-0042",
        "queue": "billing-senior",
        "priority": priority,
        "message": f"Ticket escalated: {reason}"
    }


# ── SESSION MANAGEMENT ────────────────────────────────────────────────────────
# SDK equivalent: built-in session primitives — no manual messages[] management.
# Raw API: we maintain messages[], state, and iteration count manually.

@dataclass
class AgentSession:
    """
    Mirrors what the Agent SDK manages internally.
    The SDK's session object exposes these as properties.
    """
    session_id: str
    messages: list[dict[str, Any]] = field(default_factory=list)
    tools_called: list[str] = field(default_factory=list)
    iteration_count: int = 0
    status: str = "running"   # running | success | escalated | budget_exhausted | error

    def add_user(self, content):
        self.messages.append({"role": "user", "content": content})

    def add_assistant(self, content):
        self.messages.append({"role": "assistant", "content": content})


# ── AGENT RUNNER ──────────────────────────────────────────────────────────────
# SDK equivalent: agent.run(session, user_message) — the loop is managed by the SDK.
# Raw API: we implement the loop, stop_reason routing, and tool dispatch ourselves.

SYSTEM_PROMPT = """
You are a Resolve support agent. You have access to these tools:
  - get_customer: always call this first for any billing or account question
  - lookup_order: look up a specific order or invoice
  - process_refund: only after get_customer has verified the account
  - escalate_to_human: when a situation is beyond your authority

Handle the ticket and either resolve it or escalate it. Do not make up information.
"""

def run_agent(session_id: str, ticket: str, max_iterations: int = 8) -> AgentSession:
    """
    SDK-pattern agent runner using the raw API.

    SDK equivalent:
        session = agent.create_session()
        result  = agent.run(session, ticket)
    """
    session = AgentSession(session_id=session_id)
    session.add_user(ticket)

    print(f"\n{'='*60}")
    print(f"Session {session_id}")
    print(f"Ticket : {ticket[:80]}")

    for _ in range(max_iterations):
        session.iteration_count += 1

        # SDK equivalent: the SDK calls the model internally; you only see tool callbacks.
        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=512,
            tools=_tool_schemas,
            system=SYSTEM_PROMPT,
            messages=session.messages
        )

        print(f"\n  [iter {session.iteration_count}] stop_reason={response.stop_reason}")

        if response.stop_reason == "end_turn":
            text = next((b.text for b in response.content if hasattr(b, "text")), "")
            session.status = "success"
            print(f"\n✓ Resolved:\n{text[:300]}")
            return session

        elif response.stop_reason == "tool_use":
            session.add_assistant(response.content)
            tool_results = []

            for block in response.content:
                if block.type != "tool_use":
                    continue

                handler = _tool_registry.get(block.name)
                if not handler:
                    result = {"status": "error", "message": f"No handler for tool {block.name}"}
                else:
                    # SDK equivalent: @agent.tool-decorated functions are called automatically.
                    result = handler(**block.input)

                session.tools_called.append(block.name)
                print(f"  tool: {block.name}({block.input}) → {result}")

                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": json.dumps(result)
                })

            session.add_user(tool_results)

        elif response.stop_reason == "max_tokens":
            session.status = "error"
            print("✗ Output truncated — escalating.")
            return session

        else:
            session.status = "error"
            print(f"✗ Unexpected stop_reason: {response.stop_reason} — escalating.")
            return session

    session.status = "budget_exhausted"
    print(f"✗ Budget exhausted after {max_iterations} iterations — escalating.")
    return session


# ── SDK vs RAW API SUMMARY ────────────────────────────────────────────────────

SDK_COMPARISON = """
What the Agent SDK replaces vs. what stays the same:

  REPLACED BY SDK:
    while loop + stop_reason routing  →  agent.run(session, message)
    messages[].append(...)            →  SDK manages history internally
    tool name → handler dispatch      →  @agent.tool decorator + auto-dispatch
    session state dataclass           →  agent.create_session() primitives

  UNCHANGED BY SDK (exam tests these):
    stop_reason contract              →  still drives termination
    tool call message cycle           →  assistant + tool_result still required
    iteration budget                  →  you still set max_iterations
    escalation logic                  →  you still define when to escalate
    tool return shapes                →  status field contract still applies

The SDK removes boilerplate. It does not change what you need to reason about.
"""

# ── DEMO ──────────────────────────────────────────────────────────────────────
run_agent(
    session_id="SES-001",
    ticket=(
        "Hi, I see a charge of $4,200 on my account for this month. "
        "My customer ID is cust_9182 and I was expecting to be charged $2,800. "
        "Can you look into this? Invoice is probably INV-2026-0042."
    )
)

run_agent(
    session_id="SES-002",
    ticket=(
        "I want to cancel my account and get a full refund for this month. "
        "Customer ID cust_0001. I'm very unhappy with the service."
    )
)

print(SDK_COMPARISON)

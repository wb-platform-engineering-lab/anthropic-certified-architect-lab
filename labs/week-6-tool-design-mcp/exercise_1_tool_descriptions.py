"""
exercise_1_tool_descriptions.py — Tool Descriptions: Before and After

The tool description is the model's only documentation. One-liner descriptions
copied from function docstrings describe WHAT a tool does but not WHEN to call
it, WHAT each response shape means, or WHAT to do on failure.

This file shows before/after for 4 tools and tests them on 10 tickets, including
CRM timeout scenarios where the agent's behaviour diverges significantly between
the old and new descriptions.

Key lesson: A vague description lets the model proceed when the system is down.
A four-part description tells the model exactly when to escalate.
"""

import json
import random
from typing import Optional
import anthropic
from dotenv import load_dotenv

load_dotenv()

client = anthropic.Anthropic()

MODEL = "claude-haiku-4-5-20251001"

# ---------------------------------------------------------------------------
# Four-part description template
# ---------------------------------------------------------------------------

DESCRIPTION_TEMPLATE = """
Four-part tool description template:
  1. WHAT: One sentence on what the tool does.
  2. WHEN: When to call it — and when NOT to call it.
  3. SHAPES: What each response status means (success / access_failure / empty).
  4. ON FAILURE: What the agent must do when status is access_failure.
"""

# ---------------------------------------------------------------------------
# OLD (bad) tool definitions — one-liner descriptions
# ---------------------------------------------------------------------------

OLD_TOOLS = [
    {
        "name": "get_account_status",
        "description": "Gets the account status for a customer.",
        "input_schema": {
            "type": "object",
            "properties": {
                "customer_id": {"type": "string"}
            },
            "required": ["customer_id"]
        }
    },
    {
        "name": "get_billing_history",
        "description": "Gets billing history for a customer.",
        "input_schema": {
            "type": "object",
            "properties": {
                "customer_id": {"type": "string"},
                "limit": {"type": "integer"}
            },
            "required": ["customer_id"]
        }
    },
    {
        "name": "search_knowledge_base",
        "description": "Searches the knowledge base for articles.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"}
            },
            "required": ["query"]
        }
    },
    {
        "name": "draft_reply",
        "description": "Drafts a reply to the customer.",
        "input_schema": {
            "type": "object",
            "properties": {
                "customer_id": {"type": "string"},
                "reply_text": {"type": "string"}
            },
            "required": ["customer_id", "reply_text"]
        }
    }
]

# ---------------------------------------------------------------------------
# NEW (good) tool definitions — four-part descriptions
# ---------------------------------------------------------------------------

NEW_TOOLS = [
    {
        "name": "get_account_status",
        "description": (
            "WHAT: Retrieves the current account status, plan tier, and authentication "
            "state for a customer from the CRM system.\n"
            "WHEN: Call this first before any account-related action. Do NOT call this "
            "more than once per ticket — the result is cached for the session.\n"
            "SHAPES:\n"
            "  status=success: account found, payload contains plan, active, auth_state.\n"
            "  status=empty: no CRM record for this customer_id. Do NOT proceed — ask "
            "    the customer to verify their account email before continuing.\n"
            "  status=access_failure: CRM system is unavailable. ESCALATE immediately "
            "    to the technical team. Do NOT draft a reply without this data.\n"
            "ON FAILURE: If status is access_failure, stop and escalate. Never tell "
            "a customer their account is fine when you could not verify it."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "customer_id": {"type": "string", "description": "The customer account identifier."}
            },
            "required": ["customer_id"]
        }
    },
    {
        "name": "get_billing_history",
        "description": (
            "WHAT: Retrieves the most recent invoices and charge history for a customer.\n"
            "WHEN: Call this only for billing-related tickets (duplicate charges, refund "
            "requests, subscription questions). Requires get_account_status to have "
            "returned status=success first — billing data is unreliable without a "
            "confirmed account.\n"
            "SHAPES:\n"
            "  status=success: charges found, payload contains invoices list and "
            "    duplicate_charge flag.\n"
            "  status=empty: no billing records exist for this customer. This means "
            "    the customer has never been charged — tell them no charges are on file.\n"
            "  status=access_failure: billing system unavailable. Escalate to the "
            "    billing team with ticket ID.\n"
            "ON FAILURE: Do not tell a customer their billing is fine if you received "
            "access_failure. Escalate."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "customer_id": {"type": "string"},
                "limit": {"type": "integer", "description": "Max records to return (default 10, max 50)."}
            },
            "required": ["customer_id"]
        }
    },
    {
        "name": "search_knowledge_base",
        "description": (
            "WHAT: Full-text search of the Resolve knowledge base for help articles, "
            "troubleshooting guides, and policy documents.\n"
            "WHEN: Call this for any ticket involving a how-to question, error message, "
            "or product feature. Call this BEFORE drafting a reply — the reply should "
            "cite the relevant article when one exists.\n"
            "SHAPES:\n"
            "  status=success: articles found, payload contains articles list with "
            "    title, url, and relevance_score.\n"
            "  status=empty: no matching articles. Proceed to draft a reply from "
            "    general knowledge, but note that no KB article was found.\n"
            "  status=access_failure: KB system unavailable. Draft the reply from "
            "    general knowledge and note the KB was unreachable.\n"
            "ON FAILURE: access_failure here is non-blocking — draft the reply anyway, "
            "but include a note that the KB was unavailable."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "The search query. Use keywords from the customer's ticket."}
            },
            "required": ["query"]
        }
    },
    {
        "name": "draft_reply",
        "description": (
            "WHAT: Drafts a customer-facing reply based on the account facts and "
            "knowledge base articles gathered in this session.\n"
            "WHEN: Call this LAST — only after get_account_status has returned "
            "status=success (or the ticket is purely informational). NEVER call "
            "draft_reply if get_account_status returned access_failure or if the "
            "customer's account could not be verified. This tool should be called "
            "at most once per ticket.\n"
            "SHAPES:\n"
            "  status=success: draft created, payload contains reply_text.\n"
            "  status=access_failure: drafting system failed. Return the draft manually.\n"
            "ON FAILURE: If this tool fails, compose the reply inline in your response "
            "rather than failing the ticket."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "customer_id": {"type": "string"},
                "reply_text": {"type": "string", "description": "The full customer-facing reply text."},
                "tone": {"type": "string", "enum": ["empathetic", "professional", "brief"], "description": "Tone of the reply."}
            },
            "required": ["customer_id", "reply_text"]
        }
    }
]

# ---------------------------------------------------------------------------
# Tool implementations (simulated)
# ---------------------------------------------------------------------------

def get_account_status(customer_id: str, simulate_failure: bool = False) -> dict:
    if simulate_failure:
        return {
            "status": "access_failure",
            "code": "CRM_TIMEOUT",
            "message": "CRM system did not respond within 5 seconds."
        }
    accounts = {
        "cust_001": {"status": "success", "plan": "Pro", "active": True, "auth_state": "verified"},
        "cust_002": {"status": "success", "plan": "Enterprise", "active": True, "auth_state": "verified"},
    }
    return accounts.get(customer_id, {"status": "empty", "message": f"No CRM record for {customer_id}."})


def get_billing_history(customer_id: str, limit: int = 10) -> dict:
    billing = {
        "cust_001": {
            "status": "success",
            "invoices": [{"amount": 49.0, "date": "2026-05-01"}],
            "duplicate_charge": False
        },
        "cust_002": {
            "status": "success",
            "invoices": [{"amount": 299.0, "date": "2026-05-01"}],
            "duplicate_charge": True
        },
    }
    return billing.get(customer_id, {"status": "empty", "message": "No billing records found."})


def search_knowledge_base(query: str) -> dict:
    if "password" in query.lower() or "login" in query.lower():
        return {
            "status": "success",
            "articles": [
                {
                    "title": "Password Reset Guide",
                    "url": "https://help.resolve.app/password-reset",
                    "relevance_score": 0.95
                }
            ]
        }
    return {"status": "empty", "message": "No matching articles found."}


def draft_reply(customer_id: str, reply_text: str, tone: str = "professional") -> dict:
    return {
        "status": "success",
        "reply_text": reply_text,
        "tone": tone,
        "draft_id": f"draft_{customer_id}_{tone[:3]}"
    }


TOOL_REGISTRY = {
    "get_account_status": get_account_status,
    "get_billing_history": get_billing_history,
    "search_knowledge_base": search_knowledge_base,
    "draft_reply": draft_reply,
}

# ---------------------------------------------------------------------------
# Test tickets
# ---------------------------------------------------------------------------

TEST_TICKETS = [
    {"id": "t01", "customer_id": "cust_001", "text": "How do I reset my password?", "crm_fails": False},
    {"id": "t02", "customer_id": "cust_002", "text": "I was charged twice this month.", "crm_fails": False},
    {"id": "t03", "customer_id": "cust_999", "text": "I need to check my account status.", "crm_fails": False},
    {"id": "t04", "customer_id": "cust_001", "text": "What plan am I on?", "crm_fails": True},   # CRM down
    {"id": "t05", "customer_id": "cust_002", "text": "Can I get a refund for last month?", "crm_fails": True},  # CRM down
    {"id": "t06", "customer_id": "cust_001", "text": "I can't log in to the dashboard.", "crm_fails": False},
    {"id": "t07", "customer_id": "cust_002", "text": "Update my billing email to new@company.com", "crm_fails": False},
    {"id": "t08", "customer_id": "cust_001", "text": "What features does the Pro plan include?", "crm_fails": False},
    {"id": "t09", "customer_id": "cust_002", "text": "I need an invoice for last month.", "crm_fails": True},  # CRM down
    {"id": "t10", "customer_id": "cust_001", "text": "How do I cancel my subscription?", "crm_fails": False},
]

# ---------------------------------------------------------------------------
# Agent runner
# ---------------------------------------------------------------------------

def run_agent(ticket: dict, tools: list, label: str) -> dict:
    """
    Run a single ticket through an agentic loop (max 5 iterations).

    Executes tool calls against TOOL_REGISTRY, passing simulate_failure=ticket["crm_fails"]
    when calling get_account_status.

    Returns a result dict with ticket_id, exit_reason, tool_calls list, reply text,
    and whether the agent escalated.
    """
    system_prompt = (
        "You are a Resolve support agent. Process the ticket and use the available tools "
        "to assist the customer. Always check account status before taking action. "
        "If a critical system is unavailable (access_failure), escalate by saying "
        "'ESCALATE: <reason>' and do not draft a reply."
    )

    messages = [
        {
            "role": "user",
            "content": (
                f"Ticket ID: {ticket['id']}\n"
                f"Customer ID: {ticket['customer_id']}\n"
                f"Message: {ticket['text']}"
            )
        }
    ]

    tool_calls_made = []
    reply_text = ""
    exit_reason = "max_iterations"

    for iteration in range(5):
        response = client.messages.create(
            model=MODEL,
            max_tokens=1024,
            system=system_prompt,
            tools=tools,
            messages=messages,
        )

        # Collect any text from this response turn
        for block in response.content:
            if hasattr(block, "text"):
                reply_text = block.text

        if response.stop_reason == "end_turn":
            exit_reason = "end_turn"
            break

        if response.stop_reason != "tool_use":
            exit_reason = response.stop_reason
            break

        # Process tool calls
        tool_results = []
        for block in response.content:
            if block.type != "tool_use":
                continue

            tool_name = block.name
            tool_input = block.input

            # Execute the tool
            if tool_name == "get_account_status":
                result = TOOL_REGISTRY[tool_name](
                    customer_id=tool_input.get("customer_id", ""),
                    simulate_failure=ticket["crm_fails"],
                )
            elif tool_name == "get_billing_history":
                result = TOOL_REGISTRY[tool_name](
                    customer_id=tool_input.get("customer_id", ""),
                    limit=tool_input.get("limit", 10),
                )
            elif tool_name == "search_knowledge_base":
                result = TOOL_REGISTRY[tool_name](
                    query=tool_input.get("query", ""),
                )
            elif tool_name == "draft_reply":
                result = TOOL_REGISTRY[tool_name](
                    customer_id=tool_input.get("customer_id", ""),
                    reply_text=tool_input.get("reply_text", ""),
                    tone=tool_input.get("tone", "professional"),
                )
            else:
                result = {"status": "access_failure", "code": "UNKNOWN_TOOL", "message": f"Tool {tool_name} not found."}

            tool_calls_made.append({"tool": tool_name, "result_status": result.get("status", "unknown")})

            tool_results.append({
                "type": "tool_result",
                "tool_use_id": block.id,
                "content": json.dumps(result),
            })

        # Append assistant response and tool results to messages
        messages.append({"role": "assistant", "content": response.content})
        messages.append({"role": "user", "content": tool_results})

    # Determine if the agent escalated
    escalated = "ESCALATE" in reply_text.upper() or "escalate" in reply_text.lower()

    return {
        "ticket_id": ticket["id"],
        "exit_reason": exit_reason,
        "tool_calls": tool_calls_made,
        "reply": reply_text,
        "escalated": escalated,
    }

# ---------------------------------------------------------------------------
# Comparison runner
# ---------------------------------------------------------------------------

def compare_old_vs_new() -> None:
    """
    Run all 10 tickets through both OLD_TOOLS and NEW_TOOLS.

    For CRM-failure tickets, compare whether the agent escalates
    with old descriptions vs. new four-part descriptions.
    """
    print("=" * 70)
    print("EXERCISE 1: Tool Description Quality — Old vs. New")
    print("=" * 70)
    print()
    print("Running all 10 tickets through OLD_TOOLS and NEW_TOOLS...")
    print("(This may take a moment — each ticket makes real API calls.)")
    print()

    old_results = {}
    new_results = {}

    for ticket in TEST_TICKETS:
        print(f"  Processing {ticket['id']} with OLD_TOOLS...", end=" ", flush=True)
        old_result = run_agent(ticket, OLD_TOOLS, label="OLD")
        old_results[ticket["id"]] = old_result
        print(f"done (exit: {old_result['exit_reason']})")

        print(f"  Processing {ticket['id']} with NEW_TOOLS...", end=" ", flush=True)
        new_result = run_agent(ticket, NEW_TOOLS, label="NEW")
        new_results[ticket["id"]] = new_result
        print(f"done (exit: {new_result['exit_reason']})")

    # Print comparison table for CRM-failure tickets
    crm_failure_tickets = [t for t in TEST_TICKETS if t["crm_fails"]]

    print()
    print("=" * 70)
    print("CRM FAILURE COMPARISON TABLE")
    print("=" * 70)
    print(f"{'Ticket':<10} {'CRM Fails':<12} {'Old: escalated?':<18} {'New: escalated?':<18}")
    print("-" * 60)

    for ticket in crm_failure_tickets:
        tid = ticket["id"]
        old_esc = "Yes" if old_results[tid]["escalated"] else "No"
        new_esc = "Yes" if new_results[tid]["escalated"] else "No"
        print(f"{tid:<10} {'YES':<12} {old_esc:<18} {new_esc:<18}")

    print()
    print("=" * 70)
    print("ALL TICKETS SUMMARY")
    print("=" * 70)
    print(f"{'Ticket':<8} {'CRM?':<6} {'Old tools':<30} {'New tools':<30}")
    print("-" * 76)

    for ticket in TEST_TICKETS:
        tid = ticket["id"]
        crm_flag = "YES" if ticket["crm_fails"] else "no"
        old_calls = [c["tool"] for c in old_results[tid]["tool_calls"]]
        new_calls = [c["tool"] for c in new_results[tid]["tool_calls"]]
        old_summary = f"escalated={old_results[tid]['escalated']} calls={len(old_calls)}"
        new_summary = f"escalated={new_results[tid]['escalated']} calls={len(new_calls)}"
        print(f"{tid:<8} {crm_flag:<6} {old_summary:<30} {new_summary:<30}")

    print()
    print("=" * 70)
    print("DESCRIPTION TEMPLATE REMINDER")
    print("=" * 70)
    print(DESCRIPTION_TEMPLATE)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    compare_old_vs_new()


if __name__ == "__main__":
    main()

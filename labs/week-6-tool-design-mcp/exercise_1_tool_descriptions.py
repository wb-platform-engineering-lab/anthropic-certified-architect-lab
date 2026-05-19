"""
exercise_1_tool_descriptions.py — Tool Descriptions: Before and After

The tool description is the model's only documentation. A one-liner
describes WHAT a tool does — but not WHEN to call it, WHAT each response
shape means, or WHAT to do on failure.

Four-part template:
  WHAT:       One sentence on what the tool does.
  WHEN:       When to call it — and when NOT to call it.
  SHAPES:     What each response status means.
  ON FAILURE: What the agent must do when status is access_failure.

This exercise compares old (one-liner) vs new (four-part) descriptions
on 4 tickets, including 2 where the CRM is down.
"""

import json
from typing import Optional
import anthropic
from dotenv import load_dotenv

load_dotenv()

client = anthropic.Anthropic()
MODEL = "claude-haiku-4-5-20251001"


# ---------------------------------------------------------------------------
# OLD (bad) tool definitions — one-liner descriptions
# ---------------------------------------------------------------------------

OLD_TOOLS = [
    {
        "name": "get_account_status",
        "description": "Gets the account status for a customer.",
        "input_schema": {
            "type": "object",
            "properties": {"customer_id": {"type": "string"}},
            "required": ["customer_id"],
        },
    },
    {
        "name": "draft_reply",
        "description": "Drafts a reply to the customer.",
        "input_schema": {
            "type": "object",
            "properties": {
                "customer_id": {"type": "string"},
                "reply_text": {"type": "string"},
            },
            "required": ["customer_id", "reply_text"],
        },
    },
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
            "WHEN: Call this before any account-related action. Do NOT call more than "
            "once per ticket — the result is cached for the session.\n"
            "SHAPES:\n"
            "  status=success: account found, payload has plan, active, auth_state.\n"
            "  status=empty: no CRM record for this customer_id. Ask the customer to "
            "    verify their account email before continuing.\n"
            "  status=access_failure: CRM unavailable. ESCALATE immediately. "
            "    Do NOT draft a reply without verified account data.\n"
            "ON FAILURE: If status=access_failure, stop and escalate. Never tell a "
            "customer their account is fine when you could not verify it."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "customer_id": {"type": "string", "description": "The customer account identifier."},
            },
            "required": ["customer_id"],
        },
    },
    {
        "name": "draft_reply",
        "description": (
            "WHAT: Drafts a customer-facing reply based on the account data gathered.\n"
            "WHEN: Call this LAST — only after get_account_status has returned "
            "status=success. NEVER call draft_reply if get_account_status returned "
            "access_failure or the account could not be verified.\n"
            "SHAPES:\n"
            "  status=success: draft created, payload has reply_text.\n"
            "  status=access_failure: drafting failed. Compose the reply inline instead.\n"
            "ON FAILURE: If this tool fails, write the reply in your response directly."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "customer_id": {"type": "string"},
                "reply_text": {"type": "string", "description": "The full customer-facing reply."},
            },
            "required": ["customer_id", "reply_text"],
        },
    },
]


# ---------------------------------------------------------------------------
# Simulated tools
# ---------------------------------------------------------------------------

def get_account_status(customer_id: str, crm_down: bool = False) -> dict:
    if crm_down:
        return {
            "status": "access_failure",
            "code": "CRM_TIMEOUT",
            "message": "CRM system did not respond within 5 seconds.",
        }
    accounts = {
        "cust_001": {"status": "success", "plan": "Pro", "active": True, "auth_state": "verified"},
        "cust_002": {"status": "success", "plan": "Enterprise", "active": True, "auth_state": "verified"},
    }
    return accounts.get(customer_id, {"status": "empty", "message": f"No CRM record for {customer_id}."})


def draft_reply(customer_id: str, reply_text: str) -> dict:
    return {"status": "success", "reply_text": reply_text}


TOOL_REGISTRY = {
    "get_account_status": get_account_status,
    "draft_reply": draft_reply,
}


# ---------------------------------------------------------------------------
# Test tickets
# ---------------------------------------------------------------------------

TEST_TICKETS = [
    {"id": "t01", "customer_id": "cust_001", "text": "What plan am I on?", "crm_down": False},
    {"id": "t02", "customer_id": "cust_999", "text": "I need help with my account.", "crm_down": False},
    {"id": "t03", "customer_id": "cust_001", "text": "Is my account active?", "crm_down": True},
    {"id": "t04", "customer_id": "cust_002", "text": "Can I get a refund for last month?", "crm_down": True},
]


# ---------------------------------------------------------------------------
# Agent runner
# ---------------------------------------------------------------------------

def run_agent(ticket: dict, tools: list) -> dict:
    """Run a single ticket through an agentic loop (max 5 iterations)."""
    system = (
        "You are a Resolve support agent. Process the ticket using available tools. "
        "If a critical system is unavailable (access_failure), escalate by saying "
        "'ESCALATE: <reason>' and do not draft a reply."
    )
    messages = [{
        "role": "user",
        "content": f"Ticket {ticket['id']} — Customer {ticket['customer_id']}: {ticket['text']}",
    }]

    tool_calls = []
    reply = ""

    for _ in range(5):
        response = client.messages.create(
            model=MODEL, max_tokens=512, system=system, tools=tools, messages=messages,
        )
        for block in response.content:
            if hasattr(block, "text"):
                reply = block.text
        if response.stop_reason == "end_turn":
            break
        if response.stop_reason != "tool_use":
            break

        results = []
        for block in response.content:
            if block.type != "tool_use":
                continue
            tool_name = block.name
            inp = block.input
            if tool_name == "get_account_status":
                result = TOOL_REGISTRY[tool_name](inp.get("customer_id", ""), ticket["crm_down"])
            elif tool_name == "draft_reply":
                result = TOOL_REGISTRY[tool_name](inp.get("customer_id", ""), inp.get("reply_text", ""))
            else:
                result = {"status": "access_failure", "code": "UNKNOWN_TOOL"}
            tool_calls.append({"tool": tool_name, "status": result.get("status")})
            results.append({"type": "tool_result", "tool_use_id": block.id, "content": json.dumps(result)})

        messages.append({"role": "assistant", "content": response.content})
        messages.append({"role": "user", "content": results})

    return {
        "ticket_id": ticket["id"],
        "tool_calls": tool_calls,
        "reply": reply,
        "escalated": "escalate" in reply.lower(),
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    print("=" * 65)
    print("Exercise 1: Tool Description Quality — Old vs New")
    print("=" * 65)
    print()
    print("OLD: one-liner descriptions ('Gets the account status for a customer.')")
    print("NEW: four-part descriptions (WHAT / WHEN / SHAPES / ON FAILURE)")
    print()

    for ticket in TEST_TICKETS:
        crm_label = " [CRM DOWN]" if ticket["crm_down"] else ""
        print(f"Ticket {ticket['id']}{crm_label}: \"{ticket['text']}\"")

        old = run_agent(ticket, OLD_TOOLS)
        new = run_agent(ticket, NEW_TOOLS)

        old_calls = [c["tool"] for c in old["tool_calls"]]
        new_calls = [c["tool"] for c in new["tool_calls"]]

        print(f"  OLD  escalated={old['escalated']}  tools={old_calls}")
        print(f"  NEW  escalated={new['escalated']}  tools={new_calls}")

        if ticket["crm_down"]:
            if not old["escalated"] and new["escalated"]:
                print(f"  DIFFERENCE — new description caused correct escalation on CRM failure")
            elif old["escalated"] and new["escalated"]:
                print(f"  Both escalated correctly")
            else:
                print(f"  (check results — CRM was down)")
        print()

    print("=" * 65)
    print("Key takeaway:")
    print("  Old descriptions let the agent proceed when CRM returns access_failure.")
    print("  New SHAPES + ON FAILURE sections tell the model exactly what to do.")
    print()
    print("  Four-part template:")
    print("    WHAT:       What the tool does")
    print("    WHEN:       When to call it (and when NOT to)")
    print("    SHAPES:     What success / empty / access_failure each mean")
    print("    ON FAILURE: What action to take on access_failure")
    print("=" * 65)


if __name__ == "__main__":
    main()

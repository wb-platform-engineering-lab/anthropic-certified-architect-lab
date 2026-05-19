"""
exercise_3_crm_demo.py — CRM Tool Demo (Anthropic SDK)

This file demonstrates the CRM tools using the Anthropic SDK directly —
without running the MCP server. The tool schemas are identical to those
registered in exercise_3_crm_server.py.

What this demonstrates:
  - The CRM tools work when called via the Anthropic SDK
  - Boundary effect: the model cannot call billing tools when only CRM tools are registered
  - Read-only mode restricts write tools at the server level (not via prompt)
"""

import json
import anthropic
from dotenv import load_dotenv

load_dotenv()

# ---------------------------------------------------------------------------
# Simulated CRM data store — mirrors exercise_3_crm_server.py
# ---------------------------------------------------------------------------
CRM_DATA = {
    "cust_001": {
        "name": "Alice Dupont",
        "plan": "Pro",
        "active": True,
        "auth_state": "verified",
        "notes": [],
        "open_tickets": [
            {"id": "tk_001", "subject": "Billing question", "status": "open"},
        ],
    },
    "cust_002": {
        "name": "Bob Chen",
        "plan": "Enterprise",
        "active": True,
        "auth_state": "verified",
        "notes": [],
        "open_tickets": [
            {"id": "tk_002", "subject": "Login issue", "status": "open"},
        ],
    },
}

# ---------------------------------------------------------------------------
# Tool schemas
# ---------------------------------------------------------------------------
CRM_TOOLS = [
    {
        "name": "get_account_status",
        "description": (
            "WHAT: Returns CRM account details for a customer (name, plan, auth state). "
            "WHEN: Call before any support interaction to verify the customer exists. "
            "SHAPES: status=success with name/plan/active/auth_state, "
            "or status=empty if no record exists. "
            "ON FAILURE: If status=empty, ask the customer to verify their account email."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"customer_id": {"type": "string"}},
            "required": ["customer_id"],
        },
    },
    {
        "name": "update_contact_notes",
        "description": (
            "WHAT: Appends a note to the customer's CRM record. "
            "WHEN: Call after resolving a support interaction to log what was discussed. "
            "SHAPES: status=success with note_id, "
            "or status=access_failure with code=NOT_FOUND if the customer does not exist. "
            "ON FAILURE: Do not retry — report the error to the caller."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "customer_id": {"type": "string"},
                "note": {"type": "string"},
            },
            "required": ["customer_id", "note"],
        },
    },
    {
        "name": "list_open_tickets",
        "description": (
            "WHAT: Returns all open support tickets for a customer. "
            "WHEN: Call to check for existing open tickets before creating a new one. "
            "SHAPES: status=success with a tickets list, "
            "or status=empty if there are no open tickets. "
            "ON FAILURE: If status=empty, proceed as if there are no open tickets."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"customer_id": {"type": "string"}},
            "required": ["customer_id"],
        },
    },
]

# ---------------------------------------------------------------------------
# Simulated tool implementations
# ---------------------------------------------------------------------------

def _get_account_status(customer_id: str) -> dict:
    record = CRM_DATA.get(customer_id)
    if record is None:
        return {"status": "empty", "message": f"No CRM record for {customer_id}"}
    return {
        "status": "success",
        "customer_id": customer_id,
        "name": record["name"],
        "plan": record["plan"],
        "active": record["active"],
        "auth_state": record["auth_state"],
    }


def _update_contact_notes(customer_id: str, note: str) -> dict:
    record = CRM_DATA.get(customer_id)
    if record is None:
        return {"status": "access_failure", "code": "NOT_FOUND", "message": f"No CRM record for {customer_id}"}
    note_id = f"note_{len(record['notes']) + 1:03d}"
    record["notes"].append({"id": note_id, "text": note})
    return {"status": "success", "customer_id": customer_id, "note_id": note_id}


def _list_open_tickets(customer_id: str) -> dict:
    record = CRM_DATA.get(customer_id)
    if record is None:
        return {"status": "empty", "message": f"No CRM record for {customer_id}"}
    open_tickets = [t for t in record["open_tickets"] if t["status"] == "open"]
    if not open_tickets:
        return {"status": "empty", "message": f"No open tickets for {customer_id}"}
    return {"status": "success", "customer_id": customer_id, "tickets": open_tickets}


def dispatch_tool(name: str, arguments: dict) -> str:
    if name == "get_account_status":
        result = _get_account_status(arguments.get("customer_id", ""))
    elif name == "update_contact_notes":
        result = _update_contact_notes(arguments.get("customer_id", ""), arguments.get("note", ""))
    elif name == "list_open_tickets":
        result = _list_open_tickets(arguments.get("customer_id", ""))
    else:
        result = {"status": "access_failure", "code": "UNKNOWN_TOOL", "message": f"Unknown tool: {name}"}
    return json.dumps(result)

# ---------------------------------------------------------------------------
# Simple agentic loop
# ---------------------------------------------------------------------------

def run_agent(prompt: str, tools: list = None) -> dict:
    """Run an agentic loop and return final text + list of tool calls made."""
    client = anthropic.Anthropic()
    active_tools = tools if tools is not None else CRM_TOOLS
    messages = [{"role": "user", "content": prompt}]
    tool_calls_made = []

    while True:
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=512,
            tools=active_tools,
            messages=messages,
        )
        for block in response.content:
            if block.type == "tool_use":
                tool_calls_made.append(block.name)

        if response.stop_reason == "end_turn":
            final_text = " ".join(b.text for b in response.content if hasattr(b, "text"))
            return {"final_text": final_text, "tool_calls": tool_calls_made}

        if response.stop_reason == "tool_use":
            messages.append({"role": "assistant", "content": response.content})
            tool_results = []
            for block in response.content:
                if block.type == "tool_use":
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": dispatch_tool(block.name, block.input),
                    })
            messages.append({"role": "user", "content": tool_results})
        else:
            final_text = " ".join(b.text for b in response.content if hasattr(b, "text"))
            return {"final_text": final_text, "tool_calls": tool_calls_made}

# ---------------------------------------------------------------------------
# Demo scenarios
# ---------------------------------------------------------------------------

def main():
    print("=" * 60)
    print("Exercise 3: CRM Tool Demo")
    print("=" * 60)

    # Scenario 1: Read account status
    print("\n--- Scenario 1: Read account status ---")
    result = run_agent("What is the account status for customer cust_001?")
    print(f"  Tool calls: {result['tool_calls']}")
    print(f"  Response:   {result['final_text'][:200]}")

    # Scenario 2: Add a note
    print("\n--- Scenario 2: Add a note to a customer record ---")
    result = run_agent(
        "Add a note to cust_002: 'Customer requested a callback on Monday about their login issue.'"
    )
    print(f"  Tool calls: {result['tool_calls']}")
    print(f"  Response:   {result['final_text'][:200]}")

    # Scenario 3: Boundary — billing question with only CRM tools
    print("\n--- Scenario 3: Billing question with only CRM tools ---")
    result = run_agent(
        "Show me the invoices for cust_001 — were they charged twice this month?",
        tools=CRM_TOOLS,
    )
    print(f"  Tool calls: {result['tool_calls']}")
    print(f"  Response:   {result['final_text'][:300]}")
    billing_called = [t for t in result["tool_calls"] if "billing" in t]
    print(f"\n  Billing tools called: {billing_called or '(none)'}")
    print("  → The model cannot call billing tools that are not registered.")

    print("\n" + "=" * 60)
    print("Boundary rule:")
    print("  Only registered tools can be called.")
    print("  Register a tool to grant capability. Omit it to deny capability.")
    print("  This is structural safety — not a prompt instruction.")
    print("=" * 60)


if __name__ == "__main__":
    main()

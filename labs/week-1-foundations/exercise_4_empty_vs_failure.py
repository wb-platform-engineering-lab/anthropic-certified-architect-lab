import anthropic
import json
from dotenv import load_dotenv

load_dotenv()
client = anthropic.Anthropic()

# Version A — the bad version (what Resolve had before the incident)
def get_account_status_bad(customer_id: str) -> dict:
    """Returns {} on error. The model cannot tell if the customer has no data
    or if the CRM is unavailable."""
    crm_available = False  # Simulating a CRM outage

    if not crm_available:
        return {}  # Silent failure — this is the bug

    return {"plan": "enterprise", "open_invoices": 2}

# Version B — the correct version (after the incident)
def get_account_status_good(customer_id: str) -> dict:
    """Returns typed response shapes the model can reason about."""
    crm_available = False  # Same CRM outage

    if not crm_available:
        return {
            "status": "access_failure",
            "code": "CRM_TIMEOUT",
            "message": "CRM is currently unavailable. Do not proceed with billing decisions."
        }

    if customer_id not in ["cust_9182", "cust_0001"]:
        return {
            "status": "empty",
            "message": "No account found for this customer ID."
        }

    return {
        "status": "success",
        "plan": "enterprise",
        "open_invoices": 2,
        "outstanding_amount": 4200.00
    }

tools = [{
    "name": "get_account_status",
    "description": (
        "Retrieve account status from CRM. "
        "Returns a status field: 'success' with account data, "
        "'access_failure' if the CRM is unavailable (do NOT make billing decisions — escalate), "
        "or 'empty' if no account exists for this customer ID."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "customer_id": {"type": "string"}
        },
        "required": ["customer_id"]
    }
}]

def run_with_tool_version(label: str, tool_fn):
    print(f"\n{'='*60}")
    print(f"Version: {label}")

    messages = [
        {"role": "user", "content": "I think I have an unpaid invoice. Customer ID is cust_9182."}
    ]

    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=512,
        tools=tools,
        messages=messages
    )

    if response.stop_reason == "tool_use":
        messages.append({"role": "assistant", "content": response.content})

        for block in response.content:
            if block.type == "tool_use":
                result = tool_fn(block.input["customer_id"])
                print(f"Tool returned: {result}")

                messages.append({
                    "role": "user",
                    "content": [{
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": json.dumps(result)
                    }]
                })

        final = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=512,
            tools=tools,
            messages=messages
        )

        for block in final.content:
            if block.type == "text":
                print(f"\nAgent reply:\n{block.text}")

run_with_tool_version("BAD  — {} on CRM failure", get_account_status_bad)
run_with_tool_version("GOOD — typed failure response", get_account_status_good)
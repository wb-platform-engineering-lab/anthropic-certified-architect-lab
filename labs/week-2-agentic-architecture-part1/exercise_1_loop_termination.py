"""
Exercise 1 — The Loop That Terminates Correctly

Demonstrates the difference between a broken loop (natural language detection)
and a correct loop (stop_reason driven). Every stop_reason value is handled
explicitly — there is no else branch that defaults to continuing.
"""
import anthropic
from dotenv import load_dotenv

load_dotenv()
client = anthropic.Anthropic()

TOOLS = [
    {
        "name": "get_account_status",
        "description": "Retrieve account status from the CRM.",
        "input_schema": {
            "type": "object",
            "properties": {
                "customer_id": {"type": "string"}
            },
            "required": ["customer_id"]
        }
    },
    {
        "name": "lookup_invoice",
        "description": "Look up a specific invoice by ID.",
        "input_schema": {
            "type": "object",
            "properties": {
                "invoice_id": {"type": "string"}
            },
            "required": ["invoice_id"]
        }
    }
]

def execute_tool(name: str, inputs: dict) -> dict:
    """Simulated tool execution."""
    if name == "get_account_status":
        return {
            "status": "success",
            "customer_id": inputs["customer_id"],
            "plan": "enterprise",
            "open_invoices": 2,
            "account_status": "active"
        }
    if name == "lookup_invoice":
        return {
            "status": "success",
            "invoice_id": inputs["invoice_id"],
            "amount": 4200.00,
            "paid": False,
            "due_date": "2026-05-01"
        }
    return {"status": "error", "message": f"Unknown tool: {name}"}


# ── BROKEN VERSION ────────────────────────────────────────────────────────────
def broken_loop(ticket: str) -> str:
    """
    Bug: exits when model text contains 'I have enough information' or similar.
    This is non-deterministic and will silently fail when the model phrases
    its conclusion differently.
    """
    import json
    messages = [{"role": "user", "content": ticket}]

    for _ in range(10):
        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=512,
            tools=TOOLS,
            messages=messages
        )

        # ✗ WRONG: checking model language instead of stop_reason
        for block in response.content:
            if hasattr(block, "text") and "enough information" in block.text.lower():
                return block.text  # exits on a phrase that may never appear

        # ✗ WRONG: the else branch below treats tool_use AND max_tokens the same
        if response.stop_reason == "tool_use":
            messages.append({"role": "assistant", "content": response.content})
            tool_results = []
            for block in response.content:
                if block.type == "tool_use":
                    result = execute_tool(block.name, block.input)
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": json.dumps(result)
                    })
            messages.append({"role": "user", "content": tool_results})
        else:
            # ✗ WRONG: handles end_turn AND max_tokens AND unknown values identically
            for block in response.content:
                if hasattr(block, "text"):
                    return block.text
            return "No text response"  # silently succeeds even on truncation

    return "Loop limit reached"


# ── CORRECT VERSION ───────────────────────────────────────────────────────────
def correct_loop(ticket: str) -> dict:
    """
    Every stop_reason is handled explicitly.
    Returns a typed dict so the caller knows WHY the loop exited.
    """
    import json
    messages = [{"role": "user", "content": ticket}]

    for iteration in range(1, 11):
        print(f"  iteration {iteration}")
        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=512,
            tools=TOOLS,
            messages=messages
        )

        print(f"  stop_reason: {response.stop_reason}")

        if response.stop_reason == "end_turn":
            # ✓ Model finished — output is complete
            text = next((b.text for b in response.content if hasattr(b, "text")), "")
            return {"status": "success", "reply": text, "iterations": iteration}

        elif response.stop_reason == "tool_use":
            # ✓ Execute every tool the model requested, then loop
            messages.append({"role": "assistant", "content": response.content})
            tool_results = []
            for block in response.content:
                if block.type == "tool_use":
                    print(f"  tool call: {block.name}({block.input})")
                    result = execute_tool(block.name, block.input)
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": json.dumps(result)
                    })
            messages.append({"role": "user", "content": tool_results})

        elif response.stop_reason == "max_tokens":
            # ✓ Output was cut off — never treat as success
            return {"status": "truncated", "iterations": iteration}

        else:
            # ✓ Unknown stop_reason — fail safe, never continue
            return {"status": "error", "stop_reason": response.stop_reason, "iterations": iteration}

    return {"status": "budget_exhausted", "iterations": 10}


# ── DEMO ──────────────────────────────────────────────────────────────────────
TICKET = (
    "Hi, my invoice INV-2026-0042 shows a charge of $4,200 but I thought "
    "I was on the starter plan. Customer ID is cust_9182. Can you investigate?"
)

print("=" * 60)
print("CORRECT LOOP")
print("=" * 60)
result = correct_loop(TICKET)
print(f"\nExit status : {result['status']}")
print(f"Iterations  : {result['iterations']}")
if result["status"] == "success":
    print(f"Reply       : {result['reply'][:200]}...")

print()
print("Key takeaway:")
print("  stop_reason drives termination. Model language does not.")
print("  Every branch is explicit. There is no silent success path.")

import anthropic
import json
from dotenv import load_dotenv

load_dotenv()
client = anthropic.Anthropic()

# Simulate the Resolve CRM — this is a Python function, not Claude
def get_account_status(customer_id: str) -> dict:
    """Simulated CRM lookup."""
    fake_db = {
        "cust_9182": {
            "name": "Acme Corp",
            "plan": "enterprise",
            "open_invoices": 2,
            "outstanding_amount": 4200.00,
            "status": "active"
        },
        "cust_0001": {
            "name": "New Customer Ltd",
            "plan": "starter",
            "open_invoices": 0,
            "outstanding_amount": 0,
            "status": "active"
        }
    }
    return fake_db.get(customer_id, {})  # <-- we'll revisit this return value in Exercise 4

tools = [{
    "name": "get_account_status",
    "description": (
        "Retrieve the current account status for a customer from the CRM, "
        "including their subscription plan, number of open invoices, and "
        "outstanding balance. Call this before responding to any billing inquiry."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "customer_id": {
                "type": "string",
                "description": "The customer's unique identifier (format: cust_XXXX)."
            }
        },
        "required": ["customer_id"]
    }
}]

def run_agent(customer_id: str, ticket_text: str):
    print(f"\n{'='*60}")
    print(f"Ticket: {ticket_text}")
    print(f"Customer ID: {customer_id}")

    messages = [{"role": "user", "content": ticket_text}]
    iteration = 0
    max_iterations = 5  # Guard against infinite loops

    while iteration < max_iterations:
        iteration += 1
        print(f"\n--- Iteration {iteration} ---")

        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=512,
            tools=tools,
            system=f"You are a support agent for Resolve. The current ticket is from customer ID {customer_id}. Always check their account status before responding to billing questions.",
            messages=messages
        )

        print(f"stop_reason: {response.stop_reason}")

        # The loop terminates when the model has finished — not when it says so
        if response.stop_reason == "end_turn":
            print("\n✓ Agent finished.")
            for block in response.content:
                if block.type == "text":
                    print(f"\nFinal reply:\n{block.text}")
            break

        # Handle tool calls
        if response.stop_reason == "tool_use":
            # Add the assistant's response (with tool use blocks) to message history
            messages.append({"role": "assistant", "content": response.content})

            tool_results = []
            for block in response.content:
                if block.type == "tool_use":
                    print(f"Tool call: {block.name}({block.input})")

                    # Execute the actual tool
                    if block.name == "get_account_status":
                        result = get_account_status(block.input["customer_id"])
                    else:
                        result = {"error": "unknown tool"}

                    print(f"Tool result: {result}")

                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": json.dumps(result)
                    })

            # Add tool results to message history
            messages.append({"role": "user", "content": tool_results})

        # Unexpected stop_reason — treat as failure, not success
        elif response.stop_reason == "max_tokens":
            print("✗ Hit token limit mid-response. Escalating.")
            break

    else:
        print(f"✗ Max iterations ({max_iterations}) reached. Escalating.")


# Run two scenarios
run_agent("cust_9182", "Hi, I received an invoice but I thought I already paid. Can you check?")
run_agent("cust_0001", "What plan am I currently on?")
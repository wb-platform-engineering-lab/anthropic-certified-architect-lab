import anthropic
from dotenv import load_dotenv

load_dotenv()
client = anthropic.Anthropic()

# Helper to display what we care about
def show_response(label: str, response):
    print(f"\n{'='*50}")
    print(f"Scenario: {label}")
    print(f"stop_reason: {response.stop_reason}")
    print(f"content blocks: {[b.type for b in response.content]}")

# --- Scenario A: Normal completion ---
r = client.messages.create(
    model="claude-sonnet-4-6",
    max_tokens=64,
    messages=[{"role": "user", "content": "Say 'done' and nothing else."}]
)
show_response("Normal completion", r)

# --- Scenario B: Token limit hit ---
r = client.messages.create(
    model="claude-sonnet-4-6",
    max_tokens=5,
    messages=[{"role": "user", "content": "Write a paragraph about support tickets."}]
)
show_response("Token limit hit (max_tokens)", r)

# --- Scenario C: Tool use triggered ---
r = client.messages.create(
    model="claude-sonnet-4-6",
    max_tokens=256,
    tools=[{
        "name": "get_account_status",
        "description": "Retrieve the customer's account status from the CRM.",
        "input_schema": {
            "type": "object",
            "properties": {
                "customer_id": {"type": "string", "description": "The customer's unique ID."}
            },
            "required": ["customer_id"]
        }
    }],
    messages=[{
        "role": "user",
        "content": "Check the account status for customer ID cust_9182 and tell me if they have any open invoices."
    }]
)
show_response("Tool use triggered", r)

# Identify the tool call details when stop_reason is tool_use
if r.stop_reason == "tool_use":
    for block in r.content:
        if block.type == "tool_use":
            print(f"\nTool called: {block.name}")
            print(f"Tool input:  {block.input}")
            print(f"Tool use id: {block.id}")
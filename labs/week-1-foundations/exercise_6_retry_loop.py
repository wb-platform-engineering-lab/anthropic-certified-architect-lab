import anthropic
import json
from dotenv import load_dotenv

load_dotenv()
client = anthropic.Anthropic()

def classify_ticket_with_retry(ticket: str, max_retries: int = 3) -> dict | None:
    """
    Classify a ticket using tool_use with a validation-retry loop.

    Returns the classification dict on success, None on exhausted retries.
    This is the pattern for Domain 3 + Domain 5 overlap questions on the exam.
    """
    messages = [{"role": "user", "content": ticket}]
    tool_def = {
        "name": "classify_ticket",
        "description": "Classify the support ticket. Confidence must be between 0.0 and 1.0.",
        "input_schema": {
            "type": "object",
            "properties": {
                "decision": {
                    "type": "string",
                    "enum": ["auto_resolve", "escalate"]
                },
                "confidence": {
                    "type": "number"
                },
                "reason": {
                    "type": "string"
                }
            },
            "required": ["decision", "confidence", "reason"]
        }
    }

    for attempt in range(1, max_retries + 1):
        print(f"\n--- Attempt {attempt}/{max_retries} ---")

        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=256,
            tools=[tool_def],
            tool_choice={"type": "tool", "name": "classify_ticket"},
            messages=messages
        )

        # Extract tool call
        tool_block = next((b for b in response.content if b.type == "tool_use"), None)
        if tool_block is None:
            print(f"✗ No tool_use block in response. stop_reason={response.stop_reason}")
            # Add a correction message and retry
            messages.append({"role": "assistant", "content": response.content})
            messages.append({
                "role": "user",
                "content": "You must call the classify_ticket tool. Please try again."
            })
            continue

        result = tool_block.input
        print(f"Raw tool input: {result}")

        # Validate beyond what the schema enforces
        # (JSON schema can't enforce numeric ranges, cross-field rules, etc.)
        errors = []
        if not (0.0 <= result.get("confidence", -1) <= 1.0):
            errors.append(f"confidence must be 0.0–1.0, got {result.get('confidence')}")
        if result.get("decision") == "escalate" and not result.get("escalation_team"):
            # This is a cross-field rule the schema can't express
            pass  # We'd check this if escalation_team were required conditionally

        if errors:
            print(f"✗ Validation failed: {errors}")
            # Feed the error back as a tool result with explicit correction
            messages.append({"role": "assistant", "content": response.content})
            messages.append({
                "role": "user",
                "content": [{
                    "type": "tool_result",
                    "tool_use_id": tool_block.id,
                    "content": json.dumps({
                        "status": "validation_error",
                        "errors": errors,
                        "instruction": "Correct the errors and call classify_ticket again."
                    }),
                    "is_error": True
                }]
            })
            continue

        print(f"✓ Valid classification: decision={result['decision']}, confidence={result['confidence']:.0%}")
        return result

    print(f"\n✗ Exhausted {max_retries} retries. Escalating to human review.")
    return None


# Test it
tickets = [
    "I can't log in to my account — it says my password is incorrect.",
    "I was charged twice this month. Invoice #INV-2024-0391 appears twice on my statement.",
]

for ticket in tickets:
    print(f"\n{'='*60}")
    print(f"Ticket: {ticket}")
    result = classify_ticket_with_retry(ticket)
    if result:
        print(f"\nFinal: {result}")
    else:
        print("\nFinal: Escalated to human review queue.")
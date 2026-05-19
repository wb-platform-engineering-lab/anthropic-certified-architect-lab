"""
exercise_4_validation_retry.py — The Validation-Retry Loop

tool_use ensures the model calls the tool and fills all required fields.
It does NOT enforce cross-field business rules. When a rule is violated,
we need to tell the model what went wrong and let it try again.

The pattern:
  1. Call API → get tool input
  2. Validate business rules
  3. PASS → return the result
  4. FAIL → send the error back as a tool_result with is_error=True
  5. Retry (up to MAX_RETRIES times)
  6. Still failing → return {"status": "escalation", "reason": "validation_exhausted"}

The key mechanism: is_error=True in a tool_result signals to the model
that its answer was wrong. Without this flag, the model has no reason to
change its answer on the next turn.
"""

import json
from typing import Optional
import anthropic
from dotenv import load_dotenv

load_dotenv()
client = anthropic.Anthropic()

MAX_RETRIES = 3


# =============================================================================
# The tool (same schema as Exercises 1–3)
# =============================================================================

CLASSIFY_TOOL = {
    "name": "classify_ticket",
    "description": (
        "Classify a support ticket. decision must be one of the three enum values. "
        "escalation_team: non-null when escalating, null otherwise. "
        "resolution: non-null when auto-resolving, null otherwise."
    ),
    "input_schema": {
        "type": "object",
        "required": ["decision", "confidence", "reason", "escalation_team", "resolution"],
        "properties": {
            "decision": {
                "type": "string",
                "enum": ["auto_resolve", "escalate", "needs_info"],
            },
            "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
            "reason": {"type": "string"},
            "escalation_team": {
                "type": ["string", "null"],
                "enum": ["billing", "technical", "enterprise", "legal", None],
            },
            "resolution": {"type": ["string", "null"]},
        },
    },
}


# =============================================================================
# Business rule validation (same rules as Exercise 2)
# =============================================================================

def validate(result: dict) -> Optional[str]:
    """
    Check cross-field business rules.
    Returns an error message if a rule is violated, None if all pass.
    The message is sent back to the model in the retry — make it specific.
    """
    decision = result.get("decision")
    confidence = result.get("confidence", 1.0)
    escalation_team = result.get("escalation_team")

    if decision == "auto_resolve" and confidence < 0.6:
        return (
            f"confidence is {confidence:.2f} but auto_resolve requires >= 0.6. "
            "Either increase confidence (if genuinely certain) or change decision "
            "to 'needs_info'."
        )

    if decision == "escalate" and escalation_team is None:
        return (
            "escalation_team is null but decision is 'escalate'. "
            "Set escalation_team to one of: billing, technical, enterprise, legal."
        )

    return None


# =============================================================================
# The retry loop
# =============================================================================

def classify_with_retry(ticket: str) -> dict:
    """
    Classify a ticket with validation and retry on rule violations.

    Returns:
      {"status": "success", "decision": ..., "confidence": ..., ...}
      or
      {"status": "escalation", "reason": "validation_exhausted"}
    """
    messages = [{"role": "user", "content": ticket}]
    attempts = []

    for attempt in range(1, MAX_RETRIES + 1):
        # Step 1: call the API
        try:
            response = client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=512,
                tools=[CLASSIFY_TOOL],
                tool_choice={"type": "tool", "name": "classify_ticket"},
                messages=messages,
            )
        except Exception as e:
            return {"status": "escalation", "reason": f"api_error: {e}"}

        if response.stop_reason != "tool_use":
            return {"status": "escalation", "reason": f"unexpected stop_reason={response.stop_reason!r}"}

        tool_block = response.content[0]
        result = tool_block.input

        # Step 2: validate business rules
        error_msg = validate(result)
        attempts.append({
            "attempt": attempt,
            "decision": result.get("decision"),
            "confidence": result.get("confidence"),
            "error": error_msg,
        })

        # Step 3: passed — return success
        if error_msg is None:
            return {
                "status": "success",
                "attempts": attempt,
                "decision": result.get("decision"),
                "confidence": result.get("confidence"),
                "reason": result.get("reason"),
                "escalation_team": result.get("escalation_team"),
                "resolution": result.get("resolution"),
                "history": attempts,
            }

        # Step 4: failed — send error back and retry
        # Append the assistant's tool call (required for message history continuity)
        messages.append({"role": "assistant", "content": response.content})

        # Append the error as a tool_result with is_error=True
        # is_error=True signals to the model: "your answer was rejected, try again"
        messages.append({
            "role": "user",
            "content": [{
                "type": "tool_result",
                "tool_use_id": tool_block.id,
                "is_error": True,
                "content": json.dumps({
                    "error": True,
                    "message": error_msg,
                }),
            }],
        })

    # All retries exhausted
    return {
        "status": "escalation",
        "reason": "validation_exhausted",
        "attempts": MAX_RETRIES,
        "history": attempts,
    }


# =============================================================================
# Test tickets
# =============================================================================

TICKETS = [
    {
        "label": "clean ticket (should pass on attempt 1)",
        "ticket": (
            "I was charged €750 last month that I never authorized. "
            "I want a full refund immediately."
        ),
    },
    {
        "label": "ticket that may trigger low-confidence auto_resolve",
        "ticket": (
            "Hey maybe something's off with my account? "
            "Not urgent, just wanted to flag it I guess."
        ),
    },
    {
        "label": "ticket that may trigger missing escalation_team",
        "ticket": (
            "I need to speak to someone about a legal matter regarding my data. "
            "Please escalate this."
        ),
    },
]


# =============================================================================
# main
# =============================================================================

def main() -> None:
    print("=" * 65)
    print("Exercise 4: The Validation-Retry Loop")
    print("=" * 65)
    print()
    print(f"MAX_RETRIES = {MAX_RETRIES}")
    print("Errors are sent back as tool_result with is_error=True.")
    print()

    for item in TICKETS:
        label = item["label"]
        ticket = item["ticket"]

        print(f"Ticket: {label}")
        print(f"  \"{ticket[:70]}{'...' if len(ticket) > 70 else ''}\"")
        print()

        result = classify_with_retry(ticket)

        if result["status"] == "success":
            print(f"  Status:    success (passed on attempt {result['attempts']})")
            print(f"  Decision:  {result['decision']!r}")
            print(f"  Confidence:{result['confidence']}")
        else:
            print(f"  Status:    {result['status']}")
            print(f"  Reason:    {result.get('reason')}")

        # Print the attempt history
        history = result.get("history", [])
        if history:
            print()
            for h in history:
                status = "PASS" if h["error"] is None else "FAIL"
                print(f"  Attempt {h['attempt']}: decision={h['decision']!r}, "
                      f"confidence={h['confidence']}, {status}")
                if h["error"]:
                    print(f"             Error sent: \"{h['error'][:70]}\"")

        print()

    print("=" * 65)
    print("Key takeaway:")
    print("  is_error=True in the tool_result is the signal that tells the model")
    print("  'your answer was rejected — here is what was wrong, try again'.")
    print("  Without is_error=True, the model has no reason to change its answer.")
    print()
    print("  After MAX_RETRIES failures, return a typed escalation result.")
    print("  Do not keep retrying — if the model cannot satisfy the rules in")
    print(f"  {MAX_RETRIES} attempts, a human needs to handle this ticket.")
    print("=" * 65)


if __name__ == "__main__":
    main()

"""
exercise_2_tool_use_enforcement.py — What tool_choice Guarantees (and What It Doesn't)

tool_choice={"type": "tool", "name": "classify_ticket"} forces the model to
call that tool. stop_reason will always be "tool_use". The model cannot reply
with prose instead.

But three things can still go wrong even with tool_choice. This file shows
each one and its handler.

  1. max_tokens too low  → tool call JSON is truncated, stop_reason = "max_tokens"
  2. Business rule fail  → schema passes but cross-field logic is violated
  3. Input too long      → pre-flight check needed before calling the API
"""

import json
from typing import Optional
import anthropic
from dotenv import load_dotenv

load_dotenv()
client = anthropic.Anthropic()


# =============================================================================
# The tool (same schema as Exercise 1)
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


def classify(ticket: str, max_tokens: int = 512) -> dict:
    """Standard classification. Returns tool input dict or {"error": ...}."""
    try:
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=max_tokens,
            tools=[CLASSIFY_TOOL],
            tool_choice={"type": "tool", "name": "classify_ticket"},
            messages=[{"role": "user", "content": ticket}],
        )
        if response.stop_reason != "tool_use":
            return {"error": f"stop_reason={response.stop_reason!r} (expected 'tool_use')"}
        return response.content[0].input
    except Exception as e:
        return {"error": str(e)}


# =============================================================================
# Problem 1 — max_tokens too low
# =============================================================================

def demo_max_tokens_problem(ticket: str) -> None:
    """
    With max_tokens=20, the tool call JSON is truncated mid-stream.
    stop_reason becomes "max_tokens" instead of "tool_use".
    The tool input is incomplete or missing.

    Fix: always use max_tokens >= 512 for tool calls.
    If you get stop_reason="max_tokens", retry with a larger budget.
    """
    print("--- Problem 1: max_tokens too low ---")

    # Intentionally too low
    response = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=20,
        tools=[CLASSIFY_TOOL],
        tool_choice={"type": "tool", "name": "classify_ticket"},
        messages=[{"role": "user", "content": ticket}],
    )
    print(f"  With max_tokens=20:  stop_reason = {response.stop_reason!r}")
    print(f"  content blocks:      {[b.type for b in response.content]}")
    print()

    # Retry with enough tokens
    result = classify(ticket, max_tokens=512)
    if "error" not in result:
        print(f"  Retry with 512:      stop_reason = 'tool_use', decision = {result['decision']!r}")
    else:
        print(f"  Retry error: {result['error']}")
    print()
    print("  Fix: detect stop_reason='max_tokens' and retry with max_tokens >= 512")
    print()


# =============================================================================
# Problem 2 — business rule violation (cross-field logic)
# =============================================================================

def validate_business_rules(result: dict) -> Optional[str]:
    """
    JSON Schema enforces each field independently.
    It cannot enforce cross-field rules like "confidence must be >= 0.6
    when decision is auto_resolve".

    This function checks those rules after schema validation has passed.
    Returns an error message if a rule is violated, None if all rules pass.
    """
    decision = result.get("decision")
    confidence = result.get("confidence", 1.0)
    escalation_team = result.get("escalation_team")

    # Rule: auto_resolve needs confidence >= 0.6
    if decision == "auto_resolve" and confidence < 0.6:
        return (
            f"auto_resolve with confidence={confidence:.2f} is too low. "
            f"Minimum is 0.6. Change decision to 'needs_info' or raise confidence."
        )

    # Rule: escalate needs a team
    if decision == "escalate" and escalation_team is None:
        return "escalate requires escalation_team to be non-null."

    return None  # all rules pass


def demo_business_rules(ticket: str) -> None:
    """
    Even with a valid schema response, cross-field business rules can fail.
    The validator runs after the API call, before routing.
    """
    print("--- Problem 2: business rule violation ---")
    result = classify(ticket)
    if "error" in result:
        print(f"  API error: {result['error']}")
        return

    violation = validate_business_rules(result)
    print(f"  decision:    {result.get('decision')!r}")
    print(f"  confidence:  {result.get('confidence')}")
    print(f"  Schema OK?   yes (enum and range enforced by API)")

    if violation:
        print(f"  Rule check:  FAIL — {violation}")
        print("  Action:      send structured error back to model, retry")
    else:
        print(f"  Rule check:  PASS — logically consistent")
    print()


# =============================================================================
# Problem 3 — input too long
# =============================================================================

def classify_with_preflight(ticket: str) -> dict:
    """
    For very long inputs (pasted email threads, log dumps), estimate the token
    count before calling the API. Truncate if needed to avoid a 400 error.

    Heuristic: ~4 characters per token. For production, use
    client.beta.messages.count_tokens() for an exact count.
    """
    MODEL_CONTEXT = 200_000  # claude-haiku-4-5 context window
    OUTPUT_BUDGET = 512       # tokens reserved for the tool call response
    MAX_INPUT = MODEL_CONTEXT - OUTPUT_BUDGET

    estimated = len(ticket) // 4
    was_truncated = False

    if estimated > MAX_INPUT:
        # Keep first 80% (original issue) + last 20% (most recent message)
        char_limit = MAX_INPUT * 4
        head = ticket[:int(char_limit * 0.8)]
        tail = ticket[int(-char_limit * 0.2):]
        ticket = head + "\n[... truncated ...]\n" + tail
        was_truncated = True

    result = classify(ticket)
    result["was_truncated"] = was_truncated
    result["estimated_tokens"] = estimated
    return result


def demo_preflight() -> None:
    """Show the pre-flight check truncating a very long ticket."""
    print("--- Problem 3: input too long ---")

    # Simulate a 500-message email thread pasted into a ticket
    long_ticket = ("Customer: still not resolved. Support: looking into it. ") * 4000

    estimated = len(long_ticket) // 4
    print(f"  Ticket size: ~{estimated:,} estimated tokens")
    print(f"  Calling classify_with_preflight()...")

    result = classify_with_preflight(long_ticket)
    print(f"  was_truncated: {result.get('was_truncated')}")
    print(f"  decision:      {result.get('decision', result.get('error', 'error'))!r}")
    print()
    print("  Fix: estimate tokens before the call, truncate head+tail if needed")
    print()


# =============================================================================
# main
# =============================================================================

def main() -> None:
    ticket = (
        "My payment failed three times today but I can see the charges on my "
        "bank statement. I need this resolved — I have a meeting in two hours."
    )

    ambiguous_ticket = (
        "Hey, I'm not sure if my account is working right? "
        "Sometimes it logs me out. Maybe my browser? Not urgent."
    )

    print("=" * 65)
    print("Exercise 2: What tool_choice Guarantees (and What It Doesn't)")
    print("=" * 65)
    print()
    print("tool_choice guarantees stop_reason='tool_use' and a schema-valid")
    print("tool input. These three problems survive that guarantee.")
    print()

    demo_max_tokens_problem(ticket)
    demo_business_rules(ambiguous_ticket)
    demo_preflight()

    print("=" * 65)
    print("Summary:")
    print("  1. max_tokens too low → retry with >= 512")
    print("  2. Business rules     → validate cross-field logic after the API call")
    print("  3. Input too long     → estimate tokens, truncate before calling")
    print("=" * 65)


if __name__ == "__main__":
    main()

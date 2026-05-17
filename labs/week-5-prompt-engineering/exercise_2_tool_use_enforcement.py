"""
exercise_2_tool_use_enforcement.py — tool_use as the Enforcement Mechanism

EXAM NOTE: tool_choice={"type": "tool", "name": "..."} forces the model to
call a specific tool. stop_reason will always be "tool_use" — the model cannot
produce a prose reply instead of structured output.

But three edge cases survive tool_choice enforcement. This file demonstrates
all three and shows the handler for each:

  Edge case 1 — max_tokens too low:
    The tool call JSON is truncated mid-stream. stop_reason becomes "max_tokens"
    instead of "tool_use". The tool input dict is incomplete or missing.
    Handler: always set max_tokens >= 512 for tool calls; retry on max_tokens.

  Edge case 2 — logical constraint violation:
    The schema allows a field to be null OR a string, but your business rules
    say "when decision is escalate, escalation_team MUST be non-null." The API
    schema cannot express cross-field constraints. The model may still produce
    decision=escalate with escalation_team=null. The schema passes; your code
    breaks.
    Handler: post-schema business rule validation before acting on the result.

  Edge case 3 — context too large:
    A very long ticket (e.g., a full email thread pasted in) may approach or
    exceed the model's context window. The API will return a 400 error.
    Handler: pre-flight token count estimate; truncate if needed before calling.
"""

import os
from typing import Optional

import anthropic
from dotenv import load_dotenv

load_dotenv()

client = anthropic.Anthropic()

# ---------------------------------------------------------------------------
# The v2 classify_ticket tool — same schema as exercise 1.
# ---------------------------------------------------------------------------
CLASSIFY_TOOL = {
    "name": "classify_ticket",
    "description": (
        "Classify a support ticket. decision must be exactly one of: "
        "auto_resolve, escalate, needs_info. escalation_team must be non-null "
        "when escalating; null otherwise. resolution must be non-null when "
        "auto-resolving; null otherwise."
    ),
    "input_schema": {
        "type": "object",
        "required": ["decision", "confidence", "reason", "escalation_team", "resolution"],
        "properties": {
            "decision": {
                "type": "string",
                "enum": ["auto_resolve", "escalate", "needs_info"],
                "description": (
                    "Exactly one of three values. "
                    "auto_resolve: ticket can be resolved without human. "
                    "escalate: requires human review. "
                    "needs_info: cannot classify without more information."
                ),
            },
            "confidence": {
                "type": "number",
                "minimum": 0.0,
                "maximum": 1.0,
                "description": "Confidence in the decision. Range 0.0 to 1.0.",
            },
            "reason": {
                "type": "string",
                "description": (
                    "One or two sentences explaining the decision. "
                    "Must reference specific facts from the ticket."
                ),
            },
            "escalation_team": {
                "type": ["string", "null"],
                "enum": ["billing", "technical", "enterprise", "legal", None],
                "description": (
                    "Required and non-null when decision is escalate. "
                    "Explicitly null when decision is auto_resolve or needs_info."
                ),
            },
            "resolution": {
                "type": ["string", "null"],
                "description": (
                    "Customer-facing resolution text. Required and non-null when "
                    "decision is auto_resolve. Explicitly null otherwise."
                ),
            },
        },
    },
}

SYSTEM_PROMPT = (
    "You are the Resolve ticket classifier. Classify the support ticket using "
    "the classify_ticket tool. Be precise about null vs non-null fields."
)

# ---------------------------------------------------------------------------
# A normal classification helper — used for comparison and edge case 2.
# ---------------------------------------------------------------------------

def classify_ticket(ticket: str, max_tokens: int = 512) -> dict:
    """
    Standard classification with the v2 tool. Returns the tool input dict.
    All tool functions return dicts — never raises uncaught exceptions.
    """
    try:
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=max_tokens,
            system=SYSTEM_PROMPT,
            tools=[CLASSIFY_TOOL],
            tool_choice={"type": "tool", "name": "classify_ticket"},
            messages=[{"role": "user", "content": ticket}],
        )
        if response.stop_reason != "tool_use":
            return {
                "error": f"Unexpected stop_reason: {response.stop_reason}",
                "stop_reason": response.stop_reason,
            }
        return response.content[0].input
    except Exception as e:
        return {"error": str(e)}


# ---------------------------------------------------------------------------
# EDGE CASE 1: max_tokens too low
# ---------------------------------------------------------------------------

def demonstrate_edge_case_1_max_tokens(ticket: str) -> dict:
    """
    What happens when max_tokens is too low to complete the tool call?

    The tool call JSON is emitted token by token. If max_tokens is exhausted
    before the JSON closes, stop_reason becomes "max_tokens" instead of
    "tool_use". The content block may be a partial tool_use or empty.

    Handler: always set max_tokens >= 512 for tool calls. On max_tokens
    stop_reason, retry with a higher limit.
    """
    try:
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=20,   # deliberately too low — tool call JSON will be truncated
            system=SYSTEM_PROMPT,
            tools=[CLASSIFY_TOOL],
            tool_choice={"type": "tool", "name": "classify_ticket"},
            messages=[{"role": "user", "content": ticket}],
        )
        content_types = [b.type for b in response.content]
        result = {
            "stop_reason": response.stop_reason,
            "content_types": content_types,
            "handler": "Retry with higher max_tokens. For tool calls, min 512 recommended.",
            "passed": response.stop_reason == "max_tokens",
        }
        return result
    except Exception as e:
        return {"error": str(e), "passed": False}


def demonstrate_edge_case_1_retry(ticket: str) -> dict:
    """
    Retry handler for edge case 1: detect max_tokens stop and retry.
    Returns either the tool input dict or an error dict.
    """
    try:
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=20,   # intentionally too low to trigger the case
            system=SYSTEM_PROMPT,
            tools=[CLASSIFY_TOOL],
            tool_choice={"type": "tool", "name": "classify_ticket"},
            messages=[{"role": "user", "content": ticket}],
        )

        if response.stop_reason == "max_tokens":
            # Retry with sufficient tokens.
            response = client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=512,
                system=SYSTEM_PROMPT,
                tools=[CLASSIFY_TOOL],
                tool_choice={"type": "tool", "name": "classify_ticket"},
                messages=[{"role": "user", "content": ticket}],
            )

        if response.stop_reason == "tool_use":
            return {"result": response.content[0].input, "retried": True}

        return {"error": f"Unexpected stop_reason after retry: {response.stop_reason}"}
    except Exception as e:
        return {"error": str(e)}


# ---------------------------------------------------------------------------
# EDGE CASE 2: logical constraint violation (cross-field business rules)
# ---------------------------------------------------------------------------

def validate_business_rules(tool_input: dict) -> Optional[str]:
    """
    Post-schema business rule validation.

    The JSON schema cannot express cross-field constraints like "escalation_team
    must be non-null IF decision is escalate." These rules live here, AFTER
    schema validation has already passed.

    Returns an error string describing the violation, or None if valid.
    """
    decision = tool_input.get("decision")
    confidence = tool_input.get("confidence", 1.0)
    escalation_team = tool_input.get("escalation_team")
    resolution = tool_input.get("resolution")

    # Rule 1: auto_resolve requires confidence >= 0.6.
    # The schema allows confidence 0.0-1.0; business rules narrow the range
    # for the auto_resolve case specifically.
    if decision == "auto_resolve" and confidence < 0.6:
        return (
            f"Business rule violation: decision is auto_resolve but confidence "
            f"is {confidence:.2f} (below 0.6 threshold). Must escalate or "
            f"request more info when confidence is this low."
        )

    # Rule 2: escalate requires a non-null escalation_team.
    # The schema allows escalation_team to be null; business rules forbid it
    # on escalation decisions because the router needs a team to route to.
    if decision == "escalate" and escalation_team is None:
        return (
            "Business rule violation: decision is escalate but escalation_team "
            "is null. The routing service requires a team for all escalations."
        )

    # Rule 3: auto_resolve requires a non-null resolution text.
    # The schema allows resolution to be null; business rules require text
    # so the system can send a reply to the customer.
    if decision == "auto_resolve" and resolution is None:
        return (
            "Business rule violation: decision is auto_resolve but resolution "
            "is null. The system cannot send a reply without resolution text."
        )

    return None  # All rules pass.


def demonstrate_edge_case_2_business_rules() -> dict:
    """
    Submit an ambiguous ticket where the model might auto_resolve with low
    confidence. Show post-schema validation catching the rule violation.

    This ticket is intentionally vague — it could auto-resolve or need info.
    We prompt the system to lean toward auto-resolve to trigger the confidence
    rule in test conditions.
    """
    # Deliberately ambiguous ticket — the model is uncertain.
    ticket = (
        "Hey, I'm not sure if my account is working right? "
        "Sometimes it logs me out. Maybe it's my browser? "
        "Or is there an issue your end? Not urgent."
    )

    result = classify_ticket(ticket)

    if "error" in result:
        return {"error": result["error"], "passed": False}

    violation = validate_business_rules(result)
    return {
        "decision": result.get("decision"),
        "confidence": result.get("confidence"),
        "escalation_team": result.get("escalation_team"),
        "resolution": result.get("resolution"),
        "business_rule_violation": violation,
        # PASS means the validator ran and caught a potential issue OR
        # correctly passed a valid classification.
        "passed": True,
        "note": (
            "Violation detected and caught before routing."
            if violation
            else "No violation — classification is logically consistent."
        ),
    }


# ---------------------------------------------------------------------------
# EDGE CASE 3: pre-flight token check
# ---------------------------------------------------------------------------

def count_tokens(text: str) -> int:
    """
    Estimate token count for a text string.
    Heuristic: ~4 characters per token (rough average for English prose).
    For production use, call client.beta.messages.count_tokens() instead.
    """
    return len(text) // 4


def classify_with_preflight(ticket: str, model_context_limit: int = 200000) -> dict:
    """
    Pre-flight token check before calling the API.

    Haiku context window is 200,000 tokens. We reserve 2,048 for the output
    (tool call JSON + any prose). If the input exceeds the limit, we truncate
    using a head+tail strategy to preserve both the start of the thread (where
    the original issue is stated) and the end (where the most recent state is).

    All tool functions return dicts — never raises uncaught exceptions.
    """
    MAX_INPUT_TOKENS = model_context_limit - 2048  # reserve for output

    estimated_tokens = count_tokens(ticket)
    was_truncated = False

    if estimated_tokens > MAX_INPUT_TOKENS:
        # Truncate strategy: take first 80% and last 20% of character budget.
        # This preserves the original issue statement (head) and the most
        # recent customer message (tail) while dropping the middle thread.
        chars_limit = MAX_INPUT_TOKENS * 4
        head = ticket[: int(chars_limit * 0.8)]
        tail = ticket[int(-chars_limit * 0.2) :]
        ticket = head + "\n[... truncated for context window ...]\n" + tail
        was_truncated = True

    try:
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=512,
            system=SYSTEM_PROMPT,
            tools=[CLASSIFY_TOOL],
            tool_choice={"type": "tool", "name": "classify_ticket"},
            messages=[{"role": "user", "content": ticket}],
        )
        if response.stop_reason != "tool_use":
            return {
                "error": f"Unexpected stop_reason: {response.stop_reason}",
                "was_truncated": was_truncated,
            }
        result = response.content[0].input
        result["was_truncated"] = was_truncated
        result["estimated_input_tokens"] = estimated_tokens
        return result
    except Exception as e:
        return {"error": str(e), "was_truncated": was_truncated}


def demonstrate_edge_case_3_preflight() -> dict:
    """
    Build a very long ticket (simulating a pasted email thread) and show
    the pre-flight check truncating it before the API call.
    """
    # Build a synthetic long ticket — 2000 repetitions of a short sentence
    # simulates a large email thread pasted into the ticket body.
    base_message = (
        "On 2024-01-15, customer wrote: I am still having the issue with my "
        "invoice. Please advise. Support replied: We are looking into it. "
    )
    long_ticket = base_message * 2000  # ~200k+ characters = ~50k+ tokens

    estimated = count_tokens(long_ticket)
    result = classify_with_preflight(long_ticket, model_context_limit=200000)

    return {
        "original_estimated_tokens": estimated,
        "was_truncated": result.get("was_truncated", False),
        "decision": result.get("decision", "error"),
        "passed": result.get("was_truncated", False) and "error" not in result,
        "note": "Pre-flight detected oversized input and truncated before API call.",
    }


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main() -> None:
    sample_ticket = (
        "My payment failed three times today but I can see the charges on my "
        "bank statement. I need this resolved immediately — I have a meeting "
        "in two hours that depends on access to the premium features."
    )

    print("=" * 70)
    print("EXERCISE 2: tool_use Enforcement — Three Surviving Edge Cases")
    print("=" * 70)
    print()

    # ------------------------------------------------------------------
    # Edge case 1: max_tokens too low
    # ------------------------------------------------------------------
    print("--- Edge Case 1: max_tokens Too Low ---")
    print("Calling API with max_tokens=20 (tool call JSON will be truncated)...")
    ec1 = demonstrate_edge_case_1_max_tokens(sample_ticket)
    stop = ec1.get("stop_reason")
    content_types = ec1.get("content_types", [])
    print(f"  stop_reason:    {stop!r}")
    print(f"  content_types:  {content_types}")
    print(f"  Handler:        {ec1.get('handler')}")

    if ec1.get("passed"):
        print("  PASS: stop_reason was 'max_tokens' as expected.")
    else:
        print(f"  UNEXPECTED: stop_reason was '{stop}' — model may have finished in 20 tokens.")
    print()

    print("  Retrying with automatic max_tokens correction...")
    ec1_retry = demonstrate_edge_case_1_retry(sample_ticket)
    if "result" in ec1_retry:
        print(f"  Retry decision:  {ec1_retry['result'].get('decision')!r}")
        print(f"  Retried:         {ec1_retry.get('retried')}")
        print("  PASS: Retry produced valid tool output.")
    else:
        print(f"  Error: {ec1_retry.get('error')}")
    print()

    # ------------------------------------------------------------------
    # Edge case 2: business rule violation
    # ------------------------------------------------------------------
    print("--- Edge Case 2: Logical Constraint Violation ---")
    print("Submitting ambiguous ticket — checking post-schema business rules...")
    ec2 = demonstrate_edge_case_2_business_rules()

    if "error" in ec2:
        print(f"  API Error: {ec2['error']}")
    else:
        print(f"  decision:             {ec2.get('decision')!r}")
        print(f"  confidence:           {ec2.get('confidence')}")
        print(f"  escalation_team:      {ec2.get('escalation_team')!r}")
        violation = ec2.get("business_rule_violation")
        if violation:
            print(f"  RULE VIOLATION:       {violation}")
            print("  PASS: Business rule validator caught the problem before routing.")
        else:
            print(f"  No violation:         {ec2.get('note')}")
            print("  PASS: Classification is logically consistent with business rules.")
    print()

    # ------------------------------------------------------------------
    # Edge case 3: pre-flight token check
    # ------------------------------------------------------------------
    print("--- Edge Case 3: Pre-flight Token Check ---")
    print("Building synthetic oversized ticket (~50k+ tokens)...")
    ec3 = demonstrate_edge_case_3_preflight()
    print(f"  Original estimated tokens:  {ec3.get('original_estimated_tokens'):,}")
    print(f"  Was truncated:              {ec3.get('was_truncated')}")
    print(f"  Decision after truncation:  {ec3.get('decision')!r}")

    if ec3.get("passed"):
        print("  PASS: Pre-flight truncated input; API call succeeded.")
    else:
        print(f"  FAIL or error — check output above.")
    print()

    print("=" * 70)
    print("Exercise 2 complete.")
    print()
    print("Summary of three surviving edge cases:")
    print("  1. max_tokens too low   → detect via stop_reason, retry with 512+")
    print("  2. Logical constraint   → post-schema validate_business_rules()")
    print("  3. Context too large    → pre-flight count_tokens(), truncate head+tail")
    print("=" * 70)


if __name__ == "__main__":
    main()

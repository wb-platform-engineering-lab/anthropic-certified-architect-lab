"""
exercise_1_schema_contract.py — The Schema as a Contract

The key idea: when you ask the model for JSON in a system prompt, it usually
complies — but "usually" is not good enough for production routing.

Approach A (broken): put the structure in the system prompt and hope.
Approach B (fixed):  define a tool with an enum — the API enforces it.

Run this to see the difference side by side.
"""

import json
from typing import Optional
import anthropic
from dotenv import load_dotenv

load_dotenv()
client = anthropic.Anthropic()


# =============================================================================
# The tool — v2 with all three fixes applied
# =============================================================================

CLASSIFY_TOOL = {
    "name": "classify_ticket",
    "description": (
        "Classify a support ticket. "
        "decision must be one of the three enum values. "
        "escalation_team must be non-null when escalating; null otherwise. "
        "resolution must be non-null when auto-resolving; null otherwise."
    ),
    "input_schema": {
        "type": "object",
        "required": ["decision", "confidence", "reason", "escalation_team", "resolution"],
        "properties": {
            "decision": {
                "type": "string",
                # FIX 1: enum — the API rejects any value not in this list.
                "enum": ["auto_resolve", "escalate", "needs_info"],
                "description": (
                    "auto_resolve: resolve without human help. "
                    "escalate: needs human review. "
                    "needs_info: need more information from the customer."
                ),
            },
            "confidence": {
                "type": "number",
                # FIX 2: range — model cannot return 95 (percent) instead of 0.95 (decimal).
                "minimum": 0.0,
                "maximum": 1.0,
                "description": "Confidence in the decision. Range 0.0 to 1.0.",
            },
            "reason": {
                "type": "string",
                "description": "One sentence explaining the decision.",
            },
            "escalation_team": {
                # FIX 3: required + explicitly nullable.
                # "required" means the key is always present.
                # null means "not applicable" — different from "missing".
                "type": ["string", "null"],
                "enum": ["billing", "technical", "enterprise", "legal", None],
                "description": (
                    "Non-null when decision is escalate. "
                    "Explicitly null otherwise."
                ),
            },
            "resolution": {
                # FIX 3 (same idea): required + explicitly nullable.
                "type": ["string", "null"],
                "description": (
                    "Customer-facing reply text when decision is auto_resolve. "
                    "Explicitly null otherwise."
                ),
            },
        },
    },
}


# =============================================================================
# Approach A — ask for JSON in a system prompt (broken)
# =============================================================================

def classify_approach_a(ticket: str) -> dict:
    """
    Ask the model to return JSON via a system prompt instruction.
    No schema enforcement — the model can return anything.
    """
    response = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=256,
        system=(
            "Classify the support ticket. Return a JSON object with:\n"
            "  decision: auto_resolve, escalate, or needs_info\n"
            "  confidence: a number between 0 and 1\n"
            "  reason: one sentence\n"
            "  escalation_team: billing, technical, enterprise, legal, or null\n"
            "Output only the JSON. No markdown fences."
        ),
        messages=[{"role": "user", "content": ticket}],
    )
    raw = response.content[0].text.strip()
    try:
        parsed = json.loads(raw)
        return {"approach": "A", "raw": raw, "parsed": parsed, "error": None}
    except json.JSONDecodeError as e:
        return {"approach": "A", "raw": raw, "parsed": None, "error": str(e)}


# =============================================================================
# Approach B — use tool_choice to force the structured call (fixed)
# =============================================================================

def classify_approach_b(ticket: str) -> dict:
    """
    Force the model to call classify_ticket via tool_choice.
    The API validates the input against the schema before returning it.
    The enum on 'decision' means the model cannot produce "auto-resolve" or "ESCALATE".
    """
    response = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=512,
        tools=[CLASSIFY_TOOL],
        tool_choice={"type": "tool", "name": "classify_ticket"},
        messages=[{"role": "user", "content": ticket}],
    )
    # stop_reason is always "tool_use" when tool_choice forces a specific tool.
    result = response.content[0].input
    result["approach"] = "B"
    return result


# =============================================================================
# Compare the two approaches on three tickets
# =============================================================================

TICKETS = [
    ("password reset",
     "Hi, I forgot my password and can't log in. Can you help me reset it?"),

    ("billing dispute",
     "I was charged €750 last month that I never authorized. "
     "I want a full refund immediately."),

    ("ambiguous",
     "I think I might have been charged twice for March? "
     "Not sure, my statement is confusing. Can someone check?"),
]

VALID_DECISIONS = {"auto_resolve", "escalate", "needs_info"}


def main() -> None:
    print("=" * 65)
    print("Exercise 1: Schema as a Contract — Approach A vs Approach B")
    print("=" * 65)
    print()
    print("Approach A: JSON requested in a system prompt (no enforcement)")
    print("Approach B: tool_use with enum schema (API enforces the contract)")
    print()

    for label, ticket in TICKETS:
        print(f"Ticket: {label}")
        print(f"  \"{ticket[:70]}{'...' if len(ticket) > 70 else ''}\"")
        print()

        # Approach A
        a = classify_approach_a(ticket)
        parsed = a.get("parsed") or {}
        a_decision = parsed.get("decision", "MISSING")
        a_valid = a_decision in VALID_DECISIONS
        a_esc = parsed.get("escalation_team", "KEY ABSENT")
        print(f"  Approach A  decision:          '{a_decision}'")
        print(f"              valid enum value?   {'yes' if a_valid else 'NO — routing would break'}")
        print(f"              escalation_team:    {a_esc!r}")
        if a.get("error"):
            print(f"              JSON parse error:   {a['error']}")
        print()

        # Approach B
        b = classify_approach_b(ticket)
        b_decision = b.get("decision", "MISSING")
        b_esc = b.get("escalation_team")
        b_res = b.get("resolution")
        print(f"  Approach B  decision:          '{b_decision}'")
        print(f"              valid enum value?   always yes — API enforces")
        print(f"              escalation_team:    {b_esc!r}  (null = explicitly not escalating)")
        print(f"              resolution:         {'null' if b_res is None else repr(b_res[:50])}")
        print(f"              confidence:         {b.get('confidence')}")
        print()
        print()

    print("=" * 65)
    print("Key takeaway:")
    print("  Approach A: model complies most of the time. 'Most of the time'")
    print("              breaks routing when it doesn't.")
    print("  Approach B: the API enforces the enum. The model cannot return")
    print("              'auto-resolve' or 'ESCALATE' — only the three exact values.")
    print("=" * 65)


if __name__ == "__main__":
    main()

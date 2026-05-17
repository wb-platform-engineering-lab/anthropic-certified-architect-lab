"""
exercise_1_schema_contract.py — The Schema as a Contract

EXAM NOTE: This exercise demonstrates a core prompt engineering principle:
structured output reliability comes from SCHEMA ENFORCEMENT (tool_use with
enum constraints), not from prompt instructions alone.

The "Chapter 3 incident": The original Resolve classifier used v1 schema —
decision was a free-text string described in a prompt. Over two weeks in
production, the model returned at least 9 distinct strings for what should
have been 3 values: "auto_resolve", "resolve", "auto-resolve", "RESOLVE",
"auto resolve", "escalate", "ESCALATE", "escalation", "needs more info".
Downstream routing broke. This file reproduces the failure and shows the fix.

Key insight: tool_choice forces the model to call your tool. The tool's input
schema is validated by the API before your code ever sees it. Enums in the
schema mean the model CANNOT produce a value outside the set — the API will
reject it and retry internally. This is fundamentally different from "please
return one of these values" in a system prompt.
"""

import json
import os
from typing import Optional

import anthropic
from dotenv import load_dotenv

load_dotenv()

client = anthropic.Anthropic()

# ---------------------------------------------------------------------------
# V1 tool definition (mirrors schema_v1.json)
# Problem 1: decision is free-text — no enum constraint. The model will
#            produce varied strings ("auto-resolve", "resolve", "ESCALATE")
#            depending on phrasing, temperature, and prompt wording.
# Problem 2: confidence has no range constraint — model may return 95 (percent
#            style) or 0.95 (decimal style) — ambiguous for downstream code.
# Problem 3: escalation_team is optional but the routing service always
#            expects it to be present when decision is "escalate". If the
#            model omits it, the router KeyErrors and crashes.
# ---------------------------------------------------------------------------
CLASSIFY_TOOL_V1 = {
    "name": "classify_ticket_v1",
    "description": "Classify a support ticket and decide how to handle it.",
    "input_schema": {
        "type": "object",
        "required": ["decision", "confidence", "reason"],
        # NOTE: escalation_team and resolution are NOT required — this is the bug.
        "properties": {
            "decision": {
                "type": "string",
                # No enum — the model is free to invent values.
                "description": "What to do with this ticket",
            },
            "confidence": {
                "type": "number",
                # No minimum/maximum — model may return 0-100 or 0.0-1.0.
                "description": "How confident are you",
            },
            "reason": {
                "type": "string",
                "description": "Why did you make this decision",
            },
            "escalation_team": {
                "type": "string",
                # Optional — missing when decision is escalate causes router crash.
                "description": "Which team to escalate to",
            },
            "resolution": {
                "type": "string",
                "description": "The resolution text if auto-resolving",
            },
        },
    },
}

# ---------------------------------------------------------------------------
# V2 tool definition (mirrors schema_v2.json)
# Fix 1: decision is an enum — exactly 3 valid values. The API enforces this.
# Fix 2: confidence has minimum 0.0 and maximum 1.0 — range is unambiguous.
# Fix 3: escalation_team and resolution are REQUIRED and explicitly nullable —
#        the model must always include them; null signals "not applicable" vs.
#        a missing key which signals "bug".
# ---------------------------------------------------------------------------
CLASSIFY_TOOL_V2 = {
    "name": "classify_ticket_v2",
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
                # FIX 1: Enum enforced by the API — model cannot deviate.
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
                # FIX 2: Range constraint — unambiguous decimal 0.0 to 1.0.
                "minimum": 0.0,
                "maximum": 1.0,
                "description": (
                    "Confidence in the decision. Range 0.0 to 1.0. "
                    "Values below 0.6 should not be used for auto_resolve decisions."
                ),
            },
            "reason": {
                "type": "string",
                "description": (
                    "One or two sentences explaining the decision. "
                    "Must reference specific facts from the ticket."
                ),
            },
            "escalation_team": {
                # FIX 3a: Now required. Type allows null — explicit absence vs. missing.
                "type": ["string", "null"],
                "enum": ["billing", "technical", "enterprise", "legal", None],
                "description": (
                    "Required and non-null when decision is escalate. "
                    "Explicitly null when decision is auto_resolve or needs_info."
                ),
            },
            "resolution": {
                # FIX 3b: Now required. Null is explicit — not just omitted.
                "type": ["string", "null"],
                "description": (
                    "Customer-facing resolution text. Required and non-null when "
                    "decision is auto_resolve. Explicitly null otherwise."
                ),
            },
        },
    },
}


def classify_with_v1(ticket: str) -> dict:
    """
    Classify using the BROKEN v1 approach: prompt-only instructions.

    Why this is broken: we tell the model what values to use in a system
    prompt, but there is no enforcement. The model follows instructions most
    of the time, but not always — especially for edge cases, ambiguous
    tickets, or when the instruction wording is slightly different from how
    the model's training data phrased similar constraints.

    Returns a dict with 'raw' text, 'parsed' dict (or None), and 'error'.
    """
    response = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=256,
        system=(
            "Classify the support ticket. Return a JSON object with:\n"
            "  decision: what to do (auto_resolve, escalate, or needs_info)\n"
            "  confidence: a number\n"
            "  reason: why\n"
            "  escalation_team: which team (if escalating)\n"
            "Output only valid JSON. No markdown."
        ),
        messages=[{"role": "user", "content": ticket}],
    )
    raw = response.content[0].text.strip()
    try:
        return {"raw": raw, "parsed": json.loads(raw), "error": None}
    except json.JSONDecodeError as e:
        return {"raw": raw, "parsed": None, "error": str(e)}


def classify_with_v2(ticket: str) -> dict:
    """
    Classify using the FIXED v2 approach: tool_use with enum enforcement.

    tool_choice forces the model to call classify_ticket_v2. The API validates
    the tool input against the schema before returning it to our code. The
    enum constraint on 'decision' means the model physically cannot return
    "auto-resolve" or "ESCALATE" — it must return one of the three exact
    strings or the API retries internally.

    Returns the tool input dict directly — always valid against v2 schema.
    """
    response = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=512,
        system=(
            "You are the Resolve ticket classifier. Classify the support ticket "
            "using the classify_ticket_v2 tool. Be precise: escalation_team must "
            "be null if not escalating; resolution must be null if not auto-resolving."
        ),
        tools=[CLASSIFY_TOOL_V2],
        # This is the key: forces exactly this tool, no prose alternative.
        tool_choice={"type": "tool", "name": "classify_ticket_v2"},
        messages=[{"role": "user", "content": ticket}],
    )
    # stop_reason is always "tool_use" when tool_choice forces a specific tool.
    # response.content[0] is always a ToolUseBlock.
    return response.content[0].input


# ---------------------------------------------------------------------------
# Test tickets — chosen to expose specific v1 failure modes.
# ---------------------------------------------------------------------------

# Ticket A: Should be auto_resolve — straightforward password reset.
# V1 failure mode: model sometimes returns "auto-resolve" or "resolve".
TICKET_AUTO_RESOLVE = (
    "Hi, I forgot my password and can't log in. "
    "Can you reset it for me? My account email is sarah@example.com."
)

# Ticket B: Should be escalate (billing team) — disputed charge over €500.
# V1 failure mode: model sometimes omits escalation_team entirely.
TICKET_ESCALATE = (
    "I was charged €750 last month that I never authorized. "
    "I have already disputed it with my bank. "
    "This is unacceptable and I want a full refund immediately."
)

# Ticket C: Ambiguous — small possible duplicate, could go either way.
# V1 failure mode: model returns free-text like "needs more info" or "clarify".
TICKET_AMBIGUOUS = (
    "I think I might have been charged twice for March? "
    "Not sure though, my statement is confusing. Can someone check?"
)

# Ticket D: Clear escalation — data breach mention triggers legal/technical.
# V1 failure mode: model returns "escalation" (noun) not "escalate" (verb).
TICKET_DATA_BREACH = (
    "I received an email saying my account data was part of a breach. "
    "I need to know what data was exposed and what you are doing about it. "
    "I may need to involve my solicitor."
)

ALL_TEST_TICKETS = [
    ("auto_resolve_expected", TICKET_AUTO_RESOLVE),
    ("escalate_expected", TICKET_ESCALATE),
    ("ambiguous", TICKET_AMBIGUOUS),
    ("data_breach_escalate", TICKET_DATA_BREACH),
]

# The three valid v2 decision values — used to detect v1-format output.
V2_VALID_DECISIONS = {"auto_resolve", "escalate", "needs_info"}


def demonstrate_v1_failures() -> None:
    """
    Run the 4 test tickets through both v1 and v2 classifiers.

    Shows:
    - v1 decision values may or may not match the enum (free-text variability)
    - v1 may omit escalation_team on escalation tickets
    - v2 always produces valid enum values
    - v2 always includes all required fields (escalation_team, resolution)
    """
    print("=" * 70)
    print("EXERCISE 1: Schema Contract — V1 vs V2 Comparison")
    print("=" * 70)
    print()

    for label, ticket in ALL_TEST_TICKETS:
        print(f"--- Ticket: {label} ---")
        print(f"Text: {ticket[:80]}{'...' if len(ticket) > 80 else ''}")
        print()

        # V1: prompt-only
        v1_result = classify_with_v1(ticket)
        v1_parsed = v1_result.get("parsed") or {}
        v1_decision = v1_parsed.get("decision", "MISSING")
        v1_esc_team = v1_parsed.get("escalation_team", "MISSING (key absent)")
        v1_valid = v1_decision in V2_VALID_DECISIONS

        print(f"  V1 decision:         '{v1_decision}'")
        print(f"  V1 valid enum?       {'YES' if v1_valid else 'NO — this would break routing'}")
        print(f"  V1 escalation_team:  {v1_esc_team!r}")
        print(f"  V1 confidence:       {v1_parsed.get('confidence', 'MISSING')}")
        if v1_result.get("error"):
            print(f"  V1 JSON parse error: {v1_result['error']}")
        print()

        # V2: tool_use enforcement
        v2_result = classify_with_v2(ticket)
        v2_decision = v2_result.get("decision", "MISSING")
        v2_esc_team = v2_result.get("escalation_team")
        v2_resolution = v2_result.get("resolution")

        print(f"  V2 decision:         '{v2_decision}'")
        print(f"  V2 valid enum?       {'YES' if v2_decision in V2_VALID_DECISIONS else 'IMPOSSIBLE — API enforces'}")
        print(f"  V2 escalation_team:  {v2_esc_team!r}  (explicit null means not-escalating)")
        print(f"  V2 resolution:       {str(v2_resolution)[:60] if v2_resolution else 'null'!r}")
        print(f"  V2 confidence:       {v2_result.get('confidence')}")
        print()
        print()


def is_v1_format(result: dict) -> bool:
    """
    Detect whether a classification result uses v1 format.

    A result is v1-format if:
    - decision is not in the v2 enum set, OR
    - escalation_team key is absent (v1 makes it optional), OR
    - resolution key is absent (v1 makes it optional)
    """
    decision = result.get("decision", "")
    if decision not in V2_VALID_DECISIONS:
        return True
    if "escalation_team" not in result:
        return True
    if "resolution" not in result:
        return True
    return False


def schema_migration_test() -> None:
    """
    Given a simulated v1 output dict, detect v1 format and re-run through v2.

    This simulates the migration path: old results stored in a database with
    v1 format need to be re-classified using v2 before being processed by
    the new routing service.
    """
    print("=" * 70)
    print("EXERCISE 1: Schema Migration Test")
    print("=" * 70)
    print()

    # Simulate v1 outputs that would have been stored — deliberately broken.
    simulated_v1_outputs = [
        {
            "ticket": TICKET_AUTO_RESOLVE,
            "label": "password reset",
            "v1_result": {
                "decision": "auto-resolve",    # hyphen — not in enum
                "confidence": 0.9,
                "reason": "Simple password reset request.",
                # Missing escalation_team and resolution
            },
        },
        {
            "ticket": TICKET_ESCALATE,
            "label": "billing dispute",
            "v1_result": {
                "decision": "escalate",        # correct by luck
                "confidence": 0.85,
                "reason": "Large unauthorized charge.",
                # Missing escalation_team — the key bug
            },
        },
        {
            "ticket": TICKET_DATA_BREACH,
            "label": "data breach",
            "v1_result": {
                "decision": "escalation",      # noun form — not in enum
                "confidence": 0.95,
                "reason": "Data breach requires legal review.",
                "escalation_team": "legal",
            },
        },
    ]

    for item in simulated_v1_outputs:
        label = item["label"]
        v1_result = item["v1_result"]
        ticket = item["ticket"]

        print(f"--- Stored v1 result: {label} ---")
        print(f"  Stored decision: '{v1_result.get('decision')}'")
        print(f"  Is v1 format?    {is_v1_format(v1_result)}")

        if is_v1_format(v1_result):
            print("  Action: Re-classifying through v2 tool...")
            v2_result = classify_with_v2(ticket)
            print(f"  V2 decision:         '{v2_result.get('decision')}'")
            print(f"  V2 escalation_team:  {v2_result.get('escalation_team')!r}")
            print(f"  V2 resolution:       {str(v2_result.get('resolution', ''))[:60] or 'null'!r}")
        else:
            print("  Action: Result already v2-compatible — no re-classification needed.")

        print()


def main() -> None:
    demonstrate_v1_failures()
    schema_migration_test()
    print("=" * 70)
    print("Exercise 1 complete.")
    print()
    print("Key takeaway: Schema enforcement via tool_use enums is not a style")
    print("preference — it is a reliability mechanism. Prompt instructions can")
    print("drift; API-enforced schemas cannot.")
    print("=" * 70)


if __name__ == "__main__":
    main()

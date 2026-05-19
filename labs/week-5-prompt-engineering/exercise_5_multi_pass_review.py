"""
exercise_5_multi_pass_review.py — Two-Pass Review for High-Stakes Tickets

For most tickets, one classification is enough. For high-stakes tickets
(large billing disputes, enterprise accounts, potential legal issues), a
second model call reviews the first classification and can override it.

Why two passes?
  - A single classifier optimises for the average case.
  - A reviewer with an adversarial prompt catches the edge cases.
  - If the override rate is above ~20%, the first-pass prompt needs work.
  - If it is below 5%, two-pass is probably not worth the cost.

Pass 1: classify_ticket tool (same as Exercises 1–4)
Pass 2: review_classification tool — verdict is "confirmed" or "overridden"
"""

import json
from typing import Optional
import anthropic
from dotenv import load_dotenv

load_dotenv()
client = anthropic.Anthropic()


# =============================================================================
# Pass 1 tool — standard classifier
# =============================================================================

CLASSIFY_TOOL = {
    "name": "classify_ticket",
    "description": (
        "Classify a support ticket. decision: one of the three enum values. "
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
# Pass 2 tool — reviewer
# =============================================================================

REVIEW_TOOL = {
    "name": "review_classification",
    "description": (
        "Review an initial ticket classification. "
        "If correct, confirm it. If wrong, override it with the right decision. "
        "justification is required in both cases — explain your reasoning."
    ),
    "input_schema": {
        "type": "object",
        "required": ["verdict", "justification", "overriding_decision", "overriding_escalation_team"],
        "properties": {
            "verdict": {
                "type": "string",
                "enum": ["confirmed", "overridden"],
                "description": (
                    "confirmed: the initial classification is correct. "
                    "overridden: the initial classification is wrong."
                ),
            },
            "justification": {
                "type": "string",
                "description": (
                    "Required in both cases. Explain specifically why you confirmed "
                    "or what is wrong with the initial classification."
                ),
            },
            "overriding_decision": {
                "type": ["string", "null"],
                "enum": ["auto_resolve", "escalate", "needs_info", None],
                "description": "The correct decision if overridden. Null if confirmed.",
            },
            "overriding_escalation_team": {
                "type": ["string", "null"],
                "enum": ["billing", "technical", "enterprise", "legal", None],
                "description": "The correct team if overriding_decision is escalate. Null otherwise.",
            },
        },
    },
}


# =============================================================================
# Pass 1: classify
# =============================================================================

def classify_first_pass(ticket: str) -> dict:
    """Standard classification — same as previous exercises."""
    try:
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=512,
            tools=[CLASSIFY_TOOL],
            tool_choice={"type": "tool", "name": "classify_ticket"},
            messages=[{"role": "user", "content": ticket}],
        )
        return response.content[0].input
    except Exception as e:
        return {"error": str(e)}


# =============================================================================
# Pass 2: review
# =============================================================================

def review_classification(ticket: str, first_pass: dict) -> dict:
    """
    Review the first-pass classification with an adversarial prompt.
    The reviewer is instructed to be critical — a rubber-stamp review is useless.
    """
    review_prompt = (
        f"Ticket: {ticket}\n\n"
        f"Initial classification:\n"
        f"  decision:        {first_pass.get('decision')}\n"
        f"  confidence:      {first_pass.get('confidence')}\n"
        f"  reason:          {first_pass.get('reason')}\n"
        f"  escalation_team: {first_pass.get('escalation_team')}\n\n"
        "Review this classification. Confirm it if correct. Override it if wrong."
    )

    try:
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=512,
            system=(
                "You are a senior support classification reviewer. "
                "Your job is to catch errors in the initial classification. "
                "Be critical. The cost of auto-resolving a ticket that needs escalation "
                "is much higher than the cost of escalating one that could be auto-resolved. "
                "When in doubt, escalate or ask for more info."
            ),
            tools=[REVIEW_TOOL],
            tool_choice={"type": "tool", "name": "review_classification"},
            messages=[{"role": "user", "content": review_prompt}],
        )
        return response.content[0].input
    except Exception as e:
        # On review failure, default to confirming the first pass
        # (avoid double-escalation noise from a broken reviewer)
        return {
            "verdict": "confirmed",
            "justification": f"Review failed: {e}. Defaulting to first-pass result.",
            "overriding_decision": None,
            "overriding_escalation_team": None,
        }


# =============================================================================
# Combined two-pass pipeline
# =============================================================================

def classify_two_pass(ticket: str) -> dict:
    """
    Run both passes and return a combined result with the final decision.
    """
    first = classify_first_pass(ticket)
    if "error" in first:
        return {"error": first["error"]}

    review = review_classification(ticket, first)

    overridden = review.get("verdict") == "overridden"
    final_decision = (
        review.get("overriding_decision") if overridden
        else first.get("decision")
    )
    final_team = (
        review.get("overriding_escalation_team") if overridden
        else first.get("escalation_team")
    )

    return {
        "first_pass_decision": first.get("decision"),
        "first_pass_confidence": first.get("confidence"),
        "verdict": review.get("verdict"),
        "justification": review.get("justification"),
        "final_decision": final_decision,
        "final_escalation_team": final_team,
        "was_overridden": overridden,
    }


# =============================================================================
# Test tickets
# =============================================================================

TICKETS = [
    {
        "label": "clear auto_resolve",
        "ticket": "How do I change my billing email address?",
        "note": "Reviewer should confirm. Both passes agree.",
    },
    {
        "label": "large billing dispute",
        "ticket": (
            "We are an enterprise customer. Last month we were charged €2,400 "
            "for a plan we downgraded 6 months ago. We have the contract. "
            "This needs immediate resolution."
        ),
        "note": "High stakes. Reviewer should confirm escalate/enterprise.",
    },
    {
        "label": "potentially misclassified",
        "ticket": (
            "My account shows active but I cancelled 3 months ago and I'm "
            "still being charged every month. I've emailed 4 times with no reply."
        ),
        "note": "First pass may auto_resolve. Reviewer may override to escalate.",
    },
    {
        "label": "vague complaint",
        "ticket": "Something seems off with my account. Not sure what exactly.",
        "note": "First pass may auto_resolve or escalate. Reviewer should push to needs_info.",
    },
    {
        "label": "data and legal",
        "ticket": (
            "I received an email from a third party that contained my account "
            "information. I believe this is a data breach. I am consulting my lawyer."
        ),
        "note": "Should escalate to legal. Both passes should agree.",
    },
]


# =============================================================================
# main
# =============================================================================

def main() -> None:
    print("=" * 65)
    print("Exercise 5: Two-Pass Review for High-Stakes Tickets")
    print("=" * 65)
    print()
    print("Pass 1: standard classifier")
    print("Pass 2: adversarial reviewer — 'be critical, escalation errors are costly'")
    print()

    overrides = 0
    total = len(TICKETS)

    for item in TICKETS:
        label = item["label"]
        ticket = item["ticket"]
        note = item["note"]

        print(f"Ticket: {label}")
        print(f"  \"{ticket[:70]}{'...' if len(ticket) > 70 else ''}\"")
        print()

        result = classify_two_pass(ticket)

        if "error" in result:
            print(f"  Error: {result['error']}")
            print()
            continue

        print(f"  Pass 1 decision:  {result['first_pass_decision']!r}  "
              f"(confidence {result['first_pass_confidence']})")
        print(f"  Reviewer verdict: {result['verdict']!r}")
        print(f"  Justification:    \"{result['justification'][:80]}\"")

        if result["was_overridden"]:
            overrides += 1
            print(f"  OVERRIDDEN  →  final: {result['final_decision']!r}  "
                  f"team: {result['final_escalation_team']!r}")
        else:
            print(f"  CONFIRMED   →  final: {result['final_decision']!r}")

        print(f"  Note: {note}")
        print()

    override_rate = overrides / total * 100 if total > 0 else 0
    print("=" * 65)
    print(f"Results: {overrides}/{total} tickets overridden ({override_rate:.0f}%)")
    print()
    if override_rate > 20:
        print("Override rate > 20%: the first-pass classifier has a systematic")
        print("problem. Fix the first-pass prompt rather than relying on the reviewer.")
    elif override_rate < 5:
        print("Override rate < 5%: two-pass may not be worth the cost here.")
        print("The first-pass classifier is already reliable on these ticket types.")
    else:
        print("Override rate 5–20%: two-pass is working as intended — catching")
        print("edge cases without suggesting the first pass is fundamentally broken.")
    print()
    print("Key takeaway:")
    print("  justification is required even when confirming — a reviewer that")
    print("  just stamps 'confirmed' without explaining is not a real review.")
    print("=" * 65)


if __name__ == "__main__":
    main()

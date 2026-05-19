"""
exercise_5_context_health.py — Context Health as an Escalation Trigger

Sentiment-based escalation: escalate if the customer sounds frustrated.
Context-based escalation: escalate if the context itself is unreliable.

Context health is auditable — you can explain exactly why a ticket was
escalated. Sentiment escalation cannot be explained this way.

Three context health metrics:
  compression_depth  — how many times the conversation has been summarised
  provenance_gaps    — critical facts whose source turn cannot be identified
  contradictions     — facts that appear to conflict with each other

Escalation thresholds:
  compression_depth > 2        → escalate
  any provenance gap on a billing fact → escalate
  any contradiction on a commitment    → escalate
"""

import json
from typing import Optional
import anthropic
from dotenv import load_dotenv

load_dotenv()
client = anthropic.Anthropic()

MODEL = "claude-haiku-4-5-20251001"


# ---------------------------------------------------------------------------
# Context health assessment tool
# ---------------------------------------------------------------------------

HEALTH_TOOL = {
    "name": "assess_health",
    "description": "Assess the health of a support conversation context.",
    "input_schema": {
        "type": "object",
        "required": ["provenance_gaps", "contradictions", "sentiment"],
        "properties": {
            "provenance_gaps": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Facts mentioned in the conversation whose source or turn number "
                    "is unclear or cannot be verified (e.g. amounts, reference numbers "
                    "that appear without clear origin)."
                ),
            },
            "contradictions": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Facts that appear to conflict with each other in the conversation "
                    "(e.g. the same invoice amount mentioned with two different values)."
                ),
            },
            "sentiment": {
                "type": "string",
                "enum": ["positive", "neutral", "frustrated", "angry"],
                "description": "Overall customer sentiment in the conversation.",
            },
        },
    },
}


def assess_context_health(messages: list, compression_depth: int = 0) -> dict:
    """
    Assess context health using a model call.
    Returns a ContextHealthReport dict.
    """
    transcript = "\n".join(f"{m['role'].upper()}: {m['content']}" for m in messages)

    response = client.messages.create(
        model=MODEL,
        max_tokens=256,
        tools=[HEALTH_TOOL],
        tool_choice={"type": "tool", "name": "assess_health"},
        messages=[{
            "role": "user",
            "content": f"Assess the health of this support conversation:\n\n{transcript}",
        }],
    )
    result = response.content[0].input

    return {
        "compression_depth": compression_depth,
        "provenance_gaps": result.get("provenance_gaps", []),
        "contradictions": result.get("contradictions", []),
        "sentiment": result.get("sentiment", "neutral"),
    }


# ---------------------------------------------------------------------------
# Escalation triggers
# ---------------------------------------------------------------------------

def should_escalate_by_sentiment(health_report: dict) -> bool:
    """Old approach: escalate if customer sounds frustrated or angry."""
    return health_report["sentiment"] in ("frustrated", "angry")


def should_escalate_by_context(health_report: dict) -> dict:
    """
    New approach: escalate based on context health metrics.
    Returns {"escalate": bool, "reason": str}.
    """
    if health_report["compression_depth"] > 2:
        return {
            "escalate": True,
            "reason": f"Compression depth {health_report['compression_depth']} exceeds limit of 2.",
        }
    if health_report["provenance_gaps"]:
        return {
            "escalate": True,
            "reason": f"Provenance gaps: {health_report['provenance_gaps']}",
        }
    if health_report["contradictions"]:
        return {
            "escalate": True,
            "reason": f"Contradictions found: {health_report['contradictions']}",
        }
    return {"escalate": False, "reason": "Context health is acceptable."}


# ---------------------------------------------------------------------------
# Test conversations
# ---------------------------------------------------------------------------

# Conversation 1: clean context, frustrated customer
TICKET_FRUSTRATED_CLEAN = [
    {"role": "user", "content": "I've been waiting THREE WEEKS for my refund. This is unacceptable!"},
    {"role": "assistant", "content": "I'm sorry for the delay. Let me check your refund status."},
    {"role": "user", "content": "My refund of €89 was approved on 2026-04-01, reference REF-001."},
    {"role": "assistant", "content": "I can see your refund REF-001 for €89 approved on 2026-04-01. It should arrive within 1 business day."},
    {"role": "user", "content": "This is completely unacceptable! THREE WEEKS!"},
]

# Conversation 2: calm customer, corrupted context (contradictory amounts)
TICKET_CALM_CONTRADICTED = [
    {"role": "user", "content": "Hi, quick question about my refund."},
    {"role": "assistant", "content": "Sure, happy to help!"},
    {"role": "user", "content": "My refund was approved for €89, reference REF-001."},
    {"role": "assistant", "content": "Yes, I can confirm your refund of €120 is on its way."},  # contradiction!
    {"role": "user", "content": "Wait, was it €89 or €120?"},
    {"role": "assistant", "content": "I see €89 in one record and €150 in another."},  # more confusion
    {"role": "user", "content": "OK thanks, just let me know when it arrives."},
]

# Conversation 3: deeply compressed context (high compression depth)
TICKET_COMPRESSED = [
    {"role": "user", "content": "[SUMMARY: Billing dispute discussed. Refund mentioned. Details unclear.]"},
    {"role": "assistant", "content": "I'll look into your refund."},
    {"role": "user", "content": "Can you confirm the exact amount and reference number?"},
]

TEST_CASES = [
    {
        "label": "frustrated customer, clean context",
        "messages": TICKET_FRUSTRATED_CLEAN,
        "compression_depth": 0,
        "note": "Sentiment says escalate. Context says: don't bother.",
    },
    {
        "label": "calm customer, contradicted context",
        "messages": TICKET_CALM_CONTRADICTED,
        "compression_depth": 0,
        "note": "Sentiment says: fine. Context says: escalate — contradictions.",
    },
    {
        "label": "high compression depth",
        "messages": TICKET_COMPRESSED,
        "compression_depth": 3,
        "note": "Context says: escalate — compressed too many times.",
    },
]


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    print("=" * 65)
    print("Exercise 5: Context Health as an Escalation Trigger")
    print("=" * 65)
    print()
    print("Comparing sentiment-based vs. context-based escalation.")
    print()

    for case in TEST_CASES:
        print(f"--- {case['label']} ---")
        print(f"Note: {case['note']}")
        print()

        report = assess_context_health(case["messages"], case["compression_depth"])

        print(f"  Context health report:")
        print(f"    compression_depth: {report['compression_depth']}")
        print(f"    provenance_gaps:   {report['provenance_gaps']}")
        print(f"    contradictions:    {report['contradictions']}")
        print(f"    sentiment:         {report['sentiment']}")
        print()

        sentiment_esc = should_escalate_by_sentiment(report)
        context_esc = should_escalate_by_context(report)

        print(f"  Sentiment trigger:  escalate={sentiment_esc}")
        print(f"  Context trigger:    escalate={context_esc['escalate']}")
        if context_esc["escalate"]:
            print(f"    Reason: {context_esc['reason']}")
        print()

    print("=" * 65)
    print("Key takeaway:")
    print("  Context health escalation is auditable — you can explain")
    print("  exactly why a ticket was escalated:")
    print('    "This conversation was compressed 3 times and the refund')
    print('     amount appears twice with conflicting values."')
    print()
    print("  Sentiment escalation cannot be explained this way.")
    print("  A frustrated customer with clean context does not need escalation.")
    print("  A calm customer with contradicted context does.")
    print("=" * 65)


if __name__ == "__main__":
    main()

"""
exercise_3_few_shot.py — Few-Shot Examples for tool_use

With tool_use, few-shot examples are NOT about teaching the output format —
the schema already handles that. They demonstrate JUDGMENT on hard cases:

  - When is a small billing discrepancy worth escalating vs. needs_info?
  - What confidence is right for a vague complaint?

The injection pattern for tool_use is a multi-turn conversation:
  user (example ticket)
  → assistant (tool_use block with the correct classification)
  → user (tool_result — required to maintain valid turn structure)
  → ... repeat for each example ...
  → user (the real ticket)

If you put examples in the system prompt as text instead, the model sees
them as instructions, not as demonstrations of actual tool call behaviour.
"""

import json
from typing import Optional
import anthropic
from dotenv import load_dotenv

load_dotenv()
client = anthropic.Anthropic()


# =============================================================================
# The tool (same schema as Exercises 1 and 2)
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

SYSTEM_PROMPT = (
    "You are the Resolve ticket classifier. "
    "Classify each support ticket using the classify_ticket tool. "
    "For ambiguous tickets, prefer needs_info over a low-confidence auto_resolve."
)


# =============================================================================
# Three few-shot examples
#
# Chosen to cover the three hard judgment calls:
#   1. Clear auto_resolve — shows the model what high-confidence looks like
#   2. Clear escalate — shows escalation with the right team
#   3. The hard case — a vague complaint that is needs_info, not auto_resolve
#      The reason field explains WHY, so the model learns the reasoning pattern
# =============================================================================

FEW_SHOT_EXAMPLES = [
    {
        "ticket": "Hi, I forgot my password and can't log in. How do I reset it?",
        "classification": {
            "decision": "auto_resolve",
            "confidence": 0.97,
            "reason": "Standard password reset request. Covered by self-service documentation.",
            "escalation_team": None,
            "resolution": (
                "You can reset your password at https://resolve.app/reset. "
                "Enter your account email and follow the link we send you. "
                "If you don't receive it within 5 minutes, check your spam folder."
            ),
        },
    },
    {
        "ticket": (
            "We are an enterprise customer and we were charged €1,200 that "
            "we did not authorise. This needs to be investigated immediately."
        ),
        "classification": {
            "decision": "escalate",
            "confidence": 0.95,
            "reason": (
                "Enterprise account with unauthorised charge over €500 threshold. "
                "Requires billing team review and manual authorisation check."
            ),
            "escalation_team": "enterprise",
            "resolution": None,
        },
    },
    {
        "ticket": "I think I was charged twice last month but I'm not 100% sure. It was only $9.",
        "classification": {
            "decision": "needs_info",
            "confidence": 0.75,
            "reason": (
                "Customer is uncertain — 'I think' and 'not 100% sure' indicate they "
                "haven't verified the charge. The $9 amount is below the auto-refund "
                "threshold. We need them to confirm the charge date and last 4 digits "
                "of the card before acting."
            ),
            "escalation_team": None,
            "resolution": None,
        },
    },
]


# =============================================================================
# Inject few-shot examples into the message history
# =============================================================================

def build_few_shot_messages(ticket: str) -> list:
    """
    Build the messages array with few-shot examples injected as real turns.

    Each example is three turns:
      1. user:      the example ticket
      2. assistant: the tool_use block (the "correct" classification)
      3. user:      the tool_result (required — the API enforces turn alternation)

    After all examples, the real ticket is appended as the final user turn.
    """
    messages = []

    for i, example in enumerate(FEW_SHOT_EXAMPLES):
        # Turn 1: user sends the example ticket
        messages.append({
            "role": "user",
            "content": example["ticket"],
        })

        # Turn 2: assistant makes the "correct" tool call
        messages.append({
            "role": "assistant",
            "content": [{
                "type": "tool_use",
                "id": f"ex_{i}",
                "name": "classify_ticket",
                "input": example["classification"],
            }],
        })

        # Turn 3: user confirms the tool result
        # This is required — the API needs a tool_result after every tool_use.
        # Without it, you get a 400 "messages must alternate between user and assistant".
        messages.append({
            "role": "user",
            "content": [{
                "type": "tool_result",
                "tool_use_id": f"ex_{i}",
                "content": "Classification recorded.",
            }],
        })

    # The real ticket goes last
    messages.append({"role": "user", "content": ticket})
    return messages


def classify_no_examples(ticket: str) -> dict:
    """Classify without any few-shot examples."""
    try:
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=512,
            system=SYSTEM_PROMPT,
            tools=[CLASSIFY_TOOL],
            tool_choice={"type": "tool", "name": "classify_ticket"},
            messages=[{"role": "user", "content": ticket}],
        )
        return response.content[0].input
    except Exception as e:
        return {"error": str(e)}


def classify_with_examples(ticket: str) -> dict:
    """Classify with the three few-shot examples injected into message history."""
    try:
        messages = build_few_shot_messages(ticket)
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=512,
            system=SYSTEM_PROMPT,
            tools=[CLASSIFY_TOOL],
            tool_choice={"type": "tool", "name": "classify_ticket"},
            messages=messages,
        )
        return response.content[0].input
    except Exception as e:
        return {"error": str(e)}


# =============================================================================
# Test tickets — focus on the cases where examples matter most
# =============================================================================

TEST_TICKETS = [
    {
        "label": "clear auto_resolve",
        "ticket": "How do I export my data to CSV? I can't find the option.",
        "note": "Both approaches should agree. Examples don't change easy cases.",
    },
    {
        "label": "clear escalate",
        "ticket": (
            "I believe there was a data breach affecting my account. "
            "I received a suspicious email with my full name and account number. "
            "I need to speak to someone immediately."
        ),
        "note": "Both should escalate. Examples confirm the pattern.",
    },
    {
        "label": "ambiguous — vague complaint",
        "ticket": (
            "Something seems off with my invoices lately. "
            "Not sure exactly what, just feels wrong. "
            "Can you look into it?"
        ),
        "note": "Key test: without examples, model may auto_resolve. With example 3, it needs_info.",
    },
    {
        "label": "ambiguous — small possible duplicate",
        "ticket": "I might have been charged $12 twice this month? Hard to tell from my bank app.",
        "note": "Directly mirrors example 3. Model should follow the shown reasoning.",
    },
]


# =============================================================================
# main
# =============================================================================

def main() -> None:
    print("=" * 65)
    print("Exercise 3: Few-Shot Examples for tool_use")
    print("=" * 65)
    print()
    print(f"Using {len(FEW_SHOT_EXAMPLES)} few-shot examples injected as message history turns.")
    print()

    for item in TEST_TICKETS:
        label = item["label"]
        ticket = item["ticket"]
        note = item["note"]

        print(f"Ticket: {label}")
        print(f"  \"{ticket[:75]}{'...' if len(ticket) > 75 else ''}\"")
        print()

        result_no = classify_no_examples(ticket)
        result_yes = classify_with_examples(ticket)

        no_decision = result_no.get("decision", f"ERROR: {result_no.get('error')}")
        yes_decision = result_yes.get("decision", f"ERROR: {result_yes.get('error')}")

        print(f"  Without examples:  {no_decision}")
        print(f"  With examples:     {yes_decision}")

        if no_decision != yes_decision:
            print(f"  DIFFERENCE — examples changed the decision")
        else:
            print(f"  Same decision (examples reinforced existing behaviour)")

        print(f"  Note: {note}")
        print()

    print("=" * 65)
    print("Key takeaway:")
    print("  Few-shot examples for tool_use demonstrate JUDGMENT, not format.")
    print("  Format is enforced by the schema. Examples show the model how to")
    print("  reason about the hard cases — especially example 3 (ambiguous ticket)")
    print("  where 'needs_info' is right but the model would auto_resolve without it.")
    print()
    print("  The injection pattern (user → assistant tool_use → user tool_result)")
    print("  is required. Omitting the tool_result turn causes a 400 error.")
    print("=" * 65)


if __name__ == "__main__":
    main()

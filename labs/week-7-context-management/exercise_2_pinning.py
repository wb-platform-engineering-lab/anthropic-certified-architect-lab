"""
exercise_2_pinning.py — The Pinning Pattern (ACTIVE CONTEXT Block)

The fix for context drift: extract critical facts after every turn and
write them into a structured block immediately after the system prompt.

Why this works:
  - The block is always at the top of the conversation — where attention is highest
  - It is overwritten at each turn with the current ground truth (not appended)
  - The model sees it as established context, not a recent user message

Why placing it at the END is worse (even though it is more "recent"):
  - The model may treat it as a new user instruction rather than ground truth
  - It competes with the actual user message for the model's final-turn attention
"""

import json
from typing import Optional
import anthropic
from dotenv import load_dotenv

load_dotenv()
client = anthropic.Anthropic()

MODEL = "claude-haiku-4-5-20251001"

SYSTEM = (
    "You are a Resolve support agent handling a billing dispute. "
    "Answer questions concisely based on the conversation history."
)


# ---------------------------------------------------------------------------
# Step 1: Extract critical facts from conversation history
# ---------------------------------------------------------------------------

EXTRACT_TOOL = {
    "name": "extract_facts",
    "description": "Extract critical facts that must not be forgotten from the conversation.",
    "input_schema": {
        "type": "object",
        "required": ["refund_reference", "refund_amount", "commitments"],
        "properties": {
            "refund_reference": {
                "type": ["string", "null"],
                "description": "Refund reference number if one was given, null otherwise.",
            },
            "refund_amount": {
                "type": ["number", "null"],
                "description": "Refund amount in euros if confirmed, null otherwise.",
            },
            "commitments": {
                "type": "array",
                "items": {"type": "string"},
                "description": "List of commitments made (promises, timelines, decisions).",
            },
        },
    },
}


def extract_critical_facts(messages: list) -> dict:
    """
    Call the model to extract critical facts from the current conversation.
    Returns a dict with refund_reference, refund_amount, and commitments.
    """
    response = client.messages.create(
        model=MODEL,
        max_tokens=256,
        tools=[EXTRACT_TOOL],
        tool_choice={"type": "tool", "name": "extract_facts"},
        system="Extract critical facts from this support conversation.",
        messages=messages,
    )
    return response.content[0].input


# ---------------------------------------------------------------------------
# Step 2: Build the ACTIVE CONTEXT block
# ---------------------------------------------------------------------------

def build_active_context_block(facts: dict) -> str:
    """
    Format extracted facts into the ACTIVE CONTEXT block.
    This will be inserted as the first user message right after the system prompt.
    """
    lines = ["[ACTIVE CONTEXT — ground truth for this ticket]"]

    if facts.get("refund_reference"):
        lines.append(f"  Refund reference: {facts['refund_reference']}")
    if facts.get("refund_amount") is not None:
        lines.append(f"  Refund amount:    €{facts['refund_amount']}")
    for c in facts.get("commitments", []):
        lines.append(f"  Commitment: {c}")

    lines.append("[END ACTIVE CONTEXT]")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Step 3: Run the same conversation with and without pinning
# ---------------------------------------------------------------------------

def make_filler_turns(n: int) -> list:
    turns = []
    for i in range(n):
        turns.append({"role": "user", "content": f"Question {i+1}: Is your support available 24/7?"})
        turns.append({"role": "assistant", "content": "Yes, 24/7."})
    return turns


def build_base_conversation() -> list:
    """The conversation from Exercise 1 — fact established early, buried by filler."""
    messages = []
    # Fact established at turn 0
    messages.append({"role": "user", "content": "Has my refund been approved?"})
    messages.append({
        "role": "assistant",
        "content": "Yes, your refund of €450 has been approved. Reference: REF-2024-9182. Allow 3–5 business days.",
    })
    # 10 filler exchanges to bury the fact
    messages += make_filler_turns(10)
    # Recall question at the end
    messages.append({
        "role": "user",
        "content": "I need to follow up with my bank. What is my refund reference number and amount?",
    })
    return messages


def ask_without_pinning(messages: list) -> str:
    """Ask the recall question with no context management."""
    response = client.messages.create(
        model=MODEL, max_tokens=256, system=SYSTEM, messages=messages,
    )
    return response.content[0].text


def ask_with_pinning(messages: list) -> str:
    """
    Build the ACTIVE CONTEXT block from the first part of the conversation,
    then inject it at the top before asking the recall question.
    """
    # Extract facts from the conversation (excluding the final recall question)
    history = messages[:-1]
    facts = extract_critical_facts(history)
    active_context = build_active_context_block(facts)

    # Inject the ACTIVE CONTEXT block as the first message (after the system prompt)
    pinned_messages = [
        {"role": "user", "content": active_context},
        {"role": "assistant", "content": "Understood. I have noted these facts."},
    ] + messages

    response = client.messages.create(
        model=MODEL, max_tokens=256, system=SYSTEM, messages=pinned_messages,
    )
    return response.content[0].text


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    print("=" * 65)
    print("Exercise 2: The Pinning Pattern — ACTIVE CONTEXT Block")
    print("=" * 65)
    print()
    print("Critical fact: 'refund €450 approved, REF-2024-9182'")
    print("Buried by 10 filler exchanges before the recall question.")
    print()

    messages = build_base_conversation()

    # Test without pinning
    print("--- Without pinning ---")
    reply_no_pin = ask_without_pinning(messages)
    recalled_ref = "REF-2024-9182" in reply_no_pin
    recalled_amount = "450" in reply_no_pin
    print(f"Recalled ref:    {recalled_ref}")
    print(f"Recalled amount: {recalled_amount}")
    print(f"Reply: \"{reply_no_pin[:200]}\"")
    print()

    # Test with pinning
    print("--- With pinning ---")
    reply_pinned = ask_with_pinning(messages)
    recalled_ref = "REF-2024-9182" in reply_pinned
    recalled_amount = "450" in reply_pinned
    print(f"Recalled ref:    {recalled_ref}")
    print(f"Recalled amount: {recalled_amount}")
    print(f"Reply: \"{reply_pinned[:200]}\"")
    print()

    print("=" * 65)
    print("Key takeaway:")
    print("  The ACTIVE CONTEXT block works because it is placed at the")
    print("  BEGINNING of the conversation — where attention is highest.")
    print()
    print("  It is overwritten at each turn (not appended) with the")
    print("  current ground truth — so it never contains stale facts.")
    print()
    print("  Placing it at the END is worse: the model treats it as a")
    print("  new user message rather than established ground truth.")
    print("=" * 65)


if __name__ == "__main__":
    main()

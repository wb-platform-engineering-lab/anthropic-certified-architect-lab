"""
exercise_1_context_drift.py — Reproducing the Context Drift Failure

Context window ≠ attention window.
A fact can be within the token limit and still be "forgotten" if it is
buried in the middle of a long conversation.

This exercise builds a minimal reproduction case:
  1. Build a synthetic conversation where a critical fact is established early
  2. Ask the model to recall it many turns later
  3. Show that moving the fact later in the conversation improves recall

Key insight: The model attends most strongly to the beginning (system prompt)
and the most recent messages. Middle turns are the danger zone.
"""

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
# Build a synthetic long conversation
# ---------------------------------------------------------------------------

def make_filler_turns(n: int) -> list:
    """Generate n turns of generic back-and-forth that bury the critical fact."""
    turns = []
    for i in range(n):
        turns.append({"role": "user", "content": f"Quick question {i+1}: Is your support available 24/7?"})
        turns.append({"role": "assistant", "content": "Yes, our support team is available 24/7."})
    return turns


def build_conversation(critical_fact_position: int, total_filler_turns: int) -> list:
    """
    Build a conversation where the critical fact is inserted at
    critical_fact_position (number of filler turns before the fact).

    Structure:
      [filler turns 0..critical_fact_position-1]
      [critical fact established]
      [filler turns critical_fact_position..total_filler_turns-1]
      [recall question]
    """
    messages = []

    # Filler before the critical fact
    messages += make_filler_turns(critical_fact_position)

    # The critical fact — refund approved with reference number
    messages.append({
        "role": "user",
        "content": "Has my refund been approved?",
    })
    messages.append({
        "role": "assistant",
        "content": (
            "Yes, your refund of €450 has been approved. "
            "Your reference number is REF-2024-9182. "
            "Processing takes 3–5 business days."
        ),
    })

    # Filler after the critical fact
    remaining = total_filler_turns - critical_fact_position
    messages += make_filler_turns(remaining)

    # The recall question — placed at the end
    messages.append({
        "role": "user",
        "content": (
            "I need to follow up with my bank. "
            "Can you remind me of my refund reference number and the exact amount?"
        ),
    })

    return messages


def test_recall(critical_fact_position: int, total_filler_turns: int) -> dict:
    """
    Run the conversation and check whether the model recalls the critical fact.
    Returns the model's response and a simple pass/fail.
    """
    messages = build_conversation(critical_fact_position, total_filler_turns)

    response = client.messages.create(
        model=MODEL,
        max_tokens=256,
        system=SYSTEM,
        messages=messages,
    )
    reply = response.content[0].text

    # Check if the model recalled both the reference number and the amount
    recalled_ref = "REF-2024-9182" in reply
    recalled_amount = "450" in reply

    return {
        "fact_position": critical_fact_position,
        "total_filler": total_filler_turns,
        "turns_after_fact": total_filler_turns - critical_fact_position,
        "reply": reply,
        "recalled_ref": recalled_ref,
        "recalled_amount": recalled_amount,
        "pass": recalled_ref and recalled_amount,
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    TOTAL_FILLER = 10  # 10 filler exchanges = 20 filler messages

    print("=" * 65)
    print("Exercise 1: Context Drift — Attention vs. Context Window")
    print("=" * 65)
    print()
    print(f"Total filler exchanges: {TOTAL_FILLER}")
    print("Critical fact: 'refund €450 approved, REF-2024-9182'")
    print()
    print("Testing recall at different positions in the conversation...")
    print()

    # Test the critical fact at the start, middle, and near end
    positions = [0, 3, 7, 10]

    for pos in positions:
        result = test_recall(pos, TOTAL_FILLER)
        turns_buried = result["turns_after_fact"]
        status = "PASS" if result["pass"] else "FAIL"
        print(f"  Fact at position {pos:2d}  ({turns_buried:2d} exchanges after fact)  →  {status}")
        print(f"    Recalled ref:    {result['recalled_ref']}")
        print(f"    Recalled amount: {result['recalled_amount']}")
        print(f"    Reply: \"{result['reply'][:120]}\"")
        print()

    print("=" * 65)
    print("Key takeaway:")
    print("  Context window != attention window.")
    print("  A fact can be within the token limit and still be 'forgotten'")
    print("  when buried in the middle of a long conversation.")
    print()
    print("  The model attends most strongly to:")
    print("    1. The beginning (system prompt and early turns)")
    print("    2. The most recent messages")
    print("  Middle turns are the danger zone.")
    print("=" * 65)


if __name__ == "__main__":
    main()

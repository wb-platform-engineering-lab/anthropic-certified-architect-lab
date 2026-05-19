"""
exercise_3_summarisation.py — Progressive Summarisation: When and How

Summarisation always loses information — the question is whether it loses
the RIGHT information.

Problem: Naive summarisation compresses everything. A billing dispute that
contained "€450 refund approved, REF-2024-9182" becomes "billing issue
discussed." The agent then makes up plausible-sounding details.

Fix: Commitment-preserving summarisation.
  1. Extract all commitments (amounts, reference numbers, promises) first
  2. Compress only the surrounding context — never the commitments
  3. The final summary always contains the raw commitment text verbatim
"""

import json
import anthropic
from dotenv import load_dotenv

load_dotenv()
client = anthropic.Anthropic()

MODEL = "claude-haiku-4-5-20251001"


# ---------------------------------------------------------------------------
# Sample conversation — contains specific facts that must survive compression
# ---------------------------------------------------------------------------

SAMPLE_CONVERSATION = [
    {"role": "user", "content": "Hi, I need help with my account."},
    {"role": "assistant", "content": "Hello! I'm happy to help. What's the issue?"},
    {"role": "user", "content": "I was charged twice in May. Invoice INV-2026-0441 and INV-2026-0442 — both for €89."},
    {"role": "assistant", "content": "I can see those invoices. INV-2026-0442 does appear to be a duplicate. I'll initiate a refund."},
    {"role": "user", "content": "How long will the refund take?"},
    {"role": "assistant", "content": "The refund of €89 for INV-2026-0442 is approved. Reference REF-2026-0088. Allow 3–5 business days."},
    {"role": "user", "content": "Thank you. Will I get a confirmation email?"},
    {"role": "assistant", "content": "Yes, you'll receive a confirmation email within 1 hour."},
    {"role": "user", "content": "Also, can I upgrade my plan?"},
    {"role": "assistant", "content": "Of course! You can upgrade at any time from your account settings under Billing > Plans."},
    {"role": "user", "content": "Is there a discount for annual billing?"},
    {"role": "assistant", "content": "Yes, annual billing saves 20% compared to monthly."},
    {"role": "user", "content": "Good to know. I'll think about it."},
    {"role": "assistant", "content": "Take your time. Let me know if you need anything else."},
]

# The critical facts that must survive any compression
CRITICAL_FACTS = [
    "Duplicate charge: INV-2026-0442 for €89",
    "Refund approved: €89 for INV-2026-0442",
    "Refund reference: REF-2026-0088",
    "Timeline: 3–5 business days",
    "Confirmation email promised within 1 hour",
]


# ---------------------------------------------------------------------------
# Approach 1 — Naive summarisation (compress everything)
# ---------------------------------------------------------------------------

def summarise_naive(messages: list) -> str:
    """Compress the entire conversation into a single paragraph."""
    transcript = "\n".join(
        f"{m['role'].upper()}: {m['content']}" for m in messages
    )
    response = client.messages.create(
        model=MODEL,
        max_tokens=256,
        messages=[{
            "role": "user",
            "content": (
                f"Summarise this support conversation in 2-3 sentences:\n\n{transcript}"
            ),
        }],
    )
    return response.content[0].text


# ---------------------------------------------------------------------------
# Approach 2 — Commitment-preserving summarisation
# ---------------------------------------------------------------------------

EXTRACT_COMMITMENTS_TOOL = {
    "name": "extract_commitments",
    "description": "Extract all specific commitments made in this conversation.",
    "input_schema": {
        "type": "object",
        "required": ["commitments"],
        "properties": {
            "commitments": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "List of specific commitments: amounts, reference numbers, "
                    "deadlines, approvals. Include exact numbers and codes verbatim."
                ),
            },
        },
    },
}


def extract_commitments(messages: list) -> list:
    """Extract commitments from the conversation using tool_use."""
    transcript = "\n".join(f"{m['role'].upper()}: {m['content']}" for m in messages)
    response = client.messages.create(
        model=MODEL,
        max_tokens=256,
        tools=[EXTRACT_COMMITMENTS_TOOL],
        tool_choice={"type": "tool", "name": "extract_commitments"},
        messages=[{"role": "user", "content": f"Extract all commitments from:\n\n{transcript}"}],
    )
    return response.content[0].input.get("commitments", [])


def summarise_context_only(messages: list) -> str:
    """Summarise only the conversational context — not the commitments."""
    transcript = "\n".join(f"{m['role'].upper()}: {m['content']}" for m in messages)
    response = client.messages.create(
        model=MODEL,
        max_tokens=256,
        messages=[{
            "role": "user",
            "content": (
                "Summarise the general context of this conversation in 1-2 sentences. "
                "Do NOT include specific numbers, reference codes, or amounts — "
                "those will be preserved separately.\n\n" + transcript
            ),
        }],
    )
    return response.content[0].text


def summarise_preserving(messages: list) -> dict:
    """
    Commitment-preserving summarisation:
      1. Extract commitments verbatim
      2. Summarise everything else
      3. Return both — commitments are NEVER compressed
    """
    commitments = extract_commitments(messages)
    context_summary = summarise_context_only(messages)
    return {
        "context_summary": context_summary,
        "commitments": commitments,
    }


# ---------------------------------------------------------------------------
# Check what survived compression
# ---------------------------------------------------------------------------

def check_survival(summary_text: str) -> dict:
    """Check whether each critical fact survived compression."""
    checks = {}
    checks["invoice_number"] = "INV-2026-0442" in summary_text
    checks["refund_amount"] = "89" in summary_text
    checks["ref_number"] = "REF-2026-0088" in summary_text
    return checks


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    print("=" * 65)
    print("Exercise 3: Progressive Summarisation")
    print("=" * 65)
    print()
    print(f"Conversation: {len(SAMPLE_CONVERSATION)} turns")
    print()
    print("Critical facts that must survive:")
    for fact in CRITICAL_FACTS:
        print(f"  - {fact}")
    print()

    # Naive summarisation
    print("--- Approach 1: Naive summarisation ---")
    naive = summarise_naive(SAMPLE_CONVERSATION)
    naive_survival = check_survival(naive)
    print(f"Summary: \"{naive}\"")
    print()
    print("Critical fact survival:")
    for fact, survived in naive_survival.items():
        status = "OK" if survived else "LOST"
        print(f"  {fact:<20} {status}")
    print()

    # Commitment-preserving summarisation
    print("--- Approach 2: Commitment-preserving summarisation ---")
    preserved = summarise_preserving(SAMPLE_CONVERSATION)
    print(f"Context summary: \"{preserved['context_summary']}\"")
    print()
    print("Extracted commitments (never compressed):")
    for c in preserved["commitments"]:
        print(f"  - {c}")

    # Check survival in extracted commitments
    combined = " ".join(preserved["commitments"])
    preserved_survival = check_survival(combined)
    print()
    print("Critical fact survival in commitments:")
    for fact, survived in preserved_survival.items():
        status = "OK" if survived else "LOST"
        print(f"  {fact:<20} {status}")
    print()

    print("=" * 65)
    print("Key takeaway:")
    print("  Naive summarisation loses specific facts — invoice numbers,")
    print("  amounts, reference codes — replacing them with vague summaries.")
    print("  The model then makes up plausible-sounding replacements.")
    print()
    print("  Commitment-preserving summarisation extracts all commitments")
    print("  verbatim BEFORE compressing. The commitments are never compressed.")
    print()
    print("  Safe to compress:  greetings, acknowledgements, procedural turns")
    print("  Never compress:    any turn with a number, date, reference code,")
    print("                     or specific promise")
    print("=" * 65)


if __name__ == "__main__":
    main()

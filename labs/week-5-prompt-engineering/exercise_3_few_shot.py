"""
exercise_3_few_shot.py — Few-Shot Examples for tool_use

EXAM NOTE: Few-shot examples for tool_use are NOT about teaching the model
the output format — the schema already enforces that. They are about
demonstrating JUDGMENT on edge cases the schema cannot encode:

  - When is a small billing discrepancy worth auto-resolving vs. escalating?
  - When does "I think I was charged twice" mean needs_info vs. billing escalation?
  - What confidence level is appropriate for ambiguous tickets?

The few-shot injection pattern for tool_use is a multi-turn conversation:
  user message (the example ticket)
  → assistant message (tool_use block with the example classification)
  → user message (tool_result block — required to maintain valid turn structure)
  → ... repeat for each example ...
  → user message (the real ticket)

This differs from text completion few-shot where examples go in the system
prompt. For tool_use, examples must be in the messages array as actual tool
interactions because the model has seen tool_use in training as a conversational
pattern, not as a monologue pattern.
"""

import os
from typing import Optional

import anthropic
from dotenv import load_dotenv

load_dotenv()

client = anthropic.Anthropic()

# ---------------------------------------------------------------------------
# The v2 classify_ticket tool (same schema as exercises 1 and 2).
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
    "You are the Resolve ticket classifier for a B2B SaaS platform. "
    "Classify each support ticket using the classify_ticket tool. "
    "Be precise: escalation_team must be null if not escalating; "
    "resolution must be null if not auto-resolving. "
    "For ambiguous tickets, prefer needs_info over a low-confidence auto_resolve."
)

# ---------------------------------------------------------------------------
# 20 production tickets with expected decisions.
# Chosen to be realistic, messy, and representative of the three categories.
# ---------------------------------------------------------------------------

PRODUCTION_TICKETS = [
    # ----------------------------------------------------------------
    # Clear auto-resolve (7) — unambiguous, self-service resolvable.
    # ----------------------------------------------------------------
    {
        "id": "T001",
        "category": "clear_auto_resolve",
        "expected_decision": "auto_resolve",
        "text": (
            "Hi, I forgot my password and cant log in. "
            "Can you send me a reset link? My email is tom@example.com. Thanks"
        ),
    },
    {
        "id": "T002",
        "category": "clear_auto_resolve",
        "expected_decision": "auto_resolve",
        "text": (
            "How do I export my data as CSV? I've been looking in Settings "
            "but can't find the option. Is it a paid feature?"
        ),
    },
    {
        "id": "T003",
        "category": "clear_auto_resolve",
        "expected_decision": "auto_resolve",
        "text": (
            "What are your support hours? Do you offer 24/7 support or "
            "only business hours? We're based in Sydney (AEST)."
        ),
    },
    {
        "id": "T004",
        "category": "clear_auto_resolve",
        "expected_decision": "auto_resolve",
        "text": (
            "I need to update the billing email on my account. "
            "Currently it goes to sarah@oldcompany.com and I need it "
            "changed to sarah@newcompany.com. How do I do this?"
        ),
    },
    {
        "id": "T005",
        "category": "clear_auto_resolve",
        "expected_decision": "auto_resolve",
        "text": (
            "Is there an API rate limit? We are planning to send about "
            "500 requests per minute during batch processing jobs. "
            "Will this cause any issues on the starter plan?"
        ),
    },
    {
        "id": "T006",
        "category": "clear_auto_resolve",
        "expected_decision": "auto_resolve",
        "text": (
            "Hey, our team uses SSO via Google Workspace. One of our new "
            "hires (james@company.com) is getting an 'unauthorized' error "
            "when logging in with Google. He was just added to the workspace "
            "last week. Any ideas?"
        ),
    },
    {
        "id": "T007",
        "category": "clear_auto_resolve",
        "expected_decision": "auto_resolve",
        "text": (
            "Can you confirm whether your platform is SOC 2 Type II certified? "
            "Our procurement team needs the certification date and scope "
            "for our vendor questionnaire."
        ),
    },
    # ----------------------------------------------------------------
    # Clear escalate (7) — require human review or specialist team.
    # ----------------------------------------------------------------
    {
        "id": "T008",
        "category": "clear_escalate",
        "expected_decision": "escalate",
        "text": (
            "I have been charged €750 for a plan I cancelled three months ago. "
            "I have the cancellation confirmation email. "
            "I want a full refund and an explanation of why this happened. "
            "If not resolved today I will dispute with my bank."
        ),
    },
    {
        "id": "T009",
        "category": "clear_escalate",
        "expected_decision": "escalate",
        "text": (
            "We received a notification that our account data may have been "
            "included in a security incident. We handle sensitive customer PII "
            "and need to know: what data was exposed, when, and what your "
            "remediation plan is. Please escalate to your security team."
        ),
    },
    {
        "id": "T010",
        "category": "clear_escalate",
        "expected_decision": "escalate",
        "text": (
            "We are an enterprise customer (account #ENT-4821) and our SLA "
            "guarantees 99.9% uptime. We have experienced 6 hours of degraded "
            "service this month which breaches our contract. "
            "We need to speak with our account manager about SLA credits."
        ),
    },
    {
        "id": "T011",
        "category": "clear_escalate",
        "expected_decision": "escalate",
        "text": (
            "Our entire production database appears to have been deleted. "
            "This happened about 20 minutes ago. We have 50,000 active users "
            "and all our data is gone. This is critical. "
            "We need emergency support immediately."
        ),
    },
    {
        "id": "T012",
        "category": "clear_escalate",
        "expected_decision": "escalate",
        "text": (
            "I received a legal notice today claiming that content hosted on "
            "your platform under our account infringes on third-party IP. "
            "The notice is from a law firm. Please advise on next steps — "
            "we believe this is a DMCA takedown attempt."
        ),
    },
    {
        "id": "T013",
        "category": "clear_escalate",
        "expected_decision": "escalate",
        "text": (
            "We are looking to migrate our entire org (2,400 seats) from our "
            "current provider to your platform. We need custom pricing, a "
            "dedicated implementation team, and data migration support. "
            "Who should we talk to about enterprise contracts?"
        ),
    },
    {
        "id": "T014",
        "category": "clear_escalate",
        "expected_decision": "escalate",
        "text": (
            "I've been trying to cancel my subscription for three weeks. "
            "Every time I click 'Cancel Plan' I get an error. "
            "I've contacted support twice and got no response. "
            "I was charged again yesterday. I am furious. "
            "Escalate this to a manager immediately."
        ),
    },
    # ----------------------------------------------------------------
    # Ambiguous (6) — edge cases where judgment determines the decision.
    # ----------------------------------------------------------------
    {
        "id": "T015",
        "category": "ambiguous",
        "expected_decision": "needs_info",
        "text": (
            "I think I might have been charged twice this month?? "
            "Not 100% sure, my bank statement is a bit confusing. "
            "Can someone check pls"
        ),
    },
    {
        "id": "T016",
        "category": "ambiguous",
        "expected_decision": "needs_info",
        "text": (
            "Things seem slow today. Is it on your end or mine? "
            "Our team is in London if that matters."
        ),
    },
    {
        "id": "T017",
        "category": "ambiguous",
        "expected_decision": "needs_info",
        "text": (
            "We want to add more users but I'm not sure what plan we're on "
            "or how many seats we have left. Also how do bulk discounts work? "
            "Maybe we should upgrade? Not urgent."
        ),
    },
    {
        "id": "T018",
        "category": "ambiguous",
        "expected_decision": "needs_info",
        "text": (
            "One of my colleagues says she can access a feature that I can't. "
            "We should be on the same plan. Could be a permissions thing? "
            "Or maybe she signed up separately. I don't know."
        ),
    },
    {
        "id": "T019",
        "category": "ambiguous",
        "expected_decision": "needs_info",
        "text": (
            "Hi, we have a complaint about service recently. "
            "The team hasn't been happy. We may need to reconsider our options "
            "if things don't improve. Just wanted to flag this."
        ),
    },
    {
        "id": "T020",
        "category": "ambiguous",
        "expected_decision": "needs_info",
        "text": (
            "Can you help? Something is broken. "
            "It worked yesterday but not today. Thanks"
        ),
    },
]

# ---------------------------------------------------------------------------
# Few-shot examples — 3 chosen for maximum coverage of judgment edge cases.
#
# Selection rationale:
#   Example 1 (auto_resolve): Establishes what a clear, high-confidence
#     auto-resolve looks like. Simple password reset — no ambiguity.
#     Shows the model: write a helpful resolution, confidence can be 0.9+.
#
#   Example 2 (escalate/enterprise): Shows an enterprise billing dispute with
#     a large amount. Demonstrates: escalation_team="enterprise" for large
#     enterprise accounts, not "billing" alone. Confidence can be 0.95+.
#
#   Example 3 (needs_info — the hard one): Ambiguous small duplicate charge.
#     This is the most important example. The reason field explains the
#     judgment: "amount unconfirmed, could be legitimate" → needs_info.
#     Without this example, models often auto_resolve or escalate low-value
#     billing questions rather than asking for clarification.
# ---------------------------------------------------------------------------

FEW_SHOT_EXAMPLES = [
    {
        "ticket": (
            "Hi, I forgot my password. I've tried the 'forgot password' link "
            "three times but the email isn't arriving. My email is john@acme.com."
        ),
        "classification": {
            "decision": "auto_resolve",
            "confidence": 0.95,
            "reason": (
                "Standard password reset request. The customer has already "
                "tried the self-service flow but the email is not arriving, "
                "which is a common deliverability issue resolvable via manual reset."
            ),
            "escalation_team": None,
            "resolution": (
                "Hi John, I've manually triggered a password reset for john@acme.com. "
                "Please check your spam folder — our emails sometimes land there. "
                "If you still don't receive it within 5 minutes, reply and we'll "
                "send it to an alternate address. You can also log in via Google "
                "SSO if your account is linked."
            ),
        },
    },
    {
        "ticket": (
            "We are an enterprise customer (contract #ENT-7734, 800 seats). "
            "We have been billed €1,200 this month instead of our contracted "
            "rate of €800. The overage is unexplained. This is the second month "
            "in a row. Our CFO is now involved and we need this escalated."
        ),
        "classification": {
            "decision": "escalate",
            "confidence": 0.97,
            "reason": (
                "Enterprise customer with a contract billing discrepancy of €400 "
                "for the second consecutive month. CFO involvement and contract "
                "reference #ENT-7734 indicate this requires enterprise account "
                "management review, not standard billing support."
            ),
            "escalation_team": "enterprise",
            "resolution": None,
        },
    },
    {
        "ticket": (
            "I think maybe I got charged twice this month? My statement shows "
            "two payments but I'm not sure if one is from last month. "
            "The amounts are €29 each. Not a huge deal but want to check."
        ),
        "classification": {
            "decision": "needs_info",
            "confidence": 0.82,
            "reason": (
                "Customer believes they were double-charged €29 but is uncertain "
                "— they acknowledge one charge may be from the prior month. "
                "Without confirming the charge dates against our billing records, "
                "we cannot determine if a refund is warranted or explain the "
                "legitimate charge. Requesting account details to investigate."
            ),
            "escalation_team": None,
            "resolution": None,
        },
    },
]


# ---------------------------------------------------------------------------
# Classification functions
# ---------------------------------------------------------------------------

def classify_no_few_shot(ticket: str) -> str:
    """
    Classify a ticket using the v2 tool with NO few-shot examples.
    Returns the decision string, or "error" if the call fails.
    All tool functions return dicts — never raises uncaught exceptions.
    """
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
            return "error"
        return response.content[0].input.get("decision", "error")
    except Exception:
        return "error"


def classify_with_few_shot(ticket: str) -> str:
    """
    Classify a ticket using the v2 tool WITH few-shot examples injected as
    a multi-turn conversation.

    The injection pattern:
      user: example ticket text
      assistant: tool_use block (the example classification)
      user: tool_result block (required — maintains valid turn alternation)
      ... repeat for each example ...
      user: real ticket text

    Why this works: the model has learned in training that tool_use appears
    in a conversational pattern. By showing it previous tool_use/tool_result
    exchanges, we demonstrate the JUDGMENT behind each decision — not just
    the format, which the schema already enforces.

    Returns the decision string, or "error" if the call fails.
    """
    messages = []

    for i, example in enumerate(FEW_SHOT_EXAMPLES):
        # User turn: the example ticket.
        messages.append({
            "role": "user",
            "content": example["ticket"],
        })
        # Assistant turn: the tool call with the example classification.
        # This is a ToolUseBlock encoded as a dict in the messages array.
        messages.append({
            "role": "assistant",
            "content": [
                {
                    "type": "tool_use",
                    "id": f"example_{i}",
                    "name": "classify_ticket",
                    "input": example["classification"],
                }
            ],
        })
        # User turn: tool_result — required by the API to maintain valid
        # turn structure. Without this, the API returns a 400 error because
        # an assistant tool_use must be followed by a user tool_result.
        messages.append({
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": f"example_{i}",
                    "content": "Classification recorded.",
                }
            ],
        })

    # Now the real ticket — appended after all the example turns.
    messages.append({"role": "user", "content": ticket})

    try:
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=512,
            system=SYSTEM_PROMPT,
            tools=[CLASSIFY_TOOL],
            tool_choice={"type": "tool", "name": "classify_ticket"},
            messages=messages,
        )
        if response.stop_reason != "tool_use":
            return "error"
        return response.content[0].input.get("decision", "error")
    except Exception:
        return "error"


# ---------------------------------------------------------------------------
# Accuracy measurement
# ---------------------------------------------------------------------------

def measure_accuracy(classify_fn, tickets: list) -> dict:
    """
    Run classify_fn against all tickets and compare to expected_decision.

    Returns:
      {
        "correct": int,
        "total": int,
        "accuracy": float,
        "by_category": {
          "clear_auto_resolve": {"correct": int, "total": int, "accuracy": float},
          "clear_escalate":     {"correct": int, "total": int, "accuracy": float},
          "ambiguous":          {"correct": int, "total": int, "accuracy": float},
        },
        "results": [
          {"id": str, "category": str, "expected": str, "got": str, "correct": bool},
          ...
        ]
      }
    """
    by_category: dict = {}
    results = []
    total_correct = 0

    for ticket_data in tickets:
        ticket_id = ticket_data["id"]
        category = ticket_data["category"]
        expected = ticket_data["expected_decision"]
        text = ticket_data["text"]

        got = classify_fn(text)
        correct = got == expected

        if correct:
            total_correct += 1

        results.append({
            "id": ticket_id,
            "category": category,
            "expected": expected,
            "got": got,
            "correct": correct,
        })

        if category not in by_category:
            by_category[category] = {"correct": 0, "total": 0}
        by_category[category]["total"] += 1
        if correct:
            by_category[category]["correct"] += 1

    # Compute per-category accuracy.
    for cat in by_category:
        cat_data = by_category[cat]
        n = cat_data["total"]
        cat_data["accuracy"] = round(cat_data["correct"] / n, 2) if n > 0 else 0.0

    total = len(tickets)
    return {
        "correct": total_correct,
        "total": total,
        "accuracy": round(total_correct / total, 2) if total > 0 else 0.0,
        "by_category": by_category,
        "results": results,
    }


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main() -> None:
    print("=" * 70)
    print("EXERCISE 3: Few-Shot Examples for tool_use")
    print("=" * 70)
    print()
    print(f"Running baseline (no few-shot) against {len(PRODUCTION_TICKETS)} tickets...")
    print("(This makes one API call per ticket — takes ~30-60 seconds)")
    print()

    baseline = measure_accuracy(classify_no_few_shot, PRODUCTION_TICKETS)

    print(f"Running few-shot against {len(PRODUCTION_TICKETS)} tickets...")
    print()

    few_shot = measure_accuracy(classify_with_few_shot, PRODUCTION_TICKETS)

    # ------------------------------------------------------------------
    # Per-ticket results table
    # ------------------------------------------------------------------
    print("--- Per-Ticket Results ---")
    print(f"{'ID':<6} {'Category':<20} {'Expected':<15} {'Baseline':<15} {'Few-Shot':<15}")
    print("-" * 75)

    # Build lookup dicts for easy comparison.
    baseline_by_id = {r["id"]: r for r in baseline["results"]}
    few_shot_by_id = {r["id"]: r for r in few_shot["results"]}

    for ticket_data in PRODUCTION_TICKETS:
        tid = ticket_data["id"]
        category = ticket_data["category"]
        expected = ticket_data["expected_decision"]
        b_got = baseline_by_id[tid]["got"]
        f_got = few_shot_by_id[tid]["got"]
        b_mark = "OK" if baseline_by_id[tid]["correct"] else "WRONG"
        f_mark = "OK" if few_shot_by_id[tid]["correct"] else "WRONG"
        print(f"{tid:<6} {category:<20} {expected:<15} {b_got+' '+b_mark:<15} {f_got+' '+f_mark:<15}")

    print()

    # ------------------------------------------------------------------
    # Summary comparison table
    # ------------------------------------------------------------------
    print("--- Accuracy Comparison ---")
    print(f"{'Category':<25} {'Baseline':>10} {'Few-Shot':>10} {'Delta':>10}")
    print("-" * 55)

    categories = ["clear_auto_resolve", "clear_escalate", "ambiguous"]
    for cat in categories:
        b_acc = baseline["by_category"].get(cat, {}).get("accuracy", 0.0)
        f_acc = few_shot["by_category"].get(cat, {}).get("accuracy", 0.0)
        delta = f_acc - b_acc
        delta_str = f"+{delta:.0%}" if delta > 0 else f"{delta:.0%}"
        print(f"{cat:<25} {b_acc:>10.0%} {f_acc:>10.0%} {delta_str:>10}")

    print("-" * 55)
    b_total = baseline["accuracy"]
    f_total = few_shot["accuracy"]
    delta_total = f_total - b_total
    delta_total_str = f"+{delta_total:.0%}" if delta_total > 0 else f"{delta_total:.0%}"
    print(f"{'OVERALL':<25} {b_total:>10.0%} {f_total:>10.0%} {delta_total_str:>10}")
    print()

    # ------------------------------------------------------------------
    # Key insight
    # ------------------------------------------------------------------
    print("=" * 70)
    print("Key insight:")
    print()
    print("  Few-shot examples for tool_use teach JUDGMENT, not FORMAT.")
    print()
    print("  The schema already guarantees format — the model cannot return")
    print("  'auto-resolve' or omit a required field. What the schema cannot")
    print("  encode is when a small billing discrepancy warrants needs_info")
    print("  vs. escalate, or why a vague complaint is not auto-resolvable.")
    print()
    print("  The three examples cover three judgment patterns:")
    print("    1. High-confidence auto_resolve (password reset → write resolution)")
    print("    2. Enterprise escalation (large amount + contract ref → enterprise team)")
    print("    3. Ambiguous billing → needs_info (not auto_resolve or escalate)")
    print()
    print("  Accuracy improvements are most visible in the 'ambiguous' category")
    print("  because that is exactly what the third example teaches.")
    print("=" * 70)


if __name__ == "__main__":
    main()

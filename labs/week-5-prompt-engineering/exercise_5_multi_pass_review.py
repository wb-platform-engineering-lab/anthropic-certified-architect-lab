"""
exercise_5_multi_pass_review.py — Multi-Pass Review for High-Stakes Output

For high-stakes tickets (enterprise tier, billing dispute, potential credit > €500),
a single classification pass is not enough. A second model call reviews the first
classification and can override it with a justification.

Two-pass is justified when:
  - The cost of a wrong classification is high (escalate→auto_resolve loses €500+)
  - The ticket has characteristics that make it genuinely ambiguous
  - The first-pass classifier has a known error rate on this ticket type

Two-pass is NOT justified for routine tickets: the token cost doubles.
The selector function at the bottom decides which path each ticket takes.
"""

import json
import time
import anthropic
from dataclasses import dataclass
from typing import Optional
from dotenv import load_dotenv

load_dotenv()
client = anthropic.Anthropic()

# ---------------------------------------------------------------------------
# Tool schemas
# ---------------------------------------------------------------------------

# Standard v2 classify_ticket tool — identical to exercises 1-4.
# Defined here so this file is self-contained and runnable independently.
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

# The review tool — used in the second pass to confirm or override.
# EXAM NOTE: The review tool has a different schema than classify_ticket because
# its job is different: it does not re-classify from scratch, it evaluates the
# initial classification and produces a verdict (confirmed/overridden).
# Separating the schemas keeps each model call focused on a single responsibility.
REVIEW_TOOL = {
    "name": "review_classification",
    "description": (
        "Review an initial ticket classification and either confirm it or "
        "override it with justification."
    ),
    "input_schema": {
        "type": "object",
        "required": [
            "verdict",
            "justification",
            "overriding_decision",
            "overriding_escalation_team",
        ],
        "properties": {
            "verdict": {
                "type": "string",
                "enum": ["confirmed", "overridden"],
                "description": (
                    "confirmed: the initial classification is correct. "
                    "overridden: the initial classification is wrong and the "
                    "override below should be used instead."
                ),
            },
            "justification": {
                "type": "string",
                "description": (
                    "Required explanation of why the classification was confirmed "
                    "or overridden. Must reference specific facts from the ticket."
                ),
            },
            "overriding_decision": {
                "type": ["string", "null"],
                "enum": ["auto_resolve", "escalate", "needs_info", None],
                "description": (
                    "The correct decision if verdict is overridden. "
                    "Null if verdict is confirmed."
                ),
            },
            "overriding_escalation_team": {
                "type": ["string", "null"],
                "enum": ["billing", "technical", "enterprise", "legal", None],
                "description": (
                    "The correct escalation team if overriding_decision is escalate. "
                    "Null otherwise."
                ),
            },
        },
    },
}


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

@dataclass
class FirstPassResult:
    """
    Output of the first-pass classifier.

    input_tokens and output_tokens are captured from response.usage so the
    two-pass token cost can be compared against the one-pass cost in the summary.
    """
    decision: str
    confidence: float
    reason: str
    escalation_team: Optional[str]
    resolution: Optional[str]
    input_tokens: int
    output_tokens: int


@dataclass
class ReviewResult:
    """
    Output of the second-pass reviewer.

    verdict is "confirmed" or "overridden".
    If overridden, overriding_decision and overriding_escalation_team hold the
    corrected values. If confirmed, both are None.
    """
    verdict: str          # "confirmed" | "overridden"
    justification: str
    overriding_decision: Optional[str]
    overriding_escalation_team: Optional[str]
    input_tokens: int
    output_tokens: int


@dataclass
class TwoPassResult:
    """
    The combined output of a two-pass classification.

    final_decision is always set:
      - If reviewer confirmed: final_decision = first_pass.decision
      - If reviewer overrode:  final_decision = review.overriding_decision

    was_overridden is True when the reviewer changed the first-pass decision.
    This is the key metric for evaluating whether two-pass is warranted:
    high override rates indicate the first-pass prompt needs improvement;
    very low rates indicate two-pass may not be cost-effective.

    EXAM NOTE: Track override rates in production. If below ~5%, the first-pass
    classifier is reliable enough that two-pass adds cost without benefit.
    If above ~25%, the first-pass prompt has a systematic problem that should
    be fixed at the prompt level, not papered over with review passes.
    """
    ticket: str
    first_pass: FirstPassResult
    review: ReviewResult
    final_decision: str       # first_pass.decision if confirmed, overriding_decision if overridden
    total_input_tokens: int
    total_output_tokens: int
    was_overridden: bool


# ---------------------------------------------------------------------------
# First-pass classifier
# ---------------------------------------------------------------------------

def classify_first_pass(ticket: str) -> FirstPassResult:
    """
    Standard single-pass ticket classification using the v2 tool.

    Returns a FirstPassResult with token usage captured for cost comparison.
    Never raises — returns a sentinel result on error.

    EXAM NOTE: In a real system, the first-pass prompt would include few-shot
    examples (as in exercise_3_few_shot.py) to improve accuracy on known edge
    cases. Here we use a minimal system prompt to keep the exercise focused on
    the two-pass review pattern itself.
    """
    try:
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=512,
            system=(
                "You are the Resolve ticket classifier for a B2B SaaS platform. "
                "Classify each support ticket using the classify_ticket tool. "
                "Be precise: escalation_team must be null if not escalating; "
                "resolution must be null if not auto-resolving."
            ),
            tools=[CLASSIFY_TOOL],
            tool_choice={"type": "tool", "name": "classify_ticket"},
            messages=[{"role": "user", "content": ticket}],
        )

        if response.stop_reason != "tool_use":
            # Unexpected stop — return a safe escalation sentinel.
            return FirstPassResult(
                decision="escalate",
                confidence=0.0,
                reason=f"first-pass error: unexpected stop_reason={response.stop_reason}",
                escalation_team="technical",
                resolution=None,
                input_tokens=response.usage.input_tokens,
                output_tokens=response.usage.output_tokens,
            )

        tool_input = response.content[0].input
        return FirstPassResult(
            decision=tool_input.get("decision", "escalate"),
            confidence=tool_input.get("confidence", 0.0),
            reason=tool_input.get("reason", ""),
            escalation_team=tool_input.get("escalation_team"),
            resolution=tool_input.get("resolution"),
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
        )

    except Exception as exc:
        # API call failed. Return a safe sentinel that escalates to technical.
        return FirstPassResult(
            decision="escalate",
            confidence=0.0,
            reason=f"first-pass api error: {exc}",
            escalation_team="technical",
            resolution=None,
            input_tokens=0,
            output_tokens=0,
        )


# ---------------------------------------------------------------------------
# Second-pass reviewer
# ---------------------------------------------------------------------------

def review_classification(ticket: str, initial: FirstPassResult) -> ReviewResult:
    """
    Review the first-pass classification and confirm or override it.

    The reviewer receives:
      - The original ticket text (so it can re-read the facts)
      - The initial classification (decision, confidence, reason, escalation_team)

    The reviewer uses the review_classification tool with verdict=confirmed or
    verdict=overridden. If overriding, it must supply overriding_decision and
    (if that decision is "escalate") overriding_escalation_team.

    The system prompt explicitly instructs the reviewer to be CRITICAL and to
    flag any mismatch between the ticket facts and the initial classification.

    EXAM NOTE: The reviewer model should be the same model as the classifier
    for cost predictability. Using a larger model for review and smaller for
    first-pass is tempting but adds latency and makes cost modelling harder.
    The two-pass benefit comes from a different PROMPT perspective (adversarial
    review vs. initial classification), not a smarter model.
    """
    # Build the user message that presents the initial classification for review.
    # Including all first-pass fields gives the reviewer full context to spot errors
    # (e.g., wrong team, misread urgency cues, overconfident auto_resolve).
    review_message = (
        f"Ticket: {ticket}\n\n"
        f"Initial classification:\n"
        f"  decision: {initial.decision}\n"
        f"  confidence: {initial.confidence}\n"
        f"  reason: {initial.reason}\n"
        f"  escalation_team: {initial.escalation_team}\n\n"
        f"Review this classification. If the initial decision is correct, confirm it. "
        f"If the initial decision is wrong (wrong team, should escalate instead of "
        f"auto-resolve, etc.), override it and explain why."
    )

    try:
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=512,
            system=(
                "You are a senior support classification reviewer. Your job is to review "
                "an initial classification and identify any errors. Be critical — the cost "
                "of a wrong escalation decision is high."
            ),
            tools=[REVIEW_TOOL],
            tool_choice={"type": "tool", "name": "review_classification"},
            messages=[{"role": "user", "content": review_message}],
        )

        if response.stop_reason != "tool_use":
            # Unexpected stop — default to confirming to avoid double-escalation noise.
            return ReviewResult(
                verdict="confirmed",
                justification=f"review error: unexpected stop_reason={response.stop_reason}",
                overriding_decision=None,
                overriding_escalation_team=None,
                input_tokens=response.usage.input_tokens,
                output_tokens=response.usage.output_tokens,
            )

        tool_input = response.content[0].input
        return ReviewResult(
            verdict=tool_input.get("verdict", "confirmed"),
            justification=tool_input.get("justification", ""),
            overriding_decision=tool_input.get("overriding_decision"),
            overriding_escalation_team=tool_input.get("overriding_escalation_team"),
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
        )

    except Exception as exc:
        # API call failed. Default to confirming the first pass to avoid
        # introducing errors from a broken review pass.
        return ReviewResult(
            verdict="confirmed",
            justification=f"review api error: {exc}",
            overriding_decision=None,
            overriding_escalation_team=None,
            input_tokens=0,
            output_tokens=0,
        )


# ---------------------------------------------------------------------------
# Two-pass orchestrator
# ---------------------------------------------------------------------------

def run_two_pass(ticket: str) -> TwoPassResult:
    """
    Run the full two-pass pipeline: first-pass classify → second-pass review.

    The final_decision is determined by the reviewer's verdict:
      - "confirmed": use the first-pass decision as-is.
      - "overridden": use the reviewer's overriding_decision instead.

    Token totals are the sum of both passes. These are used in the summary to
    show the real cost of two-pass vs. one-pass.

    EXAM NOTE: The two calls are sequential (not parallel) because the review
    pass depends on the first-pass output. If you want to reduce latency for
    multiple tickets, parallelise across tickets (e.g., ThreadPoolExecutor),
    not within a single ticket's pipeline.
    """
    # First pass.
    first_pass = classify_first_pass(ticket)

    # Second pass — receives first-pass output as context.
    review = review_classification(ticket, first_pass)

    # Determine the final decision.
    was_overridden = review.verdict == "overridden"
    if was_overridden and review.overriding_decision is not None:
        final_decision = review.overriding_decision
    else:
        final_decision = first_pass.decision

    return TwoPassResult(
        ticket=ticket,
        first_pass=first_pass,
        review=review,
        final_decision=final_decision,
        total_input_tokens=first_pass.input_tokens + review.input_tokens,
        total_output_tokens=first_pass.output_tokens + review.output_tokens,
        was_overridden=was_overridden,
    )


# ---------------------------------------------------------------------------
# High-stakes selector
# ---------------------------------------------------------------------------

def is_high_stakes(ticket: str, account_tier: str = "standard") -> bool:
    """
    Decide whether a ticket warrants the two-pass review pipeline.

    Returns True if any of the following conditions hold:
      1. The account tier is "enterprise" — enterprise SLAs make wrong
         classifications expensive regardless of ticket content.
      2. The ticket mentions a monetary amount >= €500 — large refund
         or credit decisions need human-quality review.
      3. The ticket contains legal/compliance language — these can have
         regulatory implications beyond support resolution.
      4. The ticket mentions an SLA breach — contractual obligations
         require specialist handling, not auto-resolution.

    EXAM NOTE: The is_high_stakes selector is a heuristic, not a guarantee.
    It should be tuned based on observed false-negative rates (high-stakes
    tickets incorrectly sent through one-pass). Log all routing decisions
    so you can audit the selector's accuracy over time.
    """
    ticket_lower = ticket.lower()

    # Condition 1: Enterprise account tier (all enterprise tickets are high-stakes).
    if account_tier == "enterprise":
        return True

    # Condition 2: Large monetary amount (€500+).
    # Simple heuristic: ticket contains "€" or "euro" and a number >= 500.
    # In production, use a regex or NER model for more robust amount extraction.
    if ("€" in ticket or "euro" in ticket_lower):
        # Look for any number >= 500 in the ticket text.
        import re
        amounts = re.findall(r"\d+(?:,\d{3})*(?:\.\d+)?", ticket)
        for amount_str in amounts:
            amount = float(amount_str.replace(",", ""))
            if amount >= 500:
                return True

    # Condition 3: Legal or compliance language.
    if any(kw in ticket_lower for kw in ("data breach", "legal", "compliance")):
        return True

    # Condition 4: SLA breach.
    if "sla" in ticket_lower and "breach" in ticket_lower:
        return True

    return False


# ---------------------------------------------------------------------------
# Smart classifier (one-pass or two-pass selector)
# ---------------------------------------------------------------------------

def classify_ticket_smart(ticket: str, account_tier: str = "standard") -> dict:
    """
    Route each ticket to one-pass or two-pass based on is_high_stakes().

    Returns a dict with:
      "mode": "one_pass" | "two_pass"
      "result": FirstPassResult | TwoPassResult

    EXAM NOTE: The selector pattern is important for cost control. Applying
    two-pass to all tickets roughly doubles token cost. By routing only
    high-stakes tickets to two-pass, you get the safety benefit where it
    matters without paying for it on routine tickets.

    In production, you would also log the routing decision (which mode was
    chosen and why) to a data store so you can audit the selector and adjust
    the is_high_stakes() thresholds.
    """
    if is_high_stakes(ticket, account_tier):
        result = run_two_pass(ticket)
        return {"mode": "two_pass", "result": result}
    else:
        result = classify_first_pass(ticket)
        return {"mode": "one_pass", "result": result}


# ---------------------------------------------------------------------------
# Test tickets for main()
# ---------------------------------------------------------------------------

# ---- High-stakes tickets (will use two-pass) ----

# 1. Large billing dispute — €750 refund request with legal threat.
TICKET_HS_1 = (
    "I have been charged €750 for a plan I cancelled three months ago. "
    "I have the cancellation confirmation. I want a full refund immediately. "
    "If not resolved today I will initiate a chargeback with my bank."
)

# 2. Enterprise SLA breach — contractual violation requires account manager.
TICKET_HS_2 = (
    "We are an enterprise customer (account #ENT-9901). Our SLA guarantees "
    "99.95% uptime. We have experienced 4 hours of degraded service this week. "
    "This is an SLA breach. We need to discuss credits with our account manager."
)

# 3. Security/data breach — legal and compliance implications.
TICKET_HS_3 = (
    "We received a notification that our account data may have been included "
    "in a data breach. We process sensitive customer PII. We need to know what "
    "was exposed and when. Our legal team is asking for a written incident report."
)

# 4. Large enterprise migration deal — €500+ implied deal value.
TICKET_HS_4 = (
    "We are looking to migrate 3,000 seats from our current provider. "
    "The annual contract would be approximately €180,000. We need custom "
    "pricing, dedicated implementation support, and a data migration plan. "
    "Please connect us with enterprise sales and legal for contract review."
)

# 5. Compliance audit request — regulatory language triggers high-stakes.
TICKET_HS_5 = (
    "Our external auditors are requesting documentation of your GDPR compliance "
    "controls, data processing agreements, and sub-processor list. "
    "This is required for our annual compliance audit. "
    "Please provide the relevant legal and compliance documentation."
)

# ---- Routine tickets (will use one-pass) ----

# 6. Simple password reset — unambiguous, self-service resolvable.
TICKET_ROUTINE_1 = (
    "Hi, I forgot my password and can't log in. "
    "Can you send me a reset link? My email is user@example.com."
)

# 7. Feature question — FAQ-level, no financial or legal implications.
TICKET_ROUTINE_2 = (
    "How do I export my reports as PDF? "
    "I can see CSV export but not PDF. Is it available on the Pro plan?"
)

# 8. Support hours inquiry — simple informational request.
TICKET_ROUTINE_3 = (
    "What are your support hours? Do you offer weekend support? "
    "We are based in Australia (AEST)."
)

# 9. New user SSO issue — common technical issue with self-service resolution.
TICKET_ROUTINE_4 = (
    "One of our new hires can't log in with Google SSO. She was added to "
    "our workspace last week. She's getting an 'unauthorized domain' error. "
    "Her email is newuser@ourcompany.com."
)

# 10. Plan and billing question — informational, no dispute.
TICKET_ROUTINE_5 = (
    "We have 12 users and are thinking about upgrading from Starter to Pro. "
    "What's the difference in API rate limits between the two plans? "
    "Also, do you offer annual billing with a discount?"
)

ALL_TICKETS = [
    # (label, ticket, account_tier)
    # High-stakes
    ("HS-1  Billing dispute €750",          TICKET_HS_1,      "standard"),
    ("HS-2  Enterprise SLA breach",          TICKET_HS_2,      "enterprise"),
    ("HS-3  Data breach / legal",            TICKET_HS_3,      "standard"),
    ("HS-4  Enterprise migration deal",      TICKET_HS_4,      "enterprise"),
    ("HS-5  Compliance audit / legal",       TICKET_HS_5,      "standard"),
    # Routine
    ("RT-1  Password reset",                 TICKET_ROUTINE_1, "standard"),
    ("RT-2  PDF export question",            TICKET_ROUTINE_2, "standard"),
    ("RT-3  Support hours",                  TICKET_ROUTINE_3, "standard"),
    ("RT-4  SSO new hire error",             TICKET_ROUTINE_4, "standard"),
    ("RT-5  Plan upgrade question",          TICKET_ROUTINE_5, "standard"),
]


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main() -> None:
    """
    Run 10 tickets through the smart classifier and print per-ticket results
    followed by a summary that shows the override rate and token cost comparison.

    EXAM NOTE: The summary metrics at the end are the operational output that
    justifies (or refutes) the two-pass investment. If the override rate is
    close to zero, the first-pass classifier is reliable and two-pass adds
    cost without safety benefit. If the override rate is high, the first-pass
    prompt needs improvement.
    """
    print("=" * 70)
    print("EXERCISE 5: Multi-Pass Review for High-Stakes Output")
    print("=" * 70)
    print()
    print("Running 10 tickets (5 high-stakes → two-pass, 5 routine → one-pass)")
    print("Each API call takes ~1-3 seconds. Total runtime: ~30-60 seconds.")
    print()

    two_pass_results = []
    one_pass_results = []

    for label, ticket, account_tier in ALL_TICKETS:
        print(f"--- {label} ---")

        outcome = classify_ticket_smart(ticket, account_tier)
        mode = outcome["mode"]
        result = outcome["result"]

        if mode == "two_pass":
            # result is a TwoPassResult
            tpr: TwoPassResult = result
            two_pass_results.append(tpr)

            print(f"  Mode            : two-pass (high-stakes)")
            print(f"  First-pass      : {tpr.first_pass.decision} "
                  f"(confidence {tpr.first_pass.confidence:.2f})")

            if tpr.was_overridden:
                print(f"  Review verdict  : OVERRIDDEN")
                print(f"  Override to     : {tpr.review.overriding_decision} "
                      f"/ team={tpr.review.overriding_escalation_team}")
            else:
                print(f"  Review verdict  : confirmed")

            print(f"  Final decision  : {tpr.final_decision}")
            # Truncate long justifications for readability.
            just = tpr.review.justification
            just_preview = just[:100] + "..." if len(just) > 100 else just
            print(f"  Justification   : {just_preview}")
            print(f"  Tokens (in/out) : {tpr.total_input_tokens} / {tpr.total_output_tokens}")

        else:
            # result is a FirstPassResult
            fpr: FirstPassResult = result
            one_pass_results.append(fpr)

            print(f"  Mode            : one-pass (routine)")
            print(f"  Decision        : {fpr.decision} "
                  f"(confidence {fpr.confidence:.2f})")
            print(f"  Tokens (in/out) : {fpr.input_tokens} / {fpr.output_tokens}")

        print()

    # ------------------------------------------------------------------
    # Summary statistics
    # ------------------------------------------------------------------
    print("=" * 70)
    print("SUMMARY")
    print("=" * 70)
    print()

    # Two-pass statistics.
    n_two_pass = len(two_pass_results)
    n_overridden = sum(1 for r in two_pass_results if r.was_overridden)
    n_confirmed = n_two_pass - n_overridden
    override_rate = (n_overridden / n_two_pass * 100) if n_two_pass > 0 else 0.0
    total_two_pass_input = sum(r.total_input_tokens for r in two_pass_results)
    total_two_pass_output = sum(r.total_output_tokens for r in two_pass_results)
    # Estimate one-pass cost for comparison (just first-pass tokens, not review tokens).
    one_pass_equivalent_input = sum(r.first_pass.input_tokens for r in two_pass_results)
    one_pass_equivalent_output = sum(r.first_pass.output_tokens for r in two_pass_results)
    review_overhead_input = total_two_pass_input - one_pass_equivalent_input
    review_overhead_output = total_two_pass_output - one_pass_equivalent_output

    print(f"Two-pass results ({n_two_pass} tickets):")
    print(f"  Confirmed  : {n_confirmed}")
    print(f"  Overridden : {n_overridden}")
    print(f"  Override rate: {override_rate:.1f}%")
    print(f"  Total tokens: {total_two_pass_input + total_two_pass_output:,} "
          f"(in={total_two_pass_input:,}, out={total_two_pass_output:,})")
    print(f"  Review overhead: +{review_overhead_input + review_overhead_output:,} tokens "
          f"vs one-pass equivalent ({one_pass_equivalent_input + one_pass_equivalent_output:,})")
    print()

    # One-pass statistics.
    n_one_pass = len(one_pass_results)
    total_one_pass_input = sum(r.input_tokens for r in one_pass_results)
    total_one_pass_output = sum(r.output_tokens for r in one_pass_results)

    print(f"One-pass results ({n_one_pass} tickets):")
    print(f"  Total tokens: {total_one_pass_input + total_one_pass_output:,} "
          f"(in={total_one_pass_input:,}, out={total_one_pass_output:,})")
    print()

    # Key insight based on the override rate.
    print("-" * 70)
    if override_rate == 0.0:
        insight = (
            "Override rate of 0% suggests the first-pass classifier is very reliable\n"
            "  on these ticket types. Consider whether two-pass is cost-effective,\n"
            "  or reduce the high-stakes criteria threshold."
        )
    elif override_rate <= 20.0:
        insight = (
            f"Override rate of {override_rate:.1f}% is at or below the 20% threshold.\n"
            "  The first-pass classifier handles most cases correctly. Two-pass is\n"
            "  catching the edge cases that matter most. This is the target range."
        )
    else:
        insight = (
            f"Override rate of {override_rate:.1f}% is ABOVE the 20% threshold.\n"
            "  The first-pass prompt has a systematic problem on these ticket types.\n"
            "  Fix the first-pass prompt (add few-shot examples, tighten instructions)\n"
            "  rather than relying on two-pass to correct it every time."
        )

    print(f"INSIGHT: {insight}")
    print()

    # EXAM NOTE summary.
    print("=" * 70)
    print("EXAM NOTES from this exercise:")
    print()
    print("  1. Two-pass is a cost/risk trade-off, not a universal improvement.")
    print("     Apply it selectively using a is_high_stakes() selector.")
    print()
    print("  2. The review model sees the same ticket + the first-pass output.")
    print("     This 'adversarial review' perspective catches different errors")
    print("     than re-running the same prompt twice would.")
    print()
    print("  3. Track override rate in production. Target: 5%-20%.")
    print("     Below 5%: two-pass is probably not justified for this ticket type.")
    print("     Above 25%: fix the first-pass prompt, not the review system.")
    print()
    print("  4. Use the same model for both passes for cost predictability.")
    print("     The benefit comes from different prompt perspectives, not model size.")
    print()
    print("  5. Token cost nearly doubles for two-pass tickets. The selector")
    print("     function is the mechanism that makes this sustainable at scale.")
    print("=" * 70)


if __name__ == "__main__":
    main()

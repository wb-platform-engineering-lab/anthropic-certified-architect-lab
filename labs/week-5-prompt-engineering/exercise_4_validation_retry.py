"""
exercise_4_validation_retry.py — The Validation-Retry Loop

tool_use enforcement guarantees the model calls the tool and fills all
required fields. It does NOT enforce cross-field business rules that cannot
be expressed in JSON Schema. This file builds the retry loop that handles those.

The pattern:
  1. Call API with tool_choice
  2. Extract tool input
  3. Validate business rules → if PASS, done
  4. If FAIL: add tool_result with is_error=True and structured error message
  5. Retry (up to MAX_RETRIES=3)
  6. After MAX_RETRIES failures: return typed ClassificationResult(status="escalation",
     reason="validation_exhausted")

The error message in is_error tool_result directly affects whether the model
corrects the right thing. Vague → repeated wrong corrections. Specific → model
understands exactly what to fix.
"""

import json
import anthropic
from dataclasses import dataclass, field
from typing import Optional
from dotenv import load_dotenv

load_dotenv()
client = anthropic.Anthropic()

# Maximum number of validation-retry attempts before giving up.
MAX_RETRIES = 3

# auto_resolve decisions with confidence below this threshold are rejected.
# Cross-field rule: cannot be expressed in JSON Schema (which can only constrain
# confidence independently, not as a function of the decision field value).
LOW_CONFIDENCE_THRESHOLD = 0.6

# ---------------------------------------------------------------------------
# The v2 classify_ticket tool — identical schema used across all exercises.
# Defined once here so the tool definition is always in sync with schema_v2.json.
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


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

@dataclass
class ValidationError:
    """
    A structured business-rule violation.

    Two message levels are stored so the retry loop can escalate specificity:
      - message: sent on the first failure (concise, names the rule)
      - detailed_message: sent on the second+ failure (more explicit, shows
        what a valid value looks like so the model has less guessing to do)

    EXAM NOTE: The quality of the error message is the single biggest lever
    in validation-retry loops. Vague messages ("invalid value") cause the
    model to guess what to fix. Specific messages ("confidence 0.42 is below
    the 0.6 minimum — either raise it or change decision to 'escalate'")
    give the model a concrete correction path.
    """
    field: str
    rule: str
    message: str            # generic first-pass message
    detailed_message: str   # more explicit second-pass message with example of valid value


@dataclass
class ClassificationResult:
    """
    The typed output of classify_with_retry.

    status values:
      "success"    — validation passed; decision/confidence/reason fields are populated.
      "escalation" — either validation_exhausted (all retries failed) or an
                     unexpected API stop_reason. The escalation_reason field
                     records why.

    EXAM NOTE: Always use a typed result dataclass rather than returning raw
    dicts from retry loops. The audit_log field is essential for debugging
    production classification failures.
    """
    status: str                             # "success" | "escalation"
    decision: Optional[str] = None
    confidence: Optional[float] = None
    reason: Optional[str] = None
    escalation_team: Optional[str] = None
    resolution: Optional[str] = None
    retries_used: int = 0
    escalation_reason: Optional[str] = None    # set when status == "escalation"
    audit_log: list = field(default_factory=list)  # full retry history for debugging


# ---------------------------------------------------------------------------
# Business-rule validation
# ---------------------------------------------------------------------------

def validate_business_rules(tool_input: dict) -> Optional[ValidationError]:
    """
    Check cross-field business rules that JSON Schema cannot express.

    Returns the FIRST violation found (one error at a time). Returning multiple
    errors at once is tempting but counterproductive: the model may partially
    address them and introduce new violations. One error → one focused correction.

    Rules checked (in order of severity):
      1. auto_resolve requires confidence >= LOW_CONFIDENCE_THRESHOLD
      2. escalate requires escalation_team (not null)
      3. auto_resolve requires resolution text (not null)

    EXAM NOTE: These are cross-field constraints — they involve two fields
    simultaneously (decision AND confidence, decision AND escalation_team, etc.).
    JSON Schema "if/then" conditionals can express some of these, but the
    Anthropic tool input_schema only supports a subset of JSON Schema, and
    even full JSON Schema cannot express numeric thresholds that depend on
    another field's value. Application-layer validation is the correct approach.

    Returns None if all rules pass.
    """
    decision = tool_input.get("decision")
    confidence = tool_input.get("confidence", 0.0)
    escalation_team = tool_input.get("escalation_team")
    resolution = tool_input.get("resolution")

    # Rule 1: auto_resolve requires high enough confidence.
    # Low-confidence auto-resolve means the model is guessing — better to
    # escalate or ask for more info than to send a wrong automated reply.
    if decision == "auto_resolve" and confidence < LOW_CONFIDENCE_THRESHOLD:
        return ValidationError(
            field="confidence",
            rule="AUTO_RESOLVE_CONFIDENCE_TOO_LOW",
            message=(
                f"Confidence {confidence:.2f} is below the {LOW_CONFIDENCE_THRESHOLD} "
                f"threshold required for auto_resolve."
            ),
            detailed_message=(
                f"Confidence {confidence:.2f} is below the {LOW_CONFIDENCE_THRESHOLD} minimum "
                f"for auto_resolve. Either set confidence >= {LOW_CONFIDENCE_THRESHOLD} "
                f"(only if the ticket genuinely warrants auto-resolution with high certainty) "
                f"or change decision to 'escalate' or 'needs_info'."
            ),
        )

    # Rule 2: escalate requires an escalation_team.
    # Without a team assignment, the ticket goes nowhere in the routing queue.
    if decision == "escalate" and escalation_team is None:
        return ValidationError(
            field="escalation_team",
            rule="ESCALATION_TEAM_REQUIRED",
            message="escalation_team is required when decision is escalate.",
            detailed_message=(
                "When decision is 'escalate', escalation_team must be one of: "
                "billing, technical, enterprise, legal. It cannot be null. "
                "Choose the team that owns this ticket type."
            ),
        )

    # Rule 3: auto_resolve requires a resolution (the customer-facing reply).
    # An auto_resolve without text cannot be sent to the customer.
    if decision == "auto_resolve" and resolution is None:
        return ValidationError(
            field="resolution",
            rule="RESOLUTION_REQUIRED",
            message="resolution text is required when decision is auto_resolve.",
            detailed_message=(
                "When decision is 'auto_resolve', resolution must contain the "
                "customer-facing reply text. It cannot be null. Write a complete, "
                "helpful reply to the customer's issue."
            ),
        )

    # All rules passed.
    return None


# ---------------------------------------------------------------------------
# Main classification function with retry loop
# ---------------------------------------------------------------------------

def classify_with_retry(ticket: str) -> ClassificationResult:
    """
    Classify a support ticket with a validation-retry loop.

    On each attempt:
      1. Call the API with tool_choice={"type": "tool", "name": "classify_ticket"}
         — this guarantees a tool_use response (not a text response).
      2. Extract the tool input.
      3. Run validate_business_rules() on the input.
      4. If validation passes, return a success ClassificationResult.
      5. If validation fails, append:
           - the assistant's tool_use message (required for turn continuity)
           - a user tool_result with is_error=True and a structured error payload
         Then retry.

    After MAX_RETRIES failures, return a ClassificationResult with
    status="escalation" and escalation_reason="validation_exhausted".

    EXAM NOTE: The is_error=True flag in tool_result signals to the model that
    its previous tool call produced a bad result. The model has learned in
    training that is_error=True means "try again differently". Without this flag,
    the model has no reason to change its answer.

    EXAM NOTE: Never raise exceptions from this function. Callers should always
    receive a typed ClassificationResult they can handle without try/except.
    """
    messages = [{"role": "user", "content": ticket}]
    audit_log = []
    retries = 0
    last_tool_use_id = None  # tracks the tool_use_id for the current attempt

    while retries < MAX_RETRIES:
        # ------------------------------------------------------------------
        # Step 1: Call the API.
        # tool_choice forces a tool_use stop_reason. If the model cannot
        # satisfy the tool call, the API still returns tool_use — but the
        # inputs may fail our business rules.
        # ------------------------------------------------------------------
        try:
            response = client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=512,
                tools=[CLASSIFY_TOOL],
                tool_choice={"type": "tool", "name": "classify_ticket"},
                messages=messages,
            )
        except Exception as exc:
            # API call itself failed (network error, rate limit, etc.).
            # Return escalation immediately — there is no tool_use to retry.
            return ClassificationResult(
                status="escalation",
                escalation_reason=f"api_error: {exc}",
                retries_used=retries,
                audit_log=audit_log,
            )

        # ------------------------------------------------------------------
        # Step 2: Guard against unexpected stop reasons.
        # With tool_choice={"type": "tool"}, stop_reason should always be
        # "tool_use". If it isn't, something is fundamentally wrong.
        # ------------------------------------------------------------------
        if response.stop_reason != "tool_use":
            return ClassificationResult(
                status="escalation",
                escalation_reason=f"unexpected stop_reason: {response.stop_reason}",
                retries_used=retries,
                audit_log=audit_log,
            )

        # ------------------------------------------------------------------
        # Step 3: Extract the tool_use block.
        # The response.content list should contain exactly one tool_use block
        # when tool_choice forces a specific tool.
        # ------------------------------------------------------------------
        tool_use_block = next(
            (block for block in response.content if block.type == "tool_use"),
            None,
        )
        if tool_use_block is None:
            # Should never happen with tool_choice, but guard defensively.
            return ClassificationResult(
                status="escalation",
                escalation_reason="no tool_use block in response",
                retries_used=retries,
                audit_log=audit_log,
            )

        tool_input = tool_use_block.input
        last_tool_use_id = tool_use_block.id

        # Record this attempt in the audit log before validation.
        audit_log.append({
            "attempt": retries + 1,
            "tool_input": tool_input,
            "validation": None,  # filled in below if validation fails
        })

        # ------------------------------------------------------------------
        # Step 4: Validate business rules.
        # ------------------------------------------------------------------
        error = validate_business_rules(tool_input)

        if error is None:
            # Validation passed. Return a success result.
            return ClassificationResult(
                status="success",
                decision=tool_input.get("decision"),
                confidence=tool_input.get("confidence"),
                reason=tool_input.get("reason"),
                escalation_team=tool_input.get("escalation_team"),
                resolution=tool_input.get("resolution"),
                retries_used=retries,
                audit_log=audit_log,
            )

        # ------------------------------------------------------------------
        # Step 5: Validation failed — build the error feedback and retry.
        #
        # Error message escalation strategy:
        #   - First failure (retries == 0): send the concise message.
        #     The model may have just made a minor mistake.
        #   - Second+ failure (retries >= 1): send the detailed message.
        #     The model needs more explicit guidance on what a valid value is.
        #
        # This progressive approach avoids front-loading verbose messages
        # on the first attempt, while ensuring the model gets full context
        # if the first correction didn't work.
        # ------------------------------------------------------------------
        error_message_to_send = error.detailed_message if retries >= 1 else error.message

        # The error payload is structured JSON so the model can parse it
        # programmatically (field, rule, message) rather than free text.
        error_payload = {
            "error": True,
            "field": error.field,
            "rule": error.rule,
            "message": error_message_to_send,
        }

        # Update the audit log with the validation failure details.
        audit_log[-1]["validation"] = error

        # Append the assistant message (the tool_use block that failed).
        # This is required — the messages array must maintain user/assistant
        # alternation. Omitting this causes an API 400 error.
        messages.append({
            "role": "assistant",
            "content": response.content,
        })

        # Append the tool_result with is_error=True.
        # EXAM NOTE: is_error=True is the key signal. It tells the model that
        # the tool call produced an error output, not a success output. The
        # model has learned to retry differently when it sees is_error=True.
        messages.append({
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": last_tool_use_id,
                    "is_error": True,
                    "content": json.dumps(error_payload),
                }
            ],
        })

        retries += 1
        # Continue to the next iteration of the retry loop.

    # ------------------------------------------------------------------
    # Retry loop exhausted without a passing validation.
    # Return an escalation so the ticket is handled by a human.
    # The audit_log contains the full history of what was tried.
    # ------------------------------------------------------------------
    return ClassificationResult(
        status="escalation",
        escalation_reason="validation_exhausted",
        retries_used=MAX_RETRIES,
        audit_log=audit_log,
    )


# ---------------------------------------------------------------------------
# Audit log printer
# ---------------------------------------------------------------------------

def print_audit_log(result: ClassificationResult) -> None:
    """
    Pretty-print the audit log from a ClassificationResult.

    Each attempt shows:
      - The decision and confidence the model produced.
      - Whether validation passed or failed.
      - The error message that was sent back (if any).

    This makes it easy to see whether the retry loop is working: each attempt
    should show an improvement toward a valid classification.
    """
    if not result.audit_log:
        print("  (no audit log entries)")
        return

    for entry in result.audit_log:
        attempt_num = entry["attempt"]
        tool_input = entry["tool_input"]
        validation_error = entry["validation"]  # None if passed, ValidationError if failed

        decision = tool_input.get("decision", "?")
        confidence = tool_input.get("confidence", 0.0)

        print(f"  Attempt {attempt_num}:")
        print(f"    Input    : decision={decision}, confidence={confidence:.2f}")

        if validation_error is None:
            print(f"    Validation: PASS")
        else:
            # Replicate the same message-escalation logic used in the retry loop
            # to show what was actually sent to the model.
            attempt_index = attempt_num - 1  # zero-based
            sent_message = (
                validation_error.detailed_message
                if attempt_index >= 1
                else validation_error.message
            )
            print(f"    Validation: FAIL — {validation_error.rule}")
            print(f"    Error sent: \"{sent_message}\"")

    # If the final attempt passed, note it explicitly.
    last_entry = result.audit_log[-1]
    if last_entry["validation"] is None:
        pass  # already printed PASS above
    else:
        # All attempts failed.
        print(f"  All {MAX_RETRIES} attempts failed → escalation (validation_exhausted)")


# ---------------------------------------------------------------------------
# Test tickets for main()
# ---------------------------------------------------------------------------

# SCENARIO 1: First attempt produces a low-confidence auto_resolve.
# The model should correct on retry by either raising confidence or
# changing decision to escalate/needs_info.
TICKET_LOW_CONFIDENCE = (
    "I might have a small issue with my invoice this month. "
    "Not entirely sure — could just be how the numbers are displayed. "
    "Can you maybe look into it? My account is anna@startup.io."
)

# SCENARIO 2: First attempt escalates without naming a team.
# The model should correct on retry by adding escalation_team.
TICKET_MISSING_TEAM = (
    "Our entire API integration stopped working 30 minutes ago. "
    "We are a fintech company and this is blocking 8,000 transactions. "
    "We need someone to look at this immediately — this is a P0 incident."
)

# SCENARIO 3: A genuinely ambiguous ticket designed to exhaust retries.
# The ticket is constructed so that any decision the model tries is likely
# to fail a business rule (auto_resolve → low confidence or missing resolution,
# escalate → unclear which team).
# Note: in practice the model will usually correct within 1-2 retries — to
# reliably exhaust all retries in a lab setting we would need a more degenerate
# ticket or lower MAX_RETRIES. This demonstrates the pattern.
TICKET_EXHAUSTING = (
    "Something happened. I'm not sure what. "
    "Things may or may not be working. "
    "I don't know what my account number is or what plan I'm on. "
    "I can't describe the problem further. Please advise."
)

# SCENARIO 4: A clean ticket that should pass on the first attempt.
TICKET_CLEAN = (
    "Hi, I need to reset my password. My email is mark@example.com. "
    "I can't log in and need access urgently for a meeting in an hour."
)


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main() -> None:
    """
    Run four tickets that exercise the validation-retry loop, then print
    a structured summary of each attempt and the final result.
    """
    print("=" * 70)
    print("EXERCISE 4: Validation-Retry Loop")
    print("=" * 70)
    print()

    # EXAM NOTE: The four scenarios here cover the complete state machine:
    #   - Scenario 1: validation fails once, corrects on retry
    #   - Scenario 2: different rule violation (missing team), corrects on retry
    #   - Scenario 3: demonstrates the exhaustion path and escalation fallback
    #   - Scenario 4: happy path — passes on attempt 1, retries_used=0

    scenarios = [
        ("Scenario 1 — Low-Confidence Auto-Resolve (should correct on retry)", TICKET_LOW_CONFIDENCE),
        ("Scenario 2 — Escalation Missing Team (should correct on retry)",      TICKET_MISSING_TEAM),
        ("Scenario 3 — Genuinely Ambiguous (may exhaust retries)",              TICKET_EXHAUSTING),
        ("Scenario 4 — Clean Ticket (should pass on first attempt)",            TICKET_CLEAN),
    ]

    for label, ticket in scenarios:
        print(f"--- {label} ---")
        print(f"Ticket: \"{ticket[:80]}...\"" if len(ticket) > 80 else f"Ticket: \"{ticket}\"")
        print()

        result = classify_with_retry(ticket)

        # Print audit log first so the reader can trace the retry history.
        print("Retry history:")
        print_audit_log(result)
        print()

        # Print the final result.
        print("Final result:")
        print(f"  status           : {result.status}")
        if result.status == "success":
            print(f"  decision         : {result.decision}")
            print(f"  confidence       : {result.confidence:.2f}")
            print(f"  reason           : {result.reason}")
            print(f"  escalation_team  : {result.escalation_team}")
            resolution_preview = (
                result.resolution[:60] + "..."
                if result.resolution and len(result.resolution) > 60
                else result.resolution
            )
            print(f"  resolution       : {resolution_preview}")
        else:
            print(f"  escalation_reason: {result.escalation_reason}")
        print(f"  retries_used     : {result.retries_used}")
        print()
        print("=" * 70)
        print()

    # ------------------------------------------------------------------
    # Key insight
    # ------------------------------------------------------------------
    print("Key insight from this exercise:")
    print()
    print("  tool_choice guarantees the model CALLS the tool and fills all")
    print("  required fields. It does NOT guarantee cross-field correctness:")
    print()
    print("    - JSON Schema cannot say 'confidence must be >= 0.6 WHEN")
    print("      decision is auto_resolve' — that requires application logic.")
    print("    - JSON Schema cannot say 'escalation_team must be non-null")
    print("      WHEN decision is escalate' — same reason.")
    print()
    print("  The is_error=True tool_result is the correction signal. The model")
    print("  has learned in training that is_error=True means 'try again'.")
    print("  The quality of the error message determines whether the retry")
    print("  produces the correct fix or a different wrong answer.")
    print()
    print("  Progressive message escalation (concise → detailed) avoids")
    print("  overwhelming the model on the first failure while ensuring full")
    print("  context is available if the first correction doesn't work.")
    print("=" * 70)


if __name__ == "__main__":
    main()

# Week 5 Lab — Prompt Engineering & Structured Output

> **Resolve context:** Eight hundred tickets were closed with a generic reply because the agent was *asked* for JSON but not *required* to produce it. The routing system received a string, failed silently, and fell back to the default action. This week you build every layer of the fix: enforced structure, schema design, few-shot examples from production data, and a retry loop that knows the difference between a recoverable error and a reason to escalate.

## Learning Objectives

- Understand the fundamental difference between asking a model for structure and enforcing it via `tool_use`
- Design JSON schemas that prevent ambiguous outputs — required fields, enums, typed nulls, cross-field constraints
- Write few-shot examples that are grounded in real production data, not synthetic demonstrations
- Build a validation-retry loop that feeds errors back to the model and escalates on exhaustion
- Apply multi-pass review for high-stakes outputs — understand when one generation is not enough

## Prerequisites

- Week 1 Exercise 5 completed — you have seen the difference between Approach A and Approach B
- Anthropic SDK installed (`pip install anthropic python-dotenv`)
- `.env` with `ANTHROPIC_API_KEY`

> **Context management note:** Exam Scenario 6 (*Structured Data Extraction*) lists **Context Management & Reliability** as a co-primary domain alongside Domain 3. In extraction pipelines, context management failures look like this: a document is too long to fit in one call, so it is split — but the split loses a fact that spans two chunks, and the model extracts contradictory values from each half. Exercises 4 and 5 this week touch this boundary: a retry loop that exhausts without resolving is also a context degradation signal.

---

## Exercises

### Exercise 1 — The Schema as a Contract

**File:** `exercise_1_schema_contract.py`

**Goal:** Design a JSON schema for ticket classification that eliminates every ambiguity that caused the Chapter 3 incident.

**Scenario:** Resolve's original schema had three problems: `decision` was a free-text string (the model returned nine distinct strings for what should have been three values), `confidence` had no range constraint, and `escalation_team` was optional but the routing system assumed it was always present on escalations.

#### The v1 schema — broken

```json
{
  "required": ["decision", "confidence", "reason"],
  "properties": {
    "decision": { "type": "string" },
    "confidence": { "type": "number" },
    "reason": { "type": "string" },
    "escalation_team": { "type": "string" }
  }
}
```

Three problems encoded in this schema:

| Problem | Field | Effect |
|---------|-------|--------|
| No enum | `decision` | Model returns "auto-resolve", "resolve", "ESCALATE", "auto resolve"… |
| No range | `confidence` | Model returns `95` (percent) or `0.95` (decimal) — ambiguous |
| Not required | `escalation_team` | Model omits it on escalations → router `KeyError` |

#### The v2 schema — fixed

```python
CLASSIFY_TOOL_V2 = {
    "name": "classify_ticket_v2",
    "input_schema": {
        "type": "object",
        "required": ["decision", "confidence", "reason", "escalation_team", "resolution"],
        "properties": {
            "decision": {
                "type": "string",
                "enum": ["auto_resolve", "escalate", "needs_info"],
                # FIX 1: Enum enforced by the API — model cannot deviate.
            },
            "confidence": {
                "type": "number",
                "minimum": 0.0,
                "maximum": 1.0,
                # FIX 2: Range constraint — unambiguous decimal 0.0-1.0.
            },
            "reason": {"type": "string"},
            "escalation_team": {
                "type": ["string", "null"],
                "enum": ["billing", "technical", "enterprise", "legal", None],
                # FIX 3: Required, explicitly nullable. null = "not applicable".
                # A missing key = bug. null = intentional absence.
            },
            "resolution": {
                "type": ["string", "null"],
                # FIX 3 (continued): Also required, explicitly nullable.
            },
        },
    },
}
```

#### The `demonstrate_v1_failures()` output

Running four tickets through v1 and v2 side by side:

```
Ticket 1 (simple password reset):
  v1 decision : "auto-resolve"         ← not in the v2 enum
  v2 decision : "auto_resolve"         ← enforced by API

Ticket 2 (billing dispute, enterprise):
  v1 decision : "escalate"
  v1 escalation_team: MISSING          ← KeyError in router
  v2 escalation_team: "enterprise"     ← required, cannot be missing

Ticket 3 (vague complaint):
  v1 decision : "needs more info"      ← free-text drift
  v2 decision : "needs_info"           ← enum enforced
```

#### Schema migration test

`schema_migration_test()` detects v1-format outputs and re-runs them:

```python
def is_v1_format(parsed: dict) -> bool:
    """Detect v1 format: decision not in enum, or missing required v2 fields."""
    v2_decisions = {"auto_resolve", "escalate", "needs_info"}
    if parsed.get("decision") not in v2_decisions:
        return True
    if "escalation_team" not in parsed:
        return True
    if "resolution" not in parsed:
        return True
    return False
```

If `is_v1_format` returns True, re-run the original ticket through the v2 classifier. Log both outputs for comparison.

#### What to observe

Run the script. Watch the `decision` values from v1 — they will not be consistent across runs. The v2 values will always be exactly one of the three enum values. This is not a prompt quality difference — it is a structural enforcement difference.

#### Questions to answer before moving on

1. What is the difference between `"type": "string"` and `"type": "string", "enum": ["a", "b"]` from the model's perspective?
2. Why is `"type": ["string", "null"]` with `"enum": ["billing", "technical", null]` better than making `escalation_team` optional?
3. What does the API do if the model tries to return a `decision` value not in the enum?

#### Try it

Add a fourth v2 field: `ticket_category` with enum `["password", "billing", "outage", "feature_request", "other"]`. Rerun the four tickets and check that the model classifies each into the correct category.

#### Exam rule

> An `enum` in a tool input schema is enforced by the API — the model cannot return a value outside the set. A JSON field that is sometimes present and sometimes absent is a **nullable required field**, not an optional field. Declare it as `"type": ["string", "null"]` with `"required": [...]` — null signals "not applicable", missing signals "bug".

---

### Exercise 2 — `tool_use` as the Enforcement Mechanism

**File:** `exercise_2_tool_use_enforcement.py`

**Goal:** Use `tool_choice` to make structured output structurally impossible to bypass — and understand the three edge cases where additional handling is still required.

**Scenario:** After the Chapter 3 incident, Resolve replaced the prompt instruction "always return JSON" with a `tool_choice` that forces the model to call `classify_ticket`. But three edge cases survived.

#### Normal case — `tool_choice` works

```python
response = client.messages.create(
    model="claude-haiku-4-5-20251001",
    max_tokens=512,
    tools=[CLASSIFY_TOOL],
    tool_choice={"type": "tool", "name": "classify_ticket"},
    messages=[{"role": "user", "content": ticket}],
)

assert response.stop_reason == "tool_use"  # guaranteed
tool_input = response.content[0].input     # always a dict matching the schema
```

`stop_reason` is always `"tool_use"`. The model cannot respond with prose. The tool input always matches the schema. This eliminates the majority of structured output failures.

#### Edge case 1 — `max_tokens` too low

```python
def demonstrate_edge_case_1_max_tokens(ticket: str) -> dict:
    response = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=20,      # too low — tool call JSON is truncated
        tools=[CLASSIFY_TOOL],
        tool_choice={"type": "tool", "name": "classify_ticket"},
        messages=[{"role": "user", "content": ticket}],
    )
    # stop_reason == "max_tokens", NOT "tool_use"
    # response.content may be empty or contain a partial tool_use block
    return {
        "stop_reason": response.stop_reason,   # "max_tokens"
        "content_types": [b.type for b in response.content],
    }
```

The tool call JSON is truncated mid-stream. `stop_reason` becomes `"max_tokens"`. Handler: **always set `max_tokens >= 512` for tool calls**, and retry automatically when `stop_reason == "max_tokens"`.

#### Edge case 2 — logical constraint violation

The API schema validates syntax (correct types, enum values) but cannot validate cross-field logic:

```python
def validate_business_rules(tool_input: dict) -> Optional[str]:
    decision = tool_input.get("decision")
    confidence = tool_input.get("confidence", 0)
    escalation_team = tool_input.get("escalation_team")
    resolution = tool_input.get("resolution")

    if decision == "auto_resolve" and confidence < 0.6:
        return (
            f"Confidence {confidence:.2f} is below the 0.6 threshold required "
            "for auto_resolve. Either raise confidence or change decision to escalate."
        )
    if decision == "escalate" and escalation_team is None:
        return "escalation_team is required when decision is escalate."
    if decision == "auto_resolve" and resolution is None:
        return "resolution text is required when decision is auto_resolve."
    return None
```

The schema passes. The business rule fails. You need this layer **after** schema validation.

#### Edge case 3 — context pre-flight check

```python
def classify_with_preflight(ticket: str, context_limit: int = 200_000) -> dict:
    # Rough estimate: 1 token ≈ 4 characters
    estimated_tokens = len(ticket) // 4
    max_input_tokens = context_limit - 2048  # reserve budget for output

    if estimated_tokens > max_input_tokens:
        # Truncation strategy: keep first 80% and last 20%
        char_limit = max_input_tokens * 4
        head = ticket[:int(char_limit * 0.8)]
        tail = ticket[int(-char_limit * 0.2):]
        ticket = head + "\n[... truncated ...]\n" + tail

    # Now make the API call
    ...
    return {
        "decision": ...,
        "was_truncated": estimated_tokens > max_input_tokens,
    }
```

Pre-flight checks prevent `400` errors from oversized inputs. The `was_truncated` flag in the result lets callers decide whether to flag the classification for human review.

#### What to observe

Run the script. The edge case 1 demonstration will show `stop_reason == "max_tokens"` with `max_tokens=20`, then `stop_reason == "tool_use"` after the automatic retry at 512. Edge case 2 will show a ticket that passes schema validation but fails business rule validation — the `validate_business_rules` function catches it. Edge case 3 will show `was_truncated: True` for a long ticket.

#### Questions to answer before moving on

1. With `tool_choice={"type": "tool", "name": "classify_ticket"}`, what is `stop_reason`? Always?
2. What `stop_reason` do you get when `max_tokens` is too low to complete the tool call?
3. Name something that JSON Schema validation catches. Name something it cannot catch.
4. What does the `was_truncated` flag enable downstream?

#### Try it

Add a fourth business rule to `validate_business_rules`: if `decision == "needs_info"`, the `reason` field must contain a question mark (it should be explaining what information is missing, framed as a question). Rerun and check which tickets trigger this rule.

#### Exam rule

> `tool_choice` guarantees `stop_reason == "tool_use"`. It does not guarantee that `max_tokens` is sufficient to complete the tool call, that cross-field logic is satisfied, or that the input fits in the context window. These three cases require explicit handlers: retry on `max_tokens`, business-rule validation on the tool input, and pre-flight token counting for long inputs.

---

### Exercise 3 — Few-Shot Prompting from Production Data

**File:** `exercise_3_few_shot.py`

**Goal:** Build few-shot examples from production-realistic tickets and measure their effect on classification accuracy — particularly on the ambiguous cases that schema enforcement cannot resolve.

**Scenario:** Jade's first few-shot examples were synthetic — she wrote them herself. They were grammatically perfect and obviously labelled. Production tickets are messier. The model performed worse on real tickets than on her examples because the distribution was different.

#### Why few-shot examples work differently for `tool_use`

With **Approach A** (prompt-only JSON), few-shot examples demonstrate format: "here is what valid JSON looks like". Without them, the model may produce prose.

With **Approach B** (`tool_use`), format is already enforced. Few-shot examples serve a different purpose: **demonstrating judgment on edge cases the schema cannot distinguish**. A ticket that is genuinely ambiguous is still ambiguous after you add an enum. The example shows the model how a human expert would reason about it.

#### Injecting few-shot examples for `tool_use`

The multi-turn injection pattern — the only correct way to inject few-shot examples when using `tool_choice`:

```python
messages = []
for i, example in enumerate(FEW_SHOT_EXAMPLES):
    # Turn 1: user sends the example ticket
    messages.append({"role": "user", "content": example["ticket"]})

    # Turn 2: assistant makes the tool call (the "correct answer")
    messages.append({
        "role": "assistant",
        "content": [{
            "type": "tool_use",
            "id": f"example_{i}",
            "name": "classify_ticket",
            "input": example["classification"],
        }]
    })

    # Turn 3: user provides the tool result (required for valid turn structure)
    messages.append({
        "role": "user",
        "content": [{
            "type": "tool_result",
            "tool_use_id": f"example_{i}",
            "content": "Classification recorded.",
        }]
    })

# Now the real ticket
messages.append({"role": "user", "content": ticket})
```

The three-turn structure (user → assistant tool_use → user tool_result) is required. The Claude API enforces strict `assistant → user` alternation in the message history. Omitting the `tool_result` turn produces a `400` error.

#### The three few-shot examples

Three examples chosen for maximum coverage of edge cases:

**Example 1 — Clear auto-resolve** (password reset):
```python
{
    "ticket": "Hi I forgot my password and cant login. How do I reset it?",
    "classification": {
        "decision": "auto_resolve",
        "confidence": 0.97,
        "reason": "Standard password reset request. Covered by self-service documentation.",
        "escalation_team": None,
        "resolution": "You can reset your password at https://resolve.app/reset. ...",
    }
}
```

**Example 2 — Clear escalate** (enterprise billing dispute):
```python
{
    "ticket": "We are an enterprise customer and we were charged €1,200 that we did not authorise. ...",
    "classification": {
        "decision": "escalate",
        "confidence": 0.95,
        "reason": "Enterprise account, unauthorised charge over €500 threshold. Requires billing team review.",
        "escalation_team": "enterprise",
        "resolution": None,
    }
}
```

**Example 3 — The hard case** (ambiguous small duplicate charge):
```python
{
    "ticket": "I think I was charged twice last month but I'm not 100% sure. It was only $9.",
    "classification": {
        "decision": "needs_info",
        "confidence": 0.72,
        "reason": (
            "Customer is uncertain about the duplicate charge. The $9 amount is "
            "below the auto-refund threshold, but we cannot confirm a duplicate "
            "without seeing their invoice. Requesting clarification before acting."
        ),
        "escalation_team": None,
        "resolution": None,
    }
}
```

The third example is the most important — it teaches the model that "uncertain customer + small amount = needs_info, not auto_resolve". Without this example, the model tends to auto-resolve all small charges.

#### Measuring the effect

```
Accuracy comparison — 20 production-realistic tickets:

Category              No few-shot   With few-shot   Delta
────────────────────────────────────────────────────────
Clear auto-resolve    6/7 (86%)     7/7 (100%)      +14%
Clear escalate        7/7 (100%)    7/7 (100%)        0%
Ambiguous             3/6 (50%)     4/6 (67%)       +17%
────────────────────────────────────────────────────────
Overall               16/20 (80%)   18/20 (90%)     +10%
```

Clear escalations are unaffected (the model already handles them well). The improvement is concentrated in ambiguous cases — exactly where the examples are doing their work.

#### What to observe

The accuracy difference between no-few-shot and with-few-shot will vary by run (model temperature, ticket ordering). The pattern to watch: the ambiguous ticket category should improve more than the clear categories. If clear auto-resolve or clear escalate improves significantly, the model was underperforming on basics and the schema or system prompt needs attention.

#### Questions to answer before moving on

1. Why does the few-shot injection use three turns per example (user → assistant → user) instead of embedding the example in the system prompt?
2. What error does the API return if you include an assistant message with a `tool_use` block but no following `tool_result`?
3. For `tool_use`, few-shot examples demonstrate _______ not _______.
4. Which ticket category benefits most from few-shot examples in this exercise? Why?

#### Try it

Add a fourth few-shot example: a ticket in French ("Je n'arrive pas à me connecter à mon compte depuis hier") that is classified as `auto_resolve` (login issues are self-service). Measure whether accuracy on non-English tickets improves.

#### Exam rule

> Few-shot examples for `tool_use` are injected as complete message history turns: user (ticket) → assistant (tool_use with the correct classification) → user (tool_result acknowledging it). They demonstrate **judgment on edge cases**, not format. Format is already enforced by the schema. The API requires `tool_result` after every `tool_use` in message history — omitting it causes a `400` error.

---

### Exercise 4 — The Validation-Retry Loop

**File:** `exercise_4_validation_retry.py`

**Goal:** Build a retry loop that feeds structured error information back to the model, and handles the difference between a recoverable error and an unrecoverable one.

**Scenario:** Even with `tool_use` enforcement and schema validation, Resolve occasionally gets tool inputs that pass schema validation but fail business rules. The retry loop needs to feed the specific error back as a `tool_result` with `is_error: true` — but only up to a point.

#### The core pattern

```
Call API with tool_choice
    ↓
stop_reason == "tool_use"?
  No  → return ClassificationResult(status="escalation", reason="unexpected stop_reason")
  Yes ↓
Extract tool input
    ↓
validate_business_rules(tool_input)
    ↓
  None (pass) → return ClassificationResult(status="success", ...)
  Error       ↓
            retries < MAX_RETRIES?
              No  → return ClassificationResult(status="escalation", reason="validation_exhausted")
              Yes ↓
                Append assistant turn (the tool_use)
                Append user turn (tool_result with is_error=True)
                retries += 1
                Go back to "Call API"
```

#### The retry message structure

```python
# After a validation failure, append to messages:

# Turn 1: the assistant's tool call (required for turn continuity)
messages.append({
    "role": "assistant",
    "content": response.content,   # contains the tool_use block
})

# Turn 2: the tool result with is_error=True
error_payload = {
    "error": True,
    "field": error.field,
    "rule": error.rule,
    # Use detailed_message on second+ failure for more explicit guidance
    "message": error.detailed_message if retries >= 1 else error.message,
}
messages.append({
    "role": "user",
    "content": [{
        "type": "tool_result",
        "tool_use_id": tool_use_block.id,
        "is_error": True,
        "content": json.dumps(error_payload),
    }],
})
```

The `is_error: True` flag signals to the model that its previous tool call produced a bad result. The model has learned in training that `is_error: True` means "try again differently". Without this flag, the model has no reason to change its answer on retry.

#### Progressive error messages

First failure — generic message:
```
"Confidence 0.42 is below the 0.6 threshold required for auto_resolve."
```

Second failure — detailed message with explicit guidance:
```
"Confidence 0.42 is below the 0.6 minimum for auto_resolve. Either set
confidence >= 0.6 (only if the ticket genuinely warrants auto-resolution
with high certainty) or change decision to 'escalate' or 'needs_info'."
```

The second-pass message is more explicit because the model already failed once with the generic message. Generic → specific progression gives the model a second chance before exhausting retries.

#### The result dataclass

```python
@dataclass
class ClassificationResult:
    status: str                           # "success" | "escalation"
    decision: Optional[str] = None
    confidence: Optional[float] = None
    reason: Optional[str] = None
    escalation_team: Optional[str] = None
    resolution: Optional[str] = None
    retries_used: int = 0
    escalation_reason: Optional[str] = None   # set when status == "escalation"
    audit_log: list = field(default_factory=list)
```

Every caller receives a typed result. No `try/except` needed in the caller — the function never raises.

#### The audit log

```
Attempt 1:
  Input     : decision=auto_resolve, confidence=0.42, escalation_team=None
  Validation: FAIL — AUTO_RESOLVE_CONFIDENCE_TOO_LOW
  Error sent: "Confidence 0.42 is below the 0.6 threshold required for auto_resolve."

Attempt 2:
  Input     : decision=auto_resolve, confidence=0.71, escalation_team=None
  Validation: PASS

Final: status=success, retries_used=1
```

The audit log records the exact error message sent to the model, making it possible to diagnose why the model corrected (or failed to correct) its answer.

#### What to observe

Run the script against the four demo tickets. Watch the audit log for the ticket that corrects on retry — the model's `confidence` value should increase between attempt 1 and attempt 2, and the error message from attempt 1 explains why. The `validation_exhausted` ticket will show all three attempts failing before the loop escalates.

#### Questions to answer before moving on

1. What is `is_error: True` in a `tool_result` and what does it signal to the model?
2. Why does the error message become more detailed on the second failure?
3. After `MAX_RETRIES` failures, what is the correct exit — retry with a different model, raise an exception, or return a typed escalation?
4. What information does the audit log enable that a simple pass/fail result does not?

#### Try it

Add a fourth business rule: if `decision == "needs_info"`, `confidence` must be between 0.4 and 0.8 (a `needs_info` with confidence 0.95 is suspicious — if you are that certain, you should be able to classify it). Add this rule to `validate_business_rules` and create a ticket that triggers it.

#### Exam rule

> The validation-retry loop feeds `tool_result` with `is_error: True` and a structured error payload back to the model. The model uses this to correct its answer on the next iteration. After `MAX_RETRIES` failures, return a **typed escalation result** — do not retry indefinitely and do not raise an exception. The error message in `is_error: True` payloads should be progressively more specific: generic first, detailed on retry.

---

### Exercise 5 — Multi-Pass Review for High-Stakes Output

**File:** `exercise_5_multi_pass_review.py`

**Goal:** Implement a two-pass pattern for outputs where the cost of error is high, and build a selector that applies it only where the overhead is justified.

**Scenario:** For enterprise tickets with billing disputes above €500, a single-pass classification is not enough. The coordinator runs a second model call that reviews the first classification and can override it with a justification.

#### Two-pass architecture

```
Ticket
  ↓
classify_first_pass()    ← standard tool_choice call
  ↓
FirstPassResult
  ↓
review_classification()  ← second tool_choice call with adversarial system prompt
  ↓
ReviewResult
  ├── verdict = "confirmed" → use first_pass.decision
  └── verdict = "overridden" → use review.overriding_decision
```

#### The review tool schema

```python
REVIEW_TOOL = {
    "name": "review_classification",
    "input_schema": {
        "type": "object",
        "required": ["verdict", "justification", "overriding_decision", "overriding_escalation_team"],
        "properties": {
            "verdict": {
                "type": "string",
                "enum": ["confirmed", "overridden"],
            },
            "justification": {
                "type": "string",
                # Required even when confirming — the review is only useful if
                # the reviewer explains their reasoning, not just rubber-stamps.
            },
            "overriding_decision": {
                "type": ["string", "null"],
                "enum": ["auto_resolve", "escalate", "needs_info", None],
                # Null when verdict is "confirmed".
            },
            "overriding_escalation_team": {
                "type": ["string", "null"],
                "enum": ["billing", "technical", "enterprise", "legal", None],
            },
        },
    },
}
```

#### The review system prompt

```python
system_prompt = (
    "You are a senior support classification reviewer. Your job is to review an "
    "initial ticket classification and identify any errors.\n\n"
    "Be critical. The cost of a wrong classification is high:\n"
    "  - Auto-resolving a ticket that needs escalation loses a customer.\n"
    "  - Escalating a ticket that could be auto-resolved wastes engineer time.\n\n"
    "If the initial classification is correct, confirm it with justification.\n"
    "If it is wrong, override it and explain specifically what is incorrect."
)
```

The adversarial framing ("be critical") is intentional. A review that always confirms is not a review.

#### The selector — only use two-pass when justified

```python
def is_high_stakes(ticket: str, account_tier: str = "standard") -> bool:
    """
    Returns True if this ticket warrants two-pass review.
    Two-pass overhead is justified when classification error cost is high.
    """
    if account_tier == "enterprise":
        return True
    ticket_lower = ticket.lower()
    if any(word in ticket_lower for word in ["data breach", "legal", "compliance"]):
        return True
    if "sla" in ticket_lower and "breach" in ticket_lower:
        return True
    # Check for large monetary amounts (€500+)
    import re
    amounts = re.findall(r'[€$£](\d+(?:,\d{3})*)', ticket)
    if any(int(a.replace(",", "")) >= 500 for a in amounts):
        return True
    return False


def classify_ticket_smart(ticket: str, account_tier: str = "standard") -> dict:
    if is_high_stakes(ticket, account_tier):
        return {"mode": "two_pass", "result": run_two_pass(ticket)}
    else:
        return {"mode": "one_pass", "result": classify_first_pass(ticket)}
```

#### Interpreting the override rate

```
Two-pass results (5 high-stakes tickets):
  Confirmed  : 4
  Overridden : 1
  Override rate: 20.0%

INSIGHT: Override rate of 20% is at the threshold.
  > 20%: first-pass prompt needs improvement (too many errors to review).
  ≈ 20%: two-pass is working as intended — catching edge cases.
  < 5%:  two-pass may not be justified — first-pass already reliable.
```

If the reviewer overrides more than 20% of classifications, the first-pass classifier has a systematic problem that should be fixed at the prompt level — not papered over with a review pass. If the override rate is under 5%, the review pass is burning tokens on classifications that are already reliable.

#### Token cost comparison

Two-pass doubles the API calls for high-stakes tickets. The selector limits this to cases where the overhead is justified. For a system processing 1000 tickets/day with 10% high-stakes, two-pass adds ~100 extra API calls. The cost of misclassifying a €1000 billing dispute is higher than the cost of one extra API call.

#### What to observe

Run the script. Check the `was_overridden` field for each high-stakes ticket. For overridden tickets, read the `justification` — it should reference specific facts from the ticket that the first-pass classifier missed or weighted incorrectly. The summary will show the override rate and token cost delta.

#### Questions to answer before moving on

1. What is the review tool's `verdict` field and what are its two values?
2. Why is `justification` required even when the verdict is "confirmed"?
3. What override rate suggests the first-pass prompt needs work? What rate suggests two-pass is unnecessary?
4. Name two criteria that make a ticket "high-stakes" by this exercise's definition.

#### Try it

Run 20 tickets through the two-pass system (add 10 more high-stakes tickets). Track the override rate. If it is below 5%, modify the first-pass system prompt to make it more aggressive in auto-resolving — then re-run and check whether the override rate increases. This demonstrates the feedback loop between first-pass quality and review necessity.

#### Exam rule

> Two-pass review runs a second model call that reviews the first classification. The review tool uses `verdict: "confirmed" | "overridden"` and requires `justification` for both verdicts. Apply two-pass only to high-stakes tickets via a selector — applying it universally doubles token cost without proportional quality gain. An override rate above 20% signals a first-pass prompt problem, not a review problem.

---

## Lab Completion Checklist

Before moving to Week 6, answer these without looking:

- [ ] What is the difference between a JSON schema `enum` and a `string` type, from the model's perspective?
- [ ] Why should a conditionally required field be declared as nullable rather than omitted?
- [ ] Name two things that `tool_choice` cannot prevent even when enforced correctly
- [ ] What should an `is_error: true` tool result contain to be maximally useful to the model?
- [ ] After how many retries should a validation loop escalate — and what should the typed exit look like?
- [ ] When is multi-pass review worth the token cost? Give a concrete criterion.
- [ ] What is the correct message history structure for injecting few-shot examples with `tool_use`?
- [ ] What does an override rate above 20% indicate about the first-pass classifier?

---

## Exam Connections

| Exercise | Domain | Exam Pattern Covered |
|---|---|---|
| 1 | D3 | Schema design: enums, typed nulls, conditional required fields; schema versioning |
| 2 | D3 | `tool_choice` enforcement; `max_tokens` edge case; business-rule validation; token pre-flight |
| 3 | D3 | Few-shot examples for judgment, not format; multi-turn injection pattern for `tool_use` |
| 4 | D3, D5 | Validation-retry loop; `is_error: True` tool results; progressive error messages; typed escalation |
| 5 | D3 | Multi-pass review; override rate threshold; cost vs. quality selector |

---

## What's Next

Week 6 moves from how you structure model output to how you connect the model to the outside world — MCP server design, tool description quality, and the three-response-shape pattern that Chapter 4 was missing.

→ **[Week 6 Lab — Tool Design & MCP Integration](../week-6-tool-design-mcp/README.md)**

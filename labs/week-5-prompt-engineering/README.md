# Week 5 Lab — Prompt Engineering & Structured Output

> **Resolve context:** Eight hundred tickets were closed with a generic reply because the agent was *asked* for JSON but not *required* to produce it. The routing system received a string, failed silently, and fell back to the default action. This week you build every layer of the fix: enforced structure, schema design, few-shot examples, a retry loop, and a two-pass review.

## Learning Objectives

- Understand the difference between asking a model for structure and enforcing it with `tool_use`
- Design JSON schemas that prevent ambiguous outputs — enums, typed nulls, required fields
- Write few-shot examples that demonstrate judgment on hard cases
- Build a validation-retry loop that feeds errors back to the model
- Apply multi-pass review for high-stakes tickets

## Prerequisites

- Anthropic SDK installed (`pip install anthropic python-dotenv`)
- `.env` with `ANTHROPIC_API_KEY`

---

## Exercises

### Exercise 1 — The Schema as a Contract

**File:** `exercise_1_schema_contract.py`

**Goal:** See the difference between asking the model for JSON (Approach A) and forcing it with `tool_use` (Approach B).

#### Approach A — broken

The system prompt asks for JSON. The model *usually* complies — but "usually" is not enough for routing:

```python
def classify_approach_a(ticket: str) -> dict:
    response = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=256,
        system="Classify the support ticket. Return a JSON object with: "
               "decision: auto_resolve, escalate, or needs_info ...",
        messages=[{"role": "user", "content": ticket}],
    )
    raw = response.content[0].text.strip()
    parsed = json.loads(raw)   # may fail — model can return prose
    return {"approach": "A", "parsed": parsed}
```

Problems:
- Model can return `"auto-resolve"` instead of `"auto_resolve"` — router breaks
- Model can omit `escalation_team` on escalations — `KeyError` in routing
- Model can return `95` instead of `0.95` for confidence — downstream logic breaks

#### Approach B — fixed

`tool_choice` forces the model to call the tool. The API validates the input against the schema before returning:

```python
CLASSIFY_TOOL = {
    "name": "classify_ticket",
    "input_schema": {
        "type": "object",
        "required": ["decision", "confidence", "reason", "escalation_team", "resolution"],
        "properties": {
            "decision": {
                "type": "string",
                "enum": ["auto_resolve", "escalate", "needs_info"],  # enforced by API
            },
            "confidence": {
                "type": "number",
                "minimum": 0.0,
                "maximum": 1.0,   # model cannot return 95 instead of 0.95
            },
            "escalation_team": {
                "type": ["string", "null"],
                "enum": ["billing", "technical", "enterprise", "legal", None],
                # required + nullable = always present, null when not applicable
            },
            "resolution": {"type": ["string", "null"]},
        },
    },
}

def classify_approach_b(ticket: str) -> dict:
    response = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=512,
        tools=[CLASSIFY_TOOL],
        tool_choice={"type": "tool", "name": "classify_ticket"},  # forces the call
        messages=[{"role": "user", "content": ticket}],
    )
    return response.content[0].input  # always valid — API already checked it
```

#### What to observe

Run the script. Approach A `decision` values may vary across tickets. Approach B values are always exactly one of the three enum values — this is not prompt quality, it is structural enforcement.

#### Questions to answer before moving on

1. What is the difference between `"type": "string"` and `"type": "string", "enum": ["a", "b"]` from the API's perspective?
2. Why is `"type": ["string", "null"]` with `"required": [...]` better than making `escalation_team` optional?
3. What does the API do if the model tries to return a `decision` value not in the enum?

#### Try it

Add a field `ticket_category` with `"enum": ["password", "billing", "outage", "other"]`. Rerun the three tickets and check the model always picks one of those four values.

#### Exam rule

> An `enum` in a tool input schema is enforced by the API — the model cannot return a value outside the set. A field that is sometimes present and sometimes absent is a **nullable required field**, not an optional field. Declare it as `"type": ["string", "null"]` with `"required": [...]` — `null` signals "not applicable", missing signals "bug".

---

### Exercise 2 — What `tool_choice` Guarantees (and What It Doesn't)

**File:** `exercise_2_tool_use_enforcement.py`

**Goal:** Understand three edge cases that survive `tool_choice` enforcement.

`tool_choice={"type": "tool", "name": "classify_ticket"}` guarantees:
- `stop_reason == "tool_use"` (model cannot reply with prose)
- Tool input matches the schema

It does **not** guarantee:
1. `max_tokens` is sufficient to complete the tool call
2. Cross-field business rules are satisfied
3. The ticket fits in the context window

#### Edge case 1 — `max_tokens` too low

```python
response = client.messages.create(
    model="claude-haiku-4-5-20251001",
    max_tokens=20,   # too low — JSON gets cut off mid-stream
    ...
)
print(response.stop_reason)   # "max_tokens", NOT "tool_use"
```

Fix: always use `max_tokens >= 512` for tool calls. Retry when `stop_reason == "max_tokens"`.

#### Edge case 2 — business rule violation

The schema validates each field independently. It cannot enforce cross-field logic:

```python
def validate_business_rules(result: dict) -> Optional[str]:
    decision = result.get("decision")
    confidence = result.get("confidence", 1.0)
    escalation_team = result.get("escalation_team")

    if decision == "auto_resolve" and confidence < 0.6:
        return f"confidence is {confidence:.2f} but auto_resolve requires >= 0.6."

    if decision == "escalate" and escalation_team is None:
        return "escalation_team is null but decision is 'escalate'."

    return None  # all rules pass
```

This layer runs **after** the API call, before routing.

#### Edge case 3 — input too long

```python
def classify_with_preflight(ticket: str) -> dict:
    estimated = len(ticket) // 4   # ~4 chars per token
    max_input = 200_000 - 512      # context window minus output budget

    if estimated > max_input:
        char_limit = max_input * 4
        head = ticket[:int(char_limit * 0.8)]
        tail = ticket[int(-char_limit * 0.2):]
        ticket = head + "\n[... truncated ...]\n" + tail

    result = classify(ticket)
    result["was_truncated"] = estimated > max_input
    return result
```

#### What to observe

Run the script. Edge case 1 shows `stop_reason="max_tokens"` then a successful retry. Edge case 2 shows a ticket that passes schema validation but fails business rules. Edge case 3 shows `was_truncated: True` on a very long ticket.

#### Questions to answer before moving on

1. With `tool_choice` forced, what is `stop_reason`? Always?
2. What `stop_reason` do you get when `max_tokens` is too low?
3. Name something JSON Schema catches. Name something it cannot catch.
4. What does `was_truncated` enable downstream?

#### Try it

Add a third business rule: if `decision == "needs_info"`, `reason` must contain a question mark (the reason should state what information is needed, framed as a question).

#### Exam rule

> `tool_choice` guarantees `stop_reason == "tool_use"`. It does not guarantee that `max_tokens` is sufficient, that cross-field logic is satisfied, or that the input fits in the context window. These three cases require explicit handlers: retry on `max_tokens`, business-rule validation on the result, and pre-flight token counting.

---

### Exercise 3 — Few-Shot Examples for `tool_use`

**File:** `exercise_3_few_shot.py`

**Goal:** Inject few-shot examples into message history to improve judgment on ambiguous tickets.

#### Why few-shot works differently for `tool_use`

With Approach A (prompt-only JSON), examples demonstrate format. With `tool_use`, format is already enforced. Examples serve a different purpose: **demonstrating judgment on edge cases the schema cannot resolve**.

A ticket that is genuinely ambiguous is still ambiguous after you add an enum. The example shows how a human expert would reason about it.

#### The injection pattern

Few-shot examples must be injected as real message history turns — **not** in the system prompt:

```python
messages = []
for i, example in enumerate(FEW_SHOT_EXAMPLES):
    # Turn 1: user sends the example ticket
    messages.append({"role": "user", "content": example["ticket"]})

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

    # Turn 3: user acknowledges the tool result (required for valid turn structure)
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
```

The three-turn structure (user → assistant tool_use → user tool_result) is required. The API enforces strict turn alternation. Omitting the `tool_result` turn produces a `400` error.

#### The three examples

| # | Ticket type | Classification | Why it matters |
|---|---|---|---|
| 1 | Password reset | `auto_resolve`, confidence 0.97 | Shows high-confidence clear case |
| 2 | Enterprise €1,200 charge | `escalate`, team `enterprise` | Shows when to escalate with team |
| 3 | "I think I was charged twice, $9" | `needs_info`, confidence 0.75 | The hard case — uncertain customer + small amount = ask first |

Example 3 is the most important. Without it, the model tends to auto-resolve small uncertain charges. With it, the model learns: when the customer is uncertain, ask for confirmation first.

#### What to observe

Run the script. Compare "Without examples" vs "With examples" for the ambiguous tickets. The clear cases should be the same either way. The ambiguous tickets (vague complaint, small possible duplicate) are where examples change the decision.

#### Questions to answer before moving on

1. Why inject examples as message history turns instead of in the system prompt?
2. What error does the API return if you include an assistant `tool_use` block with no following `tool_result`?
3. For `tool_use`, few-shot examples demonstrate _______ not _______.

#### Try it

Add a fourth example: a French ticket (`"Je n'arrive pas à me connecter depuis hier"`) classified as `auto_resolve`. Run the ambiguous tickets again and check whether the model handles them differently.

#### Exam rule

> Few-shot examples for `tool_use` are injected as complete message turns: user (ticket) → assistant (tool_use with the correct answer) → user (tool_result acknowledging it). They demonstrate **judgment on edge cases**, not format. The API requires a `tool_result` after every `tool_use` in message history — omitting it causes a `400` error.

---

### Exercise 4 — The Validation-Retry Loop

**File:** `exercise_4_validation_retry.py`

**Goal:** Feed structured error information back to the model when business rules fail, and escalate when retries are exhausted.

#### The pattern

```
Call API with tool_choice
    ↓
validate business rules
    ↓
  Pass → return {"status": "success", ...}
  Fail → send error back with is_error=True
         if attempts < MAX_RETRIES: retry
         else: return {"status": "escalation", "reason": "validation_exhausted"}
```

#### The retry message structure

```python
messages = [{"role": "user", "content": ticket}]

for attempt in range(1, MAX_RETRIES + 1):
    response = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=512,
        tools=[CLASSIFY_TOOL],
        tool_choice={"type": "tool", "name": "classify_ticket"},
        messages=messages,
    )
    tool_block = response.content[0]
    result = tool_block.input

    error_msg = validate(result)
    if error_msg is None:
        return {"status": "success", "attempts": attempt, ...}

    # Validation failed — send the error back
    messages.append({"role": "assistant", "content": response.content})
    messages.append({
        "role": "user",
        "content": [{
            "type": "tool_result",
            "tool_use_id": tool_block.id,
            "is_error": True,          # tells the model: your answer was rejected
            "content": json.dumps({"error": True, "message": error_msg}),
        }],
    })

return {"status": "escalation", "reason": "validation_exhausted"}
```

The `is_error: True` flag signals to the model that its previous tool call produced a bad result. Without this flag, the model has no reason to change its answer on retry.

#### What to observe

Run the script. The "clean ticket" should pass on attempt 1. The low-confidence ticket may fail attempt 1 (auto_resolve with low confidence), then correct on attempt 2 after seeing the error message. Check the attempt history printed for each ticket.

#### Questions to answer before moving on

1. What does `is_error: True` in a `tool_result` signal to the model?
2. After `MAX_RETRIES` failures, what is the correct exit — keep retrying, raise an exception, or return a typed result?
3. Why must you append `{"role": "assistant", "content": response.content}` before the `tool_result`?

#### Try it

Change `MAX_RETRIES` to 1 and run again. Which ticket now escalates that previously succeeded? This demonstrates the cost/retry tradeoff.

#### Exam rule

> The validation-retry loop sends `tool_result` with `is_error: True` and a specific error message back to the model. The model uses this to correct its answer. After `MAX_RETRIES` failures, return a **typed escalation result** — do not retry indefinitely. `is_error: True` is the signal that makes the model try again differently; without it, the model has no reason to change its answer.

---

### Exercise 5 — Multi-Pass Review for High-Stakes Tickets

**File:** `exercise_5_multi_pass_review.py`

**Goal:** Add a second model call that reviews the first classification and can override it when the stakes are high.

#### Why two passes?

A single classifier optimises for the average case. A reviewer with an adversarial prompt catches the edge cases. For enterprise billing disputes and potential legal issues, the cost of misclassification is high enough to justify the extra API call.

#### Two-pass architecture

```
Ticket
  ↓
classify_first_pass()    ← standard tool_choice call
  ↓
review_classification()  ← second call with adversarial system prompt
  ↓
verdict: "confirmed" → use first pass decision
verdict: "overridden" → use reviewer's decision
```

#### The review tool

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
                # Required even when confirming — a rubber-stamp review is useless.
            },
            "overriding_decision": {
                "type": ["string", "null"],
                "enum": ["auto_resolve", "escalate", "needs_info", None],
            },
            "overriding_escalation_team": {
                "type": ["string", "null"],
                "enum": ["billing", "technical", "enterprise", "legal", None],
            },
        },
    },
}
```

#### The adversarial system prompt

```python
system=(
    "You are a senior support classification reviewer. "
    "Your job is to catch errors in the initial classification. "
    "Be critical. The cost of auto-resolving a ticket that needs escalation "
    "is much higher than the cost of escalating one that could be auto-resolved. "
    "When in doubt, escalate or ask for more info."
)
```

The "be critical" framing is intentional. A reviewer that always confirms is not a review.

#### Interpreting the override rate

```
Results: 1/5 tickets overridden (20%)

Override rate > 20%: first-pass classifier has a systematic problem.
                     Fix the first-pass prompt, not the reviewer.
Override rate < 5%:  two-pass may not be worth the cost.
                     The first-pass is already reliable.
Override rate 5–20%: two-pass is working as intended.
```

#### What to observe

Run the script. Check each ticket's verdict and justification. For any overridden ticket, read the justification — it should reference something the first pass missed. The override rate summary tells you whether two-pass is earning its cost.

#### Questions to answer before moving on

1. What are the two values of the review tool's `verdict` field?
2. Why is `justification` required even when the verdict is "confirmed"?
3. What override rate suggests the first-pass prompt needs work?
4. What override rate suggests two-pass is unnecessary?

#### Try it

Change the reviewer system prompt to be less adversarial (remove "be critical", soften the framing). Run again. Does the override rate change? This demonstrates how the reviewer's framing affects how often it overrides.

#### Exam rule

> Two-pass review runs a second model call that reviews the first classification. The review tool uses `verdict: "confirmed" | "overridden"` and requires `justification` for both verdicts. An override rate above 20% signals a first-pass prompt problem — fix the classifier, not the reviewer. An override rate below 5% suggests two-pass is not worth the cost.

---

## Lab Completion Checklist

Before moving to Week 6, answer these without looking:

- [ ] What is the difference between a JSON schema `enum` and a plain `string` type, from the API's perspective?
- [ ] Why declare a conditionally required field as nullable rather than optional?
- [ ] Name two things `tool_choice` cannot prevent even when enforced
- [ ] What does `is_error: True` in a `tool_result` signal to the model?
- [ ] After `MAX_RETRIES` failures, what should the retry loop return?
- [ ] What is the correct message history structure for injecting few-shot examples with `tool_use`?
- [ ] What does an override rate above 20% indicate about the first-pass classifier?

---

## Exam Connections

| Exercise | Domain | Pattern Covered |
|---|---|---|
| 1 | D3 | Schema design: enums, typed nulls, required fields; Approach A vs B |
| 2 | D3 | `tool_choice` enforcement; `max_tokens` edge case; business-rule validation; token pre-flight |
| 3 | D3 | Few-shot examples for judgment; multi-turn injection pattern for `tool_use` |
| 4 | D3, D5 | Validation-retry loop; `is_error: True` tool results; typed escalation |
| 5 | D3 | Multi-pass review; override rate threshold; adversarial reviewer prompt |

---

## What's Next

Week 6 moves from how you structure model output to how you connect the model to the outside world — MCP server design, tool description quality, and the three-response-shape pattern.

→ **[Week 6 Lab — Tool Design & MCP Integration](../week-6-tool-design-mcp/README.md)**

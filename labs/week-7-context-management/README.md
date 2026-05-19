# Week 7 Lab — Context Management & Reliability

> **Resolve context:** The agent told a customer their refund was processing, then fifteen messages later asked for the information to start the refund. That was a context positioning failure. The system prompt was correct. The account data was in the history. The model had simply stopped attending to information said too far back. These exercises make that failure reproducible and fixable.

## Learning Objectives

- Understand why context positioning matters — not just what fits in the window, but what the model attends to
- Implement the pinning pattern for critical facts that must survive long conversations
- Apply progressive summarisation correctly and understand what naive summarisation loses
- Build error propagation handling in multi-agent chains so a failure at step 3 does not silently corrupt steps 4 and 5
- Implement escalation triggers based on context health, not sentiment

## Prerequisites

- Anthropic SDK installed (`pip install anthropic python-dotenv`)
- `.env` with `ANTHROPIC_API_KEY`

---

## Exercises

### Exercise 1 — Reproducing the Context Drift Failure

**File:** `exercise_1_context_drift.py`

**Goal:** Deliberately reproduce the Chapter 5 failure — a critical fact established early in a conversation is "forgotten" later — and understand exactly what causes it.

#### Context window ≠ attention window

A fact can be within the token limit and still be effectively invisible if it is positioned where the model's attention decays. The model attends most strongly to:
1. The beginning (system prompt and early turns)
2. The most recent messages

Middle turns are the danger zone.

#### The experiment

The script builds a conversation where a critical fact is established, then buried by filler exchanges, then recalled:

```python
# Turn 0: critical fact established
user:      "Has my refund been approved?"
assistant: "Yes, €450 approved. Reference REF-2024-9182. Allow 3–5 days."

# Turns 1–10: filler exchanges (bury the fact)
user:      "Is support available 24/7?"
assistant: "Yes, 24/7."
# ... repeat 9 more times

# Final turn: recall question
user:      "I need to follow up with my bank. What is my refund reference and amount?"
```

The fact is tested at four positions (0, 3, 7, 10 filler exchanges before the recall question) to show how attention decays as the fact moves further from both ends.

#### What to observe

Run the script. Early positions (fact at position 0 or 3) should recall both the reference number and amount. Later positions (fact at 7 or 10) may fail to recall one or both. This is not a prompt quality issue — it is a structural attention issue.

#### Questions to answer before moving on

1. Why does a fact within the context window sometimes get "forgotten"?
2. Which positions in a long conversation receive the most model attention?
3. Is this a problem that can be solved by making the system prompt longer?

#### Try it

Change `TOTAL_FILLER` to 5 and re-run. Does recall improve? Then increase to 20. At what depth does the model reliably fail?

#### Exam rule

> **Context window ≠ attention window.** A fact can be within the token limit and still be effectively invisible if buried in the middle of a long conversation. The model attends most strongly to the beginning and the most recent messages. Middle turns are the danger zone.

---

### Exercise 2 — The Pinning Pattern

**File:** `exercise_2_pinning.py`

**Goal:** Fix the context drift failure by extracting critical facts and pinning them to the beginning of the conversation.

#### The ACTIVE CONTEXT block

```
[System prompt]
[ACTIVE CONTEXT — ground truth for this ticket]
  Refund reference: REF-2024-9182
  Refund amount:    €450
  Commitment: Refund processing in 3–5 business days
[END ACTIVE CONTEXT]
[Conversation history...]
[Current user message]
```

The block is:
- Always placed **after the system prompt** — where attention is highest
- **Overwritten at each turn** with the current ground truth (not appended to)
- Formatted as a first-class user message so the model treats it as established context

#### Why placing it at the END is worse

Even though a message at the end of the history is more "recent," placing the ACTIVE CONTEXT there causes two problems:
- The model may treat it as a new user instruction rather than established ground truth
- It competes with the actual user message for the model's final-turn attention

#### The implementation

```python
# Step 1: extract critical facts from the conversation
facts = extract_critical_facts(messages)
# → {"refund_reference": "REF-2024-9182", "refund_amount": 450.0,
#    "commitments": ["Refund processing in 3–5 business days"]}

# Step 2: build the ACTIVE CONTEXT block
active_context = build_active_context_block(facts)

# Step 3: inject at the top of the message history
pinned_messages = [
    {"role": "user",      "content": active_context},
    {"role": "assistant", "content": "Understood. I have noted these facts."},
] + messages
```

#### What to observe

Run the script. Both approaches use the same 10-filler conversation from Exercise 1. Without pinning, the model may fail to recall the reference or amount. With pinning, both should be recalled correctly because the facts are injected at the position of highest attention.

#### Questions to answer before moving on

1. Where should the ACTIVE CONTEXT block be placed — before or after the conversation history? Why?
2. Should the ACTIVE CONTEXT block be appended to or overwritten at each turn?
3. Why does placing the block at the end of history fail?

#### Try it

Modify `ask_with_pinning()` to place the ACTIVE CONTEXT block at the END of the messages instead of the beginning. Does recall still work? This demonstrates position matters.

#### Exam rule

> The ACTIVE CONTEXT block extracts critical facts and pins them immediately after the system prompt — always at the position of highest attention. It is overwritten at each turn with the current ground truth. Placing it at the end of history is worse because the model treats it as a new user message rather than established context.

---

### Exercise 3 — Progressive Summarisation

**File:** `exercise_3_summarisation.py`

**Goal:** Understand what naive summarisation loses — and implement commitment-preserving summarisation that keeps specific facts intact.

#### The problem with naive summarisation

```
Before compression:
  "Your refund of €89 has been approved. Reference REF-2026-0088."

After naive compression:
  "Billing issue discussed."

Agent response when asked for reference:
  "Your reference number is REF-2026-0099."   ← made up
```

Naive summarisation replaces specific facts with vague summaries. The model then fills in plausible-sounding replacements.

#### Commitment-preserving summarisation

```python
# Step 1: extract commitments verbatim BEFORE compressing
commitments = extract_commitments(messages)
# → ["Duplicate charge: INV-2026-0442 for €89",
#    "Refund approved: €89, reference REF-2026-0088",
#    "Confirmation email promised within 1 hour"]

# Step 2: summarise only the context (not the commitments)
context_summary = summarise_context_only(messages)
# → "Customer reported a duplicate charge and a refund was processed."

# Step 3: the commitments are NEVER compressed — kept verbatim
```

#### What to survive compression

| Safe to compress | Never compress |
|---|---|
| Greetings, acknowledgements | Reference numbers (INV-*, REF-*) |
| Procedural exchanges | Amounts (€89, $450) |
| Emotional expressions | Dates and deadlines |
| Repeat clarifications | Specific promises |

#### What to observe

Run the script on the sample 12-turn conversation. Naive summarisation will lose the invoice number, refund amount, and reference code. Commitment-preserving summarisation will keep all three in the extracted commitments list.

#### Questions to answer before moving on

1. What does naive summarisation replace specific facts with?
2. Name two types of information that should never be compressed.
3. Why does the model make up plausible-sounding replacements after summarisation?

#### Try it

Add a 5th commitment to the sample conversation: `"Enterprise discount of 15% applied to next invoice."` Re-run and verify it survives commitment-preserving summarisation but is lost in naive summarisation.

#### Exam rule

> Summarisation always loses information — the question is whether it loses the *right* information. Commitments (amounts, reference numbers, deadlines, promises) must survive intact. Extract them verbatim before compressing. Naive summarisation replaces them with vague summaries and the model fills in plausible-sounding fabrications.

---

### Exercise 4 — Error Propagation in Multi-Agent Chains

**File:** `exercise_4_error_propagation.py`

**Goal:** Show that a failure at one pipeline step must abort downstream steps — not let them proceed with corrupted input.

#### The pipeline

```
Step 1: account_lookup   (required)
Step 2: billing_lookup   (required)
Step 3: incident_lookup  (optional — failure is tolerated)
Step 4: classify_ticket  (required)
Step 5: draft_reply      (required)
```

#### The bug — proceeding with corrupted context

```python
# Step 3 fails (incident API timeout) — returns {} with old code
# Step 4 sees: {"account": {...}, "billing": {...}, "incidents": {}}
# Step 4 treats {} as "no incidents found" and proceeds
# Step 5 drafts a reply that ignores an active incident
```

The step that proceeds with a failed predecessor's output is **worse** than a step that aborts — it generates confident-sounding incorrect output.

#### The fix — context health check before each step

```python
def check_context_health(context: dict) -> Optional[str]:
    for step_config in PIPELINE_STEPS:
        step_name = step_config["name"]
        if step_name not in context:
            continue
        result = context[step_name]
        if result.get("status") == "failed" and step_config["required"]:
            return f"Required step '{step_name}' failed: {result.get('reason')}"
    return None

# Before each step:
health_error = check_context_health(context)
if health_error:
    context[step_name] = {"status": "aborted", "reason": health_error}
    continue  # skip this step
```

#### Required vs optional steps

```python
PIPELINE_STEPS = [
    {"name": "account_lookup",  "required": True},   # fails → all downstream abort
    {"name": "billing_lookup",  "required": True},
    {"name": "incident_lookup", "required": False},  # fails → pipeline continues
    {"name": "classify_ticket", "required": True},
    {"name": "draft_reply",     "required": True},
]
```

#### What to observe

Three scenarios:
- **No failures**: all 5 steps succeed, final reply is generated
- **Required step fails** (billing_lookup): steps 3–5 are aborted, no reply generated
- **Optional step fails** (incident_lookup): steps 4–5 continue, reply is generated with available context

#### Questions to answer before moving on

1. Why is proceeding with a failed step's output worse than aborting?
2. What distinguishes a required step from an optional step in the pipeline config?
3. Where does the context health check run — before or after the step executes?

#### Try it

Change `incident_lookup` to `required: True` in `PIPELINE_STEPS`. Re-run Scenario C. Does the pipeline now abort when incident lookup fails?

#### Exam rule

> A step that proceeds with a failed predecessor's output generates confident-sounding incorrect output. The context health check runs **before** each step. Required step failure → all downstream steps abort. Optional step failure → pipeline continues, step is skipped.

---

### Exercise 5 — Context Health as an Escalation Trigger

**File:** `exercise_5_context_health.py`

**Goal:** Replace sentiment-based escalation with context-health-based escalation that is auditable and consistent.

#### The two escalation approaches

**Sentiment-based:** escalate if the customer sounds frustrated or angry.

**Context-based:** escalate if the context itself is unreliable — regardless of tone.

#### Context health metrics

```python
{
    "compression_depth": 3,         # how many times the conversation was summarised
    "provenance_gaps": [            # facts whose source cannot be identified
        "refund amount mentioned but source turn unclear"
    ],
    "contradictions": [             # facts that conflict with each other
        "refund amount: €89 in turn 3, €120 in turn 7"
    ],
    "sentiment": "calm",            # customer tone (used only for sentiment trigger)
}
```

#### Escalation thresholds

```python
def should_escalate_by_context(report: dict) -> dict:
    if report["compression_depth"] > 2:
        return {"escalate": True, "reason": "Compressed more than twice."}
    if report["provenance_gaps"]:
        return {"escalate": True, "reason": f"Provenance gaps: {report['provenance_gaps']}"}
    if report["contradictions"]:
        return {"escalate": True, "reason": f"Contradictions: {report['contradictions']}"}
    return {"escalate": False, "reason": "Context is healthy."}
```

#### Three test cases

| Ticket | Sentiment | Context health | Sentiment trigger | Context trigger |
|---|---|---|---|---|
| Frustrated, clean context | angry | no issues | escalate | do not escalate |
| Calm, contradicted context | calm | contradictions | do not escalate | escalate |
| Compressed 3 times | neutral | depth > 2 | do not escalate | escalate |

The two approaches diverge on every test case.

#### What to observe

Run the script. For each test case, compare `sentiment trigger` vs `context trigger`. The frustrated-but-clean-context ticket shows the clearest divergence: the old approach wastes an escalation on a customer who is merely impatient, while the calm-but-contradicted ticket would be silently sent an incorrect reply.

#### Questions to answer before moving on

1. Name the three context health metrics.
2. Why is context-based escalation more auditable than sentiment-based?
3. A customer is angry but the context health report shows no gaps or contradictions. Which trigger should fire?

#### Try it

Add a fourth test case: a ticket that has both high compression depth AND an angry customer. Do both triggers fire? Does it matter which one fires first?

#### Exam rule

> Context health escalation is auditable: you can explain exactly why a ticket was escalated ("compressed 3 times, refund amount appears with conflicting values"). Sentiment escalation cannot be explained this way. A frustrated customer with clean context does not need escalation. A calm customer with contradicted context does.

---

## Lab Completion Checklist

Before moving to Week 8, answer these without looking:

- [ ] Why does a critical fact within the context window sometimes still get "forgotten"?
- [ ] Where should the ACTIVE CONTEXT block be placed — before or after the conversation history?
- [ ] Should the ACTIVE CONTEXT block be appended to or overwritten at each turn?
- [ ] Name two types of information that should never be summarised away
- [ ] What happens downstream when a required step in a multi-agent pipeline fails?
- [ ] Name the three context health metrics and the escalation threshold for each

---

## Exam Connections

| Exercise | Domain | Pattern Covered |
|---|---|---|
| 1 | D5 | Context window vs. attention window; reproducing drift |
| 2 | D5 | ACTIVE CONTEXT pinning pattern; position matters |
| 3 | D5 | Progressive summarisation; commitment preservation |
| 4 | D1, D5 | Error propagation in multi-agent chains; health check before each step |
| 5 | D5 | Context health vs. sentiment escalation; auditable triggers |

---

## What's Next

Week 8 is the final push before the exam: scenario walkthroughs, anti-pattern drills, and a full timed practice simulation across all five domains.

→ **[Week 8 Lab — Review & Practice Exam](../week-8-review/README.md)**

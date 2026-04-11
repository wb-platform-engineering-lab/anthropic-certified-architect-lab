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
- Anthropic SDK installed (`pip install anthropic` / `npm install @anthropic-ai/sdk`)
- `.env` with `ANTHROPIC_API_KEY`

**Languages:** Each exercise is implemented in both Python (`exercise_N.py`) and TypeScript (`exercise_N.ts`).

---

## Exercises

### Exercise 1 — The Schema as a Contract

**Goal:** Design a JSON schema for ticket classification that eliminates every ambiguity that caused the Chapter 3 incident.

**Scenario:** Resolve's original schema had three problems: `decision` was a free-text string (the model sometimes returned "auto_resolve", sometimes "auto-resolve", sometimes "resolve automatically"), `confidence` had no range constraint, and `escalation_team` was optional but the routing system assumed it was always present on escalations.

**You will:**
1. Start with the broken schema and reproduce at least two of its failure modes by crafting prompts that trigger them
2. Fix the schema: `decision` becomes an enum, `confidence` gets a range description, `escalation_team` is conditionally required based on `decision`
3. Add a `resolution` field for auto-resolve cases and verify it is nullable with an explicit `null` type when `decision` is "escalate" — not omitted, explicitly null
4. Version the schema: save it as `schema_v1.json` and `schema_v2.json`, write a migration test that verifies v1 outputs can be detected and re-run through the v2 tool

**Key insight:** A schema that uses `enum` instead of `string` eliminates an entire class of model variability. A field that is sometimes present and sometimes omitted is a nullable field — declare it explicitly rather than relying on the downstream code to handle both cases.

---

### Exercise 2 — `tool_use` as the Enforcement Mechanism

**Goal:** Use `tool_choice` to make structured output structurally impossible to bypass — and understand the edge cases where even this can be circumvented.

**Scenario:** After the Chapter 3 incident, Resolve replaced the prompt instruction "always return JSON" with a `tool_choice` that forces the model to call `classify_ticket`. But there are three remaining edge cases the team had to handle.

**You will:**
1. Implement ticket classification with `tool_choice: {"type": "tool", "name": "classify_ticket"}` — verify `stop_reason` is always `tool_use`
2. Discover edge case 1: what happens when `max_tokens` is too low to complete the tool call? Implement the handler
3. Discover edge case 2: what happens if the tool input passes schema validation but contains a logically invalid combination (e.g., `decision: "auto_resolve"` with `confidence: 0.12`)? Add a post-schema validation layer in code
4. Discover edge case 3: what happens with a very long ticket that fills the context window before the tool call can be generated? Implement a pre-flight token count check and a truncation strategy

**Key insight:** `tool_choice` eliminates most structured output failures. It does not eliminate token budget issues, logical constraint violations, or context overflow. You need a layer of code validation on top of schema validation.

---

### Exercise 3 — Few-Shot Prompting from Production Data

**Goal:** Build few-shot examples from real (or realistic) production tickets and measure their effect on output consistency.

**Scenario:** Jade's first few-shot examples were synthetic — she wrote them herself to demonstrate the expected format. They were grammatically perfect and obviously labelled. Production tickets are messier. The model performed worse on real tickets than on Jade's examples because the distribution was different.

**You will:**
1. Create a set of 20 synthetic "production-realistic" tickets with known correct classifications — include tickets with typos, mixed languages, incomplete information, and ambiguous intent
2. Measure baseline classification accuracy without few-shot examples
3. Add 3 few-shot examples chosen for maximum coverage: one clear auto-resolve, one clear escalation, and one genuinely ambiguous case with an explanation of why it was classified the way it was
4. Measure accuracy again — identify which ticket types most benefited from the examples and which were unaffected
5. Understand the difference between few-shot examples that demonstrate format (useful in Approach A, where you're asking for JSON) and few-shot examples that demonstrate judgment (useful in any approach for hard cases)

**Key insight:** Few-shot examples for structured output are not about demonstrating the format — `tool_use` handles that. They are about demonstrating judgment on the edge cases the schema cannot distinguish.

---

### Exercise 4 — The Validation-Retry Loop

**Goal:** Build a retry loop that feeds structured error information back to the model, and handles the difference between a recoverable error and an unrecoverable one.

**Scenario:** Even with `tool_use` enforcement, Resolve occasionally gets tool inputs that pass schema validation but fail business rules. The retry loop needs to feed the specific error back as a `tool_result` with `is_error: true` and ask the model to correct it — but only up to a point. Exhausted retries must escalate, not silently succeed.

**You will:**
1. Implement a `validate_classification(tool_input) → ValidationResult` function that catches logical errors the schema cannot express: low-confidence auto-resolves, escalations without team assignment on enterprise accounts, confidence scores that contradict the stated reason
2. On validation failure, add the tool result with `is_error: true` and a structured error object to the message history and retry
3. On the second failure, change the error message to be more explicit: include the specific field that failed and an example of a valid value
4. After three failures, return a typed `{status: "escalation", reason: "validation_exhausted"}` — do not attempt a fourth call
5. Verify that the audit log for every ticket shows the full retry history, including each error message sent back to the model

**Key insight:** The error message you feed back in the `tool_result` directly affects whether the model corrects the right thing. A generic "validation failed" is less useful than "confidence score 0.12 is below the 0.6 threshold required for auto_resolve — either raise confidence or change decision to escalate."

---

### Exercise 5 — Multi-Pass Review for High-Stakes Output

**Goal:** Implement a two-pass pattern for outputs where the cost of error is high — a generation pass followed by a review pass — and understand when the overhead is justified.

**Scenario:** Resolve's enterprise clients have an SLA on response quality. For tickets tagged as high-value (account tier = enterprise, ticket type = billing dispute, potential credit > €500), a single-pass classification is not enough. The coordinator runs a second model call that reviews the first classification and can override it with a justification.

**You will:**
1. Implement a `review_classification(ticket, initial_classification) → reviewed_classification` function that uses a second `tool_use` call to either confirm or override the initial decision
2. Define a `ReviewResult` schema with: `verdict` (enum: "confirmed" or "overridden"), `overriding_decision` (nullable, present only on "overridden"), `justification` (required string)
3. Run 10 tickets through the two-pass system and count how often the reviewer overrides the initial classification — adjust the initial classification prompt if the override rate is above 20%
4. Measure the token cost of two-pass vs. one-pass and build a selector that only uses two-pass for high-stakes tickets

**Key insight:** Two-pass review is not about catching model errors — it is about adding a second perspective on genuinely ambiguous cases. If the override rate is very low, the second pass is wasted tokens. If it is very high, the first pass prompt needs work.

---

## Lab Completion Checklist

Before moving to Week 6, answer these without looking:

- [ ] What is the difference between a JSON schema `enum` and a `string` type, from the model's perspective?
- [ ] Why should a conditionally required field be declared as nullable rather than omitted?
- [ ] Name two things that `tool_choice` cannot prevent even when enforced correctly
- [ ] What should an `is_error: true` tool result contain to be maximally useful to the model?
- [ ] After how many retries should a validation loop escalate — and what should the typed exit look like?
- [ ] When is multi-pass review worth the token cost? Give a concrete criterion.

---

## Exam Connections

| Exercise | Domain | Exam Pattern Covered |
|---|---|---|
| 1 | D3 | Schema design: enums, typed nulls, conditional required fields |
| 2 | D3 | `tool_choice` enforcement; edge cases that survive schema validation |
| 3 | D3 | Few-shot examples for judgment, not format demonstration |
| 4 | D3, D5 | Validation-retry loop; structured error feedback; typed exhaustion exit |
| 5 | D3 | Multi-pass review; cost vs. quality tradeoff; high-stakes routing |

---

## What's Next

Week 6 moves from how you structure model output to how you connect the model to the outside world — MCP server design, tool description quality, and the three-response-shape pattern that Chapter 4 was missing.

→ **[Week 6 Lab — Tool Design & MCP Integration](../week-6-tool-design-mcp/README.md)**

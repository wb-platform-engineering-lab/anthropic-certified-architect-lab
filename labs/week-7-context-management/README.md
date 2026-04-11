# Week 7 Lab — Context Management & Reliability

> **Resolve context:** The agent that told a customer their refund was processing, then fifteen messages later asked for the information to start the refund — that was a context positioning failure. The system prompt was correct. The account data was in the history. The model had simply stopped attending to things said too far back. These exercises make that failure reproducible, measurable, and fixable.

## Learning Objectives

- Understand why context positioning matters — not just what fits in the window, but what the model attends to
- Implement a pinning pattern for critical facts that must survive long conversations
- Apply progressive summarisation correctly and understand the specific risks of doing it wrong
- Design escalation triggers based on context health, not sentiment
- Build error propagation handling in multi-agent chains so a failure at step 3 does not silently corrupt steps 4 and 5
- Implement information provenance tracking so every fact can be traced to its source

## Prerequisites

- Weeks 2–3 completed — context management failures appear at the intersection of long conversations and agentic loops
- Anthropic SDK installed (`pip install anthropic` / `npm install @anthropic-ai/sdk`)
- `.env` with `ANTHROPIC_API_KEY`

**Languages:** Each exercise is implemented in both Python (`exercise_N.py`) and TypeScript (`exercise_N.ts`).

---

## Exercises

### Exercise 1 — Reproducing the Context Drift Failure

**Goal:** Deliberately reproduce the Chapter 5 failure — a model contradicting itself in a long conversation — and verify you understand exactly what causes it.

**Scenario:** The Chapter 5 agent contradicted itself by turn 30. Before you can fix it, you need to be able to reproduce it reliably. This exercise builds the minimal reproduction case: a long conversation where a critical fact established early is contradicted later.

**You will:**
1. Build a 40-turn synthetic conversation where turn 3 establishes a critical fact (e.g., "refund approved, reference REF-2024-9182") and turn 35 asks a question that requires recalling it
2. Run the conversation without any context management — observe whether and when the model loses the fact
3. Measure the "attention decay" by varying when the critical fact is established: turns 3, 10, 20, and 30 — at which point does the model reliably recall it vs. lose it?
4. Confirm that the problem is not the context window (the fact is always within the window) but attention — the model is not actively attending to information buried in the middle of a long history

**Key insight:** Context window ≠ attention window. A fact can be within the token limit and still be effectively invisible if it is positioned where the model's attention decays. The exam tests whether you know the difference.

---

### Exercise 2 — The Pinning Pattern

**Goal:** Implement the ACTIVE CONTEXT block pattern that prevents critical facts from being lost in long conversations.

**Scenario:** Resolve's fix for Chapter 5 was to extract critical facts after every turn and write them into a structured block immediately after the system prompt. The block is not appended to — it is overwritten at each turn with the current ground truth. It is always at the top of the conversation, always fresh, always visible.

**You will:**
1. Define what qualifies as a "critical fact" for a ticket conversation: commitments made, amounts confirmed, refund reference numbers, account tier, any fact that, if forgotten, would cause a contradiction
2. Implement `extract_critical_facts(message_history) → CriticalFacts` — a function that calls the API to extract facts from the current conversation and returns a structured object
3. Implement the ACTIVE CONTEXT block: a fixed-position section in the message history that is rebuilt from `CriticalFacts` at every turn
4. Re-run the 40-turn conversation from Exercise 1 with pinning enabled — verify the model recalls the critical fact at turn 35
5. Verify that the ACTIVE CONTEXT block is always placed after the system prompt and before the conversation history — test what happens when it is placed at the end instead

**Key insight:** The ACTIVE CONTEXT block works because it is always at the beginning of the conversation, right after the system prompt — where attention is highest. Placing it at the end of the history, where it is more "recent," is actually worse because the model may treat it as a new user message rather than established ground truth.

---

### Exercise 3 — Progressive Summarisation: When and How

**Goal:** Implement conversation summarisation correctly and understand the specific failure modes that make naive summarisation dangerous.

**Scenario:** Resolve's early summarisation strategy compressed the first 30 turns of a ticket into a paragraph to save tokens. It worked for most tickets. For billing disputes, it silently lost the specific invoice numbers and amounts, replacing them with "billing issue discussed." The agent then made up plausible-sounding invoice numbers when asked.

**You will:**
1. Implement naive summarisation: compress the oldest 20 turns into a single paragraph using a model call
2. Run the summarised conversation through 10 more turns and identify which facts survived compression and which were lost
3. Implement commitment-preserving summarisation: before compressing, extract all commitments (amounts, reference numbers, deadlines, promises) into a structured list — these are never compressed, only the surrounding context is
4. Define a "safe to compress" heuristic: greetings, acknowledgements, and procedural exchanges are safe; any turn containing a number, a date, a reference code, or a specific claim is not
5. Implement a compression depth counter: track how many times the conversation has been compressed and flag for human review after two compressions

**Key insight:** Summarisation always loses information — the question is whether it loses the *right* information. A commitment expressed as "refund approved for €450 by REF-9182" must survive intact. "The customer expressed frustration" can be discarded.

---

### Exercise 4 — Error Propagation in Multi-Agent Chains

**Goal:** Understand how a failure at one step in a multi-agent chain can silently corrupt subsequent steps — and implement explicit error propagation that prevents this.

**Scenario:** Resolve's pipeline processes tickets through five agents in sequence. When the third agent (incident lookup) fails, it returns `{}` from an earlier version of the code — before the Chapter 4 fix was applied everywhere. The fourth agent (billing check) receives a context that includes the empty incident lookup and proceeds as if incidents were checked and nothing was found. The fifth agent drafts a response that ignores an active incident.

**You will:**
1. Build a five-step pipeline where each step appends its result to a shared context object
2. Simulate a failure at step 3 — return a typed `{status: "failed", step: "incident_lookup", reason: "timeout"}` instead of an empty result
3. Implement a context health check at the start of each step: before running, the agent checks whether any previous step in the context object has `status: "failed"` — if so, it aborts and propagates the failure rather than proceeding with incomplete information
4. Verify that a failure at step 3 causes steps 4 and 5 to abort with a typed failure — not to run with corrupted input
5. Implement a partial-success policy: some steps are optional (incident lookup) and their failure should not block the pipeline — define which steps are required vs. optional in a pipeline config

**Key insight:** Error propagation in a multi-agent chain is not about catching exceptions — it is about making every step aware of the health of the context it receives. A step that proceeds with a failed predecessor's output is worse than a step that aborts, because it generates confident-sounding incorrect output.

---

### Exercise 5 — Context Health as an Escalation Trigger

**Goal:** Implement escalation triggers based on context health metrics — compression depth, ambiguity score, provenance gaps — not sentiment.

**Scenario:** Resolve's enterprise SLA requires that tickets with uncertain context are reviewed by a human before a resolution is sent. The original trigger was sentiment-based: if the customer sounded frustrated, escalate. The new trigger is context-based: if the context has been compressed more than twice, or if critical facts have low provenance confidence, escalate regardless of tone.

**You will:**
1. Define a `ContextHealthReport` with three metrics: `compression_depth` (int), `provenance_gaps` (list of facts whose source turn cannot be identified), and `contradiction_count` (facts that appear to conflict with each other in the history)
2. Implement `assess_context_health(message_history, critical_facts) → ContextHealthReport` using a model call to identify provenance gaps and contradictions
3. Define three escalation thresholds: `compression_depth > 2`, `len(provenance_gaps) > 0` for billing facts, `contradiction_count > 0` for any confirmed commitment
4. Run the same 10 tickets through both the sentiment-based and health-based escalation triggers — compare escalation rates and identify where they diverge
5. Add a provenance annotation to every critical fact: `{fact: "refund approved", source_turn: 12, confidence: "high"}` — use this to build the provenance gaps list

**Key insight:** Context health escalation is auditable. You can show a client exactly why a ticket was escalated: "this conversation was compressed twice and the invoice amount appears in turns 8 and 31 with different values." Sentiment-based escalation cannot be explained this way.

---

## Lab Completion Checklist

Before moving to Week 8, answer these without looking:

- [ ] Why does a critical fact within the context window sometimes still get "forgotten" by the model?
- [ ] Where should the ACTIVE CONTEXT block be placed relative to the system prompt and conversation history?
- [ ] Name two categories of information that should never be compressed in a summarisation step
- [ ] What does "compression depth" measure and why is it an escalation signal?
- [ ] What is the difference between an error that propagates (cascades) and one that short-circuits (stops the chain)?
- [ ] Write a `ContextHealthReport` with three fields from memory and define the escalation threshold for each

---

## Exam Connections

| Exercise | Domain | Exam Pattern Covered |
|---|---|---|
| 1 | D5 | Context window vs. attention window; reproducing drift failure |
| 2 | D5 | ACTIVE CONTEXT pinning pattern; position matters |
| 3 | D5 | Progressive summarisation; commitment preservation; compression depth |
| 4 | D1, D5 | Error propagation in multi-agent chains; context health checks |
| 5 | D5 | Context health as escalation trigger; provenance tracking; auditable escalation |

---

## What's Next

Week 8 is the final push before the exam: scenario walkthroughs, anti-pattern drills, and a full timed practice simulation across all five domains.

→ **[Week 8 Lab — Review & Practice Exam](../week-8-review/README.md)**

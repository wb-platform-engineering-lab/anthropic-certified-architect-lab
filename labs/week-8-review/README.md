# Week 8 Lab — Review & Practice Exam

> **Resolve context:** Sofia asked Jade one question before the certification exam: *"If the CRM goes down at 2 AM and the agent is in the middle of a batch, what happens?"* Jade answered without hesitating — every layer, in order, from the typed `access_failure` through the iteration budget to the escalation queue. That's what this week prepares you for: questions designed to sound reasonable when they're wrong.

## Learning Objectives

- Walk through all 6 exam scenarios end-to-end and identify which domains each tests
- Drill the anti-pattern recognition that the exam tests through plausible wrong answers
- Simulate exam conditions: 60 questions, 120 minutes, no tools, no documentation
- Identify and close gaps in your weakest domain before exam day

## Prerequisites

- Weeks 1–7 lab completed
- All 7 completion checklists answered without looking
- Practice test accounts: [certsafari.com](https://www.certsafari.com/anthropic), [Sundog Education](https://www.sundog-education.com/2026/03/26/practice-tests-for-claude-certified-architect-certification/)

**Note:** This week has no new code exercises. The work is reading, pattern recognition, and timed practice.

---

## Part 1 — Scenario Walkthroughs

For each of the 6 exam scenarios, work through the architecture decisions you would make in production. The exam presents 10 questions per scenario — you will see 4 of 6 on exam day.

### Scenario 1 — Customer Support Resolution Agent

This is the Resolve product. You have built it across 7 weeks.

**Architecture decisions to know cold:**
- Loop termination: `stop_reason` only — never model language
- Tool definitions: four-part description, three response shapes
- Escalation: complexity-based criteria, typed exit
- Schema enforcement: `tool_choice` with a classification tool, not a prompt asking for JSON
- MCP: CRM, billing, and knowledge base as separate servers with capability boundaries

**Common exam traps in this scenario:**
- A question will ask which escalation approach is correct. The answer involving "detect customer frustration" is always wrong.
- A question will describe a tool returning `{}` on timeout as "safe default behaviour." It is not.
- A question will suggest checking the model's response text for "I have enough information" to end the loop. Always wrong.

---

### Scenario 2 — Code Generation with Claude Code

This is Domain 2 in applied form. The scenario presents an engineering team using Claude Code to write and modify code across a multi-file TypeScript project.

**Architecture decisions to know cold:**
- CLAUDE.md hierarchy: global (personal preferences) → project root (architecture, constraints) → subdirectory (local rules for that module)
- Custom commands are markdown files in `.claude/commands/` — not code, not scripts
- Plan mode must be used for changes touching more than one file
- Non-interactive mode changes what Claude Code does at decision points — it does not just run faster

**Common exam traps in this scenario:**
- A question will describe adding a CLAUDE.md instruction to a subdirectory and ask where it applies. Subdirectory instructions apply to files in that directory only — not the parent.
- A question will ask which approach enforces "never modify X without confirmation." CLAUDE.md instructions set expectations — they are not programmatic constraints. The exam distinguishes between these.
- Non-interactive mode is for CI. Using it for a developer workflow where the engineer wants to review changes is wrong.

---

### Scenario 3 — Multi-Agent Research System

This is Domain 1 Part 2 applied to a research context: a coordinator agent decomposes a research question and dispatches to specialist subagents that each query a different source.

**Architecture decisions to know cold:**
- Context isolation: each subagent receives a task definition, not the coordinator's full history
- Parallel vs. sequential: sources that can be queried independently run in parallel; synthesis runs after all queries complete
- `Promise.allSettled` / `asyncio.gather` with individual result checking — not `Promise.all`
- Fault isolation: a failed source returns a typed failure, not an exception; the coordinator continues with partial results if above the minimum success threshold

**Common exam traps in this scenario:**
- A question will suggest passing the coordinator's full message history to each subagent "so they have context." This breaks isolation and wastes tokens — each subagent needs only its task.
- A question will describe the coordinator waiting for all subagents before proceeding. Correct in principle — but the question will distinguish between `Promise.all` (fails if any fail) and `Promise.allSettled` (collects all results including failures). `allSettled` is correct.

---

### Scenario 4 — Developer Productivity with Claude

This is Domain 4 (built-in tools, MCP) and Domain 2 (codebase exploration) applied to an internal engineering tools context.

**Architecture decisions to know cold:**
- Built-in tools (file read, web search, code execution) are available in Claude Code — they do not need to be defined as custom tools
- When to define a custom MCP tool: when the data source is private, requires authentication, or has no Claude built-in equivalent
- A custom tool named the same as a built-in overrides the built-in — this is a source of bugs
- Tool descriptions for developer tools should describe file path conventions, expected output formats, and what not to do with the results

**Common exam traps in this scenario:**
- A question will ask whether to use a custom `read_file` tool or Claude Code's built-in file reading. The answer is almost always the built-in unless the question specifies a private or authenticated file source.
- A question will describe an MCP server exposing both read and write tools in the same namespace. The correct answer is that read and write tools should be in separate namespaces or separate servers.

---

### Scenario 5 — Claude Code for CI/CD

This is Domain 2 focused entirely on non-interactive, automated workflows.

**Architecture decisions to know cold:**
- Non-interactive mode: Claude Code does not prompt for input — it either completes or exits with a non-zero code
- CI scripts should capture structured JSON output from Claude Code, not parse text
- Exit codes matter: 0 = success, non-zero = failure — the CI pipeline must react to exit codes, not output parsing
- The `/validate-schema` command pattern: runs a check, exits non-zero on failure, produces structured output

**Common exam traps in this scenario:**
- A question will describe a CI script that parses Claude Code's text output to determine success. The correct approach uses exit codes and structured output.
- A question will suggest running Claude Code in interactive mode in CI "so it can ask questions if unsure." Interactive mode in CI will hang. Always non-interactive for automated pipelines.

---

### Scenario 6 — Structured Data Extraction

This is Domain 3 in pure form: extracting structured data from unstructured documents (PDFs, emails, forms) with high reliability.

**Architecture decisions to know cold:**
- `tool_use` enforcement is mandatory — asking for JSON is not enough
- Enum fields for anything with a known value set: document type, currency code, status
- Nullable fields with explicit `null` type — not omitted
- Few-shot examples for genuinely ambiguous cases — not to demonstrate format
- Validation-retry loop: 3 attempts maximum, typed `escalation` exit on exhaustion

**Common exam traps in this scenario:**
- A question will describe a schema with a `type: "string"` field for a document type that has 5 known values. The correct answer replaces it with `enum`.
- A question will describe using few-shot examples to show the model the correct JSON format. The correct answer: `tool_use` handles format enforcement; few-shot examples are for demonstrating correct classification of ambiguous inputs, not format.

---

## Part 2 — Anti-Pattern Drill

Study the anti-patterns table below until you can identify the wrong answer immediately without reasoning through it. The exam is designed so that wrong answers are *plausible* — they represent things engineers actually do.

For each anti-pattern, write out: (1) why it sounds reasonable, (2) why it fails in production, (3) the correct approach.

| Anti-Pattern | Sounds Reasonable Because | Fails In Production Because | Correct Approach |
|---|---|---|---|
| End loop when model says "I'm done" | Model language is readable | Model is uncertain → never says those words → infinite loop | Check `stop_reason == "end_turn"` |
| Return `{}` on tool timeout | Returning something feels safer than throwing | Model interprets `{}` as "no data found" → proceeds incorrectly | Return `{status: "access_failure", code: "TIMEOUT", ...}` |
| Use sentiment to trigger escalation | Frustrated customers need escalation | Politely-worded churn risks are missed; false positives on dramatic phrasing | Complexity-based criteria in code |
| Ask for JSON in the system prompt | Straightforward to implement | Model finds ways to comply in spirit while breaking the parser | `tool_use` with `tool_choice` |
| Summarise entire history to save tokens | Reduces token count | Compresses commitments into prose → model loses specific values | Pin critical facts; compress only non-critical context |
| Pass coordinator's full history to subagents | Gives subagents "full context" | Context grows with each subagent; subagents have irrelevant noise | Each subagent receives a task definition only |
| Use `Promise.all` for parallel subagents | Waits for all results | One failure throws → coordinator crashes → all results lost | Use `Promise.allSettled` to collect typed results for all |
| Prompt rule: "always check account before replying" | Easy to add to system prompt | Can be forgotten, misunderstood, or skipped mid-conversation | Pre-call hook that blocks `draft_reply` if account not checked |
| Generic "validation failed" error in retry loop | Simple to implement | Model cannot determine what to fix → retries produce same error | Structured error with specific field, expected value, example |
| Use interactive Claude Code mode in CI | "Handles edge cases better" | Hangs waiting for input that never comes | `--no-interactive` flag; non-zero exit on any decision point |

---

## Part 3 — Timed Practice Simulation

Complete this before exam day. Do not use any external resources during the simulation.

**Setup:**
1. Create a dedicated 2-hour block with no interruptions
2. Open one of the practice test providers: [certsafari.com/anthropic](https://www.certsafari.com/anthropic) or [Sundog Education](https://www.sundog-education.com/2026/03/26/practice-tests-for-claude-certified-architect-certification/)
3. Set a 120-minute timer
4. No Claude, no documentation, no notes

**After the simulation:**
- [ ] Score the results — target 750+ before the real exam (passing is 720)
- [ ] Identify which domain had the most wrong answers
- [ ] For every wrong answer, write out: what the correct answer is and why the answer you chose sounds plausible

**Gap analysis:**
If your weakest domain is below 65% correct, spend two more days on that domain's lab before booking the exam.

| Domain | Lab | Practice Score |
|---|---|---|
| D1 — Agentic Architecture | Weeks 2–3 | |
| D2 — Claude Code | Week 4 | |
| D3 — Prompt Engineering | Week 5 | |
| D4 — Tool Design & MCP | Week 6 | |
| D5 — Context Management | Week 7 | |

---

## Exam Day Checklist

- [ ] Identity document ready (proctored exam requires ID)
- [ ] Quiet room, stable internet, no second monitors
- [ ] Read the scenario description before answering any question in that scenario — the scenario frames the context for all 10 questions
- [ ] On ambiguous questions: eliminate the two obviously wrong answers first (they are usually the anti-patterns above), then choose between the remaining two based on the production implication
- [ ] If a question mentions "detect" or "check" using the model's output text → it is almost certainly wrong; the correct answer uses code or structured data
- [ ] If a question mentions sentiment analysis for routing decisions → it is wrong; use complexity criteria

**Target:** 750+ (passing is 720)

→ **[Register for the exam](https://anthropic.skilljar.com/claude-certified-architect-foundations-access-request)**

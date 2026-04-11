# Claude Certified Architect – Foundations (CCA-F) Roadmap

## Product Story — Resolve

Every domain in this roadmap is grounded in a realistic engineering failure. The company is **Resolve** — an AI-native B2B SaaS platform that automates customer support using Claude agents. SaaS companies subscribe to let Resolve triage, investigate, and resolve support tickets without human intervention.

**Why this product?**
- Agents making decisions on behalf of customers → agentic architecture has real stakes
- Multiple engineers sharing the same AI codebase → Claude Code configuration matters
- Downstream systems consuming agent output → structured output failures have cascading consequences
- Third-party integrations (CRM, billing, knowledge base) → tool design and MCP are load-bearing
- Long conversation threads → context management has a direct user-facing failure mode

→ **[Read the full story before studying](./STORY.md)** — understand *why* each domain matters before you study *how* it works.

### The Failure Map

| Chapter | Business Failure | Root Cause | Domain |
|---|---|---|---|
| 1 | $11,400 in API calls overnight — one ticket, infinite loop | Loop termination based on model language, not `stop_reason` | Agentic Architecture (27%) |
| 2 | New engineers blocked for 2 days — setup lived in one person's head | No CLAUDE.md, no custom commands, no CI validation | Claude Code (20%) |
| 3 | 800 tickets closed with a generic reply — routing system silently broke | Agent *asked* for JSON; not *required* to produce it via `tool_use` | Prompt Engineering & Structured Output (20%) |
| 4 | 43 billing dispute tickets told "everything is fine" — CRM was down | Tool returned `{}` instead of a typed access failure | Tool Design & MCP (18%) |
| 5 | Agent contradicted commitments it made 20 messages ago | Critical context drifted out of effective attention window | Context Management (15%) |

---

## Exam at a Glance

| | |
|---|---|
| **Format** | 60 scenario-based multiple-choice questions |
| **Duration** | 120 minutes |
| **Passing Score** | 720 / 1000 |
| **Scenarios** | 4 of 6 randomly selected |
| **Cost** | Free for first 5,000 partner employees |
| **Register** | https://anthropic.skilljar.com/claude-certified-architect-foundations-access-request |

---

## Exam Domains

| # | Domain | Weight |
|---|---|---|
| 1 | Agentic Architecture & Orchestration | ~27% |
| 2 | Claude Code Configuration & Workflows | ~20% |
| 3 | Prompt Engineering & Structured Output | ~20% |
| 4 | Tool Design & MCP Integration | ~18% |
| 5 | Context Management & Reliability | ~15% |

---

## Domain Breakdown

### Domain 1 — Agentic Architecture & Orchestration (27%)

> **Business context — Resolve, 200 enterprise customers**
> The resolution agent gets into an infinite loop on a Portuguese-language ticket. The knowledge base search returns nothing. The agent decides it needs more context. It calls the same tools again and again. It runs for four hours and makes 14,000 tool calls. Loop termination was based on the model saying it had enough information — natural language, parsed by the model itself. When the model was uncertain, it never said those words.
>
> **The fix:** Loop termination moves out of the model's judgment entirely. `stop_reason` drives iteration control. Maximum iterations are enforced at the system level. Escalation is triggered by task complexity criteria, not sentiment.

Key topics:
- Agentic loops and `stop_reason` handling — never parse natural language for loop termination
- Multi-agent orchestration: coordinator + subagent patterns, hub-and-spoke models
- Agent SDK: session management, task decomposition
- Hooks for programmatic guardrails — not prompt-based rule enforcement
- Fallback loop design and error recovery strategies
- Escalation: task-complexity criteria, not sentiment analysis

---

### Domain 2 — Claude Code Configuration & Workflows (20%)

> **Business context — Resolve, 400 enterprise customers**
> Resolve hires its third engineer. By day two, she has given up on setting up her environment and is waiting for Jade to become available. The CLAUDE.md file doesn't exist. The custom commands for running test suites and validating agent output are shortcuts in Jade's local config, never shared. Every new engineer requires a two-hour onboarding call that covers the same things. If Jade is unavailable, the AI half of the product is blocked.
>
> **The fix:** A `CLAUDE.md` hierarchy covers the project, the agent loop, and the evals directory separately. Custom commands replace muscle memory. The CI pipeline runs the agent in non-interactive mode on every PR — if the output schema breaks or the tool call count exceeds budget, the build fails.

Key topics:
- `CLAUDE.md` hierarchy (global, project, local) — each level serves a different purpose
- Custom slash commands and skills
- Plan mode and iterative refinement workflows
- CI/CD integration and non-interactive mode
- Batch processing patterns

---

### Domain 3 — Prompt Engineering & Structured Output (20%)

> **Business context — Resolve, 600 enterprise customers**
> Jade updates the system prompt to make the agent sound warmer. For three days, nothing looks wrong. Then a client reports that 200 tickets received a generic reply and were closed. The agent had started generating friendly plain-text responses instead of calling the `resolve_ticket` tool. The routing system received a string, failed to parse it silently, and fell back to a default action. Eight hundred tickets were processed this way.
>
> **The fix:** The `decision` field is no longer generated by *asking* Claude to write JSON — it is generated by calling a tool whose schema enforces the exact structure. `tool_use` stop reason is the only valid end state for a completed ticket. Any other stop reason triggers a retry. The schema is versioned in the repo and treated as a contract.

Key topics:
- `tool_use` for structured output — asking for JSON is not the same as requiring it
- JSON schema design to prevent hallucinations: required fields, enums, typed nulls
- Few-shot prompting for reliability — real production examples, not synthetic ones
- Validation-retry loops
- Multi-pass review strategies
- Explicit success/failure criteria

---

### Domain 4 — Tool Design & MCP Integration (18%)

> **Business context — Resolve, 800 enterprise customers**
> The CRM API is intermittently down. The `get_account_status` tool's error handling returns `{}` when the CRM doesn't respond — returning *something* had felt safer than raising an exception. The agent receives the empty object, interprets it as "no account data found," and continues. During a 90-minute CRM outage, 400 tickets are processed with no account context. Forty-three billing dispute tickets are told their account looks fine. Five escalate the next morning.
>
> **The fix:** Every tool returns one of three explicitly typed response shapes: a success payload, an access failure with an error code, or a typed empty result. The agent now knows the difference between "the CRM is down" and "this customer has no history." Tool descriptions explain when to call the tool, what each response shape means, and what to do on failure. CRM, knowledge base, and billing become separate MCP servers with clear capability boundaries.

Key topics:
- Tool description quality — the description is the model's only source of truth
- Structured error responses: success payload / access failure / typed empty result
- Never suppress errors silently — distinguish failure from absence
- MCP server design and configuration
- Tool boundary management — structural constraints prevent wrong tool calls
- Claude's built-in tools

---

### Domain 5 — Context Management & Reliability (15%)

> **Business context — Resolve, 1,200 enterprise customers**
> Enterprise tickets run 40–80 messages deep across multiple agents and weeks. When Resolve's AI takes over a long thread, it contradicts itself by turn thirty. It confirms a refund is processing, then fifteen messages later asks for the information needed to start one. It applies the correct account tier discount, then later calculates pricing as if the customer is on the default plan. The system prompt is at the top. By the time the model generates turn thirty, it is paying more attention to the recent conversation than to the instructions from the beginning.
>
> **The fix:** Critical account facts are pinned in a structured `ACTIVE CONTEXT` block immediately after the system prompt and overwritten at each turn. Thread compression has a strict rule: commitments and contradictions are preserved at full fidelity, never summarised. A thread that has been compressed more than twice is flagged for human review before the agent continues.

Key topics:
- Context positioning — critical information must be placed where the model will attend to it
- Progressive summarization risks — compression loses what it cannot distinguish from noise
- Pinning vs. summarising: when each is appropriate
- Escalation patterns: complexity-based, not sentiment-based
- Error propagation in multi-agent chains
- Human-in-the-loop review triggers
- Information provenance tracking

---

## Exam Scenarios (4 of 6 randomly selected)

| Scenario | Primary Domains |
|---|---|
| Customer Support Resolution Agent | D1 (agent loop, escalation), D4 (MCP tools) |
| Code Generation with Claude Code | D2 (CLAUDE.md, plan mode, custom commands) |
| Multi-Agent Research System | D1 (coordinator-subagent, context isolation, error propagation) |
| Developer Productivity with Claude | D4 (built-in tools, MCP), D2 (codebase exploration) |
| Claude Code for CI/CD | D2 (non-interactive mode), D3 (structured output, batch) |
| Structured Data Extraction | D3 (JSON schemas, validation loops, few-shot) |

---

## Study Resources

### Official Anthropic Courses (Free on Skilljar)
All courses at: https://anthropic.skilljar.com

| Course | Relevant Domain(s) |
|---|---|
| Claude 101 | All |
| Building with the Claude API | D1, D3, D5 |
| Claude Code 101 | D2 |
| Claude Code in Action | D2 |
| Introduction to Model Context Protocol | D4 |
| Model Context Protocol: Advanced Topics | D4 |
| Introduction to agent skills | D1 |
| Introduction to subagents | D1 |
| AI Capabilities and Limitations | D5 |

### Official Documentation
- Claude API docs: https://docs.anthropic.com
- Claude Code docs: https://docs.anthropic.com/claude-code
- MCP specification: https://modelcontextprotocol.io
- Agent SDK: https://docs.anthropic.com/claude/agent-sdk

### Third-Party Prep
- [claudecertifications.com exam guide](https://claudecertifications.com/claude-certified-architect/exam-guide)
- [Practice tests – Sundog Education](https://www.sundog-education.com/2026/03/26/practice-tests-for-claude-certified-architect-certification/)
- [Free practice questions – certsafari.com](https://www.certsafari.com/anthropic)

---

## 8-Week Study Plan

### Week 1 — Foundations
- [ ] Read **[STORY.md](./STORY.md)** — all 5 chapters
- [ ] Complete **Claude 101** (Skilljar)
- [ ] Complete **Building with the Claude API** (Skilljar)
- [ ] Read: Claude API docs — Messages, tool_use, structured output
- [ ] Goal: Understand core API patterns, tool_use vs. text output

### Week 2 — Agentic Architecture (Domain 1, Part 1)
- [ ] Complete **Introduction to subagents** (Skilljar)
- [ ] Complete **Introduction to agent skills** (Skilljar)
- [ ] Study: Agentic loops — `stop_reason` values and loop termination logic
- [ ] Study: Session management and task decomposition patterns
- [ ] Re-read Chapter 1 of STORY.md — the $11,400 incident
- [ ] Goal: Build a simple agentic loop from scratch, verify termination logic

### Week 3 — Agentic Architecture (Domain 1, Part 2)
- [ ] Study: Multi-agent orchestration — coordinator/subagent pattern
- [ ] Study: Hub-and-spoke architecture, parallelization strategies
- [ ] Study: Hooks design and programmatic guardrails
- [ ] Practice: Design a multi-agent system for the "Multi-Agent Research System" scenario
- [ ] Goal: Know when to parallelize vs. sequence agents, and when to use hooks vs. prompts

### Week 4 — Claude Code (Domain 2)
- [ ] Complete **Claude Code 101** (Skilljar)
- [ ] Complete **Claude Code in Action** (Skilljar)
- [ ] Study: CLAUDE.md hierarchy (global → project → local)
- [ ] Study: Custom commands, skills, plan mode
- [ ] Study: CI/CD and non-interactive mode
- [ ] Re-read Chapter 2 of STORY.md — the onboarding failure
- [ ] Goal: Configure a real CLAUDE.md for a project with 3 custom commands

### Week 5 — Prompt Engineering & Structured Output (Domain 3)
- [ ] Study: `tool_use` for enforcing structured output
- [ ] Study: JSON schema design patterns — required fields, enums, typed nulls
- [ ] Study: Few-shot prompting — when and how
- [ ] Study: Validation-retry loop pattern
- [ ] Practice: Build a data extraction pipeline with a validation loop
- [ ] Re-read Chapter 3 of STORY.md — the 800 generic replies
- [ ] Goal: Know when structured output fails and how to recover

### Week 6 — Tool Design & MCP Integration (Domain 4)
- [ ] Complete **Introduction to Model Context Protocol** (Skilljar)
- [ ] Complete **Model Context Protocol: Advanced Topics** (Skilljar)
- [ ] Study: MCP server design — transports (stdio, SSE), tool registration
- [ ] Study: Writing high-quality tool descriptions
- [ ] Study: Structured error objects — access failure vs. empty result
- [ ] Practice: Build a simple MCP server with 2–3 tools, including error handling
- [ ] Re-read Chapter 4 of STORY.md — the CRM outage
- [ ] Goal: Understand tool boundaries and why `{}` is the most dangerous return value

### Week 7 — Context Management & Reliability (Domain 5)
- [ ] Complete **AI Capabilities and Limitations** (Skilljar)
- [ ] Study: Context window positioning — what to put where and why
- [ ] Study: Progressive summarization risks and mitigation
- [ ] Study: Escalation patterns — complexity-based vs. sentiment-based
- [ ] Study: Error propagation in multi-hop agent chains
- [ ] Study: Human-in-the-loop triggers and information provenance
- [ ] Re-read Chapter 5 of STORY.md — the context drift incident
- [ ] Goal: Design a reliable context management strategy for a long-thread scenario

### Week 8 — Review & Practice Exams
- [ ] Review all 6 exam scenarios end-to-end
- [ ] Drill anti-patterns cheatsheet (see below)
- [ ] Complete practice tests (Sundog, certsafari)
- [ ] Focus extra time on weakest domain
- [ ] Simulate timed exam: 60 questions in 120 minutes
- [ ] Goal: Score 750+ on practice exams before sitting the real one

---

## Critical Anti-Patterns (High-Value for Exam)

These are the "trap" wrong answers — they represent mistakes engineers actually make when they understand the concepts but haven't thought through the production implications.

| Anti-Pattern | Correct Approach | Story Chapter |
|---|---|---|
| Parse natural language to end loops | Check `stop_reason` programmatically | Chapter 1 |
| Prompt-based rule enforcement | Use hooks | Chapter 1 |
| Sentiment-based escalation | Task-complexity criteria | Chapter 1 |
| Generic error messages from tools | Structured error objects with typed failure modes | Chapter 4 |
| Suppress tool errors silently | Distinguish access failures from empty results | Chapter 4 |
| Ask model to output raw JSON in text | Use `tool_use` to enforce structure | Chapter 3 |
| Summarise all context to save tokens | Pin critical facts; preserve commitments verbatim | Chapter 5 |
| Trust model to self-police output format | Schema validation + retry loop | Chapter 3 |

---

## Prerequisites

- 6+ months hands-on production experience with Claude API
- Familiarity with Claude Code
- Some MCP exposure recommended
- Python or TypeScript for API/SDK work

---

## Notes
- Exam launched: March 12, 2026
- Currently available through the **Claude Partner Network** (free to join)
- Credential stack will expand through 2026 (Foundations is the entry point)
- Exam is proctored — no Claude, no external tools, no documentation during the test

# Week 4 Lab — Claude Code Configuration & Workflows

> **Resolve context:** When Jade was the only person who understood the AI codebase, Resolve had a bus problem. When the third engineer couldn't set up her environment after two days, Sofia called it a culture problem. Both were actually a documentation problem — the kind that a well-structured CLAUDE.md hierarchy and a set of custom commands fixes permanently. This week you build that infrastructure.

## Learning Objectives

- Understand the three-level CLAUDE.md hierarchy and what each level is responsible for
- Write CLAUDE.md files that meaningfully constrain Claude Code's behaviour — not just describe the project
- Create custom slash commands that replace procedures that previously lived in someone's head
- Configure and use plan mode for complex, multi-file changes
- Run Claude Code in non-interactive mode for CI/CD integration
- Understand what the exam means by "Claude Code for CI/CD" scenario

## Prerequisites

- Claude Code installed (`npm install -g @anthropic-ai/claude-code` or via desktop app)
- A small working project to configure — use the agent code from Weeks 2–3 or any Python/TypeScript project
- Anthropic SDK installed

**Note:** Domain 2 is the most configuration-dependent domain on the exam. The questions test whether you know exactly where files go, what each level of the hierarchy controls, and what non-interactive mode changes about Claude Code's behaviour. There is no shortcut — you need to have configured a real project.

> **Context management note:** Exam Scenario 2 (*Code Generation with Claude Code*) lists **Context Management & Reliability** as a co-primary domain alongside Domain 2. A CLAUDE.md hierarchy is itself a context management tool — it defines what Claude Code knows about the project before any conversation starts. As you build exercises this week, consider each CLAUDE.md instruction as context you are pinning for Claude Code's attention, not just documentation for human readers.

---

## Exercises

### Exercise 1 — The Three-Level CLAUDE.md Hierarchy

**Goal:** Understand the scope and override rules of global, project, and subdirectory CLAUDE.md files by building all three for the Resolve agent project.

**Scenario:** Resolve's codebase has three distinct concerns: the agent runtime, the evaluation harness, and the CI scripts. Each has different Claude Code behaviours that make sense in context but would be wrong everywhere else. The hierarchy makes this possible.

**You will:**
1. Create `~/.claude/CLAUDE.md` (global): add your personal coding preferences — language defaults, comment style, test framework preference. These apply to every project.
2. Create `CLAUDE.md` at the project root: describe the Resolve architecture, which files control which behaviour, the output schema, and what Claude Code should never change without asking
3. Create `agents/CLAUDE.md`: describe the agent loop state machine in detail — every field in `SessionState`, the exit codes, the tool call budget
4. Create `evals/CLAUDE.md`: explain how evaluation output is structured and what a passing vs. failing eval looks like
5. Make a change to a file in `agents/` and observe which CLAUDE.md files Claude Code reads — verify subdirectory instructions override project-level instructions

**Key insight:** The hierarchy is additive, not exclusive — subdirectory files add to or override the project root, which adds to or overrides the global. Understanding which instructions are visible when is what the exam tests.

---

### Exercise 2 — Writing Effective CLAUDE.md Content

**Goal:** Write CLAUDE.md instructions that meaningfully change Claude Code's behaviour — not just describe what the project does.

**Scenario:** Jade's first CLAUDE.md described the architecture in prose. It was accurate but useless — Claude Code would read it and then make the same mistakes anyway. Effective CLAUDE.md files constrain behaviour, not just describe it.

**You will:**
1. Write a section that defines what Claude Code must do before modifying any file in `agents/`: read `SessionState`, check the output schema, verify the iteration budget is unchanged
2. Write a section that defines what Claude Code must never do: change `max_iterations` without explicit confirmation, add new tools without updating the tool description guide, modify the message history structure
3. Write a section describing the project's custom commands (you will build these in Exercise 3) so Claude Code knows what they do and when to suggest them
4. Test each instruction by making a request that should trigger a constraint and verifying Claude Code asks for confirmation

**Key insight:** "Never do X" instructions in CLAUDE.md are enforced through Claude Code's behaviour, not programmatically. They must be specific enough to be unambiguous. "Be careful with database code" is not an instruction — "Do not modify the message history structure without explicit confirmation" is.

---

### Exercise 3 — Custom Slash Commands

**Goal:** Build three custom commands that replace the procedures previously explained in onboarding calls.

**Scenario:** Jade's onboarding calls covered three recurring procedures: running a synthetic ticket through the agent, validating that the output schema matches the current definition, and doing a dry-run deploy against a staging ticket batch. These are now custom commands — discoverable, versioned, and runnable by any engineer.

**You will:**
1. Create `/test-ticket` in `.claude/commands/test-ticket.md`: a command that runs a synthetic ticket through the full agent pipeline, prints the decision path step by step, and reports the exit reason
2. Create `/validate-schema` in `.claude/commands/validate-schema.md`: a command that reads the current `output_schema.json`, runs 10 synthetic tickets through the agent, and checks every output against the schema — failing loudly on any mismatch
3. Create `/dry-run-deploy` in `.claude/commands/dry-run-deploy.md`: a command that runs the agent in non-interactive mode against the staging ticket batch and diffs the output against the last known-good run
4. Use each command and verify it produces the expected output
5. Document each command in the project-root CLAUDE.md so new engineers can discover them

**Key insight:** Custom commands are markdown files with instructions. They are not code. The power is in making complex, multi-step procedures discoverable and reproducible without requiring the engineer who wrote them to be present.

---

### Exercise 4 — Plan Mode for Complex Changes

**Goal:** Use plan mode to design a multi-file change before executing it, and understand when plan mode prevents mistakes.

**Scenario:** Arnaud wants to add a new tool (`get_policy_details`) to the resolution agent. This requires changes in four files: the tool definition, the `SessionState` schema, the coordinator routing logic, and the CLAUDE.md tool guide. Doing this without a plan tends to miss the fourth file.

**You will:**
1. Use plan mode (`/plan`) to design the addition of `get_policy_details` before writing any code — verify that Claude Code identifies all four files that need changing
2. Review the plan and add a constraint: the tool must follow the three-response-shape pattern from Exercise 4 of Week 1
3. Execute the plan step by step, approving each file change individually
4. Compare the outcome with a version done without plan mode — identify which files plan mode would have caught that a direct prompt would have missed

**Key insight:** Plan mode forces explicit enumeration of every file that will change before any file changes. For multi-file changes in an agent codebase — where a missing update in one file causes a silent behaviour change — this is not optional.

---

### Exercise 5 — Non-Interactive Mode for CI/CD

**Goal:** Run Claude Code in non-interactive mode and understand what changes about its behaviour — and why this matters for the "Claude Code for CI/CD" exam scenario.

**Scenario:** Resolve's CI pipeline validates every PR by running the agent against a synthetic ticket batch and checking that output schemas are valid. No human is watching. Claude Code must complete without prompting for input, without making open-ended decisions, and without writing anything that wasn't explicitly requested.

**You will:**
1. Run Claude Code with `--no-interactive` on a task that would normally prompt for confirmation — observe what happens
2. Create a CI script (GitHub Actions or bash) that: runs `/validate-schema` in non-interactive mode, exits with code 1 if any schema validation fails, and produces structured JSON output summarising the results
3. Run the script against the agent code with one intentional schema violation — verify the script catches it and exits non-zero
4. Understand the difference between non-interactive mode and headless mode — what each disables and when to use each

**Key insight:** Non-interactive mode is not "Claude Code but faster." It changes which decisions Claude Code will make autonomously vs. abort on. A CI script that runs Claude Code in interactive mode will hang waiting for input that never comes.

---

## Lab Completion Checklist

Before moving to Week 5, answer these without looking:

- [ ] List the three levels of the CLAUDE.md hierarchy in order of precedence (most specific wins)
- [ ] Where do custom slash commands live in the project directory?
- [ ] What does plan mode do that a direct prompt does not?
- [ ] What happens when Claude Code encounters a decision point in non-interactive mode?
- [ ] Why is "be careful with database code" not a useful CLAUDE.md instruction?
- [ ] Name two things the `/validate-schema` command should do that a human code reviewer might miss

---

## Exam Connections

| Exercise | Domain | Exam Pattern Covered |
|---|---|---|
| 1 | D2 | Three-level hierarchy; scope and override rules |
| 2 | D2 | Effective constraint instructions vs. descriptive prose |
| 3 | D2 | Custom commands; discoverability; replacing tribal knowledge |
| 4 | D2 | Plan mode for multi-file changes; explicit change enumeration |
| 5 | D2 | Non-interactive mode; CI/CD integration; exit codes |

---

## What's Next

Week 5 goes deep on Domain 3 — the difference between asking for structured output and requiring it, JSON schema design that prevents hallucinations, and building a validation-retry loop that escalates correctly.

→ **[Week 5 Lab — Prompt Engineering & Structured Output](../week-5-prompt-engineering/README.md)**

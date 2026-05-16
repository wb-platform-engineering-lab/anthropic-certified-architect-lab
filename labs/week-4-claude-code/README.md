# Week 4 Lab — Claude Code Configuration & Workflows

> **Resolve context:** When Jade was the only person who understood the AI codebase, Resolve had a bus problem. When the third engineer couldn't set up her environment after two days, Sofia called it a culture problem. Both were actually a documentation problem — the kind that a well-structured CLAUDE.md hierarchy and a set of custom commands fixes permanently. This week you build that infrastructure.

## Learning Objectives

- Understand the three-level CLAUDE.md hierarchy and what each level is responsible for
- Write CLAUDE.md files that meaningfully constrain Claude Code's behaviour — not just describe the project
- Create custom slash commands that replace procedures that previously lived in someone's head
- Configure and use plan mode for complex, multi-file changes
- Run Claude Code in non-interactive mode for CI/CD integration
- Apply file-path-conditional conventions using `.claude/rules/` with glob patterns
- Know exactly when to use the Message Batches API versus the real-time API

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

#### The hierarchy

```
~/.claude/CLAUDE.md          ← Level 1: global (your machine, all projects)
    │
    └── project/CLAUDE.md    ← Level 2: project root (this repo)
            │
            └── agents/CLAUDE.md    ← Level 3: subdirectory (agents/ only)
```

Each level **adds to** the levels above it. Subdirectory files take precedence where they conflict with the project root. Project root takes precedence over global.

#### What goes at each level

**Level 1 — global `~/.claude/CLAUDE.md`**

Personal preferences that apply to every project you work on. This is not committed to any repo. Example:

```markdown
# Personal Claude Code Defaults

- Default language: Python 3.11
- Comment style: inline only, no docstrings unless the function is public API
- Test framework: pytest
- Never add print statements to production code without asking
```

**Level 2 — project root `CLAUDE.md`**

Architecture overview and project-wide constraints. Committed to the repo. See the `CLAUDE.md` at the root of `labs/week-4-claude-code/` for a full example:

```markdown
# Resolve — Project-Level Claude Code Configuration

## Architecture

Resolve uses a coordinator-subagent pattern. A coordinator receives an inbound
support ticket, classifies it, and dispatches specialised subagents.

## Constraints — What Claude Code Must NEVER Do Without Explicit Confirmation

1. Change `max_iterations` in any agent loop.
2. Modify the message history structure.
3. Add a new tool without updating `output_schema.json` and `agents/CLAUDE.md`.
4. Change the `status` field values in tool return dicts.
5. Modify `evals/run_evals.py` without running the eval suite first.
```

**Level 3 — subdirectory `agents/CLAUDE.md`**

Detail that is only meaningful inside `agents/`. Claude Code loads this automatically when you work on any file under `agents/`. See `labs/week-4-claude-code/agents/CLAUDE.md` for the full content:

```markdown
# Resolve Agents — Subdirectory Claude Code Configuration

## Iteration Budgets

| Agent         | max_iterations |
|---------------|---------------|
| Coordinator   | 8             |
| AccountAgent  | 5             |
| BillingAgent  | 5             |
| IncidentAgent | 3             |

## Tool Call Contract

Every tool must return a dict with a `status` field.
Valid values: "success" | "access_failure" | "empty"
Never raise an exception out of a tool.
```

#### What to observe

Open `agents/CLAUDE.md` in a Claude Code session and ask: *"What are the iteration budgets for each agent?"* Claude Code should answer from `agents/CLAUDE.md` without you specifying where to look. It reads the subdirectory file because you are working inside `agents/`.

Now ask the same question from the project root. Claude Code will answer from the project-root `CLAUDE.md` constraint list, which references `agents/CLAUDE.md` but does not repeat the budget values. This is the hierarchy working: detail lives where it is needed, not everywhere.

#### Questions to answer before moving on

1. If `~/.claude/CLAUDE.md` says "use 2-space indentation" and `agents/CLAUDE.md` says "use 4-space indentation", which wins for files in `agents/`?
2. Which CLAUDE.md level is NOT committed to version control?
3. What is the difference between a CLAUDE.md file that *describes* the project and one that *constrains* Claude Code's behaviour?

#### Try it

Create `~/.claude/CLAUDE.md` with one preference that is specific to you (a language default, a comment style rule, anything). Then open this project in Claude Code and ask: *"What are your current coding preferences?"* Verify that Claude Code names your global preference alongside the project-level constraints.

#### Exam rule

> The three levels are: **global** (`~/.claude/CLAUDE.md`), **project root** (`CLAUDE.md`), **subdirectory** (`<dir>/CLAUDE.md`). Subdirectory wins over project root, which wins over global. All three files are loaded simultaneously — lower levels add detail or override, they do not replace. Only the global file is not committed to version control.

---

### Exercise 2 — Writing Effective CLAUDE.md Content

**Goal:** Write CLAUDE.md instructions that meaningfully change Claude Code's behaviour — not just describe what the project does.

**Scenario:** Jade's first CLAUDE.md described the architecture in prose. It was accurate but useless — Claude Code would read it and then make the same mistakes anyway. Effective CLAUDE.md files constrain behaviour, not just describe it.

#### Descriptive prose vs. behavioural constraints

**Ineffective (descriptive prose):**

```markdown
## Agents

The coordinator handles ticket routing. The subagents handle domain-specific
lookups. Be careful when modifying agent code because changes can break things.
```

Claude Code reads this and proceeds anyway. "Be careful" is not an instruction.

**Effective (behavioural constraint):**

```markdown
## Before Modifying Anything in `agents/`

Complete all three steps before touching any file under `agents/`:

1. Read `agents/session_state.py` — confirm which fields exist and their types.
   If you add logic that writes to a field that does not exist in `SessionState`,
   the change will fail at runtime.

2. Check `output_schema.json` — confirm the expected output shape for the agent
   or tool you are modifying.

3. Verify the iteration budget is unchanged — open `agents/coordinator.py` and
   check `max_iterations`. Do not proceed if it differs from the value in
   `agents/CLAUDE.md`.

## Constraints — What Claude Code Must NEVER Do Without Explicit Confirmation

1. **Change `max_iterations` in any agent loop.**
   The budgets were set after measuring the eval suite. Changing them silently
   breaks cost guarantees.

2. **Modify the message history structure.**
   The Claude API requires strict assistant → tool_result alternation.
   Any deviation produces a 400 error at runtime.

3. **Add a new tool without updating `output_schema.json` and `agents/CLAUDE.md`.**
   Adding a tool to only one place causes silent schema drift.
```

#### What to observe

With the effective version in `CLAUDE.md`, try asking Claude Code: *"Increase `max_iterations` from 8 to 12 in coordinator.py."*

It should pause and say something like: *"This file's CLAUDE.md requires explicit confirmation before changing `max_iterations`. The current value is 8. Do you want me to proceed?"* It will not make the change silently.

Without the CLAUDE.md constraint, the same request results in an immediate edit.

#### Questions to answer before moving on

1. What is the difference between *"be careful with database code"* and *"do not modify the database schema without explicit confirmation"* as CLAUDE.md instructions?
2. Why does the pre-flight checklist (read `SessionState`, check schema, verify budget) work better as a numbered list than as a paragraph?
3. What happens if two CLAUDE.md files have contradictory instructions? Which takes precedence?

#### Try it

Add a constraint to your project CLAUDE.md: *"Never add a new Python dependency without first checking `requirements.txt` to confirm the package is not already listed."*

Then ask Claude Code to add a package that is already in `requirements.txt`. Verify that it reads the file first and reports the package is already present rather than adding a duplicate entry.

#### Exam rule

> CLAUDE.md instructions that change Claude Code's behaviour must be **specific and actionable**: they name a file, a field, a value, or a step. Instructions that describe the project or say "be careful" do not constrain behaviour and will be ignored.

---

### Exercise 3 — Custom Slash Commands

**Goal:** Build three custom commands that replace the procedures previously explained in onboarding calls.

**Scenario:** Jade's onboarding calls covered three recurring procedures: running a synthetic ticket through the agent, validating that the output schema matches the current definition, and doing a dry-run deploy against a staging ticket batch. These are now custom commands — discoverable, versioned, and runnable by any engineer.

#### Where custom commands live

```
project/
└── .claude/
    └── commands/
        ├── test-ticket.md        ← /test-ticket command
        ├── validate-schema.md    ← /validate-schema command
        └── dry-run-deploy.md     ← /dry-run-deploy command
```

Each file is a markdown document. The filename (without `.md`) becomes the slash command name. The content is the instruction set Claude Code follows when the command is invoked.

#### Command file structure

The `test-ticket.md` command (see `.claude/commands/test-ticket.md`):

```markdown
# /test-ticket

Run a synthetic support ticket through the full Resolve agent pipeline — coordinator
classification, subagent dispatch, and final reply assembly.

**Arguments:**
- `--customer-id <id>` — Customer account identifier (default: `cust_9182`)
- `--ticket <text>` — Ticket body text

## Instructions

1. Parse arguments from the command invocation.
2. Confirm `agents/coordinator.py` and `agents/session_state.py` exist.
3. Print the test parameters before running.
4. Run: `python -c "from agents.coordinator import run_coordinator; ..."`
5. Print classification result.
6. Print per-subagent status with iterations used.
7. Print HookViolations if any fired.
8. Print the final exit reason and reply.
9. Print a final PASS or FAIL verdict.
```

The numbered instruction list is important: Claude Code follows it step by step. Commands without numbered steps are harder for Claude Code to execute consistently across invocations.

#### The three commands for this project

**`/test-ticket`** — End-to-end pipeline run with per-stage output:

```
/test-ticket --customer-id cust_4471 --ticket "I was charged twice for my Pro plan."
```

Expected output structure:
```
── Test Ticket Run ────────────────────────────────────────
Customer ID : cust_4471
Ticket      : I was charged twice for my Pro plan...
──────────────────────────────────────────────────────────
Classification : billing
[BillingAgent]  exit=success  iterations=3/5
No hook violations.

── Result ─────────────────────────────────────────────────
Exit reason : success
Reply       : The duplicate charge has been identified...
──────────────────────────────────────────────────────────
PASS
```

**`/validate-schema`** — Schema conformance across 10 synthetic tickets:

```
/validate-schema
```

Expected output: a PASS/FAIL per field per ticket, then a summary count.

**`/dry-run-deploy`** — Non-interactive run against the staging batch, diffed against the last known-good run:

```
/dry-run-deploy
```

Uses `claude -p` internally. Reports any ticket whose exit reason regressed (previously `success`, now `budget_exhausted` or `error`).

#### What to observe

After creating the command files, type `/test-ticket` in a Claude Code session in this project directory. Claude Code should discover the command automatically — it does not need to be registered anywhere else. The `.claude/commands/` directory is the complete registration mechanism.

Try invoking a command with and without arguments. Verify that defaults work when arguments are omitted.

#### Questions to answer before moving on

1. Where must command files live relative to the project root?
2. What determines the name of the slash command — the filename, the `# Header`, or a registration step?
3. Why does the instruction list in a command file need to be numbered rather than bulleted?
4. Can a custom command use arguments? How are they passed?

#### Try it

Create a fourth command: `/check-budgets`. Instructions:
1. Read `agents/coordinator.py` and print the current `max_iterations` value.
2. Read `agents/subagents.py` and print the per-agent budgets.
3. Compare against the expected values in `agents/CLAUDE.md`.
4. Print PASS if all match, FAIL with a diff if any diverge.

This command makes the "verify the iteration budget is unchanged" pre-flight step from Exercise 2 executable in one keystroke.

#### Exam rule

> Custom slash commands live in `.claude/commands/<name>.md`. The filename is the command name. The file content is a markdown instruction set. No registration is required beyond placing the file in that directory. Commands can accept arguments; the argument values are substituted into the instruction text when the command is invoked.

---

### Exercise 4 — Plan Mode for Complex Changes

**Goal:** Use plan mode to design a multi-file change before executing it, and understand when plan mode prevents mistakes.

**Scenario:** Arnaud wants to add a new tool (`get_policy_details`) to the resolution agent. This requires changes in four files: the tool definition, `SessionState`, the coordinator routing logic, and `agents/CLAUDE.md`. Without a plan, engineers consistently miss the fourth file.

#### What plan mode does

Without plan mode, a prompt like *"Add a `get_policy_details` tool to the BillingAgent"* results in immediate file edits. Claude Code writes the tool, maybe updates the coordinator, and stops. The CLAUDE.md update and the `output_schema.json` update are missed.

With plan mode (`/plan` or `Shift+Tab` to toggle), Claude Code:
1. Enumerates every file it intends to change
2. Describes what change it will make to each file
3. Waits for your approval before writing anything

#### Using plan mode

Activate it before making a multi-file request:

```
/plan

Add a `get_policy_details` tool to the BillingAgent. The tool should accept a
policy_id and return the plan name, renewal date, and monthly cost. It must
follow the three-status return pattern (success | access_failure | empty).
```

Claude Code should produce a plan like:

```
Files that will change:

1. agents/subagents.py
   Add `get_policy_details(policy_id)` tool function with the three-status
   return pattern. Register it in BillingAgent's tool list.

2. agents/session_state.py
   Add `policy_facts: dict` field to SessionState. BillingAgent will populate
   this field after calling get_policy_details.

3. output_schema.json
   Add the `get_policy_details` tool schema with expected return fields:
   plan_name (str), renewal_date (str), monthly_cost (float).

4. agents/CLAUDE.md
   Add `get_policy_details` to the Tool Call Contract section with its
   return shape and the field it writes to in SessionState.

Proceed with all four changes? (yes / modify plan / cancel)
```

Approve the plan, then Claude Code executes all four changes in sequence.

#### What to observe

Run the same request without plan mode first. Count how many of the four files are modified. Then run with plan mode. The plan should identify all four. The difference is that plan mode makes the complete change set visible before any file is written.

This matters most for agent codebases because a missing update in one file (like `output_schema.json`) does not cause an immediate error — it causes silent schema drift that only surfaces in the eval harness later.

#### Questions to answer before moving on

1. What does Claude Code do differently in plan mode compared to a direct prompt?
2. At what point in plan mode can you add a constraint or modify the plan?
3. For what category of change is plan mode most valuable — single-file changes, multi-file changes, or both? Why?
4. If Claude Code's plan misses a file you know needs changing, what do you do?

#### Try it

Use plan mode to design (but not execute) the removal of the `IncidentAgent`. List every file that would need to change if `IncidentAgent` were deleted. Compare Claude Code's plan against your own list.

#### Exam rule

> Plan mode produces an enumeration of all files that will change before any file changes. For multi-file agentic changes — where a missed update causes silent drift rather than an immediate error — plan mode is the right tool. It is activated with `/plan` or the `Shift+Tab` toggle.

---

### Exercise 5 — Non-Interactive Mode for CI/CD

**Goal:** Run Claude Code in non-interactive mode and understand what changes about its behaviour — and why this matters for the "Claude Code for CI/CD" exam scenario.

**Scenario:** Resolve's CI pipeline validates every PR by running a schema violation check. No human is watching. Claude Code must complete without prompting for input and exit with a meaningful exit code.

#### The correct flag: `-p`

```bash
claude -p "Analyse agents/ for schema violations. Output PASS or FAIL: <description>."
```

`-p` (or `--print`) tells Claude Code to:
1. Accept the prompt as a command-line argument
2. Process it once
3. Print the response to stdout
4. Exit — it does **not** wait for user input

**The wrong flags — they do not exist:**

| Flag | Status |
|------|--------|
| `--no-interactive` | Does not exist |
| `--headless` | Does not exist |
| `--batch` | Does not exist |
| `CLAUDE_HEADLESS=true` | Not a valid env var |

Any exam option referencing these flags is a distractor. There is only one flag for non-interactive mode: `-p`.

#### The CI script (`exercise_5_ci_script.sh`)

```bash
#!/usr/bin/env bash
set -euo pipefail

# Capture claude -p output. Non-zero exit → CLAUDE_FAILED=true.
CLAUDE_FAILED=false
CLAUDE_OUTPUT=""

if ! CLAUDE_OUTPUT=$(claude -p \
    "Analyse the agent code in agents/ for schema violations.
Check that every tool returns a dict with a 'status' field set to one of:
success, access_failure, empty.
Check that no tool raises an exception.
Output ONLY:
  PASS
if no violations found, or one line per violation:
  FAIL: <filename>: <description>
No other output. No markdown." 2>&1); then
    CLAUDE_FAILED=true
fi

# Count FAIL lines
FAIL_COUNT=0
while IFS= read -r line; do
    if [[ "${line}" == FAIL:* ]]; then
        FAIL_COUNT=$((FAIL_COUNT + 1))
    fi
done <<< "${CLAUDE_OUTPUT}"

# Exit with appropriate code
if [ "${FAIL_COUNT}" -gt 0 ]; then
    echo "FAIL — ${FAIL_COUNT} schema violation(s) found."
    exit 1
else
    echo "PASS — no schema violations found."
    exit 0
fi
```

Three things to note:
- `claude -p` with a structured prompt — no prose, no markdown, just `PASS` or `FAIL:` lines
- Stdout is parsed mechanically — any extra output from Claude Code breaks the parser
- Exit code drives the CI gate: 0 = pass, 1 = fail

#### Chaining multiple `-p` calls

`-p` is single-turn: one prompt in, one response out, then Claude exits. For multi-step CI workflows, chain calls via temp files:

```bash
# Step 1: analyse
STEP_1_OUTPUT=$(mktemp)
claude -p "Analyse agents/coordinator.py for budget violations.
Output a JSON array: [{file, line, description}].
Output only valid JSON." > "${STEP_1_OUTPUT}"

# Step 2: generate fix plan using step 1's output
VIOLATIONS=$(cat "${STEP_1_OUTPUT}")
claude -p "Given these violations: ${VIOLATIONS}
For each, output one line: <file>:<line>: <fix description>." > step2_output.txt

rm -f "${STEP_1_OUTPUT}"
```

Each `claude -p` call is independent — no shared context between calls. Pass output explicitly via files or environment variables.

#### What to observe

Run the script against the agent code as-is (no intentional violations). Verify exit code 0 and `PASS` output. Then introduce a deliberate violation — make a tool function raise an exception instead of returning a status dict. Re-run the script. Verify exit code 1 and a `FAIL:` line in the output.

#### Questions to answer before moving on

1. What does `-p` stand for and what does it change about Claude Code's behaviour?
2. Name three flags that look plausible but do not exist.
3. Why must the prompt in a CI `claude -p` call request structured output with no prose?
4. Why can't a single `claude -p` call handle a multi-step CI workflow?

#### Try it

Add a second `claude -p` step to the CI script that takes the violations found in step 1 and generates a one-line fix suggestion for each. Print the suggestions after the FAIL output. Verify that the fix suggestions reference the correct file names.

#### Exam rule

> The correct flag for non-interactive Claude Code is **`-p`** (or `--print`). `--no-interactive`, `--headless`, `--batch`, and `CLAUDE_HEADLESS=true` do not exist. Exam Q10's wrong answers all reference these non-existent flags. `-p` is single-turn — for multi-step CI, chain multiple `claude -p` calls via temp files.

---

### Exercise 6 — `.claude/rules/` with Glob Patterns

**Goal:** Use `.claude/rules/` with YAML frontmatter to apply different conventions automatically based on file paths — including files spread across the codebase that cannot be covered by a directory-level CLAUDE.md.

**Scenario:** Exam Scenario 2 Q6 tests this exactly. Resolve's codebase has three convention sets: React components use functional style with hooks, API handlers use async/await with specific error handling, and all test files follow a strict mock isolation pattern regardless of where they live. A test file in `components/` must follow the same conventions as one in `api/`.

#### Why `.claude/rules/` exists

A `CLAUDE.md` in `src/components/` applies to all files in that directory. It cannot reach test files in `src/api/`. And it applies to non-test files — so you cannot add test-specific rules there without polluting the component rules.

`.claude/rules/` solves this with YAML glob frontmatter:

```
project/
└── .claude/
    └── rules/
        ├── react-components.md    ← applies to src/components/**/*.tsx
        ├── api-handlers.md        ← applies to src/api/**/*.ts
        └── test-files.md          ← applies to **/*.test.ts, **/*.spec.ts
```

Each rule file has a YAML frontmatter block that specifies which files it applies to:

```yaml
---
globs: ["src/components/**/*.tsx"]
---
```

Claude Code loads the rule file automatically when you open or edit a file whose path matches the glob pattern.

#### The three rule files

**`.claude/rules/react-components.md`** — applies to `src/components/**/*.tsx`:

```markdown
---
globs: ["src/components/**/*.tsx"]
---

# React Component Conventions

- Use functional components only. No class components.
- Define props with an explicit interface named `<ComponentName>Props`.
- Destructure props in the function signature.
- Use hooks (useState, useEffect) rather than lifecycle equivalents.
- useEffect must always specify a dependency array.
- Use named exports, not default exports.
- One component per file. Filename matches component name.
```

**`.claude/rules/api-handlers.md`** — applies to `src/api/**/*.ts`:

```markdown
---
globs: ["src/api/**/*.ts"]
---

# API Handler Conventions

- All handlers must be async and return Promise<Response>.
- Never use .then()/.catch() chains — use await with try/catch.
- Every handler must have a top-level try/catch block.
- Errors are returned as typed responses, not thrown.
- Every response includes a status field: success | validation_error |
  not_found | internal_error.
```

**`.claude/rules/test-files.md`** — applies to `**/*.test.ts` and `**/*.spec.ts` everywhere:

```markdown
---
globs: ["**/*.test.ts", "**/*.spec.ts", "**/*.test.tsx", "**/*.spec.tsx"]
---

# Test File Conventions

These rules apply to ALL test files anywhere in the codebase — identified
by suffix, regardless of directory.

- Mock all external dependencies at the top of the file.
- Use beforeEach(() => { jest.resetAllMocks(); }) in every describe block.
- One describe block per file, named after the module under test.
- Test names must be complete sentences.
- Arrange / Act / Assert order within every test.
- Every test must have at least one expect().
```

#### The key distinction

`src/components/Button.test.tsx` gets:
- **Test file rules** (from `test-files.md` glob `**/*.test.tsx`) ✓
- **React component rules** (from `react-components.md` glob `src/components/**/*.tsx`) — does this apply?

The test file glob `**/*.test.tsx` matches. The React component glob `src/components/**/*.tsx` also matches. Both rule files load. Claude Code sees both sets of rules for this file.

This is intentional: a test file for a React component should follow test conventions (mocking, assertion style) AND React conventions where relevant (not convert functional components to class components in test setup code).

The scenario that only a glob rule can solve: apply test conventions to `src/components/Button.test.tsx` AND `src/api/billing.test.ts`. A `CLAUDE.md` in `src/components/` cannot reach `src/api/`. A `CLAUDE.md` in `src/` would apply to all files, not just test files. Only a glob on `**/*.test.ts` solves this.

#### What to observe

Open `src/components/Button.tsx` in Claude Code. Ask it to add a prop. Verify it uses a named export, a `ButtonProps` interface, and functional component syntax — driven by the glob rule.

Open `src/api/billing.test.ts`. Ask it to add a test case. Verify it adds `jest.resetAllMocks()` in `beforeEach` and uses a complete-sentence test name — driven by the test-files glob rule.

#### Questions to answer before moving on

1. What is the mechanism that makes a `.claude/rules/` file apply to a specific set of files?
2. Why can't a `CLAUDE.md` in `src/components/` apply test conventions to `src/api/` test files?
3. If a file matches two glob patterns, which rule file wins?
4. What is the exam's wrong answer for "how do you apply test conventions to test files everywhere"?

#### Try it

Add a fourth rule file: `.claude/rules/migration-files.md` with glob `["**/migrations/**/*.sql"]`. Add the rule: *"Migration files must begin with a comment containing the migration number and author."* Open a file that matches the glob and ask Claude Code to create a new migration. Verify it adds the required comment header.

#### Exam rule

> `.claude/rules/<name>.md` with `globs: [...]` YAML frontmatter is the only mechanism in Claude Code for applying conventions based on file path patterns. Use it when the same convention must apply to files across multiple directories (like all test files regardless of location). A `CLAUDE.md` is directory-scoped — it cannot match files by path pattern across different directories.

---

### Exercise 7 — Message Batches API: Blocking vs. Async Workflows

**Goal:** Know when the Message Batches API is appropriate and when it is not — Exam Scenario 5 Q11 tests this with a 50% cost savings offer that is only partially correct to accept.

**Scenario:** Resolve's CI pipeline has two Claude-powered workflows: (1) a pre-merge security check that developers wait for before merging, and (2) an overnight technical debt report generated at 2 AM for engineers to review the next morning. The team proposes switching both to the Message Batches API for 50% cost savings.

#### The rule

```
Batches API: async, results within 24 hours, 50% cost reduction
             ↓
Use when: nobody is waiting for the result RIGHT NOW
             (overnight jobs, weekly reports, background classification)

Real-time API: synchronous, returns in seconds
             ↓
Use when: a human or system is blocking on the result
             (pre-merge checks, interactive agents, synchronous CI pipelines)
```

The 50% cost saving is real. But it comes with **no latency SLA**. "Often completes in minutes" is not a service level agreement you can give a developer who is blocking a merge.

#### Workflow A — WRONG: pre-merge check via Batches API

From `exercise_7_batches_api.py`:

```python
def wrong_workflow_a_pre_merge_check(pr_diff: str) -> None:
    """WRONG for this use case. The code runs without error — that is not the point."""

    batch = client.beta.messages.batches.create(
        requests=[{
            "custom_id": "pre-merge-security-check",
            "params": {
                "model": "claude-opus-4-5",
                "max_tokens": 512,
                "messages": [{
                    "role": "user",
                    "content": f"Review this PR diff for security issues.\n\n{pr_diff}\n\nOutput: PASS or FAIL: <description>."
                }],
            },
        }]
    )

    print(f"Batch submitted: {batch.id}")
    print(f"Status: {batch.processing_status}")
    # WHY THIS IS WRONG:
    # The developer is blocked on this result before merging.
    # "Often completes in minutes" is not an SLA.
    # If the batch takes 2 hours, the PR is blocked for 2 hours.
```

The Batches API accepts the request without error. The problem is operational: there is no guarantee the result arrives in time for the developer's workflow.

#### Workflow B — CORRECT: overnight report via Batches API

```python
def submit_overnight_batch() -> str:
    """Called at 2 AM by a scheduled CI job."""

    requests = [
        {
            "custom_id": file_info["custom_id"],   # correlation key
            "params": {
                "model": "claude-opus-4-5",
                "max_tokens": 1024,
                "messages": [{
                    "role": "user",
                    "content": build_analysis_prompt(file_info),
                }],
            },
        }
        for file_info in FILES_TO_ANALYSE
    ]

    batch = client.beta.messages.batches.create(requests=requests)
    print(f"Batch submitted: {batch.id}")
    return batch.id


def poll_until_complete(batch_id: str, poll_interval_seconds: int = 5) -> None:
    """Morning retrieval step: poll until processing_status == 'ended'."""
    while True:
        batch = client.beta.messages.batches.retrieve(batch_id)
        if batch.processing_status == "ended":
            break
        time.sleep(poll_interval_seconds)


def retrieve_and_print_results(batch_id: str) -> list[dict]:
    """Correlate results back to source files using custom_id."""

    # Build a lookup from custom_id to file info
    file_lookup = {f["custom_id"]: f for f in FILES_TO_ANALYSE}

    results = []
    for result in client.beta.messages.batches.results(batch_id):
        custom_id = result.custom_id
        file_info = file_lookup.get(custom_id)   # correlation

        if result.result.type == "succeeded":
            raw = result.result.message.content[0].text
            results.append({
                "custom_id": custom_id,
                "file": file_info["path"],
                "analysis": json.loads(raw),
                "status": "success",
            })
        elif result.result.type == "errored":
            results.append({
                "custom_id": custom_id,
                "file": file_info["path"],
                "analysis": None,
                "status": "api_error",
            })
    return results
```

WHY THIS IS CORRECT:
- Engineers are asleep. No one is waiting for this result.
- The 24-hour result window fits within the overnight schedule.
- 50% cost reduction applies to all 5 analysis requests.
- `custom_id` lets us correlate results back to specific files.

#### The `custom_id` field

Batch results are **not guaranteed to arrive in submission order**. The `custom_id` field is the only mechanism for correlating a result with the request that produced it.

```python
# Submit with custom_id
{
    "custom_id": "file-agents-coordinator",   # your key
    "params": { ... }
}

# Result comes back with the same custom_id
result.custom_id == "file-agents-coordinator"  # True — use this to look up the file
```

Exam option C for Q11 claims *"batch results can't be correlated back to requests"*. This is false. `custom_id` exists precisely for this purpose.

#### What to observe

Run `exercise_7_batches_api.py`. Watch:
1. Workflow A submits a batch and immediately explains why this is wrong — the batch is submitted successfully, but the developer workflow assumption is violated.
2. Workflow B submits all 5 file analyses, polls until complete, retrieves results by `custom_id`, and prints a summary table with per-file severity and estimated remediation hours.

The batch API rules printed at the end summarise every correct and wrong use case.

#### Questions to answer before moving on

1. What is the maximum result window for the Message Batches API?
2. Why can't the Batches API be used for pre-merge security checks?
3. What field do you use to correlate batch results with the requests that produced them?
4. What cost saving does the Batches API offer versus the standard Messages API?
5. Exam Scenario 5 Q11 proposes switching BOTH workflows to Batches API. What is the correct answer?

#### Try it

Modify `submit_overnight_batch()` to add a sixth file to `FILES_TO_ANALYSE`. Add it with a `custom_id` that follows the `"file-<directory>-<basename>"` naming pattern already used. Re-run the script and verify the sixth result appears in the summary table.

#### Exam rule

> The Message Batches API offers 50% cost reduction with no guaranteed latency SLA. Use it for **background jobs where results are not needed immediately** (overnight reports, weekly analyses, background classification). Use the real-time API when **a human or system is blocking on the response** (pre-merge checks, interactive agents, synchronous pipelines). The `custom_id` field correlates results to requests — batch results do NOT arrive in submission order.

---

## Lab Completion Checklist

Before moving to Week 5, answer these without looking:

- [ ] List the three levels of the CLAUDE.md hierarchy in order of precedence (most specific wins)
- [ ] Where do custom slash commands live in the project directory?
- [ ] What does plan mode do that a direct prompt does not?
- [ ] What is the correct CLI flag to run Claude Code non-interactively in CI? (Not `--no-interactive`)
- [ ] Why is "be careful with database code" not a useful CLAUDE.md instruction?
- [ ] What is `.claude/rules/` and when is it required instead of a subdirectory CLAUDE.md?
- [ ] Name two workflows where the Message Batches API is appropriate and two where it is not
- [ ] What field in the Batches API response lets you correlate results with submitted requests?

---

## Exam Connections

| Exercise | Domain | Exam Pattern Covered |
|---|---|---|
| 1 | D2 | Three-level hierarchy; scope and override rules |
| 2 | D2 | Effective constraint instructions vs. descriptive prose |
| 3 | D2 | Custom commands; discoverability; replacing tribal knowledge |
| 4 | D2 | Plan mode for multi-file changes; explicit change enumeration |
| 5 | D2 | `-p` flag for CI; exit codes; non-existent flags as distractors |
| 6 | D2 | `.claude/rules/` with glob patterns; file-path-conditional conventions |
| 7 | D2, D3 | Message Batches API; blocking vs. async; `custom_id` correlation |

---

## What's Next

Week 5 goes deep on Domain 3 — the difference between asking for structured output and requiring it, JSON schema design that prevents hallucinations, and building a validation-retry loop that escalates correctly.

→ **[Week 5 Lab — Prompt Engineering & Structured Output](../week-5-prompt-engineering/README.md)**

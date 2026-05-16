# Resolve — Project-Level Claude Code Configuration

Resolve is an AI-powered support ticket resolution system. This file defines the project
architecture, the files that control system behaviour, and the constraints Claude Code must
respect when working anywhere in this repository.

---

## Architecture

Resolve uses a coordinator-subagent pattern. A single **coordinator** receives an inbound
support ticket, classifies it, and dispatches one or more specialised subagents. Each subagent
runs its own isolated agentic loop and returns a typed result to the coordinator. The coordinator
assembles the final reply.

```
Inbound ticket
      │
      ▼
 Coordinator  ──────────────────────────────────────────────────────────┐
      │                                                                  │
      ├──► AccountAgent   (account lookups, auth issues, plan details)   │
      ├──► BillingAgent   (charges, refunds, invoice queries)            │
      └──► IncidentAgent  (outages, degraded service, SLA breaches)      │
                                                                         │
All subagents write into SessionState. Coordinator reads final state ───►┘
and emits a typed exit: success | budget_exhausted | truncated | error
```

`SessionState` is the shared fact store. It is passed to each subagent runner and mutated
in place. No subagent communicates with another directly — all coordination is through
`SessionState` and the coordinator's dispatch logic.

---

## Files That Matter

| File | Purpose |
|------|---------|
| `agents/coordinator.py` | Coordinator logic: ticket classification, subagent dispatch, final reply assembly |
| `agents/subagents.py` | `run_subagent(agent_name, state, tools)` — the shared subagent runner |
| `agents/session_state.py` | `SessionState` dataclass — the single source of truth for per-ticket facts |
| `agents/hooks.py` | `PreCallHook` and `PostCallHook` — fire before/after every tool call |
| `evals/run_evals.py` | Evaluation harness — runs 50 synthetic tickets, writes per-ticket JSON results |
| `output_schema.json` | Defines the exact shape every agent response must conform to |

Before modifying any of these files, read the relevant `CLAUDE.md` in its subdirectory.

---

## Constraints — What Claude Code Must NEVER Do Without Explicit Confirmation

These are not suggestions. If any of the following situations arises, Claude Code must stop,
state what it was about to do, and wait for an explicit "yes, proceed" before continuing.

1. **Change `max_iterations` in any agent loop.**
   The iteration budgets in `agents/coordinator.py` and `agents/subagents.py` were set after
   measuring the eval suite. Changing them silently breaks cost guarantees and may cause
   budget_exhausted exits on tickets that previously resolved.

2. **Modify the message history structure.**
   The `messages` list follows a strict `assistant` → `tool_result` alternation pattern.
   The Claude API requires this. Any deviation produces a 400 error at runtime. This includes
   changing the role labels, collapsing turns, or reordering tool_result blocks.

3. **Add a new tool without updating `output_schema.json` and `agents/CLAUDE.md`.**
   Every tool that an agent can call must be declared in `output_schema.json` (so the eval
   harness can validate its return shape) and described in `agents/CLAUDE.md` (so other
   engineers understand the tool call contract). Adding a tool to only one place causes silent
   schema drift.

4. **Change the `status` field values in tool return dicts.**
   Every tool returns `{"status": "success" | "access_failure" | "empty", ...}`. The
   coordinator and eval harness branch on these exact strings. Renaming or adding values
   breaks downstream logic without a traceable error.

5. **Modify `evals/run_evals.py` without running the eval suite first.**
   The eval harness is the ground truth for system behaviour. Changes to it must be compared
   against a baseline run. Modify eval logic before establishing a baseline and you lose the
   ability to detect regressions.

---

## Before Modifying Anything in `agents/`

Complete all three steps before touching any file under `agents/`:

1. **Read `agents/session_state.py`** — confirm which fields exist, their types, and which
   subagent is responsible for populating each one. If you add logic that writes to a field
   that does not exist in `SessionState`, the change will fail at runtime.

2. **Check `output_schema.json`** — confirm the expected output shape for the agent or tool
   you are modifying. If you change what an agent returns, the schema must be updated in the
   same commit or the eval harness will flag every ticket as a failure.

3. **Verify the iteration budget is unchanged** — open `agents/coordinator.py` and check
   `max_iterations`. Open `agents/subagents.py` and check the per-agent budgets. Do not
   proceed if either value differs from what `agents/CLAUDE.md` specifies.

---

## Custom Commands

These commands are available via `/command-name` in any Claude Code session within this project.

| Command | What it does |
|---------|-------------|
| `/test-ticket` | Runs a synthetic ticket through the full coordinator → subagent → reply pipeline. Prints per-subagent status, the final exit reason, and any HookViolations. |
| `/validate-schema` | Reads `output_schema.json`, runs 10 synthetic tickets, validates every output field, and prints a PASS/FAIL summary. |
| `/dry-run-deploy` | Runs the agent non-interactively against the staging ticket batch and diffs output against the last known-good run. Flags any ticket whose exit reason regressed to non-success. |

Command definitions live in `.claude/commands/`. Do not modify them without updating this table.

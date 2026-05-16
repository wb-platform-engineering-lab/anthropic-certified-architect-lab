# Resolve Agents — Subdirectory Claude Code Configuration

This file applies to all files under `agents/`. It adds constraints and detail that are
specific to the agent runtime. Instructions here take precedence over the project-root
`CLAUDE.md` where they conflict.

---

## The Agentic Loop State Machine

Every agent in Resolve (coordinator and subagents) runs the same loop structure:

```
while iteration < max_iterations:
    response = client.messages.create(...)
    stop_reason = response.stop_reason

    if stop_reason == "end_turn":
        # Agent produced a final reply — exit with success
        break

    if stop_reason == "tool_use":
        # Execute the tool calls in response.content
        # Append tool results to message history
        # Continue to next iteration

    if stop_reason == "max_tokens":
        # Response was truncated — exit with truncated
        break

    if stop_reason == "stop_sequence":
        # Hit an explicit stop sequence — treat as end_turn
        break
```

### `stop_reason` values and their meaning

| Value | Meaning | Action |
|-------|---------|--------|
| `"end_turn"` | Agent finished naturally | Record reply, exit loop with `success` |
| `"tool_use"` | Agent wants to call a tool | Execute all tool calls, append results, continue |
| `"max_tokens"` | Response hit token limit | Set exit reason `truncated`, break |
| `"stop_sequence"` | Hit a configured stop string | Treat as `end_turn` |

If `stop_reason` is any other value, log the unexpected value and exit with `error`.

---

## SessionState Fields

`SessionState` is defined in `session_state.py`. Every field is listed here with its type,
which agent is responsible for setting it, and what it represents.

| Field | Type | Set by | Purpose |
|-------|------|--------|---------|
| `ticket_id` | `str` | Coordinator (on entry) | Unique identifier for the inbound ticket |
| `customer_id` | `str` | Coordinator (on entry) | Customer account identifier |
| `raw_ticket` | `str` | Coordinator (on entry) | Original ticket text, unmodified |
| `classification` | `str` | Coordinator | Category: `"account"`, `"billing"`, `"incident"`, `"unknown"` |
| `dispatched_agents` | `list[str]` | Coordinator | Names of subagents dispatched for this ticket |
| `account_facts` | `dict` | AccountAgent | Account status, plan, auth state — populated by AccountAgent |
| `billing_facts` | `dict` | BillingAgent | Invoice history, charge details — populated by BillingAgent |
| `incident_facts` | `dict` | IncidentAgent | Active incidents, SLA status — populated by IncidentAgent |
| `violations` | `list[str]` | Hooks | HookViolation messages appended by PreCallHook or PostCallHook |
| `iteration_counts` | `dict[str, int]` | Each agent | Maps agent name → iterations used, for budget tracking |
| `exit_reason` | `str` | Each agent | Final exit reason for that agent's loop |
| `final_reply` | `str` | Coordinator | The reply text sent to the customer |

Do not add fields to `SessionState` without updating this table and `output_schema.json`.

---

## Exit Statuses

Every agent loop exits with one of four statuses. These are the only valid values for the
`exit_reason` field in `SessionState` and in the eval harness output.

| Status | Meaning |
|--------|---------|
| `success` | Agent completed its task and produced a valid reply |
| `budget_exhausted` | Agent hit `max_iterations` without reaching `end_turn` |
| `truncated` | A response was cut off by `max_tokens`; reply may be incomplete |
| `error` | An unexpected exception, unexpected `stop_reason`, or unrecoverable tool failure |

The eval harness marks any exit status other than `success` as a failing ticket unless
`violations` is empty and the exit is `budget_exhausted` (budget exhaustion without
violations is tracked separately as a coverage gap, not a correctness failure).

---

## Iteration Budgets

These values are fixed. Do not change them without running the full eval suite and confirming
that pass rates are equal to or better than the current baseline.

| Agent | `max_iterations` |
|-------|-----------------|
| Coordinator | 8 |
| AccountAgent | 5 |
| BillingAgent | 5 |
| IncidentAgent | 3 |

**Why IncidentAgent is limited to 3:** Incident lookups resolve in one or two tool calls
against the incident registry. Three iterations is sufficient. Budget beyond this is
wasteful and historically correlates with runaway loops on ambiguous tickets.

---

## Tool Call Contract

Every tool in Resolve must return a `dict`. The following rules apply without exception:

**Required field:** Every return dict must include a `"status"` key.

**Valid `status` values:**

| Value | When to use |
|-------|-------------|
| `"success"` | Tool executed successfully and returned meaningful data |
| `"access_failure"` | The tool could not access the required resource (permissions, not-found) |
| `"empty"` | Tool succeeded but the result set is empty (no charges, no incidents, etc.) |

**Never return `{}` on error.** An empty dict has no `status` field and will crash the
coordinator's result-handling logic. Return `{"status": "error", "message": "<reason>"}` if
none of the three standard statuses applies — but this indicates the tool needs a redesign.

**Never raise an exception out of a tool.** All tools must catch their own exceptions and
return an `access_failure` or `error` status dict. Uncaught exceptions from tools will set
the agent's exit reason to `error` and terminate the loop.

---

## Hook Rules

Hooks are defined in `agents/hooks.py`. There are two hook types:

**`PreCallHook`** — fires before every tool call. Receives the tool name and arguments.
Use it for: rate limit checks, PII scrubbing in arguments, audit logging.

**`PostCallHook`** — fires after every tool call. Receives the tool name, arguments, and
the result dict. Use it for: result validation, anomaly detection, cost tracking.

### HookViolation escalation

If a hook raises `HookViolation`, the agent loop must:
1. Append the violation message to `SessionState.violations`
2. Set `exit_reason = "error"`
3. Break out of the loop immediately

**Never catch `HookViolation` and retry.** A violation indicates a policy breach, not a
transient failure. Retrying after a violation compounds the breach and makes the audit log
unreliable.

---

## What This File Adds to the Project-Root CLAUDE.md

The project-root `CLAUDE.md` defines global constraints (never change `max_iterations`,
never break the message history structure, etc.). This file adds:

- The exact `max_iterations` values for each agent (so you know what "unchanged" means)
- The complete `SessionState` field list (so you know what exists before adding anything)
- The tool call contract in full detail (not just the status field names)
- The `HookViolation` escalation rule (no retry — ever)
- The full exit status semantics (how the eval harness interprets each one)

When there is a conflict between this file and the project root, this file wins for code
under `agents/`.

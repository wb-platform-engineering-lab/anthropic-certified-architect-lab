# Resolve Evals — Subdirectory Claude Code Configuration

This file applies to all files under `evals/`. It defines the eval harness structure, the
output schema, what Claude Code may and may not change here, and how to run the suite.

---

## Eval Harness Overview

`evals/run_evals.py` is the canonical measurement tool for Resolve. It runs **50 synthetic
support tickets** through the full coordinator → subagent → reply pipeline and writes a
per-ticket result to a JSON output file.

The 50 tickets in `evals/fixtures/` span the full ticket classification space:

| Category | Count | Notes |
|----------|-------|-------|
| `account` | 15 | Auth issues, plan changes, account lookups |
| `billing` | 15 | Refund requests, invoice disputes, charge queries |
| `incident` | 12 | Outage reports, degraded service, SLA breach claims |
| `unknown` | 8 | Ambiguous tickets the coordinator must handle gracefully |

A passing eval suite has **≥ 46 of 50 tickets** with `status: "pass"` and zero violations.
The 4-ticket tolerance exists for known edge cases in the `unknown` category. If pass count
drops below 46, the change that caused it must be reverted before merging.

---

## Eval Output Schema

`run_evals.py` writes a JSON array to the output file. Each element has this shape:

```json
{
  "ticket_id": "ticket_042",
  "status": "pass",
  "exit_reason": "success",
  "reply": "Your account has been updated...",
  "violations": [],
  "iteration_counts": {
    "coordinator": 3,
    "AccountAgent": 2
  },
  "duration_ms": 1840
}
```

### Field definitions

| Field | Type | Meaning |
|-------|------|---------|
| `ticket_id` | `str` | Matches the filename in `evals/fixtures/` (without `.json`) |
| `status` | `"pass"` \| `"fail"` | Pass if `exit_reason == "success"` AND `violations == []` |
| `exit_reason` | `str` | The coordinator's exit reason: `success`, `budget_exhausted`, `truncated`, `error` |
| `reply` | `str` | The final reply text the coordinator produced |
| `violations` | `list[str]` | HookViolation messages. A passing eval has `violations: []` |
| `iteration_counts` | `dict[str, int]` | Iterations used per agent. Used to track budget trends |
| `duration_ms` | `int` | Wall-clock time for this ticket in milliseconds |

**A passing eval result is defined as:** `status == "pass"`, which requires both
`exit_reason == "success"` AND `violations == []`. An eval with `exit_reason == "success"`
but non-empty `violations` is a failure — a policy breach occurred even if the reply was
produced.

---

## What Claude Code Must Not Do Here

**Do not modify `evals/run_evals.py` evaluation logic.**
The harness is the measurement instrument. Changing how it evaluates results — even
"fixing" something that looks like a bug — changes the measurement, not the system. Any
proposed change to `run_evals.py` must be reviewed by a human and run against the current
baseline before it can be merged.

**Do not modify or delete fixture tickets in `evals/fixtures/`.**
The 50 fixture tickets are the ground truth for what the system must handle. Removing a
difficult ticket improves the pass rate without improving the system. This is prohibited.

**Do not promote a dry-run result to `last_known_good` automatically.**
The file `evals/results/dry_run_last_known_good.json` is the baseline against which
`/dry-run-deploy` diffs. Only a human can update this file after reviewing a dry-run result.
Claude Code must not overwrite it under any circumstances.

**Adding new fixture tickets is fine.** If the ticket space has a gap (e.g., a class of
billing disputes not currently covered), add a new fixture file to `evals/fixtures/`. The
harness will pick it up automatically. Update the count table at the top of this file.

---

## How to Run the Eval Suite

**Full suite (50 tickets):**
```bash
python evals/run_evals.py --output evals/results/latest.json
```

**Single ticket (for debugging):**
```bash
python evals/run_evals.py --ticket ticket_042 --output evals/results/debug.json
```

**Comparison against last known good:**
```bash
python evals/run_evals.py \
  --output evals/results/latest.json \
  --compare evals/results/last_known_good.json
```

The comparison mode prints a diff of per-ticket status changes and exits with code 1 if
any ticket regressed from `pass` to `fail`.

---

## Results Directory

```
evals/results/
├── latest.json                  # Output of the most recent eval run
├── last_known_good.json         # Promoted by humans after a verified passing run
├── dry_run_latest.json          # Output of the most recent /dry-run-deploy
└── dry_run_last_known_good.json # Baseline for dry-run diffs
```

Claude Code may read any file in `evals/results/`. It must not write to
`last_known_good.json` or `dry_run_last_known_good.json`.

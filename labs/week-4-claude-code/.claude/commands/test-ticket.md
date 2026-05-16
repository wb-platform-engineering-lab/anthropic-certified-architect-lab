# /test-ticket

Run a synthetic support ticket through the full Resolve agent pipeline — coordinator
classification, subagent dispatch, and final reply assembly — and print each stage's result
as it completes. Flags any HookViolations that fire during the run.

**Arguments:**
- `--customer-id <id>` — Customer account identifier (default: `cust_9182`)
- `--ticket <text>` — Ticket body text (default: a standard billing dispute fixture)

**Example usage:**
```
/test-ticket --customer-id cust_4471 --ticket "I was charged twice for my Pro plan in March."
/test-ticket
/test-ticket --customer-id cust_0019
```

---

## Instructions

When this command is invoked, follow these steps in order. Do not skip steps. Do not
proceed to the next step until the current step is complete.

1. **Parse arguments from the command invocation.**
   Extract `--customer-id` (default: `cust_9182`) and `--ticket` (default: use the text
   from `evals/fixtures/ticket_001.json` as the test ticket body). If the fixture file does
   not exist, use: `"I need help with my account. My subscription was cancelled but I am
   still being charged."`

2. **Confirm the agent code is importable before running.**
   Check that `agents/coordinator.py` and `agents/session_state.py` exist. If either is
   missing, print an error and stop: `ERROR: agents/ directory is incomplete. Run
   /validate-schema first to diagnose the project state.`

3. **Print the test parameters.**
   Before running anything, print:
   ```
   ── Test Ticket Run ────────────────────────────────────────
   Customer ID : <customer_id>
   Ticket      : <first 80 chars of ticket text>...
   ──────────────────────────────────────────────────────────
   ```

4. **Run the coordinator against the ticket.**
   Execute: `python -c "from agents.coordinator import run_coordinator; import json; result = run_coordinator('<customer_id>', '<ticket_text>'); print(json.dumps(result, indent=2))"`

   Capture the output. If the command fails (non-zero exit), print the error output and
   stop with: `FATAL: Coordinator raised an unhandled exception. See output above.`

5. **Print the classification result.**
   From the coordinator output, extract and print the ticket classification:
   ```
   Classification : <classification>
   ```

6. **Print per-subagent status as each completes.**
   For each agent name in `dispatched_agents` (from the coordinator result), print:
   ```
   [AccountAgent]  exit=success  iterations=2/5
   [BillingAgent]  exit=success  iterations=3/5
   ```
   Use the `iteration_counts` and `exit_reason` fields from `SessionState`. If a subagent
   exited with anything other than `success`, print its status in uppercase:
   ```
   [IncidentAgent] exit=BUDGET_EXHAUSTED  iterations=3/3  ← INVESTIGATE
   ```

7. **Print HookViolations if any fired.**
   Check `SessionState.violations`. If the list is non-empty, print each violation:
   ```
   ── HookViolations ─────────────────────────────────────────
   [1] PreCallHook: PII detected in arguments to get_account_details
   ──────────────────────────────────────────────────────────
   ```
   If `violations` is empty, print: `No hook violations.`

8. **Print the final exit reason and reply.**
   ```
   ── Result ─────────────────────────────────────────────────
   Exit reason : success
   Reply       :
     Your account has been reviewed. The duplicate charge from
     March 14 has been identified and a refund of $29.00 has
     been initiated. You will see it within 5–7 business days.
   ──────────────────────────────────────────────────────────
   ```

9. **Print a final PASS or FAIL verdict.**
   PASS if: `exit_reason == "success"` AND `violations == []`
   FAIL otherwise. Include the failure reason:
   ```
   FAIL — exit_reason=budget_exhausted (expected success)
   ```
   or:
   ```
   FAIL — 1 HookViolation fired (see above)
   ```

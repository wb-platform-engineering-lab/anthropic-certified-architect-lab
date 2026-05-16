# /dry-run-deploy

Run the Resolve agent in non-interactive mode against the staging ticket batch and diff the
output against the last known-good run. Flags any ticket whose exit reason regressed from
`success` to anything else. Never modifies the `last_known_good` baseline — only a human
can promote a run.

This command is the gate before any deployment to staging or production. Run it after every
change to `agents/` and review the diff before merging.

---

## Instructions

Follow these steps in order. Complete each step fully before proceeding.

1. **Verify required files exist.**
   Check that all of the following exist before starting:
   - `evals/fixtures/staging_batch.json` — the staging ticket batch
   - `evals/results/dry_run_last_known_good.json` — the baseline for diffing

   If `staging_batch.json` is missing, stop:
   ```
   ERROR: evals/fixtures/staging_batch.json not found.
   This file must exist before a dry-run deploy can proceed.
   ```
   If `dry_run_last_known_good.json` is missing, warn but continue:
   ```
   WARNING: No last_known_good baseline found.
   This run will produce output but cannot diff against a baseline.
   A human must review evals/results/dry_run_latest.json and promote it manually.
   ```

2. **Print the run header.**
   ```
   ── Dry Run Deploy ─────────────────────────────────────────
   Batch      : evals/fixtures/staging_batch.json
   Baseline   : evals/results/dry_run_last_known_good.json
   Output     : evals/results/dry_run_latest.json
   Mode       : non-interactive (claude -p)
   ──────────────────────────────────────────────────────────
   ```

3. **Run the agent in non-interactive mode against the staging batch.**
   Use `claude -p` — not `--no-interactive`, not `--headless`, not `--batch`. These flags
   do not exist. The correct flag for non-interactive, single-turn execution is `-p`.

   Run:
   ```
   claude -p "Run the Resolve agent against every ticket in evals/fixtures/staging_batch.json.
   For each ticket, output a JSON object with fields: ticket_id, exit_reason, reply, violations.
   Write the full results array to evals/results/dry_run_latest.json.
   Do not modify any other file."
   ```

   Wait for completion. If the command exits non-zero, print the error and stop:
   ```
   FATAL: claude -p exited with code <N>. Output:
   <stdout/stderr>
   Dry run aborted — evals/results/dry_run_latest.json was not written.
   ```

4. **Confirm the output file was written.**
   Check that `evals/results/dry_run_latest.json` exists and is valid JSON. If the file
   is missing or malformed, stop with the same FATAL message as step 3.

5. **Load both files for diffing.**
   Load `evals/results/dry_run_latest.json` (new run) and
   `evals/results/dry_run_last_known_good.json` (baseline). Index both by `ticket_id`.

6. **Diff the results — added, removed, and changed tickets.**
   Print three sections:

   **Added tickets** (in new run, not in baseline):
   ```
   ── Added (not in baseline) ────────────────────────────────
   [ticket_051]  exit=success  (new ticket, no baseline to compare)
   ```

   **Removed tickets** (in baseline, not in new run):
   ```
   ── Removed (in baseline, not in new run) ──────────────────
   [ticket_009]  WARN: this ticket was in the baseline but is missing from the new run
   ```

   **Changed tickets** (in both, but result differs):
   For each changed ticket, show what changed:
   ```
   ── Changed ────────────────────────────────────────────────
   [ticket_003]
     exit_reason : success → budget_exhausted  ← REGRESSION
     reply       : (changed)

   [ticket_017]
     exit_reason : success → success  (unchanged)
     violations  : [] → ["PreCallHook: rate limit warning"]  ← NEW VIOLATION
   ```

7. **Flag exit reason regressions explicitly.**
   After the diff, print a dedicated section for any ticket whose `exit_reason` changed
   FROM `success` TO anything else:
   ```
   ── Exit Reason Regressions ────────────────────────────────
   [ticket_003]  success → budget_exhausted  MUST INVESTIGATE before deploying
   ──────────────────────────────────────────────────────────
   ```
   If there are no regressions, print: `No exit reason regressions. All previously
   successful tickets still exit with success.`

8. **Print the overall verdict.**
   If there are any exit reason regressions or newly introduced violations:
   ```
   FAIL — dry run has regressions. Do NOT promote to production.
   Review the diff above and fix the root cause before re-running.
   ```
   If the diff is clean (no regressions, no new violations):
   ```
   PASS — dry run matches or improves on the baseline.
   To promote this run: manually copy evals/results/dry_run_latest.json
   to evals/results/dry_run_last_known_good.json after human review.
   ```

9. **Do not promote the run automatically.**
   Under no circumstances should `dry_run_last_known_good.json` be overwritten by this
   command. Promotion is a human decision. The command must only read that file, never write
   to it. If asked to promote, respond: "Promotion requires explicit human action. Copy
   dry_run_latest.json to dry_run_last_known_good.json manually after reviewing the diff."

# /validate-schema

Read `output_schema.json`, run 10 synthetic tickets through the agent pipeline, and validate
every field in every result against the schema. Prints a per-ticket PASS/FAIL, identifies
the specific field that failed on any mismatch, and concludes with an overall PASS or FAIL.

This command is the first thing to run after any change to `output_schema.json` or to a
tool's return value. It catches schema drift before it reaches the eval suite or production.

---

## Instructions

Follow these steps in order. Do not skip any step.

1. **Read `output_schema.json`.**
   Load the file and confirm it is valid JSON. If it is missing or malformed, stop:
   ```
   ERROR: output_schema.json is missing or invalid JSON.
   Path checked: <absolute path>
   Fix this before running validation.
   ```
   Print the top-level keys found in the schema so the operator can confirm the right file
   was loaded.

2. **Select the 10 validation tickets.**
   Use these ticket IDs from `evals/fixtures/`:
   `ticket_001`, `ticket_005`, `ticket_010`, `ticket_015`, `ticket_020`,
   `ticket_025`, `ticket_030`, `ticket_035`, `ticket_040`, `ticket_048`

   These are chosen to cover all four classification categories. If any fixture file is
   missing, skip it and note which one was skipped in the output — do not abort.

3. **Print the validation header.**
   ```
   ── Schema Validation Run ──────────────────────────────────
   Schema     : output_schema.json (<N> top-level fields)
   Tickets    : 10 synthetic tickets
   ──────────────────────────────────────────────────────────
   ```

4. **Run each ticket through the coordinator.**
   For each of the 10 tickets, run:
   `python -c "from agents.coordinator import run_coordinator; import json; ..."`
   Capture the full result dict (including `SessionState` fields and the agent's output).
   Do not suppress errors — if a ticket raises an exception, record it as a failure:
   ```
   [ticket_001]  FAIL — coordinator raised exception: <exception message>
   ```

5. **Validate each result against the schema.**
   For each ticket result, check every field defined in `output_schema.json`:
   - Field is present in the result
   - Field's value matches the declared type (`string`, `array`, `object`, etc.)
   - If the schema defines an `enum` for a field, the value is one of the allowed values
   - If the schema defines `required` fields, all are present

   For each ticket, print one line:
   ```
   [ticket_001]  PASS
   [ticket_005]  FAIL — field "exit_reason" value "done" not in enum [success, budget_exhausted, truncated, error]
   [ticket_010]  FAIL — field "violations" expected array, got null
   [ticket_015]  PASS
   ```

6. **Print the summary.**
   After all 10 tickets:
   ```
   ── Validation Summary ─────────────────────────────────────
   Passed : 8 / 10
   Failed : 2 / 10

   Failures:
     ticket_005 — field "exit_reason" value "done" not in enum
     ticket_010 — field "violations" expected array, got null

   Root cause hint: both failures involve output_schema.json fields
   that may have changed since the agent code was last updated.
   Check git log -- output_schema.json for recent changes.
   ──────────────────────────────────────────────────────────
   ```

7. **Exit with a clear verdict.**
   If all 10 passed (or all non-skipped tickets passed):
   ```
   PASS — all tickets conform to output_schema.json
   ```
   If any ticket failed:
   ```
   FAIL — 2 ticket(s) failed schema validation
   Action required: fix the field mismatches listed above before merging.
   ```

   Do not suggest changes to `output_schema.json` to make failing tickets pass — the schema
   is the contract. The agent output must conform to it, not the other way around.

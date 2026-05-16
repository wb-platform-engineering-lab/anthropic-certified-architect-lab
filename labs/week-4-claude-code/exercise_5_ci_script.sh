#!/usr/bin/env bash
# =============================================================================
# exercise_5_ci_script.sh — Claude Code in Non-Interactive CI Mode
# =============================================================================
#
# IMPORTANT — The correct flag for non-interactive Claude Code is: -p (or --print)
#
# Common wrong answers (these flags do NOT exist — do not use them):
#   --no-interactive    ← does not exist
#   --headless          ← does not exist
#   --batch             ← does not exist
#   CLAUDE_HEADLESS=true ← not a valid env var for this purpose
#
# The -p flag tells Claude Code to:
#   1. Accept a prompt as a command-line argument
#   2. Process it once
#   3. Print the response to stdout
#   4. Exit — it does NOT wait for user input
#
# This makes it safe to use in CI pipelines where no human is present.
#
# NOTE: -p is single-turn. It processes one prompt and exits. For multi-step
# CI workflows, chain multiple `claude -p` calls and pass output between them
# via temporary files (shown at the bottom of this script).
#
# =============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TIMESTAMP="$(date +%Y%m%d_%H%M%S)"
LOG_FILE="${SCRIPT_DIR}/evals/results/ci_run_${TIMESTAMP}.log"

# -----------------------------------------------------------------------------
# Colour helpers (safe to use in CI — checks for terminal support)
# -----------------------------------------------------------------------------
if [ -t 1 ] && command -v tput &>/dev/null; then
    RED=$(tput setaf 1)
    GREEN=$(tput setaf 2)
    YELLOW=$(tput setaf 3)
    RESET=$(tput sgr0)
else
    RED="" GREEN="" YELLOW="" RESET=""
fi

print_header() {
    echo ""
    echo "============================================================"
    echo "  Resolve CI — Schema Violation Check"
    echo "  $(date '+%Y-%m-%d %H:%M:%S')"
    echo "============================================================"
    echo ""
}

# -----------------------------------------------------------------------------
# Verify claude is on PATH before doing anything else
# -----------------------------------------------------------------------------
if ! command -v claude &>/dev/null; then
    echo "${RED}ERROR: 'claude' command not found on PATH.${RESET}"
    echo "Install Claude Code: npm install -g @anthropic-ai/claude-code"
    echo "Or check that your PATH includes the npm global bin directory."
    exit 1
fi

# -----------------------------------------------------------------------------
# Step 1 — Run the schema violation check
# -----------------------------------------------------------------------------
#
# The prompt instructs Claude to output ONLY structured results — no prose,
# no explanations, no interactive questions. This is important for CI:
# downstream steps parse stdout, so any extra output breaks parsing.
#
# Format: one line per finding.
#   PASS                            — if no violations found
#   FAIL: <file>: <description>     — one line per violation found
#
# Using -p (--print): single-turn, prints to stdout, exits.
# NOT --no-interactive, NOT --headless, NOT --batch.
#
print_header

echo "Running schema violation check against agents/ ..."
echo "Prompt: Analyse agent code for schema violations (structured output mode)"
echo ""

# Capture claude's output to a variable.
# If claude itself fails (non-zero exit), set CLAUDE_FAILED=true and capture the error.
CLAUDE_FAILED=false
CLAUDE_OUTPUT=""

if ! CLAUDE_OUTPUT=$(claude -p \
    "Analyse the agent code in agents/ for schema violations. \
Check each tool's return dict against output_schema.json. \
Check that every tool returns a dict with a 'status' field set to one of: \
success, access_failure, empty. \
Check that no tool raises an exception (all exceptions must be caught and \
returned as a status dict). \
Check that max_iterations in coordinator.py is 8, in subagents.py is 5 for \
AccountAgent and BillingAgent, and 3 for IncidentAgent. \
Output ONLY: \
  PASS \
if no violations found, or one line per violation in this exact format: \
  FAIL: <filename>: <description of violation> \
No other output. No explanations. No markdown." 2>&1); then
    CLAUDE_FAILED=true
fi

# Log the raw output for audit purposes
mkdir -p "$(dirname "${LOG_FILE}")"
{
    echo "=== Claude Code CI Run ==="
    echo "Timestamp: ${TIMESTAMP}"
    echo "Exit status: $([ "${CLAUDE_FAILED}" = "true" ] && echo "non-zero" || echo "0")"
    echo "Output:"
    echo "${CLAUDE_OUTPUT}"
} >> "${LOG_FILE}"

echo "Raw output from claude:"
echo "------------------------------------------------------------"
echo "${CLAUDE_OUTPUT}"
echo "------------------------------------------------------------"
echo ""

# -----------------------------------------------------------------------------
# Step 2 — Parse the output and determine pass/fail
# -----------------------------------------------------------------------------

if [ "${CLAUDE_FAILED}" = "true" ]; then
    echo "${RED}FATAL: claude -p exited with a non-zero status.${RESET}"
    echo "This indicates Claude Code could not process the prompt."
    echo "Check your ANTHROPIC_API_KEY and network connectivity."
    echo "Log written to: ${LOG_FILE}"
    exit 1
fi

# Count FAIL lines in the output
FAIL_COUNT=0
while IFS= read -r line; do
    if [[ "${line}" == FAIL:* ]]; then
        FAIL_COUNT=$((FAIL_COUNT + 1))
    fi
done <<< "${CLAUDE_OUTPUT}"

# -----------------------------------------------------------------------------
# Step 3 — Report and exit
# -----------------------------------------------------------------------------

if [ "${FAIL_COUNT}" -gt 0 ]; then
    echo "${RED}FAIL — ${FAIL_COUNT} schema violation(s) found.${RESET}"
    echo ""
    echo "Violations:"
    while IFS= read -r line; do
        if [[ "${line}" == FAIL:* ]]; then
            echo "  ${RED}✗${RESET} ${line#FAIL: }"
        fi
    done <<< "${CLAUDE_OUTPUT}"
    echo ""
    echo "Action required: fix the violations listed above before merging."
    echo "Log written to: ${LOG_FILE}"
    exit 1
else
    echo "${GREEN}PASS — no schema violations found in agents/.${RESET}"
    echo "Log written to: ${LOG_FILE}"
    exit 0
fi


# =============================================================================
# APPENDIX: Chaining multiple claude -p calls
# =============================================================================
#
# -p is single-turn: one prompt in, one response out, then claude exits.
# For multi-step CI workflows, chain calls by passing output via temp files.
#
# Example: run a two-step pipeline where step 2 uses step 1's output.
#
# (Not executed — reference implementation only)
#
# STEP_1_OUTPUT=$(mktemp /tmp/resolve_ci_step1.XXXXXX)
# STEP_2_OUTPUT=$(mktemp /tmp/resolve_ci_step2.XXXXXX)
#
# # Step 1: Analyse the code
# claude -p "Analyse agents/coordinator.py for budget violations.
# Output a JSON array of violations. Each item: {file, line, description}.
# Output only valid JSON." > "${STEP_1_OUTPUT}"
#
# # Step 2: Use step 1's output to generate a fix plan
# VIOLATIONS=$(cat "${STEP_1_OUTPUT}")
# claude -p "Given these violations: ${VIOLATIONS}
# For each violation, output a one-line fix description.
# Format: <file>:<line>: <fix>" > "${STEP_2_OUTPUT}"
#
# cat "${STEP_2_OUTPUT}"
# rm -f "${STEP_1_OUTPUT}" "${STEP_2_OUTPUT}"
#
# Key points:
# - Each claude -p call is independent — no shared context between calls
# - Pass output between calls via files or environment variables
# - Keep prompts structured so output is machine-readable
# - Never use --no-interactive, --headless, or --batch — they do not exist
#
# =============================================================================

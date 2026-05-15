"""
Exercise 3 — Pre-call and Post-call Hooks as Programmatic Guardrails

Implements two hook classes that wrap every tool call in an agentic loop:

  PreCallHook  — fires BEFORE the tool executes
    - Blocks redundant calls for idempotent lookups (same tool called twice)
    - Enforces prerequisite ordering (draft_reply requires account + invoices first)

  PostCallHook — fires AFTER the tool returns
    - Validates output schema (status field must be present)
    - Validates plan values for get_account_status
    - Validates non-zero amounts for get_invoice_detail

  HookedAgentLoop — integrates both hooks into a standard agentic loop.
    When a pre-call HookViolation fires the loop returns immediately with
    status="hook_violation" — it does NOT retry the call.

Three demo scenarios:
  1. Normal flow — no violations
  2. Redundancy violation — get_account_status called a second time
  3. Prerequisite violation — draft_reply before account data retrieved
"""
import json
from dataclasses import dataclass, field
from typing import Any, Optional

import anthropic
from dotenv import load_dotenv

load_dotenv()
client = anthropic.Anthropic()


# ── HOOK VIOLATION ────────────────────────────────────────────────────────────

class HookViolation(Exception):
    """Raised by a hook when a guardrail is breached."""
    def __init__(self, rule: str, detail: str = ""):
        self.rule = rule
        self.detail = detail
        super().__init__(f"[{rule}] {detail}")


# ── PRE-CALL HOOK ─────────────────────────────────────────────────────────────

class PreCallHook:
    """
    Runs before every tool call. Maintains session_state as a shared dict.

    Rules enforced:
      1. Redundancy guard — idempotent lookups must not be called twice per session.
         Applies to: get_account_status, check_status_page
      2. Prerequisite ordering — some tools require others to have run first.
         draft_reply  → requires: get_account_status AND list_invoices
         process_refund → requires: get_account_status
    """

    # Tools that are idempotent and should only run once per session
    IDEMPOTENT_TOOLS = {"get_account_status", "check_status_page"}

    # Map: tool_name → set of tools that must have been called first
    PREREQUISITES: dict[str, set] = {
        "draft_reply": {"get_account_status", "list_invoices"},
        "process_refund": {"get_account_status"}
    }

    def __init__(self):
        self._called: set[str] = set()

    def before_call(self, tool_name: str, tool_input: dict, session_state: dict):
        """
        Called before tool_name executes. Raises HookViolation if a rule is broken.
        Does NOT modify state — that happens in after_call.
        """
        # Rule 1: Redundancy guard for idempotent tools
        if tool_name in self.IDEMPOTENT_TOOLS and tool_name in self._called:
            raise HookViolation(
                rule="redundancy_guard",
                detail=(
                    f"'{tool_name}' is idempotent and was already called this session. "
                    f"Called so far: {sorted(self._called)}"
                )
            )

        # Rule 2: Prerequisite ordering
        if tool_name in self.PREREQUISITES:
            required = self.PREREQUISITES[tool_name]
            missing = required - self._called
            if missing:
                raise HookViolation(
                    rule="prerequisite_violation",
                    detail=(
                        f"'{tool_name}' requires {sorted(required)} to have been called first. "
                        f"Missing: {sorted(missing)}. "
                        f"Called so far: {sorted(self._called)}"
                    )
                )

    def after_call(self, tool_name: str):
        """Registers the tool as successfully called this session."""
        self._called.add(tool_name)

    @property
    def called(self) -> set[str]:
        return frozenset(self._called)


# ── POST-CALL HOOK ────────────────────────────────────────────────────────────

class PostCallHook:
    """
    Runs after every tool call returns. Validates the output dict.

    Rules enforced:
      1. Every output must contain a 'status' field.
      2. get_account_status must return a recognised plan name.
      3. get_invoice_detail must return an amount > 0.
    """

    VALID_PLANS = {"starter", "professional", "enterprise"}

    def validate_output(self, tool_name: str, output: dict):
        """
        Validates the output dict. Raises HookViolation if output is invalid.
        """
        # Rule 1: status field must be present
        if "status" not in output:
            raise HookViolation(
                rule="missing_status_field",
                detail=f"Output of '{tool_name}' is missing required 'status' field. Got: {output}"
            )

        # Rule 2: plan validation for get_account_status
        if tool_name == "get_account_status":
            plan = output.get("plan")
            if plan is not None and plan not in self.VALID_PLANS:
                raise HookViolation(
                    rule="invalid_plan_value",
                    detail=(
                        f"get_account_status returned plan='{plan}'. "
                        f"Expected one of: {sorted(self.VALID_PLANS)}"
                    )
                )

        # Rule 3: non-zero amount for get_invoice_detail
        if tool_name == "get_invoice_detail":
            amount = output.get("amount")
            if amount is not None and amount <= 0:
                raise HookViolation(
                    rule="invalid_invoice_amount",
                    detail=(
                        f"get_invoice_detail returned amount={amount}. "
                        "Invoice amount must be greater than 0."
                    )
                )


# ── HOOKED AGENT LOOP ─────────────────────────────────────────────────────────

@dataclass
class HookedLoopResult:
    status: str          # success | hook_violation | budget_exhausted | failed
    reply: str = ""
    rule: Optional[str] = None
    escalated: bool = False
    iterations: int = 0
    tools_called: list[str] = field(default_factory=list)

    def __str__(self):
        base = f"status={self.status}, iterations={self.iterations}"
        if self.rule:
            base += f", rule={self.rule}"
        if self.escalated:
            base += ", escalated=True"
        if self.tools_called:
            base += f", tools={self.tools_called}"
        return base


def run_hooked_agent(
    ticket: str,
    system: str,
    tools: list[dict],
    tool_fn,
    session_state: Optional[dict] = None,
    max_iterations: int = 6
) -> HookedLoopResult:
    """
    Standard agentic loop with both hooks integrated.

    When a PreCallHook fires:
      - The violating tool call is blocked (never executed)
      - The loop returns immediately with status='hook_violation'
      - escalated=True signals the coordinator to route to a human

    When a PostCallHook fires:
      - The output is flagged as invalid
      - The loop returns immediately with status='hook_violation'
    """
    pre_hook = PreCallHook()
    post_hook = PostCallHook()
    if session_state is None:
        session_state = {}

    messages: list[dict[str, Any]] = [{"role": "user", "content": ticket}]
    tools_called: list[str] = []

    for iteration in range(1, max_iterations + 1):
        print(f"  [hooked_loop] iteration {iteration}")

        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=512,
            tools=tools,
            system=system,
            messages=messages
        )

        print(f"  [hooked_loop] stop_reason={response.stop_reason}")

        if response.stop_reason == "end_turn":
            text = next((b.text for b in response.content if hasattr(b, "text")), "")
            return HookedLoopResult(
                status="success",
                reply=text,
                iterations=iteration,
                tools_called=tools_called
            )

        elif response.stop_reason == "tool_use":
            messages.append({"role": "assistant", "content": response.content})
            tool_results = []

            for block in response.content:
                if block.type != "tool_use":
                    continue

                print(f"  [hooked_loop] tool requested: {block.name}({block.input})")

                # ── Pre-call hook ─────────────────────────────────────────────
                try:
                    pre_hook.before_call(block.name, block.input, session_state)
                except HookViolation as e:
                    print(f"  [PRE-CALL HOOK] VIOLATION — rule={e.rule}: {e.detail}")
                    # Immediate exit — do not execute the tool, do not retry
                    return HookedLoopResult(
                        status="hook_violation",
                        rule=e.rule,
                        escalated=True,
                        iterations=iteration,
                        tools_called=tools_called
                    )

                # ── Execute tool ──────────────────────────────────────────────
                result = tool_fn(block.name, block.input)
                print(f"  [hooked_loop] tool result: {result}")

                # ── Post-call hook ────────────────────────────────────────────
                try:
                    post_hook.validate_output(block.name, result)
                except HookViolation as e:
                    print(f"  [POST-CALL HOOK] VIOLATION — rule={e.rule}: {e.detail}")
                    return HookedLoopResult(
                        status="hook_violation",
                        rule=e.rule,
                        escalated=True,
                        iterations=iteration,
                        tools_called=tools_called
                    )

                # ── Register call only after both hooks passed ─────────────────
                pre_hook.after_call(block.name)
                tools_called.append(block.name)

                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": json.dumps(result)
                })

            messages.append({"role": "user", "content": tool_results})

        elif response.stop_reason == "max_tokens":
            return HookedLoopResult(
                status="failed",
                iterations=iteration,
                tools_called=tools_called
            )

        else:
            return HookedLoopResult(
                status="failed",
                iterations=iteration,
                tools_called=tools_called
            )

    return HookedLoopResult(
        status="budget_exhausted",
        iterations=max_iterations,
        tools_called=tools_called
    )


# ── TOOL DEFINITIONS ──────────────────────────────────────────────────────────

ALL_TOOLS = [
    {
        "name": "get_account_status",
        "description": "Retrieve account and plan status. Call once per session.",
        "input_schema": {
            "type": "object",
            "properties": {"customer_id": {"type": "string"}},
            "required": ["customer_id"]
        }
    },
    {
        "name": "list_invoices",
        "description": "List invoices for a customer. Call once per session.",
        "input_schema": {
            "type": "object",
            "properties": {"customer_id": {"type": "string"}},
            "required": ["customer_id"]
        }
    },
    {
        "name": "get_invoice_detail",
        "description": "Get full detail for a specific invoice.",
        "input_schema": {
            "type": "object",
            "properties": {"invoice_id": {"type": "string"}},
            "required": ["invoice_id"]
        }
    },
    {
        "name": "check_status_page",
        "description": "Check status page for active incidents. Call once per session.",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": []
        }
    },
    {
        "name": "draft_reply",
        "description": (
            "Draft a reply to the customer. "
            "Requires get_account_status AND list_invoices to have been called first."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"reply_text": {"type": "string"}},
            "required": ["reply_text"]
        }
    }
]

NORMAL_SYSTEM = (
    "You are a Resolve support agent. "
    "To handle a billing query: call get_account_status, then list_invoices, "
    "then draft_reply. Do not skip steps."
)

# This system prompt instructs the model to call get_account_status twice
# to trigger the redundancy guardrail.
REDUNDANCY_SYSTEM = (
    "You are a Resolve support agent. "
    "First call get_account_status to verify the account. "
    "Then call get_account_status AGAIN to double-check it. "
    "Then list invoices and draft a reply."
)

# This system prompt instructs the model to call draft_reply first
# to trigger the prerequisite guardrail.
PREREQUISITE_SYSTEM = (
    "You are a Resolve support agent. "
    "Immediately call draft_reply to send a response. "
    "Do not call get_account_status or list_invoices first."
)


def normal_tool_fn(name: str, inputs: dict) -> dict:
    """Returns valid, well-formed outputs for all tools."""
    if name == "get_account_status":
        return {
            "status": "success",
            "plan": "enterprise",
            "account_status": "active",
            "open_invoices": 2
        }
    if name == "list_invoices":
        return {
            "status": "success",
            "invoices": ["INV-2026-0041", "INV-2026-0042"]
        }
    if name == "get_invoice_detail":
        return {
            "status": "success",
            "invoice_id": inputs.get("invoice_id"),
            "amount": 4200.00,
            "paid": False
        }
    if name == "check_status_page":
        return {"status": "success", "active_incidents": []}
    if name == "draft_reply":
        return {"status": "success", "drafted": True}
    return {"status": "error", "message": f"Unknown tool: {name}"}


TICKET = (
    "Hi, my invoice INV-2026-0042 shows $4,200. "
    "I'm on the enterprise plan. Can you confirm this is correct? "
    "Customer ID: cust_9182."
)

# ── SCENARIO 1: Normal flow ───────────────────────────────────────────────────
print("=" * 60)
print("SCENARIO 1 — Normal flow (no violations expected)")
print("=" * 60)
result1 = run_hooked_agent(
    ticket=TICKET,
    system=NORMAL_SYSTEM,
    tools=ALL_TOOLS,
    tool_fn=normal_tool_fn
)
print(f"\nResult: {result1}")
if result1.status == "success":
    print(f"Reply  : {result1.reply[:200]}")

# ── SCENARIO 2: Redundancy violation ─────────────────────────────────────────
print("\n" + "=" * 60)
print("SCENARIO 2 — Redundancy violation (get_account_status called twice)")
print("=" * 60)
result2 = run_hooked_agent(
    ticket=TICKET,
    system=REDUNDANCY_SYSTEM,
    tools=ALL_TOOLS,
    tool_fn=normal_tool_fn
)
print(f"\nResult   : {result2}")
print(f"Escalated: {result2.escalated}")
print(f"Rule     : {result2.rule}")

# ── SCENARIO 3: Prerequisite violation ───────────────────────────────────────
print("\n" + "=" * 60)
print("SCENARIO 3 — Prerequisite violation (draft_reply before account checked)")
print("=" * 60)
result3 = run_hooked_agent(
    ticket=TICKET,
    system=PREREQUISITE_SYSTEM,
    tools=ALL_TOOLS,
    tool_fn=normal_tool_fn
)
print(f"\nResult   : {result3}")
print(f"Escalated: {result3.escalated}")
print(f"Rule     : {result3.rule}")

# ── SUMMARY ───────────────────────────────────────────────────────────────────
print("\n" + "=" * 60)
print("HOOK SUMMARY")
print("=" * 60)
for label, result in [("Normal", result1), ("Redundancy", result2), ("Prerequisite", result3)]:
    print(f"  {label:<14} status={result.status:<20} escalated={result.escalated}")

print("\nKey takeaway:")
print("  Hooks fire in code — they are not subject to model non-determinism.")
print("  A pre-call violation blocks execution before the tool runs.")
print("  hook_violation with escalated=True routes to a human immediately.")
print("  PostCallHook catches bad data before it reaches downstream agents.")

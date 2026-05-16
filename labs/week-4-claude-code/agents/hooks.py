"""
agents/hooks.py — PreCallHook and PostCallHook for the Resolve agent pipeline.

Hooks fire before and after every tool call. If a hook raises HookViolation,
the agent loop must: append the violation to SessionState.violations, set
exit_reason = "error", and break immediately. Never catch HookViolation and
retry — a violation is a policy breach, not a transient failure.
"""


class HookViolation(Exception):
    """
    Raised by Pre/PostCallHook when a policy rule is violated.

    Attributes:
        rule   -- the rule name (e.g. "REDUNDANT_CALL", "PREREQUISITE_MISSING")
        detail -- human-readable description of the violation
    """
    def __init__(self, rule: str, detail: str) -> None:
        super().__init__(f"[{rule}] {detail}")
        self.rule = rule
        self.detail = detail


class PreCallHook:
    """
    Fires before every tool call. Receives the tool name and arguments.

    Checks enforced:
      - REDUNDANT_CALL: idempotent tools must not be called more than once per ticket
      - PREREQUISITE_MISSING: some tools require a prior tool to have run first
    """

    # Tools that must run at most once per ticket
    IDEMPOTENT_TOOLS: set = {
        "get_account_details",
        "get_billing_summary",
        "get_incident_status",
    }

    # tool -> the tool that must have run before it
    PREREQUISITES: dict = {
        "issue_refund": "get_billing_summary",
        "escalate_incident": "get_incident_status",
    }

    def __init__(self) -> None:
        self._called: set = set()  # tracks which tools have been called this session

    def before_call(self, tool_name: str, tool_args: dict) -> None:
        """
        Called immediately before a tool executes.
        Raises HookViolation if a policy rule is broken.
        """
        # Check for redundant calls on idempotent tools
        if tool_name in self.IDEMPOTENT_TOOLS and tool_name in self._called:
            raise HookViolation(
                rule="REDUNDANT_CALL",
                detail=f"{tool_name} was already called this session. Idempotent tools run at most once.",
            )

        # Check prerequisites
        required = self.PREREQUISITES.get(tool_name)
        if required and required not in self._called:
            raise HookViolation(
                rule="PREREQUISITE_MISSING",
                detail=f"{tool_name} requires {required} to run first, but it has not been called yet.",
            )

        self._called.add(tool_name)

    def after_call(self, tool_name: str, tool_args: dict, result: dict) -> None:
        """
        Called immediately after a tool executes (before PostCallHook).
        No-op in the PreCallHook — override in PostCallHook.
        """
        pass


class PostCallHook:
    """
    Fires after every tool call. Receives the tool name, arguments, and result.

    Checks enforced:
      - MISSING_STATUS: result dict must have a "status" field
      - INVALID_STATUS: status must be one of the three valid values
      - INVALID_AMOUNT: refund amounts must be positive and within policy limits
    """

    VALID_STATUSES: set = {"success", "access_failure", "empty"}
    MAX_REFUND_AMOUNT: float = 500.0

    def validate_output(self, tool_name: str, tool_args: dict, result: dict) -> None:
        """
        Called after every tool call. Raises HookViolation if the result is invalid.
        """
        # Every tool must return a dict with a status field
        if "status" not in result:
            raise HookViolation(
                rule="MISSING_STATUS",
                detail=f"{tool_name} returned a dict without a 'status' field. All tools must include status.",
            )

        status = result["status"]
        if status not in self.VALID_STATUSES:
            raise HookViolation(
                rule="INVALID_STATUS",
                detail=f"{tool_name} returned status='{status}'. Valid values: {self.VALID_STATUSES}.",
            )

        # Additional check for refund amounts
        if tool_name == "issue_refund" and status == "success":
            amount = result.get("amount", 0)
            if amount <= 0:
                raise HookViolation(
                    rule="INVALID_AMOUNT",
                    detail=f"issue_refund returned amount={amount}. Refund amounts must be positive.",
                )
            if amount > self.MAX_REFUND_AMOUNT:
                raise HookViolation(
                    rule="INVALID_AMOUNT",
                    detail=f"issue_refund returned amount={amount}, which exceeds the policy limit of {self.MAX_REFUND_AMOUNT}.",
                )

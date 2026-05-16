"""
agents/session_state.py — Shared fact store for the Resolve agent pipeline.

SessionState is passed to every subagent and mutated in place. No subagent
communicates with another directly — all coordination is through this object
and the coordinator's dispatch logic.

Field ownership is defined in agents/CLAUDE.md. Do not add fields without
updating that file and output_schema.json.
"""

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class SessionState:
    # --- Set by coordinator on entry ---
    ticket_id: str = ""
    customer_id: str = ""
    raw_ticket: str = ""

    # --- Set by coordinator after classification ---
    classification: str = ""          # "account" | "billing" | "incident" | "unknown"
    dispatched_agents: list = field(default_factory=list)  # names of agents dispatched

    # --- Populated by subagents ---
    account_facts: dict = field(default_factory=dict)   # AccountAgent
    billing_facts: dict = field(default_factory=dict)   # BillingAgent
    incident_facts: dict = field(default_factory=dict)  # IncidentAgent

    # --- Hook violations: appended by Pre/PostCallHook ---
    violations: list = field(default_factory=list)

    # --- Tracking: set by each agent ---
    iteration_counts: dict = field(default_factory=dict)  # agent_name -> iterations used
    exit_reason: str = ""             # set by the most recent agent that exited

    # --- Set by coordinator at the end ---
    final_reply: str = ""

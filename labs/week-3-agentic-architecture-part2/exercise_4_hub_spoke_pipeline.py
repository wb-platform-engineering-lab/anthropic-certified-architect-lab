"""
Exercise 4 — Hub-and-Spoke vs Pipeline Multi-Agent Patterns

Implements and compares two architectural patterns for the same support ticket:

  Hub-and-Spoke
    - Coordinator owns ALL state
    - Subagents are stateless: each receives only a task string, returns a result dict
    - No subagent can see any other agent's output
    - Coordinator assembles all results and makes the final decision

  Pipeline
    - Three ordered stages, each receiving a growing context dict
    - Stage 1 (EnrichmentAgent)  : adds account + incident data to context
    - Stage 2 (ClassificationAgent): reads enriched context, adds decision + confidence
    - Stage 3 (ResponseAgent)    : reads full context, writes final reply
    - Context grows: {ticket} → + account/incident → + decision/confidence → + reply

Both patterns run on the same ticket. The demo prints a side-by-side comparison
showing context visibility at each step and which pattern gives more auditable
intermediate state.
"""
import json
from dataclasses import dataclass, field
from typing import Any, Optional

import anthropic
from dotenv import load_dotenv

load_dotenv()
client = anthropic.Anthropic()


# ── GENERIC SUBAGENT RUNNER ───────────────────────────────────────────────────

@dataclass
class SubAgentResult:
    agent: str
    status: str
    output: dict
    iterations: int

    def __str__(self):
        return (
            f"SubAgentResult(agent={self.agent}, status={self.status}, "
            f"output_keys={list(self.output.keys())})"
        )


def run_subagent(
    name: str,
    system: str,
    task: str,
    tools: list[dict],
    tool_fn,
    max_iterations: int = 5
) -> SubAgentResult:
    """
    Isolated agentic loop for a single agent.
    The agent sees only its task string — never the caller's history.
    """
    messages: list[dict[str, Any]] = [{"role": "user", "content": task}]
    accumulated_output: dict = {}

    for iteration in range(1, max_iterations + 1):
        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=512,
            tools=tools,
            system=system,
            messages=messages
        )

        if response.stop_reason == "end_turn":
            text = next((b.text for b in response.content if hasattr(b, "text")), "")
            accumulated_output["reply"] = text
            return SubAgentResult(
                agent=name,
                status="success",
                output=accumulated_output,
                iterations=iteration
            )

        elif response.stop_reason == "tool_use":
            messages.append({"role": "assistant", "content": response.content})
            tool_results = []

            for block in response.content:
                if block.type == "tool_use":
                    result = tool_fn(block.name, block.input)
                    accumulated_output.update(result)
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": json.dumps(result)
                    })

            messages.append({"role": "user", "content": tool_results})

        else:
            return SubAgentResult(
                agent=name,
                status="failed",
                output={"error": f"stop_reason={response.stop_reason}"},
                iterations=iteration
            )

    return SubAgentResult(
        agent=name,
        status="budget_exhausted",
        output=accumulated_output,
        iterations=max_iterations
    )


# ── TOOL DEFINITIONS ──────────────────────────────────────────────────────────

ACCOUNT_TOOLS = [
    {
        "name": "get_account_status",
        "description": "Retrieve account and plan status from the CRM.",
        "input_schema": {
            "type": "object",
            "properties": {"customer_id": {"type": "string"}},
            "required": ["customer_id"]
        }
    }
]

INCIDENT_TOOLS = [
    {
        "name": "check_status_page",
        "description": "Check the status page for active incidents.",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": []
        }
    }
]

CLASSIFY_TOOLS = [
    {
        "name": "classify_ticket",
        "description": "Classify the ticket and set routing decision.",
        "input_schema": {
            "type": "object",
            "properties": {
                "decision": {
                    "type": "string",
                    "enum": ["auto_resolve", "escalate", "investigate"]
                },
                "confidence": {"type": "number"},
                "reason": {"type": "string"}
            },
            "required": ["decision", "confidence", "reason"]
        }
    }
]


# ── SIMULATED TOOL HANDLERS ───────────────────────────────────────────────────

def account_tool_fn(name: str, inputs: dict) -> dict:
    if name == "get_account_status":
        return {
            "status": "success",
            "customer_id": inputs.get("customer_id"),
            "plan": "enterprise",
            "account_status": "active",
            "open_invoices": 2
        }
    return {"status": "error", "message": f"Unknown tool: {name}"}


def incident_tool_fn(name: str, inputs: dict) -> dict:
    if name == "check_status_page":
        return {
            "status": "success",
            "overall_status": "operational",
            "active_incidents": []
        }
    return {"status": "error", "message": f"Unknown tool: {name}"}


def classify_tool_fn(name: str, inputs: dict) -> dict:
    if name == "classify_ticket":
        return {
            "status": "success",
            "decision": inputs.get("decision"),
            "confidence": inputs.get("confidence"),
            "reason": inputs.get("reason")
        }
    return {"status": "error", "message": f"Unknown tool: {name}"}


# ══════════════════════════════════════════════════════════════════════════════
# PATTERN 1 — HUB-AND-SPOKE
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class HubSpokeResult:
    status: str
    reply: str
    agent_results: list[SubAgentResult]
    # Explicit audit of what each spoke saw
    account_context_visible_to: list[str] = field(default_factory=list)
    incident_context_visible_to: list[str] = field(default_factory=list)

    def context_visibility_summary(self) -> str:
        return (
            f"AccountAgent: sees ticket only\n"
            f"IncidentAgent: sees ticket only\n"
            f"DraftAgent: sees coordinator-assembled summary (no raw agent outputs)"
        )


def run_hub_and_spoke(ticket: str, customer_id: str) -> HubSpokeResult:
    """
    Hub-and-spoke: coordinator dispatches to each spoke independently.
    Spokes are fully stateless — they cannot see each other.
    The coordinator assembles all results before calling the DraftAgent.
    """
    print("\n" + "━" * 60)
    print("PATTERN: HUB-AND-SPOKE")
    print("━" * 60)

    agent_results: list[SubAgentResult] = []

    # Spoke 1: AccountAgent — sees ticket only
    print("\n[Hub] dispatching AccountAgent (sees: task string only)")
    account_result = run_subagent(
        name="AccountAgent",
        system="You are the account specialist. Retrieve account status for the given customer.",
        task=f"Retrieve account status for customer ID {customer_id}.",
        tools=ACCOUNT_TOOLS,
        tool_fn=account_tool_fn
    )
    agent_results.append(account_result)
    print(f"  AccountAgent: {account_result.status}")

    # Spoke 2: IncidentAgent — sees ticket only (NOT account result)
    print("\n[Hub] dispatching IncidentAgent (sees: task string only)")
    incident_result = run_subagent(
        name="IncidentAgent",
        system="You are the incident specialist. Check the status page for active incidents.",
        task="Check the Resolve status page for active incidents right now.",
        tools=INCIDENT_TOOLS,
        tool_fn=incident_tool_fn
    )
    agent_results.append(incident_result)
    print(f"  IncidentAgent: {incident_result.status}")

    # Coordinator assembles facts — spokes never see each other's output
    # The DraftAgent receives ONLY the coordinator-curated summary
    assembled = (
        f"Customer ID: {customer_id}\n"
        f"Plan: {account_result.output.get('plan', 'unknown')}\n"
        f"Account status: {account_result.output.get('account_status', 'unknown')}\n"
        f"Active incidents: {incident_result.output.get('active_incidents', 'unknown')}\n"
        f"\nOriginal ticket: {ticket}"
    )

    print("\n[Hub] dispatching DraftAgent (sees: coordinator summary only)")
    draft_result = run_subagent(
        name="DraftAgent",
        system=(
            "You are a senior Resolve support specialist. "
            "Draft a professional reply based only on the facts provided."
        ),
        task=assembled,
        tools=[],
        tool_fn=lambda n, i: {}
    )
    agent_results.append(draft_result)

    return HubSpokeResult(
        status="success" if draft_result.status == "success" else "failed",
        reply=draft_result.output.get("reply", ""),
        agent_results=agent_results,
        account_context_visible_to=["coordinator"],    # only hub sees it
        incident_context_visible_to=["coordinator"]    # only hub sees it
    )


# ══════════════════════════════════════════════════════════════════════════════
# PATTERN 2 — PIPELINE
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class PipelineStage:
    name: str
    system: str
    tool_fn: Any
    tools: list[dict]


@dataclass
class PipelineResult:
    status: str
    reply: str
    # Full context at each stage boundary — this is the auditable trail
    context_snapshots: list[dict]
    stages_completed: list[str]

    def context_visibility_summary(self) -> str:
        lines = []
        for i, (stage, snapshot) in enumerate(
            zip(self.stages_completed, self.context_snapshots)
        ):
            lines.append(f"  Stage {i+1} ({stage}) context keys: {list(snapshot.keys())}")
        return "\n".join(lines)


def run_pipeline(ticket: str, customer_id: str) -> PipelineResult:
    """
    Pipeline: context grows as it flows through each stage.

    Stage 1 (EnrichmentAgent)   : adds account_data + incident_data
    Stage 2 (ClassificationAgent): reads enriched context, adds decision + confidence
    Stage 3 (ResponseAgent)     : reads full context, writes final reply

    Each stage sees ALL context accumulated so far — this is the key difference
    from hub-and-spoke where spokes are isolated.
    """
    print("\n" + "━" * 60)
    print("PATTERN: PIPELINE")
    print("━" * 60)

    # Seed the context — grows as it flows through stages
    context: dict = {"ticket": ticket, "customer_id": customer_id}
    context_snapshots: list[dict] = []
    stages_completed: list[str] = []

    # Define the three pipeline stages
    stages = [
        PipelineStage(
            name="EnrichmentAgent",
            system=(
                "You are the enrichment specialist for Resolve. "
                "Retrieve account status and check for active incidents. "
                "Report the facts concisely."
            ),
            tool_fn=lambda n, i: {
                **account_tool_fn(n, i),
                **incident_tool_fn(n, i)
            } if n in ("get_account_status", "check_status_page") else account_tool_fn(n, i),
            tools=ACCOUNT_TOOLS + INCIDENT_TOOLS
        ),
        PipelineStage(
            name="ClassificationAgent",
            system=(
                "You are the classification specialist for Resolve. "
                "Based on the enriched context provided, classify this ticket. "
                "Call classify_ticket with your decision, confidence (0.0–1.0), and reason."
            ),
            tool_fn=classify_tool_fn,
            tools=CLASSIFY_TOOLS
        ),
        PipelineStage(
            name="ResponseAgent",
            system=(
                "You are a senior Resolve support specialist. "
                "Using the full enriched context and classification provided, "
                "draft a professional reply to the customer."
            ),
            tool_fn=lambda n, i: {},
            tools=[]
        )
    ]

    for stage in stages:
        # Each stage task includes the full current context
        task = f"Current context:\n{json.dumps(context, indent=2)}"
        print(f"\n[Pipeline] stage={stage.name}, context_keys={list(context.keys())}")

        result = run_subagent(
            name=stage.name,
            system=stage.system,
            task=task,
            tools=stage.tools,
            tool_fn=stage.tool_fn
        )

        if result.status != "success":
            print(f"  [Pipeline] {stage.name} failed — aborting pipeline")
            return PipelineResult(
                status="failed",
                reply="",
                context_snapshots=context_snapshots,
                stages_completed=stages_completed
            )

        # Merge stage outputs into context — context grows at each boundary
        for key, value in result.output.items():
            if key not in ("reply",):
                context[key] = value

        # reply text goes into context with a stage-specific key
        if result.output.get("reply"):
            context[f"{stage.name.lower()}_reply"] = result.output["reply"]

        # Snapshot context AFTER this stage — this is the auditable trail
        context_snapshots.append(dict(context))
        stages_completed.append(stage.name)
        print(f"  [Pipeline] {stage.name} done. Context now has keys: {list(context.keys())}")

    final_reply = context.get("responseagent_reply", context.get("reply", ""))

    return PipelineResult(
        status="success",
        reply=final_reply,
        context_snapshots=context_snapshots,
        stages_completed=stages_completed
    )


# ── DEMO ──────────────────────────────────────────────────────────────────────

TICKET = (
    "Hi, my invoice INV-2026-0042 shows a charge of $4,200 but I thought "
    "I was on the starter plan. Customer ID is cust_9182. Can you investigate?"
)
CUSTOMER_ID = "cust_9182"

# Run both patterns on the same ticket
hs_result = run_hub_and_spoke(ticket=TICKET, customer_id=CUSTOMER_ID)
pl_result = run_pipeline(ticket=TICKET, customer_id=CUSTOMER_ID)

# ── SIDE-BY-SIDE COMPARISON ───────────────────────────────────────────────────
print("\n" + "=" * 60)
print("PATTERN COMPARISON")
print("=" * 60)

print("\n── Hub-and-Spoke ──")
print(f"Status : {hs_result.status}")
print(f"Agents : {[r.agent for r in hs_result.agent_results]}")
print("Context visibility per agent:")
print(f"  {hs_result.context_visibility_summary()}")
if hs_result.reply:
    print(f"Reply  : {hs_result.reply[:200]}...")

print("\n── Pipeline ──")
print(f"Status : {pl_result.status}")
print(f"Stages : {pl_result.stages_completed}")
print("Context at each stage boundary:")
print(pl_result.context_visibility_summary())
if pl_result.reply:
    print(f"Reply  : {pl_result.reply[:200]}...")

print("\n── Auditable intermediate state ──")
print(f"Hub-and-spoke: coordinator has all results; intermediate state lives in code variables")
print(f"Pipeline     : {len(pl_result.context_snapshots)} context snapshots, one per stage boundary")
if pl_result.context_snapshots:
    print(f"  Snapshot keys by stage:")
    for stage, snapshot in zip(pl_result.stages_completed, pl_result.context_snapshots):
        print(f"    After {stage}: {list(snapshot.keys())}")

print("\n" + "=" * 60)
print("WHEN TO USE EACH PATTERN")
print("=" * 60)
rows = [
    ("Concern",             "Hub-and-Spoke",                 "Pipeline"),
    ("Agent isolation",     "Complete (spokes never share)",  "Partial (each stage sees prior)"),
    ("Audit trail",         "Coordinator-level only",         "Context snapshot per stage"),
    ("Sequential dependency","Not natural (coordinator joins)","Natural (context flows forward)"),
    ("Parallel execution",  "Easy (spokes independent)",      "Hard (stages depend on prior)"),
    ("Adding a new step",   "Add a spoke (no other changes)", "Add a stage (touches context shape)"),
    ("Debugging",           "Inspect coordinator state",      "Inspect context at each snapshot"),
]
col = [30, 32, 32]
for row in rows:
    print("  " + "  ".join(f"{v:<{c}}" for v, c in zip(row, col)))

print("\nKey takeaway:")
print("  Hub-and-spoke maximises agent isolation — spokes cannot influence each other.")
print("  Pipeline maximises context richness — each stage builds on prior findings.")
print("  Pipeline context snapshots give a richer audit trail than hub-and-spoke.")
print("  Choose hub-and-spoke when agents must be independent; pipeline when order matters.")

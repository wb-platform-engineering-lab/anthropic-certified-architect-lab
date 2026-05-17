# Week 6 Lab — Tool Design & MCP Integration

> **Resolve context:** The CRM tool returned `{}` when the CRM was down. The model interpreted silence as a clean slate and told 43 customers their billing was fine. The fix was not a better prompt — it was a better tool. This week you redesign every tool in the integration layer and then lift the most important ones into proper MCP servers with clear capability boundaries.

## Learning Objectives

- Write tool descriptions that give the model everything it needs to call the tool correctly — and know when not to call it
- Implement the three-response-shape pattern: success / access_failure / empty result
- Understand MCP architecture: what a server is, what a transport is, how tools are registered and discovered
- Design MCP server boundaries that prevent wrong-tool-call mistakes structurally
- Configure Claude Code to use an MCP server and understand the difference between `stdio` and `SSE` transports

## Prerequisites

- Week 1 Exercise 4 completed — you have seen the `{}` failure mode and the typed response fix
- Anthropic SDK installed (`pip install anthropic python-dotenv`)
- MCP SDK installed: `pip install mcp` (Exercises 3–4)
- SSE server deps installed: `pip install starlette uvicorn` (Exercise 4 only)
- `.env` with `ANTHROPIC_API_KEY`

---

## Exercises

### Exercise 1 — Tool Descriptions That Work

**File:** `exercise_1_tool_descriptions.py`

**Goal:** Rewrite Resolve's integration tool descriptions using the four-part template and measure the effect on model behaviour during a simulated CRM outage.

**Scenario:** Resolve's original tool descriptions were one-liners copied from function docstrings. They said what the tool does but not when to call it, what each response shape means, or what to do on failure. The model guessed — and guessed wrong.

#### The four-part template

```python
DESCRIPTION_TEMPLATE = """
  1. WHAT: One sentence on what the tool does.
  2. WHEN: When to call it — and when NOT to call it.
  3. SHAPES: What each response status means (success / access_failure / empty).
  4. ON FAILURE: What the agent must do when status is access_failure.
"""
```

#### Old description — one liner, says nothing useful

```python
{
    "name": "get_account_status",
    "description": "Gets the account status for a customer.",
    ...
}
```

What the model does when this returns `access_failure` during a CRM outage: it inspects the payload, finds no data it recognises as an error, and proceeds to draft a reply. Result: customer told their billing is fine during a system outage.

#### New description — four-part, actionable

```python
{
    "name": "get_account_status",
    "description": (
        "WHAT: Retrieves the current account status, plan tier, and authentication "
        "state for a customer from the CRM system.\n"
        "WHEN: Call this first before any account-related action. Do NOT call this "
        "more than once per ticket — the result is cached for the session.\n"
        "SHAPES:\n"
        "  status=success: account found, payload contains plan, active, auth_state.\n"
        "  status=empty: no CRM record for this customer_id. Do NOT proceed — ask "
        "    the customer to verify their account email before continuing.\n"
        "  status=access_failure: CRM system is unavailable. ESCALATE immediately "
        "    to the technical team. Do NOT draft a reply without this data.\n"
        "ON FAILURE: If status is access_failure, stop and escalate. Never tell "
        "a customer their account is fine when you could not verify it."
    ),
    ...
}
```

What the model does when this returns `access_failure`: it reads the `ON FAILURE` instruction and escalates rather than proceeding.

#### The `draft_reply` sequencing rule

The `draft_reply` description uses the WHEN section to encode a prerequisite:

```python
"WHEN: Call this LAST — only after get_account_status has returned "
"status=success. NEVER call draft_reply if get_account_status returned "
"access_failure. This tool should be called at most once per ticket."
```

This does not structurally prevent the model from calling `draft_reply` first — only a `PreCallHook` can do that. But it dramatically reduces the frequency of out-of-order calls. The description is your cheapest guardrail.

#### What to observe

Run the script. For the three CRM-failure tickets (t04, t05, t09), compare the `escalated` field between old and new tools:

```
Ticket  CRM Fails  Old: escalated  New: escalated
t04     YES        False           True
t05     YES        False           True
t09     YES        False           True
```

Same tool implementation, different description — different behaviour.

#### Questions to answer before moving on

1. Why does the WHEN section of a tool description affect model behaviour more than the WHAT section?
2. What is the difference between a description that says "escalate on failure" and a `PreCallHook` that checks for failure?
3. The `draft_reply` description says "call this last". Is this sufficient to prevent the model from calling it first?

#### Try it

Add a fifth tool: `send_email(customer_id, subject, body)`. Write a four-part description that makes it clear this sends a real email (no undo) and should only be called after `draft_reply` has returned `status=success`. Observe whether the model respects the ordering without a hook.

#### Exam rule

> The tool description is the model's only documentation at call time. If the description does not state what to do on `access_failure`, the model will make a reasonable-sounding guess — and that guess will be wrong in ways that are hard to predict. The four-part template (WHAT / WHEN / SHAPES / ON FAILURE) covers every decision the model needs to make about when and how to use the tool.

---

### Exercise 2 — The Three-Response-Shape Pattern

**File:** `exercise_2_three_response_shapes.py`

**Goal:** Implement the three-response-shape pattern as typed dataclasses and verify that each shape produces the correct model routing.

**Scenario:** After the Chapter 4 incident, Resolve's rule became: every tool returns one of three explicitly typed shapes. The model checks `status` first — always — and routes before inspecting the payload.

#### The three shapes

```python
@dataclass
class SuccessResponse:
    status: str = "success"
    data: dict = None           # meaningful result data

@dataclass
class AccessFailureResponse:
    code: str                   # machine-readable error code
    message: str                # human-readable, includes routing instruction
    status: str = "access_failure"

@dataclass
class EmptyResultResponse:
    message: str                # explains what was searched and not found
    status: str = "empty"
```

Each has a `to_dict()` method. The `status` key is always present — the model routes on it before touching any other field.

#### The routing the model performs

```
Tool returns result
      ↓
result["status"] == ?
  ├── "success"        → use result data, continue processing
  ├── "empty"          → handle absence (ask for more info, or note "none found")
  └── "access_failure" → stop, escalate, NEVER proceed with missing data
```

#### Why `{}` is dangerous

```python
# The CRM outage response — the original bug
def get_account_status_v1(customer_id: str, crm_is_down: bool = False) -> dict:
    if crm_is_down:
        return {}   # empty dict — no status field

# What the model sees: {}
# What the model does: inspects payload, finds nothing alarming, continues
# Result: 43 customers told their billing is fine during a CRM outage

# The fixed response
def get_account_status_v2(customer_id: str, crm_is_down: bool = False) -> dict:
    if crm_is_down:
        return AccessFailureResponse(
            code="CRM_TIMEOUT",
            message="CRM did not respond. Do not proceed without account verification.",
        ).to_dict()
    # {"status": "access_failure", "code": "CRM_TIMEOUT", "message": "..."}
```

#### The `partial_result` decision

The exercise includes an inline comment making the case for merging `partial_result` into `access_failure`:
- Partial data is worse than no data for routing decisions (the model may act on 3 of 10 invoices and miss the duplicate in invoice 7)
- A fourth shape means the model must learn four routing rules — increasing the chance of misrouting
- Exception: if the system can reliably quantify what percentage of data is present and the action is safe on partial data, `partial_result` may be justified

#### What to observe

Run the three demo tickets. For each shape, the `verify_routing()` function checks:

```
demo_success        → model uses account data, drafts reply          PASS
demo_empty          → model asks customer to verify account email    PASS
demo_access_failure → model escalates, does NOT draft reply         PASS
```

#### Questions to answer before moving on

1. What does the model do when a tool returns `{}`? Why?
2. Name the three valid `status` values and the action associated with each.
3. When is a fourth shape (`partial_result`) justified? When is it not?

#### Try it

Add a tool that intentionally returns `{}` for a simulated failure (reproducing the original bug). Run it against a ticket. Verify the model proceeds incorrectly. Then fix the tool to return `AccessFailureResponse` and verify it escalates.

#### Exam rule

> Every tool must return a `status` field. Valid values: `"success"`, `"access_failure"`, `"empty"`. `"access_failure"` means the system was unavailable — stop and escalate. `"empty"` means the system responded but found nothing — handle the absence. An empty dict `{}` has no `status` field and is the root cause of the Chapter 4 incident.

---

### Exercise 3 — Building Your First MCP Server

**Files:** `exercise_3_crm_server.py`, `exercise_3_crm_demo.py`, `.claude/mcp_settings.json`

**Goal:** Move Resolve's CRM integration from inline tool definitions to a standalone MCP server, and understand how capability boundaries work structurally.

#### What an MCP server is

An MCP server is a process that exposes tools via a standardised protocol. Claude Code discovers servers from `.claude/mcp_settings.json` and calls their tools exactly like inline tools.

```
Claude Code
    │
    ├── reads .claude/mcp_settings.json
    ├── spawns: python exercise_3_crm_server.py   (stdio process)
    │         │
    │         ├── tool: get_account_status
    │         ├── tool: update_contact_notes
    │         └── tool: list_open_tickets
    │
    └── connects to: http://127.0.0.1:8001/sse    (SSE server — Exercise 4)
```

#### The server — key structure

```python
# pip install mcp
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp import types

server = Server("resolve-crm")

@server.list_tools()
async def list_tools():
    return [
        types.Tool(
            name="get_account_status",
            description="WHAT: ... WHEN: ... SHAPES: ... ON FAILURE: ...",
            inputSchema={"type": "object", "properties": {"customer_id": {"type": "string"}}, "required": ["customer_id"]},
        ),
        # update_contact_notes, list_open_tickets
    ]

@server.call_tool()
async def call_tool(name: str, arguments: dict):
    result = dispatch(name, arguments)
    return [types.TextContent(type="text", text=json.dumps(result))]

async def main():
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream,
                         server.create_initialization_options())

if __name__ == "__main__":
    asyncio.run(main())
```

#### `stdio` transport

`stdio_server()` — the server reads JSON-RPC from stdin and writes to stdout. Claude Code spawns it as a child process.

**Use stdio when:** local process, single client, no network needed. This is the correct default.

**Do not use stdio when:** multiple clients, remote deployment. Use SSE for those.

#### Read-only mode — capability restriction at the server level

```bash
CRM_READ_ONLY=true python exercise_3_crm_server.py
```

With `CRM_READ_ONLY=true`, `update_contact_notes` returns:
```python
{"status": "access_failure", "code": "READ_ONLY_MODE",
 "message": "Server is in read-only mode. update_contact_notes is disabled."}
```

The restriction is enforced in the server — not via a prompt instruction. The model cannot circumvent it.

#### Registering with Claude Code

```json
// .claude/mcp_settings.json
{
  "mcpServers": {
    "resolve-crm": {
      "command": "python",
      "args": ["exercise_3_crm_server.py"],
      "env": {"CRM_READ_ONLY": "false"}
    },
    "resolve-crm-readonly": {
      "command": "python",
      "args": ["exercise_3_crm_server.py"],
      "env": {"CRM_READ_ONLY": "true"}
    }
  }
}
```

#### The boundary effect (`exercise_3_crm_demo.py`)

The demo registers only CRM tool schemas — no billing tools. A billing question cannot produce a `get_billing_history` tool call because the tool is not registered. The model either says it cannot help or uses the available CRM tools to answer what it can.

```python
# Only these tools exist in this session:
tools = [GET_ACCOUNT_STATUS_SCHEMA, UPDATE_NOTES_SCHEMA, LIST_TICKETS_SCHEMA]

# Billing question
response = client.messages.create(tools=tools,
    messages=[{"role": "user", "content": "What is my billing history?"}])

# Model CANNOT call get_billing_history — it is not registered.
# Capability boundary is structural, not a prompt instruction.
```

#### What to observe

Run `exercise_3_crm_demo.py` — no MCP infrastructure needed. For the boundary demo, check that `response` contains no `get_billing_history` tool calls.

#### Questions to answer before moving on

1. What does `stdio_server()` mean for how the server communicates?
2. How does Claude Code discover MCP servers in a project?
3. The `CRM_READ_ONLY` mode disables write tools at the server level. Why is this safer than a prompt instruction?
4. Why can't the model call `get_billing_history` when only CRM tools are registered?

#### Try it

Register both `resolve-crm` and `resolve-crm-readonly` in `.claude/mcp_settings.json`. In Claude Code, try `update_contact_notes` with each. Verify the readonly server returns `access_failure`.

#### Exam rule

> MCP servers are discovered via `.claude/mcp_settings.json`. `stdio` transport means the server communicates over stdin/stdout — correct for local single-client use. What is not registered cannot be called: the capability boundary is structural.

---

### Exercise 4 — MCP Server Design Patterns

**File:** `exercise_4_billing_server.py`

**Goal:** Build the billing MCP server with SSE transport, namespaced read/write tools, typed error responses, and a `server_info` discovery tool.

#### SSE vs `stdio`

| | `stdio` | `SSE` |
|--|--------|-------|
| Transport | stdin / stdout | HTTP (Server-Sent Events) |
| Clients | One | Many |
| Network | No | Yes |
| Right for | Local single-client | Remote or multi-client |

The billing server uses SSE because the billing system is shared across multiple agent instances in production.

#### Read/write namespace separation

```python
"billing_read.get_billing_summary"   # safe to call from any agent
"billing_read.list_invoices"
"billing_write.issue_refund"         # only the refund agent needs this
"billing_write.apply_credit"
```

Operators grant read access broadly, write access narrowly — without any server code changes.

#### Write tool description pattern

```python
"description": (
    "WHAT: Issues a refund for a specific charge.\n"
    "WHEN: Call only when the customer explicitly requests a refund AND "
    "billing_read.get_billing_summary has confirmed the charge. "
    "WRITE OPERATION: modifies billing data. Only call when explicitly "
    "requested by the customer.\n"
    "ON FAILURE: Do not retry. Escalate to billing team with the charge_id."
)
```

The `WRITE OPERATION` warning in the description reduces accidental write calls.

#### Typed errors — never throw exceptions

```python
# WRONG: exception thrown out of the handler
async def issue_refund(customer_id, charge_id, amount):
    if amount > 500:
        raise ValueError("Amount exceeds limit")   # model receives opaque MCP error

# CORRECT: typed access_failure returned
async def issue_refund(customer_id, charge_id, amount):
    if amount <= 0 or amount > 500:
        return {"status": "access_failure", "code": "INVALID_AMOUNT",
                "message": f"Amount {amount} must be between 0 and 500."}
```

An uncaught exception produces an opaque MCP error with no routing information. A typed `access_failure` gives the model what it needs to escalate correctly.

#### The `server_info` discovery tool

```python
# Call before planning a billing workflow
server_info()
→ {
    "status": "success",
    "server": "resolve-billing",
    "capabilities": {
        "billing_read": ["billing_read.get_billing_summary", "billing_read.list_invoices"],
        "billing_write": ["billing_write.issue_refund", "billing_write.apply_credit"],
    },
    "note": "Grant billing_write access only to refund agents."
  }
```

#### Running the SSE server

```bash
python exercise_4_billing_server.py --port 8001
```

Register in `.claude/mcp_settings.json`:
```json
{
  "mcpServers": {
    "resolve-billing": {
      "type": "sse",
      "url": "http://127.0.0.1:8001/sse"
    }
  }
}
```

#### Questions to answer before moving on

1. When is SSE transport appropriate instead of stdio?
2. What does the namespace prefix `billing_write.*` communicate to an operator?
3. What does the model receive when a tool handler throws an uncaught exception?
4. What does `server_info` enable that the `mcp_settings.json` registration alone does not?

#### Try it

Add a `billing_write.void_invoice` tool to the billing server with a four-part description emphasising this is irreversible. Restart the server and verify Claude Code discovers the new tool.

#### Exam rule

> `stdio` is the default for local MCP servers. `SSE` is for remote or multi-client servers. Namespace tools by capability (`billing_read.*`, `billing_write.*`). Tool handlers must never throw exceptions — return typed `access_failure` dicts. A `server_info` tool enables runtime capability discovery.

---

### Exercise 5 — Claude Code's Five Built-In Tools

**File:** `exercise_5_builtin_tools.py`

**Goal:** Know the five built-in tools by name, capability, and constraint — and know when a custom MCP tool is required instead.

**Scenario:** Exam Scenario 4 (*Developer Productivity with Claude Code*) explicitly names all five built-in tools. Getting them wrong costs marks on every question in that scenario.

#### The five tools

```
Tool   Reads?  Writes?  Executes?  Notes
────── ─────── ──────── ─────────  ─────────────────────────────────────────
Read   YES     no       no         Respects .claudeignore; local files only
Write  no      YES      no         Creates parent dirs; does NOT git commit
Bash   no      indirect YES        Prompts for confirmation on destructive cmds
Grep   YES     no       no         Pattern search across file contents
Glob   no      no       no         Returns file paths matching a pattern; no contents
```

**Availability:** Claude Code built-ins only. Not available in `client.messages.create()` or Agent SDK sessions unless explicitly registered.

**Trust model:** Built-in tools operate within Anthropic-defined safety constraints. `Bash` prompts on destructive commands. Custom MCP tools have **no** inherited safety constraints — the developer owns all validation.

#### The codebase exploration agent

The exercise builds an agent that uses all five in sequence on the current directory:

```python
# 1. Glob — find Python files
Glob(pattern="**/*.py") → ["agents/coordinator.py", ...]

# 2. Read — inspect a file
Read(file_path="agents/coordinator.py") → file contents

# 3. Grep — find usages of a function
Grep(pattern="run_subagent", path=".") → [{file, line, content}, ...]

# 4. Bash — run tests
Bash(command="python -m pytest evals/ -q") → "1 passed in 0.3s"

# 5. Write — save report
Write(file_path="exploration_report.txt", content="...") → file created
```

#### Tasks that require a custom MCP tool

| Task | Why built-ins fail | Solution |
|------|--------------------|----------|
| Read from private S3 | `Read` only accesses local filesystem | Custom MCP: `s3_read(bucket, key)` |
| Query internal database | `Bash` cannot safely manage credentials | Custom MCP: `db_query(sql)` |
| Call authenticated internal API | `Bash` embeds credentials in command string (visible in logs) | Custom MCP: `internal_api_call(endpoint, method, body)` |
| Read a `.claudeignore`'d file | `Read` respects `.claudeignore` | Custom MCP tool with explicit file access |

#### The naming conflict rule

```python
# Never name a custom tool the same as a built-in
custom_tools = [{"name": "Read", "description": "Reads from our internal API..."}]

# Claude Code now uses your custom "Read" when it needs to read a file.
# It calls: Read(file_path="/etc/config.yaml")
# Your API handler receives file_path as an API endpoint → silent failure.

# Fix: use distinct names — "crm_read", "s3_read", "db_read"
```

#### Exam questions for Scenario 4

```
Q: An agent needs to read from a private S3 bucket. Which tool?
A: Custom MCP tool — Read only accesses the local filesystem.

Q: An agent needs to find all TypeScript files in a repo. Which tool?
A: Glob with pattern **/*.ts

Q: An agent runs: Bash("rm -rf ./temp"). What does Claude Code do?
A: Prompts for confirmation. Custom MCP tools do NOT inherit this behaviour.

Q: Are the five built-in tools available in client.messages.create()?
A: No — Claude Code session tools only.
```

#### Questions to answer before moving on

1. Name the five Claude Code built-in tools without looking.
2. Which built-in prompts for confirmation on destructive commands?
3. Name two tasks that require a custom MCP tool instead of a built-in.
4. What happens if a custom tool is named `"Read"`?
5. Are the five built-ins available in a direct `client.messages.create()` call?

#### Try it

Add a simulated `s3_read(bucket, key)` tool alongside the five built-ins. Ask the agent: "Find all Python files locally, then also read s3://my-bucket/config.yaml." Verify the agent uses `Glob` for local files and `s3_read` for S3 — not `Read` for the S3 path.

#### Exam rule

> The five Claude Code built-in tools are **Read, Write, Bash, Grep, Glob**. They are available in Claude Code sessions only — not in direct API calls. `Bash` prompts on destructive commands; custom MCP tools have no inherited safety constraints. Never name a custom tool with the same name as a built-in. When a task requires external authentication or access to `.claudeignore`'d files, a custom MCP tool is required.

---

## Lab Completion Checklist

Before moving to Week 7, answer these without looking:

- [ ] Write the four-part tool description template from memory (WHAT / WHEN / SHAPES / ON FAILURE)
- [ ] Name the three response shapes and what the model does for each
- [ ] What does `stdio` transport mean? When would you use `SSE` instead?
- [ ] Where does Claude Code look for MCP server configuration?
- [ ] Why is a thrown exception a worse tool error than a typed `access_failure` response?
- [ ] Name the five Claude Code built-in tools and one sentence on what each does
- [ ] Name two tasks from Scenario 4 that require a custom MCP tool rather than a built-in
- [ ] What happens when a custom tool is defined with the same name as a built-in?

---

## Exam Connections

| Exercise | Domain | Exam Pattern Covered |
|---|---|---|
| 1 | D4 | Tool description quality; four-part template; description vs. hook as guardrail |
| 2 | D4 | Three-response-shape pattern; `status` field routing; `{}` failure mode |
| 3 | D4 | MCP server with `stdio` transport; tool discovery; capability boundaries |
| 4 | D4 | SSE vs stdio; read/write namespace separation; `server_info` discovery; no-exception rule |
| 5 | D4 | Five built-in tools; availability boundary; naming conflicts; custom MCP requirements |

---

## What's Next

Week 7 covers the final domain — context management and reliability. The Chapter 5 incident (the agent that forgot its commitments) is fully reproducible in about 30 lines. These exercises build every layer of the fix.

→ **[Week 7 Lab — Context Management & Reliability](../week-7-context-management/README.md)**

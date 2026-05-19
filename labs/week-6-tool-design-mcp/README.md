# Week 6 Lab — Tool Design & MCP Integration

> **Resolve context:** The CRM tool returned `{}` when the CRM was down. The model interpreted silence as a clean slate and told 43 customers their billing was fine. The fix was not a better prompt — it was a better tool. This week you redesign every tool in the integration layer, then lift the most important ones into MCP servers with clear capability boundaries.

## Learning Objectives

- Write tool descriptions that tell the model exactly when to call a tool, what each response means, and what to do on failure
- Implement the three-response-shape pattern: success / access_failure / empty
- Understand MCP architecture: what a server is, what a transport is, how tools are discovered
- Configure Claude Code to use an MCP server and understand `stdio` vs `SSE` transports
- Know the five Claude Code built-in tools and when a custom MCP tool is required instead

## Prerequisites

- Anthropic SDK installed (`pip install anthropic python-dotenv`)
- MCP SDK installed: `pip install mcp` (Exercises 3–4)
- SSE server deps: `pip install starlette uvicorn` (Exercise 4 only)
- `.env` with `ANTHROPIC_API_KEY`

---

## Exercises

### Exercise 1 — Tool Descriptions That Work

**File:** `exercise_1_tool_descriptions.py`

**Goal:** See how the four-part tool description template changes model behaviour during a CRM outage — same tool implementation, different description.

#### The four-part template

```
WHAT:       One sentence on what the tool does.
WHEN:       When to call it — and when NOT to call it.
SHAPES:     What each response status means (success / empty / access_failure).
ON FAILURE: What the agent must do when status is access_failure.
```

#### Old description — one liner

```python
{"name": "get_account_status", "description": "Gets the account status for a customer."}
```

When the CRM returns `access_failure`, the model finds nothing alarming in the payload, assumes the account is fine, and proceeds to draft a reply.

#### New description — four-part

```python
{
    "name": "get_account_status",
    "description": (
        "WHAT: Retrieves the current account status, plan tier, and auth state.\n"
        "WHEN: Call this before any account-related action. Do NOT call more than once per ticket.\n"
        "SHAPES:\n"
        "  status=success: account found, payload has plan/active/auth_state.\n"
        "  status=empty: no CRM record. Ask the customer to verify their email.\n"
        "  status=access_failure: CRM unavailable. ESCALATE immediately.\n"
        "    Do NOT draft a reply without verified account data.\n"
        "ON FAILURE: If access_failure, stop and escalate. Never tell a customer "
        "their account is fine when you could not verify it."
    ),
    ...
}
```

When the CRM returns `access_failure` with this description, the model reads the `ON FAILURE` instruction and escalates instead of proceeding.

#### What to observe

Run the script. For the two CRM-failure tickets (t03, t04), compare `escalated` between old and new:

```
Ticket  CRM down  Old: escalated  New: escalated
t03     YES       False           True
t04     YES       False           True
```

Same tool implementation. Different description. Different behaviour.

#### Questions to answer before moving on

1. Why does the WHEN section of a description affect model behaviour more than the WHAT section?
2. What is the difference between a description that says "escalate on failure" and a `PreCallHook` that blocks the call?
3. Is the WHEN section of `draft_reply` ("call this last") sufficient to prevent out-of-order calls?

#### Try it

Add a third tool: `send_email(customer_id, subject, body)`. Write a four-part description that makes clear this sends a real email (no undo) and should only be called after `draft_reply` returns success.

#### Exam rule

> The tool description is the model's only documentation at call time. If it does not state what to do on `access_failure`, the model guesses — and guesses wrong. The four-part template (WHAT / WHEN / SHAPES / ON FAILURE) covers every decision the model needs to make about when and how to use the tool.

---

### Exercise 2 — The Three-Response-Shape Pattern

**File:** `exercise_2_three_response_shapes.py`

**Goal:** Implement the three-response-shape pattern and verify each shape produces the correct model routing.

#### The three shapes

```python
# status="success" — system responded, payload has meaningful data
{"status": "success", "plan": "Pro", "active": True, ...}

# status="access_failure" — system unavailable, do not proceed
{"status": "access_failure", "code": "CRM_TIMEOUT", "message": "CRM did not respond."}

# status="empty" — system responded but found nothing
{"status": "empty", "message": "No CRM record found. Ask customer to verify email."}
```

The `status` key is always present. The model routes on it before inspecting any other field.

#### The routing rule

```
status="success"        → use the data, continue
status="empty"          → handle the absence (ask for more info or note "none found")
status="access_failure" → stop, escalate, NEVER proceed with missing data
```

#### Why `{}` is dangerous

```python
# Original bug — CRM timeout returned empty dict
def get_account_status(customer_id, crm_down=False):
    if crm_down:
        return {}   # no status field

# Model sees {}, finds nothing alarming, proceeds
# Result: 43 customers told their billing was fine during a CRM outage

# Fix — return typed access_failure
def get_account_status(customer_id, crm_down=False):
    if crm_down:
        return {
            "status": "access_failure",
            "code": "CRM_TIMEOUT",
            "message": "CRM did not respond. Do not proceed.",
        }
```

#### What to observe

Run the script with 3 demo tickets — one per shape. The `scenario` parameter forces each shape so you can observe routing without a real CRM outage:

```
demo_success        → model uses account data, replies normally
demo_empty          → model asks customer to verify email
demo_access_failure → model escalates, does NOT draft a reply
```

#### Questions to answer before moving on

1. What does the model do when a tool returns `{}`? Why?
2. Name the three valid `status` values and the agent action for each.
3. What is the difference between `"empty"` and `"access_failure"`?

#### Try it

Change the `demo_success` ticket to pass `scenario="access_failure"` and run again. Confirm the model now escalates instead of replying.

#### Exam rule

> Every tool must return a `status` field with one of three values: `"success"`, `"access_failure"`, `"empty"`. The model routes on `status` first. An empty dict `{}` has no `status` field and is the root cause of the Chapter 4 incident.

---

### Exercise 3 — Building Your First MCP Server

**Files:** `exercise_3_crm_server.py`, `exercise_3_crm_demo.py`

**Goal:** Move Resolve's CRM integration from inline tool definitions to a standalone MCP server. Understand how capability boundaries work structurally.

#### What an MCP server is

An MCP server is a process that exposes tools via a standard protocol. Claude Code discovers it from `.claude/mcp_settings.json` and calls its tools exactly like inline tools.

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
            inputSchema={"type": "object", "properties": {"customer_id": {"type": "string"}},
                         "required": ["customer_id"]},
        ),
    ]

@server.call_tool()
async def call_tool(name: str, arguments: dict):
    result = dispatch(name, arguments)
    return [types.TextContent(type="text", text=json.dumps(result))]

async def main():
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())

asyncio.run(main())
```

#### `stdio` transport

`stdio_server()` — the server reads JSON-RPC from stdin and writes to stdout. Claude Code spawns it as a child process.

**Use stdio when:** local process, single client, no network needed. This is the right default.

**Use SSE when:** multiple clients, remote deployment, or shared service.

#### Read-only mode — capability restriction at the server level

```bash
CRM_READ_ONLY=true python exercise_3_crm_server.py
```

With `CRM_READ_ONLY=true`, `update_contact_notes` returns `access_failure` with `code=READ_ONLY_MODE`. The restriction is enforced in the server — the model cannot circumvent it via prompt.

#### Registering with Claude Code

```json
{
  "mcpServers": {
    "resolve-crm": {
      "command": "python",
      "args": ["exercise_3_crm_server.py"],
      "env": {}
    }
  }
}
```

#### The boundary effect (`exercise_3_crm_demo.py`)

The demo runs 3 scenarios using the Anthropic SDK directly (no MCP infrastructure needed):

1. Read account status — verifies `get_account_status` is called
2. Add a note — verifies `update_contact_notes` is called
3. Billing question with only CRM tools registered — model cannot call billing tools

```python
# Only CRM tools are registered
result = run_agent("Show me invoices for cust_001", tools=CRM_TOOLS)
# Model cannot call get_billing_history — it is not registered
# This is structural safety, not a prompt instruction
```

#### What to observe

Run `exercise_3_crm_demo.py`. For Scenario 3, check that no `billing_*` tool appears in `tool_calls`.

#### Questions to answer before moving on

1. What does `stdio_server()` mean for how the server communicates?
2. How does Claude Code discover MCP servers in a project?
3. The `CRM_READ_ONLY` flag disables write tools at the server level. Why is this safer than a prompt instruction?
4. Why can't the model call `get_billing_history` when only CRM tools are registered?

#### Try it

Register the server in `.claude/mcp_settings.json`. Open Claude Code and ask: "What is the account status for cust_001?" — Claude Code will call the tool via MCP automatically.

#### Exam rule

> MCP servers are discovered via `.claude/mcp_settings.json`. `stdio` transport means stdin/stdout — correct for local single-client use. What is not registered cannot be called: the capability boundary is structural, not a prompt instruction.

---

### Exercise 4 — MCP Server Design Patterns

**File:** `exercise_4_billing_server.py`

**Goal:** Build a billing MCP server with SSE transport, namespaced read/write tools, typed error responses, and a `server_info` discovery tool.

#### SSE vs `stdio`

| | `stdio` | `SSE` |
|--|--------|-------|
| Transport | stdin / stdout | HTTP (Server-Sent Events) |
| Clients | One | Many |
| Network | No | Yes |
| Right for | Local single-client | Remote or multi-client |

The billing server uses SSE because the billing system is shared across multiple agent instances.

#### Read/write namespace separation

```python
"billing_read.get_billing_summary"   # safe to call from any agent
"billing_read.list_invoices"
"billing_write.issue_refund"         # only the refund agent needs this
"billing_write.apply_credit"
```

Operators grant read access broadly, write access narrowly — without changing server code.

#### Write tool description pattern

```python
"description": (
    "WHAT: Issues a refund for a specific charge.\n"
    "WHEN: Call only when the customer explicitly requests a refund. "
    "WRITE OPERATION: modifies billing data.\n"
    "ON FAILURE: Do not retry. Escalate to billing team with the charge_id."
)
```

The `WRITE OPERATION` warning reduces accidental write calls.

#### Typed errors — never throw exceptions

```python
# WRONG — model receives opaque MCP error with no routing information
if amount > 500:
    raise ValueError("Amount exceeds limit")

# CORRECT — typed access_failure the model can route on
if amount > 500:
    return {"status": "access_failure", "code": "AMOUNT_EXCEEDS_LIMIT",
            "message": f"Amount {amount} exceeds the maximum allowed refund of 500."}
```

#### The `server_info` discovery tool

```python
server_info()
→ {
    "status": "success",
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
4. What does `server_info` enable that `mcp_settings.json` registration alone does not?

#### Try it

Add a `billing_write.void_invoice` tool with a four-part description that emphasises it is irreversible. Restart the server and verify Claude Code discovers the new tool.

#### Exam rule

> `stdio` is the default for local MCP servers. `SSE` is for remote or multi-client servers. Namespace tools by capability (`billing_read.*`, `billing_write.*`). Handlers must never throw exceptions — return typed `access_failure`. A `server_info` tool enables runtime capability discovery.

---

### Exercise 5 — Claude Code's Five Built-In Tools

**File:** `exercise_5_builtin_tools.py`

**Goal:** Know the five built-in tools by name, capability, and constraint — and know when a custom MCP tool is required instead.

#### The five tools

```
Tool   Reads?  Writes?  Executes?  Notes
────── ─────── ──────── ─────────  ──────────────────────────────────────────
Read   YES     no       no         Respects .claudeignore; local files only
Write  no      YES      no         Creates parent dirs; does NOT git commit
Bash   no      indirect YES        Prompts for confirmation on destructive cmds
Grep   YES     no       no         Pattern search across file contents
Glob   no      no       no         Returns file paths only — no contents
```

**Availability:** Claude Code sessions only. Not in `client.messages.create()` or Agent SDK sessions unless explicitly registered.

**Trust model:** Built-ins have Anthropic-defined safety constraints. Custom MCP tools have **no** inherited safety — you own all validation.

#### When to use each tool

```python
Glob("**/*.py")         # find files by path pattern
Read("agents/main.py")  # read a specific file you already know
Grep("run_subagent")    # find where a symbol appears across files
Bash("pytest -q")       # run tests, git status — things the other tools can't do
Write("report.txt", "…")# save output to a file
```

#### Tasks that require a custom MCP tool

| Task | Why built-ins fail |
|------|-------------------|
| Read from private S3 | `Read` only accesses local filesystem |
| Query a database | `Bash` can't safely manage credentials |
| Call authenticated internal API | `Bash` embeds tokens in command strings (visible in logs) |
| Read a `.claudeignore`'d file | `Read` respects `.claudeignore` |

#### The naming conflict rule

```python
# WRONG — overrides the built-in Read silently
custom_tools = [{"name": "Read", "description": "Reads from our internal API..."}]
# Claude Code now calls your custom "Read" when it needs to read a file → silent failure

# CORRECT — use distinct names
custom_tools = [{"name": "crm_read", ...}]  # or s3_read, db_read
```

#### What to observe

Run the script. An agent uses Glob to find Python files, then Grep to identify which ones import `anthropic`. Watch the tool call sequence printed during the agentic loop.

#### Questions to answer before moving on

1. Name the five Claude Code built-in tools without looking.
2. Which built-in prompts for confirmation on destructive commands?
3. Name two tasks that require a custom MCP tool instead of a built-in.
4. What happens if a custom tool is named `"Read"`?
5. Are the five built-ins available in a direct `client.messages.create()` call?

#### Try it

Add a simulated `s3_read(bucket, key)` tool alongside the five built-ins. Ask the agent to find local Python files and also read `s3://my-bucket/config.yaml`. Verify it uses `Glob` for local files and `s3_read` for S3 — not `Read` for the S3 path.

#### Exam rule

> The five Claude Code built-in tools are **Read, Write, Bash, Grep, Glob**. Available in Claude Code sessions only — not in direct API calls. `Bash` prompts on destructive commands; custom MCP tools do not inherit this. Never name a custom tool the same as a built-in. When a task requires external authentication or access to `.claudeignore`'d files, use a custom MCP tool.

---

## Lab Completion Checklist

Before moving to Week 7, answer these without looking:

- [ ] Write the four-part tool description template from memory (WHAT / WHEN / SHAPES / ON FAILURE)
- [ ] Name the three response shapes and what the model does for each
- [ ] What does `stdio` transport mean? When would you use `SSE` instead?
- [ ] Where does Claude Code look for MCP server configuration?
- [ ] Why is a thrown exception worse than a typed `access_failure` response?
- [ ] Name the five Claude Code built-in tools and one sentence on what each does
- [ ] Name two tasks that require a custom MCP tool rather than a built-in

---

## Exam Connections

| Exercise | Domain | Pattern Covered |
|---|---|---|
| 1 | D4 | Tool description quality; four-part template; description vs. hook as guardrail |
| 2 | D4 | Three-response-shape pattern; `status` field routing; `{}` failure mode |
| 3 | D4 | MCP server with `stdio`; tool discovery; capability boundaries; boundary effect |
| 4 | D4 | SSE vs stdio; read/write namespacing; no-exception rule; `server_info` discovery |
| 5 | D4 | Five built-in tools; availability boundary; naming conflicts; custom MCP use cases |

---

## What's Next

Week 7 covers context management and reliability. The Chapter 5 incident (the agent that forgot its commitments) is fully reproducible in about 30 lines. These exercises build every layer of the fix.

→ **[Week 7 Lab — Context Management & Reliability](../week-7-context-management/README.md)**

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
- Anthropic SDK installed (`pip install anthropic` / `npm install @anthropic-ai/sdk`)
- MCP SDK installed (`pip install mcp` / `npm install @modelcontextprotocol/sdk`)
- `.env` with `ANTHROPIC_API_KEY`

**Languages:** Each exercise is implemented in both Python (`exercise_N.py`) and TypeScript (`exercise_N.ts`).

---

## Exercises

### Exercise 1 — Tool Descriptions That Work

**Goal:** Rewrite the tool descriptions for Resolve's integration tools and measure the effect on model behaviour.

**Scenario:** Resolve's original tool descriptions were one-liners copied from the function docstrings. They described what the tool does but not when to call it, what to do when it fails, or what an empty result means. The model was left to guess — and guessed wrong on the CRM outage.

**You will:**
1. Take four existing tools (get_account_status, get_billing_history, search_knowledge_base, draft_reply) and rewrite their descriptions following a four-part template: **What it does**, **When to call it**, **What each response shape means**, **What to do on failure**
2. Test the old descriptions vs. the new descriptions on the same set of 10 tickets — specifically tickets that involve a simulated CRM timeout
3. Verify that with the new descriptions, the model escalates on `access_failure` instead of proceeding with empty data
4. Write a description for `draft_reply` that makes it structurally clear this tool should only be called after `get_account_status` has succeeded — observe whether the model respects this without a hook

**Key insight:** The tool description is the model's only documentation. If the description does not say what to do on failure, the model will make a reasonable-sounding guess. That guess will be wrong in ways that are hard to predict.

---

### Exercise 2 — The Three-Response-Shape Pattern

**Goal:** Implement the three-response-shape pattern for every integration tool and verify that each shape is handled correctly by the agent.

**Scenario:** After the Chapter 4 incident, Resolve's rule became: every tool returns one of three explicitly typed shapes. The agent receives a `status` field first and routes accordingly — it never inspects the payload before checking `status`.

**You will:**
1. Define the three shapes as TypeScript interfaces or Python dataclasses: `SuccessResponse[T]`, `AccessFailureResponse`, `EmptyResultResponse`
2. Implement `get_account_status` using all three shapes: success (found), empty (customer not in CRM), access_failure (CRM unavailable)
3. Run the agent on three tickets — one triggering each shape — and verify the model's behaviour in each case: replies with data (success), asks for customer ID verification (empty), escalates immediately (access_failure)
4. Introduce a fourth response type that exists in some codebases: `partial_result` (data available but incomplete due to a downstream timeout). Decide whether to add it to the pattern or merge it with `access_failure` — justify your decision in a comment

**Key insight:** The model checks `status` first. If `status` is missing, the model looks at the payload and guesses. If `status` is present and typed, the model's routing is deterministic. Never rely on the model to infer failure from the shape of the data.

---

### Exercise 3 — Building Your First MCP Server

**Goal:** Move Resolve's CRM integration from inline tool definitions to a standalone MCP server.

**Scenario:** Resolve's CRM, billing system, and knowledge base are three separate integrations. As inline tools, they compete for namespace space and their descriptions are scattered across the codebase. As MCP servers, each has a clear owner, a versioned interface, and a boundary the model cannot cross accidentally.

**You will:**
1. Create a `crm-server/` directory with an MCP server that exposes three tools: `get_account_status`, `update_contact_notes`, `list_open_tickets`
2. Implement the server using `stdio` transport — understand that `stdio` means the server communicates over stdin/stdout, making it suitable for local processes
3. Register the server in `.claude/mcp_settings.json` and verify Claude Code can discover and call its tools
4. Test that calling `update_contact_notes` from a read-only context is blocked — implement a read-only mode flag in the server that disables write tools
5. Observe that the model cannot call tools from the billing MCP server when only the CRM MCP server is registered — the boundary is structural

**Key insight:** An MCP server is not just a tool registry — it is a capability boundary. What is not registered cannot be called. This is structurally safer than a prompt instruction saying "only use CRM tools for account questions."

---

### Exercise 4 — MCP Server Design Patterns

**Goal:** Understand the design decisions that make MCP servers maintainable and safe — and the decisions that make them fragile.

**Scenario:** Resolve's second MCP server (billing) was built by a different engineer and made three design choices that Jade later had to fix: tools had no descriptions, errors were thrown as exceptions (not returned as typed failures), and the server mixed read and write tools with no capability separation.

**You will:**
1. Build the `billing-server/` with the lessons from the CRM server applied: every tool has a four-part description, every error is a typed response (never a thrown exception), read and write tools are in separate namespaces (`billing_read.*` and `billing_write.*`)
2. Implement SSE transport as an alternative to stdio — understand when SSE is appropriate (remote servers, multiple clients) vs. stdio (local processes, single client)
3. Test the billing server by connecting to it from both the Anthropic SDK (direct tool use) and Claude Code (registered MCP server) — verify both work with the same server implementation
4. Write a `server_info` tool that returns the server's version, available tools, and their capability categories — this is what an operator uses to understand what a server can do

**Key insight:** `stdio` transport is the right default for local MCP servers. `SSE` is for cases where the server needs to serve multiple clients or run remotely. Choosing SSE for a local server adds network overhead and authentication complexity with no benefit.

---

### Exercise 5 — Claude Code's Five Built-In Tools (Scenario 4)

**Goal:** Know the five Claude Code built-in tools by name, understand exactly what each does, and decide when to use them versus a custom MCP tool.

**Scenario:** Exam Scenario 4 (*Developer Productivity with Claude*) explicitly names five built-in tools: `Read`, `Write`, `Bash`, `Grep`, `Glob`. An engineer building a codebase exploration agent must know when each is the right choice and when a private data source requires a custom MCP tool instead. Getting this wrong costs marks on every question in that scenario.

**You will:**
1. Map each built-in tool to its exact capability and constraints:
   - `Read` — reads a file at a given path; respects `.claudeignore`; does not authenticate to external systems
   - `Write` — writes or overwrites a file; creates directories as needed; does not commit to version control
   - `Bash` — runs a shell command and returns stdout/stderr; scoped to the project directory by default
   - `Grep` — searches file contents for a pattern; faster than running `grep` via `Bash` for large codebases
   - `Glob` — finds files matching a pattern (e.g. `**/*.ts`); returns sorted paths; does not read file contents
2. Build a codebase exploration agent for Scenario 4 that uses all five: `Glob` to find TypeScript files, `Read` to inspect them, `Grep` to find usages of a function, `Bash` to run the test suite, `Write` to generate a summary report
3. Identify which tasks in Scenario 4 require a custom MCP tool instead of a built-in: (a) reading a file from a private S3 bucket, (b) querying an internal database, (c) calling an authenticated internal API
4. Verify the naming conflict rule: define a custom tool named `Read` and confirm it overrides the built-in — understand why this is a source of silent bugs in codebases that define generic tool names
5. Understand the trust boundary: built-in tools operate within Anthropic-defined constraints (e.g. `Bash` prompts for confirmation on destructive commands); custom MCP tools have no inherited safety constraints — the developer owns all validation

**Key insight:** `Read`, `Write`, `Bash`, `Grep`, `Glob` are Claude Code built-ins — not general API tools. They are available when using Claude Code; they are not automatically available in a direct API call or Agent SDK session unless explicitly registered. The exam tests whether you know this boundary.

---

## Lab Completion Checklist

Before moving to Week 7, answer these without looking:

- [ ] Write the four-part template for a tool description from memory
- [ ] Name the three response shapes in the typed pattern and what each means to the model
- [ ] What does `stdio` transport mean in MCP? When would you use `SSE` instead?
- [ ] Where does Claude Code look for MCP server configuration?
- [ ] Why is a thrown exception a worse tool error than a typed `access_failure` response?
- [ ] Name the five Claude Code built-in tools and one sentence on what each does
- [ ] Name two tasks from Scenario 4 that require a custom MCP tool rather than a built-in
- [ ] What happens when a custom tool is defined with the same name as a built-in?

---

## Exam Connections

| Exercise | Domain | Exam Pattern Covered |
|---|---|---|
| 1 | D4 | Tool description quality; four-part template |
| 2 | D4 | Three-response-shape pattern; `status` field routing |
| 3 | D4 | MCP server with `stdio` transport; tool discovery; capability boundaries |
| 4 | D4 | Server design patterns; `SSE` vs. `stdio`; read/write separation |
| 5 | D4 | Built-in tools vs. custom tools; trust model; naming conflicts |

---

## What's Next

Week 7 covers the final domain — context management and reliability. The Chapter 5 incident (the agent that forgot its commitments) is fully reproducible in about 30 lines. These exercises build every layer of the fix.

→ **[Week 7 Lab — Context Management & Reliability](../week-7-context-management/README.md)**

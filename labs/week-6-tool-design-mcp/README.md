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

### Exercise 5 — Claude's Built-In Tools

**Goal:** Understand which tools Claude has natively and when to use them instead of defining custom tools.

**Scenario:** Resolve's early codebase defined a custom `search_web` tool that called the Brave Search API. Marcus noticed during a code review that Claude Code already has a built-in web search capability. The custom tool was redundant and introduced an additional API key to manage.

**You will:**
1. Identify the three categories of built-in Claude tools: computer use tools, web search, and code execution — understand the scope of each
2. Compare a custom `read_file` tool definition against using Claude Code's built-in file reading capability — when is the custom definition needed?
3. Build a scenario where a built-in tool is the correct choice (web search for a knowledge base question that is genuinely external) and one where a custom MCP tool is required (looking up a customer record in Resolve's private CRM)
4. Understand the trust model: built-in tools have Anthropic-defined safety constraints; custom tools do not inherit these constraints — the developer is responsible for safe tool behaviour

**Key insight:** Built-in tools are available in Claude Code but not in direct API calls unless explicitly enabled. If you define a tool with the same name as a built-in, the custom definition takes precedence. This is the source of subtle bugs when naming custom tools.

---

## Lab Completion Checklist

Before moving to Week 7, answer these without looking:

- [ ] Write the four-part template for a tool description from memory
- [ ] Name the three response shapes in the typed pattern and what each means to the model
- [ ] What does `stdio` transport mean in MCP? When would you use `SSE` instead?
- [ ] Where does Claude Code look for MCP server configuration?
- [ ] Why is a thrown exception a worse tool error than a typed `access_failure` response?
- [ ] Name one scenario where using Claude's built-in tools is correct and one where a custom MCP tool is required

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

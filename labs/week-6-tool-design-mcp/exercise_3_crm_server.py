# pip install mcp
"""
exercise_3_crm_server.py — CRM MCP Server (stdio transport)

This is a standalone MCP server that exposes three CRM tools:
  - get_account_status: read account details from the CRM
  - update_contact_notes: write a note to the customer's CRM record
  - list_open_tickets: list all open tickets for a customer

Run this server: python exercise_3_crm_server.py
Register with Claude Code in .claude/mcp_settings.json:
  {
    "mcpServers": {
      "resolve-crm": {
        "command": "python",
        "args": ["labs/week-6-tool-design-mcp/exercise_3_crm_server.py"],
        "env": {}
      }
    }
  }

Transport: stdio — the server reads from stdin and writes to stdout.
stdio is the right choice for local processes (single client, no network).
Do not use SSE for local servers — it adds network overhead with no benefit.

Read-only mode: set READ_ONLY = True to disable write tools (update_contact_notes).
This demonstrates capability restriction at the server level.
"""

import asyncio
import json
import os
from typing import Optional

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp import types
from dotenv import load_dotenv

load_dotenv()

# ---------------------------------------------------------------------------
# Read-only mode — set to True to disable write tools.
# When True, update_contact_notes returns access_failure instead of writing.
# This demonstrates capability restriction at the server level — not via prompt.
# ---------------------------------------------------------------------------
READ_ONLY = os.environ.get("CRM_READ_ONLY", "false").lower() == "true"

server = Server("resolve-crm")

# ---------------------------------------------------------------------------
# Simulated CRM data store
# In production this would call your CRM API.
# ---------------------------------------------------------------------------
CRM_DATA = {
    "cust_001": {
        "name": "Alice Dupont",
        "plan": "Pro",
        "active": True,
        "auth_state": "verified",
        "notes": [],
        "open_tickets": [
            {"id": "tk_001", "subject": "Billing question", "status": "open"},
        ],
    },
    "cust_002": {
        "name": "Bob Chen",
        "plan": "Enterprise",
        "active": True,
        "auth_state": "verified",
        "notes": [],
        "open_tickets": [
            {"id": "tk_002", "subject": "Login issue", "status": "open"},
            {"id": "tk_003", "subject": "Feature request", "status": "open"},
        ],
    },
}


# ---------------------------------------------------------------------------
# Tool handlers
# ---------------------------------------------------------------------------

def _get_account_status(customer_id: str) -> dict:
    """Return CRM account details for the given customer."""
    record = CRM_DATA.get(customer_id)
    if record is None:
        return {
            "status": "empty",
            "message": f"No CRM record for {customer_id}",
        }
    return {
        "status": "success",
        "customer_id": customer_id,
        "name": record["name"],
        "plan": record["plan"],
        "active": record["active"],
        "auth_state": record["auth_state"],
    }


def _update_contact_notes(customer_id: str, note: str) -> dict:
    """Append a note to the customer's CRM record."""
    if READ_ONLY:
        return {
            "status": "access_failure",
            "code": "READ_ONLY_MODE",
            "message": "Server is in read-only mode. update_contact_notes is disabled.",
        }
    record = CRM_DATA.get(customer_id)
    if record is None:
        return {
            "status": "access_failure",
            "code": "NOT_FOUND",
            "message": f"No CRM record for {customer_id}",
        }
    note_id = f"note_{len(record['notes']) + 1:03d}"
    record["notes"].append({"id": note_id, "text": note})
    return {
        "status": "success",
        "customer_id": customer_id,
        "note_id": note_id,
        "message": f"Note appended to {customer_id}'s record.",
    }


def _list_open_tickets(customer_id: str) -> dict:
    """Return all open support tickets for the given customer."""
    record = CRM_DATA.get(customer_id)
    if record is None:
        return {
            "status": "empty",
            "message": f"No CRM record for {customer_id}",
        }
    open_tickets = [t for t in record["open_tickets"] if t["status"] == "open"]
    if not open_tickets:
        return {
            "status": "empty",
            "message": f"No open tickets for {customer_id}",
        }
    return {
        "status": "success",
        "customer_id": customer_id,
        "tickets": open_tickets,
    }


# ---------------------------------------------------------------------------
# MCP server handlers
# ---------------------------------------------------------------------------

@server.list_tools()
async def list_tools() -> list:
    return [
        types.Tool(
            name="get_account_status",
            description=(
                "WHAT: Returns CRM account details for a customer, including name, plan, "
                "active status, and auth state. "
                "WHEN: Call this before any support interaction to verify the customer exists "
                "and retrieve their current plan and verification status. "
                "SHAPES: Returns status=success with name/plan/active/auth_state, "
                "or status=empty if no CRM record exists for the given customer_id. "
                "ON FAILURE: If status=empty, do not proceed — the customer may not be "
                "in the system yet or the ID may be incorrect."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "customer_id": {
                        "type": "string",
                        "description": "The unique customer identifier (e.g. cust_001).",
                    },
                },
                "required": ["customer_id"],
            },
        ),
        types.Tool(
            name="update_contact_notes",
            description=(
                "WHAT: Appends a free-text note to the customer's CRM record. "
                "WHEN: Call this after resolving a support interaction to log what was "
                "discussed or actioned. Do not call for read-only queries. "
                "NOTE: This tool is disabled when the server is running in read-only mode "
                "and will return status=access_failure with code=READ_ONLY_MODE. "
                "SHAPES: Returns status=success with note_id, "
                "status=access_failure with code=READ_ONLY_MODE when disabled, "
                "or status=access_failure with code=NOT_FOUND if the customer does not exist. "
                "ON FAILURE: If access_failure is returned, do not retry — report the "
                "restriction to the caller."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "customer_id": {
                        "type": "string",
                        "description": "The unique customer identifier.",
                    },
                    "note": {
                        "type": "string",
                        "description": "The note text to append to the customer record.",
                    },
                },
                "required": ["customer_id", "note"],
            },
        ),
        types.Tool(
            name="list_open_tickets",
            description=(
                "WHAT: Returns all open support tickets for a customer. "
                "WHEN: Call this to check whether the customer has existing open tickets "
                "before creating a new one, or to give the agent context on ongoing issues. "
                "SHAPES: Returns status=success with a tickets list, "
                "or status=empty if there are no open tickets or no CRM record for the customer. "
                "ON FAILURE: If status=empty, proceed as if there are no open tickets — "
                "this is a normal state, not an error."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "customer_id": {
                        "type": "string",
                        "description": "The unique customer identifier.",
                    },
                },
                "required": ["customer_id"],
            },
        ),
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list:
    if name == "get_account_status":
        customer_id = arguments.get("customer_id", "")
        result = _get_account_status(customer_id)
    elif name == "update_contact_notes":
        customer_id = arguments.get("customer_id", "")
        note = arguments.get("note", "")
        result = _update_contact_notes(customer_id, note)
    elif name == "list_open_tickets":
        customer_id = arguments.get("customer_id", "")
        result = _list_open_tickets(customer_id)
    else:
        result = {
            "status": "access_failure",
            "code": "UNKNOWN_TOOL",
            "message": f"Unknown tool: {name}",
        }
    return [types.TextContent(type="text", text=json.dumps(result))]


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

async def main():
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options(),
        )


if __name__ == "__main__":
    asyncio.run(main())

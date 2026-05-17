# pip install mcp starlette uvicorn
"""
exercise_4_billing_server.py — Billing MCP Server (SSE transport)

This server uses SSE (Server-Sent Events) transport instead of stdio.
SSE is appropriate when:
  - The server needs to serve multiple clients simultaneously
  - The server runs remotely (different machine or container)
  - The client connects over HTTP

SSE is NOT appropriate for local single-client use — it adds HTTP overhead
and authentication complexity with no benefit over stdio.

For Resolve's billing server, SSE is used because the billing system
is a shared service accessed by multiple agent instances in production.

Tools are namespaced:
  billing_read.get_billing_summary    — read-only
  billing_read.list_invoices           — read-only
  billing_write.issue_refund           — write (modifies data)
  billing_write.apply_credit           — write (modifies data)

Read/write separation lets operators grant read-only access to most agents
and write access only to the refund processing agent.

Run this server: python exercise_4_billing_server.py --port 8001
Connect from Claude Code: add to .claude/mcp_settings.json as SSE transport.
"""

import asyncio
import argparse
import json
from typing import Optional

from mcp.server import Server
from mcp.server.sse import SseServerTransport
from mcp import types
from starlette.applications import Starlette
from starlette.routing import Route
from starlette.requests import Request
import uvicorn
from dotenv import load_dotenv

load_dotenv()

server = Server("resolve-billing")

# ---------------------------------------------------------------------------
# Simulated billing data
# In production this would call your billing API (Stripe, Chargebee, etc.)
# ---------------------------------------------------------------------------
BILLING_DATA = {
    "cust_001": {
        "invoices": [
            {"id": "inv_001", "amount": 49.0, "date": "2026-05-01", "paid": True},
            {"id": "inv_002", "amount": 49.0, "date": "2026-04-01", "paid": True},
        ],
        "duplicate_charge": None,
        "credit_balance": 0.0,
    },
    "cust_002": {
        "invoices": [
            {"id": "inv_003", "amount": 299.0, "date": "2026-05-01", "paid": True},
            {"id": "inv_004", "amount": 299.0, "date": "2026-05-01", "paid": True},  # duplicate
        ],
        "duplicate_charge": {"charge_id": "inv_004", "amount": 299.0},
        "credit_balance": 0.0,
    },
}


# ---------------------------------------------------------------------------
# Tool handlers
# ---------------------------------------------------------------------------

def _get_billing_summary(customer_id: str) -> dict:
    """Return a high-level billing summary for the customer."""
    record = BILLING_DATA.get(customer_id)
    if record is None:
        return {
            "status": "empty",
            "message": f"No billing record for {customer_id}",
        }
    return {
        "status": "success",
        "customer_id": customer_id,
        "invoice_count": len(record["invoices"]),
        "total_paid": sum(inv["amount"] for inv in record["invoices"] if inv["paid"]),
        "credit_balance": record["credit_balance"],
        "duplicate_charge": record["duplicate_charge"],
    }


def _list_invoices(customer_id: str, limit: Optional[int] = None) -> dict:
    """Return invoices for the customer, optionally limited."""
    record = BILLING_DATA.get(customer_id)
    if record is None:
        return {
            "status": "empty",
            "message": f"No billing record for {customer_id}",
        }
    invoices = record["invoices"]
    if limit is not None and limit > 0:
        invoices = invoices[:limit]
    if not invoices:
        return {
            "status": "empty",
            "message": f"No invoices found for {customer_id}",
        }
    return {
        "status": "success",
        "customer_id": customer_id,
        "invoices": invoices,
    }


def _issue_refund(customer_id: str, charge_id: str, amount: float) -> dict:
    """Issue a refund for a specific charge. Amount must be > 0 and <= 500."""
    if amount <= 0:
        return {
            "status": "access_failure",
            "code": "INVALID_AMOUNT",
            "message": f"Refund amount must be greater than 0. Got: {amount}",
        }
    if amount > 500:
        return {
            "status": "access_failure",
            "code": "AMOUNT_EXCEEDS_LIMIT",
            "message": f"Refund amount {amount} exceeds the maximum allowed refund of 500.",
        }
    record = BILLING_DATA.get(customer_id)
    if record is None:
        return {
            "status": "access_failure",
            "code": "NOT_FOUND",
            "message": f"No billing record for {customer_id}",
        }
    # Find the charge
    invoice = next((inv for inv in record["invoices"] if inv["id"] == charge_id), None)
    if invoice is None:
        return {
            "status": "access_failure",
            "code": "CHARGE_NOT_FOUND",
            "message": f"Charge {charge_id} not found for customer {customer_id}",
        }
    # In production: call refund API here
    refund_id = f"ref_{charge_id}"
    return {
        "status": "success",
        "customer_id": customer_id,
        "refund_id": refund_id,
        "charge_id": charge_id,
        "amount": amount,
        "message": f"Refund of {amount} issued for charge {charge_id}.",
    }


def _apply_credit(customer_id: str, amount: float, reason: str) -> dict:
    """Apply a credit to the customer's account balance."""
    if amount <= 0:
        return {
            "status": "access_failure",
            "code": "INVALID_AMOUNT",
            "message": f"Credit amount must be greater than 0. Got: {amount}",
        }
    record = BILLING_DATA.get(customer_id)
    if record is None:
        return {
            "status": "access_failure",
            "code": "NOT_FOUND",
            "message": f"No billing record for {customer_id}",
        }
    record["credit_balance"] += amount
    return {
        "status": "success",
        "customer_id": customer_id,
        "credit_applied": amount,
        "new_credit_balance": record["credit_balance"],
        "reason": reason,
        "message": f"Credit of {amount} applied to {customer_id}'s account.",
    }


def _server_info() -> dict:
    """Return metadata about this MCP server and its capabilities."""
    return {
        "status": "success",
        "server": "resolve-billing",
        "version": "1.0.0",
        "transport": "sse",
        "capabilities": {
            "billing_read": [
                "billing_read.get_billing_summary",
                "billing_read.list_invoices",
            ],
            "billing_write": [
                "billing_write.issue_refund",
                "billing_write.apply_credit",
            ],
            "meta": ["server_info"],
        },
        "note": "Write tools modify billing data. Grant billing_write access only to refund agents.",
    }


# ---------------------------------------------------------------------------
# MCP server handlers
# ---------------------------------------------------------------------------

@server.list_tools()
async def list_tools() -> list:
    return [
        types.Tool(
            name="billing_read.get_billing_summary",
            description=(
                "WHAT: Returns a high-level billing summary for a customer, including total paid, "
                "credit balance, invoice count, and whether a duplicate charge exists. "
                "WHEN: Call this first when a customer reports a billing problem — it surfaces "
                "the most actionable billing signals in a single call. "
                "SHAPES: Returns status=success with invoice_count, total_paid, credit_balance, "
                "and duplicate_charge (null or object), or status=empty if no billing record exists. "
                "ON FAILURE: If status=empty, the customer may not be in the billing system yet."
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
        types.Tool(
            name="billing_read.list_invoices",
            description=(
                "WHAT: Returns the invoice history for a customer, optionally limited to the N most recent. "
                "WHEN: Call this when the customer asks about specific invoices or payment history. "
                "Use limit to avoid returning more data than needed. "
                "SHAPES: Returns status=success with an invoices list, "
                "or status=empty if there are no invoices or no billing record. "
                "ON FAILURE: If status=empty, report that no invoices were found."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "customer_id": {
                        "type": "string",
                        "description": "The unique customer identifier.",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum number of invoices to return. Omit for all.",
                    },
                },
                "required": ["customer_id"],
            },
        ),
        types.Tool(
            name="billing_write.issue_refund",
            description=(
                "WHAT: Issues a refund to the customer for a specific charge. "
                "WRITE OPERATION: modifies billing data. Only call when explicitly requested by the customer. "
                "WHEN: Call this after confirming with the customer which charge to refund and the amount. "
                "Amount must be > 0 and <= 500. "
                "SHAPES: Returns status=success with refund_id, "
                "status=access_failure with code=INVALID_AMOUNT if amount is 0 or negative, "
                "status=access_failure with code=AMOUNT_EXCEEDS_LIMIT if amount > 500, "
                "status=access_failure with code=NOT_FOUND if customer does not exist, "
                "or status=access_failure with code=CHARGE_NOT_FOUND if charge_id is invalid. "
                "ON FAILURE: Do not retry a failed refund without confirming the issue with the customer."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "customer_id": {
                        "type": "string",
                        "description": "The unique customer identifier.",
                    },
                    "charge_id": {
                        "type": "string",
                        "description": "The invoice or charge ID to refund (e.g. inv_004).",
                    },
                    "amount": {
                        "type": "number",
                        "description": "Refund amount in the account currency. Must be > 0 and <= 500.",
                    },
                },
                "required": ["customer_id", "charge_id", "amount"],
            },
        ),
        types.Tool(
            name="billing_write.apply_credit",
            description=(
                "WHAT: Applies a credit to the customer's account balance. "
                "WRITE OPERATION: modifies billing data. Only call when explicitly requested by the customer. "
                "WHEN: Call this when issuing goodwill credit or compensating for a service disruption. "
                "Amount must be > 0. "
                "SHAPES: Returns status=success with credit_applied and new_credit_balance, "
                "status=access_failure with code=INVALID_AMOUNT if amount <= 0, "
                "or status=access_failure with code=NOT_FOUND if customer does not exist. "
                "ON FAILURE: Do not apply credit repeatedly — check the new_credit_balance in the response."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "customer_id": {
                        "type": "string",
                        "description": "The unique customer identifier.",
                    },
                    "amount": {
                        "type": "number",
                        "description": "Credit amount to apply. Must be > 0.",
                    },
                    "reason": {
                        "type": "string",
                        "description": "Reason for applying the credit (logged for audit).",
                    },
                },
                "required": ["customer_id", "amount", "reason"],
            },
        ),
        types.Tool(
            name="server_info",
            description=(
                "WHAT: Returns metadata about this MCP server. "
                "WHEN: Call this to discover what tools are available and their capability categories "
                "before planning a billing workflow."
            ),
            inputSchema={
                "type": "object",
                "properties": {},
                "required": [],
            },
        ),
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list:
    if name == "billing_read.get_billing_summary":
        result = _get_billing_summary(arguments.get("customer_id", ""))
    elif name == "billing_read.list_invoices":
        result = _list_invoices(
            arguments.get("customer_id", ""),
            arguments.get("limit"),
        )
    elif name == "billing_write.issue_refund":
        result = _issue_refund(
            arguments.get("customer_id", ""),
            arguments.get("charge_id", ""),
            float(arguments.get("amount", 0)),
        )
    elif name == "billing_write.apply_credit":
        result = _apply_credit(
            arguments.get("customer_id", ""),
            float(arguments.get("amount", 0)),
            arguments.get("reason", ""),
        )
    elif name == "server_info":
        result = _server_info()
    else:
        result = {
            "status": "access_failure",
            "code": "UNKNOWN_TOOL",
            "message": f"Unknown tool: {name}",
        }
    return [types.TextContent(type="text", text=json.dumps(result))]


# ---------------------------------------------------------------------------
# SSE transport setup
# ---------------------------------------------------------------------------

sse = SseServerTransport("/messages/")


async def handle_sse(request: Request):
    async with sse.connect_sse(
        request.scope,
        request.receive,
        request._send,
    ) as streams:
        await server.run(
            streams[0],
            streams[1],
            server.create_initialization_options(),
        )


async def handle_messages(request: Request):
    await sse.handle_post_message(request.scope, request.receive, request._send)


starlette_app = Starlette(
    routes=[
        Route("/sse", endpoint=handle_sse),
        Route("/messages/", endpoint=handle_messages, methods=["POST"]),
    ]
)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Resolve Billing MCP Server (SSE)")
    parser.add_argument("--port", type=int, default=8001)
    parser.add_argument("--host", default="127.0.0.1")
    args = parser.parse_args()

    print(f"Billing MCP server starting on http://{args.host}:{args.port}/sse")
    print("Connect from Claude Code using SSE transport.")
    uvicorn.run(starlette_app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()

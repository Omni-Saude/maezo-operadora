"""MCP WhatsApp — in-process MCP server for WhatsApp Business API (ADR-0022).

Exposes 2 tools:
- send_message(to, text) -> response_dict
- verify_webhook(challenge) -> challenge_str

Uses httpx.AsyncClient for REST calls to WhatsApp Cloud API.
WhatsApp is BLOCKED for PHI per ADR-0006 — only non-PHI messages allowed.
"""

from maezo.tools.mcp_whatsapp.server import WhatsAppServer

__all__ = ["WhatsAppServer"]

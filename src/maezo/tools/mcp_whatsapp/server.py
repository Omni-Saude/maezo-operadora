"""MCP WhatsApp server — WhatsApp Business API client.

Provides send_message(to, text) and verify_webhook(challenge).
Uses httpx.AsyncClient for async HTTP calls to WhatsApp Cloud API (v18.0).

WhatsApp is a BLOCKED channel for PHI per ADR-0006 — the ToolRegistry
PEP enforces this at the gateway level, not here.

Tools:
- send_message(to, text) -> response_dict
- verify_webhook(challenge) -> challenge_str
"""

from __future__ import annotations

from typing import Any

import httpx
import structlog
from pydantic_settings import BaseSettings

logger = structlog.get_logger(__name__)


class WhatsAppSettings(BaseSettings):
    """Configuration for the WhatsApp Business API.

    Environment variables prefixed with WHATSAPP_ (default).
    """

    model_config = {"env_prefix": "WHATSAPP_", "extra": "ignore"}

    base_url: str = "https://graph.facebook.com/v18.0"
    whatsapp_token: str = ""
    whatsapp_app_secret: str = ""
    whatsapp_verify_token: str = ""


class WhatsAppServer:
    """MCP server for WhatsApp Business API — in-process (ADR-0022).

    Wraps the WhatsApp Cloud API with async httpx calls.
    Tools are registered via register_tools() for integration
    with the ToolRegistry (ADR-0016).

    Usage:
        server = WhatsAppServer()
        result = await server.send_message("5511999999999", "Ola!")
        challenge = server.verify_webhook("verify_token", "challenge_str")
    """

    def __init__(self, settings: WhatsAppSettings | None = None) -> None:
        """Initialize the WhatsApp server.

        Args:
            settings: Optional WhatsAppSettings; defaults to empty tokens
                      (must be configured via env vars in production).
        """
        self._settings = settings or WhatsAppSettings()
        logger.info("whatsapp_server_initialized")

    def list_tools(self) -> list[dict[str, str]]:
        """Return tool definitions for registration with ToolRegistry.

        Returns:
            List of tool dicts with 'name' and 'description' keys.
        """
        return [
            {
                "name": "send_message",
                "description": "Send a WhatsApp text message to a phone number.",
            },
            {
                "name": "verify_webhook",
                "description": "Verify a WhatsApp webhook challenge for endpoint registration.",
            },
        ]

    def register_tools(self, registry: Any) -> None:
        """Register WhatsApp tools with the given ToolRegistry (ADR-0022, ADR-0016).

        Args:
            registry: A ToolRegistry instance that accepts register(name, handler).
        """
        registry.register("send_message", self.send_message)
        registry.register("verify_webhook", self.verify_webhook)
        logger.info("whatsapp_tools_registered", count=2)

    def verify_webhook(self, verify_token: str, challenge: str) -> str:
        """Verify a WhatsApp webhook challenge (GET request from Meta).

        Used during WhatsApp webhook endpoint registration.
        Must match the configured WHATSAPP_VERIFY_TOKEN.

        Args:
            verify_token: The hub.verify_token from the webhook request.
            challenge: The hub.challenge from the webhook request.

        Returns:
            The challenge string if verification succeeds.

        Raises:
            ValueError: If the verify_token does not match the configured token.
        """
        if verify_token != self._settings.whatsapp_verify_token:
            raise ValueError(f"Invalid verify token (expected {self._settings.whatsapp_verify_token!r})")

        logger.info("whatsapp_webhook_verified")
        return challenge

    async def send_message(
        self,
        to: str,
        text: str,
    ) -> dict[str, Any]:
        """Send a WhatsApp text message via the Cloud API.

        POST /{phone_number_id}/messages

        Args:
            to: Recipient phone number in international format (e.g., '5511999999999').
            text: The message body text.

        Returns:
            The API response as a dict containing message IDs.

        Raises:
            httpx.HTTPStatusError: If the WhatsApp API returns an error.
        """
        url = f"{self._settings.base_url}/{self._settings.whatsapp_token}/messages"

        payload = {
            "messaging_product": "whatsapp",
            "to": to,
            "type": "text",
            "text": {"body": text},
        }
        headers = {
            "Authorization": f"Bearer {self._settings.whatsapp_token}",
            "Content-Type": "application/json",
        }

        logger.info("whatsapp_send_message", to=to)

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(url, json=payload, headers=headers)
            response.raise_for_status()
            data: dict[str, Any] = response.json()

        logger.info("whatsapp_message_sent", message_id=data.get("messages", [{}])[0].get("id"))
        return data

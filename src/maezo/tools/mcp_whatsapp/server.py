"""MCP WhatsApp server — WhatsApp Business API client.

Provides send_message(to, text) and verify_webhook(verify_token, challenge).
Uses httpx.AsyncClient for async HTTP calls to WhatsApp Cloud API (v18.0).

WhatsApp is a BLOCKED channel for PHI per ADR-0006 — the ToolRegistry
PEP enforces this at the gateway level, not here.

Credential handling (auditoria 09, achados 9.3/9.4) is specified per method.
"""

from __future__ import annotations

import hmac
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
    phone_number_id: str = ""  # NOT a secret: the sender number's Graph id (achado 9.4)
    whatsapp_token: str = ""
    whatsapp_app_secret: str = ""
    whatsapp_verify_token: str = ""


class WhatsAppServer:
    """MCP server for WhatsApp Business API — in-process (ADR-0022).

    Wraps the WhatsApp Cloud API with async httpx calls.
    Tools are registered via register_tools() for integration
    with the ToolRegistry (ADR-0016).

    Usage:
        server = WhatsAppServer()  # WHATSAPP_* env; unset credentials => refuses
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
        """Verify a WhatsApp webhook challenge (GET from Meta), returning the challenge.

        Compares the supplied token with the configured WHATSAPP_VERIFY_TOKEN via
        `hmac.compare_digest` over UTF-8 bytes — never a plain `==` on a secret.

        Args:
            verify_token: The hub.verify_token from the webhook request.
            challenge: The hub.challenge from the webhook request.

        Raises:
            ValueError: on ANY refusal — an UNSET configured token refuses
                everything, and the message names NEITHER token (achado 9.3),
                because it reaches structlog and the caller's HTTP body.
        """
        expected = self._settings.whatsapp_verify_token.encode("utf-8")
        if not expected or not hmac.compare_digest(verify_token.encode("utf-8"), expected):
            raise ValueError("Invalid verify token")

        logger.info("whatsapp_webhook_verified")
        return challenge

    async def send_message(
        self,
        to: str,
        text: str,
    ) -> dict[str, Any]:
        """Send a WhatsApp text message via the Cloud API.

        `POST {base_url}/{phone_number_id}/messages` — the shape the Graph API
        actually documents. The WABA token is a BEARER credential and travels
        ONLY in the `Authorization` header (achado 9.4). The previous build
        interpolated it into the URL PATH in place of the phone-number id, which
        (a) is not a real Graph endpoint and (b) published the secret to every
        access log and proxy on the way — and to `httpx.HTTPStatusError`, whose
        message quotes the request URL verbatim.

        Fail-closed: an unset `phone_number_id` (or an unset token) REFUSES the
        send. There is deliberately no fallback to the token, and none to the
        `{base_url}//messages` an empty path segment would otherwise produce.

        The recipient is NOT logged. On the live path `to` is the RAW phone
        number (`webhooks/whatsapp/dispatch.py`'s `_ScopedWhatsAppSender` passes
        `raw_to`), so an INFO line carrying it would put a raw identifier in the
        general zone — against ADR-0006 and against the I-3 claim recorded in
        `docs/design/wave1-effect-chokepoint.md`. The sender's own
        `phone_number_id` is logged instead: an operadora-side business id, not
        a beneficiary identifier and not a secret.

        Args:
            to: Recipient phone number in international format (e.g., '5511999999999').
            text: The message body text.

        Returns:
            The API response as a dict containing message IDs.

        Raises:
            ValueError: If `phone_number_id` or the WABA token is unconfigured.
                Neither refusal names a configured value.
            httpx.HTTPStatusError: If the WhatsApp API returns an error.
        """
        phone_number_id = self._settings.phone_number_id
        if not phone_number_id:
            raise ValueError("WhatsApp phone_number_id is not configured — refusing to send")
        if not self._settings.whatsapp_token:
            raise ValueError("WhatsApp access token is not configured — refusing to send")

        url = f"{self._settings.base_url}/{phone_number_id}/messages"

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

        logger.info("whatsapp_send_message", phone_number_id=phone_number_id)

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(url, json=payload, headers=headers)
            response.raise_for_status()
            data: dict[str, Any] = response.json()

        logger.info("whatsapp_message_sent", message_id=(data.get("messages") or [{}])[0].get("id"))
        return data

"""MCP WhatsApp server — WhatsApp Business API client (ADR-0006: WhatsApp is a BLOCKED
channel for PHI; the ToolRegistry PEP enforces that at the gateway, not here). Credential
handling (auditoria 09, achados 9.3/9.4) is specified per method. OPS DISCLOSURE: reply
path inoperative in Helm until `WHATSAPP_PHONE_NUMBER_ID` is provisioned (owner-gated,
see OWNER-DECISIONS) — no deployment injects it; row in `docs/review-queue.md`.

OUTBOUND IDEMPOTENCY (gap `WEBHOOK-WAMID-DEDUP`, owner decision R-071, 2026-09-04): this client
is the second leg of the "uma entrega so" guard — `send_message` can claim a durable key before
the POST so a re-delivered inbound webhook cannot produce a second reply to the beneficiary. The
registry is injected (`dedup=`), never constructed here: this module owns credentials and HTTP,
not connection pools.
"""

from __future__ import annotations

import hmac
from typing import Any

import httpx
import structlog
from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from maezo.platform.driver_idempotency import DedupRegistry

logger = structlog.get_logger(__name__)


def _secret(suffix: str) -> Any:
    """CANONICAL `WHATSAPP_<suffix>` env name + the field it binds; never rendered."""
    names = AliasChoices(f"WHATSAPP_{suffix}", f"whatsapp_{suffix.lower()}")
    return Field(default="", validation_alias=names, repr=False, exclude=True)


class WhatsAppSettings(BaseSettings):
    """Cloud-API config read from the CANONICAL `WHATSAPP_*` env names (see `_secret`)."""

    model_config = SettingsConfigDict(env_prefix="WHATSAPP_", extra="ignore")

    base_url: str = "https://graph.facebook.com/v18.0"
    phone_number_id: str = ""  # NOT a secret: the sender number's Graph id (achado 9.4)
    whatsapp_token: str = _secret("TOKEN")
    whatsapp_app_secret: str = _secret("APP_SECRET")
    whatsapp_verify_token: str = _secret("VERIFY_TOKEN")


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

    def __init__(
        self,
        settings: WhatsAppSettings | None = None,
        *,
        dedup: DedupRegistry | None = None,
    ) -> None:
        """Initialize the WhatsApp server.

        Args:
            settings: Optional WhatsAppSettings; defaults to empty tokens
                      (must be configured via env vars in production).
            dedup: Optional durable idempotency registry (gap `WEBHOOK-WAMID-DEDUP`,
                   owner decision R-071: "mais idempotencia na saida `send`").
                   When present, `send_message` calls that carry an
                   `idempotency_key` are claimed BEFORE the HTTP POST, so the
                   same logical reply cannot be delivered twice to the
                   beneficiary. When absent — every caller that passes no key,
                   plus the unit suites — the send path is byte-identical to the
                   pre-dedup one.
        """
        self._settings = settings or WhatsAppSettings()
        self._dedup = dedup
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
        *,
        idempotency_key: str | None = None,
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

        OPS DISCLOSURE, stated because the fail-closed branch below is REACHED in
        production today: reply path inoperative in Helm until
        `WHATSAPP_PHONE_NUMBER_ID` is provisioned (owner-gated, see OWNER-DECISIONS).
        `deploy/helm/maezo-tenant/templates/deployment-webhook-receiver.yaml:38,47,52`
        injects `WHATSAPP_TOKEN`, `WHATSAPP_APP_SECRET` and `WHATSAPP_VERIFY_TOKEN`
        and NOTHING injects `WHATSAPP_PHONE_NUMBER_ID` (only `.env.example:72`, i.e.
        local dev), so every Helena reply through `dispatch.py`'s
        `_ScopedWhatsAppSender` refuses here until the secret `maezo-whatsapp-config`
        carries it. Editing `deploy/` is owner-gated and was NOT done in this package;
        the follow-up is tracked in `docs/review-queue.md`. Refusing is the correct
        failure mode — it is disclosed, not silent.

        The recipient is NOT logged. On the live path `to` is the RAW phone
        number (`webhooks/whatsapp/dispatch.py`'s `_ScopedWhatsAppSender` passes
        `raw_to`), so an INFO line carrying it would put a raw identifier in the
        general zone — against ADR-0006 and against the I-3 claim recorded in
        `docs/design/wave1-effect-chokepoint.md`. The sender's own
        `phone_number_id` is logged instead: an operadora-side business id, not
        a beneficiary identifier and not a secret.

        OUTBOUND IDEMPOTENCY (gap `WEBHOOK-WAMID-DEDUP`, owner decision R-071 —
        "mais idempotencia na saida `send`, tratadas como uma entrega so"). When a
        `dedup` registry is wired AND the caller supplies an `idempotency_key`,
        the key is CLAIMED durably before the POST and sealed after it:

          * first send for that key -> the POST happens, then the claim is sealed;
          * a repeat inside the TTL -> NO POST at all, and the return value says
            `{"suppressed_duplicate": True}` — deliberately NOT a fabricated Cloud
            API response with an invented `messages[].id`, because a caller that
            treated it as a real delivery would be reading an id no message has;
          * a failed POST -> the claim is WITHDRAWN, so the legitimate retry of a
            transient failure is not deduped into silent loss.

        The key comes from the inbound `wamid`
        (`platform/webhooks/whatsapp/dedup.py::WhatsAppDedupGuard.outbound_key`),
        never from the message text, and is already a keyed pseudonym: a raw
        `wamid` base64-embeds the counterpart phone number.

        Args:
            to: Recipient phone number in international format (e.g., '5511999999999').
            text: The message body text.
            idempotency_key: Optional durable dedup key for this send (see above).
                Ignored — with a loud log line, never silently — when no registry
                is wired.

        Returns:
            The API response as a dict containing message IDs, or
            `{"suppressed_duplicate": True, ...}` when the guard suppressed the send.

        Raises:
            ValueError: If `phone_number_id` or the WABA token is unconfigured.
                Neither refusal names a configured value.
            DedupRegistryUnavailableError: If the guard was asked for a decision
                and could not give one — fail closed, because sending anyway is
                exactly the duplicate reply the guard exists to prevent.
            httpx.HTTPStatusError: If the WhatsApp API returns an error.
        """
        phone_number_id = self._settings.phone_number_id
        if not phone_number_id:
            raise ValueError("WhatsApp phone_number_id is not configured — refusing to send")
        if not self._settings.whatsapp_token:
            raise ValueError("WhatsApp access token is not configured — refusing to send")

        if idempotency_key is not None and self._dedup is None:
            # Announced, never silent: the caller asked for once-only delivery and this instance
            # cannot provide it. (Reachable only from a composition root that wired a key factory
            # without a registry — the production root wires both or neither.)
            logger.error(
                "whatsapp_send_idempotency_key_ignored",
                detail="an idempotency key was supplied but this WhatsAppServer has no dedup "
                "registry — the send is NOT protected against duplicate delivery",
            )
        # Bound to locals so the guarded branches below are narrowed by mypy without any cast.
        registry = self._dedup
        guard_key = idempotency_key if registry is not None else None
        if registry is not None and guard_key is not None and not await registry.claim(guard_key):
            logger.info("whatsapp_send_suppressed_duplicate", dedup_key=guard_key)
            return {"suppressed_duplicate": True, "idempotency_key": guard_key}

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

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(url, json=payload, headers=headers)
                response.raise_for_status()
                data: dict[str, Any] = response.json()
        except Exception:
            # The send did NOT happen: withdraw the claim so the legitimate retry of a transient
            # Cloud API failure is not suppressed as a duplicate of a message nobody received.
            # The original exception is re-raised untouched (the caller decides what a failed
            # reply means); only the claim is cleaned up. A registry failure DURING the cleanup
            # must not replace the real error, so it is logged and swallowed here — the claim then
            # expires with its in-flight lease.
            if registry is not None and guard_key is not None:
                try:
                    await registry.release(guard_key)
                except Exception:  # noqa: BLE001 — never mask the send failure being re-raised.
                    logger.error("whatsapp_send_claim_release_failed", dedup_key=guard_key)
            raise

        if registry is not None and guard_key is not None:
            # Sealed AFTER the Cloud API accepted the message: from here on the key suppresses
            # any repeat for the whole TTL, which is exactly the "no duplicate reply to the
            # beneficiary" property R-071 asks for.
            await registry.mark_processed(guard_key)

        logger.info("whatsapp_message_sent", message_id=(data.get("messages") or [{}])[0].get("id"))
        return data

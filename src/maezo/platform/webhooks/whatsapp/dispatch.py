"""WhatsApp inbound dispatch — verified webhook POST -> Helena, in-process (T1.11, defect B6).

LABELED BOUNDARY (disclosed, not fabricated): this is an EXPLICIT, HONEST IN-PROCESS DISPATCH.
There is no message queue/Kafka producer in this build (`service.py`'s own module docstring:
"no downstream consumer yet") — each verified inbound WhatsApp message runs Helena's compiled
graph to completion SYNCHRONOUSLY, within the same request that received it. A queue-backed
upgrade (so a slow LLM/engine call does not hold the HTTP response open) is a follow-up, not
built here.

DURABLE MULTI-TURN STATE (T4b, closing the T3.4/F4 boundary #165 left here): the dispatcher is
now constructed WITH a durable LangGraph checkpointer (`runtime.checkpoint.Checkpointer`, wrapping
an `AsyncPostgresSaver` in production). Each turn compiles Helena's graph checkpoint-enabled
(`graph.compile(checkpointer=...)`) and invokes it under a PHI-safe thread config keyed by the
`wa:{tenant}:{phone_hash}` conversation id (`checkpoint_thread_config` — the SAME hashed identity
already derived below), so a SECOND inbound message from the same beneficiary RESUMES the first
turn's persisted graph state, and a receiver restart no longer drops the conversation. The
fail-closed decision (a prod receiver that cannot durably checkpoint must REFUSE to serve rather
than silently run stateless) lives at the webhook composition seam, not here — see
`platform/webhooks/service.py` (`_provision_dispatch_checkpointer` -> a `None` dispatcher ->
`/webhook` 501, the same refuse-to-serve shape a missing audit sink already triggers). When no checkpointer
is injected (unit tests, or a build that deliberately runs stateless) the graph still compiles
stateless — every turn then starts fresh, exactly as the pre-T4b behavior.

PHI custody note: no persistent, reversible phone-number vault exists in v2 (ADR-0006 general-
zone pseudonymization is one-way, `gateway/pseudonymizer.py`). Helena's own graph state NEVER
carries a raw phone number — only a `wa:{tenant}:{phone_hash}` conversation id and a
`beneficiario_pseudo_id` derived by pseudonymizing that SAME hash a second time (hash-of-a-hash:
even if `conversation_id` ever leaked, `beneficiario_pseudo_id` is not trivially re-derivable
from it without also knowing the tenant-salted first hash step). The ONE place the raw number is
needed — replying over the WhatsApp Cloud API — is handled by `_ScopedWhatsAppSender`, a
per-turn closure over the raw number THIS SAME request already received; it is never written
into `HelenaState`, never logged, and never persisted past this one dispatch call.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import structlog

from maezo.agents.helena.graph import HelenaState, WhatsAppSender, build, new_helena_state
from maezo.gateway.pseudonymizer import Pseudonymizer
from maezo.runtime.checkpoint import Checkpointer, checkpoint_thread_config
from maezo.runtime.inference import InferenceProvider
from maezo.tools.mcp_cibseven.transport import AuditStartSink, CibSevenTransport
from maezo.tools.mcp_whatsapp.server import WhatsAppServer
from maezo.tools.workers.dmn_transport import DmnTransport

from .security import hash_phone

logger = structlog.get_logger(__name__)


@dataclass(frozen=True, slots=True)
class InboundMessage:
    """One inbound WhatsApp TEXT message, extracted from the Cloud API webhook envelope."""

    from_number: str
    text: str
    message_id: str


def extract_inbound_messages(payload: Any) -> list[InboundMessage]:
    """Parse the WhatsApp Cloud API webhook envelope for inbound TEXT messages.

    Non-text messages (image/audio/location/...) and non-message change events (delivery-status
    callbacks, `value.statuses`) are skipped, logged, NEVER fabricated into a fake text turn.
    Malformed/unexpected shapes yield an empty list rather than raising — the caller acks 200
    regardless (a benign webhook shape drift this build doesn't parse yet should not make Meta
    hammer the endpoint with retries).
    """
    if not isinstance(payload, dict):
        return []
    out: list[InboundMessage] = []
    try:
        for entry in payload.get("entry", []) or []:
            for change in entry.get("changes", []) or []:
                value = change.get("value", {}) or {}
                for msg in value.get("messages", []) or []:
                    if msg.get("type") != "text":
                        logger.info("whatsapp_inbound_message_skipped", message_type=msg.get("type"))
                        continue
                    text = (msg.get("text") or {}).get("body", "")
                    from_number = msg.get("from", "")
                    if not from_number or not text:
                        continue
                    out.append(
                        InboundMessage(
                            from_number=str(from_number), text=str(text), message_id=str(msg.get("id", ""))
                        )
                    )
    except (AttributeError, TypeError) as exc:
        logger.warning("whatsapp_inbound_payload_unparseable", error=str(exc))
        return []
    return out


class _ScopedWhatsAppSender:
    """Per-turn WhatsApp sender — closes over the RAW recipient number for exactly one inbound
    turn (module docstring: never stored on `HelenaState`, never persisted past this call)."""

    def __init__(self, *, raw_to: str, expected_hash: str, client: WhatsAppServer) -> None:
        self._raw_to = raw_to
        self._expected_hash = expected_hash
        self._client = client

    async def send(self, to_hash: str, text: str) -> dict[str, Any]:
        if to_hash != self._expected_hash:
            # Defensive only — Helena's state always carries the SAME hash this dispatcher
            # derived from `raw_to` moments ago; a mismatch means a programming error upstream,
            # never a valid alternate destination. Fail closed rather than send to an unverified
            # destination.
            raise ValueError(
                f"WhatsApp send hash mismatch: turn produced {to_hash!r}, dispatch expected "
                f"{self._expected_hash!r} — refusing to send to an unverified destination"
            )
        return await self._client.send_message(self._raw_to, text)


@dataclass
class HelenaDispatcher:
    """Constructed once at webhook-receiver startup; builds a FRESH Helena graph per inbound
    message (cheap: `HelenaGraph.__init__` just stores references — the underlying transports
    are shared, long-lived objects passed in unchanged on every call)."""

    tenant_id: str
    inference: InferenceProvider
    dmn: DmnTransport
    cibseven: CibSevenTransport
    whatsapp_client: WhatsAppServer
    pseudonymizer: Pseudonymizer
    # T-C2 fence: the durable ADR-0007 sink Helena's escalation start audits BEFORE the engine
    # effect (fail-closed). Required — the dispatcher cannot be built without it (`service.py`).
    audit_sink: AuditStartSink
    # T4b (closes the T3.4/F4 boundary #165 left here): the durable LangGraph checkpointer that
    # makes multi-turn conversation state SURVIVE across webhook invocations / receiver restarts.
    # Optional so unit tests and any deliberately-stateless build construct without one (the graph
    # then compiles stateless — every turn starts fresh). The fail-closed "prod receiver that
    # cannot durably checkpoint refuses to serve" decision is made at construction time in
    # `platform/webhooks/service.py` (a `None` dispatcher -> `/webhook` 501), not per-turn here.
    checkpointer: Checkpointer | None = None

    async def dispatch(self, message: InboundMessage) -> dict[str, Any]:
        """Run ONE complete Helena turn (receive..respond) for `message`.

        When a durable `checkpointer` is wired (production), the graph is compiled
        checkpoint-enabled and invoked under a PHI-safe thread config keyed by the
        `wa:{tenant}:{phone_hash}` conversation id — so a subsequent inbound message from the same
        beneficiary RESUMES this turn's persisted state (multi-turn), and a receiver restart does
        not drop the conversation. With no checkpointer the graph compiles stateless (fresh turn
        every time)."""
        phone_hash = hash_phone(message.from_number, self.tenant_id)
        conversation_id = f"wa:{self.tenant_id}:{phone_hash}"
        beneficiario_pseudo_id = self.pseudonymizer.pseudonymize({"telefone": phone_hash})["telefone"]

        sender: WhatsAppSender = _ScopedWhatsAppSender(
            raw_to=message.from_number, expected_hash=phone_hash, client=self.whatsapp_client
        )
        graph = build(
            {
                "inference": self.inference,
                "dmn": self.dmn,
                "cibseven": self.cibseven,
                "whatsapp": sender,
                "audit_sink": self.audit_sink,
                "agent_version": "helena@v0",
            }
        )
        # `conversation_id` (a hashed `wa:{tenant}:{phone_hash}`) IS the checkpoint thread id — it
        # carries no raw phone/CPF, so `checkpoint_thread_config` (which fail-closes on an
        # obviously-raw-numeric id) accepts it and the PHI-bearing `checkpoint_blobs` rows keyed by
        # it stay LGPD-safe. `saver=None` compiles stateless AND yields a None config (the thread
        # id is only meaningful with a saver attached).
        saver = self.checkpointer.saver if self.checkpointer is not None else None
        compiled = graph.compile(checkpointer=saver)
        thread_config = checkpoint_thread_config(conversation_id) if saver is not None else None
        # INPUT-BOUNDARY GATE (T1.11): assemble state through the typed constructor, NOT an inline
        # dict literal. `new_helena_state`'s explicit keyword-only signature makes it structurally
        # impossible to pass an output-only key (a forged `next_kind`/`error`/`escalation_*`/
        # `dmn_decision_ref`) from here into `HelenaState` — the caller-planted read-through class
        # is unreachable at the construction seam, not just neutralized inside `receive`.
        initial_state: HelenaState = new_helena_state(
            tenant_id=self.tenant_id,
            conversation_id=conversation_id,
            canal="whatsapp",
            beneficiario_pseudo_id=beneficiario_pseudo_id,
            message_body=message.text,
        )
        logger.info(
            "helena_dispatch_turn_started",
            tenant_id=self.tenant_id,
            conversation_id=conversation_id,
            message_id=message.message_id,
            checkpointed=saver is not None,
        )
        # `thread_config` scopes the checkpoint thread when a saver is attached; None (stateless
        # compile) is passed through as a no-op config, so this call site is single-path.
        result = await compiled.ainvoke(initial_state, thread_config)
        logger.info(
            "helena_dispatch_turn_completed",
            tenant_id=self.tenant_id,
            conversation_id=conversation_id,
            next_kind=result.get("next_kind"),
            escalation_started=result.get("escalation_started"),
            escalation_business_key=result.get("escalation_business_key"),
            error=result.get("error"),
        )
        return dict(result)

"""WhatsApp inbound dispatch — verified webhook POST -> Helena, in-process (T1.11, defect B6).

LABELED BOUNDARY (disclosed, not fabricated): this is an EXPLICIT, HONEST IN-PROCESS DISPATCH.
There is no message queue/Kafka producer in this build (`service.py`'s own module docstring:
"no downstream consumer yet") — each verified inbound WhatsApp message runs Helena's compiled
graph to completion SYNCHRONOUSLY, within the same request that received it, unless the receiver
is running in ack-then-queue mode (owner decision R-072, `WHATSAPP_WEBHOOK_ACK_THEN_QUEUE`,
DEFAULT OFF), in which case `app.py` claims the delivery durably, answers Meta first and runs
this same code in a background task. Still no broker: the durable claim row is the entry leg, and
`docs/processes/webhook-whatsapp-ack-then-queue.md` states exactly what that does and does not
buy.

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

NON-TEXT INBOUND IS ACKNOWLEDGED, NOT DROPPED (gap `WHATSAPP-NON-TEXT-DROPPED`). Until this
change, every inbound whose `type != "text"` (audio/image/document/location/...) was discarded
inside `extract_inbound_messages` with a single INFO line, and `app.py` then acked Meta with
`{"dispatched": 0}` — so a beneficiary who sent a voice note or a photo of an exam got NOTHING
back: no reply, no fallback, no human. Non-text messages are now FIRST-CLASS and TYPED
(`InboundNonTextMessage`, a sibling of `InboundMessage`), and `HelenaDispatcher.acknowledge_non_text`
sends exactly ONE fixed pt-BR reply through the SAME gated per-turn seam a Helena turn uses
(`_ScopedWhatsAppSender` + `gate_whatsapp`). No graph turn, no LLM call, no process start, no
checkpoint write — the ack is a canned string, and nothing about it is inferred from the media.

WHAT THE ACK DOES NOT PROMISE (honesty rule). The text says only what this code does: that the
channel accepts text. It does NOT promise a human follow-up, Libras, transcription or a callback,
because none of those is wired here — the richer fallback (owner decision 10.2) and the
single-channel question (owner decision 9.6) are OPEN owner decisions, not something this module
may imply. A reply that promised a human while starting no escalation would be exactly the
fabricated-fact pattern this repo's gates exist to prevent.

wamid DEDUP — TWO LEGS, ONE DELIVERY (gap `WEBHOOK-WAMID-DEDUP`, owner decision R-071 option C,
2026-09-04). Meta retries a webhook that did not answer 2xx (including one that merely took too
long), and this module used to state that no idempotency store existed. It exists now, over the
repurposed `driver_idempotency` table (`platform/driver_idempotency.py`, migration 0010):

  INBOUND leg   `app.py` claims the `wamid` BEFORE calling anything here, so a redelivery never
                reaches `dispatch`/`acknowledge_non_text` at all.
  OUTBOUND leg  every send this module performs carries an idempotency key derived from the SAME
                inbound wamid (`_ScopedWhatsAppSender` -> `WhatsAppServer.send_message`), which
                covers the window the inbound claim cannot: the claim is released when a turn
                fails, and a turn can fail AFTER the beneficiary was already answered (e.g. a
                checkpoint write that raises after `respond`). Without the outbound leg, that
                redelivery would legitimately re-run the turn and legitimately re-send the reply.

The owner's words for treating them as one act: "mais idempotencia na saida `send`, tratadas como
uma entrega so".

PHI custody note: no persistent, reversible phone-number vault exists in v2 (ADR-0006 general-
zone pseudonymization is one-way, `gateway/pseudonymizer.py`). Helena's own graph state NEVER
carries a raw phone number — only a `wa:{tenant}:hk1_{phone_hash}` conversation id, where
`phone_hash` is a KEYED HMAC-SHA256 pseudonym (ADR-0035, derived through the vault-keyed
`Pseudonymizer` — irreversible without `PHI_HMAC_KEY`, NOT a reversible bare sha256), and a
`beneficiario_pseudo_id` derived by pseudonymizing that SAME keyed hash a second time
(hash-of-a-hash: even if `conversation_id` ever leaked, it is already keyed-irreversible, and
`beneficiario_pseudo_id` is a distinct second keyed derivation). The ONE place the raw number is
needed — replying over the WhatsApp Cloud API — is handled by `_ScopedWhatsAppSender`, a
per-turn closure over the raw number THIS SAME request already received; it is never written
into `HelenaState`, never logged, and never persisted past this one dispatch call.

RETOMADA POS-HUMANO (GAP-XHITL-4). `HelenaDispatcher.resume` is the SECOND way a turn is injected:
the agent-resume consumer (`platform/integrations/agent_resume.py`) calls it after a human
concluded SP-OP-ESCALATION-001 as `devolvido_agente`. Same shape as `dispatch` — the same graph
build, the same checkpoint thread (`wa:{tenant}:{phone_hash}` IS the `conversation_id` the event
carries), the same gated per-turn sender — with ONE difference the custody note above makes
unavoidable: there is no inbound request, so there is no raw number in hand. The caller must pass
one, and `resume` REFUSES it unless its keyed hash is the `phone_hash` embedded in the
conversation id (a resolver bug can never redirect a human's instructions to another person).
Where that number comes from is an OPEN custody decision (ADR-0006: no reversible phone vault
exists) — see `agent_resume.RecipientResolver`.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Final

import structlog

from maezo.agents.helena.graph import (
    HelenaState,
    WhatsAppSender,
    build,
    new_helena_resume_state,
    new_helena_state,
)
from maezo.gateway.pseudonymizer import Pseudonymizer
from maezo.gateway.recipient_custody import RecipientSealer
from maezo.gateway.seams import SeamContext
from maezo.gateway.seams.whatsapp import gate_whatsapp
from maezo.platform.observability import record_agent_first_response, record_mensagem_limitada
from maezo.runtime.checkpoint import Checkpointer, checkpoint_thread_config
from maezo.runtime.dependency_failures import EXTERNAL_DEPENDENCY_FAILURES
from maezo.runtime.inference import InferenceProvider
from maezo.runtime.metrics import classify_agent_error_type
from maezo.tools.mcp_cibseven.transport import AuditStartSink, CibSevenTransport
from maezo.tools.mcp_whatsapp.server import WhatsAppServer
from maezo.tools.workers.dmn_transport import DmnTransport

from .dedup import WhatsAppDedupGuard
from .limite import LimitadorDeVolume, Veredito
from .security import hash_phone, log_safe_message_id

logger = structlog.get_logger(__name__)


#: The ONE fixed pt-BR reply a non-text inbound gets. Deliberately a module-level constant, not an
#: f-string assembled per message: nothing about the media (type, caption, filename, media id) may
#: leak into the body, and the text must be reviewable as a single, stable sentence pair.
#:
#: HONESTY (module docstring): it promises ONLY what `acknowledge_non_text` does — tell the
#: beneficiary this channel accepts text. No human, no transcription, no Libras, no callback:
#: those are owner decisions 9.6/10.2, and none of them is wired. If a human follow-up is ever
#: wired through Helena's audited escalation start, this string changes IN THE SAME commit as the
#: wiring and its tests, never before.
NON_TEXT_ACK_TEXT: Final[str] = (
    "Este canal aceita apenas mensagens de texto. Por favor, envie sua mensagem em texto."
)

#: A resposta de quem bateu no teto de volume (Frente 7.1). CONSTANTE e sem modelo no meio: o
#: motivo de a mensagem nao ter sido atendida e' de infraestrutura, e pedir um texto ao modelo
#: justamente quando se esta' cortando volume seria gastar a chamada que o teto existe para poupar.
#:
#: O QUE ELA PRECISA DIZER, e por que nao pode simplesmente sumir: o documento e' explicito — o
#: limite NAO pode descartar em silencio. Uma pessoa que escreve e nao recebe nada conclui que
#: ninguem leu. Entao ela diz que a mensagem nao foi atendida AGORA, que e' verdade, e aponta o
#: caminho de emergencia, porque quem esta' em laco pode estar em panico.
LIMITE_EXCEDIDO_TEXT: Final[str] = (
    "Recebemos muitas mensagens suas em pouco tempo e nao conseguimos atender esta agora. "
    "Aguarde um minuto e envie novamente. Se voce estiver passando por uma emergencia, procure "
    "o servico de emergencia mais proximo ou ligue para a central de atendimento do seu plano."
)


@dataclass(frozen=True, slots=True)
class InboundMessage:
    """One inbound WhatsApp TEXT message, extracted from the Cloud API webhook envelope."""

    from_number: str
    text: str
    message_id: str


@dataclass(frozen=True, slots=True)
class InboundNonTextMessage:
    """One inbound WhatsApp NON-TEXT message (audio/image/document/location/sticker/...).

    A SIBLING type rather than a `kind` flag on :class:`InboundMessage`, on purpose: there is no
    `text` to carry, and the split makes it a TYPE ERROR — not a runtime surprise — to feed a voice
    note into `HelenaDispatcher.dispatch`, which would run a Helena turn over an empty body.
    `app.py` must branch, and mypy enforces that it does.

    Carries NOTHING from the media itself: no media id, no caption, no filename, no mime type. Only
    `message_type` (Meta's own low-cardinality enum token, used for logs — never as a metric label)
    and the wamid, plus the raw number that the per-turn sender closure needs and that never
    outlives :meth:`HelenaDispatcher.acknowledge_non_text`. The wamid HELD here is the raw one
    (dedup e idempotencia de saida precisam do valor real); o que sai em log passa sempre por
    `security.py::log_safe_message_id` (gap `WEBHOOK-LOG-RAW-WAMID`), que tem DOIS desfechos —
    nao um so: o pseudonimo keyed `hk1_` quando ha wamid para pseudonimizar, e o marcador
    `MESSAGE_ID_LOG_OMITTED` quando o wamid chega vazio. Em nenhum dos dois o wamid bruto sai.
    """

    from_number: str
    message_type: str
    message_id: str


#: What `extract_inbound_messages` yields: a real message of either shape. Delivery-status
#: callbacks and malformed entries are NOT members — they are not messages, and they are skipped.
InboundEvent = InboundMessage | InboundNonTextMessage


def extract_inbound_messages(payload: Any) -> list[InboundEvent]:
    """Parse the WhatsApp Cloud API webhook envelope for inbound messages of BOTH shapes.

    A `type == "text"` message becomes an :class:`InboundMessage` — byte-for-byte the same
    extraction as before. Any OTHER message type becomes an :class:`InboundNonTextMessage` so the
    caller can acknowledge it (gap `WHATSAPP-NON-TEXT-DROPPED`: this function used to drop them
    with one INFO line, which is why a beneficiary's voice note got no answer at all). Nothing is
    ever fabricated into a fake text turn: the non-text branch carries no body and no media.

    STILL SKIPPED, because they are not messages: non-message change events (delivery-status
    callbacks, `value.statuses` — they never reach the `messages` loop) and malformed entries (a
    message with no `from`, a message with no usable `type` token, a text message with an empty
    body). Malformed/unexpected shapes yield an empty list rather than raising — the caller acks
    200 regardless (a benign webhook shape drift this build doesn't parse yet should not make Meta
    hammer the endpoint with retries).
    """
    if not isinstance(payload, dict):
        return []
    out: list[InboundEvent] = []
    try:
        for entry in payload.get("entry", []) or []:
            for change in entry.get("changes", []) or []:
                value = change.get("value", {}) or {}
                for msg in value.get("messages", []) or []:
                    message_type = msg.get("type")
                    from_number = msg.get("from", "")
                    # A non-`str` / empty `type` is a shape this build cannot classify at all: it
                    # is neither a text turn nor a media kind worth naming back to the sender.
                    # Fail closed by skipping (and say so), rather than coercing it into a token
                    # that then travels into log lines.
                    if not from_number or not isinstance(message_type, str) or not message_type:
                        logger.info("whatsapp_inbound_message_skipped", reason="malformed_message")
                        continue
                    message_id = str(msg.get("id", ""))
                    if message_type != "text":
                        out.append(
                            InboundNonTextMessage(
                                from_number=str(from_number),
                                message_type=message_type,
                                message_id=message_id,
                            )
                        )
                        continue
                    text = (msg.get("text") or {}).get("body", "")
                    if not text:
                        logger.info("whatsapp_inbound_message_skipped", reason="empty_text_body")
                        continue
                    out.append(
                        InboundMessage(from_number=str(from_number), text=str(text), message_id=message_id)
                    )
    except (AttributeError, TypeError) as exc:
        logger.warning("whatsapp_inbound_payload_unparseable", error=str(exc))
        return []
    return out


class _ScopedWhatsAppSender:
    """Per-turn WhatsApp sender — closes over the RAW recipient number for exactly one inbound
    turn (module docstring: never stored on `HelenaState`, never persisted past this call)."""

    def __init__(
        self,
        *,
        raw_to: str,
        expected_hash: str,
        client: WhatsAppServer,
        idempotency_key_for: Callable[[int], str] | None = None,
    ) -> None:
        self._raw_to = raw_to
        self._expected_hash = expected_hash
        self._client = client
        # OUTBOUND dedup leg (`WEBHOOK-WAMID-DEDUP`). A callable of the send ORDINAL, not a fixed
        # string: exactly one send exists per inbound message today, but a turn that ever sent two
        # messages would otherwise have its SECOND suppressed by its own first — a silently
        # truncated reply. The ordinal restarts at 1 for a re-delivered turn, which is precisely
        # what makes the replay's first send collide with the original's and be suppressed.
        self._idempotency_key_for = idempotency_key_for
        self._sends = 0

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
        self._sends += 1
        if self._idempotency_key_for is None:
            # No dedup guard wired (unit tests; see `HelenaDispatcher.dedup`). The call stays
            # BYTE-IDENTICAL to the pre-dedup one so a fake client with the old two-argument
            # signature keeps working.
            return await self._client.send_message(self._raw_to, text)
        return await self._client.send_message(
            self._raw_to, text, idempotency_key=self._idempotency_key_for(self._sends)
        )


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
    # ONDA 1 §5.5 / O4 — the resolution of the per-request knot. `dmn`/`cibseven`/`inference` above
    # arrive ALREADY gated from the registry, because they are long-lived. The WhatsApp seam cannot
    # be: `_ScopedWhatsAppSender` is built per turn, below, around the raw recipient of THIS
    # request. So the expensive half of the gate — the `DecisionContext` (PEP, capability view,
    # approvals path), built once per (tenant, principal) — is frozen into this `SeamContext` at
    # receiver bring-up, and `dispatch()` re-wraps the per-turn sender with it at the cost of one
    # object allocation. No policy load, no PEP build, no I/O per turn (I-9).
    #
    # Optional ONLY so the unit tests that construct a dispatcher directly keep working. The
    # PRODUCTION root always supplies it (`platform/webhooks/service.py::_build_dispatcher`), and
    # `dispatch()` logs loudly at error level if it is ever absent — a disclosed, test-only
    # affordance, never a silent ungated live path.
    seam_context: SeamContext | None = None
    # OUTBOUND dedup leg (`WEBHOOK-WAMID-DEDUP`, R-071). The SAME guard `app.py` uses for the
    # inbound claim, so both legs derive their keys from one pseudonymizer and one tenant. Optional
    # for the same reason `seam_context` is: the unit tests construct a dispatcher directly. The
    # production root (`platform/webhooks/service.py::_build_dispatcher`) always supplies it.
    dedup: WhatsAppDedupGuard | None = None
    #: MEMORIA CLINICA ENTRE TURNOS (Frente 2.1). Default `True` aqui pelo mesmo motivo que nas
    #: settings: desligar preserva o defeito do bebe triado como adulto. A raiz de composicao
    #: (`webhooks/service.py`) passa o valor das settings; os testes que nao o passam recebem a
    #: Helena que lembra, que e' a de producao.
    memoria_clinica_enabled: bool = True
    #: TETO DE VOLUME (Frente 7.1). `None` = sem teto, que e' o comportamento anterior e continua
    #: sendo o dos testes que nao o passam. A raiz de composicao (`webhooks/service.py`) constroi
    #: um a partir das settings, entao o receptor implantado SEMPRE tem teto.
    limitador: LimitadorDeVolume | None = None
    #: CUSTODIA DO TELEFONE (GAP-XHITL-4, ADR-0061). `None` = nada e' gravado (o padrao, e o
    #: comportamento anterior). Com custodia, toda mensagem recebida (texto ou nao) atualiza o
    #: numero CIFRADO e o `last_inbound_at` da conversa — o relogio da janela de 24h da Meta.
    recipient_vault: RecipientSealer | None = None

    async def _custodiar_destinatario(self, raw_from: str, conversation_id: str) -> None:
        """Grava o numero cifrado. NAO derruba o turno: a pessoa que escreveu recebe a resposta
        mesmo se a custodia falhar — o custo e' uma retomada futura sem destinatario (fila morta
        `resume_recipient_unavailable`), e isso fica visivel no log de erro. O numero NUNCA vai
        para o log: so' o tipo da excecao."""
        if self.recipient_vault is None:
            return
        try:
            await self.recipient_vault.upsert(
                conversation_id=conversation_id, phone=raw_from, received_at=datetime.now(UTC)
            )
        except Exception as exc:
            logger.error(
                "whatsapp_recipient_custody_failed",
                tenant_id=self.tenant_id,
                conversation_id=conversation_id,
                error_type=type(exc).__name__,
            )

    def _outbound_key_factory(self, message_id: str) -> Callable[[int], str] | None:
        """The per-send idempotency-key builder for ONE inbound message, or None with no guard."""
        if self.dedup is None or not message_id:
            return None
        guard = self.dedup
        return lambda occurrence: guard.outbound_key(message_id, occurrence=occurrence)

    def _gated_scoped_sender(
        self,
        *,
        raw_to: str,
        phone_hash: str,
        conversation_id: str,
        idempotency_key_for: Callable[[int], str] | None = None,
    ) -> WhatsAppSender:
        """Build THE per-turn outbound seam: a scoped sender, wrapped by the effect gate.

        ONDA 1 §5.5 / O4: the wrapper's INNER is the scoped sender, so the raw number stays exactly
        where it already was — inside `_ScopedWhatsAppSender`, for exactly this turn — and the gate
        itself never sees it (`EffectCall` has no field for a recipient, I-3). Cost: one allocation
        over the `SeamContext` frozen at bring-up.

        ONE helper for BOTH outbound paths (a Helena turn and the non-text acknowledgement) so the
        acknowledgement cannot grow a second, ungated way out silently: a duplicated, ungated
        build of `_ScopedWhatsAppSender` inside `acknowledge_non_text` is caught ONLY by
        `tests/unit/platform/webhooks/whatsapp/test_dispatch.py
        ::test_acknowledge_non_text_goes_through_the_effect_gate` and
        `::test_acknowledge_non_text_without_a_seam_context_announces_it_loudly`. It is NOT caught
        by `make effect-chokepoint-fence` (its §8.1 fenced-name list does not include
        `_ScopedWhatsAppSender`, an in-module class) nor by
        `tests/unit/gateway/seams/test_live_dispatch_wiring.py` (it only ever calls
        `HelenaDispatcher.dispatch`, never `acknowledge_non_text`) — both stay green on that
        mutant. The effect-chokepoint fence does NOT cover this seam; the two tests above are the
        only guard.
        """
        sender: WhatsAppSender = _ScopedWhatsAppSender(
            raw_to=raw_to,
            expected_hash=phone_hash,
            client=self.whatsapp_client,
            idempotency_key_for=idempotency_key_for,
        )
        if self.seam_context is not None:
            return gate_whatsapp(sender, self.seam_context)
        # Test-only affordance (see the field's comment). Announced, never silent.
        logger.error(
            "helena_dispatch_whatsapp_seam_ungated",
            tenant_id=self.tenant_id,
            conversation_id=conversation_id,
            detail="no SeamContext on the dispatcher — the WhatsApp seam is NOT choked for "
            "this turn. The production composition root always supplies one; reaching this "
            "branch in a deployed receiver is a wiring defect.",
        )
        return sender

    async def acknowledge_non_text(self, message: InboundNonTextMessage) -> dict[str, Any]:
        """Send the ONE fixed pt-BR reply for a non-text inbound (`WHATSAPP-NON-TEXT-DROPPED`).

        Deliberately NOT a Helena turn: no graph build, no LLM call, no DMN evaluation, no
        `start_process_idempotent`, no checkpoint write. A voice note carries no text to classify,
        so running the triage graph over an empty body would be inventing an utterance the
        beneficiary never made. What the beneficiary gets is a canned, reviewable sentence pair
        (:data:`NON_TEXT_ACK_TEXT`) telling them the channel takes text — nothing more, because
        nothing more is wired (owner decisions 9.6/10.2 stay OPEN).

        Same identity derivation and the SAME gated per-turn seam as `dispatch` — the ack is an
        EFFECT (`whatsapp.send_message`, action class `comunicacao_beneficiario`) and is choked
        exactly like a Helena reply. The raw number lives only inside the scoped sender's closure,
        for this call; the telemetry carries the keyed `hk1_` pseudonym of the NUMBER, the keyed
        `hk1_` pseudonym of the WAMID (`message_pseudonym` — gap `WEBHOOK-LOG-RAW-WAMID`: this
        line used to say "the wamid", and the code used to mean it) and Meta's `message_type`
        token, and NEVER the media id, caption, filename, raw number or raw wamid.

        RETRY SEMANTICS (disclosed, module docstring): with no `wamid` idempotency store, a webhook
        that Meta re-delivers re-sends this ack. Duplicate courtesy message, never a duplicate
        adverse effect. Dedup (`WEBHOOK-WAMID-DEDUP`) is owner-gated and NOT implemented here.

        Raises:
            Exception: whatever the seam or the Cloud API client raises (a denied effect, an
                unconfigured `WHATSAPP_PHONE_NUMBER_ID`, an HTTP error). `app.py` counts it as a
                failed message and never lets it escape the webhook.
        """
        phone_hash = hash_phone(message.from_number, self.tenant_id, self.pseudonymizer)
        conversation_id = f"wa:{self.tenant_id}:{phone_hash}"
        await self._custodiar_destinatario(message.from_number, conversation_id)
        sender = self._gated_scoped_sender(
            raw_to=message.from_number,
            phone_hash=phone_hash,
            conversation_id=conversation_id,
            idempotency_key_for=self._outbound_key_factory(message.message_id),
        )
        # Gap `WEBHOOK-LOG-RAW-WAMID`: `message_pseudonym`, nunca `message_id`. O wamid bruto
        # embute o telefone da contraparte em base64 (`security.py::hash_message_id`), entao ele
        # recebe no log o MESMO tratamento keyed que a chave de dedup ja recebe no banco.
        message_pseudonym = log_safe_message_id(message.message_id, self.tenant_id, self.pseudonymizer)
        logger.info(
            "whatsapp_non_text_ack_started",
            tenant_id=self.tenant_id,
            conversation_id=conversation_id,
            message_pseudonym=message_pseudonym,
            message_type=message.message_type,
        )
        ack = await sender.send(phone_hash, NON_TEXT_ACK_TEXT)
        logger.info(
            "whatsapp_non_text_ack_sent",
            tenant_id=self.tenant_id,
            conversation_id=conversation_id,
            message_pseudonym=message_pseudonym,
            message_type=message.message_type,
        )
        return ack

    async def _recusar_por_limite(
        self,
        message: InboundMessage,
        phone_hash: str,
        conversation_id: str,
        veredito: Veredito,
    ) -> dict[str, Any]:
        """Responde o teto de volume e NAO roda o turno.

        Devolve um estado marcado com `limite_excedido`, e nao levanta excecao, porque isto nao e'
        falha: e' o canal funcionando como configurado. `app.py` le' a marca para contar o lote
        separadamente — confundir com `failed` faria o painel acusar defeito onde ha protecao.
        """
        record_mensagem_limitada(tenant=self.tenant_id, escopo=veredito.escopo or "desconhecido")
        logger.warning(
            "whatsapp_mensagem_limitada",
            tenant_id=self.tenant_id,
            conversation_id=conversation_id,
            escopo=veredito.escopo,
            observadas=veredito.observadas,
            message_pseudonym=log_safe_message_id(message.message_id, self.tenant_id, self.pseudonymizer),
        )
        if not veredito.avisar:
            # Ja' avisamos esta conversa nesta janela. Responder de novo a cada mensagem do laco
            # seria pagar um envio por recusa e, com um bot do outro lado, fabricar um laco de
            # ida e volta (security-reviewer, 18/09/2026). A recusa esta' contada acima.
            return {
                "limite_excedido": True,
                "escopo_do_limite": veredito.escopo,
                "conversation_id": conversation_id,
                "aviso_enviado": False,
                "aviso_suprimido": True,
            }
        sender = self._gated_scoped_sender(
            raw_to=message.from_number,
            phone_hash=phone_hash,
            conversation_id=conversation_id,
            idempotency_key_for=self._outbound_key_factory(message.message_id),
        )
        enviada = True
        try:
            await sender.send(phone_hash, LIMITE_EXCEDIDO_TEXT)
        except EXTERNAL_DEPENDENCY_FAILURES:
            # O aviso e' cortesia; falhar ao envia-lo nao pode transformar uma protecao em erro do
            # lote. O contador acima ja registrou a recusa, que e' o que importa medir.
            enviada = False
            logger.warning("whatsapp_aviso_de_limite_nao_enviado", tenant_id=self.tenant_id, exc_info=True)
        return {
            "limite_excedido": True,
            "escopo_do_limite": veredito.escopo,
            "conversation_id": conversation_id,
            "aviso_enviado": enviada,
            "aviso_suprimido": False,
        }

    def _compile_turn_graph(self, sender: WhatsAppSender) -> tuple[Any, Any]:
        """Build + compile Helena's graph for ONE turn. Returns `(compiled, thread_saver)`.

        Shared by `dispatch` and `resume` so the two entry doors can never drift in which
        dependencies, agent version or memory flag the graph is built with.
        """
        graph = build(
            {
                "inference": self.inference,
                "dmn": self.dmn,
                "cibseven": self.cibseven,
                "whatsapp": sender,
                "audit_sink": self.audit_sink,
                "agent_version": "helena@v0",
                # MEMORIA CLINICA (Frente 2.1). Passada EXPLICITAMENTE em vez de deixada no default
                # do `build`: o despachante e' quem sabe se ha' checkpointer, e uma memoria ligada
                # sem estado duravel nao e' um erro, mas tambem nao lembra nada — deixar a decisao
                # visivel aqui e' o que permite ler, num lugar so', por que a Helena lembrou (ou
                # nao) num ambiente.
                "memoria_clinica_enabled": self.memoria_clinica_enabled,
            }
        )
        saver = self.checkpointer.saver if self.checkpointer is not None else None
        return graph.compile(checkpointer=saver), saver

    async def resume(
        self,
        *,
        conversation_id: str,
        instrucoes: str,
        raw_to: str,
        resume_ref: str,
    ) -> dict[str, Any]:
        """Run ONE Helena RESUME turn (GAP-XHITL-4) under the conversation's checkpoint thread.

        `conversation_id` is the `wa:{tenant}:{phone_hash}` the `process_completed` event carries
        (the same id `dispatch` derived when the escalation started). `instrucoes` is the human's
        `notas_resolucao`, read by the caller from the engine HISTORY — untrusted content that the
        graph's `resume` node fences before anything is sent. `raw_to` is the recipient number,
        REFUSED unless `hash_phone(raw_to)` equals the conversation's `phone_hash`. `resume_ref` is
        a stable per-case reference (the escalation's process instance id): it seeds the OUTBOUND
        idempotency key, so a redelivered event does not send the instructions twice.

        Raises:
            ValueError: foreign/malformed conversation id, or a recipient that does not match it.
            RetomadaEnvioFalhouError: the WhatsApp send failed (the caller must NOT ack).
        """
        prefix = f"wa:{self.tenant_id}:"
        phone_hash = conversation_id[len(prefix) :] if conversation_id.startswith(prefix) else ""
        if not phone_hash or ":" in phone_hash:
            raise ValueError(
                "helena resume: conversation_id is not a WhatsApp conversation of this tenant — "
                "refusing to resume a thread this dispatcher does not own"
            )
        if hash_phone(raw_to, self.tenant_id, self.pseudonymizer) != phone_hash:
            raise ValueError(
                "helena resume: recipient does not match the conversation's keyed phone hash — "
                "refusing to send a human's instructions to an unverified destination"
            )
        sender = self._gated_scoped_sender(
            raw_to=raw_to,
            phone_hash=phone_hash,
            conversation_id=conversation_id,
            idempotency_key_for=self._outbound_key_factory(f"resume:{resume_ref}"),
        )
        compiled, saver = self._compile_turn_graph(sender)
        thread_config = checkpoint_thread_config(conversation_id) if saver is not None else None
        initial_state: HelenaState = new_helena_resume_state(
            tenant_id=self.tenant_id,
            conversation_id=conversation_id,
            canal="whatsapp",
            instrucoes=instrucoes,
        )
        logger.info(
            "helena_resume_turn_started",
            tenant_id=self.tenant_id,
            conversation_id=conversation_id,
            checkpointed=saver is not None,
        )
        try:
            result = await compiled.ainvoke(initial_state, thread_config)
        except Exception as exc:
            # Same counter as `dispatch` (ALERTS-WITHOUT-METRICS-a): a resume turn is a Helena turn.
            from maezo.platform.observability import record_agent_error

            record_agent_error(agent="helena", error_type=classify_agent_error_type(exc))
            raise
        logger.info(
            "helena_resume_turn_completed",
            tenant_id=self.tenant_id,
            conversation_id=conversation_id,
            desfecho=result.get("desfecho"),
            error=result.get("error"),
        )
        return dict(result)

    async def dispatch(self, message: InboundMessage) -> dict[str, Any]:
        """Run ONE complete Helena turn (receive..respond) for `message`.

        When a durable `checkpointer` is wired (production), the graph is compiled
        checkpoint-enabled and invoked under a PHI-safe thread config keyed by the
        `wa:{tenant}:{phone_hash}` conversation id — so a subsequent inbound message from the same
        beneficiary RESUMES this turn's persisted state (multi-turn), and a receiver restart does
        not drop the conversation. With no checkpointer the graph compiles stateless (fresh turn
        every time)."""
        # GAP 11.2 (`first_response_p95`): wall-clock start of THIS turn, monotonic so a system
        # clock adjustment mid-turn cannot corrupt the observation. See
        # `record_agent_first_response`'s docstring for exactly what the interval between this and
        # the post-`ainvoke` observation below does and does not measure.
        turn_started_at = time.monotonic()
        # KEYED identity (ADR-0035 extension): `hash_phone` routes through the SAME vault-keyed
        # `Pseudonymizer` (fail-closed in prod), so `conversation_id` — which is persisted as the
        # checkpoint `thread_id` and wrapped into the `ESC-{tenant}-...` CIB Seven business key — is
        # irreversible without `PHI_HMAC_KEY`, not a reversible bare sha256 of the phone.
        phone_hash = hash_phone(message.from_number, self.tenant_id, self.pseudonymizer)
        conversation_id = f"wa:{self.tenant_id}:{phone_hash}"

        # TETO DE VOLUME (Frente 7.1), ANTES de qualquer efeito caro. Aqui e' o primeiro ponto do
        # turno em que a conversa tem identidade keyed — e o ultimo antes de a mensagem virar uma
        # chamada de modelo, uma avaliacao de DMN e, quando o modelo cai, um escalonamento.
        #
        # A RECUSA NAO E' SILENCIO. O documento e' explicito: mensagem recusada por limite vira
        # resposta honesta ao beneficiario e contador. Uma pessoa que escreve e nao recebe nada
        # conclui que ninguem leu — e quem esta' em laco pode estar em panico, por isso o texto
        # constante aponta o caminho de emergencia.
        #
        # O ENVIO USA A MESMA COSTURA CERCADA do resto: e' um efeito
        # (`comunicacao_beneficiario`), e um caminho de saida paralelo seria exatamente a segunda
        # porta que o gate existe para impedir.
        if self.limitador is not None:
            veredito = self.limitador.registrar(tenant_id=self.tenant_id, conversation_id=conversation_id)
            if not veredito.permitido:
                return await self._recusar_por_limite(message, phone_hash, conversation_id, veredito)

        beneficiario_pseudo_id = self.pseudonymizer.pseudonymize({"telefone": phone_hash})["telefone"]
        await self._custodiar_destinatario(message.from_number, conversation_id)

        sender = self._gated_scoped_sender(
            raw_to=message.from_number,
            phone_hash=phone_hash,
            conversation_id=conversation_id,
            idempotency_key_for=self._outbound_key_factory(message.message_id),
        )
        # `conversation_id` (a KEYED `wa:{tenant}:hk1_{hmac}`) IS the checkpoint thread id — it
        # carries no raw phone/CPF AND no reversible unkeyed hash, so `checkpoint_thread_config`
        # (which fail-closes unless the id embeds the `hk1_` keyed-pseudonym marker) accepts it and
        # the PHI-bearing `checkpoint_blobs` rows keyed by it stay LGPD-safe. `saver=None` compiles
        # stateless AND yields a None config (the thread id is only meaningful with a saver attached).
        compiled, saver = self._compile_turn_graph(sender)
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
            # Gap `WEBHOOK-LOG-RAW-WAMID` (ver `acknowledge_non_text`): pseudonimo keyed, nunca o
            # wamid bruto que embute o telefone da contraparte.
            message_pseudonym=log_safe_message_id(message.message_id, self.tenant_id, self.pseudonymizer),
            checkpointed=saver is not None,
        )
        # `thread_config` scopes the checkpoint thread when a saver is attached; None (stateless
        # compile) is passed through as a no-op config, so this call site is single-path.
        try:
            result = await compiled.ainvoke(initial_state, thread_config)
        except Exception as exc:
            # ALERTS-WITHOUT-METRICS-a. This receiver is the SECOND turn-execution seam in the
            # repo — it compiles and invokes Helena's graph directly rather than through
            # `runtime.harness.Harness.invoke`, so instrumenting only the harness would have left
            # the ONE live agent path uncounted. `asyncio.CancelledError` is deliberately excluded
            # (BaseException): a drained turn is not a failed agent. See
            # `maezo.platform.observability.record_agent_error` for the full contract.
            # ALERT-COUNTER-LABELS / R-063: this dispatcher only ever runs Helena's graph
            # (`agent_version="helena@v0"` above), so the `agent` label is the literal id.
            from maezo.platform.observability import record_agent_error

            record_agent_error(agent="helena", error_type=classify_agent_error_type(exc))
            raise
        # GAP 11.2: the turn completed (whatever the outcome) — `respond()`, Helena's one terminal
        # node, always attempts exactly one WhatsApp send by this point. Observed AFTER `ainvoke`
        # returns, never in the `except` branch above: an `ainvoke` that raised means no reply was
        # even attempted, so there is nothing honest to time.
        record_agent_first_response(agent_id="helena", seconds=time.monotonic() - turn_started_at)
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

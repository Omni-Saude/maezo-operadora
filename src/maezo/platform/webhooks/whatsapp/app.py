"""WhatsApp Cloud API webhook receiver — FastAPI app (T1.6, defect B1; T1.11 real dispatch).

Endpoint contract per `docs/runbooks/whatsapp-webhook.md` §1 (Meta's own webhook contract, not
this build's invention):

    GET  /webhook   Meta verification handshake (`hub.mode`/`hub.challenge`/`hub.verify_token`).
    POST /webhook   Event ingestion (messages + delivery status).

**Capability honesty (constraint 3).** The GET handshake and the POST signature verification
below are REAL: a genuine HMAC-SHA256 check (`security.py`, timing-safe) against
`WHATSAPP_APP_SECRET`, exactly per §2 of the runbook. T1.11 replaces the prior explicit 501 with
REAL dispatch to Helena's graph (`dispatch.HelenaDispatcher`) — an EXPLICIT, HONEST IN-PROCESS
call (no Kafka producer exists in this build; see `dispatch.py`'s module docstring for the
labeled boundary and the queue-upgrade follow-up). When `dispatcher` is not injected (e.g. this
replica's dependency bring-up failed — `service.py`'s STEP B), a signature-verified message still
returns an explicit 501 with a documented reason, exactly as before — never a fabricated success.

**Non-text inbound (gap `WHATSAPP-NON-TEXT-DROPPED`).** A message whose `type != "text"` used to
vanish inside `extract_inbound_messages`, and this endpoint acked Meta `{"dispatched": 0}` while
the beneficiary who sent a voice note or a photo of an exam received nothing at all. Such a message
is now a typed `InboundNonTextMessage` and takes `dispatcher.acknowledge_non_text` — ONE fixed
pt-BR reply through the same gated seam, no Helena turn (see `dispatch.py`'s docstring for the
honesty rule on what that reply may promise).
Delivery-status callbacks (`value.statuses`) remain "nothing to do": they are not messages.

**`wamid` DEDUP (gap `WEBHOOK-WAMID-DEDUP`, owner decision R-071 option C, 2026-09-04).** Meta
re-delivers a webhook it did not see a 2xx for — including one that merely took too long — and
until now this receiver captured the `wamid` and only LOGGED it. A redelivery therefore ran a
SECOND complete Helena turn: a duplicate reply to the beneficiary, duplicate LLM spend and a
duplicate checkpointed turn, the landmine `docs/adr/0024-...:7` names. Every message in the batch
now takes a DURABLE claim (`dedup.py::WhatsAppDedupGuard` over `driver_idempotency`, migration
0010) BEFORE any effect runs:

  * first delivery inside the TTL -> claim won -> dispatch/ack, then the claim is SEALED;
  * a redelivery inside the TTL   -> claim refused -> NO dispatch, counted as `duplicate`, 200;
  * a failed handling             -> the claim is WITHDRAWN, so Meta's next delivery gets a real
                                     second chance instead of being deduped into silent loss;
  * an UNREACHABLE registry       -> fail closed: no dispatch at all, `dedup_unavailable`, 500.
    Dispatching on the word of a registry that cannot answer would re-open the duplicate-reply
    landmine exactly when the platform is already degraded.

**ACK-THEN-QUEUE (owner decision R-072, same PR, DEFAULT OFF).** With
`WHATSAPP_WEBHOOK_ACK_THEN_QUEUE=true` the durable claim becomes the queue's entry leg: the
receiver claims, answers Meta 200 immediately and runs the turn in a background task, which is
what actually closes the timeout window that CAUSES the retry. It is OFF by default because this
build has no re-drive consumer — see `settings.py`'s field comment and
`docs/processes/webhook-whatsapp-ack-then-queue.md` for the redelivery contract, the ack-latency
budget and exactly what is lost if the process dies after the ack.
"""

from __future__ import annotations

import asyncio
import hmac
import json
from collections.abc import Callable, Coroutine
from typing import Any

import structlog
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, PlainTextResponse
from prometheus_client import Counter

from maezo.platform.driver_idempotency import DedupRegistryUnavailableError
from maezo.platform.health import CheckResult, create_health_app
from maezo.platform.observability import get_metrics_collector

from .dedup import WhatsAppDedupGuard
from .dispatch import HelenaDispatcher, InboundEvent, InboundMessage, extract_inbound_messages
from .security import log_safe_message_id, verify_hub_signature
from .settings import WhatsAppWebhookSettings

logger = structlog.get_logger(__name__)

#: `docs/runbooks/whatsapp-webhook.md` §7 — labels `tenant`, `status`. `status` values this build
#: actually emits: "ok" (GET handshake success; POST ack with 0 or more messages dispatched),
#: "invalid_signature", "parse_error", "dispatch_failed" (every message in the batch raised),
#: "not_implemented" (signature verified, an actual message needs dispatch, but no dispatcher is
#: configured for this replica — see module docstring), "non_text_acked" (the batch contained ONLY
#: non-text messages and every one of them was acknowledged — the operational signal for how much
#: of the inbound volume this channel cannot actually process), plus the four the 2026-09-04 owner
#: decisions add: "duplicate" (every message in the batch was a redelivery the `wamid` dedup
#: suppressed — R-071; a sustained rate is the measure of how much Meta is actually retrying),
#: "partial_failure" (a MIXED batch: something in it succeeded and something failed — R-100, whose
#: whole point is that this outcome used to be indistinguishable from a clean "ok"),
#: "dedup_unavailable" (the durable registry could not answer, so nothing was dispatched — the
#: fail-closed branch) and "queued" (ack-then-queue mode accepted the batch for background
#: processing — R-072). The label set stays CLOSED and tiny
#: on purpose: `message_type` is deliberately NOT a label (Meta's enum is attacker-influenced
#: shape-wise and would multiply the timeseries) — it is logged instead. Module-level (not
#: per-`create_app()`
#: call) — `Counter()` registers into the collector's registry once at import time; redefining it
#: per app instance would raise a duplicate-timeseries error the second time a test constructs an
#: app. Registered on the SAME dedicated registry `MetricsCollector` owns (not the global
#: default) — matches worker_runtime/agent_runtime/gateway (T1.1/T1.6) and `MetricsCollector`'s
#: own docstring: "so metrics don't collide with the default PROCESS_COLLECTOR or other
#: libraries." `create_app` below exposes this SAME registry via `/metrics`, or the counter would
#: silently never appear there.
WEBHOOK_REQUESTS_TOTAL = Counter(
    "maezo_webhook_requests_total",
    "WhatsApp webhook requests by outcome",
    ["tenant", "status"],
    registry=get_metrics_collector().registry,
)

#: Strong references to the background turns of ack-then-queue mode (R-072). `asyncio` keeps only
#: a WEAK reference to a running task, so a task nobody holds can be garbage-collected mid-await —
#: the message would then vanish after the 200 with no exception anywhere. Discarded by the task's
#: own done-callback, so this set is bounded by in-flight work, never by traffic volume.
_BACKGROUND_TURNS: set[asyncio.Task[None]] = set()


def _spawn_background_turn(coro: Coroutine[Any, Any, None]) -> None:
    task = asyncio.create_task(coro)
    _BACKGROUND_TURNS.add(task)
    task.add_done_callback(_BACKGROUND_TURNS.discard)


async def _seal_claim(dedup: WhatsAppDedupGuard | None, message: InboundEvent, *, tenant: str) -> None:
    """Seal the claim of a delivery that really happened. Never raises.

    A registry failure HERE cannot fail the request: the beneficiary was already answered, and a
    non-2xx would make Meta re-deliver a message that WAS processed — the duplicate reply this
    whole change exists to prevent. So it is logged loudly and swallowed; the unsealed row still
    suppresses redelivery until its lease expires (`driver_idempotency.py`), which bounds the
    exposure to one lease instead of one TTL.
    """
    if dedup is None or not message.message_id:
        # No guard, or a message with no wamid to key on — nothing was ever claimed, so there is
        # nothing to seal (and a key derived from an empty id would be a shared, bogus row that
        # suppressed every other unkeyable message).
        return
    try:
        await dedup.mark_inbound_processed(message.message_id)
    except DedupRegistryUnavailableError as exc:
        logger.error(
            "whatsapp_webhook_dedup_seal_failed",
            tenant=tenant,
            dedup_key=dedup.inbound_key(message.message_id),
            # The error processor keeps class/frames; never stringify an upstream payload.
            error=exc,
            detail="the message WAS processed; the claim could not be sealed — redelivery stays "
            "suppressed only until the in-flight lease expires",
        )


async def _withdraw_claim(dedup: WhatsAppDedupGuard | None, message: InboundEvent, *, tenant: str) -> None:
    """Withdraw the claim of a delivery whose handling FAILED. Never raises.

    Without this, the dedup would convert a transient failure into permanent silent loss: the key
    would stay claimed, and Meta's redelivery — the only second chance this message has — would be
    suppressed as a duplicate of a turn that never answered anybody.
    """
    if dedup is None or not message.message_id:
        return  # nothing was claimed — see `_seal_claim`.
    try:
        await dedup.release_inbound(message.message_id)
    except DedupRegistryUnavailableError as exc:
        logger.error(
            "whatsapp_webhook_dedup_release_failed",
            tenant=tenant,
            dedup_key=dedup.inbound_key(message.message_id),
            error=exc,
            detail="handling failed AND the claim could not be withdrawn — redelivery is "
            "suppressed until the in-flight lease expires",
        )


async def _run_one_message(
    message: InboundEvent,
    *,
    dispatcher: HelenaDispatcher,
    dedup: WhatsAppDedupGuard | None,
    tenant: str,
    turno: dict[str, str] | None = None,
) -> str:
    """Run ONE claimed message's effect and settle its claim. Returns the outcome name.

    `"dispatched"` (a Helena turn), `"acked"` (the fixed non-text reply) or `"failed"`. Never
    raises: one message's failure must not drop the rest of the batch, and in ack-then-queue mode
    this coroutine IS the background task, whose exception nobody would ever see.

    `turno` e' um COLETOR OPCIONAL, nao um retorno: quando passado, recebe `resposta` e
    `conversation_id` do turno despachado. Coletor em vez de alargar o tipo de retorno porque o
    chamador de fundo (`_run_background_turn`) nao tem requisicao para responder e nao deve
    carregar um campo que nunca vai usar. Escrito SOMENTE no ramo `dispatched` — um ack de
    nao-texto nao e' um turno da Helena, e um turno que falhou nao tem texto honesto a mostrar.
    """
    try:
        if isinstance(message, InboundMessage):
            resultado = await dispatcher.dispatch(message)
            if turno is not None:
                turno["resposta"] = str(resultado.get("response_text") or "")
                turno["conversation_id"] = str(resultado.get("conversation_id") or "")
            outcome = "dispatched"
        else:
            # Gap `WHATSAPP-NON-TEXT-DROPPED`: ONE fixed reply through the same gated seam.
            # Counted apart from `dispatched` on purpose — an acknowledgement is NOT a Helena
            # turn, and a dashboard that conflated the two would report conversations that never
            # happened.
            await dispatcher.acknowledge_non_text(message)
            outcome = "acked"
    except Exception:  # deliberately total: one message's failure must not drop the batch.
        # Gap `WEBHOOK-LOG-RAW-WAMID`: esta linha carregava `message_id=<wamid bruto>` — o mesmo
        # identificador que embute o telefone da contraparte e que o registro DURAVEL de dedup ja
        # se recusa a persistir (`security.py::hash_message_id`). O unico renderizador PHI-safe
        # alcancavel aqui e o guard de dedup (`dedup.py`, que existe justamente para que a
        # derivacao da chave nao varie por call site); sem ele `log_safe_message_id` devolve o
        # marcador constante, e a ausencia do guard ja e anunciada em nivel error na propria
        # requisicao (`whatsapp_webhook_dedup_guard_absent`) — nunca um caminho silencioso.
        logger.error(
            "whatsapp_dispatch_failed",
            tenant=tenant,
            message_pseudonym=log_safe_message_id(
                message.message_id, tenant, dedup.pseudonymizer if dedup is not None else None
            ),
            exc_info=True,
        )
        await _withdraw_claim(dedup, message, tenant=tenant)
        return "failed"
    await _seal_claim(dedup, message, tenant=tenant)
    return outcome


async def _run_background_turn(
    message: InboundEvent,
    *,
    dispatcher: HelenaDispatcher,
    dedup: WhatsAppDedupGuard | None,
    tenant: str,
) -> None:
    """ack-then-queue (R-072): the work Meta was ALREADY told 200 about.

    The outcome is logged rather than returned — there is no request left to answer. A `"failed"`
    outcome here is NOT retried by Meta (it already got its 200); what survives is the released
    claim plus this line. That asymmetry is the reason the flag defaults OFF — see
    `docs/processes/webhook-whatsapp-ack-then-queue.md`.
    """
    outcome = await _run_one_message(message, dispatcher=dispatcher, dedup=dedup, tenant=tenant)
    logger.info(
        "whatsapp_webhook_queued_turn_finished",
        tenant=tenant,
        outcome=outcome,
        dedup_key=dedup.inbound_key(message.message_id) if dedup is not None else None,
    )


def create_app(
    settings: WhatsAppWebhookSettings,
    *,
    is_live: Callable[[], bool] | None = None,
    dispatcher: HelenaDispatcher | None = None,
    dedup: WhatsAppDedupGuard | None = None,
) -> FastAPI:
    """Build the webhook-receiver FastAPI app: health endpoints (via `platform.health`) plus the
    two WhatsApp endpoints, on the SAME app instance (Helm serves both from one pod/port,
    `deployment-webhook-receiver.yaml:66-88`).

    `is_live` is forwarded to `create_health_app` unchanged — the caller (`service.py`) flips it
    False on SIGTERM so `/healthz` participates in the same drain semantics as the other three
    daemons (design §8: liveness leaves the load-balancing rotation before the server stops).

    `dispatcher` (T1.11): when given, a signature-verified POST with at least one text message
    runs `dispatcher.dispatch(...)` for each — real Helena turns, real DMN/engine calls; a non-text
    message runs `dispatcher.acknowledge_non_text(...)` instead (one fixed reply, no turn). `None`
    (e.g. this replica's STEP B dependency bring-up failed) preserves the prior explicit-501
    behavior for any POST that actually contains a message — including a non-text one, which this
    replica can no more acknowledge than it can dispatch, and must not silently swallow.

    `dedup` (gap `WEBHOOK-WAMID-DEDUP`): the durable `wamid` guard. The PRODUCTION composition
    root always supplies it — `service.py` builds it from the SAME mandatory `DATABASE_URL` the
    dispatcher already fails closed on, so a replica that has a dispatcher has a guard. `None` is
    a TEST-ONLY affordance (it keeps the pre-dedup behavior byte for byte) and is ANNOUNCED at
    error level on every request that would dispatch — never a silent ungated path.
    """

    async def config_loaded() -> CheckResult:
        # Trivially healthy once we got this far — `WhatsAppWebhookSettings` construction (in
        # __main__.py, BEFORE this app is even built) already fails closed (raises) if
        # WHATSAPP_APP_SECRET / WHATSAPP_VERIFY_TOKEN are absent (constraint 2). This check exists
        # so /readyz is self-documenting rather than an empty (always-200) list.
        return CheckResult(name="config_loaded", healthy=True, detail=f"tenant={settings.tenant_id}")

    app = create_health_app(
        readiness_checks=[config_loaded], is_live=is_live, registry=get_metrics_collector().registry
    )

    @app.get("/webhook", include_in_schema=False)
    async def verify_webhook(request: Request) -> PlainTextResponse:
        """Meta verification handshake — docs/runbooks/whatsapp-webhook.md §1."""
        params = request.query_params
        mode = params.get("hub.mode")
        challenge = params.get("hub.challenge", "")
        token = params.get("hub.verify_token", "")

        # UTF-8 bytes, not `str`: `hmac.compare_digest` REFUSES a non-ASCII `str` with a
        # TypeError, and `hub.verify_token` is attacker-controlled — comparing the raw
        # query param turned `?hub.verify_token=café` into an unhandled 500 instead of the
        # 403 below. Encoding first keeps the handshake fail-closed for every input.
        if mode == "subscribe" and hmac.compare_digest(
            token.encode("utf-8"), settings.verify_token.encode("utf-8")
        ):
            WEBHOOK_REQUESTS_TOTAL.labels(tenant=settings.tenant_id, status="ok").inc()
            return PlainTextResponse(challenge, status_code=200)

        logger.warning("whatsapp_webhook_verify_failed", mode=mode)
        WEBHOOK_REQUESTS_TOTAL.labels(tenant=settings.tenant_id, status="invalid_signature").inc()
        return PlainTextResponse("forbidden", status_code=403)

    @app.post("/webhook", include_in_schema=False)
    async def receive_event(request: Request) -> JSONResponse:
        """Event ingestion — signature-verified, then real dispatch to Helena (T1.11) when a
        dispatcher is configured, else the prior explicit, documented 501 (module docstring)."""
        body = await request.body()
        signature_header = request.headers.get("X-Hub-Signature-256")

        if not verify_hub_signature(body, signature_header, settings.app_secret):
            logger.warning("whatsapp_webhook_invalid_signature")
            WEBHOOK_REQUESTS_TOTAL.labels(tenant=settings.tenant_id, status="invalid_signature").inc()
            return JSONResponse(status_code=401, content={"status": "invalid_signature"})

        try:
            payload = json.loads(body)
        except (ValueError, UnicodeDecodeError):
            logger.warning("whatsapp_webhook_parse_error")
            WEBHOOK_REQUESTS_TOTAL.labels(tenant=settings.tenant_id, status="parse_error").inc()
            return JSONResponse(status_code=400, content={"status": "parse_error"})

        messages = extract_inbound_messages(payload)
        if not messages:
            # No actual message at all (e.g. a delivery-status callback, or a malformed entry —
            # `extract_inbound_messages` already logged it). Nothing fabricated: just ack. NOTE:
            # a non-text message no longer lands here; it is a real message and is acknowledged
            # below (gap `WHATSAPP-NON-TEXT-DROPPED`).
            WEBHOOK_REQUESTS_TOTAL.labels(tenant=settings.tenant_id, status="ok").inc()
            return JSONResponse(status_code=200, content={"status": "ok", "dispatched": 0})

        if dispatcher is None:
            logger.warning(
                "whatsapp_webhook_dispatcher_not_configured",
                note="signature verified; message(s) present but no HelenaDispatcher is wired "
                "for this replica (dependency bring-up failed or disabled) — see "
                "service.py's STEP B / module docstring",
            )
            WEBHOOK_REQUESTS_TOTAL.labels(tenant=settings.tenant_id, status="not_implemented").inc()
            return JSONResponse(
                status_code=501,
                content={
                    "status": "not_implemented",
                    "detail": (
                        "signature verified; message present but no dispatcher is configured "
                        "for this replica (dependency bring-up failed or disabled) — this "
                        "receiver does not fabricate a Helena turn without one"
                    ),
                },
            )

        if dedup is None:
            # TEST-ONLY affordance (see `create_app`'s docstring): the production root always
            # supplies a guard. Announced at error level, never silent — a deployed receiver that
            # reaches this line is dispatching without duplicate protection.
            logger.error(
                "whatsapp_webhook_dedup_guard_absent",
                tenant=settings.tenant_id,
                detail="no WhatsAppDedupGuard on this app — a Meta redelivery WILL run a second "
                "Helena turn (gap WEBHOOK-WAMID-DEDUP). The production composition root always "
                "supplies one; reaching this branch in a deployed receiver is a wiring defect.",
            )

        dispatched = 0
        acked = 0
        failed = 0
        duplicates = 0
        queued = 0
        #: Campos do primeiro turno despachado, devolvidos no corpo SO' com `devolve_turno`.
        turno_do_lote: dict[str, str] = {}
        # Whether the BATCH carried non-text at all — decided by the INPUT shape, not by the
        # outcome, so the `acked` field below does not disappear from the body exactly when an
        # acknowledgement failed. A text-only batch keeps the pre-change body byte for byte.
        non_text_present = any(not isinstance(message, InboundMessage) for message in messages)
        ack_then_queue = settings.ack_then_queue

        if ack_then_queue and dedup is None:
            # The durable claim IS the queue's entry leg (R-072). Without it, "ack then queue"
            # would be "ack then hope": nothing durable would record that the turn is owed.
            logger.error("whatsapp_webhook_ack_then_queue_without_registry", tenant=settings.tenant_id)
            WEBHOOK_REQUESTS_TOTAL.labels(tenant=settings.tenant_id, status="dedup_unavailable").inc()
            return JSONResponse(
                status_code=500,
                content={
                    "status": "dedup_unavailable",
                    "detail": "ack-then-queue is enabled but no durable dedup registry is wired — "
                    "refusing to acknowledge work this replica cannot durably record",
                },
            )

        for message in messages:
            if dedup is not None:
                if not message.message_id:
                    # Meta always sends `id`; a message without one cannot be deduped at all.
                    # Dispatched anyway (dropping a real beneficiary message would be worse) and
                    # announced, because the duplicate-protection claim does NOT hold for it.
                    logger.error(
                        "whatsapp_webhook_message_without_wamid",
                        tenant=settings.tenant_id,
                        detail="inbound message carries no wamid — dispatched WITHOUT duplicate "
                        "protection (gap WEBHOOK-WAMID-DEDUP cannot key this delivery)",
                    )
                else:
                    try:
                        first_delivery = await dedup.claim_inbound(message.message_id)
                    except DedupRegistryUnavailableError as exc:
                        # FAIL CLOSED for the whole request. Dispatching on the word of a registry
                        # that cannot answer would re-open the duplicate-reply landmine exactly
                        # when the platform is already degraded; a non-2xx makes Meta re-deliver,
                        # and whatever this batch already processed is sealed and will be
                        # suppressed on that redelivery. EXACTLY ONE counter increment.
                        logger.error(
                            "whatsapp_webhook_dedup_unavailable",
                            tenant=settings.tenant_id,
                            error=exc,
                            exc_info=True,
                        )
                        WEBHOOK_REQUESTS_TOTAL.labels(
                            tenant=settings.tenant_id, status="dedup_unavailable"
                        ).inc()
                        return JSONResponse(
                            status_code=500,
                            content={
                                "status": "dedup_unavailable",
                                "detail": "the durable wamid dedup registry is unreachable — "
                                "refusing to dispatch without duplicate protection",
                            },
                        )
                    if not first_delivery:
                        duplicates += 1
                        # The wamid itself is NOT logged: it base64-embeds the counterpart phone
                        # number (`security.py::hash_message_id`). The keyed pseudonym is.
                        logger.info(
                            "whatsapp_webhook_duplicate_suppressed",
                            tenant=settings.tenant_id,
                            dedup_key=dedup.inbound_key(message.message_id),
                        )
                        continue

            if ack_then_queue:
                # R-072: the claim is durable, so the turn no longer has to finish inside Meta's
                # ack window. Nothing is dispatched synchronously here.
                _spawn_background_turn(
                    _run_background_turn(
                        message, dispatcher=dispatcher, dedup=dedup, tenant=settings.tenant_id
                    )
                )
                queued += 1
                continue

            outcome = await _run_one_message(
                message,
                dispatcher=dispatcher,
                dedup=dedup,
                tenant=settings.tenant_id,
                # PRIMEIRO turno despachado do lote, e so' ele. A rota de teste do Canal manda uma
                # mensagem por requisicao, entao "o primeiro" e "o unico" coincidem; num lote de
                # varias, devolver o primeiro e' a escolha honesta — devolver o ultimo faria o
                # corpo depender da ordem em que a Meta empacotou, que nao e' contrato de ninguem.
                turno=turno_do_lote if not turno_do_lote else None,
            )
            if outcome == "dispatched":
                dispatched += 1
            elif outcome == "acked":
                acked += 1
            else:
                failed += 1

        if ack_then_queue:
            # EXACTLY ONE counter increment per request, as everywhere else in this handler. The
            # body says `queued`, never `dispatched`: nothing was processed yet, and an ack that
            # claimed otherwise would be the fabricated-success pattern this repo fences against.
            status = "queued" if queued else "duplicate"
            WEBHOOK_REQUESTS_TOTAL.labels(tenant=settings.tenant_id, status=status).inc()
            queued_content: dict[str, object] = {"status": status, "queued": queued}
            if duplicates:
                queued_content["duplicates"] = duplicates
            return JSONResponse(status_code=200, content=queued_content)

        succeeded = dispatched + acked
        if succeeded == 0 and duplicates == 0 and failed > 0:
            # NOTHING in the batch succeeded -> 500 so Meta retries (unchanged semantics; the
            # `acked == 0` term only keeps a partially successful batch out of this branch).
            WEBHOOK_REQUESTS_TOTAL.labels(tenant=settings.tenant_id, status="dispatch_failed").inc()
            content: dict[str, object] = {
                "status": "dispatch_failed",
                "dispatched": dispatched,
                "failed": failed,
            }
            if non_text_present:
                content["acked"] = acked
            return JSONResponse(status_code=500, content=content)

        if failed > 0:
            # MIXED BATCH — gap `WHATSAPP-MIXED-BATCH-RETRY-TRADEOFF`, owner decision R-100.
            # Something in this batch really happened and something else failed. Until now the
            # two were indistinguishable from a clean `ok` in the metric, so a lost non-text ack
            # was invisible; the label below is the observability half of R-100.
            #
            # THE HTTP CODE IS THE FLIP CRITERION THE OWNER WROTE, EVALUATED IN CODE: "quando a
            # guarda de dedup por `wamid` estiver ativa no caminho de entrada, o lote misto passa
            # a devolver `500`, sem nova decisao do dono". With a guard (production), 500 is now
            # SAFE and strictly better: Meta re-delivers the whole batch, the parts that already
            # succeeded are suppressed as duplicates, and only the failed message runs again — so
            # the beneficiary whose message failed gets a real second chance without anybody
            # receiving a duplicate reply. Without a guard the old trade-off still holds (a 500
            # would re-send what already succeeded), so the answer stays 200 and the failure is
            # visible only in the label and in the `whatsapp_dispatch_failed` log line above.
            WEBHOOK_REQUESTS_TOTAL.labels(tenant=settings.tenant_id, status="partial_failure").inc()
            partial_content: dict[str, object] = {
                "status": "partial_failure" if dedup is not None else "ok",
                "dispatched": dispatched,
                "failed": failed,
            }
            if non_text_present:
                partial_content["acked"] = acked
            if duplicates:
                partial_content["duplicates"] = duplicates
            return JSONResponse(status_code=500 if dedup is not None else 200, content=partial_content)

        # EXACTLY ONE counter increment per request, as everywhere else in this handler.
        if succeeded == 0 and duplicates > 0:
            status = "duplicate"  # the whole batch was a Meta redelivery; no effect ran
        elif dispatched == 0 and acked > 0:
            status = "non_text_acked"
        else:
            status = "ok"
        WEBHOOK_REQUESTS_TOTAL.labels(tenant=settings.tenant_id, status=status).inc()
        # The BODY's `status` stays "ok" for every 2xx: it is Meta's ack, whose only contract is
        # the status code (`docs/runbooks/whatsapp-webhook.md` §6), and the ack-only case is
        # distinguished where operators actually read it — the metric label above.
        ok_content: dict[str, object] = {"status": "ok", "dispatched": dispatched, "failed": failed}
        if non_text_present:
            ok_content["acked"] = acked
        if duplicates:
            ok_content["duplicates"] = duplicates
        # O TURNO, sob portao (12/09/2026). Sem `devolve_turno` o corpo acima fica byte por byte
        # como sempre foi — e' o que a Meta recebe em producao, cujo contrato e' o codigo de
        # status. Com ele, quem assinou a requisicao (em dev, o Canal de Teste) le' o texto que a
        # Helena redigiu e a conversa em que o turno caiu. `escalation_business_key` NAO entra
        # aqui: a pagina a deriva de `ESC-{tenant}-{conversation_id}`, e devolver a chave pronta
        # criaria uma segunda fonte para a mesma verdade.
        if settings.devolve_turno and turno_do_lote:
            ok_content["resposta"] = turno_do_lote.get("resposta", "")
            ok_content["conversation_id"] = turno_do_lote.get("conversation_id", "")
        return JSONResponse(status_code=200, content=ok_content)

    return app

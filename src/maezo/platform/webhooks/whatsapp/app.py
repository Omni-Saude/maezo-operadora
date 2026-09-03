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
"""

from __future__ import annotations

import hmac
import json
from collections.abc import Callable

import structlog
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, PlainTextResponse
from prometheus_client import Counter

from maezo.platform.health import CheckResult, create_health_app
from maezo.platform.observability import get_metrics_collector

from .dispatch import HelenaDispatcher, extract_inbound_messages
from .security import verify_hub_signature
from .settings import WhatsAppWebhookSettings

logger = structlog.get_logger(__name__)

#: `docs/runbooks/whatsapp-webhook.md` §7 — labels `tenant`, `status`. `status` values this build
#: actually emits: "ok" (GET handshake success; POST ack with 0 or more messages dispatched),
#: "invalid_signature", "parse_error", "dispatch_failed" (every message in the batch raised),
#: "not_implemented" (signature verified, an actual message needs dispatch, but no dispatcher is
#: configured for this replica — see module docstring). Module-level (not per-`create_app()`
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


def create_app(
    settings: WhatsAppWebhookSettings,
    *,
    is_live: Callable[[], bool] | None = None,
    dispatcher: HelenaDispatcher | None = None,
) -> FastAPI:
    """Build the webhook-receiver FastAPI app: health endpoints (via `platform.health`) plus the
    two WhatsApp endpoints, on the SAME app instance (Helm serves both from one pod/port,
    `deployment-webhook-receiver.yaml:66-88`).

    `is_live` is forwarded to `create_health_app` unchanged — the caller (`service.py`) flips it
    False on SIGTERM so `/healthz` participates in the same drain semantics as the other three
    daemons (design §8: liveness leaves the load-balancing rotation before the server stops).

    `dispatcher` (T1.11): when given, a signature-verified POST with at least one text message
    runs `dispatcher.dispatch(...)` for each — real Helena turns, real DMN/engine calls. `None`
    (e.g. this replica's STEP B dependency bring-up failed) preserves the prior explicit-501
    behavior for any POST that actually contains a message to dispatch.
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
            # No actual text message to dispatch (e.g. a delivery-status callback, or a
            # message type this build doesn't parse — `extract_inbound_messages` already logged
            # it). Nothing fabricated: just ack.
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

        dispatched = 0
        failed = 0
        for message in messages:
            try:
                await dispatcher.dispatch(message)
                dispatched += 1
            except Exception:  # noqa: BLE001 — one message's failure must not drop the batch.
                failed += 1
                logger.error("whatsapp_dispatch_failed", message_id=message.message_id, exc_info=True)

        if dispatched == 0 and failed > 0:
            WEBHOOK_REQUESTS_TOTAL.labels(tenant=settings.tenant_id, status="dispatch_failed").inc()
            return JSONResponse(
                status_code=500,
                content={"status": "dispatch_failed", "dispatched": dispatched, "failed": failed},
            )
        WEBHOOK_REQUESTS_TOTAL.labels(tenant=settings.tenant_id, status="ok").inc()
        return JSONResponse(
            status_code=200, content={"status": "ok", "dispatched": dispatched, "failed": failed}
        )

    return app

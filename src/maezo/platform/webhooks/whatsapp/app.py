"""WhatsApp Cloud API webhook receiver — FastAPI app (T1.6, defect B1).

Endpoint contract per `docs/runbooks/whatsapp-webhook.md` §1 (Meta's own webhook contract, not
this build's invention):

    GET  /webhook   Meta verification handshake (`hub.mode`/`hub.challenge`/`hub.verify_token`).
    POST /webhook   Event ingestion (messages + delivery status).

**Capability honesty — read this before extending the POST handler (constraint 3).** The GET
handshake and the POST signature verification below are REAL: a genuine HMAC-SHA256 check
(`security.py`, timing-safe) against `WHATSAPP_APP_SECRET`, exactly per §2 of the runbook. What
happens AFTER a signature verifies is deliberately NOT the full pipeline the runbook's §3/§4
describe (idempotency store, WhatsAppMessageEvent normalization, Kafka publish to
`agents.events.whatsapp.*`): there is no consumer of that Kafka topic yet — Helena's WhatsApp
intake graph is T1.11's job (`docs/design/T1.1-runtime-spine.md` §17 Q-6). Publishing "real" Kafka
events into a topic nothing reads would not be a fabrication in the technical sense (the publish
call would genuinely succeed), but it WOULD be dead, unverifiable machinery this build cannot
honestly claim is "the WhatsApp intake path" — so a signature-verified POST returns 501 with an
explicit, documented reason instead of silently pretending to queue the message (charter:
"explicitly-labeled 501/queue-less behavior documented — NO fabricated processing"). Wiring the
Kafka publish is a small, mechanical follow-up once something consumes the topic.
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

from .security import verify_hub_signature
from .settings import WhatsAppWebhookSettings

logger = structlog.get_logger(__name__)

#: `docs/runbooks/whatsapp-webhook.md` §7 — labels `tenant`, `status`. `status` values this build
#: actually emits: "ok" (GET handshake success), "invalid_signature", "parse_error",
#: "not_implemented" (signature verified, but see the module docstring — queue-less by design).
#: Module-level (not per-`create_app()` call) — `Counter()` registers into the collector's
#: registry once at import time; redefining it per app instance would raise a duplicate-timeseries
#: error the second time a test constructs an app. Registered on the SAME dedicated registry
#: `MetricsCollector` owns (not the global default) — matches worker_runtime/agent_runtime/gateway
#: (T1.1/T1.6) and `MetricsCollector`'s own docstring: "so metrics don't collide with the default
#: PROCESS_COLLECTOR or other libraries." `create_app` below exposes this SAME registry via
#: `/metrics`, or the counter would silently never appear there.
WEBHOOK_REQUESTS_TOTAL = Counter(
    "maezo_webhook_requests_total",
    "WhatsApp webhook requests by outcome",
    ["tenant", "status"],
    registry=get_metrics_collector().registry,
)


def create_app(settings: WhatsAppWebhookSettings, *, is_live: Callable[[], bool] | None = None) -> FastAPI:
    """Build the webhook-receiver FastAPI app: health endpoints (via `platform.health`) plus the
    two WhatsApp endpoints, on the SAME app instance (Helm serves both from one pod/port,
    `deployment-webhook-receiver.yaml:66-88`).

    `is_live` is forwarded to `create_health_app` unchanged — the caller (`service.py`) flips it
    False on SIGTERM so `/healthz` participates in the same drain semantics as the other three
    daemons (design §8: liveness leaves the load-balancing rotation before the server stops).
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

        if mode == "subscribe" and hmac.compare_digest(token, settings.verify_token):
            WEBHOOK_REQUESTS_TOTAL.labels(tenant=settings.tenant_id, status="ok").inc()
            return PlainTextResponse(challenge, status_code=200)

        logger.warning("whatsapp_webhook_verify_failed", mode=mode)
        WEBHOOK_REQUESTS_TOTAL.labels(tenant=settings.tenant_id, status="invalid_signature").inc()
        return PlainTextResponse("forbidden", status_code=403)

    @app.post("/webhook", include_in_schema=False)
    async def receive_event(request: Request) -> JSONResponse:
        """Event ingestion — signature-verified, then an explicit, documented 501 (see module
        docstring: queue-less by design, no downstream consumer yet, T1.11)."""
        body = await request.body()
        signature_header = request.headers.get("X-Hub-Signature-256")

        if not verify_hub_signature(body, signature_header, settings.app_secret):
            logger.warning("whatsapp_webhook_invalid_signature")
            WEBHOOK_REQUESTS_TOTAL.labels(tenant=settings.tenant_id, status="invalid_signature").inc()
            return JSONResponse(status_code=401, content={"status": "invalid_signature"})

        try:
            json.loads(body)
        except (ValueError, UnicodeDecodeError):
            logger.warning("whatsapp_webhook_parse_error")
            WEBHOOK_REQUESTS_TOTAL.labels(tenant=settings.tenant_id, status="parse_error").inc()
            return JSONResponse(status_code=400, content={"status": "parse_error"})

        logger.info(
            "whatsapp_webhook_received_not_queued",
            note="signature verified; message queuing pending T1.11 (Helena WhatsApp intake "
            "graph) — no downstream consumer wired yet, see app.py module docstring",
        )
        WEBHOOK_REQUESTS_TOTAL.labels(tenant=settings.tenant_id, status="not_implemented").inc()
        return JSONResponse(
            status_code=501,
            content={
                "status": "not_implemented",
                "detail": (
                    "signature verified; message queuing pending T1.11 (Helena WhatsApp intake "
                    "graph) — this receiver does not yet publish to Kafka (queue-less scaffold, "
                    "T1.6)"
                ),
            },
        )

    return app

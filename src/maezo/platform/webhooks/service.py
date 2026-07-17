"""Webhook-receiver daemon (T1.6, defect B1) — serves the WhatsApp app built in `whatsapp/app.py`.

`deployment-webhook-receiver.yaml` runs `command: ["python", "-m", "maezo.platform.webhooks"]`,
but `src/maezo/platform/webhooks/` did not exist at all — `webhookReceiver.enabled: true` in
`values.yaml` (already ON, unlike `gateway`) meant this Deployment was CrashLoopBackOff'ing in
any environment that actually applied the chart. This module closes that gap.

Health-first bring-up, matching the `worker_runtime`/`agent_runtime`/`gateway` pattern
(T1.1/T1.6), but simpler still — `WhatsAppWebhookSettings` construction (in `__main__.py`, BEFORE
`run()` is even called) is ITSELF the fail-closed boot gate (constraint 2): a missing
`WHATSAPP_APP_SECRET`/`WHATSAPP_VERIFY_TOKEN` raises a pydantic `ValidationError` before any
server binds, matching `deployment-webhook-receiver.yaml:43-46`'s documented expectation exactly.
There is no further bounded/non-fatal dependency bring-up here (no engine, no DB, no Kafka
producer is constructed by this build — see `whatsapp/app.py`'s module docstring for why).

  STEP A  Bind the app (health + `/webhook`) IMMEDIATELY.
  STEP B  (none — nothing further to bring up in this build.)
  STEP C  `/readyz` reflects the one trivial `config_loaded` check (`whatsapp/app.py`).
  STEP D  SIGTERM/SIGINT -> drain: `live=False` (`/healthz` -> 503), stop the server.
"""

from __future__ import annotations

import asyncio
import contextlib
import signal
from dataclasses import dataclass

import structlog

from maezo.platform.health import build_health_server

from .whatsapp.app import create_app
from .whatsapp.settings import WhatsAppWebhookSettings

logger = structlog.get_logger(__name__)


@dataclass
class WebhookState:
    settings: WhatsAppWebhookSettings
    live: bool = True

    def is_live(self) -> bool:
        return self.live


async def run(settings: WhatsAppWebhookSettings) -> None:
    """Run the webhook-receiver until SIGTERM/SIGINT. See module docstring for STEP A..D."""
    logger.info("webhook_receiver_starting", tenant=settings.tenant_id, health_port=settings.health_port)

    state = WebhookState(settings=settings)
    # STEP A: bind the app (health + /webhook) IMMEDIATELY — no dependency bring-up precedes it
    # in this build (see module docstring).
    app = create_app(settings, is_live=state.is_live)
    server = build_health_server(app, port=settings.health_port)
    server.capture_signals = contextlib.nullcontext  # type: ignore[assignment]  # we own the signals
    shutdown = asyncio.Event()
    loop = asyncio.get_running_loop()
    serve_task = asyncio.create_task(server.serve(), name="webhook-server")

    def _request_shutdown(sig: signal.Signals) -> None:
        logger.info("shutdown_signal", signal=sig.name)
        state.live = False
        server.should_exit = True
        shutdown.set()

    def _on_serve_done(task: asyncio.Task[None]) -> None:
        state.live = False
        if not shutdown.is_set():
            exc = None if task.cancelled() else task.exception()
            logger.error("health_server_stopped_early", error=repr(exc) if exc else "no_exception")
            shutdown.set()

    serve_task.add_done_callback(_on_serve_done)
    for _sig in (signal.SIGTERM, signal.SIGINT):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(_sig, _request_shutdown, _sig)

    logger.info("webhook_receiver_running")
    await shutdown.wait()

    server.should_exit = True
    with contextlib.suppress(asyncio.CancelledError, SystemExit):
        await serve_task

    logger.info("webhook_receiver_stopped", tenant=settings.tenant_id)

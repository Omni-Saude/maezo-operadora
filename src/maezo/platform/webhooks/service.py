"""Webhook-receiver daemon (T1.6, defect B1; T1.11 real dispatch) — serves the WhatsApp app
built in `whatsapp/app.py`.

`deployment-webhook-receiver.yaml` runs `command: ["python", "-m", "maezo.platform.webhooks"]`,
but `src/maezo/platform/webhooks/` did not exist at all — `webhookReceiver.enabled: true` in
`values.yaml` (already ON, unlike `gateway`) meant this Deployment was CrashLoopBackOff'ing in
any environment that actually applied the chart. T1.6 closed that gap with a health-only
scaffold; T1.11 adds the bounded, non-fatal dependency bring-up Helena's real dispatch needs.

Health-first bring-up, matching the `worker_runtime`/`agent_runtime` pattern (T1.1/T1.6):
`WhatsAppWebhookSettings` construction (in `__main__.py`, BEFORE `run()` is even called) is
ITSELF the fail-closed boot gate (constraint 2): a missing `WHATSAPP_APP_SECRET`/
`WHATSAPP_VERIFY_TOKEN` raises a pydantic `ValidationError` before any server binds, matching
`deployment-webhook-receiver.yaml:43-46`'s documented expectation exactly.

  STEP A  Bring up Helena's dispatch dependencies, BOUNDED and NON-FATAL (same isolation
          discipline as `worker_runtime`/`agent_runtime`'s `_bring_up_dependencies`): the
          inference provider, the DMN transport, the CIB Seven start transport, the WhatsApp
          send client, and the pseudonymizer. Every one of these constructors is PURE (no
          network call happens until a node actually runs) — unlike the engine-dialing STEP B
          in `worker_runtime`/`agent_runtime`, this genuinely never blocks, so it runs BEFORE
          binding the health server without risking `/healthz`'s liveness promise. On success,
          `/webhook` dispatches for real; on failure (caught, logged), it keeps the prior
          explicit-501 behavior for any message that needs dispatch — never a fabricated
          dispatch. No Kafka producer is constructed (still true — see `dispatch.py`'s module
          docstring for why an in-process call, not a queue, is this build's honest choice).
  STEP B  Bind the app (health + `/webhook`) with the STEP A result baked in.
  STEP C  `/readyz` reflects `config_loaded` (`whatsapp/app.py`) — dispatch readiness is NOT a
          separate gate: an unconfigured dispatcher degrades `/webhook` to 501, it does not
          affect `/readyz` (mirrors `agent_runtime`'s stance that a "ready" pod may still be
          missing an optional capability, as long as it says so honestly per request).
  STEP D  SIGTERM/SIGINT -> drain: `live=False` (`/healthz` -> 503), stop the server, close the
          long-lived CIB Seven transport.
"""

from __future__ import annotations

import asyncio
import contextlib
import signal
from dataclasses import dataclass

import structlog

from maezo.gateway.pseudonymizer import Pseudonymizer
from maezo.platform.health import build_health_server
from maezo.runtime.inference import InferenceProvider
from maezo.tools.mcp_cibseven.transport import CibSevenHttpTransport
from maezo.tools.mcp_whatsapp.server import WhatsAppServer
from maezo.tools.workers.dmn_transport import CibSevenDmnTransport

from .whatsapp.app import create_app
from .whatsapp.dispatch import HelenaDispatcher
from .whatsapp.settings import WhatsAppWebhookSettings

logger = structlog.get_logger(__name__)


@dataclass
class WebhookState:
    settings: WhatsAppWebhookSettings
    live: bool = True
    dispatcher: HelenaDispatcher | None = None
    dispatcher_error: str | None = None
    cibseven_transport: CibSevenHttpTransport | None = None

    def is_live(self) -> bool:
        return self.live


def _build_dispatcher(settings: WhatsAppWebhookSettings) -> tuple[HelenaDispatcher, CibSevenHttpTransport]:
    """Construct Helena's dispatch dependencies. Pure construction — no network call happens
    until a node actually runs (mirrors `agent_runtime`'s `_build_tool_deps`)."""
    inference = InferenceProvider()
    dmn = CibSevenDmnTransport(settings.cibseven_base_url)
    cibseven = CibSevenHttpTransport(settings.cibseven_base_url)
    whatsapp_client = WhatsAppServer()
    dispatcher = HelenaDispatcher(
        tenant_id=settings.tenant_id,
        inference=inference,
        dmn=dmn,
        cibseven=cibseven,
        whatsapp_client=whatsapp_client,
        pseudonymizer=Pseudonymizer(),
    )
    return dispatcher, cibseven


def _bring_up_dependencies(state: WebhookState) -> None:
    """STEP A: bounded, non-fatal, and provably non-blocking (module docstring). Failure leaves
    `state.dispatcher` `None` — `/webhook` degrades to the explicit 501 for any message needing
    dispatch (never a fabricated dispatch)."""
    try:
        state.dispatcher, state.cibseven_transport = _build_dispatcher(state.settings)
        logger.info("webhook_dispatcher_ready", tenant=state.settings.tenant_id)
    except Exception as exc:  # noqa: BLE001 — isolated: liveness/readiness must stay up.
        state.dispatcher_error = f"{type(exc).__name__}: {exc}"
        logger.error("webhook_dispatcher_build_failed", exc_info=True)


async def run(settings: WhatsAppWebhookSettings) -> None:
    """Run the webhook-receiver until SIGTERM/SIGINT. See module docstring for STEP A..D."""
    logger.info("webhook_receiver_starting", tenant=settings.tenant_id, health_port=settings.health_port)

    state = WebhookState(settings=settings)
    # STEP A: bring up dispatch dependencies — pure construction, no I/O (module docstring).
    _bring_up_dependencies(state)

    # STEP B: bind the app (health + /webhook) with the STEP A result baked in.
    app = create_app(settings, is_live=state.is_live, dispatcher=state.dispatcher)
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

    if state.cibseven_transport is not None:
        with contextlib.suppress(Exception):
            await state.cibseven_transport.close()

    logger.info("webhook_receiver_stopped", tenant=settings.tenant_id)

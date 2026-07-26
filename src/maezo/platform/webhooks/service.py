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
          send client, and the pseudonymizer — all PURE constructors (no network call until a
          node runs) — PLUS (T4b) the ONE bounded bring-up I/O this receiver now performs: the
          durable LangGraph checkpointer's connect+`setup()` (multi-turn conversation state that
          survives webhook invocations / receiver restarts), bounded by `dep_connect_timeout_s`
          so a hung Postgres cannot stall the `/healthz` bind that follows. F2 FAIL-CLOSED: in
          production (`runtime_mode != "local"`) a checkpointer that fails to provision makes the
          receiver REFUSE TO SERVE — the dispatcher is dropped and `/webhook` returns its explicit
          501 rather than running Helena stateless; local/dev falls back to an in-memory saver
          with a loud warning. On success, `/webhook` dispatches for real; on any failure (caught,
          logged) it keeps the prior explicit-501 behavior — never a fabricated dispatch, never a
          silently-stateless prod dispatch. No Kafka producer is constructed (still true — see
          `dispatch.py`'s module docstring for why an in-process call, not a queue, is this
          build's honest choice).
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

from maezo.gateway.audit_postgres import PostgresAuditSink
from maezo.gateway.pseudonymizer import Pseudonymizer
from maezo.platform.health import build_health_server
from maezo.runtime.checkpoint import Checkpointer, provision_checkpointer
from maezo.runtime.inference import InferenceProvider
from maezo.tools.mcp_cibseven.transport import CibSevenHttpTransport
from maezo.tools.mcp_whatsapp.server import WhatsAppServer
from maezo.tools.workers.dmn_transport import CibSevenDmnTransport

from .whatsapp.app import create_app
from .whatsapp.dispatch import HelenaDispatcher
from .whatsapp.settings import WhatsAppWebhookSettings

#: F2 mode discriminator — the ONLY non-production `runtime_mode`. Anything else (Helm injects
#: "kubernetes") is PRODUCTION, where a checkpointer that fails to provision makes the receiver
#: refuse to serve. Mirrors `agent_runtime.service._LOCAL_RUNTIME_MODE`.
_LOCAL_RUNTIME_MODE = "local"

logger = structlog.get_logger(__name__)


@dataclass
class WebhookState:
    settings: WhatsAppWebhookSettings
    live: bool = True
    dispatcher: HelenaDispatcher | None = None
    dispatcher_error: str | None = None
    cibseven_transport: CibSevenHttpTransport | None = None
    # T4b: the durable LangGraph checkpointer wired into the dispatcher (its pool is released on
    # drain). None when the dispatcher was never built, or when a prod-mode provision failed
    # closed (in which case `dispatcher` is also None -> `/webhook` 501, refuse-to-serve).
    checkpointer: Checkpointer | None = None
    checkpointer_backend: str | None = None

    def is_live(self) -> bool:
        return self.live


def _build_dispatcher(settings: WhatsAppWebhookSettings) -> tuple[HelenaDispatcher, CibSevenHttpTransport]:
    """Construct Helena's dispatch dependencies. Pure construction — no network call happens
    until a node actually runs (mirrors `agent_runtime`'s `_build_tool_deps`).

    T-C2 fail-closed: Helena's escalation start (SP-OP-ESCALATION-001) structurally requires a
    durable ADR-0007 audit sink (`start_process_idempotent`'s fence). Without `DATABASE_URL` the
    sink cannot be constructed, so this raises — caught by `_bring_up_dependencies`, leaving
    `state.dispatcher` None so `/webhook` degrades to its explicit 501 (module docstring STEP A):
    Helena never starts an un-audited escalation."""
    if not settings.database_url:
        raise ValueError(
            "DATABASE_URL is required to build Helena's dispatcher: the escalation process start "
            "must audit to a durable ADR-0007 sink BEFORE any engine effect (T-C2 fence). Refusing "
            "to construct a dispatcher that could start an un-audited escalation."
        )
    inference = InferenceProvider()
    dmn = CibSevenDmnTransport(settings.cibseven_base_url)
    cibseven = CibSevenHttpTransport(settings.cibseven_base_url)
    whatsapp_client = WhatsAppServer()
    # Pure construction (asyncpg pool is lazy) — mirrors the transports above.
    audit_sink = PostgresAuditSink(settings.database_url, settings.tenant_id)
    dispatcher = HelenaDispatcher(
        tenant_id=settings.tenant_id,
        inference=inference,
        dmn=dmn,
        cibseven=cibseven,
        whatsapp_client=whatsapp_client,
        pseudonymizer=Pseudonymizer(),
        audit_sink=audit_sink,
    )
    return dispatcher, cibseven


async def _provision_dispatch_checkpointer(state: WebhookState) -> None:
    """T4b: attach the durable LangGraph checkpointer to the just-built dispatcher, with F2
    fail-closed discipline (shared `runtime.checkpoint.provision_checkpointer` — the SAME policy
    the agent-runtime daemon's readiness gate uses, so the two can never drift).

    Fail-closed SHAPE for a webhook (decided from how this receiver already handles a mandatory
    dep): a production receiver that cannot durably checkpoint must REFUSE TO SERVE — so on a
    prod-mode provision failure the just-built dispatcher is DROPPED (`state.dispatcher = None`),
    exactly like a missing audit sink / DSN already does, and `/webhook` returns its explicit 501
    rather than silently running Helena stateless (no resume-after-restart). In local/dev the
    provision falls back to an in-memory saver (loud warning) and the dispatcher is kept.

    Note: this receiver's DSN is ALREADY mandatory for the dispatcher to build at all (the T-C2
    audit sink), so the checkpointer's "DSN absent" branch is unreachable from here — the live
    fail-closed axis for the webhook is a connect/`setup()` FAILURE (or timeout), not a missing
    DSN (a missing DSN refuses even earlier, in `_build_dispatcher`)."""
    if state.dispatcher is None:
        return  # dispatcher never built (e.g. missing DSN) — nothing to checkpoint; already 501.
    settings = state.settings
    is_production = settings.runtime_mode != _LOCAL_RUNTIME_MODE
    provision = await provision_checkpointer(
        database_url=settings.database_url,
        is_production=is_production,
        component="webhook",
        setup_timeout_s=settings.dep_connect_timeout_s,
    )
    if not provision.ready:
        # FAIL-CLOSED (production): refuse to serve rather than run stateless. Drop the dispatcher
        # so `/webhook` returns the explicit 501 (same refuse-to-serve shape as a missing dep).
        state.dispatcher = None
        state.dispatcher_error = (
            f"durable checkpointer unavailable and runtime_mode={settings.runtime_mode!r} is "
            f"production — refusing to serve Helena stateless (T4b/F4 fail-closed): {provision.error}"
        )
        logger.error(
            "webhook_dispatcher_refused_no_durable_checkpointer",
            tenant=settings.tenant_id,
            runtime_mode=settings.runtime_mode,
            reason=provision.error,
        )
        return
    state.dispatcher.checkpointer = provision.checkpointer
    state.checkpointer = provision.checkpointer
    state.checkpointer_backend = provision.backend
    logger.info(
        "webhook_dispatch_checkpointer_ready",
        tenant=settings.tenant_id,
        backend=provision.backend,
    )


async def _bring_up_dependencies(state: WebhookState) -> None:
    """STEP A: bounded, non-fatal. Pure dispatcher CONSTRUCTION (no I/O) followed by the ONE
    bounded bring-up I/O this receiver now performs — the durable checkpointer's connect+setup()
    (T4b), bounded by `dep_connect_timeout_s`. Any failure leaves `state.dispatcher` `None` —
    `/webhook` degrades to the explicit 501 for a message needing dispatch (never a fabricated
    dispatch, never a silently-stateless prod dispatch)."""
    try:
        state.dispatcher, state.cibseven_transport = _build_dispatcher(state.settings)
        logger.info("webhook_dispatcher_ready", tenant=state.settings.tenant_id)
    except Exception as exc:  # noqa: BLE001 — isolated: liveness/readiness must stay up.
        state.dispatcher_error = f"{type(exc).__name__}: {exc}"
        logger.error("webhook_dispatcher_build_failed", exc_info=True)
        return

    # T4b: wire durable multi-turn persistence into the dispatcher, fail-closed in production.
    # Isolated exactly like the construction above — a failure here must not crash bring-up.
    try:
        await _provision_dispatch_checkpointer(state)
    except Exception as exc:  # noqa: BLE001 — isolated; never propagate (liveness stays up).
        state.dispatcher = None
        state.dispatcher_error = f"checkpointer provisioning error: {type(exc).__name__}: {exc}"
        logger.error("webhook_dispatch_checkpointer_provision_failed", exc_info=True)


async def run(settings: WhatsAppWebhookSettings) -> None:
    """Run the webhook-receiver until SIGTERM/SIGINT. See module docstring for STEP A..D."""
    logger.info("webhook_receiver_starting", tenant=settings.tenant_id, health_port=settings.health_port)

    state = WebhookState(settings=settings)
    # STEP A: bring up dispatch dependencies — pure construction + the ONE bounded I/O (the T4b
    # durable checkpointer connect+setup, `dep_connect_timeout_s`-bounded; module docstring).
    await _bring_up_dependencies(state)

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

    # T4b: release the checkpointer's connection pool (idempotent; no-op for the in-memory
    # fallback / when never provisioned). Non-fatal — a close failure must not mask shutdown.
    if state.checkpointer is not None:
        with contextlib.suppress(Exception):
            await state.checkpointer.aclose()

    logger.info("webhook_receiver_stopped", tenant=settings.tenant_id)

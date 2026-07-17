"""Gateway health-only daemon (T1.6, defect B1).

`deployment-gateway.yaml` (`gateway.enabled` in `values.yaml`) has always run `command: ["python",
"-m", "maezo.gateway"]`, but `src/maezo/gateway/` was a library package with no `__main__.py` — a
CrashLoopBackOff waiting to happen the moment anyone flipped `gateway.enabled: true` (the chart
kept it `false` for exactly this reason; see the `values.yaml` comment this build's evidence
supersedes).

**Capability honesty (constraint 3 — no fabricated integrations).** `maezo.gateway` is, and
remains, an IN-PROCESS library (`docs/runbooks/gateway.md`'s deployment-model note): the PEP,
pseudonymizer, and audit trail execute *inside* the agent-runtime / worker-daemon pods that call
them, not over the network from a separate gateway service. This module does **not** add an HTTP
PEP-evaluation endpoint, an audit-query API, or any other business surface — `src/maezo/gateway/`
has none today (verified: `pep.py`, `pseudonymizer.py`, `audit.py`, `audit_postgres.py`,
`custody.py`, `credential_vault.py`, `log_scrubber.py` are all plain library modules, zero FastAPI
routes). Per the charter's explicit fallback ("if the gateway library has no HTTP-worthy surface
yet beyond health, ship health+readiness and document that business endpoints arrive with their
features"), this build ships **exactly** `/healthz`, `/readyz`, `/metrics` — nothing more. Business
endpoints, if the gateway ever gets a standalone HTTP surface, arrive with that feature, in a
follow-up.

Same health-first, bounded/non-fatal pattern as `worker_runtime`/`agent_runtime` (T1.1/T1.6),
reduced to what a health-only process needs:

  STEP A  Bind the health app IMMEDIATELY — `/healthz` 200 before any dependency is touched.
  STEP B  ONE bounded, non-fatal check: autonomy policies loadable
          (`maezo.gateway.pep.build_pep()` — the fail-closed factory, ADR-0025 D5). Failure logs
          + leaves `/readyz` unhealthy; liveness stays up (no CrashLoop over a bad policy file —
          an operator can `kubectl exec` in and inspect instead of an infinite restart loop).
  STEP C  `/readyz` reflects that ONE check (fail-closed: unhealthy until policies genuinely load).
  STEP D  SIGTERM/SIGINT -> drain: `live=False`, stop the health server. Nothing else in-flight.
"""

from __future__ import annotations

import asyncio
import contextlib
import signal
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

import structlog

from maezo.gateway.pep import PEP, PolicyError, build_pep
from maezo.platform.health import CheckResult, build_health_server, create_health_app
from maezo.platform.observability import get_metrics_collector

from .settings import GatewaySettings

logger = structlog.get_logger(__name__)


@dataclass
class GatewayState:
    settings: GatewaySettings
    live: bool = True
    pep: PEP | None = None
    pep_error: str | None = None

    def is_live(self) -> bool:
        return self.live


def build_readiness_checks(state: GatewayState) -> list[Callable[[], Awaitable[CheckResult]]]:
    async def policies_loadable(_state: GatewayState = state) -> CheckResult:
        if _state.pep is not None:
            return CheckResult(
                name="policies_loadable", healthy=True, detail=f"tenant={_state.pep.matrix.tenant}"
            )
        return CheckResult(
            name="policies_loadable", healthy=False, detail=_state.pep_error or "PEP not constructed"
        )

    return [policies_loadable]


async def _bring_up_dependencies(state: GatewayState) -> None:
    try:
        state.pep = build_pep(tenant=state.settings.tenant_id)
    except PolicyError as exc:
        state.pep_error = str(exc)
        logger.error("gateway_pep_build_failed", tenant=state.settings.tenant_id, exc_info=True)
    except Exception as exc:  # noqa: BLE001 — isolated: liveness must stay up (design pattern, T1.1/T1.6).
        state.pep_error = f"{type(exc).__name__}: {exc}"
        logger.error("gateway_pep_build_failed", tenant=state.settings.tenant_id, exc_info=True)

    logger.info("gateway_dependencies_brought_up", policies_loadable=state.pep is not None)
    logger.info(
        "gateway_business_endpoints_pending",
        note="maezo.gateway has no HTTP business surface today — PEP/pseudonymizer/audit run "
        "in-process inside agent-runtime/worker-daemon pods (docs/runbooks/gateway.md). This "
        "process serves health/readiness only; business endpoints arrive with their features.",
    )


async def run(settings: GatewaySettings) -> None:
    """Run the gateway health-only daemon until SIGTERM/SIGINT. See module docstring STEP A..D."""
    logger.info("gateway_starting", tenant=settings.tenant_id, health_port=settings.health_port)
    state = GatewayState(settings=settings)
    shutdown = asyncio.Event()
    loop = asyncio.get_running_loop()

    app = create_health_app(
        readiness_checks=build_readiness_checks(state),
        is_live=state.is_live,
        registry=get_metrics_collector().registry,
    )
    server = build_health_server(app, port=settings.health_port)
    server.capture_signals = contextlib.nullcontext  # type: ignore[assignment]  # we own the signals
    serve_task = asyncio.create_task(server.serve(), name="health-server")

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

    if not shutdown.is_set():
        await _bring_up_dependencies(state)

    logger.info("shutdown_before_run" if shutdown.is_set() else "gateway_running")
    await shutdown.wait()

    server.should_exit = True
    with contextlib.suppress(asyncio.CancelledError, SystemExit):
        await serve_task

    logger.info("gateway_stopped", tenant=settings.tenant_id)

"""AF-13: `setup_observability` has production callers, and its no-op state is stated out loud.

THE GAP. `maezo.platform.observability.setup_observability` builds a structlog processor chain, an
OTel `TracerProvider`, a `BatchSpanProcessor` and an OTLP exporter, and it had ZERO production
callers — the module said so itself, in `_build_key_scrubber`'s docstring. Every daemon therefore
ran structlog's DEFAULT configuration and built no tracer provider at all, and the DL-0043 log
scrubber (which is installed by exactly that function) could never take effect no matter what the
policy said.

THE SECOND HALF, which is why "just call it" was not enough. The module documents a no-op exporter
when no OTLP endpoint is configured. That is a legitimate dev posture, but from outside a pod it
was INDISTINGUISHABLE from a working trace pipeline. So the wiring also had to make the difference
visible: `ObservabilityStatus.traces_exported`, the `observability_configured` readiness check, and
a WARNING-level log line naming the no-export state.

WHAT IS NOT CLAIMED HERE: that traces reach a collector. Nothing in a unit test can show that, and
these tests deliberately assert only what is checkable in-process — that the bootstrap ran, what it
reports, and that a failure is contained rather than fatal.
"""

from __future__ import annotations

from typing import Any

import pytest
import structlog

from maezo.platform.observability import ObservabilityStatus, bootstrap_observability

# =================================================================================================
# The bootstrap itself
# =================================================================================================


def test_bootstrap_reports_the_no_op_exporter_explicitly() -> None:
    """`configured=True, traces_exported=False` — healthy, and the detail SAYS no span leaves."""
    status = bootstrap_observability(service_name="maezo-test", otlp_endpoint=None)

    assert status.configured is True
    assert status.traces_exported is False
    assert status.otlp_endpoint is None
    assert "NOT EXPORTED" in status.detail
    assert "OTEL_EXPORTER_OTLP_ENDPOINT" in status.detail


def test_bootstrap_reports_a_configured_exporter_with_its_endpoint() -> None:
    status = bootstrap_observability(
        service_name="maezo-test", otlp_endpoint="otel-collector.observability:4317"
    )

    assert status.configured is True
    assert status.traces_exported is True
    assert status.otlp_endpoint == "otel-collector.observability:4317"
    assert "otlp:otel-collector.observability:4317" in status.detail


def test_bootstrap_contains_a_failure_instead_of_crashlooping_the_pod(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A raise (e.g. ADR-0035's fail-closed missing `PHI_HMAC_KEY` under a ratified key policy)
    must leave the daemon NOT READY, never dead. Liveness staying up is what lets an operator
    `kubectl exec` in and read the reason instead of watching a restart loop."""

    def _explode(**_kwargs: Any) -> None:
        raise RuntimeError("PHI_HMAC_KEY ausente")

    monkeypatch.setattr("maezo.platform.observability.setup_observability", _explode)
    status = bootstrap_observability(service_name="maezo-test", otlp_endpoint=None)

    assert status.configured is False
    assert status.traces_exported is False
    assert status.error is not None
    assert "PHI_HMAC_KEY ausente" in status.error
    assert "FAILED" in status.detail


def test_bootstrap_actually_configures_structlog() -> None:
    """Non-vacuity: the wire must really replace the process's logging configuration, otherwise
    the DL-0043 scrubber seam this function installs stays as inert as it was before AF-13."""
    structlog.reset_defaults()
    default_processors = list(structlog.get_config()["processors"])

    bootstrap_observability(service_name="maezo-test", otlp_endpoint=None)

    assert list(structlog.get_config()["processors"]) != default_processors


# =================================================================================================
# The three composition roots
# =================================================================================================


@pytest.mark.asyncio
async def test_gateway_bring_up_configures_observability_and_reports_it() -> None:
    from maezo.gateway.service import GatewayState, _bring_up_dependencies, build_readiness_checks
    from maezo.gateway.settings import GatewaySettings

    state = GatewayState(settings=GatewaySettings(tenant_id="amh"))
    assert state.observability is None
    await _bring_up_dependencies(state)

    assert isinstance(state.observability, ObservabilityStatus)
    assert state.observability.configured is True
    results = {check.__name__: await check() for check in build_readiness_checks(state)}
    assert results["observability_configured"].healthy is True


@pytest.mark.asyncio
async def test_worker_runtime_bring_up_configures_observability() -> None:
    from maezo.runtime.worker_runtime.service import WorkerState, _ensure_observability
    from maezo.runtime.worker_runtime.settings import WorkerRuntimeSettings

    state = WorkerState(settings=WorkerRuntimeSettings(tenant_id="amh"))
    assert state.observability is None
    _ensure_observability(state)

    assert isinstance(state.observability, ObservabilityStatus)
    assert state.observability.configured is True


@pytest.mark.asyncio
async def test_agent_runtime_bring_up_configures_observability() -> None:
    from maezo.runtime.agent_runtime.service import AgentState, _ensure_observability
    from maezo.runtime.agent_runtime.settings import AgentRuntimeSettings

    state = AgentState(settings=AgentRuntimeSettings(agent_id="helena", tenant_id="amh"))
    assert state.observability is None
    _ensure_observability(state)

    assert isinstance(state.observability, ObservabilityStatus)
    assert state.observability.configured is True


def test_the_bootstrap_is_not_run_twice_when_run_already_did_it() -> None:
    """OTel refuses to override an already-set `TracerProvider`, so a second bootstrap would log a
    warning that means nothing. `_ensure_observability` is the guard; this pins its idempotence."""
    from maezo.runtime.agent_runtime.service import AgentState, _ensure_observability
    from maezo.runtime.agent_runtime.settings import AgentRuntimeSettings

    state = AgentState(settings=AgentRuntimeSettings(agent_id="helena", tenant_id="amh"))
    first = _ensure_observability(state)
    second = _ensure_observability(state)

    assert first is second


@pytest.mark.asyncio
async def test_an_unconfigured_root_reports_red_rather_than_assuming_healthy() -> None:
    """ "Not evaluated" is not "healthy" — the same posture every other readiness check here takes."""
    from maezo.gateway.service import GatewayState, build_readiness_checks
    from maezo.gateway.settings import GatewaySettings

    state = GatewayState(settings=GatewaySettings(tenant_id="amh"))
    results = {check.__name__: await check() for check in build_readiness_checks(state)}

    assert results["observability_configured"].healthy is False
    assert "has not run" in (results["observability_configured"].detail or "")


@pytest.mark.asyncio
async def test_a_failed_bootstrap_turns_the_readiness_check_red(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from maezo.gateway.service import GatewayState, _bring_up_dependencies, build_readiness_checks
    from maezo.gateway.settings import GatewaySettings

    def _explode(**_kwargs: Any) -> None:
        raise RuntimeError("configuracao invalida")

    monkeypatch.setattr("maezo.platform.observability.setup_observability", _explode)
    state = GatewayState(settings=GatewaySettings(tenant_id="amh"))
    await _bring_up_dependencies(state)

    results = {check.__name__: await check() for check in build_readiness_checks(state)}
    assert results["observability_configured"].healthy is False
    # ...and liveness is untouched: the failure is contained, not fatal.
    assert state.is_live() is True
    # ...and the OTHER bring-up work still happened — one red check, not a dead daemon.
    assert results["policies_loadable"].healthy is True


def test_every_composition_root_declares_the_observability_readiness_check() -> None:
    """A fence, not a triplicated assertion: a fourth root added without the check would be a root
    whose trace/log configuration is once again unobservable from outside the pod."""
    import asyncio

    from maezo.gateway.service import GatewayState
    from maezo.gateway.service import build_readiness_checks as gateway_checks
    from maezo.gateway.settings import GatewaySettings
    from maezo.runtime.agent_runtime.service import AgentState
    from maezo.runtime.agent_runtime.service import build_readiness_checks as agent_checks
    from maezo.runtime.agent_runtime.settings import AgentRuntimeSettings
    from maezo.runtime.worker_runtime.service import WorkerState
    from maezo.runtime.worker_runtime.service import build_readiness_checks as worker_checks
    from maezo.runtime.worker_runtime.settings import WorkerRuntimeSettings

    roots = [
        gateway_checks(GatewayState(settings=GatewaySettings())),
        worker_checks(WorkerState(settings=WorkerRuntimeSettings())),
        agent_checks(AgentState(settings=AgentRuntimeSettings())),
    ]
    for checks in roots:
        names = {asyncio.iscoroutinefunction(c) and c.__name__ for c in checks}
        assert "observability_configured" in names, sorted(str(n) for n in names)

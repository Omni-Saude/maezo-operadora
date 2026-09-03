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

import io
import re
import sys
from typing import Any

import pytest
import structlog

from maezo.platform.observability import (
    ObservabilityStatus,
    bootstrap_observability,
    setup_observability,
)

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


# =================================================================================================
# Log FORMAT — the production behaviour change this wiring applies (WP-COMPOSICAO-V2, MAJOR-1)
# =================================================================================================
#
# Wiring the three roots means these daemons stop running structlog's DEFAULT configuration and
# start running THIS module's. The first cut of that change silently dropped `add_log_level` and
# forced ANSI colours regardless of TTY, which breaks two different kinds of operator grep:
# level triage (`docs/runbooks/devops-stack.md:318`, and the SLA-alert runbook on the in-flight
# `docs/slo-runbooks-alertas` branch) and FIELD extraction (escape codes land between key, `=`
# and value, so `topic=t` stops being a substring at all). These tests pin both, in both
# directions, so the format cannot drift back without a red test.


class _FakeTty(io.StringIO):
    """A stream that claims to be a terminal. Colours are correct HERE and wrong in a container."""

    def isatty(self) -> bool:
        return True


def _configure_onto(monkeypatch: pytest.MonkeyPatch, stream: io.StringIO) -> None:
    """Point `PrintLoggerFactory` at `stream` and configure through the real production function."""
    monkeypatch.setattr(sys, "stdout", stream)
    setup_observability(service_name="maezo-test-log-format", otlp_endpoint=None)
    stream.seek(0)
    stream.truncate(0)


def _log_one(stream: io.StringIO, method: str = "error", **fields: Any) -> str:
    getattr(structlog.get_logger("maezo.tests.log_format"), method)("worker_failed", **fields)
    return stream.getvalue()


def test_a_container_log_line_carries_no_ansi_escape_codes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Non-TTY stdout (every deployment of all three daemons) => plain text.

    `structlog.dev.ConsoleRenderer.__init__` defaults to `colors=True`, so a bare
    `ConsoleRenderer()` FORCES escapes even with no terminal. structlog's own default renderer is
    built TTY-aware; this asserts we match the default rather than the constructor.
    """
    stream = io.StringIO()  # StringIO.isatty() is False
    _configure_onto(monkeypatch, stream)

    line = _log_one(stream, topic="operadora.eventos.publish")

    assert "\x1b" not in line, repr(line)


def test_an_error_line_carries_its_severity_so_level_triage_works(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`add_log_level` is what renders the `[error    ]` token runbooks grep for.

    Note the CASE: structlog renders the level lower-case, which was already true of the default
    configuration these daemons ran before the wiring — a runbook line spelling `grep ERROR`
    therefore needs `-i`, and that is a pre-existing property of structlog, not of this change.
    What this test forbids is the level DISAPPEARING, which is what dropping the processor did.
    """
    stream = io.StringIO()
    _configure_onto(monkeypatch, stream)

    line = _log_one(stream, topic="operadora.eventos.publish")

    assert re.search(r"\[error\s*\]", line), repr(line)
    assert re.search(r"(?i)\berror\b", line), repr(line)
    assert structlog.processors.add_log_level in structlog.get_config()["processors"]


def test_a_rendered_line_survives_a_field_grep(monkeypatch: pytest.MonkeyPatch) -> None:
    """`key=value` extraction — the other half of what forced colours break.

    With colours forced ON the rendered bytes are `\\x1b[36mtopic\\x1b[0m=\\x1b[35m<value>\\x1b[0m`,
    so `grep 'topic=...'` matches nothing even though the field is present. This is the assertion
    that would have caught that, and it is deliberately written the way an on-call greps.
    """
    stream = io.StringIO()
    _configure_onto(monkeypatch, stream)

    line = _log_one(stream, topic="operadora.eventos.publish", tenant="amh")

    assert "topic=operadora.eventos.publish" in line, repr(line)
    match = re.search(r"\btenant=(\S+)", line)
    assert match is not None, repr(line)
    assert match.group(1) == "amh"


def test_a_real_terminal_still_gets_colours(monkeypatch: pytest.MonkeyPatch) -> None:
    """The other direction: TTY-aware means aware, not disabled. A developer keeps their colours."""
    monkeypatch.delenv("NO_COLOR", raising=False)
    stream = _FakeTty()
    _configure_onto(monkeypatch, stream)

    line = _log_one(stream, topic="operadora.eventos.publish")

    assert "\x1b[" in line, repr(line)


def test_no_color_wins_over_a_terminal(monkeypatch: pytest.MonkeyPatch) -> None:
    """https://no-color.org — for the operator who pipes an interactive session into a file."""
    monkeypatch.setenv("NO_COLOR", "1")
    stream = _FakeTty()
    _configure_onto(monkeypatch, stream)

    line = _log_one(stream, topic="operadora.eventos.publish")

    assert "\x1b" not in line, repr(line)


def test_the_chain_keeps_what_structlogs_default_chain_provides(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Non-vacuity for the two processors the first cut dropped, asserted by identity.

    `merge_contextvars` is latent today (nothing in `src/` binds contextvars) but dropping it
    would make any future `bind_contextvars` field vanish from production logs with no error.
    """
    _configure_onto(monkeypatch, io.StringIO())

    processors = list(structlog.get_config()["processors"])

    assert structlog.contextvars.merge_contextvars in processors
    assert structlog.processors.add_log_level in processors


def test_an_unset_log_level_filters_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    """The default must stay what it was: no filtering. `MAEZO_LOG_LEVEL` is an opt-in."""
    monkeypatch.delenv("MAEZO_LOG_LEVEL", raising=False)
    stream = io.StringIO()
    _configure_onto(monkeypatch, stream)

    assert _log_one(stream, "debug", topic="t") != ""


def test_the_declared_log_level_is_actually_applied(monkeypatch: pytest.MonkeyPatch) -> None:
    """`MAEZO_LOG_LEVEL` was read, emitted as a FIELD, and never applied (review minor-7).

    `structlog.stdlib.BoundLogger` over `PrintLoggerFactory` performs no filtering whatsoever, so
    the variable described behaviour the process did not have — and the wiring is what put that
    docstring on the production path.
    """
    monkeypatch.setenv("MAEZO_LOG_LEVEL", "ERROR")
    stream = io.StringIO()
    _configure_onto(monkeypatch, stream)

    assert _log_one(stream, "info", topic="t") == ""
    assert _log_one(stream, "error", topic="t") != ""


def test_an_unknown_log_level_is_refused_rather_than_silently_ignored(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fail-closed on the knob itself. A typo must not resolve to "whatever the default was"."""
    monkeypatch.setenv("MAEZO_LOG_LEVEL", "VERBOSO")

    with pytest.raises(ValueError, match="MAEZO_LOG_LEVEL"):
        setup_observability(service_name="maezo-test-log-format", otlp_endpoint=None)


def test_a_bad_log_level_leaves_the_root_red_instead_of_crashlooping(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """And the raise is contained by the composition-root wrapper, like every other bring-up fault."""
    monkeypatch.setenv("MAEZO_LOG_LEVEL", "VERBOSO")

    status = bootstrap_observability(service_name="maezo-test-log-format", otlp_endpoint=None)

    assert status.configured is False
    assert "ValueError" in (status.error or "")

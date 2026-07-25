"""Unit tests for `maezo.runtime.agent_runtime.service` (T1.6, design §10/Q-6).

No engine, no network — exercises the three bounded/non-fatal STEP B checks (against the REAL
`spec/` tree already checked into this repo, ADR-0011 spirit: no mock where a real, cheap,
deterministic local dependency is available) and the readiness checks that read `AgentState`.
"""

from __future__ import annotations

import pytest

from maezo.gateway.pep import PolicyError
from maezo.runtime.agent_runtime.service import (
    AgentState,
    _bring_up_dependencies,
    build_readiness_checks,
)
from maezo.runtime.agent_runtime.settings import AgentRuntimeSettings

# T-C2: agents that start a BPMN process REQUIRE a durable ADR-0007 audit sink, which
# `_build_tool_deps` constructs from `DATABASE_URL`. A dummy DSN suffices here — `PostgresAuditSink`
# construction is pure (asyncpg pool is lazy), so no connection is opened during the bring-up
# construction check.
_DSN = "postgresql+asyncpg://user:pw@localhost:5432/maezo"


def _state(**overrides: object) -> AgentState:
    settings = overrides.pop("settings", None) or AgentRuntimeSettings()
    return AgentState(settings=settings, **overrides)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# _bring_up_dependencies — against the real spec/ tree (helena is a real agent)
# ---------------------------------------------------------------------------


async def test_bring_up_loads_real_agent_definition_and_policies() -> None:
    state = _state(settings=AgentRuntimeSettings(agent_id="helena", tenant_id="amh", database_url=_DSN))
    await _bring_up_dependencies(state)

    assert state.agent_definition is not None
    assert state.agent_definition.id == "helena"
    assert state.agent_definition_error is None

    assert state.pep is not None
    assert state.pep.matrix.tenant == "amh"
    assert state.pep_error is None

    assert state.inference_provider is not None
    assert state.inference_provider.provider_name == "noop"  # Q-6: noop is an acceptable outcome
    assert state.inference_error is None

    # T1.11/defect B6: Helena's REAL graph builds + compiles (construction only, no node runs —
    # the injected CIB Seven/WhatsApp transports never make a network call at this point).
    assert state.agent_graph is not None
    assert state.agent_graph_error is None


async def test_bring_up_loads_real_rafael_graph() -> None:
    """Rafael's `build(config)` additionally needs an `fhir` dependency (optional) — bring-up
    supplies one via `FhirServerReader`, still without any network call."""
    state = _state(settings=AgentRuntimeSettings(agent_id="rafael", tenant_id="amh", database_url=_DSN))
    await _bring_up_dependencies(state)

    assert state.agent_graph is not None
    assert state.agent_graph_error is None


async def test_bring_up_loads_real_lucas_graph() -> None:
    """Lucas's `build(config)` additionally needs a `whatsapp` dependency (required, unlike
    Rafael's optional `fhir`) — bring-up supplies one via Lucas's own `WhatsAppServerSender`
    (`agents/lucas/adapters.py`), still without any network call (T1.12)."""
    state = _state(settings=AgentRuntimeSettings(agent_id="lucas", tenant_id="amh", database_url=_DSN))
    await _bring_up_dependencies(state)

    assert state.agent_graph is not None
    assert state.agent_graph_error is None


async def test_bring_up_loads_real_carolina_graph() -> None:
    """T1.12: Carolina's `build(config)` additionally needs an `fhir` dependency (optional,
    mirrors Rafael) — bring-up supplies one via the same `FhirServerReader`, still without any
    network call (construction only)."""
    state = _state(settings=AgentRuntimeSettings(agent_id="carolina", tenant_id="amh", database_url=_DSN))
    await _bring_up_dependencies(state)

    assert state.agent_graph is not None
    assert state.agent_graph_error is None


async def test_bring_up_loads_real_valentina_graph() -> None:
    """T1.12: Valentina's `build(config)` additionally takes an optional `fhir` dependency —
    bring-up supplies one via her OWN `agents/valentina/adapters.FhirServerReader`
    (`read_patient_summary` Protocol shape), still without any network call (construction
    only). Her consent chokepoint is a graph-execution concern — never exercised here."""
    state = _state(settings=AgentRuntimeSettings(agent_id="valentina", tenant_id="amh", database_url=_DSN))
    await _bring_up_dependencies(state)

    assert state.agent_graph is not None
    assert state.agent_graph_error is None


async def test_bring_up_loads_real_andre_graph() -> None:
    """T1.12: Andre's `build(config)` additionally accepts optional `fhir`/`population` seams —
    bring-up supplies `fhir` via the same `FhirServerReader` as Rafael (still without any network
    call; construction only). `population` (PORT-PENDING WB.4) is deliberately absent and the
    build must still succeed (labeled boundary in `agents/andre/graph.py`)."""
    state = _state(settings=AgentRuntimeSettings(agent_id="andre", tenant_id="amh", database_url=_DSN))
    await _bring_up_dependencies(state)

    assert state.agent_graph is not None
    assert state.agent_graph_error is None


async def test_bring_up_process_starting_agent_fail_closed_without_database_url() -> None:
    """T-C2 fail-closed: without `DATABASE_URL` a real agent that starts a process cannot construct
    its required durable audit sink, so `build(config)` fails-closed and `graph_loaded` reports
    unhealthy with the audit_sink reason — an agent that cannot durably audit its process starts
    must not advertise a loadable graph (never a silent fallback)."""
    state = _state(settings=AgentRuntimeSettings(agent_id="rafael", tenant_id="amh"))  # no database_url
    await _bring_up_dependencies(state)

    assert state.agent_graph is None
    assert state.agent_graph_error is not None
    assert "audit_sink" in state.agent_graph_error


async def test_bring_up_still_stubbed_agent_graph_still_loads() -> None:
    """A still-stubbed agent's no-arg `build()` also goes through `graph_loaded` for real —
    T1.11/T1.12 only change helena/rafael/fernando/carolina/andre's graphs, but the readiness
    check itself is generic."""
    state = _state(settings=AgentRuntimeSettings(agent_id="beatriz", tenant_id="amh", database_url=_DSN))
    await _bring_up_dependencies(state)

    assert state.agent_graph is not None
    assert state.agent_graph_error is None


async def test_bring_up_unknown_agent_id_leaves_definition_unhealthy() -> None:
    state = _state(settings=AgentRuntimeSettings(agent_id="not-a-real-agent"))
    await _bring_up_dependencies(state)

    assert state.agent_definition is None
    assert state.agent_definition_error is not None
    # The other checks are independent — they still succeed/fail on their own terms.
    assert state.pep is not None
    assert state.inference_provider is not None
    # Fail-closed (T1.11): an unknown agent id never falls back to a trivial default graph.
    assert state.agent_graph is None
    assert state.agent_graph_error is not None


async def test_bring_up_agent_definition_path_override(tmp_path: object) -> None:
    import shutil
    from pathlib import Path

    src = Path("spec/agents/helena/agent.yaml")
    dest = Path(str(tmp_path)) / "effective-agent-definition.yaml"
    shutil.copy(src, dest)

    state = _state(settings=AgentRuntimeSettings(agent_id="helena", agent_definition_path=str(dest)))
    await _bring_up_dependencies(state)

    assert state.agent_definition is not None
    assert state.agent_definition.id == "helena"


async def test_bring_up_unknown_tenant_overlay_mismatch_leaves_pep_unhealthy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`build_pep` raises `PolicyError` when it cannot construct a valid matrix — the bring-up
    isolates that into `pep_error`, never propagating (liveness must stay up, Q-6)."""

    def _raise(*, tenant: str) -> None:
        raise PolicyError("boom")

    monkeypatch.setattr("maezo.runtime.agent_runtime.service.build_pep", _raise)
    state = _state()
    await _bring_up_dependencies(state)

    assert state.pep is None
    assert state.pep_error == "boom"


# ---------------------------------------------------------------------------
# Readiness checks (fail-closed, design §12)
# ---------------------------------------------------------------------------


async def test_agent_definition_loaded_unhealthy_when_absent() -> None:
    state = _state()
    checks = {c.__name__: c for c in build_readiness_checks(state)}
    result = await checks["agent_definition_loaded"]()
    assert result.healthy is False


async def test_policies_loadable_unhealthy_when_absent() -> None:
    state = _state()
    checks = {c.__name__: c for c in build_readiness_checks(state)}
    result = await checks["policies_loadable"]()
    assert result.healthy is False


async def test_inference_provider_ready_unhealthy_when_absent() -> None:
    state = _state()
    checks = {c.__name__: c for c in build_readiness_checks(state)}
    result = await checks["inference_provider_ready"]()
    assert result.healthy is False


async def test_graph_loaded_unhealthy_when_absent() -> None:
    state = _state()
    checks = {c.__name__: c for c in build_readiness_checks(state)}
    result = await checks["graph_loaded"]()
    assert result.healthy is False


async def test_graph_loaded_healthy_after_real_bring_up() -> None:
    state = _state(settings=AgentRuntimeSettings(agent_id="helena", tenant_id="amh", database_url=_DSN))
    await _bring_up_dependencies(state)
    checks = {c.__name__: c for c in build_readiness_checks(state)}
    result = await checks["graph_loaded"]()
    assert result.healthy is True
    assert result.detail == "agent_id=helena"


async def test_a2a_dispatcher_ready_not_applicable_for_unrelated_agent() -> None:
    """T2.4 A2A W3: the ONE Helena->Rafael edge does not force its dependency posture onto
    unrelated agent replicas (design doc §9.2)."""
    state = _state(settings=AgentRuntimeSettings(agent_id="marina", tenant_id="amh"))
    checks = {c.__name__: c for c in build_readiness_checks(state)}
    result = await checks["a2a_dispatcher_ready"]()
    assert result.healthy is True
    assert result.detail == "not applicable to agent_id='marina'"


async def test_a2a_dispatcher_ready_unhealthy_when_absent() -> None:
    state = _state(settings=AgentRuntimeSettings(agent_id="rafael", tenant_id="amh"))
    checks = {c.__name__: c for c in build_readiness_checks(state)}
    result = await checks["a2a_dispatcher_ready"]()
    assert result.healthy is False


async def test_a2a_dispatcher_ready_healthy_after_real_bring_up() -> None:
    state = _state(settings=AgentRuntimeSettings(agent_id="rafael", tenant_id="amh", database_url=_DSN))
    await _bring_up_dependencies(state)
    checks = {c.__name__: c for c in build_readiness_checks(state)}
    result = await checks["a2a_dispatcher_ready"]()
    assert result.healthy is True, state.a2a_dispatcher_error


async def test_a2a_dispatcher_ready_unhealthy_without_database_url() -> None:
    """No DATABASE_URL -> `_build_tool_deps` never constructs `audit_sink` -> the composition
    fails closed (mirrors `graph_loaded`'s own DATABASE_URL-gated behavior)."""
    state = _state(settings=AgentRuntimeSettings(agent_id="rafael", tenant_id="amh"))
    await _bring_up_dependencies(state)
    checks = {c.__name__: c for c in build_readiness_checks(state)}
    result = await checks["a2a_dispatcher_ready"]()
    assert result.healthy is False
    assert "audit_sink" in (state.a2a_dispatcher_error or "")


async def test_all_readiness_checks_healthy_after_real_bring_up() -> None:
    state = _state(settings=AgentRuntimeSettings(agent_id="helena", tenant_id="amh", database_url=_DSN))
    await _bring_up_dependencies(state)

    checks = build_readiness_checks(state)
    results = [await c() for c in checks]
    assert all(r.healthy for r in results), results


def test_agent_state_is_live_helper() -> None:
    state = _state()
    assert state.is_live() is True
    state.live = False
    assert state.is_live() is False

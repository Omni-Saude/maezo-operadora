"""Unit tests for `maezo.gateway.service`/`.settings` — the health-only daemon (T1.6, B1).

No network — exercises the single bounded/non-fatal readiness check (`build_pep()`) against the
real `spec/` tree, mirroring `tests/unit/runtime/agent_runtime/test_service.py`.
"""

from __future__ import annotations

import pytest

from maezo.gateway.pep import PolicyError
from maezo.gateway.service import GatewayState, _bring_up_dependencies, build_readiness_checks
from maezo.gateway.settings import GatewaySettings


def test_settings_defaults() -> None:
    settings = GatewaySettings()
    assert settings.tenant_id == "amh"
    assert settings.health_port == 8000


def test_settings_construct_by_env_alias() -> None:
    settings = GatewaySettings(TENANT_ID="omni", GATEWAY_PORT=9000)
    assert settings.tenant_id == "omni"
    assert settings.health_port == 9000


async def test_bring_up_loads_real_policies() -> None:
    state = GatewayState(settings=GatewaySettings(tenant_id="amh"))
    await _bring_up_dependencies(state)

    assert state.pep is not None
    assert state.pep.matrix.tenant == "amh"
    assert state.pep_error is None

    checks = build_readiness_checks(state)
    results = [await c() for c in checks]
    assert all(r.healthy for r in results), results


async def test_readiness_unhealthy_before_bring_up() -> None:
    state = GatewayState(settings=GatewaySettings())
    checks = {c.__name__: c for c in build_readiness_checks(state)}
    result = await checks["policies_loadable"]()
    assert result.healthy is False


async def test_bring_up_isolates_policy_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def _raise(*, tenant: str) -> None:
        raise PolicyError("boom")

    monkeypatch.setattr("maezo.gateway.service.build_pep", _raise)
    state = GatewayState(settings=GatewaySettings())
    await _bring_up_dependencies(state)

    assert state.pep is None
    assert state.pep_error == "boom"


def test_gateway_state_is_live_helper() -> None:
    state = GatewayState(settings=GatewaySettings())
    assert state.is_live() is True
    state.live = False
    assert state.is_live() is False

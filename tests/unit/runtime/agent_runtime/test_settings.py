"""Unit tests for `maezo.runtime.agent_runtime.settings.AgentRuntimeSettings` (T1.6).

ADR-0039 Q7 (owner-decided): `agent_runtime_mode`'s default is FAIL-CLOSED. It used to default to
`"local"`, so an absent `AGENT_RUNTIME_MODE` in a genuinely-production pod silently qualified as
non-production. An absent governance key must inherit the RESTRICTIVE baseline; permissive is an
explicit opt-in. The tests below pin the default itself AND its downstream consequence — a field
value nobody acts on would be a vacuous pin.
"""

from __future__ import annotations

import pytest

from maezo.runtime.agent_runtime.settings import AgentRuntimeSettings

_MODE_ENV = "AGENT_RUNTIME_MODE"


@pytest.fixture(autouse=True)
def _no_ambient_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    """These tests are ABOUT the absent-variable default, so an ambient `AGENT_RUNTIME_MODE` (or a
    developer's `.env`, which `model_config` reads) must never decide the answer."""
    monkeypatch.delenv(_MODE_ENV, raising=False)


def test_defaults() -> None:
    settings = AgentRuntimeSettings()
    assert settings.tenant_id == "amh"
    assert settings.agent_id == "helena"
    assert settings.agent_security_zone == "general"
    # ADR-0039 Q7: fail-CLOSED. An absent AGENT_RUNTIME_MODE is production, never "local".
    assert settings.agent_runtime_mode == "production"
    assert settings.agent_definition_path is None
    assert settings.kafka_bootstrap_servers == "localhost:9092"
    assert settings.cibseven_base_url == "http://cibseven:8080/engine-rest"
    assert settings.fhir_base_url == "http://hapi-fhir:8080/fhir"
    assert settings.health_port == 8000


def test_construct_by_env_alias() -> None:
    settings = AgentRuntimeSettings(
        TENANT_ID="omni",
        AGENT_ID="rafael",
        AGENT_SECURITY_ZONE="phi",
        AGENT_RUNTIME_MODE="kubernetes",
    )
    assert settings.tenant_id == "omni"
    assert settings.agent_id == "rafael"
    assert settings.agent_security_zone == "phi"
    assert settings.agent_runtime_mode == "kubernetes"


def test_construct_by_field_name_populate_by_name() -> None:
    settings = AgentRuntimeSettings(tenant_id="omni", agent_id="marina")
    assert settings.tenant_id == "omni"
    assert settings.agent_id == "marina"


def test_agent_definition_path_from_env() -> None:
    settings = AgentRuntimeSettings(AGENT_DEFINITION_PATH="/etc/maezo/agent/effective-agent-definition.yaml")
    assert settings.agent_definition_path == "/etc/maezo/agent/effective-agent-definition.yaml"


# ---------------------------------------------------------------------------
# ADR-0039 Q7 — the fail-closed default, pinned by its CONSEQUENCE
# ---------------------------------------------------------------------------


def test_an_absent_mode_variable_resolves_to_production_not_local() -> None:
    """(a) The default itself. Stated as BOTH halves — it IS production and it is NOT "local" —
    because "local" is the exact literal every fail-closed gate keys on, and a future typo'd default
    ("Production", "prod") would still be fail-closed but must not silently become the dev branch."""
    mode = AgentRuntimeSettings().agent_runtime_mode
    assert mode == "production"
    assert mode != "local"


def test_the_absent_default_is_production_to_the_shared_discriminator() -> None:
    """The default is only meaningful through the ONE discriminator every gate consults. Pinning the
    string without pinning what `is_production_runtime_mode` makes of it would leave the two free to
    drift (e.g. a default of "" would also read as production, but a default of "local" would not)."""
    from maezo.runtime.agent_runtime.a2a_composition import is_production_runtime_mode

    assert is_production_runtime_mode(AgentRuntimeSettings().agent_runtime_mode) is True


def test_the_two_runtime_mode_discriminators_now_agree_on_an_absent_variable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The asymmetry ADR-0039 Q7 repaired. `a2a_composition.is_production_runtime_mode`'s docstring
    used to DISCLOSE that an absent variable meant production on the worker path and local on the
    agent path. Both are now production. This is the regression guard on the repair itself."""
    from maezo.runtime.agent_runtime.a2a_composition import (
        WORKER_RUNTIME_MODE_ENV_VAR,
        is_production_runtime_mode,
        worker_runtime_mode_from_env,
    )

    monkeypatch.delenv(WORKER_RUNTIME_MODE_ENV_VAR, raising=False)
    assert is_production_runtime_mode(worker_runtime_mode_from_env()) is True
    assert is_production_runtime_mode(AgentRuntimeSettings().agent_runtime_mode) is True


def test_consequence_the_absent_default_makes_the_durability_gates_refuse() -> None:
    """(b) The OBSERVABLE downstream refusal — the reason this default matters at all.

    Feeding the absent-variable default into the two durability gates with no DSN now REFUSES. Under
    the old `"local"` default the identical call BUILT: a no-op fact sink that discarded every
    delegation fact, and `None` idempotency (the dispatcher's single-process, non-restart-durable
    `_inflight` set). This is the pre-flip posture an unset variable used to buy in a real pod."""
    from maezo.runtime.agent_runtime.a2a_composition import (
        _require_fact_producer_or_fail_closed,
        _require_idempotency_store_or_fail_closed,
    )

    mode = AgentRuntimeSettings().agent_runtime_mode
    with pytest.raises(RuntimeError, match="no DATABASE_URL is present"):
        _require_fact_producer_or_fail_closed(
            runtime_mode=mode, tenant="amh", edge="absent-mode", database_url=None
        )
    with pytest.raises(RuntimeError, match="durable, cross-replica idempotency"):
        _require_idempotency_store_or_fail_closed(
            runtime_mode=mode, tenant="amh", edge="absent-mode", database_url=None
        )


def test_consequence_the_absent_default_refuses_unsigned_cards_even_with_the_dev_optout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The sharpest consequence: the signer gate's dev opt-out is IGNORED in production mode. So
    with `AGENT_RUNTIME_MODE` absent, `MAEZO_A2A_ALLOW_UNSIGNED_CARDS=1` no longer buys an unsigned
    edge — it did before the flip. An operator who forgets the variable can no longer be talked into
    an unsigned-Card composition by an env var."""
    from maezo.runtime.agent_runtime.a2a_composition import (
        ALLOW_UNSIGNED_CARDS_ENV_VAR,
        _require_signer_or_fail_closed,
    )

    monkeypatch.delenv("MAEZO_A2A_CARD_SIGNING_KEY", raising=False)
    monkeypatch.setenv(ALLOW_UNSIGNED_CARDS_ENV_VAR, "1")
    with pytest.raises(RuntimeError, match="no Agent Card signing key"):
        _require_signer_or_fail_closed(
            runtime_mode=AgentRuntimeSettings().agent_runtime_mode, tenant="amh", edge="absent-mode"
        )


def test_non_vacuity_an_explicit_local_still_reaches_every_dev_branch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The flip must not have made local dev unreachable — it made it EXPLICIT. Setting the variable
    to "local" (as `docker-compose.yml:173` and `.env.example` both do) still resolves to the dev
    branch on all three gates. Without this, the tests above would pass on a build that had simply
    broken dev, and the "explicit opt-in" claim would be unproven."""
    from maezo.runtime.agent_runtime.a2a_composition import (
        ALLOW_UNSIGNED_CARDS_ENV_VAR,
        _NoopKafkaProducer,
        _require_fact_producer_or_fail_closed,
        _require_idempotency_store_or_fail_closed,
        _require_signer_or_fail_closed,
        is_production_runtime_mode,
    )

    monkeypatch.setenv(_MODE_ENV, "local")
    monkeypatch.delenv("MAEZO_A2A_CARD_SIGNING_KEY", raising=False)
    monkeypatch.setenv(ALLOW_UNSIGNED_CARDS_ENV_VAR, "1")

    mode = AgentRuntimeSettings().agent_runtime_mode
    assert mode == "local"
    assert is_production_runtime_mode(mode) is False
    assert isinstance(
        _require_fact_producer_or_fail_closed(runtime_mode=mode, tenant="amh", edge="dev", database_url=None),
        _NoopKafkaProducer,
    )
    assert (
        _require_idempotency_store_or_fail_closed(
            runtime_mode=mode, tenant="amh", edge="dev", database_url=None
        )
        is None
    )
    assert _require_signer_or_fail_closed(runtime_mode=mode, tenant="amh", edge="dev") is None


def test_the_env_variable_still_overrides_the_default_in_both_directions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The default is a FALLBACK, not a hardcode: the env var must still win. Pinned in both
    directions so a future `Field(default=...)` change cannot quietly become a constant."""
    monkeypatch.setenv(_MODE_ENV, "kubernetes")
    assert AgentRuntimeSettings().agent_runtime_mode == "kubernetes"
    monkeypatch.setenv(_MODE_ENV, "local")
    assert AgentRuntimeSettings().agent_runtime_mode == "local"

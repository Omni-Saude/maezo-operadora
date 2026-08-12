"""Onda 3 / Train C leg 2 — the A2A composition roots must FAIL-CLOSED on the FACT sink.

The defect: both roots built `FactProducer(kafka_producer or _NoopKafkaProducer())`, and
`_NoopKafkaProducer.send` is `return None`. In production that silently DISCARDED every
`agents.events.delegation.*` fact, forever, with nothing anywhere recording that it had happened.
It is the same failure shape `_require_signer_or_fail_closed` was written for one wave earlier — a
missing dependency degrading silently to a permissive default — applied to a different dependency.

The fix (`_require_fact_producer_or_fail_closed`): `DATABASE_URL` present -> the transactional
outbox; absent + production -> RAISE; absent + EXPLICIT local -> the labeled no-op, loudly warned.

DISCRIMINATOR ASYMMETRY — pinned by leg 2, REPAIRED by ADR-0039 Q7 (owner-decided). The two
runtime-mode discriminators used to disagree about an ABSENT variable; they now agree:

    worker-runtime  `RUNTIME_MODE` absent        -> "production"  (fail-closed; Helm injects nothing)
    agent-runtime   `AGENT_RUNTIME_MODE` absent  -> "production"  (was "local" — the Q7 flip)

Both also agree on PRESENT-BUT-EMPTY: it resolves to production. `test_the_empty_string_edge_*` and
`test_the_absent_edge_*` below pin all four corners, so a future "tidy-up" of either default fails
here instead of silently flipping a daemon's posture. The absent-edge test was leg 2's disclosure of
the asymmetry; it is now the regression guard on the repair, with its assertions kept and inverted
rather than deleted — the corner is still pinned, it just pins the opposite (correct) answer.

The parallel fail-closed gate for the IDEMPOTENCY store is leg 3's, not this file's; the shared
`is_production_runtime_mode` helper it will reuse is pinned here.
"""

from __future__ import annotations

from typing import Any

import pytest
from tests.support.audit_fakes import FakeStartAuditSink
from tests.unit.a2a.fakes import RecordingProducer

from maezo.a2a import per_tenant_key_env_var
from maezo.a2a.outbox import PostgresOutboxFactProducer
from maezo.runtime.agent_runtime.a2a_composition import (
    ALLOW_UNSIGNED_CARDS_ENV_VAR,
    WORKER_RUNTIME_MODE_ENV_VAR,
    _NoopKafkaProducer,
    _require_fact_producer_or_fail_closed,
    build_dossier_delegation_dispatcher,
    is_production_runtime_mode,
    worker_runtime_mode_from_env,
)
from maezo.runtime.agent_runtime.settings import AgentRuntimeSettings
from maezo.tools.mcp_cibseven.transport import FakeCibSevenTransport
from maezo.tools.workers.dmn_transport import FakeDmnTransport

# Leg E2 (ADR-0039 §4.4): the composition roots resolve the PER-TENANT signing key now. Every test
# here composes for tenant "amh", so its per-tenant var (`MAEZO_A2A_CARD_SIGNING_KEY__AMH`) is what
# provisions the key past the signer gate.
_SIGNING_KEY_ENV = per_tenant_key_env_var("amh")
_VALID_KEY = "unit-test-card-signing-key-0123456789abcdef"
_DSN = "postgresql://maezo:maezo@localhost:5433/maezo"


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(_SIGNING_KEY_ENV, raising=False)
    monkeypatch.delenv(ALLOW_UNSIGNED_CARDS_ENV_VAR, raising=False)
    monkeypatch.delenv(WORKER_RUNTIME_MODE_ENV_VAR, raising=False)
    monkeypatch.delenv("AGENT_RUNTIME_MODE", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)


class _FakeInference:
    async def generate(self, prompt: str, *, phi: bool = False) -> str:
        return "dossie sintetico"


def _dossier_deps() -> dict[str, Any]:
    dmn = FakeDmnTransport()
    dmn.register("cred_admissibility", [{"roteamento": "SEGUE_ANALISE"}])
    dmn.register("cred_route", [{"roteamento": "ANALISE_DESCREDENCIAMENTO"}])
    dmn.register("cred_sla", [{"sla_analise": "P10D", "sla_alerta": "P7D"}])
    dmn.register("cred_prior_notice", [{"exige_notificacao_previa": True}])
    return {
        "dmn": dmn,
        "cibseven": FakeCibSevenTransport(),
        "audit_sink": FakeStartAuditSink(),
        "inference": _FakeInference(),
    }


# ---------------------------------------------------------------------------
# The gate's decision table
# ---------------------------------------------------------------------------

#: (runtime_mode, database_url) -> expected outcome. Written from the gate's DOCUMENTED contract
#: (see `_require_fact_producer_or_fail_closed`'s docstring), never derived from
#: `_LOCAL_RUNTIME_MODE` or from `is_production_runtime_mode` — the modes are typed as literals so
#: a change to the constant under test cannot silently rewrite this table.
_GATE_CASES: tuple[tuple[str, str | None, str], ...] = (
    ("production", None, "refuse"),
    ("kubernetes", None, "refuse"),
    ("staging", None, "refuse"),
    ("Local", None, "refuse"),  # case-sensitive: only the exact literal "local" is non-production
    ("", None, "refuse"),  # present-but-empty is production, on BOTH discriminators
    (" local ", None, "refuse"),  # untrimmed is not the literal, so it fails closed
    ("production", "   ", "refuse"),  # blank-after-strip DSN == absent (the leg-4 legibility fix)
    ("local", None, "noop"),
    ("local", "   ", "noop"),  # ... and a blank DSN in local dev is 'absent', not an outbox
    ("production", _DSN, "outbox"),
    ("kubernetes", _DSN, "outbox"),
    ("local", _DSN, "outbox"),  # a DSN in local dev still gets the durable path
    ("production", f"  {_DSN}  ", "outbox"),  # merely PADDED is present: presence check, not normalizer
)


@pytest.mark.parametrize(("mode", "dsn", "expected"), _GATE_CASES)
def test_fact_producer_gate_decision_table(mode: str, dsn: str | None, expected: str) -> None:
    if expected == "refuse":
        with pytest.raises(RuntimeError, match="no DATABASE_URL is present"):
            _require_fact_producer_or_fail_closed(
                runtime_mode=mode, tenant="amh", edge="test", database_url=dsn
            )
        return
    producer = _require_fact_producer_or_fail_closed(
        runtime_mode=mode, tenant="amh", edge="test", database_url=dsn
    )
    if expected == "noop":
        assert isinstance(producer, _NoopKafkaProducer)
    else:
        assert isinstance(producer, PostgresOutboxFactProducer)
        assert producer.outbox.tenant == "amh"


def test_the_refusal_names_the_data_loss_it_prevents() -> None:
    """The error must be legible to whoever hits it at 3am — the exact silent loss, and the two
    legitimate ways out. A refusal that just says 'misconfigured' teaches nobody anything."""
    with pytest.raises(RuntimeError) as exc:
        _require_fact_producer_or_fail_closed(
            runtime_mode="kubernetes", tenant="amh", edge="Helena->Rafael", database_url=None
        )
    message = str(exc.value)
    assert "DROPPED at emission" in message
    assert "0008" in message
    assert "Helena->Rafael" in message


def test_the_local_noop_is_still_legal_and_still_drops() -> None:
    """The one surviving no-op branch: explicit local + no database. It must actually work (a dev
    laptop composes the edge) and it must actually be a drop (never a fabricated buffer)."""
    producer = _require_fact_producer_or_fail_closed(
        runtime_mode="local", tenant="amh", edge="test", database_url=None
    )
    assert isinstance(producer, _NoopKafkaProducer)

    import asyncio

    assert asyncio.run(producer.send("t", b"{}", key=b"amh")) is None


# ---------------------------------------------------------------------------
# The shared discriminator + the disclosed asymmetry
# ---------------------------------------------------------------------------

#: mode -> is it production? Literals; the ONLY non-production value is the exact string "local".
_DISCRIMINATOR_CASES: tuple[tuple[str, bool], ...] = (
    ("local", False),
    ("production", True),
    ("kubernetes", True),
    ("", True),
    ("LOCAL", True),
    ("local ", True),
    ("dev", True),
)


@pytest.mark.parametrize(("mode", "expected"), _DISCRIMINATOR_CASES)
def test_shared_discriminator(mode: str, expected: bool) -> None:
    assert is_production_runtime_mode(mode) is expected


def test_the_signer_gate_and_the_fact_gate_share_one_discriminator() -> None:
    """Leg 3 will add a third gate. All of them must answer 'is this production?' identically, so
    the helper is the single source — this pins that the fact gate agrees with the helper across
    the whole table above rather than re-deriving the comparison."""
    for mode, is_prod in _DISCRIMINATOR_CASES:
        if is_prod:
            with pytest.raises(RuntimeError):
                _require_fact_producer_or_fail_closed(
                    runtime_mode=mode, tenant="amh", edge="t", database_url=None
                )
        else:
            assert isinstance(
                _require_fact_producer_or_fail_closed(
                    runtime_mode=mode, tenant="amh", edge="t", database_url=None
                ),
                _NoopKafkaProducer,
            )


def test_the_empty_string_edge_of_the_worker_discriminator(monkeypatch: pytest.MonkeyPatch) -> None:
    """`RUNTIME_MODE=""` -> "production". Blank is not a mode; it fails closed."""
    monkeypatch.setenv(WORKER_RUNTIME_MODE_ENV_VAR, "")
    assert worker_runtime_mode_from_env() == "production"
    assert is_production_runtime_mode(worker_runtime_mode_from_env()) is True


def test_the_absent_edge_of_the_worker_discriminator(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(WORKER_RUNTIME_MODE_ENV_VAR, raising=False)
    assert worker_runtime_mode_from_env() == "production"


def test_the_empty_string_edge_of_the_agent_discriminator(monkeypatch: pytest.MonkeyPatch) -> None:
    """`AGENT_RUNTIME_MODE=""` -> the empty string reaches the gate as-is -> production."""
    monkeypatch.setenv("AGENT_RUNTIME_MODE", "")
    settings = AgentRuntimeSettings()
    assert settings.agent_runtime_mode == ""
    assert is_production_runtime_mode(settings.agent_runtime_mode) is True


def test_the_absent_edge_of_the_agent_discriminator_is_production_and_matches_the_worker_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The corner leg 2 pinned as a DISCLOSED ASYMMETRY, now pinned as the REPAIR (ADR-0039 Q7): an
    ABSENT variable means PRODUCTION on BOTH paths. Same three assertions as leg 2's version, with
    the agent-path verdicts inverted — the corner stays covered, so a revert to the permissive
    default reddens here rather than passing quietly.

    `worker_runtime_mode_from_env()` is re-asserted (not assumed) because the repair's whole claim
    is AGREEMENT between the two paths; proving only the agent half would not show they now match."""
    monkeypatch.delenv("AGENT_RUNTIME_MODE", raising=False)
    assert AgentRuntimeSettings().agent_runtime_mode == "production"
    assert is_production_runtime_mode(AgentRuntimeSettings().agent_runtime_mode) is True
    assert is_production_runtime_mode(worker_runtime_mode_from_env()) is True


# ---------------------------------------------------------------------------
# The gate at the composition-root level (the dossier edge — where it genuinely bites)
# ---------------------------------------------------------------------------


def test_dossier_root_in_production_without_a_dsn_never_returns_a_dispatcher(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A signed, fully-dep'd, otherwise-buildable production edge is now REFUSED when its facts
    would be dropped. `audit_sink` is present and `database_url` is not — a state only this root
    can reach, since the two are independent parameters here."""
    monkeypatch.setenv(_SIGNING_KEY_ENV, _VALID_KEY)
    with pytest.raises(RuntimeError, match="no DATABASE_URL is present"):
        build_dossier_delegation_dispatcher(
            tenant="amh", runtime_mode="production", database_url=None, **_dossier_deps()
        )


def test_dossier_root_in_explicit_local_without_a_dsn_still_composes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Dev ergonomics preserved: explicit local + no database still builds a working edge."""
    monkeypatch.setenv(_SIGNING_KEY_ENV, _VALID_KEY)
    dispatcher = build_dossier_delegation_dispatcher(
        tenant="amh", runtime_mode="local", database_url=None, **_dossier_deps()
    )
    assert dispatcher is not None


def test_an_injected_producer_still_wins_over_the_fact_gate(monkeypatch: pytest.MonkeyPatch) -> None:
    """The `kafka_producer` seam predates the FACT gate and must keep bypassing it — otherwise
    every test and any future real-broker wiring would have to route through a database.

    Leg 3 scope note: an injected producer bypasses the fact gate ONLY. It does NOT satisfy the
    durable-idempotency gate, so `runtime_mode="production"` with no `database_url` is now a REFUSAL
    even WITH a producer injected (proven in test_a2a_composition_idempotency.py). This test
    therefore uses EXPLICIT local mode, where the composition builds with the injected producer and
    in-memory idempotency — the fact gate's own decision table above already pins its full behavior
    in isolation."""
    monkeypatch.setenv(_SIGNING_KEY_ENV, _VALID_KEY)
    dispatcher = build_dossier_delegation_dispatcher(
        tenant="amh",
        runtime_mode="local",
        database_url=None,
        kafka_producer=RecordingProducer(),
        **_dossier_deps(),
    )
    assert dispatcher is not None


def test_a_dsn_makes_the_root_wire_the_outbox_without_connecting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Construction stays pure — the asyncpg pool is LAZY, so wiring the outbox at composition
    time must not require a reachable server (this suite has none)."""
    monkeypatch.setenv(_SIGNING_KEY_ENV, _VALID_KEY)
    dispatcher = build_dossier_delegation_dispatcher(
        tenant="amh", runtime_mode="production", database_url=_DSN, **_dossier_deps()
    )
    assert dispatcher is not None


# ---------------------------------------------------------------------------
# Seam discipline (leg-4 requirement)
# ---------------------------------------------------------------------------


def test_the_dispatcher_public_surface_is_still_only_delegate() -> None:
    """`GatedDelegationDispatcher` gates by SUBCLASS, which is only safe while `delegate` is the
    base's ONLY public method (`gateway/seams/a2a.py`'s own docstring). Facts flow AROUND the
    dispatcher — through the `facts=` constructor argument — so this leg added nothing to that
    surface. Re-asserted here rather than assumed, because 'I did not need to touch it' is exactly
    the claim a reviewer should not have to take on faith."""
    from maezo.a2a.dispatcher import DelegationDispatcher

    public = {name for name in vars(DelegationDispatcher) if not name.startswith("_")}
    assert public == {"delegate"}

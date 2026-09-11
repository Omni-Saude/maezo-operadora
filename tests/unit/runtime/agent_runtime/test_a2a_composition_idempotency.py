"""Onda 3 / Train C leg 3 — durable idempotency is MANDATORY outside dev-local.

A fail-closed REFUSAL, not a silent dev-fallback. Both A2A composition roots used to wire the
durable Guard-4 store truthy-only — `PostgresIdempotencyStore(...) if database_url else None` — so a
NON-local runtime with no DATABASE_URL silently ran with NO durable, cross-replica idempotency: the
ONLY surviving Guard 4 was then the dispatcher's in-memory, single-process `_inflight` set, so every
replica and every restart re-executed the delegation's downstream effects (ADR-0003 Guard 4; and
ADR-0039: durable-idempotency retention DOMINATES signature validity, so the store is not optional
outside dev). `_require_idempotency_store_or_fail_closed` closes it — the THIRD sibling of the signer
and fact gates, sharing their `is_production_runtime_mode` discriminator and fail-closed posture:

    DSN present             -> PostgresIdempotencyStore (the only production posture)
    DSN absent + production  -> RAISE  (un-bypassable; injecting a producer does NOT save you)
    DSN absent + local       -> None   (in-memory _inflight Guard 4; disclosed dev behavior)

COHERENCE — the MAJOR risk this file guards against — is that the idempotency gate and leg-2's fact
gate could reach DIFFERENT verdicts on the same root, half-refusing it. They share the EXACT refuse
condition (`not database_url` AND `is_production_runtime_mode(runtime_mode)`), resolved through the
ONE shared discriminator, so they cannot; `test_the_two_gates_agree_on_every_combination` proves it
across the whole {mode} x {DSN} matrix.

RETENTION WINDOW of the durable rows is DBA/MZO-060 (ADR-0039), deliberately NOT invented here —
consistent with leg 2's outbox-retention deferral. This gate only makes the store MANDATORY; how
long a sealed row is kept is an operator/DBA decision this composition root never fabricates.

DISCLOSURE (leg 3): three leg-2 tests that built a PRODUCTION dossier dispatcher with no DSN (relying
on in-memory idempotency + an injected producer) were moved to EXPLICIT local mode — that composition
is now, by design, an idempotency REFUSAL. The signer/fact-gate contracts those tests also exercise
are untouched; see the notes at each moved site.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

import pytest
from tests.support.audit_fakes import FakeStartAuditSink
from tests.unit.a2a.fakes import RecordingProducer

from maezo.a2a import (
    DelegationEnvelope,
    HandlerOutput,
    PostgresIdempotencyStore,
    per_tenant_key_env_var,
)
from maezo.runtime.agent_runtime import a2a_composition
from maezo.runtime.agent_runtime.a2a_composition import (
    ALLOW_UNSIGNED_CARDS_ENV_VAR,
    WORKER_RUNTIME_MODE_ENV_VAR,
    _require_fact_producer_or_fail_closed,
    _require_idempotency_store_or_fail_closed,
    build_auth_delegation_dispatcher,
    build_dossier_delegation_dispatcher,
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

#: The `make_rafael_handler` return shape (module-level alias so the stub's annotation stays short).
_HandlerFn = Callable[[DelegationEnvelope], Awaitable[HandlerOutput]]


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Never depend on ambient signing-key / opt-out / mode / DSN state."""
    monkeypatch.delenv(_SIGNING_KEY_ENV, raising=False)
    monkeypatch.delenv(ALLOW_UNSIGNED_CARDS_ENV_VAR, raising=False)
    monkeypatch.delenv(WORKER_RUNTIME_MODE_ENV_VAR, raising=False)
    monkeypatch.delenv("AGENT_RUNTIME_MODE", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)


class _FakeInference:
    async def generate(
        self,
        prompt: str,
        *,
        phi: bool = False,
        agent_id: str | None = None,
        tenant_id: str | None = None,
        # CC-12 x integracao lote3 (LOTE3-INTEGRATION-FAKES-TASK-KIND): assinatura acompanha o
        # Protocol real (`runtime/inference::InferenceProvider.generate`), mesma especie do
        # defeito f1bc87f.
        task_kind: str | None = None,
    ) -> str:
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
# The gate's decision table (the disclosed semantics, verbatim)
# ---------------------------------------------------------------------------

#: (runtime_mode, database_url) -> expected outcome. Written from the gate's DOCUMENTED contract
#: (`_require_idempotency_store_or_fail_closed`'s docstring), NEVER derived from `_LOCAL_RUNTIME_MODE`
#: or `is_production_runtime_mode` — the modes are typed as literals so a change to the constant
#: under test cannot silently rewrite this table. It mirrors leg-2's fact-gate table EXACTLY on the
#: refuse/build boundary (that is the whole point — the two gates must agree), differing only in the
#: BUILD payload: a durable `store` (vs the fact outbox) and in-memory `None` (vs the fact no-op).
_GATE_CASES: tuple[tuple[str, str | None, str], ...] = (
    ("production", None, "refuse"),
    ("kubernetes", None, "refuse"),
    ("staging", None, "refuse"),
    ("Local", None, "refuse"),  # case-sensitive: only the exact literal "local" is non-production
    ("", None, "refuse"),  # present-but-empty is production, on BOTH discriminators
    (" local ", None, "refuse"),  # untrimmed is not the literal, so it fails closed
    ("production", "   ", "refuse"),  # blank-after-strip DSN == absent (the leg-4 legibility fix)
    ("local", None, "inmemory"),
    ("local", "   ", "inmemory"),  # ... and a blank DSN in local dev is 'absent', not a store
    ("production", _DSN, "store"),
    ("kubernetes", _DSN, "store"),
    ("local", _DSN, "store"),  # a DSN in local dev still gets the durable path
    ("production", f"  {_DSN}  ", "store"),  # merely PADDED is present: presence check, not normalizer
)


@pytest.mark.parametrize(("mode", "dsn", "expected"), _GATE_CASES)
def test_idempotency_gate_decision_table(mode: str, dsn: str | None, expected: str) -> None:
    if expected == "refuse":
        with pytest.raises(RuntimeError, match="no DATABASE_URL is present"):
            _require_idempotency_store_or_fail_closed(
                runtime_mode=mode, tenant="amh", edge="test", database_url=dsn
            )
        return
    store = _require_idempotency_store_or_fail_closed(
        runtime_mode=mode, tenant="amh", edge="test", database_url=dsn
    )
    if expected == "inmemory":
        # `None` -> the dispatcher's in-memory `_inflight` Guard 4 (single-process dev fallback).
        assert store is None
    else:
        # Lazy asyncpg pool: constructing the store must NOT require a reachable server.
        assert isinstance(store, PostgresIdempotencyStore)


def test_the_refusal_names_the_missing_operator_act_and_the_effect_it_prevents() -> None:
    """The error must be legible to whoever hits it at 3am — the missing operator act (provide
    DATABASE_URL), the durability it protects (Guard 4), the concrete effect it prevents
    (re-execution), and the one explicit way out (local mode). 'misconfigured' teaches nobody."""
    with pytest.raises(RuntimeError) as exc:
        _require_idempotency_store_or_fail_closed(
            runtime_mode="kubernetes", tenant="amh", edge="Helena->Rafael", database_url=None
        )
    message = str(exc.value)
    assert "DATABASE_URL" in message  # the missing operator act
    assert "Guard 4" in message  # the durability it protects
    assert "RE-EXECUTES" in message  # the concrete effect a non-durable store would cause
    assert "0003_a2a_idempotency" in message  # the migration that must be applied
    assert "Helena->Rafael" in message  # the edge, for legibility
    assert "AGENT_RUNTIME_MODE=local" in message  # the explicit, deliberate way out


def test_the_local_inmemory_path_is_intact_and_really_is_non_durable() -> None:
    """The one surviving non-durable branch: explicit local + no DSN. It must actually work (a dev
    laptop composes the edge with in-memory idempotency) and it must actually be the None fallback,
    never a fabricated durable store pointed at nothing."""
    store = _require_idempotency_store_or_fail_closed(
        runtime_mode="local", tenant="amh", edge="test", database_url=None
    )
    assert store is None


# ---------------------------------------------------------------------------
# Gate AGREEMENT — the coherence proof (two gates can never half-refuse a root)
# ---------------------------------------------------------------------------

#: (runtime_mode, database_url, must_refuse). The verdict is HARDCODED per row, provenance: a root
#: must refuse iff there is NO USABLE DSN (absent, empty, or blank-after-strip) AND the mode is not
#: the exact literal "local". Written as a literal bool on each row (NOT computed from
#: `is_production_runtime_mode` or `_dsn_is_present`) so the table is an independent oracle, not a
#: mirror of the code under test — a test that derives its own matrix cannot detect deletion.
#:
#: The oracle's RULE is unchanged by the ADR-0039 Q7 default flip: that flip changed what an ABSENT
#: `AGENT_RUNTIME_MODE` RESOLVES TO before reaching these gates (now "production", was "local"), not
#: what the gates do with a mode once they have one. These gates only ever see an already-resolved
#: mode string, so every row below is reachable exactly as before. What DID change is which row a
#: real absent-env deployment LANDS on: it used to land on `("local", None) -> build`, and now lands
#: on `("production", None) -> refuse`. Both rows were, and remain, pinned here.
#:
#: The leg-4 whitespace-DSN hardening widened the "no usable DSN" half of the rule from falsy to
#: blank-after-strip; the `"   "` rows below are that widening, and they are in THIS table (not only
#: in the per-gate tables) precisely because the hardening had to land on BOTH gates at once — a
#: one-sided fix would show up here as a half-refusing root.
_AGREEMENT_CASES: tuple[tuple[str, str | None, bool], ...] = (
    ("local", None, False),
    ("production", None, True),
    ("kubernetes", None, True),
    ("staging", None, True),
    ("", None, True),  # present-but-empty -> production
    ("Local", None, True),  # case-sensitive
    (" local ", None, True),  # untrimmed
    ("dev", None, True),  # any unrecognized mode is production
    ("local", _DSN, False),
    ("production", _DSN, False),
    ("kubernetes", _DSN, False),
    ("dev", _DSN, False),
    ("production", "", True),  # empty DSN is no DSN
    ("production", "   ", True),  # blank-after-strip DSN is no DSN (leg-4 hardening)
    ("kubernetes", "\t\n", True),  # ... any whitespace, not just spaces
    ("local", "   ", False),  # blank DSN in local dev: the sanctioned non-durable build, not a refusal
    ("production", f"  {_DSN}  ", False),  # merely PADDED is a real DSN -> build
)


def _gate_refuses(gate: Callable[..., Any], *, mode: str, dsn: str | None) -> bool:
    """True iff `gate` REFUSES (raises RuntimeError) for this (mode, dsn); False iff it builds."""
    try:
        gate(runtime_mode=mode, tenant="amh", edge="t", database_url=dsn)
    except RuntimeError:
        return True
    return False


@pytest.mark.parametrize(("mode", "dsn", "expected_refuse"), _AGREEMENT_CASES)
def test_the_two_gates_agree_on_every_combination(mode: str, dsn: str | None, expected_refuse: bool) -> None:
    """The coherence proof. Leg-2's fact gate and leg-3's idempotency gate must produce the SAME
    build/refuse verdict for EVERY (mode, DSN) — otherwise a composition root could half-refuse
    (one gate building, the other raising), which is the MAJOR risk of adding a second durability
    gate. Both are checked against the SAME hardcoded oracle AND against each other, so a future
    drift in either gate's condition reddens here."""
    fact_refuses = _gate_refuses(_require_fact_producer_or_fail_closed, mode=mode, dsn=dsn)
    idem_refuses = _gate_refuses(_require_idempotency_store_or_fail_closed, mode=mode, dsn=dsn)
    assert fact_refuses is expected_refuse  # leg-2 gate matches the independent oracle
    assert idem_refuses is expected_refuse  # leg-3 gate matches the independent oracle
    assert fact_refuses == idem_refuses  # ... therefore the two can never half-refuse a root


# ---------------------------------------------------------------------------
# RED control — name the rows the fail-closed branch owns, and prove neutering greens them wrong
# ---------------------------------------------------------------------------


def _neutered_idempotency_gate(
    *, runtime_mode: str, tenant: str, edge: str, database_url: str | None
) -> PostgresIdempotencyStore | None:
    """The PRE-leg-3 truthy-only wiring the roots used to inline: build when a DSN is present, else
    silently `None`. It NEVER raises. Exists only so the RED-control test below can name EXACTLY
    which decision-table rows the real gate's fail-closed branch is solely responsible for."""
    return PostgresIdempotencyStore(dsn=database_url, tenant=tenant) if database_url else None


_REFUSAL_ROWS: tuple[tuple[str, str | None], ...] = tuple(
    (mode, dsn) for (mode, dsn, expected) in _GATE_CASES if expected == "refuse"
)

#: (mode, dsn, what the NEUTERED gate wrongly does instead of refusing). Hardcoded per row, because
#: the pre-gate code was wrong in TWO distinct ways and collapsing them would lose information:
#:
#:   "none"             -> silent non-durable build (the dispatcher's in-memory `_inflight` Guard 4).
#:                         This is the leg-3 defect: a falsy DSN fell through to `None`.
#:   "fabricated_store" -> a real `PostgresIdempotencyStore` pointed at whitespace garbage. This is
#:                         the leg-4 FINDING the `_dsn_is_present` hardening closed: a blank DSN is
#:                         TRUTHY, so the neutered gate never even reached its `None` branch. It
#:                         failed CLOSED at first connect (never a security fail-open) but replaced
#:                         a legible composition-time refusal with an opaque runtime connect error.
#:
#: Both are wrong-greens — neither REFUSES — which is the property the test below actually owns.
_NEUTERED_WRONG_GREEN: tuple[tuple[str, str | None, str], ...] = (
    ("production", None, "none"),
    ("kubernetes", None, "none"),
    ("staging", None, "none"),
    ("Local", None, "none"),
    ("", None, "none"),
    (" local ", None, "none"),
    ("production", "   ", "fabricated_store"),
)


def test_every_refusal_row_has_a_characterized_neutered_outcome() -> None:
    """Guard on the guard: the two tables must cover the SAME rows. Without this, adding a refusal
    row to `_GATE_CASES` (as the leg-4 whitespace hardening did) could silently escape the RED
    control below, leaving a fail-closed branch with no proof that neutering it actually regresses."""
    assert {(mode, dsn) for mode, dsn, _ in _NEUTERED_WRONG_GREEN} == set(_REFUSAL_ROWS)


@pytest.mark.parametrize(("mode", "dsn", "wrong_outcome"), _NEUTERED_WRONG_GREEN)
def test_red_control_neutering_the_gate_silently_greens_exactly_these_rows(
    mode: str, dsn: str | None, wrong_outcome: str
) -> None:
    """RED control. These are the rows the fail-closed branch OWNS. The REAL gate RAISES on each;
    the PRE-leg-3 neutered gate (truthy-only, never raises) does NOT — so reverting the gate to its
    old shape turns every one of these rows from a refusal into a wrong-green, which
    `test_idempotency_gate_decision_table` catches as RED (it asserts the raise). This pins the
    neuter->wrong-green delta explicitly, per-row, AND names which of the two wrong behaviours each
    row gets (see `_NEUTERED_WRONG_GREEN`) — "it didn't refuse" alone would not distinguish the
    silent non-durable build from the store-pointed-at-garbage the whitespace row produces."""
    with pytest.raises(RuntimeError):
        _require_idempotency_store_or_fail_closed(runtime_mode=mode, tenant="amh", edge="t", database_url=dsn)

    neutered = _neutered_idempotency_gate(runtime_mode=mode, tenant="amh", edge="t", database_url=dsn)
    if wrong_outcome == "none":
        assert neutered is None  # silent non-durable build
    else:
        # Not a refusal and not even an honest `None`: a durable-LOOKING store over garbage.
        assert isinstance(neutered, PostgresIdempotencyStore)
        assert neutered._dsn == dsn  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# The gate at the composition-root level — BOTH roots
# ---------------------------------------------------------------------------


def test_dossier_root_refuses_for_idempotency_even_with_an_injected_producer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The dossier root is where the gate genuinely bites, and this is the load-bearing test: a
    signed, fully-dep'd, production edge WITH a producer injected (so leg-2's fact gate is bypassed)
    is STILL refused, because durable idempotency has no DSN. It proves the idempotency gate is NOT
    redundant with the fact gate — injecting a producer does not buy you out of it. Before leg 3
    this exact call built a dispatcher with `idempotency=None` (silent non-durable Guard 4)."""
    monkeypatch.setenv(_SIGNING_KEY_ENV, _VALID_KEY)
    with pytest.raises(RuntimeError, match="durable, cross-replica idempotency"):
        build_dossier_delegation_dispatcher(
            tenant="amh",
            runtime_mode="production",
            database_url=None,
            kafka_producer=RecordingProducer(),
            **_dossier_deps(),
        )


def test_dossier_root_in_explicit_local_without_a_dsn_still_composes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Dev ergonomics preserved (the None/in-memory path): explicit local + no DSN still builds a
    working edge with in-memory idempotency (an injected producer keeps the fact gate quiet too)."""
    monkeypatch.setenv(_SIGNING_KEY_ENV, _VALID_KEY)
    dispatcher = build_dossier_delegation_dispatcher(
        tenant="amh",
        runtime_mode="local",
        database_url=None,
        kafka_producer=RecordingProducer(),
        **_dossier_deps(),
    )
    assert dispatcher is not None


def test_dossier_root_with_a_dsn_wires_the_store_without_connecting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A DSN present -> the durable store is wired at both roots. Construction stays pure: the
    asyncpg pool is LAZY, so wiring the store must not require a reachable server (this suite has
    none)."""
    monkeypatch.setenv(_SIGNING_KEY_ENV, _VALID_KEY)
    from maezo.gateway.audit_postgres import PostgresAuditSink

    deps = _dossier_deps()
    deps["audit_sink"] = PostgresAuditSink(_DSN, "amh")
    dispatcher = build_dossier_delegation_dispatcher(
        tenant="amh",
        runtime_mode="production",
        database_url=_DSN,
        **deps,
    )
    assert dispatcher is not None


def test_auth_root_is_gated_too_and_refuses_for_idempotency(monkeypatch: pytest.MonkeyPatch) -> None:
    """The agent-runtime root (`build_auth_delegation_dispatcher`) is gated identically. Here the
    idempotency gate is DEFENSE-IN-DEPTH — the real root raises `ValueError` on the missing
    `audit_sink` for the SAME absent DSN several lines earlier (leg-2's reachability note), so to
    isolate and prove THIS gate is wired: `_build_tool_deps` is faked to supply the deps,
    `make_rafael_handler` is stubbed (its real `graph.build` needs live transports), the signing key
    is set (past the signer gate) and a producer is injected (past the fact gate) — leaving the
    idempotency gate as the thing that fires."""
    monkeypatch.setenv(_SIGNING_KEY_ENV, _VALID_KEY)

    def _fake_tool_deps(_settings_obj: AgentRuntimeSettings, inference: Any = None) -> dict[str, Any]:
        return {"dmn": object(), "cibseven": object(), "audit_sink": object(), "inference": inference}

    async def _stub_handler(_envelope: DelegationEnvelope) -> HandlerOutput:
        raise AssertionError("the handler must never run in a composition-refusal test")

    def _stub_make_rafael_handler(*_args: Any, **_kwargs: Any) -> _HandlerFn:
        return _stub_handler

    monkeypatch.setattr(a2a_composition, "_build_tool_deps", _fake_tool_deps)
    monkeypatch.setattr(a2a_composition, "make_rafael_handler", _stub_make_rafael_handler)

    settings = AgentRuntimeSettings(agent_id="rafael", agent_runtime_mode="kubernetes")
    assert settings.database_url is None
    with pytest.raises(RuntimeError, match="durable, cross-replica idempotency"):
        build_auth_delegation_dispatcher(settings, kafka_producer=RecordingProducer())


# ---------------------------------------------------------------------------
# Seam discipline — this leg added NO public method to the dispatcher
# ---------------------------------------------------------------------------


def test_the_dispatcher_public_surface_is_still_only_delegate() -> None:
    """`GatedDelegationDispatcher` gates by SUBCLASS, safe only while `delegate` is the base's ONLY
    public method (`gateway/seams/a2a.py`). Idempotency flows through the `idempotency=` constructor
    argument of `build_dispatcher`, AROUND the dispatcher's public surface — so this leg added
    nothing to it. Re-asserted rather than assumed (leg-4 requirement)."""
    from maezo.a2a.dispatcher import DelegationDispatcher

    public = {name for name in vars(DelegationDispatcher) if not name.startswith("_")}
    assert public == {"delegate"}

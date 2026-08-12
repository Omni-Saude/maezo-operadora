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


@pytest.fixture(autouse=True)
def _stub_a2a_audit_sink_probe(monkeypatch: pytest.MonkeyPatch) -> None:
    """T2.4 A2A W4: `_probe_a2a_audit_sink` would otherwise make a REAL network attempt (against
    this module's dummy `_DSN`) for every helena/rafael bring-up test in this file — this module's
    own docstring/comments document the pre-W4 contract that bring-up construction never opens a
    connection. Stub the probe deterministically GREEN by default so that contract holds for every
    test that doesn't care about audit-sink readiness specifically; the dedicated
    `a2a_audit_sink_ready` tests below override this within their own test body (a later
    `monkeypatch.setattr` call on the same fixture wins for the rest of that test)."""
    import maezo.runtime.agent_runtime.service as svc

    async def _ok(sink: object, timeout_s: float) -> bool:
        return True

    monkeypatch.setattr(svc, "_probe_a2a_audit_sink", _ok)


@pytest.fixture(autouse=True)
def _allow_unsigned_a2a_cards_in_dev(monkeypatch: pytest.MonkeyPatch) -> None:
    """F2: bring-up assembles the Helena->Rafael A2A edge in a dev/"local" `agent_runtime_mode` with
    no Card-signing key. The composition root REFUSES to build an UNSIGNED dispatcher unless the
    explicit non-production opt-out is set (no silent downgrade) — so set it here: these
    health-daemon tests deliberately exercise the dev/unsigned assembly path, and the opt-out is
    exactly the explicit dev signal F2 requires. Also delenv the signing key so this suite never
    depends on ambient env state.

    ADR-0039 Q7: `AGENT_RUNTIME_MODE=local` is now set EXPLICITLY here. It used to be inherited from
    `AgentRuntimeSettings`' pydantic default, which is now the fail-closed "production" — so without
    this line the dev/unsigned path these tests exist to exercise would be (correctly) refused, and
    the suite would be testing the refusal instead of the daemon behaviour it is named for. The
    tests that want PRODUCTION pass `agent_runtime_mode=` explicitly to the constructor, which wins
    over this env var (pydantic-settings: init kwargs outrank env), so their coverage is unaffected.
    The daemon-level consequence of the ABSENT variable is pinned separately, in
    `test_absent_runtime_mode_*` at the end of this module."""
    monkeypatch.setenv("MAEZO_A2A_ALLOW_UNSIGNED_CARDS", "1")
    monkeypatch.delenv("MAEZO_A2A_CARD_SIGNING_KEY", raising=False)
    monkeypatch.setenv("AGENT_RUNTIME_MODE", "local")


@pytest.fixture(autouse=True)
def _stub_checkpointer_connect(monkeypatch: pytest.MonkeyPatch) -> None:
    """T3.4/F4: `_provision_checkpointer` genuinely opens an AsyncPostgresSaver pool + awaited `setup()`
    (unlike the pre-existing construction-only deps, which are lazy against the dummy `_DSN`). So
    for every bring-up test that isn't specifically about the checkpointer, stub the connect to
    fail fast — the suite's explicit `agent_runtime_mode="local"` (set by
    `_allow_unsigned_a2a_cards_in_dev`; it was the pydantic DEFAULT before ADR-0039 Q7) then
    deterministically takes the in-memory fallback (checkpointer_ready GREEN, backend=memory) with
    NO real network I/O against `_DSN`. The dedicated checkpointer tests below override this within
    their own body."""
    import maezo.runtime.agent_runtime.service as svc

    async def _refuse(conn_string: str) -> object:
        raise ConnectionError("stubbed: no real Postgres in this unit suite")

    monkeypatch.setattr(svc.Checkpointer, "connect_and_setup", classmethod(lambda cls, dsn: _refuse(dsn)))


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


async def test_a2a_dispatcher_ready_red_in_production_without_signing_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """F2 (daemon level): in PRODUCTION runtime mode (`agent_runtime_mode="kubernetes"`, as Helm
    injects) with no Card-signing key, the composition fail-CLOSES — so the daemon comes up with
    `a2a_dispatcher_ready` RED (never silently healthy on an unsigned edge), and the recorded error
    names the missing signing key. The module's autouse dev opt-out is IGNORED in production, which
    is exactly the un-bypassable property F2 guarantees."""
    monkeypatch.delenv("MAEZO_A2A_CARD_SIGNING_KEY", raising=False)
    state = _state(
        settings=AgentRuntimeSettings(
            agent_id="rafael", tenant_id="amh", database_url=_DSN, agent_runtime_mode="kubernetes"
        )
    )
    await _bring_up_dependencies(state)
    checks = {c.__name__: c for c in build_readiness_checks(state)}
    result = await checks["a2a_dispatcher_ready"]()
    assert result.healthy is False
    assert "signing key" in (state.a2a_dispatcher_error or "")


async def test_all_readiness_checks_healthy_after_real_bring_up() -> None:
    state = _state(settings=AgentRuntimeSettings(agent_id="helena", tenant_id="amh", database_url=_DSN))
    await _bring_up_dependencies(state)

    checks = build_readiness_checks(state)
    results = [await c() for c in checks]
    assert all(r.healthy for r in results), results


# ---------------------------------------------------------------------------
# a2a_audit_sink_ready (T2.4 A2A W4 — T-F daemon-readiness finalization)
# ---------------------------------------------------------------------------


async def test_probe_a2a_audit_sink_red_when_unreachable(monkeypatch: pytest.MonkeyPatch) -> None:
    """Bounded probe against an address that refuses instantly fails closed — mirrors
    `worker_runtime`'s own `_probe_audit_sink` unreachable-probe test exactly.

    `monkeypatch.undo()` reverts THIS module's autouse `_stub_a2a_audit_sink_probe` fixture first —
    otherwise the `from ... import _probe_a2a_audit_sink` below would bind the STUBBED name (the
    autouse fixture patches the module attribute directly), defeating the point of this test.
    """
    monkeypatch.undo()
    from maezo.gateway.audit_postgres import PostgresAuditSink
    from maezo.runtime.agent_runtime.service import _probe_a2a_audit_sink

    sink = PostgresAuditSink("postgresql://maezo@127.0.0.1:1/none", "amh")
    try:
        healthy = await _probe_a2a_audit_sink(sink, 1.0)
        assert healthy is False
    finally:
        await sink.aclose()


async def test_probe_a2a_audit_sink_green_when_probe_passes(monkeypatch: pytest.MonkeyPatch) -> None:
    """The probe is stubbed (no live Postgres needed here) — a real-PG green path belongs to the
    integration/live-PG suites, mirroring `worker_runtime`'s equivalent unit test.

    `monkeypatch.undo()` reverts the autouse `_stub_a2a_audit_sink_probe` fixture first — same
    reason as `test_probe_a2a_audit_sink_red_when_unreachable` above: this test exercises the REAL
    `_probe_a2a_audit_sink` (with `PostgresAuditSink.check_ready` itself stubbed instead)."""
    monkeypatch.undo()
    from maezo.gateway.audit_postgres import PostgresAuditSink
    from maezo.runtime.agent_runtime.service import _probe_a2a_audit_sink

    sink = PostgresAuditSink("postgresql://maezo@localhost:5432/maezo", "amh")

    async def _ok() -> None:
        return None

    monkeypatch.setattr(sink, "check_ready", _ok)
    try:
        healthy = await _probe_a2a_audit_sink(sink, 5.0)
        assert healthy is True
    finally:
        await sink.aclose()


async def test_a2a_audit_sink_ready_not_applicable_for_unrelated_agent() -> None:
    state = _state(settings=AgentRuntimeSettings(agent_id="marina", tenant_id="amh"))
    checks = {c.__name__: c for c in build_readiness_checks(state)}
    result = await checks["a2a_audit_sink_ready"]()
    assert result.healthy is True
    assert result.detail == "not applicable to agent_id='marina'"


async def test_a2a_audit_sink_ready_unhealthy_when_never_probed() -> None:
    state = _state(settings=AgentRuntimeSettings(agent_id="rafael", tenant_id="amh"))
    checks = {c.__name__: c for c in build_readiness_checks(state)}
    result = await checks["a2a_audit_sink_ready"]()
    assert result.healthy is False


async def test_a2a_audit_sink_ready_unhealthy_without_database_url() -> None:
    """No DATABASE_URL -> nothing to probe -> fail-closed, mirrors `a2a_dispatcher_ready`'s own
    DATABASE_URL-gated posture."""
    state = _state(settings=AgentRuntimeSettings(agent_id="rafael", tenant_id="amh"))
    await _bring_up_dependencies(state)
    checks = {c.__name__: c for c in build_readiness_checks(state)}
    result = await checks["a2a_audit_sink_ready"]()
    assert result.healthy is False
    assert "DATABASE_URL" in (state.a2a_audit_sink_error or "")


async def test_a2a_audit_sink_ready_healthy_after_bring_up_with_verified_probe() -> None:
    """Proves the wiring end-to-end (bring-up -> `state.a2a_audit_sink_ready` -> the readiness
    check) against the autouse-stubbed GREEN probe — no live Postgres needed."""
    state = _state(settings=AgentRuntimeSettings(agent_id="rafael", tenant_id="amh", database_url=_DSN))
    await _bring_up_dependencies(state)
    assert state.a2a_audit_sink_ready is True
    checks = {c.__name__: c for c in build_readiness_checks(state)}
    result = await checks["a2a_audit_sink_ready"]()
    assert result.healthy is True


async def test_a2a_audit_sink_ready_unhealthy_when_probe_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    """FAIL-CLOSED: a present-but-unreachable audit sink keeps `/readyz` red (ADR-0007) — the
    daemon refuses to consider delegation processing ready even though the dispatcher itself
    assembled successfully (`a2a_dispatcher_ready` stays construction-only, unaffected)."""
    import maezo.runtime.agent_runtime.service as svc

    async def _fail(sink: object, timeout_s: float) -> bool:
        return False

    monkeypatch.setattr(svc, "_probe_a2a_audit_sink", _fail)
    state = _state(settings=AgentRuntimeSettings(agent_id="rafael", tenant_id="amh", database_url=_DSN))
    await _bring_up_dependencies(state)
    assert state.a2a_audit_sink_ready is False

    checks = {c.__name__: c for c in build_readiness_checks(state)}
    audit = await checks["a2a_audit_sink_ready"]()
    dispatcher = await checks["a2a_dispatcher_ready"]()
    assert audit.healthy is False
    assert dispatcher.healthy is True, state.a2a_dispatcher_error


def test_agent_state_is_live_helper() -> None:
    state = _state()
    assert state.is_live() is True
    state.live = False
    assert state.is_live() is False


# ---------------------------------------------------------------------------
# checkpointer_ready (T3.4/F4 — durable LangGraph checkpoint persistence, fail-closed)
# ---------------------------------------------------------------------------


async def test_checkpointer_fail_closed_in_production_without_database_url() -> None:
    """PRODUCTION (`agent_runtime_mode="kubernetes"`) with no DATABASE_URL: NO in-memory fallback —
    the daemon must not silently run stateless, so `checkpointer_ready` stays RED and the error
    names the missing durable backend (F2 discipline, mirrors a2a_composition)."""
    state = _state(
        settings=AgentRuntimeSettings(agent_id="rafael", tenant_id="amh", agent_runtime_mode="kubernetes")
    )
    await _bring_up_dependencies(state)

    assert state.checkpointer_ready is False
    assert state.checkpointer is None
    assert "DATABASE_URL" in (state.checkpointer_error or "")

    checks = {c.__name__: c for c in build_readiness_checks(state)}
    result = await checks["checkpointer_ready"]()
    assert result.healthy is False


async def test_checkpointer_fail_closed_in_production_on_setup_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """PRODUCTION with DATABASE_URL set but `setup()`/connect failing: fail CLOSED (no fallback).
    The autouse `_stub_checkpointer_connect` already makes connect raise — in production that is a
    RED readiness gate, never a silent in-memory degrade."""
    state = _state(
        settings=AgentRuntimeSettings(
            agent_id="rafael",
            tenant_id="amh",
            database_url=_DSN,
            agent_runtime_mode="kubernetes",
        )
    )
    await _bring_up_dependencies(state)

    assert state.checkpointer_ready is False
    assert state.checkpointer_backend is None
    assert "setup failed" in (state.checkpointer_error or "")

    checks = {c.__name__: c for c in build_readiness_checks(state)}
    assert (await checks["checkpointer_ready"]()).healthy is False


async def test_checkpointer_local_fallback_to_memory_with_warning() -> None:
    """LOCAL/dev with no DATABASE_URL: falls back to an in-memory saver so dev ergonomics work —
    `checkpointer_ready` GREEN, backend named 'memory' (the warning is emitted by the helper)."""
    state = _state(
        settings=AgentRuntimeSettings(agent_id="rafael", tenant_id="amh", agent_runtime_mode="local")
    )
    await _bring_up_dependencies(state)

    assert state.checkpointer_ready is True
    assert state.checkpointer_backend == "memory"
    assert state.checkpointer is not None

    checks = {c.__name__: c for c in build_readiness_checks(state)}
    result = await checks["checkpointer_ready"]()
    assert result.healthy is True
    assert result.detail == "backend=memory"


async def test_checkpointer_local_fallback_to_memory_on_setup_failure() -> None:
    """LOCAL with a DATABASE_URL that fails to connect: dev still degrades to in-memory (GREEN) —
    the autouse stub makes connect raise; local mode is permitted to fall back (unlike prod)."""
    state = _state(
        settings=AgentRuntimeSettings(
            agent_id="rafael", tenant_id="amh", database_url=_DSN, agent_runtime_mode="local"
        )
    )
    await _bring_up_dependencies(state)

    assert state.checkpointer_ready is True
    assert state.checkpointer_backend == "memory"


# ---------------------------------------------------------------------------
# ADR-0039 Q7 — the ABSENT `AGENT_RUNTIME_MODE`, pinned at the DAEMON level
# ---------------------------------------------------------------------------
#
# The rest of this module sets `AGENT_RUNTIME_MODE=local` explicitly (see
# `_allow_unsigned_a2a_cards_in_dev`) because it is about daemon behaviour, not about mode
# resolution. These two tests are the opposite: they `delenv` it — a later `monkeypatch` call in the
# test body wins over the autouse fixture — so a genuinely-unconfigured pod is what boots. Before
# the Q7 flip both of these came up GREEN on the permissive branch, which is exactly the hazard the
# flip closes: an operator who forgets the variable got a stateless, unsigned-capable daemon that
# advertised itself as ready.


async def test_absent_runtime_mode_makes_the_checkpointer_fail_closed_at_the_daemon(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Daemon-level consequence (a): with `AGENT_RUNTIME_MODE` ABSENT and no DATABASE_URL, the
    daemon refuses to run stateless — `checkpointer_ready` is RED and the error names the missing
    durable backend. Compare `test_checkpointer_local_fallback_to_memory_with_warning`, which is the
    SAME call with an explicit `local`: it is GREEN on backend=memory. That contrast is the flip:
    the in-memory fallback is now reachable only by explicit opt-in, never by omission."""
    monkeypatch.delenv("AGENT_RUNTIME_MODE", raising=False)

    settings = AgentRuntimeSettings(agent_id="rafael", tenant_id="amh")
    assert settings.agent_runtime_mode == "production"  # the default under test, not an assumption
    state = _state(settings=settings)
    await _bring_up_dependencies(state)

    assert state.checkpointer_ready is False
    assert state.checkpointer is None
    assert "DATABASE_URL" in (state.checkpointer_error or "")

    checks = {c.__name__: c for c in build_readiness_checks(state)}
    assert (await checks["checkpointer_ready"]()).healthy is False


async def test_absent_runtime_mode_refuses_the_unsigned_a2a_edge_despite_the_dev_optout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Daemon-level consequence (b), and the sharper one: the autouse fixture leaves
    `MAEZO_A2A_ALLOW_UNSIGNED_CARDS=1` set, and with the mode ABSENT that opt-out is now IGNORED —
    `a2a_dispatcher_ready` comes up RED naming the missing signing key. Before the flip this exact
    environment (no mode, no key, opt-out on) composed an UNSIGNED dispatcher and reported GREEN.
    A DSN is supplied so the composition gets past `audit_sink` and the signer gate is genuinely
    what fires, rather than the test passing for an unrelated missing dependency."""
    monkeypatch.delenv("AGENT_RUNTIME_MODE", raising=False)
    monkeypatch.delenv("MAEZO_A2A_CARD_SIGNING_KEY", raising=False)
    monkeypatch.setenv("MAEZO_A2A_ALLOW_UNSIGNED_CARDS", "1")

    state = _state(settings=AgentRuntimeSettings(agent_id="rafael", tenant_id="amh", database_url=_DSN))
    await _bring_up_dependencies(state)

    checks = {c.__name__: c for c in build_readiness_checks(state)}
    assert (await checks["a2a_dispatcher_ready"]()).healthy is False
    assert "signing key" in (state.a2a_dispatcher_error or "")

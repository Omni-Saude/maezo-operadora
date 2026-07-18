"""Unit tests for the T-C2 process-start provenance chokepoint fence.

`start_process_idempotent` (`maezo.tools.mcp_cibseven.transport`) is the SINGLE agent-side effect
chokepoint (docs/design/audit-emit-path-wiring.md SHOULD-FIX 3). These tests prove the structural
fence directly, without any of the 9 agent graphs: `audit_sink` + `provenance` are REQUIRED (a
missed caller fails LOUDLY at call time), the durable audit is emitted BEFORE the engine effect and
fail-closed, the record is PHI-safe, and the dedup key is idempotency-stable.
"""

from __future__ import annotations

import inspect
from typing import Any

import pytest

from maezo.gateway.audit import AuditRecord, hash_input
from maezo.gateway.audit_postgres import AuditPersistenceError
from maezo.tools.mcp_cibseven.transport import (
    AgentDecisionProvenance,
    AuditStartSink,
    FakeCibSevenTransport,
    ProcessInstance,
    build_start_audit_record,
    start_dedup_key,
    start_process_idempotent,
)
from tests.support.audit_fakes import FakeStartAuditSink


def _provenance(**overrides: Any) -> AgentDecisionProvenance:
    base: dict[str, Any] = {
        "agent_id": "andre",
        "agent_version": "andre@v0",
        "tenant_id": "amh",
        "decision_basis": {"route": "human_review", "faixa_valor": "ACIMA_TETO"},
        "model_id": "claude-x",
        "prompt_version": "sys@v1",
    }
    base.update(overrides)
    return AgentDecisionProvenance(**base)


async def _start(
    transport: FakeCibSevenTransport,
    sink: AuditStartSink,
    *,
    process_key: str = "SP-OP-PAGTO-001",
    business_key: str = "PAGTO-amh-1",
    variables: dict[str, Any] | None = None,
    provenance: AgentDecisionProvenance | None = None,
) -> ProcessInstance:
    return await start_process_idempotent(
        transport,
        process_key=process_key,
        business_key=business_key,
        variables=variables if variables is not None else {"numero_guia_tiss": "G-SECRET", "x": 1},
        audit_sink=sink,
        provenance=provenance or _provenance(),
    )


# ---------------------------------------------------------------------------
# The fence: required params (a missed caller fails loudly at call time)
# ---------------------------------------------------------------------------


def test_fence_signature_makes_sink_and_provenance_required_keyword_only() -> None:
    sig = inspect.signature(start_process_idempotent)
    for name in ("audit_sink", "provenance"):
        param = sig.parameters[name]
        assert param.default is inspect.Parameter.empty, f"{name} must be REQUIRED (no default)"
        assert param.kind is inspect.Parameter.KEYWORD_ONLY, f"{name} must be keyword-only"


async def test_fence_rejects_sinkless_call() -> None:
    fake = FakeCibSevenTransport()
    with pytest.raises(TypeError):
        await start_process_idempotent(  # type: ignore[call-arg]
            fake,
            process_key="SP-OP-PAGTO-001",
            business_key="PAGTO-amh-1",
            variables={},
            provenance=_provenance(),
        )


async def test_fence_rejects_provenanceless_call() -> None:
    fake = FakeCibSevenTransport()
    with pytest.raises(TypeError):
        await start_process_idempotent(  # type: ignore[call-arg]
            fake,
            process_key="SP-OP-PAGTO-001",
            business_key="PAGTO-amh-1",
            variables={},
            audit_sink=FakeStartAuditSink(),
        )


# ---------------------------------------------------------------------------
# Emit BEFORE effect, fail-closed
# ---------------------------------------------------------------------------


async def test_emits_exactly_once_before_starting() -> None:
    events: list[str] = []

    class _OrderingTransport(FakeCibSevenTransport):
        async def find_active_instance(self, business_key: str) -> ProcessInstance | None:
            events.append("find_active")
            return await super().find_active_instance(business_key)

        async def start_process_instance(self, *a: Any, **k: Any) -> ProcessInstance:
            events.append("start")
            return await super().start_process_instance(*a, **k)

    class _OrderingSink(FakeStartAuditSink):
        async def emit_once(self, record: AuditRecord, *, dedup_key: str) -> str:
            events.append("emit")
            return await super().emit_once(record, dedup_key=dedup_key)

    sink = _OrderingSink()
    inst = await _start(_OrderingTransport(), sink)

    assert inst.already_existed is False
    assert events == ["emit", "find_active", "start"], "audit must precede the engine effect"
    assert len(sink.calls) == 1


async def test_fail_closed_sink_error_prevents_engine_start() -> None:
    class _NeverStarts(FakeCibSevenTransport):
        async def start_process_instance(self, *a: Any, **k: Any) -> ProcessInstance:
            raise AssertionError("engine start ran despite an audit failure — fail-OPEN!")

        async def find_active_instance(self, business_key: str) -> ProcessInstance | None:
            raise AssertionError("idempotency probe ran despite an audit failure — fail-OPEN!")

    with pytest.raises(AuditPersistenceError):
        await _start(_NeverStarts(), FakeStartAuditSink(fail=True))


# ---------------------------------------------------------------------------
# Idempotency + dedup key
# ---------------------------------------------------------------------------


async def test_existing_active_instance_still_audits_but_never_double_starts() -> None:
    transport = FakeCibSevenTransport()
    transport.seed_instance(
        ProcessInstance(
            instance_id="existing-1",
            process_key="SP-OP-PAGTO-001",
            business_key="PAGTO-amh-1",
            state="ACTIVE",
            already_existed=True,
        )
    )
    sink = FakeStartAuditSink()

    class _NoStart(FakeCibSevenTransport):
        async def start_process_instance(self, *a: Any, **k: Any) -> ProcessInstance:
            raise AssertionError("double-started an already-active instance")

    transport.start_process_instance = _NoStart().start_process_instance  # type: ignore[method-assign]
    inst = await _start(transport, sink)

    assert inst.instance_id == "existing-1"
    assert inst.already_existed is True
    assert len(sink.calls) == 1, "the agent's decision to start is audited even on an idempotent hit"


async def test_dedup_key_shape_matches_design_and_is_business_key_stable() -> None:
    sink = FakeStartAuditSink()
    await _start(
        sink=sink, transport=FakeCibSevenTransport(), process_key="SP-OP-AUTH-001", business_key="AUTH-amh-77"
    )
    assert sink.dedup_keys == ["amh:start:SP-OP-AUTH-001:AUTH-amh-77"]
    assert start_dedup_key("amh", "SP-OP-AUTH-001", "AUTH-amh-77") == "amh:start:SP-OP-AUTH-001:AUTH-amh-77"


# ---------------------------------------------------------------------------
# PHI discipline (§3.3): curated tokens in clear, raw inputs hashed, PHI redacted
# ---------------------------------------------------------------------------


async def test_record_is_phi_safe_and_binds_inputs_by_hash() -> None:
    sink = FakeStartAuditSink()
    variables = {"numero_guia_tiss": "G-SECRET-123", "matricula_beneficiario": "M-SECRET", "valor": 5000}
    prov = _provenance(
        decision_basis={
            "route": "human_review",
            "grupo_aprovador": "comite",
            # A clinical free-text field slips into the caller's basis — the backstop MUST redact it.
            "justificativa_clinica": "paciente com diagnostico X",
        }
    )
    await _start(
        FakeCibSevenTransport(), sink, business_key="PAGTO-amh-999", variables=variables, provenance=prov
    )

    (record, _dedup) = sink.calls[0]
    blob = str(record.details)
    # Raw start variables never persisted in the clear — only a one-way hash.
    assert "G-SECRET-123" not in blob and "M-SECRET" not in blob
    assert record.details["input_sha256"] == hash_input(variables)
    # Resolvable business key never in the durable chain (§3.3).
    assert "PAGTO-amh-999" not in blob
    # Clinical free text redacted by the one-way backstop.
    assert record.details["justificativa_clinica"] == "[REDACTED_PHI]"
    # Curated bounded tokens survive in the clear.
    assert record.details["route"] == "human_review"
    assert record.details["grupo_aprovador"] == "comite"
    # ADR-0007 tuple populated.
    assert record.agent_id == "andre"
    assert record.action == "start_process:SP-OP-PAGTO-001"
    assert record.decision == "START_PROCESS"
    assert record.model_id == "claude-x"
    assert record.prompt_version == "sys@v1"
    assert record.tenant_id == "amh"


def test_build_start_audit_record_is_pure_and_hashes_variables() -> None:
    prov = _provenance(decision_basis={"route": "auto_route"})
    rec = build_start_audit_record(
        prov, process_key="SP-OP-PAGTO-001", business_key="PAGTO-amh-1", variables={"a": 1}
    )
    assert rec.details["input_sha256"] == hash_input({"a": 1})
    assert rec.details["process_key"] == "SP-OP-PAGTO-001"
    assert rec.action == "start_process:SP-OP-PAGTO-001"

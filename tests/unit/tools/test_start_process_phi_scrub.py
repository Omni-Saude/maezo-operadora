"""CC-06 — the agent -> engine start chokepoint scrubs LLM/human FREE TEXT before it lands.

`start_process_idempotent` (`maezo.tools.mcp_cibseven.transport`) is the SINGLE agent-side start
chokepoint. Before CC-06 it forwarded `variables` to `transport.start_process_instance` VERBATIM:
`build_start_audit_record` ran `redact_phi_vars` over `provenance.decision_basis` only, and bound
the variables by a one-way `input_sha256` that never inspected them. Every agent that starts a
process puts LLM-drafted prose in those variables — `resumo_contexto` (helena/lucas/fernando) and
the `narrativa` nested in each `dossie_<agent>` — so an identifier the model copied out of the
beneficiary's own message reached the engine's process-variable store in the clear.

These tests drive the chokepoint directly against `FakeCibSevenTransport` (no agent graph, no
engine), so they pin the invariant at the ONE place it is enforced rather than once per agent:
    1. free-text values are redacted at the boundary (top level AND nested in a dossier);
    2. STRUCTURED values are byte-identical on the other side (this is the property that makes
       the scrub safe to put on a BPMN process's input contract);
    3. `input_sha256` binds what the ENGINE received, not a pre-scrub form nothing retains;
    4. a scrub that cannot complete REFUSES the start — no claim, no engine call, no passthrough.
Synthetic identifiers only.
"""

from __future__ import annotations

from typing import Any

import pytest

from maezo.gateway.audit import hash_input
from maezo.tools.mcp_cibseven.transport import (
    AgentDecisionProvenance,
    CibSevenError,
    FakeCibSevenTransport,
    StartVariableRedactionError,
    redact_start_variables,
    start_process_idempotent,
)
from maezo.tools.workers.phi_vars import REDACTED_DIGITS, REDACTED_EMAIL, REDACTED_PHONE
from tests.support.audit_fakes import FakeStartAuditSink

#: Synthetic identifiers — never real PHI. The CPF is the same one Helena's own leak regressions
#: use (`tests/unit/agents/test_helena.py`), so a reader can follow the value across both layers.
_CPF = "123.456.789-09"
_EMAIL = "beneficiario.teste@exemplo.com.br"
_PHONE = "(11) 98765-4321"


def _provenance(**overrides: Any) -> AgentDecisionProvenance:
    base: dict[str, Any] = {
        "agent_id": "rafael",
        "agent_version": "rafael@v0",
        "tenant_id": "amh",
        "decision_basis": {"route": "human_auditor"},
        "model_id": "claude-x",
        "prompt_version": "sys@v1",
    }
    base.update(overrides)
    return AgentDecisionProvenance(**base)


def _recording_transport() -> tuple[FakeCibSevenTransport, list[dict[str, Any]]]:
    """A `FakeCibSevenTransport` that captures the variables dict the chokepoint HANDS IT —
    the exact bytes a live `CibSevenHttpTransport` would POST to the engine."""
    recorded: list[dict[str, Any]] = []
    transport = FakeCibSevenTransport()
    original = transport.start_process_instance

    async def _recording(process_key: str, business_key: str, variables: dict[str, Any]) -> Any:
        recorded.append(dict(variables))
        return await original(process_key, business_key, variables)

    transport.start_process_instance = _recording  # type: ignore[method-assign]
    return transport, recorded


async def _start(
    transport: FakeCibSevenTransport,
    variables: dict[str, Any],
    *,
    sink: FakeStartAuditSink | None = None,
) -> FakeStartAuditSink:
    audit_sink = sink or FakeStartAuditSink()
    await start_process_idempotent(
        transport,
        process_key="SP-OP-AUTH-001",
        business_key="AUTH-amh-cc06-1",
        variables=variables,
        audit_sink=audit_sink,
        provenance=_provenance(),
    )
    return audit_sink


# ---------------------------------------------------------------------------
# 1 + 2 — free text redacted, structured values untouched
# ---------------------------------------------------------------------------


async def test_free_text_variable_is_redacted_before_it_reaches_the_transport() -> None:
    """`resumo_contexto` is the contractual SP-OP-ESCALATION-001 handoff summary (free text).
    The identifiers must be gone; the SENTENCE must survive (the human attendant reads it)."""
    transport, recorded = _recording_transport()

    await _start(
        transport,
        {
            "resumo_contexto": (
                f"Beneficiario pediu atendente humano e informou CPF {_CPF}, "
                f"e-mail {_EMAIL} e telefone {_PHONE}."
            ),
        },
    )

    assert recorded, "the process must have been started"
    resumo = recorded[0]["resumo_contexto"]
    for identifier in (_CPF, _EMAIL, _PHONE, "123.456.789", "98765-4321"):
        assert identifier not in resumo, f"engine-bound resumo_contexto leaked {identifier!r}: {resumo!r}"
    assert REDACTED_DIGITS in resumo and REDACTED_EMAIL in resumo and REDACTED_PHONE in resumo
    # The handoff is pseudonimizado, NOT destroyed (SP-OP-ESCALATION-001 §Variaveis).
    assert "Beneficiario pediu atendente humano" in resumo


async def test_dossier_narrativa_is_redacted_recursively_and_structure_survives() -> None:
    """`dossie_<agent>.narrativa` is the LLM-drafted field EVERY `_build_dossier` produces. The
    walk must reach it (and a nested `diagnostico`) while leaving the dossier's SHAPE intact."""
    transport, recorded = _recording_transport()
    dossier = {
        "prompt_version": "dossier@v1",
        "route": "human_auditor",
        "narrativa": f"Guia encaminhada; beneficiario informou CPF {_CPF}.",
        "fatos": {
            "numero_guia_tiss": "GUIA-2026-000123456789",
            "codigo_procedimento_tuss": "40808010",
            "valor_estimado_brl": 12345.67,
            "dut_atendida": True,
            "diagnostico": f"contato {_EMAIL}",
        },
        "documentos_refs": ["process://amh/doc/1", {"narrativa": f"anexo de {_PHONE}"}],
        "decisao_cobertura": None,
    }

    await _start(transport, {"dossie_rafael": dossier, "numero_guia_tiss": "GUIA-2026-000123456789"})

    sent = recorded[0]["dossie_rafael"]
    assert _CPF not in sent["narrativa"] and REDACTED_DIGITS in sent["narrativa"]
    assert _EMAIL not in sent["fatos"]["diagnostico"] and REDACTED_EMAIL in sent["fatos"]["diagnostico"]
    assert _PHONE not in sent["documentos_refs"][1]["narrativa"]
    # STRUCTURE + structured values byte-identical (the property that makes this safe on a BPMN
    # process's input contract). `numero_guia_tiss` is an 11+ digit run BY CONSTRUCTION: an
    # unscoped identifier net over every string would have destroyed the process's own key.
    assert sent["fatos"]["numero_guia_tiss"] == "GUIA-2026-000123456789"
    assert recorded[0]["numero_guia_tiss"] == "GUIA-2026-000123456789"
    assert sent["fatos"]["codigo_procedimento_tuss"] == "40808010"
    assert sent["fatos"]["valor_estimado_brl"] == 12345.67
    assert sent["fatos"]["dut_atendida"] is True
    assert sent["documentos_refs"][0] == "process://amh/doc/1"
    assert sent["prompt_version"] == "dossier@v1" and sent["route"] == "human_auditor"
    assert sent["decisao_cobertura"] is None


async def test_structured_variables_are_passed_through_untouched() -> None:
    """No key outside the free-text allowlist is rewritten — ids, business keys, enums,
    `process://` refs, pseudo-ids, DMN refs, booleans and amounts reach the engine verbatim."""
    transport, recorded = _recording_transport()
    variables: dict[str, Any] = {
        "tenant_id": "amh",
        "beneficiario_pseudo_id": "PSEUDO-TESTE-007",
        "matricula_beneficiario": "12345678901",
        "conversation_id": "wa:amh:evl-cc06",
        "numero_boleto": "34191790010104351004791020150008291070026000",
        "motivo_categoria": "solicitacao_humano",
        "severidade": "leve",
        "dmn_decision_ref": "process://amh/dmn/triage@3",
        "valor_estimado_brl": 1234.56,
        "documentacao_completa": False,
    }

    await _start(transport, dict(variables))

    assert recorded[0] == variables


async def test_scrub_does_not_mutate_the_callers_variables() -> None:
    """The caller's dict (and its nested dossier, which is the agent's own graph state) must be
    left alone — the scrub returns a COPY. A mutation here would silently rewrite agent state."""
    transport, _recorded = _recording_transport()
    dossier = {"narrativa": f"CPF {_CPF}"}
    variables = {"resumo_contexto": f"CPF {_CPF}", "dossie_lucas": dossier}

    await _start(transport, variables)

    assert variables["resumo_contexto"] == f"CPF {_CPF}"
    assert dossier["narrativa"] == f"CPF {_CPF}"


# ---------------------------------------------------------------------------
# 3 — the durable record binds what the engine actually received
# ---------------------------------------------------------------------------


async def test_input_sha256_binds_the_scrubbed_variables_the_engine_received() -> None:
    """The digest exists to bind the start inputs one-way. The only copy that outlives the call
    is the ENGINE's, so the hash must be of the scrubbed form — otherwise an auditor re-hashing
    the engine's variables mismatches on every started process and the binding is unverifiable."""
    transport, recorded = _recording_transport()
    raw = {"resumo_contexto": f"contato {_EMAIL}", "conversation_id": "wa:amh:evl-cc06"}

    sink = await _start(transport, dict(raw))

    (record, _dedup) = sink.calls[0]
    assert record.details["input_sha256"] == hash_input(recorded[0])
    assert record.details["input_sha256"] != hash_input(raw)
    # And the raw identifier is nowhere in the durable record.
    assert _EMAIL not in str(record.details)


# ---------------------------------------------------------------------------
# 4 — fail-closed: a scrub that cannot complete refuses the start
# ---------------------------------------------------------------------------


async def test_unscrubbable_variables_refuse_the_start_with_no_claim_and_no_engine_call() -> None:
    """Fail-CLOSED, never passthrough: a self-referential (unwalkable) dossier must raise
    BEFORE the durable claim is written and BEFORE the engine is touched."""
    transport, recorded = _recording_transport()
    dossier: dict[str, Any] = {"narrativa": "ok"}
    dossier["self"] = dossier  # unbounded depth
    sink = FakeStartAuditSink()

    with pytest.raises(StartVariableRedactionError):
        await start_process_idempotent(
            transport,
            process_key="SP-OP-AUTH-001",
            business_key="AUTH-amh-cc06-boom",
            variables={"dossie_rafael": dossier},
            audit_sink=sink,
            provenance=_provenance(),
        )

    assert sink.calls == [], "no durable claim may exist for a start that was refused"
    assert recorded == [], "the engine must not have been called"


def test_redaction_error_is_not_a_cibseven_error() -> None:
    """Every agent graph wraps its start call in `except CibSevenError` and degrades to an honest
    `*_started: False`. A broken PHI control is NOT a transient engine outage and must not be
    absorbed by that handler — it has to fail the turn loudly."""
    assert not issubclass(StartVariableRedactionError, CibSevenError)
    assert issubclass(StartVariableRedactionError, RuntimeError)


def test_redact_start_variables_reclassifies_any_scrub_failure() -> None:
    """The named wrapper is what the chokepoint calls; it must convert ANY internal failure into
    the typed refusal (never return the input unchanged)."""

    class _ExplodingMapping(dict[str, Any]):
        def items(self) -> Any:
            raise ValueError("boom")

    with pytest.raises(StartVariableRedactionError):
        redact_start_variables(_ExplodingMapping())

"""Regression tests for the caller-planted OUTPUT-FIELD read-through class in Rafael (T1.11).

Rafael's core L0 invariants (medico-auditor gate, `decisao_cobertura` hardcoded `None`, DMN
provenance overwrite) were already immune. The residual finding (`scratchpad/
probe_rafael_dmndown.py`):

  * R1 — the admissibility-DMN-down early return in `assess` omits `sla_analise` +
          `recomendacao_auto`, so a caller-planted value for either SURVIVES into
          `dossie_rafael.fatos` (via `_build_dossier` -> `_contract_variables`) — informative
          only, but a forged fact in the engine dossier nonetheless.

The per-graph `receive` reset (layer 1) neutralizes them at entry; the input-boundary gate
(layer 2) stops them entering the state dict at the construction seam (Rafael has no live A2A
seam yet, so the gate is enforced at the construction helper for when the seam lands).

BITE-PROOF: with the fix stashed, `test_r1_*` sees the planted tokens survive into the engine
dossier. See the PR body for the stash/run evidence.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from maezo.agents.rafael.graph import (
    _RAFAEL_NEUTRAL_OUTPUTS,
    RAFAEL_INPUT_FIELDS,
    RafaelGraph,
    RafaelState,
    gate_inbound_state,
    new_rafael_state,
)
from maezo.tools.mcp_cibseven.transport import FakeCibSevenTransport
from maezo.tools.workers.dmn_transport import FakeDmnTransport


class _RecordingCibSeven(FakeCibSevenTransport):
    def __init__(self) -> None:
        super().__init__()
        self.started: list[dict[str, Any]] = []

    async def start_process_instance(self, process_key: str, business_key: str, variables: dict[str, Any]):  # type: ignore[override]
        self.started.append({"business_key": business_key, "variables": dict(variables)})
        return await super().start_process_instance(process_key, business_key, variables)


class _FakeInference:
    async def generate(self, prompt: str, *, phi: bool = False) -> str:
        return "NARRATIVA"


_SLA_SENTINEL = "UNIQ_slaanalise_CPF=111.111.111-11"
_RECO_SENTINEL = "UNIQ_reco_AUTO_FORCED"

_BASE_INPUT: RafaelState = {
    "tenant_id": "amh",
    "numero_guia_tiss": "GUIA-1",
    "beneficiario_pseudo_id": "pseudo-123",
    "prestador_id": "prest-1",
    "canal": "a2a",
    "codigo_procedimento_tuss": "40304361",
    "categoria_procedimento": "exame_simples",
    "carater_atendimento": "eletivo",
    "valor_estimado_brl": 100.0,
    "cid10": "Z00",
    "documentos_refs": [],
    "requer_autorizacao": True,
    "documentacao_completa": True,
    "beneficiario_ativo": True,
    "carencia_cumprida": True,
    "dut_atendida": True,
    "dentro_teto_l2": True,
    "rede_credenciada": True,
}


def _dmn_admissibility_down() -> FakeDmnTransport:
    """auth_admissibility NOT registered -> raises -> DMN-down early return path (R1)."""
    dmn = FakeDmnTransport()
    dmn.register("auth_sla", [{"sla_analise": "5d", "sla_alerta": "none"}], version=2)
    dmn.register("auth_auto_approval", [{"recomendacao": "AUTO_APROVAR"}], version=2)
    return dmn


def _compiled(cib: _RecordingCibSeven, dmn: FakeDmnTransport):
    return RafaelGraph(inference=_FakeInference(), dmn=dmn, cibseven=cib).compile_graph().compile()


async def test_r1_planted_sla_and_recomendacao_never_reach_engine_dossier() -> None:
    cib = _RecordingCibSeven()
    compiled = _compiled(cib, _dmn_admissibility_down())

    result = await compiled.ainvoke(
        {**_BASE_INPUT, "sla_analise": _SLA_SENTINEL, "recomendacao_auto": _RECO_SENTINEL}
    )

    # DMN-down fail-safes to the human auditor; the invariant decision stays None.
    assert result["route"] == "human_auditor"
    assert (result.get("dossier") or {}).get("decisao_cobertura") is None
    assert cib.started, "SP-OP-AUTH-001 must be started"
    blob = json.dumps(cib.started[0]["variables"], ensure_ascii=False, default=str)
    assert _SLA_SENTINEL not in blob, "planted sla_analise leaked into engine dossier"
    assert _RECO_SENTINEL not in blob, "planted recomendacao_auto leaked into engine dossier"
    fatos = cib.started[0]["variables"]["dossie_rafael"]["fatos"]
    assert fatos["sla_analise"] == ""  # neutral default, not the planted value
    assert fatos["recomendacao_auto"] is None


# ---------------------------------------------------------------------------
# Input-boundary gate (layer 2) + field-split completeness
# ---------------------------------------------------------------------------


def test_input_output_field_split_partitions_every_state_key() -> None:
    all_fields = RAFAEL_INPUT_FIELDS | frozenset(_RAFAEL_NEUTRAL_OUTPUTS)
    assert all_fields == frozenset(RafaelState.__annotations__)
    assert RAFAEL_INPUT_FIELDS.isdisjoint(frozenset(_RAFAEL_NEUTRAL_OUTPUTS))


def test_new_rafael_state_yields_only_present_input_fields() -> None:
    state = new_rafael_state(dict(_BASE_INPUT))
    assert frozenset(state) <= RAFAEL_INPUT_FIELDS
    assert frozenset(state) == frozenset(_BASE_INPUT)


def test_new_rafael_state_rejects_planted_output_fields_fail_closed() -> None:
    """A strict internal delegation seam: a planted output key is a producer bug -> raise."""
    with pytest.raises(ValueError, match="non-input keys"):
        new_rafael_state({**_BASE_INPUT, "route": "auto_approve", "sla_analise": _SLA_SENTINEL})


def test_gate_inbound_state_drops_planted_output_fields() -> None:
    gated = gate_inbound_state(
        {**_BASE_INPUT, "route": "auto_approve", "recomendacao_auto": _RECO_SENTINEL, "dossier": {}}
    )
    assert frozenset(gated) <= RAFAEL_INPUT_FIELDS
    for planted in ("route", "recomendacao_auto", "dossier"):
        assert planted not in gated

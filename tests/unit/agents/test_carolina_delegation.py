"""Unit tests for `agents.carolina.delegation` — the CRED dossier A2A edge (DL-0033 real wiring).

Mirrors `tests/unit/agents/test_rafael_delegation.py`: engine-free, PG-free —
`make_carolina_handler` runs the REAL `carolina.graph.build(config)` graph against fakes
(`FakeDmnTransport`/`FakeCibSevenTransport`/`FakeStartAuditSink`/an in-file fake inference), no
I/O. The live-Postgres idempotent-replay proof for this edge is DEFERRED to the PR CI lane
(mirroring `test_a2a_edge_live_pg.py`) — no docker in this build environment.
"""

from __future__ import annotations

from typing import Any

import pytest

from maezo.agents.carolina.delegation import (
    ORIGIN_WORKER,
    TARGET_AGENT,
    TASK_TYPE_CRED_DOSSIER,
    build_cred_dossier_envelope,
    cred_task_id,
    make_carolina_handler,
    state_from_envelope,
)
from maezo.agents.carolina.graph import _CALLER_INPUT_FIELDS, build
from maezo.tools.mcp_cibseven.transport import FakeCibSevenTransport
from maezo.tools.workers.dmn_transport import FakeDmnTransport
from tests.support.audit_fakes import FakeStartAuditSink

_CASE_META = {
    "direcao": "descredenciamento",
    "tipo_prestador": "clinica",
    "origem_solicitacao": "operadora",
    "data_solicitacao_iso": "2026-07-01",
    "regiao_saude": "SP-01",
    "especialidade": "cardiologia",
    "licenca_valida": True,
    "documentacao_completa": True,
    "dentro_criterios_rede": False,
    "notificacao_previa_feita": False,
    "substituto_equivalente_identificado": False,
    "tem_beneficiarios_vinculados": True,
    "indicio_irregularidade_sinalizado": False,
    # Free text / refs — must NEVER reach payload_meta (strict allowlist):
    "motivo_informado": "texto livre potencialmente sensivel",
    "documentos_refs": [{"ref": "doc://1"}],
}


class _FakeInference:
    def __init__(self, responses: list[str] | None = None) -> None:
        self._responses = list(responses) if responses else ["dossie factual sintetico"]
        self.calls: list[tuple[str, bool]] = []

    async def generate(
        self,
        prompt: str,
        *,
        phi: bool = False,
        agent_id: str | None = None,
        tenant_id: str | None = None,
        task_kind: str | None = None,
    ) -> str:
        self.calls.append((prompt, phi))
        assert phi is True, "Carolina's dossier LLM call must be phi=True (security_zone: phi)"
        return self._responses.pop(0) if self._responses else ""


def _envelope(*, prestador_id: str = "P-EDGE-1", protocolo: str | None = None) -> Any:
    return build_cred_dossier_envelope(
        tenant="amh",
        prestador_id=prestador_id,
        case_meta=_CASE_META,
        protocolo_cred=protocolo,
    )


def _dmn(
    *,
    admissibilidade: str = "SEGUE_ANALISE",
    roteamento: str = "ANALISE_DESCREDENCIAMENTO",
) -> FakeDmnTransport:
    dmn = FakeDmnTransport()
    dmn.register("cred_admissibility", [{"roteamento": admissibilidade}])
    dmn.register("cred_route", [{"roteamento": roteamento}])
    dmn.register("cred_sla", [{"sla_analise": "P10D", "sla_alerta": "P7D", "fonte_regulatoria": "RN 566"}])
    dmn.register(
        "cred_prior_notice",
        [
            {
                "exige_notificacao_previa": True,
                "exige_substituto_equivalente": True,
                "prazo_notificacao": "P30D",
            }
        ],
    )
    return dmn


# --- task_id / envelope builder ---------------------------------------------------------------


def test_cred_task_id_is_the_process_business_key() -> None:
    assert cred_task_id("amh", "P-1") == "CRED-amh-P-1"
    assert cred_task_id("amh", "P-1", "PROTO-9") == "CRED-amh-P-1-PROTO-9"


def test_envelope_contract_matches_carolina_card() -> None:
    envelope = _envelope(protocolo="PROTO-9")
    assert envelope.task_type == TASK_TYPE_CRED_DOSSIER == "credentialing.analyze"
    assert envelope.origin == ORIGIN_WORKER == "credenciamento-worker"
    assert envelope.target == TARGET_AGENT == "carolina"
    assert envelope.task_id == "CRED-amh-P-EDGE-1-PROTO-9"
    assert envelope.payload_ref == "process://CRED-amh-P-EDGE-1-PROTO-9"
    assert envelope.delegation_chain == ("credenciamento-worker", "carolina")


def test_payload_meta_is_a_strict_non_phi_allowlist() -> None:
    """Bounded strings + "true"/"false" booleans only; free text (`motivo_informado`) and
    `documentos_refs` are structurally excluded (allowlist, not blocklist — ADR-0006)."""
    meta = dict(_envelope().payload_meta)
    assert meta["prestador_id"] == "P-EDGE-1"
    assert meta["direcao"] == "descredenciamento"
    assert meta["dentro_criterios_rede"] == "false"
    assert meta["tem_beneficiarios_vinculados"] == "true"
    assert "motivo_informado" not in meta
    assert "documentos_refs" not in meta
    assert all(isinstance(v, str) for v in meta.values())


# --- state_from_envelope ----------------------------------------------------------------------


def test_state_from_envelope_maps_meta_into_carolina_input_fields_only() -> None:
    state = state_from_envelope(_envelope(protocolo="PROTO-9"))

    assert set(state) <= _CALLER_INPUT_FIELDS
    assert state["tenant_id"] == "amh"
    assert state["prestador_id"] == "P-EDGE-1"
    assert state["protocolo_cred"] == "PROTO-9"
    assert state["canal"] == "a2a"
    assert state["direcao"] == "descredenciamento"
    assert state["dentro_criterios_rede"] is False
    assert state["tem_beneficiarios_vinculados"] is True


def test_state_from_envelope_fails_closed_without_prestador_id() -> None:
    """Carolina's `start_process` has no degenerate-key short-circuit — a blank prestador is a
    producer bug and must raise loudly here, never build a `CRED-{tenant}-` key."""
    envelope = build_cred_dossier_envelope(tenant="amh", prestador_id="P-1", case_meta={})
    object.__setattr__(envelope, "payload_meta", {})  # simulate a foreign, malformed producer
    with pytest.raises(ValueError, match="prestador_id"):
        state_from_envelope(envelope)


# --- make_carolina_handler --------------------------------------------------------------------


async def test_handler_returns_process_business_key_output_ref_and_bounded_meta() -> None:
    handler = make_carolina_handler(
        _FakeInference(),
        dmn=_dmn(),
        cibseven=FakeCibSevenTransport(),
        audit_sink=FakeStartAuditSink(),
    )
    output = await handler(_envelope())

    assert output.output_ref == "process://CRED-amh-P-EDGE-1"
    assert output.meta["route"] == "human_review"
    assert output.meta["desfecho"] == "analise_descredenciamento"
    assert output.meta["grupo_destino"] == "juridico-rede"
    assert output.meta["process_started"] == "True"


async def test_handler_clerical_direction_also_returns_reference_never_a_decision() -> None:
    clerical_meta = {**_CASE_META, "direcao": "credenciamento", "dentro_criterios_rede": True}
    envelope = build_cred_dossier_envelope(tenant="amh", prestador_id="P-CLERICAL", case_meta=clerical_meta)
    handler = make_carolina_handler(
        _FakeInference(),
        dmn=_dmn(admissibilidade="CLERICAL_CREDENCIAR"),
        cibseven=FakeCibSevenTransport(),
        audit_sink=FakeStartAuditSink(),
    )
    output = await handler(envelope)

    assert output.output_ref == "process://CRED-amh-P-CLERICAL"
    assert output.meta["route"] == "auto_route"
    # GUARDRAIL: no field of HandlerOutput ever mentions an adverse decision.
    assert "decisao_credenciamento" not in output.meta
    assert "decisao_descredenciamento" not in output.meta
    assert all("decisao" not in str(v).lower() for v in output.meta.values())


async def test_handler_never_forwards_the_dossier_and_adverse_decisions_stay_none() -> None:
    """The L1-hard structural guardrail (`graph.py::_build_dossier`): re-run the SAME state
    directly through `build(config)` to inspect the full result — the handler itself never even
    forwards the dossier, but this proves the deeper graph invariant it rests on."""
    dmn = _dmn()
    cibseven = FakeCibSevenTransport()
    audit_sink = FakeStartAuditSink()
    state = state_from_envelope(_envelope(prestador_id="P-GUARDRAIL"))

    compiled = build(
        {"inference": _FakeInference(), "dmn": dmn, "cibseven": cibseven, "audit_sink": audit_sink}
    ).compile()
    result: dict[str, Any] = await compiled.ainvoke(state)

    assert result["dossier"]["decisao_credenciamento"] is None
    assert result["dossier"]["decisao_descredenciamento"] is None

    handler = make_carolina_handler(_FakeInference(), dmn=dmn, cibseven=cibseven, audit_sink=audit_sink)
    output = await handler(_envelope(prestador_id="P-GUARDRAIL-2"))
    assert "dossier" not in output.meta
    assert "narrativa" not in output.meta

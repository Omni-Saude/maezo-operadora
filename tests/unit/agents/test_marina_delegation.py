"""Unit tests for `agents.marina.delegation` — the inbound `glosa.analyze`/`recurso.analyze`/
`reembolso.analyze` edge (CC-02 / RAF-11).

Mirrors `tests/unit/agents/test_fernando_delegation.py` exactly (same shape: engine-free, PG-free
— `make_marina_handler` runs the REAL `marina.graph.build(config)` graph against fakes, no I/O).
The origin side (`delegate_marina_analysis`) is NOT called from
`tools/workers/{contas,recurso,reembolso}.py` (see `agents/marina/delegation.py`'s module
docstring — that call site is an owner decision outside this work package); these tests cover the
TARGET side end to end plus the origin helpers' own contract in isolation.
"""

from __future__ import annotations

from typing import Any

import pytest

from maezo.agents.marina.delegation import (
    ORIGIN_CONTAS_WORKER,
    ORIGIN_RECURSO_WORKER,
    ORIGIN_REEMBOLSO_WORKER,
    TARGET_AGENT,
    TASK_TYPE_GLOSA_ANALYSIS,
    TASK_TYPE_RECURSO_ANALYSIS,
    TASK_TYPE_REEMBOLSO_ANALYSIS,
    TASK_TYPES,
    build_marina_analysis_envelope,
    make_marina_handler,
    marina_task_id,
    state_from_envelope,
)
from maezo.agents.marina.graph import _CALLER_INPUT_FIELDS, _business_key, build
from maezo.tools.mcp_cibseven.transport import FakeCibSevenTransport
from maezo.tools.workers.dmn_transport import FakeDmnTransport
from tests.support.audit_fakes import FakeStartAuditSink

# A clinical fact (`cid10`) and a free-text narrative are planted in EVERY case_meta below: the
# allowlist must drop both, at BOTH ends of the seam (ADR-0006).
_PHI_PLANTS: dict[str, Any] = {
    "cid10": "C50",
    "narrativa": "paciente relata dor no membro superior esquerdo",
    "motivo_informado": "texto livre do prestador",
}

_CASE_META_CONTAS: dict[str, Any] = {
    "beneficiario_pseudo_id": "pseudo-123",
    "prestador_id": "prestador-1",
    "patient_summary_ref": "Patient/pseudo-123",
    "numero_lote_tiss": "LOTE-001",
    "competencia": "2026-07",
    "data_recebimento_lote": "2026-08-01",
    "tipo_lote": "consulta",
    "valor_apresentado_brl": 1200.5,
    "denial_ratio": 0.1,
    "divergencia_valor": False,
    "item_conforme_tabela": True,
    "documentacao_anexa": True,
    "indicio_fraude_sinalizado": False,
    # list-shaped: only the PRINCIPAL code rides the seam (module docstring's disclosed residual)
    "reason_codes_tiss": ["1707", "1708"],
    "linhas_conta_refs": [{"ref": "linha://1"}],
    **_PHI_PLANTS,
}

_CASE_META_RECURSO: dict[str, Any] = {
    "beneficiario_pseudo_id": "pseudo-123",
    "prestador_id": "prestador-1",
    "numero_guia_tiss": "GUIA-001",
    "glosa_id": "GL-1",
    "glosa_type": "administrativa",
    "glosa_reason_code": "1707",
    "codigo_procedimento_tuss": "10101012",
    "data_recebimento_recurso_iso": "2026-08-05",
    "valor_glosado_brl": 300.0,
    "glosa_existe": True,
    "dentro_prazo_recurso": True,
    "documentacao_recurso_completa": True,
    "documentos_recurso_refs": [{"ref": "doc://1"}],
    **_PHI_PLANTS,
}

_CASE_META_REEMBOLSO: dict[str, Any] = {
    "beneficiario_pseudo_id": "pseudo-123",
    "protocolo_reembolso": "REEMB-P1",
    "tipo_reembolso": "livre_escolha",
    "categoria_procedimento": "consulta",
    "valor_solicitado_cents": 25000,
    "valor_calculado_tabela_cents": 20000,
    "cobertura_prevista": True,
    "documentacao_completa": True,
    "dentro_prazo": True,
    "beneficiario_ativo": True,
    "carencia_cumprida": True,
    "dentro_tabela": True,
    "dentro_teto_l2": True,
    "requer_avaliacao_clinica": False,
    **_PHI_PLANTS,
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
        # CC-12 x integracao lote3 (LOTE3-INTEGRATION-FAKES-TASK-KIND): assinatura acompanha o
        # Protocol real (`runtime/inference::InferenceProvider.generate`), mesma especie do
        # defeito f1bc87f.
        task_kind: str | None = None,
    ) -> str:
        self.calls.append((prompt, phi))
        return self._responses.pop(0) if self._responses else ""


def _envelope(task_type: str = TASK_TYPE_GLOSA_ANALYSIS, case_meta: dict[str, Any] | None = None) -> Any:
    meta = (
        case_meta
        or {
            TASK_TYPE_GLOSA_ANALYSIS: _CASE_META_CONTAS,
            TASK_TYPE_RECURSO_ANALYSIS: _CASE_META_RECURSO,
            TASK_TYPE_REEMBOLSO_ANALYSIS: _CASE_META_REEMBOLSO,
        }[task_type]
    )
    return build_marina_analysis_envelope(tenant="amh", task_type=task_type, case_meta=meta)


def _dmn_contas(*, roteamento: str = "PAGAR") -> FakeDmnTransport:
    dmn = FakeDmnTransport()
    dmn.register("glosa_reason_normalization", [{"categoria_normalizada": "administrativa"}])
    dmn.register("glosa_classification", [{"glosa_type": "valor", "glosa_extent": "linha"}])
    dmn.register("contas_sla", [{"sla_analise": "P30D", "sla_alerta": "P20D", "fonte_regulatoria": "RN 424"}])
    dmn.register("glosa_triage", [{"roteamento": roteamento, "motivo": "test"}])
    return dmn


def _dmn_recurso() -> FakeDmnTransport:
    dmn = FakeDmnTransport()
    dmn.register("recurso_admissibility", [{"roteamento": "SEGUE_ANALISE", "motivo": "test"}])
    dmn.register(
        "recurso_eligibility",
        [{"roteamento": "SEGUE_MERITO", "grupo_revisor": "analista-recurso-glosa", "motivo": "test"}],
    )
    dmn.register("recurso_sla", [{"sla_analise": "P10D", "sla_alerta": "P6D", "prazo_regulatorio": "P30D"}])
    return dmn


def _handler(dmn: FakeDmnTransport, *, cibseven: Any = None) -> Any:
    return make_marina_handler(
        _FakeInference(),
        dmn=dmn,
        cibseven=cibseven or FakeCibSevenTransport(),
        audit_sink=FakeStartAuditSink(),
    )


# --- task types / task_id / envelope builder ----------------------------------------------------


def test_task_types_match_the_agent_card_contract() -> None:
    assert {"glosa.analyze", "recurso.analyze", "reembolso.analyze"} == TASK_TYPES


def test_task_id_is_the_flow_business_key_for_all_three_flows() -> None:
    """MUTATION PROBE (dispatcher Guard 4): a `task_id` that is not the graph's own business key
    breaks idempotent replay — the same case would run Marina twice under two keys."""
    assert marina_task_id("amh", "contas", {"numero_lote_tiss": "LOTE-001"}) == "CONTAS-amh-LOTE-001"
    assert (
        marina_task_id("amh", "recurso", {"numero_guia_tiss": "GUIA-001", "glosa_id": "GL-1"})
        == "RECURSO-amh-GUIA-001-GL-1"
    )
    assert marina_task_id("amh", "reembolso", {"protocolo_reembolso": "REEMB-P1"}) == "REEMB-amh-REEMB-P1"


@pytest.mark.parametrize(
    ("task_type", "origin", "task_id"),
    [
        (TASK_TYPE_GLOSA_ANALYSIS, ORIGIN_CONTAS_WORKER, "CONTAS-amh-LOTE-001"),
        (TASK_TYPE_RECURSO_ANALYSIS, ORIGIN_RECURSO_WORKER, "RECURSO-amh-GUIA-001-GL-1"),
        (TASK_TYPE_REEMBOLSO_ANALYSIS, ORIGIN_REEMBOLSO_WORKER, "REEMB-amh-REEMB-P1"),
    ],
)
def test_envelope_contract_matches_marina_card(task_type: str, origin: str, task_id: str) -> None:
    envelope = _envelope(task_type)
    assert envelope.task_type == task_type
    assert envelope.origin == origin
    assert envelope.target == TARGET_AGENT == "marina"
    assert envelope.task_id == task_id
    assert envelope.payload_ref == f"process://{task_id}"
    assert envelope.delegation_chain == (origin, "marina")


def test_envelope_task_id_equals_the_graphs_own_business_key() -> None:
    """The two derivations must be the SAME function, not two that happen to agree today."""
    for task_type in sorted(TASK_TYPES):
        envelope = _envelope(task_type)
        assert envelope.task_id == _business_key(state_from_envelope(envelope))


def test_unknown_task_type_fails_closed_at_the_envelope_builder() -> None:
    with pytest.raises(ValueError, match="accepted_task_types"):
        build_marina_analysis_envelope(tenant="amh", task_type="glosa.decide", case_meta=_CASE_META_CONTAS)


# --- payload_meta: strict non-PHI allowlist -----------------------------------------------------


@pytest.mark.parametrize("task_type", sorted(TASK_TYPES))
def test_payload_meta_never_carries_clinical_facts_or_free_text(task_type: str) -> None:
    """MUTATION PROBE (ADR-0006): adding `cid10`/`narrativa`/`motivo_informado` to any
    `_..._META_KEYS` tuple turns this RED. Allowlist, not blocklist — the plants are present in
    every `case_meta` above and must be dropped by omission."""
    meta = dict(_envelope(task_type).payload_meta)
    for forbidden in _PHI_PLANTS:
        assert forbidden not in meta
    assert all(isinstance(value, str) for value in meta.values())


def test_payload_meta_excludes_the_list_shaped_facts() -> None:
    meta = dict(_envelope(TASK_TYPE_GLOSA_ANALYSIS).payload_meta)
    assert "linhas_conta_refs" not in meta
    assert "reason_codes_tiss" not in meta
    # ...but the PRINCIPAL reason code DOES ride, as a scalar (disclosed reduction).
    assert meta["reason_code_tiss"] == "1707"
    recurso_meta = dict(_envelope(TASK_TYPE_RECURSO_ANALYSIS).payload_meta)
    assert "documentos_recurso_refs" not in recurso_meta


def test_payload_meta_serializes_bounded_scalars() -> None:
    meta = dict(_envelope(TASK_TYPE_GLOSA_ANALYSIS).payload_meta)
    assert meta["valor_apresentado_brl"] == "1200.5"
    assert meta["item_conforme_tabela"] == "true"
    assert meta["divergencia_valor"] == "false"
    reembolso_meta = dict(_envelope(TASK_TYPE_REEMBOLSO_ANALYSIS).payload_meta)
    assert reembolso_meta["valor_solicitado_cents"] == "25000"


# --- state_from_envelope ------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("task_type", "flow"),
    [
        (TASK_TYPE_GLOSA_ANALYSIS, "contas"),
        (TASK_TYPE_RECURSO_ANALYSIS, "recurso"),
        (TASK_TYPE_REEMBOLSO_ANALYSIS, "reembolso"),
    ],
)
def test_state_from_envelope_sets_the_flow_from_the_task_type(task_type: str, flow: str) -> None:
    """Never left to `graph._flow`'s missing-key default (`contas`) — which PROCESS a delegated
    turn starts must not be decided by an omission."""
    state = state_from_envelope(_envelope(task_type))
    assert state["flow"] == flow
    assert state["tenant_id"] == "amh"
    assert state["canal"] == "a2a"
    assert set(state) <= _CALLER_INPUT_FIELDS


@pytest.mark.parametrize("task_type", sorted(TASK_TYPES))
def test_state_from_envelope_never_seeds_a_clinical_fact(task_type: str) -> None:
    state = state_from_envelope(_envelope(task_type))
    assert "cid10" not in state
    assert "narrativa" not in state


def test_state_from_envelope_reexpands_only_the_principal_reason_code() -> None:
    state = state_from_envelope(_envelope(TASK_TYPE_GLOSA_ANALYSIS))
    assert state["reason_codes_tiss"] == ["1707"]


def test_state_from_envelope_coerces_meta_strings_back_to_scalars() -> None:
    state = state_from_envelope(_envelope(TASK_TYPE_GLOSA_ANALYSIS))
    assert state["valor_apresentado_brl"] == 1200.5
    assert state["item_conforme_tabela"] is True
    assert state["divergencia_valor"] is False
    reembolso = state_from_envelope(_envelope(TASK_TYPE_REEMBOLSO_ANALYSIS))
    assert reembolso["valor_solicitado_cents"] == 25000


@pytest.mark.parametrize(
    ("task_type", "case_meta"),
    [
        (TASK_TYPE_GLOSA_ANALYSIS, {"prestador_id": "p"}),
        (TASK_TYPE_RECURSO_ANALYSIS, {"numero_guia_tiss": "GUIA-001"}),  # glosa_id missing
        (TASK_TYPE_REEMBOLSO_ANALYSIS, {"beneficiario_pseudo_id": "pseudo-1"}),
    ],
)
def test_state_from_envelope_fails_closed_without_the_flow_identity(
    task_type: str, case_meta: dict[str, Any]
) -> None:
    envelope = _envelope(task_type)
    object.__setattr__(envelope, "payload_meta", case_meta)  # a foreign, malformed producer
    with pytest.raises(ValueError, match="business-key identity"):
        state_from_envelope(envelope)


def test_state_from_envelope_rejects_a_contas_guia_without_its_conta() -> None:
    """`contas` adjudicated by guia needs the CONTA too — `graph._business_key` composes the
    guia form ONLY as `CONTAS-{tenant}-{guia}-{conta}`. A guia WITHOUT a conta falls through to
    the lote form with an EMPTY lote, so the key degenerates to `CONTAS-{tenant}-` and every such
    case shares one `task_id`."""
    envelope = _envelope(TASK_TYPE_GLOSA_ANALYSIS)
    object.__setattr__(envelope, "payload_meta", {"numero_guia_tiss": "GUIA-001"})
    with pytest.raises(ValueError, match="business-key identity"):
        state_from_envelope(envelope)


def test_contas_guias_without_a_conta_collide_and_the_seam_refuses_them() -> None:
    """MUTATION PROBE (dispatcher Guard 4, DURABLE): relax the guard back to `lote OR guia` and
    this test dies. Two DIFFERENT guias with no conta derive the SAME degenerate `task_id`, so
    the dispatcher would treat the second case as an idempotent REPLAY of the first and hand back
    the first case's result — `output_ref` anchored to a process that is not this case's. The
    seam must refuse the ambiguous form; the guia+conta form it DOES accept is collision-free."""
    degenerate = [
        build_marina_analysis_envelope(
            tenant="amh", task_type=TASK_TYPE_GLOSA_ANALYSIS, case_meta={"numero_guia_tiss": guia}
        )
        for guia in ("GUIA-001", "GUIA-002")
    ]
    assert degenerate[0].task_id == degenerate[1].task_id == "CONTAS-amh-"
    for envelope in degenerate:
        with pytest.raises(ValueError, match="business-key identity"):
            state_from_envelope(envelope)

    accepted = [
        build_marina_analysis_envelope(
            tenant="amh",
            task_type=TASK_TYPE_GLOSA_ANALYSIS,
            case_meta={"numero_guia_tiss": guia, "numero_conta": "CT-1"},
        )
        for guia in ("GUIA-001", "GUIA-002")
    ]
    assert accepted[0].task_id == "CONTAS-amh-GUIA-001-CT-1"
    assert accepted[1].task_id == "CONTAS-amh-GUIA-002-CT-1"
    assert accepted[0].task_id != accepted[1].task_id
    for envelope in accepted:
        assert _business_key(state_from_envelope(envelope)) == envelope.task_id


def test_state_from_envelope_rejects_a_task_type_outside_the_card() -> None:
    envelope = _envelope(TASK_TYPE_GLOSA_ANALYSIS)
    object.__setattr__(envelope, "task_type", "glosa.decide")
    with pytest.raises(ValueError, match="accepted_task_types"):
        state_from_envelope(envelope)


def test_state_from_envelope_drops_a_planted_output_field() -> None:
    """A caller planting `route`/`triagem`/`dossier` in `payload_meta` cannot reach the state:
    the keys are never READ (allowlist by construction), and `new_marina_state` would raise if
    they somehow were."""
    envelope = _envelope(TASK_TYPE_GLOSA_ANALYSIS)
    planted = {**dict(envelope.payload_meta), "route": "auto_route", "triagem": "PAGAR", "dossier": "x"}
    object.__setattr__(envelope, "payload_meta", planted)
    state = state_from_envelope(envelope)
    assert "route" not in state
    assert "triagem" not in state
    assert "dossier" not in state


# --- make_marina_handler ------------------------------------------------------------------------


async def test_handler_contas_auto_routes_and_starts_the_process() -> None:
    output = await _handler(_dmn_contas())(_envelope(TASK_TYPE_GLOSA_ANALYSIS))

    assert output.output_ref == "process://CONTAS-amh-LOTE-001"
    assert output.meta["flow"] == "contas"
    assert output.meta["route"] == "auto_route"
    assert output.meta["desfecho"] == "pagar_integral"
    assert output.meta["process_started"] == "True"


async def test_handler_contas_dmn_unavailable_fails_closed_to_human_review() -> None:
    output = await _handler(FakeDmnTransport())(_envelope(TASK_TYPE_GLOSA_ANALYSIS))

    assert output.meta["route"] == "human_review"
    assert output.meta["motivo_humano"] == "dmn_indisponivel"


async def test_handler_recurso_routes_and_starts_the_recurso_process() -> None:
    output = await _handler(_dmn_recurso())(_envelope(TASK_TYPE_RECURSO_ANALYSIS))

    assert output.output_ref == "process://RECURSO-amh-GUIA-001-GL-1"
    assert output.meta["flow"] == "recurso"
    assert output.meta["process_started"] == "True"


async def test_handler_reembolso_never_starts_a_second_instance() -> None:
    """CC-13's honesty check: Marina's `reembolso` `start_process` is a NO-OP (the instance is
    already running), so the handler reports `process_started=False` and ALWAYS human review.
    The `output_ref` is an ANCHOR, not proof that anything started."""
    cibseven = FakeCibSevenTransport()
    output = await _handler(FakeDmnTransport(), cibseven=cibseven)(_envelope(TASK_TYPE_REEMBOLSO_ANALYSIS))

    assert output.output_ref == "process://REEMB-amh-REEMB-P1"
    assert output.meta["flow"] == "reembolso"
    assert output.meta["route"] == "human_review"
    assert output.meta["process_started"] == "False"


@pytest.mark.parametrize("task_type", sorted(TASK_TYPES))
async def test_handler_never_forwards_the_dossier_content(task_type: str) -> None:
    """Mirrors Carolina's structural guardrail: `dossie_marina` stays in Marina's own engine
    variables; `HandlerOutput.meta` carries bounded class tokens only."""
    dmn = _dmn_contas() if task_type == TASK_TYPE_GLOSA_ANALYSIS else _dmn_recurso()
    output = await _handler(dmn)(_envelope(task_type))

    # A CLOSED key set — adding `dossier`/`summary_facts`/`fatos` to the handler turns this RED.
    assert set(output.meta) == {
        "flow",
        "route",
        "desfecho",
        "motivo_humano",
        "grupo_destino",
        "process_started",
    }
    # Bounded class tokens only. `desfecho` legitimately CONTAINS the substring "dossie"
    # (`reembolso_dossie_humano` is one of Marina's own outcome tokens), which is exactly why
    # this is a length/shape bound and not a substring ban.
    assert all(isinstance(v, str) and len(v) <= 40 for v in output.meta.values())


# --- consistency with `build(config)`'s own fail-closed contract --------------------------------


async def test_handler_uses_the_real_fail_closed_build_contract() -> None:
    """`make_marina_handler` must go through the SAME `build(config)` every other caller uses —
    proven by reusing `build` directly against the SAME envelope-materialized state."""
    state = state_from_envelope(_envelope(TASK_TYPE_GLOSA_ANALYSIS))
    direct = build(
        {
            "inference": _FakeInference(),
            "dmn": _dmn_contas(),
            "cibseven": FakeCibSevenTransport(),
            "audit_sink": FakeStartAuditSink(),
        }
    ).compile()
    direct_result: dict[str, Any] = await direct.ainvoke(state)

    handler_output = await _handler(_dmn_contas())(_envelope(TASK_TYPE_GLOSA_ANALYSIS))

    assert direct_result["route"] == "auto_route"
    assert handler_output.meta["route"] == "auto_route"

"""R-101 / F-139 — CONTAS: compositor UNICO da business key e dedup EXCLUSIVE (dark) de Marina.

Contrato: `docs/processes/contracts/SP-OP-CONTAS-001.md` §"Business key (idempotencia)" —
`CONTAS-{tenant_id}-{numero_lote_tiss}` ou, na adjudicacao por guia,
`CONTAS-{tenant_id}-{numero_guia_tiss}-{numero_conta}`; "reenvio do mesmo lote/conta retorna a
instancia ativa". DL-0046 fixa o mapa de postura de dedup de start (`_START_DEDUP_POLICY`) e deixa
`SP-OP-CONTAS-001` como "Human call": a promocao a `EXCLUSIVE` aqui e DARK (flag default off).

O que estes testes fixam (e o que cada mutacao derruba):
  * o compositor recusa a chave degenerada `CONTAS-{tenant}-` (guia sem conta e sem lote) em vez
    de produzi-la — antes ela era compartilhada por TODOS os casos assim;
  * Marina (`graph._business_key`, `receive`, `delegation`) e o worker `emitir_demonstrativo`
    usam o MESMO compositor — e nenhuma f-string `CONTAS-` sobra em `src/`;
  * a flag `contas_start_dedup_exclusive` desligada mantem o comportamento NON_STRICT do mapa;
    ligada, o start de CONTAS passa pelo portao EXCLUSIVE (claim orfao -> recusa ALTA, zero
    starts), e SO para CONTAS (RECURSO nunca e promovido; PAGTO nunca e enfraquecido).
Fixtures sinteticas; nenhum PHI.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

import pytest

from maezo.agents.marina.delegation import (
    TASK_TYPE_GLOSA_ANALYSIS,
    build_marina_analysis_envelope,
    make_marina_handler,
    marina_task_id,
    state_from_envelope,
)
from maezo.agents.marina.graph import MarinaGraph, MarinaState, _business_key, build
from maezo.tools.mcp_cibseven import transport as transport_mod
from maezo.tools.mcp_cibseven.transport import (
    FakeCibSevenTransport,
    StartClaimWithoutInstanceError,
    StartDedupPosture,
    start_dedup_posture,
)
from maezo.tools.process_business_keys import BusinessKeyComponentError, contas_business_key
from maezo.tools.workers.contas import DemonstrativoInput, _mint_protocolo_demonstrativo, emitir_demonstrativo
from maezo.tools.workers.dmn_transport import FakeDmnTransport
from tests.support.audit_fakes import FakeStartAuditSink

_SRC = Path(__file__).resolve().parents[3] / "src" / "maezo"


class _FakeInference:
    async def generate(
        self,
        prompt: str,
        *,
        phi: bool = False,
        agent_id: str | None = None,
        tenant_id: str | None = None,
        task_kind: str | None = None,
    ) -> str:
        return "dossie factual sintetico"


def _dmn_contas() -> FakeDmnTransport:
    dmn = FakeDmnTransport()
    dmn.register("glosa_reason_normalization", [{"categoria_normalizada": "administrativa"}])
    dmn.register("glosa_classification", [{"glosa_type": "valor", "glosa_extent": "linha"}])
    dmn.register("contas_sla", [{"sla_analise": "P30D", "sla_alerta": "P20D", "fonte_regulatoria": "RN 424"}])
    dmn.register("glosa_triage", [{"roteamento": "PAGAR", "motivo": "test"}])
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


def _contas_state(**overrides: Any) -> MarinaState:
    state: dict[str, Any] = {
        "flow": "contas",
        "tenant_id": "amh",
        "canal": "a2a",
        "beneficiario_pseudo_id": "pseudo-123",
        "prestador_id": "prestador-1",
        "numero_lote_tiss": "LOTE-001",
        "competencia": "2026-06",
        "valor_apresentado_brl": 1500.0,
        "tipo_lote": "sadt",
        "linhas_conta_refs": [{"ref": "linha-1"}],
        "reason_codes_tiss": ["VALOR_ACIMA_TABELA"],
        "divergencia_valor": False,
        "item_conforme_tabela": True,
        "documentacao_anexa": True,
        "denial_ratio": 0.1,
        "indicio_fraude_sinalizado": False,
    }
    state.update(overrides)
    return state  # type: ignore[return-value]


def _recurso_state() -> MarinaState:
    state: dict[str, Any] = {
        "flow": "recurso",
        "tenant_id": "amh",
        "canal": "a2a",
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
    }
    return state  # type: ignore[return-value]


@pytest.fixture(autouse=True)
def _fast_gate(monkeypatch: pytest.MonkeyPatch) -> None:
    """The EXCLUSIVE gate re-polls the engine before declaring an orphan claim; keep it instant."""
    monkeypatch.setattr(transport_mod, "_STRICT_GATE_RESOLVE_DELAY_S", 0.0)


# --- the single composer (contract §Business key) -----------------------------------------------


def test_marina_dedup_composer_lote_form() -> None:
    assert contas_business_key(tenant_id="amh", numero_lote_tiss="LOTE-001") == "CONTAS-amh-LOTE-001"


def test_marina_dedup_composer_guia_conta_form_wins_when_both_present() -> None:
    key = contas_business_key(
        tenant_id="amh", numero_lote_tiss="LOTE-001", numero_guia_tiss="GUIA-42", numero_conta="CONTA-7"
    )
    assert key == "CONTAS-amh-GUIA-42-CONTA-7"


def test_marina_dedup_composer_guia_without_conta_uses_the_lote_form() -> None:
    key = contas_business_key(tenant_id="amh", numero_lote_tiss="LOTE-001", numero_guia_tiss="GUIA-42")
    assert key == "CONTAS-amh-LOTE-001"


@pytest.mark.parametrize(
    "ids",
    [
        {"numero_guia_tiss": "GUIA-42"},  # guia sem conta e sem lote: a degeneracao de F-139
        {"numero_conta": "CONTA-7"},
        {},
        {"numero_lote_tiss": ""},
    ],
)
def test_marina_dedup_composer_refuses_the_degenerate_key(ids: dict[str, str]) -> None:
    with pytest.raises(BusinessKeyComponentError):
        contas_business_key(tenant_id="amh", **ids)


@pytest.mark.parametrize(
    "tenant_id",
    ["", "a-b", " amh", "amh\n", None, 7],
)
def test_marina_dedup_composer_refuses_a_bad_tenant(tenant_id: Any) -> None:
    with pytest.raises(BusinessKeyComponentError):
        contas_business_key(tenant_id=tenant_id, numero_lote_tiss="LOTE-001")


@pytest.mark.parametrize(
    "ids",
    [
        {"numero_lote_tiss": "LOTE 001"},
        {"numero_lote_tiss": "LOTE-001\n"},
        {"numero_guia_tiss": "GUIA 42", "numero_conta": "CONTA-7"},
        {"numero_guia_tiss": "GUIA-42", "numero_conta": "CONTA\t7"},
    ],
)
def test_marina_dedup_composer_refuses_whitespace_components(ids: dict[str, str]) -> None:
    with pytest.raises(BusinessKeyComponentError):
        contas_business_key(tenant_id="amh", **ids)


# --- every call site goes through it ------------------------------------------------------------


@pytest.mark.parametrize(
    "ids",
    [
        {"numero_lote_tiss": "LOTE-001"},
        {"numero_lote_tiss": "LOTE-001", "numero_guia_tiss": "GUIA-42"},
        {"numero_lote_tiss": "LOTE-001", "numero_guia_tiss": "GUIA-42", "numero_conta": "CONTA-7"},
        {"numero_guia_tiss": "GUIA-42", "numero_conta": "CONTA-7"},
    ],
)
def test_marina_dedup_graph_key_is_the_composer(ids: dict[str, str]) -> None:
    state = _contas_state(**{"numero_lote_tiss": "", **ids})
    assert _business_key(state) == contas_business_key(tenant_id="amh", **ids)


def test_marina_dedup_graph_key_refuses_guia_without_conta_or_lote() -> None:
    with pytest.raises(BusinessKeyComponentError):
        _business_key(_contas_state(numero_lote_tiss="", numero_guia_tiss="GUIA-42"))


def test_marina_dedup_task_id_and_envelope_builder_refuse_the_degenerate_key() -> None:
    with pytest.raises(ValueError):
        marina_task_id("amh", "contas", {"numero_guia_tiss": "GUIA-42"})
    with pytest.raises(ValueError):
        build_marina_analysis_envelope(
            tenant="amh", task_type=TASK_TYPE_GLOSA_ANALYSIS, case_meta={"numero_guia_tiss": "GUIA-42"}
        )


def test_marina_dedup_delegation_seam_refuses_what_the_composer_refuses() -> None:
    """The seam's identity check IS the composer, not a parallel predicate: a whitespace lote is
    non-empty (the old `bool(...)` predicate accepted it) but is not a composable key."""
    envelope = build_marina_analysis_envelope(
        tenant="amh", task_type=TASK_TYPE_GLOSA_ANALYSIS, case_meta={"numero_lote_tiss": "LOTE-001"}
    )
    object.__setattr__(envelope, "payload_meta", {"numero_lote_tiss": "LOTE 001"})
    with pytest.raises(ValueError, match="business-key identity"):
        state_from_envelope(envelope)


async def test_marina_dedup_receive_routes_human_on_guia_without_conta_and_never_starts() -> None:
    """Pre-fix `receive` accepted `lote OR guia` and stamped `CONTAS-amh-` on a guia-only case;
    `start_process` then started (or JOINED another case's) SP-OP-CONTAS-001 under that key."""
    cibseven = FakeCibSevenTransport()
    graph = MarinaGraph(
        inference=_FakeInference(),  # type: ignore[arg-type]
        dmn=_dmn_contas(),
        cibseven=cibseven,
        audit_sink=FakeStartAuditSink(),
    )
    state = _contas_state(numero_lote_tiss="", numero_guia_tiss="GUIA-42")
    result = await graph.receive(state)
    assert result["route"] == "human_review"
    assert result["error"]
    assert result.get("business_key", "") == ""
    started = await graph.start_process({**state, **result})  # type: ignore[typeddict-item]
    assert started == {"process_started": False}
    assert await cibseven.find_any_instance("CONTAS-amh-") is None


def test_marina_dedup_worker_demonstrativo_uses_the_composer() -> None:
    out = emitir_demonstrativo(
        DemonstrativoInput(tenant_id="amh", numero_lote_tiss="LOTE-001", prestador_id="prestador-1"),
        tipo_comunicacao="demonstrativo_analise",
    )
    expected = _mint_protocolo_demonstrativo(
        contas_business_key(tenant_id="amh", numero_lote_tiss="LOTE-001"), "demonstrativo_analise"
    )
    assert out["protocolo_demonstrativo"] == expected


def test_marina_dedup_worker_demonstrativo_refuses_the_degenerate_key() -> None:
    """Pre-fix: an empty lote minted `DEMONS-hash(CONTAS-amh-|tipo)` — one protocol shared by every
    lote-less conta of the tenant. The composer refuses instead (ValueError -> fail-closed)."""
    with pytest.raises(BusinessKeyComponentError):
        emitir_demonstrativo(
            DemonstrativoInput(tenant_id="amh", numero_lote_tiss="", prestador_id="prestador-1"),
            tipo_comunicacao="demonstrativo_analise",
        )


def test_marina_dedup_no_contas_fstring_outside_the_composer() -> None:
    """Fence: no f-string starting with `CONTAS-` anywhere in `src/maezo` except the composer."""
    offenders: list[str] = []
    for path in _SRC.rglob("*.py"):
        if path.name == "process_business_keys.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.JoinedStr) and node.values:
                head = node.values[0]
                if (
                    isinstance(head, ast.Constant)
                    and isinstance(head.value, str)
                    and head.value.startswith("CONTAS-")
                ):
                    offenders.append(f"{path.relative_to(_SRC)}:{node.lineno}")
    assert offenders == []


# --- dark EXCLUSIVE posture (DL-0046 map untouched) ---------------------------------------------


def test_marina_dedup_policy_map_keeps_contas_non_strict_by_default() -> None:
    assert start_dedup_posture("SP-OP-CONTAS-001") is StartDedupPosture.NON_STRICT


def test_marina_dedup_promotion_is_exclusive_for_contas_only() -> None:
    assert start_dedup_posture("SP-OP-CONTAS-001", promote=True) is StartDedupPosture.EXCLUSIVE
    assert start_dedup_posture("SP-OP-RECURSO-001", promote=True) is StartDedupPosture.NON_STRICT
    # a promotion never WEAKENS a gated family
    assert start_dedup_posture("SP-OP-PAGTO-001", promote=True) is StartDedupPosture.PERMANENT
    assert start_dedup_posture("SP-OP-CANCEL-001", promote=True) is StartDedupPosture.EXCLUSIVE


def _graph(*, flag: bool | None, cibseven: FakeCibSevenTransport, sink: FakeStartAuditSink, dmn: Any) -> Any:
    cfg: dict[str, Any] = {
        "inference": _FakeInference(),
        "dmn": dmn,
        "cibseven": cibseven,
        "audit_sink": sink,
    }
    if flag is not None:
        cfg["contas_start_dedup_exclusive"] = flag
    return build(cfg).compile()


async def test_marina_dedup_flag_default_off_keeps_non_strict_start() -> None:
    """Orphan claim (claim durable, engine without instance): NON_STRICT re-starts and converges."""
    cibseven = FakeCibSevenTransport()
    compiled = _graph(
        flag=None, cibseven=cibseven, sink=FakeStartAuditSink(already_audited=True), dmn=_dmn_contas()
    )
    result = await compiled.ainvoke(_contas_state())
    assert result["process_started"] is True
    assert await cibseven.find_any_instance("CONTAS-amh-LOTE-001") is not None


async def test_marina_dedup_flag_on_gates_contas_start_exclusive_via_build() -> None:
    """Flag ON: the same orphan claim is UNDECIDABLE under EXCLUSIVE — loud refusal, zero starts."""
    cibseven = FakeCibSevenTransport()
    compiled = _graph(
        flag=True, cibseven=cibseven, sink=FakeStartAuditSink(already_audited=True), dmn=_dmn_contas()
    )
    with pytest.raises(StartClaimWithoutInstanceError):
        await compiled.ainvoke(_contas_state())
    assert await cibseven.find_any_instance("CONTAS-amh-LOTE-001") is None


async def test_marina_dedup_flag_on_returns_the_live_instance() -> None:
    """Contract: 'reenvio do mesmo lote/conta retorna a instancia ativa' — under the gate too."""
    cibseven = FakeCibSevenTransport()
    sink = FakeStartAuditSink()
    first = await _graph(flag=True, cibseven=cibseven, sink=sink, dmn=_dmn_contas()).ainvoke(_contas_state())
    second = await _graph(flag=True, cibseven=cibseven, sink=sink, dmn=_dmn_contas()).ainvoke(_contas_state())
    assert first["process_ref"]["instance_id"] == second["process_ref"]["instance_id"]
    assert second["process_ref"]["already_existed"] is True


async def test_marina_dedup_flag_on_never_promotes_recurso() -> None:
    cibseven = FakeCibSevenTransport()
    compiled = _graph(
        flag=True, cibseven=cibseven, sink=FakeStartAuditSink(already_audited=True), dmn=_dmn_recurso()
    )
    result = await compiled.ainvoke(_recurso_state())
    assert result["process_started"] is True


@pytest.mark.parametrize("bad", ["true", 1, "false", 0])
def test_marina_dedup_flag_must_be_a_real_bool(bad: Any) -> None:
    with pytest.raises(TypeError):
        _graph(flag=bad, cibseven=FakeCibSevenTransport(), sink=FakeStartAuditSink(), dmn=_dmn_contas())


async def test_marina_dedup_handler_forwards_the_flag() -> None:
    cibseven = FakeCibSevenTransport()
    handler = make_marina_handler(
        _FakeInference(),  # type: ignore[arg-type]
        dmn=_dmn_contas(),
        cibseven=cibseven,
        audit_sink=FakeStartAuditSink(already_audited=True),
        contas_start_dedup_exclusive=True,
    )
    envelope = build_marina_analysis_envelope(
        tenant="amh",
        task_type=TASK_TYPE_GLOSA_ANALYSIS,
        case_meta={"numero_lote_tiss": "LOTE-001", "prestador_id": "prestador-1"},
    )
    with pytest.raises(StartClaimWithoutInstanceError):
        await handler(envelope)
    assert await cibseven.find_any_instance("CONTAS-amh-LOTE-001") is None


async def test_marina_dedup_handler_flag_default_off() -> None:
    cibseven = FakeCibSevenTransport()
    handler = make_marina_handler(
        _FakeInference(),  # type: ignore[arg-type]
        dmn=_dmn_contas(),
        cibseven=cibseven,
        audit_sink=FakeStartAuditSink(already_audited=True),
    )
    envelope = build_marina_analysis_envelope(
        tenant="amh",
        task_type=TASK_TYPE_GLOSA_ANALYSIS,
        case_meta={"numero_lote_tiss": "LOTE-001", "prestador_id": "prestador-1"},
    )
    await handler(envelope)
    assert await cibseven.find_any_instance("CONTAS-amh-LOTE-001") is not None

"""R-070 opcao B (dono, 2026-10-31, #1084) + R-076 — faixa automatica do REEMBOLSO.

O que este arquivo prova SEM motor (o comportamento da DMN e' provado contra o CIB Seven real em
tests/integration/processes/test_sp_op_reembolso_001.py — nada aqui avalia FEEL localmente):

- o worker `calculate_amount` RELAIA os fatos da faixa (politica + fonte) e NAO compara nada;
- defaults = forma "desconhecido" (teto 0, kill-switch fechado, sem limite contratual, NF nao
  verificada, sem contadores), e fonte quebrada/forjada vira "desconhecido";
- os seams de `register_reembolso_workers` chegam ao worker registrado (mata a mutacao "seam
  ignorado");
- cercas estruturais do modelo: o valor pago no caminho automatico vem da DMN (nunca da tabela),
  a regra AUTO e' unica, so' ela emite valor, e exige a lista fechada da fase 1;
- cercas de politica: teto do REEMBOLSO = 0 ate a assinatura do §6 e nunca > R$ 500 sem novo ADR;
  kill-switch de politica fechado no L0-core.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest
import yaml

from maezo.platform.xml_safe import parse_xml_without_dtd
from maezo.tools.workers.ceilings import CeilingResolver
from maezo.tools.workers.harness import ExternalTask, FakeAuditSink, FakeWorkerTransport, WorkerHarness
from maezo.tools.workers.reembolso import (
    FatosFaixaAuto,
    ReembolsoCalculoDmn,
    ReembolsoInput,
    calculate_amount_entry,
    calculate_value,
    register_reembolso_workers,
)

_REPO = Path(__file__).resolve().parents[4]
_BPMN = _REPO / "spec/processes/bpmn/SP-OP-REEMBOLSO-001_Reembolso_Beneficiario.bpmn"
_DMN = _REPO / "spec/processes/dmn/reembolso_coverage.dmn"
_CORE = _REPO / "spec/policies/autonomy/L0-core.yaml"
_AMH = _REPO / "spec/policies/autonomy/tenants-amh.yaml"
_BPMN_NS = "http://www.omg.org/spec/BPMN/20100524/MODEL"
_CAMUNDA_NS = "http://camunda.org/schema/1.0/bpmn"
_DMN_NS = "https://www.omg.org/spec/DMN/20191111/MODEL/"

_HARD_BLOCK = """\
  clinical_decision:      { level: L0, hard: true }
  authorization_denial:   { level: L0, hard: true }
  nip_manter_negativa:    { level: L0, hard: true }
  fraud_accusation:       { level: L0, hard: true }
  contract_termination:   { level: L0, hard: true }
"""

_CONSULTA = ReembolsoCalculoDmn(
    valor_calculado_tabela_cents=12000,
    multiplo_tabela_aplicado=1.0,
    fonte_tabela="TABELA_REFERENCIA_CONSULTA",
)


def _resolver(tmp_path: Path, params: str) -> CeilingResolver:
    tmp_path.mkdir(parents=True, exist_ok=True)
    core = tmp_path / "L0-core.yaml"
    core.write_text(
        f"version: 1\nactions:\n{_HARD_BLOCK}"
        f"  reembolso_auto_approval: {{ level: L2, params: {{ {params} }} }}\n",
        encoding="utf-8",
    )
    return CeilingResolver(core_path=core)


_PARAMS_ABERTOS = (
    "max_value_brl: 300, faixa_auto_desligada: false, exposicao_max_aprovacoes_beneficiario_30d: 2, "
    "exposicao_max_valor_beneficiario_30d_brl: 600, orcamento_diario_tenant_brl: 3000"
)


def _request(**overrides: Any) -> ReembolsoInput:
    base: dict[str, Any] = {
        "tenant_id": "amh",
        "protocolo_reembolso": "REEMB-R070-0001",
        "tipo_reembolso": "livre_escolha",
        "categoria_procedimento": "consulta",
        "valor_solicitado_cents": 10000,
        "beneficiario_pseudo_id": "benef-TESTE-001",
    }
    base.update(overrides)
    return ReembolsoInput(**base)


def _fonte_completa(_: ReembolsoInput) -> FatosFaixaAuto:
    return FatosFaixaAuto(
        limite_contratual_cents=20000,
        valor_nf_verificado=True,
        aprovacoes_auto_benef_30d=0,
        valor_auto_benef_30d_cents=0,
        valor_auto_tenant_dia_cents=0,
    )


# --- worker: relaia, nunca compara -------------------------------------------------------------


def test_defaults_sao_a_forma_desconhecida_com_a_politica_real() -> None:
    """Sem seam: matriz REAL (teto 0, kill-switch fechado) e nenhuma fonte => tudo desconhecido."""
    out = calculate_value(_request(), _CONSULTA)
    assert out.teto_l2_cents == 0
    assert out.faixa_auto_desligada is True
    assert out.limite_contratual_cents is None
    assert out.valor_nf_verificado is False
    assert out.aprovacoes_auto_benef_30d is None
    assert out.valor_auto_benef_30d_cents is None
    assert out.valor_auto_tenant_dia_cents is None
    # Limites DRAFT relaiados da politica real (BRL -> centavos e' troca de unidade, x100).
    assert out.limite_aprovacoes_auto_benef_30d == 2
    assert out.limite_valor_auto_benef_30d_cents == 60000
    assert out.orcamento_auto_tenant_dia_cents == 300000


def test_relaia_politica_e_fonte_injetadas(tmp_path: Path) -> None:
    out = calculate_value(
        _request(),
        _CONSULTA,
        resolver=_resolver(tmp_path, _PARAMS_ABERTOS),
        fonte_faixa_auto=_fonte_completa,
    )
    assert out.teto_l2_cents == 30000
    assert out.faixa_auto_desligada is False
    assert out.limite_contratual_cents == 20000
    assert out.valor_nf_verificado is True
    assert (
        out.aprovacoes_auto_benef_30d,
        out.valor_auto_benef_30d_cents,
        out.valor_auto_tenant_dia_cents,
    ) == (
        0,
        0,
        0,
    )


@pytest.mark.parametrize("valor", ["true", "1", "nao", "null"])
def test_kill_switch_invalido_ou_ausente_fica_fechado(tmp_path: Path, valor: str) -> None:
    """So' um `false` YAML real abre o kill-switch de politica; qualquer outra coisa = fechado."""
    out = calculate_value(
        _request(),
        _CONSULTA,
        resolver=_resolver(tmp_path, f"max_value_brl: 300, faixa_auto_desligada: {valor}"),
    )
    assert out.faixa_auto_desligada is True


def test_kill_switch_ausente_fica_fechado(tmp_path: Path) -> None:
    out = calculate_value(_request(), _CONSULTA, resolver=_resolver(tmp_path, "max_value_brl: 300"))
    assert out.faixa_auto_desligada is True
    assert out.limite_aprovacoes_auto_benef_30d == 0  # limite ausente = 0 = nada admitido


def test_fonte_forjada_vira_desconhecido() -> None:
    """bool/float/negativo/str nao viram contagem; NF so' conta com `True` literal."""

    def _forjada(_: ReembolsoInput) -> FatosFaixaAuto:
        return FatosFaixaAuto(
            limite_contratual_cents=True,
            valor_nf_verificado=1,  # type: ignore[arg-type]
            aprovacoes_auto_benef_30d=-1,
            valor_auto_benef_30d_cents=10.0,  # type: ignore[arg-type]
            valor_auto_tenant_dia_cents="0",  # type: ignore[arg-type]
        )

    out = calculate_value(_request(), _CONSULTA, fonte_faixa_auto=_forjada)
    assert out.limite_contratual_cents is None
    assert out.valor_nf_verificado is False
    assert out.aprovacoes_auto_benef_30d is None
    assert out.valor_auto_benef_30d_cents is None
    assert out.valor_auto_tenant_dia_cents is None


def test_fonte_que_quebra_ou_devolve_tipo_errado_vira_desconhecido() -> None:
    def _quebra(_: ReembolsoInput) -> FatosFaixaAuto:
        raise RuntimeError("fonte fora do ar")

    def _errada(_: ReembolsoInput) -> FatosFaixaAuto:
        return {"valor_nf_verificado": True}  # type: ignore[return-value]

    for fonte in (_quebra, _errada):
        out = calculate_value(_request(), _CONSULTA, fonte_faixa_auto=fonte)
        assert out.valor_nf_verificado is False
        assert out.limite_contratual_cents is None


def test_worker_nao_decide_nem_calcula_o_valor_pago(tmp_path: Path) -> None:
    """O worker nao emite recomendacao nem valor aprovado: isso e' da DMN (ADR-0012)."""
    out = calculate_amount_entry(
        {
            "tenant_id": "amh",
            "protocolo_reembolso": "REEMB-R070-0002",
            "tipo_reembolso": "livre_escolha",
            "categoria_procedimento": "consulta",
            "valor_solicitado_cents": 10000,
            "valor_calculado_tabela_cents": 12000,
            "multiplo_tabela_aplicado": 1.0,
            "fonte_tabela": "TABELA_REFERENCIA_CONSULTA",
        },
        resolver=_resolver(tmp_path, _PARAMS_ABERTOS),
        fonte_faixa_auto=_fonte_completa,
    )
    for proibido in ("recomendacao", "valor_aprovado_auto_cents", "valor_reembolso_aprovado_cents"):
        assert proibido not in out
    assert out["teto_l2_cents"] == 30000


def test_register_encaminha_os_seams_ao_worker_registrado(tmp_path: Path) -> None:
    """Mutacao "seam ignorado" morre aqui: o handler REGISTRADO usa resolver e fonte injetados."""
    transport = FakeWorkerTransport()
    harness = WorkerHarness(transport, worker_id="w-r070", tenant="amh", audit_sink=FakeAuditSink())
    register_reembolso_workers(
        harness,
        None,
        reembolso_ceiling_resolver=_resolver(tmp_path, _PARAMS_ABERTOS),
        reembolso_fonte_faixa_auto=_fonte_completa,
    )
    task = ExternalTask(
        task_id="t-r070",
        topic="operadora.reembolso.calculate_amount",
        process_instance_id="pi-r070",
        business_key="REEMB-amh-R070-0003",
        worker_id="w-r070",
        variables={
            "tenant_id": "amh",
            "protocolo_reembolso": "REEMB-R070-0003",
            "tipo_reembolso": "livre_escolha",
            "categoria_procedimento": "consulta",
            "valor_solicitado_cents": 10000,
            "valor_calculado_tabela_cents": 12000,
            "multiplo_tabela_aplicado": 1.0,
            "fonte_tabela": "TABELA_REFERENCIA_CONSULTA",
        },
    )
    asyncio.run(harness._handle(task))
    assert transport.completed, f"calculate_amount nao completou: failures={transport.failures!r}"
    _tid, out = transport.completed[-1]
    assert out["teto_l2_cents"] == 30000
    assert out["valor_nf_verificado"] is True
    assert out["limite_contratual_cents"] == 20000


# --- cercas estruturais do modelo (sem avaliar FEEL) -------------------------------------------


def test_bpmn_caminho_automatico_paga_o_valor_da_dmn_nunca_a_tabela() -> None:
    """Fecha o sobrepagamento por construcao (F-028 / REEMBOLSO-AUTO-OVERPAY-a)."""
    root = parse_xml_without_dtd(_BPMN.read_bytes())
    task = next(el for el in root.iter(f"{{{_BPMN_NS}}}serviceTask") if el.get("id") == "ST_IssuePaymentAuto")
    params = {el.get("name"): (el.text or "").strip() for el in task.iter(f"{{{_CAMUNDA_NS}}}inputParameter")}
    assert params["valor_reembolso_aprovado_cents"] == "${auto_aprovacao.valor_aprovado_auto_cents}"
    assert "valor_calculado_tabela_cents" not in params["valor_reembolso_aprovado_cents"]


def _auto_approval_rules() -> tuple[list[str], list[tuple[str, list[str], list[str]]]]:
    root = parse_xml_without_dtd(_DMN.read_bytes())
    decision = next(
        el for el in root.iter(f"{{{_DMN_NS}}}decision") if el.get("id") == "reembolso_auto_approval"
    )
    exprs = [
        (el.findtext(f"{{{_DMN_NS}}}text") or "").strip()
        for el in decision.iter(f"{{{_DMN_NS}}}inputExpression")
    ]
    rules = []
    for rule in decision.iter(f"{{{_DMN_NS}}}rule"):
        ins = [
            (e.findtext(f"{{{_DMN_NS}}}text") or "").strip() for e in rule.findall(f"{{{_DMN_NS}}}inputEntry")
        ]
        outs = [
            (e.findtext(f"{{{_DMN_NS}}}text") or "").strip()
            for e in rule.findall(f"{{{_DMN_NS}}}outputEntry")
        ]
        rules.append((rule.get("id") or "", ins, outs))
    return exprs, rules


def test_dmn_so_a_regra_auto_emite_valor_e_ele_e_o_solicitado() -> None:
    _exprs, rules = _auto_approval_rules()
    autos = [r for r in rules if r[2][0] == '"AUTO_APROVAR"']
    assert [r[0] for r in autos] == ["r_auto"], "exatamente UMA regra pode auto-aprovar"
    assert autos[0][2][2] == "valor_solicitado_cents", "aprovacao integral: paga o solicitado"
    for rid, _ins, outs in rules:
        if rid != "r_auto":
            assert outs[0] == '"ANALISE_HUMANA"', rid
            assert outs[2] == "null", f"{rid} nao pode emitir valor"
    rid, ins, _outs = rules[-1]
    assert rid == "r_auto_catchall" and set(ins) == {"-"}, "catch-all humano tem de ser a ultima regra"


def test_dmn_regra_auto_exige_todas_as_condicoes_da_opcao_b() -> None:
    exprs, rules = _auto_approval_rules()
    _rid, ins, _outs = next(r for r in rules if r[0] == "r_auto")
    por_expr = dict(zip(exprs, ins, strict=True))
    assert "-" not in ins, "r_auto nao pode ter don't-care: toda condicao e' obrigatoria"
    assert por_expr["faixa_auto_desligada"] == "false"
    assert por_expr["requer_avaliacao_clinica"] == "false"
    assert por_expr["tipo_reembolso"] == '"livre_escolha"'
    assert por_expr["categoria_procedimento"] == '"consulta"'  # lista fechada: art. 10-B fica fora
    assert por_expr["valor_nf_verificado"] == "true"
    # Comparacoes de valor — todas na DMN, nenhuma no worker (ADR-0012).
    assert por_expr["valor_solicitado_cents"] == "> 0"
    for teto in ("valor_calculado_tabela_cents", "limite_contratual_cents", "teto_l2_cents"):
        assert por_expr[teto] == ">= valor_solicitado_cents", teto
    assert por_expr["aprovacoes_auto_benef_30d"] == "< limite_aprovacoes_auto_benef_30d"
    assert (
        por_expr["valor_auto_benef_30d_cents"]
        == "<= limite_valor_auto_benef_30d_cents - valor_solicitado_cents"
    )
    assert (
        por_expr["valor_auto_tenant_dia_cents"]
        == "<= orcamento_auto_tenant_dia_cents - valor_solicitado_cents"
    )
    # Cerca PHI-completeness: toda inputExpression e' um identificador simples.
    assert all(e.isidentifier() for e in exprs), exprs


# --- cercas de politica ------------------------------------------------------------------------


def _reembolso_params(path: Path, *, overlay: bool) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    node = (data["overrides"] if overlay else data["actions"])["reembolso_auto_approval"]
    return dict(node.get("params") or {})


def test_teto_reembolso_zero_ate_assinatura_e_nunca_acima_de_500() -> None:
    """R-070 §3.3/§3.6: R$ 0 ate o lake AMH + assinatura do §6; nunca > R$ 500 sem novo ADR.

    Esta cerca MUDA no PR de ativacao (apos o §6), nunca antes.
    """
    for path, overlay in ((_CORE, False), (_AMH, True)):
        teto = _reembolso_params(path, overlay=overlay)["max_value_brl"]
        assert teto == 0, f"{path.name}: teto do REEMBOLSO subiu sem a assinatura do R-070 §6"
        assert teto <= 500


def test_kill_switch_e_exposicao_draft_no_l0_core() -> None:
    params = _reembolso_params(_CORE, overlay=False)
    assert params["faixa_auto_desligada"] is True
    assert params["exposicao_max_aprovacoes_beneficiario_30d"] == 2
    assert params["exposicao_max_valor_beneficiario_30d_brl"] == 600
    assert params["orcamento_diario_tenant_brl"] == 3000
    assert "faixa_auto_desligada" not in _reembolso_params(_AMH, overlay=True)


# --- CeilingResolver: leituras de parametro (config-validity) ----------------------------------


def test_bool_param_so_aceita_bool_real(tmp_path: Path) -> None:
    r = _resolver(tmp_path, "max_value_brl: 1, a: false, b: true, c: 0, d: 'false'")
    kw = {"tenant": "amh", "action": "reembolso_auto_approval"}
    assert r.bool_param(**kw, param="a") is False
    assert r.bool_param(**kw, param="b") is True
    assert r.bool_param(**kw, param="c") is None
    assert r.bool_param(**kw, param="d") is None
    assert r.bool_param(**kw, param="ausente") is None
    assert r.bool_param(tenant="amh", action="acao_inexistente", param="a") is None


def test_int_param_clampa_invalido_em_zero(tmp_path: Path) -> None:
    r = _resolver(tmp_path, "max_value_brl: 1, a: 2, b: -3, c: true, d: x")
    kw = {"tenant": "amh", "action": "reembolso_auto_approval"}
    assert r.int_param(**kw, param="a") == 2
    assert r.int_param(**kw, param="b") == 0
    assert r.int_param(**kw, param="c") == 0
    assert r.int_param(**kw, param="d") == 0
    assert r.int_param(**kw, param="ausente") == 0

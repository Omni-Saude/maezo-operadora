"""R-070 opcao B + R-076 + DL-0164 — faixa automatica do REEMBOLSO: PRETENDIDA, ativacao DESLIGADA.

O que este arquivo prova SEM motor (o comportamento da DMN e' provado contra o CIB Seven real em
tests/integration/processes/test_sp_op_reembolso_001.py — nada aqui avalia FEEL localmente):

- o worker `validate_auto_criteria` RELAIA os fatos da faixa (politica + fonte) e NAO compara nada;
- defaults = forma "desconhecido" (teto 0, kill-switch fechado, R-058 False, sem tabela, sem limite
  contratual, NF nao verificada, sem merito clinico conhecido, sem contadores); fonte quebrada/forjada
  vira "desconhecido";
- `register_auto_approval` (segunda barreira) recusa cada violacao — testes NEGATIVOS da guarda de alcada;
- os seams de `register_reembolso_workers` chegam aos workers registrados (mata a mutacao "seam ignorado");
- cercas estruturais (B1): nenhuma entrada da DMN e' sombreada por inputParameter local; o valor aprovado
  no caminho automatico vem da DMN (nunca de tabela); so' `r_auto` emite valor e ela exige todas as
  condicoes da opcao B;
- cercas de politica: teto do REEMBOLSO = 0 ate a assinatura do §6 e nunca > R$ 500 sem novo ADR;
  kill-switch de politica fechado no L0-core; TETO_DURO da opcao B (R$ 300) e nenhum teto de tenant acima
  dele; politica com teto acima do TETO_DURO fecha a faixa (R-070 §3.1 item 5 / §3.5 "configuracao
  invalida") e a guarda recusa (DL-0164);
- cerca de ATIVACAO (DL-0164): o interruptor unico de ativacao (R-058) e os freios de politica so' abrem
  com as assinaturas do dono E da atuaria no R-070 §6.
"""

from __future__ import annotations

import asyncio
import dataclasses
import re
from pathlib import Path
from typing import Any

import pytest
import yaml

from maezo.platform.xml_safe import parse_xml_without_dtd
from maezo.tools.workers import reembolso as reembolso_module
from maezo.tools.workers.ceilings import CeilingResolver
from maezo.tools.workers.harness import ExternalTask, FakeAuditSink, FakeWorkerTransport, WorkerHarness
from maezo.tools.workers.reembolso import (
    ERR_REEMBOLSO_AUTO_PAGAMENTO_BLOQUEADO,
    FatosFaixaAuto,
    ReembolsoAutoPagamentoBloqueadoError,
    ReembolsoInput,
    register_auto_approval_entry,
    register_reembolso_workers,
    validate_auto_criteria,
    validate_auto_criteria_entry,
)

_REPO = Path(__file__).resolve().parents[4]
_BPMN = _REPO / "spec/processes/bpmn/SP-OP-REEMBOLSO-001_Reembolso_Beneficiario.bpmn"
_DMN = _REPO / "spec/processes/dmn/reembolso_auto_approval.dmn"
_CORE = _REPO / "spec/policies/autonomy/L0-core.yaml"
_AMH = _REPO / "spec/policies/autonomy/tenants-amh.yaml"
_R070 = _REPO / "docs/compliance/human-windows/R-070-dec-teto-d07.md"
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
    "max_value_brl: 300, teto_duro_brl: 300, faixa_auto_desligada: false, "
    "exposicao_max_aprovacoes_beneficiario_30d: 2, "
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
        avaliacao_clinica_requerida=False,
        valor_tabela_cents=12000,
        limite_contratual_cents=20000,
        valor_nf_verificado=True,
        aprovacoes_auto_benef_30d=0,
        valor_auto_benef_30d_cents=0,
        valor_auto_tenant_dia_cents=0,
    )


# --- worker: relaia, nunca compara -------------------------------------------------------------


def test_defaults_sao_a_forma_desconhecida_com_a_politica_real() -> None:
    """Sem seam: matriz REAL (teto 0, kill-switch fechado) e nenhuma fonte => tudo desconhecido."""
    out = validate_auto_criteria(_request())
    assert out.reembolso_auto_liberado is False  # R-058
    assert out.origem_aprovacao == "HUMANA"
    assert out.avaliacao_clinica_requerida is None
    assert out.valor_tabela_dmn_cents is None
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
    out = validate_auto_criteria(
        _request(),
        resolver=_resolver(tmp_path, _PARAMS_ABERTOS),
        fonte_faixa_auto=_fonte_completa,
    )
    assert out.teto_l2_cents == 30000
    assert out.faixa_auto_desligada is False
    assert out.avaliacao_clinica_requerida is False
    assert out.valor_tabela_dmn_cents == 12000
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
    out = validate_auto_criteria(
        _request(),
        resolver=_resolver(tmp_path, f"max_value_brl: 300, faixa_auto_desligada: {valor}"),
    )
    assert out.faixa_auto_desligada is True


def test_kill_switch_ausente_fica_fechado(tmp_path: Path) -> None:
    out = validate_auto_criteria(_request(), resolver=_resolver(tmp_path, "max_value_brl: 300"))
    assert out.faixa_auto_desligada is True
    assert out.limite_aprovacoes_auto_benef_30d == 0  # limite ausente = 0 = nada admitido


def test_fonte_forjada_vira_desconhecido() -> None:
    """bool/float/negativo/str nao viram contagem; NF so' conta com `True` literal."""

    def _forjada(_: ReembolsoInput) -> FatosFaixaAuto:
        return FatosFaixaAuto(
            avaliacao_clinica_requerida=0,  # type: ignore[arg-type]
            valor_tabela_cents=-5,
            limite_contratual_cents=True,
            valor_nf_verificado=1,  # type: ignore[arg-type]
            aprovacoes_auto_benef_30d=-1,
            valor_auto_benef_30d_cents=10.0,  # type: ignore[arg-type]
            valor_auto_tenant_dia_cents="0",  # type: ignore[arg-type]
        )

    out = validate_auto_criteria(_request(), fonte_faixa_auto=_forjada)
    assert out.avaliacao_clinica_requerida is None
    assert out.valor_tabela_dmn_cents is None
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
        out = validate_auto_criteria(_request(), fonte_faixa_auto=fonte)
        assert out.valor_nf_verificado is False
        assert out.limite_contratual_cents is None


def test_worker_nao_decide_nem_calcula_o_valor_pago(tmp_path: Path) -> None:
    """O worker nao emite recomendacao nem valor aprovado nem booleano de comparacao (ADR-0012, B3)."""
    out = validate_auto_criteria_entry(
        {
            "tenant_id": "amh",
            "protocolo_reembolso": "REEMB-R070-0002",
            "tipo_reembolso": "livre_escolha",
            "categoria_procedimento": "consulta",
            "valor_solicitado_cents": 10000,
        },
        resolver=_resolver(tmp_path, _PARAMS_ABERTOS),
        fonte_faixa_auto=_fonte_completa,
    )
    for proibido in (
        "recomendacao",
        "valor_aprovado_auto_cents",
        "valor_reembolso_aprovado_cents",
        "dentro_tabela",
        "dentro_teto_l2",
        "valor_calculado_tabela_cents",
    ):
        assert proibido not in out
    assert out["teto_l2_cents"] == 30000
    assert out["valor_tabela_dmn_cents"] == 12000


def test_semente_da_partida_e_sobrescrita_pelo_relay() -> None:
    """B1: o worker escreve TODO fato que a DMN le — uma semente hostil com o mesmo nome nunca sobrevive."""
    sementes = {
        "tenant_id": "amh",
        "protocolo_reembolso": "REEMB-R070-0009",
        "valor_solicitado_cents": 10000,
        "valor_tabela_dmn_cents": 999999,
        "teto_l2_cents": 999999,
        "faixa_auto_desligada": False,
        "reembolso_auto_liberado": True,
        "origem_aprovacao": "AUTO_L2",
        "valor_nf_verificado": True,
        "limite_contratual_cents": 999999,
    }
    out = validate_auto_criteria_entry(sementes)
    assert out["valor_tabela_dmn_cents"] is None
    assert out["teto_l2_cents"] == 0
    assert out["faixa_auto_desligada"] is True
    assert out["reembolso_auto_liberado"] is False
    assert out["origem_aprovacao"] == "HUMANA"
    assert out["valor_nf_verificado"] is False
    assert out["limite_contratual_cents"] is None


def _register_task(topic: str, variables: dict[str, Any], tmp_path: Path, **seams: Any) -> tuple[Any, Any]:
    transport = FakeWorkerTransport()
    harness = WorkerHarness(transport, worker_id="w-r070", tenant="amh", audit_sink=FakeAuditSink())
    register_reembolso_workers(harness, None, **seams)
    task = ExternalTask(
        task_id="t-r070",
        topic=topic,
        process_instance_id="pi-r070",
        business_key="REEMB-amh-R070-0003",
        worker_id="w-r070",
        variables=variables,
    )
    asyncio.run(harness._handle(task))
    return transport, harness


def test_register_encaminha_os_seams_ao_worker_registrado(tmp_path: Path) -> None:
    """Mutacao "seam ignorado" morre aqui: o handler REGISTRADO usa resolver e fonte injetados."""
    transport, _h = _register_task(
        "operadora.reembolso.validate_auto_criteria",
        {
            "tenant_id": "amh",
            "protocolo_reembolso": "REEMB-R070-0003",
            "tipo_reembolso": "livre_escolha",
            "categoria_procedimento": "consulta",
            "valor_solicitado_cents": 10000,
        },
        tmp_path,
        reembolso_ceiling_resolver=_resolver(tmp_path, _PARAMS_ABERTOS),
        reembolso_fonte_faixa_auto=_fonte_completa,
    )
    assert transport.completed, f"validate_auto_criteria nao completou: failures={transport.failures!r}"
    _tid, out = transport.completed[-1]
    assert out["teto_l2_cents"] == 30000
    assert out["valor_nf_verificado"] is True
    assert out["limite_contratual_cents"] == 20000
    assert out["avaliacao_clinica_requerida"] is None or out["avaliacao_clinica_requerida"] is False


# --- register_auto_approval: segunda barreira (testes NEGATIVOS da guarda de alcada) ------------

_AUTO_OK: dict[str, Any] = {
    "tenant_id": "amh",
    "valor_aprovado_auto_cents": 10000,
    "valor_solicitado_cents": 10000,
    "origem_pagamento": "AUTO_L2",
}


def test_register_auto_approval_com_defaults_recusa_r058(tmp_path: Path) -> None:
    """Producao: R-058 False + teto 0 + kill-switch fechado => incidente, nada aprovado."""
    with pytest.raises(ReembolsoAutoPagamentoBloqueadoError) as exc:
        register_auto_approval_entry(dict(_AUTO_OK))
    msg = str(exc.value)
    assert msg.startswith(ERR_REEMBOLSO_AUTO_PAGAMENTO_BLOQUEADO)
    assert "R-058" in msg
    assert "teto" in msg
    assert "faixa_auto_desligada" in msg


def test_register_auto_approval_so_aprova_com_tudo_liberado(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(reembolso_module, "REEMBOLSO_AUTO_PAGAMENTO_LIBERADO", True)
    out = register_auto_approval_entry(dict(_AUTO_OK), resolver=_resolver(tmp_path, _PARAMS_ABERTOS))
    assert out == {"valor_reembolso_aprovado_cents": 10000, "origem_aprovacao": "AUTO_L2"}


@pytest.mark.parametrize(
    ("caso", "override", "params", "motivo"),
    [
        ("teto_zero", {}, "max_value_brl: 0, faixa_auto_desligada: false", "teto"),
        (
            "acima_do_teto",
            {"valor_aprovado_auto_cents": 30001, "valor_solicitado_cents": 30001},
            None,
            "teto",
        ),
        ("parcial_nunca", {"valor_aprovado_auto_cents": 9000}, None, "!= solicitado"),
        ("acima_do_solicitado", {"valor_aprovado_auto_cents": 11000}, None, "!= solicitado"),
        ("valor_zero", {"valor_aprovado_auto_cents": 0, "valor_solicitado_cents": 0}, None, "nao positivo"),
        ("valor_nulo", {"valor_aprovado_auto_cents": None}, None, "invalido"),
        ("valor_float", {"valor_aprovado_auto_cents": 10000.0}, None, "invalido"),
        ("valor_bool", {"valor_aprovado_auto_cents": True, "valor_solicitado_cents": True}, None, "invalido"),
        ("origem_nao_auto", {"origem_pagamento": "HUMANA"}, None, "origem_pagamento"),
        (
            "kill_switch_politica",
            {},
            "max_value_brl: 300, faixa_auto_desligada: true",
            "faixa_auto_desligada",
        ),
        ("kill_switch_ausente", {}, "max_value_brl: 300", "faixa_auto_desligada"),
        (
            "teto_acima_do_teto_duro",
            {},
            "max_value_brl: 400, teto_duro_brl: 300, faixa_auto_desligada: false",
            "teto_duro",
        ),
        ("teto_duro_ausente", {}, "max_value_brl: 300, faixa_auto_desligada: false", "teto_duro"),
    ],
)
def test_register_auto_approval_recusa_cada_violacao_mesmo_com_r058_aberto(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caso: str,
    override: dict[str, Any],
    params: str | None,
    motivo: str,
) -> None:
    """Teste NEGATIVO da guarda de alcada: R-058 aberto no teste, UMA violacao — sempre incidente."""
    monkeypatch.setattr(reembolso_module, "REEMBOLSO_AUTO_PAGAMENTO_LIBERADO", True)
    resolver = _resolver(tmp_path / caso, params if params is not None else _PARAMS_ABERTOS)
    with pytest.raises(ReembolsoAutoPagamentoBloqueadoError) as exc:
        register_auto_approval_entry({**_AUTO_OK, **override}, resolver=resolver)
    assert motivo in str(exc.value)
    assert "R-058" not in str(exc.value)


def test_register_auto_approval_registrado_vira_incidente_com_defaults(tmp_path: Path) -> None:
    """No harness real (sem seams = producao), o worker registrado FALHA e nada completa."""
    transport, _h = _register_task("operadora.reembolso.register_auto_approval", dict(_AUTO_OK), tmp_path)
    assert not transport.completed
    assert transport.failures


# --- cercas estruturais do modelo (sem avaliar FEEL) -------------------------------------------


def _service_task(task_id: str) -> Any:
    root = parse_xml_without_dtd(_BPMN.read_bytes())
    return next(el for el in root.iter(f"{{{_BPMN_NS}}}serviceTask") if el.get("id") == task_id)


def test_bpmn_caminho_automatico_aprova_o_valor_da_dmn_nunca_a_tabela() -> None:
    """Fecha o sobrepagamento por construcao (F-028 / REEMBOLSO-AUTO-OVERPAY-a) na v2."""
    task = _service_task("ST_RegistrarAprovacaoAuto")
    params = {el.get("name"): (el.text or "").strip() for el in task.iter(f"{{{_CAMUNDA_NS}}}inputParameter")}
    assert params == {
        "valor_aprovado_auto_cents": "${auto_aprovacao.valor_aprovado_auto_cents}",
        "origem_pagamento": "AUTO_L2",
    }
    assert task.get(f"{{{_CAMUNDA_NS}}}topic") == "operadora.reembolso.register_auto_approval"


def test_b1_nenhuma_entrada_da_dmn_e_sombreada_por_variavel_local() -> None:
    """B1: o relay nao tem inputOutput (tudo vai ao escopo do processo) e nenhuma entrada da DMN e'
    nome de inputParameter LOCAL de qualquer task do processo."""
    relay = _service_task("ST_ValidarFaixaAuto")
    assert relay.find(f".//{{{_CAMUNDA_NS}}}inputOutput") is None
    root = parse_xml_without_dtd(_BPMN.read_bytes())
    locais = {el.get("name") for el in root.iter(f"{{{_CAMUNDA_NS}}}inputParameter")}
    exprs, _rules = _auto_approval_rules()
    assert not set(exprs) & locais, set(exprs) & locais
    # E toda entrada que nao e' fato da partida e' produzida pelo relay.
    relaiados = {f.name for f in dataclasses.fields(reembolso_module.ReembolsoFaixaAutoResult)}
    da_partida = {"tipo_reembolso", "categoria_procedimento", "valor_solicitado_cents"}
    assert set(exprs) - da_partida <= relaiados
    assert "valor_tabela_dmn_cents" in exprs
    assert "valor_calculado_tabela_cents" not in exprs


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
    assert por_expr["avaliacao_clinica_requerida"] == "false"
    assert por_expr["tipo_reembolso"] == '"livre_escolha"'
    assert por_expr["categoria_procedimento"] == '"consulta"'  # lista fechada: art. 10-B fica fora
    assert por_expr["valor_nf_verificado"] == "true"
    # B3: sem booleano de comparacao vindo do worker (dentro_tabela/dentro_teto_l2 nao voltaram).
    assert "dentro_tabela" not in por_expr and "dentro_teto_l2" not in por_expr
    # Comparacoes de valor — todas na DMN, nenhuma no worker (ADR-0012).
    assert por_expr["valor_solicitado_cents"] == "> 0"
    for teto in ("valor_tabela_dmn_cents", "limite_contratual_cents", "teto_l2_cents"):
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
    for limite in (
        "limite_aprovacoes_auto_benef_30d",
        "limite_valor_auto_benef_30d_cents",
        "orcamento_auto_tenant_dia_cents",
    ):
        assert por_expr[limite] == "> 0", limite
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


# --- DL-0164 (DEC-31 D31-1): TETO_DURO da opcao B e configuracao invalida fecha a faixa -----------


@pytest.mark.parametrize(
    ("params", "fechada"),
    [
        ("max_value_brl: 300, teto_duro_brl: 300, faixa_auto_desligada: false", False),
        ("max_value_brl: 0, teto_duro_brl: 300, faixa_auto_desligada: false", False),
        ("max_value_brl: 301, teto_duro_brl: 300, faixa_auto_desligada: false", True),
        ("max_value_brl: 300, faixa_auto_desligada: false", True),
        ("max_value_brl: 300, teto_duro_brl: x, faixa_auto_desligada: false", True),
    ],
)
def test_teto_acima_do_teto_duro_fecha_a_faixa(tmp_path: Path, params: str, *, fechada: bool) -> None:
    """R-070 §3.1 item 5 / §3.5: politica invalida (teto acima do TETO_DURO, ou TETO_DURO ausente com
    teto positivo) FECHA a faixa — relaiada como kill-switch fechado, nunca como "paga ate o TETO_DURO"."""
    out = validate_auto_criteria(_request(), resolver=_resolver(tmp_path, params))
    assert out.faixa_auto_desligada is fechada
    # O teto relaiado e' o da politica, sem clamp: a DMN nunca ve um teto "corrigido" em Python.
    assert out.teto_l2_cents == int(params.split(",")[0].split(":")[1]) * 100


def test_teto_duro_da_opcao_b_e_nenhum_tenant_passa_dele() -> None:
    """DL-0164: o TETO_DURO da fase 1 e' o da opcao B escolhida pelo dono (R-070 §4 linha B, §5-bis): R$ 300;
    nunca > R$ 500 sem novo ADR (§3.6 item 3); nenhum overlay de tenant sobe o teto acima dele nem o
    proprio TETO_DURO acima do core."""
    core = _reembolso_params(_CORE, overlay=False)
    teto_duro = core["teto_duro_brl"]
    assert teto_duro == 300
    assert teto_duro <= 500
    assert core["max_value_brl"] <= teto_duro
    overlays = sorted((_REPO / "spec/policies/autonomy").glob("tenants-*.yaml"))
    assert _AMH in overlays
    for path in overlays:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        node = (data.get("overrides") or {}).get("reembolso_auto_approval")
        if node is None:
            continue
        params = dict(node.get("params") or {})
        assert params.get("teto_duro_brl", teto_duro) <= teto_duro, f"{path.name}: TETO_DURO acima do core"
        assert params.get("max_value_brl", 0) <= teto_duro, f"{path.name}: teto acima do TETO_DURO"


# --- DL-0164: interruptor unico de ativacao (R-058) amarrado as assinaturas do R-070 §6 ------------

_PLACEHOLDER = re.compile(r"^\[\s*(nome)?\s*\]$")
_PAPEIS_S6 = ("Decisor (dono)", "Atuária")


def _assinaturas_s6(texto: str) -> dict[str, bool]:
    """Para cada papel do §6 do R-070: True so' com nome, data E assinatura preenchidos.

    Le' o bloco ``## 6.`` ate a proxima secao. Linha de papel ausente = erro (a cerca nunca e' vacua).
    """
    inicio = texto.index("## 6.")
    fim = texto.find("\n## ", inicio + 1)
    bloco = texto[inicio : fim if fim != -1 else len(texto)]
    out: dict[str, bool] = {}
    for papel in _PAPEIS_S6:
        m = re.search(
            rf"^- \*\*{re.escape(papel)}:\*\* (?P<nome>.*?)\s+\*\*Data:\*\* (?P<data>.*?)\s+"
            r"\*\*Assinatura:\*\* (?P<assinatura>.*?)\s*$",
            bloco,
            flags=re.MULTILINE,
        )
        assert m is not None, f"R-070 §6 sem a linha de {papel!r}: a cerca de ativacao ficaria vacua"
        out[papel] = all(v.strip() and not _PLACEHOLDER.match(v.strip()) for v in m.groupdict().values())
    return out


def test_parser_do_s6_le_os_dois_papeis_e_reconhece_preenchimento() -> None:
    hoje = _assinaturas_s6(_R070.read_text(encoding="utf-8"))
    assert set(hoje) == set(_PAPEIS_S6)
    sintetico = (
        "## 6. Bloco\n"
        "- **Decisor (dono):** Fulano   **Data:** 2027-01-01   **Assinatura:** Fulano\n"
        "- **Atuária:** [nome]   **Data:** [  ]   **Assinatura:** [  ]\n"
        "\n## 7. Outra\n"
    )
    assert _assinaturas_s6(sintetico) == {"Decisor (dono)": True, "Atuária": False}


def test_ativacao_da_faixa_b_so_com_o_s6_assinado_pelo_dono_e_pela_atuaria() -> None:
    """DL-0164 (DEC-31 D31-1): a faixa B e' o comportamento PRETENDIDO, mas a ATIVACAO fica desligada ate o
    R-070 §6 ter as assinaturas do dono E da atuaria (§3.6 item 2). O interruptor unico de ativacao e' a
    constante R-058; os freios de politica (kill-switch `faixa_auto_desligada`, teto do tenant) seguem
    independentes e tambem nao abrem sem o §6. Nenhum agente preenche o §6 (ADR-0007)."""
    assinado = all(_assinaturas_s6(_R070.read_text(encoding="utf-8")).values())
    core = _reembolso_params(_CORE, overlay=False)
    pedidos = {
        "R-058 REEMBOLSO_AUTO_PAGAMENTO_LIBERADO": reembolso_module.REEMBOLSO_AUTO_PAGAMENTO_LIBERADO is not False,
        "L0-core faixa_auto_desligada": core.get("faixa_auto_desligada") is not True,
        "L0-core max_value_brl": core.get("max_value_brl", 0) != 0,
    }
    for path in sorted((_REPO / "spec/policies/autonomy").glob("tenants-*.yaml")):
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        node = (data.get("overrides") or {}).get("reembolso_auto_approval") or {}
        params = dict(node.get("params") or {})
        pedidos[f"{path.name} max_value_brl"] = params.get("max_value_brl", 0) != 0
        pedidos[f"{path.name} faixa_auto_desligada"] = params.get("faixa_auto_desligada", True) is not True
    abertos = sorted(k for k, v in pedidos.items() if v)
    if not assinado:
        assert not abertos, f"ativacao da faixa B sem as assinaturas do R-070 §6 (dono + atuaria): {abertos}"

"""Cercas estáticas do wiring GP11 — DMN `suppression_routing` + relógio de SP-OP-SUPP-001.

Insumos ACEITOS PELO DONO (2026-10-07 — VW0-DECISION-REGISTER §"INCORPORAÇÃO VW4-ANSWERS",
sha `ab262f7b…`; conteúdo `VW4-RATIFICATION-ANSWERS-V1.md` §GP11). O que cada cerca prova:

- **DMN:** hitPolicy FIRST com catch-all ÚLTIMA row; as partições da taxonomia C1–C6 (§4b);
  `k_piso` como INPUT/PARAM (a cifra ratificada NUNCA está na tabela); saídas NUNCA são os
  verbos proibidos; `REGISTRO_HONRADO` só nas rows nomeadas de célula ≥ k / classe livre.
- **BPMN:** o relógio por registro (§4c) — três timers `timeDate` ABSOLUTOS sobre as variáveis
  do registro (âncora = fato gerador, idioma CONTAS GAP-4), 50/80/100%, só o timeout é
  interrupting; NENHUM outro timer; TTL de histórico = precedente LGPD-DSR (o enforceTTL do
  engine recusa deploy sem TTL — ENGINE-12018, prova live); payloads de evento minimizados
  e SEM RATE (G-CADE assert estrutural).
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[3]
_DMN = _REPO / "spec/processes/dmn/suppression_routing.dmn"
_BPMN = _REPO / "spec/processes/bpmn/SP-OP-SUPP-001_Direitos_de_Nao_Contato.bpmn"


def _local(tag: str) -> str:
    return tag.rpartition("}")[2]


def _text_of(element: ET.Element) -> str:
    for child in element:
        if _local(child.tag) == "text":
            return (child.text or "").strip()
    return ""


_ROOT_DMN = ET.parse(_DMN).getroot()
_ROOT_BPMN = ET.parse(_BPMN).getroot()

_TABLE = next(el for el in _ROOT_DMN.iter() if _local(el.tag) == "decisionTable")
_RULES = [el for el in _TABLE if _local(el.tag) == "rule"]
_INPUTS = [el for el in _TABLE if _local(el.tag) == "input"]


def _input_entries(rule: ET.Element) -> list[str]:
    return [_text_of(el) for el in rule if _local(el.tag) == "inputEntry"]


def _output_entries(rule: ET.Element) -> list[str]:
    return [_text_of(el) for el in rule if _local(el.tag) == "outputEntry"]


def _input_names() -> list[str]:
    names = []
    for declared in _INPUTS:
        for child in declared:
            if _local(child.tag) == "inputExpression":
                names.append(_text_of(child))
    return names


# ----------------------------------------------------------------------------------
# DMN suppression_routing — taxonomia C1–C6, k como param, catch-all fail-closed
# ----------------------------------------------------------------------------------


def test_a_tabela_e_first_com_a_catch_all_como_ultima_row() -> None:
    assert _TABLE.get("hitPolicy") == "FIRST"
    assert _RULES[-1].get("id") == "r_catchall"
    assert all(entry == "-" for entry in _input_entries(_RULES[-1]))


def test_as_cinco_colunas_sao_as_do_contrato_e_k_e_input_param() -> None:
    """`k_piso` é INPUT/PARAM — a cifra ratificada (K_ANON_FLOOR=100) NUNCA está na tabela."""
    assert _input_names() == ["canal", "categoria_sujeito", "classe_campo", "celula_tamanho", "k_piso"]


def test_a_cifra_k_nunca_esta_na_tabela() -> None:
    """§4a: k=100 vive na constante ACEITA do código (`K_ANON_FLOOR`); a DMN compara contra o
    input `k_piso`. Nenhuma entry carrega o literal 100."""
    for rule in _RULES:
        for entry in _input_entries(rule):
            assert "100" not in entry, f"{rule.get('id')}: o piso não se materializa na tabela"
        for entry in _output_entries(rule):
            assert "100" not in entry


def test_as_particoes_da_taxonomia_estao_nomeadas_e_na_ordem_fail_closed() -> None:
    ids = [rule.get("id") for rule in _RULES]
    assert ids == [
        "r_desconhecido",  # categoria não declarada nunca auto-honra (primeira row)
        "r_c1",
        "r_c3",
        "r_c4",
        "r_c2_subk",
        "r_c2_ok",
        "r_c6_subk",
        "r_c6_ok",
        "r_c5",
        "r_catchall",
    ]


def test_as_rows_de_classe_carregam_o_token_fechado_c1_c6() -> None:
    tokens = {rule.get("id"): _input_entries(rule) for rule in _RULES}
    for classe in ("C1", "C3", "C4", "C5"):
        rule_id = {"C1": "r_c1", "C3": "r_c3", "C4": "r_c4", "C5": "r_c5"}[classe]
        assert tokens[rule_id][2] == f'"{classe}"'
    assert tokens["r_c2_subk"][2] == '"C2"' and tokens["r_c2_subk"][3] == "< k_piso"
    assert tokens["r_c2_ok"][2] == '"C2"' and tokens["r_c2_ok"][3] == ">= k_piso"
    assert tokens["r_c6_subk"][2] == '"C6"' and tokens["r_c6_subk"][3] == "< k_piso"
    assert tokens["r_c6_ok"][2] == '"C6"' and tokens["r_c6_ok"][3] == ">= k_piso"


def test_honra_somente_nas_rows_nomeadas_e_nunca_por_omissao() -> None:
    """`REGISTRO_HONRADO` EXPLÍCITO só existe na célula ≥ k (C2/C6) e na classe livre (C5) —
    tudo o mais (incluída a catch-all) vai ao humano."""
    honoridas = {
        rule.get("id")
        for rule in _RULES
        if any('"REGISTRO_HONRADO"' in entry for entry in _output_entries(rule))
    }
    assert honoridas == {"r_c2_ok", "r_c6_ok", "r_c5"}


@pytest.mark.parametrize("verbo", ["LIBERAR", "AUTORIZAR", "PAGAR", "CALCULAR"])
def test_nenhuma_saida_e_verbo_proibido(verbo: str) -> None:
    """Transversal 1 do dossiê: saída de DMN nunca LIBERAR/AUTORIZAR/PAGAR/CALCULAR."""
    for rule in _RULES:
        for entry in _output_entries(rule):
            assert verbo not in entry


def test_toda_row_tem_aridade_cinco() -> None:
    for rule in _RULES:
        assert len(_input_entries(rule)) == len(_INPUTS) == 5, rule.get("id")


def test_a_categoria_desconhecida_e_a_primeira_row_fail_closed() -> None:
    assert _input_entries(_RULES[0]) == ["-", '"desconhecido"', "-", "-", "-"]


def test_todas_as_rows_vao_ao_grupo_dpo_fechado() -> None:
    """Grupo FECHADO `dpo` (encarregado art. 41) — nenhuma persona/rota nova de portal."""
    for rule in _RULES:
        assert '"dpo"' in _output_entries(rule), rule.get("id")


# ----------------------------------------------------------------------------------
# BPMN SP-OP-SUPP-001 — o relógio por registro (§4c) e a higiene dos payloads
# ----------------------------------------------------------------------------------


def _process() -> ET.Element:
    return next(el for el in _ROOT_BPMN.iter() if _local(el.tag) == "process")


def test_os_tres_timers_do_relogio_leem_as_variaveis_do_registro() -> None:
    """§4c: timers `timeDate` ABSOLUTOS sobre as variáveis devolvidas por verify_subject —
    âncora = o fato gerador (nascimento do registro), nunca o attach da User Task."""
    timedates: dict[str, str] = {}
    for boundary in (el for el in _process().iter() if _local(el.tag) == "boundaryEvent"):
        for definition in (el for el in boundary if _local(el.tag) == "timerEventDefinition"):
            for timedate in (el for el in definition if _local(el.tag) == "timeDate"):
                # `timeDate` carrega a expressão como PRÓPRIO texto (não é um <text> filho).
                timedates[boundary.get("id") or ""] = (timedate.text or "").strip()
    assert timedates == {
        "BT_Lembrete50": "${t_lembrete_iso}",
        "BT_Escala80": "${t_escalonado_iso}",
        "BT_Timeout100": "${t_deadline_iso}",
    }


def test_so_o_timeout_100_e_interrupting() -> None:
    boundaries = {
        el.get("id"): el.get("cancelActivity")
        for el in _process().iter()
        if _local(el.tag) == "boundaryEvent" and (el.get("id") or "").startswith("BT_")
    }
    assert boundaries == {"BT_Lembrete50": "false", "BT_Escala80": "false", "BT_Timeout100": None}
    # None = ausente = interrupting (default BPMN) — o SLA estourado interrompe a UT.


def test_nenhum_outro_timer_existe_no_processo() -> None:
    """O relógio GP11 é POR REGISTRO: nenhum timer de instância/global/ ciclo acompanha."""
    timers = [el for el in _process().iter() if _local(el.tag) == "timerEventDefinition"]
    assert sorted(el.get("id") for el in timers) == ["TED_Escala80", "TED_Lembrete50", "TED_Timeout100"]


def test_o_ttl_de_historico_espelha_o_irmao_lgpd_dsr() -> None:
    """TTL de limpeza de HISTÓRICO DO ENGINE (enforceTTL — ENGINE-12018, prova live do wiring):
    sem TTL o engine RECUSA o deploy e o wiring GP11 nasce morto. Valores = precedente do
    irmão LGPD-DSR (BPMN 1825 / DMN P180D — 63/63 DMNs e 20/21 BPMNs têm TTL; o envelope era
    a única exceção da árvore). NÃO é o prazo de retenção de negócio (VW0-D12 RATIFY-LATER —
    a disposição do registro vivo é do dono; o downgrade da 0021 recusa store populado)."""
    bpmn = _BPMN.read_text(encoding="utf-8")
    assert 'isExecutable="true" camunda:historyTimeToLive="1825"' in bpmn
    assert 'camunda:historyTimeToLive="P180D"' in _DMN.read_text(encoding="utf-8")


def test_os_boundaries_estao_presetes_na_ut_do_encarregado() -> None:
    for boundary in (el for el in _process().iter() if _local(el.tag) == "boundaryEvent"):
        if (boundary.get("id") or "").startswith("BT_"):
            assert boundary.get("attachedToRef") == "UT_RotearEncarregado"


def test_eventos_de_escalacao_tem_payloads_minimizados() -> None:
    """Payloads dos eventos `sla_*`: identificador + prazo, nada mais (art. 10 §1º; o prazo é
    parte da tríade minimizada do registry OP20 — identificador + DATA + canal)."""
    esperado = {
        "agents.events.vendor.suppression.sla_lembrete": "tenant_id,suppression_ref,t_deadline_iso",
        "agents.events.vendor.suppression.sla_escalonado": "tenant_id,suppression_ref,t_deadline_iso",
        "agents.events.vendor.suppression.sla_estourado": "tenant_id,suppression_ref,t_deadline_iso",
    }
    vistos: dict[str, str] = {}
    for task in (el for el in _process().iter() if _local(el.tag) == "serviceTask"):
        for parameter in (el for el in task.iter() if _local(el.tag) == "inputParameter"):
            if parameter.get("name") == "event_topic":
                topic = (parameter.text or "").strip()
                if topic in esperado:
                    payload = next(
                        (p.text or "").strip()
                        for p in task.iter()
                        if _local(p.tag) == "inputParameter" and p.get("name") == "event_payload_vars"
                    )
                    vistos[topic] = payload
    assert vistos == esperado


@pytest.mark.parametrize(
    "termo",
    ["rate", "taxa", "frequencia", "frequência", "percent", "comissao", "comissão", "premio", "prêmio"],
)
def test_nenhuma_rate_ou_comparacao_comercial_em_payload_estrutural_cade(termo: str) -> None:
    """G-CADE (VW0-D23, assert estrutural): nenhum `event_payload_vars` de SP-OP-SUPP-001
    carrega RATE/medida comercial — telemetria presence-only, contagens nunca atravessam."""
    for parameter in (el for el in _process().iter() if _local(el.tag) == "inputParameter"):
        if parameter.get("name") == "event_payload_vars":
            assert termo not in (parameter.text or "").lower()


def test_o_processo_preserva_as_recusas_tipadas_do_envelope() -> None:
    """Os dois erros modelados continuam declarados (consumption-covered ADR-0030 — nunca
    dead models; as recusas são a forma preservada do estado de admissão)."""
    source = _BPMN.read_text(encoding="utf-8")
    assert 'errorCode="ERR_SUPP_SUBJECT_UNRESOLVED"' in source
    assert 'errorCode="ERR_SUPP_ROUTING_UNAVAILABLE"' in source

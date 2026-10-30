"""Fences ESTATICOS de R-007 (F-002, F-121) — calendario ANS sem placeholder e sem gatilho sem norma.

Contrato: `docs/processes/contracts/SP-OP-ANS-CRON-001.md` (secoes "RN 639/2025" e "Calendario
automatico desligado"). Decisao do dono DEC-03 (issue #1081 -> R-006): retirar o timer
`RN_124_SIP` para competencias novas (RN 639/2025 revoga a RN 551/2022, vigente 2026-03-02;
DL-0029: art. 2o desobriga o SIP apos o 4o tri/2025), manter caminho MANUAL para o backlog ate o
4o tri/2025, demais gatilhos SO com norma primaria, `DRAFT_DUE_DATE` sai.

O que estes fences provam (pura leitura de XML + funcao pura do worker, sem engine):

  1. `ans_calendar.dmn` nao devolve nenhum literal `DRAFT_*`; toda saida de data e uma data ISO
     `YYYY-MM-DD` real OU vazia (sem data automatica: o prazo e resolvido por humano). Nenhuma
     data foi inventada: nenhuma das obrigacoes tem data verificada em fonte primaria hoje.
  2. A row do SIP so cobre o backlog (competencia ate o 4o tri/2025) e cita a RN 639/2025; uma
     competencia nova do SIP cai numa row propria de obrigacao extinta.
  3. O BPMN do agendador nao tem mais o timer `RN_124_SIP`, e nenhuma definition de timer fica
     EXECUTAVEL enquanto nenhum gatilho estiver ancorado em norma primaria verificada.
  4. A competencia trimestral sai no formato `YYYY-Qn` do contrato de envio (F-121).

As metades que exigem engine (o engine nao agenda nenhum timer do agendador; a avaliacao real da
DMN para backlog x extinto) vivem em `tests/integration/processes/test_sp_op_ans_cron_001.py` e
`tests/integration/dmn/test_dmn_golden_parity.py`.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from pathlib import Path

from maezo.tools.workers.ans_cron import _REPORT_PERIODICIDADE, _compute_competencia

_REPO = Path(__file__).resolve().parents[3]
_DMN = _REPO / "spec/processes/dmn/ans_calendar.dmn"
_BPMN_CRON = _REPO / "spec/processes/bpmn/SP-OP-ANS-CRON-001_Agendador_Envios_ANS.bpmn"

_DMN_NS = {"d": "https://www.omg.org/spec/DMN/20191111/MODEL/"}
_BPMN_NS = {
    "b": "http://www.omg.org/spec/BPMN/20100524/MODEL",
    "c": "http://camunda.org/schema/1.0/bpmn",
}

_ISO_DATE = re.compile(r'"\d{4}-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])"')


def _rules() -> list[ET.Element]:
    root = ET.parse(_DMN).getroot()
    table = root.find("d:decision/d:decisionTable", _DMN_NS)
    assert table is not None
    return table.findall("d:rule", _DMN_NS)


def _output_names() -> list[str]:
    root = ET.parse(_DMN).getroot()
    return [o.get("name", "") for o in root.findall("d:decision/d:decisionTable/d:output", _DMN_NS)]


def _row(rule: ET.Element) -> tuple[list[str], dict[str, str]]:
    ins = [(e.findtext("d:text", "", _DMN_NS) or "").strip() for e in rule.findall("d:inputEntry", _DMN_NS)]
    outs = [(e.findtext("d:text", "", _DMN_NS) or "").strip() for e in rule.findall("d:outputEntry", _DMN_NS)]
    return ins, dict(zip(_output_names(), outs, strict=True))


def test_ans_calendar_sem_placeholder_draft() -> None:
    """F-002: nenhum literal `DRAFT_*` (DRAFT_DUE_DATE/DRAFT_ALERTA) em nenhuma saida da tabela."""
    texto = _DMN.read_text(encoding="utf-8")
    assert "DRAFT_DUE_DATE" not in texto
    assert "DRAFT_ALERTA" not in texto
    for rule in _rules():
        _, outs = _row(rule)
        for name, value in outs.items():
            assert not value.strip('"').startswith("DRAFT_"), f"{rule.get('id')}.{name} = {value}"


def test_ans_calendar_datas_sao_iso_reais_ou_vazias() -> None:
    """Toda saida de data e `YYYY-MM-DD` real ou `""` (sem data automatica). Nunca um literal que
    se passe por data. Hoje TODAS sao vazias: nenhuma obrigacao tem data verificada em fonte
    primaria (raw/prazos-verificados.csv), e nenhuma data foi inventada."""
    for rule in _rules():
        _, outs = _row(rule)
        for name in ("due_date", "sla_alerta"):
            value = outs[name]
            assert value == '""' or _ISO_DATE.fullmatch(value), f"{rule.get('id')}.{name} = {value!r}"


def test_ans_calendar_sip_so_cobre_backlog_ate_4o_tri_2025() -> None:
    """DEC-03: o SIP tem DUAS rows proprias. A primeira (backlog) so casa competencia com forma
    valida anterior a `2026-01` (ate o 4o tri/2025) e cita a RN 639/2025; a segunda casa qualquer
    outra competencia do SIP como obrigacao extinta, roteada a humano."""
    sip = [r for r in _rules() if _row(r)[0][0] == '"RN_124_SIP"']
    assert [r.get("id") for r in sip] == ["r_rn124_sip_backlog", "r_rn124_sip_extinta"]

    backlog_in, backlog_out = _row(sip[0])
    assert '"2026-01"' in backlog_in[1], backlog_in
    assert "matches(" in backlog_in[1], "a forma da competencia precisa ser validada"
    assert "RN 639/2025" in backlog_out["fonte_regulatoria"]
    assert backlog_out["fonte_regulatoria"].startswith('"CALENDARIO_MANUAL')

    extinta_in, extinta_out = _row(sip[1])
    assert extinta_in[1] == "-"
    assert extinta_out["fonte_regulatoria"].startswith('"REVISAO_HUMANA')
    assert "RN 639/2025" in extinta_out["fonte_regulatoria"]


def test_ans_calendar_demais_tipos_sem_norma_primaria_sao_manuais() -> None:
    """Nenhum dos demais gatilhos tem norma primaria verificada (REGULATORIO.md: RN 209 revogada
    pela RN 451, RN 388 revogada pela RN 483, RN 424 e junta medica, DIOPS sem norma localizada).
    As rows continuam (o tipo continua existindo no envio manual), mas sem data e marcadas
    `CALENDARIO_MANUAL`."""
    tipos = {"RN_209_UTILIZACAO", "RN_388_QUALIDADE", "RN_424_TISS_MONITORAMENTO", "DIOPS_TRIMESTRAL"}
    vistos = set()
    for rule in _rules():
        ins, outs = _row(rule)
        tipo = ins[0].strip('"')
        if tipo in tipos:
            vistos.add(tipo)
            assert outs["fonte_regulatoria"].startswith('"CALENDARIO_MANUAL'), rule.get("id")
    assert vistos == tipos


def test_cron_sem_timer_rn_124_sip() -> None:
    """F-002 / DEC-03: o timer do SIP sai do agendador (nenhuma definition, nenhum literal)."""
    texto = _BPMN_CRON.read_text(encoding="utf-8")
    assert "RN_124_SIP" not in texto
    assert "RN124SIP" not in texto
    assert "Rn124Sip" not in texto


def test_cron_nenhum_gatilho_automatico_sem_norma_primaria() -> None:
    """DEC-03 ("demais gatilhos so com norma primaria"): nenhuma definition do agendador e
    executavel hoje, porque nenhum dos 4 gatilhos restantes tem norma primaria verificada. Com
    `isExecutable="false"` o engine nao cria a definition nem o job do timer (provado contra o
    engine em `test_sp_op_ans_cron_001.py`). Religar um gatilho exige parecer COUNSEL (R-006) com
    a norma e a cadencia, e a troca deste fence no mesmo PR."""
    root = ET.parse(_BPMN_CRON).getroot()
    processes = root.findall("b:process", _BPMN_NS)
    assert len(processes) == 4
    for proc in processes:
        assert proc.get("isExecutable") == "false", f"{proc.get('id')} ainda executavel"


def test_competencia_trimestral_no_formato_do_contrato() -> None:
    """F-121: o contrato de envio (SP-OP-ANS-SUBMIT-001 §entrada) define a competencia trimestral
    como `YYYY-Qn`. O trimestre fechado anterior a ancora sai nessa forma."""
    assert _compute_competencia("2026-05-15", "P3M") == "2026-Q1"
    assert _compute_competencia("2026-02-15", "P3M") == "2025-Q4"
    assert _REPORT_PERIODICIDADE["DIOPS_TRIMESTRAL"] == ("trimestral", "P3M")

"""R-005 / F-018 (DEC-02) — prazo de resposta AUTH em dias uteis (RN 623/2024 art. 12), DARK.

O que estes testes fixam:

* QUANTOS dias uteis vem da DMN `auth_sla` (coluna `prazo_resposta_dias_uteis`: 10 para PAC/
  internacao eletiva, 5 nos demais eletivos e no fail-safe, nulo na urgencia). O worker
  `operadora.auth.compute_response_deadline` so faz a aritmetica de calendario.
* Desligada (`MAEZO_AUTH_PRAZO_DIAS_UTEIS`, padrao), o worker escreve tres nulos e o timer
  `BT_SlaAnalise` segue no ISO corrido da DMN — comportamento de antes.
* Ligada, o timer le a duracao ate o fim do N-esimo dia util contado da data da solicitacao.
* A fiacao settings -> bring-up -> register_default_workers -> register_auth_workers -> worker
  falha sob mutacao (cada elo testado com a bandeira nos dois valores).

Fixtures sinteticas; nenhum dado real.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Any

import pytest

from maezo.platform.calendario import CALENDARIO_VERSAO
from maezo.platform.xml_safe import parse_xml_without_dtd
from maezo.tools.workers.auth import (
    COMPUTE_RESPONSE_DEADLINE_TOPIC,
    COMPUTE_RESPONSE_DEADLINE_VARIABLES,
    ComputeResponseDeadlineWorker,
    register_auth_workers,
)
from maezo.tools.workers.harness import FakeWorkerTransport, WorkerHarness

DURACAO = "prazo_resposta_duracao_util"
VENCIMENTO = "prazo_resposta_vencimento_util_iso"
CALENDARIO = "prazo_resposta_calendario_versao"
NULOS = {DURACAO: None, VENCIMENTO: None, CALENDARIO: None}

_REPO = Path(__file__).resolve().parents[4]
_BPMN = _REPO / "spec/processes/bpmn/SP-OP-AUTH-001_Autorizacao_Previa.bpmn"
_DMN = _REPO / "spec/processes/dmn/auth_sla.dmn"
_B = "{http://www.omg.org/spec/BPMN/20100524/MODEL}"
_C = "{http://camunda.org/schema/1.0/bpmn}"
_D = "{https://www.omg.org/spec/DMN/20191111/MODEL/}"

#: Relogio fixo: segunda 9/11/2026 13:00 UTC (10:00 em Brasilia).
AGORA = dt.datetime(2026, 11, 9, 13, 0, tzinfo=dt.UTC)


def _relogio() -> dt.datetime:
    return AGORA


def _vars(**overrides: object) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "tenant_id": "tenant_sintetico",
        "numero_guia_tiss": "GUIA-SINTETICA-001",
        "prazo_resposta_dias_uteis": 5,
    }
    payload.update(overrides)
    return payload


def _ligado() -> ComputeResponseDeadlineWorker:
    return ComputeResponseDeadlineWorker(dias_uteis_ativo=True, relogio=_relogio)


# --- worker -------------------------------------------------------------------------------------


def test_topico() -> None:
    assert ComputeResponseDeadlineWorker().topic == COMPUTE_RESPONSE_DEADLINE_TOPIC
    assert COMPUTE_RESPONSE_DEADLINE_TOPIC == "operadora.auth.compute_response_deadline"


def test_desligado_por_padrao_escreve_so_nulos() -> None:
    worker = ComputeResponseDeadlineWorker(relogio=_relogio)
    assert worker.execute(_vars(uf_beneficiario="SP", data_solicitacao_iso="2026-11-06")) == NULOS


@pytest.mark.parametrize("lixo", ["true", 1, "1", object()])
def test_so_o_booleano_true_liga(lixo: object) -> None:
    worker = ComputeResponseDeadlineWorker(dias_uteis_ativo=lixo, relogio=_relogio)  # type: ignore[arg-type]
    assert worker.execute(_vars()) == NULOS


def test_ligado_ancora_ausente_conta_do_relogio() -> None:
    # segunda 9/11 + 5 dias uteis -> fim de segunda 16/11 (15/11 e domingo) = 02:59:59Z de 17/11
    out = _ligado().execute(_vars())
    assert out[VENCIMENTO] == "2026-11-16T23:59:59-03:00"
    assert out[DURACAO] == f"PT{7 * 86400 + 13 * 3600 + 59 * 60 + 59}S"
    assert out[CALENDARIO] == CALENDARIO_VERSAO


def test_ligado_ancora_da_solicitacao_e_feriados() -> None:
    # solicitacao quinta 29/10/2026: 30 (1), 2/11 Finados, 3 (2), 4 (3), 5 (4), 6 (5)
    out = _ligado().execute(_vars(data_solicitacao_iso="2026-10-29T15:00:00-03:00"))
    assert out[VENCIMENTO] == "2026-11-06T23:59:59-03:00"


def test_ligado_pac_10_dias_uteis_e_data_pura() -> None:
    out = _ligado().execute(_vars(prazo_resposta_dias_uteis=10, data_solicitacao_iso="2026-10-30"))
    assert out[VENCIMENTO] == "2026-11-16T23:59:59-03:00"


def test_ligado_feriado_da_uf() -> None:
    sp = _ligado().execute(
        _vars(prazo_resposta_dias_uteis=1, data_solicitacao_iso="2026-07-08", uf_beneficiario="SP")
    )
    nac = _ligado().execute(_vars(prazo_resposta_dias_uteis=1, data_solicitacao_iso="2026-07-08"))
    assert sp[VENCIMENTO] == "2026-07-10T23:59:59-03:00"
    assert nac[VENCIMENTO] == "2026-07-09T23:59:59-03:00"
    # vencimento ja passado em relacao ao relogio -> o timer dispara de imediato
    assert sp[DURACAO] == nac[DURACAO] == "PT0S"


def test_ligado_ancora_no_futuro_e_limitada_a_agora() -> None:
    out = _ligado().execute(_vars(data_solicitacao_iso="2027-03-01T10:00:00-03:00"))
    assert out[VENCIMENTO] == "2026-11-16T23:59:59-03:00"


@pytest.mark.parametrize("ancora", ["2026-11-06T10:00:00", "06/11/2026", "amanha", 20261106, ["2026-11-06"]])
def test_ligado_ancora_ambigua_ou_invalida_nao_e_adivinhada(ancora: object) -> None:
    assert _ligado().execute(_vars(data_solicitacao_iso=ancora)) == NULOS


@pytest.mark.parametrize("dias", [None, 0, -5, 367, True, "5", 5.0])
def test_ligado_sem_dias_uteis_validos_mantem_o_iso_da_dmn(dias: object) -> None:
    # None = urgencia (resposta imediata): a DMN nao da dias uteis e o PT2H da DMN segue valendo
    assert _ligado().execute(_vars(prazo_resposta_dias_uteis=dias)) == NULOS


def test_nao_le_carater_nem_categoria() -> None:
    """Nenhuma regra de prazo em Python: o worker nao pede as entradas da DMN."""
    assert "carater_atendimento" not in COMPUTE_RESPONSE_DEADLINE_VARIABLES
    assert "categoria_procedimento" not in COMPUTE_RESPONSE_DEADLINE_VARIABLES
    out_urg = _ligado().execute(_vars(carater_atendimento="urgencia"))
    out_ele = _ligado().execute(_vars(carater_atendimento="eletivo"))
    assert out_urg == out_ele


# --- composicao: settings -> bring-up -> register_default_workers -> register_auth_workers --------


def _harness() -> WorkerHarness:
    return WorkerHarness(FakeWorkerTransport(), worker_id="r005-probe")


def _ativo(harness: WorkerHarness) -> bool:
    worker = harness.registry.get(COMPUTE_RESPONSE_DEADLINE_TOPIC)
    assert type(worker) is ComputeResponseDeadlineWorker
    return worker.execute(_vars(data_solicitacao_iso="2026-10-29")) != NULOS


def test_settings_bandeira_dark_por_padrao(monkeypatch: pytest.MonkeyPatch) -> None:
    from maezo.runtime.worker_runtime.settings import WorkerRuntimeSettings

    monkeypatch.delenv("MAEZO_AUTH_PRAZO_DIAS_UTEIS", raising=False)
    assert WorkerRuntimeSettings().auth_prazo_dias_uteis is False
    monkeypatch.setenv("MAEZO_AUTH_PRAZO_DIAS_UTEIS", "true")
    assert WorkerRuntimeSettings().auth_prazo_dias_uteis is True


@pytest.mark.parametrize("ligada", [False, True])
def test_register_auth_workers_repassa_a_bandeira(ligada: bool) -> None:
    harness = _harness()
    register_auth_workers(harness, auth_prazo_dias_uteis=ligada)
    assert _ativo(harness) is ligada


@pytest.mark.parametrize("lixo", ["true", "false", 1, "1"])
def test_seam_lixo_nao_liga(lixo: object) -> None:
    harness = _harness()
    register_auth_workers(harness, auth_prazo_dias_uteis=lixo)
    assert _ativo(harness) is False


def test_assinatura_restrita_de_variaveis() -> None:
    """Least-privilege: o fetchAndLock pede so as variaveis declaradas, nunca todas."""
    harness = _harness()
    register_auth_workers(harness)
    subs = {s.topic_name: s for s in harness._topic_subscriptions()}
    assert subs[COMPUTE_RESPONSE_DEADLINE_TOPIC].variables == COMPUTE_RESPONSE_DEADLINE_VARIABLES
    assert "prazo_resposta_dias_uteis" in COMPUTE_RESPONSE_DEADLINE_VARIABLES


@pytest.mark.parametrize("ligada", [False, True])
def test_register_default_workers_repassa_a_bandeira(ligada: bool) -> None:
    from maezo.runtime.worker_runtime.service import register_default_workers

    harness = _harness()
    register_default_workers(harness, auth_prazo_dias_uteis=ligada)
    assert _ativo(harness) is ligada


def test_topico_no_conjunto_esperado_do_daemon() -> None:
    from maezo.runtime.worker_runtime.service import _expected_worker_topics

    assert COMPUTE_RESPONSE_DEADLINE_TOPIC in _expected_worker_topics()


# --- DMN: o numero de dias uteis mora aqui --------------------------------------------------------


def _coluna_dias_uteis() -> dict[str, str]:
    root = parse_xml_without_dtd(_DMN.read_bytes())
    table = root.find(f".//{_D}decisionTable")
    assert table is not None
    outputs = [o.get("name") for o in table.findall(f"{_D}output")]
    idx = outputs.index("prazo_resposta_dias_uteis")
    assert table.findall(f"{_D}output")[idx].get("typeRef") == "integer"
    return {
        rule.get("id") or "": (rule.findall(f"{_D}outputEntry")[idx].findtext(f"{_D}text") or "").strip()
        for rule in table.findall(f"{_D}rule")
    }


def test_dmn_dias_uteis_do_art_12() -> None:
    # r1 urgencia (imediata: sem conversao) | r2 PAC/internacao eletiva | r3 eletivo | r4 fail-safe
    assert _coluna_dias_uteis() == {"r1": "null", "r2": "10", "r3": "5", "r4": "5"}


def test_dmn_iso_corrido_preservado() -> None:
    """D-05: o teto ISO corrido continua sendo a saida sla_analise (caminho ativo com a bandeira off)."""
    root = parse_xml_without_dtd(_DMN.read_bytes())
    table = root.find(f".//{_D}decisionTable")
    assert table is not None
    outputs = [o.get("name") for o in table.findall(f"{_D}output")]
    idx = outputs.index("sla_analise")
    valores = [
        (r.findall(f"{_D}outputEntry")[idx].findtext(f"{_D}text") or "") for r in table.findall(f"{_D}rule")
    ]
    assert valores == ['"PT2H"', '"P10D"', '"P5D"', '"P5D"']


# --- BPMN: a task fica imediatamente antes da UT e o timer cai no ISO quando nulo ---------------


def _proc():  # type: ignore[no-untyped-def]
    root = parse_xml_without_dtd(_BPMN.read_bytes())
    proc = root.find(f"{_B}process")
    assert proc is not None
    return proc


def test_bpmn_task_entre_o_dossie_e_a_analise_humana() -> None:
    proc = _proc()
    flows = {f.get("id"): (f.get("sourceRef"), f.get("targetRef")) for f in proc.iter(f"{_B}sequenceFlow")}
    task = proc.find(f"{_B}serviceTask[@id='ST_CalcularPrazoRespostaUtil']")
    assert task is not None
    assert task.get(f"{_C}topic") == COMPUTE_RESPONSE_DEADLINE_TOPIC
    assert task.get(f"{_C}type") == "external"
    entradas = [s for s, t in flows.values() if t == "ST_CalcularPrazoRespostaUtil"]
    saidas = [t for s, t in flows.values() if s == "ST_CalcularPrazoRespostaUtil"]
    assert entradas == ["ST_PrepararDossie"]
    assert saidas == ["UT_AnaliseMedicoAuditor"]
    assert [s for s, t in flows.values() if t == "UT_AnaliseMedicoAuditor"] == [
        "ST_CalcularPrazoRespostaUtil"
    ]
    params = {p.get("name"): (p.text or "").strip() for p in task.iter(f"{_C}inputParameter")}
    assert params == {"prazo_resposta_dias_uteis": "${sla.prazo_resposta_dias_uteis}"}


def test_bpmn_timer_sla_cai_no_iso_da_dmn_quando_nulo() -> None:
    proc = _proc()
    bt = proc.find(f"{_B}boundaryEvent[@id='BT_SlaAnalise']")
    assert bt is not None
    expr = (bt.findtext(f"{_B}timerEventDefinition/{_B}timeDuration") or "").strip()
    assert expr == (
        "${execution.getVariable('prazo_resposta_duracao_util') != null ? "
        "execution.getVariable('prazo_resposta_duracao_util') : sla.sla_analise}"
    )
    alerta = proc.find(f"{_B}boundaryEvent[@id='BT_AlertaSla']")
    assert alerta is not None
    assert (
        alerta.findtext(f"{_B}timerEventDefinition/{_B}timeDuration") or ""
    ).strip() == "${sla.sla_alerta}"

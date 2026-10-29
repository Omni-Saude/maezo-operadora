"""Unit tests for maezo.tools.workers.reembolso — SP-OP-REEMBOLSO-001 v0.2.0 (DL-0158).

100% analise humana: o worker NAO calcula, NAO compara teto, NAO auto-aprova e NAO emite pagamento.
Estes testes exercitam os contratos de external task que sobraram e CERCAM a remocao: um teste que lê o BPMN
e a DMN falha se o caminho automatico, o calculo ou o worker de pagamento voltarem sem um DL.
"""

import asyncio
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

import pytest

from maezo.tools.workers import reembolso
from maezo.tools.workers.base import FunctionWorker
from maezo.tools.workers.harness import (
    ExternalTask,
    FakeAuditSink,
    FakeKafkaPublisher,
    FakeWorkerTransport,
    WorkerBpmnError,
    WorkerHarness,
)
from maezo.tools.workers.reembolso import (
    DIAS_NAO_CALCULAVEL,
    ERR_REEMBOLSO_INVALID_PROTOCOLO,
    REEMBOLSO_BPMN_ERROR_ALLOWLIST,
    ReembolsoDenialInput,
    ReembolsoDenialNotHumanError,
    ReembolsoInput,
    analyze_request,
    analyze_request_entry,
    check_coverage,
    check_coverage_entry,
    notify_sla_risk,
    notify_sla_risk_entry,
    publish_completed,
    publish_completed_entry,
    register_reembolso_workers,
    request_documents,
    request_documents_entry,
    send_reembolso_denial,
    send_reembolso_denial_entry,
)

_REPO_ROOT = Path(__file__).resolve().parents[4]
_BPMN_PATH = _REPO_ROOT / "spec" / "processes" / "bpmn" / "SP-OP-REEMBOLSO-001_Reembolso_Beneficiario.bpmn"
_DMN_DIR = _REPO_ROOT / "spec" / "processes" / "dmn"
_BPMN_NS = "{http://www.omg.org/spec/BPMN/20100524/MODEL}"
_CAMUNDA_NS = "{http://camunda.org/schema/1.0/bpmn}"


def _input(**overrides: Any) -> ReembolsoInput:
    base: dict[str, Any] = {
        "tenant_id": "amh",
        "protocolo_reembolso": "REE-AB12-CD34",
        "beneficiario_pseudo_id": "BEN-PSEUDO-001",
        "matricula_beneficiario": "MAT-001",
        "tipo_reembolso": "fora_rede",
        "categoria_procedimento": "consulta",
        "valor_solicitado_cents": 35000,
        "data_atendimento": "2026-10-01",
        "data_solicitacao": "2026-10-11",
    }
    base.update(overrides)
    return ReembolsoInput(**base)


def _denial(**overrides: Any) -> ReembolsoDenialInput:
    base: dict[str, Any] = {
        "decisao_reembolso": "NEGAR",
        "justificativa": "Procedimento fora da cobertura contratual.",
        "fundamentacao_contratual": "Clausula 12.3 do contrato.",
        "texto_ao_beneficiario": (
            "Nao foi possivel aprovar este reembolso. Fale com a equipe se quiser revisao."
        ),
        "valor_reembolso_aprovado_cents": 1,
        "valor_solicitado_cents": 35000,
        "analista_id": "analista-01",
    }
    base.update(overrides)
    return ReembolsoDenialInput(**base)


# ---------------------------------------------------------------------------
# check_coverage — origin guard + dias_desde_atendimento (fato, nunca regra)
# ---------------------------------------------------------------------------


def test_check_coverage_resolve_apenas_dias_desde_atendimento() -> None:
    result = check_coverage(_input())
    assert result == {"dias_desde_atendimento": 10}


@pytest.mark.parametrize(
    ("atendimento", "solicitacao"),
    [
        ("", "2026-10-11"),  # ausente
        ("2026-10-01", ""),  # ausente
        ("nao-e-data", "2026-10-11"),  # ilegivel
        ("2026-10-11", "2026-10-01"),  # solicitacao ANTES do atendimento
    ],
)
def test_dias_nao_calculavel_vira_sentinela_e_nunca_nega(atendimento: str, solicitacao: str) -> None:
    """Datas ausentes/invertidas => -1 => a DMN informativa cai no catch-all NAO_AVALIADO. Nunca erro,
    nunca negativa."""
    result = check_coverage(_input(data_atendimento=atendimento, data_solicitacao=solicitacao))
    assert result == {"dias_desde_atendimento": DIAS_NAO_CALCULAVEL}


def test_check_coverage_nao_afirma_cobertura_nem_decide_nada() -> None:
    """v0.2.0: nao ha conferencia de cobertura por maquina (o analista avalia). Nenhum booleano de
    decisao sai."""
    result = check_coverage(_input())
    assert set(result) == {"dias_desde_atendimento"}


def test_check_coverage_protocolo_ausente_levanta_o_erro_modelado() -> None:
    with pytest.raises(WorkerBpmnError) as exc:
        check_coverage(_input(protocolo_reembolso="   "))
    assert exc.value.error_code == ERR_REEMBOLSO_INVALID_PROTOCOLO


def test_check_coverage_entry_marshals_the_dict_and_ignores_unrelated_variables() -> None:
    variables = {
        "protocolo_reembolso": "REE-1",
        "data_atendimento": "2026-10-01",
        "data_solicitacao": "2026-10-04",
        "variavel_qualquer_que_nao_e_do_dataclass": "x",
    }
    assert check_coverage_entry(variables) == {"dias_desde_atendimento": 3}


def test_check_coverage_entry_aceita_pedido_sem_tuss_nem_cid() -> None:
    """O Lucas nao coleta TUSS nem CID: `codigo_procedimento_tuss` e opcional (contrato v0.2.0)."""
    assert _input().codigo_procedimento_tuss is None
    assert check_coverage_entry({"protocolo_reembolso": "REE-1"}) == {
        "dias_desde_atendimento": DIAS_NAO_CALCULAVEL
    }


# ---------------------------------------------------------------------------
# Boundary REACHABILITY (ADR-0030) — o boundary BE_ReembolsoProtocoloInvalido continua alcancavel
# ---------------------------------------------------------------------------


def _drive_check_coverage_failure(
    variables: dict[str, Any], *, allowlist: frozenset[str]
) -> tuple[list, list]:
    """Run check_coverage_entry through the real harness; return (bpmn_errors, failures)."""
    transport = FakeWorkerTransport()
    harness = WorkerHarness(
        transport,
        worker_id="reembolso-boundary-test",
        tenant="amh",
        audit_sink=FakeAuditSink(),
        bpmn_error_allowlist=allowlist,
    )
    harness.register_worker(FunctionWorker("operadora.reembolso.check_coverage", check_coverage_entry))
    task = ExternalTask(
        task_id="task-1",
        topic="operadora.reembolso.check_coverage",
        process_instance_id="proc-1",
        business_key="REEMB-amh-1",
        worker_id="reembolso-boundary-test",
        variables=variables,
    )
    asyncio.run(harness._handle(task))
    return transport.bpmn_errors, transport.failures


def test_check_coverage_reaches_boundary_when_allowlisted() -> None:
    bpmn_errors, failures = _drive_check_coverage_failure(
        {"protocolo_reembolso": ""}, allowlist=frozenset({ERR_REEMBOLSO_INVALID_PROTOCOLO})
    )
    assert bpmn_errors == [("task-1", ERR_REEMBOLSO_INVALID_PROTOCOLO, bpmn_errors[0][2])]
    assert failures == []


def test_check_coverage_demotes_to_incident_when_not_allowlisted() -> None:
    bpmn_errors, failures = _drive_check_coverage_failure({"protocolo_reembolso": ""}, allowlist=frozenset())
    assert bpmn_errors == []
    assert len(failures) == 1
    assert failures[0][0] == "task-1"


def test_the_error_allowlist_has_only_the_origin_guard() -> None:
    """A negativa (`ERR_REEMBOLSO_DENIAL_NOT_HUMAN`) NAO tem boundary modelado: fica incidente."""
    assert frozenset({ERR_REEMBOLSO_INVALID_PROTOCOLO}) == REEMBOLSO_BPMN_ERROR_ALLOWLIST


# ---------------------------------------------------------------------------
# request_documents / notify_sla_risk — registram a etapa, nao afirmam entrega
# ---------------------------------------------------------------------------


def test_request_documents_registra_a_etapa_sem_afirmar_entrega() -> None:
    assert request_documents("BEN-1", "REE-1") == {}
    assert request_documents_entry({"beneficiario_pseudo_id": "BEN-1", "protocolo_reembolso": "REE-1"}) == {}


def test_notify_sla_risk_registra_a_etapa_sem_afirmar_entrega() -> None:
    assert notify_sla_risk(_input()) == {}
    assert notify_sla_risk_entry({"protocolo_reembolso": "REE-1"}) == {}


# ---------------------------------------------------------------------------
# analyze_request — dossie instrui, nunca decide
# ---------------------------------------------------------------------------


def test_analyze_request_recomenda_sempre_analise_humana() -> None:
    result = analyze_request(_input(situacao_prazo="NAO_AVALIADO"))
    assert result["recomendacao_sugerida"] == "ANALISE_HUMANA"
    assert result["situacao_prazo"] == "NAO_AVALIADO"
    assert result["valor_solicitado_cents"] == 35000


def test_analyze_request_nao_carrega_fato_de_calculo_nem_de_teto() -> None:
    result = analyze_request_entry(
        {"protocolo_reembolso": "REE-1", "dentro_tabela": True, "dentro_teto_l2": True}
    )
    leaked = {"dentro_tabela", "dentro_teto_l2", "valor_calculado_tabela_cents", "requer_avaliacao_clinica"}
    assert not leaked & set(result)


# ---------------------------------------------------------------------------
# send_reembolso_denial — o guard L0 (ERR_REEMBOLSO_DENIAL_NOT_HUMAN)
# ---------------------------------------------------------------------------


def test_negar_com_decisao_humana_completa_valida() -> None:
    result = send_reembolso_denial(_denial())
    assert result["decisao_adversa_validada"] is True
    # F-009 (#1063): o worker so' registra; o aviso sai depois, pela tarefa de aviso do BPMN.
    assert result["denial_registered"] is True and result["denial_sent"] is False
    assert result["analista_id"] == "analista-01"


def test_o_worker_nao_afirma_ter_enviado_nada() -> None:
    """O aviso sai pelo Lucas (agent_resume). Este worker so VALIDA e registra: estado honesto (#1063)."""
    result = send_reembolso_denial(_denial())
    assert result["denial_sent"] is False
    assert result["denial_registered"] is True
    assert result["notification_status"] == "PENDING_CHANNEL"
    assert "notified" not in result


@pytest.mark.parametrize(
    "campo",
    ["justificativa", "fundamentacao_contratual", "texto_ao_beneficiario", "analista_id"],
)
def test_negar_sem_campo_obrigatorio_e_recusado(campo: str) -> None:
    with pytest.raises(ReembolsoDenialNotHumanError) as exc:
        send_reembolso_denial(_denial(**{campo: "  "}))
    assert exc.value.missing_fields


def test_texto_ao_beneficiario_e_obrigatorio_e_diferente_da_justificativa() -> None:
    """`justificativa`/`fundamentacao_contratual` sao internas; o que vai ao WhatsApp e
    `texto_ao_beneficiario`."""
    with pytest.raises(ReembolsoDenialNotHumanError) as exc:
        send_reembolso_denial(_denial(texto_ao_beneficiario=""))
    assert "texto_ao_beneficiario" in exc.value.missing_fields


def test_o_auditor_substitui_o_analista_como_responsavel() -> None:
    result = send_reembolso_denial(_denial(analista_id="", auditor_id="auditor-07"))
    assert result["analista_id"] == "auditor-07"


@pytest.mark.parametrize("decisao", ["APROVAR", "SOLICITAR_INFO", "SOLICITAR_AUDITOR", "", "negar"])
def test_so_negar_e_aprovar_parcial_passam_pelo_guard(decisao: str) -> None:
    with pytest.raises(ReembolsoDenialNotHumanError):
        send_reembolso_denial(_denial(decisao_reembolso=decisao))


def test_aprovar_parcial_exige_valor_menor_que_o_solicitado() -> None:
    ok = send_reembolso_denial(
        _denial(decisao_reembolso="APROVAR_PARCIAL", valor_reembolso_aprovado_cents=20000)
    )
    assert ok["valor_reembolso_aprovado_cents"] == 20000
    for aprovado in (35000, 40000, 0, -1):  # nao e reducao / nao positivo
        with pytest.raises(ReembolsoDenialNotHumanError):
            send_reembolso_denial(
                _denial(decisao_reembolso="APROVAR_PARCIAL", valor_reembolso_aprovado_cents=aprovado)
            )


@pytest.mark.parametrize("valor", [True, 350.0, "35000", None])
def test_dinheiro_nao_inteiro_nunca_chega_a_comparacao(valor: Any) -> None:
    """bool e subclasse de int; float/str/None nao sao centavos inteiros do motor."""
    with pytest.raises(ReembolsoDenialNotHumanError):
        send_reembolso_denial(_denial(decisao_reembolso="APROVAR_PARCIAL", valor_solicitado_cents=valor))


def test_send_reembolso_denial_entry_levanta_para_decisao_sem_humano() -> None:
    with pytest.raises(ReembolsoDenialNotHumanError):
        send_reembolso_denial_entry({"decisao_reembolso": "NEGAR"})


# ---------------------------------------------------------------------------
# publish_completed — convencao do registro de topicos
# ---------------------------------------------------------------------------


def test_publish_completed_carrega_o_desfecho() -> None:
    result = publish_completed(payload={"protocolo_reembolso": "REE-1"}, desfecho="aprovado_analista")
    assert result["topic"] == "agents.events.reembolso.completed"
    assert result["payload"]["desfecho"] == "aprovado_analista"
    assert (
        publish_completed_entry({"desfecho": "negado_analista"})["payload"]["desfecho"] == "negado_analista"
    )


# ---------------------------------------------------------------------------
# register_reembolso_workers — o registro bate com o BPMN, sem orfaos
# ---------------------------------------------------------------------------

_CONVENCAO = frozenset({"operadora.reembolso.publish_completed"})


def _bpmn_external_topics() -> set[str]:
    root = ET.parse(_BPMN_PATH).getroot()
    return {
        el.get(f"{_CAMUNDA_NS}topic", "")
        for el in root.iter()
        if el.get(f"{_CAMUNDA_NS}type") == "external"
        and el.get(f"{_CAMUNDA_NS}topic", "").startswith("operadora.reembolso.")
    }


def test_registered_topics_are_exactly_the_bpmn_topics_plus_the_publish_convention() -> None:
    harness = WorkerHarness(FakeWorkerTransport(), worker_id="test-worker")
    register_reembolso_workers(harness, FakeKafkaPublisher())
    registered = {t for t in harness.registered_topics if t.startswith("operadora.reembolso.")}
    assert registered == _bpmn_external_topics() | _CONVENCAO


def test_the_removed_workers_never_come_back_without_a_dl() -> None:
    """DL-0158: sem calculo, sem teto, sem pagamento pelo sistema, sem conferencia de prazo por maquina."""
    harness = WorkerHarness(FakeWorkerTransport(), worker_id="test-worker")
    register_reembolso_workers(harness, FakeKafkaPublisher())
    for topic in (
        "operadora.reembolso.calculate_amount",
        "operadora.reembolso.issue_payment",
        "operadora.reembolso.check_prazo",
        "operadora.reembolso.auto_approve_or_route",
        "operadora.reembolso.notify_beneficiario",
    ):
        assert topic not in harness.registered_topics, topic


@pytest.mark.parametrize(
    "nome",
    [
        "calculate_value",
        "calculate_amount_entry",
        "process_payment",
        "issue_payment_entry",
        "validate_reembolso",
        "check_prazo_entry",
        "REEMBOLSO_AUTO_PAGAMENTO_LIBERADO",
        "require_pagamento_autorizado",
        "ReembolsoCalculoDmn",
        "_CEILING_ACTION",
    ],
)
def test_the_automatic_money_path_is_gone_from_the_module(nome: str) -> None:
    assert not hasattr(reembolso, nome), f"{nome} voltou: o reembolso v2 nao calcula nem paga (DL-0158)"


# ---------------------------------------------------------------------------
# BPMN/DMN fences — todo pedido vai a analise humana
# ---------------------------------------------------------------------------


def _bpmn_ids() -> dict[str, ET.Element]:
    root = ET.parse(_BPMN_PATH).getroot()
    return {el.get("id", ""): el for el in root.iter() if el.get("id")}


def test_the_automatic_path_elements_are_gone_from_the_bpmn() -> None:
    ids = _bpmn_ids()
    for removido in (
        "BRT_Admissibilidade",
        "GW_Admissibilidade",
        "BRT_Calculo",
        "ST_CalculateAmount",
        "BRT_AutoApproval",
        "GW_AutoAprovacao",
        "ST_IssuePaymentAuto",
        "ST_IssuePaymentAnalista",
        "ST_IssuePaymentParcial",
        "End_ReembolsoAprovadoAutomatico",
        "ST_CheckPrazo",
    ):
        assert removido not in ids, f"{removido} voltou ao BPMN de reembolso (DL-0158)"


def test_every_path_to_a_terminal_crosses_a_human_task() -> None:
    """O unico caminho de Start ate a analise passa por UT_AnaliseReembolso; nenhum terminal de decisao
    e alcancavel sem humano."""
    root = ET.parse(_BPMN_PATH).getroot()
    flows = [(f.get("sourceRef"), f.get("targetRef")) for f in root.iter(f"{_BPMN_NS}sequenceFlow")]
    adj: dict[str, set[str]] = {}
    for src, dst in flows:
        adj.setdefault(src or "", set()).add(dst or "")
    human = {el.get("id") for el in root.iter(f"{_BPMN_NS}userTask")}
    terminais_de_decisao = {"End_ReembolsoAprovadoAnalista", "End_ReembolsoParcial", "End_ReembolsoNegado"}

    def alcanca(sem_humano: bool) -> set[str]:
        seen: set[str] = set()
        stack = ["Start_SolicitacaoReembolso"]
        while stack:
            n = stack.pop()
            if n in seen or (sem_humano and n in human):
                continue
            seen.add(n)
            stack.extend(adj.get(n, ()))
        return seen

    assert not terminais_de_decisao & alcanca(sem_humano=True)
    assert terminais_de_decisao <= alcanca(sem_humano=False)


def test_the_decision_gateway_has_no_default_so_an_unknown_decision_is_an_incident() -> None:
    gw = _bpmn_ids()["GW_DecisaoReembolso"]
    assert gw.get("default") is None


def test_the_payment_is_a_human_task_of_the_finance_group() -> None:
    ut = _bpmn_ids()["UT_RegistrarPagamento"]
    assert ut.tag == f"{_BPMN_NS}userTask"
    assert ut.get(f"{_CAMUNDA_NS}candidateGroups") == "financeiro-reembolso"


def test_every_beneficiary_notice_is_gated_on_the_whatsapp_flag_and_carries_no_text() -> None:
    """Aviso = FATO sem PHI: so tenant_id, agent_id, conversation_id e resultado viajam no evento."""
    root = ET.parse(_BPMN_PATH).getroot()
    avisos = [t for t in root.iter(f"{_BPMN_NS}serviceTask") if (t.get("id") or "").startswith("ST_Avisar")]
    resultados = set()
    for t in avisos:
        params = {p.get("name"): (p.text or "") for p in t.iter(f"{_CAMUNDA_NS}inputParameter")}
        assert params["event_topic"] == "agents.events.process_completed"
        assert params["event_payload_vars"] == "tenant_id,agent_id,conversation_id,resultado"
        resultados.add(params["resultado"])
    assert resultados == {
        "reembolso_aprovado",
        "reembolso_parcial",
        "reembolso_negado",
        "reembolso_info_solicitada",
        "reembolso_pago",
    }


def test_the_prazo_dmn_is_informative_only() -> None:
    """Sem linha DENTRO/FORA enquanto o prazo contratual nao for confirmado; nenhuma saida adversa."""
    ns = {"d": "https://www.omg.org/spec/DMN/20191111/MODEL/"}
    root = ET.parse(_DMN_DIR / "reembolso_prazo.dmn").getroot()
    outs = {o.text for o in root.iterfind(".//d:outputEntry/d:text", ns)}
    assert outs == {'"NAO_AVALIADO"'}
    assert not (_DMN_DIR / "reembolso_calculo.dmn").exists()
    assert not (_DMN_DIR / "reembolso_coverage.dmn").exists()

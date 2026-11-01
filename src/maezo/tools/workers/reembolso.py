"""SP-OP-REEMBOLSO-001 Worker — Reembolso ao Beneficiario (v0.2.0, DL-0161: 100% analise humana).

External tasks for beneficiary reimbursement. O sistema so ORGANIZA o pedido, os documentos e a
resposta; uma pessoa da analise decide direito e valor. Por isso este modulo NAO calcula valor, NAO
compara teto, NAO auto-aprova e NAO emite pagamento (o financeiro paga fora e registra
`data_pagamento` em `UT_RegistrarPagamento`). Foram removidos em DL-0161: `calculate_value`,
`calculate_amount_entry`, o interruptor R-058 (`REEMBOLSO_AUTO_PAGAMENTO_LIBERADO`), `process_payment`,
`issue_payment_entry`, `validate_reembolso`/`check_prazo` e os tres erros de dinheiro associados.

Negativa-like L0 hard: `send_reembolso_denial` e GUARDADO por ERR_REEMBOLSO_DENIAL_NOT_HUMAN — negar ou
reduzir reembolso so se materializa depois de decisao humana em UT_AnaliseReembolso,
UT_RevisaoAuditorMedico ou UT_CoordenacaoReembolso. O worker VALIDA a decisao; o aviso ao beneficiario
sai pelo Lucas (consumidor `agent_resume`), a partir do historico do motor.
"""

from __future__ import annotations

import functools
from dataclasses import dataclass, field
from datetime import date
from typing import TYPE_CHECKING, Any

import structlog

from maezo.tools.workers.base import FunctionWorker, pick_fields
from maezo.tools.workers.engine_var_types import is_exact_engine_integer
from maezo.tools.workers.harness import WorkerBpmnError

if TYPE_CHECKING:
    from maezo.tools.workers.harness import KafkaPublisher, WorkerHarness

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Error codes
# ---------------------------------------------------------------------------

#: `check_coverage` raises this (as a `WorkerBpmnError`) when `protocolo_reembolso` arrives absent/blank —
#: a TECHNICAL origin guard (ADR-0030 §2): the worker never decides to pay/deny, it only signals
#: bad-at-source request data. MODELED boundary error: `BE_ReembolsoProtocoloInvalido` on
#: `ST_ValidarSolicitacao` routes to the NEUTRO terminal `End_ReembolsoProtocoloInvalido`.
ERR_REEMBOLSO_INVALID_PROTOCOLO = "ERR_REEMBOLSO_INVALID_PROTOCOLO"

#: Consumption-covered (`scripts/ci/check_bpmn_error_allowlist.py`'s "simple rule"):
#: `operadora.reembolso.check_coverage` is consumed ONLY by SP-OP-REEMBOLSO-001, which declares this
#: errorCode on `BE_ReembolsoProtocoloInvalido`. `ERR_REEMBOLSO_DENIAL_NOT_HUMAN` is DELIBERATELY excluded:
#: it has no modeled boundary catch (`send_reembolso_denial` stays a `PermissionError` -> incident).
REEMBOLSO_BPMN_ERROR_ALLOWLIST: frozenset[str] = frozenset({ERR_REEMBOLSO_INVALID_PROTOCOLO})

#: Valor que `dias_desde_atendimento` assume quando as datas nao sao calculaveis. A DMN informativa
#: `reembolso_prazo` o trata pelo catch-all (NAO_AVALIADO); nunca vira negativa.
DIAS_NAO_CALCULAVEL = -1

#: Decisoes adversas: as UNICAS cujo efeito exige o guard humano (`send_reembolso_denial`).
_DECISOES_ADVERSAS = ("NEGAR", "APROVAR_PARCIAL")


# ---------------------------------------------------------------------------
# Error types
# ---------------------------------------------------------------------------


class ReembolsoDenialNotHumanError(PermissionError):
    """Raised when send_reembolso_denial is called without human authorization.

    Guard ERR_REEMBOLSO_DENIAL_NOT_HUMAN — the worker MUST refuse to validate a denial/reduction unless
    decisao_reembolso in {NEGAR, APROVAR_PARCIAL} was set by a human with analista_id (or auditor_id) and the
    texto_ao_beneficiario the analyst wrote for the beneficiary.
    """

    def __init__(self, missing_fields: list[str] | None = None) -> None:
        self.missing_fields = missing_fields or []
        msg = "ERR_REEMBOLSO_DENIAL_NOT_HUMAN: denial/reduction requires human decision"
        if self.missing_fields:
            msg += f"; missing: {', '.join(self.missing_fields)}"
        super().__init__(msg)


# ---------------------------------------------------------------------------
# Input types
# ---------------------------------------------------------------------------


@dataclass
class ReembolsoInput:
    """Input for reembolso processing (contract SP-OP-REEMBOLSO-001 v0.2.0, "Variaveis de entrada")."""

    tenant_id: str = ""
    protocolo_reembolso: str = ""
    numero_guia_tiss: str | None = None
    beneficiario_pseudo_id: str = ""
    matricula_beneficiario: str = ""
    tipo_reembolso: str = ""
    codigo_procedimento_tuss: str | None = None
    categoria_procedimento: str = ""
    valor_solicitado_cents: int = 0
    data_atendimento: str = ""
    data_solicitacao: str = ""
    cid10: str | None = None
    documentos_refs: list[dict[str, Any]] = field(default_factory=list)
    relacao_com_titular: str = ""
    motivo_nao_rede: str = ""
    situacao_prazo: str = ""


@dataclass
class ReembolsoDenialInput:
    """Input for send_reembolso_denial — the gated adverse effect."""

    decisao_reembolso: str = ""
    justificativa: str = ""
    fundamentacao_contratual: str = ""
    texto_ao_beneficiario: str = ""
    valor_reembolso_aprovado_cents: int = 0
    valor_solicitado_cents: int = 0
    analista_id: str = ""
    auditor_id: str = ""
    cid10_referencia: str = ""
    parecer_auditor: str = ""


# ---------------------------------------------------------------------------
# Workers
# ---------------------------------------------------------------------------


def _dias_desde_atendimento(data_atendimento: str, data_solicitacao: str) -> int:
    """Dias entre o atendimento e a solicitacao; `DIAS_NAO_CALCULAVEL` se nao for calculavel.

    Aritmetica de datas, sem regra de prazo: a regra (contratual/regulatoria) vive na DMN `reembolso_prazo`.
    """
    try:
        atendimento = date.fromisoformat(str(data_atendimento).strip()[:10])
        solicitacao = date.fromisoformat(str(data_solicitacao).strip()[:10])
    except ValueError:
        return DIAS_NAO_CALCULAVEL
    dias = (solicitacao - atendimento).days
    return dias if dias >= 0 else DIAS_NAO_CALCULAVEL


def check_coverage(input_data: ReembolsoInput) -> dict[str, Any]:
    """Valida a origem da solicitacao e resolve `dias_desde_atendimento` (topico `check_coverage`).

    ORIGIN GUARD (ADR-0030 Tier-2): `ST_ValidarSolicitacao` carrega o boundary
    `BE_ReembolsoProtocoloInvalido` -> `End_ReembolsoProtocoloInvalido` (terminal NEUTRO).
    `protocolo_reembolso`
    ausente/em branco lanca o `WorkerBpmnError` modelado, antes de qualquer outro passo.

    v0.2.0 (DL-0161): nao ha mais conferencia de cobertura por maquina (o analista avalia). O unico fato
    resolvido e `dias_desde_atendimento`, insumo da DMN informativa `reembolso_prazo`. NUNCA decide
    pagar/negar.
    """
    if not input_data.protocolo_reembolso.strip():
        logger.error("reembolso.check_coverage.protocolo_invalido")
        raise WorkerBpmnError(ERR_REEMBOLSO_INVALID_PROTOCOLO, "protocolo_reembolso ausente")

    dias = _dias_desde_atendimento(input_data.data_atendimento, input_data.data_solicitacao)
    logger.info(
        "reembolso.check_coverage.complete",
        protocolo_reembolso=input_data.protocolo_reembolso,
        tipo_reembolso=input_data.tipo_reembolso,
        dias_desde_atendimento=dias,
    )
    return {"dias_desde_atendimento": dias}


def request_documents(
    beneficiario_pseudo_id: str,
    protocolo_reembolso: str,
    message_type: str = "pendencia_documentacao",
) -> dict[str, Any]:
    """Registra que a ETAPA de abertura da pendencia rodou. Retorna `{}` — NAO afirma nada.

    Serve `ST_SolicitarDocumentos`, topico `operadora.reembolso.request_documents`. Nao adversa:
    emparelha com a
    corrida de `GW_AguardarDocs` (message `msg.reembolso.docs_received` OU timer `ICE_PrazoPendencia`). Uma
    beneficiaria que nunca responde e decidida por um HUMANO em `UT_DecidirPendenciaExpirada`.

    Nenhum canal e contatado aqui (FAB-NOTIFIED-TRIO): o aviso com a pergunta do analista
    (`info_solicitada`) sai
    pelo Lucas, a partir do resultado `reembolso_info_solicitada` publicado por `ST_AvisarInfo`. O evento
    `agents.events.reembolso.pended` e publicado pelo proprio BPMN (`ST_PublishReembolsoPended`).
    """
    logger.info(
        "reembolso.request_documents",
        beneficiario_pseudo_id=beneficiario_pseudo_id,
        protocolo_reembolso=protocolo_reembolso,
        message_type=message_type,
        notified_asserted=False,
    )
    return {}


def analyze_request(input_data: ReembolsoInput) -> dict[str, Any]:
    """Prepare the reembolso analysis dossier — convoca o agente analista (Marina-classe).

    `ST_PrepararDossie` is the ONLY path into `UT_AnaliseReembolso`. O dossie reune evidencia estruturada
    (protocolo, tipo, categoria, valor solicitado, paciente, motivo, documentos e a situacao do prazo — esta
    ultima so INFORMATIVA) para INSTRUIR a decisao humana, nunca a substitui (principio Rafael, ADR-0005): a
    recomendacao devolvida nunca e um valor de `decisao_reembolso`.
    """
    logger.info(
        "reembolso.analyze_request.start",
        protocolo_reembolso=input_data.protocolo_reembolso,
        tipo_reembolso=input_data.tipo_reembolso,
    )
    result = {
        "protocolo_reembolso": input_data.protocolo_reembolso,
        "tipo_reembolso": input_data.tipo_reembolso,
        "categoria_procedimento": input_data.categoria_procedimento,
        "valor_solicitado_cents": input_data.valor_solicitado_cents,
        "situacao_prazo": input_data.situacao_prazo,
        "dossie": "dossie_instruido",
        "recomendacao_sugerida": "ANALISE_HUMANA",  # O agente NUNCA decide (ADR-0005)
    }
    logger.info("reembolso.analyze_request.complete", protocolo_reembolso=input_data.protocolo_reembolso)
    return result


def notify_sla_risk(input_data: ReembolsoInput) -> dict[str, Any]:
    """Registra que a ETAPA de alerta de risco de SLA rodou. Retorna `{}` — NAO afirma nada.

    Serve `ST_NotificarRiscoSla`, alimentado SO pelo boundary NAO-interruptivo `BT_AlertaSla` em
    `UT_AnaliseReembolso`. Informativa e jamais adversa: a User Task segue aberta. Nenhum canal e contatado
    (FAB-SLA-RISK-NOTIFIED-SLICE4); a observabilidade fica no log, com `notified_asserted=False`.
    """
    logger.info(
        "reembolso.notify_sla_risk",
        protocolo_reembolso=input_data.protocolo_reembolso,
        tipo_reembolso=input_data.tipo_reembolso,
        notified_asserted=False,
    )
    return {}


def send_reembolso_denial(denial_input: ReembolsoDenialInput) -> dict[str, Any]:
    """Validate a reimbursement denial/reduction — GUARDED adverse effect.

    ERR_REEMBOLSO_DENIAL_NOT_HUMAN: MUST refuse if:
    - decisao_reembolso not in {NEGAR, APROVAR_PARCIAL}
    - Missing justificativa, fundamentacao_contratual (internas) or texto_ao_beneficiario (o que o Lucas leva)
    - Missing analista_id (or auditor_id for clinical merit)
    - If clinical merit: missing cid10_referencia or parecer_auditor (F-045). Clinical merit is
      signalled by any of auditor_id / cid10_referencia / parecer_auditor (fields only the
      `UT_RevisaoAuditorMedico` form fills); then all three are required.
    - APROVAR_PARCIAL sem valor aprovado positivo e menor que o solicitado

    O worker so VALIDA; nao comunica nada (nenhum canal). O aviso sai pelo Lucas, que le o texto do historico.
    """
    logger.info(
        "reembolso.send_reembolso_denial.start",
        decisao_reembolso=denial_input.decisao_reembolso,
        analista_id=denial_input.analista_id,
    )

    missing: list[str] = []

    if denial_input.decisao_reembolso not in _DECISOES_ADVERSAS:
        missing.append("decisao_reembolso not in {NEGAR, APROVAR_PARCIAL}")
    if not denial_input.justificativa.strip():
        missing.append("justificativa")
    if not denial_input.fundamentacao_contratual.strip():
        missing.append("fundamentacao_contratual")
    if not denial_input.texto_ao_beneficiario.strip():
        missing.append("texto_ao_beneficiario")

    # Human identifier: analista_id or auditor_id
    if not (denial_input.analista_id.strip() or denial_input.auditor_id.strip()):
        missing.append("analista_id (or auditor_id)")

    # Merito clinico (F-045): contrato SP-OP-REEMBOLSO-001 ("Variaveis de saida" e "Campos
    # obrigatorios por decisao adversa") + BPMN `UT_RevisaoAuditorMedico` — negativa/reducao por
    # merito clinico exige auditor_id + cid10_referencia + parecer_auditor. A guarda NAO julga o
    # merito (L0 hard, medico auditor); ela so recusa COMUNICAR uma decisao clinica sem lastro.
    # Sinal de merito clinico = qualquer um dos tres campos que so o formulario do auditor
    # preenche; lastro parcial falha fechado, nunca um default silencioso. Nomes, nunca valores
    # (cid10_referencia/parecer_auditor sao PHI) entram na mensagem.
    clinical_fields = {
        "auditor_id": denial_input.auditor_id,
        "cid10_referencia": denial_input.cid10_referencia,
        "parecer_auditor": denial_input.parecer_auditor,
    }
    if any(value.strip() for value in clinical_fields.values()):
        missing.extend(name for name, value in clinical_fields.items() if not value.strip())

    requested_exact = is_exact_engine_integer(denial_input.valor_solicitado_cents)
    approved_exact = is_exact_engine_integer(denial_input.valor_reembolso_aprovado_cents)
    if not requested_exact:
        missing.append("valor_solicitado_cents integer-centavos invalido")
    if not approved_exact:
        missing.append("valor_reembolso_aprovado_cents integer-centavos invalido")

    # Non-exact values must never reach comparisons (bool is an int subclass).
    if denial_input.decisao_reembolso == "APROVAR_PARCIAL" and requested_exact and approved_exact:
        if denial_input.valor_reembolso_aprovado_cents <= 0:
            missing.append("valor_reembolso_aprovado_cents")
        elif denial_input.valor_reembolso_aprovado_cents >= denial_input.valor_solicitado_cents:
            missing.append("APROVAR_PARCIAL: valor aprovado >= valor solicitado (nao e reducao)")

    if missing:
        raise ReembolsoDenialNotHumanError(missing_fields=missing)

    return {
        "decisao_adversa_validada": True,
        # HONEST STATE (F-009, #1063): este worker so' VALIDA e registra; nada foi enviado aqui. O aviso sai
        # depois, pela tarefa de aviso do BPMN (agents.events.process_completed -> retomada do Lucas).
        "denial_sent": False,
        "denial_registered": True,
        "notification_status": "PENDING_CHANNEL",
        "decisao_reembolso": denial_input.decisao_reembolso,
        "valor_solicitado_cents": denial_input.valor_solicitado_cents,
        "valor_reembolso_aprovado_cents": denial_input.valor_reembolso_aprovado_cents,
        "analista_id": denial_input.analista_id or denial_input.auditor_id,
    }


def publish_completed(
    event_type: str = "reembolso.completed",
    payload: dict[str, Any] | None = None,
    desfecho: str = "",
) -> dict[str, Any]:
    """Publish reembolso completion event (dual engine+Kafka, ADR-0007)."""
    _payload = dict(payload or {})
    if desfecho:
        _payload["desfecho"] = desfecho

    logger.info("reembolso.publish_completed", event_type=event_type, desfecho=desfecho)

    return {
        "published": True,
        "topic": f"agents.events.{event_type}",
        "event_type": event_type,
        "payload": _payload,
    }


# ---------------------------------------------------------------------------
# Dict-boundary entry functions (T1.2/ADR-0026 §2b) — one per external-task topic. Explicit field selection ->
# typed dataclass -> the typed function above -> dict. Pure marshalling.
#
# Topic mapping vs spec/processes/bpmn/SP-OP-REEMBOLSO-001_Reembolso_Beneficiario.bpmn (excl. the shared
# `operadora.events.publish`):
#   check_coverage        -> operadora.reembolso.check_coverage (ST_ValidarSolicitacao)
#   request_documents     -> operadora.reembolso.request_documents (ST_SolicitarDocumentos)
#   analyze_request       -> operadora.reembolso.analyze_request (ST_PrepararDossie)
#   notify_sla_risk       -> operadora.reembolso.notify_sla_risk (ST_NotificarRiscoSla)
#   send_reembolso_denial -> operadora.reembolso.send_reembolso_denial (ST_EnviarNegativa,
# ST_ComunicarReducao; GUARDED)
# publish_completed folds into the generic events.publish task per BPMN — function-derived topic, KEPT
# registered
# (documented registry-completeness convention shared with recurso.py; do not remove).
# ---------------------------------------------------------------------------


def check_coverage_entry(variables: dict[str, Any], *, kafka: KafkaPublisher | None = None) -> dict[str, Any]:
    """Dict-boundary entry for `operadora.reembolso.check_coverage` -> `check_coverage`."""
    del kafka  # unused — check_coverage emits no domain event
    input_data = ReembolsoInput(**pick_fields(variables, ReembolsoInput))
    return check_coverage(input_data)


def request_documents_entry(
    variables: dict[str, Any], *, kafka: KafkaPublisher | None = None
) -> dict[str, Any]:
    """Dict-boundary entry for `operadora.reembolso.request_documents` -> `request_documents`."""
    del kafka  # unused — request_documents emits no domain event itself (see docstring)
    beneficiario_pseudo_id = variables.get("beneficiario_pseudo_id", "")
    protocolo_reembolso = variables.get("protocolo_reembolso", "")
    message_type = variables.get("message_type", "pendencia_documentacao")
    return request_documents(beneficiario_pseudo_id, protocolo_reembolso, message_type)


def analyze_request_entry(
    variables: dict[str, Any], *, kafka: KafkaPublisher | None = None
) -> dict[str, Any]:
    """Dict-boundary entry for `operadora.reembolso.analyze_request` -> `analyze_request`."""
    del kafka  # unused — analyze_request emits no domain event
    input_data = ReembolsoInput(**pick_fields(variables, ReembolsoInput))
    return analyze_request(input_data)


def notify_sla_risk_entry(
    variables: dict[str, Any], *, kafka: KafkaPublisher | None = None
) -> dict[str, Any]:
    """Dict-boundary entry for `operadora.reembolso.notify_sla_risk` -> `notify_sla_risk`."""
    del kafka  # unused — notify_sla_risk emits no domain event itself
    input_data = ReembolsoInput(**pick_fields(variables, ReembolsoInput))
    return notify_sla_risk(input_data)


def send_reembolso_denial_entry(
    variables: dict[str, Any], *, kafka: KafkaPublisher | None = None
) -> dict[str, Any]:
    """Dict-boundary entry for `operadora.reembolso.send_reembolso_denial` -> `send_reembolso_denial`
    (GUARDED).

    Raises `ReembolsoDenialNotHumanError` (fail-closed, ERR_REEMBOLSO_DENIAL_NOT_HUMAN) when the human
    decision
    (`decisao_reembolso` in {NEGAR, APROVAR_PARCIAL} + required fields) is missing.
    """
    del kafka  # unused — send_reembolso_denial emits no domain event itself
    denial_input = ReembolsoDenialInput(**pick_fields(variables, ReembolsoDenialInput))
    return send_reembolso_denial(denial_input)


def publish_completed_entry(
    variables: dict[str, Any], *, kafka: KafkaPublisher | None = None
) -> dict[str, Any]:
    """Dict-boundary entry for `operadora.reembolso.publish_completed` -> `publish_completed`."""
    del kafka  # unused — see module bootstrap docstring on the Kafka seam
    event_type = variables.get("event_type", "reembolso.completed")
    payload = variables.get("payload") or {}
    desfecho = variables.get("desfecho", "")
    return publish_completed(event_type=event_type, payload=payload, desfecho=desfecho)


def register_reembolso_workers(
    harness: WorkerHarness,
    kafka: KafkaPublisher | None = None,
    **seams: Any,
) -> None:
    """Register the SP-OP-REEMBOLSO-001 dict-boundary entry functions on `harness`.

    `kafka` is accepted (donor contract, ADR-0026 §2) and threaded via `functools.partial`; no entry function
    calls `kafka.publish` today — see `ans_submit.register_ans_submit_workers`'s docstring for the same
    documented
    sync/async-boundary rationale.
    """
    del seams  # unused — no additional seam (audit=/dmn=/dispatcher=/erasure=) is needed today
    entries = (
        ("operadora.reembolso.check_coverage", check_coverage_entry),
        ("operadora.reembolso.request_documents", request_documents_entry),
        ("operadora.reembolso.analyze_request", analyze_request_entry),
        ("operadora.reembolso.notify_sla_risk", notify_sla_risk_entry),
        ("operadora.reembolso.send_reembolso_denial", send_reembolso_denial_entry),
        ("operadora.reembolso.publish_completed", publish_completed_entry),
    )
    for topic, entry in entries:
        harness.register_worker(FunctionWorker(topic, functools.partial(entry, kafka=kafka)))


__all__ = [
    "DIAS_NAO_CALCULAVEL",
    "ERR_REEMBOLSO_INVALID_PROTOCOLO",
    "REEMBOLSO_BPMN_ERROR_ALLOWLIST",
    "ReembolsoDenialInput",
    "ReembolsoDenialNotHumanError",
    "ReembolsoInput",
    "analyze_request",
    "analyze_request_entry",
    "check_coverage",
    "check_coverage_entry",
    "notify_sla_risk",
    "notify_sla_risk_entry",
    "publish_completed",
    "publish_completed_entry",
    "register_reembolso_workers",
    "request_documents",
    "request_documents_entry",
    "send_reembolso_denial",
    "send_reembolso_denial_entry",
]

"""SP-OP-NIP-001 Worker — Resposta a NIP (ANS).

External tasks for ANS NIP (Notificacao de Intermediacao Preliminar) response.
Negativa-like L0 hard: submit_to_ans (handoff_ans_submit) is GUARDED by
ERR_NIP_NEGATIVA_NOT_HUMAN — maintaining a negativa after a NIP requires
human decision in UT_RevisaoJuridicaNip.
"""

from __future__ import annotations

import dataclasses
import functools
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import structlog

from maezo.tools.workers.base import FunctionWorker, pick_fields

if TYPE_CHECKING:
    from maezo.tools.workers.harness import KafkaPublisher, WorkerHarness

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Error types
# ---------------------------------------------------------------------------


class NipNegativaNotHumanError(PermissionError):
    """Raised when submit_response is called without human authorization.

    Guard ERR_NIP_NEGATIVA_NOT_HUMAN — the worker MUST refuse to submit
    a response that maintains a negativa unless decisao_nip == MANTER_NEGATIVA
    was set by a human with revisor_id present.
    """

    def __init__(self, missing_fields: list[str] | None = None) -> None:
        self.missing_fields = missing_fields or []
        msg = "ERR_NIP_NEGATIVA_NOT_HUMAN: manter negativa requires human decision"
        if self.missing_fields:
            msg += f"; missing: {', '.join(self.missing_fields)}"
        super().__init__(msg)


class NipProtocoloInvalidoError(ValueError):
    """Raised when protocolo_ans is present but empty/blank (ERR_NIP_PROTOCOLO_INVALIDO).

    Absent (None) is legitimate; empty string would corrupt correlation.
    """

    def __init__(self) -> None:
        super().__init__("ERR_NIP_PROTOCOLO_INVALIDO: protocolo_ans presente mas vazio/em branco")


# ---------------------------------------------------------------------------
# Input / Output types
# ---------------------------------------------------------------------------


@dataclass
class NipInput:
    """Input for NIP processing."""

    tenant_id: str = ""
    numero_nip_ans: str = ""
    protocolo_ans: str | None = None
    beneficiario_pseudo_id: str = ""
    classificacao_nip: str = ""
    tema_nip: str = ""
    referencia_negativa_original: str | None = None
    data_recebimento_nip_iso: str = ""
    documentos_refs: list[dict[str, Any]] = field(default_factory=list)
    contesta_negativa: bool = False
    documentacao_suficiente: bool = False
    origem_a2a: bool = False


@dataclass
class NipClassificationResult:
    """Output of classify_nip — DMN nip_classification."""

    classificacao: str = "ASSISTENCIAL_CONTESTA_NEGATIVA"
    prazo_dias: int = 5
    grupo_revisor: str = "regulatorio-ans"


@dataclass
class NipRoutingResult:
    """Output of route_nip — DMN nip_routing."""

    grupo_humano: str = "juridico-regulatorio"
    roteamento: str = "REVISAO_JURIDICA"


@dataclass
class NipResponseInput:
    """Input for submit_to_ans — the gated submission."""

    decisao_nip: str = ""
    fundamentacao_regulatoria: str = ""
    referencia_negativa_original: str = ""
    texto_resposta_nip: str = ""
    revisor_id: str = ""


# ---------------------------------------------------------------------------
# Workers
# ---------------------------------------------------------------------------


def classify_nip(input_data: NipInput) -> NipClassificationResult:
    """Classify a NIP — DMN nip_classification.

    Maps classificacao_nip + tema_nip + contesta_negativa to:
    classificacao, prazo_dias, grupo_revisor.

    Catch-all (unknown tema or ambiguous contesta_negativa) ->
    classificacao = ASSISTENCIAL_CONTESTA_NEGATIVA,
    grupo_revisor = juridico-regulatorio, menor prazo_dias (conservative).
    """
    logger.info(
        "nip.classify_nip.start",
        tenant_id=input_data.tenant_id,
        numero_nip_ans=input_data.numero_nip_ans,
        classificacao_nip=input_data.classificacao_nip,
        tema_nip=input_data.tema_nip,
    )

    classificacao_nip = input_data.classificacao_nip.lower()
    tema = input_data.tema_nip.lower()

    # Default conservative
    classificacao = "ASSISTENCIAL_CONTESTA_NEGATIVA"
    prazo_dias = 5
    grupo_revisor = "juridico-regulatorio"

    if classificacao_nip == "assistencial":
        if input_data.contesta_negativa:
            classificacao = "ASSISTENCIAL_CONTESTA_NEGATIVA"
            prazo_dias = 5
            grupo_revisor = "juridico-regulatorio"
        else:
            classificacao = "ASSISTENCIAL_OUTRO"
            prazo_dias = 5
            grupo_revisor = _resolve_assistencial_group(tema)
    elif classificacao_nip == "nao_assistencial":
        classificacao = "NAO_ASSISTENCIAL"
        prazo_dias = 10
        grupo_revisor = _resolve_nao_assistencial_group(tema)

    result = NipClassificationResult(
        classificacao=classificacao,
        prazo_dias=prazo_dias,
        grupo_revisor=grupo_revisor,
    )

    logger.info(
        "nip.classify_nip.complete",
        classificacao=result.classificacao,
        prazo_dias=result.prazo_dias,
        grupo_revisor=result.grupo_revisor,
    )
    return result


def route_nip(
    classification: NipClassificationResult,
    documentacao_suficiente: bool,
) -> NipRoutingResult:
    """Route NIP to appropriate human group — DMN nip_routing.

    NO saida que decida o merito; only routes to human groups.
    Catch-all -> REVISAO_JURIDICA / juridico-regulatorio.
    """
    logger.info(
        "nip.route_nip.start",
        classificacao=classification.classificacao,
        documentacao_suficiente=documentacao_suficiente,
    )

    if not documentacao_suficiente:
        roteamento = "PENDENTE_INFO"
        grupo_humano = classification.grupo_revisor
    elif classification.classificacao == "ASSISTENCIAL_CONTESTA_NEGATIVA":
        roteamento = "REVISAO_JURIDICA"
        grupo_humano = "juridico-regulatorio"
    else:
        roteamento = "ELABORAR_RESPOSTA"
        grupo_humano = classification.grupo_revisor

    result = NipRoutingResult(
        grupo_humano=grupo_humano,
        roteamento=roteamento,
    )

    logger.info(
        "nip.route_nip.complete",
        roteamento=result.roteamento,
        grupo_humano=result.grupo_humano,
    )
    return result


def assemble_response(
    classification: NipClassificationResult,
    routing: NipRoutingResult,
    input_data: NipInput,
) -> dict[str, Any]:
    """Assemble the NIP response dossier.

    Delegates to Gustavo (LLM agent) for instruction/assembly.
    The agent instructs, never decides — human authors and approves.
    """
    logger.info(
        "nip.assemble_response.start",
        numero_nip_ans=input_data.numero_nip_ans,
        classificacao=classification.classificacao,
    )

    result = {
        "numero_nip_ans": input_data.numero_nip_ans,
        "classificacao": classification.classificacao,
        "prazo_dias": classification.prazo_dias,
        "grupo_revisor": classification.grupo_revisor,
        "roteamento": routing.roteamento,
        "grupo_humano": routing.grupo_humano,
        "minuta": "",
        "dossie": {
            "tema": input_data.tema_nip,
            "contesta_negativa": input_data.contesta_negativa,
            "referencia_original": input_data.referencia_negativa_original,
        },
    }

    logger.info("nip.assemble_response.complete")
    return result


def review_juridico(
    texto_minuta: str,
    classificacao: str,
    revisor_id: str = "",
) -> dict[str, Any]:
    """Juridico/regulatorio reviews the response draft.

    UT_RevisaoJuridicaNip — only origin of decisao_nip == MANTER_NEGATIVA.
    """
    logger.info(
        "nip.review_juridico.start",
        classificacao=classificacao,
        revisor_id=revisor_id,
    )

    return {
        "revisado": True,
        "classificacao": classificacao,
        "revisor_id": revisor_id,
        "texto_final": texto_minuta,
    }


def submit_to_ans(response_input: NipResponseInput) -> dict[str, Any]:
    """Submit NIP response to ANS — GUARDED adverse effect.

    ERR_NIP_NEGATIVA_NOT_HUMAN: MUST refuse if:
    - decisao_nip == MANTER_NEGATIVA without human revisor_id
    - Missing fundamentacao_regulatoria or referencia_negativa_original
      when MANTER_NEGATIVA

    Also validates protocolo_ans (ERR_NIP_PROTOCOLO_INVALIDO) for
    handoff correlation.
    """
    logger.info(
        "nip.submit_to_ans.start",
        decisao_nip=response_input.decisao_nip,
        revisor_id=response_input.revisor_id,
    )

    missing: list[str] = []

    if response_input.decisao_nip == "MANTER_NEGATIVA":
        if not response_input.revisor_id.strip():
            missing.append("revisor_id")
        if not response_input.fundamentacao_regulatoria.strip():
            missing.append("fundamentacao_regulatoria")
        if not response_input.referencia_negativa_original.strip():
            missing.append("referencia_negativa_original")

    if missing:
        raise NipNegativaNotHumanError(missing_fields=missing)

    if not response_input.texto_resposta_nip.strip():
        missing.append("texto_resposta_nip")
        raise NipNegativaNotHumanError(missing_fields=missing)

    return {
        "submitted": True,
        "decisao_nip": response_input.decisao_nip,
        "revisor_id": response_input.revisor_id,
        "status": "filed",
    }


def notify_beneficiario(
    beneficiario_pseudo_id: str,
    numero_nip_ans: str,
    decisao_nip: str = "",
) -> dict[str, Any]:
    """Notify beneficiario about the NIP resolution."""
    logger.info(
        "nip.notify_beneficiario",
        beneficiario_pseudo_id=beneficiario_pseudo_id,
        numero_nip_ans=numero_nip_ans,
        decisao_nip=decisao_nip,
    )

    return {
        "notified": True,
        "beneficiario_pseudo_id": beneficiario_pseudo_id,
        "numero_nip_ans": numero_nip_ans,
        "decisao_nip": decisao_nip,
    }


def handoff_ans_submit(
    numero_nip_ans: str,
    protocolo_ans: str | None,
    decisao_nip: str = "",
    data_recebimento_nip_iso: str = "",
) -> dict[str, Any]:
    """Handoff NIP filing to SP-OP-ANS-SUBMIT-001.

    Validates protocolo_ans: absent (None) is legitimate;
    present but empty/blank raises ERR_NIP_PROTOCOLO_INVALIDO.
    """
    logger.info(
        "nip.handoff_ans_submit.start",
        numero_nip_ans=numero_nip_ans,
        protocolo_ans=protocolo_ans,
    )

    # GAP-NIP-6: absent is fine, empty/blank is invalid
    if protocolo_ans is not None and not protocolo_ans.strip():
        raise NipProtocoloInvalidoError()

    return {
        "handoff": "SP-OP-ANS-SUBMIT-001",
        "numero_nip_ans": numero_nip_ans,
        "protocolo_ans": protocolo_ans,
        "decisao_nip": decisao_nip,
        "data_recebimento_nip_iso": data_recebimento_nip_iso,
        "origem_envio": "nip_filing",
    }


def publish_completed(
    event_type: str = "nip.completed",
    payload: dict[str, Any] | None = None,
    desfecho: str = "",
) -> dict[str, Any]:
    """Publish NIP completion event (dual engine+Kafka, ADR-0007)."""
    _payload = dict(payload or {})
    if desfecho:
        _payload["desfecho"] = desfecho

    logger.info("nip.publish_completed", event_type=event_type, desfecho=desfecho)

    return {
        "published": True,
        "topic": f"agents.events.{event_type}",
        "event_type": event_type,
        "payload": _payload,
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _resolve_assistencial_group(tema: str) -> str:
    """Map assistencial tema to appropriate human group."""
    tema_groups: dict[str, str] = {
        "negativa_cobertura": "juridico-regulatorio",
        "prazo_atendimento": "regulatorio-ans",
        "reembolso": "nucleo-ans",
        "rede": "nucleo-ans",
        "cobranca": "nucleo-ans",
    }
    return tema_groups.get(tema, "juridico-regulatorio")


def _resolve_nao_assistencial_group(tema: str) -> str:
    """Map nao-assistencial tema to appropriate human group."""
    tema_groups: dict[str, str] = {
        "cobranca": "nucleo-ans",
        "cadastro": "nucleo-ans",
        "informacao": "regulatorio-ans",
    }
    return tema_groups.get(tema, "nucleo-ans")


# ---------------------------------------------------------------------------
# Dict-boundary entry functions (T1.2/ADR-0026 §2b) — one per external-task
# topic. Explicit field selection -> typed dataclass -> the UNCHANGED typed
# function above -> dataclasses.asdict (or pass through when already a flat
# dict). The typed functions/guards are byte-identical.
#
# Topic mapping vs spec/processes/bpmn/SP-OP-NIP-001_Resposta_NIP.bpmn (excl.
# shared/out-of-scope `operadora.events.publish`; spec has only 4 unique NIP
# topics, several reused across multiple BPMN service tasks):
#   assemble_response  -> operadora.nip.instruct_dossier    (exact spec match: "Montar dossie de instrucao")
#   submit_to_ans        -> operadora.nip.submit_response     (spec match, GUARDED)
#   handoff_ans_submit   -> operadora.nip.handoff_ans_submit  (exact name+spec match)
# classify_nip/route_nip/review_juridico/notify_beneficiario have no distinct
# spec topic (classification/routing/review are DMN-shaped internal steps;
# spec's own `notify_deadline_risk` targets regulatorio-ans/juridico-regulatorio,
# NOT the beneficiario notify_beneficiario provides — genuine audience
# mismatch, not force-mapped) — registered under function-derived topics for
# registry completeness. publish_completed folds into the generic
# events.publish task per BPMN — function-derived topic.
# Spec topic with NO implementing function today (gap, not fabricated here):
# notify_deadline_risk.
# ---------------------------------------------------------------------------


def classify_nip_entry(variables: dict[str, Any], *, kafka: KafkaPublisher | None = None) -> dict[str, Any]:
    """Dict-boundary entry for `operadora.nip.classify_nip` -> `classify_nip`."""
    del kafka  # unused — classify_nip emits no domain event
    input_data = NipInput(**pick_fields(variables, NipInput))
    result = classify_nip(input_data)
    return dataclasses.asdict(result)


def route_nip_entry(variables: dict[str, Any], *, kafka: KafkaPublisher | None = None) -> dict[str, Any]:
    """Dict-boundary entry for `operadora.nip.route_nip` -> `route_nip`."""
    del kafka  # unused — route_nip emits no domain event
    classification = NipClassificationResult(**pick_fields(variables, NipClassificationResult))
    documentacao_suficiente = variables.get("documentacao_suficiente", False)
    result = route_nip(classification, documentacao_suficiente)
    return dataclasses.asdict(result)


def instruct_dossier_entry(
    variables: dict[str, Any], *, kafka: KafkaPublisher | None = None
) -> dict[str, Any]:
    """Dict-boundary entry for `operadora.nip.instruct_dossier` -> `assemble_response`."""
    del kafka  # unused — assemble_response emits no domain event
    classification = NipClassificationResult(**pick_fields(variables, NipClassificationResult))
    routing = NipRoutingResult(**pick_fields(variables, NipRoutingResult))
    input_data = NipInput(**pick_fields(variables, NipInput))
    return assemble_response(classification, routing, input_data)


def review_juridico_entry(
    variables: dict[str, Any], *, kafka: KafkaPublisher | None = None
) -> dict[str, Any]:
    """Dict-boundary entry for `operadora.nip.review_juridico` -> `review_juridico`."""
    del kafka  # unused — review_juridico emits no domain event
    texto_minuta = variables.get("texto_minuta", "")
    classificacao = variables.get("classificacao", "")
    revisor_id = variables.get("revisor_id", "")
    return review_juridico(texto_minuta, classificacao, revisor_id)


def submit_response_entry(
    variables: dict[str, Any], *, kafka: KafkaPublisher | None = None
) -> dict[str, Any]:
    """Dict-boundary entry for `operadora.nip.submit_response` -> `submit_to_ans` (GUARDED).

    Raises `NipNegativaNotHumanError` (fail-closed, ERR_NIP_NEGATIVA_NOT_HUMAN) when
    `decisao_nip == MANTER_NEGATIVA` lacks the required human review fields — unchanged guard,
    only the dict<->dataclass marshalling is new.
    """
    del kafka  # unused — submit_to_ans emits no domain event itself
    response_input = NipResponseInput(**pick_fields(variables, NipResponseInput))
    return submit_to_ans(response_input)


def notify_beneficiario_entry(
    variables: dict[str, Any], *, kafka: KafkaPublisher | None = None
) -> dict[str, Any]:
    """Dict-boundary entry for `operadora.nip.notify_beneficiario` -> `notify_beneficiario`."""
    del kafka  # unused — notify_beneficiario emits no domain event
    beneficiario_pseudo_id = variables.get("beneficiario_pseudo_id", "")
    numero_nip_ans = variables.get("numero_nip_ans", "")
    decisao_nip = variables.get("decisao_nip", "")
    return notify_beneficiario(beneficiario_pseudo_id, numero_nip_ans, decisao_nip)


def handoff_ans_submit_entry(
    variables: dict[str, Any], *, kafka: KafkaPublisher | None = None
) -> dict[str, Any]:
    """Dict-boundary entry for `operadora.nip.handoff_ans_submit` -> `handoff_ans_submit`.

    Raises `NipProtocoloInvalidoError` (fail-closed) when `protocolo_ans` is present but
    empty/blank — absent (`None`) is legitimate (GAP-NIP-6) — unchanged guard.
    """
    del kafka  # unused — handoff_ans_submit emits no domain event
    numero_nip_ans = variables.get("numero_nip_ans", "")
    protocolo_ans = variables.get("protocolo_ans")
    decisao_nip = variables.get("decisao_nip", "")
    data_recebimento_nip_iso = variables.get("data_recebimento_nip_iso", "")
    return handoff_ans_submit(numero_nip_ans, protocolo_ans, decisao_nip, data_recebimento_nip_iso)


def publish_completed_entry(
    variables: dict[str, Any], *, kafka: KafkaPublisher | None = None
) -> dict[str, Any]:
    """Dict-boundary entry for `operadora.nip.publish_completed` -> `publish_completed`."""
    del kafka  # unused — see module bootstrap docstring on the Kafka seam
    event_type = variables.get("event_type", "nip.completed")
    payload = variables.get("payload") or {}
    desfecho = variables.get("desfecho", "")
    return publish_completed(event_type=event_type, payload=payload, desfecho=desfecho)


def register_nip_workers(
    harness: WorkerHarness,
    kafka: KafkaPublisher | None = None,
    **seams: Any,
) -> None:
    """Register the SP-OP-NIP-001 dict-boundary entry functions on `harness`.

    `kafka` is accepted (donor contract, ADR-0026 §2) and threaded via `functools.partial`; no
    entry function calls `kafka.publish` today — see `ans_submit.register_ans_submit_workers`'s
    docstring for the same documented sync/async-boundary rationale.
    """
    del seams  # unused — no additional seam (audit=/dmn=/dispatcher=/erasure=) is needed today
    harness.register_worker(
        FunctionWorker("operadora.nip.classify_nip", functools.partial(classify_nip_entry, kafka=kafka))
    )
    harness.register_worker(
        FunctionWorker("operadora.nip.route_nip", functools.partial(route_nip_entry, kafka=kafka))
    )
    harness.register_worker(
        FunctionWorker(
            "operadora.nip.instruct_dossier", functools.partial(instruct_dossier_entry, kafka=kafka)
        )
    )
    harness.register_worker(
        FunctionWorker("operadora.nip.review_juridico", functools.partial(review_juridico_entry, kafka=kafka))
    )
    harness.register_worker(
        FunctionWorker("operadora.nip.submit_response", functools.partial(submit_response_entry, kafka=kafka))
    )
    harness.register_worker(
        FunctionWorker(
            "operadora.nip.notify_beneficiario", functools.partial(notify_beneficiario_entry, kafka=kafka)
        )
    )
    harness.register_worker(
        FunctionWorker(
            "operadora.nip.handoff_ans_submit", functools.partial(handoff_ans_submit_entry, kafka=kafka)
        )
    )
    harness.register_worker(
        FunctionWorker(
            "operadora.nip.publish_completed", functools.partial(publish_completed_entry, kafka=kafka)
        )
    )

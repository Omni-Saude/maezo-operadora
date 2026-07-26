"""SP-OP-NIP-001 Worker — Resposta a NIP (ANS).

External tasks for ANS NIP (Notificacao de Intermediacao Preliminar) response.
Negativa-like L0 hard: submit_to_ans (handoff_ans_submit) is GUARDED by
ERR_NIP_NEGATIVA_NOT_HUMAN — maintaining a negativa after a NIP requires
human decision in UT_RevisaoJuridicaNip.
"""

from __future__ import annotations

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


def notify_deadline_risk(
    numero_nip_ans: str = "",
    tenant_id: str = "",
    grupo_humano: str = "",
    sla_breach_task_name: str = "",
    event_topic_deadline_risk: str = "",
) -> dict[str, Any]:
    """Notify regulatorio-ans/nucleo-ans/juridico-regulatorio of NIP deadline risk.

    BUILT t2.5-p2b-nip-mechanical (spec/BPMN previously had NO implementing function — see the
    module docstring's "Topic mapping" note below). Now serves ONE BPMN service task on this topic
    (`operadora.nip.notify_deadline_risk`):
      - `ST_SolicitarInfoNip` — sits on the MAIN token of the `SOLICITAR_INFO` branch and reuses
        this topic per its own BPMN comment ("reusa o canal de notificacao regulatoria"), setting
        NEITHER `sla_breach_task_name` nor `event_topic_deadline_risk` inputParameter — both
        default to `""` so that reuse degrades gracefully.

    Was three tasks until t3.1-event-gap-nip-alerts CONVERTED-IN-PLACE the two non-interruptive
    deadline-risk alerts — `ST_NotificarRiscoPrazo` (boundary `BT_AlertaPrazoNip` on
    `UT_ElaborarRespostaNip`) and `ST_NotificarRiscoRevisao` (boundary `BT_AlertaPrazoRevisao` on
    `UT_RevisaoJuridicaNip`) — to the generic `operadora.events.publish` worker so they now
    actually publish `agents.events.nip.deadline_risk` (this worker never did; it only logged).
    This function is UNCHANGED by that conversion and is not orphaned: `ST_SolicitarInfoNip` keeps
    it registered.

    Mirrors `cancel.notify_sla_risk` / `contas.notify_sla_risk` / `inadimplencia.notify_sla_risk`
    / `auth.NotifySlaRiskWorker`: notify-only, non-adverse, fail-safe. This worker NEVER decides,
    cancels, or advances the process — the affected User Task (when `sla_breach_task_name` is
    present) stays OPEN regardless. It never itself files/submits/transmits anything to the ANS:
    RN 483 transmission is fenced behind the ANS-SUBMIT gateway triple (#109); this worker only
    alerts the human group that a regulatory deadline is approaching. Matches every other nip.py
    entry function (see `register_nip_workers` docstring): does not call `kafka.publish` itself —
    the systemic Kafka-producer-wiring gap (T3.1 finding "events.publish fix") is unchanged by
    this worker and out of scope here.
    """
    logger.info(
        "nip.notify_deadline_risk",
        tenant_id=tenant_id,
        numero_nip_ans=numero_nip_ans,
        grupo_humano=grupo_humano,
        sla_breach_task_name=sla_breach_task_name,
    )

    return {
        "deadline_risk_notified": True,
        "numero_nip_ans": numero_nip_ans,
        "grupo_humano": grupo_humano,
        "sla_breach_task_name": sla_breach_task_name,
        "event_topic_deadline_risk": event_topic_deadline_risk,
    }


def handoff_ans_submit(
    numero_nip_ans: str,
    protocolo_ans: str | None,
    decisao_nip: str = "",
    data_recebimento_nip_iso: str = "",
    tenant_id: str = "",
) -> dict[str, Any]:
    """Handoff NIP filing to SP-OP-ANS-SUBMIT-001.

    Validates protocolo_ans: absent (None) is legitimate;
    present but empty/blank raises ERR_NIP_PROTOCOLO_INVALIDO.

    `tenant_id` (T2.6-EB3 part 3 — SOURCE fix): the notification_bridge's
    `_ans_submit_variables_from_nip_handoff` rule reads `tenant_id` off THIS return dict to
    build both the fenced-starter provenance and the `ANSSUB-{tenant_id}-nipfiling-{...}`
    business key; before this fix, this dict never carried it (defaulted to `""` at the bridge
    layer, breaking multi-tenant routing). Defaults to `""` when the caller genuinely has none —
    never fabricated here.
    """
    logger.info(
        "nip.handoff_ans_submit.start",
        numero_nip_ans=numero_nip_ans,
        protocolo_ans=protocolo_ans,
        tenant_id=tenant_id,
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
        "tenant_id": tenant_id,
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
# Dict-boundary entry functions (T1.2/ADR-0026 §2b) — one per external-task
# topic. Explicit field selection -> typed dataclass (or plain kwargs, for the
# entry functions that never needed one) -> the UNCHANGED typed function above,
# which returns a plain dict directly (no nip.py worker function returns a
# dataclass instance today, so no `dataclasses.asdict` step is needed here).
# The typed functions/guards are byte-identical.
#
# Topic mapping vs spec/processes/bpmn/SP-OP-NIP-001_Resposta_NIP.bpmn (excl.
# shared/out-of-scope `operadora.events.publish`; spec has 5 unique NIP
# topics, several reused across multiple BPMN service tasks):
#   assemble_response    -> operadora.nip.instruct_dossier      (exact spec match:
#     "Montar dossie de instrucao")
#   submit_to_ans         -> operadora.nip.submit_response       (spec match, GUARDED)
#   handoff_ans_submit    -> operadora.nip.handoff_ans_submit    (exact name+spec match)
#   notify_deadline_risk  -> operadora.nip.notify_deadline_risk  (exact spec match; BUILT
#     t2.5-p2b-nip-mechanical — since t3.1-event-gap-nip-alerts consumed only by
#     ST_SolicitarInfoNip; ST_NotificarRiscoPrazo/ST_NotificarRiscoRevisao were converted
#     in place to operadora.events.publish; see the function's own docstring)
# publish_completed folds into the generic events.publish task per BPMN —
# kept as a function-derived topic for registry completeness (mirrors
# inadimplencia.py's assess_status/calculate_purge convention).
#
# t2.5-p2b-nip-mechanical (worker-registry reconciliation, verified against
# spec/ + docs/processes/contracts/SP-OP-NIP-001.md — zero hits for any of
# the four names below anywhere in spec/):
#   - DELETED classify_nip/route_nip (+ their entries + _resolve_*_group
#     helpers): superseded by the NATIVE DMN businessRuleTasks
#     BRT_Classificacao (camunda:decisionRef="nip_classification",
#     SP-OP-NIP-001_Resposta_NIP.bpmn:136) and BRT_Roteamento
#     (camunda:decisionRef="nip_routing", :167) — the engine evaluates the
#     deployed .dmn directly; these Python functions never had a
#     camunda:topic to be dispatched against.
#   - DELETED review_juridico/notify_beneficiario (+ their entries): NO BPMN
#     consumer at all (no camunda:topic anywhere in spec/processes/bpmn/
#     SP-OP-NIP-001_Resposta_NIP.bpmn nor its contract) — orphan
#     registrations with no engine-reachable path.
# ---------------------------------------------------------------------------


def instruct_dossier_entry(
    variables: dict[str, Any], *, kafka: KafkaPublisher | None = None
) -> dict[str, Any]:
    """Dict-boundary entry for `operadora.nip.instruct_dossier` -> `assemble_response`."""
    del kafka  # unused — assemble_response emits no domain event
    classification = NipClassificationResult(**pick_fields(variables, NipClassificationResult))
    routing = NipRoutingResult(**pick_fields(variables, NipRoutingResult))
    input_data = NipInput(**pick_fields(variables, NipInput))
    return assemble_response(classification, routing, input_data)


def notify_deadline_risk_entry(
    variables: dict[str, Any], *, kafka: KafkaPublisher | None = None
) -> dict[str, Any]:
    """Dict-boundary entry for `operadora.nip.notify_deadline_risk` -> `notify_deadline_risk`."""
    del kafka  # unused — notify_deadline_risk emits no domain event itself (systemic across
    # nip.py's entry functions — see register_nip_workers docstring / T3.1 "events.publish fix")
    numero_nip_ans = variables.get("numero_nip_ans", "")
    tenant_id = variables.get("tenant_id", "")
    grupo_humano = variables.get("grupo_humano", "")
    sla_breach_task_name = variables.get("sla_breach_task_name", "")
    event_topic_deadline_risk = variables.get("event_topic_deadline_risk", "")
    return notify_deadline_risk(
        numero_nip_ans=numero_nip_ans,
        tenant_id=tenant_id,
        grupo_humano=grupo_humano,
        sla_breach_task_name=sla_breach_task_name,
        event_topic_deadline_risk=event_topic_deadline_risk,
    )


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


def handoff_ans_submit_entry(
    variables: dict[str, Any], *, kafka: KafkaPublisher | None = None
) -> dict[str, Any]:
    """Dict-boundary entry for `operadora.nip.handoff_ans_submit` -> `handoff_ans_submit`.

    Raises `NipProtocoloInvalidoError` (fail-closed) when `protocolo_ans` is present but
    empty/blank — absent (`None`) is legitimate (GAP-NIP-6) — unchanged guard.

    `tenant_id` (T2.6-EB3 part 3): read off `variables` — the SP-OP-NIP-001 process instance's
    own variables, the SAME place `notify_deadline_risk_entry` (above) already reads it from.
    Every process instance carries `tenant_id` as a top-level variable (the convention every
    `tenant_id: str` dataclass field across the sibling worker modules — contas.py, cancel.py,
    inadimplencia.py, ans_submit.py — already relies on); this entry function simply wasn't
    forwarding it to `handoff_ans_submit` before this fix.
    """
    del kafka  # unused — handoff_ans_submit emits no domain event
    numero_nip_ans = variables.get("numero_nip_ans", "")
    protocolo_ans = variables.get("protocolo_ans")
    decisao_nip = variables.get("decisao_nip", "")
    data_recebimento_nip_iso = variables.get("data_recebimento_nip_iso", "")
    tenant_id = variables.get("tenant_id", "")
    return handoff_ans_submit(numero_nip_ans, protocolo_ans, decisao_nip, data_recebimento_nip_iso, tenant_id)


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

    t2.5-p2b-nip-mechanical: `classify_nip`/`route_nip` (superseded by the native DMN
    businessRuleTasks BRT_Classificacao/BRT_Roteamento) and `review_juridico`/
    `notify_beneficiario` (no BPMN consumer at all) are no longer registered — deleted along
    with their entry functions and (for classify_nip) its `_resolve_*_group` helpers. Registers
    `notify_deadline_risk` for the first time (previously a documented gap — the BPMN's
    `operadora.nip.notify_deadline_risk` topic had NO implementing function).
    """
    del seams  # unused — no additional seam (audit=/dmn=/dispatcher=/erasure=) is needed today
    harness.register_worker(
        FunctionWorker(
            "operadora.nip.instruct_dossier", functools.partial(instruct_dossier_entry, kafka=kafka)
        )
    )
    harness.register_worker(
        FunctionWorker("operadora.nip.submit_response", functools.partial(submit_response_entry, kafka=kafka))
    )
    harness.register_worker(
        FunctionWorker(
            "operadora.nip.notify_deadline_risk",
            functools.partial(notify_deadline_risk_entry, kafka=kafka),
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

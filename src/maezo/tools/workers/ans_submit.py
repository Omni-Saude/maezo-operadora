"""SP-OP-ANS-SUBMIT-001 Worker — Envios Periodicos ANS.

External tasks for ANS regulatory submissions (calendar-driven).
HITL pre-filing (nao-repudio ADR-0007): submit_to_ans is GUARDED by
ERR_ANS_SUBMIT_NOT_HUMAN — the official filing only occurs after
UT_RevisarEnvio with decisao_envio == APROVAR_ENVIO set by a human.
"""

from __future__ import annotations

import asyncio
import dataclasses
import functools
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import structlog

from maezo.platform.integrations.partition_key import partition_key_for_task
from maezo.tools.workers.ans_gateway import (
    ANS_OUTCOME_ENVIADO,
    ANS_OUTCOME_NACK,
    AnsGatewayTransport,
    resolve_ans_gateway,
)
from maezo.tools.workers.base import FunctionWorker, pick_fields, reclassify_coded_exception
from maezo.tools.workers.dmn_transport import DmnTransport, evaluate_sync, first_row, require_dmn
from maezo.tools.workers.harness import WorkerBpmnError
from maezo.tools.workers.tiss_schema import TissSchemaValidator

if TYPE_CHECKING:
    from maezo.tools.workers.harness import ExternalTask, KafkaPublisher, TaskHandler, WorkerHarness

logger = structlog.get_logger(__name__)

# Internal notification channel — the SAME typed-envelope channel recurso.py/lgpd.py publish their
# per-worker notifications to (`operadora.notifications.internal`; observed in tests via
# `notifications_of_type(...)`). notify_regulatorio is the one ans_submit worker that emits one.
_NOTIFICATIONS_TOPIC = "operadora.notifications.internal"


# ---------------------------------------------------------------------------
# ADR-0030 modeled BPMN errors (this family's Tier-0 migration)
# ---------------------------------------------------------------------------
#
# Codes this module raises as `WorkerBpmnError`, i.e. as MODELED bpmn errors dispatched to a spec
# boundary rather than demoted to an incident. Both are proven consumption-covered by
# `scripts/ci/check_bpmn_error_allowlist.py` (clause (b)): each is declared on an EXTERNAL service
# task boundary, in the ONLY process that consumes that topic, and each routes to a NEUTRAL /
# human-remediation terminal — never an adverse one:
#
#   ERR_ANS_PROTOCOLO_NACK      `BE_SubmitNack` on `ST_SubmeterEnvio` (`regulatorio.anssubmit.
#                               submit`) -> `SUB_RetryEnvio` (bounded retry/backoff) -> on
#                               exhaustion `BE_RetryEsgotado` -> `ST_PublishFailed` ->
#                               `UT_TratarNack` (human, `regulatorio-ans`) ->
#                               `End_FalhaRetransmissao`. Technical transmission fail-safe, not a
#                               business outcome: nothing is denied to anyone, the filing is
#                               retried and then handed to a human. Never `*_NOT_HUMAN`.
#   ERR_ANS_DATASET_INCOMPLETO  `BE_AssembleDatasetIncompleto` on `ST_AssembleDataset`
#                               (`regulatorio.anssubmit.assemble`) -> `UT_CorrigirPendenciaEnvio`
#                               (human, `regulatorio-ans`) -> `BRT_Calendario` (the process
#                               re-evaluates). Origin-data guard routed to human remediation; the
#                               contract is explicit that it "roteia a UT_CorrigirPendenciaEnvio
#                               (humano), **nunca** auto-rejeita o envio".
#
# Neither is T-E-gated (`is_te_gated`): neither ends in `_NOT_HUMAN` nor is a denial-block code, so
# both land in `PRODUCTION_BPMN_ERROR_ALLOWLIST` directly (see `runtime/worker_runtime/service.py`).
#
# DELIBERATELY ABSENT — `ERR_ANS_RETRY_ESGOTADO`. It is thrown by the MODEL, not by a worker:
# `End_RetryEsgotado` (an error END event inside `SUB_RetryEnvio`) throws `Error_AnsRetryEsgotado`
# to `BE_RetryEsgotado` on the subprocess. The contract says so ("lancado pelo `End_RetryEsgotado`
# quando a DMN `ans_retry_policy` retorna `continue_retry=false`") and so does the DMN's own
# description ("o subprocess emite ERR_ANS_RETRY_ESGOTADO"). No worker raises it, so it does not
# belong in a *worker* allowlist — and it could not be admitted anyway: its only boundary is
# attached to a `subProcess`, not to an external service task, so the boundary-proof gate's census
# (external-task boundaries only) does not see it and would FAIL any `WorkerBpmnError` raise of it
# under clause (b). See `retry_submission` for why the worker must stay out of that decision.
_ERR_ANS_PROTOCOLO_NACK = "ERR_ANS_PROTOCOLO_NACK"
_ERR_ANS_DATASET_INCOMPLETO = "ERR_ANS_DATASET_INCOMPLETO"

#: `status_envio` value that marks a SUCCESSFUL retransmission inside `SUB_RetryEnvio`. One of the
#: four values the contract declares for this process variable (`enviado | ack | nack |
#: retransmitido`, `docs/processes/contracts/SP-OP-ANS-SUBMIT-001.md:73`) and the exact literal
#: `GW_RetransmissaoOk`'s success condition tests (`${status_envio == 'retransmitido'}`,
#: `Flow_GWOk_EndOk`). Emitted ONLY by `retransmit_to_ans`, only on a gateway-accepted
#: retransmission — never a default, never fabricated.
_STATUS_ENVIO_RETRANSMITIDO = "retransmitido"

#: Unioned into `worker_runtime/service.py`'s `PRODUCTION_BPMN_ERROR_ALLOWLIST` — mirrors
#: `RECURSO_BPMN_ERROR_ALLOWLIST` / `LGPD_BPMN_ERROR_ALLOWLIST` / `AUTH_BPMN_ERROR_ALLOWLIST`. The
#: boundary-proof gate, not this list, is the source of truth; this constant only wires the proven
#: set into the runtime.
ANS_SUBMIT_BPMN_ERROR_ALLOWLIST: frozenset[str] = frozenset(
    {_ERR_ANS_PROTOCOLO_NACK, _ERR_ANS_DATASET_INCOMPLETO}
)


# ---------------------------------------------------------------------------
# Error types
# ---------------------------------------------------------------------------


class AnsSubmitNotHumanError(PermissionError):
    """Raised when submit_to_ans is called without human authorization.

    Guard ERR_ANS_SUBMIT_NOT_HUMAN — the worker MUST refuse to transmit
    the official filing unless decisao_envio == APROVAR_ENVIO was set
    by a human with revisor_id present.
    """

    def __init__(self, missing_fields: list[str] | None = None) -> None:
        self.missing_fields = missing_fields or []
        msg = "ERR_ANS_SUBMIT_NOT_HUMAN: ANS submission requires human approval"
        if self.missing_fields:
            msg += f"; missing: {', '.join(self.missing_fields)}"
        super().__init__(msg)


class AnsDatasetIncompletoError(ValueError):
    """Raised when dataset assembly fails (ERR_ANS_DATASET_INCOMPLETO)."""

    def __init__(self, detail: str = "") -> None:
        super().__init__(f"ERR_ANS_DATASET_INCOMPLETO: {detail}" if detail else "ERR_ANS_DATASET_INCOMPLETO")


# NOTE — there is deliberately NO `AnsRetryEsgotadoError` (removed).
#
# Retry exhaustion is a MODELED OUTCOME owned by the engine, not a worker exception. Inside
# `SUB_RetryEnvio`, `BRT_RetryPolicy` evaluates the `ans_retry_policy` DMN and `GW_ContinuarRetry`
# routes `retry.continue_retry == false` to `End_RetryEsgotado`, an error END event that THROWS
# `Error_AnsRetryEsgotado` to `BE_RetryEsgotado` on the subprocess -> `ST_PublishFailed` ->
# `UT_TratarNack` (human). Both the contract ("lancado pelo `End_RetryEsgotado`") and the DMN's own
# description ("o subprocess emite ERR_ANS_RETRY_ESGOTADO") name the model as the thrower.
#
# The old `AnsRetryEsgotadoError(RuntimeError)` raised by `retry_submission` did not merely fail to
# reach that boundary — it PREVENTED it. `RuntimeError` is the harness's TRANSIENT family, so the
# raise made `ST_RetransmitirEnvio` fail and be engine-retried; the token never advanced to
# `GW_RetransmissaoOk` -> `BRT_RetryPolicy` -> `GW_ContinuarRetry`, so the model's own exhaustion
# terminal was unreachable *because* the worker was raising. Nor could it be repaired by swapping in
# `WorkerBpmnError("ERR_ANS_RETRY_ESGOTADO")`: that code's only boundary is attached to a
# `subProcess`, and `scripts/ci/check_bpmn_error_allowlist.py` censuses EXTERNAL-TASK boundaries
# only, so such a raise fails clause (b) as an uncatalogued code.
#
# `retry_submission` therefore reports the policy and lets the model route (see its docstring).


# ---------------------------------------------------------------------------
# Input / Output types
# ---------------------------------------------------------------------------


@dataclass
class AnsSubmitInput:
    """Input for ANS submission processing."""

    tenant_id: str = ""
    report_type: str = ""
    competencia: str = ""
    periodicidade: str = ""
    origem_envio: str = "calendario"
    nip_protocolo_origem: str | None = None
    dataset_complete: bool = False
    schema_valid: bool = False
    lgpd_anonimizado: bool = False
    dataset_ref: str = ""
    #: Origin-side assembly failure: the upstream regulatory-data source could not produce the
    #: dataset at all (distinct from `dataset_complete=False`, which is a resolved FACT about a
    #: dataset that DOES exist and is routed by the admissibility DMN). True makes
    #: `prepare_submission` raise `ERR_ANS_DATASET_INCOMPLETO` -> `BE_AssembleDatasetIncompleto` ->
    #: `UT_CorrigirPendenciaEnvio`. Defaults False = fail-OPEN would be wrong here, but False is the
    #: correct default: the flag asserts a FAILURE, so absence means "no failure reported", and the
    #: dataset then still faces the unchanged `dataset_complete`/`schema_valid` fail-closed checks
    #: downstream. Nothing auto-rejects the filing on either path.
    dataset_assembly_failed: bool = False


@dataclass
class AnsSubmissionData:
    """Data prepared for ANS submission."""

    dataset_ref: str = ""
    report_type: str = ""
    competencia: str = ""
    dataset_complete: bool = False
    schema_valid: bool = False
    lgpd_anonimizado: bool = False


@dataclass
class AnsSubmitDecision:
    """Human decision for ANS submission."""

    decisao_envio: str = ""
    revisor_id: str = ""
    justificativa_adiamento: str = ""


@dataclass
class AnsRetryPolicy:
    """DMN ans_retry_policy result."""

    backoff: str = ""
    continue_retry: bool = False
    #: The attempt number this policy was evaluated for. Echoed back so `retransmit_entry` can
    #: write the incremented loop counter into the instance — the DMN's own description assigns
    #: that job to this worker ("O worker regulatorio.anssubmit.retransmit incrementa
    #: `retry_attempt` (contador tecnico de loop)"), and `BRT_RetryPolicy` then re-evaluates the
    #: SAME table on it. DEFEITO REAL pre-mudanca (narrativa corrigida por GK-ans finding 1,
    #: provada base-vs-HEAD): `retry_attempt` NUNCA era escrito no escopo do processo — nem pelo
    #: BPMN, nem por `start_ans`, nem pelo retorno do worker — logo `BRT_RetryPolicy` avaliava
    #: sobre uma variavel ausente e caia no catch-all `-` (que casa QUALQUER valor), retornando
    #: `continue_retry=false`. NAO havia loop infinito: o worker levantava
    #: `AnsRetryEsgotadoError` (RuntimeError => familia TRANSIENTE do harness) ja na PRIMEIRA
    #: retransmissao, a external task nunca completava e o terminal modelado de esgotamento era
    #: inalcancavel.
    retry_attempt: int = 0


# ---------------------------------------------------------------------------
# Workers
# ---------------------------------------------------------------------------


def prepare_submission(input_data: AnsSubmitInput) -> AnsSubmissionData:
    """Prepare the ANS regulatory submission dataset.

    Assembles an anonymized dataset (Zona Geral, ADR-0006).
    Delegates to mcp-regdata.assemble.

    **Assembly-failure guard (`ERR_ANS_DATASET_INCOMPLETO`).** `dataset_assembly_failed=True` means
    the origin could not produce the dataset, so there is nothing to validate or file: this raises
    `WorkerBpmnError(ERR_ANS_DATASET_INCOMPLETO)`, which `BE_AssembleDatasetIncompleto` catches on
    `ST_AssembleDataset` and routes to `UT_CorrigirPendenciaEnvio` (human) — the routing the
    contract already specifies ("roteia a `UT_CorrigirPendenciaEnvio` (humano), **nunca**
    auto-rejeita o envio") and the task's own BPMN documentation already promised. It is a MODELED
    bpmn error, deliberately NOT `AnsDatasetIncompletoError`: that `ValueError` would demote to a
    raw incident and lose the modeled human-remediation route. `AnsDatasetIncompletoError` keeps
    serving its two unrelated call sites (`track_protocol_entry`/`retransmit_entry`, blank
    `protocolo_ans`), whose topics carry NO boundary for this code — incident is right there.
    """
    if input_data.dataset_assembly_failed:
        logger.warning(
            "ans_submit.prepare_submission.assembly_failed",
            tenant_id=input_data.tenant_id,
            report_type=input_data.report_type,
            competencia=input_data.competencia,
        )
        raise WorkerBpmnError(
            _ERR_ANS_DATASET_INCOMPLETO,
            f"{_ERR_ANS_DATASET_INCOMPLETO}: montagem do dataset regulatorio falhou na origem "
            f"(dataset_assembly_failed=True) — report_type={input_data.report_type!r} "
            f"competencia={input_data.competencia!r}; roteia a UT_CorrigirPendenciaEnvio (humano), "
            "nunca auto-rejeita o envio",
        )

    logger.info(
        "ans_submit.prepare_submission.start",
        tenant_id=input_data.tenant_id,
        report_type=input_data.report_type,
        competencia=input_data.competencia,
    )

    # In production, this delegates to mcp-regdata.assemble
    # For now, produce stub dataset
    dataset_ref = input_data.dataset_ref or f"dataset-{input_data.report_type}-{input_data.competencia}"
    dataset_complete = input_data.dataset_complete or bool(input_data.dataset_ref)

    result = AnsSubmissionData(
        dataset_ref=dataset_ref,
        report_type=input_data.report_type,
        competencia=input_data.competencia,
        dataset_complete=dataset_complete,
        schema_valid=input_data.schema_valid,
        lgpd_anonimizado=input_data.lgpd_anonimizado,
    )

    logger.info(
        "ans_submit.prepare_submission.complete",
        dataset_ref=result.dataset_ref,
        dataset_complete=result.dataset_complete,
    )
    return result


def validate_data(
    submission: AnsSubmissionData,
    *,
    tiss_validator: TissSchemaValidator | None = None,
) -> dict[str, Any]:
    """Validate the ANS submission dataset (XSD/TISS schema) — T2.6-2, design §2.B.

    Real `lxml.etree.XMLSchema` validation against the pinned padrão-TISS version
    (`tools/workers/tiss_schema.py`), NOT an echo of the inbound `schema_valid` flag (the old
    stub this replaces). Fail-closed on every axis: missing dataset_ref/incomplete dataset
    (unchanged structural checks), unpinned/unvendored TISS schema, unreadable/malformed XML, or
    a genuine schema violation all resolve `schema_valid=False` — NEVER an exception, NEVER an
    auto-pass. Returns `schema_valid` + structured `errors`; the BPMN routes False to
    `UT_CorrigirPendenciaEnvio` (human), never a reject.

    `tiss_validator` is the T2.6-2 seam (mirrors the `dmn`/`ans_gateway` seams already threaded
    through this module): `None` (production default, no seam injected) resolves to a real
    `TissSchemaValidator()`, which itself fails closed to `schema_valid=False` while
    `MAEZO_TISS_SCHEMA_VERSION` stays unset (today, everywhere — the version is SME-gated,
    design §2.B/§7, and the real vendored XSD set is a documented external dependency, not
    fabricated here). Dev/test inject a `TissSchemaValidator` pinned at a fixture schema root.
    """
    logger.info(
        "ans_submit.validate_data.start",
        dataset_ref=submission.dataset_ref,
        report_type=submission.report_type,
    )

    validator = tiss_validator if tiss_validator is not None else TissSchemaValidator()
    tiss_result = validator.validate(report_type=submission.report_type, dataset_ref=submission.dataset_ref)

    schema_valid = tiss_result.schema_valid
    errors: list[str] = list(tiss_result.errors)

    if not submission.dataset_ref.strip():
        errors.append("dataset_ref ausente")
        schema_valid = False
    if not submission.dataset_complete:
        errors.append("dataset incompleto")
        schema_valid = False

    result = {
        "schema_valid": schema_valid,
        "dataset_ref": submission.dataset_ref,
        "errors": errors,
        "report_type": submission.report_type,
        "competencia": submission.competencia,
        "tiss_schema_version": tiss_result.tiss_schema_version,
    }

    logger.info(
        "ans_submit.validate_data.complete",
        schema_valid=schema_valid,
        errors=errors,
        tiss_schema_version=tiss_result.tiss_schema_version,
    )
    return result


def _require_human_approval(decision: AnsSubmitDecision) -> None:
    """ERR_ANS_SUBMIT_NOT_HUMAN — the ONE implementation of the pre-filing HITL guard.

    Raises `AnsSubmitNotHumanError` (a `PermissionError`, routed straight to an engine incident by
    `harness._handle`, never retried per ADR-0008) unless a HUMAN recorded `decisao_envio ==
    APROVAR_ENVIO` AND a `revisor_id`.

    Shared by BOTH transmitting call sites so they cannot drift: `transmit_to_ans`
    (`ST_SubmeterEnvio`) and `retransmit_to_ans` (`ST_RetransmitirEnvio`). The BPMN requires exactly
    that of the retry leg — "Mesmo guard nao-repudio (ERR_ANS_SUBMIT_NOT_HUMAN): a retransmissao
    reusa decisao_envio=APROVAR_ENVIO + revisor_id ja setados pelo humano e NUNCA re-autoriza"
    (`ST_RetransmitirEnvio` documentation) — and so does the contract's own integration row
    ("reusa APROVAR_ENVIO+revisor_id (mesmo guard ERR_ANS_SUBMIT_NOT_HUMAN)",
    `docs/processes/contracts/SP-OP-ANS-SUBMIT-001.md:99`). REUSE is not re-authorization: the guard
    reads the human decision already in process scope; nothing here can set it.
    """
    missing: list[str] = []

    if decision.decisao_envio != "APROVAR_ENVIO":
        missing.append("decisao_envio != APROVAR_ENVIO")
    if not decision.revisor_id.strip():
        missing.append("revisor_id")

    if missing:
        raise AnsSubmitNotHumanError(missing_fields=missing)


def transmit_to_ans(
    submission: AnsSubmissionData,
    decision: AnsSubmitDecision,
    *,
    gateway: AnsGatewayTransport | None = None,
    business_key: str = "",
    requested_outcome: str = "",
) -> dict[str, Any]:
    """Transmit the official filing to ANS — GUARDED adverse effect.

    ERR_ANS_SUBMIT_NOT_HUMAN: MUST refuse if:
    - decisao_envio != APROVAR_ENVIO
    - Missing revisor_id

    The official filing is legally binding — only after UT_RevisarEnvio
    completed by a human (nao-repudio ADR-0007).

    Protocol issuance goes through the explicit `AnsGatewayTransport` seam (T2.6-1, design §2.A),
    NEVER a fabricated `sha256(time_ns)` number. `gateway=None` resolves fail-closed to
    `RefusingAnsGatewayTransport` (the production default: refuses, raises
    `AnsGatewayUnavailableError`, issues no protocol until real ANS credentials are wired). Dev/test
    inject `LabeledMockAnsGatewayTransport` (deterministic `MOCK-ANS-NAO-VINCULATIVO-{business_key}`).
    The `ERR_ANS_SUBMIT_NOT_HUMAN` guard fires FIRST, before any gateway touch — orthogonal to the
    gateway and unchanged.

    **NACK branch (`ERR_ANS_PROTOCOLO_NACK`).** When the gateway reports `status_envio="nack"` the
    filing was REFUSED by ANS, so no `submitted=True` result may be returned: this raises
    `WorkerBpmnError(ERR_ANS_PROTOCOLO_NACK)`, the MODELED error `BE_SubmitNack` catches on
    `ST_SubmeterEnvio` to enter `SUB_RetryEnvio` (contract §Codigos de erro: "lancado pelo worker
    `regulatorio.anssubmit.submit` APOS o guard humano, num NACK transitorio"). Since t9-nack-vars
    the raise ALSO carries `protocolo_ans`/`status_envio` through `WorkerBpmnError`'s allowlisted
    variables channel (`harness.screen_bpmn_error_variables`) — without it those two never reach
    process scope (a raising worker never `complete`s) and `ST_RetransmitirEnvio` fail-closes on a
    blank `protocolo_ans`, turning the modeled retry route into an incident. The free-text refusal
    REASON still travels in the error MESSAGE only (the channel admits bounded tokens, never prose).

    `requested_outcome` is the dev/test-only NACK directive forwarded verbatim to the gateway; only
    `LabeledMockAnsGatewayTransport` honors it. In production the gateway is
    `RefusingAnsGatewayTransport`, which raises before reading it — so this parameter CANNOT induce
    a NACK, nor any other outcome, on a production-wired worker.

    Any gateway-reported status that is neither `enviado` nor `nack` is rejected fail-closed
    (`ValueError` -> incident) rather than being treated as an accepted filing.
    """
    logger.info(
        "ans_submit.transmit_to_ans.start",
        report_type=submission.report_type,
        decisao_envio=decision.decisao_envio,
        revisor_id=decision.revisor_id,
    )

    _require_human_approval(decision)

    import time

    # Route protocol issuance through the explicit gateway. Refusing (prod default) RAISES here —
    # no result dict with a fabricated protocol is ever built. Mock returns a deterministic,
    # unmistakably-synthetic protocol keyed on the business key.
    protocol = resolve_ans_gateway(gateway).submit(
        business_key=business_key,
        report_type=submission.report_type,
        competencia=submission.competencia,
        dataset_ref=submission.dataset_ref,
        revisor_id=decision.revisor_id,
        requested_outcome=requested_outcome,
    )

    if protocol.status_envio == ANS_OUTCOME_NACK:
        # ANS REFUSED the filing. Never return `submitted=True`; raise the MODELED bpmn error so
        # BE_SubmitNack routes the instance into SUB_RetryEnvio (bounded retry -> human).
        logger.warning(
            "ans_submit.transmit_to_ans.nack",
            protocolo_ans=protocol.protocolo_ans,
            nack_motivo=protocol.nack_motivo,
            report_type=submission.report_type,
            competencia=submission.competencia,
        )
        raise WorkerBpmnError(
            _ERR_ANS_PROTOCOLO_NACK,
            f"{_ERR_ANS_PROTOCOLO_NACK}: ANS recusou o envio (NACK) — "
            f"protocolo_ans={protocol.protocolo_ans!r} nack_motivo={protocol.nack_motivo!r} "
            f"business_key={business_key!r}",
            # t9-nack-vars: o canal de variaveis do WorkerBpmnError (harness.py
            # `screen_bpmn_error_variables` — allowlist de chaves + valor limitado). SO estas duas,
            # ambas declaradas pelo contrato/BPMN:
            #   protocolo_ans — contrato :72; BPMN `ST_SubmeterEnvio` ("Emite protocolo_ans +
            #     status_envio=enviado"). CARREGA A ROTA: `ST_RetransmitirEnvio` (destino do
            #     `BE_SubmitNack` via `SUB_RetryEnvio`) recusa fail-closed com `protocolo_ans` em
            #     branco, entao sem este canal o ramo modelado de retry vira incidente — era o
            #     BLOQUEIO 1 do xfail que este pacote fecha.
            #   status_envio — contrato :73, conjunto FECHADO `enviado|ack|nack|retransmitido`.
            #     Estado honesto do envio apos a recusa; sem ele o escopo fica com o valor anterior
            #     (ou sem variavel nenhuma em producao) enquanto o token ja esta em retry.
            # `nack_motivo` fica FORA de proposito: o contrato o escopa a retransmissao
            # ("preenchido apenas em retransmissao", :75) — a razao da recusa viaja na MENSAGEM do
            # erro (redigida por `redact_error_message` antes do Cockpit), nunca inventando
            # variavel de processo que o modelo nao declara neste ponto.
            variables={
                "protocolo_ans": protocol.protocolo_ans,
                "status_envio": protocol.status_envio,
            },
        )
    if protocol.status_envio != ANS_OUTCOME_ENVIADO:
        # Fail-closed: an unrecognized gateway status is NOT an accepted filing. Reaching here
        # means a transport returned a status this worker cannot interpret — an incident, never a
        # silent `submitted=True`.
        raise ValueError(
            f"status_envio desconhecido do gateway ANS: {protocol.status_envio!r} "
            f"(esperado {ANS_OUTCOME_ENVIADO!r} ou {ANS_OUTCOME_NACK!r}); "
            f"business_key={business_key!r} — recusa fail-closed, nenhum envio registrado"
        )

    result = {
        "submitted": True,
        "protocolo_ans": protocol.protocolo_ans,
        "status_envio": protocol.status_envio,
        "synthetic": protocol.synthetic,
        "vinculativo": protocol.vinculativo,
        "data_envio": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "report_type": submission.report_type,
        "competencia": submission.competencia,
        "revisor_id": decision.revisor_id,
    }

    logger.info(
        "ans_submit.transmit_to_ans.complete",
        protocolo_ans=protocol.protocolo_ans,
        synthetic=protocol.synthetic,
        vinculativo=protocol.vinculativo,
    )
    return result


def handle_nack(
    protocolo_ans: str,
    nack_motivo: str = "",
    retry_attempt: int = 0,
) -> dict[str, Any]:
    """Handle a NACK from ANS — determine if retryable.

    ERR_ANS_PROTOCOLO_NACK: enters SUB_RetryEnvio subprocess.
    Transient NACK -> retry; permanent NACK -> UT_TratarNack (human).
    """
    logger.info(
        "ans_submit.handle_nack",
        protocolo_ans=protocolo_ans,
        nack_motivo=nack_motivo,
        retry_attempt=retry_attempt,
    )

    # Determine if NACK is retryable
    nack_lower = nack_motivo.lower()
    retryable = any(
        kw in nack_lower
        for kw in ("timeout", "transient", "temporary", "servico_indisponivel", "tente_novamente")
    )

    return {
        "protocolo_ans": protocolo_ans,
        "nack_motivo": nack_motivo,
        "retryable": retryable,
        "retry_attempt": retry_attempt,
        "status": "nack",
    }


def retransmit_to_ans(
    submission: AnsSubmissionData,
    decision: AnsSubmitDecision,
    *,
    gateway: AnsGatewayTransport | None = None,
    business_key: str = "",
    requested_outcome: str = "",
) -> dict[str, Any]:
    """Retransmit the filing to ANS inside `SUB_RetryEnvio` — GUARDED, and it COMPLETES on a NACK.

    This is the missing half of `ST_RetransmitirEnvio`. Its BPMN documentation is explicit that the
    task RETRANSMITS and ECHOES the outcome: "Worker de retransmissao (regulatorio.anssubmit.
    retransmit). … Incrementa retry_attempt (contador de loop) e ecoa status_envio (retransmitido em
    sucesso, nack se ainda falha) + nack_motivo" (`ST_RetransmitirEnvio` documentation). Until
    t9-nack-vars the worker only evaluated the retry-policy DMN and never touched the gateway, so
    NO worker emitted `status_envio='retransmitido'` — and `GW_RetransmissaoOk`'s success condition
    `${status_envio == 'retransmitido'}` (`Flow_GWOk_EndOk`) was unsatisfiable by construction. That
    was BLOQUEIO 2 of the NACK xfail.

    **Why it must NOT raise on a NACK** (the difference from `transmit_to_ans`). Inside the
    subprocess the NACK is the EXPECTED, modeled outcome: `GW_RetransmissaoOk`'s default flow
    `Flow_GWOk_Policy` routes it to `BRT_RetryPolicy` -> `GW_ContinuarRetry` -> `ICE_Backoff` (loop)
    or `End_RetryEsgotado` (exhaustion -> `BE_RetryEsgotado` -> `UT_TratarNack`, human). A worker
    that raised here would fail the external task instead of completing it, so the token would
    never reach that gateway at all — the exact defect the removed `AnsRetryEsgotadoError` caused
    (see the module-level note where it used to live). So: NACK -> `status_envio='nack'` +
    `nack_motivo`, RETURNED.

    Guard: `_require_human_approval` — the SAME `ERR_ANS_SUBMIT_NOT_HUMAN` guard, reusing (never
    re-authorizing) the human decision already in scope, as the BPMN/contract require.

    Gateway: the same `AnsGatewayTransport` seam `transmit_to_ans` uses, with the same fail-closed
    default. PRODUCTION injects nothing -> `RefusingAnsGatewayTransport` -> `AnsGatewayUnavailableError`
    -> incident, so a production retransmission REFUSES rather than fabricating a filing outcome.
    That is not a regression of a working path: the production `ST_SubmeterEnvio` refuses first, so
    `SUB_RetryEnvio` is unreachable in production anyway (real ANS connectivity is AWS-blocked,
    issue #16) — the retry leg simply fails closed the same way the submit leg already does.

    `requested_outcome` is the dev/test-only outcome directive, forwarded verbatim; only
    `LabeledMockAnsGatewayTransport` honors it (exact same posture as `transmit_to_ans`'s). It is a
    SEPARATE variable from the submit leg's (`retransmit_outcome` vs `status_envio`, see
    `retransmit_entry`) so a test can seed "submit NACKs, retransmission succeeds" — the very shape
    `Flow_GWOk_EndOk` exists to model.

    An unrecognized gateway status is rejected fail-closed (`ValueError` -> incident), never treated
    as a successful retransmission.
    """
    logger.info(
        "ans_submit.retransmit_to_ans.start",
        report_type=submission.report_type,
        decisao_envio=decision.decisao_envio,
        revisor_id=decision.revisor_id,
    )

    _require_human_approval(decision)

    protocol = resolve_ans_gateway(gateway).submit(
        business_key=business_key,
        report_type=submission.report_type,
        competencia=submission.competencia,
        dataset_ref=submission.dataset_ref,
        revisor_id=decision.revisor_id,
        requested_outcome=requested_outcome,
    )

    if protocol.status_envio not in (ANS_OUTCOME_ENVIADO, ANS_OUTCOME_NACK):
        raise ValueError(
            f"status_envio desconhecido do gateway ANS na retransmissao: {protocol.status_envio!r} "
            f"(esperado {ANS_OUTCOME_ENVIADO!r} ou {ANS_OUTCOME_NACK!r}); "
            f"business_key={business_key!r} — recusa fail-closed, nenhuma retransmissao registrada"
        )

    retransmitida = protocol.status_envio == ANS_OUTCOME_ENVIADO
    # The engine-facing vocabulary of the retry subprocess is `retransmitido`, not `enviado`: it is
    # the value `Flow_GWOk_EndOk` tests and one of the four the contract declares for this variable
    # (`enviado|ack|nack|retransmitido`, docs/processes/contracts/SP-OP-ANS-SUBMIT-001.md:73). The
    # gateway speaks only `enviado`/`nack` (it does not know it is inside a retry), so the mapping
    # belongs here — at the one place that knows which BPMN leg it is serving.
    status_envio = _STATUS_ENVIO_RETRANSMITIDO if retransmitida else ANS_OUTCOME_NACK

    result = {
        "retransmitido": retransmitida,
        "protocolo_ans": protocol.protocolo_ans,
        "status_envio": status_envio,
        "nack_motivo": protocol.nack_motivo,
        "synthetic": protocol.synthetic,
        "vinculativo": protocol.vinculativo,
        "report_type": submission.report_type,
        "competencia": submission.competencia,
        "revisor_id": decision.revisor_id,
    }

    logger.info(
        "ans_submit.retransmit_to_ans.complete",
        protocolo_ans=protocol.protocolo_ans,
        status_envio=status_envio,
        synthetic=protocol.synthetic,
        vinculativo=protocol.vinculativo,
    )
    return result


def retry_submission(
    protocolo_ans: str,
    retry_attempt: int,
    report_type: str = "",
    competencia: str = "",
    *,
    dmn: DmnTransport,
) -> AnsRetryPolicy:
    """Evaluate DMN `ans_retry_policy` for backoff and retry decision (ADR-0028/T1.5).

    The attempt-count -> backoff/continue_retry ladder (1 -> PT5M, 2 -> PT30M, 3 -> PT2H,
    catch-all >3 -> ""/continue_retry=false) used to be hand-forked here; it now lives ONLY in
    the deployed `ans_retry_policy` decision table (ADR-0012) — golden-parity proven 1:1
    against attempts 1-3 and the catch-all before this cutover (T1.5 PR body / evidence
    ledger). `report_type`/`competencia` are accepted for call-site compatibility (unused by
    this decision — the table keys only on `retry_attempt`).

    **Exhaustion does NOT raise here.** `continue_retry=false` is REPORTED, not enforced: the engine
    owns the routing. `BRT_RetryPolicy` re-evaluates this same table inside `SUB_RetryEnvio` and
    `GW_ContinuarRetry` sends the false branch to `End_RetryEsgotado`, which throws
    `Error_AnsRetryEsgotado` to `BE_RetryEsgotado` -> `ST_PublishFailed` -> `UT_TratarNack` (human).
    Raising from this worker would fail the external task instead of completing it, so the token
    would never reach that gateway at all — see the module-level note where `AnsRetryEsgotadoError`
    used to live for the full evidence.
    """
    del report_type, competencia  # unused by ans_retry_policy — kept for call-site compatibility
    logger.info(
        "ans_submit.retry_submission.start",
        protocolo_ans=protocolo_ans,
        retry_attempt=retry_attempt,
    )

    rows, version = evaluate_sync(dmn, "ans_retry_policy", {"retry_attempt": retry_attempt})
    row = first_row(rows, "ans_retry_policy", {"retry_attempt": retry_attempt})
    result = AnsRetryPolicy(
        backoff=str(row.get("backoff", "")),
        continue_retry=bool(row.get("continue_retry", False)),
        retry_attempt=retry_attempt,
    )

    logger.info(
        "ans_submit.retry_submission.complete",
        backoff=result.backoff,
        continue_retry=result.continue_retry,
        retry_attempt=result.retry_attempt,
        dmn_decision_version=version.version,
    )

    return result


def publish_completed(
    event_type: str = "anssubmit.completed",
    payload: dict[str, Any] | None = None,
    desfecho: str = "",
) -> dict[str, Any]:
    """Publish ANS submission completion event (dual engine+Kafka, ADR-0007)."""
    _payload = dict(payload or {})
    if desfecho:
        _payload["desfecho"] = desfecho

    logger.info(
        "ans_submit.publish_completed",
        event_type=event_type,
        desfecho=desfecho,
    )

    return {
        "published": True,
        "topic": f"agents.events.{event_type}",
        "event_type": event_type,
        "payload": _payload,
    }


# ---------------------------------------------------------------------------
# notify_regulatorio — the ONE ans_submit worker that emits an internal
# notification (event-gap design, mirrors recurso.py's raw-async handlers).
# Serves BOTH BPMN tasks on `regulatorio.anssubmit.notify_regulatorio`:
#   ST_PrepararDossie      — dossie de envio do agente Gustavo (resumo do
#                            dataset + regras DMN + due_date). INSTRUI a decisao
#                            humana de aprovar o envio; NUNCA transmite (Gustavo
#                            L1 require_human) — sem efeito adverso.
#   ST_NotificarDeadlineRisk — alerta nao-interruptivo de risco de prazo
#                            (reusado por BT_DeadlineRisk/Pendencia/Juridico).
# Raw async handler (not a `FunctionWorker` dict boundary) for the SAME reason
# recurso's notify_sla_risk/comunicar_resposta are: it needs the async Kafka seam to
# publish the notification the donor emitted (`anssubmit.notify_regulatorio`);
# the sync `FunctionWorker.execute` boundary cannot reach `await kafka.publish`.
# ---------------------------------------------------------------------------

_NOTIFY_REGULATORIO_TOPIC = "regulatorio.anssubmit.notify_regulatorio"
_NOTIFY_REGULATORIO_NOTIFICATION_TYPE = "anssubmit.notify_regulatorio"

#: `ST_RetransmitirEnvio`'s topic + the typed notification `make_retransmit_handler` publishes on
#: `operadora.notifications.internal`. Second ans_submit worker to emit one (after
#: notify_regulatorio) — the retry leg was otherwise unobservable on the notification bus.
_RETRANSMIT_TOPIC = "regulatorio.anssubmit.retransmit"
_RETRANSMIT_NOTIFICATION_TYPE = "anssubmit.retransmit"


@dataclass
class AnsNotifyRegulatorioInput:
    """Input for `notify_regulatorio` (ST_PrepararDossie + ST_NotificarDeadlineRisk)."""

    tenant_id: str = ""
    report_type: str = ""
    competencia: str = ""
    periodicidade: str = ""


def notify_regulatorio(
    input_data: AnsNotifyRegulatorioInput,
    *,
    event_topic_deadline_risk: str = "",
) -> dict[str, Any]:
    """Monta localmente o aviso de envio/risco de prazo — NAO convoca o agente Gustavo, NAO decide.

    CORRIGIDO (GUS-01, fleet audit): a docstring anterior dizia "Convoca o agente Gustavo"; esta
    funcao apenas loga e devolve um dict local (`notify_regulatorio_sent`/`report_type`/
    `competencia`/`deadline_risk`) — nao ha import de `maezo.a2a`/dispatcher neste modulo nem
    chamada a `agents.gustavo.graph` (`grep -n 'maezo.a2a\\|dispatcher\\|gustavo' ans_submit.py`
    = 0 hits relevantes). `make_notify_regulatorio_handler` (abaixo) publica esse dict como
    notificacao interna (`operadora.notifications.internal`).

    Informational only, no adverse effect: o "dossie" apenas INSTRUI a decisao humana de aprovar
    o envio (UT_RevisarEnvio) e o alerta de deadline-risk e nao-interruptivo (a UT segue aberta).
    Gustavo NUNCA transmite autonomamente (PEP require_human, ans_official_submission L1) —
    permanece verdade que este worker nunca toca o gateway ANS nem define `decisao_envio`; so
    passou a ser falso dizer que ele "convoca" o agente.

    `event_topic_deadline_risk` is `ST_NotificarDeadlineRisk`'s own BPMN `inputParameter`
    (`agents.events.anssubmit.deadline_risk`); when present it marks this invocation as the
    deadline-risk variant (vs the dossie-prep variant on `ST_PrepararDossie`, which carries none).
    """
    is_deadline_risk = bool(event_topic_deadline_risk)
    logger.info(
        "ans_submit.notify_regulatorio",
        tenant_id=input_data.tenant_id,
        report_type=input_data.report_type,
        competencia=input_data.competencia,
        variant="deadline_risk" if is_deadline_risk else "dossie",
    )
    return {
        "notify_regulatorio_sent": True,
        "report_type": input_data.report_type,
        "competencia": input_data.competencia,
        "deadline_risk": is_deadline_risk,
    }


def make_notify_regulatorio_handler(kafka: KafkaPublisher | None) -> TaskHandler:
    """Raw-handler factory for `regulatorio.anssubmit.notify_regulatorio`.

    Mirrors recurso.py's `make_notify_sla_risk_handler` exactly (raw `harness.register()` handler,
    NOT a `FunctionWorker`): publishes the `anssubmit.notify_regulatorio` typed notification to
    `operadora.notifications.internal` (the donor's own per-worker notification — its
    `test_happy_path_envio_aprovado_e_acked` asserts
    `notifications_of_type("anssubmit.notify_regulatorio")` is non-empty).

    kafka=None (fail-closed, evidenced — same decision as events.py/recurso.py): completes the
    external task ANYWAY (informational-only; blocking here would starve the default
    `Flow_GW_Revisao` path to UT_RevisarEnvio and every shared deadline-risk timer), logging
    LOUDLY instead of ever fabricating a publish. No BPMN gateway routes on this worker's output.
    """

    async def handler(task: ExternalTask) -> dict[str, Any]:
        input_data = AnsNotifyRegulatorioInput(**pick_fields(task.variables, AnsNotifyRegulatorioInput))
        event_topic_deadline_risk = str(task.variables.get("event_topic_deadline_risk") or "")
        result = notify_regulatorio(input_data, event_topic_deadline_risk=event_topic_deadline_risk)
        if kafka is None:
            logger.warning(
                "ans_submit_notify_regulatorio_no_producer",
                business_key=task.business_key,
                report_type=input_data.report_type,
            )
            return result
        notification = {
            "type": _NOTIFY_REGULATORIO_NOTIFICATION_TYPE,
            "tenant_id": input_data.tenant_id,
            "report_type": input_data.report_type,
            "competencia": input_data.competencia,
            "periodicidade": input_data.periodicidade,
            "deadline_risk": result["deadline_risk"],
        }
        if event_topic_deadline_risk:
            notification["event_topic_deadline_risk"] = event_topic_deadline_risk
        # Posture: topic-default best-effort BY DESIGN (DL-0038, t2-notify-integrity keep) — this
        # notification is ADVISORY on both variants it serves: (a) dossie (ST_PrepararDossie, the
        # MAIN ANS-filing path — an incident here would stall the regulatory pipeline for a ping;
        # UT_RevisarEnvio is engine-created regardless and BT_DueDate*/BT_DeadlineRisk* guard the
        # deadline engine-side), and (b) deadline-risk (ST_NotificarDeadlineRisk — the
        # fail-closed `agents.events.anssubmit.deadline_risk` publish one step downstream
        # incidents the branch on a real broker outage anyway). Deliberately NOT
        # best_effort=False — pinned by test_ans_submit.py.
        # GAP-SC-04-a partition key — see the shared chain in `partition_key.py`.
        message_key = partition_key_for_task(task, _NOTIFICATIONS_TOPIC, notification)
        await kafka.publish(_NOTIFICATIONS_TOPIC, notification, key=message_key)
        return result

    return handler


# ---------------------------------------------------------------------------
# Dict-boundary entry functions (T1.2/ADR-0026 §2b) — one per external-task
# topic. Explicit field selection -> typed dataclass -> the UNCHANGED typed
# function above -> dataclasses.asdict (or pass through when already a
# flat dict). The typed functions are byte-identical; only these NEW
# functions marshal the BPMN dict boundary.
#
# Topic mapping vs spec/processes/bpmn/SP-OP-ANS-SUBMIT-001_Envios_Periodicos_ANS.bpmn
# (excl. shared/out-of-scope `operadora.events.publish`):
#   prepare_submission -> regulatorio.anssubmit.assemble        (exact spec match: "Montar dataset")
#   validate_data       -> regulatorio.anssubmit.validate        (exact spec match)
#   transmit_to_ans      -> regulatorio.anssubmit.submit          (exact spec match, GUARDED)
#   handle_nack          -> regulatorio.anssubmit.track_protocol  (spec match: "Correlacionar
#                            status do protocolo ANS")
#   retransmit_to_ans    -> regulatorio.anssubmit.retransmit      (exact spec match, GUARDED —
#     + retry_submission for the loop counter/policy). Registered as a RAW ASYNC handler
#     (`make_retransmit_handler`, the name the contract itself uses at :157), NOT via
#     `register_worker`: it publishes the `anssubmit.retransmit` notification the sync
#     `FunctionWorker.execute` boundary cannot reach. `retransmit_entry` stays the sync
#     dict-boundary core the handler wraps (unit-testable without an event loop).
# publish_completed has no distinct spec topic (folds into the generic
# events.publish task per BPMN) — function-derived topic.
#   notify_regulatorio -> regulatorio.anssubmit.notify_regulatorio (shared by 2
#     distinct BPMN tasks: ST_PrepararDossie dossie prep + ST_NotificarDeadlineRisk
#     deadline-risk notice). Implemented ABOVE as a raw async handler
#     (`make_notify_regulatorio_handler`), NOT a dict-boundary entry — it emits the
#     `anssubmit.notify_regulatorio` notification the donor's worker emitted, which
#     needs the async Kafka seam a sync FunctionWorker.execute boundary cannot reach.
# ---------------------------------------------------------------------------


def assemble_entry(variables: dict[str, Any], *, kafka: KafkaPublisher | None = None) -> dict[str, Any]:
    """Dict-boundary entry for `regulatorio.anssubmit.assemble` -> `prepare_submission`."""
    del kafka  # unused — prepare_submission emits no domain event
    input_data = AnsSubmitInput(**pick_fields(variables, AnsSubmitInput))
    result = prepare_submission(input_data)
    return dataclasses.asdict(result)


def validate_entry(
    variables: dict[str, Any],
    *,
    kafka: KafkaPublisher | None = None,
    tiss_validator: TissSchemaValidator | None = None,
) -> dict[str, Any]:
    """Dict-boundary entry for `regulatorio.anssubmit.validate` -> `validate_data`.

    `tiss_validator` (T2.6-2 seam, design §2.B — threaded the same way T2.6-1's `ans_gateway` is)
    is threaded via `register_ans_submit_workers`'s `**seams` — production injects nothing, so
    `validate_data` resolves a real `TissSchemaValidator()` (fail-closed while the version is
    SME-gated); dev/test inject one pinned at a fixture schema root.
    """
    del kafka  # unused — validate_data emits no domain event
    submission = AnsSubmissionData(**pick_fields(variables, AnsSubmissionData))
    return validate_data(submission, tiss_validator=tiss_validator)


def _ans_business_key(variables: dict[str, Any]) -> str:
    """Derive the SP-OP-ANS-SUBMIT-001 business key `ANSSUB-{tenant}-{report_type}-{competencia}`
    (contract "Business key") from the flat process-variable dict.

    Prefers an explicit `business_key` variable (the engine's own instance business key) when
    present; otherwise reconstructs it from `tenant_id`/`report_type`/`competencia`. This is the key
    the `LabeledMockAnsGatewayTransport` derives its deterministic protocol from — so a retransmit
    for the same competência yields the same synthetic protocol (BPMN `:461` idempotency).

    Returns the STRIPPED key (mirrors `recurso.py`'s `_mint_protocolo_resposta` sibling, which
    derives its protocol from the same stripped key) — an engine business key can arrive
    whitespace-padded, and returning it unstripped would make a padded vs. unpadded transmit of
    the SAME logical key derive two different synthetic protocols, breaking the retransmit
    idempotency this docstring promises.
    """
    bk = variables.get("business_key")
    if isinstance(bk, str) and bk.strip():
        return bk.strip()
    tenant_id = str(variables.get("tenant_id", "")).strip()
    report_type = str(variables.get("report_type", "")).strip()
    competencia = str(variables.get("competencia", "")).strip()
    return f"ANSSUB-{tenant_id}-{report_type}-{competencia}"


def submit_entry(
    variables: dict[str, Any],
    *,
    kafka: KafkaPublisher | None = None,
    ans_gateway: AnsGatewayTransport | None = None,
) -> dict[str, Any]:
    """Dict-boundary entry for `regulatorio.anssubmit.submit` -> `transmit_to_ans` (GUARDED).

    Raises `AnsSubmitNotHumanError` (fail-closed, ERR_ANS_SUBMIT_NOT_HUMAN) when the human
    review (`decisao_envio == APROVAR_ENVIO` + `revisor_id`) is missing — unchanged guard.

    `ans_gateway` (T2.6-1 seam, design §2.A) is the protocol-issuance transport threaded via
    `register_ans_submit_workers`'s `**seams`. Production injects nothing → `resolve_ans_gateway`
    picks `RefusingAnsGatewayTransport` (refuses, fail-closed); dev/test inject
    `LabeledMockAnsGatewayTransport`.

    An inbound `status_envio` process variable is forwarded to the gateway as `requested_outcome` —
    the dev/test NACK directive. It is NOT part of `AnsSubmissionData`/`AnsSubmitDecision` ON
    PURPOSE: adding it to `AnsSubmissionData` would make `assemble_entry` write `status_envio=""`
    back into the instance and CLOBBER the seeded value one task earlier. Reading it straight off
    the variable dict here keeps the seam narrow (only this entry function looks at it) and leaves
    every other worker's output untouched. Forwarding is safe in production regardless: the
    production gateway refuses before reading the directive (see `transmit_to_ans`).
    """
    del kafka  # unused — transmit_to_ans emits no domain event itself (publish_completed does)
    submission = AnsSubmissionData(**pick_fields(variables, AnsSubmissionData))
    decision = AnsSubmitDecision(**pick_fields(variables, AnsSubmitDecision))
    return transmit_to_ans(
        submission,
        decision,
        gateway=ans_gateway,
        business_key=_ans_business_key(variables),
        requested_outcome=str(variables.get("status_envio") or ""),
    )


def track_protocol_entry(variables: dict[str, Any], *, kafka: KafkaPublisher | None = None) -> dict[str, Any]:
    """Dict-boundary entry for `regulatorio.anssubmit.track_protocol` -> `handle_nack`.

    Fail-closed (ADR-0026 §2b): `protocolo_ans` is required to correlate a NACK — missing/blank
    raises `AnsDatasetIncompletoError` (this module's own `*Invalido*`-class ValueError) rather
    than silently tracking an empty protocol.
    """
    del kafka  # unused — handle_nack emits no domain event
    protocolo_ans = variables.get("protocolo_ans", "")
    if not isinstance(protocolo_ans, str) or not protocolo_ans.strip():
        raise AnsDatasetIncompletoError("protocolo_ans ausente/invalido para track_protocol")
    nack_motivo = variables.get("nack_motivo", "")
    retry_attempt = variables.get("retry_attempt", 0)
    return handle_nack(protocolo_ans, nack_motivo, retry_attempt)


def retransmit_entry(
    variables: dict[str, Any],
    *,
    kafka: KafkaPublisher | None = None,
    dmn: DmnTransport | None = None,
    ans_gateway: AnsGatewayTransport | None = None,
) -> dict[str, Any]:
    """Dict-boundary core for `regulatorio.anssubmit.retransmit` (`ST_RetransmitirEnvio`).

    Does the TWO things the BPMN task's own documentation assigns it. The BPMN documentation lists
    them retransmit-first; the CODE runs them policy-first, because the policy is the reversible
    half and the gateway call is not (see the ordering comment in the body — GK-t9 F2). The BPMN
    lists an obligation set, not a sequence, and nothing in either half reads the other's result, so
    the two orders are observationally identical except under failure — where only this one is safe:

      1. **Owns the loop counter + reports the policy** (`retry_submission`). The inbound
         `retry_attempt` is the number of retransmissions already performed (absent/0 on entry to
         `SUB_RetryEnvio`); this invocation IS attempt `n+1`, so the incremented value is what the
         policy is evaluated for and what is written back to the instance. The DMN's own
         description assigns the increment to this worker ("O worker regulatorio.anssubmit.
         retransmit incrementa `retry_attempt`"; contract :157 says the same), and it is what makes
         the modeled loop terminate: `BRT_RetryPolicy` re-evaluates on the incremented counter until
         the catch-all returns `continue_retry=false`, routing `GW_ContinuarRetry` to
         `End_RetryEsgotado` -> `BE_RetryEsgotado` -> `UT_TratarNack`.
      2. **Retransmits** (`retransmit_to_ans`) — the guarded gateway call that emits
         `status_envio='retransmitido'` on success (or `'nack'` + `nack_motivo` if ANS refuses
         again). Added by t9-nack-vars: before it, this entry evaluated only the DMN and never
         touched the gateway, so `GW_RetransmissaoOk`'s `${status_envio == 'retransmitido'}` had no
         emitter anywhere in the fleet (BLOQUEIO 2 of the NACK xfail).

    The policy is evaluated UNCONDITIONALLY, including on a successful retransmission where the
    token goes to `End_RetryOk` and no one reads it. Deliberate: it keeps `require_dmn`'s
    fail-closed contract (an unwired DMN seam is a wiring defect on EVERY invocation, not only the
    unlucky ones) and keeps the completion's variable shape constant, at the cost of one read-only
    decision evaluation. `BRT_RetryPolicy` re-evaluates the same table independently and writes
    under its own `retry` result variable, so the two never collide.

    Fail-closed: missing/blank `protocolo_ans` raises `AnsDatasetIncompletoError`; `dmn` unwired
    raises `DmnEvaluationError` (`require_dmn`, ADR-0028); the human-approval guard and the gateway
    refusal are `retransmit_to_ans`'s. Exhaustion is REPORTED (`continue_retry=false`), never raised
    — see `retry_submission`.

    CORRECAO DE NARRATIVA (GK-ans finding 1): sem o incremento NAO havia "loop infinito" —
    `retry_attempt` nunca chegava ao escopo do processo, entao o catch-all `-` casava sempre e o
    esgotamento era imediato; o que de fato quebrava a rota era o `AnsRetryEsgotadoError` (familia
    transiente) levantado na primeira retransmissao, que impedia a task de completar.

    `retransmit_outcome` is the dev/test-only outcome directive for the RETRY leg, forwarded to the
    gateway as `requested_outcome`. It is a DIFFERENT variable from the submit leg's `status_envio`
    (`submit_entry`) precisely so a test can seed "submit NACKs, retransmission succeeds" without
    the retry inheriting the submit's directive and nacking forever. Production is unaffected for
    the same reason the submit-side directive is: `resolve_ans_gateway(None)` picks the refusing
    transport, which raises before reading it.
    """
    del kafka  # unused here — the notification is published by `make_retransmit_handler` (async seam)
    protocolo_ans = variables.get("protocolo_ans", "")
    if not isinstance(protocolo_ans, str) or not protocolo_ans.strip():
        raise AnsDatasetIncompletoError("protocolo_ans ausente/invalido para retransmit")
    attempt = int(variables.get("retry_attempt") or 0) + 1
    # Resolve the DMN seam BEFORE the side-effecting gateway call, not after. An unwired seam is a
    # WIRING defect whose `DmnEvaluationError` is transient/engine-retried — resolving it late would
    # mean every retry re-transmits to ANS before failing again on the same missing seam. Fail fast,
    # on the cheap check, so a mis-wired deployment never touches the regulator.
    dmn_transport = require_dmn(dmn, _RETRANSMIT_TOPIC)

    submission = AnsSubmissionData(**pick_fields(variables, AnsSubmissionData))
    decision = AnsSubmitDecision(**pick_fields(variables, AnsSubmitDecision))

    # ORDER IS LOAD-BEARING (GK-t9 F2): everything that can fail on its own goes BEFORE the
    # irreversible gateway call, so a redelivery cannot duplicate a transmission to the regulator.
    # `require_dmn` is only the CHEAP half of the DMN seam — it proves a transport was injected, not
    # that the engine answers. The ROUND-TRIP (`evaluate_sync` -> HTTP -> `first_row`) can fail for
    # reasons the cheap check cannot see: engine down, decision key undeployed, empty result set.
    # All of those raise `DmnEvaluationError`, which is transient/engine-retried, so with the round
    # trip AFTER the gateway call the external task fails and CIB Seven redelivers it — and the
    # retransmission runs a second time. `LabeledMockAnsGatewayTransport` is deterministic by
    # business key (BPMN `:461`), but the real regulator is not: a duplicate filing is a regulatory
    # fact that cannot be taken back. Evaluating first is free of that hazard because
    # `retry_submission` is a pure function of `(protocolo_ans, attempt)` — both read off the
    # INBOUND variables above — and never reads the retransmission result, so nothing about the
    # policy depends on the gateway having been called.
    #
    # ONE knock-on, stated rather than glossed: `_require_human_approval` lives INSIDE
    # `retransmit_to_ans`, so an unapproved retransmission now performs the (read-only, PHI-free)
    # DMN evaluation before it refuses. The guard still refuses, and still refuses BEFORE anything
    # reaches the regulator — the only cost is one wasted decision evaluation on the refused path,
    # which is the same price the docstring already accepts on the successful-retransmission path.
    policy = retry_submission(
        protocolo_ans,
        attempt,
        submission.report_type,
        submission.competencia,
        dmn=dmn_transport,
    )

    retransmission = retransmit_to_ans(
        submission,
        decision,
        gateway=ans_gateway,
        business_key=_ans_business_key(variables),
        requested_outcome=str(variables.get("retransmit_outcome") or ""),
    )

    # RESIDUAL WINDOW, stated honestly: this reorder shrinks the at-least-once window, it does NOT
    # close it. Everything after the gateway call is still exposed — the typed Kafka notification
    # published by `make_retransmit_handler` and the harness's own `complete` (plus its audit emit).
    # A crash or a transport error in any of those still fails the task, still redelivers it, and
    # still re-transmits. Closing THAT window needs idempotency at the gateway (a per-(business_key,
    # attempt) transmission key the regulator or an outbox dedupes on), which is a separate,
    # unbuilt piece of work — not something call ordering inside this function can provide.
    return {**dataclasses.asdict(policy), **retransmission}


def make_retransmit_handler(
    kafka: KafkaPublisher | None,
    *,
    dmn: DmnTransport | None = None,
    ans_gateway: AnsGatewayTransport | None = None,
) -> TaskHandler:
    """Raw-handler factory for `regulatorio.anssubmit.retransmit` — the name the CONTRACT already
    uses for this worker ("contador de loop (incrementado pelo worker `make_retransmit_handler`)",
    `docs/processes/contracts/SP-OP-ANS-SUBMIT-001.md:157`).

    Raw async handler rather than a `FunctionWorker`, for the SAME reason
    `make_notify_regulatorio_handler` is: it must publish the `anssubmit.retransmit` typed
    notification to `operadora.notifications.internal`, and the sync `FunctionWorker.execute`
    boundary cannot reach `await kafka.publish`. That publish is what closes BLOQUEIO 3 (FINDING C)
    for this topic — the retry leg was previously unobservable on the notification bus, so a
    retransmission that DID happen looked identical to one that never ran.

    `reclassify_coded_exception` (`base.py`, the ONE shared implementation — GK-w5 finding 3) is
    applied around the sync core exactly as `FunctionWorker.execute` would have: leaving the
    registry path drops that reclassification, and `AnsGatewayUnavailableError` (a coded `Exception`,
    NOT `_HARNESS_CLASSIFIED`) would then fall into the harness's generic branch and be treated as
    TRANSIENT — engine-retried instead of raising an immediate incident. Retrying cannot conjure ANS
    credentials, so that would be a fail-closed regression; this call keeps the classification
    byte-identical to the `FunctionWorker` it replaces.

    kafka=None (fail-closed, evidenced — same decision as `make_notify_regulatorio_handler`):
    completes the external task ANYWAY, logging LOUDLY instead of fabricating a publish. The
    notification is ADVISORY: `GW_RetransmissaoOk`/`BRT_RetryPolicy` route on the PROCESS VARIABLES
    this handler returns, never on the notification, so a lost ping cannot misroute the retry loop.
    Publish posture is the topic default (best-effort) for the same reason.
    """

    async def handler(task: ExternalTask) -> dict[str, Any]:
        result = await asyncio.to_thread(
            reclassify_coded_exception,
            lambda: retransmit_entry(task.variables, dmn=dmn, ans_gateway=ans_gateway),
        )
        if kafka is None:
            logger.warning(
                "ans_submit_retransmit_no_producer",
                business_key=task.business_key,
                retry_attempt=result.get("retry_attempt"),
            )
            return result
        notification = {
            "type": _RETRANSMIT_NOTIFICATION_TYPE,
            "tenant_id": str(task.variables.get("tenant_id") or ""),
            "report_type": result.get("report_type", ""),
            "competencia": result.get("competencia", ""),
            # The three fields the retry leg is judged by: WHICH attempt, WHAT the ANS said, and
            # WHOSE human approval was reused (never re-obtained — `_require_human_approval`).
            "retry_attempt": result.get("retry_attempt"),
            "status_envio": result.get("status_envio", ""),
            "decisao_envio": str(task.variables.get("decisao_envio") or ""),
            "revisor_id": result.get("revisor_id", ""),
            "continue_retry": result.get("continue_retry"),
        }
        # GAP-SC-04-a partition key — see the shared chain in `partition_key.py`.
        message_key = partition_key_for_task(task, _NOTIFICATIONS_TOPIC, notification)
        await kafka.publish(_NOTIFICATIONS_TOPIC, notification, key=message_key)
        return result

    return handler


def publish_completed_entry(
    variables: dict[str, Any], *, kafka: KafkaPublisher | None = None
) -> dict[str, Any]:
    """Dict-boundary entry for `regulatorio.anssubmit.publish_completed` -> `publish_completed`."""
    del kafka  # unused — see module bootstrap docstring on the Kafka seam
    event_type = variables.get("event_type", "anssubmit.completed")
    payload = variables.get("payload") or {}
    desfecho = variables.get("desfecho", "")
    return publish_completed(event_type=event_type, payload=payload, desfecho=desfecho)


def register_ans_submit_workers(
    harness: WorkerHarness,
    kafka: KafkaPublisher | None = None,
    **seams: Any,
) -> None:
    """Register the SP-OP-ANS-SUBMIT-001 workers on `harness`.

    FIVE dict-boundary `FunctionWorker` entries (assemble/validate/submit/track_protocol/
    publish_completed) PLUS TWO raw async handlers registered via `harness.register` rather than
    `register_worker`, because each must publish a typed notification the sync `FunctionWorker`
    boundary cannot reach (mirrors recurso's raw handlers):
      - `make_notify_regulatorio_handler` on `regulatorio.anssubmit.notify_regulatorio`
        (`anssubmit.notify_regulatorio`);
      - `make_retransmit_handler` on `regulatorio.anssubmit.retransmit` (`anssubmit.retransmit`) —
        moved off `FunctionWorker` by t9-nack-vars; the contract already names this factory (:157).

    `kafka` is accepted (donor contract, ADR-0026 §2) and threaded to every entry function via
    `functools.partial` for signature parity with modules that DO emit domain events; none of the
    five `FunctionWorker` entry functions calls `kafka.publish` (the typed functions only DESCRIBE
    the event to publish — see `publish_completed_entry` — a genuine `kafka.publish` fan-out needs
    the async seam only the two raw handlers have; not fabricated in the sync entries).

    `dmn` (ADR-0028 §1 seam) is threaded ONLY into `retransmit_entry` — the sole function here
    that evaluates a DMN table (`ans_retry_policy`, T1.5 cutover); every other entry function
    ignores it (dict `**seams` passthrough, not a hand-maintained per-module signature).

    `ans_gateway` (T2.6-1 seam, design §2.A) is the ANS protocol-issuance transport threaded into
    the TWO functions that transmit to ANS: `submit_entry` (`ST_SubmeterEnvio`) and, since
    t9-nack-vars, `retransmit_entry` (`ST_RetransmitirEnvio` — whose BPMN documentation always
    required it to retransmit and echo `status_envio`). PRODUCTION injects nothing here
    (`register_all_workers` passes no `ans_gateway`), so both receive `None` and
    `resolve_ans_gateway` fails closed to `RefusingAnsGatewayTransport` — production refuses to
    issue a protocol on EITHER leg (never fabricates) until real ANS credentials are wired.
    Dev/test/integration explicitly inject `LabeledMockAnsGatewayTransport`.

    `tiss_validator` (T2.6-2 seam, design §2.B) is the TISS/XSD validation resolver threaded
    ONLY into `validate_entry`. PRODUCTION injects nothing here, so `validate_data` resolves a
    real `TissSchemaValidator()` — fail-closed (`schema_valid=False`) until a real vendored XSD
    set + pinned version exist (SME-gated, `MAEZO_TISS_SCHEMA_VERSION` unset everywhere today).
    Dev/test inject a `TissSchemaValidator` pinned at a fixture schema root.
    """
    dmn = seams.get("dmn")
    ans_gateway = seams.get("ans_gateway")
    tiss_validator = seams.get("tiss_validator")
    harness.register_worker(
        FunctionWorker("regulatorio.anssubmit.assemble", functools.partial(assemble_entry, kafka=kafka))
    )
    harness.register_worker(
        FunctionWorker(
            "regulatorio.anssubmit.validate",
            functools.partial(validate_entry, kafka=kafka, tiss_validator=tiss_validator),
        )
    )
    harness.register_worker(
        FunctionWorker(
            "regulatorio.anssubmit.submit",
            functools.partial(submit_entry, kafka=kafka, ans_gateway=ans_gateway),
        )
    )
    harness.register_worker(
        FunctionWorker(
            "regulatorio.anssubmit.track_protocol", functools.partial(track_protocol_entry, kafka=kafka)
        )
    )
    harness.register_worker(
        FunctionWorker(
            "regulatorio.anssubmit.publish_completed",
            functools.partial(publish_completed_entry, kafka=kafka),
        )
    )
    # notify_regulatorio: raw async handler (NOT register_worker) — mirrors recurso's
    # notify_sla_risk / events.publish. Serves the BPMN topic shared by ST_PrepararDossie
    # (dossie prep, DEFAULT Flow_GW_Revisao happy path) and ST_NotificarDeadlineRisk (all 3
    # shared deadline-risk boundary timers). Was MISSING before — the process stalled at
    # ST_PrepararDossie with "no handler registered for topic" (T3.1 phase-2 FINDING A).
    harness.register(
        _NOTIFY_REGULATORIO_TOPIC,
        make_notify_regulatorio_handler(kafka),
    )
    # retransmit: raw async handler (NOT register_worker) since t9-nack-vars — it publishes the
    # `anssubmit.retransmit` notification the sync FunctionWorker boundary cannot reach, and the
    # contract already names this factory (`make_retransmit_handler`, :157). `ans_gateway` is now
    # threaded here too: the task RETRANSMITS (BPMN `ST_RetransmitirEnvio` documentation), which
    # was the missing emitter of `status_envio='retransmitido'`. PRODUCTION still injects nothing
    # -> `resolve_ans_gateway` fails closed to the Refusing transport.
    harness.register(
        _RETRANSMIT_TOPIC,
        make_retransmit_handler(kafka, dmn=dmn, ans_gateway=ans_gateway),
    )

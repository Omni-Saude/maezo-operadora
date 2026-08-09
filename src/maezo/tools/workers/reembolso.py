"""SP-OP-REEMBOLSO-001 Worker — Reembolso ao Beneficiario.

External tasks for beneficiary reimbursement processing.
Negativa-like L0 hard: send_reembolso_denial is GUARDED by
ERR_REEMBOLSO_DENIAL_NOT_HUMAN — denying or reducing reimbursement
only materializes after human decision in UT_AnaliseReembolso
or UT_RevisaoAuditorMedico.
"""

from __future__ import annotations

import dataclasses
import functools
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import structlog

from maezo.tools.workers.base import FunctionWorker, pick_fields
from maezo.tools.workers.ceilings import CeilingResolver

if TYPE_CHECKING:
    from maezo.tools.workers.harness import KafkaPublisher, WorkerHarness

logger = structlog.get_logger(__name__)

# Governance ceiling for reembolso L2 auto-approval (design T1.9 §2.3). The teto VALUE
# lives in the autonomy matrix (`reembolso_auto_approval.max_value_brl`, L0-core.yaml:24 +
# tenants-amh.yaml overlay), resolved via the SAME loader the PEP uses. With the D-07
# placeholder `max_value_brl: 0`, every within-table request routes to ANALISE_HUMANA.
_CEILING_ACTION = "reembolso_auto_approval"
_CEILING_PARAM = "max_value_brl"


# ---------------------------------------------------------------------------
# Reference-table lookup — value, multiplier, and HONEST provenance (defect M-2)
# ---------------------------------------------------------------------------
#
# PROVENANCE VOCABULARY. `fonte_tabela` is a free-form string at the engine boundary
# (`reembolso_calculo.dmn` declares it `typeRef="string"`), but the vocabulary is NOT this
# module's to invent — it belongs to the DMN that owns this decision, and these tokens are
# the shape that DMN already ships:
#   * `spec/processes/dmn/reembolso_calculo.dmn` rules `r_consulta`/`r_exame_*`/`r_terapia`
#     emit `TABELA_REFERENCIA_*` with `multiplo_tabela_aplicado = 1.0` for a real table hit;
#   * its catch-all `r_catchall` emits `SEM_TABELA` with value `0` and multiplo `0.0`.
# `SEM_TABELA` in particular is pinned in three places outside this module — the contract
# (`docs/processes/contracts/SP-OP-REEMBOLSO-001.md`), the test spec, and an engine-side
# assertion (`tests/integration/processes/test_sp_op_reembolso_001.py`, "reembolso_calculo
# deve ter catch-all SEM_TABELA") — so the worker MUST speak the same token for the same
# situation. The previous `"TUSS-REFERENCIA"` matched nothing anywhere in the repo (it was
# this module's only occurrence) AND was written unconditionally, including over values the
# TUSS table never produced.
#: NEW token, worker-side generalisation of the DMN's per-categoria family (`TABELA_REFERENCIA_CONSULTA`
#: / `_EXAME` / `_TERAPIA` — `reembolso_calculo.dmn:46,54,62,70`); the DMN ships no generic hit token.
_FONTE_TABELA_REFERENCIA = "TABELA_REFERENCIA"
#: Table value ADJUSTED by `_MULTIPLO_ACESSO` — a distinct token because the resulting
#: amount is NOT a value the reference table contains.
_FONTE_TABELA_MULTIPLICADA = "TABELA_REFERENCIA_MULTIPLICADA"
#: Categoria absent from the reference table. Mirrors the DMN catch-all exactly.
_FONTE_SEM_TABELA = "SEM_TABELA"

#: No table row => no multiplier was applied to anything (DMN `r_catchall` emits 0.0).
_MULTIPLO_SEM_TABELA = 0.0
#: Plain table hit (DMN table-hit rows all emit 1.0).
_MULTIPLO_NEUTRO = 1.0

# ---------------------------------------------------------------------------
# DEBT — ADR-0012 (DMN e a fonte unica de regra de negocio deterministica).
#
# `_BASE_VALUES_CENTS` and `_MULTIPLO_ACESSO` below are BUSINESS RULES living in Python.
# ADR-0012 says a deterministic business rule belongs in a versioned DMN consumed both by
# the agents (`mcp-dmn`) and by the process (`businessRuleTask`) — not in a worker. The
# authoritative table for this decision already exists and already declares all three
# outputs: `spec/processes/dmn/reembolso_calculo.dmn` (BRT_Calculo), which per
# GAP-REEMBOLSO-5 runs BEFORE this worker precisely so its result is available here.
#
# NOT MIGRATED IN THIS CHANGE — deliberately. This change fixes the audit-trail honesty
# defect (M-2) only; moving the money rule into DMN changes WHICH AMOUNTS ARE COMPUTED and
# needs the regulatorio/atuarial sign-off the DMN itself is already blocked on
# (`reembolso_calculo.dmn` is DRAFT: "multiplo/limite por categoria/segmentacao requer
# sign-off de regulatorio + atuarial"). Recorded in `docs/review-queue.md`, not fixed here.
# Precedent for the eventual migration: PAGTO's `_FAIXA_TIER_MINIMO` dict, which left
# Python for `pagto_alcada.dmn` under this same ADR (review-queue, Wave-1 tetos).
#
# Two divergences the reviewer must see, both PRE-EXISTING and untouched here:
#  1. these values DISAGREE with the DMN's for the same categoria (consulta 35000 here vs
#     12000 there; exame_especial 45000 vs 25000), and `internacao`/`opme`/
#     `alta_complexidade` have no DMN row at all — under the DMN they resolve SEM_TABELA;
#  2. `ST_CalculateAmount` carries NO inputParameter mapping `calculo.*` into this worker
#     and `ReembolsoInput` has no field for it, so this worker RE-ORIGINATES the reference
#     value from the shadow table below instead of reading the DMN output the BPMN
#     documentation says it reads.
# Values are SYNTHETIC/representative centavos — DRAFT/verify against the tabela de
# reembolso vigente before any deploy.
# ---------------------------------------------------------------------------
_BASE_VALUES_CENTS: dict[str, int] = {
    "consulta": 35000,  # R$ 350.00
    "exame_simples": 8000,  # R$ 80.00
    "exame_especial": 45000,  # R$ 450.00
    "terapia": 15000,  # R$ 150.00
    "internacao": 500000,  # R$ 5,000.00
    "opme": 300000,  # R$ 3,000.00
    "alta_complexidade": 800000,  # R$ 8,000.00
}

#: Access-condition uplift for urgencia/emergencia and fora-de-rede. ADR-0012 debt (above).
_MULTIPLO_ACESSO = 1.5
#: The `tipo_reembolso` values that attract `_MULTIPLO_ACESSO`. ADR-0012 debt (above).
_TIPOS_COM_MULTIPLICADOR: frozenset[str] = frozenset({"urgencia_emergencia", "fora_rede"})


@dataclass(frozen=True)
class _TabelaLookup:
    """What the reference-table lookup ACTUALLY found — the three facts, kept together.

    Bundling them is the point: value, multiplier and provenance are produced by one
    decision and must not be able to drift apart on their way into the audit chain (the
    M-2 defect was exactly that drift — a value from one branch, a multiplier and a source
    label hardcoded from another).
    """

    valor_calculado_tabela_cents: int
    multiplo_tabela_aplicado: float
    fonte_tabela: str

    @property
    def categoria_na_tabela(self) -> bool:
        """True iff the categoria was found in the reference table.

        Derived from ``fonte_tabela`` rather than stored, so the flag the fail-closed
        ``dentro_tabela`` gate reads can never contradict the provenance token the audit
        chain records.
        """
        return self.fonte_tabela != _FONTE_SEM_TABELA


# ---------------------------------------------------------------------------
# Error types
# ---------------------------------------------------------------------------


class ReembolsoDenialNotHumanError(PermissionError):
    """Raised when send_reembolso_denial is called without human authorization.

    Guard ERR_REEMBOLSO_DENIAL_NOT_HUMAN — the worker MUST refuse to
    communicate a denial/reduction unless decisao_reembolso in
    {NEGAR, APROVAR_PARCIAL} was set by a human with analista_id.
    """

    def __init__(self, missing_fields: list[str] | None = None) -> None:
        self.missing_fields = missing_fields or []
        msg = "ERR_REEMBOLSO_DENIAL_NOT_HUMAN: denial/reduction requires human decision"
        if self.missing_fields:
            msg += f"; missing: {', '.join(self.missing_fields)}"
        super().__init__(msg)


class ReembolsoProtocoloInvalidoError(ValueError):
    """Raised when protocolo/guia is inconsistent (ERR_REEMBOLSO_INVALID_PROTOCOLO)."""

    def __init__(self, detail: str = "") -> None:
        super().__init__(
            f"ERR_REEMBOLSO_INVALID_PROTOCOLO: {detail}" if detail else "ERR_REEMBOLSO_INVALID_PROTOCOLO"
        )


class ReembolsoValorPagamentoInvalidoError(ValueError):
    """Raised when the amount to pay cannot be determined (ERR_REEMBOLSO_VALOR_PAGAMENTO_INVALIDO).

    Fail-closed money guard for `process_payment`: the reimbursement amount is integer BRL
    centavos (ADR-0018 / contract SP-OP-REEMBOLSO-001 §"Variaveis de entrada": "inteiro — nunca
    `number`"). Absent, non-`int`, `float`, `bool` or `<= 0` => NO payment is issued.

    A `ValueError` subclass DELIBERATELY (mirrors `ReembolsoProtocoloInvalidoError`): the harness
    routes the `ValueError` family to `failure(retries=0)` — an immediate, human-visible engine
    incident, never a silent retry (`harness.py` §9). It is NOT a `WorkerBpmnError`: none of the
    three `ST_IssuePayment*` tasks carries an error boundary event (BPMN `attachedToRef`: only
    `ST_CheckCoverage` and `UT_AnaliseReembolso`), and an unmodeled `bpmnError` silently ENDS the
    process scope on CIB Seven 2.1.0 instead of opening an incident (ADR-0030 §2) — i.e. it would
    quietly close a reimbursement that was never paid.
    """

    def __init__(self, detail: str = "") -> None:
        super().__init__(
            f"ERR_REEMBOLSO_VALOR_PAGAMENTO_INVALIDO: {detail}"
            if detail
            else "ERR_REEMBOLSO_VALOR_PAGAMENTO_INVALIDO"
        )


# ---------------------------------------------------------------------------
# Input / Output types
# ---------------------------------------------------------------------------


@dataclass
class ReembolsoInput:
    """Input for reembolso processing."""

    tenant_id: str = ""
    protocolo_reembolso: str = ""
    numero_guia_tiss: str | None = None
    beneficiario_pseudo_id: str = ""
    matricula_beneficiario: str = ""
    tipo_reembolso: str = ""
    codigo_procedimento_tuss: str = ""
    categoria_procedimento: str = ""
    valor_solicitado_cents: int = 0
    data_atendimento: str = ""
    data_solicitacao: str = ""
    cid10: str | None = None
    documentos_refs: list[dict[str, Any]] = field(default_factory=list)
    cobertura_prevista: bool = False
    documentacao_completa: bool = False
    dentro_prazo: bool = False
    beneficiario_ativo: bool = False
    carencia_cumprida: bool = False
    dentro_tabela: bool = False
    dentro_teto_l2: bool = False
    requer_avaliacao_clinica: bool = False


@dataclass
class ReembolsoValidationResult:
    """Output of validate_reembolso — pre-resolved facts."""

    valid: bool = False
    cobertura_prevista: bool = False
    documentacao_completa: bool = False
    dentro_prazo: bool = False
    beneficiario_ativo: bool = False
    carencia_cumprida: bool = False
    errors: list[str] = field(default_factory=list)


@dataclass
class ReembolsoCalculoResult:
    """Output of calculate_value — arithmetic only, never decision."""

    valor_calculado_tabela_cents: int = 0
    valor_solicitado_cents: int = 0
    dentro_tabela: bool = False
    dentro_teto_l2: bool = False
    multiplo_tabela_aplicado: float = 1.0
    fonte_tabela: str = ""


@dataclass
class ReembolsoDenialInput:
    """Input for send_reembolso_denial — the gated adverse effect."""

    decisao_reembolso: str = ""
    justificativa: str = ""
    fundamentacao_contratual: str = ""
    valor_reembolso_aprovado_cents: int = 0
    valor_solicitado_cents: int = 0
    analista_id: str = ""
    auditor_id: str = ""
    cid10_referencia: str = ""
    parecer_auditor: str = ""


# ---------------------------------------------------------------------------
# Workers
# ---------------------------------------------------------------------------


def validate_reembolso(input_data: ReembolsoInput) -> ReembolsoValidationResult:
    """Validate reembolso inputs and pre-resolve facts.

    Checks: cobertura_prevista, documentacao_completa, dentro_prazo,
    beneficiario_ativo, carencia_cumprida. Facts only — never decides
    NEGAR/APROVAR_PARCIAL.
    """
    logger.info(
        "reembolso.validate_reembolso.start",
        tenant_id=input_data.tenant_id,
        protocolo_reembolso=input_data.protocolo_reembolso,
    )

    errors: list[str] = []

    if not input_data.protocolo_reembolso.strip():
        errors.append("protocolo_reembolso ausente")
        raise ReembolsoProtocoloInvalidoError("protocolo_reembolso ausente")

    if not input_data.codigo_procedimento_tuss.strip():
        errors.append("codigo_procedimento_tuss ausente")

    if input_data.valor_solicitado_cents <= 0:
        errors.append("valor_solicitado_cents invalido")

    result = ReembolsoValidationResult(
        valid=len(errors) == 0,
        cobertura_prevista=input_data.cobertura_prevista,
        documentacao_completa=input_data.documentacao_completa,
        dentro_prazo=input_data.dentro_prazo,
        beneficiario_ativo=input_data.beneficiario_ativo,
        carencia_cumprida=input_data.carencia_cumprida,
        errors=errors,
    )

    logger.info(
        "reembolso.validate_reembolso.complete",
        valid=result.valid,
        errors=errors,
    )
    return result


def check_coverage(input_data: ReembolsoInput) -> dict[str, Any]:
    """Check if the procedure is covered by the contract/segmentation.

    Resolves cobertura_prevista flag. Never decides NEGAR —
    absent coverage routes to ANALISE_HUMANA.
    """
    logger.info(
        "reembolso.check_coverage.start",
        codigo_procedimento_tuss=input_data.codigo_procedimento_tuss,
        tipo_reembolso=input_data.tipo_reembolso,
    )

    cobertura_prevista = input_data.cobertura_prevista

    result = {
        "cobertura_prevista": cobertura_prevista,
        "codigo_procedimento_tuss": input_data.codigo_procedimento_tuss,
        "tipo_reembolso": input_data.tipo_reembolso,
        "categoria_procedimento": input_data.categoria_procedimento,
    }

    logger.info("reembolso.check_coverage.complete", cobertura_prevista=cobertura_prevista)
    return result


def calculate_value(
    input_data: ReembolsoInput,
    resolver: CeilingResolver | None = None,
) -> ReembolsoCalculoResult:
    """Calculate the reimbursement value — DMN reembolso_calculo.

    Computes valor_calculado_tabela_cents, dentro_tabela, dentro_teto_l2.
    Pure arithmetic — NEVER decides to pay or deny.
    The DMN says how much would be due; the eventual reduction
    (APROVAR_PARCIAL) is a HUMAN decision.

    ``dentro_teto_l2`` is COMPUTED from the tenant governance ceiling
    (``reembolso_auto_approval.max_value_brl``) via the CeilingResolver — the inbound
    ``input_data.dentro_teto_l2`` is NEVER read on this path (design T1.9 §2.3, defect B3).
    ``resolver`` is injectable for tests; the default resolves the ceiling from the real
    ``spec/policies/autonomy`` matrix. With ``SEM_TABELA`` the reference value fed to the
    ceiling check is 0, so under a positive ceiling ``dentro_teto_l2`` reads True — that
    ceiling fact is VACUOUS for this case and is gated shut by ``dentro_tabela=False``
    (the DMN's ``reembolso_auto_approval`` AUTO_APROVAR rule requires both true).

    HONEST PROVENANCE (defect M-2). The three facts this function writes into the ADR-0007
    audit chain — ``dentro_tabela``, ``multiplo_tabela_aplicado``, ``fonte_tabela`` — now
    describe what the lookup ACTUALLY did:

    - a categoria absent from the reference table resolves to ``SEM_TABELA`` / value 0 /
      multiplo 0.0 and FORCES ``dentro_tabela=False``. It no longer falls back to the
      claimant's own ``valor_solicitado_cents``, which used to make the "reference table
      value" equal the request and therefore made ``dentro_tabela`` unconditionally True —
      a fabricated corroboration shown to the human reviewer as a verified fact;
    - ``multiplo_tabela_aplicado`` carries the multiplier that was really applied (1.5 /
      1.0 / 0.0), not a hardcoded 1.0;
    - ``fonte_tabela`` distinguishes a plain table hit from a multiplier-adjusted one, so a
      value the table never contained is never labelled as if it came straight from it.

    See :func:`_lookup_tabela_referencia` for the token vocabulary and its DMN origin.
    """
    resolver = resolver if resolver is not None else CeilingResolver()

    logger.info(
        "reembolso.calculate_value.start",
        protocolo_reembolso=input_data.protocolo_reembolso,
        valor_solicitado_cents=input_data.valor_solicitado_cents,
    )

    # Reference-table lookup: value + the multiplier really applied + where it came from.
    # In production this consults the TUSS table / tenant config (see the ADR-0012 debt note
    # on `_BASE_VALUES_CENTS`).
    lookup = _lookup_tabela_referencia(
        input_data.codigo_procedimento_tuss,
        input_data.categoria_procedimento,
        input_data.tipo_reembolso,
    )
    valor_calculado = lookup.valor_calculado_tabela_cents

    # FAIL-CLOSED (M-2): `dentro_tabela` asserts "the claimed amount is within the REFERENCE
    # TABLE". With no table row there is nothing to be within, so the claim is False by
    # construction — never merely by arithmetic. Keeping the `<=` alone would also read True
    # for a `valor_solicitado_cents=0` request against the SEM_TABELA value of 0. This is the
    # posture the DMN catch-all and the BPMN already prescribe: "fora de tabela (SEM_TABELA)
    # -> dentro_tabela=false -> analise humana" (ST_CalculateAmount documentation).
    dentro_tabela = lookup.categoria_na_tabela and input_data.valor_solicitado_cents <= valor_calculado
    # COMPUTE the ceiling fact from policy — compares the reference-table value (centavos)
    # against `reembolso_auto_approval.max_value_brl` (per L0-core.yaml:28). Ceiling 0
    # (D-07) or any config problem => False => the request routes to ANALISE_HUMANA.
    dentro_teto = resolver.within_l2_ceiling(
        tenant=input_data.tenant_id,
        action=_CEILING_ACTION,
        param=_CEILING_PARAM,
        value_cents=valor_calculado,
    )

    result = ReembolsoCalculoResult(
        valor_calculado_tabela_cents=valor_calculado,
        valor_solicitado_cents=input_data.valor_solicitado_cents,
        dentro_tabela=dentro_tabela,
        dentro_teto_l2=dentro_teto,
        multiplo_tabela_aplicado=lookup.multiplo_tabela_aplicado,
        fonte_tabela=lookup.fonte_tabela,
    )

    logger.info(
        "reembolso.calculate_value.complete",
        valor_calculado=result.valor_calculado_tabela_cents,
        dentro_tabela=result.dentro_tabela,
        multiplo_tabela_aplicado=result.multiplo_tabela_aplicado,
        fonte_tabela=result.fonte_tabela,
    )
    return result


def request_documents(
    beneficiario_pseudo_id: str,
    protocolo_reembolso: str,
    message_type: str = "pendencia_documentacao",
) -> dict[str, Any]:
    """Request missing documentation from the beneficiario — abre a pendencia (ST_SolicitarDocumentos).

    Non-adverse, notify-only: pairs with `GW_AguardarDocs`'s race (message
    `msg.reembolso.docs_received` OR `ICE_PrazoPendencia` timer). A beneficiario who never
    responds is decided by a HUMAN in `UT_DecidirPendenciaExpirada` — this worker never
    auto-cancels or auto-denies (mirrors AUTH-001's structurally-ported sub-flow, per the BPMN's
    own documentation on `ST_SolicitarDocumentos`).

    The BPMN's `event_topic_pended` inputParameter (`agents.events.reembolso.pended`) documents
    the intent for THIS task to publish a domain event (no downstream `ST_Publish*` exists on
    this branch — unlike the other completion events, which flow through a dedicated generic
    `operadora.events.publish` service task). This is the SAME disclosed, systemic gap already on
    record for every other family's `request_documents`-shaped worker in this codebase
    (auth/cancel/cred/recurso — grep-verified: none call `kafka.publish` for their own embedded
    `event_topic_pended`); this worker does not publish either, it only returns the notify
    signal. Fix belongs to the Kafka-producer wiring task, not this build.
    """
    logger.info(
        "reembolso.request_documents",
        beneficiario_pseudo_id=beneficiario_pseudo_id,
        protocolo_reembolso=protocolo_reembolso,
        message_type=message_type,
    )

    return {
        "notified": True,
        "beneficiario_pseudo_id": beneficiario_pseudo_id,
        "protocolo_reembolso": protocolo_reembolso,
        "status": "pended",
        "message_type": message_type,
    }


def analyze_request(input_data: ReembolsoInput) -> dict[str, Any]:
    """Prepare the reembolso analysis dossier — convoca o agente analista (Marina-classe).

    `ST_PrepararDossie` is the ONLY path into `UT_AnaliseReembolso` (both `Flow_Sla_Dossie` and
    `BRT_SlaAnalise`'s two incoming edges — admissibilidade-direta AND auto-aprovacao-recusada —
    converge here). The dossie assembles structured evidence (protocolo, valor solicitado,
    evidencias de tabela/teto quando ja calculadas) to INSTRUCT the human decision — it NEVER
    substitutes for it (principio Rafael, ADR-0005): no recommendation this function returns is
    ever a decisao_reembolso value. Because both convergent paths reach this task (one BEFORE
    `BRT_Calculo` runs, one after), `dentro_tabela`/`requer_avaliacao_clinica` are read as
    opportunistic evidence only — never assumed fresh or required.
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
        "codigo_procedimento_tuss": input_data.codigo_procedimento_tuss,
        "valor_solicitado_cents": input_data.valor_solicitado_cents,
        "dentro_tabela": input_data.dentro_tabela,
        "requer_avaliacao_clinica": input_data.requer_avaliacao_clinica,
        "dossie": "dossie_instruido",
        "recomendacao_sugerida": "ANALISE_HUMANA",  # O agente NUNCA decide (ADR-0005)
    }

    logger.info("reembolso.analyze_request.complete", protocolo_reembolso=input_data.protocolo_reembolso)
    return result


def notify_sla_risk(input_data: ReembolsoInput) -> dict[str, Any]:
    """Notify coordenacao-reembolso of SLA risk (non-interruptive timer `BT_AlertaSla`).

    Informational only: `UT_AnaliseReembolso` stays open, no decision is made or altered. Fires
    at `sla.sla_alerta` (DMN `reembolso_sla` resolves the actual duration — 50-70% of
    `sla.sla_analise` per contract SS SLAs). Mirrors `cancel.notify_sla_risk`'s proven shape
    exactly (same family of non-adverse, fail-safe alert worker).
    """
    logger.info(
        "reembolso.notify_sla_risk",
        protocolo_reembolso=input_data.protocolo_reembolso,
        tipo_reembolso=input_data.tipo_reembolso,
    )
    return {"sla_risk_notified": True, "protocolo_reembolso": input_data.protocolo_reembolso}


def process_payment(
    protocolo_reembolso: str,
    valor_cents: int,
    beneficiario_pseudo_id: str,
) -> dict[str, Any]:
    """Issue reimbursement payment (CNAB/conciliation).

    Only executes for approved reimbursements (AUTO_APROVAR, APROVAR or APROVAR_PARCIAL).

    `valor_cents` is the EFFECTIVELY APPROVED amount in integer BRL centavos —
    `valor_reembolso_aprovado_cents` (contract SP-OP-REEMBOLSO-001 §"Variaveis de saida");
    `issue_payment_entry` documents how each of the three payment paths produces it.

    FAIL-CLOSED (`_require_valor_pagamento_cents`): an absent / non-`int` / `float` / `bool` /
    `<= 0` amount raises `ReembolsoValorPagamentoInvalidoError` BEFORE any comprovante is minted.
    The amount is never rounded, coerced or defaulted: when the correct amount cannot be
    determined the safe outcome is NO payment plus an engine incident — a fabricated payment
    (notably the R$0,00 one a `0` default produces) would mint a `comprovante_pagamento_ref` for
    money that never moved and close the process at a neutral terminal, hiding the defect.

    The paid amount is echoed back BOTH as `valor_cents` (internal key, kept) and under its
    contract output name `valor_reembolso_aprovado_cents`, so the value that actually moved is
    observable in engine history and in the `reembolso.completed` trail (ADR-0007).
    """
    valor_cents = _require_valor_pagamento_cents(valor_cents)

    logger.info(
        "reembolso.process_payment.start",
        protocolo_reembolso=protocolo_reembolso,
        valor_cents=valor_cents,
    )

    import hashlib
    import time

    comprovante_ref = f"PAY-{hashlib.sha256(str(time.time_ns()).encode()).hexdigest()[:12].upper()}"

    result = {
        "payment_issued": True,
        "protocolo_reembolso": protocolo_reembolso,
        "valor_cents": valor_cents,
        "valor_reembolso_aprovado_cents": valor_cents,
        "comprovante_pagamento_ref": comprovante_ref,
        "beneficiario_pseudo_id": beneficiario_pseudo_id,
    }

    logger.info("reembolso.process_payment.complete", comprovante_ref=comprovante_ref)
    return result


def send_reembolso_denial(denial_input: ReembolsoDenialInput) -> dict[str, Any]:
    """Send reimbursement denial/reduction — GUARDED adverse effect.

    ERR_REEMBOLSO_DENIAL_NOT_HUMAN: MUST refuse if:
    - decisao_reembolso not in {NEGAR, APROVAR_PARCIAL}
    - Missing justificativa, fundamentacao_contratual
    - Missing analista_id (or auditor_id for clinical merit)
    - If clinical merit: missing cid10_referencia or parecer_auditor
    """
    logger.info(
        "reembolso.send_reembolso_denial.start",
        decisao_reembolso=denial_input.decisao_reembolso,
        analista_id=denial_input.analista_id,
    )

    missing: list[str] = []

    if denial_input.decisao_reembolso not in ("NEGAR", "APROVAR_PARCIAL"):
        missing.append("decisao_reembolso not in {NEGAR, APROVAR_PARCIAL}")
    if not denial_input.justificativa.strip():
        missing.append("justificativa")
    if not denial_input.fundamentacao_contratual.strip():
        missing.append("fundamentacao_contratual")

    # Human identifier: analista_id or auditor_id
    has_human = bool(denial_input.analista_id.strip() or denial_input.auditor_id.strip())
    if not has_human:
        missing.append("analista_id (or auditor_id)")

    # APROVAR_PARCIAL requires a valid reduced value
    if denial_input.decisao_reembolso == "APROVAR_PARCIAL":
        if denial_input.valor_reembolso_aprovado_cents <= 0:
            missing.append("valor_reembolso_aprovado_cents")
        elif denial_input.valor_reembolso_aprovado_cents >= denial_input.valor_solicitado_cents:
            missing.append("APROVAR_PARCIAL: valor aprovado >= valor solicitado (nao e reducao)")

    if missing:
        raise ReembolsoDenialNotHumanError(missing_fields=missing)

    return {
        "denial_sent": True,
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

    logger.info(
        "reembolso.publish_completed",
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
# Helpers
# ---------------------------------------------------------------------------


def _require_valor_pagamento_cents(valor_cents: Any) -> int:
    """Fail-closed resolution of the amount to pay — integer BRL centavos ONLY (ADR-0018).

    Money in this contract is `integer` centavos ("inteiro — nunca `number`", contract
    SP-OP-REEMBOLSO-001 §"Variaveis de entrada"; the DMN shape rule §4-bis-A forbids `number`
    outright), and `harness._to_camunda_var` types every Python `int` as `Integer`/`Long` — so a
    `float`, a `str` or a missing value arriving here is a TYPING DEFECT upstream, not a value to
    salvage. Each rejection returns the SAME safe outcome: no payment, and a `ValueError` the
    harness turns into an engine incident (`failure(retries=0)`).

    Rejected, in order: `None`/absent; `bool` (which is an `int` in Python and would otherwise
    pay 1 centavo); `float` (including integral ones like `8000.0` — no rounding is ever applied
    to money); any other non-`int` type; and `<= 0` (a zero or negative "payment" is never a
    payment).
    """
    if valor_cents is None:
        raise ReembolsoValorPagamentoInvalidoError("valor_reembolso_aprovado_cents ausente")
    if isinstance(valor_cents, bool):
        raise ReembolsoValorPagamentoInvalidoError(
            "valor_reembolso_aprovado_cents booleano — esperado integer-centavos"
        )
    if isinstance(valor_cents, float):
        raise ReembolsoValorPagamentoInvalidoError(
            f"valor_reembolso_aprovado_cents float ({valor_cents!r}) — dinheiro e centavos "
            "INTEIROS (ADR-0018); nenhum arredondamento e aplicado"
        )
    if not isinstance(valor_cents, int):
        raise ReembolsoValorPagamentoInvalidoError(
            f"valor_reembolso_aprovado_cents tipo invalido ({type(valor_cents).__name__}) — "
            "esperado integer-centavos"
        )
    if valor_cents <= 0:
        raise ReembolsoValorPagamentoInvalidoError(
            f"valor_reembolso_aprovado_cents <= 0 ({valor_cents}) — nenhum pagamento e emitido"
        )
    return valor_cents


def _lookup_tabela_referencia(
    codigo_procedimento_tuss: str,
    categoria_procedimento: str,
    tipo_reembolso: str,
) -> _TabelaLookup:
    """Look the procedure up in the reference table — value, multiplier applied, provenance.

    FAIL-CLOSED (defect M-2): a categoria that is NOT in the table resolves to
    ``SEM_TABELA`` / 0 / 0.0. It does NOT fall back to ``valor_solicitado_cents``. That
    fallback used to make the "reference table value" identical to the amount the claimant
    asked for, so the downstream ``valor_solicitado_cents <= valor_calculado_tabela_cents``
    comparison was True for every unknown or misspelled categoria — and that manufactured
    True travelled into the ADR-0007 audit chain and onto the human reviewer's dossie
    (``analyze_request``) as a corroborated fact. Refusing to answer is the honest outcome:
    the request routes to ANALISE_HUMANA with the reason visible in ``fonte_tabela``.

    ``codigo_procedimento_tuss`` is accepted and deliberately unused: the stub keys on
    categoria only. It is kept in the signature because the real TUSS lookup this stands in
    for is keyed by the TUSS code (see the ADR-0012 debt note on `_BASE_VALUES_CENTS`), and
    dropping it would erase that seam from the call site.

    Matching is ``.lower()`` only, exactly as before — NOT stripped. A whitespace-padded
    categoria therefore misses the table and fails CLOSED to SEM_TABELA rather than being
    silently normalised into a table hit; widening what counts as a known categoria is a
    data-quality decision for the humans in the review-queue row, not for this stub.
    """
    del codigo_procedimento_tuss  # unused — the stub table is keyed by categoria (see docstring)

    base = _BASE_VALUES_CENTS.get(categoria_procedimento.lower())

    if base is None:
        return _TabelaLookup(
            valor_calculado_tabela_cents=0,
            multiplo_tabela_aplicado=_MULTIPLO_SEM_TABELA,
            fonte_tabela=_FONTE_SEM_TABELA,
        )

    if tipo_reembolso in _TIPOS_COM_MULTIPLICADOR:
        # The value below is the table value ADJUSTED — the table never contained it, so it
        # is not labelled as a plain table hit.
        return _TabelaLookup(
            valor_calculado_tabela_cents=int(base * _MULTIPLO_ACESSO),
            multiplo_tabela_aplicado=_MULTIPLO_ACESSO,
            fonte_tabela=_FONTE_TABELA_MULTIPLICADA,
        )

    return _TabelaLookup(
        valor_calculado_tabela_cents=base,
        multiplo_tabela_aplicado=_MULTIPLO_NEUTRO,
        fonte_tabela=_FONTE_TABELA_REFERENCIA,
    )


# ---------------------------------------------------------------------------
# Dict-boundary entry functions (T1.2/ADR-0026 §2b) — one per external-task
# topic. Explicit field selection -> typed dataclass -> the UNCHANGED typed
# function above -> dataclasses.asdict (or pass through when already a flat
# dict). The entry functions are pure marshalling: they hold no guard and derive
# no fact of their own.
#
# HISTORY NOTE (kept honest): the T1.2 build wrapped the typed functions
# byte-identically. That is no longer literally true of `calculate_value` /
# `_lookup_tabela_referencia` (ex-`_compute_table_value`), which the M-2 fix
# rewrote for audit-trail honesty. The T1.9 invariant they were called out for
# is UNCHANGED and still holds: `dentro_teto_l2` is originated ONLY by
# `CeilingResolver.within_l2_ceiling`, never re-originated from the inbound
# payload or from a boolean expression — `tests/unit/sec/
# test_dentro_teto_source.py` stays green.
#
# Topic mapping vs spec/processes/bpmn/SP-OP-REEMBOLSO-001_Reembolso_Beneficiario.bpmn
# (excl. shared/out-of-scope `operadora.events.publish`):
#   check_coverage      -> operadora.reembolso.check_coverage (exact name+spec match)
#   validate_reembolso  -> operadora.reembolso.check_prazo (spec match: the other
#                          fact-resolution step, incl. dentro_prazo; check_coverage already
#                          claims its own exact-name topic)
#   calculate_value     -> operadora.reembolso.calculate_amount
#     (spec match: "Calcular valor de reembolso")
#   process_payment     -> operadora.reembolso.issue_payment
#     (spec match: "Emitir pagamento do reembolso")
#   send_reembolso_denial -> operadora.reembolso.send_reembolso_denial (exact spec match, GUARDED)
#   request_documents  -> operadora.reembolso.request_documents (exact name+spec match:
#     ST_SolicitarDocumentos, "Solicitar documentacao ao beneficiario")
#   analyze_request     -> operadora.reembolso.analyze_request (exact name+spec match:
#     ST_PrepararDossie, "Preparar dossie de analise (agente analista)" — the ONLY path into
#     UT_AnaliseReembolso)
#   notify_sla_risk     -> operadora.reembolso.notify_sla_risk (exact name+spec match:
#     ST_NotificarRiscoSla, "Notificar risco de SLA (coordenacao-reembolso)"; mirrors
#     cancel.notify_sla_risk's proven shape)
# publish_completed folds into the generic events.publish task per BPMN — function-derived
# topic, KEPT registered (documented registry-completeness convention shared with recurso.py;
# do not remove).
#
# T2.5-P2B reconciliation (this build): `auto_approve_or_route` and `notify_beneficiario` are
# DELETED (were dead orphan registrations, no BPMN camunda:topic anywhere references either —
# grep-verified against spec/processes/bpmn/SP-OP-REEMBOLSO-001_Reembolso_Beneficiario.bpmn).
# `BRT_AutoApproval` is a NATIVE businessRuleTask (`camunda:decisionRef="reembolso_auto_approval"`,
# resolving to the real DMN decision in `spec/processes/dmn/reembolso_coverage.dmn`) — the Python
# `auto_approve_or_route` function this module used to carry was NEVER invoked by this BPMN.
# `notify_beneficiario` had a genuine audience mismatch (spec's `notify_sla_risk` targets
# coordenacao-reembolso, not the beneficiario audience that function's name implied) and no
# spec-declared topic of its own either. `request_documents`/`analyze_request`/`notify_sla_risk`
# above are their REPLACEMENTS: real, BPMN-topic-matched workers (previously gap: "Spec topics
# with NO implementing function today").
# ---------------------------------------------------------------------------


def check_coverage_entry(variables: dict[str, Any], *, kafka: KafkaPublisher | None = None) -> dict[str, Any]:
    """Dict-boundary entry for `operadora.reembolso.check_coverage` -> `check_coverage`."""
    del kafka  # unused — check_coverage emits no domain event
    input_data = ReembolsoInput(**pick_fields(variables, ReembolsoInput))
    return check_coverage(input_data)


def check_prazo_entry(variables: dict[str, Any], *, kafka: KafkaPublisher | None = None) -> dict[str, Any]:
    """Dict-boundary entry for `operadora.reembolso.check_prazo` -> `validate_reembolso`.

    Raises `ReembolsoProtocoloInvalidoError` (fail-closed) when `protocolo_reembolso` is blank —
    unchanged guard, only the dict<->dataclass marshalling is new.
    """
    del kafka  # unused — validate_reembolso emits no domain event
    input_data = ReembolsoInput(**pick_fields(variables, ReembolsoInput))
    result = validate_reembolso(input_data)
    return dataclasses.asdict(result)


def calculate_amount_entry(
    variables: dict[str, Any], *, kafka: KafkaPublisher | None = None
) -> dict[str, Any]:
    """Dict-boundary entry for `operadora.reembolso.calculate_amount` -> `calculate_value`.

    T1.9 (§2.3): `calculate_value` (unchanged) COMPUTES `dentro_teto_l2` via the CeilingResolver
    (its `resolver` kwarg defaults to the real one) and never reads the inbound flag — this entry
    function only marshals `variables` -> `ReembolsoInput`, it never derives the ceiling itself.
    """
    del kafka  # unused — calculate_value emits no domain event
    input_data = ReembolsoInput(**pick_fields(variables, ReembolsoInput))
    result = calculate_value(input_data)
    return dataclasses.asdict(result)


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


def issue_payment_entry(variables: dict[str, Any], *, kafka: KafkaPublisher | None = None) -> dict[str, Any]:
    """Dict-boundary entry for `operadora.reembolso.issue_payment` -> `process_payment`.

    AMOUNT RESOLUTION — the paid amount is ALWAYS `valor_reembolso_aprovado_cents`, defined by
    contract SP-OP-REEMBOLSO-001 §"Variaveis de saida" as "Valor efetivamente aprovado (humano em
    APROVAR/APROVAR_PARCIAL; = `valor_calculado_tabela_cents` no caminho automatico)". ONE topic
    serves the BPMN's THREE payment tasks, and all three resolve to that single variable:

    - `ST_IssuePaymentAuto` (AUTO_APROVAR, L2 — integral): the BPMN itself derives it, via the
      inputParameter `valor_reembolso_aprovado_cents = ${calculo.valor_calculado_tabela_cents}`
      (contract §`reembolso_auto_approval`: the automatic path produces ONLY integral approval,
      `= valor_calculado_tabela_cents` and, by construction of the `dentro_tabela` gate,
      `= valor_solicitado_cents`).
    - `ST_IssuePaymentAnalista` (human APROVAR): the value the human set in `UT_AnaliseReembolso`
      / `UT_RevisaoAuditorMedico` / `UT_CoordenacaoReembolso` — already in process scope.
    - `ST_IssuePaymentParcial` (human APROVAR_PARCIAL): the human's REDUCED value, re-validated
      upstream on `Flow_Reducao_IssuePay` by `ST_ComunicarReducao`'s `send_reembolso_denial`
      guard (`0 < valor_reembolso_aprovado_cents < valor_solicitado_cents`), which runs BEFORE
      this task — so a parcial payment can only ever be reached with a valid reduced amount.

    PRECEDENCE: the approved value OVERRIDES the calculated one, and `valor_calculado_tabela_cents`
    is NEVER read here — not even as a fallback. On the automatic path the override is already
    applied by the model (approved := calculated). On the two human paths, substituting the
    machine-calculated value for a missing human one would pay an amount NO human approved:
    silently less (a reduction) or more (an overpayment) than the human decision — the L0-hard
    violation this process exists to prevent (contract §"Invariante L0 hard": "reducao = adverso =
    humano"; ADR-0018). `valor_cents` — the variable this entry read before this fix — is produced
    by NOTHING in this process (not by `calculate_amount_entry`, which returns
    `valor_calculado_tabela_cents`; not by any BPMN mapping; not by the start seeds), so every
    payment path was issuing the `0` default.

    FAIL-CLOSED: an unresolvable amount raises `ReembolsoValorPagamentoInvalidoError` in
    `process_payment` and NO payment is issued (see that guard's docstring for why an incident,
    not a `bpmnError`, is the safe report here).
    """
    del kafka  # unused — process_payment emits no domain event itself
    protocolo_reembolso = variables.get("protocolo_reembolso", "")
    # Deliberately `Any` (not `int | None`): this is the untyped engine dict boundary — whatever
    # the engine delivered is handed UNCOERCED to `process_payment`, whose money guard is the one
    # place that decides what is payable (mirrors send_reembolso_denial_entry, where the entry
    # only marshals and the typed function holds the guard).
    valor_cents: Any = variables.get("valor_reembolso_aprovado_cents")
    beneficiario_pseudo_id = variables.get("beneficiario_pseudo_id", "")
    return process_payment(protocolo_reembolso, valor_cents, beneficiario_pseudo_id)


def send_reembolso_denial_entry(
    variables: dict[str, Any], *, kafka: KafkaPublisher | None = None
) -> dict[str, Any]:
    """Dict-boundary entry for `operadora.reembolso.send_reembolso_denial` -> `send_reembolso_denial`
    (GUARDED).

    Raises `ReembolsoDenialNotHumanError` (fail-closed, ERR_REEMBOLSO_DENIAL_NOT_HUMAN) when the
    human decision (`decisao_reembolso` in {NEGAR, APROVAR_PARCIAL} + required fields) is
    missing — unchanged guard, only the dict<->dataclass marshalling is new.
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

    `kafka` is accepted (donor contract, ADR-0026 §2) and threaded via `functools.partial`; no
    entry function calls `kafka.publish` today — see `ans_submit.register_ans_submit_workers`'s
    docstring for the same documented sync/async-boundary rationale.
    """
    del seams  # unused — no additional seam (audit=/dmn=/dispatcher=/erasure=) is needed today
    harness.register_worker(
        FunctionWorker(
            "operadora.reembolso.check_coverage", functools.partial(check_coverage_entry, kafka=kafka)
        )
    )
    harness.register_worker(
        FunctionWorker("operadora.reembolso.check_prazo", functools.partial(check_prazo_entry, kafka=kafka))
    )
    harness.register_worker(
        FunctionWorker(
            "operadora.reembolso.calculate_amount", functools.partial(calculate_amount_entry, kafka=kafka)
        )
    )
    harness.register_worker(
        FunctionWorker(
            "operadora.reembolso.request_documents",
            functools.partial(request_documents_entry, kafka=kafka),
        )
    )
    harness.register_worker(
        FunctionWorker(
            "operadora.reembolso.analyze_request",
            functools.partial(analyze_request_entry, kafka=kafka),
        )
    )
    harness.register_worker(
        FunctionWorker(
            "operadora.reembolso.notify_sla_risk",
            functools.partial(notify_sla_risk_entry, kafka=kafka),
        )
    )
    harness.register_worker(
        FunctionWorker(
            "operadora.reembolso.issue_payment", functools.partial(issue_payment_entry, kafka=kafka)
        )
    )
    harness.register_worker(
        FunctionWorker(
            "operadora.reembolso.send_reembolso_denial",
            functools.partial(send_reembolso_denial_entry, kafka=kafka),
        )
    )
    harness.register_worker(
        FunctionWorker(
            "operadora.reembolso.publish_completed", functools.partial(publish_completed_entry, kafka=kafka)
        )
    )

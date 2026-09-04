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
import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import structlog

from maezo.tools.workers.base import FunctionWorker, pick_fields
from maezo.tools.workers.ceilings import CeilingResolver
from maezo.tools.workers.harness import WorkerBpmnError

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
# BRT_Calculo (DMN `reembolso_calculo`) — the SINGLE source of the reimbursement amount
# ---------------------------------------------------------------------------
#
# GAP F-1 / ADR-0012 ("a DMN e a fonte unica da regra de negocio deterministica"). This module
# used to carry `_BASE_VALUES_CENTS` (consulta 35000, exame_especial 45000, ...) plus
# `_MULTIPLO_ACESSO = 1.5` / `_TIPOS_COM_MULTIPLICADOR` — a SHADOW reference table that
# RE-ORIGINATED the money in Python and DISAGREED with the authoritative DMN for the same
# categoria (`spec/processes/dmn/reembolso_calculo.dmn` pays 12000 for `consulta` and 25000 for
# `exame_especial`, and has no row at all for `internacao`/`opme`/`alta_complexidade`, which
# therefore resolve SEM_TABELA there). All three are DELETED. There is no shim and no "fall back
# to the Python table when the DMN variable is missing": an absent or malformed DMN result FAILS
# THE MONEY PATH CLOSED (`ReembolsoCalculoIndisponivelError`), because guessing an amount is the
# defect itself. The VALUES inside the DMN are an owner/atuarial/regulatorio decision (the table
# is DRAFT and blocked on that sign-off) and are NOT this module's to set, reconcile or round.
#
# WHERE THE VALUES COME FROM NOW. `BRT_Calculo`
# (`spec/processes/bpmn/SP-OP-REEMBOLSO-001_Reembolso_Beneficiario.bpmn`,
# `camunda:decisionRef="reembolso_calculo"`, `mapDecisionResult="singleResult"`,
# `camunda:resultVariable="calculo"`) is the ONLY predecessor of `ST_CalculateAmount`
# (`Flow_BRTCalc_Calc`) — GAP-REEMBOLSO-5 ordered it that way precisely so the decision is
# already resolved when this worker runs. Its three declared outputs
# (`reembolso_calculo.dmn:37-39`) are this worker's inputs, consumed UNCHANGED and never
# recomputed: `valor_calculado_tabela_cents` (integer centavos), `multiplo_tabela_aplicado`
# (double), `fonte_tabela` (string). What is left in Python is exactly two COMPARISONS over
# values it did not originate — `dentro_tabela` and `dentro_teto_l2`.
#
# DELIVERY SHAPE (why the BPMN needed an input mapping). `calculo` is a `singleResult` Java Map
# stored as an `Object` variable; `fetchAndLock`'s `deserializeValues` defaults to false and
# `harness._from_camunda_var` re-decodes only `type == "Json"`, so the Map reaches an external
# worker OPAQUE (auth item-9, live-caught on CIB Seven 2.1.0 — `auth.py::_auto_approval_
# sanctioned`). `ST_CalculateAmount` therefore now carries the SAME flattening idiom the repo
# already uses on `ST_IssuePaymentAuto` (`valor_reembolso_aprovado_cents =
# ${calculo.valor_calculado_tabela_cents}`) and on AUTH's `ST_EmitirAutorizacaoAuto`: three
# activity-LOCAL variables the engine evaluates from `calculo` on every entry into the task.
# NOT SPOOFABLE AT START: an activity-local variable shadows any process-scope homonym a
# `start_process` payload could seed (the start seeds DO carry `valor_calculado_tabela_cents`),
# and `BRT_Calculo` — the sole inbound edge — always rewrites `calculo` first.
#
# WHAT THE DMN DOES NOT DELIVER: its decision-definition VERSION. `mapDecisionResult` returns
# output values only, and no JUEL expression on a `businessRuleTask` exposes the decision
# definition itself, so the version is NOT observable from inside this worker. It therefore
# cites the COORDINATES of the decision instance (`dmn_decisao_id` = the `camunda:decisionRef`,
# `dmn_atividade_bpmn` = the activity id) — which together locate the engine's own
# `/history/decision-instance` record, where the authoritative version lives — and NEVER a
# version number it did not observe. Stamping the DMN file's declared `v0.1.0` here would claim
# provenance for a deployment this process never checked: the same species of fabricated
# corroboration the M-2 fix removed. See `docs/review-queue.md` (GAP F-1 row).
# ---------------------------------------------------------------------------

#: `camunda:decisionRef` of `BRT_Calculo` — the decision that OWNS this money rule (ADR-0012).
_DMN_DECISAO_ID = "reembolso_calculo"
#: The BPMN activity that evaluates it. Decision id + activity id are the citation the audit
#: chain carries; the concrete version is the engine's decision-instance record for this pair.
_DMN_ATIVIDADE_BPMN = "BRT_Calculo"

#: The DMN catch-all's provenance token (`reembolso_calculo.dmn:78`, rule `r_catchall`: categoria
#: with no reference-table row -> value 0, multiplo 0.0, `SEM_TABELA`). Pinned identically in the
#: contract, in the test spec and in an engine-side assertion. The worker no longer INVENTS a
#: provenance vocabulary (the retired `TABELA_REFERENCIA` / `TABELA_REFERENCIA_MULTIPLICADA`
#: tokens matched nothing in the DMN); it PASSES THROUGH whatever token the DMN emitted, and only
#: needs to recognise this one to force `dentro_tabela=False`.
_FONTE_SEM_TABELA = "SEM_TABELA"


def _is_sem_tabela(fonte_tabela: str) -> bool:
    """True iff ``fonte_tabela`` denotes the DMN's `SEM_TABELA` catch-all — normalized ONCE, here.

    Whitespace and case are folded before the comparison — and nowhere else in the module — so
    the single deny-token check cannot be fooled the way it was before VERIFY-WP-REEMBOLSO MINOR-2:
    ``_require_dmn_fonte`` validated with ``.strip()`` while this comparison used the raw string,
    so `" SEM_TABELA "` or `"sem_tabela"` validated as non-blank yet compared UNEQUAL to the exact
    literal and therefore read as a table HIT — a fail-OPEN direction in a money gate.

    Same STRUCTURE as the cited precedent (`auth.py::_auto_approval_sanctioned`: normalize once,
    compare the normalized form to a single literal), but the opposite polarity of normalization
    on purpose: `auth.py` normalizes its ALLOW literal (`AUTO_APROVAR`) with `.strip()` only, no
    case fold — fewer matches there means fewer auto-approvals, which is fail-closed for an ALLOW
    token. Here the literal is a DENY token feeding an allow-by-default vocabulary (any non-
    `SEM_TABELA` token with a positive value reads as a table hit, deliberately, per
    VERIFY-WP-REEMBOLSO §3(d) — an allowlist of hit tokens would break the next
    `TABELA_REFERENCIA_*` category the DMN grows). For a deny token under allow-by-default, MORE
    matches means fewer hits, so folding whitespace AND case here is the fail-closed choice: any
    token that could plausibly BE the catch-all, however the DMN or a decoding hop mangled its
    spacing or case, must be read as "no table" (human review), never as "a table hit" (potential
    auto-approval). The raw ``fonte_tabela`` is never mutated — only this comparison's input is
    normalized — so the audit trail still records exactly what the DMN emitted.
    """
    return fonte_tabela.strip().upper() == _FONTE_SEM_TABELA


#: The three activity-LOCAL variables `ST_CalculateAmount`'s `camunda:inputParameter` mapping
#: flattens out of `calculo`. Names are the DMN's OWN output names (`reembolso_calculo.dmn:37-39`).
_DMN_VAR_VALOR = "valor_calculado_tabela_cents"
_DMN_VAR_MULTIPLO = "multiplo_tabela_aplicado"
_DMN_VAR_FONTE = "fonte_tabela"
#: The un-flattened `singleResult` map — a belt-and-braces SECOND channel, exactly as
#: `auth.py::_auto_approval_sanctioned` keeps one: it is what in-process fixtures carry and what
#: an engine configured to deserialize Object variables would deliver. Never a fallback to a
#: Python table: both channels carry the DMN's own answer, or there is no answer, or — when both
#: are present and DISAGREE — neither is trusted (see `_pick_calculo_channel`).
_DMN_RESULT_VAR = "calculo"


@dataclass(frozen=True)
class ReembolsoCalculoDmn:
    """`BRT_Calculo`'s three outputs, validated, exactly as the DMN produced them.

    Frozen, and constructed ONLY by :func:`_require_calculo_dmn`, so nothing between the engine
    boundary and the ADR-0007 audit chain can re-tune the amount. Bundling the three keeps them
    from drifting apart on the way in (the M-2 defect was exactly that drift: a value from one
    branch with a multiplier and a source label from another).
    """

    valor_calculado_tabela_cents: int
    multiplo_tabela_aplicado: float
    fonte_tabela: str

    @property
    def categoria_na_tabela(self) -> bool:
        """True iff the DMN matched a reference-table row instead of falling to its catch-all.

        Derived from ``fonte_tabela`` rather than stored, so the flag the fail-closed
        ``dentro_tabela`` gate reads can never contradict the provenance token the audit chain
        records. The comparison itself is delegated to :func:`_is_sem_tabela` — the ONE place in
        this module that normalizes the catch-all token (VERIFY-WP-REEMBOLSO MINOR-2) — so this
        property can never drift back to a raw, un-normalized comparison.
        """
        return not _is_sem_tabela(self.fonte_tabela)


# ---------------------------------------------------------------------------
# Error codes
# ---------------------------------------------------------------------------

#: `check_coverage` raises this (as a `WorkerBpmnError`, below) when `protocolo_reembolso` arrives
#: absent/blank — a TECHNICAL origin/consistency guard (G2-val, ADR-0030 §2 + migration Tier-2:
#: the worker never decides to pay/deny, it only signals bad-at-source request data). MODELED
#: boundary error: `BE_ReembolsoProtocoloInvalido` (Error_ReembolsoProtocoloInvalido) on
#: `ST_CheckCoverage` routes to the NEUTRO terminal `End_ReembolsoProtocoloInvalido` — the BPMN's
#: own documentation on `ST_CheckCoverage`: "protocolo_reembolso inconsistente na origem lanca
#: ERR_REEMBOLSO_INVALID_PROTOCOLO ... capturado pelo boundary BE_ReembolsoProtocoloInvalido".
#: NOT a `*_NOT_HUMAN` guard, so NOT T-E-gated (ADR-0030 §4) — lands directly in
#: `PRODUCTION_BPMN_ERROR_ALLOWLIST` (mirrors `pagto`'s `ERR_PAGTO_ORDEM_INVALIDA` /
#: `credenciamento`'s `ERR_CRED_INVALID_PRESTADOR`, the item-9 bucket-3 precedent).
ERR_REEMBOLSO_INVALID_PROTOCOLO = "ERR_REEMBOLSO_INVALID_PROTOCOLO"

#: Consumption-covered (`scripts/ci/check_bpmn_error_allowlist.py`'s "simple rule"):
#: `operadora.reembolso.check_coverage` is consumed ONLY by SP-OP-REEMBOLSO-001, which declares
#: this errorCode on `BE_ReembolsoProtocoloInvalido`. Unioned into `worker_runtime/service.py`'s
#: `_GATE_PROVEN_BPMN_ERROR_CODES`. `ERR_REEMBOLSO_DENIAL_NOT_HUMAN` is DELIBERATELY excluded: it
#: has no modeled boundary catch anywhere in `SP-OP-REEMBOLSO-001_Reembolso_Beneficiario.bpmn`
#: (`send_reembolso_denial` stays a `PermissionError` -> incident, unchanged).
REEMBOLSO_BPMN_ERROR_ALLOWLIST: frozenset[str] = frozenset({ERR_REEMBOLSO_INVALID_PROTOCOLO})


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
    """Raised by `validate_reembolso` (topic `operadora.reembolso.check_prazo`) when protocolo/guia
    is inconsistent (ERR_REEMBOLSO_INVALID_PROTOCOLO) — kept as a defensive, non-modeled fallback.

    DEFENSE IN DEPTH, not the modeled boundary path (ADR-0030): the actual `bpmn:error@errorCode=
    ERR_REEMBOLSO_INVALID_PROTOCOLO` boundary (`BE_ReembolsoProtocoloInvalido`) is declared on
    `ST_CheckCoverage` (topic `operadora.reembolso.check_coverage`), NOT on `ST_CheckPrazo`
    (this raise's own topic) — `check_coverage` (below) now carries the MODELED
    `WorkerBpmnError(ERR_REEMBOLSO_INVALID_PROTOCOLO)` guard for that condition, and
    `ST_CheckCoverage` runs BEFORE `ST_CheckPrazo` in the modeled flow
    (`Flow_PubReceived_Coverage -> ST_CheckCoverage -> Flow_Coverage_Prazo -> ST_CheckPrazo`), so a
    blank `protocolo_reembolso` is refused there first and this function is never reached with one
    via the live process. This `ValueError` subclass therefore stays a plain coded exception
    (`FunctionWorker` never reclassifies an already-`ValueError` subclass, `base.py:276-293`) ->
    `failure(retries=0)` incident if ever reached directly (e.g. a direct unit call, or a future
    reordering) — never a `WorkerBpmnError`, since raising one on THIS topic would be a
    clause-(b) violation (no boundary is declared on `ST_CheckPrazo`).
    """

    def __init__(self, detail: str = "") -> None:
        super().__init__(
            f"{ERR_REEMBOLSO_INVALID_PROTOCOLO}: {detail}" if detail else ERR_REEMBOLSO_INVALID_PROTOCOLO
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


class ReembolsoCalculoIndisponivelError(ValueError):
    """BRT_Calculo's DMN result is absent or malformed (ERR_REEMBOLSO_CALCULO_DMN_INDISPONIVEL).

    Fail-closed money guard for `calculate_amount_entry` (GAP F-1). The reimbursement reference
    amount is owned by `spec/processes/dmn/reembolso_calculo.dmn` and delivered by `BRT_Calculo`
    (see the module note above). `ST_CalculateAmount` is reachable ONLY from that
    businessRuleTask (`Flow_BRTCalc_Calc`), so a missing or ill-typed
    `valor_calculado_tabela_cents` / `multiplo_tabela_aplicado` / `fonte_tabela` is a MODEL OR
    DEPLOY DEFECT upstream, never a value to salvage. The safe outcome is to compute nothing:
    no `dentro_tabela`, no `dentro_teto_l2`, hence no `AUTO_APROVAR` input for `BRT_AutoApproval`
    and no automatic payment — plus a human-visible engine incident.

    A `ValueError` subclass DELIBERATELY, exactly like `ReembolsoValorPagamentoInvalidoError` and
    for the same reason: the harness routes the `ValueError` family to `failure(retries=0)`
    (`harness.py` §9) — an immediate incident, never a silent retry. It is NOT a `WorkerBpmnError`
    and `ERR_REEMBOLSO_CALCULO_DMN_INDISPONIVEL` is deliberately NOT an ADR-0030 boundary code:
    `ST_CalculateAmount` carries no error boundary event (BPMN `attachedToRef`: only
    `ST_CheckCoverage` and `UT_AnaliseReembolso`), and an unmodeled `bpmnError` silently ENDS the
    process scope on CIB Seven 2.1.0 instead of opening an incident (ADR-0030 §2) — i.e. it would
    quietly close a reimbursement request whose amount was never determined.
    """

    def __init__(self, detail: str = "") -> None:
        super().__init__(
            f"ERR_REEMBOLSO_CALCULO_DMN_INDISPONIVEL: {detail}"
            if detail
            else "ERR_REEMBOLSO_CALCULO_DMN_INDISPONIVEL"
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
    """Output of calculate_value — two comparisons plus the DMN's own values, never a decision.

    `valor_calculado_tabela_cents` / `multiplo_tabela_aplicado` / `fonte_tabela` are RELAYED
    verbatim from `BRT_Calculo` (DMN `reembolso_calculo`); this worker never originates them.
    `dmn_decisao_id` / `dmn_atividade_bpmn` cite WHICH decision produced them, so every audit
    record (ADR-0007) names its basis instead of implying a Python table (GAP F-1).

    Defaults are the fail-safe shape, not a usable answer: `multiplo_tabela_aplicado` defaults to
    `0.0` ("no multiplier was applied to anything", the DMN catch-all's own value) rather than the
    old `1.0`, which read as a neutral table hit on an object that had never seen the table.
    """

    valor_calculado_tabela_cents: int = 0
    valor_solicitado_cents: int = 0
    dentro_tabela: bool = False
    dentro_teto_l2: bool = False
    multiplo_tabela_aplicado: float = 0.0
    fonte_tabela: str = ""
    dmn_decisao_id: str = ""
    dmn_atividade_bpmn: str = ""


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

    ORIGIN GUARD (ADR-0030 Tier-2 G2-val, root-cause fix): `ST_CheckCoverage` is the boundary-
    carrying external task for `ERR_REEMBOLSO_INVALID_PROTOCOLO` (`BE_ReembolsoProtocoloInvalido`
    -> `End_ReembolsoProtocoloInvalido`, a NEUTRO fail-safe terminal — the BPMN's own
    documentation: "protocolo/guia inconsistente na origem ... NUNCA decide pagar/negar/reduzir").
    A blank/absent `protocolo_reembolso` therefore raises the MODELED `WorkerBpmnError` here —
    checked BEFORE any coverage resolution, so an inconsistent request never reaches the DMN chain.
    See `ReembolsoProtocoloInvalidoError`'s docstring for why `validate_reembolso`'s own (different
    topic, no modeled boundary) check of the same field is a defensive fallback, not the fix.
    """
    if not input_data.protocolo_reembolso.strip():
        logger.error(
            "reembolso.check_coverage.protocolo_invalido",
            protocolo_reembolso=input_data.protocolo_reembolso,
        )
        raise WorkerBpmnError(ERR_REEMBOLSO_INVALID_PROTOCOLO, "protocolo_reembolso ausente")

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
    calculo: ReembolsoCalculoDmn,
    *,
    resolver: CeilingResolver | None = None,
) -> ReembolsoCalculoResult:
    """Resolve the two comparison facts over the DMN's amount — never the amount itself.

    ``calculo`` is `BRT_Calculo`'s already-resolved result (DMN `reembolso_calculo`), REQUIRED and
    with no default: this function cannot be called without the DMN's answer, which is how the
    "no shim, no Python fallback table" property of GAP F-1 is enforced by the signature rather
    than by a comment. `calculate_amount_entry` builds it with :func:`_require_calculo_dmn`, which
    fails the money path closed when the engine did not deliver it.

    What this function still computes is exactly two BOOLEANS, both comparisons over values it did
    not originate:

    - ``dentro_tabela`` — "the claimed amount is within the REFERENCE TABLE". FAIL-CLOSED on three
      independent conditions, all of which must hold: the DMN matched a real row
      (``categoria_na_tabela``, i.e. not :func:`_is_sem_tabela` on the normalized token), that
      row carries a positive reference value, and
      ``valor_solicitado_cents <= valor_calculado_tabela_cents``. With no row there is nothing to
      be within, so the claim is False BY CONSTRUCTION and never merely by arithmetic — the `<=`
      alone would read True for a `valor_solicitado_cents=0` request against the catch-all's 0.
      This is the posture the DMN catch-all and the BPMN already prescribe ("fora de tabela
      (SEM_TABELA) -> dentro_tabela=false -> analise humana", `ST_CalculateAmount` documentation).
    - ``dentro_teto_l2`` — COMPUTED from the tenant governance ceiling
      (``reembolso_auto_approval.max_value_brl``) via the CeilingResolver, comparing the DMN's
      reference value against the teto. The inbound ``input_data.dentro_teto_l2`` is NEVER read on
      this path (design T1.9 §2.3, defect B3; canary `tests/unit/sec/test_dentro_teto_source.py`).
      ``resolver`` is injectable for tests; the default resolves the ceiling from the real
      ``spec/policies/autonomy`` matrix. Under SEM_TABELA the reference value is 0, so any positive
      ceiling reads True — that fact is VACUOUS and is gated shut by ``dentro_tabela=False`` (the
      DMN's `reembolso_auto_approval` AUTO_APROVAR rule requires both).

    Pure arithmetic — NEVER a decision to pay or deny. The DMN says how much would be due; the
    eventual reduction (APROVAR_PARCIAL) is a HUMAN decision.

    MONEY IS RELAYED, NOT RE-DERIVED (GAP F-1). ``valor_calculado_tabela_cents``,
    ``multiplo_tabela_aplicado`` and ``fonte_tabela`` are copied out of ``calculo`` unchanged.
    There is no multiplication, no ``int(base * multiplo)``, no rounding and no coercion anywhere
    on this path any more — the integer centavos the DMN emitted are the integer centavos the
    audit chain records, so the old `int(base * 1.5)` truncation surface simply does not exist.

    HONEST PROVENANCE (defect M-2, preserved and strengthened). The facts written into the
    ADR-0007 audit chain describe what actually happened: ``fonte_tabela`` is now the DMN's OWN
    per-rule token (`TABELA_REFERENCIA_CONSULTA` / `_EXAME` / `_TERAPIA` / `SEM_TABELA`) instead of
    a worker-invented vocabulary, ``multiplo_tabela_aplicado`` is the multiplier the DMN really
    applied, and ``dmn_decisao_id`` / ``dmn_atividade_bpmn`` name the decision that is the basis
    for all of it.
    """
    resolver = resolver if resolver is not None else CeilingResolver()

    logger.info(
        "reembolso.calculate_value.start",
        protocolo_reembolso=input_data.protocolo_reembolso,
        valor_solicitado_cents=input_data.valor_solicitado_cents,
        dmn_decisao_id=_DMN_DECISAO_ID,
        fonte_tabela=calculo.fonte_tabela,
    )

    valor_calculado = calculo.valor_calculado_tabela_cents

    dentro_tabela = (
        calculo.categoria_na_tabela
        and valor_calculado > 0
        and input_data.valor_solicitado_cents <= valor_calculado
    )
    # COMPUTE the ceiling fact from policy — compares the DMN's reference value (centavos)
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
        multiplo_tabela_aplicado=calculo.multiplo_tabela_aplicado,
        fonte_tabela=calculo.fonte_tabela,
        dmn_decisao_id=_DMN_DECISAO_ID,
        dmn_atividade_bpmn=_DMN_ATIVIDADE_BPMN,
    )

    logger.info(
        "reembolso.calculate_value.complete",
        valor_calculado=result.valor_calculado_tabela_cents,
        dentro_tabela=result.dentro_tabela,
        multiplo_tabela_aplicado=result.multiplo_tabela_aplicado,
        fonte_tabela=result.fonte_tabela,
        dmn_decisao_id=result.dmn_decisao_id,
        dmn_atividade_bpmn=result.dmn_atividade_bpmn,
    )
    return result


def request_documents(
    beneficiario_pseudo_id: str,
    protocolo_reembolso: str,
    message_type: str = "pendencia_documentacao",
) -> dict[str, Any]:
    """Registra que a ETAPA de abertura da pendencia rodou. Retorna `{}` — NAO afirma nada.

    Serve `ST_SolicitarDocumentos` ("Solicitar documentacao ao beneficiario",
    `spec/processes/bpmn/SP-OP-REEMBOLSO-001_Reembolso_Beneficiario.bpmn:132-138`), topico
    `operadora.reembolso.request_documents`.

    Nao adversa: emparelha com a corrida de `GW_AguardarDocs` (message
    `msg.reembolso.docs_received` OU timer `ICE_PrazoPendencia`). Um beneficiario que nunca
    responde e decidido por um HUMANO em `UT_DecidirPendenciaExpirada` — este worker nunca
    auto-cancela nem auto-indefere.

    GAP-REEMBOLSO-8 / FAB-NOTIFIED-TRIO (o motivo desta docstring). O retorno era, em toda
    entrega e sem calcular nada::

        {"notified": True, "beneficiario_pseudo_id": ..., "protocolo_reembolso": ...,
         "status": "pended", "message_type": ...}

    A docstring ANTERIOR ja divulgava, no mesmo corpo de funcao, que este worker nunca publica o
    evento de pendencia — e devolvia `notified=True` assim mesmo: a divulgacao e o retorno se
    contradiziam. `notified` nao era computado de nada (nenhum canal contatado, nenhuma entrega
    observada) e a harness carrega o retorno para o escopo do processo no `complete`
    (`harness.py:1778-1782`), entao a constante entrava na instancia como trilha de auditoria.

    DUAS AFIRMACOES DA DOCSTRING ANTIGA ESTAVAM DESATUALIZADAS, e foram removidas em vez de
    reproduzidas: (1) "the BPMN's `event_topic_pended` inputParameter" — esse parametro NAO
    existe mais neste BPMN (`grep -n event_topic_pended spec/processes/bpmn/*.bpmn` so encontra
    CRED-001 e RECURSO-001); (2) "no downstream `ST_Publish*` exists on this branch" — existe:
    `ST_PublishReembolsoPended` (`:151-161`) publica `agents.events.reembolso.pended` via o
    generico `operadora.events.publish`, uma task adiante, em AMBOS os ramos que chegam aqui
    (pendencia inicial e `SOLICITAR_INFO` do analista). Ambas foram introduzidas por t3.1
    (`docs/evidence-ledger.md:125`); a obrigacao "produz" do contrato ja e cumprida — pelo BPMN,
    nao por este worker.

    POR QUE `{}` E NAO UM PUBLISH REAL (opcao (a) avaliada e rejeitada com evidencia). O evento de
    dominio ja e publicado (acima), entao publicar aqui duplicaria um evento real. E nao existe
    canal externo ao BENEFICIARIO para um worker: as seams sao `engine`/`dmn`/`kafka`/`audit_sink`
    (`harness.py`); o unico canal de mensageria da arvore (`mcp_whatsapp`) so e alcancavel pelos
    agentes conversacionais e pelo servico de webhooks, nunca por um worker BPMN. Um registro
    Kafka interno seria um PEDIDO de disparo, jamais a prova de que o beneficiario foi avisado.

    O QUE SE PERDE AO DEVOLVER `{}`: nada consumido. As cinco chaves tinham ZERO consumidores —
    nenhum `conditionExpression`, nenhum `inputExpression` de DMN (nenhuma das quatro DMNs de
    reembolso le `status`/`notified`/`message_type`), nenhum modelo de entrada de worker a jusante
    e nenhuma linha de contrato. `status: "pended"` era, alem disso, a unica das cinco que
    ESCREVIA um valor novo no escopo — sob um nome generico, sem dono declarado.
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
    """Registra que a ETAPA de alerta de risco de SLA rodou. Retorna `{}` — NAO afirma nada.

    Serve `ST_NotificarRiscoSla` (topico `operadora.reembolso.notify_sla_risk`), alimentado SO
    pelo boundary NAO-interruptivo `BT_AlertaSla` em `UT_AnaliseReembolso`. Informativa e jamais
    adversa: a User Task segue aberta, nenhuma decisao e tomada ou alterada. Dispara em
    `sla.sla_alerta` (DMN `reembolso_sla`).

    FAB-SLA-RISK-NOTIFIED-SLICE4 (o motivo desta docstring). O retorno era, em toda entrega e sem
    calcular nada, `{"sla_risk_notified": True, "protocolo_reembolso": ...}`. NENHUM canal e
    contatado por esta funcao — a `coordenacao-reembolso` pode nunca ter sido avisada — e a
    harness grava o retorno no escopo do processo no `complete` (`harness.py:1779-1783`), entao a
    constante entrava na instancia como trilha de auditoria. Mesma especie de
    `contas.notify_sla_risk` (FAB-NOTIFIED-TRIO), sob outra chave.

    ZERO CONSUMIDORES (mapa refeito antes de editar): `sla_risk_notified` nao aparece em nenhum
    `conditionExpression` de BPMN, nenhum `inputExpression` de DMN, nenhum worker a jusante e
    nenhuma linha de contrato — so nos proprios produtores. `protocolo_reembolso` era eco do
    proprio input, ja no escopo. A observabilidade da etapa fica no `logger.info` abaixo, que
    declara explicitamente `notified_asserted=False`.
    """
    logger.info(
        "reembolso.notify_sla_risk",
        protocolo_reembolso=input_data.protocolo_reembolso,
        tipo_reembolso=input_data.tipo_reembolso,
        notified_asserted=False,
    )
    return {}


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


def _require_calculo_dmn(process_vars: dict[str, Any]) -> ReembolsoCalculoDmn:
    """BRT_Calculo's three DMN outputs, or FAIL CLOSED — the money path's single entry (GAP F-1).

    This function replaced `_lookup_tabela_referencia`, the Python shadow table that used to
    RE-ORIGINATE the reference amount (`_BASE_VALUES_CENTS` / `_MULTIPLO_ACESSO`). It does not
    compute an amount and cannot: it only ACCEPTS what
    `spec/processes/dmn/reembolso_calculo.dmn` produced, or refuses.

    THE CHANNELS (mirrors `auth.py::_auto_approval_sanctioned`):

      1. the three activity-LOCAL flat variables `ST_CalculateAmount`'s `camunda:inputParameter`
         mapping flattens out of `calculo` — the channel that actually works on a real engine,
         and the non-spoofable one (an activity-local variable shadows any process-scope
         homonym, including a `valor_calculado_tabela_cents` seeded at `start_process`);
      2. `calculo` as a real `Mapping` — belt-and-braces for in-process fixtures and for any
         engine/config that DOES deserialize the `singleResult` Object.

    Both channels carry the DMN's own answer, never a fallback to a Python table. When only ONE
    channel is complete, that one is used (see `_pick_calculo_channel`). When BOTH are complete
    they must AGREE — on a correctly configured engine they are two views of the same
    `BRT_Calculo` result — and a DISAGREEMENT fails the money path CLOSED
    (`ReembolsoCalculoIndisponivelError`, VERIFY-WP-REEMBOLSO MINOR-1) rather than silently
    preferring the flat channel. When NEITHER yields a well-formed triple this raises the same
    error, and the task becomes an engine incident with no `dentro_tabela` / `dentro_teto_l2`
    written, hence no `AUTO_APROVAR` input for `BRT_AutoApproval` and no automatic payment.
    Absence is a model or deploy defect (`ST_CalculateAmount` is reachable ONLY from
    `BRT_Calculo`), never a value to guess at.

    VALIDATION, field by field — every rejection returns the same safe outcome:

    - `valor_calculado_tabela_cents` (DMN `typeRef="integer"`, ADR-0018 integer centavos): must be
      an `int`. `bool` is rejected first (it is an `int` subclass and would read as 1 centavo);
      `float` is rejected even when integral (`12000.0`) because money is never rounded or
      coerced here; `str` and every other type are a typing defect upstream. Negative is rejected
      (no reference value is negative); `0` is ACCEPTED because it is the catch-all's own legal
      value, and it is `fonte_tabela` that then forces `dentro_tabela=False`.
    - `multiplo_tabela_aplicado` (DMN `typeRef="double"`): `int` or `float`, never `bool`, must be
      finite and non-negative. Kept as `float` — it is a provenance fact for the audit chain, and
      NOTHING multiplies by it any more (the DMN already emitted the final integer).
    - `fonte_tabela` (DMN `typeRef="string"`): a non-blank `str`. Blank provenance would let a
      value with no recorded origin reach the human reviewer's dossie, which is the M-2 defect.
      The VOCABULARY is not validated here: the tokens belong to the DMN and this worker must not
      grow an opinion about which ones are legal (it only recognises `SEM_TABELA`, the catch-all).
    """
    raw = _pick_calculo_channel(process_vars)
    if raw is None:
        raise ReembolsoCalculoIndisponivelError(
            f"resultado da DMN `{_DMN_DECISAO_ID}` ({_DMN_ATIVIDADE_BPMN}) ausente — nem as "
            f"variaveis achatadas ({_DMN_VAR_VALOR}/{_DMN_VAR_MULTIPLO}/{_DMN_VAR_FONTE}) nem "
            f"`{_DMN_RESULT_VAR}` chegaram ao worker"
        )
    valor_raw, multiplo_raw, fonte_raw = raw

    return ReembolsoCalculoDmn(
        valor_calculado_tabela_cents=_require_dmn_valor_cents(valor_raw),
        multiplo_tabela_aplicado=_require_dmn_multiplo(multiplo_raw),
        fonte_tabela=_require_dmn_fonte(fonte_raw),
    )


def _pick_calculo_channel(process_vars: dict[str, Any]) -> tuple[Any, Any, Any] | None:
    """The raw `(valor, multiplo, fonte)` triple, reconciled across the flat and `calculo` channels.

    A channel counts as PRESENT only when it carries all three keys — a partial delivery is a
    defect, not a channel to complete from another one (mixing halves of two deliveries is how a
    value and its provenance drift apart). Returns `None` when neither channel is complete;
    validation of the values themselves is the caller's.

    RECONCILIATION (VERIFY-WP-REEMBOLSO MINOR-1). When only one channel is complete, that one is
    used — the documented precedence is unaffected. When BOTH are complete, they must AGREE
    (identical triple, by `==`, before either is validated): on a correctly configured engine they
    are two views of the SAME `BRT_Calculo` result, so agreement costs nothing. A DISAGREEMENT
    means the flat locals and the `calculo` Mapping are telling two different stories about what
    the DMN decided — exactly the drift/deserialize-mode case the module docstring warns is not a
    fallback to paper over — so this fails the money path CLOSED
    (`ReembolsoCalculoIndisponivelError`) instead of silently preferring the flat channel, which is
    what this module used to do before this fix.
    """
    flat_keys = (_DMN_VAR_VALOR, _DMN_VAR_MULTIPLO, _DMN_VAR_FONTE)
    flat: tuple[Any, Any, Any] | None = None
    if all(key in process_vars for key in flat_keys):
        flat = (
            process_vars[_DMN_VAR_VALOR],
            process_vars[_DMN_VAR_MULTIPLO],
            process_vars[_DMN_VAR_FONTE],
        )

    nested = process_vars.get(_DMN_RESULT_VAR)
    mapping: tuple[Any, Any, Any] | None = None
    if isinstance(nested, Mapping) and all(key in nested for key in flat_keys):
        mapping = (nested[_DMN_VAR_VALOR], nested[_DMN_VAR_MULTIPLO], nested[_DMN_VAR_FONTE])

    if flat is not None and mapping is not None:
        if flat != mapping:
            raise ReembolsoCalculoIndisponivelError(
                "canais divergentes: os `camunda:inputParameter` achatados "
                f"({_DMN_VAR_VALOR}={flat[0]!r}, {_DMN_VAR_MULTIPLO}={flat[1]!r}, "
                f"{_DMN_VAR_FONTE}={flat[2]!r}) discordam do Mapping `{_DMN_RESULT_VAR}` "
                f"({_DMN_VAR_VALOR}={mapping[0]!r}, {_DMN_VAR_MULTIPLO}={mapping[1]!r}, "
                f"{_DMN_VAR_FONTE}={mapping[2]!r}) — nenhum dos dois e' preferido silenciosamente"
            )
        return flat

    if flat is not None:
        return flat
    if mapping is not None:
        return mapping
    return None


def _require_dmn_valor_cents(value: Any) -> int:
    """`valor_calculado_tabela_cents` as integer centavos, or FAIL CLOSED (see caller's docstring)."""
    if value is None:
        raise ReembolsoCalculoIndisponivelError(f"{_DMN_VAR_VALOR} ausente no resultado da DMN")
    if isinstance(value, bool):
        raise ReembolsoCalculoIndisponivelError(f"{_DMN_VAR_VALOR} booleano — esperado integer-centavos")
    if isinstance(value, float):
        raise ReembolsoCalculoIndisponivelError(
            f"{_DMN_VAR_VALOR} float ({value!r}) — dinheiro e centavos INTEIROS (ADR-0018; a DMN "
            'declara typeRef="integer"); nenhum arredondamento e aplicado'
        )
    if not isinstance(value, int):
        raise ReembolsoCalculoIndisponivelError(
            f"{_DMN_VAR_VALOR} tipo invalido ({type(value).__name__}) — esperado integer-centavos"
        )
    if value < 0:
        raise ReembolsoCalculoIndisponivelError(
            f"{_DMN_VAR_VALOR} negativo ({value}) — nao e um valor de tabela de referencia"
        )
    return value


def _require_dmn_multiplo(value: Any) -> float:
    """`multiplo_tabela_aplicado` as a finite non-negative float, or FAIL CLOSED."""
    if value is None:
        raise ReembolsoCalculoIndisponivelError(f"{_DMN_VAR_MULTIPLO} ausente no resultado da DMN")
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ReembolsoCalculoIndisponivelError(
            f"{_DMN_VAR_MULTIPLO} tipo invalido ({type(value).__name__}) — esperado double"
        )
    multiplo = float(value)
    if not math.isfinite(multiplo) or multiplo < 0:
        raise ReembolsoCalculoIndisponivelError(f"{_DMN_VAR_MULTIPLO} invalido ({value!r})")
    return multiplo


def _require_dmn_fonte(value: Any) -> str:
    """`fonte_tabela` as a non-blank string, or FAIL CLOSED."""
    if not isinstance(value, str) or not value.strip():
        raise ReembolsoCalculoIndisponivelError(
            f"{_DMN_VAR_FONTE} ausente/vazio — um valor sem procedencia registrada nao entra na "
            "trilha de auditoria (ADR-0007)"
        )
    return value


# ---------------------------------------------------------------------------
# Dict-boundary entry functions (T1.2/ADR-0026 §2b) — one per external-task
# topic. Explicit field selection -> typed dataclass -> the UNCHANGED typed
# function above -> dataclasses.asdict (or pass through when already a flat
# dict). The entry functions are pure marshalling: they hold no guard and derive
# no fact of their own.
#
# HISTORY NOTE (kept honest): the T1.2 build wrapped the typed functions
# byte-identically. That stopped being literally true of `calculate_value` when
# the M-2 fix rewrote it for audit-trail honesty, and `calculate_amount_entry`
# is now the ONE exception to "pure marshalling, no guard": GAP F-1 gave it the
# `_require_calculo_dmn` fail-closed resolution of `BRT_Calculo`'s DMN outputs,
# because that guard belongs exactly at the untyped engine dict boundary (the
# same place `issue_payment_entry` leaves its money guard to `process_payment` —
# here the resolution IS the marshalling). `_lookup_tabela_referencia` (ex-
# `_compute_table_value`) and its Python amount table are DELETED.
# The T1.9 invariant these functions were called out for is UNCHANGED and still
# holds: `dentro_teto_l2` is originated ONLY by
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

    GAP F-1: the reimbursement amount is the DMN's. This entry resolves `BRT_Calculo`'s outputs
    from the untyped engine dict with `_require_calculo_dmn` — the one place that decides what
    counts as a delivered decision — and hands them to `calculate_value`, which relays them and
    computes only the two comparison facts. It derives NO amount and holds NO fallback: an absent
    or malformed DMN result raises `ReembolsoCalculoIndisponivelError` here (ADR-0026 §2b: "a
    missing/invalid required field raises the module's own *Invalido*Error") and the money path
    stops with an engine incident rather than a guessed value.

    T1.9 (§2.3): `calculate_value` COMPUTES `dentro_teto_l2` via the CeilingResolver (its
    `resolver` kwarg defaults to the real one) and never reads the inbound flag — this entry
    function only marshals, it never derives the ceiling itself.
    """
    del kafka  # unused — calculate_value emits no domain event
    input_data = ReembolsoInput(**pick_fields(variables, ReembolsoInput))
    calculo = _require_calculo_dmn(variables)
    result = calculate_value(input_data, calculo)
    return dataclasses.asdict(result)


def request_documents_entry(
    variables: dict[str, Any], *, kafka: KafkaPublisher | None = None
) -> dict[str, Any]:
    """Dict-boundary entry for `operadora.reembolso.request_documents` -> `request_documents`.

    GAP-REEMBOLSO-8 / FAB-NOTIFIED-TRIO: o retorno passou a ser `{}` (o antigo `notified=True` era
    fato fabricado — ver a docstring de `request_documents`). Nenhuma outra mudanca de
    comportamento; o `event_topic_pended` que a docstring antiga citava nao existe mais neste
    BPMN e o evento `agents.events.reembolso.pended` e publicado por `ST_PublishReembolsoPended`.
    """
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
    """Dict-boundary entry for `operadora.reembolso.notify_sla_risk` -> `notify_sla_risk`.

    FAB-SLA-RISK-NOTIFIED-SLICE4: o retorno passou a ser `{}` (o antigo `sla_risk_notified=True`
    era fato fabricado — ver a docstring de `notify_sla_risk`). As leituras de entrada seguem
    identicas; so o payload de saida mudou, e ele tinha zero consumidores.
    """
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

"""Worker: pagto (SP-OP-PAGTO-001).

Pagamentos de Alcada — value-driven candidate groups.
Guard: ERR_PAYMENT_RELEASE_NOT_HUMAN + tier-match (L1, ADR-0018).
Clones AUTH auto-approval for low-value (dentro_teto_l2).
"""

from __future__ import annotations

import functools
from typing import TYPE_CHECKING, Any

import structlog

from maezo.tools.workers.base import FunctionWorker, non_blank
from maezo.tools.workers.ceilings import CeilingResolver
from maezo.tools.workers.dmn_transport import DmnTransport, evaluate_sync, first_row, require_dmn

if TYPE_CHECKING:
    from collections.abc import Mapping

    from maezo.a2a import DelegationDispatcher
    from maezo.tools.workers.harness import (
        ExternalTask,
        KafkaPublisher,
        TaskHandler,
        WorkerHarness,
    )

logger = structlog.get_logger(__name__)

# Governance ceiling for the PAGTO auto-release band (design T1.9 §1.4, sibling of B3). The
# teto VALUE lives in the autonomy matrix (`high_value_payment.threshold_brl`,
# L0-core.yaml:21), resolved via the SAME loader the PEP uses — never a hard-coded literal.
_CEILING_ACTION = "high_value_payment"
_CEILING_PARAM = "threshold_brl"

# ---------------------------------------------------------------
# Error codes
# ---------------------------------------------------------------

ERR_PAYMENT_RELEASE_NOT_HUMAN = "ERR_PAYMENT_RELEASE_NOT_HUMAN"
ERR_PAGTO_ORDEM_INVALIDA = "ERR_PAGTO_ORDEM_INVALIDA"
ERR_PAYMENT_REFUSAL_NOT_HUMAN = "ERR_PAYMENT_REFUSAL_NOT_HUMAN"

# Decision values (UT_AprovacaoAlcada / UT_CoordenacaoAlcada — `decisao_pagamento`)
DECISAO_APROVAR = "APROVAR"
DECISAO_RECUSAR = "RECUSAR"
DECISAO_CANCELAR = "CANCELAR"

# Decision value (UT_AnaliseAdmissibilidade — `decisao_admissibilidade`, a DIFFERENT process
# variable than `decisao_pagamento`; GW_ResolucaoAdmissibilidade's own conservative default).
DECISAO_ADMISSIBILIDADE_DEVOLVER = "DEVOLVER"

# Tier constants
# Tier required per faixa_valor: higher tiers require higher approval levels
_TIER_MINIMO: dict[str, int] = {
    "DENTRO_TETO_L2": 0,  # auto (no human)
    "ALCADA_L1": 1,
    "ALCADA_L2": 2,
    "ALCADA_L3": 3,
    "ANALISE_HUMANA": 4,  # comite
}


# ---------------------------------------------------------------
# validate_pagto — validate payment data (FACT, never release)
# ---------------------------------------------------------------


def validate_pagto(variables: dict[str, Any]) -> dict[str, Any]:
    """Validate payment order data — FACT only, NEVER releases.

    Checks: credor/instrumento/lastro/duplicidade.
    """
    ordem_id = variables.get("ordem_pagamento_id", "")
    dados_validos = bool(ordem_id)
    lastro = variables.get("lastro_confirmado", False)
    duplicidade = variables.get("duplicidade_suspeita", False)

    if not dados_validos:
        logger.error("pagto_ordem_invalida", ordem_id=ordem_id)
        raise PagtoError(ERR_PAGTO_ORDEM_INVALIDA, "ordem_pagamento_id ausente/invalido")

    logger.info(
        "pagto_validate",
        ordem_id=ordem_id,
        dados_validos=dados_validos,
        lastro=lastro,
        duplicidade=duplicidade,
    )

    return {
        "dados_pagamento_validos": dados_validos,
        "lastro_confirmado": lastro,
        "duplicidade_suspeita": duplicidade,
    }


# ---------------------------------------------------------------
# assess_admissibility — classify and route (NEVER release)
# ---------------------------------------------------------------


def assess_admissibility(variables: dict[str, Any], *, dmn: DmnTransport | None = None) -> dict[str, Any]:
    """Classify payment admissibility — NEVER releases/authorizes.

    Evaluates the deployed `pagto_admissibility` decision table (ADR-0028/T1.5) — replaces the
    hand-forked if/elif ladder that used to live here. golden-parity divergence found + DMN
    wins (documented, not patched — ADR-0028 §7): the DMN's FIRST-hit-policy rule ORDER checks
    `duplicidade_suspeita` before `dados_pagamento_validos` (the old Python checked
    `dados_pagamento_validos` first) — e.g. `dados_pagamento_validos=False` AND
    `duplicidade_suspeita=True` now yields `ANALISE_HUMANA` (was `PENDENTE_DADOS`). Both
    outcomes are conservative/non-releasing; live-verified against the compose engine before
    cutover (T1.5 PR body / evidence ledger).
    """
    dados_validos = variables.get("dados_pagamento_validos", False)
    lastro = variables.get("lastro_confirmado", False)
    duplicidade = variables.get("duplicidade_suspeita", False)

    rows, version = evaluate_sync(
        require_dmn(dmn, "operadora.pagto.assess_admissibility"),
        "pagto_admissibility",
        {
            "dados_pagamento_validos": bool(dados_validos),
            "lastro_confirmado": bool(lastro),
            "duplicidade_suspeita": bool(duplicidade),
        },
    )
    row = first_row(rows, "pagto_admissibility", variables)
    roteamento = str(row.get("roteamento", ""))
    motivo = str(row.get("motivo", ""))

    logger.info(
        "pagto_assess_admissibility",
        roteamento=roteamento,
        dmn_decision_version=version.version,
    )

    return {
        "roteamento": roteamento,
        "motivo": motivo,
    }


# ---------------------------------------------------------------
# route_aprovacao — value-driven tier routing via DMN pagto_alcada
# ---------------------------------------------------------------


def route_aprovacao(
    variables: dict[str, Any],
    resolver: CeilingResolver | None = None,
    *,
    dmn: DmnTransport | None = None,
) -> dict[str, Any]:
    """Route payment to correct approval tier based on value.

    Evaluates the deployed `pagto_alcada` decision table (ADR-0028/T1.5) — the flagship
    money-path cutover the ADR names as its forcing argument (comparison/range operators the
    old local XML evaluator could never handle). This is the ONLY process with value-driven
    ``camunda:candidateGroups``.

    The DENTRO_TETO_L2 auto-release band is still COMPUTED from the tenant governance ceiling
    (``high_value_payment.threshold_brl``) via the CeilingResolver — the inbound
    ``dentro_teto_l2`` is NEVER read (design T1.9 §1.4, unchanged, `dmn=` does not touch this).
    The resolver's boolean is then fed to the DMN as `dentro_teto_l2` — the engine, not this
    function, decides the resulting `faixa_valor`/`grupo_aprovador`/`tier_minimo`.

    golden-parity divergence found + DMN wins (documented, not patched — ADR-0028 §7): the
    deployed table's `DENTRO_TETO_L2` row ALSO requires `valor_pagamento_cents <= 10_000_000`
    (hardcoded ~R$100k) in addition to `dentro_teto_l2=true` — the old Python trusted the
    resolver's boolean alone. Today `high_value_payment.threshold_brl` in `L0-core.yaml` is
    ALSO R$100,000 (unlike the D-07 `max_value_brl=0` gap on `authorization_approval`/
    `reembolso_auto_approval`), so the two happen to agree — but they are NOT structurally
    coupled: a future tenant overlay raising the resolver's ceiling above R$100k would silently
    diverge from the DMN's independently-hardcoded gate. Live-verified
    (`valor_pagamento_cents=15_000_000, dentro_teto_l2=True` -> DMN returns `ALCADA_L1`, not
    `DENTRO_TETO_L2`) before this cutover; flagged for finance sign-off (the DMN's own
    description already requires it).
    """
    resolver = resolver if resolver is not None else CeilingResolver()

    valor_cents = variables.get("valor_pagamento_cents", 0)
    # COMPUTE the auto-release fact from policy: value within `high_value_payment.threshold_brl`.
    # A config problem / unloadable matrix => False => routes to a human alcada band.
    dentro_teto = resolver.within_l2_ceiling(
        tenant=variables.get("tenant_id", ""),
        action=_CEILING_ACTION,
        param=_CEILING_PARAM,
        value_cents=valor_cents,
    )

    rows, version = evaluate_sync(
        require_dmn(dmn, "operadora.pagto.calculate_facts"),
        "pagto_alcada",
        {
            "valor_pagamento_cents": int(valor_cents),
            "dentro_teto_l2": dentro_teto,
            "tipo_pagamento": variables.get("tipo_pagamento", ""),
        },
    )
    row = first_row(rows, "pagto_alcada", variables)
    faixa = str(row.get("faixa_valor", ""))
    grupo = str(row.get("grupo_aprovador", ""))

    logger.info(
        "pagto_route_aprovacao",
        valor_cents=valor_cents,
        faixa_valor=faixa,
        grupo_aprovador=grupo,
        dmn_decision_version=version.version,
    )

    return {
        "faixa_valor": faixa,
        "grupo_aprovador": grupo,
        # Additive (GAP-PAGTO-3, ADR-0028 §1 pagto_alcada description): the tier_minimo the
        # faixa maps to now lives in the DMN, not a second hand-coded dict here. Surfaced for
        # any downstream consumer; `release_high_value_payment`'s OWN tier-match guard is
        # untouched (out of the ADR-0028 migration table — a safety GUARD, not a routing
        # decision — same non-touch boundary as the ceiling resolver, T1.9).
        "tier_minimo": row.get("tier_minimo"),
        # Write the COMPUTED ceiling fact back (item-9 bucket-3 Class-C — module FINDING 1 of
        # test_sp_op_pagto_001, v2 regression): `BRT_AlcadaRouting` is a NATIVE
        # businessRuleTask (camunda:decisionRef="pagto_alcada", no external topic) that reads
        # whatever `dentro_teto_l2` PROCESS VARIABLE is already set — without this write-back
        # it evaluated the RAW start seed, so the CeilingResolver's fail-closed computation
        # never reached the live routing decision (a within-ceiling payment seeded
        # dentro_teto_l2=False fell to the ANALISE_HUMANA catch-all instead of auto-release).
        # Mirrors auth.AnalyzeRequestWorker's own `dentro_teto_l2` write-back and the donor's
        # calculate_facts design intent (design T1.9 §1.4/§2.4): the engine receives the
        # policy-computed fact, never the inbound boolean.
        "dentro_teto_l2": dentro_teto,
    }


# ---------------------------------------------------------------
# execute_pagto — low-value release (L2, auto — no adverse)
# ---------------------------------------------------------------


def execute_pagto(variables: dict[str, Any]) -> dict[str, Any]:
    """Execute low-value payment (dentro_teto_l2 — L2 auto path).

    This is NOT adverse — below-threshold payments are auto-released.
    Modeled after auth_auto_approval.
    """
    faixa = variables.get("faixa_valor", "")

    if faixa != "DENTRO_TETO_L2":
        logger.warning(
            "pagto_execute_faixa_incompativel",
            faixa=faixa,
        )
        return {"pagamento_executado": False}

    logger.info(
        "pagto_execute_auto",
        ordem_id=variables.get("ordem_pagamento_id"),
        valor_cents=variables.get("valor_pagamento_cents"),
    )

    return {
        "pagamento_executado": True,
        "tipo_liberacao": "auto_L2",
    }


# ---------------------------------------------------------------
# release_high_value_payment — GATED L1 adverse effect
# ---------------------------------------------------------------


def _norm_str(value: Any) -> str:
    """Normalize an engine variable to a stripped string for guard checks — FAIL-CLOSED.

    R1 live-validation finding (t2.5-p2b-round2, wave2a, 3 of 12 bypass vectors): the guard's
    original bare `if not aprovador_id` / `if not justificativa` checks let WHITESPACE-ONLY
    accountability fields REGISTER a refusal — defeating ADR-0007 (a refusal recorded with a
    non-identifying approver) — whereas the claimed mirror `recurso.register_desistencia`
    `.strip()`s every human field. This helper closes that class:
    - a `str` normalizes to `value.strip()` — whitespace-only ("   ", "\\t", "\\n", ...)
      becomes "" and is treated EXACTLY like an absent field (refusal, never registration);
    - a NON-string (None, int, bool, list, dict — engine variables arrive untyped) normalizes
      to "" (fail-closed refusal), never a truthy pass-through (`if not 123` was falsy — a
      non-string aprovador_id would previously have silently PASSED the accountability check)
      and never an AttributeError incident from calling `.strip()` on a non-string.

    Shared by `release_high_value_payment` (t3.1-guard-input-hardening — closes the pre-existing
    bare-truthiness gap flagged in the #133 audit note, pagto.py:283,285) and
    `register_payment_refusal` (original t2.5-p2b-round2 fix, below).
    """
    if isinstance(value, str):
        return value.strip()
    return ""


def release_high_value_payment(variables: dict[str, Any]) -> dict[str, Any]:
    """Release high-value payment — GATED with tier-match.

    GUARDED: ERR_PAYMENT_RELEASE_NOT_HUMAN.
    Requires:
      - decisao_pagamento == APROVAR from human
      - aprovador_id + justificativa + valor_aprovado_cents
      - tier-match: aprovador_tier >= required for faixa_valor

    NORMALIZATION (t3.1-guard-input-hardening, closing the bare-truthiness gap noted in the
    #133 audit — pagto.py:283,285 pre-fix): `decisao_pagamento`, `aprovador_id` and
    `justificativa_aprovacao` are normalized via `_norm_str` (strip; non-string -> "") BEFORE any
    guard check, mirroring `register_payment_refusal`'s own fix below. Consequences, all
    fail-closed:
    - whitespace-only `aprovador_id`/`justificativa_aprovacao` REFUSES exactly like an absent
      field (was a silent pass-through: `if not aprovador_id` treats "   " as truthy);
    - a whitespace-PADDED but otherwise exact `decisao_pagamento` literal ("APROVAR ") normalizes
      to the literal and still passes Guard 1 (still subject to tier-match and the
      accountability-field checks) — case variants/substrings ("aprovar", "APROVAR_X") still
      refuse (exact `!=` match, no folding);
    - a non-string in ANY of these fields normalizes to "" (refusal), never a truthy
      pass-through and never an AttributeError incident.
    """
    decisao = _norm_str(variables.get("decisao_pagamento", ""))
    aprovador_id = _norm_str(variables.get("aprovador_id", ""))
    aprovador_tier = variables.get("aprovador_tier", 0)
    justificativa = _norm_str(variables.get("justificativa_aprovacao", ""))
    valor_aprovado = variables.get("valor_aprovado_cents", 0)
    faixa = variables.get("faixa_valor", "")

    if not isinstance(aprovador_tier, int):
        try:
            aprovador_tier = int(aprovador_tier)
        except (ValueError, TypeError):
            aprovador_tier = 0

    errors: list[str] = []

    # Guard 1: human decision
    if decisao != DECISAO_APROVAR:
        errors.append(f"decisao_pagamento != {DECISAO_APROVAR} (got: {decisao!r})")
    if not aprovador_id:
        errors.append("aprovador_id ausente (ADR-0007)")
    if not justificativa:
        errors.append("justificativa_aprovacao ausente")

    # Guard 2: tier-match
    tier_requerido = _TIER_MINIMO.get(faixa, 4)
    if aprovador_tier < tier_requerido:
        errors.append(
            f"tier-match falhou: aprovador_tier={aprovador_tier} "
            f"< requerido={tier_requerido} para faixa '{faixa}'"
        )

    if errors:
        logger.error(
            "pagto_release_guard_rejected",
            errors=errors,
            ordem_id=variables.get("ordem_pagamento_id"),
        )
        raise PagtoError(ERR_PAYMENT_RELEASE_NOT_HUMAN, "; ".join(errors))

    logger.info(
        "pagto_high_value_released",
        ordem_id=variables.get("ordem_pagamento_id"),
        aprovador_id=aprovador_id,
        aprovador_tier=aprovador_tier,
        valor_aprovado_cents=valor_aprovado,
    )

    return {
        "pagamento_liberado": True,
        "tipo_liberacao": "humano_alcada",
    }


# ---------------------------------------------------------------
# notify_sla_risk — informational SLA alert (non-interruptive timer)
# ---------------------------------------------------------------


def notify_sla_risk(variables: dict[str, Any]) -> dict[str, Any]:
    """Alert coordenacao-financeira of SLA risk (non-interruptive timer BT_AlertaSlaPagto).

    External task: `operadora.pagto.notify_sla_risk` (`ST_NotificarRiscoSla`,
    SP-OP-PAGTO-001_Pagamentos_Alcada.bpmn:300-304 — task name "Notificar risco de SLA
    (coordenacao-financeira)"). Fires at `${sla.sla_alerta}` (60-70% of `pagto_sla`'s
    `sla_alerta`, per the contract, DRAFT/verify -- docs/processes/contracts/
    SP-OP-PAGTO-001.md:134,199) on the non-interruptive boundary event
    (cancelActivity="false") attached to `UT_AprovacaoAlcada`.

    Informational only (mirrors `inadimplencia.notify_sla_risk`/`cancel.notify_sla_risk`/
    `fraude.notify_sla_risk`/`adequacao.notify_sla_risk`): UT_AprovacaoAlcada stays open, no
    decision is made or altered, NO adverse outcome (payment release, refusal, or otherwise) is
    ever produced by this alert. The payment release NEVER arises from a timer -- only the human
    decision at UT_AprovacaoAlcada/UT_CoordenacaoAlcada (`release_high_value_payment`'s own
    tier-match guard, unchanged) does.
    """
    ordem_id = variables.get("ordem_pagamento_id", "")
    faixa = variables.get("faixa_valor", "")

    logger.info(
        "pagto_notify_sla_risk",
        ordem_id=ordem_id,
        faixa_valor=faixa,
        grupo_alertado="coordenacao-financeira",
    )

    return {
        "sla_risk_notified": True,
        "grupo_alertado": "coordenacao-financeira",
        "ordem_pagamento_id": ordem_id,
    }


# ---------------------------------------------------------------
# register_payment_refusal — GATED, dual human channel (NOT adverse-release; refuse-if-no-human)
# ---------------------------------------------------------------


def register_payment_refusal(variables: dict[str, Any]) -> dict[str, Any]:
    """Register payment refusal/return for review — GUARDED, dual human channel.

    External task: `operadora.pagto.register_payment_refusal` (`ST_RegisterPaymentRefusal`,
    SP-OP-PAGTO-001_Pagamentos_Alcada.bpmn:408-414). Task documentation (line 410): "Registra a
    recusa/devolucao do pagamento para revisao quando decisao_pagamento=RECUSAR (decisao de
    alcada) OU quando a admissibilidade e devolvida por humano (decisao_admissibilidade=DEVOLVER)
    -- ambos registram justificativa_recusa + responsavel humano. NAO e glosa (glosa nasce em
    CONTAS-001); NAO libera." NOT adverse in the release sense (it never authorizes payment) but
    IS adverse-SHAPED for the counterparty (a formal refusal/return-for-review record) and is
    channel-gated identically to a genuine adverse effect -- so this worker GUARDS it exactly
    like `release_high_value_payment` guards a release, refusing to register anything absent a
    genuine human decision (mirrors `recurso.register_desistencia`'s dual-channel
    refuse-if-no-human pattern, `DesistenciaNotHumanError`).

    GUARDED, dual channel -- ONE of TWO human decisions must be attempted:
    - alcada channel (`Flow_GWDec_Recusar`, GW_DecisaoPagamento's OWN conservative default --
      BPMN line 375 -- from `UT_AprovacaoAlcada`/`UT_CoordenacaoAlcada`):
      `decisao_pagamento == RECUSAR` (contract docs/processes/contracts/SP-OP-PAGTO-001.md:92,95:
      "justificativa_recusa Obrigatoria se RECUSAR").
    - admissibilidade channel (`Flow_GWResol_Devolver`, `GW_ResolucaoAdmissibilidade`'s OWN
      conservative default -- BPMN line 173 -- from `UT_AnaliseAdmissibilidade`):
      `decisao_admissibilidade == DEVOLVER` (BPMN lines 163-164: "DEVOLVER (default
      conservador) exige justificativa_recusa + aprovador_id").
    Both channels ALSO require `aprovador_id` (ST_RegisterPaymentRefusal's own task doc, line
    410: "Carrega aprovador_id na trilha de auditoria" -- ADR-0007) and `justificativa_recusa`.
    CANCELAR is a SEPARATE, direct-publish terminal (`ST_PublishCancelado` -- BPMN lines 431-439,
    contract line 40) and is DELIBERATELY never routed through this worker.

    Refuses with `ERR_PAYMENT_REFUSAL_NOT_HUMAN` if NEITHER channel's decision field matches
    (e.g. both gateways' own conservative DEFAULTS can route here with the decision variable
    unset/empty on an omitted UT completion -- GW_DecisaoPagamento defaults to
    `Flow_GWDec_Recusar`, GW_ResolucaoAdmissibilidade defaults to `Flow_GWResol_Devolver` -- the
    engine's fail-safe routing does NOT by itself constitute a human decision; this worker still
    requires the explicit decision literal plus its required fields) -- never silently registers
    a refusal without a genuine human decision behind it.

    NORMALIZATION (R1 live-validation fix, t2.5-p2b-round2 wave2a finding): ALL decision and
    human-accountability fields are normalized via `_norm_str` (strip; non-string -> "") BEFORE
    any guard check -- mirroring `recurso.register_desistencia`'s `.strip()` treatment of every
    human field. Consequences, all fail-closed:
    - whitespace-only `aprovador_id`/`justificativa_recusa` (space/tab/newline) REFUSES exactly
      like an absent field (was the 3-of-12-vector bypass: V4/V5/V12 previously REGISTERED);
    - whitespace-only decision fields normalize to "" -> neither-channel refusal;
    - a whitespace-PADDED but otherwise exact decision literal ("RECUSAR ") normalizes to the
      literal and selects its channel (still subject to the accountability-field checks) --
      case variants/substrings ("recusar", "RECUSAR_X") still refuse (exact match, no folding);
    - a non-string in ANY of these fields normalizes to "" (refusal), never a truthy
      pass-through and never an AttributeError incident.
    """
    decisao_pagamento = _norm_str(variables.get("decisao_pagamento", ""))
    decisao_admissibilidade = _norm_str(variables.get("decisao_admissibilidade", ""))
    aprovador_id = _norm_str(variables.get("aprovador_id", ""))
    justificativa = _norm_str(variables.get("justificativa_recusa", ""))
    ordem_id = variables.get("ordem_pagamento_id", "")

    is_alcada = decisao_pagamento == DECISAO_RECUSAR
    is_admissibilidade = decisao_admissibilidade == DECISAO_ADMISSIBILIDADE_DEVOLVER

    if not is_alcada and not is_admissibilidade:
        logger.error(
            "pagto_refusal_guard_rejected_no_channel",
            decisao_pagamento=decisao_pagamento,
            decisao_admissibilidade=decisao_admissibilidade,
            ordem_id=ordem_id,
        )
        raise PagtoError(
            ERR_PAYMENT_REFUSAL_NOT_HUMAN,
            f"decisao_pagamento != {DECISAO_RECUSAR} (canal alcada) e decisao_admissibilidade != "
            f"{DECISAO_ADMISSIBILIDADE_DEVOLVER} (canal admissibilidade) -- nenhum canal humano",
        )

    channel = "alcada" if is_alcada else "admissibilidade"
    errors: list[str] = []
    if not aprovador_id:
        errors.append("aprovador_id ausente (ADR-0007)")
    if not justificativa:
        errors.append("justificativa_recusa ausente")

    if errors:
        logger.error(
            "pagto_refusal_guard_rejected",
            errors=errors,
            channel=channel,
            ordem_id=ordem_id,
        )
        raise PagtoError(ERR_PAYMENT_REFUSAL_NOT_HUMAN, f"canal {channel}: " + "; ".join(errors))

    logger.info(
        "pagto_refusal_registered",
        ordem_id=ordem_id,
        aprovador_id=aprovador_id,
        channel=channel,
    )

    return {
        "recusa_registrada": True,
        "canal": channel,
    }


# ---------------------------------------------------------------
# publish_completed — publishing completion event
# ---------------------------------------------------------------


def publish_completed(variables: dict[str, Any]) -> dict[str, Any]:
    """Publish domain event for payment completion."""
    desfecho = "liberado_automatico"
    if variables.get("faixa_valor", "") != "DENTRO_TETO_L2":
        decisao = variables.get("decisao_pagamento", "")
        if decisao == DECISAO_APROVAR:
            desfecho = "liberado_humano"
        elif decisao == DECISAO_RECUSAR:
            desfecho = "recusado_humano"
        elif decisao == DECISAO_CANCELAR:
            desfecho = "cancelado"

    logger.info(
        "pagto_publish_completed",
        ordem_id=variables.get("ordem_pagamento_id"),
        desfecho=desfecho,
    )

    return {
        "evento_publicado": True,
        "desfecho": desfecho,
    }


# ---------------------------------------------------------------
# prepare_approval_dossier — REAL Andre A2A delegation (raw async handler; DL-0033 closed, DL-0037)
# ---------------------------------------------------------------


def make_prepare_approval_dossier_handler(dispatcher: DelegationDispatcher | None) -> TaskHandler:
    """Create the handler for `operadora.pagto.prepare_approval_dossier` — the REAL Andre A2A
    delegation (the LAST worker-originated dossier edge, closing the #181 gap that adequacao/cred
    had already closed in #178).

    Serves `ST_PrepareApprovalDossier` (the only path from `BRT_PagtoSla` to `UT_AprovacaoAlcada`;
    also reached on the GAP-PAGTO-5 `seguir_analise` re-entry). Replaces the DL-0033 local echo/log
    stub with `delegate_pagto_dossier` -> `DelegationDispatcher.delegate` -> Andre's REAL graph.
    The SHARED task_type `analytics.population` is REUSED (no new type minted); Andre's DEFAULT
    `pagto_dossier` flow is selected by the `pagto-worker` origin alone — the payment-approval risk
    dossier his graph documents as convoked here (`agents/andre/delegation.py`,
    `agents/andre/graph.py`).

    RAW ASYNC HANDLER (DL-0034 precedent, mirrors adequacao/cred): a `FunctionWorker.execute(dict)`
    boundary is sync while `dispatcher.delegate` is async — the raw `harness.register()` form runs
    this handler on the harness's own loop, where the dispatcher's pooled audit sink lives.
    Populates `_handlers` but NOT the `WorkerRegistry` (see `test_bootstrap_registration.py`'s
    `raw_handler_topics`).

    FAIL-NEUTRAL-WITH-DISCLOSED-GAP (DL-0037): assembling a dossier INSTRUCTS the human approver
    (UT_AprovacaoAlcada/UT_CoordenacaoAlcada) — it "instrui, nao decide" (SP-OP-PAGTO-001); the
    payment release is born SOLELY in the human User Task with `decisao_pagamento=APROVAR` +
    tier-match (`release_high_value_payment`, unchanged, this handler NEVER invokes it). A missing
    dispatcher (degraded runtime: no signing key / no DATABASE_URL), missing business identifiers,
    a structured rejection or ANY delegation failure therefore returns
    `{"dossier_prepared": False, "dossier_gap": <bounded reason token>}` + a LOUD log and COMPLETES
    the task — the human UT MUST still open; this handler NEVER raises. The gap token is a bounded
    class token (engine-variable hygiene) — raw error text stays in the log.

    NO-DUPLICATE-INSTANCE (GK-dossier finding 1, two anchors): this handler runs from INSIDE an
    already-running SP-OP-PAGTO-001 instance, so the delegation must NEVER open a second one
    (a second instance = a second `UT_AprovacaoAlcada` approval/release path). (a) `task.business_key`
    — the ENGINE's authoritative key — is threaded verbatim into the delegation, so Andre's
    idempotent start consults the key the LIVE instance carries instead of its own ordem-first
    derivation (which diverges for the contract's CONTAS variant
    `PAGTO-{tenant}-{numero_lote_tiss}-{prestador_id}`); (b) his `start_process` structurally
    no-ops for this worker's `pagto-worker` origin regardless of the key.

    Idempotency note (disclosed): the delegation `task_id` IS that business key — the
    `seguir_analise` re-entry for the SAME case receives the idempotent REPLAY of the same
    dossier, never a duplicated release.
    """

    async def handler(task: ExternalTask) -> Mapping[str, Any]:
        v = task.variables
        tenant_id = str(v.get("tenant_id", "") or "")
        ordem_pagamento_id = str(v.get("ordem_pagamento_id", "") or "").strip()
        numero_lote_tiss = str(v.get("numero_lote_tiss", "") or "").strip()
        prestador_id = str(v.get("prestador_id", "") or "").strip()

        if dispatcher is None:
            # Degraded runtime (DL-0037): dispatcher absent at composition (no signing key /
            # no DATABASE_URL — worker_runtime readiness reports dossier_delegation_ready=false).
            # The UT still opens; the approver sees the disclosed gap instead of a dossier.
            logger.warning(
                "pagto_prepare_approval_dossier_dispatcher_unavailable",
                tenant_id=tenant_id,
                ordem_pagamento_id=ordem_pagamento_id,
                business_key=task.business_key,
            )
            return {"dossier_prepared": False, "dossier_gap": "dispatcher_unavailable"}

        # A well-formed PAGTO business key needs tenant + (ordem OR lote+prestador) — the SAME
        # identity `andre.graph._business_key` requires (EB-4 R1 `non_blank` discipline). Without
        # it, never delegate with a degenerate task_id; the UT still opens with the gap disclosed.
        has_key = non_blank(ordem_pagamento_id) or (non_blank(numero_lote_tiss) and non_blank(prestador_id))
        if not (non_blank(tenant_id) and has_key):
            logger.error(
                "pagto_prepare_approval_dossier_missing_business_identifiers",
                tenant_id=tenant_id,
                ordem_pagamento_id=ordem_pagamento_id,
                numero_lote_tiss=numero_lote_tiss,
                prestador_id=prestador_id,
                business_key=task.business_key,
            )
            return {"dossier_prepared": False, "dossier_gap": "missing_business_identifiers"}

        from maezo.agents.andre.delegation import delegate_pagto_dossier

        try:
            result = await delegate_pagto_dossier(
                dispatcher,
                tenant=tenant_id.strip(),
                case_meta=dict(v),
                ordem_pagamento_id=ordem_pagamento_id,
                numero_lote_tiss=numero_lote_tiss,
                prestador_id=prestador_id,
                # The ENGINE's authoritative key of the instance THIS task belongs to (GK-dossier
                # finding 1a). Threading it verbatim keeps Andre anchored on the SAME case: the
                # ordem-first derivation DIVERGES from an instance keyed with the contract's
                # CONTAS variant (`PAGTO-{tenant}-{numero_lote_tiss}-{prestador_id}`, contract
                # §Business key) whenever an ordem is also in scope, and a diverging key would
                # miss the idempotency lookup and open a SECOND SP-OP-PAGTO-001 instance — a
                # duplicated UT_AprovacaoAlcada approval/release path. Blank -> derivation.
                business_key=str(task.business_key or ""),
            )
        except Exception as exc:  # noqa: BLE001 — DL-0037: the UT must open; never raise here.
            logger.error(
                "pagto_prepare_approval_dossier_delegation_failed",
                tenant_id=tenant_id,
                ordem_pagamento_id=ordem_pagamento_id,
                business_key=task.business_key,
                error=str(exc),
            )
            return {"dossier_prepared": False, "dossier_gap": "delegation_failed"}

        if not result.success:
            reason = str(result.rejection_reason or "unknown")
            logger.error(
                "pagto_prepare_approval_dossier_delegation_rejected",
                tenant_id=tenant_id,
                ordem_pagamento_id=ordem_pagamento_id,
                business_key=task.business_key,
                reason=reason,
                detail=result.detail,
            )
            return {"dossier_prepared": False, "dossier_gap": f"delegation_rejected:{reason}"}

        logger.info(
            "pagto_prepare_approval_dossier_delegated",
            tenant_id=tenant_id,
            ordem_pagamento_id=ordem_pagamento_id,
            business_key=task.business_key,
            dossier_ref=result.output_ref,
            idempotent_replay=result.idempotent_replay,
        )
        return {
            "dossier_prepared": True,
            "dossier_ref": result.output_ref or "",
            # UT-FORM SEAM (SME/PO sign-off PENDING): the dossier CONTENT field schema for
            # UT_AprovacaoAlcada/UT_CoordenacaoAlcada is uncontracted — no dossier field appears in
            # the contract's variable table. `dossier_summary` carries Andre's agent-produced
            # bounded summary tokens AS-IS (route/desfecho/motivo/grupo — never the narrative,
            # never a decision, never a price/release); the full dossier is reachable via
            # `dossier_ref`. Do NOT invent/extend this schema here — it is the human-gated
            # injection point.
            "dossier_summary": dict(result.meta),
        }

    return handler


# ---------------------------------------------------------------
# Custom error
# ---------------------------------------------------------------


class PagtoError(Exception):
    """Worker guard error for pagto adverse effects."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(f"{code}: {message}")


# ---------------------------------------------------------------
# Bootstrap — FunctionWorker adapter (T1.2/ADR-0026 Decisao §2a). No function
# body above is touched — `route_aprovacao` COMPUTES `dentro_teto_l2` via the
# CeilingResolver (T1.9 §1.4; the resolver kwarg defaults to the real one when
# the FunctionWorker calls `route_aprovacao(process_vars)`), never from the
# inbound variables — this bootstrap only adds registry/retry/metrics wiring
# on top and never re-originates the ceiling fact.
#
# Topic mapping vs spec/processes/bpmn/SP-OP-PAGTO-001_Pagamentos_Alcada.bpmn
# (excl. shared/out-of-scope `operadora.events.publish`):
#   validate_pagto    -> operadora.pagto.validate_payment_data (exact spec match)
#   route_aprovacao   -> operadora.pagto.calculate_facts (closest spec match: value/tier
#                        classification)
#   execute_pagto     -> operadora.pagto.release_low_value_payment
#     (spec match: DENTRO_TETO_L2 auto/clerical release)
#   release_high_value_payment -> operadora.pagto.release_high_value_payment
#     (exact spec match, GUARDED)
#   notify_sla_risk -> operadora.pagto.notify_sla_risk (spec match, informational —
#     t2.5-p2b-round2 closed this gap)
#   register_payment_refusal -> operadora.pagto.register_payment_refusal (spec match,
#     GUARDED dual-channel refuse-if-no-human — t2.5-p2b-round2 closed this gap)
# assess_admissibility has no distinct spec topic — registered under a
# function-derived topic for registry completeness. publish_completed folds
# into the generic events.publish task per BPMN — function-derived topic.
#   make_prepare_approval_dossier_handler -> operadora.pagto.prepare_approval_dossier (exact spec
#     match; RAW async handler — the REAL Andre A2A delegation (analytics.population, origin-
#     disambiguated to his DEFAULT pagto_dossier flow) DL-0033 deferred, now wired (the LAST
#     dossier edge, closing the #181 gap adequacao/cred closed in #178). NEUTRAL — instructs
#     UT_AprovacaoAlcada, never decides the payment; dispatcher absent/failed -> disclosed-gap
#     marker, the UT still opens (DL-0037).)
# ---------------------------------------------------------------


def register_pagto_workers(
    harness: WorkerHarness,
    kafka: KafkaPublisher | None = None,
    **seams: Any,
) -> None:
    """Register the SP-OP-PAGTO-001 function workers on `harness`.

    `dmn` (ADR-0028 §1 seam) is threaded into `assess_admissibility` (`pagto_admissibility`)
    and `route_aprovacao` (`pagto_alcada`, T1.5 cutover) via `functools.partial`; no other
    function here evaluates a DMN table. `kafka` is accepted but unused — no pagto.py worker
    declares a Kafka dependency, `notify_sla_risk`/`register_payment_refusal` included
    (dict-first, mirror the family's existing idiom).

    `dossier_dispatcher` (dossier-A2A seam, DL-0033 real wiring) is threaded into the
    `prepare_approval_dossier` RAW async handler — a `DelegationDispatcher` assembled by the
    worker-runtime composition root (`build_dossier_delegation_dispatcher`). Absent (`None`, the
    topic-probe default and the degraded-runtime posture) the topic still registers and the handler
    fail-neutrals with a disclosed gap (DL-0037) — the human UT always still opens.
    """
    del kafka  # unused — no pagto.py worker declares a Kafka dependency
    dmn = seams.get("dmn")
    dossier_dispatcher: DelegationDispatcher | None = seams.get("dossier_dispatcher")
    harness.register_worker(FunctionWorker("operadora.pagto.validate_payment_data", validate_pagto))
    harness.register_worker(
        FunctionWorker(
            "operadora.pagto.assess_admissibility", functools.partial(assess_admissibility, dmn=dmn)
        )
    )
    harness.register_worker(
        FunctionWorker("operadora.pagto.calculate_facts", functools.partial(route_aprovacao, dmn=dmn))
    )
    harness.register_worker(FunctionWorker("operadora.pagto.release_low_value_payment", execute_pagto))
    harness.register_worker(
        FunctionWorker("operadora.pagto.release_high_value_payment", release_high_value_payment)
    )
    harness.register_worker(FunctionWorker("operadora.pagto.notify_sla_risk", notify_sla_risk))
    harness.register_worker(
        FunctionWorker("operadora.pagto.register_payment_refusal", register_payment_refusal)
    )
    harness.register_worker(FunctionWorker("operadora.pagto.publish_completed", publish_completed))
    # RAW handler (NOT register_worker) — needs the async dispatcher seam (module topic-map note).
    harness.register(
        "operadora.pagto.prepare_approval_dossier",
        make_prepare_approval_dossier_handler(dossier_dispatcher),
    )

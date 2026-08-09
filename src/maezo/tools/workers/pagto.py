"""Worker: pagto (SP-OP-PAGTO-001).

Pagamentos de Alcada — value-driven candidate groups.
Guard: ERR_PAYMENT_RELEASE_NOT_HUMAN + tier-match (L1, ADR-0018).
Clones AUTH auto-approval for low-value (dentro_teto_l2).
"""

from __future__ import annotations

import asyncio
import functools
from typing import TYPE_CHECKING, Any

import structlog

from maezo.tools.workers.base import FunctionWorker, non_blank
from maezo.tools.workers.ceilings import CeilingResolver
from maezo.tools.workers.dmn_transport import DmnTransport, evaluate_sync, first_row, require_dmn
from maezo.tools.workers.phi_vars import redact_error_message

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

# The `valor_pagamento_cents` fed to `pagto_alcada` when the inbound amount is NOT a trustworthy
# money value (B-1). NOT a fabricated payment amount: with `dentro_teto_l2=False` forced alongside
# it, the DMN's own rows decide the route and 0 matches NONE of the value bands —
# `r_dentro_teto_l2` requires `dentro_teto_l2=true` (pagto_alcada.dmn:65), and the three ALCADA
# ranges all start ABOVE 10000000 (dmn:74,84,94) — so the token lands on `r_catchall`
# (dmn:102-111), whose own description names this exact case: "dentro_teto_l2 inconsistente ou
# inputs nao mapeados -> tier mais alto (comite-financeiro / ANALISE_HUMANA). NUNCA auto-libera."
#
# Route-EQUIVALENCE (pinned by test): every value this worker rejects either cannot be sent to the
# engine at all (str/float/bool/None/list — `long`-typed input) or is an int <= 0, and EVERY int
# <= 0 lands on the SAME `r_catchall` row that 0 does. Normalising to 0 therefore changes no
# routing outcome for any rejected vector — it only makes the input well-typed.
#
# This value is NEVER written back as a process variable: `route_aprovacao` does not echo
# `valor_pagamento_cents` (see the WRITE-BACK ASYMMETRY note on the function), so the engine's own
# `valor_pagamento_cents` — whatever malformed thing it is — stays visible to the human approver.
_VALOR_CENTS_ROTA_CONSERVADORA = 0

#: Bounded, non-PHI rejection tokens for `_valor_pagamento_cents_or_none` (log only — no engine
#: variable is minted for them; see the DISCLOSED note on `route_aprovacao`).
_VALOR_AUSENTE = "ausente"
_VALOR_BOOLEANO = "booleano"
_VALOR_FLOAT = "float"
_VALOR_TIPO_INVALIDO = "tipo_invalido"
_VALOR_NAO_POSITIVO = "nao_positivo"

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


def _valor_pagamento_cents_or_none(valor_cents: Any) -> tuple[int | None, str]:
    """Fail-closed resolution of `valor_pagamento_cents` — integer BRL centavos ONLY (ADR-0018).

    Returns `(centavos, "")` when the amount is trustworthy, or `(None, <bounded token>)` when it
    is not. B-1 (blocker, money, fail-OPEN) — pre-fix this function did not exist and the amount
    was read as `variables.get("valor_pagamento_cents", 0)`, so:

    - an ABSENT amount silently became 0, `within_l2_ceiling(0)` was True under the R$100k teto,
      and `pagto_alcada`'s `r_dentro_teto_l2` (`0 <= 10000000` + `dentro_teto_l2=true`) routed the
      order to DENTRO_TETO_L2 -> `ST_ReleaseLowValue` -> `execute_pagto` EXECUTED a payment for an
      order that never carried an amount. The contract makes the field REQUIRED
      (docs/processes/contracts/SP-OP-PAGTO-001.md:69, "sim") — absent is a defect, not a default;
    - a NEGATIVE amount passed both the ceiling check and the DMN's `<= 10000000` row;
    - a `str` (an engine `String` variable passes through `harness._from_camunda_var` verbatim)
      raised an UNCAUGHT `TypeError` inside the ceiling comparison.

    Mirrors `reembolso._require_valor_pagamento_cents` vector-for-vector — rejected, in order:
    `None`/absent; `bool` (an `int` in Python; would otherwise route on 1 centavo); `float`
    (integral ones like `8000.0` included — money is never rounded, and NaN/+-inf are rejected as
    a subcase since they exist only as floats); any other non-`int` type; and `<= 0` (a zero or
    negative "payment" is not a payment).

    DELIBERATE DIVERGENCE from the reembolso mirror — it RAISES, this RETURNS. `ST_CalculateFacts`
    (spec/processes/bpmn/SP-OP-PAGTO-001_Pagamentos_Alcada.bpmn:115-120) declares NO error
    boundary event (the file's only boundaries are `BE_PagtoOrdemInvalida` on
    `ST_ValidatePaymentData`:105-108 and the two SLA boundaries on `UT_AprovacaoAlcada`:294,310),
    so per ADR-0030 house rules this task must fail NEUTRALLY: the caller routes conservatively
    through the DMN's own catch-all instead of raising. The SAME posture
    `IssueAuthorizationWorker`'s ceiling gate takes at `ST_EmitirAutorizacaoAuto` for the same
    reason.
    """
    if valor_cents is None:
        return None, _VALOR_AUSENTE
    if isinstance(valor_cents, bool):
        return None, _VALOR_BOOLEANO
    if isinstance(valor_cents, float):
        return None, _VALOR_FLOAT
    if not isinstance(valor_cents, int):
        return None, _VALOR_TIPO_INVALIDO
    if valor_cents <= 0:
        return None, _VALOR_NAO_POSITIVO
    return valor_cents, ""


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

    MONEY IS REQUIRED, NOT DEFAULTED (B-1 — blocker, fail-OPEN, closed here). The amount is
    resolved through `_valor_pagamento_cents_or_none` (integer centavos only; see there for the
    rejected vectors and the pre-fix live path from an ABSENT amount to an EXECUTED payment). When
    it cannot be trusted, this function does NOT raise (`ST_CalculateFacts` declares no error
    boundary — ADR-0030) and does NOT invent a route: it forces `dentro_teto_l2=False`, which
    structurally closes the auto-release band (`r_dentro_teto_l2` requires `true` —
    spec/processes/dmn/pagto_alcada.dmn:65), and hands the DMN `_VALOR_CENTS_ROTA_CONSERVADORA`,
    which matches none of the three ALCADA ranges (dmn:74,84,94). The token therefore lands on the
    table's OWN conservative catch-all `r_catchall` (dmn:102-111) — `ANALISE_HUMANA` /
    `comite-financeiro` / `tier_minimo=4`, the highest human band — whose description names this
    exact case ("dentro_teto_l2 inconsistente ou inputs nao mapeados ... NUNCA auto-libera"). From
    there the token continues down `Flow_Alcada_PubRouted` to the ordinary human alcada path
    (BPMN:469); nothing about the model's shape changes.

    DISCLOSED (deliberate, in the WRITE-BACK ASYMMETRY family below): the rejection is recorded in
    the operator log with a bounded non-PHI token, NOT as a new engine variable. Minting one would
    add an undeclared variable to a contracted process (the contract's variable table is
    human-gated) — the engine-visible evidence today is the route itself (`faixa_valor` /
    `grupo_aprovador` / `tier_minimo=4` / `dentro_teto_l2=false`) plus the untouched, still-visible
    `valor_pagamento_cents` the approver at comite-financeiro reads. Closing this is deferred, not
    denied.

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

    WRITE-BACK ASYMMETRY vs the modeled task (GK-w4 finding 6 — DISCLOSED, deliberate, not a
    regression): this function is bound to `operadora.pagto.calculate_facts`, and
    `ST_CalculateFacts`'s BPMN documentation describes a broader echo than what is returned here.
    The model says the task echoes `valor_pagamento_cents` (long/int64) AND the three booleans
    `pagto_admissibility` reads (`dados_pagamento_validos` / `lastro_confirmado` /
    `duplicidade_suspeita`) with fail-closed defaults `false`/`false`/`true` when absent. The
    return below writes back only `dentro_teto_l2` (plus the routing outputs `faixa_valor` /
    `grupo_aprovador` / `tier_minimo`) — the MINIMAL fix that item-9 bucket-3 Class-C needed to
    make `BRT_AlcadaRouting`'s native read of `dentro_teto_l2` see the policy-computed fact
    instead of the raw start seed. Broadening the echo was intentionally left out of that fix's
    blast radius.
    The residual gap is fail-safe in the adverse direction: `BRT_PagtoAdmissibility` runs
    downstream of this task and `pagto_admissibility` is `hitPolicy="FIRST"` with a catch-all row
    (`r_catchall`, `-`/`-`/`-`) returning `ANALISE_HUMANA` ("Admissibilidade ambigua / fato
    ausente ... NUNCA auto-libera"). An unechoed/absent admissibility fact therefore routes to a
    human, never to an automatic release. `valor_pagamento_cents` is likewise only read, never
    re-originated, so not echoing it cannot change its value. Closing the asymmetry (echo the
    three booleans with the modeled defaults, echo the normalized cents) is deferred, not denied.
    """
    resolver = resolver if resolver is not None else CeilingResolver()

    # ONE normalized money fact for BOTH consumers (B-1): pre-fix the resolver received the RAW
    # `valor_cents` while the DMN received `int(valor_cents)` — two different readings of one fact
    # from one variable, and the only place they could disagree was on exactly the malformed
    # inputs that must not route at all.
    raw_valor = variables.get("valor_pagamento_cents")
    valor_cents, motivo_valor = _valor_pagamento_cents_or_none(raw_valor)

    if valor_cents is None:
        # FAIL-CLOSED, fail-NEUTRAL (ADR-0030 — `ST_CalculateFacts` has no error boundary): never
        # raise, never guess an amount. `dentro_teto_l2=False` structurally closes the auto-release
        # band (`r_dentro_teto_l2` requires `true`, pagto_alcada.dmn:65) and the conservative token
        # lands the DMN on its OWN catch-all row -> ANALISE_HUMANA / comite-financeiro / tier 4.
        # The route is the MODEL's, not this function's.
        logger.error(
            "pagto_valor_pagamento_invalido",
            motivo=motivo_valor,
            valor_tipo=type(raw_valor).__name__,
            ordem_id=variables.get("ordem_pagamento_id"),
        )
        dentro_teto = False
        valor_dmn = _VALOR_CENTS_ROTA_CONSERVADORA
    else:
        # COMPUTE the auto-release fact from policy: value within `high_value_payment.threshold_brl`.
        # A config problem / unloadable matrix => False => routes to a human alcada band.
        dentro_teto = resolver.within_l2_ceiling(
            tenant=variables.get("tenant_id", ""),
            action=_CEILING_ACTION,
            param=_CEILING_PARAM,
            value_cents=valor_cents,
        )
        valor_dmn = valor_cents

    rows, version = evaluate_sync(
        require_dmn(dmn, "operadora.pagto.calculate_facts"),
        "pagto_alcada",
        {
            "valor_pagamento_cents": valor_dmn,
            "dentro_teto_l2": dentro_teto,
            "tipo_pagamento": variables.get("tipo_pagamento", ""),
        },
    )
    row = first_row(rows, "pagto_alcada", variables)
    faixa = str(row.get("faixa_valor", ""))
    grupo = str(row.get("grupo_aprovador", ""))

    logger.info(
        "pagto_route_aprovacao",
        valor_cents=valor_dmn,
        motivo_valor=motivo_valor,
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

# Deadline for the whole Andre delegation round-trip (GK-dossier finding 2). Deliberately BELOW
# the external-task LOCK the harness takes on this task (`WorkerHarness(lock_duration_ms=30_000)`
# / `WORKER_LOCK_DURATION_MS`, `runtime/worker_runtime/settings.py`): a hung dispatcher (wedged
# pool, unreachable engine/PG inside Andre's graph) would otherwise hold the task past lock
# expiry, at which point the ENGINE re-delivers the SAME task to another worker while this one is
# still awaiting — two concurrent in-flight delegations for one `ST_PrepareApprovalDossier`, and
# a completion that arrives on an expired lock. 20s leaves ~10s of headroom for the completion
# round-trip. Expiry is DL-0037-shaped: a DISCLOSED gap on a COMPLETED task, never a raise —
# UT_AprovacaoAlcada still opens, the approver just sees `dossier_gap=delegation_timeout`.
_DOSSIER_DELEGATION_TIMEOUT_S: float = 20.0


def _dossier_topic_variables() -> list[str]:
    """The EXACT process-variable read-set of `prepare_approval_dossier` (GK-dossier finding 10).

    Fed to `harness.register(..., variables=...)` -> `TopicSubscription.variables` so the engine's
    `fetchAndLock` returns ONLY these for this topic (least privilege, design §5) instead of the
    default "every variable of the instance".

    Single-sourced from the delegation layer's own allowlists (the handler reads `tenant_id` and
    the case identifiers directly, and hands the rest to `build_pagto_dossier_envelope` as
    `case_meta`, which serializes exactly these) — so the subscription cannot drift from what the
    handler actually consumes. Imported lazily for the same reason the handler's own
    `delegate_pagto_dossier` import is lazy: the worker package must not import the agent package
    at module load.
    """
    from maezo.agents.andre.delegation import (
        _PAGTO_BOOLEAN_META_KEYS,
        _PAGTO_STRING_META_KEYS,
    )

    return [
        "tenant_id",  # ADR-0004 tenant scope — read directly by the handler's guard
        *_PAGTO_STRING_META_KEYS,  # case identifiers + bounded payment tokens
        "valor_pagamento_cents",  # INTEGER centavos (ADR-0018 part 2)
        *_PAGTO_BOOLEAN_META_KEYS,  # worker-pre-resolved facts (validate/calculate_facts)
    ]


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
    a structured rejection, a DELEGATION TIMEOUT or ANY delegation failure therefore returns
    `{"dossier_prepared": False, "dossier_gap": <bounded reason token>}` + a LOUD log and COMPLETES
    the task — the human UT MUST still open; this handler NEVER raises. The gap token is a bounded
    class token (engine-variable hygiene) — raw error text stays in the log.

    DEGRADED-BUT-SUCCESSFUL DISCLOSURE (GK-dossier finding 4): a delegation can succeed
    structurally while Andre ran DEGRADED inside (DMN unavailable / engine unreachable at the
    anchor step / missing runtime context). That returns `dossier_prepared=True` (the dossier does
    exist and is referenced) PLUS `dossier_gap="degraded:<bounded token>"` — the human approver is
    told how much the dossier is worth instead of seeing a clean success. The token is
    re-validated against Andre's own CLOSED `DEGRADED_TOKENS` set before it becomes an engine
    variable.

    BOUNDED AWAIT (GK-dossier finding 2): the delegation is awaited under
    `asyncio.wait_for(_DOSSIER_DELEGATION_TIMEOUT_S)` — safely below the 30s external-task lock, so
    a hung dispatcher can never hold this task to lock expiry (which would let the engine
    re-deliver the SAME task to another worker alongside this still-awaiting one). Expiry ->
    `dossier_gap="delegation_timeout"`, task COMPLETED, UT opens.

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

        from maezo.agents.andre.delegation import (
            DEGRADED_META_KEY,
            DEGRADED_TOKENS,
            delegate_pagto_dossier,
        )

        try:
            # BOUNDED AWAIT (GK-dossier finding 2): never hold the external-task lock to expiry.
            result = await asyncio.wait_for(
                delegate_pagto_dossier(
                    dispatcher,
                    tenant=tenant_id.strip(),
                    case_meta=dict(v),
                    ordem_pagamento_id=ordem_pagamento_id,
                    numero_lote_tiss=numero_lote_tiss,
                    prestador_id=prestador_id,
                    # The ENGINE's authoritative key of the instance THIS task belongs to
                    # (GK-dossier finding 1a). Threading it verbatim keeps Andre anchored on the
                    # SAME case: the ordem-first derivation DIVERGES from an instance keyed with
                    # the contract's CONTAS variant
                    # (`PAGTO-{tenant}-{numero_lote_tiss}-{prestador_id}`, contract §Business key)
                    # whenever an ordem is also in scope, and a diverging key would miss the
                    # idempotency lookup and open a SECOND SP-OP-PAGTO-001 instance — a duplicated
                    # UT_AprovacaoAlcada approval/release path. Blank -> derivation.
                    business_key=str(task.business_key or ""),
                ),
                timeout=_DOSSIER_DELEGATION_TIMEOUT_S,
            )
        except TimeoutError:
            # `asyncio.wait_for` CANCELS the pending delegation before raising, so nothing is left
            # running behind this return. DL-0037: complete the task with the disclosed gap — the
            # human UT opens on time instead of the lock expiring into an engine re-delivery.
            logger.error(
                "pagto_prepare_approval_dossier_delegation_timeout",
                tenant_id=tenant_id,
                ordem_pagamento_id=ordem_pagamento_id,
                business_key=task.business_key,
                timeout_s=_DOSSIER_DELEGATION_TIMEOUT_S,
            )
            return {"dossier_prepared": False, "dossier_gap": "delegation_timeout"}
        except Exception as exc:  # noqa: BLE001 — DL-0037: the UT must open; never raise here.
            logger.error(
                "pagto_prepare_approval_dossier_delegation_failed",
                tenant_id=tenant_id,
                ordem_pagamento_id=ordem_pagamento_id,
                business_key=task.business_key,
                # GK-dossier finding 5: `str(exc)` put RAW exception text in the log. This handler
                # sits downstream of PHI-bearing case variables (`case_meta=dict(v)`), and the
                # exceptions it catches come from the dispatcher/PG/engine layers whose messages
                # routinely echo the offending payload — a CPF/CNS in a driver error would land
                # verbatim in the operator log. `redact_error_message` (T3.4 F5) is the SAME
                # one-way, never-raising backstop the harness applies before an error reaches the
                # engine's incident store; it preserves the error CLASS for diagnosis.
                error=redact_error_message(exc),
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

        # DEGRADED-BUT-SUCCESSFUL (GK-dossier finding 4): the delegation succeeded STRUCTURALLY
        # (Andre's graph ran, routed and produced a dossier) but he may have been degraded INSIDE
        # — a DMN in the assess chain unavailable, the engine unreachable at the anchor step, or
        # runtime context missing. Reporting a clean `dossier_prepared=True` would hide that from
        # the human approver, who is the one who must weigh how much the dossier is worth. The
        # dossier IS prepared (True — it exists and is referenced), and the gap is disclosed
        # ALONGSIDE it. The token is re-validated against Andre's own CLOSED set here, so an
        # unexpected/unbounded meta value can never become an engine variable.
        degraded = str(result.meta.get(DEGRADED_META_KEY, "") or "").strip()
        logger.info(
            "pagto_prepare_approval_dossier_delegated",
            tenant_id=tenant_id,
            ordem_pagamento_id=ordem_pagamento_id,
            business_key=task.business_key,
            dossier_ref=result.output_ref,
            idempotent_replay=result.idempotent_replay,
            degraded=degraded,
        )
        outputs: dict[str, Any] = {
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
        if degraded:
            token = degraded if degraded in DEGRADED_TOKENS else "unknown"
            logger.warning(
                "pagto_prepare_approval_dossier_degraded",
                tenant_id=tenant_id,
                ordem_pagamento_id=ordem_pagamento_id,
                business_key=task.business_key,
                degraded=token,
            )
            outputs["dossier_gap"] = f"degraded:{token}"
        return outputs

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
    # LEAST PRIVILEGE (GK-dossier finding 10, design §5 `TopicSubscription.variables` seam): the
    # raw registration used the engine DEFAULT (`variables=None` = return EVERY process variable
    # of the instance). This handler forwards `case_meta=dict(task.variables)` into an A2A
    # envelope, so every extra variable the engine hands it is one more thing that has to be
    # stopped by the delegation-layer allowlist alone. Declaring the exact read-set moves the
    # boundary UPSTREAM — an un-allowlisted (potentially PHI-bearing) variable is never fetched,
    # never locked, never in this process's memory. The list is derived from the SAME allowlists
    # the envelope builder serializes, so it cannot drift from what the handler actually reads
    # (`test_dossier_topic_variables_match_what_the_handler_reads` single-sources it).
    harness.register(
        "operadora.pagto.prepare_approval_dossier",
        make_prepare_approval_dossier_handler(dossier_dispatcher),
        variables=_dossier_topic_variables(),
    )

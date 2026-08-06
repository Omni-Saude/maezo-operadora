"""Worker: credenciamento (SP-OP-CRED-001).

(Des)credenciamento de Prestador/Rede.
Two adverse directions: register_decred (L1) and register_cred_denial.
Guards: ERR_DECRED_NOT_HUMAN, ERR_CRED_DENIAL_NOT_HUMAN (ADR-0018).
"""

from __future__ import annotations

import functools
from typing import TYPE_CHECKING, Any

import structlog

from maezo.tools.workers.base import FunctionWorker, non_blank
from maezo.tools.workers.dmn_transport import DmnTransport, evaluate_sync, first_row, require_dmn
from maezo.tools.workers.harness import WorkerBpmnError

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

# ---------------------------------------------------------------
# Error codes
# ---------------------------------------------------------------

# The two ADVERSE guard codes are MODELED BPMN boundary errors (spec/processes/bpmn/
# SP-OP-CRED-001_Descredenciamento.bpmn: `Error_DecredNotHuman`/`Error_CredDenialNotHuman`, caught
# by `BE_DecredNaoHumano`/`BE_CredDenialNaoHumano` on `ST_RegisterDescredenciamento`/
# `ST_RegisterCredDenial` -> the NEUTRO terminals `End_DecredBloqueadoNaoHumano`/
# `End_CredGuardBloqueadoNaoHumano`). Their guards therefore raise `WorkerBpmnError(code)` — NOT a
# `.code`/`.message` `CredError` — mirroring `cancel.confirm_maintained_decision`
# (`ERR_CANCEL_MANTER_NOT_HUMAN`), the sibling adverse `_NOT_HUMAN` guard (ADR-0030 §2/§4). Rationale:
# a `CredError` is reclassified by `FunctionWorker.execute` (base.py:284-293) into a bare
# `ValueError`, which `WorkerHarness._handle`'s `except ValueError` branch reports as a generic
# `failure(retries=0)` incident and NEVER consults the `bpmn_error_allowlist` — so the modeled
# boundary could NEVER fire (the guard blocked the adverse write, but the clean fail-safe terminal was
# structurally UNREACHABLE, left as an opaque engine incident). `WorkerBpmnError` propagates unchanged
# through `execute` (it exposes `.error_code`, not `.code`/`.message`) to the harness's
# `except WorkerBpmnError` branch, which routes it to `handle_bpmn_error` (the boundary) when the code
# is allowlisted. Both codes are T-E-gated (adverse `_NOT_HUMAN` — ADR-0030 §4): consumption-covered
# by the boundary-proof gate (`scripts/ci/check_bpmn_error_allowlist.py`), yet DEFERRED out of
# `PRODUCTION_BPMN_ERROR_ALLOWLIST` until T-E audited-refusal is production-activated, so today they
# still fail-closed to an (audited) incident — identical runtime effect, but the boundary is now
# REACHABLE the moment T-E flips the allowlist (exactly the `ERR_CANCEL_MANTER_NOT_HUMAN` posture).
ERR_DECRED_NOT_HUMAN = "ERR_DECRED_NOT_HUMAN"
ERR_CRED_DENIAL_NOT_HUMAN = "ERR_CRED_DENIAL_NOT_HUMAN"
#: `validate_cred` raises this when `prestador_id` arrives absent/blank/non-string — a TECHNICAL
#: origin/consistency guard (G2-val, ADR-0030 §2 + migration Tier-2: the worker never decides to
#: deny/de-credential, it only signals bad-at-source identity data — T3.1 phase-2 finding 3, the
#: dead-model gap: the code was declared here and the boundary modeled, but no worker ever raised
#: it). MODELED boundary error: `BE_PrestadorInvalido` (Error_CredPrestadorInvalido) on
#: `ST_VerifyCredentials` routes to the NEUTRO terminal `End_CredPrestadorInvalido` — the BPMN's
#: own documentation: "Fail-safe de validacao (NAO adverso) ... Nenhum efeito adverso automatico
#: (ADR-0018)". NOT a `*_NOT_HUMAN` guard, so NOT T-E-gated (ADR-0030 §4).
ERR_CRED_INVALID_PRESTADOR = "ERR_CRED_INVALID_PRESTADOR"
# Clerical (favorable-direction) input-validation code — NOT a modeled boundary error; a genuine
# bad-input failure that correctly stays a `CredError` -> ValueError -> incident (no boundary to fire).
ERR_CRED_REGISTER_INVALID = "ERR_CRED_REGISTER_INVALID"

#: Consumption-covered (`scripts/ci/check_bpmn_error_allowlist.py`'s "simple rule"):
#: `operadora.cred.verify_credentials` is consumed ONLY by SP-OP-CRED-001, which declares this
#: errorCode on `BE_PrestadorInvalido`. Mirrors `nip.NIP_BPMN_ERROR_ALLOWLIST` /
#: `programa.PROGRAMA_BPMN_ERROR_ALLOWLIST` (#181, the item-9 bucket-3 precedent) — unioned into
#: `worker_runtime/service.py`'s `_GATE_PROVEN_BPMN_ERROR_CODES`. The two adverse `*_NOT_HUMAN`
#: codes above are DELIBERATELY excluded: they stay T-E-deferred (ADR-0030 §4) and reach
#: production only via the T-E enablement, never via this constant.
CRED_BPMN_ERROR_ALLOWLIST: frozenset[str] = frozenset({ERR_CRED_INVALID_PRESTADOR})

# Decision values
DECISAO_DESCREDENCIAR = "DESCREDENCIAR"
DECISAO_NEGAR_CREDENCIAMENTO = "NEGAR_CREDENCIAMENTO"
DECISAO_APROVAR_CREDENCIAMENTO = "APROVAR_CREDENCIAMENTO"
DECISAO_MANTER = "MANTER"


# ---------------------------------------------------------------
# validate_cred — verify provider credentials (FACT, never deny)
# ---------------------------------------------------------------


def validate_cred(variables: dict[str, Any]) -> dict[str, Any]:
    """Verify professional registration/CNES validity — FACT only.

    NEVER decides to deny or de-credential. TASY write DROP (ADR-0013).

    ORIGIN GUARD (item-9 bucket-3 Class-C — T3.1 phase-2 finding 3, ADR-0030 Tier-2 G2-val):
    a blank/absent/non-string `prestador_id` raises the MODELED
    `WorkerBpmnError(ERR_CRED_INVALID_PRESTADOR)` — caught by `BE_PrestadorInvalido` on
    `ST_VerifyCredentials` and routed to the NEUTRO terminal `End_CredPrestadorInvalido`
    ("fail-safe, nao adverso"). Checked BEFORE any fact resolution: a request whose provider
    identity is inconsistent at the source cannot be processed at all.

    FACT PRESERVATION (item-9 bucket-3 Class-C — T3.1 phase-2 finding 2, the
    `documentacao_completa`/`licenca_valida` OVERWRITE bug): an ALREADY-RESOLVED boolean
    `licenca_valida`/`documentacao_completa` process variable is respected, never clobbered.
    The pre-fix worker unconditionally returned `licenca_valida=True` (hardcoded placeholder —
    fail-OPEN whenever the resolved fact was False) and recomputed `documentacao_completa` from
    the SHAPE of `documentos_refs` (`isinstance(..., dict)`) — but the sibling families'
    convention carries `documentos_refs` as a Camunda STRING ref, so the recompute was ALWAYS
    False and, forwarded as real process variables on task complete, OVERWROTE the resolved fact
    before the native `BRT_Admissibilidade` businessRuleTask evaluated (`cred_admissibility`'s
    FIRST-hit `r_cred_doc_pendente` row then rerouted every credenciamento-direction scenario to
    PENDENTE_DOCUMENTACAO). Only an explicit engine BOOLEAN is respected (engine variables
    arrive untyped — a string/int/None "fact" is NOT a resolved fact and falls back to the
    placeholder computation); respecting a resolved False is strictly MORE conservative than the
    old hardcoded True (routes to pendency/human, never away from it).

    LOOP-BACK CONTRACT (GK-w4 finding 3 — the corollary of FACT PRESERVATION above): the
    PENDENTE_DOCUMENTACAO branch loops back through here. `ST_NotifyDocPendente` ->
    `ICE_AguardarInfoDoc` waits for `msg.cred.info_received` and its outgoing flow
    (`Flow_InfoReceived_VerifyCred`) re-triggers `ST_VerifyCredentials`, i.e. this function.
    Because a resolved boolean is now RESPECTED and never re-derived from `documentos_refs`, the
    correlation payload of `msg.cred.info_received` MUST carry a boolean
    `documentacao_completa=True` for the pendency to clear. Sending only the documents (e.g. an
    updated `documentos_refs`) leaves the previously-resolved `documentacao_completa=False`
    standing, `cred_admissibility`'s FIRST-hit `r_cred_doc_pendente` row re-routes to
    PENDENTE_DOCUMENTACAO, and the instance re-enters the same wait — a pendency loop, not a
    self-heal. This is deliberate and fail-safe (the loop is neutral: no denial, no
    de-credentialing, and the human User Tasks remain the only adverse path), but it makes the
    correlation payload part of the contract rather than an implementation detail — see the
    `msg.cred.info_received` row in docs/processes/contracts/SP-OP-CRED-001.md.
    """
    prestador_id = _norm_str(variables.get("prestador_id", ""))
    if not prestador_id:
        logger.error(
            "cred_prestador_invalido_na_origem",
            tenant_id=variables.get("tenant_id"),
            direcao=variables.get("direcao"),
        )
        # MODELED boundary error (BE_PrestadorInvalido -> End_CredPrestadorInvalido, terminal
        # NEUTRO "fail-safe, nao adverso") — WorkerBpmnError, not CredError; ADR-0030 §2 Tier-2
        # (G2-val), the nip/programa #181 precedent. A CredError would be reclassified to a bare
        # ValueError -> generic failure(retries=0) incident stalling the mainline at the very
        # first service task; the gate-proven WorkerBpmnError routes to the clean terminal.
        # Fail-closed normalization: blank/whitespace-only/non-string prestador_id all refuse.
        raise WorkerBpmnError(
            ERR_CRED_INVALID_PRESTADOR,
            "prestador_id ausente/vazio na origem — solicitacao inconsistente; "
            "validacao barrou (fail-safe, nao adverso; ADR-0018)",
        )

    documentos = variables.get("documentos_refs", {})
    seeded_licenca = variables.get("licenca_valida")
    seeded_doc_completa = variables.get("documentacao_completa")

    # Factual check: are documents present and license supposedly valid?
    # In real implementation, this queries external registries.
    # placeholder fallback — real: query CRM/CNES; only used when no resolved boolean fact exists.
    licenca_valida = seeded_licenca if isinstance(seeded_licenca, bool) else True
    documentacao_completa = (
        seeded_doc_completa
        if isinstance(seeded_doc_completa, bool)
        else isinstance(documentos, dict) and len(documentos) > 0
    )

    logger.info(
        "cred_validate_cred",
        prestador_id=variables.get("prestador_id"),
        licenca_valida=licenca_valida,
        documentacao_completa=documentacao_completa,
    )

    return {
        "licenca_valida": licenca_valida,
        "documentacao_completa": documentacao_completa,
    }


# ---------------------------------------------------------------
# assess_admissibility — classify (NEVER deny/decred)
# ---------------------------------------------------------------


def assess_admissibility(variables: dict[str, Any], *, dmn: DmnTransport | None = None) -> dict[str, Any]:
    """Classify admissibility and route — NEVER produces NEGAR/DESCREDENCIAR.

    Evaluates TWO chained deployed decision tables (ADR-0028/T1.5): `cred_admissibility`
    (direcao, tipo_prestador, documentacao_completa, licenca_valida, dentro_criterios_rede,
    indicio_irregularidade_sinalizado) -> `roteamento` in {CLERICAL_CREDENCIAR, SEGUE_ANALISE,
    PENDENTE_DOCUMENTACAO, ANALISE_HUMANA}; ONLY when it returns `SEGUE_ANALISE` (non-terminal
    — proceed to human-track routing), chained into `cred_route` (direcao, tipo_prestador,
    origem_solicitacao, indicio_irregularidade_sinalizado) -> `roteamento` in
    {ANALISE_CREDENCIAMENTO, ANALISE_DESCREDENCIAMENTO, ANALISE_HUMANA} — the FINAL routing.
    The old Python conflated both tables' output domains into a single hand-forked if/elif
    ladder (its own `roteamento` sometimes held `cred_admissibility` values, sometimes
    `cred_route` values, matching neither table's real rule order). Both tables live-verified
    against the compose engine before cutover (5/5 existing test scenarios reproduced exactly,
    plus a new `CLERICAL_CREDENCIAR` case). Adverse-adjacent (provider credentialing) — per
    ADR-0028 §7, policy-guardian review recommended before this cutover is considered cleared.
    """
    direcao = variables.get("direcao", "credenciamento")
    tipo_prestador = variables.get("tipo_prestador", "")
    licenca_valida = variables.get("licenca_valida", False)
    documentacao_completa = variables.get("documentacao_completa", False)
    dentro_criterios = variables.get("dentro_criterios_rede", False)
    indicio_irregular = variables.get("indicio_irregularidade_sinalizado", False)

    dmn_transport = require_dmn(dmn, "operadora.cred.check_network_criteria")

    adm_rows, adm_version = evaluate_sync(
        dmn_transport,
        "cred_admissibility",
        {
            "direcao": direcao,
            "tipo_prestador": tipo_prestador,
            "documentacao_completa": bool(documentacao_completa),
            "licenca_valida": bool(licenca_valida),
            "dentro_criterios_rede": bool(dentro_criterios),
            "indicio_irregularidade_sinalizado": bool(indicio_irregular),
        },
    )
    adm_row = first_row(adm_rows, "cred_admissibility", variables)
    roteamento = str(adm_row.get("roteamento", "ANALISE_HUMANA"))
    motivo = str(adm_row.get("motivo", ""))
    route_version = None

    if roteamento == "SEGUE_ANALISE":
        route_rows, route_version = evaluate_sync(
            dmn_transport,
            "cred_route",
            {
                "direcao": direcao,
                "tipo_prestador": tipo_prestador,
                "origem_solicitacao": variables.get("origem_solicitacao", ""),
                "indicio_irregularidade_sinalizado": bool(indicio_irregular),
            },
        )
        route_row = first_row(route_rows, "cred_route", variables)
        roteamento = str(route_row.get("roteamento", roteamento))
        motivo = str(route_row.get("motivo", motivo))

    logger.info(
        "cred_assess_admissibility",
        prestador_id=variables.get("prestador_id"),
        roteamento=roteamento,
        motivo=motivo,
        dmn_admissibility_version=adm_version.version,
        dmn_route_version=route_version.version if route_version else None,
    )

    return {
        "roteamento": roteamento,
        "motivo": motivo,
    }


# ---------------------------------------------------------------
# notify_prestador — neutral notification
# ---------------------------------------------------------------


def notify_prestador(variables: dict[str, Any]) -> dict[str, Any]:
    """Notify provider about the (de)credentialing process (NEUTRAL)."""
    prestador_id = variables.get("prestador_id", "")
    logger.info("cred_notify_prestador", prestador_id=prestador_id)

    return {
        "notificacao_previa_feita": True,
    }


# ---------------------------------------------------------------
# notify_doc_pendente — neutral notification (documentacao pendente)
# ---------------------------------------------------------------


def notify_doc_pendente(variables: dict[str, Any]) -> dict[str, Any]:
    """Request missing documentation from the provider (NEUTRAL — never a denial).

    Reached ONLY when `cred_admissibility` routes `PENDENTE_DOCUMENTACAO`
    (`documentacao_completa=false` — BPMN `ST_NotifyDocPendente`, upstream of
    `ICE_AguardarInfoDoc`, which waits for `msg.cred.info_received` to re-trigger
    `ST_VerifyCredentials`). Incomplete documentation NEVER produces an automatic denial — only
    the human User Tasks (`UT_AnaliseCredenciamento`/`UT_AnaliseDescredenciamento`) can ever
    deny/de-credential (ADR-0018); this worker only observes/logs the pendency and asks for the
    missing documents. Mirrors this module's own `notify_prestador` (`check_prior_notice`) idiom —
    same shape, same BPMN `event_topic_pended` extensionElement.

    A genuine `kafka.publish` of `agents.events.cred.pended` (the BPMN's own
    `event_topic_pended` extensionElement) is NOT fabricated here — same documented gap as this
    module's sibling `notify_prestador` (which carries the identical extensionElement and does
    not publish either) and the cross-family "not fabricated here" convention documented in
    `ans_submit.register_ans_submit_workers`'s docstring (mirrored by `cancel.py`/`contas.py`/
    `reembolso.py`/`nip.py`/`recurso.py`/`inadimplencia.py` for their own entry functions).
    """
    prestador_id = variables.get("prestador_id", "")
    tenant_id = variables.get("tenant_id", "")
    direcao = variables.get("direcao", "")

    logger.info(
        "cred_notify_doc_pendente",
        prestador_id=prestador_id,
        tenant_id=tenant_id,
        direcao=direcao,
    )

    return {
        "documentacao_pendente_notificada": True,
        "prestador_id": prestador_id,
    }


# ---------------------------------------------------------------
# notify_sla_risk — neutral notification (alerta de risco de SLA)
# ---------------------------------------------------------------


def notify_sla_risk(variables: dict[str, Any]) -> dict[str, Any]:
    """Alert coordenacao-rede of SLA risk (non-interruptive timer, both directions).

    Mirrors the proven `cancel.notify_sla_risk` / `inadimplencia.notify_sla_risk` /
    `auth.NotifySlaRiskWorker` idiom: informational only — the User Task
    (`UT_AnaliseDescredenciamento`/`UT_AnaliseCredenciamento`) stays open, no decision is made or
    altered, no adverse outcome is produced by the alert. Fires at `sla.sla_alerta` (~50-70% of
    `sla.sla_analise` — DMN `cred_sla`); shared by BOTH `BT_AlertaSlaDescred` and
    `BT_AlertaSlaCred` (BPMN `ST_NotifySlaRisk`, fed by both non-interruptive boundary timers).
    """
    prestador_id = variables.get("prestador_id", "")
    tenant_id = variables.get("tenant_id", "")
    direcao = variables.get("direcao", "")

    logger.warning(
        "cred_notify_sla_risk",
        prestador_id=prestador_id,
        tenant_id=tenant_id,
        direcao=direcao,
        grupo_alertado="coordenacao-rede",
    )

    return {
        "sla_risk_notified": True,
        "grupo_alertado": "coordenacao-rede",
        "prestador_id": prestador_id,
    }


# ---------------------------------------------------------------
# register_decred — GATED adverse effect A (descredenciamento)
# ---------------------------------------------------------------


def _norm_str(value: Any) -> str:
    """Normalize an engine variable to a stripped string for guard checks — FAIL-CLOSED.

    Mirrors `pagto._norm_str` (t2.5-p2b-round2 / t3.1-guard-input-hardening): the pre-fix bare
    `if not responsavel_id` / `if not fundamentacao` / ... checks in BOTH of this module's L1
    adverse guards (`_register_descredenciamento`, `register_cred_denial`) let WHITESPACE-ONLY
    decision + accountability fields pass — defeating ADR-0007/RN 566/RN 567 (an adverse
    de-credentialing/denial recorded with a non-identifying approver or blank justification/
    regulatory reference/prior-notice proof). Closes that class:
    - a `str` normalizes to `value.strip()` — whitespace-only ("   ", "\\t", "\\n", ...)
      becomes "" and is treated EXACTLY like an absent field (refusal, never registration);
    - a NON-string (None, int, bool, list, dict — engine variables arrive untyped) normalizes
      to "" (fail-closed refusal), never a truthy pass-through and never an AttributeError
      incident from calling `.strip()` on a non-string.
    """
    if isinstance(value, str):
        return value.strip()
    return ""


def register_decred(variables: dict[str, Any]) -> dict[str, Any]:
    """Register provider de-credentialing.

    GUARDED: ERR_DECRED_NOT_HUMAN.
    Requires decisao_cred == DESCREDENCIAR from human + required fields.
    """
    return _register_descredenciamento(variables)


def _register_descredenciamento(variables: dict[str, Any]) -> dict[str, Any]:
    """Internal: validate guard and register de-credentialing.

    NORMALIZATION (t3.1-guard-input-hardening): ALL decision + human-accountability string
    fields (`decisao_cred`, `responsavel_id`, `fundamentacao`, `referencia_regulatoria`,
    `comprovacao_notificacao_previa`, `plano_substituicao` — the last per contract
    SP-OP-CRED-001.md:80,149, obligatory when `tem_beneficiarios_vinculados`) are normalized
    via `_norm_str` (strip; non-string -> "") BEFORE any guard check, mirroring
    `pagto.register_payment_refusal`'s fix. Whitespace-only/non-string refuses exactly like
    absent; a whitespace-PADDED exact `decisao_cred` literal still passes (case variants/
    substrings still refuse — exact `!=` match, no folding). `tem_beneficiarios_vinculados`
    is a boolean routing FACT, not a human-typed string — untouched.

    GATEWAY FACT ECHO (GW_Substituicao, BPMN lines 430-438): `plano_substituicao` is an OPTIONAL
    UT field (only obligatory when `tem_beneficiarios_vinculados`, per the guard above), so it is
    ABSENT from process scope on the pure descredenciado path — the BPMN's own inline comment
    documents that referencing it directly in the gateway's JUEL condition would throw "Cannot
    resolve identifier" and stall the instance. This worker therefore echoes a guaranteed-in-scope
    FLAT boolean `tem_plano_substituicao` (idiom mirrored from SP-OP-FRAUDE-001's
    `destino_referral_cred`/`destino_referral_contratual` flat gateway facts, null-safe `== true`
    JUEL), derived from the ALREADY-normalized `plano_substituicao` above: `bool(plano_substituicao)`
    — a non-blank string is `True`; absent/`None`/blank/whitespace-only/non-string junk (int, list,
    dict, bool — `_norm_str` maps every non-string to `""`) is `False`. FAIL-SAFE DIRECTION: `False`
    (plain de-accreditation) is the conservative reading for junk input, not `True` (substitution
    registered) — a substitution wrongly claimed on malformed input would falsely signal continuity
    of care to beneficiaries who in fact have none, whereas the reverse (a real plan misread as
    absent) merely falls through to the plain de-accreditation terminal without inventing a false
    assurance. Same direction `_norm_str` already fails closed in, elsewhere in this module.
    """
    decisao = _norm_str(variables.get("decisao_cred", ""))
    responsavel_id = _norm_str(variables.get("responsavel_id", ""))
    fundamentacao = _norm_str(variables.get("fundamentacao", ""))
    ref_regulatoria = _norm_str(variables.get("referencia_regulatoria", ""))
    comprovacao_notif = _norm_str(variables.get("comprovacao_notificacao_previa", ""))
    tem_benef = variables.get("tem_beneficiarios_vinculados", False)
    plano_substituicao = _norm_str(variables.get("plano_substituicao", ""))

    errors: list[str] = []

    if decisao != DECISAO_DESCREDENCIAR:
        errors.append(f"decisao_cred != {DECISAO_DESCREDENCIAR} (got: {decisao!r})")
    if not responsavel_id:
        errors.append("responsavel_id ausente (ADR-0007)")
    if not fundamentacao:
        errors.append("fundamentacao ausente")
    if not ref_regulatoria:
        errors.append("referencia_regulatoria ausente (RN 567)")
    if not comprovacao_notif:
        errors.append("comprovacao_notificacao_previa ausente (RN 567)")
    if tem_benef and not plano_substituicao:
        errors.append("plano_substituicao ausente (ha beneficiarios vinculados, RN 567)")

    if errors:
        logger.error(
            "cred_decred_guard_rejected",
            errors=errors,
            prestador_id=variables.get("prestador_id"),
        )
        # MODELED boundary error (BE_DecredNaoHumano) — WorkerBpmnError, not CredError; see the
        # error-codes section above and cancel.confirm_maintained_decision for the rationale.
        raise WorkerBpmnError(ERR_DECRED_NOT_HUMAN, "; ".join(errors))

    tem_plano_substituicao = bool(plano_substituicao)

    logger.info(
        "cred_prestador_descredenciado",
        prestador_id=variables.get("prestador_id"),
        responsavel_id=responsavel_id,
        tem_plano_substituicao=tem_plano_substituicao,
    )

    return {
        "descredenciamento_registrado": True,
        "network_changed": True,
        "data_efeito_iso": variables.get("data_efeito_iso", ""),
        "tem_plano_substituicao": tem_plano_substituicao,
    }


register_descredenciamento = _register_descredenciamento


# ---------------------------------------------------------------
# register_cred_denial — GATED adverse effect B (negativa de credenciamento)
# ---------------------------------------------------------------


def register_cred_denial(variables: dict[str, Any]) -> dict[str, Any]:
    """Register denial of credentialing application.

    GUARDED: ERR_CRED_DENIAL_NOT_HUMAN.

    NORMALIZATION (t3.1-guard-input-hardening): ALL decision + human-accountability fields
    (`decisao_cred`, `responsavel_id`, `fundamentacao`, `referencia_regulatoria` — contract
    SP-OP-CRED-001.md:148,168) are normalized via `_norm_str` (strip; non-string -> "")
    BEFORE any guard check — whitespace-only/non-string refuses exactly like absent; a
    whitespace-PADDED exact decision literal still passes (exact `!=` match, no folding).
    """
    decisao = _norm_str(variables.get("decisao_cred", ""))
    responsavel_id = _norm_str(variables.get("responsavel_id", ""))
    fundamentacao = _norm_str(variables.get("fundamentacao", ""))
    ref_regulatoria = _norm_str(variables.get("referencia_regulatoria", ""))

    errors: list[str] = []

    if decisao != DECISAO_NEGAR_CREDENCIAMENTO:
        errors.append(f"decisao_cred != {DECISAO_NEGAR_CREDENCIAMENTO} (got: {decisao!r})")
    if not responsavel_id:
        errors.append("responsavel_id ausente (ADR-0007)")
    if not fundamentacao:
        errors.append("fundamentacao ausente")
    if not ref_regulatoria:
        errors.append("referencia_regulatoria ausente (RN 566)")

    if errors:
        logger.error(
            "cred_denial_guard_rejected",
            errors=errors,
            prestador_id=variables.get("prestador_id"),
        )
        # MODELED boundary error (BE_CredDenialNaoHumano) — WorkerBpmnError, not CredError; see the
        # error-codes section above and cancel.confirm_maintained_decision for the rationale.
        raise WorkerBpmnError(ERR_CRED_DENIAL_NOT_HUMAN, "; ".join(errors))

    logger.info(
        "cred_credenciamento_negado",
        prestador_id=variables.get("prestador_id"),
        responsavel_id=responsavel_id,
    )

    return {
        "credenciamento_negado": True,
    }


# ---------------------------------------------------------------
# register_credenciamento — NEUTRAL clerical registration (favorable direction)
# ---------------------------------------------------------------


def register_credenciamento(variables: dict[str, Any]) -> dict[str, Any]:
    """Register APPROVED credentialing (NEUTRAL — favorable direction, NEVER adverse).

    EXCECAO CLERICAL (BPMN process documentation lines 54-59; contract SS Terminais
    humano-gated, `End_PrestadorCredenciado` row): crediting a NEW provider is NOT an adverse
    effect against anyone. Unlike `register_descredenciamento`/`register_cred_denial` (this
    module's two L1 `ERR_*_NOT_HUMAN`-guarded adverse workers), this worker carries NO
    human-decision requirement — the BPMN reaches it via EITHER of two inbound edges, both
    already gated by the engine itself before this task ever executes:
      - `Flow_GW_ClericalCredenciar`: `cred_admissibility` DMN routed `CLERICAL_CREDENCIAR`
        (documentacao completa + licenca valida + dentro de criterios de rede) — no
        `decisao_cred` variable is even set on this path;
      - `Flow_GWCred_Aprovar`: sequenceFlow `conditionExpression`
        `${decisao_cred == 'APROVAR_CREDENCIAMENTO'}` — an explicit equality gate, NOT a
        default/catch-all edge (contrast `cancel.py`'s `GW_Manter`, whose default edge fires for
        ANY unrecognized value and therefore genuinely needs `confirm_maintained_decision`-style
        defense-in-depth; `GW_DecisaoCred` has no such default routed here — its default targets
        `ST_PublishVinculoMantidoCred` instead).
    Fail-closed ONLY on the identity fields required to safely record the registration and the
    downstream `network_changed` fact (`tenant_id`/`prestador_id`) — never on
    `decisao_cred`/`responsavel_id` (both correctly absent on the clerical path; `responsavel_id`
    is contractually scoped to "os workers adversos" only — SS Variaveis de saida). This worker
    has no adverse branch to guard against and therefore raises no `*_NOT_HUMAN` error.
    """
    tenant_id = variables.get("tenant_id", "")
    prestador_id = variables.get("prestador_id", "")

    errors: list[str] = []
    if not tenant_id:
        errors.append("tenant_id ausente")
    if not prestador_id:
        errors.append("prestador_id ausente")

    if errors:
        logger.error(
            "cred_register_credenciamento_rejected",
            errors=errors,
        )
        raise CredError(ERR_CRED_REGISTER_INVALID, "; ".join(errors))

    logger.info(
        "cred_prestador_credenciado",
        tenant_id=tenant_id,
        prestador_id=prestador_id,
        decisao_cred=variables.get("decisao_cred", ""),
    )

    return {
        "credenciamento_registrado": True,
        "network_changed": True,
        "data_efeito_iso": variables.get("data_efeito_iso", ""),
    }


# ---------------------------------------------------------------
# prepare_dossier — REAL Carolina A2A delegation (raw async handler; DL-0033 closed, DL-0037)
# ---------------------------------------------------------------


def make_prepare_dossier_handler(dispatcher: DelegationDispatcher | None) -> TaskHandler:
    """Create the handler for `operadora.cred.prepare_dossier` — the REAL Carolina A2A delegation.

    Serves BOTH `ST_PrepareDossierDescred` and `ST_PrepareDossierCred`. Replaces the DL-0033
    local stub with `delegate_cred_dossier` -> `DelegationDispatcher.delegate` -> Carolina's REAL
    graph (`credentialing.analyze`, `agents/carolina/delegation.py`) — the full-A2A wiring
    DL-0033 explicitly deferred.

    RAW ASYNC HANDLER (DL-0034 precedent, escalation/events/#55 R-B sanctioned form): a
    `FunctionWorker.execute(dict)` boundary is sync while `dispatcher.delegate` is async — the
    raw `harness.register()` form runs this handler on the harness's own loop, where the
    dispatcher's pooled audit sink lives. Populates `_handlers` but NOT the `WorkerRegistry`
    (see `test_bootstrap_registration.py`'s `raw_handler_topics`).

    FAIL-NEUTRAL-WITH-DISCLOSED-GAP (DL-0037): assembling a dossier INSTRUCTS the human decision
    (UT_AnaliseDescredenciamento / UT_AnaliseCredenciamento) — it "instrui, nao decide"
    (SP-OP-CRED-001). A missing dispatcher (degraded runtime: no signing key / no DATABASE_URL),
    missing business identifiers, a structured rejection or ANY delegation failure therefore
    returns `{"dossier_prepared": False, "dossier_gap": <bounded reason token>}` + a LOUD log and
    COMPLETES the task — the human User Task MUST still open; this handler NEVER raises. The gap
    token is a bounded class token (engine-variable hygiene) — raw error text stays in the log.
    """

    async def handler(task: ExternalTask) -> Mapping[str, Any]:
        v = task.variables
        tenant_id = str(v.get("tenant_id", "") or "")
        prestador_id = str(v.get("prestador_id", "") or "")
        protocolo_cred = str(v.get("protocolo_cred", "") or "").strip() or None
        direcao = v.get("direcao", "")

        if dispatcher is None:
            # Degraded runtime (DL-0037): dispatcher absent at composition (no signing key /
            # no DATABASE_URL — worker_runtime readiness reports dossier_delegation_ready=false).
            # The UT still opens; the reviewer sees the disclosed gap instead of a dossier.
            logger.warning(
                "cred_prepare_dossier_dispatcher_unavailable",
                tenant_id=tenant_id,
                prestador_id=prestador_id,
                direcao=direcao,
                business_key=task.business_key,
            )
            return {"dossier_prepared": False, "dossier_gap": "dispatcher_unavailable"}

        if not (non_blank(tenant_id) and non_blank(prestador_id)):
            # No idempotent CRED business key can be derived (EB-4 R1 `non_blank` discipline) —
            # never delegate with a degenerate task_id; the UT still opens with the gap disclosed.
            logger.error(
                "cred_prepare_dossier_missing_identifiers",
                tenant_id=tenant_id,
                prestador_id=prestador_id,
                business_key=task.business_key,
            )
            return {"dossier_prepared": False, "dossier_gap": "missing_business_identifiers"}

        from maezo.agents.carolina.delegation import delegate_cred_dossier

        try:
            result = await delegate_cred_dossier(
                dispatcher,
                tenant=tenant_id.strip(),
                prestador_id=prestador_id.strip(),
                case_meta=dict(v),
                protocolo_cred=protocolo_cred,
            )
        except Exception as exc:  # noqa: BLE001 — DL-0037: the UT must open; never raise here.
            logger.error(
                "cred_prepare_dossier_delegation_failed",
                tenant_id=tenant_id,
                prestador_id=prestador_id,
                business_key=task.business_key,
                error=str(exc),
            )
            return {"dossier_prepared": False, "dossier_gap": "delegation_failed"}

        if not result.success:
            reason = str(result.rejection_reason or "unknown")
            logger.error(
                "cred_prepare_dossier_delegation_rejected",
                tenant_id=tenant_id,
                prestador_id=prestador_id,
                business_key=task.business_key,
                reason=reason,
                detail=result.detail,
            )
            return {"dossier_prepared": False, "dossier_gap": f"delegation_rejected:{reason}"}

        logger.info(
            "cred_prepare_dossier_delegated",
            tenant_id=tenant_id,
            prestador_id=prestador_id,
            direcao=direcao,
            business_key=task.business_key,
            dossier_ref=result.output_ref,
            idempotent_replay=result.idempotent_replay,
        )
        return {
            "dossier_prepared": True,
            "dossier_ref": result.output_ref or "",
            # UT-FORM SEAM (SME/PO sign-off PENDING): the dossier CONTENT field schema for
            # UT_AnaliseDescredenciamento/UT_AnaliseCredenciamento is uncontracted — no dossier
            # field appears in the contract's variable table. `dossier_summary` carries Carolina's
            # agent-produced bounded summary tokens AS-IS (route/desfecho/motivo/grupo — never the
            # narrative, never a decision); the full dossier is reachable via `dossier_ref`.
            # Do NOT invent/extend this schema here — it is the human-gated injection point.
            "dossier_summary": dict(result.meta),
        }

    return handler


# ---------------------------------------------------------------
# Custom error
# ---------------------------------------------------------------


class CredError(Exception):
    """Coded worker error for credenciamento CLERICAL input-validation (`ERR_CRED_REGISTER_INVALID`).

    Reclassified by `FunctionWorker.execute` (base.py:284-293) into a bare `ValueError` -> harness
    `failure(retries=0)` incident, the correct outcome for a genuine bad-input failure with NO modeled
    BPMN boundary. The two ADVERSE guards (`register_descredenciamento`/`register_cred_denial`) do NOT
    use this — they raise `WorkerBpmnError(ERR_DECRED_NOT_HUMAN)`/`WorkerBpmnError(
    ERR_CRED_DENIAL_NOT_HUMAN)` so their modeled boundary catches can fire (see the error-codes
    section at the top of this module).
    """

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(f"{code}: {message}")


# ---------------------------------------------------------------
# Bootstrap — FunctionWorker adapter (T1.2/ADR-0026 Decisao §2a).
#
# Topic mapping vs spec/processes/bpmn/SP-OP-CRED-001_Descredenciamento.bpmn
# (excl. shared/out-of-scope `operadora.events.publish`):
#   validate_cred          -> operadora.cred.verify_credentials     (exact spec match; raises the
#                             MODELED ERR_CRED_INVALID_PRESTADOR origin-guard, ADR-0030 Tier-2)
#   assess_admissibility   -> operadora.cred.check_network_criteria (spec match: RN 566 criteria
#                             classification)
#   notify_prestador       -> operadora.cred.check_prior_notice     (spec match: RN 567 prior-notice
#                             dispatch)
#   notify_doc_pendente    -> operadora.cred.notify_doc_pendente    (exact spec match, T2.5-p2b;
#                             NEUTRAL — PENDENTE_DOCUMENTACAO never denies)
#   register_decred (alias register_descredenciamento)
#     -> operadora.cred.register_descredenciamento (exact spec match, GUARDED)
#   register_cred_denial   -> operadora.cred.register_cred_denial   (exact spec match, GUARDED)
#   register_credenciamento -> operadora.cred.register_credenciamento (exact spec match, T2.5-p2b;
#                             NEUTRAL — EXCECAO CLERICAL, no human-gate guard by design)
#   notify_sla_risk        -> operadora.cred.notify_sla_risk        (exact spec match, T2.5-p2b;
#                             informational, shared by both directions' boundary timers)
#   make_prepare_dossier_handler -> operadora.cred.prepare_dossier  (exact spec match; RAW async
#                             handler — the REAL Carolina A2A delegation (credentialing.analyze)
#                             DL-0033 deferred, now wired. NEUTRAL — instructs the human UT, never
#                             adverse; dispatcher absent/failed -> disclosed-gap marker, the UT
#                             still opens (DL-0037). Both ST_PrepareDossierDescred and
#                             ST_PrepareDossierCred route here.)
# ---------------------------------------------------------------


def register_credenciamento_workers(
    harness: WorkerHarness,
    kafka: KafkaPublisher | None = None,
    **seams: Any,
) -> None:
    """Register the SP-OP-CRED-001 function workers on `harness`.

    `dmn` (ADR-0028 §1 seam) is threaded into `assess_admissibility` (`cred_admissibility` +
    `cred_route`, T1.5 cutover) via `functools.partial`; no other function here evaluates a
    DMN table.

    `dossier_dispatcher` (dossier-A2A seam, DL-0033 real wiring) is threaded into the
    `prepare_dossier` RAW async handler — a `DelegationDispatcher` assembled by the
    worker-runtime composition root (`build_dossier_delegation_dispatcher`). Absent (`None`, the
    topic-probe default and the degraded-runtime posture) the topic still registers and the
    handler fail-neutrals with a disclosed gap (DL-0037) — the human UT always still opens.
    """
    del kafka  # unused — no credenciamento.py worker declares a Kafka dependency
    dmn = seams.get("dmn")
    dossier_dispatcher: DelegationDispatcher | None = seams.get("dossier_dispatcher")
    harness.register_worker(FunctionWorker("operadora.cred.verify_credentials", validate_cred))
    harness.register_worker(
        FunctionWorker(
            "operadora.cred.check_network_criteria", functools.partial(assess_admissibility, dmn=dmn)
        )
    )
    harness.register_worker(FunctionWorker("operadora.cred.check_prior_notice", notify_prestador))
    harness.register_worker(FunctionWorker("operadora.cred.notify_doc_pendente", notify_doc_pendente))
    harness.register_worker(
        FunctionWorker("operadora.cred.register_descredenciamento", register_descredenciamento)
    )
    harness.register_worker(FunctionWorker("operadora.cred.register_cred_denial", register_cred_denial))
    harness.register_worker(FunctionWorker("operadora.cred.register_credenciamento", register_credenciamento))
    harness.register_worker(FunctionWorker("operadora.cred.notify_sla_risk", notify_sla_risk))
    # RAW handler (NOT register_worker) — needs the async dispatcher seam (module topic-map note).
    harness.register("operadora.cred.prepare_dossier", make_prepare_dossier_handler(dossier_dispatcher))

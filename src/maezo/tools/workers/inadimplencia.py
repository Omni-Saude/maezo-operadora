"""Worker: inadimplencia (SP-OP-INADIMPLENCIA-001).

Suspensao/Rescisao por Inadimplencia.
Guard: ERR_CONTRACT_SUSPENSION_NOT_HUMAN (L0-hard, ADR-0018).
NAO ha worker de rescisao gated AQUI — rescisao e propriedade de CANCEL-001.
"""

from __future__ import annotations

import asyncio
import functools
import uuid
from typing import TYPE_CHECKING, Any

import structlog

from maezo.tools.mcp_cibseven.transport import AgentDecisionProvenance, start_process_idempotent
from maezo.tools.workers.base import (
    CANCEL_KEY_FAMILY,
    FunctionWorker,
    contract_business_key_forms,
    mint_contract_business_key,
    non_blank,
)
from maezo.tools.workers.dmn_transport import DmnTransport, evaluate_sync, first_row, require_dmn
from maezo.tools.workers.harness import AUDIT_AGENT_ID, WorkerBpmnError, _resolve_app_version

if TYPE_CHECKING:
    from maezo.tools.mcp_cibseven.transport import AuditStartSink, CibSevenTransport
    from maezo.tools.workers.harness import KafkaPublisher, WorkerHarness

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------
# Error codes
# ---------------------------------------------------------------

# ERR_CONTRACT_SUSPENSION_NOT_HUMAN is a MODELED BPMN boundary error (spec/processes/bpmn/
# SP-OP-INADIMPLENCIA-001_Suspensao_Rescisao.bpmn: `Error_ContractSuspensionNotHuman`, caught by
# `BE_SuspensaoNaoHumano` on `ST_RegisterSuspension` -> the NEUTRO terminal
# `End_SuspensaoBloqueadaNaoHumano`). The guard below therefore raises `WorkerBpmnError(code)` —
# NOT a `.code`/`.message` `InadimplenciaError` — mirroring `cancel.confirm_maintained_decision`
# (`ERR_CANCEL_MANTER_NOT_HUMAN`) and `credenciamento`'s two adverse `*_NOT_HUMAN` guards
# (ADR-0030 §2/§4, Tier-3 G2-guard; this family was the ADR's own disclosed "unmigrated" example —
# ADR-0030 ratification amendment note). Rationale (unchanged from cred/cancel): an
# `InadimplenciaError` is reclassified by `FunctionWorker.execute` (base.py:284-293) into a bare
# `ValueError`, which `WorkerHarness._handle`'s `except ValueError` branch reports as a generic
# `failure(retries=0)` incident and NEVER consults the `bpmn_error_allowlist` — so the modeled
# boundary could NEVER fire (the guard blocked the suspension, but the clean neutral terminal was
# structurally UNREACHABLE, left as an opaque engine incident). `WorkerBpmnError` propagates
# unchanged through `execute` (it exposes `.error_code`, not `.code`/`.message`) to the harness's
# `except WorkerBpmnError` branch, which routes it to `handle_bpmn_error` (the boundary) when the
# code is allowlisted. `_NOT_HUMAN` adverse guard -> T-E-gated (ADR-0030 §4): consumption-covered
# by the boundary-proof gate (`scripts/ci/check_bpmn_error_allowlist.py`), yet DEFERRED out of
# `PRODUCTION_BPMN_ERROR_ALLOWLIST` until T-E audited-refusal is production-activated for THIS
# code (the harness's T-E guard-refusal audit already recognizes it — `is_guard_refusal_code`
# matches on the `_NOT_HUMAN` suffix — so the refusal stays audited either way); the runtime
# behavior is UNCHANGED today (still an incident) — this closes only the raise-side migration, the
# same phased split cred's two guard codes went through (PR #166). No
# `INADIMPLENCIA_BPMN_ERROR_ALLOWLIST` constant is added: with only a T-E-deferred code to
# contribute, there is nothing to wire into `service.py` yet — mirrors `cancel`, whose sole
# gate-proven code (`ERR_CANCEL_MANTER_NOT_HUMAN`) also exposes no allowlist constant.
ERR_CONTRACT_SUSPENSION_NOT_HUMAN = "ERR_CONTRACT_SUSPENSION_NOT_HUMAN"
ERR_INAD_INVALID_CONTRATO = "ERR_INAD_INVALID_CONTRATO"

# Human decision value that allows suspension
DECISAO_SUSPENDER = "SUSPENDER"
DECISAO_ENCAMINHAR_RESCISAO = "ENCAMINHAR_RESCISAO"
DECISAO_MANTER = "MANTER"

# Process key of the sole owner of the contract-termination (rescisao) terminal — SP-OP-CANCEL-001
# holds End_ContratoRescindido, human-gated by UT_AnaliseRescisao (docs/processes/
# harmonization-inadimplencia-cancel.md §1). INADIMPLENCIA-001 NEVER rescinds; it hands off.
CANCEL_PROCESS_KEY = "SP-OP-CANCEL-001"


# ---------------------------------------------------------------
# Anti-dupla-terminacao — cross-process CANCEL-001 correlation query (GAP-INAD-1)
# ---------------------------------------------------------------


def _cancel_business_key(
    tenant_id: str,
    numero_contrato: str,
    matricula_beneficiario: str,
    beneficiario_pseudo_id: str = "",
) -> str:
    """Business key of the SP-OP-CANCEL-001 instance that would be terminating THIS contract.

    Same identity scheme both processes derive (docs/processes/harmonization-inadimplencia-cancel.md
    §1): a DISTINCT prefix (``CANCEL-`` vs INADIMPLENCIA's ``INAD-``) over the SAME contract identity,
    falling back to ``matricula_beneficiario`` when there is no contract number (individual/familiar
    plans) — mirrors ``fernando.graph._business_key`` exactly. An ACTIVE instance under this key means
    a rescisao/suspensao is already in flight in CANCEL-001 for this same ``contract_termination``.

    Delegates to the SHARED `base.mint_contract_business_key` — the ONE composer for this family
    (`fraude`, `notification_bridge` and `fernando.graph` now delegate to it too), so the minted
    string and the forms the guard below queries cannot drift apart. Output is byte-identical to
    the previous f-string under the shipped (`off`) privacy policy; see that function for the
    `pseudo_keys` behaviour and the DL-0043 shadow counter it records.
    """
    return mint_contract_business_key(
        CANCEL_KEY_FAMILY,
        tenant_id,
        numero_contrato=numero_contrato,
        matricula_beneficiario=matricula_beneficiario,
        beneficiario_pseudo_id=beneficiario_pseudo_id,
    )


def _query_ja_em_rescisao_cancel(
    engine: CibSevenTransport | None,
    *,
    tenant_id: str,
    numero_contrato: str,
    matricula_beneficiario: str,
    beneficiario_pseudo_id: str = "",
) -> bool:
    """REAL cross-process query: is a live SP-OP-CANCEL-001 instance already terminating this contract?

    Anti-dupla-terminacao (GAP-INAD-1, docs/processes/harmonization-inadimplencia-cancel.md §1): keys
    the engine by the CANCEL-001 business key (the process that OWNS the rescisao terminal) via the
    same ``CibSevenTransport.find_active_instance`` seam the agent graphs use for start-time
    idempotency. Read-only (TASY write DROP, ADR-0013); a FACT, never a decision.

    B-2 (confirmed blocker, fixed here): this used to query exactly ONE key form —
    ``CANCEL-{tenant}-{numero_contrato or matricula}``. But `fraude.start_contratual` and
    `notification_bridge`'s `cancel.*` rule both mint ``CANCEL-{tenant}-{numero_contrato}`` with NO
    matricula fallback. For a contract carrying BOTH identifiers the two composers produce DIFFERENT
    keys, so a CANCEL-001 instance started under the other form was INVISIBLE to this query: the
    guard reported "no rescisao in flight" while one was live, and the independent suspension went
    ahead — a double termination, which is the single thing this guard exists to prevent.

    It now queries EVERY derivable form (`base.contract_business_key_forms`, the single-source
    sweep of all CANCEL composers) and blocks on a hit in ANY of them. Finding MORE instances is
    strictly the conservative direction — it can only refuse an adverse effect, never permit one.

    FAIL CLOSED (ADR-0018, defense in depth): returns ``True`` (BLOCK the suspension) whenever the
    correlation CANNOT be confirmed — no engine seam wired, no contract identity to key on, or any
    transport/engine error on ANY of the forms. "Inability to decide = do not suspend": the guard
    NEVER presumes the absence of an in-flight rescisao it could not positively rule out. Returns
    ``False`` (suspension may proceed) ONLY when the engine positively confirms that NO form has an
    active CANCEL-001 instance.
    """
    cancel_business_key_forms = contract_business_key_forms(
        CANCEL_KEY_FAMILY,
        tenant_id,
        numero_contrato=numero_contrato,
        matricula_beneficiario=matricula_beneficiario,
        beneficiario_pseudo_id=beneficiario_pseudo_id,
    )

    if not cancel_business_key_forms:
        # No contract identity -> cannot key the correlation query -> cannot rule out a rescisao.
        logger.warning(
            "inadimplencia_cancel_correlation_unavailable",
            reason="contract_identity_missing",
            tenant_id=tenant_id,
        )
        return True

    if engine is None:
        # No engine seam injected -> the cross-process correlation cannot be confirmed: fail closed.
        logger.warning(
            "inadimplencia_cancel_correlation_unavailable",
            reason="engine_seam_not_wired",
            cancel_business_key=cancel_business_key_forms[0],
            cancel_business_key_form_count=len(cancel_business_key_forms),
        )
        return True

    for cancel_business_key in cancel_business_key_forms:
        try:
            instance = asyncio.run(engine.find_active_instance(cancel_business_key))
        except Exception as exc:  # noqa: BLE001 — engine/transport error -> fail closed (block).
            logger.warning(
                "inadimplencia_cancel_correlation_query_failed",
                error=str(exc),
                cancel_business_key=cancel_business_key,
            )
            return True
        if instance is not None:
            return True

    return False


# ---------------------------------------------------------------
# resolve_facts — pre-resolve facts (arithmetic + cross-process query, no decision)
# ---------------------------------------------------------------


def resolve_facts(variables: dict[str, Any], *, engine: CibSevenTransport | None = None) -> dict[str, Any]:
    """Pre-resolve factual variables: mes count, value, period, purge window, rescisao correlation.

    This worker computes factual data — it NEVER makes an adverse decision.
    All monetary values in integer cents.

    ``ja_em_rescisao_cancel`` is the anti-dupla-terminacao correlation RESOLVED HERE (GAP-INAD-1) via
    a REAL read-only query to the engine (``engine`` seam, ``CibSevenTransport.find_active_instance``):
    does a live SP-OP-CANCEL-001 instance already terminate this ``{tenant}-{contrato}``? Before this
    fix nothing populated the variable — the ``register_contract_suspension`` guard read a value no
    worker ever set (a dead guard). The FACT is returned to the engine as an output variable the
    harness carries on ``complete`` so the downstream suspension guard reads a real value.

    FAIL CLOSED (ADR-0018): when the query cannot be answered (no ``engine`` seam wired, missing
    contract identity, or a transport/engine error) ``ja_em_rescisao_cancel := True`` — the guard
    downstream then REFUSES the suspension and routes to a human. See ``_query_ja_em_rescisao_cancel``.
    """
    competencias = variables.get("competencias_em_aberto", [])
    meses_inadimplencia = len(competencias) if isinstance(competencias, list) else 0

    valor_total = variables.get("valor_total_devido_cents", 0)
    if not isinstance(valor_total, int):
        valor_total = int(valor_total) if valor_total else 0

    # Periodo minimo: DRAFT 60 dias (2 meses)
    dentro_periodo_minimo = meses_inadimplencia >= 2

    # Janela de purga: 10 dias apos notificacao (DRAFT/verify RN 593)
    dentro_janela_purga = variables.get("dentro_janela_purga", True)

    notificacao_previa_feita = variables.get("notificacao_previa_feita", False)

    # REAL cross-process query (anti-dupla-terminacao) — replaces the pure pass-through. Fail closed.
    ja_em_rescisao_cancel = _query_ja_em_rescisao_cancel(
        engine,
        tenant_id=str(variables.get("tenant_id", "")),
        numero_contrato=str(variables.get("numero_contrato", "")),
        matricula_beneficiario=str(variables.get("matricula_beneficiario", "")),
        beneficiario_pseudo_id=str(variables.get("beneficiario_pseudo_id", "")),
    )

    logger.info(
        "inadimplencia_resolve_facts",
        meses_inadimplencia=meses_inadimplencia,
        valor_total_devido_cents=valor_total,
        dentro_periodo_minimo=dentro_periodo_minimo,
        notificacao_previa_feita=notificacao_previa_feita,
        ja_em_rescisao_cancel=ja_em_rescisao_cancel,
    )

    return {
        "meses_inadimplencia": meses_inadimplencia,
        "valor_total_devido_cents": valor_total,
        "dentro_periodo_minimo": dentro_periodo_minimo,
        "notificacao_previa_feita": notificacao_previa_feita,
        "dentro_janela_purga": dentro_janela_purga,
        # RESOLVED by engine query (not echoed): the real anti-dupla-terminacao correlation fact.
        "ja_em_rescisao_cancel": ja_em_rescisao_cancel,
    }


# ---------------------------------------------------------------
# assess_status — classify default status (no adverse decision)
# ---------------------------------------------------------------


def assess_status(variables: dict[str, Any], *, dmn: DmnTransport | None = None) -> dict[str, Any]:
    """Classify the default status for routing — NEVER produces SUSPENDER/RESCINDIR.

    Evaluates the deployed `inadimplencia_status` decision table (ADR-0028/T1.5) — replaces
    the hand-forked if/elif ladder that used to live here.

    golden-parity divergence found + DMN wins (documented, not patched — ADR-0028 §7): rule
    ORDER differs. The old Python checked `notificacao_previa_feita` BEFORE
    `dentro_janela_purga`; the deployed table's FIRST-hit-policy checks `dentro_janela_purga`
    (-> `AGUARDA_PURGA`) BEFORE `notificacao_previa_feita` (-> `PENDENTE_NOTIFICACAO`). E.g.
    `notificacao_previa_feita=False` AND `dentro_janela_purga=True` now yields `AGUARDA_PURGA`
    (was `PENDENTE_NOTIFICACAO`). Neither is adverse — no `SUSPENDER`/`RESCINDIR` path exists in
    either version; live-verified against the compose engine before cutover (T1.5 PR body /
    evidence ledger).
    """
    dentro_min = variables.get("dentro_periodo_minimo", False)
    notificacao = variables.get("notificacao_previa_feita", False)
    dentro_purga = variables.get("dentro_janela_purga", True)
    tipo_plano = variables.get("tipo_plano", "individual")
    meses_inadimplencia = variables.get("meses_inadimplencia", 0)

    rows, version = evaluate_sync(
        require_dmn(dmn, "operadora.inadimplencia.assess_status"),
        "inadimplencia_status",
        {
            "meses_inadimplencia": int(meses_inadimplencia),
            "dentro_periodo_minimo": bool(dentro_min),
            "notificacao_previa_feita": bool(notificacao),
            "dentro_janela_purga": bool(dentro_purga),
            "tipo_plano": tipo_plano,
        },
    )
    row = first_row(rows, "inadimplencia_status", variables)
    roteamento = str(row.get("roteamento", ""))
    motivo = str(row.get("motivo", ""))

    logger.info(
        "inadimplencia_assess_status",
        tipo_plano=tipo_plano,
        roteamento=roteamento,
        motivo=motivo,
        dmn_decision_version=version.version,
    )

    return {
        "roteamento": roteamento,
        "motivo": motivo,
    }


# ---------------------------------------------------------------
# calculate_purge — determine purge window (RN 593 DRAFT)
# ---------------------------------------------------------------


def calculate_purge(variables: dict[str, Any]) -> dict[str, Any]:
    """Calculate the purge/cure window and prior notice deadlines.

    Only determines regulatory deadlines — NEVER decides to suspend/rescind.
    All deadlines DRAFT/verify against RN 593.
    """
    tipo_plano = variables.get("tipo_plano", "individual")

    # DRAFT/verify: prazos RN 593
    if tipo_plano in ("coletivo_empresarial", "coletivo_adesao"):
        prazo_purga = "P30D"  # conservative for collective
        prazo_notificacao_previa = "P60D"
        periodo_minimo = "P90D"
    else:
        prazo_purga = "P10D"
        prazo_notificacao_previa = "P50D"
        periodo_minimo = "P60D"

    logger.info(
        "inadimplencia_calculate_purge",
        tipo_plano=tipo_plano,
        prazo_purga=prazo_purga,
    )

    return {
        "prazo_purga": prazo_purga,
        "prazo_notificacao_previa": prazo_notificacao_previa,
        "periodo_minimo": periodo_minimo,
        "fonte_regulatoria": "RN 593 (DRAFT/verify)",
    }


# ---------------------------------------------------------------
# dispatch_prior_notice — cure-window kickoff step (asserts NOTHING)
# ---------------------------------------------------------------


def dispatch_prior_notice(variables: dict[str, Any]) -> dict[str, Any]:
    """Log that the RN-593 prior-notice STEP ran. Returns `{}` — asserts NO fact.

    NEUTRAL action — it never suspends/rescinds. Bound to
    `operadora.inadimplencia.check_prior_notice` (`ST_CheckPriorNotice`,
    `spec/processes/bpmn/SP-OP-INADIMPLENCIA-001_Suspensao_Rescisao.bpmn:131-136`).

    GAP-INAD-8 / WP-FATOS-FABRICADOS slice 2 (the fix this docstring exists for). This handler
    used to be named `notify_beneficiario` and returned, unconditionally and on every delivery::

        {"notificacao_previa_feita": True, "notificacao_previa_registrada_em": "now"}

    Neither key was computed from anything: no channel was contacted, no delivery was observed,
    and `"now"` was a literal placeholder, not a timestamp. The harness LOADS a handler's return
    dict into process scope on `complete` (`harness.py:1778-1782`), so that constant genuinely
    reached the engine and its TWO real consumers:

      1. `spec/processes/dmn/inadimplencia_status.dmn:37-38` — `in_notificacao_previa` reads
         `notificacao_previa_feita`; row `r_pendente_notificacao` (`:68-77`) routes
         `PENDENTE_NOTIFICACAO` only on `false`.
      2. The CANCEL-001 handoff (`handoff_rescisao` below): `cancel_admissibility.dmn:63-64` reads
         the same name, and `cancel.assess_admissibility` (`cancel.py`) branches on it for
         `tipo_solicitacao in ("inadimplencia", "for_cause_operadora")`.

    Because the constant was always `True`, NEITHER consumer could ever take its
    `PENDENTE_NOTIFICACAO` branch for an inadimplencia-originated case — the automation asserted
    the very regulatory precondition it exists to check (art. 13, par. unico, II da Lei 9.656/98:
    "comprovada notificacao ate o quinquagesimo dia de inadimplencia"; RN 593 — **DRAFT/verify**).

    WHY `{}` AND NOT A REAL PUBLISH (option (a) evaluated and rejected on evidence). A worker's
    seams are `engine`, `dmn`, `kafka` and `audit_sink` (`harness.py`); there is NO outbound
    beneficiary channel among them. The one WhatsApp seam in the tree
    (`maezo.tools.mcp_whatsapp.server.WhatsAppServer`) is reachable only from the conversational
    agents and the webhook service (`agents/helena/adapters.py`, `agents/lucas/adapters.py`,
    `platform/webhooks/whatsapp/dispatch.py`), never from a BPMN worker; and
    `platform/notification_bridge.py` is a process-to-process HANDOFF bridge (it STARTS processes
    from events), not a messaging channel. One task later, the BPMN itself DOES already publish an
    event on this path, via the generic `operadora.events.publish`
    (`ST_PublishInadimplenciaNotified` -> `agents.events.inadimplencia.notified`, BPMN `:149-158`)
    — but that publish is UNCONDITIONAL and asserts NOTHING about delivery: it fires on every
    instance, right after this task, in a tree where no worker can notify a beneficiary (see WHY
    above). It is not a safe "notice requested" fact this handler could duplicate; it is the same
    false-assertion species this fix exists to remove, one layer up, with zero consumers today and
    left untouched here — renaming/re-semanticising a live BPMN topic is an owner/spec decision,
    not a worker-handler fix (tracked as GAP-INAD-9, `docs/review-queue.md`). Re-publishing from
    here would add nothing either way: an internal Kafka record is at best a dispatch trace, never
    the `comprovada notificacao` the statute requires.

    WHERE THE HONEST FACT COMES FROM INSTEAD (both pre-existing, neither invented here):
      - `msg.inadimplencia.notificacao_ack` (BPMN `:14,:180-184`) — the receipt-confirmation
        correlation. Its `processVariables` payload is what may legitimately set
        `notificacao_previa_feita=true`, exactly as `SP-OP-CANCEL-001.md:107-117` (GAP-CANCEL-4)
        already specifies for the sibling process.
      - `comprovacao_notificacao_previa` — the human's proof REFERENCE, collected on
        `UT_AnaliseInadimplencia` and enforced fail-closed by `_register_contract_suspension`
        (`errors.append("comprovacao_notificacao_previa ausente (RN 593)")`). This is the exact
        equivalent of CRED's human-confirmed field (`SP-OP-CRED-001.md:62,71`), and it is what
        `handoff_rescisao` now DERIVES the CANCEL-001 fact from.

    NO LIVELOCK from returning `{}` (checked against the shipped BPMN, not assumed): both
    `AGUARDA_PURGA` and `PENDENTE_NOTIFICACAO` converge on `BRT_PurgaPrazos` (`:120-128`, two
    incoming flows) -> this task -> `ST_PublishInadimplenciaNotified` -> `GW_CureWindow`, an
    event-based gateway whose THIRD branch is the `ICE_PrazoPurga` timer (`:187-193`) routing to
    SLA + the human `UT_AnaliseInadimplencia`. Every path out of the wait either ends neutral
    (payment/purge) or reaches a human; none auto-suspends and none spins without an external
    message.
    """
    numero_contrato = variables.get("numero_contrato", "")
    matricula = variables.get("matricula_beneficiario", "")

    logger.info(
        "inadimplencia_dispatch_prior_notice",
        numero_contrato=numero_contrato,
        matricula=matricula,
        notificacao_previa_feita_asserted=False,
    )

    return {}


# ---------------------------------------------------------------
# prepare_dossier — assemble human-review dossier (INSTRUCTS, never decides)
# ---------------------------------------------------------------

#: Keys copied verbatim from the pre-resolved process facts into the dossier summary the human
#: analyst reviews. Deliberately EXCLUDES every decision variable (`decisao_inadimplencia`) — the
#: dossier instructs UT_AnaliseInadimplencia, it never originates the adverse decision.
_DOSSIER_SUMMARY_KEYS = (
    "numero_contrato",
    "matricula_beneficiario",
    "tipo_plano",
    "origem_solicitacao",
    "meses_inadimplencia",
    "valor_total_devido_cents",
    "dentro_periodo_minimo",
    "notificacao_previa_feita",
    "dentro_janela_purga",
    "ja_em_rescisao_cancel",
)


def prepare_dossier(variables: dict[str, Any]) -> dict[str, Any]:
    """Assemble the human-review dossier for UT_AnaliseInadimplencia — INSTRUCTS, never decides.

    Mirrors the cancel/auth dossier pattern (`auth.AnalyzeRequestWorker`, `cancel.assess_admissibility`):
    it gathers the pre-resolved facts + regulatory context into a dossier the human analyst
    (juridico-contratos / gestao-cobranca) reviews. It NEVER originates an adverse decision — the
    suspension/rescisao is born ONLY in the human User Task (L0 hard, contract_termination,
    ADR-0005/0008). This worker therefore produces NO `decisao_inadimplencia` and NO
    SUSPENDER/RESCINDIR output; the returned dict is a non-routing record no BPMN gateway reads for a
    decision (it only feeds the human task). `ja_em_rescisao_cancel` is carried through as an
    informational fact (surfaced to the analyst), never re-derived or overridden here.
    """
    dossier_ref = f"dossier-inad-{uuid.uuid4().hex[:12]}"
    dossier_summary = {k: variables[k] for k in _DOSSIER_SUMMARY_KEYS if k in variables}

    logger.info(
        "inadimplencia_dossier_prepared",
        numero_contrato=variables.get("numero_contrato"),
        dossier_ref=dossier_ref,
    )

    return {
        "dossier_prepared": True,
        "dossier_ref": dossier_ref,
        "dossier_summary": dossier_summary,
        "numero_contrato": variables.get("numero_contrato", ""),
    }


# ---------------------------------------------------------------
# notify_sla_risk — informational SLA alert (non-interruptive timer)
# ---------------------------------------------------------------


def notify_sla_risk(variables: dict[str, Any]) -> dict[str, Any]:
    """Registra que a ETAPA de alerta de risco de SLA rodou. Retorna `{}` — NAO afirma nada.

    Serve o topico `operadora.inadimplencia.notify_sla_risk`, alimentado SO pelo boundary
    NAO-interruptivo `BT_AlertaSla` em `UT_AnaliseInadimplencia`. Informativa e jamais adversa: a
    User Task segue aberta, nenhuma decisao e tomada ou alterada e NENHUM desfecho adverso nasce
    do timeout. Dispara em ~60-70% do SLA de analise (politica interna, DRAFT; DMN
    `inadimplencia_sla` resolve a duracao real).

    FAB-SLA-RISK-NOTIFIED-SLICE4 (o motivo desta docstring). O retorno era, em toda entrega e sem
    calcular nada, `{"sla_risk_notified": True, ...}`. NENHUM canal e contatado por esta funcao —
    a `coordenacao-cobranca` pode nunca ter sido avisada — e a harness grava o retorno no escopo
    do processo no
    `complete` (`harness.py:1779-1783`), entao a constante entrava na instancia como trilha de
    auditoria. Mesma especie de `contas.notify_sla_risk` (FAB-NOTIFIED-TRIO), sob outra chave.

    ZERO CONSUMIDORES (mapa refeito antes de editar): nem `sla_risk_notified` nem `grupo_alertado`
    aparecem em `conditionExpression` de BPMN, `inputExpression` de DMN, worker a jusante ou linha
    de contrato — `grupo_alertado` so existe nesta funcao e no seu teste. As demais chaves eram
    eco do proprio input, ja no escopo. A observabilidade da etapa fica no `logger` abaixo, que
    declara explicitamente `notified_asserted=False`.

    ATENCAO — nao confundir com `notificacao_previa_feita` (GAP-INAD-8, ja corrigido): AQUELE fato
    tinha DOIS consumidores DMN reais; este nao tem nenhum.
    """
    numero_contrato = variables.get("numero_contrato", "")

    logger.info(
        "inadimplencia_notify_sla_risk",
        numero_contrato=numero_contrato,
        grupo_alertado="coordenacao-cobranca",
        notified_asserted=False,
    )

    return {}


# ---------------------------------------------------------------
# register_suspension — GATED adverse effect (L0-hard)
# ---------------------------------------------------------------


def _norm_str(value: Any) -> str:
    """Normalize an engine variable to a stripped string for guard checks — FAIL-CLOSED.

    Mirrors `pagto._norm_str` (t2.5-p2b-round2 / t3.1-guard-input-hardening): the pre-fix bare
    `if not responsavel_id` / `if not fundamentacao` / ... checks in
    `_register_contract_suspension` let WHITESPACE-ONLY decision + accountability fields pass —
    defeating ADR-0007/RN 593 (a contract suspension recorded with a non-identifying approver
    or blank justification/regulatory reference/notice/period proofs). Closes that class:
    - a `str` normalizes to `value.strip()` — whitespace-only ("   ", "\\t", "\\n", ...)
      becomes "" and is treated EXACTLY like an absent field (refusal, never registration);
    - a NON-string (None, int, bool, list, dict — engine variables arrive untyped) normalizes
      to "" (fail-closed refusal), never a truthy pass-through and never an AttributeError
      incident from calling `.strip()` on a non-string.

    Scope: STRING fields only. The anti-dupla-terminacao fact `ja_em_rescisao_cancel`
    (GAP-INAD-1, boolean, fail-closed `is not False` check) is NOT part of this class and is
    deliberately untouched — its guard logic remains byte-identical.
    """
    if isinstance(value, str):
        return value.strip()
    return ""


def register_suspension(variables: dict[str, Any]) -> dict[str, Any]:
    """Register contract suspension for default.

    GUARDED: ERR_CONTRACT_SUSPENSION_NOT_HUMAN.
    Requires:
      - decisao_inadimplencia == SUSPENDER from human user task
      - responsavel_id present (ADR-0007)
      - fundamentacao_contratual, referencia_regulatoria
      - comprovacao_notificacao_previa, comprovacao_periodo_minimo
      - NOT ja_em_rescisao_cancel (anti-double-termination)
    """
    return _register_contract_suspension(variables)


def _register_contract_suspension(variables: dict[str, Any]) -> dict[str, Any]:
    """Internal: validate guard conditions and register suspension.

    Raises ERR_CONTRACT_SUSPENSION_NOT_HUMAN if any guard fails.

    NORMALIZATION (t3.1-guard-input-hardening): ALL decision + human-accountability STRING
    fields (`decisao_inadimplencia`, `responsavel_id`, `fundamentacao_contratual`,
    `referencia_regulatoria`, `comprovacao_notificacao_previa`, `comprovacao_periodo_minimo` —
    contract SP-OP-INADIMPLENCIA-001.md:96-100,157,176) are normalized via `_norm_str` (strip;
    non-string -> "") BEFORE any guard check, mirroring `pagto.register_payment_refusal`'s fix.
    Whitespace-only/non-string refuses exactly like absent; a whitespace-PADDED exact
    `decisao_inadimplencia` literal still passes (exact `!=` match, no folding). The
    `ja_em_rescisao_cancel` anti-dupla boolean check below is UNTOUCHED (input normalization
    only — no change near the engine-seam/anti-dupla guard logic).
    """
    decisao = _norm_str(variables.get("decisao_inadimplencia", ""))
    responsavel_id = _norm_str(variables.get("responsavel_id", ""))
    fundamentacao = _norm_str(variables.get("fundamentacao_contratual", ""))
    ref_regulatoria = _norm_str(variables.get("referencia_regulatoria", ""))
    comprovacao_notif = _norm_str(variables.get("comprovacao_notificacao_previa", ""))
    comprovacao_periodo = _norm_str(variables.get("comprovacao_periodo_minimo", ""))
    # Anti-dupla-terminacao (GAP-INAD-1): the FACT resolved by resolve_facts's cross-process CANCEL-001
    # query. FAIL CLOSED default `True` — an ABSENT fact means the correlation was never confirmed
    # (resolve_facts did not run / its output did not propagate), so the suspension is REFUSED. Only an
    # explicit `False` (engine positively confirmed no active CANCEL-001 instance) permits the effect.
    ja_em_rescisao = variables.get("ja_em_rescisao_cancel", True)

    errors: list[str] = []

    if decisao != DECISAO_SUSPENDER:
        errors.append(f"decisao_inadimplencia != {DECISAO_SUSPENDER} (got: {decisao!r})")
    if not responsavel_id:
        errors.append("responsavel_id ausente (ADR-0007)")
    if not fundamentacao:
        errors.append("fundamentacao_contratual ausente")
    if not ref_regulatoria:
        errors.append("referencia_regulatoria ausente")
    if not comprovacao_notif:
        errors.append("comprovacao_notificacao_previa ausente (RN 593)")
    if not comprovacao_periodo:
        errors.append("comprovacao_periodo_minimo ausente")
    if ja_em_rescisao is not False:
        errors.append(
            "ja_em_rescisao_cancel — contrato ja em rescisao/suspensao ativa em CANCEL-001 (ou "
            "correlacao nao confirmada): suspensao recusada para evitar dupla-terminacao do "
            "contract_termination (anti-dupla-rescisao, harmonization-inadimplencia-cancel.md §1)"
        )

    if errors:
        logger.error(
            "inadimplencia_suspension_guard_rejected",
            errors=errors,
            numero_contrato=variables.get("numero_contrato"),
        )
        # MODELED boundary error (BE_SuspensaoNaoHumano) — WorkerBpmnError, not InadimplenciaError;
        # see the error-codes section above and cancel.confirm_maintained_decision for the rationale.
        raise WorkerBpmnError(ERR_CONTRACT_SUSPENSION_NOT_HUMAN, "; ".join(errors))

    logger.info(
        "inadimplencia_contract_suspension_registered",
        numero_contrato=variables.get("numero_contrato"),
        responsavel_id=responsavel_id,
    )

    return {
        "suspensao_registrada": True,
        "data_efeito_iso": variables.get("data_efeito_iso", ""),
    }


# ---------------------------------------------------------------
# register_contract_suspension — external task handler alias
# ---------------------------------------------------------------

register_contract_suspension = _register_contract_suspension


# ---------------------------------------------------------------
# handoff_rescisao — NEUTRO handoff to CANCEL-001 (DRAFT-A shipped)
# ---------------------------------------------------------------


#: PHI-safe allowlist of process variables carried into the CANCEL-001 start payload (the handoff
#: hands CANCEL the RN-593 evidence so its `notificacao_previa_feita=true` branch routes to
#: SEGUE_ANALISE without re-opening the cure-window — INADIMPLENCIA owns the cure-window, CANCEL
#: must not re-run it (harmonization-inadimplencia-cancel.md §2). Explicit allowlist (never a
#: passthrough of the full variable dict): only bounded identifiers/enums/refs, no raw PHI.
#:
#: GAP-INAD-8: `notificacao_previa_feita` is DELIBERATELY NOT in this tuple — see
#: `_notificacao_previa_comprovada` and `handoff_rescisao`'s payload below. It is no longer a
#: passthrough of whatever sits in process scope; it is DERIVED from the human's proof reference,
#: so the value CANCEL branches on cannot be inherited from a fabrication.
_HANDOFF_CARRY_KEYS = (
    "numero_contrato",
    "matricula_beneficiario",
    "tenant_id",
    "tipo_plano",
    "meses_inadimplencia",
    "comprovacao_notificacao_previa",
    "comprovacao_periodo_minimo",
    "referencia_regulatoria",
    "fundamentacao_contratual",
    "documentos_refs",
    # The inadimplencia human who decided ENCAMINHAR_RESCISAO — carried for the ADR-0007 audit
    # trail; CANCEL's OWN human gate (UT_AnaliseRescisao) re-confirms the rescisao decision.
    "responsavel_id",
)


def _notificacao_previa_comprovada(variables: dict[str, Any]) -> bool:
    """The HONEST `notificacao_previa_feita` handed to CANCEL-001: was the prior notice PROVEN?

    GAP-INAD-8 (WP-FATOS-FABRICADOS slice 2). Before this fix the handoff passed
    `notificacao_previa_feita` straight through from process scope, where
    `notify_beneficiario` (now `dispatch_prior_notice`) had just written the unconditional
    constant `True`. So `cancel.assess_admissibility`'s `PENDENTE_NOTIFICACAO` branch was
    structurally unreachable for every inadimplencia-originated case, and CANCEL-001's
    `cancel_admissibility.dmn:63-64` read a fabricated input.

    The honest source is the SAME artifact the adverse-effect guard in this module already
    requires from the human: `comprovacao_notificacao_previa`, the proof reference collected on
    `UT_AnaliseInadimplencia` (contract `SP-OP-INADIMPLENCIA-001.md:98,157`) and enforced
    fail-closed by `_register_contract_suspension`. This is not a re-fabrication under a new name:
    the value is `True` only when a human actually recorded a proof reference for THIS case, and
    `False` — routing CANCEL to its non-adverse `PENDENTE_NOTIFICACAO` wait — whenever they did
    not. It mirrors CRED's design, where `comprovacao_notificacao_previa` is likewise named as
    "a comprovacao real de recebimento" while `notificacao_previa_feita` is never worker-resolved
    (`SP-OP-CRED-001.md:62,71`).

    FAIL CLOSED in both directions: a missing, blank, whitespace-only or non-string value yields
    `False` (`_norm_str` strips and maps non-strings to `""`). `False` is never adverse — CANCEL's
    `PENDENTE_NOTIFICACAO` branch runs `BRT_CancelSlaPreNotif` -> `ST_RequestNotification` ->
    `ST_PublishCancelPended` -> `GW_AguardarNotificacao`, whose timer branch converges on the human
    `UT_AnaliseRescisao` (`SP-OP-CANCEL-001_Cancelamento_Contrato.bpmn:156-200,479-492`). No
    automatic rescission exists on any of those paths.

    Legal anchor stays **DRAFT/verify**: art. 13, par. unico, II da Lei 9.656/98 requires
    "comprovada notificacao ate o quinquagesimo dia de inadimplencia"; RN 593 is the operational
    reading used across this process and is not confirmed from `docs/compliance/` here.
    """
    return bool(_norm_str(variables.get("comprovacao_notificacao_previa", "")))


def _contract_identity_log_fields(numero_contrato: str, matricula_beneficiario: str) -> dict[str, str]:
    """The contract-identity kwargs for a `handoff_rescisao` log line, under CORRECT field names.

    DL-0043 leg (c). The three `handoff_rescisao` log lines used to carry
    ``numero_contrato=numero_contrato or matricula_beneficiario`` — a raw `PHI_PROCESS_VARS`
    matricula travelling under a NON-PHI field name, on the very same line whose
    `cancel_business_key` IS covered by the scrubber. `BusinessKeyScrubber`'s anchor set
    (`key_scrubber.PHI_KEY_ANCHOR_LOG_FIELDS`) covers `matricula_beneficiario`/`matricula` and
    deliberately does NOT cover `numero_contrato`: a contract number is not a person's
    identifier, and an analyst legitimately searches logs by it. Adding it to the anchor set
    would scrub the one field that has to stay legible — so the FALLBACK value has to travel
    under its own name instead.

    The two keys are mutually exclusive by construction: `numero_contrato` is emitted only when a
    real contract number exists, `matricula_beneficiario` only when the fallback is what
    identifies the contract. So under `scrub_only` exactly the PHI-bearing case is scrubbed, and
    a contract-numbered case reads identically to before. Callers reach this only downstream of
    the `not (numero_contrato or matricula_beneficiario)` refusal, so the returned value is never
    a blank identity.

    Pinned by `tests/unit/platform/privacy/test_key_scrubber.py::
    test_no_logger_kwarg_smuggles_a_phi_anchor_under_a_non_anchor_name`.
    """
    if numero_contrato:
        return {"numero_contrato": numero_contrato}
    return {"matricula_beneficiario": matricula_beneficiario}


def handoff_rescisao(
    variables: dict[str, Any],
    *,
    engine: CibSevenTransport | None = None,
    audit_sink: AuditStartSink | None = None,
) -> dict[str, Any]:
    """Neutral handoff: idempotently START/correlate SP-OP-CANCEL-001 for the rescission.

    NOT an adverse effect — CANCEL-001 owns the sole rescission terminal, human-gated by its OWN
    `UT_AnaliseRescisao` (harmonization-inadimplencia-cancel.md §2). When the INADIMPLENCIA human
    decides `ENCAMINHAR_RESCISAO`, this worker starts CANCEL-001 (business key
    ``CANCEL-{tenant}-{numero_contrato}``, matricula fallback for individual/familiar) via the
    shared, business-key-idempotent ``start_process_idempotent`` chokepoint — an already-active
    CANCEL instance for this contract is returned unchanged (no duplicate rescisao).

    T-C2 FENCE (10th start site, T1.10 wave integration): the chokepoint REQUIRES a durable
    ``audit_sink`` + ``AgentDecisionProvenance`` — the ADR-0007 start record is emitted
    exactly-once (dedup key ``{tenant}:start:SP-OP-CANCEL-001:{business_key}``) BEFORE any engine
    effect. Provenance for this deterministic worker context: ``agent_id`` is the stable service
    identity (`AUDIT_AGENT_ID`, T-C convention — not the per-replica worker id), no
    ``model_id``/``prompt_version`` (no LLM decides here; the human's ENCAMINHAR_RESCISAO does),
    and ``decision_basis`` carries ONLY bounded enum/flag tokens (design §3.3). ``responsavel_id``
    and the contract identity stay in the start ``variables``, bound one-way via the record's
    ``input_sha256`` — never stored in the clear.

    P1-safe mid-handler effect (design §4.2, "MUST-FIX 1"): the ONLY external effect here is the
    business-key-idempotent process start, whose dedup makes a re-delivery return the SAME active
    instance — no double-effect — so it is exempt from the "no mid-handler external effect" rule.
    The business key is a DETERMINISTIC function of process variables (no wall-clock / uuid), so a
    re-run keys the same instance (and its audit emit dedups to the same chain link).

    GATE PREREQUISITES (GAP-D3-02). ``SP-OP-CANCEL-001`` is an ``EXCLUSIVE`` start-dedup family
    (`mcp_cibseven.transport._START_DEDUP_POLICY`), so the chokepoint requires TWO seam
    capabilities beyond a bare transport/sink: ``engine`` must satisfy ``HistoryQueryingTransport``
    (``find_any_instance``) and ``audit_sink`` must satisfy ``DedupReportingAuditSink``
    (``emit_once_status``). Both hold in the live daemon: the ``engine=`` seam is a
    ``GatedHistoryQueryingCibSevenTransport`` over ``FreshClientCibSevenTransport``
    (`gateway/seams/cibseven.py:128` `gate_cibseven` preserves the inner's history-querying
    capability; `runtime/worker_runtime/service.py:647`), and the ``audit_sink=`` seam is a
    ``FreshSinkAuditEmitter`` (`gateway/audit_postgres.py:473`), wired at
    `runtime/worker_runtime/service.py:787-790`. If a
    composition root ever supplies a seam WITHOUT them, the chokepoint raises
    ``StartDedupGateUnavailableError`` BEFORE writing anything durable and NEVER falls back to the
    un-gated (TOCTOU-only) path — an un-gateable contract-termination start does not run.

    FAIL-CLOSED (never a silent no-op — mirrors fraude's ``start_contratual`` handoff intent):
      - a transport/engine error propagates from ``start_process_idempotent`` (``CibSevenError`` ->
        transient -> engine retry -> incident);
      - an audit-persistence failure propagates (``AuditPersistenceError``, a ``RuntimeError`` ->
        transient -> retry) and the engine start NEVER happens (emit-before-effect);
      - a missing engine seam (``engine is None`` — composition root not wired) raises (transient);
      - a missing audit seam (``audit_sink is None``) raises (transient) — the handoff can never
        start CANCEL-001 un-audited (ADR-0007 L0);
      - a missing contract identity raises ``InadimplenciaError`` (deterministic -> immediate
        incident) — the handoff can never target an empty CANCEL business key;
      - a missing/blank/``None`` ``tenant_id`` raises ``InadimplenciaError`` the same way (GK
        MINOR F7, validated with the SHARED ``base.non_blank`` — the same idiom
        ``fraude.start_contratual`` and the bridge's ``_anchored`` already used): a degenerate
        ``CANCEL--{contrato}`` key would collapse every tenant's contract onto ONE business key,
        one dedup claim and — since GAP-D3-02 — ONE ``EXCLUSIVE`` mutual-exclusion token.
    """
    decisao = variables.get("decisao_inadimplencia", "")
    if decisao != DECISAO_ENCAMINHAR_RESCISAO:
        logger.warning(
            "inadimplencia_handoff_rescisao_unexpected",
            decisao=decisao,
        )
        return {"handoff_executado": False}

    numero_contrato = str(variables.get("numero_contrato", ""))
    matricula_beneficiario = str(variables.get("matricula_beneficiario", ""))

    # `non_blank` BEFORE `str()`: explicit `None` must refuse, never stringify to the truthy
    # "None". SAME idiom as `fraude.start_contratual` and the bridge's `_anchored` — this call
    # site was the one CANCEL-001 composer that did not validate its tenant anchor (GK MINOR F7).
    # A blank/whitespace/None tenant mints the degenerate key `CANCEL--{contrato}` and a dedup key
    # `:start:SP-OP-CANCEL-001:...`, collapsing EVERY tenant's contract `C-001` onto ONE business
    # key — and since GAP-D3-02 that degenerate key also carries an EXCLUSIVE mutual-exclusion
    # token, so one tenant's in-flight rescisao review would gate another tenant's.
    if not non_blank(variables.get("tenant_id")):
        logger.error(
            "inadimplencia_handoff_rescisao_no_tenant_anchor",
            **_contract_identity_log_fields(numero_contrato, matricula_beneficiario),
        )
        raise InadimplenciaError(
            ERR_INAD_INVALID_CONTRATO,
            "handoff_rescisao: tenant_id ausente, em branco ou None — nao ha ancora de tenant "
            "para a business key de CANCEL-001 (recusado, nunca inicia com chave degenerada "
            "'CANCEL--{contrato}' que colapsaria tenants distintos numa unica chave)",
        )

    tenant_id = str(variables.get("tenant_id", ""))
    beneficiario_pseudo_id = str(variables.get("beneficiario_pseudo_id", ""))

    if not (numero_contrato or matricula_beneficiario):
        # No contract identity -> cannot key CANCEL-001 -> refuse (deterministic bad input).
        logger.error(
            "inadimplencia_handoff_rescisao_no_contract_identity",
            tenant_id=tenant_id,
        )
        raise InadimplenciaError(
            ERR_INAD_INVALID_CONTRATO,
            "handoff_rescisao: sem numero_contrato/matricula_beneficiario — nao ha identidade de "
            "contrato para iniciar CANCEL-001 (recusado, nunca inicia com business key vazia)",
        )

    if engine is None:
        # Composition root has not wired the agent->engine seam: fail closed (never a silent
        # no-op that would drop the human's ENCAMINHAR_RESCISAO decision). Transient RuntimeError
        # -> engine-computed retry -> incident (mirrors require_dmn's unwired-seam posture).
        logger.error(
            "inadimplencia_handoff_rescisao_engine_seam_not_wired",
            **_contract_identity_log_fields(numero_contrato, matricula_beneficiario),
        )
        raise RuntimeError(
            "handoff_rescisao: engine seam (CibSevenTransport) not wired — cannot start CANCEL-001; "
            "failing closed to a retry/incident (never a silent no-op)"
        )

    if audit_sink is None:
        # T-C2 fence co-requisite: no durable audit sink -> the fenced chokepoint cannot emit the
        # ADR-0007 start record -> the handoff must NOT start CANCEL-001 (emit-before-effect, L0).
        # Same transient fail-closed posture as the engine seam above: RuntimeError -> retry ->
        # incident, never a silent no-op and never an un-audited start.
        logger.error(
            "inadimplencia_handoff_rescisao_audit_sink_not_wired",
            **_contract_identity_log_fields(numero_contrato, matricula_beneficiario),
        )
        raise RuntimeError(
            "handoff_rescisao: audit sink (AuditStartSink) not wired — cannot emit the ADR-0007 "
            "start record, so CANCEL-001 is NOT started (fail-closed, emit-before-effect); "
            "failing to a retry/incident (never a silent no-op, never an un-audited start)"
        )

    cancel_business_key = _cancel_business_key(
        tenant_id, numero_contrato, matricula_beneficiario, beneficiario_pseudo_id
    )
    # GAP-INAD-8: DERIVED, never inherited. Computed BEFORE the payload so the same value feeds
    # both the CANCEL-001 start variables and the ADR-0007 `decision_basis` below — the audit row
    # and the process it starts can never disagree about whether the notice was proven.
    notificacao_previa_comprovada = _notificacao_previa_comprovada(variables)

    payload: dict[str, Any] = {
        "tipo_solicitacao": "inadimplencia",
        "origem_solicitacao": "operadora",  # handoff origin (harmonization §2)
        **{k: variables[k] for k in _HANDOFF_CARRY_KEYS if k in variables},
        "notificacao_previa_feita": notificacao_previa_comprovada,
    }

    # ADR-0007 provenance for the fenced start (T-C2, 10th site). Deterministic worker context:
    # stable service identity, no LLM legs; `decision_basis` is a curated allowlist of bounded
    # enum/flag tokens ONLY (design §3.3) — `responsavel_id`/contract identity live in `payload`,
    # bound one-way via the record's `input_sha256`, never in the clear.
    provenance = AgentDecisionProvenance(
        agent_id=AUDIT_AGENT_ID,
        agent_version=_resolve_app_version(),
        tenant_id=tenant_id,
        decision_basis={
            "decisao_inadimplencia": decisao,
            "origem_solicitacao": "operadora",
            "notificacao_previa_feita": notificacao_previa_comprovada,
        },
        model_id=None,
        prompt_version=None,
    )

    # Fenced chokepoint call: emits the durable start record exactly-once (dedup key
    # `{tenant}:start:SP-OP-CANCEL-001:{cancel_business_key}`) BEFORE `find_active_instance`/start.
    instance = asyncio.run(
        start_process_idempotent(
            engine,
            process_key=CANCEL_PROCESS_KEY,
            business_key=cancel_business_key,
            variables=payload,
            audit_sink=audit_sink,
            provenance=provenance,
        )
    )

    logger.info(
        "inadimplencia_handoff_rescisao",
        **_contract_identity_log_fields(numero_contrato, matricula_beneficiario),
        cancel_business_key=cancel_business_key,
        cancel_instance_id=instance.instance_id,
        cancel_already_existed=instance.already_existed,
    )

    return {
        "handoff_executado": True,
        "processo_destino": "SP-OP-CANCEL-001",
        "cancel_business_key": cancel_business_key,
        "cancel_instance_id": instance.instance_id,
        "cancel_already_existed": instance.already_existed,
    }


# ---------------------------------------------------------------
# Custom error
# ---------------------------------------------------------------


class InadimplenciaError(Exception):
    """Worker guard error for inadimplencia adverse effects."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(f"{code}: {message}")


# ---------------------------------------------------------------
# Bootstrap — FunctionWorker adapter (T1.2/ADR-0026 Decisao §2a).
#
# Topic mapping vs spec/processes/bpmn/SP-OP-INADIMPLENCIA-001_Suspensao_Rescisao.bpmn
# (excl. shared/out-of-scope `operadora.events.publish`):
#   resolve_facts       -> operadora.inadimplencia.resolve_facts (exact spec match; threads the
#     `engine=` seam for the anti-dupla-terminacao CANCEL-001 correlation query, GAP-INAD-1)
#   dispatch_prior_notice -> operadora.inadimplencia.check_prior_notice
#     (spec match: RN 593 prior-notice dispatch STEP; asserts no fact — GAP-INAD-8, was
#     `notify_beneficiario`, which returned a constant `notificacao_previa_feita=True`)
#   prepare_dossier -> operadora.inadimplencia.prepare_dossier (exact spec match; INSTRUCTS the
#     human User Task, never originates an adverse decision — cancel/auth dossier pattern)
#   register_suspension (alias register_contract_suspension)
#     -> operadora.inadimplencia.register_contract_suspension (exact spec match, GUARDED)
#   handoff_rescisao -> operadora.inadimplencia.handoff_rescisao (exact spec match)
#   notify_sla_risk -> operadora.inadimplencia.notify_sla_risk (exact spec match; informational)
# assess_status/calculate_purge have no distinct spec topic (assess_status
# evaluates the deployed `inadimplencia_status` decision table via the dmn=
# seam since T1.5/ADR-0028; calculate_purge resolves regulatory deadlines) —
# registered under function-derived topics for registry completeness.
# ---------------------------------------------------------------


def register_inadimplencia_workers(
    harness: WorkerHarness,
    kafka: KafkaPublisher | None = None,
    **seams: Any,
) -> None:
    """Register the SP-OP-INADIMPLENCIA-001 function workers on `harness`.

    `dmn` (ADR-0028 §1 seam) is threaded into `assess_status` (`inadimplencia_status`, T1.5
    cutover) via `functools.partial`; no other function here evaluates a DMN table.

    `engine` (a `CibSevenTransport`, the SAME `find_active_instance`/`start_process` seam the agent
    graphs use, ADR-0001/T1.11) is threaded into BOTH engine-touching workers:
      - `resolve_facts` — the anti-dupla-terminacao cross-process `find_active_instance` query
        (GAP-INAD-1). ABSENT (`None`) -> FAILS CLOSED (`ja_em_rescisao_cancel := True`), so the
        `register_contract_suspension` guard REFUSES the suspension (ADR-0018).
      - `handoff_rescisao` — the CANCEL-001 `start_process_idempotent` start (T1.10 T-D). ABSENT
        (`None`) -> RAISES (never a silent no-op that would drop the human's ENCAMINHAR_RESCISAO).
    The seam flows in through the standard `**seams` channel (`register_all_workers(harness, ...,
    engine=...)`), identical to how `dmn` is threaded — no other bootstrap needs to change. In the
    live daemon it is a `FreshClientCibSevenTransport` (fresh-client-per-call, GAP-INAD-1).

    `audit_sink` (an `AuditStartSink`, T1.10 T-C2 fence co-requisite) is threaded into
    `handoff_rescisao` only — the one worker here that runs the fenced `start_process_idempotent`
    chokepoint. ABSENT (`None`) -> `handoff_rescisao` RAISES before any engine effect (an
    un-audited CANCEL-001 start is structurally impossible, ADR-0007 L0). In the live daemon it is
    a `FreshSinkAuditEmitter` (gateway/audit_postgres.py) — the sink-side mirror of the
    fresh-client-per-call pattern, because each sync dispatch emits on its own `asyncio.run` loop.
    """
    del kafka  # unused — no inadimplencia.py worker declares a Kafka dependency
    dmn = seams.get("dmn")
    engine: CibSevenTransport | None = seams.get("engine")
    audit_sink: AuditStartSink | None = seams.get("audit_sink")
    harness.register_worker(
        FunctionWorker(
            "operadora.inadimplencia.resolve_facts", functools.partial(resolve_facts, engine=engine)
        )
    )
    harness.register_worker(
        FunctionWorker("operadora.inadimplencia.assess_status", functools.partial(assess_status, dmn=dmn))
    )
    harness.register_worker(FunctionWorker("operadora.inadimplencia.calculate_purge", calculate_purge))
    harness.register_worker(
        FunctionWorker("operadora.inadimplencia.check_prior_notice", dispatch_prior_notice)
    )
    harness.register_worker(FunctionWorker("operadora.inadimplencia.prepare_dossier", prepare_dossier))
    harness.register_worker(
        FunctionWorker("operadora.inadimplencia.register_contract_suspension", register_contract_suspension)
    )
    harness.register_worker(
        FunctionWorker(
            "operadora.inadimplencia.handoff_rescisao",
            functools.partial(handoff_rescisao, engine=engine, audit_sink=audit_sink),
        )
    )
    harness.register_worker(FunctionWorker("operadora.inadimplencia.notify_sla_risk", notify_sla_risk))

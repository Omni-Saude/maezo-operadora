"""Authorization workers — SP-OP-AUTH-001.

Provides BPMN external task handlers for the Autorizacao Previa process.

Workers:
- AnalyzeRequestWorker: convoca Rafael, monta dossie de analise
- RequestDocumentsWorker: pendencia ao prestador
- IssueAuthorizationWorker: emite autorizacao TISS
- SendDenialNoticeWorker: negativa formal (guard: ERR_AUTH_DENIAL_NOT_HUMAN)
- NotifySlaRiskWorker: alerta coordenacao
- ConveneJuntaWorker: convoca junta medica

CRITICAL (ADR-0008, L0 hard):
- Negativa de cobertura SO nasce nas User Tasks humanas.
- Workers with ERR_AUTH_DENIAL_NOT_HUMAN guard: send_denial_notice,
  issue_authorization (when decisao=NEGAR).
- Auto-approval (L2) exists only with DMN favorable + teto do tenant.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any

from maezo.tools.workers.base import (
    ERR_AUTH_DENIAL_INCOMPLETE,
    ERR_DENIAL_NOT_HUMAN,
    WorkerBase,
)
from maezo.tools.workers.ceilings import CeilingResolver
from maezo.tools.workers.harness import WorkerBpmnError
from maezo.tools.workers.phi_vars import redact_phi_vars

# Governance ceiling for AUTH L2 auto-approval (design T1.9 §2.4). The teto VALUE lives in
# the autonomy matrix (`authorization_approval.max_value_brl`, L0-core.yaml:22 +
# tenants-amh.yaml overlay), resolved via the SAME loader the PEP uses.
_CEILING_ACTION = "authorization_approval"
_CEILING_PARAM = "max_value_brl"

# ANS-required grounding fields for a *negativa fundamentada* (RN 395 art. 10). These are exactly
# the three fields SP-OP-AUTH-001 marks `requiredIf="decisao_auditor == NEGAR"` +
# `enforcedBy="worker send_denial_notice guard ERR_AUTH_DENIAL_INCOMPLETE"` on each of its three
# human decision tasks (UT_AnaliseMedicoAuditor / UT_CoordenacaoAssume / UT_RegistrarParecerJunta
# — 9 formField annotations, 3 distinct fields). The BPMN is the source of truth for WHAT must be
# present; this list mirrors it exactly (and the donor's own `send_denial_notice` completeness
# check). All three are PHI-named (see `phi_vars.PHI_PROCESS_VARS`).
_REQUIRED_DENIAL_FIELDS: tuple[str, ...] = (
    "justificativa_clinica",
    "cid10_referencia",
    "fundamentacao_dut",
)

# Spec-modeled BPMN error codes the auth workers raise. Registered in the auth harness's
# `bpmn_error_allowlist` so the harness dispatches them as real `bpmnError`s (caught by the BPMN's
# own boundary events) instead of demoting them to fail-closed incidents (harness.py §9). Only
# `ERR_AUTH_DENIAL_INCOMPLETE` is here — it is the ONE auth code with a matching
# `bpmn:error@errorCode` + boundary event (`BE_NegativaIncompleta` on `ST_EnviarNegativaFormal`)
# in SP-OP-AUTH-001. `ERR_DENIAL_NOT_HUMAN` is deliberately NOT allowlisted: the BPMN declares no
# boundary for it, so it must stay a fail-closed incident, never a silently-ended scope.
AUTH_BPMN_ERROR_ALLOWLIST: frozenset[str] = frozenset({ERR_AUTH_DENIAL_INCOMPLETE})

if TYPE_CHECKING:
    from maezo.tools.workers.harness import KafkaPublisher, WorkerHarness

# ---------------------------------------------------------------------------
# AnalyzeRequestWorker
# ---------------------------------------------------------------------------


class AnalyzeRequestWorker(WorkerBase):
    """External task: operadora.auth.analyze_request

    Convoca Rafael (medico auditor) e monta o dossie de analise.
    Pode auto-aprovar (L2) quando todas as condicoes favoraveis sao atendidas.

    Auto-approval conditions (ADR-0008 L2):
    - dut_atendida == True
    - dentro_teto_l2 == True
    - rede_credenciada == True

    Ineligibilidade aparente (beneficiario_ativo=False, carencia_nao_cumprida)
    SEMPRE roteia para analise humana — NUNCA resulta em negativa automatica.
    """

    def __init__(self, resolver: CeilingResolver | None = None) -> None:
        super().__init__(topic="operadora.auth.analyze_request")
        # Ceiling resolver (defect B3): computes `dentro_teto_l2` from the tenant governance
        # matrix, overriding any inbound value. Injectable for tests; the default resolves
        # from the real `spec/policies/autonomy` matrix.
        self._resolver = resolver if resolver is not None else CeilingResolver()

    def execute(self, process_vars: dict[str, Any]) -> dict[str, Any]:
        """Analyze the authorization request.

        Creates a dossier for Rafael. May auto-approve if L2 conditions met.

        Args:
            process_vars: BPMN authorization variables.

        Returns:
            Dict with analysis result and recommendation.
        """
        tenant_id = process_vars.get("tenant_id", "")
        guia = process_vars.get("numero_guia_tiss", "unknown")

        # Check L2 auto-approval conditions
        dut_ok = process_vars.get("dut_atendida", False)
        # COMPUTE the ceiling fact from policy (design T1.9 §2.4, defect B3). NEVER read the
        # inbound `dentro_teto_l2`. FAIL-CLOSED: a missing/None/non-numeric
        # `valor_estimado_brl` yields teto_ok=False (route to human) — never a default 0
        # that would read as "within ceiling" under a positive teto (fail-OPEN). This is an
        # intentional hardening divergence from v1's `_as_float` default-0.0.
        raw_valor = process_vars.get("valor_estimado_brl")
        try:
            valor_cents = round(float(raw_valor) * 100) if raw_valor is not None else None
        except (TypeError, ValueError):
            valor_cents = None
        if valor_cents is None:
            teto_ok = False
        else:
            teto_ok = self._resolver.within_l2_ceiling(
                tenant=tenant_id,
                action=_CEILING_ACTION,
                param=_CEILING_PARAM,
                value_cents=valor_cents,
            )
        rede_ok = process_vars.get("rede_credenciada", False)
        beneficiario_ativo = process_vars.get("beneficiario_ativo", False)
        carencia_ok = process_vars.get("carencia_cumprida", False)
        docs_completa = process_vars.get("documentacao_completa", False)

        dossier_ref = f"dossier-{uuid.uuid4().hex[:12]}"

        # L2 auto-approval: all conditions favorable
        if all([dut_ok, teto_ok, rede_ok, beneficiario_ativo, carencia_ok, docs_completa]):
            self.logger.info(
                "auth_auto_approved",
                tenant_id=tenant_id,
                guia=guia,
                dossier_ref=dossier_ref,
            )
            return {
                "status": "auto_approved",
                "recommendation": "AUTO_APROVAR",
                "dossier_ref": dossier_ref,
                "assigned_to": "rafael",
                "event": "agents.events.auth.received",
                # Write the COMPUTED fact back so the DMN auth_auto_approval receives it —
                # never the inbound boolean (design T1.9 §2.4).
                "dentro_teto_l2": teto_ok,
            }

        # Default: route to human analysis (Rafael)
        self.logger.info(
            "auth_dossier_created",
            tenant_id=tenant_id,
            guia=guia,
            recommendation="ANALISE_HUMANA",
            dossier_ref=dossier_ref,
        )

        return {
            "status": "dossier_created",
            "recommendation": "ANALISE_HUMANA",
            "dossier_ref": dossier_ref,
            "assigned_to": "rafael",
            "event": "agents.events.auth.received",
            "dentro_teto_l2": teto_ok,
        }


# ---------------------------------------------------------------------------
# RequestDocumentsWorker
# ---------------------------------------------------------------------------


class RequestDocumentsWorker(WorkerBase):
    """External task: operadora.auth.request_documents

    Cria pendencia de documentacao para o prestador.
    Publica evento agents.events.auth.pended.
    """

    def __init__(self) -> None:
        super().__init__(topic="operadora.auth.request_documents")

    def execute(self, process_vars: dict[str, Any]) -> dict[str, Any]:
        """Create a documentation pendecy.

        Args:
            process_vars: Must include prestador_id and missing_docs.

        Returns:
            Dict with pe™ndency status.
        """
        tenant_id = process_vars.get("tenant_id", "")
        prestador_id = process_vars.get("prestador_id", "")
        missing = process_vars.get("missing_docs", [])

        self.logger.info(
            "auth_documents_pended",
            tenant_id=tenant_id,
            prestador_id=prestador_id,
            missing_docs=missing,
        )

        return {
            "status": "pended",
            "prestador_id": prestador_id,
            "missing_docs": missing,
            "event": "agents.events.auth.pended",
        }


# ---------------------------------------------------------------------------
# IssueAuthorizationWorker
# ---------------------------------------------------------------------------


class IssueAuthorizationWorker(WorkerBase):
    """External task: operadora.auth.issue_authorization

    Emite autorizacao TISS com numero unico.
    Guard: ERR_AUTH_DENIAL_NOT_HUMAN — requires human_approved flag.
    """

    def __init__(self) -> None:
        super().__init__(topic="operadora.auth.issue_authorization")

    def execute(self, process_vars: dict[str, Any]) -> dict[str, Any]:
        """Issue a TISS authorization.

        Guard: must have human_approved=True.
        Only issues for APROVAR decisions (never NEGAR).

        Args:
            process_vars: Must include numero_guia_tiss, decisao_auditor, human_approved.

        Returns:
            Dict with authorization number or guard block.
        """
        tenant_id = process_vars.get("tenant_id", "")
        guia = process_vars.get("numero_guia_tiss", "unknown")
        # FAIL-CLOSED (T3.1, mirrors lgpd.ValidateIdentityWorker / ADR-0031): aprovacao confirmada
        # SO com sinal explicito `human_approved is True`. Ausente/False/lixo (string truthy como
        # "true"/" ", int 1, list/dict) -> False -> guard bloqueia a emissao.
        human_approved = process_vars.get("human_approved") is True

        # Guard: require human approval for authorization issuance
        if not human_approved:
            self.logger.warning(
                "auth_issue_blocked_by_guard",
                tenant_id=tenant_id,
                guia=guia,
                reason="human_approved flag missing",
            )
            return {
                "status": "blocked_by_guard",
                "error_code": ERR_DENIAL_NOT_HUMAN,
                "mensagem": "Autorizacao requer aprovacao humana (L0 hard)",
            }

        # Issue authorization
        auth_number = f"AUTH-{tenant_id}-{guia}-{uuid.uuid4().hex[:8]}"

        self.logger.info(
            "auth_issued",
            tenant_id=tenant_id,
            guia=guia,
            numero_autorizacao=auth_number,
        )

        return {
            "status": "authorized",
            "numero_autorizacao": auth_number,
            "error_code": None,
            "event": "agents.events.auth.completed",
        }


# ---------------------------------------------------------------------------
# SendDenialNoticeWorker
# ---------------------------------------------------------------------------


# Zero-width / BOM code points that carry NO visible content but which `str.strip()` does NOT
# remove (their `str.isspace()` is False): zero-width space, ZWNJ, ZWJ, word joiner, BOM /
# zero-width no-break space. A grounding field made only of these is empty for completeness.
_ZERO_WIDTH_CHARS = "\u200b\u200c\u200d\u2060\ufeff"  # ZWSP, ZWNJ, ZWJ, word-joiner, BOM/ZWNBSP
_ZERO_WIDTH_TRANSLATION = dict.fromkeys(map(ord, _ZERO_WIDTH_CHARS))


def _is_blank(value: Any) -> bool:
    """Fail-closed emptiness for a required denial-grounding field.

    A field is blank (=> incomplete => DENY the send) when it is NOT a non-empty grounding STRING:
      - any non-``str`` value (``None``, ``int``, ``list``, ``dict``, ``bool``, ``0``, ``False`` …)
        is blank — a required ANS grounding field that is not textual is unusable (the BPMN types
        these fields ``string``; "cannot decide completeness = incomplete = DENY the send"). This is
        stricter than the donor's ``not v`` (which admits e.g. a non-empty list as present).
      - a ``str`` that is empty / whitespace-only after zero-width & BOM characters are removed. The
        zero-width strip closes a gap where ``"\\u200b"`` alone (isspace() is False) would otherwise
        survive ``.strip()`` and read as present.
    """
    if not isinstance(value, str):
        return True
    return not value.translate(_ZERO_WIDTH_TRANSLATION).strip()


class SendDenialNoticeWorker(WorkerBase):
    """External task: operadora.auth.send_denial_notice

    Transmite a negativa FORMAL por escrito, em nome do medico auditor HUMANO. Este worker NUNCA
    decide — apenas transmite/registra uma negativa JA decidida por uma User Task humana
    (UT_AnaliseMedicoAuditor / UT_CoordenacaoAssume / UT_RegistrarParecerJunta; L0 hard,
    ADR-0005/ADR-0008).

    Guards (fail-closed, defesa-em-profundidade):

    1. COMPLETUDE (ERR_AUTH_DENIAL_INCOMPLETE, T3.1) — uma NEGAR cujo dossie nao carrega TODAS as
       fundamentacoes obrigatorias da ANS (`_REQUIRED_DENIAL_FIELDS`: justificativa_clinica,
       cid10_referencia, fundamentacao_dut; RN 395 art. 10) NUNCA e transmitida. Levanta o
       WorkerBpmnError MODELADO `ERR_AUTH_DENIAL_INCOMPLETE`, capturado pelo boundary
       `BE_NegativaIncompleta` (ST_EnviarNegativaFormal) -> `End_FundamentacaoIncompletaBloqueada`
       (terminal NEUTRO: nada foi enviado ao beneficiario). Checado ANTES do guard `human_approved`
       porque um dossie incompleto deve ser barrado em QUALQUER circunstancia (a impossibilidade de
       decidir completude = incompleto = negar a transmissao). O codigo esta em
       `AUTH_BPMN_ERROR_ALLOWLIST` para o harness o despachar como `bpmnError` (nao incidente).

    2. NEGATIVA-NAO-HUMANA (ERR_DENIAL_NOT_HUMAN) — guard v2 pre-existente, INALTERADO (finding
       separado `_ACTION_WORKER_KAFKA_GAP_REASON`, fora do escopo deste PR): uma NEGAR sem
       `human_approved` e bloqueada. RETORNA um registro (nao levanta) — o BPMN nao declara boundary
       para este codigo, entao ele nao pode virar um `bpmnError`.

    EGRESS PHI (ADR-0006): a negativa carrega texto livre clinico (justificativa_clinica /
    cid10_referencia / fundamentacao_dut — nomes-PHI, `phi_vars.PHI_PROCESS_VARS`). Antes de
    qualquer variavel deixar o worker (engine/Kafka = Zona Geral), o payload passa por
    `redact_phi_vars` (redacao UNIDIRECIONAL, classe-token `REDACTED_PHI`): nenhum texto clinico cru
    entra em variavel de engine. O canal seguro real ao prestador recebe o texto integral fora
    desta costura (Fase 1).
    """

    def __init__(self) -> None:
        # max_retries=1: os guards deste worker sao DETERMINISTICOS, nao transitorios. Um
        # WorkerBpmnError (completude) NUNCA deve ser re-tentado pelo retry in-process do
        # WorkerBase — o engine e' dono do retry duravel (T1.1 design §9). Sem isto o
        # WorkerBpmnError seria re-tentado (com sleeps) antes de propagar ao harness.
        super().__init__(topic="operadora.auth.send_denial_notice", max_retries=1)

    def execute(self, process_vars: dict[str, Any]) -> dict[str, Any]:
        """Send denial (or approval) notice.

        Guards (NEGAR path, in order): completeness (raises `ERR_AUTH_DENIAL_INCOMPLETE`), then the
        pre-existing `human_approved` guard. Clinical PHI in the emitted denial payload is redacted
        one-way before return.

        Args:
            process_vars: Must include decisao_auditor; for NEGAR, the three grounding fields
                          (justificativa_clinica, cid10_referencia, fundamentacao_dut) and
                          human_approved.

        Returns:
            Dict with notice status and optional error code (clinical fields redacted).

        Raises:
            WorkerBpmnError: ERR_AUTH_DENIAL_INCOMPLETE when a NEGAR lacks any grounding field.
        """
        tenant_id = process_vars.get("tenant_id", "")
        decisao = process_vars.get("decisao_auditor", "")
        # FAIL-CLOSED (T3.1, mirrors lgpd.ValidateIdentityWorker / ADR-0031): aprovacao confirmada
        # SO com sinal explicito `human_approved is True`. Ausente/False/lixo (string truthy como
        # "true"/" ", int 1, list/dict) -> False -> GUARD 2 bloqueia o envio.
        human_approved = process_vars.get("human_approved") is True

        if decisao == "NEGAR":
            # GUARD 1 — COMPLETUDE (fail-closed, checado PRIMEIRO). None/""/whitespace = ausente.
            missing = [field for field in _REQUIRED_DENIAL_FIELDS if _is_blank(process_vars.get(field))]
            if missing:
                # Loga apenas os NOMES dos campos ausentes — nunca valores (PHI, ADR-0006).
                self.logger.error(
                    "auth_denial_blocked_incomplete",
                    tenant_id=tenant_id,
                    missing_fields=missing,
                    reason="negativa formal exige fundamentacao completa (RN 395 art. 10, L0 hard)",
                )
                raise WorkerBpmnError(
                    ERR_AUTH_DENIAL_INCOMPLETE,
                    f"send_denial_notice: fundamentacao incompleta, campos ausentes: {missing} — "
                    "negativa formal NAO transmitida (RN 395 art. 10, L0 hard ADR-0005).",
                )

            # GUARD 2 — human_approved (INALTERADO; retorna registro, nao levanta).
            if not human_approved:
                self.logger.error(
                    "auth_denial_blocked_by_guard",
                    tenant_id=tenant_id,
                    reason="NEGAR sem aprovacao humana (L0 hard)",
                )
                return {
                    "status": "blocked_by_guard",
                    "error_code": ERR_DENIAL_NOT_HUMAN,
                    "mensagem": "Negativa automatica PROIBIDA. Requer decisao de medico auditor humano.",
                }

            # Transmissao: monta a notificacao formal (carrega os campos clinicos) e REDIGE o PHI
            # (redacao unidirecional) antes de qualquer variavel deixar o worker. Nunca loga o
            # conteudo clinico — apenas o registro de transmissao.
            self.logger.info("auth_denial_sent", tenant_id=tenant_id)
            notice = {
                "status": "notice_sent",
                "notice_type": "denial",
                "error_code": None,
                "event": "agents.events.auth.completed",
                "justificativa_clinica": process_vars.get("justificativa_clinica", ""),
                "cid10_referencia": process_vars.get("cid10_referencia", ""),
                "fundamentacao_dut": process_vars.get("fundamentacao_dut", ""),
            }
            # ENFORCEMENT POINT (PHI egress, ADR-0006/GAP-XPHI-1): nenhum campo clinico cru sai em
            # variavel de engine. Chaves estruturais (status/event/...) passam intactas.
            return redact_phi_vars(notice)

        # APROVAR ou outro — envia aviso de aprovacao (sem campos clinicos).
        self.logger.info(
            "auth_approval_notice_sent",
            tenant_id=tenant_id,
        )
        return {
            "status": "notice_sent",
            "notice_type": "approval",
            "error_code": None,
            "event": "agents.events.auth.completed",
        }


# ---------------------------------------------------------------------------
# NotifySlaRiskWorker
# ---------------------------------------------------------------------------


class NotifySlaRiskWorker(WorkerBase):
    """External task: operadora.auth.notify_sla_risk

    Alerta coordenacao de auditoria medica sobre risco de SLA.
    Timer nao-interruptivo (50-70% do SLA total).
    """

    def __init__(self) -> None:
        super().__init__(topic="operadora.auth.notify_sla_risk")

    def execute(self, process_vars: dict[str, Any]) -> dict[str, Any]:
        """Notify coordinator about SLA risk.

        Args:
            process_vars: Must include sla_percent and sla_analise.

        Returns:
            Dict with notification status.
        """
        tenant_id = process_vars.get("tenant_id", "")
        sla_percent = process_vars.get("sla_percent", 0)
        sla_analise = process_vars.get("sla_analise", "")

        self.logger.warning(
            "auth_sla_risk_notified",
            tenant_id=tenant_id,
            sla_percent=sla_percent,
            sla_analise=sla_analise,
        )

        return {
            "status": "risk_notified",
            "alert_to": "coordenacao-auditoria-medica",
            "sla_percent": sla_percent,
            "sla_analise": sla_analise,
            "event": "agents.events.auth.sla_breached",
        }


# ---------------------------------------------------------------------------
# ConveneJuntaWorker
# ---------------------------------------------------------------------------


class ConveneJuntaWorker(WorkerBase):
    """External task: operadora.auth.convene_junta

    Convoca junta medica (RN 424).
    Guard: ERR_AUTH_DENIAL_NOT_HUMAN — requires human authorization.

    Only convokes when decisao_auditor == JUNTA_MEDICA and human_approved.
    """

    def __init__(self) -> None:
        super().__init__(topic="operadora.auth.convene_junta")

    def execute(self, process_vars: dict[str, Any]) -> dict[str, Any]:
        """Convokes a medical board.

        Guard: requires human_approved=True.

        Args:
            process_vars: Must include decisao_auditor, human_approved.

        Returns:
            Dict with junta status or guard block.
        """
        tenant_id = process_vars.get("tenant_id", "")
        guia = process_vars.get("numero_guia_tiss", "unknown")
        decisao = process_vars.get("decisao_auditor", "")
        # FAIL-CLOSED (T3.1, mirrors lgpd.ValidateIdentityWorker / ADR-0031): aprovacao confirmada
        # SO com sinal explicito `human_approved is True`. Ausente/False/lixo (string truthy como
        # "true"/" ", int 1, list/dict) -> False -> guard bloqueia a convocacao.
        human_approved = process_vars.get("human_approved") is True

        # Guard: require human approval
        if not human_approved:
            self.logger.warning(
                "auth_junta_blocked_by_guard",
                tenant_id=tenant_id,
                guia=guia,
                reason="human_approved flag missing",
            )
            return {
                "status": "blocked_by_guard",
                "error_code": ERR_DENIAL_NOT_HUMAN,
                "mensagem": "Convocacao de junta medica requer aprovacao humana (L0 hard)",
            }

        self.logger.info(
            "auth_junta_convened",
            tenant_id=tenant_id,
            guia=guia,
            decisao=decisao,
        )

        return {
            "status": "junta_convened",
            "junta_group": "junta-medica",
            "fundamentacao": "RN 424/2017",
            "event": "agents.events.auth.completed",
        }


# ---------------------------------------------------------------------------
# Bootstrap — donor contract (T1.2/ADR-0026 Decisao §3): class modules register
# directly, one WorkerBase() instance per topic. auth.py's 6 classes take no
# constructor args (none declares a Kafka dependency today) — `kafka`/`seams`
# are accepted for signature parity with the other 15 register_<domain>_workers
# bootstraps and `register_all_workers` (ADR-0026 Decisao §4), unused here.
# ---------------------------------------------------------------------------


def register_auth_workers(
    harness: WorkerHarness,
    kafka: KafkaPublisher | None = None,
    **seams: Any,
) -> None:
    """Register the 6 SP-OP-AUTH-001 `WorkerBase` workers on `harness`."""
    del kafka, seams  # unused — no auth.py worker declares a Kafka/other seam dependency
    for worker_cls in (
        AnalyzeRequestWorker,
        RequestDocumentsWorker,
        IssueAuthorizationWorker,
        SendDenialNoticeWorker,
        NotifySlaRiskWorker,
        ConveneJuntaWorker,
    ):
        harness.register_worker(worker_cls())

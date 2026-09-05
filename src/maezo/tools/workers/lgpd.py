"""LGPD DSR workers — SP-OP-LGPD-DSR-001.

Provides BPMN external task handlers for Direitos do Titular (art. 18, LGPD).

Workers:
- ValidateIdentityWorker: verificacao de identidade (FAIL-CLOSED em identidade_verificada is True)
- request_additional_proof (#55 R-B): pede prova adicional (challenge anti eng. social)
- ExecuteExportWorker: compilacao de dados para exportacao
- ExecuteRectificationWorker: retificacao de dados
- ExecuteErasureWorker: eliminacao de dados (guard: ERR_DENIAL_NOT_HUMAN)
- send_response (#55 R-F): despacha/notifica o envio da resposta aprovada pelo humano
- notify_sla_risk (#55 R-G): notifica risco/estouro de SLA (fase ack P7D e resolution P15D)

CRITICAL (LGPD, ADR-0008, L0 hard):
- Nenhum dado sensivel sai sem revisao humana (DPO/juridico-privacidade)
- Negativa fundamentada e SEMPRE decisao humana
- Verificacao de identidade ANTES de qualquer compilacao — FAIL-CLOSED: identidade so e
  confirmada por um sinal explicito `identidade_verificada is True` (anti engenharia social,
  bpmn:87). A mera presenca do titular_pseudo_id (OBRIGATORIO) NUNCA confirma identidade.
- GAP-LGPD-6: ausencia de titular_pseudo_id -> RAISE WorkerBpmnError(ERR_DSR_IDENTITY_UNVERIFIED)
  (guarda TECNICO, NUNCA acusacao automatica de fraude), capturado pelo boundary
  BE_IdentidadeInverificavel -> End_IdentidadeInverificavel (terminal NEUTRO, fail-safe).
- FAIL-CLOSED (GAP-LGPD-4): decisao_dsr ausente/desconhecida NUNCA libera dados

NOTE (#55 R-E, T2.8): `AssessRequestWorker` (topic `operadora.lgpd.assess_request`) was RETIRED —
`BRT_RotearDsr` is a native `businessRuleTask` (`camunda:decisionRef="lgpd_dsr_routing"`,
bpmn:172-179) evaluated ENGINE-SIDE (ADR-0028: DMN-as-sole-router, ratified). The worker duplicated
the DMN's routing logic and was unreachable by construction — no BPMN service task ever called its
topic (verified: `docs/compliance/lgpd-topic-reconciliation.md` R-E). No `dmn` seam is consumed by
this module anymore.

NOTE (R-H, gap `LGPD-PUBLISH-COMPLETED-ORPHAN-TOPIC`): `PublishCompletedWorker` (topic
`operadora.lgpd.publish_completed`) was RETIRED for the same reason, one class of defect further
along. It was an ORPHAN CODE TOPIC — `grep -rn "operadora.lgpd.publish_completed" spec/` returns
ZERO hits, no BPMN service task ever carried it — and it was worse than merely unreachable: a
SYNCHRONOUS `WorkerBase.execute` with no publisher seam of any kind returned
`{"status": "published", "event": "agents.events.lgpd_dsr.completed"}`, asserting a publication it
never performed. The DSR's completion IS published, by the BPMN's shared generic publisher
`ST_PublishCompleted` -> `operadora.events.publish`
(`spec/processes/bpmn/SP-OP-LGPD-DSR-001_Direitos_do_Titular.bpmn`, with `event_topic`/
`event_desfecho` computed in-engine by JUEL, GAP-LGPD-7), which is out of this module's scope
(ADR-0026 §2b) and is untouched by the retirement. `docs/compliance/lgpd-topic-reconciliation.md`
classifies the fix as "Fix code -> spec · MECHANICAL" (R-H); that document is itself DRAFT and
ratifies nothing — the retirement stands on the engineering fact above, not on a sign-off.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

import structlog

from maezo.platform.integrations.partition_key import partition_key_for_task
from maezo.tools.workers.base import ERR_DENIAL_NOT_HUMAN, WorkerBase
from maezo.tools.workers.harness import ExternalTask, WorkerBpmnError, WorkerFailureError

if TYPE_CHECKING:
    from maezo.tools.workers.harness import KafkaPublisher, TaskHandler, WorkerHarness

logger = structlog.get_logger(__name__)
# Stdlib logger mirror (harness.py's dual-logging convention) — the kafka=None loud log below must
# surface even where structlog isn't wired.
_stdlib_logger = logging.getLogger(__name__)

# ADR-0030 Tier-0 / GAP-LGPD-6: the ONE consumption-covered BPMN error code an lgpd worker raises.
# `ValidateIdentityWorker` raises `WorkerBpmnError("ERR_DSR_IDENTITY_UNVERIFIED")` when the titular
# is MECHANICALLY unverifiable (empty `titular_pseudo_id`); the BPMN declares a matching
# `bpmn:error@errorCode="ERR_DSR_IDENTITY_UNVERIFIED"` error boundary
# (`BE_IdentidadeInverificavel` on `ST_VerificarIdentidade`, SP-OP-LGPD-DSR-001) ->
# `End_IdentidadeInverificavel` (terminal NEUTRO, fail-safe, NAO adverso). `verify_identity` is
# consumed only by SP-OP-LGPD-DSR-001, which declares that boundary, so the code is
# consumption-covered; it is a technical impossibility (not a denial), so it is NOT T-E-gated
# (ADR-0030 §4) and the runtime unions it into its production `bpmn_error_allowlist`
# (`worker_runtime/service.py`). The boundary-proof gate
# (`scripts/ci/check_bpmn_error_allowlist.py`) — not this list — is the source of truth. Mirrors
# auth's `AUTH_BPMN_ERROR_ALLOWLIST`.
LGPD_BPMN_ERROR_ALLOWLIST: frozenset[str] = frozenset({"ERR_DSR_IDENTITY_UNVERIFIED"})

# ---------------------------------------------------------------------------
# ValidateIdentityWorker
# ---------------------------------------------------------------------------


class ValidateIdentityWorker(WorkerBase):
    """External task: operadora.lgpd.verify_identity

    Verifica a identidade do titular ANTES de qualquer compilacao de dados.

    FAIL-CLOSED: identidade so e confirmada por um sinal EXPLICITO
    `identidade_verificada is True` (semeado por um produtor ratificado — canal autenticado
    DPO-ratificado, ou a correlacao humana de revisao de prova `msg.lgpd.proof_received`). Ausente/
    False/lixo -> NAO confirmada -> GW_Identidade roteia para o challenge (ST_PedirProvaAdicional).
    A mera presenca do titular_pseudo_id (OBRIGATORIO) NUNCA confirma identidade — isso seria
    fail-OPEN (vetor classico de engenharia social, bpmn:87).

    Per GAP-LGPD-6: titular_pseudo_id ausente/vazio -> RAISE
    WorkerBpmnError(ERR_DSR_IDENTITY_UNVERIFIED). Este e um guarda TECNICO (impossibilidade
    MECANICA de verificar), NUNCA uma acusacao automatica de fraude (L0 hard fraud_accusation).
    """

    def __init__(self) -> None:
        # max_retries=1: o guard de identidade e DETERMINISTICO, nao transitorio. Um
        # WorkerBpmnError (titular mecanicamente inverificavel) NUNCA deve ser re-tentado pelo
        # retry in-process do WorkerBase (base.py:143-201 re-tenta TODA Exception, incl.
        # WorkerBpmnError, com time.sleep ANTES de propagar ao harness e o boundary poder disparar)
        # — o engine e' dono do retry duravel (T1.1 design §9). Espelha auth.SendDenialNoticeWorker.
        super().__init__(topic="operadora.lgpd.verify_identity", max_retries=1)

    def execute(self, process_vars: dict[str, Any]) -> dict[str, Any]:
        """Verify the titular's identity (FAIL-CLOSED).

        Args:
            process_vars: Must include titular_pseudo_id and canal; reads identidade_verificada.

        Returns:
            Dict with identidade_confirmada boolean and status.

        Raises:
            WorkerBpmnError: ERR_DSR_IDENTITY_UNVERIFIED when titular_pseudo_id is absent/empty
                (GAP-LGPD-6 — mechanically unverifiable).
        """
        tenant_id = process_vars.get("tenant_id", "")
        pseudo_id = process_vars.get("titular_pseudo_id")
        canal = process_vars.get("canal", "")

        # GAP-LGPD-6: absent/empty pseudo_id -> identity MECHANICALLY unverifiable. RAISE a modeled
        # BPMN error so BE_IdentidadeInverificavel (boundary on ST_VerificarIdentidade) fires ->
        # End_IdentidadeInverificavel (terminal NEUTRO, fail-safe). Guarda TECNICO, NUNCA acusacao
        # de fraude (L0 hard fraud_accusation intacto). Retornar o codigo como VALOR de dict (o
        # comportamento anterior) deixava o boundary pendurado e End_IdentidadeInverificavel
        # inalcancavel (o proprio GAP-LGPD-6).
        if not pseudo_id:
            self.logger.warning(
                "lgpd_identity_unverifiable",
                tenant_id=tenant_id,
                canal=canal,
                reason="titular_pseudo_id ausente ou vazio",
            )
            raise WorkerBpmnError(
                "ERR_DSR_IDENTITY_UNVERIFIED",
                "verify_identity: titular_pseudo_id ausente/vazio — identidade MECANICAMENTE "
                "inverificavel (GAP-LGPD-6, guarda TECNICO: impossibilidade de verificar, "
                "distinta de qualquer decisao de merito sobre o titular).",
            )

        # FAIL-CLOSED: identidade confirmada SO com sinal explicito `identidade_verificada is True`.
        # Ausente/False/lixo -> False -> challenge sub-flow. Ler o sinal real fecha o defeito
        # fail-OPEN (confirmar por mera presenca do pseudo_id).
        identidade_confirmada = process_vars.get("identidade_verificada") is True

        self.logger.info(
            "lgpd_identity_checked",
            tenant_id=tenant_id,
            canal=canal,
            identidade_confirmada=identidade_confirmada,
        )

        return {
            "identidade_confirmada": identidade_confirmada,
            "status": "verified" if identidade_confirmada else "pending_proof",
        }


# ---------------------------------------------------------------------------
# ExecuteExportWorker
# ---------------------------------------------------------------------------


class ExecuteExportWorker(WorkerBase):
    """External task: operadora.lgpd.execute_export

    Compila pacote de dados para exportacao/portabilidade.
    Guard: requires human_approved=True.

    LGPD art. 18, II e V — acesso e portabilidade.
    """

    def __init__(self) -> None:
        super().__init__(topic="operadora.lgpd.execute_export")

    def execute(self, process_vars: dict[str, Any]) -> dict[str, Any]:
        """Compile a data package for export.

        Guard: requires human_approved=True.

        Args:
            process_vars: Must include titular_pseudo_id, decisao_dsr, human_approved.

        Returns:
            Dict with package reference or guard block.
        """
        tenant_id = process_vars.get("tenant_id", "")
        decisao = process_vars.get("decisao_dsr", "")
        # FAIL-CLOSED (T3.1, mirrors ValidateIdentityWorker / ADR-0031): aprovacao confirmada SO
        # com sinal explicito `human_approved is True`. Ausente/False/lixo (string truthy como
        # "true"/" ", int 1, list/dict) -> False -> guard bloqueia a exportacao.
        human_approved = process_vars.get("human_approved") is True

        # Guard: require human approval before exporting data
        if not human_approved:
            self.logger.warning(
                "lgpd_export_blocked_by_guard",
                tenant_id=tenant_id,
            )
            return {
                "status": "blocked_by_guard",
                "error_code": ERR_DENIAL_NOT_HUMAN,
                "mensagem": "Exportacao de dados requer aprovacao humana (DPO/juridico)",
            }

        package_ref = f"lgpd-export-{uuid.uuid4().hex[:12]}"

        self.logger.info(
            "lgpd_export_compiled",
            tenant_id=tenant_id,
            package_ref=package_ref,
            decisao=decisao,
        )

        return {
            "status": "export_compiled",
            "package_ref": package_ref,
            "event": "agents.events.lgpd_dsr.completed",
        }


# ---------------------------------------------------------------------------
# ExecuteRectificationWorker
# ---------------------------------------------------------------------------


class ExecuteRectificationWorker(WorkerBase):
    """External task: operadora.lgpd.execute_rectification

    Executa retificacao de dados do titular.
    Guard: requires human_approved=True.

    LGPD art. 18, III — correcao de dados incompletos, inexatos ou desatualizados.
    """

    def __init__(self) -> None:
        super().__init__(topic="operadora.lgpd.execute_rectification")

    def execute(self, process_vars: dict[str, Any]) -> dict[str, Any]:
        """Execute data rectification.

        Guard: requires human_approved=True.

        Args:
            process_vars: Must include titular_pseudo_id, decisao_dsr, human_approved.

        Returns:
            Dict with rectification status.
        """
        tenant_id = process_vars.get("tenant_id", "")
        # FAIL-CLOSED (T3.1, mirrors ValidateIdentityWorker / ADR-0031): aprovacao confirmada SO
        # com sinal explicito `human_approved is True`. Ausente/False/lixo (string truthy como
        # "true"/" ", int 1, list/dict) -> False -> guard bloqueia a retificacao.
        human_approved = process_vars.get("human_approved") is True

        if not human_approved:
            self.logger.warning(
                "lgpd_rectification_blocked_by_guard",
                tenant_id=tenant_id,
            )
            return {
                "status": "blocked_by_guard",
                "error_code": ERR_DENIAL_NOT_HUMAN,
                "mensagem": "Retificacao de dados requer aprovacao humana",
            }

        self.logger.info(
            "lgpd_rectification_completed",
            tenant_id=tenant_id,
        )

        return {
            "status": "rectification_completed",
            "data_type": "rectification",
            "event": "agents.events.lgpd_dsr.completed",
        }


# ---------------------------------------------------------------------------
# ExecuteErasureWorker
# ---------------------------------------------------------------------------


class ExecuteErasureWorker(WorkerBase):
    """External task: operadora.lgpd.execute_erasure

    Executa eliminacao de dados (LGPD art. 18, VI).
    Guard DUPLO:
    1. ERR_DENIAL_NOT_HUMAN: human_approved deve ser True
    2. FAIL-CLOSED (GAP-LGPD-4): decisao_dsr deve ser EXECUTAR_E_ENVIAR

    NUNCA elimina dados com NEGAR_FUNDAMENTADO (decisao humana de negar).
    NUNCA elimina dados sem decisao_dsr explicita (fail-closed).

    FAIL-CLOSED (T3.4-F3): mesmo com AMBOS os guards satisfeitos (human_approved is True +
    decisao_dsr == EXECUTAR_E_ENVIAR), a eliminacao REAL ainda NAO e executavel — a cascata de
    delecao (`platform.erasure.ErasureManager`) nao esta implementada (gated na matriz de bases
    legais/retencao do DPO + reconciliacao de schema de checkpoint, T3.4-F4) E nao ha resolucao
    `titular_pseudo_id`->`fhir_patient_id` disponivel nas process_vars. Retornar
    `status="erasure_completed"` aqui — o comportamento anterior — mentia ao titular (art. 18, VI):
    afirmava delecao concluida sem executar UMA LINHA de SQL. O worker portanto LEVANTA um INCIDENTE
    fail-closed (`WorkerFailureError(retries_left=0)`), NUNCA reporta sucesso. Nao e um
    `WorkerBpmnError`: o unico `bpmn:error` com boundary nesta BPMN e ERR_DSR_IDENTITY_UNVERIFIED;
    `ERR_DSR_ERASURE_FAILED` e declarado no spec MAS SEM boundary event e sem service task no topico
    de erasure, entao um raise dele reprovaria o gate de allowlist (ADR-0030 §2 clausula b) e seria
    demovido a failure de qualquer forma. Um incidente (retries=0) e o desfecho honesto: exige
    atencao humana, NUNCA avanca o processo para "enviar confirmacao ao titular".
    """

    def __init__(self) -> None:
        # max_retries=1: este e um refuso DETERMINISTICO (delecao nao implementada), nao um fault
        # transitorio. O WorkerFailureError(retries_left=0) abaixo NUNCA deve ser re-tentado pelo
        # retry in-process do WorkerBase (base.py re-tenta TODA Exception com time.sleep) — o engine
        # e' dono do retry duravel (T1.1 design §9). Espelha ValidateIdentityWorker/auth.
        super().__init__(topic="operadora.lgpd.execute_erasure", max_retries=1)

    def execute(self, process_vars: dict[str, Any]) -> dict[str, Any]:
        """Execute data erasure — or fail closed if real deletion is not yet possible.

        Duplo guard: human_approved + decisao_dsr == EXECUTAR_E_ENVIAR.

        Args:
            process_vars: Must include titular_pseudo_id, decisao_dsr, human_approved.

        Returns:
            Dict with erasure status — for the GUARD-BLOCKED outcomes only (blocked_by_guard /
            erasure_blocked). The APPROVED path never returns: it raises (see Raises).

        Raises:
            WorkerFailureError: retries_left=0 (immediate, non-retried engine incident) when both
                guards pass but real deletion is unimplemented (T3.4-F3). NEVER reports success.
        """
        tenant_id = process_vars.get("tenant_id", "")
        decisao = process_vars.get("decisao_dsr", "")
        # FAIL-CLOSED (T3.1, mirrors ValidateIdentityWorker / ADR-0031): aprovacao confirmada SO
        # com sinal explicito `human_approved is True`. Ausente/False/lixo (string truthy como
        # "true"/" ", int 1, list/dict) -> False -> Guard 1 bloqueia a eliminacao.
        human_approved = process_vars.get("human_approved") is True
        fundamentacao = process_vars.get("fundamentacao_legal", "")

        # Guard 1: human approval required
        if not human_approved:
            self.logger.error(
                "lgpd_erasure_blocked_by_guard",
                tenant_id=tenant_id,
                reason="human_approved flag missing (L0 hard)",
            )
            return {
                "status": "blocked_by_guard",
                "error_code": ERR_DENIAL_NOT_HUMAN,
                "mensagem": "Eliminacao de dados requer aprovacao humana (DPO/juridico)",
            }

        # Guard 2: FAIL-CLOSED — must have explicit decision
        if decisao == "NEGAR_FUNDAMENTADO":
            self.logger.info(
                "lgpd_erasure_blocked_negada_fundamentada",
                tenant_id=tenant_id,
                fundamentacao=fundamentacao,
            )
            return {
                "status": "erasure_blocked",
                "reason": "negada_fundamentada",
                "fundamentacao_legal": fundamentacao,
                "event": "agents.events.lgpd_dsr.completed",
            }

        # FAIL-CLOSED (GAP-LGPD-4): decisao_dsr must be EXECUTAR_E_ENVIAR
        if decisao != "EXECUTAR_E_ENVIAR":
            self.logger.error(
                "lgpd_erasure_blocked_invalid_decision",
                tenant_id=tenant_id,
                decisao=decisao,
            )
            return {
                "status": "blocked_by_guard",
                "error_code": ERR_DENIAL_NOT_HUMAN,
                "mensagem": (
                    "Eliminacao requer decisao_dsr='EXECUTAR_E_ENVIAR' explicita (fail-closed, GAP-LGPD-4)"
                ),
            }

        # Both guards satisfied (human_approved is True + decisao_dsr == EXECUTAR_E_ENVIAR) — but
        # real deletion is NOT IMPLEMENTED. FAIL CLOSED: raise a non-retried incident instead of
        # fabricating "erasure_completed". A future implementor MUST wire the real cascade
        # (`platform.erasure.ErasureManager` — currently raises ErasureNotImplementedError) AND the
        # titular_pseudo_id->fhir_patient_id resolution, then consciously update this branch and its
        # tests. Removing this raise without implementing deletion re-introduces the audited defect
        # (LGPD art. 18, VI: telling the titular their data is gone while it remains).
        self.logger.error(
            "lgpd_erasure_refused_not_implemented",
            tenant_id=tenant_id,
            reason="real deletion cascade unimplemented (T3.4-F3, gated on DPO matrix + T3.4-F4)",
        )
        raise WorkerFailureError(
            "execute_erasure: eliminacao APROVADA (human_approved + decisao_dsr=EXECUTAR_E_ENVIAR) "
            "mas a cascata de delecao real NAO esta implementada (platform.erasure.ErasureManager "
            "levanta ErasureNotImplementedError; falta resolucao titular_pseudo_id->fhir_patient_id). "
            "Recusando a reportar 'erasure_completed' sem executar SQL (LGPD art. 18, VI) — "
            "incidente fail-closed, NAO retentar, NAO avancar para envio de confirmacao ao titular.",
            retries_left=0,
        )


# ---------------------------------------------------------------------------
# request_additional_proof (#55 R-B) — anti-social-engineering challenge step
# ---------------------------------------------------------------------------

_REQUEST_PROOF_TOPIC = "operadora.lgpd.request_additional_proof"
_NOTIFICATIONS_TOPIC = "operadora.notifications.internal"
_REQUEST_PROOF_NOTIFICATION_TYPE = "lgpd.request_additional_proof"


def make_request_additional_proof_handler(kafka: KafkaPublisher | None) -> TaskHandler:
    """Create the handler for `operadora.lgpd.request_additional_proof` (#55 R-B).

    Serves `ST_PedirProvaAdicional` (SP-OP-LGPD-DSR-001), reached from `GW_Identidade`'s default
    "nao confirmada" branch when `ValidateIdentityWorker` reports `identidade_confirmada=False`.
    Its ONLY job is to ASK the titular for additional identity proof (emit an internal
    notification) so the flow can park at `GW_AguardarProva` awaiting `msg.lgpd.proof_received`.

    CRITICAL — it MUST NOT set `identidade_verificada`: this worker NEVER confirms identity. The
    verified signal is seeded LATER, out of band, by the two ratified producers (a DPO-ratified
    authenticated-channel seed, and the human proof-review message correlation
    `msg.lgpd.proof_received`, which loops back to `ST_VerificarIdentidade`). Confirming here would
    reintroduce the fail-OPEN defect this change closes. No adverse/denial semantics (LGPD Art. 18
    — a subject-rights request is a classic social-engineering exfiltration vector, bpmn:87).

    Fail-closed publish posture (t2-notify-integrity item 1): the publish is this task's ONLY
    business effect, on the MAIN token path — after it, the instance parks at `GW_AguardarProva`
    for proof that can only arrive if the titular was actually asked, with a P10D timer
    (`ICE_PrazoProva`) that kills the DSR as `expirada_identidade` when it never comes. A
    silently-swallowed publish therefore ends the Art. 18 request unanswered WITH THE RECORD
    BLAMING THE TITULAR. `kafka.publish(..., best_effort=False)` forces the producer to PROPAGATE
    a broker failure; no BPMN error boundary is declared on `ST_PedirProvaAdicional`, so the RAW
    exception rides the harness retry/incident ladder (ADR-0030) — the token is HELD at this task
    (preventing the un-asked P10D death) until the challenge is actually dispatched or an operator
    intervenes. Exact twin of `make_notify_sla_risk_handler`'s posture below.

    Raw-handler registration (mirrors events.py) because emitting a notification needs the async
    Kafka seam a sync `WorkerBase.execute` boundary cannot reach.
    """

    async def handler(task: ExternalTask) -> Mapping[str, Any]:
        v = task.variables
        tenant_id = v.get("tenant_id", "")
        canal = v.get("canal", "")
        # No PHI: titular_pseudo_id is a pseudonym; detalhes_requisicao (free-text PHI) is NEVER
        # copied into the notification (ADR-0006).
        notification = {
            "type": _REQUEST_PROOF_NOTIFICATION_TYPE,
            "tenant_id": tenant_id,
            "canal": canal,
            "titular_pseudo_id": v.get("titular_pseudo_id"),
            "tipo_requisicao": v.get("tipo_requisicao", ""),
        }

        if kafka is None:
            # No producer wired yet (T1.2/ADR-0026 gap, same reality as events.py). Log LOUDLY and
            # complete anyway — the task MUST complete so the flow reaches GW_AguardarProva (else
            # the challenge path HANGS in prod). NEVER fabricate identidade_verificada.
            logger.warning(
                "lgpd_request_additional_proof_no_producer",
                tenant_id=tenant_id,
                canal=canal,
                business_key=task.business_key,
            )
            _stdlib_logger.warning(
                "lgpd_request_additional_proof_no_producer business_key=%s — kafka=None (no "
                "producer wired); notification NOT published, completing to reach GW_AguardarProva",
                task.business_key,
            )
            return {}

        # best_effort=False (t2-notify-integrity item 1): _NOTIFICATIONS_TOPIC is a producer
        # BEST_EFFORT topic — the default posture would swallow a broker-down failure and this
        # handler would report success while the titular was never asked for proof (the instance
        # then dies at ICE_PrazoProva P10D as `expirada_identidade`, blaming the titular). No BPMN
        # boundary is declared on ST_PedirProvaAdicional -> RAW propagate to the harness
        # retry/incident ladder (ADR-0030), holding the token AT this task until delivered.
        # GAP-SC-04-a: the partition key comes from the ONE shared chain (task business key ->
        # payload anchors -> `{tenant}|{process_instance_id}`), never from `task.business_key or
        # None` — that idiom degraded a blank business key into an UNKEYED publish, i.e.
        # round-robin across the topic's 3 default partitions and no per-entity ordering. Hoisted
        # above the publish so a `PseudonymizerKeyMissingError` (ratified `scrub_only` with no
        # provisioned `PHI_HMAC_KEY`) stays a configuration fault, never a broker diagnosis.
        message_key = partition_key_for_task(task, _NOTIFICATIONS_TOPIC, notification)
        await kafka.publish(_NOTIFICATIONS_TOPIC, notification, key=message_key, best_effort=False)
        logger.info(
            "lgpd_request_additional_proof_sent",
            tenant_id=tenant_id,
            canal=canal,
            business_key=task.business_key,
        )
        return {}

    return handler


# ---------------------------------------------------------------------------
# send_response (#55 R-F) — dispatch/notify the human-approved response
# ---------------------------------------------------------------------------

_SEND_RESPONSE_TOPIC = "operadora.lgpd.send_response"
_SEND_RESPONSE_NOTIFICATION_TYPE = "lgpd.send_response"


def make_send_response_handler(kafka: KafkaPublisher | None) -> TaskHandler:
    """Create the handler for `operadora.lgpd.send_response` (#55 R-F).

    Serves `ST_EnviarResposta` (SP-OP-LGPD-DSR-001, bpmn:277-284), reached from THREE incoming
    flows: `Flow_GWDec_Enviar` (`APROVAR_ENVIO`), `Flow_GWFund_Ok` (`NEGAR_FUNDAMENTADO`, engine-
    guarded non-empty `fundamentacao_legal` by `GW_GuardFundamentacao`, GAP-LGPD-3), and
    `Flow_Executar_Enviar` (post-`ST_ExecutarRequisicao`, `EXECUTAR_E_ENVIAR`). Its ONLY job is to
    DISPATCH/NOTIFY that the human-approved response is being sent — the response CONTENT itself
    (data package, execution confirmation, or negativa fundamentada wording — bpmn:279) is
    human-authored at `UT_RevisaoDpo` / compiled at `compile_data_package` (#55 R-C, out of scope
    here), never fabricated by this worker.

    CRITICAL — no PHI/free-text in the notification payload: `fundamentacao_legal` (the
    NEGAR_FUNDAMENTADO legal justification, free text) is read ONLY to derive a bounded PRESENCE
    flag (`tem_fundamentacao: bool`) — its raw text is NEVER copied into the notification or
    logged, mirroring #55 R-B's `detalhes_requisicao` exclusion (ADR-0006).

    Fail-closed publish posture (t2-notify-integrity item 1): dispatch IS this task's only job,
    on the MAIN path's TERMINAL leg — immediately after it, `ST_PublishCompleted` publishes
    `lgpd_dsr.completed` and the process ends (`End_RequisicaoConcluida`). A silently-swallowed
    publish means the titular never receives the legally mandated response (including a negativa
    fundamentada — LGPD Art. 18/19 exposure) while the process reaches TERMINAL with false
    success; there is no timer, no backstop, no re-tick. `kafka.publish(..., best_effort=False)`
    forces the producer to PROPAGATE; no BPMN error boundary is declared on `ST_EnviarResposta`,
    so the RAW exception rides the harness retry/incident ladder (ADR-0030) — the token is held at
    this task until the response is actually dispatched or an operator intervenes, never a false
    terminal.

    Raw-handler registration (mirrors `make_request_additional_proof_handler`) because emitting a
    notification needs the async Kafka seam a sync `WorkerBase.execute` boundary cannot reach.
    """

    async def handler(task: ExternalTask) -> Mapping[str, Any]:
        v = task.variables
        tenant_id = v.get("tenant_id", "")
        canal = v.get("canal", "")
        decisao = v.get("decisao_dsr", "")
        notification: dict[str, Any] = {
            "type": _SEND_RESPONSE_NOTIFICATION_TYPE,
            "tenant_id": tenant_id,
            "canal": canal,
            "titular_pseudo_id": v.get("titular_pseudo_id"),
            "tipo_requisicao": v.get("tipo_requisicao", ""),
            "decisao_dsr": decisao,
        }
        if decisao == "NEGAR_FUNDAMENTADO":
            # Presence-only signal (bounded bool) — the legal-justification TEXT never leaves
            # UT_RevisaoDpo via this worker.
            notification["tem_fundamentacao"] = bool(v.get("fundamentacao_legal"))

        if kafka is None:
            # No producer wired yet (T1.2/ADR-0026 gap, same reality as R-B). Log LOUDLY and
            # complete anyway — the task MUST complete so the flow reaches ST_PublishCompleted
            # (else the response-delivery step HANGS in prod).
            logger.warning(
                "lgpd_send_response_no_producer",
                tenant_id=tenant_id,
                canal=canal,
                decisao=decisao,
                business_key=task.business_key,
            )
            _stdlib_logger.warning(
                "lgpd_send_response_no_producer business_key=%s decisao=%s — kafka=None (no "
                "producer wired); notification NOT published, completing to reach ST_PublishCompleted",
                task.business_key,
                decisao,
            )
            return {}

        # best_effort=False (t2-notify-integrity item 1): the default best-effort posture for
        # _NOTIFICATIONS_TOPIC would swallow a broker-down failure and let the DSR reach a
        # TERMINAL false success with the titular's legally mandated response never dispatched
        # (worst of the four audited swallows). No BPMN boundary on ST_EnviarResposta -> RAW
        # propagate to the harness retry/incident ladder (ADR-0030).
        # GAP-SC-04-a: the partition key comes from the ONE shared chain (task business key ->
        # payload anchors -> `{tenant}|{process_instance_id}`), never from `task.business_key or
        # None` — that idiom degraded a blank business key into an UNKEYED publish, i.e.
        # round-robin across the topic's 3 default partitions and no per-entity ordering. Hoisted
        # above the publish so a `PseudonymizerKeyMissingError` (ratified `scrub_only` with no
        # provisioned `PHI_HMAC_KEY`) stays a configuration fault, never a broker diagnosis.
        message_key = partition_key_for_task(task, _NOTIFICATIONS_TOPIC, notification)
        await kafka.publish(_NOTIFICATIONS_TOPIC, notification, key=message_key, best_effort=False)
        logger.info(
            "lgpd_send_response_sent",
            tenant_id=tenant_id,
            canal=canal,
            decisao=decisao,
            business_key=task.business_key,
        )
        return {}

    return handler


# ---------------------------------------------------------------------------
# notify_sla_risk (#55 R-G) — SLA-risk notification (ack + resolution phases)
# ---------------------------------------------------------------------------

_NOTIFY_SLA_RISK_TOPIC = "operadora.lgpd.notify_sla_risk"
_NOTIFY_SLA_RISK_NOTIFICATION_TYPE = "lgpd.notify_sla_risk"


def make_notify_sla_risk_handler(kafka: KafkaPublisher | None) -> TaskHandler:
    """Create the handler for `operadora.lgpd.notify_sla_risk` (#55 R-G).

    Serves TWO service tasks on the SAME topic:
      - `ST_NotificarRiscoSla` (bpmn:217-232): non-interrupting `BT_AlertaDpo` boundary timer
        (P7D) on `UT_RevisaoDpo` — internal ack-phase alert. Inputs `sla_breach_task_name=
        UT_RevisaoDpo`, `sla_breach_phase=ack` (bpmn:226-227).
      - `ST_NotificarJuridicoBreach` (bpmn:325-337): inside the non-interrupting event subprocess
        `ESP_SlaGlobal` (P15D since instance start, LGPD art. 19-II) — the LEGAL deadline breach.
        Inputs `sla_breach_task_name=UT_RevisaoDpo`, `sla_breach_phase=resolution` (bpmn:331-332).

    `ExternalTask` (harness.py) does not expose `activityId` — these two `camunda:inputParameter`s
    are the ONLY discriminator available to distinguish the caller. Notify-only: NEVER an adverse
    action against the titular (an internal/legal SLA alert, not a merit decision).

    Fail-safe (no BPMN error boundary is declared on either service task — verified: neither
    `bpmn:217-232` nor `bpmn:325-337` carries a `bpmn:boundaryEvent`): a `kafka.publish` failure
    PROPAGATES uncaught, mirroring `make_publish_event_handler`'s non-opt-in path (`events.py`) —
    the harness's normal dispatch ladder (`harness.py` module docstring, design §9) computes the
    retry/incident, never silently swallowed, never a fabricated success. NON-HOLLOW NOTE
    (t8-escalation-boundary): this claim was only true in the ABSTRACT until the real producer
    landed — `_NOTIFICATIONS_TOPIC` is one of `AioKafkaEventsProducer.BEST_EFFORT_TOPICS`, so the
    producer's DEFAULT posture SWALLOWED the failure one layer below and this docstring lied. The
    publish now passes `best_effort=False`, forcing the producer to PROPAGATE, which is what makes
    "PROPAGATES uncaught" actually hold against the real transport. Both callers are
    non-interrupting SIDE branches running IN PARALLEL with the main DSR token (the boundary timer
    does not cancel `UT_RevisaoDpo`; the event subprocess is a separate token from instance start)
    — so a notify failure here is contained to its own side branch and never corrupts/blocks the
    main DSR flow's continuation.
    """

    async def handler(task: ExternalTask) -> Mapping[str, Any]:
        v = task.variables
        tenant_id = v.get("tenant_id", "")
        phase = v.get("sla_breach_phase", "")
        task_name = v.get("sla_breach_task_name", "")
        notification = {
            "type": _NOTIFY_SLA_RISK_NOTIFICATION_TYPE,
            "tenant_id": tenant_id,
            "titular_pseudo_id": v.get("titular_pseudo_id"),
            "tipo_requisicao": v.get("tipo_requisicao", ""),
            "sla_breach_task_name": task_name,
            "sla_breach_phase": phase,
        }

        if kafka is None:
            # No producer wired yet (T1.2/ADR-0026 gap, same reality as R-B/R-F). Log LOUDLY and
            # complete anyway — notify-only, never blocks the DSR flow either way.
            logger.warning(
                "lgpd_notify_sla_risk_no_producer",
                tenant_id=tenant_id,
                phase=phase,
                task_name=task_name,
                business_key=task.business_key,
            )
            _stdlib_logger.warning(
                "lgpd_notify_sla_risk_no_producer business_key=%s phase=%s task_name=%s — "
                "kafka=None (no producer wired); notification NOT published, completing "
                "(notify-only, never blocks the DSR flow)",
                task.business_key,
                phase,
                task_name,
            )
            return {}

        # best_effort=False (t8-escalation-boundary ROOT-CAUSE fix): _NOTIFICATIONS_TOPIC is a
        # producer BEST_EFFORT topic, so the DEFAULT posture would SWALLOW a broker-down failure
        # one layer below — silently losing the LGPD Art. 19-II legal-deadline notice while this
        # handler falsely reported success, directly contradicting this function's own docstring
        # ("a `kafka.publish` failure PROPAGATES uncaught"). No BPMN error boundary is declared on
        # either notify_sla_risk service task (verified: neither ST_NotificarRiscoSla nor
        # ST_NotificarJuridicoBreach carries a bpmn:boundaryEvent), so the root-cause-correct
        # posture is to PROPAGATE the RAW exception to the harness's retry/incident ladder (never a
        # modeled bpmnError, never a fabricated success) — both callers are non-interrupting SIDE
        # branches, so the propagation is contained and never blocks the main DSR token.
        # GAP-SC-04-a: the partition key comes from the ONE shared chain (task business key ->
        # payload anchors -> `{tenant}|{process_instance_id}`), never from `task.business_key or
        # None` — that idiom degraded a blank business key into an UNKEYED publish, i.e.
        # round-robin across the topic's 3 default partitions and no per-entity ordering. Hoisted
        # above the publish so a `PseudonymizerKeyMissingError` (ratified `scrub_only` with no
        # provisioned `PHI_HMAC_KEY`) stays a configuration fault, never a broker diagnosis.
        message_key = partition_key_for_task(task, _NOTIFICATIONS_TOPIC, notification)
        await kafka.publish(_NOTIFICATIONS_TOPIC, notification, key=message_key, best_effort=False)
        logger.info(
            "lgpd_notify_sla_risk_sent",
            tenant_id=tenant_id,
            phase=phase,
            task_name=task_name,
            business_key=task.business_key,
        )
        return {}

    return handler


# ---------------------------------------------------------------------------
# Bootstrap — donor contract (T1.2/ADR-0026 Decisao §3). Of
# `spec/processes/bpmn/SP-OP-LGPD-DSR-001_*.bpmn`'s external-task topics,
# `operadora.lgpd.verify_identity` (WorkerBase), `operadora.lgpd.request_additional_proof`
# (#55 R-B), `operadora.lgpd.send_response` (#55 R-F), and `operadora.lgpd.notify_sla_risk`
# (#55 R-G) are now served; that BPMN's remaining 2 topics (compile_data_package/
# execute_request) still have no worker — #55 R-C/R-D, DPO/SME-sign-off-gated, out of scope
# here (no business-logic/topic edits). `operadora.lgpd.assess_request` (formerly
# `AssessRequestWorker`) was RETIRED (#55 R-E, T2.8) — routing is a native engine-side DMN
# decision (`BRT_RotearDsr` -> `lgpd_dsr_routing`), never an external task.
# `operadora.lgpd.publish_completed` (formerly `PublishCompletedWorker`) was RETIRED too (R-H, gap
# `LGPD-PUBLISH-COMPLETED-ORPHAN-TOPIC`): orphan code topic, zero BPMN service tasks, and the
# completion event is published by the shared generic publisher `ST_PublishCompleted` ->
# `operadora.events.publish` (out of scope here, ADR-0026 §2b). See this module's docstring.
# ---------------------------------------------------------------------------


def register_lgpd_workers(
    harness: WorkerHarness,
    kafka: KafkaPublisher | None = None,
    **seams: Any,
) -> None:
    """Register the SP-OP-LGPD-DSR-001 `WorkerBase` workers + the #55 R-B/R-F/R-G raw handlers on
    `harness`.

    `kafka` is threaded into the THREE raw handlers with a Kafka dependency: `request_additional_
    proof` (#55 R-B), `send_response` (#55 R-F), `notify_sla_risk` (#55 R-G). The remaining
    `WorkerBase` classes take no constructor args. No `dmn`/other seam is consumed by this module
    (#55 R-E: routing is the engine-side `BRT_RotearDsr` DMN, not a worker).
    """
    del seams  # unused — no dmn/other seam is needed (#55 R-E: routing is engine-side DMN)
    for worker_cls in (
        ValidateIdentityWorker,
        ExecuteExportWorker,
        ExecuteRectificationWorker,
        ExecuteErasureWorker,
    ):
        harness.register_worker(worker_cls())
    # #55 R-B: request_additional_proof (raw handler — needs the async Kafka seam). Registered on
    # the BPMN topic so ST_PedirProvaAdicional completes and the challenge path reaches
    # GW_AguardarProva instead of hanging.
    harness.register(_REQUEST_PROOF_TOPIC, make_request_additional_proof_handler(kafka))
    # #55 R-F: send_response (raw handler — same Kafka-seam shape as R-B). Serves ST_EnviarResposta.
    harness.register(_SEND_RESPONSE_TOPIC, make_send_response_handler(kafka))
    # #55 R-G: notify_sla_risk (raw handler). Serves BOTH ST_NotificarRiscoSla (ack) and
    # ST_NotificarJuridicoBreach (resolution) via the sla_breach_phase discriminator.
    harness.register(_NOTIFY_SLA_RISK_TOPIC, make_notify_sla_risk_handler(kafka))

"""LGPD DSR workers — SP-OP-LGPD-DSR-001.

Provides BPMN external task handlers for Direitos do Titular (art. 18, LGPD).

Workers:
- ValidateIdentityWorker: verificacao de identidade (FAIL-CLOSED em identidade_verificada is True)
- AssessRequestWorker: avaliacao e roteamento da requisicao
- request_additional_proof (#55 R-B): pede prova adicional (challenge anti eng. social)
- ExecuteExportWorker: compilacao de dados para exportacao
- ExecuteRectificationWorker: retificacao de dados
- ExecuteErasureWorker: eliminacao de dados (guard: ERR_DENIAL_NOT_HUMAN)
- PublishCompletedWorker: publicacao do evento de conclusao

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
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

import structlog

from maezo.tools.workers.base import ERR_DENIAL_NOT_HUMAN, WorkerBase
from maezo.tools.workers.dmn_transport import DmnTransport, evaluate_sync, first_row, require_dmn
from maezo.tools.workers.harness import ExternalTask, WorkerBpmnError

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
# AssessRequestWorker
# ---------------------------------------------------------------------------


class AssessRequestWorker(WorkerBase):
    """External task: operadora.lgpd.assess_request

    Avalia a requisicao e determina o fluxo (EXPORTACAO, RETIFICACAO,
    ELIMINACAO_AVALIACAO, INFORMATIVO) e o grupo revisor (dpo ou
    juridico-privacidade).

    Evaluates the deployed `lgpd_dsr_routing` decision table (ADR-0028/T1.5) — replaces the
    hand-coded `fluxo_map`/`grupo_revisor` ladder that used to live here.

    golden-parity divergence found + DMN wins (documented, not patched — ADR-0028 §7): the old
    Python's `grupo_revisor` logic routed to `juridico-privacidade` only when
    `envolve_dados_saude=true` OR `tipo_requisicao` was unmapped; the deployed table's
    `eliminacao` rule (r5) ALWAYS routes to `juridico-privacidade`, REGARDLESS of
    `envolve_dados_saude` (conflict with legal retention — prontuario/ANS — is always a
    juridico matter). E.g. `tipo_requisicao="eliminacao"` + `envolve_dados_saude=False` now
    yields `grupo_revisor="juridico-privacidade"` (was `"dpo"`). Both are PHI/LGPD-adjacent —
    per ADR-0028 §7, policy-guardian review recommended before this cutover is considered
    cleared. `dmn` is injected via `__init__` (class-based `WorkerBase`, not `FunctionWorker`);
    `evaluate_sync` bridges the sync `execute()` boundary the same way `FunctionWorker`-wrapped
    modules do (T1.1 design §7).
    """

    def __init__(self, *, dmn: DmnTransport | None = None) -> None:
        super().__init__(topic="operadora.lgpd.assess_request")
        self._dmn = dmn

    def execute(self, process_vars: dict[str, Any]) -> dict[str, Any]:
        """Assess the DSR request and determine routing.

        Args:
            process_vars: Must include tipo_requisicao, envolve_dados_saude.

        Returns:
            Dict with fluxo, grupo_revisor, sla information.
        """
        tenant_id = process_vars.get("tenant_id", "")
        tipo = process_vars.get("tipo_requisicao", "")
        dados_saude = process_vars.get("envolve_dados_saude", False)

        rows, version = evaluate_sync(
            require_dmn(self._dmn, self.topic),
            "lgpd_dsr_routing",
            {"tipo_requisicao": tipo, "envolve_dados_saude": bool(dados_saude)},
        )
        row = first_row(rows, "lgpd_dsr_routing", process_vars)
        fluxo = str(row.get("fluxo", "INFORMATIVO"))
        grupo_revisor = str(row.get("grupo_revisor", "juridico-privacidade"))
        sla_resposta = row.get("sla_resposta")
        sla_alerta = row.get("sla_alerta")

        self.logger.info(
            "lgpd_request_assessed",
            tenant_id=tenant_id,
            tipo=tipo,
            fluxo=fluxo,
            grupo_revisor=grupo_revisor,
            dados_saude=dados_saude,
            dmn_decision_version=version.version,
        )

        return {
            "status": "assessed",
            "fluxo": fluxo,
            "grupo_revisor": grupo_revisor,
            "tipo_requisicao": tipo,
            "envolve_dados_saude": dados_saude,
            "sla_resposta": sla_resposta,
            "sla_alerta": sla_alerta,
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
        human_approved = process_vars.get("human_approved", False)

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
        human_approved = process_vars.get("human_approved", False)

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
    """

    def __init__(self) -> None:
        super().__init__(topic="operadora.lgpd.execute_erasure")

    def execute(self, process_vars: dict[str, Any]) -> dict[str, Any]:
        """Execute data erasure.

        Duplo guard: human_approved + decisao_dsr == EXECUTAR_E_ENVIAR.

        Args:
            process_vars: Must include titular_pseudo_id, decisao_dsr, human_approved.

        Returns:
            Dict with erasure status.
        """
        tenant_id = process_vars.get("tenant_id", "")
        decisao = process_vars.get("decisao_dsr", "")
        human_approved = process_vars.get("human_approved", False)
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

        # Execute erasure
        self.logger.info(
            "lgpd_erasure_completed",
            tenant_id=tenant_id,
        )

        return {
            "status": "erasure_completed",
            "data_type": "erasure",
            "fundamentacao_legal": fundamentacao,
            "event": "agents.events.lgpd_dsr.completed",
        }


# ---------------------------------------------------------------------------
# PublishCompletedWorker
# ---------------------------------------------------------------------------


class PublishCompletedWorker(WorkerBase):
    """External task: operadora.lgpd.publish_completed

    Publica o evento final agents.events.lgpd_dsr.completed com o desfecho.
    Chamado em todos os fins do processo (atendida, negada_fundamentada,
    identidade_inverificavel, expirada_identidade).
    """

    def __init__(self) -> None:
        super().__init__(topic="operadora.lgpd.publish_completed")

    def execute(self, process_vars: dict[str, Any]) -> dict[str, Any]:
        """Publish the completion event.

        Args:
            process_vars: Must include desfecho.

        Returns:
            Dict with published event reference.
        """
        tenant_id = process_vars.get("tenant_id", "")
        desfecho = process_vars.get("desfecho", "unknown")

        self.logger.info(
            "lgpd_dsr_completed",
            tenant_id=tenant_id,
            desfecho=desfecho,
        )

        return {
            "status": "published",
            "event": "agents.events.lgpd_dsr.completed",
            "desfecho": desfecho,
        }


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

        await kafka.publish(_NOTIFICATIONS_TOPIC, notification, key=task.business_key or None)
        logger.info(
            "lgpd_request_additional_proof_sent",
            tenant_id=tenant_id,
            canal=canal,
            business_key=task.business_key,
        )
        return {}

    return handler


# ---------------------------------------------------------------------------
# Bootstrap — donor contract (T1.2/ADR-0026 Decisao §3). NOTE (known drift, not
# introduced by this change): of `spec/processes/bpmn/SP-OP-LGPD-DSR-001_*.bpmn`'s
# external-task topics, `operadora.lgpd.verify_identity` (WorkerBase) and
# `operadora.lgpd.request_additional_proof` (#55 R-B, raw handler below) are now
# served; that BPMN's other 4 topics (compile_data_package/execute_request/
# notify_sla_risk/send_response) still have no worker — #55 R-C/R-D/R-F/R-G,
# out of scope here (no business-logic/topic edits).
# ---------------------------------------------------------------------------


def register_lgpd_workers(
    harness: WorkerHarness,
    kafka: KafkaPublisher | None = None,
    **seams: Any,
) -> None:
    """Register the SP-OP-LGPD-DSR-001 `WorkerBase` workers + the #55 R-B raw handler on `harness`.

    `dmn` (ADR-0028 §1 seam) is injected into `AssessRequestWorker` (`lgpd_dsr_routing`, T1.5
    cutover) at construction time; the other WorkerBase classes take no constructor args. `kafka`
    is threaded into the #55 R-B `request_additional_proof` raw handler (the only lgpd worker with
    a Kafka dependency).
    """
    dmn = seams.get("dmn")
    for worker_cls in (
        ValidateIdentityWorker,
        ExecuteExportWorker,
        ExecuteRectificationWorker,
        ExecuteErasureWorker,
        PublishCompletedWorker,
    ):
        harness.register_worker(worker_cls())
    harness.register_worker(AssessRequestWorker(dmn=dmn))
    # #55 R-B: request_additional_proof (raw handler — needs the async Kafka seam). Registered on
    # the BPMN topic so ST_PedirProvaAdicional completes and the challenge path reaches
    # GW_AguardarProva instead of hanging.
    harness.register(_REQUEST_PROOF_TOPIC, make_request_additional_proof_handler(kafka))

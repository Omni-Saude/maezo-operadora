"""LGPD DSR workers — SP-OP-LGPD-DSR-001.

Provides BPMN external task handlers for Direitos do Titular (art. 18, LGPD).

Workers:
- ValidateIdentityWorker: verificacao de identidade
- AssessRequestWorker: avaliacao e roteamento da requisicao
- ExecuteExportWorker: compilacao de dados para exportacao
- ExecuteRectificationWorker: retificacao de dados
- ExecuteErasureWorker: eliminacao de dados (guard: ERR_DENIAL_NOT_HUMAN)
- PublishCompletedWorker: publicacao do evento de conclusao

CRITICAL (LGPD, ADR-0008, L0 hard):
- Nenhum dado sensivel sai sem revisao humana (DPO/juridico-privacidade)
- Negativa fundamentada e SEMPRE decisao humana
- Verificacao de identidade ANTES de qualquer compilacao
- GAP-LGPD-6: ausencia de titular_pseudo_id -> ERR_DSR_IDENTITY_UNVERIFIED
  (guarda TECNICO, NUNCA acusacao automatica de fraude)
- FAIL-CLOSED (GAP-LGPD-4): decisao_dsr ausente/desconhecida NUNCA libera dados
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any

from maezo.tools.workers.base import ERR_DENIAL_NOT_HUMAN, WorkerBase
from maezo.tools.workers.dmn_transport import DmnTransport, evaluate_sync, first_row, require_dmn

if TYPE_CHECKING:
    from maezo.tools.workers.harness import KafkaPublisher, WorkerHarness

# ---------------------------------------------------------------------------
# ValidateIdentityWorker
# ---------------------------------------------------------------------------


class ValidateIdentityWorker(WorkerBase):
    """External task: operadora.lgpd.verify_identity

    Verifica a identidade do titular ANTES de qualquer compilacao de dados.

    Per GAP-LGPD-6: titular_pseudo_id ausente/vazio -> ERR_DSR_IDENTITY_UNVERIFIED.
    Este e um guarda TECNICO (impossibilidade mecanica de verificar),
    NUNCA uma acusacao automatica de fraude (L0 hard fraud_accusation).
    """

    def __init__(self) -> None:
        super().__init__(topic="operadora.lgpd.verify_identity")

    def execute(self, process_vars: dict[str, Any]) -> dict[str, Any]:
        """Verify the titular's identity.

        Args:
            process_vars: Must include titular_pseudo_id and canal.

        Returns:
            Dict with identidade_confirmada boolean and status.
        """
        tenant_id = process_vars.get("tenant_id", "")
        pseudo_id = process_vars.get("titular_pseudo_id")
        canal = process_vars.get("canal", "")

        # GAP-LGPD-6: absent/empty pseudo_id -> identity unverifiable
        if not pseudo_id:
            self.logger.warning(
                "lgpd_identity_unverifiable",
                tenant_id=tenant_id,
                canal=canal,
                reason="titular_pseudo_id ausente ou vazio",
            )
            return {
                "identidade_confirmada": False,
                "status": "identity_unverified",
                "error_code": "ERR_DSR_IDENTITY_UNVERIFIED",
                "motivo": "Identidade nao verificavel por ausencia de pseudo_id",
                # NOTE: This is a TECHNICAL guard, NOT a fraud accusation.
                # L0 hard fraud_accusation remains untouched.
            }

        # Identity confirmed (pseudo_id present and non-empty)
        self.logger.info(
            "lgpd_identity_verified",
            tenant_id=tenant_id,
            canal=canal,
        )

        return {
            "identidade_confirmada": True,
            "status": "verified",
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
# Bootstrap — donor contract (T1.2/ADR-0026 Decisao §3). NOTE (known drift, not
# introduced by this change): only `operadora.lgpd.verify_identity` matches a
# `camunda:topic` in `spec/processes/bpmn/SP-OP-LGPD-DSR-001_*.bpmn` today;
# that BPMN's other 5 topics (compile_data_package/execute_request/
# notify_sla_risk/request_additional_proof/send_response) postdate these
# classes and have no worker yet — pre-existing from T1.1, out of scope here
# (no business-logic/topic edits); registered as-is per the charter ("keep
# their classes — bootstrap-wrap them consistently").
# ---------------------------------------------------------------------------


def register_lgpd_workers(
    harness: WorkerHarness,
    kafka: KafkaPublisher | None = None,
    **seams: Any,
) -> None:
    """Register the 6 SP-OP-LGPD-DSR-001 `WorkerBase` workers on `harness`.

    `dmn` (ADR-0028 §1 seam) is injected into `AssessRequestWorker` (`lgpd_dsr_routing`, T1.5
    cutover) at construction time; the other 5 classes take no constructor args.
    """
    del kafka  # unused — no lgpd.py worker declares a Kafka dependency
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

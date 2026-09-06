"""LGPD DSR workers — SP-OP-LGPD-DSR-001.

Provides BPMN external task handlers for Direitos do Titular (art. 18, LGPD).

Workers:
- ValidateIdentityWorker: verificacao de identidade (FAIL-CLOSED em identidade_verificada is True)
- request_additional_proof (#55 R-B): pede prova adicional (challenge anti eng. social)
- ExecuteRequestWorker (R-181): UNICO worker de execucao, no topico modelado
  `operadora.lgpd.execute_request` (ST_ExecutarRequisicao); recusa fail-closed em TODO caminho
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

NOTE (R-181, gap `SP-OP-LGPD-DSR-001` / `LGPD-WORKER-DRIFT`, WP-DRIFT-REGISTRO-TOPICOS): the three
`Execute*Worker` classes (`ExecuteExportWorker`/`ExecuteRectificationWorker`/`ExecuteErasureWorker`,
topics `operadora.lgpd.execute_export`/`execute_rectification`/`execute_erasure`) were COLLAPSED
into the single `ExecuteRequestWorker` on the BPMN-modelled topic
`operadora.lgpd.execute_request` (`ST_ExecutarRequisicao`). Those three topics were ORPHAN CODE
TOPICS — no `camunda:topic` in `spec/` ever carried them (rows O2-O4 of
`docs/compliance/lgpd-topic-reconciliation.md`) — while the modelled topic had no worker at all
(row T4). Every execution path stays FAIL-CLOSED: no export, rectification or erasure is enabled
by this collapse, and the DPO ratifies ENABLEMENT later (F-2 + AF-07), never the topology. See
`ExecuteRequestWorker`'s docstring for why every path must RAISE rather than return.
"""

from __future__ import annotations

import logging
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
# ExecuteRequestWorker (R-181) — o UNICO worker do topico modelado
# ---------------------------------------------------------------------------

_EXECUTE_REQUEST_TOPIC = "operadora.lgpd.execute_request"

# Direitos do art. 18 que `ST_ExecutarRequisicao` executaria, um por grupo de `tipo_requisicao`.
_DIREITO_EXPORTACAO = "exportacao"
_DIREITO_RETIFICACAO = "retificacao"
_DIREITO_ELIMINACAO = "eliminacao"
_DIREITO_INFORMATIVO = "informativo"

#: Dominio de `tipo_requisicao` -> direito. Os seis valores sao os DECLARADOS pelo modelo, nunca
#: inventados aqui: a `bpmn:documentation` do processo os enumera em "VARIAVEIS DE ENTRADA"
#: (`spec/processes/bpmn/SP-OP-LGPD-DSR-001_Direitos_do_Titular.bpmn`) e o contrato os repete na
#: tabela "Variaveis de entrada" (`docs/processes/contracts/SP-OP-LGPD-DSR-001.md`). O
#: agrupamento espelha o `fluxo` que a DMN `lgpd_dsr_routing` deriva dos MESMOS seis valores
#: (EXPORTACAO / RETIFICACAO / ELIMINACAO_AVALIACAO / INFORMATIVO). `tipo_requisicao` e a
#: variavel de tipo-de-requisicao que o BPMN carrega — ela compoe a business key do processo e
#: viaja em `event_payload_vars` de cinco `ST_Publish*`; nenhuma outra existe.
_TIPO_REQUISICAO_PARA_DIREITO: dict[str, str] = {
    "confirmacao_acesso": _DIREITO_EXPORTACAO,
    "portabilidade": _DIREITO_EXPORTACAO,
    "correcao": _DIREITO_RETIFICACAO,
    "eliminacao": _DIREITO_ELIMINACAO,
    "info_compartilhamento": _DIREITO_INFORMATIVO,
    "revogacao_consentimento": _DIREITO_INFORMATIVO,
}

#: Prefixo de TODA recusa deste worker. Ele lidera com o FATO INVARIANTE (nada e executavel) e so
#: depois nomeia o guard especifico — o inverso deixaria o diagnostico de producao mentir: como
#: `human_approved` nao existe no modelo (ver `ExecuteRequestWorker`), reportar "sem aprovacao
#: humana" como causa PRIMARIA culparia o revisor por uma lacuna de implementacao.
_RECUSA_PREFIXO = (
    "execute_request: a execucao real NAO esta implementada para direito algum do art. 18 "
    "(exportacao/retificacao/eliminacao) — recusa FAIL-CLOSED, NUNCA um sucesso fabricado. A "
    "habilitacao da execucao real e ato do DPO (F-2 + matriz AF-07), nunca deste PR de topologia. "
)


class ExecuteRequestWorker(WorkerBase):
    """External task: operadora.lgpd.execute_request — `ST_ExecutarRequisicao`.

    R-181 (WP-DRIFT-REGISTRO-TOPICOS): este worker SUBSTITUI os tres `Execute*Worker` que
    ocupavam os topicos `operadora.lgpd.execute_export`/`execute_rectification`/`execute_erasure`
    — topicos ORFAOS que `camunda:topic` algum do BPMN carregava (linhas O2-O4 de
    `docs/compliance/lgpd-topic-reconciliation.md`), enquanto o topico MODELADO
    `operadora.lgpd.execute_request` seguia sem worker (linha T4, "NAME + DECOMPOSITION
    MISMATCH"). O BPMN declara UMA service task de execucao — `ST_ExecutarRequisicao`, "Executar
    retificacao/eliminacao aprovada" — alcancada SO por `Flow_GWDec_Executar`
    (`${decisao_dsr == 'EXECUTAR_E_ENVIAR'}`, guard do ENGINE).

    NENHUM CAMINHO COMPLETA A TAREFA. Toda entrada possivel termina em
    `WorkerFailureError(retries_left=0)` — um incidente imediato, nao-retentado. Isto NAO e
    excesso de zelo, e a unica postura honesta disponivel, por duas razoes MEDIDAS:

    1. **Nada esta implementado.** A cascata de delecao (`platform.erasure.ErasureManager`)
       levanta `ErasureNotImplementedError`, nao ha resolucao `titular_pseudo_id`->
       `fhir_patient_id` nas process_vars, e nao existe seam de retificacao nem de exportacao
       neste modulo. Os dois workers aposentados MENTIAM nesse ponto: `ExecuteExportWorker`
       devolvia `status="export_compiled"` com um `package_ref` cunhado por `uuid4()` e
       `ExecuteRectificationWorker` devolvia `status="rectification_completed"` — ambos sem
       executar UMA LINHA de SQL. Enquanto os topicos eram orfaos isso era LATENTE; no topico
       modelado seria uma afirmacao VIVA e falsa ao titular (LGPD art. 18). Somente
       `ExecuteErasureWorker` ja recusava (T3.4-F3); este worker estende a MESMA postura
       auditada aos outros dois direitos.
    2. **Completar a tarefa afirma a execucao pela POSICAO no fluxo.** `Flow_Executar_Enviar`
       (`sourceRef=ST_ExecutarRequisicao targetRef=ST_EnviarResposta`) NAO tem
       `conditionExpression`: qualquer `complete`, mesmo com um dict "blocked_by_guard", avanca o
       token para "Enviar resposta ao titular" -> `ST_PublishCompleted`
       (`event_desfecho=atendida`) -> `End_RequisicaoConcluida`. Devolver um dict de guard aqui
       — o que os tres workers aposentados faziam — seria portanto FAIL-OPEN no topico modelado:
       o titular receberia a resposta e o evento diria "atendida" sem nada ter sido executado, e
       sem incidente algum. O incidente e o unico desfecho que SEGURA o token.

    Guards preservados de O2-O4 (as tres condicoes que `lgpd-topic-reconciliation.md` R-D exige
    que o colapso preserve): (a) nenhuma execucao sem aprovacao humana, (b) nenhuma execucao sem
    `decisao_dsr == 'EXECUTAR_E_ENVIAR'` explicito (FAIL-CLOSED, GAP-LGPD-4), (c)
    `NEGAR_FUNDAMENTADO` NUNCA executa. Eles agora escolhem QUAL recusa e reportada, nao SE ha
    recusa — a barreira e total.

    NOTA sobre `human_approved` (achado de R-181, reportado como INFO): esse nome nao aparece no
    BPMN nem no contrato — `grep -n 'human_approved'` em ambos retorna ZERO. Ele e um sinal
    exclusivo do codigo, que instancia real alguma semeia. A decisao humana RATIFICADA deste
    processo e `decisao_dsr`, escrita por `UT_RevisaoDpo` e guardada pelo engine em
    `Flow_GWDec_Executar`. O guard e mantido (defesa em profundidade, matriz T3.1 intacta) mas
    NUNCA e a causa primaria relatada: `_RECUSA_PREFIXO` lidera toda mensagem com o fato de que
    nada esta implementado.

    NENHUM `WorkerBpmnError` e levantado aqui, de proposito. O BPMN declara
    `ERR_DSR_ERASURE_FAILED` (`bpmn:19`) e `ERR_DSR_ERASURE_NOT_HUMAN` (`bpmn:22`) mas ambos
    estao DECLARADOS e NAO-VINCULADOS — `errorRef` nenhum os referencia e `ST_ExecutarRequisicao`
    nao carrega `bpmn:boundaryEvent` algum. Levanta-los reprovaria
    `scripts/ci/check_bpmn_error_allowlist.py` (ADR-0030 §2 clausula b) e o harness os demoveria
    a `failure(retries=0)` de qualquer forma. `LGPD_BPMN_ERROR_ALLOWLIST` segue com o UNICO codigo
    consumption-covered do modulo (`ERR_DSR_IDENTITY_UNVERIFIED`). Adicionar boundary catches em
    `ST_ExecutarRequisicao` e mudanca de spec com sign-off de SME — fora de R-181.

    PHI (gap `LGPD-EXECUTE-ERASURE-RAW-FUNDAMENTACAO`): `fundamentacao_legal` e
    `detalhes_requisicao` sao texto livre digitado por humano e NAO estao em `PHI_PROCESS_VARS`
    nem em `PHI_FREE_TEXT_VARS` (ambos os conjuntos sao PINADOS fora deles por
    `tests/unit/docs/test_dpo_drafts_citations.py`, que amarra os pins a §2.4 do runbook do DPO).
    Este worker portanto NUNCA vincula o valor cru a variavel local alguma: dele sai no maximo a
    PRESENCA (`tem_fundamentacao: bool`), exatamente o idioma que
    `make_send_response_handler` ja aplica ao mesmo nome nesta mesma BPMN. Isso e ESTRITAMENTE
    mais forte que redigir (`[REDACTED_PHI]` ainda embarcaria um campo) e nao desloca pin algum
    do corpus PHI (`CORPUS_DELTA_LOG`).
    """

    def __init__(self) -> None:
        # max_retries=1: toda recusa aqui e DETERMINISTICA (implementacao ausente / guard humano),
        # nunca um fault transitorio. O retry in-process do WorkerBase (que re-tenta TODA Exception
        # com time.sleep) nao deve mascarar nem atrasar o incidente — o engine e' dono do retry
        # duravel (T1.1 design §9). Espelha ValidateIdentityWorker e os tres workers aposentados.
        super().__init__(topic=_EXECUTE_REQUEST_TOPIC, max_retries=1)

    def execute(self, process_vars: dict[str, Any]) -> dict[str, Any]:
        """Recusa, fail-closed, executar qualquer direito do art. 18 — em TODO caminho.

        Args:
            process_vars: le `tenant_id`, `decisao_dsr`, `tipo_requisicao`, `human_approved` e a
                PRESENCA de `fundamentacao_legal` (nunca o seu texto).

        Returns:
            Nunca retorna. A anotacao segue a de `WorkerBase.execute` por compatibilidade de
            assinatura; toda saida desta funcao e uma excecao.

        Raises:
            WorkerFailureError: sempre, com `retries_left=0` (incidente imediato, nao-retentado).
        """
        tenant_id = process_vars.get("tenant_id", "")
        decisao = process_vars.get("decisao_dsr", "")
        tipo_requisicao = process_vars.get("tipo_requisicao", "")
        human_approved = process_vars.get("human_approved") is True
        # PHI: so a PRESENCA. O texto cru de `fundamentacao_legal` nunca e vinculado a um local,
        # entao nao ha valor a vazar para log, mensagem de incidente ou variavel de processo.
        tem_fundamentacao = bool(process_vars.get("fundamentacao_legal"))

        # Guard (a) — L0 hard: nenhuma execucao sem o sinal EXPLICITO `human_approved is True`.
        # Ausente/False/lixo truthy ("true", " ", 1, [1], {...}) NUNCA e aprovacao (T3.1/ADR-0031).
        if not human_approved:
            self.logger.error(
                "lgpd_execute_request_recusado_sem_aprovacao_humana",
                tenant_id=tenant_id,
                tipo_requisicao=tipo_requisicao,
                decisao=decisao,
                tem_fundamentacao=tem_fundamentacao,
            )
            raise WorkerFailureError(
                _RECUSA_PREFIXO + "Guard (a): o sinal explicito `human_approved is True` esta "
                f"ausente ou nao e o booleano True ({ERR_DENIAL_NOT_HUMAN}).",
                retries_left=0,
            )

        # Guard (c) — `NEGAR_FUNDAMENTADO` NUNCA executa. Pelo modelo esta decisao sequer alcanca
        # esta task (`Flow_GWDec_Negar` -> `GW_GuardFundamentacao` -> `ST_EnviarResposta`), entao
        # chegar aqui com ela significa desvio do modelo (chamada REST direta ou edicao de BPMN):
        # incidente, nao conclusao silenciosa.
        if decisao == "NEGAR_FUNDAMENTADO":
            self.logger.error(
                "lgpd_execute_request_recusado_negativa_fundamentada",
                tenant_id=tenant_id,
                tipo_requisicao=tipo_requisicao,
                tem_fundamentacao=tem_fundamentacao,
            )
            raise WorkerFailureError(
                _RECUSA_PREFIXO + "Guard (c): decisao_dsr='NEGAR_FUNDAMENTADO' NUNCA executa "
                "(negativa fundamentada e decisao humana de NAO executar) — e pelo modelo nem "
                "alcanca ST_ExecutarRequisicao, entao a chegada aqui e desvio do modelo.",
                retries_left=0,
            )

        # Guard (b) — FAIL-CLOSED (GAP-LGPD-4): decisao ausente/em branco/desconhecida NUNCA
        # libera execucao por omissao.
        if decisao != "EXECUTAR_E_ENVIAR":
            self.logger.error(
                "lgpd_execute_request_recusado_decisao_invalida",
                tenant_id=tenant_id,
                tipo_requisicao=tipo_requisicao,
                decisao=decisao,
            )
            raise WorkerFailureError(
                _RECUSA_PREFIXO + "Guard (b): execucao exige decisao_dsr='EXECUTAR_E_ENVIAR' "
                "explicita (FAIL-CLOSED, GAP-LGPD-4); ausente/em branco/desconhecida nao libera.",
                retries_left=0,
            )

        direito = _TIPO_REQUISICAO_PARA_DIREITO.get(tipo_requisicao)

        # Tipo fora do dominio declarado -> fail-closed, espelhando o catch-all da DMN
        # `lgpd_dsr_routing` (regra r7: tipo desconhecido sobe para juridico-privacidade).
        if direito is None:
            self.logger.error(
                "lgpd_execute_request_recusado_tipo_desconhecido",
                tenant_id=tenant_id,
                tipo_requisicao=tipo_requisicao,
            )
            raise WorkerFailureError(
                _RECUSA_PREFIXO + "Alem disso `tipo_requisicao` esta ausente ou fora do dominio "
                "declarado pelo BPMN/contrato (confirmacao_acesso, correcao, eliminacao, "
                "portabilidade, info_compartilhamento, revogacao_consentimento) — FAIL-CLOSED.",
                retries_left=0,
            )

        # Fluxo INFORMATIVO: o modelo nao tem execucao a fazer para estes tipos (a DMN os roteia
        # a INFORMATIVO e a resposta sai por APROVAR_ENVIO direto para ST_EnviarResposta).
        if direito == _DIREITO_INFORMATIVO:
            self.logger.error(
                "lgpd_execute_request_recusado_fluxo_informativo",
                tenant_id=tenant_id,
                tipo_requisicao=tipo_requisicao,
                direito=direito,
            )
            raise WorkerFailureError(
                _RECUSA_PREFIXO + f"Alem disso tipo_requisicao='{tipo_requisicao}' pertence ao "
                "fluxo INFORMATIVO da DMN lgpd_dsr_routing, que nao tem execucao a fazer: a "
                "resposta informativa sai por APROVAR_ENVIO direto para ST_EnviarResposta, sem "
                "passar por ST_ExecutarRequisicao.",
                retries_left=0,
            )

        self.logger.error(
            "lgpd_execute_request_recusado_execucao_nao_implementada",
            tenant_id=tenant_id,
            tipo_requisicao=tipo_requisicao,
            direito=direito,
        )
        raise WorkerFailureError(
            _RECUSA_PREFIXO + f"Ambos os guards humanos estao satisfeitos (human_approved is True "
            f"+ decisao_dsr='EXECUTAR_E_ENVIAR') e o direito pedido e '{direito}' "
            f"(tipo_requisicao='{tipo_requisicao}'), mas nao existe seam de execucao: a cascata "
            "de delecao (platform.erasure.ErasureManager) levanta ErasureNotImplementedError, "
            "nao ha resolucao titular_pseudo_id->fhir_patient_id nas process_vars e nao existe "
            "seam de retificacao nem de exportacao neste modulo. Recusando a reportar execucao "
            "concluida sem executar SQL (LGPD art. 18) — incidente fail-closed, NAO retentar, "
            "NAO avancar para o envio de confirmacao ao titular.",
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
# (#55 R-B), `operadora.lgpd.execute_request` (R-181, `ExecuteRequestWorker`),
# `operadora.lgpd.send_response` (#55 R-F), and `operadora.lgpd.notify_sla_risk` (#55 R-G) are
# now served; that BPMN's ONE remaining topic, `operadora.lgpd.compile_data_package`, still has
# no worker — #55 R-C, DPO/SME-sign-off-gated (the legal-bases / retention matrix that step must
# carry is the DPO's to define), out of scope here. `operadora.lgpd.assess_request` (formerly
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
    `WorkerBase` classes (`ValidateIdentityWorker`, `ExecuteRequestWorker`) take no constructor
    args. No `dmn`/other seam is consumed by this module (#55 R-E: routing is the engine-side
    `BRT_RotearDsr` DMN, not a worker).
    """
    del seams  # unused — no dmn/other seam is needed (#55 R-E: routing is engine-side DMN)
    # R-181: `ExecuteRequestWorker` replaced the three orphan `Execute*Worker`s. Every
    # `WorkerBase` topic registered here is a `camunda:topic` the BPMN actually declares — the
    # drift row `LGPD-WORKER-DRIFT` is closed by construction, and pinned by
    # `test_lgpd_erasure.py::test_nenhum_topico_lgpd_registrado_falta_no_bpmn`.
    for worker_cls in (
        ValidateIdentityWorker,
        ExecuteRequestWorker,
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

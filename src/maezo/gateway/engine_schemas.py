"""Reviewed D7-A rows, traced to contract + caller + BPMN, never a runtime wildcard.

These are field contracts, not deployed grants. No production profile is installed. A row may
only be bound to the exact identity/definition/version in a reviewed deployment manifest.
Remaining worker/bridge rows require progressive contract review before B/C can grant them.
"""

from __future__ import annotations

from dataclasses import replace

from maezo.gateway.engine_contracts import (
    EngineCapabilityError,
    EngineOperation,
    EngineRefusalCode,
    EngineSchema,
    FieldOrigin,
    ValueKind,
    VariableField,
)


def _fields(
    names: str,
    kind: ValueKind = ValueKind.STRING,
    *,
    required: bool = False,
    origin: FieldOrigin = FieldOrigin.FACT,
) -> tuple[VariableField, ...]:
    return tuple(VariableField(n, kind, required, origin) for n in names.split())


_TENANT = _fields("tenant_id", required=True, origin=FieldOrigin.BOUND)
_AGENT = _TENANT + _fields("source_agent_id source_agent_version", required=True, origin=FieldOrigin.BOUND)
_REFS = _fields("dmn_decision_refs", ValueKind.JSON)
_ROUTING = _fields("motivo_encaminhamento grupo_destino")


def _dossier(agent: str, nulls: str) -> tuple[VariableField, ...]:
    return (VariableField(f"dossie_{agent}", ValueKind.JSON, null_members=tuple(nulls.split())),)


def _agent(
    agent: str, family: str, filename: str, builder: str, fields: tuple[VariableField, ...]
) -> EngineSchema:
    key = f"SP-OP-{family}-001"
    return EngineSchema(
        f"{agent}.{family.lower()}.start.v1",
        EngineOperation.START,
        key,
        agent,
        _AGENT + fields,
        (
            f"docs/processes/contracts/{key}.md#variaveis-de-entrada",
            f"docs/processes/contracts/{key}.md#variaveis-de-proveniencia-do-agente",
            f"spec/agents/{agent}/agent.yaml",
            f"src/maezo/agents/{agent}/graph.py:{builder}",
            f"spec/processes/bpmn/{key}_{filename}.bpmn",
        ),
    )


_ESC = _fields(
    "conversation_id beneficiario_pseudo_id canal motivo_categoria severidade resumo_contexto", required=True
) + _fields("dmn_decision_ref")
HELENA_START = _agent("helena", "ESCALATION", "Escalonamento_Humano_Universal", "_start_escalation", _ESC)
LUCAS_START = _agent(
    "lucas",
    "ESCALATION",
    "Escalonamento_Humano_Universal",
    "_escalation_variables",
    _ESC
    + _REFS
    + _fields("lucas_route motivo_encaminhamento grupo_humano_sugerido")
    + _dossier("lucas", "decisao_cancelamento"),
)
RAFAEL_START = _agent(
    "rafael",
    "AUTH",
    "Autorizacao_Previa",
    "_contract_variables",
    _fields(
        "numero_guia_tiss beneficiario_pseudo_id prestador_id codigo_procedimento_tuss "
        "categoria_procedimento carater_atendimento",
        required=True,
    )
    + _fields("valor_estimado_brl", ValueKind.DOUBLE, required=True)
    + _fields("documentos_refs", ValueKind.JSON, required=True)
    + _fields(
        "requer_autorizacao documentacao_completa beneficiario_ativo carencia_cumprida "
        "dut_atendida dentro_teto_l2 rede_credenciada",
        ValueKind.BOOLEAN,
        required=True,
    )
    + _fields("cid10 rafael_route motivo_encaminhamento")
    + _REFS
    + _dossier("rafael", "decisao_cobertura"),
)
CAROLINA_START = _agent(
    "carolina",
    "CRED",
    "Descredenciamento",
    "_contract_variables",
    _fields("prestador_id direcao tipo_prestador origem_solicitacao data_solicitacao_iso", required=True)
    + _fields("documentos_refs", ValueKind.JSON, required=True)
    + _fields(
        "licenca_valida documentacao_completa dentro_criterios_rede indicio_irregularidade_sinalizado",
        ValueKind.BOOLEAN,
        required=True,
    )
    + _fields("protocolo_cred motivo_informado regiao_saude especialidade carolina_route")
    + _fields(
        "notificacao_previa_feita tem_beneficiarios_vinculados substituto_equivalente_identificado",
        ValueKind.BOOLEAN,
    )
    + _ROUTING
    + _REFS
    + _dossier("carolina", "decisao_credenciamento decisao_descredenciamento decisao_cred"),
)
FERNANDO_START = _agent(
    "fernando",
    "INADIMPLENCIA",
    "Suspensao_Rescisao",
    "_inadimplencia_variables",
    _fields(
        "numero_contrato matricula_beneficiario beneficiario_pseudo_id tipo_plano origem_solicitacao "
        "canal motivo_categoria resumo_contexto",
        required=True,
    )
    + _fields("competencias_em_aberto documentos_refs", ValueKind.JSON, required=True)
    + _fields("meses_inadimplencia valor_total_devido_cents", ValueKind.INTEGER, required=True)
    + _fields(
        "dentro_periodo_minimo notificacao_previa_feita dentro_janela_purga", ValueKind.BOOLEAN, required=True
    )
    + _fields("ja_em_rescisao_cancel", ValueKind.BOOLEAN)
    + _fields("fernando_route motivo_encaminhamento data_solicitacao_iso dmn_decision_ref")
    + _REFS
    + _fields("decisao_inadimplencia", origin=FieldOrigin.FIXED_NULL)
    + _dossier("fernando", "decisao_inadimplencia decisao_recomendada"),
)
VALENTINA_START = _agent(
    "valentina",
    "PROGRAMA",
    "Programas_Cuidado",
    "_contract_variables",
    _fields("programa_id beneficiario_pseudo_id ciclo gatilho consent_scope", required=True)
    + _fields(
        "consentimento_ativo consent_checked elegibilidade_criterios_atendidos criterio_alta_aparente",
        ValueKind.BOOLEAN,
        required=True,
    )
    + _fields(
        "consent_status elegivel_programa risco_estratificado valentina_task valentina_route "
        "proactive_trigger_ref consent_event_ref patient_summary_ref"
    )
    + _fields(
        "decisao_programa motivo_desligamento_clinico referencia_clinica responsavel_clinico_id",
        origin=FieldOrigin.FIXED_NULL,
    )
    + _ROUTING
    + _REFS
    + _dossier(
        "valentina",
        "decisao_clinica decisao_desligamento decisao_programa motivo_desligamento_clinico "
        "referencia_clinica responsavel_clinico_id",
    ),
)
_GUSTAVO = _fields("gustavo_route motivo_encaminhamento") + _REFS
GUSTAVO_NIP_START = _agent(
    "gustavo",
    "NIP",
    "Resposta_NIP",
    "_contract_variables[fluxo=nip]",
    _GUSTAVO
    + _dossier("gustavo", "decisao_merito decisao_nip")
    + _fields(
        "numero_nip_ans beneficiario_pseudo_id classificacao_nip tema_nip "
        "data_recebimento_nip_iso prazo_resposta_iso",
        required=True,
    )
    + _fields("documentos_refs", ValueKind.JSON, required=True)
    + _fields("contesta_negativa documentacao_suficiente origem_a2a", ValueKind.BOOLEAN, required=True)
    + _fields("protocolo_ans referencia_negativa_original")
    + _fields("decisao_nip", origin=FieldOrigin.FIXED_NULL),
)
GUSTAVO_ANS_START = _agent(
    "gustavo",
    "ANS-SUBMIT",
    "Envios_Periodicos_ANS",
    "_contract_variables[fluxo=ans_submit]",
    _GUSTAVO
    + _dossier("gustavo", "decisao_merito decisao_envio")
    + _fields("report_type competencia periodicidade origem_envio dataset_ref due_date", required=True)
    + _fields("dataset_complete schema_valid lgpd_anonimizado", ValueKind.BOOLEAN, required=True)
    + _fields("nip_protocolo_origem")
    + _fields("decisao_envio", origin=FieldOrigin.FIXED_NULL),
)
_MARINA = (
    _fields("beneficiario_pseudo_id prestador_id marina_flow marina_route", required=True) + _ROUTING + _REFS
)
MARINA_CONTAS_START = _agent(
    "marina",
    "CONTAS",
    "Processamento_Contas_Glosa",
    "_contract_variables[flow=contas]",
    _MARINA
    + _dossier("marina", "decisao_glosa decisao_recurso decisao_reembolso decisao_contas")
    + _fields("numero_lote_tiss competencia tipo_lote", required=True)
    + _fields("valor_apresentado_brl", ValueKind.DOUBLE, required=True)
    + _fields("linhas_conta_refs reason_codes_tiss", ValueKind.JSON, required=True)
    + _fields(
        "divergencia_valor item_conforme_tabela documentacao_anexa indicio_fraude_sinalizado",
        ValueKind.BOOLEAN,
        required=True,
    )
    + _fields("data_recebimento_lote numero_guia_tiss numero_conta"),
)
MARINA_RECURSO_START = _agent(
    "marina",
    "RECURSO",
    "Recurso_Glosa",
    "_contract_variables[flow=recurso]",
    _MARINA
    + _dossier("marina", "decisao_glosa decisao_recurso decisao_reembolso")
    + _fields(
        "numero_guia_tiss glosa_id numero_lote_tiss glosa_type glosa_reason_code "
        "codigo_procedimento_tuss data_recebimento_recurso_iso",
        required=True,
    )
    + _fields("valor_glosado_brl", ValueKind.DOUBLE, required=True)
    + _fields("documentos_recurso_refs", ValueKind.JSON, required=True)
    + _fields(
        "glosa_existe dentro_prazo_recurso documentacao_recurso_completa", ValueKind.BOOLEAN, required=True
    )
    + _fields("cid10 data_ciencia_alegada_prestador"),
)

# Deliberately no Andre start row: current builder seeds `lastro_confirmado`, prohibited by
# PAGTO inputs/I-PAGTO-1. Its `faixa_valor`/`grupo_aprovador` are output/DMN-owned fields. C must
# reconcile that caller with B; neither accepting nor silently stripping them belongs to A.
_PAGTO_INPUT = (
    _TENANT
    + _fields(
        "ordem_pagamento_id prestador_id tipo_pagamento moeda competencia data_vencimento conta_origem_ref "
        "instrumento_pagamento fonte_valor lastro_origem",
        required=True,
    )
    + _fields("valor_pagamento_cents", ValueKind.INTEGER, required=True)
    + _fields(
        "numero_lote_tiss numero_guia_tiss glosa_id",
    )
    + _fields("lastro_decisor_id", required=True, origin=FieldOrigin.PRIOR_HUMAN)
)


def _payment_handoff(source: str) -> EngineSchema:
    return EngineSchema(
        f"{source}.pagto.handoff.v1",
        EngineOperation.START,
        "SP-OP-PAGTO-001",
        "worker_runtime",
        tuple(
            replace(f, fixed_json=b'"BRL"')
            if f.name == "moeda"
            else replace(f, empty_evidence_when=("lastro_origem", b'"contas_adjudicacao_automatica"'))
            if f.name == "lastro_decisor_id" and source == "contas"
            else replace(f, origin=FieldOrigin.ENGINE)
            if f.name in ("fonte_valor", "lastro_origem")
            else replace(f, fixed_json=b'"prestador_rede"' if source == "contas" else b'"glosa_revertida"')
            if f.name == "tipo_pagamento"
            else f
            for f in _PAGTO_INPUT
        ),
        (
            "docs/processes/contracts/SP-OP-PAGTO-001.md#variaveis-de-entrada",
            f"docs/processes/contracts/SP-OP-{source.upper()}-001.md",
            f"src/maezo/tools/workers/{source}.py:handoff_pagamento",
            "spec/processes/bpmn/SP-OP-PAGTO-001_Pagamentos_Alcada.bpmn",
            f"spec/processes/bpmn/SP-OP-{source.upper()}-001_"
            + ("Processamento_Contas_Glosa.bpmn" if source == "contas" else "Recurso_Glosa.bpmn"),
        ),
        source_process_key=f"SP-OP-{source.upper()}-001",
        source_topic=f"operadora.{source}.handoff_pagamento",
        audit_actor="operadora-worker",
    )


CONTAS_PAGTO_START = _payment_handoff("contas")
RECURSO_PAGTO_START = _payment_handoff("recurso")

# Contracted successor: the bridge module is ABSENT on this SHA. This row preserves the legal
# tenant/beneficiary fan-out semantics for B/C, without instantiating a bridge or workload grant.
CONSENT_REVOKED = EngineSchema(
    "consent_revocation_bridge.programa.correlate.v1",
    EngineOperation.CORRELATE,
    "SP-OP-PROGRAMA-001",
    "consent_revocation_bridge",
    _fields("consent_event_ref", required=True, origin=FieldOrigin.PRIOR_HUMAN),
    (
        "docs/processes/contracts/SP-OP-PROGRAMA-001.md#design-consent-chokepoint",
        "docs/processes/contracts/SP-OP-LGPD-DSR-001.md",
        "spec/processes/bpmn/SP-OP-PROGRAMA-001_Programas_Cuidado.bpmn",
        "deploy/helm/maezo-tenant/templates/deployment-bridge-consent-revocation.yaml",
    ),
    message="msg.programa.consent_revoked",
    correlation_fields=("tenant_id", "beneficiario_pseudo_id"),
    all_matching=True,
    source_process_key="SP-OP-LGPD-DSR-001",
)
CONTAS_IMPACT_COMPLETE = EngineSchema(
    "contas.calculate_impact.complete.v1",
    EngineOperation.COMPLETE,
    "SP-OP-CONTAS-001",
    "worker_runtime",
    _fields(
        "denial_ratio total_glosado_candidato_brl valor_apresentado_brl impacto_percentual",
        ValueKind.DOUBLE,
        required=True,
    )
    + _fields("divergencia_valor", ValueKind.BOOLEAN, required=True)
    + _fields("total_glosado_candidato_centavos glosa_count", ValueKind.INTEGER, required=True),
    (
        "docs/processes/contracts/SP-OP-CONTAS-001.md#fatos-computados",
        "src/maezo/tools/workers/contas.py:calculate_impact_entry",
        "spec/processes/bpmn/SP-OP-CONTAS-001_Processamento_Contas_Glosa.bpmn#ST_CalculateImpact",
    ),
    topic="operadora.contas.calculate_impact",
    audit_actor="operadora-worker",
    read_projection=(
        "denial_ratio",
        "divergencia_valor",
        "total_glosado_candidato_centavos",
        "glosa_count",
        "valor_apresentado_brl",
    ),
)

ESCALATION_NOTIFY_ERROR = EngineSchema(
    "escalation.notify_team.bpmn_error.v1",
    EngineOperation.BPMN_ERROR,
    "SP-OP-ESCALATION-001",
    "worker_runtime",
    (),
    (
        "docs/processes/contracts/SP-OP-ESCALATION-001.md",
        "src/maezo/tools/workers/escalation.py:make_notify_team_handler",
        "spec/processes/bpmn/SP-OP-ESCALATION-001_Escalonamento_Humano_Universal.bpmn#BE_FalhaNotificacao",
    ),
    topic="operadora.escalation.notify_team",
    error_codes=("ERR_ESC_NOTIFY_FAILED",),
    audit_actor="operadora-worker",
    read_projection=("tenant_id", "severidade", "grupo_atendimento", "prioridade", "motivo_categoria"),
)

SCHEMAS: tuple[EngineSchema, ...] = (
    HELENA_START,
    LUCAS_START,
    RAFAEL_START,
    CAROLINA_START,
    FERNANDO_START,
    VALENTINA_START,
    GUSTAVO_NIP_START,
    GUSTAVO_ANS_START,
    MARINA_CONTAS_START,
    MARINA_RECURSO_START,
    CONTAS_PAGTO_START,
    RECURSO_PAGTO_START,
    CONSENT_REVOKED,
    CONTAS_IMPACT_COMPLETE,
    ESCALATION_NOTIFY_ERROR,
)


def schema_by_id(schema_id: str) -> EngineSchema:
    for schema in SCHEMAS:
        if schema.schema_id == schema_id:
            return schema
    raise EngineCapabilityError(EngineRefusalCode.PROFILE_UNAVAILABLE)


def start_read_schema(start: EngineSchema, operation: EngineOperation) -> EngineSchema:
    """Companion read grant for dedup only; resource authorization still belongs to B's resolver."""
    if start.operation is not EngineOperation.START or operation not in (
        EngineOperation.READ_ACTIVE,
        EngineOperation.READ_HISTORY,
    ):
        raise EngineCapabilityError(EngineRefusalCode.OPERATION_DENIED)
    return replace(start, schema_id=f"{start.schema_id}.{operation.value}", operation=operation, fields=())


def worker_lifecycle_schema(worker: EngineSchema, operation: EngineOperation) -> EngineSchema:
    """Same reviewed topic/resource for lifecycle; never upgrades to an arbitrary variable write."""
    if (
        worker not in SCHEMAS
        or not worker.topic
        or operation
        not in (
            EngineOperation.FETCH_LOCK,
            EngineOperation.FAILURE,
            EngineOperation.EXTEND_LOCK,
            EngineOperation.UNLOCK,
        )
    ):
        raise EngineCapabilityError(EngineRefusalCode.OPERATION_DENIED)
    return replace(
        worker,
        schema_id=f"{worker.schema_id}.{operation.value}",
        operation=operation,
        fields=(),
        error_codes=(),
    )


def registered_schema(schema: EngineSchema) -> bool:
    """A configured profile cannot widen reviewed rows or invent an anonymous/admin row."""
    if type(schema) is not EngineSchema:
        return False
    for base in SCHEMAS:
        if schema == base:
            return True
        if base.operation is EngineOperation.START:
            for operation in (EngineOperation.READ_ACTIVE, EngineOperation.READ_HISTORY):
                if schema == start_read_schema(base, operation):
                    return True
        if base.topic:
            for operation in (
                EngineOperation.FETCH_LOCK,
                EngineOperation.FAILURE,
                EngineOperation.EXTEND_LOCK,
                EngineOperation.UNLOCK,
            ):
                if schema == worker_lifecycle_schema(base, operation):
                    return True
    return False

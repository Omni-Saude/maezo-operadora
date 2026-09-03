"""SP-OP-RECURSO-001 — suite de integracao executavel (engine CIB Seven REAL).

Implementa o test-spec do W5 (docs/processes/test-specs/SP-OP-RECURSO-001.md) contra o engine
real (ADR-0011: SEM mock de engine). Cada teste:

1. inicia a instancia via REST com business key `RECURSO-amh-{numero_guia_tiss}-{glosa_id}`;
2. drena as external tasks com o `recurso_probe` (workers reais Phase-2 + generic publish);
3. avanca o HITL completando User Tasks como humano sintetico (caminho HITL permitido);
4. dispara timers via job execution (NUNCA sleep);
5. verifica invariantes via historia do engine (garantia de que o INDEFERIMENTO passa pela UT
   humana).

## PERSPECTIVA (ADR-0040) — o que esta suite passou a provar

O dono do processo e a OPERADORA. Ela RECEBE o recurso que o prestador interpos contra uma glosa
que ela mesma aplicou, e EMITE a resposta. A fase de recorrente que existia neste BPMN
(`ST_SubmitAppeal`, `GW_AguardarResposta`, `ST_TrackStatus`, `ST_ReconcilePayment*`, a mensagem
`msg.recurso.resposta_recebida` e o loop de acompanhamento) foi REMOVIDA sem shim — junto com os
testes que a exercitavam. `test_loop_acompanhamento_limitado` MORREU SEM SUBSTITUTO, e isso e
deliberado: o comportamento que ele testava nao deve existir. E a unica perda liquida de cobertura
do pacote.

Dados sinteticos obvios: guia `GUIA-TESTE-0001`, glosa `GLOSA-TESTE-NNNN`, lote `LOTE-TESTE-0001`,
prestador `PREST-TESTE-001`, beneficiario pseudonimizado `PSEUDO-TESTE-001`, tenant `amh`.
Business key `RECURSO-amh-{guia}-{glosa}`. Process key: SP-OP-RECURSO-001 (exato — nao alterar).

## Invariante L0 (DoD deliverable)

`test_nenhum_caminho_automatizado_indeferimento`:
  Varredura de TODAS as combinacoes de input das DMNs (recurso_admissibility / recurso_eligibility)
  em 120 combinacoes. As instancias NUNCA atingem `End_RecursoIndeferido` /
  `End_RecursoIndeferidoAuditor` / `End_RecursoDeferidoParcial` / `End_RecursoInadmissivel` sem que
  uma User Task humana tenha sido completada. A prova e feita consultando a historia do engine
  (history/activity-instance). E a prova da parte 5 do padrao no-denial (ADR-0018).

## Notas de fixture

  - `engine` NAO e redefinida localmente — o `conftest.py` compartilhado a iça centralmente.
    `deploy_artifacts`/`recurso_probe`/`start_recurso` ficam LOCAIS (o contrato de construcao do
    probe NAO e identico entre familias).
  - `recurso_probe` wira `bpmn_error_allowlist=RECURSO_BPMN_ERROR_ALLOWLIST` (GAP-RECURSO-3): o
    `WorkerBpmnError(ERR_RECURSO_INVALID_GLOSA)` alcanca o boundary catch modelado em vez de
    demover a incidente fail-closed. Sao TRES sites do mesmo codigo agora
    (`validate_recurso`/`request_documents`/`analyze_request`).
  - `recurso_probe` tambem wira os seams de start fenceado (`engine=`/`audit_sink=`) porque
    `handoff_pagamento` roda o chokepoint `start_process_idempotent` contra SP-OP-PAGTO-001 —
    a UNICA familia STRICT de dedup, que exige `DedupReportingAuditSink` +
    `HistoryQueryingTransport` (`FreshSinkAuditEmitter` + `FreshClientCibSevenTransport`, mesma
    divisao que o daemon vivo e o probe de contas usam).
  - O canal `notifications_of_type("recurso.<fn>")` so existe para os workers de raw handler
    (`notify_sla_risk`, `escalate_ans_timeout`, `comunicar_resposta`); os `FunctionWorker`
    descartam o seam Kafka (`del kafka`, ADR-0026) — para eles a evidencia e engine-side
    (`activity_instances_ended`) ou o evento de dominio real (`has_event`, publicado por
    `events.py`).
"""

from __future__ import annotations

import asyncio
import itertools
import uuid
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
import pytest_asyncio

from maezo.gateway.audit_postgres import FreshSinkAuditEmitter
from maezo.tools.workers.cibseven_engine import FreshClientCibSevenTransport
from maezo.tools.workers.events import register_events_workers
from maezo.tools.workers.harness import CibSevenWorkerTransport, FakeKafkaPublisher, WorkerHarness
from maezo.tools.workers.recurso import (
    HANDOFF_PAGTO_FORBIDDEN_KEYS,
    RECURSO_BPMN_ERROR_ALLOWLIST,
    RecursoIndeferimentoNotHumanError,
    register_recurso_workers,
    registrar_indeferimento_entry,
)

from .conftest import CIBSEVEN_BASE_URL, drain_topics
from .engine_rest import EngineRest

pytestmark = pytest.mark.integration

_REPO = Path(__file__).resolve().parents[3]
_BPMN = _REPO / "spec/processes/bpmn/SP-OP-RECURSO-001_Recurso_Glosa.bpmn"
_DMN_ADMISSIBILITY = _REPO / "spec/processes/dmn/recurso_admissibility.dmn"
_DMN_ELIGIBILITY = _REPO / "spec/processes/dmn/recurso_eligibility.dmn"
_DMN_SLA = _REPO / "spec/processes/dmn/recurso_sla.dmn"

# SP-OP-PAGTO-001 — o destino do handoff de glosa revertida. Deployado APENAS pelos testes que
# provam I-PAGTO-1 (nao pelo `deploy_artifacts` padrao): o resto da suite nao depende dele.
_BPMN_PAGTO = _REPO / "spec/processes/bpmn/SP-OP-PAGTO-001_Pagamentos_Alcada.bpmn"
_DMN_PAGTO_ADMISSIBILITY = _REPO / "spec/processes/dmn/pagto_admissibility.dmn"
_DMN_PAGTO_ALCADA = _REPO / "spec/processes/dmn/pagto_alcada.dmn"

# External task topics do contrato SP-OP-RECURSO-001 que TEM worker real registrado E aparecem no
# BPMN (a intersecao pratica — ver finding 2 no docstring do modulo para os dois lados do drift).
_PUBLISH_TOPIC = "operadora.events.publish"
_VALIDATE_RECURSO_TOPIC = "operadora.recurso.validate_recurso"
_REQUEST_DOCS_TOPIC = "operadora.recurso.request_documents"
_ANALYZE_TOPIC = "operadora.recurso.analyze_request"
_REGISTRAR_INDEFERIMENTO_TOPIC = "operadora.recurso.registrar_indeferimento"
_COMUNICAR_RESPOSTA_TOPIC = "operadora.recurso.comunicar_resposta"
_HANDOFF_PAGAMENTO_TOPIC = "operadora.recurso.handoff_pagamento"
_NOTIFY_SLA_RISK_TOPIC = "operadora.recurso.notify_sla_risk"
_ESCALATE_ANS_TIMEOUT_TOPIC = "operadora.recurso.escalate_ans_timeout"

# Topicos servidos pelos workers REAIS registrados no harness (drain generico) — os 8 topicos
# `operadora.recurso.*` que o BPMN de fato declara, mais o publish generico.
_RECURSO_WORKER_TOPICS = [
    _PUBLISH_TOPIC,
    _VALIDATE_RECURSO_TOPIC,
    _REQUEST_DOCS_TOPIC,
    _ANALYZE_TOPIC,
    _REGISTRAR_INDEFERIMENTO_TOPIC,
    _COMUNICAR_RESPOSTA_TOPIC,
    _HANDOFF_PAGAMENTO_TOPIC,
    _NOTIFY_SLA_RISK_TOPIC,
    _ESCALATE_ANS_TIMEOUT_TOPIC,
]

# Nenhum topico declarado no BPMN fica sem worker registrado.
_RECURSO_UNIMPLEMENTED_TOPICS: frozenset[str] = frozenset()

# Topicos REGISTRADOS sem nenhuma service task BPMN correspondente (dead code do ponto de vista do
# engine — nunca invocados por SP-OP-RECURSO-001). `validate_recurso` SAIU desta lista: ele agora
# serve `ST_ValidarRecurso`, o intake que roda em TODO caminho.
_RECURSO_ORPHAN_TOPICS = frozenset(
    {
        "operadora.recurso.assess_eligibility",
        "operadora.recurso.prepare_dossier",
        "operadora.recurso.escalate_to_junta",
        "operadora.recurso.publish_completed",
    }
)

_NOTIFICATIONS_TOPIC = "operadora.notifications.internal"

# Eventos de dominio (contrato SP-OP-RECURSO-001.md)
_RECURSO_RECEIVED = "agents.events.recurso.received"
_RECURSO_PENDED = "agents.events.recurso.pended"
_RECURSO_SLA_BREACHED = "agents.events.recurso.sla_breached"
_RECURSO_COMPLETED = "agents.events.recurso.completed"

# User Task definition keys (BPMN)
_UT_ANALISTA = "UT_AnaliseRecursoAnalista"
_UT_AUDITOR = "UT_RevisaoAuditorMedico"
_UT_COORDENACAO = "UT_CoordenacaoRecursoAssume"
_UT_ESCALONAMENTO = "UT_EscalonamentoPrazo"

# Terminais ADVERSOS (o recurso e indeferido / parcialmente deferido / inadmitido — o prestador
# suporta o efeito) — todos humano-gated (L0 hard).
_END_INDEFERIDO = "End_RecursoIndeferido"
_END_INDEFERIDO_AUDITOR = "End_RecursoIndeferidoAuditor"
_END_PARCIAL = "End_RecursoDeferidoParcial"
_END_INADMISSIVEL = "End_RecursoInadmissivel"
_ENDS_ADVERSOS = frozenset({_END_INDEFERIDO, _END_INDEFERIDO_AUDITOR, _END_PARCIAL, _END_INADMISSIVEL})

# Terminal FAVORAVEL (a glosa e revertida integralmente)
_END_DEFERIDO = "End_RecursoDeferido"
# GAP-RECURSO-3: terminal NEUTRO compartilhado pelos TRES boundary catches de origem-invalida
# (BE_GlosaInvalidaValidacao / BE_GlosaInvalidaDocs / BE_GlosaInvalidaDossie).
_END_GLOSA_INVALIDA_ORIGEM = "End_RecursoGlosaInvalidaOrigem"
# Terminal de ERRO fail-closed dos dois gateways decisorios (decisao ausente/desconhecida).
_END_ERR_DECISAO_INVALIDA = "End_ErrRecursoDecisaoInvalida"

# Os 5 terminais de NEGOCIO — cada um DEVE comunicar o prestador antes de publicar o evento.
_ENDS_NEGOCIO = frozenset(
    {_END_DEFERIDO, _END_PARCIAL, _END_INDEFERIDO, _END_INDEFERIDO_AUDITOR, _END_INADMISSIVEL}
)

# User Tasks humanas que podem produzir um indeferimento (L0 hard guard).
_UT_HUMANAS_INDEFERIMENTO = frozenset({_UT_ANALISTA, _UT_AUDITOR, _UT_COORDENACAO, _UT_ESCALONAMENTO})


# ---------------------------------------------------------------------------
# _REASON constants — um por finding recurso-specifico (ver module docstring FINDINGS).
# ---------------------------------------------------------------------------

# Nota historica: as constantes `_RECURSO_KAFKA_GAP_REASON` /
# `_RECURSO_INVALID_GLOSA_GUARD_MISSING_REASON` foram retiradas com o redesenho. A primeira
# descrevia um gap ja fechado; a segunda descrevia GAP-RECURSO-3 como aberto, o que deixou de ser
# verdade quando `_require_glosa_id` passou a levantar `WorkerBpmnError` e o probe passou a wirar
# `bpmn_error_allowlist`. Nenhuma das duas era referenciada por um `pytest.mark.xfail` ativo.


@dataclass
class RecursoEngineProbe:
    """Driva os workers reais de Phase-2 (recurso) contra o engine CIB Seven."""

    engine: EngineRest
    harness: WorkerHarness
    transport: CibSevenWorkerTransport
    kafka: FakeKafkaPublisher
    worker_id: str

    @property
    def _captured(self) -> list[tuple[str, dict[str, Any], str | None]]:
        return self.kafka.published

    def _domain_events(self) -> list[dict[str, Any]]:
        out = []
        for topic, payload, _key in self._captured:
            if topic == _NOTIFICATIONS_TOPIC:
                continue
            out.append({"topic": topic, "payload": payload})
        return out

    def events_on(self, topic: str) -> list[dict[str, Any]]:
        return [e for e in self._domain_events() if e["topic"] == topic]

    def has_event(self, topic: str, **match: Any) -> bool:
        return any(all(e["payload"].get(k) == v for k, v in match.items()) for e in self.events_on(topic))

    def notifications_of_type(self, ntype: str) -> list[dict[str, Any]]:
        return [v for (_t, v, _k) in self._captured if v.get("type") == ntype]

    async def drain(self, *, rounds: int = 30) -> None:
        await drain_topics(
            self.transport, self.harness, self.worker_id, _RECURSO_WORKER_TOPICS, rounds=rounds
        )


@pytest_asyncio.fixture
async def deploy_artifacts(engine: EngineRest) -> str:
    """Deploya BPMN + 3 DMN de recurso de glosa da arvore no engine real."""
    return await engine.deploy(
        _BPMN, _DMN_ADMISSIBILITY, _DMN_ELIGIBILITY, _DMN_SLA, name="SP-OP-RECURSO-001-qa"
    )


@pytest_asyncio.fixture
async def recurso_probe(
    engine: EngineRest, audit_sink: Any, audit_tenant: str, audit_pg: tuple[str, str]
) -> AsyncIterator[RecursoEngineProbe]:
    """Probe que serve as external tasks com os workers reais Phase-2 de recurso.

    `bpmn_error_allowlist=RECURSO_BPMN_ERROR_ALLOWLIST` E CONFIGURADO (mirrors auth/cancel) —
    GAP-RECURSO-3: recurso.py levanta `WorkerBpmnError(ERR_RECURSO_INVALID_GLOSA)` em TRES sites
    (`validate_recurso_entry`/`request_documents_entry`/`analyze_request_entry`, todos guardados
    por `_require_glosa_id`) e `RECURSO_BPMN_ERROR_ALLOWLIST` esta unido ao allowlist de PRODUCAO
    (`worker_runtime/service.py`, gate-proven via `scripts/ci/check_bpmn_error_allowlist.py`).
    Contra este harness (allowlist wired below), o `WorkerBpmnError` alcanca o boundary catch
    modelado em vez de demover a incidente fail-closed.

    SEAMS DE START FENCEADO (`engine=`/`audit_sink=`): `handoff_pagamento` roda
    `start_process_idempotent` contra SP-OP-PAGTO-001 — a UNICA familia STRICT de dedup, que EXIGE
    um `DedupReportingAuditSink` (`emit_once_status`) e um `HistoryQueryingTransport`
    (`find_any_instance`), senao recusa com `StartDedupGateUnavailableError`. `FreshSinkAuditEmitter`
    e `FreshClientCibSevenTransport` sao a mesma divisao que o worker-daemon vivo e o probe de
    contas usam (o worker sincrono emite no seu proprio loop `asyncio.run`, NAO no sink pooled do
    harness).
    """
    worker_id = f"qa-recurso-worker-{uuid.uuid4().hex[:8]}"
    transport = CibSevenWorkerTransport(CIBSEVEN_BASE_URL)
    # T1.10 wave: emit-before-complete is FAIL-CLOSED (harness.py _emit_audit) — a real
    # PostgresAuditSink (lane PG, migrations 0001->0005) is REQUIRED or the harness refuses
    # to complete. `tenant` scopes the durable audit chain / dedup key to the per-run schema.
    harness = WorkerHarness(
        transport,
        worker_id=worker_id,
        tenant=audit_tenant,
        lock_duration_ms=10_000,
        audit_sink=audit_sink,
        bpmn_error_allowlist=RECURSO_BPMN_ERROR_ALLOWLIST,
    )
    kafka = FakeKafkaPublisher()
    engine_seam = FreshClientCibSevenTransport(CIBSEVEN_BASE_URL)
    handoff_audit_sink = FreshSinkAuditEmitter(audit_pg[0], audit_tenant)
    register_recurso_workers(harness, kafka, engine=engine_seam, audit_sink=handoff_audit_sink)
    # T3.1 R2: o worker generico de operadora.events.publish que toda ST_Publish* deste BPMN usa.
    register_events_workers(harness, kafka)
    # DRIFT GUARD (espelha contas/cancel): todo topico recurso.* registrado no harness DEVE estar
    # na lista de drain — falha AQUI, explicita, se um worker novo ficar fora.
    recurso_registered = {t for t in harness.registered_topics if t.startswith("operadora.recurso.")}
    missing_from_drain = recurso_registered - set(_RECURSO_WORKER_TOPICS) - _RECURSO_ORPHAN_TOPICS
    assert not missing_from_drain, (
        f"_RECURSO_WORKER_TOPICS desatualizada — topicos registrados fora do drain: {missing_from_drain}"
    )
    probe = RecursoEngineProbe(
        engine=engine,
        harness=harness,
        transport=transport,
        kafka=kafka,
        worker_id=worker_id,
    )
    try:
        yield probe
    finally:
        await transport.close()


def _unique_glosa(prefix: str = "GLOSA-TESTE") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8].upper()}"


def _future_anchor_iso(days: int = 40) -> str:
    """Ancora do teto P30D no FUTURO dinamico (GAP-RECURSO-1; licao GAP-NIP-1).

    Com o `timeDate` absoluto, ancora + P30D no PASSADO faz o boundary de teto disparar
    IMEDIATAMENTE ao attach (correto em prod: teto ja estourado; mas quebraria todo teste que
    precisa alcancar uma UT sem estouro). Ancora relativa a "agora" (+40d, confortavelmente alem
    do P30D do teto: teto em ~+70d).
    """
    return (datetime.now(UTC) + timedelta(days=days)).date().isoformat()


@pytest_asyncio.fixture
async def start_recurso(engine: EngineRest, deploy_artifacts: str) -> Callable[..., Any]:
    """Inicia uma instancia com business key RECURSO-amh-{guia}-{glosa} e payload canonico.

    Overrides via kwargs. Dados sinteticos obvios (GUIA-TESTE-0001, GLOSA-TESTE-NNNN, tenant amh).
    Os fatos pre-resolvidos por worker (glosa_existe, dentro_prazo_recurso,
    documentacao_recurso_completa) sao seeded como variaveis de start — o WorkerHarness NAO
    escreve facts de roteamento de volta ao engine (so observa/echo/publica), entao o teste e o
    agente de origem semeia os facts (mesma tecnica de auth/cancel).

    GAP-RECURSO-1: `data_recebimento_recurso_iso` (ancora UNICA do teto absoluto P30D) e seedada
    DINAMICA no futuro (+40d — ver `_future_anchor_iso`). Override com `None` REMOVE a variavel do
    start: o intake (`ST_ValidarRecurso`) entao a normaliza e defaulta fail-safe para HOJE/UTC com
    warning — a DMN NAO tem mais fail-safe para a data de ciencia do prestador (a ancora do
    recorrente saiu de `recurso_sla` inteira).
    """

    async def _start(**overrides: Any) -> dict[str, Any]:
        glosa = overrides.pop("glosa_id", _unique_glosa())
        guia = overrides.pop("numero_guia_tiss", "GUIA-TESTE-0001")
        variables: dict[str, Any] = {
            "tenant_id": "amh",
            "numero_guia_tiss": guia,
            "glosa_id": glosa,
            "numero_lote_tiss": "LOTE-TESTE-0001",
            "numero_conta": "CONTA-TESTE-0001",
            "prestador_id": "PREST-TESTE-001",
            "beneficiario_pseudo_id": "PSEUDO-TESTE-001",
            "glosa_type": "administrativa",
            "glosa_reason_code": "GUIA_INCOMPLETA",
            "valor_glosado_brl": "150.00",
            "codigo_procedimento_tuss": "40304361",
            "cid10": "",
            "documentos_recurso_refs": "[]",
            "data_ciencia_glosa": "2026-06-01",
            "data_recebimento_recurso_iso": _future_anchor_iso(),
            # Herdados do envelope de intake e exigidos pelo handoff de pagamento da glosa
            # revertida (SP-OP-PAGTO-001). `data_vencimento` e recusada em branco pelo worker
            # (OQ-3, DRAFT/verify) — as demais sao ecoadas verbatim, nunca inventadas.
            "data_vencimento": "2026-12-31",
            "competencia": "2026-05",
            "conta_origem_ref": "CONTA-REF-TESTE-0001",
            "instrumento_pagamento": "pix",
            "glosa_existe": True,
            "dentro_prazo_recurso": True,
            "documentacao_recurso_completa": True,
            "desfecho_humano": "",
        }
        variables.update(overrides)
        variables = {k: v for k, v in variables.items() if v is not None}
        business_key = f"RECURSO-amh-{guia}-{glosa}"
        return await engine.start_by_key("SP-OP-RECURSO-001", business_key, variables)

    return _start


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _await_end(engine: EngineRest, iid: str, *, attempts: int = 60, delay: float = 0.25) -> set[str]:
    """Espera a instancia terminar e retorna os end events atingidos."""
    for _ in range(attempts):
        state = await engine.history_state(iid)
        if state == "COMPLETED":
            return await engine.activity_instances_ended(iid)
        await asyncio.sleep(delay)
    return await engine.activity_instances_ended(iid)


async def _assert_no_indeferimento_without_human_task(engine: EngineRest, iid: str) -> None:
    """INVARIANTE L0: prova que os terminais adversos nao existem sem UT humana."""
    ended = await engine.activity_instances_ended(iid)
    adversos_atingidos = ended & _ENDS_ADVERSOS
    if adversos_atingidos:
        human_tasks_in_history = ended & _UT_HUMANAS_INDEFERIMENTO
        assert human_tasks_in_history, (
            f"INVARIANTE L0 VIOLADA: terminal(is) adverso(s) {adversos_atingidos} atingido(s) "
            f"para instancia {iid} SEM nenhuma User Task humana no historico. "
            f"User Tasks esperadas (qualquer uma de): {_UT_HUMANAS_INDEFERIMENTO}. "
            f"Atividades historicas encontradas: {ended}. "
            "Isto indica um caminho automatizado de indeferimento — violacao do L0 hard (ADR-0005)."
        )


async def _drive_to_analista(engine: EngineRest, probe: RecursoEngineProbe, iid: str) -> Any:
    """Drena ate UT_AnaliseRecursoAnalista surgir (dossie de Marina preparado pelo worker real)."""
    await probe.drain()
    return await engine.await_user_task(iid, _UT_ANALISTA)


def _indeferimento_fields(*, analista: bool = True) -> dict[str, Any]:
    """Campos obrigatorios do indeferimento humano (espelha o guard de registrar_indeferimento)."""
    fields: dict[str, Any] = {
        "decisao_recurso": "INDEFERIR",
        "fundamentacao_indeferimento": "Sem elementos novos que afastem a glosa (sintetico — teste L0)",
        "valor_glosa_mantido_brl": "150.00",
        "referencia_contratual": "Clausula 12.3 do contrato de prestacao (sintetico)",
    }
    if analista:
        fields["analista_id"] = "analista-sintetico-001"
    return fields


def _deferir_parcial_fields(*, analista: bool = True) -> dict[str, Any]:
    """DEFERIR_PARCIAL humano: mantem parte da glosa (adverso L0) e reverte a outra parte.

    A soma FECHA em centavos-inteiros contra `valor_glosado_brl="150.00"` do fixture — o guard
    recusa uma soma que nao fecha (OQ-R2: tolerancia/arredondamento e decisao de SME).
    """
    fields: dict[str, Any] = {
        "decisao_recurso": "DEFERIR_PARCIAL",
        "fundamentacao_indeferimento": "Parte da glosa se mantem (sintetico — teste L0)",
        "valor_glosa_mantido_brl": "50.00",
        "valor_deferido_brl": "100.00",
        "referencia_contratual": "Clausula 12.3 do contrato de prestacao (sintetico)",
    }
    if analista:
        fields["analista_id"] = "analista-sintetico-001"
    return fields


def _parse_due(due: str) -> datetime:
    """dueDate do engine (ISO 8601, possivelmente com offset) -> datetime naive p/ comparacao."""
    return datetime.fromisoformat(due).replace(tzinfo=None)


# ===========================================================================
# INVARIANTE L0 — DoD deliverable (varredura de inputs das DMNs)
# ===========================================================================


async def test_nenhum_caminho_automatizado_indeferimento(
    engine: EngineRest,
    recurso_probe: RecursoEngineProbe,
    start_recurso: Callable[..., Any],
) -> None:
    """INVARIANTE L0: NENHUM caminho automatizado indefere o recurso (nem mantem parte da glosa,
    nem inadmite).

    Varredura de TODAS as combinacoes de input das DMNs:
      glosa_existe / dentro_prazo_recurso / documentacao_recurso_completa in {true, false}
      glosa_type in {administrativa, tecnica, clinica, linha_duplicada, formatacao}
      valor_glosado_brl em {baixo, medio, alto}
    (2*2*2 * 5 * 3 = 120 combinacoes). Cada instancia so avanca UMA rodada de `drain()` (sem
    completar UT humana). E a prova da PARTE 5 do padrao no-denial (ADR-0018): a varredura mostra
    que nenhuma combinacao de input alcanca `End_RecursoIndeferido` /
    `End_RecursoIndeferidoAuditor` / `End_RecursoDeferidoParcial` / `End_RecursoInadmissivel`.
    """
    glosa_types = ["administrativa", "tecnica", "clinica", "linha_duplicada", "formatacao"]
    valores = ["150.00", "5000.00", "75000.00"]
    bools = [True, False]
    checked = 0

    for existe, prazo, docs, gtype, valor in itertools.product(bools, bools, bools, glosa_types, valores):
        inst = await start_recurso(
            glosa_existe=existe,
            dentro_prazo_recurso=prazo,
            documentacao_recurso_completa=docs,
            glosa_type=gtype,
            valor_glosado_brl=valor,
        )
        iid = inst["id"]
        await recurso_probe.drain()

        ended = await engine.activity_instances_ended(iid)
        adversos = ended & _ENDS_ADVERSOS
        assert not adversos, (
            f"L0 VIOLADO: existe={existe} prazo={prazo} docs={docs} gtype={gtype} valor={valor} "
            f"atingiu terminal adverso {adversos} automaticamente. ended={ended}"
        )
        await _assert_no_indeferimento_without_human_task(engine, iid)
        checked += 1

    assert checked == 120, f"Esperava 120 combinacoes varridas; varri {checked}"


async def test_inadmissibilidade_aparente_roteia_para_humano(
    engine: EngineRest,
    recurso_probe: RecursoEngineProbe,
    start_recurso: Callable[..., Any],
) -> None:
    """dentro_prazo_recurso=false (inadmissibilidade procedural aparente — R5) => ANALISE_HUMANA."""
    for override in ({"dentro_prazo_recurso": False}, {"glosa_existe": False}):
        inst = await start_recurso(**override)
        iid = inst["id"]

        ut = await _drive_to_analista(engine, recurso_probe, iid)
        assert "analista-recurso-glosa" in ut.candidate_groups

        ended = await engine.activity_instances_ended(iid)
        assert not (ended & _ENDS_ADVERSOS), (
            f"Inadmissibilidade aparente ({override}) nao deve produzir terminal adverso automatico (L0)"
        )
        await _assert_no_indeferimento_without_human_task(engine, iid)


async def test_inelegibilidade_roteia_para_humano_nao_nega(
    engine: EngineRest,
    recurso_probe: RecursoEngineProbe,
    start_recurso: Callable[..., Any],
) -> None:
    """glosa_type=tecnica (merito clinico/tecnico) => recurso_eligibility -> medico-auditor.

    A DMN roteia grupo_revisor=medico-auditor; o fluxo chega a UT_AnaliseRecursoAnalista (entrada
    HITL — candidateGroups hardcoded no BPMN, independente do grupo_revisor da DMN), que pode
    ESCALAR_AUDITOR. NUNCA um fim adverso automatico.
    """
    inst = await start_recurso(glosa_type="tecnica")
    iid = inst["id"]

    ut = await _drive_to_analista(engine, recurso_probe, iid)
    assert "analista-recurso-glosa" in ut.candidate_groups

    ended = await engine.activity_instances_ended(iid)
    assert not (ended & _ENDS_ADVERSOS), "Glosa tecnica nao deve produzir terminal adverso automatico (L0)"
    await _assert_no_indeferimento_without_human_task(engine, iid)


# ===========================================================================
# Happy paths
# ===========================================================================


async def test_happy_path_deferir_pelo_analista(
    engine: EngineRest,
    recurso_probe: RecursoEngineProbe,
    start_recurso: Callable[..., Any],
) -> None:
    """DEFERIR humano => comunica a resposta ao prestador + encaminha o pagamento da glosa
    revertida a SP-OP-PAGTO-001 => End_RecursoDeferido (desfecho=deferido_humano).

    Substitui `test_happy_path_recorrer_e_deferido`, cujo gatilho era `resposta_operadora ==
    'deferido'` — a decisao de um TERCEIRO chegando por mensagem. Aqui o deferimento e a decisao da
    PROPRIA operadora, tomada por um humano na User Task.

    SP-OP-PAGTO-001 e deployado por este teste (o handoff roda o chokepoint fenceado contra ele;
    nao dependa de vazamento de deploy entre testes).
    """
    await engine.deploy(
        _BPMN_PAGTO, _DMN_PAGTO_ADMISSIBILITY, _DMN_PAGTO_ALCADA, name="SP-OP-PAGTO-001-qa-recurso"
    )
    glosa_id = _unique_glosa()
    inst = await start_recurso(glosa_type="administrativa", glosa_id=glosa_id)
    iid = inst["id"]

    ut = await _drive_to_analista(engine, recurso_probe, iid)
    assert "analista-recurso-glosa" in ut.candidate_groups
    ended_pre = await engine.activity_instances_ended(iid)
    assert "ST_ValidarRecurso" in ended_pre, (
        f"ST_ValidarRecurso (intake) roda em TODO caminho, antes de BRT_Admissibilidade. ended={ended_pre}"
    )
    assert "ST_PrepararDossie" in ended_pre, (
        "Worker analyze_request (Marina/ST_PrepararDossie) deve ter sido executado"
    )

    await engine.complete_task_as_human(
        ut.id,
        {
            "decisao_recurso": "DEFERIR",
            "valor_deferido_brl": "150.00",
            "analista_id": "analista-sintetico-001",
        },
    )
    await recurso_probe.drain()
    ended = await _await_end(engine, iid)

    assert _END_DEFERIDO in ended, f"DEFERIR humano => End_RecursoDeferido. ended={ended}"
    assert not (ended & _ENDS_ADVERSOS)
    assert "ST_ComunicarDeferimento" in ended, "a operadora DEVE comunicar a resposta ao prestador"
    assert "ST_HandoffPagamentoRecurso" in ended, (
        "o deferimento DEVE encaminhar o pagamento da glosa revertida a SP-OP-PAGTO-001"
    )
    assert recurso_probe.has_event(
        _RECURSO_COMPLETED, desfecho="deferido_humano", analista_id="analista-sintetico-001"
    ), "ST_PublishDeferido deve emitir completed(desfecho=deferido_humano) com o analista_id"

    # A ordem de pagamento existe, sob a business key derivada da identidade da glosa revertida.
    pagto_bk = f"PAGTO-amh-GUIA-TESTE-0001-{glosa_id}"
    pagto_instances = await engine.find_active_instances(pagto_bk)
    assert len(pagto_instances) == 1, (
        f"o deferimento deve abrir EXATAMENTE 1 SP-OP-PAGTO-001 sob {pagto_bk}; achei {pagto_instances}"
    )


async def test_handoff_pagamento_glosa_revertida_inicia_pagto_idempotente(
    engine: EngineRest,
    recurso_probe: RecursoEngineProbe,
    start_recurso: Callable[..., Any],
) -> None:
    """O handoff semeia `tipo_pagamento=glosa_revertida` + a EVIDENCIA do lastro, e e idempotente
    por business key (SP-OP-PAGTO-001 e a UNICA familia STRICT de dedup — um deferimento nunca
    vira pagamento duplicado)."""
    await engine.deploy(
        _BPMN_PAGTO, _DMN_PAGTO_ADMISSIBILITY, _DMN_PAGTO_ALCADA, name="SP-OP-PAGTO-001-qa-recurso"
    )
    glosa_id = _unique_glosa()
    inst = await start_recurso(glosa_type="administrativa", glosa_id=glosa_id)
    iid = inst["id"]

    ut = await _drive_to_analista(engine, recurso_probe, iid)
    await engine.complete_task_as_human(
        ut.id,
        {
            "decisao_recurso": "DEFERIR",
            "valor_deferido_brl": "150.00",
            "analista_id": "analista-sintetico-001",
        },
    )
    await recurso_probe.drain()
    await _await_end(engine, iid)

    pagto_bk = f"PAGTO-amh-GUIA-TESTE-0001-{glosa_id}"
    pagto = await engine.find_active_instances(pagto_bk)
    assert len(pagto) == 1
    pagto_iid = pagto[0]["id"]

    assert await engine.get_variable(pagto_iid, "tipo_pagamento") == "glosa_revertida"
    assert await engine.get_variable(pagto_iid, "valor_pagamento_cents") == 15000
    assert await engine.get_variable(pagto_iid, "moeda") == "BRL"
    assert await engine.get_variable(pagto_iid, "data_vencimento") == "2026-12-31"
    assert await engine.get_variable(pagto_iid, "fonte_valor") == "deferido"


async def test_handoff_pagamento_nunca_semeia_os_fatos_de_admissibilidade_de_pagto(
    engine: EngineRest,
    recurso_probe: RecursoEngineProbe,
    start_recurso: Callable[..., Any],
) -> None:
    """I-PAGTO-1, engine-side: o processo de ORIGEM fornece a EVIDENCIA do lastro; so PAGTO
    resolve o FATO. As quatro variaveis de admissibilidade NAO chegam a instancia de PAGTO."""
    await engine.deploy(
        _BPMN_PAGTO, _DMN_PAGTO_ADMISSIBILITY, _DMN_PAGTO_ALCADA, name="SP-OP-PAGTO-001-qa-recurso"
    )
    glosa_id = _unique_glosa()
    inst = await start_recurso(glosa_type="administrativa", glosa_id=glosa_id)
    iid = inst["id"]

    ut = await _drive_to_analista(engine, recurso_probe, iid)
    await engine.complete_task_as_human(
        ut.id,
        {
            "decisao_recurso": "DEFERIR",
            "valor_deferido_brl": "150.00",
            "analista_id": "analista-sintetico-001",
        },
    )
    await recurso_probe.drain()
    await _await_end(engine, iid)

    pagto_bk = f"PAGTO-amh-GUIA-TESTE-0001-{glosa_id}"
    pagto = await engine.find_active_instances(pagto_bk)
    assert len(pagto) == 1
    pagto_iid = pagto[0]["id"]

    for proibida in sorted(HANDOFF_PAGTO_FORBIDDEN_KEYS):
        assert await engine.get_variable(pagto_iid, proibida) is None, (
            f"{proibida} vazou de RECURSO para PAGTO — I-PAGTO-1 violada (quem julgou o recurso "
            "teria confirmado o lastro da ordem que ele proprio gerou)"
        )
    assert await engine.get_variable(pagto_iid, "lastro_origem") == "recurso_deferimento_humano"
    assert await engine.get_variable(pagto_iid, "lastro_decisor_id") == "analista-sintetico-001"


async def test_glosa_revertida_nunca_alcanca_liberacao_automatica_sem_ut(
    engine: EngineRest,
    recurso_probe: RecursoEngineProbe,
    start_recurso: Callable[..., Any],
) -> None:
    """M1 / I-PAGTO-1: a ordem originada num deferimento humano NAO alcanca
    `End_PagamentoLiberadoAutomatico` sem `UT_AnaliseAdmissibilidade` concluida.

    E a prova da SEGREGACAO DE FUNCOES, nao apenas do HITL: sem a invariante, o deferimento
    humano semearia `lastro_confirmado=true` e a ordem cairia direto na faixa clerical
    `DENTRO_TETO_L2` (`ST_ReleaseLowValue`, sem nenhuma User Task). Com ela,
    `pagto_admissibility` le o lastro ausente => `ANALISE_HUMANA` => a ordem PARA na fila de
    `coordenacao-financeira`.

    Este teste NAO drena os workers de PAGTO (eles nao pertencem a este probe): ele prova o
    estado em que a instancia de PAGTO fica — nao liberada, e com uma decisao humana pendente.
    """
    await engine.deploy(
        _BPMN_PAGTO, _DMN_PAGTO_ADMISSIBILITY, _DMN_PAGTO_ALCADA, name="SP-OP-PAGTO-001-qa-recurso"
    )
    glosa_id = _unique_glosa()
    inst = await start_recurso(glosa_type="administrativa", glosa_id=glosa_id)
    iid = inst["id"]

    ut = await _drive_to_analista(engine, recurso_probe, iid)
    await engine.complete_task_as_human(
        ut.id,
        {
            "decisao_recurso": "DEFERIR",
            "valor_deferido_brl": "150.00",
            "analista_id": "analista-sintetico-001",
        },
    )
    await recurso_probe.drain()
    await _await_end(engine, iid)

    pagto_bk = f"PAGTO-amh-GUIA-TESTE-0001-{glosa_id}"
    pagto = await engine.find_active_instances(pagto_bk)
    assert len(pagto) == 1
    pagto_iid = pagto[0]["id"]

    pagto_ended = await engine.activity_instances_ended(pagto_iid)
    assert "End_PagamentoLiberadoAutomatico" not in pagto_ended, (
        "uma glosa revertida NUNCA pode ser liberada automaticamente: quem julgou o recurso nao "
        "e quem admite a ordem de pagamento (I-PAGTO-1, segregacao de funcoes)"
    )
    assert "ST_ReleaseLowValue" not in pagto_ended
    assert "ST_ReleaseHighValue" not in pagto_ended


async def test_happy_path_escalar_auditor_defere(
    engine: EngineRest,
    recurso_probe: RecursoEngineProbe,
    start_recurso: Callable[..., Any],
) -> None:
    """ESCALAR_AUDITOR (glosa tecnica/clinica); auditor DEFERIR => comunica + handoff de pagamento.

    Substitui `test_happy_path_escalar_auditor_mantem_recurso` (MANTER_RECURSO ->
    `ST_SubmitAppeal`): o auditor da operadora nao "mantem um recurso", ele DEFERE ou INDEFERE o
    merito tecnico-clinico. `Flow_GWMerito_Deferir` converge no MESMO `ST_ComunicarDeferimento` do
    canal do analista.
    """
    await engine.deploy(
        _BPMN_PAGTO, _DMN_PAGTO_ADMISSIBILITY, _DMN_PAGTO_ALCADA, name="SP-OP-PAGTO-001-qa-recurso"
    )
    inst = await start_recurso(glosa_type="tecnica")
    iid = inst["id"]

    ut = await _drive_to_analista(engine, recurso_probe, iid)
    await engine.complete_task_as_human(ut.id, {"decisao_recurso": "ESCALAR_AUDITOR"})
    await recurso_probe.drain()

    ut_auditor = await engine.await_user_task(iid, _UT_AUDITOR)
    assert "medico-auditor" in ut_auditor.candidate_groups

    await engine.complete_task_as_human(
        ut_auditor.id,
        {
            "decisao_auditor_recurso": "DEFERIR",
            "parecer_auditor": "Glosa tecnica improcedente — recurso deferido (sintetico)",
            "valor_deferido_brl": "150.00",
            "auditor_id": "auditor-sintetico-001",
        },
    )
    await recurso_probe.drain()
    ended = await _await_end(engine, iid)

    assert _END_DEFERIDO in ended, f"auditor DEFERIR => End_RecursoDeferido. ended={ended}"
    assert not (ended & _ENDS_ADVERSOS), "DEFERIR nunca mantem a glosa"
    assert "ST_ComunicarDeferimento" in ended
    assert "ST_HandoffPagamentoRecurso" in ended
    await _assert_no_indeferimento_without_human_task(engine, iid)


async def test_happy_path_auditor_indefere_humano(
    engine: EngineRest,
    recurso_probe: RecursoEngineProbe,
    start_recurso: Callable[..., Any],
) -> None:
    """ESCALAR_AUDITOR; auditor INDEFERIR com campos => End_RecursoIndeferidoAuditor (humano-gated).

    O canal auditor de `registrar_indeferimento` continua sendo o mesmo topico do canal analista —
    so o vocabulario mudou (`ACEITAR_GLOSA` -> `INDEFERIR`). O `auditor_id` viaja ate o evento de
    dominio (`ST_PublishIndeferidoAuditor`'s `event_payload_vars`), que e a evidencia
    engine-observavel de que o guard executou com o humano acusado.
    """
    inst = await start_recurso(glosa_type="clinica")
    iid = inst["id"]

    ut = await _drive_to_analista(engine, recurso_probe, iid)
    await engine.complete_task_as_human(ut.id, {"decisao_recurso": "ESCALAR_AUDITOR"})
    await recurso_probe.drain()

    ut_auditor = await engine.await_user_task(iid, _UT_AUDITOR)
    await engine.complete_task_as_human(
        ut_auditor.id,
        {
            "decisao_auditor_recurso": "INDEFERIR",
            "parecer_auditor": "Glosa clinica procedente — recurso indeferido (sintetico)",
            "fundamentacao_indeferimento": "Merito tecnico-clinico confirma a glosa (sintetico)",
            "valor_glosa_mantido_brl": "150.00",
            "referencia_contratual": "Diretriz clinica DUT (sintetico)",
            "auditor_id": "auditor-sintetico-001",
        },
    )
    await recurso_probe.drain()

    ended = await _await_end(engine, iid)
    await _assert_no_indeferimento_without_human_task(engine, iid)
    assert _END_INDEFERIDO_AUDITOR in ended, (
        f"auditor INDEFERIR => End_RecursoIndeferidoAuditor. ended={ended}"
    )
    assert "ST_ComunicarIndeferimentoAuditor" in ended, (
        "o prestador DEVE receber a resposta tambem no indeferimento do auditor"
    )
    assert recurso_probe.has_event(
        _RECURSO_COMPLETED, desfecho="indeferido_humano", auditor_id="auditor-sintetico-001"
    ), "ST_PublishIndeferidoAuditor deve emitir completed(desfecho=indeferido_humano) com o auditor_id"


async def test_recurso_parcialmente_deferido(
    engine: EngineRest,
    recurso_probe: RecursoEngineProbe,
    start_recurso: Callable[..., Any],
) -> None:
    """DEFERIR_PARCIAL humano => guard + comunica + handoff da parcela revertida =>
    End_RecursoDeferidoParcial (desfecho=deferido_parcial_humano).

    RESCRITO: o parcial deixou de vir de `msg.recurso.resposta_recebida` (a resposta de um
    terceiro) e passou a ser a decisao humana da propria operadora. E classificado L0 ADVERSO por
    espelhar `End_ReembolsoParcial` ("reducao adversa") — parte da glosa e MANTIDA contra o
    prestador, entao ele passa pelo mesmo worker gated do indeferimento.
    """
    await engine.deploy(
        _BPMN_PAGTO, _DMN_PAGTO_ADMISSIBILITY, _DMN_PAGTO_ALCADA, name="SP-OP-PAGTO-001-qa-recurso"
    )
    inst = await start_recurso(glosa_type="administrativa")
    iid = inst["id"]

    ut = await _drive_to_analista(engine, recurso_probe, iid)
    await engine.complete_task_as_human(ut.id, _deferir_parcial_fields())
    await recurso_probe.drain()
    ended = await _await_end(engine, iid)

    await _assert_no_indeferimento_without_human_task(engine, iid)
    assert _END_PARCIAL in ended, f"DEFERIR_PARCIAL humano => End_RecursoDeferidoParcial. ended={ended}"
    assert "ST_RegistrarIndeferimentoParcial" in ended, (
        "o parcial e ADVERSO (mantem parte da glosa) — passa pelo worker gated"
    )
    assert "ST_ComunicarDeferimentoParcial" in ended
    assert "ST_HandoffPagamentoParcial" in ended, "a parcela revertida vira ordem de pagamento"
    assert recurso_probe.has_event(_RECURSO_COMPLETED, desfecho="deferido_parcial_humano")


# ===========================================================================
# INDEFERIR exige campos / worker guard
# ===========================================================================


async def test_indeferir_exige_campos_obrigatorios(
    engine: EngineRest,
    recurso_probe: RecursoEngineProbe,
    start_recurso: Callable[..., Any],
) -> None:
    """INDEFERIR sem fundamentacao/valor/referencia/analista_id => worker guard recusa.

    O engine despacha registrar_indeferimento; o worker lanca
    `RecursoIndeferimentoNotHumanError` (defesa em profundidade); a instancia NAO atinge nenhum
    terminal adverso (incidente no worker).
    """
    inst = await start_recurso(glosa_type="administrativa")
    iid = inst["id"]

    ut = await _drive_to_analista(engine, recurso_probe, iid)
    await engine.complete_task_as_human(ut.id, {"decisao_recurso": "INDEFERIR"})
    await recurso_probe.drain()

    ended = await engine.activity_instances_ended(iid)
    assert not (ended & _ENDS_ADVERSOS), (
        "INDEFERIR sem campos obrigatorios NAO pode atingir terminal adverso (guard do worker)"
    )
    await _assert_no_indeferimento_without_human_task(engine, iid)


async def test_deferir_parcial_exige_valor_deferido(
    engine: EngineRest,
    recurso_probe: RecursoEngineProbe,
    start_recurso: Callable[..., Any],
) -> None:
    """DEFERIR_PARCIAL sem `valor_deferido_brl` => guard recusa (o valor alimenta a ordem de
    pagamento da parcela revertida; sem ele nao ha o que encaminhar)."""
    inst = await start_recurso(glosa_type="administrativa")
    iid = inst["id"]

    ut = await _drive_to_analista(engine, recurso_probe, iid)
    campos = _deferir_parcial_fields()
    campos.pop("valor_deferido_brl")
    await engine.complete_task_as_human(ut.id, campos)
    await recurso_probe.drain()

    ended = await engine.activity_instances_ended(iid)
    assert not (ended & _ENDS_ADVERSOS), (
        "DEFERIR_PARCIAL sem valor_deferido_brl NAO pode atingir terminal adverso (guard do worker)"
    )
    await _assert_no_indeferimento_without_human_task(engine, iid)


async def test_worker_guard_registrar_indeferimento_recusa_sem_humano() -> None:
    """Invocacao direta do entry function `registrar_indeferimento_entry` sem decisao humana =>
    `RecursoIndeferimentoNotHumanError` (guard). Unit-style sobre o handler real (SEM engine).

    O guard e um `PermissionError` — o harness o roteia para um INCIDENTE auditado (ADR-0030 §4),
    nao para um boundary catch: uma tentativa de indeferir sem humano tem de ficar visivel.
    """
    kafka = FakeKafkaPublisher()

    # (a) decisao ausente -> recusa
    with pytest.raises(RecursoIndeferimentoNotHumanError) as exc_a:
        registrar_indeferimento_entry({}, kafka=kafka)
    assert "ERR_RECURSO_INDEFERIMENTO_NOT_HUMAN" in str(exc_a.value)

    # (b) decisao FAVORAVEL nunca alcanca o worker adverso -> recusa
    with pytest.raises(RecursoIndeferimentoNotHumanError) as exc_b:
        registrar_indeferimento_entry({"decisao_recurso": "DEFERIR"}, kafka=kafka)
    assert "ERR_RECURSO_INDEFERIMENTO_NOT_HUMAN" in str(exc_b.value)

    # (c) INDEFERIR mas faltando a identidade do decisor (analista_id) -> recusa por analista_id
    # (valor_glosa_mantido_brl="150.00" e parseado, NAO crasha).
    with pytest.raises(RecursoIndeferimentoNotHumanError) as exc_c:
        registrar_indeferimento_entry(
            {
                "decisao_recurso": "INDEFERIR",
                "fundamentacao_indeferimento": "x",
                "valor_glosa_mantido_brl": "150.00",
                "referencia_contratual": "clausula 12.3",
            },
            kafka=kafka,
        )
    assert "ERR_RECURSO_INDEFERIMENTO_NOT_HUMAN" in str(exc_c.value)
    assert "analista_id" in str(exc_c.value)
    assert "valor_glosa_mantido_brl" not in str(exc_c.value), (
        "valor_glosa_mantido_brl='150.00' e valido (>0) — a recusa e por analista_id ausente"
    )
    assert not kafka.published, "Nenhum registro deve ser publicado quando o guard recusa"

    # (d) decisao humana completa (analista) -> registra e cunha o protocolo deterministico
    result_d = registrar_indeferimento_entry(
        {
            "decisao_recurso": "INDEFERIR",
            "fundamentacao_indeferimento": "Sem elementos novos (teste)",
            "valor_glosa_mantido_brl": "150.00",
            "referencia_contratual": "Clausula 12.3",
            "analista_id": "analista-sintetico-001",
            "tenant_id": "amh",
            "numero_guia_tiss": "GUIA-TESTE-0001",
            "glosa_id": "GLOSA-TESTE-GUARD",
        },
        kafka=kafka,
    )
    assert result_d["registered"] is True
    assert result_d["protocolo"] == "RECIND-RECURSO-amh-GUIA-TESTE-0001-GLOSA-TESTE-GUARD"


async def test_worker_guard_registrar_indeferimento_auditor_path() -> None:
    """Canal auditor: `decisao_auditor_recurso == INDEFERIR` + `auditor_id` registra pelo MESMO
    topico do canal analista (o BPMN roteia os dois para `registrar_indeferimento`)."""
    kafka = FakeKafkaPublisher()
    result = registrar_indeferimento_entry(
        {
            "decisao_auditor_recurso": "INDEFERIR",
            "fundamentacao_indeferimento": "Merito clinico confirma glosa (teste)",
            "valor_glosa_mantido_brl": "150.00",
            "referencia_contratual": "Diretriz DUT",
            "auditor_id": "auditor-sintetico-001",
            "tenant_id": "amh",
            "numero_guia_tiss": "GUIA-TESTE-0001",
            "glosa_id": "GLOSA-TESTE-GUARD",
        },
        kafka=kafka,
    )
    assert result["registered"] is True
    assert result["protocolo"]


# ===========================================================================
# Boundary-error catches (GAP-RECURSO-3, dangling-catch fix no donor) — guards TECNICOS terminam
# LIMPO, nunca travam.
# ===========================================================================


async def test_glosa_id_ausente_pendencia_termina_limpo_sem_incidente_travado(
    engine: EngineRest,
    recurso_probe: RecursoEngineProbe,
    start_recurso: Callable[..., Any],
) -> None:
    """glosa_id ausente + PENDENTE_DOCUMENTACAO => o boundary catch BE_GlosaInvalidaDocs termina a
    instancia LIMPO em End_RecursoGlosaInvalidaOrigem.

    NOTA sobre a ORDEM: com `ST_ValidarRecurso` no intake, o PRIMEIRO site a recusar e o dele
    (`BE_GlosaInvalidaValidacao`) — o token nem chega a `GW_Admissibilidade`. O terminal
    compartilhado e o mesmo, e e ele que este teste assere; o boundary especifico e coberto por
    `test_glosa_id_ausente_validacao_termina_limpo_sem_incidente_travado` abaixo.
    """
    inst = await start_recurso(
        glosa_id="",
        numero_guia_tiss=f"GUIA-TESTE-INVALIDO-DOCS-{uuid.uuid4().hex[:8]}",
        documentacao_recurso_completa=False,
    )
    iid = inst["id"]

    await recurso_probe.drain()
    ended = await _await_end(engine, iid)

    assert _END_GLOSA_INVALIDA_ORIGEM in ended, (
        f"glosa_id ausente (ramo PENDENTE_DOCUMENTACAO) deve terminar LIMPO em "
        f"{_END_GLOSA_INVALIDA_ORIGEM} (boundary catch BE_GlosaInvalidaDocs), nao travar "
        f"silenciosamente sem end event. ended={ended}"
    )
    assert not (ended & _ENDS_ADVERSOS), "Validacao de origem nunca produz terminal adverso (L0)"
    assert not recurso_probe.notifications_of_type("recurso.request_documents"), (
        "Guard deve recusar ANTES de abrir a pendencia (glosa_id ausente)"
    )
    assert not recurso_probe.has_event(_RECURSO_PENDED), "Guard recusa antes de publicar recurso.pended"
    await _assert_no_indeferimento_without_human_task(engine, iid)


async def test_glosa_id_ausente_analise_termina_limpo_sem_incidente_travado(
    engine: EngineRest,
    recurso_probe: RecursoEngineProbe,
    start_recurso: Callable[..., Any],
) -> None:
    """glosa_id ausente + SEGUE_ANALISE => BE_GlosaInvalidaDossie (ou, a montante,
    BE_GlosaInvalidaValidacao) termina em End_RecursoGlosaInvalidaOrigem.

    Mesma nota de ORDEM da irma acima: com o intake no lugar, o token e barrado ja em
    `ST_ValidarRecurso`. O terminal compartilhado continua sendo a evidencia.
    """
    inst = await start_recurso(
        glosa_id="",
        numero_guia_tiss=f"GUIA-TESTE-INVALIDO-DOSSIE-{uuid.uuid4().hex[:8]}",
        documentacao_recurso_completa=True,
    )
    iid = inst["id"]

    await recurso_probe.drain()
    ended = await _await_end(engine, iid)

    assert _END_GLOSA_INVALIDA_ORIGEM in ended, (
        f"glosa_id ausente (ramo SEGUE_ANALISE) deve terminar LIMPO em "
        f"{_END_GLOSA_INVALIDA_ORIGEM} (boundary catch BE_GlosaInvalidaDossie), nao travar "
        f"silenciosamente sem end event. ended={ended}"
    )
    assert not (ended & _ENDS_ADVERSOS), "Validacao de origem nunca produz terminal adverso (L0)"
    assert not recurso_probe.notifications_of_type("recurso.analyze_request"), (
        "Guard deve recusar ANTES de convocar Marina/preparar o dossie (glosa_id ausente)"
    )
    assert "ST_PrepararDossie" not in ended, "o dossie de Marina nunca e montado sem glosa_id"
    await _assert_no_indeferimento_without_human_task(engine, iid)


async def test_glosa_id_ausente_validacao_termina_limpo_sem_incidente_travado(
    engine: EngineRest,
    recurso_probe: RecursoEngineProbe,
    start_recurso: Callable[..., Any],
) -> None:
    """3o site de ERR_RECURSO_INVALID_GLOSA — e o PRIMEIRO em todo caminho.

    `ST_ValidarRecurso` (intake) guarda `glosa_id` antes de qualquer coisa: a instancia termina
    LIMPO em `End_RecursoGlosaInvalidaOrigem` pelo boundary `BE_GlosaInvalidaValidacao`, sem
    sequer avaliar `recurso_admissibility`. Prova que o novo boundary esta wired (o gate ADR-0030
    exige o codigo consumption-covered nos TRES topicos).
    """
    inst = await start_recurso(
        glosa_id="",
        numero_guia_tiss=f"GUIA-TESTE-INVALIDO-VALID-{uuid.uuid4().hex[:8]}",
    )
    iid = inst["id"]

    await recurso_probe.drain()
    ended = await _await_end(engine, iid)

    assert _END_GLOSA_INVALIDA_ORIGEM in ended, (
        f"glosa_id ausente no intake deve terminar LIMPO em {_END_GLOSA_INVALIDA_ORIGEM} "
        f"(boundary catch BE_GlosaInvalidaValidacao). ended={ended}"
    )
    assert "BRT_Admissibilidade" not in ended, (
        "o guard de origem fica ANTES da DMN — um recurso sem glosa_id nao chega a ser admitido"
    )
    assert not (ended & _ENDS_ADVERSOS), "Validacao de origem nunca produz terminal adverso (L0)"
    await _assert_no_indeferimento_without_human_task(engine, iid)


# ===========================================================================
# Pendencia de documentacao
# ===========================================================================


async def test_pendencia_docs_recebidos_reavalia(
    engine: EngineRest,
    recurso_probe: RecursoEngineProbe,
    start_recurso: Callable[..., Any],
) -> None:
    """documentacao_recurso_completa=false => PENDENTE_DOCUMENTACAO; docs_received => reavalia.

    T3.1 event-gap remedy B: ST_PublishRecursoPended (spec/processes/bpmn/
    SP-OP-RECURSO-001_Recurso_Glosa.bpmn) now publishes agents.events.recurso.pended immediately
    downstream of ST_SolicitarDocumentos, on the same token — so the event itself is the
    execution-proof and the formerly-dead `notifications_of_type("recurso.request_documents")`
    assert (worker channel is del-kafka'd, finding 1) is redundant and dropped in favor of a
    single has_event(...) match on the new task's own payload vars (strongest-kwargs precedent
    #108/#123). LIVE-PROVEN (t3.1-event-gap-recurso-pended live validation): ST_PublishRecursoPended
    completed in engine history on the pendencia instance and has_event matched all 4 payload
    vars against a real CIB Seven 2.1.0 engine — strict-xfail mark removed on that proof.
    """
    glosa_id = _unique_glosa()
    inst = await start_recurso(documentacao_recurso_completa=False, glosa_id=glosa_id)
    iid = inst["id"]

    await recurso_probe.drain()

    assert recurso_probe.has_event(
        _RECURSO_PENDED,
        tenant_id="amh",
        numero_guia_tiss="GUIA-TESTE-0001",
        glosa_id=glosa_id,
        prestador_id="PREST-TESTE-001",
    ), "recurso.pended (ST_PublishRecursoPended) deve ser publicado com os business keys do processo"

    business_key = inst["businessKey"]
    correlate_payload = {
        "messageName": "msg.recurso.docs_received",
        "businessKey": business_key,
        "processVariables": {"documentacao_recurso_completa": {"value": True, "type": "Boolean"}},
    }
    async with httpx.AsyncClient(base_url=CIBSEVEN_BASE_URL, timeout=20.0) as client:
        resp = await client.post("/message", json=correlate_payload)
        assert resp.status_code in (200, 204), (
            f"Correlacao msg.recurso.docs_received falhou [{resp.status_code}]: {resp.text[:200]}"
        )

    await recurso_probe.drain()

    ut = await engine.await_user_task(iid, _UT_ANALISTA)
    assert "analista-recurso-glosa" in ut.candidate_groups
    ended = await engine.activity_instances_ended(iid)
    assert not (ended & _ENDS_ADVERSOS), "Reavaliacao por mensagem nunca auto-indefere (L0)"
    await _assert_no_indeferimento_without_human_task(engine, iid)


async def test_pendencia_expira_decisao_humana(
    engine: EngineRest,
    recurso_probe: RecursoEngineProbe,
    start_recurso: Callable[..., Any],
) -> None:
    """Prazo de pendencia (ICE_PrazoPendencia P5D) expira => UT_AnaliseRecursoAnalista (humano decide)."""
    inst = await start_recurso(documentacao_recurso_completa=False)
    iid = inst["id"]

    await recurso_probe.drain()

    job = await engine.await_timer_job(iid, "ICE_PrazoPendencia")
    await engine.execute_job(job.id)
    await recurso_probe.drain()

    ut = await engine.await_user_task(iid, _UT_ANALISTA)
    assert "analista-recurso-glosa" in ut.candidate_groups
    ended = await engine.activity_instances_ended(iid)
    assert not (ended & _ENDS_ADVERSOS), "Pendencia expirada nunca auto-indefere (L0)"
    await _assert_no_indeferimento_without_human_task(engine, iid)


# ===========================================================================
# Timers de SLA (auto-approve-on-timeout INVERTIDO)
# ===========================================================================


async def test_timer_alerta_sla_nao_interruptivo(
    engine: EngineRest,
    recurso_probe: RecursoEngineProbe,
    start_recurso: Callable[..., Any],
) -> None:
    """Timer BT_AlertaSlaRecurso (nao-interruptivo): notify_sla_risk recebe task; UT segue aberta."""
    inst = await start_recurso(glosa_type="administrativa")
    iid = inst["id"]

    await _drive_to_analista(engine, recurso_probe, iid)

    job = await engine.await_timer_job(iid, "BT_AlertaSlaRecurso")
    await engine.execute_job(job.id)
    await recurso_probe.drain()

    sla_alerts = recurso_probe.notifications_of_type("recurso.notify_sla_risk")
    assert sla_alerts, "Worker notify_sla_risk deve ser executado no alerta de SLA"

    open_keys = {t.task_definition_key for t in await engine.list_user_tasks(iid)}
    assert _UT_ANALISTA in open_keys, "Timer nao-interruptivo nao deve cancelar a User Task"


async def test_timer_sla_estourado_coordenacao_assume_nao_auto_aprova(
    engine: EngineRest,
    recurso_probe: RecursoEngineProbe,
    start_recurso: Callable[..., Any],
) -> None:
    """Timer BT_SlaAnaliseRecurso (interruptivo): UT_AnaliseRecursoAnalista cancelada; coordenacao assume.

    recurso.sla_breached (fase=analise) publicado via ST_PublishSlaBreach (generic
    operadora.events.publish, real kafka.publish call site — works). NAO ha auto-aprovacao/
    auto-desistencia por timeout.
    """
    inst = await start_recurso(glosa_type="administrativa")
    iid = inst["id"]

    await _drive_to_analista(engine, recurso_probe, iid)

    job = await engine.await_timer_job(iid, "BT_SlaAnaliseRecurso")
    await engine.execute_job(job.id)
    await recurso_probe.drain()

    assert recurso_probe.has_event(_RECURSO_SLA_BREACHED, fase="analise"), (
        "recurso.sla_breached (fase=analise) deve ser publicado"
    )

    ut_coord = await engine.await_user_task(iid, _UT_COORDENACAO)
    assert "coordenacao-recurso" in ut_coord.candidate_groups

    open_keys = {t.task_definition_key for t in await engine.list_user_tasks(iid)}
    assert _UT_ANALISTA not in open_keys, "UT_AnaliseRecursoAnalista deve ser cancelada (interruptivo)"

    ended = await engine.activity_instances_ended(iid)
    assert not (ended & _ENDS_ADVERSOS), "Estouro de SLA nunca auto-indefere (L0)"
    await _assert_no_indeferimento_without_human_task(engine, iid)


async def test_coordenacao_assume_e_indefere_humano(
    engine: EngineRest,
    recurso_probe: RecursoEngineProbe,
    start_recurso: Callable[..., Any],
) -> None:
    """SLA estourado; coordenacao assume e INDEFERIR => End_RecursoIndeferido com UT humana.

    Prova que mesmo no escalonamento o indeferimento passa por UT humana (invariante). O payload
    seta `valor_glosa_mantido_brl="150.00"` (string, a convencao BRL-como-string do fixture) — o
    valor monetario e parseado fail-closed antes do guard `<= 0`.
    """
    inst = await start_recurso(glosa_type="administrativa")
    iid = inst["id"]

    await _drive_to_analista(engine, recurso_probe, iid)
    job = await engine.await_timer_job(iid, "BT_SlaAnaliseRecurso")
    await engine.execute_job(job.id)
    await recurso_probe.drain()

    ut_coord = await engine.await_user_task(iid, _UT_COORDENACAO)
    await engine.complete_task_as_human(
        ut_coord.id,
        {
            "decisao_recurso": "INDEFERIR",
            "fundamentacao_indeferimento": "Prazo esgotado — coordenacao mantem a glosa (sintetico)",
            "valor_glosa_mantido_brl": "150.00",
            "referencia_contratual": "Clausula 12.3 (sintetico)",
            "analista_id": "coordenacao-sintetica-001",
        },
    )
    await recurso_probe.drain()

    ended = await _await_end(engine, iid)
    assert _END_INDEFERIDO in ended
    await _assert_no_indeferimento_without_human_task(engine, iid)
    assert recurso_probe.has_event(_RECURSO_COMPLETED, desfecho="indeferido_humano")


async def test_coordenacao_inadmissivel_humano_gated(
    engine: EngineRest,
    recurso_probe: RecursoEngineProbe,
    start_recurso: Callable[..., Any],
) -> None:
    """SLA estourado; coordenacao decide inadmissibilidade humana => End_RecursoInadmissivel (gated).

    RESCRITO: a condicao de `Flow_GWDec_Inadmissivel` deixou de cavalgar a variavel de DESISTENCIA
    do recorrente (`NAO_RECORRER + desfecho_humano=inadmissivel`) e passou a cavalgar `INDEFERIR +
    desfecho_humano=inadmissivel` — a inadmissibilidade e uma especie procedural do ato de
    pagador do qual ela deriva. A mecanica de `desfecho_humano` (inicializada em
    `ST_PublishReceived` para ser resolvivel no engine) e preservada verbatim.
    """
    inst = await start_recurso(glosa_type="administrativa", dentro_prazo_recurso=False)
    iid = inst["id"]

    await _drive_to_analista(engine, recurso_probe, iid)
    job = await engine.await_timer_job(iid, "BT_SlaAnaliseRecurso")
    await engine.execute_job(job.id)
    await recurso_probe.drain()

    ut_coord = await engine.await_user_task(iid, _UT_COORDENACAO)
    await engine.complete_task_as_human(
        ut_coord.id,
        {
            "decisao_recurso": "INDEFERIR",
            "desfecho_humano": "inadmissivel",
            "fundamentacao_indeferimento": "Recurso fora do prazo — inadmissivel (sintetico)",
            "valor_glosa_mantido_brl": "150.00",
            "referencia_contratual": "Prazo contratual de interposicao (sintetico DRAFT)",
            "analista_id": "coordenacao-sintetica-001",
        },
    )
    await recurso_probe.drain()

    ended = await _await_end(engine, iid)
    await _assert_no_indeferimento_without_human_task(engine, iid)
    assert _END_INADMISSIVEL in ended, f"inadmissibilidade humana => End_RecursoInadmissivel. ended={ended}"
    assert "ST_ComunicarInadmissibilidade" in ended, (
        "o prestador DEVE ser comunicado tambem quando o recurso e inadmitido"
    )
    assert recurso_probe.has_event(
        _RECURSO_COMPLETED, desfecho="inadmissivel_humano", analista_id="coordenacao-sintetica-001"
    ), "ST_PublishInadmissivel deve emitir completed(desfecho=inadmissivel_humano) com o analista_id"


# `test_loop_acompanhamento_limitado` MORREU SEM SUBSTITUTO (ADR-0040 §2.10). Ele exercitava o
# loop de acompanhamento do RECORRENTE (`ST_SubmitAppeal` -> `GW_AguardarResposta` ->
# `ICE_AguardarResposta` P5D -> `ST_TrackStatus` -> `GW_LoopLimite`), que foi removido inteiro: a
# operadora nao acompanha a resposta de terceiro ao seu proprio recurso — ela E a resposta. E a
# UNICA perda liquida de cobertura do pacote, e ela e correta: o comportamento testado nao deve
# existir. O ciclo que RESTA (`SOLICITAR_INFO`) e coberto por
# `test_solicitar_info_repetido_nao_sobrevive_ao_teto_absoluto` abaixo.


async def test_prazo_max_recurso_escala_humano(
    engine: EngineRest,
    recurso_probe: RecursoEngineProbe,
    start_recurso: Callable[..., Any],
) -> None:
    """Timer BT_PrazoMaxRecurso (teto absoluto): escalate_ans_timeout; UT_EscalonamentoPrazo."""
    inst = await start_recurso(glosa_type="administrativa")
    iid = inst["id"]

    await _drive_to_analista(engine, recurso_probe, iid)

    job = await engine.await_timer_job(iid, "BT_PrazoMaxRecurso")
    await engine.execute_job(job.id)
    await recurso_probe.drain()

    escalations = recurso_probe.notifications_of_type("recurso.escalate_ans_timeout")
    assert recurso_probe.has_event(_RECURSO_SLA_BREACHED, fase="prazo_max") or escalations, (
        "escalate_ans_timeout deve publicar recurso.sla_breached (fase=prazo_max)"
    )

    ut_esc = await engine.await_user_task(iid, _UT_ESCALONAMENTO)
    assert "coordenacao-recurso" in ut_esc.candidate_groups
    ended = await engine.activity_instances_ended(iid)
    assert not (ended & _ENDS_ADVERSOS), "Estouro do prazo max nunca auto-desfecho adverso (L0)"
    await _assert_no_indeferimento_without_human_task(engine, iid)


# ===========================================================================
# GAP-RECURSO-1 — teto P30D (RN 424): UMA UNICA JANELA ABSOLUTA em toda UT humana
# ===========================================================================


async def test_prazo_max_ancora_absoluta_nao_no_attach_da_ut(
    engine: EngineRest,
    recurso_probe: RecursoEngineProbe,
    start_recurso: Callable[..., Any],
) -> None:
    """GAP-RECURSO-1: o teto P30D ancora em data_recebimento_recurso_iso, NAO no attach da UT."""
    anchor_date = (datetime.now(UTC) + timedelta(days=40)).date()
    anchor_midnight = datetime.combine(anchor_date, datetime.min.time())

    inst = await start_recurso(
        glosa_type="administrativa",
        data_recebimento_recurso_iso=anchor_date.isoformat(),
    )
    iid = inst["id"]

    await _drive_to_analista(engine, recurso_probe, iid)
    job_max = await engine.await_timer_job(iid, "BT_PrazoMaxRecurso")
    assert job_max.due_date, "dueDate do teto deve estar presente (timeDate absoluto)"

    due = _parse_due(job_max.due_date)
    expected_anchor_based = anchor_midnight + timedelta(days=30)
    now_naive = datetime.now(UTC).replace(tzinfo=None)
    legacy_attach_based = now_naive + timedelta(days=30)

    tolerance = timedelta(hours=6)
    far_enough = timedelta(days=20)

    assert abs(due - expected_anchor_based) < tolerance, (
        f"BT_PrazoMaxRecurso.dueDate={due} nao bate com ancora+P30D={expected_anchor_based} "
        "(GAP-RECURSO-1: teto deve ser absoluto, ancorado em data_recebimento_recurso_iso)"
    )
    assert abs(due - legacy_attach_based) > far_enough, (
        "BT_PrazoMaxRecurso.dueDate esta perto de attach-da-UT+P30D — regressao GAP-RECURSO-1"
    )

    await engine.execute_job(job_max.id)
    await recurso_probe.drain()
    assert recurso_probe.has_event(_RECURSO_SLA_BREACHED, fase="prazo_max")
    ut_esc = await engine.await_user_task(iid, _UT_ESCALONAMENTO)
    assert "coordenacao-recurso" in ut_esc.candidate_groups
    await _assert_no_indeferimento_without_human_task(engine, iid)


async def test_prazo_max_ancora_defaultada_pelo_intake_com_warning(
    engine: EngineRest,
    recurso_probe: RecursoEngineProbe,
    start_recurso: Callable[..., Any],
) -> None:
    """Sem `data_recebimento_recurso_iso` no start, o INTAKE a normaliza — nao a DMN.

    SUBSTITUI `test_prazo_max_fail_safe_ancora_data_ciencia_glosa`. O fail-safe antigo caia na
    data de ciencia da glosa pelo prestador: a ancora do RECORRENTE. Isso trocava de perspectiva
    em silencio, e o prazo de INTERPOSICAO nao e da operadora — a expressao FEEL nao tem mais esse
    ramo. Agora `operadora.recurso.validate_recurso` (ST_ValidarRecurso) garante a ancora
    nao-nula: defaulta fail-safe para HOJE/UTC (com warning no worker) e a ESCREVE de volta como
    variavel de processo, de onde `recurso_sla` a le.

    A tempestividade continua aferida SEPARADAMENTE: `dentro_prazo_recurso=False` roteia a
    `ANALISE_HUMANA` por `recurso_admissibility` — um recurso com ancora duvidosa vai a humano
    pela admissibilidade, nao passa despercebido pelo relogio. Read-only (nao executa o job).
    """
    hoje = datetime.now(UTC).date()
    ciencia_date = hoje - timedelta(days=5)

    inst = await start_recurso(
        glosa_type="administrativa",
        data_ciencia_glosa=ciencia_date.isoformat(),
        data_recebimento_recurso_iso=None,
    )
    iid = inst["id"]

    await _drive_to_analista(engine, recurso_probe, iid)

    # O intake escreveu a ancora normalizada de volta ao escopo do processo.
    ancora = await engine.get_variable(iid, "data_recebimento_recurso_iso")
    assert ancora == hoje.isoformat(), (
        f"ST_ValidarRecurso deve defaultar a ancora para HOJE/UTC; veio {ancora!r}"
    )

    job_max = await engine.await_timer_job(iid, "BT_PrazoMaxRecurso")
    assert job_max.due_date, "dueDate do teto deve existir mesmo sem ancora no start"

    due = _parse_due(job_max.due_date)
    expected = datetime.combine(hoje, datetime.min.time()) + timedelta(days=30)
    assert abs(due - expected) < timedelta(hours=6), (
        f"BT_PrazoMaxRecurso.dueDate={due} deveria ancorar em HOJE+P30D={expected} (ancora "
        "defaultada pelo intake)"
    )
    ciencia_based = datetime.combine(ciencia_date, datetime.min.time()) + timedelta(days=30)
    assert abs(due - ciencia_based) > timedelta(days=2), (
        "o teto NAO pode ancorar na data de ciencia do prestador — essa e a ancora do recorrente "
        "e ela saiu de recurso_sla inteira (ADR-0040 §2.3)"
    )


async def test_prazo_max_coord_mesmo_instante_absoluto(
    engine: EngineRest,
    recurso_probe: RecursoEngineProbe,
    start_recurso: Callable[..., Any],
) -> None:
    """GAP-RECURSO-1: BT_SlaAnaliseRecurso mata BT_PrazoMaxRecurso; BT_PrazoMaxCoord reanexa o teto
    no MESMO instante absoluto (nunca janela nova).

    The due-date-equality proof (before the final `execute_job`) is independent of finding 2 —
    only the trailing execution (-> ST_EscalateAnsTimeout) is blocked.
    """
    inst = await start_recurso(glosa_type="administrativa")
    iid = inst["id"]

    await _drive_to_analista(engine, recurso_probe, iid)
    job_original = await engine.await_timer_job(iid, "BT_PrazoMaxRecurso")
    assert job_original.due_date, "teto original deve ter dueDate (timeDate absoluto)"

    job_sla = await engine.await_timer_job(iid, "BT_SlaAnaliseRecurso")
    await engine.execute_job(job_sla.id)
    await recurso_probe.drain()

    ut_coord = await engine.await_user_task(iid, _UT_COORDENACAO)
    assert "coordenacao-recurso" in ut_coord.candidate_groups

    job_max = await engine.await_timer_job(iid, "BT_PrazoMaxCoord")
    assert job_max.due_date, "teto reanexado deve ter dueDate (timeDate absoluto)"
    assert _parse_due(job_max.due_date) == _parse_due(job_original.due_date), (
        f"BT_PrazoMaxCoord.dueDate={job_max.due_date} != BT_PrazoMaxRecurso.dueDate="
        f"{job_original.due_date} — a escalada NAO pode reiniciar a janela do teto (GAP-RECURSO-1)"
    )

    await engine.execute_job(job_max.id)
    await recurso_probe.drain()

    assert recurso_probe.has_event(_RECURSO_SLA_BREACHED, fase="prazo_max"), (
        "BT_PrazoMaxCoord deve publicar recurso.sla_breached (fase=prazo_max), igual ao teto original"
    )

    ut_esc = await engine.await_user_task(iid, _UT_ESCALONAMENTO)
    assert "coordenacao-recurso" in ut_esc.candidate_groups
    ended = await engine.activity_instances_ended(iid)
    assert not (ended & _ENDS_ADVERSOS), "Teto reanexado na coordenacao nunca auto-desfecho adverso (L0)"
    await _assert_no_indeferimento_without_human_task(engine, iid)


async def test_prazo_max_auditor_mesmo_instante_absoluto(
    engine: EngineRest,
    recurso_probe: RecursoEngineProbe,
    start_recurso: Callable[..., Any],
) -> None:
    """GAP-RECURSO-1: ESCALAR_AUDITOR roteia sem teto proprio; BT_PrazoMaxAuditor reanexa o teto
    no MESMO instante absoluto (o hop analista->auditor nao estende o teto contratual).
    """
    inst = await start_recurso(glosa_type="administrativa")
    iid = inst["id"]

    ut = await _drive_to_analista(engine, recurso_probe, iid)
    job_original = await engine.await_timer_job(iid, "BT_PrazoMaxRecurso")
    assert job_original.due_date, "teto original deve ter dueDate (timeDate absoluto)"

    await engine.complete_task_as_human(ut.id, {"decisao_recurso": "ESCALAR_AUDITOR"})
    await recurso_probe.drain()

    ut_auditor = await engine.await_user_task(iid, _UT_AUDITOR)
    assert "medico-auditor" in ut_auditor.candidate_groups

    job_max = await engine.await_timer_job(iid, "BT_PrazoMaxAuditor")
    assert job_max.due_date, "teto no auditor deve ter dueDate (timeDate absoluto)"
    assert _parse_due(job_max.due_date) == _parse_due(job_original.due_date), (
        f"BT_PrazoMaxAuditor.dueDate={job_max.due_date} != BT_PrazoMaxRecurso.dueDate="
        f"{job_original.due_date} — o hop ao auditor NAO pode reiniciar a janela (GAP-RECURSO-1)"
    )

    await engine.execute_job(job_max.id)
    await recurso_probe.drain()

    assert recurso_probe.has_event(_RECURSO_SLA_BREACHED, fase="prazo_max"), (
        "BT_PrazoMaxAuditor deve publicar recurso.sla_breached (fase=prazo_max), igual ao teto original"
    )

    ut_esc = await engine.await_user_task(iid, _UT_ESCALONAMENTO)
    assert "coordenacao-recurso" in ut_esc.candidate_groups

    open_keys = {t.task_definition_key for t in await engine.list_user_tasks(iid)}
    assert _UT_AUDITOR in open_keys, "BT_PrazoMaxAuditor e nao-interruptivo (cancelActivity=false)"

    ended = await engine.activity_instances_ended(iid)
    assert not (ended & _ENDS_ADVERSOS), "Teto reanexado no auditor nunca auto-desfecho adverso (L0)"
    await _assert_no_indeferimento_without_human_task(engine, iid)


async def test_prazo_max_escalonamento_sem_cascata(
    engine: EngineRest,
    recurso_probe: RecursoEngineProbe,
    start_recurso: Callable[..., Any],
) -> None:
    """GAP-RECURSO-1 (guard adversarial): UT_EscalonamentoPrazo NAO carrega boundary de teto.

    Executes BT_PrazoMaxRecurso immediately -> ST_EscalateAnsTimeout, which has zero registered
    worker (finding 2): UT_EscalonamentoPrazo never appears.
    """
    inst = await start_recurso(glosa_type="administrativa")
    iid = inst["id"]

    await _drive_to_analista(engine, recurso_probe, iid)
    job_first = await engine.await_timer_job(iid, "BT_PrazoMaxRecurso")
    await engine.execute_job(job_first.id)
    await recurso_probe.drain()

    await engine.await_user_task(iid, _UT_ESCALONAMENTO)
    assert recurso_probe.has_event(_RECURSO_SLA_BREACHED, fase="prazo_max")
    breaches_before = len(
        [e for e in recurso_probe.events_on(_RECURSO_SLA_BREACHED) if e["payload"].get("fase") == "prazo_max"]
    )

    pending = {job.activity_id for job in await engine.list_timer_jobs(iid)}
    assert "BT_PrazoMaxEscalonamento" not in pending, (
        "UT_EscalonamentoPrazo NAO pode carregar boundary de teto (timeDate ja-passado => cascata)"
    )
    assert pending <= {"BT_AlertaSlaRecurso", "BT_SlaAnaliseRecurso"}, (
        f"jobs de timer inesperados apos o estouro do teto: {pending}"
    )

    await recurso_probe.drain(rounds=6)
    open_esc = [t for t in await engine.list_user_tasks(iid) if t.task_definition_key == _UT_ESCALONAMENTO]
    assert len(open_esc) == 1, f"cascata detectada: {len(open_esc)} UT_EscalonamentoPrazo abertas"
    breaches_after = len(
        [e for e in recurso_probe.events_on(_RECURSO_SLA_BREACHED) if e["payload"].get("fase") == "prazo_max"]
    )
    assert breaches_after == breaches_before, "sla_breached (prazo_max) re-publicado sem novo estouro"

    assert await engine.instance_is_active(iid)
    ended = await engine.activity_instances_ended(iid)
    assert not (ended & _ENDS_ADVERSOS), "Estouro do teto nunca auto-desfecho adverso (L0)"
    await _assert_no_indeferimento_without_human_task(engine, iid)


async def test_dmn_recurso_sla_valores(
    engine: EngineRest,
    recurso_probe: RecursoEngineProbe,
    start_recurso: Callable[..., Any],
) -> None:
    """glosa_type=clinica, valor alto => DMN recurso_sla resolve sla_analise (ISO); timers existem.

    Os VALORES de prazo nao mudaram (P15D/P9D analise, P30D teto) — so a FONTE: o prazo de
    resposta ao recurso e contratual (DRAFT/verify), com RN 501/2022 pelo fluxo TISS; RN 424/2017
    permanece citada APENAS na row tecnico-clinica e ainda assim condicionada a junta medica.
    """
    inst = await start_recurso(glosa_type="clinica", valor_glosado_brl="75000.00")
    iid = inst["id"]

    await _drive_to_analista(engine, recurso_probe, iid)

    job = await engine.await_timer_job(iid, "BT_AlertaSlaRecurso")
    assert job.activity_id == "BT_AlertaSlaRecurso"
    job_sla = await engine.await_timer_job(iid, "BT_SlaAnaliseRecurso")
    assert job_sla.activity_id == "BT_SlaAnaliseRecurso"
    job_max = await engine.await_timer_job(iid, "BT_PrazoMaxRecurso")
    assert job_max.activity_id == "BT_PrazoMaxRecurso"

    sla = await engine.get_variable(iid, "sla")
    assert isinstance(sla, dict), f"BRT_Sla deve resolver o resultVariable 'sla'; veio {sla!r}"
    fonte = str(sla.get("fonte_regulatoria", ""))
    assert "Prazo contratual" in fonte, (
        f"a fonte do prazo passou a ser o prazo CONTRATUAL de resposta ao recurso; veio {fonte!r}"
    )
    assert "junta medica" in fonte, (
        "na row tecnico-clinica a citacao de RN 424/2017 fica CONDICIONADA a instauracao de junta"
    )


# ===========================================================================
# Roteamento DMN (catch-all fail-safe) — sem engine, varredura estatica do XML
# ===========================================================================


def test_dmn_admissibility_sem_saida_adversa() -> None:
    """recurso_admissibility roteia EXATAMENTE {SEGUE_ANALISE, PENDENTE_DOCUMENTACAO, ANALISE_HUMANA}."""
    from xml.etree import ElementTree as ET

    tree = ET.parse(_DMN_ADMISSIBILITY)
    root = tree.getroot()

    def _local(tag: str) -> str:
        return tag.rsplit("}", 1)[-1]

    roteamentos: set[str] = set()
    last_rule_first_output: str | None = None
    for rule in (e for e in root.iter() if _local(e.tag) == "rule"):
        outputs = [c for c in rule if _local(c.tag) == "outputEntry"]
        assert outputs, "cada rule deve ter outputEntry"
        text_el = next((c for c in outputs[0] if _local(c.tag) == "text"), None)
        assert text_el is not None and text_el.text
        val = text_el.text.strip().strip('"')
        roteamentos.add(val)
        last_rule_first_output = val

    assert roteamentos == {"SEGUE_ANALISE", "PENDENTE_DOCUMENTACAO", "ANALISE_HUMANA"}, (
        f"dominio inesperado: {roteamentos} — NAO pode conter INDEFERIR/INADMISSIVEL (L0)"
    )
    joined = " ".join(roteamentos)
    assert "INDEFERIR" not in joined and "DESIST" not in joined and "INADMISS" not in joined
    assert last_rule_first_output == "ANALISE_HUMANA", "row catch-all deve rotear a ANALISE_HUMANA"


def test_dmn_eligibility_glosa_tecnica_vai_ao_auditor() -> None:
    """recurso_eligibility roteia EXATAMENTE {SEGUE_MERITO, ANALISE_HUMANA}; tecnica/clinica -> auditor."""
    from xml.etree import ElementTree as ET

    tree = ET.parse(_DMN_ELIGIBILITY)
    root = tree.getroot()

    def _local(tag: str) -> str:
        return tag.rsplit("}", 1)[-1]

    roteamentos: set[str] = set()
    grupos: set[str] = set()
    last_roteamento: str | None = None
    tecnica_grupo: str | None = None
    for rule in (e for e in root.iter() if _local(e.tag) == "rule"):
        outputs = [c for c in rule if _local(c.tag) == "outputEntry"]
        assert len(outputs) >= 2
        rot_text = next((c for c in outputs[0] if _local(c.tag) == "text"), None)
        grp_text = next((c for c in outputs[1] if _local(c.tag) == "text"), None)
        assert rot_text is not None and rot_text.text
        assert grp_text is not None and grp_text.text
        rot = rot_text.text.strip().strip('"')
        grp = grp_text.text.strip().strip('"')
        roteamentos.add(rot)
        grupos.add(grp)
        last_roteamento = rot
        inputs = [c for c in rule if _local(c.tag) == "inputEntry"]
        first_in = next((c for c in inputs[0] if _local(c.tag) == "text"), None)
        if first_in is not None and first_in.text and "tecnica" in first_in.text:
            tecnica_grupo = grp

    assert roteamentos == {"SEGUE_MERITO", "ANALISE_HUMANA"}, (
        f"dominio inesperado: {roteamentos} — NAO pode conter INDEFERIR (L0)"
    )
    assert "RECORRIVEL" not in " ".join(roteamentos), (
        "`RECORRIVEL` e vocabulario de recorrente — o julgador roteia ao MERITO, nao decide se "
        "algo e recorrivel (ADR-0040)"
    )
    assert grupos == {"analista-recurso-glosa", "medico-auditor"}
    assert tecnica_grupo == "medico-auditor", "glosa tecnica/clinica deve rotear a medico-auditor"
    assert last_roteamento == "ANALISE_HUMANA", "row catch-all deve rotear a ANALISE_HUMANA"


def test_dmn_typeref_allowlist() -> None:
    """Toda DMN do processo usa typeRef in {string, boolean, integer, long, double, date}."""
    from xml.etree import ElementTree as ET

    allowed = {"string", "boolean", "integer", "long", "double", "date"}
    for dmn_path in (_DMN_ADMISSIBILITY, _DMN_ELIGIBILITY, _DMN_SLA):
        tree = ET.parse(dmn_path)
        for el in tree.getroot().iter():
            type_ref = el.get("typeRef")
            if type_ref is not None:
                assert type_ref in allowed, (
                    f"{dmn_path.name}: typeRef invalido '{type_ref}' (allowlist={sorted(allowed)})"
                )
                assert type_ref != "number", f"{dmn_path.name}: 'number' e proibido (use double)"


def test_todos_os_fins_emitem_evento_de_dominio() -> None:
    """Cada end event do BPMN e precedido por uma service task de publish recurso.completed."""
    from xml.etree import ElementTree as ET

    tree = ET.parse(_BPMN)
    root = tree.getroot()

    def _local(tag: str) -> str:
        return tag.rsplit("}", 1)[-1]

    desfechos: set[str] = set()
    for el in root.iter():
        if _local(el.tag) == "inputParameter" and el.get("name") == "event_desfecho" and el.text:
            desfechos.add(el.text.strip())

    esperados = {
        "deferido_humano",
        "deferido_parcial_humano",
        "indeferido_humano",
        "inadmissivel_humano",
    }
    assert esperados <= desfechos, (
        f"Desfechos faltando no BPMN: {esperados - desfechos} "
        "(todos os fins devem publicar recurso.completed)"
    )
    proibidos = {"deferido", "indeferido", "parcialmente_deferido", "inadmissivel", "nao_interposto_humano"}
    assert not (proibidos & desfechos), (
        f"vocabulario antigo sobrevivendo no BPMN: {proibidos & desfechos} — os desfechos passaram "
        "a nomear a decisao HUMANA da operadora, sem shim"
    )


def test_recurso_worker_registry_drift_vs_bpmn() -> None:
    """Registry drift — static, no engine needed. Asserts EXACT SETS, not counts.

    12 topicos registrados = 13 - 3 (`submit_appeal`, `track_status`, `reconcile_payment`,
    deletados com o ramo do recorrente) + 2 (`comunicar_resposta`, `handoff_pagamento`); a
    renomeacao `register_desistencia` -> `registrar_indeferimento` troca um nome, e a promocao de
    `validate_recurso` de orfao a referenciado troca um STATUS — nenhuma das duas altera a
    contagem. LACUNAS = conjunto vazio: todo topico que o BPMN declara tem worker.

    Orfaos (4): registrados mas sem service task BPMN — `assess_eligibility` (as duas DMN sao
    avaliadas engine-side pelos BRT; o worker existe para o caminho de agente), `prepare_dossier`,
    `escalate_to_junta` (o ramo ESCALAR_AUDITOR vai direto a `UT_RevisaoAuditorMedico`, sem
    service task) e `publish_completed` (dobra no generico `operadora.events.publish`).
    """
    from xml.etree import ElementTree as ET

    tree = ET.parse(_BPMN)
    camunda_ns = "{http://camunda.org/schema/1.0/bpmn}"
    bpmn_topics = {
        el.get(f"{camunda_ns}topic")
        for el in tree.getroot().iter()
        if el.get(f"{camunda_ns}topic") is not None
    }
    bpmn_recurso_topics = {t for t in bpmn_topics if t.startswith("operadora.recurso.")}

    transport = CibSevenWorkerTransport(CIBSEVEN_BASE_URL)
    harness = WorkerHarness(transport, worker_id="static-registry-drift-check")
    register_recurso_workers(harness, FakeKafkaPublisher())
    registered_recurso_topics = {t for t in harness.registered_topics if t.startswith("operadora.recurso.")}

    assert len(registered_recurso_topics) == 12, (
        f"esperava 12 topicos operadora.recurso.*; achei {len(registered_recurso_topics)}: "
        f"{sorted(registered_recurso_topics)}"
    )
    assert bpmn_recurso_topics == {
        "operadora.recurso.validate_recurso",
        "operadora.recurso.request_documents",
        "operadora.recurso.analyze_request",
        "operadora.recurso.registrar_indeferimento",
        "operadora.recurso.comunicar_resposta",
        "operadora.recurso.handoff_pagamento",
        "operadora.recurso.notify_sla_risk",
        "operadora.recurso.escalate_ans_timeout",
    }, f"topicos do BPMN mudaram: {sorted(bpmn_recurso_topics)}"

    orphans = registered_recurso_topics - bpmn_recurso_topics
    gaps = bpmn_recurso_topics - registered_recurso_topics

    assert orphans == _RECURSO_ORPHAN_TOPICS, (
        f"registered-but-not-in-BPMN drift changed (update _RECURSO_ORPHAN_TOPICS): {orphans}"
    )
    assert gaps == _RECURSO_UNIMPLEMENTED_TOPICS, (
        f"BPMN-declared-but-unregistered drift changed (update _RECURSO_UNIMPLEMENTED_TOPICS): {gaps}"
    )
    for morto in (
        "operadora.recurso.submit_appeal",
        "operadora.recurso.track_status",
        "operadora.recurso.reconcile_payment",
        "operadora.recurso.register_desistencia",
    ):
        assert morto not in registered_recurso_topics, f"{morto} sobreviveu — o desenho nao tem shim"
        assert morto not in bpmn_recurso_topics, f"{morto} sobreviveu no BPMN — o desenho nao tem shim"


def test_todos_os_fins_comunicam_o_prestador() -> None:
    """M6 nesta cadeia: os 5 terminais de NEGOCIO passam por `operadora.recurso.comunicar_resposta`
    ANTES do `ST_Publish*`.

    A operadora responde ao prestador em TODO desfecho que o afeta — favoravel ou adverso. Isso ja
    era verdade por desenho; aqui passa a ser PROVADO estaticamente sobre o grafo do BPMN, caminhando
    para tras a partir de cada end event de negocio.
    """
    from xml.etree import ElementTree as ET

    tree = ET.parse(_BPMN)
    root = tree.getroot()

    def _local(tag: str) -> str:
        return tag.rsplit("}", 1)[-1]

    camunda_ns = "{http://camunda.org/schema/1.0/bpmn}"
    flows_by_target: dict[str, list[str]] = {}
    topic_of: dict[str, str] = {}
    for el in root.iter():
        if _local(el.tag) == "sequenceFlow":
            flows_by_target.setdefault(el.get("targetRef", ""), []).append(el.get("sourceRef", ""))
        topic = el.get(f"{camunda_ns}topic")
        if topic is not None and el.get("id"):
            topic_of[el.get("id", "")] = topic

    def _reaches_comunicar(end_id: str) -> bool:
        seen: set[str] = set()
        stack = list(flows_by_target.get(end_id, []))
        while stack:
            node = stack.pop()
            if node in seen:
                continue
            seen.add(node)
            if topic_of.get(node) == "operadora.recurso.comunicar_resposta":
                return True
            # Nao atravessa User Task nem gateway: a comunicacao tem de estar no MESMO trecho
            # terminal, entre a decisao humana e o evento de dominio.
            if node.startswith(("UT_", "GW_")):
                continue
            stack.extend(flows_by_target.get(node, []))
        return False

    for end_id in sorted(_ENDS_NEGOCIO):
        assert end_id in flows_by_target, f"{end_id} nao existe no BPMN"
        assert _reaches_comunicar(end_id), (
            f"{end_id} nao e precedido por operadora.recurso.comunicar_resposta — a operadora tem de "
            "emitir a sua resposta ao prestador em TODO terminal que o afeta"
        )


def test_decisao_invalida_termina_em_erro_sem_efeito(
    engine: EngineRest,
) -> None:
    """Default fail-closed de `GW_DecisaoRecurso`: uma decisao ausente/desconhecida vai a um
    terminal de ERRO, nunca a uma acao.

    Prova ESTATICA sobre o BPMN (o gateway default e uma propriedade do artefato, e o teste vivo
    correspondente e `test_decisao_invalida_termina_em_erro_sem_efeito_no_engine`). Antes, o
    default de `GW_DecisaoRecurso` era `Flow_GWDec_Recorrer` e o de `GW_MeritoAuditor` era
    `Flow_GWMerito_Manter`: uma variavel ausente PRODUZIA um ato.
    """
    del engine
    from xml.etree import ElementTree as ET

    tree = ET.parse(_BPMN)
    root = tree.getroot()

    def _local(tag: str) -> str:
        return tag.rsplit("}", 1)[-1]

    defaults = {
        el.get("id"): el.get("default")
        for el in root.iter()
        if _local(el.tag) == "exclusiveGateway" and el.get("default")
    }
    assert defaults.get("GW_DecisaoRecurso") == "Flow_GWDec_Invalida"
    assert defaults.get("GW_MeritoAuditor") == "Flow_GWMerito_Invalida"

    targets = {el.get("id"): el.get("targetRef") for el in root.iter() if _local(el.tag) == "sequenceFlow"}
    assert targets.get("Flow_GWDec_Invalida") == _END_ERR_DECISAO_INVALIDA
    assert targets.get("Flow_GWMerito_Invalida") == _END_ERR_DECISAO_INVALIDA

    err_ends = {
        el.get("id")
        for el in root.iter()
        if _local(el.tag) == "endEvent" and any(_local(c.tag) == "errorEventDefinition" for c in el)
    }
    assert err_ends == {_END_ERR_DECISAO_INVALIDA}


async def test_decisao_invalida_termina_em_erro_sem_efeito_no_engine(
    engine: EngineRest,
    recurso_probe: RecursoEngineProbe,
    start_recurso: Callable[..., Any],
) -> None:
    """Contra o engine real: o analista conclui a UT sem `decisao_recurso` => o default leva a
    `End_ErrRecursoDecisaoInvalida`, e NENHUM efeito e materializado (nem indeferimento, nem
    comunicacao, nem ordem de pagamento)."""
    inst = await start_recurso(glosa_type="administrativa")
    iid = inst["id"]

    ut = await _drive_to_analista(engine, recurso_probe, iid)
    await engine.complete_task_as_human(ut.id, {})
    await recurso_probe.drain()
    ended = await _await_end(engine, iid)

    assert _END_ERR_DECISAO_INVALIDA in ended, (
        f"decisao ausente => {_END_ERR_DECISAO_INVALIDA} (default fail-closed). ended={ended}"
    )
    assert not (ended & _ENDS_ADVERSOS), "um default NUNCA pode produzir um efeito adverso"
    assert _END_DEFERIDO not in ended, "um default NUNCA pode produzir um deferimento"
    assert "ST_RegistrarIndeferimento" not in ended
    assert not recurso_probe.notifications_of_type("recurso.comunicar_resposta")


async def test_merito_auditor_invalido_termina_em_erro_sem_efeito(
    engine: EngineRest,
    recurso_probe: RecursoEngineProbe,
    start_recurso: Callable[..., Any],
) -> None:
    """Mesmo default fail-closed no gateway do auditor: `decisao_auditor_recurso` fora do dominio
    termina em erro, nao numa acao por omissao."""
    inst = await start_recurso(glosa_type="tecnica")
    iid = inst["id"]

    ut = await _drive_to_analista(engine, recurso_probe, iid)
    await engine.complete_task_as_human(ut.id, {"decisao_recurso": "ESCALAR_AUDITOR"})
    await recurso_probe.drain()

    ut_auditor = await engine.await_user_task(iid, _UT_AUDITOR)
    await engine.complete_task_as_human(ut_auditor.id, {"parecer_auditor": "parecer sem decisao (sintetico)"})
    await recurso_probe.drain()
    ended = await _await_end(engine, iid)

    assert _END_ERR_DECISAO_INVALIDA in ended, (
        f"merito ausente => {_END_ERR_DECISAO_INVALIDA} (default fail-closed). ended={ended}"
    )
    assert not (ended & _ENDS_ADVERSOS)
    assert _END_DEFERIDO not in ended


async def test_escalonamento_prazo_emite_vocabulario_de_pagador(
    engine: EngineRest,
    recurso_probe: RecursoEngineProbe,
    start_recurso: Callable[..., Any],
) -> None:
    """`UT_EscalonamentoPrazo` e o TERCEIRO canal humano, e ele fala o mesmo vocabulario de pagador.

    A matriz da auditoria nao o apanhou: a documentacao dele listava
    `decisao_recurso in {RECORRER, NAO_RECORRER, ...}`. Aqui a coordenacao INDEFERE a partir do
    escalonamento do teto e a instancia alcanca o terminal adverso humano-gated — provando que o
    canal aceita o dominio de pagador de ponta a ponta.
    """
    inst = await start_recurso(glosa_type="administrativa")
    iid = inst["id"]

    await _drive_to_analista(engine, recurso_probe, iid)
    job = await engine.await_timer_job(iid, "BT_PrazoMaxRecurso")
    await engine.execute_job(job.id)
    await recurso_probe.drain()

    ut_esc = await engine.await_user_task(iid, _UT_ESCALONAMENTO)
    assert "coordenacao-recurso" in ut_esc.candidate_groups
    await engine.complete_task_as_human(
        ut_esc.id,
        {
            "decisao_recurso": "INDEFERIR",
            "fundamentacao_indeferimento": "Teto de resposta estourado — coordenacao indefere (sintetico)",
            "valor_glosa_mantido_brl": "150.00",
            "referencia_contratual": "Clausula 12.3 (sintetico)",
            "analista_id": "coordenacao-sintetica-001",
        },
    )
    await recurso_probe.drain()
    ended = await _await_end(engine, iid)

    await _assert_no_indeferimento_without_human_task(engine, iid)
    assert _END_INDEFERIDO in ended, (
        f"UT_EscalonamentoPrazo deve aceitar INDEFERIR (vocabulario de pagador). ended={ended}"
    )
    assert recurso_probe.has_event(
        _RECURSO_COMPLETED, desfecho="indeferido_humano", analista_id="coordenacao-sintetica-001"
    )


async def test_solicitar_info_repetido_nao_sobrevive_ao_teto_absoluto(
    engine: EngineRest,
    recurso_probe: RecursoEngineProbe,
    start_recurso: Callable[..., Any],
) -> None:
    """m10: o ciclo `SOLICITAR_INFO` NAO tem contador — e isso e deliberado (um teto por contagem
    produziria um auto-desfecho por esgotamento, o anti-padrao que ADR-0018 proibe). O que o
    limita e o TETO ABSOLUTO, que reentrar no ciclo NAO adia.

    Tres ciclos `SOLICITAR_INFO -> docs recebidos -> UT` e, com o relogio alem de
    `prazo_max_absoluto_iso`, o token tem de estar em `UT_EscalonamentoPrazo` — NUNCA num terminal
    adverso, e NUNCA num quarto ciclo.
    """
    inst = await start_recurso(glosa_type="administrativa")
    iid = inst["id"]
    business_key = inst["businessKey"]

    ut = await _drive_to_analista(engine, recurso_probe, iid)
    due_inicial = (await engine.await_timer_job(iid, "BT_PrazoMaxRecurso")).due_date
    assert due_inicial

    for _ in range(3):
        await engine.complete_task_as_human(ut.id, {"decisao_recurso": "SOLICITAR_INFO"})
        await recurso_probe.drain()
        async with httpx.AsyncClient(base_url=CIBSEVEN_BASE_URL, timeout=20.0) as client:
            resp = await client.post(
                "/message",
                json={
                    "messageName": "msg.recurso.docs_received",
                    "businessKey": business_key,
                    "processVariables": {"documentacao_recurso_completa": {"value": True, "type": "Boolean"}},
                },
            )
            assert resp.status_code in (200, 204)
        await recurso_probe.drain()
        ut = await engine.await_user_task(iid, _UT_ANALISTA)
        # O teto NAO e recalculado a cada volta: o instante absoluto e o mesmo.
        due_agora = (await engine.await_timer_job(iid, "BT_PrazoMaxRecurso")).due_date
        assert due_agora and _parse_due(due_agora) == _parse_due(due_inicial), (
            "reentrar no ciclo SOLICITAR_INFO NAO pode adiar o teto absoluto (GAP-RECURSO-1)"
        )
        ended_ciclo = await engine.activity_instances_ended(iid)
        assert not (ended_ciclo & _ENDS_ADVERSOS), "o ciclo nunca produz um desfecho adverso"

    # Estoura o teto: o destino e HUMANO, nunca um terminal.
    job_max = await engine.await_timer_job(iid, "BT_PrazoMaxRecurso")
    await engine.execute_job(job_max.id)
    await recurso_probe.drain()

    ut_esc = await engine.await_user_task(iid, _UT_ESCALONAMENTO)
    assert "coordenacao-recurso" in ut_esc.candidate_groups
    ended = await engine.activity_instances_ended(iid)
    assert not (ended & _ENDS_ADVERSOS), "o estouro do teto nunca produz um desfecho adverso (L0)"
    await _assert_no_indeferimento_without_human_task(engine, iid)


# ===========================================================================
# Idempotencia (business key)
# ===========================================================================


async def test_business_key_uma_instancia_por_glosa(
    engine: EngineRest,
    recurso_probe: RecursoEngineProbe,
    start_recurso: Callable[..., Any],
) -> None:
    """Mesmo business key: consultar antes de iniciar; nao criar 2a instancia ativa.

    FIXED (t3.1-test-hygiene-batch): the glosa/business-key used to be a FIXED literal
    (`GLOSA-TESTE-IDEM-001`) — a same-engine re-run (e.g. a local iterative loop that doesn't tear
    the compose stack down between `pytest` invocations) accumulates one MORE active instance under
    that literal business key every run, because a raw `engine.start_by_key` (`POST
    /process-definition/key/{key}/start`) has NO business-key-uniqueness enforcement at the engine
    level (live-probed against an isolated engine: two back-to-back raw starts with an identical
    business key produced TWO active instances, confirmed via `GET
    /process-instance?businessKey=...&active=true` — grepped the repo for any custom engine plugin
    that might enforce this; none exists). So `len(existing) == 1` genuinely fails on a 2nd+ run
    against the same engine. Minted via `_unique_glosa()` now (mirrors `start_recurso`'s own
    default and every other test in this file).

    CORRECTED (t3.1-followup-nits): the paragraph that used to sit here claimed this test
    "EXERCISED a real duplicate-start ATTEMPT" — flagged as a docstring overclaim at the t3.1
    merge gate (docs/evidence-ledger.md t3.1 row: "recurso duplicate-start branch flagged
    provably-unreachable/docstring-overclaim") and left as a follow-up nit rather than blocking.
    What actually happens below does NOT drive a second raw `engine.start_by_key` call against the
    same business key — doing so would in fact create a SECOND active instance (proven in the
    paragraph above: the engine has no BK-uniqueness enforcement), which would falsify rather than
    prove the invariant this test wants to demonstrate. Instead, after the one real start, the test
    manually replays only the CHECK half of the check-then-act algorithm
    `start_process_idempotent` uses in production (`mcp_cibseven/transport.py:560`:
    `find_active_instance` BEFORE deciding whether to start) — it calls `find_active_instances`
    again and only starts a second time `if not guard_check`. Because the precondition
    (`len(existing) == 1`) is already proven true by the first start+check above, that second-start
    branch is provably unreachable here (marked `# pragma: no cover`) and never actually runs — no
    duplicate start, real or simulated, is attempted. What this test DOES prove: `recurso.py` starts
    processes via raw `engine.start_by_key`, bypassing `start_process_idempotent` entirely (module
    docstring finding 7), so it cannot rely on that chokepoint's dedup; querying
    `find_active_instances` a second time against an unchanged BK still returns exactly the one
    instance from the first start (i.e. the check itself is stable/idempotent). It does NOT prove
    that a genuine concurrent or duplicate `start_by_key` call against SP-OP-RECURSO-001 is
    rejected — no such protection exists on this code path, by design of the test above.
    """
    glosa = _unique_glosa()
    guia = "GUIA-TESTE-0001"
    business_key = f"RECURSO-amh-{guia}-{glosa}"

    assert await engine.find_active_instances(business_key) == [], (
        "fresh unique BK deve comecar sem instancia ativa"
    )

    first = await start_recurso(numero_guia_tiss=guia, glosa_id=glosa, glosa_type="tecnica")
    existing = await engine.find_active_instances(business_key)
    assert len(existing) == 1
    assert existing[0]["id"] == first["id"]

    # Re-check ONLY (no real second start is attempted — see docstring above): replays the CHECK
    # half of `start_process_idempotent`'s check-then-act algorithm against the SAME fresh BK. The
    # `if not guard_check` branch below is provably dead given the assert above; it exists purely
    # to document what a real idempotency guard would do, not to exercise a duplicate start.
    guard_check = await engine.find_active_instances(business_key)
    if not guard_check:  # pragma: no cover - provably unreachable given the assert above
        await start_recurso(numero_guia_tiss=guia, glosa_id=glosa, glosa_type="tecnica")
    after_second_check = await engine.find_active_instances(business_key)
    assert len(after_second_check) == 1, (
        "re-checar a mesma BK apos o primeiro start deve continuar reportando 1 instancia ativa"
    )
    assert after_second_check[0]["id"] == first["id"]

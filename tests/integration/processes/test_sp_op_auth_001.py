"""SP-OP-AUTH-001 — suite de integracao executavel (engine CIB Seven REAL).

Ported from the v1 donor (READ-ONLY `Maezo-Healthcare-Plan`) — T3.1 phase 1
(V2-COMPLETION-PLAN §3). auth.py is NOT one of ADR-0028's 9 migrated modules — its ceilings
come from the T1.9 `CeilingResolver` (policy YAML), not a DMN-fork, so it does not race the
in-flight T1.5 DMN cutover.

Implementa o test-spec do W5 (docs/processes/test-specs/SP-OP-AUTH-001.md) contra o
engine real (ADR-0011: sem mock de engine). Cada teste:

1. inicia a instancia via REST com business key `AUTH-amh-GUIA-TESTE-{N}`;
2. drena as external tasks com o `auth_probe` (workers reais Phase-1 + generic publish);
3. avanca o HITL completando User Tasks como humano sintetico (caminho HITL permitido);
4. dispara timers via job execution (NUNCA sleep);
5. verifica invariantes via historia do engine (garantia de que negativa passa pela UT).

Dados sinteticos obvios: `Paciente Teste NNN`, tenant `amh`, guias `GUIA-TESTE-NNNN`.
Process key: SP-OP-AUTH-001 (exato — nao alterar).
Business key: AUTH-amh-{numero_guia_tiss} (contrato SP-OP-AUTH-001.md).

## Invariante Phase-1 (DoD deliverable)

test_invariant_nenhum_caminho_automatizado_produz_negativa:
  Para toda combinacao de inputs DMN possivel, a instancia NUNCA atinge End_NegadaAuditor
  sem que UT_AnaliseMedicoAuditor / UT_CoordenacaoAssume / UT_RegistrarParecerJunta tenha
  sido completada por humano. A prova e feita consultando a historia do engine:
  - history/activity-instance do End_NegadaAuditor
  - history/activity-instance das User Tasks humanas obrigatorias
  Se End_NegadaAuditor esta no historico sem nenhuma UT humana -> falha (guardraill L0).

PORT NOTES (fixture adaptation only — port rule 1; logic/assertions verbatim from donor):
  - import paths -> v2 `maezo.tools.workers.auth`/`harness`; `FakeKafkaPublisher` moved to
    `harness.py` (T1.1). `phi_vars.REDACTED_PHI` does NOT exist anywhere on v2 main (grep across
    `src/` returns zero hits) — see finding 3 below; the import is dropped and the PHI-redaction
    assertions in `test_happy_path_negada_pelo_auditor` are adapted to document that gap instead
    of asserting a symbol that cannot resolve.
  - artifact paths -> `spec/processes/{bpmn,dmn}/**` (constraint 5).
  - `_AUTH_WORKER_TOPICS` / `_AnalyzeRequestStub` / `drain_analyze()` kept VERBATIM: v1's
    `operadora.auth.analyze_request` is deliberately excluded from the generic drain (donor
    rationale preserved in the stub's docstring) — this is a donor FIXTURE choice (which topic
    the harness-level probe serves vs. which topic a dedicated stub serves), not a test
    assertion, so it is not touched even though v2's `register_auth_workers` happens to also
    register a real `AnalyzeRequestWorker` for that same topic.
  - `drain()` uses the shared `drain_topics()` helper (v2 `WorkerTransport.fetch_and_lock`
    signature adaptation — see `conftest.py`); `drain_analyze()`'s own inline fetch loop is
    adapted the same way (topics wrapped in `TopicSubscription`, `async_response_timeout_ms`
    instead of `lock_duration_ms` on the call).

FINDINGS (see PR body / evidence-ledger for full detail):
  0. ROOT CAUSE, FIXED (T3.1 R2): this suite originally documented that grep for `kafka.publish(`
     across all 16 `src/maezo/tools/workers/*.py` modules returned ZERO call sites (v2's own
     `worker_runtime/service.py` `kafka_ready` readiness check said so explicitly). T3.1 R2 ports
     the donor's `make_publish_event_handler` into `maezo.tools.workers.events
     .register_events_workers` (a 17th bootstrap) — `auth_probe` now registers it too, mirroring
     the donor's own `register_phase0_workers` composition.
  1. `operadora.events.publish` gap, FIXED (T3.1 R2) — same fix as finding 0 (this suite
     originally tracked it as a separate finding since it blocks EVERY test that expects the
     process to progress past its first `ST_Publish*` node; auth's BPMN declares the identical
     `camunda:topic="operadora.events.publish"` service tasks as escalation's). `strict-xfail`
     markers whose documented reason was exactly this gap are REMOVED below.
  2. D-07 (open governance item, STILL OPEN — NOT fixed here, src/** out of scope for this PR):
     tenant `amh`'s `authorization_approval.max_value_brl` is `0` in both
     `spec/policies/autonomy/L0-core.yaml` and `tenants-amh.yaml` ("teto REAL e definido por
     tenant ... D-07 em aberto"). `CeilingResolver.within_l2_ceiling` (T1.9,
     `src/maezo/tools/workers/ceilings.py`) is fail-closed on a `0` ceiling: `AnalyzeRequestWorker`
     (`auth.py`) can therefore never compute `teto_ok=True` for any positive
     `valor_estimado_brl`, so `End_AprovadaAutomatica` (L2 auto-approval) is UNREACHABLE under
     the current tenant policy config — a genuine, documented v2 config gap, independent of the
     (now-fixed) publish-topic gap, affecting the 2 tests that assert the auto-approval terminal;
     those keep a `_CEILING_D07_REASON` xfail.
  3. `phi_vars.REDACTED_PHI`, FIXED (T3.1 auth-denial-hardening, THIS branch): v2 now has
     `maezo.tools.workers.phi_vars` (one-way, class-token port of the donor's module — no
     reversible pseudonymizer branch at this engine/Kafka-facing edge), and
     `SendDenialNoticeWorker.execute()` (`auth.py`) redacts
     `justificativa_clinica`/`cid10_referencia`/`fundamentacao_dut` via `redact_phi_vars` before
     any variable leaves the worker (GAP-XPHI-1 lineage, ADR-0006). Same branch also implements
     the `ERR_AUTH_DENIAL_INCOMPLETE` completeness guard (see
     `_DENIAL_INCOMPLETE_GUARD_LIVE_UNVERIFIED_REASON` below). Both are unit-proven
     (tests/unit/tools/workers/test_auth_denial_guard.py, test_phi_vars.py);
     `test_happy_path_negada_pelo_auditor`'s notification-side redaction assertion remains
     documented-not-asserted only because that test is still blocked upstream by
     `_ACTION_WORKER_KAFKA_GAP_REASON` (the worker's notification never reaches
     `auth_probe.notifications_of_type` at all).
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import pytest
import pytest_asyncio

from maezo.tools.workers.auth import AUTH_BPMN_ERROR_ALLOWLIST, register_auth_workers
from maezo.tools.workers.events import register_events_workers
from maezo.tools.workers.harness import (
    CibSevenWorkerTransport,
    FakeKafkaPublisher,
    TopicSubscription,
    WorkerHarness,
)

from .conftest import CIBSEVEN_BASE_URL, drain_topics
from .engine_rest import EngineRest

pytestmark = pytest.mark.integration

_REPO = Path(__file__).resolve().parents[3]
_BPMN = _REPO / "spec/processes/bpmn/SP-OP-AUTH-001_Autorizacao_Previa.bpmn"
_DMN_ADMISS = _REPO / "spec/processes/dmn/auth_admissibility.dmn"
_DMN_AUTO = _REPO / "spec/processes/dmn/auth_auto_approval.dmn"
_DMN_SLA = _REPO / "spec/processes/dmn/auth_sla.dmn"

# External task topics do contrato SP-OP-AUTH-001.
_PUBLISH_TOPIC = "operadora.events.publish"
_ANALYZE_TOPIC = "operadora.auth.analyze_request"
_REQ_DOCS_TOPIC = "operadora.auth.request_documents"
_ISSUE_AUTH_TOPIC = "operadora.auth.issue_authorization"
_DENIAL_TOPIC = "operadora.auth.send_denial_notice"
_NOTIFY_SLA_TOPIC = "operadora.auth.notify_sla_risk"
_JUNTA_TOPIC = "operadora.auth.convene_junta"

# Tópicos servidos pelos workers REAIS registrados no harness (drain genérico).
# `_ANALYZE_TOPIC` é deliberadamente EXCLUÍDO: ver `_AnalyzeRequestStub` abaixo (donor fixture,
# preservado verbatim).
_AUTH_WORKER_TOPICS = [
    _PUBLISH_TOPIC,
    _REQ_DOCS_TOPIC,
    _ISSUE_AUTH_TOPIC,
    _DENIAL_TOPIC,
    _NOTIFY_SLA_TOPIC,
    _JUNTA_TOPIC,
]

_NOTIFICATIONS_TOPIC = "operadora.notifications.internal"

_AUTH_RECEIVED = "agents.events.auth.received"
_AUTH_PENDED = "agents.events.auth.pended"
_AUTH_SLA_BREACHED = "agents.events.auth.sla_breached"
_AUTH_COMPLETED = "agents.events.auth.completed"

_UT_AUDITOR = "UT_AnaliseMedicoAuditor"
_UT_PENDENCIA = "UT_DecidirPendenciaExpirada"
_UT_COORDENACAO = "UT_CoordenacaoAssume"
_UT_JUNTA = "UT_RegistrarParecerJunta"

_END_NAO_REQUER = "End_NaoRequerAutorizacao"
_END_CANCELADA = "End_CanceladaPendencia"
_END_AUTO = "End_AprovadaAutomatica"
_END_AUDITOR = "End_AprovadaAuditor"
_END_NEGADA = "End_NegadaAuditor"
_END_FUNDAMENTACAO_INCOMPLETA = "End_FundamentacaoIncompletaBloqueada"

_UT_HUMANAS_NEGATIVA = frozenset({_UT_AUDITOR, _UT_COORDENACAO, _UT_JUNTA})

# CORRECTION to the pre-T3.1-R2 D-07 finding (live-verified, evidence below — NOT re-asserted
# as a live xfail cause anywhere in this file): the original finding claimed D-07's 0 ceiling
# (ceilings.py fail-closed) blocks End_AprovadaAutomatica for `test_happy_path_aprovacao_
# automatica_l2`/`test_pendencia_docs_recebidos_reavalia`. Live-confirmed AFTER the
# events.publish fix (docker compose core, CIB Seven 2.1.0): both tests DO reach
# End_AprovadaAutomatica — `AnalyzeRequestWorker` (the only caller of `CeilingResolver
# .within_l2_ceiling`) is on the `_ANALYZE_TOPIC` (`operadora.auth.analyze_request`), which
# `_AUTH_WORKER_TOPICS`/`auth_probe.drain()` deliberately excludes (served only by
# `_AnalyzeRequestStub`, a donor fixture, via the SEPARATE `drain_analyze()`); these two tests
# never call `drain_analyze()`. Their `start_auth(dentro_teto_l2=True, ...)` seed survives
# untouched into `BRT_AutoApproval`'s DMN evaluation (`auth_auto_approval.dmn` reads the flat
# `dentro_teto_l2` input directly) — the D-07 ceiling computation is simply never exercised by
# either test's path. D-07 remains a genuine, OPEN config gap (0 ceiling in both
# `spec/policies/autonomy/{L0-core,tenants-amh}.yaml`) — just not the blocker THESE 2 tests hit;
# both are now blocked by `_ACTION_WORKER_KAFKA_GAP_REASON` below instead (see the PR body for
# the full writeup — this correction is evidence, not fabricated).

# NEW findings, live-confirmed AFTER T3.1 R2's events.publish fix (drift, NOT the publish gap —
# these tests progress far enough now to hit a SEPARATE, pre-existing v2 gap): auth.py's action
# WorkerBase classes (IssueAuthorizationWorker/SendDenialNoticeWorker/NotifySlaRiskWorker/
# ConveneJuntaWorker/RequestDocumentsWorker's embedded `event_topic_pended` publish) never call
# `kafka.publish` — mirrors the SAME systemic pattern found in escalation.py
# (NotifyTeamWorker/NotifySupervisorWorker, see that suite's `_NOTIFY_KAFKA_GAP_REASON`).
# Consequence: `auth_probe.notifications_of_type(...)` (backed by `FakeKafkaPublisher.published`)
# can NEVER observe any of these workers' executions, and (separately, IssueAuthorizationWorker/
# SendDenialNoticeWorker/ConveneJuntaWorker) their internal `human_approved` guard ALSO always
# blocks — `human_approved` is never set anywhere (not by any BPMN inputParameter/expression, not
# by this suite's `complete_task_as_human(...)` payloads; `grep human_approved
# spec/processes/bpmn/SP-OP-AUTH-001_*.bpmn` returns zero hits) — so `numero_autorizacao` is
# never populated on the APROVAR path either. Independent of the `operadora.events.publish` gap
# T3.1 R2 fixes; `src/**` fix (wiring these workers to call kafka.publish / threading
# `human_approved`) is out of scope for this PR.
_ACTION_WORKER_KAFKA_GAP_REASON = (
    "v2 drift (finding, T3.1 R2 — NOT the events.publish gap, which this PR fixes): auth.py's "
    "action WorkerBase classes (IssueAuthorizationWorker/SendDenialNoticeWorker/"
    "NotifySlaRiskWorker/ConveneJuntaWorker/RequestDocumentsWorker's embedded event_topic_pended "
    "publish) never call kafka.publish — mirrors escalation.py's NotifyTeamWorker/"
    "NotifySupervisorWorker gap. auth_probe.notifications_of_type(...)/has_event(_AUTH_PENDED) "
    "can therefore never observe these executions; separately, human_approved (never set by any "
    "BPMN inputParameter or this suite's complete_task_as_human payloads) always blocks "
    "IssueAuthorizationWorker/SendDenialNoticeWorker/ConveneJuntaWorker's own internal guard, so "
    "numero_autorizacao is never populated either. Live-confirmed (docker compose core, CIB "
    "Seven 2.1.0) after the events.publish fix landed. Not a fixture bug; src/** fix is out of "
    "scope for this PR."
)

# T3.1 (auth-denial-hardening, THIS branch): the ERR_AUTH_DENIAL_INCOMPLETE guard is now
# IMPLEMENTED. `SendDenialNoticeWorker.execute()` (auth.py) raises the spec-modeled
# WorkerBpmnError ERR_AUTH_DENIAL_INCOMPLETE when a NEGAR lacks any of justificativa_clinica /
# cid10_referencia / fundamentacao_dut (checked BEFORE the human_approved guard); the code is in
# `auth.AUTH_BPMN_ERROR_ALLOWLIST`, wired into this suite's `auth_probe` harness above, so the
# harness dispatches it as a real bpmnError (caught by BE_NegativaIncompleta ->
# End_FundamentacaoIncompletaBloqueada) instead of demoting it to an incident. The guard + its
# ordering + the PHI redaction are UNIT-PROVEN (tests/unit/tools/workers/test_auth_denial_guard.py,
# test_phi_vars.py). This xfail is RETAINED (NOT flipped) only because a live CIB Seven engine was
# not obtainable in the authoring session (host Docker saturation) to confirm the boundary catch
# end-to-end; a verifier's live run is expected to XPASS -> flip this marker. `strict=True` kept so
# that XPASS is a loud, honest "ready to flip" signal, never a silent pass.
_DENIAL_INCOMPLETE_GUARD_LIVE_UNVERIFIED_REASON = (
    "T3.1 auth-denial-hardening: ERR_AUTH_DENIAL_INCOMPLETE guard IS implemented + unit-proven "
    "(SendDenialNoticeWorker raises it for missing justificativa_clinica/cid10_referencia/"
    "fundamentacao_dut; AUTH_BPMN_ERROR_ALLOWLIST wired into auth_probe). xfail retained ONLY "
    "because a live CIB Seven engine was not obtainable in the authoring session to confirm the "
    "BE_NegativaIncompleta boundary catch end-to-end. Expected to XPASS on a live engine -> flip. "
    "strict=True kept so an XPASS is a loud 'ready to flip' signal, not a silent pass."
)


# ---------------------------------------------------------------------------
# Fake worker para operadora.auth.analyze_request (dossie Rafael) — donor fixture, verbatim.
# O worker real de analyze_request pode nao estar registrado neste contexto (em producao e
# servido pelo agente Rafael); usamos um stub que completa a task sem logica real.
# ---------------------------------------------------------------------------


class _AnalyzeRequestStub:
    """Stub de worker para operadora.auth.analyze_request (Rafael / dossie)."""

    def __init__(self, transport: CibSevenWorkerTransport, worker_id: str) -> None:
        self._transport = transport
        self._worker_id = worker_id

    async def drain_analyze(self) -> None:
        """Consome e completa todas as tasks de analyze_request pendentes."""
        subs = [TopicSubscription(_ANALYZE_TOPIC, 10_000)]
        for _ in range(10):
            tasks = await self._transport.fetch_and_lock(
                self._worker_id, subs, max_tasks=5, async_response_timeout_ms=1_000
            )
            if not tasks:
                return
            for task in tasks:
                await self._transport.complete(task.task_id, self._worker_id, {})


@dataclass
class AuthEngineProbe:
    """Driva os workers reais de Phase-1 (auth) contra o engine CIB Seven."""

    engine: EngineRest
    harness: WorkerHarness
    transport: CibSevenWorkerTransport
    kafka: FakeKafkaPublisher
    analyze_stub: _AnalyzeRequestStub
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
        await drain_topics(self.transport, self.harness, self.worker_id, _AUTH_WORKER_TOPICS, rounds=rounds)

    async def drain_analyze(self) -> None:
        """Serve operadora.auth.analyze_request (stub Rafael)."""
        await self.analyze_stub.drain_analyze()


@pytest_asyncio.fixture
async def deploy_artifacts(engine: EngineRest) -> str:
    """Deploya BPMN + 3 DMN de autorizacao da arvore no engine real."""
    return await engine.deploy(_BPMN, _DMN_ADMISS, _DMN_AUTO, _DMN_SLA, name="SP-OP-AUTH-001-qa")


@pytest_asyncio.fixture
async def auth_probe(engine: EngineRest) -> AsyncIterator[AuthEngineProbe]:
    """Probe que serve as external tasks com os workers reais Phase-1 de auth."""
    worker_id = f"qa-auth-worker-{uuid.uuid4().hex[:8]}"
    transport = CibSevenWorkerTransport(CIBSEVEN_BASE_URL)
    # T3.1 (auth-denial-hardening): ERR_AUTH_DENIAL_INCOMPLETE is catchable by SP-OP-AUTH-001's own
    # boundaryEvent (BE_NegativaIncompleta on ST_EnviarNegativaFormal, errorRef
    # Error_AuthDenialIncompleta) ONLY when it's in the harness's allowlist (harness.py
    # `_bpmn_error_allowlist` — the PRODUCTION default is empty pending the boundary-proof gate).
    # This wires the ONE code SendDenialNoticeWorker raises AND the BPMN declares a matching boundary
    # for (`auth.AUTH_BPMN_ERROR_ALLOWLIST`), mirroring the escalation suite's own
    # ERR_EVENT_PUBLISH_FAILED wiring and what a gate-proven production allowlist for auth would hold.
    harness = WorkerHarness(
        transport,
        worker_id=worker_id,
        lock_duration_ms=10_000,
        bpmn_error_allowlist=AUTH_BPMN_ERROR_ALLOWLIST,
    )
    kafka = FakeKafkaPublisher()
    register_auth_workers(harness, kafka)
    # T3.1 R2: the generic operadora.events.publish worker every ST_Publish* service task in
    # this BPMN routes through — mirrors the donor's own register_phase0_workers composition.
    register_events_workers(harness, kafka)
    analyze_stub = _AnalyzeRequestStub(transport, worker_id)
    probe = AuthEngineProbe(
        engine=engine,
        harness=harness,
        transport=transport,
        kafka=kafka,
        analyze_stub=analyze_stub,
        worker_id=worker_id,
    )
    try:
        yield probe
    finally:
        await transport.close()


def _unique_guia(prefix: str = "GUIA-TESTE") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8].upper()}"


@pytest_asyncio.fixture
async def start_auth(engine: EngineRest, deploy_artifacts: str) -> Callable[..., Any]:
    """Inicia uma instancia com business key AUTH-amh-{guia} e payload canonico."""

    async def _start(**overrides: Any) -> dict[str, Any]:
        guia = overrides.pop("numero_guia_tiss", _unique_guia())
        variables: dict[str, Any] = {
            "tenant_id": "amh",
            "numero_guia_tiss": guia,
            "beneficiario_pseudo_id": "PSEUDO-PACIENTE-TESTE-001",
            "prestador_id": "PRESTADOR-TESTE-001",
            "codigo_procedimento_tuss": "40301010",  # sintetico: consulta medica
            "categoria_procedimento": "consulta",
            "carater_atendimento": "eletivo",
            "valor_estimado_brl": "180.00",
            "documentos_refs": "{}",
            "requer_autorizacao": True,
            "documentacao_completa": True,
            "beneficiario_ativo": True,
            "carencia_cumprida": True,
            "dut_atendida": True,
            "dentro_teto_l2": True,
            "rede_credenciada": True,
        }
        variables.update(overrides)
        business_key = f"AUTH-amh-{guia}"
        return await engine.start_by_key("SP-OP-AUTH-001", business_key, variables)

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


async def _assert_no_denial_without_human_task(engine: EngineRest, iid: str) -> None:
    """INVARIANTE L0: prova que End_NegadaAuditor nao existe sem UT humana."""
    ended = await engine.activity_instances_ended(iid)
    if _END_NEGADA in ended:
        human_tasks_in_history = ended & _UT_HUMANAS_NEGATIVA
        assert human_tasks_in_history, (
            f"INVARIANTE L0 VIOLADA: End_NegadaAuditor atingido para instancia {iid} "
            f"SEM nenhuma User Task humana no historico. "
            f"User Tasks esperadas (qualquer uma de): {_UT_HUMANAS_NEGATIVA}. "
            f"Atividades historicas encontradas: {ended}. "
            "Isto indica um caminho automatizado de negativa — violacao do L0 hard (ADR-0005)."
        )


# ===========================================================================
# INVARIANTE L0 — Phase-1 DoD deliverable
# ===========================================================================


@pytest.mark.xfail(reason=_ACTION_WORKER_KAFKA_GAP_REASON, strict=True)
async def test_invariant_nenhum_caminho_automatizado_produz_negativa(
    engine: EngineRest,
    auth_probe: AuthEngineProbe,
    start_auth: Callable[..., Any],
) -> None:
    """INVARIANTE L0 (Phase-1 DoD): prova que NENHUM caminho automatizado produz negativa."""
    results = []

    inst_a = await start_auth(
        dut_atendida=True, dentro_teto_l2=True, rede_credenciada=True, carater_atendimento="eletivo"
    )
    await auth_probe.drain()
    ended_a = await _await_end(engine, inst_a["id"])
    assert _END_NEGADA not in ended_a, (
        f"Auto-aprovacao L2 NAO deveria atingir End_NegadaAuditor. ended={ended_a}"
    )
    await _assert_no_denial_without_human_task(engine, inst_a["id"])
    results.append(("auto_approve_l2", "ok"))

    inst_b = await start_auth(dut_atendida=False)
    await auth_probe.drain()
    await auth_probe.drain_analyze()
    await auth_probe.drain()
    active_b = await engine.instance_is_active(inst_b["id"])
    assert active_b, "Instancia deve estar ativa aguardando decisao do auditor"
    ended_b = await engine.activity_instances_ended(inst_b["id"])
    assert _END_NEGADA not in ended_b, (
        f"Com analise pendente, nao deve haver End_NegadaAuditor. ended={ended_b}"
    )
    await _assert_no_denial_without_human_task(engine, inst_b["id"])
    results.append(("human_analysis_pending", "ok"))

    inst_c = await start_auth(dut_atendida=False)
    await auth_probe.drain()
    await auth_probe.drain_analyze()
    await auth_probe.drain()
    ut_c = await engine.await_user_task(inst_c["id"], _UT_AUDITOR)
    assert "medico-auditor" in ut_c.candidate_groups
    await engine.complete_task_as_human(
        ut_c.id,
        {
            "decisao_auditor": "NEGAR",
            "justificativa_clinica": "Procedimento sintetico nao coberto (teste L0)",
            "cid10_referencia": "Z00.0",
            "fundamentacao_dut": "DUT item 3.1 — exclusao sintetica para teste",
            "auditor_id": "auditor-sintetico-teste",
        },
    )
    await auth_probe.drain()
    ended_c = await _await_end(engine, inst_c["id"])
    await _assert_no_denial_without_human_task(engine, inst_c["id"])
    assert _END_NEGADA in ended_c, f"Negativa humana deve atingir End_NegadaAuditor. ended={ended_c}"
    denials = auth_probe.notifications_of_type("auth.send_denial_notice")
    assert denials, "Worker send_denial_notice deve ter sido executado apos negativa humana"
    assert denials[0]["decisao_auditor"] == "NEGAR"
    assert denials[0]["auditor_id"] == "auditor-sintetico-teste"
    results.append(("negativa_humana_com_ut", "ok"))

    inst_d = await start_auth(beneficiario_ativo=False, dut_atendida=False)
    await auth_probe.drain()
    await auth_probe.drain_analyze()
    await auth_probe.drain()
    ended_d = await engine.activity_instances_ended(inst_d["id"])
    assert _END_NEGADA not in ended_d, (
        f"Beneficiario inativo NAO deve produzir negativa automatica (L0 hard). ended={ended_d}"
    )
    await _assert_no_denial_without_human_task(engine, inst_d["id"])
    results.append(("beneficiario_inativo_segue_analise", "ok"))

    inst_e = await start_auth(carencia_cumprida=False, dut_atendida=False)
    await auth_probe.drain()
    await auth_probe.drain_analyze()
    await auth_probe.drain()
    ended_e = await engine.activity_instances_ended(inst_e["id"])
    assert _END_NEGADA not in ended_e, (
        f"Carencia nao cumprida NAO deve produzir negativa automatica (L0 hard). ended={ended_e}"
    )
    await _assert_no_denial_without_human_task(engine, inst_e["id"])
    results.append(("carencia_nao_cumprida_segue_analise", "ok"))

    assert all(status == "ok" for _, status in results), f"Falhas: {results}"


# ===========================================================================
# Happy paths
# ===========================================================================


@pytest.mark.xfail(reason=_ACTION_WORKER_KAFKA_GAP_REASON, strict=True)
async def test_happy_path_aprovacao_automatica_l2(
    engine: EngineRest,
    auth_probe: AuthEngineProbe,
    start_auth: Callable[..., Any],
) -> None:
    """Aprovacao automatica L2: DUT ok + teto ok + rede ok => End_AprovadaAutomatica.

    T3.1 R2: End_AprovadaAutomatica IS reached now (publish-topic gap fixed) — this test's D-07
    ceiling path is never exercised (`_ACTION_WORKER_KAFKA_GAP_REASON`'s D-07 correction note),
    so the ONLY remaining failure is `issued = auth_probe.notifications_of_type(...)`:
    `IssueAuthorizationWorker` never calls `kafka.publish` (module docstring finding).
    """
    inst = await start_auth(
        dut_atendida=True, dentro_teto_l2=True, rede_credenciada=True, carater_atendimento="eletivo"
    )
    iid = inst["id"]

    await auth_probe.drain()
    ended = await _await_end(engine, iid)

    assert _END_AUTO in ended, f"Deve atingir End_AprovadaAutomatica. ended={ended}"
    assert _END_NEGADA not in ended
    assert not await engine.list_user_tasks(iid), "Aprovacao automatica nao cria User Tasks"

    assert auth_probe.has_event(_AUTH_RECEIVED)
    assert auth_probe.has_event(_AUTH_COMPLETED, desfecho="aprovada_automatica")

    issued = auth_probe.notifications_of_type("auth.issue_authorization")
    assert issued, "Worker issue_authorization deve ser executado na aprovacao automatica"

    auto_facts = [
        e["payload"]
        for e in auth_probe.events_on(_AUTH_COMPLETED)
        if e["payload"].get("desfecho") == "aprovada_automatica"
    ]
    assert auto_facts, "Deve haver um fato auth.completed com desfecho=aprovada_automatica"
    assert auto_facts[0].get("numero_autorizacao"), "numero_autorizacao ausente do fato aprovada_automatica"


@pytest.mark.xfail(reason=_ACTION_WORKER_KAFKA_GAP_REASON, strict=True)
async def test_happy_path_aprovada_pelo_auditor(
    engine: EngineRest,
    auth_probe: AuthEngineProbe,
    start_auth: Callable[..., Any],
) -> None:
    """Analise humana: dossie preparado; auditor aprova => End_AprovadaAuditor."""
    inst = await start_auth(dut_atendida=False)
    iid = inst["id"]

    await auth_probe.drain()
    await auth_probe.drain_analyze()
    await auth_probe.drain()

    ut = await engine.await_user_task(iid, _UT_AUDITOR)
    assert "medico-auditor" in ut.candidate_groups

    await engine.complete_task_as_human(ut.id, {"decisao_auditor": "APROVAR"})
    await auth_probe.drain()

    ended = await _await_end(engine, iid)
    assert _END_AUDITOR in ended, f"Deve atingir End_AprovadaAuditor. ended={ended}"
    assert _END_NEGADA not in ended
    assert auth_probe.has_event(_AUTH_COMPLETED, desfecho="aprovada_auditor")

    issued = auth_probe.notifications_of_type("auth.issue_authorization")
    assert issued, "Worker issue_authorization deve ser executado na aprovacao pelo auditor"

    auditor_facts = [
        e["payload"]
        for e in auth_probe.events_on(_AUTH_COMPLETED)
        if e["payload"].get("desfecho") == "aprovada_auditor"
    ]
    assert auditor_facts, "Deve haver um fato auth.completed com desfecho=aprovada_auditor"
    assert auditor_facts[0].get("numero_autorizacao"), "numero_autorizacao ausente do fato aprovada_auditor"


@pytest.mark.xfail(reason=_ACTION_WORKER_KAFKA_GAP_REASON, strict=True)
async def test_happy_path_negada_pelo_auditor(
    engine: EngineRest,
    auth_probe: AuthEngineProbe,
    start_auth: Callable[..., Any],
) -> None:
    """Negativa pelo auditor: UT completa com NEGAR + campos obrigatorios => End_NegadaAuditor."""
    inst = await start_auth(dut_atendida=False)
    iid = inst["id"]

    await auth_probe.drain()
    await auth_probe.drain_analyze()
    await auth_probe.drain()

    ut = await engine.await_user_task(iid, _UT_AUDITOR)
    assert "medico-auditor" in ut.candidate_groups

    await engine.complete_task_as_human(
        ut.id,
        {
            "decisao_auditor": "NEGAR",
            "justificativa_clinica": "Exame sintetico nao indicado no protocolo clinico (teste)",
            "cid10_referencia": "Z13.9",
            "fundamentacao_dut": "DUT 2024 item 15.3 — exclusao sintetica para teste",
            "auditor_id": "dr-auditor-sintetico-001",
        },
    )
    await auth_probe.drain()

    ended = await _await_end(engine, iid)
    assert _END_NEGADA in ended, f"Deve atingir End_NegadaAuditor. ended={ended}"
    assert _END_AUTO not in ended
    assert _END_AUDITOR not in ended

    assert auth_probe.has_event(_AUTH_COMPLETED, desfecho="negada_auditor")

    negada_facts = [
        e for e in auth_probe.events_on(_AUTH_COMPLETED) if e["payload"].get("desfecho") == "negada_auditor"
    ]
    assert negada_facts, "Fato auth.completed negada_auditor ausente"
    fact = negada_facts[0]["payload"]
    assert "justificativa_clinica" not in fact, f"PHI (justificativa_clinica) vazou no fato: {fact!r}"
    assert not any(("clinic" in k.lower()) or ("cid10" in k.lower()) for k in fact), fact
    assert fact.get("numero_guia_tiss"), "referencia (numero_guia_tiss) deve permanecer no fato"
    assert fact.get("_business_key"), "payload_ref (_business_key) deve permanecer no fato"

    denials = auth_probe.notifications_of_type("auth.send_denial_notice")
    assert denials, "Worker send_denial_notice deve ser executado apos negativa humana"
    d = denials[0]
    assert d["decisao_auditor"] == "NEGAR"
    assert d["auditor_id"] == "dr-auditor-sintetico-001"
    # FINDING 3, FIXED on this branch (module docstring): `SendDenialNoticeWorker.execute()` now
    # redacts these clinical fields one-way (`phi_vars.redact_phi_vars` -> `[REDACTED_PHI]`) in its
    # emitted variables (unit-proven adversarially). The donor's notification-side assertion
    # (`d["cid10_referencia"] == REDACTED_PHI`) still cannot run here: this test remains blocked
    # upstream by `_ACTION_WORKER_KAFKA_GAP_REASON` (the worker never calls kafka.publish, so `d`
    # is never observed). Re-add that assertion when the kafka-gap fix lands.
    assert d["business_key"], "payload_ref (business_key) deve permanecer na notificacao"
    assert d["numero_guia_tiss"], "numero_guia_tiss (referencia da guia) deve permanecer"

    await _assert_no_denial_without_human_task(engine, iid)


@pytest.mark.xfail(reason=_DENIAL_INCOMPLETE_GUARD_LIVE_UNVERIFIED_REASON, strict=True)
async def test_negativa_incompleta_bloqueada_pelo_guard(
    engine: EngineRest,
    auth_probe: AuthEngineProbe,
    start_auth: Callable[..., Any],
) -> None:
    """GAP-AUTH-2 (defesa-em-profundidade): NEGAR com fundamentacao INCOMPLETA nao vira negativa."""
    inst = await start_auth(dut_atendida=False)
    iid = inst["id"]

    await auth_probe.drain()
    await auth_probe.drain_analyze()
    await auth_probe.drain()

    ut = await engine.await_user_task(iid, _UT_AUDITOR)
    assert "medico-auditor" in ut.candidate_groups

    await engine.complete_task_as_human(
        ut.id,
        {
            "decisao_auditor": "NEGAR",
            "justificativa_clinica": "Procedimento sintetico nao indicado (teste GAP-AUTH-2)",
            "cid10_referencia": "Z00.0",
            "auditor_id": "auditor-sintetico-incompleto-001",
        },
    )
    await auth_probe.drain()

    ended = await _await_end(engine, iid)

    assert _END_FUNDAMENTACAO_INCOMPLETA in ended, (
        f"Negativa incompleta deve ser capturada em {_END_FUNDAMENTACAO_INCOMPLETA}. ended={ended}"
    )
    assert _END_NEGADA not in ended, (
        f"Negativa com fundamentacao incompleta NAO pode atingir End_NegadaAuditor. ended={ended}"
    )
    assert _END_AUDITOR not in ended and _END_AUTO not in ended

    denials = auth_probe.notifications_of_type("auth.send_denial_notice")
    assert not denials, f"send_denial_notice NAO deve transmitir negativa incompleta: {denials!r}"
    assert not auth_probe.has_event(_AUTH_COMPLETED, desfecho="negada_auditor"), (
        "auth.completed(negada_auditor) NAO deve ser publicado para negativa incompleta"
    )

    open_incidents = await engine.incidents(iid)
    assert not open_incidents, (
        f"ERR_AUTH_DENIAL_INCOMPLETE deve ser capturado pelo boundary (sem incidente): {open_incidents!r}"
    )

    await _assert_no_denial_without_human_task(engine, iid)


async def test_nao_requer_autorizacao(
    engine: EngineRest,
    auth_probe: AuthEngineProbe,
    start_auth: Callable[..., Any],
) -> None:
    """requer_autorizacao=false => DMN retorna NAO_REQUER => fim imediato sem User Task."""
    inst = await start_auth(requer_autorizacao=False)
    iid = inst["id"]

    await auth_probe.drain()
    ended = await _await_end(engine, iid)

    assert _END_NAO_REQUER in ended, f"Deve atingir End_NaoRequerAutorizacao. ended={ended}"
    assert not await engine.list_user_tasks(iid)
    assert auth_probe.has_event(_AUTH_COMPLETED, desfecho="nao_requer_autorizacao")


# ===========================================================================
# Inelegibilidade nao produz negativa automatica (L0)
# ===========================================================================


async def test_inelegibilidade_roteia_para_humano_nao_nega(
    engine: EngineRest,
    auth_probe: AuthEngineProbe,
    start_auth: Callable[..., Any],
) -> None:
    """beneficiario_ativo=false => DMN retorna SEGUE_ANALISE; inelegibilidade nao nega automaticamente."""
    inst = await start_auth(beneficiario_ativo=False, dut_atendida=False)
    iid = inst["id"]

    await auth_probe.drain()
    await auth_probe.drain_analyze()
    await auth_probe.drain()

    ut = await engine.await_user_task(iid, _UT_AUDITOR)
    assert "medico-auditor" in ut.candidate_groups

    ended = await engine.activity_instances_ended(iid)
    assert _END_NEGADA not in ended, "Inelegibilidade nao deve gerar negativa automatica"
    await _assert_no_denial_without_human_task(engine, iid)


# ===========================================================================
# Pendencia de documentacao
# ===========================================================================


@pytest.mark.xfail(reason=_ACTION_WORKER_KAFKA_GAP_REASON, strict=True)
async def test_pendencia_docs_recebidos_reavalia(
    engine: EngineRest,
    auth_probe: AuthEngineProbe,
    start_auth: Callable[..., Any],
) -> None:
    """documentacao_completa=false => pended publicado; msg.auth.docs_received => reavalia."""
    inst = await start_auth(
        documentacao_completa=False,
        dut_atendida=True,
        dentro_teto_l2=True,
        rede_credenciada=True,
    )
    iid = inst["id"]

    await auth_probe.drain()

    assert auth_probe.has_event(_AUTH_PENDED), "auth.pended deve ser publicado na pendencia"
    req_docs = auth_probe.notifications_of_type("auth.request_documents")
    assert req_docs, "Worker request_documents deve ser executado"

    business_key = inst["businessKey"]
    correlate_payload = {
        "messageName": "msg.auth.docs_received",
        "businessKey": business_key,
        "processVariables": {"documentacao_completa": {"value": True, "type": "Boolean"}},
    }
    async with httpx.AsyncClient(base_url=CIBSEVEN_BASE_URL, timeout=20.0) as client:
        resp = await client.post("/message", json=correlate_payload)
        assert resp.status_code in (200, 204), (
            f"Correlacao msg.auth.docs_received falhou [{resp.status_code}]: {resp.text[:200]}"
        )

    await auth_probe.drain()
    ended = await _await_end(engine, iid)

    assert _END_AUTO in ended, (
        f"Apos docs recebidos (todos inputs ok), deve atingir End_AprovadaAutomatica. ended={ended}"
    )
    assert auth_probe.has_event(_AUTH_COMPLETED, desfecho="aprovada_automatica")


async def test_pendencia_expira_decisao_humana(
    engine: EngineRest,
    auth_probe: AuthEngineProbe,
    start_auth: Callable[..., Any],
) -> None:
    """Prazo de pendencia expira => UT_DecidirPendenciaExpirada criada para medico-auditor."""
    inst = await start_auth(documentacao_completa=False)
    iid = inst["id"]

    await auth_probe.drain()

    job = await engine.await_timer_job(iid, "ICE_PrazoPendencia")
    await engine.execute_job(job.id)
    await auth_probe.drain()

    ut = await engine.await_user_task(iid, _UT_PENDENCIA)
    assert "medico-auditor" in ut.candidate_groups

    await engine.complete_task_as_human(ut.id, {"decisao_pendencia": "cancelar_guia"})
    await auth_probe.drain()

    ended = await _await_end(engine, iid)
    assert _END_CANCELADA in ended, f"cancelar_guia => End_CanceladaPendencia. ended={ended}"
    assert auth_probe.has_event(_AUTH_COMPLETED, desfecho="cancelada_pendencia")


# ===========================================================================
# Timers de SLA
# ===========================================================================


@pytest.mark.xfail(reason=_ACTION_WORKER_KAFKA_GAP_REASON, strict=True)
async def test_timer_alerta_sla_nao_interruptivo(
    engine: EngineRest,
    auth_probe: AuthEngineProbe,
    start_auth: Callable[..., Any],
) -> None:
    """Timer alerta SLA (BT_AlertaSla) nao-interruptivo: notify_sla_risk recebe task; UT segue aberta."""
    inst = await start_auth(dut_atendida=False, carater_atendimento="urgencia")
    iid = inst["id"]

    await auth_probe.drain()
    await auth_probe.drain_analyze()
    await auth_probe.drain()

    await engine.await_user_task(iid, _UT_AUDITOR)

    job = await engine.await_timer_job(iid, "BT_AlertaSla")
    await engine.execute_job(job.id)
    await auth_probe.drain()

    sla_alerts = auth_probe.notifications_of_type("auth.notify_sla_risk")
    assert sla_alerts, "Worker notify_sla_risk deve ser executado no alerta de SLA"

    open_keys = {t.task_definition_key for t in await engine.list_user_tasks(iid)}
    assert _UT_AUDITOR in open_keys, "Timer nao-interruptivo nao deve cancelar a User Task"


async def test_timer_sla_estourado_coordenacao_assume(
    engine: EngineRest,
    auth_probe: AuthEngineProbe,
    start_auth: Callable[..., Any],
) -> None:
    """Timer BT_SlaAnalise (interruptivo): UT_AnaliseMedicoAuditor cancelada; UT_CoordenacaoAssume criada."""
    inst = await start_auth(dut_atendida=False)
    iid = inst["id"]

    await auth_probe.drain()
    await auth_probe.drain_analyze()
    await auth_probe.drain()

    await engine.await_user_task(iid, _UT_AUDITOR)

    job = await engine.await_timer_job(iid, "BT_SlaAnalise")
    await engine.execute_job(job.id)
    await auth_probe.drain()

    assert auth_probe.has_event(_AUTH_SLA_BREACHED), "auth.sla_breached deve ser publicado"

    ut_coord = await engine.await_user_task(iid, _UT_COORDENACAO)
    assert "coordenacao-auditoria-medica" in ut_coord.candidate_groups

    open_keys = {t.task_definition_key for t in await engine.list_user_tasks(iid)}
    assert _UT_AUDITOR not in open_keys, "UT_AnaliseMedicoAuditor deve ser cancelada (interruptivo)"


async def test_dmn_auth_sla_urgencia(
    engine: EngineRest,
    auth_probe: AuthEngineProbe,
    start_auth: Callable[..., Any],
) -> None:
    """carater_atendimento=urgencia => DMN auth_sla retorna sla_analise=PT2H."""
    inst = await start_auth(
        dut_atendida=False,
        carater_atendimento="urgencia",
        categoria_procedimento="consulta",
    )
    iid = inst["id"]

    await auth_probe.drain()
    await auth_probe.drain_analyze()
    await auth_probe.drain()

    await engine.await_user_task(iid, _UT_AUDITOR)

    job = await engine.await_timer_job(iid, "BT_AlertaSla")
    assert job.activity_id == "BT_AlertaSla"

    job_sla = await engine.await_timer_job(iid, "BT_SlaAnalise")
    assert job_sla.activity_id == "BT_SlaAnalise"


# ===========================================================================
# Coordenacao assume (SLA breach) e decide
# ===========================================================================


async def test_coordenacao_assume_e_aprova(
    engine: EngineRest,
    auth_probe: AuthEngineProbe,
    start_auth: Callable[..., Any],
) -> None:
    """SLA estourado; coordenacao assume e aprova => End_AprovadaAuditor."""
    inst = await start_auth(dut_atendida=False)
    iid = inst["id"]

    await auth_probe.drain()
    await auth_probe.drain_analyze()
    await auth_probe.drain()

    await engine.await_user_task(iid, _UT_AUDITOR)
    job = await engine.await_timer_job(iid, "BT_SlaAnalise")
    await engine.execute_job(job.id)
    await auth_probe.drain()

    ut_coord = await engine.await_user_task(iid, _UT_COORDENACAO)
    await engine.complete_task_as_human(ut_coord.id, {"decisao_auditor": "APROVAR"})
    await auth_probe.drain()

    ended = await _await_end(engine, iid)
    assert _END_AUDITOR in ended, f"Coordenacao aprova => End_AprovadaAuditor. ended={ended}"
    assert auth_probe.has_event(_AUTH_COMPLETED, desfecho="aprovada_auditor")


async def test_coordenacao_assume_e_nega(
    engine: EngineRest,
    auth_probe: AuthEngineProbe,
    start_auth: Callable[..., Any],
) -> None:
    """SLA estourado; coordenacao nega => End_NegadaAuditor com UT humana no historico."""
    inst = await start_auth(dut_atendida=False)
    iid = inst["id"]

    await auth_probe.drain()
    await auth_probe.drain_analyze()
    await auth_probe.drain()

    await engine.await_user_task(iid, _UT_AUDITOR)
    job = await engine.await_timer_job(iid, "BT_SlaAnalise")
    await engine.execute_job(job.id)
    await auth_probe.drain()

    ut_coord = await engine.await_user_task(iid, _UT_COORDENACAO)
    await engine.complete_task_as_human(
        ut_coord.id,
        {
            "decisao_auditor": "NEGAR",
            "justificativa_clinica": "Prazo esgotado — decisao da coordenacao (sintetico)",
            "cid10_referencia": "Z00.1",
            "fundamentacao_dut": "DUT sintetico para teste",
            "auditor_id": "coordenacao-sintetica-001",
        },
    )
    await auth_probe.drain()

    ended = await _await_end(engine, iid)
    assert _END_NEGADA in ended
    await _assert_no_denial_without_human_task(engine, iid)
    assert auth_probe.has_event(_AUTH_COMPLETED, desfecho="negada_auditor")


# ===========================================================================
# Junta medica (DRAFT)
# ===========================================================================


@pytest.mark.xfail(reason=_ACTION_WORKER_KAFKA_GAP_REASON, strict=True)
async def test_junta_medica_parecer_aprova(
    engine: EngineRest,
    auth_probe: AuthEngineProbe,
    start_auth: Callable[..., Any],
) -> None:
    """Auditor -> JUNTA_MEDICA; convene_junta executado; junta aprova => End_AprovadaAuditor."""
    inst = await start_auth(dut_atendida=False)
    iid = inst["id"]

    await auth_probe.drain()
    await auth_probe.drain_analyze()
    await auth_probe.drain()

    ut = await engine.await_user_task(iid, _UT_AUDITOR)
    await engine.complete_task_as_human(ut.id, {"decisao_auditor": "JUNTA_MEDICA"})
    await auth_probe.drain()

    juntas = auth_probe.notifications_of_type("auth.convene_junta")
    assert juntas, "Worker convene_junta deve ser executado"

    ut_junta = await engine.await_user_task(iid, _UT_JUNTA)
    assert "junta-medica" in ut_junta.candidate_groups

    await engine.complete_task_as_human(ut_junta.id, {"decisao_auditor": "APROVAR"})
    await auth_probe.drain()

    ended = await _await_end(engine, iid)
    assert _END_AUDITOR in ended, f"Junta aprova => End_AprovadaAuditor. ended={ended}"
    assert auth_probe.has_event(_AUTH_COMPLETED, desfecho="aprovada_auditor")


async def test_junta_medica_parecer_nega(
    engine: EngineRest,
    auth_probe: AuthEngineProbe,
    start_auth: Callable[..., Any],
) -> None:
    """Junta nega => End_NegadaAuditor com UT_RegistrarParecerJunta no historico (invariante)."""
    inst = await start_auth(dut_atendida=False)
    iid = inst["id"]

    await auth_probe.drain()
    await auth_probe.drain_analyze()
    await auth_probe.drain()

    ut = await engine.await_user_task(iid, _UT_AUDITOR)
    await engine.complete_task_as_human(ut.id, {"decisao_auditor": "JUNTA_MEDICA"})
    await auth_probe.drain()

    ut_junta = await engine.await_user_task(iid, _UT_JUNTA)
    await engine.complete_task_as_human(
        ut_junta.id,
        {
            "decisao_auditor": "NEGAR",
            "justificativa_clinica": "Parecer unanime da junta: nao indicado (sintetico)",
            "cid10_referencia": "Z01.9",
            "fundamentacao_dut": "DUT sintetico junta para teste",
            "auditor_id": "junta-sintetica-001",
        },
    )
    await auth_probe.drain()

    ended = await _await_end(engine, iid)
    assert _END_NEGADA in ended
    await _assert_no_denial_without_human_task(engine, iid)
    assert auth_probe.has_event(_AUTH_COMPLETED, desfecho="negada_auditor")


# ===========================================================================
# SOLICITAR_INFO => volta para pendencia
# ===========================================================================


@pytest.mark.xfail(reason=_ACTION_WORKER_KAFKA_GAP_REASON, strict=True)
async def test_solicitar_info_volta_para_pendencia(
    engine: EngineRest,
    auth_probe: AuthEngineProbe,
    start_auth: Callable[..., Any],
) -> None:
    """Auditor -> SOLICITAR_INFO => request_documents reexecutado; aguarda docs."""
    inst = await start_auth(dut_atendida=False)
    iid = inst["id"]

    await auth_probe.drain()
    await auth_probe.drain_analyze()
    await auth_probe.drain()

    ut = await engine.await_user_task(iid, _UT_AUDITOR)
    await engine.complete_task_as_human(ut.id, {"decisao_auditor": "SOLICITAR_INFO"})
    await auth_probe.drain()

    req_docs = auth_probe.notifications_of_type("auth.request_documents")
    assert req_docs, "Worker request_documents deve ser executado apos SOLICITAR_INFO"

    assert auth_probe.has_event(_AUTH_PENDED), "auth.pended deve ser publicado apos SOLICITAR_INFO"

    assert await engine.instance_is_active(iid), "Instancia deve estar ativa aguardando docs"


# ===========================================================================
# Idempotencia (business key)
# ===========================================================================


async def test_business_key_uma_instancia_por_guia(
    engine: EngineRest,
    auth_probe: AuthEngineProbe,
    start_auth: Callable[..., Any],
) -> None:
    """Mesmo business key: consultar antes de iniciar; nao criar 2a instancia ativa.

    Nao depende de `auth_probe.drain()` progredir alem do primeiro publish — sobrevive ao
    gap de `operadora.events.publish` acima (nao marcado xfail, ao contrario dos demais).
    """
    guia = "GUIA-TESTE-IDEM-001"
    business_key = f"AUTH-amh-{guia}"

    first = await start_auth(numero_guia_tiss=guia)
    existing = await engine.find_active_instances(business_key)
    assert len(existing) == 1

    active_before = await engine.find_active_instances(business_key)
    assert len(active_before) == 1
    assert active_before[0]["id"] == first["id"]

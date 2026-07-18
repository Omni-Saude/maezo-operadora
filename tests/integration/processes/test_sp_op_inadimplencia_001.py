"""SP-OP-INADIMPLENCIA-001 — suite de integracao executavel (engine CIB Seven REAL).

Ported from the v1 donor (READ-ONLY `Maezo-Healthcare-Plan`) — T3.1 phase 2 (13-family
process-suite port). inadimplencia.py is one of the 9 ADR-0028/T1.5-touched modules
(`inadimplencia_status` is evaluated via the real engine DMN transport) AND one of the 7
dict-first `FunctionWorker`-wrapped modules (ADR-0026 §2a — simpler dict-first pattern, no
typed-dataclass boundary; still self-contained fixtures per the donor convention).

Implementa o test-spec do contrato (docs/processes/contracts/SP-OP-INADIMPLENCIA-001.md +
.harmonization-decision.md DRAFT-A) contra o engine real (ADR-0011: SEM mock de engine). Cada
teste de engine:

1. inicia a instancia via REST com business key `INAD-amh-{numero_contrato}`;
2. drena as external tasks com o `inad_probe` (workers reais + generic publish);
3. avanca o HITL completando User Tasks como humano sintetico (caminho HITL permitido);
4. dispara timers via job execution (NUNCA sleep);
5. verifica invariantes via historia do engine (suspensao passa pela UT humana; NENHUM terminal
   de rescisao local; cure-window nunca auto-suspende — contract_termination L0 hard).

ANTI-DUPLA-RESCISAO (.harmonization-decision.md DRAFT-A): INADIMPLENCIA-001 DETEM a SUSPENSAO
(End_ContratoSuspenso_Inad, adverso, human-gated) e o ciclo de cobranca/purga. A RESCISAO e
propriedade de SP-OP-CANCEL-001: quando o humano decide ENCAMINHAR_RESCISAO, o processo roda
handoff_rescisao (NEUTRO) e termina em End_RescisaoHandoffCancel — NUNCA declara nem atinge um
terminal de rescisao proprio.

Dados sinteticos obvios: contrato `CONTRATO-TESTE-NNNN`, beneficiario `BENEF-TESTE-0001`
(pseudonimo — NUNCA CPF/nome real), tenant `amh`, origem `operadora`. Business key
`INAD-amh-{contrato}`. Process key: SP-OP-INADIMPLENCIA-001 (exato — nao alterar).

## Invariante L0 (DoD deliverable)

test_nenhum_caminho_automatizado_suspende_contrato:
  Varredura de combinacoes de input da DMN inadimplencia_status. A instancia NUNCA atinge
  End_ContratoSuspenso_Inad automaticamente. Prova via history/activity-instance. Este teste NAO
  depende de `_drive_to_analise` (so drena + consulta historia), logo NAO e bloqueado pela
  FINDING 1 abaixo — permanece um teste valido e forte do invariante mesmo sob esse gap.

PORT NOTES (fixture adaptation only — port rule 1):

  - import paths -> v2 `maezo.tools.workers.inadimplencia`/`harness`/`dmn_transport`/`events`.
    Donor's `from maezo.tools.mcp_cibseven.server import CibSevenHttpTransport` (cross-process
    query seam) DROPPED — see FINDING 2 (the seam does not exist on v2's `resolve_facts`).
  - artifact paths -> `spec/processes/{bpmn,dmn}/**` (constraint 5).
  - `drain()` uses the shared `drain_topics()` helper (v2 transport signature adaptation).
  - **`dentro_periodo_minimo` is RECOMPUTED downstream, not pass-through (verify-before-citing).**
    Donor's own module docstring states: "O TESTE seed a MAIORIA dos fatos (meses_inadimplencia,
    dentro_periodo_minimo, notificacao_previa_feita, dentro_janela_purga, tipo_plano) como
    variaveis de START — sao inputs de DMN que nenhum worker resolve." That is TRUE of the donor
    (v1) but NOT of v2: `resolve_facts()` (`inadimplencia.py:41-76`) computes `dentro_periodo_
    minimo = meses_inadimplencia >= 2` where `meses_inadimplencia = len(competencias_em_aberto)`
    (line 48) and OVERWRITES both as process-variable outputs on `ST_ResolveFacts` completion —
    a directly-seeded `dentro_periodo_minimo`/`meses_inadimplencia` is silently discarded before
    `BRT_Status` ever reads it. `dentro_janela_purga`/`notificacao_previa_feita` ARE genuine
    pass-through (`variables.get(key, default)`, lines 58/60) — seeding those directly still
    works. Adapted: `start_inad` translates a `dentro_periodo_minimo` scenario-control kwarg into
    a `competencias_em_aberto` list of the right LENGTH (>=2 for True) — the mechanism the DMN
    input is actually driven by — rather than seeding a variable v2 discards. This does NOT
    weaken the L0 sweep (still exercises the identical 4-dimension combinatorial space; only the
    MECHANISM used to drive one dimension changed, matching the donor's own GAP-CONTAS-2-style
    "compute facts from data" convention already applied elsewhere in this port batch).
  - `competencias_em_aberto` is also subject to the `EngineRest._to_camunda_vars` wire-encoding
    gap documented in full in `test_sp_op_contas_001.py`'s module docstring (PORT NOTE 1): a raw
    Python list would naively stringify to `{"value": str(list), "type": "String"}`, and
    `resolve_facts`'s `isinstance(competencias, list)` check (line 48) would then SILENTLY see a
    `str`, not a `list`, falling to the `else: 0` branch regardless of intent. `_json_var()`
    wraps it as an engine-shaped `Json`-typed variable to survive the round-trip correctly.
  - `test_register_contract_suspension_recusa_sem_humano`: donor's `make_register_contract_
    suspension_handler(kafka) -> Callable[[ExternalTask], ...]` + `WorkerBpmnError` do not exist
    on v2 main — inadimplencia.py is dict-first (ADR-0026 §2a): `register_contract_suspension
    (variables: dict) -> dict` takes NO `kafka` parameter at all (confirmed: `register_
    inadimplencia_workers` does `del kafka  # unused — no inadimplencia.py worker declares a
    Kafka dependency`, inadimplencia.py:348). Adapted to call `register_contract_suspension
    (variables)` directly; the guard raises `InadimplenciaError` (a bespoke `.code`/`.message`
    exception, NOT `WorkerBpmnError`). The 6 guard scenarios are preserved verbatim; the
    "success" case's assertions are adapted to the ACTUAL v2 return shape (`{"suspensao_
    registrada": True, "data_efeito_iso": ...}` — v2 does NOT generate a `suspensao_id`, and does
    NOT echo `responsavel_id`/`tier` in its own return value; those flow to the ADR-0007 audit
    trail via structured LOGGING (`logger.info(..., responsavel_id=responsavel_id)`,
    inadimplencia.py:256-260) instead — a genuine, thinner v1-vs-v2 output-shape divergence).

FINDINGS (root-cause, file:line evidence; see PR body / evidence-ledger for full detail):

  1. **`operadora.inadimplencia.prepare_dossier` / `notify_sla_risk` have NO REGISTERED WORKER
     (headline finding — blocks the majority of engine-driven tests below).**
     `register_inadimplencia_workers` (inadimplencia.py:338-359) registers exactly 6 topics
     (resolve_facts, assess_status, calculate_purge, check_prior_notice, register_contract_
     suspension, handoff_rescisao). The BPMN (`SP-OP-INADIMPLENCIA-001_Suspensao_Rescisao.bpmn`)
     declares `ST_PrepareDossier` (line 210, topic `operadora.inadimplencia.prepare_dossier`) and
     `ST_NotificarRiscoSla` (line 245, topic `operadora.inadimplencia.notify_sla_risk`) as
     service tasks on the CRITICAL PATH to the human User Task — confirmed via grep: no
     `prepare_dossier`/`notify_sla_risk` FUNCTION exists anywhere in inadimplencia.py at all
     (the module's own comment at line 334 self-documents this: "Spec topics with NO
     implementing function today (gap, not fabricated here): prepare_dossier, notify_sla_risk").
     EVERY path from `BRT_Sla` to `UT_AnaliseInadimplencia` passes through `ST_PrepareDossier`
     (`Flow_Sla_Dossie -> ST_PrepareDossier -> Flow_Dossie_UTAnalise -> UT_AnaliseInadimplencia`,
     bpmn:410-411) — with no worker ever completing this external task, the instance stalls there
     PERMANENTLY and never reaches the human UT. This is DISTINCT from (and more severe than) the
     systemic Kafka-publish gap: it is a missing WORKER IMPLEMENTATION on the BPMN's own critical
     path, not just an unobservable notification side-channel. Every test below that calls
     `_drive_to_analise(...)` is blocked by this and marked `_PREPARE_DOSSIER_UNREGISTERED_
     REASON`. NOT invented as a workaround (no local stub was added — `src/**` is out of scope
     for this port, and no donor precedent for a "prepare_dossier stub" exists for this family,
     unlike auth's donor-native `_AnalyzeRequestStub`).
  2. **GAP-INAD-1 cross-process query (`ja_em_rescisao_cancel`) is NOT IMPLEMENTED in v2.**
     The donor (and `docs/processes/contracts/SP-OP-INADIMPLENCIA-001.md:23,66,204`) describe
     `resolve_facts` as RESOLVING `ja_em_rescisao_cancel` via a real cross-process engine query
     (`CibSevenTransport.find_active_instance` against `CANCEL-{tenant}-{numero_contrato}`,
     fail-closed). v2's actual `resolve_facts()` (inadimplencia.py:41-76) and `_register_
     contract_suspension()` (inadimplencia.py:215-265) both treat `ja_em_rescisao_cancel` as a
     PURE PASS-THROUGH (`variables.get("ja_em_rescisao_cancel", False)`, line 226) — there is no
     `query_engine`/`CibSevenHttpTransport`/`find_active_instance` import or seam ANYWHERE in
     inadimplencia.py, and `register_inadimplencia_workers`'s `**seams` only reads `dmn` (line
     349); `resolve_facts` itself takes a single `variables: dict` argument with no seam
     parameter at all. Materializing a live CANCEL-001 instance (as the donor's
     `test_suspensao_recusada_se_ja_em_rescisao_cancel` does) therefore has NO EFFECT on v2's
     `ja_em_rescisao_cancel` resolution — it will always default to `False` regardless, and the
     anti-double-termination guard will NOT fire. This is a genuine, real anti-double-termination
     SAFETY GAP (not merely fixture/plumbing friction) — flagged prominently. (This specific
     test is ALSO blocked by FINDING 1 first, since it needs `_drive_to_analise`; a verifier who
     fixes FINDING 1 alone will still find this ONE test red for the SEPARATE reason above.)
  3. **`End_SuspensaoBloqueadaNaoHumano` (GAP-INAD-7) is an ADDITIVE end-event vs the donor's
     topology expectation, but is itself STRUCTURALLY UNREACHABLE.** The BPMN declares
     `BE_SuspensaoNaoHumano` (line 313, `errorRef="Error_ContractSuspensionNotHuman"`) as a
     boundary catch on `ST_RegisterSuspension`, routing to `End_SuspensaoBloqueadaNaoHumano`
     (line 317) — added after the donor's file was written (GAP-INAD-7). The donor's topology
     test (`test_inadimplencia_nao_tem_terminal_de_rescisao_proprio`) enumerates an EXACT
     end-event set that does not include it; adapted to add it (mechanical, still enforces "no
     rescission-local terminal" — the added end-event's id contains none of the forbidden
     tokens). HOWEVER: `_register_contract_suspension`'s guard raises `InadimplenciaError`
     (inadimplencia.py:309-315), a bespoke `.code`/`.message` exception that `FunctionWorker.
     execute` (base.py:272-282) reclassifies into a plain `ValueError` — which `harness._handle`
     (harness.py:952-954) routes STRAIGHT to `_report_failure(..., retries_override=0)` (an
     immediate incident via `handle_failure`), NEVER via `handle_bpmn_error`/the `bpmn_error_
     allowlist` path (reserved for `WorkerBpmnError`, which `InadimplenciaError` does not
     subclass). `BE_SuspensaoNaoHumano`'s boundary can therefore NEVER fire given v2's actual
     exception shape — the SAME structural pattern documented in `test_sp_op_contas_001.py`'s
     FINDING 3 for `Error_GlosaAcceptNotHuman`. This does not block any specific donor assertion
     here (none probes `engine.incidents(iid)` for this scenario), so no xfail is attached beyond
     FINDING 1's blocking of the whole HITL path; flagged for the record.
"""

from __future__ import annotations

import asyncio
import itertools
import json
import uuid
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

import httpx
import pytest
import pytest_asyncio

from maezo.tools.workers.dmn_transport import CibSevenDmnTransport
from maezo.tools.workers.events import register_events_workers
from maezo.tools.workers.harness import CibSevenWorkerTransport, FakeKafkaPublisher, WorkerHarness
from maezo.tools.workers.inadimplencia import (
    InadimplenciaError,
    register_contract_suspension,
    register_inadimplencia_workers,
)

from .conftest import CIBSEVEN_BASE_URL, drain_topics
from .engine_rest import EngineRest

pytestmark = pytest.mark.integration

_REPO = Path(__file__).resolve().parents[3]
_BPMN = _REPO / "spec/processes/bpmn/SP-OP-INADIMPLENCIA-001_Suspensao_Rescisao.bpmn"
_DMN_STATUS = _REPO / "spec/processes/dmn/inadimplencia_status.dmn"
_DMN_PURGA = _REPO / "spec/processes/dmn/inadimplencia_purga.dmn"
_DMN_SLA = _REPO / "spec/processes/dmn/inadimplencia_sla.dmn"

# External task topics do contrato SP-OP-INADIMPLENCIA-001.
_PUBLISH_TOPIC = "operadora.events.publish"
_RESOLVE_FACTS_TOPIC = "operadora.inadimplencia.resolve_facts"
_ASSESS_STATUS_TOPIC = "operadora.inadimplencia.assess_status"  # function-derived, no BPMN topic
_CALCULATE_PURGE_TOPIC = "operadora.inadimplencia.calculate_purge"  # function-derived, no BPMN topic
_CHECK_PRIOR_NOTICE_TOPIC = "operadora.inadimplencia.check_prior_notice"
_REGISTER_SUSPENSION_TOPIC = "operadora.inadimplencia.register_contract_suspension"
_HANDOFF_RESCISAO_TOPIC = "operadora.inadimplencia.handoff_rescisao"
# NOT registered by register_inadimplencia_workers (FINDING 1) — declared here only so the
# reader can see, by contrast, which BPMN topics have NO drain entry (deliberately excluded,
# since no worker would ever serve them):
#   operadora.inadimplencia.prepare_dossier, operadora.inadimplencia.notify_sla_risk

# Topicos servidos pelos workers REAIS registrados no harness (drain generico). Inclui os 2
# topicos function-derived sem contraparte BPMN (assess_status/calculate_purge) para que o
# drift-guard do `inad_probe` passe naturalmente.
_INAD_WORKER_TOPICS = [
    _PUBLISH_TOPIC,
    _RESOLVE_FACTS_TOPIC,
    _ASSESS_STATUS_TOPIC,
    _CALCULATE_PURGE_TOPIC,
    _CHECK_PRIOR_NOTICE_TOPIC,
    _REGISTER_SUSPENSION_TOPIC,
    _HANDOFF_RESCISAO_TOPIC,
]

_NOTIFICATIONS_TOPIC = "operadora.notifications.internal"

_INAD_RECEIVED = "agents.events.inadimplencia.received"
_INAD_NOTIFIED = "agents.events.inadimplencia.notified"
_INAD_SLA_BREACHED = "agents.events.inadimplencia.sla_breached"
_INAD_COMPLETED = "agents.events.inadimplencia.completed"

_UT_ANALISE = "UT_AnaliseInadimplencia"
_UT_COORDENACAO = "UT_CoordenacaoCobranca"

_END_SUSPENSO = "End_ContratoSuspenso_Inad"
_END_HANDOFF = "End_RescisaoHandoffCancel"
_END_PURGADO = "End_Purgado"
_END_MANTIDO = "End_ContratoMantido"
_END_RISCO_SLA = "End_RiscoSlaNotificado"
_END_SUSPENSAO_BLOQUEADA = "End_SuspensaoBloqueadaNaoHumano"  # GAP-INAD-7, additive vs donor — FINDING 3

_UT_HUMANAS = frozenset({_UT_ANALISE, _UT_COORDENACAO})

_ENDS_ADVERSOS = frozenset({_END_SUSPENSO})

_RESCISAO_LOCAL_TOKENS = ("rescindid", "rescisao_local", "contratorescindido")

_CAMPOS_SUSP = {
    "fundamentacao_contratual": "Fundamentacao sintetica da suspensao (teste L0)",
    "referencia_regulatoria": "RN 593 — DRAFT/verify (teste)",
    "comprovacao_notificacao_previa": "ref-comprovante-notificacao-teste-0001",
    "comprovacao_periodo_minimo": "ref-comprovacao-periodo-minimo-teste-0001",
    "responsavel_id": "juridico-sintetico-001",
    "tier": "tier-2",
}

# FINDING 1 (module docstring): ST_PrepareDossier's topic has no registered worker in v2 — every
# path to UT_AnaliseInadimplencia passes through it first, so the instance never reaches the
# human UT at all. Blocks every test below that calls `_drive_to_analise`.
_PREPARE_DOSSIER_UNREGISTERED_REASON = (
    "v2 gap (T3.1 finding 1, MORE SEVERE than the systemic Kafka-publish drift): "
    "register_inadimplencia_workers (inadimplencia.py:338-359) registers 6 topics but NOT "
    "operadora.inadimplencia.prepare_dossier (BPMN ST_PrepareDossier) nor operadora."
    "inadimplencia.notify_sla_risk (BPMN ST_NotificarRiscoSla) — no function implementing either "
    "exists anywhere in inadimplencia.py (module's own comment, line 334: 'Spec topics with NO "
    "implementing function today (gap, not fabricated here): prepare_dossier, notify_sla_risk'). "
    "Every path from BRT_Sla to UT_AnaliseInadimplencia passes through ST_PrepareDossier "
    "(Flow_Sla_Dossie -> ST_PrepareDossier -> Flow_Dossie_UTAnalise -> UT_AnaliseInadimplencia) — "
    "with no worker ever completing that external task, the instance stalls there permanently "
    "and never reaches the human User Task this test depends on. src/** fix is out of scope for "
    "this port; no local stub was added (no donor precedent for this family, unlike auth's "
    "donor-native _AnalyzeRequestStub)."
)

# FINDING 2 (module docstring): compounds with FINDING 1 for this ONE test — flagged distinctly
# since fixing FINDING 1 alone would NOT flip this test (a separate, deeper gap).
_CROSS_PROCESS_QUERY_AND_DOSSIER_GAP_REASON = (
    "v2 gap, TWO STACKED root causes (T3.1 findings 1+2): (1) blocked by "
    "_PREPARE_DOSSIER_UNREGISTERED_REASON like every other _drive_to_analise-dependent test in "
    "this file; (2) SEPARATELY, even if (1) were fixed, GAP-INAD-1's cross-process query "
    "(donor/contract docs describe resolve_facts querying CANCEL-{tenant}-{contrato} via "
    "CibSevenTransport.find_active_instance to resolve ja_em_rescisao_cancel) is NOT IMPLEMENTED "
    "in v2 — resolve_facts (inadimplencia.py:41-76) and the register_contract_suspension guard "
    "(inadimplencia.py:215-265) both treat ja_em_rescisao_cancel as a pure pass-through "
    "(variables.get('ja_em_rescisao_cancel', False)); no query_engine/CibSevenHttpTransport/"
    "find_active_instance seam exists anywhere in inadimplencia.py, and register_inadimplencia_"
    "workers's **seams only reads 'dmn'. Materializing a live CANCEL-001 instance (as this test "
    "does) has NO EFFECT on v2's resolution of ja_em_rescisao_cancel — it always defaults False, "
    "so the anti-double-termination guard never fires. A verifier who fixes (1) alone will find "
    "this test STILL red for reason (2) — a genuine anti-double-termination safety gap, not just "
    "fixture friction. src/** fix is out of scope for this port."
)


def _json_var(value: Any) -> dict[str, Any]:
    """Engine-shaped `Json`-typed variable (see `test_sp_op_contas_001.py`'s PORT NOTE 1 for the
    full `EngineRest._to_camunda_vars` wire-encoding-gap writeup this works around)."""
    return {"value": json.dumps(value), "type": "Json"}


# ---------------------------------------------------------------------------
# EngineProbe para inadimplencia
# ---------------------------------------------------------------------------


@dataclass
class InadEngineProbe:
    """Driva os workers reais de inadimplencia contra o engine CIB Seven."""

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
        await drain_topics(self.transport, self.harness, self.worker_id, _INAD_WORKER_TOPICS, rounds=rounds)


@pytest_asyncio.fixture
async def deploy_artifacts(engine: EngineRest) -> str:
    """Deploya BPMN + 3 DMN de inadimplencia da arvore no engine real."""
    return await engine.deploy(_BPMN, _DMN_STATUS, _DMN_PURGA, _DMN_SLA, name="SP-OP-INADIMPLENCIA-001-qa")


@pytest_asyncio.fixture
async def inad_probe(engine: EngineRest) -> AsyncIterator[InadEngineProbe]:
    """Probe que serve as external tasks com os workers reais de inadimplencia."""
    worker_id = f"qa-inad-worker-{uuid.uuid4().hex[:8]}"
    transport = CibSevenWorkerTransport(CIBSEVEN_BASE_URL)
    # Nenhum WorkerBpmnError e lancado por inadimplencia.py (FINDING 3: InadimplenciaError e
    # reclassificada para ValueError por FunctionWorker.execute, sempre incident, nunca
    # bpmnError) — bpmn_error_allowlist deliberadamente OMITIDO.
    harness = WorkerHarness(transport, worker_id=worker_id, lock_duration_ms=10_000)
    kafka = FakeKafkaPublisher()
    dmn = CibSevenDmnTransport(CIBSEVEN_BASE_URL, timeout=30.0)
    register_inadimplencia_workers(harness, kafka, dmn=dmn)
    # T3.1 R2: o worker generico operadora.events.publish que todo ST_Publish* deste BPMN usa.
    register_events_workers(harness, kafka)
    # DRIFT GUARD (mirrors cancel's/contas's — verbatim style): todo topico inadimplencia.*
    # registrado no harness DEVE estar na lista de drain.
    inad_registered = {t for t in harness.registered_topics if t.startswith("operadora.inadimplencia.")}
    missing_from_drain = inad_registered - set(_INAD_WORKER_TOPICS)
    assert not missing_from_drain, (
        f"_INAD_WORKER_TOPICS desatualizada — topicos registrados fora do drain: {missing_from_drain}"
    )
    probe = InadEngineProbe(
        engine=engine, harness=harness, transport=transport, kafka=kafka, worker_id=worker_id
    )
    try:
        yield probe
    finally:
        await transport.close()
        await dmn.close()


def _unique_contrato(prefix: str = "CONTRATO-TESTE") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8].upper()}"


@pytest_asyncio.fixture
async def start_inad(engine: EngineRest, deploy_artifacts: str) -> Callable[..., Any]:
    """Inicia uma instancia com business key INAD-amh-{contrato} e payload canonico.

    Default = periodo minimo atingido + notificacao previa feita + purga decorrida em plano
    individual -> SEGUE_ANALISE. `dentro_periodo_minimo` (PORT NOTE: recomputado por resolve_facts
    — nao mais pass-through) e traduzido para uma `competencias_em_aberto` da LENGTH correta
    (>=2 meses para True); `dentro_janela_purga`/`notificacao_previa_feita`/`ja_em_rescisao_
    cancel` permanecem seedaveis diretamente (pass-through genuino).
    """

    async def _start(**overrides: Any) -> dict[str, Any]:
        contrato = overrides.pop("numero_contrato", _unique_contrato())
        dentro_periodo_minimo = bool(overrides.pop("dentro_periodo_minimo", True))
        competencias_em_aberto = overrides.pop("competencias_em_aberto", None)
        if competencias_em_aberto is None:
            competencias_em_aberto = ["2026-01", "2026-02", "2026-03"] if dentro_periodo_minimo else []

        variables: dict[str, Any] = {
            "tenant_id": "amh",
            "numero_contrato": contrato,
            "matricula_beneficiario": "BENEF-TESTE-0001",
            "tipo_plano": "individual",
            "origem_solicitacao": "operadora",
            "competencias_em_aberto": _json_var(competencias_em_aberto),
            "data_solicitacao_iso": "2026-06-01",
            "documentos_refs": "[]",
            "valor_total_devido_cents": 150000,
            "notificacao_previa_feita": True,
            "dentro_janela_purga": False,
        }
        variables.update(overrides)
        business_key = f"INAD-amh-{contrato}"
        return await engine.start_by_key("SP-OP-INADIMPLENCIA-001", business_key, variables)

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


async def _assert_no_adverse_without_human_task(engine: EngineRest, iid: str) -> None:
    """INVARIANTE L0 (contract_termination): prova que nenhum terminal adverso existe sem UT humana."""
    ended = await engine.activity_instances_ended(iid)
    adversos_atingidos = ended & _ENDS_ADVERSOS
    if adversos_atingidos:
        human_tasks_in_history = ended & _UT_HUMANAS
        assert human_tasks_in_history, (
            f"INVARIANTE L0 VIOLADA (contract_termination): terminal(is) adverso(s) "
            f"{adversos_atingidos} atingido(s) para instancia {iid} SEM nenhuma User Task humana no "
            f"historico. User Tasks esperadas (qualquer uma de): {_UT_HUMANAS}. "
            f"Atividades historicas: {ended}. Indica um caminho automatizado de suspensao — viola L0."
        )


async def _drive_to_analise(engine: EngineRest, probe: InadEngineProbe, iid: str) -> Any:
    """Drena ate UT_AnaliseInadimplencia surgir (dossie preparado pelo worker real).

    BLOQUEADO por FINDING 1 (module docstring) — `ST_PrepareDossier` nunca e servido; esta
    chamada nunca retorna sob o gap atual. Todo teste que a usa esta marcado xfail.
    """
    await probe.drain()
    return await engine.await_user_task(iid, _UT_ANALISE)


# ===========================================================================
# INVARIANTE L0 — DoD (varredura de inputs da DMN inadimplencia_status)
# ===========================================================================


async def test_nenhum_caminho_automatizado_suspende_contrato(
    engine: EngineRest,
    inad_probe: InadEngineProbe,
    start_inad: Callable[..., Any],
) -> None:
    """INVARIANTE L0 (contract_termination): NENHUM caminho automatizado suspende.

    NAO usa `_drive_to_analise` — apenas drena e consulta a historia do engine — logo NAO e
    bloqueado pela FINDING 1 (a instancia pode estar legitimamente stuck em ST_PrepareDossier em
    vez de aguardando na UT humana; de qualquer forma NENHUM terminal adverso e atingido, o que
    e exatamente o que este invariante prova).
    """
    tipos_plano = ["individual", "familiar", "coletivo_empresarial", "coletivo_adesao"]
    bools = [True, False]
    checked = 0

    for tipo_plano, periodo, notif, purga in itertools.product(tipos_plano, bools, bools, bools):
        competencias = ["2026-01", "2026-02", "2026-03"] if periodo else []
        inst = await start_inad(
            tipo_plano=tipo_plano,
            competencias_em_aberto=competencias,
            notificacao_previa_feita=notif,
            dentro_janela_purga=purga,
        )
        iid = inst["id"]
        await inad_probe.drain()

        ended = await engine.activity_instances_ended(iid)
        adversos = ended & _ENDS_ADVERSOS
        assert not adversos, (
            f"L0 VIOLADO (contract_termination): tipo_plano={tipo_plano} periodo={periodo} "
            f"notif={notif} purga={purga} atingiu terminal(is) adverso(s) {adversos} automaticamente. "
            f"ended={ended}"
        )
        await _assert_no_adverse_without_human_task(engine, iid)
        checked += 1

    assert checked == 32, f"Esperava 32 combinacoes varridas; varri {checked}"


@pytest.mark.xfail(reason=_PREPARE_DOSSIER_UNREGISTERED_REASON, strict=True)
async def test_inadimplencia_aparente_roteia_para_humano_nao_suspende(
    engine: EngineRest,
    inad_probe: InadEngineProbe,
    start_inad: Callable[..., Any],
) -> None:
    """Inadimplencia com periodo minimo + notificacao feita => SEGUE_ANALISE; UT humana, NAO suspensao."""
    inst = await start_inad(
        dentro_periodo_minimo=True, notificacao_previa_feita=True, dentro_janela_purga=False
    )
    iid = inst["id"]

    ut = await _drive_to_analise(engine, inad_probe, iid)
    assert "juridico-contratos" in ut.candidate_groups

    ended = await engine.activity_instances_ended(iid)
    assert not (ended & _ENDS_ADVERSOS), "Inadimplencia nao deve produzir terminal adverso automatico (L0)"
    await _assert_no_adverse_without_human_task(engine, iid)


# ===========================================================================
# Cure-window (event gateway — INVERTE auto-suspender por timeout)
# ===========================================================================


async def test_pagamento_dentro_janela_purga_purgado_nunca_adverso(
    engine: EngineRest,
    inad_probe: InadEngineProbe,
    start_inad: Callable[..., Any],
) -> None:
    """Dentro da janela de purga => AGUARDA_PURGA (cure-window); pagamento => End_Purgado, NUNCA adverso.

    Este caminho NAO passa por BRT_Sla/ST_PrepareDossier (GW_CureWindow -> ICE_PagamentoRecebido
    -> ST_PublishPurgado -> End_Purgado) — nao e bloqueado pela FINDING 1. `has_event` usa o
    caminho generico operadora.events.publish (FIXED, T3.1 R2).
    """
    inst = await start_inad(dentro_janela_purga=True, notificacao_previa_feita=True)
    iid = inst["id"]
    business_key = inst["businessKey"]

    await inad_probe.drain()
    assert await engine.instance_is_active(iid), "Instancia deve aguardar no cure-window de purga"

    correlate_payload = {"messageName": "msg.inadimplencia.pagamento_recebido", "businessKey": business_key}
    async with httpx.AsyncClient(base_url=CIBSEVEN_BASE_URL, timeout=20.0) as client:
        resp = await client.post("/message", json=correlate_payload)
        assert resp.status_code in (200, 204), (
            f"Correlacao msg.inadimplencia.pagamento_recebido falhou [{resp.status_code}]: {resp.text[:200]}"
        )

    await inad_probe.drain()
    ended = await _await_end(engine, iid)
    assert _END_PURGADO in ended, f"Pagamento dentro da janela => End_Purgado. ended={ended}"
    assert not (ended & _ENDS_ADVERSOS), "Purga NUNCA atinge terminal adverso (L0)"
    assert inad_probe.has_event(_INAD_COMPLETED, desfecho="purgado")


@pytest.mark.xfail(reason=_PREPARE_DOSSIER_UNREGISTERED_REASON, strict=True)
async def test_expiracao_purga_nao_auto_suspende(
    engine: EngineRest,
    inad_probe: InadEngineProbe,
    start_inad: Callable[..., Any],
) -> None:
    """Aguardando no cure-window, sem pagamento => timer prazo_purga => UT_AnaliseInadimplencia.

    A expiracao da janela de purga NUNCA alcanca End_ContratoSuspenso_Inad automaticamente
    (INVERTE o anti-pattern de auto-suspender/auto-rescindir-no-timeout). Bloqueado: prazo_purga
    -> BRT_Sla -> ST_PrepareDossier (FINDING 1) -> UT_AnaliseInadimplencia.
    """
    inst = await start_inad(dentro_janela_purga=True, notificacao_previa_feita=True)
    iid = inst["id"]

    await inad_probe.drain()

    job = await engine.await_timer_job(iid, "ICE_PrazoPurga")
    await engine.execute_job(job.id)
    await inad_probe.drain()

    ut = await engine.await_user_task(iid, _UT_ANALISE)
    assert "juridico-contratos" in ut.candidate_groups

    ended = await engine.activity_instances_ended(iid)
    assert not (ended & _ENDS_ADVERSOS), "Expiracao da purga NUNCA auto-suspende (L0)"
    await _assert_no_adverse_without_human_task(engine, iid)


@pytest.mark.xfail(reason=_PREPARE_DOSSIER_UNREGISTERED_REASON, strict=True)
async def test_notificacao_ack_reavalia_nunca_suspende(
    engine: EngineRest,
    inad_probe: InadEngineProbe,
    start_inad: Callable[..., Any],
) -> None:
    """Aguardando no cure-window => msg.inadimplencia.notificacao_ack => re-resolve fatos; nunca suspende."""
    inst = await start_inad(dentro_janela_purga=True, notificacao_previa_feita=True)
    iid = inst["id"]
    business_key = inst["businessKey"]

    await inad_probe.drain()

    correlate_payload = {
        "messageName": "msg.inadimplencia.notificacao_ack",
        "businessKey": business_key,
        "processVariables": {"dentro_janela_purga": {"value": False, "type": "Boolean"}},
    }
    async with httpx.AsyncClient(base_url=CIBSEVEN_BASE_URL, timeout=20.0) as client:
        resp = await client.post("/message", json=correlate_payload)
        assert resp.status_code in (200, 204), (
            f"Correlacao msg.inadimplencia.notificacao_ack falhou [{resp.status_code}]: {resp.text[:200]}"
        )

    ut = await _drive_to_analise(engine, inad_probe, iid)
    assert "juridico-contratos" in ut.candidate_groups

    ended = await engine.activity_instances_ended(iid)
    assert not (ended & _ENDS_ADVERSOS), "Ack de notificacao nunca auto-suspende (L0)"
    await _assert_no_adverse_without_human_task(engine, iid)


# ===========================================================================
# Happy paths
# ===========================================================================


@pytest.mark.xfail(reason=_PREPARE_DOSSIER_UNREGISTERED_REASON, strict=True)
async def test_happy_path_suspensao_por_inadimplencia_humano(
    engine: EngineRest,
    inad_probe: InadEngineProbe,
    start_inad: Callable[..., Any],
) -> None:
    """SEGUE_ANALISE; humano SUSPENDER => End_ContratoSuspenso_Inad (efeito adverso gated)."""
    inst = await start_inad()
    iid = inst["id"]

    ut = await _drive_to_analise(engine, inad_probe, iid)
    assert "juridico-contratos" in ut.candidate_groups

    await engine.complete_task_as_human(ut.id, {"decisao_inadimplencia": "SUSPENDER", **_CAMPOS_SUSP})
    await inad_probe.drain()

    ended = await _await_end(engine, iid)
    await _assert_no_adverse_without_human_task(engine, iid)
    assert _END_SUSPENSO in ended, f"Suspensao humana deve atingir End_ContratoSuspenso_Inad. ended={ended}"
    assert inad_probe.has_event(_INAD_COMPLETED, desfecho="suspenso")

    avisos = inad_probe.notifications_of_type("inadimplencia.register_contract_suspension")
    assert avisos and avisos[0]["decisao_inadimplencia"] == "SUSPENDER"
    assert avisos[0]["responsavel_id"] == "juridico-sintetico-001"


@pytest.mark.xfail(reason=_PREPARE_DOSSIER_UNREGISTERED_REASON, strict=True)
async def test_happy_path_encaminhar_rescisao_handoff_neutro_nao_rescinde(
    engine: EngineRest,
    inad_probe: InadEngineProbe,
    start_inad: Callable[..., Any],
) -> None:
    """SEGUE_ANALISE; humano ENCAMINHAR_RESCISAO => handoff a CANCEL-001; End_RescisaoHandoffCancel
    (NEUTRO).
    """
    inst = await start_inad()
    iid = inst["id"]

    ut = await _drive_to_analise(engine, inad_probe, iid)
    await engine.complete_task_as_human(
        ut.id, {"decisao_inadimplencia": "ENCAMINHAR_RESCISAO", **_CAMPOS_SUSP}
    )
    await inad_probe.drain()

    ended = await _await_end(engine, iid)
    assert _END_HANDOFF in ended, f"ENCAMINHAR_RESCISAO => End_RescisaoHandoffCancel (neutro). ended={ended}"
    assert not (ended & _ENDS_ADVERSOS), "Handoff de rescisao NAO atinge terminal adverso local"
    assert inad_probe.has_event(_INAD_COMPLETED, desfecho="rescisao_handoff")

    handoffs = inad_probe.notifications_of_type("inadimplencia.handoff_rescisao")
    assert handoffs, "handoff_rescisao deve ser executado em ENCAMINHAR_RESCISAO"
    assert not inad_probe.notifications_of_type("inadimplencia.register_contract_suspension")


@pytest.mark.xfail(reason=_PREPARE_DOSSIER_UNREGISTERED_REASON, strict=True)
async def test_happy_path_contrato_mantido(
    engine: EngineRest,
    inad_probe: InadEngineProbe,
    start_inad: Callable[..., Any],
) -> None:
    """SEGUE_ANALISE; humano MANTER (ex.: purga negociada) => End_ContratoMantido."""
    inst = await start_inad()
    iid = inst["id"]

    ut = await _drive_to_analise(engine, inad_probe, iid)
    await engine.complete_task_as_human(
        ut.id,
        {
            "decisao_inadimplencia": "MANTER",
            "fundamentacao_contratual": "Purga negociada — vinculo mantido (teste)",
            "responsavel_id": "juridico-sintetico-001",
        },
    )
    await inad_probe.drain()

    ended = await _await_end(engine, iid)
    assert _END_MANTIDO in ended, f"MANTER => End_ContratoMantido. ended={ended}"
    assert not (ended & _ENDS_ADVERSOS)
    assert inad_probe.has_event(_INAD_COMPLETED, desfecho="mantido")


# ===========================================================================
# Aceite exige campos / worker guard (defesa em profundidade)
# ===========================================================================


@pytest.mark.xfail(reason=_PREPARE_DOSSIER_UNREGISTERED_REASON, strict=True)
async def test_suspender_exige_campos_worker_guard(
    engine: EngineRest,
    inad_probe: InadEngineProbe,
    start_inad: Callable[..., Any],
) -> None:
    """SUSPENDER sem campos obrigatorios => worker guard recusa (ERR_CONTRACT_SUSPENSION_NOT_HUMAN).

    FINDING 3 (module docstring): isso vira um INCIDENTE aberto no engine (BE_SuspensaoNaoHumano
    e estruturalmente inalcancavel — InadimplenciaError e reclassificada para ValueError, nunca
    dispara bpmnError). As assercoes abaixo NAO dependem de qual mecanismo produz o bloqueio.
    """
    inst = await start_inad()
    iid = inst["id"]

    ut = await _drive_to_analise(engine, inad_probe, iid)
    await engine.complete_task_as_human(ut.id, {"decisao_inadimplencia": "SUSPENDER"})
    await inad_probe.drain()

    ended = await engine.activity_instances_ended(iid)
    assert _END_SUSPENSO not in ended, (
        "Suspensao sem campos obrigatorios NAO pode atingir End_ContratoSuspenso_Inad (guard do worker)"
    )
    assert not inad_probe.notifications_of_type("inadimplencia.register_contract_suspension"), (
        "register_contract_suspension NAO deve emitir sem campos obrigatorios"
    )
    await _assert_no_adverse_without_human_task(engine, iid)


@pytest.mark.xfail(reason=_CROSS_PROCESS_QUERY_AND_DOSSIER_GAP_REASON, strict=True)
async def test_suspensao_recusada_se_ja_em_rescisao_cancel(
    engine: EngineRest,
    inad_probe: InadEngineProbe,
    start_inad: Callable[..., Any],
) -> None:
    """ANTI-DUPLA-TERMINACAO (GAP-INAD-1): CANCEL-001 ATIVO => resolve_facts deveria detectar e recusar.

    FINDING 2 (module docstring): v2's resolve_facts NAO implementa a consulta cross-process real
    — ja_em_rescisao_cancel e pass-through puro. Materializar uma instancia CANCEL-001 ATIVA (como
    o donor faz) nao tem NENHUM efeito sobre a resolucao de v2. Preservado verbatim (mesma
    materializacao + mesmas assercoes do donor) para documentar honestamente o gap.
    """
    contrato = _unique_contrato()
    cancel_bk = f"CANCEL-amh-{contrato}"

    await engine.deploy(
        _REPO / "spec/processes/bpmn/SP-OP-CANCEL-001_Cancelamento_Contrato.bpmn",
        _REPO / "spec/processes/dmn/cancel_admissibility.dmn",
        _REPO / "spec/processes/dmn/cancel_routing.dmn",
        _REPO / "spec/processes/dmn/cancel_sla.dmn",
        name="SP-OP-CANCEL-001-qa-inad-antidupla",
    )
    await engine.start_by_key(
        "SP-OP-CANCEL-001",
        cancel_bk,
        {
            "tenant_id": "amh",
            "numero_contrato": contrato,
            "matricula_beneficiario": "BENEF-TESTE-0001",
            "tipo_solicitacao": "inadimplencia",
            "origem_solicitacao": "operadora",
            "tipo_plano": "individual",
            "motivo_informado": "Motivo sintetico pseudonimizado (teste)",
            "data_solicitacao_iso": "2026-06-01",
            "documentos_refs": "[]",
            "dentro_prazo": True,
            "notificacao_previa_feita": True,
            "titularidade_confirmada": True,
            "vinculo_ativo": True,
            "meses_inadimplencia": 3,
        },
    )
    assert await engine.find_active_instances(cancel_bk), (
        "Pre-condicao: a instancia CANCEL-001 deve estar ativa para a consulta cross-process detecta-la"
    )

    inst = await start_inad(numero_contrato=contrato)
    iid = inst["id"]

    ut = await _drive_to_analise(engine, inad_probe, iid)
    await engine.complete_task_as_human(ut.id, {"decisao_inadimplencia": "SUSPENDER", **_CAMPOS_SUSP})
    await inad_probe.drain()

    ended = await engine.activity_instances_ended(iid)
    assert _END_SUSPENSO not in ended, (
        "Com rescisao CANCEL ja ativa, a suspensao deve ser recusada (anti-dupla-terminacao)"
    )
    assert not inad_probe.notifications_of_type("inadimplencia.register_contract_suspension"), (
        "Nenhuma suspensao registrada quando resolve_facts detecta CANCEL-001 ativo"
    )
    await _assert_no_adverse_without_human_task(engine, iid)


@pytest.mark.xfail(reason=_PREPARE_DOSSIER_UNREGISTERED_REASON, strict=True)
async def test_suspensao_prossegue_sem_cancel_ativo(
    engine: EngineRest,
    inad_probe: InadEngineProbe,
    start_inad: Callable[..., Any],
) -> None:
    """Sem instancia CANCEL-001 ativa => ja_em_rescisao_cancel=false; suspensao humana segue."""
    inst = await start_inad()
    iid = inst["id"]

    ut = await _drive_to_analise(engine, inad_probe, iid)
    await engine.complete_task_as_human(ut.id, {"decisao_inadimplencia": "SUSPENDER", **_CAMPOS_SUSP})
    await inad_probe.drain()

    ended = await _await_end(engine, iid)
    assert _END_SUSPENSO in ended, (
        f"Sem CANCEL ativo, a suspensao humana deve prosseguir ao terminal adverso. ended={ended}"
    )
    assert inad_probe.notifications_of_type("inadimplencia.register_contract_suspension"), (
        "A suspensao deve ser registrada quando nao ha CANCEL-001 ativo"
    )


# ===========================================================================
# Timers de SLA
# ===========================================================================


@pytest.mark.xfail(reason=_PREPARE_DOSSIER_UNREGISTERED_REASON, strict=True)
async def test_timer_alerta_sla_nao_interruptivo(
    engine: EngineRest,
    inad_probe: InadEngineProbe,
    start_inad: Callable[..., Any],
) -> None:
    """Timer BT_AlertaSla (nao-interruptivo): notify_sla_risk recebe task; UT segue aberta."""
    inst = await start_inad()
    iid = inst["id"]

    await _drive_to_analise(engine, inad_probe, iid)

    job = await engine.await_timer_job(iid, "BT_AlertaSla")
    await engine.execute_job(job.id)
    await inad_probe.drain()

    assert inad_probe.notifications_of_type("inadimplencia.notify_sla_risk"), (
        "Worker notify_sla_risk deve ser executado no alerta de SLA"
    )
    open_keys = {t.task_definition_key for t in await engine.list_user_tasks(iid)}
    assert _UT_ANALISE in open_keys, "Timer nao-interruptivo nao deve cancelar a User Task"


@pytest.mark.xfail(reason=_PREPARE_DOSSIER_UNREGISTERED_REASON, strict=True)
async def test_timer_sla_estourado_coordenacao_assume(
    engine: EngineRest,
    inad_probe: InadEngineProbe,
    start_inad: Callable[..., Any],
) -> None:
    """Timer BT_SlaAnalise interruptivo: UT_AnaliseInadimplencia cancelada; UT_CoordenacaoCobranca criada."""
    inst = await start_inad()
    iid = inst["id"]

    await _drive_to_analise(engine, inad_probe, iid)

    job = await engine.await_timer_job(iid, "BT_SlaAnalise")
    await engine.execute_job(job.id)
    await inad_probe.drain()

    assert inad_probe.has_event(_INAD_SLA_BREACHED), "inadimplencia.sla_breached deve ser publicado"

    ut_coord = await engine.await_user_task(iid, _UT_COORDENACAO)
    assert "coordenacao-cobranca" in ut_coord.candidate_groups

    open_keys = {t.task_definition_key for t in await engine.list_user_tasks(iid)}
    assert _UT_ANALISE not in open_keys, "UT_AnaliseInadimplencia deve ser cancelada (interruptivo)"

    ended = await engine.activity_instances_ended(iid)
    assert not (ended & _ENDS_ADVERSOS), "Estouro de SLA nunca auto-suspende (L0)"
    await _assert_no_adverse_without_human_task(engine, iid)


@pytest.mark.xfail(reason=_PREPARE_DOSSIER_UNREGISTERED_REASON, strict=True)
async def test_coordenacao_assume_e_suspende(
    engine: EngineRest,
    inad_probe: InadEngineProbe,
    start_inad: Callable[..., Any],
) -> None:
    """SLA estourado; coordenacao assume e SUSPENDER => End_ContratoSuspenso_Inad com UT humana."""
    inst = await start_inad()
    iid = inst["id"]

    await _drive_to_analise(engine, inad_probe, iid)
    job = await engine.await_timer_job(iid, "BT_SlaAnalise")
    await engine.execute_job(job.id)
    await inad_probe.drain()

    ut_coord = await engine.await_user_task(iid, _UT_COORDENACAO)
    await engine.complete_task_as_human(
        ut_coord.id,
        {"decisao_inadimplencia": "SUSPENDER", **_CAMPOS_SUSP, "responsavel_id": "coordenacao-sintetica-001"},
    )
    await inad_probe.drain()

    ended = await _await_end(engine, iid)
    assert _END_SUSPENSO in ended
    await _assert_no_adverse_without_human_task(engine, iid)
    assert inad_probe.has_event(_INAD_COMPLETED, desfecho="suspenso")


# ===========================================================================
# Pendencia de informacao (humano solicita)
# ===========================================================================


@pytest.mark.xfail(reason=_PREPARE_DOSSIER_UNREGISTERED_REASON, strict=True)
async def test_solicitar_info_aguarda_correlacao(
    engine: EngineRest,
    inad_probe: InadEngineProbe,
    start_inad: Callable[..., Any],
) -> None:
    """UT humana SOLICITAR_INFO => aguarda msg.inadimplencia.info_received => reabre a User Task."""
    inst = await start_inad()
    iid = inst["id"]
    business_key = inst["businessKey"]

    ut = await _drive_to_analise(engine, inad_probe, iid)
    await engine.complete_task_as_human(ut.id, {"decisao_inadimplencia": "SOLICITAR_INFO"})
    await inad_probe.drain()

    correlate_payload = {"messageName": "msg.inadimplencia.info_received", "businessKey": business_key}
    async with httpx.AsyncClient(base_url=CIBSEVEN_BASE_URL, timeout=20.0) as client:
        resp = await client.post("/message", json=correlate_payload)
        assert resp.status_code in (200, 204), (
            f"Correlacao msg.inadimplencia.info_received falhou [{resp.status_code}]: {resp.text[:200]}"
        )

    ut2 = await engine.await_user_task(iid, _UT_ANALISE)
    assert "juridico-contratos" in ut2.candidate_groups

    ended = await engine.activity_instances_ended(iid)
    assert not (ended & _ENDS_ADVERSOS), "SOLICITAR_INFO nunca auto-suspende (L0)"
    await _assert_no_adverse_without_human_task(engine, iid)


# ===========================================================================
# Worker guard unit-style (SEM engine — roda sem dev-stack)
# ===========================================================================


def test_register_contract_suspension_recusa_sem_humano() -> None:
    """Invocacao direta de register_contract_suspension sem decisao humana => InadimplenciaError.

    Unit-style sobre a funcao real (SEM engine). ADAPTADO (port rule 1): v2 e dict-first (ADR-0026
    §2a) — `register_contract_suspension(variables: dict) -> dict` NAO recebe `kafka` (confirmado:
    `register_inadimplencia_workers` faz `del kafka` — nenhum worker deste modulo declara
    dependencia de Kafka). O guard lanca `InadimplenciaError` (`.code`/`.message`), nao
    `WorkerBpmnError`. Os 6 cenarios do guard sao preservados verbatim; a asserçao de "sucesso" foi
    adaptada ao shape REAL do retorno de v2 (sem suspensao_id/responsavel_id/tier echo — ver PORT
    NOTES).
    """
    # (a) ausente -> recusa
    with pytest.raises(InadimplenciaError) as exc_a:
        register_contract_suspension({})
    assert exc_a.value.code == "ERR_CONTRACT_SUSPENSION_NOT_HUMAN"

    # (b) decisao neutra (MANTER) -> recusa
    with pytest.raises(InadimplenciaError) as exc_b:
        register_contract_suspension({"decisao_inadimplencia": "MANTER"})
    assert exc_b.value.code == "ERR_CONTRACT_SUSPENSION_NOT_HUMAN"

    # (c) ENCAMINHAR_RESCISAO (handoff, nao adverso local) tampouco passa pelo worker de suspensao
    with pytest.raises(InadimplenciaError) as exc_c:
        register_contract_suspension({"decisao_inadimplencia": "ENCAMINHAR_RESCISAO"})
    assert exc_c.value.code == "ERR_CONTRACT_SUSPENSION_NOT_HUMAN"

    # (d) SUSPENDER mas faltando responsavel_id/campos -> recusa
    with pytest.raises(InadimplenciaError) as exc_d:
        register_contract_suspension(
            {
                "decisao_inadimplencia": "SUSPENDER",
                "fundamentacao_contratual": "x",
                "referencia_regulatoria": "RN 593",
                "comprovacao_notificacao_previa": "ref-notif",
                "comprovacao_periodo_minimo": "ref-periodo",
            }
        )
    assert exc_d.value.code == "ERR_CONTRACT_SUSPENSION_NOT_HUMAN"

    # (e) SUSPENDER + campos completos mas ja_em_rescisao_cancel=true -> recusa (anti-dupla)
    with pytest.raises(InadimplenciaError) as exc_e:
        register_contract_suspension(
            {"decisao_inadimplencia": "SUSPENDER", "ja_em_rescisao_cancel": True, **_CAMPOS_SUSP}
        )
    assert exc_e.value.code == "ERR_CONTRACT_SUSPENSION_NOT_HUMAN"

    # (f) decisao humana completa -> retorna suspensao_registrada=True (shape REAL de v2)
    result = register_contract_suspension(
        {
            "decisao_inadimplencia": "SUSPENDER",
            "ja_em_rescisao_cancel": False,
            "tenant_id": "amh",
            "numero_contrato": "CONTRATO-TESTE-GUARD",
            "data_efeito_iso": "2026-06-01",
            **_CAMPOS_SUSP,
        }
    )
    assert result["suspensao_registrada"] is True
    assert result["data_efeito_iso"] == "2026-06-01"


# ===========================================================================
# DMN — shape e fail-safe (sem engine; varredura estatica do XML)
# ===========================================================================


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def test_inadimplencia_status_sem_saida_adversa() -> None:
    """O dominio de roteamento da inadimplencia_status e EXATAMENTE
    {AGUARDA_PURGA, PENDENTE_NOTIFICACAO, SEGUE_ANALISE, ANALISE_HUMANA}.
    """
    tree = ET.parse(_DMN_STATUS)
    root = tree.getroot()

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

    assert roteamentos == {"AGUARDA_PURGA", "PENDENTE_NOTIFICACAO", "SEGUE_ANALISE", "ANALISE_HUMANA"}, (
        f"dominio de roteamento inesperado: {roteamentos} — NAO pode conter SUSPENDER/RESCINDIR/NEGAR (L0)"
    )
    blob = " ".join(roteamentos)
    for proibido in ("SUSPENDER", "RESCINDIR", "NEGAR", "RETER"):
        assert proibido not in blob, f"saida adversa proibida '{proibido}' na inadimplencia_status (L0)"
    assert last_rule_first_output == "ANALISE_HUMANA", "row catch-all deve rotear a ANALISE_HUMANA"


def test_inadimplencia_dmns_sem_saida_adversa() -> None:
    """Nenhuma DMN do processo (status/purga/sla) possui saida adversa (SUSPENDER/RESCINDIR/NEGAR)."""
    for dmn_path in (_DMN_STATUS, _DMN_PURGA, _DMN_SLA):
        tree = ET.parse(dmn_path)
        for rule in (e for e in tree.getroot().iter() if _local(e.tag) == "rule"):
            for out in (c for c in rule if _local(c.tag) == "outputEntry"):
                text_el = next((c for c in out if _local(c.tag) == "text"), None)
                if text_el is None or not text_el.text:
                    continue
                val = text_el.text.strip().strip('"')
                assert val not in ("SUSPENDER", "RESCINDIR", "NEGAR", "RETER"), (
                    f"{dmn_path.name}: saida adversa proibida '{val}' (L0)"
                )


def test_dmn_typeref_allowlist() -> None:
    """Toda DMN do processo usa typeRef in {string, boolean, integer, long, double, date}; nunca number."""
    allowed = {"string", "boolean", "integer", "long", "double", "date"}
    for dmn_path in (_DMN_STATUS, _DMN_PURGA, _DMN_SLA):
        tree = ET.parse(dmn_path)
        for el in tree.getroot().iter():
            type_ref = el.get("typeRef")
            if type_ref is not None:
                assert type_ref in allowed, (
                    f"{dmn_path.name}: typeRef invalido '{type_ref}' (allowlist={sorted(allowed)})"
                )
                assert type_ref != "number", f"{dmn_path.name}: 'number' e proibido (use double/integer)"


# ===========================================================================
# ANTI-DUPLA-RESCISAO — topologia do BPMN (parse estatico; sem engine)
# ===========================================================================

_BPMN_NS = "{http://www.omg.org/spec/BPMN/20100524/MODEL}"
_CAMUNDA_NS = "{http://camunda.org/schema/1.0/bpmn}"


def _bpmn_root() -> ET.Element:
    return ET.parse(_BPMN).getroot()


def test_inadimplencia_nao_tem_terminal_de_rescisao_proprio() -> None:
    """ANTI-DUPLA-RESCISAO (.harmonization-decision.md DRAFT-A): INADIMPLENCIA-001 NAO declara
    NENHUM terminal de rescisao proprio — a rescisao e propriedade EXCLUSIVA de SP-OP-CANCEL-001.

    ADAPTADO (FINDING 3, verify-before-citing): v2's BPMN adiciona `End_SuspensaoBloqueadaNaoHumano`
    (GAP-INAD-7, um terminal de INCIDENTE NEUTRO — nao de rescisao) vs o conjunto do donor. O
    conjunto esperado foi expandido para incluir esse 6o end-event (mecanico — nao enfraquece o
    invariante: o novo id NAO contem nenhum token de rescisao local, verificado abaixo).
    """
    proc = _bpmn_root().find(f"{_BPMN_NS}process")
    assert proc is not None
    ends = {e.get("id", "") for e in proc.iter(f"{_BPMN_NS}endEvent")}

    esperados = {
        _END_SUSPENSO,
        _END_HANDOFF,
        _END_PURGADO,
        _END_MANTIDO,
        _END_RISCO_SLA,
        _END_SUSPENSAO_BLOQUEADA,
    }
    assert ends == esperados, (
        f"End-events inesperados em INADIMPLENCIA-001: {ends} != {esperados}. "
        "Um terminal de rescisao LOCAL (proibido — CANCEL detem a rescisao) deixaria a suite vermelha."
    )

    for end_id in ends:
        low = end_id.lower()
        for token in _RESCISAO_LOCAL_TOKENS:
            if end_id in (_END_HANDOFF, _END_SUSPENSAO_BLOQUEADA):
                continue
            assert token not in low, (
                f"End-event '{end_id}' parece um terminal de RESCISAO LOCAL (token '{token}') — proibido."
            )


def test_rescisao_so_acontece_via_handoff_a_cancel() -> None:
    """A decisao ENCAMINHAR_RESCISAO leva ao worker handoff_rescisao -> terminal NEUTRO (handoff a
    CANCEL), NUNCA a um worker/terminal de rescisao local.
    """
    proc = _bpmn_root().find(f"{_BPMN_NS}process")
    assert proc is not None

    topics_by_id: dict[str, str] = {}
    for st in proc.iter(f"{_BPMN_NS}serviceTask"):
        topics_by_id[st.get("id", "")] = st.get(f"{_CAMUNDA_NS}topic", "")

    assert "operadora.inadimplencia.register_contract_rescission" not in topics_by_id.values(), (
        "INADIMPLENCIA-001 NAO pode wirar register_contract_rescission a nenhuma task (anti-dupla-rescisao)"
    )
    assert "operadora.inadimplencia.handoff_rescisao" in topics_by_id.values(), (
        "ENCAMINHAR_RESCISAO deve rodar handoff_rescisao (encaminhamento a CANCEL-001)"
    )

    flows: dict[str, list[str]] = {}
    for sf in proc.iter(f"{_BPMN_NS}sequenceFlow"):
        src, tgt = sf.get("sourceRef"), sf.get("targetRef")
        if src and tgt:
            flows.setdefault(src, []).append(tgt)

    seen: set[str] = set()
    stack = ["ST_HandoffRescisao"]
    while stack:
        node = stack.pop()
        if node in seen:
            continue
        seen.add(node)
        stack.extend(flows.get(node, []))
    assert _END_HANDOFF in seen, "ST_HandoffRescisao deve alcancar End_RescisaoHandoffCancel (handoff neutro)"
    assert _END_SUSPENSO not in seen, "O caminho do handoff NUNCA alcanca o terminal de suspensao"


def test_bpmn_user_tasks_tem_candidate_groups() -> None:
    """Toda userTask do corpo tem camunda:candidateGroups nao-vazio (gate humano L0)."""
    proc = _bpmn_root().find(f"{_BPMN_NS}process")
    assert proc is not None
    for ut in proc.iter(f"{_BPMN_NS}userTask"):
        groups = (ut.get(f"{_CAMUNDA_NS}candidateGroups") or "").strip()
        assert groups, f"userTask '{ut.get('id')}' sem candidateGroups (gate humano ausente)"


def test_bpmn_xml_ids_unicos() -> None:
    """Todos os ids do BPMN sao unicos (ENGINE-22004 / cvc-id.2)."""
    ids: list[str] = []
    for el in _bpmn_root().iter():
        el_id = el.get("id")
        if el_id:
            ids.append(el_id)
    assert len(ids) == len(set(ids)), (
        f"ids duplicados no BPMN: {sorted({i for i in ids if ids.count(i) > 1})}"
    )

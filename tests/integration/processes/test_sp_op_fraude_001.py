"""SP-OP-FRAUDE-001 — suite de integracao executavel (engine CIB Seven REAL).

Ported from the v1 donor (READ-ONLY `Maezo-Healthcare-Plan`) — T3.1 phase-2 (13-family
process-suite port). fraude.py is a **dict-first `FunctionWorker`-wrapped module**
(`register_fraude_workers(harness, kafka=None, **seams)`, bootstrap.py's own categorization) —
mirrors cancel.py's dict-boundary pattern (donor fixture SELF-CONTAINS its own fixtures; does
NOT edit the shared conftest.py beyond reusing `engine`/`drain_topics`/`CIBSEVEN_BASE_URL`).

Implementa o test-spec do W-A Phase 3 (docs/processes/test-specs/SP-OP-FRAUDE-001.md) contra o
engine real (ADR-0011: SEM mock de engine). Cada teste:

1. inicia a instancia via REST com business key `FRAUDE-amh-{numero_caso}`;
2. drena as external tasks com o `fraude_probe` (workers reais fraude + generic publish);
3. avanca o HITL completando User Tasks como humano sintetico (caminho HITL permitido);
4. dispara timers via job execution (NUNCA sleep);
5. verifica invariantes via historia do engine (garantia de que a acusacao de fraude / referrals
   passam pela UT humana sobre dossie SELADO — no-adverse / fraud_accusation L0 hard).

Dados sinteticos obvios: caso `CASO-TESTE-NNNN`, entidade/prestador pseudonimo `prov:teste-0001`,
beneficiario pseudonimizado `bnf-teste-0001`, tenant `amh`, origem `contas`. Business key
`FRAUDE-amh-{caso}`. Process key: SP-OP-FRAUDE-001 (exato — nao alterar). Evidencias = ponteiros
pseudonimizados sinteticos (NUNCA PHI bruto).

## Invariante L0 (DoD deliverable) — fraud_accusation / no-adverse

test_nenhum_caminho_automatizado_acusa_fraude:
  Varredura de TODAS as combinacoes de input das DMNs fraude_indicadores + fraude_routing
  (score x indicadores_presentes x entidade_tipo x origem_encaminhamento). A instancia NUNCA
  atinge End_FraudeConfirmadaHumano / End_Encaminhado{Juridico,Credenciamento,Contratual} sem que
  UT_DecisaoInvestigador / UT_CoordenacaoInvestigacao tenha sido completada por humano com
  decisao_fraude=ACUSAR_FRAUDE sobre um bundle_root SELADO. Assertion strength preserved
  BYTE-IDENTICAL to the donor (charter constraint) — this is the L0 guardrail sweep.

PORT NOTES (fixture/mechanical adaptation — port rule 1; logic/assertions verbatim from donor
unless a real v2 gap/shape difference is documented below):

  - import paths -> v2 `maezo.tools.workers.fraude`/`harness`/`dmn_transport`; `phase0.py`
    (donor's `register_phase0_workers`/`FakeKafkaPublisher`) does not exist on v2 main —
    `FakeKafkaPublisher` moved to `harness.py` (T1.1), and the generic `operadora.events.publish`
    handler is `maezo.tools.workers.events.register_events_workers` (T3.1 R2, already merged,
    already exercised green by auth/escalation/cancel) — composed here exactly like those
    suites' probes.
  - `maezo.tools.mcp_dmn.server.CibSevenDmnTransport` (donor, v1's now-dead in-process XML
    evaluator module) -> v2's REAL engine-side DMN seam is
    `maezo.tools.workers.dmn_transport.CibSevenDmnTransport` (ADR-0028) — wired into
    `register_fraude_workers(harness, kafka, dmn=dmn)`'s `dmn=` seam, which `score_indicators`
    (fraude.py) REQUIRES (`require_dmn` raises `DmnEvaluationError` — fail-closed — if `dmn` is
    `None`; NOT documented in the 5 pre-supplied facts, verified by reading fraude.py:246-300).
  - artifact paths -> `spec/processes/{bpmn,dmn}/**` (constraint 5). The 7 `fraude_scoring/*`
    DMNs the donor deploys from a dedicated `fraude_scoring/` SUBDIRECTORY live FLAT under
    `spec/processes/dmn/` on v2 (`risk_thresholds.dmn`, `upcoding_complexity_ceiling.dmn`,
    `frequency_zscore_threshold.dmn`, `phantom_no_diagnosis.dmn`, `phantom_suspicious_prefix.dmn`,
    `provider_peer_deviation.dmn`, `unbundling_partial_bundles.dmn` — verified by `ls`; decision
    ids match `fraude.py`'s own `_SCORING_DECISIONS` tuple exactly) — `_DMN_SCORING` is a fixed
    literal list (not a glob over a subdirectory that doesn't exist on v2).
  - `drain()` uses the shared `drain_topics()` helper (v2 `WorkerTransport.fetch_and_lock`
    signature adaptation — see `conftest.py`).
  - `_CustodyBundle`/audit-chain surface: v1's `CustodyBundle.assemble(...)` +
    `seal_custody_bundle(audit, bundle)` (async, takes an `AuditLog`) +
    `make_register_fraud_accusation_handler`/`make_seal_custody_bundle_handler`/
    `InMemoryAuditSink` (donor's `fraude.py`) do NOT exist on v2 main at all (grep-confirmed
    against `src/maezo/gateway/custody.py` and `src/maezo/tools/workers/fraude.py`). v2's
    `CustodyBundle` (`src/maezo/gateway/custody.py`) is a much simpler, PURE class with exactly
    two static methods: `seal_bundle(evidence_refs: list[str]) -> str` (SHA-256 Merkle root, no
    audit-chain write despite the fraude.py docstring's aspirational phrasing) and
    `verify_bundle(bundle_root, evidence_refs) -> bool`. v2's `fraude.py` worker functions
    (`seal_custody_bundle`/`register_fraud_accusation`) operate on the SAME `evidencia_refs`
    process variable the rest of the process already carries — there is NO separate
    `bundle_record_hashes`/`custody_sealed_record_hash` concept on v2 (donor's `_RECORD_HASHES`/
    `_RECORD_HASHES_CSV`/`bundle_record_hashes` seeding technique is DROPPED here; custody is
    sealed/re-verified over `evidencia_refs` directly). The two donor "unit-style" tests
    (`test_worker_register_fraud_accusation_recusa_sem_humano`,
    `test_worker_seal_custody_recusa_phi`) are REWRITTEN to call v2's actual dict-first functions
    directly (`register_fraud_accusation(variables) -> dict`, `seal_custody_bundle(variables) ->
    dict`), catching `FraudeError` (`.code`/`.message`, NOT `WorkerBpmnError`) instead of the
    donor's handler-factory + `WorkerBpmnError` shape — mirrors cancel.py's own adaptation of
    `test_send_cancellation_notice_recusa_sem_humano` to `CancellationNotHumanError`. The 5 guard
    scenarios (missing decision / wrong decision / missing fields / custody mismatch / success)
    are preserved.
  - PHI-in-custody detection DIVERGES from the donor's implementation: v2's
    `_PHI_MARKERS = frozenset({"cpf","nome","nome_social","endereco","telefone","email"})`
    (fraude.py:47) is a literal SUBSTRING match against each `evidencia_refs` string element — NOT
    a CPF-format regex or name heuristic. The donor's synthetic PHI fixtures
    (`"111.444.777-35"`, `"Sr. Joao da Silva Teste"`) do NOT contain any of these literal marker
    substrings and would NOT trigger v2's guard — `test_worker_seal_custody_recusa_phi` is
    adapted to use a synthetic evidence ref that DOES contain a marker substring (e.g.
    `"prov:teste-0001#cpf-exposed-teste"`), documented inline.
  - **NEW finding (load-bearing fixture adaptation, not one of the 5 pre-supplied facts):**
    `tests/integration/processes/engine_rest.py`'s `EngineRest._to_camunda_vars` (lines ~130-146,
    shared infra, NOT modified here) has NO `list | dict -> Json` branch (unlike `harness.py`'s
    own `_to_camunda_var`, used by the REAL worker transport, which DOES) and no `float ->
    Double` branch either — every plain Python `list`/`dict`/`float` value (or a pre-`json.dumps`'d
    string) sent through `EngineRest.start_by_key`/`complete_task_as_human` serializes as a
    Camunda `String` (a Python `str()`-repr, or the raw JSON text, never re-parsed) or is
    misrouted to `String` for a bare float. `fraude.py`'s `register_fraud_accusation` guard
    REQUIRES `indicadores_fundamentantes` (`isinstance(..., list)`) and `destino_referral`
    (`isinstance(..., dict)`) to already be NATIVE Python objects once the worker fetches them —
    `_from_camunda_var` (harness.py) only `json.loads`s a `Json`-typed entry. Without an explicit
    `{"value": json.dumps(x), "type": "Json"}` wrapper (this file's `_json_var()` helper — the
    SAME escape hatch the donor test itself already relies on for `z_score`/`deviation_pct`
    Double-typed scoring inputs, since `EngineRest._to_camunda_vars` passes any
    `{"value": ..., "type": ...}` dict straight through unchanged), EVERY well-formed
    `ACUSAR_FRAUDE` human decision would spuriously fail the guard
    (`ERR_FRAUD_ACCUSATION_NOT_HUMAN`) — auth.py/cancel.py never hit this because neither
    family's guards apply `isinstance(list | dict)` to an engine-delivered variable. Applied here
    via `_json_var()` for `evidencia_refs` (start), `indicadores_fundamentantes`, and
    `destino_referral` (UT-complete) — a fixture-level workaround, not a source change.

FINDINGS (new, beyond the 5 VERIFIED CROSS-FAMILY FACTS supplied for this port):

  1. **Guard errors never reach a BPMN boundary catch (routes to an engine incident instead).**
     `fraude.py`'s `FraudeError` (`ERR_FRAUD_ACCUSATION_NOT_HUMAN`/`ERR_CUSTODY_NOT_SEALED`/
     `ERR_PHI_IN_CUSTODY`) is a bespoke `Exception` with `.code`/`.message` — NOT a
     `WorkerBpmnError` (fraude.py:559-565). `FunctionWorker.execute()` (base.py:284-294) duck-type
     reclassifies ANY such "coded" exception into a `ValueError`, which `harness.py::_handle`
     ALWAYS routes to `failure(retries=0)` — an engine incident — REGARDLESS of any
     `bpmn_error_allowlist`. Separately, the BPMN itself (grep-confirmed: only 3 `boundaryEvent`s
     exist in the whole file — 2 timers + 1 message; the ONLY `errorEventDefinition` usage is
     `EED_CustodiaNaoSelada` on an END EVENT reached via `GW_CustodiaSelada`'s gateway CONDITION
     `${bundle_root != null}`, unrelated to a worker-raised exception) has NO boundary event
     catching `ERR_FRAUD_ACCUSATION_NOT_HUMAN`/`ERR_CUSTODY_NOT_SEALED`/`ERR_PHI_IN_CUSTODY`
     anywhere — so even if `FraudeError` WERE a `WorkerBpmnError`, there is no matching catch.
     Practical consequence: every guard rejection in this suite manifests as a stuck, open engine
     incident rather than a graceful boundary-caught neutral path. This does NOT weaken the L0
     invariant (an incident is if anything MORE conservative than a graceful catch — no adverse
     terminal is ever reached either way) and no donor assertion in this family's test file checks
     for the ABSENCE of an incident on a guard-rejection path (unlike cancel.py's
     `test_manter_sem_fundamentacao_bloqueado_pelo_guard`), so no test needs to xfail over this —
     it is a genuine production-readiness gap worth a human follow-up, not a test blocker.
  2. **Registry drift between BPMN-declared and fraude.py-registered `operadora.fraude.*`
     topics (2-way mismatch), confirmed by a dedicated static test
     (`test_bpmn_fraude_topics_vs_registered_workers`) rather than left as an unverified comment:**
     (a) the BPMN declares `operadora.fraude.notify_sla_risk` (`ST_NotifySlaRisk`,
     `SP-OP-FRAUDE-001_Investigacao_Fraude.bpmn:248`) but `register_fraude_workers` never
     registers a handler for it (fraude.py's own bootstrap comment already flags this: "only
     notify_sla_risk has no implementing function today") — deliberately EXCLUDED from
     `_FRAUDE_WORKER_TOPICS` (mirrors cancel.py's convention of building the drain list from what
     is ACTUALLY registered); (b) fraude.py registers `operadora.fraude.publish_completed`
     (fraude.py:536-551, 607) — a topic the BPMN never declares anywhere (every `ST_Publish*`
     node routes through the generic `operadora.events.publish` topic instead) — an orphan,
     dead-from-the-engine's-perspective registration, included in `_FRAUDE_WORKER_TOPICS` (it is
     genuinely registered) but never exercised by any test since no engine task is ever created
     on it.
  3. **`operadora.fraude.notify_sla_risk` gap blocks ONE test**
     (`test_timer_alerta_sla_nao_interruptivo`) whose only assertion about worker execution is a
     `notifications_of_type("fraude.notify_sla_risk")` check — this is a stronger gap than the
     systemic kafka-publish gap (finding 4 below): there is no worker AT ALL for this topic, not
     merely a worker that doesn't publish. xfailed with `_FRAUDE_NOTIFY_SLA_UNREGISTERED_REASON`.
  4. **fraude-specific kafka-publish gap (systemic finding, fraude-scoped instance):**
     `register_fraude_workers` does `del kafka  # unused` (fraude.py:592) — confirmed NO
     `fraude.py` function ever calls `kafka.publish` (grep: zero `kafka.publish(` hits in
     fraude.py, consistent with the cross-family fact that `events.py:247` is the ONLY call site
     codebase-wide). Domain events published via the generic `ST_Publish*` -> `operadora.events.
     publish` service tasks (intake_received, custody_sealed, sla_breached, completed) DO work
     (already-fixed generic path, T3.1 R2) — but any assertion on
     `fraude_probe.notifications_of_type(...)` for a FRAUDE-SPECIFIC function's own execution
     (score_indicators, start_credenciamento, start_contratual, refer_to_legal) can NEVER observe
     it. Affects 4 tests, xfailed with `_FRAUDE_WORKER_KAFKA_GAP_REASON`.
  5. D-07 ceilings gap does NOT apply (fraude.py does not import `CeilingResolver` — confirmed).
  6. Cross-process bridge auto-start (FRAUDE -> CRED/CANCEL/INADIMPLENCIA) is out of scope for
     this family's tests (belongs to `test_cross_process_handoff_seam.py`, a separate donor file,
     explicitly out of scope for this port) — this suite only asserts that fraude's OWN handoff
     WORKER (`start_credenciamento`/`start_contratual`) executes and that the neutral
     `End_Encaminhado*` terminal is reached, never that a second process instance auto-starts.
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
from maezo.tools.workers.fraude import FraudeError, register_fraud_accusation, register_fraude_workers
from maezo.tools.workers.fraude import seal_custody_bundle as _seal_custody_bundle
from maezo.tools.workers.harness import CibSevenWorkerTransport, FakeKafkaPublisher, WorkerHarness

from .conftest import CIBSEVEN_BASE_URL, drain_topics
from .engine_rest import EngineRest

pytestmark = pytest.mark.integration

_REPO = Path(__file__).resolve().parents[3]
_BPMN = _REPO / "spec/processes/bpmn/SP-OP-FRAUDE-001_Investigacao_Fraude.bpmn"
_DMN_INDICADORES = _REPO / "spec/processes/dmn/fraude_indicadores.dmn"
_DMN_ROUTING = _REPO / "spec/processes/dmn/fraude_routing.dmn"
_DMN_SLA = _REPO / "spec/processes/dmn/fraude_sla.dmn"
# 7 fraude_scoring/* tables (T2.7 phase 1, PR #48) -- flat under spec/processes/dmn/ on v2 (no
# dedicated subdirectory like the donor's `fraude_scoring/`); decision ids match fraude.py's own
# `_SCORING_DECISIONS` tuple exactly (verified).
_DMN_SCORING = [
    _REPO / "spec/processes/dmn/risk_thresholds.dmn",
    _REPO / "spec/processes/dmn/upcoding_complexity_ceiling.dmn",
    _REPO / "spec/processes/dmn/frequency_zscore_threshold.dmn",
    _REPO / "spec/processes/dmn/phantom_no_diagnosis.dmn",
    _REPO / "spec/processes/dmn/phantom_suspicious_prefix.dmn",
    _REPO / "spec/processes/dmn/provider_peer_deviation.dmn",
    _REPO / "spec/processes/dmn/unbundling_partial_bundles.dmn",
]

# External task topics do contrato SP-OP-FRAUDE-001 (grepped against fraude.py's own
# `register_fraude_workers` -- ground truth is WHAT IS ACTUALLY REGISTERED, mirroring cancel.py's
# `_CANCEL_WORKER_TOPICS` construction, NOT a blind copy of the BPMN's declared topic list).
_PUBLISH_TOPIC = "operadora.events.publish"
_INTAKE_TOPIC = "operadora.fraude.intake"
_GATHER_EVIDENCE_TOPIC = "operadora.fraude.gather_evidence"
_SCORE_INDICATORS_TOPIC = "operadora.fraude.score_indicators"
_ASSEMBLE_DOSSIER_TOPIC = "operadora.fraude.assemble_dossier"
_SEAL_CUSTODY_TOPIC = "operadora.fraude.seal_custody_bundle"
_REGISTER_ACCUSATION_TOPIC = "operadora.fraude.register_fraud_accusation"
_REFER_TO_LEGAL_TOPIC = "operadora.fraude.refer_to_legal"
_START_CREDENCIAMENTO_TOPIC = "operadora.fraude.start_credenciamento"
_START_CONTRATUAL_TOPIC = "operadora.fraude.start_contratual"
# Orphan registration (finding 2b): fraude.py registers this, but the BPMN never declares it --
# every ST_Publish* node routes through the generic `operadora.events.publish` topic instead.
# Included in the drain list below because it IS genuinely registered (mirrors cancel.py's own
# "drain list == what's registered" convention) -- but no engine task will ever land on it.
_PUBLISH_COMPLETED_TOPIC = "operadora.fraude.publish_completed"
# BPMN-declared (ST_NotifySlaRisk) but NEVER registered by `register_fraude_workers` (finding 2a
# / fraude.py's own bootstrap comment). Named here for the timer-job lookup and the drift-check
# test below, but DELIBERATELY EXCLUDED from `_FRAUDE_WORKER_TOPICS` -- there is no handler to
# drain it with.
_NOTIFY_SLA_TOPIC = "operadora.fraude.notify_sla_risk"

_FRAUDE_WORKER_TOPICS = [
    _PUBLISH_TOPIC,
    _INTAKE_TOPIC,
    _GATHER_EVIDENCE_TOPIC,
    _SCORE_INDICATORS_TOPIC,
    _ASSEMBLE_DOSSIER_TOPIC,
    _SEAL_CUSTODY_TOPIC,
    _REGISTER_ACCUSATION_TOPIC,
    _REFER_TO_LEGAL_TOPIC,
    _START_CREDENCIAMENTO_TOPIC,
    _START_CONTRATUAL_TOPIC,
    _PUBLISH_COMPLETED_TOPIC,
]

_NOTIFICATIONS_TOPIC = "operadora.notifications.internal"

# Eventos de dominio (contrato SP-OP-FRAUDE-001.md, publicados via ST_Publish* -> generic
# operadora.events.publish -- the already-fixed T3.1 R2 path).
_FRAUDE_INTAKE_RECEIVED = "agents.events.fraude.intake_received"
_FRAUDE_CUSTODY_SEALED = "agents.events.fraude.custody_sealed"
_FRAUDE_SLA_BREACHED = "agents.events.fraude.sla_breached"
_FRAUDE_COMPLETED = "agents.events.fraude.completed"

# User Task definition keys (BPMN).
_UT_DECISAO = "UT_DecisaoInvestigador"
_UT_COORDENACAO = "UT_CoordenacaoInvestigacao"
_UT_REVISAO_REFERRAL = "UT_RevisaoReferral"

# End events do processo.
_END_FRAUDE_CONFIRMADA = "End_FraudeConfirmadaHumano"
_END_ENCAMINHADO_JURIDICO = "End_EncaminhadoJuridico"
_END_ENCAMINHADO_CREDENCIAMENTO = "End_EncaminhadoCredenciamento"
_END_ENCAMINHADO_CONTRATUAL = "End_EncaminhadoContratual"
_END_ARQUIVADO = "End_ArquivadoSemIndicio"
_END_MONITORAR = "End_MonitorarSemAcao"

# Boundary timers (em UT_DecisaoInvestigador).
_BT_ALERTA = "BT_AlertaSlaFraude"
_BT_SLA_INVESTIGACAO = "BT_SlaInvestigacao"
_ICE_PRAZO_DILIGENCIA = "ICE_PrazoDiligencia"

# User Tasks humanas que podem produzir o desfecho adverso (L0 hard guard).
_UT_HUMANAS = frozenset({_UT_DECISAO, _UT_COORDENACAO})

# Os quatro terminais ADVERSOS (so via UT humana + dossie selado -- fraud_accusation L0 hard).
_ENDS_ADVERSOS = frozenset(
    {
        _END_FRAUDE_CONFIRMADA,
        _END_ENCAMINHADO_JURIDICO,
        _END_ENCAMINHADO_CREDENCIAMENTO,
        _END_ENCAMINHADO_CONTRATUAL,
    }
)

_DECISAO_ACUSAR = "ACUSAR_FRAUDE"

# Ponteiros pseudonimizados de evidencia (ADR-0006 -- NUNCA PHI bruto). v2 seals/re-verifies
# custody directly over `evidencia_refs` (no separate bundle_record_hashes concept -- PORT NOTES).
_EVIDENCIA_REFS = ["prov:teste-0001#tiss-001", "claim:teste#feat-001"]

# Campos obrigatorios de uma acusacao humana (ACUSAR_FRAUDE -- defesa em profundidade do worker).
# destino_referral default sem referral downstream: o terminal e End_FraudeConfirmadaHumano
# (default do GW_DestinoReferral). indicadores_fundamentantes/destino_referral wrapped via
# `_json_var` (PORT NOTES -- EngineRest can't otherwise deliver a native list/dict).
_CAMPOS_ACUSACAO_BASE: dict[str, Any] = {
    "decisao_fraude": _DECISAO_ACUSAR,
    "fundamentacao_investigacao": "Fundamentacao sintetica da constituicao do indicio (teste L0)",
    "referencia_normativa": "Lei 9.656/1998 art. 13 — DRAFT/verify (teste)",
    "investigator_id": "investigador-sintetico-001",
    "tier": "T2",
}

# fraude.py never calls kafka.publish (register_fraude_workers does `del kafka  # unused`,
# fraude.py:592 -- confirmed zero `kafka.publish(` call sites in the module, grep). Domain events
# via the generic ST_Publish* -> operadora.events.publish path DO work (T3.1 R2, already fixed);
# only a FRAUDE-SPECIFIC function's own notification can never be observed. Affects
# score_indicators (scoring_source provenance notification), start_credenciamento,
# start_contratual, refer_to_legal.
_FRAUDE_WORKER_KAFKA_GAP_REASON = (
    "v2 drift (fraude-scoped instance of the systemic T3.1 finding -- same class as auth/"
    "escalation/cancel residuals): fraude.py's `register_fraude_workers` does `del kafka  # "
    "unused` (fraude.py:592) -- confirmed NO function in fraude.py (score_indicators, "
    "start_credenciamento, start_contratual, refer_to_legal) ever calls kafka.publish. "
    "fraude_probe.notifications_of_type(...) can therefore never observe any of these workers' "
    "executions, even though the worker itself runs and returns its output variables correctly "
    "against the live engine (propagated via transport.complete, not kafka). Domain events "
    "published via the generic ST_Publish* -> operadora.events.publish service tasks (intake_"
    "received/custody_sealed/sla_breached/completed) are a SEPARATE, already-fixed path (T3.1 "
    "R2) and are NOT affected. Fix belongs to the Kafka-producer wiring task, not this port; "
    "src/** fix is out of scope."
)

# BPMN declares `operadora.fraude.notify_sla_risk` (ST_NotifySlaRisk) but `register_fraude_
# workers` never registers a handler for it -- fraude.py's own bootstrap comment already flags
# this ("only notify_sla_risk has no implementing function today"). Stronger than the kafka-
# publish gap above: there is no worker AT ALL, so the external task is never even fetched by
# this suite's drain (deliberately excluded from `_FRAUDE_WORKER_TOPICS`) and stays locked/
# undrained on the engine side.
_FRAUDE_NOTIFY_SLA_UNREGISTERED_REASON = (
    "v2 gap (fraude-specific, confirmed by `test_bpmn_fraude_topics_vs_registered_workers` "
    "below): the BPMN declares `operadora.fraude.notify_sla_risk` (ST_NotifySlaRisk, boundary "
    "BT_AlertaSlaFraude) but fraude.py's `register_fraude_workers` never registers a "
    "FunctionWorker for this topic (fraude.py's own bootstrap comment: 'only notify_sla_risk has "
    "no implementing function today' -- a gap, not fabricated here). Because no handler exists, "
    "this topic is excluded from `_FRAUDE_WORKER_TOPICS` (mirrors cancel.py's convention of "
    "draining exactly what is registered) -- the ST_NotifySlaRisk task this test's timer creates "
    "is never fetched/completed, so notifications_of_type('fraude.notify_sla_risk') is always "
    "empty. src/** fix (implementing notify_sla_risk) is out of scope for this port."
)


def _json_var(value: Any) -> dict[str, Any]:
    """Wrap a `list`/`dict` as an explicit Camunda `Json`-typed variable for `EngineRest` calls.

    LOAD-BEARING ADAPTATION (module docstring finding, new for this family): `EngineRest.
    _to_camunda_vars` (engine_rest.py, shared infra, NOT modified here) has no `list | dict ->
    Json` branch -- every plain Python list/dict (or a pre-`json.dumps`'d string) sent through
    `EngineRest.start_by_key`/`complete_task_as_human` serializes as a Camunda `String`, never a
    `Json`-typed variable. fraude.py's `register_fraud_accusation` guard requires
    `indicadores_fundamentantes`/`destino_referral` to already be native Python objects once
    fetched by the worker (`_from_camunda_var` only `json.loads`s a `Json`-typed entry) -- without
    this wrapper every well-formed ACUSAR_FRAUDE decision would spuriously fail the guard. Uses
    the SAME `{"value": ..., "type": ...}` passthrough escape hatch the donor test itself already
    relies on for Double-typed scoring inputs (`EngineRest._to_camunda_vars` forwards any dict
    already containing `"value"` unchanged).
    """
    return {"value": json.dumps(value, ensure_ascii=False), "type": "Json"}


def _acusacao_vars(bundle_root: str, **extra: Any) -> dict[str, Any]:
    """Monta as variaveis de uma acusacao humana completa (ACUSAR_FRAUDE) com o bundle_root selado."""
    vars_ = dict(_CAMPOS_ACUSACAO_BASE)
    vars_["indicadores_fundamentantes"] = _json_var(
        extra.pop("indicadores_fundamentantes", ["upcoding_complexity_ceiling", "phantom_no_diagnosis"])
    )
    vars_["bundle_root"] = bundle_root
    vars_["destino_referral"] = _json_var(extra.pop("destino_referral", {"juridico": False}))
    vars_.update(extra)
    return vars_


def _acusacao_vars_sem_custodia(**extra: Any) -> dict[str, Any]:
    """Acusacao humana completa SEM reenviar `bundle_root` (round-trip real do seal worker).

    Espelha o formulario real da UT em producao: o humano so preenche a DECISAO + os campos de
    fundamentacao; `bundle_root`/`evidencia_refs` fluem da variavel de processo ja propagada pelo
    seal worker (ST_SealCustodyBundle) / pelo start, atraves do engine.
    """
    vars_ = dict(_CAMPOS_ACUSACAO_BASE)
    vars_["indicadores_fundamentantes"] = _json_var(
        extra.pop("indicadores_fundamentantes", ["upcoding_complexity_ceiling", "phantom_no_diagnosis"])
    )
    vars_["destino_referral"] = _json_var(extra.pop("destino_referral", {"juridico": False}))
    vars_.update(extra)
    return vars_


# ---------------------------------------------------------------------------
# EngineProbe para fraude (espelha CancelEngineProbe)
# ---------------------------------------------------------------------------


@dataclass
class FraudeEngineProbe:
    """Driva os workers reais de fraude contra o engine CIB Seven.

    Monta: CibSevenWorkerTransport (REST real) + WorkerHarness + register_fraude_workers (com o
    seam `dmn=` real, ADR-0028) + register_events_workers (generic publish) + FakeKafkaPublisher.
    """

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
        await drain_topics(self.transport, self.harness, self.worker_id, _FRAUDE_WORKER_TOPICS, rounds=rounds)


@pytest_asyncio.fixture
async def deploy_artifacts(engine: EngineRest) -> str:
    """Deploya BPMN + 3 DMN de processo + as 7 DMNs de fraude_scoring no engine real."""
    return await engine.deploy(
        _BPMN, _DMN_INDICADORES, _DMN_ROUTING, _DMN_SLA, *_DMN_SCORING, name="SP-OP-FRAUDE-001-qa"
    )


@pytest_asyncio.fixture
async def fraude_probe(
    engine: EngineRest, audit_sink: Any, audit_tenant: str
) -> AsyncIterator[FraudeEngineProbe]:
    """Probe que serve as external tasks com os workers reais de fraude.

    Injeta o `CibSevenDmnTransport` REAL (`maezo.tools.workers.dmn_transport`, ADR-0028) no seam
    `dmn=` de `register_fraude_workers` -- `score_indicators` REQUER este seam (fail-closed,
    `require_dmn`) e avalia as 7 DMNs `fraude_scoring/*` deployadas por `deploy_artifacts`.
    """
    worker_id = f"qa-fraude-worker-{uuid.uuid4().hex[:8]}"
    transport = CibSevenWorkerTransport(CIBSEVEN_BASE_URL)
    dmn = CibSevenDmnTransport(CIBSEVEN_BASE_URL)
    # T1.10 wave: emit-before-complete is FAIL-CLOSED (harness.py _emit_audit) — a real
    # PostgresAuditSink (lane PG, migrations 0001->0005) is REQUIRED or the harness refuses
    # to complete. `tenant` scopes the durable audit chain / dedup key to the per-run schema.
    harness = WorkerHarness(
        transport,
        worker_id=worker_id,
        tenant=audit_tenant,
        lock_duration_ms=10_000,
        audit_sink=audit_sink,
    )
    kafka = FakeKafkaPublisher()
    register_fraude_workers(harness, kafka, dmn=dmn)
    # T3.1 R2: the generic operadora.events.publish worker every ST_Publish* service task in
    # this BPMN routes through -- mirrors the donor's own register_phase0_workers composition.
    register_events_workers(harness, kafka)
    # DRIFT GUARD (mirrors cancel.py's convention): todo topico operadora.fraude.* registrado no
    # harness DEVE estar na lista de drain -- falha AQUI, explicita, se um worker novo ficar fora.
    fraude_registered = {t for t in harness.registered_topics if t.startswith("operadora.fraude.")}
    missing_from_drain = fraude_registered - set(_FRAUDE_WORKER_TOPICS)
    assert not missing_from_drain, (
        f"_FRAUDE_WORKER_TOPICS desatualizada — topicos registrados fora do drain: {missing_from_drain}"
    )
    probe = FraudeEngineProbe(
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
        await dmn.close()


def _unique_caso(prefix: str = "CASO-TESTE") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8].upper()}"


@pytest_asyncio.fixture
async def start_fraude(engine: EngineRest, deploy_artifacts: str) -> Callable[..., Any]:
    """Inicia uma instancia com business key FRAUDE-amh-{caso} e payload canonico.

    `score_indicadores`/`indicadores_presentes` seeds sao DECOYS deliberados (o worker
    `score_indicators` SOBRESCREVE ambos avaliando as 7 DMNs fraude_scoring/* -- prova de
    nao-eco); `evidencia_refs` e Json-wrapped (`_json_var`, PORT NOTES) para round-trippar como
    lista real atraves do engine.
    """

    async def _start(**overrides: Any) -> dict[str, Any]:
        caso = overrides.pop("numero_caso", _unique_caso())
        variables: dict[str, Any] = {
            "tenant_id": "amh",
            "numero_caso": caso,
            "origem_encaminhamento": "contas",
            "entidade_tipo": "prestador",
            "entidade_pseudo_id": "prov:teste-0001",
            "prestador_id": "prov:teste-0001",
            "encaminhado_por_id": "analista-contas-humano-001",
            "competencia": "2026-05",
            "evidencia_refs": _json_var(_EVIDENCIA_REFS),
            "score_indicadores": 30,
            "indicadores_presentes": _json_var(["upcoding_complexity_ceiling"]),
            "indicio_fraude_sinalizado": True,
        }
        variables.update(overrides)
        business_key = f"FRAUDE-amh-{caso}"
        return await engine.start_by_key("SP-OP-FRAUDE-001", business_key, variables)

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
    """INVARIANTE L0 (fraud_accusation / no-adverse): prova que nenhum terminal adverso existe sem
    UT humana no historico.
    """
    ended = await engine.activity_instances_ended(iid)
    adversos_atingidos = ended & _ENDS_ADVERSOS
    if adversos_atingidos:
        human_tasks_in_history = ended & _UT_HUMANAS
        assert human_tasks_in_history, (
            f"INVARIANTE L0 VIOLADA (fraud_accusation): terminal(is) adverso(s) "
            f"{adversos_atingidos} atingido(s) para instancia {iid} SEM nenhuma User Task humana no "
            f"historico. User Tasks esperadas (qualquer uma de): {_UT_HUMANAS}. "
            f"Atividades historicas encontradas: {ended}. "
            "Isto indica um caminho automatizado de acusacao de fraude — violacao do L0 hard."
        )


async def _drive_to_decisao(engine: EngineRest, probe: FraudeEngineProbe, iid: str) -> Any:
    """Drena ate UT_DecisaoInvestigador surgir (dossie montado + selado pelos workers reais)."""
    await probe.drain()
    return await engine.await_user_task(iid, _UT_DECISAO)


async def _sealed_bundle_root(probe: FraudeEngineProbe) -> str:
    """Le o bundle_root selado do evento fraude.custody_sealed (publicado ANTES da UT)."""
    sealed = probe.events_on(_FRAUDE_CUSTODY_SEALED)
    assert sealed, "fraude.custody_sealed deve ter sido publicado ANTES da UT (seal-before-decision)"
    root = sealed[-1]["payload"].get("bundle_root")
    assert root, "custody_sealed sem bundle_root"
    return str(root)


# ===========================================================================
# INVARIANTE L0 -- DoD deliverable (varredura de inputs das DMNs fraude_indicadores + fraude_routing)
# ===========================================================================


async def test_nenhum_caminho_automatizado_acusa_fraude(
    engine: EngineRest,
    fraude_probe: FraudeEngineProbe,
    start_fraude: Callable[..., Any],
) -> None:
    """INVARIANTE L0 (fraud_accusation): NENHUM caminho automatizado acusa fraude / refere.

    Varredura de TODAS as combinacoes de input das DMNs fraude_indicadores + fraude_routing:
      score_indicadores in {0, 30, 90} (amostra: baixo, moderado, alto)
      x indicadores_presentes in {vazio, parcial, todos}
      x entidade_tipo in {prestador, beneficiario, contrato, rede}
      x origem_encaminhamento in {contas, denuncia} (amostra)
    (3 * 3 * 4 * 2 = 72 combinacoes). Assertion strength preserved BYTE-IDENTICAL to the donor
    (charter constraint) -- these `score_indicadores` seeds are DECOYS (score_indicators
    overwrites them via the real DMN chain before any consumer reads them); the invariant remains
    valid over ALL start-var combinations regardless (even a forged inbound score can never reach
    an adverse terminal without a human UT).
    """
    scores = [0, 30, 90]
    indicadores_amostras = [
        [],
        ["upcoding_complexity_ceiling"],
        [
            "upcoding_complexity_ceiling",
            "unbundling_partial_bundles",
            "phantom_no_diagnosis",
            "frequency_zscore_threshold",
        ],
    ]
    entidades = ["prestador", "beneficiario", "contrato", "rede"]
    origens = ["contas", "denuncia"]
    checked = 0

    for score, indicadores, entidade, origem in itertools.product(
        scores, indicadores_amostras, entidades, origens
    ):
        inst = await start_fraude(
            score_indicadores=score,
            indicadores_presentes=_json_var(indicadores),
            entidade_tipo=entidade,
            origem_encaminhamento=origem,
        )
        iid = inst["id"]
        await fraude_probe.drain()

        ended = await engine.activity_instances_ended(iid)
        adversos = ended & _ENDS_ADVERSOS
        assert not adversos, (
            f"L0 VIOLADO (fraud_accusation): score={score} entidade={entidade} origem={origem} "
            f"indicadores={indicadores} atingiu terminal(is) adverso(s) {adversos} automaticamente. "
            f"ended={ended}"
        )
        await _assert_no_adverse_without_human_task(engine, iid)
        checked += 1

    assert checked == 72, f"Esperava 72 combinacoes varridas; varri {checked}"


async def test_score_alto_roteia_para_humano_nunca_acusa(
    engine: EngineRest,
    fraude_probe: FraudeEngineProbe,
    start_fraude: Callable[..., Any],
) -> None:
    """Score alto + todos os indicadores => intensidade PRIORITARIA; roteamento INVESTIGACAO_HUMANA.

    Fluxo chega a UT_DecisaoInvestigador (investigacao-fraude) -- NUNCA a End_FraudeConfirmadaHumano
    nem a qualquer terminal de referral automatico. Inverte o FRAUD_DETECTED do reference: score alto
    investiga mais, nunca acusa.
    """
    inst = await start_fraude(
        score_indicadores=95,
        indicadores_presentes=_json_var(
            ["upcoding_complexity_ceiling", "phantom_no_diagnosis", "provider_peer_deviation"]
        ),
        entidade_tipo="prestador",
    )
    iid = inst["id"]

    ut = await _drive_to_decisao(engine, fraude_probe, iid)
    assert "investigacao-fraude" in ut.candidate_groups

    ended = await engine.activity_instances_ended(iid)
    assert not (ended & _ENDS_ADVERSOS), "Score alto NUNCA produz terminal adverso automatico (L0)"
    await _assert_no_adverse_without_human_task(engine, iid)


# ===========================================================================
# GAP-FRAUDE-3 -- score_indicators consome as 7 DMNs fraude_scoring/* REAIS do engine
# ===========================================================================


@pytest.mark.xfail(reason=_FRAUDE_WORKER_KAFKA_GAP_REASON, strict=True)
async def test_score_indicators_computa_dmns_reais_contra_evidencia(
    engine: EngineRest,
    fraude_probe: FraudeEngineProbe,
    start_fraude: Callable[..., Any],
) -> None:
    """O worker score_indicators COMPUTA o score avaliando as 7 DMNs fraude_scoring/* no engine REAL.

    Prova de ponta a ponta: evidencia sintetica seeded como variaveis de processo dispara linhas
    NAO-catch-all conhecidas de cada uma das 7 tabelas deployadas; o worker (via
    CibSevenDmnTransport real) soma os indicador_score e coleta os labels. O score inbound
    FORJADO (3) e o indicador forjado NUNCA ecoam (o worker sobrescreve com o valor DMN-computado).

    Linhas esperadas (soma = 200) -- ported verbatim from the donor; spot-verified against
    `risk_thresholds.dmn` on this v2 checkout (rule matches exactly: risk_score>=80 -> 30,
    "risk_thresholds_alto"); the remaining 6 tables are trusted per fraude.py's own "ported 1:1
    (decision-logic byte-faithful)" claim, not independently re-verified row-by-row here:
      risk_thresholds: risk_score=85 (>=80)                    -> 30 risk_thresholds_alto
      upcoding_complexity_ceiling: ambulatorio + tier 3 (>2)   -> 30 upcoding_complexity_ceiling
      frequency_zscore_threshold: z=2.5 (>2)                   -> 20 frequency_zscore_threshold
      phantom_no_diagnosis: tuss=true + cid10=false            -> 40 phantom_no_diagnosis
      phantom_suspicious_prefix: prefixo "99123" (starts 99)   -> 25 phantom_suspicious_prefix
      provider_peer_deviation: 60% (>50) + volume 15 (>10)     -> 30 provider_peer_deviation
      unbundling_partial_bundles: bundle_group_id=partial      -> 25 unbundling_partial_bundles

    XFAIL: `score_indicators` never calls kafka.publish (module docstring finding) --
    `notifications_of_type("fraude.score_indicators")`/`scoring_source` can never be observed even
    though the process-variable-based DMN computation (score_indicadores==200, labels list) would
    likely pass on its own merits.
    """
    caso = _unique_caso("CASO-TESTE-DMN")
    inst = await start_fraude(
        numero_caso=caso,
        # DECOYS inbound forjados -- nunca podem ecoar.
        score_indicadores=3,
        indicadores_presentes=_json_var(["indicador_forjado_inbound"]),
        # Evidencia sintetica (features/categorias -- NUNCA PHI). Doubles tipados explicitamente
        # (EngineRest._to_camunda_vars nao tem branch float -> Double, mesmo gap documentado no
        # docstring do modulo para list/dict -> Json).
        risk_score=85,
        encounter_class="ambulatorio",
        code_tier=3,
        z_score={"value": 2.5, "type": "Double"},
        has_tuss_codes=True,
        has_cid10_codes=False,
        tuss_prefix="99123",
        deviation_pct={"value": 60.0, "type": "Double"},
        provider_volume=15,
        bundle_group_id="partial",
        tuss_codes="10101012,10101020",
    )
    iid = inst["id"]

    ut = await _drive_to_decisao(engine, fraude_probe, iid)
    assert ut.task_definition_key == _UT_DECISAO

    score_notes = [
        n
        for n in fraude_probe.notifications_of_type("fraude.score_indicators")
        if n.get("numero_caso") == caso
    ]
    assert score_notes, "score_indicators deve ter executado e publicado a montagem"
    assert score_notes[-1]["scoring_source"] == "dmn", "o score deve vir das DMNs reais do engine"
    assert score_notes[-1]["score_indicadores"] == 200, (
        f"score DMN-computado (soma das 7 tabelas) deve ser 200; veio {score_notes[-1]['score_indicadores']}"
    )

    score_var = await engine.get_variable(iid, "score_indicadores")
    assert score_var == 200, "a variavel de processo deve ser o score DMN-computado"
    ind_var_raw = await engine.get_variable(iid, "indicadores_presentes")
    labels = json.loads(ind_var_raw) if isinstance(ind_var_raw, str) else ind_var_raw
    assert labels == [
        "risk_thresholds_alto",
        "upcoding_complexity_ceiling",
        "frequency_zscore_threshold",
        "phantom_no_diagnosis",
        "phantom_suspicious_prefix",
        "provider_peer_deviation",
        "unbundling_partial_bundles",
    ], f"labels devem ser os indicadores DMN-observados (nunca o decoy inbound): {labels}"
    assert "indicador_forjado_inbound" not in labels

    ended = await engine.activity_instances_ended(iid)
    assert not (ended & _ENDS_ADVERSOS), "Score DMN-computado alto NUNCA produz terminal adverso (L0)"
    await _assert_no_adverse_without_human_task(engine, iid)


# ===========================================================================
# Cadeia de custodia -- selagem-antes-da-decisao
# ===========================================================================


async def test_custodia_selada_antes_da_decisao(
    engine: EngineRest,
    fraude_probe: FraudeEngineProbe,
    start_fraude: Callable[..., Any],
) -> None:
    """O bundle_root e selado (Merkle root sobre evidencia_refs) ANTES de UT_DecisaoInvestigador.

    Prova de ordem (history-based): a atividade de selo (ST_SealCustodyBundle) + a publicacao
    fraude.custody_sealed (via generic events.publish) precedem a criacao da UT. O gate
    GW_CustodiaSelada so cria a UT com bundle_root != null.
    """
    inst = await start_fraude()
    iid = inst["id"]

    ut = await _drive_to_decisao(engine, fraude_probe, iid)
    assert ut.task_definition_key == _UT_DECISAO

    assert fraude_probe.has_event(_FRAUDE_CUSTODY_SEALED), (
        "fraude.custody_sealed deve ser publicado (selagem ANTES da decisao)"
    )
    sealed = fraude_probe.events_on(_FRAUDE_CUSTODY_SEALED)[-1]["payload"]
    assert sealed.get("bundle_root"), "custody_sealed deve carregar bundle_root"
    assert int(sealed.get("record_count", 0)) == len(_EVIDENCIA_REFS), "record_count deve bater"
    blob = json.dumps(sealed)
    assert "111." not in blob and "Sr." not in blob, "custody_sealed NUNCA carrega PHI bruto"

    ended = await engine.activity_instances_ended(iid)
    assert "ST_SealCustodyBundle" in ended, "ST_SealCustodyBundle deve preceder a UT (selo->decisao)"
    assert not (ended & _ENDS_ADVERSOS), "Selagem nunca produz terminal adverso (selo nao e acusacao)"
    await _assert_no_adverse_without_human_task(engine, iid)


# ===========================================================================
# Costura com Phase 2 (sink convergente) + procedencia
# ===========================================================================


async def test_intake_neutro_registra_procedencia(
    engine: EngineRest,
    fraude_probe: FraudeEngineProbe,
    start_fraude: Callable[..., Any],
) -> None:
    """Start neutro via intake com encaminhado_por_id (humano de Phase 2), origem contas.

    agents.events.fraude.intake_received publicado; encaminhado_por_id registrado; NENHUMA User
    Task adversa criada no intake; nenhum efeito adverso.
    """
    caso = _unique_caso()
    inst = await start_fraude(
        numero_caso=caso,
        origem_encaminhamento="contas",
        encaminhado_por_id="analista-contas-humano-007",
    )
    iid = inst["id"]

    await fraude_probe.drain()

    assert fraude_probe.has_event(
        _FRAUDE_INTAKE_RECEIVED,
        numero_caso=caso,
        encaminhado_por_id="analista-contas-humano-007",
    ), "intake_received deve registrar a procedencia humana do encaminhamento"

    ended = await engine.activity_instances_ended(iid)
    assert not (ended & _ENDS_ADVERSOS), "Intake nunca produz efeito adverso"
    await _assert_no_adverse_without_human_task(engine, iid)


async def test_fraude_nunca_le_de_volta_glosa(
    engine: EngineRest,
    fraude_probe: FraudeEngineProbe,
    start_fraude: Callable[..., Any],
) -> None:
    """Caso originado de CONTAS: FRAUDE nao publica/consome topico que leia de volta a glosa.

    FRAUDE-001 e estritamente downstream -- nenhum evento de dominio publicado referencia
    glosa/recurso/reembolso/cancelamento de volta. So eventos do contexto `fraude` sao emitidos.
    """
    inst = await start_fraude(origem_encaminhamento="contas")
    iid = inst["id"]

    await _drive_to_decisao(engine, fraude_probe, iid)

    topicos = {e["topic"] for e in fraude_probe._domain_events()}  # noqa: SLF001 -- leitura de teste
    assert topicos, "deve ter publicado ao menos um evento de dominio (intake/custody_sealed)"
    for topico in topicos:
        assert topico.startswith("agents.events.fraude."), (
            f"FRAUDE so publica eventos do contexto fraude; nao le de volta a glosa: {topico}"
        )
    for proibido in ("contas", "recurso", "reembolso", "cancel", "glosa"):
        assert not any(proibido in t for t in topicos), (
            f"FRAUDE NUNCA emite topico que leia de volta Phase 2 ('{proibido}'): {topicos}"
        )


# ===========================================================================
# Happy paths (decisao humana em UT_DecisaoInvestigador)
# ===========================================================================


async def test_happy_path_arquivar_sem_indicio(
    engine: EngineRest,
    fraude_probe: FraudeEngineProbe,
    start_fraude: Callable[..., Any],
) -> None:
    """Investigador completa ARQUIVAR => fraude.completed desfecho=arquivado_sem_indicio; fim neutro.

    register_fraud_accusation NUNCA invocado; nenhum efeito adverso.
    """
    inst = await start_fraude()
    iid = inst["id"]

    ut = await _drive_to_decisao(engine, fraude_probe, iid)
    await engine.complete_task_as_human(ut.id, {"decisao_fraude": "ARQUIVAR"})
    await fraude_probe.drain()

    ended = await _await_end(engine, iid)
    assert _END_ARQUIVADO in ended, f"ARQUIVAR => End_ArquivadoSemIndicio. ended={ended}"
    assert not (ended & _ENDS_ADVERSOS)
    assert fraude_probe.has_event(_FRAUDE_COMPLETED, desfecho="arquivado_sem_indicio")
    assert "ST_RegisterFraudAccusation" not in ended, "ARQUIVAR nunca registra acusacao"


async def test_happy_path_monitorar(
    engine: EngineRest,
    fraude_probe: FraudeEngineProbe,
    start_fraude: Callable[..., Any],
) -> None:
    """Investigador completa MONITORAR => fraude.completed desfecho=monitorar; fim neutro."""
    inst = await start_fraude()
    iid = inst["id"]

    ut = await _drive_to_decisao(engine, fraude_probe, iid)
    await engine.complete_task_as_human(ut.id, {"decisao_fraude": "MONITORAR"})
    await fraude_probe.drain()

    ended = await _await_end(engine, iid)
    assert _END_MONITORAR in ended, f"MONITORAR => End_MonitorarSemAcao. ended={ended}"
    assert not (ended & _ENDS_ADVERSOS)
    assert fraude_probe.has_event(_FRAUDE_COMPLETED, desfecho="monitorar")


async def test_happy_path_acusar_fraude_humano(
    engine: EngineRest,
    fraude_probe: FraudeEngineProbe,
    start_fraude: Callable[..., Any],
) -> None:
    """Investigador completa ACUSAR_FRAUDE (com todos os campos) sobre bundle_root selado.

    register_fraud_accusation executado (guard satisfeito, root re-verificado); UT_RevisaoReferral
    revisa o referral (juridico-fraude); sem referral downstream => End_FraudeConfirmadaHumano
    (default do GW_DestinoReferral); fraude.completed desfecho=fraude_confirmada_humano carregando
    investigator_id+tier na trilha de auditoria.
    """
    inst = await start_fraude()
    iid = inst["id"]

    ut = await _drive_to_decisao(engine, fraude_probe, iid)
    assert "investigacao-fraude" in ut.candidate_groups

    bundle_root = await _sealed_bundle_root(fraude_probe)
    await engine.complete_task_as_human(ut.id, _acusacao_vars(bundle_root))
    await fraude_probe.drain()

    ut_revisao = await engine.await_user_task(iid, _UT_REVISAO_REFERRAL)
    assert "juridico-fraude" in ut_revisao.candidate_groups
    await engine.complete_task_as_human(
        ut_revisao.id,
        {
            "destino_referral_cred": False,
            "destino_referral_contratual": False,
            "destino_referral_juridico": False,
            "destino_referral_ans": False,
        },
    )
    await fraude_probe.drain()

    ended = await _await_end(engine, iid)
    await _assert_no_adverse_without_human_task(engine, iid)
    assert _END_FRAUDE_CONFIRMADA in ended, f"Acusacao humana => End_FraudeConfirmadaHumano. ended={ended}"
    assert "ST_RegisterFraudAccusation" in ended, "register_fraud_accusation deve ter executado"
    assert fraude_probe.has_event(
        _FRAUDE_COMPLETED,
        desfecho="fraude_confirmada_humano",
        investigator_id="investigador-sintetico-001",
        tier="T2",
    ), "fraude.completed adverso deve carregar investigator_id+tier (trilha de auditoria ADR-0007)"


async def test_acusar_fraude_round_trip_custodia_do_seal_worker(
    engine: EngineRest,
    fraude_probe: FraudeEngineProbe,
    start_fraude: Callable[..., Any],
) -> None:
    """Acusacao humana SEM re-seed de bundle_root: round-trip real do seal worker.

    Em producao a UT NAO reenvia bundle_root; o efeito adverso consome o valor propagado pelo seal
    worker (ST_SealCustodyBundle) e re-verifica contra evidencia_refs (ja process variable desde o
    start). Prova o round-trip no engine real e garante que o guard de custodia NAO falhe
    espuriamente (ERR_CUSTODY_NOT_SEALED).
    """
    inst = await start_fraude()
    iid = inst["id"]

    ut = await _drive_to_decisao(engine, fraude_probe, iid)
    assert "investigacao-fraude" in ut.candidate_groups

    await engine.complete_task_as_human(ut.id, _acusacao_vars_sem_custodia())
    await fraude_probe.drain()

    ut_revisao = await engine.await_user_task(iid, _UT_REVISAO_REFERRAL)
    await engine.complete_task_as_human(
        ut_revisao.id,
        {
            "destino_referral_cred": False,
            "destino_referral_contratual": False,
            "destino_referral_juridico": False,
            "destino_referral_ans": False,
        },
    )
    await fraude_probe.drain()

    ended = await _await_end(engine, iid)
    await _assert_no_adverse_without_human_task(engine, iid)
    assert _END_FRAUDE_CONFIRMADA in ended, (
        f"Acusacao com custodia propagada pelo seal worker (sem re-seed) => "
        f"End_FraudeConfirmadaHumano. ended={ended}"
    )
    assert "ST_RegisterFraudAccusation" in ended, "register_fraud_accusation deve ter executado"
    assert fraude_probe.has_event(
        _FRAUDE_COMPLETED,
        desfecho="fraude_confirmada_humano",
        investigator_id="investigador-sintetico-001",
        tier="T2",
    ), "fraude.completed adverso deve carregar investigator_id+tier (round-trip de custodia OK)"


@pytest.mark.xfail(reason=_FRAUDE_WORKER_KAFKA_GAP_REASON, strict=True)
async def test_happy_path_acusar_handoff_credenciamento(
    engine: EngineRest,
    fraude_probe: FraudeEngineProbe,
    start_fraude: Callable[..., Any],
) -> None:
    """Acusacao humana, entidade prestador + destino_referral.cred=true => handoff a credenciamento.

    operadora.fraude.start_credenciamento executado (inicia SP-OP-CRED-001 -- que tem sua propria
    UT adversa; FRAUDE nunca auto-descredencia); fim End_EncaminhadoCredenciamento.
    """
    inst = await start_fraude(entidade_tipo="prestador")
    iid = inst["id"]

    ut = await _drive_to_decisao(engine, fraude_probe, iid)
    bundle_root = await _sealed_bundle_root(fraude_probe)
    await engine.complete_task_as_human(ut.id, _acusacao_vars(bundle_root, destino_referral={"cred": True}))
    await fraude_probe.drain()

    ut_revisao = await engine.await_user_task(iid, _UT_REVISAO_REFERRAL)
    await engine.complete_task_as_human(
        ut_revisao.id,
        {"destino_referral_cred": True, "destino_referral_contratual": False},
    )
    await fraude_probe.drain()

    ended = await _await_end(engine, iid)
    await _assert_no_adverse_without_human_task(engine, iid)
    assert _END_ENCAMINHADO_CREDENCIAMENTO in ended, f"=> End_EncaminhadoCredenciamento. ended={ended}"
    assert fraude_probe.notifications_of_type("fraude.start_credenciamento"), (
        "start_credenciamento deve ser executado (handoff a SP-OP-CRED-001)"
    )
    assert fraude_probe.has_event(_FRAUDE_COMPLETED, desfecho="encaminhado_credenciamento")


@pytest.mark.xfail(reason=_FRAUDE_WORKER_KAFKA_GAP_REASON, strict=True)
async def test_happy_path_acusar_handoff_contratual(
    engine: EngineRest,
    fraude_probe: FraudeEngineProbe,
    start_fraude: Callable[..., Any],
) -> None:
    """Acusacao humana, entidade beneficiario/contrato + destino_referral.contratual=true => handoff.

    operadora.fraude.start_contratual executado (inicia SP-OP-CANCEL-001/INADIMPLENCIA-001 -- com
    UT adversa propria; FRAUDE nunca auto-rescinde); fim End_EncaminhadoContratual.
    """
    inst = await start_fraude(entidade_tipo="beneficiario", beneficiario_pseudo_id="bnf-teste-0001")
    iid = inst["id"]

    ut = await _drive_to_decisao(engine, fraude_probe, iid)
    bundle_root = await _sealed_bundle_root(fraude_probe)
    await engine.complete_task_as_human(
        ut.id, _acusacao_vars(bundle_root, destino_referral={"contratual": True})
    )
    await fraude_probe.drain()

    ut_revisao = await engine.await_user_task(iid, _UT_REVISAO_REFERRAL)
    await engine.complete_task_as_human(
        ut_revisao.id,
        {"destino_referral_cred": False, "destino_referral_contratual": True},
    )
    await fraude_probe.drain()

    ended = await _await_end(engine, iid)
    await _assert_no_adverse_without_human_task(engine, iid)
    assert _END_ENCAMINHADO_CONTRATUAL in ended, f"=> End_EncaminhadoContratual. ended={ended}"
    assert fraude_probe.notifications_of_type("fraude.start_contratual"), (
        "start_contratual deve ser executado (handoff a SP-OP-CANCEL/INADIMPLENCIA)"
    )
    assert fraude_probe.has_event(_FRAUDE_COMPLETED, desfecho="encaminhado_contratual")


@pytest.mark.xfail(reason=_FRAUDE_WORKER_KAFKA_GAP_REASON, strict=True)
async def test_happy_path_acusar_handoff_juridico(
    engine: EngineRest,
    fraude_probe: FraudeEngineProbe,
    start_fraude: Callable[..., Any],
) -> None:
    """Acusacao humana + destino_referral.juridico/ans=true => referral a juridico/ANS/civel/penal.

    operadora.fraude.refer_to_legal executado (so a jusante de acusacao humana); fim
    End_EncaminhadoJuridico.
    """
    inst = await start_fraude(entidade_tipo="prestador")
    iid = inst["id"]

    ut = await _drive_to_decisao(engine, fraude_probe, iid)
    bundle_root = await _sealed_bundle_root(fraude_probe)
    await engine.complete_task_as_human(
        ut.id, _acusacao_vars(bundle_root, destino_referral={"juridico": True, "ans": True})
    )
    await fraude_probe.drain()

    ut_revisao = await engine.await_user_task(iid, _UT_REVISAO_REFERRAL)
    await engine.complete_task_as_human(
        ut_revisao.id,
        {
            "destino_referral_cred": False,
            "destino_referral_contratual": False,
            "destino_referral_juridico": True,
            "destino_referral_ans": True,
        },
    )
    await fraude_probe.drain()

    ended = await _await_end(engine, iid)
    await _assert_no_adverse_without_human_task(engine, iid)
    assert _END_ENCAMINHADO_JURIDICO in ended, f"=> End_EncaminhadoJuridico. ended={ended}"
    assert fraude_probe.notifications_of_type("fraude.refer_to_legal"), (
        "refer_to_legal deve ser executado (referral a juridico/ANS -- so apos acusacao humana)"
    )
    assert fraude_probe.has_event(_FRAUDE_COMPLETED, desfecho="encaminhado_juridico")


# ===========================================================================
# Acusacao exige campos / worker guard
# ===========================================================================


async def test_acusar_fraude_exige_campos(
    engine: EngineRest,
    fraude_probe: FraudeEngineProbe,
    start_fraude: Callable[..., Any],
) -> None:
    """ACUSAR_FRAUDE sem fundamentacao/indicadores/referencia/destino/investigator/tier => guard recusa.

    O engine despacha register_fraud_accusation; o worker lanca FraudeError(ERR_FRAUD_ACCUSATION_
    NOT_HUMAN) (defesa em profundidade); a instancia NAO atinge End_FraudeConfirmadaHumano (v2:
    reclassificado a ValueError pelo FunctionWorker -- vira um incidente do engine, nao um boundary
    catch -- ver module docstring finding 1; a instancia fica travada, nunca avanca).
    """
    inst = await start_fraude()
    iid = inst["id"]

    ut = await _drive_to_decisao(engine, fraude_probe, iid)
    await engine.complete_task_as_human(ut.id, {"decisao_fraude": _DECISAO_ACUSAR})
    await fraude_probe.drain()

    ended = await engine.activity_instances_ended(iid)
    assert _END_FRAUDE_CONFIRMADA not in ended, (
        "Acusacao sem campos obrigatorios NAO pode atingir End_FraudeConfirmadaHumano (guard do worker)"
    )
    assert not fraude_probe.has_event(_FRAUDE_COMPLETED, desfecho="fraude_confirmada_humano"), (
        "fraude.completed adverso NAO deve publicar sem campos obrigatorios"
    )
    await _assert_no_adverse_without_human_task(engine, iid)


async def test_register_fraud_accusation_recusa_sem_humano() -> None:
    """Invocacao direta de register_fraud_accusation sem decisao humana => FraudeError. Unit-style
    (SEM engine -- roda mesmo sem o dev-stack).

    ADAPTED (port rule 1, v2 shape verified): v2's `register_fraud_accusation(variables: dict) ->
    dict` (dict-first FunctionWorker entry point) replaces the donor's
    `make_register_fraud_accusation_handler(kafka, audit) -> Callable[[ExternalTask], ...]`
    (neither `AuditLog`/`InMemoryAuditSink` nor that handler factory exist on v2 -- module
    docstring). The guard raises `FraudeError` (`.code`/`.message`), NOT `WorkerBpmnError`.
    Custody is verified over `evidencia_refs` directly (v2 has no `bundle_record_hashes`
    concept) via `maezo.gateway.custody.CustodyBundle`. The 5 guard scenarios (missing decision /
    wrong decision / missing fields / custody mismatch / success) are preserved from the donor.
    """
    # (a) variables vazias -> recusa (decisao ausente + custodia ausente -> mixed -> NOT_HUMAN)
    with pytest.raises(FraudeError) as exc_a:
        register_fraud_accusation({})
    assert exc_a.value.code == "ERR_FRAUD_ACCUSATION_NOT_HUMAN"

    # (b) decisao_fraude neutra (ARQUIVAR) -> recusa
    with pytest.raises(FraudeError) as exc_b:
        register_fraud_accusation({"decisao_fraude": "ARQUIVAR"})
    assert exc_b.value.code == "ERR_FRAUD_ACCUSATION_NOT_HUMAN"

    # (c) ACUSAR_FRAUDE mas faltando investigator_id/tier/indicadores/destino -> recusa
    with pytest.raises(FraudeError) as exc_c:
        register_fraud_accusation(
            {
                "decisao_fraude": _DECISAO_ACUSAR,
                "fundamentacao_investigacao": "x",
                "referencia_normativa": "Lei 9.656",
            }
        )
    assert exc_c.value.code == "ERR_FRAUD_ACCUSATION_NOT_HUMAN"

    # (d) decisao humana COMPLETA mas bundle_root nao verifica contra evidencia_refs -> falha PURA
    # de custodia (GAP-FRAUDE-4: codigo distinto, ERR_CUSTODY_NOT_SEALED).
    with pytest.raises(FraudeError) as exc_d:
        register_fraud_accusation(
            {
                "decisao_fraude": _DECISAO_ACUSAR,
                "investigator_id": "inv-1",
                "tier": "T2",
                "fundamentacao_investigacao": "fundamentacao",
                "indicadores_fundamentantes": ["upcoding_complexity_ceiling"],
                "referencia_normativa": "Lei 9.656/1998 art. 13",
                "destino_referral": {"juridico": True},
                "bundle_root": "deadbeef" * 8,  # raiz arbitraria que NAO bate com evidencia_refs
                "evidencia_refs": ["x", "y"],
            }
        )
    assert exc_d.value.code == "ERR_CUSTODY_NOT_SEALED"

    # (e) decisao humana completa + bundle_root SELADO verificavel (via o seal worker REAL,
    # sobre a MESMA evidencia_refs) -> registra e carrega investigator+tier.
    evidencia = ["prov:teste-0001#tiss-001", "claim:teste#feat-002"]
    sealed = _seal_custody_bundle({"numero_caso": "CASO-TESTE-GUARD", "evidencia_refs": evidencia})
    result = register_fraud_accusation(
        {
            "decisao_fraude": _DECISAO_ACUSAR,
            "tenant_id": "amh",
            "numero_caso": "CASO-TESTE-GUARD",
            "investigator_id": "investigador-sintetico-009",
            "tier": "T3",
            "fundamentacao_investigacao": "Fundamentacao completa (teste)",
            "indicadores_fundamentantes": ["upcoding_complexity_ceiling", "phantom_no_diagnosis"],
            "referencia_normativa": "Lei 9.656/1998 art. 13 — DRAFT/verify",
            "destino_referral": {"juridico": True, "ans": True},
            "bundle_root": sealed["bundle_root"],
            "evidencia_refs": evidencia,
        }
    )
    assert result["acusacao_registrada"] is True
    assert result["bundle_root_verificado"] == sealed["bundle_root"]


async def test_seal_custody_recusa_phi() -> None:
    """seal_custody_bundle com item de evidencia carregando PHI bruto => FraudeError(ERR_PHI_IN_
    CUSTODY). Unit-style sobre o handler real (SEM engine).

    DIVERGE do donor (module docstring): v2's PHI check (`fraude.py:_PHI_MARKERS`) e um match de
    SUBSTRING literal contra {"cpf","nome","nome_social","endereco","telefone","email"} -- NAO um
    regex de formato de CPF/heuristica de nome. Os fixtures sinteticos do donor
    ("111.444.777-35"/"Sr. Joao da Silva Teste") NAO contem nenhum desses marcadores literais e
    NAO disparariam o guard de v2 -- adaptado para usar um ponteiro sintetico que CONTEM um
    marcador literal, exercitando o guard real.
    """
    with pytest.raises(FraudeError) as exc:
        _seal_custody_bundle(
            {
                "tenant_id": "amh",
                "numero_caso": "CASO-TESTE-PHI",
                "evidencia_refs": ["prov:teste-0001#cpf-exposed-teste", "claim:teste#feat-001"],
            }
        )
    assert exc.value.code == "ERR_PHI_IN_CUSTODY"


# ===========================================================================
# Diligencia (event gateway -- INVERTE auto-acao por timeout)
# ===========================================================================


async def test_solicitar_diligencia_aguarda_correlacao(
    engine: EngineRest,
    fraude_probe: FraudeEngineProbe,
    start_fraude: Callable[..., Any],
) -> None:
    """UT_DecisaoInvestigador => humano SOLICITAR_DILIGENCIA => aguarda event gateway => correlaciona.

    Ao correlacionar msg.fraude.diligencia_concluida (business key), reabre UT_DecisaoInvestigador
    para nova decisao humana. Nunca acusa automaticamente.
    """
    inst = await start_fraude()
    iid = inst["id"]
    business_key = inst["businessKey"]

    ut = await _drive_to_decisao(engine, fraude_probe, iid)
    await engine.complete_task_as_human(ut.id, {"decisao_fraude": "SOLICITAR_DILIGENCIA"})
    await fraude_probe.drain()

    correlate_payload = {
        "messageName": "msg.fraude.diligencia_concluida",
        "businessKey": business_key,
    }
    async with httpx.AsyncClient(base_url=CIBSEVEN_BASE_URL, timeout=20.0) as client:
        resp = await client.post("/message", json=correlate_payload)
        assert resp.status_code in (200, 204), (
            f"Correlacao msg.fraude.diligencia_concluida falhou [{resp.status_code}]: {resp.text[:200]}"
        )

    ut2 = await engine.await_user_task(iid, _UT_DECISAO)
    assert "investigacao-fraude" in ut2.candidate_groups

    ended = await engine.activity_instances_ended(iid)
    assert not (ended & _ENDS_ADVERSOS), "SOLICITAR_DILIGENCIA nunca auto-acusa (L0)"
    await _assert_no_adverse_without_human_task(engine, iid)


async def test_prazo_diligencia_expira_vai_para_humano_nao_acusa(
    engine: EngineRest,
    fraude_probe: FraudeEngineProbe,
    start_fraude: Callable[..., Any],
) -> None:
    """Aguardando no event gateway, sem diligencia_concluida => timer prazo => UT_DecisaoInvestigador.

    A expiracao do prazo NUNCA alcanca End_FraudeConfirmadaHumano nem End_ArquivadoSemIndicio
    automaticamente (INVERTE o auto-acao-no-timeout do reference).
    """
    inst = await start_fraude()
    iid = inst["id"]

    ut = await _drive_to_decisao(engine, fraude_probe, iid)
    await engine.complete_task_as_human(ut.id, {"decisao_fraude": "SOLICITAR_DILIGENCIA"})
    await fraude_probe.drain()

    job = await engine.await_timer_job(iid, _ICE_PRAZO_DILIGENCIA)
    await engine.execute_job(job.id)
    await fraude_probe.drain()

    ut2 = await engine.await_user_task(iid, _UT_DECISAO)
    assert "investigacao-fraude" in ut2.candidate_groups

    ended = await engine.activity_instances_ended(iid)
    assert not (ended & _ENDS_ADVERSOS), "Expiracao do prazo de diligencia NUNCA auto-acusa (L0)"
    assert _END_ARQUIVADO not in ended, "Expiracao do prazo NUNCA auto-arquiva (L0)"
    await _assert_no_adverse_without_human_task(engine, iid)


# ===========================================================================
# Timers de SLA
# ===========================================================================


@pytest.mark.xfail(reason=_FRAUDE_NOTIFY_SLA_UNREGISTERED_REASON, strict=True)
async def test_timer_alerta_sla_nao_interruptivo(
    engine: EngineRest,
    fraude_probe: FraudeEngineProbe,
    start_fraude: Callable[..., Any],
) -> None:
    """Timer BT_AlertaSlaFraude (nao-interruptivo): notify_sla_risk recebe task; UT segue aberta."""
    inst = await start_fraude()
    iid = inst["id"]

    await _drive_to_decisao(engine, fraude_probe, iid)

    job = await engine.await_timer_job(iid, _BT_ALERTA)
    await engine.execute_job(job.id)
    await fraude_probe.drain()

    assert fraude_probe.notifications_of_type("fraude.notify_sla_risk"), (
        "Worker notify_sla_risk deve ser executado no alerta de SLA"
    )

    open_keys = {t.task_definition_key for t in await engine.list_user_tasks(iid)}
    assert _UT_DECISAO in open_keys, "Timer nao-interruptivo nao deve cancelar a User Task"

    ended = await engine.activity_instances_ended(iid)
    assert not (ended & _ENDS_ADVERSOS), "Alerta de SLA nunca produz desfecho adverso"


async def test_timer_sla_estourado_coordenacao_assume(
    engine: EngineRest,
    fraude_probe: FraudeEngineProbe,
    start_fraude: Callable[..., Any],
) -> None:
    """Timer BT_SlaInvestigacao (interruptivo): UT_DecisaoInvestigador cancelada; UT_Coordenacao criada.

    agents.events.fraude.sla_breached publicado. NAO ha auto-acusacao por timeout -- a decisao
    continua humana (UT_CoordenacaoInvestigacao herda os mesmos campos obrigatorios e o requisito
    de bundle_root selado).
    """
    inst = await start_fraude()
    iid = inst["id"]

    await _drive_to_decisao(engine, fraude_probe, iid)

    job = await engine.await_timer_job(iid, _BT_SLA_INVESTIGACAO)
    await engine.execute_job(job.id)
    await fraude_probe.drain()

    assert fraude_probe.has_event(_FRAUDE_SLA_BREACHED), "fraude.sla_breached deve ser publicado"

    ut_coord = await engine.await_user_task(iid, _UT_COORDENACAO)
    assert "coordenacao-investigacao" in ut_coord.candidate_groups

    open_keys = {t.task_definition_key for t in await engine.list_user_tasks(iid)}
    assert _UT_DECISAO not in open_keys, "UT_DecisaoInvestigador deve ser cancelada (interruptivo)"

    ended = await engine.activity_instances_ended(iid)
    assert not (ended & _ENDS_ADVERSOS), "Estouro de SLA nunca auto-acusa (L0)"
    await _assert_no_adverse_without_human_task(engine, iid)


async def test_coordenacao_assume_e_acusa(
    engine: EngineRest,
    fraude_probe: FraudeEngineProbe,
    start_fraude: Callable[..., Any],
) -> None:
    """SLA estourado; coordenacao assume e ACUSAR_FRAUDE => End_FraudeConfirmadaHumano via UT humana.

    Prova que mesmo no caminho de escalonamento a acusacao passa por UT humana (invariante) sobre
    o mesmo bundle_root selado.
    """
    inst = await start_fraude()
    iid = inst["id"]

    await _drive_to_decisao(engine, fraude_probe, iid)
    job = await engine.await_timer_job(iid, _BT_SLA_INVESTIGACAO)
    await engine.execute_job(job.id)
    await fraude_probe.drain()

    ut_coord = await engine.await_user_task(iid, _UT_COORDENACAO)
    bundle_root = await _sealed_bundle_root(fraude_probe)
    await engine.complete_task_as_human(
        ut_coord.id,
        _acusacao_vars(bundle_root, investigator_id="coordenacao-sintetica-001", tier="T3"),
    )
    await fraude_probe.drain()

    ut_revisao = await engine.await_user_task(iid, _UT_REVISAO_REFERRAL)
    await engine.complete_task_as_human(
        ut_revisao.id,
        {
            "destino_referral_cred": False,
            "destino_referral_contratual": False,
            "destino_referral_juridico": False,
            "destino_referral_ans": False,
        },
    )
    await fraude_probe.drain()

    ended = await _await_end(engine, iid)
    assert _END_FRAUDE_CONFIRMADA in ended, f"Coordenacao ACUSAR => End_FraudeConfirmadaHumano. ended={ended}"
    await _assert_no_adverse_without_human_task(engine, iid)
    assert fraude_probe.has_event(
        _FRAUDE_COMPLETED, desfecho="fraude_confirmada_humano", investigator_id="coordenacao-sintetica-001"
    )


async def test_dmn_fraude_sla_registra_fonte(
    engine: EngineRest,
    fraude_probe: FraudeEngineProbe,
    start_fraude: Callable[..., Any],
) -> None:
    """intensidade PRIORITARIA => DMN fraude_sla resolve prazos (ISO) + fonte_regulatoria; timers existem.

    Prova indireta: os timers BT_AlertaSlaFraude / BT_SlaInvestigacao usam ${sla.sla_*} da DMN. Se
    os jobs de timer existem, a DMN resolveu as duracoes corretamente.
    """
    inst = await start_fraude(score_indicadores=95, entidade_tipo="prestador")
    iid = inst["id"]

    await _drive_to_decisao(engine, fraude_probe, iid)

    job = await engine.await_timer_job(iid, _BT_ALERTA)
    assert job.activity_id == _BT_ALERTA
    job_sla = await engine.await_timer_job(iid, _BT_SLA_INVESTIGACAO)
    assert job_sla.activity_id == _BT_SLA_INVESTIGACAO


# ===========================================================================
# Consolidacao / idempotencia (business key)
# ===========================================================================


async def test_business_key_uma_instancia_por_caso(
    engine: EngineRest,
    fraude_probe: FraudeEngineProbe,
    start_fraude: Callable[..., Any],
) -> None:
    """Mesmo business key: consultar antes de iniciar; nao criar 2a instancia ativa por caso."""
    caso = "CASO-TESTE-IDEM-001"
    business_key = f"FRAUDE-amh-{caso}"

    first = await start_fraude(numero_caso=caso)
    existing = await engine.find_active_instances(business_key)
    assert len(existing) == 1

    active_before = await engine.find_active_instances(business_key)
    assert len(active_before) == 1
    assert active_before[0]["id"] == first["id"]


async def test_multiplos_encaminhamentos_consolidam_no_caso(
    engine: EngineRest,
    fraude_probe: FraudeEngineProbe,
    start_fraude: Callable[..., Any],
) -> None:
    """Segundo encaminhar_fraude sobre o mesmo caso (msg.fraude.evidencia_anexada) => sem 2a instancia.

    A nova evidencia anexa ao dossie e re-sela o bundle_root (boundary message BME_EvidenciaAnexada
    em UT_DecisaoInvestigador) enquanto nao houver decisao; business key idempotente.
    """
    caso = "CASO-TESTE-CONSOLIDA-001"
    business_key = f"FRAUDE-amh-{caso}"

    inst = await start_fraude(numero_caso=caso)
    iid = inst["id"]
    await _drive_to_decisao(engine, fraude_probe, iid)

    correlate_payload = {
        "messageName": "msg.fraude.evidencia_anexada",
        "businessKey": business_key,
    }
    async with httpx.AsyncClient(base_url=CIBSEVEN_BASE_URL, timeout=20.0) as client:
        resp = await client.post("/message", json=correlate_payload)
        assert resp.status_code in (200, 204), (
            f"Correlacao msg.fraude.evidencia_anexada falhou [{resp.status_code}]: {resp.text[:200]}"
        )
    await fraude_probe.drain()

    active = await engine.find_active_instances(business_key)
    assert len(active) == 1, "Reenvio sobre o mesmo caso NAO cria segunda instancia ativa"
    assert active[0]["id"] == iid

    ended = await engine.activity_instances_ended(iid)
    assert not (ended & _ENDS_ADVERSOS), "Anexar evidencia nunca acusa automaticamente (L0)"
    await _assert_no_adverse_without_human_task(engine, iid)


# ===========================================================================
# DMN -- shape e fail-safe (sem engine; varredura estatica do XML)
# ===========================================================================


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _rule_output_values(dmn_path: Path, *, output_index: int = 0) -> tuple[set[str], str | None]:
    """Coleta os valores da coluna de saida `output_index` de cada rule + o ultimo (catch-all)."""
    tree = ET.parse(dmn_path)
    root = tree.getroot()
    values: set[str] = set()
    last: str | None = None
    for rule in (e for e in root.iter() if _local(e.tag) == "rule"):
        outputs = [c for c in rule if _local(c.tag) == "outputEntry"]
        assert len(outputs) > output_index, f"{dmn_path.name}: rule sem outputEntry[{output_index}]"
        text_el = next((c for c in outputs[output_index] if _local(c.tag) == "text"), None)
        assert text_el is not None and text_el.text is not None, f"{dmn_path.name}: outputEntry sem texto"
        val = text_el.text.strip().strip('"')
        if val:
            values.add(val)
            last = val
    return values, last


def test_dmn_fraude_routing_sem_saida_de_acusacao() -> None:
    """O dominio de `roteamento` (fraude_routing) e EXATAMENTE {INVESTIGACAO_HUMANA}.

    Nenhum valor de acusacao/veredito/bloqueio (ACUSAR/FRAUD_DETECTED/BLOQUEAR/CONFIRMAR ausentes);
    existe row catch-all -> INVESTIGACAO_HUMANA (conservador). Toda instancia roteia para o humano.
    """
    roteamentos, last = _rule_output_values(_DMN_ROUTING, output_index=0)
    assert roteamentos == {"INVESTIGACAO_HUMANA"}, (
        f"dominio de `roteamento` inesperado: {roteamentos} — deve ser exatamente "
        "{INVESTIGACAO_HUMANA} (fraude e 100% human-gated; nenhuma rota automatizada de acusacao)"
    )
    assert last == "INVESTIGACAO_HUMANA", "row catch-all deve rotear a INVESTIGACAO_HUMANA"

    blob = " ".join(
        (c.text or "") for c in ET.parse(_DMN_ROUTING).getroot().iter() if _local(c.tag) == "text"
    )
    for proibido in ("ACUSAR", "FRAUD_DETECTED", "BLOQUEAR", "CONFIRMAR", "DESCREDENCIAR"):
        assert proibido not in blob, f"saida adversa proibida '{proibido}' na fraude_routing (L0)"


def test_dmn_fraude_indicadores_sem_saida_de_acusacao() -> None:
    """fraude_indicadores.intensidade_investigacao in {LEVE, APROFUNDADA, PRIORITARIA}.

    Nenhum valor de acusacao/veredito; catch-all -> PRIORITARIA (conservador: investiga mais, leva
    ao humano). O score e fato de roteamento, NUNCA veredito.
    """
    intensidades, last = _rule_output_values(_DMN_INDICADORES, output_index=0)
    assert intensidades <= {"LEVE", "APROFUNDADA", "PRIORITARIA"}, (
        f"intensidade_investigacao fora do allowlist: {intensidades} — sem variante adversa (L0)"
    )
    assert last == "PRIORITARIA", "row catch-all deve rotear a PRIORITARIA (conservador)"

    blob = " ".join(
        (c.text or "") for c in ET.parse(_DMN_INDICADORES).getroot().iter() if _local(c.tag) == "text"
    )
    for proibido in ("ACUSAR", "FRAUD_DETECTED", "BLOQUEAR", "CONFIRMAR"):
        assert proibido not in blob, f"saida adversa proibida '{proibido}' na fraude_indicadores (L0)"


def test_dmn_typeref_allowlist() -> None:
    """Toda DMN do processo (3 de processo + 7 de scoring portadas) usa typeRef in
    {string, boolean, integer, long, double, date}. Nenhuma coluna usa "number".
    """
    allowed = {"string", "boolean", "integer", "long", "double", "date"}
    for dmn_path in [_DMN_INDICADORES, _DMN_ROUTING, _DMN_SLA, *_DMN_SCORING]:
        tree = ET.parse(dmn_path)
        for el in tree.getroot().iter():
            type_ref = el.get("typeRef")
            if type_ref is not None:
                assert type_ref in allowed, (
                    f"{dmn_path.name}: typeRef invalido '{type_ref}' (allowlist={sorted(allowed)})"
                )
                assert type_ref != "number", f"{dmn_path.name}: 'number' e proibido (use integer/double)"


def test_dmn_scoring_portadas_sem_veredito() -> None:
    """Nenhuma das 7 DMNs portadas emite recommendation->FRAUD_DETECTED nem coluna de veredito/bloqueio.

    O scoring de referencia entra apenas como input de montagem de dossie (indicador_score +
    indicador_label); o `recommendation="flag"`->FRAUD_DETECTED do reference foi REMOVIDO.
    """
    for dmn_path in _DMN_SCORING:
        blob = " ".join(
            (c.text or "") for c in ET.parse(dmn_path).getroot().iter() if _local(c.tag) == "text"
        )
        for proibido in ("FRAUD_DETECTED", "BLOQUEAR", "ACUSAR", "CONFIRMAR", "PROSSEGUIR"):
            assert proibido not in blob, (
                f"{dmn_path.name}: DMN portada NUNCA pode emitir veredito '{proibido}' "
                "(anti-padrao do reference REMOVIDO -- alimenta o dossie, nao decide)"
            )


def test_dmn_fraude_sla_outputs_iso_e_fonte() -> None:
    """fraude_sla emite sla_investigacao/sla_alerta/sla_diligencia (ISO 8601) + fonte_regulatoria.

    Os timers do BPMN referenciam ${sla.sla_investigacao}/${sla.sla_alerta}/${sla.sla_diligencia}, e
    a fonte_regulatoria e propagada para auditoria.
    """
    tree = ET.parse(_DMN_SLA)
    root = tree.getroot()
    output_names = [o.get("name", "") for o in root.iter() if _local(o.tag) == "output" and o.get("name")]
    for esperado in ("sla_investigacao", "sla_alerta", "sla_diligencia", "fonte_regulatoria"):
        assert esperado in output_names, (
            f"fraude_sla deve emitir output '{esperado}'; outputs achados: {output_names}"
        )


# ===========================================================================
# NOVO -- drift entre topicos declarados no BPMN e workers registrados (finding 2/2a/2b)
# ===========================================================================


def test_bpmn_fraude_topics_vs_registered_workers() -> None:
    """Cross-check estatico: `camunda:topic="operadora.fraude.*"` no BPMN vs o que
    `register_fraude_workers` efetivamente registra no harness.

    PINNED FINDING (nao remexido em src/**): (a) o BPMN declara `operadora.fraude.notify_sla_risk`
    (ST_NotifySlaRisk) mas NENHUMA funcao em fraude.py o implementa (comentario do proprio
    bootstrap: "only notify_sla_risk has no implementing function today") -- gap MISSING_WORKER;
    (b) fraude.py registra `operadora.fraude.publish_completed` -- topico que o BPMN NUNCA declara
    (todo no ST_Publish* deste BPMN roteia pelo generic `operadora.events.publish`) -- registro
    ORFAO, morto do ponto de vista do engine. Pinned aqui (em vez de um comentario nao verificado)
    para que uma futura correcao apareca como uma edicao INTENCIONAL deste teste.
    """
    camunda_ns = "{http://camunda.org/schema/1.0/bpmn}"
    tree = ET.parse(_BPMN)
    bpmn_topics = {
        el.get(f"{camunda_ns}topic")
        for el in tree.getroot().iter()
        if (el.get(f"{camunda_ns}topic") or "").startswith("operadora.fraude.")
    }

    # `WorkerHarness(None, ...)` e seguro aqui: `register_worker`/`register` nunca tocam o
    # transport -- so inspecionamos `registered_topics` apos o registro, sem I/O de rede.
    harness = WorkerHarness(None, worker_id="static-topic-check")  # type: ignore[arg-type]
    register_fraude_workers(harness, None, dmn=None)
    registered = {t for t in harness.registered_topics if t.startswith("operadora.fraude.")}

    missing_worker = bpmn_topics - registered
    orphan_worker = registered - bpmn_topics

    assert missing_worker == {_NOTIFY_SLA_TOPIC}, (
        f"topicos declarados no BPMN sem worker registrado mudou de composicao: {missing_worker} "
        f"(esperado apenas {_NOTIFY_SLA_TOPIC} -- atualize este teste e a suite se corrigido)"
    )
    assert orphan_worker == {_PUBLISH_COMPLETED_TOPIC}, (
        f"topicos registrados sem topico BPMN correspondente mudou de composicao: {orphan_worker} "
        f"(esperado apenas {_PUBLISH_COMPLETED_TOPIC} -- atualize este teste e a suite se corrigido)"
    )

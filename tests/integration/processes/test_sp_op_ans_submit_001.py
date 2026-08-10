"""SP-OP-ANS-SUBMIT-001 — suite de integracao executavel (engine CIB Seven REAL).

Ported from the v1 donor (READ-ONLY `Maezo-Healthcare-Plan`,
`tests/integration/processes/test_sp_op_ans_submit_001.py`, 1557 lines) — T3.1 phase 2
(13-family process-suite port). The donor file covers TWO distinct process families in one
module: SP-OP-ANS-SUBMIT-001 proper (lines ~1-1397) and a "GAP-ANS-1 seam cron" section
(lines ~1400-1557, `SP-OP-ANS-CRON-001 -> fato ans.cron_due -> bridge -> SP-OP-ANS-SUBMIT-001`).
This file ports ONLY the SUBMIT-001 half; the CRON seam lives in the sibling
`test_sp_op_ans_cron_001.py` (own module docstring cross-references back here) — the split
mirrors the v2 family boundary (`ans_submit.py` vs `ans_cron.py`, two distinct worker modules).

Implementa o test-spec do W5 (docs/processes/test-specs/SP-OP-ANS-SUBMIT-001.md) contra o engine
real (ADR-0011: SEM mock de engine). Cada teste:

1. inicia a instancia via REST com business key `ANSSUB-amh-{report_type}-{competencia}`;
2. drena as external tasks com o `ans_probe` (workers reais + generic publish);
3. avanca o HITL completando User Tasks como humano sintetico (caminho HITL permitido);
4. dispara timers via job execution (NUNCA sleep);
5. verifica invariantes via historia do engine (garantia de que o filing passa pela UT humana).

Dados sinteticos obvios: tenant `amh`, `report_type=RN_124_SIP`, `competencia=2026-01`, dataset
`DATASET-TESTE-0001`. Business key `ANSSUB-amh-RN_124_SIP-2026-01`.
Process key: SP-OP-ANS-SUBMIT-001 (exato — nao alterar).

## NAO negativa-like

Este processo nao tem padrao no-denial — nao ha decisao adversa, logo nao ha invariante de negativa
por DMN. A salvaguarda equivalente, testada como PRIORITARIA, e o HITL PRE-FILING
(`test_submit_exige_user_task_humana`): NENHUM caminho automatizado transmite a ANS. A prova e
feita consultando a historia do engine: o submit/terminal de envio so co-ocorre com a User Task de
aprovacao concluida por humano.

PORT NOTES (fixture adaptation only — port rule 1; logic/assertions verbatim from donor unless a
FINDING below documents a genuine v2 behavioral gap):
  - import paths -> v2 `maezo.tools.workers.ans_submit`/`events`/`harness`; the shared `engine`
    fixture + `drain_topics()` helper come from `.conftest` (v2 convention established by the
    already-merged `test_sp_op_auth_001.py`/`test_sp_op_cancel_001.py` templates) — UNLIKE the
    donor, which self-contains `engine`/`_engine_available` locally. `deploy_artifacts` /
    `AnsEngineProbe` / `start_ans` stay self-contained (family-specific probe construction, per
    `conftest.py`'s own port-rule-3 note).
  - artifact paths -> `spec/processes/{bpmn,dmn}/**` (constraint 5). SUBMIT's BPMN references ALL
    FOUR DMNs under `spec/processes/dmn/`: `ans_calendar` (BRT_Calendario), `ans_sla`
    (BRT_AnsSla), `ans_submission_admissibility` (BRT_Admissibilidade), `ans_retry_policy`
    (BRT_RetryPolicy, inside `SUB_RetryEnvio`) — verified by
    `grep -o 'camunda:decisionRef="[^"]*"' spec/processes/bpmn/SP-OP-ANS-SUBMIT-001_*.bpmn`,
    all 4 present; `deploy_artifacts` deploys all 4 alongside the BPMN.
  - v1's `register_phase0_workers`/`FakeKafkaPublisher` (from v1's `phase0.py`) -> v2's
    `maezo.tools.workers.events.register_events_workers` + `maezo.tools.workers.harness
    .FakeKafkaPublisher` (T1.1/T3.1 R2 spine).
  - v1's `make_submit_handler(kafka) -> Callable[[ExternalTask], ...]` / `make_assemble_handler`
    factories do NOT exist on v2 main (grep confirms zero hits) — v2 replaced the class/factory-
    per-topic shape for this module with ADR-0026 SS2b's typed dict-boundary entry functions
    (`submit_entry`/`assemble_entry(variables: dict, *, kafka=None) -> dict`). The 2 donor
    unit-style tests that construct a handler directly are adapted to call the entry functions
    with a plain dict (mirrors `test_sp_op_cancel_001.py`'s adaptation of
    `send_cancellation_notice_entry`); `test_err_ans_submit_not_human_recusa_sem_aprovacao`'s guard
    genuinely still works end-to-end (port rule 1 verified) and is kept as a REAL, unmarked test —
    `AnsSubmitNotHumanError` (a `PermissionError` subclass) replaces the donor's
    `WorkerBpmnError(ERR_ANS_SUBMIT_NOT_HUMAN)` (guard shape changed the SAME way cancel.py's
    `CancellationNotHumanError` did: v2 routes `PermissionError` guards straight to an engine
    incident, `harness.py`'s `_handle`, never through the `bpmn_error_allowlist`).
    `test_assemble_dataset_incompleto_roteia_a_humano` does NOT survive adaptation this way — see
    FINDING D below (the guard it exercises does not exist in v2 at all).
  - `ans_probe`'s harness wires `dmn=CibSevenDmnTransport(CIBSEVEN_BASE_URL)` into
    `register_ans_submit_workers` (the ONLY entry function that evaluates a DMN table directly,
    `retransmit_entry` -> `ans_retry_policy`, ADR-0028 T1.5 cutover) — LIVE since t9-nack-vars
    (FINDING B resolved: the retry subprocess this seam feeds is reachable and exercised by the two
    NACK tests).
  - `bpmn_error_allowlist=ANS_SUBMIT_BPMN_ERROR_ALLOWLIST` — the codes this family raises as
    MODELED bpmn errors, each with a matching boundary catch in SUBMIT's BPMN (`BE_SubmitNack` /
    `BE_AssembleDatasetIncompleto`); SAME precedent as the auth/cancel probes' allowlists. LIVE:
    `transmit_to_ans` raises `ERR_ANS_PROTOCOLO_NACK` on a gateway refusal (and carries
    `protocolo_ans`/`status_envio` through the harness's allowlisted `WorkerBpmnError.variables`
    channel). `ERR_ANS_RETRY_ESGOTADO` is deliberately NOT in it — the MODEL throws that one
    (`End_RetryEsgotado`), never a worker.

FINDINGS (grep-confirmed on this v2 main; see PR body / evidence-ledger for full detail). Two are
genuinely NEW (not previously documented in the auth/cancel/escalation port ledger) and are the
dominant reason most of this suite's tests are blocked:

  FINDING A (NEW — registration gap, blocks the MAJORITY of this file's tests): the BPMN topic
  `regulatorio.anssubmit.notify_regulatorio` is declared by TWO service tasks —
  `ST_PrepararDossie` (spec/processes/bpmn/SP-OP-ANS-SUBMIT-001_Envios_Periodicos_ANS.bpmn, ~line
  258-263, "Preparar dossie de envio (agente Gustavo)") and `ST_NotificarDeadlineRisk` (~line
  294-306, shared by ALL THREE deadline-risk boundary timers: `BT_DeadlineRisk` on
  `UT_RevisarEnvio`, `BT_DeadlineRiskPendencia` on `UT_CorrigirPendenciaEnvio`,
  `BT_DeadlineRiskJuridico` on `UT_RevisarEnvioJuridico`). `ans_submit.py`'s
  `register_ans_submit_workers` (lines ~444-487) registers EXACTLY 6 topics — assemble, validate,
  submit, track_protocol, retransmit, publish_completed — NOT notify_regulatorio. The module's own
  docstring (lines ~344-356) documents this as a known, undisguised gap ("Spec topic with NO
  implementing function today (gap, not fabricated here): notify_regulatorio"). This is a genuine
  v1->v2 REGRESSION, not a fixture-serving choice like auth's `_AnalyzeRequestStub`: the v1 donor
  itself registers a REAL worker for this topic (donor file's own dataclass docstring: "sem stub —
  todos os topicos teem worker real"; donor's `test_happy_path_envio_aprovado_e_acked` asserts
  `ans_probe.notifications_of_type("anssubmit.notify_regulatorio")` is non-empty).
  Consequence: `harness.py`'s `_handle` reports a task on an unregistered topic as an immediate
  incident (`"no handler registered for topic %r"`, `retries_override=0`) — the external task
  itself never completes, so the process instance stalls BEFORE `ST_PrepararDossie` /
  `ST_NotificarDeadlineRisk` ever advance. `_ANS_SUBMIT_WORKER_TOPICS` below still LISTS
  `_NOTIFY_TOPIC` (mirrors the donor's 1:1 topic/worker expectation and the BPMN's own
  declaration) so any task landing there surfaces as a LOUD incident rather than silently
  starving un-polled forever (`harness.py` design: "a task with no handler must not vanish").
  Blocks EVERY test whose flow reaches `ST_PrepararDossie` (the DEFAULT `Flow_GW_Revisao` branch
  out of `GW_Admissibilidade` — i.e. any `SEGUE_ENVIO`/`REVISAO_HUMANA` routing, NOT `PENDENTE` or
  `nip_filing`, which bypass it) or fires ANY of the three shared deadline-risk boundary timers.
  Does NOT block: the `nip_filing` route (`Flow_GW_NipFiling` goes DIRECTLY to
  `UT_RevisarEnvioJuridico`, skipping `ST_PrepararDossie`) nor the two INTERRUPTIVE due-date
  boundary timers (`BT_DueDateJuridico`/`BT_DueDatePendencia` route DIRECTLY to
  `UT_CoordenacaoEnvioAssume`, never through `ST_NotificarDeadlineRisk`) — these paths are left
  UNMARKED below (genuinely fine, verified by tracing the BPMN's own sequence flows).

  FINDING B — RESOLVIDA (t9-nack-vars, provada no engine real; os 2 xfails que ela sustentava
  foram REMOVIDOS). HISTORICO: `transmit_to_ans` nao tinha ramo de NACK, entao `BE_SubmitNack`
  (boundary em `ST_SubmeterEnvio`) nunca disparava e todo o `SUB_RetryEnvio` era codigo morto da
  perspectiva do engine; e a antiga `AnsRetryEsgotadoError` (`RuntimeError` = familia TRANSIENTE do
  harness) impedia `ST_RetransmitirEnvio` de completar, tornando o terminal modelado de esgotamento
  inalcancavel mesmo em hipotese. As duas quebras foram fechadas em ondas anteriores (mock de
  gateway honrando `requested_outcome`, `WorkerBpmnError(ERR_ANS_PROTOCOLO_NACK)`, remocao da
  `AnsRetryEsgotadoError`, incremento de `retry_attempt`) e as TRES restantes nesta (canal de
  variaveis do `WorkerBpmnError`, `retransmit_to_ans` emitindo `status_envio='retransmitido'`, e a
  notificacao `anssubmit.retransmit`) — ver o bloco FINDING B logo acima de `AnsEngineProbe`.

  FINDING C (systemic, same class as auth/cancel/escalation's residual — Step-3 fact #1): the
  dict-boundary entry functions of `ans_submit.py` do not call `kafka.publish` themselves (`kafka`
  is threaded through for signature parity only, `del kafka  # unused`). The donor's PER-WORKER
  notification pattern (`ans_probe.notifications_of_type("anssubmit.submit")` etc.) can therefore
  never observe an execution on those topics — DISTINCT from the domain-event assertions
  (`has_event`/`events_on`, `agents.events.anssubmit.*`), which DO work: those flow through the
  GENERIC `operadora.events.publish` handler
  (`maezo.tools.workers.events.make_publish_event_handler`), which DOES call `kafka.publish`.
  SCOPE NARROWED TWICE: `notify_regulatorio` (t5) and now `retransmit` (t9-nack-vars) are RAW ASYNC
  handlers that DO publish their typed notification, so `notifications_of_type
  ("anssubmit.notify_regulatorio")` and `notifications_of_type("anssubmit.retransmit")` are both
  live. FINDING C still holds for the remaining sync entries (assemble/validate/submit/
  track_protocol/publish_completed) — `notifications_of_type("anssubmit.submit")` is still
  structurally always [].

  FINDING D (NEW — a guard the donor tests for does not exist in v2 at all): the donor's
  `make_assemble_handler` raises `ERR_ANS_DATASET_INCOMPLETO` when `dataset_assembly_failed=True`
  (montagem falhou na origem). v2's `AnsSubmitInput` dataclass (ans_submit.py) has NO
  `dataset_assembly_failed` field at all, and `prepare_submission`/`assemble_entry` never raise
  `AnsDatasetIncompletoError` for ANY input shape — that exception class is raised ONLY by
  `track_protocol_entry`/`retransmit_entry`, for a completely different condition (blank
  `protocolo_ans`). The assembly-failure guard itself is simply MISSING, not merely
  unreachable/misrouted like A/B.

  D-07 / ceilings (Step-3 fact #2, confirmed inapplicable): `CeilingResolver` is used only by
  auth.py/pagto.py/reembolso.py — ans_submit.py has no ceiling dependency, so D-07 is not cited
  here.

  ans_calendar DMN taxonomy mismatch (Step-3 fact #4) does NOT apply to this file: that mismatch is
  specific to `ans_cron.py`'s own Python re-implementation of `check_calendar`
  (`_REPORT_PERIODICIDADE`'s literals vs the DMN's RN-citation literals) — a function this file
  never calls. SUBMIT's OWN `BRT_Calendario` business-rule task evaluates `ans_calendar` NATIVELY
  via the engine using the SAME RN-citation literals (`RN_124_SIP`/`RN_209_UTILIZACAO`/
  `RN_388_QUALIDADE`/`RN_424_TISS_MONITORAMENTO`/`DIOPS_TRIMESTRAL`) this file's own `start_ans`
  fixture and `test_golden_por_report_type`/`test_catch_all_calendar_roteia_para_humano` already
  use — NO taxonomy mismatch for SUBMIT. Do not conflate the two; see the sibling
  `test_sp_op_ans_cron_001.py` for the ans_cron-specific finding.

T2.6-2 (design §2.B) — TWO SCHEMA-VALIDATION REGIMES coexist in this suite. `validate_data` no
longer echoes the seeded `schema_valid` flag; it computes it via the real `TissSchemaValidator`
(lxml XSD validation, `tools/workers/tiss_schema.py`):

  UNPINNED (fail-closed — production TODAY, and the default `ans_probe` fixture): no
  `MAEZO_TISS_SCHEMA_VERSION` is pinned (the padrão-TISS version in force is SME-gated, design
  §2.B/§7) and no real TISS XSDs are vendored, so `schema_valid` is ALWAYS False. The
  admissibility DMN's `[-, false, -] -> PENDENTE` row then fires for EVERY submission —
  including `origem_envio=nip_filing` — routing to `UT_CorrigirPendenciaEnvio` (human pendency)
  and pre-empting the SEGUE_ENVIO branch where nip_filing would route to
  `UT_RevisarEnvioJuridico`. IMPORTANT correction to an earlier claim in this file: nip_filing's
  `Flow_GW_NipFiling` bypasses ST_PrepararDossie only WITHIN the SEGUE_ENVIO branch — the
  admissibility gate is UPSTREAM of it, so fail-closed `schema_valid=False` re-routes nip_filing
  too (live-confirmed by the R1 validator: actual task = UT_CorrigirPendenciaEnvio). The tests
  `test_nip_filing_sem_schema_pinado_roteia_pendencia_fail_closed` and
  `test_timer_due_date_pendencia_nip_filing_fail_closed` prove THIS regime: human pendency,
  ST_SubmeterEnvio never reached, never auto-transmits.

  PINNED (fixture regime — the `ans_probe_tiss_pinned` fixture): a `TissSchemaValidator` pinned
  at a tmp_path fixture schema root (version `TISS-FIXTURE-0`, one minimal XSD per report_type —
  NOT a real TISS schema; clearly-labeled fixture, same pattern as the unit tests) is injected
  through the `tiss_validator` seam, and `dataset_ref` points at a real tmp XML file. A
  schema-VALID payload -> `schema_valid=True` -> SEGUE_ENVIO -> the nip_filing juridical-review
  human gate (`UT_RevisarEnvioJuridico`) and its BT_DueDateJuridico escalation work exactly as
  before; a schema-INVALID payload still -> PENDENTE -> `UT_CorrigirPendenciaEnvio`. The
  `test_tiss_pinned_*` tests prove THIS regime — the juridico gate is preserved under test
  instead of silently lost to the fail-closed default.

Synthetic data mirrors the donor's obviously-fake identifiers exactly (`revisor-sintetico-001`,
`DATASET-TESTE-0001`, `NIP-TESTE-0001`, etc.).
"""

from __future__ import annotations

import asyncio
import itertools
import uuid
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import pytest
import pytest_asyncio

from maezo.tools.workers.ans_gateway import MOCK_ANS_PROTOCOL_PREFIX, LabeledMockAnsGatewayTransport
from maezo.tools.workers.ans_submit import (
    ANS_SUBMIT_BPMN_ERROR_ALLOWLIST,
    AnsSubmitNotHumanError,
    assemble_entry,
    register_ans_submit_workers,
    submit_entry,
)
from maezo.tools.workers.dmn_transport import CibSevenDmnTransport
from maezo.tools.workers.events import register_events_workers
from maezo.tools.workers.harness import (
    CibSevenWorkerTransport,
    FakeKafkaPublisher,
    WorkerBpmnError,
    WorkerHarness,
)
from maezo.tools.workers.tiss_schema import TissSchemaValidator

from .conftest import CIBSEVEN_BASE_URL, drain_topics
from .engine_rest import EngineRest

pytestmark = pytest.mark.integration

_REPO = Path(__file__).resolve().parents[3]
_BPMN = _REPO / "spec/processes/bpmn/SP-OP-ANS-SUBMIT-001_Envios_Periodicos_ANS.bpmn"
_DMN_CALENDAR = _REPO / "spec/processes/dmn/ans_calendar.dmn"
_DMN_SLA = _REPO / "spec/processes/dmn/ans_sla.dmn"
_DMN_ADMISS = _REPO / "spec/processes/dmn/ans_submission_admissibility.dmn"
_DMN_RETRY = _REPO / "spec/processes/dmn/ans_retry_policy.dmn"

# External task topics do contrato SP-OP-ANS-SUBMIT-001.
_PUBLISH_TOPIC = "operadora.events.publish"
_ASSEMBLE_TOPIC = "regulatorio.anssubmit.assemble"
_VALIDATE_TOPIC = "regulatorio.anssubmit.validate"
_SUBMIT_TOPIC = "regulatorio.anssubmit.submit"
_RETRANSMIT_TOPIC = "regulatorio.anssubmit.retransmit"
_TRACK_TOPIC = "regulatorio.anssubmit.track_protocol"
_NOTIFY_TOPIC = "regulatorio.anssubmit.notify_regulatorio"

# Topicos "servidos" pelo drain generico. `_NOTIFY_TOPIC` TEM worker real registrado desde
# t5-workers-f1 (FINDING A, no docstring do modulo, esta RESOLVIDA —
# `make_notify_regulatorio_handler` via `register_ans_submit_workers`) — listado aqui mesmo assim
# (mirrors a declaracao do BPMN e a expectativa 1:1 do donor) para consistencia com os outros
# topicos "servidos" pelo drain generico; se REGREDISSE (worker desregistrado), qualquer task que
# caisse nele produziria um INCIDENTE alto-falante (harness.py: "no handler registered for
# topic") em vez de ficar presa sem nunca ser buscada.
_ANS_SUBMIT_WORKER_TOPICS = [
    _PUBLISH_TOPIC,
    _ASSEMBLE_TOPIC,
    _VALIDATE_TOPIC,
    _SUBMIT_TOPIC,
    _RETRANSMIT_TOPIC,
    _TRACK_TOPIC,
    _NOTIFY_TOPIC,
]

_NOTIFICATIONS_TOPIC = "operadora.notifications.internal"

# Eventos de dominio (contrato SP-OP-ANS-SUBMIT-001.md) — publicados via o handler GENERICO
# `operadora.events.publish` (funciona — FINDING C no docstring do modulo).
_ANS_SUBMITTED = "agents.events.anssubmit.submitted"
_ANS_ACKED = "agents.events.anssubmit.acked"
_ANS_DEADLINE_RISK = "agents.events.anssubmit.deadline_risk"
_ANS_FAILED = "agents.events.anssubmit.failed"
_ANS_COMPLETED = "agents.events.anssubmit.completed"

# User Task definition keys (BPMN)
_UT_REVISAR = "UT_RevisarEnvio"
_UT_REVISAR_JURIDICO = "UT_RevisarEnvioJuridico"
_UT_CORRIGIR = "UT_CorrigirPendenciaEnvio"
_UT_COORDENACAO = "UT_CoordenacaoEnvioAssume"
_UT_TRATAR_NACK = "UT_TratarNack"

# End events de envio efetivo (so alcancaveis apos UT humana de aprovacao)
_END_ENVIADO_ACK = "End_EnviadoAck"
_END_ENVIADO_PENDENTE_ACK = "End_EnviadoPendenteAck"
_END_ADIADO = "End_AdiadoHumano"

# Service task que materializa o ato vinculante (so co-ocorre com UT humana de aprovacao).
_ST_SUBMETER = "ST_SubmeterEnvio"

_SUB_RETRY = "SUB_RetryEnvio"
_ST_RETRANSMITIR = "ST_RetransmitirEnvio"
_ICE_BACKOFF = "ICE_Backoff"
_END_RETRY_ESGOTADO = "End_RetryEsgotado"

# User Tasks humanas que podem produzir a aprovacao do envio (invariante HITL pre-filing).
_UT_HUMANAS_APROVACAO = frozenset({_UT_REVISAR, _UT_REVISAR_JURIDICO, _UT_COORDENACAO})

# Terminais de envio efetivo (ato vinculante consumado).
_ENDS_ENVIO = frozenset({_END_ENVIADO_ACK, _END_ENVIADO_PENDENTE_ACK})

# CURRENT cause (T2.6-2, see module docstring): the UNPINNED `ans_probe` fixture always computes
# `schema_valid=False` (real TissSchemaValidator, no MAEZO_TISS_SCHEMA_VERSION pinned, no vendored
# XSDs), so the admissibility DMN's `[-, false, -] -> PENDENTE` row fires for EVERY submission and
# routes to `UT_CorrigirPendenciaEnvio` — pre-empting `GW_Admissibilidade`'s SEGUE_ENVIO/
# REVISAO_HUMANA branch entirely (upstream of `ST_PrepararDossie`/notify_regulatorio and of
# `UT_RevisarEnvio`/`UT_RevisarEnvioJuridico`). FINDING A (registration gap, see below) is
# RESOLVED and no longer the reason these tests block; do not re-cite it as a current cause.
_NOTIFY_REGULATORIO_GAP_REASON = (
    "T2.6-2 fail-closed schema-pinning regime (CURRENT cause, not FINDING A — see PORT NOTES below "
    "for the historical finding): this test runs on the UNPINNED `ans_probe` fixture, so "
    "`validate_data` resolves a real `TissSchemaValidator()` with no `MAEZO_TISS_SCHEMA_VERSION` "
    "pinned and no vendored TISS XSDs, which computes `schema_valid=False` unconditionally "
    "(regardless of report_type or the seeded schema_valid=True in start_ans's default payload). "
    "The admissibility DMN's `[-, false, -] -> PENDENTE` row therefore fires for EVERY submission on "
    "this probe, routing to UT_CorrigirPendenciaEnvio and pre-empting GW_Admissibilidade's "
    "SEGUE_ENVIO/REVISAO_HUMANA branch (-> ST_PrepararDossie -> UT_RevisarEnvio) and the "
    "nip_filing branch (-> UT_RevisarEnvioJuridico) alike — so any assertion expecting to reach "
    "UT_RevisarEnvio/UT_RevisarEnvioJuridico (directly or via _drive_to_revisar) never gets there; "
    "the wait times out/errors before the test's real assertions run. Proven correct-by-contrast by "
    "the `ans_probe_tiss_pinned`-based `test_tiss_pinned_*` tests, which inject a pinned "
    "TissSchemaValidator and DO reach SEGUE_ENVIO. "
    "HISTORICAL — FINDING A (T3.1 phase-2 registration gap, RESOLVED in t5-workers-f1): BPMN topic "
    "regulatorio.anssubmit.notify_regulatorio (ST_PrepararDossie / ST_NotificarDeadlineRisk) had NO "
    "worker registered in register_ans_submit_workers, so any task landing there stalled the "
    "instance on an unregistered-topic incident. This is now FIXED — "
    "register_ans_submit_workers registers regulatorio.anssubmit.notify_regulatorio via the raw "
    "async handler make_notify_regulatorio_handler (see the unit regression fence "
    "test_register_ans_submit_workers_registers_notify_regulatorio) — so FINDING A no longer blocks "
    "any test, including this one; it is cited here only as historical context for why this reason "
    "constant predates the T2.6-2 finding above. Where a test would ALSO assert a per-worker "
    "notification (notifications_of_type('anssubmit.notify_regulatorio'/'anssubmit.submit'/etc.)) "
    "even after reaching the relevant User Task under a pinned schema, that assertion would "
    "SEPARATELY hit the still-open systemic FINDING C (no ans_submit.py entry function calls "
    "kafka.publish; only the generic operadora.events.publish handler does) — noted for completeness, "
    "not the reason this particular xfail currently fires."
)

# FINDING B — RESOLVIDA (t9-nack-vars). O antigo `_SUBMIT_NACK_UNREACHABLE_REASON` (constante de
# xfail dos 2 testes de NACK/retry) FOI REMOVIDO junto com os 2 markers, porque os TRES bloqueios
# que ele nomeava foram fechados e provados no engine real — nao contornados:
#   BLOQUEIO 1 (canal de variaveis) — `WorkerBpmnError` ganhou um canal `variables` ALLOWLISTADO
#     (`harness.screen_bpmn_error_variables`: allowlist explicita de chaves + guard de valor
#     limitado, recusa tudo-ou-nada, descarte ruidoso na democao) e `transmit_to_ans` passa
#     `protocolo_ans`/`status_envio` nele. `ST_RetransmitirEnvio` deixa de cair no guard
#     fail-closed de `protocolo_ans` em branco.
#   BLOQUEIO 2 (`status_envio == 'retransmitido'` sem emissor) — `retransmit_to_ans` foi
#     IMPLEMENTADO: `ST_RetransmitirEnvio` agora de fato retransmite pelo MESMO seam de gateway
#     (`AnsGatewayTransport`) e ecoa `retransmitido`/`nack` + `nack_motivo`, que e exatamente o que
#     a documentacao do proprio BPMN sempre exigiu da task ("Incrementa retry_attempt … e ecoa
#     status_envio (retransmitido em sucesso, nack se ainda falha) + nack_motivo"). Nao ha
#     fabricacao: o sucesso vem de uma chamada real ao seam, que em PRODUCAO e o transporte
#     recusador (AWS-blocked, issue #16), e so o mock rotulado de dev/test devolve um resultado.
#   BLOQUEIO 3 (FINDING C, eco kafka morto NESTE topico) — `regulatorio.anssubmit.retransmit` virou
#     raw async handler (`make_retransmit_handler`, o nome que o proprio contrato ja usa em :157) e
#     publica a notificacao tipada `anssubmit.retransmit`. FINDING C segue valendo para os OUTROS
#     topicos de entry function sincrona deste modulo.
# Os 2 testes tambem passaram a usar o probe PINADO (`ans_probe_tiss_pinned`) — o regime nao-pinado
# nunca alcanca `UT_RevisarEnvio` (T2.6-2, ver `_NOTIFY_REGULATORIO_GAP_REASON`), e o pinado nao
# exige XSD real nem SME (escreve um XSD de fixture em tmp_path).

# FINDING D (new — guard genuinely missing in v2, not merely misrouted).


@dataclass
class AnsEngineProbe:
    """Driva os workers reais de ans_submit contra o engine CIB Seven."""

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
            self.transport, self.harness, self.worker_id, _ANS_SUBMIT_WORKER_TOPICS, rounds=rounds
        )


@pytest_asyncio.fixture
async def deploy_artifacts(engine: EngineRest) -> str:
    """Deploya BPMN + 4 DMN de envio ANS da arvore no engine real (todos os 4 referenciados por
    decisionRef — verificado por grep, module docstring)."""
    return await engine.deploy(
        _BPMN, _DMN_CALENDAR, _DMN_SLA, _DMN_ADMISS, _DMN_RETRY, name="SP-OP-ANS-SUBMIT-001-qa"
    )


def _build_ans_probe(
    engine: EngineRest,
    audit_sink: Any,
    audit_tenant: str,
    *,
    tiss_validator: TissSchemaValidator | None = None,
) -> AnsEngineProbe:
    """Shared probe construction for the two schema-validation regimes (module docstring, T2.6-2):
    `tiss_validator=None` -> UNPINNED fail-closed regime (validate_data resolves a real
    `TissSchemaValidator()`, `MAEZO_TISS_SCHEMA_VERSION` unset -> `schema_valid` always False);
    a pinned validator -> PINNED fixture regime (real lxml validation against the fixture XSD)."""
    worker_id = f"qa-anssubmit-worker-{uuid.uuid4().hex[:8]}"
    transport = CibSevenWorkerTransport(CIBSEVEN_BASE_URL)
    # bpmn_error_allowlist = the codes this family RAISES as modeled bpmn errors, each boundary-
    # proven in SUBMIT's BPMN (BE_SubmitNack / BE_AssembleDatasetIncompleto). LIVE since
    # t9-nack-vars: the 2 NACK tests drive ERR_ANS_PROTOCOLO_NACK end-to-end (FINDING B resolved).
    harness = WorkerHarness(
        transport,
        worker_id=worker_id,
        tenant=audit_tenant,
        lock_duration_ms=10_000,
        bpmn_error_allowlist=ANS_SUBMIT_BPMN_ERROR_ALLOWLIST,
        # T1.10 wave: emit-before-complete is fail-closed — real lane PostgresAuditSink required.
        audit_sink=audit_sink,
    )
    kafka = FakeKafkaPublisher()
    dmn = CibSevenDmnTransport(CIBSEVEN_BASE_URL)
    # T2.6-1 (design §2.A): inject the LabeledMock ANS gateway (dev/test) so legitimately-transmitting
    # paths get a deterministic, non-binding `MOCK-ANS-NAO-VINCULATIVO-{business_key}` protocol. In
    # PRODUCTION no gateway is injected -> `resolve_ans_gateway` fails closed to the Refusing
    # transport (issues nothing). The old fabricated `ANSPROTO-{sha256(time_ns)}` is gone.
    register_ans_submit_workers(
        harness,
        kafka,
        dmn=dmn,
        ans_gateway=LabeledMockAnsGatewayTransport(),
        tiss_validator=tiss_validator,
    )
    # The generic operadora.events.publish worker every ST_Publish* service task in this BPMN
    # routes through — mirrors the donor's own register_phase0_workers composition.
    register_events_workers(harness, kafka)
    return AnsEngineProbe(
        engine=engine,
        harness=harness,
        transport=transport,
        kafka=kafka,
        worker_id=worker_id,
    )


@pytest_asyncio.fixture
async def ans_probe(engine: EngineRest, audit_sink: Any, audit_tenant: str) -> AsyncIterator[AnsEngineProbe]:
    """Probe que serve as external tasks com os workers reais de envio ANS — regime NAO-PINADO
    (fail-closed, T2.6-2): nenhum `tiss_validator` injetado, `MAEZO_TISS_SCHEMA_VERSION` unset =>
    `schema_valid` sempre False => admissibilidade PENDENTE => UT_CorrigirPendenciaEnvio."""
    probe = _build_ans_probe(engine, audit_sink, audit_tenant)
    try:
        yield probe
    finally:
        await probe.transport.close()


#: T2.6-2 fixture-schema constants — NOT a real ANS Padrão TISS version/schema. A minimal,
#: clearly-labeled fixture (same `loteGuias` shape as tests/unit/tools/workers/test_tiss_schema.py)
#: used only to prove the PINNED regime's routing end-to-end against the real engine.
_TISS_FIXTURE_VERSION = "TISS-FIXTURE-0"

_TISS_FIXTURE_XSD = """<?xml version="1.0" encoding="UTF-8"?>
<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema">
  <xs:element name="loteGuias">
    <xs:complexType>
      <xs:sequence>
        <xs:element name="numeroLote" type="xs:string"/>
      </xs:sequence>
    </xs:complexType>
  </xs:element>
</xs:schema>
"""

_TISS_VALID_XML = "<loteGuias><numeroLote>LOTE-TESTE-0001</numeroLote></loteGuias>"
_TISS_SCHEMA_INVALID_XML = "<loteGuias><campoInexistente/></loteGuias>"


def _write_tiss_dataset(tmp_path: Path, name: str, xml: str) -> str:
    """Write a fixture dataset XML under tmp_path and return its path as the `dataset_ref` (the
    validator resolves `dataset_ref` as a local file path — `tiss_schema.py` docstring)."""
    path = tmp_path / name
    path.write_text(xml, encoding="utf-8")
    return str(path)


@pytest_asyncio.fixture
async def ans_probe_tiss_pinned(
    engine: EngineRest, audit_sink: Any, audit_tenant: str, tmp_path: Path
) -> AsyncIterator[AnsEngineProbe]:
    """Probe do regime PINADO (T2.6-2): injeta um `TissSchemaValidator` pinado num schema-root de
    fixture (tmp_path) com a versao `TISS-FIXTURE-0` e o XSD minimo para o report_type default
    (`RN_124_SIP`). Com `dataset_ref` apontando a um XML valido, `schema_valid=True` e o roteamento
    SEGUE_ENVIO (incl. nip_filing -> UT_RevisarEnvioJuridico) funciona; um XML schema-invalido
    continua PENDENTE. pytest injeta o MESMO `tmp_path` no teste, entao os testes escrevem seus
    datasets via `_write_tiss_dataset(tmp_path, ...)` no mesmo diretorio."""
    schema_root = tmp_path / "tiss-fixture-schemas"
    version_dir = schema_root / _TISS_FIXTURE_VERSION
    version_dir.mkdir(parents=True)
    (version_dir / "RN_124_SIP.xsd").write_text(_TISS_FIXTURE_XSD, encoding="utf-8")
    validator = TissSchemaValidator(schema_root=schema_root, version=_TISS_FIXTURE_VERSION)
    probe = _build_ans_probe(engine, audit_sink, audit_tenant, tiss_validator=validator)
    try:
        yield probe
    finally:
        await probe.transport.close()


@pytest_asyncio.fixture
async def start_ans(engine: EngineRest, deploy_artifacts: str) -> Callable[..., Any]:
    """Inicia uma instancia com business key ANSSUB-amh-{report_type}-{competencia} e payload
    canonico. Overrides via kwargs. Dados sinteticos obvios (tenant amh, RN_124_SIP, 2026-01).
    """

    async def _start(**overrides: Any) -> dict[str, Any]:
        report_type = overrides.pop("report_type", "RN_124_SIP")
        competencia = overrides.pop("competencia", "2026-01")
        variables: dict[str, Any] = {
            "tenant_id": "amh",
            "report_type": report_type,
            "competencia": competencia,
            "periodicidade": "mensal",
            "origem_envio": "calendario",
            "dataset_ref": "DATASET-TESTE-0001",
            "dataset_complete": True,
            "schema_valid": True,
            "lgpd_anonimizado": True,
        }
        variables.update(overrides)
        business_key = f"ANSSUB-amh-{report_type}-{competencia}"
        return await engine.start_by_key("SP-OP-ANS-SUBMIT-001", business_key, variables)

    return _start


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _await_end(engine: EngineRest, iid: str, *, attempts: int = 60, delay: float = 0.25) -> set[str]:
    """Espera a instancia terminar e retorna as atividades historicas atingidas."""
    for _ in range(attempts):
        state = await engine.history_state(iid)
        if state == "COMPLETED":
            return await engine.activity_instances_ended(iid)
        await asyncio.sleep(delay)
    return await engine.activity_instances_ended(iid)


async def _assert_no_filing_without_human_task(engine: EngineRest, iid: str) -> None:
    """INVARIANTE HITL pre-filing: prova que o ato vinculante (submit / terminal de envio) nao
    existe sem uma User Task humana de aprovacao."""
    ended = await engine.activity_instances_ended(iid)
    filing_in_history = (_ST_SUBMETER in ended) or bool(ended & _ENDS_ENVIO)
    if filing_in_history:
        human_tasks_in_history = ended & _UT_HUMANAS_APROVACAO
        assert human_tasks_in_history, (
            f"INVARIANTE HITL VIOLADA: filing ANS (submit/terminal de envio) atingido para "
            f"instancia {iid} SEM nenhuma User Task humana de aprovacao no historico. "
            f"User Tasks esperadas (qualquer uma de): {_UT_HUMANAS_APROVACAO}. "
            f"Atividades historicas encontradas: {ended}. "
            "Isto indica um caminho automatizado de envio — violacao do HITL pre-filing (ADR-0007)."
        )


async def _drive_to_revisar(engine: EngineRest, probe: AnsEngineProbe, iid: str) -> Any:
    """Drena ate UT_RevisarEnvio surgir (dossie preparado pelo worker real)."""
    await probe.drain()
    return await engine.await_user_task(iid, _UT_REVISAR)


async def _aprovar_envio(
    engine: EngineRest, ut_id: str, *, revisor_id: str = "revisor-sintetico-001"
) -> None:
    """Completa a User Task de revisao como humano com APROVAR_ENVIO + revisor_id."""
    await engine.complete_task_as_human(ut_id, {"decisao_envio": "APROVAR_ENVIO", "revisor_id": revisor_id})


async def _correlate_ack(business_key: str, protocolo_ans: str = "ANS-TESTE-0001") -> None:
    """Correlaciona msg.anssubmit.ack_received (ACK assincrono da ANS) por business key."""
    correlate_payload = {
        "messageName": "msg.anssubmit.ack_received",
        "businessKey": business_key,
        "processVariables": {
            "status_envio": {"value": "ack", "type": "String"},
            "protocolo_ans": {"value": protocolo_ans, "type": "String"},
        },
    }
    async with httpx.AsyncClient(base_url=CIBSEVEN_BASE_URL, timeout=20.0) as client:
        resp = await client.post("/message", json=correlate_payload)
        assert resp.status_code in (200, 204), (
            f"Correlacao msg.anssubmit.ack_received falhou [{resp.status_code}]: {resp.text[:200]}"
        )


# ===========================================================================
# INVARIANTE HITL pre-filing — DoD deliverable (varredura de inputs da DMN admissibilidade)
# ===========================================================================


async def test_submit_exige_user_task_humana(
    engine: EngineRest,
    ans_probe: AnsEngineProbe,
    start_ans: Callable[..., Any],
) -> None:
    """INVARIANTE HITL pre-filing: NENHUM caminho automatizado transmite a ANS.

    Varredura de TODAS as combinacoes de input da DMN ans_submission_admissibility (2*2*2 = 8).
    Sobrevive a FINDING A (module docstring): as 3 combinacoes que roteiam SEGUE_ENVIO/
    REVISAO_HUMANA ficam presas em ST_PrepararDossie (nunca alcancam ST_SubmeterEnvio de qualquer
    forma); as 5 que roteiam PENDENTE alcancam UT_CorrigirPendenciaEnvio sem depender de
    notify_regulatorio (Flow_GW_Pendente sai direto de GW_Admissibilidade). Em ambos os casos a
    invariante (nunca transmite) se mantem — nao depende de notifications_of_type().
    """
    bools = [True, False]
    checked = 0

    for complete, schema, lgpd in itertools.product(bools, bools, bools):
        inst = await start_ans(
            competencia=f"2026-SCAN-{checked:02d}",
            dataset_complete=complete,
            schema_valid=schema,
            lgpd_anonimizado=lgpd,
        )
        iid = inst["id"]
        await ans_probe.drain()

        ended = await engine.activity_instances_ended(iid)
        assert _ST_SUBMETER not in ended, (
            f"HITL VIOLADO: complete={complete} schema={schema} lgpd={lgpd} executou "
            f"ST_SubmeterEnvio automaticamente. ended={ended}"
        )
        assert not (ended & _ENDS_ENVIO), (
            f"HITL VIOLADO: complete={complete} schema={schema} lgpd={lgpd} atingiu terminal de "
            f"envio automaticamente. ended={ended}"
        )
        await _assert_no_filing_without_human_task(engine, iid)
        checked += 1

    assert checked == 8, f"Esperava 8 combinacoes varridas; varri {checked}"


async def test_dados_faltantes_roteiam_para_humano_nunca_auto_rejeita(
    engine: EngineRest,
    ans_probe: AnsEngineProbe,
    start_ans: Callable[..., Any],
) -> None:
    """dataset_complete=false => admissibilidade -> PENDENTE -> UT_CorrigirPendenciaEnvio.

    Nao depende de notify_regulatorio (Flow_GW_Pendente bypassa ST_PrepararDossie) — nao afetado
    por FINDING A.

    PORT FIX (T3.1 phase-2, test-only bug — src/** untouched): `dataset_ref` MUST also be
    cleared. `start_ans`'s default payload seeds `dataset_ref="DATASET-TESTE-0001"` (a truthy
    stub); `ans_submit.py::prepare_submission` (line ~133) computes
    `dataset_complete = input_data.dataset_complete or bool(input_data.dataset_ref)` — i.e. it
    RECOMPUTES dataset_complete, and a non-empty dataset_ref forces it back to True regardless of
    the seeded `dataset_complete=False` override, so GW_Admissibilidade never sees a false
    `dataset_complete` and routes SEGUE_ENVIO/REVISAO_HUMANA (-> ST_PrepararDossie, topic
    `regulatorio.anssubmit.notify_regulatorio`, unregistered per FINDING A) instead of PENDENTE.
    Confirmed live: without this fix the instance stalls, `UT_CorrigirPendenciaEnvio` never
    appears. Clearing `dataset_ref` here is a straight port/fixture-usage fix (mirrors the
    `_MEASURE_GAP_OVERRIDES_SEEDED_FACTS_REASON` pattern already documented in adequacao), not a
    weakening of this test's intent (still exercises the PENDENTE/dados-faltantes path).
    """
    inst = await start_ans(competencia="2026-PEND", dataset_complete=False, dataset_ref="")
    iid = inst["id"]

    await ans_probe.drain()
    ut = await engine.await_user_task(iid, _UT_CORRIGIR)
    assert "regulatorio-ans" in ut.candidate_groups

    ended = await engine.activity_instances_ended(iid)
    assert _ST_SUBMETER not in ended, "Dado faltante nao deve transmitir (HITL)"
    assert not (ended & _ENDS_ENVIO), "Dado faltante nao deve atingir terminal de envio"
    await _assert_no_filing_without_human_task(engine, iid)


@pytest.mark.xfail(reason=_NOTIFY_REGULATORIO_GAP_REASON, strict=True)
async def test_aprovar_envio_exige_revisor_id(
    engine: EngineRest,
    ans_probe: AnsEngineProbe,
    start_ans: Callable[..., Any],
) -> None:
    """APROVAR_ENVIO sem revisor_id => worker submit recusa (ERR_ANS_SUBMIT_NOT_HUMAN)."""
    inst = await start_ans(competencia="2026-NOREV")
    iid = inst["id"]

    ut = await _drive_to_revisar(engine, ans_probe, iid)
    await engine.complete_task_as_human(ut.id, {"decisao_envio": "APROVAR_ENVIO"})
    await ans_probe.drain()

    ended = await engine.activity_instances_ended(iid)
    assert not (ended & _ENDS_ENVIO), "Aprovacao sem revisor_id NAO pode atingir terminal de envio"
    submits = ans_probe.notifications_of_type("anssubmit.submit")
    assert not submits, "submit NAO deve transmitir sem revisor_id"
    await _assert_no_filing_without_human_task(engine, iid)


async def test_nip_filing_sem_schema_pinado_roteia_pendencia_fail_closed(
    engine: EngineRest,
    ans_probe: AnsEngineProbe,
    start_ans: Callable[..., Any],
) -> None:
    """REGIME NAO-PINADO (T2.6-2 fail-closed — adaptado de `test_nip_filing_exige_revisao_juridica`,
    que regrediu quando `validate_data` deixou de ecoar o `schema_valid` seedado): sem
    `MAEZO_TISS_SCHEMA_VERSION` pinada, `schema_valid` e SEMPRE False, e a admissibilidade
    (`[-, false, -] -> PENDENTE`) roteia TODA submissao — inclusive nip_filing — a
    `UT_CorrigirPendenciaEnvio` (pendencia humana), pre-emptando o ramo SEGUE_ENVIO onde
    `Flow_GW_NipFiling` levaria a `UT_RevisarEnvioJuridico`. (Correcao do claim anterior deste
    teste: o bypass de ST_PrepararDossie so existe DENTRO do ramo SEGUE_ENVIO — o gate de
    admissibilidade e UPSTREAM dele.) O roteamento juridico em si e provado no regime PINADO
    (`test_tiss_pinned_nip_filing_valido_roteia_revisao_juridica`). Invariante mantida: nunca
    auto-transmite — ST_SubmeterEnvio jamais alcancado sem humano."""
    inst = await start_ans(
        competencia="2026-NIP", origem_envio="nip_filing", nip_protocolo_origem="NIP-TESTE-0001"
    )
    iid = inst["id"]

    await ans_probe.drain()
    ut = await engine.await_user_task(iid, _UT_CORRIGIR)
    assert "regulatorio-ans" in ut.candidate_groups

    # Fail-closed computado pelo validador real (nao mais o echo do flag seedado True).
    assert await engine.get_variable(iid, "schema_valid") is False

    open_keys = {t.task_definition_key for t in await engine.list_user_tasks(iid)}
    assert _UT_REVISAR_JURIDICO not in open_keys, (
        "Sem schema pinado, nip_filing NAO deve alcancar a revisao juridica (PENDENTE pre-empta)"
    )

    ended = await engine.activity_instances_ended(iid)
    assert _ST_SUBMETER not in ended, "Fail-closed nunca transmite (HITL)"
    assert not (ended & _ENDS_ENVIO), "Fail-closed nunca atinge terminal de envio"
    await _assert_no_filing_without_human_task(engine, iid)


# ===========================================================================
# T2.6-2 — REGIME PINADO (fixture): valida que com um `TissSchemaValidator` pinado (XSD de
# fixture, NAO um schema TISS real) o roteamento SEGUE_ENVIO volta a funcionar — o gate juridico
# do nip_filing e preservado sob teste em vez de silenciosamente perdido ao fail-closed.
# ===========================================================================


async def test_tiss_pinned_nip_filing_valido_roteia_revisao_juridica(
    engine: EngineRest,
    ans_probe_tiss_pinned: AnsEngineProbe,
    start_ans: Callable[..., Any],
    tmp_path: Path,
) -> None:
    """REGIME PINADO: schema pinado (fixture) + payload XML schema-VALIDO => o validador real
    computa `schema_valid=True` => admissibilidade SEGUE_ENVIO => `Flow_GW_NipFiling` roteia a
    `UT_RevisarEnvioJuridico` (juridico-regulatorio; texto legal sempre humano) => aprovacao
    humana => transmit (LabeledMock) => ACK => End_EnviadoAck. E o conteudo integral do antigo
    `test_nip_filing_exige_revisao_juridica`, agora sob o regime que o torna alcancavel."""
    dataset_ref = _write_tiss_dataset(tmp_path, "dataset-nip-valido.xml", _TISS_VALID_XML)
    inst = await start_ans(
        competencia="2026-NIPPIN",
        origem_envio="nip_filing",
        nip_protocolo_origem="NIP-TESTE-PIN1",
        dataset_ref=dataset_ref,
    )
    iid = inst["id"]

    await ans_probe_tiss_pinned.drain()
    ut = await engine.await_user_task(iid, _UT_REVISAR_JURIDICO)
    assert "juridico-regulatorio" in ut.candidate_groups

    # schema_valid COMPUTADO True pelo lxml contra o XSD de fixture; pin da versao surfaceado
    # como variavel de processo (registro de auditoria — design §2.B).
    assert await engine.get_variable(iid, "schema_valid") is True
    assert await engine.get_variable(iid, "tiss_schema_version") == _TISS_FIXTURE_VERSION

    ended = await engine.activity_instances_ended(iid)
    assert _ST_SUBMETER not in ended, "nip_filing nao transmite antes da revisao juridica"
    await _assert_no_filing_without_human_task(engine, iid)

    await _aprovar_envio(engine, ut.id, revisor_id="juridico-sintetico-001")
    await ans_probe_tiss_pinned.drain()
    await _correlate_ack(inst["businessKey"])
    await ans_probe_tiss_pinned.drain()

    ended = await _await_end(engine, iid)
    assert _END_ENVIADO_ACK in ended, f"nip_filing aprovado juridicamente => enviado_ack. ended={ended}"
    await _assert_no_filing_without_human_task(engine, iid)


async def test_tiss_pinned_payload_schema_invalido_roteia_pendencia(
    engine: EngineRest,
    ans_probe_tiss_pinned: AnsEngineProbe,
    start_ans: Callable[..., Any],
    tmp_path: Path,
) -> None:
    """REGIME PINADO, payload INVALIDO: mesmo com o schema pinado, um XML que viola o XSD =>
    `schema_valid=False` => PENDENTE => `UT_CorrigirPendenciaEnvio` (humano corrige; nunca
    rejeita, nunca transmite). Prova que o True do teste anterior vem da validacao real — nao de
    um echo/sempre-True."""
    dataset_ref = _write_tiss_dataset(tmp_path, "dataset-nip-invalido.xml", _TISS_SCHEMA_INVALID_XML)
    inst = await start_ans(
        competencia="2026-PININV",
        origem_envio="nip_filing",
        nip_protocolo_origem="NIP-TESTE-PIN2",
        dataset_ref=dataset_ref,
    )
    iid = inst["id"]

    await ans_probe_tiss_pinned.drain()
    ut = await engine.await_user_task(iid, _UT_CORRIGIR)
    assert "regulatorio-ans" in ut.candidate_groups

    assert await engine.get_variable(iid, "schema_valid") is False

    ended = await engine.activity_instances_ended(iid)
    assert _ST_SUBMETER not in ended, "Schema invalido nunca transmite (HITL)"
    assert not (ended & _ENDS_ENVIO)
    await _assert_no_filing_without_human_task(engine, iid)


async def test_tiss_pinned_timer_due_date_coordenacao_assume_juridico(
    engine: EngineRest,
    ans_probe_tiss_pinned: AnsEngineProbe,
    start_ans: Callable[..., Any],
    tmp_path: Path,
) -> None:
    """REGIME PINADO: o conteudo integral do antigo `test_timer_due_date_coordenacao_assume_juridico`
    (que regrediu no regime nao-pinado — `UT_RevisarEnvioJuridico` nunca aparecia). Com schema
    pinado + payload valido, a UT juridica existe e seu timer INTERRUPTIVO `BT_DueDateJuridico`
    (Flow_DueDateJuridico_UTCoord, direto — sem ST_NotificarDeadlineRisk, nao bloqueado por
    FINDING A) cancela a UT juridica e cria `UT_CoordenacaoEnvioAssume`; a coordenacao aprova =>
    filing so apos UT humana (invariante)."""
    dataset_ref = _write_tiss_dataset(tmp_path, "dataset-ddjur-valido.xml", _TISS_VALID_XML)
    inst = await start_ans(
        competencia="2026-DDJPIN",
        origem_envio="nip_filing",
        nip_protocolo_origem="NIP-TESTE-DDPIN",
        dataset_ref=dataset_ref,
    )
    iid = inst["id"]

    await ans_probe_tiss_pinned.drain()
    await engine.await_user_task(iid, _UT_REVISAR_JURIDICO)

    job = await engine.await_timer_job(iid, "BT_DueDateJuridico")
    await engine.execute_job(job.id)
    await ans_probe_tiss_pinned.drain()

    ut_coord = await engine.await_user_task(iid, _UT_COORDENACAO)
    assert "coordenacao-regulatorio" in ut_coord.candidate_groups

    open_keys = {t.task_definition_key for t in await engine.list_user_tasks(iid)}
    assert _UT_REVISAR_JURIDICO not in open_keys, "UT_RevisarEnvioJuridico deve ser cancelada (interruptivo)"

    ended = await engine.activity_instances_ended(iid)
    assert _ST_SUBMETER not in ended, "Estouro de prazo nunca transmite automaticamente (HITL)"
    await _assert_no_filing_without_human_task(engine, iid)

    await _aprovar_envio(engine, ut_coord.id, revisor_id="coordenacao-sintetica-002")
    await ans_probe_tiss_pinned.drain()
    await _correlate_ack(inst["businessKey"])
    await ans_probe_tiss_pinned.drain()

    ended = await _await_end(engine, iid)
    assert _END_ENVIADO_ACK in ended
    await _assert_no_filing_without_human_task(engine, iid)


# ===========================================================================
# Happy paths
# ===========================================================================


@pytest.mark.xfail(reason=_NOTIFY_REGULATORIO_GAP_REASON, strict=True)
async def test_happy_path_envio_aprovado_e_acked(
    engine: EngineRest,
    ans_probe: AnsEngineProbe,
    start_ans: Callable[..., Any],
) -> None:
    """Humano aprova o envio (APROVAR_ENVIO + revisor_id); ACK correlacionado => End_EnviadoAck."""
    inst = await start_ans(competencia="2026-HAPPY")
    iid = inst["id"]

    ut = await _drive_to_revisar(engine, ans_probe, iid)
    assert "regulatorio-ans" in ut.candidate_groups

    dossiers = ans_probe.notifications_of_type("anssubmit.notify_regulatorio")
    assert dossiers, "Worker notify_regulatorio (dossie) deve ter sido executado"

    await _aprovar_envio(engine, ut.id)
    await ans_probe.drain()
    await _correlate_ack(inst["businessKey"])
    await ans_probe.drain()

    ended = await _await_end(engine, iid)
    await _assert_no_filing_without_human_task(engine, iid)
    assert _END_ENVIADO_ACK in ended, f"Envio aprovado+ACK deve atingir End_EnviadoAck. ended={ended}"
    assert ans_probe.has_event(_ANS_SUBMITTED)
    assert ans_probe.has_event(_ANS_ACKED)
    assert ans_probe.has_event(_ANS_COMPLETED, desfecho="enviado_ack")

    submits = ans_probe.notifications_of_type("anssubmit.submit")
    assert submits, "Worker submit deve ser executado apos aprovacao humana"
    s = submits[0]
    assert s["decisao_envio"] == "APROVAR_ENVIO"
    assert s["revisor_id"] == "revisor-sintetico-001"
    # T2.6-1 (design §2.A): the probe injects the LabeledMock gateway, so the protocol is the
    # deterministic, unmistakably-synthetic `MOCK-ANS-NAO-VINCULATIVO-{business_key}` — NOT the old
    # fabricated `ANSPROTO-{sha256(time_ns)}`. (This test stays strict-xfail on the T2.6-2
    # fail-closed schema-pinning regime — see _NOTIFY_REGULATORIO_GAP_REASON — which blocks the
    # flow before it ever reaches UT_RevisarEnvio, let alone submit; NOT FINDING A, which is
    # resolved. The assertion is corrected for when this test runs under a pinned schema.)
    assert s["protocolo_ans"].startswith(MOCK_ANS_PROTOCOL_PREFIX)


@pytest.mark.xfail(reason=_NOTIFY_REGULATORIO_GAP_REASON, strict=True)
@pytest.mark.parametrize(
    ("report_type", "competencia", "periodicidade"),
    [
        ("RN_124_SIP", "2026-01", "mensal"),
        ("RN_209_UTILIZACAO", "2026-01", "mensal"),
        ("RN_388_QUALIDADE", "2026-01", "anual"),
        ("RN_424_TISS_MONITORAMENTO", "2026-01", "mensal"),
        ("DIOPS_TRIMESTRAL", "2026-Q1", "trimestral"),
    ],
)
async def test_golden_por_report_type(
    engine: EngineRest,
    ans_probe: AnsEngineProbe,
    start_ans: Callable[..., Any],
    report_type: str,
    competencia: str,
    periodicidade: str,
) -> None:
    """Golden por report_type: ans_calendar resolve prazos coerentes; happy path ate enviado_ack."""
    inst = await start_ans(report_type=report_type, competencia=competencia, periodicidade=periodicidade)
    iid = inst["id"]
    assert inst["businessKey"] == f"ANSSUB-amh-{report_type}-{competencia}"

    ut = await _drive_to_revisar(engine, ans_probe, iid)
    assert "regulatorio-ans" in ut.candidate_groups

    await _aprovar_envio(engine, ut.id)
    await ans_probe.drain()
    await _correlate_ack(inst["businessKey"])
    await ans_probe.drain()

    ended = await _await_end(engine, iid)
    assert _END_ENVIADO_ACK in ended, f"{report_type} {competencia} => enviado_ack. ended={ended}"
    await _assert_no_filing_without_human_task(engine, iid)
    assert ans_probe.has_event(_ANS_COMPLETED, desfecho="enviado_ack")


@pytest.mark.xfail(reason=_NOTIFY_REGULATORIO_GAP_REASON, strict=True)
async def test_ack_pendente_completa_como_enviado_pendente_ack(
    engine: EngineRest,
    ans_probe: AnsEngineProbe,
    start_ans: Callable[..., Any],
) -> None:
    """Envio transmitido mas ACK nao chegou; ICE_AguardarAck (P1D) expira => enviado_pendente_ack."""
    inst = await start_ans(competencia="2026-PENDACK")
    iid = inst["id"]

    ut = await _drive_to_revisar(engine, ans_probe, iid)
    await _aprovar_envio(engine, ut.id)
    await ans_probe.drain()

    job = await engine.await_timer_job(iid, "ICE_AguardarAck")
    await engine.execute_job(job.id)
    await ans_probe.drain()

    ended = await _await_end(engine, iid)
    assert _END_ENVIADO_PENDENTE_ACK in ended, (
        f"ACK ausente no prazo => End_EnviadoPendenteAck. ended={ended}"
    )
    await _assert_no_filing_without_human_task(engine, iid)
    assert ans_probe.has_event(_ANS_COMPLETED, desfecho="enviado_pendente_ack")


# ===========================================================================
# Pendencia de dados / adiamento
# ===========================================================================


@pytest.mark.xfail(reason=_NOTIFY_REGULATORIO_GAP_REASON, strict=True)
async def test_corrigir_pendencia_reavalia(
    engine: EngineRest,
    ans_probe: AnsEngineProbe,
    start_ans: Callable[..., Any],
) -> None:
    """dataset_complete=false => PENDENTE -> UT_CorrigirPendenciaEnvio; corrigido => reavalia.

    Alcanca UT_CorrigirPendenciaEnvio (nao bloqueado): a correcao humana so seta
    `dataset_complete=True` (nao `schema_valid`), e no regime NAO-PINADO `schema_valid`
    permanece SEMPRE False (T2.6-2 — ver _NOTIFY_REGULATORIO_GAP_REASON), entao a reavaliacao
    pos-correcao continua roteando PENDENTE em vez de SEGUE_ENVIO/REVISAO_HUMANA ->
    UT_RevisarEnvio. BLOQUEADO por T2.6-2 (nao mais por FINDING A, que esta resolvida).
    """
    inst = await start_ans(competencia="2026-CORR", dataset_complete=False)
    iid = inst["id"]

    await ans_probe.drain()
    ut_corrigir = await engine.await_user_task(iid, _UT_CORRIGIR)
    assert "regulatorio-ans" in ut_corrigir.candidate_groups

    await engine.complete_task_as_human(ut_corrigir.id, {"dataset_complete": True})
    await ans_probe.drain()

    ut_revisar = await engine.await_user_task(iid, _UT_REVISAR)
    assert "regulatorio-ans" in ut_revisar.candidate_groups

    ended = await engine.activity_instances_ended(iid)
    assert _ST_SUBMETER not in ended, "Correcao nao transmite automaticamente (HITL)"
    await _assert_no_filing_without_human_task(engine, iid)


@pytest.mark.xfail(reason=_NOTIFY_REGULATORIO_GAP_REASON, strict=True)
async def test_lgpd_nao_anonimizado_roteia_direto_a_revisao_humana(
    engine: EngineRest,
    ans_probe: AnsEngineProbe,
    start_ans: Callable[..., Any],
) -> None:
    """lgpd_anonimizado=false (com dataset_complete/schema_valid=true SEEDADOS) => intencao e
    REVISAO_HUMANA (rule r_revisao_lgpd) => Flow_GW_Revisao (default) => ST_PrepararDossie =>
    UT_RevisarEnvio DIRETO. Na pratica, no regime NAO-PINADO `schema_valid` e recomputado SEMPRE
    False (T2.6-2 — ver _NOTIFY_REGULATORIO_GAP_REASON), entao o seed `schema_valid=true` e
    ignorado e a admissibilidade roteia PENDENTE em vez de REVISAO_HUMANA — BLOQUEADO por T2.6-2
    (nao mais por FINDING A, que esta resolvida).
    """
    inst = await start_ans(competencia="2026-LGPDREV", lgpd_anonimizado=False)
    iid = inst["id"]

    await ans_probe.drain()
    ut_revisar = await engine.await_user_task(iid, _UT_REVISAR)
    assert "regulatorio-ans" in ut_revisar.candidate_groups
    assert await engine.get_variable(iid, "lgpd_anonimizado") is False

    ended = await engine.activity_instances_ended(iid)
    assert _UT_CORRIGIR not in ended, (
        "lgpd_anonimizado=false sozinho nao deve rotear a UT_CorrigirPendenciaEnvio"
    )
    assert _ST_SUBMETER not in ended, "Sem decisao humana, nao deve transmitir (HITL)"
    await _assert_no_filing_without_human_task(engine, iid)


@pytest.mark.xfail(reason=_NOTIFY_REGULATORIO_GAP_REASON, strict=True)
async def test_corrigir_pendencia_lgpd_anonimizado_reavalia(
    engine: EngineRest,
    ans_probe: AnsEngineProbe,
    start_ans: Callable[..., Any],
) -> None:
    """GAP-ANS-5: lgpd_anonimizado=false => intencao e REVISAO_HUMANA => UT_RevisarEnvio, mas
    BLOQUEADO por T2.6-2 (nao mais FINDING A, que esta resolvida) antes mesmo da primeira
    UT_RevisarEnvio aparecer — `schema_valid` recomputado sempre False no regime NAO-PINADO
    pre-empta a admissibilidade para PENDENTE (ver _NOTIFY_REGULATORIO_GAP_REASON)."""
    inst = await start_ans(competencia="2026-CORRLGPD", lgpd_anonimizado=False)
    iid = inst["id"]

    await ans_probe.drain()
    ut_revisar_1 = await engine.await_user_task(iid, _UT_REVISAR)
    assert "regulatorio-ans" in ut_revisar_1.candidate_groups

    await engine.complete_task_as_human(ut_revisar_1.id, {"decisao_envio": "CORRIGIR_PENDENCIA"})
    await ans_probe.drain()

    ut_corrigir = await engine.await_user_task(iid, _UT_CORRIGIR)
    assert "regulatorio-ans" in ut_corrigir.candidate_groups

    await engine.complete_task_as_human(ut_corrigir.id, {"lgpd_anonimizado": True})
    await ans_probe.drain()

    ut_revisar_2 = await engine.await_user_task(iid, _UT_REVISAR)
    assert "regulatorio-ans" in ut_revisar_2.candidate_groups
    assert await engine.get_variable(iid, "lgpd_anonimizado") is True

    ended = await engine.activity_instances_ended(iid)
    assert _ST_SUBMETER not in ended, "Correcao nao transmite automaticamente (HITL)"
    await _assert_no_filing_without_human_task(engine, iid)


@pytest.mark.xfail(reason=_NOTIFY_REGULATORIO_GAP_REASON, strict=True)
async def test_adiar_envio_registra_justificativa(
    engine: EngineRest,
    ans_probe: AnsEngineProbe,
    start_ans: Callable[..., Any],
) -> None:
    """Humano completa ADIAR_ENVIO => End_AdiadoHumano; submit NAO executado."""
    inst = await start_ans(competencia="2026-ADIAR")
    iid = inst["id"]

    ut = await _drive_to_revisar(engine, ans_probe, iid)
    await engine.complete_task_as_human(
        ut.id,
        {
            "decisao_envio": "ADIAR_ENVIO",
            "justificativa_adiamento": "Aguardando reconciliacao de competencia (teste)",
            "revisor_id": "revisor-sintetico-001",
        },
    )
    await ans_probe.drain()

    ended = await _await_end(engine, iid)
    assert _END_ADIADO in ended, f"ADIAR_ENVIO => End_AdiadoHumano. ended={ended}"
    assert _ST_SUBMETER not in ended, "Adiamento nao transmite a ANS"
    assert not (ended & _ENDS_ENVIO)
    assert ans_probe.has_event(_ANS_COMPLETED, desfecho="adiado_humano")
    await _assert_no_filing_without_human_task(engine, iid)


# ===========================================================================
# Timers de calendario / SLA
# ===========================================================================


@pytest.mark.xfail(reason=_NOTIFY_REGULATORIO_GAP_REASON, strict=True)
async def test_dmn_ans_calendar_resolve_due_date(
    engine: EngineRest,
    ans_probe: AnsEngineProbe,
    start_ans: Callable[..., Any],
) -> None:
    """report_type=RN_124_SIP => ans_calendar resolve due_date; ans_sla resolve duracoes; timers
    existem. NAO relacionado a taxonomia (module docstring) — bloqueado por T2.6-2 (nao mais
    FINDING A, que esta resolvida): _drive_to_revisar nunca alcanca UT_RevisarEnvio no regime
    NAO-PINADO (ver _NOTIFY_REGULATORIO_GAP_REASON)."""
    inst = await start_ans(competencia="2026-CAL")
    iid = inst["id"]

    await _drive_to_revisar(engine, ans_probe, iid)

    job_alerta = await engine.await_timer_job(iid, "BT_DeadlineRisk")
    assert job_alerta.activity_id == "BT_DeadlineRisk"
    job_due = await engine.await_timer_job(iid, "BT_DueDate")
    assert job_due.activity_id == "BT_DueDate"


@pytest.mark.xfail(reason=_NOTIFY_REGULATORIO_GAP_REASON, strict=True)
async def test_timer_deadline_risk_nao_interruptivo(
    engine: EngineRest,
    ans_probe: AnsEngineProbe,
    start_ans: Callable[..., Any],
) -> None:
    """Timer BT_DeadlineRisk (nao-interruptivo): notify_regulatorio recebe task; UT segue aberta."""
    inst = await start_ans(competencia="2026-DLRISK")
    iid = inst["id"]

    await _drive_to_revisar(engine, ans_probe, iid)

    job = await engine.await_timer_job(iid, "BT_DeadlineRisk")
    await engine.execute_job(job.id)
    await ans_probe.drain()

    assert ans_probe.has_event(_ANS_DEADLINE_RISK), "anssubmit.deadline_risk deve ser publicado"

    open_keys = {t.task_definition_key for t in await engine.list_user_tasks(iid)}
    assert _UT_REVISAR in open_keys, "Timer nao-interruptivo nao deve cancelar a User Task"


@pytest.mark.xfail(reason=_NOTIFY_REGULATORIO_GAP_REASON, strict=True)
async def test_timer_due_date_coordenacao_assume(
    engine: EngineRest,
    ans_probe: AnsEngineProbe,
    start_ans: Callable[..., Any],
) -> None:
    """Timer BT_DueDate (interruptivo): UT_RevisarEnvio cancelada; UT_CoordenacaoEnvioAssume criada."""
    inst = await start_ans(competencia="2026-DUEDATE")
    iid = inst["id"]

    await _drive_to_revisar(engine, ans_probe, iid)

    job = await engine.await_timer_job(iid, "BT_DueDate")
    await engine.execute_job(job.id)
    await ans_probe.drain()

    ut_coord = await engine.await_user_task(iid, _UT_COORDENACAO)
    assert "coordenacao-regulatorio" in ut_coord.candidate_groups

    open_keys = {t.task_definition_key for t in await engine.list_user_tasks(iid)}
    assert _UT_REVISAR not in open_keys, "UT_RevisarEnvio deve ser cancelada (interruptivo)"

    ended = await engine.activity_instances_ended(iid)
    assert _ST_SUBMETER not in ended, "Estouro de prazo nunca transmite automaticamente (HITL)"
    await _assert_no_filing_without_human_task(engine, iid)


@pytest.mark.xfail(reason=_NOTIFY_REGULATORIO_GAP_REASON, strict=True)
async def test_coordenacao_assume_e_aprova(
    engine: EngineRest,
    ans_probe: AnsEngineProbe,
    start_ans: Callable[..., Any],
) -> None:
    """Prazo estourado; coordenacao assume e APROVAR_ENVIO => filing apos UT humana (invariante)."""
    inst = await start_ans(competencia="2026-COORD")
    iid = inst["id"]

    await _drive_to_revisar(engine, ans_probe, iid)
    job = await engine.await_timer_job(iid, "BT_DueDate")
    await engine.execute_job(job.id)
    await ans_probe.drain()

    ut_coord = await engine.await_user_task(iid, _UT_COORDENACAO)
    await _aprovar_envio(engine, ut_coord.id, revisor_id="coordenacao-sintetica-001")
    await ans_probe.drain()
    await _correlate_ack(inst["businessKey"])
    await ans_probe.drain()

    ended = await _await_end(engine, iid)
    assert _END_ENVIADO_ACK in ended
    await _assert_no_filing_without_human_task(engine, iid)
    assert ans_probe.has_event(_ANS_COMPLETED, desfecho="enviado_ack")


# ===========================================================================
# GAP-ANS-4: UT_RevisarEnvioJuridico / UT_CorrigirPendenciaEnvio boundary SLA timers (mirror dos
# timers de UT_RevisarEnvio). O timer NAO-INTERRUPTIVO converge no MESMO ST_NotificarDeadlineRisk
# (topico com worker registrado desde t5-workers-f1 — FINDING A resolvida; mas no regime
# NAO-PINADO a UT_RevisarEnvioJuridico em si e inalcancavel por T2.6-2, ver abaixo); o timer
# INTERRUPTIVO vai DIRETO a UT_CoordenacaoEnvioAssume (nao afetado).
# ===========================================================================


@pytest.mark.xfail(reason=_NOTIFY_REGULATORIO_GAP_REASON, strict=True)
async def test_timer_deadline_risk_nao_interruptivo_juridico(
    engine: EngineRest,
    ans_probe: AnsEngineProbe,
    start_ans: Callable[..., Any],
) -> None:
    """Timer BT_DeadlineRiskJuridico (nao-interruptivo): converge em ST_NotificarDeadlineRisk
    (topico compartilhado regulatorio.anssubmit.notify_regulatorio, com worker registrado desde
    t5-workers-f1 — FINDING A resolvida), mesmo a UT_RevisarEnvioJuridico em si NAO precisando de
    notify_regulatorio para ser criada.

    T2.6-2 (ver _NOTIFY_REGULATORIO_GAP_REASON): no regime NAO-PINADO este teste falha ANTES de
    UT_RevisarEnvioJuridico sequer aparecer — sem schema pinado, `schema_valid` e sempre False
    (fail-closed => UT_CorrigirPendenciaEnvio; ver docstring do modulo), entao o
    `await_user_task` ja estoura. O strict-xfail continua satisfeito; este teste precisara do
    probe pinado (`ans_probe_tiss_pinned` + dataset valido) para alcancar a UT juridica.
    """
    inst = await start_ans(
        competencia="2026-DLRJUR", origem_envio="nip_filing", nip_protocolo_origem="NIP-TESTE-DLR"
    )
    iid = inst["id"]

    await ans_probe.drain()
    await engine.await_user_task(iid, _UT_REVISAR_JURIDICO)

    job = await engine.await_timer_job(iid, "BT_DeadlineRiskJuridico")
    await engine.execute_job(job.id)
    await ans_probe.drain()

    assert ans_probe.has_event(_ANS_DEADLINE_RISK), "anssubmit.deadline_risk deve ser publicado"

    open_keys = {t.task_definition_key for t in await engine.list_user_tasks(iid)}
    assert _UT_REVISAR_JURIDICO in open_keys, "Timer nao-interruptivo nao deve cancelar a User Task"


async def test_timer_due_date_pendencia_nip_filing_fail_closed(
    engine: EngineRest,
    ans_probe: AnsEngineProbe,
    start_ans: Callable[..., Any],
) -> None:
    """REGIME NAO-PINADO (T2.6-2 fail-closed — adaptado de
    `test_timer_due_date_coordenacao_assume_juridico`, que regrediu: sem schema pinado,
    `UT_RevisarEnvioJuridico` nunca aparece, entao seu timer `BT_DueDateJuridico` e inalcancavel).
    Realidade atual: nip_filing fail-closed => `UT_CorrigirPendenciaEnvio`; o timer INTERRUPTIVO
    dessa UT e `BT_DueDatePendencia` (Flow_DueDatePendencia_UTCoord, direto — nao bloqueado por
    FINDING A) => cancela a pendencia e cria `UT_CoordenacaoEnvioAssume`. O timer juridico
    equivalente e provado no regime PINADO
    (`test_tiss_pinned_timer_due_date_coordenacao_assume_juridico`). Invariantes mantidas:
    estouro de prazo nunca transmite; filing so apos UT humana."""
    inst = await start_ans(
        competencia="2026-DDJUR", origem_envio="nip_filing", nip_protocolo_origem="NIP-TESTE-DD"
    )
    iid = inst["id"]

    await ans_probe.drain()
    await engine.await_user_task(iid, _UT_CORRIGIR)

    open_keys = {t.task_definition_key for t in await engine.list_user_tasks(iid)}
    assert _UT_REVISAR_JURIDICO not in open_keys, (
        "Sem schema pinado, a revisao juridica e inalcancavel (PENDENTE pre-empta)"
    )

    job = await engine.await_timer_job(iid, "BT_DueDatePendencia")
    await engine.execute_job(job.id)
    await ans_probe.drain()

    ut_coord = await engine.await_user_task(iid, _UT_COORDENACAO)
    assert "coordenacao-regulatorio" in ut_coord.candidate_groups

    open_keys = {t.task_definition_key for t in await engine.list_user_tasks(iid)}
    assert _UT_CORRIGIR not in open_keys, "UT_CorrigirPendenciaEnvio deve ser cancelada (interruptivo)"

    ended = await engine.activity_instances_ended(iid)
    assert _ST_SUBMETER not in ended, "Estouro de prazo nunca transmite automaticamente (HITL)"
    assert not (ended & _ENDS_ENVIO)
    await _assert_no_filing_without_human_task(engine, iid)


async def test_timer_deadline_risk_nao_interruptivo_pendencia(
    engine: EngineRest,
    ans_probe: AnsEngineProbe,
    start_ans: Callable[..., Any],
) -> None:
    """Timer BT_DeadlineRiskPendencia (nao-interruptivo): converge em ST_NotificarDeadlineRisk
    (topico notify_regulatorio, REGISTRADO em t5 — FINDING A resolvido), publica
    anssubmit.deadline_risk (via o handler generico operadora.events.publish) e mantem
    UT_CorrigirPendenciaEnvio aberta. Sem dependencia da FINDING C (nao asserta
    notifications_of_type), logo passa ao vivo (R1 gatekeeper, CIB Seven 2.1.0) apos o fix de A."""
    inst = await start_ans(competencia="2026-DLRPEND", dataset_complete=False)
    iid = inst["id"]

    await ans_probe.drain()
    await engine.await_user_task(iid, _UT_CORRIGIR)

    job = await engine.await_timer_job(iid, "BT_DeadlineRiskPendencia")
    await engine.execute_job(job.id)
    await ans_probe.drain()

    assert ans_probe.has_event(_ANS_DEADLINE_RISK), "anssubmit.deadline_risk deve ser publicado"

    open_keys = {t.task_definition_key for t in await engine.list_user_tasks(iid)}
    assert _UT_CORRIGIR in open_keys, "Timer nao-interruptivo nao deve cancelar a User Task"


async def test_timer_due_date_coordenacao_assume_pendencia(
    engine: EngineRest,
    ans_probe: AnsEngineProbe,
    start_ans: Callable[..., Any],
) -> None:
    """Timer BT_DueDatePendencia (interruptivo): vai DIRETO a UT_CoordenacaoEnvioAssume
    (Flow_DueDatePendencia_UTCoord) — NAO passa por ST_NotificarDeadlineRisk, logo NAO bloqueado
    por FINDING A (verificado tracando o BPMN).

    PORT FIX (T3.1 phase-2, test-only bug — src/** untouched): same `dataset_ref` override
    documented in `test_dados_faltantes_roteiam_para_humano_nunca_auto_rejeita` above
    (`ans_submit.py::prepare_submission` recomputes `dataset_complete = dataset_complete or
    bool(dataset_ref)`, and `start_ans`'s default `dataset_ref` is a truthy stub) — clear it so
    the seeded `dataset_complete=False` actually reaches GW_Admissibilidade and the instance
    reaches UT_CorrigirPendenciaEnvio in the first place.
    """
    inst = await start_ans(competencia="2026-DDPEND", dataset_complete=False, dataset_ref="")
    iid = inst["id"]

    await ans_probe.drain()
    await engine.await_user_task(iid, _UT_CORRIGIR)

    job = await engine.await_timer_job(iid, "BT_DueDatePendencia")
    await engine.execute_job(job.id)
    await ans_probe.drain()

    ut_coord = await engine.await_user_task(iid, _UT_COORDENACAO)
    assert "coordenacao-regulatorio" in ut_coord.candidate_groups

    open_keys = {t.task_definition_key for t in await engine.list_user_tasks(iid)}
    assert _UT_CORRIGIR not in open_keys, "UT_CorrigirPendenciaEnvio deve ser cancelada (interruptivo)"

    ended = await engine.activity_instances_ended(iid)
    assert _ST_SUBMETER not in ended, "Estouro de prazo nunca transmite automaticamente (HITL)"
    await _assert_no_filing_without_human_task(engine, iid)


@pytest.mark.xfail(reason=_NOTIFY_REGULATORIO_GAP_REASON, strict=True)
async def test_catch_all_calendar_roteia_para_humano(
    engine: EngineRest,
    ans_probe: AnsEngineProbe,
    start_ans: Callable[..., Any],
) -> None:
    """report_type desconhecido => ans_calendar catch-all (fonte_regulatoria=REVISAO_HUMANA);
    intencao e admissibilidade SEGUE_ENVIO (demais facts default true) => ST_PrepararDossie =>
    UT_RevisarEnvio. No regime NAO-PINADO `schema_valid` e recomputado SEMPRE False (T2.6-2 — ver
    _NOTIFY_REGULATORIO_GAP_REASON) independente do report_type, entao a admissibilidade roteia
    PENDENTE em vez disso — BLOQUEADO por T2.6-2 (nao mais FINDING A, que esta resolvida)."""
    inst = await start_ans(report_type="RELATORIO_TESTE_INVALIDO", competencia="2026-CATCH")
    iid = inst["id"]

    ut = await _drive_to_revisar(engine, ans_probe, iid)
    assert "regulatorio-ans" in ut.candidate_groups

    job = await engine.await_timer_job(iid, "BT_DueDate")
    assert job.activity_id == "BT_DueDate"

    ended = await engine.activity_instances_ended(iid)
    assert _ST_SUBMETER not in ended, "Tipo desconhecido nunca transmite automaticamente"
    await _assert_no_filing_without_human_task(engine, iid)


# ===========================================================================
# Retry / NACK — FINDING B RESOLVIDA (t9-nack-vars): o subprocess inteiro e alcancavel e estes 2
# testes sao a prova viva dele. Regime PINADO — sem ele nao ha aprovacao humana, logo nao ha NACK.
# ===========================================================================


async def test_nack_entra_em_retry_e_retransmite_sucesso(
    engine: EngineRest,
    ans_probe_tiss_pinned: AnsEngineProbe,
    start_ans: Callable[..., Any],
    tmp_path: Path,
) -> None:
    """submit -> ERR_ANS_PROTOCOLO_NACK (transitorio) => SUB_RetryEnvio; retransmissao OK =>
    enviado_ack.

    Os 3 bloqueios que mantinham este teste em xfail estao fechados (ver FINDING B acima): o
    `WorkerBpmnError` do NACK carrega `protocolo_ans`/`status_envio` pelo canal allowlistado, a
    retransmissao passou a ser uma chamada REAL ao seam de gateway (emitindo o `retransmitido` que
    `GW_RetransmissaoOk` le) e o worker de retransmissao publica a notificacao tipada.

    Regime PINADO (`ans_probe_tiss_pinned` + XSD/XML de fixture em tmp_path): o regime nao-pinado
    nunca chega a `UT_RevisarEnvio` (T2.6-2, `_NOTIFY_REGULATORIO_GAP_REASON`), e sem chegar la nao
    ha aprovacao humana — logo nao ha submit, nao ha NACK e nao ha retry para exercitar.
    `status_envio="nack"` e a diretiva dev/test do LEG DE SUBMIT (`submit_entry` a repassa como
    `requested_outcome`); a perna de retransmissao le a sua propria (`retransmit_outcome`, ausente
    aqui) — e por isso que a retransmissao SUCEDE enquanto o submit NACKa.
    """
    dataset_ref = _write_tiss_dataset(tmp_path, "dataset-nack-ok.xml", _TISS_VALID_XML)
    inst = await start_ans(competencia="2026-NACK-OK", status_envio="nack", dataset_ref=dataset_ref)
    iid = inst["id"]

    ut = await _drive_to_revisar(engine, ans_probe_tiss_pinned, iid)
    await _aprovar_envio(engine, ut.id)
    await ans_probe_tiss_pinned.drain()

    ended_mid = await engine.activity_instances_ended(iid)
    assert _ST_RETRANSMITIR in ended_mid, (
        f"ST_RetransmitirEnvio devia ter executado apos o NACK transitorio. ended={ended_mid}"
    )
    retransmits = ans_probe_tiss_pinned.notifications_of_type("anssubmit.retransmit")
    assert retransmits, "worker de retransmissao deve ter sido executado no subprocess"
    assert retransmits[0]["status_envio"] == "retransmitido"
    assert retransmits[0]["revisor_id"] == "revisor-sintetico-001"
    assert "decisao_envio" in retransmits[0]

    await _correlate_ack(inst["businessKey"])
    await ans_probe_tiss_pinned.drain()

    ended = await _await_end(engine, iid)
    await _assert_no_filing_without_human_task(engine, iid)
    assert _SUB_RETRY in ended, f"SUB_RetryEnvio devia constar no historico. ended={ended}"
    assert _END_ENVIADO_ACK in ended, f"retransmissao OK + ACK => End_EnviadoAck. ended={ended}"
    assert ans_probe_tiss_pinned.has_event(_ANS_SUBMITTED)
    assert ans_probe_tiss_pinned.has_event(_ANS_COMPLETED, desfecho="enviado_ack")


async def test_retry_esgotado_roteia_para_humano(
    engine: EngineRest,
    ans_probe_tiss_pinned: AnsEngineProbe,
    start_ans: Callable[..., Any],
    tmp_path: Path,
) -> None:
    """Retransmissao sempre NACK => DMN ans_retry_policy esgota o retry => ERR_ANS_RETRY_ESGOTADO
    => UT_TratarNack (humano) + anssubmit.failed.

    Mesmo regime PINADO do teste acima; aqui `retransmit_outcome="nack"` mantem a perna de
    retransmissao recusando, ate o catch-all da `ans_retry_policy` devolver `continue_retry=false` e
    o MODELO (nao um worker) lancar `ERR_ANS_RETRY_ESGOTADO` pelo `End_RetryEsgotado`.
    """
    dataset_ref = _write_tiss_dataset(tmp_path, "dataset-nack-esg.xml", _TISS_VALID_XML)
    inst = await start_ans(
        competencia="2026-NACK-ESG",
        status_envio="nack",
        retransmit_outcome="nack",
        dataset_ref=dataset_ref,
    )
    iid = inst["id"]

    ut = await _drive_to_revisar(engine, ans_probe_tiss_pinned, iid)
    await _aprovar_envio(engine, ut.id)
    await ans_probe_tiss_pinned.drain()

    backoffs = 0
    for _ in range(6):
        open_keys = {t.task_definition_key for t in await engine.list_user_tasks(iid)}
        if _UT_TRATAR_NACK in open_keys:
            break
        job = await engine.await_timer_job(iid, _ICE_BACKOFF, attempts=8, delay=0.25)
        await engine.execute_job(job.id)
        backoffs += 1
        await ans_probe_tiss_pinned.drain()

    ut_nack = await engine.await_user_task(iid, _UT_TRATAR_NACK)
    assert "regulatorio-ans" in ut_nack.candidate_groups

    ended = await engine.activity_instances_ended(iid)
    assert _ST_RETRANSMITIR in ended, f"ST_RetransmitirEnvio devia ter executado. ended={ended}"
    assert _END_RETRY_ESGOTADO in ended, f"retry esgotado deve atingir End_RetryEsgotado. ended={ended}"
    assert 1 <= backoffs <= 3, f"loop de retry deve ser bounded (esperado ~3 backoffs); foi {backoffs}"

    retransmits = ans_probe_tiss_pinned.notifications_of_type("anssubmit.retransmit")
    attempts = sorted(int(r["retry_attempt"]) for r in retransmits)
    assert len(retransmits) >= 2, f"o loop deve retransmitir mais de uma vez; foi {len(retransmits)}"
    assert attempts == list(range(1, len(attempts) + 1)), (
        f"retry_attempt deve incrementar 1..N; foi {attempts}"
    )

    assert ans_probe_tiss_pinned.has_event(_ANS_FAILED), (
        "anssubmit.failed deve ser publicado no esgotamento do retry"
    )
    await _assert_no_filing_without_human_task(engine, iid)


# ===========================================================================
# Guards (unit-style, SEM engine) — port rule 1: v2 usa entry functions dict-boundary, nao
# handler factories (make_submit_handler/make_assemble_handler nao existem em v2 main).
# ===========================================================================


def test_err_ans_submit_not_human_recusa_sem_aprovacao() -> None:
    """Invocacao direta de submit_entry sem decisao humana => AnsSubmitNotHumanError.

    Unit-style sobre o handler real (SEM engine). ADAPTED (port rule 1): v2's
    `submit_entry(variables: dict, *, kafka=None) -> dict` (dict-boundary) replaces donor's
    `make_submit_handler(kafka) -> Callable[[ExternalTask], ...]`; the guard raises
    `AnsSubmitNotHumanError` (a `PermissionError` subclass, routed straight to an engine incident
    by harness.py's `_handle`) rather than donor's `WorkerBpmnError(ERR_ANS_SUBMIT_NOT_HUMAN)`. The
    guard's 4 scenarios (a-d) are preserved verbatim — this guard genuinely works end-to-end in v2.
    """
    kafka = FakeKafkaPublisher()

    # (a) decisao_envio ausente -> recusa
    with pytest.raises(AnsSubmitNotHumanError) as exc_a:
        submit_entry({}, kafka=kafka)
    assert "ERR_ANS_SUBMIT_NOT_HUMAN" in str(exc_a.value)

    # (b) decisao_envio != APROVAR_ENVIO -> recusa
    with pytest.raises(AnsSubmitNotHumanError) as exc_b:
        submit_entry({"decisao_envio": "ADIAR_ENVIO"}, kafka=kafka)
    assert "ERR_ANS_SUBMIT_NOT_HUMAN" in str(exc_b.value)

    # (c) APROVAR_ENVIO mas faltando revisor_id -> recusa
    with pytest.raises(AnsSubmitNotHumanError) as exc_c:
        submit_entry({"decisao_envio": "APROVAR_ENVIO"}, kafka=kafka)
    assert "ERR_ANS_SUBMIT_NOT_HUMAN" in str(exc_c.value)
    assert not kafka.published, "Nenhum filing deve ser publicado quando o guard recusa"

    # (d) decisao humana completa -> transmite e carrega revisor_id. T2.6-1 (design §2.A): dev/test
    # injects the LabeledMock gateway; the protocol is the deterministic, non-binding
    # `MOCK-ANS-NAO-VINCULATIVO-{business_key}` — NOT the removed fabricated `ANSPROTO-{sha256}`.
    result = submit_entry(
        {
            "decisao_envio": "APROVAR_ENVIO",
            "revisor_id": "revisor-sintetico-001",
            "tenant_id": "amh",
            "report_type": "RN_124_SIP",
            "competencia": "2026-GUARD",
        },
        kafka=kafka,
        ans_gateway=LabeledMockAnsGatewayTransport(),
    )
    assert result["submitted"] is True
    assert result["revisor_id"] == "revisor-sintetico-001"
    assert result["protocolo_ans"] == f"{MOCK_ANS_PROTOCOL_PREFIX}ANSSUB-amh-RN_124_SIP-2026-GUARD"
    assert not result["protocolo_ans"].startswith("ANSPROTO-")
    assert result["synthetic"] is True and result["vinculativo"] is False


def test_assemble_dataset_incompleto_roteia_a_humano() -> None:
    """Worker assemble com dataset_assembly_failed=true => ERR_ANS_DATASET_INCOMPLETO.

    FLIPPED (2026-08-06): `AnsSubmitInput` ganhou `dataset_assembly_failed` e
    `prepare_submission` passou a levantar o erro MODELADO. A excecao e
    `WorkerBpmnError(ERR_ANS_DATASET_INCOMPLETO)` — NAO `AnsDatasetIncompletoError`
    (um `ValueError`, que o harness demote a incidente cru e perde a rota modelada).
    O boundary `BE_AssembleDatasetIncompleto` em `ST_AssembleDataset` (novo) roteia a
    `UT_CorrigirPendenciaEnvio` — humano, nunca auto-rejeita (contrato :198).
    """
    kafka = FakeKafkaPublisher()

    with pytest.raises(WorkerBpmnError) as exc:
        assemble_entry({"dataset_assembly_failed": True}, kafka=kafka)
    assert exc.value.error_code == "ERR_ANS_DATASET_INCOMPLETO"


# ===========================================================================
# DMN — shape e fail-safe (sem engine; varredura estatica do XML)
# ===========================================================================


def test_ans_admissibility_sem_saida_de_reject() -> None:
    """O dominio de roteamento da ans_submission_admissibility e EXATAMENTE
    {SEGUE_ENVIO, PENDENTE, REVISAO_HUMANA}. NAO existe valor reject/deny (nao-negativa)."""
    from xml.etree import ElementTree as ET

    tree = ET.parse(_DMN_ADMISS)
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

    assert roteamentos == {"SEGUE_ENVIO", "PENDENTE", "REVISAO_HUMANA"}, (
        f"dominio de roteamento inesperado: {roteamentos} — NAO pode conter REJECT/DENY (nao-negativa)"
    )
    joined = " ".join(roteamentos)
    assert "REJECT" not in joined.upper()
    assert "DENY" not in joined.upper()
    assert "NEGAR" not in joined.upper()
    assert last_rule_first_output == "REVISAO_HUMANA", "row catch-all deve rotear a REVISAO_HUMANA"


def test_ans_calendar_catch_all_revisao_humana() -> None:
    """ans_calendar: a row catch-all (ultima) tem fonte_regulatoria=REVISAO_HUMANA com prazos
    conservadores (nunca prazo infinito)."""
    from xml.etree import ElementTree as ET

    tree = ET.parse(_DMN_CALENDAR)
    root = tree.getroot()

    def _local(tag: str) -> str:
        return tag.rsplit("}", 1)[-1]

    rules = [e for e in root.iter() if _local(e.tag) == "rule"]
    assert rules, "ans_calendar deve ter rules"
    last_rule = rules[-1]
    outputs = [c for c in last_rule if _local(c.tag) == "outputEntry"]
    assert len(outputs) == 4, "ans_calendar deve ter 4 outputs (due_date/sla_alerta/periodicidade/fonte)"
    due_date_el = next((c for c in outputs[0] if _local(c.tag) == "text"), None)
    fonte_el = next((c for c in outputs[3] if _local(c.tag) == "text"), None)
    assert due_date_el is not None and due_date_el.text and due_date_el.text.strip().strip('"'), (
        "catch-all deve ter due_date conservador (nunca prazo infinito/vazio)"
    )
    assert fonte_el is not None and fonte_el.text
    # v2's ans_calendar.dmn embeds a full descriptive sentence in fonte_regulatoria (not a bare
    # enum token like the donor's DMN) — adapted (port rule 1, verified against v2 main) to check
    # the semantic prefix rather than exact string equality.
    assert fonte_el.text.strip().strip('"').startswith("REVISAO_HUMANA"), (
        "catch-all do ans_calendar deve rotear a REVISAO_HUMANA"
    )


def test_dmn_typeref_allowlist() -> None:
    """Toda DMN do processo usa typeRef in {string, boolean, integer, long, double, date}."""
    from xml.etree import ElementTree as ET

    allowed = {"string", "boolean", "integer", "long", "double", "date"}
    for dmn_path in (_DMN_CALENDAR, _DMN_SLA, _DMN_ADMISS, _DMN_RETRY):
        tree = ET.parse(dmn_path)
        for el in tree.getroot().iter():
            type_ref = el.get("typeRef")
            if type_ref is not None:
                assert type_ref in allowed, (
                    f"{dmn_path.name}: typeRef invalido '{type_ref}' (allowlist={sorted(allowed)})"
                )
                assert type_ref != "number", f"{dmn_path.name}: 'number' e proibido (use double)"


# ===========================================================================
# Idempotencia (por competencia)
# ===========================================================================


async def test_business_key_uma_instancia_por_competencia(
    engine: EngineRest,
    ans_probe: AnsEngineProbe,
    start_ans: Callable[..., Any],
) -> None:
    """Mesmo business key (report_type+competencia): consultar antes de iniciar; uma instancia
    ativa. Competencia com sufixo unico por run (evita leftovers)."""
    report_type = "RN_124_SIP"
    competencia = f"2026-IDEM-{uuid.uuid4().hex[:4]}"
    business_key = f"ANSSUB-amh-{report_type}-{competencia}"

    first = await start_ans(report_type=report_type, competencia=competencia)
    existing = await engine.find_active_instances(business_key)
    assert len(existing) == 1
    assert existing[0]["id"] == first["id"]


async def test_competencia_diferente_cria_nova_instancia(
    engine: EngineRest,
    ans_probe: AnsEngineProbe,
    start_ans: Callable[..., Any],
) -> None:
    """Competencia diferente => nova instancia (idempotencia e por periodo, nao por tipo)."""
    report_type = "RN_124_SIP"
    run_id = uuid.uuid4().hex[:4]
    comp_a = f"2026-IDA-{run_id}"
    comp_b = f"2026-IDB-{run_id}"
    bk_a = f"ANSSUB-amh-{report_type}-{comp_a}"
    bk_b = f"ANSSUB-amh-{report_type}-{comp_b}"

    first = await start_ans(report_type=report_type, competencia=comp_a)
    second = await start_ans(report_type=report_type, competencia=comp_b)

    assert (await engine.find_active_instances(bk_a))[0]["id"] == first["id"]
    assert (await engine.find_active_instances(bk_b))[0]["id"] == second["id"]
    assert first["id"] != second["id"]

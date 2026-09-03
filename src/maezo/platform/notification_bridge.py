"""NotificationBridge — Cross-process choreography via Kafka → CIB Seven.

Listens to domain-event Kafka topics and starts downstream BPMN processes
via the CIB Seven REST API when handoff conditions are met.

Handoffs:
- INTAKE→RECURSO: when the TISS intake of a glosa APPEAL FILED BY THE PRESTADOR is
  received, starts SP-OP-RECURSO-001 so the operadora can judge and answer it
  (ADR-0040). Registered DORMANT: `agents.events.recurso.intake_recebido` has no
  publisher in `main` yet (OQ-R1) — see `_register_default_handoffs`.
- CONTAS→FRAUDE: when encaminhar_fraude == true, starts SP-OP-FRAUDE-001
  with evidence references and provenance.
- FRAUDE→CRED: when fraud confirmed against a prestador, starts SP-OP-CRED-001.
- FRAUDE→CANCEL/INADIMPLENCIA: when fraud confirmed against a beneficiario,
  starts SP-OP-CANCEL-001 / SP-OP-INADIMPLENCIA-001.
- NIP→ANS-SUBMIT (T2.6-7): when SP-OP-NIP-001 hands off a formal NIP response
  (`operadora.nip.handoff_ans_submit` worker output, `origem_envio == "nip_filing"`),
  starts SP-OP-ANS-SUBMIT-001 so `Flow_GW_NipFiling` (`${origem_envio=='nip_filing'}`,
  BPMN `SP-OP-ANS-SUBMIT-001_Envios_Periodicos_ANS.bpmn:595-596`) routes it to the
  mandatory juridical review `UT_RevisarEnvioJuridico` — this handoff NEVER transmits;
  it only opens the gated pipeline (ADR-0007 HITL pre-filing is unchanged downstream).
- ans.cron_due→ANS-SUBMIT (T2.6-7): when SP-OP-ANS-CRON-001's per-report_type timer
  publishes the typed `ans.cron_due` fact, starts SP-OP-ANS-SUBMIT-001 seeded with
  fail-closed admissibility facts (`Start_DespachoEnvio` BPMN comment) — the human
  resolves the real competência/dataset facts in `UT_CorrigirPendenciaEnvio`.

Design source of truth for the two T2.6-7 handoffs:
`docs/design/T2.6-ans-submission-rescope.md` §1.5 (current-state map) + the T2.6-7 row
of §5's task table (business-key/variable derivation, SME-gated placeholders, and the
explicit "no live trigger today" finding this module now (partially) closes — the
Kafka-publish leg on the `nip.py`/`ans_cron.py` side and the live-consumer wiring that
actually calls `on_event`/`execute_handoff` in production remain open, see this module's
`build_cibseven_process_starter` docstring and the xfail-strict integration coverage in
`tests/integration/processes/`).

T2.6-EB3 (production-wiring hardening — the bridge was dormant fail-OPEN, this closes it):
1. `execute_handoff` is now fail-CLOSED — a genuine start failure PROPAGATES
   (`NotificationBridgeHandoffFailedError`) instead of being swallowed into `.reason`.
2. The two T2.6-7 rules' anchor-field predicates reject an explicit `None` the same as
   absent/blank (`_non_blank` helper) — a naive `bool(str(...).strip())` check would otherwise
   treat `None` as the non-blank string `"None"`.
3. `tenant_id` now flows from a real source for both T2.6-7 rules: `nip.py`'s
   `handoff_ans_submit_entry` reads it off the process instance's own variables; `ans_cron.py`'s
   `trigger_submissions` accepts it as a worker-registration seam (deployment-scoped — see that
   function's docstring for the residual live-wire gap this does NOT close).
4. The 5 pre-existing rules (INTAKE→RECURSO, CONTAS→FRAUDE, FRAUDE→CRED/CANCEL/INADIMPLENCIA) now derive
   `business_key` (they never did before — the fenced starter would have fail-closed refused
   every one of them).
5. `maezo.platform.integrations.notifications_bridge` (new module) is the Kafka consumer
   entry point Helm's `deployment-bridge.yaml` already references — see that module's docstring
   for the live-Kafka-runtime boundary this repo cannot exercise.

London School TDD: the bridge is pure domain logic. Kafka consumption and
CIB Seven HTTP calls are injected as async callables so tests remain fast.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

import structlog

from maezo.tools.mcp_cibseven.transport import (
    AgentDecisionProvenance,
    AuditStartSink,
    CibSevenTransport,
    ProcessInstance,
    start_process_idempotent,
)
from maezo.tools.workers.base import CANCEL_KEY_FAMILY as _CANCEL_KEY_FAMILY
from maezo.tools.workers.base import INADIMPLENCIA_KEY_FAMILY as _INADIMPLENCIA_KEY_FAMILY
from maezo.tools.workers.base import mint_contract_business_key as _shared_mint_contract_business_key
from maezo.tools.workers.base import non_blank as _shared_non_blank
from maezo.tools.workers.base import resolve_fraude_numero_caso as _resolve_fraude_numero_caso

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# T2.6-7 constants — SP-OP-ANS-SUBMIT-001 target + SME-gated placeholders
# ---------------------------------------------------------------------------

#: Target process key for both new T2.6-7 handoffs (contract `SP-OP-ANS-SUBMIT-001.md`).
PROCESS_KEY_ANS_SUBMIT = "SP-OP-ANS-SUBMIT-001"

# ---------------------------------------------------------------------------
# EB-4 event_type reconciliation (option b — repoint to the REAL emitted event shape)
#
# The 5 pre-existing rules previously keyed off event_type strings NO publisher ever emits
# (`contas.glosa_confirmed`, `contas.encaminhar_fraude`, `fraude.acusacao_registrada`) — they were
# DORMANT. The events the source processes ACTUALLY emit are the `operadora.events.publish`
# domain-event trail (BPMN `event_topic` inputParameter): `agents.events.contas.completed` /
# `agents.events.fraude.completed`, each carrying `payload.desfecho` (the routing outcome). This
# repoints the rules onto those real event_type names, keyed on `desfecho` (the real routing
# signal) — see `docs/design/audit-emit-path-wiring.md` §"EB-4 bridge reconciliation".
#
# CANONICAL PATH IS THE IN-FLOW WORKER. The dedicated BPMN service-task workers
# (`operadora.contas.start_recurso` / `operadora.fraude.start_credenciamento` /
# `operadora.fraude.start_contratual`, un-stubbed in EB-4 to run `start_process_idempotent` through
# the same fence — mirroring the merged `inadimplencia.handoff_rescisao`) are the GUARANTEED,
# business-key-CORRECT, PHI-complete handoff, because they run in-flow with full process variables.
# This bridge is the DECOUPLED Kafka mirror; it derives the SAME business keys (identical helpers),
# so if it ever fires it converges idempotently on the SAME instance (never a divergent double-start).
#
# FAIL-CLOSED ANCHOR REQUIREMENT (no divergent-key hazard). The real `agents.events.*.completed`
# payloads are intentionally MINIMAL/PHI-safe (`event_payload_vars`) and do NOT all carry the
# downstream business-key anchors (`numero_guia_tiss`, `prestador_id`, `numero_contrato`) or the
# `entidade_tipo` discriminator. Each repointed predicate therefore ALSO requires its business-key
# anchor to be present/non-blank: against today's minimal events the anchor is absent -> the rule is
# CORRECTLY DORMANT (no start under a wrong/partial key), and it arms automatically iff/when the
# source BPMN enriches `event_payload_vars` with the anchor (a named `spec/` follow-up, OUT of this
# task's mechanical scope — flagged, not silently done). The in-flow worker is live regardless.
CONTAS_COMPLETED_EVENT = "agents.events.contas.completed"
FRAUDE_COMPLETED_EVENT = "agents.events.fraude.completed"

#: INTAKE→RECURSO (ADR-0040 §3.1). Emitted by the TISS intake adapter when a glosa APPEAL FILED
#: BY THE PRESTADOR is received by the operadora. **This event has NO PUBLISHER in `main`** — the
#: adapter is not built by this redesign (OQ-R1, `docs/review-queue.md`); the rule keyed on it is
#: registered DORMANT on purpose, so the future adapter is born against an already-fenced anchor
#: contract. See `_register_default_handoffs`'s own disclosure comment.
RECURSO_INTAKE_EVENT = "agents.events.recurso.intake_recebido"

#: Sentinel competência the BPMN's own `Start_DespachoEnvio` comment documents for the
#: calendar path ("competencia=COMPETENCIA_PENDENTE (sentinela)") — reused verbatim for the
#: nip_filing path below (per-case filings do not carry a periodic competência either; the
#: fail-closed admissibility facts route BOTH paths to a human who resolves the real value).
_COMPETENCIA_PENDENTE = "COMPETENCIA_PENDENTE"

#: SME-GATED PLACEHOLDER (docs/review-queue.md:315; design doc §1.5 table row
#: "RN_209_UTILIZACAO ... Also the slot the NIP-response filing reuses"): the exact
#: `report_type` classification for a NIP-originated filing is NOT yet confirmed by
#: regulatório/jurídico. Reusing the already-documented placeholder here is NOT a new
#: regulatory claim — it is the value the design's own current-state map already records as
#: today's best-available slot; a future SME sign-off only needs to change this one constant.
_ANS_REPORT_TYPE_NIP_FILING = "RN_209_UTILIZACAO"

#: SME-gated placeholder periodicity for the nip_filing path (docs/review-queue.md:315 flags
#: it for regulatório confirmation) — conservative monthly granularity, never derived from the
#: processing clock (constants only preserve business-key idempotency on redelivery).
_PERIODICIDADE_NIP_FILING = "mensal"


# ---------------------------------------------------------------------------
# Domain event types
# ---------------------------------------------------------------------------


@dataclass
class HandoffEvent:
    """A domain event that may trigger a cross-process handoff."""

    event_type: str
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass
class HandoffResult:
    """Result of evaluating and (optionally) executing a handoff."""

    evaluated: bool = False
    handoff_triggered: bool = False
    target_process: str = ""
    variables: dict[str, Any] = field(default_factory=dict)
    process_instance_id: str = ""
    reason: str = ""
    #: F3 MAJOR-2 — the start chokepoint's TYPED verdict for this handoff (a `StartOutcome` value,
    #: or `""` when no start was attempted / the injected starter cannot report one).
    #:
    #: `process_instance_id` alone was structurally unable to distinguish a fresh start from an
    #: idempotent replay from a strict-gate refusal — and when the chokepoint had no id to give it
    #: returned `""`, which `execute_handoff` stamped onto an otherwise "complete"-looking result
    #: (`:801`/`:965`). A consumer branches on THIS field; the blank-id fail-closed guard in
    #: `execute_handoff` makes the empty-id shape unreachable rather than merely unlikely.
    start_outcome: str = ""


# ---------------------------------------------------------------------------
# Internal rule representation
# ---------------------------------------------------------------------------


@dataclass
class _HandoffRule:
    """Internal representation of a handoff rule."""

    event_type: str
    predicate: Callable[[dict[str, Any]], bool]
    target_process: str
    variables_fn: Callable[[dict[str, Any]], dict[str, Any]]


# ---------------------------------------------------------------------------
# T2.6-7 business-key derivation (contract "Business key" / "Variaveis de saida")
# ---------------------------------------------------------------------------


def _ans_cron_business_key(tenant_id: str, report_type: str, competencia: str) -> str:
    """Deterministic SUBMIT business key for the calendar path.

    `ANSSUB-{tenant_id}-{report_type}-{competencia}` (contract `SP-OP-ANS-SUBMIT-001.md`
    "Business key") — one instance per `{report_type, competencia}`. A re-tick for the same
    (unresolved) `COMPETENCIA_PENDENTE` sentinel converges on the SAME pending instance —
    idempotent, never duplicates the filing (BPMN `:461`'s own invariant).
    """
    return f"ANSSUB-{tenant_id}-{report_type}-{competencia}"


def _ans_nip_business_key(tenant_id: str, numero_nip_ans: str) -> str:
    """Deterministic SUBMIT business key for the nip_filing path.

    `ANSSUB-{tenant_id}-nipfiling-{numero_nip_ans}` (contract `SP-OP-ANS-SUBMIT-001.md`
    "Variaveis de saida", `protocolo_ans` row: "correlacionar por nip_protocolo_origem/business
    key ANSSUB-{tenant}-nipfiling-{submit_id}"). `numero_nip_ans` anchors `submit_id` — it is
    the one per-case identifier `nip.handoff_ans_submit` ALWAYS carries; `protocolo_ans` cannot
    anchor it because it may legitimately be absent (GAP-NIP-6, nip.py). One instance per NIP
    case (per-case filing, not periodic — the report_type/competência pair alone would collide
    every nip_filing case sharing a tenant onto a single pending instance).
    """
    return f"ANSSUB-{tenant_id}-nipfiling-{numero_nip_ans}"


# ---------------------------------------------------------------------------
# EB-3 part 2 — None-predicate hardening (fail-closed anchor-field validation)
# ---------------------------------------------------------------------------


def _non_blank(value: Any) -> bool:
    """True iff `value` is a present, non-blank business-key anchor field.

    The naive `bool(str(p.get(field, "")).strip())` idiom treats an EXPLICIT `None` as the
    4-character string `"None"` — `str(None) == "None"`, which `.strip()` leaves non-empty, so
    the naive check returns `True` for a field that is actually absent-as-None. That produces a
    garbage, non-deterministic-looking business key like `ANSSUB-amh-nipfiling-None` instead of
    correctly refusing to start (the SAME failure mode the absent/blank-string cases already
    guard against).

    Fail-closed: `None` is treated EXACTLY like an absent or blank field — both fail this check,
    so the owning rule's predicate returns `False` and the handoff does not trigger (no start,
    no garbage business key). A non-`None`, non-blank value round-trips through `str(...).strip()`
    unchanged, same as before.

    EB-4 R1 follow-up: the implementation is now the SHARED `tools.workers.base.non_blank` — the
    single source of truth the 3 fenced-start handoff workers (`contas.start_recurso`,
    `fraude.start_credenciamento`/`start_contratual`) also use for their anchor/tenant guards, so
    the bridge predicates and the in-flow workers can never drift on what counts as a valid anchor.
    """
    return _shared_non_blank(value)


def _anchored(payload: dict[str, Any], *anchor_fields: str) -> bool:
    """True iff `tenant_id` AND every named business-key anchor field is present/non-blank.

    t2-notify-integrity item 3 (bridge tenant anchor): ALL of the in-flow fenced-start workers
    (`contas.start_recurso`/`start_fraude`, `fraude.start_credenciamento`/`start_contratual`)
    guard `tenant_id` via the shared `non_blank` and REFUSE (incident) a tenant-less start — but
    the bridge's 7 predicates historically checked only their per-rule anchors, so a tenant-less
    payload minted a degenerate business key (`FRAUDE--{caso}`, `ANSSUB--nipfiling-{nip}`, ...):
    a tenant-scoping/cockpit orphan whose key DIVERGES from the canonical in-flow key minted after
    the tenant is fixed — exactly the double-start divergence the convergence design exists to
    prevent. This ONE shared helper (used by every default rule) restores fail-closed symmetry:
    no tenant -> the rule stays DORMANT, mirroring the workers' refusal. `tenant_id` is required
    here structurally (not listed per rule) so no future rule can forget it.
    """
    return _non_blank(payload.get("tenant_id")) and all(
        _non_blank(payload.get(field)) for field in anchor_fields
    )


# ---------------------------------------------------------------------------
# T2.6-7 variable derivation (NIP handoff + ans.cron_due fact -> SUBMIT seed variables)
# ---------------------------------------------------------------------------


def _ans_submit_variables_from_nip_handoff(payload: dict[str, Any]) -> dict[str, Any]:
    """Map `nip.handoff_ans_submit`'s worker output (`nip.py:327-355`) onto
    SP-OP-ANS-SUBMIT-001 seed variables.

    `origem_envio="nip_filing"` drives `Flow_GW_NipFiling` straight to the mandatory
    `UT_RevisarEnvioJuridico` — this mapping NEVER transmits, it only opens the gated
    pipeline. Admissibility facts are seeded FAIL-CLOSED (`False`) — mirrors the BPMN's own
    documented calendar-path convention (`Start_DespachoEnvio` comment); a human resolves them
    in `UT_CorrigirPendenciaEnvio`/`UT_RevisarEnvioJuridico`, never auto-passed.

    `report_type`/`competencia`/`periodicidade` are SME-gated placeholders (module-level
    constants, see their docstrings) — CONSTANTS, never derived from the processing clock, so
    redelivery of the same NIP case always reconstructs the same business key.

    `tenant_id` (EB-3 part 3 — SOURCE fixed): `handoff_ans_submit`'s return dict now carries a
    real `tenant_id` (`nip.py`'s `handoff_ans_submit_entry` reads it off the SP-OP-NIP-001
    process instance's own variables — every process instance carries `tenant_id` as a top-level
    variable, the SAME convention every other `tenant_id: str` dataclass field in `nip.py`'s
    sibling worker modules already relies on; `handoff_ans_submit_entry` simply wasn't reading it
    before this fix). This mapping still defaults to `""` fail-closed ONLY when the upstream
    payload genuinely omits the key (e.g. a caller that predates the T2.6-EB3 fix, or a
    malformed/legacy event) — never fabricated here.
    """
    tenant_id = str(payload.get("tenant_id", ""))
    numero_nip_ans = str(payload.get("numero_nip_ans", ""))
    protocolo_ans = payload.get("protocolo_ans")
    return {
        "tenant_id": tenant_id,
        "report_type": _ANS_REPORT_TYPE_NIP_FILING,
        "competencia": _COMPETENCIA_PENDENTE,
        "periodicidade": _PERIODICIDADE_NIP_FILING,
        "origem_envio": "nip_filing",
        "nip_protocolo_origem": protocolo_ans if isinstance(protocolo_ans, str) else "",
        "numero_nip_ans": numero_nip_ans,
        "dataset_complete": False,
        "schema_valid": False,
        "lgpd_anonimizado": False,
        "dataset_ref": "",
        "business_key": _ans_nip_business_key(tenant_id, numero_nip_ans),
    }


def _ans_submit_variables_from_cron_due(payload: dict[str, Any]) -> dict[str, Any]:
    """Map the `ans.cron_due` fact (SP-OP-ANS-CRON-001's per-report_type timer dispatch,
    `ans_cron.py:79-108`) onto SP-OP-ANS-SUBMIT-001 seed variables.

    `origem_envio="calendario"` (the fact's own literal). Admissibility facts are seeded
    FAIL-CLOSED (`False`) — same `Start_DespachoEnvio` BPMN convention as the nip_filing rule
    above. `competencia` passes through the fact's own value (today always the literal
    `COMPETENCIA_PENDENTE` sentinel baked into the BPMN's `camunda:inputParameter`s — FINDING #1,
    `tests/integration/processes/test_sp_op_ans_cron_001.py`), falling back to the sentinel only
    if the fact is missing it entirely (never silently invents a real period).

    `tenant_id` (EB-3 part 3 — SOURCE fixed at the generic publisher; t2-notify-integrity item 3
    follow-up CLOSED the former live-wire residual): `ans_cron.py`'s `trigger_submissions`
    accepts a `tenant_id` KEYWORD parameter but is UNREACHABLE in the real deployed BPMN
    (FINDING #1c — the 5 `SP-OP-ANS-CRON-001-*` definitions bind `ST_PublishCronDue*` directly to
    the generic `operadora.events.publish` topic with literal `camunda:inputParameter`s whose
    `event_payload_vars` do not list `tenant_id`). The generic publisher
    (`events.py::make_publish_event_handler`) therefore now stamps the DEPLOYMENT's own tenant
    (`register_events_workers`' `tenant_id` seam, sourced from
    `WorkerRuntimeSettings.tenant_id` — the live composition root
    `worker_runtime/service.py::register_default_workers` already threads it) into any payload
    the process variables left tenant-less, via `setdefault` (never overriding a
    process-var-sourced tenant like SP-OP-NIP-001's). Deployment truth, not fabrication: there is
    no PER-INSTANCE tenant on a TimerStartEvent-triggered scheduler — the per-tenant worker
    deployment's identity IS the fact's tenant. So the REAL live fact now carries `tenant_id`
    (`test_sp_op_ans_cron_001.py` pins it) and this rule ARMS under the tenant anchor with a
    proper `ANSSUB-{tenant}-...` key. HONEST no-seam boundary: a registration WITHOUT the seam
    (legacy/tests) still emits tenant-less facts — this mapping then defaults to `""` fail-closed
    and the rule's `_anchored` predicate keeps it DORMANT (never an `ANSSUB--...` orphan);
    nothing is fabricated here.
    """
    tenant_id = str(payload.get("tenant_id", ""))
    report_type = str(payload.get("report_type", ""))
    competencia = str(payload.get("competencia", "")).strip() or _COMPETENCIA_PENDENTE
    periodicidade = str(payload.get("periodicidade", ""))
    return {
        "tenant_id": tenant_id,
        "report_type": report_type,
        "competencia": competencia,
        "periodicidade": periodicidade,
        "origem_envio": "calendario",
        "dataset_complete": False,
        "schema_valid": False,
        "lgpd_anonimizado": False,
        "dataset_ref": "",
        "business_key": _ans_cron_business_key(tenant_id, report_type, competencia),
    }


# ---------------------------------------------------------------------------
# EB-3 part 4 — business_key derivation for the 5 pre-existing bridge rules
#
# Added so the fenced starter (`build_cibseven_process_starter`) can actually start these five
# rules: it fail-closed REFUSES any rule whose variables lack `business_key`
# (`NotificationBridgeMissingBusinessKeyError`) — before this fix, NONE of the 5 pre-existing
# rules (INTAKE→RECURSO, CONTAS→FRAUDE, FRAUDE→CRED, FRAUDE→CANCEL, FRAUDE→INADIMPLENCIA) set
# one, so a fenced-starter start attempt on any of them raised (and, under the OLD swallowing
# `execute_handoff` — see `NotificationBridgeHandoffFailedError`'s docstring — was silently
# absorbed): these five handoffs have never actually started a process through the fenced path.
# Each derivation mirrors the ANSSUB pattern (`_ans_cron_business_key`/`_ans_nip_business_key`
# above): a plain, deterministic f-string over the exact shape each target process's own
# contract documents under "Business key (idempotencia)".
# ---------------------------------------------------------------------------


def _recurso_business_key(tenant_id: str, numero_guia_tiss: str, glosa_id: str) -> str:
    """`RECURSO-{tenant_id}-{numero_guia_tiss}-{glosa_id}` (contract `SP-OP-RECURSO-001.md`
    "Business key (idempotencia)") — one recurso per glosa per guia TISS; a re-intake of the same
    appeal (the prestador re-transmitting, or the operation opening it manually) converges on the
    SAME active instance."""
    return f"RECURSO-{tenant_id}-{numero_guia_tiss}-{glosa_id}"


def _fraude_business_key(tenant_id: str, numero_caso: str) -> str:
    """`FRAUDE-{tenant_id}-{numero_caso}` (contract `SP-OP-FRAUDE-001.md` "Business key
    (idempotencia)") — one active investigation instance per case."""
    return f"FRAUDE-{tenant_id}-{numero_caso}"


def _fraude_numero_caso_for_contas_handoff(payload: dict[str, Any]) -> str:
    """Resolve the FRAUDE-001 `numero_caso` business-key anchor for a CONTAS→FRAUDE handoff.

    Contract `SP-OP-FRAUDE-001.md` "Business key (idempotencia)": `numero_caso` is the case's
    stable identifier and — pending a product/regulatory sign-off (contract's own
    DRAFT/verify note) — is documented to be "a chave fornecida pelo intake" until then. For
    THIS bridge rule the notification_bridge itself is the intake (it is what starts
    SP-OP-FRAUDE-001 from a CONTAS forward), so when the CONTAS payload does not already carry
    an assigned `numero_caso`, this falls back to `prestador_id` — the entity under
    investigation — which honors the contract's OWN documented idempotency intent ("reenvio do
    mesmo caso... retorna a instancia ativa" for repeated forwards on the SAME prestador) instead
    of minting a fresh, non-deterministic case id on every forward.

    Thin wrapper over the SHARED `maezo.tools.workers.base.resolve_fraude_numero_caso` — see
    that function's docstring for the full derivation contract. Delegating (rather than
    re-implementing) is what guarantees a BYTE-IDENTICAL business key with
    `contas._fraude_numero_caso_for_handoff` for ALL input types (BK convergence is the L0
    requirement), not just coincidentally-matching logic that can silently drift.
    """
    return _resolve_fraude_numero_caso(payload)


def _cred_business_key(tenant_id: str, prestador_id: str) -> str:
    """`CRED-{tenant_id}-{prestador_id}` (contract `SP-OP-CRED-001.md` "Business key
    (idempotencia)") — one active (des)credenciamento cycle per prestador. The contract also
    documents a per-protocolo variant (`CRED-{tenant}-{prestador}-{protocolo_cred}`) for
    multi-cycle cases; not used here because this bridge rule's payload does not carry a
    `protocolo_cred`."""
    return f"CRED-{tenant_id}-{prestador_id}"


def _cancel_business_key(tenant_id: str, numero_contrato: str) -> str:
    """`CANCEL-{tenant_id}-{numero_contrato}` (contract `SP-OP-CANCEL-001.md` "Business key
    (idempotencia)") — one active cancelamento/rescisao instance per contract.

    Delegates to the SHARED `base.mint_contract_business_key` (the third of the three CANCEL
    composers). NO matricula fallback here — this rule's predicate already requires a non-blank
    `numero_contrato`. `inadimplencia`'s composer DOES fall back, which is why the
    anti-dupla-terminacao guard now sweeps every derivable form rather than one (B-2).

    WHY THE MINT COMPOSER AND NOT THE BARE FORMATTER (DL-0043 counter completeness). This used to
    call `base.contract_business_key` directly, which bypassed the shadow counter — so the
    `anchor="contrato"` series under-counted by exactly the CANCEL keys minted on this bridge
    path (and this path really does start SP-OP-CANCEL-001: `_register_default_handoffs`'
    FRAUDE->CANCEL rule below). Routing through `mint_contract_business_key` with an EMPTY
    `matricula_beneficiario` completes the series without changing a byte of output: the rule's
    `variables_fn` runs ONLY after `_anchored(p, "numero_contrato")` passed, so the argument is
    always a non-blank string and `resolve_contract_identity` short-circuits on it, returning
    `(numero_contrato, "contrato")` in every policy mode."""
    return _shared_mint_contract_business_key(
        _CANCEL_KEY_FAMILY,
        tenant_id,
        numero_contrato=numero_contrato,
        matricula_beneficiario="",
    )


def _inadimplencia_business_key(tenant_id: str, numero_contrato: str) -> str:
    """`INAD-{tenant_id}-{numero_contrato}` (contract `SP-OP-INADIMPLENCIA-001.md` "Business key
    (idempotencia) — coordenada com CANCEL-001") — a prefix DISTINCT from CANCEL for the SAME
    contract (the two processes coordinate via topology + a runtime active-instance check, not
    via a shared business key — see the contract's own "Coordenacao com CANCEL-001" note).

    Same `mint_contract_business_key` routing, and the same byte-identity argument, as
    `_cancel_business_key` above — the INAD half of the DL-0043 counter completeness fix."""
    return _shared_mint_contract_business_key(
        _INADIMPLENCIA_KEY_FAMILY,
        tenant_id,
        numero_contrato=numero_contrato,
        matricula_beneficiario="",
    )


# ---------------------------------------------------------------------------
# EB-3 part 1 — fail-closed execute_handoff error type
# ---------------------------------------------------------------------------


class NotificationBridgeHandoffFailedError(RuntimeError):
    """Raised by `execute_handoff`/`on_event` when a MATCHED rule's process start genuinely
    fails (the starter raised) — fail-closed (EB-3 part 1).

    Before this fix, `execute_handoff` caught `Exception` broadly around the starter call and
    only recorded `result.reason = f"CIB Seven start failed: {exc}"`, returning a "successful"
    HandoffResult with an empty `process_instance_id` — a real start failure (a missing
    business key, a durable-audit-persistence failure, a transport/HTTP error against CIB Seven)
    was SILENTLY SWALLOWED: the caller (a Kafka consumer) would see `evaluated=True,
    handoff_triggered=True` and move on, with no signal that the process never actually started.
    That is fail-OPEN — exactly backwards for a chokepoint whose entire purpose (ADR-0007/T-C2)
    is to make a start failure impossible to miss.

    Now the starter's exception PROPAGATES (wrapped here, `raise ... from exc` preserves the
    original traceback/cause) instead of being absorbed into `.reason`. This is deliberately
    DISTINCT from the two cases that must NOT raise:
      - "no matching rule" (`result.handoff_triggered is False`) — a legitimate skip, unrelated
        to any process-start attempt; `execute_handoff` returns immediately, unchanged.
      - idempotent replay (`start_process_idempotent`'s `find_active_instance` hit) — the
        starter returns the EXISTING instance normally (no exception at all), so a duplicate
        delivery of the same business key is indistinguishable from a fresh success here; it is
        not a failure and never raises.
    A caller (the Kafka consumer entry point, `maezo.platform.integrations.notifications_bridge`)
    that lets this propagate turns a matched-but-failed handoff into a visible incident (the
    message is not acked/committed, so it can be retried/escalated) instead of a silently
    "successful" no-op.
    """

    def __init__(self, target_process: str, cause: BaseException) -> None:
        self.target_process = target_process
        super().__init__(
            f"notification_bridge: handoff to target_process={target_process!r} failed "
            f"(fail-closed, not swallowed): {cause}"
        )


# ---------------------------------------------------------------------------
# NotificationBridge
# ---------------------------------------------------------------------------


class NotificationBridge:
    """Listens to domain-event Kafka topics and starts CIB Seven processes.

    Handoff rules are defined by their event_type prefix, a predicate
    over the payload, and the target process key + variable mapping.

    Multiple rules may match the same event_type (e.g., fraude.acusacao_registrada
    triggers both CRED, CANCEL, and INADIMPLENCIA for different entity types).

    Injectables (London School):
    - cibseven_starter: async callable(process_key, variables) -> instance_id
    """

    # List of (event_type, predicate, process_key, variables_fn)
    _HANDOFF_RULES: list[_HandoffRule] = []

    def __init__(
        self,
        cibseven_starter: Callable[..., Any] | None = None,
    ) -> None:
        """Initialize the bridge.

        Args:
            cibseven_starter: Callable(process_key, variables) that starts a CIB Seven
                              process. Production MUST inject a starter built by
                              `build_cibseven_process_starter` (below) — the ONLY sanctioned
                              path, since it funnels every start through the fenced
                              `start_process_idempotent` chokepoint (ADR-0007/T-C2: durable
                              audit BEFORE effect, idempotent by business key). NEVER inject a
                              raw engine call directly. In tests it can be a simple spy.
        """
        self._starter = cibseven_starter or _noop_starter
        self._rules: list[_HandoffRule] = []
        self._register_default_handoffs()

    # -------------------------------------------------------------------
    # Default handoff rules (cross-process choreography)
    # -------------------------------------------------------------------

    def _register_default_handoffs(self) -> None:
        """Register the seven cross-process handoff rules from the spec.

        INTAKE→RECURSO, CONTAS→FRAUDE, FRAUDE→CRED, FRAUDE→CANCEL/INADIMPLENCIA (5, pre-T2.6-7)
        + NIP→ANS-SUBMIT, ans.cron_due→ANS-SUBMIT (2, T2.6-7).

        EB-3 part 4: all 5 pre-existing rules now derive `business_key` (see the module-level
        `_recurso_business_key`/`_fraude_business_key`/`_cred_business_key`/
        `_cancel_business_key`/`_inadimplencia_business_key` helpers) and read `tenant_id` from
        the payload — required for the fenced starter (`build_cibseven_process_starter`) to
        start them at all; it fail-closed refuses any rule whose variables lack `business_key`.
        """
        # INTAKE→RECURSO (ADR-0040 §3.1): the TISS intake of a glosa APPEAL FILED BY THE PRESTADOR
        # starts SP-OP-RECURSO-001. This REPLACES the old CONTAS→RECURSO edge, which encoded the
        # inverted perspective — it treated RECURSO as something CONTAS hands off when the payer
        # itself decides to appeal. The payer does not appeal its own glosa; it RECEIVES the
        # appeal and answers it. Fail-closed anchor requirement (see module reconciliation note
        # + `_anchored` — tenant_id is required structurally, t2-notify-integrity item 3):
        # `tenant_id` + `numero_guia_tiss` + `glosa_id` must be present (the business-key anchors)
        # or the rule refuses — never a divergent-key start.
        #
        # DORMANT ON PURPOSE, AND SAID SO (OQ-R1): `agents.events.recurso.intake_recebido` HAS NO
        # PUBLISHER IN `main`. The TISS intake adapter that would emit it is NOT built here; this
        # redesign specifies only the SHAPE of the inbound contract and the bridge rule that
        # consumes it. The rule is registered anyway so the future adapter is born against an
        # already-fenced anchor contract (`_anchored` + `NotificationBridgeMissingBusinessKeyError`)
        # instead of bringing its own. This is the SAME dormancy mechanism the previous rule had —
        # `agents.events.contas.completed`'s minimal payload never carried the anchors — only the
        # REASON changes: from "the payload is thin" to "the publisher does not exist". No
        # synthetic publisher, no stub, no `event_published=True`: the absence is declared here,
        # asserted by `test_regra_intake_recurso_e_dormente_ate_o_adaptador_existir` (which goes
        # RED the day a publisher appears, forcing this comment to be updated in the same PR), and
        # dated in `docs/review-queue.md`. Until the adapter exists, RECURSO-001 is started by
        # Marina or by the operation — exactly today's maturity.
        self.register_handoff(
            event_type=RECURSO_INTAKE_EVENT,
            predicate=lambda p: _anchored(p, "tenant_id", "numero_guia_tiss", "glosa_id"),
            target_process="SP-OP-RECURSO-001",
            variables_fn=lambda p: {
                "tenant_id": str(p.get("tenant_id", "")),
                "glosa_id": p.get("glosa_id", ""),
                "numero_guia_tiss": p.get("numero_guia_tiss", ""),
                "numero_lote_tiss": p.get("numero_lote_tiss", ""),
                "prestador_id": p.get("prestador_id", ""),
                "glosa_type": p.get("glosa_type", ""),
                "glosa_reason_code": p.get("glosa_reason_code", ""),
                "valor_glosado_brl": p.get("valor_glosado_brl", 0.0),
                "codigo_procedimento_tuss": p.get("codigo_procedimento_tuss", ""),
                "documentos_recurso_refs": p.get("documentos_recurso_refs", []),
                "data_recebimento_recurso_iso": p.get("data_recebimento_recurso_iso", ""),
                "data_vencimento": p.get("data_vencimento", ""),
                # FACTS, not tautologies. `glosa_existe` was hard-coded `True` on the old edge
                # ("it only reaches the handoff after RECORRER on an active glosa") — an
                # assumption that does not survive an externally-filed appeal, whose `glosa_id`
                # may reference nothing the payer ever minted. It is now echoed from the envelope
                # and FAIL-CLOSED to `False` when absent, which routes to `ANALISE_HUMANA` via
                # `recurso_admissibility` (row `r_glosa_inexistente_humano`) rather than asserting
                # a glosa exists. `dentro_prazo_recurso` likewise stops being the day-0 tautology
                # and is left to `validate_recurso` / the envelope; absent => `False` =>
                # `r_fora_prazo_humano` => a human looks at it.
                "glosa_existe": bool(p.get("glosa_existe", False)),
                "dentro_prazo_recurso": bool(p.get("dentro_prazo_recurso", False)),
                "documentacao_recurso_completa": bool(p.get("documentacao_recurso_completa", False)),
                "business_key": _recurso_business_key(
                    str(p.get("tenant_id", "")),
                    str(p.get("numero_guia_tiss", "")),
                    str(p.get("glosa_id", "")),
                ),
            },
        )

        # CONTAS→FRAUDE: ARMED (commit 9cc8aaa, t4-bridge-arming — Phase 3 landed). The CONTAS
        # BPMN's `ST_PublishEncaminhadaFraude` now emits `agents.events.contas.completed` with
        # desfecho=`encaminhada_fraude` (`event_payload_vars: tenant_id,numero_lote_tiss,
        # prestador_id` — the `prestador_id` anchor this predicate requires IS present), and
        # `contas.start_fraude` (worker module, ~:623) is the LIVE in-flow worker — the canonical
        # path per the module preamble above. This bridge rule is the DECOUPLED Kafka-mirror
        # redundancy: it derives the SAME business key as the in-flow worker (both delegate to
        # the shared `tools.workers.base.resolve_fraude_numero_caso`), so the two paths CONVERGE
        # on the SAME FRAUDE-001 instance, never a divergent double-start.
        self.register_handoff(
            event_type=CONTAS_COMPLETED_EVENT,
            predicate=lambda p: p.get("desfecho") == "encaminhada_fraude" and _anchored(p, "prestador_id"),
            target_process="SP-OP-FRAUDE-001",
            variables_fn=lambda p: {
                "tenant_id": str(p.get("tenant_id", "")),
                "origem_encaminhamento": "contas",
                "encaminhado_por_id": p.get("analista_id", ""),
                "numero_lote_tiss": p.get("numero_lote_tiss", ""),
                "prestador_id": p.get("prestador_id", ""),
                "evidencia_refs": p.get("evidencia_refs", []),
                "indicadores_presentes": p.get("indicadores_presentes", []),
                "business_key": _fraude_business_key(
                    str(p.get("tenant_id", "")), _fraude_numero_caso_for_contas_handoff(p)
                ),
            },
        )

        # FRAUDE→CRED: on the REAL `agents.events.fraude.completed`
        # (desfecho=encaminhado_credenciamento — the BPMN's own routing encoding, replaces the
        # never-emitted decisao_fraude/entidade_tipo pair). Fail-closed anchor: `prestador_id`.
        self.register_handoff(
            event_type=FRAUDE_COMPLETED_EVENT,
            predicate=lambda p: (
                p.get("desfecho") == "encaminhado_credenciamento" and _anchored(p, "prestador_id")
            ),
            target_process="SP-OP-CRED-001",
            variables_fn=lambda p: {
                "tenant_id": str(p.get("tenant_id", "")),
                "prestador_id": p.get("prestador_id", ""),
                "numero_caso": p.get("numero_caso", ""),
                "bundle_root": p.get("bundle_root", ""),
                "destino_referral": p.get("destino_referral", {}),
                "business_key": _cred_business_key(
                    str(p.get("tenant_id", "")), str(p.get("prestador_id", ""))
                ),
            },
        )

        # FRAUDE→CANCEL: on the REAL `agents.events.fraude.completed`
        # (desfecho=encaminhado_contratual — covers BOTH beneficiario and contrato, matching the
        # BPMN's single `ST_StartContratual` handoff). Fail-closed anchor: `numero_contrato`.
        self.register_handoff(
            event_type=FRAUDE_COMPLETED_EVENT,
            predicate=lambda p: (
                p.get("desfecho") == "encaminhado_contratual" and _anchored(p, "numero_contrato")
            ),
            target_process="SP-OP-CANCEL-001",
            variables_fn=lambda p: {
                "tenant_id": str(p.get("tenant_id", "")),
                "numero_contrato": p.get("numero_contrato", ""),
                "beneficiario_pseudo_id": p.get("beneficiario_pseudo_id", ""),
                "numero_caso": p.get("numero_caso", ""),
                "bundle_root": p.get("bundle_root", ""),
                "business_key": _cancel_business_key(
                    str(p.get("tenant_id", "")), str(p.get("numero_contrato", ""))
                ),
            },
        )

        # FRAUDE→INADIMPLENCIA: secondary handoff for CONTRATO fraud only. Same real completed
        # event (desfecho=encaminhado_contratual) but ALSO requires `entidade_tipo == "contrato"`
        # — ARMED (commit 9cc8aaa, t4-bridge-arming): the FRAUDE BPMN's
        # `ST_PublishEncaminhadoContratual` now includes `entidade_tipo` in its
        # `event_payload_vars` (a real process variable carried since intake — it also drives the
        # BPMN's own CRED/CONTRATUAL gateway routing), so the real completed-event payload DOES
        # carry the discriminator this predicate requires. Matches the in-flow `start_contratual`
        # worker, which starts INADIMPLENCIA only for a contrato — this bridge rule is the
        # decoupled Kafka-mirror redundancy, converging on the same business key.
        self.register_handoff(
            event_type=FRAUDE_COMPLETED_EVENT,
            predicate=lambda p: (
                p.get("desfecho") == "encaminhado_contratual"
                and p.get("entidade_tipo") == "contrato"
                and _anchored(p, "numero_contrato")
            ),
            target_process="SP-OP-INADIMPLENCIA-001",
            variables_fn=lambda p: {
                "tenant_id": str(p.get("tenant_id", "")),
                "numero_contrato": p.get("numero_contrato", ""),
                "beneficiario_pseudo_id": p.get("beneficiario_pseudo_id", ""),
                "numero_caso": p.get("numero_caso", ""),
                "business_key": _inadimplencia_business_key(
                    str(p.get("tenant_id", "")), str(p.get("numero_contrato", ""))
                ),
            },
        )

        # NIP→ANS-SUBMIT (T2.6-7): formal NIP response handed off for official filing.
        # Fail-closed predicate: the origin marker AND the anchors (`tenant_id` structurally via
        # `_anchored` + numero_nip_ans, the business-key anchors) must be present/non-blank, or
        # the handoff does not trigger — a malformed/incomplete event never starts a process
        # with a garbage/non-deterministic business key (t2-notify-integrity item 3: a tenant-less
        # payload previously minted the literal `ANSSUB--nipfiling-...`-class key `_non_blank`'s
        # own docstring warns about). EB-3 part 2: `_non_blank` also fail-closed rejects an
        # EXPLICIT `None` (not just absent/blank-string), which the naive
        # `bool(str(...).strip())` idiom would otherwise treat as the non-blank string `"None"`.
        self.register_handoff(
            event_type="nip.handoff_ans_submit",
            predicate=lambda p: p.get("origem_envio") == "nip_filing" and _anchored(p, "numero_nip_ans"),
            target_process=PROCESS_KEY_ANS_SUBMIT,
            variables_fn=_ans_submit_variables_from_nip_handoff,
        )

        # ans.cron_due→ANS-SUBMIT (T2.6-7): per-report_type scheduler tick.
        # Fail-closed predicate: report_type must be present/non-blank (no sensible
        # calendar/business-key derivation without it), plus `tenant_id` structurally via
        # `_anchored` (t2-notify-integrity item 3 — no more `ANSSUB--{report_type}-...`
        # degenerate keys). LIVE-WIRE ARMED (item 3 follow-up): the generic publisher now stamps
        # the deployment tenant into tenant-less payloads (`events.py` `deployment_tenant_id`
        # seam, setdefault-only — see `_ans_submit_variables_from_cron_due`'s docstring), so the
        # real monthly tick carries `tenant_id` and this rule fires with a proper
        # `ANSSUB-{tenant}-...` key; a registration WITHOUT that seam still emits tenant-less
        # facts and the rule stays honestly DORMANT (fail-closed, never an orphan start). EB-3
        # part 2: `_non_blank` also fail-closed rejects an EXPLICIT `None` report_type (see
        # `nip.handoff_ans_submit` rule above).
        self.register_handoff(
            event_type="ans.cron_due",
            predicate=lambda p: _anchored(p, "report_type"),
            target_process=PROCESS_KEY_ANS_SUBMIT,
            variables_fn=_ans_submit_variables_from_cron_due,
        )

    # -------------------------------------------------------------------
    # Public API
    # -------------------------------------------------------------------

    def register_handoff(
        self,
        event_type: str,
        predicate: Callable[[dict[str, Any]], bool],
        target_process: str,
        variables_fn: Callable[[dict[str, Any]], dict[str, Any]],
    ) -> None:
        """Register a handoff rule.

        Args:
            event_type: The domain event type (e.g., 'contas.glosa_confirmed').
            predicate: Returns True when the handoff condition is satisfied.
            target_process: CIB Seven process definition key (e.g., 'SP-OP-RECURSO-001').
            variables_fn: Maps the event payload to process start variables.
        """
        rule = _HandoffRule(
            event_type=event_type,
            predicate=predicate,
            target_process=target_process,
            variables_fn=variables_fn,
        )
        self._rules.append(rule)
        logger.debug(
            "notification_bridge.handoff_registered",
            event_type=event_type,
            target_process=target_process,
        )

    def evaluate(self, event: HandoffEvent) -> HandoffResult:
        """Evaluate whether a handoff should be triggered.

        Returns the FIRST matching rule. Use evaluate_all() to get
        all matching rules (e.g., for events that trigger multiple processes).

        Args:
            event: The domain event to evaluate.

        Returns:
            HandoffResult with evaluation outcome.
        """
        matches = self._find_matches(event)
        if matches:
            rule, variables = matches[0]
            return self._build_result(rule, variables)
        return HandoffResult(
            evaluated=True,
            handoff_triggered=False,
            reason=f"No handoff rule matched for {event.event_type}",
        )

    def evaluate_all(self, event: HandoffEvent) -> list[HandoffResult]:
        """Evaluate all matching handoff rules for an event.

        Use this when an event may trigger multiple downstream processes
        (e.g., fraude.acusacao_registrada → CRED + CANCEL + INADIMPLENCIA).

        Args:
            event: The domain event to evaluate.

        Returns:
            List of HandoffResult (at least one, with handoff_triggered=False
            if no rules matched).
        """
        matches = self._find_matches(event)
        if not matches:
            return [
                HandoffResult(
                    evaluated=True,
                    handoff_triggered=False,
                    reason=f"No handoff rule matched for {event.event_type}",
                )
            ]
        return [self._build_result(rule, variables) for rule, variables in matches]

    async def execute_handoff(self, result: HandoffResult) -> HandoffResult:
        """Execute the handoff by starting the target CIB Seven process.

        FAIL-CLOSED (EB-3 part 1, see `NotificationBridgeHandoffFailedError`): a rule that
        matched but whose process start genuinely fails PROPAGATES that failure — it is never
        swallowed into `result.reason`. Distinguish three outcomes:
          1. No rule matched (`handoff_triggered=False`) — legitimate skip, returns immediately,
             never touches the starter, never raises.
          2. Rule matched, starter succeeds (fresh start OR idempotent replay of an existing
             instance — `start_process_idempotent`'s `find_active_instance` hit returns
             normally, no exception) — returns the result with `process_instance_id` populated.
          3. Rule matched, starter RAISES (missing business key, durable-audit-persistence
             failure, CIB Seven transport/HTTP error, ...) — raises
             `NotificationBridgeHandoffFailedError` (chained via `from exc`) instead of
             returning a falsely-"complete" result. The caller (a Kafka consumer) must treat
             this as an incident: never ack/commit a message whose handoff failed to start.

        Args:
            result: The evaluated HandoffResult (must have handoff_triggered=True).

        Returns:
            The result with process_instance_id populated, on success only.

        Raises:
            NotificationBridgeHandoffFailedError: the matched rule's process start failed.
        """
        if not result.handoff_triggered:
            logger.warning(
                "notification_bridge.execute_skipped",
                reason=result.reason,
            )
            return result

        logger.info(
            "notification_bridge.execute_handoff",
            target_process=result.target_process,
            variables_keys=list(result.variables.keys()),
        )

        try:
            started = await self._starter(result.target_process, result.variables)
        except Exception as exc:
            logger.error(
                "notification_bridge.handoff_failed",
                target_process=result.target_process,
                error=str(exc),
            )
            raise NotificationBridgeHandoffFailedError(result.target_process, exc) from exc

        # F3 MAJOR-2. The fenced starter (`build_cibseven_process_starter`) returns the whole
        # `ProcessInstance`, so the chokepoint's TYPED outcome reaches `HandoffResult` instead of
        # being flattened to an id (and, on a strict gate hit, to a BLANK id that still looked
        # like a completed handoff). The `str` shape is still accepted — `_noop_starter` and the
        # test/dev spies use it — and is honestly reported as "outcome unknown" (`""`).
        if isinstance(started, ProcessInstance):
            result.process_instance_id = started.instance_id
            result.start_outcome = started.start_outcome.value
        else:
            result.process_instance_id = str(started)
            result.start_outcome = ""
        if not result.process_instance_id.strip():
            # FAIL-CLOSED: a triggered handoff with no instance id is not a completed handoff. The
            # fenced starter can no longer produce one (the chokepoint raises instead), so this
            # guards a non-conforming injected starter rather than the fence itself.
            blank = ValueError(
                "starter returned a blank process instance id — refusing to report a "
                "handoff as complete without one (fail-closed)"
            )
            logger.error(
                "notification_bridge.handoff_blank_instance_id",
                target_process=result.target_process,
                start_outcome=result.start_outcome,
            )
            raise NotificationBridgeHandoffFailedError(result.target_process, blank) from blank
        logger.info(
            "notification_bridge.handoff_complete",
            target_process=result.target_process,
            instance_id=result.process_instance_id,
            start_outcome=result.start_outcome,
        )
        return result

    async def on_event(self, event_type: str, payload: dict[str, Any]) -> list[HandoffResult]:
        """Full pipeline: evaluate all matching handoffs and execute them.

        This is the entry point called by Kafka consumers.
        Multiple processes may be started for a single event
        (e.g., fraude.acusacao_registrada triggers CRED + CANCEL + INADIMPLENCIA).

        FAIL-CLOSED (EB-3 part 1): if ANY matched rule's `execute_handoff` genuinely fails to
        start its process, `NotificationBridgeHandoffFailedError` PROPAGATES out of this method
        (no try/except here) — the caller (the Kafka consumer handler,
        `maezo.platform.integrations.notifications_bridge.handle_bridge_message`) must not
        ack/commit the triggering message when this raises. A prior successful start earlier in
        the same `evaluate_all` fan-out (e.g. CRED started, CANCEL then failed) is NOT rolled
        back — each rule's start is independently idempotent by its own business key, so a safe
        redelivery re-attempts only the failed one(s) (the already-started one is a no-op
        idempotent hit, never a double start).

        Args:
            event_type: Domain event type (e.g., 'contas.glosa_confirmed').
            payload: Event payload dictionary.

        Returns:
            List of HandoffResult with evaluation and execution outcome (only on full success).

        Raises:
            NotificationBridgeHandoffFailedError: a matched rule's process start failed.
        """
        event = HandoffEvent(event_type=event_type, payload=payload)
        results = self.evaluate_all(event)
        executed: list[HandoffResult] = []
        for result in results:
            if result.handoff_triggered:
                result = await self.execute_handoff(result)
            executed.append(result)
        return executed

    # -------------------------------------------------------------------
    # Introspection (for tests)
    # -------------------------------------------------------------------

    def list_handoffs(self) -> list[dict[str, str]]:
        """Return all registered handoff rules for introspection."""
        return [{"event_type": r.event_type, "target_process": r.target_process} for r in self._rules]

    def count_handoffs(self) -> int:
        """Return the number of registered handoff rules."""
        return len(self._rules)

    def get_handoff(self, event_type: str) -> list[tuple[str, Callable[[dict[str, Any]], bool]]]:
        """Return [(target_process, predicate), ...] for all rules matching event_type."""
        return [(r.target_process, r.predicate) for r in self._rules if r.event_type == event_type]

    # -------------------------------------------------------------------
    # Helpers
    # -------------------------------------------------------------------

    def _find_matches(self, event: HandoffEvent) -> list[tuple[_HandoffRule, dict[str, Any]]]:
        """Find all rules that match the event, resolving variables."""
        results: list[tuple[_HandoffRule, dict[str, Any]]] = []
        for r in self._rules:
            if r.event_type == event.event_type and r.predicate(event.payload):
                results.append((r, r.variables_fn(event.payload)))
        return results

    @staticmethod
    def _build_result(rule: _HandoffRule, variables: dict[str, Any]) -> HandoffResult:
        """Build a HandoffResult from a matched rule."""
        return HandoffResult(
            evaluated=True,
            handoff_triggered=True,
            target_process=rule.target_process,
            variables=variables,
            reason="Handoff condition satisfied",
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _noop_starter(process_key: str, variables: dict[str, Any]) -> str:
    """No-op starter for tests — returns a placeholder instance ID."""
    return f"instance-{process_key}-noop"


# ---------------------------------------------------------------------------
# T2.6-7 fenced starter — the ONE sanctioned way to build a production
# `cibseven_starter` for this bridge (ADR-0007/T-C2 chokepoint).
# ---------------------------------------------------------------------------


class NotificationBridgeMissingBusinessKeyError(ValueError):
    """Raised when a matched handoff rule's variables lack a `business_key`.

    Fail-closed: `start_process_idempotent` requires a deterministic business key to dedupe
    against; a rule that produced variables without one is a bridge-side bug (every rule's
    `variables_fn` in this module sets it), never something to paper over with a fabricated or
    empty key that could silently collide across unrelated cases.
    """

    def __init__(self, process_key: str) -> None:
        super().__init__(
            f"notification_bridge: missing/blank business_key for target_process={process_key!r} "
            "— refusing to start (fail-closed, never a non-deterministic engine start)"
        )


def build_cibseven_process_starter(
    transport: CibSevenTransport,
    audit_sink: AuditStartSink,
    *,
    agent_id: str = "notification_bridge",
    agent_version: str = "notification_bridge@v1",
) -> Callable[[str, dict[str, Any]], Awaitable[ProcessInstance]]:
    """Build a `cibseven_starter` for `NotificationBridge` that goes through the FENCED
    process-start chokepoint (`start_process_idempotent`, ADR-0007/T-C2) — never a raw,
    un-audited engine POST.

    This is the ONLY starter production code should inject into `NotificationBridge(...)`. The
    default `_noop_starter` and any ad hoc raw callable (e.g. a bare
    `transport.start_process_instance` call) are test/dev-only shortcuts that bypass the durable
    ADR-0007 provenance record `start_process_idempotent` structurally requires — exactly the
    "raw engine start" this fence exists to make impossible in production.

    Requires `variables["business_key"]` to already be populated by the matched rule's
    `variables_fn` (every one of the 7 rules registered by `_register_default_handoffs` sets it
    — the 2 T2.6-7 ANS-SUBMIT rules from the start, and the 5 pre-existing rules
    INTAKE→RECURSO, CONTAS→FRAUDE + FRAUDE→CRED/CANCEL/INADIMPLENCIA as of EB-3 part 4) — raises
    `NotificationBridgeMissingBusinessKeyError` (fail-closed) rather than starting with a
    garbage/empty key when it is missing.

    `decision_basis` carries ONLY bounded class tokens (`trigger`, `target_process`) — no
    resolvable business identifier or free text, per `AgentDecisionProvenance`'s PHI discipline;
    the chokepoint's own `redact_phi_vars`/`input_sha256` backstop applies regardless.

    RETURNS THE WHOLE `ProcessInstance`, not just its id (F3 MAJOR-2). Flattening to an id here
    was what stranded the chokepoint's outcome behind a log line and let a blank id reach
    `HandoffResult.process_instance_id`. `execute_handoff` accepts both shapes, so a `str`-returning
    test/dev starter still works — it just reports `start_outcome=""` (unknown), honestly.

    STRICT-CAPABLE BY CONSTRUCTION. `process_key` comes from the matched rule, so this starter can
    front ANY family, strict included, even though none of the 7 rules registered today targets
    the one strict family (`SP-OP-PAGTO-001` — see `_START_DEDUP_POLICY`'s table). If a future rule
    does, the gate applies here unchanged and its refusal arrives as a typed `start_outcome`.
    """

    async def _start(process_key: str, variables: dict[str, Any]) -> ProcessInstance:
        business_key = variables.get("business_key")
        if not isinstance(business_key, str) or not business_key.strip():
            raise NotificationBridgeMissingBusinessKeyError(process_key)

        tenant_id = str(variables.get("tenant_id", ""))
        provenance = AgentDecisionProvenance(
            agent_id=agent_id,
            agent_version=agent_version,
            tenant_id=tenant_id,
            decision_basis={"trigger": "notification_bridge", "target_process": process_key},
        )
        return await start_process_idempotent(
            transport,
            process_key=process_key,
            business_key=business_key,
            variables=variables,
            audit_sink=audit_sink,
            provenance=provenance,
        )

    return _start

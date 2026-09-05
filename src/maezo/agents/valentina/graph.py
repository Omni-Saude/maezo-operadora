"""Valentina — Coordenadora de Programas de Cuidado Agent (Phase 3, PROGRAMA, T1.12).

Journey (mirrors the v1 donor's structure, READ-ONLY reference `Maezo-Healthcare-Plan
src/maezo/agents/valentina/graph.py`, adapted to v2's flatter seam set — same rationale as
`agents/rafael/graph.py`'s module docstring; SOURCE OF TRUTH on conflict:
`spec/agents/valentina/agent.yaml` + `spec/processes/` SP-OP-PROGRAMA-001):

    receive -> consent_gate ─┬─(sem consentimento)──> no_consent (terminal NEUTRO)
                             ├─(revogacao)──────────> stopped    (terminal NEUTRO, fail-safe)
                             └─(consentimento ativo)─> gather -> assess(DMN)
                                                          -> {auto_route | human_review}
                                                          -> start_process -> finalize

Valentina is the Phase 3 analog of Rafael/Marina, but she INVERTS the reference shape: this is
not an enrollment happy-path — the entry CHOKEPOINT is consent (LGPD) and the adverse decision
(clinical disenrollment) is ALWAYS human. TWO structural gates (contract §invariantes A/B/C):

- (A) CONSENT CHOKEPOINT (LGPD art. 7/11): `consent_gate` gates ALL program-PHI processing.
  Without active AND verified consent for `consent_scope=programa_cuidado`, the graph
  fail-closes to the `no_consent` terminal WITHOUT touching PHI — no FHIR gather runs, no
  clinical DMN runs, NO process is started. Mirrors the engine's own
  `operadora.programa.check_consent` worker / `ERR_PROGRAMA_NO_CONSENT` boundary ->
  `End_SemConsentimento`. FAIL-CLOSED SEMANTICS: the consent VERDICT (`consent_status`) is
  COMPUTED here from the raw input FACTS (`consentimento_ativo`/`consent_checked`/
  `consent_revoked`/`consent_scope`) and from nothing else; ambiguous facts (anything other
  than the exact boolean `True` for the active/checked flags, or a non-`programa_cuidado`
  scope) are NO consent; inability to verify consent at all (missing runtime context) is NO
  consent. The asymmetry is deliberate: consent requires the STRICTEST possible signal
  (`is True`), while revocation honors the BROADEST (any truthy signal stops processing).
- (B) REVOCATION = STOP (LGPD art. 8 §5 / art. 18 §2, fail-safe, NOT adverse): a revocation
  already present in state (`consent_revoked`) stops processing at the `stopped` neutral
  terminal, taking precedence over an otherwise-active consent. In the engine this is the
  interrupting boundary message event `msg.programa.consent_revoked` -> `stop_processing` ->
  `End_ProcessamentoInterrompidoRevogacao`; in the graph we respect the same edge fail-closed.
- (C) NO-ADVERSE CLINICO (ADR-0018, L0 hard `clinical_decision`): Valentina NEVER decides the
  alta/desligamento clinico, NEVER denies coverage, NEVER makes a clinical decision. `Route`
  has NO adverse variant — only `auto_route` (neutral routing the DMN determined:
  ELEGIVEL/NAO_ELEGIVEL, both informative) and `human_review` (fail-safe, always available).
  The clinical disenrollment is born SOLELY in the human User Task `UT_DecisaoClinica`
  (`decisao_programa == DESLIGAR_CLINICO`) and materialized by the engine-side worker
  `operadora.programa.register_program_discharge` (guarded by
  `ERR_PROGRAM_DISCHARGE_NOT_HUMAN`) — NEVER by this graph. The `programa_routing` DMN only
  stratifies/suggests eligibility (`ELEGIVEL`/`NAO_ELEGIVEL`/`ANALISE_HUMANA`); it has no
  `DESLIGAR`/`ALTA`/`NEGAR_CUIDADO` output by design, and `assess` enforces a CLOSED allowlist
  on top: any unknown/unexpected value routes human. `NAO_ELEGIVEL` is informative
  non-inclusion in a health-promotion program — never a coverage denial (KPI invariant:
  `false_denial_rate == 0`). Apparent-discharge criterion, high risk, clinical ambiguity, and
  DMN unavailability ALL fail-safe to the coordenacao clinica.

DISTINCTION the donor makes and this build preserves: stopping for revocation (B) is an
LGPD fail-safe (neutral); disenrolling for a clinical reason (C) is an adverse clinical
decision and is human-only. The two are different terminals and never blur.

CALLER-PLANTED-OUTPUT SANITIZATION (baked in from the start — the R1-found defect class on
fernando/carolina/marina, HIGHEST-STAKES instance here: a planted consent verdict would be an
LGPD PHI-gate bypass): `receive` — the graph's SINGLE entry node (START has exactly one edge,
into `receive`; regression-tested) — overwrites EVERY output-only `ValentinaState` field with
its benign reset (`_output_field_resets`) on BOTH of its paths, BEFORE `consent_gate`/`gather`/
`assess` run. In particular the consent VERDICT field `consent_status` is output-only and IS in
the reset set: a caller planting `consent_status="ativo"` (with or without a planted
`route`/`desfecho`/`dossier`/`process_ref`) has the plant wiped at entry and the verdict
recomputed by `consent_gate` from the raw input facts — without real consent the turn STILL
terminates at the neutral `no_consent` terminal with zero PHI gather, zero DMN, zero process.
`route` resets to `"human_review"` (the fail-safe `_route` default) and `_consent_branch`'s own
default for any non-`ativo`/`revogado` verdict is `no_consent` — even a hypothetical
node-skipping path could neither auto-route nor pass the gate off planted state.
`_CALLER_INPUT_FIELDS` + the partition-completeness regression test force every future state
field to be classified input vs output, so the sanitization list cannot silently drift. The
`consent_gate`/`gather`/`assess` early-bail guards are safe because the only `error` that can
exist when they run is the one `receive` itself set (class token, missing runtime context) —
and that error path fail-closes through the `no_consent` terminal (inability to verify consent
= no consent), never `{}`, never an adverse effect. Engine-variable hygiene: `error`/
`dmn_error` (raw transport/exception text) are NEVER shipped into engine-bound variables — only
bounded class tokens (`motivo_encaminhamento`) travel.

`assess` ALWAYS consults the deterministic DMN (`programa_routing`; `programa_sla` purely
informative — feeds the dossier, never gates routing, mirrors Rafael's `auth_sla`) via the
ENGINE-side `DmnTransport` (ADR-0028/T1.5) — DMN logic is never re-implemented in Python. The
LLM REASONS over the DMN result to assemble the stratification/care-plan dossier (ADR-0012); it
never substitutes or re-decides it. HARDENING (this charter, diverges from the
Rafael/Marina precedent where an LLM dossier failure keeps the route): an LLM failure while
assembling the AUTO-route dossier downgrades the case to `human_review`
(`motivo_humano="falha_tecnica"`) — on the consented path, any technical failure lands on a
human, never on the automatic path. On the already-human path an LLM failure just degrades to
the deterministic minimal dossier (the route is already the fail-safe one).

PHI discipline: Valentina's `security_zone` is `phi` (`spec/agents/valentina/agent.yaml`, D10)
— every LLM call in this module passes `phi=True` (ADR-0006/ADR-0017/T1.7), and ALL of them sit
AFTER the consent gate. `gather`'s FHIR summary facts stay in graph state only — they are never
copied into `_care_facts`, the dossier, or engine-bound variables (mirrors Marina).

LABELED BOUNDARIES (this build, disclosed — never fabricated; same rationale as
`agents/rafael/graph.py`'s/`agents/helena/graph.py`'s module docstrings):
- `gather` uses a thin `PatientSummaryReader` Protocol over v2's generic `FhirServer`
  (`adapters.py`) — NOT the donor's PEP-gated `mcp-fhir.read_patient_summary` tool shape.
  CORRECTED (`grep -n '"valentina"' gateway/tool_registry.py`, `_FHIR_ADAPTER_BY_AGENT`): this
  reader IS wired through a PEP-gated ToolRegistry now — `gateway/tool_registry.py
  ::build_agent_seams` wraps it in `gateway/seams/fhir.py::GatedFhirReader` (Valentina is one of
  the five agents in `_FHIR_ADAPTER_BY_AGENT`, adapter `read_patient_summary`), and both live
  composition roots (`runtime/agent_runtime/service.py::_build_tool_deps`, `platform/
  webhooks/service.py`) build Valentina's `fhir` dependency through it — the prior "no
  ToolRegistry/PEP gateway wiring for agent tool calls yet" claim is false today. Best-effort: a
  FHIR failure degrades to a dossier gap note, never blocks routing, and structurally can only
  run AFTER the consent gate.
- No episodic memory write (`mcp-memory.read_write`, ADR-0002) — the donor's `finalize` writes
  the LGPD cessation/case note to memory; v2's `MemoryServer.store_episodic` refuses fail-closed
  (the `agent_memory` table exists since migration `0001`; the tool's `(agent_id, event)`
  signature carries neither `tenant_id` nor `thread_id`, both `text NOT NULL` — GAP-DU-01-a).
  The semantic column was dropped by `0009_drop_pgvector`, ADR-0002 §3 SUSPENDED pending a
  consumer (ADR-0047, DRAFT). Same boundary Helena/Rafael/Marina disclose. `finalize` is a
  terminal no-op; adding the memory write is a follow-up.
- A2A delegation (`care.stratify`/`care.enroll` -> Valentina) is HALF wired (VAL-01).
  CORRECTED (CC-04, fleet audit) — the prior text here claimed v2's `a2a/` package had no
  `DelegationEnvelope`/`DelegationDispatcher`; both exist and are fully built/tested
  (`a2a/delegation.py::DelegationEnvelope`, `a2a/dispatcher.py::DelegationDispatcher`, exported
  from `maezo.a2a`). UPDATED (A2A handlers, lote3) — CC-04's companion claims that "there is no
  `src/maezo/agents/valentina/delegation.py`" and that `grep -rn 'make_valentina_handler'
  src/maezo/` returns 0 hits are NO LONGER TRUE at this tip: the TARGET handler now exists
  (`agents/valentina/delegation.py::make_valentina_handler`), the donor's handler IS ported, and
  nine of the ten agents (all but lucas) now have a real `delegation.py` using that infra. What is
  still missing for Valentina is registration and origin: the handler is NOT registered with any
  dispatcher (`grep -n '"valentina"' runtime/agent_runtime/a2a_composition.py` = 0 hits) and
  `tools/workers/programa.py` still does not originate the delegation — both are an owner decision
  (gap `FERNANDO-DELEGATION-CALL-SITE`, ver VAL-01 do fleet audit). The graph is still invoked
  directly with an already-assembled case state (as the unit tests do).
- Live engine acceptance (deployed SP-OP-PROGRAMA-001 + programa DMNs) is DEFERRED — this
  build is unit-proven with Fake transports only (host constraint; disclosed, not fabricated).
"""

from __future__ import annotations

from typing import Any, Final, Literal, Protocol, TypedDict, cast

import structlog
from langgraph.graph import END, START, StateGraph

from maezo.runtime.inference import InferenceProvider
from maezo.runtime.prompt_format import render_fatos_para_prompt
from maezo.runtime.start_outcome import (
    notify_start_failure as emit_start_failure_notice,
)
from maezo.runtime.start_outcome import (
    route_after_start,
    start_failed_state,
)
from maezo.runtime.turn_telemetry import emit_turn_desfecho
from maezo.tools.mcp_cibseven.transport import (
    AgentDecisionProvenance,
    AuditStartSink,
    CibSevenError,
    CibSevenTransport,
    start_process_idempotent,
)
from maezo.tools.workers.dmn_transport import (
    DmnEvaluationError,
    DmnNoResultError,
    DmnTransport,
    first_row,
)

from .prompts import (
    CARE_PLAN_PROMPT_VERSION,
    STRATIFICATION_PROMPT_VERSION,
    SYSTEM_PROMPT_VERSION,
    care_plan_prompt,
    stratification_prompt,
)

# --- Domain enums (mirror the SP-OP-PROGRAMA-001 contract + DMN schema) -----------------------

# Valentina's A2A task (decides which dossier: risk stratification vs care plan). Both are
# in-zone, AFTER the consent gate; both instruct, never decide.
Task = Literal["stratify", "enroll"]

# Graph routing. STRUCTURALLY WITHOUT AN ADVERSE VARIANT: no value disenrolls, discharges, or
# denies care. The ABSENCE of a "discharge"/"deny" variant is the structural guarantee
# (invariant C, L0 hard).
Route = Literal["auto_route", "human_review"]

# `programa_routing` output domain — no DESLIGAR/ALTA/NEGAR output by design (informative).
ElegibilidadePrograma = Literal["ELEGIVEL", "NAO_ELEGIVEL", "ANALISE_HUMANA"]

# Consent chokepoint verdict (structural gate A/B — computed by `consent_gate`, never an input).
ConsentStatus = Literal["ativo", "ausente", "revogado"]

# Why the case went to human review — bounded CLASS TOKENS (the only failure vocabulary that
# ever reaches engine-bound variables; raw error text never does). NONE of these is a
# disenrollment/discharge/denial — they are all reasons FOR human analysis.
MotivoHumano = Literal[
    "estratificacao_analise_humana",  # programa_routing = ANALISE_HUMANA (ambiguous/high risk)
    "criterio_alta_aparente",  # APPARENT discharge criterion -> human decides (never Valentina)
    "dmn_indisponivel",  # DMN unavailable -> never an adverse outcome by omission
    "falha_tecnica",  # LLM/dossier technical failure on the consented path -> human
    "outro",
]

PROCESS_KEY_PROGRAMA = "SP-OP-PROGRAMA-001"

DMN_PROGRAMA_ROUTING = "programa_routing"
DMN_PROGRAMA_SLA = "programa_sla"

# Consent scope required by this flow (fixed — contract §input variables).
CONSENT_SCOPE_PROGRAMA = "programa_cuidado"

# Default human destination (candidate group of UT_DecisaoClinica; `equipe-cuidado` is the
# other declared group — coordenacao-clinica coordinates and is the conservative default).
GRUPO_COORDENACAO_CLINICA = "coordenacao-clinica"

# Class token for `receive`'s missing-context guard (`error` is internal state — it is still a
# bounded token, never free text, and it NEVER ships into engine-bound variables).
ERROR_MISSING_CONTEXT = "contexto_de_runtime_ausente"


logger = structlog.get_logger(__name__)


class PatientSummaryReader(Protocol):
    """Best-effort FHIR summary read seam (`gather`, post-consent only). See module docstring's
    labeled boundary."""

    async def read_patient_summary(self, patient_id: str) -> dict[str, Any]: ...


# --- Graph state (working memory; ADR-0002) ----------------------------------------------------


class ValentinaState(TypedDict, total=False):
    """Care-program case state. Every field is pseudonymized (Zona PHI, ADR-0006) —
    `beneficiario_pseudo_id` NEVER carries a raw CPF/name/CNS. The consent FACTS and the
    clinical booleans/risk band arrive PRE-RESOLVED by deterministic upstream workers (contract
    SP-OP-PROGRAMA-001) — Valentina CONSUMES them, never computes them. The consent VERDICT
    (`consent_status`) is OUTPUT-ONLY: computed by `consent_gate`, sanitized at `receive`
    (module docstring §CALLER-PLANTED-OUTPUT SANITIZATION). No clinical-PHI field is read
    before the consent gate passes."""

    # A2A task (decides the dossier: stratification vs care plan). Default `stratify`.
    task: Task

    # Runtime identifiers / task origin.
    tenant_id: str
    canal: str  # a2a | canal_proativo
    programa_id: str
    beneficiario_pseudo_id: str
    ciclo: str
    # canal_proativo | indicacao_clinica | auto_inscricao_beneficiario | estratificacao_populacional
    gatilho: str
    proactive_trigger_ref: str

    # --- Consent FACTS (caller inputs, pre-resolved by the engine-side chokepoint worker) ---
    consent_scope: str  # must be `programa_cuidado`
    consentimento_ativo: bool  # active and non-revoked consent (worker-resolved)
    consent_checked: bool  # verified before proactive contact (D9)
    consent_revoked: bool  # revocation already signalled (interrupting boundary in the engine)
    consent_event_ref: str  # consent/revocation record reference (LGPD audit)

    # --- Clinical inputs (pre-resolved in-zone; only USED after the consent gate) ---
    risco_estratificado: str  # risk band (never raw clinical content in Zona Geral)
    elegibilidade_criterios_atendidos: bool  # program eligibility criteria met
    criterio_alta_aparente: bool  # INFORMATIVE signal (never decides) — only routes human

    # FHIR reference for the dossier (patient summary). Never raw PHI.
    patient_summary_ref: str

    # --- OUTPUT-ONLY fields below (all reset by `receive`; see `_output_field_resets`) ---

    # Consent VERDICT — computed by `consent_gate` from the facts above, NEVER caller-supplied.
    consent_status: ConsentStatus

    # Filled by `gather` (pseudonymized FHIR facts; stay in state — never engine-bound).
    gathered: bool
    summary_facts: dict[str, Any]
    gather_notes: list[str]  # FHIR enrichment gaps (attached to the dossier)

    # Filled by `assess` (DMN results + refs).
    elegivel_programa: ElegibilidadePrograma  # programa_routing (informative)
    motivo_estratificacao: str  # programa_routing.motivo
    sla_decisao: str  # ISO 8601 (programa_sla output; informative)
    sla_alerta: str
    dmn_refs: dict[str, str]  # table -> decision-definition reference (programa_routing#<id>)
    dmn_error: str  # raw transport error text — NEVER shipped to engine variables

    # Filled by `auto_route`/`human_review`.
    dossier: dict[str, Any]  # LLM-assembled dossier (instruction, never a decision)

    # Routing (NEVER a disenrollment/discharge/denial — only neutral or human).
    route: Route
    motivo_humano: MotivoHumano
    grupo_humano: str  # candidate group of the human destination

    # Filled by `start_process` (consented path only — structurally unreachable otherwise).
    process_started: bool
    #: CC-01: o start foi TENTADO e FALHOU tecnicamente (`except CibSevenError` de
    #: `start_process`). NAO e a mesma coisa que `process_started is False`, que tambem cobre
    #: no-ops legitimos; e este marcador — e so ele — que a aresta condicional le.
    start_failed: bool
    business_key: str
    process_ref: dict[str, Any]

    # Output (contract §payload.desfecho — NO automatic adverse variant exists).
    desfecho: str  # enrollment_realizado | nao_elegivel | sem_consentimento |
    #                 interrompido_revogacao | analise_humana_clinica
    error: str  # bounded class token for a technical/context failure (internal-only)


# --- Helpers -----------------------------------------------------------------------------------


def _task(state: ValentinaState) -> Task:
    """Case task (default `stratify` — risk stratification)."""
    return state.get("task", "stratify")


def _business_key(state: ValentinaState) -> str:
    """Idempotent business key per contract: `PROG-{tenant}-{programa}-{benef}-{ciclo}`.

    One active instance per (programa x beneficiario x ciclo); a re-dispatch of the same
    enrollment returns the active instance (idempotent start).
    """
    return "PROG-{tenant}-{programa}-{benef}-{ciclo}".format(
        tenant=state.get("tenant_id", ""),
        programa=state.get("programa_id", ""),
        benef=state.get("beneficiario_pseudo_id", ""),
        ciclo=state.get("ciclo", ""),
    )


# --- State sanitization (the fernando/carolina/marina R1 defect class, baked in from the start) -

#: The ONLY fields a caller may legitimately seed on the initial state (runtime identifiers +
#: SP-OP-PROGRAMA-001 contract inputs: the consent FACTS, the worker-pre-resolved clinical
#: inputs, and the gather reference). Everything else in `ValentinaState` is OUTPUT-ONLY:
#: produced exclusively by this graph's own nodes. CRITICAL PARTITION LINE: the raw consent
#: FACTS (`consentimento_ativo`/`consent_checked`/`consent_revoked`/`consent_scope`/
#: `consent_event_ref`) are inputs; the consent VERDICT (`consent_status`) is an output —
#: a caller can assert facts, never the verdict. Single-sourced against `ValentinaState` by the
#: partition-completeness regression test (`test_output_field_partition_is_complete`): a new
#: state field MUST be classified into exactly one of the two sets or that test fails, so the
#: sanitization below can never silently drift out of date.
_CALLER_INPUT_FIELDS: frozenset[str] = frozenset(
    {
        "task",
        "tenant_id",
        "canal",
        "programa_id",
        "beneficiario_pseudo_id",
        "ciclo",
        "gatilho",
        "proactive_trigger_ref",
        # Consent FACTS (the verdict `consent_status` is deliberately NOT here).
        "consent_scope",
        "consentimento_ativo",
        "consent_checked",
        "consent_revoked",
        "consent_event_ref",
        # Pre-resolved clinical inputs (in-zone workers; consumed only after the gate).
        "risco_estratificado",
        "elegibilidade_criterios_atendidos",
        "criterio_alta_aparente",
        "patient_summary_ref",
    }
)


def _output_field_resets() -> dict[str, Any]:
    """Benign reset values for EVERY output-only `ValentinaState` field — applied
    unconditionally at `receive` entry (module docstring §CALLER-PLANTED-OUTPUT SANITIZATION).

    An inbound turn's state may only carry INPUT fields (identifiers + consent facts +
    pre-resolved worker facts). Every field a NODE of this graph is supposed to fill is reset
    here first, so a caller-planted CONSENT VERDICT (`consent_status="ativo"` — the
    highest-stakes plant: an LGPD PHI-gate bypass), `error`, `route`, forged DMN
    facts/`dmn_refs`, a pre-cooked `dossier`, or fake `process_*` outputs can never survive into
    the consent branch, routing, the dossier `fatos`, or engine-bound process variables. Returns
    a FRESH dict per call — the mutable container values (`{}`/`[]`) must never be shared across
    turns.

    `consent_status` resets to `None` (no verdict): `_consent_branch` maps any non-`ativo`/
    `revogado` verdict to the `no_consent` terminal, so even a hypothetical path that skipped
    `consent_gate` entirely would fail CLOSED (no PHI), not open. `route` resets to
    `"human_review"` (fail-safe `_route` default): even a hypothetical `assess`-skipping path
    could not auto-route off sanitized state. `business_key` resets to `""` and is re-derived
    from input identifiers on the happy path.
    """
    return {
        "consent_status": None,  # THE critical reset — the verdict is only ever computed
        "gathered": False,
        "summary_facts": {},
        "gather_notes": [],
        "elegivel_programa": None,
        "motivo_estratificacao": "",
        "sla_decisao": "",
        "sla_alerta": "",
        "dmn_refs": {},
        "dmn_error": "",
        "dossier": {},
        "route": "human_review",
        "motivo_humano": None,
        "grupo_humano": "",
        "process_started": False,
        "start_failed": False,
        "business_key": "",
        "process_ref": {},
        "desfecho": "",
        "error": "",
    }


#: Fatos BOOLEANOS deste fluxo, com o nome que o humano de destino reconhece (CC-11).
#:
#: Incidente de 24/08/2026 (contado por inteiro em `agents/rafael/graph.py::_FATOS_BOOLEANOS`):
#: fatos passados ao modelo como repr de dicionario deixam `False` e `None` com a mesma cara de
#: "vazio", e um fato APURADO-e-desfavoravel vira "nao ha registro". O conserto ficou num agente
#: so' ate' a auditoria da frota; este mapa e' a adocao aqui. Chave -> rotulo; a ORDEM e' a ordem
#: das linhas no prompt. So' entram fatos declarados `bool` no state — nada que seja enum/str.
#: `elegivel_programa`/`consent_status` NAO entram: sao enums de tres ou mais valores, e nao
#: booleanos — o colapso de repr que este mapa conserta e' entre `False` e `None`.
_FATOS_BOOLEANOS: Final[dict[str, str]] = {
    "elegibilidade_criterios_atendidos": "criterios de elegibilidade do programa atendidos",
    "criterio_alta_aparente": "criterio de alta aparente",
}


class ValentinaGraph:
    """Wires Valentina's injected dependencies into a compilable `StateGraph[ValentinaState]`."""

    def __init__(
        self,
        *,
        inference: InferenceProvider,
        dmn: DmnTransport,
        cibseven: CibSevenTransport,
        audit_sink: AuditStartSink,
        fhir: PatientSummaryReader | None = None,
        agent_version: str = "valentina@v0",
    ) -> None:
        self._llm = inference
        self._dmn = dmn
        self._cibseven = cibseven
        # T-C2 fence: required durable ADR-0007 sink for the SP-OP-PROGRAMA-001 start.
        self._audit_sink = audit_sink
        self._model_id = getattr(inference, "model_id", None)
        self._fhir = fhir
        self._agent_version = agent_version

    # -- Nodes ----------------------------------------------------------------------------

    async def receive(self, state: ValentinaState) -> dict[str, Any]:
        """Turn start: task arrives (A2A `care.stratify`/`care.enroll`). Idempotent.

        SANITIZATION FIRST (module docstring §CALLER-PLANTED-OUTPUT SANITIZATION): EVERY
        output-only field is reset (`_output_field_resets`) before anything else — an inbound
        consent VERDICT/`error`/`route`/forged DMN fact is a caller plant, never trusted. This
        node is the graph's SINGLE entry (`compile_graph` wires exactly one edge out of START,
        into `receive`; structurally regression-tested), so the reset covers every downstream
        read.

        Defense: never proceeds without the minimum contract identifiers (tenant + programa +
        beneficiario + ciclo — without them there is no idempotent business key AND no way to
        verify consent for a concrete titular/scope). The guard fail-closes through the consent
        chokepoint: `consent_gate` treats the (self-set, class-token) `error` as inability to
        verify consent = NO consent -> neutral `no_consent` terminal, zero PHI, zero DMN, zero
        process (never an adverse effect, never a bare `{}`).
        """
        sanitized = _output_field_resets()
        tenant_ok = bool(state.get("tenant_id"))
        key_ok = bool(state.get("programa_id") and state.get("beneficiario_pseudo_id") and state.get("ciclo"))
        if not tenant_ok or not key_ok:
            return {**sanitized, "error": ERROR_MISSING_CONTEXT}
        return {**sanitized, "business_key": _business_key(state)}

    async def consent_gate(self, state: ValentinaState) -> dict[str, Any]:
        """CONSENT CHOKEPOINT (structural gate A/B, LGPD) — gates ALL program-PHI processing.

        COMPUTES the consent verdict from the raw input FACTS (post-`receive`-sanitization, a
        planted verdict no longer exists to be read). Mirrors the engine worker
        `operadora.programa.check_consent` / `ERR_PROGRAMA_NO_CONSENT`, fail-closed:

          - inability to verify (missing runtime context, `receive`'s own guard) -> NO consent
            (`ausente`) — on doubt, PHI is never processed;
          - revocation signalled (`consent_revoked` truthy in ANY form) -> `revogado`
            (stop processing; fail-safe LGPD art. 8 §5 / art. 18 §2; NOT adverse; precedence
            over everything, including an otherwise-active consent);
          - consent active AND verified for the `programa_cuidado` scope — the STRICTEST
            signal: `consentimento_ativo is True` and `consent_checked is True` (exact
            booleans; a string `"true"`, `1`, or any coercible truthy is AMBIGUOUS and
            therefore NO consent) and the scope matches -> `ativo`;
          - anything else -> `ausente` (no consent; neutral terminal; no PHI).

        No adverse decision is born here — only ceasing/not-starting PHI processing, which is
        the safe and legal behavior.
        """
        if state.get("error"):
            # Only `receive`'s own missing-context token can be here post-sanitization.
            # Cannot verify consent for a concrete titular/scope -> NO consent (fail-closed).
            return {"consent_status": "ausente"}

        # (B) Revocation precedence: ANY truthy revocation signal stops processing (broadest
        # reading is the fail-safe one — the opposite asymmetry of the active-consent check).
        if bool(state.get("consent_revoked", False)):
            return {"consent_status": "revogado"}

        # (A) Chokepoint: consent MUST be active AND verified for `programa_cuidado`, as exact
        # booleans. FAIL-CLOSED — absent/false/ambiguous flags or a foreign scope bar PHI.
        scope = state.get("consent_scope", CONSENT_SCOPE_PROGRAMA)
        consent_ok = (
            scope == CONSENT_SCOPE_PROGRAMA
            and state.get("consentimento_ativo") is True
            and state.get("consent_checked") is True
        )
        if not consent_ok:
            return {"consent_status": "ausente"}
        return {"consent_status": "ativo"}

    async def no_consent(self, state: ValentinaState) -> dict[str, Any]:
        """NEUTRAL terminal (invariant A): no active/verified consent — nothing was processed.

        Mirrors `End_SemConsentimento` (LGPD fail-safe, NOT adverse to the beneficiary): no PHI
        gather ran, no clinical DMN ran, NO process was started (structural: this node's only
        outgoing edge is END — `start_process` is unreachable from here). Also the fail-closed
        landing for `receive`'s missing-context guard (inability to verify consent = no
        consent; the class-token `error` field preserves the distinction for observability).
        """
        outcome = {"desfecho": "sem_consentimento", "process_started": False}
        emit_turn_desfecho({**state, **outcome}, agent_id="valentina")
        return outcome

    async def stopped(self, state: ValentinaState) -> dict[str, Any]:
        """NEUTRAL terminal (invariant B): consent revoked — processing STOPS (fail-safe LGPD).

        Mirrors the engine's interrupting revocation boundary -> `stop_processing` ->
        `End_ProcessamentoInterrompidoRevogacao`: ceasing treatment on the titular's request is
        the safe and legal behavior, never an adverse effect. No PHI gather, no DMN, no process
        (structural: only outgoing edge is END).
        """
        outcome = {"desfecho": "interrompido_revogacao", "process_started": False}
        emit_turn_desfecho({**state, **outcome}, agent_id="valentina")
        return outcome

    async def gather(self, state: ValentinaState) -> dict[str, Any]:
        """Best-effort FHIR enrichment — ONLY reachable via `consent_gate`'s `proceed` branch
        (the conditional edge is the structural PHI gate). NEVER blocks routing.

        Defense in depth: the error bail (unreachable in the current topology — `receive`'s
        guard lands on `no_consent`) still re-asserts the fail-safe human route, never `{}`.
        """
        if state.get("error"):
            return {"route": "human_review"}

        summary_facts: dict[str, Any] = {}
        notes: list[str] = []

        if self._fhir is None:
            notes.append(
                "FHIR reader not configured for this build (labeled boundary — see graph.py "
                "module docstring); dossier proceeds with pre-resolved worker facts only."
            )
            return {"gathered": True, "summary_facts": summary_facts, "gather_notes": notes}

        summary_ref = state.get("patient_summary_ref") or state.get("beneficiario_pseudo_id", "")
        if summary_ref:
            try:
                summary_facts = await self._fhir.read_patient_summary(summary_ref)
            except Exception as exc:  # noqa: BLE001 — best-effort enrichment, never fatal.
                # CLASS TOKEN ONLY (CC-10): `str(exc)` de um cliente FHIR tipicamente ecoa a
                # URL / id em que falhou — o proprio `summary_ref` — e esta nota e' copiada para o
                # prompt do dossie E para `dossie_valentina`, que o engine sela na zona geral
                # (ADR-0006/ADR-0007). O trace completo fica no log estruturado, canal de
                # diagnostico, nunca na nota.
                logger.warning("valentina_fhir_resumo_indisponivel", exc_info=True)
                notes.append(f"resumo FHIR indisponivel: {type(exc).__name__}")

        return {"gathered": True, "summary_facts": summary_facts, "gather_notes": notes}

    async def assess(self, state: ValentinaState) -> dict[str, Any]:
        """Evaluate the deterministic DMN and decide ROUTING (neutral vs human).

        ADR-0012: the DMN decides the stratification; the LLM reasons over the result
        (dossier). NO branch here produces a disenrollment, a clinical discharge, or a care
        denial — only `auto_route` (neutral) or `human_review` (fail-safe). `programa_routing`
        only stratifies/suggests (`ELEGIVEL`/`NAO_ELEGIVEL`/`ANALISE_HUMANA`); `programa_sla`
        is purely informative (feeds the dossier, never gates routing).

        FAIL-SAFE: apparent-discharge criterion, high risk (DMN-decided), DMN unavailable ->
        ALWAYS human, NEVER an adverse outcome by omission.
        """
        if state.get("error"):
            # Same rationale as `gather`'s bail (defense in depth, unreachable topologically).
            return {"route": "human_review"}

        dmn_refs: dict[str, str] = {}

        # APPARENT discharge criterion is an INFORMATIVE signal (L0 hard): Valentina NEVER
        # disenrolls. Routes to the coordenacao clinica to decide (in the User Task). Takes
        # precedence over automatic stratification; broad truthiness is the fail-safe reading.
        if bool(state.get("criterio_alta_aparente", False)):
            sla = await self._evaluate_dmn(DMN_PROGRAMA_SLA, self._sla_input(state))
            base = self._sla_base(sla, dmn_refs)
            return {
                **base,
                **self._route_human("criterio_alta_aparente", dmn_refs),
                "desfecho": "analise_humana_clinica",
            }

        # 1) Stratification (the informative heart — NO DESLIGAR/ALTA/NEGAR output).
        strat = await self._evaluate_dmn(
            DMN_PROGRAMA_ROUTING,
            {
                "risco_estratificado": str(state.get("risco_estratificado", "")),
                "elegibilidade_criterios_atendidos": bool(
                    state.get("elegibilidade_criterios_atendidos", False)
                ),
            },
        )
        if strat.get("error"):
            return {
                **self._route_human("dmn_indisponivel", dmn_refs),
                "dmn_error": str(strat["error"]),
                "desfecho": "analise_humana_clinica",
            }
        elegivel = cast(ElegibilidadePrograma, str(strat["row"].get("elegivel_programa", "ANALISE_HUMANA")))
        motivo = str(strat["row"].get("motivo", ""))
        dmn_refs[DMN_PROGRAMA_ROUTING] = strat["ref"]

        # 2) SLA (always — attaches the clinical-decision deadlines to the dossier; purely
        # informative: its failure never gates routing, mirrors Rafael's `auth_sla`).
        sla = await self._evaluate_dmn(DMN_PROGRAMA_SLA, self._sla_input(state))
        base = self._sla_base(sla, dmn_refs)
        base["elegivel_programa"] = elegivel
        base["motivo_estratificacao"] = motivo
        base["dmn_refs"] = dmn_refs

        # FAIL-SAFE (CLOSED allowlist): ONLY the known neutral values proceed to auto_route.
        # ELEGIVEL -> informative enrollment (the clinical User Task confirms). NAO_ELEGIVEL ->
        # informative non-inclusion (NOT a coverage denial). ANY other value (ANALISE_HUMANA,
        # unexpected/unknown — including a hypothetical adverse-looking string, empty) -> human.
        # Nothing ambiguous falls through to automatic by omission (L0 hard invariant).
        if elegivel in ("ELEGIVEL", "NAO_ELEGIVEL"):
            desfecho = "enrollment_realizado" if elegivel == "ELEGIVEL" else "nao_elegivel"
            return {**base, "route": "auto_route", "desfecho": desfecho}
        return {
            **base,
            **self._route_human("estratificacao_analise_humana", dmn_refs),
            "desfecho": "analise_humana_clinica",
        }

    async def auto_route(self, state: ValentinaState) -> dict[str, Any]:
        """Neutral routing (ELEGIVEL / NAO_ELEGIVEL — both informative).

        GUARDRAIL: this path NEVER produces a disenrollment, a clinical discharge, or a care
        denial. It only assembles the dossier; `start_process` (next node) opens the instance,
        whose human User Task `UT_DecisaoClinica` is where any substantive clinical decision is
        made. `NAO_ELEGIVEL` is informative non-inclusion — never a coverage denial.

        HARDENING (this charter — diverges from the Rafael/Marina LLM-failure precedent,
        disclosed in the module docstring): if the dossier LLM call fails, the case DOWNGRADES
        to `human_review` (`falha_tecnica`) — a technical failure on the consented path always
        lands on a human, never on the automatic path.
        """
        dossier, llm_ok = await self._build_dossier(state, route="auto_route")
        if not llm_ok:
            return {
                "dossier": dossier,
                **self._route_human("falha_tecnica", dict(state.get("dmn_refs") or {})),
                "desfecho": "analise_humana_clinica",
            }
        return {"dossier": dossier}

    async def human_review(self, state: ValentinaState) -> dict[str, Any]:
        """Prepares the coordenacao clinica's dossier and marks the human route.

        The route for ANY ambiguous/high-risk/apparent-discharge/DMN-unavailable case. NONE of
        these is a disenrollment/discharge/denial: the clinical disenrollment is born SOLELY in
        `UT_DecisaoClinica` (`DESLIGAR_CLINICO`, by a human clinician, materialized by the
        `register_program_discharge` worker behind `ERR_PROGRAM_DISCHARGE_NOT_HUMAN`).
        Valentina instructs; the clinician decides. An LLM failure here only degrades to the
        deterministic minimal dossier — the route is already the fail-safe one.
        """
        dossier, _llm_ok = await self._build_dossier(state, route="human_review")
        return {"dossier": dossier, "desfecho": state.get("desfecho") or "analise_humana_clinica"}

    async def start_process(self, state: ValentinaState) -> dict[str, Any]:
        """Start SP-OP-PROGRAMA-001 idempotently (business key `PROG-{tenant}-{programa}-
        {benef}-{ciclo}`) with the contract's variables.

        ONLY reachable on the CONSENTED path (structural: both incoming edges come from
        `auto_route`/`human_review`, which sit behind `consent_gate`'s `proceed` branch — the
        `no_consent`/`stopped` terminals never reach here). The engine's own `ST_CheckConsent`
        chokepoint independently re-verifies consent in-process (defense in depth). A start
        failure never loses the case: it records the error and keeps the routing. NEVER emits a
        disenrollment/discharge/denial "on the side".
        """
        if state.get("error") and not state.get("business_key"):
            return {"process_started": False}

        business_key = state.get("business_key") or _business_key(state)
        variables = self._contract_variables(state)
        provenance = AgentDecisionProvenance(
            agent_id="valentina",
            agent_version=self._agent_version,
            tenant_id=state.get("tenant_id", ""),
            model_id=self._model_id,
            prompt_version=SYSTEM_PROMPT_VERSION,
            decision_basis={
                "route": state.get("route", ""),
                "desfecho": state.get("desfecho", ""),
                "programa": state.get("programa_id") or "",
            },
        )
        try:
            instance = await start_process_idempotent(
                self._cibseven,
                process_key=PROCESS_KEY_PROGRAMA,
                business_key=business_key,
                variables=variables,
                audit_sink=self._audit_sink,
                provenance=provenance,
            )
        except CibSevenError as exc:
            # CC-01: `start_failed_state` devolve as MESMAS tres chaves de antes mais o marcador
            # `start_failed`, que e o que `route_after_start` le para desviar a
            # `notify_start_failure` em vez de seguir calado para o terminal.
            return start_failed_state(business_key=business_key, error=f"start_process indisponivel: {exc}")
        return {
            "process_started": True,
            "business_key": business_key,
            "process_ref": {
                "instance_id": instance.instance_id,
                "state": instance.state,
                "already_existed": instance.already_existed,
            },
        }

    async def notify_start_failure(self, state: ValentinaState) -> dict[str, Any]:
        """CC-01: o start FALHOU — grava o desfecho de erro e ALERTA, em vez de seguir calado.

        Ate CC-01 a aresta que saia de `start_process` era INCONDICIONAL: o turno chegava ao
        terminal com o `desfecho` de SUCESSO que um no a montante ja havia gravado, afirmando um
        fato que nao aconteceu, e sem prazo nenhum — o timer de SLA vive na instancia BPMN que
        nunca nasceu. O corpo deste no e o helper compartilhado
        (`maezo.runtime.start_outcome.notify_start_failure`): uma definicao para os 9 agentes,
        nunca 9 copias.
        """
        return emit_start_failure_notice(dict(state), agent_id="valentina", process_key=PROCESS_KEY_PROGRAMA)

    async def finalize(self, state: ValentinaState) -> dict[str, Any]:
        """Terminal node — no further computation; `desfecho` was already set upstream.

        No episodic memory write here (labeled boundary, module docstring — divergence from the
        donor's `finalize`, which records the LGPD cessation/case note to `mcp-memory`; v2 has
        no memory seam wired into any agent graph yet).

        CC-09: emits ONE `maezo_agent_desfecho_total` for this turn. `no_consent`/`stopped` are
        the OTHER two terminals in this graph (both wired straight to END) and emit their own
        record directly at the point they set `desfecho` — this node never sees those turns.
        """
        emit_turn_desfecho(state, agent_id="valentina")
        return {}

    # -- Conditional routing --------------------------------------------------------------

    @staticmethod
    def _consent_branch(state: ValentinaState) -> str:
        """Routing AFTER the consent chokepoint (structural gate A/B).

        FAIL-CLOSED: only proceeds to PHI processing (`proceed`) when the COMPUTED verdict is
        exactly `ativo`. `revogado` -> the `stopped` neutral terminal. ANYTHING else — `ausente`,
        a missing verdict (e.g. a hypothetical gate-skipping path reading the sanitized reset),
        or an unknown value — lands on the `no_consent` neutral terminal: no PHI, no DMN, no
        process, never adverse.
        """
        status = state.get("consent_status")
        if status == "ativo" and not state.get("error"):
            return "proceed"
        if status == "revogado":
            return "stopped"
        return "no_consent"

    @staticmethod
    def _route(state: ValentinaState) -> str:
        # FAIL-SAFE: on absence/doubt, ALWAYS human (never auto_route by omission).
        return "auto_route" if state.get("route") == "auto_route" else "human_review"

    @staticmethod
    def _route_human(motivo: MotivoHumano, dmn_refs: dict[str, str]) -> dict[str, Any]:
        return {
            "route": "human_review",
            "motivo_humano": motivo,
            "grupo_humano": GRUPO_COORDENACAO_CLINICA,
            "dmn_refs": dmn_refs,
        }

    @staticmethod
    def _sla_input(state: ValentinaState) -> dict[str, Any]:
        return {"programa_id": str(state.get("programa_id", ""))}

    @staticmethod
    def _sla_base(sla: dict[str, Any], dmn_refs: dict[str, str]) -> dict[str, Any]:
        sla_row = sla.get("row", {}) if not sla.get("error") else {}
        if not sla.get("error"):
            dmn_refs[DMN_PROGRAMA_SLA] = sla["ref"]
        return {
            "sla_decisao": str(sla_row.get("sla_decisao", "")),
            "sla_alerta": str(sla_row.get("sla_alerta", "")),
        }

    # -- DMN (ADR-0028): the engine evaluates; the LLM never decides ----------------------------

    async def _evaluate_dmn(self, table: str, dmn_input: dict[str, Any]) -> dict[str, Any]:
        try:
            rows, version = await self._dmn.evaluate(table, dmn_input)
            row = first_row(rows, table, dmn_input)
        except (DmnEvaluationError, DmnNoResultError) as exc:
            return {"error": f"DMN `{table}` indisponivel: {exc}"}
        # See `rafael/graph.py::_evaluate_dmn`/`helena/graph.py::_evaluate_dmn` for why this
        # cites the decision-definition id (ADR-0028 §2) rather than a rule id the engine's
        # evaluate response never returns.
        return {"row": row, "ref": f"{table}#{version.id}"}

    # -- Contract variables + dossier assembly (ADR-0007 audit provenance) --------------------

    def _contract_variables(self, state: ValentinaState) -> dict[str, Any]:
        """Assembles the SP-OP-PROGRAMA-001 start variables (exactly the contract's input set).

        Includes Valentina's dossier as `dossie_valentina` (instruction) and
        `motivo_encaminhamento`/`grupo_destino` (bounded class tokens) when routed human —
        NEVER a disenrollment/discharge/denial. Engine-variable hygiene: `error`/`dmn_error`
        (raw exception/transport text) are DELIBERATELY never included — only class tokens
        travel. `summary_facts` (raw FHIR content) is DELIBERATELY never included either.

        STRUCTURAL GUARDRAIL (L0 hard): the human-decision fields are explicitly `None` here —
        the clinical decision belongs to `UT_DecisaoClinica`; the engine worker
        `register_program_discharge` requires them human-set and refuses otherwise
        (`ERR_PROGRAM_DISCHARGE_NOT_HUMAN`).
        """
        variables: dict[str, Any] = {
            "tenant_id": state.get("tenant_id", ""),
            "programa_id": state.get("programa_id", ""),
            "beneficiario_pseudo_id": state.get("beneficiario_pseudo_id", ""),
            "ciclo": state.get("ciclo", ""),
            "gatilho": state.get("gatilho", ""),
            "consent_scope": state.get("consent_scope", CONSENT_SCOPE_PROGRAMA),
            "consentimento_ativo": bool(state.get("consentimento_ativo", False)),
            "consent_checked": bool(state.get("consent_checked", False)),
            # COMPUTED chokepoint verdict (LGPD audit; the engine's own ST_CheckConsent
            # re-verifies in-process regardless — defense in depth).
            "consent_status": state.get("consent_status") or "",
            # Informative stratification (suggests eligibility — NEVER discharges/denies).
            "elegivel_programa": state.get("elegivel_programa") or "",
            "risco_estratificado": str(state.get("risco_estratificado", "")),
            "elegibilidade_criterios_atendidos": bool(state.get("elegibilidade_criterios_atendidos", False)),
            "criterio_alta_aparente": bool(state.get("criterio_alta_aparente", False)),
            # Agent provenance (audit, ADR-0007).
            "source_agent_id": "valentina",
            "source_agent_version": self._agent_version,
            # Valentina's dossier + routing (instruction, NEVER an adverse decision).
            "dossie_valentina": state.get("dossier") or {},
            "valentina_task": _task(state),
            "valentina_route": state.get("route", "human_review"),
            # STRUCTURAL GUARDRAIL: Valentina NEVER fills the adverse clinical decision. These
            # exist to make explicit that the disenrollment belongs to the human clinician
            # (always None here); `register_program_discharge` requires them human-set.
            "decisao_programa": None,  # ENROLL/MANTER/DESLIGAR_CLINICO: SOLELY UT_DecisaoClinica
            "motivo_desligamento_clinico": None,  # SOLELY the human clinician
            "referencia_clinica": None,  # SOLELY the human clinician
            "responsavel_clinico_id": None,  # SOLELY the human clinician
        }
        if state.get("proactive_trigger_ref"):
            variables["proactive_trigger_ref"] = state["proactive_trigger_ref"]
        if state.get("consent_event_ref"):
            variables["consent_event_ref"] = state["consent_event_ref"]
        if state.get("patient_summary_ref"):
            variables["patient_summary_ref"] = state["patient_summary_ref"]
        if state.get("route") == "human_review":
            # `or`-based (not `.get(key, default)`): post-sanitization these keys EXIST with
            # None/"" until a node sets them — the default must still apply then.
            variables["motivo_encaminhamento"] = state.get("motivo_humano") or "outro"
            variables["grupo_destino"] = state.get("grupo_humano") or GRUPO_COORDENACAO_CLINICA
        # Auditable rule references (ADR-0007/0012). `dmn_error` deliberately never ships.
        dmn_refs = state.get("dmn_refs")
        if dmn_refs:
            variables["dmn_decision_refs"] = dmn_refs
        return variables

    async def _build_dossier(self, state: ValentinaState, *, route: Route) -> tuple[dict[str, Any], bool]:
        """Assembles the stratification/care-plan dossier. The LLM reasons over the FACTS; it
        never decides. Returns `(dossier, llm_ok)`: on LLM failure the dossier falls back to
        the deterministic minimum (empty narrative) and `llm_ok=False` — `auto_route` uses the
        flag to downgrade to `human_review` (charter hardening), `human_review` ignores it (the
        route is already the fail-safe one)."""
        task = _task(state)
        if task == "enroll":
            prompt_text = care_plan_prompt()
            prompt_version = CARE_PLAN_PROMPT_VERSION
        else:
            prompt_text = stratification_prompt()
            prompt_version = STRATIFICATION_PROMPT_VERSION
        facts = self._care_facts(state)

        motivo_humano = state.get("motivo_humano") if route == "human_review" else None
        grupo_humano = state.get("grupo_humano") if route == "human_review" else None
        prompt = (
            f"{prompt_text}\n\ntask={task} route={route} motivo_humano={motivo_humano}\n"
            f"{render_fatos_para_prompt(facts, booleanos=_FATOS_BOOLEANOS)}"
        )
        llm_ok = True
        try:
            # Zona PHI (D10): Valentina reasons in-zone AFTER the consent gate — phi=True on
            # EVERY LLM call in this module (ADR-0006/ADR-0017/T1.7).
            narrativa = await self._llm.generate(
                prompt,
                phi=True,
                agent_id="valentina",
                tenant_id=state.get("tenant_id", ""),
                # ADR-0009 §2 / CC-12: dossie lido pelo humano/clinico antes de decidir -> reasoning.
                task_kind="reasoning",
            )
        except Exception:  # noqa: BLE001 — deterministic minimal dossier; caller decides route.
            narrativa = ""
            llm_ok = False

        dossier = {
            "prompt_version": prompt_version,
            "task": task,
            "route": route,
            "consent_status": state.get("consent_status"),
            "motivo_humano": motivo_humano,
            "grupo_humano": grupo_humano,
            "fatos": facts,
            "dmn_decision_refs": state.get("dmn_refs") or {},
            "narrativa": narrativa,
            # STRUCTURAL GUARDRAIL (L0 hard): the dossier NEVER carries an adverse clinical
            # decision. These exist to make explicit the decision is the human clinician's.
            "decisao_clinica": None,  # enrollment/manutencao/desligamento: SOLELY UT_DecisaoClinica
            "decisao_desligamento": None,  # alta/desligamento clinico: SOLELY the human clinician
        }
        return dossier, llm_ok

    @staticmethod
    def _care_facts(state: ValentinaState) -> dict[str, Any]:
        """Dossier facts — identifiers, consent verdict, bands, DMN outputs and refs. NEVER the
        raw FHIR `summary_facts` (those stay in graph state; only gap notes travel)."""
        return {
            "programa_id": state.get("programa_id"),
            "ciclo": state.get("ciclo"),
            "beneficiario_pseudo_id": state.get("beneficiario_pseudo_id"),
            "gatilho": state.get("gatilho"),
            "consent_status": state.get("consent_status"),
            "risco_estratificado": state.get("risco_estratificado"),
            "elegibilidade_criterios_atendidos": state.get("elegibilidade_criterios_atendidos"),
            "criterio_alta_aparente": state.get("criterio_alta_aparente"),
            "elegivel_programa": state.get("elegivel_programa"),
            "motivo_estratificacao": state.get("motivo_estratificacao"),
            "dmn_refs": state.get("dmn_refs") or {},
            "sla_decisao": state.get("sla_decisao"),
            "lacunas_enriquecimento": state.get("gather_notes") or [],
        }

    # -- Graph assembly ---------------------------------------------------------------------

    def compile_graph(self) -> StateGraph[ValentinaState]:
        g: StateGraph[ValentinaState] = StateGraph(ValentinaState)
        g.add_node("receive", self.receive)
        g.add_node("consent_gate", self.consent_gate)
        g.add_node("no_consent", self.no_consent)
        g.add_node("stopped", self.stopped)
        g.add_node("gather", self.gather)
        g.add_node("assess", self.assess)
        g.add_node("auto_route", self.auto_route)
        g.add_node("human_review", self.human_review)
        g.add_node("start_process", self.start_process)
        g.add_node("notify_start_failure", self.notify_start_failure)
        g.add_node("finalize", self.finalize)

        g.add_edge(START, "receive")
        g.add_edge("receive", "consent_gate")
        # CHOKEPOINT (A/B): PHI is only processed with an active, COMPUTED consent verdict.
        # Revoked -> `stopped`; absent/ambiguous/unverifiable -> `no_consent`. Both are NEUTRAL
        # terminals wired straight to END: `gather`/`assess`/`start_process` are structurally
        # unreachable without consent (zero PHI, zero DMN, zero process).
        g.add_conditional_edges(
            "consent_gate",
            self._consent_branch,
            {"proceed": "gather", "no_consent": "no_consent", "stopped": "stopped"},
        )
        g.add_edge("no_consent", END)
        g.add_edge("stopped", END)
        g.add_edge("gather", "assess")
        g.add_conditional_edges(
            "assess", self._route, {"auto_route": "auto_route", "human_review": "human_review"}
        )
        g.add_edge("auto_route", "start_process")
        g.add_edge("human_review", "start_process")
        # CC-01: a aresta que sai de `start_process` e CONDICIONAL. Uma falha tecnica de
        # start desvia para `notify_start_failure` (desfecho de erro + alerta); qualquer
        # outro caminho — incluindo os no-ops legitimos com `process_started=False` —
        # segue para o terminal de sempre. O predicado e compartilhado (uma definicao).
        g.add_conditional_edges(
            "start_process",
            route_after_start,
            {"notify_start_failure": "notify_start_failure", "continue": "finalize"},
        )
        g.add_edge("notify_start_failure", END)
        g.add_edge("finalize", END)
        return g


def build(config: dict[str, Any] | None = None) -> StateGraph[ValentinaState]:
    """Contract `_template/graph.py:build`, resolved by `AgentLoader`/`runtime.harness.Harness`.

    `config` MUST contain `inference` (ADR-0009), `dmn` (ADR-0028/T1.5), `cibseven`
    (ADR-0001/T1.11). `fhir` is OPTIONAL (see module docstring's labeled boundary) — its absence
    never fails the build, only degrades `gather` to a disclosed gap note.

    Fail-closed: missing a REQUIRED dependency raises `ValueError` at build time.
    """
    cfg = config or {}
    inference = cfg.get("inference")
    dmn = cfg.get("dmn")
    cibseven = cfg.get("cibseven")
    audit_sink = cfg.get("audit_sink")
    missing = [
        name
        for name, value in (
            ("inference", inference),
            ("dmn", dmn),
            ("cibseven", cibseven),
            ("audit_sink", audit_sink),
        )
        if value is None
    ]
    if missing:
        raise ValueError(
            f"Valentina build(config) is missing required dependencies: {missing} "
            "(ADR-0001/0007/0009/0028 — inference/dmn/cibseven/audit_sink must all be injected; "
            "audit_sink is the T-C2 fence — no process start without a durable sink)"
        )
    agent_version = str(cfg.get("agent_version", "valentina@v0"))
    return ValentinaGraph(
        inference=cast(InferenceProvider, inference),
        dmn=cast(DmnTransport, dmn),
        cibseven=cast(CibSevenTransport, cibseven),
        audit_sink=cast(AuditStartSink, audit_sink),
        fhir=cast("PatientSummaryReader | None", cfg.get("fhir")),
        agent_version=agent_version,
    ).compile_graph()


# Prompt versions exposed for audit/eval gating (ADR-0007/0009).
PROMPT_VERSIONS: dict[str, str] = {
    "system": SYSTEM_PROMPT_VERSION,
    "stratification": STRATIFICATION_PROMPT_VERSION,
    "care_plan": CARE_PLAN_PROMPT_VERSION,
}

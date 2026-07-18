"""Gustavo Andrade — Operador do Calendario Regulatorio ANS + Instrucao de NIP (Phase 2, T1.12).

Two processes, one agent (mirrors the v1 donor's structure, READ-ONLY reference
`Maezo-Healthcare-Plan src/maezo/agents/gustavo/graph.py`, adapted to v2's flatter seam set —
same rationale as `agents/helena/graph.py` / `agents/rafael/graph.py` / `agents/carolina/
graph.py`'s module docstrings: v2 has no `ToolRegistry`/PEP-gateway wiring for agent tool calls
yet, so this graph's nodes call the seams already on `main` DIRECTLY: `DmnTransport.evaluate`
(ADR-0028/T1.5), `InferenceProvider.generate(phi=True)` (ADR-0009/T1.7), and
`CibSevenTransport`/`start_process_idempotent` (ADR-0001, T1.11)):

    receive -> gather -> assess -> {review_submission | instruct_nip} -> start_process -> finalize

  J1 (ANS-SUBMIT, calendar-driven): assemble/validate happen in WORKERS (`regulatorio.anssubmit.
     assemble`/`validate`, engine-side) — Gustavo consumes the pre-resolved facts
     (`dataset_complete`/`schema_valid`/`lgpd_anonimizado`), evaluates `ans_calendar` +
     `ans_submission_admissibility`, and ALWAYS routes to `review_submission`: a HUMAN
     (`UT_RevisarEnvio`, grupo regulatorio-ans) signs the binding filing
     (decisao_envio=APROVAR_ENVIO + revisor_id, worker guard ERR_ANS_SUBMIT_NOT_HUMAN).
     Gustavo NEVER transmits — even `SEGUE_ENVIO` only ENABLES the human review task. Then
     starts SP-OP-ANS-SUBMIT-001 idempotently (bk `ANSSUB-{tenant}-{report_type}-{competencia}`).

  J2 (NIP, instruction): a NIP arrives (msg / A2A `nip.instruct`) — Gustavo evaluates
     `nip_classification` + `nip_sla` + `nip_routing`, assembles the instruction dossier, and
     ALWAYS routes to `instruct_nip`: a HUMAN authors/approves the response
     (`UT_ElaborarRespostaNip` / `UT_RevisaoJuridicaNip`). Gustavo NEVER decides to maintain a
     denial — `nip_manter_negativa` is a hard-deny action in the PEP; MANTER_NEGATIVA is born
     EXCLUSIVELY in `UT_RevisaoJuridicaNip` (worker guard ERR_NIP_NEGATIVA_NOT_HUMAN). Then
     starts SP-OP-NIP-001 idempotently (bk `NIP-{tenant}-{numero_nip_ans}`).

`assess` ALWAYS consults the deterministic DMNs (ADR-0012); the LLM reasons over the RESULTS to
assemble the dossier — it NEVER substitutes them nor decides the rule. The routing between the
two work nodes is a function EXCLUSIVELY of the flow type (`fluxo in {ans_submit, nip}`), never
of a merit.

L0 HARD INVARIANT (ADR-0005/0007, contracts SP-OP-NIP-001 §Invariante L0 hard +
SP-OP-ANS-SUBMIT-001 §Invariante HITL pre-filing): BOTH journeys ALWAYS route to a human for the
binding/adverse action. Structurally enforced:
  - `Route` admits ONLY `{"review_submission", "instruct_nip"}` — BOTH are human-instruction
    destinations; no transmit/maintain-denial/deny/auto variant exists in the type.
  - The dossier's `decisao_merito` (manter/conceder NIP) and `assinatura_envio` (binding ANS
    transmission) are ALWAYS `None` (`_build_dossier`) — explicit guardrails making it
    structurally clear both belong to the human, never to this code.
  - The engine variables ship `decisao_envio=None` (J1) / `decisao_nip=None` (J2) — the OUTPUT
    variables only the human User Tasks may fill; the gated workers
    (`regulatorio.anssubmit.submit` / `operadora.nip.submit_response`) refuse anything else.
  - NO DMN this graph consults has an adverse output by contract design (see each table's own
    `<description>`): `ans_submission_admissibility`'s domain is exactly
    {SEGUE_ENVIO, PENDENTE, REVISAO_HUMANA} (no reject), `nip_classification`/`nip_routing`
    classify/route only (no decisao_nip output), `ans_calendar`/`nip_sla` resolve dates/SLAs.
  - FAIL-SAFE FECHADO: DMN unavailable, a value outside the CLOSED allowlists below, or any
    ambiguity -> the conservative human route with a bounded motivo token. A planted/unknown
    value NEVER "passes through" — and there is no non-human destination to fall through to.

MANDATORY HARDENING (T1.12 charter — the caller-planted output-field class all four tranche-1
graphs exhibited; carolina's R1 cycle-1 fix, baked in here from the start):
  - `receive` is the SINGLE entry node (START has exactly one edge, regression-tested) and
    merges `_output_field_resets()` into its return on EVERY path (success AND each fail-safe
    bail), so a caller-planted value in ANY output-only `GustavoState` field is overwritten
    before gather/assess run. A planted `route` can never skip/redirect the human task: it is
    wiped at entry, `assess` re-derives it from `fluxo` + the DMNs, and `_route` fail-safes by
    `fluxo` to a HUMAN destination on absence/doubt (both destinations are human).
  - `_CALLER_INPUT_FIELDS` partitions `GustavoState` — the partition-completeness test
    (`test_output_field_partition_is_complete`) forces every new state field to be classified.
  - Error bails return the fail-safe human route (never `{}` from `receive`); the downstream
    early-bail guards (`if state.get("error"): return {}`) are safe ONLY because `receive` is
    the single entry — the only `error` that can exist when they run is `receive`'s own.
  - Class-token-only failure reasons: `error` carries a bounded CLASS TOKEN
    (`missing_tenant_id`/`invalid_fluxo`/...), NEVER the raw offending value; free-text fields
    (`tema_nip`, `referencia_negativa_original`) travel ONLY in their own contract slots, never
    echoed into `motivo_encaminhamento`/`error`/engine class-token fields.

PHI discipline (ADR-0006/ADR-0017, T1.7): Gustavo's `security_zone` is `general`
(`spec/agents/gustavo/agent.yaml`) and his data arrives pseudonymized/aggregated —
`beneficiario_pseudo_id` never carries CPF/name; ANS datasets travel as `dataset_ref` pointers.
The one LLM call (`_build_dossier`) still passes `phi=True`: NIP dossiers reason over
beneficiary-adjacent facts (tema, referencia da negativa) and the conservative posture of every
in-repo graph is uniform (helena/rafael/carolina/fernando all tag every call).

LABELED BOUNDARIES (this build, disclosed — never fabricated):
- SP-OP-ANS-SUBMIT-001 HAS NO LIVE TRIGGER TODAY (T2.6 re-scope design, merged —
  `docs/design/T2.6-ans-submission-rescope.md` §1.5/§5 T2.6-7): `notification_bridge` registers
  5 handoff rules, NONE targeting ANS-SUBMIT (neither NIP->SUBMIT nor cron->SUBMIT), and nothing
  in `src/` starts SUBMIT. This graph's J1 assess/route logic is therefore implemented and
  UNIT-PROVEN here, but no runtime path invokes it yet — wiring the bridge rules is T2.6-7,
  explicitly out of this charter's scope. Disclosed residual, not fabricated liveness.
- `ans_calendar` carries the T1.5 taxonomy hold (T2.6 design §1.4): the DMN keys `report_type`
  on RN-citation literals (`RN_124_SIP`/...) with zero overlap with the runtime scheduler's
  literals, all dates are `DRAFT_*` placeholders, and several RN citations are misattributed
  (T2.5 currency review). This graph passes `report_type` through VERBATIM to the engine-side
  DMN and consumes whatever the table resolves (the catch-all fail-safes to
  `fonte_regulatoria="REVISAO_HUMANA"` + conservative dates) — it NEVER re-implements or
  reconciles the taxonomy in Python (that reconciliation is T2.6-3, which also clears the T1.5
  cutover hold).
- A2A inbound delegation (`nip.instruct`, `spec/agents/gustavo/agent.yaml`'s
  `accepted_task_types`) is NOT wired: v2's `a2a/` package has no `DelegationEnvelope`/
  `DelegationDispatcher` yet (same boundary rafael/fernando disclose). This graph is invoked
  directly with an already-assembled `GustavoState`, not via a live delegation.
- No episodic memory write (ADR-0002): the donor's `finalize` writes `mcp-memory.read_write`;
  v2's `MemoryServer` has no live Postgres/pgvector schema in this repo's migrations yet — same
  labeled boundary as helena/rafael/carolina. `finalize` is a terminal no-op.
- `gather`'s FHIR enrichment (J2 only) is best-effort and OPTIONAL — reuses rafael's
  `FhirReader` seam shape; absence/failure never blocks routing, only degrades the dossier with
  a disclosed gap note.

DIVERGENCES FROM DONOR (disclosed — spec wins where they disagree):
1. `nip_sla` input: the donor passed only `{classificacao}`; the DEPLOYED v2 table
   (`spec/processes/dmn/nip_sla.dmn` v0.2.0, GAP-NIP-1) also declares
   `data_recebimento_nip_iso` — the regulatory anchor its FEEL expressions read to compute the
   ABSOLUTE deadlines (`prazo_resposta_absoluto_iso`/`sla_alerta_absoluto_iso`). This graph
   passes both and captures all four prazo outputs (the donor captured only the two relative
   legacy ones).
2. `nip_classification`'s `prazo_dias`/`grupo_revisor` and `nip_routing`'s `grupo_humano` are
   CAPTURED here (informational dossier context; the groups are validated against the closed
   group set with the same `juridico-regulatorio` fail-safe the BPMN's own `BRT_Roteamento`
   outputParameter applies — GAP-NIP-2 made `grupo_humano` a consumed output). The donor
   ignored all three.
3. Provenance refs cite the decision-definition id (`{table}#{version.id}`, ADR-0028 §2) — the
   donor fabricated a `rule_id` ref the Camunda evaluate response never returns (see
   `helena/graph.py::_evaluate_dmn`).
4. Direct transports instead of the donor's `ToolInvoker`/PEP gateway; no `model_tiers`
   resolution (v2's `InferenceProvider.generate` has no tier parameter yet — T1.7 boundary).
5. `receive`-time output-field sanitization (`_output_field_resets`) does not exist in the
   donor — it is the T1.12 mandatory hardening.
6. No memory write in `finalize` (labeled boundary above).
"""

from __future__ import annotations

from typing import Any, Literal, Protocol, TypedDict, cast

from langgraph.graph import END, START, StateGraph

from maezo.runtime.inference import InferenceProvider
from maezo.tools.mcp_cibseven.transport import (
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

from .prompts import DOSSIER_PROMPT_VERSION, SYSTEM_PROMPT_VERSION, dossier_prompt

PROCESS_KEY_ANS_SUBMIT = "SP-OP-ANS-SUBMIT-001"
PROCESS_KEY_NIP = "SP-OP-NIP-001"

DMN_ANS_CALENDAR = "ans_calendar"
DMN_ANS_ADMISSIBILITY = "ans_submission_admissibility"
DMN_NIP_CLASSIFICATION = "nip_classification"
DMN_NIP_ROUTING = "nip_routing"
DMN_NIP_SLA = "nip_sla"

# Flow type conducted by Gustavo. Decides WHICH human-instruction node the graph runs — NEVER a
# merit. An unknown/absent value fail-safes to the NIP instruction path (human, conservative).
Fluxo = Literal["ans_submit", "nip"]
_VALID_FLUXOS: frozenset[str] = frozenset({"ans_submit", "nip"})

# Graph routing: BOTH destinations are HUMAN-instruction paths. STRUCTURALLY no adverse variant
# exists (no "transmitir", no "manter_negativa", no "negar") — an adverse route is impossible to
# express in this type.
Route = Literal["review_submission", "instruct_nip"]

# Recognized outputs of `ans_submission_admissibility` (CLOSED allowlist — no "reject" exists by
# the table's own design). Anything else -> conservative human route (fail-safe fechado).
AdmissibilidadeEnvio = Literal["SEGUE_ENVIO", "PENDENTE", "REVISAO_HUMANA"]
_ADMISSIBILIDADE_ENVIO_ALLOW: frozenset[str] = frozenset({"SEGUE_ENVIO", "PENDENTE", "REVISAO_HUMANA"})

# Recognized outputs of `nip_classification` (CLOSED allowlist).
ClassificacaoNip = Literal["ASSISTENCIAL_CONTESTA_NEGATIVA", "ASSISTENCIAL_OUTRO", "NAO_ASSISTENCIAL"]
_CLASSIFICACAO_NIP_ALLOW: frozenset[str] = frozenset(
    {"ASSISTENCIAL_CONTESTA_NEGATIVA", "ASSISTENCIAL_OUTRO", "NAO_ASSISTENCIAL"}
)

# Recognized outputs of `nip_routing` (CLOSED allowlist — no "manter"/"deny" exists by design).
RoteamentoNip = Literal["ELABORAR_RESPOSTA", "PENDENTE_INFO", "REVISAO_JURIDICA"]
_ROTEAMENTO_NIP_ALLOW: frozenset[str] = frozenset({"ELABORAR_RESPOSTA", "PENDENTE_INFO", "REVISAO_JURIDICA"})

# Human groups the NIP DMNs may route to (closed set, mirrors the DMN outputs). A value outside
# this set fail-safes to `juridico-regulatorio` — the SAME conservative fallback the BPMN's own
# `BRT_Roteamento` outputParameter applies (GAP-NIP-2). Never an invented group.
_GRUPO_HUMANO_ALLOW: frozenset[str] = frozenset({"nucleo-ans", "regulatorio-ans", "juridico-regulatorio"})
_GRUPO_HUMANO_FAILSAFE = "juridico-regulatorio"

# Reason the case went to the human route. NONE of these is an adverse outcome — they are
# INSTRUCTION/forwarding reasons; the merit (manter/conceder) and the signature (transmitir) are
# the human's. Bounded CLASS TOKENS only (they reach engine variables via
# `motivo_encaminhamento`).
MotivoHumano = Literal[
    "revisao_envio",  # J1: human signs the binding filing (SEGUE_ENVIO only ENABLES the UT)
    "pendencia_envio",  # J1: dataset/schema/anonymization pending -> human corrects/reviews
    "elaborar_resposta_nip",  # J2: human authors the response (favorable/non-assistencial path)
    "revisao_juridica_nip",  # J2: contests a negativa / catch-all -> juridico (only origin of MANTER)
    "pendente_info_nip",  # J2: documentation insufficient -> awaits info / human
    "dmn_indisponivel",  # any DMN unavailable -> human (fail-safe fechado; never proceeds)
    "ambiguidade",  # DMN value outside the allowlist / ambiguous inputs -> conservative human
    "falha_tecnica",  # missing runtime context -> human by safety
]

_NIP_ROUTING_TO_MOTIVO: dict[RoteamentoNip, MotivoHumano] = {
    "ELABORAR_RESPOSTA": "elaborar_resposta_nip",
    "PENDENTE_INFO": "pendente_info_nip",
    "REVISAO_JURIDICA": "revisao_juridica_nip",
}


class FhirReader(Protocol):
    """Best-effort FHIR read seam (`gather`, J2 only). See module docstring's labeled boundary —
    structurally satisfied by `agents.rafael.adapters.FhirServerReader` when the runtime injects
    it (deliberate duck-typed reuse; mypy checks the call site)."""

    async def read_patient(self, patient_id: str) -> dict[str, Any]: ...


class GustavoState(TypedDict, total=False):
    """Calendar/NIP operation state. Everything here is pseudonymized/aggregated (Zona Geral,
    ADR-0006): `beneficiario_pseudo_id` NEVER carries CPF/name/CNS; ANS datasets are referenced
    by `dataset_ref` (aggregated/anonymized dataset pointer, never raw PHI). The boolean facts
    (`dataset_complete`/`schema_valid`/`lgpd_anonimizado`/`contesta_negativa`/
    `documentacao_suficiente`/`classificacao_nip`) arrive PRE-RESOLVED by deterministic workers
    (contracts SP-OP-ANS-SUBMIT-001 / SP-OP-NIP-001) — Gustavo CONSUMES them, never computes
    them (ADR-0012), and NEVER derives an adverse decision from them."""

    # Runtime identifiers / task framing.
    tenant_id: str
    fluxo: Fluxo  # ans_submit | nip — which process to conduct (NEVER a merit)
    canal: str  # a2a | calendario | portal

    # --- J1 inputs (contract SP-OP-ANS-SUBMIT-001 §Variaveis de entrada) ---
    report_type: str  # RN-citation literal, passed VERBATIM to ans_calendar (T1.5 hold disclosed)
    competencia: str  # YYYY-MM | YYYY-Qn (business-key material)
    periodicidade: str  # mensal | trimestral | anual (worker-derived)
    origem_envio: str  # calendario | nip_filing | retransmissao_manual
    nip_protocolo_origem: str  # required iff origem_envio == nip_filing
    dataset_complete: bool
    schema_valid: bool
    lgpd_anonimizado: bool
    dataset_ref: str

    # --- J2 inputs (contract SP-OP-NIP-001 §Variaveis de entrada) ---
    numero_nip_ans: str
    protocolo_ans: str
    beneficiario_pseudo_id: str
    classificacao_nip: str  # assistencial | nao_assistencial (worker-pre-resolved)
    tema_nip: str
    referencia_negativa_original: str
    data_recebimento_nip_iso: str  # regulatory deadline anchor (GAP-NIP-1)
    documentos_refs: list[dict[str, Any]]
    contesta_negativa: bool
    documentacao_suficiente: bool
    origem_a2a: bool
    patient_ref: str  # pseudonymized FHIR reference (J2 dossier enrichment; never raw PHI)

    # Filled by `gather` (best-effort enrichment; never decides routing).
    gathered: bool
    nip_facts: dict[str, Any]
    gather_notes: list[str]

    # Filled by `assess` (DMN results — only the DMNs, via assess, may fill these).
    admissibilidade_envio: AdmissibilidadeEnvio
    due_date: str  # J1: ans_calendar absolute competencia deadline (dossier/events; not timers)
    sla_alerta_data: str  # J1: ans_calendar absolute alert date
    periodicidade_calendario: str  # J1: ans_calendar-resolved periodicity
    fonte_regulatoria: str  # calendar/SLA regulatory source citation (DRAFT/verify upstream)
    classificacao: ClassificacaoNip
    prazo_dias: int  # J2: nip_classification prazo (dias UTEIS — informational)
    grupo_revisor: str  # J2: nip_classification review group (informational)
    roteamento_nip: RoteamentoNip
    grupo_humano: str  # J2: nip_routing group (closed-set validated, juridico fail-safe)
    prazo_resposta_iso: str  # J2: nip_sla relative duration (legacy/observability)
    prazo_resposta_absoluto_iso: str  # J2: nip_sla ABSOLUTE deadline (BPMN timeDate re-resolves)
    sla_alerta_iso: str
    sla_alerta_absoluto_iso: str
    dmn_refs: dict[str, str]
    dmn_error: str

    # Routing (BOTH destinations human/instruction — NEVER an adverse merit).
    route: Route
    motivo_humano: MotivoHumano

    # Filled by `review_submission`/`instruct_nip`.
    dossier: dict[str, Any]

    # Filled by `start_process`.
    process_key: str
    process_started: bool
    business_key: str
    process_ref: dict[str, Any]

    # Turn output (instruction outcome — NEVER a consummated adverse effect).
    desfecho: str
    error: str


# --- Helpers -----------------------------------------------------------------------------------


def _business_key(state: GustavoState) -> str:
    """Idempotent business key per flow (contracts §Business key).

    J1: `ANSSUB-{tenant}-{report_type}-{competencia}`. J2: `NIP-{tenant}-{numero_nip_ans}`.
    """
    tenant = state.get("tenant_id", "")
    if state.get("fluxo") == "ans_submit":
        return f"ANSSUB-{tenant}-{state.get('report_type', '')}-{state.get('competencia', '')}"
    return f"NIP-{tenant}-{state.get('numero_nip_ans', '')}"


def _process_key(state: GustavoState) -> str:
    return PROCESS_KEY_ANS_SUBMIT if state.get("fluxo") == "ans_submit" else PROCESS_KEY_NIP


# --- State sanitization (T1.12 mandatory hardening — caller-planted output fields) -------------

#: The ONLY fields a caller may legitimately seed on the initial state (runtime identifiers +
#: the two contracts' input variables + the gather reference). Everything else in `GustavoState`
#: is OUTPUT-ONLY: produced exclusively by this graph's own nodes. Single-sourced against
#: `GustavoState` by the partition-completeness regression test
#: (`test_output_field_partition_is_complete`) — a new state field MUST be classified into
#: exactly one of the two sets or that test fails, so the sanitization can never silently drift.
_CALLER_INPUT_FIELDS: frozenset[str] = frozenset(
    {
        "tenant_id",
        "fluxo",
        "canal",
        # J1 contract inputs.
        "report_type",
        "competencia",
        "periodicidade",
        "origem_envio",
        "nip_protocolo_origem",
        "dataset_complete",
        "schema_valid",
        "lgpd_anonimizado",
        "dataset_ref",
        # J2 contract inputs.
        "numero_nip_ans",
        "protocolo_ans",
        "beneficiario_pseudo_id",
        "classificacao_nip",
        "tema_nip",
        "referencia_negativa_original",
        "data_recebimento_nip_iso",
        "documentos_refs",
        "contesta_negativa",
        "documentacao_suficiente",
        "origem_a2a",
        "patient_ref",
    }
)


def _output_field_resets() -> dict[str, Any]:
    """Fresh (never-shared) neutral defaults for EVERY output-only `GustavoState` field.

    T1.12 MANDATORY HARDENING (the caller-planted output-field class all four tranche-1 graphs
    exhibited — carolina's R1 cycle-1 fix, baked in here from the start): `receive` merges these
    into its return on EVERY path, BEFORE gather/assess run. LangGraph merges the caller's
    initial input verbatim, and a key no node overwrites flows through to the final state — so
    without this reset a caller planting e.g. `route`/`dmn_refs`/`dossier`/`business_key` would
    ship forged routing/audit-trail/engine values on any journey where the graph deliberately
    does not compute that field. Post-reset, only node-produced values can reach
    `_build_dossier`/`_contract_variables`. Built fresh per call (function, not module constant)
    so the mutable `{}`/`[]` defaults are never shared across graph invocations.

    `route`/`motivo_humano` sanitize to `None` — `_route` itself fail-safes by `fluxo` to a
    HUMAN destination (both destinations are human; a planted route can neither skip the human
    task nor cross journeys, because `process_key`/`business_key` are also reset here and
    re-derived from `fluxo` + the contract identity).
    """
    return {
        # gather outputs.
        "gathered": False,
        "nip_facts": {},
        "gather_notes": [],
        # assess outputs (DMN results).
        "admissibilidade_envio": None,
        "due_date": "",
        "sla_alerta_data": "",
        "periodicidade_calendario": "",
        "fonte_regulatoria": "",
        "classificacao": None,
        "prazo_dias": 0,
        "grupo_revisor": "",
        "roteamento_nip": None,
        "grupo_humano": "",
        "prazo_resposta_iso": "",
        "prazo_resposta_absoluto_iso": "",
        "sla_alerta_iso": "",
        "sla_alerta_absoluto_iso": "",
        "dmn_refs": {},
        "dmn_error": "",
        # routing outputs.
        "route": None,
        "motivo_humano": None,
        # work-node outputs.
        "dossier": {},
        # start_process outputs.
        "process_key": "",
        "process_started": False,
        "business_key": "",
        "process_ref": {},
        # terminal outputs.
        "desfecho": "",
        "error": "",
    }


class GustavoGraph:
    """Wires Gustavo's injected dependencies into a compilable `StateGraph[GustavoState]`."""

    def __init__(
        self,
        *,
        inference: InferenceProvider,
        dmn: DmnTransport,
        cibseven: CibSevenTransport,
        fhir: FhirReader | None = None,
        agent_version: str = "gustavo@v0",
    ) -> None:
        self._llm = inference
        self._dmn = dmn
        self._cibseven = cibseven
        self._fhir = fhir
        self._agent_version = agent_version

    # -- Nodes ----------------------------------------------------------------------------

    async def receive(self, state: GustavoState) -> dict[str, Any]:
        """Turn start: RESET every output-only field (`_output_field_resets`, EVERY path), then
        defend against missing flow identifiers (fail-safe -> the flow's HUMAN route). Without
        the minimum contract identity there is no idempotent business key — the case still
        routes to a human (with a bounded class token in `error`), never a silent drop and
        never an adverse effect. Class tokens ONLY — the raw offending value is never echoed."""
        if not state.get("tenant_id"):
            return self._fail_safe_min(state, "falha_tecnica", "missing_tenant_id")

        fluxo = state.get("fluxo")
        if fluxo not in _VALID_FLUXOS:
            # Unknown/absent flow -> conservative human NIP-instruction route (never proceeds
            # by omission; never echoes the raw value — class token only).
            return self._fail_safe_min(state, "ambiguidade", "invalid_fluxo")
        if fluxo == "ans_submit" and not (state.get("report_type") and state.get("competencia")):
            return self._fail_safe_min(state, "pendencia_envio", "missing_report_type_competencia")
        if fluxo == "nip" and not state.get("numero_nip_ans"):
            return self._fail_safe_min(state, "revisao_juridica_nip", "missing_numero_nip_ans")

        return {
            **_output_field_resets(),
            "process_key": _process_key(state),
            "business_key": _business_key(state),
        }

    async def gather(self, state: GustavoState) -> dict[str, Any]:
        """Best-effort enrichment (J2 only) — NEVER blocks routing.

        J1 works off the aggregated dataset pointer (`dataset_ref`) — nothing to fetch here.
        J2 may enrich the dossier with a pseudonymized FHIR read; failure/absence degrades to a
        disclosed gap note (classification/routing come from the worker-pre-resolved variables).
        """
        if state.get("error"):
            return {}  # already routed by receive's fail-safe (single-entry guarantee)
        if state.get("fluxo") != "nip":
            return {"gathered": True, "nip_facts": {}, "gather_notes": []}

        nip_facts: dict[str, Any] = {}
        notes: list[str] = []
        patient_ref = state.get("patient_ref")
        if self._fhir is None:
            notes.append(
                "leitor FHIR nao configurado para este build (labeled boundary — ver docstring "
                "do modulo graph.py); dossie prossegue so com as variaveis pre-resolvidas."
            )
        elif patient_ref:
            try:
                nip_facts = await self._fhir.read_patient(patient_ref)
            except Exception as exc:  # noqa: BLE001 — best-effort enrichment, never fatal.
                notes.append(f"beneficiario FHIR indisponivel: {type(exc).__name__}")
        return {"gathered": True, "nip_facts": nip_facts, "gather_notes": notes}

    async def assess(self, state: GustavoState) -> dict[str, Any]:
        """Evaluate the deterministic DMNs and resolve the (ALWAYS human) route.

        ADR-0012: the DMN decides; the LLM only reasons over the result (dossier, downstream).
        The routing is by FLOW, never by merit: ans_submit -> review_submission (human signs);
        nip -> instruct_nip (human authors/approves). FAIL-SAFE FECHADO: any DMN unavailable or
        out-of-allowlist value -> the conservative human route; nothing ever "passes through".
        """
        if state.get("error"):
            return {}  # already routed by receive's fail-safe (single-entry guarantee)
        if state.get("fluxo") == "ans_submit":
            return await self._assess_ans_submit(state)
        return await self._assess_nip(state)

    async def _assess_ans_submit(self, state: GustavoState) -> dict[str, Any]:
        """J1: `ans_calendar` (dates) + `ans_submission_admissibility` (no 'reject' by design).

        Whatever the results, the destination is ALWAYS `review_submission` — the human signs.
        `SEGUE_ENVIO` only ENABLES the approval User Task; missing data -> PENDENTE/
        REVISAO_HUMANA (never a rejection); out-of-allowlist -> conservative human.
        """
        dmn_refs: dict[str, str] = {}

        cal_result = await self._evaluate_dmn(
            DMN_ANS_CALENDAR,
            {
                # Passed VERBATIM — the T1.5 taxonomy hold lives in the TABLE's domain, never
                # re-reconciled in Python (module docstring's labeled boundary).
                "report_type": str(state.get("report_type", "")),
                "competencia": str(state.get("competencia", "")),
            },
        )
        if cal_result.get("error"):
            # Calendar unavailable -> human immediately (never an invented/"infinite" deadline;
            # donor parity: admissibility is not consulted on this path).
            return self._route_review_submission(
                "dmn_indisponivel", dmn_refs, dmn_error=str(cal_result["error"])
            )
        cal_row = cal_result["row"]
        dmn_refs[DMN_ANS_CALENDAR] = cal_result["ref"]
        base: dict[str, Any] = {
            "due_date": str(cal_row.get("due_date", "")),
            "sla_alerta_data": str(cal_row.get("sla_alerta", "")),
            "periodicidade_calendario": str(cal_row.get("periodicidade", "")),
            "fonte_regulatoria": str(cal_row.get("fonte_regulatoria", "")),
        }

        admis_result = await self._evaluate_dmn(
            DMN_ANS_ADMISSIBILITY,
            {
                "dataset_complete": bool(state.get("dataset_complete", False)),
                "schema_valid": bool(state.get("schema_valid", False)),
                "lgpd_anonimizado": bool(state.get("lgpd_anonimizado", False)),
            },
        )
        if admis_result.get("error"):
            return {
                **base,
                **self._route_review_submission(
                    "dmn_indisponivel", dmn_refs, dmn_error=str(admis_result["error"])
                ),
            }
        dmn_refs[DMN_ANS_ADMISSIBILITY] = admis_result["ref"]
        roteamento = str(admis_result["row"].get("roteamento", ""))
        if roteamento not in _ADMISSIBILIDADE_ENVIO_ALLOW:
            # FAIL-SAFE FECHADO: an out-of-allowlist value is a DMN-contract violation and
            # never "passes through" — conservative human review instead.
            return {**base, **self._route_review_submission("ambiguidade", dmn_refs)}
        admissibilidade = cast(AdmissibilidadeEnvio, roteamento)
        motivo: MotivoHumano = (
            "pendencia_envio" if admissibilidade in ("PENDENTE", "REVISAO_HUMANA") else "revisao_envio"
        )
        return {
            **base,
            "admissibilidade_envio": admissibilidade,
            **self._route_review_submission(motivo, dmn_refs),
        }

    async def _assess_nip(self, state: GustavoState) -> dict[str, Any]:
        """J2: `nip_classification` -> `nip_sla` (best-effort) -> `nip_routing` — none has a
        denial/merit output by design. Whatever the results, the destination is ALWAYS
        `instruct_nip` — the human authors/approves; out-of-allowlist -> conservative juridico.
        """
        dmn_refs: dict[str, str] = {}

        clf_result = await self._evaluate_dmn(
            DMN_NIP_CLASSIFICATION,
            {
                "classificacao_nip": str(state.get("classificacao_nip", "")),
                "tema_nip": str(state.get("tema_nip", "")),
                "contesta_negativa": bool(state.get("contesta_negativa", False)),
            },
        )
        if clf_result.get("error"):
            return self._route_instruct_nip("dmn_indisponivel", dmn_refs, dmn_error=str(clf_result["error"]))
        clf_row = clf_result["row"]
        dmn_refs[DMN_NIP_CLASSIFICATION] = clf_result["ref"]
        classificacao_raw = str(clf_row.get("classificacao", ""))
        if classificacao_raw not in _CLASSIFICACAO_NIP_ALLOW:
            # FAIL-SAFE FECHADO: unknown classification -> conservative juridico review.
            return self._route_instruct_nip("ambiguidade", dmn_refs)
        classificacao = cast(ClassificacaoNip, classificacao_raw)
        grupo_revisor_raw = str(clf_row.get("grupo_revisor", ""))
        base: dict[str, Any] = {
            "classificacao": classificacao,
            "prazo_dias": int(clf_row.get("prazo_dias", 0) or 0),
            "grupo_revisor": (
                grupo_revisor_raw if grupo_revisor_raw in _GRUPO_HUMANO_ALLOW else _GRUPO_HUMANO_FAILSAFE
            ),
        }

        # SLA: best-effort informational context (deadlines feed the dossier; the REAL timers
        # come from the BPMN's own `BRT_NipSla` re-evaluation — GAP-NIP-1). Failure never blocks
        # the already-decided human route. Divergence #1: `data_recebimento_nip_iso` is passed
        # (the deployed table's FEEL reads it to compute the ABSOLUTE deadlines).
        sla_result = await self._evaluate_dmn(
            DMN_NIP_SLA,
            {
                "classificacao": classificacao,
                "data_recebimento_nip_iso": str(state.get("data_recebimento_nip_iso", "")),
            },
        )
        if not sla_result.get("error"):
            sla_row = sla_result["row"]
            dmn_refs[DMN_NIP_SLA] = sla_result["ref"]
            base["prazo_resposta_iso"] = str(sla_row.get("prazo_resposta_iso", ""))
            base["prazo_resposta_absoluto_iso"] = str(sla_row.get("prazo_resposta_absoluto_iso", ""))
            base["sla_alerta_iso"] = str(sla_row.get("sla_alerta_iso", ""))
            base["sla_alerta_absoluto_iso"] = str(sla_row.get("sla_alerta_absoluto_iso", ""))
            if sla_row.get("fonte_regulatoria"):
                base["fonte_regulatoria"] = str(sla_row["fonte_regulatoria"])

        rot_result = await self._evaluate_dmn(
            DMN_NIP_ROUTING,
            {
                "classificacao": classificacao,
                "documentacao_suficiente": bool(state.get("documentacao_suficiente", False)),
            },
        )
        if rot_result.get("error"):
            return {
                **base,
                **self._route_instruct_nip("dmn_indisponivel", dmn_refs, dmn_error=str(rot_result["error"])),
            }
        rot_row = rot_result["row"]
        dmn_refs[DMN_NIP_ROUTING] = rot_result["ref"]
        roteamento_raw = str(rot_row.get("roteamento", ""))
        if roteamento_raw not in _ROTEAMENTO_NIP_ALLOW:
            # FAIL-SAFE FECHADO: unknown routing -> conservative juridico review.
            return {**base, **self._route_instruct_nip("ambiguidade", dmn_refs)}
        roteamento_nip = cast(RoteamentoNip, roteamento_raw)
        grupo_raw = str(rot_row.get("grupo_humano", ""))
        return {
            **base,
            "roteamento_nip": roteamento_nip,
            "grupo_humano": grupo_raw if grupo_raw in _GRUPO_HUMANO_ALLOW else _GRUPO_HUMANO_FAILSAFE,
            **self._route_instruct_nip(_NIP_ROUTING_TO_MOTIVO[roteamento_nip], dmn_refs),
        }

    async def review_submission(self, state: GustavoState) -> dict[str, Any]:
        """J1 work node: prepare the case for the HUMAN review of the filing.

        GUARDRAIL: this node NEVER transmits to the ANS. It instructs the filing (dataset
        summary, deadlines, admissibility) and marks an INSTRUCTION outcome. The binding
        transmission belongs to `UT_RevisarEnvio` (human signs; `ans_official_submission` is
        PEP-gated require-human; worker guard ERR_ANS_SUBMIT_NOT_HUMAN)."""
        dossier = await self._build_dossier(state, route="review_submission")
        return {"dossier": dossier, "desfecho": "envio_encaminhado_revisao_humana"}

    async def instruct_nip(self, state: GustavoState) -> dict[str, Any]:
        """J2 work node: assemble the instruction dossier and mark the human route.

        This is the route of EVERY NIP — including the ones contesting a negativa. NONE is
        decided here: MANTER_NEGATIVA is born SOLELY in `UT_RevisaoJuridicaNip` (human;
        `nip_manter_negativa` is a hard-deny action in the PEP; worker guard
        ERR_NIP_NEGATIVA_NOT_HUMAN). Gustavo instructs; the human authors/approves."""
        dossier = await self._build_dossier(state, route="instruct_nip")
        return {"dossier": dossier, "desfecho": "nip_encaminhada_instrucao_humana"}

    async def start_process(self, state: GustavoState) -> dict[str, Any]:
        """Start the flow's process idempotently (business key per contract; the start consults
        the key before creating — a re-dispatch returns the active instance).

        A start failure never loses the case: it records the error and keeps the human route
        observable. When `receive` fail-safed WITHOUT a valid contract identity (its own
        `error` is set and no business key was derivable), no engine start is attempted — a
        garbage/non-idempotent business key must never reach the engine; the unstarted
        escalation surfaces via `process_started=False` + the class token (donor parity)."""
        if state.get("error") and not state.get("business_key"):
            return {"process_started": False}

        process_key = state.get("process_key") or _process_key(state)
        business_key = state.get("business_key") or _business_key(state)
        variables = self._contract_variables(state)
        try:
            instance = await start_process_idempotent(
                self._cibseven, process_key=process_key, business_key=business_key, variables=variables
            )
        except CibSevenError as exc:
            return {
                "process_started": False,
                "business_key": business_key,
                "error": f"start_process indisponivel: {exc}",
            }
        return {
            "process_started": True,
            "business_key": business_key,
            "process_ref": {
                "instance_id": instance.instance_id,
                "state": instance.state,
                "already_existed": instance.already_existed,
            },
        }

    async def finalize(self, state: GustavoState) -> dict[str, Any]:
        """Terminal node — no further computation; `desfecho` was set by the work node. No
        episodic memory write (module docstring's labeled boundary; donor divergence #6)."""
        return {}

    # -- Conditional routing ----------------------------------------------------------------

    @staticmethod
    def _route(state: GustavoState) -> str:
        """Route between the two work nodes — BOTH are human-instruction destinations.

        FAIL-SAFE FECHADO: on absence/doubt of `route`, pick the work node by `fluxo`; if the
        flow is not ans_submit, fall to `instruct_nip` (human). There is NO adverse destination
        to fall through to — the `Route` type does not admit one."""
        route = state.get("route")
        if route == "review_submission":
            return "review_submission"
        if route == "instruct_nip":
            return "instruct_nip"
        return "review_submission" if state.get("fluxo") == "ans_submit" else "instruct_nip"

    @staticmethod
    def _fail_safe_min(state: GustavoState, motivo: MotivoHumano, error_token: str) -> dict[str, Any]:
        """Minimal fail-safe route used by `receive`, before any DMN runs. `error_token` is a
        bounded CLASS TOKEN ONLY — never the raw offending value. Merges `_output_field_resets()`
        FIRST: the receive-fail journeys are exactly where caller-planted output fields would
        otherwise read through (no downstream node overwrites them on these paths). The route is
        the flow's own HUMAN destination; `process_key` is recorded when the flow is known (so
        the failure is attributable), but `business_key` stays reset ("") — `start_process`
        refuses to start off a non-derivable identity."""
        fluxo = state.get("fluxo")
        route: Route = "review_submission" if fluxo == "ans_submit" else "instruct_nip"
        out: dict[str, Any] = {
            **_output_field_resets(),
            "route": route,
            "motivo_humano": motivo,
            "error": error_token,
        }
        if fluxo in _VALID_FLUXOS:
            out["process_key"] = _process_key(state)
        return out

    @staticmethod
    def _route_review_submission(
        motivo: MotivoHumano, dmn_refs: dict[str, str], *, dmn_error: str | None = None
    ) -> dict[str, Any]:
        out: dict[str, Any] = {"route": "review_submission", "motivo_humano": motivo, "dmn_refs": dmn_refs}
        if dmn_error is not None:
            out["dmn_error"] = dmn_error
        return out

    @staticmethod
    def _route_instruct_nip(
        motivo: MotivoHumano, dmn_refs: dict[str, str], *, dmn_error: str | None = None
    ) -> dict[str, Any]:
        out: dict[str, Any] = {"route": "instruct_nip", "motivo_humano": motivo, "dmn_refs": dmn_refs}
        if dmn_error is not None:
            out["dmn_error"] = dmn_error
        return out

    # -- DMN (ans_calendar / ans_submission_admissibility / nip_classification / nip_sla /
    # nip_routing; none has an adverse output by design) ---------------------------------------

    async def _evaluate_dmn(self, table: str, dmn_input: dict[str, Any]) -> dict[str, Any]:
        try:
            rows, version = await self._dmn.evaluate(table, dmn_input)
            row = first_row(rows, table, dmn_input)
        except (DmnEvaluationError, DmnNoResultError) as exc:
            return {"error": f"DMN `{table}` indisponivel: {exc}"}
        # See `helena/graph.py::_evaluate_dmn` for why this cites the decision-definition id
        # (ADR-0028 §2) rather than a rule id the engine's evaluate response never returns
        # (donor divergence #3).
        return {"row": row, "ref": f"{table}#{version.id}"}

    # -- Dossier assembly (ADR-0007 audit provenance; L0-hard structural guardrail) ------------

    async def _build_dossier(self, state: GustavoState, *, route: Route) -> dict[str, Any]:
        """Assemble the instruction dossier. The LLM reasons over the FACTS; it never decides
        nor signs. LLM failure degrades to a deterministic facts-only dossier — it NEVER blocks
        the already-decided human route."""
        if state.get("fluxo") == "ans_submit":
            facts: dict[str, Any] = {
                "fluxo": "ans_submit",
                "report_type": state.get("report_type"),
                "competencia": state.get("competencia"),
                "periodicidade": state.get("periodicidade"),
                "origem_envio": state.get("origem_envio"),
                "dataset_complete": state.get("dataset_complete"),
                "schema_valid": state.get("schema_valid"),
                "lgpd_anonimizado": state.get("lgpd_anonimizado"),
                "dataset_ref": state.get("dataset_ref"),
                "admissibilidade_envio": state.get("admissibilidade_envio"),
                "due_date": state.get("due_date"),
                "sla_alerta_data": state.get("sla_alerta_data"),
                "periodicidade_calendario": state.get("periodicidade_calendario"),
                "fonte_regulatoria": state.get("fonte_regulatoria"),
                "dmn_refs": state.get("dmn_refs", {}),
            }
        else:
            facts = {
                "fluxo": "nip",
                "numero_nip_ans": state.get("numero_nip_ans"),
                "protocolo_ans": state.get("protocolo_ans"),
                "tema_nip": state.get("tema_nip"),
                "classificacao_nip": state.get("classificacao_nip"),
                "classificacao": state.get("classificacao"),
                "prazo_dias": state.get("prazo_dias"),
                "grupo_revisor": state.get("grupo_revisor"),
                "contesta_negativa": state.get("contesta_negativa"),
                "referencia_negativa_original": state.get("referencia_negativa_original"),
                "documentacao_suficiente": state.get("documentacao_suficiente"),
                "roteamento_nip": state.get("roteamento_nip"),
                "grupo_humano": state.get("grupo_humano"),
                "data_recebimento_nip_iso": state.get("data_recebimento_nip_iso"),
                "prazo_resposta_iso": state.get("prazo_resposta_iso"),
                "prazo_resposta_absoluto_iso": state.get("prazo_resposta_absoluto_iso"),
                "sla_alerta_absoluto_iso": state.get("sla_alerta_absoluto_iso"),
                "fonte_regulatoria": state.get("fonte_regulatoria"),
                "dmn_refs": state.get("dmn_refs", {}),
                "lacunas_enriquecimento": state.get("gather_notes", []),
            }
        prompt = (
            f"{dossier_prompt()}\n\nroute={route} motivo_humano={state.get('motivo_humano')}\nfatos={facts}"
        )
        try:
            narrativa = await self._llm.generate(prompt, phi=True)
        except Exception:  # noqa: BLE001 — LLM failure never blocks the human route.
            narrativa = ""
        return {
            "prompt_version": DOSSIER_PROMPT_VERSION,
            "route": route,
            "motivo_humano": state.get("motivo_humano"),
            "fatos": facts,
            "dmn_decision_refs": state.get("dmn_refs", {}),
            "narrativa": narrativa,
            "documentos_refs": state.get("documentos_refs") or [],
            # STRUCTURAL GUARDRAILS (L0 hard): the dossier NEVER carries a merit decision nor a
            # filing signature — both are the human's. A value here would be a detectable bug.
            "decisao_merito": None,  # manter/conceder NIP — always the human's
            "assinatura_envio": None,  # binding ANS transmission — always the human's
        }

    def _contract_variables(self, state: GustavoState) -> dict[str, Any]:
        """Build the flow's process input variables (contracts §Variaveis de entrada). The
        dossier travels as `dossie_gustavo` (instruction to the human) — NEVER a decision, a
        denial, or a signature. The human-only OUTPUT variables ship as explicit `None`
        guardrails (`decisao_envio`/`decisao_nip`) — the gated workers refuse anything not set
        by a human User Task (mirrors fernando's `decisao_inadimplencia=None`)."""
        common: dict[str, Any] = {
            "tenant_id": state.get("tenant_id", ""),
            "source_agent_id": "gustavo",
            "source_agent_version": self._agent_version,
            "dossie_gustavo": state.get("dossier") or {},
            "gustavo_route": state.get("route") or "",
            "motivo_encaminhamento": state.get("motivo_humano") or "",
        }
        dmn_refs = state.get("dmn_refs")
        if dmn_refs:
            common["dmn_decision_refs"] = dmn_refs

        if state.get("fluxo") == "ans_submit":
            common.update(
                {
                    "report_type": str(state.get("report_type", "")),
                    "competencia": str(state.get("competencia", "")),
                    "periodicidade": str(state.get("periodicidade", "")),
                    "origem_envio": str(state.get("origem_envio") or "calendario"),
                    "dataset_complete": bool(state.get("dataset_complete", False)),
                    "schema_valid": bool(state.get("schema_valid", False)),
                    "lgpd_anonimizado": bool(state.get("lgpd_anonimizado", False)),
                    "dataset_ref": str(state.get("dataset_ref", "")),
                    "due_date": str(state.get("due_date", "")),
                    # STRUCTURAL GUARDRAIL (L0 hard): only UT_RevisarEnvio's human fills this.
                    "decisao_envio": None,
                }
            )
            if state.get("nip_protocolo_origem"):
                common["nip_protocolo_origem"] = str(state["nip_protocolo_origem"])
            return common

        common.update(
            {
                "numero_nip_ans": str(state.get("numero_nip_ans", "")),
                "beneficiario_pseudo_id": str(state.get("beneficiario_pseudo_id", "")),
                "classificacao_nip": str(state.get("classificacao_nip", "")),
                "tema_nip": str(state.get("tema_nip", "")),
                "data_recebimento_nip_iso": str(state.get("data_recebimento_nip_iso", "")),
                "documentos_refs": state.get("documentos_refs") or [],
                "contesta_negativa": bool(state.get("contesta_negativa", False)),
                "documentacao_suficiente": bool(state.get("documentacao_suficiente", False)),
                "origem_a2a": bool(state.get("origem_a2a", False)),
                "prazo_resposta_iso": str(state.get("prazo_resposta_iso", "")),
                # STRUCTURAL GUARDRAIL (L0 hard): only the human User Tasks fill this —
                # MANTER_NEGATIVA is impossible to originate here.
                "decisao_nip": None,
            }
        )
        for opt in ("protocolo_ans", "referencia_negativa_original"):
            if state.get(opt):
                common[opt] = str(state[opt])
        return common

    # -- Graph assembly -----------------------------------------------------------------------

    def compile_graph(self) -> StateGraph[GustavoState]:
        g: StateGraph[GustavoState] = StateGraph(GustavoState)
        g.add_node("receive", self.receive)
        g.add_node("gather", self.gather)
        g.add_node("assess", self.assess)
        g.add_node("review_submission", self.review_submission)
        g.add_node("instruct_nip", self.instruct_nip)
        g.add_node("start_process", self.start_process)
        g.add_node("finalize", self.finalize)

        g.add_edge(START, "receive")
        g.add_edge("receive", "gather")
        g.add_edge("gather", "assess")
        g.add_conditional_edges(
            "assess",
            self._route,
            {"review_submission": "review_submission", "instruct_nip": "instruct_nip"},
        )
        g.add_edge("review_submission", "start_process")
        g.add_edge("instruct_nip", "start_process")
        g.add_edge("start_process", "finalize")
        g.add_edge("finalize", END)
        return g


def build(config: dict[str, Any] | None = None) -> StateGraph[GustavoState]:
    """Contract `_template/graph.py:build`, resolved by `AgentLoader`/`runtime.harness.Harness`.

    `config` MUST contain `inference` (ADR-0009), `dmn` (ADR-0028/T1.5), `cibseven`
    (ADR-0001/T1.11). `fhir` is OPTIONAL (module docstring's labeled boundary) — its absence
    never fails the build, only degrades J2's `gather` to a disclosed gap note.

    Fail-closed: missing a REQUIRED dependency raises `ValueError` at build time.
    """
    cfg = config or {}
    inference = cfg.get("inference")
    dmn = cfg.get("dmn")
    cibseven = cfg.get("cibseven")
    missing = [
        name
        for name, value in (("inference", inference), ("dmn", dmn), ("cibseven", cibseven))
        if value is None
    ]
    if missing:
        raise ValueError(
            f"Gustavo build(config) is missing required dependencies: {missing} "
            "(ADR-0001/0009/0028 — inference/dmn/cibseven must all be injected)"
        )
    agent_version = str(cfg.get("agent_version", "gustavo@v0"))
    return GustavoGraph(
        inference=cast(InferenceProvider, inference),
        dmn=cast(DmnTransport, dmn),
        cibseven=cast(CibSevenTransport, cibseven),
        fhir=cast("FhirReader | None", cfg.get("fhir")),
        agent_version=agent_version,
    ).compile_graph()


# Prompt versions exposed for audit/eval gating (ADR-0007/0009).
PROMPT_VERSIONS: dict[str, str] = {
    "system": SYSTEM_PROMPT_VERSION,
    "dossier": DOSSIER_PROMPT_VERSION,
}

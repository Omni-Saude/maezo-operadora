"""Marina Andrade — Analista de Contas Medicas e Recurso de Glosa Agent (Phase 2, T1.12).

Journey (mirrors the v1 donor's structure, READ-ONLY reference `Maezo-Healthcare-Plan
src/maezo/agents/marina/graph.py`, adapted to v2's flatter seam set — same rationale as
`agents/rafael/graph.py`'s module docstring, of which Marina is explicitly the Phase 2 analog):

    receive -> gather -> assess(DMN) -> {auto_route | human_review} -> start_process -> finalize

Marina serves THREE flows, distinguished by `flow` in state (`contas` | `recurso` | `reembolso`):

- `contas`    : convoked by `operadora.contas.prepare_triage_dossier` (A2A `glosa.analyze`,
                SP-OP-CONTAS-001). `assess` evaluates the DMN chain
                `glosa_reason_normalization` -> `glosa_classification` -> `glosa_triage` (+
                `contas_sla`, purely informative — never gates routing, mirrors Rafael's
                `auth_sla`). Triage produces `PAGAR` | `ANALISE_HUMANA` — this is ROUTING, never a
                merit decision: the payer's decision to glosa is born solely in
                `UT_AnalistaContas`, and no DMN in this chain has an output that gloses.
                Marina STARTS SP-OP-CONTAS-001 idempotently (business key
                `CONTAS-{tenant}-{numero_lote_tiss}` or, when adjudicating by guia/conta,
                `CONTAS-{tenant}-{numero_guia_tiss}-{numero_conta}`).
- `recurso`   : convoked by `operadora.recurso.analyze_request` (A2A `recurso.analyze`,
                SP-OP-RECURSO-001). `assess` evaluates `recurso_admissibility` ->
                `recurso_eligibility` (+ `recurso_sla`, purely informative). Admissibility/
                eligibility produce `SEGUE_ANALISE` | `PENDENTE_DOCUMENTACAO` | `ANALISE_HUMANA`
                / `SEGUE_MERITO` | `ANALISE_HUMANA` — no INDEFERIR output exists by design.
                Marina STARTS SP-OP-RECURSO-001 idempotently (business key
                `RECURSO-{tenant}-{numero_guia_tiss}-{glosa_id}`).
- `reembolso` : convoked by `operadora.reembolso.analyze_request` (A2A `reembolso.analyze`,
                SP-OP-REEMBOLSO-001) from INSIDE an ALREADY-RUNNING instance (`ST_PrepararDossie`,
                after the BPMN's own `BRT_Admissibilidade`/`BRT_Calculo`/`BRT_AutoApproval`
                business-rule tasks — `reembolso_admissibility`/`reembolso_calculo`/
                `reembolso_auto_approval` — have already run). STRUCTURALLY different from
                contas/recurso: `assess` evaluates NO DMN at all here (only REPORTS the
                pre-resolved facts) and `start_process` is a NO-OP (Marina never starts a second
                instance) — she only assembles the factual dossier and ALWAYS routes to the human
                `analise-reembolso` group (no `auto_route` variant exists for this flow — mirrors
                `andre.adequacao_dossier`'s single-outcome shape referenced by the donor).

`assess` ALWAYS consults the deterministic DMN for `contas`/`recurso` (ADR-0012); the LLM
REASONS over the DMN result to assemble the dossier — it NEVER substitutes or re-decides it.

L0 HARD STRUCTURAL GUARDRAIL (contracts SP-OP-CONTAS-001/RECURSO-001/REEMBOLSO-001 invariant;
ADR-0005/0008, CI-enforced): Marina NEVER applies nor waives a glosa, NEVER grants nor denies a
recurso (defere/indefere), NEVER approves/denies/reduces a reembolso, NEVER decides clinically,
NEVER accuses fraud. `Route` has NO adverse variant — only `auto_route` (neutral routing determined by
the DMN, `contas`/`recurso` only) and `human_review` (fail-safe/instructive, the ONLY path in
`reembolso`). No automatic outcome ever gloses a conta, indefere a recurso, or approves/denies/
reduces a reembolso: the APPLICATION of a glosa is born SOLELY in `UT_AnalistaContas`
(`operadora.contas.registrar_glosa`, guarded `ERR_CONTAS_GLOSA_NOT_HUMAN`); the
INDEFERIMENTO of a recurso SOLELY in `UT_AnaliseRecursoAnalista`/`UT_RevisaoAuditorMedico`
(`operadora.recurso.registrar_indeferimento`, guarded
`ERR_RECURSO_INDEFERIMENTO_NOT_HUMAN`); reembolso denial/reduction SOLELY in `UT_AnaliseReembolso`/
`UT_RevisaoAuditorMedico`/`UT_CoordenacaoReembolso` (`operadora.reembolso.send_reembolso_denial`,
guarded `ERR_REEMBOLSO_DENIAL_NOT_HUMAN`) — all three worker guards untouched by this build
(T1.5 already cut these workers over to engine-side DMN; this graph CONSUMES their outputs,
never edits `tools/workers/{contas,recurso,reembolso}.py`). Ambiguity, glosa tecnica/clinica, an
indicio de fraude signal, DMN unavailability, or an apparently-expired deadline ALWAYS fail-safe
to human review — never a silent auto_route (structurally: every `assess` branch that cannot
prove a neutral, allow-listed DMN output returns `human_review`, mirroring Rafael's own
"nothing ambiguous falls through to automatic by omission" invariant).

`dentro_teto_l2` (and every other reembolso ceiling fact) arrives PRE-RESOLVED by
`tools/workers/reembolso.py::calculate_value` (via `CeilingResolver`, T1.9) — this graph
CONSUMES it, NEVER computes it (`tests/unit/sec/test_dentro_teto_source.py` scans
`tools/workers/{reembolso,auth,pagto}.py` only; this module is deliberately out of that scan's
scope because it never originates the fact in the first place — `_reembolso_facts` below is a
pure pass-through of `state.get("dentro_teto_l2")`).

PHI discipline: Marina's `security_zone` is `phi` end-to-end (`spec/agents/marina/agent.yaml`) —
the one LLM call per turn (`_build_dossier`'s narrative) passes `phi=True` (ADR-0006/ADR-0017/T1.7).

CALLER-PLANTED-OUTPUT SANITIZATION (R1 cycle-1 fix — same defect class as fernando's/carolina's
graphs, worst expression here because real instances start): `receive` resets EVERY output-only
state field (`_output_field_resets`) before any other node reads them. Pre-fix, `gather`/`assess`
early-bailed on a truthy `state["error"]` WITHOUT recomputing `route`, and `receive` neither
cleared nor distrusted a caller-supplied `error` — so a planted `error` + `route="auto_route"` +
forged `triagem`/`categoria_normalizada`/`dmn_refs` skipped the DMN chain entirely (0 DMN calls)
and started a REAL SP-OP-CONTAS-001/SP-OP-RECURSO-001 instance with `marina_route="auto_route"`
and the forged facts verbatim in the engine-bound `dossie_marina` (live-proven by the R1
verifier); on legitimate human shortcuts (dmn-down/fraud/pendente), planted fact fields survived
into the dossier `fatos` beside the genuine `motivo_humano` (forged contradictory provenance for
the human auditor). Post-fix, BOTH halves of the class are closed: (1) clearing inbound `error`
(and `route`) at `receive` makes `assess` ALWAYS recompute the route from the real DMN chain,
overwriting any plant; (2) resetting every fact/dossier/process output field keeps planted values
out of the dossier `fatos` and engine-bound variables even on the legitimate human-shortcut
paths. Defense in depth: the `gather`/`assess` error bails — now reachable ONLY via `receive`'s
own missing-context guard — explicitly re-assert `route="human_review"` instead of returning
`{}`, so the bail is fail-safe by construction even if sanitization were ever regressed.

INPUT-BOUNDARY GATE (T1.11 layer 1 — CC-14/LUC-09a/RAF-13; the sanitization above is layer 2).
The reset above is a HAND-ENUMERATED list: it neutralizes the output fields somebody remembered
to list, and a state field added later is silently un-reset AND silently caller-settable. Layer 1
closes that: `_CALLER_INPUT_FIELDS` declares the complete INPUT half of `MarinaState`, an
import-time guard REFUSES TO LOAD THE MODULE if any state key is unclassified (or double
classified), and `new_marina_state` (strict, raises `ValueError` naming the keys) /
`gate_inbound_state` (lenient, drops + logs key NAMES only) are the two construction seams
through which an output-only key cannot enter the state dict at all. Marina has no live inbound
seam in this build (see LABELED BOUNDARIES) — the gate is built now because it is the ordered
prerequisite for that seam, not after it.

DIVERGENCE FROM DONOR (disclosed, spec wins per this task's charter): the v1 donor lets a
`glosa_classification` DMN failure pass through silently (glosa_type stays `""`, `assess`
proceeds straight to `contas_sla`/`glosa_triage`) — safe-by-defense-in-depth only because a later
`recurso_eligibility` call would still catch an empty `glosa_type` on its own catch-all row. This
build instead routes `glosa_classification` failure straight to `human_review` (`motivo_humano
= "dmn_indisponivel"`), uniformly with every other DMN in the assess chain (normalization,
triage, admissibility, eligibility) — this reads spec's `agent.yaml` `escalation.triggers: -
signal: dmn_unavailable` literally (a generic, unqualified trigger, not scoped to only the
routing-critical tables) and is simpler to prove/test than relying on a downstream table's
catch-all as the real safety net. `contas_sla`/`recurso_sla` remain PURELY INFORMATIVE and
non-gating on failure — mirrors Rafael's `auth_sla` exactly (feeds the dossier only, never
affects `route`).

LABELED BOUNDARIES (this build, disclosed — never fabricated, same rationale as
`agents/rafael/graph.py`'s/`agents/helena/graph.py`'s module docstrings):
- `gather` uses a thin `PatientSummaryReader` Protocol over v2's generic `FhirServer`
  (`tools/mcp_fhir/server.py: read_resource`) — NOT the donor's dedicated
  `mcp-fhir.read_patient_summary` tool (no PEP/ToolRegistry gateway wiring for agent tool calls
  yet, T2.4 gap). `gather` is best-effort and NEVER blocks routing on a FHIR failure (mirrors
  Rafael exactly) — a missing/unreachable FHIR endpoint degrades to a dossier gap note, never a
  fabricated fact. When no `fhir` dependency is injected at all, `gather` records an explicit gap
  note rather than silently producing empty facts that look like "no findings".
- No episodic memory write (`mcp-memory.read_write`, ADR-0002) — same rationale as Helena's/
  Rafael's graphs: v2's `MemoryServer` requires a live Postgres/pgvector schema not yet wired
  into any agent graph in this repo; adding it is a follow-up once that schema exists.
- No cross-agent A2A delegation (`operadora.contas/recurso/reembolso.*` -> Marina) is wired in
  this build: v2's `a2a/` package has no `DelegationEnvelope`/`DelegationDispatcher` yet (only
  `AgentCard`/`A2ARegistry`/`AntiLoopGuard` exist) — same gap Rafael's/Helena's graphs already
  disclose. Marina's graph is invoked directly with an already-assembled case state, as the unit
  tests do, rather than via a live delegation envelope. The T1.11 input-boundary gate
  (`new_marina_state`/`gate_inbound_state`) is nonetheless present and tested, exactly as
  Rafael's is, so the seam is gated on the day it lands (CC-02) rather than after.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal, Protocol, TypedDict, cast

import structlog
from langgraph.graph import END, START, StateGraph

from maezo.runtime.inference import InferenceProvider
from maezo.runtime.start_outcome import (
    notify_start_failure as emit_start_failure_notice,
)
from maezo.runtime.start_outcome import (
    route_after_start,
    start_failed_state,
)
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
    DOSSIER_PROMPT_VERSION,
    RECURSO_PROMPT_VERSION,
    REEMBOLSO_PROMPT_VERSION,
    SYSTEM_PROMPT_VERSION,
    dossier_prompt,
    recurso_prompt,
    reembolso_prompt,
)

logger = structlog.get_logger(__name__)

# --- Domain enums (mirror the CONTAS/RECURSO/REEMBOLSO contracts + DMN schema) ----------------

Flow = Literal["contas", "recurso", "reembolso"]

# Graph routing. STRUCTURALLY WITHOUT AN ADVERSE VARIANT — no value here gloses a conta, denies
# a recurso, or approves/denies/reduces a reembolso.
Route = Literal["auto_route", "human_review"]

# `glosa_triage` output domain (CONTAS) — no output that gloses, by design (ADR-0040).
TriagemGlosa = Literal["PAGAR", "ANALISE_HUMANA"]
# `recurso_admissibility` output domain (RECURSO) — no INDEFERIR/INADMISSIVEL output by design.
AdmissibilidadeRecurso = Literal["SEGUE_ANALISE", "PENDENTE_DOCUMENTACAO", "ANALISE_HUMANA"]
# `recurso_eligibility` output domain (RECURSO) — no INDEFERIR output by design.
ElegibilidadeRecurso = Literal["SEGUE_MERITO", "ANALISE_HUMANA"]

# Why the case went to human review — attached to the dossier/contract variables. NONE of these
# is an accept/deny — they are all reasons FOR human review.
MotivoHumano = Literal[
    "triagem_analise_humana",  # glosa_triage = ANALISE_HUMANA (tecnica/clinica/valor/ambiguidade)
    "documentacao_pendente",  # recurso_admissibility = PENDENTE_DOCUMENTACAO
    "recurso_analise_humana",  # recurso_admissibility/eligibility = ANALISE_HUMANA (or medico-auditor)
    "indicio_fraude",  # fraud signal — informative only, human decides the referral
    "dmn_indisponivel",  # a DMN in the assess chain failed -> never an adverse outcome by omission
    "reembolso_dossie",  # reembolso: SP-OP-REEMBOLSO-001 already resolved its own DMN -> analise-reembolso
    "outro",
]

PROCESS_KEY_CONTAS = "SP-OP-CONTAS-001"
PROCESS_KEY_RECURSO = "SP-OP-RECURSO-001"

DMN_GLOSA_NORMALIZATION = "glosa_reason_normalization"
DMN_GLOSA_CLASSIFICATION = "glosa_classification"
DMN_GLOSA_TRIAGE = "glosa_triage"
DMN_CONTAS_SLA = "contas_sla"
DMN_RECURSO_ADMISSIBILITY = "recurso_admissibility"
DMN_RECURSO_ELIGIBILITY = "recurso_eligibility"
DMN_RECURSO_SLA = "recurso_sla"


class PatientSummaryReader(Protocol):
    """Best-effort FHIR summary read seam (`gather`). See module docstring's labeled boundary."""

    async def read_patient_summary(self, patient_id: str) -> dict[str, Any]: ...


# --- Graph state (working memory; ADR-0002) ----------------------------------------------------


class MarinaState(TypedDict, total=False):
    """Case state. Every field here is pseudonymized (Zona PHI, ADR-0006) —
    `beneficiario_pseudo_id` NEVER carries a raw CPF/name/CNS. Boolean facts
    (`divergencia_valor`/`item_conforme_tabela`/`dentro_prazo_recurso`/reembolso ceiling facts/
    ...) arrive PRE-RESOLVED by a deterministic upstream worker — Marina CONSUMES them, never
    computes them (module docstring's L0-hard invariant)."""

    # Flow (decides process, DMN chain, and human group).
    flow: Flow

    # Runtime identifiers / task origin.
    tenant_id: str
    canal: str  # a2a | portal_tiss
    beneficiario_pseudo_id: str
    prestador_id: str

    # --- CONTAS inputs (SP-OP-CONTAS-001) ---
    numero_lote_tiss: str
    numero_guia_tiss: str
    numero_conta: str
    competencia: str
    data_recebimento_lote: str
    valor_apresentado_brl: float
    tipo_lote: str  # consulta | sadt | internacao | honorario | opme | misto
    linhas_conta_refs: list[dict[str, Any]]
    reason_codes_tiss: list[str]
    # Pre-resolved by worker (deterministic input to the CONTAS DMN chain).
    divergencia_valor: bool
    item_conforme_tabela: bool
    documentacao_anexa: bool
    denial_ratio: float  # worker arithmetic (operadora.contas.calculate_impact)
    indicio_fraude_sinalizado: bool  # informative signal only — never decides, only routes human

    # --- RECURSO inputs (SP-OP-RECURSO-001) ---
    glosa_id: str
    glosa_type: str  # administrativa | tecnica | clinica | linha_duplicada | formatacao
    glosa_reason_code: str
    valor_glosado_brl: float
    codigo_procedimento_tuss: str
    cid10: str
    documentos_recurso_refs: list[dict[str, Any]]
    data_ciencia_alegada_prestador: str
    data_recebimento_recurso_iso: str
    # Pre-resolved by worker (deterministic input to the RECURSO DMN chain).
    glosa_existe: bool
    dentro_prazo_recurso: bool
    documentacao_recurso_completa: bool

    # --- REEMBOLSO inputs (SP-OP-REEMBOLSO-001) ---
    protocolo_reembolso: str  # business key component (REEMB-{tenant}-{protocolo})
    tipo_reembolso: str  # urgencia_emergencia | livre_escolha | fora_de_rede | indisponibilidade_rede
    # consulta | exame_simples | exame_especial | terapia | internacao | opme | alta_complexidade
    categoria_procedimento: str
    valor_solicitado_cents: int  # integer-centavos — NEVER float/BRL (contract SP-OP-REEMBOLSO-001)
    # Pre-resolved/pre-calculated by SP-OP-REEMBOLSO-001's OWN BusinessRuleTasks
    # (BRT_Admissibilidade/BRT_Calculo/BRT_AutoApproval) BEFORE this hop — Marina REPORTS them,
    # never recomputes them.
    cobertura_prevista: bool
    documentacao_completa: bool
    dentro_prazo: bool
    beneficiario_ativo: bool
    carencia_cumprida: bool
    dentro_tabela: bool
    dentro_teto_l2: bool
    requer_avaliacao_clinica: bool
    valor_calculado_tabela_cents: int  # output of BRT_Calculo (reembolso_calculo)

    # FHIR reference for the dossier (patient summary). Never raw PHI.
    patient_summary_ref: str

    # Filled by `gather` (pseudonymized FHIR facts).
    gathered: bool
    summary_facts: dict[str, Any]
    gather_notes: list[str]  # FHIR enrichment gaps (attached to the dossier)

    # Filled by `assess` (DMN results + refs).
    categoria_normalizada: str  # glosa_reason_normalization (CONTAS)
    glosa_classificada: str  # glosa_classification.glosa_type (CONTAS)
    triagem: TriagemGlosa  # glosa_triage (CONTAS)
    admissibilidade_recurso: AdmissibilidadeRecurso  # recurso_admissibility (RECURSO)
    elegibilidade_recurso: ElegibilidadeRecurso  # recurso_eligibility (RECURSO)
    grupo_revisor: str  # recurso_eligibility.grupo_revisor (RECURSO)
    sla_analise: str  # ISO 8601 (flow's own SLA DMN output)
    sla_alerta: str
    dmn_refs: dict[str, str]  # table -> rule reference (e.g. glosa_triage#<decision-def-id>)
    dmn_error: str  # set when a DMN in the assess chain failed/was unavailable
    dossier: dict[str, Any]  # the LLM-assembled dossier (instruction, never a decision)

    # Routing (NEVER an accept/deny — only neutral or human).
    route: Route
    motivo_humano: MotivoHumano
    grupo_humano: str  # candidate group of the human destination

    # Filled by `start_process` (CONTAS/RECURSO only; no-op in `reembolso`).
    process_started: bool
    #: CC-01: o start foi TENTADO e FALHOU tecnicamente (`except CibSevenError` de
    #: `start_process`). NAO e a mesma coisa que `process_started is False`, que tambem cobre
    #: no-ops legitimos; e este marcador — e so ele — que a aresta condicional le.
    start_failed: bool
    business_key: str
    process_ref: dict[str, Any]

    # Output.
    desfecho: str  # pagar_integral | triagem_humana |
    #                 recurso_segue_analise | recurso_humano | reembolso_dossie_humano
    error: str  # technical failure (routes human, for safety)


# --- Helpers -------------------------------------------------------------------------------


def _flow(state: MarinaState) -> Flow:
    """Case flow (default `contas` — glosa triage)."""
    return state.get("flow", "contas")


def _business_key(state: MarinaState) -> str:
    """Idempotent business key per the flow's contract.

    CONTAS:    `CONTAS-{tenant}-{numero_lote_tiss}` (or, when adjudicating by guia/conta:
               `CONTAS-{tenant}-{numero_guia_tiss}-{numero_conta}`). Marina STARTS this instance.
    RECURSO:   `RECURSO-{tenant}-{numero_guia_tiss}-{glosa_id}`. Marina STARTS this instance.
    REEMBOLSO: `REEMB-{tenant}-{protocolo_reembolso}` — the SAME format SP-OP-REEMBOLSO-001 itself
               uses (contract §Correlacao). Marina does NOT start this instance (already running
               when `reembolso.analyze` arrives); the key only ANCHORS the factual dossier to the
               existing instance (`start_process` is a no-op in this flow).
    """
    tenant = state.get("tenant_id", "")
    flow = _flow(state)
    if flow == "recurso":
        return f"RECURSO-{tenant}-{state.get('numero_guia_tiss', '')}-{state.get('glosa_id', '')}"
    if flow == "reembolso":
        return f"REEMB-{tenant}-{state.get('protocolo_reembolso', '')}"
    guia = state.get("numero_guia_tiss", "")
    conta = state.get("numero_conta", "")
    if guia and conta:
        return f"CONTAS-{tenant}-{guia}-{conta}"
    return f"CONTAS-{tenant}-{state.get('numero_lote_tiss', '')}"


def _process_key(state: MarinaState) -> str:
    """Only called for `contas`/`recurso` — `reembolso`'s `start_process` no-ops before this."""
    return PROCESS_KEY_RECURSO if _flow(state) == "recurso" else PROCESS_KEY_CONTAS


# --- Input boundary (T1.11 layer 1: partition + constructor) -----------------------------------
#
# `MarinaState` carries TWO disjoint classes of key (same split helena/rafael/beatriz/valentina
# already enforce; added here by CC-14 — marina previously had ONLY the layer-2 reset below):
#   * INPUT-ONLY  (`_CALLER_INPUT_FIELDS`): the ONLY keys a caller/upstream (a future A2A
#     `glosa.analyze`/`recurso.analyze`/`reembolso.analyze` delegation seam, the portal-TISS
#     worker) may set — the flow selector, the runtime identifiers, each flow's contract inputs,
#     and the PRE-RESOLVED worker/BRT facts this graph legitimately CONSUMES (never computes;
#     module docstring's L0-hard invariant).
#   * OUTPUT-ONLY (`_output_field_resets()`): keys OWNED by this graph's nodes (route, the DMN
#     verdicts, sla_*, dmn_refs, dossier, process_*, desfecho, error). A caller must NEVER set
#     one — a planted output field is an injection, and on this graph it is the one that starts
#     REAL SP-OP-CONTAS-001/SP-OP-RECURSO-001 instances (module docstring
#     §CALLER-PLANTED-OUTPUT SANITIZATION).
#
# PER-FLOW SHAPE (mirrors `agents/valentina/graph.py` and `agents/andre/graph.py`, which also
# carry a flow/task selector): the set is the UNION over the three flows, grouped by flow below,
# not a per-flow dict. Rationale — the flow selector `flow` is itself caller-supplied, so a
# per-flow narrowing would be gated by the very value the caller controls, buying no security
# while manufacturing a second source of truth to drift. What the union DOES guarantee is the
# property that matters: no OUTPUT-only key can enter the state dict under any flow. A
# `recurso` turn carrying a stray CONTAS input is a benign upstream mis-fill and is already
# ignored by `_business_key`/`_contract_variables`, which read only the active flow's fields.
#
# TWO defenses, both fail-closed:
#   1. Per-graph entry sanitization (layer 2, pre-existing) — `receive` resets EVERY output-only
#      field to its neutral default before any downstream node runs.
#   2. Input-boundary gate (layer 1, THIS block) — a construction seam assembles state ONLY
#      through the typed `new_marina_state` constructor or the `gate_inbound_state` allowlist
#      filter, so an output-only key can never enter the state dict at all. Marina has NO live
#      A2A/delegation seam in this build (module docstring's labeled boundary: the graph is
#      invoked directly with an already-assembled case state, as the unit tests do); the gate is
#      provided HERE so it is enforced the moment that seam lands (CC-02).
#
# The completeness guard below fails at IMPORT TIME if a newly added `MarinaState` field is not
# classified into exactly one of the two sets — "any missed key is a hole". That is what the
# hand-enumerated `_output_field_resets` alone could not give: a forgotten new field used to be
# silently un-reset AND silently caller-settable.
_CALLER_INPUT_FIELDS: frozenset[str] = frozenset(
    {
        # Flow selector (decides process, DMN chain, human group).
        "flow",
        # Runtime identifiers / task origin.
        "tenant_id",
        "canal",
        "beneficiario_pseudo_id",
        "prestador_id",
        # --- CONTAS inputs (SP-OP-CONTAS-001) + its pre-resolved worker facts ---
        "numero_lote_tiss",
        "numero_guia_tiss",
        "numero_conta",
        "competencia",
        "data_recebimento_lote",
        "valor_apresentado_brl",
        "tipo_lote",
        "linhas_conta_refs",
        "reason_codes_tiss",
        "divergencia_valor",
        "item_conforme_tabela",
        "documentacao_anexa",
        "denial_ratio",
        "indicio_fraude_sinalizado",
        # --- RECURSO inputs (SP-OP-RECURSO-001) + its pre-resolved worker facts ---
        "glosa_id",
        "glosa_type",
        "glosa_reason_code",
        "valor_glosado_brl",
        "codigo_procedimento_tuss",
        "cid10",
        "documentos_recurso_refs",
        "data_ciencia_alegada_prestador",
        "data_recebimento_recurso_iso",
        "glosa_existe",
        "dentro_prazo_recurso",
        "documentacao_recurso_completa",
        # --- REEMBOLSO inputs (SP-OP-REEMBOLSO-001) ---
        # The booleans/`valor_calculado_tabela_cents` are outputs of the PROCESS's OWN
        # BusinessRuleTasks (BRT_Admissibilidade/BRT_Calculo/BRT_AutoApproval) computed BEFORE
        # this hop — inputs FROM MARINA'S POINT OF VIEW, which she REPORTS and never recomputes.
        "protocolo_reembolso",
        "tipo_reembolso",
        "categoria_procedimento",
        "valor_solicitado_cents",
        "cobertura_prevista",
        "documentacao_completa",
        "dentro_prazo",
        "beneficiario_ativo",
        "carencia_cumprida",
        "dentro_tabela",
        "dentro_teto_l2",
        "requer_avaliacao_clinica",
        "valor_calculado_tabela_cents",
        # FHIR reference consumed by `gather` (a pointer, never raw PHI).
        "patient_summary_ref",
    }
)


def _output_field_resets() -> dict[str, Any]:
    """Benign reset values for EVERY output-only `MarinaState` field — applied unconditionally at
    `receive` entry (R1 cycle-1 fix; module docstring §CALLER-PLANTED-OUTPUT SANITIZATION).

    An inbound turn's state may only carry INPUT fields (identifiers + pre-resolved worker
    facts). Every field a NODE of this graph is supposed to fill is reset here first, so a
    caller-planted `error`/`route`/forged DMN fact/forged `dmn_refs`/pre-cooked `dossier` can
    never survive into routing, the dossier `fatos`, or engine-bound process variables. Returns
    a FRESH dict per call — the mutable container values (`{}`/`[]`) must never be shared across
    turns.

    `route` resets to `"human_review"` (fail-safe: if any node were ever skipped, the
    conditional edge still lands on the human path); `assess` ALWAYS overwrites it from the real
    DMN chain. `business_key` resets to `""` and is re-derived from the input identifiers on the
    happy path — on `receive`'s own missing-context guard it stays empty, which keeps
    `start_process`'s error short-circuit closed against a planted key.
    """
    return {
        "error": "",
        "route": "human_review",
        "motivo_humano": None,
        "grupo_humano": None,
        "business_key": "",
        "gathered": False,
        "summary_facts": {},
        "gather_notes": [],
        "categoria_normalizada": None,
        "glosa_classificada": None,
        "triagem": None,
        "admissibilidade_recurso": None,
        "elegibilidade_recurso": None,
        "grupo_revisor": None,
        "sla_analise": "",
        "sla_alerta": "",
        "dmn_refs": {},
        "dmn_error": "",
        "dossier": {},
        "desfecho": "",
        "process_started": False,
        "start_failed": False,
        "process_ref": {},
    }


_MARINA_ALL_FIELDS: frozenset[str] = _CALLER_INPUT_FIELDS | frozenset(_output_field_resets())
if frozenset(MarinaState.__annotations__) != _MARINA_ALL_FIELDS:
    _unclassified = frozenset(MarinaState.__annotations__) - _MARINA_ALL_FIELDS
    _stale = _MARINA_ALL_FIELDS - frozenset(MarinaState.__annotations__)
    raise RuntimeError(
        "MarinaState input/output field split is incomplete (T1.11 input-boundary gate, CC-14): "
        f"unclassified fields={sorted(_unclassified)} stale entries={sorted(_stale)} — every "
        "MarinaState key MUST be either a `_CALLER_INPUT_FIELDS` member or carry a neutral "
        "default in `_output_field_resets()`."
    )
if _CALLER_INPUT_FIELDS & frozenset(_output_field_resets()):
    raise RuntimeError(
        "MarinaState field classified as BOTH input and output (T1.11 input-boundary gate, "
        f"CC-14): {sorted(_CALLER_INPUT_FIELDS & frozenset(_output_field_resets()))}"
    )


def new_marina_state(raw: Mapping[str, Any]) -> MarinaState:
    """Typed input-boundary constructor for a fresh Marina case (T1.11 layer 1).

    Accepts a raw mapping — the shape a future A2A `glosa.analyze`/`recurso.analyze`/
    `reembolso.analyze` delegation envelope or the portal-TISS worker would hand over — and
    returns a `MarinaState` containing ONLY `_CALLER_INPUT_FIELDS` keys.

    An unknown key is a HARD ERROR, and the error NAMES the offending keys. Unlike a lenient
    public ingestion edge, a delegation seam is an INTERNAL contract: a stray key means a
    producer bug (or an injection attempt at the one graph in this fleet that starts real
    SP-OP-CONTAS-001/SP-OP-RECURSO-001 instances), and it must fail closed, LOUDLY, rather than
    be silently tolerated. Mirrors `agents/rafael/graph.py::new_rafael_state` exactly.

    NOTE the two error classes are deliberately NOT distinguished in the message beyond the key
    list: an output-only key and a wholly-unknown key are both "not a legitimate caller input",
    and telling a hostile caller which of its keys the state model recognizes would be a hint it
    does not need.
    """
    unknown = sorted(k for k in raw if k not in _CALLER_INPUT_FIELDS)
    if unknown:
        raise ValueError(
            "new_marina_state received non-input keys (T1.11 input-boundary gate): "
            f"{unknown} — only `_CALLER_INPUT_FIELDS` may be set by a caller/delegation seam; "
            "output-only fields are owned by Marina's graph nodes."
        )
    return cast(MarinaState, {k: raw[k] for k in _CALLER_INPUT_FIELDS if k in raw})


def gate_inbound_state(raw: Mapping[str, Any]) -> MarinaState:
    """Fail-closed input allowlist (drop-and-log variant of `new_marina_state`).

    Only `_CALLER_INPUT_FIELDS` keys survive; every other key — any caller-planted output field,
    any unknown key — is DROPPED and LOGGED. Use where tolerating benign upstream drift is
    preferable to raising (a lenient ingestion edge); use `new_marina_state` on a strict internal
    delegation seam.

    LOG HYGIENE: the event carries the dropped KEY NAMES only, never their values — a planted
    value is unbounded caller-controlled content and could carry PHI (Zona PHI, ADR-0006), the
    same rule this graph applies to failure reasons in engine-bound variables.
    """
    dropped = sorted(k for k in raw if k not in _CALLER_INPUT_FIELDS)
    if dropped:
        logger.warning("marina_inbound_output_fields_dropped", dropped=dropped)
    return cast(MarinaState, {k: raw[k] for k in _CALLER_INPUT_FIELDS if k in raw})


class MarinaGraph:
    """Wires Marina's injected dependencies into a compilable `StateGraph[MarinaState]`."""

    def __init__(
        self,
        *,
        inference: InferenceProvider,
        dmn: DmnTransport,
        cibseven: CibSevenTransport,
        audit_sink: AuditStartSink,
        fhir: PatientSummaryReader | None = None,
        agent_version: str = "marina@v0",
    ) -> None:
        self._llm = inference
        self._dmn = dmn
        self._cibseven = cibseven
        # T-C2 fence: required durable ADR-0007 sink for the contas/recurso process start.
        self._audit_sink = audit_sink
        self._model_id = getattr(inference, "model_id", None)
        self._fhir = fhir
        self._agent_version = agent_version

    # -- Nodes ----------------------------------------------------------------------------

    async def receive(self, state: MarinaState) -> dict[str, Any]:
        """Turn start (part of state 1): task arrives (A2A `glosa.analyze`/`recurso.analyze`/
        `reembolso.analyze`). Idempotent.

        SANITIZATION FIRST (R1 cycle-1 fix; module docstring §CALLER-PLANTED-OUTPUT
        SANITIZATION): EVERY output-only field is reset (`_output_field_resets`) before anything
        else — an inbound `error`/`route`/forged DMN fact is a caller plant, never trusted.
        Pre-fix, a planted `error` made `gather`/`assess` early-bail without recomputing
        `route`, letting a planted `route="auto_route"` + forged `triagem`/`dmn_refs` skip the
        DMN chain entirely and start a REAL process carrying the forged facts (live-proven by
        the R1 verifier).

        Defense: never proceeds without the minimum contract identifiers (tenant + the flow's
        own business key). Without them there is no idempotent/anchorable business key — routes
        to human by safety (fail-safe, never an adverse effect).
        """
        sanitized = _output_field_resets()
        flow = _flow(state)
        tenant_ok = bool(state.get("tenant_id"))
        if flow == "recurso":
            key_ok = bool(state.get("numero_guia_tiss") and state.get("glosa_id"))
        elif flow == "reembolso":
            key_ok = bool(state.get("protocolo_reembolso"))
        else:
            key_ok = bool(state.get("numero_lote_tiss") or state.get("numero_guia_tiss"))
        if not tenant_ok or not key_ok:
            return {
                **sanitized,
                "route": "human_review",
                "motivo_humano": "outro",
                "grupo_humano": self._default_human_group(flow),
                "error": "contexto de runtime ausente (tenant/chave de negocio)",
            }
        return {**sanitized, "business_key": _business_key(state)}

    async def gather(self, state: MarinaState) -> dict[str, Any]:
        """Best-effort FHIR enrichment — NEVER blocks routing (module docstring)."""
        if state.get("error"):
            # Post-`receive`-sanitization a truthy `error` can ONLY have been set by `receive`'s
            # own missing-context guard (which already routed human) — never by the caller.
            # Re-assert the fail-safe route anyway (R1 cycle-1 fix, defense in depth): this bail
            # must NEVER be reachable with a non-human route.
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
                notes.append(f"resumo FHIR indisponivel: {exc}")

        return {"gathered": True, "summary_facts": summary_facts, "gather_notes": notes}

    async def assess(self, state: MarinaState) -> dict[str, Any]:
        """Evaluate the flow's deterministic DMN chain and decide ROUTING (neutral vs human).

        ADR-0012: the DMN decides; the LLM reasons over the result (dossier). NO branch here ever
        gloses a conta, denies a recurso, or approves/denies/reduces a reembolso —
        only `auto_route` (neutral routing) or `human_review` (fail-safe/instructive).

        FAIL-SAFE: any DMN in the chain being unavailable NEVER becomes an adverse outcome by
        omission — always `human_review` (see module docstring's disclosed divergence from the
        donor for `glosa_classification` specifically).
        """
        if state.get("error"):
            # Same rationale as `gather`'s bail: only reachable via `receive`'s own guard
            # post-sanitization; re-asserts the fail-safe route (R1 cycle-1, defense in depth).
            return {"route": "human_review"}
        flow = _flow(state)
        if flow == "recurso":
            return await self._assess_recurso(state)
        if flow == "reembolso":
            # No reembolso DMN is re-evaluated here (already ran in the BPMN before this hop) —
            # sync, no I/O (module docstring's structural divergence from contas/recurso).
            return self._assess_reembolso(state)
        return await self._assess_contas(state)

    async def _assess_contas(self, state: MarinaState) -> dict[str, Any]:
        """CONTAS flow: normalization -> classification -> triage (+ SLA, informative)."""
        dmn_refs: dict[str, str] = {}

        # A fraud signal is INFORMATIVE (L0 hard): never self-flags, routes to human to decide
        # the referral. Takes precedence over automatic triage.
        if bool(state.get("indicio_fraude_sinalizado", False)):
            sla = await self._evaluate_dmn(DMN_CONTAS_SLA, self._contas_sla_input(state))
            base = self._sla_base(sla, dmn_refs, DMN_CONTAS_SLA)
            return {**base, **self._route_human("indicio_fraude", dmn_refs, "auditoria-contas")}

        # 1) Reason code normalization (first code in the list — defensive).
        reason_codes = state.get("reason_codes_tiss") or []
        reason_code = str(reason_codes[0]) if reason_codes else ""
        norm = await self._evaluate_dmn(DMN_GLOSA_NORMALIZATION, {"reason_code_tiss": reason_code})
        if norm.get("error"):
            return self._route_human(
                "dmn_indisponivel", dmn_refs, "auditoria-contas", dmn_error=str(norm["error"])
            )
        categoria = str(norm["row"].get("categoria_normalizada", "desconhecida"))
        dmn_refs[DMN_GLOSA_NORMALIZATION] = norm["ref"]

        # 2) Classification (categoria + worker denial_ratio). See module docstring's disclosed
        # divergence: failure here now routes human (donor let it pass through silently).
        classif = await self._evaluate_dmn(
            DMN_GLOSA_CLASSIFICATION,
            {"categoria_normalizada": categoria, "denial_ratio": float(state.get("denial_ratio", 0.0))},
        )
        if classif.get("error"):
            return self._route_human(
                "dmn_indisponivel", dmn_refs, "auditoria-contas", dmn_error=str(classif["error"])
            )
        glosa_type = str(classif["row"].get("glosa_type", ""))
        dmn_refs[DMN_GLOSA_CLASSIFICATION] = classif["ref"]

        # 3) SLA (always — attaches deadlines to the dossier; never decides routing).
        sla = await self._evaluate_dmn(DMN_CONTAS_SLA, self._contas_sla_input(state))
        base = self._sla_base(sla, dmn_refs, DMN_CONTAS_SLA)
        base["categoria_normalizada"] = categoria
        base["glosa_classificada"] = glosa_type

        # 4) Triage — the negativa-like heart of CONTAS, with NO accept/confirm output.
        triage = await self._evaluate_dmn(
            DMN_GLOSA_TRIAGE,
            {
                "tipo_item": str(state.get("tipo_lote", "")),
                "categoria_normalizada": categoria,
                "item_conforme_tabela": bool(state.get("item_conforme_tabela", False)),
                "divergencia_valor": bool(state.get("divergencia_valor", False)),
                "documentacao_anexa": bool(state.get("documentacao_anexa", False)),
            },
        )
        if triage.get("error"):
            return {
                **base,
                **self._route_human(
                    "dmn_indisponivel", dmn_refs, "auditoria-contas", dmn_error=str(triage["error"])
                ),
            }
        triagem = cast(TriagemGlosa, str(triage["row"].get("roteamento", "ANALISE_HUMANA")))
        dmn_refs[DMN_GLOSA_TRIAGE] = triage["ref"]
        base["triagem"] = triagem
        base["dmn_refs"] = dmn_refs

        # FAIL-SAFE (closed allowlist): ONLY the known neutral value proceeds to auto_route.
        # PAGAR -> neutral routing, and the FAVOURABLE outcome (never a glosa). ANY other value
        # (ANALISE_HUMANA, an unexpected/unknown value, empty) -> human. Mirrors Rafael: nothing
        # ambiguous falls through to automatic by omission (L0 hard invariant).
        if triagem == "PAGAR":
            return {**base, "route": "auto_route", "desfecho": "pagar_integral", "dmn_refs": dmn_refs}
        return {**base, **self._route_human("triagem_analise_humana", dmn_refs, "auditoria-contas")}

    async def _assess_recurso(self, state: MarinaState) -> dict[str, Any]:
        """RECURSO flow: admissibility -> eligibility (+ SLA, informative)."""
        dmn_refs: dict[str, str] = {}

        # 1) Admissibility — NO NEGAR/INADMISSIVEL output.
        admis = await self._evaluate_dmn(
            DMN_RECURSO_ADMISSIBILITY,
            {
                "glosa_existe": bool(state.get("glosa_existe", False)),
                "dentro_prazo_recurso": bool(state.get("dentro_prazo_recurso", False)),
                "documentacao_recurso_completa": bool(state.get("documentacao_recurso_completa", False)),
            },
        )
        if admis.get("error"):
            return self._route_human(
                "dmn_indisponivel", dmn_refs, "analista-recurso-glosa", dmn_error=str(admis["error"])
            )
        admissibilidade = cast(AdmissibilidadeRecurso, str(admis["row"].get("roteamento", "ANALISE_HUMANA")))
        dmn_refs[DMN_RECURSO_ADMISSIBILITY] = admis["ref"]

        # 2) SLA (always — attaches deadlines to the dossier).
        sla = await self._evaluate_dmn(DMN_RECURSO_SLA, self._recurso_sla_input(state))
        base = self._sla_base(sla, dmn_refs, DMN_RECURSO_SLA)
        base["admissibilidade_recurso"] = admissibilidade

        if admissibilidade == "PENDENTE_DOCUMENTACAO":
            return {**base, **self._route_human("documentacao_pendente", dmn_refs, "analista-recurso-glosa")}
        # FAIL-SAFE (closed allowlist): ONLY `SEGUE_ANALISE` proceeds to eligibility.
        # ANALISE_HUMANA and any other/unexpected value -> human by omission (L0 hard).
        if admissibilidade != "SEGUE_ANALISE":
            return {**base, **self._route_human("recurso_analise_humana", dmn_refs, "analista-recurso-glosa")}

        # 3) Eligibility — NO INDEFERIR output.
        elig = await self._evaluate_dmn(
            DMN_RECURSO_ELIGIBILITY,
            {
                "glosa_type": str(state.get("glosa_type", "")),
                "glosa_reason_code": str(state.get("glosa_reason_code", "")),
                "valor_glosado_brl": float(state.get("valor_glosado_brl", 0.0)),
            },
        )
        if elig.get("error"):
            return {
                **base,
                **self._route_human(
                    "dmn_indisponivel", dmn_refs, "analista-recurso-glosa", dmn_error=str(elig["error"])
                ),
            }
        elegibilidade = cast(ElegibilidadeRecurso, str(elig["row"].get("roteamento", "ANALISE_HUMANA")))
        grupo_revisor = str(elig["row"].get("grupo_revisor", "analista-recurso-glosa"))
        dmn_refs[DMN_RECURSO_ELIGIBILITY] = elig["ref"]
        base["elegibilidade_recurso"] = elegibilidade
        base["grupo_revisor"] = grupo_revisor
        base["dmn_refs"] = dmn_refs

        # FAIL-SAFE (closed allowlist): ONLY `SEGUE_MERITO` with a non-medico-auditor reviewer
        # proceeds to auto_route. Glosa tecnica/clinica (grupo_revisor=medico-auditor) always
        # goes human for merit review. ANALISE_HUMANA / any unexpected value -> human by omission.
        if elegibilidade == "SEGUE_MERITO" and grupo_revisor != "medico-auditor":
            return {**base, "route": "auto_route", "desfecho": "recurso_segue_analise", "dmn_refs": dmn_refs}
        grupo = "medico-auditor" if grupo_revisor == "medico-auditor" else "analista-recurso-glosa"
        return {**base, **self._route_human("recurso_analise_humana", dmn_refs, grupo)}

    def _assess_reembolso(self, state: MarinaState) -> dict[str, Any]:
        """REEMBOLSO flow: factual dossier of an ALREADY-RUNNING SP-OP-REEMBOLSO-001 instance.

        Structurally different from CONTAS/RECURSO: Marina re-evaluates NO reembolso DMN
        (`reembolso_admissibility`/`reembolso_calculo`/`reembolso_auto_approval` already ran in
        the BPMN's own `BRT_Admissibilidade`/`BRT_Calculo`/`BRT_AutoApproval` BEFORE she is
        convoked — the facts arrive pre-resolved in `cobertura_prevista`/`dentro_prazo`/
        `dentro_tabela`/`dentro_teto_l2`/`valor_calculado_tabela_cents`) and never starts a
        process (`start_process` is a no-op outside `contas`/`recurso`). She only ASSEMBLES the
        factual dossier and ALWAYS routes to the `analise-reembolso` human group
        (`UT_AnaliseReembolso`) — NO `auto_route` variant exists for this flow: reembolso
        approval/denial/reduction is ALWAYS a human decision (ADR-0005/0018, worker-guard
        `ERR_REEMBOLSO_DENIAL_NOT_HUMAN`).
        """
        return {
            **self._route_human("reembolso_dossie", {}, "analise-reembolso"),
            "desfecho": "reembolso_dossie_humano",
        }

    async def auto_route(self, state: MarinaState) -> dict[str, Any]:
        """Neutral routing (PAGAR/SEGUE_ANALISE/SEGUE_MERITO). Only reachable for `contas`/`recurso`.

        GUARDRAIL: this path NEVER gloses a conta nor indefere a recurso. It only
        assembles the dossier and records the routing outcome; `start_process` (next node) opens
        the instance, whose human User Task is where any substantive decision is made.
        """
        return {"dossier": await self._build_dossier(state, route="auto_route")}

    async def human_review(self, state: MarinaState) -> dict[str, Any]:
        """Prepares the human analyst's/auditor's/coordenacao's dossier and marks the human route.

        This is the route for ANY ambiguous/technical/clinical/pending/fraud-signal/
        DMN-unavailable case, and the ONLY route in `reembolso`. NONE of these is an accept/deny:
        the APPLICATION of a glosa is born SOLELY in `UT_AnalistaContas`; the recurso INDEFERIMENTO SOLELY
        in `UT_AnaliseRecursoAnalista`/medico-auditor merit review; reembolso approval/denial/
        reduction SOLELY in `UT_AnaliseReembolso`/`UT_RevisaoAuditorMedico`/
        `UT_CoordenacaoReembolso`. Marina instructs; the human decides.
        """
        dossier = await self._build_dossier(state, route="human_review")
        desfecho = state.get("desfecho") or self._human_desfecho(state)
        return {"dossier": dossier, "desfecho": desfecho}

    async def start_process(self, state: MarinaState) -> dict[str, Any]:
        """Starts (idempotently) the flow's process with the contract's variables.

        ONLY for `contas`/`recurso` (Marina STARTS those). NO-OP in `reembolso`: the
        SP-OP-REEMBOLSO-001 instance is already running when `reembolso.analyze` arrives
        (convoked from INSIDE the process, `ST_PrepararDossie`) — starting a second instance
        would duplicate the case.

        Idempotent business key (the start checks the key before creating — a resend returns the
        active instance). A start failure never loses the case: it records the error and keeps
        the routing. NEVER emits a glosa nor a recurso denial "on the side".
        """
        if _flow(state) == "reembolso":
            return {}  # SP-OP-REEMBOLSO-001 is already running — Marina NEVER starts a 2nd instance.
        if state.get("error") and not state.get("business_key"):
            return {"process_started": False}

        business_key = state.get("business_key") or _business_key(state)
        variables = self._contract_variables(state)
        provenance = AgentDecisionProvenance(
            agent_id="marina",
            agent_version=self._agent_version,
            tenant_id=state.get("tenant_id", ""),
            model_id=self._model_id,
            prompt_version=SYSTEM_PROMPT_VERSION,
            decision_basis={
                "route": state.get("route", ""),
                "desfecho": state.get("desfecho", ""),
                "flow": _flow(state),
            },
        )
        try:
            instance = await start_process_idempotent(
                self._cibseven,
                process_key=_process_key(state),
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

    async def notify_start_failure(self, state: MarinaState) -> dict[str, Any]:
        """CC-01: o start FALHOU — grava o desfecho de erro e ALERTA, em vez de seguir calado.

        Ate CC-01 a aresta que saia de `start_process` era INCONDICIONAL: o turno chegava ao
        terminal com o `desfecho` de SUCESSO que um no a montante ja havia gravado, afirmando um
        fato que nao aconteceu, e sem prazo nenhum — o timer de SLA vive na instancia BPMN que
        nunca nasceu. O corpo deste no e o helper compartilhado
        (`maezo.runtime.start_outcome.notify_start_failure`): uma definicao para os 9 agentes,
        nunca 9 copias.
        """
        return emit_start_failure_notice(dict(state), agent_id="marina", process_key=_process_key(state))

    async def finalize(self, state: MarinaState) -> dict[str, Any]:
        """Terminal node — no further computation; `desfecho` was already set upstream.

        No episodic memory write here (labeled boundary, module docstring — same rationale as
        Rafael's/Helena's graphs).
        """
        return {}

    # -- Conditional routing --------------------------------------------------------------

    @staticmethod
    def _route(state: MarinaState) -> str:
        # FAIL-SAFE: on absence/doubt, ALWAYS human (never auto_route by omission).
        return "auto_route" if state.get("route") == "auto_route" else "human_review"

    @staticmethod
    def _default_human_group(flow: Flow) -> str:
        if flow == "recurso":
            return "analista-recurso-glosa"
        if flow == "reembolso":
            return "analise-reembolso"
        return "auditoria-contas"

    @staticmethod
    def _human_desfecho(state: MarinaState) -> str:
        flow = _flow(state)
        if flow == "recurso":
            return "recurso_humano"
        if flow == "reembolso":
            return "reembolso_dossie_humano"
        return "triagem_humana"

    @staticmethod
    def _route_human(
        motivo: MotivoHumano,
        dmn_refs: dict[str, str],
        grupo_humano: str,
        *,
        dmn_error: str | None = None,
    ) -> dict[str, Any]:
        out: dict[str, Any] = {
            "route": "human_review",
            "motivo_humano": motivo,
            "grupo_humano": grupo_humano,
            "dmn_refs": dmn_refs,
        }
        if dmn_error is not None:
            out["dmn_error"] = dmn_error
        return out

    @staticmethod
    def _contas_sla_input(state: MarinaState) -> dict[str, Any]:
        return {
            "tipo_lote": str(state.get("tipo_lote", "")),
            "valor_apresentado_brl": float(state.get("valor_apresentado_brl", 0.0)),
        }

    @staticmethod
    def _recurso_sla_input(state: MarinaState) -> dict[str, Any]:
        return {
            "glosa_type": str(state.get("glosa_type", "")),
            "valor_glosado_brl": float(state.get("valor_glosado_brl", 0.0)),
        }

    @staticmethod
    def _sla_base(sla: dict[str, Any], dmn_refs: dict[str, str], table: str) -> dict[str, Any]:
        sla_row = sla.get("row", {}) if not sla.get("error") else {}
        if not sla.get("error"):
            dmn_refs[table] = sla["ref"]
        return {
            "sla_analise": str(sla_row.get("sla_analise", "")),
            "sla_alerta": str(sla_row.get("sla_alerta", "")),
        }

    # -- DMN (ADR-0028): the engine evaluates; the LLM never decides ----------------------------

    async def _evaluate_dmn(self, table: str, dmn_input: dict[str, Any]) -> dict[str, Any]:
        try:
            rows, version = await self._dmn.evaluate(table, dmn_input)
            row = first_row(rows, table, dmn_input)
        except (DmnEvaluationError, DmnNoResultError) as exc:
            return {"error": f"DMN `{table}` indisponivel: {exc}"}
        # See `rafael/graph.py::_evaluate_dmn`/`helena/graph.py::_evaluate_dmn` for why this cites
        # the decision-definition id (ADR-0028 §2) rather than a rule id the engine's evaluate
        # response never returns.
        return {"row": row, "ref": f"{table}#{version.id}"}

    # -- Contract variables + dossier assembly (ADR-0007 audit provenance) --------------------

    def _contract_variables(self, state: MarinaState) -> dict[str, Any]:
        """Assembles the flow's process start variables (exactly the contract's own input set).

        Includes Marina's dossier as `dossie_marina` (instruction) and `motivo_encaminhamento`/
        `grupo_destino` when routed to human — NEVER a glosa applied nor a recurso denied.
        Never called for `reembolso` (`start_process` no-ops before this for that flow).
        """
        flow = _flow(state)
        variables: dict[str, Any] = {
            "tenant_id": state.get("tenant_id", ""),
            "beneficiario_pseudo_id": state.get("beneficiario_pseudo_id", ""),
            "prestador_id": state.get("prestador_id", ""),
            "source_agent_id": "marina",
            "source_agent_version": self._agent_version,
            "dossie_marina": state.get("dossier") or {},
            "marina_flow": flow,
            "marina_route": state.get("route", "human_review"),
        }
        if flow == "recurso":
            variables.update(
                {
                    "numero_guia_tiss": state.get("numero_guia_tiss", ""),
                    "glosa_id": state.get("glosa_id", ""),
                    "numero_lote_tiss": state.get("numero_lote_tiss", ""),
                    "glosa_type": str(state.get("glosa_type", "")),
                    "glosa_reason_code": str(state.get("glosa_reason_code", "")),
                    "valor_glosado_brl": float(state.get("valor_glosado_brl", 0.0)),
                    "codigo_procedimento_tuss": state.get("codigo_procedimento_tuss", ""),
                    "documentos_recurso_refs": state.get("documentos_recurso_refs") or [],
                    "glosa_existe": bool(state.get("glosa_existe", False)),
                    "dentro_prazo_recurso": bool(state.get("dentro_prazo_recurso", False)),
                    "documentacao_recurso_completa": bool(state.get("documentacao_recurso_completa", False)),
                }
            )
            if state.get("cid10"):
                variables["cid10"] = state["cid10"]
            # `data_ciencia_alegada_prestador` stays a PLEITO datum (the date the prestador
            # ALLEGES in the appeal he filed), never an SLA anchor: `recurso_sla` no longer
            # falls back to it (ADR-0040 §2.3). Renamed from the old identifier in the PR-3
            # gate repair (M1): that one named the APPELLANT's clock, and the perspective
            # fence (PR-1, family `ancora-kpi`) flags it as such.
            if state.get("data_ciencia_alegada_prestador"):
                variables["data_ciencia_alegada_prestador"] = state["data_ciencia_alegada_prestador"]
            # ALWAYS seeded — it is the SINGLE anchor of the analysis SLA and of the absolute
            # ceiling, and `recurso_sla`'s FEEL has no other branch to fall into. Blank here is
            # fine and deliberate: `operadora.recurso.validate_recurso` (ST_ValidarRecurso, the
            # first task on every path) normalises it and defaults fail-safe to TODAY/UTC with a
            # warning. Seeding it conditionally would leave the variable UNDEFINED in process
            # scope, which is what ENGINE-16004 ("Cannot resolve identifier") is about.
            variables["data_recebimento_recurso_iso"] = state.get("data_recebimento_recurso_iso", "")
        else:
            variables.update(
                {
                    "numero_lote_tiss": state.get("numero_lote_tiss", ""),
                    "competencia": state.get("competencia", ""),
                    "valor_apresentado_brl": float(state.get("valor_apresentado_brl", 0.0)),
                    "tipo_lote": str(state.get("tipo_lote", "")),
                    "linhas_conta_refs": state.get("linhas_conta_refs") or [],
                    "reason_codes_tiss": state.get("reason_codes_tiss") or [],
                    "divergencia_valor": bool(state.get("divergencia_valor", False)),
                    "item_conforme_tabela": bool(state.get("item_conforme_tabela", False)),
                    "documentacao_anexa": bool(state.get("documentacao_anexa", False)),
                    "indicio_fraude_sinalizado": bool(state.get("indicio_fraude_sinalizado", False)),
                }
            )
            if state.get("data_recebimento_lote"):
                variables["data_recebimento_lote"] = state["data_recebimento_lote"]
            if state.get("numero_guia_tiss"):
                variables["numero_guia_tiss"] = state["numero_guia_tiss"]
            if state.get("numero_conta"):
                variables["numero_conta"] = state["numero_conta"]

        if state.get("route") == "human_review":
            # `or`-based (not `.get(key, default)`): post-sanitization these keys EXIST with
            # value `None` until a node sets them — the default must still apply then.
            variables["motivo_encaminhamento"] = state.get("motivo_humano") or "outro"
            variables["grupo_destino"] = state.get("grupo_humano") or self._default_human_group(flow)
        # Auditable rule references (ADR-0007/0012). Note: `dmn_error` (raw transport error text,
        # potentially engine-echoed) is DELIBERATELY never included here — only the bounded class
        # token in `motivo_encaminhamento` reaches engine-bound variables (hardening: engine-
        # variable hygiene).
        dmn_refs = state.get("dmn_refs")
        if dmn_refs:
            variables["dmn_decision_refs"] = dmn_refs
        return variables

    async def _build_dossier(self, state: MarinaState, *, route: Route) -> dict[str, Any]:
        """Assembles the analyst's/auditor's/coordenacao's dossier. The LLM reasons over the
        FACTS; it never decides. Failure never blocks the route — falls back to a minimal
        deterministic dossier with an empty narrative (mirrors Rafael/Helena exactly)."""
        flow = _flow(state)
        if flow == "recurso":
            facts = self._recurso_facts(state)
            prompt_text = recurso_prompt()
            prompt_version = RECURSO_PROMPT_VERSION
        elif flow == "reembolso":
            facts = self._reembolso_facts(state)
            prompt_text = reembolso_prompt()
            prompt_version = REEMBOLSO_PROMPT_VERSION
        else:
            facts = self._contas_facts(state)
            prompt_text = dossier_prompt()
            prompt_version = DOSSIER_PROMPT_VERSION

        motivo_humano = state.get("motivo_humano") if route == "human_review" else None
        grupo_humano = state.get("grupo_humano") if route == "human_review" else None
        prompt = f"{prompt_text}\n\nflow={flow} route={route} motivo_humano={motivo_humano}\nfatos={facts}"
        try:
            narrativa = await self._llm.generate(
                prompt, phi=True, agent_id="marina", tenant_id=state.get("tenant_id", "")
            )
        except Exception:  # noqa: BLE001 — LLM failure never blocks the human/auto route.
            narrativa = ""

        return {
            "prompt_version": prompt_version,
            "flow": flow,
            "route": route,
            "motivo_humano": motivo_humano,
            "grupo_humano": grupo_humano,
            "fatos": facts,
            "dmn_decision_refs": state.get("dmn_refs", {}),
            "narrativa": narrativa,
            "documentos_refs": self._documentos_refs_for(flow, state),
            # STRUCTURAL GUARDRAIL (L0 hard): the dossier NEVER carries an adverse decision.
            "decisao_glosa": None,  # glosa APPLICATION: SOLELY UT_AnalistaContas
            "decisao_recurso": None,  # recurso deferimento/indeferimento: SOLELY UT_AnaliseRecursoAnalista
            "decisao_reembolso": None,  # approval/denial/reduction: ALWAYS human (UT_AnaliseReembolso+)
        }

    @staticmethod
    def _documentos_refs_for(flow: Flow, state: MarinaState) -> list[dict[str, Any]]:
        """Document refs attached to the dossier, per flow (never raw PHI — only refs/ids)."""
        if flow == "recurso":
            return state.get("documentos_recurso_refs") or []
        if flow == "reembolso":
            # `tools/workers/reembolso.py`'s payload does not carry a document-ref list today —
            # only pre-resolved identifiers/booleans/numerics.
            return []
        return state.get("linhas_conta_refs") or []

    @staticmethod
    def _contas_facts(state: MarinaState) -> dict[str, Any]:
        return {
            "numero_lote_tiss": state.get("numero_lote_tiss"),
            "numero_guia_tiss": state.get("numero_guia_tiss"),
            "numero_conta": state.get("numero_conta"),
            "competencia": state.get("competencia"),
            "tipo_lote": state.get("tipo_lote"),
            "valor_apresentado_brl": state.get("valor_apresentado_brl"),
            "reason_codes_tiss": state.get("reason_codes_tiss", []),
            "categoria_normalizada": state.get("categoria_normalizada"),
            "glosa_classificada": state.get("glosa_classificada"),
            "denial_ratio": state.get("denial_ratio"),
            "item_conforme_tabela": state.get("item_conforme_tabela"),
            "divergencia_valor": state.get("divergencia_valor"),
            "documentacao_anexa": state.get("documentacao_anexa"),
            "triagem": state.get("triagem"),
            "indicio_fraude_sinalizado": state.get("indicio_fraude_sinalizado"),
            "dmn_refs": state.get("dmn_refs", {}),
            "sla_analise": state.get("sla_analise"),
            "lacunas_enriquecimento": state.get("gather_notes", []),
        }

    @staticmethod
    def _recurso_facts(state: MarinaState) -> dict[str, Any]:
        return {
            "numero_guia_tiss": state.get("numero_guia_tiss"),
            "glosa_id": state.get("glosa_id"),
            "glosa_type": state.get("glosa_type"),
            "glosa_reason_code": state.get("glosa_reason_code"),
            "valor_glosado_brl": state.get("valor_glosado_brl"),
            "codigo_procedimento_tuss": state.get("codigo_procedimento_tuss"),
            "cid10": state.get("cid10"),
            "glosa_existe": state.get("glosa_existe"),
            "dentro_prazo_recurso": state.get("dentro_prazo_recurso"),
            "documentacao_recurso_completa": state.get("documentacao_recurso_completa"),
            "admissibilidade_recurso": state.get("admissibilidade_recurso"),
            "elegibilidade_recurso": state.get("elegibilidade_recurso"),
            "grupo_revisor": state.get("grupo_revisor"),
            "dmn_refs": state.get("dmn_refs", {}),
            "sla_analise": state.get("sla_analise"),
            "lacunas_enriquecimento": state.get("gather_notes", []),
        }

    @staticmethod
    def _reembolso_facts(state: MarinaState) -> dict[str, Any]:
        """REEMBOLSO facts — ALL pre-resolved by the process BEFORE this hop (Marina REPORTS
        them, never recomputes: `dmn_refs` is deliberately absent here — no DMN was evaluated by
        her in this flow, unlike CONTAS/RECURSO. `dentro_teto_l2` in particular is a pure
        pass-through of the pre-resolved worker fact — see module docstring's L0-hard invariant)."""
        return {
            "protocolo_reembolso": state.get("protocolo_reembolso"),
            "numero_guia_tiss": state.get("numero_guia_tiss"),
            "tipo_reembolso": state.get("tipo_reembolso"),
            "categoria_procedimento": state.get("categoria_procedimento"),
            "codigo_procedimento_tuss": state.get("codigo_procedimento_tuss"),
            "valor_solicitado_cents": state.get("valor_solicitado_cents"),
            "valor_calculado_tabela_cents": state.get("valor_calculado_tabela_cents"),
            "cobertura_prevista": state.get("cobertura_prevista"),
            "dentro_prazo": state.get("dentro_prazo"),
            "dentro_tabela": state.get("dentro_tabela"),
            "dentro_teto_l2": state.get("dentro_teto_l2"),
            "lacunas_enriquecimento": state.get("gather_notes", []),
        }

    # -- Graph assembly ---------------------------------------------------------------------

    def compile_graph(self) -> StateGraph[MarinaState]:
        g: StateGraph[MarinaState] = StateGraph(MarinaState)
        g.add_node("receive", self.receive)
        g.add_node("gather", self.gather)
        g.add_node("assess", self.assess)
        g.add_node("auto_route", self.auto_route)
        g.add_node("human_review", self.human_review)
        g.add_node("start_process", self.start_process)
        g.add_node("notify_start_failure", self.notify_start_failure)
        g.add_node("finalize", self.finalize)

        g.add_edge(START, "receive")
        g.add_edge("receive", "gather")
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


def build(config: dict[str, Any] | None = None) -> StateGraph[MarinaState]:
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
            f"Marina build(config) is missing required dependencies: {missing} "
            "(ADR-0001/0007/0009/0028 — inference/dmn/cibseven/audit_sink must all be injected; "
            "audit_sink is the T-C2 fence — no process start without a durable sink)"
        )
    agent_version = str(cfg.get("agent_version", "marina@v0"))
    return MarinaGraph(
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
    "dossier": DOSSIER_PROMPT_VERSION,
    "recurso": RECURSO_PROMPT_VERSION,
    "reembolso": REEMBOLSO_PROMPT_VERSION,
}

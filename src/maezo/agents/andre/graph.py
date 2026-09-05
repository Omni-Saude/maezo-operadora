"""Andre Nakamura — Analista de Populacao/Atuarial e Lago de Dados Agent (Phase 3, T1.12).

Journey (Andre is explicitly the Rafael/Marina analog, READ-ONLY reference
`Maezo-Healthcare-Plan src/maezo/agents/andre/graph.py`, cloning the same proven structure and
adapted to v2's flatter seam set — same rationale as `agents/rafael/graph.py`'s and
`agents/helena/graph.py`'s module docstrings):

    receive -> gather -> assess(DMN) -> {auto_route | human_review} -> start_process -> finalize

Andre serves THREE flows, distinguished by `flow` in state:

- `pagto_dossier` (default/core): convoked by `operadora.pagto.prepare_approval_dossier` (A2A
  `analytics.actuarial` / `analytics.population`, SP-OP-PAGTO-001). Enriches the APPROVAL dossier
  of SP-OP-PAGTO-001 with AGGREGATED actuarial/population risk. `assess` evaluates the DMN chain
  `pagto_admissibility` -> `pagto_alcada` (classifies faixa + value-driven approver group; NEVER
  releases) -> `pagto_sla` (informative only, evaluated with the REAL resolved faixa, only on the
  above-teto human branch — mirrors the BPMN's `BRT_PagtoSla` position after `GW_Faixa`).
  `auto_route` means ONLY "low-value clerical L2 dossier ready" (`faixa_valor=DENTRO_TETO_L2` AND
  the worker-pre-resolved `dentro_teto_l2` flag); EVERYTHING above the L2 ceiling or ambiguous ->
  `human_review` (human approval with tier-match). Andre STARTS/anchors SP-OP-PAGTO-001
  idempotently (business key `PAGTO-{tenant}-{ordem_pagamento_id}`, or
  `PAGTO-{tenant}-{numero_lote_tiss}-{prestador_id}` when the payment stems from an adjudicated
  CONTAS-001 account) — the idempotent start returns the already-running instance untouched when
  the delegation arrives from inside it.
- `population_analytics`: autonomous population/actuarial analytics over cohorts (A2A capability
  `population_analytics`). Produces a cohort dossier over k-anon AGGREGATES delivered by the
  injected `PopulationFeatureClient`. No payment => no adverse effect possible; routing is
  neutral (`auto_route`, `desfecho=analytics_pronto`) unless the egress gate blocked an
  aggregate. `start_process` is a NO-OP (no process for autonomous analytics).
- `adequacao_dossier`: convoked by `operadora.adequacao.prepare_remediation_dossier` (A2A
  `analytics.population`, SP-OP-ADEQUACAO-001 contract) ONLY on the ANALISE_HUMANA branch of the
  ALREADY-RUNNING adequacao instance. Andre does NOT re-evaluate `adequacao_gap`/
  `adequacao_remediation_routing` (already resolved by the process's own DMNs BEFORE this hop),
  evaluates NO pagto DMN, and NEVER starts a process (`start_process` is a no-op; the business
  key `ADEQ-{tenant}-{regiao}-{especialidade}[-{ciclo}]` only ANCHORS the factual dossier to the
  cell/instance feeding `UT_DecisaoFallback`). Route is ALWAYS `human_review`
  (`gestao-rede` — no auto variant exists for this flow).

An UNRECOGNIZED `flow` value is fail-neutral: `receive` routes it to conservative human review
(`ambiguidade`, comite) with NO business key and NO process start — never treated as a payment
(mirrors the donor's `dossier_review` fail-neutral flow).

`assess` ALWAYS consults the deterministic DMNs for `pagto_dossier` (ADR-0012); the LLM REASONS
over the DMN results + k-anon aggregates to assemble the risk dossier — it NEVER substitutes or
re-decides them. DMN evaluation is ENGINE-SIDE via the `DmnTransport` triple (T1.5/ADR-0028) —
no DMN table is ever re-implemented in Python here.

L0 HARD STRUCTURAL GUARDRAIL (contract SP-OP-PAGTO-001 §invariante L1 no-adverse; ADR-0008/0018;
`spec/agents/andre/agent.yaml` autonomy notes; CI-enforced): Andre NEVER decides a price, NEVER
releases/authorizes a payment, NEVER decides clinically, NEVER accuses fraud. The `Route` type
has NO adverse variant — only `auto_route` (neutral: below-ceiling clerical L2 dossier ready /
analytics ready) and `human_review` (fail-safe, always available — human approval with
tier-match). NO automatic outcome releases/authorizes/pays: the high-value release is born SOLELY
in the human User Task `UT_AprovacaoAlcada`/`UT_AprovacaoComite` with `decisao_pagamento=APROVAR`
and tier-match, and is materialized ONLY by the gated worker
`operadora.pagto.release_high_value_payment` (`ERR_PAYMENT_RELEASE_NOT_HUMAN` + tier-match guard,
`tools/workers/pagto.py`, T1.5 cutover — untouched by this build; this graph never invokes it).
Ambiguous faixa, value above the highest tier, inconsistent data, a duplicity signal, a PHI
egress risk, DMN unavailability, or an out-of-allowlist DMN output ALWAYS fail-safe to the
highest human tier (comite) — never a silent auto_route. KPI invariants:
`false_pricing_decision_rate == 0`, `phi_egress_violations == 0`.

`dentro_teto_l2` (and every other payment fact — `dados_pagamento_validos`/`lastro_confirmado`/
`duplicidade_suspeita`/`valor_pagamento_cents`) arrives PRE-RESOLVED by the deterministic pagto
workers (`operadora.pagto.validate_payment_data`/`calculate_facts` via `CeilingResolver`, T1.9)
— this graph CONSUMES them, NEVER computes them (`tests/unit/sec/test_dentro_teto_source.py`
scans `tools/workers/{reembolso,auth,pagto}.py`; this module never originates the ceiling fact —
every occurrence below is a pure pass-through of `state.get("dentro_teto_l2")` into the DMN input
or the contract variables).

EGRESS STRUCTURAL GUARDRAIL (Andre is the PHI egress chokepoint; ADR-0006/0019): the population/
lake client is INJECTED (Protocol `PopulationFeatureClient`) and returns ONLY k-anon aggregates
(`CohortAggregate`) + `dataset_ref` pointers — NEVER a resolvable `fhir_patient_id`. The graph
only EMITS aggregates; an aggregate carrying resolvable-PHI indications is detected
(`CohortAggregate.has_resolvable_phi`) and SUPPRESSED + routed to human (`phi_egress_risk`) —
never egressed by omission. Suppression notes carry CLASS TOKENS only, never the suspect value
itself (hardening beyond the donor, which echoed `cohort_id!r` into the note).

CALLER-PLANTED-OUTPUT SANITIZATION (mandatory hardening — the defect class R1 found on ALL FOUR
tranche-1 graphs, baked in from the start here): `receive` is the SINGLE graph entry (exactly one
edge out of START, regression-tested) and overwrites EVERY output-only `AndreState` field with a
fresh neutral default (`_output_field_resets`) on ALL of its return paths, BEFORE gather/assess
run. Without this, a hostile caller pre-planting output fields (`error` + `route="auto_route"` +
forged `faixa_valor`/`dmn_refs`/`dossier`/`process_ref`) would ride the early-bail guards past
the DMN gate and ship forged provenance into engine variables / the approver's dossier (on
marina this started a REAL instance). Here: (1) `receive` wipes the plants at turn start —
`route` resets to the fail-safe `human_review`, never a cleared/auto value; (2) the
`gather`/`assess` error bails — reachable only via `receive`'s own guards post-sanitization —
return `{"route": "human_review"}` instead of `{}` (defense in depth); (3) `_CALLER_INPUT_FIELDS`
partitions the state and a completeness test (`test_output_field_partition_is_complete`) forces
every future field to be classified; (4) failure reasons that reach engine variables are bounded
CLASS TOKENS only (`motivo_encaminhamento` Literal) — `dmn_error` (raw transport text) is
deliberately NEVER included in `_contract_variables`.

PHI discipline: Andre's `security_zone` is `phi` (`spec/agents/andre/agent.yaml`) — the one LLM
call per turn (`_build_dossier`'s narrative) passes `phi=True` (ADR-0006/ADR-0017/T1.7).

DIVERGENCES FROM DONOR (disclosed, spec wins per this task's charter):

1. **Duplicity precedence is DMN-decided, not hand-forked in Python.** The donor checks
   `duplicidade_suspeita` BEFORE calling any DMN and detours to human without ever evaluating
   `pagto_admissibility`. The v2 deployed table (`spec/processes/dmn/pagto_admissibility.dmn`,
   rule `r_duplicidade_suspeita`, hitPolicy FIRST) already encodes that precedence as its FIRST
   rule — evaluating ALWAYS the DMN (never hand-forking the precedence) is what ADR-0012 requires
   and what this graph does: `duplicidade_suspeita=true` always enters as a DMN input and the
   table returns `ANALISE_HUMANA` on its own. The bounded class token is then refined to
   `duplicidade_suspeita` from the state's own informative signal (same routing outcome as the
   donor, decided BY the DMN).
2. **`pagto_sla` follows the BPMN's exact sequence, not the donor's.** The donor evaluates
   `pagto_sla` BEFORE `pagto_alcada` with an empty `faixa_valor` input (always hitting the
   catch-all) and again on the duplicity shortcut. The BPMN (`BRT_PagtoSla`) evaluates it ONLY on
   the above-teto human branch, AFTER `GW_Faixa`, with the real resolved `faixa_valor`. This
   graph mirrors the BPMN: SLA is evaluated only once the faixa is resolved to a non-clerical
   value, with that faixa as input; admissibility human shortcuts terminate before it (mirrors
   `UT_AnaliseAdmissibilidade`'s branch, which has no SLA task). Informative-only either way —
   SLA failure never blocks routing (mirrors Rafael's `auth_sla`).
3. **Admissibility-branch human group is `coordenacao-financeira`, not comite.** The BPMN's
   `UT_AnaliseAdmissibilidade` (the target of the non-admissible branch) declares
   `camunda:candidateGroups="coordenacao-financeira"`; the donor sent `PENDENTE_DADOS`/
   admissibility-`ANALISE_HUMANA` to `comite-financeiro`. This graph follows the BPMN. DMN-down/
   ambiguity/out-of-allowlist still go to the conservative `comite-financeiro` (highest tier).
4. **The clerical path echoes the DMN's own `grupo_aprovador` output verbatim**
   (`clerical-pagamentos` in the v2 table) instead of the donor's hardcoded `""` — consuming the
   deployed table's output, never rewriting it. The human-destination group (`grupo_humano`) is
   still resolved from the CLOSED alcada-group allowlist only on human routes.
5. **`finalize` does NOT write episodic memory** (donor's `finalize` calls
   `mcp-memory.read_write`). v2 has no `MemoryServer`/pgvector schema wired into any agent graph
   yet — same labeled boundary as `helena/rafael`'s graphs.
6. **No `dossier_review` Flow literal.** The donor models the unrecognized-origin fallback as a
   fourth `Flow` member; v2's charter scopes Andre to THREE flows — the fail-neutral handling of
   an unrecognized `flow` value lives in `receive`/`assess`'s conservative catch-alls instead
   (same behavior: human review, no payment key, no process).
7. **No `model_tiers`/`resolve_tier` routing.** The donor routes the dossier narrative to a
   `reasoning`/frontier tier; v2's `maezo.runtime.inference.InferenceProvider` exposes a single
   `generate(prompt, *, phi)` seam with no tier parameter yet (ADR-0009 gap disclosed by every
   real v2 graph). The agent.yaml `model:` block remains declarative.

LABELED BOUNDARIES (this build, disclosed — never fabricated):
- The population/lake client (`PopulationFeatureClient`) is a Protocol seam defined HERE (the
  spec's agent.yaml note references the donor's `contracts.py` layout; v2's proven idiom keeps
  the Protocol beside the graph, like `rafael/graph.py`'s `FhirReader`). The concrete client is
  PORT-PENDING (WB.4, `mcp-datalake`) — `population=None` is a supported configuration:
  `gather` records an explicit gap note and the dossier proceeds without aggregates rather than
  fabricating them.
- `gather`'s FHIR read is a thin `PatientSummaryReader` Protocol over v2's generic `FhirServer`
  (`agents.rafael.adapters.FhirServerReader.read_patient`, injected by the runtime) — NOT the
  donor's `ToolInvoker`/PEP `mcp-fhir.read_patient` gateway call shape. CORRECTED (`grep -n
  '"andre"' gateway/tool_registry.py`, `_FHIR_ADAPTER_BY_AGENT`): this reader IS wired through a
  PEP-gated ToolRegistry now — `gateway/tool_registry.py::build_agent_seams` wraps it in
  `gateway/seams/fhir.py::GatedFhirReader` (Andre is one of the five agents in
  `_FHIR_ADAPTER_BY_AGENT`, adapter `read_patient`), and both live composition roots
  (`runtime/agent_runtime/service.py::_build_tool_deps`, `platform/webhooks/service.py`) build
  Andre's `fhir` dependency through it — the prior "no PEP/ToolRegistry wiring for agent tool
  calls yet" claim is false today. Best-effort, `pagto_dossier` only (per the agent.yaml tool
  note), and the RAW summary never enters state/dossier (egress chokepoint) — a failure/absence
  degrades to a class-token gap note, never a fabricated fact.
- Cross-agent A2A delegation IS wired for Andre in this build. CORRECTED (CC-04, fleet audit) —
  the prior text here claimed v2's `a2a/` package had no `DelegationEnvelope`/
  `DelegationDispatcher`; both exist and are fully built/tested (`a2a/delegation.py
  ::DelegationEnvelope`, `a2a/dispatcher.py::DelegationDispatcher`, exported from `maezo.a2a`).
  Andre has his own `agents/andre/delegation.py` (`make_andre_handler` TARGET side +
  `delegate_adequacao_dossier`/`delegate_pagto_dossier` ORIGIN side, called from
  `tools/workers/adequacao.py`/`pagto.py`) and is registered in the dossier composition root
  (`runtime/agent_runtime/a2a_composition.py::_DOSSIER_EDGE_AGENT_IDS = ("carolina", "andre")`,
  `handlers={"carolina": carolina_handler, "andre": andre_handler}`) — a real, live edge, not a
  disclosed gap. Andre's graph is ALSO invoked directly with an already-assembled case state in
  the unit tests (both paths exist).
- No episodic memory write (divergence #5 above).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, TypedDict, cast

from langgraph.graph import END, START, StateGraph

from maezo.runtime.inference import InferenceProvider
from maezo.tools.mcp_cibseven.transport import (
    AgentDecisionProvenance,
    AuditStartSink,
    CibSevenError,
    CibSevenTransport,
    StartOutcome,
    start_process_idempotent,
)
from maezo.tools.workers.dmn_transport import (
    DmnEvaluationError,
    DmnNoResultError,
    DmnTransport,
    first_row,
)

from .keys import adequacao_business_key, is_blank, pagto_business_key
from .prompts import DOSSIER_PROMPT_VERSION, SYSTEM_PROMPT_VERSION, dossier_prompt

# --- Domain enums (mirror the SP-OP-PAGTO-001 contract + deployed DMN schema) ------------------

# Andre's flow: payment-approval risk dossier (A2A delegation target), autonomous population
# analytics, or the adequacao remediation dossier (already-running SP-OP-ADEQUACAO-001). Decides
# which DMNs run and whether a process is started — NEVER decides a merit.
Flow = Literal["pagto_dossier", "population_analytics", "adequacao_dossier"]

# Graph routing. STRUCTURALLY WITHOUT AN ADVERSE VARIANT — no value here releases/authorizes/
# pays a payment or decides a price:
#   - auto_route   : NEUTRAL routing determined by the DMN (payment DENTRO_TETO_L2 clerical L2 /
#                    analytics dossier ready) — NEVER an adverse release.
#   - human_review : fail-safe — any faixa above the L2 ceiling, ambiguity, inconsistent data,
#                    duplicity signal, PHI-egress risk or DMN unavailability goes to the human
#                    (approval with tier-match). NEVER a release.
# The ABSENCE of a "release_payment"/"approve"/"price" variant is the structural guarantee (L1).
Route = Literal["auto_route", "human_review"]

# Recognized `pagto_admissibility` outputs (CLOSED allowlist — no release output by design).
Admissibilidade = Literal["SEGUE_ROTEAMENTO", "PENDENTE_DADOS", "ANALISE_HUMANA"]
_ADMISSIBILIDADE_ALLOW: frozenset[str] = frozenset({"SEGUE_ROTEAMENTO", "PENDENTE_DADOS", "ANALISE_HUMANA"})

# Recognized `pagto_alcada` outputs (CLOSED allowlist — classifies the faixa, NEVER releases).
# `DENTRO_TETO_L2` is the ONLY automatic clerical faixa (analogous to auth_auto_approval); every
# `ALCADA_*` faixa and the `ANALISE_HUMANA` catch-all require a human approver of matching tier.
FaixaValor = Literal["DENTRO_TETO_L2", "ALCADA_L1", "ALCADA_L2", "ALCADA_L3", "ANALISE_HUMANA"]
_FAIXA_VALOR_ALLOW: frozenset[str] = frozenset(
    {"DENTRO_TETO_L2", "ALCADA_L1", "ALCADA_L2", "ALCADA_L3", "ANALISE_HUMANA"}
)

# Why the case went to human review — bounded CLASS TOKENS attached to the dossier/contract
# variables (`motivo_encaminhamento`, contract SP-OP-PAGTO-001 §Variaveis de proveniencia).
# NONE of these is a release/denial/price — they are all reasons FOR human review.
MotivoHumano = Literal[
    "aprovacao_alcada",  # faixa ALCADA_* -> human approver of the corresponding tier
    "pendencia_dados",  # pagto_admissibility = PENDENTE_DADOS -> human corrects the data
    "analise_humana",  # admissibility/faixa = ANALISE_HUMANA catch-all -> conservative human
    "duplicidade_suspeita",  # informative duplicity signal -> human confirms (never auto-cancels)
    "phi_egress_risk",  # aggregate with resolvable-PHI indication suppressed -> human (egress blocked)
    "dmn_indisponivel",  # a DMN in the assess chain failed -> never a release by omission
    "ambiguidade",  # out-of-allowlist DMN value / unrecognized flow / missing context -> comite
    "dossie_remediacao",  # adequacao_dossier: factual remediation dossier -> gestao-rede
    "outro",
]

# Human candidate groups of the alcada ladder (contract DRAFT — value-driven by pagto_alcada).
# CLOSED set (ADR-0018 part 3): no group outside this list is ever emitted as a human destination
# on the pagto flow.
_GRUPOS_VALIDOS: frozenset[str] = frozenset(
    {
        "aprovacao-financeira-l1",
        "aprovacao-financeira-l2",
        "aprovacao-financeira-l3",
        "comite-financeiro",
        "coordenacao-financeira",
    }
)
# Faixa -> candidate group of the corresponding tier (map DRAFT/verify financas).
_FAIXA_TO_GRUPO: dict[str, str] = {
    "ALCADA_L1": "aprovacao-financeira-l1",
    "ALCADA_L2": "aprovacao-financeira-l2",
    "ALCADA_L3": "aprovacao-financeira-l3",
    "ANALISE_HUMANA": "comite-financeiro",  # conservative catch-all -> highest tier
}
_GRUPO_CONSERVADOR = "comite-financeiro"
# BPMN `UT_AnaliseAdmissibilidade`'s candidate group (module docstring divergence #3).
_GRUPO_ADMISSIBILIDADE = "coordenacao-financeira"
# Human group of the adequacao_dossier flow: the factual remediation dossier feeds
# `UT_DecisaoFallback` of SP-OP-ADEQUACAO-001 (candidate group per that contract).
_GRUPO_GESTAO_REDE = "gestao-rede"

DMN_PAGTO_ADMISSIBILITY = "pagto_admissibility"
DMN_PAGTO_ALCADA = "pagto_alcada"
DMN_PAGTO_SLA = "pagto_sla"

PROCESS_KEY_PAGTO = "SP-OP-PAGTO-001"

# Origin id of the `operadora.pagto.prepare_approval_dossier` worker (GK-dossier finding 1b).
# SINGLE SOURCE OF TRUTH — `agents.andre.delegation` re-exports this symbol (it cannot be defined
# there: this module must not import the delegation layer, which imports THIS one). A delegation
# carrying this origin comes from INSIDE an ALREADY-RUNNING SP-OP-PAGTO-001 instance, so
# `start_process` structurally refuses to start a second one (same rationale as the
# `adequacao_dossier` no-op, which is flow-scoped rather than origin-scoped).
ORIGIN_PAGTO_WORKER = "pagto-worker"

# The EXACT `error` text `start_process` records when the engine is unreachable. A module
# constant (not an inline literal) so `delegation._degradation_token` can classify it by EQUALITY
# — never by sniffing substrings out of free text (GK-dossier finding 4).
ERROR_START_PROCESS_ENGINE_UNAVAILABLE = "start_process indisponivel (engine inacessivel)"


# --- Injected seams (Protocols) ---------------------------------------------------------------


class PatientSummaryReader(Protocol):
    """Best-effort FHIR summary read seam (`gather`, `pagto_dossier` only). See module
    docstring's labeled boundary — satisfied structurally by
    `agents.rafael.adapters.FhirServerReader.read_patient` when the runtime injects it."""

    async def read_patient(self, patient_id: str) -> dict[str, Any]: ...


@dataclass(frozen=True, slots=True)
class CohortAggregate:
    """K-anon aggregate of a cohort — the ONLY shape that leaves the lake for Andre's graph.

    STRUCTURAL EGRESS GATE (ADR-0006/0019): this type carries NO `fhir_patient_id`, CPF, CNS,
    name, nor individual row — ONLY aggregate counts/metrics and a `dataset_ref` (opaque pointer
    to the aggregated dataset in the lake, never a resolvable patient id). `k_anonymity`
    documents the suppression k (the cohort has `>= k` individuals); `suppressed` marks cells
    suppressed by k-anon. The graph reasons ONLY over this — it never asks for nor receives the
    individual row. Ported from the donor's `contracts.py` (READ-ONLY reference).
    """

    cohort_id: str  # LOGICAL cohort id (e.g. "cohort-amh-2026-06") — never a patient id
    dataset_ref: str  # opaque dataset pointer (e.g. "lake://ds/...") — never PHI
    metrics: dict[str, float] = field(default_factory=dict)  # aggregated actuarial/population metrics
    cohort_size: int = 0
    k_anonymity: int = 0  # k of the applied k-anon suppression (k>=1)
    suppressed: tuple[str, ...] = ()  # names of cells suppressed by k-anon

    def has_resolvable_phi(self) -> bool:
        """Structural defense: True if a field carries a resolvable-PHI indication (NEVER
        should). Since the *type* has no patient-id field, this checks that
        `dataset_ref`/`cohort_id` are opaque pointers (no resolvable FHIR-resource prefix) —
        defense in depth, not the only barrier."""
        suspect = (self.cohort_id, self.dataset_ref)
        return any(
            isinstance(v, str) and (v.startswith("Patient/") or v.startswith("fhir:") or "cpf" in v.lower())
            for v in suspect
        )


class PopulationFeatureClient(Protocol):
    """Injected population/lake client (PORT-PENDING WB.4 — `mcp-datalake`). The graph depends
    ONLY on this Protocol, never on a concrete class (decoupling, ADR-0019). Both operations
    return ONLY `CohortAggregate` (k-anon, with `dataset_ref`) — never a resolvable patient id
    nor an individual row. KPI `phi_egress_violations == 0`."""

    async def actuarial_risk(self, cohort_id: str, *, features: list[str]) -> CohortAggregate: ...

    async def population_metrics(self, cohort_id: str, *, features: list[str]) -> CohortAggregate: ...


# --- Graph state (working memory; ADR-0002) ----------------------------------------------------


class AndreState(TypedDict, total=False):
    """Payment-risk-dossier / population-analytics case state. Everything here is already
    pseudonymized/aggregated (PHI egress zone, ADR-0006): `prestador_id`/`patient_summary_ref`
    NEVER carry a raw CPF/name/CNS/bank detail; cohorts are referenced by `cohort_id` and
    aggregates by `dataset_ref` (k-anon pointers). The payment booleans
    (`dados_pagamento_validos`/`lastro_confirmado`/`dentro_teto_l2`/`duplicidade_suspeita`)
    arrive PRE-RESOLVED by deterministic workers (contract SP-OP-PAGTO-001) — Andre CONSUMES
    them, never computes them (module docstring's L0-hard invariant). `valor_pagamento_cents` is
    INTEGER-CENTAVOS (money never as float/number, ADR-0018 part 2)."""

    # Flow (decides DMNs, work node, dossier shape, and whether a process is started).
    flow: Flow

    # Runtime identifiers / task origin.
    tenant_id: str
    canal: str  # a2a | calendario | portal
    # The ENGINE's authoritative business key, threaded verbatim by a delegation that originates
    # INSIDE an already-running instance (`ExternalTask.business_key` -> envelope -> here).
    # `_business_key` prefers it over its own derivation so an instance keyed with the contract's
    # CONTAS variant (`PAGTO-{tenant}-{lote}-{prestador}`) is never shadowed by an ordem-first
    # derivation (GK-dossier finding 1a). Tenant-scoped at use site — never trusted blindly.
    engine_business_key: str
    # `DelegationEnvelope.origin` of the inbound A2A hop (`""` for a direct/local invocation).
    # `start_process` refuses to start when it is `ORIGIN_PAGTO_WORKER` (finding 1b).
    delegation_origin: str

    # --- pagto_dossier inputs (contract SP-OP-PAGTO-001 §Variaveis de entrada) ---
    ordem_pagamento_id: str
    numero_lote_tiss: str  # when the payment stems from an adjudicated CONTAS-001 account
    prestador_id: str  # pseudonymized creditor
    tipo_pagamento: str  # prestador_rede | reembolso_beneficiario | ... (contract enum)
    valor_pagamento_cents: int  # INTEGER-CENTAVOS (ADR-0018 — money never as number/float)
    moeda: str  # BRL
    competencia: str  # YYYY-MM
    data_vencimento: str  # YYYY-MM-DD — deadline anchor (contract :72, GAP-PAGTO-7)
    conta_origem_ref: str  # token, never a raw account number
    instrumento_pagamento: str  # cnab | pix | ted | compensacao
    # Pre-resolved by worker (deterministic DMN inputs — consumed, never computed).
    dados_pagamento_validos: bool
    lastro_confirmado: bool
    dentro_teto_l2: bool
    duplicidade_suspeita: bool  # INFORMATIVE signal (never decides) — only routes to human

    # --- population_analytics inputs ---
    cohort_id: str
    features: list[str]  # requested actuarial/population features

    # --- adequacao_dossier inputs (contract SP-OP-ADEQUACAO-001, already-resolved facts) ---
    regiao_saude: str  # region/municipality granularity (IBGE) — NEVER a raw address (ADR-0006)
    especialidade: str
    ciclo_avaliacao: str  # YYYY-QN
    tipo_carater: str  # eletivo | urgencia_emergencia
    # Resolved by SP-OP-ADEQUACAO-001's OWN DMNs BEFORE convoking Andre — ECHOED, never recomputed.
    gap_adequacao: str  # CONFORME | GAP_LEVE | GAP_MODERADO | GAP_CRITICO
    roteamento_remediacao: str  # MONITORAR | ENCAMINHAR_CREDENCIAMENTO | ANALISE_HUMANA
    tempo_acesso_apurado_min: int
    distancia_apurada_km: float  # double — never "number" (ADR-0018 part 2)
    prestadores_disponiveis: int
    cobertura_geo_suficiente: bool
    dados_geo_completos: bool

    # FHIR reference for the best-effort summary read (gather, pagto only). Never raw PHI.
    patient_summary_ref: str

    # Filled by `gather` (k-anon aggregates from the injected client; egress-gated).
    gathered: bool
    actuarial_aggregate: dict[str, Any]
    population_aggregate: dict[str, Any]
    aggregate_dataset_refs: list[str]  # opaque k-anon dataset pointers
    egress_blocked: bool  # True if an aggregate carried a resolvable-PHI indication (suppressed)
    gather_notes: list[str]  # enrichment gaps / k-anon suppressions (attached to the dossier)

    # Filled by `assess` (DMN results + refs).
    admissibilidade: Admissibilidade
    faixa_valor: FaixaValor  # pagto_alcada classification (NEVER a release)
    grupo_aprovador: str  # value-driven candidate group echoed from the DMN output
    sla_aprovacao: str  # ISO 8601 (pagto_sla, informative only)
    sla_alerta: str
    dmn_refs: dict[str, str]  # table -> decision-definition reference (ADR-0007/0028)
    dmn_error: str  # raw transport error text — state-only, NEVER an engine variable
    dossier: dict[str, Any]  # the LLM-assembled risk dossier (instruction, never a decision)

    # Routing (NEVER a release/price — only neutral or human).
    route: Route
    motivo_humano: MotivoHumano
    grupo_humano: str  # candidate group of the human destination (closed set)

    # Filled by `start_process` (pagto_dossier only; no-op in the other flows).
    process_started: bool
    business_key: str
    process_ref: dict[str, Any]

    # Output.
    desfecho: str  # dossie_pronto_clerical | dossie_aprovacao_humana | analytics_pronto |
    #                pagamento_pendente_dados | egresso_bloqueado | dossie_remediacao_humano
    error: str  # technical failure (routes human, for safety) — state-only, class-token text


# --- Helpers -----------------------------------------------------------------------------------


def _flow(state: AndreState) -> str:
    """Case flow as given (default `pagto_dossier` — the A2A delegation target). Deliberately
    returns the RAW string: an unrecognized value must fall into the conservative catch-alls,
    never be silently coerced into a known flow."""
    return str(state.get("flow", "pagto_dossier"))


def _business_key(state: AndreState) -> str:
    """Idempotent business key of the case, dispatched by `flow`.

    - `pagto_dossier` (default): the ENGINE's own `engine_business_key` VERBATIM when the caller
      threaded one (a delegation from inside a running instance knows the authoritative key);
      otherwise derived as `PAGTO-{tenant}-{ordem_pagamento_id}` (or, when the payment stems from
      an adjudicated CONTAS-001 account: `PAGTO-{tenant}-{numero_lote_tiss}-{prestador_id}`).
      The idempotent start consults the key before creating — avoids a DUPLICATED release
      (contract §Business key). GK-dossier finding 1a: the derivation is ordem-FIRST, so for an
      instance keyed with the CONTAS variant while an `ordem_pagamento_id` is ALSO in scope the
      derived key DIVERGES from the live one and the idempotent start would miss its own
      instance; the threaded engine key closes that duplicate-instance window.
      TENANT SCOPE (never trusted blindly): the threaded key is honored ONLY when it carries this
      state's own `PAGTO-{tenant}-` prefix — a planted/foreign key falls back to the derivation
      and can therefore never anchor another tenant's case (ADR-0004).
    - `adequacao_dossier`: `ADEQ-{tenant}-{regiao}-{especialidade}[-{ciclo}]` — the SAME cell
      format SP-OP-ADEQUACAO-001 itself uses; ANCHORS only (Andre never starts that process).
      Never a malformed PAGTO key for a non-payment flow. The ciclo segment enters only when
      PRESENT AND NON-BLANK, and is REFUSED (never dropped) when blank.
    - any other flow: no business key (nothing to start/anchor).

    M-8: both families are now composed by the SHARED strict composers in `andre/keys.py`, which
    `andre/delegation.py` also uses — the two sites used to diverge (this one stripped segments
    and DROPPED empty ones; delegation did neither), and the drop made two distinct adequacao
    cells collide on one key. Segment-dropping is gone: an empty required segment now raises.
    Both `_business_key` call sites in this module are pre-guarded by `receive` (which routes a
    case without the flow's minimum identifiers to conservative human review before reaching
    here), so a raise from these composers is a genuine programming error, never a routing path.
    """
    tenant = state.get("tenant_id", "")
    flow = _flow(state)
    if flow == "adequacao_dossier":
        ciclo = state.get("ciclo_avaliacao")
        return adequacao_business_key(
            tenant,
            state.get("regiao_saude", ""),
            state.get("especialidade", ""),
            # Blank/absent ciclo -> None: the 3-segment cell key. NEVER an empty 4th segment.
            ciclo if not is_blank(ciclo) else None,
        )
    if flow != "pagto_dossier":
        return ""
    return pagto_business_key(
        tenant,
        ordem_pagamento_id=state.get("ordem_pagamento_id", ""),
        numero_lote_tiss=state.get("numero_lote_tiss", ""),
        prestador_id=state.get("prestador_id", ""),
        business_key=state.get("engine_business_key", ""),
    )


# --- State sanitization (mandatory hardening — closes the caller-planted-output class) ---------

#: The ONLY fields a caller may legitimately seed on the initial state (flow selector + runtime
#: identifiers + contract inputs + worker-pre-resolved facts + the gather references). Everything
#: else in `AndreState` is OUTPUT-ONLY: produced exclusively by this graph's own nodes.
#: Single-sourced against `AndreState` by the partition-completeness regression test
#: (`test_output_field_partition_is_complete`) — a new state field MUST be classified into
#: exactly one of the two sets or that test fails, so the sanitization below can never silently
#: drift out of date.
_CALLER_INPUT_FIELDS: frozenset[str] = frozenset(
    {
        "flow",
        "tenant_id",
        "canal",
        "engine_business_key",
        "delegation_origin",
        "ordem_pagamento_id",
        "numero_lote_tiss",
        "prestador_id",
        "tipo_pagamento",
        "valor_pagamento_cents",
        "moeda",
        "competencia",
        "data_vencimento",
        "conta_origem_ref",
        "instrumento_pagamento",
        "dados_pagamento_validos",
        "lastro_confirmado",
        "dentro_teto_l2",
        "duplicidade_suspeita",
        "cohort_id",
        "features",
        "regiao_saude",
        "especialidade",
        "ciclo_avaliacao",
        "tipo_carater",
        "gap_adequacao",
        "roteamento_remediacao",
        "tempo_acesso_apurado_min",
        "distancia_apurada_km",
        "prestadores_disponiveis",
        "cobertura_geo_suficiente",
        "dados_geo_completos",
        "patient_summary_ref",
    }
)


def _output_field_resets() -> dict[str, Any]:
    """Fresh (never-shared) neutral defaults for EVERY output-only `AndreState` field — applied
    unconditionally at `receive` entry, on ALL of its return paths (module docstring
    §CALLER-PLANTED-OUTPUT SANITIZATION).

    An inbound turn's state may only carry INPUT fields (`_CALLER_INPUT_FIELDS`). Every field a
    NODE of this graph is supposed to fill is reset here first, so a caller-planted
    `error`/`route`/forged `faixa_valor`/forged `dmn_refs`/pre-cooked `dossier`/`process_ref` can
    never survive into routing, the dossier facts, or engine-bound process variables. Returns a
    FRESH dict per call — the mutable container values (`{}`/`[]`) must never be shared across
    turns.

    `route` resets to `"human_review"` (fail-safe: if any node were ever skipped, the conditional
    edge still lands on the human path); `assess` ALWAYS overwrites it from the real DMN chain /
    flow dispatch. `business_key` resets to `""` and is re-derived from the input identifiers on
    the happy path — on `receive`'s own missing-context guards it stays empty, which keeps
    `start_process`'s error short-circuit closed against a planted key.
    """
    return {
        # gather outputs
        "gathered": False,
        "actuarial_aggregate": {},
        "population_aggregate": {},
        "aggregate_dataset_refs": [],
        "egress_blocked": False,
        "gather_notes": [],
        # assess outputs (DMN results — only the DMNs, via assess, may fill these)
        "admissibilidade": None,
        "faixa_valor": None,
        "grupo_aprovador": "",
        "sla_aprovacao": "",
        "sla_alerta": "",
        "dmn_refs": {},
        "dmn_error": "",
        # auto_route/human_review outputs
        "dossier": {},
        # routing outputs
        "route": "human_review",
        "motivo_humano": None,
        "grupo_humano": "",
        # start_process outputs
        "process_started": False,
        "business_key": "",
        "process_ref": {},
        # terminal outputs
        "desfecho": "",
        "error": "",
    }


class AndreGraph:
    """Wires Andre's injected dependencies into a compilable `StateGraph[AndreState]`."""

    def __init__(
        self,
        *,
        inference: InferenceProvider,
        dmn: DmnTransport,
        cibseven: CibSevenTransport,
        audit_sink: AuditStartSink,
        fhir: PatientSummaryReader | None = None,
        population: PopulationFeatureClient | None = None,
        agent_version: str = "andre@v0",
    ) -> None:
        self._llm = inference
        self._dmn = dmn
        self._cibseven = cibseven
        # T-C2 fence: the durable, fail-closed ADR-0007 sink Andre's money-path process start
        # (SP-OP-PAGTO-001) audits BEFORE the engine effect. Required — never Optional.
        self._audit_sink = audit_sink
        self._model_id = getattr(inference, "model_id", None)
        self._fhir = fhir
        self._population = population
        self._agent_version = agent_version

    # -- Nodes ----------------------------------------------------------------------------------

    async def receive(self, state: AndreState) -> dict[str, Any]:
        """Turn start: task arrives (A2A `analytics.actuarial`/`analytics.population` from PAGTO/
        ADEQUACAO, or an autonomous analytics request). Idempotent.

        SANITIZATION FIRST (module docstring §CALLER-PLANTED-OUTPUT SANITIZATION): EVERY
        output-only field is reset (`_output_field_resets`) on ALL return paths, before anything
        else — an inbound `error`/`route`/forged DMN fact is a caller plant, never trusted. This
        node is the graph's SINGLE entry (`compile_graph` wires exactly one edge out of START,
        regression-tested), so the downstream early-bail guards can only ever observe an `error`
        this node itself set.

        Defense: never proceeds without the flow's minimum contract identifiers. Without them
        there is no idempotent/anchorable business key — routes to human by safety (fail-safe,
        never an adverse effect). An unrecognized `flow` is fail-neutral: conservative human
        review, NO payment business key, NO process (module docstring divergence #6).
        """
        sanitized = _output_field_resets()
        flow = _flow(state)
        if is_blank(state.get("tenant_id")):
            return {
                **sanitized,
                **self._route_human("ambiguidade", {}, _GRUPO_CONSERVADOR),
                "error": "contexto de runtime ausente (tenant_id)",
            }
        if flow == "population_analytics":
            if not state.get("cohort_id"):
                return {
                    **sanitized,
                    **self._route_human("ambiguidade", {}, _GRUPO_CONSERVADOR),
                    "error": "analytics sem cohort_id",
                }
            return dict(sanitized)  # no process to start/anchor — business_key stays ""
        if flow == "adequacao_dossier":
            # Needs the cell IDENTITY (regiao x especialidade) for a well-formed adequacao anchor
            # key. Without it there is no valid `ADEQ-...` -> fail-safe human (NEVER a malformed
            # payment key).
            # M-8: `is_blank` (not truthiness) — a WHITESPACE-ONLY identifier used to pass this
            # guard and then compose a degenerate/colliding anchor. The strict composer would now
            # raise on it, so the fail-safe routing must catch it FIRST.
            if is_blank(state.get("regiao_saude")) or is_blank(state.get("especialidade")):
                return {
                    **sanitized,
                    **self._route_human("ambiguidade", {}, _GRUPO_GESTAO_REDE),
                    "error": "adequacao sem regiao_saude/especialidade (sem chave de celula)",
                }
            return {**sanitized, "business_key": _business_key(state)}
        if flow == "pagto_dossier":
            # Needs the ordem (or lote+prestador) for the idempotent PAGTO business key.
            # M-8: `is_blank` (not truthiness) — same whitespace hole as the adequacao guard
            # above; a padded id used to slip through and mint `PAGTO-{t}- 123 `.
            has_key = not is_blank(state.get("ordem_pagamento_id")) or not (
                is_blank(state.get("numero_lote_tiss")) or is_blank(state.get("prestador_id"))
            )
            if not has_key:
                return {
                    **sanitized,
                    **self._route_human("analise_humana", {}, _GRUPO_CONSERVADOR),
                    "error": "pagamento sem ordem_pagamento_id nem lote+prestador (sem chave)",
                }
            return {**sanitized, "business_key": _business_key(state)}
        # Unrecognized flow — fail-neutral: conservative human review, never treated as payment.
        return {
            **sanitized,
            **self._route_human("ambiguidade", {}, _GRUPO_CONSERVADOR),
            "error": "fluxo de delegacao nao reconhecido (revisao humana conservadora)",
        }

    async def gather(self, state: AndreState) -> dict[str, Any]:
        """Gather k-anon AGGREGATES from the injected population client + best-effort FHIR probe.

        EGRESS CHOKEPOINT: aggregates come from the injected `PopulationFeatureClient` and pass
        the structural gate `_scrub_aggregate` (k-anon + no resolvable-PHI indication). A
        suspect aggregate is BLOCKED (not emitted) and marks `egress_blocked` -> `assess` routes
        to human (`phi_egress_risk`). The FHIR summary read (pagto flow only, per the agent.yaml
        tool note) is best-effort and its RAW content NEVER enters state/dossier — only a
        class-token gap note on failure. Failures NEVER block routing.
        """
        if state.get("error"):
            # Post-`receive`-sanitization a truthy `error` can ONLY have been set by `receive`'s
            # own guards — never by the caller. Re-assert the fail-safe route anyway (defense in
            # depth): this bail must NEVER be reachable with a non-human route.
            return {"route": "human_review"}

        notes: list[str] = []
        refs: list[str] = []
        egress_blocked = False
        actuarial: dict[str, Any] = {}
        population: dict[str, Any] = {}
        flow = _flow(state)
        cohort_id = state.get("cohort_id", "")

        if cohort_id and self._population is not None:
            features = state.get("features") or ["sinistro_agregado", "utilizacao", "exposicao_atuarial"]
            try:
                agg = await self._population.actuarial_risk(cohort_id, features=features)
                scrubbed = _scrub_aggregate(agg, notes)
                if scrubbed is None:
                    egress_blocked = True
                else:
                    actuarial = scrubbed
                    if agg.dataset_ref:
                        refs.append(agg.dataset_ref)
            except Exception:  # noqa: BLE001 — best-effort enrichment, never fatal.
                notes.append("risco atuarial agregado indisponivel (cliente de populacao falhou)")
            if flow == "population_analytics":
                try:
                    pagg = await self._population.population_metrics(cohort_id, features=features)
                    pscrubbed = _scrub_aggregate(pagg, notes)
                    if pscrubbed is None:
                        egress_blocked = True
                    else:
                        population = pscrubbed
                        if pagg.dataset_ref:
                            refs.append(pagg.dataset_ref)
                except Exception:  # noqa: BLE001 — best-effort enrichment, never fatal.
                    notes.append("metricas populacionais indisponiveis (cliente de populacao falhou)")
        elif cohort_id:
            notes.append(
                "cliente de populacao nao injetado (port WB.4 pendente — labeled boundary, ver "
                "docstring do modulo graph.py); dossie prossegue sem agregados de coorte."
            )

        # FHIR probe (pagto flow only; agent.yaml: mcp-fhir.read_patient "SO no caminho do
        # dossie de pagamento"). Best-effort; the raw summary NEVER enters state/dossier (egress
        # chokepoint) — a failure degrades to a class-token gap note.
        if flow == "pagto_dossier":
            if self._fhir is None:
                notes.append(
                    "leitor FHIR nao configurado para este build (labeled boundary — ver "
                    "docstring do modulo graph.py); dossie prossegue com fatos de worker."
                )
            else:
                summary_ref = state.get("patient_summary_ref", "")
                if summary_ref:
                    try:
                        await self._fhir.read_patient(summary_ref)
                    except Exception:  # noqa: BLE001 — best-effort enrichment, never fatal.
                        notes.append("resumo FHIR indisponivel (leitura best-effort falhou)")

        return {
            "gathered": True,
            "actuarial_aggregate": actuarial,
            "population_aggregate": population,
            "aggregate_dataset_refs": refs,
            "egress_blocked": egress_blocked,
            "gather_notes": notes,
        }

    async def assess(self, state: AndreState) -> dict[str, Any]:
        """Evaluate the deterministic DMNs (pagto flow) and decide ROUTING (neutral vs human).

        Dispatches by flow. ADR-0012: the DMN decides; the LLM reasons over the result (dossier).
        NO branch here releases/authorizes/pays or decides a price — only `auto_route` (neutral:
        DENTRO_TETO_L2 clerical / analytics ready) or `human_review` (fail-safe/instructive).

        CLOSED FAIL-SAFE: DMN unavailable, an out-of-allowlist DMN value, a PHI-egress risk or an
        unrecognized flow NEVER become an adverse outcome — always `human_review` (conservative
        highest tier).
        """
        if state.get("error"):
            # Same rationale as `gather`'s bail: only reachable via `receive`'s own guards
            # post-sanitization; re-asserts the fail-safe route (defense in depth).
            return {"route": "human_review"}
        # EGRESS: an aggregate blocked on a resolvable-PHI indication -> human (never egress by
        # omission). Applies to every flow (the gate precedes flow dispatch).
        if state.get("egress_blocked"):
            return {
                **self._route_human("phi_egress_risk", {}, _GRUPO_CONSERVADOR),
                "desfecho": "egresso_bloqueado",
            }
        flow = _flow(state)
        if flow == "population_analytics":
            # No payment => no adverse effect possible. Neutral routing: the analytics dossier is
            # ready over the k-anon aggregates (egress already gated in gather). No DMN applies.
            return {"route": "auto_route", "desfecho": "analytics_pronto"}
        if flow == "adequacao_dossier":
            # Andre does NOT re-evaluate `adequacao_gap`/`adequacao_remediation_routing` (already
            # resolved by SP-OP-ADEQUACAO-001's own DMNs BEFORE this hop), evaluates no pagto DMN
            # and starts no process. He only ASSEMBLES the factual remediation dossier and ALWAYS
            # routes it to the `gestao-rede` human (`UT_DecisaoFallback`) — no auto variant.
            return {
                **self._route_human("dossie_remediacao", {}, _GRUPO_GESTAO_REDE),
                "desfecho": "dossie_remediacao_humano",
            }
        if flow == "pagto_dossier":
            return await self._assess_pagto(state)
        # Unrecognized flow: NEVER the payment DMNs — fail-neutral conservative human review
        # (defense in depth; `receive` already routed it).
        return self._route_human("ambiguidade", {}, _GRUPO_CONSERVADOR)

    async def _assess_pagto(self, state: AndreState) -> dict[str, Any]:
        """pagto_dossier flow: admissibility -> alcada faixa (+ SLA on the human branch).

        The `pagto_alcada` DMN CLASSIFIES the value into a faixa and routes to the approver group
        — it NEVER releases. `DENTRO_TETO_L2` is the ONLY automatic clerical faixa (`auto_route`);
        every `ALCADA_*` faixa and the `ANALISE_HUMANA` catch-all require a human approver of
        matching tier (`human_review`). Duplicity precedence is DMN-decided (module docstring
        divergence #1); `pagto_sla` follows the BPMN's position (divergence #2).
        """
        dmn_refs: dict[str, str] = {}

        # 1) Admissibility (no release output). The deployed table's own FIRST rules encode the
        # duplicity/lastro/dados precedence — `duplicidade_suspeita` enters as an input, never as
        # a hand-coded Python fork (divergence #1).
        admis = await self._evaluate_dmn(
            DMN_PAGTO_ADMISSIBILITY,
            {
                "dados_pagamento_validos": bool(state.get("dados_pagamento_validos", False)),
                "lastro_confirmado": bool(state.get("lastro_confirmado", False)),
                "duplicidade_suspeita": bool(state.get("duplicidade_suspeita", False)),
            },
        )
        if admis.get("error"):
            return self._route_human(
                "dmn_indisponivel", dmn_refs, _GRUPO_CONSERVADOR, dmn_error=str(admis["error"])
            )
        admissibilidade_raw = str(admis["row"].get("roteamento", ""))
        dmn_refs[DMN_PAGTO_ADMISSIBILITY] = admis["ref"]
        # CLOSED FAIL-SAFE: an out-of-allowlist value -> conservative comite (never auto).
        if admissibilidade_raw not in _ADMISSIBILIDADE_ALLOW:
            return self._route_human("ambiguidade", dmn_refs, _GRUPO_CONSERVADOR)
        admissibilidade = cast(Admissibilidade, admissibilidade_raw)
        base: dict[str, Any] = {"admissibilidade": admissibilidade, "dmn_refs": dmn_refs}

        if admissibilidade == "PENDENTE_DADOS":
            # BPMN: non-admissible branch -> UT_AnaliseAdmissibilidade (coordenacao-financeira,
            # divergence #3); terminates BEFORE pagto_alcada/pagto_sla.
            return {**base, **self._route_human("pendencia_dados", dmn_refs, _GRUPO_ADMISSIBILIDADE)}
        if admissibilidade == "ANALISE_HUMANA":
            # The bounded class token is refined from the state's own informative duplicity
            # signal (divergence #1) — the ROUTING was decided by the DMN either way.
            motivo: MotivoHumano = (
                "duplicidade_suspeita" if bool(state.get("duplicidade_suspeita", False)) else "analise_humana"
            )
            return {**base, **self._route_human(motivo, dmn_refs, _GRUPO_ADMISSIBILIDADE)}

        # 2) SEGUE_ROTEAMENTO -> alcada ladder (classifies faixa + approver group; NEVER
        # releases). `dentro_teto_l2` is the worker-pre-resolved fact, passed through verbatim.
        alcada = await self._evaluate_dmn(
            DMN_PAGTO_ALCADA,
            {
                "valor_pagamento_cents": int(state.get("valor_pagamento_cents", 0)),
                "tipo_pagamento": str(state.get("tipo_pagamento", "")),
                "dentro_teto_l2": bool(state.get("dentro_teto_l2", False)),
            },
        )
        if alcada.get("error"):
            return {
                **base,
                **self._route_human(
                    "dmn_indisponivel", dmn_refs, _GRUPO_CONSERVADOR, dmn_error=str(alcada["error"])
                ),
            }
        faixa_raw = str(alcada["row"].get("faixa_valor", ""))
        dmn_refs[DMN_PAGTO_ALCADA] = alcada["ref"]
        # CLOSED FAIL-SAFE: a faixa outside the allowlist -> conservative comite (NEVER
        # auto-releases).
        if faixa_raw not in _FAIXA_VALOR_ALLOW:
            return {**base, **self._route_human("ambiguidade", dmn_refs, _GRUPO_CONSERVADOR)}
        faixa = cast(FaixaValor, faixa_raw)
        grupo_dmn = str(alcada["row"].get("grupo_aprovador", ""))
        base["faixa_valor"] = faixa
        base["grupo_aprovador"] = grupo_dmn  # DMN output echoed verbatim (divergence #4)

        # CLOSED FAIL-SAFE (L1 hard): ONLY `DENTRO_TETO_L2` — with the worker-pre-resolved
        # `dentro_teto_l2` fact confirming it — follows the automatic clerical path (analogous to
        # auth_auto_approval). ANY `ALCADA_*` faixa / `ANALISE_HUMANA` / inconsistency -> human
        # approval with tier-match. Nothing ambiguous falls through to automatic by omission.
        if faixa == "DENTRO_TETO_L2":
            if bool(state.get("dentro_teto_l2", False)):
                return {**base, "route": "auto_route", "desfecho": "dossie_pronto_clerical"}
            # Defense in depth: the real table only emits DENTRO_TETO_L2 when the flag is true —
            # a clerical faixa WITHOUT the worker fact is inconsistent -> conservative comite.
            return {**base, **self._route_human("ambiguidade", dmn_refs, _GRUPO_CONSERVADOR)}

        # 3) Above-teto human branch: SLA with the REAL resolved faixa (BPMN `BRT_PagtoSla`,
        # divergence #2). Informative only — failure NEVER blocks routing (mirrors auth_sla).
        sla = await self._evaluate_dmn(
            DMN_PAGTO_SLA,
            {"faixa_valor": str(faixa), "tipo_pagamento": str(state.get("tipo_pagamento", ""))},
        )
        if not sla.get("error"):
            sla_row = sla["row"]
            base["sla_aprovacao"] = str(sla_row.get("sla_aprovacao", ""))
            base["sla_alerta"] = str(sla_row.get("sla_alerta", ""))
            dmn_refs[DMN_PAGTO_SLA] = sla["ref"]

        grupo = self._resolve_grupo(faixa, grupo_dmn)
        motivo_alcada: MotivoHumano = "analise_humana" if faixa == "ANALISE_HUMANA" else "aprovacao_alcada"
        return {**base, **self._route_human(motivo_alcada, dmn_refs, grupo)}

    async def auto_route(self, state: AndreState) -> dict[str, Any]:
        """NEUTRAL routing (payment DENTRO_TETO_L2 clerical / analytics dossier ready).

        GUARDRAIL: this path NEVER releases/authorizes/pays nor decides a price. It only
        assembles the risk dossier; in the payment flow, `start_process` (next node) opens/
        anchors the instance, whose clerical L2 path (`release_low_value_payment` — below the
        ceiling, no alcada adverse effect) or human User Task is where anything substantive
        happens. No DMN/branch of this graph releases a payment above alcada (L1 hard).
        """
        return {"dossier": await self._build_dossier(state, route="auto_route")}

    async def human_review(self, state: AndreState) -> dict[str, Any]:
        """Prepares the human approver's/gestao-rede's risk dossier and marks the human route.

        This is the route for ANY faixa above the L2 ceiling / ambiguity / pending data /
        duplicity signal / egress risk / DMN unavailability, and the ONLY route in
        `adequacao_dossier`. NONE of these is a release/price: the high-value release is born
        SOLELY in `UT_AprovacaoAlcada`/`UT_AprovacaoComite` with `decisao_pagamento=APROVAR` and
        tier-match. Andre instructs the risk; the human approver decides.
        """
        dossier = await self._build_dossier(state, route="human_review")
        desfecho = state.get("desfecho") or self._human_desfecho(state)
        return {"dossier": dossier, "desfecho": desfecho}

    async def start_process(self, state: AndreState) -> dict[str, Any]:
        """Starts (idempotently) SP-OP-PAGTO-001 with the contract's variables.

        ONLY in the `pagto_dossier` flow. NO-OP for `population_analytics` (no process exists for
        autonomous analytics), for `adequacao_dossier` (SP-OP-ADEQUACAO-001 is already running
        when the delegation arrives — starting a second instance would duplicate the cell; the
        `ADEQ-` business key only anchors the dossier) and for any unrecognized flow.

        TWO ANCHORS keep this method from ever creating a SECOND live SP-OP-PAGTO-001 instance
        (GK-dossier finding 1 — the duplicate `UT_AprovacaoAlcada` approval/release path):

        1. ORIGIN NO-OP (belt): a delegation whose `delegation_origin` is `ORIGIN_PAGTO_WORKER`
           was originated by `operadora.pagto.prepare_approval_dossier` from INSIDE an
           already-running instance (`ST_PrepareApprovalDossier`, incl. the GAP-PAGTO-5
           `seguir_analise` re-entry) — it must NEVER start one, exactly like the flow-scoped
           `adequacao_dossier` no-op above; the key only ANCHORS the dossier. `start_process`
           stays fully functional for every OTHER origin of the `pagto_dossier` flow (an
           autonomous/foreign originator legitimately opens the case).
        2. ENGINE-KEY IDEMPOTENCY (braces): the business key prefers the ENGINE's own threaded
           `engine_business_key` over the ordem-first derivation (`_business_key`), so the
           `start_process_idempotent` lookup consults the SAME key the live instance carries
           (the CONTAS variant `PAGTO-{tenant}-{lote}-{prestador}` included) instead of a
           diverging one that would miss it and start a duplicate.

        Idempotent business key (the start consults the key before creating — a resend returns
        the active instance untouched; avoids a DUPLICATED payment release). A start failure
        never loses the case: it records the error and keeps the routing. NEVER releases a
        payment "on the side".

        DECLARED AUTHORITY: NONE (gap `ANDRE-PROCESS-KEYS`, owner decision R-036, 2026-09-04).
        `spec/agents/andre/agent.yaml` no longer declares `mcp-cibseven.start_process` nor the
        `start_compliance_process` autonomy action, and it declares no `process_keys` — so the
        effect-PEP's decision core (`decide_effect`, L-1) returns DENY for this seam for `andre`
        (`AgentCapabilities.allows_tool` / `allows_process_key`, pinned in
        `tests/unit/gateway/test_andre_least_privilege.py`). That verdict is ADVISORY today, not
        a runtime fence: no seam wires `start_process_instance`
        (`gateway/seams/cibseven.py`, "the one deliberate hole, disclosed"), the
        `inicio_processo_regulatorio` start surface is `choked: false` by decision
        (`spec/policies/autonomy/action-approvals.yaml`), and that class's `enforcement` is
        `shadow` (`modo: shadow`, `approved_count=0`). Narrowing the manifest is what makes the
        DENY the right answer WHEN that surface is eventually choked. This node is KEPT because
        no live originator reaches it today (Anchor 1 above returns `start_skipped` for the only
        wired origin, and `delegation.py` maps only the no-op `adequacao-worker`), so removing it
        is not what the decision ordered; if a foreign originator ever appears, the grant returns
        as an explicit line of YAML under owner review — never by omission.
        """
        if _flow(state) != "pagto_dossier":
            return {}
        if state.get("delegation_origin") == ORIGIN_PAGTO_WORKER:
            # Anchor 1 — the instance is ALREADY running; the dossier only instructs its UT.
            return {
                "process_started": False,
                "process_ref": {"start_skipped": "delegation_from_running_instance"},
            }
        if state.get("error") and not state.get("business_key"):
            return {"process_started": False}

        business_key = state.get("business_key") or _business_key(state)
        variables = self._contract_variables(state)
        provenance = AgentDecisionProvenance(
            agent_id="andre",
            agent_version=self._agent_version,
            tenant_id=state.get("tenant_id", ""),
            model_id=self._model_id,
            prompt_version=SYSTEM_PROMPT_VERSION,
            # Curated PHI-safe routing tokens (design §3.3) — bounded enums only, never PHI.
            decision_basis={
                "route": state.get("route", ""),
                "faixa_valor": str(state.get("faixa_valor") or ""),
                "grupo_aprovador": str(state.get("grupo_aprovador") or ""),
                "motivo_humano": state.get("motivo_humano") or "",
            },
        )
        try:
            instance = await start_process_idempotent(
                self._cibseven,
                process_key=PROCESS_KEY_PAGTO,
                business_key=business_key,
                variables=variables,
                audit_sink=self._audit_sink,
                provenance=provenance,
            )
        except CibSevenError:
            return {
                "process_started": False,
                "business_key": business_key,
                "error": ERROR_START_PROCESS_ENGINE_UNAVAILABLE,
            }
        # F3 BLOCKER-1 — HONEST REPORTING. `process_started` used to be a hard-coded `True` for
        # every non-raising return, including the strict dedup gate's "I refused to start this".
        # It is now derived from the chokepoint's own typed verdict: True iff a live instance
        # exists for this key because of, or at the time of, this call (STARTED / ALREADY_ACTIVE);
        # False for ALREADY_COMPLETED, where the order was settled by an EARLIER instance and this
        # call caused nothing. `start_outcome` (a bounded class token, PHI-safe) travels with it so
        # a consumer branches on the outcome instead of re-deriving it from `already_existed`,
        # which cannot tell "live" from "already finished". The undecidable case does not reach
        # here at all — `StartClaimWithoutInstanceError` is not a `CibSevenError` and propagates.
        return {
            "process_started": instance.start_outcome is not StartOutcome.ALREADY_COMPLETED,
            "business_key": business_key,
            "process_ref": {
                "instance_id": instance.instance_id,
                "state": instance.state,
                "already_existed": instance.already_existed,
                "start_outcome": instance.start_outcome.value,
            },
        }

    async def finalize(self, state: AndreState) -> dict[str, Any]:
        """Terminal node — no further computation; `desfecho` was already set upstream.

        No episodic memory write here (labeled boundary, module docstring divergence #5 — same
        rationale as Rafael's/Helena's graphs). NEVER releases a payment nor communicates an
        approval — that is the process's/human approver's alone.
        """
        return {}

    # -- Conditional routing --------------------------------------------------------------------

    @staticmethod
    def _route(state: AndreState) -> str:
        # FAIL-SAFE: on absence/doubt, ALWAYS human (never auto_route by omission).
        return "auto_route" if state.get("route") == "auto_route" else "human_review"

    @staticmethod
    def _resolve_grupo(faixa: str, grupo_dmn: str) -> str:
        """Value-driven approver group (CLOSED set). DMN output wins when it is a known group;
        else the faixa->group map; catch-all always -> comite (highest tier). NEVER a group
        outside the closed set (ADR-0018 part 3)."""
        if grupo_dmn in _GRUPOS_VALIDOS:
            return grupo_dmn
        return _FAIXA_TO_GRUPO.get(faixa, _GRUPO_CONSERVADOR)

    @staticmethod
    def _human_desfecho(state: AndreState) -> str:
        motivo = state.get("motivo_humano")
        if motivo == "phi_egress_risk":
            return "egresso_bloqueado"
        if motivo == "pendencia_dados":
            return "pagamento_pendente_dados"
        if motivo == "dossie_remediacao":
            return "dossie_remediacao_humano"
        return "dossie_aprovacao_humana"

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

    # -- DMN (ADR-0012/0028): the engine evaluates; the LLM never decides ----------------------

    async def _evaluate_dmn(self, table: str, dmn_input: dict[str, Any]) -> dict[str, Any]:
        try:
            rows, version = await self._dmn.evaluate(table, dmn_input)
            row = first_row(rows, table, dmn_input)
        except (DmnEvaluationError, DmnNoResultError) as exc:
            return {"error": f"DMN `{table}` indisponivel: {exc}"}
        # See `rafael/graph.py::_evaluate_dmn` for why this cites the decision-definition id
        # (ADR-0028 §2) rather than a rule id the engine's evaluate response never returns.
        return {"row": row, "ref": f"{table}#{version.id}"}

    # -- Contract variables + dossier assembly (ADR-0007 audit provenance) ---------------------

    def _contract_variables(self, state: AndreState) -> dict[str, Any]:
        """Assembles SP-OP-PAGTO-001's start variables (exactly the contract's input set +
        the contract's declared agent-provenance variables, §Variaveis de proveniencia).

        Includes Andre's risk dossier as `dossie_andre` (instruction) and `motivo_encaminhamento`
        /`grupo_destino` when routed to human — NEVER a payment release nor a price decision.
        `valor_pagamento_cents` travels as INTEGER-CENTAVOS; `dentro_teto_l2` is the
        worker-pre-resolved fact passed through verbatim (consumed, never computed).
        `data_vencimento` is seeded per GAP-PAGTO-7 (contract :72). Only called for
        `pagto_dossier` (`start_process` no-ops before this for every other flow).
        """
        variables: dict[str, Any] = {
            "tenant_id": state.get("tenant_id", ""),
            "ordem_pagamento_id": state.get("ordem_pagamento_id", ""),
            "prestador_id": state.get("prestador_id", ""),
            "tipo_pagamento": str(state.get("tipo_pagamento", "")),
            "valor_pagamento_cents": int(state.get("valor_pagamento_cents", 0)),
            "moeda": str(state.get("moeda", "BRL")),
            "competencia": state.get("competencia", ""),
            "data_vencimento": state.get("data_vencimento", ""),  # deadline anchor (GAP-PAGTO-7)
            "conta_origem_ref": str(state.get("conta_origem_ref", "")),  # token, never raw account
            "instrumento_pagamento": str(state.get("instrumento_pagamento", "")),
            "dados_pagamento_validos": bool(state.get("dados_pagamento_validos", False)),
            "lastro_confirmado": bool(state.get("lastro_confirmado", False)),
            "dentro_teto_l2": bool(state.get("dentro_teto_l2", False)),  # pure pass-through
            "duplicidade_suspeita": bool(state.get("duplicidade_suspeita", False)),
            # Agent provenance (ADR-0007/0015 — contract §Variaveis de proveniencia).
            "source_agent_id": "andre",
            "source_agent_version": self._agent_version,
            # Risk dossier + Andre's routing (instruction, NEVER a release/price).
            "dossie_andre": state.get("dossier") or {},
            "andre_route": state.get("route", "human_review"),
            # Faixa classified by the DMN (NEVER a release) + value-driven approver group.
            # `or`-based (not `.get(key, default)`): post-sanitization these keys EXIST with
            # value `None`/`""` until a node sets them — the default must still apply then.
            "faixa_valor": str(state.get("faixa_valor") or ""),
            "grupo_aprovador": str(state.get("grupo_aprovador") or ""),
        }
        if state.get("numero_lote_tiss"):
            variables["numero_lote_tiss"] = state["numero_lote_tiss"]
        if state.get("route") == "human_review":
            variables["motivo_encaminhamento"] = state.get("motivo_humano") or "outro"
            variables["grupo_destino"] = state.get("grupo_humano") or _GRUPO_CONSERVADOR
        # Auditable decision references (ADR-0007/0012). Note: `dmn_error` (raw transport error
        # text, potentially engine-echoed) is DELIBERATELY never included here — only the bounded
        # class token in `motivo_encaminhamento` reaches engine-bound variables (engine-variable
        # hygiene, mandatory hardening item 4).
        dmn_refs = state.get("dmn_refs")
        if dmn_refs:
            variables["dmn_decision_refs"] = dmn_refs
        # Aggregated dataset pointers (k-anon) — NEVER resolvable PHI (egress chokepoint;
        # contract §Variaveis de proveniencia `aggregate_dataset_refs`).
        refs = state.get("aggregate_dataset_refs")
        if refs:
            variables["aggregate_dataset_refs"] = refs
        return variables

    async def _build_dossier(self, state: AndreState, *, route: Route) -> dict[str, Any]:
        """Assembles the actuarial/population risk dossier. The LLM reasons over the AGGREGATES
        and DMN results; it never decides. Failure never blocks the route — falls back to a
        minimal deterministic dossier with an empty narrative (mirrors Rafael/Helena).

        The `decisao_pagamento`/`preco_recomendado`/`fhir_patient_id` fields are ALWAYS None —
        structural guardrails making explicit that the decision is the human approver's and that
        no resolvable patient id ever leaves this graph (a dossier carrying a value there would
        be a detectable bug)."""
        flow = _flow(state)
        facts = self._facts(state)
        motivo_humano = state.get("motivo_humano") if route == "human_review" else None
        grupo_humano = state.get("grupo_humano") if route == "human_review" else None
        prompt = (
            f"{dossier_prompt()}\n\nflow={flow} route={route} motivo_humano={motivo_humano}\n"
            f"fatos_agregados={facts}"
        )
        try:
            narrativa = await self._llm.generate(
                prompt, phi=True, agent_id="andre", tenant_id=state.get("tenant_id", "")
            )
        except Exception:  # noqa: BLE001 — LLM failure never blocks the human/auto route.
            narrativa = ""

        return {
            "prompt_version": DOSSIER_PROMPT_VERSION,
            "flow": flow,
            "route": route,
            "motivo_humano": motivo_humano,
            "grupo_humano": grupo_humano,
            "fatos_agregados": facts,
            "dmn_decision_refs": state.get("dmn_refs", {}),
            "aggregate_dataset_refs": state.get("aggregate_dataset_refs", []),
            "narrativa": narrativa,
            # STRUCTURAL GUARDRAILS (L1 hard + egress): the dossier NEVER carries a payment/price
            # decision nor a resolvable patient id. Always None here, by construction.
            "decisao_pagamento": None,  # APROVAR/RECUSAR: SOLELY the human User Task (tier-match)
            "preco_recomendado": None,  # pricing: NEVER the agent (false_pricing_decision_rate==0)
            "fhir_patient_id": None,  # egress chokepoint: NEVER a resolvable patient id
        }

    @staticmethod
    def _facts(state: AndreState) -> dict[str, Any]:
        """AGGREGATED facts for the dossier. NEVER resolvable PHI — only k-anon aggregates,
        refs, worker-pre-resolved facts and DMN results. In the `adequacao_dossier` flow includes
        the `adequacao` block with the cell's already-resolved facts (network granularity/
        booleans/numerics, never beneficiary PHI — ADR-0006)."""
        facts: dict[str, Any] = {
            "flow": _flow(state),
            "ordem_pagamento_id": state.get("ordem_pagamento_id"),
            "tipo_pagamento": state.get("tipo_pagamento"),
            "competencia": state.get("competencia"),
            "data_vencimento": state.get("data_vencimento"),
            "instrumento_pagamento": state.get("instrumento_pagamento"),
            "faixa_valor": state.get("faixa_valor"),
            "grupo_aprovador": state.get("grupo_aprovador"),
            "admissibilidade": state.get("admissibilidade"),
            "dados_pagamento_validos": state.get("dados_pagamento_validos"),
            "lastro_confirmado": state.get("lastro_confirmado"),
            "dentro_teto_l2": state.get("dentro_teto_l2"),  # pass-through worker fact
            "duplicidade_suspeita": state.get("duplicidade_suspeita"),
            # K-anon aggregates (NEVER per-individual) + dataset pointers.
            "cohort_id": state.get("cohort_id"),
            "actuarial_aggregate": state.get("actuarial_aggregate", {}),
            "population_aggregate": state.get("population_aggregate", {}),
            "aggregate_dataset_refs": state.get("aggregate_dataset_refs", []),
            "dmn_refs": state.get("dmn_refs", {}),
            "sla_aprovacao": state.get("sla_aprovacao"),
            "lacunas_enriquecimento": state.get("gather_notes", []),
        }
        if _flow(state) == "adequacao_dossier":
            facts["adequacao"] = {
                "regiao_saude": state.get("regiao_saude"),
                "especialidade": state.get("especialidade"),
                "ciclo_avaliacao": state.get("ciclo_avaliacao"),
                "tipo_carater": state.get("tipo_carater"),
                "gap_adequacao": state.get("gap_adequacao"),
                "roteamento_remediacao": state.get("roteamento_remediacao"),
                "tempo_acesso_apurado_min": state.get("tempo_acesso_apurado_min"),
                "distancia_apurada_km": state.get("distancia_apurada_km"),
                "prestadores_disponiveis": state.get("prestadores_disponiveis"),
                "cobertura_geo_suficiente": state.get("cobertura_geo_suficiente"),
                "dados_geo_completos": state.get("dados_geo_completos"),
            }
        return facts

    # -- Graph assembly -------------------------------------------------------------------------

    def compile_graph(self) -> StateGraph[AndreState]:
        g: StateGraph[AndreState] = StateGraph(AndreState)
        g.add_node("receive", self.receive)
        g.add_node("gather", self.gather)
        g.add_node("assess", self.assess)
        g.add_node("auto_route", self.auto_route)
        g.add_node("human_review", self.human_review)
        g.add_node("start_process", self.start_process)
        g.add_node("finalize", self.finalize)

        g.add_edge(START, "receive")
        g.add_edge("receive", "gather")
        g.add_edge("gather", "assess")
        g.add_conditional_edges(
            "assess", self._route, {"auto_route": "auto_route", "human_review": "human_review"}
        )
        g.add_edge("auto_route", "start_process")
        g.add_edge("human_review", "start_process")
        g.add_edge("start_process", "finalize")
        g.add_edge("finalize", END)
        return g


def _scrub_aggregate(agg: CohortAggregate, notes: list[str]) -> dict[str, Any] | None:
    """STRUCTURAL egress gate: accept the aggregate ONLY if k-anon and without resolvable-PHI
    indication.

    Andre is the egress chokepoint (ADR-0006/0019). Even though the injected client's type
    (`CohortAggregate`) has no patient-id field, this checks in depth: `has_resolvable_phi()`
    (defense against a `dataset_ref`/`cohort_id` with a resolvable prefix) and
    `k_anonymity >= 1`. On failure the aggregate is NOT emitted: a CLASS-TOKEN suppression note
    is recorded (never the suspect value itself — hardening beyond the donor) and None is
    returned — the caller marks `egress_blocked` and `assess` routes to human
    (`phi_egress_risk`). Never egresses PHI by omission.
    """
    if agg.has_resolvable_phi():
        notes.append("agregado bloqueado: indicio de PHI resolvivel (egresso negado)")
        return None
    if agg.k_anonymity < 1:
        notes.append("agregado bloqueado: k-anonimato ausente (k<1)")
        return None
    if agg.suppressed:
        notes.append(f"celulas suprimidas por k-anon: {list(agg.suppressed)}")
    return {
        "cohort_id": agg.cohort_id,
        "dataset_ref": agg.dataset_ref,  # opaque pointer — never PHI
        "metrics": dict(agg.metrics),  # aggregates — never per-individual
        "cohort_size": agg.cohort_size,
        "k_anonymity": agg.k_anonymity,
        "suppressed": list(agg.suppressed),
    }


def build(config: dict[str, Any] | None = None) -> StateGraph[AndreState]:
    """Contract `_template/graph.py:build`, resolved by `AgentLoader`/`runtime.harness.Harness`.

    `config` MUST contain `inference` (ADR-0009), `dmn` (ADR-0028/T1.5), `cibseven`
    (ADR-0001/T1.11). `fhir` and `population` are OPTIONAL (module docstring's labeled
    boundaries) — their absence never fails the build, only degrades `gather` to disclosed gap
    notes.

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
            f"Andre build(config) is missing required dependencies: {missing} "
            "(ADR-0001/0007/0009/0028 — inference/dmn/cibseven/audit_sink must all be injected; "
            "audit_sink is the T-C2 fence — no money-path process start without a durable sink)"
        )
    agent_version = str(cfg.get("agent_version", "andre@v0"))
    return AndreGraph(
        inference=cast(InferenceProvider, inference),
        dmn=cast(DmnTransport, dmn),
        cibseven=cast(CibSevenTransport, cibseven),
        audit_sink=cast(AuditStartSink, audit_sink),
        fhir=cast("PatientSummaryReader | None", cfg.get("fhir")),
        population=cast("PopulationFeatureClient | None", cfg.get("population")),
        agent_version=agent_version,
    ).compile_graph()


# Prompt versions exposed for audit/eval gating (ADR-0007/0009).
PROMPT_VERSIONS: dict[str, str] = {
    "system": SYSTEM_PROMPT_VERSION,
    "dossier": DOSSIER_PROMPT_VERSION,
}

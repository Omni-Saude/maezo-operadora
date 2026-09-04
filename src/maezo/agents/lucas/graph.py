"""Lucas Ferreira — Atendimento e Cobranca ao Beneficiario Agent (Phase 2, T1.12).

Journey map (mirrors the v1 donor's structure, READ-ONLY reference `Maezo-Healthcare-Plan
src/maezo/agents/lucas/graph.py`, adapted to v2's flatter seam set — same rationale as
`agents/helena/graph.py`'s and `agents/rafael/graph.py`'s module docstrings; SOURCE OF TRUTH for
any donor/spec disagreement is v2's `spec/agents/lucas/agent.yaml` + `spec/processes/`):

    receive -> gather -> assess -> {respond_member | escalate_human} -> start_process -> complete

ONE agent, THREE journeys — distinguished by `intencao` in state, NEVER by a merit Lucas decides:

  J1 (cobranca_info): boleto/2a via/vencimento doubt -> `assess` evaluates
     `lucas_billing_admissibility` -> `respond_member` drafts/sends an informational WhatsApp
     message. No adverse outcome ever originates here.

  J2 (confirmacao_pagamento): "was this payment conciliated?" -> Lucas READS the CNAB
     conciliation status PRE-RESOLVED by a worker upstream (`status_conciliado`,
     `ciclos_sem_conciliacao`) — he NEVER computes or invents it (ADR-0012) — and reports what
     conciliation says via `lucas_billing_admissibility`. A non-conciliated / inadimplencia
     signal routes to `escalate_human`.

  J3 (inadimplencia | cancelamento | contestacao_cobranca | pedido de cancelamento sinalizado):
     ALWAYS escalates to a human (SP-OP-ESCALATION-001; SP-OP-CANCEL-001 is where the human
     decides RESCINDIR/MANTER/SUSPENDER). Lucas NEVER suspends, cancels, or communicates any
     adverse outcome himself — this journey never even reaches the billing DMN.

`assess` ALWAYS evaluates the deterministic DMN tables (ADR-0012: `lucas_billing_admissibility`
decides RESPONDER/LEMBRETE/ESCALAR_HUMANO; `lucas_escalation_routing` decides which human GROUP a
case suggests, never an adverse outcome) — the LLM never decides a rule, only drafts prose over
an already-decided fact. Both tables are evaluated ENGINE-SIDE via `DmnTransport`
(`tools/workers/dmn_transport.py`, ADR-0028) — this module never re-implements a decision table
in Python.

L0 HARD INVARIANT (`spec/processes/dmn/lucas_billing_admissibility.dmn` +
`lucas_escalation_routing.dmn`'s own `<description>`, CI-enforced by
`test_route_type_admits_no_adverse_variant` in `tests/unit/agents/test_lucas.py`): `Route` admits
ONLY `{"respond_member", "escalate_human"}` — no cancel/suspend/deny/rescind variant exists in
the type. `AdmissibilidadeCobranca` admits ONLY `{"RESPONDER", "LEMBRETE", "ESCALAR_HUMANO"}` —
no COBRAR/SUSPENDER/CANCELAR/NEGAR output exists. `RoteamentoEscalacao` admits ONLY
`{"ATENDIMENTO_HUMANO", "COBRANCA_HUMANO", "CONTRATOS_HUMANO"}` — every destination is a HUMAN
GROUP, never an adverse outcome. The dossier's `decisao_cancelamento` field is ALWAYS `None`
(`_build_dossier`) — a guardrail making it explicit the decision belongs to the human, never to
this code (mirrors Rafael's `decisao_cobertura` guardrail).

FAIL-SAFE FECHADO (not fail-OPEN): any DMN unavailable, any DMN output outside its recognized
allowlist, or an unknown/missing `intencao` routes to `escalate_human` — NEVER a silent default
or a "no adverse signal" happy path. `receive`'s `intencao` gate is Lucas's "classification"
fail-closed guard (mirrors Helena's `_classify_llm` fail-closed discipline, T1.11 R1 cycle-1):
an unrecognized `intencao` is treated as a CLASSIFY FAILURE, never silently read as J1.

ENGINE-VARIABLE HYGIENE (mirrors Helena's T1.11 R1 cycle-2 fix): every failure reason
(`motivo_humano`, `dmn_error` prefix) is a bounded CLASS TOKEN — `ambiguidade`,
`dmn_indisponivel`, `falha_tecnica`, `inadimplencia_detectada`, `pedido_cancelamento`,
`contestacao_cobranca` — never a raw DMN-returned value, never a repr(), never a truncation of
untrusted data. A DMN `roteamento` value outside its allowlist is NEVER echoed into any
engine-bound variable or log — only the class token `ambiguidade` is recorded.

R1 CYCLE-1 (verifier REVISE — two real defects found in this graph's first revision, fixed
here; disclosed, not hidden):
- F1 (fail-OPEN case loss): a CALLER-planted `error` short-circuited `gather`/`assess` (no
  `route` stamp); `_route`'s conservative default still ran `escalate_human` (dossier built,
  beneficiary ACK SENT), but the old `start_process` gate (`route != "escalate_human"` -> skip)
  then silently dropped the start — the beneficiary was promised a human while ZERO engine
  instances existed. Fixed both ways: `escalate_human` stamps `route="escalate_human"`
  authoritatively in its own output (F1a), and `start_process` fails CLOSED — only an explicit
  `route == "respond_member"` skips the start (F1b).
- F2 (caller-planted output-field passthrough): on skip-assess shortcuts, OUTPUT-ONLY state
  fields (dmn_refs, motivo_*, severidade, grupo_humano, dossier, ...) planted by the caller
  flowed verbatim into SP-OP-ESCALATION-001 engine variables (verifier's live probe: a planted
  `dmn_refs` entry reached `dmn_decision_refs` via the `ambiguidade` path). Fixed at `receive`:
  `_OUTPUT_FIELDS_RESET` resets every output-only field on entry (plus `_escalate_min`'s
  explicit `dmn_refs` clear) — same defect class and fix shape as the sibling agents' cycles.

INPUT-BOUNDARY GATE (T1.11 layer 1 — CC-14/LUC-09a/RAF-13; the F2 reset above is layer 2). The
reset is a HAND-ENUMERATED list, so a `LucasState` field added later would be silently un-reset
AND silently caller-settable — the drift `spec/agents/lucas/agent.yaml` recorded as the open
security prerequisite for any inbound channel (gap 11.7). Layer 1 closes it: `_CALLER_INPUT_FIELDS`
declares the complete INPUT half, an import-time guard REFUSES TO LOAD THE MODULE if any state key
is unclassified (or double-classified), and `new_lucas_state` (strict, raises `ValueError` naming
the keys) / `gate_inbound_state` (lenient, drops + logs key NAMES only) are the two construction
seams. WHAT THIS DOES NOT DO: it does not give Lucas an inbound channel. Gap 11.7 stays open —
`accepted_task_types` is still `[]` and a beneficiary reply still arrives, if at all, through
Helena's single webhook without billing context. The gate is the ORDERED PREREQUISITE, built so
the seam is gated on the day it lands (CC-02), which is the posture `agents/rafael/graph.py`
already took for its own absent delegation seam.

PHI discipline: every LLM call in this module passes `phi=True` (ADR-0006/ADR-0017/T1.7) — the
one PHI-tagged content boundary is state derived from a beneficiary's billing case, treated the
same as Helena's/Rafael's PHI-tagged content.

DIVERGENCES FROM THE v1 DONOR (disclosed, not hidden — `spec/` wins per this task's charter):
- No `lucas_sla` DMN evaluation: `spec/processes/dmn/` has no `lucas_sla` table (only
  `lucas_billing_admissibility` and `lucas_escalation_routing` exist under `spec/processes/dmn/`,
  confirmed against `spec/processes/dmn/orphans-allowlist.yaml`). The donor's SLA annotation step
  is dropped entirely rather than fabricated.
- No `resume_ack`/GAP-XHITL-4B retomada node: v2 has no `resume_driver`/`RESUME_STATE_BUILDERS`
  infrastructure yet (Helena's graph does not implement this either — same labeled boundary,
  "no multi-turn conversation checkpointing across separate webhook deliveries").
- Model-tier routing (`task_default`/`reasoning`) — DISCLOSURE CORRECTED, AF-12 (2026-09-03).
  WAS: "v2's `InferenceProvider.generate(prompt, phi=True)` has no `task_kind` parameter
  (ADR-0009's tiering is not wired to agents yet)". The parameter now exists and this graph
  passes it: `_build_message`/`_build_escalation_ack` are `task_default` (phrasing already-known
  facts), `_build_dossier` is `reasoning` (it narrates over the assembled facts for a human).
  WHAT THAT DOES AND DOES NOT BUY, precisely: `lucas/agent.yaml`'s declared tiers now reach the
  provider, are validated fail-closed against the ADR-0009 vocabulary at construction, and are
  counted per call on `maezo_llm_tier_resolution_total`. It does NOT change which model runs —
  the repo configures exactly ONE model and no per-tier map exists, so both kinds resolve to it
  (`InferenceProvider._resolve_task_model`, and `docs/review-queue.md` for the owner decision the
  per-tier model values are).
- No `mcp-memory.read_write` episodic write in `finalize`: same disclosed boundary as
  Helena/Rafael ("no episodic memory write (ADR-0002) — a follow-up once that schema exists").
  The terminal node is a no-op `complete`, matching Rafael's naming/shape exactly.
- `receive`'s failure-reason text NEVER echoes the raw `intencao`/field value (donor's original
  `f"intencao desconhecida: {intencao!r}"` is replaced with a bounded class-token message) — the
  NON-NEGOTIABLE engine-variable hygiene requirement for this task, matching the fix Helena
  needed in T1.11 R1 cycle-2.
- `escalate_human` ADDITIONALLY drafts + sends a short WhatsApp acknowledgement (never revealing
  the adverse-outcome-in-waiting) before `start_process` — the donor's `escalate_human` never
  notified the beneficiary at all. This mirrors Helena's convergent `respond` node (every turn,
  informational or escalated, ends with the beneficiary told what happens next) — a deliberate
  improvement over the donor, not a silent deviation.
- No `matricula_beneficiario` fallback correlation key: v2's `spec/agents/lucas/agent.yaml` does
  not declare one; `conversation_id` is the sole correlation key, matching Helena's
  `SP-OP-ESCALATION-001` business-key usage exactly (same shared contract).
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
    MESSAGE_PROMPT_VERSION,
    SYSTEM_PROMPT_VERSION,
    dossier_prompt,
    escalation_ack_prompt,
    message_prompt,
)

logger = structlog.get_logger(__name__)

PROCESS_KEY = "SP-OP-ESCALATION-001"

DMN_BILLING_ADMISSIBILITY = "lucas_billing_admissibility"
DMN_ESCALATION_ROUTING = "lucas_escalation_routing"

# --- Domain enums (mirror the SP-OP-ESCALATION-001 contract + the lucas_* DMN schemas) -------

Intencao = Literal["cobranca_info", "confirmacao_pagamento", "inadimplencia", "cancelamento"]
_VALID_INTENCOES: frozenset[str] = frozenset(
    {"cobranca_info", "confirmacao_pagamento", "inadimplencia", "cancelamento"}
)

# STRUCTURALLY SAFE BY CONSTRUCTION — no adverse variant exists in this type (L0 hard, CI-enforced
# by `test_route_type_admits_no_adverse_variant`).
Route = Literal["respond_member", "escalate_human"]

# Allow-list mirrors `spec/processes/dmn/lucas_billing_admissibility.dmn`'s `roteamento` output
# domain EXACTLY — no COBRAR/SUSPENDER/CANCELAR/NEGAR value exists there or here.
AdmissibilidadeCobranca = Literal["RESPONDER", "LEMBRETE", "ESCALAR_HUMANO"]
_ADMISSIBILIDADE_ALLOW: frozenset[str] = frozenset({"RESPONDER", "LEMBRETE", "ESCALAR_HUMANO"})

# Allow-list mirrors `spec/processes/dmn/lucas_escalation_routing.dmn`'s `roteamento` output
# domain EXACTLY — every destination is a HUMAN GROUP, never an adverse outcome.
RoteamentoEscalacao = Literal["ATENDIMENTO_HUMANO", "COBRANCA_HUMANO", "CONTRATOS_HUMANO"]
_ROTEAMENTO_ESCALACAO_ALLOW: frozenset[str] = frozenset(
    {"ATENDIMENTO_HUMANO", "COBRANCA_HUMANO", "CONTRATOS_HUMANO"}
)

# `roteamento` (Lucas's own suggestion DMN) -> `grupo_humano_sugerido` annotation carried in the
# dossier/process variables for the human's benefit. This is NOT the same as the shared
# `escalation_routing` DMN that SP-OP-ESCALATION-001's own `BRT_RotearEscalonamento` evaluates
# engine-side (keyed on `motivo_categoria`/`severidade`, whose own `grupo_atendimento` domain —
# `docs/processes/contracts/SP-OP-ESCALATION-001.md` — is `plantao-clinico` |
# `enfermagem-triagem` | `atendimento-humano`, with no dedicated billing/contracts group). The
# ACTUAL BPMN candidate group always comes from that shared table, never from this map — this map
# only feeds the dossier's `grupo_humano_sugerido` (an instruction, never a decision).
_ESCALACAO_GRUPO: dict[RoteamentoEscalacao, str] = {
    "ATENDIMENTO_HUMANO": "atendimento-humano",
    "COBRANCA_HUMANO": "atendimento-humano",
    "CONTRATOS_HUMANO": "gestao-contratos",
}

# Reason Lucas routes to a human. NONE of these is an adverse outcome — every one is a reason for
# HANDOFF; the decision (rescindir/suspender/negar) is always the human's. Mirrors
# `lucas_escalation_routing.dmn`'s `motivo` input literals exactly (single-sourced: the DMN table
# only recognizes `pedido_cancelamento`/`inadimplencia_detectada`/`contestacao_cobranca` by name;
# every other value here falls on that table's own conservative catch-all).
MotivoHumano = Literal[
    "inadimplencia_detectada",
    "pedido_cancelamento",
    "contestacao_cobranca",
    "ambiguidade",
    "dmn_indisponivel",
    "falha_tecnica",
]

# Subset of the shared SP-OP-ESCALATION-001 `motivo_categoria` domain
# (`docs/processes/contracts/SP-OP-ESCALATION-001.md`) — Lucas is billing/collection, never
# clinical, so `red_flag_clinico`/`risco_psicossocial`/`intencao_clinica` are structurally
# unreachable from this module (L0 hard — see `test_motivo_categoria_never_clinical` below).
MotivoCategoria = Literal["outro", "solicitacao_humano", "falha_tecnica"]

# Full shared contract domain (kept for type-fidelity with `docs/processes/contracts/
# SP-OP-ESCALATION-001.md`) — `grave` is intentionally unreachable from Lucas's own motivo mapping
# (cobranca/contrato is never P1 clinical, mirrors the donor's own comment on this point).
Severidade = Literal["grave", "moderada", "leve"]


class WhatsAppSender(Protocol):
    """Outbound WhatsApp send seam — mirrors `helena/graph.py::WhatsAppSender` exactly (agent
    independence: redeclared here, not imported, per ADR-0004's federated-definition
    zero-cross-contamination stance). Operates on a phone HASH, never a raw number."""

    async def send(self, to_hash: str, text: str) -> dict[str, Any]: ...


# Output-only state fields — written EXCLUSIVELY by this graph's own nodes, never legitimate
# caller input. `receive` resets EVERY one of them on entry (R1 cycle-1 F2): a caller-planted
# value in any of these would otherwise flow VERBATIM into SP-OP-ESCALATION-001 engine variables
# on the skip-assess shortcuts (verifier's live probe: a planted `dmn_refs` entry reached
# `dmn_decision_refs` via the natural `ambiguidade` path). `error` is included deliberately: it
# is a node-written signal (receive's guards / start_process failures), and a caller-planted
# `error` was ALSO the R1 cycle-1 F1 fail-OPEN vector (gather/assess short-circuit on it).
# Literal-typed fields reset to "" (a value outside every allowlist — every consumer treats it
# as absent/conservative); containers reset to their empty shape. `business_key` is re-derived
# by `receive` itself (success path) or `start_process` (falsy -> `_business_key(state)`).
# This dict is ALSO the OUTPUT half of the T1.11 input/output partition — see
# `_CALLER_INPUT_FIELDS` below, whose import-time guard makes the two halves cover `LucasState`
# exactly. Adding a key here without removing it from `_CALLER_INPUT_FIELDS` (or vice versa)
# fails the module import, not a later test run.
_OUTPUT_FIELDS_RESET: dict[str, Any] = {
    "gathered": False,
    "billing_facts": {},
    "gather_notes": [],
    "admissibilidade": "",
    "roteamento_escalacao": "",
    "dmn_refs": {},
    "dmn_error": "",
    "route": "",
    "motivo_humano": "",
    "motivo_categoria": "",
    "severidade": "",
    "grupo_humano": "",
    "mensagem": {},
    "mensagem_enviada": False,
    "ack_pending": False,
    "retryable_error": False,
    "dossier": {},
    "process_started": False,
    "start_failed": False,
    "business_key": "",
    "process_ref": {},
    "desfecho": "",
    "error": "",
}


# --- Graph state (working memory; ADR-0002 working-memory layer) -----------------------------


class LucasState(TypedDict, total=False):
    """Billing/collection case state. Every field is pseudonymized (Zona Geral, ADR-0006) —
    `beneficiario_pseudo_id` NEVER carries a raw CPF/name/phone number. `status_conciliado`/
    `ciclos_sem_conciliacao` arrive PRE-RESOLVED by a deterministic CNAB conciliation worker
    upstream — Lucas CONSUMES them, never computes them (ADR-0012, L0 hard — see
    `test_conciliation_facts_passed_through_unchanged_never_recomputed` in
    `tests/unit/agents/test_lucas.py`)."""

    # Runtime identifiers (injected by the calling layer at turn start).
    tenant_id: str
    conversation_id: str
    canal: str  # whatsapp | portal | telefone
    beneficiario_pseudo_id: str
    to_hash: str  # WhatsApp destination hash (never the raw number)

    # Turn input — `intencao` selects the JOURNEY, never a merit Lucas decides.
    intencao: Intencao
    tipo_solicitacao: str  # boleto | 2a_via | vencimento | status_pagamento
    numero_boleto: str
    competencia: str  # YYYY-MM

    # Pre-resolved by a deterministic CNAB conciliation worker upstream — CONSUMED, never
    # computed here (module docstring's L0-hard invariant).
    status_conciliado: bool
    ciclos_sem_conciliacao: int
    contesta_cobranca: bool
    pedido_cancelamento: bool
    cnab_ref: str

    # Filled by `gather`.
    gathered: bool
    billing_facts: dict[str, Any]
    gather_notes: list[str]

    # Filled by `assess`.
    admissibilidade: AdmissibilidadeCobranca
    roteamento_escalacao: RoteamentoEscalacao
    dmn_refs: dict[str, str]
    dmn_error: str
    route: Route
    motivo_humano: MotivoHumano
    motivo_categoria: MotivoCategoria
    severidade: Severidade
    grupo_humano: str

    # Filled by `respond_member` / `escalate_human` / `send_escalation_ack`.
    mensagem: dict[str, Any]
    mensagem_enviada: bool
    #: LUC-05: o ACK da escalacao ainda NAO foi ao beneficiario. True desde `escalate_human`
    #: (que so monta o dossie) ate `send_escalation_ack` (que roda DEPOIS do start). Se o start
    #: falha, permanece True — e o registro honesto de "prometemos nada a ninguem ainda".
    ack_pending: bool
    #: LUC-05: a falha e transitoria e o replay e seguro (business key idempotente).
    retryable_error: bool
    dossier: dict[str, Any]

    # Filled by `start_process`.
    process_started: bool
    #: CC-01: o start foi TENTADO e FALHOU tecnicamente (`except CibSevenError` de
    #: `start_process`). NAO e a mesma coisa que `process_started is False`, que tambem cobre
    #: no-ops legitimos; e este marcador — e so ele — que a aresta condicional le.
    start_failed: bool
    business_key: str
    process_ref: dict[str, Any]

    # Output.
    desfecho: str
    error: str


# --- Input boundary (T1.11 layer 1: partition + constructor) -----------------------------------
#
# `LucasState` carries TWO disjoint classes of key. Until CC-14 Lucas had ONLY layer 2 — the
# `receive`-entry reset of the HAND-ENUMERATED `_OUTPUT_FIELDS_RESET` above — which is exactly
# the gap `spec/agents/lucas/agent.yaml` recorded as the security PRE-REQUISITE for any future
# inbound channel (gap 11.7). A hand list neutralizes the output fields somebody remembered to
# list; a state field added later is silently un-reset AND silently caller-settable.
#   * INPUT-ONLY  (`_CALLER_INPUT_FIELDS`): the ONLY keys a caller/upstream may set — the runtime
#     identifiers, the turn's `intencao`/request fields, and the PRE-RESOLVED CNAB conciliation
#     facts this graph CONSUMES and never computes (module docstring's L0-hard invariant).
#   * OUTPUT-ONLY (`_OUTPUT_FIELDS_RESET`): keys OWNED by this graph's nodes (route, the DMN
#     verdicts, motivo_*/severidade/grupo_humano, mensagem*, dossier, process_*, desfecho,
#     error). A caller must NEVER set one — that is literally the R1 cycle-1 F2 defect, whose
#     live probe watched a planted `dmn_refs` entry reach `dmn_decision_refs` in
#     SP-OP-ESCALATION-001 (module docstring §R1 CYCLE-1).
#
# TWO defenses, both fail-closed (mirrors `agents/helena/graph.py`, the sibling that owns the one
# WhatsApp webhook Lucas's beneficiary replies would arrive through):
#   1. Per-graph entry sanitization (layer 2, pre-existing) — `receive` resets EVERY output-only
#      field on EVERY branch before any downstream node runs.
#   2. Input-boundary gate (layer 1, THIS block) — a construction seam assembles state ONLY
#      through the typed `new_lucas_state` constructor or the `gate_inbound_state` allowlist
#      filter, so an output-only key can never enter the state dict at all.
#
# WHY IT EXISTS BEFORE THE SEAM DOES. Lucas declares `accepted_task_types: []` and has no inbound
# channel of his own (agent.yaml gap 11.7 — STILL OPEN; this block does NOT close it and does not
# claim to). The gate is the ORDERED prerequisite: it is built now so the day a channel lands it
# is gated by construction, the same posture `agents/rafael/graph.py` took.
#
# The completeness guard below fails at IMPORT TIME if a newly added `LucasState` field is not
# classified into exactly one of the two sets — "any missed key is a hole".
_CALLER_INPUT_FIELDS: frozenset[str] = frozenset(
    {
        # Runtime identifiers (injected by the calling layer at turn start).
        "tenant_id",
        "conversation_id",
        "canal",
        "beneficiario_pseudo_id",
        "to_hash",
        # Turn input — `intencao` selects the JOURNEY, never a merit Lucas decides. It is an
        # INPUT on purpose: `receive` validates it against `_VALID_INTENCOES` and fails closed to
        # `ambiguidade`, so a hostile value cannot pick a journey, only forfeit the turn.
        "intencao",
        "tipo_solicitacao",
        "numero_boleto",
        "competencia",
        # Pre-resolved by the deterministic CNAB conciliation worker upstream — CONSUMED, never
        # computed here. These are FACTS a caller may assert; every VERDICT derived from them
        # (`admissibilidade`, `roteamento_escalacao`, `route`, `severidade`) is output-only.
        "status_conciliado",
        "ciclos_sem_conciliacao",
        "contesta_cobranca",
        "pedido_cancelamento",
        "cnab_ref",
    }
)

_LUCAS_ALL_FIELDS: frozenset[str] = _CALLER_INPUT_FIELDS | frozenset(_OUTPUT_FIELDS_RESET)
if frozenset(LucasState.__annotations__) != _LUCAS_ALL_FIELDS:
    _unclassified = frozenset(LucasState.__annotations__) - _LUCAS_ALL_FIELDS
    _stale = _LUCAS_ALL_FIELDS - frozenset(LucasState.__annotations__)
    raise RuntimeError(
        "LucasState input/output field split is incomplete (T1.11 input-boundary gate, CC-14): "
        f"unclassified fields={sorted(_unclassified)} stale entries={sorted(_stale)} — every "
        "LucasState key MUST be either a `_CALLER_INPUT_FIELDS` member or carry a neutral "
        "default in `_OUTPUT_FIELDS_RESET`."
    )
if _CALLER_INPUT_FIELDS & frozenset(_OUTPUT_FIELDS_RESET):
    raise RuntimeError(
        "LucasState field classified as BOTH input and output (T1.11 input-boundary gate, "
        f"CC-14): {sorted(_CALLER_INPUT_FIELDS & frozenset(_OUTPUT_FIELDS_RESET))}"
    )


def new_lucas_state(raw: Mapping[str, Any]) -> LucasState:
    """Typed input-boundary constructor for a fresh Lucas turn (T1.11 layer 1).

    Accepts a raw mapping — the shape a future inbound channel (an A2A envelope, or a
    billing-context webhook handler) would hand over — and returns a `LucasState` containing ONLY
    `_CALLER_INPUT_FIELDS` keys.

    An unknown key is a HARD ERROR, and the error NAMES the offending keys: an internal seam is a
    contract, so a stray key means a producer bug and must fail closed, LOUDLY. Mirrors
    `agents/rafael/graph.py::new_rafael_state` and `agents/helena/graph.py::new_helena_state`.

    The two error classes (an output-only key vs a wholly-unknown key) are deliberately NOT
    distinguished beyond the key list — telling a hostile caller which of its keys the state
    model recognizes is a hint it does not need.
    """
    unknown = sorted(k for k in raw if k not in _CALLER_INPUT_FIELDS)
    if unknown:
        raise ValueError(
            "new_lucas_state received non-input keys (T1.11 input-boundary gate): "
            f"{unknown} — only `_CALLER_INPUT_FIELDS` may be set by a caller/inbound seam; "
            "output-only fields are owned by Lucas's graph nodes."
        )
    return cast(LucasState, {k: raw[k] for k in _CALLER_INPUT_FIELDS if k in raw})


def gate_inbound_state(raw: Mapping[str, Any]) -> LucasState:
    """Fail-closed input allowlist (drop-and-log variant of `new_lucas_state`).

    Only `_CALLER_INPUT_FIELDS` keys survive; every other key — any caller-planted output field,
    any unknown key — is DROPPED and LOGGED. Use where tolerating benign upstream drift is
    preferable to raising (a lenient ingestion edge, e.g. a WhatsApp webhook payload); use
    `new_lucas_state` on a strict internal delegation seam.

    LOG HYGIENE: the event carries the dropped KEY NAMES only, never their values — a planted
    value is unbounded caller-controlled content, the same reason `receive`'s failure reasons are
    bounded class tokens that never echo `intencao`.
    """
    dropped = sorted(k for k in raw if k not in _CALLER_INPUT_FIELDS)
    if dropped:
        logger.warning("lucas_inbound_output_fields_dropped", dropped=dropped)
    return cast(LucasState, {k: raw[k] for k in _CALLER_INPUT_FIELDS if k in raw})


# --- Helpers -----------------------------------------------------------------------------------


def _business_key(state: LucasState) -> str:
    """Idempotent business key per the SHARED SP-OP-ESCALATION-001 contract (same format Helena
    uses — `ESC-{tenant_id}-{conversation_id}`)."""
    return f"ESC-{state.get('tenant_id', '')}-{state.get('conversation_id', '')}"


def _is_escalation_intent(state: LucasState) -> bool:
    """True when the case is, by nature, a J3 escalation path.

    `inadimplencia`/`cancelamento` intencao ALWAYS escalates — never an auto-response. A
    beneficiary contesting a charge or a signaled cancellation request also always escalates,
    regardless of `intencao`."""
    return (
        state.get("intencao") in {"inadimplencia", "cancelamento"}
        or bool(state.get("contesta_cobranca", False))
        or bool(state.get("pedido_cancelamento", False))
    )


def _escalation_motivo(state: LucasState) -> MotivoHumano:
    """Reason for the J3 handoff. Never an adverse outcome — only a reason for a human to look."""
    if state.get("intencao") == "cancelamento" or state.get("pedido_cancelamento"):
        return "pedido_cancelamento"
    if state.get("contesta_cobranca"):
        return "contestacao_cobranca"
    return "inadimplencia_detectada"


def _motivo_categoria(motivo: MotivoHumano) -> MotivoCategoria:
    """Maps the internal motivo to the SP-OP-ESCALATION-001 contract's `motivo_categoria`. Lucas
    NEVER escalates by a clinical category — that is not his domain (L0 hard)."""
    if motivo in {"falha_tecnica", "dmn_indisponivel"}:
        return "falha_tecnica"
    return "outro"


def _severidade_humano(motivo: MotivoHumano) -> Severidade:
    """Cobranca/contrato is never P1 clinical (`grave` is unreachable) — technical failures are
    `leve` (mirrors Helena's falha_tecnica -> leve convention); every business-handoff reason is
    `moderada` (mirrors the donor's own fixed choice, documented as deliberate there)."""
    if motivo in {"falha_tecnica", "dmn_indisponivel"}:
        return "leve"
    return "moderada"


# --- Graph -----------------------------------------------------------------------------------


class LucasGraph:
    """Wires Lucas's injected dependencies into a compilable `StateGraph[LucasState]`."""

    def __init__(
        self,
        *,
        inference: InferenceProvider,
        dmn: DmnTransport,
        cibseven: CibSevenTransport,
        audit_sink: AuditStartSink,
        whatsapp: WhatsAppSender,
        agent_version: str = "lucas@v0",
    ) -> None:
        self._llm = inference
        self._dmn = dmn
        self._cibseven = cibseven
        # T-C2 fence: required durable ADR-0007 sink for the SP-OP-ESCALATION-001 start.
        self._audit_sink = audit_sink
        self._model_id = getattr(inference, "model_id", None)
        self._whatsapp = whatsapp
        self._agent_version = agent_version

    # -- Nodes ----------------------------------------------------------------------------

    async def receive(self, state: LucasState) -> dict[str, Any]:
        """Turn start: fail-closed guards, never a silent default.

        Missing runtime identifiers -> `falha_tecnica` (there is no idempotent business key
        without them). An unrecognized/missing `intencao` -> `ambiguidade` — this IS Lucas's
        "classification" fail-closed guard (mirrors Helena's `_classify_llm` discipline, T1.11
        R1 cycle-1): an unmapped intencao is NEVER silently read as J1 (`cobranca_info`).

        Failure reasons are bounded CLASS TOKENS — the raw `intencao`/field value is NEVER
        echoed here (engine-variable hygiene; donor's original code echoed `intencao!r}`, fixed
        here per this task's non-negotiable hardening requirement).

        R1 CYCLE-1 F2 FIX: every OUTPUT-ONLY state field is reset on entry
        (`_OUTPUT_FIELDS_RESET`) — a caller-planted value in any node-written field (dmn_refs,
        motivo_*, severidade, grupo_humano, dossier, error, ...) must never survive into
        engine-bound variables via the skip-assess shortcuts. The reset happens on EVERY branch,
        including the two fail-closed ones (whose `_escalate_min` output overrides the relevant
        reset keys with real class tokens).
        """
        reset = dict(_OUTPUT_FIELDS_RESET)

        if not state.get("tenant_id") or not state.get("conversation_id"):
            return {
                **reset,
                **self._escalate_min(
                    "falha_tecnica", error="missing runtime context (tenant_id/conversation_id)"
                ),
            }

        if state.get("intencao") not in _VALID_INTENCOES:
            return {**reset, **self._escalate_min("ambiguidade", error="unrecognized or missing intencao")}

        return {**reset, "business_key": _business_key(state)}

    async def gather(self, state: LucasState) -> dict[str, Any]:
        """Best-effort consolidation of billing facts already present in state — NEVER blocks
        routing, NEVER computes/derives the conciliation facts (they arrive pre-resolved)."""
        if state.get("error"):
            return {}  # already routed by `receive`

        notes: list[str] = []
        billing_facts: dict[str, Any] = {
            "tipo_solicitacao": state.get("tipo_solicitacao", ""),
            "competencia": state.get("competencia", ""),
            "status_conciliado": state.get("status_conciliado"),
            "ciclos_sem_conciliacao": state.get("ciclos_sem_conciliacao"),
            "cnab_ref": state.get("cnab_ref", ""),
        }
        if state.get("intencao") == "confirmacao_pagamento" and state.get("status_conciliado") is None:
            # The absence of a pre-resolved status NEVER becomes "inadimplente" by assumption —
            # it is recorded as a gap; `assess`'s DMN catch-all (never this code) decides the
            # conservative routing.
            notes.append("conciliation status not yet pre-resolved by the CNAB worker")

        return {"gathered": True, "billing_facts": billing_facts, "gather_notes": notes}

    async def assess(self, state: LucasState) -> dict[str, Any]:
        """Evaluate the deterministic DMN tables and decide the ROUTE (respond vs escalate).

        ADR-0012: the DMN decides; the LLM never does. J3 (escalation-intent) NEVER even reaches
        the billing DMN — `inadimplencia`/`cancelamento`/contestation/signaled cancellation
        ALWAYS escalates (L0 hard).

        FAIL-SAFE FECHADO: DMN unavailable or a `roteamento` value outside its recognized
        allowlist ALWAYS escalates — NEVER a silent default, NEVER treated as "admissible" by
        omission. The raw non-conforming value is NEVER echoed into `motivo_humano`/`error` —
        only the class token `ambiguidade`/`dmn_indisponivel` (engine-variable hygiene).
        """
        if state.get("error"):
            return {}  # already routed by `receive`

        dmn_refs: dict[str, str] = {}

        if _is_escalation_intent(state):
            return await self._assess_escalation(state, dmn_refs, motivo=_escalation_motivo(state))

        admis_in: dict[str, Any] = {
            "tipo_solicitacao": str(state.get("tipo_solicitacao", "")),
            "status_conciliado": bool(state.get("status_conciliado", False)),
            "ciclos_sem_conciliacao": int(state.get("ciclos_sem_conciliacao", 0) or 0),
        }
        try:
            rows, version = await self._dmn.evaluate(DMN_BILLING_ADMISSIBILITY, admis_in)
            row = first_row(rows, DMN_BILLING_ADMISSIBILITY, admis_in)
        except (DmnEvaluationError, DmnNoResultError) as exc:
            return await self._assess_escalation(
                state,
                dmn_refs,
                motivo="dmn_indisponivel",
                dmn_error=f"DMN `{DMN_BILLING_ADMISSIBILITY}` indisponivel: {exc}",
            )
        dmn_refs[DMN_BILLING_ADMISSIBILITY] = f"{DMN_BILLING_ADMISSIBILITY}#{version.id}"

        roteamento = str(row.get("roteamento", ""))
        if roteamento not in _ADMISSIBILIDADE_ALLOW:
            # FAIL-SAFE FECHADO: an unrecognized value NEVER "passes through" — and is NEVER
            # echoed anywhere (class token only, engine-variable hygiene).
            return await self._assess_escalation(state, dmn_refs, motivo="ambiguidade")

        admissibilidade = cast(AdmissibilidadeCobranca, roteamento)
        if admissibilidade == "ESCALAR_HUMANO":
            return await self._assess_escalation(state, dmn_refs, motivo="inadimplencia_detectada")

        return {
            "admissibilidade": admissibilidade,
            "route": "respond_member",
            "dmn_refs": dmn_refs,
        }

    async def _assess_escalation(
        self,
        state: LucasState,
        dmn_refs: dict[str, str],
        *,
        motivo: MotivoHumano,
        dmn_error: str | None = None,
    ) -> dict[str, Any]:
        """Resolve the escalation's suggested human group (`lucas_escalation_routing`).

        The destination is ALWAYS `escalate_human` regardless of this DMN's outcome — it only
        picks the SUGGESTED group. A DMN failure or an out-of-allowlist value NEVER loses the
        case: the conservative catch-all group `atendimento-humano` is used, and the case still
        escalates (fail-safe fechado — the case is never dropped)."""
        grupo = "atendimento-humano"  # conservative catch-all, always available
        roteamento_escalacao: RoteamentoEscalacao | None = None
        esc_in: dict[str, Any] = {
            "intencao": str(state.get("intencao", "")),
            "motivo": motivo,
            "ciclos_sem_conciliacao": int(state.get("ciclos_sem_conciliacao", 0) or 0),
        }
        try:
            rows, version = await self._dmn.evaluate(DMN_ESCALATION_ROUTING, esc_in)
            row = first_row(rows, DMN_ESCALATION_ROUTING, esc_in)
            dmn_refs[DMN_ESCALATION_ROUTING] = f"{DMN_ESCALATION_ROUTING}#{version.id}"
            rot_raw = str(row.get("roteamento", ""))
            if rot_raw in _ROTEAMENTO_ESCALACAO_ALLOW:
                roteamento_escalacao = cast(RoteamentoEscalacao, rot_raw)
                grupo = _ESCALACAO_GRUPO[roteamento_escalacao]
        except (DmnEvaluationError, DmnNoResultError):
            pass  # fail-safe: keep the conservative catch-all — the case is NEVER lost

        out: dict[str, Any] = {
            "route": "escalate_human",
            "motivo_humano": motivo,
            "motivo_categoria": _motivo_categoria(motivo),
            "severidade": _severidade_humano(motivo),
            "grupo_humano": grupo,
            "dmn_refs": dmn_refs,
        }
        if roteamento_escalacao is not None:
            out["roteamento_escalacao"] = roteamento_escalacao
        if dmn_error is not None:
            out["dmn_error"] = dmn_error
        return out

    async def respond_member(self, state: LucasState) -> dict[str, Any]:
        """J1/J2: draft + send the informational/reminder message. NEVER threatens suspension or
        cancellation, NEVER communicates a denial (structural guardrail in `_build_message`)."""
        mensagem = await self._build_message(state)
        enviada = False
        to_hash = state.get("to_hash")
        if to_hash and state.get("canal", "whatsapp") == "whatsapp":
            try:
                await self._whatsapp.send(to_hash, str(mensagem.get("texto", "")))
                enviada = True
            except Exception as exc:  # noqa: BLE001 — best-effort send, never an adverse outcome.
                mensagem["envio_nota"] = f"whatsapp send failed: {type(exc).__name__}"

        desfecho = (
            "lembrete_enviado"
            if state.get("admissibilidade") == "LEMBRETE"
            else "resposta_informativa_enviada"
        )
        return {"mensagem": mensagem, "mensagem_enviada": enviada, "desfecho": desfecho}

    async def escalate_human(self, state: LucasState) -> dict[str, Any]:
        """J3 / fail-safe: build the dossier for the human handoff and acknowledge the
        beneficiary. NEITHER communicates the adverse decision — that is exclusively the human's
        (`_build_dossier`'s `decisao_cancelamento` is always `None`).

        R1 CYCLE-1 F1a FIX: this node now stamps `route="escalate_human"` AUTHORITATIVELY in its
        own output. Pre-fix it relied on `assess` having stamped it — but `_route`'s conservative
        default also sends route-UNSET states here (e.g. a turn whose assess short-circuited),
        and `start_process`'s old gate then read the missing stamp as "not an escalation" and
        silently skipped the start AFTER this node had already told the beneficiary a human
        would continue (fail-OPEN case loss, verifier-proven). The node that performs the human
        handoff is the authority on the fact that a handoff is happening.

        Defensive companion: reaching this node without a `motivo_humano` is a technical
        anomaly — it is stamped `falha_tecnica` (class token; never left empty/unset in
        engine-bound fields).
        """
        defaults: dict[str, Any] = {}
        if not state.get("motivo_humano"):
            defaults = {
                "motivo_humano": "falha_tecnica",
                "motivo_categoria": "falha_tecnica",
                "severidade": "leve",
                "grupo_humano": "atendimento-humano",
            }
            state = cast(LucasState, {**state, **defaults})

        dossier = await self._build_dossier(state)

        # LUC-05 (ORDEM): este no NAO fala mais com o beneficiario. Ate 2026-09-04 o ACK ("um
        # atendente humano vai continuar") saia DAQUI, ANTES de `start_process` — e quando o
        # start falhava, o `except CibSevenError` so gravava `process_started=False`, o desfecho
        # seguia `escalado_humano` e o turno terminava calado: o beneficiario informado de que um
        # humano assumiria, e ZERO instancias de SP-OP-ESCALATION-001 existindo. O envio migrou
        # para `send_escalation_ack`, alcancavel SO pelo ramo de sucesso da aresta condicional
        # que sai de `start_process`. `ack_pending` registra a divida ate la.
        return {
            **defaults,
            "route": "escalate_human",  # authoritative stamp (F1a) — never inferred downstream
            "dossier": dossier,
            "mensagem_enviada": False,
            "ack_pending": True,
            "desfecho": "escalado_humano",
        }

    async def start_process(self, state: LucasState) -> dict[str, Any]:
        """Start SP-OP-ESCALATION-001 idempotently. The informational route is the ONLY one
        that never opens a process.

        R1 CYCLE-1 F1b FIX (fail-CLOSED gate): pre-fix this node gated on
        `route != "escalate_human"` -> skip — so a state that reached it WITHOUT the stamp
        (assess short-circuited on a planted `error`; `_route`'s conservative default still ran
        `escalate_human`, which built the dossier and SENT the beneficiary the "a human will
        continue" ack) silently returned `process_started=False`: the beneficiary was promised a
        human while ZERO engine instances existed (fail-OPEN case loss, verifier-proven). The
        gate is now inverted: ONLY an explicit `route == "respond_member"` skips the start;
        `escalate_human` (now also stamped authoritatively by the escalate node itself, F1a) or
        ANY unset/unknown route starts the escalation — mirroring `_route`'s own conservative
        default (on doubt, the human path), never a silent skip.
        """
        if state.get("route") == "respond_member":
            return {"process_started": False}

        business_key = state.get("business_key") or _business_key(state)
        variables = self._escalation_variables(state)
        provenance = AgentDecisionProvenance(
            agent_id="lucas",
            agent_version=self._agent_version,
            tenant_id=state.get("tenant_id", ""),
            model_id=self._model_id,
            prompt_version=SYSTEM_PROMPT_VERSION,
            decision_basis={
                "route": state.get("route", ""),
                "desfecho": state.get("desfecho", ""),
            },
        )
        try:
            instance = await start_process_idempotent(
                self._cibseven,
                process_key=PROCESS_KEY,
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

    async def send_escalation_ack(self, state: LucasState) -> dict[str, Any]:
        """LUC-05: o ACK ao beneficiario — SO depois que a escalacao existe de verdade.

        GUARDA: so envia quando a rota e `escalate_human` E o start reportou uma instancia viva
        (`process_started`, que em Lucas cobre STARTED e ALREADY_ACTIVE). A jornada informativa
        (`respond_member`), que nunca abre processo, passa por aqui como no-op — ela ja respondeu
        no seu proprio no.

        O envio continua best-effort (uma falha de WhatsApp nao pode desfazer uma escalacao que
        JA existe no engine), mas agora `mensagem_enviada` conta a verdade e `ack_pending`
        registra o que ficou por entregar.
        """
        if state.get("route") != "escalate_human" or state.get("process_started") is not True:
            return {}
        to_hash = state.get("to_hash")
        if not to_hash or state.get("canal", "whatsapp") != "whatsapp":
            return {"ack_pending": True}

        ack_text = await self._build_escalation_ack(state)
        try:
            await self._whatsapp.send(to_hash, ack_text)
        except Exception:  # noqa: BLE001 — best-effort ack, never undoes a live escalation.
            return {"mensagem_enviada": False, "ack_pending": True}
        return {"mensagem_enviada": True, "ack_pending": False, "desfecho": "escalado_humano"}

    async def notify_start_failure(self, state: LucasState) -> dict[str, Any]:
        """CC-01: o start FALHOU — grava o desfecho de erro e ALERTA, em vez de seguir calado.

        Ate CC-01 a aresta que saia de `start_process` era INCONDICIONAL: o turno chegava ao
        terminal com o `desfecho` de SUCESSO que um no a montante ja havia gravado, afirmando um
        fato que nao aconteceu, e sem prazo nenhum — o timer de SLA vive na instancia BPMN que
        nunca nasceu. O corpo deste no e o helper compartilhado
        (`maezo.runtime.start_outcome.notify_start_failure`): uma definicao para os 9 agentes,
        nunca 9 copias.

        LUC-05: `ack_pending`/`retryable_error` viajam junto porque o ACK ao
        beneficiario NAO foi enviado (ele migrou para `send_escalation_ack`, depois do
        start) e porque o replay e seguro — `start_process_idempotent` e idempotente por
        business key, entao uma retentativa reencontra a instancia viva em vez de abrir
        uma segunda.
        """
        return emit_start_failure_notice(
            dict(state),
            agent_id="lucas",
            process_key=PROCESS_KEY,
            extra={"mensagem_enviada": False, "ack_pending": True, "retryable_error": True},
        )

    async def complete(self, state: LucasState) -> dict[str, Any]:
        """Terminal node — no further computation; `desfecho` was already set upstream."""
        return {}

    # -- Conditional routing ------------------------------------------------------------------

    @staticmethod
    def _route(state: LucasState) -> str:
        # FAIL-SAFE: on absence/doubt, ALWAYS the human (never respond_member by omission). No
        # branch of this function can return an adverse destination — `Route` does not admit one.
        return "respond_member" if state.get("route") == "respond_member" else "escalate_human"

    # -- Fail-closed minimal escalation (used by `receive`, before any DMN runs) --------------

    @staticmethod
    def _escalate_min(motivo: MotivoHumano, *, error: str) -> dict[str, Any]:
        return {
            "route": "escalate_human",
            "motivo_humano": motivo,
            "motivo_categoria": _motivo_categoria(motivo),
            "severidade": _severidade_humano(motivo),
            "grupo_humano": "atendimento-humano",
            # R1 cycle-1 F2: explicitly empty — no DMN ran on this shortcut, so nothing (least
            # of all a caller-planted value) may pose as a DMN provenance ref in engine
            # variables. Redundant with `receive`'s blanket reset by design (defense in depth).
            "dmn_refs": {},
            "error": error,
        }

    # -- Contract variables + drafting -------------------------------------------------------

    def _escalation_variables(self, state: LucasState) -> dict[str, Any]:
        """Assembles SP-OP-ESCALATION-001's input variables per
        `docs/processes/contracts/SP-OP-ESCALATION-001.md` (the SAME shared contract Helena
        starts) plus Lucas-specific audit annotations (mirrors Rafael's `dossie_rafael`/
        `rafael_route` additive style) — never a decision, only an instruction for the human."""
        dossier = state.get("dossier") or {}
        resumo = str(dossier.get("narrativa", "")) or (
            f"Encaminhamento automatico ({state.get('motivo_humano') or 'outro'})."
        )
        # `or`-fallbacks (not `.get(k, default)`) on the output-only fields: after `receive`'s
        # R1 cycle-1 F2 reset these keys are PRESENT but "" until a node writes them — the
        # engine variables must carry well-formed class tokens, never an empty string.
        variables: dict[str, Any] = {
            "tenant_id": state.get("tenant_id", ""),
            "source_agent_id": "lucas",
            "source_agent_version": self._agent_version,
            "conversation_id": state.get("conversation_id", ""),
            "beneficiario_pseudo_id": state.get("beneficiario_pseudo_id", ""),
            "canal": state.get("canal", "whatsapp"),
            "motivo_categoria": state.get("motivo_categoria") or "outro",
            "severidade": state.get("severidade") or "moderada",
            "resumo_contexto": resumo,
        }
        dmn_refs = state.get("dmn_refs")
        if dmn_refs:
            variables["dmn_decision_ref"] = next(iter(dmn_refs.values()), "")
            variables["dmn_decision_refs"] = dmn_refs
        # Lucas-specific audit annotations — additive, never a decision (module docstring).
        variables["lucas_route"] = state.get("route") or "escalate_human"
        variables["motivo_encaminhamento"] = state.get("motivo_humano") or ""
        variables["grupo_humano_sugerido"] = state.get("grupo_humano") or "atendimento-humano"
        variables["dossie_lucas"] = dossier
        return variables

    async def _build_message(self, state: LucasState) -> dict[str, Any]:
        """Drafts the informational/reminder message (J1/J2). The LLM only phrases already-known
        facts — it never decides `admissibilidade` (that is 100% DMN-derived, ADR-0012)."""
        facts: dict[str, Any] = {
            "tipo_solicitacao": state.get("tipo_solicitacao"),
            "competencia": state.get("competencia"),
            "numero_boleto": state.get("numero_boleto"),
            "status_conciliado": state.get("status_conciliado"),
            "admissibilidade": state.get("admissibilidade"),
            "dmn_refs": state.get("dmn_refs", {}),
        }
        prompt = f"{message_prompt()}\n\nfatos={facts}"
        try:
            texto = await self._llm.generate(
                prompt,
                phi=True,
                agent_id="lucas",
                tenant_id=state.get("tenant_id", ""),
                # AF-12: phrasing facts the DMN already decided — `task_default` (ADR-0009 §2
                # "classificacao -> modelo rapido/barato").
                task_kind="task_default",
            )
        except Exception:  # noqa: BLE001 — fail-safe default: never leave the beneficiary with nothing.
            texto = "Recebemos sua solicitacao. Em breve enviaremos os detalhes por aqui."
        return {
            "prompt_version": MESSAGE_PROMPT_VERSION,
            "tipo": "mensagem_beneficiario",
            "fatos": facts,
            "texto": texto,
            # STRUCTURAL GUARDRAIL: never a suspension/cancellation communication — always None
            # here; a value would be a detectable bug. Mirrors the donor's own guardrail fields.
            "comunicacao_suspensao": None,
            "comunicacao_cancelamento": None,
        }

    async def _build_dossier(self, state: LucasState) -> dict[str, Any]:
        """Drafts the escalation dossier narrative (J3 / fail-safe). The LLM reasons over the
        FACTS only — `decisao_cancelamento` is ALWAYS `None` (mirrors Rafael's
        `decisao_cobertura` guardrail): the decision belongs exclusively to the human.

        `intencao` is ALLOWLIST-GATED (engine-variable hygiene, R1 cycle-1): on the
        `ambiguidade` path the raw value is by definition UNRECOGNIZED — echoing it into the
        dossier (which ships into engine variables as `dossie_lucas`) would be exactly the
        offending-enum-value leak class Helena's T1.11 cycle-2 fixed. Out-of-allowlist -> None.
        """
        raw_intencao = state.get("intencao")
        facts: dict[str, Any] = {
            "intencao": raw_intencao if raw_intencao in _VALID_INTENCOES else None,
            "tipo_solicitacao": state.get("tipo_solicitacao"),
            "competencia": state.get("competencia"),
            "numero_boleto": state.get("numero_boleto"),
            "status_conciliado": state.get("status_conciliado"),
            "ciclos_sem_conciliacao": state.get("ciclos_sem_conciliacao"),
            "contesta_cobranca": state.get("contesta_cobranca"),
            "pedido_cancelamento": state.get("pedido_cancelamento"),
            "cnab_ref": state.get("cnab_ref"),
            "motivo_humano": state.get("motivo_humano"),
            "grupo_humano": state.get("grupo_humano"),
            "roteamento_escalacao": state.get("roteamento_escalacao"),
            "dmn_refs": state.get("dmn_refs", {}),
            "lacunas_enriquecimento": state.get("gather_notes", []),
        }
        prompt = f"{dossier_prompt()}\n\nmotivo_humano={state.get('motivo_humano')}\nfatos={facts}"
        try:
            narrativa = await self._llm.generate(
                prompt,
                phi=True,
                agent_id="lucas",
                tenant_id=state.get("tenant_id", ""),
                # AF-12: the escalation narrative is what a human reads before deciding —
                # `reasoning` (ADR-0009 §2 "raciocinio critico -> fronteira").
                task_kind="reasoning",
            )
        except Exception:  # noqa: BLE001 — LLM failure never blocks the escalation.
            narrativa = ""
        return {
            "prompt_version": DOSSIER_PROMPT_VERSION,
            "tipo": "dossie_escalacao",
            "motivo_humano": state.get("motivo_humano"),
            "grupo_humano": state.get("grupo_humano"),
            "fatos": facts,
            "dmn_decision_refs": state.get("dmn_refs", {}),
            "narrativa": narrativa or f"Encaminhamento automatico ({state.get('motivo_humano', 'outro')}).",
            # STRUCTURAL GUARDRAIL (L0 hard): the dossier NEVER carries the adverse decision.
            "decisao_cancelamento": None,  # rescindir/manter/suspender — always human (CANCEL-001)
        }

    async def _build_escalation_ack(self, state: LucasState) -> str:
        """Short WhatsApp acknowledgement sent on the escalation path (module docstring's
        disclosed improvement over the donor). NEVER reveals the pending adverse outcome."""
        prompt = f"{escalation_ack_prompt()}\n\nmotivo_humano={state.get('motivo_humano')}"
        try:
            return await self._llm.generate(
                prompt,
                phi=True,
                agent_id="lucas",
                tenant_id=state.get("tenant_id", ""),
                task_kind="task_default",  # AF-12: a short fixed-shape acknowledgement.
            )
        except Exception:  # noqa: BLE001 — fail-safe default: never leave the beneficiary with nothing.
            return "Recebemos sua solicitacao. Um atendente humano vai continuar por aqui em breve."

    # -- Graph assembly -----------------------------------------------------------------------

    def compile_graph(self) -> StateGraph[LucasState]:
        g: StateGraph[LucasState] = StateGraph(LucasState)
        g.add_node("receive", self.receive)
        g.add_node("gather", self.gather)
        g.add_node("assess", self.assess)
        g.add_node("respond_member", self.respond_member)
        g.add_node("escalate_human", self.escalate_human)
        g.add_node("start_process", self.start_process)
        g.add_node("notify_start_failure", self.notify_start_failure)
        g.add_node("send_escalation_ack", self.send_escalation_ack)
        g.add_node("complete", self.complete)

        g.add_edge(START, "receive")
        g.add_edge("receive", "gather")
        g.add_edge("gather", "assess")
        g.add_conditional_edges(
            "assess", self._route, {"respond_member": "respond_member", "escalate_human": "escalate_human"}
        )
        g.add_edge("respond_member", "start_process")
        g.add_edge("escalate_human", "start_process")
        # CC-01: a aresta que sai de `start_process` e CONDICIONAL. Uma falha tecnica de
        # start desvia para `notify_start_failure` (desfecho de erro + alerta); qualquer
        # outro caminho — incluindo os no-ops legitimos com `process_started=False` —
        # segue para o terminal de sempre. O predicado e compartilhado (uma definicao).
        g.add_conditional_edges(
            "start_process",
            route_after_start,
            {"notify_start_failure": "notify_start_failure", "continue": "send_escalation_ack"},
        )
        g.add_edge("notify_start_failure", END)
        g.add_edge("send_escalation_ack", "complete")
        g.add_edge("complete", END)
        return g


def build(config: dict[str, Any] | None = None) -> StateGraph[LucasState]:
    """Contract `_template/graph.py:build`, resolved by `AgentLoader`/`runtime.harness.Harness`.

    `config` MUST contain:
      - `inference`: an `InferenceProvider` (ADR-0009).
      - `dmn`: a `DmnTransport` (ADR-0028/T1.5).
      - `cibseven`: a `CibSevenTransport` (ADR-0001/T1.11).
      - `whatsapp`: a `WhatsAppSender`.
    Optional:
      - `agent_version`: audit provenance string (ADR-0007), defaults to `"lucas@v0"`.

    Fail-closed: missing a required dependency raises `ValueError` at build time — Lucas never
    silently constructs a graph that would crash mid-conversation on its first tool call.
    """
    cfg = config or {}
    inference = cfg.get("inference")
    dmn = cfg.get("dmn")
    cibseven = cfg.get("cibseven")
    whatsapp = cfg.get("whatsapp")
    audit_sink = cfg.get("audit_sink")
    missing = [
        name
        for name, value in (
            ("inference", inference),
            ("dmn", dmn),
            ("cibseven", cibseven),
            ("whatsapp", whatsapp),
            ("audit_sink", audit_sink),
        )
        if value is None
    ]
    if missing:
        raise ValueError(
            f"Lucas build(config) is missing required dependencies: {missing} "
            "(ADR-0001/0007/0009/0028 — inference/dmn/cibseven/whatsapp/audit_sink must all be "
            "injected; audit_sink is the T-C2 fence — no escalation start without a durable sink)"
        )
    agent_version = str(cfg.get("agent_version", "lucas@v0"))
    return LucasGraph(
        inference=cast(InferenceProvider, inference),
        dmn=cast(DmnTransport, dmn),
        cibseven=cast(CibSevenTransport, cibseven),
        audit_sink=cast(AuditStartSink, audit_sink),
        whatsapp=cast(WhatsAppSender, whatsapp),
        agent_version=agent_version,
    ).compile_graph()


# Prompt versions exposed for audit/eval gating (ADR-0007/0009).
PROMPT_VERSIONS: dict[str, str] = {
    "system": SYSTEM_PROMPT_VERSION,
    "message": MESSAGE_PROMPT_VERSION,
    "dossier": DOSSIER_PROMPT_VERSION,
}

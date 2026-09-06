"""Beatriz Salgado — Investigadora de Fraude / Cadeia de Custodia (Phase 3, T1.12).

Journey (mirrors the v1 donor's structure, READ-ONLY reference `Maezo-Healthcare-Plan
src/maezo/agents/beatriz/graph.py`, adapted to v2's flatter seam set — same rationale as
`agents/rafael/graph.py`'s/`agents/marina/graph.py`'s module docstrings). The graph is REDUCED
to a single substantive node (`instruct_investigation`, the Rafael/Beatriz principle):

    receive -> gather -> instruct_investigation -> finalize

Beatriz serves SP-OP-FRAUDE-001 (Investigacao de Fraude — Cadeia de Custodia): convoked via A2A
`fraude.investigate` by the engine's own service tasks (`operadora.fraude.gather_evidence` /
`operadora.fraude.assemble_dossier`), she COLLECTS/normalizes the case evidence (PSEUDONYMIZED
pointers + hashes — never raw PHI) and ASSEMBLES the investigation dossier from the
`indicadores_presentes`/`score_indicadores` that arrive PRE-RESOLVED by the worker
`operadora.fraude.score_indicators` (T2.7: the 7 `fraude_scoring/*` DMNs evaluated engine-side;
`fraude_indicadores`/`fraude_routing` are engine-native `businessRuleTask`s downstream in the
SAME BPMN). The LLM REASONS over the observed facts/indicators to organize the case — it NEVER
decides. This graph evaluates NO DMN and starts NO process: per the R1-audited
`spec/agents/beatriz/agent.yaml` (tools allowlist TIGHT — `mcp-fhir.read_patient_summary` +
`mcp-memory.read_write` ONLY; `mcp-cibseven.start_process`/`mcp-dmn.evaluate` deliberately NOT
declared), the ENGINE drives SP-OP-FRAUDE-001 and convokes Beatriz — she never starts it and
never re-evaluates the scoring chain (the donor's `len(evidencia)*10` heuristic was defect B10,
deleted in T2.7 — nothing here reintroduces any score arithmetic).

L0 HARD STRUCTURAL GUARDRAIL (invariant of SP-OP-FRAUDE-001; ADR-0005/0008/0018, CI-enforced;
KPIs `zero_auto_accusation == 0` / `false_accusation_rate == 0`): Beatriz NEVER accuses fraud,
NEVER decides fraud, NEVER produces an adverse verdict — she instructs the investigation and a
HUMAN decides (`UT_DecisaoInvestigador`, over the SEALED `bundle_root`). Structurally:
  - There is NO fraud-accusation path in her route/desfecho type: `Desfecho` admits ONLY
    `{"dossie_instruido", "instrucao_incompleta"}` — no ACUSAR/FRAUD_DETECTED/BLOQUEAR/
    DESCREDENCIAR variant exists in the type, and the graph has NO conditional edge at all
    (linear, single entry) — there is no branch that could even express an adverse outcome.
  - `BeatrizState` has NO `decisao_fraude`/`bundle_root`/`destino_referral` channel — those
    variables cannot even be transported by this graph's state, let alone set. The dossier
    carries them explicitly as ALWAYS-`None` guardrail fields (`_build_dossier`) to make it
    unmistakable that the decision belongs to the human and the sealing to the engine worker
    (`operadora.fraude.seal_custody_bundle` / gated `register_fraud_accusation`, guarded by
    `ERR_FRAUD_ACCUSATION_NOT_HUMAN` — workers untouched by this build).
  - A high `score_indicadores` is a ROUTING FACT ("investigate more"), NEVER a verdict: the
    score is CONSUMED from the pre-resolved input, coerced defensively, and echoed to the
    dossier as an observed fact — no branch reads it to change behavior, and no code path
    derives it from the evidence.

CUSTODY / NO-PHI-IN-CUSTODY (ADR-0006/0020): everything reaching this graph is already
pseudonymized (`entidade_pseudo_id`/`beneficiario_pseudo_id`/`prestador_id` — never
CPF/CNPJ/nome/CNS). BOTH corpora `gather` produces are CLOSED PROJECTIONS, never passthroughs:

  - EVIDENCE (`_normalize_evidence`): refuses any item without a `ref`, refuses any item
    carrying a known raw-PHI key, and PROJECTS survivors to the closed key allowlist
    `{ref, hash, tipo, origem}` — so even an unknown extra key smuggling raw PHI in its value is
    stripped before the dossier (defense in depth; the hard barrier for THIS corpus remains the
    `seal_custody_bundle` worker's `ERR_PHI_IN_CUSTODY` guard).
  - FHIR SUMMARY (`_normalize_summary`): the `PatientSummaryReader` seam is a GENERIC Protocol
    over v2's FHIR server, NOT the donor's PEP-gated `mcp-fhir.read_patient_summary` ToolInvoker (see
    the labeled boundary below), so its payload is controlled by a server UPSTREAM of this
    graph — it is untrusted input, exactly like `evidencia_refs`. It is refused WHOLE when any
    key (case-insensitively, RECURSIVELY through nested dicts/lists) is in `_PHI_KEYS`, and
    otherwise projected to `_SUMMARY_ALLOWED_KEYS` with a CLOSED VALUE domain per key
    (`resourceType` ∈ `_SUMMARY_RESOURCE_TYPES`; `id` only as an ECHO of the already-
    pseudonymized reference the graph itself asked for). The result: `summary_facts` can only
    ever be a subset of `{resourceType: <one of three FHIR types>, id: <the ref already in
    state>}` — NO upstream-controlled content can reach `_facts()`'s `resumo_fhir`, hence
    neither the `phi=True` dossier prompt nor the custody-bound dossier. There is NO downstream
    backstop for this corpus: `operadora.fraude.seal_custody_bundle` inspects ONLY
    `variables["evidencia_refs"]`'s `str` elements, so it never sees the summary at all and
    this projection is the SINGLE barrier (defect BEA-06). Fail-closed on every abnormal
    shape/failure: empty summary + a bounded lacuna token, NEVER a partial passthrough.

TASY write DROP (ADR-0013): CDC is consumed, Tasy is never written.

CALLER-PLANTED-OUTPUT SANITIZATION (baked in from the start — the R1 cycle-1 defect class all
four tranche-1 graphs had, per fernando/carolina/marina's proven fix): `receive` is the SINGLE
entry (one edge START->receive) and merges `_output_field_resets()` on ALL its paths BEFORE
gather/instruct run, so a hostile caller pre-planting output fields (a forged/accusation-shaped
`dossier`, a forged `evidencia_normalizada`, a planted `desfecho="dossie_instruido"`, a planted
`error`, a forged `business_key`) can NEVER flow verbatim into the dossier the engine will seal
into the custody chain. For Beatriz this class is ESPECIALLY dangerous — a planted
accusation-shaped value reaching the sealed dossier would be indistinguishable from agent
output to the human investigator. `_CALLER_INPUT_FIELDS` + the partition-completeness test
(`test_output_field_partition_is_complete`) keep the reset list structurally in sync with
`BeatrizState`. Defense in depth: the `gather`/`instruct_investigation` error bails — reachable
ONLY via `receive`'s own missing-context guard post-sanitization — re-assert the fail-safe
`desfecho="instrucao_incompleta"` instead of returning `{}`.

FAIL-CLOSED FAILURE POSTURE (class tokens only): every failure reason this graph records
(`error`, `gather_notes`, dossier `lacunas`) is a BOUNDED CLASS TOKEN (e.g.
`contexto_runtime_ausente`, `resumo_fhir_indisponivel`, `evidencia_recusada:<idx>`,
`narrativa_indisponivel`) — NEVER an exception string, field value, or raw LLM output. The
dossier is the corpus the engine seals into the ADR-0007 custody chain; echoing free text into
it would smuggle unbounded content past the no-PHI guarantee. (Disclosed divergence from the
donor, which interpolated `{exc}` into gather notes — spec's custody hygiene wins.) LLM/FHIR/
gather failure NEVER blocks the instruction path and NEVER produces a silent auto-anything: the
turn always ends at the human-bound dossier (`dossie_instruido`) or the explicit incomplete
marker (`instrucao_incompleta`) — both of which SP-OP-FRAUDE-001 routes to the human
investigator by design (`fraude_routing`'s output domain is exactly `{INVESTIGACAO_HUMANA}`).

LABELED BOUNDARIES (this build, disclosed — never fabricated, same rationale as
`agents/rafael/graph.py`'s/`agents/marina/graph.py`'s module docstrings):
- `gather` uses a thin `PatientSummaryReader` Protocol over v2's generic FHIR server. CORRECTED
  (WP FHIR-TOOL-SURFACE-PARITY): the T2.4 "no ToolRegistry/PEP gateway wiring for agent tool
  calls yet" claim is FALSE fleet-wide — `gateway/seams/fhir.py::GatedFhirReader` gates every
  agent named in `gateway/tool_registry.py::_FHIR_ADAPTER_BY_AGENT`. What is true FOR BEATRIZ is
  narrower and is stated in `_INVOKED_WITHOUT_GATED_SEAM` below/in the parity fence: she is
  absent from that map, so no composition root builds her an `fhir` seam at all and this branch
  never runs in production (it lives in `NOTE_FHIR_READER_NAO_CONFIGURADO`). Wiring her would
  LIGHT UP a PHI read that does not happen today — an owner decision, not an implementation
  follow-up. Her `agent.yaml` now declares `mcp-fhir.read_patient_summary`, the id this node
  actually calls (it declared `mcp-fhir.read_patient`, so the PEP's L1 would have denied her own
  read with `TOOL_NAO_DECLARADA` — the same species as carolina's NEW-04, masked only by
  `leitura_phi_clinica`'s shadow enforcement). Best-effort: FHIR absence/failure degrades to a
  dossier gap note, never a fabricated fact, never a blocked instruction.
- No episodic memory write (`mcp-memory.read_write`, ADR-0002) in `finalize` — same rationale
  as Helena's/Rafael's/Marina's graphs: `MemoryServer.store_episodic` still refuses fail-closed.
  The table exists (`agent_memory`, migration `0001`); what is missing is the tool's
  `(agent_id, event)` signature, which carries neither `tenant_id` nor `thread_id` (both
  `text NOT NULL`) — GAP-DU-01-a. The semantic column that once accompanied it was dropped by
  `0009_drop_pgvector`; ADR-0002 §3 is SUSPENDED pending a consumer (ADR-0047, DRAFT).
- The inbound A2A delegation adapter (`fraude.investigate`) is HALF wired (BEA-09).
  CORRECTED (CC-04, fleet audit) — the prior text here claimed v2's `a2a/` package had no
  `DelegationEnvelope`/`DelegationDispatcher`; both exist and are fully built/tested
  (`a2a/delegation.py::DelegationEnvelope`, `a2a/dispatcher.py::DelegationDispatcher`, exported
  from `maezo.a2a`). UPDATED (BEA-09, lote3) — CC-04's companion claim that "there is no
  `src/maezo/agents/beatriz/delegation.py`" is NO LONGER TRUE at this tip: the handler now exists
  (`agents/beatriz/delegation.py::make_beatriz_handler`), and nine of the ten agents (all but
  lucas) now have a real `delegation.py` using that infra. What is still missing for Beatriz is
  registration and origin: the handler is NOT registered with any dispatcher (`grep -n
  '"beatriz"' runtime/agent_runtime/a2a_composition.py` = 0 hits) and `tools/workers/fraude.py`
  still does not convoke her — both are an owner decision (gap `FERNANDO-DELEGATION-CALL-SITE`).
  No live delegation reaches this graph; every non-test invocation still hands it an
  already-assembled case state, as the unit tests do. (The `ToolRegistry`/PEP-gateway claim two
  paragraphs above remains true for Beatriz specifically: she is absent from
  `gateway/tool_registry.py::_FHIR_ADAPTER_BY_AGENT`, so no composition root ever builds her an
  `fhir` seam at all, gated or not — now also pinned by
  `tests/unit/gateway/test_fhir_tool_surface_parity.py::_INVOKED_WITHOUT_GATED_SEAM`, which fails
  loudly the day the map gains her key without this note being updated.)
- Unanchorable case (missing `tenant_id`/`numero_caso`): this build bails WITHOUT assembling a
  dossier (`dossier` stays `{}`, `desfecho="instrucao_incompleta"`) — a disclosed divergence
  from the donor, which assembled a best-effort dossier anyway. Rationale: without the
  idempotent business key (`FRAUDE-{tenant}-{numero_caso}`, contract §Business key) the dossier
  cannot be anchored to a case instance, and producing an unanchored corpus that could be
  sealed would be worse than the explicit incomplete marker (spec's conservative-ambiguity
  escalation trigger wins).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Final, Literal, Protocol, TypedDict, cast

from langgraph.graph import END, START, StateGraph

from maezo.runtime.dependency_failures import EXTERNAL_DEPENDENCY_FAILURES, PROGRAMMING_ERRORS
from maezo.runtime.guards import require_number
from maezo.runtime.inference import InferenceProvider
from maezo.runtime.prompt_format import render_fatos_para_prompt
from maezo.runtime.turn_telemetry import emit_turn_desfecho

from .prompts import DOSSIER_PROMPT_VERSION, SYSTEM_PROMPT_VERSION, dossier_prompt

# --- Domain enums (mirror the SP-OP-FRAUDE-001 contract) --------------------------------------

# Investigated-entity type (contract input `entidade_tipo`).
EntidadeTipo = Literal["prestador", "beneficiario", "contrato", "rede"]

# Investigation intensity (DMN `fraude_indicadores` output, resolved ENGINE-SIDE by
# `BRT_Indicadores` — arrives pre-resolved in state; a ROUTING FACT, never a verdict).
Intensidade = Literal["LEVE", "APROFUNDADA", "PRIORITARIA"]

# Turn outcome. STRUCTURALLY WITHOUT AN ADVERSE VARIANT (L0 hard, `zero_auto_accusation`): the
# only outcomes this graph can produce are "the dossier was instructed/assembled" and "the
# instruction is incomplete" — there is NO `fraude_confirmada`, NO `acusar`, NO `descredenciar`,
# NO `referir` variant. Those are born SOLELY in the human User Task `UT_DecisaoInvestigador`.
Desfecho = Literal["dossie_instruido", "instrucao_incompleta"]

# Bounded failure-class tokens (module docstring §FAIL-CLOSED FAILURE POSTURE) — the ONLY
# values ever written to `error`/`gather_notes`/dossier `lacunas` (plus the
# `evidencia_recusada:<idx>` family, index-only by construction).
ERROR_CONTEXTO_RUNTIME_AUSENTE = "contexto_runtime_ausente"
NOTE_FHIR_READER_NAO_CONFIGURADO = "fhir_reader_nao_configurado"
NOTE_RESUMO_FHIR_INDISPONIVEL = "resumo_fhir_indisponivel"
NOTE_RESUMO_FHIR_RECUSADO = "resumo_fhir_recusado"
NOTE_SEM_EVIDENCIA_NO_INTAKE = "sem_evidencia_no_intake"
NOTE_NARRATIVA_INDISPONIVEL = "narrativa_indisponivel"
NOTE_EVIDENCIA_RECUSADA_PREFIX = "evidencia_recusada"
#: BEA-04 (fleet audit ciclo 2): tokens que `_score_consumed` anexa via `maezo.runtime.guards.require_number`
#: quando `score_indicadores` chega ausente/do tipo errado -- nunca mais um `0` que se passa por score real.
NOTE_SCORE_AUSENTE = "score_indicadores_ausente"
NOTE_SCORE_INVALIDO = "score_indicadores_invalido"

# Closed projection allowlist for a normalized evidence pointer (no-PHI-in-custody,
# ADR-0006/0020): whatever else an inbound item carries is STRIPPED, never forwarded.
_POINTER_ALLOWED_KEYS: frozenset[str] = frozenset({"ref", "hash", "tipo", "origem"})

# Known raw-PHI key names — an item carrying ANY of these is refused whole (it signals upstream
# contamination; laundering it via projection would hide the incident from the lacuna trail).
_PHI_KEYS: frozenset[str] = frozenset(
    {"cpf", "cnpj", "nome", "name", "nome_social", "cns", "rg", "telefone", "endereco", "email"}
)


# Maximum nesting depth the raw-PHI key scan walks (`_has_phi_key`). Deep enough for any real
# FHIR summary shape (`Bundle.entry[].resource.subject...`), bounded so an adversarial or
# self-referential payload can never turn the scan into a RecursionError on `gather`'s hot path
# — exceeding it is treated as CONTAMINATED (fail-closed), never as clean.
_MAX_PHI_SCAN_DEPTH: int = 12

# Closed VALUE domain for an admitted `resourceType` (BEA-06). A patient-summary read can only
# legitimately answer with the `Patient` resource itself or the `Bundle`/`Composition` a FHIR
# `$summary`-style operation returns; anything else under this key is upstream-controlled
# CONTENT, not a resource type, and is dropped. A closed value domain (rather than a shape
# regex) is what makes it impossible to smuggle free text through an allowlisted key.
_SUMMARY_RESOURCE_TYPES: frozenset[str] = frozenset({"Bundle", "Composition", "Patient"})


def _admits_resource_type(value: Any, summary_ref: str) -> bool:
    """`resourceType` is admitted ONLY from the closed domain above."""
    return isinstance(value, str) and value in _SUMMARY_RESOURCE_TYPES


def _admits_subject_id(value: Any, summary_ref: str) -> bool:
    """`id` is admitted ONLY as an ECHO of the pseudonymized reference this graph itself asked
    for — so it contributes NO new content (the ref is already in state) while still letting a
    subject-swapped answer (an id the graph never requested, a CPF-shaped id) be dropped."""
    return isinstance(value, str) and bool(value) and value == summary_ref


#: The CLOSED projection for the FHIR summary: allowlisted key -> its value-admission predicate.
#: Every key the DOSSIER/PROMPT actually consumes from the summary is here — which is, by
#: inspection of `dossier_prompt()` and `_build_dossier`/`_facts`, NONE of them: the prompt's
#: five-part structure (resumo do caso / evidencia referenciada / indicadores / lacunas / pontos
#: de atencao) never names a FHIR summary field, and `SP-OP-FRAUDE-001.md` mentions no summary
#: variable at all (`grep -niE 'resumo|summary|fhir'` on the contract: no match). The two keys
#: below are therefore admitted purely as PROVENANCE — "a summary WAS read, for THIS subject" —
#: which is what distinguishes an enriched turn from the `resumo_fhir_indisponivel`/
#: `fhir_reader_nao_configurado` gap notes. Anything else is stripped.
_SUMMARY_PROJECTION: dict[str, Callable[[Any, str], bool]] = {
    "resourceType": _admits_resource_type,
    "id": _admits_subject_id,
}

#: Derived from `_SUMMARY_PROJECTION` so the allowlist and the admission rules can never drift.
_SUMMARY_ALLOWED_KEYS: frozenset[str] = frozenset(_SUMMARY_PROJECTION)


class PatientSummaryReader(Protocol):
    """Best-effort FHIR summary-read seam (`gather`). See module docstring's labeled boundary."""

    async def read_patient_summary(self, patient_id: str) -> dict[str, Any]: ...


# --- Graph state (working memory; ADR-0002) ----------------------------------------------------


class BeatrizState(TypedDict, total=False):
    """Investigation-case state. Every field is pseudonymized (ADR-0006): `entidade_pseudo_id`/
    `beneficiario_pseudo_id`/`prestador_id` NEVER carry raw CPF/CNPJ/nome/CNS. The indicators
    and score arrive PRE-RESOLVED by the `operadora.fraude.score_indicators` worker — Beatriz
    CONSUMES them as assembly facts, never computes them, never turns them into a verdict.

    Deliberately ABSENT channels (L0 hard): `decisao_fraude`, `bundle_root`, `destino_referral`
    and every accusation/referral variable of the contract — this graph's state cannot even
    transport them (`test_state_has_no_decision_channels`).
    """

    # --- Caller inputs (contract SP-OP-FRAUDE-001 §Variaveis de entrada) ---
    tenant_id: str
    numero_caso: str
    origem_encaminhamento: str  # contas|recurso|reembolso|cancel|nip|cdc_proativo|denuncia|auditoria
    entidade_tipo: EntidadeTipo
    entidade_pseudo_id: str
    prestador_id: str
    beneficiario_pseudo_id: str
    numero_contrato: str
    encaminhado_por_id: str  # the Phase-2 HUMAN who set encaminhar_fraude (provenance)
    competencia: str
    evidencia_refs: list[dict[str, Any]]  # inbound pointers — normalized by `gather`, never raw PHI
    feature_snapshot_ref: str
    indicadores_presentes: list[str]  # PRE-RESOLVED by worker (assembly fact, NEVER verdict)
    score_indicadores: int  # PRE-RESOLVED by worker (ROUTING FACT, NEVER verdict)
    intensidade_investigacao: Intensidade  # engine-side BRT_Indicadores output (pre-resolved)
    indicio_fraude_sinalizado: bool  # informative Phase-2 signal (NEVER decides)
    patient_summary_ref: str  # FHIR reference for dossier enrichment (never raw PHI)

    # --- Output-only fields (produced EXCLUSIVELY by this graph's nodes; reset at `receive`) ---
    business_key: str  # FRAUDE-{tenant}-{numero_caso} (re-derived every turn)
    gathered: bool
    summary_facts: dict[str, Any]  # CLOSED projection of the FHIR summary (BEA-06, never verbatim)
    evidencia_normalizada: list[dict[str, Any]]  # validated pointer PROJECTIONS (custody-bound)
    gather_notes: list[str]  # bounded class tokens only (dossier lacunas)
    dossier: dict[str, Any]  # the assembled instruction (NEVER a decision)
    desfecho: Desfecho
    error: str  # bounded class token only (technical failure — never an adverse outcome)


# --- Helpers -----------------------------------------------------------------------------------


def _business_key(state: BeatrizState) -> str:
    """Idempotent business key per contract: `FRAUDE-{tenant_id}-{numero_caso}`."""
    return f"FRAUDE-{state.get('tenant_id', '')}-{state.get('numero_caso', '')}"


# --- State sanitization (caller-planted-output class, closed at the single entry) --------------

#: The ONLY fields a caller may legitimately seed on the initial state (runtime identifiers +
#: SP-OP-FRAUDE-001 contract inputs + worker-pre-resolved facts + the gather reference).
#: Everything else in `BeatrizState` is OUTPUT-ONLY: produced exclusively by this graph's own
#: nodes. Single-sourced against `BeatrizState` by the partition-completeness regression test
#: (`test_output_field_partition_is_complete`) — a new state field MUST be classified into
#: exactly one of the two sets or that test fails, so the sanitization below can never silently
#: drift out of date.
_CALLER_INPUT_FIELDS: frozenset[str] = frozenset(
    {
        "tenant_id",
        "numero_caso",
        "origem_encaminhamento",
        "entidade_tipo",
        "entidade_pseudo_id",
        "prestador_id",
        "beneficiario_pseudo_id",
        "numero_contrato",
        "encaminhado_por_id",
        "competencia",
        "evidencia_refs",
        "feature_snapshot_ref",
        "indicadores_presentes",
        "score_indicadores",
        "intensidade_investigacao",
        "indicio_fraude_sinalizado",
        "patient_summary_ref",
    }
)


def _output_field_resets() -> dict[str, Any]:
    """Fresh (never-shared) neutral defaults for EVERY output-only `BeatrizState` field —
    merged unconditionally at `receive` entry, on ALL paths (module docstring
    §CALLER-PLANTED-OUTPUT SANITIZATION).

    A caller-planted accusation-shaped `dossier`, forged `evidencia_normalizada`, planted
    `desfecho="dossie_instruido"`, planted `error`, or forged `business_key` is cleared here
    before any other node reads it — only node-produced values can reach the custody-bound
    dossier afterwards. Built fresh per call (function, not module constant) so the mutable
    `{}`/`[]` defaults are never shared across graph invocations.

    `desfecho` deliberately resets to `"instrucao_incompleta"` (the fail-safe): if
    `instruct_investigation` were ever skipped, the turn reads as an INCOMPLETE instruction —
    never as a planted "dossier assembled". `business_key` resets to `""` and is re-derived
    from the input identifiers on the happy path only.
    """
    return {
        "business_key": "",
        "gathered": False,
        "summary_facts": {},
        "evidencia_normalizada": [],
        "gather_notes": [],
        "dossier": {},
        "desfecho": "instrucao_incompleta",
        "error": "",
    }


def _normalize_evidence(
    evidencia_refs: list[Any],
) -> tuple[list[dict[str, Any]], list[str]]:
    """Normalize inbound evidence to validated pointer PROJECTIONS (no-PHI-in-custody).

    Per item: (1) must be a dict with a non-empty string `ref` — else refused; (2) any known
    raw-PHI key present -> the WHOLE item is refused (upstream contamination surfaces as a
    lacuna, never laundered); (3) survivors are PROJECTED to the closed allowlist
    `{ref, hash, tipo, origem}` with string values only — unknown keys (which could smuggle raw
    PHI in their values) are STRIPPED. Refusals are recorded as index-only class tokens
    (`evidencia_recusada:<idx>`) — item VALUES never reach the notes (custody hygiene).
    """
    normalized: list[dict[str, Any]] = []
    refused_notes: list[str] = []
    for idx, item in enumerate(evidencia_refs):
        if not isinstance(item, dict):
            refused_notes.append(f"{NOTE_EVIDENCIA_RECUSADA_PREFIX}:{idx}")
            continue
        ref = item.get("ref")
        if not isinstance(ref, str) or not ref:
            refused_notes.append(f"{NOTE_EVIDENCIA_RECUSADA_PREFIX}:{idx}")
            continue
        if _PHI_KEYS & {str(k).lower() for k in item}:
            refused_notes.append(f"{NOTE_EVIDENCIA_RECUSADA_PREFIX}:{idx}")
            continue
        projected = {
            key: item[key] for key in _POINTER_ALLOWED_KEYS if isinstance(item.get(key), str) and item[key]
        }
        normalized.append(projected)
    return normalized, refused_notes


def _has_phi_key(value: Any, *, _depth: int = 0) -> bool:
    """True if `value` carries ANY known raw-PHI key name, RECURSIVELY (dicts and lists/tuples).

    Single-sources the vocabulary from `_PHI_KEYS` — the same set `_normalize_evidence` refuses
    on, and the set `platform/validation/phi_completeness.py` already cites this module for
    (`cns`/`rg`/`endereco`) — so there is exactly ONE PHI key vocabulary for Beatriz, never a
    third copy. Matching folds case (`CPF` == `cpf`). Recursion matters because that is how a
    real FHIR payload carries an identifier (`Bundle.entry[].resource.subject.cpf`); a
    top-level-only check would wave it straight through.

    FAIL-CLOSED at `_MAX_PHI_SCAN_DEPTH`: a payload nested deeper than any legitimate summary
    (or self-referential) is reported as CONTAMINATED rather than scanned further — the scan
    can never raise `RecursionError` onto `gather`'s hot path, and can never conclude "clean"
    about a region it did not read.
    """
    if _depth > _MAX_PHI_SCAN_DEPTH:
        return True
    if isinstance(value, dict):
        if _PHI_KEYS & {str(key).lower() for key in value}:
            return True
        return any(_has_phi_key(item, _depth=_depth + 1) for item in value.values())
    if isinstance(value, (list, tuple)):
        return any(_has_phi_key(item, _depth=_depth + 1) for item in value)
    return False


def _normalize_summary(facts: Any, summary_ref: str) -> tuple[dict[str, Any], list[str]]:
    """Normalize the UPSTREAM FHIR summary to a closed projection (BEA-06) — the summary-side
    twin of `_normalize_evidence`, and for the same reason: the payload is controlled by a
    server upstream of this graph (module docstring §CUSTODY), so it is untrusted input.

    (1) anything but a dict -> refused (bounded token; a reader that answers with a string/list
    is off-contract and its content must never be trusted); (2) any known raw-PHI key present
    anywhere in the structure -> the WHOLE summary is refused, never laundered by projection
    (upstream contamination surfaces as a lacuna the human investigator can see); (3) survivors
    are PROJECTED to `_SUMMARY_ALLOWED_KEYS`, each value additionally admitted only from its own
    CLOSED domain (`_SUMMARY_PROJECTION`) — a key outside the allowlist, or an allowlisted key
    whose value is free content, is STRIPPED silently (same posture as `_normalize_evidence`'s
    unknown-key strip: a stripped key is not an incident, a PHI key is).

    Refusals are recorded as the bounded class token `resumo_fhir_recusado` — the payload's
    VALUES never reach the notes (custody hygiene; those notes land in the sealed dossier's
    `lacunas`).
    """
    if not isinstance(facts, dict):
        return {}, [NOTE_RESUMO_FHIR_RECUSADO]
    if _has_phi_key(facts):
        return {}, [NOTE_RESUMO_FHIR_RECUSADO]
    projected = {
        key: facts[key]
        for key, admits in _SUMMARY_PROJECTION.items()
        if key in facts and admits(facts[key], summary_ref)
    }
    return projected, []


def _score_consumed(state: BeatrizState) -> tuple[int | None, str | None]:
    """The pre-resolved worker score, CONSUMED defensively — never derived, never recomputed.

    BEA-04 (fleet audit ciclo 2): anything but a genuine int (bool excluded — a bool is an int
    subclass) used to collapse silently to `0`, a value indistinguishable from a real "zero
    indicators" fact in the sealed dossier a human investigator reads. It now REJECTS to `None`
    via `maezo.runtime.guards.require_number`, returning the class-token lacuna
    (`NOTE_SCORE_AUSENTE`/`NOTE_SCORE_INVALIDO`) the caller must fold into `lacunas` — a
    corrupted/missing worker score becomes a visible data-quality signal, never a routing fact
    that reads as "investigacao de baixo indicio". No code path in this module performs
    arithmetic over the evidence to produce a score (the donor's `len(evidencia)*10` heuristic
    was defect B10, deleted in T2.7 — see module docstring).
    """
    notes: list[str] = []
    score = require_number(state.get("score_indicadores"), field="score_indicadores", notes=notes)
    return score, (notes[0] if notes else None)


#: Fatos BOOLEANOS deste fluxo, com o nome que o humano de destino reconhece (CC-11).
#:
#: Incidente de 24/08/2026 (contado por inteiro em `agents/rafael/graph.py::_FATOS_BOOLEANOS`):
#: fatos passados ao modelo como repr de dicionario deixam `False` e `None` com a mesma cara de
#: "vazio", e um fato APURADO-e-desfavoravel vira "nao ha registro". O conserto ficou num agente
#: so' ate' a auditoria da frota; este mapa e' a adocao aqui. Chave -> rotulo; a ORDEM e' a ordem
#: das linhas no prompt. So' entram fatos declarados `bool` no state — nada que seja enum/str.
_FATOS_BOOLEANOS: Final[dict[str, str]] = {
    "indicio_fraude_sinalizado": "indicio de fraude sinalizado",
}


class BeatrizGraph:
    """Wires Beatriz's injected dependencies into a compilable `StateGraph[BeatrizState]`."""

    def __init__(
        self,
        *,
        inference: InferenceProvider,
        fhir: PatientSummaryReader | None = None,
        agent_version: str = "beatriz@v0",
    ) -> None:
        self._llm = inference
        self._fhir = fhir
        self._agent_version = agent_version

    # -- Nodes ----------------------------------------------------------------------------

    async def receive(self, state: BeatrizState) -> dict[str, Any]:
        """Turn start — the SINGLE graph entry (one edge START->receive). Idempotent.

        SANITIZATION FIRST: every output-only field is reset (`_output_field_resets`) on BOTH
        paths before anything else — an inbound `dossier`/`desfecho`/`error`/forged pointer
        list is a caller plant, never trusted (module docstring §CALLER-PLANTED-OUTPUT
        SANITIZATION).

        Defense: never proceeds without the minimum contract identifiers (tenant + numero do
        caso) — without them there is no idempotent business key (`FRAUDE-{tenant}-{caso}`) to
        anchor the dossier to. Fail-safe: the reset `desfecho="instrucao_incompleta"` stands and
        `error` records the BOUNDED class token (never an echoed value) — NEVER an adverse
        outcome; SP-OP-FRAUDE-001 routes every desfecho to the human investigator by design.
        """
        sanitized = _output_field_resets()
        if not state.get("tenant_id") or not state.get("numero_caso"):
            return {**sanitized, "error": ERROR_CONTEXTO_RUNTIME_AUSENTE}
        return {**sanitized, "business_key": _business_key(state)}

    async def gather(self, state: BeatrizState) -> dict[str, Any]:
        """Collect the FHIR summary (best-effort) and NORMALIZE the evidence to pointer
        projections (no-PHI-in-custody). NEVER blocks the instruction path.

        BOTH inbound corpora are projected before they land in state: the evidence through
        `_normalize_evidence` and the UPSTREAM-CONTROLLED FHIR summary through
        `_normalize_summary` (BEA-06 — module docstring §CUSTODY). Nothing a reader or a caller
        supplied ever reaches `summary_facts`/`evidencia_normalizada` verbatim.

        The pre-resolved indicators/score are NOT touched here — they are consumed as facts by
        `instruct_investigation` (never recomputed; module docstring's L0-hard invariant).
        """
        if state.get("error"):
            # Post-`receive`-sanitization a truthy `error` can ONLY have been set by `receive`'s
            # own missing-context guard — never by the caller. Re-assert the fail-safe outcome
            # (defense in depth): this bail must NEVER return `{}` nor a "dossier assembled"
            # signal for an unanchorable case.
            return {"desfecho": "instrucao_incompleta"}

        notes: list[str] = []
        summary_facts: dict[str, Any] = {}

        if self._fhir is None:
            notes.append(NOTE_FHIR_READER_NAO_CONFIGURADO)
        else:
            summary_ref = state.get("patient_summary_ref") or state.get("beneficiario_pseudo_id", "")
            if summary_ref:
                try:
                    raw_summary: Any = await self._fhir.read_patient_summary(summary_ref)
                except PROGRAMMING_ERRORS:
                    raise
                except EXTERNAL_DEPENDENCY_FAILURES:  # best-effort enrichment; class token only.
                    notes.append(NOTE_RESUMO_FHIR_INDISPONIVEL)
                else:
                    # BEA-06: the reader's payload is UPSTREAM-CONTROLLED and NEVER lands in
                    # state verbatim — it is projected/refused FIRST (module docstring
                    # §CUSTODY). Fail-closed by construction: `summary_facts` keeps its empty
                    # default on every path that does not produce a projection.
                    summary_facts, refused_summary = _normalize_summary(raw_summary, summary_ref)
                    notes.extend(refused_summary)

        inbound = state.get("evidencia_refs") or []
        normalized, refused_notes = _normalize_evidence(list(inbound))
        notes.extend(refused_notes)
        if not inbound:
            # Contract §Business key: the dossier may grow by annexation (msg.fraude.
            # evidencia_anexada) — an empty intake is a lacuna, never a failure.
            notes.append(NOTE_SEM_EVIDENCIA_NO_INTAKE)

        return {
            "gathered": True,
            "summary_facts": summary_facts,
            "evidencia_normalizada": normalized,
            "gather_notes": notes,
        }

    async def instruct_investigation(self, state: BeatrizState) -> dict[str, Any]:
        """The single substantive node (Rafael/Beatriz principle): assemble the investigation
        dossier. INSTRUCTS, NEVER decides.

        L0 HARD (zero_auto_accusation): this node NEVER sets `decisao_fraude`, NEVER accuses,
        NEVER recommends an adverse effect. It organizes the FACTS (validated evidence pointers
        + OBSERVED indicators + the score as a routing fact) into the dossier the
        `seal_custody_bundle` worker will seal and the human investigator will decide over in
        `UT_DecisaoInvestigador`. The dossier's `decisao_fraude`/`bundle_root`/
        `destino_referral` fields are ALWAYS `None` here — if any ever weren't, it is an L0 bug
        (tested). A high score does NOT change behavior — there is no conditional branch in
        this graph at all; the only possible outputs are the assembled dossier or the explicit
        incomplete marker.
        """
        if state.get("error"):
            # Unanchorable case (receive's guard): NO dossier is assembled — see the module
            # docstring's disclosed divergence from the donor. Fail-safe re-assert, never `{}`.
            return {"desfecho": "instrucao_incompleta"}
        dossier = await self._build_dossier(state)
        return {"dossier": dossier, "desfecho": "dossie_instruido"}

    async def finalize(self, state: BeatrizState) -> dict[str, Any]:
        """Terminal node — no further computation; `desfecho` was already set upstream.

        No episodic memory write here (labeled boundary, module docstring — same rationale as
        Helena's/Rafael's/Marina's graphs). NEVER accuses, NEVER seals, NEVER triggers a
        downstream handoff — sealing and decision belong to the engine workers and the human.

        CC-09: emits ONE `maezo_agent_desfecho_total` for this turn. Beatriz has no `route` and
        never calls `start_process` (L0 structural — no conditional edge exists), so `route` and
        `start_failed` are always absent/`False` here; only `desfecho` (`dossie_instruido` |
        `instrucao_incompleta`) carries signal.
        """
        emit_turn_desfecho(state, agent_id="beatriz")
        return {}

    # -- Dossier assembly (ADR-0007 audit provenance; L0-hard structural guardrail) -------------

    async def _build_dossier(self, state: BeatrizState) -> dict[str, Any]:
        """Assemble the investigator's dossier. The LLM reasons over the FACTS to write the
        narrative; it never decides — and its output lands ONLY in `narrativa` (free text for
        the human), never in a decision/verdict field. LLM failure never blocks the
        instruction: the dossier degrades to an empty narrative + a bounded lacuna token."""
        facts = self._facts(state)
        lacunas = list(state.get("gather_notes") or [])
        score_indicadores, score_lacuna = _score_consumed(state)
        if score_lacuna:
            lacunas.append(score_lacuna)

        prompt = f"{dossier_prompt()}\n\n{render_fatos_para_prompt(facts, booleanos=_FATOS_BOOLEANOS)}"
        try:
            narrativa = await self._llm.generate(
                prompt,
                phi=True,
                agent_id="beatriz",
                tenant_id=state.get("tenant_id", ""),
                # ADR-0009 §2 / CC-12: dossie lido pelo humano antes de decidir -> reasoning.
                task_kind="reasoning",
            )
        except PROGRAMMING_ERRORS:
            raise
        except EXTERNAL_DEPENDENCY_FAILURES:  # LLM failure never blocks the human-bound instruction.
            narrativa = ""
            lacunas.append(NOTE_NARRATIVA_INDISPONIVEL)

        return {
            "prompt_version": DOSSIER_PROMPT_VERSION,
            "business_key": state.get("business_key", ""),
            "numero_caso": state.get("numero_caso"),
            "origem_encaminhamento": state.get("origem_encaminhamento"),
            "entidade_tipo": state.get("entidade_tipo"),
            "entidade_pseudo_id": state.get("entidade_pseudo_id"),
            "encaminhado_por_id": state.get("encaminhado_por_id"),  # provenance (ADR-0007)
            "competencia": state.get("competencia"),
            "fatos": facts,
            # Evidence: ONLY the validated pointer projections from `gather` (no-PHI-in-custody).
            # These are the items `seal_custody_bundle` will order and seal into `bundle_root`.
            "evidencia_refs": state.get("evidencia_normalizada") or [],
            "feature_snapshot_ref": state.get("feature_snapshot_ref"),
            # Indicators OBSERVED (assembly/routing fact — NEVER a verdict).
            "indicadores_observados": list(state.get("indicadores_presentes") or []),
            "score_indicadores": score_indicadores,
            "intensidade_investigacao": state.get("intensidade_investigacao"),
            "indicio_fraude_sinalizado": bool(state.get("indicio_fraude_sinalizado", False)),
            "narrativa": narrativa,
            "lacunas": lacunas,
            # STRUCTURAL GUARDRAIL (L0 hard, zero_auto_accusation): the dossier NEVER carries a
            # decision/accusation/seal. These exist ONLY to make it explicit that the decision
            # is the human's and the seal is the engine worker's — ALWAYS None here (tested; a
            # non-None value would be an L0 bug).
            "decisao_fraude": None,  # accusation/archive/monitor: SOLELY UT_DecisaoInvestigador
            "bundle_root": None,  # sealing: SOLELY the seal_custody_bundle engine worker
            "destino_referral": None,  # referral: SOLELY downstream of a HUMAN accusation
        }

    @staticmethod
    def _facts(state: BeatrizState) -> dict[str, Any]:
        """Deterministic fact sheet (pseudonymized identifiers + pre-resolved worker facts +
        counts). `n_evidencia` counts the VALIDATED pointers — it is a count, never a score.

        BEA-04: a rejected `score_indicadores` folds its class-token lacuna into THIS sheet's
        own `lacunas` — the LLM prompt this feeds (`render_fatos_para_prompt`) must show the
        gap, not a silent `0` indistinguishable from a genuine low-indicator case."""
        score_indicadores, score_lacuna = _score_consumed(state)
        lacunas = list(state.get("gather_notes") or [])
        if score_lacuna:
            lacunas.append(score_lacuna)
        return {
            "numero_caso": state.get("numero_caso"),
            "tenant_id": state.get("tenant_id"),
            "origem_encaminhamento": state.get("origem_encaminhamento"),
            "entidade_tipo": state.get("entidade_tipo"),
            "entidade_pseudo_id": state.get("entidade_pseudo_id"),
            "prestador_id": state.get("prestador_id"),
            "beneficiario_pseudo_id": state.get("beneficiario_pseudo_id"),
            "numero_contrato": state.get("numero_contrato"),
            "competencia": state.get("competencia"),
            "n_evidencia": len(state.get("evidencia_normalizada") or []),
            "indicadores_presentes": list(state.get("indicadores_presentes") or []),
            "score_indicadores": score_indicadores,
            "intensidade_investigacao": state.get("intensidade_investigacao"),
            "indicio_fraude_sinalizado": bool(state.get("indicio_fraude_sinalizado", False)),
            "feature_snapshot_ref": state.get("feature_snapshot_ref"),
            "resumo_fhir": state.get("summary_facts") or {},
            "lacunas": lacunas,
        }

    # -- Graph assembly -----------------------------------------------------------------------

    def compile_graph(self) -> StateGraph[BeatrizState]:
        """Linear single-entry graph: receive -> gather -> instruct_investigation -> finalize.

        There is NO `add_conditional_edges` call and NO adverse node/route: the graph can only
        assemble and deliver the dossier. The ABSENCE of any accusation node/edge/branch is the
        L0 structural guarantee (`zero_auto_accusation`) — tested by
        `test_graph_is_linear_single_entry_no_conditional_edges`.
        """
        g: StateGraph[BeatrizState] = StateGraph(BeatrizState)
        g.add_node("receive", self.receive)
        g.add_node("gather", self.gather)
        g.add_node("instruct_investigation", self.instruct_investigation)
        g.add_node("finalize", self.finalize)

        g.add_edge(START, "receive")
        g.add_edge("receive", "gather")
        g.add_edge("gather", "instruct_investigation")
        g.add_edge("instruct_investigation", "finalize")
        g.add_edge("finalize", END)
        return g


def build(config: dict[str, Any] | None = None) -> StateGraph[BeatrizState]:
    """Contract `_template/graph.py:build`, resolved by `AgentLoader`/`runtime.harness.Harness`.

    `config` MUST contain `inference` (ADR-0009). `fhir` is OPTIONAL (module docstring's labeled
    boundary) — its absence never fails the build, only degrades `gather` to a disclosed gap
    note. `dmn`/`cibseven` (which the harness's shared `tool_deps` may carry for the
    rafael/marina-shaped graphs) are DELIBERATELY ignored: per the R1-audited
    `spec/agents/beatriz/agent.yaml`, Beatriz evaluates no DMN (the scoring chain is
    worker/engine-side) and starts no process (the engine convokes her) — accepting those
    transports here would silently widen her action surface beyond the agent.yaml allowlist.

    Fail-closed: a missing REQUIRED dependency raises `ValueError` at build time.
    """
    cfg = config or {}
    inference = cfg.get("inference")
    if inference is None:
        raise ValueError(
            "Beatriz build(config) is missing required dependencies: ['inference'] "
            "(ADR-0009 — the inference provider must be injected)"
        )
    agent_version = str(cfg.get("agent_version", "beatriz@v0"))
    return BeatrizGraph(
        inference=cast(InferenceProvider, inference),
        fhir=cast("PatientSummaryReader | None", cfg.get("fhir")),
        agent_version=agent_version,
    ).compile_graph()


# Prompt versions exposed for audit/eval gating (ADR-0007/0009).
PROMPT_VERSIONS: dict[str, str] = {
    "system": SYSTEM_PROMPT_VERSION,
    "dossier": DOSSIER_PROMPT_VERSION,
}

"""Helena Moreira — Health Navigator Agent (Phase 0, AGJ-HELENA-TRIAGE, T1.11/defect B6).

Journey map (mirrors the v1 donor's `AGJ-HELENA-TRIAGE`, READ-ONLY reference
`Maezo-Healthcare-Plan src/maezo/agents/helena/graph.py`, adapted to v2's flatter seam set —
v2 has no `ToolRegistry`/PEP-gateway wiring for agent tool calls yet, so this graph's nodes call
the seams already on `main` DIRECTLY: `DmnTransport.evaluate` (ADR-0028/T1.5), `InferenceProvider.
generate(phi=True)` (ADR-0009/T1.7), and the new `CibSevenTransport` (ADR-0001, T1.11 —
`tools/mcp_cibseven/transport.py`)):

    receive -> classify -> {inform | schedule | escalate} -> respond

`classify` ALWAYS evaluates the red-flag DMN when the message describes a symptom (ADR-0012:
the LLM extracts + normalizes `sintoma_codigo`/`intensidade`/a population field; the DMN decides
`red_flag`/`conduta`/`prioridade` — the LLM never decides). `conduta=ESCALATE_*` or `red_flag`
true -> `escalate`, which starts SP-OP-ESCALATION-001 (idempotent, business key
`ESC-{tenant}-{conversation_id}`) via the CibSeven transport.

Five escalation triggers (each maps to a distinct `motivo_categoria`, contract
SP-OP-ESCALATION-001):
1. DMN red_flag=true                       -> red_flag_clinico (or risco_psicossocial if the
                                               mental_health table fired)
2. intent = clinical question              -> intencao_clinica (L0 hard — Helena never answers)
3. explicit request for a human            -> solicitacao_humano
4. technical failure                       -> falha_tecnica — DMN down/no-result, OR any
                                               classify-LLM failure: exception, unparseable
                                               JSON, or schema-invalid JSON (unknown intent,
                                               invalid population, non-allow-listed
                                               sintoma_codigo, out-of-domain intensidade).
                                               R1 cycle-1 blocking fix: pre-fix, a classify
                                               failure silently defaulted to intent=
                                               "information" -> inform (fail-OPEN,
                                               live-reproduced by the verifier); it now
                                               escalates, symmetric with every other failure
                                               path in this graph.
5. psychosocial risk in ANY message        -> risco_psicossocial (always evaluated, highest
                                               priority — never gated behind `intent`)

L0 HARD INVARIANT: Helena NEVER resolves a clinical concern herself. Every path ends in either
a human task (`escalate` -> SP-OP-ESCALATION-001's `UT_TratarEscalonamento`) or an explicit,
non-clinical response (`inform`/`schedule`) drafted by an LLM that is instructed to never give
clinical guidance (see `prompts.py`).

PHI discipline (ADR-0006/ADR-0017, T1.7's gate): every LLM call in this module passes
`phi=True` — inbound WhatsApp free text is treated as PHI-adjacent content even after the
webhook-edge phone-number pseudonymization, so it may ONLY be served by a `phi_capable`
provider (`phi_zone_mock` in dev; a real BR-resident endpoint is blocked(external), T1.7
charter). A non-PHI-capable provider raises `PhiZoneRoutingError` — this graph does NOT catch
that error specially: in `_classify_llm` it is a classify failure -> escalate `falha_tecnica`
(trigger 4, human takes over); in `_respond_llm`/`_resumo_contexto` (pure text drafting, the
route is already decided) it degrades to a safe canned text. Neither path ever silently
downgrades to a general-zone provider.

LABELED BOUNDARIES (this build, disclosed — never fabricated):
- FHIR patient/coverage enrichment (`mcp-fhir.read_patient_summary`/`read_coverage`/
  `search_coverage` in `spec/agents/helena/agent.yaml`) is NOT wired in this graph. The contract
  SP-OP-ESCALATION-001 build steps in the T1.11 charter do not require it, and v2's `FhirServer`
  is a generic HAPI client with no PEP/ToolRegistry gateway yet (a real gap, not hidden here) —
  wiring it is a follow-up.
- Episodic memory write (`mcp-memory.read_write`, ADR-0002) is NOT wired in this graph. v2's
  `MemoryServer` requires a live Postgres/pgvector schema; adding it is a follow-up once that
  schema exists in this repo's migrations.
- Free-text WhatsApp message content is NOT scanned for embedded PHI patterns (e.g. a
  beneficiary typing their own CPF into the message) before reaching the LLM — mitigated by the
  mandatory `phi=True` routing (content never reaches a general-zone cloud provider), but a
  dedicated free-text scrubber does not exist in v2 yet; this is a follow-up, not built here.
- No multi-turn conversation checkpointing across separate webhook deliveries: each inbound
  WhatsApp message runs this graph as ONE complete turn (receive..respond) with no LangGraph
  checkpointer attached. Cross-turn memory is a follow-up (ADR-0002's episodic/semantic layers).
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from typing import Any, Literal, Protocol, TypedDict, cast

import structlog
from langgraph.graph import END, START, StateGraph

from maezo.runtime.inference import InferenceProvider
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
    ALLOWED_SINTOMA_CODIGOS,
    CLASSIFY_PROMPT_VERSION,
    RESPONSE_PROMPT_VERSION,
    SYSTEM_PROMPT_VERSION,
    classify_prompt,
    response_prompt,
)

logger = structlog.get_logger(__name__)

# --- Domain enums (mirror the SP-OP-ESCALATION-001 contract + DMN schema) -------------------

Intent = Literal["symptom", "scheduling", "information", "human_request", "clinical_question"]
Population = Literal["adult", "pediatric", "gestante", "mental_health", "none"]
ResponseKind = Literal["inform", "schedule", "escalate"]

# Classify-output schema domains (classify-v1's own contract) — the R1 cycle-1 fail-closed
# validator (`_validate_extraction`) checks membership against these. Kept as explicit
# frozensets (not `typing.get_args` derivations) so the validation surface is self-contained
# and greppable next to the Literal types it mirrors.
_VALID_INTENTS: frozenset[str] = frozenset(
    {"symptom", "scheduling", "information", "human_request", "clinical_question"}
)
_VALID_POPULATIONS: frozenset[str] = frozenset({"adult", "pediatric", "gestante", "mental_health", "none"})
_VALID_INTENSIDADES: frozenset[str] = frozenset({"leve", "moderada", "grave", "desconhecida"})

MotivoCategoria = Literal[
    "red_flag_clinico",
    "risco_psicossocial",
    "intencao_clinica",
    "solicitacao_humano",
    "falha_tecnica",
    "outro",
]
Severidade = Literal["grave", "moderada", "leve"]

PROCESS_KEY = "SP-OP-ESCALATION-001"

# DMN table per population (contract SP-OP-ESCALATION-001 + spec/processes/dmn/triage_redflag_*).
_DMN_BY_POPULATION: dict[str, str] = {
    "adult": "triage_redflag_adult",
    "pediatric": "triage_redflag_pediatric",
    "gestante": "triage_redflag_gestante",
    "mental_health": "triage_redflag_mental_health",
}


class WhatsAppSender(Protocol):
    """Outbound WhatsApp send seam. Operates on a phone HASH, never a raw number — Helena's
    state is pseudonymized end to end (ADR-0006); resolving the hash back to a real number for
    actual delivery is the DISPATCH layer's job (it has the raw number in-hand for the inbound
    turn it is currently handling), never this graph's."""

    async def send(self, to_hash: str, text: str) -> dict[str, Any]: ...


# --- Graph state (working memory; ADR-0002 working-memory layer) ---------------------------


class HelenaState(TypedDict, total=False):
    """Conversation state. Every field here is pseudonymized (Zona Geral, ADR-0006) —
    `beneficiario_pseudo_id` NEVER carries a raw CPF/name/phone number."""

    # Runtime identifiers (injected by the dispatch layer at turn start).
    tenant_id: str
    conversation_id: str
    canal: str  # whatsapp | portal | telefone
    beneficiario_pseudo_id: str

    # Turn input (free text already pseudonymized at the webhook edge — sender identity only;
    # see module docstring's labeled boundary on embedded-PHI-in-free-text scanning).
    message_body: str

    # Filled by `classify`.
    intent: Intent
    population: Population
    psychosocial_risk: bool
    sintoma_codigo: str | None
    intensidade: str
    idade_anos: int | None
    idade_meses: int | None
    idade_gestacional_semanas: int | None
    risco_imediato: bool | None
    dmn_table: str
    dmn_decision: dict[str, Any]
    dmn_decision_ref: str

    # Routing.
    next_kind: ResponseKind
    escalation_motivo: MotivoCategoria
    escalation_severidade: Severidade

    # `escalate` outputs.
    escalation_started: bool
    escalation_business_key: str
    escalation_process_ref: dict[str, Any]

    # Turn output.
    response_text: str
    response_kind: ResponseKind
    error: str


# --- Input/output field split + input-boundary gate (T1.11 caller-planted read-through fix) ---
#
# HelenaState carries TWO disjoint classes of key:
#   * INPUT-ONLY  (`HELENA_INPUT_FIELDS`): the ONLY keys a caller/upstream/dispatch seam may set.
#   * OUTPUT-ONLY (`_HELENA_NEUTRAL_OUTPUTS`): keys OWNED by this graph's nodes. A caller must
#     NEVER set one — a planted output field is an injection (forged routing, forged escalation
#     motivo/severidade, forged ADR-0007 `dmn_decision_ref` provenance, or an anti-escalation
#     `next_kind`/`error` that suppresses a red flag).
#
# TWO defenses, both fail-closed:
#   1. Per-graph entry sanitization — `receive` resets EVERY output-only field to its neutral
#      default before any downstream node runs, so a planted value cannot be read even if it
#      reached the state dict (see `HelenaGraph.receive`).
#   2. Input-boundary gate — the production construction seam(s) assemble state ONLY through the
#      typed `new_helena_state` constructor or the `gate_inbound_state` allowlist filter, so an
#      output-only key can never enter the state dict in the first place. This is the durable,
#      class-killing layer: the read-throughs stay unreachable even if a future node regresses.
#
# The `_completeness` guard below fails at import time if a newly added HelenaState field is not
# classified into exactly one of the two sets — "any missed key is a hole".

HELENA_INPUT_FIELDS: frozenset[str] = frozenset(
    {"tenant_id", "conversation_id", "canal", "beneficiario_pseudo_id", "message_body"}
)

# Neutral default for every OUTPUT-ONLY field. `receive` writes a copy of this over the incoming
# state so nothing a caller planted survives to a downstream read. Every value here is immutable
# (scalars / None) — safe to share across turns via a shallow copy.
_HELENA_NEUTRAL_OUTPUTS: dict[str, Any] = {
    "intent": None,
    "population": None,
    "psychosocial_risk": False,
    "sintoma_codigo": None,
    "intensidade": None,
    "idade_anos": None,
    "idade_meses": None,
    "idade_gestacional_semanas": None,
    "risco_imediato": None,
    "dmn_table": None,
    "dmn_decision": None,
    "dmn_decision_ref": None,
    "next_kind": "inform",
    "escalation_motivo": None,
    "escalation_severidade": "leve",
    "escalation_started": False,
    "escalation_business_key": None,
    "escalation_process_ref": None,
    "response_text": None,
    "response_kind": None,
    "error": None,
}

_HELENA_ALL_FIELDS = HELENA_INPUT_FIELDS | frozenset(_HELENA_NEUTRAL_OUTPUTS)
if frozenset(HelenaState.__annotations__) != _HELENA_ALL_FIELDS:
    _missing = frozenset(HelenaState.__annotations__) - _HELENA_ALL_FIELDS
    _extra = _HELENA_ALL_FIELDS - frozenset(HelenaState.__annotations__)
    raise RuntimeError(
        "HelenaState input/output field split is incomplete (T1.11 input-boundary gate): "
        f"unclassified fields={sorted(_missing)} stale entries={sorted(_extra)} — every "
        "HelenaState key MUST be either an INPUT field or carry a neutral output default."
    )


def new_helena_state(
    *,
    tenant_id: str,
    conversation_id: str,
    canal: str,
    beneficiario_pseudo_id: str,
    message_body: str,
) -> HelenaState:
    """Typed input-boundary constructor for a fresh Helena turn (T1.11).

    This is the production construction seam's ONLY sanctioned way to build a `HelenaState`: its
    explicit keyword-only signature makes it STRUCTURALLY impossible to pass an output-only key
    through it (a forged `next_kind`/`error`/`escalation_*`/`dmn_decision_ref`). Every accepted
    argument is an `HELENA_INPUT_FIELDS` member.
    """
    return {
        "tenant_id": tenant_id,
        "conversation_id": conversation_id,
        "canal": canal,
        "beneficiario_pseudo_id": beneficiario_pseudo_id,
        "message_body": message_body,
    }


def gate_inbound_state(raw: Mapping[str, Any]) -> HelenaState:
    """Fail-closed input allowlist for seams that receive a raw mapping (A2A/delegation, future).

    Only `HELENA_INPUT_FIELDS` keys survive; EVERY other key — i.e. any caller-planted output
    field — is DROPPED (never reaches a downstream node) and logged. Use this at any seam that
    assembles Helena state from an untrusted/upstream dict; use `new_helena_state` where the
    input scalars are already in hand (the dispatch path).
    """
    dropped = sorted(k for k in raw if k not in HELENA_INPUT_FIELDS)
    if dropped:
        logger.warning("helena_inbound_output_fields_dropped", dropped=dropped)
    return cast(HelenaState, {k: raw[k] for k in HELENA_INPUT_FIELDS if k in raw})


# --- Helpers ---------------------------------------------------------------------------------


def _business_key(state: HelenaState) -> str:
    """Idempotent business key per contract: `ESC-{tenant_id}-{conversation_id}`."""
    return f"ESC-{state.get('tenant_id', '')}-{state.get('conversation_id', '')}"


def _to_hash_from_state(state: HelenaState) -> str:
    """Extract the phone hash from `conversation_id = wa:{tenant}:{phone_hash}`.

    Falls back to the raw `conversation_id` as an opaque destination handle when the format
    doesn't match (e.g. `canal != whatsapp`) — the sender treats it as an opaque key regardless.
    """
    conv = state.get("conversation_id", "")
    if conv.startswith("wa:"):
        parts = conv.split(":", 2)
        if len(parts) == 3 and parts[2]:
            return parts[2]
    return conv


def _severidade_from_prioridade(prioridade: str) -> Severidade:
    """P1 -> grave; P2 -> moderada; anything else -> leve (contract SP-OP-ESCALATION-001)."""
    if prioridade == "P1":
        return "grave"
    if prioridade == "P2":
        return "moderada"
    return "leve"


_JSON_OBJECT_RE = re.compile(r"\{.*\}", re.DOTALL)


def _parse_json_object(text: str) -> dict[str, Any] | None:
    """Best-effort extraction of a JSON object from an LLM's free-text response.

    `InferenceProvider.generate` returns a plain string (no structured-output mode, unlike the
    v1 donor's `.complete(..., response_schema=...)`) — this defensively locates the first
    `{...}` span and parses it, tolerating stray prose/markdown fencing around the JSON. Returns
    `None` (never raises) on any parse failure — the caller (`_classify_llm`) treats `None` as
    a CLASSIFY FAILURE and routes to escalation (R1 cycle-1 fix), never a benign default.
    """
    match = _JSON_OBJECT_RE.search(text)
    if not match:
        return None
    try:
        data = json.loads(match.group(0))
    except (ValueError, TypeError):
        return None
    return data if isinstance(data, dict) else None


def _validate_extraction(data: dict[str, Any]) -> str | None:
    """Validate the classify LLM's parsed JSON against classify-v1's own schema.

    Returns a CLASS TOKEN failure reason (e.g. `invalid_intent`,
    `non_allowlisted_sintoma_codigo`) or `None` when valid. Any non-`None` return is a
    CLASSIFY FAILURE: the caller routes to escalation `falha_tecnica` (R1 cycle-1 fix — an
    invalid classification must never be silently read as an administrative-info turn).

    R1 CYCLE-2 FIX (leak): the failure reason NEVER carries the offending FIELD VALUE. The
    cycle-1 version echoed `repr(value)[:80]` — raw LLM output — into the reason, which
    `escalate` appends to `resumo_contexto` and ships into ENGINE PROCESS VARIABLES (general
    zone); an LLM copying a beneficiary-typed identifier (e.g. a CPF) into any field would put
    PHI into the engine DB (live-proven by the verifier). Class tokens only — no field values,
    no reprs, no truncation-based mitigation. The offending value is not logged anywhere either
    (the class token alone is sufficient for triage; the beneficiary's own message reaches the
    human attendant through the normal escalation flow).

    Checks (classify-v1's required fields + domains):
    - `intent` present and in `_VALID_INTENTS` -> `invalid_intent`;
    - `population` present and in `_VALID_POPULATIONS` -> `invalid_population`;
    - `psychosocial_risk` present and a real boolean (the always-active gatilho-5 signal must
      never be silently absent/coerced) -> `missing_or_invalid_psychosocial_risk`;
    - `sintoma_codigo` either `null` or in `ALLOWED_SINTOMA_CODIGOS` (an invented code is a
      failure — the DMN tables cannot match it, which would silently bypass every symptom rule
      and land on the no-red-flag catch-all) -> `non_allowlisted_sintoma_codigo`;
    - `intensidade`, when present, in `_VALID_INTENSIDADES` (an out-of-domain intensity would
      miss the DMN's own `"grave"` fail-safe rows the same way an invented code would) ->
      `invalid_intensidade`.
    """
    if data.get("intent") not in _VALID_INTENTS:
        return "invalid_intent"
    if data.get("population") not in _VALID_POPULATIONS:
        return "invalid_population"
    if not isinstance(data.get("psychosocial_risk"), bool):
        return "missing_or_invalid_psychosocial_risk"
    codigo = data.get("sintoma_codigo")
    if codigo is not None and codigo not in ALLOWED_SINTOMA_CODIGOS:
        return "non_allowlisted_sintoma_codigo"
    intensidade = data.get("intensidade")
    if intensidade is not None and intensidade not in _VALID_INTENSIDADES:
        return "invalid_intensidade"
    return None


# --- Graph ------------------------------------------------------------------------------------


class HelenaGraph:
    """Wires Helena's injected dependencies into a compilable `StateGraph[HelenaState]`.

    The dispatch layer (webhook receiver) or the harness's `build()` contract instantiates this
    with concrete transports; tests inject `FakeDmnTransport`/`FakeCibSevenTransport`/a fake
    inference provider/a fake `WhatsAppSender`.
    """

    def __init__(
        self,
        *,
        inference: InferenceProvider,
        dmn: DmnTransport,
        cibseven: CibSevenTransport,
        audit_sink: AuditStartSink,
        whatsapp: WhatsAppSender,
        agent_version: str = "helena@v0",
    ) -> None:
        self._llm = inference
        self._dmn = dmn
        self._cibseven = cibseven
        # T-C2 fence: required durable ADR-0007 sink for the SP-OP-ESCALATION-001 start
        # (audit-before-effect). This is the LIVE agent execution path (webhook dispatch).
        self._audit_sink = audit_sink
        self._model_id = getattr(inference, "model_id", None)
        self._whatsapp = whatsapp
        self._agent_version = agent_version

    # -- Nodes ----------------------------------------------------------------------------

    async def receive(self, state: HelenaState) -> dict[str, Any]:
        """Turn start: (1) ENTRY SANITIZATION — reset EVERY output-only field to its neutral
        default so no caller/upstream-planted value can be read by a downstream node (T1.11
        caller-planted read-through fix, layer 1); (2) defend against missing runtime identifiers
        (fail-closed -> escalate).

        The reset is what makes the `if state.get("error")` bails downstream (`classify`,
        `escalate`) safe: after `receive`, `error` reflects ONLY a failure THIS graph set this
        turn (missing context here; a classify/DMN/tool failure later) — never a caller-supplied
        one. In particular it defeats the anti-escalation probe (planted `error`+`next_kind=
        'inform'` on a red-flag message): the planted `error` is cleared, `classify` runs, and the
        existing fail-closed red-flag logic re-engages instead of being skipped.
        """
        reset: dict[str, Any] = dict(_HELENA_NEUTRAL_OUTPUTS)
        if not state.get("conversation_id") or not state.get("tenant_id"):
            reset["next_kind"] = "escalate"
            reset["error"] = "missing runtime context (tenant_id/conversation_id)"
        return reset

    async def classify(self, state: HelenaState) -> dict[str, Any]:
        """Classify intent + normalize any symptom, then ALWAYS evaluate the red-flag DMN for a
        symptom (or for psychosocial risk, forced to the mental_health table). The LLM never
        decides red_flag/severity — only the DMN does (ADR-0012).

        FAIL-CLOSED on classifier failure (R1 cycle-1 blocking fix): an LLM exception,
        unparseable JSON, or schema-invalid JSON is gatilho 4 (`falha_tecnica`) -> escalate,
        NEVER a silent `inform` default — symmetric with the DMN-down and missing-context
        failure paths below/in `receive`.
        """
        if state.get("error"):
            # `receive` already reset every output-only field, so a surviving `error` here can
            # ONLY be one THIS graph set this turn (missing runtime context) — never a caller-
            # planted one. That genuine mid-turn failure was already routed to escalate by
            # `receive`; do not re-classify over it.
            return {}

        extraction, classify_failure = await self._classify_llm(state)
        if extraction is None:
            # Gatilho 4: classifier failure is a TECHNICAL failure -> human, NEVER read as a
            # benign administrative turn (fail-safe; see `_classify_llm`'s docstring).
            return {
                "next_kind": "escalate",
                "escalation_motivo": "falha_tecnica",
                "escalation_severidade": "leve",
                "error": classify_failure or "classify LLM failed",
            }
        intent = cast(Intent, extraction.get("intent", "information"))
        psychosocial = bool(extraction.get("psychosocial_risk", False))
        population = cast(Population, extraction.get("population", "none"))

        update: dict[str, Any] = {
            "intent": intent,
            "population": population,
            "psychosocial_risk": psychosocial,
            "sintoma_codigo": extraction.get("sintoma_codigo"),
            "intensidade": extraction.get("intensidade", "desconhecida"),
        }

        # Gatilho 5 (always evaluated, highest priority): psychosocial risk in ANY message.
        if psychosocial:
            dmn_out = await self._evaluate_dmn(extraction, force_population="mental_health")
            update.update(dmn_out)
            update["next_kind"] = "escalate"
            update["escalation_motivo"] = "risco_psicossocial"
            update["escalation_severidade"] = "grave"
            return update

        # Gatilho 2: clinical question (L0 hard — Helena never answers one herself).
        if intent == "clinical_question":
            update["next_kind"] = "escalate"
            update["escalation_motivo"] = "intencao_clinica"
            update["escalation_severidade"] = "moderada"
            return update

        # Gatilho 3: explicit request for a human.
        if intent == "human_request":
            update["next_kind"] = "escalate"
            update["escalation_motivo"] = "solicitacao_humano"
            update["escalation_severidade"] = "leve"
            return update

        # Symptom: ALWAYS goes through the DMN — the DMN decides red flag, never the LLM.
        if intent == "symptom":
            dmn_out = await self._evaluate_dmn(extraction)
            update.update(dmn_out)
            if dmn_out.get("error"):
                # Gatilho 4: DMN unavailable/no-result is a technical failure -> human, NEVER
                # treated as "no red flag" (fail-safe, ADR-0028 §3).
                update["next_kind"] = "escalate"
                update["escalation_motivo"] = "falha_tecnica"
                update["escalation_severidade"] = "leve"
                return update
            decision = dmn_out.get("dmn_decision", {})
            conduta = str(decision.get("conduta", "CONTINUE"))
            if decision.get("red_flag") is True or conduta.startswith("ESCALATE"):
                prioridade = str(decision.get("prioridade", "P2"))
                is_mental = dmn_out.get("dmn_table") == _DMN_BY_POPULATION["mental_health"]
                update["next_kind"] = "escalate"
                update["escalation_motivo"] = "risco_psicossocial" if is_mental else "red_flag_clinico"
                update["escalation_severidade"] = _severidade_from_prioridade(prioridade)
                return update
            update["next_kind"] = "inform"
            return update

        if intent == "scheduling":
            update["next_kind"] = "schedule"
            return update

        update["next_kind"] = "inform"
        return update

    async def inform(self, state: HelenaState) -> dict[str, Any]:
        """Administrative response (no clinical guidance, no red flag)."""
        text = await self._respond_llm(state, "inform")
        return {"response_text": text, "response_kind": "inform"}

    async def schedule(self, state: HelenaState) -> dict[str, Any]:
        """Direct scheduling is not yet available on this channel -> offers a human follow-up."""
        text = await self._respond_llm(state, "schedule")
        return {"response_text": text, "response_kind": "schedule"}

    async def escalate(self, state: HelenaState) -> dict[str, Any]:
        """Start SP-OP-ESCALATION-001 idempotently and draft the handoff response.

        A tool failure here never blocks the turn — the response still tells the beneficiary a
        human will follow up (`error` records the failure for observability; the runtime's own
        fail-closed posture treats an unstarted escalation as an incident, never a silent drop).
        """
        motivo: MotivoCategoria = state.get("escalation_motivo") or (
            "falha_tecnica" if state.get("error") else "outro"
        )
        severidade: Severidade = state.get("escalation_severidade", "leve")
        business_key = _business_key(state)

        resumo = await self._resumo_contexto(state, motivo)
        if motivo == "falha_tecnica" and state.get("error"):
            # R1 cycle-1 fix: carry the technical-failure reason into the human handoff so the
            # attendant sees WHY the automated turn failed. The reason is bounded and contains
            # no raw LLM output / no beneficiary text (see `_classify_llm`) — everything else in
            # this variable set is already pseudonymized (ADR-0006).
            resumo = f"{resumo} [falha tecnica: {str(state['error'])[:300]}]"
        variables: dict[str, Any] = {
            "tenant_id": state.get("tenant_id", ""),
            "source_agent_id": "helena",
            "source_agent_version": self._agent_version,
            "conversation_id": state.get("conversation_id", ""),
            "beneficiario_pseudo_id": state.get("beneficiario_pseudo_id", ""),
            "canal": state.get("canal", "whatsapp"),
            "motivo_categoria": motivo,
            "severidade": severidade,
            "resumo_contexto": resumo,
        }
        ref = state.get("dmn_decision_ref")
        if ref:
            variables["dmn_decision_ref"] = ref

        response_text = await self._respond_llm(state, "escalate")
        provenance = AgentDecisionProvenance(
            agent_id="helena",
            agent_version=self._agent_version,
            tenant_id=state.get("tenant_id", ""),
            model_id=self._model_id,
            prompt_version=SYSTEM_PROMPT_VERSION,
            # Bounded routing tokens ONLY — never `resumo_contexto` (PHI, ADR-0006).
            decision_basis={
                "escalation_motivo": motivo,
                "escalation_severidade": severidade,
                "dmn_decision_ref": state.get("dmn_decision_ref") or "",
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
            return {
                "escalation_started": False,
                "escalation_motivo": motivo,
                "escalation_severidade": severidade,
                "escalation_business_key": business_key,
                "error": f"start_process indisponivel: {exc}",
                "response_text": response_text,
                "response_kind": "escalate",
            }

        return {
            "escalation_started": True,
            "escalation_motivo": motivo,
            "escalation_severidade": severidade,
            "escalation_business_key": business_key,
            "escalation_process_ref": {
                "instance_id": instance.instance_id,
                "state": instance.state,
                "already_existed": instance.already_existed,
            },
            "response_text": response_text,
            "response_kind": "escalate",
        }

    async def respond(self, state: HelenaState) -> dict[str, Any]:
        """Send the drafted response over WhatsApp. A policy refusal or transport failure is
        recorded in `error` (observable) — it never silently disappears (v1 landmine #2 this
        design avoids by design: no blanket `except Exception: pass`)."""
        text = state.get("response_text") or ""
        try:
            await self._whatsapp.send(_to_hash_from_state(state), text)
        except Exception as exc:  # noqa: BLE001 — surfaced via `error`, never swallowed silently.
            return {"error": f"whatsapp send failed: {exc}"}
        return {}

    # -- Conditional routing ----------------------------------------------------------------

    @staticmethod
    def _route(state: HelenaState) -> str:
        kind = state.get("next_kind", "inform")
        if kind == "escalate":
            return "escalate"
        if kind == "schedule":
            return "schedule"
        return "inform"

    # -- DMN (ADR-0012/ADR-0028): the DMN decides red flag, never the LLM -------------------

    async def _evaluate_dmn(
        self,
        extraction: dict[str, Any],
        *,
        force_population: str | None = None,
    ) -> dict[str, Any]:
        population = force_population or extraction.get("population") or "adult"
        table = _DMN_BY_POPULATION.get(population, "triage_redflag_adult")

        dmn_input: dict[str, Any] = {
            "sintoma_codigo": extraction.get("sintoma_codigo"),
            "intensidade": extraction.get("intensidade", "desconhecida"),
        }
        if table == "triage_redflag_adult":
            dmn_input["idade_anos"] = extraction.get("idade_anos")
        elif table == "triage_redflag_pediatric":
            dmn_input["idade_meses"] = extraction.get("idade_meses")
        elif table == "triage_redflag_gestante":
            dmn_input["idade_gestacional_semanas"] = extraction.get("idade_gestacional_semanas")
        elif table == "triage_redflag_mental_health":
            risco = extraction.get("risco_imediato")
            # Conservative posture: unknown -> treat as immediate risk (matches the DMN's own
            # fail-safe stance and the classify prompt's instruction).
            dmn_input["risco_imediato"] = True if risco is None else bool(risco)

        try:
            rows, version = await self._dmn.evaluate(table, dmn_input)
            row = first_row(rows, table, dmn_input)
        except (DmnEvaluationError, DmnNoResultError) as exc:
            return {"dmn_table": table, "dmn_decision": {}, "error": f"DMN `{table}` indisponivel: {exc}"}

        # Provenance reference: `{table}#{decision-definition id}` — the engine's evaluate
        # response does not return which RULE fired (no `ruleId` in the Camunda 7 REST evaluate
        # contract), so this cites the authoritative deployed decision-definition id/version
        # (ADR-0028 §2) rather than fabricating a rule id the engine never gave us.
        ref = f"{table}#{version.id}"
        return {"dmn_table": table, "dmn_decision": row, "dmn_decision_ref": ref}

    # -- LLM helpers (all PHI-tagged — ADR-0006/ADR-0017/T1.7) ------------------------------

    async def _classify_llm(self, state: HelenaState) -> tuple[dict[str, Any] | None, str | None]:
        """Run the classify LLM call. Returns `(extraction, None)` on success or
        `(None, failure_reason)` on ANY failure — exception, unparseable JSON, or
        schema-invalid JSON per `_validate_extraction`.

        FAIL-CLOSED (R1 cycle-1 blocking fix): a failure here must NEVER be read as a benign
        `intent="information"` turn — the pre-fix behavior defaulted to exactly that, silently
        routing an unclassifiable (possibly clinical) message to `inform` (fail-OPEN,
        live-reproduced by the verifier with "não consigo respirar" + malformed JSON). The
        caller (`classify`) routes any failure to escalation `falha_tecnica` — the same
        SP-OP-ESCALATION-001 technical-failure trigger the DMN-down path already uses, restoring
        symmetry with every other failure path in this graph.

        The failure reason is short and bounded and names the failure CLASS only (for schema
        violations: `_validate_extraction`'s class token, e.g. `non_allowlisted_sintoma_codigo`)
        — never a field value, never the raw LLM output, never the beneficiary's message text
        (R1 cycle-2 fix: the cycle-1 version echoed the offending field value, which flowed
        into engine process variables via `resumo_contexto` — a live-proven PHI leak vector
        when the LLM copies a beneficiary-typed identifier into a field).
        """
        prompt = f"{classify_prompt()}\n\nMensagem do beneficiario:\n{state.get('message_body', '')}"
        try:
            raw = await self._llm.generate(prompt, phi=True)
        except Exception as exc:  # noqa: BLE001 — classified into a failure reason, never swallowed.
            return None, f"classify LLM call failed: {type(exc).__name__}: {str(exc)[:200]}"
        data = _parse_json_object(raw)
        if data is None:
            return None, "classify LLM returned unparseable JSON"
        schema_failure = _validate_extraction(data)
        if schema_failure is not None:
            return None, f"classify LLM returned schema-invalid JSON: {schema_failure}"
        return data, None

    async def _respond_llm(self, state: HelenaState, response_kind: ResponseKind) -> str:
        context = {
            "response_kind": response_kind,
            "dmn_motivo": (state.get("dmn_decision") or {}).get("motivo"),
            "escalation_severidade": state.get("escalation_severidade"),
        }
        prompt = (
            f"{response_prompt()}\n\ncontexto={context}\n"
            f"mensagem_beneficiario={state.get('message_body', '')}"
        )
        try:
            return await self._llm.generate(prompt, phi=True)
        except Exception:  # noqa: BLE001 — fail-safe default: never leave the beneficiary with nothing.
            return "Recebemos sua mensagem. Um profissional humano vai continuar o atendimento em breve."

    async def _resumo_contexto(self, state: HelenaState, motivo: str) -> str:
        prompt = (
            "Resuma em 1-2 frases, em portugues, o contexto desta conversa para um atendente "
            "humano assumir. NAO inclua dado identificavel. NAO de conduta clinica. Apenas o "
            f"essencial do caso e o motivo do encaminhamento.\nmotivo={motivo}\n"
            f"mensagem={state.get('message_body', '')}"
        )
        try:
            text = await self._llm.generate(prompt, phi=True)
        except Exception:  # noqa: BLE001 — fail-safe: never block the escalation on a summary.
            text = ""
        return text or f"Encaminhamento automatico ({motivo})."

    # -- Graph assembly -----------------------------------------------------------------------

    def compile_graph(self) -> StateGraph[HelenaState]:
        g: StateGraph[HelenaState] = StateGraph(HelenaState)
        g.add_node("receive", self.receive)
        g.add_node("classify", self.classify)
        g.add_node("inform", self.inform)
        g.add_node("schedule", self.schedule)
        g.add_node("escalate", self.escalate)
        g.add_node("respond", self.respond)

        g.add_edge(START, "receive")
        g.add_edge("receive", "classify")
        g.add_conditional_edges(
            "classify",
            self._route,
            {"inform": "inform", "schedule": "schedule", "escalate": "escalate"},
        )
        g.add_edge("inform", "respond")
        g.add_edge("schedule", "respond")
        g.add_edge("escalate", "respond")
        g.add_edge("respond", END)
        return g


def build(config: dict[str, Any] | None = None) -> StateGraph[HelenaState]:
    """Contract `_template/graph.py:build`, resolved by `AgentLoader`/`runtime.harness.Harness`.

    `config` MUST contain:
      - `inference`: an `InferenceProvider` (ADR-0009).
      - `dmn`: a `DmnTransport` (ADR-0028/T1.5).
      - `cibseven`: a `CibSevenTransport` (ADR-0001/T1.11).
      - `whatsapp`: a `WhatsAppSender`.
    Optional:
      - `agent_version`: audit provenance string (ADR-0007), defaults to `"helena@v0"`.

    Fail-closed: missing a required dependency raises `ValueError` at build time — Helena never
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
            f"Helena build(config) is missing required dependencies: {missing} "
            "(ADR-0001/0007/0009/0028 — inference/dmn/cibseven/whatsapp/audit_sink must all be "
            "injected; audit_sink is the T-C2 fence — no escalation start without a durable sink)"
        )
    agent_version = str(cfg.get("agent_version", "helena@v0"))
    return HelenaGraph(
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
    "classify": CLASSIFY_PROMPT_VERSION,
    "response": RESPONSE_PROMPT_VERSION,
}

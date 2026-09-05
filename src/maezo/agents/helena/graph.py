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

Six escalation triggers (each maps to a distinct `motivo_categoria`, contract
SP-OP-ESCALATION-001):
1. DMN red_flag=true                       -> red_flag_clinico (or risco_psicossocial if the
                                               mental_health table fired)
2. intent = clinical question              -> intencao_clinica (L0 hard — Helena never answers)
3. explicit request for a human            -> solicitacao_humano
4. intent = scheduling                     -> solicitacao_humano (GAP 9.2 — direct scheduling is
                                               OUT OF SCOPE this phase; the `schedule` node drafts
                                               its own honest, scheduling-specific reply, then
                                               hands off through the SAME SP-OP-ESCALATION-001
                                               start `escalate` uses, via the shared
                                               `_start_escalation` helper — the beneficiary is
                                               actually routed to a human, not just told one will
                                               follow up. See `spec/agents/helena/agent.yaml`'s
                                               `scope`/`out_of_scope` block for the phase-boundary
                                               declaration this trigger implements.)
5. technical failure                       -> falha_tecnica — DMN down/no-result, OR any
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
6. psychosocial risk in ANY message        -> risco_psicossocial (always evaluated, highest
                                               priority — never gated behind `intent`)

L0 HARD INVARIANT: Helena NEVER resolves a clinical concern herself. Every path ends in either
a human task (`escalate`/`schedule` -> SP-OP-ESCALATION-001's `UT_TratarEscalonamento`) or an
explicit, non-clinical response (`inform`) drafted by an LLM that is instructed to never give
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
- Episodic memory write (`mcp-memory.read_write`, ADR-0002) is NOT wired in this graph. The
  schema is NOT what is missing — migration `0001` creates `agent_memory`;
  `MemoryServer.store_episodic` refuses because its `(agent_id, event)` signature carries neither
  `tenant_id` nor `thread_id` (both `text NOT NULL`), which is GAP-DU-01-a, an owner decision. The semantic
  column that once sat in the same table was dropped by `0009_drop_pgvector` and ADR-0002 §3 is
  SUSPENDED pending a consumer (ADR-0047, DRAFT) — so there is no semantic write to follow up
  on at all.
- Free-text WhatsApp message content is NOT scanned for embedded PHI patterns (e.g. a
  beneficiary typing their own CPF into the message) before reaching the LLM — mitigated by the
  mandatory `phi=True` routing (content never reaches a general-zone cloud provider). What DOES
  exist since CC-06/HEL-05 is the EGRESS scrub: `_start_escalation` runs
  `phi_vars.redact_free_text` over the `resumo_contexto` it produces (and
  `redact_error_message` over the `[falha tecnica: ...]` suffix), and the shared start chokepoint
  `start_process_idempotent` runs the same net over every process-start variable. That is an
  identifier net (CPF/CNPJ/e-mail/BR phone/long digit runs), NOT a PHI classifier: clinical
  content in prose is still carried, in-zone, by design.
- No multi-turn conversation checkpointing WITHIN this module: this graph is compiled here
  without a checkpointer. The LIVE webhook path is NOT stateless, though:
  `platform/webhooks/whatsapp/dispatch.py::HelenaDispatcher.dispatch` compiles it WITH a durable
  LangGraph checkpointer (T4b, `runtime.checkpoint.Checkpointer`) and invokes it under a PHI-safe
  per-conversation thread config, so cross-turn state does persist in production. The remaining
  follow-up is ADR-0002's episodic/semantic memory layers, not the checkpointer.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from typing import Any, Literal, Protocol, TypedDict, cast

import structlog
from langgraph.graph import END, START, StateGraph

from maezo.runtime.inference import InferenceProvider
from maezo.runtime.start_outcome import notify_start_failure as emit_start_failure_notice
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
from maezo.tools.workers.phi_vars import redact_error_message, redact_free_text

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
#: CC-01: o vocabulario do `response_kind` EMITIDO e um superconjunto do de ROTEAMENTO. Helena
#: pode responder um `falha_tecnica_start` (a resposta honesta quando a escalacao nao abriu), mas
#: nunca ROTEIA para ele — `next_kind` continua sendo `ResponseKind`, com os tres destinos que
#: `_route` sabe mapear. Alargar o tipo de roteamento aqui criaria um valor que nenhuma aresta
#: conhece; alargar so o de saida nao cria destino nenhum.
ResponseKindOut = Literal["inform", "schedule", "escalate", "falha_tecnica_start"]

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

#: CC-01: a resposta HONESTA quando a escalacao nao pode ser aberta. Constante, nunca um draft de
#: LLM (ver `_respond_start_failure`). Nao promete atendente, nao promete prazo, nao cita
#: identificador nenhum — diz o que houve e o que o beneficiario pode fazer agora.
RESPOSTA_FALHA_TECNICA_START: str = (
    "Nao consegui registrar seu atendimento agora por uma falha tecnica no nosso sistema, "
    "e por isso nenhum atendente foi acionado ainda. Por favor, envie sua mensagem novamente "
    "em alguns minutos. Se voce estiver passando por uma emergencia, procure o servico de "
    "emergencia mais proximo."
)

#: `response_kind` do turno de falha de start. Token de classe fechado, como os demais — o que
#: permite a um golden/alerta distinguir esta resposta de um handoff de verdade.
RESPONSE_KIND_FALHA_TECNICA_START: str = "falha_tecnica_start"

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
    #: CC-01: o start de SP-OP-ESCALATION-001 foi TENTADO e FALHOU tecnicamente
    #: (`_start_escalation`'s `except CibSevenError`). Distinto de `escalation_started is False`,
    #: que tambem e o valor NEUTRO de um turno informativo que nunca tentou escalar.
    start_failed: bool

    # Turn output.
    response_text: str
    response_kind: ResponseKindOut
    #: CC-01: desfecho do turno. Helena nao tinha este campo — a conversa nao e um processo e o
    #: turno feliz nao produz desfecho contratual nenhum (fica ""). Ele existe para o UNICO
    #: desfecho que Helena PRECISA declarar: `erro_inicio_processo`, quando ela nao conseguiu
    #: abrir a escalacao. Escrito exclusivamente por `respond` no ramo de falha.
    desfecho: str
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
    "start_failed": False,
    "response_text": None,
    "response_kind": None,
    "desfecho": "",
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


# The three age signals the red-flag DMN tables consume, each DMN-typed `integer`
# (`idade_anos`->triage_redflag_adult, `idade_meses`->triage_redflag_pediatric,
# `idade_gestacional_semanas`->triage_redflag_gestante). They arrive from the classify LLM's
# free-text JSON and drive which pediatric/gestational/adult red-flag rows can fire — a wrong
# age could MISS a red flag, so they are validated (below) exactly like every other classify
# field before ever reaching the DMN.
_AGE_FIELDS: tuple[str, ...] = ("idade_anos", "idade_meses", "idade_gestacional_semanas")


def _coerce_age(value: Any) -> tuple[bool, int | None]:
    """Coerce an LLM-sourced age value to a non-negative `int`, fail-closed on ambiguity.

    Returns `(ok, coerced)`:
    - `None`/absent -> `(True, None)` — many turns carry no age; that is valid, the DMN's own
      catch-all handles the missing-age case;
    - a native `int` that is not a `bool` and `>= 0` -> `(True, value)`;
    - a clean non-negative integer STRING like `"5"` -> `(True, 5)` (the classify LLM emits JSON
      where a number may arrive quoted);
    - ANYTHING else -> `(False, None)`: a `bool`, a float / fractional or signed string
      (`5.9`, `"5.9"`, `"-5"`), a non-numeric string, a list/dict. We NEVER `int(float(...))` a
      `"5.9"` into `5` — a plausible-but-wrong age could mis-triage — the caller fails closed
      (escalate to a human) instead, never passing an ambiguous value to the DMN.

    The string guard is `str.isdecimal()`, NOT `str.isdigit()`: `isdigit()` is `True` for
    Unicode digit glyphs that `int()` cannot parse (superscripts like `"²"`, circled digits),
    so `isdigit()` + `int()` would RAISE on such input; `isdecimal()` admits only base-10 digits
    `int()` accepts, so a Unicode-digit glyph fails CLOSED to `(False, None)` here rather than
    raising up an unwrapped call site.
    """
    if value is None:
        return True, None
    if isinstance(value, bool):  # bool is an int subclass — a `true`/`false` age is malformed.
        return False, None
    if isinstance(value, int):
        return (True, value) if value >= 0 else (False, None)
    if isinstance(value, str) and value.strip().isdecimal():  # base-10 digits only -> int()-safe.
        return True, int(value.strip())
    return False, None


def _is_explicitly_false(value: Any) -> bool:
    """True only for an EXPLICIT boolean-false signal (native `False` or the string `"false"`).

    Mirrors this codebase's `_is_true` idiom (fraude/contas workers) but inverted for the
    conservative red-flag posture: any value that is NOT an explicit false — `None`, `"true"`,
    an unparseable string, a list — is treated as NOT-explicitly-false so the caller can fail
    SAFE toward immediate risk. Never coerces a truthiness (`bool("false") == True`), which is
    the very bug this replaces at the `risco_imediato` call site.
    """
    if isinstance(value, bool):
        return value is False
    if isinstance(value, str):
        return value.strip().lower() == "false"
    return False


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
      `invalid_intensidade`;
    - each of `_AGE_FIELDS` (`idade_anos`/`idade_meses`/`idade_gestacional_semanas`) is
      int-or-None (`None`/absent is valid). A non-coercible age (list, non-numeric or fractional
      string, float, bool) -> `invalid_age`: these feed the red-flag DMN tables as typed
      `integer`s and a wrong/ambiguous age could MISS a red flag, so an un-validated value must
      never reach the DMN (previously a bad value only failed INCIDENTALLY via a downstream
      engine FEEL/400 error — this makes the escalation EXPLICIT and DETERMINISTIC). Valid ages
      are COERCED IN PLACE (a quoted `"5"` -> `5`) so the DMN receives the correct integer type.
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
    for field in _AGE_FIELDS:
        ok, coerced = _coerce_age(data.get(field))
        if not ok:
            return "invalid_age"
        # Coerce in place (e.g. "5" -> 5) so `_evaluate_dmn` reads the same dict and hands the
        # DMN a correctly typed integer. Absent fields (coerced None) are left as-is.
        if coerced is not None:
            data[field] = coerced
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
        """GAP 9.2 fix: direct scheduling is out of scope this phase, but the beneficiary is
        actually HANDED OFF to a human, not just told one will follow up. Pre-fix this node only
        drafted the honest "not available on this channel" reply and ended the turn — the
        promise of a human follow-up (`prompts.py`'s `response_kind="schedule"` instructions)
        was never backed by a real SP-OP-ESCALATION-001 start, so a beneficiary asking to
        (re)schedule got a dead end unless they asked again in words `classify` recognizes as
        `human_request`. This now routes through the SAME `_start_escalation` helper `escalate`
        uses (`motivo_categoria="solicitacao_humano"` — the contract vocabulary's closest fit for
        "needs a human to act", `spec/agents/helena/agent.yaml`'s `escalation.triggers`), just
        with the scheduling-specific reply text (`response_kind="schedule"`) instead of the
        generic escalate one."""
        return await self._start_escalation(
            state,
            motivo="solicitacao_humano",
            severidade=state.get("escalation_severidade", "leve"),
            response_kind="schedule",
        )

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
        return await self._start_escalation(
            state, motivo=motivo, severidade=severidade, response_kind="escalate"
        )

    async def _start_escalation(
        self,
        state: HelenaState,
        *,
        motivo: MotivoCategoria,
        severidade: Severidade,
        response_kind: ResponseKind,
    ) -> dict[str, Any]:
        """Shared SP-OP-ESCALATION-001 start, factored out of `escalate` (GAP 9.2) so `schedule`
        can hand off to a human through the EXACT SAME audited/idempotent path instead of a
        second, parallel (and easy-to-drift) implementation. `response_kind` is the only thing
        that varies downstream: it selects which honest reply `prompts.py.response_prompt()`
        drafts (`"escalate"`'s generic handoff text vs `"schedule"`'s scheduling-specific one) —
        the escalation itself (business key, audit-before-effect, idempotent start, provenance)
        is identical regardless of caller.
        """
        business_key = _business_key(state)

        # HEL-05 — DEFENSE IN DEPTH, on top of the chokepoint's own scrub. `_resumo_contexto` is
        # a free-text LLM draft over the beneficiary's own message, and Helena is the DIRECT
        # producer of the contractual `resumo_contexto` (SP-OP-ESCALATION-001 §Variaveis:
        # "pseudonimizado"), so the identifier net is applied HERE, at the producer, and again at
        # `start_process_idempotent` (CC-06). Scrubbing twice is idempotent: `redact_free_text`
        # replaces identifier substrings with class tokens, and the class tokens match no pattern.
        resumo = redact_free_text(await self._resumo_contexto(state, motivo))
        if motivo == "falha_tecnica" and state.get("error"):
            # R1 cycle-1 fix: carry the technical-failure reason into the human handoff so the
            # attendant sees WHY the automated turn failed. The reason is bounded and contains
            # no raw LLM output / no beneficiary text (see `_classify_llm`) — everything else in
            # this variable set is already pseudonymized (ADR-0006).
            #
            # HEL-05: `str(...)[:300]` was a LENGTH bound, never a CONTENT one. `error` is not
            # always the bounded classifier token this comment describes — `escalate`'s own
            # `CibSevenError` handler writes `f"start_process indisponivel: {exc}"` into it, and a
            # transport exception message is arbitrary text from another system. `redact_error_message`
            # (which delegates to the same `redact_free_text` net) bounds BOTH.
            resumo = f"{resumo} [falha tecnica: {redact_error_message(state['error'])}]"
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

        response_text = await self._respond_llm(state, response_kind)
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
                # CC-01: o marcador que `respond` le para NAO prometer um humano que ninguem
                # acionou. `escalation_started is False` sozinho nao serve: e tambem o neutro de
                # um turno informativo, que nunca tentou escalar coisa nenhuma.
                "start_failed": True,
                "escalation_motivo": motivo,
                "escalation_severidade": severidade,
                "escalation_business_key": business_key,
                # HEL-05 (feeder): a transport exception message is arbitrary text from another
                # system, and this `error` survives into the NEXT turn's `[falha tecnica: ...]`
                # suffix under the live checkpointed dispatch (T4b).
                "error": f"start_process indisponivel: {redact_error_message(exc)}",
                "response_text": response_text,
                "response_kind": response_kind,
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
            "response_kind": response_kind,
        }

    async def respond(self, state: HelenaState) -> dict[str, Any]:
        """Send the drafted response over WhatsApp. A policy refusal or transport failure is
        recorded in `error` (observable) — it never silently disappears (v1 landmine #2 this
        design avoids by design: no blanket `except Exception: pass`).

        CC-01 — A MENSAGEM TEM DE CORRESPONDER AO QUE ACONTECEU. `_start_escalation` redige o
        texto de handoff (`_respond_llm`) ANTES de tentar o start, e ate 2026-09-04 este no
        enviava esse texto tambem quando o start havia falhado: o beneficiario lia "um
        profissional vai continuar seu atendimento" enquanto ZERO instancias de
        SP-OP-ESCALATION-001 existiam — uma promessa de humano que ninguem acionou, sem prazo
        (o SLA vive na instancia que nao nasceu) e sem alerta. Agora o handoff so sai quando a
        escalacao existe; caso contrario o texto e SUBSTITUIDO pela mensagem honesta de falha
        tecnica e o turno declara `desfecho=erro_inicio_processo`.

        CC-09: emite UM `maezo_agent_desfecho_total` para este turno. `HelenaState.desfecho` e'
        campo morto (nenhum no' de Helena escreve nele — ver `turn_telemetry`'s module
        docstring), entao o `desfecho` do label e' DERIVADO aqui, via override, de
        `escalation_started`/`response_kind`/`escalation_motivo` — os sinais reais desta
        agente. O ramo `start_failed=True` NAO emite aqui: `_start_failure_outcome` ja delega
        ao helper compartilhado (`runtime.start_outcome.notify_start_failure`), que emite por
        conta propria — emitir aqui tambem duplicaria o turno.
        """
        # UM unico `send` e UM unico handler de falha de envio nos dois ramos — o que muda entre
        # eles e O QUE se diz e O QUE o turno declara, nunca o mecanismo de envio.
        if state.get("start_failed") is True:
            saida = self._start_failure_outcome(state)
            text = RESPOSTA_FALHA_TECNICA_START
        else:
            saida = {}
            text = state.get("response_text") or ""
        enviada = False
        try:
            await self._whatsapp.send(_to_hash_from_state(state), text)
            enviada = True
        except Exception as exc:  # noqa: BLE001 — surfaced via `error`, never swallowed silently.
            # HEL-05 (feeder): same chain as the two above — `error` reaches `resumo_contexto`.
            # O desfecho de falha de start (quando ha um) NAO e apagado por uma falha de envio:
            # o caso continua marcado como start falho, que e o que a operacao precisa ver.
            saida = {**saida, "error": f"whatsapp send failed: {redact_error_message(exc)}"}
        if state.get("start_failed") is not True:
            emit_turn_desfecho(
                state,
                agent_id="helena",
                desfecho=(
                    "escalado_humano" if state.get("escalation_started") is True else "resolvido_automatico"
                ),
                route=saida.get("response_kind") or state.get("response_kind"),
                motivo_categoria=saida.get("escalation_motivo") or state.get("escalation_motivo"),
                enviada=enviada,
            )
        return saida

    def _start_failure_outcome(self, state: HelenaState) -> dict[str, Any]:
        """CC-01: o desfecho + o alerta do turno em que a escalacao NAO pode ser aberta.

        O texto que acompanha (`RESPOSTA_FALHA_TECNICA_START`) e uma CONSTANTE, nao um draft de
        LLM: um modelo, pedido para "explicar uma falha tecnica", volta a prometer um atendente
        com facilidade — e a promessa e exatamente o que nao pode existir aqui. Ele substitui o
        handoff que `_start_escalation` ja havia redigido ANTES de tentar o start.

        O desfecho e o alerta vem do helper compartilhado (uma definicao para os 9 agentes).

        F1 (VERIFY-CC09): `HelenaState` nao tem chave `route` (usa `response_kind`), entao o
        `state.get("route")` generico do helper devolveria `None` — passamos
        `route=RESPONSE_KIND_FALHA_TECNICA_START` explicitamente, o literal que
        `_ROUTE_VOCAB["helena"]` ja declara para este caso.
        """
        return emit_start_failure_notice(
            dict(state),
            agent_id="helena",
            process_key=PROCESS_KEY,
            route=RESPONSE_KIND_FALHA_TECNICA_START,
            extra={
                "response_text": RESPOSTA_FALHA_TECNICA_START,
                "response_kind": RESPONSE_KIND_FALHA_TECNICA_START,
                "escalation_started": False,
            },
        )

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
            # Conservative posture: immediate risk UNLESS the LLM says explicitly false. This
            # matches the DMN's fail-safe stance and the classify prompt's instruction, and fixes
            # a wrong coercion: the prior `bool(risco)` read the string `"false"` as `True`
            # (harmless here — it fails safe) but also read `""`/`0`/`[]` as `False` (fail-OPEN).
            # `_is_explicitly_false` yields True only for native `False`/`"false"`; everything
            # else (None, "true", unparseable) stays immediate-risk.
            dmn_input["risco_imediato"] = not _is_explicitly_false(risco)

        try:
            rows, version = await self._dmn.evaluate(table, dmn_input)
            row = first_row(rows, table, dmn_input)
        except (DmnEvaluationError, DmnNoResultError) as exc:
            # HEL-05 (feeder): the DMN-down path routes to escalate `falha_tecnica` in THIS turn,
            # so this message reaches `resumo_contexto` directly (`table` is a class token).
            return {
                "dmn_table": table,
                "dmn_decision": {},
                "error": f"DMN `{table}` indisponivel: {redact_error_message(exc)}",
            }

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
            raw = await self._llm.generate(
                prompt,
                phi=True,
                agent_id="helena",
                tenant_id=state.get("tenant_id", ""),
                # ADR-0009 §2 / CC-12: extracao estruturada (JSON) -> task_default.
                task_kind="task_default",
            )
        except Exception as exc:  # noqa: BLE001 — classified into a failure reason, never swallowed.
            # HEL-05 (feeder): `str(exc)[:200]` was a LENGTH bound, never a CONTENT one, and this
            # string becomes `state["error"]` (`classify`) which `_start_escalation` appends to
            # `resumo_contexto` as `[falha tecnica: ...]` — i.e. straight into engine process
            # variables, IN THE SAME TURN. The exception comes from the inference provider, whose
            # message may echo the request (which carries the beneficiary's own message body and
            # any identifier typed into it). `redact_error_message` bounds BOTH, and keeps the
            # `{ClassName}: ` prefix this f-string used to build by hand.
            return None, f"classify LLM call failed: {redact_error_message(exc)}"
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
            return await self._llm.generate(
                prompt,
                phi=True,
                agent_id="helena",
                tenant_id=state.get("tenant_id", ""),
                # ADR-0009 §2 / CC-12: fraseia fatos ja decididos (DMN motivo/severidade) -> task_default.
                task_kind="task_default",
            )
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
            text = await self._llm.generate(
                prompt,
                phi=True,
                agent_id="helena",
                tenant_id=state.get("tenant_id", ""),
                # ADR-0009 §2 / CC-12: o humano le isto antes de assumir o caso -> reasoning
                # (mesma logica do dossie de escalacao do lucas).
                task_kind="reasoning",
            )
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

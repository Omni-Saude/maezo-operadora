"""Fernando — Agente de Inadimplencia / Cobranca Avancada (Phase 3, T1.12).

Journey map (mirrors the v1 donor's structure, READ-ONLY reference `Maezo-Healthcare-Plan
src/maezo/agents/fernando/graph.py`, adapted to v2's flatter seam set — same rationale as
`agents/helena/graph.py` / `agents/rafael/graph.py`'s module docstrings: v2 has no
`ToolRegistry`/PEP-gateway wiring for agent tool calls yet, so this graph's nodes call the seams
already on `main` DIRECTLY: `DmnTransport.evaluate` (ADR-0028/T1.5), `InferenceProvider.
generate(phi=True)` (ADR-0009/T1.7), and `CibSevenTransport`/`start_process_idempotent`
(ADR-0001, T1.11)):

    receive -> assess -> {notify | escalate -> start_process} -> __end__

One agent, THREE journeys distinguished by `intencao` in state, NEVER by merit (spec
`spec/agents/fernando/agent.yaml`):

  J1 (`notificacao_previa`): inadimplencia ainda dentro da janela de purga / notificacao previa
     pendente. `assess` evaluates `inadimplencia_status` (ADR-0028, engine-side DMN) ->
     AGUARDA_PURGA / PENDENTE_NOTIFICACAO -> `notify` drafts + sends the prior notice /
     regularization reminder (WhatsApp). Purely informational — NEVER communicates
     suspension/rescission.

  J2 (`acompanhamento_purga`): Fernando READS the inadimplencia facts PRE-RESOLVED by a worker
     (`operadora.inadimplencia.resolve_facts`, T1.5) — `dentro_janela_purga`,
     `meses_inadimplencia`, `notificacao_previa_feita`, `dentro_periodo_minimo` — and NEVER
     computes/invents them (they arrive as plain state fields; this module contains no
     date/count arithmetic on them). `assess` reports the DMN-classified state: purga still open
     -> `notify` (a status follow-up, same node as J1, distinguished only by `desfecho`); purga
     elapsed (DMN says SEGUE_ANALISE/ANALISE_HUMANA) -> `escalate` (safety net, same as J3).

  J3 (`analise_inadimplencia` | `rescisao`): ALWAYS routes to human — Fernando NEVER suspends,
     rescinds, or communicates an adverse outcome himself. `rescisao` intent short-circuits
     straight to `escalate` WITHOUT even consulting `inadimplencia_status` (a rescission
     indicium/request is never a neutral, DMN-gated case — donor `graph.py:269-278`,
     `_is_rescisao_intent`). `analise_inadimplencia` intent ALWAYS escalates too, regardless of
     what `inadimplencia_status` says (an explicit human-analysis request is honored
     unconditionally) — DMN evaluation still runs for dossier context (motive/refs), it just
     never overrides the forced `escalate` route for this intent.

L0 HARD INVARIANT (contract SP-OP-INADIMPLENCIA-001 §invariante L0-hard, ADR-0005/0018):
Fernando NEVER exercises `contract_termination` (suspension/rescission), `authorization_denial`,
or `fraud_accusation`. Structurally enforced:
  - `Route` admits only `{"notify", "escalate"}` — no adverse variant exists in the type.
  - `_build_dossier`'s `decisao_inadimplencia`/`decisao_recomendada` are ALWAYS `None`, and
    `_inadimplencia_variables`'s `decisao_inadimplencia` engine variable is ALWAYS `None` —
    explicit guardrails making it structurally clear the decision belongs to the human analyst
    (`UT_AnaliseInadimplencia`), never to this code (mirrors rafael's `decisao_cobertura=None`).
  - `_build_message`'s `comunicacao_suspensao`/`comunicacao_rescisao` are ALWAYS `None` — the
    beneficiary-facing text is guaranteed-informational by construction.
  - The DMN `inadimplencia_status` has NO suspend/rescind output by contract design (see
    `spec/processes/dmn/inadimplencia_status.dmn`'s own `<description>`); an out-of-allowlist
    roteamento value (should the engine ever return one) fails CLOSED to `escalate`, never
    "passes through" as an implicit auto-response.
  - Rescission NEVER happens in this agent nor this process: `escalate`'s `rescisao`/`indicio_
    rescisao` path starts SP-OP-INADIMPLENCIA-001 (never SP-OP-CANCEL-001 directly) — the actual
    handoff to CANCEL-001 (which holds the sole rescission terminal) is the PROCESS's own
    `operadora.inadimplencia.handoff_rescisao` worker, gated behind the human User Task's
    `ENCAMINHAR_RESCISAO` decision (anti-double-termination, `.harmonization-decision.md`).

FAIL-CLOSED (mirrors Helena's R1 cycle-1 fix): a missing/invalid `intencao`, a missing
`tenant_id`/business key, or ANY `inadimplencia_status`/`inadimplencia_sla` DMN failure — engine
unreachable, non-2xx, empty result, or an out-of-allowlist `roteamento` value — routes to
`escalate`, NEVER a silent default `notify`. `_route` itself defaults to `escalate` on any
doubt/absence (never `notify` by omission).

ENGINE-VARIABLE HYGIENE (mirrors Helena's R1 cycle-2 fix — class tokens only, never a field
value): Fernando has no free-text LLM *classification* step (unlike Helena's `classify` —
`intencao` arrives as an already-structured field from the caller/A2A delegation, never
LLM-extracted here), but `intencao` plays the exact same structural role Helena's `intent`/
`sintoma_codigo` played: a closed-enum field that determines routing and could arrive corrupted
(a compromised/careless upstream LLM-based delegator, or a malformed A2A envelope). The SAME
discipline applies: `receive`'s `_escalate_min` records a bounded CLASS TOKEN
(`invalid_intencao`/`missing_tenant_id`/`missing_contract_key`) in `error` — NEVER the raw
`intencao!r}` value (the donor's `graph.py:332` echoes exactly that raw value; the spec here
does NOT). `_build_dossier`'s `facts["intencao"]` is populated ONLY when the value validated
against the closed `_VALID_INTENCOES` set — an invalid/planted value (e.g. a PHI-bearing string)
is carried as `None`, never echoed into `dossier["fatos"]` -> `dossie_fernando` -> the
SP-OP-INADIMPLENCIA-001 engine process variable (general security zone, ADR-0006). See
`tests/unit/agents/test_fernando.py::test_phi_bearing_intencao_never_reaches_engine_variables`.

R1 CYCLE-1 FIX (verifier finding, helena-cycle-2 class — caller-planted OUTPUT-field
read-through): the first build only guarded INPUT fields (`intencao`). The R1 verifier planted
`status_inadimplencia="SUSPENDER CPF=..."` directly in caller state on the `rescisao` journey —
where `assess` deliberately never classifies — and it read through verbatim into
`dossier["fatos"]` -> `dossie_fernando` -> engine process variables; `sla_analise_iso`/
`fonte_regulatoria_sla` leaked the same way on the receive-fail journeys, and a planted
`business_key` would have become the ENGINE business key on those paths (`start_process`'s
`state.get("business_key") or ...` fallback). Closed as a CLASS, both sides:
  - WRITE side: `receive` (success return AND `_escalate_min`) merges `_output_field_resets()` —
    every output-only state key this graph itself fills is reset to a neutral value at turn
    start, so a caller-planted output value is overwritten before ANY downstream node reads it.
  - READ side (defense in depth): `_build_dossier`/`_build_message` re-validate
    `status_inadimplencia` against `_STATUS_ALLOW` at the point of use — out-of-allowlist ->
    `None`, never echoed.
See `test_caller_planted_status_never_reaches_engine_variables_on_rescisao` (the verifier's
exact probe) and the `test_caller_planted_*` family. Residual (disclosed): `sla_analise_iso`/
`sla_alerta_iso`/`fonte_regulatoria_*`/`prazo_*_iso` have no read-side closed-vocabulary check
of their own (they are free-form DMN output strings with no finite domain to allowlist) — for
these, the write-side reset is the load-bearing guard; they are only ever populated from DMN
evaluation results after the reset.

PHI discipline (ADR-0006/ADR-0017, T1.7's gate): every LLM call in this module passes `phi=True`
(the message/dossier text is built from beneficiary-adjacent facts) — a non-PHI-capable provider
raises `PhiZoneRoutingError`, which this graph does NOT special-case: `_build_message`/
`_build_dossier` degrade to a safe, deterministic minimal text on ANY LLM failure (including that
one) — the route is already decided by the time these run, so a drafting failure never blocks a
neutral notify or a human escalation (mirrors Helena's `_respond_llm`/Rafael's `_build_dossier`
fail-safe-open text, NOT a fail-closed route change — those are different failure classes).

LABELED BOUNDARIES (this build, disclosed — never fabricated):
- Episodic/semantic memory write (`mcp-memory.read_write`, ADR-0002) is NOT wired in this graph —
  same follow-up as Helena/Rafael (v2's `MemoryServer` needs a live Postgres/pgvector schema that
  does not exist in this repo's migrations yet). The donor's `finalize`/memory node has no v2
  analog here.
- A2A inbound delegation (`arrears.followup`, Lucas -> Fernando, `spec/agents/fernando/
  agent.yaml`'s `accepted_task_types`) is NOT wired: v2's `a2a/` package has no
  `DelegationEnvelope`/`DelegationDispatcher` yet (same boundary Rafael's module docstring
  discloses for Helena -> Rafael). This graph is invoked directly with an already-assembled
  `FernandoState` (as the unit tests do), not via a live delegation.
- `tipo_plano`/`canal` are consumed as opaque strings (passed straight to the DMN / dossier /
  engine variables) with NO closed-allowlist validation of their own — unlike `intencao`. The
  DMN's own catch-all rows (`spec/processes/dmn/inadimplencia_status.dmn`'s `r_catchall`,
  `.../inadimplencia_sla.dmn`'s `r_catchall`) already fail safe to a conservative output for an
  unrecognized `tipo_plano`, so an invalid value here cannot silently misroute a DMN decision —
  it is a lower-severity residual than `intencao` (which directly drives THIS graph's own
  Python-level routing, not just a DMN row match) and is disclosed, not hidden.

DIVERGENCE FROM DONOR (spec wins — `spec/processes/dmn/inadimplencia_sla.dmn` vs the donor):
the donor's `_assess_escalation` calls `inadimplencia_sla` with `{tipo_plano, motivo,
meses_inadimplencia}` and reads a `roteamento` output to pick a `grupo_humano` (JURIDICO_
CONTRATOS/GESTAO_COBRANCA/COORDENACAO_COBRANCA). The DEPLOYED v2 spec table
(`spec/processes/dmn/inadimplencia_sla.dmn`) takes ONLY `tipo_plano` and outputs
`{sla_analise, sla_alerta, fonte_regulatoria}` — there is no `roteamento`/group-selection output.
The v2 BPMN (`spec/processes/bpmn/SP-OP-INADIMPLENCIA-001_Suspensao_Rescisao.bpmn`) uses STATIC
`candidateGroups` on `UT_AnaliseInadimplencia` (`juridico-contratos,gestao-cobranca`) and
`UT_CoordenacaoCobranca` (`coordenacao-cobranca`) instead of agent-computed group routing. Per
"spec wins," this graph does NOT compute/emit a `grupo_humano` — `inadimplencia_sla` is consulted
purely for informational dossier content (`sla_analise_iso`/`sla_alerta_iso`/
`fonte_regulatoria_sla`), best-effort (a failure never blocks the already-decided `escalate`
route, mirrors Rafael's `auth_sla`).
"""

from __future__ import annotations

from typing import Any, Literal, Protocol, TypedDict, cast

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
    DOSSIER_PROMPT_VERSION,
    MESSAGE_PROMPT_VERSION,
    SYSTEM_PROMPT_VERSION,
    dossier_prompt,
    message_prompt,
)

PROCESS_KEY = "SP-OP-INADIMPLENCIA-001"

DMN_STATUS = "inadimplencia_status"
DMN_PURGA = "inadimplencia_purga"
DMN_SLA = "inadimplencia_sla"

# Intencao (journey selector) — decides which of the three journeys to run, NEVER a merit.
Intencao = Literal["notificacao_previa", "acompanhamento_purga", "analise_inadimplencia", "rescisao"]
_VALID_INTENCOES: frozenset[str] = frozenset(
    {"notificacao_previa", "acompanhamento_purga", "analise_inadimplencia", "rescisao"}
)

# Roteamento allowlist — CLOSED set, mirrors the DMN's own contract (no SUSPENDER/RESCINDIR
# output exists by design). A value outside this set is a DMN-contract violation and fails
# CLOSED to `escalate`, never treated as an implicit auto-response (fail-safe fechado).
StatusInadimplencia = Literal["AGUARDA_PURGA", "PENDENTE_NOTIFICACAO", "SEGUE_ANALISE", "ANALISE_HUMANA"]
_STATUS_ALLOW: frozenset[str] = frozenset(
    {"AGUARDA_PURGA", "PENDENTE_NOTIFICACAO", "SEGUE_ANALISE", "ANALISE_HUMANA"}
)
_STATUS_NOTIFY: frozenset[str] = frozenset({"AGUARDA_PURGA", "PENDENTE_NOTIFICACAO"})

# Graph-level routing. STRUCTURALLY no adverse variant exists — `notify` is always informational,
# `escalate` always routes to a human User Task; neither ever suspends/rescinds/denies.
Route = Literal["notify", "escalate"]

MotivoHumano = Literal[
    "segue_analise",  # periodo minimo + notificacao + purga decorrida -> humano (J2 safety net)
    "analise_humana",  # DMN catch-all/coletivo -> humano (J2 safety net)
    "analise_solicitada",  # J3 analise_inadimplencia: humano por pedido explicito
    "indicio_rescisao",  # J3 rescisao: humano (handoff a CANCEL-001 e do PROCESSO, nunca daqui)
    "ambiguidade",  # intencao invalida ou roteamento fora da allowlist -> humano conservador
    "dmn_indisponivel",  # DMN indisponivel -> humano (fail-safe fechado)
    "falha_tecnica",  # contexto de runtime ausente -> humano por seguranca
]
MotivoCategoria = Literal["inadimplencia", "rescisao", "dmn_unavailable", "ambiguity", "falha_tecnica"]


class WhatsAppSender(Protocol):
    """Outbound WhatsApp send seam. Operates on a phone HASH, never a raw number — Fernando's
    state is pseudonymized end to end (ADR-0006). Structurally identical to `helena.graph.
    WhatsAppSender` (same adapter, `agents.helena.adapters.WhatsAppServerSender`, satisfies both
    — Protocols are structural, so this is a deliberate duck-typed reuse, not a copy-paste
    drift risk: any future divergence in the two Protocols would be caught by mypy at the call
    site, not silently)."""

    async def send(self, to_hash: str, text: str) -> dict[str, Any]: ...


# --- Graph state (working memory; ADR-0002 working-memory layer) ---------------------------


class FernandoState(TypedDict, total=False):
    """Cobranca/inadimplencia case state. Everything here is pseudonymized (Zona Geral,
    ADR-0006) — `beneficiario_pseudo_id`/`to_hash` NEVER carry a raw CPF/name/phone number. The
    inadimplencia FACTS (`meses_inadimplencia`, `valor_total_devido_cents`,
    `dentro_periodo_minimo`, `notificacao_previa_feita`, `dentro_janela_purga`,
    `ja_em_rescisao_cancel`) arrive PRE-RESOLVED by `operadora.inadimplencia.resolve_facts`
    (T1.5) — this graph CONSUMES them, never computes them (module docstring's J2 invariant)."""

    # Runtime identifiers / task framing.
    tenant_id: str
    intencao: Intencao
    canal: str  # whatsapp | portal | telefone
    numero_contrato: str  # business key material (same identity CANCEL-001 uses)
    matricula_beneficiario: str  # pseudonymized enrollment key (fallback business key material)
    beneficiario_pseudo_id: str
    to_hash: str  # WhatsApp phone HASH (never the raw number) — message destination
    tipo_plano: str  # individual | familiar | coletivo_empresarial | coletivo_adesao
    origem_solicitacao: str  # cobranca | operadora | agente_fernando | juridico
    data_solicitacao_iso: str

    # Pre-resolved inadimplencia facts (worker `operadora.inadimplencia.resolve_facts`) —
    # CONSUMED, never computed here.
    competencias_em_aberto: list[Any]
    meses_inadimplencia: int
    valor_total_devido_cents: int
    dentro_periodo_minimo: bool
    notificacao_previa_feita: bool
    dentro_janela_purga: bool
    ja_em_rescisao_cancel: bool
    documentos_refs: list[dict[str, Any]]

    # Filled by `assess`.
    status_inadimplencia: StatusInadimplencia
    prazo_purga_iso: str
    prazo_notificacao_previa_iso: str
    periodo_minimo_iso: str
    fonte_regulatoria_purga: str
    sla_analise_iso: str
    sla_alerta_iso: str
    fonte_regulatoria_sla: str
    dmn_refs: dict[str, str]
    dmn_error: str

    # Routing.
    route: Route
    motivo_humano: MotivoHumano
    motivo_categoria: MotivoCategoria
    business_key: str
    error: str

    # `notify` outputs.
    mensagem: dict[str, Any]
    mensagem_enviada: bool

    # `escalate` outputs.
    dossier: dict[str, Any]

    # `start_process` outputs.
    process_started: bool
    process_ref: dict[str, Any]

    # Turn output.
    desfecho: str


# --- Helpers ---------------------------------------------------------------------------


def _business_key(state: FernandoState) -> str:
    """Idempotent business key per contract: `INAD-{tenant_id}-{numero_contrato}` — falls back
    to `matricula_beneficiario` when there is no contract number (individual/familiar plans),
    matching the BPMN's own documented variant (`spec/processes/bpmn/
    SP-OP-INADIMPLENCIA-001_Suspensao_Rescisao.bpmn`'s `<bpmn:documentation>`, "BUSINESS KEY").
    """
    tenant = state.get("tenant_id", "")
    contrato = state.get("numero_contrato") or state.get("matricula_beneficiario", "")
    return f"INAD-{tenant}-{contrato}"


def _motivo_categoria(motivo: MotivoHumano) -> MotivoCategoria:
    """Maps the internal reason to the engine-facing category (agent.yaml's own `escalation.
    triggers` vocabulary: `signal: inadimplencia`/`rescisao`/`dmn_unavailable`/`ambiguity`)."""
    if motivo == "falha_tecnica":
        return "falha_tecnica"
    if motivo == "dmn_indisponivel":
        return "dmn_unavailable"
    if motivo == "ambiguidade":
        return "ambiguity"
    if motivo == "indicio_rescisao":
        return "rescisao"
    return "inadimplencia"


def _output_field_resets() -> dict[str, Any]:
    """Fresh neutral values for EVERY output-only state key this graph itself fills.

    `receive` merges these into its return on BOTH the success and fail paths (R1 cycle-1 fix
    — helena-cycle-2 class): LangGraph merges the caller's initial input state verbatim, and a
    key no node overwrites flows through to the final state — so a caller-PLANTED value in an
    OUTPUT field would read through into downstream nodes and engine variables on any journey
    where the graph deliberately does not compute that field. Live example (R1 verifier probe):
    `status_inadimplencia="SUSPENDER CPF=..."` planted in caller state on the `rescisao`
    journey (where `assess` never classifies) landed verbatim in `dossier["fatos"]` ->
    `dossie_fernando` -> SP-OP-INADIMPLENCIA-001 engine process variables (general zone).
    Resetting every output field at turn start closes the CLASS, not just that field — including
    `business_key` (a planted one would otherwise become the ENGINE business key on
    receive-fail paths, since `start_process` falls back `state.get("business_key") or ...`)
    and the observability outputs (`process_started`/`process_ref`/`desfecho` — a planted value
    would fabricate a turn outcome that never happened).

    Returns a FRESH dict with fresh nested containers per call — never a shared module-level
    constant whose mutable values could alias across turns.
    """
    return {
        # Filled by `assess`.
        "status_inadimplencia": None,
        "prazo_purga_iso": "",
        "prazo_notificacao_previa_iso": "",
        "periodo_minimo_iso": "",
        "fonte_regulatoria_purga": "",
        "sla_analise_iso": "",
        "sla_alerta_iso": "",
        "fonte_regulatoria_sla": "",
        "dmn_refs": {},
        "dmn_error": "",
        # Routing.
        "route": None,
        "motivo_humano": None,
        "motivo_categoria": None,
        "business_key": "",
        "error": "",
        # `notify` outputs.
        "mensagem": {},
        "mensagem_enviada": False,
        # `escalate` outputs.
        "dossier": {},
        # `start_process` outputs.
        "process_started": False,
        "process_ref": {},
        # Turn output.
        "desfecho": "",
    }


# --- Graph ------------------------------------------------------------------------------------


class FernandoGraph:
    """Wires Fernando's injected dependencies into a compilable `StateGraph[FernandoState]`.

    The runtime (`agent_runtime`) or the harness's `build()` contract instantiates this with
    concrete transports; tests inject `FakeDmnTransport`/`FakeCibSevenTransport`/a fake inference
    provider/a fake `WhatsAppSender`.
    """

    def __init__(
        self,
        *,
        inference: InferenceProvider,
        dmn: DmnTransport,
        cibseven: CibSevenTransport,
        audit_sink: AuditStartSink,
        whatsapp: WhatsAppSender,
        agent_version: str = "fernando@v0",
    ) -> None:
        self._llm = inference
        self._dmn = dmn
        self._cibseven = cibseven
        # T-C2 fence: required durable ADR-0007 sink for the SP-OP-INADIMPLENCIA-001 start.
        self._audit_sink = audit_sink
        self._model_id = getattr(inference, "model_id", None)
        self._whatsapp = whatsapp
        self._agent_version = agent_version

    # -- Nodes ----------------------------------------------------------------------------

    async def receive(self, state: FernandoState) -> dict[str, Any]:
        """Turn start: RESET every output-only state field (`_output_field_resets` — R1 cycle-1
        fix: caller-planted values in output fields must never read through into downstream
        nodes/engine variables), then defend against missing runtime context / an unrecognized
        `intencao` (fail-closed -> escalate). NEVER echoes the raw offending value — class
        tokens only (module docstring's engine-variable-hygiene section)."""
        if not state.get("tenant_id"):
            return self._escalate_min("falha_tecnica", "missing_tenant_id")
        if state.get("intencao") not in _VALID_INTENCOES:
            return self._escalate_min("ambiguidade", "invalid_intencao")
        if not (state.get("numero_contrato") or state.get("matricula_beneficiario")):
            return self._escalate_min("falha_tecnica", "missing_contract_key")
        return {**_output_field_resets(), "business_key": _business_key(state)}

    async def assess(self, state: FernandoState) -> dict[str, Any]:
        """Evaluate the deterministic DMNs and decide `route` — NEVER by merit, only by the
        pre-resolved facts + the DMN's own classification (ADR-0012: the DMN decides, the LLM
        only reasons about the RESULT when drafting text downstream).

        FAIL-CLOSED (module docstring): DMN unavailable, an out-of-allowlist `roteamento`, or an
        already-routed `receive` failure -> `escalate`, NEVER a silent `notify` default.
        """
        if state.get("error"):
            return {}  # already routed to escalate by `receive`

        dmn_refs: dict[str, str] = {}
        intencao = state.get("intencao")

        # J3 `rescisao` shortcut: NEVER consults `inadimplencia_status` — a rescission
        # indicium/request is never a neutral, DMN-gated case (donor parity, module docstring).
        if intencao == "rescisao":
            return await self._assess_escalation(state, dmn_refs, motivo="indicio_rescisao")

        status_result = await self._evaluate_dmn(
            DMN_STATUS,
            {
                "meses_inadimplencia": int(state.get("meses_inadimplencia", 0) or 0),
                "dentro_periodo_minimo": bool(state.get("dentro_periodo_minimo", False)),
                "notificacao_previa_feita": bool(state.get("notificacao_previa_feita", False)),
                "dentro_janela_purga": bool(state.get("dentro_janela_purga", False)),
                "tipo_plano": str(state.get("tipo_plano", "")),
            },
        )
        if status_result.get("error"):
            return await self._assess_escalation(
                state, dmn_refs, motivo="dmn_indisponivel", dmn_error=str(status_result["error"])
            )
        dmn_refs[DMN_STATUS] = status_result["ref"]
        roteamento = str(status_result["row"].get("roteamento", ""))

        # FAIL-SAFE FECHADO: a value outside the closed allowlist never "passes through" as an
        # implicit auto-response — conservative human escalation instead.
        if roteamento not in _STATUS_ALLOW:
            return await self._assess_escalation(state, dmn_refs, motivo="ambiguidade")
        status_inadimplencia = cast(StatusInadimplencia, roteamento)

        # J3 `analise_inadimplencia`: ALWAYS escalates (explicit human-analysis request honored
        # unconditionally), regardless of what the DMN says — module docstring.
        if intencao == "analise_inadimplencia":
            motivo: MotivoHumano = "analise_solicitada"
            if status_inadimplencia == "SEGUE_ANALISE":
                motivo = "segue_analise"
            elif status_inadimplencia == "ANALISE_HUMANA":
                motivo = "analise_humana"
            return await self._assess_escalation(
                state, dmn_refs, motivo=motivo, status_inadimplencia=status_inadimplencia
            )

        # J1/J2 (`notificacao_previa` / `acompanhamento_purga`): DMN-driven safety net — purga
        # elapsed (SEGUE_ANALISE/ANALISE_HUMANA) escalates exactly like the explicit J3 request
        # above; only AGUARDA_PURGA/PENDENTE_NOTIFICACAO stay on the neutral `notify` path.
        if status_inadimplencia not in _STATUS_NOTIFY:
            motivo = "segue_analise" if status_inadimplencia == "SEGUE_ANALISE" else "analise_humana"
            return await self._assess_escalation(
                state, dmn_refs, motivo=motivo, status_inadimplencia=status_inadimplencia
            )

        # Purga/notice deadlines — best-effort informational context for the message (mirrors
        # `inadimplencia_sla`'s best-effort stance below; never blocks the already-decided
        # `notify` route).
        purga_result = await self._evaluate_dmn(DMN_PURGA, {"tipo_plano": str(state.get("tipo_plano", ""))})
        prazo_purga = prazo_notif = periodo_minimo = fonte_purga = ""
        if not purga_result.get("error"):
            dmn_refs[DMN_PURGA] = purga_result["ref"]
            purga_row = purga_result["row"]
            prazo_purga = str(purga_row.get("prazo_purga", ""))
            prazo_notif = str(purga_row.get("prazo_notificacao_previa", ""))
            periodo_minimo = str(purga_row.get("periodo_minimo", ""))
            fonte_purga = str(purga_row.get("fonte_regulatoria", ""))

        return {
            "route": "notify",
            "status_inadimplencia": status_inadimplencia,
            "prazo_purga_iso": prazo_purga,
            "prazo_notificacao_previa_iso": prazo_notif,
            "periodo_minimo_iso": periodo_minimo,
            "fonte_regulatoria_purga": fonte_purga,
            "dmn_refs": dmn_refs,
        }

    async def notify(self, state: FernandoState) -> dict[str, Any]:
        """J1/J2 neutral path: draft + send the prior notice / regularization reminder /
        follow-up status. NEVER threatens suspension/rescission (structural guardrail in
        `_build_message`). A WhatsApp send failure is recorded but NEVER escalates — an
        undelivered notice is not an adverse effect."""
        mensagem = await self._build_message(state)
        enviada = False
        to_hash = state.get("to_hash") or state.get("beneficiario_pseudo_id")
        if to_hash and state.get("canal", "whatsapp") == "whatsapp":
            try:
                await self._whatsapp.send(to_hash, str(mensagem.get("texto", "")))
                enviada = True
            except Exception as exc:  # noqa: BLE001 — best-effort send, never an adverse effect.
                mensagem["envio_nota"] = f"envio WhatsApp indisponivel: {type(exc).__name__}"

        desfecho = (
            "notificacao_previa_enviada"
            if state.get("status_inadimplencia") == "PENDENTE_NOTIFICACAO"
            else "lembrete_regularizacao_enviado"
        )
        return {"mensagem": mensagem, "mensagem_enviada": enviada, "desfecho": desfecho}

    async def escalate(self, state: FernandoState) -> dict[str, Any]:
        """J3 / fail-safe path: assemble the analysis dossier. NEVER decides — the dossier
        INSTRUCTS the human, it never substitutes for `UT_AnaliseInadimplencia` (mirrors
        Rafael's `human_auditor`/`_build_dossier` discipline exactly)."""
        dossier = await self._build_dossier(state)
        return {"dossier": dossier, "desfecho": "encaminhado_analise_humana"}

    async def start_process(self, state: FernandoState) -> dict[str, Any]:
        """Start SP-OP-INADIMPLENCIA-001 idempotently (business key `INAD-{tenant}-{contrato}`).
        Only reached on the `escalate` route (graph topology) — `notify` never starts a process
        (mirrors the donor's explicit design note and the BPMN: the notice/reminder path is
        handled entirely inside an ALREADY-running instance's own cure-window, never something
        Fernando originates)."""
        business_key = state.get("business_key") or _business_key(state)
        variables = self._inadimplencia_variables(state)
        provenance = AgentDecisionProvenance(
            agent_id="fernando",
            agent_version=self._agent_version,
            tenant_id=state.get("tenant_id", ""),
            model_id=self._model_id,
            prompt_version=SYSTEM_PROMPT_VERSION,
            decision_basis={
                "route": state.get("route", ""),
                "desfecho": state.get("desfecho", ""),
                "status_inadimplencia": state.get("status_inadimplencia") or "",
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

    # -- Conditional routing ----------------------------------------------------------------

    @staticmethod
    def _route(state: FernandoState) -> str:
        # FAIL-SAFE: on absence/doubt, ALWAYS escalate (never `notify` by omission).
        return "notify" if state.get("route") == "notify" else "escalate"

    @staticmethod
    def _escalate_min(motivo: MotivoHumano, error_token: str) -> dict[str, Any]:
        """Minimal fail-closed escalation used by `receive`, before any DMN runs. `error_token`
        is a bounded CLASS TOKEN ONLY — never the raw offending value (module docstring's
        engine-variable-hygiene section; unlike the donor's `graph.py:332`, which echoes
        `intencao!r}` verbatim). Merges `_output_field_resets()` FIRST (R1 cycle-1 fix): the
        receive-fail journeys were exactly where caller-planted output fields (e.g.
        `status_inadimplencia`, `sla_analise_iso`, `business_key`) read through into engine
        variables, because no downstream node overwrites them on these paths."""
        return {
            **_output_field_resets(),
            "route": "escalate",
            "motivo_humano": motivo,
            "motivo_categoria": _motivo_categoria(motivo),
            "error": error_token,
        }

    # -- DMN (ADR-0012/ADR-0028): the DMN decides, never the LLM -----------------------------

    async def _evaluate_dmn(self, table: str, dmn_input: dict[str, Any]) -> dict[str, Any]:
        try:
            rows, version = await self._dmn.evaluate(table, dmn_input)
            row = first_row(rows, table, dmn_input)
        except (DmnEvaluationError, DmnNoResultError) as exc:
            return {"error": f"DMN `{table}` indisponivel: {exc}"}
        # See `helena/graph.py::_evaluate_dmn` for why this cites the decision-definition id
        # (ADR-0028 §2) rather than a rule id the engine's evaluate response never returns.
        return {"row": row, "ref": f"{table}#{version.id}"}

    async def _assess_escalation(
        self,
        state: FernandoState,
        dmn_refs: dict[str, str],
        *,
        motivo: MotivoHumano,
        dmn_error: str | None = None,
        status_inadimplencia: StatusInadimplencia | None = None,
    ) -> dict[str, Any]:
        """Resolve the escalation payload. The destination is ALWAYS `escalate` — `inadimplencia_
        sla` is consulted purely for informational SLA context (never a group/route decision,
        module docstring's donor divergence), best-effort (a failure never blocks the
        already-decided `escalate` route, mirrors Rafael's `auth_sla`)."""
        sla_result = await self._evaluate_dmn(DMN_SLA, {"tipo_plano": str(state.get("tipo_plano", ""))})
        sla_analise = sla_alerta = fonte_sla = ""
        if not sla_result.get("error"):
            dmn_refs[DMN_SLA] = sla_result["ref"]
            sla_row = sla_result["row"]
            sla_analise = str(sla_row.get("sla_analise", ""))
            sla_alerta = str(sla_row.get("sla_alerta", ""))
            fonte_sla = str(sla_row.get("fonte_regulatoria", ""))

        out: dict[str, Any] = {
            "route": "escalate",
            "motivo_humano": motivo,
            "motivo_categoria": _motivo_categoria(motivo),
            "dmn_refs": dmn_refs,
            "sla_analise_iso": sla_analise,
            "sla_alerta_iso": sla_alerta,
            "fonte_regulatoria_sla": fonte_sla,
        }
        if status_inadimplencia is not None:
            out["status_inadimplencia"] = status_inadimplencia
        if dmn_error is not None:
            out["dmn_error"] = dmn_error
        return out

    # -- LLM helpers (all PHI-tagged — ADR-0006/ADR-0017/T1.7) ------------------------------

    async def _build_message(self, state: FernandoState) -> dict[str, Any]:
        """Draft the prior-notice/reminder text (J1/J2). LLM failure degrades to a safe,
        deterministic minimal text — NEVER blocks the already-decided neutral `notify` route
        (mirrors Helena's `_respond_llm`). `status_inadimplencia` gets the same read-side
        `_STATUS_ALLOW` treatment as `_build_dossier`'s (R1 cycle-1 fix, symmetric defense in
        depth on top of `receive`'s write-side reset — the message facts feed an LLM prompt and
        the turn's `mensagem` output, never engine variables, but the discipline is uniform)."""
        status_raw = state.get("status_inadimplencia")
        status_validated = status_raw if status_raw in _STATUS_ALLOW else None
        facts: dict[str, Any] = {
            "numero_contrato": state.get("numero_contrato"),
            "tipo_plano": state.get("tipo_plano"),
            "competencias_em_aberto": state.get("competencias_em_aberto", []),
            "status_inadimplencia": status_validated,
            "dentro_janela_purga": state.get("dentro_janela_purga"),
            "prazo_purga_iso": state.get("prazo_purga_iso"),
            "prazo_notificacao_previa_iso": state.get("prazo_notificacao_previa_iso"),
            "fonte_regulatoria_purga": state.get("fonte_regulatoria_purga"),
        }
        intencao = state.get("intencao")
        intencao_validated = intencao if intencao in _VALID_INTENCOES else None
        prompt = f"{message_prompt()}\n\nintencao={intencao_validated}\nfatos={facts}"
        try:
            texto = await self._llm.generate(
                prompt, phi=True, agent_id="fernando", tenant_id=state.get("tenant_id", "")
            )
        except Exception:  # noqa: BLE001 — fail-safe default: never leave the beneficiary with nothing.
            texto = "Identificamos uma pendencia em seu contrato. Consulte os canais de regularizacao."

        return {
            "prompt_version": MESSAGE_PROMPT_VERSION,
            "fatos": facts,
            "texto": texto,
            # STRUCTURAL GUARDRAIL (L0 hard): the notice/reminder NEVER carries a communication
            # of suspension/rescission — always None, a bug would be immediately detectable.
            "comunicacao_suspensao": None,
            "comunicacao_rescisao": None,
        }

    async def _build_dossier(self, state: FernandoState) -> dict[str, Any]:
        """Assemble the analysis dossier (J3 / fail-safe). LLM failure degrades to a safe,
        deterministic minimal narrativa — NEVER blocks the already-decided `escalate` route
        (mirrors Rafael's `_build_dossier`).

        `facts["intencao"]` is populated ONLY from the closed `_VALID_INTENCOES` allowlist — an
        invalid/unvalidated value is NEVER echoed here (module docstring's engine-variable-
        hygiene section; this is the load-bearing fix over the donor, whose `graph.py:782`
        embeds the raw `state.get("intencao")` unconditionally). `facts["status_inadimplencia"]`
        gets the same read-side treatment against `_STATUS_ALLOW` (R1 cycle-1 fix, defense in
        depth on top of `receive`'s write-side reset): on the `rescisao`/DMN-unavailable/
        receive-fail journeys the graph deliberately never classifies, so this field's value at
        read time is only trustworthy if it survived the closed allowlist."""
        intencao = state.get("intencao")
        intencao_validated = intencao if intencao in _VALID_INTENCOES else None
        status_raw = state.get("status_inadimplencia")
        status_validated = status_raw if status_raw in _STATUS_ALLOW else None
        facts: dict[str, Any] = {
            "intencao": intencao_validated,
            "numero_contrato": state.get("numero_contrato"),
            "tipo_plano": state.get("tipo_plano"),
            "competencias_em_aberto": state.get("competencias_em_aberto", []),
            "meses_inadimplencia": state.get("meses_inadimplencia"),
            "valor_total_devido_cents": state.get("valor_total_devido_cents"),
            "dentro_periodo_minimo": state.get("dentro_periodo_minimo"),
            "notificacao_previa_feita": state.get("notificacao_previa_feita"),
            "dentro_janela_purga": state.get("dentro_janela_purga"),
            "ja_em_rescisao_cancel": state.get("ja_em_rescisao_cancel"),
            "motivo_humano": state.get("motivo_humano"),
            "status_inadimplencia": status_validated,
            "sla_analise_iso": state.get("sla_analise_iso"),
            "fonte_regulatoria_sla": state.get("fonte_regulatoria_sla"),
            "dmn_refs": state.get("dmn_refs", {}),
        }
        prompt = f"{dossier_prompt()}\n\nmotivo={state.get('motivo_humano')}\nfatos={facts}"
        try:
            narrativa = await self._llm.generate(
                prompt, phi=True, agent_id="fernando", tenant_id=state.get("tenant_id", "")
            )
        except Exception:  # noqa: BLE001 — LLM failure never blocks the human escalation.
            narrativa = ""

        return {
            "prompt_version": DOSSIER_PROMPT_VERSION,
            "motivo_humano": state.get("motivo_humano"),
            "fatos": facts,
            "dmn_decision_refs": state.get("dmn_refs", {}),
            "narrativa": narrativa,
            "documentos_refs": state.get("documentos_refs") or [],
            # STRUCTURAL GUARDRAILS (L0 hard) — mirrors Rafael's `decisao_cobertura=None`. The
            # dossier NEVER carries a decision; it is always the human's.
            "decisao_inadimplencia": None,
            "decisao_recomendada": None,
        }

    def _inadimplencia_variables(self, state: FernandoState) -> dict[str, Any]:
        """Build SP-OP-INADIMPLENCIA-001's input variables (contract §VARIAVEIS DE ENTRADA,
        BPMN documentation). Facts travel as integers (cents / month counts — contract:
        never `number`). `decisao_inadimplencia` is ALWAYS `None` (L0 hard structural
        guardrail) — the decision is exclusively `UT_AnaliseInadimplencia`'s."""
        dossier = state.get("dossier") or {}
        resumo = str(dossier.get("narrativa", "")) or "Encaminhamento automatico (sem narrativa)"
        variables: dict[str, Any] = {
            "tenant_id": state.get("tenant_id", ""),
            "source_agent_id": "fernando",
            "source_agent_version": self._agent_version,
            "numero_contrato": state.get("numero_contrato", "") or state.get("matricula_beneficiario", ""),
            "matricula_beneficiario": state.get("matricula_beneficiario", ""),
            "beneficiario_pseudo_id": state.get("beneficiario_pseudo_id", ""),
            "tipo_plano": state.get("tipo_plano", ""),
            "origem_solicitacao": state.get("origem_solicitacao") or "agente_fernando",
            "canal": state.get("canal", "whatsapp"),
            "motivo_categoria": state.get("motivo_categoria", "inadimplencia"),
            "competencias_em_aberto": state.get("competencias_em_aberto", []),
            "meses_inadimplencia": int(state.get("meses_inadimplencia", 0) or 0),
            "valor_total_devido_cents": int(state.get("valor_total_devido_cents", 0) or 0),
            "dentro_periodo_minimo": bool(state.get("dentro_periodo_minimo", False)),
            "notificacao_previa_feita": bool(state.get("notificacao_previa_feita", False)),
            "dentro_janela_purga": bool(state.get("dentro_janela_purga", False)),
            "documentos_refs": state.get("documentos_refs") or [],
            "resumo_contexto": resumo,
            "fernando_route": state.get("route", "escalate"),
            "motivo_encaminhamento": state.get("motivo_humano", ""),
            "dossie_fernando": dossier,
            # STRUCTURAL GUARDRAIL (L0 hard): Fernando NEVER sets an adverse decision — always
            # None, exclusively the human User Task's field to fill.
            "decisao_inadimplencia": None,
        }
        ja_em_rescisao = state.get("ja_em_rescisao_cancel")
        if ja_em_rescisao is not None:
            variables["ja_em_rescisao_cancel"] = bool(ja_em_rescisao)
        data_solicitacao = state.get("data_solicitacao_iso")
        if data_solicitacao:
            variables["data_solicitacao_iso"] = data_solicitacao
        dmn_refs = state.get("dmn_refs")
        if dmn_refs:
            variables["dmn_decision_refs"] = dmn_refs
            variables["dmn_decision_ref"] = next(iter(dmn_refs.values()), "")
        return variables

    # -- Graph assembly -----------------------------------------------------------------------

    def compile_graph(self) -> StateGraph[FernandoState]:
        g: StateGraph[FernandoState] = StateGraph(FernandoState)
        g.add_node("receive", self.receive)
        g.add_node("assess", self.assess)
        g.add_node("notify", self.notify)
        g.add_node("escalate", self.escalate)
        g.add_node("start_process", self.start_process)

        g.add_edge(START, "receive")
        g.add_edge("receive", "assess")
        g.add_conditional_edges("assess", self._route, {"notify": "notify", "escalate": "escalate"})
        g.add_edge("notify", END)
        g.add_edge("escalate", "start_process")
        g.add_edge("start_process", END)
        return g


def build(config: dict[str, Any] | None = None) -> StateGraph[FernandoState]:
    """Contract `_template/graph.py:build`, resolved by `AgentLoader`/`runtime.harness.Harness`.

    `config` MUST contain:
      - `inference`: an `InferenceProvider` (ADR-0009).
      - `dmn`: a `DmnTransport` (ADR-0028/T1.5).
      - `cibseven`: a `CibSevenTransport` (ADR-0001/T1.11).
      - `whatsapp`: a `WhatsAppSender`.
    Optional:
      - `agent_version`: audit provenance string (ADR-0007), defaults to `"fernando@v0"`.

    Fail-closed: missing a required dependency raises `ValueError` at build time — Fernando
    never silently constructs a graph that would crash mid-case on its first tool call (mirrors
    Helena's `build`).
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
            f"Fernando build(config) is missing required dependencies: {missing} "
            "(ADR-0001/0007/0009/0028 — inference/dmn/cibseven/whatsapp/audit_sink must all be "
            "injected; audit_sink is the T-C2 fence — no process start without a durable sink)"
        )
    agent_version = str(cfg.get("agent_version", "fernando@v0"))
    return FernandoGraph(
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

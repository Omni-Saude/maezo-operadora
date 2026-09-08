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
exact probe) and the `test_caller_planted_*` family. `fonte_regulatoria_*` (free-form regulatory
CITATION text, no finite domain and no declared format — ADR-0018 does not shape it) still has
no read-side check of its own; the write-side reset remains its load-bearing guard.

FER-08 (fleet audit ciclo 2, CLOSED for the `*_iso` fields): `sla_analise_iso`/`prazo_purga_iso`/
`prazo_notificacao_previa_iso` DO have a declared shape — ADR-0018 §4-bis-A ("dias/SLA -> string
ISO 8601") and the deployed DMNs themselves (`spec/processes/dmn/inadimplencia_{purga,sla}.dmn`,
`typeRef="string"` outputs like `"P10D"`/`"P7D"`) both fix them as ISO-8601 DURATIONS — a shape a
corrupted DMN response or a future non-conforming table revision could violate. `_build_message`/
`_build_dossier` now re-validate them at the dossier-assembly read-site via
`maezo.runtime.guards.require_iso8601_duration` (format-only, C3: no threshold/business rule) —
an out-of-shape value is blanked (never echoed as a fact that looks like a real deadline) and
logged as a structured warning (`fernando_iso_duration_invalido`), citing only the FIELD NAME,
never the raw value (which the DMN never sources from caller input, but is untrusted transport
content regardless). This is defense in depth on top of the same write-side reset — the DMN
integration is the load-bearing source either way.

PHI discipline (ADR-0006/ADR-0017, T1.7's gate): every LLM call in this module passes `phi=True`
(the message/dossier text is built from beneficiary-adjacent facts) — a non-PHI-capable provider
raises `PhiZoneRoutingError`, which this graph does NOT special-case: `_build_message`/
`_build_dossier` degrade to a safe, deterministic minimal text on ANY LLM failure (including that
one) — the route is already decided by the time these run, so a drafting failure never blocks a
neutral notify or a human escalation (mirrors Helena's `_respond_llm`/Rafael's `_build_dossier`
fail-safe-open text, NOT a fail-closed route change — those are different failure classes).

LABELED BOUNDARIES (this build, disclosed — never fabricated):
- Episodic memory write (`mcp-memory.read_write`, ADR-0002) is NOT wired in this graph — same
  follow-up as Helena/Rafael. The episodic table exists (`agent_memory`, migration `0001`); what
  blocks the write is `store_episodic`'s `(agent_id, event)` signature, which carries neither
  `tenant_id` nor `thread_id` (both `text NOT NULL`) — GAP-DU-01-a. There is no SEMANTIC write to
  wire at all any more: `0009_drop_pgvector` removed the column, and ADR-0002 §3 is SUSPENDED
  pending a consumer (ADR-0047, DRAFT). The donor's `finalize`/memory node has no v2 analog
  here.
- A2A inbound delegation (`arrears.followup`, `spec/agents/fernando/agent.yaml`'s
  `accepted_task_types`) is REGISTERED **and ORIGINATED**. UPDATED 2026-09-05 (owner decision
  R-081, gap `FERNANDO-DELEGATION-CALL-SITE`) — three earlier claims were overtaken and are
  corrected here rather than left to rot: (a) "no `make_fernando_handler` exists anywhere in this
  repo" is FALSE since `agents/fernando/delegation.py` landed (`make_fernando_handler`/
  `state_from_envelope`, mirroring Carolina's pattern); (b) "NOT wired" is FALSE for the
  REGISTRATION half — `runtime/agent_runtime/a2a_composition.py::
  build_dossier_delegation_dispatcher` registers `handlers={..., "fernando": fernando_handler}`
  and `_DOSSIER_EDGE_AGENT_IDS` includes him, so an `arrears.followup` envelope delivered to that
  dispatcher REACHES this graph (proved end to end by `tests/unit/runtime/agent_runtime/
  test_a2a_composition.py::test_dossier_dispatcher_routes_the_registered_fernando_edge`); and
  (c) "no production code path ORIGINATES a turn" is FALSE since the second half of R-081 landed
  — `tools/workers/inadimplencia.py::make_prepare_dossier_handler` (the raw-async form of
  `operadora.inadimplencia.prepare_dossier`, precedent `credenciamento.py::
  make_prepare_dossier_handler`) calls `delegate_arrears_followup(...)` from INSIDE a running
  SP-OP-INADIMPLENCIA-001 instance, FAIL-NEUTRALLY (a delegation failure never fails the CIB
  Seven task and never fabricates success; the human User Task always opens with the gap
  disclosed in `arrears_followup_gap`). Lucas is explicitly NOT the origin (owner decision
  R-082: no new A2A contract for Lucas until a journey names the consumer), which is why this
  line no longer says "Lucas -> Fernando". The second finding the original correction surfaced
  still stands FOR LUCAS ONLY: `agents/lucas/graph.py` is not invoked (a turn executed) by ANY
  production code path at all — not through a static import (0 hits outside its own package
  directory) and not through the dynamic `Harness.create_graph`/`_load_agent_graph` path either,
  which is used EXCLUSIVELY by `runtime/agent_runtime/service.py`'s health-only readiness daemon
  (`graph_loaded`'s own comment: "Construction only — no node ever runs from this check"). The
  platform DOES have a real, LIVE, turn-EXECUTING HTTP ingress pattern
  (`runtime/agent_runtime/ingress.py::build_ingress_router`, mounted behind
  `settings.agent_ingress_enabled` in `service.py`) — but it exists ONLY for Rafael's
  `portal_tiss` channel today; there is still no HTTP ingress for Fernando or Lucas. THIS graph
  now has a live inbound path that is not HTTP: the worker->A2A dossier edge above.
- CLOSED 2026-09-05 (fleet audit FER-04/FER-05, WP-FERNANDO-INPUT-DESFECHO). This bullet used
  to disclose that `tipo_plano`/`canal` were consumed as OPAQUE strings with no allowlist of
  their own, relying on the DMNs' catch-all rows as the only safety net. That residual is gone:
  both fields now have closed frozensets (`_TIPO_PLANO_ALLOW`/`_CANAL_ALLOW`, single-sourced
  against the contract's own variable table by a fence) plus the same TWO-SIDED discipline
  `intencao`/`status_inadimplencia` already had — `receive` fail-closes on a DECLARED
  out-of-domain value (class token `invalid_tipo_plano`/`invalid_canal`, never the raw value),
  and every read site goes through the normalizer pair (`_tipo_plano`/`_canal`), so a planted
  value cannot reach the DMN inputs, the dossier, the message facts, or the engine's process
  variables even on the escalate route (which still runs `start_process` after the fail-close).
  RESIDUAL still disclosed, NOT closed here: `origem_solicitacao` has a documented closed domain
  in the same contract table and has NO allowlist — the fleet audit's FER-10 only asked for (and
  this WP only delivered) the removal of its fabricated `"agente_fernando"` literal default;
  a planted `origem_solicitacao` still reaches the engine variable verbatim.
- DELIVERY HONESTY (FER-03/FER-04): `notify`'s `desfecho` branches on the REAL send outcome
  (`Entrega` = `enviada`/`nao_enviada`/`canal_sem_entrega`), never on `status_inadimplencia`
  alone. Only `whatsapp` has a sender wired here (`_CANAL_COM_ENTREGA`) — `portal`/`telefone`/
  `a2a` are legal canais with NO delivery seam, so they yield `*_canal_sem_entrega`. The live
  A2A edge (`delegation.py::state_from_envelope` always sets `canal="a2a"`) is exactly where the
  old label lied in production.

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

from typing import Any, Final, Literal, Protocol, TypedDict, cast

import structlog
from langgraph.graph import END, START, StateGraph

from maezo.runtime.dependency_failures import EXTERNAL_DEPENDENCY_FAILURES, PROGRAMMING_ERRORS
from maezo.runtime.error_text import (
    dmn_unavailable_error,
    start_unavailable_error,
)
from maezo.runtime.guards import require_iso8601_duration
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
from maezo.tools.workers.base import INADIMPLENCIA_KEY_FAMILY, mint_contract_business_key
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

logger = structlog.get_logger(__name__)

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

# Dominio FECHADO de `tipo_plano` (FER-05, auditoria de frota 2026-09-04). A FONTE do conjunto e'
# o contrato do processo (`docs/processes/contracts/SP-OP-INADIMPLENCIA-001.md`, tabela
# `## Variaveis de entrada`), NUNCA esta linha: `tests/unit/agents/
# test_fernando_input_allowlist_fence.py::test_allowlist_matches_the_contract_declared_domain`
# prende as duas pontas, de modo que ampliar o dominio aqui sem tocar o contrato fica VERMELHO
# (disciplina C3 — quem decide o dominio e' a especificacao, nao o Python). Mesma forma de
# `_VALID_INTENCOES`/`_STATUS_ALLOW`, aplicada ao campo que a varredura do R1 ciclo-1 nao alcancou.
_TIPO_PLANO_ALLOW: frozenset[str] = frozenset(
    {"individual", "familiar", "coletivo_empresarial", "coletivo_adesao"}
)

# Dominio FECHADO de `canal` (FER-04). `a2a` faz parte do dominio porque `agents/fernando/
# delegation.py::state_from_envelope` o semeia SEMPRE na aresta A2A VIVA (`tools/workers/
# inadimplencia.py::make_prepare_dossier_handler` -> `delegate_arrears_followup`) — uma allowlist
# que o rejeitasse quebraria uma jornada de producao, entao ele e' DECLARADO (no contrato tambem),
# nao tolerado por omissao.
_CANAL_ALLOW: frozenset[str] = frozenset({"whatsapp", "portal", "telefone", "a2a"})

#: Default do contrato para a AUSENCIA de `canal` — jamais para um valor invalido (esse fica
#: vazio, ver `_canal`): um canal recusado nunca vira "whatsapp" por conveniencia.
_CANAL_DEFAULT: Final[str] = "whatsapp"

#: Os canais deste grafo que tem remetente REALMENTE ligado. `portal`/`telefone`/`a2a` estao no
#: dominio mas nao tem seam de entrega nenhuma aqui — e' exatamente essa diferenca que FER-04
#: cobrava: pular o envio calado e ainda assim rotular o turno como "enviado".
_CANAL_COM_ENTREGA: frozenset[str] = frozenset({"whatsapp"})

#: Resultado REAL do envio no caminho `notify` (FER-03/FER-04). Nao e' um enum de negocio: e' a
#: descricao do que aconteceu com o efeito externo, e e' ELE que escolhe o desfecho abaixo.
Entrega = Literal["enviada", "nao_enviada", "canal_sem_entrega"]
_ENTREGA_ESTADOS: Final[tuple[str, ...]] = ("enviada", "nao_enviada", "canal_sem_entrega")

# Desfechos do caminho `notify`, por (jornada, resultado REAL do envio). Ate a auditoria de frota
# o desfecho saia so' de `status_inadimplencia` e o turno AFIRMAVA um envio que nao aconteceu
# (FER-03) — inclusive na aresta A2A, onde `canal="a2a"` nunca teve remetente (FER-04). Literais
# explicitos (nao f-strings) de proposito: o vocabulario fechado de telemetria
# (`runtime/turn_telemetry.py::_DESFECHO_VOCAB`) e' reproduzido por grep dos literais, e uma
# cerca (`test_notify_desfecho_tables_are_declared_in_the_closed_vocabulary`) exige que cada um
# esteja la — um rotulo nao declarado viraria `"outro"` no Prometheus, um KPI cego.
_DESFECHO_NOTIFICACAO_PREVIA: Final[dict[str, str]] = {
    "enviada": "notificacao_previa_enviada",
    "nao_enviada": "notificacao_previa_nao_enviada",
    "canal_sem_entrega": "notificacao_previa_canal_sem_entrega",
}
_DESFECHO_LEMBRETE: Final[dict[str, str]] = {
    "enviada": "lembrete_regularizacao_enviado",
    "nao_enviada": "lembrete_regularizacao_nao_enviado",
    "canal_sem_entrega": "lembrete_regularizacao_canal_sem_entrega",
}

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
    canal: str  # dominio FECHADO — `_CANAL_ALLOW` (so' `whatsapp` tem remetente ligado)
    numero_contrato: str  # business key material (same identity CANCEL-001 uses)
    matricula_beneficiario: str  # pseudonymized enrollment key (fallback business key material)
    beneficiario_pseudo_id: str
    to_hash: str  # WhatsApp phone HASH (never the raw number) — message destination
    tipo_plano: str  # dominio FECHADO — `_TIPO_PLANO_ALLOW` (fonte: contrato)
    origem_solicitacao: str  # cobranca | operadora | agente_fernando | juridico (SEM allowlist —
    #: residual divulgado no docstring do modulo; ausencia viaja vazia, nunca um literal fabricado)
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
    #: CC-01: o start foi TENTADO e FALHOU tecnicamente (`except CibSevenError` de
    #: `start_process`). NAO e a mesma coisa que `process_started is False`, que tambem cobre
    #: no-ops legitimos; e este marcador — e so ele — que a aresta condicional le.
    start_failed: bool
    process_ref: dict[str, Any]

    # Turn output.
    desfecho: str


# --- Helpers ---------------------------------------------------------------------------


def _business_key(state: FernandoState) -> str:
    """Idempotent business key per contract: `INAD-{tenant_id}-{numero_contrato}` — falls back
    to `matricula_beneficiario` when there is no contract number (individual/familiar plans),
    matching the BPMN's own documented variant (`spec/processes/bpmn/
    SP-OP-INADIMPLENCIA-001_Suspensao_Rescisao.bpmn`'s `<bpmn:documentation>`, "BUSINESS KEY").

    That fallback is the SECOND of the two sites DL-0043 leg (c) records: `matricula_beneficiario`
    is a `PHI_PROCESS_VARS` name (`tools/workers/phi_vars.py`) minted raw into a durable business
    key. Minting now goes through the shared `base.mint_contract_business_key`, which is
    byte-identical under the shipped (`off`) privacy policy, records the content-free DL-0043
    shadow counter, and switches to `beneficiario_pseudo_id` (the `PROG-...` precedent this repo
    already follows in `valentina/graph.py`) only under a RATIFIED `modo: pseudo_keys`.
    """
    return mint_contract_business_key(
        INADIMPLENCIA_KEY_FAMILY,
        state.get("tenant_id", ""),
        numero_contrato=state.get("numero_contrato") or "",
        matricula_beneficiario=state.get("matricula_beneficiario", ""),
        beneficiario_pseudo_id=state.get("beneficiario_pseudo_id", ""),
    )


def _tipo_plano(state: FernandoState) -> str:
    """Revalidacao do LADO DA LEITURA de `tipo_plano` (FER-05) — o unico ponto do modulo
    autorizado a ler o campo cru (cerca de AST em `test_fernando_input_allowlist_fence.py`).

    Fora do dominio fechado -> string vazia, NUNCA o valor cru: `receive` ja escala para humano
    quando o chamador manda um valor invalido, mas a rota `escalate` segue para `start_process`,
    e o LangGraph mescla o estado inicial do chamador verbatim — sem esta revalidacao o valor
    plantado continuaria chegando as entradas da DMN, ao dossie e as variaveis de processo do
    engine (zona geral, ADR-0006). Mesma disciplina que `_STATUS_ALLOW` ja aplicava a
    `status_inadimplencia` em `_build_dossier`/`_build_message`.

    Ausencia tambem devolve vazio: a DMN `inadimplencia_status` tem `r_catchall` conservador
    (-> `ANALISE_HUMANA`, nunca adverso), entao a ausencia converge para humano em vez de
    inventar um plano.
    """
    valor = str(state.get("tipo_plano") or "")
    return valor if valor in _TIPO_PLANO_ALLOW else ""


def _tipo_plano_recusado(state: FernandoState) -> bool:
    """Segunda metade do PAR normalizador de `tipo_plano` (e o unico outro ponto autorizado a ler
    o campo cru): `True` somente quando o chamador DECLAROU um valor e ele esta fora do dominio.

    A ausencia NAO e' recusa — `receive` fail-closa em cima de uma DECLARACAO invalida, nunca do
    silencio: recusar o silencio quebraria a aresta A2A viva (o `payload_meta` de
    `delegation.py::state_from_envelope` so' copia chaves nao-vazias) e a DMN ja tem catch-all
    conservador para a ausencia.
    """
    return bool(str(state.get("tipo_plano") or "")) and not _tipo_plano(state)


def _canal(state: FernandoState) -> str:
    """Revalidacao do LADO DA LEITURA de `canal` (FER-04) — unico ponto autorizado a ler o campo
    cru (mesma cerca de AST).

    Tres casos, deliberadamente distintos:
      - AUSENTE/vazio -> `_CANAL_DEFAULT`, o default que o contrato declara para esta variavel;
      - dentro do dominio -> o proprio valor;
      - fora do dominio -> string vazia (o valor e' RECUSADO, nao substituido pelo default): um
        canal invalido nunca pode virar `whatsapp` e disparar um envio que o chamador nao pediu,
        nem ser ecoado para as variaveis de processo.
    Como `""` nao esta em `_CANAL_COM_ENTREGA`, o caminho recusado tambem produz o desfecho
    honesto `*_canal_sem_entrega` em `notify`, jamais um `*_enviad*`.
    """
    valor = str(state.get("canal") or "")
    if not valor:
        return _CANAL_DEFAULT
    return valor if valor in _CANAL_ALLOW else ""


def _canal_recusado(state: FernandoState) -> bool:
    """Segunda metade do PAR normalizador de `canal` — mesma regra de `_tipo_plano_recusado`:
    so' uma DECLARACAO fora do dominio e' recusada; a ausencia cai no default do contrato."""
    valor = str(state.get("canal") or "")
    return bool(valor) and valor not in _CANAL_ALLOW


def _iso_duration_validated(state: FernandoState, field: str) -> str | None:
    """FER-08 (fleet audit ciclo 2): read-side format guard for a DMN-sourced ISO-8601 DURATION
    fact (`sla_analise_iso`/`prazo_purga_iso`/`prazo_notificacao_previa_iso` — module docstring's
    FER-08 section). Delegates the shape check to `maezo.runtime.guards.require_iso8601_duration`
    (format-only, C3: no threshold/business rule); a rejection is logged as a structured warning
    naming only the FIELD, never the raw value (untrusted transport content, even though the DMN
    never sources it from caller input) — a data-quality signal, never a silent blank that could
    be confused with "the DMN simply did not evaluate this row"."""
    value = state.get(field)
    validated = require_iso8601_duration(value, field=field)
    if validated is None and value is not None and value != "":
        # Only a NON-ausente rejection is worth a log line — `None`/`""` is the ordinary "this
        # DMN branch never ran" case already covered by `_output_field_resets`. A falsy value
        # of the WRONG TYPE (`0`/`False`/`[]`/`{}`) is NOT that case — it is data-quality
        # corruption (§Delta NONE-GUARDRAIL F4) and must log exactly like a malformed string
        # does, never fall through silently just because `bool(value)` happens to be `False`.
        logger.warning("fernando_iso_duration_invalido", field=field)
    return validated


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
        "start_failed": False,
        "process_ref": {},
        # Turn output.
        "desfecho": "",
    }


#: GAP 11.7 — the ONLY fields a caller (or an A2A delegation seam, `agents/fernando/
#: delegation.py::state_from_envelope`) may legitimately seed on the initial state. Everything
#: else in `FernandoState` is OUTPUT-ONLY (`_output_field_resets()` above). Mirrors `carolina/
#: graph.py::_CALLER_INPUT_FIELDS` exactly — Fernando already resets every output field on
#: `receive` (defense at the WRITE side); this is the matching READ-side/construction-time
#: allowlist a delegation handler needs so a malformed/malicious envelope cannot seed an
#: output-only key before `receive` even runs. Single-sourced against `FernandoState` by
#: `test_output_field_partition_is_complete` (`tests/unit/agents/test_fernando.py`) — a new
#: state field must be classified into exactly one of the two sets or that test fails.
_CALLER_INPUT_FIELDS: frozenset[str] = frozenset(
    {
        "tenant_id",
        "intencao",
        "canal",
        "numero_contrato",
        "matricula_beneficiario",
        "beneficiario_pseudo_id",
        "to_hash",
        "tipo_plano",
        "origem_solicitacao",
        "data_solicitacao_iso",
        "competencias_em_aberto",
        "meses_inadimplencia",
        "valor_total_devido_cents",
        "dentro_periodo_minimo",
        "notificacao_previa_feita",
        "dentro_janela_purga",
        "ja_em_rescisao_cancel",
        "documentos_refs",
    }
)


# --- Graph ------------------------------------------------------------------------------------


#: Fatos BOOLEANOS deste fluxo, com o nome que o humano de destino reconhece (CC-11).
#:
#: Incidente de 24/08/2026 (contado por inteiro em `agents/rafael/graph.py::_FATOS_BOOLEANOS`):
#: fatos passados ao modelo como repr de dicionario deixam `False` e `None` com a mesma cara de
#: "vazio", e um fato APURADO-e-desfavoravel vira "nao ha registro". O conserto ficou num agente
#: so' ate' a auditoria da frota; este mapa e' a adocao aqui. Chave -> rotulo; a ORDEM e' a ordem
#: das linhas no prompt. So' entram fatos declarados `bool` no state — nada que seja enum/str.
_FATOS_BOOLEANOS_MENSAGEM: Final[dict[str, str]] = {
    "dentro_janela_purga": "dentro da janela de purga",
}

#: O dossie (J3) carrega os quatro booleanos do state — a mensagem so' o da purga.
_FATOS_BOOLEANOS_DOSSIE: Final[dict[str, str]] = {
    "dentro_periodo_minimo": "dentro do periodo minimo de inadimplencia",
    "notificacao_previa_feita": "notificacao previa ao beneficiario feita",
    "dentro_janela_purga": "dentro da janela de purga",
    "ja_em_rescisao_cancel": "contrato ja em rescisao/cancelamento",
}


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
        # FER-05/FER-04: os outros dois campos de DOMINIO FECHADO do chamador. Um valor presente
        # e fora do dominio e' recusado aqui (fail-closed -> humano), com TOKEN DE CLASSE — nunca
        # o valor cru, que pode carregar PHI. AUSENCIA nao e' recusada: o contrato tem default
        # para `canal` e a DMN tem catch-all conservador para `tipo_plano`, e recusar a ausencia
        # quebraria a aresta A2A (o `payload_meta` so' copia chaves nao-vazias).
        if _tipo_plano_recusado(state):
            return self._escalate_min("ambiguidade", "invalid_tipo_plano")
        if _canal_recusado(state):
            return self._escalate_min("ambiguidade", "invalid_canal")
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
                "tipo_plano": _tipo_plano(state),
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
        purga_result = await self._evaluate_dmn(DMN_PURGA, {"tipo_plano": _tipo_plano(state)})
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
        to_hash = state.get("to_hash") or state.get("beneficiario_pseudo_id")
        entrega: Entrega
        if _canal(state) not in _CANAL_COM_ENTREGA:
            # FER-04: canal DENTRO do dominio (ou recusado por `_canal`) mas sem remetente
            # ligado neste grafo — `portal`/`telefone`/`a2a`. Nada e' enviado; o turno diz isso.
            entrega = "canal_sem_entrega"
        elif not to_hash:
            entrega = "nao_enviada"
        else:
            try:
                await self._whatsapp.send(to_hash, str(mensagem.get("texto", "")))
                entrega = "enviada"
            except PROGRAMMING_ERRORS:
                raise
            except Exception as exc:
                entrega = "nao_enviada"
                mensagem["envio_nota"] = f"envio WhatsApp indisponivel: {type(exc).__name__}"
        mensagem["entrega"] = entrega
        enviada = entrega == "enviada"

        # FER-03: o desfecho ramifica no resultado REAL do envio, nao so' no `status_
        # inadimplencia`. Ate a auditoria de frota este calculo ignorava o booleano que a propria
        # funcao acabara de computar, e o turno AFIRMAVA um envio que nao aconteceu — a
        # telemetria CC-09 ja emitia `enviada=False` ao lado do rotulo "...enviado", tornando a
        # contradicao visivel na MESMA linha de log sem corrigi-la.
        tabela = (
            _DESFECHO_NOTIFICACAO_PREVIA
            if state.get("status_inadimplencia") == "PENDENTE_NOTIFICACAO"
            else _DESFECHO_LEMBRETE
        )
        desfecho = tabela[entrega]
        # CC-09: `notify` is a TERMINAL node (its only outgoing edge is END, no `start_process`
        # on this branch) — `desfecho`/`mensagem_enviada` are still LOCAL at this point, so both
        # are passed as explicit overrides rather than read back from `state`.
        emit_turn_desfecho(state, agent_id="fernando", desfecho=desfecho, enviada=enviada)
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
            # CC-01: `start_failed_state` devolve as MESMAS tres chaves de antes mais o marcador
            # `start_failed`, que e o que `route_after_start` le para desviar a
            # `notify_start_failure` em vez de seguir calado para o terminal.
            return start_failed_state(business_key=business_key, error=start_unavailable_error(exc))
        # CC-09: on the `escalate` branch, `start_process`'s OWN success return is the last node
        # body that runs before the graph's `continue` edge lands directly on END — Fernando has
        # no separate `finalize`/`complete` node on this path (see `compile_graph`). `state` here
        # already carries `escalate`'s merged `desfecho`/`route`/`motivo_categoria`, so no
        # override is needed.
        emit_turn_desfecho(state, agent_id="fernando")
        return {
            "process_started": True,
            "business_key": business_key,
            "process_ref": {
                "instance_id": instance.instance_id,
                "state": instance.state,
                "already_existed": instance.already_existed,
            },
        }

    async def notify_start_failure(self, state: FernandoState) -> dict[str, Any]:
        """CC-01: o start FALHOU — grava o desfecho de erro e ALERTA, em vez de seguir calado.

        Ate CC-01 a aresta que saia de `start_process` era INCONDICIONAL: o turno chegava ao
        terminal com o `desfecho` de SUCESSO que um no a montante ja havia gravado, afirmando um
        fato que nao aconteceu, e sem prazo nenhum — o timer de SLA vive na instancia BPMN que
        nunca nasceu. O corpo deste no e o helper compartilhado
        (`maezo.runtime.start_outcome.notify_start_failure`): uma definicao para os 9 agentes,
        nunca 9 copias.
        """
        return emit_start_failure_notice(dict(state), agent_id="fernando", process_key=PROCESS_KEY)

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
            return {"error": dmn_unavailable_error(table, exc)}
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
        sla_result = await self._evaluate_dmn(DMN_SLA, {"tipo_plano": _tipo_plano(state)})
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
            "tipo_plano": _tipo_plano(state) or None,
            "competencias_em_aberto": state.get("competencias_em_aberto", []),
            "status_inadimplencia": status_validated,
            "dentro_janela_purga": state.get("dentro_janela_purga"),
            # FER-08: read-side ISO-8601-duration format guard (docstring's FER-08 section) —
            # a malformed DMN response is blanked, never echoed as a fact that looks real.
            "prazo_purga_iso": _iso_duration_validated(state, "prazo_purga_iso"),
            "prazo_notificacao_previa_iso": _iso_duration_validated(state, "prazo_notificacao_previa_iso"),
            "fonte_regulatoria_purga": state.get("fonte_regulatoria_purga"),
        }
        intencao = state.get("intencao")
        intencao_validated = intencao if intencao in _VALID_INTENCOES else None
        prompt = (
            f"{message_prompt()}\n\nintencao={intencao_validated}\n"
            f"{render_fatos_para_prompt(facts, booleanos=_FATOS_BOOLEANOS_MENSAGEM)}"
        )
        try:
            texto = await self._llm.generate(
                prompt,
                phi=True,
                agent_id="fernando",
                tenant_id=state.get("tenant_id", ""),
                # ADR-0009 §2 / CC-12: frasear fatos que a DMN ja decidiu -> task_default.
                task_kind="task_default",
            )
        except PROGRAMMING_ERRORS:
            raise
        except EXTERNAL_DEPENDENCY_FAILURES:  # fail-safe default: never leave the beneficiary with nothing.
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
            "tipo_plano": _tipo_plano(state) or None,
            "competencias_em_aberto": state.get("competencias_em_aberto", []),
            "meses_inadimplencia": state.get("meses_inadimplencia"),
            "valor_total_devido_cents": state.get("valor_total_devido_cents"),
            "dentro_periodo_minimo": state.get("dentro_periodo_minimo"),
            "notificacao_previa_feita": state.get("notificacao_previa_feita"),
            "dentro_janela_purga": state.get("dentro_janela_purga"),
            "ja_em_rescisao_cancel": state.get("ja_em_rescisao_cancel"),
            "motivo_humano": state.get("motivo_humano"),
            "status_inadimplencia": status_validated,
            # FER-08: same read-side ISO-8601-duration format guard as `_build_message`.
            "sla_analise_iso": _iso_duration_validated(state, "sla_analise_iso"),
            "fonte_regulatoria_sla": state.get("fonte_regulatoria_sla"),
            "dmn_refs": state.get("dmn_refs", {}),
        }
        prompt = (
            f"{dossier_prompt()}\n\nmotivo={state.get('motivo_humano')}\n"
            f"{render_fatos_para_prompt(facts, booleanos=_FATOS_BOOLEANOS_DOSSIE)}"
        )
        try:
            narrativa = await self._llm.generate(
                prompt,
                phi=True,
                agent_id="fernando",
                tenant_id=state.get("tenant_id", ""),
                # ADR-0009 §2 / CC-12: dossie lido pelo humano antes de decidir -> reasoning.
                task_kind="reasoning",
            )
        except PROGRAMMING_ERRORS:
            raise
        except EXTERNAL_DEPENDENCY_FAILURES:  # LLM failure never blocks the human escalation.
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
            "tipo_plano": _tipo_plano(state),
            # FER-10: sem literal fabricado. O silencio do chamador e uma declaracao explicita de
            # `agente_fernando` eram indistinguiveis nesta variavel; a ausencia agora viaja como
            # string vazia (o tipo declarado no contrato), que e' o fato verdadeiro.
            "origem_solicitacao": str(state.get("origem_solicitacao") or ""),
            "canal": _canal(state),
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
        g.add_node("notify_start_failure", self.notify_start_failure)

        g.add_edge(START, "receive")
        g.add_edge("receive", "assess")
        g.add_conditional_edges("assess", self._route, {"notify": "notify", "escalate": "escalate"})
        g.add_edge("notify", END)
        g.add_edge("escalate", "start_process")
        # CC-01: a aresta que sai de `start_process` e CONDICIONAL. Uma falha tecnica de
        # start desvia para `notify_start_failure` (desfecho de erro + alerta); qualquer
        # outro caminho — incluindo os no-ops legitimos com `process_started=False` —
        # segue para o terminal de sempre. O predicado e compartilhado (uma definicao).
        g.add_conditional_edges(
            "start_process",
            route_after_start,
            {"notify_start_failure": "notify_start_failure", "continue": END},
        )
        g.add_edge("notify_start_failure", END)
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

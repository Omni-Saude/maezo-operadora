"""Marina as A2A delegation TARGET (`glosa.analyze` / `recurso.analyze` / `reembolso.analyze`).

`spec/agents/marina/agent.yaml` has declared `accepted_task_types: [glosa.analyze,
recurso.analyze, reembolso.analyze]` since Phase 2, and the three contracts name the ORIGIN
service tasks explicitly — `docs/processes/contracts/SP-OP-CONTAS-001.md`
(`operadora.contas.prepare_triage_dossier`, "consome (worker->A2A) ... delegacao `glosa.analyze`"),
`SP-OP-RECURSO-001.md` (`operadora.recurso.analyze_request`, "convoca Marina ... A2A
`recurso.analyze`") and `SP-OP-REEMBOLSO-001.md` (`operadora.reembolso.analyze_request`,
"convoca o agente analista (Marina-classe)"). All THREE sides were missing (CC-02/RAF-11); this
module builds ONE of them.

WHAT THIS MODULE IS, AND WHAT IT IS NOT (read this before believing the seam is live):

- TARGET side (`state_from_envelope` + `make_marina_handler`) — BUILT HERE. The handler compiles
  Marina's REAL graph (`agents.marina.graph.build(config)`, fail-closed on missing `inference`/
  `dmn`/`cibseven`/`audit_sink`) and runs it against envelope-materialized state. Structural twin
  of `agents/carolina/delegation.py` (pure TARGET, non-PHI `payload_meta` allowlist) with
  `agents/andre/delegation.py`'s multi-flow disambiguation on top.
- ORIGIN builders (`build_marina_analysis_envelope` / `delegate_marina_analysis`) — BUILT HERE as
  the function a future caller would use, and unit-proven in isolation. **NOT CALLED from
  `tools/workers/{contas,recurso,reembolso}.py`**: that call site is an OWNER-DECISION
  (gap `FERNANDO-DELEGATION-CALL-SITE`) and `tools/workers/` is outside this work package's
  editable surface. The three workers still assemble their dossier locally today.
- REGISTRATION in the composition root — **NOT DONE**. `runtime.agent_runtime.a2a_composition`
  still registers only `{"rafael"}` / `{"carolina", "andre"}`; adding `"marina"` inherits the same
  owner decision. Until it does, no dispatcher can route a `glosa.analyze` envelope here, and
  `tests/unit/a2a/test_agent_card_handlers_parity.py` records that gap as an executable fact.

Idempotency (Guard 4): `task_id` IS the flow's business key, delegated to
`marina.graph._business_key` itself (never reimplemented here, so the two derivations cannot
drift) — `CONTAS-{tenant}-{lote}` (or `CONTAS-{tenant}-{guia}-{conta}`),
`RECURSO-{tenant}-{guia}-{glosa}`, `REEMB-{tenant}-{protocolo}`. An engine re-delivery of the
external task replays the SAME delegation result without re-running Marina, and her own
`start_process` is business-key idempotent anyway.

REEMBOLSO IS DIFFERENT, AND THIS MODULE DOES NOT PRETEND OTHERWISE (CC-13 re-confirmed it):
`marina.graph`'s `reembolso` flow evaluates NO DMN and its `start_process` is a **no-op** — the
SP-OP-REEMBOLSO-001 instance is ALREADY RUNNING when `reembolso.analyze` arrives
(`ST_PrepararDossie`, after the BPMN's own `BRT_Admissibilidade`/`BRT_Calculo`/
`BRT_AutoApproval`). A `reembolso.analyze` delegation therefore ALWAYS comes back with
`process_started="False"` and `route="human_review"`, and its `output_ref`
(`process://REEMB-...`) is an ANCHOR to the running instance, not proof that anything started.
The handler reports what the graph did; it never promises a start the graph will not perform.

INPUT-BOUNDARY GATE: Marina's graph HAS a strict constructor since CC-14
(`graph.new_marina_state`), so — unlike Carolina/Fernando, whose graphs only expose
`_CALLER_INPUT_FIELDS` — `state_from_envelope` routes the raw mapping through it, exactly the way
`rafael/delegation.py` routes through `new_rafael_state`. Two layers, both fail-closed: `raw` is
built by ALLOWLIST-BY-CONSTRUCTION (only named keys off the per-flow `_..._META_KEYS` tuples are
ever copied, so an out-of-allowlist `payload_meta` entry is never read at all), and
`new_marina_state` then RAISES on any key that is not a `_CALLER_INPUT_FIELDS` member — the
structural regression guard against a future edit that adds a meta key without classifying it.
Her `receive` node re-sanitizes every output-only field on top of that (layer 2).

PHI / ADR-0006 — WHAT NEVER RIDES THIS SEAM. `payload_meta` is a strict non-PHI allowlist of
bounded enums, TISS/cadastral identifiers, ISO dates, worker-pre-resolved booleans and integer
centavos. Deliberately EXCLUDED:
  * `cid10` — a diagnosis code IS a clinical fact. `agents/rafael/delegation.py` does forward it
    on the Helena->Rafael edge; this module deliberately DIVERGES (the AUTH edge's precedent is
    not a licence for the CONTAS/RECURSO edge). Marina's `_contract_variables` seeds `cid10`
    CONDITIONALLY (`if state.get("cid10")`), so the recurso dossier degrades gracefully without
    it, and the clinical fact stays in the engine's own variables.
  * every free-text field and every LLM-authored narrative — nothing Marina writes, and nothing a
    caller could write, travels as prose over this seam.
  * the list-shaped facts `linhas_conta_refs` / `documentos_recurso_refs` — `payload_meta` is a
    strict `Mapping[str, str]`; mirrors Carolina's own documented exclusion of her
    `documentos_refs`.
  * `reason_codes_tiss` (list-shaped) rides in a REDUCED, disclosed form: the single
    `reason_code_tiss` meta key carries the PRINCIPAL code, and `state_from_envelope`
    reconstructs `reason_codes_tiss=[code]`. `_assess_contas` reads ONLY `reason_codes_tiss[0]`
    ("first code in the list — defensive"), so the DMN chain sees exactly what it would have
    seen; what a delegated CONTAS start seeds into the ENGINE, however, is a ONE-element list,
    and that fidelity loss is a REAL residual of a `Mapping[str, str]` payload — recorded here,
    not hidden. Whoever wires the (owner-gated) origin call site must either widen the payload
    channel or have the worker seed the full list itself.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, cast

from maezo.a2a import Budget, DelegationEnvelope, HandlerOutput
from maezo.a2a.dispatcher import origin_signer_of
from maezo.platform.observability import record_agent_error
from maezo.runtime.metrics import classify_agent_error_type
from maezo.runtime.start_outcome import StartProcessFailedError

from .graph import (
    Flow,
    MarinaState,
    _business_key,
    _process_key,
    build,
    new_marina_state,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from maezo.a2a import DelegationDispatcher, DelegationResult, EnvelopeSigner
    from maezo.runtime.inference import InferenceProvider
    from maezo.tools.mcp_cibseven.transport import AuditStartSink, CibSevenTransport
    from maezo.tools.workers.dmn_transport import DmnTransport

    from .graph import PatientSummaryReader

# Task types delegated to Marina — MUST match `spec/agents/marina/agent.yaml`'s
# `a2a.accepted_task_types` exactly (fleet-wide parity fence:
# `tests/unit/a2a/test_agent_card_handlers_parity.py`).
TASK_TYPE_GLOSA_ANALYSIS = "glosa.analyze"
TASK_TYPE_RECURSO_ANALYSIS = "recurso.analyze"
TASK_TYPE_REEMBOLSO_ANALYSIS = "reembolso.analyze"
TASK_TYPES: frozenset[str] = frozenset(
    {TASK_TYPE_GLOSA_ANALYSIS, TASK_TYPE_RECURSO_ANALYSIS, TASK_TYPE_REEMBOLSO_ANALYSIS}
)

# Each delegation ORIGINATES in the worker runtime, from INSIDE the flow's own process (the
# contracts' "consome (worker->A2A)" rows), never in an agent graph — the dispatcher validates
# only the TARGET's Card, so the origin id is a stable, self-describing worker identity (audited
# as `agent_id` on the delegation chain link). Mirrors `carolina/delegation.py::ORIGIN_WORKER`.
ORIGIN_CONTAS_WORKER = "contas-worker"
ORIGIN_RECURSO_WORKER = "recurso-worker"
ORIGIN_REEMBOLSO_WORKER = "reembolso-worker"
TARGET_AGENT = "marina"

#: task_type -> Marina's graph `flow`. Unlike Andre (whose two flows SHARE one task_type and are
#: therefore disambiguated by ORIGIN), Marina's three task types are DISJOINT, so the task type
#: itself is the flow selector — the origin is a cross-check, never the decision.
_TASK_TYPE_TO_FLOW: dict[str, Flow] = {
    TASK_TYPE_GLOSA_ANALYSIS: "contas",
    TASK_TYPE_RECURSO_ANALYSIS: "recurso",
    TASK_TYPE_REEMBOLSO_ANALYSIS: "reembolso",
}
#: task_type -> the contract-declared origin worker (used by the ORIGIN builder only).
_TASK_TYPE_TO_ORIGIN: dict[str, str] = {
    TASK_TYPE_GLOSA_ANALYSIS: ORIGIN_CONTAS_WORKER,
    TASK_TYPE_RECURSO_ANALYSIS: ORIGIN_RECURSO_WORKER,
    TASK_TYPE_REEMBOLSO_ANALYSIS: ORIGIN_REEMBOLSO_WORKER,
}

# Default budget for a dossier delegation chain (per the Helena->Rafael exemplar).
_DEFAULT_BUDGET = Budget(tokens=64, time_ms=60_000, cost_per_hop=1)

# Wall-clock in-flight horizon stamped as `deadline` on a SIGNED envelope (ADR-0039 §4.3.1) —
# same 6h rationale as `agents/helena/delegation.py::_SIGNED_ENVELOPE_TTL`.
_SIGNED_ENVELOPE_TTL = timedelta(hours=6)

# --- Non-PHI `payload_meta` allowlists, per flow (module docstring §PHI) ------------------------

#: Identifiers every flow may carry (all pseudonymized/cadastral — never a raw CPF/name/CNS).
_COMMON_STRING_META_KEYS = ("beneficiario_pseudo_id", "prestador_id", "patient_summary_ref")

_CONTAS_STRING_META_KEYS = (
    "numero_lote_tiss",
    "numero_guia_tiss",
    "numero_conta",
    "competencia",
    "data_recebimento_lote",
    "tipo_lote",
)
_CONTAS_FLOAT_META_KEYS = ("valor_apresentado_brl", "denial_ratio")
_CONTAS_BOOLEAN_META_KEYS = (
    "divergencia_valor",
    "item_conforme_tabela",
    "documentacao_anexa",
    "indicio_fraude_sinalizado",
)

_RECURSO_STRING_META_KEYS = (
    "numero_guia_tiss",
    "numero_lote_tiss",
    "glosa_id",
    "glosa_type",
    "glosa_reason_code",
    "codigo_procedimento_tuss",
    "data_ciencia_alegada_prestador",
    "data_recebimento_recurso_iso",
)
_RECURSO_FLOAT_META_KEYS = ("valor_glosado_brl",)
_RECURSO_BOOLEAN_META_KEYS = ("glosa_existe", "dentro_prazo_recurso", "documentacao_recurso_completa")

_REEMBOLSO_STRING_META_KEYS = ("protocolo_reembolso", "tipo_reembolso", "categoria_procedimento")
_REEMBOLSO_INT_META_KEYS = ("valor_solicitado_cents", "valor_calculado_tabela_cents")
_REEMBOLSO_BOOLEAN_META_KEYS = (
    "cobertura_prevista",
    "documentacao_completa",
    "dentro_prazo",
    "beneficiario_ativo",
    "carencia_cumprida",
    "dentro_tabela",
    "dentro_teto_l2",
    "requer_avaliacao_clinica",
)

#: The PRINCIPAL TISS reason code, carried as a scalar and re-expanded into the list-shaped
#: `reason_codes_tiss` state field (module docstring's disclosed fidelity residual).
_REASON_CODE_META_KEY = "reason_code_tiss"

_STRING_META_KEYS_BY_FLOW: dict[Flow, tuple[str, ...]] = {
    "contas": _COMMON_STRING_META_KEYS + _CONTAS_STRING_META_KEYS,
    "recurso": _COMMON_STRING_META_KEYS + _RECURSO_STRING_META_KEYS,
    "reembolso": _COMMON_STRING_META_KEYS + _REEMBOLSO_STRING_META_KEYS,
}
_FLOAT_META_KEYS_BY_FLOW: dict[Flow, tuple[str, ...]] = {
    "contas": _CONTAS_FLOAT_META_KEYS,
    "recurso": _RECURSO_FLOAT_META_KEYS,
    "reembolso": (),
}
_INT_META_KEYS_BY_FLOW: dict[Flow, tuple[str, ...]] = {
    "contas": (),
    "recurso": (),
    "reembolso": _REEMBOLSO_INT_META_KEYS,
}
_BOOLEAN_META_KEYS_BY_FLOW: dict[Flow, tuple[str, ...]] = {
    "contas": _CONTAS_BOOLEAN_META_KEYS,
    "recurso": _RECURSO_BOOLEAN_META_KEYS,
    "reembolso": _REEMBOLSO_BOOLEAN_META_KEYS,
}


def _flow_for_task_type(task_type: str) -> Flow:
    """`task_type` -> Marina's graph `flow`. Fail-closed on anything else.

    The dispatcher already refuses a task type outside her Card's `accepted_task_types`
    (`AgentCard.accepts`), so this raise is the SECOND fence, not the first: it exists so a
    direct/handler-level caller can never silently fall through to her graph's own missing-key
    default (`_flow` -> `contas`) and start the WRONG process.
    """
    flow = _TASK_TYPE_TO_FLOW.get(task_type)
    if flow is None:
        raise ValueError(
            f"task_type {task_type!r} is not one of Marina's accepted_task_types "
            f"({sorted(TASK_TYPES)}) — the flow (and therefore the process and the business key) "
            "cannot be derived; a delegation with an unknown task type is a producer bug"
        )
    return flow


def marina_task_id(tenant: str, flow: Flow, ids: dict[str, Any]) -> str:
    """Idempotent `task_id` == the flow's business key (dispatcher Guard 4).

    Delegates to `marina.graph._business_key` itself — never reimplements the per-flow derivation
    here, so the two can never drift. A plain dict with the identity fields satisfies
    `_business_key`'s `.get(...)` reads structurally.
    """
    stub = cast(MarinaState, {"tenant_id": tenant, "flow": flow, **ids})
    return _business_key(stub)


def _identity_ids(flow: Flow, case_meta: dict[str, Any]) -> dict[str, Any]:
    """The subset of `case_meta` that the flow's business key is derived from."""
    keys: tuple[str, ...]
    if flow == "recurso":
        keys = ("numero_guia_tiss", "glosa_id")
    elif flow == "reembolso":
        keys = ("protocolo_reembolso",)
    else:
        keys = ("numero_lote_tiss", "numero_guia_tiss", "numero_conta")
    return {key: str(case_meta.get(key, "") or "") for key in keys}


def _build_payload_meta(flow: Flow, case_meta: dict[str, Any]) -> dict[str, str]:
    """Serialize the case into `payload_meta` (`Mapping[str, str]`, strict non-PHI allowlist).

    Allowlist, NOT blocklist (mirrors `carolina/delegation.py::_build_payload_meta`): a key that
    is not named in this flow's tuples is silently NOT forwarded — never inspected, never copied.
    """
    meta: dict[str, str] = {}
    for key in _STRING_META_KEYS_BY_FLOW[flow]:
        if case_meta.get(key):
            meta[key] = str(case_meta[key])
    for key in _FLOAT_META_KEYS_BY_FLOW[flow]:
        if case_meta.get(key) is not None:
            meta[key] = str(float(case_meta[key]))
    for key in _INT_META_KEYS_BY_FLOW[flow]:
        if case_meta.get(key) is not None:
            meta[key] = str(int(case_meta[key]))
    for key in _BOOLEAN_META_KEYS_BY_FLOW[flow]:
        if key in case_meta:
            meta[key] = "true" if bool(case_meta[key]) else "false"
    if flow == "contas":
        codes = case_meta.get("reason_codes_tiss") or []
        principal = case_meta.get(_REASON_CODE_META_KEY) or (str(codes[0]) if codes else "")
        if principal:
            meta[_REASON_CODE_META_KEY] = str(principal)
    return meta


def build_marina_analysis_envelope(
    *,
    tenant: str,
    task_type: str,
    case_meta: dict[str, Any],
    budget: Budget | None = None,
    signer: EnvelopeSigner | None = None,
) -> DelegationEnvelope:
    """Build the worker->Marina root envelope for one of her three analyses.

    - `payload_ref` is the case's process reference (`process://CONTAS-...`/`RECURSO-...`/
      `REEMB-...`) — a non-PHI anchor (the envelope constructor additionally rejects any
      PHI-looking reference).
    - `case_meta`: the flow's process variables available at the origin service task. Serialized
      into `payload_meta` through the strict per-flow allowlist above.

    Applies the anti-loop guards at the root itself (origin != target, chain <= max_hops, budget).

    SIGNING (ADR-0039 §4.4, leg E3): with `signer` present the envelope carries an explicit
    `deadline` and is HMAC-signed over its v2 canonical digest. Absent -> unsigned (dev path).
    """
    flow = _flow_for_task_type(task_type)
    task_id = marina_task_id(tenant, flow, _identity_ids(flow, case_meta))
    envelope = DelegationEnvelope.root(
        task_id=task_id,
        task_type=task_type,
        origin=_TASK_TYPE_TO_ORIGIN[task_type],
        target=TARGET_AGENT,
        tenant=tenant,
        budget=budget or _DEFAULT_BUDGET,
        payload_ref=f"process://{task_id}",
        deadline=(datetime.now(tz=UTC) + _SIGNED_ENVELOPE_TTL) if signer is not None else None,
        payload_meta=_build_payload_meta(flow, case_meta),
    )
    return signer.sign(envelope) if signer is not None else envelope


async def delegate_marina_analysis(
    dispatcher: DelegationDispatcher,
    *,
    tenant: str,
    task_type: str,
    case_meta: dict[str, Any],
    budget: Budget | None = None,
    signer: EnvelopeSigner | None = None,
) -> DelegationResult:
    """Originate and dispatch a worker->Marina delegation. Idempotent by `task_id`.

    NOT CALLED from `tools/workers/{contas,recurso,reembolso}.py` — see the module docstring's
    STOP boundary (owner decision, gap `FERNANDO-DELEGATION-CALL-SITE`). Returns the dispatcher's
    structured `DelegationResult` (never a raise out of the dispatcher). On re-delivery of the
    same `task_id`, `idempotent_replay=True` and the handler does NOT run again.

    SIGNING (ADR-0039 §4.4): `signer` defaults to the edge signer carried by `dispatcher`
    (`origin_signer_of`), so a future live worker call site signs without per-worker wiring.
    """
    resolved_signer = signer if signer is not None else origin_signer_of(dispatcher)
    envelope = build_marina_analysis_envelope(
        tenant=tenant,
        task_type=task_type,
        case_meta=case_meta,
        budget=budget,
        signer=resolved_signer,
    )
    return await dispatcher.delegate(envelope)


# --- Target side (mirrors carolina/delegation.py + andre's flow disambiguation) -----------------


def _as_bool(value: Any) -> bool:
    """`payload_meta` is `Mapping[str, str]` (A2A) — normalize "true"/"false" strings to bool."""
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"true", "1", "yes", "sim"}


def _as_int(value: Any) -> int:
    """Parse a `payload_meta` decimal string back to `int` — fails closed to 0 rather than
    dropping the whole envelope over one malformed numeric field (the same conservative default
    `graph.py`'s own DMN inputs use at every call site)."""
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return 0


def _as_float(value: Any) -> float:
    """Same contract as `_as_int`, for the BRL-denominated facts Marina only REPORTS."""
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return 0.0


def _has_business_key_identity(flow: Flow, meta: dict[str, str]) -> bool:
    """The flow's minimum identity — the exact set of shapes `graph._business_key` can COMPOSE.

    STRICTER THAN `graph.receive`'s own `key_ok` ON `contas`, DELIBERATELY. `receive` accepts
    `lote OR guia`, but `_business_key` composes the guia form ONLY as
    `CONTAS-{tenant}-{guia}-{conta}` — a guia WITHOUT a conta falls through to the lote form with
    an EMPTY lote, so the key degenerates to `CONTAS-{tenant}-` and EVERY such case shares it.
    In the graph that degeneracy is merely a bad anchor on a turn a human still reviews. HERE it
    is a DISTRIBUTED IDEMPOTENCY KEY: `task_id == _business_key`, and the dispatcher's Guard 4 is
    DURABLE — the second guia-only case would be answered as an idempotent REPLAY of the first,
    handing back the first case's `output_ref` (a process that is not this case's) without ever
    running Marina. A seam that mints the key must therefore refuse every input the key
    derivation cannot tell apart, which is a strictly smaller set than what a graph invoked with
    an already-assembled state may fail-safe on.

    The two forms below are the ONLY two `_business_key` composes for `contas` (`graph.py`
    `_business_key`: `if guia and conta -> ...-{guia}-{conta}`, else `...-{lote}`); `recurso` and
    `reembolso` each compose exactly one form, and for those the graph's guard and this one
    already coincide.
    """
    if flow == "recurso":
        return bool(meta.get("numero_guia_tiss") and meta.get("glosa_id"))
    if flow == "reembolso":
        return bool(meta.get("protocolo_reembolso"))
    return bool(meta.get("numero_lote_tiss") or (meta.get("numero_guia_tiss") and meta.get("numero_conta")))


def state_from_envelope(envelope: DelegationEnvelope) -> MarinaState:
    """Materialize Marina's initial graph state from a delegation envelope.

    `flow` is set EXPLICITLY from the task type (`_flow_for_task_type`) — never omitted, so her
    graph's own missing-key default (`graph._flow` -> `contas`) can never be what decides which
    PROCESS a delegated turn starts. Case identifiers/facts come from `payload_meta` (never PHI);
    `tenant` comes from the envelope (ADR-0004); `canal` is ALWAYS `"a2a"` (never
    `payload_meta`-sourced).

    TWO fail-closed layers (module docstring §INPUT-BOUNDARY GATE): `raw` is built by
    allowlist-by-construction from the per-flow `_..._META_KEYS` tuples, and the result is then
    routed through `graph.new_marina_state`, which RAISES naming any key outside
    `_CALLER_INPUT_FIELDS`.

    Fails closed on a missing identity: Marina's business key anchors the dossier (and, for
    `contas`/`recurso`, the REAL process instance she starts). Her `receive` would fail-safe to
    human review with a context error, but a blank identity here is a producer bug and must be
    loud at the seam, not a human-review ticket downstream.
    """
    flow = _flow_for_task_type(envelope.task_type)
    meta = dict(envelope.payload_meta)
    if not _has_business_key_identity(flow, meta):
        raise ValueError(
            f"{envelope.task_type} envelope has no {flow} business-key identity in payload_meta — "
            "the SP-OP-CONTAS/RECURSO/REEMBOLSO-001 business key cannot be derived (producer bug; "
            "the originating worker validates identifiers before delegating)"
        )
    raw: dict[str, Any] = {"flow": flow, "tenant_id": envelope.tenant, "canal": "a2a"}
    for key in _STRING_META_KEYS_BY_FLOW[flow]:
        if meta.get(key):
            raw[key] = meta[key]
    for key in _FLOAT_META_KEYS_BY_FLOW[flow]:
        if key in meta:
            raw[key] = _as_float(meta[key])
    for key in _INT_META_KEYS_BY_FLOW[flow]:
        if key in meta:
            raw[key] = _as_int(meta[key])
    for key in _BOOLEAN_META_KEYS_BY_FLOW[flow]:
        if key in meta:
            raw[key] = _as_bool(meta[key])
    if flow == "contas" and meta.get(_REASON_CODE_META_KEY):
        # Disclosed reduction (module docstring): the PRINCIPAL code only — which is exactly and
        # only what `_assess_contas` reads (`reason_codes[0]`).
        raw["reason_codes_tiss"] = [meta[_REASON_CODE_META_KEY]]
    return new_marina_state(raw)


def make_marina_handler(
    inference: InferenceProvider,
    *,
    dmn: DmnTransport,
    cibseven: CibSevenTransport,
    audit_sink: AuditStartSink,
    fhir: PatientSummaryReader | None = None,
    agent_version: str = "marina@v0",
) -> Callable[[DelegationEnvelope], Awaitable[HandlerOutput]]:
    """Build Marina's A2A handler (delegation target).

    NOT REGISTERED with any dispatcher: `runtime.agent_runtime.a2a_composition` still wires only
    `handlers={"rafael": ...}` / `{"carolina": ..., "andre": ..., "fernando": ...}`, and adding
    `"marina"` is the OWNER DECISION this work package deliberately stops in front of (module
    docstring). This
    factory is the half that had to exist first.

    Compiles the REAL Marina graph via `marina.graph.build(config)` (the same fail-closed contract
    every other caller goes through — `inference`/`dmn`/`cibseven`/`audit_sink` REQUIRED, `fhir`
    optional) and invokes it with the envelope-materialized state.

    `output_ref` is the flow's business key reference (auditable, never PHI). `meta` carries ONLY
    bounded routing class tokens — the dossier CONTENT (`dossie_marina`, and every fact and
    narrative in it) stays in Marina's own engine variables and is never forwarded over this seam,
    mirroring Carolina's guardrail and Marina's own L0-hard invariant that she neither applies a
    glosa, nor denies a recurso, nor approves/denies/reduces a reembolso.
    """
    compiled = build(
        {
            "inference": inference,
            "dmn": dmn,
            "cibseven": cibseven,
            "audit_sink": audit_sink,
            "fhir": fhir,
            "agent_version": agent_version,
        }
    ).compile()

    async def handler(envelope: DelegationEnvelope) -> HandlerOutput:
        state = state_from_envelope(envelope)
        try:
            result: dict[str, Any] = await compiled.ainvoke(state)
        except Exception as exc:
            # ALERTS-WITHOUT-METRICS-a (mirrors carolina/delegation.py's handler exactly — see its
            # comment for the full rationale and the fence that pins this shape,
            # `tests/unit/platform/test_alert_metrics_fence.py::
            # test_every_graph_invocation_in_src_counts_agent_errors`).
            record_agent_error(agent="marina", error_type=classify_agent_error_type(exc))
            raise
        business_key = result.get("business_key") or _business_key(state)
        if result.get("start_failed") is True:
            # RAF-02: o grafo TENTOU abrir o processo e o engine recusou. Devolver
            # `HandlerOutput` aqui seria um sucesso para o dispatcher (`HandlerOutput` nao tem
            # campo `success`): ele gravaria o audit terminal `_DECISION_COMPLETED`, emitiria o
            # fato COMPLETED e SELARIA o resultado por `task_id` — tornando o falso sucesso
            # irretentavel. A excecao tipada propaga, entao nada disso acontece e a reentrega do
            # mesmo `task_id` reexecuta o handler. So tokens de classe na mensagem, nunca PHI.
            raise StartProcessFailedError(
                f"marina nao conseguiu iniciar {_process_key(state)} "
                f"(business_key={business_key!r}): o turno NAO foi concluido"
            )
        return HandlerOutput(
            output_ref=f"process://{business_key}",
            meta={
                "flow": str(result.get("flow") or state.get("flow", "")),
                "route": str(result.get("route") or "human_review"),
                "desfecho": str(result.get("desfecho", "")),
                "motivo_humano": str(result.get("motivo_humano") or ""),
                "grupo_destino": str(result.get("grupo_humano") or ""),
                # `reembolso` ALWAYS reports False here — her `start_process` is a no-op in that
                # flow (module docstring). This field reports; it never promises.
                "process_started": str(result.get("process_started", False)),
            },
        )

    return handler

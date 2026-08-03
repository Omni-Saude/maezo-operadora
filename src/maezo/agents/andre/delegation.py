"""Andre as A2A delegation TARGET + the ADEQUACAO-worker ORIGIN builder (`analytics.population`).

DL-0033 declared the `operadora.adequacao.prepare_remediation_dossier` worker a LOCAL STUB and
deferred the real Andre wiring to a future full-A2A task. This module is that wiring's Andre
half, mirroring the LIVE Helena->Rafael exemplar:

- ORIGIN side (mirrors `agents/helena/delegation.py`): `build_adequacao_dossier_envelope` /
  `delegate_adequacao_dossier`, called by the `operadora.adequacao.prepare_remediation_dossier`
  worker (`tools/workers/adequacao.py`) from INSIDE the running SP-OP-ADEQUACAO-001 instance
  (`ST_PrepareRemediationDossier`, ANALISE_HUMANA branch — also the `seguir_analise` re-entry).
  A SECOND origin, `build_pagto_dossier_envelope` / `delegate_pagto_dossier`, is called by the
  `operadora.pagto.prepare_approval_dossier` worker (`tools/workers/pagto.py`,
  `ST_PrepareApprovalDossier` — also the GAP-PAGTO-5 `seguir_analise` re-entry): it targets
  Andre's DEFAULT `pagto_dossier` flow via the SAME shared task_type, disambiguated by its
  `pagto-worker` origin (see the taxonomy note below).
- TARGET side (mirrors `agents/rafael/delegation.py`): `state_from_envelope` +
  `make_andre_handler` — compiles Andre's REAL graph (`agents.andre.graph.build(config)`,
  fail-closed) and runs it with envelope-materialized state.

TASK-TYPE TAXONOMY (orchestrator decision, this build): the contract names `analytics.population`
(`SP-OP-ADEQUACAO-001.md`), a type Andre's card ALREADY accepts — the PAGTO edge REUSES the SAME
type (no new task_type is minted and `spec/agents/andre/agent.yaml` is untouched for either edge).
The SHARED type is disambiguated by `envelope.origin` -> Andre's graph `flow` state key
(`_flow_for`): `adequacao-worker` -> `flow="adequacao_dossier"`; `pagto-worker` (and ANY other
origin) -> his default (`pagto_dossier`). The pagto edge is disambiguated by ORIGIN alone, riding
the default branch on purpose (`pagto-worker` is deliberately NOT mapped in `_ORIGIN_TO_FLOW`).
CRITICAL: a state with NO `flow` key silently defaults to `pagto_dossier` inside his graph
(`graph._flow`), which would treat an adequacao cell as a payment triage — this layer therefore
sets `flow` EXPLICITLY on every materialized state, never by omission.

Idempotency (Guard 4): the `task_id` IS the adequacao cell/business key
(`ADEQ-{tenant}-{regiao}-{especialidade}[-{ciclo}]` — `andre.graph._business_key`'s own format).
An engine re-delivery replays the SAME delegation result without re-running Andre. NOTE
(disclosed): the BPMN's `seguir_analise` re-entry re-reaches the dossier task for the SAME cell;
under this key it receives the idempotent REPLAY, not a fresh dossier — a genuinely new
evaluation cycle disambiguates via `ciclo_avaliacao` (a new task_id).

GUARDRAILS (L0/L1, `andre/graph.py` — re-enforced here): the adequacao flow NEVER starts a
process (his `start_process` no-ops; the ADEQ key only ANCHORS), route is ALWAYS `human_review`
(`gestao-rede`, `UT_DecisaoFallback` — "instrui, nao decide", DL-0037), and the dossier's
`decisao_pagamento`/`preco_recomendado`/`fhir_patient_id` are structurally `None`. This handler
forwards NO dossier body — `output_ref` is the anchor reference, `meta` bounded class tokens.

PHI (ADR-0006): `payload_meta` is a strict non-PHI allowlist of network-cell aggregates
(region/especialidade granularity, DMN-resolved gap tokens, numeric aggregates, booleans) —
Andre is the PHI-egress chokepoint and only ever receives/emits aggregates on this seam.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from maezo.a2a import Budget, DelegationEnvelope, HandlerOutput

from .graph import (
    _CALLER_INPUT_FIELDS,
    ERROR_START_PROCESS_ENGINE_UNAVAILABLE,
    ORIGIN_PAGTO_WORKER,
    AndreState,
    Flow,
    PatientSummaryReader,
    PopulationFeatureClient,
    _business_key,
    build,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from maezo.a2a import DelegationDispatcher, DelegationResult
    from maezo.runtime.inference import InferenceProvider
    from maezo.tools.mcp_cibseven.transport import AuditStartSink, CibSevenTransport
    from maezo.tools.workers.dmn_transport import DmnTransport

# SHARED task type (contract SP-OP-ADEQUACAO-001; already in Andre's accepted_task_types —
# deliberately NOT a new type, see module docstring's taxonomy decision).
TASK_TYPE_POPULATION_ANALYTICS = "analytics.population"

# The delegation ORIGINATES in the worker runtime — the origin id is what disambiguates the
# shared task_type into Andre's `adequacao_dossier` flow (`_flow_for`).
ORIGIN_WORKER = "adequacao-worker"

# `ORIGIN_PAGTO_WORKER` (the PAGTO worker's origin) is IMPORTED from `graph.py` above and
# re-exported here — `graph.py` is the single source of truth because HIS `start_process` is what
# compares against it (GK-dossier finding 1b: a delegation carrying this origin came from INSIDE
# an already-running SP-OP-PAGTO-001 instance, so no second instance may ever be started), and
# this module imports graph, never the reverse. It is deliberately NOT in `_ORIGIN_TO_FLOW`: it
# must resolve through `_flow_for`'s DEFAULT branch to Andre's own default flow `pagto_dossier`
# (his graph documents `pagto_dossier` as "convoked by operadora.pagto.prepare_approval_dossier").
# The SHARED task_type `analytics.population` is reused — no new task_type is minted and
# `spec/agents/andre/agent.yaml` stays untouched, exactly as the adequacao edge does.

TARGET_AGENT = "andre"

# Default budget for a dossier delegation chain (per the Helena->Rafael exemplar).
_DEFAULT_BUDGET = Budget(tokens=64, time_ms=60_000, cost_per_hop=1)

# envelope.origin -> Andre graph `flow`. Any origin NOT in this map gets his graph's DEFAULT flow
# (`pagto_dossier`) — set EXPLICITLY by `state_from_envelope`, never left to the graph's silent
# missing-key default.
_ORIGIN_TO_FLOW: dict[str, Flow] = {ORIGIN_WORKER: "adequacao_dossier"}
_DEFAULT_FLOW: Flow = "pagto_dossier"

# Non-PHI STRING keys forwarded in `payload_meta` for the adequacao cell (bounded enums /
# IBGE-granularity region tokens / cycle ids — never an address, never beneficiary data).
_ADEQ_STRING_META_KEYS = (
    "regiao_saude",
    "especialidade",
    "ciclo_avaliacao",
    "tipo_carater",
    "gap_adequacao",
    "roteamento_remediacao",
)

# Numeric aggregates of the cell (serialized as strings in payload_meta; parsed back typed).
_ADEQ_INT_META_KEYS = ("tempo_acesso_apurado_min", "prestadores_disponiveis")
_ADEQ_FLOAT_META_KEYS = ("distancia_apurada_km",)

# Geo-analysis booleans resolved by the process's own workers/DMNs BEFORE this hop.
_ADEQ_BOOLEAN_META_KEYS = ("cobertura_geo_suficiente", "dados_geo_completos")

# pagto_dossier meta keys (the DEFAULT flow of the shared task_type — PAGTO originators).
_PAGTO_STRING_META_KEYS = (
    "ordem_pagamento_id",
    "numero_lote_tiss",
    "prestador_id",
    "tipo_pagamento",
    "moeda",
    "competencia",
    "data_vencimento",
    "conta_origem_ref",
    "instrumento_pagamento",
    "cohort_id",
)
_PAGTO_BOOLEAN_META_KEYS = (
    "dados_pagamento_validos",
    "lastro_confirmado",
    "dentro_teto_l2",
    "duplicidade_suspeita",
)


def adequacao_task_id(
    tenant: str, regiao_saude: str, especialidade: str, ciclo_avaliacao: str | None = None
) -> str:
    """Idempotent `task_id` == the adequacao cell key (dispatcher Guard 4).

    `ADEQ-{tenant}-{regiao}-{especialidade}` or, with an evaluation cycle,
    `ADEQ-{tenant}-{regiao}-{especialidade}-{ciclo}` — the SAME cell format
    `andre.graph._business_key` anchors (and SP-OP-ADEQUACAO-001 itself uses).
    """
    if ciclo_avaliacao:
        return f"ADEQ-{tenant}-{regiao_saude}-{especialidade}-{ciclo_avaliacao}"
    return f"ADEQ-{tenant}-{regiao_saude}-{especialidade}"


def build_adequacao_dossier_envelope(
    *,
    tenant: str,
    regiao_saude: str,
    especialidade: str,
    case_meta: dict[str, Any],
    ciclo_avaliacao: str | None = None,
    budget: Budget | None = None,
) -> DelegationEnvelope:
    """Build the worker->Andre root envelope for the remediation dossier (adequacao cell).

    `payload_ref` is the cell's process reference (`process://ADEQ-...`) — a non-PHI anchor.
    `case_meta`: the process variables available at `ST_PrepareRemediationDossier` time,
    serialized through the strict allowlists above. Applies the anti-loop guards at the root.
    """
    task_id = adequacao_task_id(tenant, regiao_saude, especialidade, ciclo_avaliacao)
    meta: dict[str, str] = {"regiao_saude": regiao_saude, "especialidade": especialidade}
    if ciclo_avaliacao:
        meta["ciclo_avaliacao"] = ciclo_avaliacao
    for key in _ADEQ_STRING_META_KEYS:
        if key not in meta and case_meta.get(key):
            meta[key] = str(case_meta[key])
    for key in _ADEQ_INT_META_KEYS + _ADEQ_FLOAT_META_KEYS:
        if case_meta.get(key) is not None:
            meta[key] = str(case_meta[key])
    for key in _ADEQ_BOOLEAN_META_KEYS:
        if key in case_meta:
            meta[key] = "true" if bool(case_meta[key]) else "false"
    return DelegationEnvelope.root(
        task_id=task_id,
        task_type=TASK_TYPE_POPULATION_ANALYTICS,
        origin=ORIGIN_WORKER,
        target=TARGET_AGENT,
        tenant=tenant,
        budget=budget or _DEFAULT_BUDGET,
        payload_ref=f"process://{task_id}",
        payload_meta=meta,
    )


async def delegate_adequacao_dossier(
    dispatcher: DelegationDispatcher,
    *,
    tenant: str,
    regiao_saude: str,
    especialidade: str,
    case_meta: dict[str, Any],
    ciclo_avaliacao: str | None = None,
    budget: Budget | None = None,
) -> DelegationResult:
    """Originate and dispatch the worker->Andre delegation. Idempotent by `task_id` (the cell).

    Returns the dispatcher's structured `DelegationResult` (success with `output_ref` = the cell's
    anchor reference + `meta` = Andre's bounded routing summary, or a structured rejection —
    never a raise out of the dispatcher).
    """
    envelope = build_adequacao_dossier_envelope(
        tenant=tenant,
        regiao_saude=regiao_saude,
        especialidade=especialidade,
        case_meta=case_meta,
        ciclo_avaliacao=ciclo_avaliacao,
        budget=budget,
    )
    return await dispatcher.delegate(envelope)


# --- PAGTO origin (payment-approval dossier — the DEFAULT `pagto_dossier` flow) ------------------


def pagto_task_id(
    tenant: str,
    *,
    ordem_pagamento_id: str = "",
    numero_lote_tiss: str = "",
    prestador_id: str = "",
    business_key: str = "",
) -> str:
    """Idempotent `task_id` == the SP-OP-PAGTO-001 business key (dispatcher Guard 4).

    The ENGINE's authoritative `business_key` VERBATIM when the caller threaded one and it
    carries this tenant's `PAGTO-{tenant}-` prefix (GK-dossier finding 1a — the running
    instance's own key, which for a CONTAS-001-adjudicated payment is the
    `PAGTO-{tenant}-{numero_lote_tiss}-{prestador_id}` variant even when an `ordem_pagamento_id`
    is also in scope). Otherwise DERIVED: `PAGTO-{tenant}-{ordem_pagamento_id}` or
    `PAGTO-{tenant}-{numero_lote_tiss}-{prestador_id}` — the SAME derivation
    `andre.graph._business_key` uses for the `pagto_dossier` flow (contract SP-OP-PAGTO-001
    §Business key), so an engine re-delivery (incl. the GAP-PAGTO-5 `seguir_analise` re-entry for
    the same case) replays the SAME delegation result without re-running Andre.

    GUARDS ITSELF (GK-dossier finding 6, EB-4 R1 `non_blank` discipline): a blank/whitespace-only
    tenant, or no ordem AND no complete lote+prestador, raises `ValueError` instead of minting a
    degenerate key like `PAGTO-amh--`. The pagto worker's own `except` turns that into a DISCLOSED
    gap (DL-0037: the human UT still opens) — the guard is here so no future caller can bypass it.
    """
    tenant = str(tenant or "").strip()
    if not tenant:
        raise ValueError("pagto_task_id requires a non-blank tenant (ADR-0004 tenant scope)")
    engine_key = str(business_key or "").strip()
    if engine_key and engine_key.startswith(f"PAGTO-{tenant}-"):
        return engine_key
    ordem = str(ordem_pagamento_id or "").strip()
    lote = str(numero_lote_tiss or "").strip()
    prestador = str(prestador_id or "").strip()
    if ordem:
        return f"PAGTO-{tenant}-{ordem}"
    if not (lote and prestador):
        raise ValueError(
            "pagto_task_id requires a non-blank ordem_pagamento_id, or a complete "
            "numero_lote_tiss + prestador_id pair (no degenerate PAGTO business key)"
        )
    return f"PAGTO-{tenant}-{lote}-{prestador}"


def build_pagto_dossier_envelope(
    *,
    tenant: str,
    case_meta: dict[str, Any],
    ordem_pagamento_id: str = "",
    numero_lote_tiss: str = "",
    prestador_id: str = "",
    business_key: str = "",
    budget: Budget | None = None,
) -> DelegationEnvelope:
    """Build the worker->Andre root envelope for the payment APPROVAL dossier (SP-OP-PAGTO-001).

    The SHARED task_type `analytics.population` targets Andre; `origin=ORIGIN_PAGTO_WORKER` is NOT
    in `_ORIGIN_TO_FLOW`, so `_flow_for` resolves it to Andre's DEFAULT flow `pagto_dossier` (the
    payment-approval risk dossier his graph documents as convoked by
    `operadora.pagto.prepare_approval_dossier`). No new task_type is minted — the shared type is
    disambiguated by origin ALONE (mirrors the adequacao edge; `spec/agents/andre` untouched).

    `payload_ref` is the case's process reference (`process://PAGTO-...`) — a non-PHI anchor.
    `case_meta`: the process variables at `ST_PrepareApprovalDossier` time, serialized through the
    strict pagto allowlists (`_PAGTO_STRING_META_KEYS` + `valor_pagamento_cents` INTEGER-CENTAVOS +
    `_PAGTO_BOOLEAN_META_KEYS`) — the SAME facts Andre's `pagto_dossier` flow consumes, all
    worker-pre-resolved (validate_payment_data/calculate_facts). Free text never rides this seam.
    Applies the anti-loop guards at the root.

    `business_key` (GK-dossier finding 1a) is the ENGINE's authoritative key for the RUNNING
    instance (`ExternalTask.business_key`), threaded verbatim by the pagto worker. It becomes the
    `task_id`/`payload_ref` anchor AND rides `payload_meta["engine_business_key"]` so
    `state_from_envelope` can hand it to Andre's graph, whose `start_process` then consults the
    SAME key the live instance carries instead of its own ordem-first derivation (which diverges
    for the contract's CONTAS variant and would start a SECOND SP-OP-PAGTO-001 instance — a
    duplicate `UT_AprovacaoAlcada` approval/release path). Blank -> pure derivation, as before.
    A business key is NOT PHI: it is the same `PAGTO-{tenant}-...` token already in `payload_ref`.
    """
    task_id = pagto_task_id(
        tenant,
        ordem_pagamento_id=ordem_pagamento_id,
        numero_lote_tiss=numero_lote_tiss,
        prestador_id=prestador_id,
        business_key=business_key,
    )
    # Seed the case-identity keys explicitly, then overlay the case_meta allowlists (so the target's
    # `receive` always has the business key even if `case_meta` is partial — mirrors adequacao).
    meta: dict[str, str] = {}
    # The ENGINE's key travels as its own meta entry (NOT one of the case allowlists): the target
    # side seeds it into `engine_business_key`, which `graph._business_key` prefers verbatim.
    if task_id == str(business_key or "").strip():
        meta["engine_business_key"] = task_id
    for key, value in (
        ("ordem_pagamento_id", ordem_pagamento_id),
        ("numero_lote_tiss", numero_lote_tiss),
        ("prestador_id", prestador_id),
    ):
        if value:
            meta[key] = str(value)
    for key in _PAGTO_STRING_META_KEYS:
        if key not in meta and case_meta.get(key):
            meta[key] = str(case_meta[key])
    if case_meta.get("valor_pagamento_cents") is not None:
        # INTEGER-CENTAVOS (ADR-0018 part 2 — money never as float/number on the seam).
        meta["valor_pagamento_cents"] = str(int(case_meta["valor_pagamento_cents"]))
    for key in _PAGTO_BOOLEAN_META_KEYS:
        if key in case_meta:
            meta[key] = "true" if bool(case_meta[key]) else "false"
    return DelegationEnvelope.root(
        task_id=task_id,
        task_type=TASK_TYPE_POPULATION_ANALYTICS,
        origin=ORIGIN_PAGTO_WORKER,
        target=TARGET_AGENT,
        tenant=tenant,
        budget=budget or _DEFAULT_BUDGET,
        payload_ref=f"process://{task_id}",
        payload_meta=meta,
    )


async def delegate_pagto_dossier(
    dispatcher: DelegationDispatcher,
    *,
    tenant: str,
    case_meta: dict[str, Any],
    ordem_pagamento_id: str = "",
    numero_lote_tiss: str = "",
    prestador_id: str = "",
    business_key: str = "",
    budget: Budget | None = None,
) -> DelegationResult:
    """Originate and dispatch the worker->Andre payment-dossier delegation. Idempotent by `task_id`.

    `business_key` is the ENGINE's authoritative key of the RUNNING instance the caller sits in
    (`ExternalTask.business_key`) — threaded verbatim so Andre anchors the SAME case and never
    starts a duplicate one (GK-dossier finding 1a; see `build_pagto_dossier_envelope`).

    Returns the dispatcher's structured `DelegationResult` (success with `output_ref` = the case's
    process anchor + `meta` = Andre's bounded routing summary, or a structured rejection — never a
    raise out of the dispatcher).
    """
    envelope = build_pagto_dossier_envelope(
        tenant=tenant,
        case_meta=case_meta,
        ordem_pagamento_id=ordem_pagamento_id,
        numero_lote_tiss=numero_lote_tiss,
        prestador_id=prestador_id,
        business_key=business_key,
        budget=budget,
    )
    return await dispatcher.delegate(envelope)


# --- Target side (mirrors rafael/delegation.py) -------------------------------------------------

#: `HandlerOutput.meta` key carrying Andre's INTERNAL degradation class (GK-dossier finding 4).
#: A delegation can SUCCEED (structurally: the graph ran, routed and produced a dossier) while
#: Andre was degraded inside — a DMN in the assess chain unavailable, the engine unreachable at
#: the anchor step, or runtime context missing. Without this key the originating worker reported a
#: clean `dossier_prepared=True` and the human approver could not tell an enriched dossier from a
#: degraded one. The value is ALWAYS from `DEGRADED_TOKENS` (or ""), never free text.
DEGRADED_META_KEY = "degraded"

#: CLOSED set of degradation class tokens. Exported so the originating worker can re-validate the
#: token it disclosed as an engine variable instead of trusting whatever meta arrives.
DEGRADED_DMN = "dmn_indisponivel"
DEGRADED_ENGINE = "engine_inacessivel"
DEGRADED_CONTEXT = "contexto_incompleto"
DEGRADED_TOKENS: frozenset[str] = frozenset({DEGRADED_DMN, DEGRADED_ENGINE, DEGRADED_CONTEXT})


def _degradation_token(result: dict[str, Any]) -> str:
    """Classify Andre's terminal state into a BOUNDED degradation token (or `""` = not degraded).

    Ordered, first match wins; all three inputs are already bounded/class-shaped, and the only
    free-text one (`error`) is matched by EQUALITY against `graph`'s own constant, never sniffed:

    - `dmn_error` set, or `motivo_humano == "dmn_indisponivel"` -> `dmn_indisponivel` (a DMN in
      the assess chain failed; his conservative route stands in for the missing decision).
    - `error == ERROR_START_PROCESS_ENGINE_UNAVAILABLE` -> `engine_inacessivel` (the anchor step
      could not reach the engine).
    - any other `error` -> `contexto_incompleto` (his `receive` guards: missing tenant/cohort/cell
      identity/payment key, or an unrecognized flow).

    NEVER a decision, a price or PHI — a class token only.
    """
    if result.get("dmn_error") or result.get("motivo_humano") == DEGRADED_DMN:
        return DEGRADED_DMN
    error = str(result.get("error") or "")
    if not error:
        return ""
    return DEGRADED_ENGINE if error == ERROR_START_PROCESS_ENGINE_UNAVAILABLE else DEGRADED_CONTEXT


def _as_bool(value: Any) -> bool:
    """`payload_meta` is `Mapping[str, str]` (A2A) — normalize "true"/"false" strings to bool."""
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"true", "1", "yes", "sim"}


def _flow_for(envelope: DelegationEnvelope) -> Flow:
    """envelope.origin -> Andre graph `flow` (the shared-task_type disambiguation).

    `adequacao-worker` -> `adequacao_dossier`; ANY other origin -> Andre's default flow
    (`pagto_dossier`). The result is ALWAYS set explicitly on the materialized state — his
    graph's own missing-key default (`graph._flow`) must never be what decides the flow on an
    A2A hop.
    """
    return _ORIGIN_TO_FLOW.get(envelope.origin, _DEFAULT_FLOW)


def state_from_envelope(envelope: DelegationEnvelope) -> AndreState:
    """Materialize Andre's initial graph state from a delegation envelope.

    Sets `flow` EXPLICITLY from `_flow_for(envelope)` (never omitted — the graph would silently
    treat a flow-less state as a payment triage). Enforces Andre's input boundary
    (`graph._CALLER_INPUT_FIELDS`) the way `new_rafael_state` does on the Rafael edge; his
    `receive` node then re-sanitizes output-only fields as defense in depth (and fail-safes to
    conservative human review when the flow's minimum identifiers are missing — never a malformed
    payment key, never an adverse effect).
    """
    meta = dict(envelope.payload_meta)
    flow = _flow_for(envelope)
    raw: dict[str, Any] = {
        "flow": flow,
        "tenant_id": envelope.tenant,
        "canal": "a2a",
        # GK-dossier finding 1b: the graph's `start_process` refuses to start SP-OP-PAGTO-001 when
        # the delegation came from INSIDE a running instance (`pagto-worker`). Always seeded (the
        # graph reads a bounded comparison only), never inferred at the graph layer.
        "delegation_origin": envelope.origin,
    }
    if meta.get("engine_business_key"):
        # GK-dossier finding 1a: the ENGINE's authoritative key wins over the graph's ordem-first
        # derivation (tenant-prefix-checked there — a foreign key falls back to the derivation).
        raw["engine_business_key"] = str(meta["engine_business_key"])
    if flow == "adequacao_dossier":
        for key in _ADEQ_STRING_META_KEYS:
            if meta.get(key):
                raw[key] = meta[key]
        for key in _ADEQ_INT_META_KEYS:
            if meta.get(key) is not None:
                raw[key] = int(float(str(meta[key])))
        for key in _ADEQ_FLOAT_META_KEYS:
            if meta.get(key) is not None:
                raw[key] = float(str(meta[key]))
        for key in _ADEQ_BOOLEAN_META_KEYS:
            if key in meta:
                raw[key] = _as_bool(meta[key])
    else:
        for key in _PAGTO_STRING_META_KEYS:
            if meta.get(key):
                raw[key] = meta[key]
        if meta.get("valor_pagamento_cents") is not None:
            # INTEGER-CENTAVOS (ADR-0018 part 2 — money never as float/number).
            raw["valor_pagamento_cents"] = int(str(meta["valor_pagamento_cents"]))
        for key in _PAGTO_BOOLEAN_META_KEYS:
            if key in meta:
                raw[key] = _as_bool(meta[key])

    unknown = sorted(k for k in raw if k not in _CALLER_INPUT_FIELDS)
    if unknown:  # pragma: no cover — structural guard; raw is built from the allowlists above.
        raise ValueError(
            f"state_from_envelope produced non-input keys for Andre: {unknown} — only "
            "graph._CALLER_INPUT_FIELDS may be seeded by a delegation seam"
        )
    return cast("AndreState", raw)


def make_andre_handler(
    inference: InferenceProvider,
    *,
    dmn: DmnTransport,
    cibseven: CibSevenTransport,
    audit_sink: AuditStartSink,
    fhir: PatientSummaryReader | None = None,
    population: PopulationFeatureClient | None = None,
    agent_version: str = "andre@v0",
) -> Callable[[DelegationEnvelope], Awaitable[HandlerOutput]]:
    """Build Andre's A2A handler (delegation target). The worker-runtime composition root
    registers it with the dispatcher (`handlers={"andre": ...}`, see
    `runtime.agent_runtime.a2a_composition.build_dossier_delegation_dispatcher`).

    Compiles the REAL Andre graph via `andre.graph.build(config)` (fail-closed — `inference`/
    `dmn`/`cibseven`/`audit_sink` REQUIRED; `fhir`/`population` optional labeled boundaries) and
    invokes it with the envelope-materialized state. In the `adequacao_dossier` flow no process is
    ever started (his `start_process` no-ops) — `output_ref` is the cell's ANCHOR reference and
    the remediation decision belongs solely to the human `UT_DecisaoFallback`.
    """
    compiled = build(
        {
            "inference": inference,
            "dmn": dmn,
            "cibseven": cibseven,
            "audit_sink": audit_sink,
            "fhir": fhir,
            "population": population,
            "agent_version": agent_version,
        }
    ).compile()

    async def handler(envelope: DelegationEnvelope) -> HandlerOutput:
        state = state_from_envelope(envelope)
        result: dict[str, Any] = await compiled.ainvoke(state)
        business_key = result.get("business_key") or _business_key(state)
        # GUARDRAIL: output_ref is the case/cell reference — never a payment release, a price, or
        # a remediation decision. The dossier (whose `decisao_pagamento`/`preco_recomendado`/
        # `fhir_patient_id` are structurally always None, `graph.py`'s own guardrails) is
        # deliberately NOT forwarded here at all — meta carries ONLY bounded routing class tokens.
        return HandlerOutput(
            output_ref=f"process://{business_key}",
            meta={
                "route": str(result.get("route", "human_review")),
                "desfecho": str(result.get("desfecho", "")),
                "motivo_humano": str(result.get("motivo_humano") or ""),
                "grupo_destino": str(result.get("grupo_humano") or ""),
                "process_started": str(result.get("process_started", False)),
                # GK-dossier finding 4: a STRUCTURALLY successful delegation can still have run
                # degraded inside. Disclose the class so the originator can flag it to the human
                # approver instead of reporting a clean dossier. "" = not degraded.
                DEGRADED_META_KEY: _degradation_token(result),
            },
        )

    return handler

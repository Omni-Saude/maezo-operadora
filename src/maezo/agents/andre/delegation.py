"""Andre as A2A delegation TARGET + the ADEQUACAO-worker ORIGIN builder (`analytics.population`).

DL-0033 declared the `operadora.adequacao.prepare_remediation_dossier` worker a LOCAL STUB and
deferred the real Andre wiring to a future full-A2A task. This module is that wiring's Andre
half, mirroring the LIVE Helena->Rafael exemplar:

- ORIGIN side (mirrors `agents/helena/delegation.py`): `build_adequacao_dossier_envelope` /
  `delegate_adequacao_dossier`, called by the `operadora.adequacao.prepare_remediation_dossier`
  worker (`tools/workers/adequacao.py`) from INSIDE the running SP-OP-ADEQUACAO-001 instance
  (`ST_PrepareRemediationDossier`, ANALISE_HUMANA branch — also the `seguir_analise` re-entry).
- TARGET side (mirrors `agents/rafael/delegation.py`): `state_from_envelope` +
  `make_andre_handler` — compiles Andre's REAL graph (`agents.andre.graph.build(config)`,
  fail-closed) and runs it with envelope-materialized state.

TASK-TYPE TAXONOMY (orchestrator decision, this build): the contract names `analytics.population`
(`SP-OP-ADEQUACAO-001.md`), a type Andre's card ALREADY accepts and PAGTO will also use — no new
task_type is minted and `spec/agents/andre/agent.yaml` is untouched. The SHARED type is
disambiguated by `envelope.origin` -> Andre's graph `flow` state key (`_flow_for`):
`adequacao-worker` -> `flow="adequacao_dossier"`; ANY other origin -> his default
(`pagto_dossier`). CRITICAL: a state with NO `flow` key silently defaults to `pagto_dossier`
inside his graph (`graph._flow`), which would treat an adequacao cell as a payment triage — this
layer therefore sets `flow` EXPLICITLY on every materialized state, never by omission.

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


# --- Target side (mirrors rafael/delegation.py) -------------------------------------------------


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
    }
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
            },
        )

    return handler

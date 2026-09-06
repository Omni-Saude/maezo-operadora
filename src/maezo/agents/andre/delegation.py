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

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, cast

from maezo.a2a import Budget, DelegationEnvelope, HandlerOutput
from maezo.a2a.dispatcher import origin_signer_of
from maezo.runtime.metrics import classify_agent_error_type
from maezo.runtime.start_outcome import StartProcessFailedError
from maezo.tools.mcp_cibseven.transport import StartOutcome

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
from .keys import adequacao_business_key, pagto_business_key

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from maezo.a2a import DelegationDispatcher, DelegationResult, EnvelopeSigner
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

# Wall-clock in-flight horizon stamped as `deadline` on a SIGNED envelope (ADR-0039 §4.3.1). 6 hours
# — see agents/helena/delegation.py::_SIGNED_ENVELOPE_TTL for the shared rationale (far under the
# 7-day max-signature-age, so max-age dominates key-purge timing). Unsigned envelopes keep None.
_SIGNED_ENVELOPE_TTL = timedelta(hours=6)

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
    `ADEQ-{tenant}-{regiao}-{especialidade}-{ciclo}` — the SAME cell format SP-OP-ADEQUACAO-001
    itself uses.

    M-8: "the SAME cell format `andre.graph._business_key` anchors" used to be asserted here and
    was FALSE — this site did not strip its segments and did not drop empty ones, while the graph
    did both, so the two composed DIFFERENT keys for the same cell. Both now delegate to the one
    strict, position-preserving composer (`keys.adequacao_business_key`), which additionally
    REFUSES an empty segment instead of dropping it (the drop made
    `(R-001, "2026-Q3", "")` and `(R-001, "", "2026-Q3")` collide on one key).

    Raises:
        ValueError: blank tenant/regiao/especialidade, or a supplied-but-blank `ciclo_avaliacao`.
            The adequacao worker's own `except` turns that into a DISCLOSED gap that still opens
            the human UT (`tools/workers/adequacao.py:731-749`, DL-0037).
    """
    return adequacao_business_key(tenant, regiao_saude, especialidade, ciclo_avaliacao)


def build_adequacao_dossier_envelope(
    *,
    tenant: str,
    regiao_saude: str,
    especialidade: str,
    case_meta: dict[str, Any],
    ciclo_avaliacao: str | None = None,
    budget: Budget | None = None,
    signer: EnvelopeSigner | None = None,
) -> DelegationEnvelope:
    """Build the worker->Andre root envelope for the remediation dossier (adequacao cell).

    `payload_ref` is the cell's process reference (`process://ADEQ-...`) — a non-PHI anchor.
    `case_meta`: the process variables available at `ST_PrepareRemediationDossier` time,
    serialized through the strict allowlists above. Applies the anti-loop guards at the root.

    SIGNING (ADR-0039 §4.4, leg E3): with `signer` present the envelope carries an explicit
    `deadline` (§4.3.1) and is HMAC-signed over its v2 canonical digest. Absent -> unsigned (dev).
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
    envelope = DelegationEnvelope.root(
        task_id=task_id,
        task_type=TASK_TYPE_POPULATION_ANALYTICS,
        origin=ORIGIN_WORKER,
        target=TARGET_AGENT,
        tenant=tenant,
        budget=budget or _DEFAULT_BUDGET,
        payload_ref=f"process://{task_id}",
        deadline=(datetime.now(tz=UTC) + _SIGNED_ENVELOPE_TTL) if signer is not None else None,
        payload_meta=meta,
    )
    return signer.sign(envelope) if signer is not None else envelope


async def delegate_adequacao_dossier(
    dispatcher: DelegationDispatcher,
    *,
    tenant: str,
    regiao_saude: str,
    especialidade: str,
    case_meta: dict[str, Any],
    ciclo_avaliacao: str | None = None,
    budget: Budget | None = None,
    signer: EnvelopeSigner | None = None,
) -> DelegationResult:
    """Originate and dispatch the worker->Andre delegation. Idempotent by `task_id` (the cell).

    Returns the dispatcher's structured `DelegationResult` (success with `output_ref` = the cell's
    anchor reference + `meta` = Andre's bounded routing summary, or a structured rejection —
    never a raise out of the dispatcher for a TERMINAL handler failure).

    A regra REAL, e ela nao e' uma ressalva unica: SO' as classes TERMINAIS (`ValueError`,
    `TypeError`, `KeyError` E SUBCLASSES, decididas por `isinstance` em
    `a2a/dispatcher.py::_TERMINAL_HANDLER_ERROR_CLASSES`) voltam como rejeicao estruturada e
    selada. TODA OUTRA excecao do handler PROPAGA sem selar, para que a entrega continue
    retentavel — e isso inclui DUAS coisas diferentes: o canal TRANSITORIO RAF-02
    (`StartProcessFailedError` de um start recusado pelo engine, `AuditPersistenceError`), que
    propaga de proposito, E qualquer bug NAO CLASSIFICADO do grafo (`AttributeError`,
    `IndexError`, ...), que propaga porque selar um bug desconhecido seria pior. Nenhuma das duas
    e' silenciosa: o dispatcher grava a linha de audit NAO-terminal `PROPAGATED` e conta em
    `maezo_a2a_handler_error_total` antes de relevantar
    (`a2a/dispatcher.py::DelegationDispatcher._trace_propagated_handler_error`).

    SIGNING (ADR-0039 §4.4): `signer` defaults to the edge signer carried by `dispatcher`
    (`origin_signer_of`), so the LIVE worker path signs with no per-worker wiring change.
    """
    resolved_signer = signer if signer is not None else origin_signer_of(dispatcher)
    envelope = build_adequacao_dossier_envelope(
        tenant=tenant,
        regiao_saude=regiao_saude,
        especialidade=especialidade,
        case_meta=case_meta,
        ciclo_avaliacao=ciclo_avaliacao,
        budget=budget,
        signer=resolved_signer,
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

    M-8: the derivation is now literally SHARED with `andre.graph._business_key` via
    `keys.pagto_business_key` rather than re-implemented. The graph's copy normalised NOTHING, so
    a whitespace-padded or non-`str` `ordem_pagamento_id` composed a DIFFERENT key there than
    here, and a fully blank case minted `PAGTO-{tenant}--` instead of refusing.
    """
    return pagto_business_key(
        tenant,
        ordem_pagamento_id=ordem_pagamento_id,
        numero_lote_tiss=numero_lote_tiss,
        prestador_id=prestador_id,
        business_key=business_key,
    )


def build_pagto_dossier_envelope(
    *,
    tenant: str,
    case_meta: dict[str, Any],
    ordem_pagamento_id: str = "",
    numero_lote_tiss: str = "",
    prestador_id: str = "",
    business_key: str = "",
    budget: Budget | None = None,
    signer: EnvelopeSigner | None = None,
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
    # GUARDS ITSELF (GK-dossier finding 6): `pagto_task_id` raises on a blank tenant or a missing
    # identifier — this builder must NOT be a way around that, so it calls the guard FIRST and
    # anchors the envelope on the SAME normalized tenant. Passing the RAW `tenant` to
    # `DelegationEnvelope.root` would let `PAGTO-{stripped}-...` ride an envelope whose
    # `envelope.tenant` still has the surrounding whitespace — the target's `state_from_envelope`
    # would seed THAT into `tenant_id` and `graph._business_key` would then derive a DIFFERENT key
    # (`PAGTO- amh -...`), reopening the finding-1 divergence from the other side.
    task_id = pagto_task_id(
        tenant,
        ordem_pagamento_id=ordem_pagamento_id,
        numero_lote_tiss=numero_lote_tiss,
        prestador_id=prestador_id,
        business_key=business_key,
    )
    tenant = str(tenant).strip()
    # Seed the case-identity keys explicitly, then overlay the case_meta allowlists (so the target's
    # `receive` always has the business key even if `case_meta` is partial — mirrors adequacao).
    meta: dict[str, str] = {}
    # The ENGINE's key travels as its own meta entry (NOT one of the case allowlists): the target
    # side seeds it into `engine_business_key`, which `graph._business_key` prefers verbatim.
    if task_id == str(business_key or "").strip():
        meta["engine_business_key"] = task_id
    # NON-BLANK discipline (GK-dossier finding 6): the identity keys ride STRIPPED, and a
    # whitespace-only one is omitted entirely rather than forwarded. A blank-but-truthy `"  "`
    # would otherwise reach the target's state and, since `graph._business_key` is ordem-FIRST,
    # derive `PAGTO-{tenant}-  ` — a degenerate key that anchors nothing (and a fresh instance).
    for key, value in (
        ("ordem_pagamento_id", ordem_pagamento_id),
        ("numero_lote_tiss", numero_lote_tiss),
        ("prestador_id", prestador_id),
    ):
        if str(value or "").strip():
            meta[key] = str(value).strip()
    for key in _PAGTO_STRING_META_KEYS:
        if key not in meta and str(case_meta.get(key) or "").strip():
            meta[key] = str(case_meta[key]).strip()
    if case_meta.get("valor_pagamento_cents") is not None:
        # INTEGER-CENTAVOS (ADR-0018 part 2 — money never as float/number on the seam). REJECTS a
        # non-int rather than coercing (GK-dossier finding 8): the old `int(...)` TRUNCATED, so a
        # `1234.99` reaching this builder became `1234` and Andre's faixa/alcada routing ran on a
        # value the process never had — silently, on the money path. A `bool` is an `int` subclass
        # and is refused too. The worker's own `except` turns this into a DISCLOSED gap (DL-0037:
        # the UT still opens and the approver sees the real number), never a truncated dossier.
        valor = case_meta["valor_pagamento_cents"]
        if not isinstance(valor, int) or isinstance(valor, bool):
            raise ValueError(
                "valor_pagamento_cents must be INTEGER centavos (ADR-0018 part 2) — got "
                f"{type(valor).__name__}; money is never truncated onto this seam"
            )
        meta["valor_pagamento_cents"] = str(valor)
    for key in _PAGTO_BOOLEAN_META_KEYS:
        if key in case_meta:
            meta[key] = "true" if bool(case_meta[key]) else "false"
    # SIGNING (ADR-0039 §4.4, leg E3): with `signer` present the envelope carries an explicit
    # `deadline` (§4.3.1) and is HMAC-signed over its v2 canonical digest — so `payload_meta_hash`
    # binds `valor_pagamento_cents` (Andre's faixa/alcada routing input). Absent -> unsigned (dev).
    envelope = DelegationEnvelope.root(
        task_id=task_id,
        task_type=TASK_TYPE_POPULATION_ANALYTICS,
        origin=ORIGIN_PAGTO_WORKER,
        target=TARGET_AGENT,
        tenant=tenant,
        budget=budget or _DEFAULT_BUDGET,
        payload_ref=f"process://{task_id}",
        deadline=(datetime.now(tz=UTC) + _SIGNED_ENVELOPE_TTL) if signer is not None else None,
        payload_meta=meta,
    )
    return signer.sign(envelope) if signer is not None else envelope


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
    signer: EnvelopeSigner | None = None,
) -> DelegationResult:
    """Originate and dispatch the worker->Andre payment-dossier delegation. Idempotent by `task_id`.

    `business_key` is the ENGINE's authoritative key of the RUNNING instance the caller sits in
    (`ExternalTask.business_key`) — threaded verbatim so Andre anchors the SAME case and never
    starts a duplicate one (GK-dossier finding 1a; see `build_pagto_dossier_envelope`).

    Returns the dispatcher's structured `DelegationResult` (success with `output_ref` = the case's
    process anchor + `meta` = Andre's bounded routing summary, or a structured rejection — never a
    raise out of the dispatcher).

    SIGNING (ADR-0039 §4.4): `signer` defaults to the edge signer carried by `dispatcher`
    (`origin_signer_of`), so the LIVE worker path signs with no per-worker wiring change.
    """
    resolved_signer = signer if signer is not None else origin_signer_of(dispatcher)
    envelope = build_pagto_dossier_envelope(
        tenant=tenant,
        case_meta=case_meta,
        ordem_pagamento_id=ordem_pagamento_id,
        numero_lote_tiss=numero_lote_tiss,
        prestador_id=prestador_id,
        business_key=business_key,
        budget=budget,
        signer=resolved_signer,
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

#: `HandlerOutput.meta` key carrying the PAGTO start's typed outcome (F3 BLOCKER-1/MAJOR-2).
#: `process_started` is a bool and therefore CANNOT distinguish "I started SP-OP-PAGTO-001" from
#: "an instance was already live" from "the strict gate refused because this order already ran" —
#: yet an A2A originator deciding whether a payment case is live, duplicated, or already settled
#: needs exactly that. The value is always a `StartOutcome` token (or "" when no start was
#: attempted: the non-`pagto_dossier` flows, the ORIGIN_PAGTO_WORKER no-op, and the error bails).
#: A bounded class token, never PHI and never a value — same discipline as `DEGRADED_META_KEY`.
START_OUTCOME_META_KEY = "start_outcome"


def _start_outcome_token(result: dict[str, Any]) -> str:
    """Read the chokepoint's typed verdict out of `process_ref`, validated against `StartOutcome`.

    Re-VALIDATED rather than passed through: `process_ref` is graph state, and meta is a
    cross-agent wire surface — an unrecognised value is dropped to `""` (no start attested) rather
    than shipped, so this key can only ever carry a token the originator can branch on.
    """
    process_ref = result.get("process_ref")
    if not isinstance(process_ref, dict):
        return ""
    token = str(process_ref.get("start_outcome") or "")
    return token if token in _START_OUTCOME_TOKENS else ""


_START_OUTCOME_TOKENS: frozenset[str] = frozenset(o.value for o in StartOutcome)


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
    if unknown:  # REG-06: exercised for real, see test_andre_delegation.py's monkeypatch fence.
        raise ValueError(
            f"state_from_envelope produced non-input keys for Andre: {unknown} — only "
            "graph._CALLER_INPUT_FIELDS may be seeded by a delegation seam"
        )
    return cast("AndreState", raw)


#: `output_ref` when the run produced NO valid business key at all — i.e. `receive` fail-safed to
#: human review because the case lacked its flow's minimum identifiers. Honest placeholder: there
#: is no case/cell to reference. It is NOT a `process://` anchor, so no consumer can mistake it
#: for one, and (M-8) it replaces the previous behaviour of re-deriving a DEGENERATE key such as
#: `process://ADEQ-{tenant}-` on exactly this path — a malformed anchor is worse than none.
OUTPUT_REF_SEM_CHAVE = "sem-chave://contexto-incompleto"


def _resolved_business_key(state: AndreState, result: dict[str, Any]) -> str:
    """The run's business key, or `""` when no VALID key exists — never a degenerate one.

    Prefers the key the graph itself resolved (`receive` sets it for every flow that has one).
    Falls back to re-deriving from the inbound state only as a belt, and swallows the strict
    composers' `ValueError` (`andre/keys.py`): reaching that fallback means `receive` already
    fail-safed this case to human review for want of identifiers, so there is nothing to compose
    and the honest answer is "no key".
    """
    resolved = str(result.get("business_key") or "")
    if resolved:
        return resolved
    try:
        return _business_key(state)
    except ValueError:
        return ""


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
        try:
            result: dict[str, Any] = await compiled.ainvoke(state)
        except Exception as exc:
            # ALERTS-WITHOUT-METRICS-a: a delegated turn that raised IS a failed agent turn, and
            # `maezo_agent_errors_total` is what `MaezoAgentCrashLoop` reads. Placed at the
            # `ainvoke` seam — the structural entry to a graph run — and NOT anywhere in andre's
            # business logic, which is why it is one line here rather than a rule each node has to
            # remember. `asyncio.CancelledError` is excluded (BaseException): a drained delegation
            # is not a failed agent. Enumerated and pinned by
            # `tests/unit/platform/test_alert_metrics_fence.py::
            # test_every_graph_invocation_in_src_counts_agent_errors`.
            from maezo.platform.observability import record_agent_error  # noqa: PLC0415

            record_agent_error(agent="andre", error_type=classify_agent_error_type(exc))
            raise
        business_key = _resolved_business_key(state, result)
        if result.get("start_failed") is True:
            # RAF-02: o grafo TENTOU abrir o processo e o engine recusou. Devolver
            # `HandlerOutput` aqui seria um sucesso para o dispatcher (`HandlerOutput` nao tem
            # campo `success`): ele gravaria o audit terminal `_DECISION_COMPLETED`, emitiria o
            # fato COMPLETED e SELARIA o resultado por `task_id` — tornando o falso sucesso
            # irretentavel. A excecao tipada propaga, entao nada disso acontece e a reentrega do
            # mesmo `task_id` reexecuta o handler. So tokens de classe na mensagem, nunca PHI.
            raise StartProcessFailedError(
                f"andre nao conseguiu iniciar SP-OP-PAGTO-001 "
                f"(business_key={business_key!r}): o turno NAO foi concluido"
            )
        # GUARDRAIL: output_ref is the case/cell reference — never a payment release, a price, or
        # a remediation decision. The dossier (whose `decisao_pagamento`/`preco_recomendado`/
        # `fhir_patient_id` are structurally always None, `graph.py`'s own guardrails) is
        # deliberately NOT forwarded here at all — meta carries ONLY bounded routing class tokens.
        return HandlerOutput(
            output_ref=f"process://{business_key}" if business_key else OUTPUT_REF_SEM_CHAVE,
            meta={
                "route": str(result.get("route", "human_review")),
                "desfecho": str(result.get("desfecho", "")),
                "motivo_humano": str(result.get("motivo_humano") or ""),
                "grupo_destino": str(result.get("grupo_humano") or ""),
                "process_started": str(result.get("process_started", False)),
                # F3 BLOCKER-1: `process_started` alone once shipped a hard-coded True even when
                # the strict gate had REFUSED to start an already-settled payment order. The bool
                # is now honest, and this token carries WHICH outcome produced it.
                START_OUTCOME_META_KEY: _start_outcome_token(result),
                # GK-dossier finding 4: a STRUCTURALLY successful delegation can still have run
                # degraded inside. Disclose the class so the originator can flag it to the human
                # approver instead of reporting a clean dossier. "" = not degraded.
                DEGRADED_META_KEY: _degradation_token(result),
            },
        )

    return handler

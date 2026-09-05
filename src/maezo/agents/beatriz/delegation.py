"""Beatriz as A2A delegation TARGET (`fraude.investigate`) — BEA-09's target half.

`spec/agents/beatriz/agent.yaml` declares `accepted_task_types: [fraude.investigate]` and its own
comment says the inbound handler "e DEFERIDA a W-R2/R9 (NAO autorada aqui)".
`docs/processes/contracts/SP-OP-FRAUDE-001.md` names BOTH origin service tasks as
"consome (worker->A2A)": `operadora.fraude.gather_evidence` ("convoca Beatriz (delegacao
`fraude.investigate`, human-gated)") and `operadora.fraude.assemble_dossier`. This module is the
TARGET half, mirroring `agents/fernando/delegation.py` / `agents/carolina/delegation.py` end to
end.

WHAT IS AND IS NOT BUILT HERE:
- TARGET side (`state_from_envelope` + `make_beatriz_handler`) — BUILT. Compiles Beatriz's REAL
  graph (`agents.beatriz.graph.build(config)`, fail-closed on a missing `inference`) and runs it
  against envelope-materialized state.
- ORIGIN builders (`build_fraude_investigation_envelope` / `delegate_fraude_investigation`) —
  BUILT and unit-proven in isolation, **NOT CALLED** from `tools/workers/fraude.py`. That worker
  is out of this work package's editable surface, and CONFIRM-B recorded its
  `gather_evidence`/`assemble_dossier` as PLACEHOLDERS that return `dossie_montado=True` without
  convoking Beatriz at all (family FAB-*). Honestifying them is a SEPARATE work package
  (BEA-09/FAB-FRAUDE2); this module does not touch it and does not claim it is done.
- REGISTRATION in `runtime.agent_runtime.a2a_composition` — **NOT DONE** (owner decision, gap
  `FERNANDO-DELEGATION-CALL-SITE`). Nothing can route a `fraude.investigate` envelope here today.

Idempotency (Guard 4): `task_id` IS the SP-OP-FRAUDE-001 business key
(`FRAUDE-{tenant}-{numero_caso}`), delegated to `beatriz.graph._business_key` itself so the two
derivations cannot drift. Both origin service tasks of one case share that key, so a re-delivery
of either external task replays the SAME delegation result without re-running Beatriz.

L0 HARD (`zero_auto_accusation`, ADR-0018) — RE-ENFORCED AT THIS SEAM: Beatriz NEVER accuses.
`BeatrizState` cannot even transport `decisao_fraude`/`bundle_root`/`destino_referral`, this
handler forwards NO dossier content, and `HandlerOutput.meta` carries only the two bounded
`Desfecho` tokens (`dossie_instruido` | `instrucao_incompleta`) plus a gap COUNT. The accusation
is born solely in `UT_DecisaoInvestigador` over the sealed bundle.

INPUT-BOUNDARY GATE: Beatriz's graph has no `new_beatriz_state` constructor (like Carolina's and
Fernando's, unlike Marina's/Rafael's); her gate is `graph._CALLER_INPUT_FIELDS` + `receive`'s own
output-field reset. `state_from_envelope` enforces it by ALLOWLIST-BY-CONSTRUCTION — `raw` only
ever copies named keys off the `_..._META_KEYS` tuples below (each a subset of
`_CALLER_INPUT_FIELDS`), so an out-of-allowlist `payload_meta` entry is never read at all. The
`unknown` check at the end is the same structural, currently-unreachable regression guard
Carolina's and Fernando's carry.

PHI / no-PHI-in-custody (ADR-0006, KPI `evidence_pseudonymized_rate == 1.0`) — WHAT NEVER RIDES
THIS SEAM. `payload_meta` is a strict non-PHI allowlist: pseudonymized identifiers, bounded enums
(`entidade_tipo`, `intensidade_investigacao`), a competencia, pointer refs, one integer and one
boolean. DELIBERATELY EXCLUDED, with the consequences stated rather than hidden:
  * `evidencia_refs` (list of dicts) — `payload_meta` is a strict `Mapping[str, str]`. A
    delegated turn therefore normalizes an EMPTY inbound evidence list, so
    `evidencia_normalizada` comes back `[]`. That is an ARTIFACT OF THIS SEAM, not a finding of
    "no evidence": the `gather_evidence` hop's entire point is normalizing that list, and a live
    origin call site must carry it over a list-capable channel (or have Beatriz read it from the
    process variables). Recorded as a residual of this work package, deliberately NOT papered
    over with an ad-hoc string encoding — a delimiter-joined blob of caller-controlled values is
    exactly how raw PHI would enter a custody-bound corpus.
  * `indicadores_presentes` (list of tokens) — same shape constraint, same consequence: the
    dossier's `indicadores_observados` is `[]` on a delegated turn. NOTE the BPMN order makes
    this benign on the FIRST hop and NOT benign on the second: `ST_GatherEvidence` (Beatriz) runs
    BEFORE `ST_ScoreIndicators`, so on `gather_evidence` the indicators genuinely do not exist
    yet; on `assemble_dossier` they do, and their absence would be a seam artifact. The scalar
    `score_indicadores` and `intensidade_investigacao` DO ride, so the routing facts survive.
  * every free-text field and every narrative — Beatriz's dossier is assembled INSIDE her graph
    and never travels back over this seam.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, cast

from maezo.a2a import Budget, DelegationEnvelope, HandlerOutput
from maezo.a2a.dispatcher import origin_signer_of
from maezo.platform.observability import record_agent_error
from maezo.runtime.metrics import classify_agent_error_type

from .graph import (
    _CALLER_INPUT_FIELDS,
    BeatrizState,
    _business_key,
    build,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from maezo.a2a import DelegationDispatcher, DelegationResult, EnvelopeSigner
    from maezo.runtime.inference import InferenceProvider

    from .graph import PatientSummaryReader

# Task type delegated to Beatriz — MUST match `spec/agents/beatriz/agent.yaml`'s
# `a2a.accepted_task_types` (fleet parity fence: `tests/unit/a2a/test_agent_card_handlers_parity.py`).
TASK_TYPE_FRAUDE_INVESTIGATE = "fraude.investigate"
TASK_TYPES: frozenset[str] = frozenset({TASK_TYPE_FRAUDE_INVESTIGATE})

# The delegation ORIGINATES in the worker runtime (`operadora.fraude.gather_evidence` /
# `operadora.fraude.assemble_dossier`, both "consome (worker->A2A)" in the contract), from INSIDE
# a running SP-OP-FRAUDE-001 instance — never in an agent graph. The dispatcher validates only the
# TARGET's Card, so the origin id is a stable, self-describing worker identity (audited as
# `agent_id` on the delegation chain link). Mirrors `carolina/delegation.py::ORIGIN_WORKER`.
ORIGIN_WORKER = "fraude-worker"
TARGET_AGENT = "beatriz"

# Default budget for a dossier delegation chain (per the Helena->Rafael exemplar).
_DEFAULT_BUDGET = Budget(tokens=64, time_ms=60_000, cost_per_hop=1)

# Wall-clock in-flight horizon stamped as `deadline` on a SIGNED envelope (ADR-0039 §4.3.1) —
# same 6h rationale as `agents/helena/delegation.py::_SIGNED_ENVELOPE_TTL`.
_SIGNED_ENVELOPE_TTL = timedelta(hours=6)

# Non-PHI STRING keys forwarded in `payload_meta` (pseudonymized ids / bounded enums / pointer
# refs / a competencia — see the module docstring's PHI note for what is deliberately excluded).
_STRING_META_KEYS = (
    "numero_caso",
    "origem_encaminhamento",
    "entidade_tipo",
    "entidade_pseudo_id",
    "prestador_id",
    "beneficiario_pseudo_id",
    "numero_contrato",
    "encaminhado_por_id",
    "competencia",
    "feature_snapshot_ref",
    "patient_summary_ref",
    "intensidade_investigacao",
)

# Worker-pre-resolved INTEGER routing fact, forwarded as a decimal string (never a verdict).
_INTEGER_META_KEYS = ("score_indicadores",)

# Informative Phase-2 boolean signal (never decides).
_BOOLEAN_META_KEYS = ("indicio_fraude_sinalizado",)


def fraude_task_id(tenant: str, numero_caso: str) -> str:
    """Idempotent `task_id` == the SP-OP-FRAUDE-001 business key (dispatcher Guard 4).

    Delegates to `beatriz.graph._business_key` (`FRAUDE-{tenant}-{numero_caso}`, contract
    §Business key) rather than re-formatting it here, so the two derivations cannot drift.
    """
    return _business_key(cast(BeatrizState, {"tenant_id": tenant, "numero_caso": numero_caso}))


def _build_payload_meta(numero_caso: str, case_meta: dict[str, Any]) -> dict[str, str]:
    """Serialize the case into `payload_meta` (`Mapping[str, str]`, strict non-PHI allowlist)."""
    meta: dict[str, str] = {"numero_caso": numero_caso}
    for key in _STRING_META_KEYS:
        if key == "numero_caso":
            continue  # explicit parameter, not case_meta-sourced
        if case_meta.get(key):
            meta[key] = str(case_meta[key])
    for key in _INTEGER_META_KEYS:
        if key in case_meta:
            meta[key] = str(int(case_meta[key]))
    for key in _BOOLEAN_META_KEYS:
        if key in case_meta:
            meta[key] = "true" if bool(case_meta[key]) else "false"
    return meta


def build_fraude_investigation_envelope(
    *,
    tenant: str,
    numero_caso: str,
    case_meta: dict[str, Any],
    budget: Budget | None = None,
    signer: EnvelopeSigner | None = None,
) -> DelegationEnvelope:
    """Build the worker->Beatriz root envelope for an investigation instruction.

    - `payload_ref` is the case's process reference (`process://FRAUDE-...`) — a non-PHI anchor.
    - `case_meta`: the SP-OP-FRAUDE-001 process variables available at `ST_GatherEvidence` /
      `ST_AssembleDossier` time, serialized through the strict allowlist above (unknown keys are
      silently NOT forwarded — allowlist, not blocklist).

    Applies the anti-loop guards at the root itself (origin != target, chain <= max_hops, budget).

    SIGNING (ADR-0039 §4.4, leg E3): with `signer` present the envelope carries an explicit
    `deadline` and is HMAC-signed over its v2 canonical digest. Absent -> unsigned (dev path).
    """
    task_id = fraude_task_id(tenant, numero_caso)
    envelope = DelegationEnvelope.root(
        task_id=task_id,
        task_type=TASK_TYPE_FRAUDE_INVESTIGATE,
        origin=ORIGIN_WORKER,
        target=TARGET_AGENT,
        tenant=tenant,
        budget=budget or _DEFAULT_BUDGET,
        payload_ref=f"process://{task_id}",
        deadline=(datetime.now(tz=UTC) + _SIGNED_ENVELOPE_TTL) if signer is not None else None,
        payload_meta=_build_payload_meta(numero_caso, case_meta),
    )
    return signer.sign(envelope) if signer is not None else envelope


async def delegate_fraude_investigation(
    dispatcher: DelegationDispatcher,
    *,
    tenant: str,
    numero_caso: str,
    case_meta: dict[str, Any],
    budget: Budget | None = None,
    signer: EnvelopeSigner | None = None,
) -> DelegationResult:
    """Originate and dispatch the worker->Beatriz delegation. Idempotent by `task_id`.

    NOT CALLED from `tools/workers/fraude.py` — see the module docstring's STOP boundary. Returns
    the dispatcher's structured `DelegationResult` (never a raise out of the dispatcher for a
    TERMINAL handler failure). On re-delivery of the same `task_id`, `idempotent_replay=True` and
    the handler does NOT run again.

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
    (`origin_signer_of`), so a future live worker call site signs without per-worker wiring.
    """
    resolved_signer = signer if signer is not None else origin_signer_of(dispatcher)
    envelope = build_fraude_investigation_envelope(
        tenant=tenant,
        numero_caso=numero_caso,
        case_meta=case_meta,
        budget=budget,
        signer=resolved_signer,
    )
    return await dispatcher.delegate(envelope)


# --- Target side (mirrors carolina/fernando delegation.py) --------------------------------------


def _as_bool(value: Any) -> bool:
    """`payload_meta` is `Mapping[str, str]` (A2A) — normalize "true"/"false" strings to bool."""
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"true", "1", "yes", "sim"}


def _as_int(value: Any) -> int:
    """Parse a `payload_meta` decimal string back to `int` — fails closed to 0 rather than
    dropping the whole envelope over one malformed numeric field. `_score_consumed` in her graph
    applies the SAME conservative default to anything that is not a genuine int."""
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return 0


def state_from_envelope(envelope: DelegationEnvelope) -> BeatrizState:
    """Materialize Beatriz's initial graph state from a delegation envelope.

    Case identifiers/facts come from `payload_meta` (never PHI); `tenant` comes from the envelope
    (ADR-0004). Enforces her input boundary (`graph._CALLER_INPUT_FIELDS`) by ALLOWLIST-BY-
    CONSTRUCTION: `raw` only ever copies named keys off the three `_..._META_KEYS` tuples, so an
    unknown/output-only `payload_meta` key is silently DROPPED (never read, never copied) rather
    than raising. The `unknown` check below is a structural, currently-unreachable regression
    guard, identical to Carolina's and Fernando's; `receive` re-sanitizes every output-only field
    on top of it as defense in depth.

    Fails closed on a missing `numero_caso`: her graph derives the idempotent business key from it
    and `receive` refuses to assemble an UNANCHORED dossier (`instrucao_incompleta`) — the
    contract's own reason being that an unanchored corpus could be sealed. A blank case number
    here is a producer bug, and the seam says so loudly instead of shipping the incomplete marker.
    """
    meta = dict(envelope.payload_meta)
    numero_caso = str(meta.get("numero_caso", "")).strip()
    if not numero_caso:
        raise ValueError(
            "fraude.investigate envelope has no numero_caso in payload_meta — the FRAUDE business "
            "key cannot be derived (producer bug; the originating worker validates identifiers "
            "before delegating)"
        )
    raw: dict[str, Any] = {"tenant_id": envelope.tenant, "numero_caso": numero_caso}
    for key in _STRING_META_KEYS:
        if key == "numero_caso":
            continue
        if meta.get(key):
            raw[key] = meta[key]
    for key in _INTEGER_META_KEYS:
        if key in meta:
            raw[key] = _as_int(meta[key])
    for key in _BOOLEAN_META_KEYS:
        if key in meta:
            raw[key] = _as_bool(meta[key])

    unknown = sorted(k for k in raw if k not in _CALLER_INPUT_FIELDS)
    if unknown:  # pragma: no cover - structural guard; raw is built from the allowlists above.
        raise ValueError(
            f"state_from_envelope produced non-input keys for Beatriz: {unknown} — only "
            "graph._CALLER_INPUT_FIELDS may be seeded by a delegation seam"
        )
    return cast(BeatrizState, raw)


def make_beatriz_handler(
    inference: InferenceProvider,
    *,
    fhir: PatientSummaryReader | None = None,
    agent_version: str = "beatriz@v0",
) -> Callable[[DelegationEnvelope], Awaitable[HandlerOutput]]:
    """Build Beatriz's A2A handler (delegation target).

    NOT REGISTERED with any dispatcher (module docstring): `a2a_composition` still wires only
    rafael / carolina+andre+fernando, and adding `"beatriz"` is the owner decision this work
    package stops
    in front of.

    Compiles the REAL Beatriz graph via `beatriz.graph.build(config)` — the same fail-closed
    contract every other caller goes through. Her `build` REQUIRES only `inference` and takes an
    OPTIONAL `fhir`; it deliberately IGNORES `dmn`/`cibseven` (per her audited `agent.yaml` she
    evaluates no DMN and starts no process — the engine convokes her), so this factory does not
    accept them either: offering them would silently widen her action surface beyond her
    allowlist.

    `output_ref` is the SP-OP-FRAUDE-001 business key reference (auditable, never PHI). `meta`
    carries ONLY bounded class tokens — the investigation dossier CONTENT stays in Beatriz's own
    state/engine variables and is NEVER forwarded over this seam (L0 hard: nothing that leaves
    this handler can be read as an accusation).

    PRE-CONDICOES DE REGISTRO (what the owner must settle BEFORE wiring `"beatriz"` into
    `a2a_composition` — this handler is correct on its own terms and still WRONG to register
    while either is unmet; the module docstring's §PHI residuals are the causes, these are the
    operational consequences):
      1. EVIDENCE CHANNEL FIRST. The originating call site must carry `evidencia_refs` over a
         list-capable channel (or Beatriz must read it from the SP-OP-FRAUDE-001 process
         variables) BEFORE any registration. `payload_meta` is a strict `Mapping[str, str]`, so a
         delegated turn today normalizes an EMPTY evidence list; on the `assemble_dossier` hop
         that yields `desfecho="dossie_instruido"` over an EMPTY corpus — a dossier that reads as
         instructed while resting on nothing. Registering first buys a silent, auditable-looking
         wrong answer, which is worse than the honest absence of the seam.
      2. THE SECOND HOP IS A REPLAY, NOT A SECOND RUN. `gather_evidence` and `assemble_dossier`
         share one task_type (`fraude.investigate`) AND one `task_id`
         (`FRAUDE-{tenant}-{numero_caso}`), so the dispatcher's Guard 4 answers the second hop
         with the FIRST hop's result and never re-runs Beatriz. A caller that needs the two hops
         to run as distinct units must carry a PER-HOP key, which SP-OP-FRAUDE-001 does not
         define — recorded here rather than invented (same wording as
         `agents/valentina/delegation.py`, whose `care.stratify`/`care.enroll` share a key for
         the same structural reason).
    """
    compiled = build({"inference": inference, "fhir": fhir, "agent_version": agent_version}).compile()

    async def handler(envelope: DelegationEnvelope) -> HandlerOutput:
        state = state_from_envelope(envelope)
        try:
            result: dict[str, Any] = await compiled.ainvoke(state)
        except Exception as exc:
            # ALERTS-WITHOUT-METRICS-a (mirrors carolina/delegation.py's handler exactly — see its
            # comment for the full rationale and the fence that pins this shape,
            # `tests/unit/platform/test_alert_metrics_fence.py::
            # test_every_graph_invocation_in_src_counts_agent_errors`).
            record_agent_error(agent="beatriz", error_type=classify_agent_error_type(exc))
            raise
        business_key = result.get("business_key") or _business_key(state)
        return HandlerOutput(
            output_ref=f"process://{business_key}",
            meta={
                # One of exactly two `Desfecho` tokens — never a verdict, never an accusation.
                "desfecho": str(result.get("desfecho", "")),
                "error": str(result.get("error") or ""),
                # COUNTS only: the gap notes themselves are bounded class tokens, but forwarding
                # them would still be forwarding dossier content over the seam.
                "gather_notes_count": str(len(result.get("gather_notes") or [])),
                "evidencia_normalizada_count": str(len(result.get("evidencia_normalizada") or [])),
            },
        )

    return handler

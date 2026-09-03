"""Fernando as A2A delegation TARGET (`arrears.followup`) — GAP 11.7 (Lucas -> Fernando).

`spec/agents/fernando/agent.yaml` already declares `accepted_task_types: [arrears.followup]`
and its own comment names the intended origin exactly: "Lucas->Fernando `arrears.followup`
(phase3-plan §4 — reconciliacao R9 sobre #58)... O wiring inbound (delegation.py /
make_fernando_handler) esta agora em escopo (W-R9)." `docs/processes/contracts/
SP-OP-INADIMPLENCIA-001.md`'s own Pendencias section claims this was already shipped ("GAP-INAD-6,
PR #134: `prepare_dossier` delega `arrears.followup` a Fernando via A2A real") — re-checked this
session (GAP 11.7) and found FALSE against the code as it stands: `tools/workers/inadimplencia.py`'s
`prepare_dossier` builds a dict locally and calls nothing in `maezo.a2a`. This module is the
missing TARGET half, mirroring the LIVE Rafael/Carolina/Andre exemplars end to end
(`agents/carolina/delegation.py` is the closest structural twin — inadimplencia case facts
instead of credentialing ones, same PHI/idempotency/HandlerOutput shape):

- ORIGIN side (`delegate_arrears_followup` / `build_arrears_followup_envelope`): what a future
  caller — the `operadora.inadimplencia.prepare_dossier` worker
  (`tools/workers/inadimplencia.py`), from INSIDE a running SP-OP-INADIMPLENCIA-001 instance —
  would call to actually originate the delegation. **NOT WIRED from that worker in this change**:
  `tools/workers/` is outside this work package's editable surface (hard constraint) — the
  origin function is built and tested here, ready for that one-line call site addition, but the
  call site itself is the explicit STOP boundary this gap-closure session reports.
- TARGET side (`state_from_envelope` + `make_fernando_handler`): the handler compiles Fernando's
  REAL graph (`agents.fernando.graph.build(config)`, fail-closed on missing `inference`/`dmn`/
  `cibseven`/`audit_sink`) and runs it with envelope-materialized state — this half IS complete
  and tested, and is registered with the SAME readiness-check composition Rafael/Carolina/Andre
  already use (`runtime.agent_runtime.a2a_composition`).

Idempotency (Guard 4): `task_id` IS the SP-OP-INADIMPLENCIA-001 business key
(`fernando.graph._business_key`'s own derivation — `INAD-{tenant}-{numero_contrato}`, falling
back to `matricula_beneficiario` when there is no contract number, exactly mirroring the
contract's documented BUSINESS KEY variant) — an engine re-delivery of the external task replays
the SAME delegation result without re-running Fernando, and his own `start_process` is
business-key idempotent anyway.

INPUT-BOUNDARY GATE: mirrors `carolina/delegation.py`'s posture exactly. Fernando's graph has no
`new_fernando_state` constructor (unlike Rafael's `new_rafael_state`) — his gate is
`graph._CALLER_INPUT_FIELDS` (added this session alongside this module, GAP 11.7) +
`receive`'s own output-field reset (`_output_field_resets()`, pre-existing defense in depth).
CORRECTED (verifier finding, re-checked): `state_from_envelope` does NOT raise on an unknown
`payload_meta` key — it never reads one in the first place. `raw` is built EXCLUSIVELY by
copying named keys off `_STRING_META_KEYS`/`_INTEGER_META_KEYS`/`_BOOLEAN_META_KEYS` (each
already a subset of `_CALLER_INPUT_FIELDS` by construction), so an out-of-allowlist
`payload_meta` entry is silently DROPPED by omission — never inspected, never copied, never
seen. That is the real, fail-closed mechanism: unrecognized producer noise cannot reach the
state at all. The `unknown = sorted(...)` check at the end of the function is a separate,
currently-unreachable STRUCTURAL guard (`# pragma: no cover`, identical to Carolina's own) —
defense-in-depth against a FUTURE regression (someone adding a key to one of the three
`_..._META_KEYS` tuples without also adding it to `_CALLER_INPUT_FIELDS`), not a live check
against a malicious/malformed envelope's extra `payload_meta` fields.

WHAT NEVER RIDES THIS SEAM (PHI/ADR-0006): `beneficiario_pseudo_id`/`to_hash` (WhatsApp-turn
identity — this delegation always sets `canal="a2a"`, so Fernando's own `notify()` WhatsApp
branch never fires on this path; sending a beneficiary message, if warranted, is the human's or
a SEPARATE turn's job, never this dossier-preparation handoff's) and `competencias_em_aberto`/
`documentos_refs` (list-shaped facts — `payload_meta` is a strict `Mapping[str, str]`, mirroring
Carolina's own documented exclusion of her list-shaped `documentos_refs`; the dossier degrades
gracefully without them, exactly as her docstring says hers does).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, cast

from maezo.a2a import Budget, DelegationEnvelope, HandlerOutput

from .graph import (
    _CALLER_INPUT_FIELDS,
    FernandoState,
    _business_key,
    build,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from maezo.a2a import DelegationDispatcher, DelegationResult, EnvelopeSigner
    from maezo.runtime.inference import InferenceProvider
    from maezo.tools.mcp_cibseven.transport import AuditStartSink, CibSevenTransport
    from maezo.tools.workers.dmn_transport import DmnTransport

    from .graph import WhatsAppSender

# Task type delegated to Fernando (must match his agent.yaml's accepted_task_types).
TASK_TYPE_ARREARS_FOLLOWUP = "arrears.followup"

# The delegation ORIGINATES in the worker runtime (`operadora.inadimplencia.prepare_dossier`,
# NOT WIRED from there yet — see module docstring), not in an agent graph — the dispatcher
# validates only the TARGET's Card, so the origin id is a stable, self-describing worker identity
# (audited as `agent_id` on the delegation chain link). Mirrors `carolina/delegation.py`'s
# `ORIGIN_WORKER` naming convention exactly.
ORIGIN_WORKER = "inadimplencia-worker"
TARGET_AGENT = "fernando"

# Default budget for a dossier/follow-up delegation chain (per the Helena->Rafael exemplar).
_DEFAULT_BUDGET = Budget(tokens=64, time_ms=60_000, cost_per_hop=1)

# Wall-clock in-flight horizon stamped as `deadline` on a SIGNED envelope (ADR-0039 §4.3.1) — same
# 6h rationale as `agents/helena/delegation.py::_SIGNED_ENVELOPE_TTL` / `carolina/delegation.py`.
_SIGNED_ENVELOPE_TTL = timedelta(hours=6)

# Non-PHI STRING keys forwarded in `payload_meta` (bounded enums / ISO dates / the `intencao`
# journey selector — `matricula_beneficiario` is a PSEUDONYMIZED enrollment key per `graph.py`'s
# own `FernandoState` docstring, already minted directly into the engine business key elsewhere
# in this codebase, so it travels the same way `prestador_id` does on the Carolina edge).
_STRING_META_KEYS = (
    "intencao",
    "numero_contrato",
    "matricula_beneficiario",
    "tipo_plano",
    "origem_solicitacao",
    "data_solicitacao_iso",
)

# Worker-pre-resolved integer facts (module docstring's J2 invariant: Fernando CONSUMES these,
# never computes them) forwarded as decimal strings — `payload_meta` is `Mapping[str, str]`.
_INTEGER_META_KEYS = ("meses_inadimplencia", "valor_total_devido_cents")

# Worker-pre-resolved boolean facts forwarded in `payload_meta` (never PHI).
_BOOLEAN_META_KEYS = (
    "dentro_periodo_minimo",
    "notificacao_previa_feita",
    "dentro_janela_purga",
    "ja_em_rescisao_cancel",
)


def arrears_followup_task_id(
    tenant: str, *, numero_contrato: str | None = None, matricula_beneficiario: str | None = None
) -> str:
    """Idempotent `task_id` == the SP-OP-INADIMPLENCIA-001 business key (dispatcher Guard 4).

    Delegates to `fernando.graph._business_key` itself (never reimplements the fallback/minting
    logic here) so the two derivations can never drift — a plain dict with just the three
    identity fields satisfies `_business_key`'s `.get(...)` reads structurally.
    """
    stub = cast(
        FernandoState,
        {
            "tenant_id": tenant,
            "numero_contrato": numero_contrato or "",
            "matricula_beneficiario": matricula_beneficiario or "",
        },
    )
    return _business_key(stub)


def build_arrears_followup_envelope(
    *,
    tenant: str,
    case_meta: dict[str, Any],
    numero_contrato: str | None = None,
    matricula_beneficiario: str | None = None,
    budget: Budget | None = None,
    signer: EnvelopeSigner | None = None,
) -> DelegationEnvelope:
    """Build the worker->Fernando root envelope for an arrears follow-up/dossier request.

    - `payload_ref` is the case's process reference (`process://INAD-...`) — a non-PHI anchor.
    - `case_meta`: the SP-OP-INADIMPLENCIA-001 process variables available at
      `ST_PrepareDossier`-equivalent time. Serialized into `payload_meta` (`Mapping[str, str]`)
      through the strict allowlists above — unknown keys are silently NOT forwarded
      (allowlist, not blocklist, mirrors Carolina's `_build_payload_meta`).

    Applies the anti-loop guards at the root itself (origin != target, chain <= max_hops, budget).

    SIGNING (ADR-0039 §4.4, leg E3): with `signer` present the envelope carries an explicit
    `deadline` and is HMAC-signed over its v2 canonical digest. Absent -> unsigned (dev path).
    """
    task_id = arrears_followup_task_id(
        tenant, numero_contrato=numero_contrato, matricula_beneficiario=matricula_beneficiario
    )
    envelope = DelegationEnvelope.root(
        task_id=task_id,
        task_type=TASK_TYPE_ARREARS_FOLLOWUP,
        origin=ORIGIN_WORKER,
        target=TARGET_AGENT,
        tenant=tenant,
        budget=budget or _DEFAULT_BUDGET,
        payload_ref=f"process://{task_id}",
        deadline=(datetime.now(tz=UTC) + _SIGNED_ENVELOPE_TTL) if signer is not None else None,
        payload_meta=_build_payload_meta(numero_contrato, matricula_beneficiario, case_meta),
    )
    return signer.sign(envelope) if signer is not None else envelope


def _build_payload_meta(
    numero_contrato: str | None, matricula_beneficiario: str | None, case_meta: dict[str, Any]
) -> dict[str, str]:
    """Serialize the case into `payload_meta` (`Mapping[str, str]`, strict non-PHI allowlist)."""
    meta: dict[str, str] = {}
    if numero_contrato:
        meta["numero_contrato"] = numero_contrato
    if matricula_beneficiario:
        meta["matricula_beneficiario"] = matricula_beneficiario
    for key in _STRING_META_KEYS:
        if key in {"numero_contrato", "matricula_beneficiario"}:
            continue  # already handled above (explicit params, not case_meta-sourced)
        if case_meta.get(key):
            meta[key] = str(case_meta[key])
    for key in _INTEGER_META_KEYS:
        if key in case_meta:
            meta[key] = str(int(case_meta[key]))
    for key in _BOOLEAN_META_KEYS:
        if key in case_meta:
            meta[key] = "true" if bool(case_meta[key]) else "false"
    return meta


async def delegate_arrears_followup(
    dispatcher: DelegationDispatcher,
    *,
    tenant: str,
    case_meta: dict[str, Any],
    numero_contrato: str | None = None,
    matricula_beneficiario: str | None = None,
    budget: Budget | None = None,
    signer: EnvelopeSigner | None = None,
) -> DelegationResult:
    """Originate and dispatch the worker->Fernando delegation. Idempotent by `task_id`.

    NOT CALLED from `tools/workers/inadimplencia.py` yet — see module docstring's STOP boundary.
    Returns the dispatcher's structured `DelegationResult` (success with `output_ref` = the
    case's process reference + `meta` = Fernando's bounded routing summary, or a structured
    rejection — never a raise out of the dispatcher). On re-delivery of the same `task_id`,
    `idempotent_replay=True` and the handler does NOT run again.

    SIGNING (ADR-0039 §4.4): `signer` defaults to the edge signer carried by `dispatcher`
    (`origin_signer_of`), so a future live worker call site signs without any per-worker wiring
    change.
    """
    from maezo.a2a.dispatcher import origin_signer_of  # noqa: PLC0415 - mirrors carolina/delegation.py

    resolved_signer = signer if signer is not None else origin_signer_of(dispatcher)
    envelope = build_arrears_followup_envelope(
        tenant=tenant,
        case_meta=case_meta,
        numero_contrato=numero_contrato,
        matricula_beneficiario=matricula_beneficiario,
        budget=budget,
        signer=resolved_signer,
    )
    return await dispatcher.delegate(envelope)


# --- Target side (mirrors carolina/delegation.py) -----------------------------------------------


def _as_bool(value: Any) -> bool:
    """`payload_meta` is `Mapping[str, str]` (A2A) — normalize "true"/"false" strings to bool."""
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"true", "1", "yes", "sim"}


def _as_int(value: Any) -> int:
    """Parse a `payload_meta` decimal string back to `int` — fails closed to 0 (never a raise
    that would drop the whole envelope over one malformed numeric field; `graph.py`'s own DMN
    inputs coerce with `int(... or 0)` at every call site, the same conservative default)."""
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return 0


def state_from_envelope(envelope: DelegationEnvelope) -> FernandoState:
    """Materialize Fernando's initial graph state from a delegation envelope.

    Case identifiers/facts come from `payload_meta` (never PHI); `tenant` comes from the
    envelope (ADR-0004). `canal` is ALWAYS `"a2a"` here (never `payload_meta`-sourced) — see
    module docstring's PHI note on why this keeps Fernando's `notify()` WhatsApp branch from
    ever firing on this path. Enforces Fernando's input boundary (`graph._CALLER_INPUT_FIELDS`)
    the way `state_from_envelope` does on the Carolina/Rafael edges — but by ALLOWLIST-BY-
    CONSTRUCTION, not by a raise: `raw` only ever copies named keys off the three `_..._META_
    KEYS` tuples (each a subset of `_CALLER_INPUT_FIELDS`), so an unknown/out-of-allowlist
    `payload_meta` key is silently DROPPED — never read, never seen — rather than triggering an
    exception. The `unknown` check below is a structural, currently-unreachable regression
    guard (`# pragma: no cover`, mirrors Carolina's identical one), not a live rejection of a
    malformed envelope's extra fields; his `receive` node still re-sanitizes every output-only
    field on top of this as defense in depth (`_output_field_resets()`).

    Fails closed on a missing identity: Fernando's graph derives the idempotent business key
    from `numero_contrato`/`matricula_beneficiario`, and neither `receive` nor `start_process`
    has a degenerate-key short-circuit — a blank identity here is a producer bug, never a case.
    """
    meta = dict(envelope.payload_meta)
    numero_contrato = str(meta.get("numero_contrato", "")).strip()
    matricula_beneficiario = str(meta.get("matricula_beneficiario", "")).strip()
    if not numero_contrato and not matricula_beneficiario:
        raise ValueError(
            "arrears.followup envelope has no numero_contrato/matricula_beneficiario in "
            "payload_meta — the INAD business key cannot be derived (producer bug; the "
            "originating worker validates identifiers before delegating)"
        )
    raw: dict[str, Any] = {"tenant_id": envelope.tenant, "canal": "a2a"}
    if numero_contrato:
        raw["numero_contrato"] = numero_contrato
    if matricula_beneficiario:
        raw["matricula_beneficiario"] = matricula_beneficiario
    for key in _STRING_META_KEYS:
        if key in {"numero_contrato", "matricula_beneficiario"}:
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
            f"state_from_envelope produced non-input keys for Fernando: {unknown} — only "
            "graph._CALLER_INPUT_FIELDS may be seeded by a delegation seam"
        )
    return cast(FernandoState, raw)


def make_fernando_handler(
    inference: InferenceProvider,
    *,
    dmn: DmnTransport,
    cibseven: CibSevenTransport,
    audit_sink: AuditStartSink,
    whatsapp: WhatsAppSender,
    agent_version: str = "fernando@v0",
) -> Callable[[DelegationEnvelope], Awaitable[HandlerOutput]]:
    """Build Fernando's A2A handler (delegation target). A future worker-runtime composition
    root registers it with the dispatcher (`handlers={"fernando": ...}`), mirroring
    `runtime.agent_runtime.a2a_composition`'s existing `handlers={"carolina": ..., "andre": ...}`
    wiring exactly.

    Compiles the REAL Fernando graph via `fernando.graph.build(config)` (the same fail-closed
    contract every other caller goes through — `inference`/`dmn`/`cibseven`/`audit_sink`/
    `whatsapp` are ALL required, unlike Rafael's optional `fhir`: `graph.py::build` fail-closes
    without a `whatsapp` sender because `notify()` is a real node in Fernando's graph, even
    though `state_from_envelope` always sets `canal="a2a"` — which makes `notify()`'s WhatsApp
    branch structurally unreachable on THIS path, exactly the same "declare every seam, some
    branches never touch it" posture every other `build(config)` caller in this repo already
    follows) and invokes it with the envelope-materialized state. `output_ref` is
    SP-OP-INADIMPLENCIA-001's business key reference (auditable, never PHI); the dossier CONTENT
    stays in Fernando's own engine variables (via his idempotent `start_process`) and is never
    forwarded over this seam — mirrors Carolina's own guardrail, and Fernando's own L0-hard
    invariant that he never decides/communicates an adverse outcome himself.
    """
    compiled = build(
        {
            "inference": inference,
            "dmn": dmn,
            "cibseven": cibseven,
            "audit_sink": audit_sink,
            "whatsapp": whatsapp,
            "agent_version": agent_version,
        }
    ).compile()

    async def handler(envelope: DelegationEnvelope) -> HandlerOutput:
        state = state_from_envelope(envelope)
        try:
            result: dict[str, Any] = await compiled.ainvoke(state)
        except Exception:
            # ALERTS-WITHOUT-METRICS-a (mirrors carolina/delegation.py's handler exactly — see
            # its comment for the full rationale and the fence that pins this shape,
            # `tests/unit/platform/test_alert_metrics_fence.py::
            # test_every_graph_invocation_in_src_counts_agent_errors`).
            from maezo.platform.observability import record_agent_error  # noqa: PLC0415

            record_agent_error()
            raise
        business_key = result.get("business_key") or _business_key(state)
        return HandlerOutput(
            output_ref=f"process://{business_key}",
            meta={
                "route": str(result.get("route") or "escalate"),
                "desfecho": str(result.get("desfecho", "")),
                "motivo_humano": str(result.get("motivo_humano") or ""),
                "process_started": str(result.get("process_started", False)),
            },
        )

    return handler

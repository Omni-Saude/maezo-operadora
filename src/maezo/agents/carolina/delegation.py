"""Carolina as A2A delegation TARGET + the CRED-worker ORIGIN builder (`credentialing.analyze`).

DL-0033 declared the `operadora.cred.prepare_dossier` worker a LOCAL STUB and deferred "a
INTEGRACAO A2A REAL (`DelegationEnvelope` + `DelegationDispatcher.delegate` ate Carolina/Andre)"
to a future full-A2A wiring task. This module is that wiring's Carolina half, mirroring the LIVE
Helena->Rafael exemplar end to end:

- ORIGIN side (mirrors `agents/helena/delegation.py`): `build_cred_dossier_envelope` /
  `delegate_cred_dossier`, called by the `operadora.cred.prepare_dossier` worker
  (`tools/workers/credenciamento.py`) from INSIDE a running SP-OP-CRED-001 instance
  (`ST_PrepareDossierDescred` / `ST_PrepareDossierCred`). The origin is the WORKER
  (`credenciamento-worker`), not an agent — the dispatcher validates only the TARGET's Agent
  Card, and `spec/agents/carolina/agent.yaml` already declares
  `accepted_task_types: [credentialing.analyze]`.
- TARGET side (mirrors `agents/rafael/delegation.py`): `state_from_envelope` +
  `make_carolina_handler` — the handler compiles Carolina's REAL graph
  (`agents.carolina.graph.build(config)`, fail-closed on missing `inference`/`dmn`/`cibseven`/
  `audit_sink`) and runs it with envelope-materialized state.

Idempotency (Guard 4): the `task_id` IS the SP-OP-CRED-001 business key
(`CRED-{tenant}-{prestador}[-{protocolo}]`) — an engine re-delivery of the external task replays
the SAME delegation result without re-running Carolina, and her own `start_process` is
business-key idempotent anyway (the already-running instance is returned untouched).

INPUT-BOUNDARY GATE: Carolina's graph has no `new_carolina_state` constructor (unlike Rafael's
`new_rafael_state`); her gate is `_CALLER_INPUT_FIELDS` + `receive`'s output-field sanitization.
`state_from_envelope` therefore enforces the SAME allowlist here — an unknown/output-only key
raises loudly (a producer bug, never silently tolerated), exactly the posture
`new_rafael_state` takes on the Rafael edge.

HARD GUARDRAIL (L1, `carolina/graph.py::_build_dossier` — re-enforced here): the dossier NEVER
carries an adverse decision (`decisao_credenciamento`/`decisao_descredenciamento` structurally
`None`), and this handler does not forward the dossier body at all. `HandlerOutput.output_ref` is
always `process://{business_key}` (a reference); `HandlerOutput.meta` carries ONLY bounded,
non-PHI routing class tokens (route/desfecho/motivo/grupo) — the dossier "instrui, nao decide"
(contract SP-OP-CRED-001; posture recorded as DL-0037).

PHI (ADR-0006): `payload_meta` is a strict non-PHI allowlist — cadastral identifiers
(`prestador_id` is PJ/PF registry data, "nao PHI do beneficiario" per the contract), bounded
enums, ISO dates and worker-pre-resolved booleans. Free text (`motivo_informado`) and
`documentos_refs` are DELIBERATELY excluded from the envelope: the contract says the motivo
carries no beneficiary PHI, but a free-text field is not mechanically bounded, so it never rides
the A2A seam (the dossier degrades gracefully without it).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, cast

from maezo.a2a import Budget, DelegationEnvelope, HandlerOutput
from maezo.a2a.dispatcher import origin_signer_of
from maezo.runtime.metrics import classify_agent_error_type
from maezo.runtime.start_outcome import StartProcessFailedError

from .graph import (
    _CALLER_INPUT_FIELDS,
    CarolinaState,
    SummaryReader,
    _business_key,
    build,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from maezo.a2a import DelegationDispatcher, DelegationResult, EnvelopeSigner
    from maezo.runtime.inference import InferenceProvider
    from maezo.tools.mcp_cibseven.transport import AuditStartSink, CibSevenTransport
    from maezo.tools.workers.dmn_transport import DmnTransport

# Task type delegated to Carolina (must match her agent.yaml's accepted_task_types).
TASK_TYPE_CRED_DOSSIER = "credentialing.analyze"

# The delegation ORIGINATES in the worker runtime (`operadora.cred.prepare_dossier`), not in an
# agent graph — the dispatcher validates only the TARGET's Card, so the origin id is a stable,
# self-describing worker identity (audited as `agent_id` on the delegation chain link).
ORIGIN_WORKER = "credenciamento-worker"
TARGET_AGENT = "carolina"

# Default budget for a dossier delegation chain (per the Helena->Rafael exemplar).
_DEFAULT_BUDGET = Budget(tokens=64, time_ms=60_000, cost_per_hop=1)

# Wall-clock in-flight horizon stamped as `deadline` on a SIGNED envelope (ADR-0039 §4.3.1). 6 hours
# — see agents/helena/delegation.py::_SIGNED_ENVELOPE_TTL for the shared rationale (far under the
# 7-day max-signature-age, so max-age dominates key-purge timing). Unsigned envelopes keep None.
_SIGNED_ENVELOPE_TTL = timedelta(hours=6)

# Non-PHI STRING keys forwarded in `payload_meta` (cadastral ids / bounded enums / ISO dates —
# see the module docstring's PHI note for what is deliberately excluded).
_STRING_META_KEYS = (
    "direcao",
    "tipo_prestador",
    "origem_solicitacao",
    "data_solicitacao_iso",
    "regiao_saude",
    "especialidade",
)

# Worker-pre-resolved boolean facts forwarded in `payload_meta` (never PHI).
_BOOLEAN_META_KEYS = (
    "licenca_valida",
    "documentacao_completa",
    "dentro_criterios_rede",
    "notificacao_previa_feita",
    "substituto_equivalente_identificado",
    "tem_beneficiarios_vinculados",
    "indicio_irregularidade_sinalizado",
)


def cred_task_id(tenant: str, prestador_id: str, protocolo_cred: str | None = None) -> str:
    """Idempotent `task_id` == the SP-OP-CRED-001 business key (dispatcher Guard 4).

    `CRED-{tenant}-{prestador}` or, with a cycle protocol, `CRED-{tenant}-{prestador}-{protocolo}`
    — the SAME derivation as `carolina.graph._business_key` (contract §business key), so the
    delegation's idempotency is keyed to the process case itself.
    """
    if protocolo_cred:
        return f"CRED-{tenant}-{prestador_id}-{protocolo_cred}"
    return f"CRED-{tenant}-{prestador_id}"


def build_cred_dossier_envelope(
    *,
    tenant: str,
    prestador_id: str,
    case_meta: dict[str, Any],
    protocolo_cred: str | None = None,
    budget: Budget | None = None,
    signer: EnvelopeSigner | None = None,
) -> DelegationEnvelope:
    """Build the worker->Carolina root envelope for the (des)credenciamento analysis dossier.

    - `payload_ref` is the case's process reference (`process://CRED-...`) — a non-PHI anchor
      (the envelope constructor additionally rejects any PHI-looking reference).
    - `case_meta`: the process variables available at `ST_PrepareDossier*` time. Serialized into
      `payload_meta` (`Mapping[str, str]`) through the strict allowlist above — unknown keys are
      silently NOT forwarded (allowlist, not blocklist).

    Applies the anti-loop guards at the root itself (origin != target, chain <= max_hops, budget).

    SIGNING (ADR-0039 §4.4, leg E3): with `signer` present the envelope carries an explicit
    `deadline` (§4.3.1) and is HMAC-signed over its v2 canonical digest — so `payload_meta_hash`
    binds `prestador_id` (the CIB business key Carolina derives). Absent -> unsigned (dev path).
    """
    task_id = cred_task_id(tenant, prestador_id, protocolo_cred)
    envelope = DelegationEnvelope.root(
        task_id=task_id,
        task_type=TASK_TYPE_CRED_DOSSIER,
        origin=ORIGIN_WORKER,
        target=TARGET_AGENT,
        tenant=tenant,
        budget=budget or _DEFAULT_BUDGET,
        payload_ref=f"process://{task_id}",
        deadline=(datetime.now(tz=UTC) + _SIGNED_ENVELOPE_TTL) if signer is not None else None,
        payload_meta=_build_payload_meta(prestador_id, protocolo_cred, case_meta),
    )
    return signer.sign(envelope) if signer is not None else envelope


def _build_payload_meta(
    prestador_id: str, protocolo_cred: str | None, case_meta: dict[str, Any]
) -> dict[str, str]:
    """Serialize the case into `payload_meta` (`Mapping[str, str]`, strict non-PHI allowlist)."""
    meta: dict[str, str] = {"prestador_id": prestador_id}
    if protocolo_cred:
        meta["protocolo_cred"] = protocolo_cred
    for key in _STRING_META_KEYS:
        if case_meta.get(key):
            meta[key] = str(case_meta[key])
    for key in _BOOLEAN_META_KEYS:
        if key in case_meta:
            meta[key] = "true" if bool(case_meta[key]) else "false"
    return meta


async def delegate_cred_dossier(
    dispatcher: DelegationDispatcher,
    *,
    tenant: str,
    prestador_id: str,
    case_meta: dict[str, Any],
    protocolo_cred: str | None = None,
    budget: Budget | None = None,
    signer: EnvelopeSigner | None = None,
) -> DelegationResult:
    """Originate and dispatch the worker->Carolina delegation. Idempotent by `task_id`.

    Returns the dispatcher's structured `DelegationResult` (success with `output_ref` = the case's
    process reference + `meta` = Carolina's bounded routing summary, or a structured rejection —
    never a raise out of the dispatcher for a TERMINAL handler failure). On re-delivery of the
    same `task_id`, `idempotent_replay=True` and the handler does NOT run again.

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
    (`origin_signer_of`), so the LIVE worker path signs without any per-worker wiring change.
    """
    resolved_signer = signer if signer is not None else origin_signer_of(dispatcher)
    envelope = build_cred_dossier_envelope(
        tenant=tenant,
        prestador_id=prestador_id,
        case_meta=case_meta,
        protocolo_cred=protocolo_cred,
        budget=budget,
        signer=resolved_signer,
    )
    return await dispatcher.delegate(envelope)


# --- Target side (mirrors rafael/delegation.py) -------------------------------------------------


def _as_bool(value: Any) -> bool:
    """`payload_meta` is `Mapping[str, str]` (A2A) — normalize "true"/"false" strings to bool."""
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"true", "1", "yes", "sim"}


def state_from_envelope(envelope: DelegationEnvelope) -> CarolinaState:
    """Materialize Carolina's initial graph state from a delegation envelope.

    Case identifiers/facts come from `payload_meta` (never PHI); `tenant` comes from the envelope
    (ADR-0004). Enforces Carolina's input boundary (`graph._CALLER_INPUT_FIELDS`) the way
    `new_rafael_state` does on the Rafael edge: an unknown/output-only key raises loudly instead
    of silently entering the state (her `receive` node then re-sanitizes output-only fields as
    defense in depth).

    Fails closed on a missing `prestador_id`: Carolina's graph derives the idempotent business
    key from it, and her `start_process` node has no degenerate-key short-circuit — a blank
    prestador here is a producer bug, never a case.
    """
    meta = dict(envelope.payload_meta)
    prestador_id = str(meta.get("prestador_id", "")).strip()
    if not prestador_id:
        raise ValueError(
            "credentialing.analyze envelope has no prestador_id in payload_meta — the CRED "
            "business key cannot be derived (producer bug; the originating worker validates "
            "identifiers before delegating)"
        )
    raw: dict[str, Any] = {
        "tenant_id": envelope.tenant,
        "canal": "a2a",
        "prestador_id": prestador_id,
    }
    if meta.get("protocolo_cred"):
        raw["protocolo_cred"] = meta["protocolo_cred"]
    for key in _STRING_META_KEYS:
        if meta.get(key):
            raw[key] = meta[key]
    for key in _BOOLEAN_META_KEYS:
        if key in meta:
            raw[key] = _as_bool(meta[key])

    unknown = sorted(k for k in raw if k not in _CALLER_INPUT_FIELDS)
    if unknown:  # pragma: no cover — structural guard; raw is built from the allowlists above.
        raise ValueError(
            f"state_from_envelope produced non-input keys for Carolina: {unknown} — only "
            "graph._CALLER_INPUT_FIELDS may be seeded by a delegation seam"
        )
    return cast("CarolinaState", raw)


def make_carolina_handler(
    inference: InferenceProvider,
    *,
    dmn: DmnTransport,
    cibseven: CibSevenTransport,
    audit_sink: AuditStartSink,
    fhir: SummaryReader | None = None,
    agent_version: str = "carolina@v0",
) -> Callable[[DelegationEnvelope], Awaitable[HandlerOutput]]:
    """Build Carolina's A2A handler (delegation target). The worker-runtime composition root
    registers it with the dispatcher (`handlers={"carolina": ...}`, see
    `runtime.agent_runtime.a2a_composition.build_dossier_delegation_dispatcher`).

    Compiles the REAL Carolina graph via `carolina.graph.build(config)` (the same fail-closed
    contract every other caller goes through — `inference`/`dmn`/`cibseven`/`audit_sink` are
    REQUIRED, `fhir` is optional) and invokes it with the envelope-materialized state. The
    `output_ref` is SP-OP-CRED-001's business key reference (auditable, never PHI); the dossier
    CONTENT stays in Carolina's own engine variables (`dossie_carolina`, via her idempotent
    `start_process`) and is never forwarded over this seam.
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
            # ALERTS-WITHOUT-METRICS-a: a delegated turn that raised IS a failed agent turn, and
            # `maezo_agent_errors_total` is what `MaezoAgentCrashLoop` reads. Placed at the
            # `ainvoke` seam — the structural entry to a graph run — and NOT anywhere in carolina's
            # business logic, which is why it is one line here rather than a rule each node has to
            # remember. `asyncio.CancelledError` is excluded (BaseException): a drained delegation
            # is not a failed agent. Enumerated and pinned by
            # `tests/unit/platform/test_alert_metrics_fence.py::
            # test_every_graph_invocation_in_src_counts_agent_errors`.
            from maezo.platform.observability import record_agent_error

            record_agent_error(agent="carolina", error_type=classify_agent_error_type(exc))
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
                f"carolina nao conseguiu iniciar SP-OP-CRED-001 "
                f"(business_key={business_key!r}): o turno NAO foi concluido"
            )
        # GUARDRAIL: output_ref is the process business key — never a (des)credenciamento
        # decision. The dossier (whose `decisao_credenciamento`/`decisao_descredenciamento` are
        # structurally always None, `graph.py`'s own L1 guardrail) is deliberately NOT forwarded
        # here at all — meta carries ONLY bounded routing class tokens.
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

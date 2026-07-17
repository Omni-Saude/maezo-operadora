"""Worker: `operadora.events.publish` — the generic domain-event Kafka publisher.

Ported from the v1 donor (READ-ONLY `Maezo-Healthcare-Plan`,
`src/maezo/tools/workers/phase0.py::make_publish_event_handler` +
`register_phase0_workers`) — T3.1 R2 (runtime-spine-engineer #5 charter). ROOT CAUSE (R1-proven):
v1's `phase0.py` served this ONE generic topic for every SP-OP-* BPMN's `ST_Publish*` service
tasks; v2's 16-family `register_all_workers` (`bootstrap.py`) never ported it. Every ported
process therefore stalls at its FIRST `operadora.events.publish` service task — "no handler
registered for topic 'operadora.events.publish'" — live-confirmed by the T3.1 phase-1 integration
suites (`tests/integration/processes/test_sp_op_{escalation,auth,cancel}_001.py`), which trace 46
of their 53 `strict-xfail` markers to exactly this gap.

WHY A RAW `harness.register()` HANDLER, NOT a `FunctionWorker` (deliberately breaks from the
other 16 modules' ADR-0026 dict-first convention): the donor's payload construction reads
`task.business_key`, `task.process_instance_id`, and `task.topic` — `ExternalTask` METADATA, not
process variables. A `FunctionWorker`/`WorkerBase`'s `execute(variables: dict)` boundary only ever
sees `task.variables` (`harness.py`'s `register_worker._adapter` calls
`_worker.run(task.variables)`) — a dict-first function has no way to reconstruct
business_key/process_instance_id short of the harness leaking them into the variables dict (a
change to the shared dispatch adapter every other module goes through — riskier and out of scope
here). `WorkerHarness` already documents and supports a second registration path for exactly this
shape (`harness.py` module docstring: "`harness.register(topic, handler)  # raw async handler`")
— the SAME mechanism the donor used. This module mirrors that shape rather than force-fitting
`FunctionWorker`.

kafka=None decision (fail-closed, evidenced — see the PR body for the full writeup):

  v1 ALWAYS had a concrete `KafkaPublisher` — `register_phase0_workers(harness, kafka:
  KafkaPublisher, ...)` takes `kafka` as a REQUIRED positional argument, never `Optional`. The
  only "no real Kafka" v1 path was `LoggingKafkaPublisher`, itself a working *publisher* (logs,
  then returns success) — v1 never had a `kafka=None` branch to port.

  v2's `register_<domain>_workers(harness, kafka: KafkaPublisher | None = None, **seams)` contract
  is `Optional` for every one of the other 16 modules, and NO real Kafka producer is wired
  anywhere in this build yet (`worker_runtime/service.py`'s own `kafka_ready` readiness check says
  so explicitly). This handler cannot reuse "always call kafka.publish" when `kafka` is `None` —
  it would raise `AttributeError: 'NoneType' object has no attribute 'publish'` on every SP-OP-*
  instance, trading one stall (missing handler) for another (crashing handler).

  Decision rubric applied: does a downstream BPMN branch on this worker's own output variables?
  `event_published`/`event_topic` are the ONLY two output variables this handler returns.
  `grep -c 'event_published'` across every `spec/processes/bpmn/**.bpmn` returns 0 — every
  `ST_Publish*` service task's outgoing sequence flow is unconditional (mirrors the donor's own
  docstring: "NENHUM gateway BPMN roteia sobre estas variaveis... fluxo incondicional"). A
  `published=false` marker therefore changes NO downstream BPMN branch.

  Consequence: with `kafka=None`, this handler NEVER fabricates `event_published=True`. It logs
  LOUDLY (event topic + business key + process instance id + the payload's KEY NAMES only — never
  values, since a var like `numero_guia_tiss` is a sensitive business identifier even though it
  isn't in `PHI_PROCESS_VARS`) and completes with an explicit `event_published=False`. The
  external task itself still completes (not fails/incidents) because no BPMN branch depends on
  the marker and every other SP-OP-* worker downstream of a publish step is UNCHANGED by this —
  the alternative (raising, forcing an engine incident on a topic no gateway reads) would newly
  block flows a fabricated-success shortcut is explicitly forbidden from unblocking either.
  Fail-closed here means "the gap stays loud in every log line and every output variable", not
  "block the process for a signal nothing consumes".

PHI/SLA-metrics scope note (NOT fabricated here): the donor's handler also threaded a
`PhiTextPseudonymizer` (`phi_vars.scrub_phi_vars`) and emitted a `cibseven_user_task_sla_breach_total`
metric (`sla_metrics.emit_user_task_sla_breach`) for the escalation `sla_breached` topic. NEITHER
`phi_vars.py` NOR `sla_metrics.py` exists anywhere on v2 `main` (verified: zero hits for either
module under `src/`) — this is the SAME documented gap `test_sp_op_auth_001.py`'s finding 3 calls
out ("no `phi_vars.REDACTED_PHI`-equivalent egress redaction exists in v2 today"). Fabricating a
new PHI-scrub/SLA-metrics module here would be scope creep beyond "port the missing publish
worker" (the charter's own bpmn_error_topics/kafka=None decisions do not require it) and use an
UNPROVEN `PHI_PROCESS_VARS` allowlist. Practically this is a non-issue for the payload shape: every
`event_payload_vars` list across all 16 BPMNs already excludes free-text/clinical fields (e.g.
`notas_resolucao`, `justificativa_clinica`) at the BPMN-authoring layer — verified by grep, and
exercised by `test_sp_op_escalation_001.py::test_happy_path_resolvido_por_humano`'s
`"notas_resolucao" not in e.payload` assertion — so this handler's plain pass-through of
`event_payload_vars` never leaks those fields even without a Python-side scrub. Flagged here (not
silently dropped) as an open, separately-tracked gap — consistent with the auth suite's own
finding 3.

ans.cron_due anchor (GAP-ANS-1/GAP-ANS-3, ported verbatim from the donor for fidelity): when
`event_type == "ans.cron_due"` (SP-OP-ANS-CRON-001's 5 timer branches, the only BPMN family that
sets this literal), the handler additionally stamps `payload["ans_cron_reference_date_iso"]` — the
UTC date of the instant this serviceTask executes, captured ONCE here and sealed into the
published fact (a BPMN `inputParameter` cannot carry "today"; only a worker can). Harmless
addition for every other topic (guarded by the `event_type` value check) and out of T3.1's own
acceptance scope (SP-OP-ANS-CRON-001 is not one of the 3 flipped families), but keeps this module
a faithful, complete port of the donor function it replaces.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import structlog

from maezo.tools.workers.harness import ExternalTask, WorkerBpmnError

if TYPE_CHECKING:
    from maezo.tools.workers.harness import KafkaPublisher, TaskHandler, WorkerHarness

logger = structlog.get_logger(__name__)
# Stdlib logger mirror (harness.py's own convention, module docstring: "a dual-logging seam ...
# so warnings/errors are never silently lost outside the structlog chain") — the kafka=None loud
# log below is exactly the kind of signal that must show up even where structlog isn't wired.
_stdlib_logger = logging.getLogger(__name__)

# Donor's declared BPMN error (GAP-ESC-5) for the publish-failure boundary-catch opt-in below.
_ERR_EVENT_PUBLISH_FAILED = "ERR_EVENT_PUBLISH_FAILED"

# GAP-ESC-5 (donor `phase0.py`, ported verbatim): SP-OP-ESCALATION-001 is the ONLY BPMN family
# that declares a matching `bpmn:error errorCode="ERR_EVENT_PUBLISH_FAILED"` boundary event
# (`Error_EscPublishFailed` — see
# `spec/processes/bpmn/SP-OP-ESCALATION-001_Escalonamento_Humano_Universal.bpmn`); grep across
# every other `spec/processes/bpmn/*.bpmn` confirms none declares this error. A publish failure on
# any topic NOT in this set propagates the RAW exception — the harness's `handle_failure`
# (engine-computed retry), the UNCHANGED pre-existing behavior for every SP-OP-* this worker is
# newly serving, zero regression on that path.
_ESCALATION_REQUESTED_TOPIC = "agents.events.escalation.requested"
_ESCALATION_SLA_BREACHED_TOPIC = "agents.events.escalation.sla_breached"
_ESCALATION_RESOLVED_TOPIC = "agents.events.escalation.resolved"
_ESCALATION_PROCESS_COMPLETED_TOPIC = "agents.events.process_completed"
_ESCALATION_PUBLISH_BPMN_ERROR_TOPICS = frozenset(
    {
        _ESCALATION_REQUESTED_TOPIC,
        _ESCALATION_SLA_BREACHED_TOPIC,
        _ESCALATION_RESOLVED_TOPIC,
        _ESCALATION_PROCESS_COMPLETED_TOPIC,
    }
)

# res-ans-competencia-sentinel (GAP-ANS-1/GAP-ANS-3) — see module docstring.
_ANS_CRON_DUE_EVENT_TYPE = "ans.cron_due"
_ANS_CRON_REFERENCE_DATE_KEY = "ans_cron_reference_date_iso"


def _parse_payload_vars(raw: Any) -> list[str]:
    """Normalize `event_payload_vars` into a list of variable names.

    Against the real engine, `camunda:inputParameter` delivers this as a comma-separated STRING
    (e.g. "tenant_id,conversation_id,severidade") — iterating the string directly would produce
    1-character keys. Also accepts a list/tuple (alternative/dev/test wiring) unchanged.
    """
    if raw is None:
        return []
    if isinstance(raw, str):
        return [name.strip() for name in raw.split(",") if name.strip()]
    if isinstance(raw, (list, tuple)):
        return [str(name).strip() for name in raw if str(name).strip()]
    return []


def make_publish_event_handler(
    kafka: KafkaPublisher | None,
    *,
    bpmn_error_topics: frozenset[str] = frozenset(),
) -> TaskHandler:
    """Create the handler for `operadora.events.publish`.

    Generic worker: reads `event_topic` + `event_payload_vars` from the task's input variables and
    publishes the resulting payload to `kafka`. Every SP-OP-* BPMN's `ST_Publish*` service tasks
    route through this ONE handler.

    Required input variable:
      event_topic: str          — target Kafka topic.

    Optional input variables:
      event_payload_vars        — names of process variables to copy into the payload. The real
                                   engine always delivers this as a COMMA-SEPARATED STRING (BPMN
                                   `camunda:inputParameter` values are string literals); a
                                   list/tuple is also accepted (dev/test wiring).
      event_fase: str           — when present, becomes `payload["fase"]` (SP-OP-ESCALATION-001:
                                   `sla_breached` carries `fase` in {`ack`, `resolucao`}).
      event_desfecho: str       — when present, becomes `payload["desfecho"]` (e.g.
                                   SP-OP-AUTH-001's `auth.completed`, SP-OP-CANCEL-001's
                                   `cancel.completed`).
      event_tipo_mudanca: str   — when present, becomes `payload["tipo_mudanca"]`
                                   (SP-OP-CRED-001's `cred.network_changed`).
      event_type: str           — when present, becomes `payload["type"]` (the typed envelope for
                                   `operadora.notifications.internal`, e.g. SP-OP-ANS-CRON-001's
                                   `ans.cron_due`).
    These four are BPMN `inputParameter` LITERALS, not process variables — they never appear in
    `event_payload_vars`, mirroring the donor exactly.

    Output variables (loaded on `complete`, exactly once — see module docstring for the
    `kafka=None` decision):
      event_published: bool     — whether the event actually reached the publisher.
      event_topic: str          — echo of the input var (audit trail).
    No BPMN gateway routes on either variable (verified — module docstring); these are
    record/audit-trail, never a routing decision.
    """

    async def handler(task: ExternalTask) -> Mapping[str, Any]:
        event_topic = task.variables.get("event_topic")
        if not event_topic:
            raise WorkerBpmnError(
                "ERR_PUBLISH_MISSING_TOPIC",
                "operadora.events.publish: `event_topic` ausente nas variaveis",
            )

        payload_vars = _parse_payload_vars(task.variables.get("event_payload_vars"))
        payload: dict[str, Any] = {
            "_business_key": task.business_key,
            "_process_instance_id": task.process_instance_id,
            "_worker_topic": task.topic,
        }
        event_fase = task.variables.get("event_fase")
        if event_fase is not None:
            payload["fase"] = event_fase
        event_desfecho = task.variables.get("event_desfecho")
        if event_desfecho is not None:
            payload["desfecho"] = event_desfecho
        event_tipo_mudanca = task.variables.get("event_tipo_mudanca")
        if event_tipo_mudanca is not None:
            payload["tipo_mudanca"] = event_tipo_mudanca
        event_type = task.variables.get("event_type")
        if event_type is not None:
            payload["type"] = event_type
        for var_name in payload_vars:
            if var_name in task.variables:
                payload[var_name] = task.variables[var_name]

        # res-ans-competencia-sentinel (module docstring): stamp the tick-instant date ONCE, here.
        if event_type == _ANS_CRON_DUE_EVENT_TYPE:
            payload[_ANS_CRON_REFERENCE_DATE_KEY] = datetime.now(UTC).date().isoformat()

        if kafka is None:
            # kafka=None reality (module docstring: fail-closed decision + evidence). Loud log,
            # never a fabricated success: topic/business-key/process-instance-id + payload KEY
            # NAMES only (never values — some payload fields are sensitive business identifiers,
            # e.g. numero_guia_tiss, even though they are not PHI-named).
            logger.warning(
                "event_publish_no_producer",
                event_topic=event_topic,
                business_key=task.business_key,
                process_instance_id=task.process_instance_id,
                payload_keys=sorted(payload.keys()),
            )
            _stdlib_logger.warning(
                "event_publish_no_producer topic=%s business_key=%s process_instance_id=%s — "
                "kafka=None (no producer wired, T1.2/ADR-0026 gap); event NOT published, "
                "completing with event_published=False",
                event_topic,
                task.business_key,
                task.process_instance_id,
            )
            return {"event_published": False, "event_topic": str(event_topic)}

        try:
            await kafka.publish(
                str(event_topic),
                payload,
                key=task.business_key or None,
            )
        except Exception as exc:
            logger.error(
                "event_publish_failed",
                event_topic=event_topic,
                business_key=task.business_key,
                error=str(exc),
            )
            if str(event_topic) in bpmn_error_topics:
                # GAP-ESC-5: opt-in topic (module docstring) -> BPMN error catchable by a
                # boundaryEvent.
                raise WorkerBpmnError(
                    _ERR_EVENT_PUBLISH_FAILED,
                    f"Falha ao publicar evento {event_topic}: {exc}",
                ) from exc
            raise  # Pre-existing behavior preserved for every non-opt-in topic (zero regression).

        logger.info(
            "event_published",
            event_topic=event_topic,
            business_key=task.business_key,
            task_id=task.task_id,
        )
        return {"event_published": True, "event_topic": str(event_topic)}

    return handler


# ---------------------------------------------------------------------------
# Bootstrap
# ---------------------------------------------------------------------------


def register_events_workers(
    harness: WorkerHarness,
    kafka: KafkaPublisher | None = None,
    **seams: Any,
) -> None:
    """Register the generic `operadora.events.publish` handler on `harness`.

    Raw-handler registration (`harness.register`, not `harness.register_worker`) — see module
    docstring for why this module cannot use the `FunctionWorker` dict-first boundary every other
    domain module uses.
    """
    del seams  # unused — no seam beyond `kafka` is needed by this handler
    harness.register(
        "operadora.events.publish",
        make_publish_event_handler(kafka, bpmn_error_topics=_ESCALATION_PUBLISH_BPMN_ERROR_TOPICS),
    )

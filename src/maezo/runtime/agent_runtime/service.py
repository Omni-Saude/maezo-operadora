"""Agent-runtime SERVICE — the health-only daemon (T1.6, ratified design §10/Q-6).

`docs/design/T1.1-runtime-spine.md` §17 Q-6 (RATIFIED): T1.1 shipped `worker_runtime` but
deliberately deferred `agent_runtime` — Helm's `agents[].enabled` (`values.yaml`) already renders
per-agent Deployments whose command (`python -m maezo.runtime.agent_runtime`,
`deployment-agent-runtime.yaml:71`) pointed at a module that did not exist, so every enabled
agent pod (helena/rafael/marina) CrashLoopBackOff'd. This module closes that hazard with the
SAME health-first daemon pattern as `worker_runtime` (T1.1 design §3/§10/§12), minus the parts
that require graphs:

  STEP 0  Configure observability (AF-13) — `platform.observability.bootstrap_observability`
          installs structlog's processor chain and the OTel TracerProvider BEFORE the daemon's
          first log line. Isolated like every other bring-up block (a raise leaves the
          `observability_configured` readiness check red, never CrashLoops the pod), and EXPLICIT
          about the no-op case: with no OTLP endpoint the check is healthy and its detail says, in
          words, that no span leaves this process.
  STEP A  Bind the health app IMMEDIATELY in an asyncio task. `/healthz` answers 200 right away
          (liveness) before any dependency is touched — the pod never CrashLoops because a
          spec file is missing or a policy fails to parse.
  STEP B  Bring up the FOUR things this build can honestly check, bounded and non-fatal
          (failure logs + leaves the corresponding readiness check unhealthy — liveness stays
          up, per constraint 2 "readiness honest", never a silent 200):
            1. agent definition loadable — via the T0.3 `AgentLoader` (spec/agents/<id>/agent.yaml,
               or the effective-definition ConfigMap mount when `AGENT_DEFINITION_PATH` is set).
            2. autonomy policies loadable — `maezo.gateway.pep.build_pep()` (ADR-0025 D5's
               fail-closed factory: a PEP with no valid matrix must not construct).
            3. inference provider constructible — `maezo.runtime.inference.InferenceProvider`
               (noop is an ACCEPTABLE outcome per Q-6 — this checks construction, not that a
               real LLM is configured).
            4. agent graph loadable (T1.11, defect B6; fernando added T1.12) — `maezo.runtime.
               harness.Harness.create_graph(agent_id)` resolves + builds the REAL agent-specific
               graph (helena/rafael/fernando go through their real `build(config)` with real,
               un-invoked transports; the remaining still-stubbed agents go through their no-arg
               `build()`). This is a
               CONSTRUCTION check (StateGraph build + `.compile()`), not an execution one — no
               node ever runs, so it costs no network I/O even though the injected transports
               (`CibSevenDmnTransport`/`CibSevenHttpTransport`/FHIR/WhatsApp adapters) are the
               REAL classes pointed at this replica's configured URLs.
            5. durable checkpointer provisioned (T3.4/F4) — `_provision_checkpointer` constructs an
               `AsyncPostgresSaver` from `DATABASE_URL` and awaits `setup()` ONCE (idempotent). The
               graph (point 4) is then compiled checkpoint-enabled. FAIL-CLOSED in production
               (`agent_runtime_mode != "local"`): a missing DSN or a setup failure leaves
               `checkpointer_ready` red — the daemon refuses to run stateless; local/dev falls
               back to an in-memory saver with a loud warning. (The a2a checks 6-7 follow.)
          Turn EXECUTION (a graph actually processing a conversation) still does not happen in
          this daemon — see `agent_graph_execution_pending` below. For Helena, live execution is
          driven by the webhook-receiver's own in-process dispatch
          (`maezo.platform.webhooks.whatsapp.dispatch`, T1.11), not this health-only daemon.
  STEP C  Readiness checks read `AgentState` (mirrors `WorkerState`, worker_runtime/service.py).
  STEP D  SIGTERM/SIGINT -> drain: `live=False` (`/healthz` -> 503), stop the health server.
          There is no in-flight work to drain (no graph execution, no locked external tasks) —
          the drain is just "stop accepting traffic, close the server."
"""

from __future__ import annotations

import asyncio
import contextlib
import signal
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import structlog
from langgraph.graph import StateGraph

from maezo.a2a import DelegationDispatcher
from maezo.agents import AgentDefinition, AgentLoader
from maezo.gateway.action_execution import action_approvals
from maezo.gateway.pep import PEP, PolicyError, build_pep
from maezo.platform.health import CheckResult, build_health_server, create_health_app
from maezo.platform.observability import (
    ObservabilityStatus,
    bootstrap_observability,
    get_metrics_collector,
)
from maezo.runtime.checkpoint import Checkpointer, provision_checkpointer
from maezo.runtime.harness import Harness, UnknownAgentError
from maezo.runtime.inference import InferenceProvider

from .settings import AgentRuntimeSettings

if TYPE_CHECKING:
    from maezo.gateway.audit_postgres import PostgresAuditSink

#: The two agents party to the ONE A2A edge W3 wires (design doc §9.2) — every other agent
#: replica is not forced onto Rafael's dependency posture by the `a2a_dispatcher_ready` check.
_A2A_EDGE_AGENT_IDS = ("helena", "rafael")

#: The ONLY non-production `agent_runtime_mode`, and it must be set EXPLICITLY — ADR-0039 Q7 flipped
#: `settings.py`'s default from "local" to the fail-closed "production", so an ABSENT
#: `AGENT_RUNTIME_MODE` now lands on the restrictive branch here too. Mirrors the identically-named
#: discriminator in `a2a_composition.py` (F2): anything other than "local" (Helm injects
#: "kubernetes") is PRODUCTION, where a missing/failed durable checkpointer fails CLOSED rather
#: than silently degrading to stateless / in-memory persistence.
_LOCAL_RUNTIME_MODE = "local"

logger = structlog.get_logger(__name__)


# --- Mutable shared state (read by the readiness checks) -------------------------------------


@dataclass
class AgentState:
    """Daemon state, mutated as the four STEP B checks run. Readiness checks (STEP C) read
    THIS object — keeps the health app (generic, `platform/health.py`) decoupled from the
    runtime. `live` drives `/healthz` (drain: SIGTERM -> live=False)."""

    settings: AgentRuntimeSettings
    live: bool = True
    # AF-13: structlog + OTel are configured by `run()` BEFORE this daemon's first log line, and
    # the result is kept here so `/readyz` can state whether traces actually leave this process
    # instead of leaving "no OTLP endpoint" indistinguishable from "exporter working". None means
    # `run()` has not reached the bootstrap yet (only observable in a unit test that builds the
    # checks directly) — reported RED, because "not evaluated" is not "healthy".
    observability: ObservabilityStatus | None = None
    agent_definition: AgentDefinition | None = None
    agent_definition_error: str | None = None
    pep: PEP | None = None
    pep_error: str | None = None
    # ONDA 1 §5.7: content digest of the deployed effect-policy artefacts (action-approvals.yaml,
    # L0-core.yaml, _hard_frozen.yaml, every tenant overlay), snapshotted at bring-up so the
    # readiness check stays I/O-free. Lets an operator compare DEPLOYED policy against the
    # reviewed commit — the residual `MAEZO_SPEC_DIR` substitution (A-6) is invisible in `/readyz`
    # without it. Precedent: the AMH migration-digest pin (`platform/integrations/amh_inbox.py`).
    effect_policy_digest: str | None = None
    effect_policy_artifacts: Mapping[str, str] = field(default_factory=dict)
    effect_policy_mode: str | None = None
    effect_policy_error: str | None = None
    # ONDA 1 §5.5 / I-11: the RUNTIME half of inevitability. The CI fence catches a bypass in the
    # source; this catches one that only exists at runtime (a monkeypatch, a plugin, a
    # config-driven factory) by asserting every effect seam this replica handed its graph is a
    # gated instance. Snapshotted at bring-up so `/readyz` stays I/O-free, like every check here.
    effect_seams_gated: bool = False
    effect_seams_detail: str = "not evaluated (bring-up has not run)"
    inference_provider: InferenceProvider | None = None
    inference_error: str | None = None
    agent_graph: StateGraph[Any] | None = None
    agent_graph_error: str | None = None
    # O harness que CONSTRUIU o grafo, retido para que um turno possa ser executado.
    #
    # Antes ele era local a `_load_agent_graph` e descartado: o daemon provava que o grafo
    # compila e nao guardava com o que executa-lo. Era a razao estrutural do
    # `agent_graph_execution_not_performed_here` — nao faltava vontade, faltava a referencia.
    #
    # Retido AQUI e nao reconstruido na borda de ingresso de proposito: `_build_tool_deps`
    # delega ao construtor sancionado (`build_agent_seams`), e a checagem de prontidao
    # `effect_seams_gated` afirma que os seams DESTE harness sao instancias gated. Um harness
    # novo montado na borda seria uma segunda raiz de composicao — exatamente o adversario
    # A-11 que o repo ja pagou para eliminar.
    harness: Harness | None = None
    # T3.4/F4: durable LangGraph checkpointer (working-layer state persistence). Provisioned once
    # at bring-up (`.setup()` idempotent). `checkpointer_ready` is FAIL-CLOSED in production
    # (`agent_runtime_mode != "local"`): a prod daemon that cannot durably checkpoint must not
    # advertise readiness — it would otherwise silently run stateless (no resume-after-restart).
    # In local/dev it falls back to an in-memory saver with a LOUD warning.
    checkpointer: Checkpointer | None = None
    checkpointer_ready: bool = False
    checkpointer_backend: str | None = None
    checkpointer_error: str | None = None
    a2a_dispatcher: DelegationDispatcher | None = None
    a2a_dispatcher_error: str | None = None
    # T2.4 A2A W4 (T-F daemon-readiness finalization): FAIL-CLOSED gate mirroring worker_runtime's
    # T-D `audit_sink_ready` — a boot-time-only snapshot (see `a2a_audit_sink_ready`'s own
    # docstring for why this differs from T-D's live per-`/readyz` re-probe) of whether the A2A
    # composition's durable audit sink is VERIFIED reachable, not merely present/constructed.
    a2a_audit_sink_ready: bool = False
    a2a_audit_sink_error: str | None = None

    def is_live(self) -> bool:
        return self.live


# --- STEP C: readiness checks (read AgentState) ------------------------------------------------


def _ensure_observability(state: AgentState) -> ObservabilityStatus:
    """Guarantee STEP 0 ran, once (AF-13). Same contract as the other two composition roots.

    `run()` calls it FIRST, before this daemon's own first log line; `_bring_up_dependencies` calls
    it again so the invariant "a brought-up daemon has observability configured" holds for the
    other way bring-up is reached (a test driving it directly) — without calling
    `setup_observability` twice in production, which OTel would refuse and warn about.
    """
    if state.observability is None:
        state.observability = bootstrap_observability(
            service_name=f"maezo-agent-runtime-{state.settings.agent_id}",
            otlp_endpoint=state.settings.otel_exporter_otlp_endpoint,
        )
    return state.observability


def build_readiness_checks(state: AgentState) -> list[Callable[[], Awaitable[CheckResult]]]:
    """Assemble the NAMED readiness checks the health app runs concurrently on every `/readyz`.

    Every check is defensive — it reads state populated once at STEP B; it never itself performs
    network/file I/O (that already happened, bounded and non-fatal, in `_bring_up_dependencies`),
    so `/readyz` stays cheap regardless of how these checks are implemented.
    """

    async def observability_configured(_state: AgentState = state) -> CheckResult:
        # AF-13. RED only on a real misconfiguration (the bootstrap RAISED — e.g. ADR-0035's
        # fail-closed missing `PHI_HMAC_KEY` under a ratified key policy). An absent OTLP endpoint
        # is the module's DOCUMENTED dev no-op, so it stays healthy — but the detail says, in
        # words, that no span leaves this process. That sentence is the deliverable: before this
        # wiring the two states were indistinguishable from outside the pod.
        status = _state.observability
        if status is None:
            return CheckResult(
                name="observability_configured",
                healthy=False,
                detail="observability bootstrap has not run (run() has not reached STEP 0)",
            )
        return CheckResult(name="observability_configured", healthy=status.configured, detail=status.detail)

    async def agent_definition_loaded(_state: AgentState = state) -> CheckResult:
        # Config loadable (T0.3 loader) — design §10/Q-6, first of the three scaffold checks.
        if _state.agent_definition is not None:
            return CheckResult(
                name="agent_definition_loaded",
                healthy=True,
                detail=f"agent_id={_state.agent_definition.id}",
            )
        return CheckResult(
            name="agent_definition_loaded",
            healthy=False,
            detail=_state.agent_definition_error or "agent definition not loaded",
        )

    async def policies_loadable(_state: AgentState = state) -> CheckResult:
        # Autonomy policies loadable (build_pep() — fail-closed factory, ADR-0025 D5). A PEP
        # that failed to construct means this replica MUST NOT be considered ready: it can never
        # safely gate a tool call.
        if _state.pep is not None:
            return CheckResult(
                name="policies_loadable", healthy=True, detail=f"tenant={_state.pep.matrix.tenant}"
            )
        return CheckResult(
            name="policies_loadable", healthy=False, detail=_state.pep_error or "PEP not constructed"
        )

    async def inference_provider_ready(_state: AgentState = state) -> CheckResult:
        # Inference provider constructible — noop is an ACCEPTABLE outcome (Q-6); this is a
        # construction check, not a "real LLM configured" check. `health_check()` surfaces the
        # noop warning in the detail without failing readiness over it.
        if _state.inference_provider is not None:
            detail = _state.inference_provider.health_check().get("message")
            return CheckResult(name="inference_provider_ready", healthy=True, detail=detail)
        return CheckResult(
            name="inference_provider_ready",
            healthy=False,
            detail=_state.inference_error or "inference provider not constructed",
        )

    async def graph_loaded(_state: AgentState = state) -> CheckResult:
        # T1.11/defect B6 (fernando added T1.12): the agent's REAL graph builds (helena/rafael/
        # fernando go through their real build(config); still-stubbed agents go through their
        # no-arg build()). Construction only — no node ever runs from this check.
        if _state.agent_graph is not None:
            return CheckResult(
                name="graph_loaded", healthy=True, detail=f"agent_id={_state.settings.agent_id}"
            )
        return CheckResult(
            name="graph_loaded", healthy=False, detail=_state.agent_graph_error or "agent graph not built"
        )

    async def checkpointer_ready(_state: AgentState = state) -> CheckResult:
        # T3.4/F4: FAIL-CLOSED durable-persistence gate. In production (`agent_runtime_mode !=
        # "local"`) this is red unless an AsyncPostgresSaver was constructed AND awaited `setup()`
        # succeeded — the daemon refuses to advertise readiness while it could only run stateless
        # (mirrors the F2 a2a_composition discipline + worker_runtime's audit_sink_ready posture).
        # In local/dev the in-memory fallback reports healthy with its backend named in the detail.
        if _state.checkpointer_ready:
            return CheckResult(
                name="checkpointer_ready",
                healthy=True,
                detail=f"backend={_state.checkpointer_backend}",
            )
        return CheckResult(
            name="checkpointer_ready",
            healthy=False,
            detail=_state.checkpointer_error
            or "durable checkpointer not provisioned — the daemon refuses to run stateless in "
            "production (T3.4/F4 fail-closed)",
        )

    async def a2a_dispatcher_ready(_state: AgentState = state) -> CheckResult:
        # T2.4 A2A W3: construction-only proof the Helena->Rafael `authorization.analyze`
        # delegation edge ASSEMBLES (mirrors `graph_loaded` — no `.delegate()` call happens here,
        # no turn ever runs). Gated to the two agents party to this ONE edge (design doc §9.2):
        # every other agent replica reports healthy/not-applicable rather than being forced onto
        # Rafael's dependency posture (dmn/cibseven/audit_sink) for an edge it isn't part of.
        if _state.settings.agent_id not in _A2A_EDGE_AGENT_IDS:
            return CheckResult(
                name="a2a_dispatcher_ready",
                healthy=True,
                detail=f"not applicable to agent_id={_state.settings.agent_id!r}",
            )
        if _state.a2a_dispatcher is not None:
            return CheckResult(
                name="a2a_dispatcher_ready", healthy=True, detail="helena->rafael edge assembled"
            )
        return CheckResult(
            name="a2a_dispatcher_ready",
            healthy=False,
            detail=_state.a2a_dispatcher_error or "A2A delegation dispatcher not assembled",
        )

    async def a2a_audit_sink_ready(_state: AgentState = state) -> CheckResult:
        # T2.4 A2A W4 (T-F daemon-readiness finalization): FAIL-CLOSED gate mirroring
        # worker_runtime's T-D `audit_sink_ready` (design doc §10) — deliberately SEPARATE from
        # `a2a_dispatcher_ready` above (which stays construction-only, unchanged) so this is
        # purely ADDITIVE. Gated to the two A2A-edge agents, same as `a2a_dispatcher_ready`.
        #
        # Honest scope note (do not over-claim): unlike `worker_runtime`'s own `audit_sink_ready`,
        # this is a BOOT-TIME-ONLY snapshot, not a live per-`/readyz` re-probe — there is no
        # delegation fetch/consume rotation here for a live re-probe to gate entry into (W3's
        # "health-only daemon" nuance, unchanged by this build). If/when a real delegation
        # consumer is wired, it should condition on this bit before pulling work; this build only
        # makes the bit honestly available.
        if _state.settings.agent_id not in _A2A_EDGE_AGENT_IDS:
            return CheckResult(
                name="a2a_audit_sink_ready",
                healthy=True,
                detail=f"not applicable to agent_id={_state.settings.agent_id!r}",
            )
        if _state.a2a_audit_sink_ready:
            return CheckResult(name="a2a_audit_sink_ready", healthy=True, detail="a2a audit_chain reachable")
        return CheckResult(
            name="a2a_audit_sink_ready",
            healthy=False,
            detail=_state.a2a_audit_sink_error
            or "A2A composition's durable audit sink not verified reachable — the daemon refuses "
            "to consider delegation processing ready until audit_chain is confirmed (ADR-0007 "
            "fail-closed, mirrors worker_runtime's T-D audit_sink_ready posture)",
        )

    async def effect_policy_digest(_state: AgentState = state) -> CheckResult:
        # ONDA 1 §5.7 item 2. NOT a fail-closed gate on the policy CONTENT — `policies_loadable`
        # above already refuses readiness for a PEP that could not build, and the manifest loader
        # fails closed on its own. This check exists so the DEPLOYED policy plane is identifiable
        # from outside the pod: an operator (or the approval packet) compares this digest against
        # the reviewed commit. Unhealthy only when the digest could not be computed at all, which
        # means the artefacts were unreadable — a real config fault, not a policy verdict.
        if _state.effect_policy_digest:
            return CheckResult(
                name="effect_policy_digest",
                healthy=True,
                detail=f"sha256={_state.effect_policy_digest[:16]} mode={_state.effect_policy_mode} "
                f"artifacts={len(_state.effect_policy_artifacts)}",
            )
        return CheckResult(
            name="effect_policy_digest",
            healthy=False,
            detail=_state.effect_policy_error or "effect-policy digest not computed",
        )

    async def effect_seams_gated(_state: AgentState = state) -> CheckResult:
        # ONDA 1 §5.5's boot assertion. RED, never fatal to liveness — the same posture as every
        # check above (design I-2: "the composition roots already isolate build failures into red
        # readiness, so the failure mode is 'replica not ready', never 'replica ready and
        # ungated'"). Deliberately NON-VACUOUS: a dep map with no effect seam at all reports
        # unhealthy rather than trivially green (`tool_registry.effect_seams_gated`), because
        # "found nothing to check" is exactly what a seam-removing bypass looks like.
        if _state.effect_seams_gated:
            return CheckResult(name="effect_seams_gated", healthy=True, detail=_state.effect_seams_detail)
        return CheckResult(name="effect_seams_gated", healthy=False, detail=_state.effect_seams_detail)

    return [
        observability_configured,
        agent_definition_loaded,
        policies_loadable,
        inference_provider_ready,
        graph_loaded,
        checkpointer_ready,
        a2a_dispatcher_ready,
        a2a_audit_sink_ready,
        effect_policy_digest,
        effect_seams_gated,
    ]


# --- STEP B: dependency bring-up (bounded, non-fatal) -------------------------------------------


def _declared_model_tiers(definition: AgentDefinition | None) -> dict[str, str]:
    """This replica's `task_kind -> tier` map (AF-12), or `{}` when no definition loaded.

    Total by construction: a malformed `model:` block is already skipped by
    `AgentDefinition.model_tiers`, and a definition that failed to load leaves the map empty
    rather than blocking the inference provider — the definition's own failure is already
    reported by the `agent_definition_loaded` readiness check, and reporting it twice would make
    two red checks out of one fault.
    """
    if definition is None:
        return {}
    return definition.model_tiers()


def _load_agent_definition(settings: AgentRuntimeSettings) -> AgentDefinition:
    """Load this replica's agent definition — the effective (merged) ConfigMap mount when
    `AGENT_DEFINITION_PATH` is set (K8s, federated definitions per ADR-0004), else the T0.3
    `spec/agents/<id>/agent.yaml` source of truth directly (local dev)."""
    loader = AgentLoader()
    if settings.agent_definition_path:
        from pathlib import Path

        return loader.load(Path(settings.agent_definition_path))
    return loader.load_by_id(settings.agent_id)


def _build_tool_deps(
    settings: AgentRuntimeSettings, inference: InferenceProvider | None = None
) -> dict[str, Any]:
    """Resolve this replica's agent seams through the ONE sanctioned constructor (Onda 1 §5.5).

    THIN DELEGATION, deliberately. Until Onda 1 this function held the per-agent adapter choices
    itself, and `platform/webhooks/service.py:100` held a second, divergent copy of the same
    knowledge — adversary A-11 ("two composition roots drift"), which the a2a root had already
    tried to avoid by importing THIS function (`a2a_composition.py:74`). All of it now lives in
    `maezo.gateway.tool_registry`, so there is ONE place that knows which adapter each agent gets
    and every seam it returns is a GATED instance.

    Construction stays pure (no network until a node runs), so building the deps purely to prove
    `create_graph(agent_id)` compiles a real graph still costs nothing at readiness-check time:
    the gate itself is dict lookups over an `lru_cache`d manifest (I-9).

    `inference` is threaded through so the provider a graph receives is the GATED one. Passing
    `None` omits the key entirely, which preserves `Harness.create_graph`'s
    `config.setdefault("inference", self._inference)` behaviour byte for byte (`harness.py:170`).
    """
    from maezo.gateway.tool_registry import build_agent_seams

    return build_agent_seams(settings=settings, agent_id=settings.agent_id, inference=inference)


def _load_agent_graph(
    settings: AgentRuntimeSettings,
    inference: InferenceProvider | None,
    checkpointer: Checkpointer | None = None,
) -> tuple[Harness, StateGraph[Any]]:
    """Resolve + build `settings.agent_id`'s real graph (T1.11, defect B6).

    T3.4/F4: the durable `checkpointer` (provisioned once at bring-up) is now wired into the
    `Harness` AND into the structural-validation `compile()` — so the readiness-time proof is
    that the graph compiles *checkpoint-enabled*, not merely stateless. Construction still never
    runs a node (no checkpoint I/O happens here; the write occurs when a turn actually executes,
    e.g. the webhook dispatch path). When `checkpointer` is None (dev with no saver at all) the
    graph still compiles stateless — the fail-closed signal for "no durable persistence in prod"
    is the separate `checkpointer_ready` readiness gate, not this build check.

    Raises `UnknownAgentError`/`ValueError` on any failure — the caller (`_bring_up_dependencies`)
    isolates it into `agent_graph_error`, same as the other STEP B checks.
    """
    harness = Harness(
        inference=inference,
        checkpointer=checkpointer,
        tool_deps=_build_tool_deps(settings, inference=inference),
    )
    graph = harness.create_graph(settings.agent_id)
    saver = checkpointer.saver if checkpointer is not None else None
    graph.compile(checkpointer=saver)  # validates structure (checkpoint-enabled); never runs a node
    # O harness volta junto: quem executa um turno precisa DELE, nao apenas do StateGraph
    # (`Harness.invoke` compila com o checkpointer e emite a telemetria do turno).
    return harness, graph


async def _provision_checkpointer(state: AgentState) -> None:
    """Provision the durable LangGraph checkpointer ONCE, with F2 fail-closed discipline (T3.4/F4).

    Thin adapter over the shared `runtime.checkpoint.provision_checkpointer` policy (the SAME
    helper the live webhook dispatch path uses — so the daemon that advertises readiness and the
    receiver that actually runs Helena's turns can never drift apart on fail-closed semantics),
    mapping its `CheckpointerProvision` result onto this daemon's `AgentState`:

      - `DATABASE_URL` present -> `AsyncPostgresSaver` + awaited `setup()` (idempotent; NOT
        Alembic — `checkpoint_migrations` owns versioning). Success -> `checkpointer_ready`.
        Failure -> PRODUCTION fails closed (readiness red, no fallback); NON-production falls back
        to in-memory with a loud warning.
      - `DATABASE_URL` absent -> PRODUCTION fails closed; NON-production in-memory + warning.

    Isolated exactly like the other bring-up blocks: never propagates (liveness stays up); a
    failure leaves `checkpointer_ready` False so `/readyz` reports it red.
    """
    settings = state.settings
    is_production = settings.agent_runtime_mode != _LOCAL_RUNTIME_MODE
    provision = await provision_checkpointer(
        database_url=settings.database_url,
        is_production=is_production,
        component=f"agent_runtime:{settings.agent_id}",
    )
    state.checkpointer = provision.checkpointer
    state.checkpointer_ready = provision.ready
    state.checkpointer_backend = provision.backend
    state.checkpointer_error = provision.error


async def _probe_a2a_audit_sink(sink: PostgresAuditSink, timeout_s: float) -> bool:
    """Bounded, non-raising connectivity probe for the A2A composition's durable audit sink.

    T2.4 A2A W4 (T-F daemon-readiness finalization) — mirrors `worker_runtime.service.
    _probe_audit_sink` (T-D) exactly: wraps `PostgresAuditSink.check_ready()` (which raises on any
    unreachable/missing-table failure) in a hard timeout so `_bring_up_dependencies` can never hang
    on a slow/hung Postgres. Returns `True` only when the sink positively proves it can reach the
    tenant schema's `audit_chain`; every failure (timeout, connection refused, missing table) is
    `False` (fail-closed).
    """
    try:
        await asyncio.wait_for(sink.check_ready(), timeout=timeout_s)
    except Exception as exc:  # noqa: BLE001 — any failure means "not ready", never propagates.
        logger.warning("a2a_audit_sink_probe_failed", error=str(exc))
        return False
    return True


async def _bring_up_dependencies(state: AgentState) -> None:
    """Run the four STEP B checks. Each block is isolated: failure logs + leaves the
    corresponding readiness check unhealthy, but NEVER propagates (liveness must stay up — this
    is precisely what kills the CrashLoop, Q-6)."""
    _ensure_observability(state)  # AF-13 — no-op when `run()` already did it at STEP 0.
    settings = state.settings

    try:
        state.agent_definition = _load_agent_definition(settings)
    except Exception as exc:  # noqa: BLE001 — isolated per design §10/Q-6; leaves the check unhealthy.
        state.agent_definition_error = f"{type(exc).__name__}: {exc}"
        logger.error("agent_definition_load_failed", agent_id=settings.agent_id, exc_info=True)

    try:
        state.pep = build_pep(tenant=settings.tenant_id)
    except PolicyError as exc:
        state.pep_error = str(exc)
        logger.error("agent_pep_build_failed", tenant=settings.tenant_id, exc_info=True)
    except Exception as exc:  # noqa: BLE001 — same isolation as above.
        state.pep_error = f"{type(exc).__name__}: {exc}"
        logger.error("agent_pep_build_failed", tenant=settings.tenant_id, exc_info=True)

    # ONDA 1 §5.7: snapshot the effect-policy provenance ONCE, here, so `/readyz` never does I/O.
    # Same isolation as every other block: a failure leaves the check unhealthy and nothing else.
    try:
        approvals = action_approvals()
        state.effect_policy_digest = approvals.policy_digest
        state.effect_policy_artifacts = approvals.artifact_digests
        state.effect_policy_mode = approvals.mode
        logger.info(
            "effect_policy_pinned",
            tenant=settings.tenant_id,
            mode=approvals.mode,
            policy_digest=approvals.policy_digest,
            artifact_digests=dict(approvals.artifact_digests),
            default_enforcement=approvals.default_enforcement,
        )
    except Exception as exc:  # noqa: BLE001 — same isolation as above.
        state.effect_policy_error = f"{type(exc).__name__}: {exc}"
        logger.error("effect_policy_pin_failed", exc_info=True)

    try:
        # AF-12: the provider is built WITH this replica's agent-declared tier map, so
        # `agent.yaml`'s `model: {task_default: {tier: fast}, reasoning: {tier: frontier}}` finally
        # reaches the thing that could act on it. `_declared_model_tiers` yields {} when the
        # definition failed to load above — the provider then reports every call under the
        # `sem_mapa` sentinel rather than pretending a tier was declared. A tier outside the
        # ADR-0009 vocabulary raises here and lands on `inference_provider_ready` (red), which is
        # the same fail-closed shape a missing credential already has.
        state.inference_provider = InferenceProvider(
            model_tiers=_declared_model_tiers(state.agent_definition)
        )
    except Exception as exc:  # noqa: BLE001 — same isolation as above.
        state.inference_error = f"{type(exc).__name__}: {exc}"
        logger.error("agent_inference_provider_build_failed", exc_info=True)

    # T3.4/F4: provision the durable checkpointer BEFORE building the graph, so the graph compiles
    # checkpoint-enabled. Own isolation + fail-closed discipline lives inside the helper.
    await _provision_checkpointer(state)

    # ONDA 1 §5.5: snapshot the gatedness of the EXACT dep map `_load_agent_graph` is about to
    # feed the graph. Isolated like every other block — a failure here leaves the check red and
    # nothing else, and it never blocks the graph build below (a policy-plane fault must not
    # become a care-path outage while the gateway is inert).
    try:
        from maezo.gateway.tool_registry import effect_seams_gated as _effect_seams_gated

        state.effect_seams_gated, state.effect_seams_detail = _effect_seams_gated(
            _build_tool_deps(settings, inference=state.inference_provider)
        )
        if not state.effect_seams_gated:
            logger.error(
                "effect_seams_not_gated",
                agent_id=settings.agent_id,
                detail=state.effect_seams_detail,
            )
    except Exception as exc:  # noqa: BLE001 — same isolation as above.
        state.effect_seams_detail = f"{type(exc).__name__}: {exc}"
        logger.error("effect_seams_gated_probe_failed", agent_id=settings.agent_id, exc_info=True)

    try:
        state.harness, state.agent_graph = _load_agent_graph(
            settings, state.inference_provider, state.checkpointer
        )
    except (UnknownAgentError, ValueError) as exc:
        state.agent_graph_error = f"{type(exc).__name__}: {exc}"
        logger.error("agent_graph_build_failed", agent_id=settings.agent_id, exc_info=True)
    except Exception as exc:  # noqa: BLE001 — same isolation as above.
        state.agent_graph_error = f"{type(exc).__name__}: {exc}"
        logger.error("agent_graph_build_failed", agent_id=settings.agent_id, exc_info=True)

    if settings.agent_id in _A2A_EDGE_AGENT_IDS:
        # T2.4 A2A W3: construction-only — proves the Option-A in-process Helena->Rafael edge
        # assembles (see `a2a_composition.build_auth_delegation_dispatcher`'s own docstring for
        # why this is NOT a live consumer). Isolated exactly like the four checks above: failure
        # leaves `a2a_dispatcher_ready` unhealthy, never propagates (liveness stays up).
        # Local import: avoids a module-load-time cycle (`a2a_composition` imports
        # `_build_tool_deps` FROM this module; deferring the import until this function actually
        # runs means `service` is already fully initialized by the time it's needed).
        from .a2a_composition import build_auth_delegation_dispatcher

        try:
            state.a2a_dispatcher = build_auth_delegation_dispatcher(
                settings, inference=state.inference_provider
            )
        except Exception as exc:  # noqa: BLE001 — same isolation as above.
            state.a2a_dispatcher_error = f"{type(exc).__name__}: {exc}"
            logger.error("a2a_dispatcher_build_failed", agent_id=settings.agent_id, exc_info=True)

        # T2.4 A2A W4 (T-F daemon-readiness finalization, design doc §10): FAIL-CLOSED boot-time
        # probe of the SAME audit sink the composition above would use (reconstructed via
        # `_build_tool_deps` — construction is pure, mirrors every other use of that helper; no
        # second, divergent dep-construction path). Isolated exactly like the checks above:
        # failure leaves `a2a_audit_sink_ready` unhealthy, never propagates.
        if settings.database_url:
            try:
                audit_sink = _build_tool_deps(settings, inference=state.inference_provider).get("audit_sink")
                if audit_sink is not None:
                    state.a2a_audit_sink_ready = await _probe_a2a_audit_sink(
                        audit_sink, settings.dep_connect_timeout_s
                    )
            except Exception as exc:  # noqa: BLE001 — same isolation as above.
                state.a2a_audit_sink_error = f"{type(exc).__name__}: {exc}"
                logger.error(
                    "a2a_audit_sink_probe_construction_failed", agent_id=settings.agent_id, exc_info=True
                )
        else:
            state.a2a_audit_sink_error = (
                "DATABASE_URL unset — the A2A composition has no durable audit sink to probe"
            )

    logger.info(
        "agent_dependencies_brought_up",
        agent_id=settings.agent_id,
        agent_definition_loaded=state.agent_definition is not None,
        policies_loadable=state.pep is not None,
        inference_provider_ready=state.inference_provider is not None,
        graph_loaded=state.agent_graph is not None,
        checkpointer_ready=state.checkpointer_ready,
        checkpointer_backend=state.checkpointer_backend,
        a2a_dispatcher_ready=(
            state.a2a_dispatcher is not None if settings.agent_id in _A2A_EDGE_AGENT_IDS else "n/a"
        ),
        a2a_audit_sink_ready=(
            state.a2a_audit_sink_ready if settings.agent_id in _A2A_EDGE_AGENT_IDS else "n/a"
        ),
        effect_policy_digest=state.effect_policy_digest,
        effect_seams_gated=state.effect_seams_gated,
    )
    # Linha de log que CARREGA significado, e por isso precisa dizer a verdade sobre esta
    # replica. Ela afirmava, incondicionalmente, que o daemon nao executa turnos — o que virou
    # mentira em 19/08/2026, quando o ingresso passou a existir. Um log que contradiz o
    # comportamento e' pior que log nenhum: foi exatamente esta frase que sustentou o
    # diagnostico "o agente nao atua", e ela continuaria sustentando-o depois de deixar de ser
    # verdade.
    if settings.agent_ingress_enabled:
        logger.info(
            "agent_graph_execution_available_here",
            agent_id=settings.agent_id,
            note="a rota de ingresso esta montada (POST /v1/autorizacoes): esta replica EXECUTA "
            "turnos. O turno emite `harness_invoke` + `agent_turn` e, com provedor real, "
            "`llm_token_usage`.",
        )
    else:
        logger.info(
            "agent_graph_execution_not_performed_here",
            agent_id=settings.agent_id,
            note="the graph builds/compiles (graph_loaded check) but this replica has no intake "
            "route (MAEZO_AGENT_INGRESS_ENABLED is off), so it does not execute turns — see "
            "docs/design/T1.1-runtime-spine.md §10/§17 Q-6 and this module's STEP B point 4.",
        )


# --- run() — orchestrates STEP A..D -------------------------------------------------------------


async def run(settings: AgentRuntimeSettings) -> None:
    """Run the agent-runtime until SIGTERM/SIGINT. See the module docstring for STEP 0/A..D."""
    state = AgentState(settings=settings)

    # STEP 0 (AF-13): configure structlog + OpenTelemetry BEFORE this daemon writes its first log
    # line. Ordered first on purpose — a bootstrap run after `agent_runtime_starting` would leave
    # the daemon's own start-up record on structlog's DEFAULT configuration, which is the state
    # AF-13 found the whole fleet in. `bootstrap_observability` never propagates; a failure lands
    # on the `observability_configured` readiness check.
    observability = _ensure_observability(state)

    logger.info(
        "agent_runtime_starting",
        tenant=settings.tenant_id,
        agent_id=settings.agent_id,
        security_zone=settings.agent_security_zone,
        health_port=settings.health_port,
        observability=observability.detail,
    )
    shutdown = asyncio.Event()
    loop = asyncio.get_running_loop()

    # STEP A: health app + uvicorn server NOW — /healthz 200 immediately (liveness), BEFORE any
    # dependency comes up. We claim signal ownership (uvicorn's own capture disabled) so the
    # daemon controls the drain sequence exactly (mirrors worker_runtime, design §8/§12).
    app = create_health_app(
        readiness_checks=build_readiness_checks(state),
        is_live=state.is_live,
        registry=get_metrics_collector().registry,
    )
    # A rota de ingresso, quando ligada. Montada AQUI, no mesmo servidor da saude, porque a
    # porta 8000 do container ja e' a que o service discovery publica — abrir uma segunda porta
    # obrigaria a mais uma regra de Security Group para nenhum ganho.
    #
    # Ela le `state` a cada chamada (nao captura o harness): este bloco roda ANTES do bring-up,
    # e um harness capturado aqui seria `None` para sempre. Enquanto o bring-up nao terminar, a
    # rota responde 503 — a mesma leitura que `/readyz` da'.
    if settings.agent_ingress_enabled:
        from maezo.runtime.agent_runtime.ingress import build_ingress_router  # noqa: PLC0415

        app.include_router(build_ingress_router(state))
        logger.info(
            "agent_ingress_mounted",
            agent_id=settings.agent_id,
            rota="POST /v1/autorizacoes",
            porta=settings.health_port,
        )

    server = build_health_server(app, port=settings.health_port)
    server.capture_signals = contextlib.nullcontext  # type: ignore[assignment]  # we own the signals
    serve_task = asyncio.create_task(server.serve(), name="health-server")

    def _request_shutdown(sig: signal.Signals) -> None:
        logger.info("shutdown_signal", signal=sig.name)
        state.live = False  # /healthz -> 503: leave the load-balancing rotation (drain).
        server.should_exit = True
        shutdown.set()

    def _on_serve_done(task: asyncio.Task[None]) -> None:
        # Fail-fast: if the health server dies on its own (e.g. bind failure), don't hang on
        # shutdown.wait() with no health server up — force live=False + shutdown so run() drains
        # and exits.
        state.live = False
        if not shutdown.is_set():
            exc = None if task.cancelled() else task.exception()
            logger.error("health_server_stopped_early", error=repr(exc) if exc else "no_exception")
            shutdown.set()

    serve_task.add_done_callback(_on_serve_done)
    for _sig in (signal.SIGTERM, signal.SIGINT):
        with contextlib.suppress(NotImplementedError):
            # add_signal_handler is unavailable on some loops (e.g. Windows ProactorEventLoop):
            # suppress and move on — not a supported deployment target here.
            loop.add_signal_handler(_sig, _request_shutdown, _sig)

    # STEP B: the three bounded, non-fatal checks. Skip if a signal already arrived (or the
    # health server already died) — go straight to drain.
    if not shutdown.is_set():
        await _bring_up_dependencies(state)

    # No STEP E supervised loop — health-only scaffold, nothing to run (Q-6). Just wait for
    # shutdown.
    logger.info("shutdown_before_run" if shutdown.is_set() else "agent_runtime_running")
    await shutdown.wait()

    # STEP D drain: nothing in-flight to await (no graph execution, no locked tasks) — stop the
    # health server and exit.
    server.should_exit = True
    with contextlib.suppress(asyncio.CancelledError, SystemExit):
        await serve_task

    # T3.4/F4: release the checkpointer's connection pool (idempotent; no-op for the in-memory
    # fallback / when never provisioned). Non-fatal — a close failure must not mask shutdown.
    if state.checkpointer is not None:
        with contextlib.suppress(Exception):
            await state.checkpointer.aclose()

    logger.info("agent_runtime_stopped", tenant=settings.tenant_id, agent_id=settings.agent_id)

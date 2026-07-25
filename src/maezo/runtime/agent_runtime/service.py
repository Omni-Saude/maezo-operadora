"""Agent-runtime SERVICE — the health-only daemon (T1.6, ratified design §10/Q-6).

`docs/design/T1.1-runtime-spine.md` §17 Q-6 (RATIFIED): T1.1 shipped `worker_runtime` but
deliberately deferred `agent_runtime` — Helm's `agents[].enabled` (`values.yaml`) already renders
per-agent Deployments whose command (`python -m maezo.runtime.agent_runtime`,
`deployment-agent-runtime.yaml:71`) pointed at a module that did not exist, so every enabled
agent pod (helena/rafael/marina) CrashLoopBackOff'd. This module closes that hazard with the
SAME health-first daemon pattern as `worker_runtime` (T1.1 design §3/§10/§12), minus the parts
that require graphs:

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
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import structlog
from langgraph.graph import StateGraph

from maezo.a2a import DelegationDispatcher
from maezo.agents import AgentDefinition, AgentLoader
from maezo.gateway.pep import PEP, PolicyError, build_pep
from maezo.platform.health import CheckResult, build_health_server, create_health_app
from maezo.platform.observability import get_metrics_collector
from maezo.runtime.harness import Harness, UnknownAgentError
from maezo.runtime.inference import InferenceProvider

from .settings import AgentRuntimeSettings

#: The two agents party to the ONE A2A edge W3 wires (design doc §9.2) — every other agent
#: replica is not forced onto Rafael's dependency posture by the `a2a_dispatcher_ready` check.
_A2A_EDGE_AGENT_IDS = ("helena", "rafael")

logger = structlog.get_logger(__name__)


# --- Mutable shared state (read by the readiness checks) -------------------------------------


@dataclass
class AgentState:
    """Daemon state, mutated as the four STEP B checks run. Readiness checks (STEP C) read
    THIS object — keeps the health app (generic, `platform/health.py`) decoupled from the
    runtime. `live` drives `/healthz` (drain: SIGTERM -> live=False)."""

    settings: AgentRuntimeSettings
    live: bool = True
    agent_definition: AgentDefinition | None = None
    agent_definition_error: str | None = None
    pep: PEP | None = None
    pep_error: str | None = None
    inference_provider: InferenceProvider | None = None
    inference_error: str | None = None
    agent_graph: StateGraph[Any] | None = None
    agent_graph_error: str | None = None
    a2a_dispatcher: DelegationDispatcher | None = None
    a2a_dispatcher_error: str | None = None

    def is_live(self) -> bool:
        return self.live


# --- STEP C: readiness checks (read AgentState) ------------------------------------------------


def build_readiness_checks(state: AgentState) -> list[Callable[[], Awaitable[CheckResult]]]:
    """Assemble the NAMED readiness checks the health app runs concurrently on every `/readyz`.

    Every check is defensive — it reads state populated once at STEP B; it never itself performs
    network/file I/O (that already happened, bounded and non-fatal, in `_bring_up_dependencies`),
    so `/readyz` stays cheap regardless of how these checks are implemented.
    """

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

    return [
        agent_definition_loaded,
        policies_loadable,
        inference_provider_ready,
        graph_loaded,
        a2a_dispatcher_ready,
    ]


# --- STEP B: dependency bring-up (bounded, non-fatal) -------------------------------------------


def _load_agent_definition(settings: AgentRuntimeSettings) -> AgentDefinition:
    """Load this replica's agent definition — the effective (merged) ConfigMap mount when
    `AGENT_DEFINITION_PATH` is set (K8s, federated definitions per ADR-0004), else the T0.3
    `spec/agents/<id>/agent.yaml` source of truth directly (local dev)."""
    loader = AgentLoader()
    if settings.agent_definition_path:
        from pathlib import Path

        return loader.load(Path(settings.agent_definition_path))
    return loader.load_by_id(settings.agent_id)


def _build_tool_deps(settings: AgentRuntimeSettings) -> dict[str, Any]:
    """Construct the REAL (never-invoked-here) transports a real agent `build(config)` needs.

    Construction of every transport below is pure (no network call happens until a node
    actually runs — `CibSevenDmnTransport`/`CibSevenHttpTransport`/`FhirServer`/`WhatsAppServer`
    all defer I/O to their async methods), so building them here — purely to prove
    `create_graph(agent_id)` compiles a real graph — costs nothing at readiness-check time.
    """
    from maezo.agents.helena.adapters import WhatsAppServerSender
    from maezo.agents.rafael.adapters import FhirServerReader
    from maezo.gateway.audit_postgres import PostgresAuditSink
    from maezo.tools.mcp_cibseven.transport import CibSevenHttpTransport
    from maezo.tools.mcp_fhir.server import FhirServer, FhirSettings
    from maezo.tools.mcp_whatsapp.server import WhatsAppServer
    from maezo.tools.workers.dmn_transport import CibSevenDmnTransport

    deps: dict[str, Any] = {
        "dmn": CibSevenDmnTransport(settings.cibseven_base_url),
        "cibseven": CibSevenHttpTransport(settings.cibseven_base_url),
    }
    # T-C2 fence: every agent's `start_process` node structurally REQUIRES a durable ADR-0007 sink
    # (the fail-closed emit-before-effect chokepoint, `start_process_idempotent`). Construction is
    # pure (asyncpg pool is lazy, like the transports above — no I/O at readiness-check time). When
    # `DATABASE_URL` is unset the sink is deliberately NOT fabricated: the agent `build(config)`
    # then fail-closes (missing `audit_sink`) and `graph_loaded` reports unhealthy with the reason —
    # an agent replica that cannot durably audit its process starts must not advertise a loadable
    # graph (ADR-0007 fail-closed; mirrors T-D's worker-daemon `audit_sink_ready` posture).
    if settings.database_url:
        deps["audit_sink"] = PostgresAuditSink(settings.database_url, settings.tenant_id)
    if settings.agent_id in ("helena", "fernando"):
        # T1.12: Fernando reuses the SAME generic WhatsAppSender adapter as Helena — it is
        # documented as a structural (Protocol-satisfying) shim over `WhatsAppServer`, not
        # Helena-specific business logic (`agents/helena/adapters.py`'s own docstring).
        deps["whatsapp"] = WhatsAppServerSender(WhatsAppServer())
    if settings.agent_id == "lucas":
        # T1.12: Lucas needs the same `whatsapp` seam as Helena (his `respond_member`/
        # `escalate_human` nodes send WhatsApp text) — his OWN adapter (`agents/lucas/
        # adapters.py`), deliberately a duplicate of Helena's, not an import from Helena's
        # package (ADR-0004 federated Agent-Definition independence — mirrors why rafael's
        # `FhirServerReader` is its own copy too). The import is branch-local ONLY because the
        # top-level `WhatsAppServerSender` name is already taken by Helena's adapter — this
        # block is purely ADDITIVE to the pre-existing helena/rafael wiring around it (R1
        # cycle-1 F3: an earlier revision also moved the sibling imports branch-local, which
        # broke clean-merge semantics with in-flight sibling PRs; reverted).
        from maezo.agents.lucas.adapters import WhatsAppServerSender as LucasWhatsAppServerSender

        deps["whatsapp"] = LucasWhatsAppServerSender(WhatsAppServer())
    if settings.agent_id == "rafael":
        deps["fhir"] = FhirServerReader(FhirServer(FhirSettings(base_url=settings.fhir_base_url)))
    if settings.agent_id == "carolina":
        # T1.12: Carolina's `gather` seam (`graph.SummaryReader`) only needs `read_patient` —
        # `FhirServerReader` (built for Rafael) already implements it structurally, so it is
        # reused here rather than duplicating an adapter (module docstring's divergence #6 in
        # `agents/carolina/graph.py`).
        deps["fhir"] = FhirServerReader(FhirServer(FhirSettings(base_url=settings.fhir_base_url)))
    if settings.agent_id == "gustavo":
        # T1.12: gustavo's OPTIONAL `fhir` seam (J2 NIP dossier enrichment, best-effort) —
        # structural reuse of rafael's adapter (`agents/gustavo/graph.py::FhirReader` is the
        # same `read_patient` Protocol shape). Purely additive; required deps (dmn/cibseven/
        # inference) are already provided generically above.
        deps["fhir"] = FhirServerReader(FhirServer(FhirSettings(base_url=settings.fhir_base_url)))
    if settings.agent_id == "valentina":
        # T1.12: Valentina's optional post-consent `gather` seam (her own adapter — her
        # `PatientSummaryReader` Protocol needs `read_patient_summary`, which Rafael's adapter
        # does not expose). Purely additive; construction is pure (no I/O until a node runs),
        # same as every transport above.
        from maezo.agents.valentina.adapters import FhirServerReader as ValentinaFhirServerReader

        deps["fhir"] = ValentinaFhirServerReader(FhirServer(FhirSettings(base_url=settings.fhir_base_url)))
    if settings.agent_id == "andre":
        # T1.12: Andre's `gather` seam (`graph.PatientSummaryReader`) only needs `read_patient` —
        # `FhirServerReader` (built for Rafael) already implements it structurally, so it is
        # reused here rather than duplicating an adapter (labeled boundary in
        # `agents/andre/graph.py`'s module docstring). Andre's OTHER optional seam
        # (`population` — the k-anon lake client) is PORT-PENDING (WB.4) and deliberately not
        # wired: `build(config)` treats its absence as a disclosed gap note, never a failure.
        deps["fhir"] = FhirServerReader(FhirServer(FhirSettings(base_url=settings.fhir_base_url)))
    return deps


def _load_agent_graph(settings: AgentRuntimeSettings, inference: InferenceProvider | None) -> StateGraph[Any]:
    """Resolve + build `settings.agent_id`'s real graph (T1.11, defect B6).

    Raises `UnknownAgentError`/`ValueError` on any failure — the caller (`_bring_up_dependencies`)
    isolates it into `agent_graph_error`, same as the other three STEP B checks.
    """
    harness = Harness(inference=inference, tool_deps=_build_tool_deps(settings))
    graph = harness.create_graph(settings.agent_id)
    graph.compile()  # validates the graph is structurally sound; never runs a node
    return graph


async def _bring_up_dependencies(state: AgentState) -> None:
    """Run the four STEP B checks. Each block is isolated: failure logs + leaves the
    corresponding readiness check unhealthy, but NEVER propagates (liveness must stay up — this
    is precisely what kills the CrashLoop, Q-6)."""
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

    try:
        state.inference_provider = InferenceProvider()
    except Exception as exc:  # noqa: BLE001 — same isolation as above.
        state.inference_error = f"{type(exc).__name__}: {exc}"
        logger.error("agent_inference_provider_build_failed", exc_info=True)

    try:
        state.agent_graph = _load_agent_graph(settings, state.inference_provider)
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

    logger.info(
        "agent_dependencies_brought_up",
        agent_id=settings.agent_id,
        agent_definition_loaded=state.agent_definition is not None,
        policies_loadable=state.pep is not None,
        inference_provider_ready=state.inference_provider is not None,
        graph_loaded=state.agent_graph is not None,
        a2a_dispatcher_ready=(
            state.a2a_dispatcher is not None if settings.agent_id in _A2A_EDGE_AGENT_IDS else "n/a"
        ),
    )
    # Explicit, load-bearing log line (T1.11 update of the Q-6 scaffold note): the graph now
    # BUILDS for real (helena/rafael, defect B6) but this daemon still never EXECUTES a turn —
    # see the module docstring's STEP B point 4 for where live execution actually happens
    # (the webhook receiver's in-process dispatch, for Helena; Rafael has no live intake path
    # wired in this build — see `agents/rafael/graph.py`'s module docstring).
    logger.info(
        "agent_graph_execution_not_performed_here",
        agent_id=settings.agent_id,
        note="the graph builds/compiles (graph_loaded check) but this health-only daemon does "
        "not execute turns — see docs/design/T1.1-runtime-spine.md §10/§17 Q-6 and this "
        "module's STEP B point 4.",
    )


# --- run() — orchestrates STEP A..D -------------------------------------------------------------


async def run(settings: AgentRuntimeSettings) -> None:
    """Run the agent-runtime until SIGTERM/SIGINT. See the module docstring for STEP A..D."""
    logger.info(
        "agent_runtime_starting",
        tenant=settings.tenant_id,
        agent_id=settings.agent_id,
        security_zone=settings.agent_security_zone,
        health_port=settings.health_port,
    )
    state = AgentState(settings=settings)
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

    logger.info("agent_runtime_stopped", tenant=settings.tenant_id, agent_id=settings.agent_id)

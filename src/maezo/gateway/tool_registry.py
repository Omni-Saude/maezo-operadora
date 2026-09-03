"""The ONLY sanctioned constructor of effect seams (Onda 1, design §5.5 — Candidate B's core).

Design §3/§4 rejected "decorate `_build_tool_deps`" (Candidate A) for one fatal reason, C-A1: the
daemon that owns `_build_tool_deps` never executes a turn — it says so itself
(`agent_runtime/service.py:598-604`, `agent_graph_execution_not_performed_here`) — while the ONE
agent path that runs live traffic builds its own transports (`platform/webhooks/service.py:100-129`)
and calls `agents.helena.graph.build({...})` directly (`platform/webhooks/whatsapp/dispatch.py:161`).
Gating only `_build_tool_deps` would gate exactly the traffic that does not exist.

So: FIVE composition roots, ONE constructor, no mirrors (adversary A-11 — `a2a_composition.py`
imports `_build_tool_deps` precisely to avoid a second construction path, while
`platform/webhooks/service.py` merely "mirrors" it; the drift already happened once).

  (a) `runtime/agent_runtime/service.py::_build_tool_deps`        -> thin delegation to here
  (b) `runtime/agent_runtime/a2a_composition.py`                  -> both dispatchers + inference
  (c) `platform/webhooks/service.py`  (THE LIVE AGENT PATH)       -> dispatcher seams + per-turn
  (d) `platform/integrations/notifications_bridge.py::build_bridge`
  (e) `runtime/worker_runtime/service.py`                         -> `dmn=` / `engine=`

Every root additionally gains the `effect_seams_gated` readiness check (:func:`effect_seams_gated`)
— the RUNTIME half of inevitability (I-11). The CI fence catches source-level bypasses; this
catches a monkeypatch, a plugin, or a config-driven factory that hands a graph a raw transport.

LAYERING. `maezo.gateway` must not depend on `maezo.tools` (`action_execution.py:206-208`), so
every concrete transport is imported INSIDE the factory that builds it — the branch-local idiom
`agent_runtime/service.py` already uses at `:296-302`/`:332`. The policy core (`effect_pep`,
`effect_classes`) imports nothing from `maezo.tools` at all, at any depth, which is what keeps it
AST-fenceable.

=================================================================================================
THE PER-REQUEST-TENANT KNOT — options, counterexamples, and the split that resolves it
=================================================================================================
STEP 1 — CHECK THE PREMISE. "The tenant arrives per request" is FALSE in this tree, and it had to
be re-derived rather than assumed. `HelenaDispatcher.tenant_id` is bound at construction from
`settings.tenant_id` (`platform/webhooks/service.py:107`); `WhatsAppWebhookSettings.tenant_id` is
the `TENANT_ID` env var (`whatsapp/settings.py:26`); and `extract_inbound_messages`
(`dispatch.py:74-112`) parses `entry/changes/value/messages` into `InboundMessage(from_number,
text, message_id)` — there is no tenant anywhere in the WhatsApp Cloud API envelope this build
reads. The same holds for the other four roots (`AgentRuntimeSettings`, `WorkerRuntimeSettings`,
`NotificationsBridgeSettings` all carry a single `TENANT_ID`). Tenant is PER PROCESS.

STEP 2 — THE KNOT IS REAL, JUST RELOCATED. The per-request axis in the live path is not the
tenant, it is the RECIPIENT: `_ScopedWhatsAppSender` (`dispatch.py:100-118`) is created INSIDE
`dispatch()` (`:158-160`), closes over the raw phone number of the one request being handled, and
must not outlive the turn. So the registry cannot hand the webhook root a finished `whatsapp`
seam the way it can hand it `dmn`/`cibseven`/`inference`.

STEP 3 — OPTIONS, EACH WITH ITS COUNTEREXAMPLE.

  O1 — Build the whole `DecisionContext` per turn (then a per-request tenant would be free).
       COUNTEREXAMPLE: `build_pep(tenant=…)` (`gateway/pep.py:336`) reads and parses
       `L0-core.yaml`, `_hard_frozen.yaml` and the tenant overlay from disk, and the capability
       view re-reads `agent.yaml` through `AgentLoader`. That is filesystem I/O per inbound
       message, on a path that holds the HTTP response open synchronously (`dispatch.py`'s own
       "in-process dispatch" boundary note). It breaks I-9 ("zero added I/O on read classes")
       and makes the gateway the slowest thing in a care turn. REJECTED.

  O2 — Make `tenant`/`principal` parameters of the wrapper's methods, so one wrapper serves all.
       COUNTEREXAMPLE: adversary A-8 — "a wrapper that trusts a caller-supplied principal gates
       nothing". It also does not fit: `WhatsAppSender.send(to_hash, text)` has no room, so every
       Protocol in `agents/*/graph.py` would have to change, which is the zero-node-change claim
       gone. REJECTED, and now structurally forbidden by the no-principal-argument fence test.

  O3 — Give the webhook the long-lived `WhatsAppServerSender` adapter and gate that once.
       COUNTEREXAMPLE: `agents/helena/adapters.py`'s own docstring says the live path
       deliberately does NOT use it — it passes `to` straight through, so sending to the phone
       HASH delivers to the hash literal, not to a person. Substituting it is a live-behaviour
       change, the exact opposite of the inert build this wave promises. REJECTED.

  O4 — SPLIT THE TWO LIFETIMES. **CHOSEN.** The expensive half (`DecisionContext`: PEP,
       capability view, sampler, approvals path) is built ONCE per (tenant, principal) at
       bring-up and frozen into a `SeamContext`. The cheap half (the wrapper: two attributes) is
       re-created per turn around the per-turn inner. `HelenaDispatcher` gains ONE field,
       `seam_context`, and `dispatch()` wraps its `_ScopedWhatsAppSender` with
       `gate_whatsapp(sender, ctx)` — one object allocation, no policy load, no I/O.

STEP 4 — WHY O4 HOLDS (the proof obligations, each with its test).
  * A-8: the principal lives in the frozen `SeamContext`, never in a call argument —
    `test_no_gated_wrapper_method_accepts_a_principal_or_tenant_argument`.
  * I-3: the raw recipient stays inside `_ScopedWhatsAppSender`, which is now the INNER of the
    wrapper. The gate therefore sees strictly LESS than the pre-Onda-1 code did —
    `test_live_dispatch_wrapping_never_exposes_the_raw_recipient`.
  * I-9: per turn it is one allocation plus dict lookups over an `lru_cache`d manifest —
    `test_per_turn_wrapping_builds_no_decision_context`.
  * Forward compatibility: if tenant ever DOES become per-request, the change is "build (and
    cache) a `SeamContext` per tenant", not a redesign — because the split already exists.

STEP 5 — GENERALIZE. The other four roots inject long-lived seams, so wrapper lifetime equals
context lifetime there and :func:`build_agent_seams` returns finished gated objects. Only the
webhook needs the per-turn re-wrap, and it takes it from the same frozen context.

DISCLOSED RESIDUAL. `HelenaDispatcher.seam_context` is `SeamContext | None` so the ~dozen unit
tests that construct a dispatcher directly keep working. The PRODUCTION root always supplies it —
`_build_dispatcher` raises when it cannot, degrading `/webhook` to its existing explicit 501,
the same refuse-to-serve shape a missing `DATABASE_URL` already triggers — and `dispatch()` logs
`helena_dispatch_whatsapp_seam_ungated` at error level if it is ever `None`. Pinned by
`test_production_webhook_root_always_supplies_a_seam_context`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Final

import structlog

from maezo.gateway.effect_pep import (
    PHI_ZONE_GENERAL,
    PHI_ZONES,
    AgentCapabilities,
    DecisionContext,
)
from maezo.gateway.seams import (
    EFFECT_SEAM_KEYS,
    GatedCibSevenTransport,
    GatedDelegationDispatcher,
    GatedDmnTransport,
    GatedFhirReader,
    GatedInferenceProvider,
    GatedPopulationFeatureClient,
    GatedWhatsAppSender,
    SeamContext,
    is_gated_seam,
)
from maezo.gateway.seams.a2a import gate_a2a
from maezo.gateway.seams.cibseven import gate_cibseven
from maezo.gateway.seams.dmn import gate_dmn
from maezo.gateway.seams.fhir import gate_fhir
from maezo.gateway.seams.inference import gate_inference
from maezo.gateway.seams.population import gate_population
from maezo.gateway.seams.whatsapp import gate_whatsapp

logger = structlog.get_logger(__name__)

#: The principal recorded for the worker daemon's `engine=`/`dmn=` seams and the notifications
#: bridge. A bounded token (`effect_pep._TOKEN_RE`), deliberately NOT an agent id: there is no
#: `spec/agents/worker_runtime/agent.yaml`, and inventing one to make the telemetry tidier would
#: be inventing a capability record. The honest consequence is spelled out in
#: :func:`build_worker_seam_context`.
WORKER_PRINCIPAL: Final[str] = "worker_runtime"

#: Same, for the notifications bridge daemon (`platform/integrations/notifications_bridge.py`).
BRIDGE_PRINCIPAL: Final[str] = "notifications_bridge"


# =================================================================================================
# Contexts — the expensive half, built ONCE per (tenant, principal)
# =================================================================================================


def build_agent_seam_context(
    *,
    tenant: str,
    agent_id: str,
    approvals_path: str | Path | None = None,
) -> SeamContext:
    """The frozen `(tenant, principal, phi_zone, DecisionContext)` an agent's seams decide under.

    Reads the principal's DECLARED capabilities from `spec/agents/<agent_id>/agent.yaml` (the
    `tools:` and `process_keys:` lists that design §0.1 found already exist as data and are
    unenforced at runtime) and its ADR-0006 `security_zone`. `AgentCapabilities.of` intersects the
    keys with the ADR-0016 universe, which is the R-1 closure.

    NON-FATAL BY DESIGN, FAIL-CLOSED BY EFFECT. A definition that will not load, or a `build_pep`
    that refuses (its documented fail-closed factory posture, ADR-0025 D5), leaves the
    corresponding leg `None` — and `None` DENIES at that layer (`CAPACIDADE_INDISPONIVEL`,
    `POLITICA_INDISPONIVEL`). Raising here instead would convert an unreadable policy file into a
    total outage of five daemons, for a gateway that today changes nothing: the wrong trade. The
    SEAM constructors below still raise if a seam cannot be wrapped (I-2) — that is a different
    failure and it keeps its loud shape.
    """
    capabilities: AgentCapabilities | None = None
    phi_zone = PHI_ZONE_GENERAL
    try:
        from maezo.agents import AgentLoader

        definition = AgentLoader().load_by_id(agent_id)
        capabilities = AgentCapabilities.of(
            principal=agent_id,
            tools=definition.tools,
            process_keys=definition.process_keys,
        )
        if definition.security_zone in PHI_ZONES:
            phi_zone = definition.security_zone
    except Exception:  # noqa: BLE001 - absence DENIES at L-1; it never widens permission
        logger.error("effect_seam_capabilities_unavailable", agent_id=agent_id, exc_info=True)

    return SeamContext(
        tenant=tenant,
        principal=agent_id,
        phi_zone=phi_zone,
        decision=DecisionContext(
            capabilities=capabilities,
            autonomy=_build_autonomy(tenant),
            # L-3 is INERT: no catalogued operation declares a money teto, so a resolver would be
            # dead weight on every call. The layer fails closed the moment one does.
            ceilings=None,
            # L-4 is INERT: `ConsentDecisionSource` is a port with no adapter (Q-5), and no class
            # is flagged `consentimento_exigido`.
            consent=None,
            approvals_path=approvals_path,
        ),
    )


def build_worker_seam_context(
    *,
    tenant: str,
    principal: str = WORKER_PRINCIPAL,
    approvals_path: str | Path | None = None,
) -> SeamContext:
    """The context the worker daemon's `engine=`/`dmn=` seams (and the bridge's) decide under.

    NO CAPABILITY VIEW, DELIBERATELY, AND THE TELEMETRY WILL SAY SO. A worker daemon is not an
    agent: there is no `agent.yaml` declaring its tools, so there is nothing to authorise against
    and `capabilities=None` makes every worker-seam call a would-DENY at L-1 with
    `CAPACIDADE_INDISPONIVEL`. That is the honest encoding — "this principal has no declared-
    capability record at all" — and it is deliberately DISTINCT from `TOOL_NAO_DECLARADA` ("this
    agent exists and did not declare this tool"), which is exactly the distinction design §5.3
    says an approver reading shadow telemetry must be able to make. Manufacturing a synthetic
    capability list to make the Phase-1 evidence look cleaner would be inventing a governance
    record, which no agent may do.

    The worker leg's OWN ratification telemetry is unaffected and remains the primary evidence for
    worker topics: `harness._evaluate_action_gate` (`tools/workers/harness.py:1576`) keys on the
    external-task topic and is untouched by this wave.
    """
    return SeamContext(
        tenant=tenant,
        principal=principal,
        phi_zone=PHI_ZONE_GENERAL,
        decision=DecisionContext(
            capabilities=None,
            autonomy=_build_autonomy(tenant),
            ceilings=None,
            consent=None,
            approvals_path=approvals_path,
        ),
    )


def _build_autonomy(tenant: str) -> Any:
    """`build_pep(tenant=…)` or None. Never raises — a refusing factory DENIES at L-2."""
    try:
        from maezo.gateway.pep import build_pep

        return build_pep(tenant=tenant)
    except Exception:  # noqa: BLE001 - `POLITICA_INDISPONIVEL` at L-2 is the fail-closed outcome
        logger.error("effect_seam_autonomy_unavailable", tenant=tenant, exc_info=True)
        return None


# =================================================================================================
# Per-seam factories — the cheap half. Concrete transports imported INSIDE (layering, §5.1).
# =================================================================================================


def build_dmn_seam(
    *,
    seam: SeamContext,
    base_url: str,
    auth_token: str | None = None,
    timeout: float | None = None,
) -> GatedDmnTransport:
    """A gated `CibSevenDmnTransport`. Construction is pure — no network until a node runs."""
    from maezo.tools.workers.dmn_transport import CibSevenDmnTransport

    kwargs: dict[str, Any] = {}
    if auth_token is not None:
        kwargs["auth_token"] = auth_token
    if timeout is not None:
        kwargs["timeout"] = timeout
    return gate_dmn(CibSevenDmnTransport(base_url, **kwargs), seam)


def build_cibseven_seam(
    *,
    seam: SeamContext,
    base_url: str,
    auth_token: str | None = None,
    timeout: float | None = None,
    fresh_client: bool = False,
) -> GatedCibSevenTransport:
    """A gated engine transport.

    `fresh_client=True` selects `FreshClientCibSevenTransport` — the residual ADR-0001 leg the
    worker daemon injects as `engine=` (`cibseven_engine.py:4-7`), which adversary A-2 names as
    the second, un-fenced engine path. It becomes the INNER of the gated decorator, exactly as
    §2's defeat clause specifies; its own allowlist entry in the start-process fence
    (`scripts/ci/check_start_process_fence.py:81`) is untouched.
    """
    kwargs: dict[str, Any] = {}
    if auth_token is not None:
        kwargs["auth_token"] = auth_token
    if timeout is not None:
        kwargs["timeout"] = timeout
    if fresh_client:
        from maezo.tools.workers.cibseven_engine import FreshClientCibSevenTransport

        return gate_cibseven(FreshClientCibSevenTransport(base_url, **kwargs), seam)
    from maezo.tools.mcp_cibseven.transport import CibSevenHttpTransport

    return gate_cibseven(CibSevenHttpTransport(base_url, **kwargs), seam)


def build_inference_seam(*, seam: SeamContext, inner: Any = None) -> GatedInferenceProvider:
    """A gated `InferenceProvider`. `inner=None` constructs one (the roots' current behaviour)."""
    if inner is None:
        from maezo.runtime.inference import InferenceProvider

        inner = InferenceProvider()
    return gate_inference(inner, seam)


def build_fhir_seam(*, seam: SeamContext, base_url: str, adapter: str = "read_patient") -> GatedFhirReader:
    """A gated FHIR reader over `FhirServer`.

    `adapter` selects which of the three in-repo shims wraps the generic client, preserving the
    per-agent choices `_build_tool_deps` already makes (ADR-0004 federated independence is why
    three near-identical adapters exist rather than one):
      * `read_patient`         -> `agents/rafael/adapters.py::FhirServerReader`
      * `read_patient_summary` -> `agents/valentina/adapters.py::FhirServerReader`

    9.5 (tool-name drift, docs-hygiene): `spec/agents/*/agent.yaml` declares granular tool ids
    like `mcp-fhir.read_patient`/`mcp-fhir.read_patient_summary` in their `tools:` lists — these
    are NOT literal MCP-registered tool names. `maezo.tools.mcp_fhir.server.FhirServer` only
    registers two tools (`read_resource`, `search_resources`); `adapter` (this parameter) is
    where the translation actually happens, invisibly to whoever reads only the agent.yaml side.
    """
    from maezo.tools.mcp_fhir.server import FhirServer, FhirSettings

    server = FhirServer(FhirSettings(base_url=base_url))
    if adapter == "read_patient_summary":
        from maezo.agents.valentina.adapters import FhirServerReader as ValentinaFhirServerReader

        return gate_fhir(ValentinaFhirServerReader(server), seam)
    from maezo.agents.rafael.adapters import FhirServerReader

    return gate_fhir(FhirServerReader(server), seam)


def build_whatsapp_seam(
    *, seam: SeamContext, inner: Any = None, adapter: str = "helena"
) -> GatedWhatsAppSender:
    """A gated WhatsApp sender.

    `inner` is the escape hatch the LIVE path needs: `platform/webhooks/whatsapp/dispatch.py`
    passes its per-turn `_ScopedWhatsAppSender` here (see the module docstring's O4). With
    `inner=None` the long-lived structural adapter is built instead, which is what the
    readiness-only roots want.
    """
    if inner is None:
        from maezo.tools.mcp_whatsapp.server import WhatsAppServer

        server = WhatsAppServer()
        if adapter == "lucas":
            from maezo.agents.lucas.adapters import WhatsAppServerSender as LucasWhatsAppServerSender

            inner = LucasWhatsAppServerSender(server)
        else:
            from maezo.agents.helena.adapters import WhatsAppServerSender

            inner = WhatsAppServerSender(server)
    return gate_whatsapp(inner, seam)


def build_a2a_seam(*, seam: SeamContext, inner: Any) -> GatedDelegationDispatcher:
    """A gated `DelegationDispatcher` over an already-assembled one."""
    return gate_a2a(inner, seam)


def build_population_seam(*, seam: SeamContext, inner: Any) -> GatedPopulationFeatureClient:
    """A gated `PopulationFeatureClient`. No concrete client exists yet (PORT-PENDING WB.4)."""
    return gate_population(inner, seam)


# =================================================================================================
# The agent-side composition entry point
# =================================================================================================

#: Which FHIR adapter each agent's graph needs, re-derived from `_build_tool_deps`'s own per-agent
#: branches. Absent => that agent's graph declares no FHIR seam and none is built.
_FHIR_ADAPTER_BY_AGENT: Final[dict[str, str]] = {
    "rafael": "read_patient",
    "carolina": "read_patient",
    "gustavo": "read_patient",
    "andre": "read_patient",
    "valentina": "read_patient_summary",
}

#: Which WhatsApp adapter each agent's graph needs (same source).
_WHATSAPP_ADAPTER_BY_AGENT: Final[dict[str, str]] = {
    "helena": "helena",
    "fernando": "helena",
    "lucas": "lucas",
}


def build_agent_seams(
    *,
    settings: Any,
    agent_id: str,
    inference: Any = None,
    approvals_path: str | Path | None = None,
) -> dict[str, Any]:
    """Build EVERY effect seam `agent_id`'s graph needs, GATED. The single sanctioned constructor.

    Returns the `tool_deps` mapping the composition roots feed to `build(config)` — the same keys
    `_build_tool_deps` has always returned, with every effect seam now a gated instance and
    `inference` added (the roots used to hand the raw provider in separately, which is precisely
    how `a2a_composition.py` ended up with two independent `InferenceProvider()` constructions —
    counterexample C-A2).

    `settings` is read DUCK-TYPED, not by class: the five roots carry four different settings
    types (`AgentRuntimeSettings`, `WhatsAppWebhookSettings`, `WorkerRuntimeSettings`,
    `NotificationsBridgeSettings`) with overlapping-but-unequal fields, and importing all four
    here would give `maezo.gateway` four new upward dependencies for no gain.

    Raises:
        Exception: propagated from a seam constructor. I-2 — a seam that cannot be wrapped must
            never silently degrade into an un-gated call; the roots already isolate build failures
            into a red readiness check, so the failure mode stays "replica not ready".
    """
    seam = build_agent_seam_context(
        tenant=getattr(settings, "tenant_id", "amh"), agent_id=agent_id, approvals_path=approvals_path
    )
    cibseven_base_url = getattr(settings, "cibseven_base_url", "")
    deps: dict[str, Any] = {
        "dmn": build_dmn_seam(seam=seam, base_url=cibseven_base_url),
        "cibseven": build_cibseven_seam(seam=seam, base_url=cibseven_base_url),
    }
    if inference is not None:
        # Only when the root actually has one. Omitting the key preserves `Harness.create_graph`'s
        # `config.setdefault("inference", self._inference)` behaviour EXACTLY (`harness.py:170`).
        deps["inference"] = build_inference_seam(seam=seam, inner=inference)

    database_url = getattr(settings, "database_url", None)
    if database_url:
        # NOT an effect seam and NOT gated: the audit sink is the thing effects are recorded TO
        # (ADR-0007), never an effect itself. Kept here so the roots keep ONE dep constructor.
        from maezo.gateway.audit_postgres import PostgresAuditSink

        deps["audit_sink"] = PostgresAuditSink(database_url, getattr(settings, "tenant_id", "amh"))

    whatsapp_adapter = _WHATSAPP_ADAPTER_BY_AGENT.get(agent_id)
    if whatsapp_adapter is not None:
        deps["whatsapp"] = build_whatsapp_seam(seam=seam, adapter=whatsapp_adapter)

    fhir_adapter = _FHIR_ADAPTER_BY_AGENT.get(agent_id)
    if fhir_adapter is not None:
        deps["fhir"] = build_fhir_seam(
            seam=seam,
            base_url=getattr(settings, "fhir_base_url", "http://hapi-fhir:8080/fhir"),
            adapter=fhir_adapter,
        )
    # `population` is deliberately ABSENT: the lake client is PORT-PENDING (WB.4) and Andre's
    # `build(config)` treats its absence as a disclosed gap note, never a failure. See
    # `gateway/seams/population.py` for why the wrapper ships anyway.
    return deps


# =================================================================================================
# The runtime half of inevitability (I-11 / §5.5): the boot assertion
# =================================================================================================


def gated_seam_violations(deps: Any) -> list[str]:
    """Every effect-seam key in `deps` whose value is NOT a gated instance. Total, never raises."""
    violations: list[str] = []
    try:
        items = dict(deps)
    except Exception:  # noqa: BLE001 - an unreadable dep map is itself a violation
        return ["<deps unreadable>"]
    for key in sorted(EFFECT_SEAM_KEYS):
        if key not in items:
            continue
        value = items[key]
        if value is None:
            continue
        if not is_gated_seam(value):
            violations.append(f"{key}={type(value).__name__}")
    return violations


def effect_seams_gated(deps: Any) -> tuple[bool, str]:
    """The `effect_seams_gated` readiness check's verdict + detail (design §5.5).

    Returns `(healthy, detail)` rather than a `CheckResult` so `maezo.gateway` keeps no dependency
    on `maezo.platform.health`; each root wraps it in its own `CheckResult`, red on failure and
    never fatal to liveness (the existing check family's posture,
    `agent_runtime/service.py:139-300`).

    NON-VACUOUS: a dep map with NO effect seam at all is UNHEALTHY, not trivially healthy. A check
    that passes because it found nothing to check is the shape of gate this repo has been bitten
    by before (`check_start_process_fence.py:155`'s non-vacuity counter exists for the same
    reason), and it is exactly what a bypass that removes a seam would look like.
    """
    try:
        items = dict(deps)
    except Exception:  # noqa: BLE001 - unreadable => red, never a silent green
        return False, "dep map unreadable"
    present = sorted(k for k in EFFECT_SEAM_KEYS if items.get(k) is not None)
    if not present:
        # A seam key that IS declared but carries None is a different fault from a dep map that
        # never mentioned a seam at all, and reporting both as "vacuous — nothing was gated" sent
        # an operator looking for a missing wiring line when the real story is a seam whose
        # CONSTRUCTION failed (every root builds these inside its own isolated try). Both stay
        # UNHEALTHY — this only makes the detail say which of the two it is.
        declared_none = sorted(k for k in EFFECT_SEAM_KEYS if k in items and items[k] is None)
        if declared_none:
            return False, (
                f"effect seam(s) declared but None: {', '.join(declared_none)} — "
                "nothing was gated (a seam that failed to build is not a seam that is absent)"
            )
        return False, "no effect seam present in the dep map (vacuous — nothing was gated)"
    violations = gated_seam_violations(items)
    if violations:
        return False, f"UNGATED effect seams: {', '.join(violations)}"
    return True, f"gated={','.join(present)}"


__all__ = [
    "BRIDGE_PRINCIPAL",
    "EFFECT_SEAM_KEYS",
    "WORKER_PRINCIPAL",
    "SeamContext",
    "build_a2a_seam",
    "build_agent_seam_context",
    "build_agent_seams",
    "build_cibseven_seam",
    "build_dmn_seam",
    "build_fhir_seam",
    "build_inference_seam",
    "build_population_seam",
    "build_whatsapp_seam",
    "build_worker_seam_context",
    "effect_seams_gated",
    "gated_seam_violations",
    "is_gated_seam",
]

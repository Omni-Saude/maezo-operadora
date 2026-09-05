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

from maezo.gateway.credential_vault import (
    AgentCredentialView,
    CredentialVault,
    HumanCredentialPartition,
)
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
# Credentials — ADR-0005 mechanism #3, wired HERE because this is the ONE sanctioned constructor
# =================================================================================================
#
# WHY THIS SECTION EXISTS (gap AF-14). `gateway/credential_vault.py` implemented ADR-0005's THIRD
# HITL mechanism — "a credencial da acao proibida nao existe no runtime do agente"
# (`docs/adr/0005-hitl-architectural-guarantee.md:12`) — and then nothing in `src/` ever
# constructed a `CredentialVault`. The only mentions outside its defining module were the
# re-exports in `gateway/__init__.py`. A structural guarantee with no composition root is not a
# guarantee: it is a class that PASSES ITS OWN UNIT TESTS while every real credential in the tree
# reaches a seam by an unaudited `getattr(settings, ...)` that no partition table governs.
#
# THE HONEST OPTION, AND WHY IT IS WIRING RATHER THAN DELETION. Deleting the vault was the other
# candidate. It was rejected because ADR-0005 is ACCEPTED and names this mechanism as one of four
# INDEPENDENT ones; dropping the only code that encodes it would leave mechanism #3 with no
# artefact at all, which is a governance regression, not a cleanup. So the vault becomes the ONE
# path by which this module turns a settings surface into a credential a seam may use.
#
# WHAT IS AND IS NOT CLAIMED. This does NOT claim that a NEGATIVA signing key exists today — none
# is injected anywhere in `deploy/` (checked), and inventing one would be inventing a governance
# record. What it claims, and what the tests prove, is STRUCTURAL: a credential whose settings
# field is in :data:`HUMAN_CREDENTIAL_FIELDS` is routed into its `HumanCredentialPartition` and
# CANNOT appear in the `AgentCredentialView` the seams are built from, and a composition that
# would place one in the agent scope RAISES at build time (I-2's shape — a red readiness check,
# never a replica that is ready and leaking).

#: Settings fields that carry a credential an AGENT is allowed to hold, mapped to the vault key it
#: is stored under. Deliberately a CLOSED table and not "every field whose name looks secret":
#: a name-pattern rule would silently widen the moment someone adds a field, which is the failure
#: mode this table exists to prevent.
#:
#: EVERY ENTRY RESOLVES TO A REAL FIELD, and that is now a TEST rather than a claim
#: (`test_todo_campo_agente_visivel_existe_em_alguma_classe_de_settings_real`):
#: `worker_runtime.settings.WorkerRuntimeSettings.cibseven_auth_token` (also on
#: `platform.integrations.notifications_bridge.NotificationsBridgeSettings`),
#: `runtime.agent_runtime.settings.AgentRuntimeSettings.llm_phi_api_key` / `llm_general_api_key`,
#: `platform.webhooks.whatsapp.settings.WhatsAppWebhookSettings.whatsapp_token`. A fifth entry,
#: `fhir_auth_token`, was listed here and existed NOWHERE in `src/` — a row that could never carry
#: a credential, asserted as though it could (AF-14-F2). Removed: the FHIR seams take their
#: adapters already constructed, so no root reads such a field. A future FHIR credential is added
#: here TOGETHER with the settings field, and the test above is what forces the pair.
AGENT_CREDENTIAL_FIELDS: Final[dict[str, str]] = {
    "cibseven_auth_token": "cibseven_auth_token",
    "llm_phi_api_key": "llm_phi_api_key",
    "llm_general_api_key": "llm_general_api_key",
    "whatsapp_token": "whatsapp_token",
}

#: Settings fields that carry a HUMAN-RESTRICTED credential, and the ADR-0005 partition each one
#: belongs to. NO field here is on any settings class today, and that is the point: the table is
#: the DESTINATION declared in advance, so the first deployment that injects a denial-signing key
#: routes it into the human partition by construction instead of by a reviewer noticing.
HUMAN_CREDENTIAL_FIELDS: Final[dict[str, HumanCredentialPartition]] = {
    "negativa_assinatura_key": HumanCredentialPartition.NEGATIVA,
    "fraude_investigacao_key": HumanCredentialPartition.FRAUDE,
}


class CredentialSeparationError(RuntimeError):
    """A composition that would hand an agent a human-restricted credential (ADR-0005 #3).

    Raised at BUILD time, never at call time, and deliberately not caught anywhere in this module:
    the roots already isolate seam-construction failures into a red readiness check, so the
    failure mode stays "replica not ready" rather than "replica ready and leaking a denial key".
    """


def build_credential_vault(*, settings: Any, agent_id: str) -> CredentialVault:
    """Load `settings`'s credentials into a vault, partitioned per ADR-0005 mechanism #3.

    `settings` is read DUCK-TYPED, exactly as :func:`build_agent_seams` reads it — the five roots
    carry four different settings types and this module must not import any of them. A field that
    is absent, `None`, or empty is simply NOT stored: "no credential injected" is a real, honest
    deployment state (`worker_runtime.settings.cibseven_auth_token_value`'s own words: "or None if
    not injected (blocked seam)"), and storing an empty string would turn it into a credential
    that exists and authenticates nothing.

    Args:
        settings: any root's settings object.
        agent_id: the principal the agent-scoped credentials are stored under.

    Returns:
        A `CredentialVault` holding the agent scope AND any human partitions this settings surface
        carries. The vault is NEVER handed to a seam — only :func:`build_agent_credential_view`'s
        result is (that is the whole separation).

    Raises:
        CredentialSeparationError: if a field appears in BOTH tables. That can only happen through
            an edit to this module, and it must fail loudly at build rather than resolve to
            whichever table happens to be consulted first.
    """
    overlap = sorted(set(AGENT_CREDENTIAL_FIELDS) & set(HUMAN_CREDENTIAL_FIELDS))
    if overlap:
        raise CredentialSeparationError(
            "credential field(s) declared BOTH agent-visible and human-restricted: "
            f"{', '.join(overlap)} — ADR-0005 mechanism #3 admits no field that is both."
        )
    vault = CredentialVault()
    for field, key in sorted(AGENT_CREDENTIAL_FIELDS.items()):
        value = getattr(settings, field, None)
        if not value or not isinstance(value, str):
            continue
        vault.store_agent_credential(agent_id=agent_id, level="runtime", key=key, value=value)
    for field, partition in sorted(HUMAN_CREDENTIAL_FIELDS.items(), key=lambda item: item[0]):
        value = getattr(settings, field, None)
        if not value or not isinstance(value, str):
            continue
        vault.store_human_credential(partition=partition, key=field, value=value)
    return vault


def build_agent_credential_view(*, settings: Any, agent_id: str) -> AgentCredentialView:
    """The ONLY credential surface a seam constructor may read (AF-14).

    Returns the vault's `AgentCredentialView` for `agent_id` — a read-only object with no
    reference back to the vault, so there is no attribute path from a seam to
    `CredentialVault.get_human_credential`.

    Raises:
        CredentialSeparationError: if any human-restricted key is nonetheless visible in the view.
            The check is redundant against `CredentialVault`'s own structure and is kept anyway:
            it is the assertion that goes RED if a future edit "simplifies" the vault into one
            flat dict, which is precisely how mechanism #3 would be lost silently.
    """
    vault = build_credential_vault(settings=settings, agent_id=agent_id)
    view = vault.get_agent_view(agent_id=agent_id)
    leaked = sorted(set(view.list_keys()) & set(HUMAN_CREDENTIAL_FIELDS))
    if leaked:
        raise CredentialSeparationError(
            f"agent {agent_id!r} would see human-restricted credential(s): {', '.join(leaked)} — "
            "ADR-0005 mechanism #3 (a credencial da acao proibida nao existe no runtime do agente)."
        )
    return view


def build_worker_credential_view(*, settings: Any, principal: str = WORKER_PRINCIPAL) -> AgentCredentialView:
    """The same surface for the daemon principals (`worker_runtime`, `notifications_bridge`).

    A worker daemon is not an agent (:func:`build_worker_seam_context` explains why it has no
    capability record), but ADR-0005 mechanism #3 is about the RUNTIME, not about the principal's
    kind: a denial-signing key must not exist in the worker's runtime either. Same vault, same
    closed tables, same view type — one credential vocabulary rather than two.

    BOTH daemon principals really call it (AF-14-F1): `runtime/worker_runtime/service.py::
    _engine_credential` and `platform/integrations/notifications_bridge.py::_engine_credential`.
    The first version of this docstring named the bridge before the bridge was wired; the tests in
    `test_credential_vault_composition.py` now cover each root.

    THE EXACT SURFACE, counted rather than asserted (`grep -rn 'build_cibseven_seam(' src`,
    `grep -rn 'build_dmn_seam(' src`, `grep -rn 'auth_token=' src`): `src/` holds FOUR gated
    engine-seam constructions and one
    raw engine transport. Three of the four carry a credential — :func:`build_agent_seams` and the
    two daemon `_engine_credential`s — and all three read it here. The fourth,
    `platform/evidence/dmn_sweep.py::main`, passes NO `auth_token` at all: it is an unauthenticated
    diagnostic CLI against a dev engine, so there is no credential for a partition table to govern.
    The raw one, `worker_runtime/service.py`'s `CibSevenWorkerTransport`, is not a gated seam (it
    decides nothing) but carries the same credential and therefore also reads it here. What is
    claimed is exactly what the fence
    `test_credential_vault_composition.py::test_nenhuma_leitura_da_credencial_do_motor_escapa_do_cofre_em_src`
    asserts by AST over all of `src/`: no LITERAL read of that field — attribute access,
    `getattr`/`hasattr` with a literal name, or a constant-string subscript (so also via
    `model_dump()` / `__dict__`) — exists outside the one exempt (module, function) pair,
    `worker_runtime/settings.py::cibseven_auth_token_value`. The RESIDUAL, stated rather than
    papered over: a field name built or chosen at RUNTIME is invisible to that fence, deliberately,
    because a dynamic `getattr` is how :func:`build_credential_vault` reads this very table — a
    fence that forbade it would forbid the vault.
    """
    return build_agent_credential_view(settings=settings, agent_id=principal)


def agent_credential(view: AgentCredentialView, key: str) -> str | None:
    """`view[key]` or None when this deployment injected no such credential.

    NOT a silent fallback: absence is the documented blocked-seam state, it is reported by the
    seam's own transport (an unauthenticated engine call fails at the engine, loudly), and the
    alternative — substituting a default token — is the fabrication this repo forbids.
    """
    try:
        return view.get(key)
    except KeyError:
        return None


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


def whatsapp_adapter_for(agent_id: str) -> str | None:
    """The WhatsApp adapter `agent_id`'s graph needs, or `None` if it declares no WhatsApp seam.

    THE ONE DEFINITION of the agent->adapter choice, readable by roots that build a single
    agent's seam by hand instead of going through :func:`build_agent_seams`. Without it a caller
    has to pass `build_whatsapp_seam` a literal, and `build_whatsapp_seam` branches ONLY on
    `"lucas"` — every other string (including an agent id that is NOT an adapter id, such as
    `"fernando"`) falls to Helena's sender through an unguarded `else`. That is behaviourally
    right TODAY and silently wrong the day a branch is added or the default changes: two
    construction paths for one decision, the counterexample C-A2 this module exists to prevent.
    """
    return _WHATSAPP_ADAPTER_BY_AGENT.get(agent_id)


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
        CredentialSeparationError: propagated from :func:`build_agent_credential_view` when the
            settings surface would put a human-restricted credential in the agent scope
            (ADR-0005 mechanism #3). Same shape, same reason: refusing to build is the only
            outcome that keeps "the credential does not exist in the agent's runtime" true.
    """
    seam = build_agent_seam_context(
        tenant=getattr(settings, "tenant_id", "amh"), agent_id=agent_id, approvals_path=approvals_path
    )
    # AF-14 / ADR-0005 #3. Every credential this root hands a seam comes from the vault's AGENT
    # view — the seams never see the `CredentialVault`, so no seam can reach a human partition.
    # A settings surface that carried a NEGATIVA/FRAUDE key would have raised inside
    # `build_agent_credential_view`, which propagates exactly like a failed seam constructor (I-2).
    credentials = build_agent_credential_view(settings=settings, agent_id=agent_id)
    engine_token = agent_credential(credentials, "cibseven_auth_token")
    cibseven_base_url = getattr(settings, "cibseven_base_url", "")
    deps: dict[str, Any] = {
        "dmn": build_dmn_seam(seam=seam, base_url=cibseven_base_url, auth_token=engine_token),
        "cibseven": build_cibseven_seam(seam=seam, base_url=cibseven_base_url, auth_token=engine_token),
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

    whatsapp_adapter = whatsapp_adapter_for(agent_id)
    if whatsapp_adapter is not None:
        deps["whatsapp"] = build_whatsapp_seam(seam=seam, adapter=whatsapp_adapter)

    fhir_seam = build_agent_fhir_seam(settings=settings, agent_id=agent_id, seam=seam)
    if fhir_seam is not None:
        deps["fhir"] = fhir_seam
    # `population` is deliberately ABSENT: the lake client is PORT-PENDING (WB.4) and Andre's
    # `build(config)` treats its absence as a disclosed gap note, never a failure. See
    # `gateway/seams/population.py` for why the wrapper ships anyway.
    return deps


def build_agent_fhir_seam(
    *,
    settings: Any,
    agent_id: str,
    seam: SeamContext | None = None,
) -> GatedFhirReader | None:
    """`agent_id`'s GATED FHIR reader — the same one `build_agent_seams` puts under `"fhir"`,
    addressable on its own. `None` when the agent declares no FHIR adapter.

    Extracted (CC-03/AND-03) because a root can legitimately need ONE agent's FHIR seam without
    paying for the rest of that agent's dep map: `worker_runtime/service.py` STEP B needs
    Carolina's and Andre's readers for the dossier delegation edges, while its `dmn`/`cibseven`/
    `audit_sink` come from the DAEMON's own principal — calling `build_agent_seams` twice there
    would have opened two extra `PostgresAuditSink`s and two duplicate engine transports for
    seams the root already owns. `build_agent_seams` now delegates here, so the adapter choice
    and the base-url default keep exactly ONE definition (no second, divergent construction
    path — the very C-A2 counterexample this module exists to prevent).

    PER-AGENT, NEVER SHARED. The wrapper closes over `SeamContext.principal`, and
    `leitura_phi_clinica` (C2) is decided PER PRINCIPAL: handing Carolina's instance to Andre
    would record and decide his PHI read under HER declared-capability record. `seam` is the
    caller's already-built context (what `build_agent_seams` passes, so it keeps paying for one
    PEP parse per call, not two); absent, one is built for `agent_id`.
    """
    adapter = _FHIR_ADAPTER_BY_AGENT.get(agent_id)
    if adapter is None:
        return None
    context = seam or build_agent_seam_context(
        tenant=getattr(settings, "tenant_id", "amh"), agent_id=agent_id
    )
    return build_fhir_seam(
        seam=context,
        base_url=getattr(settings, "fhir_base_url", "http://hapi-fhir:8080/fhir"),
        adapter=adapter,
    )


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
    "AGENT_CREDENTIAL_FIELDS",
    "BRIDGE_PRINCIPAL",
    "EFFECT_SEAM_KEYS",
    "HUMAN_CREDENTIAL_FIELDS",
    "WORKER_PRINCIPAL",
    "CredentialSeparationError",
    "SeamContext",
    "agent_credential",
    "build_a2a_seam",
    "build_agent_credential_view",
    "build_agent_fhir_seam",
    "build_agent_seam_context",
    "build_agent_seams",
    "build_cibseven_seam",
    "build_dmn_seam",
    "build_fhir_seam",
    "build_inference_seam",
    "build_population_seam",
    "build_credential_vault",
    "build_whatsapp_seam",
    "build_worker_credential_view",
    "build_worker_seam_context",
    "effect_seams_gated",
    "gated_seam_violations",
    "is_gated_seam",
]

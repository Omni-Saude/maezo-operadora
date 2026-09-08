"""A2A composition root — the Option-A, in-process Helena->Rafael delegation edge (T2.4 A2A W3).

`docs/design/A2A-dispatcher-card-signing.md` §9.2 ("the honest nuance"): `agent_runtime/service.py`
is a health-only daemon that executes no turns — there is no live Kafka-consume loop and no
cross-pod delegation consumer in this build. `build_auth_delegation_dispatcher` below assembles the
SINGLE proof-of-life edge (Helena originates `authorization.analyze`, Rafael's real graph consumes
it) as **Option A: in-process co-located** (donor `assembly.py:14-17` — remote cross-pod A2A
transport is explicitly out of scope, S5/S8). The live `await dispatcher.delegate(envelope)` call is
driven by the W3 test suite / a thin driver, never by this module or a supervised daemon loop —
this module only proves the edge *assembles*.

Dependency reuse (design §9.2 / risk 2): Rafael's `dmn`/`cibseven`/`audit_sink`/`fhir` come from
`agent_runtime.service._build_tool_deps` — the SAME construction `_load_agent_graph` already uses
for the `graph_loaded` readiness check — so this is not a second, divergent dep-construction path.
`audit_sink` doubles as BOTH Rafael's own process-start fence (T-C2) and the dispatcher's delegation
audit (T-F): `maezo.a2a.dispatcher.AuditEmitter`'s own docstring says the two seams "are satisfied
by the SAME production sink ... share an implementation today" — this module takes that at face
value rather than opening a second `PostgresAuditSink`/asyncpg pool for the same tenant.

**FACTS: the no-op is no longer the default (Onda 3 / Train C leg 2).** This module used to wire
BOTH roots as `FactProducer(kafka_producer or _NoopKafkaProducer())`, whose `send` is `return
None` — so every `agents.events.delegation.*` fact this platform ever produced was DROPPED at the
instant of emission. Facts are an OBSERVABILITY surface and never the T-F audit (that rides the
real `PostgresAuditSink` above, and `_execute` audits BEFORE it emits), so no audit proof was ever
at risk; the ADR-0003 observability surface, however, was fiction. `_require_fact_producer_or_
fail_closed` replaces the default with a TRANSACTIONAL OUTBOX (`maezo.a2a.outbox`, migration
`0008_a2a_fact_outbox.py`): facts become durable rows drained at-least-once by the explicitly-
invoked `maezo.a2a.outbox_relay`, never auto-started. The no-op survives for exactly one case —
EXPLICIT local runtime mode with no `DATABASE_URL` — and is refused everywhere else. There is
still no production Kafka producer constructed here: delivery is the relay's job and is deployment-
gated, which is why the write side needed a durable buffer rather than a broker client.

**PLAN-W3-A2A atomicity.** Both roots require compatible PostgreSQL audit,
idempotency and outbox participants in production, including injected values.
Claim + ALLOW + requested + marker share one transaction; terminal audit + fact
+ done share another. The handler and all external work run outside both.

**T-G signed-Card enforcement (`docs/design/A2A-dispatcher-card-signing.md` §10).** W3 left this
composition wired with NO `verifier=`: Cards came back unsigned and the registry admitted them
unconditionally. This flips it: `_require_signer_or_fail_closed` resolves the vault/KMS-injected
signing key through a PER-TENANT `TenantKeyset` (`maezo.a2a.keyset`; ADR-0039 §4.4, owner decision
4), `card_signer_from_key` builds a `CardSigner` from it, and the SAME signer instance both signs
the Cards (`build_agent_cards(..., signer=signer)`) and gates the registry
(`build_dispatcher(..., verifier=signer)`) — symmetric HMAC, one key does both jobs. Key present ->
every Card is signed AND the registry fail-closes (`CardSignatureError`) on anything not validly
signed under that exact key — an unsigned, tampered, or wrong-key Card never reaches `register()`
successfully, so this function raises before a dispatcher object is ever returned (fail-closed:
there is nothing to `.delegate()` against).

**PER-TENANT key custody (ADR-0039 §4.4, decision 4 — leg E2).** The signing key is no longer the
bare repo-wide `MAEZO_A2A_CARD_SIGNING_KEY`: each tenant's key is resolved from its own namespaced
variable `MAEZO_A2A_CARD_SIGNING_KEY__<TENANT>` (`per_tenant_key_env_var`) via `EnvTenantKeyset` —
the SAME vault/KMS delivery seam, now scoped per tenant. A missing tenant key NEVER falls back to
the repo-wide key or another tenant's key (that is the cross-tenant key-confusion attack decision 4
prevents); it is treated as an absent key and fail-closes below. This is the single per-tenant
resolution point envelope signing (leg E3) will also resolve its key through.

**F2 — no silent fail-open when the key is absent.** The earlier W1 dev fail-safe SILENTLY
downgraded to unsigned Cards + no verifier whenever the key was unset — which in production would
have admitted ANY (unsigned/forged) Card. `_require_signer_or_fail_closed` closes that: an absent
(per-tenant) key RAISES at composition in production runtime mode (`agent_runtime_mode != "local"`,
un-bypassable), and in a non-production runtime proceeds unsigned ONLY behind the EXPLICIT
`MAEZO_A2A_ALLOW_UNSIGNED_CARDS` opt-out (loudly warned), never as a silent default. Real key
provisioning in vault/KMS remains an external/infra dependency (design doc §6.2).
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Any, Protocol, TypeGuard

import structlog

from maezo.a2a import (
    AgentHandler,
    CardSigner,
    DelegationDispatcher,
    FactProducer,
    PostgresIdempotencyStore,
    build_agent_cards,
    build_dispatcher,
)
from maezo.a2a.assembly import card_signer_from_key
from maezo.a2a.dispatcher import KafkaLike
from maezo.a2a.envelope_signing import (
    DEFAULT_REPLAY_EPOCH,
    MAX_SIGNATURE_AGE,
    EnvelopeSigner,
    EnvelopeVerifier,
    build_verification_keyset,
)
from maezo.a2a.idempotency import IdempotencyStore
from maezo.a2a.keyset import EnvTenantKeyset, TenantKeyset, per_tenant_key_env_var
from maezo.a2a.outbox import PostgresOutboxFactProducer, build_outbox_fact_producer
from maezo.a2a.transaction import PostgresDelegationTransactions
from maezo.agents.andre.delegation import make_andre_handler
from maezo.agents.andre.graph import PopulationFeatureClient
from maezo.agents.carolina.delegation import make_carolina_handler
from maezo.agents.fernando.delegation import make_fernando_handler
from maezo.agents.rafael.delegation import make_rafael_handler
from maezo.gateway.audit_postgres import FreshSinkAuditEmitter, PostgresAuditSink
from maezo.gateway.tool_registry import (
    build_a2a_seam,
    build_agent_seam_context,
    build_inference_seam,
    build_whatsapp_seam,
    build_worker_seam_context,
    whatsapp_adapter_for,
)
from maezo.runtime.inference import InferenceProvider
from maezo.tools.mcp_cibseven.transport import AuditStartSink, CibSevenTransport
from maezo.tools.workers.dmn_transport import DmnTransport

from .service import _build_tool_deps
from .settings import AgentRuntimeSettings

logger = structlog.get_logger(__name__)

#: The two agents party to this one edge (design doc §5/§9 — Helena originates, Rafael targets).
_EDGE_AGENT_IDS = ("helena", "rafael")

#: The two TARGET agents of the worker-originated dossier edges (DL-0033 real wiring). Carolina
#: serves ONE edge; Andre serves TWO, both on the SHARED `analytics.population` task_type,
#: disambiguated by ORIGIN into his flow (`agents/andre/delegation.py::_flow_for`):
#:   `operadora.cred.prepare_dossier`               -> Carolina (`credentialing.analyze`)
#:   `operadora.adequacao.prepare_remediation_dossier` -> Andre, `adequacao-worker` origin ->
#:        his `adequacao_dossier` flow
#:   `operadora.pagto.prepare_approval_dossier`     -> Andre, `pagto-worker` origin -> his DEFAULT
#:        `pagto_dossier` flow (added this wave — the AGENT set is unchanged, Andre's handler and
#:        card already accept the shared type; only a THIRD origin now routes to him)
#:   `operadora.inadimplencia.prepare_dossier`     -> Fernando (`arrears.followup`), added by
#:        owner decision R-081 (gap `FERNANDO-DELEGATION-CALL-SITE`, approved 2026-09-04:
#:        "SIM — ligar o call site em `inadimplencia.py::prepare_dossier` e registrar
#:        `make_fernando_handler` em `a2a_composition.py`, com a chamada fail-neutral"). BOTH
#:        halves landed: the handler is registered here and the worker
#:        (`tools/workers/inadimplencia.py::make_prepare_dossier_handler`) originates the
#:        envelope fail-neutrally — see `build_dossier_delegation_dispatcher`'s docstring.
#: The ORIGINS are workers (`credenciamento-worker`/`adequacao-worker`/`pagto-worker`/
#: `inadimplencia-worker`), not agents — the dispatcher validates only the TARGET's Card, so no
#: origin card exists or is needed.
_DOSSIER_EDGE_AGENT_IDS = ("carolina", "andre", "fernando")

#: The subset of `_DOSSIER_EDGE_AGENT_IDS` whose graph declares a FHIR reader seam (CC-03/AND-03).
#: Fernando's `build(config)` has NO `fhir` key at all (`agents/fernando/graph.py`), so accepting
#: `fhir={"fernando": reader}` would silently swallow a reader nothing can consume — the same
#: species of silent-degradation defect the unknown-key check below exists to refuse.
_DOSSIER_EDGE_FHIR_AGENT_IDS = ("carolina", "andre")


def _require_whatsapp_adapter_for(agent_id: str) -> str:
    """`whatsapp_adapter_for(agent_id)`, FAIL-CLOSED when the map does not name the agent.

    `build_whatsapp_seam`'s `adapter` parameter DEFAULTS to `"helena"` and its only branch is
    `== "lucas"`, so an unmapped agent would silently receive Helena's sender. On this root that
    would be a fabricated seam for a graph whose `build(config)` fail-closes precisely to avoid
    one — so an agent that this repo's single agent->adapter map does not name is a refusal to
    assemble, never a default.
    """
    adapter = whatsapp_adapter_for(agent_id)
    if adapter is None:
        raise ValueError(
            f"cannot assemble the A2A dossier delegation edges: {agent_id!r} declares no WhatsApp "
            "adapter in `gateway.tool_registry._WHATSAPP_ADAPTER_BY_AGENT`, and defaulting one "
            "would hand a graph a sender the map never chose for it"
        )
    return adapter


class FhirSummaryReader(Protocol):
    """The ONE shape both dossier targets' FHIR seams have (CC-03/AND-03).

    `carolina/graph.py::SummaryReader` and `andre/graph.py::PatientSummaryReader` are DIFFERENT
    Protocols with IDENTICAL members — exactly `async read_patient(patient_id) -> dict`. Naming
    that fact once here is what lets this root type ONE `fhir` map instead of two parameters,
    without claiming the two agents may share an INSTANCE (they may not — the gate's principal
    differs; see `build_dossier_delegation_dispatcher`). Satisfied structurally by the sanctioned
    `gateway/seams/fhir.py::GatedFhirReader`, whose inner is `rafael/adapters.py::FhirServerReader`.
    """

    async def read_patient(self, patient_id: str) -> dict[str, Any]: ...


#: The ONLY non-production `agent_runtime_mode`, and it must be set EXPLICITLY (ADR-0039 Q7 flipped
#: `settings.py`'s default from "local" to the fail-closed "production"). Helm injects "kubernetes"
#: for the deployed daemon (`deployment-agent-runtime.yaml`), so ANY value other than "local" is
#: treated as production — an unrecognized/misconfigured/ABSENT mode fails CLOSED, never open.
_LOCAL_RUNTIME_MODE = "local"

#: EXPLICIT, non-production-only opt-out permitting UNSIGNED Agent Cards when no signing key is
#: present. This exists purely for dev/test ergonomics (running the edge without provisioning a
#: vault/KMS key); it is IGNORED in production runtime mode, where an absent key ALWAYS fail-closes
#: (see `_require_signer_or_fail_closed`). It must NEVER default to on — the absence of a key must
#: never SILENTLY downgrade to unsigned Cards (T-G, ADR-0003/0007).
ALLOW_UNSIGNED_CARDS_ENV_VAR = "MAEZO_A2A_ALLOW_UNSIGNED_CARDS"

#: EXPLICIT, non-production-only opt-out permitting UNVERIFIED delegation ENVELOPES when no
#: per-tenant envelope-signing key is present (ADR-0039 §4.4, owner decision 6 — name CONFIRMED
#: verbatim). The envelope-signing twin of `ALLOW_UNSIGNED_CARDS_ENV_VAR`: dev/test ergonomics only,
#: IGNORED in production runtime mode (where an absent key ALWAYS fail-closes), and it reuses the
#: SAME shared `is_production_runtime_mode` discriminator (no second answer to "is this production?").
#: It must NEVER default to on — the absence of a key must never silently downgrade to unverified
#: envelopes. It appears in NO production-permissive default.
ALLOW_UNVERIFIED_ENVELOPES_ENV_VAR = "MAEZO_A2A_ALLOW_UNVERIFIED_ENVELOPES"

_TRUTHY = frozenset({"1", "true", "yes", "on"})

#: Worker-daemon prod/dev discriminator for the dossier edge's signer gate. Reuses the GENERIC
#: `RUNTIME_MODE` env contract already established by the webhook receiver
#: (`platform/webhooks/whatsapp/settings.py`; its Helm template injects "kubernetes"). The
#: worker-daemon Helm template (`deployment-worker-daemon.yaml`) injects NO mode variable, so —
#: unlike the webhook receiver's settings default of "local" — the resolution below FAILS CLOSED:
#: absent/blank resolves to "production" (any value != "local" is production for
#: `_require_signer_or_fail_closed`). Dev opts into local EXPLICITLY with `RUNTIME_MODE=local`;
#: the absence of configuration must never silently become the permissive mode.
WORKER_RUNTIME_MODE_ENV_VAR = "RUNTIME_MODE"


def worker_runtime_mode_from_env() -> str:
    """Resolve the worker-daemon runtime mode (see `WORKER_RUNTIME_MODE_ENV_VAR` — fail-closed)."""
    return os.environ.get(WORKER_RUNTIME_MODE_ENV_VAR, "").strip() or "production"


def is_production_runtime_mode(runtime_mode: str) -> bool:
    """The ONE prod/dev discriminator every fail-closed gate in this module shares.

    Extracted (Onda 3 / Train C) from `_require_signer_or_fail_closed`, which had this comparison
    inline, so the outbox gate below — and the idempotency-store gate that follows it — cannot
    drift into a second, subtly different answer to "is this production?". The rule is unchanged
    and stays FAIL-CLOSED: only the literal `"local"` is non-production, so an unrecognized,
    misconfigured or blank-but-present mode is production.

    Both discriminators feeding this now AGREE on an absent variable (ADR-0039 Q7 — the asymmetry
    this docstring used to disclose is REPAIRED, at its source in `settings.py`, not here):

      * worker-runtime, `worker_runtime_mode_from_env()`: absent OR blank `RUNTIME_MODE` ->
        `"production"` -> True (the Helm worker-daemon template injects no mode variable).
      * agent-runtime, `AgentRuntimeSettings.agent_runtime_mode`: ABSENT `AGENT_RUNTIME_MODE` ->
        the pydantic default `"production"` -> True; PRESENT-but-EMPTY -> `""` -> True.

    So on BOTH paths an absent variable means production, and reaching the permissive branch takes
    an explicit `local`. The repair landed on `settings.py`'s `Field(default=...)` — the surface
    that actually decides it — rather than being special-cased inside this helper, because a gate
    helper quietly overriding its caller's settings value is how a "safe tightening" becomes an
    invisible behavior change. Note the two paths still differ on BLANK-but-present: the worker
    resolver strips it to `"production"`, while the agent settings value reaches here as `""`.
    Both are production, so the VERDICT is identical; only the string in the error message differs.
    """
    return runtime_mode != _LOCAL_RUNTIME_MODE


def _dsn_is_present(database_url: str | None) -> TypeGuard[str]:
    """The ONE "is there a usable DSN?" answer both durability gates share.

    Extracted for the SAME reason as `is_production_runtime_mode` above: the fact gate and the
    idempotency gate must reach identical build/refuse verdicts on every input, or a composition
    root can half-refuse (one gate building, the other raising). Both inputs of that shared verdict
    are therefore shared helpers, not inlined expressions.

    LEGIBILITY HARDENING (Onda-3 leg-4 FINDING, closed here): this used to be a bare `if
    database_url:` truthiness check, so a whitespace-only `DATABASE_URL` (`"   "`) counted as
    PRESENT and the gates built a store/outbox pointed at garbage instead of returning the legible
    refusal. That was never a security fail-OPEN — asyncpg cannot dial `"   "`, so it failed CLOSED
    at first connect — but the operator got an opaque connect error at first delegation rather than
    the 3am-legible "provide DATABASE_URL" refusal at composition time. Blank-after-strip is now
    treated exactly like absent, which is also what makes `("production", "   ")` a REFUSAL on both
    gates rather than a disagreement between them.

    NOT a normalizer: a non-blank DSN is passed through to the store BYTE-UNCHANGED (padding
    included). This helper only decides presence; rewriting the operator's DSN is a different
    change with a different blast radius.

    Typed as a `TypeGuard[str]` so the truthy branch narrows `str | None` to `str` for the callers,
    exactly as the inlined `if database_url:` did. Without it, extracting the check into a function
    would have LOST that narrowing and forced a `cast`/`assert` at both call sites — silencing the
    type checker to keep a fail-closed gate is the wrong trade.
    """
    return bool(database_url and database_url.strip())


def _unsigned_cards_opt_out() -> bool:
    """True only if the EXPLICIT unsigned-cards opt-out env var is set to a truthy value."""
    return os.environ.get(ALLOW_UNSIGNED_CARDS_ENV_VAR, "").strip().lower() in _TRUTHY


def _unverified_envelopes_opt_out() -> bool:
    """True only if the EXPLICIT unverified-envelopes opt-out env var is set to a truthy value."""
    return os.environ.get(ALLOW_UNVERIFIED_ENVELOPES_ENV_VAR, "").strip().lower() in _TRUTHY


def _require_envelope_signing_or_fail_closed(
    *, runtime_mode: str, tenant: str, edge: str, keyset: TenantKeyset | None = None
) -> tuple[EnvelopeSigner | None, EnvelopeVerifier | None]:
    """Resolve the PER-TENANT envelope signer + verifier, fail-CLOSED when the key is absent (ADR-0039
    §4.4, leg E3). The STRUCTURAL SIBLING of `_require_signer_or_fail_closed` (Card signing), for the
    ENVELOPE surface — a DISTINCT surface (§4.1), same three-way fail-closed shape:

      - key PRESENT -> a real `(EnvelopeSigner, EnvelopeVerifier)` pair. The verifier trusts THIS
        tenant's keyset (the active key + any prior-in-grace key, `build_verification_keyset`); the
        signer signs new envelopes under the active key. Symmetric HMAC, one per-tenant key both
        jobs — mirroring the Card signer/verifier symmetry.
      - key ABSENT + PRODUCTION runtime mode -> RAISE at composition (un-bypassable; the opt-out is
        IGNORED here), so no dispatcher wired to reject-every-real-envelope is ever returned.
      - key ABSENT + non-production + the EXPLICIT `MAEZO_A2A_ALLOW_UNVERIFIED_ENVELOPES` opt-out ->
        `(None, None)` (envelope verification OFF), dev/test only, LOUDLY warned.
      - key ABSENT + non-production + NO opt-out -> RAISE (even in dev, never a SILENT downgrade).

    PER-TENANT CUSTODY (decision 4): the key is resolved through the SAME `TenantKeyset` seam Card
    signing uses (`key_for(tenant)`), NEVER the repo-wide or another tenant's key — a missing tenant
    key is the "absent key" case, it does NOT fall back across tenants (the cross-tenant
    key-confusion attack decision 4 rules out). The prior (rotation-grace) key is read from the same
    per-tenant seam (`prior_key_for`, on the `TenantKeyset` Protocol). Reuses the SHARED
    `is_production_runtime_mode` — no second prod/dev discriminator. Mirrors the E2 signer gate's
    injectable-`keyset` seam for hermetic tests.

    A PRESENT-BUT-DEGENERATE key in EITHER slot refuses composition. `build_verification_keyset`
    applies the `MIN_SIGNING_KEY_BYTES` floor to the active AND the prior key, so a misprovisioned
    prior variable (say a 1-byte value) raises `EnvelopeSignatureError` here rather than entering
    the trusted keyset — the identical posture a degenerate ACTIVE key already had via
    `EnvelopeSigner`. A garbage key is never silently skipped and never trusted.
    """
    resolver: TenantKeyset = keyset if keyset is not None else EnvTenantKeyset()
    active_key = resolver.key_for(tenant)
    if active_key is not None:
        # Prior (rotation-grace) key: read from the SAME per-tenant seam; absent in steady state.
        # A DIRECT, typed call — `prior_key_for` is on the `TenantKeyset` Protocol, so mypy checks
        # it here and a rename can no longer silently disable the rotation grace (which a
        # `getattr(..., default)` lookup would have done, invisibly to type-check).
        prior_key = resolver.prior_key_for(tenant)
        trusted, active_key_id = build_verification_keyset(active_key=active_key, prior_key=prior_key)
        signer = EnvelopeSigner(
            tenant=tenant,
            signing_key=active_key,
            key_id=active_key_id,
            replay_epoch=DEFAULT_REPLAY_EPOCH,
        )
        verifier = EnvelopeVerifier(
            trusted_keys=trusted,
            active_key_id=active_key_id,
            current_epoch=DEFAULT_REPLAY_EPOCH,
            max_signature_age=MAX_SIGNATURE_AGE,
        )
        return signer, verifier

    is_production = is_production_runtime_mode(runtime_mode)
    if is_production or not _unverified_envelopes_opt_out():
        raise RuntimeError(
            f"cannot assemble the A2A {edge} delegation edge: no envelope-signing key "
            f"({per_tenant_key_env_var(tenant)}) is present for tenant {tenant!r}, so a wired "
            "verifier would REJECT every real (unsigned) envelope. Refusing to compose "
            f"(runtime_mode={runtime_mode!r}). Provision the vault/KMS PER-TENANT key (ADR-0039 §4.4, "
            "decision 4 — per-tenant, never repo-wide), or — in a NON-production runtime ONLY — set "
            f"{ALLOW_UNVERIFIED_ENVELOPES_ENV_VAR}=1 to opt into unverified envelopes explicitly. The "
            "absence of a key must never silently downgrade to unverified envelopes (ADR-0039 §4.4), "
            "and a missing tenant key must NEVER fall back to the repo-wide or another tenant's key "
            "(cross-tenant key-confusion, decision 4)."
        )

    logger.warning(
        "a2a_envelope_signing_unverified_dev_optout",
        tenant=tenant,
        runtime_mode=runtime_mode,
        edge=edge,
        detail=(
            "UNVERIFIED delegation envelopes: no per-tenant envelope-signing key present and the "
            f"explicit non-production opt-out {ALLOW_UNVERIFIED_ENVELOPES_ENV_VAR} is set. The "
            "dispatcher will admit unsigned envelopes — dev/test ONLY, never a production posture."
        ),
    )
    return None, None


def _require_signer_or_fail_closed(
    *, runtime_mode: str, tenant: str, edge: str, keyset: TenantKeyset | None = None
) -> CardSigner | None:
    """Resolve the PER-TENANT Card-signing key, fail-CLOSED when it is absent (F2 + ADR-0039 §4.4).

    The W1 dev fail-safe silently downgraded to UNSIGNED Cards whenever the signing key was unset:
    `card_signer_from_key(None) -> None -> build_dispatcher(verifier=None) ->
    A2ARegistry(verifier=None)`, which admits ANY (unsigned/forged) Card. That silent downgrade is
    the defect. This gate makes the composition root UN-BYPASSABLE:

      - key PRESENT  -> a real `CardSigner` both signs the Cards and gates the registry (unchanged).
      - key ABSENT + PRODUCTION runtime mode (`runtime_mode != "local"`, e.g. Helm's
        "kubernetes") -> RAISE at startup. The opt-out below is IGNORED here — there is no way to
        compose an unsigned dispatcher in production, mirroring the fail-closed startup precedent of
        `AnthropicInferenceProvider` (absent credential -> raise, "no silent fallback to noop") and
        the production-default-refuses precedent of `RefusingAnsGatewayTransport`.
      - key ABSENT + non-production runtime mode + the EXPLICIT `MAEZO_A2A_ALLOW_UNSIGNED_CARDS`
        opt-out -> return `None` (unsigned Cards), for dev/test ergonomics only, and LOUDLY warn.
      - key ABSENT + non-production runtime mode + NO opt-out -> RAISE. Even in dev the absence of a
        key is never a SILENT default to unsigned (the auditor's requirement); it must be an
        explicit, deliberate choice.

    **PER-TENANT KEY CUSTODY (ADR-0039 §4.4, owner decision 4 — leg E2).** The key is resolved
    through a `TenantKeyset` — `EnvTenantKeyset` by default (the live env-backed vault/KMS seam),
    injectable for tests. The keyset reads THIS tenant's namespaced variable
    (`MAEZO_A2A_CARD_SIGNING_KEY__<TENANT>`, `per_tenant_key_env_var`), NEVER the bare repo-wide
    `MAEZO_A2A_CARD_SIGNING_KEY` and NEVER another tenant's key. A missing tenant key is exactly the
    "absent key" case above — it fail-closes, it does NOT fall back across tenants (that fallback IS
    the cross-tenant key-confusion attack decision 4 rules out). Migration off the repo-wide key is
    the fail-closed one the ADR mandates: deployments provision per-tenant keys; until they do,
    production refuses to compose the edge for that tenant. No silent shim (`maezo.a2a.keyset`).

    Generalized (dossier-A2A wave) from the original AgentRuntimeSettings-only signature so the
    worker-runtime dossier edge (`build_dossier_delegation_dispatcher`) applies the SAME gate:
    `runtime_mode` is the caller's prod/dev discriminator (agent-runtime: `agent_runtime_mode`;
    worker-runtime: `worker_runtime_mode_from_env()` — fail-closed to production when unset);
    `edge` only labels the error/log for legibility. The gate's fail-closed logic is unchanged;
    only WHERE the key comes from (per-tenant resolver) changed.
    """
    resolver = keyset if keyset is not None else EnvTenantKeyset()
    signing_key = resolver.key_for(tenant)
    if signing_key is not None:
        return card_signer_from_key(signing_key)

    is_production = is_production_runtime_mode(runtime_mode)
    if is_production or not _unsigned_cards_opt_out():
        raise RuntimeError(
            f"cannot assemble the A2A {edge} delegation edge: no Agent Card signing key "
            f"({per_tenant_key_env_var(tenant)}) is present for tenant {tenant!r}, so the registry "
            "would admit ANY unsigned/forged Card. Refusing to compose an unsigned dispatcher "
            f"(runtime_mode={runtime_mode!r}). Provision the vault/KMS PER-TENANT key (ADR-0039 "
            "§4.4, decision 4 — per-tenant, never repo-wide; design doc §6.2), or — in a "
            f"NON-production runtime ONLY — set {ALLOW_UNSIGNED_CARDS_ENV_VAR}=1 to opt into "
            "unsigned Cards explicitly. The absence of a key must never silently downgrade to "
            "unsigned Cards (T-G, ADR-0003/0007), and a missing tenant key must NEVER fall back to "
            "the repo-wide or another tenant's key (cross-tenant key-confusion, ADR-0039 "
            "decision 4)."
        )

    logger.warning(
        "a2a_card_signing_unsigned_dev_optout",
        tenant=tenant,
        runtime_mode=runtime_mode,
        edge=edge,
        detail=(
            "UNSIGNED Agent Cards: no signing key present and the explicit non-production opt-out "
            f"{ALLOW_UNSIGNED_CARDS_ENV_VAR} is set. The registry will admit unsigned Cards — dev/"
            "test ONLY, never a production posture."
        ),
    )
    return None


class _NoopKafkaProducer:
    """DEV-ONLY `KafkaLike` sink — reachable ONLY from explicit local mode with no `DATABASE_URL`.

    Never raises, never blocks; every `send` is dropped. This used to be the DEFAULT for both
    composition roots, which is precisely the defect Onda 3 / Train C leg 2 closed: in production
    it silently discarded every `agents.events.delegation.*` fact forever. It is now unreachable
    except through `_require_fact_producer_or_fail_closed`'s one disclosed branch — explicit local
    runtime mode AND no database — where dropping facts is honest dev behavior rather than a
    production data-loss posture, and where the alternative (refusing to compose) would make a
    laptop-run edge un-buildable for no safety gain.

    Kept rather than deleted, deliberately: the local branch needs a `KafkaLike` and inventing an
    in-memory buffer that grows unboundedly in a dev process would be a worse answer than an
    honest, labeled drop. It therefore REMAINS a declared §8.4 exception in
    `scripts/ci/check_effect_chokepoint_fence.py` (`("runtime/agent_runtime/a2a_composition.py",
    "_NoopKafkaProducer")`); removing that entry while the class is still defined here would redden
    the gate.

    Facts are observability and never the T-F audit surface (that rides the real
    `PostgresAuditSink` injected as `audit`, written BEFORE any fact is emitted), so even in this
    dev branch a dropped fact never compromises the delegation-audit proof.
    """

    async def send(self, topic: str, value: bytes, *, key: bytes | None = None) -> None:
        return None


def _require_fact_producer_or_fail_closed(
    *, runtime_mode: str, tenant: str, edge: str, database_url: str | None
) -> KafkaLike:
    """Resolve the delegation-fact sink, fail-CLOSED when it cannot be durable.

    The fact-side twin of `_require_signer_or_fail_closed`, and it exists for the same shape of
    defect: a MISSING dependency silently degrading to a permissive default. There, an absent
    signing key silently admitted unsigned Cards; here, an absent producer silently dropped every
    fact. Both roots now resolve through this gate:

      - `database_url` PRESENT -> `PostgresOutboxFactProducer`: the fact becomes a durable
        `a2a_fact_outbox` row (migration 0008), drained at-least-once by the explicitly-invoked
        `maezo.a2a.outbox_relay`. This is the only production posture.
      - `database_url` ABSENT + PRODUCTION runtime mode -> RAISE. Un-bypassable, mirroring the
        signer gate above and `AnthropicInferenceProvider`'s "no silent fallback to noop" startup
        precedent: composing a production A2A edge that discards its own facts is not a degraded
        mode, it is a fabricated one.
      - `database_url` ABSENT + EXPLICIT local runtime mode -> `_NoopKafkaProducer`, LOUDLY warned.
        Disclosed dev behavior (see that class's docstring for why a labeled drop beats an
        unbounded in-memory buffer on a laptop).

    NOTE ON REACHABILITY, so a reader does not over-read this gate: on the AGENT-RUNTIME root the
    production branch is currently unreachable via `DATABASE_URL` alone, because
    `build_auth_delegation_dispatcher` already raises `ValueError` on the missing `audit_sink` that
    the same absent DSN causes, several lines earlier. The gate is still applied there — the two
    refusals are for different dependencies and the audit-sink one could stop covering this if
    `_build_tool_deps` ever grows a non-DSN audit path. On the DOSSIER root the branch is directly
    reachable: `database_url` is an INDEPENDENT parameter from the injected `audit_sink`, so a
    caller can (and the worker daemon's degradation posture does) supply one without the other.

    "PRESENT" means `_dsn_is_present` — non-empty AND non-blank. A whitespace-only DSN is treated
    exactly like an absent one (see that helper for why the old bare-truthiness check was a
    legibility bug), so it takes the refusal branch here rather than building an outbox over garbage.

    `runtime_mode` is the caller's discriminator, resolved through the shared
    `is_production_runtime_mode` so this gate and the signer gate can never drift; `edge` only
    labels the error/log.
    """
    if _dsn_is_present(database_url):
        return build_outbox_fact_producer(dsn=database_url, tenant=tenant)

    if is_production_runtime_mode(runtime_mode):
        # `RuntimeError`, matching `_require_signer_or_fail_closed`'s own refusal type exactly:
        # both are "this composition root refuses to exist" startup failures, and giving the two
        # gates different exception types would make a caller's `except` clause pick one silently.
        raise RuntimeError(
            f"cannot assemble the A2A {edge} delegation edge: no DATABASE_URL is present, so "
            "delegation facts (agents.events.delegation.*) would be DROPPED at emission — the "
            "exact silent data loss the transactional outbox replaced. Refusing to compose a "
            f"production edge with a no-op fact sink (runtime_mode={runtime_mode!r}). Provide "
            "DATABASE_URL (the same DSN the audit sink and idempotency store already require) "
            "with migration 0008 applied, or — in a NON-production runtime ONLY — run with "
            f"{WORKER_RUNTIME_MODE_ENV_VAR}=local / AGENT_RUNTIME_MODE=local to opt into the "
            "labeled dev no-op explicitly."
        )

    logger.warning(
        "a2a_facts_noop_dev_sink",
        tenant=tenant,
        runtime_mode=runtime_mode,
        edge=edge,
        detail=(
            "delegation facts are being DROPPED: no DATABASE_URL and an EXPLICIT local runtime "
            "mode. Dev/test ONLY — never a production posture (the outbox is what makes facts "
            "durable; see maezo.a2a.outbox)."
        ),
    )
    return _NoopKafkaProducer()


def _require_idempotency_store_or_fail_closed(
    *, runtime_mode: str, tenant: str, edge: str, database_url: str | None
) -> PostgresIdempotencyStore | None:
    """Resolve the durable Guard-4 idempotency store, fail-CLOSED when it cannot be durable.

    The THIRD sibling of `_require_signer_or_fail_closed` and `_require_fact_producer_or_fail_closed`
    and the SAME defect shape all three exist for: a missing dependency silently degrading to a
    permissive default. There, an absent signing key silently admitted unsigned Cards; the fact
    twin, an absent producer silently dropped every fact. HERE, both roots used to wire the store
    truthy-only — `PostgresIdempotencyStore(...) if database_url else None` — so a NON-local runtime
    with no `DATABASE_URL` silently ran with NO durable, cross-replica idempotency: every replica
    and every restart re-executes the delegation's downstream effects (double process starts,
    duplicated dossiers), because the ONLY surviving Guard 4 is then the dispatcher's in-memory,
    single-process `_inflight` set. ADR-0039 makes this non-optional outside dev — durable-
    idempotency retention DOMINATES signature validity — so an absent store is a refusal, not a
    fallback. This gate closes the truthy-only gap:

      - `database_url` PRESENT -> `PostgresIdempotencyStore` (lazy asyncpg pool; migration
        0003_a2a_idempotency). The only production posture.
      - `database_url` ABSENT + PRODUCTION runtime mode -> RAISE. Un-bypassable, mirroring the
        signer and fact siblings and `AnthropicInferenceProvider`'s "no silent fallback to noop"
        startup precedent: a production A2A edge whose idempotency is not cross-replica-durable is
        not a degraded mode, it is one that re-fires effects on every restart.
      - `database_url` ABSENT + EXPLICIT local runtime mode -> `None`, LOUDLY warned. The
        dispatcher's in-memory `_inflight` Guard 4 (single-process, non-durable) remains the
        disclosed, legitimate dev behavior — a laptop-run edge stays buildable, and the local
        None/in-memory path this leg deliberately preserves.

    "PRESENT" means `_dsn_is_present` — non-empty AND non-blank. A whitespace-only DSN is treated
    exactly like an absent one (see that helper for why the old bare-truthiness check was a
    legibility bug), so it takes the refusal branch here rather than building a store over garbage.

    COHERENCE with the fact gate (leg 2): the refuse condition here — `not _dsn_is_present(
    database_url)` AND `is_production_runtime_mode(runtime_mode)` — is IDENTICAL to that gate's, and
    BOTH of its inputs are resolved through the SAME two shared helpers, so the two gates
    on a root cannot reach different verdicts (a root half-refusing is the coherence risk this
    parity removes; see `test_a2a_composition_idempotency.py`'s gate-agreement matrix). The gates
    keep SEPARATE, domain-specific error messages (leg 2's is pinned to the fact loss it prevents —
    "DROPPED at emission", migration 0008 — and is out of scope to rewrite here), so what is shared
    is the DECISION, not the raise. `edge` only labels the error/log.

    Retention WINDOW of the durable rows is DBA/MZO-060 (ADR-0039), deliberately NOT invented here —
    consistent with leg 2's outbox-retention deferral. This gate only makes the store MANDATORY; how
    long a sealed row is kept is an operator/DBA decision, not a value this composition root fabricates.
    """
    if _dsn_is_present(database_url):
        return PostgresIdempotencyStore(dsn=database_url, tenant=tenant)

    if is_production_runtime_mode(runtime_mode):
        # `RuntimeError`, matching both sibling gates' refusal type exactly — all three are "this
        # composition root refuses to exist" startup failures, and giving them different exception
        # types would make a caller's `except` clause silently catch one and not another.
        raise RuntimeError(
            f"cannot assemble the A2A {edge} delegation edge: no DATABASE_URL is present, so "
            "delegation idempotency (ADR-0003 Guard 4) would fall back to the dispatcher's "
            "in-memory _inflight set — single-process and lost on restart, so every replica and "
            "every restart RE-EXECUTES the delegation's downstream effects (double process starts, "
            "duplicated dossiers). Refusing to compose a production edge without a durable, "
            f"cross-replica idempotency store (runtime_mode={runtime_mode!r}). Provide DATABASE_URL "
            "(the same DSN the audit sink and fact outbox already require) with migration "
            "0003_a2a_idempotency applied, or — in a NON-production runtime ONLY — run with "
            f"{WORKER_RUNTIME_MODE_ENV_VAR}=local / AGENT_RUNTIME_MODE=local to opt into the "
            "in-memory dev idempotency explicitly. The absence of a database must never silently "
            "downgrade to non-durable idempotency outside local dev (ADR-0039)."
        )

    logger.warning(
        "a2a_idempotency_inmemory_dev_store",
        tenant=tenant,
        runtime_mode=runtime_mode,
        edge=edge,
        detail=(
            "durable idempotency is DISABLED: no DATABASE_URL and an EXPLICIT local runtime mode. "
            "The dispatcher falls back to its in-memory _inflight Guard 4 — dev/test ONLY, not "
            "cross-replica and not restart-durable (the Postgres store is what makes it durable; "
            "see maezo.a2a.idempotency.PostgresIdempotencyStore)."
        ),
    )
    return None


def _require_transactions_or_fail_closed(
    *,
    runtime_mode: str,
    tenant: str,
    audit: Any,
    facts: FactProducer,
    idempotency: IdempotencyStore | None,
) -> PostgresDelegationTransactions | None:
    """Production requires all three enlisted participants, including injected ones.

    FreshSink is a known DSN/tenant adapter; its enlisted form opens no pool.
    The original adapter remains the handler's loop-safe audit seam.
    """
    producer = facts._producer
    enlisted_audit = audit
    if isinstance(audit, FreshSinkAuditEmitter):
        enlisted_audit = PostgresAuditSink(audit._dsn, audit._tenant_id)
    if (
        isinstance(enlisted_audit, PostgresAuditSink)
        and isinstance(idempotency, PostgresIdempotencyStore)
        and isinstance(producer, PostgresOutboxFactProducer)
    ):
        return PostgresDelegationTransactions(
            tenant=tenant, audit=enlisted_audit, store=idempotency, outbox=producer.outbox
        )
    if is_production_runtime_mode(runtime_mode):
        raise RuntimeError("A2A production requires enlisted audit, idempotency and durable outbox")
    return None


def build_auth_delegation_dispatcher(
    settings: AgentRuntimeSettings,
    *,
    inference: InferenceProvider | None = None,
    kafka_producer: KafkaLike | None = None,
) -> DelegationDispatcher:
    """Assemble the Helena->Rafael `authorization.analyze` delegation edge (Option A, in-process).

    Construction-only — no node ever runs, no `.delegate()` call happens here (mirrors
    `agent_runtime.service._load_agent_graph`'s "construction check, not execution" posture).
    Raises `ValueError` (fail-closed) if Rafael's required deps (`dmn`/`cibseven`/`audit_sink`)
    are unavailable — mirrors `rafael.graph.build(config)`'s own fail-closed contract, surfaced
    here BEFORE `make_rafael_handler` would otherwise raise the same thing less legibly.

    **T-G signed-Card ENFORCEMENT (module docstring):** resolves the Card-signing key from the
    environment and, when present, signs every Card AND injects the same key as the registry's
    `verifier` — an unsigned/tampered/wrong-key Card raises `CardSignatureError` out of
    `build_dispatcher` (fail-closed: no dispatcher is returned, so nothing can `.delegate()`).
    When the key is ABSENT, `_require_signer_or_fail_closed` fail-CLOSES (F2): it RAISES
    `RuntimeError` in production runtime mode (un-bypassable), and proceeds unsigned only in a
    non-production runtime behind the EXPLICIT `MAEZO_A2A_ALLOW_UNSIGNED_CARDS` opt-out — never a
    silent downgrade (design doc §6.2 for why key provisioning is an external dependency).
    """
    tenant = settings.tenant_id
    # ONDA 1 §5.5, counterexample C-A2: this root used to construct its OWN `InferenceProvider()`
    # below, independently of `_build_tool_deps` — so the LLM seam was ungated on the A2A/dossier
    # edges even once the agent-runtime root was wired. The provider is now resolved through the
    # SAME registry call as every other seam, and Rafael's handler receives the GATED one.
    tool_deps = _build_tool_deps(settings, inference=inference or InferenceProvider())
    missing = [name for name in ("dmn", "cibseven", "audit_sink") if name not in tool_deps]
    if missing:
        raise ValueError(
            f"cannot assemble the A2A Helena->Rafael delegation edge: {missing} unavailable "
            "(DATABASE_URL unset disables audit_sink; rafael.graph.build(config) fail-closes "
            "without it — ADR-0007, T-C2)"
        )

    # T-G enforcement: ONE signer resolved from the injected key both signs the Cards and verifies
    # them at registration. Present key -> every Card signed AND the registry fail-closes on
    # anything not validly signed under this exact key (`build_dispatcher`'s `verifier=`). Absent
    # key -> `_require_signer_or_fail_closed` REFUSES to compose (raises) in production, and in dev
    # only proceeds unsigned behind an EXPLICIT opt-out (F2 — no silent downgrade to unsigned).
    signer = _require_signer_or_fail_closed(
        runtime_mode=settings.agent_runtime_mode, tenant=tenant, edge="Helena->Rafael"
    )
    cards = build_agent_cards(tenant, _EDGE_AGENT_IDS, signer=signer)

    # Envelope signing (ADR-0039 §4.4, leg E3), a STRUCTURAL SIBLING of the Card-signer gate above:
    # the SAME per-tenant key resolves both an origin signer (Helena signs `authorization.analyze`
    # envelopes at construction) and a verifier (the dispatcher's first admission check). Fail-closed:
    # absent key + production -> raise here, before any dispatcher is returned.
    envelope_signer, envelope_verifier = _require_envelope_signing_or_fail_closed(
        runtime_mode=settings.agent_runtime_mode, tenant=tenant, edge="Helena->Rafael"
    )

    handler: AgentHandler = make_rafael_handler(
        tool_deps["inference"],
        dmn=tool_deps["dmn"],
        cibseven=tool_deps["cibseven"],
        audit_sink=tool_deps["audit_sink"],
        fhir=tool_deps.get("fhir"),
    )

    # T-F: the SAME sink Rafael's own process-start fence uses (module docstring's audit-sink
    # reuse note) — the dispatcher's `_audit_delegation` records a DISTINCT chain link
    # (`action="a2a.delegate:rafael"`) on this one sink, never conflated with Rafael's own
    # `start_process_idempotent` audit (design doc §9.3).
    audit = tool_deps["audit_sink"]
    # Facts are DURABLE now (module docstring): absent an injected producer this resolves to the
    # transactional outbox, and REFUSES to compose in production when there is no database to make
    # it durable in. An injected `kafka_producer` (tests, or a future real broker seam) still wins.
    facts = FactProducer(
        kafka_producer
        or _require_fact_producer_or_fail_closed(
            runtime_mode=settings.agent_runtime_mode,
            tenant=tenant,
            edge="Helena->Rafael",
            database_url=settings.database_url,
        )
    )
    # Durable Guard 4 is MANDATORY outside explicit local dev now (ADR-0039): absent a DSN this
    # gate REFUSES to compose in production rather than silently running with the dispatcher's
    # in-memory _inflight set. On THIS root the refuse branch is defense-in-depth — the missing
    # `audit_sink` raised `ValueError` several lines above for the same absent DSN — but the gate
    # is applied so the two roots stay identical and so it keeps covering if `_build_tool_deps`
    # ever grows a non-DSN audit path (same reachability note as the fact gate). It also fires
    # AFTER the fact gate, which shares its exact refuse condition, so the two never half-refuse.
    idempotency = _require_idempotency_store_or_fail_closed(
        runtime_mode=settings.agent_runtime_mode,
        tenant=tenant,
        edge="Helena->Rafael",
        database_url=settings.database_url,
    )

    transactions = _require_transactions_or_fail_closed(
        runtime_mode=settings.agent_runtime_mode,
        tenant=tenant,
        audit=audit,
        facts=facts,
        idempotency=idempotency,
    )

    logger.info(
        "a2a_delegation_dispatcher_assembled",
        tenant=tenant,
        agents=_EDGE_AGENT_IDS,
        durable_idempotency=idempotency is not None,
        card_signing_enforced=signer is not None,
        envelope_verification_enforced=envelope_verifier is not None,
    )
    # ONDA 1 §5.5: the assembled dispatcher is handed out GATED (`delegacao_a2a`, C3). The
    # principal is HELENA — she originates this edge (`agents/helena/delegation.py:137`), and the
    # capability the decision asks about is hers, not Rafael's. The gate wraps the FINISHED
    # dispatcher, so every anti-loop / idempotency / Card-signature guard inside it stays exactly
    # where it was and keeps refusing with the chokepoint removed (I-6).
    return build_a2a_seam(
        seam=build_agent_seam_context(tenant=tenant, agent_id="helena"),
        inner=build_dispatcher(
            tenant=tenant,
            cards=cards,
            handlers={"rafael": handler},
            audit=audit,
            facts=facts,
            idempotency=idempotency,
            transactions=transactions,
            verifier=signer,
            envelope_verifier=envelope_verifier,
            origin_envelope_signer=envelope_signer,
        ),
    )


def build_dossier_delegation_dispatcher(
    *,
    tenant: str,
    runtime_mode: str,
    dmn: DmnTransport | None,
    cibseven: CibSevenTransport | None,
    audit_sink: AuditStartSink | None,
    database_url: str | None = None,
    inference: InferenceProvider | None = None,
    kafka_producer: KafkaLike | None = None,
    fhir: Mapping[str, FhirSummaryReader] | None = None,
    population: PopulationFeatureClient | None = None,
) -> DelegationDispatcher:
    """Assemble the WORKER-RUNTIME dossier delegation edges (DL-0033 real wiring, Option A).

    The first production-ORIGINATED `.delegate()` surface: unlike the Helena->Rafael edge above
    (assembled in agent-runtime, driven only by tests/a thin driver), this dispatcher is consumed
    LIVE by the worker daemon's raw async dossier handlers (`operadora.cred.prepare_dossier` ->
    Carolina `credentialing.analyze`; `operadora.adequacao.prepare_remediation_dossier` -> Andre
    `analytics.population`, origin-disambiguated into his `adequacao_dossier` flow; and
    `operadora.pagto.prepare_approval_dossier` -> Andre on the SAME shared type,
    `pagto-worker`-origin-disambiguated into his DEFAULT `pagto_dossier` flow). Assembled at
    worker-runtime bring-up (`worker_runtime/service.py` STEP B) and threaded to the three workers
    via the existing `**seams` bootstrap pattern. Adding the pagto edge required NO change to this
    root's agent set or handler map — Andre's card already accepts `analytics.population` and his
    handler routes by origin, so the third edge is served by construction.

    FERNANDO (`arrears.followup`) — REGISTERED **AND ORIGINATED** (owner decision R-081, gap
    `FERNANDO-DELEGATION-CALL-SITE`, approved 2026-09-04). His handler is in the map, so an
    `arrears.followup` envelope delivered to this dispatcher routes into Fernando's REAL graph
    instead of being refused for want of a handler; and the ORIGIN call site landed with it —
    `tools/workers/inadimplencia.py::make_prepare_dossier_handler` (the raw-async form of
    `operadora.inadimplencia.prepare_dossier`, sanctioned precedent
    `tools/workers/credenciamento.py::make_prepare_dossier_handler`) calls
    `delegate_arrears_followup` on the `dossier_dispatcher` this function returns. That call is
    FAIL-NEUTRAL by the owner's own condition: dispatcher absent, structured rejection,
    `StartProcessFailedError` (Fernando's RAF-02 guard) or any other exception completes the CIB
    Seven task with the LOCAL dossier plus a disclosed `arrears_followup_gap`, so a degraded
    dossier edge can never stall `UT_AnaliseInadimplencia` — the same DL-0037 posture the three
    older edges take. This edge is the FOURTH worker-originated dossier edge, not a fourth
    reason to widen the FHIR map: Fernando's graph declares no FHIR seam (see
    `_DOSSIER_EDGE_FHIR_AGENT_IDS`).

    Deps are INJECTED (not re-built here): the worker daemon already constructs the exact seams
    both target graphs need — `dmn` (`CibSevenDmnTransport`, fresh-client-per-call, loop-safe),
    `cibseven` (`FreshClientCibSevenTransport`, ditto) and `audit_sink` (the pooled
    `PostgresAuditSink` on the daemon's MAIN loop — raw async handlers run on that same loop, so
    the ONE sink serves the harness's completion audit, the dispatcher's T-F delegation audit AND
    both graphs' `start_process_idempotent` fence, mirroring the audit-sink-reuse note in this
    module's docstring) and, since CC-03/AND-03, the PER-AGENT gated `fhir` readers
    (`tool_registry.build_agent_fhir_seam`). Missing any of the three REQUIRED deps ->
    `ValueError` (fail-closed), mirroring `build_auth_delegation_dispatcher`; `fhir`/`population`
    are OPTIONAL and their absence degrades to each graph's disclosed gap note.

    T-G/F2: the SAME `_require_signer_or_fail_closed` key gate as the Helena->Rafael edge —
    `runtime_mode` comes from `worker_runtime_mode_from_env()` (fail-closed to "production" when
    unset, since the worker-daemon Helm template injects no mode var). Absent key in non-local
    mode -> RAISE: unsigned Cards NEVER compose outside explicit local dev. DEGRADATION POSTURE
    (DL-0037): the worker-runtime composition root CATCHES this raise — the daemon still RUNS
    (readiness reports `dossier_delegation_ready=false` loudly) and the dossier workers return
    the neutral disclosed-gap marker; the human User Tasks always still open ("instrui, nao
    decide").

    Durable Guard 4: `PostgresIdempotencyStore` when `database_url` is present (it always is in
    the live daemon — the same DSN gates the audit sink, without which the daemon never serves).

    **FHIR (CC-03 / AND-03, perna `fhir`).** `fhir` is a PER-AGENT map (`{"carolina": …,
    "andre": …}`), NOT one shared reader, and its absence used to be silent: this root called
    `make_carolina_handler`/`make_andre_handler` without the `fhir=` both factories accept, so
    100% of the dossiers this LIVE path produced fell into the degraded branch of both graphs
    (`carolina/graph.py::CarolinaGraph.gather` -> "leitor de resumo nao configurado …" + early
    return; `andre/graph.py::AndreGraph.gather` -> "leitor FHIR nao configurado …"). The sibling
    root already did it right for Rafael (`fhir=tool_deps.get("fhir")`, above).

    WHY PER AGENT rather than one instance. The two Protocols are structurally IDENTICAL —
    `carolina/graph.py::SummaryReader` and `andre/graph.py::PatientSummaryReader` both declare
    exactly `async read_patient(patient_id) -> dict` — so a single object would type-check for
    both. The GATE is what differs: `gateway/seams/fhir.py::GatedFhirReader` closes over a
    `SeamContext` whose `principal` is the AGENT, and `leitura_phi_clinica` (C2) is decided per
    principal. One shared instance would decide and record Andre's PHI read under CAROLINA's
    declared-capability record — a fabricated governance attribution, which is worse than the
    degradation it would fix. The daemon therefore builds one seam per agent through the ONE
    sanctioned constructor (`gateway.tool_registry.build_agent_fhir_seam`).

    FAIL-CLOSED ON AN UNKNOWN KEY. A typo (`{"carolinaa": reader}`) would silently reproduce the
    exact defect this parameter closes — a dossier degraded with nothing said. Unknown keys raise
    `ValueError` before any card/signer work.

    `population` is BLOCKED(external WB.4) and is threaded EXPLICITLY as the `None` it is: there
    is no concrete `PopulationFeatureClient` in `src/` (`andre/graph.py` defines the Protocol
    only, `gateway/seams/population.py` ships unwired, and `build_agent_seams` omits the key on
    purpose). Andre's `build(config)` turns its absence into a disclosed gap note. Passing the
    argument rather than omitting it is the point: the gap is declared at the root, not inferred
    from a default nobody reads.
    """
    fhir_by_agent = dict(fhir or {})
    unknown_agents = sorted(set(fhir_by_agent) - set(_DOSSIER_EDGE_FHIR_AGENT_IDS))
    if unknown_agents:
        raise ValueError(
            f"cannot assemble the A2A dossier delegation edges: fhir map names agents this edge "
            f"does not serve a FHIR seam to: {unknown_agents} (serves "
            f"{list(_DOSSIER_EDGE_FHIR_AGENT_IDS)}) — a misspelled key would silently degrade the "
            "dossier, which is the defect CC-03/AND-03 closed"
        )
    if dmn is None or cibseven is None or audit_sink is None:
        missing = [
            name
            for name, value in (("dmn", dmn), ("cibseven", cibseven), ("audit_sink", audit_sink))
            if value is None
        ]
        raise ValueError(
            f"cannot assemble the A2A dossier delegation edges: {missing} unavailable "
            "(DATABASE_URL unset disables audit_sink; carolina/andre graph.build(config) "
            "fail-close without their deps — ADR-0007, T-C2)"
        )

    signer = _require_signer_or_fail_closed(
        runtime_mode=runtime_mode, tenant=tenant, edge="worker->Carolina/Andre/Fernando dossier"
    )
    cards = build_agent_cards(tenant, _DOSSIER_EDGE_AGENT_IDS, signer=signer)

    # Envelope signing (ADR-0039 §4.4, leg E3) — sibling of the Card-signer gate. The origin signer
    # rides on the dispatcher; the three dossier `delegate_*` originators retrieve it
    # (`origin_signer_of`) and sign at construction, so the LIVE worker path produces SIGNED
    # envelopes the wired verifier admits (no per-worker wiring change). Fail-closed the same way.
    envelope_signer, envelope_verifier = _require_envelope_signing_or_fail_closed(
        runtime_mode=runtime_mode, tenant=tenant, edge="worker->Carolina/Andre/Fernando dossier"
    )

    # ONDA 1 §5.5 / C-A2, second of the two independent constructions: the dossier edge's LLM seam
    # is gated too. The `dmn`/`cibseven` seams arrive ALREADY gated from the worker root — they are
    # injected, not rebuilt here (this root's own docstring), so double-wrapping cannot happen.
    worker_seam = build_worker_seam_context(tenant=tenant)
    llm = build_inference_seam(seam=worker_seam, inner=inference)
    # CC-03/AND-03: the per-agent GATED reader reaches BOTH graphs. `.get(...)` (not `[...]`) is
    # deliberate — an absent key is the daemon's HONEST degraded posture (seam construction
    # failed, or a runtime with no FHIR endpoint), and both graphs turn `None` into their
    # disclosed gap note. Degradation, never a fabricated reader.
    carolina_handler: AgentHandler = make_carolina_handler(
        llm,
        dmn=dmn,
        cibseven=cibseven,
        audit_sink=audit_sink,
        fhir=fhir_by_agent.get("carolina"),
    )
    andre_handler: AgentHandler = make_andre_handler(
        llm,
        dmn=dmn,
        cibseven=cibseven,
        audit_sink=audit_sink,
        fhir=fhir_by_agent.get("andre"),
        # BLOCKED(external WB.4) — see this function's docstring. Explicit, never omitted.
        population=population,
    )
    # R-081: Fernando's `build(config)` fail-closes without a `whatsapp` sender because `notify()`
    # is a real node in his graph. On THIS seam that branch is STRUCTURALLY unreachable —
    # `agents/fernando/delegation.py::state_from_envelope` always sets `canal="a2a"` and
    # `graph.py::notify` only sends when `canal == "whatsapp"` — so the seam is DECLARED and never
    # touched, the same posture every other `build(config)` caller takes with a branch it does not
    # exercise. Built through the ONE sanctioned constructor (`build_whatsapp_seam`), gated on the
    # worker seam like the LLM above, never a hand-rolled or fabricated sender.
    #
    # The adapter comes from `whatsapp_adapter_for` — the repo's ONE agent->adapter map — never
    # from a literal here. `build_whatsapp_seam` branches only on `"lucas"`, so passing the AGENT
    # id `"fernando"` would land on Helena's sender through an unguarded `else`: the same object
    # the map names today, chosen by accident rather than by the map. Two construction paths for
    # one decision is exactly the C-A2 counterexample this module exists to avoid.
    fernando_handler: AgentHandler = make_fernando_handler(
        llm,
        dmn=dmn,
        cibseven=cibseven,
        audit_sink=audit_sink,
        whatsapp=build_whatsapp_seam(seam=worker_seam, adapter=_require_whatsapp_adapter_for("fernando")),
    )

    # Facts are DURABLE now (module docstring). This root is where the gate genuinely bites:
    # `database_url` is INDEPENDENT of the injected `audit_sink`, so "audit sink present, DSN
    # absent" is a reachable caller state, and in production it is now a refusal instead of a
    # silently fact-dropping dispatcher.
    facts = FactProducer(
        kafka_producer
        or _require_fact_producer_or_fail_closed(
            runtime_mode=runtime_mode,
            tenant=tenant,
            edge="worker->Carolina/Andre/Fernando dossier",
            database_url=database_url,
        )
    )
    # Durable Guard 4 (ADR-0039): MANDATORY outside explicit local. On THIS root the gate genuinely
    # bites — `database_url` is INDEPENDENT of the injected `audit_sink`, so "audit sink present,
    # DSN absent" is a reachable caller state (the worker daemon's degradation posture supplies one
    # without the other), and in production it is now a refusal instead of a silently non-durable
    # dispatcher. Same refuse condition as the fact gate above -> a single-valued root verdict.
    idempotency = _require_idempotency_store_or_fail_closed(
        runtime_mode=runtime_mode,
        tenant=tenant,
        edge="worker->Carolina/Andre/Fernando dossier",
        database_url=database_url,
    )

    transactions = _require_transactions_or_fail_closed(
        runtime_mode=runtime_mode,
        tenant=tenant,
        audit=audit_sink,
        facts=facts,
        idempotency=idempotency,
    )

    logger.info(
        "a2a_dossier_delegation_dispatcher_assembled",
        tenant=tenant,
        agents=_DOSSIER_EDGE_AGENT_IDS,
        runtime_mode=runtime_mode,
        durable_idempotency=idempotency is not None,
        card_signing_enforced=signer is not None,
        envelope_verification_enforced=envelope_verifier is not None,
    )
    # ONDA 1 §5.5, third construction site. Principal `worker_runtime`: these edges are
    # WORKER-originated (`operadora.cred.prepare_dossier` plus the adequacao/pagto/inadimplencia
    # topics), and
    # there is no `agent.yaml` for a worker daemon — see `build_worker_seam_context` for why the
    # honest consequence is a `CAPACIDADE_INDISPONIVEL` would-deny rather than a fabricated
    # capability record.
    return build_a2a_seam(
        seam=worker_seam,
        inner=build_dispatcher(
            tenant=tenant,
            cards=cards,
            handlers={
                "carolina": carolina_handler,
                "andre": andre_handler,
                "fernando": fernando_handler,
            },
            audit=audit_sink,
            facts=facts,
            idempotency=idempotency,
            transactions=transactions,
            verifier=signer,
            envelope_verifier=envelope_verifier,
            origin_envelope_signer=envelope_signer,
        ),
    )

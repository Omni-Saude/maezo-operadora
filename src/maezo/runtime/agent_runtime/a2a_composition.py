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

No production Kafka producer exists anywhere in this platform yet (verified: no
`KafkaProducer`/bootstrap-servers construction outside test doubles). Facts
(`agents.events.delegation.*`) are an OBSERVABILITY surface, not the T-F audit — the audit proof
rides the real `PostgresAuditSink` above, so a construction-time no-op `KafkaLike` stands in here,
clearly labeled, until a live Kafka producer seam is wired platform-wide (design §9.2 / risk 6).

**T-G signed-Card enforcement (`docs/design/A2A-dispatcher-card-signing.md` §10).** W3 left this
composition wired with NO `verifier=`: Cards came back unsigned and the registry admitted them
unconditionally. This flips it: `card_signing_key_from_env()` resolves the vault/KMS-injected
signing key (env var `MAEZO_A2A_CARD_SIGNING_KEY`), `card_signer_from_key` builds a `CardSigner`
from it, and the SAME signer instance both signs the Cards (`build_agent_cards(..., signer=signer)`)
and gates the registry (`build_dispatcher(..., verifier=signer)`) — symmetric HMAC, one key does
both jobs. Key present -> every Card is signed AND the registry fail-closes (`CardSignatureError`)
on anything not validly signed under that exact key — an unsigned, tampered, or wrong-key Card
never reaches `register()` successfully, so this function raises before a dispatcher object is ever
returned (fail-closed: there is nothing to `.delegate()` against).

**F2 — no silent fail-open when the key is absent.** The earlier W1 dev fail-safe SILENTLY
downgraded to unsigned Cards + no verifier whenever the key was unset — which in production would
have admitted ANY (unsigned/forged) Card. `_require_signer_or_fail_closed` closes that: an absent
key RAISES at composition in production runtime mode (`agent_runtime_mode != "local"`,
un-bypassable), and in a non-production runtime proceeds unsigned ONLY behind the EXPLICIT
`MAEZO_A2A_ALLOW_UNSIGNED_CARDS` opt-out (loudly warned), never as a silent default. Real key
provisioning in vault/KMS remains an external/infra dependency (design doc §6.2).
"""

from __future__ import annotations

import os

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
from maezo.a2a.assembly import (
    CARD_SIGNING_KEY_ENV_VAR,
    card_signer_from_key,
    card_signing_key_from_env,
)
from maezo.a2a.dispatcher import KafkaLike
from maezo.agents.rafael.delegation import make_rafael_handler
from maezo.runtime.inference import InferenceProvider

from .service import _build_tool_deps
from .settings import AgentRuntimeSettings

logger = structlog.get_logger(__name__)

#: The two agents party to this one edge (design doc §5/§9 — Helena originates, Rafael targets).
_EDGE_AGENT_IDS = ("helena", "rafael")

#: The ONLY non-production `agent_runtime_mode` (settings.py default). Helm injects "kubernetes"
#: for the deployed daemon (`deployment-agent-runtime.yaml`), so ANY value other than "local" is
#: treated as production — an unrecognized/misconfigured mode fails CLOSED, never open.
_LOCAL_RUNTIME_MODE = "local"

#: EXPLICIT, non-production-only opt-out permitting UNSIGNED Agent Cards when no signing key is
#: present. This exists purely for dev/test ergonomics (running the edge without provisioning a
#: vault/KMS key); it is IGNORED in production runtime mode, where an absent key ALWAYS fail-closes
#: (see `_require_signer_or_fail_closed`). It must NEVER default to on — the absence of a key must
#: never SILENTLY downgrade to unsigned Cards (T-G, ADR-0003/0007).
ALLOW_UNSIGNED_CARDS_ENV_VAR = "MAEZO_A2A_ALLOW_UNSIGNED_CARDS"

_TRUTHY = frozenset({"1", "true", "yes", "on"})


def _unsigned_cards_opt_out() -> bool:
    """True only if the EXPLICIT unsigned-cards opt-out env var is set to a truthy value."""
    return os.environ.get(ALLOW_UNSIGNED_CARDS_ENV_VAR, "").strip().lower() in _TRUTHY


def _require_signer_or_fail_closed(settings: AgentRuntimeSettings) -> CardSigner | None:
    """Resolve the Card-signing key, fail-CLOSED when it is absent (F2 — the composition root fix).

    The W1 dev fail-safe silently downgraded to UNSIGNED Cards whenever `MAEZO_A2A_CARD_SIGNING_KEY`
    was unset: `card_signer_from_key(None) -> None -> build_dispatcher(verifier=None) ->
    A2ARegistry(verifier=None)`, which admits ANY (unsigned/forged) Card. That silent downgrade is
    the defect. This gate makes the composition root UN-BYPASSABLE:

      - key PRESENT  -> a real `CardSigner` both signs the Cards and gates the registry (unchanged).
      - key ABSENT + PRODUCTION runtime mode (`agent_runtime_mode != "local"`, e.g. Helm's
        "kubernetes") -> RAISE at startup. The opt-out below is IGNORED here — there is no way to
        compose an unsigned dispatcher in production, mirroring the fail-closed startup precedent of
        `AnthropicInferenceProvider` (absent credential -> raise, "no silent fallback to noop") and
        the production-default-refuses precedent of `RefusingAnsGatewayTransport`.
      - key ABSENT + non-production runtime mode + the EXPLICIT `MAEZO_A2A_ALLOW_UNSIGNED_CARDS`
        opt-out -> return `None` (unsigned Cards), for dev/test ergonomics only, and LOUDLY warn.
      - key ABSENT + non-production runtime mode + NO opt-out -> RAISE. Even in dev the absence of a
        key is never a SILENT default to unsigned (the auditor's requirement); it must be an
        explicit, deliberate choice.
    """
    signing_key = card_signing_key_from_env()
    if signing_key is not None:
        return card_signer_from_key(signing_key)

    is_production = settings.agent_runtime_mode != _LOCAL_RUNTIME_MODE
    if is_production or not _unsigned_cards_opt_out():
        raise RuntimeError(
            "cannot assemble the A2A Helena->Rafael delegation edge: no Agent Card signing key "
            f"({CARD_SIGNING_KEY_ENV_VAR}) is present, so the registry would admit ANY unsigned/"
            "forged Card. Refusing to compose an unsigned dispatcher "
            f"(agent_runtime_mode={settings.agent_runtime_mode!r}). Provision the vault/KMS key "
            "(design doc §6.2), or — in a NON-production runtime ONLY — set "
            f"{ALLOW_UNSIGNED_CARDS_ENV_VAR}=1 to opt into unsigned Cards explicitly. The absence "
            "of a key must never silently downgrade to unsigned Cards (T-G, ADR-0003/0007)."
        )

    logger.warning(
        "a2a_card_signing_unsigned_dev_optout",
        tenant=settings.tenant_id,
        agent_runtime_mode=settings.agent_runtime_mode,
        detail=(
            "UNSIGNED Agent Cards: no signing key present and the explicit non-production opt-out "
            f"{ALLOW_UNSIGNED_CARDS_ENV_VAR} is set. The registry will admit unsigned Cards — dev/"
            "test ONLY, never a production posture."
        ),
    )
    return None


class _NoopKafkaProducer:
    """Construction-time `KafkaLike` placeholder — see module docstring's Kafka note.

    Never raises, never blocks; every `send` is dropped. Facts are observability, never the T-F
    audit surface (that rides the real `PostgresAuditSink` injected as `audit`), so a dropped fact
    never compromises the delegation-audit proof this edge is built to make real.
    """

    async def send(self, topic: str, value: bytes, *, key: bytes | None = None) -> None:
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
    tool_deps = _build_tool_deps(settings)
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
    signer = _require_signer_or_fail_closed(settings)
    cards = build_agent_cards(tenant, _EDGE_AGENT_IDS, signer=signer)

    handler: AgentHandler = make_rafael_handler(
        inference or InferenceProvider(),
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
    facts = FactProducer(kafka_producer or _NoopKafkaProducer())
    idempotency = (
        PostgresIdempotencyStore(dsn=settings.database_url, tenant=tenant) if settings.database_url else None
    )

    logger.info(
        "a2a_delegation_dispatcher_assembled",
        tenant=tenant,
        agents=_EDGE_AGENT_IDS,
        durable_idempotency=idempotency is not None,
        card_signing_enforced=signer is not None,
    )
    return build_dispatcher(
        tenant=tenant,
        cards=cards,
        handlers={"rafael": handler},
        audit=audit,
        facts=facts,
        idempotency=idempotency,
        verifier=signer,
    )

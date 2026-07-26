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

**T-G signed-Card enforcement (T2.4 A2A W4 — `docs/design/A2A-dispatcher-card-signing.md` §10).**
W3 left this composition wired with NO `verifier=`: Cards came back unsigned and the registry
admitted them unconditionally. W4 flips this: `card_signing_key_from_env()` resolves the
vault/KMS-injected signing key (env var `MAEZO_A2A_CARD_SIGNING_KEY`), `card_signer_from_key`
builds a `CardSigner` from it, and the SAME signer instance both signs the Cards
(`build_agent_cards(..., signer=signer)`) and gates the registry
(`build_dispatcher(..., verifier=signer)`) — symmetric HMAC, one key does both jobs. The W1 dev
fail-safe asymmetry is preserved exactly: key unset/empty -> `signer=None` -> unsigned Cards, no
verifier, unchanged Phase-0 behavior (real key provisioning in vault/KMS remains an external/infra
dependency, design doc §6.2); key present -> every Card is signed AND the registry fail-closes
(`CardSignatureError`) on anything not validly signed under that exact key — an unsigned, tampered,
or wrong-key Card never reaches `register()` successfully, so this function raises before a
dispatcher object is ever returned (fail-closed: there is nothing to `.delegate()` against).
"""

from __future__ import annotations

import structlog

from maezo.a2a import (
    AgentHandler,
    DelegationDispatcher,
    FactProducer,
    PostgresIdempotencyStore,
    build_agent_cards,
    build_dispatcher,
)
from maezo.a2a.assembly import card_signer_from_key, card_signing_key_from_env
from maezo.a2a.dispatcher import KafkaLike
from maezo.agents.rafael.delegation import make_rafael_handler
from maezo.runtime.inference import InferenceProvider

from .service import _build_tool_deps
from .settings import AgentRuntimeSettings

logger = structlog.get_logger(__name__)

#: The two agents party to this one edge (design doc §5/§9 — Helena originates, Rafael targets).
_EDGE_AGENT_IDS = ("helena", "rafael")


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

    **T-G signed-Card ENFORCEMENT (W4, module docstring):** resolves the Card-signing key from the
    environment and, when present, signs every Card AND injects the same key as the registry's
    `verifier` — an unsigned/tampered/wrong-key Card raises `CardSignatureError` out of
    `build_dispatcher` (fail-closed: no dispatcher is returned, so nothing can `.delegate()`).
    When the key is absent (no vault/KMS secret provisioned yet, design doc §6.2), Cards come back
    unsigned and no verifier is wired — the unchanged W1/W3 dev fail-safe path.
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

    # T-G enforcement (W4): ONE signer resolved from the injected key both signs the Cards and
    # verifies them at registration — absent key -> None -> unsigned Cards / no verifier (dev
    # fail-safe, unchanged); present key -> every Card signed AND the registry fail-closes on
    # anything not validly signed under this exact key (`build_dispatcher`'s `verifier=`).
    signer = card_signer_from_key(card_signing_key_from_env())
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

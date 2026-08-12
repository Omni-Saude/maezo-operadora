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
from maezo.agents.andre.delegation import make_andre_handler
from maezo.agents.carolina.delegation import make_carolina_handler
from maezo.agents.rafael.delegation import make_rafael_handler
from maezo.gateway.tool_registry import (
    build_a2a_seam,
    build_agent_seam_context,
    build_inference_seam,
    build_worker_seam_context,
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
#: The ORIGINS are workers (`credenciamento-worker`/`adequacao-worker`/`pagto-worker`), not
#: agents — the dispatcher validates only the TARGET's Card, so no origin card exists or is needed.
_DOSSIER_EDGE_AGENT_IDS = ("carolina", "andre")

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


def _unsigned_cards_opt_out() -> bool:
    """True only if the EXPLICIT unsigned-cards opt-out env var is set to a truthy value."""
    return os.environ.get(ALLOW_UNSIGNED_CARDS_ENV_VAR, "").strip().lower() in _TRUTHY


def _require_signer_or_fail_closed(*, runtime_mode: str, tenant: str, edge: str) -> CardSigner | None:
    """Resolve the Card-signing key, fail-CLOSED when it is absent (F2 — the composition root fix).

    The W1 dev fail-safe silently downgraded to UNSIGNED Cards whenever `MAEZO_A2A_CARD_SIGNING_KEY`
    was unset: `card_signer_from_key(None) -> None -> build_dispatcher(verifier=None) ->
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

    Generalized (dossier-A2A wave) from the original AgentRuntimeSettings-only signature so the
    worker-runtime dossier edge (`build_dossier_delegation_dispatcher`) applies the SAME gate:
    `runtime_mode` is the caller's prod/dev discriminator (agent-runtime: `agent_runtime_mode`;
    worker-runtime: `worker_runtime_mode_from_env()` — fail-closed to production when unset);
    `edge` only labels the error/log for legibility. The gate's logic is unchanged.
    """
    signing_key = card_signing_key_from_env()
    if signing_key is not None:
        return card_signer_from_key(signing_key)

    is_production = runtime_mode != _LOCAL_RUNTIME_MODE
    if is_production or not _unsigned_cards_opt_out():
        raise RuntimeError(
            f"cannot assemble the A2A {edge} delegation edge: no Agent Card signing key "
            f"({CARD_SIGNING_KEY_ENV_VAR}) is present, so the registry would admit ANY unsigned/"
            "forged Card. Refusing to compose an unsigned dispatcher "
            f"(runtime_mode={runtime_mode!r}). Provision the vault/KMS key "
            "(design doc §6.2), or — in a NON-production runtime ONLY — set "
            f"{ALLOW_UNSIGNED_CARDS_ENV_VAR}=1 to opt into unsigned Cards explicitly. The absence "
            "of a key must never silently downgrade to unsigned Cards (T-G, ADR-0003/0007)."
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
            verifier=signer,
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

    Deps are INJECTED (not re-built here): the worker daemon already constructs the exact seams
    both target graphs need — `dmn` (`CibSevenDmnTransport`, fresh-client-per-call, loop-safe),
    `cibseven` (`FreshClientCibSevenTransport`, ditto) and `audit_sink` (the pooled
    `PostgresAuditSink` on the daemon's MAIN loop — raw async handlers run on that same loop, so
    the ONE sink serves the harness's completion audit, the dispatcher's T-F delegation audit AND
    both graphs' `start_process_idempotent` fence, mirroring the audit-sink-reuse note in this
    module's docstring). Missing any of the three -> `ValueError` (fail-closed), mirroring
    `build_auth_delegation_dispatcher`.

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
    """
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
        runtime_mode=runtime_mode, tenant=tenant, edge="worker->Carolina/Andre dossier"
    )
    cards = build_agent_cards(tenant, _DOSSIER_EDGE_AGENT_IDS, signer=signer)

    # ONDA 1 §5.5 / C-A2, second of the two independent constructions: the dossier edge's LLM seam
    # is gated too. The `dmn`/`cibseven` seams arrive ALREADY gated from the worker root — they are
    # injected, not rebuilt here (this root's own docstring), so double-wrapping cannot happen.
    worker_seam = build_worker_seam_context(tenant=tenant)
    llm = build_inference_seam(seam=worker_seam, inner=inference)
    carolina_handler: AgentHandler = make_carolina_handler(
        llm, dmn=dmn, cibseven=cibseven, audit_sink=audit_sink
    )
    andre_handler: AgentHandler = make_andre_handler(llm, dmn=dmn, cibseven=cibseven, audit_sink=audit_sink)

    facts = FactProducer(kafka_producer or _NoopKafkaProducer())
    idempotency = PostgresIdempotencyStore(dsn=database_url, tenant=tenant) if database_url else None

    logger.info(
        "a2a_dossier_delegation_dispatcher_assembled",
        tenant=tenant,
        agents=_DOSSIER_EDGE_AGENT_IDS,
        runtime_mode=runtime_mode,
        durable_idempotency=idempotency is not None,
        card_signing_enforced=signer is not None,
    )
    # ONDA 1 §5.5, third construction site. Principal `worker_runtime`: these edges are
    # WORKER-originated (`operadora.cred.prepare_dossier` and the two adequacao/pagto topics), and
    # there is no `agent.yaml` for a worker daemon — see `build_worker_seam_context` for why the
    # honest consequence is a `CAPACIDADE_INDISPONIVEL` would-deny rather than a fabricated
    # capability record.
    return build_a2a_seam(
        seam=worker_seam,
        inner=build_dispatcher(
            tenant=tenant,
            cards=cards,
            handlers={"carolina": carolina_handler, "andre": andre_handler},
            audit=audit_sink,
            facts=facts,
            idempotency=idempotency,
            verifier=signer,
        ),
    )

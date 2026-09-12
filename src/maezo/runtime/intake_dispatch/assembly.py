"""Concrete adapters: the outbox reader, and the command assembler over published AUTH heads.

=================================================================================================
Why the daemon has to ASSEMBLE the command at all
=================================================================================================
`native_outbox` rows are staged by the admission transaction with NO command in them
(`stage_intake`, `gateway/intake/native_store.py:584`: the row carries the admission refs, the
principal and the authorization ceiling; `command_digest`/`command_ciphertext` stay NULL, which
the schema explicitly permits for `state='admitted'`, `native_schema.sql:20`). The command is
sealed later, by `prepare` (`native_store.py:262`), and `dispatch_prepared_start` calls `prepare`
on the command it is GIVEN (`native_composition.py:79`). Somebody has to build that command from
the admission's published sources. Before this module, nobody did — which is why
`build_start_command` (`gateway/human/auth_projection.py:102`) had no caller outside its own test.

=================================================================================================
Reading published contracts, deciding nothing
=================================================================================================
Every fact in the command comes from a published `MZO_AUTH_INPUT_HEAD` row re-verified through the
ONE sanctioned verification, `NativeAuthReader.head` (`gateway/intake/native_authority.py:272`:
publisher purpose and installed source grant, payload digest, version join, source lifetime). The
two lookups that cannot be keyed directly (`start_facts` by intake, `resource_authority` by guide)
use a `payload_::jsonb` scan as a CANDIDATE FILTER and then verify every candidate through `head`
— the pattern `published_authorities` documents for itself (`native_authority.py:344-352`).
Ambiguity is refused, never resolved by preference: two heads over one intake mean the
installation is inconsistent, not that the daemon may pick. Absence is refused too.

`build_start_command` then re-checks all of it against the ORIGINAL submission (admitted digest,
every fact field, the pin set, every validity window) and refuses on any mismatch. This module
adds no rule of its own on top of that: it selects heads and hands them over.

=================================================================================================
What this module refuses to invent
=================================================================================================
* the deployed `Definition` — pinned owner material (`settings.py`), never guessed from the engine;
* the `numero_guia_tiss` — read by the seam off the published facts or refused there
  (`native_composition.py:43-61`); no derivation from the opaque `guide_identity_ref` exists, and
  fabricating one would fabricate clinical identity;
* the `auth.documents.respond` command — it needs a bound occurrence from a live engine read
  (`DocumentContextQuery`, purpose `human-auth-read`, `gateway/human/auth_transport.py:117`) fed to
  `bind_document_response` (`gateway/intake/native_binding.py:22`). Neither has any producer in
  `src/` today (the document-request plane is WP-J1-03/04), and that read additionally needs the
  caller authority a daemon does not have. So the document path is wired end-to-end in the drain
  engine and refuses HERE, by name, instead of pretending to be implemented.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, cast

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from maezo.gateway.external_cases.postgres import transaction
from maezo.gateway.human.auth_profile import (
    Actor,
    AuditIntent,
    AuditIntentPayload,
    Definition,
    DocumentCustody,
    DocumentPolicy,
    GuideIdentity,
    InputPublication,
    Pin,
    ResourceAuthority,
    StartFacts,
)
from maezo.gateway.human.auth_projection import build_start_command
from maezo.gateway.human.read_profile import MembershipProjection, SourceProvenance, parse_model
from maezo.gateway.intake.native_authority import AuthProductionComposition, NativeAuthReader
from maezo.gateway.intake.native_store import PostgresAuthDispatchStore
from maezo.gateway.intake.postgres import PostgresIntakeStore
from maezo.portal.contracts.intake import AuthIntakeSubmission
from maezo.portal.engine.profile import strict_loads

from .service import (
    CommandUnassembledError,
    DispatchTarget,
    PendingDispatch,
    PendingIntakeSource,
    PreparedCommandSource,
    PreparedDispatch,
    ReceiptAuthority,
)

#: The signing purpose whose issuer is the start command's `workload_ref` — the same selection
#: `AuthProductionComposition.intake_components` makes for the transport (`native_authority.py`).
_START_PURPOSE = "human-auth-start"

#: Rows this engine considers, in the store's own state vocabulary (`native_schema.sql:11`).
_DRAINABLE = ("admitted", "claimed", "sending", "reconciling")

#: Oldest admission first, then `command_id` — a total order, so a sweep is reproducible and a
#: backlog drains in the order it was admitted rather than in whatever order Postgres returns.
#: `portal_intake.native_outbox` has no timestamp of its own, so the admission's own committed
#: time is taken from the row that caused it (`portal_intake.intake.created_at` for a start, the
#: earliest `native_response.admitted_at` for a response).
_PENDING_SQL = (
    "SELECT o.command_id, o.admission_ref, o.resource_ref, o.operation, o.state, "
    "o.authorization_until, o.lease_until, i.guide_identity_ref, "
    "COALESCE(i.created_at, (SELECT min(r.admitted_at) FROM portal_intake.native_response r "
    "WHERE r.tenant=o.tenant AND r.command_id=o.command_id)) AS admitted_at "
    "FROM portal_intake.native_outbox o "
    "LEFT JOIN portal_intake.intake i ON i.tenant=o.tenant AND i.command_id=o.command_id "
    "AND i.principal_ref=o.principal_ref "
    "WHERE o.tenant=:tenant AND o.state IN ('admitted','claimed','sending','reconciling') "
    "ORDER BY admitted_at ASC NULLS LAST, o.command_id ASC LIMIT :limit"
)

_HEAD_FACTS_CANDIDATES = (
    "SELECT resource_ FROM {head} WHERE tenant_=:tenant AND kind_='start_facts' "
    "AND state_='active' AND payload_::jsonb->>'intake_ref'=:intake"
)

_HEAD_AUTHORITY_CANDIDATES = (
    "SELECT resource_ FROM {head} WHERE tenant_=:tenant AND kind_='resource_authority' "
    "AND state_='active' AND payload_::jsonb->>'action'='auth.start' "
    "AND payload_::jsonb->'actor'->>'principal_ref'=:principal"
)


class PostgresPendingIntakes:
    """Reads the drainable `native_outbox` rows, in admission order. Writes nothing, ever."""

    def __init__(self, store: PostgresAuthDispatchStore, *, seconds: float = 5) -> None:
        if type(store) is not PostgresAuthDispatchStore:
            raise CommandUnassembledError("pending_source_misbound")
        self.store, self.seconds = store, seconds

    async def pending(self, *, limit: int) -> tuple[PendingDispatch, ...]:
        if limit < 1:
            raise CommandUnassembledError("pending_limit_invalid")
        async with transaction(self.store.engine, self.seconds) as connection:
            # The same protected-store qualification every other reader of these tables performs:
            # role, database, schema and relation pins, or nothing is read at all.
            await self.store.qualify(connection)
            rows = (
                (await connection.execute(text(_PENDING_SQL), {"tenant": self.store.tenant, "limit": limit}))
                .mappings()
                .all()
            )
        return tuple(
            PendingDispatch(
                command_id=row["command_id"],
                admission_ref=row["admission_ref"],
                resource_ref=row["resource_ref"],
                operation=row["operation"],
                state=row["state"],
                authorization_until=row["authorization_until"],
                lease_until=row["lease_until"],
                guide_identity_ref=row["guide_identity_ref"],
            )
            for row in rows
            if row["state"] in _DRAINABLE
        )


@dataclass(frozen=True, slots=True, repr=False)
class _PublishedStart:
    """The verified head payloads and their pins for one admitted intake."""

    facts: StartFacts
    guide: GuideIdentity
    authority: ResourceAuthority
    policy: DocumentPolicy
    custody: tuple[DocumentCustody, ...]
    pins: tuple[Pin, ...]


class PublishedCommandSource:
    """Assembles the immutable start command for one admitted intake from published sources."""

    def __init__(
        self,
        *,
        composition: AuthProductionComposition,
        intake_store: PostgresIntakeStore,
        definition: Definition,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        if type(composition) is not AuthProductionComposition or type(definition) is not Definition:
            raise CommandUnassembledError("command_source_misbound")
        self.composition, self.intake_store, self.definition, self.clock = (
            composition,
            intake_store,
            definition,
            clock,
        )

    @property
    def reader(self) -> NativeAuthReader:
        return self.composition.native

    @staticmethod
    def _exact[T](expected: type[T], value: object, token: str) -> T:
        """A published payload of any other shape is an installation fault, not a weaker fact."""
        if type(value) is not expected:
            raise CommandUnassembledError(token)
        return value

    @staticmethod
    def _pin(kind: str, resource_ref: str, row: Any) -> Pin:
        """The pin of one head row. Column mapping proven by `native_authority.py:447-455`."""
        return Pin(
            kind=cast(Any, kind),
            resource_ref=resource_ref,
            head_generation=row["generation_"],
            source=parse_model(SourceProvenance, strict_loads(row["source_"])),
            payload_digest=row["payload_digest_"],
        )

    def _workload_ref(self) -> str:
        for material in self.composition.config.client.signing:
            if material.purpose == _START_PURPOSE:
                return material.issuer
        raise CommandUnassembledError("start_signing_material_absent")

    @staticmethod
    def _admission(publication: object, command_id: str) -> AuditIntent:
        """The `AuditIntent` the command binds, taken from the published admission head.

        `publish_admission` (`gateway/intake/native_source_lifecycle.py:1039`) is idempotent — it
        returns the already-issued publication when one exists — and it is the only thing that puts
        the `audit_intent` head where `observation(read=False)` will look for it
        (`native_authority.py:463-470` re-reads that head and compares this exact `source`).
        """
        if type(publication) is not InputPublication or publication.kind != "audit_intent":
            raise CommandUnassembledError("admission_unpublished")
        payload = publication.payload
        if type(payload) is not AuditIntentPayload:
            raise CommandUnassembledError("admission_payload_shape")
        if payload.command_id != command_id or payload.operation != "auth.start":
            raise CommandUnassembledError("admission_command_mismatch")
        return AuditIntent(
            intent_ref=publication.resource_ref,
            admitted_command_id=payload.command_id,
            admitted_digest=payload.admitted_digest,
            source=publication.source,
        )

    async def _single(
        self, db: AsyncConnection, sql: str, params: dict[str, str], *, absent: str, ambiguous: str
    ) -> Sequence[str]:
        rows = (
            (await db.execute(text(sql.format(head=self.reader.table("mzo_auth_input_head"))), params))
            .scalars()
            .all()
        )
        candidates = sorted({row for row in rows if type(row) is str})
        if not candidates:
            raise CommandUnassembledError(absent)
        if len(candidates) > 8:
            # A candidate filter is not a search: an intake with a crowd of matching heads is a
            # broken installation, and verifying them one by one would just be a slow refusal.
            raise CommandUnassembledError(ambiguous)
        return candidates

    async def _facts(self, db: AsyncConnection, intake_ref: str) -> tuple[StartFacts, Any]:
        candidates = await self._single(
            db,
            _HEAD_FACTS_CANDIDATES,
            {"tenant": self.reader.binding.scope.tenant, "intake": intake_ref},
            absent="start_facts_unpublished",
            ambiguous="start_facts_ambiguous",
        )
        found: list[tuple[StartFacts, Any]] = []
        for candidate in candidates:
            payload, row, _until = await self.reader.head(db, "start_facts", candidate)
            facts = self._exact(StartFacts, payload, "start_facts_shape")
            if facts.intake_ref == intake_ref and facts.facts_ref == candidate:
                found.append((facts, row))
        if len(found) != 1:
            raise CommandUnassembledError("start_facts_unpublished" if not found else "start_facts_ambiguous")
        return found[0]

    async def _authority(
        self, db: AsyncConnection, *, principal_ref: str, guide_ref: str
    ) -> tuple[ResourceAuthority, Any]:
        candidates = await self._single(
            db,
            _HEAD_AUTHORITY_CANDIDATES,
            {"tenant": self.reader.binding.scope.tenant, "principal": principal_ref},
            absent="resource_authority_unpublished",
            ambiguous="resource_authority_ambiguous",
        )
        found: list[tuple[ResourceAuthority, Any]] = []
        for candidate in candidates:
            payload, row, _until = await self.reader.head(db, "resource_authority", candidate)
            authority = self._exact(ResourceAuthority, payload, "resource_authority_shape")
            if (
                authority.authority_ref == candidate
                and authority.actor.principal_ref == principal_ref
                and authority.action == "auth.start"
                and authority.resource_kind == "guide"
                and authority.resource_ref == guide_ref
            ):
                found.append((authority, row))
        if len(found) != 1:
            raise CommandUnassembledError(
                "resource_authority_unpublished" if not found else "resource_authority_ambiguous"
            )
        return found[0]

    async def _published(self, actor: Actor, item: PendingDispatch) -> _PublishedStart:
        """One REPEATABLE READ READ ONLY snapshot over every head the command pins.

        One snapshot, so a revocation landing mid-assembly can never be observed half-applied
        across the six reads — the same isolation `published_authorities` takes for the same
        reason (`native_authority.py:357-358`).
        """
        if item.guide_identity_ref is None:
            raise CommandUnassembledError("guide_identity_absent")
        async with self.reader.engine.connect() as db:
            await db.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"))
            await self.reader.qualified(db)
            actor_payload, actor_row, _ = await self.reader.head(db, "actor", actor.principal_ref)
            self._exact(MembershipProjection, actor_payload, "actor_head_shape")
            guide_payload, guide_row, _ = await self.reader.head(db, "guide", item.guide_identity_ref)
            guide = self._exact(GuideIdentity, guide_payload, "guide_head_shape")
            facts, facts_row = await self._facts(db, item.resource_ref)
            policy_payload, policy_row, _ = await self.reader.head(
                db, "document_policy", facts.documentary_assessment_ref
            )
            policy = self._exact(DocumentPolicy, policy_payload, "document_policy_shape")
            authority, authority_row = await self._authority(
                db, principal_ref=actor.principal_ref, guide_ref=guide.guide_identity_ref
            )
            pins = [
                self._pin("actor", actor.principal_ref, actor_row),
                self._pin("guide", guide.guide_identity_ref, guide_row),
                self._pin("start_facts", facts.facts_ref, facts_row),
                self._pin("document_policy", policy.assessment_ref, policy_row),
                self._pin("resource_authority", authority.authority_ref, authority_row),
            ]
            custody: list[DocumentCustody] = []
            for document in facts.document_refs:
                record_payload, record_row, _ = await self.reader.head(
                    db, "document_custody", document.document_ref
                )
                record = self._exact(DocumentCustody, record_payload, "document_custody_shape")
                custody.append(record)
                pins.append(self._pin("document_custody", document.document_ref, record_row))
        # `validate_pins` requires strictly ascending (kind, resource_ref); sorting here is the
        # only place the order is decided, so it cannot drift between kinds.
        return _PublishedStart(
            facts=facts,
            guide=guide,
            authority=authority,
            policy=policy,
            custody=tuple(custody),
            pins=tuple(sorted(pins, key=lambda pin: (pin.kind, pin.resource_ref))),
        )

    async def prepared(self, item: PendingDispatch) -> PreparedDispatch:
        if item.operation == "auth.documents.respond":
            # Module docstring: the document plane has no command producer in `src/` yet.
            raise CommandUnassembledError("document_command_source_absent")
        if item.operation != "auth.start":
            raise CommandUnassembledError("unknown_operation")
        store = self.composition.source.protected
        original = await store.original_identity(item.command_id)
        actor = cast(Actor, await self.composition.source.actor_for_original(original))
        submission = await self.intake_store.admitted_submission(
            original.principal.principal_ref, item.command_id
        )
        if type(submission) is not AuthIntakeSubmission:  # pragma: no cover - store's own contract
            raise CommandUnassembledError("submission_shape")
        admission = self._admission(
            await self.composition.publish_admission(item.command_id), item.command_id
        )
        published = await self._published(actor, item)
        command = build_start_command(
            scope=self.reader.binding.scope,
            workload_ref=self._workload_ref(),
            actor=actor,
            intake_ref=item.resource_ref,
            request=submission,
            admission=admission,
            definition=self.definition,
            facts=published.facts,
            guide=published.guide,
            authority=published.authority,
            policy=published.policy,
            custody=published.custody,
            pins=published.pins,
            now=self.clock(),
        )
        return PreparedDispatch(command=command, facts=published.facts)


def _conformance(
    pending: PostgresPendingIntakes,
    commands: PublishedCommandSource,
    components: Any,
    store: PostgresAuthDispatchStore,
) -> tuple[PendingIntakeSource, PreparedCommandSource, DispatchTarget, ReceiptAuthority]:
    """Static proof that the concrete objects satisfy the engine's ports; checked by mypy.

    `components` is `AuthIntakeComponents` — typed `Any` only because importing it here would
    pull the whole portal composition into the daemon's import graph for a type check.
    """
    return pending, commands, components, store

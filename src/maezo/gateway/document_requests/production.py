"""Production implementers for the document-request bridge (WP-J1-03).

The bridge in this package had **no production implementer of any of its authority
ports**: `compose()` (`composition.py`) demands a `DocumentRequestPolicySource`, a
`CompletionAuthority` and two `SystemPublicationSource`s, and every one of them existed
only as a test stand-in. `DocumentRequestHost` (`runtime/worker_runtime/document_requests.py`)
had zero constructions anywhere. This module supplies the two that the published AUTH
input plane can honestly back, and the single composition entrypoint that refuses to build
a host until the remaining deployment input is actually installed.

What this module does NOT do, deliberately:

* It never authors a `DocumentPolicy`. The document policy is an attested, published
  `document_policy` head; this module reads it, verifies it, and republishes the byte-identical
  publication so the bridge obtains the receipt it needs. Rewriting an upstream payload under
  the upstream's own `SourceProvenance` would forge that source's attestation.
* It never invents a recipient. Recipients come from the published `resource_authority` heads
  whose `action` is `auth.documents.respond`.
* It supplies no `SystemPublicationSource`. That port is the *system grant* plane (which
  producer identity may send which notice to which recipients) and it is owned by a separate
  qualified deployment source with its own DB role; a thin local imitation would be exactly the
  no-op policy source this package refuses. `build_document_request_host` therefore takes both
  publication sources as required arguments and refuses without them.

OWNER DECISION #18 (2026-09-12) — document-request recipients are the provider **and** the
beneficiary, via an active `resource_authority`. Option (B) of `J1-DESIGN.md` §4.3. This module
is where that decision becomes enforceable: `PublishedDocumentPolicySource` refuses to plan a
request whose published policy does not name *exactly* the principals that currently hold an
active `auth.documents.respond` authority over the case. A policy that omits a beneficiary who
holds one is refused just as loudly as a policy that names a principal who holds none — the
former is how the beneficiary silently stops receiving the request, and silence is the failure
mode this bridge exists to prevent.

The decision is enforced as a *consistency invariant between two attested publications*, not as
a rule computed here. Nothing in this file decides who may respond to a document request; the
published authority heads are that decision, and the published policy must agree with them.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Literal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from maezo.gateway.human.auth_profile import (
    DocumentPolicy,
    InputPublication,
    PublicationReceipt,
    ResourceAuthority,
    Scope,
)
from maezo.gateway.human.auth_publisher import AuthInputPublisher, AuthPublicationSnapshot
from maezo.gateway.human.auth_transport import AuthUnavailableError
from maezo.gateway.human.read_profile import ArtifactPin, SourceProvenance, digest, parse_model, wire
from maezo.gateway.human.read_publisher import SourceFreezeLease, SourceSnapshot
from maezo.gateway.intake.native_authority import NativeAuthReader
from maezo.gateway.native_fetch.models import FetchProfile, sha
from maezo.gateway.native_fetch.transport import AdmissionLease, NativeAdmissionProvider, NativeTLS
from maezo.portal.engine.profile import canonicalize, strict_loads

from .admission import SystemPublicationSource
from .completion import CompletionAuthority, CompletionLease
from .composition import BridgePlacement, compose
from .models import SystemProducer, SystemRecipient, require
from .producer import DocumentRequestPolicySource, NoticePlan
from .transport import ProducerObservation

if TYPE_CHECKING:  # pragma: no cover - import kept out of the runtime layer boundary
    from maezo.runtime.worker_runtime.document_requests import DocumentRequestHost

# The one action that authorises a party to answer a document request. It is also the action
# the engine-side document command re-checks (`HumanAuthDocumentCommand`), so the recipient set
# and the set of parties whose response will be accepted are the same set by construction.
RESPOND_ACTION = "auth.documents.respond"

# Decision #18 names two audiences. `staff` is structurally excluded: a staff principal holds no
# vínculo, and a published `auth.documents.respond` authority naming one is an installation
# fault, never a weaker permission (see `_recipient`).
RecipientAudience = Literal["beneficiary", "provider"]


def _unavailable() -> AuthUnavailableError:
    return AuthUnavailableError()


def recipient_identity(scope: Scope, authority: ResourceAuthority) -> str:
    """The canonical recipient identity of one published authority.

    Bound to the full actor tuple, not to `principal_ref` alone: a membership revision bump is
    a different delegated identity, and two inbox rows for "the same person" under different
    memberships must never collapse onto one another.
    """
    return digest(
        {
            "schema": "maezo.auth-document-request.recipient-identity.v1",
            "tenant": scope.tenant,
            "environment": scope.environment,
            "principal_ref": authority.actor.principal_ref,
            "issuer": authority.actor.issuer,
            "subject": authority.actor.subject,
            "membership_revision": authority.actor.membership_revision,
            "audience": authority.actor.audience,
        }
    )


@dataclass(frozen=True, slots=True, repr=False)
class RespondAuthority:
    """One verified active respond-authority over the case, with its observation ceiling."""

    authority: ResourceAuthority
    ceiling: datetime


class CaseRespondAuthorities:
    """Published `auth.documents.respond` heads over ONE case, selected *by resource*.

    `NativeAuthReader.published_authorities` answers the browser-facing question ("what may
    this principal do?"). The bridge asks the inverse question ("who may answer this case?"),
    which no reader on `main` answers. The candidate query differs; the *verification* does not:
    every candidate is re-read through `NativeAuthReader.head`, the single sanctioned path
    (publisher purpose, installed source grant, payload digest, version join, source lifetime).

    A candidate that fails verification is omitted, never softened — omission can only ever
    remove a recipient, and the caller refuses when the resulting set does not cover the
    published policy. Reads run in one `REPEATABLE READ READ ONLY` snapshot so a concurrent
    revocation is never observed half-applied.
    """

    def __init__(self, reader: NativeAuthReader) -> None:
        if type(reader) is not NativeAuthReader:
            raise _unavailable()
        self.reader = reader

    @property
    def tenant(self) -> str:
        return self.reader.binding.scope.tenant

    async def read(self, *, case_ref: str, request_ref: str) -> tuple[RespondAuthority, ...]:
        """Every verified active respond-authority over this exact case.

        `request_ref` narrows the selection the way the published contract allows: an authority
        that names a request answers only that request; one that names none answers the case.
        """
        if type(case_ref) is not str or not case_ref or type(request_ref) is not str or not request_ref:
            raise _unavailable()
        reader = self.reader
        found: list[RespondAuthority] = []
        async with reader.engine.connect() as db:
            await db.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"))
            _installed, until = await reader.qualified(db)
            refs = (
                (
                    await db.execute(
                        text(
                            "SELECT resource_ FROM "
                            + reader.table("mzo_auth_input_head")
                            + " WHERE tenant_=:tenant AND kind_='resource_authority' AND state_"
                            "='active' AND payload_::jsonb->>'action'=:action AND payload_"
                            "::jsonb->>'resource_kind'='case' AND payload_::jsonb->>'resource_ref'"
                            "=:case"
                        ),
                        {"tenant": reader.binding.scope.tenant, "action": RESPOND_ACTION, "case": case_ref},
                    )
                )
                .scalars()
                .all()
            )
            for ref in sorted(set(refs)):
                try:
                    authority, _row, deadline = await reader.head(db, "resource_authority", ref)
                except AuthUnavailableError:
                    continue
                if (
                    type(authority) is not ResourceAuthority
                    or authority.action != RESPOND_ACTION
                    or authority.state != "active"
                    or authority.resource_kind != "case"
                    or authority.resource_ref != case_ref
                    or (authority.request_ref is not None and authority.request_ref != request_ref)
                ):
                    continue
                found.append(RespondAuthority(authority, min(until, deadline)))
        return tuple(found)

    async def publication(self, *, case_ref: str) -> tuple[InputPublication, DocumentPolicy, datetime]:
        """The published `document_policy` head for this case, with its exact publication request.

        `head` verifies the row; the version row carries the *publication* the upstream source
        actually sent, which is what the bridge must republish byte-for-byte to obtain a receipt.
        Reading it back (rather than reconstructing one) is what keeps the upstream attestation
        intact: the bridge never signs a publication of its own.
        """
        reader = self.reader
        async with reader.engine.connect() as db:
            await db.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"))
            _installed, until = await reader.qualified(db)
            policy, row, deadline = await reader.head(db, "document_policy", case_ref)
            if type(policy) is not DocumentPolicy:
                raise _unavailable()
            publication = await self._publication_request(db, row, case_ref)
        if publication.payload != policy or publication.payload_digest != digest(policy):
            raise _unavailable()
        return publication, policy, min(until, deadline)

    async def _publication_request(self, db: AsyncConnection, row: Any, case_ref: str) -> InputPublication:
        reader = self.reader
        version = (
            (
                await db.execute(
                    text(
                        "SELECT request_,digest_ FROM "
                        + reader.table("mzo_auth_input_version")
                        + " WHERE tenant_=:tenant AND kind_='document_policy' AND resource_=:resource"
                        " AND generation_=:generation"
                    ),
                    {
                        "tenant": reader.binding.scope.tenant,
                        "resource": case_ref,
                        "generation": row["generation_"],
                    },
                )
            )
            .mappings()
            .one()
        )
        publication = parse_model(InputPublication, strict_loads(version["request_"]))
        if (
            digest(publication) != version["digest_"]
            or version["digest_"] != row["publication_digest_"]
            or publication.kind != "document_policy"
            or publication.state != "active"
            or publication.resource_ref != case_ref
            or publication.scope.tenant != reader.binding.scope.tenant
        ):
            raise _unavailable()
        return publication


def _recipient(
    entry: RespondAuthority, *, scope: Scope, policy_digest: str, now: datetime
) -> SystemRecipient:
    """One verified authority projected into one inbox recipient. Never a decision."""
    authority = entry.authority
    audience = authority.actor.audience
    # Decision #18 names provider and beneficiary. A staff actor on a respond-authority means
    # the publisher wrote a row this plane must never honour — refuse loudly rather than drop
    # it silently, because a dropped row would leave the remaining set looking self-consistent.
    require(audience in ("beneficiary", "provider"), "denied")
    require(
        authority.consent_state != "revoked"
        and not (authority.legal_basis == "consent" and authority.consent_state != "valid")
        and authority.valid_from <= now < authority.valid_until
        and now < entry.ceiling,
        "denied",
    )
    # The delegated party on the actor's own side must actually be named by the authority.
    require(authority.beneficiary_ref != "", "denied")
    if audience == "provider":
        require(authority.provider_ref is not None and authority.provider_ref != "", "denied")
    return SystemRecipient(
        principal_ref=authority.actor.principal_ref,
        identity_digest=recipient_identity(scope, authority),
        audience=audience,
        source_revision=str(authority.source.source_revision),
        policy_digest=policy_digest,
        valid_until=min(entry.ceiling, authority.valid_until, authority.source.valid_until),
    )


def resolve_document_recipients(
    authorities: tuple[RespondAuthority, ...],
    *,
    scope: Scope,
    policy: DocumentPolicy,
    now: datetime,
) -> tuple[SystemRecipient, ...]:
    """Decision #18, enforced: the published policy must name exactly the active authorities.

    Fail-closed in both directions, because both directions are silent failures:

    * a principal named by the policy with **no** active respond-authority would receive a
      notice about a case nobody attested they may act on;
    * a principal holding an active respond-authority who is **not** named by the policy would
      never be told the documents were requested — the beneficiary half of decision #18.

    A provider recipient is mandatory: the BPMN service task is "Solicitar documentacao ao
    prestador" and a request that reaches nobody on the provider side is not this request.
    Ambiguity (two authorities for one principal) is refused, never resolved by preference.
    """
    recipients = tuple(_recipient(entry, scope=scope, policy_digest=digest(policy), now=now) for entry in authorities)
    principals = [r.principal_ref for r in recipients]
    require(len(set(principals)) == len(principals), "denied")
    require(set(principals) == set(policy.recipient_principal_refs), "denied")
    require(any(r.audience == "provider" for r in recipients), "denied")
    ordered = tuple(sorted(recipients, key=lambda r: r.identity_digest))
    identities = [r.identity_digest for r in ordered]
    require(len(set(identities)) == len(identities), "denied")
    return ordered


@dataclass(frozen=True, slots=True, repr=False)
class _PolicyFreeze:
    """The bridge's retained observation of one already-attested publication.

    This is not a source barrier of the bridge's own: the upstream source already froze and
    committed this publication, and `AuthInputPublisher` will find the committed receipt rather
    than send anything new. What the lease guards is the *observation* — the window in which the
    bridge may still act on what it read — so that an expired or superseded head cannot be
    carried into a send.
    """

    provenance: SourceProvenance
    payload_bytes: bytes = field(repr=False)
    until: datetime
    clock: Callable[[], datetime] = field(repr=False)

    def live(self) -> None:
        now = self.clock()
        require(now < self.until and now < self.provenance.valid_until, "unavailable")

    def verify(self, raw: bytes) -> None:
        self.live()
        require(type(raw) is bytes and raw == self.payload_bytes, "unavailable")

    def committed(self, receipt: PublicationReceipt) -> None:
        require(type(receipt) is PublicationReceipt, "unavailable")

    def uncertain(self) -> None:
        # The upstream publication is immutable and already committed; an uncertain send leaves
        # nothing of the bridge's to unwind. The observation window still closes on its own.
        return None

    def lease(self) -> SourceFreezeLease:
        return SourceFreezeLease(
            provenance=self.provenance,
            verify=self.verify,
            live=self.live,
            committed=self.committed,
            uncertain=self.uncertain,
        )


class PublishedDocumentPolicySource(DocumentRequestPolicySource):
    """`DocumentRequestPolicySource` over the published AUTH input heads. Decision #18.

    `policy()` returns the *attested* publication unchanged. `notice()` projects the recipients
    from the same authority heads, re-read at notice time, so a delegation revoked between the
    two calls makes the producer's own `{principal_refs} == set(policy.recipient_principal_refs)`
    check fail and the request refuse rather than deliver to a revoked party.

    `successor()` is deliberately left refusing (the abstract base's default): a same-occurrence
    W4/W5 refresh needs a source-owned invocation authority that no installed source on `main`
    issues, and admitting one here would let an invocation reference act as a permission — the
    exact confusion the base class's docstring forbids.
    """

    def __init__(
        self,
        *,
        authorities: CaseRespondAuthorities,
        template: ArtifactPin,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        if type(authorities) is not CaseRespondAuthorities or type(template) is not ArtifactPin:
            raise _unavailable()
        self.authorities, self.template, self.clock = authorities, template, clock

    def _bound(self, observation: ProducerObservation) -> tuple[str, str]:
        observation.current()
        context = observation.context
        require(context.scope.tenant == self.authorities.tenant, "denied")
        return context.case_ref, context.request_ref

    async def policy(
        self, observation: ProducerObservation, original: InputPublication | None
    ) -> AuthPublicationSnapshot:
        """Restore the exact original durable source freeze if already sealed."""
        case_ref, request_ref = self._bound(observation)
        publication, document_policy, until = await self.authorities.publication(case_ref=case_ref)
        # The producer only ever requests documents for an unanswered request.
        require(document_policy.submitted_response_digest is None, "conflict")
        require(
            document_policy.resource_kind == "case"
            and document_policy.resource_ref == case_ref
            and document_policy.request_ref == request_ref,
            "conflict",
        )
        # Restart must resume the byte-identical publication the first attempt sealed, never a
        # newer head that happens to be active now.
        if original is not None:
            require(wire(original) == wire(publication), "conflict")
        entries = await self.authorities.read(case_ref=case_ref, request_ref=request_ref)
        # Evaluated for its refusals: the publication must not be carried forward when the
        # published policy and the published authorities disagree (decision #18).
        resolve_document_recipients(
            entries, scope=publication.scope, policy=document_policy, now=self.clock()
        )
        observation.current()
        freeze = _PolicyFreeze(
            provenance=publication.source,
            payload_bytes=canonicalize(wire(document_policy)),
            until=min(until, publication.valid_until),
            clock=self.clock,
        )
        freeze.live()
        return AuthPublicationSnapshot(
            publication=publication,
            snapshot=SourceSnapshot(publication.source, document_policy, freeze.lease()),
            acknowledge=freeze.committed,
        )

    async def notice(
        self, observation: ProducerObservation, policy: DocumentPolicy, receipt: PublicationReceipt
    ) -> NoticePlan:
        """Read the qualified template/crosswalk and original historical commands."""
        case_ref, request_ref = self._bound(observation)
        require(
            type(policy) is DocumentPolicy
            and type(receipt) is PublicationReceipt
            and receipt.kind == "document_policy"
            and receipt.state == "active"
            and receipt.resource_ref == policy.assessment_ref
            and receipt.payload_digest == digest(policy),
            "conflict",
        )
        entries = await self.authorities.read(case_ref=case_ref, request_ref=request_ref)
        recipients = resolve_document_recipients(
            entries, scope=receipt.scope, policy=policy, now=self.clock()
        )
        observation.current()
        # No prior commands: this source admits only the initial occurrence of a request
        # (`successor()` refuses), and the producer seeds exactly one outer command per
        # `request_ref`, so there is no earlier authenticated command of this occurrence to
        # carry a recipient's existing notice forward from. An invented one would rebind a
        # sealed body to newer assessment bytes.
        return NoticePlan(template=self.template, recipients=recipients, prior_commands=())


class NativeCompletionAuthority(CompletionAuthority):
    """`CompletionAuthority` over the installed native admission provider, outcome purpose.

    The completion of the external task is a *different* designation from the fetch that
    acquired it: `AdmissionLease.guard` admits `fetch` and `outcome`, and this authority
    acquires only `outcome`. Handing the fetch provider's lease to the completion client would
    let the acquisition that read the task also close it, which is precisely the separation the
    port's docstring ("never fetch authority") exists to keep.
    """

    def __init__(
        self,
        provider: NativeAdmissionProvider,
        *,
        capability_digest: str,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        if not isinstance(provider, NativeAdmissionProvider):
            raise _unavailable()
        require(
            type(capability_digest) is str and len(capability_digest) == 64, "unavailable"
        )
        self.provider, self.capability_digest, self.clock = provider, capability_digest, clock

    async def acquire(self, selection_digest: str, binding: bytes, purpose: str) -> CompletionLease:
        require(purpose == "outcome", "denied")
        lease = await self.provider.acquire(selection_digest, purpose, binding)
        require(type(lease) is AdmissionLease, "unavailable")
        lease.guard(self.clock)
        require(
            lease.purpose == "outcome"
            and lease.selection_digest == selection_digest
            and lease.binding == binding
            and lease.capability_digest == self.capability_digest
            and self.capability_digest in lease.capabilities,
            "denied",
        )
        return CompletionLease(
            purpose=lease.purpose,
            selection_digest=lease.selection_digest,
            binding=lease.binding,
            capability_digest=lease.capability_digest,
            activation_ref=lease.activation_ref,
            database_incarnation=lease.database_incarnation,
            not_before=lease.not_before,
            valid_until=lease.valid_until,
            current=lambda: lease.guard(self.clock),
        )


async def build_document_request_host(
    *,
    placement: BridgePlacement,
    producer: SystemProducer,
    tls: NativeTLS,
    fetch_profile: FetchProfile,
    designation_digest: str,
    fetch_authority: NativeAdmissionProvider,
    outcome_authority: NativeAdmissionProvider,
    completion_capability: bytes,
    completion_catalog: bytes,
    completion_catalog_digest: str,
    authorities: CaseRespondAuthorities,
    notice_template: ArtifactPin,
    policy_publisher: AuthInputPublisher,
    phi_source: SystemPublicationSource,
    metadata_source: SystemPublicationSource,
    body_keys: dict[str, bytes],
    active_body_key_id: str,
    body_valid_until: datetime,
    provenance_key_id: str,
    provenance_key: bytes,
    journal_key_id: str,
    journal_key: bytes,
    generic_topics: Iterable[str],
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> DocumentRequestHost:
    """Compose the bridge and install the dedicated host. The only constructor of either.

    Every authority is a positively supplied deployment input. There is no default, no
    environment fallback and no locally manufactured stand-in for any of them — in particular
    `phi_source` / `metadata_source` are the separate system-grant source, which this package
    does not and must not implement (module docstring).

    `generic_topics` is the *live* topic set of the generic worker harness. Exclusivity is
    checked here, before anything is composed, and again inside `DocumentRequestHost.__init__`:
    two consumers of `operadora.auth.request_documents` would both fetch-and-lock the same
    external task, and the one without PHI custody would complete it with an empty body.
    """
    from maezo.runtime.worker_runtime.document_requests import DocumentRequestHost

    topics = tuple(generic_topics)
    # Refuse before composing: composing opens native channels and DB connections, and a
    # refusal after that point would leave them to be unwound on an error path.
    DocumentRequestHost.assert_exclusive(topics)
    completion_authority = NativeCompletionAuthority(
        outcome_authority, capability_digest=sha(completion_capability), clock=clock
    )
    policy_source = PublishedDocumentPolicySource(
        authorities=authorities, template=notice_template, clock=clock
    )
    installed = await compose(
        placement=placement,
        producer=producer,
        tls=tls,
        fetch_profile=fetch_profile,
        designation_digest=designation_digest,
        fetch_authority=fetch_authority,
        completion_authority=completion_authority,
        completion_capability=completion_capability,
        completion_catalog=completion_catalog,
        completion_catalog_digest=completion_catalog_digest,
        policy_source=policy_source,
        policy_publisher=policy_publisher,
        phi_source=phi_source,
        metadata_source=metadata_source,
        body_keys=body_keys,
        active_body_key_id=active_body_key_id,
        body_valid_until=body_valid_until,
        provenance_key_id=provenance_key_id,
        provenance_key=provenance_key,
        journal_key_id=journal_key_id,
        journal_key=journal_key,
    )
    try:
        return DocumentRequestHost(
            installed=installed,
            tls=tls,
            profile=fetch_profile,
            authority=fetch_authority,
            generic_topics=topics,
            clock=clock,
        )
    except BaseException:
        await installed.close()
        raise

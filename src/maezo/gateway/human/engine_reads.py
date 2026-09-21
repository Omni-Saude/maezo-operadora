"""Concrete Q1 adapters and bounded request-owned canonical-byte CT/CA custody.

No receipt travels in a Q1 DTO. Equal Pydantic revalidated copies are valid; another
request cannot import receipts, overwrite a row, or refresh a failed predecessor.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

import httpx

from maezo.portal.api.session import HumanSessionResolver
from maezo.portal.contracts.models import HumanPrincipal
from maezo.portal.contracts.queues import TaskQueueRequest
from maezo.portal.engine.profile import canonicalize

from .completion import (
    CompletionAck,
    CompletionAuditSink,
    CompletionOutcome,
    DirectTaskCompletion,
    PseudonymizedNotes,
)
from .credentials import HumanCommandCredentialPartition
from .gateway import HumanGateway
from .models import AuthoritativeTask, CurrentTaskAuthority
from .ports import AuthorityProjection, DurableAdmission, HumanTaskTransport
from .queue import (
    CandidateWindow,
    CatalogExpectation,
    CatalogExpectationSource,
    CatalogTrustAnchor,
    HumanTaskQuery,
    QueueBinding,
    TaskDisclosureGrant,
    TaskDisclosureSource,
)
from .queue_cursor import AeadQueueCursorCustody
from .read_credentials import unavailable
from .read_profile import (
    AuthorityValue,
    CatalogValue,
    DisclosureValue,
    DiscoverValue,
    NativeContinuity,
    TaskValue,
    digest,
    wire,
)
from .read_transport import PortalReadClient, ReadResult


@dataclass(frozen=True, slots=True, repr=False)
class _TaskEntry:
    task: AuthoritativeTask = field(repr=False)
    canonical: bytes = field(repr=False)
    continuity: NativeContinuity = field(repr=False)


@dataclass(frozen=True, slots=True, repr=False)
class _AuthorityEntry:
    authority: CurrentTaskAuthority = field(repr=False)
    canonical: bytes = field(repr=False)
    principal: bytes = field(repr=False)
    continuity: NativeContinuity = field(repr=False)


class EngineReadBundle:
    """Construct only in the per-request factory; no mutable last-row or global cache."""

    def __init__(
        self, *, client: PortalReadClient, anchor: CatalogTrustAnchor, cursor: AeadQueueCursorCustody
    ) -> None:
        if anchor.scope != client.partition.scope or cursor.scope != anchor.scope:
            raise unavailable()
        self.scope, self.anchor, self.client, self.cursor = anchor.scope, anchor, client, cursor
        self._owner = object()
        self._composed = False
        self._context = client.read_context_id
        self._tasks: dict[str, _TaskEntry | None] = {}
        self._authorities: dict[str, _AuthorityEntry | None] = {}
        self._disclosed: set[str] = set()
        # INTERIM completion (DL-0049): at most one completion per task per bundle, and a
        # bundle is one BFF request. A retry is a new request, a new read and a new authority.
        self._completed: set[str] = set()
        self._closed = False

    def guard(self) -> None:
        if self._closed or self._context != self.client.read_context_id:
            raise unavailable()
        self.client.partition.guard()

    def poison(self) -> None:
        self._closed = True
        self._tasks.clear()
        self._authorities.clear()
        self._disclosed.clear()
        self._completed.clear()
        self.client.partition.close()

    def catalog(self, value: CatalogValue, result: ReadResult) -> CatalogExpectation:
        self.guard()
        value.verified_artifact(self.anchor)
        e = value.expectation
        if (
            not e.source_observed_at <= result.source_observed_at
            or e.valid_until > result.valid_until
            or e.valid_until <= self.client.partition.guard()
        ):
            raise unavailable()
        return e

    def check_continuity(
        self, ct: NativeContinuity, task: AuthoritativeTask, result: ReadResult, *, stage: str
    ) -> None:
        self.guard()
        c = ct.claims
        admission, signing = self.client.partition.admission, self.client.partition.signing
        assert admission is not None and signing is not None
        a, b = admission.record, c.binding
        if (
            c.stage != stage
            or b.scope != self.scope
            or b.engine_name != a.engine_name
            or b.database_incarnation != a.database_incarnation
            or b.read_deployment_ref != a.read_deployment_ref
            or b.read_deployment_digest != a.read_deployment_digest
            or b.runtime_admission_generation != a.runtime_admission_generation
            or b.read_context_id != self._context
            or b.requester != signing.requester
            or c.origin_request_digest != result.request_digest
            or c.task_digest != digest(task)
            or c.snapshot_digest != digest(task.snapshot)
            or c.snapshot_at != task.snapshot.snapshot_at
            or c.snapshot_at > c.issued_at
            or c.issued_at > result.source_observed_at
            or c.valid_until > result.valid_until
            or c.valid_until > task.valid_until
            or c.valid_until <= self.client.partition.guard()
        ):
            raise unavailable()

    def task_entry(self, task: AuthoritativeTask) -> _TaskEntry:
        self.guard()
        entry = self._tasks.get(task.snapshot.task_id)
        if entry is None or canonicalize(wire(task)) != entry.canonical:
            raise unavailable()
        return entry


class EngineCatalogExpectationSource(CatalogExpectationSource):
    def __init__(self, bundle: EngineReadBundle) -> None:
        self._b = bundle

    async def current_catalog(self, *, anchor: CatalogTrustAnchor) -> CatalogExpectation:
        try:
            if anchor != self._b.anchor:
                raise unavailable()
            result = await self._b.client.catalog(anchor)
            if not isinstance(result.value, CatalogValue):
                raise unavailable()
            return self._b.catalog(result.value, result)
        except BaseException:
            self._b.poison()
            raise


class EngineHumanTaskQuery(HumanTaskQuery):
    def __init__(self, bundle: EngineReadBundle) -> None:
        self._b, self.scope = bundle, bundle.scope

    async def discover(
        self, principal: HumanPrincipal, request: TaskQueueRequest, *, expected_catalog: CatalogExpectation
    ) -> CandidateWindow:
        try:
            if expected_catalog.anchor != self._b.anchor:
                raise unavailable()
            b = QueueBinding(
                scope=self.scope,
                principal=principal,
                queue=request.queue,
                limit=request.limit,
                catalog_revision=expected_catalog.catalog_revision,
                catalog_ref=self._b.anchor.catalog_ref,
                publisher_ref=self._b.anchor.publisher_ref,
                catalog_digest=expected_catalog.catalog_digest,
            )
            await self._b.cursor.prepare()
            input_grant = await self._b.cursor.resolve(request.cursor, binding=b) if request.cursor else None
            result = await self._b.client.discover(
                principal,
                expected_catalog,
                request.queue,
                request.limit,
                input_grant.after_task_id if input_grant else None,
            )
            self._b.guard()
            value = result.value
            if not isinstance(value, DiscoverValue) or value.binding != b:
                raise unavailable()
            ids = value.task_ids
            lookahead = len(ids) == request.limit + 1
            if (
                len(ids) > request.limit + 1
                or ids != tuple(sorted(set(ids), key=lambda x: x.encode("utf-8")))
                or any(input_grant and task_id <= input_grant.after_task_id for task_id in ids)
                or value.after_task_id != (ids[request.limit - 1] if lookahead else None)
            ):
                raise unavailable()
            until = min(
                result.valid_until,
                expected_catalog.valid_until,
                input_grant.valid_until if input_grant else result.valid_until,
            )
            grant = (
                self._b.cursor.provisional(binding=b, after_task_id=value.after_task_id, valid_until=until)
                if value.after_task_id
                else None
            )
            if grant:
                until = min(until, grant.valid_until)
            self._b.guard()
            return CandidateWindow(
                binding=b,
                task_ids=ids,
                source_observed_at=result.source_observed_at,
                valid_until=until,
                next_cursor=grant.cursor if grant else None,
            )
        except BaseException:
            self._b.poison()
            raise


class EngineHumanTaskTransport(HumanTaskTransport):
    def __init__(self, bundle: EngineReadBundle, completion: DirectTaskCompletion | None = None) -> None:
        self._b, self.scope = bundle, bundle.scope
        # INTERIM (DL-0049). `None` is the shipped state: `complete_task` then refuses through
        # the base class, exactly as this transport did before the interim path existed.
        self._completion = completion

    async def read_task(self, task_id: str) -> AuthoritativeTask:
        try:
            if self._b._closed or task_id in self._b._tasks or len(self._b._tasks) >= 101:
                raise unavailable()
            self._b._tasks[task_id] = None  # reserve before the first await
            result = await self._b.client.task(self._b.anchor, task_id)
            value = result.value
            if (
                not isinstance(value, TaskValue)
                or value.task.snapshot.task_id != task_id
                or value.task.tenant != self.scope.tenant
                or value.task.snapshot.allowed_actions
            ):
                raise unavailable()
            self._b.catalog(value.catalog, result)
            self._b.check_continuity(value.task_continuity, value.task, result, stage="task")
            if value.task.valid_until != value.task_continuity.claims.valid_until:
                raise unavailable()
            self._b._tasks[task_id] = _TaskEntry(
                value.task, canonicalize(wire(value.task)), value.task_continuity
            )
            return value.task
        except BaseException:
            self._b.poison()
            raise

    async def complete_task(
        self,
        task_id: str,
        *,
        resultado: CompletionOutcome,
        notes: PseudonymizedNotes,
        expected_task_revision: int,
    ) -> CompletionAck:
        """INTERIM (DL-0049): the one write this Q2 read transport performs.

        It rides the read bundle on purpose, and the bundle is what makes it safe to exist at
        all: a completion is admitted ONLY for a task whose authoritative snapshot this same
        request already read through `read_task` (CT chain verified, tenant checked, catalog
        pinned) and ONLY at that snapshot's revision. A `task_id` the bundle never read, a
        revision that drifted, a second completion of the same task, an expired read partition
        or a missing capability all refuse — and any failure poisons the bundle, so no later
        port in this request answers from state a failed write touched.

        It does NOT borrow the read signing partition: `PortalReadClient` signs
        `purpose=portal-task-read` envelopes for the six read routes and nothing here changes
        that. The completion travels on its own explicitly composed client. What it lacks — a
        signed envelope, a receipt, an outbox row, an engine-side optimistic fence — is the
        debt DL-0049 registers and the D6 relay of #427 pays.
        """
        try:
            if self._completion is None or self._completion.scope != self.scope:
                raise unavailable()
            entry = self._b._tasks.get(task_id)
            if (
                entry is None
                or entry.task.snapshot.task_id != task_id
                or entry.task.snapshot.task_revision != expected_task_revision
                or task_id in self._b._completed
                or canonicalize(wire(entry.task)) != entry.canonical
            ):
                raise unavailable()
            self._b.guard()
            self._b._completed.add(task_id)  # reserve before the first await
            ack = await self._completion.complete(
                task_id=task_id,
                resultado=resultado,
                notes=notes,
                expected_task_revision=expected_task_revision,
            )
            if (
                type(ack) is not CompletionAck
                or ack.task_id != task_id
                or ack.resultado != resultado
                or ack.consumed_task_revision != expected_task_revision
            ):
                raise unavailable()
            return ack
        except BaseException:
            self._b.poison()
            raise


class EngineTaskAuthority(AuthorityProjection):
    def __init__(self, bundle: EngineReadBundle) -> None:
        self._b, self.scope = bundle, bundle.scope

    async def current_authority(
        self, principal: HumanPrincipal, task: AuthoritativeTask
    ) -> CurrentTaskAuthority:
        try:
            entry = self._b.task_entry(task)
            task_id = task.snapshot.task_id
            if task_id in self._b._authorities:
                raise unavailable()
            self._b._authorities[task_id] = None
            result = await self._b.client.authority(self._b.anchor, principal, entry.task, entry.continuity)
            value = result.value
            if (
                not isinstance(value, AuthorityValue)
                or value.task_continuity_digest != digest(entry.continuity)
                or value.authority.permitted_operations
            ):
                raise unavailable()
            self._b.catalog(value.catalog, result)
            ct = value.authority_continuity
            self._b.check_continuity(ct, entry.task, result, stage="authority")
            if (
                ct.claims.task_continuity_digest != digest(entry.continuity)
                or ct.claims.principal_digest != digest(principal)
                or ct.claims.authority_digest != digest(value.authority)
                or value.authority.valid_until != ct.claims.valid_until
                or ct.claims.native_task_state_digest != entry.continuity.claims.native_task_state_digest
                or ct.claims.catalog_state_digest != entry.continuity.claims.catalog_state_digest
            ):
                raise unavailable()
            old = {digest(c) for c in entry.continuity.claims.ceilings}
            if not old.issubset({digest(c) for c in ct.claims.ceilings}):
                raise unavailable()
            self._b._authorities[task_id] = _AuthorityEntry(
                value.authority, canonicalize(wire(value.authority)), canonicalize(wire(principal)), ct
            )
            return value.authority
        except BaseException:
            self._b.poison()
            raise


class EngineTaskDisclosure(TaskDisclosureSource):
    def __init__(self, bundle: EngineReadBundle) -> None:
        self._b, self.scope = bundle, bundle.scope

    async def classify(
        self, principal: HumanPrincipal, task: AuthoritativeTask, authority: CurrentTaskAuthority
    ) -> TaskDisclosureGrant:
        try:
            entry = self._b.task_entry(task)
            task_id = task.snapshot.task_id
            a = self._b._authorities.get(task_id)
            if (
                a is None
                or task_id in self._b._disclosed
                or a.principal != canonicalize(wire(principal))
                or a.canonical != canonicalize(wire(authority))
            ):
                raise unavailable()
            self._b._disclosed.add(task_id)
            result = await self._b.client.disclosure(
                self._b.anchor, principal, entry.task, entry.continuity, a.authority, a.continuity
            )
            value = result.value
            if (
                not isinstance(value, DisclosureValue)
                or value.task_continuity_digest != digest(entry.continuity)
                or value.authority_continuity_digest != digest(a.continuity)
            ):
                raise unavailable()
            self._b.catalog(value.catalog, result)
            grant = value.grant
            if (
                grant.principal != principal
                or grant.scope != self.scope
                or canonicalize(wire(grant.snapshot)) != canonicalize(wire(entry.task.snapshot))
                or grant.authority_revision != task.authority_revision
                or grant.valid_until > min(result.valid_until, task.valid_until, authority.valid_until)
            ):
                raise unavailable()
            self._b.guard()
            return grant
        except BaseException:
            self._b.poison()
            raise


class EngineReadComposition:
    """Explicit app-owned composition; each call MUST allocate a fresh private bundle.

    The builder's connection pool has application lifespan. It contains no last-task
    state; each HTTP stream is bounded and closed before a port returns. Existing
    command credentials/admission retain their own separate gates and are never used
    to sign these source-free reads.
    """

    def __init__(
        self,
        *,
        new_bundle: Callable[[], EngineReadBundle],
        command_credentials: HumanCommandCredentialPartition,
        command_admission: DurableAdmission,
        transport_pool: httpx.AsyncBaseTransport,
        direct_completion: DirectTaskCompletion | None = None,
        completion_audit: CompletionAuditSink | None = None,
    ) -> None:
        self._new_bundle = new_bundle
        self._transport_pool = transport_pool
        self._closed = False
        self._command_credentials = command_credentials
        self._command_admission = command_admission
        # INTERIM (DL-0049). Both halves are required together or the path stays dark: a
        # completion with no audit link would be exactly the untraceable shortcut §2.5 of the
        # mandate refuses, and an audit sink with no client would claim a trail for nothing.
        if (direct_completion is None) != (completion_audit is None):
            raise unavailable()
        self._direct_completion = direct_completion
        self._completion_audit = completion_audit

    def build(self, resolver: HumanSessionResolver) -> HumanGateway:
        from .ports import BoundHumanPorts

        if self._closed:
            raise unavailable()
        bundle = self._new_bundle()
        if (
            not isinstance(bundle, EngineReadBundle)
            or bundle._closed
            or bundle._composed
            or bundle._tasks
            or bundle._authorities
            or bundle._completed
            or bundle.client._transport is not self._transport_pool
            or bundle.client._owns_transport
        ):
            raise unavailable()
        if self._direct_completion is not None and self._direct_completion.scope != bundle.scope:
            raise unavailable()
        bundle._composed = True
        return HumanGateway(
            resolver=resolver,
            scope=bundle.scope,
            ports=BoundHumanPorts(
                task=EngineHumanTaskTransport(bundle, self._direct_completion),
                authority=EngineTaskAuthority(bundle),
                admission=self._command_admission,
            ),
            completion_audit=self._completion_audit,
            credentials=self._command_credentials,
            query=EngineHumanTaskQuery(bundle),
            catalog_anchor=bundle.anchor,
            catalog_source=EngineCatalogExpectationSource(bundle),
            cursor_custody=bundle.cursor,
            disclosure_source=EngineTaskDisclosure(bundle),
        )

    async def close(self) -> None:
        self._closed = True
        try:
            if self._direct_completion is not None:
                await self._direct_completion.close()
        finally:
            try:
                await self._transport_pool.aclose()
            except Exception:
                raise unavailable() from None

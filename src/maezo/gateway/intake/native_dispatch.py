"""Strict native AUTH dispatch and receipt-first recovery at the audited start seam."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from maezo.tools.mcp_cibseven.transport import ProcessInstance
from uuid import uuid4

from maezo.gateway.human.auth_profile import (
    Actor,
    AuditIntent,
    Definition,
    EffectCommand,
    HumanDocumentCommand,
    HumanReceiptQuery,
    HumanStartCommand,
    NativeEffectReceipt,
    NativeReceiptLookup,
    Pin,
    Scope,
)
from maezo.gateway.human.auth_transport import AuthNativeClient, AuthUnavailableError, bind_receipt
from maezo.gateway.human.read_profile import digest, parse_model, wire

from .native_store import PostgresAuthDispatchStore


@dataclass(frozen=True, slots=True, repr=False)
class AuthEffectLease:
    scope: Scope
    actor: Actor = field(repr=False)
    command_digest: str
    action: Literal["auth.start", "auth.documents.respond", "auth.receipt.read"]
    resource_ref: str
    valid_until: datetime
    live: Callable[[], None] = field(repr=False)

    def guard(self, command: EffectCommand, now: datetime, *, read: bool = False) -> None:
        self.live()
        action = (
            "auth.receipt.read"
            if read
            else "auth.start"
            if isinstance(command, HumanStartCommand)
            else "auth.documents.respond"
        )
        ref = command.intake_ref if isinstance(command, HumanStartCommand) else command.case_ref
        if (self.scope, self.command_digest, self.action, self.resource_ref) != (
            command.scope,
            digest(command),
            action,
            ref,
        ) or now >= self.valid_until:
            raise AuthUnavailableError()
        # Reads may use a renewed membership, but must be the same human identity.
        if (self.actor.principal_ref, self.actor.issuer, self.actor.subject, self.actor.audience) != (
            command.actor.principal_ref,
            command.actor.issuer,
            command.actor.subject,
            command.actor.audience,
        ) or (not read and self.actor != command.actor):
            raise AuthUnavailableError()


class AuthEffectAuthorizationSource(ABC):
    @abstractmethod
    async def current(self, command: EffectCommand, *, read: bool) -> AuthEffectLease:
        """Actual current session + explicit resource action + published source leases.

        Execution requires exact original immutable pins. Read authority is independent;
        changed effect authority cannot rewrite an uncertain command.
        """
        raise NotImplementedError


class AuthDispatcher:
    def __init__(
        self,
        *,
        store: PostgresAuthDispatchStore,
        client: AuthNativeClient,
        authority: AuthEffectAuthorizationSource,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.store, self.client, self.authority, self.clock = store, client, authority, clock

    async def dispatch_documents(self, command: HumanDocumentCommand) -> NativeEffectReceipt:
        if type(command) is not HumanDocumentCommand:
            raise AuthUnavailableError()
        return await self._dispatch(command)

    async def _dispatch(self, command: EffectCommand) -> NativeEffectReceipt:
        command = parse_model(type(command), wire(command))
        await self.store.prove(command)
        completed = await self.store.completed(command)
        if completed is not None:
            read = await self.authority.current(command, read=True)
            read.guard(command, self.clock(), read=True)
            return completed
        claim = await self.store.claim(command.command_id)
        if claim.command != command:
            raise AuthUnavailableError()
        if claim.reconcile_first:
            read = await self.authority.current(command, read=True)
            read.guard(command, self.clock(), read=True)
            query = HumanReceiptQuery(
                schema="human-auth-receipt-query.v1",
                scope=command.scope,
                workload_ref=command.workload_ref,
                actor=read.actor,
                query_id=uuid4().hex,
                operation="auth.start"
                if isinstance(command, HumanStartCommand)
                else "auth.documents.respond",
                command_id=command.command_id,
                expected_command_digest=digest(command),
                intake_or_case_ref=command.intake_ref
                if isinstance(command, HumanStartCommand)
                else command.case_ref,
            )
            result = await self.client.execute(
                query, current=lambda: read.guard(command, self.clock(), read=True)
            )
            read.guard(command, self.clock(), read=True)
            if not isinstance(result, NativeReceiptLookup):
                raise AuthUnavailableError()
            if result.receipt is not None:
                bind_receipt(result.receipt, command)
                await self.store.reconcile(command, result.receipt)
                read.guard(command, self.clock(), read=True)
                return result.receipt
            # Authenticated absence never proves old packet cannot arrive. Native
            # uniqueness is the fence; replay below retains identical command bytes.
        effect = await self.authority.current(command, read=False)

        def current() -> None:
            effect.guard(command, self.clock())
            if self.clock() >= claim.lease_until:
                raise AuthUnavailableError()

        current()
        claim = await self.store.mark_sending(claim, current)
        current()
        receipt = await self.client.execute(command, current=current)
        if not isinstance(receipt, NativeEffectReceipt):
            raise AuthUnavailableError()
        bind_receipt(receipt, command)
        # Reconcile verified historical fact even if execution authority expires
        # after native commit. Disclosure below requires independently current read.
        await self.store.reconcile(command, receipt)
        read = await self.authority.current(command, read=True)
        read.guard(command, self.clock(), read=True)
        return receipt


@dataclass(frozen=True, slots=True, repr=False)
class HumanIntakeProvenance:
    kind: Literal["human_intake"]
    scope: Scope
    actor: Actor = field(repr=False)
    intake_ref: str
    command_id: str
    admission: AuditIntent
    guide_identity_ref: str
    definition: Definition
    input_pins: tuple[Pin, ...]
    start_facts_ref: str
    start_facts_digest: str
    projected_variables_digest: str

    def command(self, workload_ref: str) -> HumanStartCommand:
        if self.kind != "human_intake":
            raise AuthUnavailableError()
        return HumanStartCommand(
            schema="human-auth-start.v1",
            scope=self.scope,
            workload_ref=workload_ref,
            actor=self.actor,
            intake_ref=self.intake_ref,
            command_id=self.command_id,
            admission=self.admission,
            guide_identity_ref=self.guide_identity_ref,
            definition=self.definition,
            input_pins=self.input_pins,
            start_facts_ref=self.start_facts_ref,
            start_facts_digest=self.start_facts_digest,
            projected_variables_digest=self.projected_variables_digest,
        )


@dataclass(frozen=True, slots=True, repr=False)
class AuthorizedHumanStart:
    command: HumanStartCommand = field(repr=False)
    transport_identity: object = field(repr=False)


class HumanIntakeStartTransport:
    """Concrete dedicated capability; no structural fallback to an agent transport."""

    def __init__(
        self, *, dispatcher: AuthDispatcher, workload_ref: str, projection: Callable[[dict[str, Any]], str]
    ) -> None:
        self.dispatcher, self.workload_ref, self._projection = dispatcher, workload_ref, projection
        self._identity = object()

    async def authorize_human_start(self, provenance: HumanIntakeProvenance) -> AuthorizedHumanStart:
        if type(provenance) is not HumanIntakeProvenance:
            raise AuthUnavailableError()
        command = provenance.command(self.workload_ref)
        await self.dispatcher.store.prove(command)
        return AuthorizedHumanStart(command, self._identity)

    async def execute_human_start(self, authorized: AuthorizedHumanStart) -> NativeEffectReceipt:
        if (
            type(authorized) is not AuthorizedHumanStart
            or authorized.transport_identity is not self._identity
        ):
            raise AuthUnavailableError()
        return await self.dispatcher._dispatch(authorized.command)

    async def query_human_start(self, query: HumanReceiptQuery) -> NativeReceiptLookup:
        # Native revalidates the current independent read grant; callers must not
        # use this lower-level result as a browser disclosure without BFF finalization.
        if query.operation != "auth.start" or query.workload_ref != self.workload_ref:
            raise AuthUnavailableError()
        result = await self.dispatcher.client.execute(query)
        if not isinstance(result, NativeReceiptLookup):
            raise AuthUnavailableError()
        return result

    def match_projection(self, variables: dict[str, Any], expected: str) -> None:
        # Exact reviewed native typed monetary projection is a mandatory provider;
        # no local float conversion or inferred source value is supplied here.
        if self._projection(variables) != expected:
            raise AuthUnavailableError()


async def start_human(
    transport: object,
    *,
    process_key: str,
    business_key: str,
    variables: dict[str, Any],
    provenance: HumanIntakeProvenance,
) -> ProcessInstance:
    from maezo.tools.mcp_cibseven.transport import ProcessInstance, StartOutcome

    if (
        type(transport) is not HumanIntakeStartTransport
        or process_key != "SP-OP-AUTH-001"
        or business_key != "AUTHI-" + provenance.guide_identity_ref
    ):
        raise AuthUnavailableError()
    transport.match_projection(variables, provenance.projected_variables_digest)
    authorized = await transport.authorize_human_start(provenance)
    receipt = await transport.execute_human_start(authorized)
    return ProcessInstance(
        instance_id=receipt.process_instance_id,
        process_key=process_key,
        business_key=business_key,
        state="UNKNOWN",
        already_existed=receipt.outcome == "existing",
        start_outcome=StartOutcome.UNREPORTED if receipt.outcome == "existing" else StartOutcome.STARTED,
    )

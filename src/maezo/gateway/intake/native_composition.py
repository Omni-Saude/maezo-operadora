"""Explicit E04 BFF/dispatcher composition. No default source, key or native success.

The returned intake_factory is passed to create_portal_app's existing intake
factory seam. Owner-qualified connections, installation, source publication and
PHI providers are prerequisites; constructing these objects is not activation.
"""

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncEngine

from maezo.gateway.human.auth_profile import (
    HumanDocumentCommand,
    HumanStartCommand,
    NativeEffectReceipt,
    StartFacts,
)
from maezo.gateway.human.auth_projection import projection_digest, start_variables
from maezo.gateway.human.auth_transport import AuthNativeClient, AuthUnavailableError
from maezo.gateway.human.read_profile import digest
from maezo.portal.api.session import HumanSessionResolver
from maezo.tools.mcp_cibseven.transport import ProcessInstance, start_process_idempotent

from .models import IntakeAuthority
from .native_dispatch import (
    AuthDispatcher,
    AuthEffectAuthorizationSource,
    HumanIntakeProvenance,
    HumanIntakeStartTransport,
)
from .native_source_lifecycle import AuthCallerBinding
from .native_store import PostgresAuthDispatchStore
from .postgres import PostgresIntakeStore
from .service import IntakeService


@dataclass(frozen=True, slots=True, repr=False)
class AuthIntakeComponents:
    admission: PostgresIntakeStore
    dispatch_store: PostgresAuthDispatchStore
    dispatcher: AuthDispatcher
    start_transport: HumanIntakeStartTransport
    authority: IntakeAuthority

    def intake_factory(self, resolver: HumanSessionResolver) -> IntakeService:
        if resolver.settings.tenant != self.admission.tenant:
            raise ValueError("invalid AUTH tenant composition")
        return IntakeService(resolver, self.authority, self.admission)

    async def dispatch_prepared_start(
        self, command: HumanStartCommand, facts: StartFacts, *, caller: AuthCallerBinding | None = None
    ) -> ProcessInstance:
        if digest(facts) != command.start_facts_digest:
            raise AuthUnavailableError()
        variables = start_variables(command.scope.tenant, facts)
        if projection_digest(variables) != command.projected_variables_digest:
            raise AuthUnavailableError()
        await self.dispatch_store.prepare(command)
        provenance = HumanIntakeProvenance(
            kind="human_intake",
            scope=command.scope,
            actor=command.actor,
            intake_ref=command.intake_ref,
            command_id=command.command_id,
            admission=command.admission,
            guide_identity_ref=command.guide_identity_ref,
            definition=command.definition,
            input_pins=command.input_pins,
            start_facts_ref=command.start_facts_ref,
            start_facts_digest=command.start_facts_digest,
            projected_variables_digest=command.projected_variables_digest,
        )
        return await start_process_idempotent(
            HumanIntakeStartTransport(
                dispatcher=self.dispatcher,
                workload_ref=command.workload_ref,
                projection=projection_digest,
                caller=caller,
            ),
            process_key="SP-OP-AUTH-001",
            business_key="AUTHI-" + command.guide_identity_ref,
            variables=variables,
            audit_sink=self.dispatch_store,
            provenance=provenance,
        )

    async def dispatch_prepared_documents(
        self, command: HumanDocumentCommand, *, caller: AuthCallerBinding | None = None
    ) -> NativeEffectReceipt:
        await self.dispatch_store.prepare(command)
        return await self.dispatcher.dispatch_documents(command, caller=caller)


def compose_auth_intake(
    *,
    engine: AsyncEngine,
    tenant: str,
    workload_ref: str,
    intake_key_id: str,
    intake_key: bytes,
    native_key_id: str,
    native_key: bytes,
    authority: IntakeAuthority,
    native_authority: AuthEffectAuthorizationSource,
    native_client: AuthNativeClient,
) -> AuthIntakeComponents:
    dispatch_store = PostgresAuthDispatchStore(engine, tenant=tenant, key_id=native_key_id, key=native_key)
    admission = PostgresIntakeStore(
        engine, tenant=tenant, key_id=intake_key_id, encryption_key=intake_key, native_dispatch=dispatch_store
    )
    dispatcher = AuthDispatcher(store=dispatch_store, client=native_client, authority=native_authority)
    start_transport = HumanIntakeStartTransport(
        dispatcher=dispatcher, workload_ref=workload_ref, projection=projection_digest
    )
    return AuthIntakeComponents(admission, dispatch_store, dispatcher, start_transport, authority)

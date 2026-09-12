"""Strict native AUTH dispatch and receipt-first recovery at the audited start seam."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
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
from maezo.gateway.human.auth_transport import (
    AuthEffectCeiling,
    AuthNativeClient,
    AuthUnavailableError,
    bind_receipt,
)
from maezo.gateway.human.read_profile import digest, parse_model, wire
from maezo.tools.process_business_keys import (
    AUTH_BUSINESS_KEY_PREFIX,
    refuse_legacy_auth_intake_business_key,
)

from .native_source_lifecycle import AuthCallerBinding
from .native_store import PostgresAuthDispatchStore


class AuthIntakeGuideNumberUnavailableError(AuthUnavailableError):
    """A fonte `guide` publicada nao traz `numero_guia_tiss`, entao o portal nao pode montar a
    business key CONTRATUAL — e recusa iniciar (WP-J1-11, decisao do dono #16, opcao (a)).

    POR QUE RECUSAR E NAO CAIR NA CHAVE ANTIGA. O que o despachante tinha a mao era
    `guide_identity_ref`, uma referencia OPACA do portal, e era dela que saia a chave
    `AUTHI-{guide_identity_ref}`. Essa chave e' bem-formada e o engine a aceita — por isso o
    defeito sobreviveu: ela abre uma instancia PERFEITAMENTE FUNCIONAL num SEGUNDO dominio de
    idempotencia, invisivel ao canal de agente. Uma "migracao" seria inventar o numero da guia
    TISS a partir de um opaco que nao o determina. Recusar alto e' a unica resposta que nao
    fabrica identidade clinica, e deixa o caminho do portal INERTE exatamente como ja' esta'
    (`dispatch_prepared_start` nao tem chamador em `src/`).

    O QUE FECHA ISSO (nao e' deste pacote): a fonte `guide` precisa publicar `numero_guia_tiss`
    — um campo novo no DTO fechado `GuideIdentity`/`StartFacts`, com o dono da fonte, a analise
    de minimizacao do numero na fronteira do portal e o publicador correspondente (WP-J1-02) —
    e so' entao WP-J1-01 liga o daemon. `_published_guide_number` ja' le o campo: no dia em que
    ele existir, esta recusa deixa de ocorrer sem mais nenhuma edicao aqui.
    """

    def __init__(self) -> None:
        RuntimeError.__init__(self, "human_auth_guide_number_unavailable")


def assert_auth_business_key_of_tenant(business_key: object, *, tenant_id: str) -> None:
    """Recusa qualquer business key de AUTH que nao seja `AUTH-{tenant_id}-{guia nao vazia}`.

    Estrutural de proposito: este ponto NAO conhece o `numero_guia_tiss` (quem o conhece e'
    `dispatch_prepared_start`, que o le da fonte publicada), entao ele prova o que pode provar
    sem duplicar a fonte — prefixo contratual, tenant DESTA proveniencia e sufixo de guia nao
    vazio. Sem isso a guarda aceitaria uma chave de outro tenant vinda de um chamador errado.
    """
    prefix = f"{AUTH_BUSINESS_KEY_PREFIX}{tenant_id}-"
    if type(business_key) is not str or not business_key.startswith(prefix) or business_key == prefix:
        raise AuthUnavailableError()


@dataclass(frozen=True, slots=True, repr=False)
class AuthEffectLease:
    scope: Scope
    actor: Actor = field(repr=False)
    command_digest: str
    action: Literal["auth.start", "auth.documents.respond", "auth.receipt.read"]
    resource_ref: str
    valid_until: datetime
    live: Callable[[], None] = field(repr=False)
    revalidate: Callable[[], Awaitable[None]] = field(repr=False)
    effect_ceiling: AuthEffectCeiling | None = field(repr=False)

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
    async def current(
        self, command: EffectCommand, *, read: bool, caller: AuthCallerBinding | None = None
    ) -> AuthEffectLease:
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

    async def dispatch_documents(
        self, command: HumanDocumentCommand, *, caller: AuthCallerBinding | None = None
    ) -> NativeEffectReceipt:
        if type(command) is not HumanDocumentCommand:
            raise AuthUnavailableError()
        return await self._dispatch(command, caller=caller)

    async def _dispatch(
        self, command: EffectCommand, *, caller: AuthCallerBinding | None = None
    ) -> NativeEffectReceipt:
        command = parse_model(type(command), wire(command))
        await self.store.prove(command)
        completed = await self.store.completed(command)
        if completed is not None:
            read = await self.authority.current(command, read=True, caller=caller)
            read.guard(command, self.clock(), read=True)
            return completed
        claim = await self.store.claim(command.command_id)
        if claim.command != command:
            raise AuthUnavailableError()
        if claim.reconcile_first:
            read = await self.authority.current(command, read=True, caller=caller)
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
                query,
                current=lambda: read.guard(command, self.clock(), read=True),
                checkpoint=read.revalidate,
            )
            read.guard(command, self.clock(), read=True)
            if not isinstance(result, NativeReceiptLookup):
                raise AuthUnavailableError()
            if result.receipt is not None:
                bind_receipt(result.receipt, command)
                await self.store.reconcile(command, result.receipt)
                await read.revalidate()
                read.guard(command, self.clock(), read=True)
                return result.receipt
            # Authenticated absence never proves old packet cannot arrive. Native
            # uniqueness is the fence; replay below retains identical command bytes.
        effect = await self.authority.current(command, read=False, caller=caller)

        def current() -> None:
            effect.guard(command, self.clock())
            if self.clock() >= claim.lease_until:
                raise AuthUnavailableError()

        current()
        claim = await self.store.mark_sending(claim, current, checkpoint=effect.revalidate)
        current()
        receipt = await self.client.execute(
            command, current=current, checkpoint=effect.revalidate, effect_ceiling=effect.effect_ceiling
        )
        if not isinstance(receipt, NativeEffectReceipt):
            raise AuthUnavailableError()
        bind_receipt(receipt, command)
        # Reconcile verified historical fact even if execution authority expires
        # after native commit. Disclosure below requires independently current read.
        await self.store.reconcile(command, receipt)
        read = await self.authority.current(command, read=True, caller=caller)
        await read.revalidate()
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
        self,
        *,
        dispatcher: AuthDispatcher,
        workload_ref: str,
        projection: Callable[[dict[str, Any]], str],
        caller: AuthCallerBinding | None = None,
    ) -> None:
        self.dispatcher, self.workload_ref, self._projection = dispatcher, workload_ref, projection
        self._identity = object()
        self._caller = caller

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
        return await self.dispatcher._dispatch(authorized.command, caller=self._caller)

    async def query_human_start(self, query: HumanReceiptQuery) -> NativeReceiptLookup:
        # Native revalidates the current independent read grant; callers must not
        # use this lower-level result as a browser disclosure without BFF finalization.
        if query.operation != "auth.start" or query.workload_ref != self.workload_ref:
            raise AuthUnavailableError()
        command = await self.dispatcher.store.prepared_command(query.command_id)
        if digest(command) != query.expected_command_digest or command.scope != query.scope:
            raise AuthUnavailableError()
        read = await self.dispatcher.authority.current(command, read=True, caller=self._caller)
        if read.actor != query.actor:
            raise AuthUnavailableError()
        result = await self.dispatcher.client.execute(
            query,
            current=lambda: read.guard(command, self.dispatcher.clock(), read=True),
            checkpoint=read.revalidate,
        )
        await read.revalidate()
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

    if type(transport) is not HumanIntakeStartTransport or process_key != "SP-OP-AUTH-001":
        raise AuthUnavailableError()
    # WP-J1-11 (decisao do dono #16, opcao (a)). Ate aqui esta guarda exigia
    # `business_key == "AUTHI-" + provenance.guide_identity_ref`, isto e', ela EXIGIA a chave do
    # segundo dominio de idempotencia — o canal do portal nunca podia coincidir com o canal de
    # agente, que usa a chave contratual `AUTH-{tenant_id}-{numero_guia_tiss}`. A guarda agora
    # exige a forma contratual, escopada no tenant DESTA proveniencia, e recusa a legada por
    # nome (nunca a converte: ver `refuse_legacy_auth_intake_business_key`).
    refuse_legacy_auth_intake_business_key(business_key)
    assert_auth_business_key_of_tenant(business_key, tenant_id=provenance.scope.tenant)
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

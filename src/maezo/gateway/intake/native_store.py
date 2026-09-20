"""Protected gateway AUTH delivery journal; no native effect under a gateway TX.

Trace: E04 native contract 2.4/4.3. SQL owner installs native_schema.sql explicitly.
Unknown commit acknowledgement never permits a fresh effect identity.
"""

from __future__ import annotations

import hashlib
import os
import secrets
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, cast

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from maezo.gateway.external_cases.models import ExternalCaseError
from maezo.gateway.external_cases.postgres import transaction
from maezo.gateway.human.auth_profile import (
    Actor,
    AuditIntentPayload,
    EffectCommand,
    HumanDocumentCommand,
    HumanStartCommand,
    NativeEffectReceipt,
    Scope,
    SessionBinding,
)
from maezo.gateway.human.auth_transport import AuthUnavailableError, bind_receipt
from maezo.gateway.human.read_profile import digest, parse_model, wire
from maezo.portal.contracts.models import HumanPrincipal
from maezo.portal.engine.profile import canonicalize, strict_loads

from .native_source_lifecycle import AuthCallerBinding, IdentitySourceReservation, ProtectedAdmissionIdentity


@dataclass(frozen=True, slots=True, repr=False)
class DispatchClaim:
    command: EffectCommand = field(repr=False)
    generation: int
    revision: int
    owner_token: str = field(repr=False)
    lease_until: datetime
    reconcile_first: bool


class PostgresAuthDispatchStore:
    def __init__(
        self,
        engine: AsyncEngine,
        *,
        tenant: str,
        key_id: str,
        key: bytes,
        seconds: float = 5,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        if not tenant or not key_id or len(key) != 32:
            raise AuthUnavailableError()
        self.engine, self.tenant, self.key_id, self.seconds, self.clock = (
            engine,
            tenant,
            key_id,
            seconds,
            clock,
        )
        self._cipher = AESGCM(key)
        self.source: Any = None
        self.scope: Scope | None = None
        self.qualifier: Callable[[AsyncConnection], Awaitable[None]] | None = None

    def bind_source(self, source: Any, scope: Scope) -> None:
        from .native_source_lifecycle import PostgresAuthSourceLifecycle

        if (
            type(source) is not PostgresAuthSourceLifecycle
            or source.protected is not self
            or scope not in source.binding.scopes
            or self.source is not None
        ):
            raise AuthUnavailableError()
        self.source, self.scope = source, scope

    async def qualify(self, connection: AsyncConnection) -> None:
        if self.source is not None:
            if self.qualifier is None:
                raise AuthUnavailableError()
            await self.qualifier(connection)

    def _identity_record(self, command_id: str, row: Any) -> ProtectedAdmissionIdentity | HumanPrincipal:
        value = self.unseal("identity", command_id, row["key_id"], row["nonce"], row["ciphertext"])
        if isinstance(value, dict) and value.get("schema") == "human-auth-admission-identity.v2":
            return parse_model(ProtectedAdmissionIdentity, value)
        return parse_model(HumanPrincipal, value)  # Historical read only; never active v2 publication.

    def identity_principal(
        self, command_id: str, key_id: str, nonce: bytes, ciphertext: bytes
    ) -> HumanPrincipal:
        """The human principal of a sealed admission identity, whichever schema sealed it.

        Current admissions seal a `human-auth-admission-identity.v2` (`ProtectedAdmissionIdentity`,
        `stage_intake`); historical rows sealed the bare `HumanPrincipal`. Readers that only need
        the actor (recovery) must not parse the v2 envelope as a principal — that refused every
        identity written by today's admissions (PR-A landing repair; proven by
        tests/unit/gateway/intake/recovery/test_recovery.py::
        test_real_admission_commit_lost_ack_is_recoverable_without_payload).
        """
        row = {"key_id": key_id, "nonce": nonce, "ciphertext": ciphertext}
        record = self._identity_record(command_id, row)
        return record.principal if isinstance(record, ProtectedAdmissionIdentity) else record

    async def original_identity(self, command_id: str) -> ProtectedAdmissionIdentity:
        async with transaction(self.engine, self.seconds) as c:
            await self.qualify(c)
            outbox = await self._lock(c, command_id)
            row = (
                (
                    await c.execute(
                        text(
                            "SELECT * FROM portal_intake.native_identity WHERE tenant=:tena"
                            "nt AND command_id=:command"
                        ),
                        {"tenant": self.tenant, "command": command_id},
                    )
                )
                .mappings()
                .one()
            )
            value = self._identity_record(command_id, row)
            if not isinstance(value, ProtectedAdmissionIdentity):
                raise AuthUnavailableError()
            r = value.reservation
            if (
                r.tenant,
                r.command_id,
                r.admission_ref,
                r.admitted_digest,
                r.principal_ref,
                r.operation,
                value.session_binding.authorization_until,
            ) != (
                self.tenant,
                command_id,
                outbox["admission_ref"],
                outbox["admitted_digest"],
                outbox["principal_ref"],
                outbox["operation"],
                outbox["authorization_until"],
            ):
                raise AuthUnavailableError()
        return value

    async def original_principal(self, command_id: str) -> HumanPrincipal:
        """Legacy identity is usable only for independently authorized receipt recovery."""
        async with transaction(self.engine, self.seconds) as c:
            await self.qualify(c)
            outbox = await self._lock(c, command_id)
            row = (
                (
                    await c.execute(
                        text(
                            "SELECT * FROM portal_intake.native_identity WHERE tenant=:tena"
                            "nt AND command_id=:command"
                        ),
                        {"tenant": self.tenant, "command": command_id},
                    )
                )
                .mappings()
                .one()
            )
            value = self._identity_record(command_id, row)
            principal = value.principal if isinstance(value, ProtectedAdmissionIdentity) else value
            if principal.tenant != self.tenant or principal.principal_ref != outbox["principal_ref"]:
                raise AuthUnavailableError()
        return principal

    async def prepared_command(self, command_id: str) -> EffectCommand:
        async with transaction(self.engine, self.seconds) as c:
            await self.qualify(c)
            return self._command(await self._lock(c, command_id))

    def admission_identity(
        self,
        principal: HumanPrincipal,
        reservation: IdentitySourceReservation,
        command_id: str,
        admission_ref: str,
        admitted_digest: str,
        valid_until: datetime,
    ) -> ProtectedAdmissionIdentity:
        if (
            reservation.tenant,
            reservation.command_id,
            reservation.admission_ref,
            reservation.principal_ref,
            reservation.admitted_digest,
            reservation.session_binding.session_ref,
            reservation.session_binding.authenticated_at,
        ) != (
            self.tenant,
            command_id,
            admission_ref,
            principal.principal_ref,
            admitted_digest,
            principal.session_ref,
            principal.authenticated_at,
        ) or valid_until > reservation.session_binding.authorization_until:
            raise AuthUnavailableError()
        binding = parse_model(
            SessionBinding,
            wire(reservation.session_binding.model_copy(update={"authorization_until": valid_until})),
        )
        return ProtectedAdmissionIdentity(
            schema="human-auth-admission-identity.v2",
            principal=principal,
            session_binding=binding,
            reservation=reservation,
        )

    def seal(self, kind: str, ref: str, value: Any) -> tuple[bytes, bytes]:
        nonce = os.urandom(12)
        return nonce, self._cipher.encrypt(
            nonce, canonicalize(wire(value)), canonicalize([self.tenant, kind, ref, self.key_id])
        )

    def unseal(self, kind: str, ref: str, key_id: str, nonce: bytes, ciphertext: bytes) -> Any:
        if key_id != self.key_id:
            raise AuthUnavailableError()
        return strict_loads(
            self._cipher.decrypt(nonce, ciphertext, canonicalize([self.tenant, kind, ref, key_id]))
        )

    async def _lock(self, c: AsyncConnection, command_id: str) -> Any:
        return (
            (
                await c.execute(
                    text(
                        "SELECT * FROM portal_intake.native_outbox WHERE tenant=:tenant AND "
                        "command_id=:command FOR UPDATE"
                    ),
                    dict(tenant=self.tenant, command=command_id),
                )
            )
            .mappings()
            .one()
        )

    def _command(self, row: Any) -> EffectCommand:
        value = self.unseal(
            "command", row["command_id"], row["key_id"], row["command_nonce"], row["command_ciphertext"]
        )
        cls = HumanStartCommand if row["operation"] == "auth.start" else HumanDocumentCommand
        command = parse_model(cls, value)
        if digest(command) != row["command_digest"]:
            raise AuthUnavailableError()
        return cast(EffectCommand, command)

    async def prepare(self, command: EffectCommand) -> None:
        """Bind once before any send, against committed admission and sealed identity."""
        command = parse_model(type(command), wire(command))
        if command.scope.tenant != self.tenant:
            raise AuthUnavailableError()
        nonce, ciphertext = self.seal("command", command.command_id, command)
        try:
            async with transaction(self.engine, self.seconds) as c:
                await self.qualify(c)
                row = await self._lock(c, command.command_id)
                identity = (
                    (
                        await c.execute(
                            text(
                                "SELECT * FROM portal_intake.native_identity WHERE tenant=:tenant "
                                "AND command_id=:command"
                            ),
                            dict(tenant=self.tenant, command=command.command_id),
                        )
                    )
                    .mappings()
                    .one()
                )
                identity_value = self._identity_record(command.command_id, identity)
                principal = (
                    identity_value.principal
                    if isinstance(identity_value, ProtectedAdmissionIdentity)
                    else identity_value
                )
                if not isinstance(identity_value, ProtectedAdmissionIdentity) and row["state"] != "executed":
                    raise AuthUnavailableError()
                actor = command.actor
                if (actor.principal_ref, actor.issuer, actor.subject, actor.membership_revision) != (
                    principal.principal_ref,
                    principal.issuer,
                    principal.subject,
                    principal.membership_revision,
                ) or principal.tenant != self.tenant:
                    raise ExternalCaseError("denied")
                ref = command.intake_ref if isinstance(command, HumanStartCommand) else command.case_ref
                operation = (
                    "auth.start" if isinstance(command, HumanStartCommand) else "auth.documents.respond"
                )
                if (
                    row["resource_ref"],
                    row["operation"],
                    row["principal_ref"],
                    row["admitted_digest"],
                    row["admission_ref"],
                ) != (
                    ref,
                    operation,
                    actor.principal_ref,
                    command.admission.admitted_digest,
                    command.admission.intent_ref,
                ):
                    raise ExternalCaseError("conflict")
                audit = (
                    await c.execute(
                        text(
                            "SELECT digest FROM portal_intake.native_audit WHERE tenant=:tenant AND "
                            "command_id=:command AND event_kind='intent'"
                        ),
                        dict(tenant=self.tenant, command=command.command_id),
                    )
                ).scalar_one()
                if audit != row["admitted_digest"]:
                    raise ExternalCaseError("conflict")
                if isinstance(command, HumanStartCommand):
                    guide = (
                        await c.execute(
                            text(
                                "SELECT guide_identity_ref FROM portal_intake.intake "
                                "WHERE tenant=:tenant AND intake_ref=:intake AND command_id=:command"
                            ),
                            dict(tenant=self.tenant, intake=command.intake_ref, command=command.command_id),
                        )
                    ).scalar_one()
                    if guide != command.guide_identity_ref:
                        raise ExternalCaseError("conflict")
                if isinstance(command, HumanDocumentCommand):
                    r = (
                        (
                            await c.execute(
                                text(
                                    "SELECT request_ref,request_revision FROM "
                                    "portal_intake.native_response WHERE tenant=:tenant AND "
                                    "command_id=:command"
                                ),
                                dict(tenant=self.tenant, command=command.command_id),
                            )
                        )
                        .mappings()
                        .one()
                    )
                    if (r["request_ref"], r["request_revision"]) != (
                        command.occurrence.request_ref,
                        command.occurrence.request_revision,
                    ):
                        raise ExternalCaseError("conflict")
                if row["command_digest"] is not None:
                    if row["command_digest"] != digest(command) or self._command(row) != command:
                        raise ExternalCaseError("conflict")
                elif row["state"] != "admitted":
                    raise ExternalCaseError("conflict")
                else:
                    await c.execute(
                        text(
                            "UPDATE portal_intake.native_outbox SET "
                            "command_digest=:digest,command_nonce=:nonce,command_ciphertext=:cipherte"
                            "xt,revision=revision+1 WHERE tenant=:tenant AND command_id=:command"
                        ),
                        dict(
                            tenant=self.tenant,
                            command=command.command_id,
                            digest=digest(command),
                            nonce=nonce,
                            ciphertext=ciphertext,
                        ),
                    )
        except Exception:
            raise AuthUnavailableError() from None

    async def claim(self, command_id: str, *, seconds: int = 15) -> DispatchClaim:
        if not 1 <= seconds <= 60:
            raise AuthUnavailableError()
        token = secrets.token_hex(32)
        result = None
        try:
            async with transaction(self.engine, self.seconds) as c:
                await self.qualify(c)
                row = await self._lock(c, command_id)
                now = self.clock()
                if (
                    row["state"] in ("executed", "rejected")
                    or row["command_digest"] is None
                    or (row["lease_until"] is not None and now < row["lease_until"])
                ):
                    raise ExternalCaseError("conflict")
                reconcile = row["state"] in ("sending", "reconciling")
                command = self._command(row)
                until = now + timedelta(seconds=seconds)
                await c.execute(
                    text(
                        "UPDATE portal_intake.native_outbox SET "
                        "generation=generation+1,revision=revision+1,owner_digest=:owner,lease_until="
                        ":until,state=:state WHERE tenant=:tenant AND command_id=:command AND revisio"
                        "n=:revision"
                    ),
                    dict(
                        tenant=self.tenant,
                        command=command_id,
                        revision=row["revision"],
                        owner=hashlib.sha256(token.encode()).hexdigest(),
                        until=until,
                        state="reconciling" if reconcile else "claimed",
                    ),
                )
                if isinstance(command, HumanStartCommand):
                    await c.execute(
                        text(
                            "UPDATE portal_intake.intake SET disposition=:disposition,revision=revision+1 "
                            "WHERE tenant=:tenant AND intake_ref=:intake AND disposition<>'started'"
                        ),
                        dict(
                            tenant=self.tenant,
                            intake=command.intake_ref,
                            disposition="reconciling" if reconcile else "dispatching",
                        ),
                    )
                result = DispatchClaim(
                    command, row["generation"] + 1, row["revision"] + 1, token, until, reconcile
                )
                if self.clock() >= until:
                    raise ExternalCaseError("denied")
            if self.clock() >= result.lease_until:
                raise AuthUnavailableError()
            return result
        except Exception:
            raise AuthUnavailableError() from None

    def _owned(self, row: Any, claim: DispatchClaim) -> None:
        if (row["generation"], row["revision"], row["owner_digest"], row["command_digest"]) != (
            claim.generation,
            claim.revision,
            hashlib.sha256(claim.owner_token.encode()).hexdigest(),
            digest(claim.command),
        ) or self.clock() >= min(claim.lease_until, row["lease_until"]):
            raise ExternalCaseError("conflict")

    async def mark_sending(
        self, claim: DispatchClaim, current: Callable[[], None], *, checkpoint: Callable[[], Awaitable[None]]
    ) -> DispatchClaim:
        try:
            await checkpoint()
            async with transaction(self.engine, self.seconds) as c:
                await self.qualify(c)
                row = await self._lock(c, claim.command.command_id)
                self._owned(row, claim)
                if self.clock() >= row["authorization_until"]:
                    raise ExternalCaseError("denied")
                if row["state"] not in ("claimed", "reconciling"):
                    raise ExternalCaseError("conflict")
                current()
                await checkpoint()
                await c.execute(
                    text(
                        "UPDATE portal_intake.native_outbox SET state='sending',revision=revision+1 "
                        "WHERE tenant=:tenant AND command_id=:command AND revision=:revision"
                    ),
                    dict(tenant=self.tenant, command=claim.command.command_id, revision=claim.revision),
                )
                current()
                await checkpoint()
                if self.clock() >= row["authorization_until"]:
                    raise ExternalCaseError("denied")
                self._owned(row, claim)
            await checkpoint()
            current()
            if self.clock() >= min(claim.lease_until, row["authorization_until"]):
                raise AuthUnavailableError()
            return DispatchClaim(
                claim.command,
                claim.generation,
                claim.revision + 1,
                claim.owner_token,
                claim.lease_until,
                True,
            )
        except Exception:
            raise AuthUnavailableError() from None

    async def reconcile(self, command: EffectCommand, receipt: NativeEffectReceipt) -> None:
        """Authentic caller-verified receipt plus immutable command; one result/audit/link TX."""
        bind_receipt(receipt, command)
        nonce, ciphertext = self.seal("receipt", command.command_id, receipt)
        try:
            async with transaction(self.engine, self.seconds) as c:
                await self.qualify(c)
                row = await self._lock(c, command.command_id)
                if self._command(row) != command:
                    raise ExternalCaseError("conflict")
                if row["state"] == "executed":
                    if row["receipt_digest"] != digest(receipt):
                        raise ExternalCaseError("conflict")
                    return
                if row["state"] not in ("sending", "reconciling"):
                    raise ExternalCaseError("conflict")
                values = dict(
                    tenant=self.tenant,
                    command=command.command_id,
                    digest=digest(receipt),
                    nonce=nonce,
                    ciphertext=ciphertext,
                    admission=row["admission_ref"],
                )
                await c.execute(
                    text(
                        "INSERT INTO "
                        "portal_intake.native_audit(tenant,command_id,event_kind,admission_ref,digest"
                        ") VALUES(:tenant,:command,'result',:admission,:digest)"
                    ),
                    values,
                )
                if isinstance(command, HumanStartCommand):
                    values.update(
                        case=receipt.case_ref,
                        intake=command.intake_ref,
                        receipt=receipt.receipt_ref,
                        instance=receipt.process_instance_id,
                        definition=receipt.definition.definition_id,
                    )
                    await c.execute(
                        text(
                            "INSERT INTO "
                            "portal_intake.native_case_binding(tenant,case_ref,intake_ref,command_id,"
                            "receipt_ref,receipt_digest,process_instance_id,definition_id) VALUES(:te"
                            "nant,:case,:intake,:command,:receipt,:digest,:instance,:definition)"
                        ),
                        values,
                    )
                    await c.execute(
                        text(
                            "UPDATE portal_intake.intake SET "
                            "disposition='started',case_ref=:case,start_receipt_ref=:receipt,revision"
                            "=revision+1 WHERE tenant=:tenant AND intake_ref=:intake AND command_id=:"
                            "command"
                        ),
                        values,
                    )
                await c.execute(
                    text(
                        "UPDATE portal_intake.native_outbox SET "
                        "state='executed',revision=revision+1,receipt_digest=:digest,receipt_nonce=:n"
                        "once,receipt_ciphertext=:ciphertext,owner_digest=NULL,lease_until=NULL WHERE"
                        " tenant=:tenant AND command_id=:command"
                    ),
                    values,
                )
        except Exception:
            raise AuthUnavailableError() from None

    async def prove(self, command: EffectCommand) -> None:
        """Read committed intent and exact immutable command; not an object-only audit sink."""
        try:
            async with transaction(self.engine, self.seconds) as c:
                await self.qualify(c)
                row = await self._lock(c, command.command_id)
                audit = (
                    await c.execute(
                        text(
                            "SELECT digest FROM portal_intake.native_audit WHERE tenant=:tenant AND "
                            "command_id=:command AND event_kind='intent'"
                        ),
                        dict(tenant=self.tenant, command=command.command_id),
                    )
                ).scalar_one()
                if self._command(row) != command or audit != command.admission.admitted_digest:
                    raise ExternalCaseError("conflict")
        except Exception:
            raise AuthUnavailableError() from None

    async def stage_intake(
        self,
        c: AsyncConnection,
        *,
        principal: HumanPrincipal,
        intake_ref: str,
        command_id: str,
        admitted_digest: str,
        valid_until: datetime,
        reservation: IdentitySourceReservation,
    ) -> None:
        """Called only within PostgresIntakeStore's admission transaction."""
        await self.qualify(c)
        values = dict(
            tenant=self.tenant,
            command=command_id,
            admission=intake_ref,
            resource=intake_ref,
            principal=principal.principal_ref,
            digest=admitted_digest,
            until=valid_until,
            key=self.key_id,
        )
        await c.execute(
            text(
                "INSERT INTO "
                "portal_intake.native_outbox(tenant,command_id,admission_ref,resource_ref,operation,p"
                "rincipal_ref,admitted_digest,key_id,authorization_until) "
                "VALUES(:tenant,:command,:admission,:resource,'auth.start',:principal,:digest,:key,:until)"
            ),
            values,
        )
        await c.execute(
            text(
                "INSERT INTO "
                "portal_intake.native_audit(tenant,command_id,event_kind,admission_ref,digest) "
                "VALUES(:tenant,:command,'intent',:admission,:digest)"
            ),
            values,
        )
        identity = self.admission_identity(
            principal, reservation, command_id, intake_ref, admitted_digest, valid_until
        )
        nonce, ciphertext = self.seal("identity", command_id, identity)
        await c.execute(
            text(
                "INSERT INTO "
                "portal_intake.native_identity(tenant,command_id,key_id,nonce,ciphertext) "
                "VALUES(:tenant,:command,:key,:nonce,:ciphertext)"
            ),
            dict(values, nonce=nonce, ciphertext=ciphertext),
        )

    async def admit_response(
        self,
        *,
        principal: HumanPrincipal,
        case_ref: str,
        request_ref: str,
        request: Any,
        current: Callable[[], None],
        valid_until: datetime,
        caller: AuthCallerBinding,
    ) -> Any:
        """Durable submitted subset only. Caller supplies current qualified admission
        authority/custody; no completeness boolean or native binding is invented.
        """
        from uuid import uuid4

        from maezo.portal.contracts.documents import DocumentResponse, DocumentResponseReceipt

        if type(request) is not DocumentResponse or principal.tenant != self.tenant:
            raise AuthUnavailableError()
        if (
            type(caller) is not AuthCallerBinding
            or caller.principal != principal
            or self.source is None
            or self.scope is None
        ):
            raise AuthUnavailableError()
        await caller.revalidate()
        valid_until = min(valid_until, caller.valid_until)
        public = request.model_dump(mode="json")
        admitted = digest(
            {
                "schema": "human-auth-document-admission.v1",
                "case_ref": case_ref,
                "request_ref": request_ref,
                "request": public,
            }
        )
        response_ref = uuid4().hex
        async with transaction(self.engine, self.seconds) as connection:
            previous = (
                (
                    await connection.execute(
                        text(
                            "SELECT admission_ref,principal_ref,admitted_digest FROM portal"
                            "_intake.native_outbox "
                            "WHERE tenant=:tenant AND command_id=:command"
                        ),
                        {"tenant": self.tenant, "command": request.command_id},
                    )
                )
                .mappings()
                .one_or_none()
            )
            if previous is not None:
                if (previous["principal_ref"], previous["admitted_digest"]) != (
                    principal.principal_ref,
                    admitted,
                ):
                    raise AuthUnavailableError()
                response_ref = previous["admission_ref"]
        reservation = await self.source.reserve(
            caller,
            scope=self.scope,
            command_id=request.command_id,
            admission_ref=response_ref,
            operation="auth.documents.respond",
            request_digest=digest(public),
            admitted_digest=admitted,
            valid_until=valid_until,
        )
        await self.source.verify_reservation(reservation)
        response_ref = reservation.admission_ref
        valid_until = min(valid_until, reservation.session_binding.authorization_until)
        values = dict(
            tenant=self.tenant,
            command=request.command_id,
            admission=response_ref,
            resource=case_ref,
            principal=principal.principal_ref,
            digest=admitted,
            until=valid_until,
            key=self.key_id,
            request=request_ref,
            revision=int(request.expected_revision),
        )
        nonce, ciphertext = self.seal("response", request.command_id, public)
        try:
            async with transaction(self.engine, self.seconds) as c:
                await self.qualify(c)
                await c.execute(
                    text("SELECT pg_advisory_xact_lock(hashtextextended(:key,0))"),
                    {"key": canonicalize([self.tenant, "response", request.command_id]).decode()},
                )
                prior = (
                    (
                        await c.execute(
                            text(
                                "SELECT * FROM portal_intake.native_outbox WHERE tenant=:tenant AND c"
                                "ommand_id=:command"
                            ),
                            values,
                        )
                    )
                    .mappings()
                    .one_or_none()
                )
                if prior is not None:
                    if (
                        prior["operation"],
                        prior["principal_ref"],
                        prior["resource_ref"],
                        prior["admitted_digest"],
                    ) != ("auth.documents.respond", principal.principal_ref, case_ref, admitted):
                        raise ExternalCaseError("conflict")
                    response_ref = prior["admission_ref"]
                else:
                    current()
                    await c.execute(
                        text(
                            "INSERT INTO portal_intake.native_outbox(tenant,command_id,admission_ref,"
                            "resource_ref,operation,principal_ref,admitted_digest,key_id,"
                            "authorization_until) "
                            "VALUES(:tenant,:command,:admission,:resource,'auth.documents.respond',"
                            ":principal,:digest,:key,:until)"
                        ),
                        values,
                    )
                    await c.execute(
                        text(
                            "INSERT INTO portal_intake.native_response(tenant,response_ref,command_id"
                            ",case_ref,request_ref,request_revision,principal_ref,admitted_digest,key"
                            "_id,nonce,ciphertext) VALUES(:tenant,:admission,:command,:resource,:requ"
                            "est,:revision,:principal,:digest,:key,:nonce,:ciphertext)"
                        ),
                        dict(values, nonce=nonce, ciphertext=ciphertext),
                    )
                    await c.execute(
                        text(
                            "INSERT INTO portal_intake.native_audit(tenant,command_id,event_kind,admi"
                            "ssion_ref,digest) VALUES(:tenant,:command,'intent',:admission,:digest)"
                        ),
                        values,
                    )
                    original = self.admission_identity(
                        principal, reservation, request.command_id, response_ref, admitted, valid_until
                    )
                    n, sealed = self.seal("identity", request.command_id, original)
                    await c.execute(
                        text(
                            "INSERT INTO portal_intake.native_identity(tenant,command_id,key_id,nonce"
                            ",ciphertext) VALUES(:tenant,:command,:key,:nonce,:ciphertext)"
                        ),
                        dict(values, nonce=n, ciphertext=sealed),
                    )
                current()
                if self.clock() >= valid_until:
                    raise ExternalCaseError("denied")
            current()
            if self.clock() >= valid_until:
                raise AuthUnavailableError()
            if prior is not None and prior["state"] == "executed":
                committed = parse_model(
                    NativeEffectReceipt,
                    self.unseal(
                        "receipt",
                        request.command_id,
                        prior["key_id"],
                        prior["receipt_nonce"],
                        prior["receipt_ciphertext"],
                    ),
                )
                if digest(committed) != prior["receipt_digest"]:
                    raise AuthUnavailableError()
                bind_receipt(committed, self._command(prior))
                result = DocumentResponseReceipt(
                    command_id=request.command_id,
                    request_ref=request_ref,
                    revision=request.expected_revision,
                    disposition="correlated",
                    correlation_receipt_ref=committed.receipt_ref,
                )
                current()
                if self.clock() >= valid_until:
                    raise AuthUnavailableError()
                return result
            # A committed response is not proof of a bound subscription/correlation.
            return DocumentResponseReceipt(
                command_id=request.command_id,
                request_ref=request_ref,
                revision=request.expected_revision,
                disposition="admitted",
            )
        except Exception:
            raise AuthUnavailableError() from None

    async def completed(self, command: EffectCommand) -> NativeEffectReceipt | None:
        try:
            async with transaction(self.engine, self.seconds) as c:
                await self.qualify(c)
                row = await self._lock(c, command.command_id)
                return self._receipt(row, command)
        except Exception:
            raise AuthUnavailableError() from None

    def _receipt(self, row: Any, command: EffectCommand) -> NativeEffectReceipt | None:
        """The durable receipt of a SEALED row, bound to the command presented.

        Expects the caller's transaction and row lock. Raises `ExternalCaseError("conflict")` when
        the sealed command is not the one presented, or when the unsealed receipt fails its own
        digest — never an interpretation of either.
        """
        if self._command(row) != command:
            raise ExternalCaseError("conflict")
        if row["state"] != "executed":
            return None
        result = parse_model(
            NativeEffectReceipt,
            self.unseal(
                "receipt",
                command.command_id,
                row["key_id"],
                row["receipt_nonce"],
                row["receipt_ciphertext"],
            ),
        )
        if row["receipt_digest"] != digest(result):
            raise ExternalCaseError("conflict")
        bind_receipt(result, command)
        return result

    async def settled(self, command: EffectCommand) -> NativeEffectReceipt | None:
        """Receipt-first answer for a row that may not carry a sealed command YET.

        The drain (`runtime/intake_dispatch/service.py`) consults the durable receipt BEFORE any
        send, and at that point a fresh `admitted` row has nothing sealed: `stage_intake` writes no
        command column, and the schema (`native_schema.sql`) ties `state='executed'` to
        `receipt_digest IS NOT NULL` while requiring every claimed/sent/executed state to be sealed
        — so an unsealed row CANNOT carry a receipt, and `None` here is the fact, not a fallback.
        The seal (`prepare`) happens only inside the send that follows this read, which is why
        `completed` — which must compare the sealed command — is unanswerable for exactly the rows
        the drain reads first. A SEALED row is answered exactly like `completed`: the sealed
        command must match the one presented and a receipt that fails to unseal or to meet its
        digest fails closed.
        """
        try:
            async with transaction(self.engine, self.seconds) as c:
                await self.qualify(c)
                row = await self._lock(c, command.command_id)
                if row["command_digest"] is None:
                    return None
                return self._receipt(row, command)
        except Exception:
            raise AuthUnavailableError() from None

    async def audit_intent(self, command_id: str, actor: Actor) -> AuditIntentPayload:
        """Concrete committed gateway source record; source freeze/ack remains external."""
        try:
            async with transaction(self.engine, self.seconds) as c:
                await self.qualify(c)
                row = await self._lock(c, command_id)
                identity = (
                    (
                        await c.execute(
                            text(
                                "SELECT * FROM portal_intake.native_identity "
                                "WHERE tenant=:tenant AND command_id=:command"
                            ),
                            dict(tenant=self.tenant, command=command_id),
                        )
                    )
                    .mappings()
                    .one()
                )
                identity_value = self._identity_record(command_id, identity)
                if not isinstance(identity_value, ProtectedAdmissionIdentity):
                    raise AuthUnavailableError()
                principal = identity_value.principal
                if identity_value.session_binding.authorization_until != row["authorization_until"]:
                    raise AuthUnavailableError()
                if (actor.principal_ref, actor.issuer, actor.subject, actor.membership_revision) != (
                    principal.principal_ref,
                    principal.issuer,
                    principal.subject,
                    principal.membership_revision,
                ):
                    raise ExternalCaseError("denied")
                audit = (
                    (
                        await c.execute(
                            text(
                                "SELECT digest,recorded_at FROM portal_intake.native_audit "
                                "WHERE tenant=:tenant AND command_id=:command AND event_kind='intent'"
                            ),
                            dict(tenant=self.tenant, command=command_id),
                        )
                    )
                    .mappings()
                    .one()
                )
                if audit["digest"] != row["admitted_digest"]:
                    raise ExternalCaseError("conflict")
                result = AuditIntentPayload(
                    intent_ref=row["admission_ref"],
                    intake_or_response_ref=row["admission_ref"],
                    command_id=command_id,
                    actor=actor,
                    admitted_digest=row["admitted_digest"],
                    operation=row["operation"],
                    state="committed",
                    admitted_at=audit["recorded_at"],
                    session_binding=identity_value.session_binding,
                )
            return result
        except Exception:
            raise AuthUnavailableError() from None

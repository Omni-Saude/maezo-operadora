"""AUTH-SL1 identity-source reservation and revoke-before-change participant.

The identity, protected admission and native databases remain separate. Owner
installation/credentials are mandatory inputs; this module provisions no grant.
"""

from __future__ import annotations

import secrets
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from pydantic import Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from maezo.gateway.human.auth_profile import (
    InputPublication, PublicationLookup, PublicationQuery, PublicationReceipt,
    Scope, SessionBinding,
)
from maezo.gateway.human.auth_transport import AuthNativeClient, AuthUnavailableError, bind_result
from maezo.gateway.human.read_profile import Closed, SourceProvenance, digest, parse_model, wire
from maezo.portal.api.records import MembershipRecord, SessionRecord
from maezo.portal.api.session import ResolvedHumanSession
from maezo.portal.contracts.models import HumanPrincipal, OpaqueRef, Sha256Digest
from maezo.portal.engine.profile import canonicalize, strict_loads


class RelationPin(Closed):
    schema_name: str = Field(pattern=r"^[a-z][a-z0-9_]{0,62}$")
    name: str = Field(pattern=r"^[a-z][a-z0-9_]{0,62}$")
    oid: int = Field(gt=0)
    owner: str = Field(pattern=r"^[a-z][a-z0-9_]{0,62}$")


class IdentitySourceBinding(Closed):
    tenant: OpaqueRef
    installation_ref: OpaqueRef
    revision: int = Field(gt=0)
    database_name: str
    database_oid: int = Field(gt=0)
    database_incarnation: OpaqueRef
    owner_role: str
    reader_role: str
    writer_role: str
    control_role: str
    receipt_role: str
    relations: tuple[RelationPin, ...]
    scopes: tuple[Scope, ...]
    publisher_ref: OpaqueRef
    source_ref: OpaqueRef
    valid_until: datetime
    control_seconds: int = Field(gt=0, le=300)


class IdentitySourceReservation(Closed):
    reservation_ref: OpaqueRef
    tenant: OpaqueRef
    command_id: OpaqueRef
    admission_ref: OpaqueRef
    principal_ref: OpaqueRef
    operation: Literal["auth.start", "auth.documents.respond"]
    scope: Scope
    request_digest: Sha256Digest
    admitted_digest: Sha256Digest
    session_binding: SessionBinding
    membership_source_revision: int = Field(gt=0)
    membership_record_digest: Sha256Digest
    source: SourceProvenance


class ProtectedAdmissionIdentity(Closed):
    schema_: Literal["human-auth-admission-identity.v2"] = Field(alias="schema")
    principal: HumanPrincipal = Field(repr=False)
    session_binding: SessionBinding
    reservation: IdentitySourceReservation = Field(repr=False)


@dataclass(frozen=True, slots=True, repr=False)
class AuthCallerBinding:
    """Resolver-minted in-process capability; no wire/credential serialization."""

    principal: HumanPrincipal
    original: ResolvedHumanSession = field(repr=False)
    valid_until: datetime
    _resolve: Callable[..., Any] = field(repr=False)
    _secret: str = field(repr=False)
    _issuer: object = field(repr=False)

    @classmethod
    async def resolve(cls, resolver: Any, secret: str, *, ceiling: datetime | None = None) -> AuthCallerBinding:
        from maezo.portal.api.session import HumanSessionResolver
        if type(resolver) is not HumanSessionResolver:
            raise AuthUnavailableError()
        session = await resolver.resolve(secret)
        until = min(session.record.expires_at, session.membership.reviewed_until)
        if ceiling is not None:
            until = min(until, ceiling)
        return cls(session.principal, session, until, resolver.resolve, secret, resolver)

    async def revalidate(self) -> ResolvedHumanSession:
        from maezo.portal.api.session import HumanSessionResolver
        if type(self._issuer) is not HumanSessionResolver or self._resolve != self._issuer.resolve:
            raise AuthUnavailableError()
        current = await self._resolve(self._secret)
        if current.principal != self.principal or current.record != self.original.record:
            raise AuthUnavailableError()
        if datetime.now(UTC) >= min(self.valid_until, current.record.expires_at, current.membership.reviewed_until):
            raise AuthUnavailableError()
        return current


class PostgresAuthSourceLifecycle:
    """Concrete role-bound participant, including every identity session writer."""

    def __init__(
        self, *, binding: IdentitySourceBinding, reader: AsyncEngine, writer: AsyncEngine,
        control: AsyncEngine, receipts: AsyncEngine, native: AuthNativeClient,
        publication_journal: Any, protected_store: Any,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        from maezo.gateway.human.auth_publisher import PostgresAuthPublicationJournal
        from .native_store import PostgresAuthDispatchStore
        if (type(publication_journal) is not PostgresAuthPublicationJournal
            or type(protected_store) is not PostgresAuthDispatchStore
            or publication_journal.store is not protected_store
            or protected_store.tenant != binding.tenant
            or len({binding.owner_role,binding.reader_role,binding.writer_role,binding.control_role,binding.receipt_role}) != 5
            or any(engine.dialect.name != "postgresql" for engine in (reader,writer,control,receipts))):
            raise AuthUnavailableError()
        self.binding, self.reader, self.writer, self.control, self.receipts = binding, reader, writer, control, receipts
        self.native, self.journal, self.protected, self.clock = native, publication_journal, protected_store, clock
        required={("public","portal_sessions"),("public","portal_memberships")}|{("portal_auth",n) for n in ("installation","source_head","reservation","issuance","source_change")}
        if {(p.schema_name,p.name) for p in binding.relations} != required or len(binding.relations)!=len(required) or any(p.owner!=binding.owner_role for p in binding.relations) or not binding.scopes:
            raise AuthUnavailableError()

    async def qualified(self, db: AsyncConnection, role: str) -> None:
        b = self.binding
        identity = (await db.execute(text("SELECT current_user AS role,session_user AS session_role,current_database() AS name,(SELECT oid FROM pg_database WHERE datname=current_database()) AS oid"))).mappings().one()
        if (identity["role"],identity["session_role"],identity["name"],identity["oid"]) != (role,role,b.database_name,b.database_oid):
            raise AuthUnavailableError()
        installed = (await db.execute(text("SELECT * FROM portal_auth.installation WHERE tenant=:tenant"),{"tenant":b.tenant})).mappings().one()
        if (installed["installation_ref"],installed["revision"],installed["binding_digest"],installed["binding"]) != (b.installation_ref,b.revision,digest(b),wire(b)) or self.clock() >= min(b.valid_until,installed["valid_until"]):
            raise AuthUnavailableError()
        for pin in b.relations:
            row = (await db.execute(text("SELECT c.oid,c.relrowsecurity,c.relforcerowsecurity,c.relkind,pg_get_userbyid(c.relowner) AS owner,has_table_privilege(current_user,c.oid,'SELECT') AS readable,has_table_privilege(current_user,c.oid,'TRUNCATE') AS truncate FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname=:schema AND c.relname=:name"),{"schema":pin.schema_name,"name":pin.name})).mappings().one()
            if (row["oid"],row["owner"],row["relkind"],row["relrowsecurity"],row["relforcerowsecurity"],row["readable"],row["truncate"]) != (pin.oid,pin.owner,"r",False,False,True,False):
                raise AuthUnavailableError()
            if role in (b.reader_role,b.writer_role):
                writable=(await db.execute(text("SELECT has_table_privilege(current_user,:oid,'INSERT,UPDATE,DELETE,REFERENCES,TRIGGER') OR has_any_column_privilege(current_user,:oid,'INSERT,UPDATE,REFERENCES')"),{"oid":pin.oid})).scalar_one()
                if writable: raise AuthUnavailableError()
        if role==b.control_role:
            forgery=(await db.execute(text("SELECT has_column_privilege(current_user,'portal_auth.issuance',c, 'INSERT') OR has_column_privilege(current_user,'portal_auth.issuance',c,'UPDATE') FROM unnest(ARRAY['receipt','receipt_digest','committed_generation']) AS c"))).scalars().all()
            if any(forgery): raise AuthUnavailableError()
        if role==b.receipt_role:
            for name in ('source_head','source_change','reservation','installation'):
                if (await db.execute(text("SELECT has_table_privilege(current_user,:name,'INSERT,UPDATE,DELETE') OR has_any_column_privilege(current_user,:name,'INSERT,UPDATE')"),{"name":"portal_auth."+name})).scalar_one(): raise AuthUnavailableError()
        bypass = (await db.execute(text("SELECT rolsuper,rolbypassrls,rolreplication FROM pg_roles WHERE rolname=current_user"))).mappings().one()
        if any(bypass.values()):
            raise AuthUnavailableError()
        triggers = (await db.execute(text("SELECT tgname,tgenabled FROM pg_trigger WHERE tgname IN ('portal_auth_session_write','portal_auth_session_truncate','portal_auth_membership_write','portal_auth_membership_truncate') AND NOT tgisinternal"))).all()
        if len(triggers) != 4 or any(row[1] != "A" for row in triggers):
            raise AuthUnavailableError()

    def record_digest(self, category: str, record: SessionRecord | MembershipRecord) -> str:
        return digest({"installation":self.binding.installation_ref,"database_incarnation":self.binding.database_incarnation,"category":category,"record":wire(record)})

    async def _heads(self, db: AsyncConnection, session_ref: str, principal_ref: str) -> tuple[Any,Any]:
        rows = (await db.execute(text("SELECT * FROM portal_auth.source_head WHERE tenant=:tenant AND ((category='session' AND identity_ref=:session) OR (category='membership' AND identity_ref=:principal)) ORDER BY category,identity_ref FOR UPDATE"),{"tenant":self.binding.tenant,"session":session_ref,"principal":principal_ref})).mappings().all()
        if len(rows) != 2 or any(row["state"] != "active" or row["pending_change"] is not None for row in rows):
            raise AuthUnavailableError()
        return next(row for row in rows if row["category"]=="session"),next(row for row in rows if row["category"]=="membership")

    async def _observe(self, db: AsyncConnection, principal: HumanPrincipal) -> tuple[SessionRecord,MembershipRecord,Any,Any]:
        session_head, member_head = await self._heads(db,principal.session_ref,principal.principal_ref)
        row = (await db.execute(text("SELECT expires_at,payload FROM public.portal_sessions WHERE tenant=:tenant AND payload::jsonb->>'session_ref'=:session FOR SHARE"),{"tenant":self.binding.tenant,"session":principal.session_ref})).mappings().one()
        session = SessionRecord.model_validate_json(row["payload"])
        payload = (await db.execute(text("SELECT payload FROM public.portal_memberships WHERE tenant=:tenant AND issuer=:issuer AND subject=:subject FOR SHARE"),{"tenant":self.binding.tenant,"issuer":principal.issuer,"subject":principal.subject})).scalar_one()
        membership = MembershipRecord.model_validate_json(payload)
        if (session.session_ref,session.authenticated_at,session.principal_ref,session.issuer,session.subject,session.membership_revision) != (principal.session_ref,principal.authenticated_at,principal.principal_ref,principal.issuer,principal.subject,principal.membership_revision) or row["expires_at"] != session.expires_at:
            raise AuthUnavailableError()
        if (membership.principal_ref,membership.revision,membership.audience,membership.memberships,membership.subject_bindings,membership.revoked) != (principal.principal_ref,principal.membership_revision,principal.audience,principal.memberships,principal.subject_bindings,False):
            raise AuthUnavailableError()
        if self.clock() >= min(session.expires_at,membership.reviewed_until) or self.record_digest("session",session) != session_head["record_digest"] or self.record_digest("membership",membership) != member_head["record_digest"]:
            raise AuthUnavailableError()
        return session,membership,session_head,member_head

    async def reserve(self, caller: AuthCallerBinding, *, scope: Scope, command_id: str, admission_ref: str, operation: str, request_digest: str, admitted_digest: str, valid_until: datetime) -> IdentitySourceReservation:
        await caller.revalidate()
        if scope not in self.binding.scopes or scope.tenant != caller.principal.tenant:
            raise AuthUnavailableError()
        async with self.control.begin() as db:
            await self.qualified(db,self.binding.control_role)
            session,membership,sh,mh = await self._observe(db,caller.principal)
            prior = (await db.execute(text("SELECT * FROM portal_auth.reservation WHERE tenant=:tenant AND command_id=:command FOR UPDATE"),{"tenant":scope.tenant,"command":command_id})).mappings().one_or_none()
            if prior is not None:
                if (prior["principal_ref"],prior["operation"],prior["request_digest"],prior["admitted_digest"],prior["scope"],prior["session_ref"]) != (caller.principal.principal_ref,operation,request_digest,admitted_digest,wire(scope),caller.principal.session_ref):
                    raise AuthUnavailableError()
                ref=prior["reservation_ref"]
            else:
                now=self.clock();until=min(valid_until,caller.valid_until,session.expires_at,membership.reviewed_until,self.binding.valid_until)
                if now >= until: raise AuthUnavailableError()
                ref=secrets.token_urlsafe(24)
                binding=SessionBinding(session_ref=session.session_ref,authenticated_at=session.authenticated_at,session_expires_at=session.expires_at,authorization_until=until,session_source_revision=sh["revision"],session_record_digest=sh["record_digest"])
                record=dict(reservation_ref=ref,tenant=scope.tenant,command_id=command_id,admission_ref=admission_ref,principal_ref=caller.principal.principal_ref,operation=operation,scope=wire(scope),request_digest=request_digest,admitted_digest=admitted_digest,session_binding=wire(binding),membership_source_revision=mh["revision"],membership_record_digest=mh["record_digest"])
                source=SourceProvenance(publisher_ref=self.binding.publisher_ref,source_ref=self.binding.source_ref,source_revision=sh["revision"],source_digest=digest(record),receipt_ref=ref,observed_at=now,valid_until=until)
                await db.execute(text("INSERT INTO portal_auth.reservation(tenant,reservation_ref,command_id,operation,principal_ref,session_ref,admission_ref,admitted_digest,request_digest,scope,session_revision,membership_revision,session_digest,membership_digest,session_binding,reserved_at,authorization_until,source_receipt,state) VALUES(:tenant,:ref,:command,:operation,:principal,:session,:admission,:admitted,:request,CAST(:scope AS jsonb),:sr,:mr,:sd,:md,CAST(:binding AS jsonb),:now,:until,CAST(:source AS jsonb),'reserved')"),dict(tenant=scope.tenant,ref=ref,command=command_id,operation=operation,principal=caller.principal.principal_ref,session=session.session_ref,admission=admission_ref,admitted=admitted_digest,request=request_digest,scope=canonicalize(wire(scope)).decode(),sr=sh["revision"],mr=mh["revision"],sd=sh["record_digest"],md=mh["record_digest"],binding=canonicalize(wire(binding)).decode(),now=now,until=until,source=canonicalize(wire(source)).decode()))
        # A returned reservation always comes from a separate committed qualified readback.
        return await self.reservation(ref)

    async def reservation(self, ref: str) -> IdentitySourceReservation:
        async with self.reader.connect() as db:
            await self.qualified(db,self.binding.reader_role)
            row=(await db.execute(text("SELECT * FROM portal_auth.reservation WHERE tenant=:tenant AND reservation_ref=:ref"),{"tenant":self.binding.tenant,"ref":ref})).mappings().one()
            result=IdentitySourceReservation(reservation_ref=row["reservation_ref"],tenant=row["tenant"],command_id=row["command_id"],admission_ref=row["admission_ref"],principal_ref=row["principal_ref"],operation=row["operation"],scope=parse_model(Scope,row["scope"]),request_digest=row["request_digest"],admitted_digest=row["admitted_digest"],session_binding=parse_model(SessionBinding,row["session_binding"]),membership_source_revision=row["membership_revision"],membership_record_digest=row["membership_digest"],source=parse_model(SourceProvenance,row["source_receipt"]))
        return result

    async def verify_reservation(self, reservation: IdentitySourceReservation) -> None:
        if await self.reservation(reservation.reservation_ref) != reservation:
            raise AuthUnavailableError()

    async def current_original(self, identity: ProtectedAdmissionIdentity) -> datetime:
        await self.verify_reservation(identity.reservation)
        async with self.control.begin() as db:
            await self.qualified(db,self.binding.control_role)
            session,membership,sh,mh=await self._observe(db,identity.principal)
            row=(await db.execute(text("SELECT state FROM portal_auth.reservation WHERE tenant=:tenant AND reservation_ref=:ref FOR SHARE"),{"tenant":self.binding.tenant,"ref":identity.reservation.reservation_ref})).scalar_one()
            if row in ('issuance_disabled','frozen_ack','revoked_ack') or (sh["revision"],sh["record_digest"],mh["revision"],mh["record_digest"]) != (identity.session_binding.session_source_revision,identity.session_binding.session_record_digest,identity.reservation.membership_source_revision,identity.reservation.membership_record_digest):
                raise AuthUnavailableError()
            until=min(identity.session_binding.authorization_until,session.expires_at,membership.reviewed_until,self.binding.valid_until)
        if self.clock() >= until: raise AuthUnavailableError()
        return until

    async def _publication_current(self, publication: InputPublication) -> None:
        async with self.reader.connect() as db:
            await self.qualified(db,self.binding.reader_role)
            row=(await db.execute(text("SELECT request_digest,request FROM portal_auth.issuance WHERE tenant=:tenant AND publication_id=:id"),{"tenant":self.binding.tenant,"id":publication.publication_id})).mappings().one()
            if row["request_digest"] != digest(publication) or row["request"] != wire(publication):
                raise AuthUnavailableError()
        if self.clock() >= self.binding.valid_until:
            raise AuthUnavailableError()

    async def _resolve_publication(self, publication: InputPublication) -> PublicationReceipt:
        # Pending was committed before credential access. Recovery never infers
        # no-in-flight from an absent receipt and always keeps identical CAS bytes.
        await self._publication_current(publication)
        prior=await self.journal.freeze(publication)
        if prior is None:
            query=PublicationQuery(schema="human-auth-publication-query.v1",scope=publication.scope,workload_ref=publication.workload_ref,query_id=secrets.token_urlsafe(24),publication_id=publication.publication_id,expected_digest=digest(publication))
            lookup=await self.native.execute(query,checkpoint=lambda:self._publication_current(publication))
            if not isinstance(lookup,PublicationLookup): raise AuthUnavailableError()
            prior=lookup.receipt
        if prior is None:
            value=await self.native.execute(publication,checkpoint=lambda:self._publication_current(publication))
            if not isinstance(value,PublicationReceipt): raise AuthUnavailableError()
            prior=value
        bind_result(prior,publication,self.clock())
        await self.journal.acknowledge(publication,prior)
        async with self.receipts.begin() as db:
            await self.qualified(db,self.binding.receipt_role)
            row=(await db.execute(text("SELECT request_digest,receipt_digest FROM portal_auth.issuance WHERE tenant=:tenant AND publication_id=:id FOR UPDATE"),{"tenant":self.binding.tenant,"id":publication.publication_id})).mappings().one()
            if row["request_digest"] != digest(publication) or row["receipt_digest"] not in (None,digest(prior)):
                raise AuthUnavailableError()
            await db.execute(text("UPDATE portal_auth.issuance SET state='acknowledged',receipt=CAST(:receipt AS jsonb),receipt_digest=:digest,committed_generation=:generation WHERE tenant=:tenant AND publication_id=:id"),{"tenant":self.binding.tenant,"id":publication.publication_id,"receipt":canonicalize(wire(prior)).decode(),"digest":digest(prior),"generation":prior.head_generation})
        async with self.control.begin() as db:
            await self.qualified(db,self.binding.control_role)
            await db.execute(text("UPDATE portal_auth.reservation r SET state=:state,active_publication=:id FROM portal_auth.issuance i WHERE r.tenant=:tenant AND i.tenant=r.tenant AND i.reservation_ref=r.reservation_ref AND i.publication_id=:id AND i.state='acknowledged'"),{"tenant":self.binding.tenant,"id":publication.publication_id,"state":{"active":"active_ack","frozen":"frozen_ack","revoked":"revoked_ack"}[publication.state]})
        return prior

    async def publish_admission(self, identity: ProtectedAdmissionIdentity, *, workload_ref: str) -> InputPublication:
        """Join real committed source/admission records before permitting issuance."""
        from maezo.gateway.human.auth_profile import Actor
        original=await self.protected.original_identity(identity.reservation.command_id)
        if original != identity: raise AuthUnavailableError()
        await self.current_original(identity)
        actor=Actor(principal_ref=identity.principal.principal_ref,issuer=identity.principal.issuer,subject=identity.principal.subject,membership_revision=identity.principal.membership_revision,audience=identity.principal.audience)
        payload=await self.protected.audit_intent(identity.reservation.command_id,actor)
        if payload.session_binding != identity.session_binding: raise AuthUnavailableError()
        r=identity.reservation
        async with self.control.begin() as db:
            await self.qualified(db,self.binding.control_role)
            await self._observe(db,identity.principal)
            row=(await db.execute(text("SELECT * FROM portal_auth.reservation WHERE tenant=:tenant AND reservation_ref=:ref FOR UPDATE"),{"tenant":r.tenant,"ref":r.reservation_ref})).mappings().one()
            if row["state"] in ('issuance_disabled','frozen_ack','revoked_ack'): raise AuthUnavailableError()
            existing=(await db.execute(text("SELECT request FROM portal_auth.issuance WHERE tenant=:tenant AND reservation_ref=:ref AND target_state='active'"),{"tenant":r.tenant,"ref":r.reservation_ref})).scalar_one_or_none()
            if existing is not None:
                publication=parse_model(InputPublication,existing)
                if publication.payload != payload: raise AuthUnavailableError()
            else:
                now=self.clock();until=min(identity.session_binding.authorization_until,self.binding.valid_until)
                if now>=until: raise AuthUnavailableError()
                ref=secrets.token_urlsafe(24)
                source=SourceProvenance(publisher_ref=self.binding.publisher_ref,source_ref=self.binding.source_ref,source_revision=r.session_binding.session_source_revision,source_digest=digest({"reservation":wire(r),"admission":wire(payload)}),receipt_ref=ref,observed_at=now,valid_until=until)
                publication=InputPublication(schema="human-auth-input-publication.v1",scope=r.scope,workload_ref=workload_ref,publication_id=ref,kind="audit_intent",resource_ref=r.admission_ref,expected_generation=0,source=source,state="active",payload=payload,payload_digest=digest(payload),valid_until=until)
                await db.execute(text("UPDATE portal_auth.reservation SET state='protected_admission_confirmed',protected_digest=:digest WHERE tenant=:tenant AND reservation_ref=:ref"),{"tenant":r.tenant,"ref":r.reservation_ref,"digest":digest(identity)})
                await self._insert_issuance(db,r.reservation_ref,publication)
        await self._resolve_publication(publication)
        return publication

    async def _insert_issuance(self, db: AsyncConnection, reservation_ref: str, publication: InputPublication) -> None:
        await db.execute(text("INSERT INTO portal_auth.issuance(tenant,publication_id,reservation_ref,state,target_state,expected_generation,request_digest,request) VALUES(:tenant,:id,:ref,'pending',:state,:generation,:digest,CAST(:request AS jsonb))"),{"tenant":self.binding.tenant,"id":publication.publication_id,"ref":reservation_ref,"state":publication.state,"generation":publication.expected_generation,"digest":digest(publication),"request":canonicalize(wire(publication)).decode()})
        await db.execute(text("UPDATE portal_auth.reservation SET state='publication_pending' WHERE tenant=:tenant AND reservation_ref=:ref"),{"tenant":self.binding.tenant,"ref":reservation_ref})

    async def _freeze_dependency(self, ref: str, change_id: str) -> None:
        async with self.control.begin() as db:
            await self.qualified(db,self.binding.control_role)
            row=(await db.execute(text("SELECT * FROM portal_auth.reservation WHERE tenant=:tenant AND reservation_ref=:ref FOR UPDATE"),{"tenant":self.binding.tenant,"ref":ref})).mappings().one()
            if row["state"] in ('issuance_disabled','frozen_ack','revoked_ack'): return
            issued=(await db.execute(text("SELECT * FROM portal_auth.issuance WHERE tenant=:tenant AND reservation_ref=:ref ORDER BY expected_generation DESC"),{"tenant":self.binding.tenant,"ref":ref})).mappings().all()
            if not issued:
                if row["state"] not in ('reserved','protected_admission_confirmed'): raise AuthUnavailableError()
                await db.execute(text("UPDATE portal_auth.reservation SET state='issuance_disabled' WHERE tenant=:tenant AND reservation_ref=:ref"),{"tenant":self.binding.tenant,"ref":ref})
                return
            pending=[item for item in issued if item["state"]=='pending']
        for item in pending:
            await self._resolve_publication(parse_model(InputPublication,item["request"]))
        async with self.control.begin() as db:
            await self.qualified(db,self.binding.control_role)
            row=(await db.execute(text("SELECT * FROM portal_auth.reservation WHERE tenant=:tenant AND reservation_ref=:ref FOR UPDATE"),{"tenant":self.binding.tenant,"ref":ref})).mappings().one()
            if row["state"] in ('frozen_ack','revoked_ack'): return
            latest=(await db.execute(text("SELECT * FROM portal_auth.issuance WHERE tenant=:tenant AND reservation_ref=:ref ORDER BY expected_generation DESC LIMIT 1"),{"tenant":self.binding.tenant,"ref":ref})).mappings().one()
            if latest["state"]!='acknowledged': raise AuthUnavailableError()
            old=parse_model(InputPublication,latest["request"])
            change=(await db.execute(text("SELECT change_id,source_revision,request_digest,state FROM portal_auth.source_change WHERE tenant=:tenant AND change_id=:id FOR SHARE"),{"tenant":self.binding.tenant,"id":change_id})).mappings().one()
            if change["state"] not in ('freeze_pending','native_frozen'): raise AuthUnavailableError()
            now=self.clock();until=min(self.binding.valid_until,now+timedelta(seconds=self.binding.control_seconds))
            # This authenticates the committed control intent, never renews old session facts.
            source=SourceProvenance(publisher_ref=self.binding.publisher_ref,source_ref=self.binding.source_ref,source_revision=change["source_revision"],source_digest=digest(dict(change)),receipt_ref=change_id,observed_at=now,valid_until=until)
            publication=InputPublication(schema="human-auth-input-publication.v1",scope=old.scope,workload_ref=old.workload_ref,publication_id=secrets.token_urlsafe(24),kind="audit_intent",resource_ref=old.resource_ref,expected_generation=latest["committed_generation"],source=source,state="frozen",payload=None,payload_digest=None,valid_until=until)
            await self._insert_issuance(db,ref,publication)
        await self._resolve_publication(publication)

    async def _begin_change(self, category: str, identity_ref: str, old: SessionRecord | MembershipRecord | None, new: SessionRecord | MembershipRecord | None, operation: str) -> str:
        before=None if old is None else wire(old);after=None if new is None else wire(new)
        request_digest=digest({"category":category,"identity_ref":identity_ref,"old":before,"new":after,"operation":operation})
        async with self.control.begin() as db:
            await self.qualified(db,self.binding.control_role)
            head=(await db.execute(text("SELECT * FROM portal_auth.source_head WHERE tenant=:tenant AND category=:category AND identity_ref=:ref FOR UPDATE"),{"tenant":self.binding.tenant,"category":category,"ref":identity_ref})).mappings().one_or_none()
            if head is None:
                if old is not None: raise AuthUnavailableError() # No implicit legacy source backfill.
                await db.execute(text("INSERT INTO portal_auth.source_head(tenant,category,identity_ref,revision,record_digest,state) VALUES(:tenant,:category,:ref,1,:digest,'active')"),{"tenant":self.binding.tenant,"category":category,"ref":identity_ref,"digest":digest(None)})
                revision=1
            else:
                revision=head["revision"]
                if head["pending_change"] is not None:
                    pending=(await db.execute(text("SELECT change_id,request_digest FROM portal_auth.source_change WHERE tenant=:tenant AND change_id=:id"),{"tenant":self.binding.tenant,"id":head["pending_change"]})).mappings().one()
                    if pending["request_digest"] != request_digest: raise AuthUnavailableError()
                    return pending["change_id"]
                if head["state"] != 'active' or old is None or self.record_digest(category,old) != head["record_digest"]:
                    raise AuthUnavailableError()
            refs=(await db.execute(text("SELECT reservation_ref FROM portal_auth.reservation WHERE tenant=:tenant AND ((:category='session' AND session_ref=:ref) OR (:category='membership' AND principal_ref=:ref)) ORDER BY reservation_ref FOR UPDATE"),{"tenant":self.binding.tenant,"category":category,"ref":identity_ref})).scalars().all()
            change=secrets.token_urlsafe(24)
            if category=='session' and new is not None and new.session_ref!=identity_ref:
                await db.execute(text("INSERT INTO portal_auth.source_head(tenant,category,identity_ref,revision,record_digest,state,pending_change) VALUES(:tenant,'session',:ref,1,:digest,'frozen',:change)"),{"tenant":self.binding.tenant,"ref":new.session_ref,"digest":self.record_digest(category,new),"change":change})
            await db.execute(text("INSERT INTO portal_auth.source_change(tenant,change_id,category,identity_ref,source_revision,operation,old_record,new_record,new_record_digest,request_digest,state,dependencies) VALUES(:tenant,:id,:category,:ref,:revision,:operation,CAST(:old AS jsonb),CAST(:new AS jsonb),:new_digest,:digest,'freeze_pending',CAST(:dependencies AS jsonb))"),{"tenant":self.binding.tenant,"id":change,"category":category,"ref":identity_ref,"revision":revision,"operation":operation,"old":None if before is None else canonicalize(before).decode(),"new":None if after is None else canonicalize(after).decode(),"new_digest":None if new is None else self.record_digest(category,new),"digest":request_digest,"dependencies":canonicalize(list(refs)).decode()})
            await db.execute(text("UPDATE portal_auth.source_head SET state='frozen',pending_change=:id WHERE tenant=:tenant AND category=:category AND identity_ref=:ref"),{"tenant":self.binding.tenant,"category":category,"ref":identity_ref,"id":change})
        return change

    async def prepare_change(self, change_id: str) -> None:
        async with self.reader.connect() as db:
            await self.qualified(db,self.binding.reader_role)
            change=(await db.execute(text("SELECT state,dependencies FROM portal_auth.source_change WHERE tenant=:tenant AND change_id=:id"),{"tenant":self.binding.tenant,"id":change_id})).mappings().one()
        if change["state"] in ('source_committed','complete'): return
        for ref in change["dependencies"]:
            await self._freeze_dependency(ref,change_id)
        async with self.control.begin() as db:
            await self.qualified(db,self.binding.control_role)
            await db.execute(text("UPDATE portal_auth.source_change SET state='native_frozen' WHERE tenant=:tenant AND change_id=:id AND state='freeze_pending'"),{"tenant":self.binding.tenant,"id":change_id})

    async def apply_change(self, change_id: str, db: AsyncConnection | None = None) -> None:
        if db is not None:
            await self.qualified(db,self.binding.writer_role)
            await db.execute(text("SELECT portal_auth.apply_change(:tenant,:id)"),{"tenant":self.binding.tenant,"id":change_id})
            return
        async with self.writer.begin() as connection:
            await self.apply_change(change_id,connection)
        # Unknown transaction acknowledgement is recovered by this exact change ID.
        async with self.reader.connect() as connection:
            await self.qualified(connection,self.binding.reader_role)
            state=(await connection.execute(text("SELECT state FROM portal_auth.source_change WHERE tenant=:tenant AND change_id=:id"),{"tenant":self.binding.tenant,"id":change_id})).scalar_one()
            if state not in ('source_committed','complete'): raise AuthUnavailableError()

    async def finish_change(self, change_id: str) -> None:
        async with self.reader.connect() as db:
            await self.qualified(db,self.binding.reader_role)
            change=(await db.execute(text("SELECT * FROM portal_auth.source_change WHERE tenant=:tenant AND change_id=:id"),{"tenant":self.binding.tenant,"id":change_id})).mappings().one()
        if change["state"]=='complete': return
        if change["state"]!='source_committed' or change["source_receipt"] is None: raise AuthUnavailableError()
        for ref in change["dependencies"]:
            async with self.control.begin() as db:
                await self.qualified(db,self.binding.control_role)
                state=(await db.execute(text("SELECT state FROM portal_auth.reservation WHERE tenant=:tenant AND reservation_ref=:ref FOR UPDATE"),{"tenant":self.binding.tenant,"ref":ref})).scalar_one()
                if state in ('issuance_disabled','revoked_ack'): continue
                latest=(await db.execute(text("SELECT * FROM portal_auth.issuance WHERE tenant=:tenant AND reservation_ref=:ref ORDER BY expected_generation DESC LIMIT 1"),{"tenant":self.binding.tenant,"ref":ref})).mappings().one()
                old=parse_model(InputPublication,latest["request"])
                if latest["state"]=='pending':
                    if old.state!='revoked': raise AuthUnavailableError()
                    publication=old
                else:
                    if old.state!='frozen': raise AuthUnavailableError()
                    now=self.clock();until=min(self.binding.valid_until,now+timedelta(seconds=self.binding.control_seconds))
                    source=SourceProvenance(publisher_ref=self.binding.publisher_ref,source_ref=self.binding.source_ref,source_revision=change["source_revision"]+1,source_digest=digest(change["source_receipt"]),receipt_ref=change_id,observed_at=now,valid_until=until)
                    publication=InputPublication(schema="human-auth-input-publication.v1",scope=old.scope,workload_ref=old.workload_ref,publication_id=secrets.token_urlsafe(24),kind="audit_intent",resource_ref=old.resource_ref,expected_generation=latest["committed_generation"],source=source,state="revoked",payload=None,payload_digest=None,valid_until=until)
                    await self._insert_issuance(db,ref,publication)
            await self._resolve_publication(publication)
        async with self.control.begin() as db:
            await self.qualified(db,self.binding.control_role)
            await db.execute(text("UPDATE portal_auth.source_change SET state='complete' WHERE tenant=:tenant AND change_id=:id AND state='source_committed'"),{"tenant":self.binding.tenant,"id":change_id})

    async def put_session(self, session: SessionRecord, old_hash: str | None) -> None:
        old=None
        async with self.reader.connect() as db:
            await self.qualified(db,self.binding.reader_role)
            if old_hash is not None:
                raw=(await db.execute(text("SELECT payload FROM public.portal_sessions WHERE tenant=:tenant AND secret_hash=:hash"),{"tenant":self.binding.tenant,"hash":old_hash})).scalar_one_or_none()
                if raw is not None: old=SessionRecord.model_validate_json(raw)
            member_raw=(await db.execute(text("SELECT payload FROM public.portal_memberships WHERE tenant=:tenant AND issuer=:issuer AND subject=:subject"),{"tenant":self.binding.tenant,"issuer":session.issuer,"subject":session.subject})).scalar_one()
            member=MembershipRecord.model_validate_json(member_raw)
            if (member.principal_ref,member.revision,member.revoked)!=(session.principal_ref,session.membership_revision,False) or self.clock()>=min(session.expires_at,member.reviewed_until): raise AuthUnavailableError()
        change=await self._begin_change('session',old.session_ref if old is not None else session.session_ref,old,session,'rotate' if old is not None else 'put')
        await self.prepare_change(change)
        await self.apply_change(change)
        await self.finish_change(change)

    async def revoke_session(self, secret_hash: str) -> None:
        async with self.reader.connect() as db:
            await self.qualified(db,self.binding.reader_role)
            raw=(await db.execute(text("SELECT payload FROM public.portal_sessions WHERE tenant=:tenant AND secret_hash=:hash"),{"tenant":self.binding.tenant,"hash":secret_hash})).scalar_one_or_none()
        if raw is None: return
        old=SessionRecord.model_validate_json(raw)
        change=await self._begin_change('session',old.session_ref,old,None,'revoke')
        await self.prepare_change(change)
        await self.apply_change(change)
        await self.finish_change(change)

    async def purge_expired(self, now: datetime) -> None:
        async with self.reader.connect() as db:
            await self.qualified(db,self.binding.reader_role)
            rows=(await db.execute(text("SELECT secret_hash FROM public.portal_sessions WHERE tenant=:tenant AND expires_at<=:now ORDER BY secret_hash"),{"tenant":self.binding.tenant,"now":now})).scalars().all()
        for secret_hash in rows:
            await self.revoke_session(secret_hash)

    async def prepare_membership(self, record: MembershipRecord) -> str:
        async with self.reader.connect() as db:
            await self.qualified(db,self.binding.reader_role)
            raw=(await db.execute(text("SELECT payload FROM public.portal_memberships WHERE tenant=:tenant AND issuer=:issuer AND subject=:subject"),{"tenant":self.binding.tenant,"issuer":record.issuer,"subject":record.subject})).scalar_one_or_none()
        old=None if raw is None else MembershipRecord.model_validate_json(raw)
        if old is not None and (old.principal_ref!=record.principal_ref or record.revision<=old.revision): raise AuthUnavailableError()
        change=await self._begin_change('membership',record.principal_ref,old,record,'membership')
        await self.prepare_change(change)
        return change

    async def close(self) -> None:
        await self.native.close()
        for engine in {self.reader,self.writer,self.control,self.receipts}:
            await engine.dispose()

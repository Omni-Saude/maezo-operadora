"""Concrete AUTH current SQL authority, installed credentials and protected loader.

AUTH-SL1: origin session pins are immutable; receipt-read authority is independent.
Neither configuration nor enumerated native rows create a source/resource grant.
"""

from __future__ import annotations

import base64
import hashlib
import os
import ssl
import stat
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from cryptography import x509
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from pydantic import Field, SecretStr
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine

from maezo.gateway.human.auth_profile import (
    PAYLOAD_TYPES,
    Actor,
    AuditIntentPayload,
    EffectCommand,
    HumanStartCommand,
    InputKind,
    Purpose,
    ResourceAuthority,
    Scope,
)
from maezo.gateway.human.auth_transport import (
    AuthCredentialLease,
    AuthCredentialProvider,
    AuthEffectCeiling,
    AuthNativeClient,
    AuthNativeTrustLease,
    AuthUnavailableError,
)
from maezo.gateway.human.read_profile import Closed, SourceProvenance, digest, parse_model, wire
from maezo.portal.engine.profile import strict_loads

from .native_dispatch import AuthEffectAuthorizationSource
from .native_source_lifecycle import (
    AuthCallerBinding,
    IdentitySourceBinding,
    PostgresAuthSourceLifecycle,
    RelationPin,
)


class SourceGrant(Closed):
    kind: InputKind
    source_ref: str
    publisher_ref: str
    resource_ref: str


class KeyDesignation(Closed):
    schema_: Literal["human-auth-key-designation.v1"] = Field(alias="schema")
    key_id: str
    issuer: str
    purpose: Purpose
    peer_spki_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    public_key_base64: str
    not_before: datetime
    not_after: datetime
    source_grants: tuple[SourceGrant, ...]


class NativeDatabaseBinding(Closed):
    scope: Scope
    database_name: str
    database_oid: int = Field(gt=0)
    schema_name: str = Field(pattern=r"^[a-z][a-z0-9_]{0,62}$")
    schema_oid: int = Field(gt=0)
    owner_role: str
    reader_role: str
    relations: tuple[RelationPin, ...]
    installed_binding_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    installed_qualification_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    valid_until: datetime


class NativeAuthReader:
    def __init__(self, engine: AsyncEngine, binding: NativeDatabaseBinding) -> None:
        required = {
            "mzo_auth_" + n for n in ("installation", "trust", "revoked_key", "input_head", "input_version")
        }
        if (
            engine.dialect.name != "postgresql"
            or {p.name for p in binding.relations} != required
            or len(binding.relations) != len(required)
            or any(
                p.schema_name != binding.schema_name or p.owner != binding.owner_role
                for p in binding.relations
            )
            or binding.reader_role == binding.owner_role
        ):
            raise AuthUnavailableError()
        self.engine, self.binding = engine, binding

    def table(self, name: str) -> str:
        if name not in {p.name for p in self.binding.relations}:
            raise AuthUnavailableError()
        return '"' + self.binding.schema_name + '"."' + name + '"'

    async def qualified(self, db: AsyncConnection) -> tuple[dict[str, Any], datetime]:
        b = self.binding
        row = (
            (
                await db.execute(
                    text(
                        "SELECT current_user AS role,session_user AS session_role,current_database() AS "
                        "name,(SELECT oid FROM pg_database WHERE datname=current_database()) AS oid,(SELECT "
                        "oid FROM pg_namespace WHERE nspname=:schema) AS schema_oid,(SELECT ssl FROM "
                        "pg_stat_ssl WHERE pid=pg_backend_pid()) AS tls"
                    ),
                    {"schema": b.schema_name},
                )
            )
            .mappings()
            .one()
        )
        if tuple(row.values()) != (
            b.reader_role,
            b.reader_role,
            b.database_name,
            b.database_oid,
            b.schema_oid,
            True,
        ):
            raise AuthUnavailableError()
        privileged = (
            await db.execute(
                text(
                    "SELECT rolsuper OR rolbypassrls OR rolreplication FROM pg_role"
                    "s WHERE rolname=current_user"
                )
            )
        ).scalar_one()
        if privileged:
            raise AuthUnavailableError()
        for pin in b.relations:
            row = (
                (
                    await db.execute(
                        text(
                            "SELECT c.oid,pg_get_userbyid(c.relowner) AS "
                            "owner,c.relkind,c.relrowsecurity,c.relforcerowsecurity,has_tab"
                            "le_privilege(current_user,c.oid,'SELECT') AS readable,has_tabl"
                            "e_privilege(current_user,c.oid,'INSERT,UPDATE,DELETE,TRUNCATE,"
                            "REFERENCES,TRIGGER') OR has_any_column_privilege(current_user,"
                            "c.oid,'INSERT,UPDATE,REFERENCES') AS writable FROM pg_class c "
                            "JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname=:s"
                            "chema AND c.relname=:name"
                        ),
                        {"schema": pin.schema_name, "name": pin.name},
                    )
                )
                .mappings()
                .one()
            )
            if tuple(row.values()) != (pin.oid, pin.owner, "r", False, False, True, False):
                raise AuthUnavailableError()
        installed = (
            (
                await db.execute(
                    text("SELECT * FROM " + self.table("mzo_auth_installation") + " WHERE tenant_=:tenant"),
                    {"tenant": b.scope.tenant},
                )
            )
            .mappings()
            .one()
        )
        scope = strict_loads(installed["scope_"])
        qualification = strict_loads(installed["qualification_"])
        if (
            scope,
            installed["incarnation_"],
            installed["rev_"],
            digest(strict_loads(installed["binding_"])),
            digest(qualification),
        ) != (
            wire(b.scope),
            b.scope.database_incarnation,
            b.scope.installation_revision,
            b.installed_binding_digest,
            b.installed_qualification_digest,
        ):
            raise AuthUnavailableError()
        until = min(
            b.valid_until, datetime.fromisoformat(qualification["valid_until"].replace("Z", "+00:00"))
        )
        if datetime.now(UTC) >= until:
            raise AuthUnavailableError()
        return dict(installed), until

    async def key(self, db: AsyncConnection, key_id: str) -> KeyDesignation:
        row = (
            (
                await db.execute(
                    text(
                        "SELECT t.designation_,r.key_id_ AS revoked FROM "
                        + self.table("mzo_auth_trust")
                        + " t LEFT JOIN "
                        + self.table("mzo_auth_revoked_key")
                        + " r ON r.tenant_=t.tenant_ AND r.key_id_=t.key_id_ WHERE t.tena"
                        "nt_=:tenant AND t.key_id_=:key"
                    ),
                    {"tenant": self.binding.scope.tenant, "key": key_id},
                )
            )
            .mappings()
            .one()
        )
        value = parse_model(KeyDesignation, strict_loads(row["designation_"]))
        now = datetime.now(UTC)
        if (
            row["revoked"] is not None
            or value.key_id != key_id
            or not value.not_before <= now < value.not_after
        ):
            raise AuthUnavailableError()
        raw = base64.b64decode(value.public_key_base64, validate=True)
        if (
            len(raw) != 44
            or base64.b64encode(raw).decode() != value.public_key_base64
            or not isinstance(serialization.load_der_public_key(raw), Ed25519PublicKey)
        ):
            raise AuthUnavailableError()
        if len(value.source_grants) > 1024 or len({digest(g) for g in value.source_grants}) != len(
            value.source_grants
        ):
            raise AuthUnavailableError()
        if (value.purpose in ("human-auth-input-publication", "human-auth-publication-read")) != bool(
            value.source_grants
        ):
            raise AuthUnavailableError()
        return value

    async def head(self, db: AsyncConnection, kind: str, resource: str) -> tuple[Any, Any, datetime]:
        row = (
            (
                await db.execute(
                    text(
                        "SELECT * FROM "
                        + self.table("mzo_auth_input_head")
                        + " WHERE tenant_=:tenant AND kind_=:kind AND resource_=:resource"
                    ),
                    {"tenant": self.binding.scope.tenant, "kind": kind, "resource": resource},
                )
            )
            .mappings()
            .one()
        )
        if row["state_"] != "active":
            raise AuthUnavailableError()
        source = parse_model(SourceProvenance, strict_loads(row["source_"]))
        publisher = await self.key(db, row["publisher_key_"])
        required = SourceGrant(
            kind=kind, source_ref=source.source_ref, publisher_ref=source.publisher_ref, resource_ref=resource
        )
        if (
            publisher.purpose != "human-auth-input-publication"
            or digest(publisher) != row["publisher_digest_"]
            or required not in publisher.source_grants
        ):
            raise AuthUnavailableError()
        payload = parse_model(PAYLOAD_TYPES[kind], strict_loads(row["payload_"]))
        if digest(payload) != row["payload_digest_"]:
            raise AuthUnavailableError()
        version = (
            (
                await db.execute(
                    text(
                        "SELECT request_,digest_,receipt_ FROM "
                        + self.table("mzo_auth_input_version")
                        + " WHERE tenant_=:tenant AND kind_=:kind AND resource_=:resource"
                        " AND generation_=:generation"
                    ),
                    {
                        "tenant": self.binding.scope.tenant,
                        "kind": kind,
                        "resource": resource,
                        "generation": row["generation_"],
                    },
                )
            )
            .mappings()
            .one()
        )
        request = strict_loads(version["request_"])
        if (
            digest(request) != version["digest_"]
            or version["digest_"] != row["publication_digest_"]
            or request["payload"] != wire(payload)
            or request["source"] != wire(source)
        ):
            raise AuthUnavailableError()
        now = datetime.now(UTC)
        until = min(row["valid_until_"], source.valid_until, publisher.not_after)
        if not source.observed_at <= now < until:
            raise AuthUnavailableError()
        return payload, dict(row), until

    async def observation(
        self, command: EffectCommand, actor: Actor, *, read: bool, original_intent: AuditIntentPayload | None
    ) -> tuple[str, datetime]:
        b = self.binding
        if command.scope != b.scope:
            raise AuthUnavailableError()
        async with self.engine.connect() as db:
            await db.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"))
            installed, until = await self.qualified(db)
            rows = []
            values = {}
            if read:
                membership, row, deadline = await self.head(db, "actor", actor.principal_ref)
                rows.append(row)
                until = min(until, deadline, membership.reviewed_until)
                ref = command.intake_ref if isinstance(command, HumanStartCommand) else command.case_ref
                kind = "intake" if isinstance(command, HumanStartCommand) else "case"
                candidates = (
                    (
                        await db.execute(
                            text(
                                "SELECT resource_ FROM "
                                + self.table("mzo_auth_input_head")
                                + " WHERE tenant_=:tenant AND kind_='resource_authority' AND stat"
                                "e_='active' AND payload_::jsonb->>'action'='auth.receipt.read'"
                                " AND payload_::jsonb->>'resource_ref'=:resource AND payload_::"
                                "jsonb->>'resource_kind'=:kind AND payload_::jsonb->'actor'->>'"
                                "principal_ref'=:principal"
                            ),
                            {
                                "tenant": b.scope.tenant,
                                "resource": ref,
                                "kind": kind,
                                "principal": actor.principal_ref,
                            },
                        )
                    )
                    .scalars()
                    .all()
                )
                if len(candidates) != 1:
                    raise AuthUnavailableError()
                authority, row, deadline = await self.head(db, "resource_authority", candidates[0])
                rows.append(row)
                until = min(until, deadline)
            else:
                for pin in command.input_pins:
                    payload, row, deadline = await self.head(db, pin.kind, pin.resource_ref)
                    if (row["generation_"], row["payload_digest_"], strict_loads(row["source_"])) != (
                        pin.head_generation,
                        pin.payload_digest,
                        wire(pin.source),
                    ):
                        raise AuthUnavailableError()
                    rows.append(row)
                    values[(pin.kind, pin.resource_ref)] = payload
                    until = min(until, deadline)
                membership = values.get(("actor", actor.principal_ref))
                authorities = [value for (kind, _), value in values.items() if kind == "resource_authority"]
                if len(authorities) != 1 or membership is None:
                    raise AuthUnavailableError()
                authority = authorities[0]
                actual, row, deadline = await self.head(db, "audit_intent", command.admission.intent_ref)
                if actual != original_intent or strict_loads(row["source_"]) != wire(
                    command.admission.source
                ):
                    raise AuthUnavailableError()
                if original_intent is None or (actual.command_id, actual.admitted_digest, actual.actor) != (
                    command.command_id,
                    command.admission.admitted_digest,
                    actor,
                ):
                    raise AuthUnavailableError()
                rows.append(row)
                until = min(
                    until, deadline, actual.session_binding.authorization_until, membership.reviewed_until
                )
            if (
                membership.principal_ref,
                membership.issuer,
                membership.subject,
                membership.membership_revision,
                membership.audience,
                membership.state,
            ) != (
                actor.principal_ref,
                actor.issuer,
                actor.subject,
                actor.membership_revision,
                actor.audience,
                "active",
            ):
                raise AuthUnavailableError()
            if not isinstance(authority, ResourceAuthority):
                raise AuthUnavailableError()
            action = (
                "auth.receipt.read"
                if read
                else "auth.start"
                if isinstance(command, HumanStartCommand)
                else "auth.documents.respond"
            )
            resource = (
                (command.intake_ref if read else command.guide_identity_ref)
                if isinstance(command, HumanStartCommand)
                else command.case_ref
            )
            kind = ("intake" if read else "guide") if isinstance(command, HumanStartCommand) else "case"
            request = (
                None if read or isinstance(command, HumanStartCommand) else command.occurrence.request_ref
            )
            if (
                (
                    authority.actor,
                    authority.action,
                    authority.resource_kind,
                    authority.resource_ref,
                    authority.request_ref,
                    authority.state,
                )
                != (actor, action, kind, resource, request, "active")
                or authority.consent_state == "revoked"
                or (authority.legal_basis == "consent" and authority.consent_state != "valid")
            ):
                raise AuthUnavailableError()
            now = datetime.now(UTC)
            if not authority.valid_from <= now < authority.valid_until:
                raise AuthUnavailableError()
            until = min(until, authority.valid_until, authority.source.valid_until)
            observation = digest({"installation": installed, "heads": rows})
        if datetime.now(UTC) >= until:
            raise AuthUnavailableError()
        return observation, until


class PostgresAuthEffectAuthorizationSource(AuthEffectAuthorizationSource):
    def __init__(self, *, source: PostgresAuthSourceLifecycle, native: NativeAuthReader) -> None:
        self.source, self.native = source, native

    async def current(
        self, command: EffectCommand, *, read: bool, caller: AuthCallerBinding | None = None
    ) -> Any:
        from .native_dispatch import AuthEffectLease

        if read and caller is None:
            raise AuthUnavailableError()
        original = None if read else await self.source.protected.original_identity(command.command_id)
        original_principal = await self.source.protected.original_principal(command.command_id)
        if read:
            if caller is None:
                raise AuthUnavailableError()
            session = await caller.revalidate()
            principal = session.principal
            session_until = caller.valid_until
            actor = Actor.from_principal(principal, session.membership.audience)
        else:
            if original is None:
                raise AuthUnavailableError()
            principal = original.principal
            session_until = await self.source.current_original(original)
            actor = await self.source.actor_for_original(original)
        if (original_principal.principal_ref, original_principal.issuer, original_principal.subject) != (
            command.actor.principal_ref,
            command.actor.issuer,
            command.actor.subject,
        ):
            raise AuthUnavailableError()
        if (actor.principal_ref, actor.issuer, actor.subject, actor.audience) != (
            command.actor.principal_ref,
            command.actor.issuer,
            command.actor.subject,
            command.actor.audience,
        ) or (not read and actor != command.actor):
            raise AuthUnavailableError()
        intent = None if read else await self.source.protected.audit_intent(command.command_id, actor)
        observation, native_until = await self.native.observation(
            command, actor, read=read, original_intent=intent
        )
        until = min(session_until, native_until)
        invalid = False

        def guard() -> None:
            if invalid or datetime.now(UTC) >= until:
                raise AuthUnavailableError()

        async def revalidate() -> None:
            nonlocal invalid
            try:
                if read:
                    if caller is None:
                        raise AuthUnavailableError()
                    await caller.revalidate()
                else:
                    if original is None:
                        raise AuthUnavailableError()
                    await self.source.current_original(original)
                current, current_until = await self.native.observation(
                    command, actor, read=read, original_intent=intent
                )
                if current != observation or datetime.now(UTC) >= min(until, current_until):
                    raise AuthUnavailableError()
                guard()
            except BaseException:
                invalid = True
                raise

        action: Literal["auth.receipt.read", "auth.start", "auth.documents.respond"] = (
            "auth.receipt.read"
            if read
            else "auth.start"
            if isinstance(command, HumanStartCommand)
            else "auth.documents.respond"
        )
        resource = command.intake_ref if isinstance(command, HumanStartCommand) else command.case_ref
        if not read and original is None:
            raise AuthUnavailableError()
        result = AuthEffectLease(
            command.scope,
            actor,
            digest(command),
            action,
            resource,
            until,
            guard,
            revalidate,
            None
            if read or original is None
            else AuthEffectCeiling(digest(command), original.session_binding, until),
        )
        await revalidate()
        return result


class SigningMaterial(Closed):
    purpose: Purpose
    key_id: str
    issuer: str
    private_key_path: Path = Field(repr=False)
    private_key_digest: str = Field(pattern=r"^[a-f0-9]{64}$", repr=False)
    designation_digest: str = Field(pattern=r"^[a-f0-9]{64}$")


class NativeClientBinding(Closed):
    origin: str
    audience: str
    client_certificate_path: Path = Field(repr=False)
    client_private_key_path: Path = Field(repr=False)
    ca_path: Path = Field(repr=False)
    client_certificate_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    client_private_key_digest: str = Field(pattern=r"^[a-f0-9]{64}$", repr=False)
    ca_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    native_key_id: str
    native_designation_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    signing: tuple[SigningMaterial, ...]
    max_envelope_seconds: int = Field(gt=0, le=300)


def protected_bytes(path: Path, expected: str | None = None) -> bytes:
    """Bounded owner-controlled regular file; no symlink or permissive secret mode."""
    if not path.is_absolute():
        raise AuthUnavailableError()
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        metadata = os.fstat(fd)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid not in (0, os.geteuid())
            or stat.S_IMODE(metadata.st_mode) & 0o077
            or metadata.st_size > 262144
        ):
            raise AuthUnavailableError()
        value = os.read(fd, 262145)
        if len(value) != metadata.st_size or (
            expected is not None and hashlib.sha256(value).hexdigest() != expected
        ):
            raise AuthUnavailableError()
        return value
    finally:
        os.close(fd)


class PostgresAuthCredentialProvider(AuthCredentialProvider):
    """Purpose-bound private key plus actual current installed native designation."""

    def __init__(self, native: NativeAuthReader, config: NativeClientBinding) -> None:
        self.native, self.config = native, config
        purposes = [material.purpose for material in config.signing]
        if len(set(purposes)) != len(purposes) or "human-auth-result" in purposes:
            raise AuthUnavailableError()
        self.materials = {material.purpose: material for material in config.signing}

    async def acquire(self, scope: Scope, purpose: Purpose) -> AuthCredentialLease:
        if scope != self.native.binding.scope or purpose not in self.materials:
            raise AuthUnavailableError()
        material = self.materials[purpose]
        async with self.native.engine.connect() as db:
            _, until = await self.native.qualified(db)
            designation = await self.native.key(db, material.key_id)
            if (designation.purpose, designation.issuer, digest(designation)) != (
                purpose,
                material.issuer,
                material.designation_digest,
            ):
                raise AuthUnavailableError()
        key = serialization.load_pem_private_key(
            protected_bytes(material.private_key_path, material.private_key_digest), password=None
        )
        if not isinstance(key, Ed25519PrivateKey):
            raise AuthUnavailableError()
        public = key.public_key().public_bytes(
            serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
        )
        if base64.b64encode(public).decode() != designation.public_key_base64:
            raise AuthUnavailableError()
        until = min(until, designation.not_after)

        def live() -> None:
            protected_bytes(material.private_key_path, material.private_key_digest)
            if datetime.now(UTC) >= until:
                raise AuthUnavailableError()

        lease = AuthCredentialLease(
            scope,
            purpose,
            material.issuer,
            material.key_id,
            self.config.audience,
            hashlib.sha256(public).hexdigest(),
            designation.peer_spki_sha256,
            designation.not_before,
            until,
            self.config.max_envelope_seconds,
            key,
            live,
        )
        lease.guard(datetime.now(UTC))
        return lease

    async def native_trust(self, scope: Scope) -> AuthNativeTrustLease:
        if scope != self.native.binding.scope:
            raise AuthUnavailableError()
        async with self.native.engine.connect() as db:
            _, until = await self.native.qualified(db)
            d = await self.native.key(db, self.config.native_key_id)
            if d.purpose != "human-auth-result" or digest(d) != self.config.native_designation_digest:
                raise AuthUnavailableError()
        key = serialization.load_der_public_key(base64.b64decode(d.public_key_base64, validate=True))
        if not isinstance(key, Ed25519PublicKey):
            raise AuthUnavailableError()
        until = min(until, d.not_after)

        def live() -> None:
            if datetime.now(UTC) >= until:
                raise AuthUnavailableError()

        result = AuthNativeTrustLease(
            scope,
            d.issuer,
            d.key_id,
            self.config.audience,
            d.peer_spki_sha256,
            d.not_before,
            until,
            self.config.max_envelope_seconds,
            key,
            live,
        )
        result.guard(datetime.now(UTC))
        return result


class ProtectedStoreBinding(Closed):
    database_name: str
    database_oid: int = Field(gt=0)
    role: str
    schema_oid: int = Field(gt=0)
    relations: tuple[RelationPin, ...]
    valid_until: datetime


class AuthLifecycleConfiguration(Closed):
    schema_: Literal["human-auth-lifecycle-installation.v1"] = Field(alias="schema")
    identity: IdentitySourceBinding
    native: NativeDatabaseBinding
    client: NativeClientBinding
    protected: ProtectedStoreBinding
    identity_reader_url: SecretStr = Field(repr=False)
    identity_control_url: SecretStr = Field(repr=False)
    identity_receipt_url: SecretStr = Field(repr=False)
    native_reader_url: SecretStr = Field(repr=False)
    protected_url: SecretStr = Field(repr=False)
    journal_key_id: str
    journal_key_path: Path = Field(repr=False)
    journal_key_digest: str = Field(pattern=r"^[a-f0-9]{64}$", repr=False)
    intake_key_id: str
    intake_key_path: Path = Field(repr=False)
    intake_key_digest: str = Field(pattern=r"^[a-f0-9]{64}$", repr=False)


def _engine(url: SecretStr) -> AsyncEngine:
    parsed = make_url(url.get_secret_value())
    if parsed.drivername != "postgresql+asyncpg" or not parsed.host or not parsed.database or parsed.query:
        raise AuthUnavailableError()
    return create_async_engine(
        parsed,
        hide_parameters=True,
        echo=False,
        pool_size=1,
        max_overflow=0,
        connect_args={"ssl": ssl.create_default_context(), "command_timeout": 5},
    )


class AuthProductionComposition:
    """Concrete installation object for identity and existing intake/dispatch factories."""

    def __init__(
        self,
        *,
        config: AuthLifecycleConfiguration,
        source: PostgresAuthSourceLifecycle,
        native: NativeAuthReader,
        client: AuthNativeClient,
        intake_key: bytes,
    ) -> None:
        self.config, self.source, self.native, self.client, self._intake_key = (
            config,
            source,
            native,
            client,
            intake_key,
        )
        self.authority = PostgresAuthEffectAuthorizationSource(source=source, native=native)
        source.protected.qualifier = self.qualify_protected_connection

    async def qualify_protected(self) -> None:
        async with self.source.protected.engine.connect() as db:
            await self.qualify_protected_connection(db)

    async def qualify_protected_connection(self, db: AsyncConnection) -> None:
        b = self.config.protected
        row = (
            (
                await db.execute(
                    text(
                        "SELECT current_user AS role,session_user AS session_role,current_database() AS "
                        "name,(SELECT oid FROM pg_database WHERE datname=current_databa"
                        "se()) AS oid,(SELECT "
                        "oid FROM pg_namespace WHERE nspname='portal_intake') AS schema"
                        "_oid,(SELECT ssl FROM "
                        "pg_stat_ssl WHERE pid=pg_backend_pid()) AS tls"
                    )
                )
            )
            .mappings()
            .one()
        )
        if tuple(row.values()) != (b.role, b.role, b.database_name, b.database_oid, b.schema_oid, True):
            raise AuthUnavailableError()
        required = {
            "intake",
            "admission_event",
            "native_outbox",
            "native_identity",
            "native_audit",
            "native_publication",
            "native_response",
            "native_case_binding",
        }
        if {p.name for p in b.relations} != required or len(b.relations) != len(required):
            raise AuthUnavailableError()
        for pin in b.relations:
            row = (
                (
                    await db.execute(
                        text(
                            "SELECT "
                            "c.oid,c.relkind,c.relrowsecurity,c.relforcerowsecurity,pg_get_"
                            "userbyid(c.relowner) "
                            "AS owner FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE "
                            "n.nspname=:schema AND c.relname=:name"
                        ),
                        {"schema": pin.schema_name, "name": pin.name},
                    )
                )
                .mappings()
                .one()
            )
            if pin.schema_name != "portal_intake" or tuple(row.values()) != (
                pin.oid,
                "r",
                False,
                False,
                pin.owner,
            ):
                raise AuthUnavailableError()
        if datetime.now(UTC) >= b.valid_until:
            raise AuthUnavailableError()

    async def qualify(self) -> None:
        for engine, role in (
            (self.source.reader, self.source.binding.reader_role),
            (self.source.writer, self.source.binding.writer_role),
            (self.source.control, self.source.binding.control_role),
            (self.source.receipts, self.source.binding.receipt_role),
        ):
            async with engine.connect() as db:
                await self.source.qualified(db, role)
        async with self.native.engine.connect() as db:
            await self.native.qualified(db)
        await self.qualify_protected()

    async def publish_admission(self, command_id: str) -> Any:
        """Publish only the actual joined source/protected admission for host binding."""
        await self.qualify()
        original = await self.source.protected.original_identity(command_id)
        material = next(m for m in self.config.client.signing if m.purpose == "human-auth-input-publication")
        return await self.source.publish_admission(original, workload_ref=material.issuer)

    def intake_components(self, authority: Any) -> Any:
        from maezo.gateway.human.auth_projection import projection_digest

        from .native_composition import AuthIntakeComponents
        from .native_dispatch import AuthDispatcher, HumanIntakeStartTransport
        from .postgres import PostgresIntakeStore

        store = self.source.protected
        admission = PostgresIntakeStore(
            store.engine,
            tenant=store.tenant,
            key_id=self.config.intake_key_id,
            encryption_key=self._intake_key,
            native_dispatch=store,
        )
        dispatcher = AuthDispatcher(store=store, client=self.client, authority=self.authority)
        material = next(m for m in self.config.client.signing if m.purpose == "human-auth-start")
        transport = HumanIntakeStartTransport(
            dispatcher=dispatcher, workload_ref=material.issuer, projection=projection_digest
        )
        return AuthIntakeComponents(admission, store, dispatcher, transport, authority)

    async def close(self) -> None:
        await self.source.close()
        await self.native.engine.dispose()
        await self.source.protected.engine.dispose()


def load_auth_lifecycle(
    path: Path, *, tenant: str, identity_writer: AsyncEngine
) -> AuthProductionComposition:
    """Gateway-only protected loader; actual DB qualification occurs before use."""
    from maezo.gateway.human.auth_publisher import PostgresAuthPublicationJournal

    from .native_store import PostgresAuthDispatchStore

    config = AuthLifecycleConfiguration.model_validate_json(protected_bytes(path), strict=True)
    if config.identity.tenant != tenant or config.native.scope not in config.identity.scopes:
        raise AuthUnavailableError()
    cert_bytes = protected_bytes(
        config.client.client_certificate_path, config.client.client_certificate_digest
    )
    key_bytes = protected_bytes(
        config.client.client_private_key_path, config.client.client_private_key_digest
    )
    protected_bytes(config.client.ca_path, config.client.ca_digest)
    certificate = x509.load_pem_x509_certificate(cert_bytes)
    key = serialization.load_pem_private_key(key_bytes, password=None)
    if key.public_key().public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
    ) != certificate.public_key().public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
    ):
        raise AuthUnavailableError()
    tls = ssl.create_default_context(cafile=str(config.client.ca_path))
    tls.load_cert_chain(
        str(config.client.client_certificate_path), str(config.client.client_private_key_path)
    )
    reader = NativeAuthReader(_engine(config.native_reader_url), config.native)
    credentials = PostgresAuthCredentialProvider(reader, config.client)
    client = AuthNativeClient(
        origin=config.client.origin,
        tls_context=tls,
        credentials=credentials,
        client_certificate_der=certificate.public_bytes(serialization.Encoding.DER),
    )
    journal_key = protected_bytes(config.journal_key_path, config.journal_key_digest)
    intake_key = protected_bytes(config.intake_key_path, config.intake_key_digest)
    if len(journal_key) != 32 or len(intake_key) != 32 or journal_key == intake_key:
        raise AuthUnavailableError()
    store = PostgresAuthDispatchStore(
        _engine(config.protected_url), tenant=tenant, key_id=config.journal_key_id, key=journal_key
    )
    journal = PostgresAuthPublicationJournal(store)
    source = PostgresAuthSourceLifecycle(
        binding=config.identity,
        reader=_engine(config.identity_reader_url),
        writer=identity_writer,
        control=_engine(config.identity_control_url),
        receipts=_engine(config.identity_receipt_url),
        native=client,
        publication_journal=journal,
        protected_store=store,
    )
    store.bind_source(source, config.native.scope)
    return AuthProductionComposition(
        config=config, source=source, native=reader, client=client, intake_key=intake_key
    )

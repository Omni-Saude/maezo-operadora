"""Concrete GET-only PHI communication application composition."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import ssl
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from tempfile import NamedTemporaryFile

from fastapi import FastAPI
from sqlalchemy import text
from sqlalchemy.engine import URL
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from maezo.portal.api.communication_phi import create_phi_communication_app
from maezo.portal.api.config import PortalSettings
from maezo.portal.api.session import HumanSessionResolver

from .admission import PostgresCommunicationAuthority
from .content import PhiCommunicationService, PhiContentKeys, PostgresPhiCommunicationContent
from .identity_owner import IdentityOwnerClient
from .identity_reader import PhiIdentityReader
from .production_config import (
    SCRATCH_DIRECTORY,
    ObjectPin,
    PhiProductionError,
    PhiProductionSettings,
    ReaderProfile,
    RegisteredDatabase,
)
from .production_materials import PhiMaterials, load_materials


def _quiet() -> None:
    for name in tuple(logging.Logger.manager.loggerDict):
        if name.startswith(
            ("sqlalchemy.engine", "sqlalchemy.pool", "asyncpg", "httpx", "httpcore")
        ) and logging.getLogger(name).isEnabledFor(logging.INFO):
            raise PhiProductionError()


def _portal_digest(settings: PortalSettings, reference: str) -> str:
    value = {
        "reference": reference,
        "tenant": settings.tenant,
        "issuer": settings.issuer,
        "public_origin": settings.public_origin,
        "session_seconds": settings.session_seconds,
    }
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _tls(ca: bytes) -> ssl.SSLContext:
    try:
        context = ssl.create_default_context(cadata=ca.decode("utf-8"))
        if not context.check_hostname or context.verify_mode != ssl.CERT_REQUIRED:
            raise PhiProductionError()
        return context
    except Exception:
        raise PhiProductionError() from None


def _owner_tls(materials: PhiMaterials) -> ssl.SSLContext:
    context = _tls(materials.owner_ca)
    with (
        NamedTemporaryFile(dir=SCRATCH_DIRECTORY, prefix="owner-cert-", suffix=".pem") as certificate,
        NamedTemporaryFile(dir=SCRATCH_DIRECTORY, prefix="owner-key-", suffix=".pem") as key,
    ):
        certificate.write(materials.owner_certificate)
        certificate.flush()
        key.write(materials.owner_private_key)
        key.flush()
        context.load_cert_chain(certificate.name, key.name)
    return context


def _engine(url: URL, ca: bytes, seconds: int) -> AsyncEngine:
    return create_async_engine(
        url,
        hide_parameters=True,
        echo=False,
        pool_size=2,
        max_overflow=0,
        pool_timeout=seconds,
        connect_args={"ssl": _tls(ca), "timeout": seconds, "command_timeout": seconds},
    )


async def _close(close: Callable[[], Awaitable[None]], seconds: int) -> None:
    async def bounded() -> None:
        async with asyncio.timeout(seconds):
            await close()

    task = asyncio.create_task(bounded())
    try:
        await asyncio.shield(task)
    except asyncio.CancelledError:
        await task
        raise


def _pin(registration: RegisteredDatabase, name: str) -> ObjectPin:
    try:
        return next(pin for pin in registration.objects if pin.qualified_name == name)
    except StopIteration:
        raise PhiProductionError() from None


async def qualify_reader(
    engine: AsyncEngine,
    profile: ReaderProfile,
    registration: RegisteredDatabase,
    *,
    seconds: int,
) -> None:
    """Qualify one effective read-only login and its complete permitted relation set."""
    async with asyncio.timeout(seconds):
        async with engine.connect() as connection:
            identity = (
                (
                    await connection.execute(
                        text(
                            """SELECT session_user::text AS login,current_user::text AS effective,
                            current_database()::text AS database,d.oid::bigint AS database_oid,
                            c.system_identifier::text AS system_ref,r.rolsuper,r.rolbypassrls,
                            r.rolcreaterole,r.rolcreatedb,r.rolreplication
                            FROM pg_database d CROSS JOIN pg_control_system() c
                            JOIN pg_roles r ON r.rolname=session_user
                            WHERE d.datname=current_database()"""
                        )
                    )
                )
                .mappings()
                .one()
            )
            if (
                identity["login"] != profile.login
                or identity["effective"] != profile.login
                or identity["database"] != registration.database_name
                or identity["database_oid"] != registration.database_oid
                or identity["system_ref"] != registration.system_ref
                or any(
                    identity[name]
                    for name in (
                        "rolsuper",
                        "rolbypassrls",
                        "rolcreaterole",
                        "rolcreatedb",
                        "rolreplication",
                    )
                )
            ):
                raise PhiProductionError()
            rows = (
                (
                    await connection.execute(
                        text(
                            """SELECT n.nspname AS schema_name,c.relname AS name,c.oid::bigint AS oid,
                            pg_get_userbyid(c.relowner) AS owner,c.relkind AS kind,
                            c.relrowsecurity,c.relforcerowsecurity,
                            has_table_privilege(session_user,c.oid,'SELECT') AS selectable,
                            has_table_privilege(session_user,c.oid,
                              'INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER') AS writable,
                            has_any_column_privilege(session_user,c.oid,
                              'INSERT,UPDATE,REFERENCES') AS column_writable,
                            has_table_privilege('public',c.oid,
                              'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER') AS public_access,
                            has_any_column_privilege('public',c.oid,
                              'SELECT,INSERT,UPDATE,REFERENCES') AS public_column_access,
                            pg_has_role(session_user,c.relowner,'MEMBER') AS owner_member,
                            CASE WHEN c.relkind='v' THEN pg_get_viewdef(c.oid,true) ELSE NULL END
                              AS definition
                            FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
                            WHERE n.nspname IN ('public','portal_communication')
                              AND c.relkind IN ('r','p','v')
                              AND (has_table_privilege(session_user,c.oid,
                                'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER')
                                OR has_any_column_privilege(session_user,c.oid,
                                  'SELECT,INSERT,UPDATE,REFERENCES'))
                            ORDER BY n.nspname,c.relname"""
                        )
                    )
                )
                .mappings()
                .all()
            )
            if {f"{row['schema_name']}.{row['name']}" for row in rows} != set(profile.objects):
                raise PhiProductionError()
            for row in rows:
                pin = _pin(registration, f"{row['schema_name']}.{row['name']}")
                definition = row["definition"]
                definition_digest = (
                    None if definition is None else hashlib.sha256(definition.encode()).hexdigest()
                )
                if (
                    (row["oid"], row["owner"], row["kind"]) != (pin.oid, pin.owner, pin.kind)
                    or row["relrowsecurity"]
                    or row["relforcerowsecurity"]
                    or row["selectable"] is not True
                    or row["writable"]
                    or row["column_writable"]
                    or row["public_access"]
                    or row["public_column_access"]
                    or row["owner_member"]
                    or definition_digest != pin.definition_sha256
                ):
                    raise PhiProductionError()


class PhiProductionRuntime:
    def __init__(
        self,
        *,
        identity: PhiIdentityReader,
        content_engine: AsyncEngine,
        authority_engine: AsyncEngine,
        materials: PhiMaterials,
        seconds: int,
    ) -> None:
        self.identity = identity
        self.content_engine = content_engine
        self.authority_engine = authority_engine
        self.materials = materials
        self.seconds = seconds
        self.closed = False

    async def qualify(self) -> None:
        manifest = self.materials.manifest
        await qualify_reader(
            self.identity._engine,
            manifest.identity_reader,
            manifest.registered_database,
            seconds=self.seconds,
        )
        await qualify_reader(
            self.content_engine,
            manifest.content_reader,
            manifest.registered_database,
            seconds=self.seconds,
        )
        await qualify_reader(
            self.authority_engine,
            manifest.authority_reader,
            manifest.registered_database,
            seconds=self.seconds,
        )

    async def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        try:
            await _close(self.identity.close, self.seconds)
        finally:
            try:
                await _close(self.content_engine.dispose, self.seconds)
            finally:
                await _close(self.authority_engine.dispose, self.seconds)


def _scratch() -> None:
    descriptor = os.open(SCRATCH_DIRECTORY, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        value = os.fstat(descriptor)
        if value.st_uid != 1000 or value.st_gid != 1000 or value.st_mode & 0o777 != 0o700:
            raise PhiProductionError()
        if os.fstatvfs(descriptor).f_flag & os.ST_RDONLY:
            raise PhiProductionError()
    finally:
        os.close(descriptor)


def create_phi_production_app() -> FastAPI:
    """Build the concrete PHI GET app; missing or mismatched deployment input refuses."""
    try:
        settings = PhiProductionSettings()  # type: ignore[call-arg]
        portal = PortalSettings()  # type: ignore[call-arg]
        materials = load_materials(settings)
        manifest = materials.manifest
        if (
            portal.mode != "production"
            or (portal.tenant, portal.issuer, portal.public_origin)
            != (manifest.scope.tenant, manifest.issuer, manifest.public_origin)
            or _portal_digest(portal, manifest.portal_settings_ref) != manifest.portal_settings_digest
        ):
            raise PhiProductionError()
        _quiet()
        _scratch()
        seconds = settings.maximum_seconds
        identity_engine = _engine(
            materials.identity_url,
            materials.database_ca[manifest.identity_reader.ca_file],
            seconds,
        )
        owner = IdentityOwnerClient(
            origin=manifest.identity_owner.origin,
            tenant=portal.tenant,
            identity_source_ref=manifest.identity_owner.identity_source_ref,
            server_spki_sha256=manifest.identity_owner.server_spki_sha256,
            tls_context=_owner_tls(materials),
            valid_until=manifest.identity_owner.valid_until,
            seconds=seconds,
        )
        identity = PhiIdentityReader(portal.tenant, identity_engine, owner, seconds=seconds)
        content_engine = _engine(
            materials.content_url,
            materials.database_ca[manifest.content_reader.ca_file],
            seconds,
        )
        authority_engine = _engine(
            materials.authority_url,
            materials.database_ca[manifest.authority_reader.ca_file],
            seconds,
        )
        authority = PostgresCommunicationAuthority(
            authority_engine,
            scope=manifest.scope,
            seconds=seconds,
        )
        keys = PhiContentKeys(
            scope=manifest.scope,
            active_key_id=manifest.content_keys.active_key_id,
            keys=materials.content_keys,
            valid_until=manifest.content_keys.valid_until,
        )
        content = PostgresPhiCommunicationContent(
            content_engine,
            scope=manifest.scope,
            keys=keys,
            seconds=seconds,
            admission=None,
        )

        def service(resolver: HumanSessionResolver) -> PhiCommunicationService:
            if type(resolver) is not HumanSessionResolver:
                raise PhiProductionError()
            return PhiCommunicationService(resolver, authority, content)

        app = create_phi_communication_app(
            portal,
            identity_store=identity,
            content_service_factory=service,
            read_only=True,
        )
        runtime = PhiProductionRuntime(
            identity=identity,
            content_engine=content_engine,
            authority_engine=authority_engine,
            materials=materials,
            seconds=seconds,
        )
        original = app.router.lifespan_context

        @asynccontextmanager
        async def lifespan(application: FastAPI) -> AsyncIterator[None]:
            try:
                await runtime.qualify()
                _quiet()
                async with original(application):
                    yield
            except asyncio.CancelledError:
                raise
            except Exception:
                raise PhiProductionError() from None
            finally:
                await runtime.close()

        app.router.lifespan_context = lifespan
        app.state.phi_production_runtime = runtime
        return app
    except Exception:
        raise PhiProductionError() from None

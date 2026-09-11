"""Actual staff runtime construction. Identity stays in the existing app builder."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import ssl
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import AsyncExitStack, asynccontextmanager
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from maezo.gateway.external_cases.postgres import transaction
from maezo.portal.api.config import PortalSettings

from .composition import StaffCaseRuntime
from .materials import _owned, load_materials
from .postgres import NativeMembershipSource, RelationPin, StaffSessionLease, qualify_engine
from .production_config import (
    MATERIAL_DIRECTORY,
    SCRATCH_DIRECTORY,
    Connection,
    PortalProductionSettings,
    PortalStaffBootstrapError,
)
from .publisher import StaffNativeClient, StaffWitnessSource


def _quiet() -> None:
    for name in tuple(logging.Logger.manager.loggerDict):
        if name.startswith(("sqlalchemy.engine", "sqlalchemy.pool", "asyncpg")) and logging.getLogger(
            name
        ).isEnabledFor(logging.INFO):
            raise PortalStaffBootstrapError()


def _engine(url: URL, connection: Connection, seconds: int) -> AsyncEngine:
    # DSN host equals tls_server_name; asyncpg uses that actual host for TLS SNI
    # and certificate hostname verification. No query value can override SSL.
    tls = ssl.create_default_context(cafile=str(Path(MATERIAL_DIRECTORY) / connection.ca_file))
    if (
        not tls.check_hostname
        or tls.verify_mode != ssl.CERT_REQUIRED
        or url.host != connection.tls_server_name
    ):
        raise PortalStaffBootstrapError()
    return create_async_engine(
        url,
        echo=False,
        hide_parameters=True,
        pool_size=2,
        max_overflow=0,
        pool_timeout=seconds,
        connect_args={"ssl": tls, "timeout": seconds, "command_timeout": seconds},
    )


async def _qualify_login(engine: AsyncEngine, expected: Connection, seconds: int) -> None:
    qualify_engine(engine, seconds)
    async with transaction(engine, seconds) as connection:
        row = (
            (
                await connection.execute(
                    text("""
            SELECT session_user::text AS login, current_user::text AS effective,
              current_database()::text AS database, r.rolsuper, r.rolbypassrls,
              r.rolcreaterole, r.rolcreatedb, r.rolreplication
            FROM pg_roles r WHERE r.rolname=session_user
        """)
                )
            )
            .mappings()
            .one()
        )
        if (
            row["login"] != expected.login
            or row["effective"] != expected.login
            or row["database"] != expected.database
            or any(
                row[k] for k in ("rolsuper", "rolbypassrls", "rolcreaterole", "rolcreatedb", "rolreplication")
            )
        ):
            raise PortalStaffBootstrapError()
        pin = expected.function_pin
        if pin is not None:
            # This login may execute the pinned function only. Catalog privilege
            # checks include column grants, PUBLIC/ownership and every selectable
            # role, including NOINHERIT memberships; no identity rows are read.
            function = (
                (
                    await connection.execute(
                        text("""
                SELECT p.oid::bigint AS oid, pg_get_userbyid(p.proowner) AS owner,
                  p.prosecdef, p.proconfig, pg_get_functiondef(p.oid) AS definition,
                  has_function_privilege(session_user,p.oid,'EXECUTE') AS executable,
                  pg_has_role(session_user,p.proowner,'MEMBER') AS owner_member,
                  EXISTS(SELECT 1 FROM aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) a
                         WHERE a.grantee=0 AND a.privilege_type='EXECUTE') AS public_execute,
                  EXISTS(
                    SELECT 1 FROM pg_class c
                    JOIN pg_namespace n ON n.oid=c.relnamespace
                    CROSS JOIN pg_roles candidate
                    WHERE n.nspname IN ('public','portal_identity')
                      AND c.relkind IN ('r','p','v','m','f','S')
                      AND (candidate.rolname=session_user
                           OR pg_has_role(session_user,candidate.oid,'MEMBER'))
                      AND (c.relowner=candidate.oid OR CASE WHEN c.relkind='S' THEN
                        has_sequence_privilege(candidate.oid,c.oid,'USAGE,SELECT,UPDATE')
                      ELSE
                        has_table_privilege(candidate.oid,c.oid,
                          'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER')
                        OR has_any_column_privilege(candidate.oid,c.oid,
                          'SELECT,INSERT,UPDATE,REFERENCES')
                      END)
                  ) AS data_authority
                FROM pg_proc p
                WHERE p.oid=to_regprocedure('portal_identity.lock_external_session(text)')
            """)
                    )
                )
                .mappings()
                .one()
            )
            if (
                function["oid"] != int(pin.oid)
                or function["owner"] != pin.owner
                or not function["prosecdef"]
                or not function["executable"]
                or function["owner_member"]
                or function["public_execute"]
                or function["data_authority"] is not False
                or function["proconfig"] != ["search_path=pg_catalog, portal_identity"]
                or hashlib.sha256(function["definition"].encode()).hexdigest() != pin.definition_sha256
            ):
                raise PortalStaffBootstrapError()
        # No invented human/session is submitted. Actual tenant mapping is checked
        # inside the installed lock function on every real authenticated request.


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


@asynccontextmanager
async def staff_runtime(
    settings: PortalProductionSettings, identity: PortalSettings
) -> AsyncIterator[StaffCaseRuntime]:
    try:
        if (
            settings.capabilities != "identity,staff_cases"
            or identity.mode != "production"
            or settings.tenant != identity.tenant
            or settings.issuer != identity.issuer
            or identity.database_url is None
            or settings.staff_maximum_seconds is None
        ):
            raise PortalStaffBootstrapError()
        _quiet()
        material = load_materials(settings)
        identity_url = make_url(identity.database_url.get_secret_value())
        session_url = material.session_url
        if (
            identity_url.drivername != "postgresql+asyncpg"
            or (identity_url.host, identity_url.port or 5432, identity_url.database)
            != (session_url.host, session_url.port or 5432, session_url.database)
            or identity_url.username in {session_url.username, material.witness_url.username}
        ):
            raise PortalStaffBootstrapError()
        if os.environ.get("TMPDIR") != SCRATCH_DIRECTORY:
            raise PortalStaffBootstrapError()
        scratch = os.open(SCRATCH_DIRECTORY, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            _owned(os.fstat(scratch), 0o700, directory=True)
            if os.fstatvfs(scratch).f_flag & os.ST_RDONLY:
                raise PortalStaffBootstrapError()
        finally:
            os.close(scratch)
        seconds = settings.staff_maximum_seconds
        manifest = material.manifest
        async with AsyncExitStack() as resources:
            session_engine = _engine(material.session_url, manifest.session_lock_connection, seconds)
            resources.push_async_callback(_close, session_engine.dispose, seconds)
            witness_engine = _engine(material.witness_url, manifest.native_witness_connection, seconds)
            resources.push_async_callback(_close, witness_engine.dispose, seconds)
            await _qualify_login(session_engine, manifest.session_lock_connection, seconds)
            await _qualify_login(witness_engine, manifest.native_witness_connection, seconds)
            source = NativeMembershipSource(
                witness_engine,
                manifest.scope,
                manifest.native_witness_connection.login,
                {
                    name: RelationPin(int(pin.oid), pin.owner)
                    for name, pin in manifest.native_relation_pins.items()
                },
                seconds=seconds,
            )
            witnesses = StaffWitnessSource(source, material.witness_signer)
            native = StaffNativeClient(
                origin=manifest.native_origin,
                ca_file=Path(MATERIAL_DIRECTORY) / "native-ca.pem",
                certificate_file=Path(MATERIAL_DIRECTORY) / "read-client-certificate.pem",
                private_key_file=Path(MATERIAL_DIRECTORY) / "read-client-key.pem",
                server_spki_sha256=manifest.native_server_spki_sha256,
                signer=material.read_signer,
                result_authority=material.authority,
                configuration_digest=manifest.native_configuration_digest,
                seconds=seconds,
            )
            resources.push_async_callback(_close, native.close, seconds)
            runtime = StaffCaseRuntime(
                StaffSessionLease(session_engine, identity.tenant, identity.issuer, seconds=seconds),
                witnesses,
                native,
            )
            material.read_signer.guard("staff-case-read.v1")
            material.witness_signer.guard("membership_current")
            _quiet()
            yield runtime
    except asyncio.CancelledError:
        raise
    except Exception:
        raise PortalStaffBootstrapError() from None

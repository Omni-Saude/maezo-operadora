"""Dedicated PHI PostgreSQL connection, ADR0006/0007 and ADR0049 D3/D6.

The deployment descriptor is injected by the trusted PHI composition root, NEVER
accepted from HTTP or interpreted as proof of network-zone residency. It pins actual
mTLS identity, database/role/ACL and envelope-key selection. Actual infrastructure
evidence and current authorization producers must be qualified before composition.
No ambient DSN, password, pgpass, environment credentials or general-pool fallback.
"""

from __future__ import annotations

import math
import re
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib.resources import files

import asyncpg  # type: ignore[import-untyped]
from cryptography import x509
from cryptography.hazmat.primitives import hashes

from .models import Scope
from .transport import HumanTLSIdentity


class DecisionCustodyError(RuntimeError):
    """Fixed error only; persistence outcome may be unknown. Retry the same command."""

    def __init__(self) -> None:
        super().__init__("decision custody unavailable")


class DecisionCustodyConflictError(DecisionCustodyError):
    pass


@dataclass(frozen=True, repr=False)
class PhiDatabaseDeployment:
    scope: Scope
    host: str
    port: int
    database: str
    writer_role: str
    owner_role: str
    client_certificate_sha256: str
    active_key_id: str
    readable_key_ids: tuple[str, ...]
    valid_until: datetime
    evidence_digest: str

    def __post_init__(self) -> None:
        if (
            not self.host
            or any(c.isspace() or ord(c) < 32 for c in self.host)
            or any(c in self.host for c in "/@?#")
            or type(self.port) is not int
            or not 1 <= self.port <= 65535
            or self.writer_role == self.owner_role
            or any(
                not re.fullmatch(r"[a-z][a-z0-9_]{0,62}", value)
                for value in (self.database, self.writer_role, self.owner_role)
            )
            or any(
                not re.fullmatch(r"[0-9a-f]{64}", value)
                for value in (self.client_certificate_sha256, self.evidence_digest)
            )
            or self.active_key_id not in self.readable_key_ids
            or len(set(self.readable_key_ids)) != len(self.readable_key_ids)
            or any(not re.fullmatch(r"[A-Za-z0-9_.:@-]{1,255}", k) for k in self.readable_key_ids)
            or self.valid_until.tzinfo is None
        ):
            raise DecisionCustodyError()


_IDENTITY = """
SELECT current_database() AS database, current_user AS role,
       r.rolsuper, r.rolbypassrls, r.rolcreaterole, r.rolcreatedb, r.rolreplication,
       s.ssl, coalesce(length(s.client_dn)>0, false) AS client_certificate,
       pg_get_userbyid(n.nspowner) AS schema_owner,
       pg_get_userbyid(c.relowner) AS table_owner,
       pg_has_role(current_user, $1, 'MEMBER') AS owner_member,
       has_schema_privilege(current_user, 'maezo_phi_decision', 'USAGE') AS usage,
       has_schema_privilege(current_user, 'maezo_phi_decision', 'CREATE') AS schema_create,
       has_database_privilege(current_user, current_database(), 'CREATE') AS database_create,
       has_table_privilege(current_user, c.oid, 'SELECT') AS can_select,
       has_table_privilege(current_user, c.oid, 'INSERT') AS can_insert,
       has_table_privilege(current_user, c.oid, 'UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER') AS can_mutate,
       EXISTS (SELECT 1 FROM pg_auth_members m WHERE m.member=r.oid) AS role_memberships,
       EXISTS (
           SELECT 1 FROM pg_class other JOIN pg_namespace ns ON ns.oid=other.relnamespace
           WHERE other.oid<>c.oid AND other.relkind IN ('r','p','v','m','f')
           AND ns.nspname NOT IN ('pg_catalog','information_schema')
           AND ns.nspname NOT LIKE 'pg_toast%'
           AND has_table_privilege(current_user, other.oid,
                                   'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER')
       ) AS other_data_access
FROM pg_roles r JOIN pg_stat_ssl s ON s.pid=pg_backend_pid()
JOIN pg_namespace n ON n.nspname='maezo_phi_decision'
JOIN pg_class c ON c.relnamespace=n.oid AND c.relname='human_decision'
WHERE r.rolname=current_user
"""


class PhiPostgresConnection:
    """Constructs a fresh authenticated connection per operation; no injected pool.

    Expiry is deployment assurance, not retention. Certificate trust and network-zone
    evidence belong to deployment authority; SQL cannot establish geographic residency.
    """

    def __init__(
        self,
        *,
        deployment: PhiDatabaseDeployment,
        identity: HumanTLSIdentity,
        timeout_seconds: float,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        if (
            deployment.scope != identity.scope
            or type(timeout_seconds) not in (int, float)
            or not math.isfinite(timeout_seconds)
            or timeout_seconds <= 0
        ):
            raise DecisionCustodyError()
        self.deployment = deployment
        self._identity = identity
        self._timeout = timeout_seconds
        self._clock = clock

    def current(self) -> datetime:
        if self._clock() >= self.deployment.valid_until:
            raise DecisionCustodyError()
        return self.deployment.valid_until

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[asyncpg.Connection]:
        connection = None
        try:
            self.current()
            tls = self._identity.context()
            certificate = x509.load_pem_x509_certificate(self._identity.certificate_file.read_bytes())
            if certificate.fingerprint(hashes.SHA256()).hex() != self.deployment.client_certificate_sha256:
                raise DecisionCustodyError()
            connection = await asyncpg.connect(
                host=self.deployment.host,
                port=self.deployment.port,
                database=self.deployment.database,
                user=self.deployment.writer_role,
                password="",
                passfile="/dev/null",
                ssl=tls,
                direct_tls=False,
                timeout=self._timeout,
                command_timeout=self._timeout,
                server_settings={"application_name": "maezo-phi-decision", "search_path": "pg_catalog"},
            )
            async with connection.transaction(isolation="read_committed"):
                row = await connection.fetchrow(_IDENTITY, self.deployment.owner_role)
                if (
                    row is None
                    or row["database"] != self.deployment.database
                    or row["role"] != self.deployment.writer_role
                    or row["schema_owner"] != self.deployment.owner_role
                    or row["table_owner"] != self.deployment.owner_role
                    or any(
                        row[k] is not False
                        for k in (
                            "rolsuper",
                            "rolbypassrls",
                            "rolcreaterole",
                            "rolcreatedb",
                            "rolreplication",
                            "owner_member",
                            "schema_create",
                            "database_create",
                            "can_mutate",
                            "role_memberships",
                            "other_data_access",
                        )
                    )
                    or any(
                        row[k] is not True
                        for k in ("ssl", "client_certificate", "usage", "can_select", "can_insert")
                    )
                ):
                    raise DecisionCustodyError()
                self.current()
                yield connection
                self.current()
            self.current()
        except DecisionCustodyError:
            raise
        except Exception:
            raise DecisionCustodyError() from None
        finally:
            if connection is not None:
                # A close failure after COMMIT is unknown to the caller, never rollback.
                try:
                    await connection.close(timeout=self._timeout)
                except Exception:
                    raise DecisionCustodyError() from None


async def install_phi_decision_schema(connection: asyncpg.Connection, *, writer_role: str) -> None:
    """Explicit DBA installation on a dedicated PHI DB, outside General Alembic.

    Roles and certificate authentication must already be provisioned. Never invoked by
    runtime. Installation is forward-only and refuses an existing schema.
    """
    try:
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,62}", writer_role):
            raise DecisionCustodyError()
        async with connection.transaction():
            if await connection.fetchval("SELECT current_user") == writer_role:
                raise DecisionCustodyError()
            await connection.execute(files(__package__).joinpath("decision_custody_schema.sql").read_text())
            await connection.execute(f'GRANT USAGE ON SCHEMA maezo_phi_decision TO "{writer_role}"')
            await connection.execute(
                f'GRANT SELECT, INSERT ON maezo_phi_decision.human_decision TO "{writer_role}"'
            )
    except Exception:
        raise DecisionCustodyError() from None

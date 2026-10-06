"""VW1-P0 vendor transport over the GENERIC live authority path; never a second identity feed.

The reviewed vendor act is carried by `portal_auth.apply_change` (the generic source-change
function, `gateway/intake/native-authority-postgres.sql`) under the tenant's writer login. The
freeze/prepare of the change belongs to the native-source lifecycle plane (PW1-D — consumed,
never edited); this adapter only COMPLETES that path: it qualifies the session against the
pinned writer credential, executes the one generic call, and never issues a write of its own.
Revocation rides INSIDE the frozen payload as a new record with `revoked=true`
(`portal/api/records.py`), never as a DELETE. Any refusal — unavailable source, unproven
credential, conflict — surfaces as the typed `VendorAdministrationError`, with cause text
sanitized away. Mirrors `provider_membership_administration_postgres.py` (PW1-B) posture; that
module stays a read-only molde.
"""

from __future__ import annotations

import secrets
from datetime import datetime
from typing import Annotated, Literal

from pydantic import Field, StringConstraints
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from maezo.gateway.external_cases.postgres import transaction
from maezo.gateway.human.vendor_membership_administration import (
    AdministrationBinding,
    Closed,
    VendorAdministrationError,
    VendorAdministrationReason,
    VendorAdministrationReceipt,
    VendorMembershipCommand,
    command_bytes,
    command_digest,
    require,
)
from maezo.portal.contracts.models import OpaqueRef

Identifier = Annotated[str, StringConstraints(strict=True, pattern=r"^[a-z][a-z0-9_]{0,62}$")]
APPLY_CHANGE = "portal_auth.apply_change"


class VendorSourceDescriptor(Closed):
    """Deployment-pinned identity of the generic authority path for one tenant.

    `writer_role` is the login `portal_auth.apply_change` itself enforces (per-tenant
    installation); `accreditation_receipt_ref` pins the installation receipt of the migration-0019
    vendor channel store, cited by every receipt as the relationship evidence.
    """

    schema_version: Literal["vendor-membership-administration-source.v1"]
    tenant: OpaqueRef
    database_name: Identifier
    database_oid: int = Field(gt=0)
    writer_role: Identifier
    source_authority_ref: OpaqueRef
    accreditation_receipt_ref: OpaqueRef
    valid_until: datetime


class PostgresVendorMembershipAdministration:
    """The vendor `AdministrationStore`: one generic `apply_change` call, zero writes of its own."""

    def __init__(self, engine: AsyncEngine, descriptor: VendorSourceDescriptor) -> None:
        require(
            engine.dialect.name == "postgresql" and not engine.echo and engine.sync_engine.hide_parameters,
            VendorAdministrationReason.SOURCE_UNAVAILABLE,
        )
        self.engine = engine
        self.descriptor = VendorSourceDescriptor.model_validate(descriptor.model_dump(mode="python"))

    async def _qualify(self, db: AsyncConnection, binding: AdministrationBinding) -> None:
        d = self.descriptor
        require(
            not self.engine.echo and self.engine.sync_engine.hide_parameters,
            VendorAdministrationReason.SOURCE_UNAVAILABLE,
        )
        row = (
            (
                await db.execute(
                    text("""
            SELECT current_database() AS db,session_user::text AS login,current_user::text AS actor,
            clock_timestamp() AS now,(SELECT oid FROM pg_database WHERE datname=current_database()) AS db_oid,
            pg_my_temp_schema() AS temp_oid,
            COALESCE((SELECT ssl FROM pg_stat_ssl WHERE pid=pg_backend_pid()),false) AS tls
        """)
                )
            )
            .mappings()
            .one()
        )
        require(
            (row["db"], row["db_oid"], row["login"], row["actor"])
            == (d.database_name, d.database_oid, d.writer_role, d.writer_role)
            and row["tls"] is True
            and row["temp_oid"] == 0
            and (binding.tenant, binding.source_authority_ref) == (d.tenant, d.source_authority_ref)
            and row["now"] < min(d.valid_until, binding.valid_until),
            VendorAdministrationReason.SOURCE_UNAVAILABLE,
        )
        flags = (
            (
                await db.execute(
                    text("""
            SELECT rolsuper,rolcreaterole,rolcreatedb,rolbypassrls,rolreplication,rolcanlogin,
            EXISTS(SELECT 1 FROM pg_auth_members WHERE member=r.oid) AS membership,
            has_database_privilege(r.oid,current_database(),'TEMP') AS temp
            FROM pg_roles r WHERE rolname=:role
        """),
                    dict(role=d.writer_role),
                )
            )
            .mappings()
            .one_or_none()
        )
        require(flags is not None, VendorAdministrationReason.SOURCE_UNAVAILABLE)
        assert flags is not None
        require(
            not any(
                flags[k]
                for k in (
                    "rolsuper",
                    "rolcreaterole",
                    "rolcreatedb",
                    "rolbypassrls",
                    "rolreplication",
                    "membership",
                )
            )
            and flags["rolcanlogin"] is True
            and not flags["temp"],
            VendorAdministrationReason.SOURCE_UNAVAILABLE,
        )

    async def record(
        self, command: VendorMembershipCommand, binding: AdministrationBinding
    ) -> VendorAdministrationReceipt:
        """Complete the generic path for the change frozen under `command.command_id`.

        The canonical payload (`command_bytes`) — a revoke carrying `record.revoked=true` inside —
        was frozen into the source change by the preparation plane; here only the credential-bound
        completion runs. No INSERT/UPDATE/DELETE is ever issued by this adapter.
        """
        try:
            command = VendorMembershipCommand.model_validate(command.model_dump(mode="python"))
            binding = AdministrationBinding.model_validate(binding.model_dump(mode="python"))
            require(command.record.tenant == binding.tenant, VendorAdministrationReason.CONTRACT_MISMATCH)
            command_bytes(command)  # canonical codec proven before any I/O
            act, audit = secrets.token_urlsafe(24), secrets.token_urlsafe(24)
            async with transaction(self.engine, 5) as db:
                await self._qualify(db, binding)
                await db.execute(
                    text(f"SELECT {APPLY_CHANGE}(:tenant,:id)"),
                    {"tenant": binding.tenant, "id": command.command_id},
                )
                committed_at = (
                    (await db.execute(text("SELECT clock_timestamp() AS now"))).mappings().one()["now"]
                )
            return VendorAdministrationReceipt(
                schema_version="vendor-membership-administration-receipt.v1",
                tenant=binding.tenant,
                command_id=command.command_id,
                request_digest=command_digest(command),
                principal_ref=command.record.principal_ref,
                channel_ref=command.channel_ref,
                membership_revision=command.record.revision,
                administrative_act_ref=act,
                authority_receipt_ref=binding.source_authority_ref,
                relationship_receipt_ref=self.descriptor.accreditation_receipt_ref,
                audit_receipt_ref=audit,
                committed_at=committed_at,
                proof_state="validated",
                application_status="pending",
            )
        except VendorAdministrationError:
            raise
        except Exception:
            raise VendorAdministrationError(VendorAdministrationReason.SOURCE_UNAVAILABLE) from None

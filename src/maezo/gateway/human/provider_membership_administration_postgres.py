"""Source-owner function transaction; application login has EXECUTE, never table writes."""

from __future__ import annotations

import hashlib
import secrets
from datetime import datetime
from typing import Annotated, Literal

from pydantic import Field, StringConstraints, model_validator
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from maezo.gateway.human.provider_membership_administration import (
    AdministrationBinding,
    AdministrationReason,
    Closed,
    ProviderAdministrationError,
    ProviderAdministrationReceipt,
    ProviderMembershipCommand,
    command_bytes,
    require,
)
from maezo.portal.contracts.models import OpaqueRef, Sha256Digest

Identifier = Annotated[str, StringConstraints(strict=True, pattern=r"^[a-z][a-z0-9_]{0,62}$")]
RELATIONS = frozenset(
    {
        "administrator_authority",
        "provider_relationship",
        "administrative_head",
        "administrative_act",
        "administrative_audit",
    }
)
FUNCTION_TYPES = "text,text,text,text,text,text,timestamp with time zone,text,text,text"


class AdministrationRelationPin(Closed):
    name: Identifier
    oid: int = Field(gt=0)
    owner: Identifier


class AdministrationSourceDescriptor(Closed):
    schema_version: Literal["provider-membership-administration-source.v1"]
    tenant: OpaqueRef
    database_name: Identifier
    database_oid: int = Field(gt=0)
    schema_name: Identifier
    schema_oid: int = Field(gt=0)
    owner_role: Identifier
    writer_role: Identifier
    authority_publisher_role: Identifier
    source_authority_ref: OpaqueRef
    installation_receipt_ref: OpaqueRef
    valid_until: datetime
    relations: tuple[AdministrationRelationPin, ...]
    function_oid: int = Field(gt=0)
    function_definition_digest: Sha256Digest

    @model_validator(mode="after")
    def exact_pins(self) -> AdministrationSourceDescriptor:
        require(
            self.schema_name not in {"public", "maezo_native", "cibseven", "portal_auth"}
            and len({self.owner_role, self.writer_role, self.authority_publisher_role}) == 3
            and len(self.relations) == len(RELATIONS)
            and {p.name for p in self.relations} == RELATIONS
            and len({p.oid for p in self.relations}) == len(RELATIONS)
            and all(p.owner == self.owner_role for p in self.relations),
            AdministrationReason.CONTRACT_MISMATCH,
        )
        AdministrationBinding.aware(self.valid_until)
        return self


class PostgresProviderMembershipAdministration:
    def __init__(self, engine: AsyncEngine, descriptor: AdministrationSourceDescriptor) -> None:
        require(engine.dialect.name == "postgresql", AdministrationReason.SOURCE_UNAVAILABLE)
        self.engine = engine
        self.descriptor = AdministrationSourceDescriptor.model_validate(descriptor.model_dump(mode="python"))

    async def _qualify(self, db: AsyncConnection, binding: AdministrationBinding) -> None:
        d = self.descriptor
        row = (
            (
                await db.execute(
                    text(
                        "SELECT current_database() AS db, session_user AS login, current_user AS actor, "
                        "clock_timestamp() AS now, (SELECT oid FROM pg_database WHERE "
                        "datname=current_database()) AS db_oid"
                    )
                )
            )
            .mappings()
            .one()
        )
        require(
            (row["db"], row["db_oid"], row["login"], row["actor"])
            == (d.database_name, d.database_oid, d.writer_role, d.writer_role)
            and (binding.tenant, binding.source_authority_ref) == (d.tenant, d.source_authority_ref)
            and row["now"] < min(d.valid_until, binding.valid_until),
            AdministrationReason.SOURCE_UNAVAILABLE,
        )
        schema = (
            (
                await db.execute(
                    text(
                        "SELECT n.oid,r.rolname AS owner,"
                        "has_schema_privilege(session_user,n.oid,'CREATE') AS "
                        "can_create FROM pg_namespace n JOIN pg_roles r ON r.oid=n.nspowner WHERE "
                        "n.nspname=:schema"
                    ),
                    {"schema": d.schema_name},
                )
            )
            .mappings()
            .one_or_none()
        )
        require(schema is not None, AdministrationReason.SOURCE_UNAVAILABLE)
        assert schema is not None
        require(
            (schema["oid"], schema["owner"], schema["can_create"]) == (d.schema_oid, d.owner_role, False),
            AdministrationReason.SOURCE_UNAVAILABLE,
        )
        flags = (
            (
                await db.execute(
                    text(
                        "SELECT "
                        "rolsuper,rolcreaterole,rolcreatedb,rolbypassrls,pg_has_role(session_user,:owner,'MEMBER')"
                        " AS owner_member,pg_has_role(session_user,:publisher,'MEMBER') AS publisher_member "
                        "FROM pg_roles WHERE rolname=session_user"
                    ),
                    {"owner": d.owner_role, "publisher": d.authority_publisher_role},
                )
            )
            .mappings()
            .one()
        )
        require(not any(flags.values()), AdministrationReason.SOURCE_UNAVAILABLE)
        for pin in d.relations:
            r = (
                (
                    await db.execute(
                        text(
                            "SELECT c.oid,r.rolname AS owner,c.relkind,EXISTS(SELECT 1 FROM "
                            "aclexplode(coalesce(c.relacl,acldefault('r',c.relowner))) a "
                            "WHERE a.grantee=0) AS "
                            "public_acl,has_table_privilege(session_user,c.oid,'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER')"
                            " AS any_access FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace JOIN "
                            "pg_roles r ON r.oid=c.relowner WHERE n.nspname=:schema AND c.relname=:name"
                        ),
                        {"schema": d.schema_name, "name": pin.name},
                    )
                )
                .mappings()
                .one_or_none()
            )
            require(r is not None, AdministrationReason.SOURCE_UNAVAILABLE)
            assert r is not None
            require(
                (r["oid"], r["owner"], r["relkind"], r["public_acl"], r["any_access"])
                == (pin.oid, pin.owner, "r", False, False),
                AdministrationReason.SOURCE_UNAVAILABLE,
            )
        function = (
            (
                await db.execute(
                    text(
                        "SELECT p.oid,r.rolname AS owner,p.prosecdef,pg_get_functiondef(p.oid) AS "
                        "body,EXISTS(SELECT 1 FROM "
                        "aclexplode(coalesce(p.proacl,acldefault('f',p.proowner))) a"
                        " WHERE a.grantee=0) AS "
                        "public_acl,has_function_privilege(session_user,p.oid,'EXECUTE') AS can_execute FROM "
                        "pg_proc p JOIN pg_roles r ON r.oid=p.proowner "
                        "WHERE p.oid=to_regprocedure(:signature)"
                    ),
                    {"signature": f"{d.schema_name}.record_provider_administration({FUNCTION_TYPES})"},
                )
            )
            .mappings()
            .one_or_none()
        )
        require(function is not None, AdministrationReason.SOURCE_UNAVAILABLE)
        assert function is not None
        require(
            (
                function["oid"],
                function["owner"],
                function["prosecdef"],
                function["public_acl"],
                function["can_execute"],
            )
            == (d.function_oid, d.owner_role, True, False, True)
            and hashlib.sha256(function["body"].encode()).hexdigest() == d.function_definition_digest,
            AdministrationReason.SOURCE_UNAVAILABLE,
        )

    async def record(
        self, command: ProviderMembershipCommand, binding: AdministrationBinding
    ) -> ProviderAdministrationReceipt:
        try:
            command = ProviderMembershipCommand.model_validate(command.model_dump(mode="python"))
            binding = AdministrationBinding.model_validate(binding.model_dump(mode="python"))
            require(command.record.tenant == binding.tenant, AdministrationReason.CONTRACT_MISMATCH)
            raw = command_bytes(command).decode()
            async with self.engine.begin() as db:
                await self._qualify(db, binding)
                outcome = (
                    await db.execute(
                        text(
                            f'SELECT "{self.descriptor.schema_name}".record_provider_administration('
                            ":tenant,:actor,:session,:source,:policy,:install,:until,:payload,:act,:audit)"
                        ),
                        {
                            "tenant": binding.tenant,
                            "actor": binding.actor_ref,
                            "session": binding.actor_session_ref,
                            "source": binding.source_authority_ref,
                            "policy": binding.policy_revision,
                            "install": self.descriptor.installation_receipt_ref,
                            "until": min(binding.valid_until, self.descriptor.valid_until),
                            "payload": raw,
                            "act": secrets.token_urlsafe(24),
                            "audit": secrets.token_urlsafe(24),
                        },
                    )
                ).scalar_one()
                await self._qualify(db, binding)
                if outcome in {reason.value for reason in AdministrationReason}:
                    raise ProviderAdministrationError(AdministrationReason(outcome))
                return ProviderAdministrationReceipt.model_validate_json(outcome)
        except ProviderAdministrationError:
            raise
        except Exception:
            raise ProviderAdministrationError(AdministrationReason.SOURCE_UNAVAILABLE) from None

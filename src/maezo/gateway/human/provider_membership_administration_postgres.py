"""Source-owner function transaction; application login has EXECUTE, never table writes."""

from __future__ import annotations

import hashlib
import secrets
from datetime import datetime
from typing import Annotated, Literal

from pydantic import Field, StringConstraints, model_validator
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from maezo.gateway.external_cases.models import ExternalCaseError
from maezo.gateway.external_cases.postgres import transaction
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
        require(
            engine.dialect.name == "postgresql" and not engine.echo and engine.sync_engine.hide_parameters,
            AdministrationReason.SOURCE_UNAVAILABLE,
        )
        self.engine = engine
        self.descriptor = AdministrationSourceDescriptor.model_validate(descriptor.model_dump(mode="python"))

    async def _qualify(self, db: AsyncConnection, binding: AdministrationBinding) -> None:
        d = self.descriptor
        require(
            not self.engine.echo and self.engine.sync_engine.hide_parameters,
            AdministrationReason.SOURCE_UNAVAILABLE,
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
            AdministrationReason.SOURCE_UNAVAILABLE,
        )
        schema = (
            (
                await db.execute(
                    text("""
            SELECT n.oid,pg_get_userbyid(n.nspowner) AS owner,
            EXISTS(SELECT 1 FROM aclexplode(COALESCE(n.nspacl,acldefault('n',n.nspowner))) a
                WHERE a.grantee=0 OR (a.grantee<>n.nspowner AND a.grantee NOT IN
                (SELECT oid FROM pg_roles WHERE rolname IN (:writer,:publisher)))) AS extra_acl,
            EXISTS(SELECT 1 FROM aclexplode(COALESCE(n.nspacl,acldefault('n',n.nspowner))) a
                WHERE a.grantee<>n.nspowner AND a.is_grantable) AS grant_options,
            has_schema_privilege(:writer,n.oid,'CREATE') AS writer_create,
            has_schema_privilege(:publisher,n.oid,'CREATE') AS publisher_create,
            has_schema_privilege(:writer,n.oid,'USAGE') AS writer_usage,
            has_schema_privilege(:publisher,n.oid,'USAGE') AS publisher_usage
            FROM pg_namespace n WHERE n.nspname=:schema
        """),
                    dict(schema=d.schema_name, writer=d.writer_role, publisher=d.authority_publisher_role),
                )
            )
            .mappings()
            .one_or_none()
        )
        require(schema is not None, AdministrationReason.SOURCE_UNAVAILABLE)
        assert schema is not None
        require(
            (schema["oid"], schema["owner"]) == (d.schema_oid, d.owner_role)
            and not schema["extra_acl"]
            and not schema["grant_options"]
            and not schema["writer_create"]
            and not schema["publisher_create"]
            and schema["writer_usage"]
            and schema["publisher_usage"],
            AdministrationReason.SOURCE_UNAVAILABLE,
        )
        for role in (d.owner_role, d.writer_role, d.authority_publisher_role):
            flags = (
                (
                    await db.execute(
                        text("""
                SELECT rolsuper,rolcreaterole,rolcreatedb,rolbypassrls,rolreplication,rolinherit,rolcanlogin,
                EXISTS(SELECT 1 FROM pg_auth_members WHERE member=r.oid) AS membership,
                has_database_privilege(r.oid,current_database(),'TEMP') AS temp
                FROM pg_roles r WHERE rolname=:role
            """),
                        dict(role=role),
                    )
                )
                .mappings()
                .one_or_none()
            )
            require(flags is not None, AdministrationReason.SOURCE_UNAVAILABLE)
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
                        "rolinherit",
                        "membership",
                    )
                )
                and (role != d.owner_role or flags["rolcanlogin"] is False)
                and (role != d.writer_role or flags["rolcanlogin"] is True)
                and (role == d.owner_role or not flags["temp"]),
                AdministrationReason.SOURCE_UNAVAILABLE,
            )
        for pin in d.relations:
            relation = (
                (
                    await db.execute(
                        text("""
                SELECT c.oid,pg_get_userbyid(c.relowner) AS owner,c.relkind::text AS kind,
                c.relrowsecurity,c.relforcerowsecurity,
                has_table_privilege(session_user,c.oid,
                    'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER') AS access,
                has_any_column_privilege(session_user,c.oid,
                    'SELECT,INSERT,UPDATE,REFERENCES') AS column_access,
                EXISTS(SELECT 1 FROM pg_attribute at, LATERAL aclexplode(at.attacl) a
                       WHERE at.attrelid=c.oid) AS column_acl
                FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
                WHERE n.nspname=:schema AND c.relname=:name
            """),
                        dict(schema=d.schema_name, name=pin.name),
                    )
                )
                .mappings()
                .one_or_none()
            )
            require(relation is not None, AdministrationReason.SOURCE_UNAVAILABLE)
            assert relation is not None
            require(
                (relation["oid"], relation["owner"], relation["kind"]) == (pin.oid, pin.owner, "r")
                and not any(
                    relation[k]
                    for k in (
                        "access",
                        "column_access",
                        "column_acl",
                        "relrowsecurity",
                        "relforcerowsecurity",
                    )
                ),
                AdministrationReason.SOURCE_UNAVAILABLE,
            )
            grants = (
                (
                    await db.execute(
                        text("""
                SELECT a.grantee,pg_get_userbyid(a.grantee) AS grantee_name,a.privilege_type,a.is_grantable
                FROM pg_class c,LATERAL aclexplode(COALESCE(c.relacl,acldefault('r',c.relowner))) a
                WHERE c.oid=:oid AND a.grantee<>c.relowner
            """),
                        dict(oid=pin.oid),
                    )
                )
                .mappings()
                .all()
            )
            expected = (
                {(d.authority_publisher_role, p, False) for p in ("SELECT", "INSERT", "UPDATE")}
                if (pin.name in {"administrator_authority", "provider_relationship"})
                else set()
            )
            actual = {(g["grantee_name"], g["privilege_type"], g["is_grantable"]) for g in grants}
            require(
                all(g["grantee"] != 0 for g in grants) and actual == expected,
                AdministrationReason.SOURCE_UNAVAILABLE,
            )
        function = (
            (
                await db.execute(
                    text("""
            SELECT p.oid,pg_get_userbyid(p.proowner) AS owner,p.prosecdef,p.proconfig,
            pg_get_functiondef(p.oid) AS body,
            has_function_privilege(session_user,p.oid,'EXECUTE') AS can_execute
            FROM pg_proc p WHERE p.oid=to_regprocedure(:signature)
        """),
                    dict(signature=f"{d.schema_name}.record_provider_administration({FUNCTION_TYPES})"),
                )
            )
            .mappings()
            .one_or_none()
        )
        require(function is not None, AdministrationReason.SOURCE_UNAVAILABLE)
        assert function is not None
        require(
            (function["oid"], function["owner"], function["prosecdef"], function["can_execute"])
            == (d.function_oid, d.owner_role, True, True)
            and function["proconfig"] == ["search_path=pg_catalog"]
            and hashlib.sha256(function["body"].encode()).hexdigest() == d.function_definition_digest,
            AdministrationReason.SOURCE_UNAVAILABLE,
        )
        grants = (
            (
                await db.execute(
                    text("""
            SELECT a.grantee,pg_get_userbyid(a.grantee) AS grantee_name,a.privilege_type,a.is_grantable
            FROM pg_proc p,LATERAL aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) a
            WHERE p.oid=:oid AND a.grantee<>p.proowner
        """),
                    dict(oid=d.function_oid),
                )
            )
            .mappings()
            .all()
        )
        require(
            all(g["grantee"] != 0 for g in grants)
            and {(g["grantee_name"], g["privilege_type"], g["is_grantable"]) for g in grants}
            == {(d.writer_role, "EXECUTE", False)},
            AdministrationReason.SOURCE_UNAVAILABLE,
        )

    async def record(
        self, command: ProviderMembershipCommand, binding: AdministrationBinding
    ) -> ProviderAdministrationReceipt:
        failure = None
        try:
            command = ProviderMembershipCommand.model_validate(command.model_dump(mode="python"))
            binding = AdministrationBinding.model_validate(binding.model_dump(mode="python"))
            require(command.record.tenant == binding.tenant, AdministrationReason.CONTRACT_MISMATCH)
            raw = command_bytes(command).decode()
            require(
                not self.engine.echo and self.engine.sync_engine.hide_parameters,
                AdministrationReason.SOURCE_UNAVAILABLE,
            )
            async with transaction(self.engine, 5) as db:
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
                    failure = AdministrationReason(outcome)
                    raise ExternalCaseError("denied")
                receipt = ProviderAdministrationReceipt.model_validate_json(outcome)
            return receipt
        except ProviderAdministrationError:
            raise
        except ExternalCaseError as exc:
            if exc.code == "denied" and failure is not None:
                raise ProviderAdministrationError(failure) from None
            raise ProviderAdministrationError(AdministrationReason.SOURCE_UNAVAILABLE) from None
        except Exception:
            raise ProviderAdministrationError(AdministrationReason.SOURCE_UNAVAILABLE) from None

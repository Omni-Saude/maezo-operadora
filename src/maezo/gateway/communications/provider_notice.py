"""OP09 PostgreSQL notice journal and authenticated provider acknowledgements.

No authority publication, credentials, DDL or human session is fabricated here.
Preparing a notice is metadata availability. Only the recipient's explicit,
current authenticated acknowledgement creates its delivery receipt.
"""

from __future__ import annotations

import hashlib
import json
import secrets
from collections.abc import Callable
from datetime import datetime
from typing import Annotated, Any, Literal, Protocol, Self

from pydantic import Field, StringConstraints, TypeAdapter, field_validator, model_validator
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from maezo.gateway.capabilities.admission import AdmissionBinding
from maezo.gateway.capabilities.models import (
    PROVIDER_SCHEMA_VERSION,
    AnyCapabilityEnvelope,
    CandidateDTO,
    CapabilityContractError,
    CapabilityRefusalReason,
    DeliveryEvidenceReceipt,
    NoticeIntent,
    parse_envelope,
    request_digest,
)
from maezo.gateway.external_cases.models import ExternalCaseError
from maezo.gateway.external_cases.postgres import sql_failure, transaction
from maezo.portal.api.auth import AuthenticationError
from maezo.portal.api.auth import digest as secret_digest
from maezo.portal.api.session import HumanSessionResolver, ResolvedHumanSession
from maezo.portal.contracts.communications import (
    ProviderNoticeAcknowledgement,
    ProviderNoticeReceipt,
    ProviderNoticeSummary,
)
from maezo.portal.contracts.intake import Closed, ResourceRef
from maezo.portal.contracts.models import OpaqueRef, Sha256Digest

from .admission import PostgresCommunicationAdmission
from .models import CommunicationScope, alive, fingerprint

Identifier = Annotated[str, StringConstraints(strict=True, pattern=r"^[a-z][a-z0-9_]{0,62}$")]
NOTICE_TABLES = frozenset({"authority", "notice", "acknowledgement", "audit", "outbox"})
NOTICE_FUNCTIONS = {
    "prepare_notice": "jsonb,jsonb,text,text,text,text,timestamp with time zone",
    "acknowledge_notice": "text,text,text,text,text,text,text,text,text,text,timestamp with time zone",
    "inspect_notice": "text,text,text,text,timestamp with time zone",
    "recipient_authority": "text,text,text,text,timestamp with time zone",
    "immutable_history": "",
    "protect_authority": "",
}


class FunctionPin(Closed):
    name: Literal[
        "prepare_notice",
        "acknowledge_notice",
        "inspect_notice",
        "recipient_authority",
        "immutable_history",
        "protect_authority",
    ]
    oid: int = Field(gt=0)
    sha256: Sha256Digest


class NoticeRelationPin(Closed):
    name: Literal["authority", "notice", "acknowledgement", "audit", "outbox"]
    oid: int = Field(gt=0)


class ProviderNoticeInstallation(Closed):
    """Deployment-owned pins, never an operation payload or an installation proof."""

    schema_version: Literal["provider-notice-installation.v1"]
    scope: CommunicationScope
    database_oid: int = Field(gt=0)
    schema_name: Identifier
    schema_oid: int = Field(gt=0)
    owner_role: Identifier
    authority_validator_role: Identifier
    producer_role: Identifier
    recipient_role: Identifier
    installation_receipt_ref: OpaqueRef
    identity_source_ref: OpaqueRef
    identity_installation_receipt_ref: OpaqueRef
    content_relation_oid: int = Field(gt=0)
    content_owner_role: Identifier
    session_lock_oid: int = Field(gt=0)
    session_lock_owner_role: Identifier
    session_lock_sha256: Sha256Digest
    relations: tuple[NoticeRelationPin, ...]
    functions: tuple[FunctionPin, ...]
    valid_until: datetime

    @field_validator("valid_until")
    @classmethod
    def aware(cls, value: datetime) -> datetime:
        if value.utcoffset() is None:
            raise ValueError("aware installation ceiling required")
        return value

    @model_validator(mode="after")
    def separated(self) -> Self:
        if (
            self.schema_name in {"public", "maezo_native", "cibseven", "portal_auth", "portal_communication"}
            or len({self.owner_role, self.authority_validator_role, self.producer_role, self.recipient_role})
            != 4
            or not {self.content_owner_role, self.session_lock_owner_role}.isdisjoint(
                {
                    self.owner_role,
                    self.authority_validator_role,
                    self.producer_role,
                    self.recipient_role,
                }
            )
            or {p.name for p in self.relations} != NOTICE_TABLES
            or len(self.relations) != len(NOTICE_TABLES)
            or len({p.oid for p in self.relations}) != len(NOTICE_TABLES)
            or {p.name for p in self.functions} != set(NOTICE_FUNCTIONS)
            or len(self.functions) != len(NOTICE_FUNCTIONS)
            or len({p.oid for p in self.functions}) != len(NOTICE_FUNCTIONS)
        ):
            raise ValueError("exact source pins and separate owners required")
        return self


class ProviderNoticeScope(Closed):
    tenant_ref: OpaqueRef
    environment: OpaqueRef
    legal_entity_ref: OpaqueRef
    principal_ref: OpaqueRef
    task_ref: OpaqueRef
    purpose_ref: OpaqueRef
    source_authority_ref: OpaqueRef
    policy_revision: OpaqueRef
    contract_revision: OpaqueRef
    data_classification: OpaqueRef
    business_revision: OpaqueRef
    producer_role: Identifier


class NoticeRecipientObservation(Closed):
    """Server-only source ceiling. Never accepted from browser or projected on its wire."""

    result: ProviderNoticeReceipt | ProviderNoticeSummary
    valid_until: datetime

    @field_validator("valid_until")
    @classmethod
    def aware(cls, value: datetime) -> datetime:
        return ProviderNoticeInstallation.aware(value)


def _json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def _installation_digest(value: ProviderNoticeInstallation) -> str:
    # Typed installation metadata owns integer OIDs. Human/operation wire stays
    # on its existing number-free canonical profile; no values are stringified.
    return hashlib.sha256(_json(value.model_dump(mode="json")).encode("utf-8")).hexdigest()


def _notice_intent(request: CandidateDTO) -> NoticeIntent:
    try:
        intent = NoticeIntent.model_validate_json(request.model_dump_json())
        TypeAdapter(ResourceRef).validate_python(intent.notice_ref)
        TypeAdapter(ResourceRef).validate_python(intent.authorized_content_ref)
        return intent
    except Exception:
        raise CapabilityContractError(CapabilityRefusalReason.CONTRACT_MISMATCH) from None


def require_provider_delivery(result: DeliveryEvidenceReceipt) -> str:
    """Necessary receipt check, never a source-currentness or domain-authority grant.

    Consumers must obtain this result through the qualified source/admission. A
    successful metadata operation or a cached/browser DTO cannot close an obligation.
    """
    parsed = DeliveryEvidenceReceipt.model_validate_json(result.model_dump_json())
    if parsed.delivery_status != "delivered" or parsed.provider_delivery_receipt_ref is None:
        raise CapabilityContractError(CapabilityRefusalReason.AUTHORITY_UNPROVEN)
    return parsed.provider_delivery_receipt_ref


class PostgresProviderNoticeStore:
    """EXECUTE-only actors; authority/identity/content admission and audit in one TX."""

    def __init__(
        self,
        engine: AsyncEngine,
        installation: ProviderNoticeInstallation,
        *,
        role: Literal["producer", "recipient"],
        identity_admission: PostgresCommunicationAdmission | None = None,
    ) -> None:
        self.engine = engine
        self.installation = ProviderNoticeInstallation.model_validate_json(installation.model_dump_json())
        self.scope = self.installation.scope
        self.role, self.identity_admission = role, identity_admission
        self._installation_digest = _installation_digest(self.installation)
        if role == "recipient" and (
            identity_admission is None
            or identity_admission.engine is not engine
            or identity_admission.scope != self.scope
        ):
            raise ExternalCaseError("unavailable")
        self.schema = '"' + self.installation.schema_name + '"'

    async def qualify(self, db: AsyncConnection) -> None:
        d = self.installation
        alive(d.valid_until)
        if self.engine.dialect.name != "postgresql" or self._installation_digest != _installation_digest(d):
            raise ExternalCaseError("unavailable")
        role = d.producer_role if self.role == "producer" else d.recipient_role
        info = (
            (
                await db.execute(
                    text("""
            SELECT session_user::text AS login,current_user::text AS effective,
             (SELECT oid FROM pg_database WHERE datname=current_database()) AS db_oid,
             pg_my_temp_schema() AS temp_oid,
             COALESCE((SELECT ssl FROM pg_stat_ssl WHERE pid=pg_backend_pid()),false) AS tls
        """)
                )
            )
            .mappings()
            .one()
        )
        if tuple(info[k] for k in ("login", "effective", "db_oid", "temp_oid", "tls")) != (
            role,
            role,
            d.database_oid,
            0,
            True,
        ):
            raise ExternalCaseError("unavailable")
        for actor in (d.owner_role, d.authority_validator_role, d.producer_role, d.recipient_role):
            flags = (
                (
                    await db.execute(
                        text("""
                SELECT rolsuper,rolcreatedb,rolcreaterole,rolbypassrls,rolreplication,
                 EXISTS(SELECT 1 FROM pg_auth_members WHERE member=r.oid) AS inherited,
                 (r.rolname=:owner AND r.rolcanlogin) AS owner_login,
                 (r.rolname<>:owner AND has_schema_privilege(r.oid,n.oid,'CREATE')) AS can_create,
                 has_database_privilege(r.oid,current_database(),'TEMP') AS can_temp
                FROM pg_roles r JOIN pg_namespace n ON n.nspname=:schema WHERE r.rolname=:actor
            """),
                        {"owner": d.owner_role, "schema": d.schema_name, "actor": actor},
                    )
                )
                .mappings()
                .one_or_none()
            )
            if flags is None or any(flags.values()):
                raise ExternalCaseError("unavailable")
        for pin in d.relations:
            relation = (
                (
                    await db.execute(
                        text("""
                SELECT c.oid,n.oid AS schema_oid,c.relkind::text AS relkind,
                 pg_get_userbyid(c.relowner) AS owner,
                 pg_get_userbyid(n.nspowner) AS schema_owner,c.relrowsecurity,
                 EXISTS(SELECT 1 FROM aclexplode(coalesce(n.nspacl,acldefault('n',n.nspowner))) a
                   WHERE a.grantee=0) AS public_schema,
                 EXISTS(SELECT 1 FROM aclexplode(coalesce(n.nspacl,acldefault('n',n.nspowner))) a
                   WHERE a.grantee<>n.nspowner AND (a.is_grantable OR a.privilege_type<>'USAGE'
                    OR a.grantee NOT IN (SELECT oid FROM pg_roles WHERE rolname IN
                    (:validator,:producer,:recipient)))) AS schema_acl_drift,
                 (SELECT count(*) FROM aclexplode(coalesce(n.nspacl,acldefault('n',n.nspowner))) a
                   WHERE a.grantee<>n.nspowner AND a.privilege_type='USAGE' AND NOT a.is_grantable)
                   AS schema_usage_count,
                 EXISTS(SELECT 1 FROM aclexplode(coalesce(c.relacl,acldefault('r',c.relowner))) a
                   WHERE a.grantee<>c.relowner AND NOT
                   (c.relname='authority' AND a.grantee IN
                   (SELECT oid FROM pg_roles WHERE rolname=:validator))) AS extra_acl,
                 EXISTS(SELECT 1 FROM pg_attribute at,LATERAL aclexplode(at.attacl) a
                   WHERE at.attrelid=c.oid AND a.grantee<>c.relowner) AS extra_column_acl,
                 (SELECT count(*) FROM pg_trigger t WHERE t.tgrelid=c.oid AND NOT t.tgisinternal
                  AND t.tgenabled='O' AND t.tgtype=27 AND t.tgfoid=:trigger_oid
                  AND t.tgqual IS NULL AND t.tgnargs=0) AS expected_triggers,
                 (SELECT count(*) FROM pg_trigger t WHERE t.tgrelid=c.oid AND NOT t.tgisinternal
                  AND t.tgenabled='O' AND t.tgtype=34 AND t.tgfoid=:truncate_oid
                  AND t.tgqual IS NULL AND t.tgnargs=0) AS expected_truncate_triggers,
                 (SELECT count(*) FROM pg_trigger t WHERE t.tgrelid=c.oid AND NOT t.tgisinternal)
                   AS trigger_count,
                 has_table_privilege(session_user,c.oid,'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER')
                   AS direct_access,
                 has_any_column_privilege(session_user,c.oid,'SELECT,INSERT,UPDATE,REFERENCES')
                   AS column_access
                FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
                WHERE n.nspname=:schema AND c.relname=:name
            """),
                        {
                            "schema": d.schema_name,
                            "name": pin.name,
                            "validator": d.authority_validator_role,
                            "producer": d.producer_role,
                            "recipient": d.recipient_role,
                            "truncate_oid": next(f.oid for f in d.functions if f.name == "immutable_history"),
                            "trigger_oid": next(
                                f.oid
                                for f in d.functions
                                if f.name
                                == ("protect_authority" if pin.name == "authority" else "immutable_history")
                            ),
                        },
                    )
                )
                .mappings()
                .one_or_none()
            )
            if (
                relation is None
                or tuple(relation[k] for k in ("oid", "schema_oid", "relkind", "owner", "schema_owner"))
                != (pin.oid, d.schema_oid, "r", d.owner_role, d.owner_role)
                or any(
                    relation[k]
                    for k in (
                        "relrowsecurity",
                        "public_schema",
                        "schema_acl_drift",
                        "extra_acl",
                        "extra_column_acl",
                        "direct_access",
                        "column_access",
                    )
                )
                or relation["schema_usage_count"] != 3
                or (
                    relation["expected_triggers"],
                    relation["expected_truncate_triggers"],
                    relation["trigger_count"],
                )
                != (1, 1, 2)
            ):
                raise ExternalCaseError("unavailable")
            grants = (
                (
                    await db.execute(
                        text("""
                SELECT a.grantee,pg_get_userbyid(a.grantee) AS grantee_name,a.privilege_type,a.is_grantable
                FROM pg_class c,LATERAL aclexplode(coalesce(c.relacl,acldefault('r',c.relowner))) a
                WHERE c.oid=:oid AND a.grantee<>c.relowner
            """),
                        dict(oid=pin.oid),
                    )
                )
                .mappings()
                .all()
            )
            expected = (
                {(d.authority_validator_role, p, False) for p in ("SELECT", "INSERT", "UPDATE")}
                if (pin.name == "authority")
                else set()
            )
            if (
                any(g["grantee"] == 0 for g in grants)
                or {(g["grantee_name"], g["privilege_type"], g["is_grantable"]) for g in grants} != expected
            ):
                raise ExternalCaseError("unavailable")
        for function_pin in d.functions:
            signature = f"{d.schema_name}.{function_pin.name}({NOTICE_FUNCTIONS[function_pin.name]})"
            function = (
                (
                    await db.execute(
                        text("""
                SELECT p.oid,pg_get_userbyid(p.proowner) AS owner,pg_get_functiondef(p.oid) AS body,
                 p.prosecdef,has_function_privilege(session_user,p.oid,'EXECUTE') AS execute,
                 EXISTS(SELECT 1 FROM aclexplode(coalesce(p.proacl,acldefault('f',p.proowner))) a
                   WHERE a.grantee<>p.proowner AND a.grantee NOT IN
                   (SELECT oid FROM pg_roles WHERE rolname IN (:producer,:recipient))) AS extra_acl
                FROM pg_proc p WHERE p.oid=to_regprocedure(:signature)
            """),
                        {"signature": signature, "producer": d.producer_role, "recipient": d.recipient_role},
                    )
                )
                .mappings()
                .one_or_none()
            )
            can_execute = (
                function_pin.name == "prepare_notice"
                if self.role == "producer"
                else function_pin.name
                in {
                    "acknowledge_notice",
                    "inspect_notice",
                }
            )
            security_definer = function_pin.name not in {"immutable_history", "protect_authority"}
            if (
                function is None
                or tuple(function[k] for k in ("oid", "owner", "prosecdef", "execute", "extra_acl"))
                != (
                    function_pin.oid,
                    d.owner_role,
                    security_definer,
                    can_execute,
                    False,
                )
                or hashlib.sha256(function["body"].encode()).hexdigest() != function_pin.sha256
            ):
                raise ExternalCaseError("unavailable")
            grants = (
                (
                    await db.execute(
                        text("""
                SELECT a.grantee,pg_get_userbyid(a.grantee) AS grantee_name,a.privilege_type,a.is_grantable
                FROM pg_proc p,LATERAL aclexplode(coalesce(p.proacl,acldefault('f',p.proowner))) a
                WHERE p.oid=:oid AND a.grantee<>p.proowner
            """),
                        dict(oid=function_pin.oid),
                    )
                )
                .mappings()
                .all()
            )
            allowed_role = (
                d.producer_role
                if function_pin.name == "prepare_notice"
                else d.recipient_role
                if function_pin.name in {"acknowledge_notice", "inspect_notice"}
                else None
            )
            expected = {(allowed_role, "EXECUTE", False)} if allowed_role is not None else set()
            if (
                any(g["grantee"] == 0 for g in grants)
                or {(g["grantee_name"], g["privilege_type"], g["is_grantable"]) for g in grants} != expected
            ):
                raise ExternalCaseError("unavailable")
        # Cross-owner permissions must have been explicitly installed by source administrators.
        # The producer does not use the identity schema directly. Inspect pinned
        # catalog OIDs without name resolution requiring actor USAGE there, then
        # verify the namespace, object identity and exact input signature too.
        lock = (
            (
                await db.execute(
                    text("""
            SELECT p.oid,n.nspname AS schema_name,p.proname AS function_name,
             p.prokind::text AS function_kind,p.pronargs AS argument_count,
             tn.nspname AS argument_type_schema,t.typname AS argument_type_name,
             pg_catalog.pg_get_userbyid(p.proowner) AS owner,p.prosecdef,
             pg_catalog.pg_get_functiondef(p.oid) AS body,
             pg_catalog.has_function_privilege(:owner,p.oid,'EXECUTE') AS owner_execute
            FROM pg_catalog.pg_proc p
            JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace
            LEFT JOIN pg_catalog.pg_type t ON t.oid=p.proargtypes[0]
            LEFT JOIN pg_catalog.pg_namespace tn ON tn.oid=t.typnamespace
            WHERE p.oid=:lock_oid
        """),
                    {"owner": d.owner_role, "lock_oid": d.session_lock_oid},
                )
            )
            .mappings()
            .one_or_none()
        )
        if (
            lock is None
            or tuple(
                lock[k]
                for k in (
                    "oid",
                    "schema_name",
                    "function_name",
                    "function_kind",
                    "argument_count",
                    "argument_type_schema",
                    "argument_type_name",
                    "owner",
                    "prosecdef",
                    "owner_execute",
                )
            )
            != (
                d.session_lock_oid,
                "portal_communication",
                "lock_session",
                "f",
                1,
                "pg_catalog",
                "text",
                d.session_lock_owner_role,
                True,
                True,
            )
            or hashlib.sha256(lock["body"].encode()).hexdigest() != d.session_lock_sha256
        ):
            raise ExternalCaseError("unavailable")
        content = (
            (
                await db.execute(
                    text("""
            SELECT c.oid,n.nspname AS schema_name,c.relname AS relation_name,c.relkind::text AS relkind,
             pg_catalog.pg_get_userbyid(c.relowner) AS owner,
             pg_catalog.has_column_privilege(:owner,c.oid,'ciphertext','SELECT') AS ciphertext,
             pg_catalog.has_column_privilege(:owner,c.oid,'nonce','SELECT') AS nonce,
             pg_catalog.has_column_privilege(:owner,c.oid,'key_id','SELECT') AS key_id,
             pg_catalog.has_column_privilege(session_user,c.oid,'ciphertext','SELECT') AS actor_ciphertext,
             pg_catalog.has_column_privilege(session_user,c.oid,'nonce','SELECT') AS actor_nonce,
             pg_catalog.has_column_privilege(session_user,c.oid,'key_id','SELECT') AS actor_key_id
            FROM pg_catalog.pg_class c
            JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
            WHERE c.oid=:content_oid
        """),
                    {"owner": d.owner_role, "content_oid": d.content_relation_oid},
                )
            )
            .mappings()
            .one_or_none()
        )
        if content is None or tuple(
            content[k]
            for k in (
                "oid",
                "schema_name",
                "relation_name",
                "relkind",
                "owner",
                "ciphertext",
                "nonce",
                "key_id",
                "actor_ciphertext",
                "actor_nonce",
                "actor_key_id",
            )
        ) != (
            d.content_relation_oid,
            "portal_communication",
            "content",
            "r",
            d.content_owner_role,
            False,
            False,
            False,
            False,
            False,
            False,
        ):
            raise ExternalCaseError("unavailable")

    async def prepare(
        self,
        scope: ProviderNoticeScope,
        intent: NoticeIntent,
        *,
        command_ref: str,
        digest: str,
    ) -> ProviderNoticeReceipt:
        scope = ProviderNoticeScope.model_validate_json(scope.model_dump_json())
        intent = _notice_intent(intent)
        if (
            self.role != "producer"
            or scope.tenant_ref != self.scope.tenant
            or scope.environment != self.scope.environment
        ):
            raise ExternalCaseError("denied")
        try:
            async with transaction(self.engine, 5) as db:
                await self.qualify(db)
                raw = (
                    await db.execute(
                        text(
                            f"SELECT {self.schema}.prepare_notice("
                            "CAST(:scope AS jsonb),CAST(:intent AS jsonb),"
                            ":command,:digest,:metadata,:audit,:until)"
                        ),
                        {
                            "scope": _json(scope.model_dump(mode="json")),
                            "intent": _json(intent.model_dump(mode="json")),
                            "command": command_ref,
                            "digest": digest,
                            "metadata": secrets.token_urlsafe(24),
                            "audit": secrets.token_urlsafe(24),
                            "until": self.installation.valid_until,
                        },
                    )
                ).scalar_one()
                result = ProviderNoticeReceipt.model_validate_json(_json(raw))
                alive(self.installation.valid_until)
            return result
        except ExternalCaseError:
            raise
        except Exception as error:
            reason = sql_failure(error)
            raise ExternalCaseError(reason) from None

    async def recipient(
        self,
        *,
        secret: str,
        session: ResolvedHumanSession,
        notice_ref: str,
        acknowledgement: ProviderNoticeAcknowledgement | None,
    ) -> NoticeRecipientObservation:
        if self.role != "recipient" or self.identity_admission is None:
            raise ExternalCaseError("unavailable")
        try:
            async with transaction(self.engine, 5) as db:
                await self.qualify(db)
                ceiling = await self.identity_admission._identity(db, secret, session)
                ceiling = alive(ceiling, self.installation.valid_until)
                params: dict[str, Any] = {
                    "tenant": self.scope.tenant,
                    "environment": self.scope.environment,
                    "notice": notice_ref,
                    "secret": secret_digest(secret),
                    "until": ceiling,
                }
                if acknowledgement is None:
                    sql = f"SELECT {self.schema}.inspect_notice(:tenant,:environment,:notice,:secret,:until)"
                else:
                    acknowledgement = ProviderNoticeAcknowledgement.model_validate_json(
                        acknowledgement.model_dump_json()
                    )
                    params.update(
                        command=acknowledgement.command_id,
                        digest=fingerprint(
                            {"notice_ref": notice_ref, **acknowledgement.model_dump(mode="json")}
                        ),
                        revision=acknowledgement.notice_revision,
                        content=acknowledgement.content_digest,
                        receipt=secrets.token_urlsafe(24),
                        audit=secrets.token_urlsafe(24),
                    )
                    sql = f"SELECT {self.schema}.acknowledge_notice("
                    sql += ":tenant,:environment,:notice,:command,:digest,:revision,"
                    sql += ":content,:secret,:receipt,:audit,:until)"
                raw = (await db.execute(text(sql), params)).scalar_one()
                result = NoticeRecipientObservation.model_validate_json(_json(raw))
                alive(ceiling, result.valid_until)
            return result
        except ExternalCaseError:
            raise
        except Exception as error:
            raise ExternalCaseError(sql_failure(error)) from None


class ProviderNoticeSource:
    """The shared dispatcher source; no per-journey copy or human credential."""

    def __init__(self, binding: AdmissionBinding, store: PostgresProviderNoticeStore) -> None:
        self.binding = AdmissionBinding.model_validate_json(binding.model_dump_json())
        if (
            self.binding.operation_name != "notice.prepare_or_send"
            or self.binding.schema_version != PROVIDER_SCHEMA_VERSION
        ):
            raise ExternalCaseError("unavailable")
        if store.role != "producer" or store.scope.tenant != binding.tenant_ref:
            raise ExternalCaseError("unavailable")
        self.store = store

    async def execute(
        self, envelope: AnyCapabilityEnvelope, request: CandidateDTO, *, timeout_seconds: float
    ) -> object:
        envelope = parse_envelope(envelope)
        intent = _notice_intent(request)
        for key in (
            "tenant_ref",
            "legal_entity_ref",
            "operation_name",
            "schema_version",
            "source_authority_ref",
            "policy_revision",
            "data_classification",
        ):
            if getattr(envelope, key) != getattr(self.binding, key):
                raise ExternalCaseError("denied")
        scope = ProviderNoticeScope(
            tenant_ref=self.binding.tenant_ref,
            environment=self.store.scope.environment,
            legal_entity_ref=self.binding.legal_entity_ref,
            principal_ref=self.binding.principal_ref,
            task_ref=self.binding.task_ref,
            purpose_ref=self.binding.purpose_ref,
            source_authority_ref=self.binding.source_authority_ref,
            policy_revision=self.binding.policy_revision,
            contract_revision=self.binding.contract_revision,
            data_classification=self.binding.data_classification,
            business_revision=envelope.expected_business_revision,
            producer_role=self.store.installation.producer_role,
        )
        try:
            receipt = await self.store.prepare(
                scope, intent, command_ref=envelope.idempotency_key, digest=request_digest(envelope, intent)
            )
        except ExternalCaseError as error:
            reason = {
                "denied": CapabilityRefusalReason.AUTHORITY_UNPROVEN,
                "conflict": CapabilityRefusalReason.CONTRACT_MISMATCH,
            }.get(error.code, CapabilityRefusalReason.SOURCE_UNAVAILABLE)
            raise CapabilityContractError(reason) from None
        if receipt.notice_ref != intent.notice_ref or receipt.notice_revision != scope.business_revision:
            raise ExternalCaseError("denied")
        return DeliveryEvidenceReceipt(
            delivery_status=receipt.delivery_status,
            provider_delivery_receipt_ref=receipt.provider_delivery_receipt_ref,
            attempt_revision=receipt.notice_revision,
            metadata_publication_receipt_ref=receipt.metadata_publication_receipt_ref,
        )


class NoticeRecipientStore(Protocol):
    scope: CommunicationScope

    async def recipient(
        self,
        *,
        secret: str,
        session: ResolvedHumanSession,
        notice_ref: str,
        acknowledgement: ProviderNoticeAcknowledgement | None,
    ) -> NoticeRecipientObservation: ...


class ProviderNoticeRecipientService:
    def __init__(self, resolver: HumanSessionResolver, store: NoticeRecipientStore) -> None:
        if resolver.settings.tenant != store.scope.tenant:
            raise ExternalCaseError("unavailable")
        self.resolver, self.store = resolver, store

    async def operation[T](
        self,
        secret: str,
        *,
        notice_ref: str,
        body: ProviderNoticeAcknowledgement | None,
        csrf: str | None = None,
        origin: str | None = None,
        freeze: Callable[[bytes], T],
    ) -> T:
        initial = await self.resolver.resolve(secret)
        if initial.membership.audience != "provider":
            raise AuthenticationError()
        ceiling = alive(initial.record.expires_at, initial.membership.reviewed_until)
        if body is not None:
            body = ProviderNoticeAcknowledgement.model_validate_json(body.model_dump_json())
            if (
                origin != self.resolver.settings.public_origin
                or not csrf
                or not secrets.compare_digest(
                    csrf,
                    initial.record.csrf_token,
                )
            ):
                raise AuthenticationError()
        observation = await self.store.recipient(
            secret=secret, session=initial, notice_ref=notice_ref, acknowledgement=body
        )
        result = observation.result
        ceiling = alive(ceiling, observation.valid_until)
        if result.notice_ref != notice_ref or (
            body is not None
            and (
                not isinstance(result, ProviderNoticeReceipt)
                or result.notice_revision != body.notice_revision
                or result.delivery_status != "delivered"
            )
        ):
            raise ExternalCaseError("denied")
        if body is None and not isinstance(result, ProviderNoticeSummary):
            raise ExternalCaseError("denied")
        final = await self.resolver.resolve(secret)
        if final.principal != initial.principal or final.membership.audience != "provider":
            raise ExternalCaseError("conflict")
        ceiling = alive(ceiling, final.record.expires_at, final.membership.reviewed_until)
        # Re-open source/identity after the last asynchronous session resolution.
        current = await self.store.recipient(
            secret=secret, session=final, notice_ref=notice_ref, acknowledgement=None
        )
        if not isinstance(current.result, ProviderNoticeSummary):
            raise ExternalCaseError("denied")
        if body is not None and current.result.receipt != result:
            raise ExternalCaseError("conflict")
        if body is None and current.result != result:
            raise ExternalCaseError("conflict")
        ceiling = alive(ceiling, current.valid_until)
        raw = result.model_dump_json().encode()
        value = freeze(raw)
        alive(ceiling)
        return value

"""Concrete owner install/readback and SELECT-only legacy/v2 cohort qualification.

No gateway activation. Independently designated root/freeze issuers, actual consumer
qualification and a mutation authority producer remain production prerequisites.
Only immutable, signed qualification records may populate the existing native table.
"""

from __future__ import annotations

import base64
import math
import re
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from importlib.resources import files
from typing import Any, Literal

import asyncpg  # type: ignore[import-untyped]
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from pydantic import Field, model_validator

from maezo.portal.contracts.models import HumanPrincipal, OpaqueRef, Revision, Sha256Digest
from maezo.portal.engine.profile import canonicalize, strict_loads

from .decision import DecisionBindingSource, QualifiedDecisionBinding
from .decision_binding_qualification import (
    COHORT_CONTRACT,
    BindingUnavailableError,
    CohortManifest,
    NativeCohortScope,
    QualificationPacket,
    QualificationPacketV2,
    QualificationVerifier,
    Revocation,
    SignedAuthorities,
    VerifiedQualification,
    artifact_bytes,
    batch_member,
    canonical,
    decode,
    decode_packet,
    member_order,
    packet_cohort,
    sha,
)
from .decision_custody_connection import _pinned_tls_context
from .models import AuthoritativeTask, Closed, CurrentTaskAuthority, Scope
from .read_profile import ResourceProjection, SourceProvenance, parse_model
from .read_publisher import PublicationReceipt
from .transport import HumanTLSIdentity

IMMUTABLE = (
    "mzo_human_decision_binding",
    "mzo_human_decision_authority",
    "mzo_human_decision_qualification",
    "mzo_human_decision_install_receipt",
)
NATIVE = (
    "mzo_human_tenant",
    "mzo_human_principal",
    "mzo_human_evidence",
    "act_re_procdef",
    "act_re_decision_def",
    "act_ge_bytearray",
    "act_ru_task",
    "act_ru_identitylink",
    "mzo_portal_read_resource",
    "mzo_portal_read_publication_receipt",
    "mzo_portal_read_revocation",
)
RELATIONS = IMMUTABLE + NATIVE


class RelationPin(Closed):
    name: str = Field(pattern=r"^[a-z][a-z0-9_]{0,62}$")
    oid: int = Field(gt=0, lt=2**32)
    owner: str = Field(pattern=r"^[a-z][a-z0-9_]{0,62}$")


class BindingDatabase(Closed):
    scope: Scope
    installation_id: OpaqueRef
    engine_name: OpaqueRef
    database_incarnation: OpaqueRef
    resource_publisher_fingerprint: Sha256Digest
    host: str = Field(pattern=r"^[A-Za-z0-9.:-]+$")
    port: int = Field(gt=0, lt=65536)
    database: str = Field(pattern=r"^[a-z][a-z0-9_]{0,62}$")
    database_oid: int = Field(gt=0, lt=2**32)
    schema_name: str = Field(pattern=r"^[a-z][a-z0-9_]{0,62}$")
    schema_oid: int = Field(gt=0, lt=2**32)
    owner_role: str = Field(pattern=r"^[a-z][a-z0-9_]{0,62}$")
    installer_role: str = Field(pattern=r"^[a-z][a-z0-9_]{0,62}$")
    reader_role: str = Field(pattern=r"^[a-z][a-z0-9_]{0,62}$")
    engine_role: str = Field(pattern=r"^[a-z][a-z0-9_]{0,62}$")
    installer_certificate_digest: Sha256Digest
    reader_certificate_digest: Sha256Digest
    relations: tuple[RelationPin, ...]

    @model_validator(mode="after")
    def pins(self) -> BindingDatabase:
        if len({self.owner_role, self.installer_role, self.reader_role, self.engine_role}) != 4:
            raise ValueError("binding roles must be separate")
        if len(self.relations) != len(RELATIONS) or {p.name for p in self.relations} != set(RELATIONS):
            raise ValueError("incomplete native relation pins")
        if len({p.oid for p in self.relations}) != len(self.relations):
            raise ValueError("duplicate native relation pin")
        if any(p.owner != self.owner_role for p in self.relations if p.name in IMMUTABLE):
            raise ValueError("binding owner mismatch")
        return self


class InstallationReceiptFields(Closed):
    operation_id: OpaqueRef
    kind: Literal["designate", "install", "revoke"]
    request_digest: Sha256Digest
    expected_revision: Revision
    resulting_revision: Revision
    recorded_at: datetime
    packet_digests: tuple[Sha256Digest, ...]

    def validate_receipt(self, *, limit: int, digest: str) -> None:
        if self.resulting_revision != self.expected_revision + 1:
            raise ValueError("invalid installation receipt revision")
        if self.kind == "install":
            if (
                not 1 <= len(self.packet_digests) <= limit
                or tuple(sorted(set(self.packet_digests))) != self.packet_digests
                or self.request_digest != digest
            ):
                raise ValueError("invalid installation batch receipt")
        elif self.packet_digests:
            raise ValueError("unexpected installation batch")


class InstallationReceipt(InstallationReceiptFields):
    @model_validator(mode="after")
    def closed_receipt(self) -> InstallationReceipt:
        self.validate_receipt(limit=6, digest=sha(canonicalize(list(self.packet_digests))))
        return self


class InstallationReceiptV2(InstallationReceiptFields):
    schema_: Literal["human-decision-installation-receipt.v2"] = Field(alias="schema")
    cohort_digest: Sha256Digest
    kind: Literal["install"]

    @model_validator(mode="after")
    def closed_receipt(self) -> InstallationReceiptV2:
        self.validate_receipt(limit=43, digest=install_digest(self.packet_digests, self.cohort_digest))
        return self


def install_digest(digests: tuple[str, ...], cohort: str | None) -> str:
    return sha(
        canonicalize(
            list(digests)
            if cohort is None
            else {
                "schema": "human-decision-install-request.v2",
                "cohort_digest": cohort,
                "packet_digests": list(digests),
            }
        )
    )


def install_record(digests: tuple[str, ...], cohort: str | None) -> str:
    return canonicalize(
        list(digests)
        if cohort is None
        else {
            "schema": "human-decision-install-record.v2",
            "cohort_digest": cohort,
            "packet_digests": list(digests),
        }
    ).decode()


def stored_receipt(row: Any) -> InstallationReceipt | InstallationReceiptV2:
    raw = row["packet_digests_"].encode()
    value = strict_loads(raw)
    if canonicalize(value) != raw:
        raise BindingUnavailableError()
    fields = dict(
        operation_id=row["operation_"],
        kind=row["kind_"],
        request_digest=row["request_digest_"],
        expected_revision=row["expected_rev_"],
        resulting_revision=row["resulting_rev_"],
        recorded_at=row["recorded_at_"],
    )
    if type(value) is list:
        return InstallationReceipt(**fields, packet_digests=tuple(value))
    if (
        type(value) is not dict
        or set(value) != {"schema", "cohort_digest", "packet_digests"}
        or value["schema"] != "human-decision-install-record.v2"
        or type(value["packet_digests"]) is not list
    ):
        raise BindingUnavailableError()
    return InstallationReceiptV2(
        **fields,
        schema="human-decision-installation-receipt.v2",
        cohort_digest=value["cohort_digest"],
        packet_digests=tuple(value["packet_digests"]),
    )


NATIVE_COHORT_RELATIONS = tuple(
    "mzo_human_consumer_" + suffix for suffix in ("database", "trust", "revoked", "head", "qualification")
)


class NativeCohortReadConfiguration(Closed):
    """Out-of-band qualified source pins. No default reader grant or inferred owner."""

    scope: NativeCohortScope
    binding_database_digest: Sha256Digest
    designation_digest: Sha256Digest
    native_build_digest: Sha256Digest
    phi_build_digest: Sha256Digest
    relations: tuple[RelationPin, ...]

    @model_validator(mode="after")
    def exact_relations(self) -> NativeCohortReadConfiguration:
        if (
            len(self.relations) != 5
            or {p.name for p in self.relations} != set(NATIVE_COHORT_RELATIONS)
            or len({p.oid for p in self.relations}) != 5
        ):
            raise ValueError("incomplete native cohort read pins")
        return self


def native_object(raw: str) -> dict[str, Any]:
    encoded = raw.encode()
    value = strict_loads(encoded)
    if len(encoded) > 65536 or type(value) is not dict or canonicalize(value) != encoded:
        raise BindingUnavailableError()
    return value


def native_url64(value: str, size: int) -> bytes:
    raw = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
    if len(raw) != size or base64.urlsafe_b64encode(raw).rstrip(b"=").decode() != value:
        raise BindingUnavailableError()
    return raw


class BindingConnection:
    """Explicit mTLS construction; no production constructor accepts arbitrary pools."""

    def __init__(
        self,
        *,
        database: BindingDatabase,
        identity: HumanTLSIdentity,
        verifier: QualificationVerifier,
        mode: Literal["installer", "reader"],
        native_cohort_read: NativeCohortReadConfiguration | None = None,
        timeout_seconds: float = 10,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        if (
            mode not in ("installer", "reader")
            or database.scope != identity.scope
            or database.scope != verifier.root.scope
            or database.installation_id != verifier.root.installation_id
            or sha(canonical(database)) != verifier.root.installation_digest
            or type(timeout_seconds) not in (float, int)
            or not math.isfinite(timeout_seconds)
            or timeout_seconds <= 0
        ):
            raise BindingUnavailableError()
        self.database, self.identity, self.verifier, self.mode = database, identity, verifier, mode
        self.clock, self.timeout = clock, timeout_seconds
        self.native_cohort_read = native_cohort_read
        if native_cohort_read is not None:
            n = NativeCohortReadConfiguration.model_validate(native_cohort_read)
            if (
                n.binding_database_digest != sha(canonical(database))
                or (n.scope.tenant, n.scope.environment, n.scope.engine_name, n.scope.database_incarnation)
                != (
                    database.scope.tenant,
                    database.scope.environment,
                    database.engine_name,
                    database.database_incarnation,
                )
                or any(p.owner != database.owner_role for p in n.relations)
            ):
                raise BindingUnavailableError()

    def table(self, name: str) -> str:
        if name not in RELATIONS and not (
            self.native_cohort_read is not None and name in NATIVE_COHORT_RELATIONS
        ):
            raise BindingUnavailableError()
        return f'"{self.database.schema_name}"."{name}"'

    async def catalog(self, connection: Any) -> None:
        d = self.database
        role = d.installer_role if self.mode == "installer" else d.reader_role
        row = await connection.fetchrow(
            """SELECT current_database() AS database,
          (SELECT oid FROM pg_database WHERE datname=current_database()) AS database_oid,
          current_user AS role, session_user AS session_role, s.ssl,
          coalesce(length(s.client_dn)>0,false) AS client_certificate,
          n.oid AS schema_oid, pg_get_userbyid(n.nspowner) AS owner
          FROM pg_stat_ssl s JOIN pg_namespace n ON n.nspname=$1 WHERE s.pid=pg_backend_pid()""",
            d.schema_name,
        )
        if row is None or any(
            row[k] != v
            for k, v in {
                "database": d.database,
                "database_oid": d.database_oid,
                "role": role,
                "session_role": role,
                "ssl": True,
                "client_certificate": True,
                "schema_oid": d.schema_oid,
                "owner": d.owner_role,
            }.items()
        ):
            raise BindingUnavailableError()
        for name in (d.installer_role, d.reader_role, d.engine_role):
            r = await connection.fetchrow(
                """SELECT rolsuper,rolcreaterole,rolcreatedb,
              rolreplication,rolbypassrls,
              EXISTS(SELECT 1 FROM pg_auth_members WHERE member=pg_roles.oid) AS memberships,
              has_schema_privilege(rolname,$2,'CREATE') AS schema_create,
              has_database_privilege(rolname,current_database(),'CREATE') AS database_create
              FROM pg_roles WHERE rolname=$1""",
                name,
                d.schema_name,
            )
            if r is None or any(v is not False for v in dict(r).values()):
                raise BindingUnavailableError()
        for pin in (*d.relations, *(self.native_cohort_read.relations if self.native_cohort_read else ())):
            r = await connection.fetchrow(
                """SELECT c.oid,pg_get_userbyid(c.relowner) AS owner,c.relkind,c.relrowsecurity,
              has_table_privilege($3,c.oid,'SELECT') AS can_select,
              (has_table_privilege($3,c.oid,'INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER')
              OR has_any_column_privilege($3,c.oid,'INSERT,UPDATE,REFERENCES')) AS writes
              FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
              WHERE n.nspname=$1 AND c.relname=$2""",
                d.schema_name,
                pin.name,
                d.reader_role,
            )
            if r is None or (r["oid"], r["owner"], r["relkind"], r["can_select"], r["writes"]) != (
                pin.oid,
                pin.owner,
                "r",
                True,
                False,
            ):
                raise BindingUnavailableError()
            if pin.name in NATIVE_COHORT_RELATIONS:
                # Match the native owner's qualified source contract at every read,
                # including same-OID drift that could hide a revocation row.
                if r["relrowsecurity"] is not False:
                    raise BindingUnavailableError()
                native = await connection.fetchrow(
                    """SELECT
                  has_table_privilege($1,$2::oid,'SELECT') AS native_engine_select,
                  has_any_column_privilege($1,$2::oid,'SELECT') AS native_engine_column_select,
                  (has_table_privilege($1,$2::oid,'INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER')
                   OR has_any_column_privilege($1,$2::oid,'INSERT,UPDATE,REFERENCES')) AS """
                    "native_engine_writes",
                    d.engine_role,
                    pin.oid,
                )
                if native is None or tuple(native.values()) != (True, True, False):
                    raise BindingUnavailableError()
                if pin.name != "mzo_human_consumer_head":
                    trigger = await connection.fetchrow(
                        """SELECT t.tgenabled,t.tgtype,p.prosrc,p.prosecdef,
                      count(*) OVER () AS trigger_count
                      FROM pg_trigger t JOIN pg_proc p ON p.oid=t.tgfoid
                      JOIN pg_namespace n ON n.oid=p.pronamespace
                      WHERE t.tgrelid=$1::oid AND NOT t.tgisinternal
                      AND p.proname='mzo_human_consumer_immutable' AND n.nspname=$2""",
                        pin.oid,
                        d.schema_name,
                    )
                    if (
                        trigger is None
                        or trigger["tgenabled"] != "O"
                        or trigger["tgtype"] != 58
                        or trigger["prosecdef"] is not False
                        or trigger["trigger_count"] != 1
                        or trigger["prosrc"].strip()
                        != "BEGIN RAISE EXCEPTION 'immutable consumer evidence'; END"
                    ):
                        raise BindingUnavailableError()
            if pin.name in IMMUTABLE:
                r = await connection.fetchrow(
                    """SELECT
                  has_table_privilege($1,$3::oid,'SELECT') AS engine_select,
                  (has_table_privilege($1,$3::oid,'INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER')
                   OR has_any_column_privilege($1,$3::oid,'INSERT,UPDATE,REFERENCES')) AS engine_writes,
                  has_table_privilege($2,$3::oid,'SELECT') AS installer_select,
                  has_table_privilege($2,$3::oid,'INSERT') AS installer_insert,
                  (has_table_privilege($2,$3::oid,'UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER')
                   OR has_any_column_privilege($2,$3::oid,'UPDATE,REFERENCES')) AS installer_mutates""",
                    d.engine_role,
                    d.installer_role,
                    pin.oid,
                )
                if r is None or tuple(r.values()) != (True, False, True, True, False):
                    raise BindingUnavailableError()

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[Any]:
        connection = None
        try:
            d = self.database
            role = d.installer_role if self.mode == "installer" else d.reader_role
            fingerprint = (
                d.installer_certificate_digest if self.mode == "installer" else d.reader_certificate_digest
            )
            tls = _pinned_tls_context(self.identity, fingerprint)
            connection = await asyncpg.connect(
                host=d.host,
                port=d.port,
                database=d.database,
                user=role,
                password="",
                passfile="/dev/null",
                ssl=tls,
                direct_tls=False,
                timeout=self.timeout,
                command_timeout=self.timeout,
                server_settings={"search_path": "pg_catalog", "application_name": "maezo-decision-binding"},
            )
            async with connection.transaction(isolation="read_committed", readonly=self.mode == "reader"):
                await self.catalog(connection)
                yield connection
        except Exception:
            raise BindingUnavailableError() from None
        finally:
            if connection is not None:
                await connection.close()

    async def native_cohort_current(
        self, c: Any, qualified: tuple[VerifiedQualification, ...], revision: int
    ) -> tuple[str, datetime] | None:
        first = qualified[0].packet
        if not isinstance(first, QualificationPacketV2):
            return None
        n, d = self.native_cohort_read, self.database
        if n is None or n.scope != first.cohort.scope:
            raise BindingUnavailableError()
        row = await c.fetchrow(
            "SELECT h.generation_,h.qualification_generation_,q.authority_rev_,"
            "q.qualification_,q.envelope_,b.binding_ "
            f"FROM {self.table('mzo_human_consumer_head')} h "
            f"JOIN {self.table('mzo_human_consumer_qualification')} q ON q.tenant_=h.tenant_ "
            "AND q.generation_=h.qualification_generation_ "
            f"JOIN {self.table('mzo_human_consumer_database')} b ON b.tenant_=h.tenant_ WHERE h.tenant_=$1",
            d.scope.tenant,
        )
        if (
            row is None
            or row["generation_"] != row["qualification_generation_"]
            or row["authority_rev_"] != revision
        ):
            raise BindingUnavailableError()
        binding = native_object(row["binding_"])
        if (
            set(binding)
            != {
                "schema",
                "scope",
                "database_name",
                "database_oid",
                "schema_name",
                "schema_oid",
                "owner_role",
                "runtime_role",
            }
            or binding["schema"] != "phi-consumer-native-database.v1"
            or parse_model(NativeCohortScope, binding["scope"]) != n.scope
            or (
                binding["database_name"],
                binding["database_oid"],
                binding["schema_name"],
                binding["schema_oid"],
                binding["owner_role"],
                binding["runtime_role"],
            )
            != (
                d.database,
                str(d.database_oid),
                d.schema_name,
                str(d.schema_oid),
                d.owner_role,
                d.engine_role,
            )
        ):
            raise BindingUnavailableError()
        envelope = native_object(row["envelope_"])
        if set(envelope) != {
            "schema",
            "issuer",
            "key_id",
            "purpose",
            "authority_ref",
            "contract_digest",
            "body",
            "signature",
        }:
            raise BindingUnavailableError()
        trust = await c.fetchrow(
            f"SELECT designation_ FROM {self.table('mzo_human_consumer_trust')} "
            "WHERE tenant_=$1 AND key_id_=$2",
            d.scope.tenant,
            envelope["key_id"],
        )
        revoked = await c.fetchrow(
            f"SELECT key_id_ FROM {self.table('mzo_human_consumer_revoked')} WHERE tenant_=$1 AND key_id_=$2",
            d.scope.tenant,
            envelope["key_id"],
        )
        if trust is None or revoked is not None:
            raise BindingUnavailableError()
        designation = native_object(trust["designation_"])
        if (
            sha(canonicalize(designation)) != n.designation_digest
            or set(designation)
            != {
                "schema",
                "issuer",
                "key_id",
                "public_key",
                "purpose",
                "authority_ref",
                "contract_digest",
                "source_freeze_contract_digest",
                "valid_from_ms",
                "valid_until_ms",
            }
            or designation["schema"] != "phi-consumer-edge-trust.v1"
            or envelope["schema"] != "phi-consumer-edge-qualification-envelope.v1"
            or envelope["contract_digest"] != COHORT_CONTRACT
            or envelope["purpose"] != "native-consumer-edge-installation"
            or any(
                envelope[k] != designation[k]
                for k in ("issuer", "key_id", "purpose", "authority_ref", "contract_digest")
            )
        ):
            raise BindingUnavailableError()
        unsigned = {k: v for k, v in envelope.items() if k != "signature"}
        Ed25519PublicKey.from_public_bytes(native_url64(designation["public_key"], 32)).verify(
            native_url64(envelope["signature"], 64), canonicalize(unsigned)
        )
        body = envelope["body"]
        if (
            type(body) is not dict
            or set(body)
            != {
                "schema",
                "scope",
                "qualification_ref",
                "expected_generation",
                "expected_authority_revision",
                "native_build_digest",
                "phi_build_digest",
                "source_freeze",
                "targets",
                "valid_from_ms",
                "valid_until_ms",
                "cohort",
                "cohort_digest",
            }
            or body["schema"] != "phi-consumer-edge-qualification.v2"
            or parse_model(NativeCohortScope, body["scope"]) != n.scope
            or parse_model(CohortManifest, body["cohort"]) != first.cohort
            or body["cohort_digest"] != first.cohort.digest
            or body["expected_generation"] != str(row["generation_"] - 1)
            or body["expected_authority_revision"] != str(revision)
            or body["qualification_ref"] != row["qualification_"]
            or body["native_build_digest"] != n.native_build_digest
            or body["phi_build_digest"] != n.phi_build_digest
        ):
            raise BindingUnavailableError()
        freeze = body["source_freeze"]
        if (
            type(freeze) is not dict
            or set(freeze)
            != {
                "issuer_contract_digest",
                "authority_ref",
                "source_commit",
                "source_tree",
                "native_build_digest",
                "phi_build_digest",
                "source_artifacts_digest",
            }
            or freeze["issuer_contract_digest"] != designation["source_freeze_contract_digest"]
            or freeze["authority_ref"] != designation["authority_ref"]
            or any(freeze[k] != body[k] for k in ("native_build_digest", "phi_build_digest"))
            or any(
                type(freeze[k]) is not str or not re.fullmatch("[a-f0-9]{40}", freeze[k])
                for k in ("source_commit", "source_tree")
            )
            or type(freeze["source_artifacts_digest"]) is not str
            or not re.fullmatch("[a-f0-9]{64}", freeze["source_artifacts_digest"])
        ):
            raise BindingUnavailableError()
        now = int(self.clock().timestamp() * 1000)
        for record in (body, designation):
            if any(
                type(record[k]) is not str
                or not re.fullmatch("0|[1-9][0-9]*", record[k])
                or int(record[k]) >= 2**63
                for k in ("valid_from_ms", "valid_until_ms")
            ) or not int(record["valid_from_ms"]) <= now < int(record["valid_until_ms"]):
                raise BindingUnavailableError()
        if int(body["valid_until_ms"]) > int(designation["valid_until_ms"]):
            raise BindingUnavailableError()
        expected = []
        for q in sorted(
            qualified,
            key=lambda q: (
                q.packet.material.entry.process_definition_key,
                q.packet.material.entry.task_definition_key,
            ),
        ):
            e, m = q.packet.material.entry, q.packet.material
            edges = []
            choices = {
                "auth_decisao": ("JUNTA_MEDICA", "NEGAR"),
                "auth_junta": ("NEGAR",),
                "pagto_admissibilidade": ("DEVOLVER",),
            }.get(e.form_key, ())
            for outcome in choices:
                consumer, activity, topic = {
                    "NEGAR": (
                        "auth_denial_record",
                        "ST_EnviarNegativaFormal",
                        "operadora.auth.send_denial_notice",
                    ),
                    "JUNTA_MEDICA": (
                        "auth_junta_forward",
                        "ST_ConvocarJunta",
                        "operadora.auth.convene_junta",
                    ),
                    "DEVOLVER": (
                        "pagto_admissibility_return",
                        "ST_RegisterPaymentRefusal",
                        "operadora.pagto.register_payment_refusal",
                    ),
                }[outcome]
                edges.append(dict(outcome=outcome, consumer_kind=consumer, activity_id=activity, topic=topic))
            expected.append(
                dict(
                    process_definition_id=e.process_definition_id,
                    process_key=e.process_definition_key,
                    task_key=e.task_definition_key,
                    binding_digest=q.binding_digest,
                    consumer_digest=m.consumer.deployed_consumer.digest,
                    process_digest=e.process_definition_digest,
                    material_digest=sha(canonical(m)),
                    edges=edges,
                )
            )
        if body["targets"] != expected:
            raise BindingUnavailableError()
        return sha(canonicalize(envelope)), datetime.fromtimestamp(int(body["valid_until_ms"]) / 1000, UTC)

    async def revision(self, c: Any, *, lock: bool = False) -> int:
        suffix = " FOR UPDATE" if lock else ""
        value = await c.fetchval(
            f"SELECT rev_ FROM {self.table('mzo_human_tenant')} WHERE tenant_=$1{suffix}",
            self.database.scope.tenant,
        )
        if type(value) is not int or not 0 <= value < 2**63 - 1:
            raise BindingUnavailableError()
        return value

    async def authorities(self, c: Any) -> SignedAuthorities:
        d = self.database
        r = await c.fetchrow(
            f"SELECT document_,digest_,generation_ FROM {self.table('mzo_human_decision_authority')} "
            f"WHERE tenant_=$1 AND installation_=$2 ORDER BY generation_ DESC LIMIT 1",
            d.scope.tenant,
            d.installation_id,
        )
        if r is None or sha(r["document_"].encode()) != r["digest_"]:
            raise BindingUnavailableError()
        a = decode(SignedAuthorities, r["document_"].encode())
        if a.document.generation != r["generation_"]:
            raise BindingUnavailableError()
        self.verifier.authorities(a, self.clock())
        return a

    async def process(self, c: Any, material: Any) -> None:
        e = material.entry
        rows = await c.fetch(
            f"""SELECT p.id_,p.key_,p.version_,p.tenant_id_,b.bytes_
          FROM {self.table("act_re_procdef")} p JOIN {self.table("act_ge_bytearray")} b
          ON b.deployment_id_=p.deployment_id_ AND b.name_=p.resource_name_
          WHERE p.id_=$1 AND octet_length(b.bytes_)<=1048576 LIMIT 2""",
            e.process_definition_id,
        )
        if len(rows) != 1:
            raise BindingUnavailableError()
        r = rows[0]
        if (r["id_"], r["key_"], r["version_"], r["tenant_id_"]) != (
            e.process_definition_id,
            e.process_definition_key,
            e.process_definition_version,
            self.database.scope.tenant,
        ):
            raise BindingUnavailableError()
        if (
            bytes(r["bytes_"]) != artifact_bytes(material.process)
            or sha(bytes(r["bytes_"])) != e.process_definition_digest
        ):
            raise BindingUnavailableError()

        # Actual deployed task/candidate binding, including engine-pinned dynamic groups.
        import xml.etree.ElementTree as ET

        raw = bytes(r["bytes_"])
        if b"<!DOCTYPE" in raw.upper() or b"<!ENTITY" in raw.upper():
            raise BindingUnavailableError()
        tree = ET.fromstring(raw)
        ns = {"b": "http://www.omg.org/spec/BPMN/20100524/MODEL", "c": "http://camunda.org/schema/1.0/bpmn"}
        processes = [p for p in tree.findall("b:process", ns) if p.get("id") == e.process_definition_key]
        tasks = [
            t for p in processes for t in p.findall("b:userTask", ns) if t.get("id") == e.task_definition_key
        ]
        if len(processes) != 1 or len(tasks) != 1:
            raise BindingUnavailableError()
        candidate = tasks[0].get("{http://camunda.org/schema/1.0/bpmn}candidateGroups", "")
        domain = e.group_domain
        if domain.kind == "static":
            if tuple(candidate.split(",")) != domain.groups:
                raise BindingUnavailableError()
        else:
            if not candidate.startswith("${") or not candidate.endswith("}") or "." not in candidate:
                raise BindingUnavailableError()
            decision_refs = [
                t.get("{http://camunda.org/schema/1.0/bpmn}decisionRef")
                for t in processes[0].findall("b:businessRuleTask", ns)
            ]
            if domain.dmn_definition_key not in decision_refs or material.group_dmn is None:
                raise BindingUnavailableError()
            dmns = await c.fetch(
                f"""SELECT p.id_,p.key_,p.version_,p.tenant_id_,b.bytes_
              FROM {self.table("act_re_decision_def")} p JOIN {self.table("act_ge_bytearray")} b
              ON b.deployment_id_=p.deployment_id_ AND b.name_=p.resource_name_
              WHERE p.id_=$1 AND octet_length(b.bytes_)<=1048576 LIMIT 2""",
                domain.dmn_definition_id,
            )
            if len(dmns) != 1:
                raise BindingUnavailableError()
            dm = dmns[0]
            if (dm["id_"], dm["key_"], dm["version_"], dm["tenant_id_"]) != (
                domain.dmn_definition_id,
                domain.dmn_definition_key,
                domain.dmn_definition_version,
                self.database.scope.tenant,
            ):
                raise BindingUnavailableError()
            dmraw = bytes(dm["bytes_"])
            if dmraw != artifact_bytes(material.group_dmn) or sha(dmraw) != domain.dmn_resource_digest:
                raise BindingUnavailableError()
            if b"<!DOCTYPE" in dmraw.upper() or b"<!ENTITY" in dmraw.upper():
                raise BindingUnavailableError()
            dmtree = ET.fromstring(dmraw)
            output_name = candidate[2:-1].split(".")[-1]
            decisions = [
                n for n in dmtree if n.tag.endswith("}decision") and n.get("id") == domain.dmn_definition_key
            ]
            tables = [n for decision in decisions for n in decision if n.tag.endswith("}decisionTable")]
            if len(tables) != 1:
                raise BindingUnavailableError()
            outputs = [n for n in tables[0] if n.tag.endswith("}output")]
            indexes = [i for i, n in enumerate(outputs) if n.get("name") == output_name]
            if len(indexes) != 1:
                raise BindingUnavailableError()
            groups = set()
            for rule in [n for n in tables[0] if n.tag.endswith("}rule")]:
                entries = [n for n in rule if n.tag.endswith("}outputEntry")]
                text = "".join(entries[indexes[0]].itertext()).strip()
                group = strict_loads(text.encode())
                if type(group) is not str:
                    raise BindingUnavailableError()
                groups.add(group)
            if groups != set(domain.groups):
                raise BindingUnavailableError()
        if e.form_source_status == "BPMN_FORMDATA":
            fields = [n.get("id") for n in tasks[0].findall("b:extensionElements/c:formData/c:formField", ns)]
            expected = set(e.allowed_inputs) | (
                {"auditor_id"} if e.form_key in ("auth_decisao", "auth_junta") else set()
            )
            if len(fields) != len(set(fields)) or set(fields) != expected:
                raise BindingUnavailableError()


def binding_columns(q: VerifiedQualification, scope: Scope) -> dict[str, Any]:
    e, m = q.packet.material.entry, q.packet.material
    return dict(
        tenant_=scope.tenant,
        environment_=scope.environment,
        process_=e.process_definition_id,
        process_key_=e.process_definition_key,
        process_version_=str(e.process_definition_version),
        process_digest_=e.process_definition_digest,
        task_key_=e.task_definition_key,
        form_key_=e.form_key,
        form_version_=str(e.form_version),
        form_digest_=e.form_digest,
        binding_digest_=q.binding_digest,
        consumer_digest_=m.consumer.deployed_consumer.digest,
        input_kind_=e.form_key,
        required_group_=m.required_group,
        workload_=scope.workload_ref,
        authority_rev_=q.expected_tenant_revision + 1,
        active_=True,
        # Native MZO_HUMAN_* deadlines are epoch seconds; floor without float rounding.
        valid_until_=(q.valid_until - datetime(1970, 1, 1, tzinfo=UTC)) // timedelta(seconds=1),
    )


class _BindingRecords:
    def __init__(self, connection: BindingConnection) -> None:
        self._db = connection

    async def _receipt(self, c: Any, operation: str) -> InstallationReceipt | InstallationReceiptV2 | None:
        d = self._db.database
        r = await c.fetchrow(
            f"SELECT * FROM {self._db.table('mzo_human_decision_install_receipt')} "
            f"WHERE tenant_=$1 AND installation_=$2 AND operation_=$3",
            d.scope.tenant,
            d.installation_id,
            operation,
        )
        if r is None:
            return None
        return stored_receipt(r)

    async def _readback(self, c: Any, q: VerifiedQualification) -> None:
        db, d = self._db, self._db.database
        values = binding_columns(q, d.scope)
        rows = await c.fetch(
            f"SELECT * FROM {db.table('mzo_human_decision_binding')} "
            f"WHERE tenant_=$1 AND environment_=$2 AND process_=$3 AND task_key_=$4 AND authority_rev_=$5",
            d.scope.tenant,
            d.scope.environment,
            values["process_"],
            values["task_key_"],
            values["authority_rev_"],
        )
        p = await c.fetchrow(
            f"SELECT packet_,digest_ FROM {db.table('mzo_human_decision_qualification')} "
            f"WHERE tenant_=$1 AND installation_=$2 AND authority_rev_=$3 AND digest_=$4",
            d.scope.tenant,
            d.installation_id,
            values["authority_rev_"],
            q.binding_digest,
        )
        if (
            len(rows) != 1
            or dict(rows[0]) != values
            or p is None
            or p["packet_"].encode() != canonical(q.packet)
            or p["digest_"] != q.binding_digest
        ):
            raise BindingUnavailableError()


class DecisionBindingInstaller(_BindingRecords):
    def __init__(self, connection: BindingConnection) -> None:
        if type(connection) is not BindingConnection or connection.mode != "installer":
            raise BindingUnavailableError()
        self._db = connection

    async def _cas_receipt(
        self,
        c: Any,
        operation: str,
        kind: str,
        digest: str,
        revision: int,
        packet_digests: tuple[str, ...] = (),
        cohort: str | None = None,
    ) -> InstallationReceipt | InstallationReceiptV2:
        db, d = self._db, self._db.database
        changed = await c.fetchval(
            f"UPDATE {db.table('mzo_human_tenant')} SET rev_=rev_+1 "
            f"WHERE tenant_=$1 AND rev_=$2 RETURNING rev_",
            d.scope.tenant,
            revision,
        )
        if changed != revision + 1:
            raise BindingUnavailableError()
        await c.execute(
            f"INSERT INTO {db.table('mzo_human_decision_install_receipt')} "
            "(tenant_,installation_,operation_,kind_,request_digest_,"
            "expected_rev_,resulting_rev_,packet_digests_) "
            f"VALUES($1,$2,$3,$4,$5,$6,$7,$8)",
            d.scope.tenant,
            d.installation_id,
            operation,
            kind,
            digest,
            revision,
            revision + 1,
            install_record(packet_digests, cohort),
        )
        result = await self._receipt(c, operation)
        if result is None or (
            result.kind,
            result.request_digest,
            result.expected_revision,
            result.resulting_revision,
        ) != (kind, digest, revision, revision + 1):
            raise BindingUnavailableError()
        if result.packet_digests != packet_digests:
            raise BindingUnavailableError()
        return result

    async def designate(self, authorities: SignedAuthorities) -> InstallationReceipt | InstallationReceiptV2:
        db, d = self._db, self._db.database
        raw = canonical(authorities)
        async with db.transaction() as c:
            revision = await db.revision(c, lock=True)
            doc = db.verifier.authorities(authorities, db.clock())
            prior = await self._receipt(c, doc.operation_id)
            result: InstallationReceipt | InstallationReceiptV2
            if prior is not None:
                if prior.kind != "designate" or prior.request_digest != sha(raw):
                    raise BindingUnavailableError()
                latest = await db.authorities(c)
                if canonical(latest) != raw:
                    raise BindingUnavailableError()
                result = prior
            else:
                generation = await c.fetchval(
                    f"SELECT max(generation_) FROM {db.table('mzo_human_decision_authority')} "
                    f"WHERE tenant_=$1 AND installation_=$2",
                    d.scope.tenant,
                    d.installation_id,
                )
                if (
                    revision != doc.expected_tenant_revision
                    or (generation is not None and doc.generation != generation + 1)
                    or (generation is None and doc.generation != 0)
                ):
                    raise BindingUnavailableError()
                await c.execute(
                    f"INSERT INTO {db.table('mzo_human_decision_authority')} "
                    f"(tenant_,installation_,generation_,document_,digest_) VALUES($1,$2,$3,$4,$5)",
                    d.scope.tenant,
                    d.installation_id,
                    doc.generation,
                    raw.decode(),
                    sha(raw),
                )
                result = await self._cas_receipt(c, doc.operation_id, "designate", sha(raw), revision)
            if canonical(await db.authorities(c)) != raw:
                raise BindingUnavailableError()
            await db.catalog(c)
            db.verifier.authorities(authorities, db.clock())
        return result

    async def install(
        self, packet: QualificationPacket | QualificationPacketV2
    ) -> InstallationReceipt | InstallationReceiptV2:
        return await self.install_batch((packet,))

    async def install_batch(
        self, packets: tuple[QualificationPacket | QualificationPacketV2, ...]
    ) -> InstallationReceipt | InstallationReceiptV2:
        """One to six distinct existing bindings share one frozen operation and tenant CAS."""
        if not packets or len(packets) > (43 if isinstance(packets[0], QualificationPacketV2) else 6):
            raise BindingUnavailableError()
        packets = tuple(sorted(packets, key=lambda p: sha(canonical(p))))
        db, d = self._db, self._db.database
        async with db.transaction() as c:
            revision = await db.revision(c, lock=True)
            authorities = await db.authorities(c)
            qualified = tuple(db.verifier.verify(p, authorities, db.clock()) for p in packets)
            first = qualified[0]
            cohort = packet_cohort(first.packet)
            if any(packet_cohort(q.packet) != cohort for q in qualified):
                raise BindingUnavailableError()
            if len(
                {
                    (
                        q.packet.material.entry.process_definition_key,
                        q.packet.material.entry.task_definition_key,
                    )
                    for q in qualified
                }
            ) != len(qualified):
                raise BindingUnavailableError()
            if any(
                (
                    q.expected_tenant_revision,
                    q.operation_id,
                    q.authority_generation,
                    q.packet.freeze.receipt.freeze_epoch,
                )
                != (
                    first.expected_tenant_revision,
                    first.operation_id,
                    first.authority_generation,
                    first.packet.freeze.receipt.freeze_epoch,
                )
                for q in qualified
            ):
                raise BindingUnavailableError()
            batch = tuple(
                sorted(
                    (batch_member(q.packet.material) for q in qualified),
                    key=lambda m: member_order(m, v2=isinstance(first.packet, QualificationPacketV2)),
                )
            )
            if any(q.packet.freeze.receipt.batch != batch for q in qualified):
                raise BindingUnavailableError()
            digests = tuple(q.binding_digest for q in qualified)
            request_digest = install_digest(digests, cohort)
            prior = await self._receipt(c, first.operation_id)
            if prior is not None:
                if (prior.kind, prior.request_digest, prior.packet_digests) != (
                    "install",
                    request_digest,
                    digests,
                ):
                    raise BindingUnavailableError()
                result = prior
            else:
                if revision != first.expected_tenant_revision:
                    raise BindingUnavailableError()
                for q in qualified:
                    await db.process(c, q.packet.material)
                    raw = canonical(q.packet)
                    await c.execute(
                        f"INSERT INTO {db.table('mzo_human_decision_qualification')} "
                        "(tenant_,installation_,authority_rev_,packet_,digest_) VALUES($1,$2,$3,$4,$5)",
                        d.scope.tenant,
                        d.installation_id,
                        revision + 1,
                        raw.decode(),
                        q.binding_digest,
                    )
                    values = binding_columns(q, d.scope)
                    names = ",".join(values)
                    slots = ",".join(f"${i}" for i in range(1, len(values) + 1))
                    await c.execute(
                        f"INSERT INTO {db.table('mzo_human_decision_binding')} ({names}) VALUES({slots})",
                        *values.values(),
                    )
                result = await self._cas_receipt(
                    c, first.operation_id, "install", request_digest, revision, digests, cohort
                )
            for q in qualified:
                await self._readback(c, q)
                await db.process(c, q.packet.material)
            if await db.revision(c) != result.resulting_revision:
                raise BindingUnavailableError()
            latest = await db.authorities(c)
            await db.catalog(c)
            for packet in packets:
                db.verifier.verify(packet, latest, db.clock())
        return result

    async def revoke(self, value: Revocation) -> InstallationReceipt | InstallationReceiptV2:
        db = self._db
        async with db.transaction() as c:
            revision = await db.revision(c, lock=True)
            r = db.verifier.revocation(value, await db.authorities(c), db.clock())
            digest = sha(canonical(value))
            prior = await self._receipt(c, r.operation_id)
            result: InstallationReceipt | InstallationReceiptV2
            if prior is not None:
                if prior.kind != "revoke" or prior.request_digest != digest:
                    raise BindingUnavailableError()
                result = prior
            else:
                if revision != r.expected_tenant_revision:
                    raise BindingUnavailableError()
                result = await self._cas_receipt(c, r.operation_id, "revoke", digest, revision)
            latest = await db.authorities(c)
            await db.catalog(c)
            db.verifier.revocation(value, latest, db.clock())
        return result

    async def reconcile_batch(
        self, packets: tuple[QualificationPacket | QualificationPacketV2, ...]
    ) -> InstallationReceipt | InstallationReceiptV2:
        return await self.install_batch(packets)

    async def reconcile(
        self, packet: QualificationPacket | QualificationPacketV2
    ) -> InstallationReceipt | InstallationReceiptV2:
        """Fresh real connection; same immutable operation. Never infer success from an ACK."""
        # install's existing-receipt path verifies byte-exact current native state and
        # qualification again. Missing receipt permits only original-revision retry.
        return await self.install(packet)


class PostgresDecisionBindingSource(DecisionBindingSource):
    def __init__(self, connection: BindingConnection) -> None:
        if type(connection) is not BindingConnection or connection.mode != "reader":
            raise BindingUnavailableError()
        self._db = connection
        self.scope = connection.database.scope

    async def _installed_batch(
        self,
        c: Any,
        selected: VerifiedQualification,
        authorities: SignedAuthorities,
        revision: int,
    ) -> tuple[VerifiedQualification, ...]:
        """Independently authenticate the complete finite installation at this revision."""
        db, d = self._db, self._db.database
        records = _BindingRecords(db)
        receipt = await records._receipt(c, selected.operation_id)
        if (
            receipt is None
            or receipt.kind != "install"
            or receipt.operation_id != selected.operation_id
            or receipt.expected_revision != selected.expected_tenant_revision
            or receipt.resulting_revision != revision
            or selected.binding_digest not in receipt.packet_digests
        ):
            raise BindingUnavailableError()
        cohort = packet_cohort(selected.packet)
        if (receipt.cohort_digest if isinstance(receipt, InstallationReceiptV2) else None) != cohort:
            raise BindingUnavailableError()
        # One excess sentinel beyond this explicitly versioned finite cohort.
        limit = 44 if cohort is not None else 7
        packets = await c.fetch(
            f"SELECT packet_,digest_ FROM {db.table('mzo_human_decision_qualification')} "
            f"WHERE tenant_=$1 AND installation_=$2 AND authority_rev_=$3 ORDER BY digest_ LIMIT {limit}",
            self.scope.tenant,
            d.installation_id,
            revision,
        )
        bindings = await c.fetch(
            f"SELECT binding_digest_ FROM {db.table('mzo_human_decision_binding')} "
            "WHERE tenant_=$1 AND environment_=$2 AND authority_rev_=$3 "
            f"ORDER BY binding_digest_ LIMIT {limit}",
            self.scope.tenant,
            self.scope.environment,
            revision,
        )
        if (
            tuple(p["digest_"] for p in packets) != receipt.packet_digests
            or tuple(b["binding_digest_"] for b in bindings) != receipt.packet_digests
        ):
            raise BindingUnavailableError()
        qualified = tuple(
            db.verifier.verify(decode_packet(p["packet_"].encode()), authorities, db.clock()) for p in packets
        )
        if tuple(q.binding_digest for q in qualified) != receipt.packet_digests:
            raise BindingUnavailableError()
        batch = tuple(
            sorted(
                (batch_member(q.packet.material) for q in qualified),
                key=lambda m: member_order(m, v2=isinstance(selected.packet, QualificationPacketV2)),
            )
        )
        if len({(m.process_definition_key, m.task_definition_key) for m in batch}) != len(batch):
            raise BindingUnavailableError()
        for q in qualified:
            if (
                packet_cohort(q.packet) != cohort
                or q.packet.freeze.receipt.batch != batch
                or (
                    q.operation_id,
                    q.expected_tenant_revision,
                    q.authority_generation,
                    q.packet.freeze.receipt.freeze_epoch,
                )
                != (
                    selected.operation_id,
                    selected.expected_tenant_revision,
                    selected.authority_generation,
                    selected.packet.freeze.receipt.freeze_epoch,
                )
            ):
                raise BindingUnavailableError()
            await records._readback(c, q)
            await db.process(c, q.packet.material)
        return qualified

    async def qualify(
        self, principal: HumanPrincipal, task: AuthoritativeTask, authority: CurrentTaskAuthority
    ) -> QualifiedDecisionBinding:
        db, d = self._db, self._db.database
        try:
            async with db.transaction() as c:
                revision = await db.revision(c)
                authorities = await db.authorities(c)
                row = await c.fetchrow(
                    f"""SELECT q.packet_,q.digest_ FROM {db.table("mzo_human_decision_qualification")} q
                    JOIN {db.table("mzo_human_decision_binding")} b
                    ON b.tenant_=q.tenant_ AND b.authority_rev_=q.authority_rev_ AND
          b.binding_digest_=q.digest_
                    WHERE q.tenant_=$1 AND q.installation_=$2 AND q.authority_rev_=$3
                    AND b.environment_=$4 AND b.process_=$5 AND b.task_key_=$6""",
                    self.scope.tenant,
                    d.installation_id,
                    revision,
                    self.scope.environment,
                    task.snapshot.process_definition_id,
                    task.snapshot.task_definition_key,
                )
                if row is None:
                    raise BindingUnavailableError()
                packet = decode_packet(row["packet_"].encode())
                q = db.verifier.verify(packet, authorities, db.clock())
                if q.binding_digest != row["digest_"] or q.expected_tenant_revision + 1 != revision:
                    raise BindingUnavailableError()
                qualified = await self._installed_batch(c, q, authorities, revision)
                native_cohort = await db.native_cohort_current(c, qualified, revision)
                evidence_ref, until = await self._native(c, q, principal, task, authority)
                until = min(until, *(member.valid_until for member in qualified))
                if native_cohort is not None:
                    until = min(until, native_cohort[1])
                if await db.revision(c) != revision or canonical(await db.authorities(c)) != canonical(
                    authorities
                ):
                    raise BindingUnavailableError()
                await db.catalog(c)
                for member in qualified:
                    db.verifier.verify(member.packet, authorities, db.clock())
                if native_cohort != await db.native_cohort_current(c, qualified, revision):
                    raise BindingUnavailableError()
                if db.clock() >= until:
                    raise BindingUnavailableError()
                result = QualifiedDecisionBinding(
                    scope=self.scope,
                    principal=principal,
                    task=task,
                    authority=authority,
                    binding_digest=q.binding_digest,
                    evidence_ref=evidence_ref,
                    valid_until=until,
                )
            return result
        except Exception:
            raise BindingUnavailableError() from None

    async def _native(
        self,
        c: Any,
        q: VerifiedQualification,
        p: HumanPrincipal,
        t: AuthoritativeTask,
        a: CurrentTaskAuthority,
    ) -> tuple[str, datetime]:
        db, d = self._db, self._db.database
        e, s = q.packet.material.entry, t.snapshot
        if (
            p.tenant != self.scope.tenant
            or t.tenant != self.scope.tenant
            or a.tenant != self.scope.tenant
            or not t.active
        ):
            raise BindingUnavailableError()
        for name in (
            "process_definition_id",
            "process_definition_key",
            "process_definition_version",
            "process_definition_digest",
            "task_definition_key",
            "form_key",
            "form_version",
            "form_digest",
        ):
            if getattr(s, name) != getattr(e, name) or getattr(a, name) != getattr(e, name):
                raise BindingUnavailableError()
        if (
            (t.authority_revision, a.authority_revision) != (q.expected_tenant_revision + 1,) * 2
            or a.task_id != s.task_id
            or a.task_revision != s.task_revision
        ):
            raise BindingUnavailableError()
        if (a.principal_ref, a.issuer, a.subject, a.membership_revision) != (
            p.principal_ref,
            p.issuer,
            p.subject,
            p.membership_revision,
        ):
            raise BindingUnavailableError()
        if (
            not a.read_permitted
            or "decision" not in a.permitted_operations
            or "decision" not in s.allowed_actions
            or s.assignee_ref != p.principal_ref
        ):
            raise BindingUnavailableError()
        native = await c.fetchrow(
            f"""SELECT t.rev_,t.assignee_,t.proc_def_id_,t.task_def_key_,t.tenant_id_,t.suspension_state_,
          p.issuer_,p.subject_,p.rev_ AS membership_revision,p.active_,p.valid_until_ AS
          principal_until,p.groups_,
          e.ref_,e.rev_ AS evidence_revision,e.digest_,e.valid_until_ AS evidence_until,e.process_
          FROM {db.table("act_ru_task")} t JOIN {db.table("mzo_human_principal")} p ON
          p.tenant_=t.tenant_id_ AND p.principal_=$2
          JOIN {db.table("mzo_human_evidence")} e ON e.tenant_=t.tenant_id_ AND e.task_=t.id_ WHERE
          t.id_=$1""",
            s.task_id,
            p.principal_ref,
        )
        if native is None:
            raise BindingUnavailableError()
        expected = dict(
            rev_=s.task_revision,
            assignee_=p.principal_ref,
            proc_def_id_=e.process_definition_id,
            task_def_key_=e.task_definition_key,
            tenant_id_=self.scope.tenant,
            suspension_state_=1,
            issuer_=p.issuer,
            subject_=p.subject,
            membership_revision=p.membership_revision,
            active_=True,
            evidence_revision=s.evidence_revision,
            digest_=s.evidence_digest,
            process_=e.process_definition_id,
        )
        if any(native[k] != v for k, v in expected.items()) or (a.evidence_revision, a.evidence_digest) != (
            s.evidence_revision,
            s.evidence_digest,
        ):
            raise BindingUnavailableError()
        groups = strict_loads(native["groups_"].encode())
        if (
            type(groups) is not list
            or any(type(g) is not str for g in groups)
            or len(set(groups)) != len(groups)
        ):
            raise BindingUnavailableError()
        required = q.packet.material.required_group
        links = await c.fetch(
            f"SELECT group_id_ FROM {db.table('act_ru_identitylink')} "
            f"WHERE task_id_=$1 AND tenant_id_=$2 AND type_=$3",
            s.task_id,
            self.scope.tenant,
            "candidate",
        )
        if (
            required not in groups
            or required not in {r["group_id_"] for r in links}
            or required not in s.eligible_candidate_groups
        ):
            raise BindingUnavailableError()
        if t.required_roles != e.required_roles or not any(
            required in m.groups and set(e.required_roles) <= set(m.roles) for m in p.memberships
        ):
            raise BindingUnavailableError()
        row = await c.fetchrow(
            f"""SELECT r.payload_,r.source_,r.publication_,pr.receipt_,pr.digest_,pr.key_fingerprint_
            FROM {db.table("mzo_portal_read_resource")} r
            JOIN {db.table("mzo_portal_read_publication_receipt")} pr
            ON pr.tenant_=r.tenant_ AND pr.environment_=r.environment_ AND pr.engine_=r.engine_
            AND pr.incarnation_=r.incarnation_ AND pr.publication_=r.publication_
            WHERE r.tenant_=$1 AND r.environment_=$2 AND r.engine_=$3
            AND r.incarnation_=$4 AND r.task_=$5 AND NOT EXISTS(
              SELECT 1 FROM {db.table("mzo_portal_read_revocation")} rv
              WHERE rv.tenant_=pr.tenant_ AND rv.environment_=pr.environment_ AND rv.engine_=pr.engine_
              AND rv.incarnation_=pr.incarnation_ AND rv.fingerprint_=pr.key_fingerprint_)""",
            self.scope.tenant,
            self.scope.environment,
            d.engine_name,
            d.database_incarnation,
            s.task_id,
        )
        if row is None:
            raise BindingUnavailableError()
        resource = parse_model(ResourceProjection, strict_loads(row["payload_"].encode()))
        source = parse_model(SourceProvenance, strict_loads(row["source_"].encode()))
        proof = parse_model(PublicationReceipt, strict_loads(row["receipt_"].encode()))
        if (
            row["key_fingerprint_"] != d.resource_publisher_fingerprint
            or proof.kind != "resource"
            or proof.publication_id != row["publication_"]
            or proof.request_digest != row["digest_"]
            or proof.record_digest != sha(canonical(resource))
            or source.observed_at > db.clock()
            or source.valid_until <= db.clock()
            or resource.resource_policy != e.resource_policy
            or resource.classification.policy_ref != e.disclosure_policy.artifact_ref
            or resource.classification.policy_digest != e.disclosure_policy.digest
            or resource.read_only_evidence != s.read_only_evidence
        ):
            raise BindingUnavailableError()

        if (
            resource.task_id,
            resource.process_definition_id,
            resource.process_definition_digest,
            resource.observed_task_revision,
            resource.evidence_ref,
            resource.evidence_revision,
            resource.evidence_digest,
            resource.state,
        ) != (
            s.task_id,
            e.process_definition_id,
            e.process_definition_digest,
            s.task_revision,
            native["ref_"],
            s.evidence_revision,
            s.evidence_digest,
            "complete",
        ):
            raise BindingUnavailableError()
        if (
            resource.required_subject_bindings != t.required_subject_bindings
            or resource.required_consent_scopes != t.required_consent_scopes
            or not set(t.required_subject_bindings) <= set(p.subject_bindings)
        ):
            raise BindingUnavailableError()
        if not set(t.required_consent_scopes) <= set(a.consent_scopes):
            raise BindingUnavailableError()
        grants = [
            g
            for g in resource.positive_grants
            if (g.principal_ref, g.issuer, g.subject, g.membership_revision)
            == (p.principal_ref, p.issuer, p.subject, p.membership_revision)
            and set(resource.required_consent_scopes) <= set(g.consent_scopes)
            and set(a.consent_scopes) <= set(g.consent_scopes)
        ]
        if len(grants) != 1:
            raise BindingUnavailableError()
        until = min(
            q.valid_until,
            t.valid_until,
            a.valid_until,
            resource.valid_until,
            source.valid_until,
            resource.classification.valid_until,
            grants[0].valid_until,
            datetime.fromtimestamp(native["principal_until"], UTC),
            datetime.fromtimestamp(native["evidence_until"], UTC),
        )
        if db.clock() >= until:
            raise BindingUnavailableError()
        return native["ref_"], until


async def install_decision_qualification_schema(
    connection: Any, *, schema: str, owner_role: str, installer_role: str, reader_role: str, engine_role: str
) -> None:
    """Explicit migration only. No runtime call, role creation or authority designation."""
    import re

    names = (schema, owner_role, installer_role, reader_role, engine_role)
    if any(not re.fullmatch(r"[a-z][a-z0-9_]{0,62}", n) for n in names) or len(set(names[1:])) != 4:
        raise BindingUnavailableError()
    if await connection.fetchval("SELECT current_user") != owner_role:
        raise BindingUnavailableError()
    async with connection.transaction():
        await connection.execute(f'SET LOCAL search_path TO "{schema}",pg_catalog')
        sql = (
            files("maezo.portal.engine")
            .joinpath("java/src/main/resources/human-decision-qualification-postgres.sql")
            .read_text()
        )
        await connection.execute(sql)
        for name in RELATIONS:
            table = f'"{schema}"."{name}"'
            await connection.execute(f'GRANT SELECT ON {table} TO "{reader_role}","{installer_role}"')
        for name in IMMUTABLE:
            table = f'"{schema}"."{name}"'
            await connection.execute(
                f'REVOKE ALL ON {table} FROM PUBLIC,"{reader_role}","{engine_role}","{installer_role}"'
            )
            await connection.execute(
                f'GRANT SELECT ON {table} TO "{reader_role}","{engine_role}","{installer_role}"'
            )
            await connection.execute(f'GRANT INSERT ON {table} TO "{installer_role}"')
        await connection.execute(
            f'GRANT UPDATE (rev_) ON "{schema}"."mzo_human_tenant" TO "{installer_role}"'
        )
        await connection.execute(
            f'GRANT USAGE ON SCHEMA "{schema}" TO "{reader_role}","{installer_role}","{engine_role}"'
        )

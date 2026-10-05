"""Qualified PostgreSQL source authority, not a runtime authority installer.

Only an independently installed validation writer admits evidence. Publisher and
consumer roles cannot write validation proofs. No role/schema creation or DDL here.
"""

from __future__ import annotations

import hashlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from maezo.gateway.external_cases.models import ExternalCaseError
from maezo.gateway.external_cases.postgres import transaction

from .authority_source import (
    AuthoritySourceError,
    CustodyReceipt,
    SourceBinding,
    SourceDescriptor,
    descriptor_digest,
    pack,
    parse_binding,
)

if TYPE_CHECKING:
    from .contract_authority import ContractReadScope, ContractSnapshot, ContractSnapshotProof


class PostgresProviderAuthoritySource:
    def __init__(self, engine: AsyncEngine, descriptor: SourceDescriptor, *, publisher: bool = False) -> None:
        self.engine = engine
        # Reparse constructed/copied descriptors. A model instance is not admission.
        self.descriptor = SourceDescriptor.model_validate(descriptor.model_dump())
        self.descriptor_sha256 = descriptor_digest(self.descriptor)
        self.publisher = publisher
        self.schema = '"' + self.descriptor.schema_name + '"'

    async def qualify(self, connection: AsyncConnection) -> None:
        d = self.descriptor
        if descriptor_digest(d) != self.descriptor_sha256:
            raise AuthoritySourceError("AUTHORITY_UNPROVEN")
        role = d.publisher_role if self.publisher else d.reader_role
        if d.valid_until <= datetime.now(UTC) or self.engine.dialect.name != "postgresql":
            raise AuthoritySourceError("SOURCE_UNAVAILABLE")
        info = (
            (
                await connection.execute(
                    text("""
            SELECT session_user::text AS login,current_user::text AS effective,
            (SELECT oid FROM pg_database WHERE datname=current_database()) AS database_oid,
            pg_my_temp_schema() AS temp_oid,
            current_setting('session_replication_role') IN ('origin','local') AS guards_fire,
            COALESCE((SELECT ssl FROM pg_stat_ssl WHERE pid=pg_backend_pid()),false) AS tls
        """)
                )
            )
            .mappings()
            .one()
        )
        if (
            info["login"] != role
            or info["effective"] != role
            or info["database_oid"] != d.database_oid
            or info["temp_oid"] != 0
            or info["tls"] is not True
            or info["guards_fire"] is not True
        ):
            raise AuthoritySourceError("SOURCE_UNAVAILABLE")
        for login in (d.owner_role, d.publisher_role, d.validator_role, d.reader_role):
            found = (
                (
                    await connection.execute(
                        text("""
                SELECT rolsuper,rolcreatedb,rolcreaterole,rolbypassrls,rolreplication,
                EXISTS(SELECT 1 FROM pg_auth_members WHERE member=r.oid) AS member,
                has_parameter_privilege(r.oid,'session_replication_role','SET') AS can_disable_triggers,
                has_schema_privilege(r.oid,n.oid,'CREATE') AS can_create,
                has_database_privilege(r.oid,current_database(),'TEMP') AS can_temp
                FROM pg_roles r JOIN pg_namespace n ON n.nspname=:schema WHERE r.rolname=:role
            """),
                        {"schema": d.schema_name, "role": login},
                    )
                )
                .mappings()
                .one_or_none()
            )
            if found is None or any(
                found[k]
                for k in (
                    "rolsuper",
                    "rolcreatedb",
                    "rolcreaterole",
                    "rolbypassrls",
                    "rolreplication",
                    "member",
                    "can_disable_triggers",
                )
            ):
                raise AuthoritySourceError("SOURCE_UNAVAILABLE")
            if login != d.owner_role and (found["can_create"] or found["can_temp"]):
                raise AuthoritySourceError("SOURCE_UNAVAILABLE")
        for name, pin in sorted(d.relation_pins.items()):
            found = (
                (
                    await connection.execute(
                        text("""
                SELECT c.oid,c.relkind::text AS kind,c.relrowsecurity,
                EXISTS(SELECT 1 FROM aclexplode(COALESCE(c.relacl,acldefault('r',c.relowner))) a
                  WHERE a.grantee<>c.relowner AND a.grantee NOT IN
                  (SELECT oid FROM pg_roles WHERE rolname IN (:reader,:publisher,:validator))) AS extra_acl,
                EXISTS(SELECT 1 FROM pg_attribute at, LATERAL aclexplode(at.attacl) a
                  WHERE at.attrelid=c.oid AND a.grantee<>c.relowner) AS column_acl,
                n.oid AS schema_oid,pg_get_userbyid(n.nspowner) AS schema_owner,
                pg_get_userbyid(c.relowner) AS relation_owner,
                has_table_privilege(:reader,c.oid,'SELECT') AS reader_select,
                (has_table_privilege(:reader,c.oid,'INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER')
                 OR has_any_column_privilege(:reader,c.oid,'INSERT,UPDATE,REFERENCES')) AS reader_write,
                has_table_privilege(:publisher,c.oid,'SELECT') AS publisher_select,
                (has_table_privilege(:publisher,c.oid,'UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER')
                 OR has_any_column_privilege(:publisher,c.oid,'UPDATE,REFERENCES')) AS publisher_mutate,
                has_table_privilege(:publisher,c.oid,'INSERT') AS publisher_insert,
                (has_table_privilege(:validator,c.oid,'INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER')
                 OR has_any_column_privilege(:validator,c.oid,'INSERT,UPDATE,REFERENCES')) AS validator_write,
                has_table_privilege(:validator,c.oid,'DELETE,TRUNCATE,REFERENCES,TRIGGER') AS validator_extra,
                EXISTS(SELECT 1 FROM aclexplode(COALESCE(c.relacl,acldefault('r',c.relowner)))
                  a WHERE a.grantee=0)
                  AS public_table,
                EXISTS(SELECT 1 FROM aclexplode(COALESCE(n.nspacl,acldefault('n',n.nspowner)))
                  a WHERE a.grantee=0)
                  AS public_schema
                FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
                WHERE n.nspname=:schema AND c.relname=:name
            """),
                        {
                            "reader": d.reader_role,
                            "publisher": d.publisher_role,
                            "validator": d.validator_role,
                            "schema": d.schema_name,
                            "name": name,
                        },
                    )
                )
                .mappings()
                .one_or_none()
            )
            if (
                found is None
                or found["oid"] != pin.oid
                or found["schema_oid"] != d.schema_oid
                or found["kind"] != "r"
                or found["relation_owner"] != d.owner_role
                or found["schema_owner"] != d.owner_role
                or found["relrowsecurity"]
                or found["extra_acl"]
                or found["column_acl"]
                or not found["reader_select"]
                or found["reader_write"]
                or not found["publisher_select"]
                or found["public_table"]
                or found["public_schema"]
            ):
                raise AuthoritySourceError("SOURCE_UNAVAILABLE")
            if found["validator_extra"] or (
                name not in {"source_binding", "authority_proof"} and found["validator_write"]
            ):
                raise AuthoritySourceError("SOURCE_UNAVAILABLE")
            if name in {"source_binding", "authority_proof", "publication", "instrument_head"} and (
                found["publisher_insert"] or found["publisher_mutate"]
            ):
                raise AuthoritySourceError("SOURCE_UNAVAILABLE")
            if name == "evidence" and (not found["publisher_insert"] or found["publisher_mutate"]):
                raise AuthoritySourceError("SOURCE_UNAVAILABLE")
        function = (
            (
                await connection.execute(
                    text("""
            SELECT p.oid,pg_get_functiondef(p.oid) AS definition,
            pg_get_userbyid(p.proowner) AS owner,p.prosecdef,
            EXISTS(SELECT 1 FROM aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner)))
                  a WHERE a.grantee=0)
                  AS public_execute
            FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
            WHERE n.nspname=:schema AND p.proname='immutable_source_record' AND p.pronargs=0
        """),
                    {"schema": d.schema_name},
                )
            )
            .mappings()
            .one_or_none()
        )
        if (
            function is None
            or function["oid"] != d.immutable_function_oid
            or function["owner"] != d.owner_role
            or function["prosecdef"]
            or function["public_execute"]
            or hashlib.sha256(function["definition"].encode()).hexdigest() != d.immutable_function_sha256
        ):
            raise AuthoritySourceError("SOURCE_UNAVAILABLE")
        publication = (
            (
                await connection.execute(
                    text("""
            SELECT p.oid,pg_get_functiondef(p.oid) AS definition,
            pg_get_userbyid(p.proowner) AS owner,p.prosecdef,p.proconfig,
            has_function_privilege(:publisher,p.oid,'EXECUTE') AS publisher_execute,
            has_function_privilege(:reader,p.oid,'EXECUTE') AS reader_execute,
            EXISTS(SELECT 1 FROM aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) a
              WHERE a.grantee<>p.proowner
                AND a.grantee<>(SELECT oid FROM pg_roles WHERE rolname=:publisher)) AS extra_acl
            FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
            WHERE n.nspname=:schema AND p.proname='publish_validated' AND p.pronargs=4
        """),
                    dict(schema=d.schema_name, publisher=d.publisher_role, reader=d.reader_role),
                )
            )
            .mappings()
            .one_or_none()
        )
        if (
            publication is None
            or publication["oid"] != d.publication_function_oid
            or publication["owner"] != d.owner_role
            or publication["prosecdef"] is not True
            or publication["proconfig"] != ["search_path=" + d.schema_name]
            or not publication["publisher_execute"]
            or publication["reader_execute"]
            or publication["extra_acl"]
            or hashlib.sha256(publication["definition"].encode()).hexdigest() != d.publication_function_sha256
        ):
            raise AuthoritySourceError("SOURCE_UNAVAILABLE")
        history = (
            (
                await connection.execute(
                    text("""
            SELECT p.oid,pg_get_functiondef(p.oid) AS definition,
            pg_get_userbyid(p.proowner) AS owner,p.prosecdef,
            EXISTS(SELECT 1 FROM aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) a
              WHERE a.grantee=0) AS public_execute
            FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
            WHERE n.nspname=:schema AND p.proname='protect_authority_history' AND p.pronargs=0
        """),
                    dict(schema=d.schema_name),
                )
            )
            .mappings()
            .one_or_none()
        )
        if (
            history is None
            or history["oid"] != d.history_function_oid
            or history["owner"] != d.owner_role
            or history["prosecdef"]
            or history["public_execute"]
            or hashlib.sha256(history["definition"].encode()).hexdigest() != d.history_function_sha256
        ):
            raise AuthoritySourceError("SOURCE_UNAVAILABLE")
        # Exact profile of the admitted source DDL. A matching function alone
        # cannot prove a guard fires: WHEN/UPDATE OF/args and additional triggers
        # can bypass history or alter an effect. Census also covers the CAS head,
        # whose declared profile intentionally has no trigger.
        expected = {
            "source_binding": {
                "binding_history": (d.history_function_oid, 19),
                "binding_no_delete": (d.immutable_function_oid, 42),
            },
            "authority_proof": {
                "proof_history": (d.history_function_oid, 19),
                "proof_no_delete": (d.immutable_function_oid, 42),
            },
            "evidence": {"evidence_immutable": (d.immutable_function_oid, 58)},
            "publication": {"publication_immutable": (d.immutable_function_oid, 58)},
            "instrument_head": {},
        }
        fk_slots = await self._declared_fk_slots(connection)
        for relation, profile in expected.items():
            triggers = (
                (
                    await connection.execute(
                        text("""
                SELECT tgname::text AS tgname,tgfoid,tgtype,tgenabled::text AS tgenabled,
                tgconstrrelid,tgconstrindid,
                tgisinternal,tgqual IS NULL AS unconditional,tgnargs,
                octet_length(tgargs)=0 AS no_args,tgattr::text AS tgattr,
                tgconstraint,tgdeferrable,tginitdeferred,
                (tgoldtable IS NULL AND tgnewtable IS NULL) AS no_transition
                FROM pg_trigger WHERE tgrelid=:relation
            """),
                        {"relation": d.relation_pins[relation].oid},
                    )
                )
                .mappings()
                .all()
            )
            user = [t for t in triggers if t["tgisinternal"] is False]
            internal = [t for t in triggers if t["tgisinternal"] is True]
            if len(user) + len(internal) != len(triggers):
                raise AuthoritySourceError("SOURCE_UNAVAILABLE")
            actual_ri = {
                (t["tgconstraint"], t["tgfoid"], t["tgtype"], t["tgconstrrelid"], t["tgconstrindid"])
                for t in internal
            }
            if (
                len(internal) != len(fk_slots[d.relation_pins[relation].oid])
                or actual_ri != fk_slots[d.relation_pins[relation].oid]
            ):
                raise AuthoritySourceError("SOURCE_UNAVAILABLE")
            for t in internal:
                if (
                    t["tgenabled"] not in {"O", "A"}
                    or t["unconditional"] is not True
                    or t["tgnargs"] != 0
                    or t["no_args"] is not True
                    or t["tgattr"] != ""
                    or t["tgdeferrable"] is not False
                    or t["tginitdeferred"] is not False
                    or t["no_transition"] is not True
                ):
                    raise AuthoritySourceError("SOURCE_UNAVAILABLE")
            if len(user) != len(profile) or {t["tgname"] for t in user} != set(profile):
                raise AuthoritySourceError("SOURCE_UNAVAILABLE")
            for trigger in user:
                if (
                    (trigger["tgfoid"], trigger["tgtype"]) != profile[trigger["tgname"]]
                    or trigger["tgenabled"] not in {"O", "A"}
                    or trigger["tgisinternal"] is not False
                    or trigger["unconditional"] is not True
                    or trigger["tgnargs"] != 0
                    or trigger["no_args"] is not True
                    or trigger["tgattr"] != ""
                    or trigger["tgconstraint"] != 0
                    or trigger["tgdeferrable"] is not False
                    or trigger["tginitdeferred"] is not False
                    or trigger["no_transition"] is not True
                ):
                    raise AuthoritySourceError("SOURCE_UNAVAILABLE")

    async def _declared_fk_slots(
        self, connection: AsyncConnection
    ) -> dict[int, set[tuple[int, int, int, int, int]]]:
        """Only four validated v1 FK edges and their native RI quartet are admitted."""
        d = self.descriptor
        oids = {name: pin.oid for name, pin in d.relation_pins.items()}
        key = ("tenant_ref", "legal_entity_ref", "provider_ref", "instrument_ref", "business_revision")
        topology = {
            (oids["authority_proof"], ("binding_ref",), oids["source_binding"], ("binding_ref",)),
            (oids["publication"], ("proof_ref",), oids["authority_proof"], ("proof_ref",)),
            (oids["publication"], key, oids["evidence"], key),
            (oids["instrument_head"], ("publication_ref",), oids["publication"], ("publication_ref",)),
        }
        params = {"r" + str(i): oid for i, oid in enumerate(oids.values())}
        refs = ",".join(":" + name for name in params)
        constraints = (
            (
                await connection.execute(
                    text(
                        """
            SELECT c.oid,c.connamespace,c.conrelid,c.confrelid,c.conindid,
            c.confupdtype::text AS update_action,c.confdeltype::text AS delete_action,
            c.confmatchtype::text AS match_type,c.condeferrable,c.condeferred,c.convalidated,
            c.conislocal,c.coninhcount,c.conparentid,
            ARRAY(SELECT a.attname::text FROM unnest(c.conkey) WITH ORDINALITY k(num,ord)
                  JOIN pg_attribute a ON a.attrelid=c.conrelid AND a.attnum=k.num ORDER BY k.ord) AS columns,
            ARRAY(SELECT a.attnotnull FROM unnest(c.conkey) WITH ORDINALITY k(num,ord)
                  JOIN pg_attribute a ON a.attrelid=c.conrelid AND a.attnum=k.num ORDER BY k.ord) AS notnull,
            ARRAY(SELECT a.attname::text FROM unnest(c.confkey) WITH ORDINALITY k(num,ord)
                  JOIN pg_attribute a ON a.attrelid=c.confrelid AND a.attnum=k.num
                  ORDER BY k.ord) AS ref_columns,
            i.indrelid,i.indisunique,i.indisvalid,i.indisready,i.indimmediate,
            (i.indexprs IS NULL AND i.indpred IS NULL) AS plain_index,
            ARRAY(SELECT a.attname::text FROM unnest(i.indkey) WITH ORDINALITY k(num,ord)
                  JOIN pg_attribute a ON a.attrelid=i.indrelid AND a.attnum=k.num
                  WHERE k.ord<=i.indnkeyatts ORDER BY k.ord) AS index_columns
            FROM pg_constraint c LEFT JOIN pg_index i ON i.indexrelid=c.conindid
            WHERE c.contype='f' AND (c.conrelid IN ("""
                        + refs
                        + ") OR c.confrelid IN ("
                        + refs
                        + "))"
                    ),
                    params,
                )
            )
            .mappings()
            .all()
        )
        observed = {
            (c["conrelid"], tuple(c["columns"]), c["confrelid"], tuple(c["ref_columns"])) for c in constraints
        }
        if len(constraints) != len(topology) or observed != topology:
            raise AuthoritySourceError("SOURCE_UNAVAILABLE")
        for c in constraints:
            if (
                c["connamespace"] != d.schema_oid
                or c["update_action"] != "a"
                or c["delete_action"] != "a"
                or c["match_type"] != "s"
                or c["condeferrable"]
                or c["condeferred"]
                or c["convalidated"] is not True
                or c["conislocal"] is not True
                or c["coninhcount"] != 0
                or c["conparentid"] != 0
                or not c["notnull"]
                or not all(c["notnull"])
                or c["indrelid"] != c["confrelid"]
                or c["conindid"] <= 0
                or not all(
                    c[k] is True
                    for k in ("indisunique", "indisvalid", "indisready", "indimmediate", "plain_index")
                )
                or tuple(c["index_columns"]) != tuple(c["ref_columns"])
            ):
                raise AuthoritySourceError("SOURCE_UNAVAILABLE")
        names = ("RI_FKey_check_ins", "RI_FKey_check_upd", "RI_FKey_noaction_del", "RI_FKey_noaction_upd")
        functions = (
            (
                await connection.execute(
                    text("""
            SELECT p.oid,p.proname::text AS name,p.prosrc,p.prosecdef,p.pronargs,
            p.prorettype='pg_catalog.trigger'::regtype AS returns_trigger,l.lanname::text AS language
            FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
            JOIN pg_language l ON l.oid=p.prolang
            WHERE n.nspname='pg_catalog' AND p.proname IN
            ('RI_FKey_check_ins','RI_FKey_check_upd','RI_FKey_noaction_del','RI_FKey_noaction_upd')
        """)
                )
            )
            .mappings()
            .all()
        )
        if len(functions) != 4 or {f["name"] for f in functions} != set(names):
            raise AuthoritySourceError("SOURCE_UNAVAILABLE")
        if any(
            f["prosrc"] != f["name"]
            or f["language"] != "internal"
            or f["prosecdef"]
            or f["pronargs"] != 0
            or f["returns_trigger"] is not True
            for f in functions
        ):
            raise AuthoritySourceError("SOURCE_UNAVAILABLE")
        ri = {f["name"]: f["oid"] for f in functions}
        slots: dict[int, set[tuple[int, int, int, int, int]]] = {oid: set() for oid in oids.values()}
        for c in constraints:
            child, parent, constraint, index = c["conrelid"], c["confrelid"], c["oid"], c["conindid"]
            slots[child].update(
                {
                    (constraint, ri["RI_FKey_check_ins"], 5, parent, index),
                    (constraint, ri["RI_FKey_check_upd"], 17, parent, index),
                }
            )
            slots[parent].update(
                {
                    (constraint, ri["RI_FKey_noaction_del"], 9, child, index),
                    (constraint, ri["RI_FKey_noaction_upd"], 17, child, index),
                }
            )
        return slots

    @asynccontextmanager
    async def _connection(self, seconds: float) -> AsyncIterator[AsyncConnection]:
        from .models import CapabilityRefusalReason

        reason = None
        try:
            async with transaction(self.engine, seconds) as conn:
                try:
                    await self.qualify(conn)
                    yield conn
                    if self.descriptor.valid_until <= datetime.now(UTC):
                        raise AuthoritySourceError("SOURCE_UNAVAILABLE")
                except AuthoritySourceError as exc:
                    # Reuse awaited commit/rollback/invalidation boundary without
                    # losing this domain's bounded refusal through its SQL sanitizer.
                    reason = exc.reason
                    raise ExternalCaseError("denied") from None
        except ExternalCaseError as exc:
            if reason is not None and exc.code == "denied":
                raise AuthoritySourceError(reason) from None
            if exc.code == "denied":
                raise AuthoritySourceError("AUTHORITY_UNPROVEN") from None
            if exc.code == "conflict":
                raise AuthoritySourceError("STALE_REVISION") from None
            if exc.code == "invalid":
                raise AuthoritySourceError("CONTRACT_MISMATCH") from None
            raise AuthoritySourceError(CapabilityRefusalReason.SOURCE_UNAVAILABLE) from None

    def _scope(self, scope: ContractReadScope) -> None:
        from .contract_authority import ContractReadScope

        try:
            ContractReadScope.model_validate(scope)
        except Exception:
            raise AuthoritySourceError("CONTRACT_MISMATCH") from None
        if (
            scope.tenant_ref != self.descriptor.tenant_ref
            or scope.legal_entity_ref != self.descriptor.legal_entity_ref
            or scope.source_authority_ref != self.descriptor.source_authority_ref
        ):
            raise AuthoritySourceError("AUTHORITY_UNPROVEN")

    async def receive(self, snapshot: ContractSnapshot, *, timeout_seconds: float) -> CustodyReceipt:
        from .contract_authority import ContractSnapshot

        if not self.publisher:
            raise AuthoritySourceError("AUTHORITY_UNPROVEN")
        snapshot = ContractSnapshot.model_validate(snapshot.model_dump())
        d = self.descriptor
        if snapshot.tenant_ref != d.tenant_ref or snapshot.legal_entity_ref != d.legal_entity_ref:
            raise AuthoritySourceError("AUTHORITY_UNPROVEN")
        raw = pack(snapshot)
        digest = hashlib.sha256(raw).hexdigest()
        params: dict[str, Any] = dict(
            tenant=snapshot.tenant_ref,
            entity=snapshot.legal_entity_ref,
            provider=snapshot.provider_ref,
            instrument=snapshot.contract_instrument_ref,
            revision=snapshot.business_revision,
            evidence=snapshot.evidence_ref,
            payload=raw,
            digest=digest,
        )
        async with self._connection(timeout_seconds) as conn:
            await conn.execute(
                text(f"""INSERT INTO {self.schema}.evidence
                (tenant_ref,legal_entity_ref,provider_ref,instrument_ref,business_revision,evidence_ref,snapshot_bytes,snapshot_digest)
                VALUES (:tenant,:entity,:provider,:instrument,:revision,:evidence,:payload,:digest)
                ON CONFLICT DO NOTHING"""),
                params,
            )
            row = (
                (
                    await conn.execute(
                        text(f"""SELECT snapshot_digest,snapshot_bytes,evidence_ref,received_at
                FROM {self.schema}.evidence WHERE tenant_ref=:tenant
                AND legal_entity_ref=:entity
                AND provider_ref=:provider
                AND instrument_ref=:instrument AND business_revision=:revision"""),
                        params,
                    )
                )
                .mappings()
                .one_or_none()
            )
            if (
                row is None
                or row["snapshot_digest"] != digest
                or bytes(row["snapshot_bytes"]) != raw
                or row["evidence_ref"] != snapshot.evidence_ref
            ):
                raise AuthoritySourceError("CONTRACT_MISMATCH")
            receipt = CustodyReceipt(
                state="received",
                evidence_ref=row["evidence_ref"],
                snapshot_sha256=digest,
                received_at=row["received_at"],
            )
        return receipt

    async def publish_validated(
        self,
        scope: ContractReadScope,
        *,
        business_revision: str,
        proof_ref: str,
        expected_head_revision: int,
        timeout_seconds: float,
    ) -> ContractSnapshot:
        if not self.publisher or scope.contract_instrument_ref is None:
            raise AuthoritySourceError("AUTHORITY_UNPROVEN")
        if type(expected_head_revision) is not int or expected_head_revision < 0:
            raise AuthoritySourceError("CONTRACT_MISMATCH")
        self._scope(scope)
        params = dict(
            scope=pack(scope).decode(),
            revision=business_revision,
            proof=proof_ref,
            expected=expected_head_revision,
        )
        async with self._connection(timeout_seconds) as conn:
            await conn.execute(
                text(
                    f"SELECT {self.schema}.publish_validated(CAST(:scope AS jsonb),"
                    ":revision,:proof,:expected)"
                ),
                params,
            )
            observed, _, _ = await self._observe(conn, scope)
        return observed

    async def _observe(
        self, conn: AsyncConnection, scope: ContractReadScope
    ) -> tuple[ContractSnapshot, Any, SourceBinding]:
        from .contract_authority import ContractSnapshot

        self._scope(scope)
        params: dict[str, Any] = dict(
            tenant=scope.tenant_ref,
            entity=scope.legal_entity_ref,
            provider=scope.provider_ref,
            instrument=scope.contract_instrument_ref,
        )
        rows = (
            (
                await conn.execute(
                    text(f"""SELECT e.snapshot_bytes,e.snapshot_digest AS evidence_digest,
            p.snapshot_digest AS publication_digest,
            p.receipt_ref,p.publication_ref,
            a.*,a.snapshot_digest AS proof_digest,b.binding_bytes,b.binding_digest,
            b.revoked AS binding_revoked
            FROM {self.schema}.instrument_head h JOIN {self.schema}.publication p
                ON p.publication_ref=h.publication_ref

                AND p.tenant_ref=h.tenant_ref
                AND p.legal_entity_ref=h.legal_entity_ref
                AND p.provider_ref=h.provider_ref
            AND p.instrument_ref=h.instrument_ref AND p.business_revision=h.business_revision
            JOIN {self.schema}.evidence e ON e.tenant_ref=p.tenant_ref
                AND e.legal_entity_ref=p.legal_entity_ref

                AND e.provider_ref=p.provider_ref
                AND e.instrument_ref=p.instrument_ref
                AND e.business_revision=p.business_revision
            JOIN {self.schema}.authority_proof a ON a.proof_ref=p.proof_ref
            JOIN {self.schema}.source_binding b ON b.binding_ref=a.binding_ref
            WHERE h.tenant_ref=:tenant AND h.legal_entity_ref=:entity AND h.provider_ref=:provider
            AND (CAST(:instrument AS text) IS NULL OR h.instrument_ref=:instrument)"""),
                    params,
                )
            )
            .mappings()
            .all()
        )
        if len(rows) != 1:
            raise AuthoritySourceError("SOURCE_UNAVAILABLE")
        row = rows[0]
        raw = bytes(row["snapshot_bytes"])
        try:
            snapshot = ContractSnapshot.model_validate_json(raw)
            if pack(snapshot) != raw or hashlib.sha256(raw).hexdigest() != row["evidence_digest"]:
                raise ValueError("integrity")
        except Exception:
            raise AuthoritySourceError("CONTRACT_MISMATCH") from None
        binding = parse_binding(bytes(row["binding_bytes"]), row["binding_digest"])
        expected = {
            k: getattr(scope, k)
            for k in (
                "tenant_ref",
                "legal_entity_ref",
                "provider_ref",
                "principal_ref",
                "task_ref",
                "source_authority_ref",
                "policy_revision",
                "purpose_policy_ref",
                "purpose_ref",
                "data_classification",
            )
        }
        if (
            any(getattr(binding, k) != v for k, v in expected.items())
            or row["binding_revoked"]
            or binding.source_contract_publication_ref != self.descriptor.source_contract_publication_ref
            or scope.clause_purpose not in binding.clause_purposes
        ):
            raise AuthoritySourceError("AUTHORITY_UNPROVEN")
        now = datetime.now(UTC)
        if (
            row["state"] not in {"validated", "enabled"}
            or row["checked_at"] > now
            or row["valid_until"] <= now
            or binding.valid_until <= now
            or snapshot.declared_at > now
            or snapshot.valid_from > now
            or snapshot.valid_until <= now
            or snapshot.admission_state != row["state"]
            or snapshot.evidence_ref != row["evidence_ref"]
            or row["proof_digest"] != row["evidence_digest"]
            or row["publication_digest"] != row["evidence_digest"]
            or snapshot.source_revision_ref != row["source_revision_ref"]
            or snapshot.currentness_ref != row["currentness_ref"]
            or snapshot.authority_receipt_ref != row["receipt_ref"]
            or snapshot.source_publication_ref != row["publication_ref"]
            or snapshot.tenant_ref != scope.tenant_ref
            or snapshot.legal_entity_ref != scope.legal_entity_ref
            or snapshot.provider_ref != scope.provider_ref
            or snapshot.valid_until
            > min(row["valid_until"], binding.valid_until, self.descriptor.valid_until)
        ):
            raise AuthoritySourceError("SOURCE_UNAVAILABLE")
        if (
            scope.expected_business_revision is not None
            and snapshot.business_revision != scope.expected_business_revision
        ):
            raise AuthoritySourceError("STALE_REVISION")
        return snapshot, row, binding

    async def resolve(self, scope: ContractReadScope, *, timeout_seconds: float) -> ContractSnapshot:
        async with self._connection(timeout_seconds) as conn:
            snapshot, _, _ = await self._observe(conn, scope)
        return snapshot

    async def verify_snapshot(
        self, scope: ContractReadScope, snapshot: ContractSnapshot
    ) -> ContractSnapshotProof:
        from .contract_authority import ContractSnapshotProof

        async with self._connection(5) as conn:
            observed, row, _ = await self._observe(conn, scope)
            if pack(snapshot) != pack(observed):
                raise AuthoritySourceError("CONTRACT_MISMATCH")
            # Exact typed proof construction adapted to PW2's frozen DTO, not caller fields.
            return ContractSnapshotProof(
                scope=scope,
                snapshot_sha256=hashlib.sha256(pack(observed)).hexdigest(),
                snapshot_key=observed.snapshot_key(),
                authority_receipt_ref=row["receipt_ref"],
                source_publication_ref=row["publication_ref"],
                currentness_ref=row["currentness_ref"],
                checked_at=datetime.now(UTC),
                valid_until=observed.valid_until,
            )

    async def check_current(
        self, scope: ContractReadScope, proof: ContractSnapshotProof
    ) -> ContractSnapshotProof:
        snapshot = await self.resolve(scope, timeout_seconds=5)
        current = await self.verify_snapshot(scope, snapshot)
        if (
            proof.scope != scope
            or proof.snapshot_sha256 != current.snapshot_sha256
            or proof.snapshot_key != current.snapshot_key
        ):
            raise AuthoritySourceError("STALE_REVISION")
        return current

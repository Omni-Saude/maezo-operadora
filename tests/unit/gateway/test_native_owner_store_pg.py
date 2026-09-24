"""Real PG16 receipt storage mechanics; finite records do not establish D authority."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql

from maezo.gateway import native_owner_store as store
from maezo.platform.engine_bootstrap import controller_storage as s
from maezo.platform.engine_bootstrap import native_owner_contracts as n
from tests.unit.platform.engine_bootstrap.test_native_owner_contracts import record_vector

pytestmark = pytest.mark.integration
ROOT = Path(__file__).parents[3]


@pytest.fixture
def database():
    dsn = os.environ.get("MAEZO_TEST_DATABASE_URL")
    if not dsn:
        # Mesma convencao de tests/unit/gateway/test_amh_consent_revision.py: sem DSN
        # descartavel o teste SKIP honesto ("COULD NOT VERIFY") em vez de ERRAR — o
        # release-floor roda `pytest tests/` SEM o filtro `-m "not integration"` do
        # job quality, entao um assert aqui falha o floor em todo ambiente sem PG
        # (a lane de engine continua executando-o de verdade com o DSN presente).
        pytest.skip("COULD NOT VERIFY: MAEZO_TEST_DATABASE_URL required for actual PostgreSQL fence")
    name = "native_owner_receipt_" + uuid4().hex
    with psycopg.connect(dsn, autocommit=True, connect_timeout=5) as admin:
        # 16 (Aurora do Terraform) ou 17 (RDS do dev e compose, 24/09/2026).
        assert 160000 <= admin.info.server_version < 180000
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
        oid = admin.execute("SELECT oid FROM pg_database WHERE datname=%s", (name,)).fetchone()[0]
        try:
            with (
                psycopg.connect(dsn, dbname=name, autocommit=True, connect_timeout=5) as writer,
                psycopg.connect(dsn, dbname=name, autocommit=True, connect_timeout=5) as observer,
            ):
                owner = writer.execute("SELECT current_user").fetchone()[0]
                template = (
                    ROOT
                    / "src/maezo/portal/engine/java/src/main/resources"
                    / "native-owner-qualification-v1-postgres.sql"
                ).read_text()
                writer.execute(template.replace("__SCHEMA_OWNER__", sql.Identifier(owner).as_string(writer)))
                yield writer, observer
        finally:
            assert admin.execute("SELECT oid FROM pg_database WHERE datname=%s", (name,)).fetchone() == (oid,)
            # Ordinary DROP refuses any leftover connection; never FORCE or terminate others.
            admin.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(name)))


@pytest.mark.parametrize("subtransaction", ["savepoint", "plpgsql_exception"])
def test_prior_commit_stamp_rejects_own_subtransaction_and_survives_replay(database, subtransaction):
    writer, observer = database
    value = record_vector()
    raw = s.canonical(value)
    request = s.decode64(value["request_bytes"])
    q = n.request(request)
    binding = n.qualification_from_receipt(raw)
    wire = b"{}"
    checksum = hashlib.sha256(wire).hexdigest()
    writer.execute(
        """INSERT INTO maezo_native_owner_v1.installation
        (singleton,installation_id,source_manifest_sha256,ddl_sha256,database_oid,schema_oid,
         schema_owner_oid,owner_login_oid,owner_login_name,catalog_bytes,catalog_sha256,
         recorded_before_commit_at_ms)
        SELECT true,%s::uuid,%s,%s,d.oid,n.oid,r.oid,r.oid,current_user,%s,%s,0
        FROM pg_database d,pg_namespace n,pg_roles r
        WHERE d.datname=current_database() AND n.nspname='maezo_native_owner_v1'
          AND r.rolname=current_user""",
        (q["owner_installation_id"], checksum, checksum, wire, checksum),
    )
    # Deliberately supply a false creator stamp: only the installed trigger may set it.
    fields = (
        q["native_owner_operation_id"],
        "1",
        q["owner_installation_id"],
        q["native_installation_receipt_sha256"],
        q["d_preparation_id"],
        request,
        s.digest(request),
        raw,
        s.digest(raw),
        binding,
        s.digest(binding),
        0,
    )
    insert = sql.SQL(
        """INSERT INTO maezo_native_owner_v1.principal_qualification_receipt
        (native_owner_operation_id,creator_top_xid,owner_installation_id,
         native_installation_receipt_sha256,d_preparation_id,request_bytes,request_sha256,
         result_bytes,result_sha256,qualification_bytes,qualification_sha256,
         recorded_before_commit_at_ms) VALUES ({})"""
    ).format(sql.SQL(",").join(sql.Literal(item) for item in fields))
    for completion in ("ROLLBACK", "COMMIT"):
        writer.execute("BEGIN")
        if subtransaction == "savepoint":
            writer.execute("SAVEPOINT receipt_birth")
            writer.execute(insert)
            writer.execute("RELEASE SAVEPOINT receipt_birth")
        else:
            body = "BEGIN " + insert.as_string(writer) + "; EXCEPTION WHEN division_by_zero THEN NULL; END"
            writer.execute(sql.SQL("DO {}").format(sql.Literal(body)))
        stamp, top = writer.execute(
            """SELECT creator_top_xid::text,pg_current_xact_id_if_assigned()::text
            FROM maezo_native_owner_v1.principal_qualification_receipt"""
        ).fetchone()
        assert stamp == top and stamp != "1"
        with pytest.raises(s.Refusal):
            store.read_operation(writer.cursor(), q["native_owner_operation_id"])
        assert store.read_operation(observer.cursor(), q["native_owner_operation_id"]) is None
        writer.execute(completion)
        if completion == "ROLLBACK":
            assert store.read_operation(observer.cursor(), q["native_owner_operation_id"]) is None
    expected = (request, raw, binding)
    assert store.read_operation(writer.cursor(), q["native_owner_operation_id"]) == expected
    assert store.read_operation(observer.cursor(), q["native_owner_operation_id"]) == expected
    with pytest.raises(psycopg.Error) as failure:
        writer.execute("UPDATE maezo_native_owner_v1.principal_qualification_receipt SET creator_top_xid='1'")
    assert failure.value.sqlstate == "P7N01"
    assert store.read_operation(observer.cursor(), q["native_owner_operation_id"]) == expected

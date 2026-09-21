"""Offline real-parser/signature/request tests. Protocol doubles are not AWS authority."""

from __future__ import annotations

import copy
import ssl
import time
from types import SimpleNamespace

import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from maezo.gateway.d7_external_owner import AwsOwnerClients, OwnerEnrollmentClient, OwnerTrust
from maezo.gateway.d7_owner_bootstrap import ExternalOwnerBootstrap
from maezo.platform.engine_bootstrap.controller_storage import Refusal, canonical, digest, encode64
from maezo.platform.engine_bootstrap.owner_authority_contracts import Enrollment, bootstrap_rows, uuid
from tests.unit.platform.engine_bootstrap.test_controller_storage import U2, H, U, root


def initial():
    return root(
        epoch=1,
        revision=1,
        next_generation_id=1,
        db_fence_epoch=0,
        journal_revision=0,
        restore_state="UNRECONCILED",
    )


def enrollment():
    r = initial()
    return {
        "protocol": "maezo.d7-synthetic-owner-enrollment.v1",
        "mode": "PUBLIC_SYNTHETIC",
        "enrollment_id": U,
        "installation_id": U2,
        "control_scope_id": U,
        "bootstrap_operation_id": "00000000-0000-4000-8000-000000000003",
        "scope": r.value()["scope"],
        "table_arn": "arn:aws:dynamodb:sa-east-1:123456789012:table/test",
        "table_id": "table-id",
        "writer_arn": "arn:aws:sts::123456789012:assumed-role/writer/test",
        "observer_arn": "arn:aws:sts::123456789012:assumed-role/observer/test",
        "owner_login": "owner",
        "owner_oid": 30,
        "source_manifest_sha256": H,
        "cib_abi_sha256": H,
        "operational_principals": [
            {"purpose": "receipt_read", "native_actor": "issuer", "login_name": "issuer", "login_oid": 10},
            {
                "purpose": "receipt_read",
                "native_actor": "observer",
                "login_name": "observer",
                "login_oid": 11,
            },
        ],
        "initial_root_sha256": r.digest(),
    }


class DynamoProtocol:
    def __init__(self, e):
        self.e = e
        self.rows = {}
        self.calls = []
        self.write_error = None
        self.partial = False
        self.extra = False
        self.table_override = {}

    def describe_table(self, **kwargs):
        self.calls.append(("describe", kwargs))
        return {
            "Table": {
                "TableArn": self.e["table_arn"],
                "TableId": self.e["table_id"],
                "TableStatus": "ACTIVE",
                "KeySchema": [
                    {"AttributeName": "PK", "KeyType": "HASH"},
                    {"AttributeName": "SK", "KeyType": "RANGE"},
                ],
                "AttributeDefinitions": [
                    {"AttributeName": "PK", "AttributeType": "S"},
                    {"AttributeName": "SK", "AttributeType": "S"},
                ],
                **self.table_override,
            }
        }

    @staticmethod
    def key(value):
        return value["PK"]["S"], value["SK"]["S"]

    def transact_get_items(self, **kwargs):
        self.calls.append(("read", kwargs))
        return {
            "Responses": [
                {"Item": self.rows[self.key(i["Get"]["Key"])]}
                if self.key(i["Get"]["Key"]) in self.rows
                else {}
                for i in kwargs["TransactItems"]
            ]
        }

    def transact_write_items(self, **kwargs):
        self.calls.append(("write", kwargs))
        entries = kwargs["TransactItems"][:1] if self.partial else kwargs["TransactItems"]
        for entry in entries:
            self.rows[self.key(entry["Put"]["Item"])] = copy.deepcopy(entry["Put"]["Item"])
        if self.write_error:
            raise self.write_error
        return {}

    def query(self, **kwargs):
        self.calls.append(("query", kwargs))
        values = [
            v for (pk, _), v in self.rows.items() if pk == kwargs["ExpressionAttributeValues"][":pk"]["S"]
        ]
        return {"Items": values, **({"LastEvaluatedKey": {"PK": {"S": "foreign"}}} if self.extra else {})}


@pytest.fixture
def package():
    e = enrollment()
    private = Ed25519PrivateKey.generate()
    public = private.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    tls = ssl.create_default_context()
    tls.minimum_version = ssl.TLSVersion.TLSv1_3
    owner = OwnerEnrollmentClient(
        OwnerTrust("https://owner.test/d7-owner/v1/qualify", public, "owner-key", U), tls
    )
    owner.close()
    state = SimpleNamespace(mutate=lambda x: None, key=private)

    def respond(request):
        import json

        call = json.loads(request.content)
        now = time.time_ns() // 1_000_000
        result = {
            "protocol": "maezo.d7-owner-qualification-response.v1",
            "key_id": "owner-key",
            **{
                k: call[k]
                for k in ("enrollment_id", "operation", "request_sha256", "context_sha256", "challenge")
            },
            "observed_at_ms": now,
            "deadline_ms": now + 4000,
            "enrollment": copy.deepcopy(e),
            "policy_receipt_ref": "actual-owner-operation",
            "policy_receipt_sha256": H,
        }
        state.mutate(result)
        result["signature"] = encode64(state.key.sign(canonical(result)))
        return httpx.Response(
            200,
            content=canonical(result),
            headers={"Content-Type": "application/json", "Cache-Control": "no-store"},
        )

    owner._http = httpx.Client(transport=httpx.MockTransport(respond))
    db = DynamoProtocol(e)
    sts = SimpleNamespace(get_caller_identity=lambda: {"Arn": e["writer_arn"], "Account": "123456789012"})
    aws = AwsOwnerClients(db, sts, "sa-east-1")
    yield SimpleNamespace(
        e=e, owner=owner, state=state, db=db, aws=aws, producer=ExternalOwnerBootstrap(owner, aws)
    )
    owner.close()


def test_real_signature_and_atomic_four_record_shape(package):
    result = package.producer.bootstrap(initial().wire)
    assert result.kind == "COMMITTED"
    writes = [v for k, v in package.db.calls if k == "write"]
    assert len(writes) == 1 and len(writes[0]["TransactItems"]) == 4
    for put in (v["Put"] for v in writes[0]["TransactItems"]):
        assert put["ConditionExpression"] == "attribute_not_exists(#pk) AND attribute_not_exists(#sk)"
    assert (
        len({(r["PK"], r["SK"]) for r in bootstrap_rows(Enrollment(canonical(package.e)), initial().wire)})
        == 4
    )
    assert package.producer.bootstrap(initial().wire).receipt_wire == result.receipt_wire
    assert len([1 for k, _ in package.db.calls if k == "write"]) == 1
    assert all(call["ConsistentRead"] is True for kind, call in package.db.calls if kind == "query")


@pytest.mark.parametrize(
    "mutation",
    [
        lambda r: r.update(challenge="wrong"),
        lambda r: r.update(operation="readback"),
        lambda r: r.update(request_sha256="b" * 64),
        lambda r: r.update(context_sha256="b" * 64),
        lambda r: r.update(enrollment_id=U2),
        lambda r: r.update(deadline_ms=0),
        lambda r: r["enrollment"].update(mode="production"),
        lambda r: r["enrollment"]["scope"].update(environment="production"),
    ],
)
def test_signed_wrong_binding_refuses_before_aws(package, mutation):
    package.state.mutate = mutation
    with pytest.raises(Refusal):
        package.producer.bootstrap(initial().wire)
    assert package.db.calls == []


def test_self_signed_untrusted_owner_and_unknown_operation_refuse(package):
    package.state.key = Ed25519PrivateKey.generate()
    with pytest.raises(Refusal):
        package.producer.bootstrap(initial().wire)
    assert not package.db.calls
    with pytest.raises(Refusal):
        package.owner.acquire("open_runtime_generation", {}, {})


@pytest.mark.parametrize(
    "override",
    [
        {"TableId": "new-incarnation"},
        {"TableStatus": "CREATING"},
        {"Replicas": [{"RegionName": "us-east-1"}]},
        {"KeySchema": []},
        {"AttributeDefinitions": [{"AttributeName": "PK", "AttributeType": "N"}]},
    ],
)
def test_actual_table_drift_refuses_before_write(package, override):
    package.db.table_override = override
    with pytest.raises(Refusal):
        package.producer.bootstrap(initial().wire)
    assert not any(k == "write" for k, _ in package.db.calls)


@pytest.mark.parametrize("change", ["installation", "operation", "scope", "lost-root", "wrong-kind", "alias"])
def test_permanent_identity_and_partial_record_collisions(package, change):
    package.producer.bootstrap(initial().wire)
    expected = bootstrap_rows(Enrollment(canonical(package.e)), initial().wire)
    if change == "installation":
        package.e["installation_id"] = "00000000-0000-4000-8000-000000000004"
    elif change == "operation":
        package.e["bootstrap_operation_id"] = "00000000-0000-4000-8000-000000000004"
    elif change == "scope":
        package.e["scope"]["tenant"] = "another"
    elif change == "lost-root":
        del package.db.rows[(expected[2]["PK"], "ROOT")]
    elif change == "wrong-kind":
        package.db.rows[(expected[0]["PK"], "ANCHOR")]["kind"] = {"S": "reservation"}
    else:
        row = package.db.rows.pop((expected[0]["PK"], "ANCHOR"))
        row["pk"] = row.pop("PK")
        package.db.rows[(expected[0]["PK"], "anchor")] = row
    with pytest.raises(Refusal):
        package.producer.bootstrap(initial().wire)
    assert len([1 for k, _ in package.db.calls if k == "write"]) == 1


def test_ambiguous_committed_write_reconciles_without_retry(package):
    package.db.write_error = TimeoutError()
    assert package.producer.bootstrap(initial().wire).kind == "COMMITTED"
    assert len([1 for k, _ in package.db.calls if k == "write"]) == 1


def test_partial_ambiguous_outcome_never_becomes_commit(package):
    package.db.partial = True
    package.db.write_error = TimeoutError()
    assert package.producer.bootstrap(initial().wire).kind == "UNKNOWN"
    assert len([1 for k, _ in package.db.calls if k == "write"]) == 1


def test_incomplete_initial_inventory_refuses_and_no_secret_methods(package):
    package.db.extra = True
    with pytest.raises(Refusal):
        package.producer.bootstrap(initial().wire)
    assert not any(kind == "write" for kind, _ in package.db.calls)
    assert not hasattr(package.db, "get_secret_value")
    assert not hasattr(package.db, "batch_get_secret_value")


@pytest.mark.parametrize(
    "raw", [U.upper().replace("00000001", "0000000A"), "{" + U + "}", U.replace("-", "")]
)
def test_uuid_aliases_refuse(raw):
    with pytest.raises(Refusal):
        uuid(raw)


def test_operation_uuid_cannot_cross_two_fresh_installation_scope_ids(package):
    from maezo.platform.engine_bootstrap.controller_storage import Document

    package.producer.bootstrap(initial().wire)
    changed = initial().value()
    changed["control_scope_id"] = U2
    newer = Document.create("Root", changed)
    package.e.update(
        installation_id="00000000-0000-4000-8000-000000000004",
        control_scope_id=U2,
        initial_root_sha256=newer.digest(),
    )
    with pytest.raises(Refusal):
        package.producer.bootstrap(newer.wire)
    assert len([1 for k, _ in package.db.calls if k == "write"]) == 1


def test_grant_expired_during_aws_reads_cannot_reach_write(package, monkeypatch):
    from maezo.gateway import d7_external_owner as external

    current = [time.time_ns()]
    monkeypatch.setattr(external.time, "time_ns", lambda: current[0])

    def identity():
        current[0] += 6_000_000_000
        return {"Arn": package.e["writer_arn"], "Account": "123456789012"}

    package.aws.sts.get_caller_identity = identity
    with pytest.raises(Refusal):
        package.producer.bootstrap(initial().wire)
    assert not any(k == "write" for k, _ in package.db.calls)


def test_readback_requires_actual_observer_and_unchanged_root(package):
    package.producer.bootstrap(initial().wire)
    with pytest.raises(Refusal):
        package.producer.readback(initial().wire)
    package.aws.sts.get_caller_identity = lambda: {
        "Arn": package.e["observer_arn"],
        "Account": "123456789012",
    }
    assert package.producer.readback(initial().wire)
    root_key = ("D7#" + U, "ROOT")
    package.db.rows[root_key]["revision"] = {"N": "2"}
    with pytest.raises(Refusal):
        package.producer.readback(initial().wire)


def test_no_credential_discovery_or_endpoint_override(monkeypatch):
    import botocore.session
    from botocore.credentials import ReadOnlyCredentials

    calls = []

    def create(_self, name, **kwargs):
        calls.append((name, kwargs))
        return object()

    monkeypatch.setattr(botocore.session.Session, "create_client", create)
    monkeypatch.setenv("AWS_ENDPOINT_URL", "http://untrusted.invalid")
    AwsOwnerClients.create(
        credentials=ReadOnlyCredentials("test-id", "test-secret", "test-token"), region="sa-east-1"
    )
    assert {name for name, _ in calls} == {"dynamodb", "sts"}
    for name, options in calls:
        assert options["endpoint_url"] == f"https://{name}.sa-east-1.amazonaws.com"
        assert options["verify"] is True
        assert options["config"].proxies == {}
        assert options["config"].retries == {"total_max_attempts": 1}
    with pytest.raises(Refusal):
        AwsOwnerClients.create(credentials=None, region="sa-east-1")


def test_unsupported_owner_continuation_refuses(package):
    from tests.unit.platform.engine_bootstrap.test_d7_control_migration_contract import owner

    authority = owner.EnrolledInitialOwnerAuthority(connection=object(), external=package.producer)
    for name in ("preparation", "removal", "replay"):
        with pytest.raises(Refusal):
            getattr(authority, name)()
    with pytest.raises(Refusal):
        authority.catalog(before_wire=b"{}", after_wire=b"{}", input_wire=b"{}", session=())


class SameConnectionProtocol:
    """Exact SQL response script; never represents a real TLS/PG connection."""

    def __init__(self, parameters, session, schema_oid):
        self.info = SimpleNamespace(get_parameters=lambda: parameters)
        self.pgconn = SimpleNamespace(ssl_in_use=True)
        self.session = session
        self.schema_oid = schema_oid
        self.ssl_row = (True, "TLSv1.3")
        self.calls = []
        self.last = ""

    def cursor(self):
        return self

    def execute(self, sql, parameters=()):
        self.calls.append((sql, parameters))
        self.last = sql

    def fetchone(self):
        if "pg_stat_ssl" in self.last:
            return self.ssl_row
        if "SESSION_USER" in self.last:
            return self.session
        if "pg_namespace" in self.last:
            return (self.schema_oid,)
        if "pg_backend_pid" in self.last:
            return (123,)
        raise AssertionError("Unexpected SQL")

    def close(self):
        self.calls.append(("close", ()))

    def commit(self):
        raise AssertionError("Witness cannot commit caller transaction")

    def rollback(self):
        raise AssertionError("Witness cannot roll back caller transaction")


@pytest.mark.parametrize("failure", [None, "verify-ca", "tls12", "session", "schema", "symlink"])
def test_same_connection_transport_catalog_witness(package, tmp_path, failure):
    from tests.unit.platform.engine_bootstrap.test_d7_control_migration_contract import input_record, owner

    value = input_record()
    ca = tmp_path / "ca.pem"
    ca.write_bytes(b"public certificate fixture")
    binding = value["database_binding"]
    binding.update(ca_sha256=digest(ca.read_bytes()), server_identity=binding["endpoint_host"])
    value["issuer_scopes"][0]["database_binding"] = binding
    parameters = {
        "sslmode": "verify-full",
        "host": binding["endpoint_host"],
        "port": str(binding["endpoint_port"]),
        "sslrootcert": str(ca),
    }
    session = (
        "owner",
        "owner",
        30,
        binding["database_oid"],
        binding["database_name"],
        160000,
        "read committed",
    )
    connection = SameConnectionProtocol(parameters, session, binding["schema_oid"])
    if failure == "verify-ca":
        parameters["sslmode"] = "verify-ca"
    elif failure == "tls12":
        connection.ssl_row = (True, "TLSv1.2")
    elif failure == "session":
        connection.session = ("other", *session[1:])
    elif failure == "schema":
        connection.schema_oid += 1
    elif failure == "symlink":
        linked = tmp_path / "alias.pem"
        linked.symlink_to(ca)
        parameters["sslrootcert"] = str(linked)
    authority = owner.EnrolledInitialOwnerAuthority(connection=connection, external=package.producer)
    if failure:
        with pytest.raises(Refusal):
            authority._context(canonical(value), session, canonical([]))
    else:
        context = authority._context(canonical(value), session, canonical([]))
        assert context["session"] == list(session) and context["backend_pid"] == 123
        assert context["database_binding"] == binding
        assert connection.calls[-1] == ("close", ())


def test_installer_refuses_different_physical_enrolled_authority_connection(package):
    from tests.unit.platform.engine_bootstrap.test_d7_control_migration_contract import PACKAGE, SQL, owner

    first = SameConnectionProtocol({}, ("owner",) * 7, 1)
    second = SameConnectionProtocol({}, ("owner",) * 7, 1)
    authority = owner.EnrolledInitialOwnerAuthority(connection=first, external=package.producer)
    with pytest.raises(Refusal):
        owner.OwnerInstaller(second, PACKAGE, SQL.encode(), authority)
    assert first.calls == second.calls == []
    installer = owner.OwnerInstaller(first, PACKAGE, SQL.encode(), authority)
    assert installer.connection is first
    authority.connection = second
    with pytest.raises(Refusal):
        installer._begin()
    assert first.calls == second.calls == []


def test_initial_orphan_scope_refuses_before_any_permanent_write(package):
    pk = "D7#" + package.e["control_scope_id"]
    package.db.rows[(pk, "ORPHAN")] = {"PK": {"S": pk}, "SK": {"S": "ORPHAN"}}
    before = copy.deepcopy(package.db.rows)
    with pytest.raises(Refusal):
        package.producer.bootstrap(initial().wire)
    assert package.db.rows == before
    assert not any(kind == "write" for kind, _ in package.db.calls)


def test_signed_shorter_deadline_expires_by_monotonic_elapsed_time(package, monkeypatch):
    clock = {"wall": 1_000_000_000_000, "mono": 1000.0}
    monkeypatch.setattr(time, "time_ns", lambda: clock["wall"])
    monkeypatch.setattr(time, "monotonic", lambda: clock["mono"])
    package.state.mutate = lambda value: value.update(deadline_ms=value["observed_at_ms"] + 1000)
    grant = package.owner.acquire("readback", {}, {})
    grant.current()
    clock["mono"] += 2.0
    clock["wall"] += 500_000_000
    with pytest.raises(Refusal):
        grant.current()


def test_post_write_partition_query_timeout_is_unknown_without_resubmission(package, monkeypatch):
    original = package.db.query

    def query(**kwargs):
        if any(kind == "write" for kind, _ in package.db.calls):
            raise TimeoutError("finite query transport witness")
        return original(**kwargs)

    monkeypatch.setattr(package.db, "query", query)
    result = package.producer.bootstrap(initial().wire)
    assert result.kind == "UNKNOWN" and result.receipt_wire is None
    assert len([1 for kind, _ in package.db.calls if kind == "write"]) == 1

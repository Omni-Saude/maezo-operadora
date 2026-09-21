"""Finite commit/replay/cursor mechanics. Observation doubles are not real authority."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from maezo.gateway import native_owner_qualification as q
from maezo.gateway import native_owner_store as store
from maezo.platform.engine_bootstrap import controller_storage as s
from maezo.platform.engine_bootstrap import native_owner_contracts as n
from tests.unit.gateway.test_native_owner_store import pins, retained_row
from tests.unit.platform.engine_bootstrap.test_native_owner_contracts import record_vector


class OperationConnection:
    autocommit = False
    info = SimpleNamespace(transaction_status=0)

    def __init__(self):
        self.connection = self
        self.calls = []
        self.fail_commit = False
        self.fail_rollback = False

    def cursor(self):
        return self

    def execute(self, sql, params=()):
        self.calls.append(sql)

    def fetchone(self):
        return (100,)

    def commit(self):
        self.calls.append("commit")
        if self.fail_commit:
            raise RuntimeError("unknown")

    def rollback(self):
        self.calls.append("rollback")
        if self.fail_rollback:
            raise RuntimeError("rollback unknown")

    def close(self):
        self.calls.append("close")


class ObservationSource:
    def __init__(self, observation, calls):
        self.observation = observation
        self.calls = calls

    def validate_request(self, wire):
        self.calls.append("validate-request")
        n.request(wire)

    def observe(self, cursor):
        self.calls.append("final-native-read")
        return self.observation


class ProtocolOwner(q.NativeQualificationOwner):
    def _birth(self, cursor, value):
        return b"{}", s.decode64(self.value["installed_record"]["preparation_receipt_bytes"])

    def _catalog(self, cursor, version):
        self.connection.calls.append("catalog-read")
        return b"{}", b"{}"

    def _observe(self, *args):
        self.connection.calls.append("qualified-observation-double")
        return self.source.observation, b"{}"


def owner(monkeypatch):
    value = record_vector()
    request = s.decode64(value["request_bytes"])
    body = n.request(request)
    conn = OperationConnection()
    observation = q.NativeObservation(
        s.canonical(value["installed_record"]), s.decode64(value["native_catalog_bytes"]), ()
    )
    d = SimpleNamespace(
        connection=conn,
        _session=lambda cursor: ("migration", "migration", 999, 1, "synthetic", 160004, "read committed"),
        _owner_context=lambda *args, **kwargs: ({}, {}, {"generation_id": 1}),
    )
    result = ProtocolOwner(
        conn,
        pins(installation_id=body["owner_installation_id"]),
        ObservationSource(observation, conn.calls),
        d,
    )
    result.value = value
    monkeypatch.setattr(store, "read_operation", lambda *args: None)
    monkeypatch.setattr(store, "read_installation", lambda *args: b"{}")

    def append(cursor, pins, raw):
        conn.calls.append("append-operation")
        return n.qualification_from_receipt(raw)

    monkeypatch.setattr(store, "append_operation", append)
    monkeypatch.setattr(store, "one", lambda *args: (100,))
    return result, conn, request, value


def test_commit_is_after_persist_and_final_native_catalogue_observations(monkeypatch):
    actual, conn, request, _ = owner(monkeypatch)
    result = actual.qualify(request)
    assert result.kind == "COMMITTED"
    assert (
        conn.calls.index("append-operation")
        < conn.calls.index("final-native-read")
        < conn.calls.index("commit")
    )
    assert conn.calls[-1] == "close" and "rollback" not in conn.calls


def test_commit_ambiguity_returns_unknown_without_successful_binding(monkeypatch):
    actual, conn, request, _ = owner(monkeypatch)
    conn.fail_commit = True
    result = actual.qualify(request)
    assert result.kind == "UNKNOWN" and result.qualification_wire is None and result.code == "PENDING_UNKNOWN"
    assert conn.calls[-2:] == ["rollback", "close"]


def test_failed_final_observation_rolls_back_without_commit(monkeypatch):
    actual, conn, request, _ = owner(monkeypatch)

    def fail(cursor):
        raise s.Refusal("PRECONDITION_MISMATCH")

    actual.source.observe = fail
    result = actual.qualify(request)
    assert result.kind == "REFUSED" and result.qualification_wire is None and "commit" not in conn.calls
    assert conn.calls[-2:] == ["rollback", "close"]


def test_exact_replay_preserves_historical_bytes_after_current_observation(monkeypatch):
    actual, conn, request, value = owner(monkeypatch)
    value["owner_store_catalog_sha256"] = s.digest(b"{}")
    row = retained_row(value)
    monkeypatch.setattr(store, "read_operation", lambda *args: (row[0], row[2], row[4]))
    result = actual.qualify(request)
    assert result.kind == "COMMITTED" and result.qualification_wire == row[4]
    assert "qualified-observation-double" in conn.calls and "append-operation" not in conn.calls


def test_historical_readback_is_distinct_and_uses_no_current_qualification(monkeypatch):
    actual, conn, _, value = owner(monkeypatch)
    row = retained_row(value)
    monkeypatch.setattr(store, "read_operation", lambda *args: (row[0], row[2], row[4]))
    result = actual.read_committed_operation(n.request(row[0])["native_owner_operation_id"])
    assert result.kind == "HISTORICAL_COMMITTED" and result.qualification_wire == row[4]
    assert "qualified-observation-double" not in conn.calls and "commit" not in conn.calls


def test_d_consumer_verifies_exact_committed_bytes_with_same_supplied_cursor(monkeypatch):
    actual, conn, _, value = owner(monkeypatch)
    value["owner_store_catalog_sha256"] = s.digest(b"{}")
    row = retained_row(value)
    monkeypatch.setattr(
        store, "read_operation", lambda cursor, *args: (row[0], row[2], row[4]) if cursor is conn else None
    )
    monkeypatch.setattr(conn, "cursor", lambda: pytest.fail("D consumer allocated another cursor"))
    body = n.request(row[0])
    birth = s.decode64(value["installed_record"]["preparation_receipt_bytes"])
    result = actual.verify(
        cursor=conn,
        qualification_wire=row[4],
        birth_wire=birth,
        generation_wire=s.canonical(dict(generation_id=body["generation_id"], login_oid=body["login_oid"])),
        fence_wire=b"{}",
        version_wire=b"{}",
        before_catalog_wire=b"{}",
        actual_catalog_wire=b"{}",
        session=("migration", "migration", 999),
    )
    assert result == b"{}" and "cursor" not in conn.calls and "commit" not in conn.calls
    monkeypatch.setattr(store, "read_operation", lambda *args: None)
    with pytest.raises(s.Refusal):
        actual.verify(
            cursor=conn,
            qualification_wire=row[4],
            birth_wire=birth,
            generation_wire=s.canonical(
                dict(generation_id=body["generation_id"], login_oid=body["login_oid"])
            ),
            fence_wire=b"{}",
            version_wire=b"{}",
            before_catalog_wire=b"{}",
            actual_catalog_wire=b"{}",
            session=("migration", "migration", 999),
        )


def test_finite_connection_explicitly_supplies_its_dbapi_cursor_protocol():
    connection = OperationConnection()
    cursor = connection.cursor()
    cursor.execute("SELECT 100")
    assert cursor is connection and cursor.fetchone() == (100,)
    cursor.close()


def test_d_consumer_rejects_foreign_connection_before_observation(monkeypatch):
    actual, conn, _, value = owner(monkeypatch)
    value["owner_store_catalog_sha256"] = s.digest(b"{}")
    row = retained_row(value)
    monkeypatch.setattr(store, "read_operation", lambda *args: (row[0], row[2], row[4]))
    body = n.request(row[0])
    foreign = OperationConnection()
    with pytest.raises(s.Refusal):
        actual.verify(
            cursor=foreign,
            qualification_wire=row[4],
            birth_wire=s.decode64(value["installed_record"]["preparation_receipt_bytes"]),
            generation_wire=s.canonical(
                dict(generation_id=body["generation_id"], login_oid=body["login_oid"])
            ),
            fence_wire=b"{}",
            version_wire=b"{}",
            before_catalog_wire=b"{}",
            actual_catalog_wire=b"{}",
            session=("migration", "migration", 999),
        )
    assert conn.calls == [] and foreign.calls == []


def test_failed_rollback_after_sent_operation_is_unknown_and_keeps_operation_identity(monkeypatch):
    actual, conn, request, _ = owner(monkeypatch)
    conn.fail_rollback = True

    def fail(cursor):
        raise s.Refusal("PRECONDITION_MISMATCH")

    actual.source.observe = fail
    result = actual.qualify(request)
    assert result.kind == "UNKNOWN" and result.qualification_wire is None
    assert result.operation_id == n.request(request)["native_owner_operation_id"]
    assert result.code == "PENDING_UNKNOWN" and "commit" not in conn.calls

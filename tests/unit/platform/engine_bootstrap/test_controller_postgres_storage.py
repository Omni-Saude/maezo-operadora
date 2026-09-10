"""Actual fixed adapter argument/transaction controls. Protocol fixtures are not PostgreSQL."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from tests.unit.platform.engine_bootstrap import test_controller_contracts as v
from tests.unit.platform.engine_bootstrap.test_controller_storage import H, prepare_call

from maezo.platform.engine_bootstrap import controller_contracts as c
from maezo.platform.engine_bootstrap import controller_postgres_storage as p
from maezo.platform.engine_bootstrap import controller_storage as s


def binding() -> p.PostgresBinding:
    return p.PostgresBinding(
        c.parse("DatabaseBinding", s.canonical(v.sample("DatabaseBinding"))),
        c.parse("Scope", s.canonical(v.sample("Scope"))),
        "issuer",
        17,
        H,
        tuple(p.FunctionBinding(name, 100 + i, 22, H) for i, name in enumerate(sorted(s.OPERATIONS))),
    )


class QualifiedSpy:
    def __init__(self) -> None:
        self.calls = []

    def qualify(self, **kwargs: Any) -> None:
        self.calls.append(kwargs)


def response(call: dict[str, Any], purpose: str) -> dict[str, Any]:
    request = c.parse("IssuerRequest", s.decode64(call["request"]["issuer_request_bytes"]))
    receipt = v.issuer(purpose, "PREPARED")
    receipt.update(
        issuer_operation_id=call["issuer_operation_id"],
        request_sha256=request.digest(),
        epoch=call["epoch"],
        issuer_login_oid=17,
        issuer_session_user="issuer",
        issuer_current_user="issuer_owner",
    )
    wire = s.canonical(receipt)
    return {
        "protocol": "maezo.d7-store-result.v1",
        "issuer_operation_id": call["issuer_operation_id"],
        "request_sha256": request.digest(),
        "canonical_result_bytes": s.encode64(wire),
        "result_sha256": s.digest(wire),
    }


class Connection:
    """Scripted DB-API responses; never emulates D rows, roles or commit durability."""

    def __init__(self, call: dict[str, Any], purpose: str = "candidate") -> None:
        self.autocommit = False
        self.info = SimpleNamespace(transaction_status=0)
        self.log = []
        self.cursor_object = Cursor(self, call, purpose)
        self.commit_error = None
        self.close_error = None
        self.rollback_error = None

    def cursor(self) -> Cursor:
        return self.cursor_object

    def commit(self) -> None:
        self.log.append("commit")
        if self.commit_error:
            raise self.commit_error

    def rollback(self) -> None:
        self.log.append("rollback")
        if self.rollback_error:
            raise self.rollback_error


class Cursor:
    def __init__(self, connection: Connection, call: dict[str, Any], purpose: str) -> None:
        self.connection = connection
        self.call = call
        self.purpose = purpose
        self.row = None
        self.identity = None
        self.function = None
        self.result = None
        self.error = None

    def execute(self, statement: str, parameters: Any = None) -> None:
        self.connection.log.append((statement, parameters))
        if statement == p.IDENTITY_SQL:
            self.row = self.identity or ("issuer", "issuer", 17, 1, "synthetic", "read committed", 160004)
        elif statement == p.FUNCTION_SQL:
            f = next(f for f in binding().functions if f.operation == "prepare_generation")
            self.row = self.function or (
                f.oid,
                22,
                H,
                True,
                "v",
                "u",
                ["search_path=pg_catalog, pg_temp"],
                True,
            )
        elif statement == p.FUNCTIONS["prepare_generation"]:
            if self.error:
                raise self.error
            self.row = (self.result if self.result is not None else response(self.call, self.purpose),)

    def fetchone(self) -> Any:
        return self.row

    def close(self) -> None:
        self.connection.log.append("close")
        if self.connection.close_error:
            raise self.connection.close_error


@pytest.mark.parametrize("purpose", ["candidate", "runtime"])
def test_prepare_transmits_complete_bytes_and_publishes_only_after_commit(purpose: str) -> None:
    call, request, decision = prepare_call(purpose)
    conn = Connection(call, purpose)
    spy = QualifiedSpy()
    result = p.PostgresStore(binding(), spy).mutate(conn, "prepare_generation", call)
    assert result.kind == "COMMITTED" and conn.log[-2:] == ["commit", "close"]
    statements = [entry for entry in conn.log if isinstance(entry, tuple)]
    executed = next(
        params for statement, params in statements if statement == p.FUNCTIONS["prepare_generation"]
    )
    actual = s.parse_wire(executed[0].encode())
    assert s.decode64(actual["request"]["retained_decision"]["decision_bytes"]) == decision.canonical_bytes()
    assert actual["request"]["request_sha256"] == request.digest()
    assert spy.calls[0]["proof_wire"] == s.canonical(call["proof"])


@pytest.mark.parametrize(
    "identity",
    [
        ("wrong", "issuer", 17, 1, "synthetic", "read committed", 160004),
        ("issuer", "set_role", 17, 1, "synthetic", "read committed", 160004),
        ("issuer", "issuer", 18, 1, "synthetic", "read committed", 160004),
        ("issuer", "issuer", 17, 2, "synthetic", "read committed", 160004),
        ("issuer", "issuer", 17, 1, "synthetic", "serializable", 160004),
        ("issuer", "issuer", 17, 1, "synthetic", "read committed", 150000),
    ],
)
def test_current_session_identity_database_and_isolation_precede_mutation(identity: tuple[Any, ...]) -> None:
    call, _, _ = prepare_call()
    conn = Connection(call)
    conn.cursor_object.identity = identity
    result = p.PostgresStore(binding(), QualifiedSpy()).mutate(conn, "prepare_generation", call)
    assert result.kind == "REFUSED"
    assert not any(isinstance(row, tuple) and row[0] == p.FUNCTIONS["prepare_generation"] for row in conn.log)
    assert "commit" not in conn.log


@pytest.mark.parametrize(
    "index,value",
    [
        (0, 999),
        (1, 999),
        (2, "b" * 64),
        (3, False),
        (4, "s"),
        (5, "s"),
        (6, ["search_path=public, pg_catalog"]),
        (7, False),
    ],
)
def test_actual_fixed_function_oid_owner_source_and_acl_must_match(index: int, value: Any) -> None:
    call, _, _ = prepare_call()
    conn = Connection(call)
    f = next(f for f in binding().functions if f.operation == "prepare_generation")
    row = [f.oid, 22, H, True, "v", "u", ["search_path=pg_catalog, pg_temp"], True]
    row[index] = value
    conn.cursor_object.function = tuple(row)
    assert (
        p.PostgresStore(binding(), QualifiedSpy()).mutate(conn, "prepare_generation", call).kind == "REFUSED"
    )


def test_missing_producer_and_nested_transaction_refuse_without_rollback_of_caller() -> None:
    call, _, _ = prepare_call()
    conn = Connection(call)
    with pytest.raises(s.Refusal, match="UNAVAILABLE"):
        p.PostgresStore(binding(), None).mutate(conn, "prepare_generation", call)
    conn.info.transaction_status = 2
    with pytest.raises(s.Refusal):
        p.PostgresStore(binding(), QualifiedSpy()).mutate(conn, "prepare_generation", call)
    assert conn.log == []


@pytest.mark.parametrize("sqlstate", list(p.SQLSTATES))
def test_only_fixed_refusals_are_published_without_private_diagnostics(sqlstate: str) -> None:
    call, _, _ = prepare_call()
    conn = Connection(call)
    error = RuntimeError("private provider text")
    error.sqlstate = sqlstate
    conn.cursor_object.error = error
    result = p.PostgresStore(binding(), QualifiedSpy()).mutate(conn, "prepare_generation", call)
    assert result.kind == "REFUSED" and result.code == p.SQLSTATES[sqlstate] and "private" not in repr(result)
    assert "commit" not in conn.log


@pytest.mark.parametrize("failure", ["execute", "empty", "digest", "wrong-core", "commit", "close"])
def test_after_send_failure_commit_ambiguity_and_post_commit_close(failure: str) -> None:
    call, _, _ = prepare_call()
    conn = Connection(call)
    if failure == "execute":
        conn.cursor_object.error = RuntimeError("provider failure")
    elif failure == "commit":
        conn.commit_error = RuntimeError("lost commit acknowledgement")
    elif failure == "close":
        conn.close_error = RuntimeError("close")
    else:
        result = response(call, "candidate")
        if failure == "empty":
            result = {}
        elif failure == "digest":
            result["result_sha256"] = "b" * 64
        else:
            val = s.parse_wire(s.decode64(result["canonical_result_bytes"]))
            val["binding_sha256"] = "b" * 64
            wire = s.canonical(val)
            result.update(canonical_result_bytes=s.encode64(wire), result_sha256=s.digest(wire))
        conn.cursor_object.result = result
    actual = p.PostgresStore(binding(), QualifiedSpy()).mutate(conn, "prepare_generation", call)
    assert actual.kind == ("COMMITTED" if failure == "close" else "UNKNOWN")
    assert (
        len(
            [
                row
                for row in conn.log
                if isinstance(row, tuple) and row[0] == p.FUNCTIONS["prepare_generation"]
            ]
        )
        == 1
    )


def test_closed_request_rejects_sql_selector_and_carrier_omission() -> None:
    call, _, _ = prepare_call()
    call["function"] = "evil"
    with pytest.raises(s.Refusal):
        p.PostgresStore(binding(), QualifiedSpy()).mutate(Connection(call), "prepare_generation", call)
    call, _, _ = prepare_call()
    del call["request"]["retained_decision"]
    with pytest.raises(s.Refusal):
        p.PostgresStore(binding(), QualifiedSpy()).mutate(Connection(call), "prepare_generation", call)
    assert set(p.FUNCTIONS) == s.OPERATIONS and all(
        "maezo_d7_control." in sql for sql in p.FUNCTIONS.values()
    )

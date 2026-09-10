"""Fixed D issuer SQL bridge over an injected gateway-owned DB-API connection.

No connection factory, credentials, role switch, import-time effects or retry.
Success bytes are decoded/checked before commit and published only after it returns.
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass
from typing import Any, Protocol

from maezo.platform.engine_bootstrap import controller_contracts as c
from maezo.platform.engine_bootstrap.controller_storage import (
    GENERATION_OPERATIONS,
    OPERATIONS,
    Refusal,
    canonical,
    decode64,
    digest,
    exact,
    logical_request_digest,
    parse_wire,
    present,
    require,
    scalar,
    validate_store_call,
)

READ_FUNCTIONS = {
    name: f"SELECT maezo_d7_control.{name}(%s::pg_catalog.jsonb)"
    for name in ("read_issuer_operation", "read_provisioning_receipt")
}
FUNCTIONS = {name: f"SELECT maezo_d7_control.{name}(%s::pg_catalog.jsonb)" for name in sorted(OPERATIONS)}
SQLSTATES = {
    "P7D01": "AUTH_REFUSED",
    "P7D02": "SCOPE_REFUSED",
    "P7D03": "PURPOSE_REFUSED",
    "P7D04": "STALE_EPOCH",
    "P7D05": "STALE_GENERATION",
    "P7D06": "REQUEST_CONFLICT",
    "P7D07": "PRECONDITION_MISMATCH",
    "P7D08": "INVALID_BODY",
    "P7D09": "UNAVAILABLE",
    "P7D10": "PENDING_UNKNOWN",
    "P7D11": "UNSUPPORTED_ADAPTER",
}
IDENTITY_SQL = (
    "SELECT SESSION_USER::pg_catalog.text, "
    "CURRENT_USER::pg_catalog.text,\n (SELECT oid::pg_catalog.bigint "
    "FROM pg_catalog.pg_roles WHERE rolname=SESSION_USER),\n (SELECT "
    "oid::pg_catalog.bigint FROM pg_catalog.pg_database WHERE "
    "datname=pg_catalog.current_database()),\n "
    "pg_catalog.current_database(), "
    "pg_catalog.current_setting('transaction_isolation'),\n "
    "pg_catalog.current_setting('server_version_num')::pg_catalog.inte"
    "ger"
)
FUNCTION_SQL = (
    "SELECT p.oid::pg_catalog.bigint,p.proowner::pg_catalog.bigint,\n "
    "pg_catalog.encode(pg_catalog.sha256(pg_catalog.convert_to(pg_cata"
    "log.pg_get_functiondef(p.oid),'UTF8')),'hex'),\n "
    "p.prosecdef,p.provolatile,p.proparallel,p.proconfig,\n "
    "pg_catalog.has_function_privilege(SESSION_USER,p.oid,'EXECUTE')\n "
    "FROM pg_catalog.pg_proc p WHERE "
    "p.oid=pg_catalog.to_regprocedure(%s)"
)


class Cursor(Protocol):
    def execute(self, operation: str, parameters: Any = None) -> Any: ...
    def fetchone(self) -> Any: ...
    def close(self) -> Any: ...


class Connection(Protocol):
    autocommit: bool
    info: Any

    def cursor(self) -> Cursor: ...
    def commit(self) -> Any: ...
    def rollback(self) -> Any: ...


class IssuerAuthority(Protocol):
    """Qualified controller/DB observation producer, independent of caller bytes.

    Must authenticate source transport/challenge/profile, current owner lineage,
    full owner/readiness/restore evidence and actual DB context before projecting.
    This stage supplies no permissive producer. Its absence always refuses.
    """

    def qualify(
        self,
        *,
        operation: str,
        request_wire: bytes,
        proof_wire: bytes,
        scope_wire: bytes,
        expected_database_wire: bytes,
    ) -> None: ...


@dataclass(frozen=True, slots=True)
class FunctionBinding:
    operation: str
    oid: int
    owner_oid: int
    definition_sha256: str

    def __post_init__(self) -> None:
        require(self.operation in OPERATIONS | READ_FUNCTIONS.keys())
        scalar("Oid", self.oid)
        scalar("Oid", self.owner_oid)
        scalar("Sha256", self.definition_sha256)


@dataclass(frozen=True, slots=True)
class PostgresBinding:
    database: c.DatabaseBinding
    scope: c.Scope
    issuer_login: str
    issuer_login_oid: int
    installation_receipt_sha256: str
    functions: tuple[FunctionBinding, ...]

    def __post_init__(self) -> None:
        require(type(self.database) is c.DatabaseBinding and type(self.scope) is c.Scope)
        scalar("Id", self.issuer_login)
        scalar("Oid", self.issuer_login_oid)
        scalar("Sha256", self.installation_receipt_sha256)
        require(
            type(self.functions) is tuple
            and {f.operation for f in self.functions} == OPERATIONS
            and len(self.functions) == len(OPERATIONS)
        )


@dataclass(frozen=True, slots=True)
class ReceiptReadBinding:
    database: c.DatabaseBinding
    scope: c.Scope
    issuer_login: str
    issuer_login_oid: int
    installation_receipt_sha256: str
    functions: tuple[FunctionBinding, ...]
    role_class: str

    def __post_init__(self) -> None:
        require(type(self.database) is c.DatabaseBinding and type(self.scope) is c.Scope)
        require(self.scope.database_binding == self.database, "SCOPE_REFUSED")
        scalar("Id", self.issuer_login)
        scalar("Oid", self.issuer_login_oid)
        scalar("Sha256", self.installation_receipt_sha256)
        require(self.role_class in {"actual issuer login", "scoped D observer login", "one-shot login"})
        allowed = (
            {"read_issuer_operation"}
            if self.role_class == "actual issuer login"
            else (
                {"read_provisioning_receipt"} if self.role_class == "one-shot login" else set(READ_FUNCTIONS)
            )
        )
        require(
            type(self.functions) is tuple
            and len(self.functions) == len(allowed)
            and {f.operation for f in self.functions} == allowed
        )


@dataclass(frozen=True, slots=True)
class MutationResult:
    kind: str
    canonical_result_bytes: bytes | None
    result_sha256: str | None
    code: str | None

    def __post_init__(self) -> None:
        require(self.kind in {"COMMITTED", "REFUSED", "UNKNOWN"})
        if self.kind == "COMMITTED":
            require(
                type(self.canonical_result_bytes) is bytes
                and digest(self.canonical_result_bytes) == self.result_sha256
                and self.code is None
            )
        else:
            require(self.canonical_result_bytes is None and self.result_sha256 is None)
            scalar("RefusalCode", self.code)


def result_bytes(operation: str, call: dict[str, Any], result: Any, expected_login_oid: int) -> bytes:
    if isinstance(result, str):
        # SQL JSONB is an internal transport; the retained result is validated as W1.
        import json

        result = json.loads(result)
    exact(result, "protocol issuer_operation_id request_sha256 result_sha256 canonical_result_bytes")
    require(result["protocol"] == "maezo.d7-store-result.v1")
    require(
        result["issuer_operation_id"] == call["issuer_operation_id"]
        and result["request_sha256"] == logical_request_digest(operation, call)
    )
    wire = decode64(result["canonical_result_bytes"])
    require(digest(wire) == result["result_sha256"])
    value = parse_wire(wire)
    if operation in GENERATION_OPERATIONS:
        receipt = c.parse("IssuerReceipt", wire)
        require(
            receipt.scope.to_wire() == call["scope"]
            and receipt.epoch == call["epoch"]
            and receipt.issuer_operation_id == call["issuer_operation_id"]
        )
        require(
            receipt.issuer_login_oid == expected_login_oid
            and receipt.request_sha256 == result["request_sha256"]
            and receipt.provisioning_schema_version == 1
        )
        request = c.parse("IssuerRequest", decode64(call["request"]["issuer_request_bytes"]))
        generation_id = (
            request.body.generation.binding.generation_id
            if operation == "prepare_generation"
            else request.body.generation_id
        )
        require(receipt.generation_id == generation_id)
        if operation == "prepare_generation":
            require(
                receipt.binding_sha256 == request.body.generation.binding.digest()
                and receipt.activation_decision_sha256 == request.body.generation.activation_decision_sha256
                and receipt.purpose == request.body.generation.binding.purpose
                and receipt.revision == 1
            )
        if operation == "admit_candidate_login":
            require(
                receipt.purpose == "candidate"
                and receipt.activation_decision_sha256 == request.body.candidate_decision_sha256
            )
        if operation == "open_runtime_generation":
            require(
                receipt.purpose == "runtime"
                and receipt.activation_decision_sha256 == request.body.activation_decision_sha256
            )
        require(
            receipt.status
            == {
                "prepare_generation": "PREPARED",
                "admit_candidate_login": "OPEN",
                "open_runtime_generation": "OPEN",
                "retire_generation": "RETIRED",
            }[operation]
        )
        # Historical retirement intentionally retains current receipt authority epoch;
        # no comparison to the old immutable target generation epoch is made here.
    else:
        exact(
            value,
            (
                "protocol scope run_id epoch issuer_operation_id operation "
                "request_sha256 fence_revision generation_id permit_id state "
                "issuer_session_user issuer_login_oid issuer_current_user "
                "issuer_definer_oid recorded_before_commit_at_ms "
                "provisioning_schema_version"
            ),
        )
        require(
            value["protocol"] == "maezo.d7-store-transition-receipt.v1" and value["operation"] == operation
        )
        require(all(value[k] == call[k] for k in ("scope", "run_id", "epoch", "issuer_operation_id")))
        require(
            value["request_sha256"] == result["request_sha256"]
            and value["issuer_login_oid"] == expected_login_oid
            and value["provisioning_schema_version"] == 1
        )
        scalar("Positive", value["fence_revision"])
        scalar("UInt", value["recorded_before_commit_at_ms"])
        scalar("Oid", value["issuer_definer_oid"])
        require(
            value["state"]
            in {"CLOSED", "CANDIDATE", "RESTORING", "OPEN", "RECOVERY_REQUIRED", "ISSUED", "REVOKED"}
        )
        for name in ("generation_id", "permit_id"):
            if value[name] is not None:
                scalar("Positive" if name == "generation_id" else "Uuid", value[name])
    return wire


class PostgresStore:
    def __init__(
        self, binding: PostgresBinding | ReceiptReadBinding, authority: IssuerAuthority | None
    ) -> None:
        require(type(binding) in {PostgresBinding, ReceiptReadBinding})
        self.binding, self.authority = binding, authority

    def _verify_connection(self, cursor: Cursor, operation: str) -> None:
        cursor.execute(IDENTITY_SQL)
        row = cursor.fetchone()
        require(type(row) in {tuple, list} and len(row) == 7, "UNAVAILABLE")
        require(
            row[0] == row[1] == self.binding.issuer_login and row[2] == self.binding.issuer_login_oid,
            "AUTH_REFUSED",
        )
        require(
            row[3] == self.binding.database.database_oid and row[4] == self.binding.database.database_name,
            "SCOPE_REFUSED",
        )
        require(row[5] == "read committed" and 160000 <= row[6] < 170000, "UNSUPPORTED_ADAPTER")
        expected = next(f for f in self.binding.functions if f.operation == operation)
        cursor.execute(FUNCTION_SQL, ("maezo_d7_control." + operation + "(pg_catalog.jsonb)",))
        function = cursor.fetchone()
        require(type(function) in {tuple, list} and len(function) == 8, "UNAVAILABLE")
        require(
            tuple(function[:3]) == (expected.oid, expected.owner_oid, expected.definition_sha256),
            "AUTH_REFUSED",
        )
        require(
            function[3] is True and function[4] == "v" and function[5] == "u" and function[7] is True,
            "AUTH_REFUSED",
        )
        require(
            function[6] in (["search_path=pg_catalog, pg_temp"], ["search_path=pg_catalog,pg_temp"]),
            "AUTH_REFUSED",
        )

    def mutate(self, connection: Connection, operation: str, value: dict[str, Any]) -> MutationResult:
        require(type(self.binding) is PostgresBinding, "AUTH_REFUSED")
        call = validate_store_call(operation, value)
        require(call["scope"] == self.binding.scope.to_wire(), "SCOPE_REFUSED")
        authority = present(self.authority, "UNAVAILABLE")
        authority.qualify(
            operation=operation,
            request_wire=canonical(call["request"]),
            proof_wire=canonical(call["proof"]),
            scope_wire=self.binding.scope.canonical_bytes(),
            expected_database_wire=self.binding.database.canonical_bytes(),
        )
        require(connection is not None and connection.autocommit is False, "UNSUPPORTED_ADAPTER")
        require(connection.info.transaction_status == 0, "PRECONDITION_MISMATCH")
        cursor: Cursor | None = None
        sent = False
        commit_started = False
        prepared: bytes | None = None
        try:
            cursor = connection.cursor()
            cursor.execute("BEGIN ISOLATION LEVEL READ COMMITTED")
            self._verify_connection(cursor, operation)
            sent = True
            cursor.execute(FUNCTIONS[operation], (canonical(call).decode("utf8"),))
            row = cursor.fetchone()
            require(type(row) in {tuple, list} and len(row) == 1, "UNAVAILABLE")
            prepared = result_bytes(operation, call, row[0], self.binding.issuer_login_oid)
            # Nothing that can invalidate publication is deferred beyond commit.
            completed = MutationResult("COMMITTED", prepared, digest(prepared), None)
            commit_started = True
            connection.commit()
            return completed
        except Exception as exc:
            state = getattr(exc, "sqlstate", None) or getattr(exc, "pgcode", None)
            with contextlib.suppress(Exception):
                connection.rollback()
            if not commit_started and state in SQLSTATES:
                return MutationResult("REFUSED", None, None, SQLSTATES[state])
            if not sent and isinstance(exc, Refusal):
                return MutationResult("REFUSED", None, None, exc.code)
            return MutationResult("UNKNOWN", None, None, "PENDING_UNKNOWN")
        finally:
            if cursor is not None:
                # Closing a cursor after successful commit cannot revoke its result.
                with contextlib.suppress(Exception):
                    cursor.close()


class ReceiptReader(PostgresStore):
    """Fixed current-permit reads on an injected qualified singleton/one-shot connection."""

    def read_issuer_operation(self, connection: Connection, value: dict[str, Any]) -> bytes:
        return self._read(connection, "read_issuer_operation", value)

    def read_provisioning_receipt(self, connection: Connection, value: dict[str, Any]) -> bytes:
        return self._read(connection, "read_provisioning_receipt", value)

    def _read(self, connection: Connection, operation: str, value: dict[str, Any]) -> bytes:
        require(isinstance(self.binding, ReceiptReadBinding), "AUTH_REFUSED")
        require(operation in {f.operation for f in self.binding.functions}, "AUTH_REFUSED")
        issuer = operation == "read_issuer_operation"
        exact(
            value,
            "protocol scope issuer_operation_id request_sha256 read_guard"
            if issuer
            else "protocol receipt_key request_sha256 guard",
        )
        require(
            value["protocol"]
            == ("maezo.d7-issuer-receipt-read.v1" if issuer else "maezo.d7-provisioning-receipt-read.v1")
        )
        guard = exact(
            value["read_guard" if issuer else "guard"],
            "protocol scope run_id epoch permit_id request_sha256 operation purpose",
        )
        require(guard["protocol"] == "maezo.d7-provisioning-guard.v1")
        require(guard["scope"] == self.binding.scope.to_wire(), "SCOPE_REFUSED")
        require(
            (guard["operation"], guard["purpose"])
            in (
                {("receipt", "receipt_read")}
                if issuer
                else {("receipt", "receipt_read"), ("deploy", "deploy"), ("grant", "grant")}
            ),
            "PURPOSE_REFUSED",
        )
        for name in ("run_id", "permit_id"):
            scalar("Uuid", guard[name])
        scalar("Positive", guard["epoch"])
        scalar("Sha256", value["request_sha256"])
        if issuer:
            scalar("Uuid", value["issuer_operation_id"])
            require(value["scope"] == guard["scope"], "SCOPE_REFUSED")
            query = {k: value[k] for k in ("scope", "issuer_operation_id", "request_sha256")}
        else:
            scalar("ReceiptKey", value["receipt_key"])
            require(
                all(
                    value["receipt_key"][k] == guard["scope"][k]
                    for k in ("tenant", "environment", "engine_name")
                ),
                "SCOPE_REFUSED",
            )
            query = {k: value[k] for k in ("receipt_key", "request_sha256")}
        require(
            guard["request_sha256"]
            == (digest(canonical(query)) if guard["purpose"] == "receipt_read" else value["request_sha256"]),
            "REQUEST_CONFLICT",
        )
        if guard["purpose"] != "receipt_read":
            require(
                value["receipt_key"]["operation"] == guard["operation"]
                and value["receipt_key"]["run_id"] == guard["run_id"],
                "REQUEST_CONFLICT",
            )
        present(self.authority, "UNAVAILABLE").qualify(
            operation=operation,
            request_wire=canonical(value),
            proof_wire=canonical(guard),
            scope_wire=self.binding.scope.canonical_bytes(),
            expected_database_wire=self.binding.database.canonical_bytes(),
        )
        require(
            connection.autocommit is False and connection.info.transaction_status == 0,
            "PRECONDITION_MISMATCH",
        )
        cursor = connection.cursor()
        try:
            cursor.execute("BEGIN ISOLATION LEVEL READ COMMITTED")
            self._verify_connection(cursor, operation)
            cursor.execute(READ_FUNCTIONS[operation], (canonical(value).decode("utf8"),))
            row = cursor.fetchone()
            require(type(row) in {tuple, list} and len(row) == 1)
            result = row[0]
            require(type(result) is dict)
            if issuer:
                exact(result, "protocol kind canonical_result_bytes result_sha256")
                require(result["protocol"] == "maezo.d7-issuer-receipt-read-result.v1")
                require(result["kind"] in {"FOUND", "NOT_FOUND"})
                if result["kind"] == "NOT_FOUND":
                    require(result["canonical_result_bytes"] is result["result_sha256"] is None)
                else:
                    retained = decode64(result["canonical_result_bytes"])
                    require(digest(retained) == result["result_sha256"])
                    historical = parse_wire(retained)
                    if historical.get("protocol") == "maezo.provisioning-issuer-receipt.v1":
                        c.parse("IssuerReceipt", retained)
                    else:
                        exact(
                            historical,
                            "protocol scope run_id epoch issuer_operation_id operation request_sha256 "
                            "fence_revision generation_id permit_id state issuer_session_user "
                            "issuer_login_oid issuer_current_user issuer_definer_oid "
                            "recorded_before_commit_at_ms provisioning_schema_version",
                        )
                        require(
                            historical["protocol"] == "maezo.d7-store-transition-receipt.v1"
                            and historical["operation"] in OPERATIONS - GENERATION_OPERATIONS
                            and historical["provisioning_schema_version"] == 1
                        )
                        scalar("Scope", historical["scope"])
                        for name in ("run_id", "issuer_operation_id"):
                            scalar("Uuid", historical[name])
                        for name in ("epoch", "fence_revision"):
                            scalar("Positive", historical[name])
                        for name in ("issuer_login_oid", "issuer_definer_oid"):
                            scalar("Oid", historical[name])
                        for name in ("issuer_session_user", "issuer_current_user"):
                            scalar("Id", historical[name])
                        scalar("UInt", historical["recorded_before_commit_at_ms"])
                        scalar("Sha256", historical["request_sha256"])
                        require(
                            historical["state"]
                            in {
                                "CLOSED",
                                "CANDIDATE",
                                "RESTORING",
                                "OPEN",
                                "RECOVERY_REQUIRED",
                                "ISSUED",
                                "REVOKED",
                            }
                        )
                        for name, grammar in (("generation_id", "Positive"), ("permit_id", "Uuid")):
                            if historical[name] is not None:
                                scalar(grammar, historical[name])
                    require(
                        all(
                            historical[k] == value[k]
                            for k in ("scope", "issuer_operation_id", "request_sha256")
                        ),
                        "REQUEST_CONFLICT",
                    )
            else:
                parsed = c.parse("ReceiptRead", canonical(result))
                require(parsed.receipt_key.to_wire() == value["receipt_key"], "REQUEST_CONFLICT")
                if result["kind"] == "FOUND":
                    require(result["request_sha256"] == value["request_sha256"], "REQUEST_CONFLICT")
            wire = canonical(result)
            connection.commit()
            return wire
        except Exception as error:
            with contextlib.suppress(Exception):
                connection.rollback()
            if isinstance(error, Refusal):
                raise
            state = getattr(error, "sqlstate", None)
            raise Refusal(
                scalar(
                    "RefusalCode",
                    SQLSTATES.get(state, "UNAVAILABLE") if isinstance(state, str) else "UNAVAILABLE",
                )
            ) from None
        finally:
            with contextlib.suppress(Exception):
                cursor.close()

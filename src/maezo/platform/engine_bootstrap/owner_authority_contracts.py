"""ADR-0055 closed synthetic owner records; parsing alone grants no authority."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, cast
from uuid import UUID

from .controller_storage import Document, canonical, digest, exact, key, parse_wire, require, scalar

OPERATIONS = frozenset({"bootstrap_scope", "install_d", "readback"})
ENROLLMENT_FIELDS = (
    "protocol mode enrollment_id installation_id control_scope_id bootstrap_operation_id "
    "scope table_arn table_id writer_arn observer_arn owner_login owner_oid source_manifest_sha256 "
    "cib_abi_sha256 operational_principals initial_root_sha256"
)
BINDING_FIELDS = (
    "protocol enrollment_sha256 enrollment_id installation_id control_scope_id "
    "bootstrap_operation_id scope table_arn table_id initial_root_sha256"
)
ROW_FIELDS = "PK SK kind document_bytes document_sha256"
MAX_OWNER_BYTES = 131_072


def uuid(value: Any) -> str:
    require(type(value) is str, "INVALID_BODY")
    try:
        parsed = UUID(value)
    except (ValueError, AttributeError):
        require(False, "INVALID_BODY")
        raise AssertionError("unreachable") from None
    require(str(parsed) == value, "INVALID_BODY")
    return cast(str, value)


def operational(value: Any) -> list[dict[str, Any]]:
    require(type(value) is list and 2 <= len(value) <= 1024)
    seen: list[tuple[str, str, str, int]] = []
    for row in value:
        exact(row, "purpose native_actor login_name login_oid")
        require(row["purpose"] == "receipt_read", "UNSUPPORTED_ADAPTER")
        for name in ("native_actor", "login_name"):
            scalar("Id", row[name])
        scalar("Oid", row["login_oid"])
        seen.append(tuple(row[n] for n in ("purpose", "native_actor", "login_name", "login_oid")))
    require(seen == sorted(set(seen)), "INVALID_BODY")
    require(len({r["login_name"] for r in value}) == len(value), "INVALID_BODY")
    require(len({r["login_oid"] for r in value}) == len(value), "INVALID_BODY")
    return cast(list[dict[str, Any]], value)


@dataclass(frozen=True, slots=True)
class Enrollment:
    wire: bytes

    def __post_init__(self) -> None:
        require(type(self.wire) is bytes and len(self.wire) <= MAX_OWNER_BYTES)
        v = exact(parse_wire(self.wire), ENROLLMENT_FIELDS)
        require(canonical(v) == self.wire)
        require(v["protocol"] == "maezo.d7-synthetic-owner-enrollment.v1")
        require(v["mode"] == "PUBLIC_SYNTHETIC", "AUTH_REFUSED")
        for field in ("enrollment_id", "installation_id", "control_scope_id", "bootstrap_operation_id"):
            uuid(v[field])
        scalar("Scope", v["scope"])
        uuid(v["scope"]["database_binding"]["database_incarnation"])
        for field in ("source_manifest_sha256", "cib_abi_sha256", "initial_root_sha256"):
            scalar("Sha256", v[field])
        for field in ("table_arn", "writer_arn", "observer_arn"):
            scalar("Arn", v[field])
        scope = v["scope"]
        prefix = f"arn:aws:dynamodb:{scope['region']}:{scope['account']}:table/"
        require(v["table_arn"].startswith(prefix) and "/" not in v["table_arn"][len(prefix) :])
        require(v["writer_arn"] != v["observer_arn"], "AUTH_REFUSED")
        scalar("Id", v["table_id"])
        scalar("Id", v["owner_login"])
        scalar("Oid", v["owner_oid"])
        operational(v["operational_principals"])

    def value(self) -> dict[str, Any]:
        return cast(dict[str, Any], parse_wire(self.wire))

    def binding(self) -> dict[str, Any]:
        v = self.value()
        return {
            "protocol": "maezo.d7-owner-scope-binding.v1",
            "enrollment_sha256": digest(self.wire),
            **{name: v[name] for name in BINDING_FIELDS.split()[2:]},
        }

    def root(self, raw: bytes) -> Document:
        root = Document("Root", raw)
        v, r = self.value(), root.value()
        require(root.digest() == v["initial_root_sha256"], "AUTH_REFUSED")
        require(r["scope"] == v["scope"] and r["control_scope_id"] == v["control_scope_id"], "SCOPE_REFUSED")
        require(r["state"] == "REVIEWED" and r["restore_state"] == "UNRECONCILED")
        require(r["epoch"] == r["revision"] == r["next_generation_id"] == 1)
        require(r["db_fence_epoch"] == r["journal_revision"] == 0 and not r["pending_operation_ids"])
        require(
            all(
                r[k] is None
                for k in (
                    "current_owner_claim_sha256",
                    "current_generation_id",
                    "activation_decision_sha256",
                    "restore_receipt_sha256",
                    "readiness_result_sha256",
                )
            )
        )
        require(
            all(
                r[k] == digest(canonical([]))
                for k in (
                    "pending_index_sha256",
                    "managed_registry_sha256",
                    "retired_index_sha256",
                )
            )
        )
        return root


def owner_row(pk: str, sk: str, kind: str, body: dict[str, Any]) -> dict[str, Any]:
    raw = canonical(body)
    require(len(raw) <= MAX_OWNER_BYTES)
    return {"PK": pk, "SK": sk, "kind": kind, "document_bytes": raw, "document_sha256": digest(raw)}


def bootstrap_rows(enrollment: Enrollment, root_wire: bytes) -> tuple[dict[str, Any], ...]:
    root = enrollment.root(root_wire)
    v, binding = enrollment.value(), enrollment.binding()
    request = {"operation": "bootstrap_scope", "binding": binding, "root_sha256": root.digest()}
    receipt = {
        "protocol": "maezo.d7-owner-bootstrap-receipt.v1",
        "operation_id": v["bootstrap_operation_id"],
        "request_sha256": digest(canonical(request)),
        "binding": binding,
        "root_sha256": root.digest(),
    }
    return (
        owner_row("D7OWNER#INSTALLATION#" + v["installation_id"], "ANCHOR", "installation", binding),
        owner_row("D7OWNER#SCOPE#" + v["control_scope_id"], "RESERVATION", "reservation", binding),
        {**key(v["control_scope_id"], "ROOT"), **root.value()},
        owner_row("D7OWNER#OPERATION#" + v["bootstrap_operation_id"], "BOOTSTRAP", "bootstrap", receipt),
    )


def attribute(v: Any) -> dict[str, Any]:
    if v is None:
        return {"NULL": True}
    if type(v) is bool:
        return {"BOOL": v}
    if type(v) is int:
        scalar("UInt", v)
        return {"N": str(v)}
    if type(v) is str:
        return {"S": v}
    if type(v) is bytes:
        return {"B": v}
    if type(v) is list:
        return {"L": [attribute(x) for x in v]}
    require(type(v) is dict)
    return {"M": {k: attribute(x) for k, x in v.items()}}


def item(v: dict[str, Any]) -> dict[str, Any]:
    return {k: attribute(x) for k, x in v.items()}


def unattribute(value: Any, depth: int = 0) -> Any:
    require(depth <= 32 and type(value) is dict and len(value) == 1)
    tag, value = next(iter(value.items()))
    if tag == "NULL":
        require(value is True)
        return None
    if tag == "BOOL":
        require(type(value) is bool)
        return value
    if tag == "S":
        require(type(value) is str)
        return value
    if tag == "N":
        require(type(value) is str and value.isascii() and value.isdigit())
        number = int(value)
        require(str(number) == value)
        scalar("UInt", number)
        return number
    if tag == "B":
        require(type(value) is bytes)
        return value
    if tag == "L":
        require(type(value) is list and len(value) <= 1024)
        return [unattribute(v, depth + 1) for v in value]
    require(tag == "M" and type(value) is dict and len(value) <= 1024)
    return {k: unattribute(v, depth + 1) for k, v in value.items()}

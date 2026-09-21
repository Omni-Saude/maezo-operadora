"""Fixed ADR-0055 bootstrap/strong-readback operations, with no runtime entrypoint."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, cast

from maezo.gateway.d7_external_owner import AwsOwnerClients, OwnerEnrollmentClient, OwnerGrant
from maezo.platform.engine_bootstrap.controller_storage import (
    Document,
    Refusal,
    canonical,
    parse_wire,
    require,
)
from maezo.platform.engine_bootstrap.owner_authority_contracts import (
    Enrollment,
    bootstrap_rows,
    item,
    unattribute,
)


@dataclass(frozen=True, slots=True)
class BootstrapResult:
    kind: str
    receipt_wire: bytes | None

    def __post_init__(self) -> None:
        require(self.kind in {"COMMITTED", "UNKNOWN"})
        require((self.receipt_wire is not None) == (self.kind == "COMMITTED"))


class ExternalOwnerBootstrap:
    """The only write is one four-item absent-only transaction; never retried."""

    def __init__(self, owner: OwnerEnrollmentClient, aws: AwsOwnerClients) -> None:
        require(type(owner) is OwnerEnrollmentClient and type(aws) is AwsOwnerClients, "UNAVAILABLE")
        self._owner, self._aws = owner, aws

    def _grant(self, operation: str, root_wire: bytes, context: dict[str, Any]) -> OwnerGrant:
        # Closed ROOT validation also bounds the request before any HTTP/AWS effect.
        root = Document("Root", root_wire)
        grant = self._owner.acquire(operation, {"root_sha256": root.digest()}, context)
        grant.enrollment.root(root_wire)
        self._aws.identity(grant.enrollment, writer=operation == "bootstrap_scope")
        grant.current()
        return grant

    def _read(self, enrollment: Enrollment, rows: tuple[dict[str, Any], ...]) -> bool:
        table = enrollment.value()["table_arn"]
        request = {
            "TransactItems": [
                {"Get": {"TableName": table, "Key": item({"PK": r["PK"], "SK": r["SK"]})}} for r in rows
            ]
        }
        try:
            response = self._aws.dynamodb.transact_get_items(**request)
            values = response.get("Responses")
            require(type(values) is list and len(values) == 4, "UNAVAILABLE")
            require(all(type(v) is dict for v in values), "UNAVAILABLE")
            if all(not v for v in values):
                return False
            require(all(set(v) == {"Item"} for v in values), "REQUEST_CONFLICT")
            require([v["Item"] for v in values] == [item(r) for r in rows], "REQUEST_CONFLICT")
            return True
        except Refusal:
            raise
        except Exception:
            raise Refusal("UNAVAILABLE") from None

    def _initial_partition(
        self, enrollment: Enrollment, root: dict[str, Any], *, empty: bool = False
    ) -> None:
        # Initial-only domain has exactly ROOT. A later journal/root lifecycle is
        # deliberately unsupported, not treated as a successful historical replay.
        try:
            response = self._aws.dynamodb.query(
                TableName=enrollment.value()["table_arn"],
                KeyConditionExpression="PK = :pk",
                ExpressionAttributeValues={":pk": {"S": root["PK"]}},
                ConsistentRead=True,
                Limit=2,
            )
            require(
                response.get("Items") == ([] if empty else [item(root)])
                and not response.get("LastEvaluatedKey"),
                "PRECONDITION_MISMATCH",
            )
        except Refusal:
            raise
        except Exception:
            raise Refusal("UNAVAILABLE") from None

    def _complete(self, grant: OwnerGrant, rows: tuple[dict[str, Any], ...]) -> bytes:
        grant.current()
        require(self._read(grant.enrollment, rows), "UNAVAILABLE")
        self._initial_partition(grant.enrollment, rows[2])
        require(self._read(grant.enrollment, rows), "UNAVAILABLE")
        grant.current()
        return cast(bytes, rows[3]["document_bytes"])

    def bootstrap(self, root_wire: bytes) -> BootstrapResult:
        grant = self._grant("bootstrap_scope", root_wire, {})
        rows = bootstrap_rows(grant.enrollment, root_wire)
        if self._read(grant.enrollment, rows):
            return BootstrapResult("COMMITTED", self._complete(grant, rows))
        # Refuse a visible orphan partition before creating any permanent anchor.
        # Exclusive writer authority is still an external qualification obligation.
        self._initial_partition(grant.enrollment, rows[2], empty=True)
        grant.current()
        request = {
            "TransactItems": [
                {
                    "Put": {
                        "TableName": grant.enrollment.value()["table_arn"],
                        "Item": item(row),
                        "ConditionExpression": "attribute_not_exists(#pk) AND attribute_not_exists(#sk)",
                        "ExpressionAttributeNames": {"#pk": "PK", "#sk": "SK"},
                    }
                }
                for row in rows
            ]
        }
        try:
            self._aws.dynamodb.transact_write_items(**request)
        except Exception:
            # A response is not a commit oracle. Read exact outcome; never resubmit.
            try:
                if not self._read(grant.enrollment, rows):
                    return BootstrapResult("UNKNOWN", None)
            except Refusal:
                return BootstrapResult("UNKNOWN", None)
        try:
            return BootstrapResult("COMMITTED", self._complete(grant, rows))
        except Refusal:
            return BootstrapResult("UNKNOWN", None)

    def readback(self, root_wire: bytes) -> bytes:
        grant = self._grant("readback", root_wire, {})
        return self._complete(grant, bootstrap_rows(grant.enrollment, root_wire))

    def installation_facts(
        self,
        *,
        input_wire: bytes,
        context: dict[str, Any],
    ) -> tuple[Enrollment, Document]:
        grant = self._owner.acquire("install_d", {"installation": parse_wire(input_wire)}, context)
        e = grant.enrollment.value()
        value = parse_wire(input_wire)
        require(
            value["installation_id"] == e["installation_id"]
            and value["control_scope_id"] == e["control_scope_id"],
            "SCOPE_REFUSED",
        )
        require(
            value["issuer_scopes"] == [e["scope"]]
            and value["database_binding"] == e["scope"]["database_binding"],
            "SCOPE_REFUSED",
        )
        require(
            value["controller_table_arn"] == e["table_arn"]
            and value["source_manifest_sha256"] == e["source_manifest_sha256"]
            and value["cib_abi_sha256"] == e["cib_abi_sha256"]
            and not value["prepared_principals"],
            "AUTH_REFUSED",
        )
        self._aws.identity(grant.enrollment, writer=False)
        response = self._aws.dynamodb.get_item(
            TableName=e["table_arn"],
            Key=item({"PK": "D7#" + e["control_scope_id"], "SK": "ROOT"}),
            ConsistentRead=True,
        )
        row = {k: unattribute(v) for k, v in response.get("Item", {}).items()}
        require(
            row.pop("PK", None) == "D7#" + e["control_scope_id"] and row.pop("SK", None) == "ROOT",
            "UNAVAILABLE",
        )
        root = grant.enrollment.root(canonical(row))
        self._complete(grant, bootstrap_rows(grant.enrollment, root.wire))
        grant.current()
        return grant.enrollment, root

    def preparation(self, **kwargs: Any) -> None:
        raise Refusal("UNAVAILABLE")

    def removal(self, **kwargs: Any) -> None:
        raise Refusal("UNAVAILABLE")

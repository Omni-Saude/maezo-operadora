"""Bounded real DynamoDB request adapter, with injected gateway-owned client.

No SDK construction, credential discovery, fallback store, TTL or automatic retry.
A protocol fake proves request construction only. Actual AWS authority/durability
requires the separately qualified owner installation and live lane.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from maezo.platform.engine_bootstrap import controller_contracts as c
from maezo.platform.engine_bootstrap.controller import Transition
from maezo.platform.engine_bootstrap.controller_storage import (
    CHUNK_BYTES,
    MAX_CHUNKS,
    MAX_INVENTORY,
    MAX_ITEMS,
    MAX_ROOT_BYTES,
    MAX_TRANSACTION_BYTES,
    ChunkSet,
    Document,
    Refusal,
    canonical,
    digest,
    encode64,
    exact,
    key,
    parse_wire,
    require,
    scalar,
)


class ControlAuthority(Protocol):
    """Gateway-owned installed table/client qualification, never caller assertions."""

    def qualify(
        self, *, client: object, table_arn: str, table_id: str, installation_sha256: str, scope_wire: bytes
    ) -> None: ...


class DynamoClient(Protocol):
    def describe_table(self, **kwargs: Any) -> dict[str, Any]: ...
    def get_item(self, **kwargs: Any) -> dict[str, Any]: ...
    def query(self, **kwargs: Any) -> dict[str, Any]: ...
    def transact_write_items(self, **kwargs: Any) -> dict[str, Any]: ...
    def transact_get_items(self, **kwargs: Any) -> dict[str, Any]: ...


@dataclass(frozen=True, slots=True)
class ControlBinding:
    table_arn: str
    table_id: str
    account: str
    region: str
    control_scope_id: str
    scope_wire: bytes
    owner_installation_sha256: str

    def __post_init__(self) -> None:
        scope = parse_wire(self.scope_wire)
        scalar("Scope", scope)
        scalar("Uuid", self.control_scope_id)
        scalar("Id", self.table_id)
        scalar("Sha256", self.owner_installation_sha256)
        require(scope["account"] == self.account and scope["region"] == self.region, "SCOPE_REFUSED")
        require(self.table_arn.startswith(f"arn:aws:dynamodb:{self.region}:{self.account}:table/"))
        require("/" not in self.table_arn.split(":table/", 1)[1])
        scalar("Arn", self.table_arn)

    def identity_digest(self) -> str:
        return digest(
            canonical(
                {
                    "table_arn": self.table_arn,
                    "table_id": self.table_id,
                    "owner_installation_sha256": self.owner_installation_sha256,
                }
            )
        )


def attribute(value: Any) -> dict[str, Any]:
    if value is None:
        return {"NULL": True}
    if type(value) is bool:
        return {"BOOL": value}
    if type(value) is int:
        scalar("UInt", value)
        return {"N": str(value)}
    if type(value) is str:
        return {"S": value}
    if type(value) is bytes:
        require(len(value) <= CHUNK_BYTES)
        return {"B": value}
    if type(value) is list:
        require(len(value) <= MAX_INVENTORY)
        return {"L": [attribute(v) for v in value]}
    require(type(value) is dict)
    return {"M": {k: attribute(v) for k, v in value.items()}}


def unattribute(value: Any, depth: int = 0) -> Any:
    require(depth <= 32 and type(value) is dict and len(value) == 1)
    tag, v = next(iter(value.items()))
    if tag == "NULL":
        require(v is True)
        return None
    if tag == "BOOL":
        require(type(v) is bool)
        return v
    if tag == "S":
        require(type(v) is str)
        return v
    if tag == "N":
        require(type(v) is str)
        parsed = parse_wire(v.encode("ascii"))
        require(type(parsed) is int)
        return parsed
    if tag == "B":
        require(type(v) is bytes and len(v) <= CHUNK_BYTES)
        return v
    if tag == "L":
        require(type(v) is list and len(v) <= MAX_INVENTORY)
        return [unattribute(x, depth + 1) for x in v]
    require(tag == "M" and type(v) is dict)
    return {k: unattribute(x, depth + 1) for k, x in v.items()}


def item(value: dict[str, Any]) -> dict[str, Any]:
    return {k: attribute(v) for k, v in value.items()}


def unitem(value: Any) -> dict[str, Any]:
    require(type(value) is dict)
    return {k: unattribute(v) for k, v in value.items()}


def _transport_size(value: Any) -> int:
    if type(value) is bytes:
        return len(value) * 4 // 3 + 8
    if type(value) is str:
        return len(value.encode("utf8")) + 4
    if isinstance(value, dict):
        return sum(_transport_size(k) + _transport_size(v) for k, v in value.items()) + 2
    if isinstance(value, list):
        return sum(_transport_size(v) for v in value) + 2
    return 20


def _append_key(binding: ControlBinding, sk: str) -> dict[str, str]:
    pieces = sk.split("#")
    kind = pieces[0]
    if kind in {"INTENT", "OWNERCLAIM"}:
        require(len(pieces) == 2)
        return key(binding.control_scope_id, kind, pieces[1])
    if kind == "OUTCOME":
        require(len(pieces) == 3)
        revision = parse_wire(pieces[2].encode())
        return key(binding.control_scope_id, "OUTCOME", pieces[1], revision=revision)
    if kind == "GENERATION":
        require(len(pieces) == 2)
        return key(binding.control_scope_id, "GENERATION", parse_wire(pieces[1].encode()))
    if kind == "RESOURCE":
        require(len(pieces) == 3)
        return key(binding.control_scope_id, "RESOURCE", pieces[2], resource_kind=pieces[1])
    require(kind == "PROOF" and len(pieces) == 2)
    return key(binding.control_scope_id, "PROOF", pieces[1])


def transaction(binding: ControlBinding, change: Transition) -> dict[str, Any]:
    """Return the actual closed low-level TransactWriteItems request."""
    old, new = change.expected.value(), change.replacement.value()
    require(
        old["scope"] == parse_wire(binding.scope_wire)
        and old["control_scope_id"] == binding.control_scope_id,
        "SCOPE_REFUSED",
    )
    rk = key(binding.control_scope_id, "ROOT")
    root_put = {
        "TableName": binding.table_arn,
        "Item": item({**rk, **new}),
        "ConditionExpression": "#revision = :revision AND #epoch = :epoch AND #run = :run AND #owner = :owner AND #pending = :pending AND #scope = :scope AND #lease = :lease AND #high = :high",
        "ExpressionAttributeNames": {
            "#revision": "revision",
            "#epoch": "epoch",
            "#run": "run_id",
            "#owner": "owner_subject",
            "#pending": "pending_index_sha256",
            "#scope": "scope",
            "#lease": "lease_deadline_ms",
            "#high": "next_generation_id",
        },
        "ExpressionAttributeValues": {
            ":revision": attribute(old["revision"]),
            ":epoch": attribute(old["epoch"]),
            ":run": attribute(old["run_id"]),
            ":owner": attribute(old["owner_subject"]),
            ":pending": attribute(old["pending_index_sha256"]),
            ":scope": attribute(old["scope"]),
            ":lease": attribute(old["lease_deadline_ms"]),
            ":high": attribute(old["next_generation_id"]),
        },
    }
    actions = [{"Put": root_put}]
    for sk, wire in change.append:
        physical = _append_key(binding, sk)
        value = parse_wire(wire)
        if sk.startswith("PROOF#"):
            require(sk.split("#")[1] == digest(wire))
        if sk.startswith("OUTCOME#"):
            journal = c.parse("JournalObservation", wire)
            require(journal.external_operation_id == sk.split("#")[1])
        else:
            require(value["scope"] == old["scope"], "SCOPE_REFUSED")
        if sk.startswith("INTENT#"):
            intent = c.parse("RunTaskIntent" if value.get("action") == "RunTask" else "StopTaskIntent", wire)
            require(intent.external_operation_id == sk.split("#")[1])
            chunks = ChunkSet.split(wire)
            manifest = {
                **physical,
                "schema_version": 1,
                "scope": old["scope"],
                "external_operation_id": intent.external_operation_id,
                "run_id": intent.logical_run_id,
                "epoch": intent.epoch,
                "generation_id": intent.target_generation,
                "intent_sha256": digest(wire),
                "body_bytes": len(wire),
                "body_chunk_count": len(chunks.chunks),
                "body_chunk_sha256s": list(chunks.hashes),
                "state": "INTENT",
                "revision": 1,
                "reservation": None,
                "provider_outcome": None,
                "settlement": None,
                "cancellation_unsent_proof": None,
            }
            actions.append(
                {
                    "Put": {
                        "TableName": binding.table_arn,
                        "Item": item(manifest),
                        "ConditionExpression": "attribute_not_exists(PK) AND attribute_not_exists(SK)",
                    }
                }
            )
            for ordinal, data in enumerate(chunks.chunks):
                chunk = {
                    **key(binding.control_scope_id, "INTENT", intent.external_operation_id, ordinal=ordinal),
                    "scope": old["scope"],
                    "external_operation_id": intent.external_operation_id,
                    "ordinal": ordinal,
                    "bytes": data,
                    "chunk_sha256": chunks.hashes[ordinal],
                }
                actions.append(
                    {
                        "Put": {
                            "TableName": binding.table_arn,
                            "Item": item(chunk),
                            "ConditionExpression": "attribute_not_exists(PK) AND attribute_not_exists(SK)",
                        }
                    }
                )
        elif len(wire) <= MAX_ROOT_BYTES:
            body = {**physical, "document_bytes": wire, "document_sha256": digest(wire)}
            actions.append(
                {
                    "Put": {
                        "TableName": binding.table_arn,
                        "Item": item(body),
                        "ConditionExpression": "attribute_not_exists(PK) AND attribute_not_exists(SK)",
                    }
                }
            )
        else:
            require(sk.startswith(("INTENT#", "PROOF#")), "INVALID_BODY")
            chunks = ChunkSet.split(wire)
            manifest = {
                **physical,
                "scope": old["scope"],
                "body_bytes": len(wire),
                "body_chunk_count": len(chunks.chunks),
                "body_chunk_sha256s": list(chunks.hashes),
                "document_sha256": digest(wire),
            }
            actions.append(
                {
                    "Put": {
                        "TableName": binding.table_arn,
                        "Item": item(manifest),
                        "ConditionExpression": "attribute_not_exists(PK) AND attribute_not_exists(SK)",
                    }
                }
            )
            prefix, identity = sk.split("#", 1)
            for ordinal, b in enumerate(chunks.chunks):
                ck = key(binding.control_scope_id, prefix, identity, ordinal=ordinal)
                chunk = {
                    **ck,
                    "scope": old["scope"],
                    "ordinal": ordinal,
                    "bytes": b,
                    "chunk_sha256": chunks.hashes[ordinal],
                }
                actions.append(
                    {
                        "Put": {
                            "TableName": binding.table_arn,
                            "Item": item(chunk),
                            "ConditionExpression": "attribute_not_exists(PK) AND attribute_not_exists(SK)",
                        }
                    }
                )
    for delta in change.journals:
        before, after = delta.expected.to_wire(), delta.replacement.to_wire()
        mutable = ("state", "reservation", "provider_outcome", "settlement", "cancellation_unsent_proof")
        names = {
            "#revision": "revision",
            "#intent": "intent_sha256",
            "#epoch": "epoch",
            **{"#" + field: field for field in mutable},
        }
        values = {
            ":one": attribute(1),
            ":intent": attribute(before["intent_sha256"]),
            ":epoch": attribute(before["epoch"]),
        }
        for field in mutable:
            values[":old_" + field] = attribute(before[field])
            values[":new_" + field] = attribute(after[field])
        actions.append(
            {
                "Update": {
                    "TableName": binding.table_arn,
                    "Key": item(key(binding.control_scope_id, "INTENT", before["external_operation_id"])),
                    "UpdateExpression": "SET #revision = #revision + :one, "
                    + ", ".join("#" + k + " = :new_" + k for k in mutable),
                    "ConditionExpression": "#intent = :intent AND #epoch = :epoch AND "
                    + " AND ".join("#" + k + " = :old_" + k for k in mutable),
                    "ExpressionAttributeNames": names,
                    "ExpressionAttributeValues": values,
                }
            }
        )
    for delta in change.documents:
        physical = _append_key(binding, delta.key)
        values = {
            **physical,
            "document_bytes": delta.replacement_wire,
            "document_sha256": digest(delta.replacement_wire),
        }
        put = {"TableName": binding.table_arn, "Item": item(values)}
        if delta.expected_wire is None:
            put["ConditionExpression"] = "attribute_not_exists(PK) AND attribute_not_exists(SK)"
        else:
            put.update(
                ConditionExpression="#digest = :digest AND #bytes = :bytes",
                ExpressionAttributeNames={"#digest": "document_sha256", "#bytes": "document_bytes"},
                ExpressionAttributeValues={
                    ":digest": attribute(digest(delta.expected_wire)),
                    ":bytes": attribute(delta.expected_wire),
                },
            )
        actions.append({"Put": put})
    require(len(actions) <= MAX_ITEMS and _transport_size(actions) <= MAX_TRANSACTION_BYTES)
    require(all(_transport_size(a) <= 300_000 for a in actions))
    # Deliberately no provider ClientRequestToken: logical IDs/CAS and explicit readback
    # preserve unknown attempts without pretending the bounded token is lifetime dedup.
    return {"TransactItems": actions}


@dataclass(frozen=True, slots=True)
class WriteResult:
    kind: str
    expected_root_sha256: str
    replacement_root_sha256: str

    def __post_init__(self) -> None:
        require(self.kind in {"COMMITTED", "CONFLICT", "UNKNOWN"})
        scalar("Sha256", self.expected_root_sha256)
        scalar("Sha256", self.replacement_root_sha256)


class DynamoDBStore:
    def __init__(
        self, client: DynamoClient, binding: ControlBinding, authority: ControlAuthority | None = None
    ) -> None:
        require(client is not None and type(binding) is ControlBinding, "UNAVAILABLE")
        self._client = client
        self.binding = binding
        require(authority is not None, "UNAVAILABLE")
        self._authority = authority

    def _identity(self) -> None:
        """Read actual table identity on the injected already authenticated connection."""
        try:
            self._authority.qualify(
                client=self._client,
                table_arn=self.binding.table_arn,
                table_id=self.binding.table_id,
                installation_sha256=self.binding.owner_installation_sha256,
                scope_wire=self.binding.scope_wire,
            )
            table = self._client.describe_table(TableName=self.binding.table_arn)["Table"]
            require(
                table["TableArn"] == self.binding.table_arn and table["TableId"] == self.binding.table_id,
                "SCOPE_REFUSED",
            )
            require(table["TableStatus"] == "ACTIVE" and not table.get("Replicas"), "UNSUPPORTED_ADAPTER")
            require(
                sorted(table["KeySchema"], key=lambda x: x["KeyType"])
                == [{"AttributeName": "PK", "KeyType": "HASH"}, {"AttributeName": "SK", "KeyType": "RANGE"}],
                "UNSUPPORTED_ADAPTER",
            )
        except Refusal:
            raise
        except Exception:
            raise Refusal("UNAVAILABLE") from None

    def read_root(self) -> Document:
        self._identity()
        try:
            response = self._client.get_item(
                TableName=self.binding.table_arn,
                Key=item(key(self.binding.control_scope_id, "ROOT")),
                ConsistentRead=True,
            )
            require("Item" in response, "UNAVAILABLE")
            v = unitem(response["Item"])
            require(v.pop("PK") == key(self.binding.control_scope_id, "ROOT")["PK"] and v.pop("SK") == "ROOT")
            root = Document.create("Root", v)
            require(
                v["scope"] == parse_wire(self.binding.scope_wire)
                and v["control_scope_id"] == self.binding.control_scope_id,
                "SCOPE_REFUSED",
            )
            return root
        except Refusal:
            raise
        except Exception:
            raise Refusal("UNAVAILABLE") from None

    def apply(self, change: Transition) -> WriteResult:
        self._identity()
        request = transaction(self.binding, change)
        try:
            response = self._client.transact_write_items(**request)
            require(
                type(response) is dict and response.get("ResponseMetadata", {}).get("HTTPStatusCode") == 200,
                "UNAVAILABLE",
            )
        except Exception as exc:
            # Cancellation must prove every action uncommitted; an unclassified service
            # error or network exception remains UNKNOWN. Never retry here.
            response = getattr(exc, "response", None)
            reasons = response.get("CancellationReasons") if isinstance(response, dict) else None
            conflict = (
                isinstance(response, dict)
                and response.get("Error", {}).get("Code") == "TransactionCanceledException"
                and isinstance(reasons, list)
                and len(reasons) == len(request["TransactItems"])
                and any(r.get("Code") == "ConditionalCheckFailed" for r in reasons)
                and all(r.get("Code") in {"None", "ConditionalCheckFailed"} for r in reasons)
            )
            return WriteResult(
                "CONFLICT" if conflict else "UNKNOWN", change.expected.digest(), change.replacement.digest()
            )
        return WriteResult("COMMITTED", change.expected.digest(), change.replacement.digest())

    def read_document(self, sk: str) -> bytes | None:
        self._identity()
        k = _append_key(self.binding, sk)
        try:
            result = self._client.get_item(TableName=self.binding.table_arn, Key=item(k), ConsistentRead=True)
            if "Item" not in result:
                return None  # Historical absence only, never no-in-flight proof.
            row = unitem(result["Item"])
            require(row.pop("PK") == k["PK"] and row.pop("SK") == k["SK"])
            if "document_bytes" in row:
                exact(row, "document_bytes document_sha256")
                wire = row["document_bytes"]
                require(digest(wire) == row["document_sha256"])
                parse_wire(wire)
                return wire
            intent_manifest = sk.startswith("INTENT#")
            if intent_manifest:
                exact(
                    row,
                    "schema_version scope external_operation_id run_id epoch generation_id intent_sha256 body_bytes body_chunk_count body_chunk_sha256s state revision reservation provider_outcome settlement cancellation_unsent_proof",
                )
                require(row["schema_version"] == 1 and row["external_operation_id"] == sk.split("#")[1])
                scalar("Positive", row["revision"])
            else:
                exact(row, "scope body_bytes body_chunk_count body_chunk_sha256s document_sha256")
            require(
                row["scope"] == parse_wire(self.binding.scope_wire)
                and type(row["body_chunk_count"]) is int
                and 1 <= row["body_chunk_count"] <= MAX_CHUNKS
            )
            prefix, identity = sk.split("#", 1)
            request = {
                "TransactItems": [
                    {
                        "Get": {
                            "TableName": self.binding.table_arn,
                            "Key": item(key(self.binding.control_scope_id, prefix, identity, ordinal=i)),
                        }
                    }
                    for i in range(row["body_chunk_count"])
                ]
            }
            chunks_result = self._client.transact_get_items(**request)["Responses"]
            require(len(chunks_result) == row["body_chunk_count"])
            chunks = []
            for ordinal, response in enumerate(chunks_result):
                chunk = unitem(response["Item"])
                expected = key(self.binding.control_scope_id, prefix, identity, ordinal=ordinal)
                require(chunk.pop("PK") == expected["PK"] and chunk.pop("SK") == expected["SK"])
                require(chunk.pop("scope") == row["scope"])
                if intent_manifest:
                    require(chunk.pop("external_operation_id") == row["external_operation_id"])
                chunks.append(chunk)
            assembled = ChunkSet.assemble(
                chunks,
                row["body_chunk_sha256s"],
                row["intent_sha256"] if intent_manifest else row["document_sha256"],
            )
            require(len(assembled.wire) == row["body_bytes"])
            if intent_manifest:
                self._journal(row, assembled.wire)
            return assembled.wire
        except Refusal:
            raise
        except Exception:
            raise Refusal("UNAVAILABLE") from None

    @staticmethod
    def _journal(row: dict[str, Any], wire: bytes) -> c.JournalObservation:
        value = parse_wire(wire)
        intent = c.parse("RunTaskIntent" if value.get("action") == "RunTask" else "StopTaskIntent", wire)
        require(
            intent.scope.to_wire() == row["scope"]
            and intent.external_operation_id == row["external_operation_id"]
            and intent.logical_run_id == row["run_id"]
            and intent.epoch == row["epoch"]
            and intent.target_generation == row["generation_id"]
            and intent.digest() == row["intent_sha256"]
        )
        return c.parse(
            "JournalObservation",
            canonical(
                {
                    "external_operation_id": intent.external_operation_id,
                    "logical_run_id": intent.logical_run_id,
                    "epoch": intent.epoch,
                    "generation_id": intent.target_generation,
                    "intent_sha256": intent.digest(),
                    "action": intent.action,
                    **{
                        k: row[k]
                        for k in (
                            "state",
                            "reservation",
                            "provider_outcome",
                            "settlement",
                            "cancellation_unsent_proof",
                        )
                    },
                }
            ),
        )

    def read_journal(self, external_operation_id: str) -> c.JournalObservation:
        before = self.read_root()
        wire = self.read_document("INTENT#" + external_operation_id)
        require(wire is not None, "UNAVAILABLE")
        response = self._client.get_item(
            TableName=self.binding.table_arn,
            Key=item(key(self.binding.control_scope_id, "INTENT", external_operation_id)),
            ConsistentRead=True,
        )
        row = unitem(response["Item"])
        row.pop("PK")
        row.pop("SK")
        result = self._journal(row, wire)
        require(self.read_root().wire == before.wire, "PRECONDITION_MISMATCH")
        return result

    def read_complete(self) -> tuple[Document, tuple[dict[str, Any], ...]]:
        before = self.read_root()
        rows = []
        cursor: dict[str, Any] | None = None
        seen = set()
        try:
            for _ in range(MAX_INVENTORY):
                request = {
                    "TableName": self.binding.table_arn,
                    "KeyConditionExpression": "PK = :pk",
                    "ExpressionAttributeValues": {
                        ":pk": attribute(key(self.binding.control_scope_id, "ROOT")["PK"])
                    },
                    "ConsistentRead": True,
                    "Limit": MAX_INVENTORY,
                }
                if cursor is not None:
                    request["ExclusiveStartKey"] = cursor
                response = self._client.query(**request)
                require(type(response.get("Items")) is list)
                for raw in response["Items"]:
                    row = unitem(raw)
                    require(row["PK"] == key(self.binding.control_scope_id, "ROOT")["PK"])
                    require(row["SK"] not in seen)
                    seen.add(row["SK"])
                    rows.append(row)
                    require(len(rows) <= MAX_INVENTORY, "UNAVAILABLE")
                nxt = response.get("LastEvaluatedKey")
                if not nxt:
                    break
                require(nxt != cursor and type(nxt) is dict, "UNAVAILABLE")
                cursor = nxt
            else:
                raise Refusal("UNAVAILABLE")
            after = self.read_root()
            require(before.wire == after.wire, "PRECONDITION_MISMATCH")
            require("ROOT" in seen, "UNAVAILABLE")
            return before, tuple(rows)
        except Refusal:
            raise
        except Exception:
            raise Refusal("UNAVAILABLE") from None

    def read_owner_lineage(
        self,
        *,
        expected_old_owner: dict[str, Any],
        challenge_sha256: str,
        authenticated_observer_subject: str,
        observed_at_ms: int,
        deadline_ms: int,
    ) -> Document:
        """Complete stable lineage projection; caller still authenticates its fixed transport."""
        scalar("Sha256", challenge_sha256)
        scalar("Id", authenticated_observer_subject)
        before, rows = self.read_complete()
        root = before.value()
        claims = {}
        for row in rows:
            if row["SK"].startswith("OWNERCLAIM#"):
                raw = self.read_document(row["SK"])
                require(raw is not None, "UNAVAILABLE")
                claim = Document("OwnerClaimReceipt", raw)
                claims[claim.digest()] = claim
        pointer = root["current_owner_claim_sha256"]
        chain = []
        while pointer is not None:
            require(pointer in claims and len(chain) < 32, "UNAVAILABLE")
            claim = claims[pointer]
            chain.append(claim)
            if claim.value()["previous_owner"] == expected_old_owner:
                break
            pointer = claim.value()["previous_claim_sha256"]
        if root["current_owner_claim_sha256"] is not None:
            require(chain[-1].value()["previous_owner"] == expected_old_owner, "PRECONDITION_MISMATCH")
        require(self.read_root().wire == before.wire, "PRECONDITION_MISMATCH")
        current = chain[0] if chain else None
        ancestors = list(reversed(chain[1:]))
        return Document.create(
            "OwnerLineageRead",
            {
                "protocol": "maezo.d7-owner-lineage-read.v1",
                "scope": root["scope"],
                "control_scope_id": self.binding.control_scope_id,
                "table_identity_sha256": self.binding.identity_digest(),
                "challenge_sha256": challenge_sha256,
                "authenticated_observer_subject": authenticated_observer_subject,
                "root_bytes": encode64(before.wire),
                "root_sha256": before.digest(),
                "claim_receipt_bytes": encode64(current.wire) if current else None,
                "claim_receipt_sha256": current.digest() if current else None,
                "ancestor_claim_bytes": [encode64(c.wire) for c in ancestors],
                "ancestor_claim_sha256s": [c.digest() for c in ancestors],
                "strong_read_revision": root["revision"],
                "reread_revision": root["revision"],
                "observed_at_ms": observed_at_ms,
                "deadline_ms": deadline_ms,
            },
        )

"""Bounded real DynamoDB request adapter, with injected gateway-owned client.

No SDK construction, credential discovery, fallback store, TTL or automatic retry.
A protocol fake proves request construction only. Actual AWS authority/durability
requires the separately qualified owner installation and live lane.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Protocol, cast

from maezo.platform.engine_bootstrap import controller as control
from maezo.platform.engine_bootstrap import controller_contracts as c
from maezo.platform.engine_bootstrap import controller_storage as storage
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
    parse_intent,
    parse_wire,
    present,
    require,
    scalar,
)


class ControlAuthority(Protocol):
    """Gateway-owned installed table/client qualification, never caller assertions."""

    def qualify(
        self, *, client: object, table_arn: str, table_id: str, installation_sha256: str, scope_wire: bytes
    ) -> None: ...


class InitialRootAuthority(Protocol):
    """Owner-qualified bootstrap of a never-used control scope, distinct from table access."""

    def qualify_initial_root(self, *, root_wire: bytes, table_identity_sha256: str) -> None: ...


@dataclass(frozen=True, slots=True)
class InitialWriteResult:
    kind: str
    root_sha256: str

    def __post_init__(self) -> None:
        require(self.kind in {"COMMITTED", "CONFLICT", "UNKNOWN"})
        scalar("Sha256", self.root_sha256)


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
        "ConditionExpression": (
            "#revision = :revision AND #epoch = :epoch AND #run = :run AND "
            "#owner = :owner AND #pending = :pending AND #scope = :scope AND "
            "#lease = :lease AND #high = :high"
        ),
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
            intent = parse_intent(wire)
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
    for document_delta in change.documents:
        physical = _append_key(binding, document_delta.key)
        document_values: dict[str, Any] = {
            **physical,
            "document_bytes": document_delta.replacement_wire,
            "document_sha256": digest(document_delta.replacement_wire),
        }
        put = {"TableName": binding.table_arn, "Item": item(document_values)}
        if document_delta.expected_wire is None:
            put["ConditionExpression"] = "attribute_not_exists(PK) AND attribute_not_exists(SK)"
        else:
            put.update(
                ConditionExpression="#digest = :digest AND #bytes = :bytes",
                ExpressionAttributeNames={"#digest": "document_sha256", "#bytes": "document_bytes"},
                ExpressionAttributeValues={
                    ":digest": attribute(digest(document_delta.expected_wire)),
                    ":bytes": attribute(document_delta.expected_wire),
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


@dataclass(frozen=True, slots=True)
class _StageView:
    manifest: Document
    steps: tuple[Document, ...]
    fragments: dict[str, Document]
    resources: dict[str, bytes]
    published: Document | None
    fenced: Document | None


def _package(actions: list[dict[str, Any]]) -> dict[str, Any]:
    require(0 < len(actions) <= MAX_ITEMS and _transport_size(actions) <= MAX_TRANSACTION_BYTES)
    require(all(_transport_size(action) <= 300_000 for action in actions))
    return {"TransactItems": actions}


def _absent_put(binding: ControlBinding, row: dict[str, Any]) -> dict[str, Any]:
    return {
        "Put": {
            "TableName": binding.table_arn,
            "Item": item(row),
            "ConditionExpression": "attribute_not_exists(PK) AND attribute_not_exists(SK)",
        }
    }


def _exact_put(binding: ControlBinding, before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    require(set(before) == set(after) and before["PK"] == after["PK"] and before["SK"] == after["SK"])
    fields = sorted(before)
    return {
        "Put": {
            "TableName": binding.table_arn,
            "Item": item(after),
            "ConditionExpression": " AND ".join(f"#f{i} = :v{i}" for i in range(len(fields))),
            "ExpressionAttributeNames": {f"#f{i}": field for i, field in enumerate(fields)},
            "ExpressionAttributeValues": {
                f":v{i}": attribute(before[field]) for i, field in enumerate(fields)
            },
        }
    }


def _root_cas(binding: ControlBinding, before: Document, after: Document) -> dict[str, Any]:
    physical = key(binding.control_scope_id, "ROOT")
    require(
        before.value()["scope"] == parse_wire(binding.scope_wire)
        and before.value()["control_scope_id"] == binding.control_scope_id,
        "SCOPE_REFUSED",
    )
    return _exact_put(binding, {**physical, **before.value()}, {**physical, **after.value()})


def _record_row(physical: dict[str, str], record: Document) -> dict[str, Any]:
    require(len(record.wire) <= MAX_ROOT_BYTES)
    return {**physical, "document_bytes": record.wire, "document_sha256": record.digest()}


def _record(row: dict[str, Any], kind: str) -> Document:
    exact(row, "PK SK document_bytes document_sha256")
    result = Document(kind, row["document_bytes"])
    require(result.digest() == row["document_sha256"])
    return result


def _step_request(step: dict[str, Any]) -> Document:
    return Document.create(
        "StepRequestIdentity",
        {
            "protocol": storage.STAGING_PROTOCOLS["StepRequestIdentity"],
            **{
                k: step[k]
                for k in (
                    "scope",
                    "publication_sha256",
                    "ordinal",
                    "previous_step_sha256",
                    "expected_root_sha256",
                    "replacement_root_sha256",
                    "fragment_keys",
                    "fragment_sha256s",
                )
            },
            "creates_manifest": step["ordinal"] == 0,
        },
    )


class DynamoDBStore:
    def __init__(
        self, client: DynamoClient, binding: ControlBinding, authority: ControlAuthority | None = None
    ) -> None:
        require(client is not None and type(binding) is ControlBinding, "UNAVAILABLE")
        self._client = client
        self.binding = binding
        require(authority is not None, "UNAVAILABLE")
        self._authority = present(authority, "UNAVAILABLE")

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

    def initialize(self, root: Document, *, authority: InitialRootAuthority | None) -> InitialWriteResult:
        """One absent-only ROOT insertion; ambiguity never retries or bootstraps a new identity."""
        self._identity()
        require(root.kind == "Root")
        value = root.value()
        require(
            value["control_scope_id"] == self.binding.control_scope_id
            and value["scope"] == parse_wire(self.binding.scope_wire),
            "SCOPE_REFUSED",
        )
        require(
            value["state"] == "REVIEWED"
            and value["restore_state"] == "UNRECONCILED"
            and value["epoch"] == value["revision"] == value["next_generation_id"] == 1
            and value["journal_revision"] == 0
            and value["db_fence_epoch"] == 0
            and not value["pending_operation_ids"],
            "PRECONDITION_MISMATCH",
        )
        require(
            all(
                value[k] is None
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
                value[k] == digest(canonical([]))
                for k in ("pending_index_sha256", "managed_registry_sha256", "retired_index_sha256")
            )
        )
        present(authority, "UNAVAILABLE").qualify_initial_root(
            root_wire=root.wire, table_identity_sha256=self.binding.identity_digest()
        )
        request = {
            "TransactItems": [
                {
                    "Put": {
                        "TableName": self.binding.table_arn,
                        "Item": item({**key(self.binding.control_scope_id, "ROOT"), **value}),
                        "ConditionExpression": "attribute_not_exists(PK) AND attribute_not_exists(SK)",
                    }
                }
            ]
        }
        try:
            response = self._client.transact_write_items(**request)
            require(response.get("ResponseMetadata", {}).get("HTTPStatusCode") == 200, "UNAVAILABLE")
        except Exception as error:
            response = getattr(error, "response", {})
            conflict = (
                isinstance(response, dict)
                and response.get("Error", {}).get("Code") == "TransactionCanceledException"
                and response.get("CancellationReasons") == [{"Code": "ConditionalCheckFailed"}]
            )
            return InitialWriteResult("CONFLICT" if conflict else "UNKNOWN", root.digest())
        return InitialWriteResult("COMMITTED", root.digest())

    def apply(self, change: Transition) -> WriteResult:
        self._identity()
        targets = {delta.key for delta in change.documents if delta.key.startswith("RESOURCE#")}
        targets.update(sk for sk, _ in change.append if sk.startswith("RESOURCE#"))
        if targets:
            actual, _, _, _, views = self._inventory()
            require(actual.wire == change.expected.wire, "PRECONDITION_MISMATCH")
            owner = actual.value()
            for view in views.values():
                m = view.manifest.value()
                require(
                    not (view.published and targets & {r["logical_key"] for r in m["resources"]}),
                    "PRECONDITION_MISMATCH",
                )
                require(
                    not (
                        view.published is None
                        and view.fenced is None
                        and all(m[k] == owner[k] for k in ("owner_subject", "run_id", "epoch"))
                    ),
                    "PENDING_UNKNOWN",
                )
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
            error_response = getattr(exc, "response", None)
            reasons = error_response.get("CancellationReasons") if isinstance(error_response, dict) else None
            conflict = (
                isinstance(error_response, dict)
                and error_response.get("Error", {}).get("Code") == "TransactionCanceledException"
                and isinstance(reasons, list)
                and len(reasons) == len(request["TransactItems"])
                and any(r.get("Code") == "ConditionalCheckFailed" for r in reasons)
                and all(r.get("Code") in {"None", "ConditionalCheckFailed"} for r in reasons)
            )
            return WriteResult(
                "CONFLICT" if conflict else "UNKNOWN", change.expected.digest(), change.replacement.digest()
            )
        return WriteResult("COMMITTED", change.expected.digest(), change.replacement.digest())

    def _read_base_document(self, sk: str) -> bytes | None:
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
                return cast(bytes, wire)
            intent_manifest = sk.startswith("INTENT#")
            if intent_manifest:
                exact(
                    row,
                    (
                        "schema_version scope external_operation_id run_id epoch "
                        "generation_id intent_sha256 body_bytes body_chunk_count "
                        "body_chunk_sha256s state revision reservation provider_outcome "
                        "settlement cancellation_unsent_proof"
                    ),
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

    def read_document(self, sk: str) -> bytes | None:
        if sk.startswith("RESOURCE#"):
            _append_key(self.binding, sk)
            _, _, resources = self.read_inventory()
            for resource in resources:
                identity = digest(
                    canonical(
                        {
                            "scope": resource.scope.to_wire(),
                            "resource_kind": "task",
                            "provider_resource_id": resource.provider_resource_id,
                        }
                    )
                )
                if sk == "RESOURCE#task#" + identity:
                    return resource.canonical_bytes()
            return None
        return self._read_base_document(sk)

    @staticmethod
    def _journal(row: dict[str, Any], wire: bytes) -> c.JournalObservation:
        intent = parse_intent(wire)
        require(
            intent.scope.to_wire() == row["scope"]
            and intent.external_operation_id == row["external_operation_id"]
            and intent.logical_run_id == row["run_id"]
            and intent.epoch == row["epoch"]
            and intent.target_generation == row["generation_id"]
            and intent.digest() == row["intent_sha256"]
        )
        parsed = c.parse(
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

        return cast(c.JournalObservation, parsed)

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
        result = self._journal(row, present(wire))
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
            retained_root = next(row for row in rows if row["SK"] == "ROOT")
            require(
                Document.create(
                    "Root", {k: v for k, v in retained_root.items() if k not in {"PK", "SK"}}
                ).wire
                == before.wire,
                "PRECONDITION_MISMATCH",
            )
            return before, tuple(rows)
        except Refusal:
            raise
        except Exception:
            raise Refusal("UNAVAILABLE") from None

    def _stage_views(
        self, root: Document, rows: tuple[dict[str, Any], ...], journals: tuple[c.JournalObservation, ...]
    ) -> dict[str, _StageView]:
        """Verify every physical staging row; partial prefixes never enter the resource projection."""
        grouped: dict[str, dict[str, dict[str, Any]]] = {}
        physical = {row["SK"]: row for row in rows}
        journal_map = {j.external_operation_id: j for j in journals}
        revisions: set[int] = set()
        for row in rows:
            sk = row["SK"]
            if sk.startswith(("STAGE#", "PUBLICATION#")):
                parts = sk.split("#")
                scalar("Sha256", parts[1])
                grouped.setdefault(parts[1], {})[sk] = row
        result = {}
        for publication, members in grouped.items():

            def sk(kind: str, publication_id: str = publication, **kwargs: Any) -> str:
                return storage.staging_key(self.binding.control_scope_id, publication_id, kind, **kwargs)[
                    "SK"
                ]

            manifest = _record(present(members.get(sk("MANIFEST")), "UNAVAILABLE"), "PublicationManifest")
            m = manifest.value()
            require(
                manifest.digest() == publication
                and m["scope"] == parse_wire(self.binding.scope_wire)
                and m["control_scope_id"] == self.binding.control_scope_id
                and m["table_identity_sha256"] == self.binding.identity_digest(),
                "SCOPE_REFUSED",
            )
            expected = []
            resource_by_key = {}
            for resource in m["resources"]:
                resource_by_key[resource["logical_key"]] = resource
                for ordinal, h in enumerate(resource["chunk_sha256s"]):
                    expected.append(
                        (
                            sk("RESOURCE", resource=resource["logical_key"].split("#")[2], ordinal=ordinal),
                            resource,
                            ordinal,
                            h,
                        )
                    )
            require(len(expected) <= 80)
            steps: list[Document] = []
            fragments: dict[str, Document] = {}
            seen = {sk("MANIFEST")}
            cursor = 0
            previous = None
            prior_revision = m["anchor_root_revision"]
            for ordinal in range(80):
                step_row = members.get(sk("STEP", ordinal=ordinal))
                if step_row is None:
                    break
                step = _record(step_row, "StageStepReceipt")
                v = step.value()
                require(
                    v["scope"] == m["scope"]
                    and v["publication_sha256"] == publication
                    and v["ordinal"] == ordinal
                    and v["previous_step_sha256"] == previous
                )
                require(
                    v["expected_root_revision"] >= prior_revision
                    and v["replacement_root_revision"] <= root.value()["revision"]
                )
                if ordinal == 0:
                    require(v["expected_root_revision"] == m["anchor_root_revision"])
                elif v["expected_root_revision"] == prior_revision:
                    require(v["expected_root_sha256"] == steps[-1].value()["replacement_root_sha256"])
                require(v["replacement_root_revision"] not in revisions)
                revisions.add(v["replacement_root_revision"])
                selected = expected[cursor : cursor + len(v["fragment_keys"])]
                require([x[0] for x in selected] == v["fragment_keys"])
                for (fragment_key, resource, index, h), fragment_hash in zip(
                    selected, v["fragment_sha256s"], strict=True
                ):
                    row = present(members.get(fragment_key), "UNAVAILABLE")
                    exact(row, "PK SK " + storage.STAGING_FIELDS["StageFragment"])
                    raw = row["bytes"]
                    fragment = Document.create(
                        "StageFragment",
                        {
                            **{k: value for k, value in row.items() if k not in {"PK", "SK", "bytes"}},
                            "bytes": encode64(raw),
                        },
                    )
                    f = fragment.value()
                    require(
                        f["scope"] == m["scope"]
                        and f["publication_sha256"] == publication
                        and f["resource_identity_sha256"] == resource["logical_key"].split("#")[2]
                        and f["ordinal"] == index
                        and f["chunk_sha256"] == h
                        and fragment.digest() == fragment_hash
                    )
                    expected_size = min(CHUNK_BYTES, resource["body_bytes"] - index * CHUNK_BYTES)
                    require(len(raw) == expected_size)
                    fragments[fragment_key] = fragment
                    seen.add(fragment_key)
                require(_step_request(v).digest() == v["request_sha256"])
                seen.add(sk("STEP", ordinal=ordinal))
                cursor += len(selected)
                steps.append(step)
                previous, prior_revision = step.digest(), v["replacement_root_revision"]
            require(bool(steps), "UNAVAILABLE")
            resources = {}
            if cursor == len(expected):
                for logical_key, resource in resource_by_key.items():
                    chunks = tuple(
                        storage.decode64(
                            fragments[sk("RESOURCE", resource=logical_key.split("#")[2], ordinal=i)].value()[
                                "bytes"
                            ]
                        )
                        for i in range(len(resource["chunk_sha256s"]))
                    )
                    wire = b"".join(chunks)
                    require(
                        len(wire) == resource["body_bytes"] and digest(wire) == resource["replacement_sha256"]
                    )
                    control.DocumentUpdate(logical_key, None, wire)
                    require(c.parse("ManagedResource", wire).scope.to_wire() == m["scope"], "SCOPE_REFUSED")
                    resources[logical_key] = wire
            published = (
                _record(members[sk("PUBLICATION")], "PublicationReceipt")
                if sk("PUBLICATION") in members
                else None
            )
            fenced = _record(members[sk("FENCED")], "StageFenceReceipt") if sk("FENCED") in members else None
            require(not (published and fenced), "REQUEST_CONFLICT")
            for marker, kind in ((published, "PUBLICATION"), (fenced, "FENCED")):
                if marker is None:
                    continue
                v = marker.value()
                require(
                    v["scope"] == m["scope"]
                    and v["publication_sha256"] == publication
                    and prior_revision <= v["expected_root_revision"]
                    and v["committed_root_revision"] <= root.value()["revision"]
                    and v["committed_root_revision"] not in revisions
                )
                if v["expected_root_revision"] == prior_revision:
                    require(v["expected_root_sha256"] == steps[-1].value()["replacement_root_sha256"])
                revisions.add(v["committed_root_revision"])
                seen.add(sk(kind))
            journal = present(journal_map.get(m["journal_operation_id"]), "UNAVAILABLE")
            intent_row = physical["INTENT#" + m["journal_operation_id"]]
            require(intent_row["revision"] >= m["intent_record_revision_before"])
            if intent_row["revision"] == m["intent_record_revision_before"]:
                require(journal.digest() == m["journal_before_sha256"])
            if published is not None:
                v = published.value()
                require(
                    cursor == len(expected)
                    and v["step_count"] == len(steps)
                    and v["last_step_sha256"] == steps[-1].digest()
                )
                storage.same(v, m, "journal_after_sha256 outcome_sha256 registry_after_sha256")
                require(
                    journal.state == "SETTLED"
                    and journal.digest() == m["journal_after_sha256"]
                    and intent_row["revision"] == m["intent_record_revision_after"]
                )
                outcome = present(physical.get(m["outcome_key"]), "UNAVAILABLE")
                exact(outcome, "PK SK document_bytes document_sha256")
                require(
                    outcome["document_bytes"] == journal.canonical_bytes()
                    and outcome["document_sha256"] == m["outcome_sha256"]
                )
            require(seen == set(members), "UNAVAILABLE")
            result[publication] = _StageView(manifest, tuple(steps), fragments, resources, published, fenced)
        return result

    def _inventory(
        self,
    ) -> tuple[
        Document,
        tuple[dict[str, Any], ...],
        tuple[c.JournalObservation, ...],
        tuple[c.ManagedResource, ...],
        dict[str, _StageView],
    ]:
        before, rows = self.read_complete()
        journals = []
        resources: dict[str, bytes] = {}
        revision_sum = 0
        legacy = [row for row in rows if not row["SK"].startswith(("STAGE#", "PUBLICATION#"))]
        expected_chunks = {
            key(self.binding.control_scope_id, row["SK"].split("#")[0], row["SK"].split("#")[1], ordinal=i)[
                "SK"
            ]
            for row in legacy
            if "body_chunk_count" in row
            for i in range(row["body_chunk_count"])
        }
        require(expected_chunks == {row["SK"] for row in legacy if "#BODY#" in row["SK"]}, "UNAVAILABLE")
        for row in legacy:
            sk = row["SK"]
            if sk == "ROOT" or "#BODY#" in sk:
                continue
            require(_append_key(self.binding, sk) == {"PK": row["PK"], "SK": sk})
            wire = present(self._read_base_document(sk), "UNAVAILABLE")
            if sk.startswith("INTENT#"):
                journals.append(self._journal(row, wire))
                revision_sum += row["revision"]
            elif sk.startswith("RESOURCE#"):
                require(sk.startswith("RESOURCE#task#"))
                control.DocumentUpdate(sk, None, wire)
                resource = c.parse("ManagedResource", wire)
                require(resource.scope.canonical_bytes() == self.binding.scope_wire, "SCOPE_REFUSED")
                resources[sk] = wire
        journals.sort(key=lambda j: j.external_operation_id)
        views = self._stage_views(before, rows, tuple(journals))
        published = sorted(
            (v for v in views.values() if v.published is not None),
            key=lambda v: present(v.published).value()["committed_root_revision"],
        )
        for view in published:
            for delta in view.manifest.value()["resources"]:
                old = resources.get(delta["logical_key"])
                require(
                    (digest(old) if old is not None else None) == delta["expected_sha256"],
                    "PRECONDITION_MISMATCH",
                )
                wire = view.resources[delta["logical_key"]]
                control.DocumentUpdate(delta["logical_key"], old, wire)
                resources[delta["logical_key"]] = wire
        managed = tuple(
            sorted(
                (c.parse("ManagedResource", wire) for wire in resources.values()),
                key=lambda r: r.provider_resource_id,
            )
        )
        value = before.value()
        pending = [
            j.external_operation_id
            for j in journals
            if j.state not in {"SETTLED", "REJECTED", "CANCELLED_UNSENT"}
        ]
        require(
            pending == value["pending_operation_ids"] and revision_sum == value["journal_revision"],
            "PENDING_UNKNOWN",
        )
        require(
            digest(canonical([r.to_wire() for r in managed])) == value["managed_registry_sha256"],
            "PENDING_UNKNOWN",
        )
        require(self.read_root().wire == before.wire, "PRECONDITION_MISMATCH")
        return before, rows, tuple(journals), managed, views

    def read_inventory(
        self,
    ) -> tuple[Document, tuple[c.JournalObservation, ...], tuple[c.ManagedResource, ...]]:
        root, _, journals, resources, _ = self._inventory()
        return root, journals, resources

    def read_publication(self, publication_sha256: str) -> storage.PublicationReadResult:
        scalar("Sha256", publication_sha256)
        root, _, _, _, views = self._inventory()
        view = views.get(publication_sha256)
        if view is None:
            return storage.PublicationReadResult("UNRESOLVED", publication_sha256, root.digest(), 0, 0, 0)
        expected = sum(len(r["chunk_sha256s"]) for r in view.manifest.value()["resources"])
        kind = (
            "PUBLISHED"
            if view.published
            else "FENCED"
            if view.fenced
            else "STAGED"
            if len(view.fragments) == expected
            else "UNRESOLVED"
        )
        return storage.PublicationReadResult(
            kind, publication_sha256, root.digest(), len(view.steps), expected, len(view.fragments)
        )

    def _submit(self, request: dict[str, Any]) -> str:
        """One submission, no token/retry; only a complete cancellation proves conflict."""
        self._identity()
        try:
            response = self._client.transact_write_items(**request)
            require(
                type(response) is dict and response.get("ResponseMetadata", {}).get("HTTPStatusCode") == 200,
                "UNAVAILABLE",
            )
        except Exception as exc:
            error_response = getattr(exc, "response", None)
            reasons = error_response.get("CancellationReasons") if isinstance(error_response, dict) else None
            conflict = (
                isinstance(error_response, dict)
                and error_response.get("Error", {}).get("Code") == "TransactionCanceledException"
                and isinstance(reasons, list)
                and len(reasons) == len(request["TransactItems"])
                and all(
                    isinstance(r, dict) and r.get("Code") in {"None", "ConditionalCheckFailed"}
                    for r in reasons
                )
                and any(r.get("Code") == "ConditionalCheckFailed" for r in reasons)
            )
            return "CONFLICT" if conflict else "UNKNOWN"
        return "COMMITTED"

    def _absent_marker(self, publication: str, kind: str) -> dict[str, Any]:
        return {
            "ConditionCheck": {
                "TableName": self.binding.table_arn,
                "Key": item(storage.staging_key(self.binding.control_scope_id, publication, kind)),
                "ConditionExpression": "attribute_not_exists(PK) AND attribute_not_exists(SK)",
            }
        }

    @staticmethod
    def _same_snapshot(
        pair: control.ObservationPair,
        journals: tuple[c.JournalObservation, ...],
        resources: tuple[c.ManagedResource, ...],
    ) -> None:
        require(
            tuple(pair.control.payload.journal) == journals
            and tuple(pair.control.payload.managed_resources) == resources,
            "PRECONDITION_MISMATCH",
        )

    def _prepare_settlement(
        self,
        root: Document,
        journal: c.JournalObservation,
        settlement: c.Settlement,
        pair: control.ObservationPair,
        now_ms: int,
        managed: tuple[c.ManagedResource, ...],
        qualification: control.JournalQualification | None,
        recovery_intent: storage.RecoveryStopTaskIntent | None,
        publication: str | None,
    ) -> tuple[Transition, Document, tuple[dict[str, Any], ...], dict[str, _StageView]]:
        actual, rows, journals, resources, views = self._inventory()
        require(actual.wire == root.wire, "PRECONDITION_MISMATCH")
        self._same_snapshot(pair, journals, resources)
        r = root.value()
        view = views.get(publication) if publication is not None else None
        if publication is not None:
            scalar("Sha256", publication)
            view = present(view, "UNAVAILABLE")
            require(view.published is None and view.fenced is None, "PRECONDITION_MISMATCH")
            prior = view.manifest.value()
            require(
                prior["semantic_root_sha256"] == storage.semantic_root_digest(root)
                and prior["anchor_root_revision"] < r["revision"],
                "PRECONDITION_MISMATCH",
            )
            require(now_ms < prior["captured_lease_deadline_ms"], "STALE_EPOCH")
            pair = replace(
                pair,
                deadline_ms=min(
                    pair.deadline_ms, prior["captured_lease_deadline_ms"], r["lease_deadline_ms"]
                ),
            )
        for other_id, other in views.items():
            other_owner = other.manifest.value()
            if (
                other_id != publication
                and other.published is None
                and other.fenced is None
                and all(other_owner[k] == r[k] for k in ("owner_subject", "run_id", "epoch"))
            ):
                raise Refusal("PENDING_UNKNOWN")
        _, change = control.settle(
            root,
            journal,
            settlement,
            pair,
            now_ms,
            managed,
            qualification=qualification,
            recovery_intent=recovery_intent,
        )
        require(1 <= len(change.documents) <= 10 and len(change.journals) == len(change.append) == 1)
        intent_row = next(
            (row for row in rows if row["SK"] == "INTENT#" + journal.external_operation_id), None
        )
        intent_row = present(intent_row, "UNAVAILABLE")
        require(
            self._journal(intent_row, present(self._read_base_document(intent_row["SK"]))).canonical_bytes()
            == journal.canonical_bytes(),
            "PRECONDITION_MISMATCH",
        )
        deltas = []
        for delta in sorted(change.documents, key=lambda d: d.key):
            require(delta.key.startswith("RESOURCE#task#"))
            chunks = ChunkSet.split(delta.replacement_wire)
            deltas.append(
                {
                    "logical_key": delta.key,
                    "expected_sha256": digest(delta.expected_wire)
                    if delta.expected_wire is not None
                    else None,
                    "replacement_sha256": digest(delta.replacement_wire),
                    "body_bytes": len(delta.replacement_wire),
                    "chunk_sha256s": list(chunks.hashes),
                }
            )
        new = change.replacement.value()
        manifest = Document.create(
            "PublicationManifest",
            {
                "protocol": storage.STAGING_PROTOCOLS["PublicationManifest"],
                **{k: r[k] for k in ("scope", "control_scope_id", "owner_subject", "run_id", "epoch")},
                "table_identity_sha256": self.binding.identity_digest(),
                "anchor_root_revision": view.manifest.value()["anchor_root_revision"]
                if view
                else r["revision"],
                "semantic_root_sha256": storage.semantic_root_digest(root),
                "captured_lease_deadline_ms": view.manifest.value()["captured_lease_deadline_ms"]
                if view
                else r["lease_deadline_ms"],
                "journal_operation_id": journal.external_operation_id,
                "journal_before_sha256": journal.digest(),
                "journal_after_sha256": change.journals[0].replacement.digest(),
                "root_journal_revision_before": r["journal_revision"],
                "root_journal_revision_after": new["journal_revision"],
                "intent_record_revision_before": intent_row["revision"],
                "intent_record_revision_after": intent_row["revision"] + 1,
                "outcome_key": change.append[0][0],
                "outcome_sha256": digest(change.append[0][1]),
                "pending_before_sha256": r["pending_index_sha256"],
                "pending_after_sha256": new["pending_index_sha256"],
                "registry_before_sha256": r["managed_registry_sha256"],
                "registry_after_sha256": new["managed_registry_sha256"],
                "resources": deltas,
            },
        )
        if view is not None:
            require(manifest.wire == view.manifest.wire, "REQUEST_CONFLICT")
        require(self.read_root().wire == root.wire, "PRECONDITION_MISMATCH")
        return change, manifest, rows, views

    def stage_settlement(
        self,
        root: Document,
        journal: c.JournalObservation,
        settlement: c.Settlement,
        pair: control.ObservationPair,
        now_ms: int,
        managed: tuple[c.ManagedResource, ...],
        *,
        qualification: control.JournalQualification | None = None,
        recovery_intent: storage.RecoveryStopTaskIntent | None = None,
        publication_sha256: str | None = None,
    ) -> storage.StageWriteResult:
        change, manifest, rows, views = self._prepare_settlement(
            root,
            journal,
            settlement,
            pair,
            now_ms,
            managed,
            qualification,
            recovery_intent,
            publication_sha256,
        )
        publication = manifest.digest()
        view = views.get(publication)
        fragments: list[tuple[dict[str, Any], Document]] = []
        for delta in sorted(change.documents, key=lambda d: d.key):
            chunks = ChunkSet.split(delta.replacement_wire)
            for ordinal, raw in enumerate(chunks.chunks):
                fields = {
                    "protocol": storage.STAGING_PROTOCOLS["StageFragment"],
                    "scope": root.value()["scope"],
                    "publication_sha256": publication,
                    "resource_identity_sha256": delta.key.split("#")[2],
                    "ordinal": ordinal,
                    "bytes": encode64(raw),
                    "chunk_sha256": chunks.hashes[ordinal],
                }
                fragment = Document.create("StageFragment", fields)
                row = {
                    **storage.staging_key(
                        self.binding.control_scope_id,
                        publication,
                        "RESOURCE",
                        resource=fields["resource_identity_sha256"],
                        ordinal=ordinal,
                    ),
                    **fields,
                    "bytes": raw,
                }
                fragments.append((row, fragment))
        count_before = len(view.fragments) if view else 0
        ordinal = len(view.steps) if view else 0
        require(count_before < len(fragments) and ordinal < 80, "PRECONDITION_MISMATCH")
        replacement = control._replacement(root)
        best: tuple[dict[str, Any], Document, int] | None = None
        for count in range(1, min(8 if view is None else 9, len(fragments) - count_before) + 1):
            selected = fragments[count_before : count_before + count]
            step_value = {
                "protocol": storage.STAGING_PROTOCOLS["StageStepReceipt"],
                "scope": root.value()["scope"],
                "publication_sha256": publication,
                "ordinal": ordinal,
                "previous_step_sha256": view.steps[-1].digest() if view else None,
                "expected_root_sha256": root.digest(),
                "replacement_root_sha256": replacement.digest(),
                "expected_root_revision": root.value()["revision"],
                "replacement_root_revision": replacement.value()["revision"],
                "fragment_keys": [row["SK"] for row, _ in selected],
                "fragment_sha256s": [doc.digest() for _, doc in selected],
            }
            step_value["request_sha256"] = _step_request(step_value).digest()
            step = Document.create("StageStepReceipt", step_value)
            actions = [_root_cas(self.binding, root, replacement)]
            if view is None:
                actions.append(
                    _absent_put(
                        self.binding,
                        _record_row(
                            storage.staging_key(self.binding.control_scope_id, publication, "MANIFEST"),
                            manifest,
                        ),
                    )
                )
            actions += [
                _absent_put(
                    self.binding,
                    _record_row(
                        storage.staging_key(
                            self.binding.control_scope_id, publication, "STEP", ordinal=ordinal
                        ),
                        step,
                    ),
                ),
                self._absent_marker(publication, "FENCED"),
            ]
            actions.extend(_absent_put(self.binding, row) for row, _ in selected)
            if len(rows) + count + (2 if view is None else 1) > MAX_INVENTORY:
                break
            try:
                request = _package(actions)
            except Refusal:
                break
            best = request, step, count
        request, step, count = present(best, "UNAVAILABLE")
        status = self._submit(request)
        kind = (
            ("STAGED" if count_before + count == len(fragments) else "PROGRESS")
            if status == "COMMITTED"
            else status
        )
        return storage.StageWriteResult(
            kind, publication, ordinal, step.digest(), root.digest(), replacement.digest()
        )

    def publish_settlement(
        self,
        root: Document,
        journal: c.JournalObservation,
        settlement: c.Settlement,
        pair: control.ObservationPair,
        now_ms: int,
        managed: tuple[c.ManagedResource, ...],
        *,
        publication_sha256: str,
        qualification: control.JournalQualification | None = None,
        recovery_intent: storage.RecoveryStopTaskIntent | None = None,
    ) -> storage.PublicationWriteResult:
        change, manifest, rows, views = self._prepare_settlement(
            root,
            journal,
            settlement,
            pair,
            now_ms,
            managed,
            qualification,
            recovery_intent,
            publication_sha256,
        )
        view = views[publication_sha256]
        require(len(view.resources) == len(manifest.value()["resources"]), "UNAVAILABLE")
        for delta in change.documents:
            require(view.resources.get(delta.key) == delta.replacement_wire, "REQUEST_CONFLICT")
        before = next(row for row in rows if row["SK"] == "INTENT#" + journal.external_operation_id)
        after = dict(before)
        after["revision"] = manifest.value()["intent_record_revision_after"]
        after.update(
            {
                k: change.journals[0].replacement.to_wire()[k]
                for k in (
                    "state",
                    "reservation",
                    "provider_outcome",
                    "settlement",
                    "cancellation_unsent_proof",
                )
            }
        )
        receipt = Document.create(
            "PublicationReceipt",
            {
                "protocol": storage.STAGING_PROTOCOLS["PublicationReceipt"],
                "scope": root.value()["scope"],
                "publication_sha256": publication_sha256,
                "last_step_sha256": view.steps[-1].digest(),
                "step_count": len(view.steps),
                "expected_root_sha256": root.digest(),
                "replacement_root_sha256": change.replacement.digest(),
                "expected_root_revision": root.value()["revision"],
                "committed_root_revision": change.replacement.value()["revision"],
                **{
                    k: manifest.value()[k]
                    for k in ("journal_after_sha256", "outcome_sha256", "registry_after_sha256")
                },
            },
        )
        outcome_key, outcome = change.append[0]
        require(len(outcome) <= MAX_ROOT_BYTES and len(rows) + 2 <= MAX_INVENTORY)
        request = _package(
            [
                _root_cas(self.binding, root, change.replacement),
                _exact_put(self.binding, before, after),
                _absent_put(
                    self.binding,
                    {
                        **_append_key(self.binding, outcome_key),
                        "document_bytes": outcome,
                        "document_sha256": digest(outcome),
                    },
                ),
                _absent_put(
                    self.binding,
                    _record_row(
                        storage.staging_key(self.binding.control_scope_id, publication_sha256, "PUBLICATION"),
                        receipt,
                    ),
                ),
                self._absent_marker(publication_sha256, "FENCED"),
            ]
        )
        return storage.PublicationWriteResult(
            self._submit(request), publication_sha256, root.digest(), change.replacement.digest()
        )

    def fence_staging(
        self,
        publication_sha256: str,
        root: Document,
        pair: control.ObservationPair,
        now_ms: int,
        *,
        qualification: control.JournalQualification | None = None,
    ) -> storage.FenceWriteResult:
        scalar("Sha256", publication_sha256)
        actual, rows, journals, resources, views = self._inventory()
        require(actual.wire == root.wire, "PRECONDITION_MISMATCH")
        pair.current(root, now_ms)
        self._same_snapshot(pair, journals, resources)
        view = present(views.get(publication_sha256), "UNAVAILABLE")
        require(view.published is None and view.fenced is None, "PRECONDITION_MISMATCH")
        journal = next(
            j for j in journals if j.external_operation_id == view.manifest.value()["journal_operation_id"]
        )
        evidence = Document.create(
            "FenceQualificationEvidence",
            {
                "protocol": storage.STAGING_PROTOCOLS["FenceQualificationEvidence"],
                "publication_sha256": publication_sha256,
                "root_sha256": root.digest(),
                "observation_pair_sha256": pair.digest(),
                "reason": "ABANDON_UNPUBLISHED_STORAGE_STAGE",
            },
        )
        present(qualification, "UNAVAILABLE").check(
            operation="fence_staging",
            root_wire=root.wire,
            journal_wire=journal.canonical_bytes(),
            evidence_wire=evidence.wire,
            deadline_ms=min(pair.deadline_ms, root.value()["lease_deadline_ms"]),
        )
        replacement = control._replacement(root)
        r = root.value()
        receipt = Document.create(
            "StageFenceReceipt",
            {
                "protocol": storage.STAGING_PROTOCOLS["StageFenceReceipt"],
                "scope": r["scope"],
                "publication_sha256": publication_sha256,
                "fencing_owner_subject": r["owner_subject"],
                "fencing_run_id": r["run_id"],
                "fencing_epoch": r["epoch"],
                "expected_root_sha256": root.digest(),
                "replacement_root_sha256": replacement.digest(),
                "expected_root_revision": r["revision"],
                "committed_root_revision": replacement.value()["revision"],
                "reason": "ABANDON_UNPUBLISHED_STORAGE_STAGE",
            },
        )
        require(len(rows) + 1 <= MAX_INVENTORY)
        request = _package(
            [
                _root_cas(self.binding, root, replacement),
                _absent_put(
                    self.binding,
                    _record_row(
                        storage.staging_key(self.binding.control_scope_id, publication_sha256, "FENCED"),
                        receipt,
                    ),
                ),
                self._absent_marker(publication_sha256, "PUBLICATION"),
            ]
        )
        return storage.FenceWriteResult(
            self._submit(request), publication_sha256, root.digest(), replacement.digest()
        )

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
                claim = Document("OwnerClaimReceipt", present(raw))
                claims[claim.digest()] = claim
        pointer = root["current_owner_claim_sha256"]
        chain: list[Document] = []
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

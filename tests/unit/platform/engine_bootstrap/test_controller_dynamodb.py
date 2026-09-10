"""Offline tests of actual request/readback code; no AWS durability or IAM claim."""

from __future__ import annotations

import copy
from typing import Any

import pytest
from tests.unit.platform.engine_bootstrap import test_controller_contracts as v
from tests.unit.platform.engine_bootstrap.test_controller_storage import U2, H, U, root

from maezo.platform.engine_bootstrap import controller as control
from maezo.platform.engine_bootstrap import controller_contracts as c
from maezo.platform.engine_bootstrap import controller_dynamodb as d
from maezo.platform.engine_bootstrap import controller_storage as s


class AuthoritySpy:
    def __init__(self) -> None:
        self.calls = []

    def qualify(self, **kw: Any) -> None:
        self.calls.append(kw)


def binding() -> d.ControlBinding:
    return d.ControlBinding(
        "arn:aws:dynamodb:sa-east-1:123456789012:table/owned",
        "table-id",
        "123456789012",
        "sa-east-1",
        U,
        s.canonical(v.sample("Scope")),
        H,
    )


class ProtocolClient:
    """Finite response fixture; not a service/transaction emulator."""

    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = {row["SK"]: d.item(copy.deepcopy(row)) for row in rows}
        self.calls = []
        self.error = None
        self.response = {"ResponseMetadata": {"HTTPStatusCode": 200}}
        self.root_reads = 0
        self.changed_root = None
        self.page_size = 2
        self.corrupt = None

    def describe_table(self, **kw: Any) -> dict[str, Any]:
        self.calls.append(("describe", kw))
        b = binding()
        return {
            "Table": {
                "TableArn": b.table_arn,
                "TableId": b.table_id,
                "TableStatus": "ACTIVE",
                "KeySchema": [
                    {"AttributeName": "PK", "KeyType": "HASH"},
                    {"AttributeName": "SK", "KeyType": "RANGE"},
                ],
            }
        }

    def get_item(self, **kw: Any) -> dict[str, Any]:
        self.calls.append(("get", kw))
        key = d.unitem(kw["Key"])["SK"]
        if key == "ROOT":
            self.root_reads += 1
            if self.changed_root is not None and self.root_reads > 1:
                return {"Item": self.changed_root}
        return {"Item": self.rows[key]} if key in self.rows else {}

    def query(self, **kw: Any) -> dict[str, Any]:
        self.calls.append(("query", kw))
        keys = sorted(self.rows)
        offset = keys.index(d.unitem(kw["ExclusiveStartKey"])["SK"]) + 1 if "ExclusiveStartKey" in kw else 0
        selected = keys[offset : offset + self.page_size]
        result = {"Items": [self.rows[k] for k in selected]}
        if offset + self.page_size < len(keys):
            result["LastEvaluatedKey"] = d.item({"PK": "D7#" + U, "SK": selected[-1]})
        return result

    def transact_get_items(self, **kw: Any) -> dict[str, Any]:
        self.calls.append(("transact_get", kw))
        items = [{"Item": self.rows[d.unitem(entry["Get"]["Key"])["SK"]]} for entry in kw["TransactItems"]]
        if self.corrupt == "missing":
            items.pop()
        if self.corrupt == "order":
            items.reverse()
        if self.corrupt == "hash":
            items[0] = copy.deepcopy(items[0])
            items[0]["Item"]["chunk_sha256"] = {"S": H}
        return {"Responses": items}

    def transact_write_items(self, **kw: Any) -> dict[str, Any]:
        self.calls.append(("write", kw))
        if self.error is not None:
            raise self.error
        return self.response


def rows_for(change: control.Transition) -> list[dict[str, Any]]:
    return [
        d.unitem(row["Put"]["Item"])
        for row in d.transaction(binding(), change)["TransactItems"]
        if "Put" in row
    ]


def claim_change() -> control.Transition:
    return control.claim_owner(
        root(pending_operation_ids=[U]),
        {"owner_subject": "owner-b", "run_id": U2, "epoch": 10},
        claim_id=U2,
        table_identity_sha256=binding().identity_digest(),
        now_ms=1001,
    )


def intent_change() -> control.Transition:
    intent = c.parse("RunTaskIntent", s.canonical(v.intent()))
    before = root(epoch=1, revision=1, run_id=intent.logical_run_id, pending_operation_ids=[])
    after = before.value()
    after.update(
        revision=2,
        journal_revision=3,
        pending_operation_ids=[intent.external_operation_id],
        pending_index_sha256=s.digest(s.canonical([intent.external_operation_id])),
    )
    return control.Transition(
        before,
        s.Document.create("Root", after),
        (("INTENT#" + intent.external_operation_id, intent.canonical_bytes()),),
    )


def test_claim_writes_root_and_immutable_receipt_in_one_exact_transaction() -> None:
    change = claim_change()
    request = d.transaction(binding(), change)
    assert len(request["TransactItems"]) == 2 and "ClientRequestToken" not in request
    put = request["TransactItems"][0]["Put"]
    names = put["ExpressionAttributeNames"]
    assert set(names.values()) == {
        "revision",
        "epoch",
        "run_id",
        "owner_subject",
        "pending_index_sha256",
        "scope",
        "lease_deadline_ms",
        "next_generation_id",
    }
    assert ":revision" in put["ConditionExpression"] and ":owner" in put["ConditionExpression"]
    assert d.unitem(put["Item"])["pending_operation_ids"] == [U]
    assert "attribute_not_exists" in request["TransactItems"][1]["Put"]["ConditionExpression"]


def test_small_intent_still_has_exact_manifest_and_immutable_binary_chunk() -> None:
    change = intent_change()
    rows = rows_for(change)
    assert len(rows) == 3
    manifest = rows[1]
    chunk = rows[2]
    assert set(manifest) - {"PK", "SK"} == set(
        [
            "schema_version",
            "scope",
            "external_operation_id",
            "run_id",
            "epoch",
            "generation_id",
            "intent_sha256",
            "body_bytes",
            "body_chunk_count",
            "body_chunk_sha256s",
            "state",
            "revision",
            "reservation",
            "provider_outcome",
            "settlement",
            "cancellation_unsent_proof",
        ]
    )
    assert (
        chunk["external_operation_id"] == manifest["external_operation_id"]
        and chunk["bytes"] == change.append[0][1]
    )
    spy = AuthoritySpy()
    client = ProtocolClient(rows)
    store = d.DynamoDBStore(client, binding(), spy)
    assert store.read_document(change.append[0][0]) == change.append[0][1]
    assert store.read_journal(manifest["external_operation_id"]).state == "INTENT"
    assert all(
        k.get("ConsistentRead") is True for op, k in client.calls if op in {"get", "query"} and "Key" in k
    )
    assert spy.calls


@pytest.mark.parametrize("mutation", ["missing", "hash"])
def test_chunk_corruption_refuses_before_intent_use(mutation: str) -> None:
    client = ProtocolClient(rows_for(intent_change()))
    client.corrupt = mutation
    with pytest.raises(s.Refusal):
        d.DynamoDBStore(client, binding(), AuthoritySpy()).read_document(intent_change().append[0][0])


def test_reservation_constructor_updates_root_and_exact_old_intent_state_atomically() -> None:
    old = c.parse("JournalObservation", s.canonical(v.journal("INTENT")))
    new = c.parse("JournalObservation", s.canonical(v.journal("RESERVED")))
    before = root(pending_operation_ids=[old.external_operation_id])
    values = before.value()
    values.update(revision=45, journal_revision=3)
    change = control.Transition(
        before, s.Document.create("Root", values), (), (control.JournalUpdate(old, new),)
    )
    request = d.transaction(binding(), change)
    update = request["TransactItems"][1]["Update"]
    assert update["ExpressionAttributeValues"][":old_state"] == {"S": "INTENT"}
    assert update["ExpressionAttributeValues"][":new_state"] == {"S": "RESERVED"}
    assert (
        "#intent = :intent" in update["ConditionExpression"]
        and "#revision + :one" in update["UpdateExpression"]
    )


@pytest.mark.parametrize(
    "response,kind",
    [
        ({"ResponseMetadata": {"HTTPStatusCode": 200}}, "COMMITTED"),
        ({}, "UNKNOWN"),
        ({"ResponseMetadata": {"HTTPStatusCode": 500}}, "UNKNOWN"),
        (None, "UNKNOWN"),
    ],
)
def test_partial_write_response_is_unknown_and_never_retried(response: Any, kind: str) -> None:
    client = ProtocolClient([])
    client.response = response
    result = d.DynamoDBStore(client, binding(), AuthoritySpy()).apply(claim_change())
    assert result.kind == kind and len([x for x in client.calls if x[0] == "write"]) == 1


@pytest.mark.parametrize(
    "codes,kind",
    [
        (["None", "ConditionalCheckFailed"], "CONFLICT"),
        (["ConditionalCheckFailed"], "UNKNOWN"),
        (["None", "TransactionConflict"], "UNKNOWN"),
    ],
)
def test_only_complete_cancelled_transaction_proves_cas_conflict(codes: list[str], kind: str) -> None:
    error = RuntimeError("private provider diagnostic")
    error.response = {
        "Error": {"Code": "TransactionCanceledException"},
        "CancellationReasons": [{"Code": x} for x in codes],
    }
    client = ProtocolClient([])
    client.error = error
    result = d.DynamoDBStore(client, binding(), AuthoritySpy()).apply(claim_change())
    assert result.kind == kind and "private" not in repr(result)


def test_complete_pagination_owner_lineage_and_root_reread_are_required() -> None:
    change = claim_change()
    client = ProtocolClient(rows_for(change))
    store = d.DynamoDBStore(client, binding(), AuthoritySpy())
    read = store.read_owner_lineage(
        expected_old_owner={"owner_subject": "owner-a", "run_id": U, "epoch": 9},
        challenge_sha256=H,
        authenticated_observer_subject="observer",
        observed_at_ms=1100,
        deadline_ms=2000,
    )
    assert read.value()["claim_receipt_sha256"] == s.digest(change.append[0][1])
    client.root_reads = 0
    after = change.replacement.value()
    after["revision"] += 1
    client.changed_root = d.item({"PK": "D7#" + U, "SK": "ROOT", **after})
    with pytest.raises(s.Refusal, match="PRECONDITION_MISMATCH"):
        store.read_complete()


def test_missing_qualified_client_or_wrong_actual_table_never_mutates() -> None:
    with pytest.raises(s.Refusal, match="UNAVAILABLE"):
        d.DynamoDBStore(ProtocolClient([]), binding())

    class WrongTable(ProtocolClient):
        def describe_table(self, **kw: Any) -> dict[str, Any]:
            result = super().describe_table(**kw)
            result["Table"]["TableId"] = "restored-other"
            return result

    client = WrongTable([])
    with pytest.raises(s.Refusal, match="SCOPE_REFUSED"):
        d.DynamoDBStore(client, binding(), AuthoritySpy()).apply(claim_change())
    assert not any(kind == "write" for kind, _ in client.calls)

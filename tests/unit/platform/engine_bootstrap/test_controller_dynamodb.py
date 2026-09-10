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


class InitialAuthority:
    def __init__(self) -> None:
        self.calls: list[Any] = []

    def qualify_initial_root(self, **kwargs: Any) -> None:
        self.calls.append(kwargs)


def initial_root() -> s.Document:
    return root(
        epoch=1,
        revision=1,
        next_generation_id=1,
        journal_revision=0,
        db_fence_epoch=0,
        restore_state="UNRECONCILED",
    )


def test_initial_root_uses_qualified_absent_only_single_send() -> None:
    client, authority = ProtocolClient([]), InitialAuthority()
    store = d.DynamoDBStore(client, binding(), AuthoritySpy())
    initial = initial_root()
    result = store.initialize(initial, authority=authority)
    assert result.kind == "COMMITTED" and result.root_sha256 == initial.digest()
    sends = [r for operation, r in client.calls if operation == "write"]
    assert len(sends) == 1 and len(sends[0]["TransactItems"]) == 1
    put = sends[0]["TransactItems"][0]["Put"]
    assert put["ConditionExpression"] == "attribute_not_exists(PK) AND attribute_not_exists(SK)"
    assert d.unitem(put["Item"]) == {**s.key(U, "ROOT"), **initial.value()}
    assert authority.calls == [
        {"root_wire": initial.wire, "table_identity_sha256": binding().identity_digest()}
    ]


@pytest.mark.parametrize("fault", ["missing_authority", "restore", "epoch", "pending", "unknown"])
def test_initial_root_refuses_unqualified_shape_and_never_retries(fault: str) -> None:
    client = ProtocolClient([])
    store = d.DynamoDBStore(client, binding(), AuthoritySpy())
    initial, authority = initial_root(), InitialAuthority()
    if fault == "unknown":
        client.error = OSError("transport lost")
        result = store.initialize(initial, authority=authority)
        assert result.kind == "UNKNOWN"
        assert len([1 for op, _ in client.calls if op == "write"]) == 1
        return
    changes = {
        "restore": {"restore_state": "RECONCILED"},
        "epoch": {"epoch": 2},
        "pending": {"pending_operation_ids": [U], "pending_index_sha256": s.digest(s.canonical([U]))},
    }
    if fault in changes:
        initial = s.Document.create("Root", initial.value() | changes[fault])
    with pytest.raises(s.Refusal):
        store.initialize(initial, authority=None if fault == "missing_authority" else authority)
    assert not [1 for op, _ in client.calls if op == "write"]


@pytest.mark.parametrize(
    "fault", [None, "omitted_intent", "wrong_revision", "extra_chunk", "omitted_resource"]
)
def test_complete_semantic_inventory_checks_manifest_pending_and_registry(fault: str | None) -> None:
    rows = rows_for(intent_change())
    rows[0]["journal_revision"] = 1  # Exactly one actual append, with no omitted prior history.
    if fault == "omitted_intent":
        rows = [rows[0]]
    elif fault == "wrong_revision":
        rows[1]["revision"] = 2
    elif fault == "extra_chunk":
        extra = copy.deepcopy(rows[2])
        extra["SK"] = extra["SK"][:-1] + "1"
        rows.append(extra)
    elif fault == "omitted_resource":
        rows[0]["managed_registry_sha256"] = H
    store = d.DynamoDBStore(ProtocolClient(rows), binding(), AuthoritySpy())
    if fault is None:
        observed, journals, resources = store.read_inventory()
        assert observed.value()["journal_revision"] == 1 and len(journals) == 1 and resources == ()
    else:
        with pytest.raises(s.Refusal):
            store.read_inventory()


class ConditionalProtocolClient(ProtocolClient):
    """Offline atomic conditional-request model; never DynamoDB durability/IAM evidence."""

    def __init__(self, rows: list[dict[str, Any]]) -> None:
        super().__init__(rows)
        self.write_mode = "commit"
        self.requests: list[dict[str, Any]] = []

    def transact_write_items(self, **kw: Any) -> dict[str, Any]:
        self.calls.append(("write", kw))
        self.requests.append(copy.deepcopy(kw))
        if self.write_mode == "unknown_before":
            raise TimeoutError("synthetic unavailable response before application")
        working = copy.deepcopy(self.rows)
        reasons = []
        for action in kw["TransactItems"]:
            kind, operation = next(iter(action.items()))
            physical = operation.get("Item", operation.get("Key"))
            key = d.unitem(physical)["SK"]
            prior = working.get(key)
            expression = operation["ConditionExpression"]
            if expression == "attribute_not_exists(PK) AND attribute_not_exists(SK)":
                matches = prior is None
            else:
                names, values = operation["ExpressionAttributeNames"], operation["ExpressionAttributeValues"]
                matches = prior is not None and all(
                    prior.get(names[left]) == values[right]
                    for left, right in (part.split(" = ") for part in expression.split(" AND "))
                )
            reasons.append({"Code": "None" if matches else "ConditionalCheckFailed"})
            if kind == "Put":
                working[key] = copy.deepcopy(operation["Item"])
            elif kind == "Update":
                # Closed original INTENT constructor: revision+1 and five journal fields.
                assert operation["UpdateExpression"].startswith("SET #revision = #revision + :one, ")
                updated = d.unitem(working[key])
                updated["revision"] += d.unattribute(operation["ExpressionAttributeValues"][":one"])
                for field in (
                    "state",
                    "reservation",
                    "provider_outcome",
                    "settlement",
                    "cancellation_unsent_proof",
                ):
                    updated[field] = d.unattribute(operation["ExpressionAttributeValues"][":new_" + field])
                working[key] = d.item(updated)
            else:
                assert kind == "ConditionCheck"
        if any(r["Code"] == "ConditionalCheckFailed" for r in reasons):
            error = RuntimeError("synthetic conditional conflict")
            error.response = {
                "Error": {"Code": "TransactionCanceledException"},
                "CancellationReasons": reasons,
            }
            raise error
        self.rows = working
        if self.write_mode == "unknown_after":
            raise TimeoutError("synthetic lost committed response")
        return {"ResponseMetadata": {"HTTPStatusCode": 200}}


def staged_fixture(count: int = 10):
    from tests.unit.platform.engine_bootstrap.test_controller_state import frozen_lifecycle_inputs

    values = frozen_lifecycle_inputs(count)
    first = values["changes"][0]
    client = ConditionalProtocolClient([{**s.key(U, "ROOT"), **first.expected.value()}])
    store = d.DynamoDBStore(client, binding(), AuthoritySpy())
    for change in values["changes"]:
        assert store.apply(change).kind == "COMMITTED"
    assert store.read_root().wire == values["root"].wire
    before, journals, resources = store.read_inventory()
    assert journals == (values["journal"],) and resources == ()
    client.requests.clear()
    return values, client, store


def settlement_call(values, store, *, publication=None, publish=False, now_ms=300):
    root_now, journals, resources = store.read_inventory()
    kwargs = dict(
        root=root_now,
        journal=values["journal"],
        settlement=values["settlement"],
        pair=values["observe"](root_now, journals, resources, tasks=True),
        now_ms=now_ms,
        managed=values["managed"],
        qualification=values["qualification"],
    )
    if publication is not None:
        kwargs["publication_sha256"] = publication
    return (store.publish_settlement if publish else store.stage_settlement)(**kwargs)


def test_staged_ten_task_publication_survives_fresh_adapters_and_exposes_all_or_none() -> None:
    values, client, store = staged_fixture()
    original = store.read_root().value()
    first = settlement_call(values, store)
    assert first.kind == "PROGRESS" and first.step_ordinal == 0
    assert len(client.requests[-1]["TransactItems"]) == 12
    publication = first.publication_sha256
    for key in client.rows:
        assert key == "ROOT" or not key.startswith("RESOURCE#")
    fresh = d.DynamoDBStore(client, binding(), AuthoritySpy())
    current, journals, resources = fresh.read_inventory()
    assert resources == () and journals[0].state == "ACKNOWLEDGED"
    assert {k: v for k, v in current.value().items() if k != "revision"} == {
        k: v for k, v in original.items() if k != "revision"
    }
    assert fresh.read_publication(publication).kind == "UNRESOLVED"
    for managed in values["managed"]:
        logical = "RESOURCE#task#" + s.digest(
            s.canonical(
                {
                    "scope": managed.scope.to_wire(),
                    "resource_kind": "task",
                    "provider_resource_id": managed.provider_resource_id,
                }
            )
        )
        assert fresh.read_document(logical) is None
    second = settlement_call(values, fresh, publication=publication)
    assert second.kind == "STAGED" and second.step_ordinal == 1
    assert len(client.requests[-1]["TransactItems"]) == 5
    assert fresh.read_inventory()[2] == ()
    assert fresh.read_publication(publication).kind == "STAGED"
    published = settlement_call(values, fresh, publication=publication, publish=True)
    assert published.kind == "COMMITTED" and len(client.requests[-1]["TransactItems"]) == 5
    restarted = d.DynamoDBStore(client, binding(), AuthoritySpy())
    final, journals, resources = restarted.read_inventory()
    assert resources == values["managed"] and len(resources) == 10
    assert journals[0].state == "SETTLED" and final.value()["pending_operation_ids"] == []
    assert final.value()["journal_revision"] == original["journal_revision"] + 1
    assert final.value()["revision"] == original["revision"] + 3
    assert restarted.read_publication(publication).kind == "PUBLISHED"
    for resource in resources:
        logical = "RESOURCE#task#" + s.digest(
            s.canonical(
                {
                    "scope": resource.scope.to_wire(),
                    "resource_kind": "task",
                    "provider_resource_id": resource.provider_resource_id,
                }
            )
        )
        assert restarted.read_document(logical) == resource.canonical_bytes()


def fence_call(values, store, publication):
    current, journals, resources = store.read_inventory()
    return store.fence_staging(
        publication,
        current,
        values["observe"](current, journals, resources, tasks=True),
        300,
        qualification=values["qualification"],
    )


@pytest.mark.parametrize("cut", ["initial", "later", "publish", "fence"])
@pytest.mark.parametrize("mode", ["unknown_before", "unknown_after"])
def test_each_ambiguous_submission_reconciles_without_retry(cut, mode):
    values, client, store = staged_fixture()
    publication = None
    if cut != "initial":
        publication = settlement_call(values, store).publication_sha256
    if cut == "publish":
        settlement_call(values, store, publication=publication)
    count = len(client.requests)
    client.write_mode = mode
    result = (
        fence_call(values, store, publication)
        if cut == "fence"
        else settlement_call(values, store, publication=publication, publish=cut == "publish")
    )
    assert result.kind == "UNKNOWN" and len(client.requests) == count + 1
    publication = result.publication_sha256
    fresh = d.DynamoDBStore(client, binding(), AuthoritySpy())
    expected = {"initial": "UNRESOLVED", "later": "STAGED", "publish": "PUBLISHED", "fence": "FENCED"}
    actual = fresh.read_publication(publication)
    assert actual.kind == (
        expected[cut] if mode == "unknown_after" else "STAGED" if cut == "publish" else "UNRESOLVED"
    )
    current, journals, resources = fresh.read_inventory()
    visible = cut == "publish" and mode == "unknown_after"
    assert len(resources) == (10 if visible else 0)
    assert journals[0].state == ("SETTLED" if visible else "ACKNOWLEDGED")
    assert bool(current.value()["pending_operation_ids"]) != visible
    assert len(client.requests) == count + 1


@pytest.mark.parametrize("winner", ["recovery", "initial"])
@pytest.mark.parametrize("recovery_unknown", [False, True])
def test_initial_unknown_uses_existing_recover_cas_and_complete_census(winner, recovery_unknown):
    values, client, store = staged_fixture()
    client.write_mode = "unknown_before"
    attempt = settlement_call(values, store)
    retained_request = copy.deepcopy(client.requests[-1])
    before, _, _ = store.read_inventory()
    recovery = control.recover(before, "PENDING_UNKNOWN")
    assert (
        attempt.kind == "UNKNOWN" and store.read_publication(attempt.publication_sha256).kind == "UNRESOLVED"
    )
    client.write_mode = "commit"
    if winner == "initial":
        # Synthetic delayed service application of the original single submitted request.
        assert client.transact_write_items(**retained_request)["ResponseMetadata"]["HTTPStatusCode"] == 200
        assert store.apply(recovery).kind == "CONFLICT"
        assert store.read_publication(attempt.publication_sha256).kind == "UNRESOLVED"
        assert any(k.startswith("STAGE#") for k in client.rows)
    else:
        client.write_mode = "unknown_after" if recovery_unknown else "commit"
        assert store.apply(recovery).kind == ("UNKNOWN" if recovery_unknown else "COMMITTED")
        client.write_mode = "commit"
        with pytest.raises(RuntimeError):
            client.transact_write_items(**retained_request)
        current, journals, resources = store.read_inventory()
        assert current.wire == recovery.replacement.wire
        assert journals[0].state == "ACKNOWLEDGED" and resources == ()
        assert not any(k.startswith("STAGE#") for k in client.rows)
        assert current.value()["pending_operation_ids"] == before.value()["pending_operation_ids"]


@pytest.mark.parametrize("winner", ["publish", "fence"])
def test_publication_and_fence_are_mutually_exclusive_and_terminal(winner):
    values, client, store = staged_fixture()
    first = settlement_call(values, store)
    publication = first.publication_sha256
    settlement_call(values, store, publication=publication)

    def action():
        return settlement_call(values, store, publication=publication, publish=True)

    def fence():
        return fence_call(values, store, publication)

    assert (action if winner == "publish" else fence)().kind == "COMMITTED"
    count = len(client.requests)
    for operation in (action, fence):
        with pytest.raises(s.Refusal):
            operation()
    assert len(client.requests) == count
    assert store.read_publication(publication).kind == ("PUBLISHED" if winner == "publish" else "FENCED")
    if winner == "fence":
        assert store.read_inventory()[2] == ()
        assert values["qualification"].calls[-1]["operation"] == "fence_staging"
        replacement = settlement_call(values, store)
        assert replacement.publication_sha256 != publication and replacement.kind == "PROGRESS"


def test_singleton_changed_id_and_missing_qualification_fail_before_write():
    values, client, store = staged_fixture()
    first = settlement_call(values, store)
    count = len(client.requests)
    for publication in (None, H):
        with pytest.raises(s.Refusal):
            settlement_call(values, store, publication=publication)
    current, journals, resources = store.read_inventory()
    with pytest.raises(s.Refusal):
        store.fence_staging(
            first.publication_sha256,
            current,
            values["observe"](current, journals, resources, tasks=True),
            300,
        )
    assert len(client.requests) == count


def test_heartbeat_keeps_stage_semantics_but_does_not_extend_captured_lease():
    values, client, store = staged_fixture()
    first = settlement_call(values, store)
    root_now = store.read_root()
    captured = root_now.value()["lease_deadline_ms"]
    owner = {k: root_now.value()[k] for k in ("owner_subject", "run_id", "epoch")}
    assert store.apply(control.renew_lease(root_now, owner, 301)).kind == "COMMITTED"
    assert settlement_call(values, store, publication=first.publication_sha256).kind == "STAGED"
    count = len(client.requests)
    with pytest.raises(s.Refusal):
        settlement_call(values, store, publication=first.publication_sha256, publish=True, now_ms=captured)
    assert len(client.requests) == count and store.read_inventory()[2] == ()


@pytest.mark.parametrize(
    "fault", ["bytes", "hash", "missing", "extra", "ordinal", "scope", "manifest", "step"]
)
def test_partial_stage_census_rejects_corruption_before_any_visibility(fault):
    values, client, store = staged_fixture()
    settlement_call(values, store)
    key = next(k for k in client.rows if "#RESOURCE#" in k)
    row = d.unitem(client.rows[key])
    if fault == "missing":
        del client.rows[key]
    elif fault == "extra":
        row["SK"] = key[:-1] + "9"
        client.rows[row["SK"]] = d.item(row)
    elif fault in {"bytes", "hash", "ordinal", "scope"}:
        if fault == "bytes":
            row["bytes"] = b"x" + row["bytes"][1:]
        if fault == "hash":
            row["chunk_sha256"] = H
        if fault == "ordinal":
            row["ordinal"] += 1
        if fault == "scope":
            row["scope"] = dict(row["scope"], tenant_id=U2)
        client.rows[key] = d.item(row)
    else:
        selected = (
            next(k for k in client.rows if k.endswith("#MANIFEST"))
            if fault == "manifest"
            else next(k for k in client.rows if "#STEP#" in k)
        )
        del client.rows[selected]
    count = len(client.requests)
    with pytest.raises(s.Refusal):
        d.DynamoDBStore(client, binding(), AuthoritySpy()).read_inventory()
    assert len(client.requests) == count


@pytest.mark.parametrize("cut", ["initial", "later", "publish", "fence"])
def test_every_stage_write_uses_exact_root_cas_and_never_partially_commits(cut):
    values, client, store = staged_fixture()
    publication = None
    if cut != "initial":
        publication = settlement_call(values, store).publication_sha256
    if cut == "publish":
        settlement_call(values, store, publication=publication)
    client.write_mode = "unknown_before"
    result = (
        fence_call(values, store, publication)
        if cut == "fence"
        else settlement_call(values, store, publication=publication, publish=cut == "publish")
    )
    request = client.requests[-1]
    original = copy.deepcopy(client.rows)
    before = d.unitem(client.rows["ROOT"])
    before["next_generation_id"] += 1
    client.rows["ROOT"] = d.item(before)
    expected = copy.deepcopy(client.rows)
    client.write_mode = "commit"
    with pytest.raises(RuntimeError):
        client.transact_write_items(**request)
    assert client.rows == expected
    assert result.kind == "UNKNOWN" and original != expected
    put = request["TransactItems"][0]["Put"]
    assert set(put["ExpressionAttributeNames"].values()) == set(before)


def test_final_cas_includes_full_physical_intent_preimage():
    values, client, store = staged_fixture()
    first = settlement_call(values, store)
    settlement_call(values, store, publication=first.publication_sha256)
    client.write_mode = "unknown_before"
    settlement_call(values, store, publication=first.publication_sha256, publish=True)
    request = client.requests[-1]
    intent_key = "INTENT#" + values["journal"].external_operation_id
    row = d.unitem(client.rows[intent_key])
    put = request["TransactItems"][1]["Put"]
    assert set(put["ExpressionAttributeNames"].values()) == set(row)
    row["revision"] += 1
    client.rows[intent_key] = d.item(row)
    before = copy.deepcopy(client.rows)
    client.write_mode = "commit"
    with pytest.raises(RuntimeError):
        client.transact_write_items(**request)
    assert client.rows == before


def test_inventory_bound_counts_unpublished_rows_before_next_write(monkeypatch):
    values, client, store = staged_fixture()
    first = settlement_call(values, store)
    monkeypatch.setattr(d, "MAX_INVENTORY", len(client.rows))
    count = len(client.requests)
    with pytest.raises(s.Refusal):
        settlement_call(values, store, publication=first.publication_sha256)
    assert len(client.requests) == count and store.read_inventory()[2] == ()


def test_staging_chooses_largest_fitting_prefix_under_physical_package_bound(monkeypatch):
    values, client, store = staged_fixture()
    original = d._package

    def smaller_package(actions):
        if len(actions) > 7:
            raise s.Refusal("UNAVAILABLE")
        return original(actions)

    monkeypatch.setattr(d, "_package", smaller_package)
    first = settlement_call(values, store)
    assert first.kind == "PROGRESS" and len(client.requests[-1]["TransactItems"]) == 7
    later = settlement_call(values, store, publication=first.publication_sha256)
    assert later.kind == "PROGRESS" and len(client.requests[-1]["TransactItems"]) == 7
    last = settlement_call(values, store, publication=first.publication_sha256)
    assert last.kind == "STAGED" and len(client.requests[-1]["TransactItems"]) == 6
    assert (
        settlement_call(values, store, publication=first.publication_sha256, publish=True).kind == "COMMITTED"
    )
    assert store.read_inventory()[2] == values["managed"]


def test_chunk_hash_is_checked_even_when_partial_step_receipt_is_self_consistent():
    values, client, store = staged_fixture()
    settlement_call(values, store)
    key = next(k for k in client.rows if "#RESOURCE#" in k)
    row = d.unitem(client.rows[key])
    row["bytes"] = b"x" + row["bytes"][1:]
    client.rows[key] = d.item(row)
    fragment_value = {k: value for k, value in row.items() if k not in {"PK", "SK"}}
    fragment_value["bytes"] = s.encode64(row["bytes"])
    step_key = next(k for k in client.rows if "#STEP#" in k)
    step_row = d.unitem(client.rows[step_key])
    step = s.parse_wire(step_row["document_bytes"])
    step["fragment_sha256s"][step["fragment_keys"].index(key)] = s.digest(s.canonical(fragment_value))
    step["request_sha256"] = d._step_request(step).digest()
    step_row["document_bytes"] = s.Document.create("StageStepReceipt", step).wire
    step_row["document_sha256"] = s.digest(step_row["document_bytes"])
    client.rows[step_key] = d.item(step_row)
    with pytest.raises(s.Refusal):
        store.read_inventory()


def test_successor_cannot_publish_old_plan_and_can_explicitly_fence_actual_manifest():
    values, client, store = staged_fixture()
    first = settlement_call(values, store)
    current = store.read_root()
    claim = control.claim_owner(
        current,
        dict(owner_subject="owner-b", run_id=U2, epoch=current.value()["epoch"] + 1),
        claim_id=U2,
        table_identity_sha256=binding().identity_digest(),
        now_ms=current.value()["lease_deadline_ms"] + 1,
    )
    assert store.apply(claim).kind == "COMMITTED"
    count = len(client.requests)
    with pytest.raises(s.Refusal):
        settlement_call(values, store, publication=first.publication_sha256)
    assert len(client.requests) == count
    assert fence_call(values, store, first.publication_sha256).kind == "COMMITTED"
    assert store.read_publication(first.publication_sha256).kind == "FENCED"
    assert store.read_inventory()[2] == ()


def test_recovery_invalidates_existing_live_plan_without_clearing_pending():
    values, client, store = staged_fixture()
    first = settlement_call(values, store)
    before = store.read_root()
    assert store.apply(control.recover(before, "PENDING_UNKNOWN")).kind == "COMMITTED"
    count = len(client.requests)
    with pytest.raises(s.Refusal):
        settlement_call(values, store, publication=first.publication_sha256)
    assert len(client.requests) == count
    assert (
        store.read_inventory()[0].value()["pending_operation_ids"] == before.value()["pending_operation_ids"]
    )
    assert fence_call(values, store, first.publication_sha256).kind == "COMMITTED"


def test_already_recovery_required_root_hash_alone_cannot_attribute_metadata_write():
    current = control.recover(root(), "PENDING_UNKNOWN").replacement
    recovery = control.recover(current, "PENDING_UNKNOWN")
    staging_replacement = control._replacement(current)
    assert recovery.replacement.wire == staging_replacement.wire
    assert d._root_cas(binding(), current, recovery.replacement) == d._root_cas(
        binding(), current, staging_replacement
    )
    # This is the exact ROOT ambiguity; full-manifest initial/recovery race controls
    # above separately require a complete census and preserve provider uncertainty.

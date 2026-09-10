"""Actual storage codecs/constructors; fixture vectors provide no authority."""

from __future__ import annotations

import copy
from dataclasses import FrozenInstanceError
from typing import Any

import pytest
from tests.unit.platform.engine_bootstrap import test_controller_contracts as vectors

from maezo.platform.engine_bootstrap import controller_contracts as c
from maezo.platform.engine_bootstrap import controller_storage as s

H = "a" * 64
U = "00000000-0000-4000-8000-000000000001"
U2 = "00000000-0000-4000-8000-000000000002"


def root(**changes: Any) -> s.Document:
    values = dict(
        schema_version=1,
        control_scope_id=U,
        scope=vectors.sample("Scope"),
        owner_subject="owner-a",
        run_id=U,
        epoch=9,
        revision=44,
        state="REVIEWED",
        candidate_sha256=H,
        trust_profile_sha256=H,
        lease_deadline_ms=1000,
        db_fence_epoch=9,
        current_generation_id=None,
        next_generation_id=3,
        journal_revision=2,
        pending_operation_ids=[],
        pending_index_sha256=s.digest(b"[]"),
        managed_registry_sha256=s.digest(b"[]"),
        retired_index_sha256=s.digest(b"[]"),
        activation_decision_sha256=None,
        restore_receipt_sha256=None,
        readiness_result_sha256=None,
        restore_state="RECONCILED",
        current_owner_claim_sha256=None,
    )
    values.update(changes)
    if "pending_operation_ids" in changes and "pending_index_sha256" not in changes:
        values["pending_index_sha256"] = s.digest(s.canonical(values["pending_operation_ids"]))
    return s.Document.create("Root", values)


def operation_proof(operation: str = "prepare_generation", *, epoch: int = 1) -> dict[str, Any]:
    values = {k: H for k in s.OPERATION_PROOF_FIELDS.split()}
    values.update(
        protocol="maezo.d7-operation-proof.v1",
        scope=vectors.sample("Scope"),
        run_id=U,
        epoch=epoch,
        operation=operation,
        controller_state="PROVISIONER_DISPOSED",
        controller_revision=44,
        observed_at_ms=100,
        observation_deadline_ms=4000,
        controller_lease_deadline_ms=10000,
        journal_revision=2,
        pending_index_sha256=s.digest(b"[]"),
    )
    values["observation_pair_sha256"] = s.digest(
        s.canonical({k: values[k] for k in ("control_observation_sha256", "database_observation_sha256")})
    )
    return values


def prepare_call(
    purpose: str = "candidate",
) -> tuple[dict[str, Any], c.IssuerRequest, c.CandidateDecision | c.ActivationDecision]:
    dec = c.parse(
        "CandidateDecision" if purpose == "candidate" else "ActivationDecision",
        s.canonical(vectors.decision(purpose)),
    )
    gen = c.parse("RuntimeAdmissionGeneration", s.canonical(vectors.generation(purpose, "PREPARED")))
    request = vectors.sample("IssuerRequest")
    request.update(scope=dec.scope.to_wire(), epoch=dec.epoch, run_id=dec.run_id, issuer_operation_id=U)
    request["body"] = {"operation": "prepare_generation", "generation": gen.to_wire()}
    typed = c.parse("IssuerRequest", s.canonical(request))
    call = {
        "protocol": "maezo.d7-store-call.v1",
        "scope": dec.scope.to_wire(),
        "run_id": dec.run_id,
        "epoch": dec.epoch,
        "issuer_operation_id": U,
        "expected_fence_revision": 44,
        "request": s.prepare_request(typed, dec),
        "proof": operation_proof(epoch=dec.epoch),
    }
    return call, typed, dec


def preparation(purpose: str = "candidate") -> dict[str, Any]:
    core = vectors.core(purpose)
    principal = {
        "kind": "generation",
        **{k: v for k, v in core.items() if k not in {"scope", "epoch", "login_oid"}},
    }
    return dict(
        protocol="maezo.d7-owner-preparation.v1",
        installation_id=U,
        control_scope_id=U,
        scope=core["scope"],
        database_binding=vectors.sample("DatabaseBinding"),
        owner_operation_id=U,
        preparation_id=U,
        epoch=core["epoch"],
        run_id=U,
        principal=principal,
    )


def test_root_copies_are_immutable_and_pending_never_disappears() -> None:
    doc = root(pending_operation_ids=[U])
    detached = doc.value()
    detached["pending_operation_ids"].clear()
    assert doc.value()["pending_operation_ids"] == [U]
    with pytest.raises(FrozenInstanceError):
        doc.wire = b"{}"
    with pytest.raises(s.Refusal):
        root(pending_operation_ids=[U], pending_index_sha256=H)


@pytest.mark.parametrize(
    "field,value",
    [
        ("epoch", True),
        ("revision", 0),
        ("schema_version", True),
        ("current_generation_id", 3),
        ("owner_subject", "owner\x85"),
        ("run_id", "AAAAAAAA-0000-4000-8000-000000000001"),
        ("extra", 1),
    ],
)
def test_root_closed_fields_and_aliases(field: str, value: Any) -> None:
    with pytest.raises(s.Refusal):
        root(**{field: value})


@pytest.mark.parametrize(
    "raw",
    [
        b'{"a":1,"a":2}',
        b'{ "a":1}',
        b'{"a":-0}',
        b'{"a":1.0}',
        b'{"a":9007199254740992}',
        b'"\\ud800"',
        b'"\xff"',
        b"[" * 34 + b"0" + b"]" * 34,
    ],
)
def test_storage_wire_never_widens_stage1(raw: bytes) -> None:
    with pytest.raises(s.Refusal):
        s.parse_wire(raw)


@pytest.mark.parametrize("purpose", ["candidate", "runtime"])
def test_prepare_retains_complete_decision_with_unchanged_logical_digest(purpose: str) -> None:
    call, request, dec = prepare_call(purpose)
    assert s.validate_store_call("prepare_generation", call) == call
    assert s.logical_request_digest("prepare_generation", call) == request.digest()
    assert s.decode64(call["request"]["retained_decision"]["decision_bytes"]) == dec.canonical_bytes()
    detached = copy.deepcopy(call)
    detached["request"]["retained_decision"]["decision_bytes"] = s.encode64(b"{}")
    with pytest.raises(s.Refusal):
        s.validate_store_call("prepare_generation", detached)


@pytest.mark.parametrize(
    "mutation", ["missing", "purpose", "hash", "core", "scope", "run", "bytes", "unknown"]
)
def test_prepare_carrier_rejects_incomplete_or_substituted_decision(mutation: str) -> None:
    call, _, _ = prepare_call()
    carrier = call["request"]["retained_decision"]
    if mutation == "missing":
        del call["request"]["retained_decision"]
    elif mutation == "purpose":
        carrier["purpose"] = "runtime"
    elif mutation == "hash":
        carrier["decision_sha256"] = H
    elif mutation == "bytes":
        carrier["decision_bytes"] += "="
    elif mutation == "unknown":
        carrier["authority"] = True
    else:
        value = s.parse_wire(s.decode64(carrier["decision_bytes"]))
        if mutation == "core":
            value["generation_core"]["login_oid"] += 1
        elif mutation == "scope":
            value["scope"]["tenant"] = "other"
        else:
            value["run_id"] = U2
        wire = s.canonical(value)
        carrier.update(decision_bytes=s.encode64(wire), decision_sha256=s.digest(wire))
    with pytest.raises(s.Refusal):
        s.validate_store_call("prepare_generation", call)


@pytest.mark.parametrize("purpose", ["candidate", "runtime"])
def test_owner_generation_preparation_is_shape_only_and_keeps_actual_oid_out(purpose: str) -> None:
    value = preparation(purpose)
    doc = s.Document.create("OwnerPreparationInput", value)
    assert "login_oid" not in doc.value()["principal"]
    value["principal"]["login_oid"] = 1
    with pytest.raises(s.Refusal):
        s.Document.create("OwnerPreparationInput", value)


@pytest.mark.parametrize("length", [1, 131070, 131072, 260000, 1048574])
def test_chunks_are_complete_ordered_and_digest_bound(length: int) -> None:
    wire = s.canonical("a" * length)
    parts = s.ChunkSet.split(wire)
    rows = [{"ordinal": i, "bytes": b, "chunk_sha256": parts.hashes[i]} for i, b in enumerate(parts.chunks)]
    assert s.ChunkSet.assemble(rows, list(parts.hashes), s.digest(wire)).wire == wire
    rows[0]["chunk_sha256"] = H
    with pytest.raises(s.Refusal):
        s.ChunkSet.assemble(rows, list(parts.hashes), s.digest(wire))


@pytest.mark.parametrize("mutation", ["missing", "extra", "order", "body", "size", "pad"])
def test_partial_or_corrupt_chunks_never_publish(mutation: str) -> None:
    if mutation == "size":
        with pytest.raises(s.Refusal):
            s.ChunkSet.split(b'"' + b"a" * 1048576 + b'"')
        return
    if mutation == "pad":
        with pytest.raises(s.Refusal):
            s.decode64("Zh")
        return
    parts = s.ChunkSet.split(s.canonical("x" * 150000))
    rows = [dict(ordinal=i, bytes=b, chunk_sha256=parts.hashes[i]) for i, b in enumerate(parts.chunks)]
    if mutation == "missing":
        rows.pop()
    elif mutation == "extra":
        rows.append(rows[-1])
    elif mutation == "order":
        rows.reverse()
    else:
        rows[0]["bytes"] = b"bad"
    with pytest.raises(s.Refusal):
        s.ChunkSet.assemble(rows, list(parts.hashes), s.digest(parts.wire))


def test_fixed_keys_forbid_selector_injection_and_reuse_variants() -> None:
    assert s.key(U, "ROOT") == {"PK": "D7#" + U, "SK": "ROOT"}
    assert s.key(U, "INTENT", U2, ordinal=1)["SK"].endswith("#BODY#00000001")
    for kwargs in (
        {"kind": "DELETE", "identity": U},
        {"kind": "INTENT", "identity": U + "#BODY#0"},
        {"kind": "GENERATION", "identity": True},
        {"kind": "OUTCOME", "identity": U, "revision": None},
        {"kind": "INTENT", "identity": U, "ordinal": 8},
    ):
        with pytest.raises(s.Refusal):
            s.key(U, **kwargs)

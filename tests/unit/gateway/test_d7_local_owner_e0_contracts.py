"""E0 structural refusal vectors only, not owner installation or live authority."""

from __future__ import annotations

import sqlite3
from copy import deepcopy

import pytest

from maezo.platform.engine_bootstrap import owner_local_e0_contracts as e
from maezo.platform.engine_bootstrap.controller_storage import Refusal, canonical, digest, encode64
from maezo.platform.engine_bootstrap.owner_local_contracts import LocalOwnerRecord, reservation_keys
from tests.unit.gateway.test_d7_local_owner_contracts import intent, uid


def installation_body():
    i = intent().value()
    value = {
        "protocol": "maezo.d7-local-owner.ingress-installation.v1",
        "authority_id": uid(100),
        "parent_operation_id": uid(101),
        "parent_subject": "external-parent",
        "parent_key_id": "parent-key",
        "intent_sha256": intent().digest(),
        "owner_key_id": i["trust"]["key_id"],
        "owner_key_sha256": i["trust"]["public_key_sha256"],
        "source_artifact_sha256": i["source"]["artifact_sha256"],
        "not_before_ms": 1000,
        "expires_at_ms": 901000,
        "supervisor": {
            "instance_id": uid(102),
            "boot_id": uid(103),
            "pid": 500,
            "start_ticks": 900,
            "uid": 0,
            "executable_sha256": "c" * 64,
        },
        "store": {
            "store_id": uid(104),
            "absolute_path": "/private/owner/ingress.sqlite",
            "device": 10,
            "inode": 20,
            "owner_uid": 1001,
            "schema_sha256": digest(e.INGRESS_SCHEMA),
            "initial_anchor_sha256": "e" * 64,
        },
        "uids": {"owner": 1001, "reader": 1002, "requester": 1003},
    }

    anchor = {
        "protocol": "maezo.d7-local-owner.ingress-anchor.v1",
        **{
            k: value[k]
            for k in (
                "authority_id",
                "parent_operation_id",
                "intent_sha256",
                "source_artifact_sha256",
                "supervisor",
            )
        },
        "store": {k: v for k, v in value["store"].items() if k != "initial_anchor_sha256"},
    }
    value["store"]["initial_anchor_sha256"] = digest(canonical(anchor))
    return value


def installation():
    return e.E0Record("installation", canonical(installation_body()))


def ingress(state="CLAIMED", previous=None, **changes):
    value = {
        "protocol": "maezo.d7-local-owner.ingress.v1",
        "installation_sha256": installation().digest(),
        "intent_sha256": intent().digest(),
        "store_id": uid(104),
        "supervisor_instance_id": uid(102),
        "revision": 1 if previous is None else previous.value()["revision"] + 1,
        "state": state,
        "previous_sha256": None if previous is None else previous.digest(),
        "evidence_sha256": "f" * 64 if state in {"ISSUED", "DELIVERED"} else None,
        "recorded_at_ms": 2000,
    }
    value.update(changes)
    return e.E0Record("ingress", canonical(value))


def candidate_body():
    i = intent().value()
    return {
        "protocol": "maezo.d7-initial-source-candidate.v1",
        "purpose": "INITIAL_SOURCE_REVIEW",
        "mode": "PUBLIC_SYNTHETIC",
        "intent_sha256": intent().digest(),
        **{k: i[k] for k in ("installation_id", "control_scope_id", "run_id", "scope", "source")},
    }


def profile():
    i = intent().value()

    def key(name, octet):
        return {
            "key_id": name,
            "issuer": "test-owner",
            "purpose": name + "-observation",
            "subject": name,
            "audience": "initial",
            "algorithm": "Ed25519",
            "public_key": encode64(bytes([octet]) * 32),
            "not_before_ms": 0,
            "expires_at_ms": 901000,
            "lifecycle": "ACTIVE",
            "predecessor_key_id": None,
            "revoked_at_ms": None,
        }

    return {
        "protocol": "maezo.provisioning-trust-profile.v1",
        "profile_id": "test-inert-profile",
        "scope": i["scope"],
        "release_source": i["source"]["git_sha"],
        "release_tree": i["source"]["tree_sha"],
        "verifier_image_sha256": "c" * 64,
        "verifier_jar_sha256": "d" * 64,
        "limits": {
            "max_observation_age_ms": 15000,
            "max_clock_disagreement_ms": 2000,
            "lease_term_ms": 120000,
            "heartbeat_ms": 10000,
            "permit_admission_validity_ms": 60000,
            "native_tx_deadline_ms": 30000,
            "observer_request_deadline_ms": 5000,
        },
        "control_keys": [key("control", 1)],
        "database_keys": [key("database", 2)],
        "inventory_bounds_sha256": "e" * 64,
    }


def expected_root():
    candidate = candidate_body()
    empty = digest(canonical([]))
    return {
        "schema_version": 1,
        "control_scope_id": uid(4),
        "scope": candidate["scope"],
        "owner_subject": "actual-owner",
        "run_id": uid(3),
        "epoch": 1,
        "revision": 1,
        "state": "REVIEWED",
        "candidate_sha256": digest(canonical(candidate)),
        "trust_profile_sha256": digest(canonical(profile())),
        "lease_deadline_ms": 122000,
        "db_fence_epoch": 0,
        "current_generation_id": None,
        "next_generation_id": 1,
        "journal_revision": 0,
        "pending_operation_ids": [],
        "pending_index_sha256": empty,
        "managed_registry_sha256": empty,
        "retired_index_sha256": empty,
        "activation_decision_sha256": None,
        "restore_receipt_sha256": None,
        "readiness_result_sha256": None,
        "restore_state": "UNRECONCILED",
        "current_owner_claim_sha256": None,
    }


def review_body():
    i = intent().value()
    return {
        "protocol": "maezo.d7-local-owner.initial-review.v1",
        "installation_authority_id": uid(100),
        "intent_sha256": intent().digest(),
        "candidate": candidate_body(),
        "trust_profile": profile(),
        "decision": {
            "decision_id": uid(110),
            "action": "APPROVE_INITIAL_SOURCE",
            "owner_subject": "actual-owner",
            "reviewer_subject": "independent-reviewer",
            "source_review_ref": "actual-review-record",
            "source_review_sha256": "f" * 64,
            "approved_source": i["source"]["git_sha"],
            "approved_tree": i["source"]["tree_sha"],
            "approved_artifact_sha256": i["source"]["artifact_sha256"],
            "issued_at_ms": 2000,
            "expires_at_ms": 901000,
            "lease_deadline_ms": 122000,
        },
        "initial_root_sha256": digest(canonical(expected_root())),
    }


def test_real_synthetic_installation_record_shape_is_allowed_without_claiming_parent_authority():
    record = installation()
    e.bind_installation(intent(), record)
    assert record.value() == installation_body()
    assert not hasattr(record, "authorize")
    copy = record.value()
    copy["store"]["inode"] = 99
    assert record.value()["store"]["inode"] == 20


@pytest.mark.parametrize(
    "path,replacement",
    [
        (("store", "absolute_path"), "/private/../other"),
        (("store", "absolute_path"), "relative.sqlite"),
        (("store", "absolute_path"), "/private//other"),
        (("store", "absolute_path"), "/private/other/"),
        (("store", "inode"), True),
        (("store", "owner_uid"), 1003),
        (("supervisor", "pid"), 0),
        (("uids", "reader"), 1001),
        (("uids", "owner"), 0),
        (("parent_key_id",), "owner-independent"),
        (("expires_at_ms",), 901001),
    ],
)
def test_installation_refuses_unqualified_shapes_and_aliases(path, replacement):
    body = installation_body()
    target = body
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = replacement
    with pytest.raises(Refusal):
        e.E0Record("installation", canonical(body))


@pytest.mark.parametrize("field", ["intent_sha256", "owner_key_sha256", "source_artifact_sha256"])
def test_well_shaped_installation_cannot_bind_another_intent_or_key(field):
    body = installation_body()
    body[field] = "9" * 64
    with pytest.raises(Refusal):
        e.bind_installation(intent(), e.E0Record("installation", canonical(body)))


def test_ingress_reserves_full_l1_collision_domain_plus_installed_authority():
    keys = set(e.ingress_keys(intent(), installation()))
    assert len(keys) == 13
    assert set(reservation_keys(intent())) < keys
    assert ("D7INGRESS#AUTHORITY#" + uid(100), "RESERVATION") in keys
    assert ("D7INGRESS#STORE#" + uid(104), "RESERVATION") in keys
    assert ("D7INGRESS#SUPERVISOR#" + uid(102), "RESERVATION") in keys
    for field in ("installation_id", "run_id", "acquisition_id", "enrollment_id", "bootstrap_operation_id"):
        changed = intent().value()
        changed[field] = uid(150)
        with pytest.raises(Refusal):
            e.ingress_keys(LocalOwnerRecord("intent", canonical(changed)), installation())


def history():
    rows = [ingress()]
    for state in e.INGRESS_STATES[1:]:
        rows.append(ingress(state, rows[-1]))
        e.ingress_successor(rows[-2], rows[-1])
    return tuple(rows)


def test_one_way_claim_issue_delivery_values_and_terminal_states():
    rows = history()
    assert [r.value()["revision"] for r in rows] == [1, 2, 3, 4, 5]
    for record in rows:
        e.bind_ingress(installation(), record)
    for previous in rows[:-1]:
        unknown = ingress("UNKNOWN", previous, recorded_at_ms=999999)
        e.ingress_successor(previous, unknown)
        e.bind_ingress(installation(), unknown)
        with pytest.raises(Refusal):
            e.ingress_successor(unknown, ingress("ISSUE_STARTED", unknown))
    with pytest.raises(Refusal):
        e.ingress_successor(rows[-1], ingress("UNKNOWN", rows[-1]))


@pytest.mark.parametrize("state", ["ISSUED", "DELIVERY_STARTED", "DELIVERED"])
def test_claim_cannot_skip_issuance_boundaries(state):
    first = ingress()
    with pytest.raises(Refusal):
        e.ingress_successor(first, ingress(state, first))


def compare(rows, **changes):
    values = {
        "observed_supervisor": installation().value()["supervisor"],
        "observed_store": installation().value()["store"],
        "history": rows,
        "witness_revision": rows[-1].value()["revision"],
        "witness_sha256": rows[-1].digest(),
        "restarted": False,
    }
    values.update(changes)
    e.compare_continuity_values(installation(), **values)


def test_continuity_comparison_is_not_a_live_observer_and_refuses_rollback_copy_restart():
    rows = history()
    compare(rows)
    with pytest.raises(Refusal):
        compare(rows, restarted=True)
    with pytest.raises(Refusal):
        compare(rows, history=rows[:-1])  # old on-disk snapshot against current private witness
    with pytest.raises(Refusal):
        compare(rows, witness_sha256="f" * 64)
    for field in ("inode", "device", "store_id", "absolute_path", "initial_anchor_sha256"):
        store = installation().value()["store"]
        store[field] = {
            "inode": 21,
            "device": 11,
            "store_id": uid(200),
            "absolute_path": "/private/copied.sqlite",
            "initial_anchor_sha256": "f" * 64,
        }[field]
        with pytest.raises(Refusal):
            compare(rows, observed_store=store)
    for field, replacement in (
        ("boot_id", uid(201)),
        ("pid", 501),
        ("start_ticks", 901),
        ("uid", 1003),
        ("instance_id", uid(202)),
        ("executable_sha256", "f" * 64),
    ):
        supervisor = installation().value()["supervisor"]
        supervisor[field] = replacement
        with pytest.raises(Refusal):
            compare(rows, observed_supervisor=supervisor)


def test_history_gap_reorder_and_foreign_store_fail_even_with_matching_last_hash():
    rows = history()
    for bad in ((rows[0], *rows[2:]), (rows[1], rows[0], *rows[2:])):
        with pytest.raises(Refusal):
            compare(bad)
    with pytest.raises(Refusal):
        e.bind_ingress(installation(), ingress(store_id=uid(210)))


def test_initial_review_binds_precise_source_candidate_and_existing_trustprofile():
    record = e.E0Record("review", canonical(review_body()))
    e.bind_review(intent(), installation(), record)
    root = e.initial_root(record)
    assert root.value() == expected_root()
    assert root.value()["candidate_sha256"] == digest(canonical(candidate_body()))
    assert root.value()["candidate_sha256"] != intent().value()["source"]["artifact_sha256"]
    assert root.value()["restore_state"] == "UNRECONCILED"
    assert root.value()["current_generation_id"] is None
    assert root.value()["lease_deadline_ms"] == 122000


@pytest.mark.parametrize(
    "path,replacement",
    [
        (("candidate", "protocol"), "maezo.provisioning-candidate-decision.v1"),
        (("candidate", "purpose"), "runtime"),
        (("candidate", "mode"), "PRODUCTION"),
        (("candidate", "source", "git_sha"), "b" * 40),
        (("trust_profile", "limits", "lease_term_ms"), 120001),
        (("trust_profile", "release_tree"), "b" * 40),
        (("trust_profile", "control_keys"), []),
        (("decision", "action"), "ADMIT_NATIVE"),
        (("decision", "owner_subject"), "another-owner"),
        (("decision", "reviewer_subject"), "actual-owner"),
        (("decision", "approved_artifact_sha256"), "b" * 64),
        (("decision", "lease_deadline_ms"), 122001),
        (("decision", "lease_deadline_ms"), 2000),
        (("decision", "expires_at_ms"), 120000),
        (("initial_root_sha256",), "b" * 64),
    ],
)
def test_review_refuses_candidate_domain_source_owner_lease_and_root_drift(path, replacement):
    body = review_body()
    target = body
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = replacement
    with pytest.raises(Refusal):
        e.E0Record("review", canonical(body))


def test_candidate_rejects_deployment_grant_extra_fields_and_review_rebind():
    candidate = candidate_body()
    candidate["deployment_receipt"] = {"self_attested": True}
    with pytest.raises(Refusal):
        e.E0Record("candidate", canonical(candidate))
    body = review_body()
    body["installation_authority_id"] = uid(300)
    with pytest.raises(Refusal):
        e.bind_review(intent(), installation(), e.E0Record("review", canonical(body)))


@pytest.mark.parametrize(
    "kind,builder",
    [("installation", installation_body), ("review", review_body), ("candidate", candidate_body)],
)
def test_e0_closed_records_refuse_extra_top_level_authority_flags(kind, builder):
    value = builder()
    value["verified"] = True
    with pytest.raises(Refusal):
        e.E0Record(kind, canonical(value))


def test_distinct_signature_domain_and_shape_do_not_authenticate_synthetic_vector():
    record = installation()
    packet = {
        "protocol": "maezo.d7-local-owner.e0-signed.v1",
        "kind": "installation",
        "key_id": "parent-key",
        "body": record.value(),
        "signature": encode64(bytes(64)),
    }
    assert e.signed_shape(canonical(packet)) == record  # deliberately zero, shape only
    assert e.signing_bytes(record, "parent-key").startswith(e.DOMAIN)
    for field, replacement in (
        ("protocol", "maezo.d7-local-owner.signed.v1"),
        ("kind", "candidate"),
        ("key_id", "owner-independent"),
        ("signature", encode64(bytes(63))),
    ):
        changed = deepcopy(packet)
        changed[field] = replacement
        with pytest.raises(Refusal):
            e.signed_shape(canonical(changed))
    with pytest.raises(Refusal):
        e.E0Record("installation", b" " + record.wire)
    with pytest.raises(Refusal):
        e.E0Record("installation", b"x" * (e.MAX_BYTES + 1))


def test_fixed_schema_rejects_overwrite_delete_replace_and_partial_claim_in_memory_only():
    # SQLite mechanics only: no persistent store, fsync, parent or custody claim.
    with sqlite3.connect(":memory:") as connection:
        connection.execute("PRAGMA recursive_triggers=ON")
        connection.execute("PRAGMA trusted_schema=OFF")
        connection.executescript(e.INGRESS_SCHEMA.decode("ascii"))
        row = ("reserved", "key", "reservation", b"{}", "a" * 64)
        connection.execute("INSERT INTO ingress_records VALUES (?,?,?,?,?)", row)
        connection.commit()
        for sql in (
            "UPDATE ingress_records SET record_bytes=x'7b7d'",
            "DELETE FROM ingress_records",
            "INSERT OR REPLACE INTO ingress_records VALUES (?,?,?,?,?)",
        ):
            with pytest.raises(sqlite3.IntegrityError):
                connection.execute(sql, row if "VALUES" in sql else ())
            connection.rollback()
        connection.execute(
            "INSERT INTO ingress_records VALUES (?,?,?,?,?)", ("new", "key", "reservation", b"{}", "b" * 64)
        )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute("INSERT INTO ingress_records VALUES (?,?,?,?,?)", row)
        connection.rollback()
        assert connection.execute("SELECT PK FROM ingress_records").fetchall() == [("reserved",)]
        for bad in (("bad", "k", "mutable_head", b"{}", "a" * 64), ("bad", "k", "event", b"{}", "A" * 64)):
            with pytest.raises(sqlite3.IntegrityError):
                connection.execute("INSERT INTO ingress_records VALUES (?,?,?,?,?)", bad)


def test_initial_anchor_is_not_a_caller_selected_schema_or_self_hash():
    original = installation_body()
    for field in ("schema_sha256", "initial_anchor_sha256"):
        changed = deepcopy(original)
        changed["store"][field] = "f" * 64
        with pytest.raises(Refusal):
            e.E0Record("installation", canonical(changed))
    changed = deepcopy(original)
    changed["supervisor"]["pid"] += 1
    with pytest.raises(Refusal):
        e.E0Record("installation", canonical(changed))

"""Inert E0 values: parent installation, consumed ingress and initial source review.

No signature verification, kernel/store observation, credentials or effects.
Matching these values never creates an authenticated owner capability.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .controller_storage import Document, canonical, decode64, digest, exact, parse_wire, require, scalar
from .owner_local_contracts import MAX_BYTES, LocalOwnerRecord, reservation_keys

DOMAIN = b"maezo.d7-local-owner.e0-signature.v1\0"
# Contract bytes only. There is deliberately no connection or executescript consumer.
INGRESS_SCHEMA = b"""CREATE TABLE ingress_installation (
 singleton INTEGER PRIMARY KEY CHECK(singleton = 1),
 record_bytes BLOB NOT NULL CHECK(length(record_bytes) BETWEEN 1 AND 131072),
 record_sha256 TEXT NOT NULL CHECK(length(record_sha256) = 64 AND record_sha256 NOT GLOB '*[^0-9a-f]*')
) STRICT, WITHOUT ROWID;
CREATE TABLE ingress_records (
 PK TEXT NOT NULL,
 SK TEXT NOT NULL,
 kind TEXT NOT NULL CHECK(kind IN ('reservation', 'event')),
 record_bytes BLOB NOT NULL CHECK(length(record_bytes) BETWEEN 1 AND 131072),
 record_sha256 TEXT NOT NULL CHECK(length(record_sha256) = 64 AND record_sha256 NOT GLOB '*[^0-9a-f]*'),
 PRIMARY KEY(PK, SK)
) STRICT, WITHOUT ROWID;
CREATE TRIGGER ingress_installation_no_update BEFORE UPDATE ON ingress_installation
BEGIN SELECT RAISE(ABORT, 'immutable ingress installation'); END;
CREATE TRIGGER ingress_installation_no_delete BEFORE DELETE ON ingress_installation
BEGIN SELECT RAISE(ABORT, 'immutable ingress installation'); END;
CREATE TRIGGER ingress_records_no_update BEFORE UPDATE ON ingress_records
BEGIN SELECT RAISE(ABORT, 'immutable ingress record'); END;
CREATE TRIGGER ingress_records_no_delete BEFORE DELETE ON ingress_records
BEGIN SELECT RAISE(ABORT, 'immutable ingress record'); END;
"""
INSTALLATION_FIELDS = (
    "protocol authority_id parent_operation_id parent_subject parent_key_id intent_sha256 "
    "owner_key_id owner_key_sha256 source_artifact_sha256 not_before_ms expires_at_ms supervisor store uids"
)
SUPERVISOR_FIELDS = "instance_id boot_id pid start_ticks uid executable_sha256"
STORE_FIELDS = "store_id absolute_path device inode owner_uid schema_sha256 initial_anchor_sha256"
INGRESS_FIELDS = (
    "protocol installation_sha256 intent_sha256 store_id supervisor_instance_id revision state "
    "previous_sha256 evidence_sha256 recorded_at_ms"
)
CANDIDATE_FIELDS = "protocol purpose mode intent_sha256 installation_id control_scope_id run_id scope source"
SOURCE_FIELDS = "git_sha tree_sha artifact_sha256 manifest_sha256 cib_abi_sha256 policy_profile_sha256"
REVIEW_FIELDS = (
    "protocol installation_authority_id intent_sha256 candidate trust_profile decision initial_root_sha256"
)
DECISION_FIELDS = (
    "decision_id action owner_subject reviewer_subject source_review_ref source_review_sha256 "
    "approved_source approved_tree approved_artifact_sha256 issued_at_ms expires_at_ms lease_deadline_ms"
)
INGRESS_STATES = ("CLAIMED", "ISSUE_STARTED", "ISSUED", "DELIVERY_STARTED", "DELIVERED")
_FIELDS = {
    "installation": INSTALLATION_FIELDS,
    "ingress": INGRESS_FIELDS,
    "candidate": CANDIDATE_FIELDS,
    "review": REVIEW_FIELDS,
}


def _supervisor(v: Any) -> None:
    exact(v, SUPERVISOR_FIELDS)
    for name in ("instance_id", "boot_id"):
        scalar("Uuid", v[name])
    for name in ("pid", "start_ticks"):
        scalar("Positive", v[name])
    scalar("UInt", v["uid"])
    scalar("Sha256", v["executable_sha256"])


def _store(v: Any) -> None:
    exact(v, STORE_FIELDS)
    scalar("Uuid", v["store_id"])
    for name in ("device", "owner_uid"):
        scalar("UInt", v[name])
    scalar("Positive", v["inode"])
    for name in ("schema_sha256", "initial_anchor_sha256"):
        scalar("Sha256", v[name])
    path = v["absolute_path"]
    require(type(path) is str and 1 < len(path.encode("utf8")) <= 4096 and path.startswith("/"))
    require("\0" not in path and all(p not in {"", ".", ".."} for p in path.split("/")[1:]))


def _installation(v: dict[str, Any]) -> None:
    require(v["protocol"] == "maezo.d7-local-owner.ingress-installation.v1")
    for name in ("authority_id", "parent_operation_id"):
        scalar("Uuid", v[name])
    for name in ("parent_subject", "parent_key_id", "owner_key_id"):
        scalar("Id", v[name])
    require(v["parent_key_id"] != v["owner_key_id"], "AUTH_REFUSED")
    for name in ("intent_sha256", "owner_key_sha256", "source_artifact_sha256"):
        scalar("Sha256", v[name])
    for name in ("not_before_ms", "expires_at_ms"):
        scalar("UInt", v[name])
    require(0 < v["expires_at_ms"] - v["not_before_ms"] <= 900_000)
    _supervisor(v["supervisor"])
    _store(v["store"])
    uids = exact(v["uids"], "owner reader requester")
    for uid in uids.values():
        scalar("UInt", uid)
    require(len(set(uids.values())) == 3 and uids["owner"] > 0 and uids["reader"] > 0)
    require(v["store"]["owner_uid"] == uids["owner"], "AUTH_REFUSED")
    require(v["store"]["schema_sha256"] == digest(INGRESS_SCHEMA), "PRECONDITION_MISMATCH")
    require(
        v["store"]["initial_anchor_sha256"] == digest(canonical(_anchor_value(v))), "PRECONDITION_MISMATCH"
    )


def _anchor_value(v: dict[str, Any]) -> dict[str, Any]:
    return {
        "protocol": "maezo.d7-local-owner.ingress-anchor.v1",
        **{
            name: v[name]
            for name in (
                "authority_id",
                "parent_operation_id",
                "intent_sha256",
                "source_artifact_sha256",
                "supervisor",
            )
        },
        "store": {k: x for k, x in v["store"].items() if k != "initial_anchor_sha256"},
    }


def _ingress(v: dict[str, Any]) -> None:
    require(v["protocol"] == "maezo.d7-local-owner.ingress.v1")
    for name in ("installation_sha256", "intent_sha256"):
        scalar("Sha256", v[name])
    for name in ("store_id", "supervisor_instance_id"):
        scalar("Uuid", v[name])
    scalar("UInt", v["recorded_at_ms"])
    require(type(v["revision"]) is int and 1 <= v["revision"] <= 64)
    require(type(v["state"]) is str and v["state"] in {*INGRESS_STATES, "UNKNOWN"})
    if v["state"] == "CLAIMED":
        require(v["revision"] == 1 and v["previous_sha256"] is None)
    else:
        require(v["revision"] > 1)
        scalar("Sha256", v["previous_sha256"])
    if v["state"] in {"ISSUED", "DELIVERED"} or (
        v["state"] == "UNKNOWN" and v["evidence_sha256"] is not None
    ):
        scalar("Sha256", v["evidence_sha256"])
    else:
        require(v["evidence_sha256"] is None)


def _candidate(v: dict[str, Any]) -> None:
    require(v["protocol"] == "maezo.d7-initial-source-candidate.v1")
    require(v["purpose"] == "INITIAL_SOURCE_REVIEW" and v["mode"] == "PUBLIC_SYNTHETIC")
    scalar("Sha256", v["intent_sha256"])
    for name in ("installation_id", "control_scope_id", "run_id"):
        scalar("Uuid", v[name])
    scalar("Scope", v["scope"])
    source = exact(v["source"], SOURCE_FIELDS)
    for name, value in source.items():
        scalar("GitOid" if name in {"git_sha", "tree_sha"} else "Sha256", value)


def _root_value(v: dict[str, Any]) -> dict[str, Any]:
    candidate, decision = v["candidate"], v["decision"]
    empty = digest(canonical([]))
    return {
        "schema_version": 1,
        "control_scope_id": candidate["control_scope_id"],
        "scope": candidate["scope"],
        "owner_subject": decision["owner_subject"],
        "run_id": candidate["run_id"],
        "epoch": 1,
        "revision": 1,
        "state": "REVIEWED",
        "candidate_sha256": digest(canonical(candidate)),
        "trust_profile_sha256": digest(canonical(v["trust_profile"])),
        "lease_deadline_ms": decision["lease_deadline_ms"],
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


def _review(v: dict[str, Any]) -> None:
    require(v["protocol"] == "maezo.d7-local-owner.initial-review.v1")
    scalar("Uuid", v["installation_authority_id"])
    scalar("Sha256", v["intent_sha256"])
    scalar("Sha256", v["initial_root_sha256"])
    candidate = exact(v["candidate"], CANDIDATE_FIELDS)
    _candidate(candidate)
    require(candidate["intent_sha256"] == v["intent_sha256"], "SCOPE_REFUSED")
    scalar("TrustProfile", v["trust_profile"])
    profile = v["trust_profile"]
    require(profile["scope"] == candidate["scope"], "SCOPE_REFUSED")
    require(profile["release_source"] == candidate["source"]["git_sha"])
    require(profile["release_tree"] == candidate["source"]["tree_sha"])
    d = exact(v["decision"], DECISION_FIELDS)
    scalar("Uuid", d["decision_id"])
    require(d["action"] == "APPROVE_INITIAL_SOURCE")
    for name in ("owner_subject", "reviewer_subject", "source_review_ref"):
        scalar("Id", d[name])
    require(d["owner_subject"] != d["reviewer_subject"], "AUTH_REFUSED")
    for name in ("source_review_sha256", "approved_artifact_sha256"):
        scalar("Sha256", d[name])
    for name in ("approved_source", "approved_tree"):
        scalar("GitOid", d[name])
    require(d["approved_source"] == candidate["source"]["git_sha"])
    require(d["approved_tree"] == candidate["source"]["tree_sha"])
    require(d["approved_artifact_sha256"] == candidate["source"]["artifact_sha256"])
    for name in ("issued_at_ms", "expires_at_ms", "lease_deadline_ms"):
        scalar("UInt", d[name])
    require(0 < d["expires_at_ms"] - d["issued_at_ms"] <= 900_000)
    require(0 < d["lease_deadline_ms"] - d["issued_at_ms"] <= profile["limits"]["lease_term_ms"])
    require(d["lease_deadline_ms"] <= d["expires_at_ms"])
    root = Document("Root", canonical(_root_value(v)))
    require(root.digest() == v["initial_root_sha256"], "PRECONDITION_MISMATCH")


@dataclass(frozen=True, slots=True)
class E0Record:
    """Immutable structural record; never an installed trust or live capability."""

    kind: str
    wire: bytes

    def __post_init__(self) -> None:
        require(type(self.kind) is str and self.kind in _FIELDS)
        require(type(self.wire) is bytes and 0 < len(self.wire) <= MAX_BYTES)
        value = exact(parse_wire(self.wire), _FIELDS[self.kind])
        if self.kind == "installation":
            _installation(value)
        elif self.kind == "ingress":
            _ingress(value)
        elif self.kind == "candidate":
            _candidate(value)
        else:
            _review(value)

    def value(self) -> dict[str, Any]:
        return exact(parse_wire(self.wire), _FIELDS[self.kind])

    def digest(self) -> str:
        return digest(self.wire)


def bind_installation(intent: LocalOwnerRecord, installation: E0Record) -> None:
    require(intent.kind == "intent" and installation.kind == "installation")
    i, s = intent.value(), installation.value()
    require(s["intent_sha256"] == intent.digest(), "SCOPE_REFUSED")
    require(s["owner_key_id"] == i["trust"]["key_id"])
    require(s["owner_key_sha256"] == i["trust"]["public_key_sha256"], "AUTH_REFUSED")
    require(s["source_artifact_sha256"] == i["source"]["artifact_sha256"], "AUTH_REFUSED")
    require(i["not_before_ms"] <= s["not_before_ms"] < s["expires_at_ms"] <= i["deadline_ms"])


def ingress_keys(intent: LocalOwnerRecord, installation: E0Record) -> tuple[tuple[str, str], ...]:
    bind_installation(intent, installation)
    s = installation.value()
    return tuple(
        sorted(
            (
                *reservation_keys(intent),
                ("D7INGRESS#AUTHORITY#" + s["authority_id"], "RESERVATION"),
                ("D7INGRESS#STORE#" + s["store"]["store_id"], "RESERVATION"),
                ("D7INGRESS#SUPERVISOR#" + s["supervisor"]["instance_id"], "RESERVATION"),
            )
        )
    )


def bind_ingress(installation: E0Record, ingress: E0Record) -> None:
    require(installation.kind == "installation" and ingress.kind == "ingress")
    s, e = installation.value(), ingress.value()
    require(e["installation_sha256"] == installation.digest() and e["intent_sha256"] == s["intent_sha256"])
    require(e["store_id"] == s["store"]["store_id"])
    require(e["supervisor_instance_id"] == s["supervisor"]["instance_id"])
    require(e["recorded_at_ms"] >= s["not_before_ms"])
    if e["state"] != "UNKNOWN":
        require(e["recorded_at_ms"] < s["expires_at_ms"], "AUTH_REFUSED")


def ingress_successor(previous: E0Record, current: E0Record) -> None:
    require(previous.kind == current.kind == "ingress")
    p, c = previous.value(), current.value()
    require(p["state"] not in {"UNKNOWN", "DELIVERED"}, "AUTH_REFUSED")
    require(
        all(
            p[n] == c[n]
            for n in (
                "installation_sha256",
                "intent_sha256",
                "store_id",
                "supervisor_instance_id",
            )
        )
    )
    require(c["revision"] == p["revision"] + 1 and c["previous_sha256"] == previous.digest())
    require(c["recorded_at_ms"] >= p["recorded_at_ms"])
    require(c["state"] == "UNKNOWN" or c["state"] == INGRESS_STATES[INGRESS_STATES.index(p["state"]) + 1])


def compare_continuity_values(
    installation: E0Record,
    *,
    observed_supervisor: dict[str, Any],
    observed_store: dict[str, Any],
    history: tuple[E0Record, ...],
    witness_revision: int,
    witness_sha256: str,
    restarted: bool,
) -> None:
    """Value comparison ONLY; no kernel/SQLite reads or authenticated witness creation."""
    require(installation.kind == "installation" and restarted is False, "AUTH_REFUSED")
    _supervisor(observed_supervisor)
    _store(observed_store)
    s = installation.value()
    require(observed_supervisor == s["supervisor"] and observed_store == s["store"], "AUTH_REFUSED")
    require(type(history) is tuple and 1 <= len(history) <= 64)
    scalar("Positive", witness_revision)
    scalar("Sha256", witness_sha256)
    require(history[0].kind == "ingress" and history[0].value()["state"] == "CLAIMED")
    for n, record in enumerate(history):
        bind_ingress(installation, record)
        if n:
            ingress_successor(history[n - 1], record)
    require(history[-1].value()["revision"] == witness_revision)
    require(history[-1].digest() == witness_sha256, "PRECONDITION_MISMATCH")


def bind_review(intent: LocalOwnerRecord, installation: E0Record, review: E0Record) -> None:
    bind_installation(intent, installation)
    require(review.kind == "review")
    i, s, r = intent.value(), installation.value(), review.value()
    require(r["intent_sha256"] == intent.digest() and r["installation_authority_id"] == s["authority_id"])
    candidate = r["candidate"]
    require(
        all(
            candidate[n] == i[n] for n in ("installation_id", "control_scope_id", "run_id", "scope", "source")
        )
    )
    require(s["not_before_ms"] <= r["decision"]["issued_at_ms"])
    require(r["decision"]["expires_at_ms"] <= s["expires_at_ms"])


def initial_root(review: E0Record) -> Document:
    """Construct initial Root bytes only; neither signed approval nor committed state."""
    require(review.kind == "review")
    return Document("Root", canonical(_root_value(review.value())))


def signing_bytes(record: E0Record, key_id: str) -> bytes:
    scalar("Id", key_id)
    if record.kind == "installation":
        require(key_id == record.value()["parent_key_id"], "AUTH_REFUSED")
    return DOMAIN + canonical(
        {
            "protocol": "maezo.d7-local-owner.e0-signed.v1",
            "kind": record.kind,
            "key_id": key_id,
            "body": record.value(),
        }
    )


def signed_shape(wire: bytes) -> E0Record:
    """Shape only; a valid encoding or test signature never supplies parent authority."""
    require(type(wire) is bytes and 0 < len(wire) <= MAX_BYTES)
    v = exact(parse_wire(wire), "protocol kind key_id body signature")
    require(v["protocol"] == "maezo.d7-local-owner.e0-signed.v1")
    scalar("Id", v["key_id"])
    scalar("Signature", v["signature"])
    require(len(decode64(v["signature"])) == 64)
    record = E0Record(v["kind"], canonical(v["body"]))
    signing_bytes(record, v["key_id"])
    return record

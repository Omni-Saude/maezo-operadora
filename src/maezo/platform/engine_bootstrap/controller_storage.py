"""Closed D7 storage records and immutable protocol constructors.

Contract 736d48c7 / review 99fbae5e; W1 remains the approved depth32 profile.
These values never authenticate an owner or replace a qualified storage connection.
"""

from __future__ import annotations

import base64
import hashlib
from dataclasses import dataclass
from typing import Any, cast

from maezo.platform.engine_bootstrap import controller_contracts as c

MAX_BYTES = 1_048_576
MAX_ROOT_BYTES = 131_072
CHUNK_BYTES = 131_072
MAX_CHUNKS = 8
MAX_ITEMS = 12
MAX_TRANSACTION_BYTES = 2_097_152
MAX_INVENTORY = 1024
MAX_LINEAGE = 32
MAX_DEPTH = 32
Refusal = c.ContractError


def present[T](value: T | None, code: c.RefusalCode = "INVALID_BODY") -> T:
    if value is None:
        raise Refusal(code)
    return value


def require(condition: bool, code: c.RefusalCode = "INVALID_BODY") -> None:
    if not condition:
        raise Refusal(code)


def canonical(value: Any) -> bytes:
    return c.canonical_json(value)


def parse_wire(raw: bytes | None) -> Any:
    return c.decode_json(present(raw))


def digest(raw: bytes) -> str:
    require(type(raw) is bytes and 0 < len(raw) <= MAX_BYTES)
    return hashlib.sha256(raw).hexdigest()


def encode64(raw: bytes) -> str:
    require(type(raw) is bytes and len(raw) <= MAX_BYTES)
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def decode64(value: str) -> bytes:
    return c.decode_base64(value)


def exact(value: Any, fields: str | tuple[str, ...] | list[str]) -> dict[str, Any]:
    names = fields.split() if isinstance(fields, str) else fields
    require(type(value) is dict and set(value) == set(names))
    return cast(dict[str, Any], value)


def scalar(name: str, value: Any) -> Any:
    return c.parse(name, canonical(value))


def typed(name: str, value: Any) -> Any:
    return c.parse(name, canonical(value))


def same(
    left: dict[str, Any], right: dict[str, Any], names: str, code: c.RefusalCode = "INVALID_BODY"
) -> None:
    require(all(left[n] == right[n] for n in names.split()), code)


ROOT_FIELDS = (
    "schema_version control_scope_id scope owner_subject run_id epoch revision state "
    "candidate_sha256 trust_profile_sha256 lease_deadline_ms db_fence_epoch current_generation_id "
    "next_generation_id journal_revision pending_operation_ids pending_index_sha256 "
    "managed_registry_sha256 retired_index_sha256 "
    "activation_decision_sha256 restore_receipt_sha256 "
    "readiness_result_sha256 restore_state current_owner_claim_sha256"
)
OPERATION_PROOF_FIELDS = (
    "protocol scope run_id epoch operation controller_state controller_revision "
    "trust_profile_sha256 control_observation_sha256 database_observation_sha256 "
    "observation_pair_sha256 observed_at_ms observation_deadline_ms "
    "controller_lease_deadline_ms journal_revision pending_index_sha256 "
    "settled_journal_sha256 managed_registry_sha256 restore_reconciliation_sha256"
)
PHASE_PROOF_FIELDS = (
    "protocol scope run_id epoch generation_id generation_binding_sha256 "
    "decision_sha256 purpose phase controller_state controller_revision source tree "
    "image_sha256 config_set_sha256 trust_profile_sha256 control_observation_sha256 "
    "database_observation_sha256 observation_pair_sha256 observed_at_ms observation_deadline_ms "
    "controller_lease_deadline_ms restore_receipt_sha256 readiness_result_sha256 "
    "journal_revision settled_journal_sha256 managed_registry_sha256 pending_index_sha256"
)
OWNER_FIELDS = "owner_subject run_id epoch"
FENCE_OWNER_FIELDS = (
    "owner_subject run_id epoch revision controller_revision database_binding_sha256 "
    "next_generation_id current_generation_id"
)
CLAIM_FIELDS = (
    "protocol control_scope_id scope table_identity_sha256 claim_id previous_claim_sha256 "
    "previous_owner successor_owner previous_root_revision committed_root_revision "
    "previous_lease_deadline_ms claim_observed_at_ms next_generation_id journal_revision "
    "pending_index_sha256 managed_registry_sha256 retired_index_sha256 request_sha256"
)
LINEAGE_FIELDS = (
    "protocol scope control_scope_id table_identity_sha256 challenge_sha256 "
    "authenticated_observer_subject root_bytes root_sha256 claim_receipt_bytes "
    "claim_receipt_sha256 ancestor_claim_bytes ancestor_claim_sha256s strong_read_revision "
    "reread_revision observed_at_ms deadline_ms"
)
HANDOFF_FIELDS = (
    "protocol operation_proof owner_subject owner_lineage_bytes owner_lineage_sha256 "
    "old_fence_owner_sha256 restore_reconciliation_bytes restore_reconciliation_sha256"
)
RECONCILE_FIELDS = (
    "operation mode database_binding expected_owner successor_owner claim_id "
    "claim_receipt_sha256 next_generation_id retired_generation_index_sha256"
)
PREPARATION_FIELDS = (
    "protocol installation_id control_scope_id scope database_binding owner_operation_id "
    "preparation_id epoch run_id principal"
)
GENERATION_TARGET_FIELDS = (
    "kind purpose generation_id login_name credential_binding_sha256 "
    "immutable_secret_resource_version task_definition_revision task_role "
    "execution_role network_binding image_sha256 config_set_sha256"
)
ONE_SHOT_FIELDS = "kind purpose login_name native_actor resource_identity resource_identity_sha256"
OWNER_RESULT_FIELDS = (
    "protocol installation_id owner_operation_id preparation_id event scope "
    "database_binding_sha256 principal generation_core owner_catalog_bytes "
    "owner_catalog_sha256 external_resource_readback_sha256 native_qualification "
    "request_sha256 owner_session_user owner_login_oid owner_effective_user "
    "owner_effective_oid recorded_before_commit_at_ms"
)
NATIVE_QUALIFICATION_FIELDS = (
    "protocol native_installation_receipt_sha256 native_owner_operation_id "
    "d_preparation_id scope generation_id purpose generation_core_sha256 "
    "decision_sha256 login_name login_oid database_binding_sha256 helper_abi_sha256 "
    "before_catalog_sha256 after_catalog_sha256 native_receipt_bytes "
    "native_receipt_sha256"
)
RESTORE_FIELDS = (
    "protocol scope old_database_binding new_database_binding control_scope_id "
    "controller_table_identity_sha256 owner_subject run_id epoch owner_recovery_id "
    "control_observation_bytes database_observation_bytes closure_manifest_bytes "
    "closure_manifest_sha256 next_generation_id retained_generation_index_sha256 "
    "pending_index_sha256 journal_revision observed_at_ms deadline_ms"
)
STORE_FIELDS = "protocol scope run_id epoch issuer_operation_id expected_fence_revision request proof"
GENERATION_OPERATIONS = frozenset(
    {"prepare_generation", "admit_candidate_login", "open_runtime_generation", "retire_generation"}
)
OPERATIONS = GENERATION_OPERATIONS | {
    "close_runtime_fence",
    "mark_recovery",
    "reconcile_fence",
    "record_current_proof",
    "issue_permit",
    "revoke_permit",
}
REQUEST_FIELDS = {
    "close_runtime_fence": "operation candidate_sha256 trust_profile_sha256",
    "mark_recovery": "operation reason",
    "reconcile_fence": RECONCILE_FIELDS,
    "record_current_proof": (
        "operation generation_id expected_generation_revision phase_proof_bytes phase_proof_sha256"
    ),
    "issue_permit": (
        "operation permit_id native_operation permit_purpose "
        "logical_request_sha256 login_name login_oid "
        "controller_task_identity_sha256 principal_origin"
    ),
    "revoke_permit": "operation permit_id reason",
}
NULLABLE_DIGESTS = frozenset(
    {
        "settled_journal_sha256",
        "managed_registry_sha256",
        "restore_reconciliation_sha256",
        "readiness_result_sha256",
        "restore_receipt_sha256",
        "activation_decision_sha256",
        "current_owner_claim_sha256",
        "previous_claim_sha256",
        "claim_receipt_sha256",
    }
)
UUID_FIELDS = frozenset(
    {
        "run_id",
        "control_scope_id",
        "issuer_operation_id",
        "claim_id",
        "installation_id",
        "preparation_id",
        "owner_operation_id",
        "d_preparation_id",
        "native_owner_operation_id",
        "owner_recovery_id",
        "permit_id",
    }
)
POSITIVE_FIELDS = frozenset(
    {
        "epoch",
        "revision",
        "generation_id",
        "next_generation_id",
        "expected_fence_revision",
        "expected_generation_revision",
        "previous_root_revision",
        "committed_root_revision",
        "strong_read_revision",
        "reread_revision",
    }
)
ID_FIELDS = frozenset(
    {
        "owner_subject",
        "authenticated_observer_subject",
        "login_name",
        "native_actor",
        "resource_identity",
        "owner_session_user",
        "owner_effective_user",
    }
)


def _scalars(value: dict[str, Any]) -> None:
    for key, item in value.items():
        if key.endswith("_sha256"):
            if item is not None or key not in NULLABLE_DIGESTS:
                scalar("Sha256", item)
        elif key in UUID_FIELDS:
            if key != "claim_id" or item is not None:
                scalar("Uuid", item)
        elif key in POSITIVE_FIELDS:
            scalar("Positive", item)
        elif key.endswith("_ms") or key in {"controller_revision", "journal_revision", "db_fence_epoch"}:
            scalar("UInt", item)
        elif key in ID_FIELDS:
            scalar("Id", item)
        elif key.endswith("_oid"):
            scalar("Oid", item)
        elif key == "scope":
            typed("Scope", item)
        elif key.endswith("database_binding") or key == "database_binding":
            typed("DatabaseBinding", item)
        elif key == "current_generation_id" and item is not None:
            scalar("Positive", item)


def _pair(value: dict[str, Any], bytes_key: str, hash_key: str, nullable: bool = False) -> bytes | None:
    b, h = value[bytes_key], value[hash_key]
    if b is None:
        require(nullable and h is None)
        return None
    raw = decode64(b)
    require(digest(raw) == h)
    parse_wire(raw)
    return raw


def validate_root(v: dict[str, Any]) -> None:
    exact(v, ROOT_FIELDS)
    _scalars(v)
    require(v["schema_version"] == 1 and type(v["schema_version"]) is int)
    scalar("ControllerState", v["state"])
    require(v["restore_state"] in {"UNRECONCILED", "RECONCILED"})
    pending = v["pending_operation_ids"]
    require(type(pending) is list and len(pending) <= MAX_INVENTORY)
    for identifier in pending:
        scalar("Uuid", identifier)
    require(pending == sorted(set(pending)) and v["pending_index_sha256"] == digest(canonical(pending)))
    require(len(canonical(v)) <= MAX_ROOT_BYTES)
    require(v["current_generation_id"] is None or v["current_generation_id"] < v["next_generation_id"])
    if v["state"] == "ACTIVE":
        require(v["restore_state"] == "RECONCILED" and not pending and v["current_generation_id"] is not None)
        require(
            all(
                v[k] is not None
                for k in ("activation_decision_sha256", "restore_receipt_sha256", "readiness_result_sha256")
            )
        )


def validate_owner(v: dict[str, Any]) -> None:
    exact(v, OWNER_FIELDS)
    _scalars(v)


def validate_claim(v: dict[str, Any]) -> None:
    exact(v, CLAIM_FIELDS)
    _scalars(v)
    require(v["protocol"] == "maezo.d7-owner-claim.v1")
    validate_owner(v["previous_owner"])
    validate_owner(v["successor_owner"])
    require(v["successor_owner"]["epoch"] > v["previous_owner"]["epoch"], "STALE_EPOCH")
    require(v["committed_root_revision"] == v["previous_root_revision"] + 1)
    require(v["claim_observed_at_ms"] > v["previous_lease_deadline_ms"], "PRECONDITION_MISMATCH")


def validate_lineage(v: dict[str, Any]) -> None:
    exact(v, LINEAGE_FIELDS)
    _scalars(v)
    require(v["protocol"] == "maezo.d7-owner-lineage-read.v1")
    root_raw = _pair(v, "root_bytes", "root_sha256")
    require(root_raw is not None)
    root = parse_wire(root_raw)
    validate_root(root)
    same(v, root, "scope control_scope_id")
    require(v["strong_read_revision"] == v["reread_revision"] == root["revision"], "PRECONDITION_MISMATCH")
    require(v["observed_at_ms"] < v["deadline_ms"])
    ancestors, hashes = v["ancestor_claim_bytes"], v["ancestor_claim_sha256s"]
    require(type(ancestors) is list and type(hashes) is list and len(ancestors) == len(hashes) <= MAX_LINEAGE)
    current = _pair(v, "claim_receipt_bytes", "claim_receipt_sha256", True)
    require(v["claim_receipt_sha256"] == root["current_owner_claim_sha256"])
    if current is None:
        require(not ancestors)
        return
    chain = []
    for wire, h in zip(ancestors, hashes, strict=True):
        b = decode64(wire)
        require(digest(b) == h)
        chain.append(parse_wire(b))
    chain.append(parse_wire(current))
    for claim in chain:
        validate_claim(claim)
        same(claim, v, "scope control_scope_id table_identity_sha256")
    for prev, nxt in zip(chain[:-1], chain[1:], strict=True):
        require(nxt["previous_claim_sha256"] == digest(canonical(prev)))
        require(prev["successor_owner"] == nxt["previous_owner"])
        require(nxt["previous_root_revision"] >= prev["committed_root_revision"])
        require(nxt["next_generation_id"] >= prev["next_generation_id"])
    latest = chain[-1]
    require(latest["successor_owner"] == {k: root[k] for k in OWNER_FIELDS.split()})
    require(root["revision"] >= latest["committed_root_revision"])
    require(root["next_generation_id"] >= latest["next_generation_id"])


def validate_operation_proof(v: dict[str, Any]) -> None:
    exact(v, OPERATION_PROOF_FIELDS)
    _scalars(v)
    require(v["protocol"] == "maezo.d7-operation-proof.v1" and v["operation"] in OPERATIONS)
    scalar("ControllerState", v["controller_state"])
    require(v["observed_at_ms"] < min(v["observation_deadline_ms"], v["controller_lease_deadline_ms"]))


def validate_phase_proof(v: dict[str, Any]) -> None:
    exact(v, PHASE_PROOF_FIELDS)
    _scalars(v)
    require(v["protocol"] == "maezo.d7-phase-proof.v1")
    require(
        (v["phase"], v["purpose"], v["controller_state"])
        in {
            ("CANDIDATE_QUALIFICATION", "candidate", "CANDIDATE_ADMITTED"),
            ("RESTORE_QUALIFICATION", "runtime", "RESTORING"),
            ("ACTIVE", "runtime", "ACTIVE"),
        }
    )
    scalar("GitOid", v["source"])
    scalar("GitOid", v["tree"])
    require(v["observed_at_ms"] < min(v["observation_deadline_ms"], v["controller_lease_deadline_ms"]))
    require(
        v["restore_receipt_sha256"] is not None
        and v["settled_journal_sha256"] is not None
        and v["managed_registry_sha256"] is not None
    )
    require((v["readiness_result_sha256"] is not None) == (v["phase"] == "ACTIVE"))


def validate_restore(v: dict[str, Any]) -> None:
    exact(v, RESTORE_FIELDS)
    _scalars(v)
    require(v["protocol"] == "maezo.d7-owner-restore-reconciliation.v1")
    typed("ControlObservation", parse_wire(decode64(v["control_observation_bytes"])))
    typed("DatabaseObservation", parse_wire(decode64(v["database_observation_bytes"])))
    rows = parse_wire(_pair(v, "closure_manifest_bytes", "closure_manifest_sha256"))
    require(type(rows) is list and len(rows) <= MAX_INVENTORY)
    keys = []
    for row in rows:
        exact(row, "resource_kind resource_identity terminal_proof_sha256 observation_sha256")
        _scalars(row)
        require(
            row["resource_kind"]
            in {
                "role",
                "secret",
                "task",
                "service",
                "task_set",
                "session",
                "pool",
                "prepared_transaction",
                "dispatch",
                "native_history",
            }
        )
        keys.append((row["resource_kind"], row["resource_identity"]))
    require(keys == sorted(set(keys)) and v["observed_at_ms"] < v["deadline_ms"])


def validate_handoff(v: dict[str, Any]) -> None:
    exact(v, HANDOFF_FIELDS)
    _scalars(v)
    require(v["protocol"] == "maezo.d7-owner-handoff-proof.v1")
    validate_operation_proof(v["operation_proof"])
    lineage = parse_wire(_pair(v, "owner_lineage_bytes", "owner_lineage_sha256"))
    validate_lineage(lineage)
    root = parse_wire(decode64(lineage["root_bytes"]))
    same(root, v["operation_proof"], "scope run_id epoch")
    require(v["owner_subject"] == root["owner_subject"])
    restore = _pair(v, "restore_reconciliation_bytes", "restore_reconciliation_sha256", True)
    if restore is not None:
        validate_restore(parse_wire(restore))


def validate_preparation(v: dict[str, Any]) -> None:
    exact(v, PREPARATION_FIELDS)
    _scalars(v)
    require(v["protocol"] == "maezo.d7-owner-preparation.v1")
    p = v["principal"]
    if p.get("kind") == "generation":
        exact(p, GENERATION_TARGET_FIELDS)
        _scalars(p)
        core = {k: value for k, value in p.items() if k != "kind"}
        core.update(scope=v["scope"], epoch=v["epoch"], login_oid=1)
        typed("GenerationCore", core)  # Validates plan shape; actual OID is always owner catalog output.
    else:
        exact(p, ONE_SHOT_FIELDS)
        _scalars(p)
        require(p["kind"] == "one_shot" and p["purpose"] in {"deploy", "grant", "receipt_read"})
        identity = {k: p[k] for k in ("kind", "purpose", "resource_identity")}
        identity["scope"] = v["scope"]
        require(digest(canonical(identity)) == p["resource_identity_sha256"])


def validate_native_qualification(v: dict[str, Any]) -> None:
    exact(v, NATIVE_QUALIFICATION_FIELDS)
    _scalars(v)
    require(v["protocol"] == "maezo.d7-native-principal-qualification-binding.v1")
    require(v["purpose"] in {"candidate", "runtime"})
    require(v["helper_abi_sha256"] == "b0c5e39a02becb61903559a58dceecd440d7102efcb6c66ef3878876632dd71d")
    _pair(v, "native_receipt_bytes", "native_receipt_sha256")


def validate_owner_result(v: dict[str, Any]) -> None:
    exact(v, OWNER_RESULT_FIELDS)
    _scalars(v)
    require(v["protocol"] == "maezo.d7-owner-preparation-result.v1")
    require(v["event"] in {"PREPARED", "NATIVE_QUALIFIED", "REMOVED"})
    p = v["principal"]
    require(type(p) is dict)
    expected = (GENERATION_TARGET_FIELDS if p.get("kind") == "generation" else ONE_SHOT_FIELDS) + " login_oid"
    exact(p, expected)
    _scalars(p)
    if p["kind"] == "generation":
        core = typed("GenerationCore", v["generation_core"]).to_wire()
        require(all(core[k] == item for k, item in p.items() if k != "kind"))
        require(core["scope"] == v["scope"])
    else:
        require(v["generation_core"] is None and p["kind"] == "one_shot")
    _pair(v, "owner_catalog_bytes", "owner_catalog_sha256")
    if v["event"] == "NATIVE_QUALIFIED":
        validate_native_qualification(v["native_qualification"])
        require(v["native_qualification"]["d_preparation_id"] == v["preparation_id"])
        same(v["native_qualification"], p, "login_name login_oid purpose generation_id")
    else:
        require(v["native_qualification"] is None)


VALIDATORS = {
    "Root": validate_root,
    "OwnerClaimReceipt": validate_claim,
    "OwnerLineageRead": validate_lineage,
    "OperationProof": validate_operation_proof,
    "PhaseProof": validate_phase_proof,
    "OwnerHandoffProof": validate_handoff,
    "OwnerPreparationInput": validate_preparation,
    "OwnerPreparationResult": validate_owner_result,
    "OwnerRestoreReconciliation": validate_restore,
    "NativePrincipalQualificationBinding": validate_native_qualification,
}


@dataclass(frozen=True, slots=True)
class Document:
    """Immutable retained bytes; each value() call returns a detached mutable copy."""

    kind: str
    wire: bytes

    def __post_init__(self) -> None:
        require(self.kind in VALIDATORS and type(self.wire) is bytes)
        value = parse_wire(self.wire)
        try:
            VALIDATORS[self.kind](value)
        except Refusal:
            raise
        except (KeyError, TypeError, AttributeError, ValueError):
            raise Refusal() from None

    @classmethod
    def create(cls, kind: str, value: Any) -> Document:
        return cls(kind, canonical(value))

    def value(self) -> dict[str, Any]:
        return cast(dict[str, Any], parse_wire(self.wire))

    def digest(self) -> str:
        return digest(self.wire)


def prepare_request(
    request: c.IssuerRequest, decision: c.CandidateDecision | c.ActivationDecision
) -> dict[str, Any]:
    if type(request) is not c.IssuerRequest or not isinstance(request.body, c.IssuerPrepare):
        raise Refusal("INVALID_BODY")
    c.check_issuer_request(request, generation=request.body.generation, decision=decision)
    require(request.body.generation.status == "PREPARED" and request.body.generation.revision == 1)
    result = {
        "issuer_request_bytes": encode64(request.canonical_bytes()),
        "request_sha256": request.digest(),
        "retained_decision": {
            "purpose": decision.purpose,
            "decision_bytes": encode64(decision.canonical_bytes()),
            "decision_sha256": decision.digest(),
        },
    }
    canonical(result)
    return result


def validate_principal_origin(origin: Any, *, operation: str, purpose: str) -> dict[str, Any]:
    """Storage-only provenance selector; owner/catalog qualification remains mandatory."""
    require(type(origin) is dict and len(canonical(origin)) <= 4096)
    if origin.get("kind") == "owner_prepared_one_shot":
        exact(origin, "kind preparation_id")
        scalar("Uuid", origin["preparation_id"])
    else:
        exact(
            origin,
            (
                "kind installation_id installation_binding_sha256 "
                "role_acl_manifest_sha256 role_class native_actor"
            ),
        )
        require(origin["kind"] == "installation_receipt_reader")
        require(operation == "receipt" and purpose == "receipt_read", "PURPOSE_REFUSED")
        scalar("Uuid", origin["installation_id"])
        for field in ("installation_binding_sha256", "role_acl_manifest_sha256"):
            scalar("Sha256", origin[field])
        scalar("Id", origin["native_actor"])
        require(origin["role_class"] in {"actual issuer login", "scoped D observer login"}, "AUTH_REFUSED")
    return cast(dict[str, Any], origin)


def validate_store_call(operation: str, value: dict[str, Any]) -> dict[str, Any]:
    require(operation in OPERATIONS, "UNSUPPORTED_ADAPTER")
    exact(value, STORE_FIELDS)
    _scalars(value)
    require(value["protocol"] == "maezo.d7-store-call.v1")
    canonical(value)
    request, proof = value["request"], value["proof"]
    if operation in GENERATION_OPERATIONS:
        exact(
            request,
            "issuer_request_bytes request_sha256 retained_decision"
            if operation == "prepare_generation"
            else "issuer_request_bytes request_sha256",
        )
        raw = decode64(request["issuer_request_bytes"])
        parsed = c.parse("IssuerRequest", raw)
        require(parsed.digest() == request["request_sha256"])
        same(parsed.to_wire(), value, "scope run_id epoch issuer_operation_id")
        require(parsed.body.operation == operation)
        if operation == "prepare_generation":
            carrier = exact(request["retained_decision"], "purpose decision_bytes decision_sha256")
            require(carrier["purpose"] in {"candidate", "runtime"})
            decision = c.parse(
                "CandidateDecision" if carrier["purpose"] == "candidate" else "ActivationDecision",
                decode64(carrier["decision_bytes"]),
            )
            require(decision.digest() == carrier["decision_sha256"])
            require(prepare_request(parsed, decision) == request)
        validate_operation_proof(proof)
    else:
        exact(request, REQUEST_FIELDS[operation])
        _scalars(request)
        require(request["operation"] == operation)
        if operation == "record_current_proof":
            retained = _pair(request, "phase_proof_bytes", "phase_proof_sha256")
            require(parse_wire(retained) == proof)
            validate_phase_proof(proof)
        elif operation == "reconcile_fence":
            validate_handoff(proof)
            exact(request["expected_owner"], FENCE_OWNER_FIELDS)
            _scalars(request["expected_owner"])
            validate_owner(request["successor_owner"])
            require(
                request["mode"]
                in {"same_incarnation_successor", "current_owner_reconcile", "database_restore"}
            )
            require(proof["old_fence_owner_sha256"] == digest(canonical(request["expected_owner"])))
            same(request["successor_owner"], value, "run_id epoch")
            require(request["successor_owner"]["owner_subject"] == proof["owner_subject"])
            if request["mode"] == "current_owner_reconcile":
                require(request["claim_id"] is None and request["claim_receipt_sha256"] is None)
            else:
                scalar("Uuid", request["claim_id"])
                scalar("Sha256", request["claim_receipt_sha256"])
            require(
                (proof["restore_reconciliation_bytes"] is not None) == (request["mode"] == "database_restore")
            )
        else:
            validate_operation_proof(proof)
            if operation in {"mark_recovery", "revoke_permit"}:
                scalar("RefusalCode", request["reason"])
            if operation == "issue_permit":
                validate_principal_origin(
                    request["principal_origin"],
                    operation=request["native_operation"],
                    purpose=request["permit_purpose"],
                )
                require(
                    (request["native_operation"], request["permit_purpose"])
                    in {("deploy", "deploy"), ("grant", "grant"), ("receipt", "receipt_read")}
                )
    op_proof = proof["operation_proof"] if operation == "reconcile_fence" else proof
    same(value, op_proof, "scope run_id epoch")
    if operation != "record_current_proof":
        require(op_proof["operation"] == operation)
    return cast(dict[str, Any], parse_wire(canonical(value)))


def logical_request_digest(operation: str, value: dict[str, Any]) -> str:
    checked = validate_store_call(operation, value)
    if operation in GENERATION_OPERATIONS:
        return cast(str, checked["request"]["request_sha256"])
    return digest(canonical({k: v for k, v in checked.items() if k != "proof"}))


def historical_retirement(
    request: c.IssuerRequest,
    generation: c.RuntimeAdmissionGeneration,
    decision: c.CandidateDecision | c.ActivationDecision,
    owner: dict[str, Any],
) -> None:
    if not isinstance(request.body, c.IssuerRetire):
        raise Refusal("INVALID_BODY")
    c.check_generation_decision(generation, decision)
    require(
        request.scope == generation.binding.scope
        and request.epoch == owner["epoch"]
        and request.run_id == owner["run_id"],
        "STALE_EPOCH",
    )
    require(
        request.body.generation_id == generation.binding.generation_id
        and request.body.expected_db_revision == generation.revision,
        "STALE_GENERATION",
    )
    require(generation.binding.epoch <= request.epoch, "STALE_EPOCH")


def partition(control_scope_id: str) -> str:
    scalar("Uuid", control_scope_id)
    return "D7#" + control_scope_id


def key(
    control_scope_id: str,
    kind: str,
    identity: str | int | None = None,
    *,
    ordinal: int | None = None,
    revision: int | None = None,
    resource_kind: str | None = None,
) -> dict[str, str]:
    pk = partition(control_scope_id)
    require(kind in {"ROOT", "INTENT", "OUTCOME", "GENERATION", "RESOURCE", "PROOF", "OWNERCLAIM"})
    if kind == "ROOT":
        require(identity is None and ordinal is None and revision is None and resource_kind is None)
        return {"PK": pk, "SK": "ROOT"}
    if kind in {"INTENT", "OUTCOME", "OWNERCLAIM"}:
        scalar("Uuid", identity)
    elif kind == "GENERATION":
        scalar("Positive", identity)
    else:
        scalar("Sha256", identity)
    sk = kind + "#" + str(identity)
    if kind == "RESOURCE":
        require(
            resource_kind in {"role", "secret", "task_definition", "task", "service", "task_set", "resource"}
        )
        sk = "RESOURCE#" + str(resource_kind) + "#" + str(identity)
    else:
        require(resource_kind is None)
    if ordinal is not None:
        require(kind in {"INTENT", "PROOF"} and type(ordinal) is int and 0 <= ordinal < MAX_CHUNKS)
        sk += "#BODY#" + f"{ordinal:08d}"
    if revision is not None:
        require(kind == "OUTCOME")
        scalar("Positive", revision)
        sk += "#" + str(revision)
    require((kind == "OUTCOME") == (revision is not None))
    return {"PK": pk, "SK": sk}


@dataclass(frozen=True, slots=True)
class ChunkSet:
    wire: bytes
    chunks: tuple[bytes, ...]
    hashes: tuple[str, ...]

    def __post_init__(self) -> None:
        parse_wire(self.wire)
        require(type(self.chunks) is tuple and type(self.hashes) is tuple)
        expected = tuple(self.wire[i : i + CHUNK_BYTES] for i in range(0, len(self.wire), CHUNK_BYTES))
        require(self.chunks == expected and 1 <= len(expected) <= MAX_CHUNKS)
        require(self.hashes == tuple(digest(b) for b in self.chunks))

    @classmethod
    def split(cls, wire: bytes) -> ChunkSet:
        parts = tuple(wire[i : i + CHUNK_BYTES] for i in range(0, len(wire), CHUNK_BYTES))
        return cls(wire, parts, tuple(digest(p) for p in parts))

    @classmethod
    def assemble(
        cls, chunks: list[dict[str, Any]], expected_hashes: list[str], expected_digest: str
    ) -> ChunkSet:
        require(1 <= len(chunks) == len(expected_hashes) <= MAX_CHUNKS)
        parts = []
        for ordinal, row in enumerate(chunks):
            exact(row, "ordinal bytes chunk_sha256")
            require(row["ordinal"] == ordinal)
            b = row["bytes"]
            require(type(b) is bytes and 0 < len(b) <= CHUNK_BYTES)
            require(digest(b) == row["chunk_sha256"] == expected_hashes[ordinal])
            parts.append(b)
        wire = b"".join(parts)
        require(digest(wire) == expected_digest)
        return cls(wire, tuple(parts), tuple(expected_hashes))


RECOVERY_STOP_FIELDS = (
    "protocol action external_operation_id scope logical_run_id epoch owner_subject "
    "origin_external_operation_id origin_intent_sha256 generation_id generation_core_sha256 "
    "decision_sha256 managed_resource_sha256 expected_precondition expected_root_sha256 "
    "current_owner_claim_sha256 request request_bytes request_sha256 idempotency_class client_token"
)


@dataclass(frozen=True, slots=True)
class RecoveryStopTaskIntent:
    """ROOT-accepted storage-only recovery variant; never a re-stamped stage1 intent."""

    wire: bytes

    def __post_init__(self) -> None:
        value = exact(parse_wire(self.wire), RECOVERY_STOP_FIELDS)
        _scalars(value)
        require(value["protocol"] == "maezo.provisioning-recovery-stop.v1" and value["action"] == "StopTask")
        require(value["idempotency_class"] == "SINGLE_DISPATCH_RECONCILE" and value["client_token"] is None)
        for field in ("external_operation_id", "origin_external_operation_id", "logical_run_id"):
            scalar("Uuid", value[field])
        require(value["external_operation_id"] != value["origin_external_operation_id"], "REQUEST_CONFLICT")
        typed("ExpectedPrecondition", value["expected_precondition"])
        request = typed("StopTaskProviderRequest", value["request"])
        require(
            request.canonical_bytes() == decode64(value["request_bytes"])
            and request.digest() == value["request_sha256"]
        )

    def to_wire(self) -> dict[str, Any]:
        return exact(parse_wire(self.wire), RECOVERY_STOP_FIELDS)

    def canonical_bytes(self) -> bytes:
        return self.wire

    def digest(self) -> str:
        return digest(self.wire)

    @property
    def scope(self) -> c.Scope:
        return cast(c.Scope, typed("Scope", self.to_wire()["scope"]))

    @property
    def external_operation_id(self) -> str:
        return cast(str, self.to_wire()["external_operation_id"])

    @property
    def logical_run_id(self) -> str:
        return cast(str, self.to_wire()["logical_run_id"])

    @property
    def epoch(self) -> int:
        return cast(int, self.to_wire()["epoch"])

    @property
    def target_generation(self) -> int:
        return cast(int, self.to_wire()["generation_id"])

    @property
    def action(self) -> str:
        return "StopTask"

    @property
    def request(self) -> c.StopTaskProviderRequest:
        return cast(c.StopTaskProviderRequest, typed("StopTaskProviderRequest", self.to_wire()["request"]))


def parse_intent(wire: bytes) -> c.RunTaskIntent | c.StopTaskIntent | RecoveryStopTaskIntent:
    value = parse_wire(wire)
    if value.get("protocol") == "maezo.provisioning-recovery-stop.v1":
        return RecoveryStopTaskIntent(wire)
    return cast(c.RunTaskIntent | c.StopTaskIntent, c.parse("AdmittedExternalIntent", wire))

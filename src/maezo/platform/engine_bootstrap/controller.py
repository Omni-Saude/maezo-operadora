"""D7 controller transitions, signed observation verification and sticky recovery.

State changes are pure conditional proposals for the durable DynamoDB adapter. The
separate qualified stage verifier owns external readiness/disposal/owner evidence;
absence refuses. This module never starts tasks, discovers credentials or opens DBs.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from maezo.platform.engine_bootstrap import controller_contracts as c
from maezo.platform.engine_bootstrap.controller_storage import (
    MAX_INVENTORY,
    Document,
    RecoveryStopTaskIntent,
    Refusal,
    canonical,
    decode64,
    digest,
    encode64,
    exact,
    parse_intent,
    parse_wire,
    present,
    require,
    same,
    scalar,
    validate_claim,
)

STATES = (
    "REVIEWED",
    "LEASED",
    "START_FENCED",
    "DB_FENCED",
    "MIGRATION_VERIFIED",
    "DEPLOY_ADMITTED",
    "DEPLOY_COMMITTED",
    "BOUNDARY_FROZEN",
    "GRANT_ADMITTED",
    "GRANT_COMMITTED",
    "PROVISIONER_DISPOSED",
    "CANDIDATE_ADMITTED",
    "CANDIDATE_VALIDATED",
    "CANDIDATE_RETIRED",
    "ACTIVATION_DECIDED",
    "RESTORING",
    "RESTORED",
    "READINESS_VERIFIED",
    "ACTIVE",
)
_TERMINAL = {"SETTLED", "REJECTED", "CANCELLED_UNSENT"}
_SEAL = object()


@dataclass(frozen=True, slots=True)
class ObservationPair:
    control: c.ControlObservation
    database: c.DatabaseObservation
    observed_at_ms: int
    deadline_ms: int
    profile_sha256: str
    _seal: object

    def __post_init__(self) -> None:
        require(self._seal is _SEAL, "AUTH_REFUSED")

    def digest(self) -> str:
        return digest(
            canonical(
                {
                    "control_observation_sha256": self.control.digest(),
                    "database_observation_sha256": self.database.digest(),
                }
            )
        )

    def current(self, root: Document, now_ms: int) -> None:
        require(type(root) is Document and root.kind == "Root")
        r = root.value()
        scalar("UInt", now_ms)
        require(self.observed_at_ms <= now_ms < min(self.deadline_ms, r["lease_deadline_ms"]), "STALE_EPOCH")
        for observation in (self.control, self.database):
            same(
                observation.to_wire(),
                r,
                "scope run_id epoch candidate_sha256 trust_profile_sha256",
                "STALE_EPOCH",
            )
        payload = self.control.payload
        require(
            payload.operation_record_revision == r["revision"]
            and payload.journal_revision == r["journal_revision"]
            and payload.pending_index_sha256 == r["pending_index_sha256"],
            "PRECONDITION_MISMATCH",
        )
        require(payload.controller_state == r["state"], "PRECONDITION_MISMATCH")
        require(self.database.payload.fence.epoch == r["epoch"], "STALE_EPOCH")
        require(self.database.payload.fence.owner_run_id == r["run_id"], "STALE_EPOCH")


@dataclass(frozen=True, slots=True)
class ObservationVerifier:
    """Owner-pinned public profile; signatures and current challenge are checked locally."""

    profile: c.TrustProfile
    bounds: c.InventoryBounds
    expected_profile_sha256: str

    def __post_init__(self) -> None:
        require(type(self.profile) is c.TrustProfile and type(self.bounds) is c.InventoryBounds)
        require(self.profile.digest() == self.expected_profile_sha256, "AUTH_REFUSED")
        require(self.bounds.digest() == self.profile.inventory_bounds_sha256, "AUTH_REFUSED")

    def verify(
        self,
        control: c.ControlObservation,
        database: c.DatabaseObservation,
        *,
        challenge: str,
        request_sha256: str,
        now_ms: int,
        deadline_ms: int,
    ) -> ObservationPair:
        require(type(control) is c.ControlObservation and type(database) is c.DatabaseObservation)
        scalar("Challenge", challenge)
        scalar("Sha256", request_sha256)
        scalar("UInt", now_ms)
        require(
            now_ms < deadline_ms <= now_ms + self.profile.limits.observer_request_deadline_ms, "UNAVAILABLE"
        )
        for observation, keys in (
            (control, self.profile.control_keys),
            (database, self.profile.database_keys),
        ):
            c.check_observation_profile(observation, self.profile, self.bounds)
            require(
                observation.challenge == challenge and observation.request_sha256 == request_sha256,
                "AUTH_REFUSED",
            )
            require(observation.issued_at_ms <= now_ms < observation.expires_at_ms, "UNAVAILABLE")
            matches = [key for key in keys if key.key_id == observation.key_id]
            require(len(matches) == 1, "AUTH_REFUSED")
            key = matches[0]
            require(
                key.lifecycle == "ACTIVE"
                and key.revoked_at_ms is None
                and key.not_before_ms <= now_ms < key.expires_at_ms,
                "AUTH_REFUSED",
            )
            try:
                Ed25519PublicKey.from_public_bytes(decode64(key.public_key)).verify(
                    decode64(observation.signature), c.signature_input(observation)
                )
            except (InvalidSignature, ValueError, TypeError):
                raise Refusal("AUTH_REFUSED") from None
        same(
            control.to_wire(),
            database.to_wire(),
            "scope run_id epoch candidate_sha256 request_sha256 trust_profile_sha256",
            "AUTH_REFUSED",
        )
        require(
            abs(control.issued_at_ms - database.issued_at_ms)
            <= self.profile.limits.max_clock_disagreement_ms,
            "UNAVAILABLE",
        )
        return ObservationPair(
            control,
            database,
            now_ms,
            min(deadline_ms, control.expires_at_ms, database.expires_at_ms),
            self.expected_profile_sha256,
            _SEAL,
        )


class StageQualification(Protocol):
    """Existing independently qualified external owner/readiness/provisioner boundary.

    Implementations must authenticate retained full evidence for this exact state,
    root and observation pair. There is no default acceptor or bool wire carrier.
    Offline fakes test call binding only, never actual qualification.
    """

    def check(
        self,
        *,
        target: str,
        root_wire: bytes,
        observation_pair_sha256: str,
        evidence_wire: bytes,
        deadline_ms: int,
    ) -> None: ...


class JournalQualification(Protocol):
    """Independent authenticated dispatcher/resource boundary; parsing is insufficient."""

    def check(
        self, *, operation: str, root_wire: bytes, journal_wire: bytes, evidence_wire: bytes, deadline_ms: int
    ) -> None: ...


@dataclass(frozen=True, slots=True)
class JournalUpdate:
    expected: c.JournalObservation
    replacement: c.JournalObservation

    def __post_init__(self) -> None:
        require(type(self.expected) is type(self.replacement) is c.JournalObservation)
        same(
            self.expected.to_wire(),
            self.replacement.to_wire(),
            "external_operation_id logical_run_id epoch generation_id intent_sha256 action",
        )
        require(self.expected.state not in _TERMINAL, "PRECONDITION_MISMATCH")
        allowed = {
            "INTENT": {"RESERVED", "CANCELLED_UNSENT"},
            "RESERVED": {"DISPATCHED_UNKNOWN", "ACKNOWLEDGED", "RECONCILING", "SETTLED"},
            "DISPATCHED_UNKNOWN": {"DISPATCHED_UNKNOWN", "ACKNOWLEDGED", "RECONCILING", "SETTLED"},
            "ACKNOWLEDGED": {"DISPATCHED_UNKNOWN", "ACKNOWLEDGED", "RECONCILING", "SETTLED"},
            "RECONCILING": {"DISPATCHED_UNKNOWN", "ACKNOWLEDGED", "RECONCILING", "SETTLED"},
        }
        require(self.replacement.state in allowed[self.expected.state])
        if self.expected.reservation is not None:
            require(self.replacement.reservation == self.expected.reservation, "REQUEST_CONFLICT")
        if self.expected.provider_outcome is not None:
            require(self.replacement.provider_outcome == self.expected.provider_outcome, "REQUEST_CONFLICT")


@dataclass(frozen=True, slots=True)
class DocumentUpdate:
    """Finite generation/resource CAS; immutable identity is retained on every update."""

    key: str
    expected_wire: bytes | None
    replacement_wire: bytes

    def __post_init__(self) -> None:
        after = parse_wire(self.replacement_wire)
        before = parse_wire(self.expected_wire) if self.expected_wire is not None else None
        if self.key.startswith("GENERATION#"):
            before = present(before)
            exact(before, "scope run_id epoch generation_id state core decision tombstone")
            exact(after, "scope run_id epoch generation_id state core decision_sha256 tombstone")
            same(before, after, "scope run_id epoch generation_id tombstone")
            require(
                before["state"] == "RESERVED"
                and before["core"] is before["decision"] is None
                and before["tombstone"] is True
            )
            require(after["state"] == "BOUND" and self.key == "GENERATION#" + str(after["generation_id"]))
            core = c.parse("GenerationCore", canonical(after["core"]))
            require(
                core.scope.to_wire() == after["scope"]
                and core.epoch == after["epoch"]
                and core.generation_id == after["generation_id"]
            )
            scalar("Sha256", after["decision_sha256"])
        else:
            require(self.key.startswith("RESOURCE#task#"))
            resource = c.parse("ManagedResource", self.replacement_wire)
            identity = {
                "scope": resource.scope.to_wire(),
                "resource_kind": "task",
                "provider_resource_id": resource.provider_resource_id,
            }
            require(self.key == "RESOURCE#task#" + digest(canonical(identity)))
            if before is not None:
                c.parse("ManagedResource", present(self.expected_wire))
                same(
                    before,
                    after,
                    (
                        "scope generation_id external_operation_id provider_resource_id "
                        "task_definition_revision image_sha256 config_set_sha256"
                    ),
                )
                if before["retired_terminal_proof_sha256"] is not None:
                    require(before == after, "REQUEST_CONFLICT")


@dataclass(frozen=True, slots=True)
class Transition:
    expected: Document
    replacement: Document
    append: tuple[tuple[str, bytes], ...] = ()
    journals: tuple[JournalUpdate, ...] = ()
    documents: tuple[DocumentUpdate, ...] = ()

    def __post_init__(self) -> None:
        require(self.expected.kind == self.replacement.kind == "Root")
        before, after = self.expected.value(), self.replacement.value()
        same(before, after, "scope control_scope_id")
        require(after["revision"] == before["revision"] + 1)
        require(
            after["epoch"] >= before["epoch"] and after["next_generation_id"] >= before["next_generation_id"]
        )
        require(type(self.append) is tuple and len(self.append) <= 10)
        require(len({k for k, _ in self.append}) == len(self.append))
        require(type(self.documents) is tuple and len(self.documents) <= 10)
        if len(self.documents) > 8:
            require(
                len(self.journals) == len(self.append) == 1
                and self.journals[0].replacement.state == "SETTLED"
                and self.append[0]
                == (
                    "OUTCOME#"
                    + self.journals[0].replacement.external_operation_id
                    + "#"
                    + str(after["journal_revision"]),
                    self.journals[0].replacement.canonical_bytes(),
                )
                and all(d.key.startswith("RESOURCE#task#") for d in self.documents)
            )
        require(len({d.key for d in self.documents}) == len(self.documents))
        require(type(self.journals) is tuple and len(self.journals) <= 1)
        if self.journals:
            require(after["journal_revision"] == before["journal_revision"] + 1)
        for key, wire in self.append:
            require(
                type(key) is str
                and key.split("#")[0]
                in {"INTENT", "OUTCOME", "GENERATION", "RESOURCE", "PROOF", "OWNERCLAIM"}
            )
            parse_wire(wire)


def _replacement(root: Document, **changes: Any) -> Document:
    value = root.value()
    value.update(changes)
    value["revision"] = root.value()["revision"] + 1
    return Document.create("Root", value)


def recover(root: Document, reason: c.RefusalCode) -> Transition:
    scalar("RefusalCode", reason)
    return Transition(
        root,
        _replacement(
            root,
            state="RECOVERY_REQUIRED",
            current_generation_id=None,
            activation_decision_sha256=None,
            restore_receipt_sha256=None,
            readiness_result_sha256=None,
        ),
    )


def renew_lease(root: Document, owner: dict[str, Any], now_ms: int) -> Transition:
    r = root.value()
    same(r, owner, "owner_subject run_id epoch", "STALE_EPOCH")
    scalar("UInt", now_ms)
    require(now_ms < r["lease_deadline_ms"], "STALE_EPOCH")
    require(r["state"] != "RECOVERY_REQUIRED", "PENDING_UNKNOWN")
    return Transition(root, _replacement(root, lease_deadline_ms=now_ms + 120_000))


def claim_owner(
    root: Document, successor: dict[str, Any], *, claim_id: str, table_identity_sha256: str, now_ms: int
) -> Transition:
    r = root.value()
    exact(successor, "owner_subject run_id epoch")
    scalar("Id", successor["owner_subject"])
    scalar("Uuid", successor["run_id"])
    scalar("Positive", successor["epoch"])
    scalar("Uuid", claim_id)
    scalar("Sha256", table_identity_sha256)
    scalar("UInt", now_ms)
    require(now_ms > r["lease_deadline_ms"] and successor["epoch"] > r["epoch"], "STALE_EPOCH")
    request = {
        "scope": r["scope"],
        "claim_id": claim_id,
        "expected_root_sha256": root.digest(),
        "successor": successor,
        "table_identity_sha256": table_identity_sha256,
    }
    claim = {
        "protocol": "maezo.d7-owner-claim.v1",
        "control_scope_id": r["control_scope_id"],
        "scope": r["scope"],
        "table_identity_sha256": table_identity_sha256,
        "claim_id": claim_id,
        "previous_claim_sha256": r["current_owner_claim_sha256"],
        "previous_owner": {k: r[k] for k in ("owner_subject", "run_id", "epoch")},
        "successor_owner": successor,
        "previous_root_revision": r["revision"],
        "committed_root_revision": r["revision"] + 1,
        "previous_lease_deadline_ms": r["lease_deadline_ms"],
        "claim_observed_at_ms": now_ms,
        "next_generation_id": r["next_generation_id"],
        "journal_revision": r["journal_revision"],
        "pending_index_sha256": r["pending_index_sha256"],
        "managed_registry_sha256": r["managed_registry_sha256"],
        "retired_index_sha256": r["retired_index_sha256"],
        "request_sha256": digest(canonical(request)),
    }
    validate_claim(claim)
    wire = canonical(claim)
    return Transition(
        root,
        _replacement(
            root,
            **successor,
            state="RECOVERY_REQUIRED",
            lease_deadline_ms=now_ms + 120_000,
            current_generation_id=None,
            activation_decision_sha256=None,
            restore_receipt_sha256=None,
            readiness_result_sha256=None,
            current_owner_claim_sha256=digest(wire),
        ),
        (("OWNERCLAIM#" + claim_id, wire),),
    )


def reserve_generation(root: Document, owner: dict[str, Any], now_ms: int) -> tuple[int, Transition]:
    r = root.value()
    same(r, owner, "owner_subject run_id epoch", "STALE_EPOCH")
    require(now_ms < r["lease_deadline_ms"], "STALE_EPOCH")
    require(
        r["restore_state"] == "RECONCILED" and r["state"] not in {"ACTIVE", "RECOVERY_REQUIRED"},
        "PRECONDITION_MISMATCH",
    )
    allocated = r["next_generation_id"]
    scalar("Positive", allocated + 1)
    record = {
        "scope": r["scope"],
        "run_id": r["run_id"],
        "epoch": r["epoch"],
        "generation_id": allocated,
        "state": "RESERVED",
        "core": None,
        "decision": None,
        "tombstone": True,
    }
    return allocated, Transition(
        root,
        _replacement(root, next_generation_id=allocated + 1),
        (("GENERATION#" + str(allocated), canonical(record)),),
    )


def publish_generation(
    root: Document,
    reserved_wire: bytes,
    generation: c.RuntimeAdmissionGeneration,
    decision: c.CandidateDecision | c.ActivationDecision,
    issuer: c.IssuerReceipt,
    pair: ObservationPair,
    now_ms: int,
    *,
    qualification: StageQualification | None,
) -> Transition:
    pair.current(root, now_ms)
    r = root.value()
    reserved = parse_wire(reserved_wire)
    c.check_generation_decision(generation, decision)
    same(decision.to_wire(), r, "scope run_id epoch trust_profile_sha256", "STALE_EPOCH")
    require(
        [
            g
            for g in pair.database.payload.generations
            if g.binding.generation_id == generation.binding.generation_id
        ]
        == [generation],
        "PRECONDITION_MISMATCH",
    )
    require(
        generation.status == "PREPARED"
        and issuer.status == "PREPARED"
        and issuer.binding_sha256 == generation.binding.digest()
        and issuer.activation_decision_sha256 == decision.digest()
        and issuer.generation_id == generation.binding.generation_id
        and issuer.revision == generation.revision
    )
    require(
        issuer.scope == generation.binding.scope
        and issuer.epoch == r["epoch"]
        and issuer.purpose == generation.binding.purpose
    )
    same(reserved, r, "scope run_id epoch")
    require(generation.binding.generation_id == reserved["generation_id"] < r["next_generation_id"])
    qualification = present(qualification, "UNAVAILABLE")
    qualification.check(
        target="PREPARED_GENERATION",
        root_wire=root.wire,
        observation_pair_sha256=pair.digest(),
        evidence_wire=canonical(
            {
                "reservation_bytes": encode64(reserved_wire),
                "generation": generation.to_wire(),
                "decision": decision.to_wire(),
                "issuer_receipt": issuer.to_wire(),
            }
        ),
        deadline_ms=pair.deadline_ms,
    )
    replacement = {k: reserved[k] for k in ("scope", "run_id", "epoch", "generation_id", "tombstone")}
    replacement.update(state="BOUND", core=generation.binding.to_wire(), decision_sha256=decision.digest())
    delta = DocumentUpdate(
        "GENERATION#" + str(generation.binding.generation_id), reserved_wire, canonical(replacement)
    )
    return Transition(
        root, _replacement(root), (("PROOF#" + decision.digest(), decision.canonical_bytes()),), (), (delta,)
    )


def append_intent(
    root: Document,
    intent: c.RunTaskIntent | c.StopTaskIntent,
    pair: ObservationPair,
    now_ms: int,
    *,
    generation: c.RuntimeAdmissionGeneration,
    decision: c.CandidateDecision | c.ActivationDecision,
    managed_resource: c.ManagedResource | None = None,
) -> Transition:
    pair.current(root, now_ms)
    r = root.value()
    c.check_intent_binding(
        intent, generation=generation, decision=decision, managed_resource=managed_resource
    )
    require(type(intent) in {c.RunTaskIntent, c.StopTaskIntent})
    require(
        intent.scope.to_wire() == r["scope"]
        and intent.logical_run_id == r["run_id"]
        and intent.epoch == r["epoch"]
        and intent.owner_subject == r["owner_subject"],
        "STALE_EPOCH",
    )
    require(intent.target_generation < r["next_generation_id"], "STALE_GENERATION")
    expected = intent.expected_precondition
    require(
        expected.controller_revision == r["revision"]
        and expected.journal_revision == r["journal_revision"]
        and expected.pending_index_sha256 == r["pending_index_sha256"]
        and expected.db_fence_revision == pair.database.payload.fence.revision,
        "PRECONDITION_MISMATCH",
    )
    require(
        [g for g in pair.database.payload.generations if g.binding.generation_id == intent.target_generation]
        == [generation],
        "PRECONDITION_MISMATCH",
    )
    if type(intent) is c.StopTaskIntent:
        require(
            managed_resource is not None and managed_resource in pair.control.payload.managed_resources,
            "PRECONDITION_MISMATCH",
        )
        require(present(managed_resource).retired_terminal_proof_sha256 is None, "PRECONDITION_MISMATCH")
    if type(intent) is c.RunTaskIntent:
        require(
            generation.status == "OPEN"
            and intent.target_generation == r["current_generation_id"]
            and decision.digest() == r["activation_decision_sha256"],
            "STALE_GENERATION",
        )
        require(r["state"] in {"CANDIDATE_ADMITTED", "RESTORING"}, "PRECONDITION_MISMATCH")
        require(not r["pending_operation_ids"], "PENDING_UNKNOWN")
    pending = r["pending_operation_ids"]
    require(len(pending) < MAX_INVENTORY and intent.external_operation_id not in pending, "REQUEST_CONFLICT")
    pending = sorted([*pending, intent.external_operation_id])
    return Transition(
        root,
        _replacement(
            root,
            pending_operation_ids=pending,
            pending_index_sha256=digest(canonical(pending)),
            journal_revision=r["journal_revision"] + 1,
        ),
        (("INTENT#" + intent.external_operation_id, intent.canonical_bytes()),),
    )


def append_recovery_stop(
    root: Document,
    intent: RecoveryStopTaskIntent,
    pair: ObservationPair,
    now_ms: int,
    *,
    lineage: Document,
    origin: c.RunTaskIntent | c.StopTaskIntent,
    origin_generation: c.RuntimeAdmissionGeneration,
    decision: c.CandidateDecision | c.ActivationDecision,
    origin_journal: c.JournalObservation,
    managed_resource: c.ManagedResource,
    pending_intents: tuple[bytes, ...],
    qualification: JournalQualification | None,
) -> Transition:
    """New current-owner stop; historical creator, unknown work and stage1 are immutable."""
    pair.current(root, now_ms)
    value, current = intent.to_wire(), root.value()
    require(
        current["state"] == "RECOVERY_REQUIRED" and current["current_generation_id"] is None,
        "PRECONDITION_MISMATCH",
    )
    require(current["restore_state"] == "RECONCILED", "UNAVAILABLE")
    require(
        value["expected_root_sha256"] == root.digest()
        and value["current_owner_claim_sha256"] == current["current_owner_claim_sha256"],
        "PRECONDITION_MISMATCH",
    )
    same(value, current, "scope epoch owner_subject", "STALE_EPOCH")
    require(value["logical_run_id"] == current["run_id"], "STALE_EPOCH")
    require(
        lineage.kind == "OwnerLineageRead" and decode64(lineage.value()["root_bytes"]) == root.wire,
        "AUTH_REFUSED",
    )
    require(now_ms < lineage.value()["deadline_ms"], "UNAVAILABLE")
    precondition = value["expected_precondition"]
    require(
        precondition["controller_revision"] == current["revision"]
        and precondition["journal_revision"] == current["journal_revision"]
        and precondition["pending_index_sha256"] == current["pending_index_sha256"]
        and precondition["db_fence_revision"] == pair.database.payload.fence.revision,
        "PRECONDITION_MISMATCH",
    )
    c.check_intent_binding(
        origin,
        generation=origin_generation,
        decision=decision,
        managed_resource=managed_resource if type(origin) is c.StopTaskIntent else None,
    )
    require(origin.scope == intent.scope == managed_resource.scope, "SCOPE_REFUSED")
    require(
        value["origin_external_operation_id"] == origin.external_operation_id
        and value["origin_intent_sha256"] == origin.digest()
        and value["generation_id"] == origin_generation.binding.generation_id
        and value["generation_core_sha256"] == origin_generation.binding.digest()
        and value["decision_sha256"] == decision.digest()
        and value["managed_resource_sha256"] == managed_resource.digest(),
        "PRECONDITION_MISMATCH",
    )
    require(
        origin_generation.binding.epoch <= current["epoch"]
        and value["generation_id"] < current["next_generation_id"],
        "STALE_GENERATION",
    )
    observed = [
        g for g in pair.database.payload.generations if g.binding.generation_id == value["generation_id"]
    ]
    require(
        len(observed) == 1
        and observed[0].binding == origin_generation.binding
        and observed[0].activation_decision_sha256 == decision.digest()
        and observed[0].status == precondition["target_generation_status"],
        "STALE_GENERATION",
    )
    require(
        managed_resource in pair.control.payload.managed_resources
        and managed_resource.retired_terminal_proof_sha256 is None,
        "PRECONDITION_MISMATCH",
    )
    require(
        intent.request.task == managed_resource.provider_resource_id
        and intent.request.cluster == decision.topology.cluster_arn,
        "SCOPE_REFUSED",
    )
    require(
        precondition["target_inventory_sha256"]
        == digest(canonical([m.to_wire() for m in pair.control.payload.managed_resources])),
        "PRECONDITION_MISMATCH",
    )
    _current_journal(pair, origin_journal)
    require(
        origin_journal.intent_sha256 == origin.digest()
        and origin_journal.external_operation_id == origin.external_operation_id,
        "REQUEST_CONFLICT",
    )
    if type(origin) is c.RunTaskIntent:
        outcome = present(origin_journal.provider_outcome, "PENDING_UNKNOWN")
        require(
            managed_resource.external_operation_id == origin.external_operation_id
            and managed_resource.provider_resource_id in outcome.returned_resource_ids
            and origin_journal.state == "SETTLED",
            "PENDING_UNKNOWN",
        )
    else:
        require(origin_journal.state in _TERMINAL, "PENDING_UNKNOWN")
    pending = [parse_intent(wire) for wire in pending_intents]
    require(
        len({p.external_operation_id for p in pending}) == len(pending)
        and sorted(p.external_operation_id for p in pending) == current["pending_operation_ids"],
        "PENDING_UNKNOWN",
    )
    journals = {j.external_operation_id: j for j in pair.control.payload.journal}
    require(intent.external_operation_id not in journals, "REQUEST_CONFLICT")
    for previous in pending:
        journal = journals.get(previous.external_operation_id)
        require(
            journal is not None
            and journal.intent_sha256 == previous.digest()
            and journal.state not in _TERMINAL,
            "PENDING_UNKNOWN",
        )
        if isinstance(previous, (c.StopTaskIntent, RecoveryStopTaskIntent)):
            require(previous.request.task != intent.request.task, "PENDING_UNKNOWN")
    evidence = canonical(
        {
            "recovery_intent": value,
            "lineage": lineage.value(),
            "origin_intent": origin.to_wire(),
            "origin_generation": origin_generation.to_wire(),
            "decision": decision.to_wire(),
            "managed_resource": managed_resource.to_wire(),
            "observation_pair_sha256": pair.digest(),
            "pending_intents": [parse_wire(wire) for wire in pending_intents],
        }
    )
    _journal_qualified(
        qualification, "append_recovery_stop", root, origin_journal, evidence, pair.deadline_ms
    )
    pending_ids = sorted([*current["pending_operation_ids"], intent.external_operation_id])
    require(len(pending_ids) <= MAX_INVENTORY)
    return Transition(
        root,
        _replacement(
            root,
            pending_operation_ids=pending_ids,
            pending_index_sha256=digest(canonical(pending_ids)),
            journal_revision=current["journal_revision"] + 1,
        ),
        (("INTENT#" + intent.external_operation_id, intent.wire),),
    )


def _current_journal(pair: ObservationPair, journal: c.JournalObservation) -> None:
    matches = [
        entry
        for entry in pair.control.payload.journal
        if entry.external_operation_id == journal.external_operation_id
    ]
    require(len(matches) == 1 and matches[0] == journal, "PRECONDITION_MISMATCH")


def _journal_qualified(
    qualification: JournalQualification | None,
    operation: str,
    root: Document,
    journal: c.JournalObservation,
    evidence: bytes,
    deadline_ms: int,
) -> None:
    qualification = present(qualification, "UNAVAILABLE")
    qualification.check(
        operation=operation,
        root_wire=root.wire,
        journal_wire=journal.canonical_bytes(),
        evidence_wire=evidence,
        deadline_ms=deadline_ms,
    )


def reserve_dispatch(
    root: Document,
    journal: c.JournalObservation,
    reservation: c.DispatcherReservation,
    pair: ObservationPair,
    now_ms: int,
    *,
    qualification: JournalQualification | None = None,
) -> tuple[c.JournalObservation, Transition]:
    pair.current(root, now_ms)
    r = root.value()
    _current_journal(pair, journal)
    _journal_qualified(
        qualification, "reserve_dispatch", root, journal, reservation.canonical_bytes(), pair.deadline_ms
    )
    require(
        journal.external_operation_id in r["pending_operation_ids"] and journal.state == "INTENT",
        "PRECONDITION_MISMATCH",
    )
    require(journal.epoch == r["epoch"] and journal.logical_run_id == r["run_id"], "STALE_EPOCH")
    value = journal.to_wire()
    value.update(state="RESERVED", reservation=reservation.to_wire())
    updated = c.parse("JournalObservation", canonical(value))
    return updated, Transition(
        root,
        _replacement(root, journal_revision=r["journal_revision"] + 1),
        (
            (
                "OUTCOME#" + journal.external_operation_id + "#" + str(r["journal_revision"] + 1),
                updated.canonical_bytes(),
            ),
        ),
        (JournalUpdate(journal, updated),),
    )


def record_outcome(
    root: Document,
    journal: c.JournalObservation,
    outcome: c.ProviderOutcome,
    *,
    revision: int,
    now_ms: int,
    qualification: JournalQualification | None = None,
) -> Transition:
    """Retain original result even from a stale dispatcher; adoption stays recovery-only."""
    r = root.value()
    scalar("Positive", revision)
    scalar("UInt", now_ms)
    require(revision == r["journal_revision"] + 1)
    _journal_qualified(
        qualification, "record_outcome", root, journal, outcome.canonical_bytes(), now_ms + 5000
    )
    require(journal.external_operation_id in r["pending_operation_ids"], "PRECONDITION_MISMATCH")
    require(
        journal.state in {"RESERVED", "DISPATCHED_UNKNOWN", "ACKNOWLEDGED", "RECONCILING"},
        "PRECONDITION_MISMATCH",
    )
    current_acknowledged = (
        journal.state in {"RESERVED", "ACKNOWLEDGED"}
        and journal.action == "RunTask"
        and journal.epoch == r["epoch"]
        and journal.logical_run_id == r["run_id"]
        and journal.generation_id == r["current_generation_id"]
        and r["state"] not in {"RECOVERY_REQUIRED", "ACTIVE"}
        and now_ms < r["lease_deadline_ms"]
        and bool(outcome.returned_resource_ids)
        and not outcome.failures
    )
    value = journal.to_wire()
    value.update(
        state="ACKNOWLEDGED" if current_acknowledged else "DISPATCHED_UNKNOWN",
        provider_outcome=outcome.to_wire(),
    )
    updated = c.parse("JournalObservation", canonical(value))
    closed = (
        _replacement(root, journal_revision=revision)
        if current_acknowledged
        else _replacement(
            root,
            state="RECOVERY_REQUIRED",
            current_generation_id=None,
            activation_decision_sha256=None,
            restore_receipt_sha256=None,
            readiness_result_sha256=None,
            journal_revision=revision,
        )
    )
    return Transition(
        root,
        closed,
        (("OUTCOME#" + journal.external_operation_id + "#" + str(revision), updated.canonical_bytes()),),
        (JournalUpdate(journal, updated),),
    )


def settle(
    root: Document,
    journal: c.JournalObservation,
    settlement: c.Settlement,
    pair: ObservationPair,
    now_ms: int,
    managed: tuple[c.ManagedResource, ...],
    *,
    qualification: JournalQualification | None = None,
    recovery_intent: RecoveryStopTaskIntent | None = None,
) -> tuple[c.JournalObservation, Transition]:
    pair.current(root, now_ms)
    r = root.value()
    require(journal.external_operation_id in r["pending_operation_ids"], "PRECONDITION_MISMATCH")
    _current_journal(pair, journal)
    settlement_evidence = settlement.canonical_bytes()
    if recovery_intent is not None:
        settlement_evidence = canonical(
            {
                "settlement": settlement.to_wire(),
                "recovery_intent": recovery_intent.to_wire(),
                "managed_preimage": [m.to_wire() for m in pair.control.payload.managed_resources],
                "managed_replacement": [m.to_wire() for m in managed],
                "observation_pair_sha256": pair.digest(),
            }
        )
    _journal_qualified(qualification, "settle", root, journal, settlement_evidence, pair.deadline_ms)
    require(journal.state not in _TERMINAL and journal.reservation is not None, "PRECONDITION_MISMATCH")
    require(
        settlement.dispatch_terminal.invocation_id == present(journal.reservation).invocation_id
        and settlement.dispatch_terminal.intent_sha256 == journal.intent_sha256,
        "PRECONDITION_MISMATCH",
    )
    previous = {m.provider_resource_id: m for m in pair.control.payload.managed_resources}
    current = {m.provider_resource_id: m for m in managed}
    require([m.provider_resource_id for m in managed] == sorted(current), "INVALID_BODY")
    require(
        len(current) == len(managed) <= MAX_INVENTORY and set(previous) <= set(current), "PENDING_UNKNOWN"
    )
    for resource_id, old in previous.items():
        same(
            old.to_wire(),
            current[resource_id].to_wire(),
            (
                "scope provider_resource_id generation_id external_operation_id "
                "task_definition_revision image_sha256 config_set_sha256"
            ),
        )
        if old.generation_id != journal.generation_id:
            require(old == current[resource_id], "PENDING_UNKNOWN")
    if recovery_intent is not None:
        require(
            recovery_intent.digest() == journal.intent_sha256
            and recovery_intent.external_operation_id == journal.external_operation_id
            and settlement.settlement_class == "RETIRED_RESOURCES_TERMINAL",
            "PRECONDITION_MISMATCH",
        )
        affected = tuple(m for m in managed if m.provider_resource_id == recovery_intent.request.task)
        require(
            len(affected) == 1 and affected[0].generation_id == journal.generation_id, "PRECONDITION_MISMATCH"
        )
        require(
            all(m == previous.get(m.provider_resource_id) for m in managed if m not in affected),
            "PRECONDITION_MISMATCH",
        )
    else:
        affected = tuple(m for m in managed if m.generation_id == journal.generation_id)
    if settlement.settlement_class == "CURRENT_LIVE_TRACKED":
        require(journal.epoch == r["epoch"] and journal.logical_run_id == r["run_id"], "STALE_EPOCH")
        require(journal.state == "ACKNOWLEDGED" and r["state"] != "RECOVERY_REQUIRED", "PENDING_UNKNOWN")
        require(
            bool(affected)
            and journal.generation_id == r["current_generation_id"]
            and all(c.live_ready(pair.control.payload, m) for m in affected)
            and all(m.external_operation_id == journal.external_operation_id for m in affected)
            and journal.provider_outcome is not None
            and set(journal.provider_outcome.returned_resource_ids)
            == {m.provider_resource_id for m in affected},
            "PRECONDITION_MISMATCH",
        )
    else:
        require(
            all(
                m.current_observed_state in {"STOPPED", "ABSENT"}
                and m.retired_terminal_proof_sha256 is not None
                for m in affected
            ),
            "PENDING_UNKNOWN",
        )
    if recovery_intent is not None:
        for resource in affected:
            proofs = [p for p in settlement.resource_proofs if p.resource_id == resource.provider_resource_id]
            require(
                len(proofs) == 1
                and proofs[0].generation_id == resource.generation_id
                and proofs[0].terminal_kind == "TASK_STOPPED"
                and resource.retired_terminal_proof_sha256 == proofs[0].digest(),
                "PENDING_UNKNOWN",
            )
    deltas = []
    for resource in managed:
        previous_resource = previous.get(resource.provider_resource_id)
        if previous_resource == resource:
            continue
        identity = {
            "scope": resource.scope.to_wire(),
            "resource_kind": "task",
            "provider_resource_id": resource.provider_resource_id,
        }
        deltas.append(
            DocumentUpdate(
                "RESOURCE#task#" + digest(canonical(identity)),
                previous_resource.canonical_bytes() if previous_resource else None,
                resource.canonical_bytes(),
            )
        )
    registry = digest(canonical([m.to_wire() for m in managed]))
    require(registry == settlement.managed_resource_registry_sha256)
    value = journal.to_wire()
    value.update(state="SETTLED", settlement=settlement.to_wire())
    updated = c.parse("JournalObservation", canonical(value))
    projection = pair.control.payload.to_wire()
    projection["journal"] = [
        updated.to_wire() if j.external_operation_id == journal.external_operation_id else j.to_wire()
        for j in pair.control.payload.journal
    ]
    projection["managed_resources"] = [m.to_wire() for m in managed]
    c.parse("ControlPayload", canonical(projection))
    pending = [p for p in r["pending_operation_ids"] if p != journal.external_operation_id]
    next_root = _replacement(
        root,
        pending_operation_ids=pending,
        pending_index_sha256=digest(canonical(pending)),
        journal_revision=r["journal_revision"] + 1,
        managed_registry_sha256=registry,
    )
    return updated, Transition(
        root,
        next_root,
        (
            (
                "OUTCOME#" + journal.external_operation_id + "#" + str(r["journal_revision"] + 1),
                updated.canonical_bytes(),
            ),
        ),
        (JournalUpdate(journal, updated),),
        tuple(deltas),
    )


def cancel_unsent(
    root: Document,
    journal: c.JournalObservation,
    proof: c.DispatchTerminalProof,
    pair: ObservationPair,
    now_ms: int,
    *,
    qualification: JournalQualification | None = None,
) -> tuple[c.JournalObservation, Transition]:
    pair.current(root, now_ms)
    r = root.value()
    _current_journal(pair, journal)
    _journal_qualified(
        qualification, "cancel_unsent", root, journal, proof.canonical_bytes(), pair.deadline_ms
    )
    require(
        journal.state == "INTENT"
        and journal.reservation is None
        and journal.external_operation_id in r["pending_operation_ids"],
        "PENDING_UNKNOWN",
    )
    require(proof.status == "CANCELLED_BEFORE_SEND" and proof.intent_sha256 == journal.intent_sha256)
    value = journal.to_wire()
    value.update(state="CANCELLED_UNSENT", cancellation_unsent_proof=proof.to_wire())
    updated = c.parse("JournalObservation", canonical(value))
    pending = [p for p in r["pending_operation_ids"] if p != journal.external_operation_id]
    return updated, Transition(
        root,
        _replacement(
            root,
            pending_operation_ids=pending,
            pending_index_sha256=digest(canonical(pending)),
            journal_revision=r["journal_revision"] + 1,
        ),
        (
            (
                "OUTCOME#" + journal.external_operation_id + "#" + str(r["journal_revision"] + 1),
                updated.canonical_bytes(),
            ),
        ),
        (JournalUpdate(journal, updated),),
    )


def _closed_observations(pair: ObservationPair) -> None:
    control, database = pair.control.payload, pair.database.payload
    require(all(j.state in _TERMINAL for j in control.journal), "PENDING_UNKNOWN")
    require(
        not control.live_dispatchers and not any(p.enabled for p in control.start_paths), "PENDING_UNKNOWN"
    )
    require(control.desired_count == control.running_count == control.pending_count == 0, "PENDING_UNKNOWN")
    require(not database.sessions and not database.prepared_transactions, "PENDING_UNKNOWN")
    require(
        all(c.task_terminal_witness(control, m) for m in control.managed_resources),
        "PENDING_UNKNOWN",
    )
    require(not any(r.enabled or r.in_flight_admissions for r in database.routes), "PENDING_UNKNOWN")
    require(not any(role.owned and role.can_login for role in database.roles), "PENDING_UNKNOWN")
    require(database.fence.state in {"CLOSED", "RECOVERY_REQUIRED"}, "PRECONDITION_MISMATCH")


def complete_initial_reconciliation(
    root: Document,
    owner: dict[str, Any],
    pair: ObservationPair,
    *,
    now_ms: int,
    evidence_wire: bytes,
    qualification: StageQualification | None,
) -> Transition:
    """Apply authenticated empty-install reconcile/close receipts under exact ROOT CAS.

    The qualifier authenticates committed SQL receipts, full initial installation,
    retained control-table inventory and actual F restore/highwater/catalog closure.
    Signed stage1 F observations do not carry restore_state or highwater; the full
    readback is therefore separately qualified, never inferred from a signature.
    """
    from maezo.platform.engine_bootstrap.controller_postgres_storage import result_bytes
    from maezo.platform.engine_bootstrap.controller_storage import validate_store_call

    r = root.value()
    same(r, owner, "owner_subject run_id epoch", "STALE_EPOCH")
    pair.current(root, now_ms)
    require(r["state"] == "REVIEWED" and r["restore_state"] == "UNRECONCILED")
    require(r["epoch"] == r["revision"] == r["next_generation_id"] == 1)
    require(r["db_fence_epoch"] == r["journal_revision"] == 0)
    require(
        not r["pending_operation_ids"]
        and all(
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
            r[k] == digest(b"[]")
            for k in ("pending_index_sha256", "managed_registry_sha256", "retired_index_sha256")
        )
    )
    _closed_observations(pair)
    require(not pair.control.payload.journal and not pair.control.payload.managed_resources)
    require(not pair.database.payload.generations and pair.database.payload.fence.state == "CLOSED")
    evidence = parse_wire(evidence_wire)
    exact(
        evidence,
        "protocol root_bytes installation_binding_bytes reconcile_call reconcile_result "
        "close_call close_result current_fence observation_pair_sha256",
    )
    require(evidence["protocol"] == "maezo.d7-initial-reconciliation-evidence.v1")
    require(
        decode64(evidence["root_bytes"]) == root.wire
        and evidence["observation_pair_sha256"] == pair.digest(),
        "PRECONDITION_MISMATCH",
    )
    installation = parse_wire(decode64(evidence["installation_binding_bytes"]))
    exact(
        installation,
        "protocol installation_id control_scope_id component version database_binding "
        "act_schema_name act_schema_oid control_schema_oid controller_table_arn issuer_scopes "
        "role_acl_manifest_sha256 object_manifest_sha256 migration_sha256 cib_abi_sha256",
    )
    require(installation["component"] == "maezo-d7-control" and installation["version"] == 1)
    for name in ("installation_id", "control_scope_id"):
        scalar("Uuid", installation[name])
    for name in ("role_acl_manifest_sha256", "object_manifest_sha256", "migration_sha256", "cib_abi_sha256"):
        scalar("Sha256", installation[name])
    scalar("Arn", installation["controller_table_arn"])
    for name in ("act_schema_oid", "control_schema_oid"):
        scalar("Oid", installation[name])
    scalar("Id", installation["act_schema_name"])
    require(
        installation["protocol"] == "maezo.d7-installation-binding.v1"
        and installation["control_scope_id"] == r["control_scope_id"]
        and installation["database_binding"] == r["scope"]["database_binding"]
        and r["scope"] in installation["issuer_scopes"],
        "SCOPE_REFUSED",
    )
    fence = evidence["current_fence"]
    exact(
        fence,
        "scope owner_subject run_id epoch revision next_generation_id current_generation_id "
        "mode restore_state installation_binding_sha256",
    )
    same(fence, r, "scope owner_subject run_id epoch", "STALE_EPOCH")
    require(
        fence["mode"] == "CLOSED"
        and fence["restore_state"] == "RECONCILED"
        and fence["next_generation_id"] == 1
        and fence["current_generation_id"] is None
        and fence["revision"] == pair.database.payload.fence.revision
        and fence["installation_binding_sha256"] == digest(decode64(evidence["installation_binding_bytes"]))
    )
    receipts = []
    for prefix, operation in (("reconcile", "reconcile_fence"), ("close", "close_runtime_fence")):
        call = validate_store_call(operation, evidence[prefix + "_call"])
        same(call, r, "scope run_id epoch", "STALE_EPOCH")
        result = exact(
            evidence[prefix + "_result"],
            "protocol issuer_operation_id request_sha256 result_sha256 canonical_result_bytes",
        )
        receipt = parse_wire(decode64(result["canonical_result_bytes"]))
        result_bytes(operation, call, result, receipt["issuer_login_oid"])
        require(receipt["generation_id"] is receipt["permit_id"] is None)
        receipts.append(receipt)
    reconcile, close = receipts
    handoff = evidence["reconcile_call"]["proof"]
    lineage = parse_wire(decode64(handoff["owner_lineage_bytes"]))
    require(decode64(lineage["root_bytes"]) == root.wire and lineage["claim_receipt_bytes"] is None)
    require(evidence["reconcile_call"]["expected_fence_revision"] == 1 and reconcile["fence_revision"] == 2)
    request = evidence["reconcile_call"]["request"]
    same(request["expected_owner"], r, "owner_subject run_id epoch", "STALE_EPOCH")
    require(
        request["expected_owner"]["revision"] == request["expected_owner"]["next_generation_id"] == 1
        and request["expected_owner"]["current_generation_id"] is None
    )
    require(
        request["mode"] == "current_owner_reconcile"
        and request["claim_id"] is None
        and request["claim_receipt_sha256"] is None
        and request["next_generation_id"] == 1
    )
    require(reconcile["state"] == "RECOVERY_REQUIRED" and close["state"] == "CLOSED")
    require(
        evidence["close_call"]["expected_fence_revision"] == reconcile["fence_revision"]
        and close["fence_revision"] == reconcile["fence_revision"] + 1 == fence["revision"]
    )
    require(
        reconcile["issuer_operation_id"] != close["issuer_operation_id"]
        and reconcile["recorded_before_commit_at_ms"]
        <= close["recorded_before_commit_at_ms"]
        <= min(pair.control.issued_at_ms, pair.database.issued_at_ms, now_ms)
    )
    present(qualification, "UNAVAILABLE").check(
        target="INITIAL_RECONCILIATION",
        root_wire=root.wire,
        observation_pair_sha256=pair.digest(),
        evidence_wire=evidence_wire,
        deadline_ms=pair.deadline_ms,
    )
    return Transition(
        root,
        _replacement(root, restore_state="RECONCILED", db_fence_epoch=1),
        (("PROOF#" + digest(evidence_wire), evidence_wire),),
    )


def advance(
    root: Document,
    target: str,
    pair: ObservationPair,
    *,
    now_ms: int,
    evidence_wire: bytes,
    qualification: StageQualification | None,
    decision: c.CandidateDecision | c.ActivationDecision | None = None,
    issuer: c.IssuerReceipt | None = None,
    binding: c.RuntimeActivationBinding | None = None,
) -> Transition:
    """Apply one ordered transition only after real observation and external stage qualification.

    Stage2 supplies the reducer and storage. Native provisioner, disposal, readiness,
    owner recovery and caller-specific phase qualification remain separate producers.
    Their missing implementation cannot turn this function into an accepting stub.
    """
    pair.current(root, now_ms)
    r = root.value()
    require(r["state"] in STATES and r["state"] != "ACTIVE", "PENDING_UNKNOWN")
    require(target == STATES[STATES.index(r["state"]) + 1], "PRECONDITION_MISMATCH")
    require(not r["pending_operation_ids"], "PENDING_UNKNOWN")
    facts = parse_wire(evidence_wire)
    exact(
        facts,
        (
            "scope run_id epoch previous_state target_state root_sha256 "
            "observation_pair_sha256 stage_evidence_bytes "
            "stage_evidence_sha256"
        ),
    )
    same(facts, r, "scope run_id epoch")
    require(facts["previous_state"] == r["state"] and facts["target_state"] == target)
    require(facts["root_sha256"] == root.digest() and facts["observation_pair_sha256"] == pair.digest())
    retained = decode64(facts["stage_evidence_bytes"])
    require(digest(retained) == facts["stage_evidence_sha256"])
    parse_wire(retained)
    qualification = present(qualification, "UNAVAILABLE")
    qualification.check(
        target=target,
        root_wire=root.wire,
        observation_pair_sha256=pair.digest(),
        evidence_wire=retained,
        deadline_ms=pair.deadline_ms,
    )
    changes: dict[str, Any] = {"state": target, "db_fence_epoch": pair.database.payload.fence.epoch}
    if target == "START_FENCED":
        require(not any(p.enabled for p in pair.control.payload.start_paths), "PENDING_UNKNOWN")
    if target in {
        "DB_FENCED",
        "MIGRATION_VERIFIED",
        "DEPLOY_ADMITTED",
        "GRANT_ADMITTED",
        "PROVISIONER_DISPOSED",
        "CANDIDATE_RETIRED",
    }:
        _closed_observations(pair)
    if target in {
        "CANDIDATE_ADMITTED",
        "ACTIVATION_DECIDED",
        "RESTORING",
        "RESTORED",
        "READINESS_VERIFIED",
        "ACTIVE",
    }:
        if decision is None:
            raise Refusal("PRECONDITION_MISMATCH")
        same(decision.to_wire(), r, "scope run_id epoch trust_profile_sha256")
        require(decision.generation_core.generation_id < r["next_generation_id"], "STALE_GENERATION")
        require(
            (target == "CANDIDATE_ADMITTED") == (type(decision) is c.CandidateDecision), "PURPOSE_REFUSED"
        )
        changes["activation_decision_sha256"] = decision.digest()
        changes["current_generation_id"] = decision.generation_core.generation_id
    if target in {"RESTORED", "READINESS_VERIFIED", "ACTIVE"}:
        require(type(issuer) is c.IssuerReceipt and type(decision) is c.ActivationDecision)
        issuer = present(issuer)
        if decision is None:
            raise Refusal("INVALID_BODY")
        matches = [g for g in pair.database.payload.generations if g.binding == decision.generation_core]
        require(len(matches) == 1 and matches[0].status == "OPEN", "STALE_GENERATION")
        c.check_generation_decision(matches[0], decision)
        require(
            issuer.scope == decision.scope
            and issuer.epoch == decision.epoch
            and issuer.generation_id == decision.generation_core.generation_id
            and issuer.status == "OPEN"
            and issuer.revision == matches[0].revision
        )
        require(
            issuer.binding_sha256 == decision.generation_core.digest()
            and issuer.activation_decision_sha256 == decision.digest()
        )
        changes["restore_receipt_sha256"] = issuer.digest()
    if target in {"READINESS_VERIFIED", "ACTIVE"}:
        require(all(j.state in _TERMINAL for j in pair.control.payload.journal), "PENDING_UNKNOWN")
        require(
            type(binding) is c.RuntimeActivationBinding
            and type(decision) is c.ActivationDecision
            and type(issuer) is c.IssuerReceipt
        )
        binding, issuer = present(binding), present(issuer)
        if decision is None:
            raise Refusal("INVALID_BODY")
        require(
            binding.scope == decision.scope
            and binding.controller_epoch == r["epoch"]
            and binding.runtime_admission_generation == decision.generation_core.generation_id
        )
        require(
            binding.activation_decision_sha256 == decision.digest()
            and binding.issuer_restore_receipt_sha256 == issuer.digest()
        )
        require(
            binding.journal_revision == r["journal_revision"]
            and binding.settled_journal_sha256 == decision.settled_journal_sha256
        )
        require(
            any(
                m.generation_id == binding.runtime_admission_generation
                for m in pair.control.payload.managed_resources
            )
            and all(
                c.live_ready(pair.control.payload, m)
                if m.generation_id == binding.runtime_admission_generation
                else c.task_terminal_witness(pair.control.payload, m)
                for m in pair.control.payload.managed_resources
            ),
            "PENDING_UNKNOWN",
        )
        changes["readiness_result_sha256"] = binding.readiness_result_sha256
        changes["restore_state"] = "RECONCILED"
    return Transition(root, _replacement(root, **changes))

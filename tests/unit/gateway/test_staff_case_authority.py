"""SC1 synthetic signature controls; no native/database/owner activation claim."""
from base64 import b64encode
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from maezo.gateway.external_cases.models import Identity, Scope, digest, instant
from maezo.gateway.human.auth_profile import Actor
from maezo.gateway.human.read_profile import parse_model, wire
from maezo.gateway.staff_cases.authority import InstalledStaffAuthority, actor_digest, fingerprint
from maezo.gateway.staff_cases.models import FIELDS, MembershipWitness, Proof, StaffCaseError, StaffPublication
from maezo.portal.contracts.models import HumanPrincipal, MembershipBinding
from maezo.portal.engine.profile import ProfileError, canonicalize


def scenario():
    now = datetime(2026, 9, 10, 12, tzinfo=UTC)
    start, end = instant(now - timedelta(seconds=1)), instant(now + timedelta(seconds=30))
    root, issuer, identity_key = [Ed25519PrivateKey.generate() for _ in range(3)]
    scope = Scope(tenant="tenant", environment="test", engine_name="engine", database_incarnation="inc")
    principal = HumanPrincipal(schema_version=1, principal_ref="staff", issuer="https://issuer.test",
        subject="subject", tenant="tenant", membership_revision=7,
        memberships=(MembershipBinding(membership_ref="m", roles=("auditor",), groups=("med",)),),
        session_ref="session", authenticated_at=now, subject_bindings=())
    identity = Identity(upstream_resource_key="guide", case_ref="case_00000000001", process_instance_ref="instance",
        process_definition_id="definition", process_definition_key="SP-OP-AUTH-001",
        process_definition_version="3", process_definition_digest="a" * 64, kind="authorization")

    def proof(key, purpose, statement):
        value = {"schema": "staff-case-proof.v1", "purpose": purpose, "algorithm": "Ed25519",
                 "key_fingerprint": fingerprint(key.public_key()), "issued_at": start, "expires_at": end,
                 "statement_digest": digest(statement)}
        return value | {"signature": b64encode(key.sign(canonicalize(value))).decode()}

    entries = []
    for key, role, source, purpose, projections, operations in (
        (issuer, "case_issuer", "policy", "staff_case_grant", list(FIELDS), ["detail"]),
        (identity_key, "identity_verifier", "membership-source", "membership_current", [], []),
    ):
        entries.append({"entry_ref": role, "role": role, "source_namespace": role, "source_ref": source,
            "key_fingerprint": fingerprint(key.public_key()), "certificate_spki": None,
            "public_key": b64encode(key.public_key().public_bytes(serialization.Encoding.DER,
                            serialization.PublicFormat.SubjectPublicKeyInfo)).decode(),
            "login_role": role, "purposes": [purpose], "projections": projections, "operations": operations,
            "not_before": start, "valid_until": end})
    designation = {"schema": "staff-case-designation.v1", "scope": scope.wire(), "designation_ref": "designation",
        "designation_revision": "1", "expected_previous_revision": "0", "authority_ref": "owner",
        "authority_revision": "2", "entries": entries, "issued_at": start, "valid_until": end, "state": "active"}
    authority = InstalledStaffAuthority.verify(designation_bytes=canonicalize(designation),
        installation_proof=Proof.model_validate(proof(root, "installation", designation)),
        expected_digest=digest(designation), expected_scope=scope, root=root.public_key(),
        revoked_fingerprints=frozenset(), now=now)
    witness = {"schema": "staff-case-membership-witness.v1", "scope": scope.wire(),
        "actor": wire(Actor.from_principal(principal, "staff")), "session_ref": "session",
        "principal_record_revision": "19", "principal_record_digest": "b" * 64,
        "source": {"publisher_ref": "identity-source", "source_ref": "membership-source",
                   "source_revision": "8", "source_digest": "c" * 64, "receipt_ref": "receipt",
                   "observed_at": start, "valid_until": end}, "observed_at": start, "valid_until": end}
    witness["proof"] = proof(identity_key, "membership_current", witness)
    decisions = []
    for projection, fields in FIELDS.items():
        decision = {"decision_ref": projection, "policy_ref": "policy", "policy_revision": "4",
            "policy_digest": "d" * 64, "subject_identity_digest": actor_digest(principal),
            "membership_revision": "7", "resource_identity_digest": digest(identity.wire()),
            "operation": "detail", "projection": projection, "fields": sorted(fields),
            "receipt_ref": projection, "receipt_digest": "e" * 64,
            "observed_at": start, "valid_until": end, "state": "active"}
        decision["decision_proof"] = proof(issuer, "staff_case_grant", decision)
        decisions.append(decision)
    grant = {"grant_ref": "grant", "scope": scope.wire(), "case_ref": identity.case_ref,
        "identity_digest": digest(identity.wire()), "issuer": principal.issuer, "subject": principal.subject,
        "principal_ref": principal.principal_ref, "membership_revision": "7", "audience": "staff",
        "grant_revision": "1", "source_ref": "policy", "source_revision": "1", "decisions": decisions,
        "observed_at": start, "valid_until": end, "state": "active"}
    publication = {"schema": "staff-case-publication.v1", "scope": scope.wire(), "publication_id": "publication",
        "expected_source_revision": "0", "source_ref": "policy", "source_revision": "1", "kind": "case_grant",
        "membership_witness": witness, "payload": grant, "payload_digest": digest(grant),
        "observed_at": start, "valid_until": end}
    publication["proof"] = proof(issuer, "staff_case_grant", publication)
    return authority, principal, identity, witness, publication, now, issuer, proof


def test_independent_issued_grant_keeps_actual_actor_codec_and_full_witness():
    authority, principal, identity, witness, publication, now, _, _ = scenario()
    parsed = parse_model(StaffPublication, publication)
    result = authority.grant(parsed, principal, identity, parse_model(MembershipWitness, witness), now=now,
                             native_revision="19", native_digest="b" * 64)
    assert result.membership_digest == digest(witness)
    assert result.actor_digest == digest(wire(Actor.from_principal(principal, "staff")))
    with pytest.raises(ProfileError):
        digest(Actor.from_principal(principal, "staff").model_dump(mode="json"))
    assert result.fields["staff_current_task.v1"] == FIELDS["staff_current_task.v1"]
    assert result.valid_until == now + timedelta(seconds=30)


@pytest.mark.parametrize("attack", ["tampered_policy", "wrong_role", "revoked", "native_revision",
    "native_digest", "identity", "expired", "narrow_identity"])
def test_staff_grant_does_not_replace_independent_authority(attack):
    authority, principal, identity, witness, publication, now, issuer, proof = scenario()
    native_revision, native_digest = "19", "b" * 64
    if attack == "tampered_policy":
        publication["payload"]["decisions"][0]["receipt_digest"] = "f" * 64
    elif attack == "wrong_role":
        publication["proof"] = witness["proof"]
    elif attack == "revoked":
        authority = replace(authority, revoked_fingerprints=frozenset({fingerprint(issuer.public_key())}))
    elif attack == "native_revision":
        native_revision = "7"  # server membership revision is NOT the native principal revision
    elif attack == "native_digest":
        native_digest = "f" * 64
    elif attack == "identity":
        identity = identity.model_copy(update={"process_instance_ref": "other"})
    elif attack == "expired":
        now += timedelta(seconds=31)
    elif attack == "narrow_identity":
        d = next(d for d in publication["payload"]["decisions"] if d["projection"] == "staff_identity.v1")
        d["fields"] = ["case_ref"]
        d.pop("decision_proof")
        d["decision_proof"] = proof(issuer, "staff_case_grant", d)
        publication["payload_digest"] = digest(publication["payload"])
        publication.pop("proof")
        publication["proof"] = proof(issuer, "staff_case_grant", publication)
    with pytest.raises(StaffCaseError):
        authority.grant(parse_model(StaffPublication, publication), principal, identity,
            parse_model(MembershipWitness, witness), now=now, native_revision=native_revision,
            native_digest=native_digest)

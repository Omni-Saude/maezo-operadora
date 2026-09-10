"""Synthetic W6A wire fixtures; these are not operator authority or PostgreSQL qualification."""

import base64
import hashlib
import json
from datetime import UTC, datetime, timedelta

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from maezo.gateway.external_cases.authority import InstalledAuthority
from maezo.gateway.external_cases.models import (
    CaseSummary,
    CheckpointPacket,
    ExternalCaseError,
    PublicationRequest,
    Scope,
    SourceIngressReceipt,
    SourcePacket,
    digest,
    instant,
    parse,
)
from maezo.portal.engine.profile import canonicalize


def signed(value, key):
    obj = dict(value)
    obj["signature"] = base64.urlsafe_b64encode(key.sign(canonicalize(obj))).rstrip(b"=").decode()
    return obj


def scenario():
    now = datetime(2026, 9, 10, tzinfo=UTC)
    start, end = instant(now - timedelta(minutes=1)), instant(now + timedelta(minutes=2))
    root, signer = Ed25519PrivateKey.generate(), Ed25519PrivateKey.generate()
    spki = signer.public_key().public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    pin = hashlib.sha256(spki).hexdigest()
    scope = {
        "tenant": "tenant-test",
        "environment": "unit",
        "engine_name": "engine-test",
        "database_incarnation": "db-test",
    }
    bundle = {
        "schema": "portal-external-authority-designation.v1",
        "scope": scope,
        "designation_ref": "designation-test",
        "designation_revision": "1",
        "installation_receipt_ref": "installed-test",
        "policy_receipt_ref": "policy-test",
        "policy_receipt_digest": "a" * 64,
        "signers": [
            {
                "fingerprint": pin,
                "public_key_spki_base64": base64.urlsafe_b64encode(spki).rstrip(b"=").decode(),
                "purposes": ["ownership", "disclosure", "completeness"],
                "source_namespaces": ["operator-test"],
                "kinds": ["authorization", "reimbursement", "account"],
                "audiences": ["beneficiary", "provider"],
                "not_before": start,
                "not_after": end,
            }
        ],
        "completeness": {
            "designation_ref": "complete-test",
            "policy_receipt_ref": "policy-test",
            "policy_receipt_digest": "b" * 64,
            "required_namespaces": ["operator-test"],
            "signer_fingerprints": [pin],
            "valid_until": end,
        },
        "observed_at": start,
        "valid_until": end,
    }
    installation = signed(
        {
            "schema": "portal-external-authority-installation.v1",
            "scope": scope,
            "designation_digest": digest(bundle),
            "receipt_ref": "installed-test",
            "observed_at": start,
            "valid_until": end,
        },
        root,
    )
    identity = {
        "upstream_resource_key": "resource-test",
        "case_ref": "case_synthetic_only_0001",
        "process_instance_ref": "process-test",
        "process_definition_id": "definition-test",
        "process_definition_key": "SP-OP-AUTH",
        "process_definition_version": "1",
        "process_definition_digest": "c" * 64,
        "kind": "authorization",
    }
    owner = {"kind": "beneficiary", "resource_ref": "beneficiary-test"}
    grant = {
        "grant_ref": "grant-test",
        "identity": identity,
        "owner": owner,
        "issuer": "issuer-test",
        "subject": "subject-test",
        "principal_ref": "principal-test",
        "membership_revision": "2",
        "audience": "beneficiary",
        "operations": ["list", "detail"],
        "projection_id": "portal-external-case-summary.v1",
        "fields": ["case_ref", "kind", "state", "record_revision", "state_observed_at"],
        "consent_scopes": ["consent-test"],
        "grant_revision": "1",
        "source_revision": "1",
        "decision_receipt_ref": "decision-test",
        "decision_digest": "d" * 64,
        "signer_fingerprint": pin,
        "valid_until": end,
        "state": "active",
    }
    statement = {
        "schema": "portal-external-case-source.v1",
        "scope": scope,
        "source_ref": "source-test",
        "source_namespace": "operator-test",
        "source_revision": "1",
        "identity": identity,
        "owners": [owner],
        "disclosure_grants": [grant],
        "ownership_receipt_ref": "ownership-test",
        "ownership_receipt_digest": "e" * 64,
        "ownership_signer_fingerprint": pin,
        "observed_at": start,
        "valid_until": end,
        "state": "active",
    }

    def proof(payload, purpose):
        return signed(
            {
                "schema": "portal-external-assertion.v1",
                "purpose": purpose,
                "algorithm": "Ed25519",
                "key_fingerprint": pin,
                "issued_at": start,
                "expires_at": end,
                "digest": digest(payload),
            },
            signer,
        )

    packet = {
        "schema": "portal-external-source-packet.v1",
        "statement": statement,
        "ownership_proof": proof(statement, "ownership"),
        "disclosure_proofs": [proof(grant, "disclosure")],
    }
    ingress = {
        "schema": "portal-external-source-ingress-receipt.v1",
        "scope": scope,
        "ingress_id": "ingress-test",
        "request_digest": digest(packet),
        "source_ref": "source-test",
        "source_revision": "1",
        "source_digest": digest(packet),
        "source_generation": "1",
        "upstream_receipts_digest": digest({"ownership": "e" * 64, "disclosure": ["d" * 64]}),
        "committed_at": instant(now),
    }
    empty = {
        "schema": "portal-external-scope-checkpoint.v1",
        "scope": scope,
        "designation_digest": digest(bundle),
        "checkpoint_ref": "checkpoint-test",
        "epoch": "1",
        "predecessor_checkpoint_digest": None,
        "upstream_position": "upstream-test",
        "observed_at": start,
        "valid_until": end,
        "namespace_positions": [{"namespace": "operator-test", "upstream_position": "position-test"}],
        "heads": [],
        "heads_count": "0",
        "heads_digest": digest([]),
    }
    checkpoint = {
        "schema": "portal-external-checkpoint-packet.v1",
        "statement": empty,
        "completeness_proof": proof(empty, "completeness"),
    }
    request = {
        "schema": "portal-external-publication-request.v1",
        "scope": scope,
        "publication_id": "publication-test",
        "requester_fingerprint": "f" * 64,
        "captured_provenance_digest": digest(ingress),
        "kind": "case",
        "packet": packet,
        "ingress_receipt": ingress,
    }
    authority = InstalledAuthority.verify(
        bundle_bytes=canonicalize(bundle),
        receipt_bytes=canonicalize(installation),
        expected_digest=digest(bundle),
        scope=parse(Scope, canonicalize(scope)),
        installation_key=root.public_key(),
        revoked_fingerprints=frozenset(),
        authority_revision="7",
        now=now,
    )
    return locals()


def test_signed_source_and_empty_checkpoint_profiles_round_trip():
    s = scenario()
    for cls, name in (
        (SourcePacket, "packet"),
        (CheckpointPacket, "checkpoint"),
        (SourceIngressReceipt, "ingress"),
        (PublicationRequest, "request"),
    ):
        assert parse(cls, canonicalize(s[name])).canonical() == canonicalize(s[name])


@pytest.mark.parametrize("value", [1, 1.0, True, "01", "-1", str(2**63)])
def test_exact_decimal_revisions(value):
    s = scenario()
    s["packet"]["statement"]["source_revision"] = value
    with pytest.raises(ExternalCaseError):
        parse(SourcePacket, json.dumps(s["packet"], sort_keys=True, separators=(",", ":")).encode())


@pytest.mark.parametrize(
    "field",
    ["source_ref", "source_revision", "source_digest", "source_generation", "upstream_receipts_digest"],
)
def test_publication_binds_original_ingress(field):
    s = scenario()
    receipt = s["request"]["ingress_receipt"]
    receipt[field] = "2" if field in {"source_revision", "source_generation"} else "0" * 64
    s["request"]["captured_provenance_digest"] = digest(receipt)
    if field == "source_generation":
        # Generation is a journal fact; shape cannot independently authenticate the journal.
        parse(PublicationRequest, canonicalize(s["request"]))
    else:
        with pytest.raises(ExternalCaseError):
            parse(PublicationRequest, canonicalize(s["request"]))


@pytest.mark.parametrize("raw", [b'{"tenant":"x","tenant":"y"}', b'{ "tenant":"x"}', b"{}" + b" " * 65536])
def test_duplicate_noncanonical_and_oversize_refuse(raw):
    with pytest.raises(ExternalCaseError):
        parse(Scope, raw)


def test_public_summary_excludes_decision_or_identity_data():
    value = dict(
        case_ref="case_synthetic_only_0001",
        kind="authorization",
        state="ended",
        record_revision="1",
        state_observed_at="2026-09-10T00:00:00.000000Z",
    )
    assert set(CaseSummary(**value).wire()) == set(value)
    with pytest.raises(ExternalCaseError):
        parse(CaseSummary, canonicalize(value | {"decision": "approved"}))


def test_internal_python_alias_is_not_an_external_schema_field():
    s = scenario()
    s["packet"]["schema_"] = s["packet"].pop("schema")
    with pytest.raises(ExternalCaseError):
        parse(SourcePacket, canonicalize(s["packet"]))

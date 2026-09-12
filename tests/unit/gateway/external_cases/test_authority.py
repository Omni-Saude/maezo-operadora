from dataclasses import replace
from datetime import timedelta

import pytest
from tests.unit.gateway.external_cases.test_models import scenario

from maezo.gateway.external_cases.authority import InstalledAuthority
from maezo.gateway.external_cases.models import CheckpointPacket, ExternalCaseError, SourcePacket, parse
from maezo.portal.engine.profile import canonicalize


def test_original_signatures_and_positive_authoritative_empty():
    s = scenario()
    assert s["authority"].source(parse(SourcePacket, canonicalize(s["packet"])), s["now"]) > s["now"]
    assert (
        s["authority"].checkpoint(parse(CheckpointPacket, canonicalize(s["checkpoint"])), s["now"]) > s["now"]
    )


@pytest.mark.parametrize(
    "attack",
    [
        "source_tamper",
        "grant_tamper",
        "ownership_purpose",
        "wrong_namespace",
        "revoked_key",
        "expired",
        "wrong_root",
        "absent_root",
        "checkpoint_namespace",
        "checkpoint_designation",
    ],
)
def test_no_assertion_or_receipt_only_authority(attack):
    s = scenario()
    authority = s["authority"]
    now = s["now"]
    if attack == "source_tamper":
        s["packet"]["statement"]["ownership_receipt_digest"] = "0" * 64
    if attack == "grant_tamper":
        s["packet"]["statement"]["disclosure_grants"][0]["principal_ref"] = "other-principal"
    if attack == "ownership_purpose":
        s["packet"]["ownership_proof"]["purpose"] = "disclosure"
    if attack == "wrong_namespace":
        s["packet"]["statement"]["source_namespace"] = "other-namespace"
    if attack == "revoked_key":
        authority = replace(authority, revoked_fingerprints=frozenset({s["pin"]}))
    if attack == "expired":
        now += timedelta(minutes=3)
    if attack == "checkpoint_namespace":
        s["checkpoint"]["statement"]["namespace_positions"] = []
    if attack == "checkpoint_designation":
        s["checkpoint"]["statement"]["designation_digest"] = "0" * 64
    with pytest.raises(ExternalCaseError):
        if attack in {"wrong_root", "absent_root"}:
            InstalledAuthority.verify(
                bundle_bytes=canonicalize(s["bundle"]),
                receipt_bytes=canonicalize(s["installation"]),
                expected_digest=s["authority"].designation_digest,
                scope=s["authority"].bundle.scope,
                installation_key=None if attack == "absent_root" else s["signer"].public_key(),
                revoked_fingerprints=frozenset(),
                authority_revision="7",
                now=now,
            )
        elif attack.startswith("checkpoint"):
            authority.checkpoint(parse(CheckpointPacket, canonicalize(s["checkpoint"])), now)
        else:
            authority.source(parse(SourcePacket, canonicalize(s["packet"])), now)


def test_empty_requires_complete_namespace_coverage_even_with_valid_signature():
    s = scenario()
    c = s["checkpoint"]
    c["statement"]["namespace_positions"] = []
    c["completeness_proof"] = s["proof"](c["statement"], "completeness")
    with pytest.raises(ExternalCaseError):
        s["authority"].checkpoint(parse(CheckpointPacket, canonicalize(c)), s["now"])

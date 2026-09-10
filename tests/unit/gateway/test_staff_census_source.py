"""Synthetic source signatures, no owner qualification or engine claim."""

from copy import deepcopy
from dataclasses import replace
from types import MappingProxyType

import pytest

from maezo.gateway.human.auth_profile import Actor
from maezo.gateway.human.read_profile import digest, parse_model, wire
from maezo.gateway.staff_cases.census_source import OwnerManifestStaffCensusSource
from maezo.gateway.staff_cases.models import DesignationEntry, MembershipWitness, StaffCaseError
from maezo.gateway.staff_cases.publisher import StaffSigner
from maezo.portal.engine.profile import canonicalize
from tests.unit.gateway.test_staff_case_authority import scenario


def census_fixture(count=1):
    authority, principal, identity, witness, original, now, key, proof = scenario()
    entry = next(e for e in authority.entries.values() if e.role == "case_issuer")
    entry = parse_model(
        DesignationEntry,
        entry.wire()
        | {
            "source_namespace": "policy",
            "purposes": ["staff_case_grant", "staff_policy_head", "scope_complete"],
            "operations": ["detail", "list"],
        },
    )
    authority = replace(
        authority, entries=MappingProxyType(dict(authority.entries) | {entry.key_fingerprint: entry})
    )
    start, end = original["observed_at"], original["valid_until"]
    head = dict(
        schema="staff-policy-head.v1",
        scope=original["scope"],
        policy_ref="policy",
        policy_revision="4",
        policy_digest="d" * 64,
        head_revision="1",
        expected_head_revision="0",
        state="active",
        decision_issuer_key_fingerprint=entry.key_fingerprint,
        decision_purpose="staff_case_grant",
        source_ref="policy",
        source_revision="1",
        observed_at=start,
        valid_until=end,
    )
    head["proof"] = proof(key, "staff_policy_head", head)
    policy = dict(original) | {
        "publication_id": "policy-publication",
        "kind": "policy_head",
        "membership_witness": None,
        "payload": head,
        "payload_digest": digest(head),
    }
    policy.pop("proof")
    policy["proof"] = proof(key, "staff_policy_head", policy)
    grants = []
    for index in range(count):
        grant = deepcopy(original["payload"])
        grant.update(
            grant_ref=f"grant{index:05}", case_ref=f"case_{index:011}", source_revision=str(index + 2)
        )
        decision = deepcopy(grant["decisions"][0])
        decision.update(
            operation="list",
            projection="staff_summary.v1",
            fields=["case_ref", "kind", "state", "opened_at", "updated_at"],
        )
        # Existing actual fields, not a parallel guessed summary set.
        from maezo.gateway.staff_cases.models import FIELDS

        decision["fields"] = sorted(FIELDS["staff_summary.v1"])
        decision.pop("decision_proof")
        decision["decision_proof"] = proof(key, "staff_case_grant", decision)
        grant["decisions"] = [decision]
        grants.append(grant)
    cut = dict(
        schema="staff-case-census-source.v1",
        scope=original["scope"],
        source_ref="policy",
        cut_ref="cut",
        cut_revision="1",
        predecessor_cut_digest=None,
        actor=wire(Actor.from_principal(principal, "staff")),
        policy_scope=head,
        checkpoint_ref="checkpoint",
        generation="1",
        predecessor_checkpoint_digest=None,
        source_positions=[dict(source_ref="policy", expected_source_revision="0")],
        grants=grants,
        withdrawn_grants=[],
        coverage="complete",
        kind="authorization",
        operations=["list"],
        observed_at=start,
        valid_until=end,
        materials=[dict(request_digest=digest(policy), position="queued", publication=policy, receipt=None)],
        policy_bindings=[dict(policy_ref="policy", request_digest=digest(policy))],
        grant_bindings=[
            dict(grant_ref=g["grant_ref"], mode="compile_new", request_digest=None) for g in grants
        ],
        withdrawal_bindings=[],
        predecessor_material=None,
    )
    cut["proof"] = proof(key, "scope_complete", cut)
    signer = StaffSigner(authority, key, "case_issuer", clock=lambda: now)
    return authority, principal, parse_model(MembershipWitness, witness), cut, now, signer, proof


def source_read(authority, cut, now):
    return OwnerManifestStaffCensusSource(authority, maximum_bytes=67108864, maximum_records=100000).read(
        canonicalize(cut), digest(cut), now=now
    )


@pytest.mark.parametrize("count", [0, 1])
def test_real_signature_accepts_complete_explicit_scope(count):
    authority, _, _, cut, now, _, _ = census_fixture(count)
    assert len(source_read(authority, cut, now).grants) == count


@pytest.mark.parametrize(
    "attack",
    ["missing_policy", "missing_original", "duplicate", "partial", "membership", "policy_revision", "extra"],
)
def test_closed_source_dependency_refusals(attack):
    authority, _, _, cut, now, signer, proof = census_fixture()
    if attack == "missing_policy":
        cut["policy_bindings"] = []
    elif attack == "missing_original":
        cut["materials"] = []
    elif attack == "duplicate":
        cut["grants"] *= 2
    elif attack == "partial":
        cut["coverage"] = "partial"
    elif attack == "membership":
        cut["grants"][0]["membership_revision"] = "8"
    elif attack == "policy_revision":
        cut["grants"][0]["decisions"][0]["policy_revision"] = "5"
    else:
        cut["unreviewed"] = True
    cut.pop("proof")
    cut["proof"] = proof(signer.key, "scope_complete", cut)
    with pytest.raises((ValueError, StaffCaseError)):
        source_read(authority, cut, now)


def test_whole_cut_signature_is_not_a_parser_flag():
    authority, _, _, cut, now, _, _ = census_fixture()
    cut["cut_ref"] = "other"
    with pytest.raises(StaffCaseError):
        source_read(authority, cut, now)


@pytest.mark.parametrize("raw", [b'{"x":"a","x":"b"}', b'{"x":1}', b'{"x":NaN}', b'{"x":"\\ud800"}'])
def test_large_artifact_parser_keeps_strict_native_grammar(raw):
    from maezo.gateway.staff_cases.census_source import artifact_loads

    with pytest.raises((ValueError, StaffCaseError)):
        artifact_loads(raw, 1024)


def test_administration_config_roundtrip_uses_existing_typed_profile(tmp_path):
    from maezo.gateway.staff_cases.census_production import CensusProductionConfiguration
    from tests.unit.gateway.test_staff_production_materials import material_fixture

    settings, manifest, files = material_fixture()
    retained = "/var/maezo-census-retained"
    protected = dict(path="/var/maezo-census-materials/input", sha256="a" * 64)
    dependencies = dict(
        schema="staff-case-census-dependencies.v1",
        scope=manifest["scope"],
        installation_digest=settings.staff_designation_sha256,
        native_configuration_digest=settings.staff_native_configuration_sha256,
        cut_digest="a" * 64,
        sources=[
            dict(
                source_ref="policy",
                issuers=[
                    dict(
                        entry_ref="issuer",
                        source_namespace="policy",
                        key_fingerprint="b" * 64,
                        purposes=["scope_complete"],
                        signing_key=None,
                    )
                ],
                importer_entry_ref="importer",
                importer_key_fingerprint="c" * 64,
                importer_signing_key=protected,
                importer_certificate=protected,
                importer_tls_key=protected,
            )
        ],
    )
    config = CensusProductionConfiguration.model_validate(
        dict(
            schema="staff-case-census-administration.v1",
            dependencies=dependencies,
            witness_profile=settings,
            identity_settings=protected,
            session_secret=protected,
            source_cut=dict(path=retained + "/cut", sha256="a" * 64),
            material_root="/var/maezo-census-materials",
            retained_source_root=retained,
            custody_registration_ref="owner-custody",
            custody_registration_digest="d" * 64,
            output_plan_path=retained + "/plan",
            input_plan=None,
            maximum_source_bytes=67108864,
            maximum_plan_bytes=134217728,
            maximum_records=100000,
            maximum_seconds=5,
        )
    )
    assert parse_model(CensusProductionConfiguration, config.wire()) == config

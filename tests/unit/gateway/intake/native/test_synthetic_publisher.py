"""The labelled synthetic seeder: it refuses without its marker and labels what it writes.

No engine, no PostgreSQL, no AMH. The publication control flow under test is the real
`AuthInputPublisher`; only the journal and the native client are in-test doubles, which is
the same arrangement `test_publisher.py:107-126` uses for the same publisher.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from maezo.gateway.human.auth_profile import (
    Actor,
    DocumentPolicy,
    GuideIdentity,
    InputPublication,
    PublicationQuery,
    ResourceAuthority,
    Scope,
    StartFacts,
)
from maezo.gateway.human.auth_publisher import AuthInputPublisher
from maezo.gateway.human.auth_transport import AuthUnavailableError
from maezo.gateway.human.read_profile import (
    ArtifactPin,
    MembershipProjection,
    SourceProvenance,
    digest,
    wire,
)
from maezo.gateway.intake.links import SYNTHETIC_PUBLISHER_REF
from maezo.portal.contracts.models import MembershipBinding, SubjectBinding
from tests.unit.gateway.intake.native.test_publisher import Client, Journal

ROOT = Path(__file__).parents[5]
SCRIPT = ROOT / "scripts/dev/publish_auth_synthetic_inputs.py"
NOW = datetime(2026, 9, 12, 12, tzinfo=UTC)
UNTIL = NOW + timedelta(hours=6)
HASH = "a" * 64
TENANT = "tenant-synthetic"
BENEFICIARY = "beneficiary_synthetic_00001"
PROVIDER = "provider_synthetic_000001"
GUIDE = "guide_synthetic_0000000001"
INTAKE = "intake_synthetic_000000001"
PRINCIPAL = "human-synthetic-1"
ISSUER = "https://cognito-idp.sa-east-1.amazonaws.com/sa-east-1_TestPool"
SUBJECT = "00000000-0000-4000-8000-000000000001"


def seeder() -> Any:
    """Load the seeder as a module; dataclasses need it registered in sys.modules."""
    spec = importlib.util.spec_from_file_location("maezo_synthetic_seeder", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


SEEDER = seeder()


def scope() -> Scope:
    return Scope(
        tenant=TENANT,
        environment="dev",
        engine_name="engine",
        database_incarnation="db",
        installation_ref="installed",
        installation_revision=1,
    )


def provenance(publisher: str = SYNTHETIC_PUBLISHER_REF) -> SourceProvenance:
    return SourceProvenance(
        publisher_ref=publisher,
        source_ref="synthetic-fixture-source",
        source_revision=1,
        source_digest=HASH,
        receipt_ref="synthetic-receipt",
        observed_at=NOW,
        valid_until=UNTIL,
    )


def actor() -> Actor:
    return Actor(
        principal_ref=PRINCIPAL,
        issuer=ISSUER,
        subject=SUBJECT,
        membership_revision=1,
        audience="provider",
    )


def membership_projection() -> MembershipProjection:
    return MembershipProjection(
        principal_ref=PRINCIPAL,
        issuer=ISSUER,
        subject=SUBJECT,
        membership_revision=1,
        audience="provider",
        memberships=(MembershipBinding(membership_ref="m-1", roles=("provider",), groups=()),),
        subject_bindings=(SubjectBinding(kind="provider", resource_ref=PROVIDER),),
        state="active",
        reviewed_until=UNTIL,
    )


def authority(publisher: str = SYNTHETIC_PUBLISHER_REF) -> ResourceAuthority:
    return ResourceAuthority(
        authority_ref="authority-synthetic-1",
        actor=actor(),
        beneficiary_ref=BENEFICIARY,
        provider_ref=PROVIDER,
        resource_kind="guide",
        resource_ref=GUIDE,
        action="auth.start",
        request_ref=None,
        relationship_revision=1,
        consent_revision=1,
        grant_ref="grant-synthetic-1",
        basis_ref="basis-synthetic-1",
        legal_basis="other_qualified_basis",
        consent_state="not_required",
        state="active",
        valid_from=NOW - timedelta(hours=1),
        valid_until=UNTIL,
        source=provenance(publisher),
    )


def guide() -> GuideIdentity:
    return GuideIdentity(
        guide_identity_ref=GUIDE,
        source_ref="synthetic-fixture-source",
        namespace_ref="synthetic-namespace",
        source_guide_ref="synthetic-guide-source",
        cutover_ref="synthetic-cutover",
        cutover_revision=1,
        legacy_state="absent_at_cutover",
        prior_instance_id=None,
        prior_case_ref=None,
        source=provenance(),
    )


def start_facts() -> StartFacts:
    return StartFacts(
        facts_ref="facts-synthetic-1",
        intake_ref=INTAKE,
        guide_identity_ref=GUIDE,
        beneficiary_pseudo_id=BENEFICIARY,
        provider_ref=PROVIDER,
        procedure_code="10101012",
        category="consulta",
        character="eletivo",
        claimed_amount_cents=12345,
        document_refs=(),
        requer_autorizacao=True,
        beneficiario_ativo=True,
        carencia_cumprida=True,
        documentacao_completa=True,
        missing_requirement_codes=(),
        documentary_assessment_ref="assessment-synthetic-1",
        request_digest=HASH,
        factual_sources=(provenance(),),
        policy_artifacts=(ArtifactPin(artifact_ref="policy-synthetic-1", digest=HASH),),
    )


def document_policy() -> DocumentPolicy:
    return DocumentPolicy(
        assessment_ref="assessment-synthetic-1",
        resource_kind="intake",
        resource_ref=INTAKE,
        request_ref=None,
        request_revision=0,
        policy=ArtifactPin(artifact_ref="policy-synthetic-1", digest=HASH),
        policy_revision=1,
        recipient_principal_refs=(PRINCIPAL,),
        required_codes=(),
        missing_codes=(),
        submitted_response_digest=None,
        effective_document_refs=(),
        document_set_digest=digest(()),
        complete=True,
        source=provenance(),
        valid_until=UNTIL,
    )


PAYLOADS = {
    "actor": (PRINCIPAL, membership_projection),
    "resource_authority": (GUIDE, authority),
    "guide": (GUIDE, guide),
    "start_facts": (INTAKE, start_facts),
    "document_policy": (INTAKE, document_policy),
}


def fixture_document(**changes: Any) -> dict[str, Any]:
    document: dict[str, Any] = {
        "synthetic": True,
        "label": "SYNTHETIC development fixture — not a real vínculo and not a clinical fact",
        "scope": wire(scope()),
        "workload_ref": "synthetic-workload",
        "source_revision": 1,
        "valid_until": UNTIL.isoformat(),
        "inputs": [
            {"kind": kind, "resource_ref": resource, "expected_generation": 0, "payload": wire(build())}
            for kind, (resource, build) in PAYLOADS.items()
        ],
    }
    document.update(changes)
    return document


def write(tmp_path: Path, name: str, document: Any) -> Path:
    path = tmp_path / name
    path.write_text(json.dumps(document))
    return path


def marker_document(**changes: Any) -> dict[str, Any]:
    document: dict[str, Any] = {
        "marker": SEEDER.MARKER_STRING,
        "tenant": TENANT,
        "source_ref": "synthetic-fixture-source",
        "acknowledged": True,
    }
    document.update(changes)
    return document


@pytest.fixture
def seeded(tmp_path: Path) -> tuple[Path, Path, dict[str, str]]:
    marker = write(tmp_path, "marker.json", marker_document())
    fixture = write(tmp_path, "fixture.json", fixture_document())
    return marker, fixture, {SEEDER.MARKER_VARIABLE: str(marker)}


def test_refuses_without_explicit_synthetic_marker(seeded) -> None:
    _marker, fixture, _env = seeded
    for environ in ({}, {SEEDER.MARKER_VARIABLE: ""}, {SEEDER.MARKER_VARIABLE: "   "}):
        with pytest.raises(SEEDER.SyntheticRefusalError):
            SEEDER.require_marker(TENANT, environ)
    # rc != 0 and, because the refusal precedes every composition, zero publications.
    assert SEEDER.main(["--tenant", TENANT, "--fixture", str(fixture), "--dry-run"], {}) == 2


def test_marker_is_material_not_a_mode(tmp_path: Path) -> None:
    import ast

    # The seeder must never consult the deployment mode to decide whether to publish.
    # Scanned over the AST, so the prose that *explains* the rule cannot satisfy it.
    tree = ast.parse(SCRIPT.read_text())
    assert not [n for n in ast.walk(tree) if isinstance(n, ast.Attribute) and n.attr == "mode"]
    assert not [
        n for n in ast.walk(tree) if isinstance(n, ast.Constant) and n.value in ("production", "local-test")
    ]
    for change, expected in (
        ({"marker": "something-else"}, "marker string"),
        ({"acknowledged": False}, "acknowledgement"),
        ({"acknowledged": "true"}, "acknowledgement"),
        ({"tenant": "another-tenant"}, "authorises tenant"),
        ({"source_ref": ""}, "source reference"),
    ):
        path = write(tmp_path, f"marker-{abs(hash(str(change)))}.json", marker_document(**change))
        with pytest.raises(SEEDER.SyntheticRefusalError) as refusal:
            SEEDER.require_marker(TENANT, {SEEDER.MARKER_VARIABLE: str(path)})
        assert expected in str(refusal.value)


def test_fixture_must_be_declared_synthetic_and_complete(tmp_path: Path, seeded) -> None:
    marker_path, _fixture, _env = seeded
    marker = SEEDER.require_marker(TENANT, {SEEDER.MARKER_VARIABLE: str(marker_path)})
    for name, change in (
        ("unlabelled", {"synthetic": False}),
        ("no-kinds", {"inputs": []}),
        ("wrong-tenant", {"scope": wire(scope().model_copy(update={"tenant": "other"}))}),
    ):
        path = write(tmp_path, name + ".json", fixture_document(**change))
        with pytest.raises(SEEDER.SyntheticRefusalError):
            SEEDER.load_fixture(path, marker)
    # A missing kind is refused: a partial seed would leave facts citing absent authority.
    partial = fixture_document()
    partial["inputs"] = partial["inputs"][:4]
    with pytest.raises(SEEDER.SyntheticRefusalError):
        SEEDER.load_fixture(write(tmp_path, "partial.json", partial), marker)


def test_every_kind_carries_synthetic_provenance(seeded) -> None:
    marker_path, fixture_path, env = seeded
    marker = SEEDER.require_marker(TENANT, env)
    fixture = SEEDER.load_fixture(fixture_path, marker)
    snapshots = SEEDER.build_snapshots(fixture, marker, NOW)
    assert tuple(s.publication.kind for s in snapshots) == SEEDER.KINDS
    assert len(snapshots) == 5
    for snapshot in snapshots:
        publication = snapshot.publication
        assert publication.source.publisher_ref == SYNTHETIC_PUBLISHER_REF
        assert publication.source.source_ref == marker.source_ref
        assert publication.source.source_digest == fixture.digest
        assert publication.state == "active" and publication.payload is not None
        assert publication.payload_digest == digest(publication.payload)
        assert publication.valid_until <= publication.source.valid_until
    assert str(marker_path)  # marker path is material, never an inferred location


@pytest.mark.asyncio
async def test_publication_is_idempotent_with_the_real_publisher(seeded) -> None:
    _marker_path, fixture_path, env = seeded
    marker = SEEDER.require_marker(TENANT, env)
    fixture = SEEDER.load_fixture(fixture_path, marker)
    snapshots = SEEDER.build_snapshots(fixture, marker, NOW)
    authority_snapshot = next(s for s in snapshots if s.publication.kind == "resource_authority")
    journal, client = Journal(), Client()
    publisher = AuthInputPublisher(client=client, journal=journal, clock=lambda: NOW)
    first = await publisher.publish(authority_snapshot)
    second = await publisher.publish(authority_snapshot)
    # Exactly the `test_publisher.py:107-113` contract: query first, send once, replay after.
    assert first == second
    assert isinstance(client.sent[0], PublicationQuery)
    assert client.sent[1] == authority_snapshot.publication
    assert len(client.sent) == 2
    assert journal.events == ["freeze", "persist-ack", "freeze"]


@pytest.mark.asyncio
async def test_publication_identity_is_stable_across_runs(seeded, tmp_path: Path) -> None:
    _marker_path, fixture_path, env = seeded
    marker = SEEDER.require_marker(TENANT, env)
    fixture = SEEDER.load_fixture(fixture_path, marker)
    first = SEEDER.build_snapshots(fixture, marker, NOW)
    later = SEEDER.build_snapshots(fixture, marker, NOW + timedelta(minutes=5))
    assert [s.publication.publication_id for s in first] == [s.publication.publication_id for s in later]
    # A changed fixture is a different source and therefore a different publication.
    changed = fixture_document()
    changed["inputs"][0]["resource_ref"] = PRINCIPAL + "-other"
    other = SEEDER.load_fixture(write(tmp_path, "changed.json", changed), marker)
    assert SEEDER.build_snapshots(other, marker, NOW)[0].publication.publication_id != (
        first[0].publication.publication_id
    )


@pytest.mark.asyncio
async def test_removing_the_marker_mid_run_stops_publication(seeded) -> None:
    marker_path, fixture_path, env = seeded
    marker = SEEDER.require_marker(TENANT, env)
    fixture = SEEDER.load_fixture(fixture_path, marker)
    snapshots = SEEDER.build_snapshots(fixture, marker, NOW)
    marker_path.unlink()
    journal, client = Journal(), Client()
    publisher = AuthInputPublisher(client=client, journal=journal, clock=lambda: NOW)
    with pytest.raises(AuthUnavailableError):
        await publisher.publish(snapshots[0])
    assert client.sent == [] and journal.saved is None


def test_expired_fixture_publishes_nothing(seeded) -> None:
    _marker_path, fixture_path, env = seeded
    marker = SEEDER.require_marker(TENANT, env)
    fixture = SEEDER.load_fixture(fixture_path, marker)
    with pytest.raises(SEEDER.SyntheticRefusalError):
        SEEDER.build_snapshots(fixture, marker, UNTIL)


def test_dry_run_publishes_nothing_and_labels_every_line(seeded, capsys) -> None:
    _marker_path, fixture_path, env = seeded
    assert SEEDER.main(["--tenant", TENANT, "--fixture", str(fixture_path), "--dry-run"], env) == 0
    lines = capsys.readouterr().out.strip().splitlines()
    assert len(lines) == 5 and all(line.endswith("SYNTHETIC") for line in lines)


def test_publication_payloads_are_the_real_closed_models(seeded) -> None:
    _marker_path, fixture_path, env = seeded
    marker = SEEDER.require_marker(TENANT, env)
    fixture = SEEDER.load_fixture(fixture_path, marker)
    types = {
        s.publication.kind: type(s.publication.payload) for s in SEEDER.build_snapshots(fixture, marker, NOW)
    }
    assert types == {
        "actor": MembershipProjection,
        "resource_authority": ResourceAuthority,
        "guide": GuideIdentity,
        "start_facts": StartFacts,
        "document_policy": DocumentPolicy,
    }
    assert all(
        isinstance(s.publication, InputPublication) for s in SEEDER.build_snapshots(fixture, marker, NOW)
    )

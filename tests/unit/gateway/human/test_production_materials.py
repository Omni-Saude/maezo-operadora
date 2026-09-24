"""WP-J1-00 — the human deployment material bundle and the providers built from it.

These tests exercise the PRODUCTION providers (`read_materials`, `command_materials`)
against a complete, valid material bundle. No provider is substituted: the only thing
the builder supplies is deployment material, which is what an operator supplies too.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime

import pytest
from tests.support.materials_builder import (
    ASSIGNMENT_WORKLOAD,
    WORKLOAD,
    build_bundle,
    build_materials,
    pin_for,
)

from maezo.gateway.human.command_materials import (
    assignment_scope,
    assignment_signing_lease,
    install_command_credential,
)
from maezo.gateway.human.credentials import HumanCommandCredentialPartition
from maezo.gateway.human.errors import GatewayRefusalError
from maezo.gateway.human.models import Scope
from maezo.gateway.human.production_materials import (
    FILES,
    KEY_FILES,
    PRIVATE_FILES,
    PUBLIC_FILES,
    HumanMaterialError,
    HumanMaterialPin,
    HumanPublicManifest,
    load_human_materials,
    verify_materials,
)
from maezo.gateway.human.queue import ReadRefusalError
from maezo.gateway.human.read_materials import MaterialLifetime, read_providers
from maezo.gateway.human.read_profile import parse_model, wire


def test_read_command_and_assignment_keys_are_distinct_and_purpose_bound(tmp_path):
    """T00-07 — three purposes never share a key id, key material or workload."""
    materials = build_materials(tmp_path / "current")
    manifest = materials.manifest

    purposes = [key.purpose for key in manifest.keys]
    assert sorted(purposes) == sorted(KEY_FILES)
    assert len({key.key_id for key in manifest.keys}) == 3
    assert len({key.fingerprint for key in manifest.keys}) == 3
    raw = {purpose: materials.private_key(purpose).private_bytes_raw() for purpose in KEY_FILES}
    assert len(set(raw.values())) == 3

    # The assignment read plane runs as its own workload; the native client refuses
    # to share the command workload reference by construction.
    assert manifest.key("human-command").workload_ref == WORKLOAD
    assert manifest.key("human-assignment-read").workload_ref == ASSIGNMENT_WORKLOAD
    assert assignment_scope(materials).workload_ref == ASSIGNMENT_WORKLOAD
    assert assignment_scope(materials) != manifest.scope


async def test_read_credentials_refuse_a_foreign_purpose_or_key_id(tmp_path):
    """A read lease is issued only for the read purposes and the read key id."""
    materials = build_materials(tmp_path / "current")
    lifetime = MaterialLifetime()
    _, credentials, _ = read_providers(materials, lifetime)
    scope = materials.manifest.scope
    read_key_id = materials.manifest.key("portal-task-read").key_id

    lease = await credentials.acquire(scope, "portal-task-read", read_key_id)
    assert lease.purpose == "portal-task-read"
    assert lease.requester.key_id == read_key_id
    assert lease.requester.issuer == scope.workload_ref
    lease.require_current(datetime.now(UTC))

    # The command key may not be borrowed through the read plane.
    with pytest.raises(ReadRefusalError):
        await credentials.acquire(scope, "human-command", read_key_id)
    with pytest.raises(ReadRefusalError):
        await credentials.acquire(scope, "portal-task-read", "kms-human-command-1")
    other = Scope(tenant=scope.tenant, environment=scope.environment, workload_ref="other-workload")
    with pytest.raises(ReadRefusalError):
        await credentials.acquire(other, "portal-task-read", read_key_id)


async def test_read_key_never_signs_a_publication(tmp_path):
    """F8 (C1): the engine trust holds one key per Q2 purpose; the read key is only `portal-task-read`."""
    materials = build_materials(tmp_path / "current")
    _, credentials, _ = read_providers(materials, MaterialLifetime())
    read_key_id = materials.manifest.key("portal-task-read").key_id
    with pytest.raises(ReadRefusalError):
        await credentials.acquire(materials.manifest.scope, "portal-read-publication", read_key_id)


async def test_publication_credentials_use_their_own_key_and_key_id(tmp_path):
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    from maezo.gateway.human.production_materials import fingerprint
    from maezo.gateway.human.read_materials import MaterialPublicationCredentials

    materials = build_materials(tmp_path / "current")
    lifetime = MaterialLifetime()
    scope = materials.manifest.scope
    read = materials.manifest.key("portal-task-read")
    until = datetime.now(UTC).replace(year=datetime.now(UTC).year + 1)
    key = Ed25519PrivateKey.generate()
    own = MaterialPublicationCredentials(
        materials, lifetime, key=key, key_id="publication-1", key_fingerprint=fingerprint(key.public_key()),
        not_after=until,
    )
    lease = await own.acquire(scope, "portal-read-publication", "publication-1")
    assert lease.purpose == "portal-read-publication"
    assert lease.requester.key_id == "publication-1" != read.key_id
    assert lease.requester.public_key_sha256 == fingerprint(key.public_key()) != read.fingerprint
    lease.require_current(datetime.now(UTC))
    with pytest.raises(ReadRefusalError):
        await own.acquire(scope, "portal-task-read", "publication-1")
    with pytest.raises(ReadRefusalError):
        await own.acquire(scope, "portal-read-publication", read.key_id)
    # Reusing the read key or its key_id is refused at construction.
    read_key = materials.private_key("portal-task-read")
    with pytest.raises(ReadRefusalError):
        MaterialPublicationCredentials(
            materials, lifetime, key=read_key, key_id="publication-1", key_fingerprint=read.fingerprint,
            not_after=until,
        )
    with pytest.raises(ReadRefusalError):
        MaterialPublicationCredentials(
            materials, lifetime, key=key, key_id=read.key_id, key_fingerprint=fingerprint(key.public_key()),
            not_after=until,
        )


async def test_closing_the_composition_revokes_every_issued_lease(tmp_path):
    """A lease is a claim that must still hold when exercised, not a decided fact."""
    materials = build_materials(tmp_path / "current")
    lifetime = MaterialLifetime()
    admission, credentials, cursors = read_providers(materials, lifetime)
    scope = materials.manifest.scope
    read_key_id = materials.manifest.key("portal-task-read").key_id

    lease = await credentials.acquire(scope, "portal-task-read", read_key_id)
    admitted = await admission.current(scope, materials.manifest.engine_name)
    keys = await cursors.acquire(scope, materials.manifest.current_cursor().key_id)
    now = datetime.now(UTC)
    lease.require_current(now)
    admitted.require_current(now)
    keys.require_current(now)

    lifetime.close()

    for check in (lease.require_current, admitted.require_current, keys.require_current):
        with pytest.raises(ReadRefusalError):
            check(datetime.now(UTC))
    with pytest.raises(ReadRefusalError):
        await credentials.acquire(scope, "portal-task-read", read_key_id)


async def test_cursor_key_set_is_a_rotation_cohort_with_unique_tags(tmp_path):
    materials = build_materials(tmp_path / "current")
    lifetime = MaterialLifetime()
    _, _, cursors = read_providers(materials, lifetime)
    current = materials.manifest.current_cursor()

    keys = await cursors.acquire(materials.manifest.scope, current.key_id)
    assert keys.current.key_id == current.key_id
    assert len(keys.accepted) == 2
    assert len({lease.key_tag for lease in keys.accepted}) == 2
    assert all(len(lease.key_tag) == 16 for lease in keys.accepted)
    # An unknown cursor key id is refused rather than silently rotated to.
    with pytest.raises(ReadRefusalError):
        await cursors.acquire(materials.manifest.scope, "cursor-unknown")


def test_partition_without_installed_credential_refuses(tmp_path):
    """T00-04 — `for_workload` before `install` is `credential_scope_mismatch`."""
    materials = build_materials(tmp_path / "current")
    scope = materials.manifest.scope

    bare = HumanCommandCredentialPartition(scope)
    with pytest.raises(GatewayRefusalError) as raised:
        bare.for_workload(scope)
    assert str(raised.value) == "credential_scope_mismatch"

    # The production installer is the only thing that makes the partition usable.
    command = install_command_credential(materials, MaterialLifetime())
    assert command.partition.for_workload(scope) == command.credential
    assert command.credential.purpose == "human-command"
    assert command.credential.key_ref.startswith("human-command/")

    # And it stays bound to its own scope.
    foreign = Scope(tenant=scope.tenant, environment=scope.environment, workload_ref="other")
    with pytest.raises(GatewayRefusalError):
        command.partition.for_workload(foreign)


def test_assignment_lease_is_guarded_on_every_signature(tmp_path):
    materials = build_materials(tmp_path / "current")
    lifetime = MaterialLifetime()
    lease = assignment_signing_lease(materials, lifetime)

    assert lease.purpose == "human-assignment-read"
    assert lease.scope.workload_ref == ASSIGNMENT_WORKLOAD
    assert lease.sign(b"payload")

    lifetime.close()
    with pytest.raises(GatewayRefusalError):
        lease.sign(b"payload")


def test_material_bundle_is_complete_or_refused(tmp_path):
    """A half-provisioned bundle never starts the plane."""
    manifest, files = build_bundle(directory=tmp_path / "current")
    assert verify_materials(
        pin_for(manifest), manifest, files, now=datetime.now(UTC), directory=str(tmp_path / "current")
    )

    for name in sorted(FILES):
        broken = dict(files)
        del broken[name]
        with pytest.raises(HumanMaterialError):
            verify_materials(
                pin_for(manifest),
                manifest,
                broken,
                now=datetime.now(UTC),
                directory=str(tmp_path / "current"),
            )


def test_public_file_tampering_is_detected_and_secrets_are_never_hashed_publicly(tmp_path):
    manifest, files = build_bundle(directory=tmp_path / "current")
    assert PRIVATE_FILES and PUBLIC_FILES and not (PRIVATE_FILES & PUBLIC_FILES)

    # Private material has a required null entry: a public digest of a secret would
    # be a dictionary-attack oracle.
    assert all(manifest.files[name] is None for name in PRIVATE_FILES)
    assert all(manifest.files[name] is not None for name in PUBLIC_FILES)

    tampered = dict(files)
    tampered["read-admission.json"] = files["read-admission.json"] + b" "
    with pytest.raises(HumanMaterialError):
        verify_materials(
            pin_for(manifest), manifest, tampered, now=datetime.now(UTC), directory=str(tmp_path / "current")
        )


def test_read_admission_must_carry_the_installation_root_signature(tmp_path):
    """#1 is an attestation, not a controller DTO: an unsigned record is refused."""
    import json

    manifest, files = build_bundle(directory=tmp_path / "current")
    document = json.loads(files["read-admission.json"])
    document["record"]["read_deployment_ref"] = "substituted-deployment"
    forged = json.dumps(document).encode()
    tampered = dict(files)
    tampered["read-admission.json"] = forged
    tampered_manifest = manifest.model_copy(
        update={
            "files": {
                **manifest.files,
                "read-admission.json": hashlib.sha256(forged).hexdigest(),
            }
        }
    )
    with pytest.raises(HumanMaterialError):
        # Pinned to the TAMPERED manifest on purpose: the refusal under test is the
        # forged admission signature, not the out-of-band digest mismatch.
        verify_materials(
            pin_for(tampered_manifest),
            tampered_manifest,
            tampered,
            now=datetime.now(UTC),
            directory=str(tmp_path / "current"),
        )


def test_expired_material_refuses(tmp_path):
    from datetime import timedelta

    manifest, files = build_bundle(directory=tmp_path / "current")
    with pytest.raises(HumanMaterialError):
        verify_materials(
            pin_for(manifest),
            manifest,
            files,
            now=datetime.now(UTC) + timedelta(days=2),
            directory=str(tmp_path / "current"),
        )


def test_loader_accepts_exactly_one_fixed_path():
    """No search path and no configurable directory: one path or nothing."""
    pin = HumanMaterialPin(
        tenant="tenant_j1", material_version_id="j1material" + "0" * 26, public_manifest_sha256="a" * 64
    )
    with pytest.raises(HumanMaterialError):
        load_human_materials(None, pin)
    with pytest.raises(HumanMaterialError):
        load_human_materials("/tmp", pin)
    with pytest.raises(HumanMaterialError):
        load_human_materials("/run/maezo-human-materials/current/..", pin)


def test_a_wholly_substituted_bundle_is_refused_only_by_the_out_of_band_pin(tmp_path):
    """MAJOR-2 — the bundle does not attest itself; configuration says which bundle.

    Every other check in `verify_materials` compares the directory to itself, so an
    adversary who can write it supplies their own root key, their own manifest and
    their own root-signed admission and passes all of them. The second bundle below
    is exactly that: internally perfect, and refused because the deployment is pinned
    to the first one.
    """
    first, first_files = build_bundle(directory=tmp_path / "first")
    second, second_files = build_bundle(directory=tmp_path / "second")
    assert first.root_key_fingerprint != second.root_key_fingerprint

    # The substituted bundle is internally coherent: pinned to itself it verifies.
    assert verify_materials(
        pin_for(second), second, second_files, now=datetime.now(UTC), directory=str(tmp_path / "second")
    )
    # Pinned to the bundle the deployment actually approved, it is refused.
    with pytest.raises(HumanMaterialError):
        verify_materials(
            pin_for(first), second, second_files, now=datetime.now(UTC), directory=str(tmp_path / "second")
        )

    # Each of the three pinned facts is load-bearing on its own.
    anchor = pin_for(first)
    for wrong in (
        HumanMaterialPin("tenant_outro", anchor.material_version_id, anchor.public_manifest_sha256),
        HumanMaterialPin(anchor.tenant, "outro" + "0" * 27, anchor.public_manifest_sha256),
        HumanMaterialPin(anchor.tenant, anchor.material_version_id, "f" * 64),
    ):
        with pytest.raises(HumanMaterialError):
            verify_materials(
                wrong, first, first_files, now=datetime.now(UTC), directory=str(tmp_path / "first")
            )


def test_manifest_digest_pin_covers_every_manifest_field(tmp_path):
    """A single altered manifest fact changes the pinned digest, so nothing is unpinned."""
    manifest, files = build_bundle(directory=tmp_path / "current")
    anchor = pin_for(manifest)
    for update in (
        {"engine_name": "engine-substituted"},
        {"catalog_ref": "catalog-substituted"},
        {"relay_poll_seconds": 2},
        {"issuer": "https://substituted.invalid"},
    ):
        altered = manifest.model_copy(update=update)
        assert hashlib.sha256(altered.canonical()).hexdigest() != anchor.public_manifest_sha256
        with pytest.raises(HumanMaterialError):
            verify_materials(
                anchor, altered, files, now=datetime.now(UTC), directory=str(tmp_path / "current")
            )


def test_revocation_snapshot_refuses_withdrawn_material_and_bounds_every_lease(tmp_path):
    """MAJOR-2 — a withdrawn or unobserved bundle is refused, and leases are clamped."""
    from datetime import timedelta

    # 1. A manifest that attests material its own snapshot declares revoked is not a
    #    manifest at all: it is refused while parsing, for every attested digest.
    live, _ = build_bundle(directory=tmp_path / "probe")
    assert len(live.attested_digests()) >= 7
    for revoked in sorted(live.attested_digests()):
        payload = wire(live)
        payload["revocation_snapshot"] = _snapshot(live, revoked=[revoked])
        with pytest.raises(HumanMaterialError):
            parse_model(HumanPublicManifest, payload)
    # An unrelated withdrawn fingerprint is not this bundle's problem.
    payload = wire(live)
    payload["revocation_snapshot"] = _snapshot(live, revoked=["9" * 64])
    assert parse_model(HumanPublicManifest, payload).revocation_snapshot.revoked_fingerprints

    # 2. A stale observation is refused even while the issue window is open.
    past = datetime.now(UTC) - timedelta(hours=2)
    stale = build_bundle(
        directory=tmp_path / "stale",
        overrides={
            "revocation_snapshot": _snapshot(live, observed_at=past, valid_until=past + timedelta(minutes=1))
        },
    )
    manifest, files = stale
    assert manifest.issued_at <= datetime.now(UTC) < manifest.valid_until
    with pytest.raises(HumanMaterialError):
        verify_materials(
            pin_for(manifest), manifest, files, now=datetime.now(UTC), directory=str(tmp_path / "stale")
        )

    # 3. A snapshot that ends before the manifest shortens every lease minted here.
    soon = datetime.now(UTC) + timedelta(minutes=3)
    materials = build_materials(
        tmp_path / "short",
        overrides={
            "revocation_snapshot": _snapshot(
                live, observed_at=datetime.now(UTC) - timedelta(minutes=1), valid_until=soon
            )
        },
    )
    assert materials.not_after < materials.manifest.valid_until
    assert abs((materials.not_after - soon).total_seconds()) < 1
    lifetime = MaterialLifetime()
    admission, credentials, _ = read_providers(materials, lifetime)
    assert credentials._not_after == materials.not_after
    assert assignment_signing_lease(materials, lifetime).not_after == materials.not_after
    assert install_command_credential(materials, lifetime).signer._until == materials.not_after


def _snapshot(manifest, *, revoked=(), observed_at=None, valid_until=None):
    from datetime import timedelta

    observed_at = observed_at or (datetime.now(UTC) - timedelta(minutes=5))
    valid_until = valid_until or (datetime.now(UTC) + timedelta(hours=6))
    return {
        "scope": {
            "tenant": manifest.scope.tenant,
            "environment": manifest.scope.environment,
            "workload_ref": manifest.scope.workload_ref,
        },
        "source_ref": "revocation-observer-1",
        "revision": "5",
        "observed_at": observed_at.astimezone(UTC).isoformat().replace("+00:00", "Z"),
        "valid_until": valid_until.astimezone(UTC).isoformat().replace("+00:00", "Z"),
        "revoked_fingerprints": sorted(set(revoked)),
    }


def test_command_endpoint_is_the_engine_base_never_the_transport_path(tmp_path):
    """The transport appends `/v1/commands` itself; a base inside `/v1` would double it."""
    manifest, _ = build_bundle(directory=tmp_path / "current")
    assert manifest.command_endpoint == manifest.command_surface.origin
    for endpoint in (
        manifest.command_surface.origin + "/v1/commands",
        manifest.command_surface.origin + "/v1",
        manifest.command_surface.origin + "/",
        manifest.command_surface.origin + "/engine/",
        "https://other.engine.invalid/engine",
        manifest.command_surface.origin + "/engine?x=1",
    ):
        with pytest.raises(HumanMaterialError):
            build_bundle(directory=tmp_path / "bad", overrides={"command_endpoint": endpoint})
    # A path-mounted engine is still expressible.
    mounted, _ = build_bundle(
        directory=tmp_path / "mounted",
        overrides={"command_endpoint": manifest.command_surface.origin + "/engine"},
    )
    assert mounted.command_endpoint.endswith("/engine")

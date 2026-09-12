"""WP-J1-00 — the human deployment material bundle and the providers built from it.

These tests exercise the PRODUCTION providers (`read_materials`, `command_materials`)
against a complete, valid material bundle. No provider is substituted: the only thing
the builder supplies is deployment material, which is what an operator supplies too.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime

import pytest
from tests.unit.gateway.human.materials_builder import (
    ASSIGNMENT_WORKLOAD,
    WORKLOAD,
    build_bundle,
    build_materials,
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
    load_human_materials,
    verify_materials,
)
from maezo.gateway.human.queue import ReadRefusalError
from maezo.gateway.human.read_materials import MaterialLifetime, read_providers


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
    assert verify_materials(manifest, files, now=datetime.now(UTC), directory=str(tmp_path / "current"))

    for name in sorted(FILES):
        broken = dict(files)
        del broken[name]
        with pytest.raises(HumanMaterialError):
            verify_materials(manifest, broken, now=datetime.now(UTC), directory=str(tmp_path / "current"))


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
        verify_materials(manifest, tampered, now=datetime.now(UTC), directory=str(tmp_path / "current"))


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
        verify_materials(
            tampered_manifest, tampered, now=datetime.now(UTC), directory=str(tmp_path / "current")
        )


def test_expired_material_refuses(tmp_path):
    from datetime import timedelta

    manifest, files = build_bundle(directory=tmp_path / "current")
    with pytest.raises(HumanMaterialError):
        verify_materials(
            manifest,
            files,
            now=datetime.now(UTC) + timedelta(days=2),
            directory=str(tmp_path / "current"),
        )


def test_loader_accepts_exactly_one_fixed_path():
    """No search path and no configurable directory: one path or nothing."""
    with pytest.raises(HumanMaterialError):
        load_human_materials(None)
    with pytest.raises(HumanMaterialError):
        load_human_materials("/tmp")
    with pytest.raises(HumanMaterialError):
        load_human_materials("/run/maezo-human-materials/current/..")

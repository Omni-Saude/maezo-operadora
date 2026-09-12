"""WP-J1-06 Phase 0 — the decision ports are bound, so a decision can be submitted.

WP-J1-00 merged with `AssignmentRuntime(decision_ports=…)` unbound (its build report
§2.4), and `HumanGateway.submit_decision` refuses outright while that is `None`
(`gateway.py:986-988`). The consequence on `main` is that EVERY auditor decision —
APROVAR and NEGAR alike — fails with `form_projection_unavailable`.

The property under test is the same one WP-J1-00's suite asserts for the other
planes: not "an app starts", but *which concrete provider sits behind each port*,
and which refusal replaces it when the material is not exactly right.
"""

from __future__ import annotations

import dataclasses
import hashlib
from datetime import UTC, datetime, timedelta

import asyncpg
import pytest
from sqlalchemy.ext.asyncio import create_async_engine
from tests.support.decision_materials_builder import (
    ACTIVE_KEY,
    RETIRED_KEY,
    _iso,
    _wire,
    binding_database,
    build_bundle,
    build_decision_materials,
    decision_pin_for,
)
from tests.support.materials_builder import TENANT, build_materials

from maezo.gateway.human.decision import BoundDecisionPorts
from maezo.gateway.human.decision_binding import PostgresDecisionBindingSource
from maezo.gateway.human.decision_custody import PostgresHumanDecisionCustody
from maezo.gateway.human.decision_custody_connection import DecisionCustodyError
from maezo.gateway.human.decision_materials import (
    DECISION_MATERIAL_DIRECTORY,
    DecisionMaterialError,
    DecisionMaterialPin,
    DecisionPublicManifest,
    load_decision_materials,
    verify_decision_materials,
)
from maezo.gateway.human.models import Scope
from maezo.gateway.human.outbox import PostgresDecisionAdmission
from maezo.gateway.human.phi_decision_authorization import EngineBackedPhiDecisionAuthorization
from maezo.gateway.human.production import compose_human_plane
from maezo.gateway.human.read_materials import MaterialLifetime
from maezo.gateway.human.read_profile import parse_model

OUTBOX_DSN = "postgresql://human_outbox:pw@db.invalid:5432/maezo"


def _plane(tmp_path, *, with_decision: bool, **decision_kwargs):
    """Compose the real production plane over unopened (never connected) resources.

    Callers are `async def`: `asyncpg.create_pool()` binds the running loop, so the
    plane must be composed inside one — the same reason WP-J1-00's own composition
    tests are async.

    `asyncpg.create_pool` without `await` and `create_async_engine` are both lazy, so
    this is the production wiring with production providers — only the network and the
    databases are absent. Neither the binding connection nor the PHI connection opens
    a socket at composition time, by construction.
    """
    materials = build_materials(tmp_path / "human")
    decision = build_decision_materials(tmp_path / "decision", **decision_kwargs) if with_decision else None
    pool = asyncpg.create_pool(dsn=OUTBOX_DSN, min_size=1, max_size=2)
    engine = create_async_engine(materials.source_url)
    runtime = compose_human_plane(
        materials,
        pool=pool,
        source_engine=engine,
        lifetime=MaterialLifetime(),
        decision=decision,
    )
    return materials, decision, runtime


async def test_decision_ports_bind_to_the_three_concrete_providers(tmp_path):
    """#22 binding, #23 PHI custody and #25 admission — no stand-in behind any of them."""
    materials, decision, runtime = _plane(tmp_path, with_decision=True)
    ports = runtime.assignment.decision_ports

    assert isinstance(ports, BoundDecisionPorts)
    assert type(ports.binding) is PostgresDecisionBindingSource
    assert type(ports.custody) is PostgresHumanDecisionCustody
    assert type(ports.admission) is PostgresDecisionAdmission
    # `HumanGateway._check_scope` refuses unless all three carry the gateway's scope.
    assert {ports.binding.scope, ports.custody.scope, ports.admission.scope} == {materials.manifest.scope}
    assert decision is not None and decision.manifest.scope == materials.manifest.scope


async def test_the_binding_plane_is_opened_reader_only(tmp_path):
    """Qualification is installed by deployment, never by the serving composition."""
    _, _, runtime = _plane(tmp_path, with_decision=True)
    ports = runtime.assignment.decision_ports
    assert ports is not None
    assert ports.binding._db.mode == "reader"
    assert ports.binding._db.database.reader_role == "binding_reader"


async def test_custody_authorization_is_the_engine_backed_recheck(tmp_path):
    """#24 is the authorization the custody provider re-checks on EVERY operation."""
    _, _, runtime = _plane(tmp_path, with_decision=True)
    ports = runtime.assignment.decision_ports
    assert ports is not None
    assert type(ports.custody._authorization) is EngineBackedPhiDecisionAuthorization
    # Every retained vault key is loaded; a partial key set fails closed (below).
    assert set(ports.custody._keys) == {ACTIVE_KEY, RETIRED_KEY}


async def test_without_the_decision_plane_the_ports_stay_unbound(tmp_path):
    """Dark by default: absent material keeps `main`'s refusal, never a stub provider."""
    _, decision, runtime = _plane(tmp_path, with_decision=False)
    assert decision is None
    assert runtime.assignment.decision_ports is None


async def test_a_decision_plane_for_another_scope_is_refused(tmp_path):
    """Material for another tenant may not bind this gateway's decision ports."""
    other = {"tenant": "tenant_outro", "environment": "producao", "workload_ref": "portal-human"}
    with pytest.raises(DecisionMaterialError):
        _plane(
            tmp_path,
            with_decision=True,
            scope=other,
            database=binding_database(scope=other),
        )


def test_a_root_pin_for_another_database_is_refused(tmp_path):
    """`root.installation_digest` must hash the exact pinned database, at load time."""
    manifest, files = build_bundle(
        directory=tmp_path / "decision", database=binding_database(database_oid=99999)
    )
    # Re-pin the manifest to a DIFFERENT database than the one the root attests.
    repinned = manifest.model_copy(update={"binding_database": binding_database()})
    # Pinned to the REPINNED manifest on purpose: the refusal under test is the root's
    # installation digest, not the out-of-band digest mismatch.
    with pytest.raises(DecisionMaterialError):
        verify_decision_materials(
            decision_pin_for(repinned),
            repinned,
            files,
            now=datetime.now(UTC),
            directory=str(tmp_path / "decision"),
        )


async def test_a_partial_vault_key_set_fails_closed(tmp_path):
    """Removing a retained key never silently degrades to "decrypt what we can"."""
    materials = build_materials(tmp_path / "human")
    decision = build_decision_materials(tmp_path / "decision")
    partial = dataclasses.replace(decision, vault_material={ACTIVE_KEY: decision.vault_material[ACTIVE_KEY]})
    pool = asyncpg.create_pool(dsn=OUTBOX_DSN, min_size=1, max_size=2)
    with pytest.raises(DecisionCustodyError):
        compose_human_plane(
            materials,
            pool=pool,
            source_engine=create_async_engine(materials.source_url),
            lifetime=MaterialLifetime(),
            decision=partial,
        )


def test_a_secret_may_not_carry_a_public_digest(tmp_path):
    """A public digest of a secret is a dictionary-attack oracle; the manifest refuses it."""
    with pytest.raises(DecisionMaterialError):
        build_bundle(
            directory=tmp_path / "decision",
            overrides={"files": {"vault-keys.json": "6" * 64}},
        )


def test_expired_material_is_refused(tmp_path):
    """Assurance is a window, not a label: `now` outside it binds nothing."""
    manifest, files = build_bundle(directory=tmp_path / "decision")
    with pytest.raises(DecisionMaterialError):
        verify_decision_materials(
            decision_pin_for(manifest),
            manifest,
            files,
            now=datetime.now(UTC) + timedelta(days=2),
            directory=str(tmp_path / "decision"),
        )


def test_material_may_not_outlive_the_phi_deployment_it_describes(tmp_path):
    """An expired PHI deployment authorizes nothing; material may not claim otherwise."""
    with pytest.raises(DecisionMaterialError):
        build_bundle(
            directory=tmp_path / "decision",
            overrides={
                "valid_until": (datetime.now(UTC) + timedelta(days=30))
                .isoformat(timespec="microseconds")
                .replace("+00:00", "Z")
            },
        )


def test_only_the_one_fixed_directory_is_loadable(tmp_path):
    """No search path and no fallback: a configured locator is not a locator."""
    anchor = DecisionMaterialPin(
        tenant=TENANT, material_version_id="j1decision" + "0" * 26, public_manifest_sha256="a" * 64
    )
    with pytest.raises(DecisionMaterialError):
        load_decision_materials(str(tmp_path), anchor)
    with pytest.raises(DecisionMaterialError):
        load_decision_materials(None, anchor)
    assert DECISION_MATERIAL_DIRECTORY == "/run/maezo-decision-materials/current"


def test_scope_guard_names_the_material_not_the_transport(tmp_path):
    """The composition refuses before a connection is attempted, with the material named."""
    from maezo.gateway.human.production import compose_decision_ports

    decision = build_decision_materials(tmp_path / "decision")
    with pytest.raises(DecisionMaterialError):
        compose_decision_ports(
            decision,
            scope=Scope(tenant="tenant_outro", environment="producao", workload_ref="portal-human"),
            client=None,  # type: ignore[arg-type]
            outbox=None,  # type: ignore[arg-type]
        )


def test_a_wholly_substituted_decision_bundle_is_refused_only_by_the_out_of_band_pin(tmp_path):
    """V14 MAJOR-2 — the bundle does not attest itself; configuration says which bundle.

    `verify_root` compares the installation root to the fingerprint the SAME manifest
    declares, and the installation digest to a hash of the SAME manifest's
    `binding_database`. An adversary who can write the directory supplies their own
    root keypair, their own binding host and their own AES vault keys, and every
    internal check passes. The second bundle below is exactly that — internally
    perfect, refused only because the deployment is pinned to the first.
    """
    first, first_files = build_bundle(directory=tmp_path / "first")
    second, second_files = build_bundle(
        directory=tmp_path / "second", database=binding_database(database_oid=4242)
    )
    assert first.root_key_fingerprint != second.root_key_fingerprint

    # Internally coherent: pinned to itself the substituted bundle verifies.
    assert verify_decision_materials(
        decision_pin_for(second),
        second,
        second_files,
        now=datetime.now(UTC),
        directory=str(tmp_path / "second"),
    )
    # Pinned to the bundle the deployment approved, it is refused.
    with pytest.raises(DecisionMaterialError):
        verify_decision_materials(
            decision_pin_for(first),
            second,
            second_files,
            now=datetime.now(UTC),
            directory=str(tmp_path / "second"),
        )

    # Each of the three pinned facts is load-bearing on its own.
    anchor = decision_pin_for(first)
    for wrong in (
        DecisionMaterialPin("tenant_outro", anchor.material_version_id, anchor.public_manifest_sha256),
        DecisionMaterialPin(anchor.tenant, "outro" + "0" * 27, anchor.public_manifest_sha256),
        DecisionMaterialPin(anchor.tenant, anchor.material_version_id, "f" * 64),
    ):
        with pytest.raises(DecisionMaterialError):
            verify_decision_materials(
                wrong, first, first_files, now=datetime.now(UTC), directory=str(tmp_path / "first")
            )


def test_the_decision_pin_digest_covers_every_manifest_field(tmp_path):
    """One altered manifest fact changes the pinned digest, so nothing is left unpinned."""
    manifest, files = build_bundle(directory=tmp_path / "decision")
    anchor = decision_pin_for(manifest)
    for update in ({"issuer": "https://substituido.invalid"}, {"binding_timeout_seconds": 9}):
        altered = manifest.model_copy(update=update)
        assert hashlib.sha256(altered.canonical()).hexdigest() != anchor.public_manifest_sha256
        with pytest.raises(DecisionMaterialError):
            verify_decision_materials(
                anchor, altered, files, now=datetime.now(UTC), directory=str(tmp_path / "decision")
            )


def test_revoked_vault_material_and_a_stale_observation_are_refused(tmp_path):
    """V14 MAJOR-2 — withdrawn material fails closed, and the clamp shortens the plane."""
    live, _ = build_bundle(directory=tmp_path / "probe")
    assert len(live.attested_digests()) >= 6

    # A manifest that attests material its own snapshot declares revoked is refused
    # while parsing — for every attested digest, individually.
    for revoked in sorted(live.attested_digests()):
        payload = _wire(live)
        payload["revocation_snapshot"] = {**payload["revocation_snapshot"], "revoked_fingerprints": [revoked]}
        with pytest.raises(DecisionMaterialError):
            parse_model(DecisionPublicManifest, payload)
    # An unrelated withdrawn fingerprint is not this bundle's problem.
    payload = _wire(live)
    payload["revocation_snapshot"] = {**payload["revocation_snapshot"], "revoked_fingerprints": ["9" * 64]}
    assert parse_model(DecisionPublicManifest, payload).revocation_snapshot.revoked_fingerprints

    # A stale observation is refused even while the issue window is open.
    past = datetime.now(UTC) - timedelta(hours=3)
    manifest, files = build_bundle(
        directory=tmp_path / "stale",
        overrides={
            "revocation_snapshot": {
                "observed_at": _iso(past),
                "valid_until": _iso(past + timedelta(minutes=1)),
            }
        },
    )
    assert manifest.issued_at <= datetime.now(UTC) < manifest.valid_until
    with pytest.raises(DecisionMaterialError):
        verify_decision_materials(
            decision_pin_for(manifest),
            manifest,
            files,
            now=datetime.now(UTC),
            directory=str(tmp_path / "stale"),
        )

    # A snapshot ending before the manifest shortens the plane's life.
    soon = datetime.now(UTC) + timedelta(minutes=7)
    materials = build_decision_materials(
        tmp_path / "short",
        overrides={
            "revocation_snapshot": {
                "observed_at": _iso(datetime.now(UTC) - timedelta(minutes=1)),
                "valid_until": _iso(soon),
            }
        },
    )
    assert materials.not_after < materials.manifest.valid_until
    assert abs((materials.not_after - soon).total_seconds()) < 1

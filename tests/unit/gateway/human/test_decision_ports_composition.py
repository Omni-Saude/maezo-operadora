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
from datetime import UTC, datetime, timedelta

import asyncpg
import pytest
from sqlalchemy.ext.asyncio import create_async_engine
from tests.unit.gateway.human.decision_materials_builder import (
    ACTIVE_KEY,
    RETIRED_KEY,
    binding_database,
    build_bundle,
    build_decision_materials,
)
from tests.unit.gateway.human.materials_builder import build_materials

from maezo.gateway.human.decision import BoundDecisionPorts
from maezo.gateway.human.decision_binding import PostgresDecisionBindingSource
from maezo.gateway.human.decision_custody import PostgresHumanDecisionCustody
from maezo.gateway.human.decision_custody_connection import DecisionCustodyError
from maezo.gateway.human.decision_materials import (
    DECISION_MATERIAL_DIRECTORY,
    DecisionMaterialError,
    load_decision_materials,
    verify_decision_materials,
)
from maezo.gateway.human.models import Scope
from maezo.gateway.human.outbox import PostgresDecisionAdmission
from maezo.gateway.human.phi_decision_authorization import EngineBackedPhiDecisionAuthorization
from maezo.gateway.human.production import compose_human_plane
from maezo.gateway.human.read_materials import MaterialLifetime

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
    with pytest.raises(DecisionMaterialError):
        verify_decision_materials(
            manifest.model_copy(update={"binding_database": binding_database()}),
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
    with pytest.raises(DecisionMaterialError):
        load_decision_materials(str(tmp_path))
    with pytest.raises(DecisionMaterialError):
        load_decision_materials(None)
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

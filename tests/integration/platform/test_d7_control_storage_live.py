"""Real D7 storage lane, requiring ROOT-provided authenticated owner fixtures.

The source-author lane never runs this module. The fixture must provide real
PostgreSQL16 connections and the actual qualified DynamoDB adapter. No embedded
store, engine double, credentials fallback or service startup is provided here.
"""

from __future__ import annotations

from typing import Any

import pytest

from maezo.platform.engine_bootstrap import controller_storage as s

# `root_fixture`: PRIVATE ROOT-supplied materials; deselected from the global `-m integration`
# lane unless MAEZO_ROOT_FIXTURES=1 (tests/integration/conftest.py). Allowlisted with its reason
# in tests/unit/ci/test_root_fixture_deselection.py.
pytestmark = [pytest.mark.integration, pytest.mark.root_fixture]


@pytest.fixture
def live(request: pytest.FixtureRequest) -> Any:
    try:
        lane = request.getfixturevalue("d7_live_storage_lane")
    except pytest.FixtureLookupError:
        pytest.skip("NOT_RUN: ROOT must provide the real owner-qualified D7 storage lane")
    with lane.owner_connection.cursor() as cursor:
        cursor.execute(
            "SELECT pg_catalog.current_setting('server_version_num')::int,SESSION_USER,CURRENT_USER"
        )
        version, session, effective = cursor.fetchone()
        # 16 (Aurora do Terraform) ou 17 (RDS do dev e compose, 24/09/2026).
        assert 160000 <= version < 180000 and session == effective
    assert lane.dynamodb_client.meta.service_model.service_name == "dynamodb"
    return lane


def test_real_owner_installation_and_current_runtime_helper(live: Any) -> None:
    result = live.installer.install(live.installation_input_wire)
    assert result.kind == "COMMITTED"
    prepared = live.prepare_current_runtime_through_real_controller()
    with live.runtime_connection.cursor() as cursor:
        cursor.execute(
            "SELECT maezo_d7_control.lock_runtime_v2_scope(%s::jsonb)",
            (s.canonical(prepared.guard_request).decode(),),
        )
        guard = cursor.fetchone()[0]
        assert guard["admission_phase"] == "ACTIVE" and guard["login_oid"] == prepared.actual_login_oid
        assert guard["database_clock_ms"] < guard["valid_until"]


@pytest.mark.parametrize(
    "case",
    [
        "absent_fence",
        "wrong_database",
        "wrong_incarnation",
        "stale_epoch",
        "wrong_session_user",
        "wrong_login_oid",
        "public_acl",
        "owner_set_route",
        "changed_function",
        "expired_equal_deadline",
        "stale_open_receipt",
        "removed_role_reuse",
        "candidate_to_runtime",
        "unqualified_native_principal",
        "old_owner_close",
        "missing_decision_bytes",
        "duplicate_wire_key",
        "negative_zero",
        "depth33",
        "noncanonical_base64",
    ],
)
def test_real_storage_refuses_before_catalog_or_authority_effect(live: Any, case: str) -> None:
    operation, before = live.arrange_real_refusal(case)
    with pytest.raises(Exception) as caught:
        operation()
    assert getattr(caught.value, "sqlstate", None) in {
        "P7D01",
        "P7D02",
        "P7D03",
        "P7D04",
        "P7D05",
        "P7D06",
        "P7D07",
        "P7D08",
        "P7D09",
        "P7D10",
        "P7D11",
    }
    assert live.actual_catalog_and_rows() == before


def test_real_successor_receipt_recovery_and_historical_retirement(live: Any) -> None:
    transition = live.arrange_real_successor(epoch_before=9, epoch_after=10)
    committed = live.issuer_store.mutate(live.issuer_connection, "reconcile_fence", transition.call)
    assert committed.kind == "COMMITTED"
    replay = live.issuer_store.mutate(
        live.issuer_connection, "reconcile_fence", transition.current_proof_replay
    )
    assert replay.kind == "COMMITTED" and replay.canonical_result_bytes == committed.canonical_result_bytes
    historical = live.retire_real_historical_generation(transition)
    assert historical["generation_epoch"] == 9 and historical["epoch"] == 10
    assert all(
        historical[k] is None
        for k in ("valid_until", "observation_deadline", "admission_proof_digest", "journal_revision")
    )
    assert live.actual_pending_operation_ids() == transition.original_pending_ids


def test_real_dynamodb_conditional_conflict_and_complete_chunk_readback(live: Any) -> None:
    change = live.arrange_actual_claim_transition()
    first = live.dynamodb_store.apply(change)
    assert first.kind == "COMMITTED"
    second = live.dynamodb_store.apply(change)
    assert second.kind == "CONFLICT"
    read = live.dynamodb_store.read_owner_lineage(**live.current_lineage_request())
    assert read.value()["claim_receipt_sha256"] == s.digest(change.append[0][1])
    intent_change = live.arrange_actual_large_intent()
    assert live.dynamodb_store.apply(intent_change).kind == "COMMITTED"
    assert live.dynamodb_store.read_document(intent_change.append[0][0]) == intent_change.append[0][1]

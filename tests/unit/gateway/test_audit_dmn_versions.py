"""Proof that `AuditRecord.dmn_versions` can be populated from a real DMN evaluation (T1.5,
ADR-0028 §5) — closes the TODO(T1.5) that used to live on `AuditRecord.dmn_versions`'s
docstring (`gateway/audit.py`).

`DmnTransport.evaluate(...)` (`maezo.tools.workers.dmn_transport`) returns a `DmnVersion`
alongside every result; `DmnVersion.to_audit_dict()` is the exact shape this column expects
(`{"version": ..., "id": ..., "deploymentId": ...}`). This module proves the wire-up with
`FakeDmnTransport` (fast, no engine) and — when the live compose engine is reachable — with a
REAL `CibSevenDmnTransport` evaluation, so at least one example in this repo cites an ACTUAL
engine-verified `dmn_versions` payload, not a fabricated one (constraint 3).

What this does NOT prove (a separate, larger follow-up, explicitly out of T1.5's scope): that any
production code path actually constructs an `AuditRecord` from a worker/agent decision today — it
does not (`grep AuditRecord( src/` finds exactly one call site, `audit_postgres.py`'s
row-to-record decoder, which is a READ path). This module demonstrates the schema is populatable
and gives a template for the caller that eventually does the write-side wiring.
"""

from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest

from maezo.gateway.audit import AuditRecord
from maezo.tools.workers.dmn_transport import (
    CibSevenDmnTransport,
    DmnVersion,
    FakeDmnTransport,
    evaluate_sync,
)


def test_dmn_version_to_audit_dict_shape_matches_column_expectation() -> None:
    """`DmnVersion.to_audit_dict()` is exactly the per-key shape `dmn_versions` expects."""
    version = DmnVersion(key="pagto_alcada", id="pagto_alcada:3:abc", version=3, deployment_id="dep-1")
    assert version.to_audit_dict() == {"version": 3, "id": "pagto_alcada:3:abc", "deploymentId": "dep-1"}


def test_audit_record_populated_from_fake_dmn_evaluation() -> None:
    """End-to-end (fast, no engine): evaluate via FakeDmnTransport, build a real AuditRecord
    with dmn_versions populated from the returned DmnVersion — proves the schema round-trips
    (canonicalize_jsonb + hash) without raising, and the value is queryable afterward."""
    fake = FakeDmnTransport()
    fake.register(
        "pagto_alcada",
        [{"faixa_valor": "ALCADA_L1", "grupo_aprovador": "aprovacao-financeira-l1"}],
        version=7,
        definition_id="pagto_alcada:7:test",
        deployment_id="dep-test-1",
    )

    rows, version = evaluate_sync(fake, "pagto_alcada", {"valor_pagamento_cents": 25_000_000})

    record = AuditRecord(
        agent_id="pagto-worker",
        tenant_id="amh",
        agent_version="1.0.0",
        action="operadora.pagto.calculate_facts",
        decision="ALLOW",
        details={"faixa_valor": rows[0]["faixa_valor"]},
        dmn_versions={"pagto_alcada": version.to_audit_dict()},
        timestamp=datetime.now(UTC),
    )

    assert record.dmn_versions == {
        "pagto_alcada": {"version": 7, "id": "pagto_alcada:7:test", "deploymentId": "dep-test-1"}
    }
    # Never "unknown" (the donor's fail-open gap, ADR-0028 §2) — a real int version + real id.
    assert record.dmn_versions["pagto_alcada"]["version"] == 7
    assert record.record_hash is not None


@pytest.mark.integration
async def test_audit_record_populated_from_live_engine_evaluation() -> None:
    """Same proof, but against the REAL compose engine (ADR-0011, never a mock) — at least one
    example in this repo of a `dmn_versions`-populated audit record citing an ACTUAL engine
    version (constraint 3: no fabricated results). Loudly skips if the engine is unreachable
    (mirrors `tests/integration/conftest.py`'s `_skip_if_engine_unreachable` pattern, applied
    locally here since this file lives under `tests/unit/`, not `tests/integration/`)."""
    from maezo.platform.deploy.engine_deploy import resolve_engine_rest_url

    base_url = resolve_engine_rest_url()
    try:
        async with httpx.AsyncClient() as probe:
            resp = await probe.get(f"{base_url}/version", timeout=3.0)
        reachable = resp.status_code == 200
    except httpx.HTTPError:
        reachable = False
    if not reachable:
        pytest.skip(
            f"COULD NOT VERIFY: CIB Seven engine unreachable at {base_url} (GET /version "
            "failed). Run `docker compose --profile core up -d` and retry."
        )

    transport = CibSevenDmnTransport(base_url, timeout=30.0)  # engine can be slow under suite load
    try:
        rows, version = await transport.evaluate("ans_retry_policy", {"retry_attempt": 1})
    finally:
        await transport.close()

    record = AuditRecord(
        agent_id="ans-submit-worker",
        tenant_id="amh",
        agent_version="1.0.0",
        action="regulatorio.anssubmit.retransmit",
        decision="ALLOW",
        details={"backoff": rows[0]["backoff"], "continue_retry": rows[0]["continue_retry"]},
        dmn_versions={"ans_retry_policy": version.to_audit_dict()},
        timestamp=datetime.now(UTC),
    )

    assert record.dmn_versions["ans_retry_policy"]["version"] == version.version
    assert record.dmn_versions["ans_retry_policy"]["deploymentId"] == version.deployment_id
    assert record.dmn_versions["ans_retry_policy"]["id"] != "unknown"  # the donor's fail-open gap, fixed

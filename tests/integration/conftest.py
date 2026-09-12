"""Shared fixtures for `tests/integration/` — real CIB Seven engine, no mock (ADR-0011).

Per T1.1 charter constraint: "if docker unavailable, explicit could-not-verify + mocked
variants" — every test in this package first probes `GET /engine-rest/version`; if the engine
is unreachable, the test SKIPS with an explicit, loud reason (never silently passes, never
fabricates a result). CI always brings the engine up before running this lane (see
`.github/workflows/ci.yml` job `integration`), so this only triggers for local runs without
`docker compose --profile core up -d`.

Engine URL resolution mirrors `maezo.platform.deploy.engine_deploy.resolve_engine_rest_url()`
(same `ENGINE_REST_URL` env var, same default) — this package deploys ad hoc test-only BPMN,
never anything from `spec/` (constraint 5: spec/ is the single source of truth for real
artifacts; a temp BPMN string here is explicitly NOT a spec/ artifact, per design §14 point 3).
"""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest

from maezo.platform.deploy.engine_deploy import resolve_engine_rest_url

#: Unique per pytest session — every deployment/process-definition key this package creates is
#: suffixed with this so repeated local runs against a persistent dev-stack volume never collide
#: with engine-side duplicate-filtering or a stale process definition from a previous run.
RUN_ID = uuid.uuid4().hex[:8]

#: PR-A landing repair (real-engine lane, PR #375). Suites marked `root_fixture` need a PRIVATE,
#: ROOT-supplied fixture (`MAEZO_HUMAN_RELAY_PRIVATE_DIR`, `MAEZO_DECISION_BINDING_TEST_CONFIG`,
#: the dedicated PHI PostgreSQL/mTLS custody) that the global `-m integration` lane never has.
#: They must neither skip (scripts/ci/run_live_pytest.py admits no undeclared skip: "corpo nao
#: verificado") nor fake the fixture. They are DESELECTED unless ROOT opts in with
#: `MAEZO_ROOT_FIXTURES=1` — the same shape as pytest's own `-m` deselection, and the same posture
#: `tests/unit/runtime/test_inference_live.py` takes with `llm_live`. Pinned by
#: `tests/unit/ci/test_root_fixture_deselection.py` (registered marker, reviewed allowlist, the
#: hook proved to fire). Visible THREE ways, the first two of which survive the lane's `-q`: the
#: count lands in pytest's own `deselected` summary, `_announce` names every deselected module,
#: and `pytest_report_header` states the posture on a non-quiet run.
ROOT_FIXTURE_OPT_IN_ENV = "MAEZO_ROOT_FIXTURES"
ROOT_FIXTURE_MARKER = "root_fixture"


def _root_fixtures_opted_in() -> bool:
    return os.environ.get(ROOT_FIXTURE_OPT_IN_ENV) == "1"


def _announce(config: Any, message: str) -> None:
    """Write through the terminal reporter, which `-q` does not suppress (the report header is).

    The CI lane runs `-q`; an announcement only a verbose run can see would leave the deselection
    exactly as invisible as the skip it replaces.
    """
    reporter = config.pluginmanager.get_plugin("terminalreporter")
    if reporter is not None:
        reporter.write_line(message)


def pytest_collection_modifyitems(config: Any, items: list[Any]) -> None:
    """Deselect `root_fixture` suites unless ROOT opted in; never a skip, never silent."""
    if _root_fixtures_opted_in():
        return
    kept: list[Any] = []
    dropped: list[Any] = []
    for item in items:
        (dropped if item.get_closest_marker(ROOT_FIXTURE_MARKER) else kept).append(item)
    if not dropped:
        return
    config.hook.pytest_deselected(items=dropped)
    items[:] = kept
    modules = sorted({str(item.path) for item in dropped})
    _announce(
        config,
        f"[{ROOT_FIXTURE_MARKER}] DESELECTED {len(dropped)} test(s) in {len(modules)} module(s) — "
        f"they need PRIVATE ROOT-supplied fixtures; set {ROOT_FIXTURE_OPT_IN_ENV}=1 with those "
        f"fixtures present to run them: {', '.join(modules)}",
    )


def pytest_report_header(config: Any) -> str:
    if _root_fixtures_opted_in():
        return (
            f"{ROOT_FIXTURE_MARKER} suites: SELECTED ({ROOT_FIXTURE_OPT_IN_ENV}=1; "
            "ROOT fixtures must be present)"
        )
    return (
        f"{ROOT_FIXTURE_MARKER} suites: DESELECTED — set {ROOT_FIXTURE_OPT_IN_ENV}=1 together with the "
        "private ROOT fixtures to run them (tests/integration/conftest.py)"
    )


def _engine_reachable(base_url: str) -> bool:
    try:
        resp = httpx.get(f"{base_url}/version", timeout=3.0)
        return resp.status_code == 200
    except httpx.HTTPError:
        return False


@pytest.fixture(scope="session")
def engine_base_url() -> str:
    return resolve_engine_rest_url()


@pytest.fixture(scope="session", autouse=True)
def _skip_if_engine_unreachable(engine_base_url: str) -> None:
    """Explicit could-not-verify boundary (charter constraint) — never a silent pass."""
    if not _engine_reachable(engine_base_url):
        pytest.skip(
            f"COULD NOT VERIFY: CIB Seven engine unreachable at {engine_base_url} "
            "(GET /version failed). Run `docker compose --profile core up -d` and retry — "
            "see docs/design/T1.1-runtime-spine.md §14 point 3."
        )


@pytest.fixture
async def engine_client(engine_base_url: str) -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient(base_url=engine_base_url, timeout=30.0) as client:
        yield client


def echo_process_bpmn(*, process_key: str, topic: str) -> str:
    """A minimal single-external-task process: start -> external task(`topic`) -> end.

    Deliberately the smallest possible fixture — the point of this suite is to exercise the
    HARNESS's fetch/complete/failure/retry/unlock mechanics against a real engine, not to
    exercise BPMN modeling. Never written under `spec/` (constraint 5) — posted directly to
    `/deployment/create` as an in-memory string.
    """
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<bpmn:definitions xmlns:bpmn="http://www.omg.org/spec/BPMN/20100524/MODEL"
                  xmlns:camunda="http://camunda.org/schema/1.0/bpmn"
                  id="Definitions_{process_key}"
                  targetNamespace="http://maezo.health/test/t1-1-runtime-spine">
  <bpmn:process id="{process_key}" name="T1.1 integration test process" isExecutable="true"
                camunda:historyTimeToLive="1">
    <bpmn:startEvent id="StartEvent_1">
      <bpmn:outgoing>Flow_1</bpmn:outgoing>
    </bpmn:startEvent>
    <bpmn:serviceTask id="ExternalTask_1" name="test external task"
                      camunda:type="external" camunda:topic="{topic}">
      <bpmn:incoming>Flow_1</bpmn:incoming>
      <bpmn:outgoing>Flow_2</bpmn:outgoing>
    </bpmn:serviceTask>
    <bpmn:endEvent id="EndEvent_1">
      <bpmn:incoming>Flow_2</bpmn:incoming>
    </bpmn:endEvent>
    <bpmn:sequenceFlow id="Flow_1" sourceRef="StartEvent_1" targetRef="ExternalTask_1"/>
    <bpmn:sequenceFlow id="Flow_2" sourceRef="ExternalTask_1" targetRef="EndEvent_1"/>
  </bpmn:process>
</bpmn:definitions>
"""


async def deploy_process(
    client: httpx.AsyncClient, *, process_key: str, topic: str, deployment_name: str
) -> None:
    """POST /deployment/create with the ad hoc echo process — fail-closed (raises on non-2xx,
    the engine's error body is never swallowed, mirroring `engine_deploy.py`'s posture)."""
    bpmn_xml = echo_process_bpmn(process_key=process_key, topic=topic)
    resp = await client.post(
        "/deployment/create",
        data={
            "deployment-name": deployment_name,
            "enable-duplicate-filtering": "true",
            "deploy-changed-only": "true",
        },
        files={f"{process_key}.bpmn": (f"{process_key}.bpmn", bpmn_xml, "text/xml")},
    )
    if resp.status_code >= 300:
        raise AssertionError(f"deployment failed: {resp.status_code} {resp.text}")


async def start_process(client: httpx.AsyncClient, *, process_key: str, business_key: str) -> str:
    resp = await client.post(
        f"/process-definition/key/{process_key}/start",
        json={"businessKey": business_key, "variables": {}},
    )
    if resp.status_code >= 300:
        raise AssertionError(f"start-process failed: {resp.status_code} {resp.text}")
    result: dict[str, Any] = resp.json()
    return str(result["id"])


async def history_process_instance(client: httpx.AsyncClient, process_instance_id: str) -> dict[str, Any]:
    resp = await client.get(f"/history/process-instance/{process_instance_id}")
    resp.raise_for_status()
    result: dict[str, Any] = resp.json()
    return result


async def list_incidents(client: httpx.AsyncClient, process_instance_id: str) -> list[dict[str, Any]]:
    resp = await client.get("/incident", params={"processInstanceId": process_instance_id})
    resp.raise_for_status()
    result: list[dict[str, Any]] = resp.json()
    return result


# ---------------------------------------------------------------------------
# Durable audit bootstrap (T1.10 wave integration — ADR-0007 L0, fail-closed audit lane glue).
#
# T-C made the worker harness emit-before-complete FAIL-CLOSED: a harness without an
# `audit_sink` routes every task completion to an incident (`AuditEmitError`), and T-C2 made
# `start_process_idempotent` structurally REQUIRE a durable sink + provenance. This lane runs
# against the REAL engine — so it runs against the REAL durable sink too: the compose stack's
# Postgres (the same one the engine itself persists to), with migrations 0001->0005 applied to a
# per-run tenant schema. FakeStartAuditSink is deliberately NOT used here: the lane's purpose is
# the production wiring, and the lane already has Postgres.
#
# DSN resolution (mirrors tests/unit/gateway/test_audit_postgres.py's convention, extended with
# the compose port variable): `MAEZO_TEST_DATABASE_URL` wins; otherwise
# postgresql://maezo:maezo@localhost:${MAEZO_PG_HOST_PORT:-5433}/maezo — which is byte-for-byte
# the CI lane's Postgres (ci.yml pins MAEZO_PG_HOST_PORT=5432), the local dev default (5433),
# and any isolated validation stack (e.g. MAEZO_PG_HOST_PORT=5546) with zero workflow edits.
#
# Unreachable Postgres -> loud, explicit skip of the suites that REQUEST these fixtures (same
# ADR-0011 "explicit could-not-verify" posture as the engine gate above — never silent, never
# fabricated). Suites that don't audit (e.g. dmn parity) don't request them and are unaffected.
# ---------------------------------------------------------------------------


def _audit_pg_dsn() -> str:
    import os

    explicit = os.environ.get("MAEZO_TEST_DATABASE_URL")
    if explicit:
        return explicit
    port = os.environ.get("MAEZO_PG_HOST_PORT", "5433")
    return f"postgresql://maezo:maezo@localhost:{port}/maezo"


def _pg_reachable(dsn: str) -> bool:
    import asyncio

    import asyncpg  # type: ignore[import-untyped]

    from maezo.gateway.audit_postgres import normalize_dsn

    async def _probe() -> bool:
        try:
            conn = await asyncio.wait_for(asyncpg.connect(normalize_dsn(dsn)), timeout=3.0)
        except Exception:  # any failure means "skip loudly", never an error here
            return False
        await conn.close()
        return True

    return asyncio.run(_probe())


def _apply_migrations(dsn: str, tenant_id: str) -> None:
    """Apply migrations 0001->0005 to `tenant_id`'s schema — the REAL alembic migrations (not a
    DDL mirror): the lane proves the production bootstrap end-to-end, including env.py's
    tenant-aware search_path."""
    import argparse
    from pathlib import Path

    from alembic import command
    from alembic.config import Config

    repo_root = Path(__file__).resolve().parents[2]
    cfg = Config(str(repo_root / "alembic.ini"))
    cfg.set_main_option("script_location", str(repo_root / "src" / "maezo" / "platform" / "migrations"))
    # alembic.ini hardcodes the docker-internal DSN (postgres:5432); point it at the lane's
    # host-published Postgres instead. env.py's async engine needs the +asyncpg driver suffix.
    async_dsn = dsn if "+asyncpg" in dsn else dsn.replace("postgresql://", "postgresql+asyncpg://", 1)
    cfg.set_main_option("sqlalchemy.url", async_dsn)
    cfg.cmd_opts = argparse.Namespace(x=[f"tenant={tenant_id}"])  # env.py: -x tenant=<id>
    command.upgrade(cfg, "head")


@pytest.fixture(scope="session")
def audit_pg(engine_base_url: str) -> Any:
    """Session-scoped durable-audit bootstrap: per-run tenant schema + REAL migrations 0001->0005.

    Yields ``(dsn, tenant_id)``. Skips LOUDLY when the lane's Postgres is unreachable (ADR-0011
    could-not-verify — mirrors the engine gate). Depends on `engine_base_url` purely to keep the
    engine-unreachable session skip first (clearer skip reason ordering).
    """
    import asyncio

    import asyncpg  # type: ignore[import-untyped]

    from maezo.gateway.audit_postgres import normalize_dsn

    dsn = _audit_pg_dsn()
    if not _pg_reachable(dsn):
        pytest.skip(
            f"COULD NOT VERIFY: lane Postgres unreachable at {dsn!r} (override with "
            "MAEZO_TEST_DATABASE_URL / MAEZO_PG_HOST_PORT). The fail-closed audit suites need the "
            "compose stack's Postgres: `docker compose --profile core up -d`."
        )

    tenant_id = f"it_{RUN_ID}"  # [a-z][a-z0-9_]* — per-run schema, no cross-run dedup residue

    async def _create_schema() -> None:
        conn = await asyncpg.connect(normalize_dsn(dsn))
        try:
            await conn.execute(f'CREATE SCHEMA IF NOT EXISTS "{tenant_id}"')
        finally:
            await conn.close()

    async def _drop_schema() -> None:
        conn = await asyncpg.connect(normalize_dsn(dsn))
        try:
            await conn.execute(f'DROP SCHEMA IF EXISTS "{tenant_id}" CASCADE')
        finally:
            await conn.close()

    asyncio.run(_create_schema())
    _apply_migrations(dsn, tenant_id)
    yield (dsn, tenant_id)
    asyncio.run(_drop_schema())


@pytest.fixture(scope="session")
def audit_tenant(audit_pg: tuple[str, str]) -> str:
    """The per-run tenant id whose schema carries `audit_chain`/`audit_emit_dedup` — pass it as
    the harness `tenant=` so records + dedup keys are honest about which chain they write."""
    return audit_pg[1]


@pytest.fixture
async def audit_sink(audit_pg: tuple[str, str]) -> Any:
    """FUNCTION-scoped REAL `PostgresAuditSink` against the lane's Postgres.

    Function scope is deliberate (not an optimization miss): asyncpg pools bind to the event loop
    that first uses them, and pytest-asyncio gives each test its own loop — a session-scoped sink
    would emit on a dead/foreign loop from the second test on. Satisfies both the harness
    `AuditEmitter` and the chokepoint `AuditStartSink` seams.
    """
    from maezo.gateway.audit_postgres import PostgresAuditSink

    dsn, tenant_id = audit_pg
    sink = PostgresAuditSink(dsn, tenant_id)
    try:
        yield sink
    finally:
        await sink.aclose()

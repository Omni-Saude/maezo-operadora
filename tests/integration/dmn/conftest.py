"""Fixtures for `tests/integration/dmn/` — deploys `spec/processes/{bpmn,dmn}` before any
golden-parity test runs.

The CI `integration` job (`.github/workflows/ci.yml`) brings the compose engine up but does NOT
run `make deploy-artifacts` before invoking `pytest tests/integration -m integration` — this
package's tests need the REAL `spec/processes/dmn/*.dmn` tables deployed (not the synthetic
ad hoc BPMN `tests/integration/conftest.py`'s own fixtures use), so it deploys them itself here,
once per session, via the SAME `EngineDeployClient`/`collect_artifacts` machinery `make
deploy-artifacts` uses (T1.3) — idempotent (`enable-duplicate-filtering`), so re-running this
against a persistent dev-stack volume is always safe (0 new / N skipped-duplicate on repeat).

Runs after the parent package's `_skip_if_engine_unreachable` autouse fixture (same session
scope, parent conftest — pytest instantiates ancestor-conftest autouse fixtures first), so an
unreachable engine already skips the whole session before this fixture's deploy call would even
attempt a request.
"""

from __future__ import annotations

import pytest

from maezo.platform.deploy.engine_deploy import (
    DEFAULT_DEPLOYMENT_NAME,
    EngineDeployClient,
    collect_artifacts,
    resolve_spec_processes_dir,
)


@pytest.fixture(scope="session", autouse=True)
def _deploy_spec_artifacts(engine_base_url: str) -> None:
    """Idempotently deploy the full `spec/processes/{bpmn,dmn}` tree before any test in this
    package runs — see module docstring for why this package (unlike its sibling
    `test_worker_runtime_spine.py`, which deploys only its own synthetic ad hoc BPMN) needs the
    REAL spec/ artifacts on the engine."""
    processes_dir = resolve_spec_processes_dir()
    artifacts = collect_artifacts(processes_dir)
    client = EngineDeployClient(base_url=engine_base_url)
    try:
        client.deploy(artifacts, name=DEFAULT_DEPLOYMENT_NAME)
    finally:
        client.close()

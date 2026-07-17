"""Fixtures for `tests/integration/agents/` — deploys `spec/processes/{bpmn,dmn}` before any
Helena/Rafael acceptance test runs. Mirrors `tests/integration/dmn/conftest.py` exactly (same
`EngineDeployClient`/`collect_artifacts` machinery, same idempotent-redeploy rationale) — this
package needs the REAL `SP-OP-ESCALATION-001`/`SP-OP-AUTH-001` BPMN + their DMN tables deployed,
not the synthetic ad hoc BPMN `tests/integration/conftest.py`'s own fixtures use.
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
    package runs (see module docstring)."""
    processes_dir = resolve_spec_processes_dir()
    artifacts = collect_artifacts(processes_dir)
    client = EngineDeployClient(base_url=engine_base_url)
    try:
        client.deploy(artifacts, name=DEFAULT_DEPLOYMENT_NAME)
    finally:
        client.close()

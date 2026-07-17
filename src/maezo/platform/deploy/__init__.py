"""MAEZO Platform Deploy — BPMN/DMN artifact deployment to the process engine.

T1.3: idempotent, versioned deployment of `spec/processes/{bpmn,dmn}/**` to the
CIB Seven engine's REST deployment endpoint (`docker-compose.yml` service
`cibseven`, `cibseven/cibseven:2.1.0`). Sibling concern to
`maezo.platform.validation` (which checks the artifacts are well-formed
*before* they ever reach an engine); this package pushes them to a running
engine and lets the engine's own deployment parser be the final word — a
BPMN/DMN rejection here is a REAL engine-side defect, not a bug in this tool.

Entry points:
    python -m maezo.platform.deploy              # deploy spec/processes/**
    python -m maezo.platform.deploy --list        # GET /deployment listing
    make deploy-artifacts                          # Makefile wrapper
"""

from .engine_deploy import (
    DEFAULT_DEPLOYMENT_NAME,
    DEFAULT_ENGINE_REST_URL,
    ENGINE_REST_URL_ENV,
    DeploymentOutcome,
    EngineDeployClient,
    EngineDeployError,
    collect_artifacts,
    resolve_engine_rest_url,
    resolve_spec_processes_dir,
)

__all__ = [
    "DEFAULT_DEPLOYMENT_NAME",
    "DEFAULT_ENGINE_REST_URL",
    "ENGINE_REST_URL_ENV",
    "DeploymentOutcome",
    "EngineDeployClient",
    "EngineDeployError",
    "collect_artifacts",
    "resolve_engine_rest_url",
    "resolve_spec_processes_dir",
]

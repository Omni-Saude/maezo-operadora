"""Entrypoint `python -m maezo.runtime.worker_runtime` (T1.1 design §10).

Builds `WorkerRuntimeSettings` from the environment and runs `run()` until SIGTERM/SIGINT. The
Helm `Deployment` command for the worker-daemon points here exactly
(`deploy/helm/maezo-tenant/templates/deployment-worker-daemon.yaml`:
``command: ["python", "-m", "maezo.runtime.worker_runtime"]``).

Importing this module has NO side effect (no `asyncio.run` at import time) — only `main()` and
the `if __name__ == "__main__":` guard trigger execution. This is what lets
`[project.scripts] maezo-worker` (pyproject.toml) point at `main` without also running the
daemon on import.
"""

from __future__ import annotations

import asyncio

from .service import run
from .settings import WorkerRuntimeSettings


def main() -> None:
    """Console-script entrypoint (`maezo-worker`) — identical to running this module directly.

    `WorkerRuntimeSettings()` binds every field from the environment via pydantic-settings; every
    field has a default, so no positional/keyword argument is required to construct it (WORKER_ID
    falls back to a stable local default when the Downward API isn't present).
    """
    asyncio.run(run(WorkerRuntimeSettings()))


if __name__ == "__main__":
    main()

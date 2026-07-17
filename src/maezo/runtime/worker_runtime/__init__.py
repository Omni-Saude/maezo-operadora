"""Worker-runtime daemon (T1.1).

Long-running entrypoint that supervises `WorkerHarness.run()` (ADR-0001's residual direction:
engine -> worker). Health-first, bounded non-fatal bring-up, drain on SIGTERM. Run as
`python -m maezo.runtime.worker_runtime` (see `__main__.py`).

Importing this package has no side effect (no `asyncio.run` at import time).
"""

from __future__ import annotations

from maezo.runtime.worker_runtime.service import (
    WorkerState,
    build_readiness_checks,
    register_default_workers,
    run,
)
from maezo.runtime.worker_runtime.settings import WorkerRuntimeSettings

__all__ = [
    "WorkerRuntimeSettings",
    "WorkerState",
    "build_readiness_checks",
    "register_default_workers",
    "run",
]

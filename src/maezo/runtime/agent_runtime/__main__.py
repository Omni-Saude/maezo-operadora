"""Entrypoint `python -m maezo.runtime.agent_runtime` (T1.6, design §10/Q-6).

Builds `AgentRuntimeSettings` from the environment and runs `run()` until SIGTERM/SIGINT. The
Helm `Deployment` command for every per-agent runtime points here exactly
(`deploy/helm/maezo-tenant/templates/deployment-agent-runtime.yaml`:
``command: ["python", "-m", "maezo.runtime.agent_runtime"]``) — this is the module whose absence
CrashLoopBackOff'd every enabled agent pod (helena/rafael/marina, `values.yaml` `agents[]`)
before this build.

Importing this module has NO side effect (no `asyncio.run` at import time) — only `main()` and
the `if __name__ == "__main__":` guard trigger execution, matching `worker_runtime.__main__`
(T1.1) so `[project.scripts] maezo-agent` can point at `main` without also running the daemon on
import.
"""

from __future__ import annotations

import asyncio

from .service import run
from .settings import AgentRuntimeSettings


def main() -> None:
    """Console-script entrypoint (`maezo-agent`) — identical to running this module directly.

    `AgentRuntimeSettings()` binds every field from the environment via pydantic-settings; every
    field has a default (AGENT_ID falls back to a stable local default), so no
    positional/keyword argument is required to construct it.
    """
    asyncio.run(run(AgentRuntimeSettings()))


if __name__ == "__main__":
    main()

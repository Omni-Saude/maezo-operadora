"""Entrypoint `python -m maezo.gateway` (T1.6, defect B1).

Before this build, `deployment-gateway.yaml`'s `command: ["python", "-m", "maezo.gateway"]`
pointed at a package with no `__main__.py` — Python exits 1 ("'maezo.gateway' is a package and
cannot be directly executed"), CrashLoopBackOff on the spot. `gateway.enabled: false` in
`values.yaml` was the mitigation; this module removes the underlying defect so the flag is safe
to flip. See `service.py`'s module docstring for exactly what this process does (and does not)
serve.

Importing this module has NO side effect (no `asyncio.run` at import time) — matches
`worker_runtime.__main__` / `agent_runtime.__main__` (T1.1/T1.6).
"""

from __future__ import annotations

import asyncio

from .service import run
from .settings import GatewaySettings


def main() -> None:
    """Console-script entrypoint (`maezo-gateway`) — identical to running this module directly."""
    asyncio.run(run(GatewaySettings()))


if __name__ == "__main__":
    main()

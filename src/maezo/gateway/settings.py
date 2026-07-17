"""Injectable configuration for the gateway health-only daemon (T1.6).

`maezo.gateway` (PEP, pseudonymizer, audit) is the IN-PROCESS library every runtime pod
(agent-runtime, worker-daemon) imports directly (ADR-0006; see `docs/runbooks/gateway.md`'s
deployment-model note) — there is still no standalone gateway HTTP business surface. This
settings module is deliberately tiny: it exists only for the health/readiness entrypoint
(`__main__.py`/`service.py`) that closes the B1 CrashLoop hazard on `deployment-gateway.yaml`
(`command: ["python", "-m", "maezo.gateway"]`, which pointed at a package with no `__main__.py`).
"""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class GatewaySettings(BaseSettings):
    """Config for the gateway health-only daemon — matches `deployment-gateway.yaml`'s env
    (`TENANT_ID`, `GATEWAY_PORT`, `deployment-gateway.yaml:36-39`)."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore", populate_by_name=True)

    tenant_id: str = Field(default="amh", alias="TENANT_ID")
    # Helm names this GATEWAY_PORT (not HEALTH_PORT, unlike worker_runtime/agent_runtime) —
    # deployment-gateway.yaml:38-39 sets it to "8000" alongside containerPort: 8000.
    health_port: int = Field(default=8000, alias="GATEWAY_PORT")

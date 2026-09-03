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
    # AF-13: the OTLP endpoint this daemon hands `bootstrap_observability` at STEP 0. Added here
    # (rather than read from `os.environ` at the root) so the composition root keeps its single
    # sanctioned config surface — the same field, same alias, that `AgentRuntimeSettings` and
    # `WorkerRuntimeSettings` already carry. None => the documented dev no-op exporter, reported
    # explicitly by the `observability_configured` readiness check rather than silently.
    otel_exporter_otlp_endpoint: str | None = Field(default=None, alias="OTEL_EXPORTER_OTLP_ENDPOINT")
    # Helm names this GATEWAY_PORT (not HEALTH_PORT, unlike worker_runtime/agent_runtime) —
    # deployment-gateway.yaml:38-39 sets it to "8000" alongside containerPort: 8000.
    health_port: int = Field(default=8000, alias="GATEWAY_PORT")

"""Injectable configuration for the agent-runtime daemon (T1.6, design §10/Q-6).

Mirrors `maezo.runtime.worker_runtime.settings.WorkerRuntimeSettings` (T1.1) — every field is
env-driven (Helm injects into `deployment-agent-runtime.yaml`), bare UPPERCASE aliases,
`populate_by_name=True` so tests can also construct by the snake_case field name directly.

`AGENT_ID` selects which `spec/agents/<id>/agent.yaml` this replica serves (Helm sets it per
agent — `deployment-agent-runtime.yaml:75-76`). `AGENT_DEFINITION_PATH` points at the EFFECTIVE
(merged L0+overlay) definition mounted from the per-agent ConfigMap
(`deployment-agent-runtime.yaml:82-83,160-168`) — when set, the service loads THAT file instead
of resolving `spec/agents/` directly, matching the federated-definition model (ADR-0004). Local
dev (no ConfigMap mount) falls back to the T0.3 `spec/` source of truth via `AgentLoader.load_by_id`.
"""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class AgentRuntimeSettings(BaseSettings):
    """Config for the agent-runtime daemon — one process per agent replica (per-agent Deployment,
    `deployment-agent-runtime.yaml`).

    Q-6 (RATIFIED, docs/design/T1.1-runtime-spine.md §10/§17): this build is a HEALTH-ONLY
    scaffold — no LangGraph execution. The settings surface below is therefore intentionally the
    superset Helm already injects (identity, dependency URLs, LLM keys) so the daemon can run
    its three readiness checks (config/policies/inference) honestly; consuming most of these
    values for real graph execution is T1.11's job.
    """

    model_config = SettingsConfigDict(env_file=".env", extra="ignore", populate_by_name=True)

    # --- Identity (Helm injects, deployment-agent-runtime.yaml:73-83) ------------------------
    tenant_id: str = Field(default="amh", alias="TENANT_ID")
    agent_id: str = Field(default="helena", alias="AGENT_ID")
    agent_security_zone: str = Field(default="general", alias="AGENT_SECURITY_ZONE")
    agent_runtime_mode: str = Field(default="local", alias="AGENT_RUNTIME_MODE")
    # Effective (merged) Agent Definition mounted from the per-agent ConfigMap (K8s). None in
    # local dev -> the service resolves `spec/agents/<agent_id>/agent.yaml` instead (T0.3).
    agent_definition_path: str | None = Field(default=None, alias="AGENT_DEFINITION_PATH")

    # --- External dependencies (accepted for Helm/env parity; NOT dialed by this health-only
    # build — no graph execution yet, so no DB session / Kafka producer / engine call is made.
    # Kept here so a later graph-wiring change (T1.11) is a settings-surface no-op.) -----------
    database_url: str | None = Field(default=None, alias="DATABASE_URL")
    kafka_bootstrap_servers: str = Field(default="localhost:9092", alias="KAFKA_BOOTSTRAP_SERVERS")
    phi_hmac_key: str | None = Field(default=None, alias="PHI_HMAC_KEY")
    cibseven_base_url: str = Field(default="http://cibseven:8080/engine-rest", alias="CIBSEVEN_BASE_URL")
    fhir_base_url: str = Field(default="http://hapi-fhir:8080/fhir", alias="FHIR_BASE_URL")
    llm_phi_api_key: str | None = Field(default=None, alias="LLM_PHI_API_KEY")
    llm_general_api_key: str | None = Field(default=None, alias="LLM_GENERAL_API_KEY")

    # --- OTel (accepted for parity; not wired by this build) ----------------------------------
    otel_exporter_otlp_endpoint: str | None = Field(default=None, alias="OTEL_EXPORTER_OTLP_ENDPOINT")

    # --- Operational ----------------------------------------------------------------------------
    health_port: int = Field(default=8000, alias="HEALTH_PORT")
    # T2.4 A2A W4 (T-F daemon-readiness finalization): bounded connectivity-probe timeout for the
    # `a2a_audit_sink_ready` gate, mirroring `worker_runtime.settings.WorkerRuntimeSettings`'s
    # identically-named/defaulted T-D field — never let a slow/hung Postgres hang bring-up.
    dep_connect_timeout_s: float = Field(default=5.0, alias="DEP_CONNECT_TIMEOUT_S")

"""Injectable configuration for the worker-runtime daemon (T1.1 design §11).

Every field is env-driven (Helm injects into `deployment-worker-daemon.yaml`) — never
hardcoded. Aliases are bare UPPERCASE so both the Helm env and any ported v1 fixtures bind the
same names; `populate_by_name=True` also allows constructing by the snake_case field name
directly (unit tests do this).

`WORKER_ID` comes from the pod's `metadata.name` (Downward API) in production — DISTINCT per
replica: the engine uses `worker_id` as the lock owner, so two replicas sharing an id would
fight over the same lock (design §8). Falls back to a stable local default otherwise.
"""

from __future__ import annotations

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class WorkerRuntimeSettings(BaseSettings):
    """Config for the worker-runtime daemon — one process supervises the fetch-and-lock loop."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore", populate_by_name=True)

    # --- Identity (Helm injects) -------------------------------------------------------------
    tenant_id: str = Field(default="amh", alias="TENANT_ID")
    worker_id: str = Field(default="maezo-worker-local", alias="WORKER_ID")

    # --- External dependencies ----------------------------------------------------------------
    cibseven_base_url: str = Field(default="http://cibseven:8080/engine-rest", alias="CIBSEVEN_BASE_URL")
    # Service token for the worker->engine call (Bearer). Blocked seam: absent => no auth header
    # (the transport accepts None). Never hardcoded; comes from the secret store in prod.
    cibseven_auth_token: str | None = Field(default=None, alias="CIBSEVEN_AUTH_TOKEN")
    # Aurora/Postgres DSN for the durable audit sink (ADR-0007/ADR-0027, T1.10 T-D). Helm injects
    # it from the Aurora ExternalSecret (`deployment-worker-daemon.yaml`, key `database-url`), same
    # convention as `agent_runtime/settings.py`. FAIL-CLOSED: absent (`None`) means the composition
    # root CANNOT construct a `PostgresAuditSink`, so it never builds/starts the harness and
    # `audit_sink_ready` stays red — the daemon refuses to serve effect-producing traffic it could
    # not durably audit (design §7 T-D / Revision MUST-FIX 2). This is NOT a startup crash: the
    # health-first bring-up keeps `/healthz` green (liveness) while `/readyz` stays red.
    database_url: str | None = Field(default=None, alias="DATABASE_URL")
    kafka_bootstrap_servers: str = Field(default="localhost:9092", alias="KAFKA_BOOTSTRAP_SERVERS")
    # PHI pseudonymizer HMAC key (ADR-0006) — accepted for Helm/env parity (design §11 table);
    # not consumed by this build (no T1.1-registered worker publishes PHI-adjacent values to
    # Kafka yet). Kept so a later worker-egress seam can read it without a settings-surface change.
    phi_hmac_key: str | None = Field(default=None, alias="PHI_HMAC_KEY")

    # --- Fetch-and-lock loop parameters (WorkerHarness, design §11) ---------------------------
    lock_duration_ms: int = Field(default=30_000, alias="WORKER_LOCK_DURATION_MS")
    poll_interval_ms: int = Field(default=5_000, alias="WORKER_POLL_INTERVAL_MS")
    async_response_timeout_ms: int = Field(default=25_000, alias="WORKER_ASYNC_RESPONSE_TIMEOUT_MS")
    max_tasks_per_poll: int = Field(default=10, alias="WORKER_MAX_TASKS_PER_POLL")
    # Engine-side initial retry count (design §9) — now actually wired (v1's knob was dead).
    max_retry_attempts: int = Field(default=3, alias="WORKER_MAX_RETRY_ATTEMPTS")
    # httpx client timeout; MUST exceed the long-poll timeout or the client aborts its own
    # long-poll before the engine responds (design §6/§11 — validated below, fail-closed).
    client_timeout_s: float = Field(default=40.0, alias="WORKER_CLIENT_TIMEOUT_S")
    # Bound on graceful-shutdown drain (design §8) — MUST be less than the pod's
    # terminationGracePeriodSeconds (Helm-side contract, not enforced here).
    drain_deadline_s: float = Field(default=20.0, alias="WORKER_DRAIN_DEADLINE_S")

    # --- OTel (WIRED since AF-13 — `service.py` STEP 0 feeds `bootstrap_observability`) -------
    otel_exporter_otlp_endpoint: str | None = Field(default=None, alias="OTEL_EXPORTER_OTLP_ENDPOINT")

    # --- Operational ----------------------------------------------------------------------------
    health_port: int = Field(default=8000, alias="HEALTH_PORT")
    # Bound per dependency during bring-up (Kafka/engine probing) — bounded so a slow dependency
    # never hangs the boot sequence (health server binds first regardless, design §12).
    dep_connect_timeout_s: float = Field(default=5.0, alias="DEP_CONNECT_TIMEOUT_S")

    @field_validator(
        "lock_duration_ms",
        "poll_interval_ms",
        "async_response_timeout_ms",
        "max_tasks_per_poll",
        "max_retry_attempts",
        mode="after",
    )
    @classmethod
    def _positive(cls, value: int) -> int:
        if value <= 0:
            raise ValueError(f"must be a positive integer, got {value}")
        return value

    @model_validator(mode="after")
    def _validate_long_poll_fits_client_timeout(self) -> WorkerRuntimeSettings:
        """Fail-closed at startup (design §11): a misconfigured long-poll must not boot.

        The httpx client timeout must exceed the engine's `asyncResponseTimeout` by a safety
        margin, or the client aborts its own long-poll request before the engine can respond
        with an empty result — turning every idle poll into a spurious transport error.
        """
        margin_s = 2.0
        required_s = (self.async_response_timeout_ms / 1000.0) + margin_s
        if self.client_timeout_s <= required_s:
            raise ValueError(
                "client_timeout_s "
                f"({self.client_timeout_s}s) must exceed async_response_timeout_ms "
                f"({self.async_response_timeout_ms}ms) by at least {margin_s}s "
                f"(need > {required_s}s) — the httpx client would abort its own long-poll "
                "before the engine could respond."
            )
        return self

    def cibseven_auth_token_value(self) -> str | None:
        """The raw service token, or None if not injected (blocked seam)."""
        return self.cibseven_auth_token

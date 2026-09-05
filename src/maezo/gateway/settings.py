"""Injectable configuration for the gateway health-only daemon (T1.6).

`maezo.gateway` (PEP, pseudonymizer, audit) is the IN-PROCESS library every runtime pod
(agent-runtime, worker-daemon) imports directly (ADR-0006; see `docs/runbooks/gateway.md`'s
deployment-model note) — there is still no standalone gateway HTTP business surface. This
settings module is deliberately tiny: it exists only for the health/readiness entrypoint
(`__main__.py`/`service.py`) that closes the B1 CrashLoop hazard on `deployment-gateway.yaml`
(`command: ["python", "-m", "maezo.gateway"]`, which pointed at a package with no `__main__.py`).
"""

from __future__ import annotations

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from maezo.gateway.rate_limit import DEFAULT_CAPACITY, DEFAULT_REFILL_PER_SECOND


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

    # --- D6-01: effect-chokepoint rate limit (`gateway/rate_limit.py`) --------------------------
    # READ BY EVERY RUNTIME, NOT ONLY BY THE HEALTH DAEMON. This class's docstring above says the
    # module "exists only for the health/readiness entrypoint" — that is now one field-group out of
    # date, and deliberately so rather than silently: `maezo.gateway` is the IN-PROCESS library the
    # agent-runtime and worker-daemon pods import, `seams/_base.py::gate` runs inside THOSE
    # processes, and the limiter it consults is configured here because this is the gateway's one
    # sanctioned config surface. Putting the knobs on each root's settings class instead would give
    # the same chokepoint four different limits (and four chances to disagree).
    # The defaults are derived in `rate_limit.py`'s "Defaults" section from the worker daemon's own
    # `max_tasks_per_poll`/`poll_interval_ms`; they are not chosen by feel.
    rate_limit_capacity: int = Field(default=DEFAULT_CAPACITY, alias="MAEZO_GATEWAY_RATE_LIMIT_CAPACITY")
    rate_limit_refill_per_second: float = Field(
        default=DEFAULT_REFILL_PER_SECOND, alias="MAEZO_GATEWAY_RATE_LIMIT_REFILL_PER_SECOND"
    )

    @field_validator("rate_limit_capacity")
    @classmethod
    def _capacity_is_positive(cls, value: int) -> int:
        """A capacity of 0 is a chokepoint that refuses everything; a negative one is nonsense.

        Fail LOUD at settings construction rather than clamp: a deployment that typed `0` meant
        something, and quietly substituting a working number would hide a misconfiguration behind
        a system that appears to run correctly.

        LOUD IN EVERY PROCESS, not only in the health daemon that constructs this class at
        bring-up: `seams/_base.py::_rate_limiter` builds the limiter lazily on the first gated call
        in the agent-runtime and worker pods, and it re-raises this failure as
        `rate_limit.RateLimitConfigurationError` instead of installing the derived defaults
        (D6-01-F3). Without that, this docstring was true of one pod and false of the others.
        """
        if value < 1:
            raise ValueError(
                f"MAEZO_GATEWAY_RATE_LIMIT_CAPACITY must be >= 1, got {value} — a zero-capacity "
                "bucket refuses every effect call. To disable throttling, raise the limits; there "
                "is deliberately no off switch."
            )
        return value

    @field_validator("rate_limit_refill_per_second")
    @classmethod
    def _refill_is_positive(cls, value: float) -> float:
        """A refill of 0 is a bucket that never recovers — one burst and the principal is dead."""
        if value <= 0:
            raise ValueError(
                f"MAEZO_GATEWAY_RATE_LIMIT_REFILL_PER_SECOND must be > 0, got {value} — a bucket "
                "that never refills throttles the principal permanently after its first burst."
            )
        return value

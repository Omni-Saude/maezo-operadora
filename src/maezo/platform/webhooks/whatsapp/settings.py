"""Injectable configuration for the WhatsApp webhook receiver (T1.6, defect B1; T1.11 dispatch).

`app_secret` and `verify_token` are REQUIRED — no default (fail-closed, constraint 2). This
mirrors `deployment-webhook-receiver.yaml`'s own documented expectation ("Missing either raises
a pydantic ValidationError at startup -> CrashLoopBackOff" — `deployment-webhook-receiver.yaml:43-46`):
a webhook receiver that would silently accept an unverifiable signature (or hand out a
default-valued verify token) is a real security defect, not a convenience worth defaulting away.
Provisioning this secret (the ExternalSecret `maezo-whatsapp-config`) is an ops/deploy concern
outside T1.6's scope; local dev sets these via `docker-compose.yml` env directly.
"""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class WhatsAppWebhookSettings(BaseSettings):
    """Config for the webhook-receiver daemon — matches `deployment-webhook-receiver.yaml`'s env
    (`TENANT_ID`, `WHATSAPP_TOKEN`, `WHATSAPP_APP_SECRET`, `WHATSAPP_VERIFY_TOKEN`,
    `KAFKA_BOOTSTRAP_SERVERS`, `deployment-webhook-receiver.yaml:36-64`)."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore", populate_by_name=True)

    tenant_id: str = Field(default="amh", alias="TENANT_ID")

    # Meta app secret (HMAC-SHA256 signature validation, POST /webhook) — REQUIRED, no default.
    app_secret: str = Field(alias="WHATSAPP_APP_SECRET")
    # Meta verify token (GET /webhook handshake) — REQUIRED, no default.
    verify_token: str = Field(alias="WHATSAPP_VERIFY_TOKEN")
    # WABA send-side token — accepted for Helm/env parity; NOT consumed by this build (this
    # scaffold only receives; it never calls the WhatsApp Cloud API to send a message).
    whatsapp_token: str | None = Field(default=None, alias="WHATSAPP_TOKEN")

    # Accepted for Helm/env parity (deployment-webhook-receiver.yaml:60-64); NOT dialed by this
    # build — see service.py's module docstring for why (no downstream consumer yet, T1.11).
    kafka_bootstrap_servers: str = Field(default="localhost:9092", alias="KAFKA_BOOTSTRAP_SERVERS")

    # T1.11: the engine URL Helena's in-process dispatch needs (DMN evaluation + starting
    # SP-OP-ESCALATION-001) — mirrors `agent_runtime`/`worker_runtime`'s own `CIBSEVEN_BASE_URL`.
    cibseven_base_url: str = Field(default="http://cibseven:8080/engine-rest", alias="CIBSEVEN_BASE_URL")

    # T-C2 / T4b: the tenant Postgres DSN Helena's in-process dispatch needs — BOTH to construct
    # the durable ADR-0007 audit sink her escalation start (SP-OP-ESCALATION-001) fails-closed on
    # AND (T4b) to open the durable LangGraph checkpointer that makes multi-turn conversation state
    # survive across webhook invocations / receiver restarts. Unset here (no default) means the
    # dispatcher cannot be built (module docstring STEP A) and `/webhook` degrades to its explicit
    # 501 — Helena never starts an un-audited escalation, and never runs stateless in prod.
    # Provisioning the secret is an ops/deploy concern (mirrors `app_secret` above), outside scope.
    database_url: str | None = Field(default=None, alias="DATABASE_URL")

    # T4b F2 mode discriminator (mirrors `agent_runtime`'s `agent_runtime_mode`): "local" is the
    # ONLY non-production value. Anything else (Helm injects "kubernetes") is PRODUCTION, where a
    # durable checkpointer that fails to provision makes the receiver REFUSE to serve (no dispatcher
    # -> `/webhook` 501) rather than silently run Helena stateless. Local/dev falls back to an
    # in-memory checkpointer with a loud warning. Accepted for env parity (like `database_url`).
    runtime_mode: str = Field(default="local", alias="RUNTIME_MODE")

    # T4b: bounded timeout for the checkpointer connect+setup() at bring-up. Unlike the two
    # health-first daemons, this receiver binds its health server AFTER dependency bring-up, so a
    # hung Postgres connect must not stall the `/healthz` bind — mirrors the identically-named field
    # in `agent_runtime`/`worker_runtime` settings. A timeout is treated as a setup failure (prod
    # refuses to serve; local falls back to in-memory).
    dep_connect_timeout_s: float = Field(default=5.0, alias="DEP_CONNECT_TIMEOUT_S")

    # Helm's containerPort is a hardcoded 8080 (deployment-webhook-receiver.yaml:67-69), not env-
    # driven — HEALTH_PORT is accepted for local-dev override parity with the other two daemons.
    health_port: int = Field(default=8080, alias="HEALTH_PORT")

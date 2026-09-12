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

from typing import Literal

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from maezo.gateway.kafka_client import KafkaConnectionSettings


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
    kafka_auth_mode: Literal["development", "msk_iam"] = Field(default="development", alias="KAFKA_AUTH_MODE")
    kafka_aws_region: str | None = Field(default=None, alias="KAFKA_AWS_REGION")
    kafka_cluster_arn: str | None = Field(default=None, alias="KAFKA_CLUSTER_ARN")
    kafka_cluster_owner_account: str | None = Field(default=None, alias="KAFKA_CLUSTER_OWNER_ACCOUNT")
    kafka_task_role_arn: str | None = Field(default=None, alias="KAFKA_TASK_ROLE_ARN")

    # HAPI FHIR base URL for the DOSSIER delegation edges' read seams (CC-03/AND-03). Same name,
    # alias and default as `agent_runtime/settings.py::AgentRuntimeSettings.fhir_base_url` — the
    # two roots must not disagree about where FHIR is. Declared here rather than left to
    # `build_agent_fhir_seam`'s duck-typed `getattr` default so this daemon's FHIR endpoint is
    # VISIBLE on its settings surface and overridable per deployment. NOT Helm-injected today:
    # `deploy/helm/maezo-tenant/templates/deployment-worker-daemon.yaml` sets no `FHIR_BASE_URL`
    # (the default resolves in-cluster to the `hapi-fhir` Service — ADR-0021 /
    # `statefulset-hapi-fhir.yaml`); the ECS task definition DOES set it
    # (`deploy/aws-ecs/envs/dev-sa-east-1/service-worker.tf:56`). Design §11 records both.
    # EMPTY STRING = "this deployment has no
    # FHIR": the root then builds NO reader and both dossier graphs emit their disclosed gap note
    # (honest degradation, never a reader pointed at a fabricated host).
    fhir_base_url: str = Field(default="http://hapi-fhir:8080/fhir", alias="FHIR_BASE_URL")
    # PHI pseudonymizer HMAC key (ADR-0006) — accepted for Helm/env parity (design §11 table);
    # not consumed by this build (no T1.1-registered worker publishes PHI-adjacent values to
    # Kafka yet). Kept so a later worker-egress seam can read it without a settings-surface change.
    phi_hmac_key: str | None = Field(default=None, alias="PHI_HMAC_KEY")

    # --- Fetch-and-lock loop parameters (WorkerHarness, design §11) ---------------------------
    # GAP D4-03 (round-5, 2026-09-05): the register's concern is that this posture (30s lock / 5s
    # poll / 10 tasks per poll) could starve a cold topic behind a busy one once a single
    # worker-daemon serves 99+ topics. These four values are DELIBERATELY UNCHANGED here — the
    # measurement the register itself asks for (consumer-lag PER TOPIC, to see whether starvation
    # is actually happening before touching a knob that trades one failure mode for another) is
    # now POSSIBLE via the `kafka-exporter` scrape job (`deploy/observability/prometheus.yml`,
    # `kafka_consumergroup_lag_sum{consumergroup,topic}`) but has not been RUN against a
    # representative topic count — see `docs/runbooks/devops-stack.md` §"Kafka lag" for the
    # PromQL and the full decision note. Do not change these four values without that reading.
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

    # --- SC-06 / R-109: declared throughput-ceiling metadata (informational only) -------------
    # Import fan-in of `maezo.tools.mcp_cibseven.transport` / `maezo.tools.workers.harness`
    # (`deploy/helm/maezo-tenant/values.yaml`, section "Throughput ceilings"), surfaced in this
    # daemon's start-up log (`run()`, `worker_runtime_starting`) for ops visibility. `None` when
    # absent (dev/local) — NEVER used to gate or throttle anything; a missing declaration must
    # never fail this daemon closed, unlike the genuinely-required fields above.
    transport_fan_in_ceiling: int | None = Field(default=None, alias="MAEZO_TRANSPORT_FAN_IN_CEILING")
    harness_fan_in_ceiling: int | None = Field(default=None, alias="MAEZO_HARNESS_FAN_IN_CEILING")

    # --- WP-J1-03: exclusividade do topico do pedido de documentos ----------------------------
    # `operadora.auth.request_documents` e' servido OU pelo worker generico deste daemon
    # (`tools/workers/auth.py:RequestDocumentsWorker`, que so' registra em log) OU pela ponte
    # PHI dedicada (`gateway/document_requests`, que entrega de verdade ao prestador e ao
    # beneficiario — decisao #18 do dono). Os dois registrados significam dois fetch-and-lock
    # concorrentes na mesma tarefa externa, com a entrega decidida por corrida.
    #
    # Esta bandeira e' declarativa e EXPLICITA de proposito: nao ha inferencia a partir de
    # DATABASE_URL, de credenciais PHI presentes nem do modo de implantacao. Ligada, este
    # daemon deixa de registrar o topico e a ponte pode instalar-se; desligada (o padrao, e o
    # comportamento historico byte-a-byte) o worker generico continua registrado e a ponte
    # RECUSA instalar (`DocumentRequestHost.assert_exclusive`). Ligar a bandeira sem instalar
    # a ponte deixa o topico sem consumidor — visivel de imediato porque `/readyz` passa a
    # declarar o topico ausente do conjunto esperado e as instancias param em
    # ST_SolicitarDocumentos, nunca uma entrega silenciosamente perdida.
    document_request_host_installed: bool = Field(
        default=False, alias="MAEZO_AUTH_DOCUMENT_REQUEST_HOST"
    )

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

    def kafka_connection_settings(self) -> KafkaConnectionSettings:
        """Nonsecret explicit selection; no credential lookup during configuration."""
        return KafkaConnectionSettings(
            bootstrap_servers=self.kafka_bootstrap_servers,
            auth_mode=self.kafka_auth_mode,
            region=self.kafka_aws_region,
            cluster_arn=self.kafka_cluster_arn,
            cluster_owner_account=self.kafka_cluster_owner_account,
            task_role_arn=self.kafka_task_role_arn,
        )

    @model_validator(mode="after")
    def _validate_kafka_connection(self) -> WorkerRuntimeSettings:
        self.kafka_connection_settings()
        return self

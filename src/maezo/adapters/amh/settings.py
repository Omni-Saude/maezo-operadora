"""Env-driven configuration for the AMH boundary adapter — DECLARED, not wired (MZO-050a).

Mirrors the house settings shape exactly (`maezo.runtime.worker_runtime.settings`,
`maezo.platform.integrations.notifications_bridge.NotificationsBridgeSettings`):
`SettingsConfigDict(env_file=".env", extra="ignore", populate_by_name=True)`, bare UPPERCASE aliases
so Helm env and unit tests bind the same names, and fail-closed validators that refuse to construct
on a misconfiguration rather than booting into a half-configured consumer.

**Declaration only — nothing here is wired to anything.** No client is built, no broker is contacted,
no credential is read. Phase A ships the three provable layers (pin loader, mapping, this settings
surface) and deliberately NOT the consumer: `WorkItemSource.ack` may only report success on DURABLE
settlement, and ADR-0037 XRD-10 states outright that "offsets Kafka nunca representam conclusao de
negocio" — durable settlement needs the AMH inbox, which is MZO-060 (DBA-gated, and the latest
migration is `0006_*`). Shipping a consumer whose `ack` returned success without durability would
re-commit DL-0038's defect on the intake path. So these fields exist to be REVIEWED as the adapter's
configuration contract, and to be consumed by phase B without a settings-surface change.

**No secret has a usable default.** `MSK IAM/SASL` (ADR-0037 XRD-11) means the signer takes its
credentials from the workload identity, not from a field here; there is deliberately no password,
token or key on this surface for a secret to leak into a log or a `repr`. The bootstrap and registry
values are addresses, not credentials.

**No PHI.** No field names or carries a subject reference (ADR-0037 immutable prohibition #5).
"""

from __future__ import annotations

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from maezo.adapters.amh.contract import FROZEN_TOPICS

#: Topic names the pin freezes for the two AMH->Maezo intake streams and the Maezo->AMH egress
#: stream. Derived from the frozen catalogue rather than retyped, so a rename cannot be introduced
#: here without fighting `contract.py` (and, through it, the CI gate).
_WORK_ITEM_TOPIC: str = FROZEN_TOPICS[0][0]
_CONSENT_TOPIC: str = FROZEN_TOPICS[1][0]
_OUTCOME_TOPIC: str = FROZEN_TOPICS[2][0]


class AmhAdapterSettings(BaseSettings):
    """Configuration the AMH boundary adapter will need. Declared in phase A, consumed in phase B."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore", populate_by_name=True)

    # --- Broker (MSK) -------------------------------------------------------------------------
    # Reuses the repo-wide KAFKA_BOOTSTRAP_SERVERS alias (worker_runtime + notifications_bridge both
    # bind it) so a deployment configures ONE broker address, not one per component.
    kafka_bootstrap_servers: str = Field(default="localhost:9092", alias="KAFKA_BOOTSTRAP_SERVERS")
    # Consumer group. Fail-closed via the validator below: the placeholder default is refused for a
    # non-local bootstrap, because two components sharing a group silently split a partition set.
    consumer_group_id: str = Field(default="maezo-amh-local", alias="AMH_CONSUMER_GROUP_ID")
    # MSK IAM/SASL (XRD-11). Declared as a MODE, never as a credential: the signer resolves the
    # workload identity itself. `none` is legitimate only against a local/plaintext broker — the
    # model validator refuses it against a real bootstrap.
    security_protocol: str = Field(default="PLAINTEXT", alias="AMH_KAFKA_SECURITY_PROTOCOL")
    sasl_mechanism: str | None = Field(default=None, alias="AMH_KAFKA_SASL_MECHANISM")

    # --- Topics (pinned; overridable only for local isolation) --------------------------------
    work_item_topic: str = Field(default=_WORK_ITEM_TOPIC, alias="AMH_WORK_ITEM_TOPIC")
    consent_topic: str = Field(default=_CONSENT_TOPIC, alias="AMH_CONSENT_TOPIC")
    outcome_topic: str = Field(default=_OUTCOME_TOPIC, alias="AMH_OUTCOME_TOPIC")

    # --- Glue Schema Registry (coordinates only; no client, no credential) --------------------
    glue_region: str = Field(default="sa-east-1", alias="AMH_GLUE_REGION")
    glue_registry_name: str = Field(default="amh-fhir-dev", alias="AMH_GLUE_REGISTRY_NAME")

    # --- Contract pin ---------------------------------------------------------------------------
    # SAME env name the loader honours (`contract.MAEZO_AMH_CONTRACT_PIN_ENV`). Spelled as a LITERAL
    # because pydantic's `alias` must be one (mypy `literal-required`), so the constant cannot be
    # referenced here — `tests/unit/adapters/amh/test_settings.py` asserts the two are equal, which is
    # what stops them drifting. Two spellings of one override would be a configuration trap: the
    # settings object and the loader would read DIFFERENT pins, only one of them verified.
    # `None` means "resolve the default candidates" — the fail-closed path in
    # `resolve_contract_pin_path`, which raises when nothing resolves.
    contract_pin_path: str | None = Field(default=None, alias="MAEZO_AMH_CONTRACT_PIN")

    # --- Consumer loop bounds (phase B) ---------------------------------------------------------
    max_poll_records: int = Field(default=100, alias="AMH_MAX_POLL_RECORDS")
    poll_timeout_ms: int = Field(default=5_000, alias="AMH_POLL_TIMEOUT_MS")

    @field_validator("max_poll_records", "poll_timeout_ms", mode="after")
    @classmethod
    def _positive(cls, value: int) -> int:
        if value <= 0:
            raise ValueError(f"must be a positive integer, got {value}")
        return value

    @field_validator(
        "kafka_bootstrap_servers",
        "consumer_group_id",
        "work_item_topic",
        "consent_topic",
        "outcome_topic",
        "glue_region",
        "glue_registry_name",
        mode="after",
    )
    @classmethod
    def _non_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be blank")
        return value

    @field_validator("security_protocol", mode="after")
    @classmethod
    def _known_security_protocol(cls, value: str) -> str:
        """CLOSED set. An unrecognised protocol string would be silently ignored by a client library
        default, which is how a deployment ends up talking plaintext to a broker it believed was
        authenticated."""
        allowed = {"PLAINTEXT", "SSL", "SASL_PLAINTEXT", "SASL_SSL"}
        if value not in allowed:
            raise ValueError(f"must be one of {sorted(allowed)}, got {value!r}")
        return value

    @model_validator(mode="after")
    def _refuse_unauthenticated_remote_broker(self) -> AmhAdapterSettings:
        """Fail-closed: a non-local broker must be authenticated AND not share the local defaults.

        Two misconfigurations this refuses to boot with, both of which would otherwise look healthy:

        1. A real MSK bootstrap reached over `PLAINTEXT`/`SSL` with no SASL mechanism. XRD-11 requires
           MSK IAM/SASL; an unauthenticated connection to a real broker is a security regression that
           a running consumer would never report.
        2. A real bootstrap still carrying the placeholder consumer group. Group ids are the unit of
           partition assignment, so two environments sharing `maezo-amh-local` would silently steal
           each other's partitions — a data-loss-shaped failure with no error anywhere.
        """
        host = self.kafka_bootstrap_servers.split(":", 1)[0].strip().lower()
        is_local = host in {"localhost", "127.0.0.1", "::1", "kafka", "redpanda"}
        if is_local:
            return self

        if not self.security_protocol.startswith("SASL_") or not self.sasl_mechanism:
            raise ValueError(
                "a non-local kafka_bootstrap_servers requires a SASL security_protocol "
                "(SASL_SSL/SASL_PLAINTEXT) and a sasl_mechanism — ADR-0037 XRD-11 mandates MSK "
                f"IAM/SASL; got security_protocol={self.security_protocol!r}, "
                f"sasl_mechanism={self.sasl_mechanism!r}"
            )
        if self.consumer_group_id == "maezo-amh-local":
            raise ValueError(
                "consumer_group_id is still the local placeholder 'maezo-amh-local' against a "
                "non-local broker — set AMH_CONSUMER_GROUP_ID per environment so two deployments "
                "cannot silently share one partition assignment"
            )
        return self


__all__ = ["AmhAdapterSettings"]

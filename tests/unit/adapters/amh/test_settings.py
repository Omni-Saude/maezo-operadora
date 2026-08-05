"""Unit tests for `AmhAdapterSettings` — the DECLARED adapter configuration (MZO-050a).

Phase A wires nothing, so these tests prove the two things a declaration-only surface can be held to:
it matches the house settings shape exactly, and its fail-closed validators actually refuse the
misconfigurations they claim to (an unauthenticated real broker, a shared placeholder consumer group,
a non-positive bound, an unknown security protocol).
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError
from pydantic_settings import BaseSettings

from maezo.adapters.amh.contract import FROZEN_TOPICS, MAEZO_AMH_CONTRACT_PIN_ENV
from maezo.adapters.amh.settings import AmhAdapterSettings


def test_defaults_are_local_safe() -> None:
    settings = AmhAdapterSettings()
    assert settings.kafka_bootstrap_servers == "localhost:9092"
    assert settings.consumer_group_id == "maezo-amh-local"
    assert settings.security_protocol == "PLAINTEXT"
    assert settings.sasl_mechanism is None
    assert settings.contract_pin_path is None
    assert settings.max_poll_records == 100
    assert settings.poll_timeout_ms == 5_000


def test_topic_defaults_come_from_the_frozen_catalogue() -> None:
    """Not retyped: a rename would have to fight `contract.FROZEN_TOPICS` (and through it the CI gate)."""
    settings = AmhAdapterSettings()
    assert settings.work_item_topic == "amh.maezo.work-items.v1"
    assert settings.consent_topic == "amh.maezo.consent.v1"
    assert settings.outcome_topic == "maezo.amh.outcomes.v1"
    assert [settings.work_item_topic, settings.consent_topic, settings.outcome_topic] == [
        t[0] for t in FROZEN_TOPICS
    ]


def test_glue_defaults_match_the_pinned_registry() -> None:
    settings = AmhAdapterSettings()
    assert settings.glue_region == "sa-east-1"
    assert settings.glue_registry_name == "amh-fhir-dev"


# ---------------------------------------------------------------------------
# House shape
# ---------------------------------------------------------------------------


def test_matches_the_house_settings_shape() -> None:
    """Same `SettingsConfigDict` as `WorkerRuntimeSettings` / `NotificationsBridgeSettings`."""
    assert issubclass(AmhAdapterSettings, BaseSettings)
    config = AmhAdapterSettings.model_config
    assert config.get("env_file") == ".env"
    assert config.get("extra") == "ignore"
    assert config.get("populate_by_name") is True


def test_every_field_has_an_upper_snake_alias() -> None:
    for name, field in AmhAdapterSettings.model_fields.items():
        assert field.alias is not None, f"{name} has no env alias"
        assert field.alias == field.alias.upper(), f"{name}: alias {field.alias!r} is not UPPER_SNAKE"


def test_fields_bind_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KAFKA_BOOTSTRAP_SERVERS", "b-1.msk.sa-east-1.amazonaws.com:9098")
    monkeypatch.setenv("AMH_CONSUMER_GROUP_ID", "maezo-amh-prod")
    monkeypatch.setenv("AMH_KAFKA_SECURITY_PROTOCOL", "SASL_SSL")
    monkeypatch.setenv("AMH_KAFKA_SASL_MECHANISM", "AWS_MSK_IAM")
    monkeypatch.setenv("AMH_GLUE_REGION", "us-east-1")
    settings = AmhAdapterSettings()
    assert settings.kafka_bootstrap_servers == "b-1.msk.sa-east-1.amazonaws.com:9098"
    assert settings.consumer_group_id == "maezo-amh-prod"
    assert settings.glue_region == "us-east-1"


def test_populate_by_name_allows_snake_case_construction() -> None:
    settings = AmhAdapterSettings(consumer_group_id="maezo-amh-test")
    assert settings.consumer_group_id == "maezo-amh-test"


def test_the_pin_override_uses_the_same_env_name_as_the_loader() -> None:
    """Two spellings of one override would be a trap: the settings object and the loader would read
    DIFFERENT pins, and only one of them would have been verified."""
    alias = AmhAdapterSettings.model_fields["contract_pin_path"].alias
    assert alias == MAEZO_AMH_CONTRACT_PIN_ENV == "MAEZO_AMH_CONTRACT_PIN"


def test_the_pin_override_binds_from_that_env_var(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(MAEZO_AMH_CONTRACT_PIN_ENV, "/etc/maezo/contracts.lock.json")
    assert AmhAdapterSettings().contract_pin_path == "/etc/maezo/contracts.lock.json"


def test_no_field_looks_like_a_secret() -> None:
    """MSK IAM/SASL takes credentials from the workload identity (ADR-0037 XRD-11), so there is no
    field here for a secret to leak into a log or a `repr`."""
    forbidden = ("password", "secret", "token", "api_key", "private_key", "credential")
    for name in AmhAdapterSettings.model_fields:
        assert not any(fragment in name.lower() for fragment in forbidden), (
            f"{name} looks like a secret — this surface must carry addresses, not credentials"
        )


def test_no_field_is_phi_shaped() -> None:
    """ADR-0037 immutable prohibition #5."""
    fragments = ("cpf", "cns", "patient", "paciente", "beneficiario", "subject", "mpi", "nome")
    for name in AmhAdapterSettings.model_fields:
        assert not any(f in name.lower() for f in fragments), f"{name} is PHI-shaped"


# ---------------------------------------------------------------------------
# Fail-closed validators
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("field", ["max_poll_records", "poll_timeout_ms"])
@pytest.mark.parametrize("value", [0, -1, -1000])
def test_non_positive_bounds_are_refused(field: str, value: int) -> None:
    with pytest.raises(ValidationError, match="positive integer"):
        AmhAdapterSettings(**{field: value})


@pytest.mark.parametrize(
    "field",
    [
        "kafka_bootstrap_servers",
        "consumer_group_id",
        "work_item_topic",
        "consent_topic",
        "outcome_topic",
        "glue_region",
        "glue_registry_name",
    ],
)
@pytest.mark.parametrize("value", ["", "   ", "\t"])
def test_blank_required_strings_are_refused(field: str, value: str) -> None:
    with pytest.raises(ValidationError, match="must not be blank"):
        AmhAdapterSettings(**{field: value})


@pytest.mark.parametrize("protocol", ["SASL_SSL", "SASL_PLAINTEXT", "SSL", "PLAINTEXT"])
def test_known_security_protocols_are_accepted(protocol: str) -> None:
    settings = AmhAdapterSettings(
        security_protocol=protocol,
        sasl_mechanism="AWS_MSK_IAM" if protocol.startswith("SASL_") else None,
    )
    assert settings.security_protocol == protocol


@pytest.mark.parametrize("protocol", ["sasl_ssl", "SASL", "NONE", "TLS", ""])
def test_unknown_security_protocol_is_refused(protocol: str) -> None:
    """An unrecognised protocol string would be silently ignored by a client-library default — which is
    how a deployment ends up talking plaintext to a broker it believed was authenticated."""
    with pytest.raises(ValidationError, match="must be one of"):
        AmhAdapterSettings(security_protocol=protocol)


@pytest.mark.parametrize("protocol", ["PLAINTEXT", "SSL"])
def test_a_remote_broker_without_sasl_is_refused(protocol: str) -> None:
    """XRD-11 mandates MSK IAM/SASL. An unauthenticated connection to a real broker is a security
    regression a running consumer would never report."""
    with pytest.raises(ValidationError, match="MSK"):
        AmhAdapterSettings(
            kafka_bootstrap_servers="b-1.msk.sa-east-1.amazonaws.com:9098",
            consumer_group_id="maezo-amh-prod",
            security_protocol=protocol,
        )


def test_a_remote_broker_with_sasl_protocol_but_no_mechanism_is_refused() -> None:
    with pytest.raises(ValidationError, match="sasl_mechanism"):
        AmhAdapterSettings(
            kafka_bootstrap_servers="b-1.msk.sa-east-1.amazonaws.com:9098",
            consumer_group_id="maezo-amh-prod",
            security_protocol="SASL_SSL",
            sasl_mechanism=None,
        )


def test_a_remote_broker_keeping_the_placeholder_group_is_refused() -> None:
    """Group ids are the unit of partition assignment: two environments sharing `maezo-amh-local`
    would silently steal each other's partitions — data-loss-shaped, with no error anywhere."""
    with pytest.raises(ValidationError, match="placeholder"):
        AmhAdapterSettings(
            kafka_bootstrap_servers="b-1.msk.sa-east-1.amazonaws.com:9098",
            security_protocol="SASL_SSL",
            sasl_mechanism="AWS_MSK_IAM",
        )


def test_a_correctly_configured_remote_broker_is_accepted() -> None:
    """The negative control: the validator must not refuse a VALID production configuration."""
    settings = AmhAdapterSettings(
        kafka_bootstrap_servers="b-1.msk.sa-east-1.amazonaws.com:9098",
        consumer_group_id="maezo-amh-prod",
        security_protocol="SASL_SSL",
        sasl_mechanism="AWS_MSK_IAM",
    )
    assert settings.consumer_group_id == "maezo-amh-prod"


@pytest.mark.parametrize("host", ["localhost:9092", "127.0.0.1:9092", "kafka:9092", "redpanda:9092"])
def test_local_brokers_keep_the_plaintext_defaults(host: str) -> None:
    """Local/dev must stay frictionless — the fence is about REAL brokers."""
    settings = AmhAdapterSettings(kafka_bootstrap_servers=host)
    assert settings.security_protocol == "PLAINTEXT"


def test_extra_env_vars_are_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SOME_UNRELATED_VAR", "x")
    assert AmhAdapterSettings().consumer_group_id == "maezo-amh-local"

"""Unit tests for `maezo.runtime.agent_runtime.settings.AgentRuntimeSettings` (T1.6)."""

from __future__ import annotations

from maezo.runtime.agent_runtime.settings import AgentRuntimeSettings


def test_defaults() -> None:
    settings = AgentRuntimeSettings()
    assert settings.tenant_id == "amh"
    assert settings.agent_id == "helena"
    assert settings.agent_security_zone == "general"
    assert settings.agent_runtime_mode == "local"
    assert settings.agent_definition_path is None
    assert settings.kafka_bootstrap_servers == "localhost:9092"
    assert settings.cibseven_base_url == "http://cibseven:8080/engine-rest"
    assert settings.fhir_base_url == "http://hapi-fhir:8080/fhir"
    assert settings.health_port == 8000


def test_construct_by_env_alias() -> None:
    settings = AgentRuntimeSettings(
        TENANT_ID="omni",
        AGENT_ID="rafael",
        AGENT_SECURITY_ZONE="phi",
        AGENT_RUNTIME_MODE="kubernetes",
    )
    assert settings.tenant_id == "omni"
    assert settings.agent_id == "rafael"
    assert settings.agent_security_zone == "phi"
    assert settings.agent_runtime_mode == "kubernetes"


def test_construct_by_field_name_populate_by_name() -> None:
    settings = AgentRuntimeSettings(tenant_id="omni", agent_id="marina")
    assert settings.tenant_id == "omni"
    assert settings.agent_id == "marina"


def test_agent_definition_path_from_env() -> None:
    settings = AgentRuntimeSettings(AGENT_DEFINITION_PATH="/etc/maezo/agent/effective-agent-definition.yaml")
    assert settings.agent_definition_path == "/etc/maezo/agent/effective-agent-definition.yaml"

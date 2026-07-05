"""Unit tests for maezo.gateway.credential_vault — Credential Separation (ADR-0005).

TDD London School: tests written BEFORE implementation.
"""

import pytest

from maezo.gateway.credential_vault import (
    AgentCredentialView,
    CredentialVault,
    HumanCredentialPartition,
)


def test_credential_separation() -> None:
    """Agent credential view and human credential partition must be structurally
    separated — different objects with different access patterns."""
    vault = CredentialVault()

    # Store human-only credentials
    vault.store_human_credential(
        partition=HumanCredentialPartition.NEGATIVA,
        key="negativa_assinatura",
        value="chave-secreta-negativa-123",
    )

    # Store agent-level credentials
    vault.store_agent_credential(
        agent_id="helena",
        level="L2",
        key="whatsapp_api_key",
        value="wapp-key-456",
    )

    # Agent view should only see agent-level credentials
    agent_view = vault.get_agent_view(agent_id="helena")
    assert isinstance(agent_view, AgentCredentialView)
    assert agent_view.get("whatsapp_api_key") == "wapp-key-456"


def test_agent_cannot_access_human_credentials() -> None:
    """Agent MUST NOT be able to access human-restricted credentials
    (negativa, fraude — ADR-0005 structural guarantee)."""
    vault = CredentialVault()

    vault.store_human_credential(
        partition=HumanCredentialPartition.NEGATIVA,
        key="negativa_assinatura",
        value="chave-secreta-negativa-123",
    )
    vault.store_agent_credential(
        agent_id="helena",
        level="L2",
        key="whatsapp_api_key",
        value="wapp-key-456",
    )

    agent_view = vault.get_agent_view(agent_id="helena")

    # Agent cannot access human credential
    with pytest.raises(KeyError):
        agent_view.get("negativa_assinatura")

    # Agent cannot enumerate human credentials
    assert "negativa_assinatura" not in agent_view.list_keys()


def test_human_partition_access_requires_explicit_partition() -> None:
    """Human credentials must be accessed via explicit partition reference."""
    vault = CredentialVault()

    vault.store_human_credential(
        partition=HumanCredentialPartition.FRAUDE,
        key="fraude_investigacao_key",
        value="chave-investigacao-789",
    )

    # Retrieve via the correct partition
    cred = vault.get_human_credential(
        partition=HumanCredentialPartition.FRAUDE,
        key="fraude_investigacao_key",
    )
    assert cred == "chave-investigacao-789"


def test_vault_agent_credential_scope() -> None:
    """Agent A must not see Agent B's credentials."""
    vault = CredentialVault()

    vault.store_agent_credential(agent_id="helena", level="L2", key="api_key", value="helena-key")
    vault.store_agent_credential(agent_id="rafael", level="L2", key="api_key", value="rafael-key")

    helena_view = vault.get_agent_view(agent_id="helena")
    rafael_view = vault.get_agent_view(agent_id="rafael")

    assert helena_view.get("api_key") == "helena-key"
    assert rafael_view.get("api_key") == "rafael-key"
    assert helena_view.get("api_key") != rafael_view.get("api_key")


def test_human_credential_partition_enum() -> None:
    """HumanCredentialPartition must be a proper enum with the required partitions."""
    assert hasattr(HumanCredentialPartition, "NEGATIVA")
    assert hasattr(HumanCredentialPartition, "FRAUDE")

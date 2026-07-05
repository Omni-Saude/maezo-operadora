"""Credential Vault — structural separation of agent and human credentials (ADR-0005).

ADR-0005 mandates four independent HITL mechanisms. Mechanism #3 is credential
separation: the credential for a prohibited action must NOT exist in the agent's
runtime. This is enforced structurally — the AgentCredentialView CANNOT reach
credentials stored in a HumanCredentialPartition.

HumanCredentialPartition: NEGATIVA (coverage denial signing), FRAUDE (fraud
investigation). These partitions are only accessible via explicit human path.
"""

from __future__ import annotations

import enum

import structlog

logger = structlog.get_logger(__name__)


class HumanCredentialPartition(enum.Enum):
    """Partitions for human-restricted credentials.

    These credentials are structurally inaccessible to agents:
    - NEGATIVA: signing key for coverage denials (RN 259).
    - FRAUDE: fraud investigation credentials.
    """

    NEGATIVA = "negativa"
    FRAUDE = "fraude"


class AgentCredentialView:
    """Read-only view of credentials accessible to a specific agent.

    Agents can only see credentials explicitly assigned to their agent_id.
    Human-restricted credentials (NEGATIVA, FRAUDE) are NEVER visible here.
    """

    def __init__(self, agent_id: str, credentials: dict[str, str]) -> None:
        """Initialize the agent credential view.

        Args:
            agent_id: The agent identifier.
            credentials: Dict of key → value for this agent's credentials.
        """
        self._agent_id = agent_id
        self._credentials: dict[str, str] = dict(credentials)
        logger.debug("agent_credential_view_created", agent_id=agent_id)

    def get(self, key: str) -> str:
        """Retrieve a credential by key.

        Args:
            key: The credential key.

        Returns:
            The credential value.

        Raises:
            KeyError: If the credential key is not found.
        """
        if key not in self._credentials:
            raise KeyError(f"Credential '{key}' not found for agent '{self._agent_id}'")
        return self._credentials[key]

    def list_keys(self) -> frozenset[str]:
        """Return all credential keys visible to this agent.

        Returns:
            Immutable set of credential keys.
        """
        return frozenset(self._credentials.keys())


class CredentialVault:
    """Central credential store with structural agent/human separation.

    Agent credentials are scoped per agent_id. Human credentials are stored
    in explicit partitions (NEGATIVA, FRAUDE) that are NEVER exposed through
    the agent view — ensuring ADR-0005's structural guarantee.
    """

    def __init__(self) -> None:
        """Initialize an empty credential vault."""
        self._agent_credentials: dict[str, dict[str, str]] = {}
        self._human_credentials: dict[HumanCredentialPartition, dict[str, str]] = {
            partition: {} for partition in HumanCredentialPartition
        }
        logger.info("credential_vault_initialized")

    def store_agent_credential(
        self,
        agent_id: str,
        level: str,
        key: str,
        value: str,
    ) -> None:
        """Store a credential scoped to a specific agent.

        Args:
            agent_id: The agent identifier.
            level: Autonomy level of the agent (L0-L3).
            key: The credential key.
            value: The credential value (secret — never logged).
        """
        if agent_id not in self._agent_credentials:
            self._agent_credentials[agent_id] = {}
        self._agent_credentials[agent_id][key] = value
        logger.debug(
            "agent_credential_stored",
            agent_id=agent_id,
            level=level,
            key=key,
        )

    def store_human_credential(
        self,
        partition: HumanCredentialPartition,
        key: str,
        value: str,
    ) -> None:
        """Store a credential in a human-restricted partition.

        These credentials are NEVER exposed through AgentCredentialView.

        Args:
            partition: The human credential partition (NEGATIVA or FRAUDE).
            key: The credential key.
            value: The credential value (secret — never logged).
        """
        self._human_credentials[partition][key] = value
        logger.debug(
            "human_credential_stored",
            partition=partition.value,
            key=key,
        )

    def get_agent_view(self, agent_id: str) -> AgentCredentialView:
        """Return a read-only view of credentials visible to the agent.

        Only includes credentials explicitly stored for this agent_id.
        Human-restricted partitions are NEVER included.

        Args:
            agent_id: The agent identifier.

        Returns:
            AgentCredentialView scoped to this agent.
        """
        creds = self._agent_credentials.get(agent_id, {})
        return AgentCredentialView(agent_id=agent_id, credentials=creds)

    def get_human_credential(
        self,
        partition: HumanCredentialPartition,
        key: str,
    ) -> str:
        """Retrieve a human-restricted credential by partition and key.

        Only accessible through explicit human path — NEVER exposed via
        AgentCredentialView.

        Args:
            partition: The human credential partition.
            key: The credential key.

        Returns:
            The credential value.

        Raises:
            KeyError: If the credential is not found.
        """
        if key not in self._human_credentials[partition]:
            raise KeyError(f"Human credential '{key}' not found in partition '{partition.value}'")
        return self._human_credentials[partition][key]

""" "AgentCard — A2A identity and capability card (ADR-0003, ADR-0015).

An AgentCard declares an agent's identity, capabilities, endpoint, and public key
for A2A delegation. It can be serialized to/from dict for transport and storage.

Per ADR-0015, the AgentCard is derived from AgentDefinition via the harness
(not configured standalone). The `from_definition()` classmethod provides this.
"""

from __future__ import annotations

from typing import Any


class AgentCard:
    """A2A Agent Card carrying identity, capabilities, and connection info.

    Attributes:
        agent_id: Unique agent identifier (e.g. 'helena', 'rafael').
        capabilities: List of task types / capabilities this agent accepts.
        endpoint: A2A endpoint URL for this agent.
        public_key: Public key material for mTLS / signed delegation.
    """

    def __init__(
        self,
        agent_id: str,
        capabilities: list[str],
        endpoint: str,
        public_key: str,
    ) -> None:
        self.agent_id = agent_id
        self.capabilities = list(capabilities)
        self.endpoint = endpoint
        self.public_key = public_key

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a dictionary suitable for JSON transport.

        Returns:
            Dict with agent_id, capabilities, endpoint, public_key.
        """
        return {
            "agent_id": self.agent_id,
            "capabilities": list(self.capabilities),
            "endpoint": self.endpoint,
            "public_key": self.public_key,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AgentCard:
        """Deserialize from a dictionary.

        Args:
            data: Dict with agent_id, capabilities, endpoint, public_key keys.

        Returns:
            A new AgentCard instance.
        """
        return cls(
            agent_id=data["agent_id"],
            capabilities=list(data.get("capabilities", [])),
            endpoint=data["endpoint"],
            public_key=data["public_key"],
        )

    def __repr__(self) -> str:
        return f"AgentCard(agent_id={self.agent_id!r}, capabilities={self.capabilities!r})"

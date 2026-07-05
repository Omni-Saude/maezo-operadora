"""M4 Gateway & Security Core — PEP, Pseudonymizer, AuditSink, CredentialVault, CustodyBundle.

Provides the security boundary between agents and the outside world:
- PEP: Policy Enforcement Point evaluating the autonomy matrix (ADR-0005, 0008)
- Pseudonymizer: PHI pseudonymization via SHA-256 (ADR-0006)
- AuditSink: hash-linked audit chain, anti-fork (ADR-0007, DL-0018)
- CredentialVault: structural separation of agent and human credentials (ADR-0005)
- CustodyBundle: Merkle-root tamper-evident chain of custody (ADR-0020)
"""

from maezo.gateway.audit import AuditRecord, AuditSink
from maezo.gateway.credential_vault import (
    AgentCredentialView,
    CredentialVault,
    HumanCredentialPartition,
)
from maezo.gateway.custody import CustodyBundle
from maezo.gateway.pep import PEP, Decision
from maezo.gateway.pseudonymizer import PHI_FIELDS, Pseudonymizer

__all__ = [
    "PEP",
    "Decision",
    "Pseudonymizer",
    "PHI_FIELDS",
    "AuditRecord",
    "AuditSink",
    "CredentialVault",
    "AgentCredentialView",
    "HumanCredentialPartition",
    "CustodyBundle",
]

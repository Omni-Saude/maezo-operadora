"""M4 Gateway & Security Core — PEP, Pseudonymizer, AuditSink, CredentialVault, CustodyBundle.

Provides the security boundary between agents and the outside world:
- PEP: Policy Enforcement Point evaluating the autonomy matrix (ADR-0005, 0008)
- Pseudonymizer: PHI pseudonymization via SHA-256 (ADR-0006)
- AuditSink: hash-linked audit chain, anti-fork (ADR-0007, DL-0018)
- PostgresAuditSink: durable, fail-closed Postgres-backed audit chain (T1.10)
- CredentialVault: structural separation of agent and human credentials (ADR-0005)
- CustodyBundle: Merkle-root tamper-evident chain of custody (ADR-0020)

`PostgresAuditSink` is NOT re-exported here: it pulls in `asyncpg` at import time, which the
in-memory `AuditSink` path does not need. Import it directly from
`maezo.gateway.audit_postgres` where it is actually wired (avoids an unconditional hard
dependency on asyncpg for every consumer of this package).
"""

from maezo.gateway.audit import AuditRecord, AuditSink
from maezo.gateway.credential_vault import (
    AgentCredentialView,
    CredentialVault,
    HumanCredentialPartition,
)
from maezo.gateway.custody import CustodyBundle
from maezo.gateway.log_scrubber import LogScrubber
from maezo.gateway.pep import (
    HARD_ACTIONS,
    PEP,
    ActionPolicy,
    AutonomyMatrix,
    Decision,
    Level,
    PolicyError,
    build_pep,
    load_matrix,
)
from maezo.gateway.pseudonymizer import PHI_FIELDS, Pseudonymizer

__all__ = [
    "PEP",
    "Decision",
    "Level",
    "HARD_ACTIONS",
    "ActionPolicy",
    "AutonomyMatrix",
    "PolicyError",
    "build_pep",
    "load_matrix",
    "Pseudonymizer",
    "PHI_FIELDS",
    "LogScrubber",
    "AuditRecord",
    "AuditSink",
    "CredentialVault",
    "AgentCredentialView",
    "HumanCredentialPartition",
    "CustodyBundle",
]

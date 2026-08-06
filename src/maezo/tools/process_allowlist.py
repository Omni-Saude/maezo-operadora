"""Process Key Allowlist — validates process keys against known universe (ADR-0016).

ADR-0016 mandates structural enforcement of process_key validation:
- KNOWN_PROCESS_KEYS is a frozen set of all 15 SP-OP-* keys the platform knows.
- DEFAULT_ALLOWED_PROCESS_KEYS is the subset every tenant can use.
- ensure_allowed() validates format and membership, raising ProcessKeyNotAllowedError
  (a PermissionError subclass) on failure.
- Keys must match the SP-OP-<DOMAIN>-<NNN> regex (defense against homoglyphs/encoding).
"""

from __future__ import annotations

import re
from typing import Final

import structlog

logger = structlog.get_logger(__name__)

# Universe of all known process keys (ADR-0016: 15 SP-OP-* keys)
KNOWN_PROCESS_KEYS: Final[frozenset[str]] = frozenset(
    {
        # Phase 0-1: governance processes
        "SP-OP-ESCALATION-001",
        "SP-OP-LGPD-DSR-001",
        "SP-OP-AUTH-001",
        # Phase 2: core compliance processes
        "SP-OP-CONTAS-001",
        "SP-OP-RECURSO-001",
        "SP-OP-NIP-001",
        "SP-OP-ANS-SUBMIT-001",
        "SP-OP-CANCEL-001",
        "SP-OP-REEMBOLSO-001",
        # Phase 3: advanced processes
        "SP-OP-INADIMPLENCIA-001",
        "SP-OP-CRED-001",
        "SP-OP-ADEQUACAO-001",
        "SP-OP-FRAUDE-001",
        "SP-OP-PROGRAMA-001",
        "SP-OP-PAGTO-001",
    }
)

# Default allowed keys for every tenant (equals KNOWN_PROCESS_KEYS in Phase 0-3)
#
# ⚠️ GAP-AUTH-4 / GK-autol2 finding 2 — THIS DEFAULT IS NOW LOAD-BEARING, and it was not before.
# `docs/review-queue.md` already carries an SME recommendation that Phase 0/1 restrict
# agent-startable keys to {SP-OP-ESCALATION-001, SP-OP-LGPD-DSR-001}, on the reasoning that
# "iniciar AUTH-001 por agente nao deveria ser L2". That recommendation was INERT while the
# SP-OP-AUTH-001 auto route minted nothing (it published desfecho=aprovada_automatica with no
# numero_autorizacao). Since item-9 auth Class-A the route emits a REAL TISS numero_autorizacao,
# decided by `dut_atendida`/`dentro_teto_l2`/`rede_credenciada` taken verbatim from the start
# payload (GAP-AUTH-4: no worker computes them, and the tenant ceiling is not consulted on this
# route). Consequence: this frozenset is the only place that decides whether an agent can open
# that route at all. Narrowing it is a behaviour change with its own blast radius — recorded for
# the owner/SME decision, deliberately NOT changed here.
DEFAULT_ALLOWED_PROCESS_KEYS: Final[frozenset[str]] = KNOWN_PROCESS_KEYS

# Regex for valid process key format: SP-OP-<DOMAIN>-<NNN>
_PROCESS_KEY_PATTERN: Final[re.Pattern[str]] = re.compile(r"^SP-OP-[A-Z]+(?:-[A-Z]+)*-[0-9]{3}$")


class ProcessKeyNotAllowedError(PermissionError):
    """Raised when a process_key is not in the allowlist or has invalid format.

    Subclasses PermissionError so the ToolRegistry can catch it uniformly
    and audit the refusal as TOOL:deny:<tipo>:<mensagem> (ADR-0007).
    """

    def __init__(self, process_key: str, reason: str = "") -> None:
        self.process_key = process_key
        self.reason = reason
        msg = f"Process key '{process_key}' is not allowed"
        if reason:
            msg += f": {reason}"
        super().__init__(msg)


class ProcessAllowlist:
    """Validates process keys against the known universe and tenant configuration.

    Typical usage:
        ensure_allowed("SP-OP-AUTH-001")   # OK
        ensure_allowed("SP-OP-FAKE-999")   # raises ProcessKeyNotAllowedError

    Tenant-specific configuration can add keys within KNOWN_PROCESS_KEYS via
    tenant_allowed_keys, but can never remove default keys.
    """

    def __init__(
        self,
        tenant_allowed_keys: frozenset[str] | set[str] | None = None,
    ) -> None:
        """Initialize the allowlist.

        Args:
            tenant_allowed_keys: Optional extra keys to allow (must be subset
                of KNOWN_PROCESS_KEYS). Default keys are always included.
        """
        self._allowed: frozenset[str] = DEFAULT_ALLOWED_PROCESS_KEYS

        if tenant_allowed_keys:
            # Validate that all tenant keys are in KNOWN_PROCESS_KEYS
            unknown = set(tenant_allowed_keys) - KNOWN_PROCESS_KEYS
            if unknown:
                raise ProcessKeyNotAllowedError(
                    process_key=", ".join(sorted(unknown)),
                    reason="keys not in KNOWN_PROCESS_KEYS universe",
                )
            self._allowed = self._allowed | frozenset(tenant_allowed_keys)

        logger.debug(
            "process_allowlist_initialized",
            allowed_count=len(self._allowed),
        )

    def ensure_allowed(self, process_key: str) -> None:
        """Validate a process key. Raises ProcessKeyNotAllowedError if invalid.

        Checks:
        1. Format: must match SP-OP-<DOMAIN>-<NNN> pattern.
        2. Membership: must be in the allowed set.

        Args:
            process_key: The key to validate.

        Raises:
            ProcessKeyNotAllowedError: If the key is not valid or not allowed.
        """
        # Validate format (defense against homoglyphs/encoding attacks)
        if not _PROCESS_KEY_PATTERN.match(process_key):
            raise ProcessKeyNotAllowedError(
                process_key=process_key,
                reason="invalid format; must match SP-OP-<DOMAIN>-<NNN>",
            )

        # Validate membership
        if process_key not in self._allowed:
            raise ProcessKeyNotAllowedError(
                process_key=process_key,
                reason="not in allowed set",
            )


# Singleton instance for the default allowlist (per ADR-0016)
_default_allowlist = ProcessAllowlist()


def ensure_allowed(process_key: str) -> None:
    """Validate a process key against the default allowlist.

    Convenience function that delegates to the singleton ProcessAllowlist.
    Raises ProcessKeyNotAllowedError if the key is invalid or not allowed.

    Args:
        process_key: The key to validate.

    Raises:
        ProcessKeyNotAllowedError: If the key is not valid or not allowed.
    """
    _default_allowlist.ensure_allowed(process_key)

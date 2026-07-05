"""PHI Pseudonymization via SHA-256 (ADR-0006).

Pseudonymizes protected health information (PHI) fields before data leaves the
PHI zone. Uses deterministic SHA-256 hashing so the same input always produces
the same pseudonym — enabling correlation without exposing raw PHI.

PHI_FIELDS is an immutable frozenset (CI-enforced, not YAML-configurable).
"""

from __future__ import annotations

import hashlib
from typing import Any

import structlog

logger = structlog.get_logger(__name__)

# PHI fields that must be pseudonymized — immutable frozenset (CI-enforced)
PHI_FIELDS: frozenset[str] = frozenset(
    {
        "cpf",
        "nome",
        "telefone",
        "email",
    }
)


class Pseudonymizer:
    """Pseudonymizes PHI fields using deterministic SHA-256 hashing.

    Typical usage:
        p = Pseudonymizer()
        safe = p.pseudonymize({"cpf": "12345678901", "procedimento": "consulta"})
        # safe["cpf"] = "abc123..." (64-char hex digest)
        # safe["procedimento"] = "consulta" (unchanged)
    """

    def pseudonymize(self, data: dict[str, Any]) -> dict[str, Any]:
        """Replace PHI fields with deterministic SHA-256 pseudonyms.

        Non-PHI fields are returned unchanged. Empty/None PHI fields are
        preserved as-is (no crash, no spurious pseudonym generation).

        Args:
            data: Input dict potentially containing PHI fields.

        Returns:
            New dict with PHI fields replaced by SHA-256 hex digests.
        """
        result: dict[str, Any] = {}
        for key, value in data.items():
            if key in PHI_FIELDS:
                if value is not None and value != "":
                    digest = hashlib.sha256(str(value).encode("utf-8")).hexdigest()
                    result[key] = digest
                    logger.debug("phi_pseudonymized", field=key)
                else:
                    # Preserve empty/None as-is (no data to pseudonymize)
                    result[key] = value
            else:
                result[key] = value
        return result

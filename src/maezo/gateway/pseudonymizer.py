"""PHI Pseudonymization via KEYED HMAC-SHA256 (ADR-0006, ADR-0035).

Pseudonymizes protected health information (PHI) fields before data leaves the
PHI zone. Uses deterministic **keyed** HMAC-SHA256 so the same (key, input) pair
always produces the same pseudonym — enabling correlation without exposing raw PHI.

Why keyed (ADR-0035): a bare SHA-256 of a low-entropy identifier (a CPF is ~10^9
valid values after the check digits) is trivially reversible via a precomputed
table, so an unkeyed digest is NOT an LGPD-grade pseudonym — the mapping must be
infeasible to reverse without a secret. HMAC with a vault-injected key makes the
pseudonym irreversible to anyone who does not hold `PHI_HMAC_KEY`.

Fail-closed policy (ADR-0035, mirrors `webhooks/service.py`'s DATABASE_URL fence
and `runtime/inference.py`'s "no silent fallback"):
  - key present               -> keyed HMAC with the real (vault-synced) key.
  - production, key ABSENT     -> `PseudonymizerKeyMissingError` (fail-closed): a
                                 prod pod must NEVER fall back to a deterministic/
                                 reversible pseudonym.
  - dev/CI, key ABSENT         -> non-secret, deterministic per-tenant DEV key +
                                 a LOUD warning (".env vazia em dev = chave
                                 determinística por tenant — NÃO secreta").

Even the DEV fallback is HMAC (never plain SHA-256), so the reversibility weakness
is closed in every mode — in dev it is only "reversible by anyone who knows the
public dev key", which is acceptable for non-PHI local data.

PHI_FIELDS is an immutable frozenset (CI-enforced, not YAML-configurable).
"""

from __future__ import annotations

import hashlib
import hmac
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

# Non-secret, deterministic dev/CI fallback root. NEVER used in production (from_settings
# fail-closes there). Its only purpose is to give dev/CI a stable, keyed (NOT plain-SHA-256)
# pseudonym without provisioning a real secret — matching `.env.example`'s
# "vazia em dev = chave determinística por tenant — NÃO secreta".
_DEV_FALLBACK_ROOT: bytes = b"maezo-dev-phi-hmac-nonsecret-v2"


def _derive_dev_key(tenant_id: str) -> bytes:
    """Deterministic, non-secret per-tenant DEV key (dev/CI only, never production)."""
    return hmac.new(_DEV_FALLBACK_ROOT, tenant_id.encode("utf-8"), hashlib.sha256).digest()


class PseudonymizerKeyMissingError(RuntimeError):
    """Raised when a PRODUCTION Pseudonymizer is built without a PHI_HMAC_KEY (fail-closed).

    A production pod must not silently fall back to an unkeyed/deterministic pseudonym: that
    would ship the reversible weakness ADR-0035 exists to close. Provision the vault-synced
    `PHI_HMAC_KEY` (ExternalSecret `phi-hmac-key`, §6.2) before serving PHI-adjacent traffic.
    """


class Pseudonymizer:
    """Pseudonymizes PHI fields using deterministic **keyed** HMAC-SHA256.

    Prefer the `from_settings` factory in composition roots — it enforces the ADR-0035
    fail-closed policy. The bare constructor is for explicit key injection (and for the
    dev/CI/tests default, which uses a non-secret deterministic DEV key — never plain SHA-256).

    Typical usage:
        p = Pseudonymizer.from_settings(
            phi_hmac_key=settings.phi_hmac_key,
            production=settings.is_production(),
            tenant_id=settings.tenant_id,
        )
        safe = p.pseudonymize({"cpf": "12345678901", "procedimento": "consulta"})
        # safe["cpf"] = "abc123..." (64-char HMAC-SHA256 hex digest)
        # safe["procedimento"] = "consulta" (unchanged)
    """

    def __init__(self, key: bytes | None = None) -> None:
        """Build a Pseudonymizer around an HMAC key.

        Args:
            key: The HMAC-SHA256 secret. ``None`` selects a non-secret, deterministic DEV key
                (dev/CI/tests only) — NEVER plain SHA-256. Production composition roots must go
                through `from_settings`, which fail-closes on an absent key.
        """
        self._key: bytes = key if key is not None else _derive_dev_key("__dev__")

    @classmethod
    def from_settings(cls, *, phi_hmac_key: str | None, production: bool, tenant_id: str) -> Pseudonymizer:
        """Construct per the ADR-0035 fail-closed policy from runtime settings.

        Args:
            phi_hmac_key: The vault-synced `PHI_HMAC_KEY` (None/empty when not provisioned).
            production: True in a real (non-"local") runtime mode. Discriminator threaded from
                the daemon's settings (mirrors `agent_runtime_mode != "local"`).
            tenant_id: Tenant scope for the deterministic DEV fallback key.

        Returns:
            A keyed Pseudonymizer.

        Raises:
            PseudonymizerKeyMissingError: production mode with an absent/empty key (fail-closed).
        """
        if phi_hmac_key:
            return cls(key=phi_hmac_key.encode("utf-8"))
        if production:
            raise PseudonymizerKeyMissingError(
                "PHI_HMAC_KEY is absent but the runtime is in production mode: refusing to "
                "construct a Pseudonymizer that would fall back to a deterministic/reversible "
                "pseudonym (ADR-0035 fail-closed). Provision the vault-synced PHI_HMAC_KEY "
                "(ExternalSecret `phi-hmac-key`, §6.2) before serving PHI-adjacent traffic."
            )
        logger.warning(
            "phi_pseudonymizer_dev_fallback_key",
            tenant=tenant_id,
            reason=(
                "PHI_HMAC_KEY empty — using a non-secret deterministic per-tenant DEV key "
                "(dev/CI only; NEVER production). Pseudonyms are keyed HMAC, not plain SHA-256."
            ),
        )
        return cls(key=_derive_dev_key(tenant_id))

    def pseudonymize(self, data: dict[str, Any]) -> dict[str, Any]:
        """Replace PHI fields with deterministic keyed HMAC-SHA256 pseudonyms.

        Non-PHI fields are returned unchanged. Empty/None PHI fields are
        preserved as-is (no crash, no spurious pseudonym generation).

        Args:
            data: Input dict potentially containing PHI fields.

        Returns:
            New dict with PHI fields replaced by HMAC-SHA256 hex digests.
        """
        result: dict[str, Any] = {}
        for key, value in data.items():
            if key in PHI_FIELDS:
                if value is not None and value != "":
                    digest = hmac.new(self._key, str(value).encode("utf-8"), hashlib.sha256).hexdigest()
                    result[key] = digest
                    logger.debug("phi_pseudonymized", field=key)
                else:
                    # Preserve empty/None as-is (no data to pseudonymize)
                    result[key] = value
            else:
                result[key] = value
        return result

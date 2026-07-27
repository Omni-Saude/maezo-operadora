"""Log Scrubber — PHI removal from logs before write (ADR-0006 §logs).

Provides a structlog processor that pseudonymizes sensitive fields before they
are serialized and written to any log sink. Integrates with the existing
Pseudonymizer to reuse the same PHI field definitions.

Usage with structlog (composition root — fail-closed keyed instance):
    scrubber = LogScrubber.from_settings(
        phi_hmac_key=settings.phi_hmac_key,
        production=settings.is_production(),
        tenant_id=settings.tenant_id,
    )
    structlog.configure(processors=[scrubber, ...])

The scrubber is a callable structlog processor that intercepts the event_dict,
replaces PHI field values with keyed HMAC-SHA256 pseudonyms (ADR-0035), and
returns the cleaned dict.
"""

from __future__ import annotations

from typing import Any

from maezo.gateway.pseudonymizer import PHI_FIELDS, Pseudonymizer


class LogScrubber:
    """Structlog processor that pseudonymizes PHI fields in log events.

    Reuses Pseudonymizer for deterministic keyed HMAC-SHA256 hashing, ensuring
    that log scrubbing is consistent with the gateway's pseudonymization.

    Typical usage:
        import structlog
        scrubber = LogScrubber.from_settings(phi_hmac_key=key, production=prod, tenant_id=tid)
        structlog.configure(processors=[scrubber, structlog.processors.JSONRenderer()])
        log = structlog.get_logger()
        log.info("user_action", cpf="12345678901")  # cpf replaced by keyed HMAC hex digest

    PHI_FIELDS from pseudonymizer are the canonical set — the scrubber does
    NOT maintain a separate list.
    """

    def __init__(self, pseudonymizer: Pseudonymizer) -> None:
        # The keyed pseudonymizer (ADR-0035) is REQUIRED — there is no bare-ctor default. The
        # previous `pseudonymizer=None -> Pseudonymizer()` fallback silently minted a non-secret
        # deterministic DEV key EVEN IN PRODUCTION (fail-OPEN): a prod log config that forgot to
        # inject a keyed instance would have scrubbed PHI with a publicly-known dev key. Removing
        # the default forces composition roots to be explicit; use `LogScrubber.from_settings(...)`
        # to inherit the gateway's ADR-0035 fail-closed policy (prod + absent key -> raise), or
        # pass an explicit `Pseudonymizer()` for dev/CI/tests (non-secret, deterministic, still
        # keyed HMAC — never plain SHA-256).
        self._pseudonymizer = pseudonymizer

    @classmethod
    def from_settings(cls, *, phi_hmac_key: str | None, production: bool, tenant_id: str) -> LogScrubber:
        """Build a LogScrubber under the ADR-0035 fail-closed policy (the composition-root path).

        Delegates key selection to `Pseudonymizer.from_settings`, so a production `runtime_mode`
        with an absent/blank `PHI_HMAC_KEY` raises `PseudonymizerKeyMissingError` here (fail-closed)
        rather than scrubbing logs with a reversible/non-secret pseudonym — matching the gateway
        and the webhook dispatcher. This closes the latent fail-open the bare `LogScrubber()`
        default carried (it is not yet wired into `platform/observability.py::structlog.configure`,
        so the hole is pre-emptive, not active — but it is now impossible to reintroduce).
        """
        return cls(
            Pseudonymizer.from_settings(phi_hmac_key=phi_hmac_key, production=production, tenant_id=tenant_id)
        )

    def __call__(self, logger: Any, method_name: str, event_dict: dict[str, Any]) -> dict[str, Any]:
        """Process a log event dict, scrubbing PHI fields.

        Args:
            logger: The structlog logger instance (unused).
            method_name: The log method name (e.g. 'info', 'error') (unused).
            event_dict: The log event dictionary to scrub.

        Returns:
            The event_dict with PHI fields pseudonymized.
        """
        return self._pseudonymizer.pseudonymize(event_dict)

    def scrub_dict(self, data: dict[str, Any]) -> dict[str, Any]:
        """Scrub a generic dictionary of PHI fields (for programmatic use).

        Args:
            data: Input dict potentially containing PHI fields.

        Returns:
            New dict with PHI fields replaced by keyed HMAC-SHA256 hex digests.
        """
        return self._pseudonymizer.pseudonymize(data)

    @staticmethod
    def has_phi(data: dict[str, Any]) -> bool:
        """Check if a dictionary contains any PHI field keys.

        Args:
            data: Input dict to check.

        Returns:
            True if any key is a recognized PHI field.
        """
        return bool(set(data.keys()) & PHI_FIELDS)

"""Log Scrubber — PHI removal from logs before write (ADR-0006 §logs).

Provides a structlog processor that pseudonymizes sensitive fields before they
are serialized and written to any log sink. Integrates with the existing
Pseudonymizer to reuse the same PHI field definitions.

Usage with structlog:
    structlog.configure(processors=[LogScrubber(), ...])

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
        structlog.configure(processors=[LogScrubber(), structlog.processors.JSONRenderer()])
        log = structlog.get_logger()
        log.info("user_action", cpf="12345678901")  # cpf replaced by hex digest

    PHI_FIELDS from pseudonymizer are the canonical set — the scrubber does
    NOT maintain a separate list.
    """

    def __init__(self, pseudonymizer: Pseudonymizer | None = None) -> None:
        # Reuse the gateway's keyed pseudonymizer (ADR-0035). A production log config should
        # inject the SAME keyed instance the gateway uses (`Pseudonymizer.from_settings(...)`);
        # the bare default is the non-secret deterministic DEV pseudonymizer (keyed HMAC, never
        # plain SHA-256) so local/CI logging works without provisioning a secret.
        self._pseudonymizer = pseudonymizer if pseudonymizer is not None else Pseudonymizer()

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

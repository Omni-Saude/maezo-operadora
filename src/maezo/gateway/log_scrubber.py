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

from types import FrameType
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


# Exception text is arbitrary upstream input (including JSON, PHI and encoded WAMIDs).
# Its minimization is always required by ADR-0006, independently of the policy-gated
# business-key HMAC migration. No key, hash fallback or policy promotion is needed here.
_ERROR_REDACTED = "[REDACTED_ERROR_VALUE]"
_ERROR_FIELDS = frozenset({"error", "errors", "exception", "exc_text", "stack"})
_ERROR_STRUCTURE_KEYS = frozenset(
    {
        "error",
        "errors",
        "exception",
        "message",
        "detail",
        "details",
        "type",
        "code",
        "status",
        "payload",
        "input",
        "loc",
        "path",
        "cause",
        "context",
    }
)
_ERROR_MAX_DEPTH = 16


def _exception_diagnostic(exc: BaseException, *, depth: int = 0) -> str:
    """Class and code locations only: never call str/repr, inspect args, locals or source.

    Cause/context and ExceptionGroup members remain visible. A bounded walk also terminates
    on cyclic exception chains; truncation is explicit instead of concealing a failed logger.
    """
    if depth >= _ERROR_MAX_DEPTH:
        return "[TRUNCATED_ERROR_CHAIN]"
    lines: list[str] = []
    cause = exc.__cause__
    context = exc.__context__
    if cause is not None:
        lines.extend((_exception_diagnostic(cause, depth=depth + 1), "caused by:"))
    elif context is not None and not exc.__suppress_context__:
        lines.extend((_exception_diagnostic(context, depth=depth + 1), "during handling:"))
    lines.append(f"{type(exc).__name__}: {_ERROR_REDACTED}")
    tb = exc.__traceback__
    while tb is not None:
        # Only code metadata; no locals or source line that can embed a rejected value.
        code = tb.tb_frame.f_code
        lines.append(f"  {code.co_filename.rsplit('/', 1)[-1]}:{tb.tb_lineno} in {code.co_name}")
        tb = tb.tb_next
    if isinstance(exc, BaseExceptionGroup):
        for index, member in enumerate(exc.exceptions):
            lines.append(f"member {index}: {_exception_diagnostic(member, depth=depth + 1)}")
    return "\n".join(lines)


class ErrorLogScrubber:
    """Always-on minimization of log error envelopes and exception traces (ADR-0006).

    Installs at the last structured boundary, before ConsoleRenderer. Unlike LogScrubber,
    this needs no pseudonymizer: error messages have no required correlation semantics.
    Events, levels, operations and normal correlation fields remain unchanged; errors retain
    class, code location, cause/context/group structure, but no untrusted message/value.
    Nested error mappings/lists keep their safe structural keys and shape. Unknown keys and
    scalar values are minimized because either can contain PHI; this is not a PHI classifier.
    Existing business-key policy and payload/engine persistence are outside this boundary.
    """

    def __call__(self, logger: Any, method_name: str, event_dict: dict[str, Any]) -> dict[str, Any]:
        import sys

        # Consume raw exc_info ourselves: a renderer that first stringifies an exception has
        # already lost the distinction between code metadata and its untrusted input values.
        fields = {key: value for key, value in event_dict.items() if key not in {"exc_info", "stack_info"}}
        scrubbed: dict[str, Any] = self._scrub(fields)
        info = event_dict.get("exc_info")
        exc: BaseException | None = None
        if info is True:
            exc = sys.exc_info()[1]
        elif isinstance(info, BaseException):
            exc = info
        elif isinstance(info, tuple) and len(info) == 3 and isinstance(info[1], BaseException):
            exc = info[1]
        if exc is not None:
            scrubbed["exception"] = _exception_diagnostic(exc)
        elif info is not None and info is not False:
            scrubbed["exception"] = _ERROR_REDACTED
        if event_dict.get("stack_info") is True:
            # StackInfoRenderer includes source lines; emit code metadata only instead.
            frame: FrameType | None = sys._getframe(1)
            frames: list[str] = []
            while frame is not None:
                code = frame.f_code
                frames.append(f"  {code.co_filename.rsplit('/', 1)[-1]}:{frame.f_lineno} in {code.co_name}")
                frame = frame.f_back
            scrubbed["stack"] = "Stack (code locations only):\n" + "\n".join(reversed(frames))
        return scrubbed

    def _scrub(self, value: Any, *, error: bool = False, depth: int = 0) -> Any:
        if depth >= _ERROR_MAX_DEPTH:
            return "[TRUNCATED_ERROR_STRUCTURE]"
        if isinstance(value, BaseException):
            return _exception_diagnostic(value)
        if isinstance(value, dict):
            result: dict[Any, Any] = {}
            for index, (key, item) in enumerate(value.items()):
                is_error = isinstance(key, str) and (
                    key in _ERROR_FIELDS or key.endswith(("_error", "_errors", "_exception"))
                )
                safe_key = key
                if error and (not isinstance(key, str) or key not in _ERROR_STRUCTURE_KEYS):
                    safe_key = f"[REDACTED_ERROR_KEY_{index}]"
                result[safe_key] = self._scrub(item, error=error or is_error, depth=depth + 1)
            return result
        if isinstance(value, (list, tuple)):
            items = [self._scrub(item, error=error, depth=depth + 1) for item in value]
            return tuple(items) if isinstance(value, tuple) else items
        if error and value is not None and not isinstance(value, bool):
            return _ERROR_REDACTED
        return value

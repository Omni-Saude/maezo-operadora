"""Strict test-only oracle for the pinned JSSE missing-client-certificate alert.

The CIB Seven package image currently runs OpenJDK 17.0.17+10. Its TLS 1.3
``T13CertificateConsumer`` emits ``bad_certificate`` when client authentication
is required and the client sends an empty certificate chain.  CPython exposes
that peer alert as an ``ssl.SSLError`` whose typed reason is
``SSLV3_ALERT_BAD_CERTIFICATE``.

This helper deliberately recognizes only that pinned implementation behaviour.
It does not turn arbitrary transport failures, HTTP responses, error strings or
local server-certificate verification failures into evidence of required mTLS.
"""

from __future__ import annotations

import ssl
from collections.abc import Iterator
from contextlib import contextmanager

PINNED_JSSE_MISSING_CLIENT_CERTIFICATE_REASON = "SSLV3_ALERT_BAD_CERTIFICATE"
_MAX_CAUSAL_CHAIN_DEPTH = 8


def _causal_chain(exc: BaseException) -> Iterator[BaseException]:
    """Yield the effective exception chain with normal Python chaining rules."""
    current: BaseException | None = exc
    visited: set[int] = set()
    for _ in range(_MAX_CAUSAL_CHAIN_DEPTH):
        if current is None:
            return
        identity = id(current)
        if identity in visited:
            raise AssertionError("cyclic exception chain cannot prove a TLS peer alert")
        visited.add(identity)
        yield current
        if current.__cause__ is not None:
            current = current.__cause__
        elif current.__suppress_context__:
            current = None
        else:
            current = current.__context__
    if current is not None:
        raise AssertionError("exception chain is too deep to prove a TLS peer alert")


def assert_pinned_jsse_missing_client_certificate_alert(exc: BaseException) -> None:
    """Require the exact native peer alert emitted by the pinned JSSE runtime."""
    observed: list[tuple[str, object, object]] = []
    for item in _causal_chain(exc):
        observed.append((type(item).__name__, getattr(item, "library", None), getattr(item, "reason", None)))
        if (
            isinstance(item, ssl.SSLError)
            and not isinstance(item, ssl.SSLCertVerificationError)
            and item.library == "SSL"
            and item.reason == PINNED_JSSE_MISSING_CLIENT_CERTIFICATE_REASON
        ):
            return
    raise AssertionError(
        "expected native SSL peer alert "
        f"library='SSL' reason={PINNED_JSSE_MISSING_CLIENT_CERTIFICATE_REASON!r}; "
        f"observed typed chain={observed!r}"
    )


@contextmanager
def expect_pinned_jsse_missing_client_certificate_alert() -> Iterator[None]:
    """Fail unless the enclosed operation raises the exact pinned JSSE alert."""
    try:
        yield
    except Exception as exc:
        assert_pinned_jsse_missing_client_certificate_alert(exc)
        return
    raise AssertionError("operation returned without the required TLS peer alert")


def server_authenticated_tls13_context(ca_file: str) -> ssl.SSLContext:
    """Build a fresh server-auth context with no client certificate configured."""
    context = ssl.create_default_context(ssl.Purpose.SERVER_AUTH, cafile=ca_file)
    context.minimum_version = ssl.TLSVersion.TLSv1_3
    context.maximum_version = ssl.TLSVersion.TLSv1_3
    assert context.check_hostname
    assert context.verify_mode == ssl.CERT_REQUIRED
    return context

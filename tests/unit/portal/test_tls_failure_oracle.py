"""Focal controls for the real-image missing-client-certificate oracle."""

from __future__ import annotations

import ssl

import httpx
import pytest
from tests.support.tls_oracle import (
    PINNED_JSSE_MISSING_CLIENT_CERTIFICATE_REASON,
    assert_pinned_jsse_missing_client_certificate_alert,
    expect_pinned_jsse_missing_client_certificate_alert,
    server_authenticated_tls13_context,
)


def _ssl_error(
    reason: str = PINNED_JSSE_MISSING_CLIENT_CERTIFICATE_REASON,
    *,
    library: str = "SSL",
    error_type: type[ssl.SSLError] = ssl.SSLError,
) -> ssl.SSLError:
    error = error_type(1, "synthetic typed TLS failure")
    error.library = library
    error.reason = reason
    return error


def _raise_from(outer: BaseException, cause: BaseException) -> BaseException:
    try:
        raise cause
    except BaseException as caught:
        try:
            raise outer from caught
        except BaseException as chained:
            return chained


def test_exact_native_peer_alert_is_accepted_through_httpx_causal_chain() -> None:
    request = httpx.Request("POST", "https://localhost/maezo-human/v1/commands")
    inner = _raise_from(RuntimeError("httpcore wrapper"), _ssl_error())
    outer = _raise_from(httpx.ReadError("read failed", request=request), inner)

    assert_pinned_jsse_missing_client_certificate_alert(outer)


@pytest.mark.parametrize(
    "failure",
    [
        TimeoutError("timed out"),
        ConnectionResetError("reset"),
        EOFError("EOF"),
        OSError("connection refused"),
        RuntimeError("SSLV3_ALERT_BAD_CERTIFICATE"),
        _ssl_error("WRONG_VERSION_NUMBER"),
        _ssl_error("CERTIFICATE_UNKNOWN"),
        _ssl_error(library="not-SSL"),
        _ssl_error(error_type=ssl.SSLCertVerificationError),
    ],
)
def test_unrelated_failures_do_not_satisfy_missing_client_certificate_oracle(
    failure: BaseException,
) -> None:
    with pytest.raises(AssertionError, match="expected native SSL peer alert"):
        assert_pinned_jsse_missing_client_certificate_alert(failure)


def test_suppressed_historical_ssl_context_does_not_satisfy_timeout() -> None:
    try:
        raise _ssl_error()
    except ssl.SSLError:
        try:
            raise TimeoutError("current failure") from None
        except TimeoutError as timeout:
            failure = timeout

    with pytest.raises(AssertionError, match="expected native SSL peer alert"):
        assert_pinned_jsse_missing_client_certificate_alert(failure)


def test_cyclic_exception_chain_is_rejected() -> None:
    failure = RuntimeError("cycle")
    failure.__cause__ = failure

    with pytest.raises(AssertionError, match="cyclic exception chain"):
        assert_pinned_jsse_missing_client_certificate_alert(failure)


@pytest.mark.parametrize("status_code", [200, 403])
def test_http_response_alone_does_not_satisfy_strict_transport_oracle(status_code: int) -> None:
    response = httpx.Response(status_code)

    with (
        pytest.raises(AssertionError, match="returned without the required TLS peer alert"),
        expect_pinned_jsse_missing_client_certificate_alert(),
    ):
        assert response.status_code == status_code


def test_no_client_context_trusts_and_verifies_only_the_server(monkeypatch: pytest.MonkeyPatch) -> None:
    real_create_default_context = ssl.create_default_context
    calls: list[tuple[ssl.Purpose, str | None]] = []

    def recording_create_default_context(
        purpose: ssl.Purpose = ssl.Purpose.SERVER_AUTH,
        *,
        cafile: str | None = None,
        capath: str | None = None,
        cadata: str | bytes | None = None,
    ) -> ssl.SSLContext:
        calls.append((purpose, cafile))
        return real_create_default_context(purpose, cafile=cafile, capath=capath, cadata=cadata)

    monkeypatch.setattr(ssl, "create_default_context", recording_create_default_context)
    context = server_authenticated_tls13_context("/etc/ssl/cert.pem")

    assert calls == [(ssl.Purpose.SERVER_AUTH, "/etc/ssl/cert.pem")]
    assert context.check_hostname
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.minimum_version == ssl.TLSVersion.TLSv1_3
    assert context.maximum_version == ssl.TLSVersion.TLSv1_3

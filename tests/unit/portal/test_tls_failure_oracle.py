"""Focal controls for the real-image missing-client-certificate oracle."""

from __future__ import annotations

import ssl
from typing import Any

import httpcore
import httpx
import pytest
from httpcore._backends.sync import SyncStream
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


class _SocketSpecimen:
    def __init__(self, failure: BaseException) -> None:
        self.failure = failure

    def settimeout(self, timeout: float | None) -> None:
        del timeout

    def send(self, data: bytes) -> int:
        return len(data)

    def recv(self, max_bytes: int) -> bytes:
        del max_bytes
        raise self.failure

    def close(self) -> None:
        pass


class _Stream(SyncStream):
    def start_tls(self, **kwargs: Any) -> SyncStream:
        del kwargs
        return self

    def get_extra_info(self, info: str) -> object:
        return False if info == "is_readable" else None


class _Backend:
    def __init__(self, failure: BaseException) -> None:
        self.failure = failure

    def connect_tcp(self, **kwargs: Any) -> SyncStream:
        del kwargs
        return _Stream(_SocketSpecimen(self.failure))


def _request_failure(native: BaseException) -> httpx.ReadError:
    transport = httpx.HTTPTransport()
    transport._pool.close()
    transport._pool = httpcore.ConnectionPool(network_backend=_Backend(native))
    with httpx.Client(transport=transport, trust_env=False) as client:
        try:
            client.post("https://offline.invalid/commands", json={})
        except httpx.ReadError as failure:
            return failure
    raise AssertionError("pinned HTTPX/httpcore path returned without ReadError")


def test_exact_native_peer_alert_is_accepted_through_pinned_library_path() -> None:
    native = _ssl_error()
    outer = _request_failure(native)
    inner = outer.__cause__

    assert httpx.__version__ == "0.28.1"
    assert httpcore.__version__ == "1.0.9"
    assert type(inner) is httpcore.ReadError
    assert inner.__cause__ is None
    assert inner.__suppress_context__ is True
    assert len(inner.args) == 1
    assert inner.args[0] is native
    assert_pinned_jsse_missing_client_certificate_alert(outer)


def test_pinned_library_path_retains_reset_without_satisfying_oracle() -> None:
    native = ConnectionResetError("reset")
    outer = _request_failure(native)
    inner = outer.__cause__

    assert type(inner) is httpcore.ReadError
    assert len(inner.args) == 1
    assert inner.args[0] is native
    with pytest.raises(AssertionError, match="expected native SSL peer alert"):
        assert_pinned_jsse_missing_client_certificate_alert(outer)


def test_exact_native_peer_alert_is_accepted_through_explicit_causal_chain() -> None:
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
        httpcore.ReadError("SSLV3_ALERT_BAD_CERTIFICATE"),
        httpcore.ReadError(ConnectionResetError("reset")),
        httpcore.ReadError(TimeoutError("timed out")),
        httpcore.ReadError(EOFError("EOF")),
        httpcore.ReadError(_ssl_error("WRONG_VERSION_NUMBER")),
        httpcore.ReadError(_ssl_error("CERTIFICATE_UNKNOWN")),
        httpcore.ReadError(_ssl_error(library="not-SSL")),
        httpcore.ReadError(_ssl_error(error_type=ssl.SSLCertVerificationError)),
        httpcore.ReadError(_ssl_error(), "extra argument"),
        RuntimeError(_ssl_error()),
        httpcore.ReadError(RuntimeError(_ssl_error())),
    ],
)
def test_unrelated_failures_do_not_satisfy_missing_client_certificate_oracle(
    failure: BaseException,
) -> None:
    with pytest.raises(AssertionError, match="expected native SSL peer alert"):
        assert_pinned_jsse_missing_client_certificate_alert(failure)


def test_httpcore_read_error_subclass_payload_is_not_an_authorized_edge() -> None:
    class DerivedReadError(httpcore.ReadError):
        pass

    with pytest.raises(AssertionError, match="expected native SSL peer alert"):
        assert_pinned_jsse_missing_client_certificate_alert(DerivedReadError(_ssl_error()))


def test_explicit_cause_takes_precedence_over_httpcore_payload() -> None:
    failure = _raise_from(httpcore.ReadError(_ssl_error()), ConnectionResetError("current cause"))

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


def test_overlong_exception_chain_is_rejected() -> None:
    failure: BaseException = RuntimeError("root")
    for depth in range(9):
        failure = _raise_from(RuntimeError(f"wrapper {depth}"), failure)

    with pytest.raises(AssertionError, match="too deep"):
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
        # Registra o `cafile` pedido mas NAO o carrega. O que este teste prova e' a PASSAGEM do
        # caminho ate' `ssl.create_default_context` e as flags que o oracle fixa DEPOIS de criar o
        # contexto (TLS 1.3 min/max, check_hostname, CERT_REQUIRED) — nada disso depende de o
        # arquivo existir. Carrega-lo tornava o teste dependente de plataforma: `/etc/ssl/cert.pem`
        # existe no macOS e no Alpine, NAO no Ubuntu do runner (que usa
        # `/etc/ssl/certs/ca-certificates.crt`), e `load_verify_locations` levantava
        # FileNotFoundError em ssl.py:719 — a unica falha deste arquivo no CI de 18/09/2026.
        return real_create_default_context(purpose, capath=capath, cadata=cadata)

    monkeypatch.setattr(ssl, "create_default_context", recording_create_default_context)
    context = server_authenticated_tls13_context("/etc/ssl/cert.pem")

    assert calls == [(ssl.Purpose.SERVER_AUTH, "/etc/ssl/cert.pem")]
    assert context.check_hostname
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.minimum_version == ssl.TLSVersion.TLSv1_3
    assert context.maximum_version == ssl.TLSVersion.TLSv1_3

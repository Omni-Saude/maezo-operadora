"""O emissor T1.6 conecta no Aurora com verify-full (nunca `prefer`)."""

from __future__ import annotations

import ssl

from sqlalchemy.engine import make_url

from maezo.gateway.staff_cases import case_issuer_runtime as runtime


def test_database_tls_is_verify_full() -> None:
    context = runtime.database_tls()
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.check_hostname is True


def test_engine_passes_the_verifying_context(monkeypatch) -> None:
    seen: dict = {}

    def fake(url, **kwargs):
        seen.update(kwargs)
        return object()

    monkeypatch.setattr(runtime, "create_async_engine", fake)
    runtime._engine(make_url("postgresql+asyncpg://u:p@db.example:5432/maezo"), 5)
    tls = seen["connect_args"]["ssl"]
    assert isinstance(tls, ssl.SSLContext)
    assert tls.verify_mode == ssl.CERT_REQUIRED and tls.check_hostname


def test_publication_job_source_pins_tls() -> None:
    from pathlib import Path

    source = Path("src/maezo/gateway/human/membership_publication_job.py").read_text(encoding="utf-8")
    assert 'connect_args={"ssl": tls}' in source

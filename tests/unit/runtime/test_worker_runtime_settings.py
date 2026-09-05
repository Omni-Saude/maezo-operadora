"""Unit tests for `maezo.runtime.worker_runtime.settings.WorkerRuntimeSettings` (T1.1 §11)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from maezo.runtime.worker_runtime.settings import WorkerRuntimeSettings


def test_defaults_match_design_table() -> None:
    settings = WorkerRuntimeSettings()
    assert settings.tenant_id == "amh"
    assert settings.worker_id == "maezo-worker-local"
    assert settings.cibseven_base_url == "http://cibseven:8080/engine-rest"
    assert settings.cibseven_auth_token is None
    # CC-03/AND-03: mesmo default (e mesmo alias) do `agent_runtime/settings.py` — as duas raizes
    # de composicao NAO podem discordar sobre onde o FHIR esta. Espelha
    # `tests/unit/runtime/agent_runtime/test_settings.py::test_defaults`.
    assert settings.fhir_base_url == "http://hapi-fhir:8080/fhir"
    assert settings.lock_duration_ms == 30_000
    assert settings.poll_interval_ms == 5_000
    assert settings.async_response_timeout_ms == 25_000
    assert settings.max_tasks_per_poll == 10
    assert settings.max_retry_attempts == 3
    assert settings.client_timeout_s == 40.0
    assert settings.drain_deadline_s == 20.0
    assert settings.health_port == 8000


def test_construct_by_env_alias() -> None:
    settings = WorkerRuntimeSettings(
        TENANT_ID="omni",
        WORKER_ID="worker-pod-7",
        CIBSEVEN_BASE_URL="http://engine:8080/engine-rest",
    )
    assert settings.tenant_id == "omni"
    assert settings.worker_id == "worker-pod-7"
    assert settings.cibseven_base_url == "http://engine:8080/engine-rest"


def test_construct_by_field_name_populate_by_name() -> None:
    settings = WorkerRuntimeSettings(tenant_id="omni", worker_id="w2")
    assert settings.tenant_id == "omni"
    assert settings.worker_id == "w2"


def test_fail_closed_client_timeout_must_exceed_async_response_timeout() -> None:
    """Design §11: `client_timeout_s * 1000 > async_response_timeout_ms` — a misconfigured
    long-poll must not boot."""
    with pytest.raises(ValidationError, match="client_timeout_s"):
        WorkerRuntimeSettings(
            async_response_timeout_ms=25_000,
            client_timeout_s=25.0,  # equal, no margin — must be rejected
        )


def test_fail_closed_client_timeout_shorter_than_async_response_timeout() -> None:
    with pytest.raises(ValidationError):
        WorkerRuntimeSettings(async_response_timeout_ms=30_000, client_timeout_s=10.0)


def test_client_timeout_with_sufficient_margin_accepted() -> None:
    settings = WorkerRuntimeSettings(async_response_timeout_ms=25_000, client_timeout_s=40.0)
    assert settings.client_timeout_s == 40.0


@pytest.mark.parametrize(
    "field",
    [
        "lock_duration_ms",
        "poll_interval_ms",
        "async_response_timeout_ms",
        "max_tasks_per_poll",
        "max_retry_attempts",
    ],
)
def test_non_positive_fields_rejected(field: str) -> None:
    with pytest.raises(ValidationError):
        WorkerRuntimeSettings(**{field: 0})


def test_auth_token_value_none_when_absent() -> None:
    settings = WorkerRuntimeSettings()
    assert settings.cibseven_auth_token_value() is None


def test_auth_token_value_returns_raw_string() -> None:
    settings = WorkerRuntimeSettings(CIBSEVEN_AUTH_TOKEN="secret-123")
    assert settings.cibseven_auth_token_value() == "secret-123"


# ---------------------------------------------------------------------------
# DATABASE_URL — durable audit sink DSN (T1.10 T-D, ADR-0007 L0)
# ---------------------------------------------------------------------------


def test_database_url_defaults_none() -> None:
    """FAIL-CLOSED default: absent DATABASE_URL -> None -> the composition root cannot build a sink
    -> the daemon never enters the fetch rotation (design §7 T-D). NOT a startup crash — settings
    still construct so /healthz can be green while /readyz stays red."""
    settings = WorkerRuntimeSettings()
    assert settings.database_url is None


def test_database_url_binds_env_alias() -> None:
    settings = WorkerRuntimeSettings(DATABASE_URL="postgresql+asyncpg://maezo:maezo@aurora:5432/maezo")
    assert settings.database_url == "postgresql+asyncpg://maezo:maezo@aurora:5432/maezo"


def test_database_url_binds_by_field_name() -> None:
    settings = WorkerRuntimeSettings(database_url="postgresql://x@localhost:5432/db")
    assert settings.database_url == "postgresql://x@localhost:5432/db"

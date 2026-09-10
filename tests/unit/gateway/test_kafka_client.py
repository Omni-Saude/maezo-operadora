"""Actual SDK signer/parser/factory, synthetic wire only; no cloud/credentials."""

from __future__ import annotations

import asyncio
import base64
import io
import json
import logging
import ssl
import time
from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest
from botocore.stub import Stubber  # type: ignore[import-untyped]

from maezo.gateway import kafka_client as m

CLUSTER = "arn:aws:kafka:sa-east-1:111111111111:cluster/fixture/00000000-0000-0000-0000-000000000000"
ROLE = "arn:aws:iam::222222222222:role/fixture-worker"
BROKERS = "boot-fixture.kafka-serverless.sa-east-1.amazonaws.com:9098"
SETTINGS = m.KafkaConnectionSettings(BROKERS, "msk_iam", "sa-east-1", CLUSTER, "111111111111", ROLE)


@pytest.mark.parametrize(
    "delta",
    [
        {"region": "us-east-1"},
        {"cluster_owner_account": "222222222222"},
        {"task_role_arn": None},
        {"cluster_arn": None},
        {"auth_mode": "plaintext"},
        {"auth_mode": "development"},
        {"bootstrap_servers": "broker:9092"},
        {"bootstrap_servers": BROKERS + "," + BROKERS},
        {"bootstrap_servers": "https://broker:9098"},
        {"bootstrap_servers": "user@broker:9098"},
        {"bootstrap_servers": "broker:9098/path"},
        {"task_role_arn": "arn:aws:sts::222222222222:assumed-role/fixture-worker/session"},
    ],
)
def test_iam_configuration_refuses_incomplete_or_conflicting_input(delta: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        replace(SETTINGS, **delta)


@pytest.fixture
def wire(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    for name in tuple(logging.Logger.manager.loggerDict):
        if name.startswith(("botocore", "urllib3", "aiokafka")):
            monkeypatch.setattr(logging.getLogger(name), "level", logging.WARNING)
    monkeypatch.setenv("AWS_CONTAINER_CREDENTIALS_RELATIVE_URI", "/v2/credentials/fixture-id")
    # These must never become credential authorities or endpoint overrides.
    monkeypatch.setenv("AWS_CONTAINER_CREDENTIALS_FULL_URI", "https://invalid.example/credentials")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "ambient-not-authority")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "ambient-not-authority")
    monkeypatch.setenv("AWS_ENDPOINT_URL_KAFKA", "https://invalid.example/kafka")
    data: dict[str, Any] = {
        "RoleArn": ROLE,
        "AccessKeyId": "synthetic-access",
        "SecretAccessKey": "synthetic-key",
        "Token": "synthetic-session",
        "Expiration": datetime.fromtimestamp(time.time() + 3600, UTC).isoformat(),
    }
    state: dict[str, Any] = {
        "data": data,
        "metadata_calls": 0,
        "api_calls": 0,
        "closed": 0,
        "brokers": BROKERS,
        "status": 200,
    }

    class MetadataSession:
        def __init__(self, **kwargs: Any) -> None:
            assert kwargs == {"timeout": 2.0, "proxies": {}}

        def send(self, request: Any) -> Any:
            assert request.url == "http://169.254.170.2/v2/credentials/fixture-id"
            assert request.stream_output is True
            assert request.method == "GET"
            state["metadata_calls"] += 1
            body = state.get("body", json.dumps(state["data"]).encode())
            return SimpleNamespace(status_code=state["status"], raw=io.BytesIO(body))

        def close(self) -> None:
            state["closed"] += 1

    actual_session = m.Session

    class ApiSession:
        def create_client(self, service: str, **kwargs: Any) -> Any:
            assert service == "kafka"
            assert kwargs["endpoint_url"] == "https://kafka.sa-east-1.amazonaws.com"
            assert kwargs["region_name"] == "sa-east-1" and kwargs["verify"] is True
            assert kwargs["aws_access_key_id"] == data["AccessKeyId"]
            assert kwargs["aws_session_token"] == data["Token"]
            assert kwargs["config"].retries == {"total_max_attempts": 1}
            assert kwargs["config"].proxies == {}
            client = actual_session().create_client(service, **kwargs)
            stub = Stubber(client)
            if state.get("api_failure"):
                stub.add_client_error(
                    "get_bootstrap_brokers",
                    service_error_code="ForbiddenException",
                    expected_params={"ClusterArn": CLUSTER},
                )
            else:
                stub.add_response(
                    "get_bootstrap_brokers",
                    {"BootstrapBrokerStringSaslIam": state["brokers"]},
                    {"ClusterArn": CLUSTER},
                )
            stub.activate()
            state["api_calls"] += 1
            state["stub"] = stub
            return client

    monkeypatch.setattr(m, "URLLib3Session", MetadataSession)
    monkeypatch.setattr(m, "Session", ApiSession)
    return state


@pytest.mark.asyncio
async def test_real_signer_bound_to_metadata_role_region_and_actual_api(wire: dict[str, Any]) -> None:
    provider = m._MskTokenProvider(SETTINGS)
    tokens = await asyncio.gather(provider.token(), provider.token())
    assert tokens[0] == tokens[1]
    assert wire["metadata_calls"] == wire["api_calls"] == wire["closed"] == 1
    decoded = base64.urlsafe_b64decode(tokens[0] + "=" * (-len(tokens[0]) % 4)).decode()
    parsed = urlparse(decoded)
    params = parse_qs(parsed.query)
    assert parsed.hostname == "kafka.sa-east-1.amazonaws.com"
    assert params["X-Amz-Credential"][0].endswith("/sa-east-1/kafka-cluster/aws4_request")
    assert params["X-Amz-Security-Token"] == [wire["data"]["Token"]]
    assert params["Action"] == ["kafka-cluster:Connect"]
    assert 800 < provider._expires_at - time.time() <= 900
    assert "synthetic" not in repr(provider)
    wire["stub"].assert_no_pending_responses()


@pytest.mark.asyncio
async def test_refresh_revalidates_role_and_never_reuses_expiring_token(wire: dict[str, Any]) -> None:
    provider = m._MskTokenProvider(SETTINGS)
    await provider.token()
    provider._expires_at = time.time() + 59
    wire["data"]["RoleArn"] = ROLE + "-wrong"
    with pytest.raises(m.KafkaAuthenticationUnavailableError, match="^kafka_iam_unavailable$"):
        await provider.token()
    assert provider._token is None and wire["api_calls"] == 1


@pytest.mark.parametrize(
    "mutation",
    ["role", "missing", "expiry", "naive", "oversize", "bad_json", "redirect", "broker", "api_error"],
)
@pytest.mark.asyncio
async def test_closed_refusal_before_producer_allocation(
    wire: dict[str, Any], mutation: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    if mutation == "role":
        wire["data"]["RoleArn"] = ROLE + "-other"
    if mutation == "missing":
        wire["data"].pop("Token")
    if mutation == "expiry":
        wire["data"]["Expiration"] = datetime.fromtimestamp(time.time() + 60, UTC).isoformat()
    if mutation == "naive":
        wire["data"]["Expiration"] = "2099-01-01T00:00:00"
    if mutation == "oversize":
        wire["body"] = b"x" * 16385
    if mutation == "bad_json":
        wire["body"] = b"synthetic-not-json"
    if mutation == "redirect":
        wire["status"] = 302
    if mutation == "broker":
        wire["brokers"] = "other:9098"
    if mutation == "api_error":
        wire["api_failure"] = True
    allocated: list[Any] = []
    monkeypatch.setattr(m, "AIOKafkaProducer", lambda **kw: allocated.append(kw))
    with pytest.raises(m.KafkaAuthenticationUnavailableError) as caught:
        await m.create_kafka_producer(SETTINGS, request_timeout_ms=1000)
    assert str(caught.value) == "kafka_iam_unavailable" and caught.value.__suppress_context__
    assert not allocated and wire["closed"] == 1


@pytest.mark.parametrize(
    "relative_uri",
    ["", "//evil/credentials", "/v2/credentials/../bad", "/v2/credentials/id?query=1", "http://evil"],
)
@pytest.mark.asyncio
async def test_no_default_chain_or_alternate_metadata_host(
    wire: dict[str, Any], monkeypatch: pytest.MonkeyPatch, relative_uri: str
) -> None:
    monkeypatch.setenv("AWS_CONTAINER_CREDENTIALS_RELATIVE_URI", relative_uri)
    with pytest.raises(m.KafkaAuthenticationUnavailableError):
        await m._MskTokenProvider(SETTINGS).token()
    assert wire["metadata_calls"] == wire["api_calls"] == 0


@pytest.mark.asyncio
async def test_sdk_debug_refuses_before_credentials_and_on_cached_token(
    wire: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    provider = m._MskTokenProvider(SETTINGS)
    await provider.token()
    logger = logging.getLogger("botocore.auth")
    previous = logger.level
    try:
        logger.setLevel(logging.DEBUG)  # Public API also clears isEnabledFor's cache.
        with pytest.raises(m.KafkaAuthenticationUnavailableError):
            await provider.token()
    finally:
        logger.setLevel(previous)
    assert wire["metadata_calls"] == 1


@pytest.mark.asyncio
async def test_real_factory_has_verified_tls_and_iam_only(
    wire: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    # Construct actual aiokafka producer (no start/network) and inspect its configuration.
    producer = await m.create_kafka_producer(SETTINGS, request_timeout_ms=1000)
    assert producer.client._security_protocol == "SASL_SSL"
    assert producer.client._ssl_context.verify_mode == ssl.CERT_REQUIRED
    assert producer.client._ssl_context.check_hostname is True
    assert producer.client._sasl_mechanism == "OAUTHBEARER"
    assert await producer.client._sasl_oauth_token_provider.token()
    await producer.stop()
    assert wire["metadata_calls"] == 1


@pytest.mark.asyncio
async def test_development_retains_plaintext_and_performs_no_aws_lookup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def forbidden() -> Any:
        raise AssertionError("AWS lookup in development")

    monkeypatch.setattr(m, "Session", forbidden)
    producer = await m.create_kafka_producer(
        m.KafkaConnectionSettings("localhost:9092"), request_timeout_ms=1000
    )
    assert producer.client._security_protocol == "PLAINTEXT"
    await producer.stop()


@pytest.mark.asyncio
async def test_cancellation_never_allocates_from_late_qualification(monkeypatch: pytest.MonkeyPatch) -> None:
    entered = asyncio.Event()
    release = asyncio.Event()
    allocated: list[Any] = []

    async def blocked(self: Any) -> str:
        entered.set()
        await release.wait()
        return "synthetic-token"

    monkeypatch.setattr(m._MskTokenProvider, "token", blocked)
    monkeypatch.setattr(m, "AIOKafkaProducer", lambda **kw: allocated.append(kw))
    task = asyncio.create_task(m.create_kafka_producer(SETTINGS, request_timeout_ms=1000))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    release.set()
    assert not allocated


@pytest.mark.asyncio
async def test_monotonic_expiry_does_not_extend_cached_token(wire: dict[str, Any]) -> None:
    provider = m._MskTokenProvider(SETTINGS)
    await provider.token()
    provider._refresh_deadline = time.monotonic() - 1
    wire["data"]["RoleArn"] = ROLE + "-revoked"
    with pytest.raises(m.KafkaAuthenticationUnavailableError):
        await provider.token()
    assert provider._token is None and wire["metadata_calls"] == 2

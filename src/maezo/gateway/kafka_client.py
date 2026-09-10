"""ADR-0049 D9 / ADR-0037 XRD11: explicit ECS task-role MSK IAM client.

Configuration selects a target; it does not ratify owner state, topic grants,
PHI classification, or a production deployment. Credentials never leave this
module except as the official IAM token delivered to aiokafka's SASL callback.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import ssl
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal

from aiokafka import AIOKafkaProducer  # type: ignore[import-untyped]
from aiokafka.abc import AbstractTokenProvider  # type: ignore[import-untyped]
from aws_msk_iam_sasl_signer import MSKAuthTokenProvider  # type: ignore[import-untyped]
from botocore.awsrequest import AWSRequest  # type: ignore[import-untyped]
from botocore.config import Config  # type: ignore[import-untyped]
from botocore.credentials import (  # type: ignore[import-untyped]
    ContainerProvider,
    CredentialProvider,
    Credentials,
)
from botocore.httpsession import URLLib3Session  # type: ignore[import-untyped]
from botocore.session import Session  # type: ignore[import-untyped]

_REGION = "sa-east-1"
_CLUSTER = re.compile(r"arn:aws:kafka:sa-east-1:([0-9]{12}):cluster/[A-Za-z0-9_-]{1,64}/[A-Za-z0-9-]{1,64}")
_ROLE = re.compile(r"arn:aws:iam::[0-9]{12}:role/[A-Za-z0-9_+=,.@/-]{1,512}")
_RELATIVE_URI = re.compile(r"/v2/credentials/[A-Za-z0-9-]{1,128}")
_BROKER = re.compile(r"[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?:9098")
_METADATA_ORIGIN = "http://169.254.170.2"
_MAX_METADATA_BYTES = 16384
_AUTH_TIMEOUT_S = 10.0


class KafkaAuthenticationUnavailableError(RuntimeError):
    """Closed failure label; never attach SDK messages, tokens, or metadata."""


def _unavailable() -> KafkaAuthenticationUnavailableError:
    return KafkaAuthenticationUnavailableError("kafka_iam_unavailable")


def _brokers(value: str) -> frozenset[str]:
    parts = value.split(",")
    if not 1 <= len(parts) <= 16 or len(set(parts)) != len(parts):
        raise ValueError("invalid_kafka_iam_brokers")
    if any(not _BROKER.fullmatch(part) or ".." in part for part in parts):
        raise ValueError("invalid_kafka_iam_brokers")
    return frozenset(parts)


@dataclass(frozen=True)
class KafkaConnectionSettings:
    """Nonsecret deployment input. IAM has no inferred cluster/account/role."""

    bootstrap_servers: str
    auth_mode: Literal["development", "msk_iam"] = "development"
    region: str | None = None
    cluster_arn: str | None = None
    cluster_owner_account: str | None = None
    task_role_arn: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.bootstrap_servers, str) or not self.bootstrap_servers.strip():
            raise ValueError("kafka_bootstrap_required")
        selected = (self.region, self.cluster_arn, self.cluster_owner_account, self.task_role_arn)
        if self.auth_mode == "development":
            if any(item is not None for item in selected):
                raise ValueError("kafka_iam_inputs_require_iam_mode")
            return
        if self.auth_mode != "msk_iam" or not all(isinstance(item, str) for item in selected):
            raise ValueError("kafka_iam_configuration_required")
        match = _CLUSTER.fullmatch(self.cluster_arn or "")
        if (
            self.region != _REGION
            or match is None
            or match[1] != self.cluster_owner_account
            or not _ROLE.fullmatch(self.task_role_arn or "")
        ):
            raise ValueError("kafka_iam_identity_mismatch")
        _brokers(self.bootstrap_servers)


def _require_safe_logging() -> None:
    # botocore's signer/endpoint debug records can contain session tokens. Refuse
    # that deployment posture; never change another component's logger settings.
    for name in tuple(logging.Logger.manager.loggerDict):
        if (
            name == "botocore" or name.startswith(("botocore.", "urllib3.", "aiokafka."))
        ) and logging.getLogger(name).isEnabledFor(logging.DEBUG):
            raise _unavailable()


class _EcsFetcher:
    """ContainerProvider's supported fetcher seam, without raw-body logging."""

    def __init__(self, role_arn: str) -> None:
        self._role_arn = role_arn
        self.expires_at = 0.0

    def full_url(self, relative_uri: str) -> str:
        if not _RELATIVE_URI.fullmatch(relative_uri):
            raise _unavailable()
        return _METADATA_ORIGIN + relative_uri

    def retrieve_full_uri(self, full_url: str, headers: Any = None) -> dict[str, Any]:
        relative_uri = full_url.removeprefix(_METADATA_ORIGIN)
        if full_url != self.full_url(relative_uri) or headers:
            raise _unavailable()
        session = URLLib3Session(timeout=2.0, proxies={})
        response = None
        try:
            request = AWSRequest(method="GET", url=full_url, headers={"Accept": "application/json"})
            request.stream_output = True
            response = session.send(request.prepare())
            if response.status_code != 200:
                raise _unavailable()
            body = response.raw.read(_MAX_METADATA_BYTES + 1)
            if len(body) > _MAX_METADATA_BYTES:
                raise _unavailable()
            result = json.loads(body)
            if not isinstance(result, dict) or result.get("RoleArn") != self._role_arn:
                raise _unavailable()
            for field in ("AccessKeyId", "SecretAccessKey", "Token", "Expiration"):
                value = result.get(field)
                if not isinstance(value, str) or not value or len(value) > 8192:
                    raise _unavailable()
            expiry = datetime.fromisoformat(result["Expiration"].replace("Z", "+00:00"))
            if expiry.tzinfo is None:
                raise _unavailable()
            self.expires_at = expiry.astimezone(UTC).timestamp()
            # Official signer issues a 900s token. Never mint past task credentials.
            if self.expires_at <= time.time() + 960:
                raise _unavailable()
            return result
        except Exception:
            raise _unavailable() from None
        finally:
            if response is not None:
                response.raw.close()
            session.close()


class _SnapshotProvider(CredentialProvider):  # type: ignore[misc]
    def __init__(self, credentials: Any) -> None:
        self._credentials = credentials

    def load(self) -> Any:
        return self._credentials


class _MskTokenProvider(AbstractTokenProvider):  # type: ignore[misc]
    def __init__(self, settings: KafkaConnectionSettings) -> None:
        self._settings = settings
        self._lock = asyncio.Lock()
        self._token: str | None = None
        self._expires_at = 0.0
        self._refresh_deadline = 0.0

    def _mint(self) -> tuple[str, float]:
        try:
            _require_safe_logging()
            relative_uri = os.environ.get("AWS_CONTAINER_CREDENTIALS_RELATIVE_URI", "")
            fetcher = _EcsFetcher(self._settings.task_role_arn or "")
            fetcher.full_url(relative_uri)
            # No full URI, auth-token file, profiles, static env keys or default chain.
            provider = ContainerProvider(
                environ={"AWS_CONTAINER_CREDENTIALS_RELATIVE_URI": relative_uri}, fetcher=fetcher
            )
            loaded = provider.load()
            if loaded is None:
                raise _unavailable()
            frozen = loaded.get_frozen_credentials()
            credentials = Credentials(frozen.access_key, frozen.secret_key, frozen.token)
            session = Session()
            # Explicit endpoint/credentials/proxies/CA/retries override ambient SDK choices.
            client = session.create_client(
                "kafka",
                region_name=_REGION,
                endpoint_url=f"https://kafka.{_REGION}.amazonaws.com",
                aws_access_key_id=credentials.access_key,
                aws_secret_access_key=credentials.secret_key,
                aws_session_token=credentials.token,
                verify=True,
                config=Config(
                    connect_timeout=2, read_timeout=2, retries={"total_max_attempts": 1}, proxies={}
                ),
            )
            try:
                _require_safe_logging()
                response = client.get_bootstrap_brokers(ClusterArn=self._settings.cluster_arn)
                actual = response.get("BootstrapBrokerStringSaslIam")
                if not isinstance(actual, str) or _brokers(actual) != _brokers(
                    self._settings.bootstrap_servers
                ):
                    raise _unavailable()
            finally:
                client.close()
            _require_safe_logging()
            token, expiry_ms = MSKAuthTokenProvider.generate_auth_token_from_credentials_provider(
                _REGION, _SnapshotProvider(credentials)
            )
            expiry = float(expiry_ms) / 1000
            if not isinstance(token, str) or not token or not time.time() + 60 < expiry <= fetcher.expires_at:
                raise _unavailable()
            return token, expiry
        except Exception:
            raise _unavailable() from None

    async def token(self) -> str:
        async with self._lock:
            _require_safe_logging()
            if (
                self._token is not None
                and time.time() + 60 < self._expires_at
                and time.monotonic() < self._refresh_deadline
            ):
                return self._token
            self._token = None
            self._expires_at = 0.0
            self._refresh_deadline = 0.0
            # Timeout/cancellation abandons the result; it cannot stop an SDK I/O
            # thread. That thread only reads metadata/API and never publishes.
            try:
                token, expires_at = await asyncio.wait_for(asyncio.to_thread(self._mint), _AUTH_TIMEOUT_S)
            except Exception:
                raise _unavailable() from None
            if time.time() + 60 >= expires_at:
                raise _unavailable()
            self._token, self._expires_at = token, expires_at
            self._refresh_deadline = time.monotonic() + expires_at - time.time() - 60
            return token


async def create_kafka_producer(settings: KafkaConnectionSettings, *, request_timeout_ms: int) -> Any:
    """Construct the real client; IAM refuses before any producer allocation."""
    if settings.auth_mode == "development":
        return AIOKafkaProducer(
            bootstrap_servers=settings.bootstrap_servers, request_timeout_ms=request_timeout_ms
        )
    provider = _MskTokenProvider(settings)
    await provider.token()  # Actual cluster/endpoint/role qualification before allocation.
    return AIOKafkaProducer(
        bootstrap_servers=settings.bootstrap_servers,
        request_timeout_ms=request_timeout_ms,
        security_protocol="SASL_SSL",
        sasl_mechanism="OAUTHBEARER",
        ssl_context=ssl.create_default_context(),
        sasl_oauth_token_provider=provider,
    )

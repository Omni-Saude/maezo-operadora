"""ADR-0055 authenticated owner enrollment acquisition and raw AWS identity.

No owner service is deployed here. Its absence fails closed. The independently
pinned owner must produce current resource/DB/policy observations; this client
cannot turn a request digest, signed local file or protocol fake into authority.
"""

from __future__ import annotations

import re
import secrets
import ssl
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

import httpx
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from maezo.platform.engine_bootstrap.controller_storage import (
    Refusal,
    canonical,
    decode64,
    digest,
    exact,
    parse_wire,
    require,
    scalar,
)
from maezo.platform.engine_bootstrap.owner_authority_contracts import OPERATIONS, Enrollment


@dataclass(frozen=True, slots=True)
class OwnerTrust:
    """Independent deployment configuration, never fields from an owner request."""

    endpoint: str
    public_key: bytes = field(repr=False)
    key_id: str
    enrollment_id: str

    def __post_init__(self) -> None:
        from maezo.platform.engine_bootstrap.owner_authority_contracts import uuid

        url = urlsplit(self.endpoint)
        require(url.scheme == "https" and bool(url.hostname) and not url.username and not url.password)
        require(url.path == "/d7-owner/v1/qualify" and not url.query and not url.fragment)
        require(type(self.public_key) is bytes and len(self.public_key) == 32)
        scalar("Id", self.key_id)
        uuid(self.enrollment_id)


@dataclass(frozen=True, slots=True)
class OwnerGrant:
    enrollment: Enrollment
    deadline_ms: int
    monotonic_deadline: float

    def current(self) -> None:
        require(time.time_ns() // 1_000_000 < self.deadline_ms, "UNAVAILABLE")
        require(time.monotonic() < self.monotonic_deadline, "UNAVAILABLE")


class OwnerEnrollmentClient:
    """Concrete bounded mTLS challenge/response verifier; no approval-file API."""

    def __init__(self, trust: OwnerTrust, tls: ssl.SSLContext) -> None:
        require(type(trust) is OwnerTrust, "AUTH_REFUSED")
        require(tls.verify_mode == ssl.CERT_REQUIRED and tls.check_hostname, "AUTH_REFUSED")
        require(tls.minimum_version >= ssl.TLSVersion.TLSv1_3, "AUTH_REFUSED")
        self._trust = trust
        self._http = httpx.Client(
            verify=tls,
            trust_env=False,
            follow_redirects=False,
            timeout=5.0,
        )

    def close(self) -> None:
        self._http.close()

    def acquire(self, operation: str, request: dict[str, Any], context: dict[str, Any]) -> OwnerGrant:
        require(operation in OPERATIONS, "UNSUPPORTED_ADAPTER")
        challenge = secrets.token_hex(32)
        started = time.monotonic()
        issued = time.time_ns() // 1_000_000
        raw = canonical(
            {
                "protocol": "maezo.d7-owner-qualification-request.v1",
                "enrollment_id": self._trust.enrollment_id,
                "operation": operation,
                "request_sha256": digest(canonical(request)),
                "context_sha256": digest(canonical(context)),
                "request": request,
                "context": context,
                "challenge": challenge,
            }
        )
        require(len(raw) <= 131_072)
        try:
            with self._http.stream(
                "POST", self._trust.endpoint, content=raw, headers={"Content-Type": "application/json"}
            ) as response:
                require(response.status_code == 200, "UNAVAILABLE")
                require(response.headers.get("cache-control") == "no-store", "AUTH_REFUSED")
                require(response.headers.get("content-type", "").split(";")[0] == "application/json")
                require("set-cookie" not in response.headers, "AUTH_REFUSED")
                data = bytearray()
                for chunk in response.iter_bytes():
                    data.extend(chunk)
                    require(len(data) <= 262_144 and time.monotonic() - started < 5, "UNAVAILABLE")
            value = exact(
                parse_wire(bytes(data)),
                (
                    "protocol key_id enrollment_id operation request_sha256 context_sha256 challenge "
                    "observed_at_ms deadline_ms enrollment policy_receipt_ref policy_receipt_sha256 signature"
                ),
            )
            signed = {k: v for k, v in value.items() if k != "signature"}
            Ed25519PublicKey.from_public_bytes(self._trust.public_key).verify(
                decode64(value["signature"]),
                canonical(signed),
            )
            require(value["protocol"] == "maezo.d7-owner-qualification-response.v1", "AUTH_REFUSED")
            require(value["key_id"] == self._trust.key_id, "AUTH_REFUSED")
            require(value["enrollment_id"] == self._trust.enrollment_id, "AUTH_REFUSED")
            require(value["operation"] == operation and value["challenge"] == challenge, "AUTH_REFUSED")
            require(value["request_sha256"] == digest(canonical(request)), "AUTH_REFUSED")
            require(value["context_sha256"] == digest(canonical(context)), "AUTH_REFUSED")
            for name in ("observed_at_ms", "deadline_ms"):
                scalar("UInt", value[name])
            now = time.time_ns() // 1_000_000
            require(
                issued <= value["observed_at_ms"] <= now < value["deadline_ms"] <= issued + 5000,
                "UNAVAILABLE",
            )
            require(time.monotonic() - started < 5, "UNAVAILABLE")
            scalar("Id", value["policy_receipt_ref"])
            scalar("Sha256", value["policy_receipt_sha256"])
            enrollment = Enrollment(canonical(value["enrollment"]))
            require(enrollment.value()["enrollment_id"] == self._trust.enrollment_id, "AUTH_REFUSED")
            # Project the signed interval from the pre-request clock pair, not a
            # fresh fixed five seconds; wall-clock rollback cannot extend it.
            grant = OwnerGrant(
                enrollment, value["deadline_ms"], started + (value["deadline_ms"] - issued) / 1000
            )
            grant.current()
            return grant
        except Refusal:
            raise
        except Exception:
            raise Refusal("UNAVAILABLE") from None


@dataclass(frozen=True, slots=True)
class AwsOwnerClients:
    """Clients constructed together from explicit gateway-owned credentials."""

    dynamodb: Any = field(repr=False)
    sts: Any = field(repr=False)
    region: str

    @classmethod
    def create(cls, *, credentials: Any, region: str) -> AwsOwnerClients:
        import botocore.session  # type: ignore[import-untyped]
        from botocore.config import Config  # type: ignore[import-untyped]
        from botocore.credentials import ReadOnlyCredentials  # type: ignore[import-untyped]

        require(isinstance(credentials, ReadOnlyCredentials), "AUTH_REFUSED")
        require(bool(credentials.access_key and credentials.secret_key and credentials.token), "AUTH_REFUSED")
        require(bool(re.fullmatch(r"[a-z]{2}(?:-gov)?-[a-z]+-[0-9]", region)), "SCOPE_REFUSED")
        session = botocore.session.Session()
        config = Config(connect_timeout=2, read_timeout=2, retries={"total_max_attempts": 1}, proxies={})
        options = {
            "region_name": region,
            "aws_access_key_id": credentials.access_key,
            "aws_secret_access_key": credentials.secret_key,
            "aws_session_token": credentials.token,
            "config": config,
        }
        return cls(
            session.create_client(
                "dynamodb", endpoint_url=f"https://dynamodb.{region}.amazonaws.com", verify=True, **options
            ),
            session.create_client(
                "sts", endpoint_url=f"https://sts.{region}.amazonaws.com", verify=True, **options
            ),
            region,
        )

    def identity(self, enrollment: Enrollment, *, writer: bool) -> dict[str, Any]:
        e = enrollment.value()
        require(self.region == e["scope"]["region"], "SCOPE_REFUSED")
        try:
            actor = self.sts.get_caller_identity()
            expected = e["writer_arn"] if writer else e["observer_arn"]
            require(actor["Arn"] == expected and actor["Account"] == e["scope"]["account"], "AUTH_REFUSED")
            table = self.dynamodb.describe_table(TableName=e["table_arn"])["Table"]
            require(
                table["TableArn"] == e["table_arn"] and table["TableId"] == e["table_id"], "SCOPE_REFUSED"
            )
            require(table["TableStatus"] == "ACTIVE" and not table.get("Replicas"), "UNSUPPORTED_ADAPTER")
            require(
                sorted(table["KeySchema"], key=lambda x: x["KeyType"])
                == [
                    {"AttributeName": "PK", "KeyType": "HASH"},
                    {"AttributeName": "SK", "KeyType": "RANGE"},
                ],
                "UNSUPPORTED_ADAPTER",
            )
            require(
                sorted(table["AttributeDefinitions"], key=lambda x: x["AttributeName"])
                == [
                    {"AttributeName": "PK", "AttributeType": "S"},
                    {"AttributeName": "SK", "AttributeType": "S"},
                ],
                "UNSUPPORTED_ADAPTER",
            )
            require(
                not table.get("GlobalSecondaryIndexes") and not table.get("LocalSecondaryIndexes"),
                "UNSUPPORTED_ADAPTER",
            )
            return {
                "principal_arn": actor["Arn"],
                "table_arn": table["TableArn"],
                "table_id": table["TableId"],
            }
        except Refusal:
            raise
        except Exception:
            raise Refusal("UNAVAILABLE") from None

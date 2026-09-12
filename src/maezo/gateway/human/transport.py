"""Dedicated human workload mTLS transport. No agent/admin/OIDC credential fallback.

Only the gateway signs or loads credentials. Trusted composition supplies provisioned
human key material and mTLS paths; the production gateway remains closed until key
isolation and authoritative adapters have been provisioned and verified.
"""

from __future__ import annotations

import base64
import hashlib
import re
import ssl
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

import httpx
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from maezo.portal.engine.decision import HumanDecisionCommand
from maezo.portal.engine.profile import HumanCommand, SigningContext, canonicalize, seal, strict_loads

from .credentials import DedicatedHumanCredential, HumanCommandCredentialPartition
from .models import Scope
from .outbox import _positive_seconds
from .projection import verify_engine_receipt

ConflictCode = Literal["REVISION_CONFLICT", "COMMAND_CONFLICT", "FORM_NOT_ACTIVATED"]
Purpose = Literal["human-command", "human-receipt"]


class EngineUnavailableError(RuntimeError):
    def __init__(self) -> None:
        super().__init__("authenticated human engine unavailable")


class EngineConflictError(RuntimeError):
    def __init__(self, code: ConflictCode) -> None:
        self.code = code
        super().__init__(code)


class CurrentHumanSigner(ABC):
    scope: Scope

    @abstractmethod
    def envelope(self, command: HumanCommand | HumanDecisionCommand, *, purpose: Purpose) -> bytes:
        """Current explicitly configured human partition key, with fresh transport validity.

        Does not renew the canonical command's evidence/membership/task revision.
        """
        raise NotImplementedError


class PartitionedEd25519Signer(CurrentHumanSigner):
    def __init__(
        self,
        *,
        partition: HumanCommandCredentialPartition,
        credential: DedicatedHumanCredential,
        context: SigningContext,
        private_key: Ed25519PrivateKey,
        valid_from: datetime,
        valid_until: datetime,
        envelope_seconds: int,
    ) -> None:
        self.scope = credential.scope
        self._partition = partition
        self._credential = credential
        self._context = context
        self._key = private_key
        self._from = valid_from
        self._until = valid_until
        self._seconds = _positive_seconds(envelope_seconds)
        if (
            not isinstance(private_key, Ed25519PrivateKey)
            or valid_from.tzinfo is None
            or valid_until.tzinfo is None
            or valid_from >= valid_until
            or context.tenant != self.scope.tenant
            or context.workload_ref != self.scope.workload_ref
            or context.key_id != credential.key_id
            or envelope_seconds > context.max_lifetime_seconds
        ):
            raise EngineUnavailableError()
        self._current()

    def _current(self) -> None:
        if (
            self._partition.for_workload(self.scope) != self._credential
            or not self._from <= datetime.now(UTC) < self._until
        ):
            raise EngineUnavailableError()

    def envelope(self, command: HumanCommand | HumanDecisionCommand, *, purpose: Purpose) -> bytes:
        try:
            self._current()
            if (
                command.tenant != self.scope.tenant
                or command.workload_ref != self.scope.workload_ref
                or (
                    isinstance(command, HumanDecisionCommand)
                    and command.environment != self.scope.environment
                )
            ):
                raise EngineUnavailableError()
            issued = int(datetime.now(UTC).timestamp())
            expires = min(issued + self._seconds, int(self._until.timestamp()))
            if expires <= issued:
                raise EngineUnavailableError()
            if purpose == "human-command":
                result = seal(
                    command, context=self._context, issued_at=issued, expires_at=expires, sign=self._key.sign
                )
            elif purpose == "human-receipt":
                query = dict(
                    schema="human-receipt-query.v1",
                    tenant=command.tenant,
                    workload_ref=command.workload_ref,
                    task_id=command.task_id,
                    command_id=command.command_id,
                    principal_ref=command.principal_ref,
                    principal_issuer=command.principal_issuer,
                    principal_subject=command.principal_subject,
                    payload_digest=command.digest,
                )
                body = dict(
                    schema="human-envelope.v1",
                    purpose=purpose,
                    algorithm="Ed25519",
                    audience=self._context.audience,
                    issuer=self.scope.workload_ref,
                    tenant=self.scope.tenant,
                    key_id=self._context.key_id,
                    issued_at=str(issued),
                    expires_at=str(expires),
                    digest=hashlib.sha256(canonicalize(query)).hexdigest(),
                    command=query,
                )
                body["signature"] = (
                    base64.urlsafe_b64encode(self._key.sign(canonicalize(body))).rstrip(b"=").decode("ascii")
                )
                result = canonicalize(body)
            else:
                raise EngineUnavailableError()
            self._current()
            if len(result) > 65536:
                raise EngineUnavailableError()
            return result
        except Exception:
            raise EngineUnavailableError() from None


@dataclass(frozen=True)
class HumanTLSIdentity:
    scope: Scope
    ca_file: Path = field(repr=False)
    certificate_file: Path = field(repr=False)
    private_key_file: Path = field(repr=False)

    def context(self) -> ssl.SSLContext:
        try:
            if any(
                not path.is_absolute() or not path.is_file()
                for path in (self.ca_file, self.certificate_file, self.private_key_file)
            ):
                raise EngineUnavailableError()
            result = ssl.create_default_context(cafile=str(self.ca_file))
            result.minimum_version = ssl.TLSVersion.TLSv1_2
            result.load_cert_chain(str(self.certificate_file), str(self.private_key_file))
            return result
        except Exception:
            raise EngineUnavailableError() from None


class HumanEngineTransport(ABC):
    scope: Scope

    @abstractmethod
    async def receipt(self, command: HumanCommand | HumanDecisionCommand) -> bytes | None:
        """Authenticated exact receipt query; only authenticated RECEIPT_NOT_FOUND is None."""
        raise NotImplementedError

    @abstractmethod
    async def dispatch(self, command: HumanCommand | HumanDecisionCommand) -> bytes:
        """Send the immutable canonical command in a current signed envelope."""
        raise NotImplementedError


class MTLSHumanEngineTransport(HumanEngineTransport):
    def __init__(
        self,
        *,
        scope: Scope,
        endpoint: str,
        identity: HumanTLSIdentity,
        signer: CurrentHumanSigner,
        timeout_seconds: int,
    ) -> None:
        self.scope = Scope.model_validate(scope)
        try:
            url = urlsplit(endpoint)
            if (
                url.scheme != "https"
                or not url.hostname
                or url.username is not None
                or url.password is not None
                or url.query
                or url.fragment
                or "?" in endpoint
                or "#" in endpoint
                or any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in endpoint)
                or identity.scope != scope
                or signer.scope != scope
            ):
                raise EngineUnavailableError()
            self._endpoint = endpoint.rstrip("/")
            self._timeout = _positive_seconds(timeout_seconds)
            self._tls = identity.context()
            if not self._tls.check_hostname or self._tls.verify_mode != ssl.CERT_REQUIRED:
                raise EngineUnavailableError()
            self._signer = signer
        except Exception:
            raise EngineUnavailableError() from None

    async def _request(self, command: HumanCommand | HumanDecisionCommand, *, query: bool) -> bytes | None:
        try:
            if (
                command.tenant != self.scope.tenant
                or command.workload_ref != self.scope.workload_ref
                or (
                    isinstance(command, HumanDecisionCommand)
                    and command.environment != self.scope.environment
                )
            ):
                raise EngineUnavailableError()
            # Matches the engine servlet's path grammar; never encode a delimiter into a URL.
            if not all(
                re.fullmatch(r"[A-Za-z0-9_.:@-]{1,255}", v) for v in (command.task_id, command.command_id)
            ):
                raise EngineUnavailableError()
            envelope = self._signer.envelope(command, purpose="human-receipt" if query else "human-command")
            path = f"/v1/receipts/{command.task_id}/{command.command_id}" if query else "/v1/commands"
            headers = {"Accept": "application/json", "Content-Type": "application/json"}
            if query:
                headers["Authorization"] = "Maezo-Human " + base64.urlsafe_b64encode(envelope).rstrip(
                    b"="
                ).decode("ascii")
            async with (
                httpx.AsyncClient(
                    verify=self._tls, timeout=self._timeout, trust_env=False, follow_redirects=False
                ) as client,
                client.stream(
                    "GET" if query else "POST",
                    self._endpoint + path,
                    headers=headers,
                    content=None if query else envelope,
                ) as response,
            ):
                if (
                    response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
                    != "application/json"
                ):
                    raise EngineUnavailableError()
                raw = bytearray()
                async for chunk in response.aiter_bytes():
                    raw.extend(chunk)
                    if len(raw) > 65536:
                        raise EngineUnavailableError()
                payload = bytes(raw)
                if response.status_code == 200:
                    verify_engine_receipt(payload, command)
                    return payload
                error = strict_loads(payload)
                if query and response.status_code == 404 and error == {"error": "RECEIPT_NOT_FOUND"}:
                    return None
                if response.status_code == 409:
                    for code in ("REVISION_CONFLICT", "COMMAND_CONFLICT", "FORM_NOT_ACTIVATED"):
                        if error == {"error": code}:
                            raise EngineConflictError(code)
                raise EngineUnavailableError()
        except EngineConflictError:
            raise
        except Exception:
            raise EngineUnavailableError() from None

    async def receipt(self, command: HumanCommand | HumanDecisionCommand) -> bytes | None:
        return await self._request(command, query=True)

    async def dispatch(self, command: HumanCommand | HumanDecisionCommand) -> bytes:
        receipt = await self._request(command, query=False)
        if receipt is None:
            raise EngineUnavailableError()
        return receipt

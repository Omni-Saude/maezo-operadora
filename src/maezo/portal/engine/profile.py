"""ADR-0049 D3/D5: human-command.v1, a number-free RFC 8785 subprofile.

No JSON numeric token is permitted: revisions, timestamps and arbitrary-precision
integers travel as canonical decimal strings. String ordering is UTF-16 code-unit
ordering, without Unicode normalization. Credentials are supplied by the human
Gateway's dedicated signing port; this package never reads secrets or signs with
an agent, OIDC, PHI or A2A credential.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
from collections.abc import Callable
from dataclasses import asdict, dataclass
from typing import Any, Literal

_COMMAND_SCHEMA: Literal["human-command.v1"] = "human-command.v1"
_ENVELOPE_SCHEMA = "human-envelope.v1"
_DECIMAL = re.compile(r"0|[1-9][0-9]*")
_REF = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:@/-]{0,254}")
_DIGEST = re.compile(r"[0-9a-f]{64}")


class ProfileError(ValueError):
    """Malformed data: messages deliberately exclude payload material."""


def _string(value: str) -> str:
    if any(0xD800 <= ord(c) <= 0xDFFF for c in value):
        raise ProfileError("invalid Unicode scalar")
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def canonicalize(value: Any) -> bytes:
    """Serialize only the RFC8785 JSON subset accepted by human-envelope.v1."""

    def encode(item: Any, depth: int) -> str:
        if depth > 32:
            raise ProfileError("JSON nesting limit exceeded")
        if item is None:
            return "null"
        if type(item) is bool:
            return "true" if item else "false"
        if type(item) is str:
            return _string(item)
        if type(item) is list:
            return "[" + ",".join(encode(x, depth + 1) for x in item) + "]"
        if type(item) is dict:
            if not all(type(k) is str for k in item):
                raise ProfileError("JSON object keys must be strings")
            for k in item:
                _string(k)
            keys = sorted(item, key=lambda k: k.encode("utf-16-be"))
            return "{" + ",".join(_string(k) + ":" + encode(item[k], depth + 1) for k in keys) + "}"
        raise ProfileError("human-envelope.v1 forbids JSON numbers and custom objects")

    return encode(value, 0).encode("utf-8")


def strict_loads(data: bytes) -> Any:
    """Reject duplicate names, numeric tokens, malformed UTF-8 and lone surrogates."""

    if len(data) > 65536:
        raise ProfileError("JSON message size limit exceeded")

    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in items:
            if key in result:
                raise ProfileError("duplicate JSON member")
            result[key] = value
        return result

    def number(_: str) -> Any:
        raise ProfileError("human-envelope.v1 forbids JSON numeric tokens")

    try:
        result = json.loads(
            data.decode("utf-8"),
            object_pairs_hook=pairs,
            parse_int=number,
            parse_float=number,
            parse_constant=number,
        )
        canonicalize(result)
        return result
    except (UnicodeError, json.JSONDecodeError, RecursionError) as exc:
        raise ProfileError("invalid JSON") from exc


@dataclass(frozen=True, slots=True, kw_only=True)
class HumanCommand:
    """Internal projection, not a browser DTO or a generic engine-variable map.

    Only the synthetic nonclinical binding has an active Java decision projector.
    Production form activation requires ADR0049 D3 contract reconciliation.
    """

    tenant: str
    task_id: str
    command_id: str
    principal_ref: str
    principal_issuer: str
    principal_subject: str
    workload_ref: str
    operation: Literal["claim", "release", "decision"]
    process_definition_id: str
    process_definition_key: str
    process_definition_version: str
    process_definition_digest: str
    task_definition_key: str
    form_key: str
    form_version: str
    form_digest: str
    task_revision: str
    authority_revision: str
    membership_revision: str
    evidence_revision: str
    evidence_ref: str
    evidence_digest: str
    assignee_ref: str | None
    audit_intent_ref: str
    outcome: Literal["ACK"] | None
    schema: Literal["human-command.v1"] = _COMMAND_SCHEMA

    def __post_init__(self) -> None:
        fields = asdict(self)
        for key, value in fields.items():
            if key in {"assignee_ref", "outcome"} and value is None:
                continue
            if type(value) is not str or not value or any(ord(c) < 32 or ord(c) == 127 for c in value):
                raise ProfileError("invalid command field")
            _string(value)
        for key in (
            "process_definition_version",
            "form_version",
            "task_revision",
            "authority_revision",
            "membership_revision",
            "evidence_revision",
        ):
            if not _DECIMAL.fullmatch(fields[key]):
                raise ProfileError("revision must be a canonical decimal string")
        for key in ("process_definition_digest", "form_digest", "evidence_digest"):
            if not _DIGEST.fullmatch(fields[key]):
                raise ProfileError("invalid digest")
        for key in fields.keys() - {
            "principal_issuer",
            "outcome",
            "assignee_ref",
            "process_definition_version",
            "form_version",
            "task_revision",
            "authority_revision",
            "membership_revision",
            "evidence_revision",
        }:
            if not _REF.fullmatch(fields[key]):
                raise ProfileError("invalid reference")
        if self.assignee_ref is not None and not _REF.fullmatch(self.assignee_ref):
            raise ProfileError("invalid assignee reference")
        if self.schema != _COMMAND_SCHEMA or self.operation not in {"claim", "release", "decision"}:
            raise ProfileError("unsupported command schema or operation")
        if self.outcome != ("ACK" if self.operation == "decision" else None):
            raise ProfileError("invalid typed outcome")

    @property
    def canonical(self) -> bytes:
        return canonicalize(asdict(self))

    @property
    def digest(self) -> str:
        return hashlib.sha256(self.canonical).hexdigest()


@dataclass(frozen=True, slots=True, kw_only=True)
class SigningContext:
    """Explicit deployment configuration; no inherited validity or algorithm default."""

    tenant: str
    audience: str
    workload_ref: str
    key_id: str
    max_lifetime_seconds: int

    def __post_init__(self) -> None:
        for value in (self.tenant, self.audience, self.workload_ref, self.key_id):
            if type(value) is not str or not _REF.fullmatch(value):
                raise ProfileError("invalid signing configuration")
        if type(self.max_lifetime_seconds) is not int or not 0 < self.max_lifetime_seconds <= 2**63 - 1:
            raise ProfileError("explicit positive signature lifetime required")


def seal(
    command: HumanCommand,
    *,
    context: SigningContext,
    issued_at: int,
    expires_at: int,
    sign: Callable[[bytes], bytes],
) -> bytes:
    """Sign through a purpose-scoped port supplied by the Gateway credential partition.

    The supplied signer MUST be the configured Ed25519 human-command signer.
    This is wire construction only: it neither admits work nor bypasses D6 outbox.
    """
    if command.tenant != context.tenant or command.workload_ref != context.workload_ref:
        raise ProfileError("signing scope mismatch")
    if (
        type(issued_at) is not int
        or type(expires_at) is not int
        or not 0 <= issued_at < expires_at <= 2**63 - 1
        or not 0 < expires_at - issued_at <= context.max_lifetime_seconds
    ):
        raise ProfileError("invalid explicit validity")
    body: dict[str, Any] = {
        "schema": _ENVELOPE_SCHEMA,
        "purpose": "human-command",
        "algorithm": "Ed25519",
        "audience": context.audience,
        "issuer": context.workload_ref,
        "tenant": context.tenant,
        "key_id": context.key_id,
        "issued_at": str(issued_at),
        "expires_at": str(expires_at),
        "digest": command.digest,
        "command": asdict(command),
    }
    signature = sign(canonicalize(body))
    if type(signature) is not bytes or len(signature) != 64:
        raise ProfileError("invalid Ed25519 signature result")
    body["signature"] = base64.urlsafe_b64encode(signature).rstrip(b"=").decode("ascii")
    return canonicalize(body)

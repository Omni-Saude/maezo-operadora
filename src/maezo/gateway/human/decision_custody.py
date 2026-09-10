"""Durable complete human form custody in PHI, ADR0049 D3/D6 and ADR0006/0007.

AUTH mandatory clinical basis, ESC resolution notes and PAGTO admissibility inputs
are preserved unchanged. This module neither authorizes human decisions nor hydrates
General workers. Ciphertext only enters PostgreSQL; raw canonical bytes are returned
solely through the explicit PHI resolver. General receives DecisionCustodyRecord.
"""

from __future__ import annotations

import hashlib
import json
import os
from abc import ABC, abstractmethod
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import uuid4

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from pydantic import Field

from maezo.portal.contracts.models import HumanPrincipal
from maezo.portal.engine.profile import canonicalize

from .decision import AuthorizedDecision, DecisionCustodyRecord, HumanDecisionCustody
from .decision_custody_connection import (
    DecisionCustodyConflictError,
    DecisionCustodyError,
    PhiPostgresConnection,
)
from .models import Closed, Scope, Timed


class CustodyAccess(Closed):
    """Exact scope and caller to independently authenticate/currently authorize in PHI.

    A principal DTO and a previous custody record confer no access by themselves.
    Provider must recheck authoritative membership, task/resource permissions, consent
    and deployment qualification. Resolve may follow task completion only if the
    actual consumer contract permits it; preserve never makes stale decisions current.
    """

    record: DecisionCustodyRecord
    principal: HumanPrincipal = Field(repr=False)
    operation: Literal["preserve", "resolve"]


class CustodyAuthorization(Timed):
    access: CustodyAccess = Field(repr=False)


class CurrentPhiDecisionAuthorization(ABC):
    scope: Scope

    @abstractmethod
    async def authorize(self, access: CustodyAccess) -> CustodyAuthorization:
        """Authenticate current sources, refuse unknown/stale/revoked access; no DTO grant."""
        raise NotImplementedError


@dataclass(frozen=True, repr=False)
class PhiDecisionBytes:
    """PHI-only result. Never attach this to HTTP/General audit/engine/logging objects."""

    record: DecisionCustodyRecord
    canonical: bytes = field(repr=False)


_KEY = ("tenant", "environment", "workload_ref", "task_id", "command_id")
_AAD = (*_KEY, "principal_ref", "request_digest", "content_digest", "custody_ref", "key_id")
_SELECT = """SELECT * FROM maezo_phi_decision.human_decision
WHERE tenant=$1 AND environment=$2 AND workload_ref=$3 AND task_id=$4 AND command_id=$5"""
_INSERT = """INSERT INTO maezo_phi_decision.human_decision
(tenant, environment, workload_ref, task_id, command_id, principal_ref, request_digest,
 content_digest, custody_ref, key_id, nonce, ciphertext)
VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12)
ON CONFLICT (tenant, environment, workload_ref, task_id, command_id) DO NOTHING"""


def _metadata(record: DecisionCustodyRecord, key_id: str) -> dict[str, str]:
    return dict(
        **record.scope.model_dump(),
        task_id=record.task_id,
        command_id=record.command_id,
        principal_ref=record.principal_ref,
        request_digest=record.request_digest,
        content_digest=record.content_digest,
        custody_ref=record.custody_ref,
        key_id=key_id,
    )


def _aad(row: Mapping[str, Any]) -> bytes:
    return canonicalize({"schema": "phi-human-decision-custody.v1", **{k: row[k] for k in _AAD}})


class PostgresHumanDecisionCustody(HumanDecisionCustody):
    def __init__(
        self,
        *,
        connection: PhiPostgresConnection,
        keys: Mapping[str, AESGCM],
        authorization: CurrentPhiDecisionAuthorization,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        """Gateway-owned composition only. Keys are explicitly injected vault material.

        Active kid encrypts new rows; explicitly retained kids decrypt immutable old
        rows after rotation. Removing a key fails closed; no fallback or reciphering.
        Tests inject SQL unit fixtures; production must use PhiPostgresConnection.
        """
        self.scope = connection.deployment.scope
        self._connection = connection
        self._authorization = authorization
        self._keys = dict(keys)
        self._clock = clock
        if (
            authorization.scope != self.scope
            or set(self._keys) != set(connection.deployment.readable_key_ids)
            or any(not isinstance(key, AESGCM) for key in self._keys.values())
        ):
            raise DecisionCustodyError()

    async def _current(self, access: CustodyAccess, *deadlines: datetime) -> datetime:
        if (
            self._authorization.scope != self.scope
            or self._connection.deployment.scope != self.scope
            or access.record.scope != self.scope
            or access.principal.tenant != self.scope.tenant
            or access.principal.principal_ref != access.record.principal_ref
            or access.principal.principal_ref == self.scope.workload_ref
        ):
            raise DecisionCustodyError()
        grant = CustodyAuthorization.model_validate(await self._authorization.authorize(access))
        limit = min(grant.valid_until, self._connection.current(), *deadlines)
        if grant.access != access or self._clock() >= limit:
            raise DecisionCustodyError()
        return limit

    def _open(self, row: Mapping[str, Any], access: CustodyAccess) -> bytes:
        expected = _metadata(access.record, row["key_id"])
        # On preserve, an already committed row supplies the stable custody reference.
        if access.operation == "preserve":
            expected["custody_ref"] = row["custody_ref"]
        if any(row[k] != expected[k] for k in _AAD):
            raise DecisionCustodyConflictError()
        raw = self._keys[row["key_id"]].decrypt(bytes(row["nonce"]), bytes(row["ciphertext"]), _aad(row))
        body = json.loads(raw)
        # Custody is not the 64 KiB transport envelope: do not truncate valid full forms.
        # Exact canonical equality below rejects duplicates, numbers and altered bytes.
        if (
            canonicalize(body) != raw
            or hashlib.sha256(raw).hexdigest() != access.record.request_digest
            or hashlib.sha256(canonicalize(body["inputs"])).hexdigest() != access.record.content_digest
            or body["scope"] != self.scope.model_dump()
            or body["principal_ref"] != access.principal.principal_ref
            or body["principal_issuer"] != access.principal.issuer
            or body["principal_subject"] != access.principal.subject
            or body["target"]["task_id"] != access.record.task_id
            or body["target"]["command_id"] != access.record.command_id
        ):
            raise DecisionCustodyError()
        return raw

    async def preserve(self, request: AuthorizedDecision) -> DecisionCustodyRecord:
        try:
            request = AuthorizedDecision.model_validate(request)
            if request.decision.expected_membership_revision != request.principal.membership_revision:
                raise DecisionCustodyError()
            record = DecisionCustodyRecord(
                scope=request.scope,
                principal_ref=request.principal.principal_ref,
                task_id=request.decision.task_id,
                command_id=request.decision.command_id,
                request_digest=request.request_digest,
                content_digest=request.content_digest,
                custody_ref=f"phi-decision:{uuid4()}",
                valid_until=self._connection.current(),
            )
            access = CustodyAccess(record=record, principal=request.principal, operation="preserve")
            deadline = await self._current(access)
            row = _metadata(record, self._connection.deployment.active_key_id)
            nonce = os.urandom(12)
            raw = request.canonical
            encrypted = self._keys[row["key_id"]].encrypt(nonce, raw, _aad(row))
            async with self._connection.transaction() as connection:
                await connection.execute(_INSERT, *(row[k] for k in _AAD), nonce, encrypted)
                stored = await connection.fetchrow(_SELECT, *(row[k] for k in _KEY))
                if stored is None or self._open(stored, access) != raw:
                    raise DecisionCustodyConflictError()
                deadline = await self._current(access, deadline)
                record = record.model_copy(update={"custody_ref": stored["custody_ref"]})
                access = access.model_copy(update={"record": record})
            # Never acknowledge before COMMIT, and never describe a lost ACK as rollback.
            deadline = await self._current(access, deadline)
            return DecisionCustodyRecord.model_validate(record.model_copy(update={"valid_until": deadline}))
        except DecisionCustodyConflictError:
            failure: DecisionCustodyError = DecisionCustodyConflictError()
        except Exception:
            failure = DecisionCustodyError()
        # Normalize provider-owned expected errors too, without retaining their
        # traceback chain or notes; only the safe conflict classification survives.
        raise failure from None

    async def resolve(self, record: DecisionCustodyRecord, *, principal: HumanPrincipal) -> PhiDecisionBytes:
        """PHI-side exact resolution; a known ID or previous valid_until grants nothing.

        A record's expired assurance is not deletion. Fresh independent authorization
        is required even for retries; current policy decides access after completion.
        """
        try:
            record = DecisionCustodyRecord.model_validate(record)
            principal = HumanPrincipal.model_validate(principal)
            access = CustodyAccess(record=record, principal=principal, operation="resolve")
            deadline = await self._current(access)
            metadata = _metadata(record, self._connection.deployment.active_key_id)
            async with self._connection.transaction() as connection:
                stored = await connection.fetchrow(_SELECT, *(metadata[k] for k in _KEY))
                if stored is None:
                    raise DecisionCustodyError()
                raw = self._open(stored, access)
                deadline = await self._current(access, deadline)
            deadline = await self._current(access, deadline)
            fresh = DecisionCustodyRecord.model_validate(record.model_copy(update={"valid_until": deadline}))
            return PhiDecisionBytes(fresh, raw)
        except DecisionCustodyConflictError:
            failure: DecisionCustodyError = DecisionCustodyConflictError()
        except Exception:
            failure = DecisionCustodyError()
        # Normalize provider-owned expected errors too, without retaining their
        # traceback chain or notes; only the safe conflict classification survives.
        raise failure from None

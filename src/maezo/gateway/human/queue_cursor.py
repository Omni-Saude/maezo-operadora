"""Q2 AES-GCM-SIV cursor custody, with synchronous min-only finalization."""

from __future__ import annotations

import base64
import secrets
from datetime import datetime

from cryptography.exceptions import InvalidTag

from maezo.portal.engine.profile import ProfileError, canonicalize, strict_loads

from .queue import CursorCustody, CursorGrant, QueueBinding, ReadRefusalError
from .read_credentials import (
    CursorKeyLease,
    CursorKeySet,
    QueueCursorKeyProvider,
    ReadCredentialPartition,
    unavailable,
)
from .read_profile import b64decode, digest, instant, utc, wire


class AeadQueueCursorCustody(CursorCustody):
    def __init__(
        self, *, partition: ReadCredentialPartition, provider: QueueCursorKeyProvider, key_id: str
    ) -> None:
        self.scope = partition.scope
        self._partition, self._provider, self._key_id = partition, provider, key_id
        self._keys: CursorKeySet | None = None

    async def prepare(self) -> None:
        await self._partition.prepare()
        if self._keys is None:
            self._keys = await self._provider.acquire(self.scope, self._key_id)
        self._guard()

    def _guard(self) -> datetime:
        now = self._partition.guard()
        if (
            self._keys is None
            or self._keys.current.scope != self.scope
            or self._keys.current.key_id != self._key_id
        ):
            raise unavailable()
        self._keys.require_current(now)
        return now

    def _context(self) -> dict[str, object]:
        admission = self._partition.admission
        if admission is None:
            raise unavailable()
        r = admission.record
        return {
            "scope": wire(r.scope),
            "engine_name": r.engine_name,
            "database_incarnation": r.database_incarnation,
            "read_deployment_ref": r.read_deployment_ref,
            "read_deployment_digest": r.read_deployment_digest,
        }

    def _aad(self, key: CursorKeyLease) -> bytes:
        return canonicalize(
            {"schema": "portal-queue-cursor.v1", **self._context(), "key_tag": key.key_tag.hex()}
        )

    def _binding(self, binding: QueueBinding) -> str:
        if binding.scope != self.scope:
            raise ReadRefusalError("refresh_required")
        return digest({"binding": wire(binding), **self._context()})

    def _open(self, cursor: str, binding: QueueBinding) -> CursorGrant:
        now = self._guard()
        try:
            if not 1 <= len(cursor) <= 2048:
                raise ProfileError("cursor size")
            raw = b64decode(cursor, url=True)
            if len(raw) < 46 or raw[0] != 1:
                raise ProfileError("cursor version")
            assert self._keys is not None
            matches = [key for key in self._keys.accepted if key.key_tag == raw[1:17]]
            if len(matches) != 1:
                raise ReadRefusalError("refresh_required")
            key = matches[0]
            key.require_current(now)
            data = key.decrypt(raw[17:29], raw[29:], self._aad(key), now)
            value = strict_loads(data)
            if (
                type(value) is not dict
                or value.keys() != {"schema", "binding_digest", "after_task_id", "valid_until"}
                or canonicalize(value) != data
                or value["schema"] != "portal-queue-cursor.v1"
            ):
                raise ProfileError("cursor record")
            until = instant(value["valid_until"])
            if value["binding_digest"] != self._binding(binding) or until <= now:
                raise ReadRefusalError("refresh_required")
            assert self._partition.admission is not None
            if until > min(key.not_after, self._partition.admission.record.valid_until):
                raise ProfileError("cursor ceiling")
            result = CursorGrant(
                cursor=cursor, binding=binding, after_task_id=value["after_task_id"], valid_until=until
            )
            self._guard()
            return result
        except (ProfileError, ValueError, InvalidTag):
            raise ReadRefusalError("invalid_request") from None

    def provisional(self, *, binding: QueueBinding, after_task_id: str, valid_until: datetime) -> CursorGrant:
        now = self._guard()
        assert (
            self._keys is not None
            and self._partition.admission is not None
            and self._partition.signing is not None
        )
        key = self._keys.current
        until = min(
            valid_until,
            key.not_after,
            self._partition.admission.record.valid_until,
            self._partition.signing.not_after,
        )
        if until <= now:
            raise unavailable()
        payload = {
            "schema": "portal-queue-cursor.v1",
            "binding_digest": self._binding(binding),
            "after_task_id": after_task_id,
            "valid_until": utc(until),
        }
        nonce = secrets.token_bytes(12)
        encrypted = key.encrypt(nonce, canonicalize(payload), self._aad(key), now)
        token = (
            base64.urlsafe_b64encode(b"\x01" + key.key_tag + nonce + encrypted).rstrip(b"=").decode("ascii")
        )
        result = CursorGrant(cursor=token, binding=binding, after_task_id=after_task_id, valid_until=until)
        self._guard()
        return result

    async def resolve(self, cursor: str, *, binding: QueueBinding) -> CursorGrant:
        await self.prepare()
        return self._open(cursor, binding)

    def finalize(
        self, cursor: str, *, binding: QueueBinding, after_task_id: str, valid_until: datetime
    ) -> CursorGrant:
        original = self._open(cursor, binding)
        if original.after_task_id != after_task_id or valid_until > original.valid_until:
            raise unavailable()
        result = self.provisional(binding=binding, after_task_id=after_task_id, valid_until=valid_until)
        if result.valid_until != valid_until:
            raise unavailable()
        return result

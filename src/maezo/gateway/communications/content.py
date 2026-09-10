"""PHI-only encrypted content mechanism. Never instantiate in the General BFF.

Key/database residency, roles/TLS and retention are independently qualified deployment
inputs; this code does not invent them. Plaintext stays in this service and its PHI API.
"""

import hashlib
import os
from collections.abc import Callable
from datetime import datetime
from typing import Any
from uuid import uuid4

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from maezo.gateway.external_cases.models import ExternalCaseError
from maezo.gateway.external_cases.postgres import transaction
from maezo.portal.api.session import HumanSessionResolver
from maezo.portal.contracts.communication_content import (
    CommunicationContent,
    CommunicationContentReceipt,
    CommunicationContentSubmission,
)
from maezo.portal.engine.profile import canonicalize

from .models import (
    CommunicationAuthority,
    CommunicationGrant,
    CommunicationScope,
    alive,
    fingerprint,
    identity,
)
from .service import CommunicationSessionBoundary

_AAD = (
    "tenant",
    "environment",
    "case_ref",
    "sender_identity_digest",
    "command_id",
    "body_ref",
    "request_digest",
    "content_digest",
    "key_id",
)


class PhiContentKeys:
    def __init__(
        self, *, scope: CommunicationScope, active_key_id: str, keys: dict[str, bytes], valid_until: datetime
    ) -> None:
        if active_key_id not in keys or any(
            not key_id or type(key) is not bytes or len(key) != 32 for key_id, key in keys.items()
        ):
            raise ExternalCaseError("unavailable")
        self.scope, self.active_key_id, self.valid_until = scope, active_key_id, valid_until
        self._keys = {name: AESGCM(key) for name, key in keys.items()}
        alive(valid_until)

    def cipher(self, key_id: str) -> AESGCM:
        alive(self.valid_until)
        cipher = self._keys.get(key_id)
        if cipher is None:
            raise ExternalCaseError("unavailable")
        return cipher


class PostgresPhiCommunicationContent:
    def __init__(
        self, engine: AsyncEngine, *, scope: CommunicationScope, keys: PhiContentKeys, seconds: float = 5
    ) -> None:
        if keys.scope != scope:
            raise ExternalCaseError("unavailable")
        self.engine, self.scope, self.keys, self.seconds = engine, scope, keys, seconds

    def params(self, grant: CommunicationGrant) -> dict[str, Any]:
        if grant.access.scope != self.scope or grant.access.principal.tenant != self.scope.tenant:
            raise ExternalCaseError("denied")
        return dict(
            **self.scope.model_dump(),
            case_ref=grant.access.case_ref,
            sender_identity_digest=identity(grant.access.principal),
        )

    async def preserve(
        self, grant: CommunicationGrant, body: CommunicationContentSubmission, *, deadline: datetime
    ) -> CommunicationContentReceipt:
        params = self.params(grant)
        ceiling = alive(deadline, grant.ceiling(), self.keys.valid_until)
        digest = fingerprint(body.model_dump(mode="json"))
        if (
            grant.access.operation != "create_content"
            or grant.access.request_digest != digest
            or "body" not in grant.permitted_fields
        ):
            raise ExternalCaseError("denied")
        raw = body.body.encode("utf-8")
        params.update(
            command_id=body.command_id,
            body_ref=uuid4().hex,
            request_digest=digest,
            content_digest=hashlib.sha256(raw).hexdigest(),
            key_id=self.keys.active_key_id,
        )
        nonce = os.urandom(12)
        ciphertext = self.keys.cipher(self.keys.active_key_id).encrypt(
            nonce, raw, canonicalize({k: params[k] for k in _AAD})
        )
        params.update(
            nonce=nonce,
            ciphertext=ciphertext,
            authority_ref=grant.authority_receipt_ref,
            authority_digest=grant.authority_digest,
        )
        result = None
        async with transaction(self.engine, self.seconds) as connection:
            await connection.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:lock,0))"),
                {
                    "lock": fingerprint(
                        {
                            "scope": self.scope.model_dump(),
                            "identity": params["sender_identity_digest"],
                            "command": body.command_id,
                            "purpose": "content",
                        }
                    )
                },
            )
            existing = (
                (
                    await connection.execute(
                        text(
                            "SELECT body_ref,case_ref,request_digest FROM portal_communication.content "
                            "WHERE tenant=:tenant AND environment=:environment AND "
                            "sender_identity_digest=:sender_identity_digest "
                            "AND command_id=:command_id"
                        ),
                        params,
                    )
                )
                .mappings()
                .one_or_none()
            )
            if existing is not None:
                if existing["case_ref"] != grant.access.case_ref or existing["request_digest"] != digest:
                    raise ExternalCaseError("conflict")
                result = CommunicationContentReceipt(
                    body_ref=existing["body_ref"], command_id=body.command_id
                )
            else:
                await connection.execute(
                    text(
                        "INSERT INTO portal_communication.content "
                        "(tenant,environment,case_ref,sender_identity_digest,command_id,body_ref,"
                        "request_digest,content_digest,"
                        "key_id,nonce,ciphertext,authority_receipt_ref,authority_digest) VALUES "
                        "(:tenant,:environment,:case_ref,:sender_identity_digest,:command_id,:body_ref,"
                        ":request_digest,"
                        ":content_digest,:key_id,:nonce,:ciphertext,:authority_ref,:authority_digest)"
                    ),
                    params,
                )
                result = CommunicationContentReceipt(body_ref=params["body_ref"], command_id=body.command_id)
            alive(ceiling)
        alive(ceiling)
        if result is None:
            raise ExternalCaseError("uncertain")
        return result

    async def read(
        self, grant: CommunicationGrant, *, communication_ref: str, audience: str, deadline: datetime
    ) -> CommunicationContent:
        params = self.params(grant)
        ceiling = alive(deadline, grant.ceiling(), self.keys.valid_until)
        if (
            grant.access.operation != "read_content"
            or grant.access.resource_ref != communication_ref
            or not {"communication_ref", "body"}.issubset(grant.permitted_fields)
        ):
            raise ExternalCaseError("denied")
        params.update(communication=communication_ref, audience=audience)
        async with transaction(self.engine, self.seconds) as connection:
            row = (
                (
                    await connection.execute(
                        text(
                            "SELECT c.* FROM portal_communication.content c "
                            "JOIN portal_communication.message m ON m.tenant=c.tenant AND "
                            "m.environment=c.environment "
                            "AND m.case_ref=c.case_ref AND m.body_ref=c.body_ref JOIN "
                            "portal_communication.inbox i "
                            "ON i.tenant=m.tenant AND i.environment=m.environment AND "
                            "i.communication_ref=m.communication_ref "
                            "JOIN portal_communication.intended_recipient r ON r.tenant=i.tenant AND "
                            "r.environment=i.environment "
                            "AND r.communication_ref=i.communication_ref AND "
                            "r.recipient_identity_digest=i.recipient_identity_digest "
                            "WHERE c.tenant=:tenant AND c.environment=:environment AND c.case_ref=:case_ref "
                            "AND m.communication_ref=:communication AND "
                            "i.recipient_identity_digest=:sender_identity_digest "
                            "AND r.audience=:audience"
                        ),
                        params,
                    )
                )
                .mappings()
                .one_or_none()
            )
            if row is None:
                raise ExternalCaseError("denied")
            if (
                row["tenant"] != self.scope.tenant
                or row["environment"] != self.scope.environment
                or row["case_ref"] != grant.access.case_ref
            ):
                raise ExternalCaseError("denied")
            try:
                raw = self.keys.cipher(row["key_id"]).decrypt(
                    bytes(row["nonce"]), bytes(row["ciphertext"]), canonicalize({k: row[k] for k in _AAD})
                )
                if hashlib.sha256(raw).hexdigest() != row["content_digest"]:
                    raise ValueError
                body = raw.decode("utf-8")
            except Exception:
                raise ExternalCaseError("unavailable") from None
            result = CommunicationContent(communication_ref=communication_ref, body=body)
            alive(ceiling)
        alive(ceiling)
        return result


class PhiCommunicationService(CommunicationSessionBoundary):
    def __init__(
        self,
        resolver: HumanSessionResolver,
        authority: CommunicationAuthority,
        store: PostgresPhiCommunicationContent,
    ) -> None:
        super().__init__(resolver, authority, store.scope)
        self.store = store

    async def preserve[T](
        self,
        secret: str,
        *,
        csrf: str,
        origin: str,
        case_ref: str,
        body: CommunicationContentSubmission,
        freeze: Callable[[bytes], T],
    ) -> T:
        session = await self.session(secret, mutation=True, csrf=csrf, origin=origin)
        grant = await self.grant(
            session,
            operation="create_content",
            case_ref=case_ref,
            request_digest=fingerprint(body.model_dump(mode="json")),
        )
        ceiling = alive(
            session.record.expires_at,
            session.membership.reviewed_until,
            grant.ceiling(),
            self.store.keys.valid_until,
        )
        result = await self.store.preserve(grant, body, deadline=ceiling)
        return await self.finish(
            secret, session, [grant], result, ceiling, self.store.keys.valid_until, freeze=freeze
        )

    async def read_content[T](
        self, secret: str, *, case_ref: str, communication_ref: str, freeze: Callable[[bytes], T]
    ) -> T:
        session = await self.session(secret)
        grant = await self.grant(
            session, operation="read_content", case_ref=case_ref, resource_ref=communication_ref
        )
        ceiling = alive(
            session.record.expires_at,
            session.membership.reviewed_until,
            grant.ceiling(),
            self.store.keys.valid_until,
        )
        result = await self.store.read(
            grant, communication_ref=communication_ref, audience=session.membership.audience, deadline=ceiling
        )
        return await self.finish(
            secret, session, [grant], result, ceiling, self.store.keys.valid_until, freeze=freeze
        )

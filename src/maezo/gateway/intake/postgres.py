"""Concrete encrypted intake + admission event in one PostgreSQL transaction.

The injected engine must be a separately qualified protected database connection;
this module neither provisions it nor treats constructor inputs as PHI-zone proof.
No engine I/O occurs under this transaction. No automatic retry of unknown commit.
"""

import hashlib
import os
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from maezo.gateway.external_cases.models import ExternalCaseError
from maezo.gateway.external_cases.postgres import transaction
from maezo.portal.contracts.intake import AuthIntakeSubmission, IntakeReceipt
from maezo.portal.engine.profile import canonicalize

from .models import AdmissionGrant, IntakeError, request_bytes


class PostgresIntakeStore:
    def __init__(
        self, engine: AsyncEngine, *, tenant: str, key_id: str, encryption_key: bytes, seconds: float = 5
    ) -> None:
        if not tenant or not key_id or len(encryption_key) != 32:
            raise IntakeError()
        self.engine, self.tenant, self.key_id, self.seconds = engine, tenant, key_id, seconds
        self._cipher = AESGCM(encryption_key)

    @staticmethod
    def _receipt(row: Any) -> IntakeReceipt:
        return IntakeReceipt(
            intake_ref=row["intake_ref"],
            command_id=row["command_id"],
            revision=str(row["revision"]),
            disposition=row["disposition"],
            case_ref=row["case_ref"],
            start_receipt_ref=row["start_receipt_ref"],
        )

    async def admit(self, grant: AdmissionGrant, request: AuthIntakeSubmission) -> IntakeReceipt:
        raw = request_bytes(request)
        if (
            grant.principal.tenant != self.tenant
            or grant.request_digest != hashlib.sha256(raw).hexdigest()
            or datetime.now(UTC) >= grant.valid_until
        ):
            raise IntakeError("operation_forbidden")
        values = dict(
            tenant=self.tenant,
            principal=grant.principal.principal_ref,
            command=request.command_id,
            intake=uuid4().hex,
            guide=grant.guide_identity_ref,
            digest=grant.request_digest,
            authority_ref=grant.authority_receipt_ref,
            authority_digest=grant.authority_digest,
            key_id=self.key_id,
        )
        nonce = os.urandom(12)
        ciphertext = self._cipher.encrypt(nonce, raw, canonicalize(values))
        result = None
        error = None
        try:
            async with transaction(self.engine, self.seconds) as connection:
                # Separate serialization keys: one command across guides and one guide
                # across commands. Stable lock order prevents cross-key deadlocks.
                keys = sorted(
                    (
                        canonicalize(
                            [self.tenant, "command", values["principal"], request.command_id]
                        ).decode(),
                        canonicalize([self.tenant, "guide", grant.guide_identity_ref]).decode(),
                    )
                )
                for key in keys:
                    await connection.execute(
                        text("SELECT pg_advisory_xact_lock(hashtextextended(:key,0))"), {"key": key}
                    )
                row = (
                    (
                        await connection.execute(
                            text(
                                "SELECT * FROM portal_intake.intake WHERE tenant=:tenant "
                                "AND principal_ref=:principal AND command_id=:command"
                            ),
                            values,
                        )
                    )
                    .mappings()
                    .one_or_none()
                )
                if row is not None:
                    if (
                        row["request_digest"] != grant.request_digest
                        or row["guide_identity_ref"] != grant.guide_identity_ref
                    ):
                        raise ExternalCaseError("conflict")
                    result = self._receipt(row)
                else:
                    existing = (
                        await connection.execute(
                            text(
                                "SELECT intake_ref FROM portal_intake.intake WHERE tenant=:tenant "
                                "AND guide_identity_ref=:guide"
                            ),
                            values,
                        )
                    ).first()
                    if existing is not None:
                        raise ExternalCaseError("conflict")
                    await connection.execute(
                        text(
                            "INSERT INTO portal_intake.intake "
                            "(tenant,principal_ref,command_id,intake_ref,guide_identity_ref,request_digest,"
                            "authority_receipt_ref,authority_digest,key_id,nonce,ciphertext,disposition) "
                            "VALUES (:tenant,:principal,:command,:intake,:guide,:digest,:authority_ref,"
                            ":authority_digest,:key_id,:nonce,:ciphertext,'admitted')"
                        ),
                        dict(values, nonce=nonce, ciphertext=ciphertext),
                    )
                    await connection.execute(
                        text(
                            "INSERT INTO portal_intake.admission_event "
                            "(tenant,intake_ref,command_id,principal_ref,request_digest,"
                            "authority_receipt_ref,authority_digest) VALUES "
                            "(:tenant,:intake,:command,:principal,:digest,:authority_ref,:authority_digest)"
                        ),
                        values,
                    )
                    result = IntakeReceipt(
                        intake_ref=values["intake"],
                        command_id=request.command_id,
                        revision="0",
                        disposition="admitted",
                    )
                if datetime.now(UTC) >= grant.valid_until:
                    raise ExternalCaseError("denied")
        except ExternalCaseError as exc:
            error = IntakeError(
                "conflict"
                if exc.code == "conflict"
                else "operation_forbidden"
                if exc.code == "denied"
                else "dependency_unavailable"
            )
        except Exception:
            error = IntakeError()
        if error is not None or result is None:
            raise error or IntakeError() from None
        return result

    async def read(self, tenant: str, principal_ref: str, intake_ref: str) -> IntakeReceipt:
        if tenant != self.tenant:
            raise IntakeError("operation_forbidden")
        result = None
        try:
            async with transaction(self.engine, self.seconds) as connection:
                row = (
                    (
                        await connection.execute(
                            text(
                                "SELECT * FROM portal_intake.intake WHERE tenant=:tenant "
                                "AND principal_ref=:principal AND intake_ref=:intake"
                            ),
                            {"tenant": tenant, "principal": principal_ref, "intake": intake_ref},
                        )
                    )
                    .mappings()
                    .one_or_none()
                )
                if row is not None:
                    result = self._receipt(row)
        except Exception:
            raise IntakeError() from None
        if result is None:
            raise IntakeError("resource_unavailable")
        return result

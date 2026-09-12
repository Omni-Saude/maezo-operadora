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
from maezo.portal.engine.profile import canonicalize, strict_loads

from .models import AdmissionGrant, IntakeError, request_bytes
from .native_store import PostgresAuthDispatchStore


class PostgresIntakeStore:
    def __init__(
        self,
        engine: AsyncEngine,
        *,
        tenant: str,
        key_id: str,
        encryption_key: bytes,
        seconds: float = 5,
        native_dispatch: PostgresAuthDispatchStore | None = None,
    ) -> None:
        if not tenant or not key_id or len(encryption_key) != 32:
            raise IntakeError()
        self.engine, self.tenant, self.key_id, self.seconds = engine, tenant, key_id, seconds
        self._cipher = AESGCM(encryption_key)
        if native_dispatch is not None and (
            native_dispatch.engine is not engine or native_dispatch.tenant != tenant
        ):
            raise IntakeError()
        self.native_dispatch = native_dispatch

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

    async def admit(
        self, grant: AdmissionGrant, request: AuthIntakeSubmission, *, caller: Any = None
    ) -> IntakeReceipt:
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
        admitted_digest = hashlib.sha256(
            canonicalize(
                {
                    "schema": "human-auth-admission.v1",
                    "request": strict_loads(raw),
                    "guide_identity_ref": grant.guide_identity_ref,
                }
            )
        ).hexdigest()
        reservation = None
        if self.native_dispatch is not None:
            from .native_source_lifecycle import AuthCallerBinding

            source = self.native_dispatch.source
            if (
                type(caller) is not AuthCallerBinding
                or caller.principal != grant.principal
                or source is None
                or self.native_dispatch.scope is None
            ):
                raise IntakeError("dependency_unavailable")
            async with transaction(self.engine, self.seconds) as connection:
                prior = (
                    (
                        await connection.execute(
                            text(
                                "SELECT intake_ref,request_digest,guide_identity_ref FROM porta"
                                "l_intake.intake WHERE "
                                "tenant=:tenant AND principal_ref=:principal AND command_id=:command"
                            ),
                            values,
                        )
                    )
                    .mappings()
                    .one_or_none()
                )
                if prior is not None:
                    if (prior["request_digest"], prior["guide_identity_ref"]) != (
                        grant.request_digest,
                        grant.guide_identity_ref,
                    ):
                        raise IntakeError("conflict")
                    values["intake"] = prior["intake_ref"]
            reservation = await source.reserve(
                caller,
                scope=self.native_dispatch.scope,
                command_id=request.command_id,
                admission_ref=values["intake"],
                operation="auth.start",
                request_digest=grant.request_digest,
                admitted_digest=admitted_digest,
                valid_until=grant.valid_until,
            )
            await source.verify_reservation(reservation)
            values["intake"] = reservation.admission_ref
            grant = grant.model_copy(
                update={
                    "valid_until": min(grant.valid_until, reservation.session_binding.authorization_until)
                }
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
                    if self.native_dispatch is not None:
                        if reservation is None:
                            raise IntakeError("dependency_unavailable")
                        admitted_digest = hashlib.sha256(
                            canonicalize(
                                {
                                    "schema": "human-auth-admission.v1",
                                    "request": strict_loads(raw),
                                    "guide_identity_ref": grant.guide_identity_ref,
                                }
                            )
                        ).hexdigest()
                        await self.native_dispatch.stage_intake(
                            connection,
                            principal=grant.principal,
                            intake_ref=values["intake"],
                            command_id=request.command_id,
                            admitted_digest=admitted_digest,
                            valid_until=grant.valid_until,
                            reservation=reservation,
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

    async def admitted_submission(self, principal_ref: str, command_id: str) -> AuthIntakeSubmission:
        """The committed submission itself, decrypted under the SAME binding `admit` sealed it with.

        The dispatcher (`runtime/intake_dispatch`) starts a process from an admission committed
        minutes earlier by a browser that is long gone; `build_start_command`
        (`gateway/human/auth_projection.py:102`) re-derives the admitted digest from the ORIGINAL
        submission, so the submission has to be readable again. `read` above deliberately returns
        only the `IntakeReceipt`, and until now nothing could recover the request.

        The unsealer belongs with the sealer: the AAD (`admit`, this file, `:141`) is the row's own
        identifying columns, and reconstructing it in a second module would let the two drift into
        a decryption that no longer proves which row the plaintext came from. Every column of that
        AAD is read back from the row, so a request moved to another tenant, principal, command,
        intake or guide does not decrypt at all — AES-GCM fails closed, and the `request_digest`
        re-check below is a second, independent fence over the same bytes.
        """
        if not principal_ref or not command_id:
            raise IntakeError("invalid_request")
        try:
            async with transaction(self.engine, self.seconds) as connection:
                row = (
                    (
                        await connection.execute(
                            text(
                                "SELECT * FROM portal_intake.intake WHERE tenant=:tenant "
                                "AND principal_ref=:principal AND command_id=:command"
                            ),
                            {"tenant": self.tenant, "principal": principal_ref, "command": command_id},
                        )
                    )
                    .mappings()
                    .one()
                )
            if row["key_id"] != self.key_id:
                # A row sealed under a different key is not "unreadable for now": this store
                # cannot prove anything about it. Key rotation is an owner procedure, never an
                # inference here.
                raise IntakeError("dependency_unavailable")
            aad = canonicalize(
                {
                    "tenant": self.tenant,
                    "principal": row["principal_ref"],
                    "command": row["command_id"],
                    "intake": row["intake_ref"],
                    "guide": row["guide_identity_ref"],
                    "digest": row["request_digest"],
                    "authority_ref": row["authority_receipt_ref"],
                    "authority_digest": row["authority_digest"],
                    "key_id": row["key_id"],
                }
            )
            raw = self._cipher.decrypt(bytes(row["nonce"]), bytes(row["ciphertext"]), aad)
            if hashlib.sha256(raw).hexdigest() != row["request_digest"]:
                raise ExternalCaseError("conflict")
            request = AuthIntakeSubmission.model_validate(strict_loads(raw), strict=True)
            if request_bytes(request) != raw or request.command_id != command_id:
                # Re-serialisation must reproduce the sealed bytes exactly; anything else means
                # the stored plaintext is not the admitted representation the digest attests.
                raise ExternalCaseError("conflict")
            return request
        except IntakeError:
            raise
        except Exception:
            raise IntakeError("dependency_unavailable") from None

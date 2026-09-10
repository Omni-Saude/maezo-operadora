"""PHI-only system content and atomic original provenance, including pre-inbox recovery."""

import hashlib
import os
from datetime import datetime
from typing import Any, NoReturn, SupportsIndex
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from maezo.gateway.communications.content import PhiContentKeys
from maezo.gateway.communications.models import alive
from maezo.gateway.human.read_profile import digest, parse_model, wire
from maezo.gateway.intake.native_store import PostgresAuthDispatchStore
from maezo.portal.engine.profile import canonicalize

from .admission import SystemAdmission, lock_key, one
from .models import (
    BodyFound,
    BodyRefused,
    PreserveBodyBinding,
    RequestBodyCommand,
    RequestBodyReceipt,
    RequestBodyReceiptSelector,
    SystemGrant,
    child_id,
    recipient_digest,
    require,
    selector,
)

AAD = (
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

_COMMITTED = object()


class AuthenticatedBodyReceipt:
    """Local PHI custody result; a DTO is not accepted by the General writer."""

    __slots__ = ("receipt",)
    receipt: RequestBodyReceipt

    def __init__(self, receipt: RequestBodyReceipt, *, _mint: object) -> None:
        require(_mint is _COMMITTED, "unavailable")
        object.__setattr__(self, "receipt", receipt)

    def __setattr__(self, name: str, value: Any) -> NoReturn:
        raise TypeError("immutable PHI receipt")

    def __reduce_ex__(self, protocol: SupportsIndex) -> NoReturn:
        raise TypeError("local PHI receipt")

    def __repr__(self) -> str:
        return "AuthenticatedBodyReceipt()"


class PhiRequestContent:
    def __init__(
        self, admission: SystemAdmission, keys: PhiContentKeys, protected: PostgresAuthDispatchStore
    ) -> None:
        require(
            admission.engine is protected.engine
            and admission.scope == keys.scope
            and protected.tenant == admission.scope.tenant,
            "unavailable",
        )
        self.admission, self.keys, self.protected = admission, keys, protected

    def _ref(self, sender: str, command: str) -> str:
        return digest({"scope": self.admission.scope.model_dump(), "sender": sender, "command": command})

    async def _original(
        self, c: AsyncConnection, sender: str, command: str
    ) -> tuple[RequestBodyCommand, RequestBodyReceipt, SystemGrant] | None:
        p = dict(**self.admission.scope.model_dump(), sender=sender, command=command)
        row = await one(
            c,
            "SELECT c.*,j.key_id AS provenance_key,j.nonce AS provenance_nonce,"
            "j.ciphertext AS provenance_ciphertext,j.payload_digest AS provenance_digest "
            "FROM portal_communication.content c JOIN portal_document_request.producer_journal j "
            "ON j.tenant=c.tenant AND j.environment=c.environment AND "
            "j.sender_identity_digest=c.sender_identity_digest "
            "AND j.command_id=c.command_id AND j.kind='phi-body' WHERE c.tenant=:tenant AND "
            "c.environment=:environment "
            "AND c.sender_identity_digest=:sender AND c.command_id=:command",
            p,
        )
        if row is None:
            return None
        payload = self.protected.unseal(
            "document-request-phi-body",
            self._ref(sender, command),
            row["provenance_key"],
            bytes(row["provenance_nonce"]),
            bytes(row["provenance_ciphertext"]),
        )
        require(set(payload) == {"body", "grant", "receipt"} and digest(payload) == row["provenance_digest"])
        body = parse_model(RequestBodyCommand, payload["body"])
        grant = parse_model(SystemGrant, payload["grant"])
        receipt = parse_model(RequestBodyReceipt, payload["receipt"])
        # Original provenance is checked for equality, never treated as a current read grant.
        self._bind_preserve(grant, body)
        require(receipt == self._receipt(body, grant, row["body_ref"]))
        require(
            row["request_digest"] == digest(body)
            and row["case_ref"] == body.request.case_ref
            and row["authority_receipt_ref"] == receipt.body_authority_receipt_ref
            and row["authority_digest"] == receipt.body_authority_digest
        )
        plaintext = self.keys.cipher(row["key_id"]).decrypt(
            bytes(row["nonce"]), bytes(row["ciphertext"]), canonicalize({k: row[k] for k in AAD})
        )
        require(
            plaintext == body.body.encode() and hashlib.sha256(plaintext).hexdigest() == row["content_digest"]
        )
        return body, receipt, grant

    @staticmethod
    def _bind_preserve(grant: SystemGrant, body: RequestBodyCommand) -> None:
        a = grant.access
        binding = PreserveBodyBinding(
            recipient_identity_digest=body.recipient_identity_digest,
            outer_command_id=body.outer_command_id,
            body_command_id=body.command_id,
            message_command_id=body.message_command_id,
            body_ref=None,
            body_request_digest=digest(body),
        )
        require(
            a.operation == "preserve_request_body"
            and a.command_id == body.command_id
            and a.request_digest == digest(body)
            and a.request == body.request
            and a.request_revision == body.request_revision
            and a.assessment_version_digest == body.assessment_version_digest
            and a.producer.identity_digest == body.sender_identity_digest
            and grant.template == body.template
            and grant.body_bindings == (binding,)
            and recipient_digest(grant.recipients) == body.recipient_set_digest
        )

    @staticmethod
    def _receipt(body: RequestBodyCommand, grant: SystemGrant, body_ref: str) -> RequestBodyReceipt:
        return RequestBodyReceipt(
            schema="auth-document-request-body-receipt.v1",
            scope=body.request.communication_scope(),
            case_ref=body.request.case_ref,
            request_identity_digest=digest(body.request),
            body_command_id=body.command_id,
            body_ref=body_ref,
            body_request_digest=digest(body),
            body_authority_receipt_ref=grant.authority_receipt_ref,
            body_authority_digest=grant.authority_digest,
            **{
                k: getattr(body, k)
                for k in (
                    "sender_identity_digest",
                    "outer_command_id",
                    "message_command_id",
                    "request_revision",
                    "assessment_version_digest",
                    "recipient_set_digest",
                    "recipient_identity_digest",
                    "template",
                )
            },
        )

    async def preserve(
        self, grant: SystemGrant, body: RequestBodyCommand, deadline: datetime
    ) -> RequestBodyReceipt:
        self._bind_preserve(grant, body)
        ceiling = alive(deadline, grant.ceiling(), self.keys.valid_until)
        sender, command = body.sender_identity_digest, body.command_id
        async with self.admission.acquire(grant, ceiling) as c:
            await lock_key(c, {"scope": grant.access.scope.model_dump(), "sender": sender, "body": command})
            original = await self._original(c, sender, command)
            if original is not None:
                require(original[0] == body, "conflict")
                receipt = original[1]
            else:
                p = dict(
                    **grant.access.scope.model_dump(),
                    sender_identity_digest=sender,
                    command_id=command,
                    case_ref=body.request.case_ref,
                    body_ref=uuid4().hex,
                    request_digest=digest(body),
                    content_digest=hashlib.sha256(body.body.encode()).hexdigest(),
                    key_id=self.keys.active_key_id,
                    authority_receipt_ref=grant.authority_receipt_ref,
                    authority_digest=grant.authority_digest,
                )
                # Missing provenance does not permit adoption/replacement of existing content.
                existing = await one(
                    c,
                    "SELECT body_ref FROM portal_communication.content WHERE tenant=:tenant "
                    "AND environment=:environment AND sender_identity_digest=:sender_identity_digest "
                    "AND command_id=:command_id",
                    p,
                )
                require(existing is None, "conflict")
                nonce = os.urandom(12)
                encrypted = self.keys.cipher(self.keys.active_key_id).encrypt(
                    nonce, body.body.encode(), canonicalize({k: p[k] for k in AAD})
                )
                await c.execute(
                    text(
                        "INSERT INTO portal_communication.content "
                        "(tenant,environment,case_ref,sender_identity_digest,command_id,"
                        "body_ref,request_digest,"
                        "content_digest,key_id,nonce,ciphertext,authority_receipt_ref,"
                        "authority_digest) VALUES "
                        "(:tenant,:environment,:case_ref,:sender_identity_digest,:command_id,"
                        ":body_ref,:request_digest,"
                        ":content_digest,:key_id,:nonce,:ciphertext,:authority_receipt_ref,:authority_digest)"
                    ),
                    dict(p, nonce=nonce, ciphertext=encrypted),
                )
                receipt = self._receipt(body, grant, p["body_ref"])
                payload = {"body": wire(body), "grant": wire(grant), "receipt": wire(receipt)}
                pn, pc = self.protected.seal("document-request-phi-body", self._ref(sender, command), payload)
                await c.execute(
                    text(
                        "INSERT INTO portal_document_request.producer_journal "
                        "(tenant,environment,sender_identity_digest,command_id,kind,key_id,"
                        "nonce,ciphertext,payload_digest,state) "
                        "VALUES "
                        "(:tenant,:environment,:sender_identity_digest,:command_id,'phi-body',"
                        ":provenance_key,"
                        ":provenance_nonce,:provenance_ciphertext,:provenance_digest,'committed')"
                    ),
                    dict(
                        p,
                        provenance_key=self.protected.key_id,
                        provenance_nonce=pn,
                        provenance_ciphertext=pc,
                        provenance_digest=digest(payload),
                    ),
                )
            alive(ceiling)
        alive(ceiling)
        return receipt

    async def read_receipt(
        self, grant: SystemGrant, selected: RequestBodyReceiptSelector, deadline: datetime
    ) -> BodyFound | BodyRefused:
        a = grant.access
        ceiling = alive(deadline, grant.ceiling(), self.keys.valid_until)
        require(
            a.operation == "read_request_body_receipt"
            and grant.body_bindings == (selected,)
            and a.command_id == selected.body_command_id
            and a.request_digest == selected.body_request_digest
            and digest(a.request) == selected.request_identity_digest
            and a.request_revision == selected.request_revision
            and a.assessment_version_digest == selected.assessment_version_digest
            and grant.template == selected.template
        )
        for kind, actual in (("body", selected.body_command_id), ("message", selected.message_command_id)):
            require(
                child_id(
                    kind,
                    a.request,
                    selected.sender_identity_digest,
                    selected.outer_command_id,
                    selected.recipient_identity_digest,
                )
                == actual
            )
        result: BodyFound | BodyRefused
        async with self.admission.acquire(grant, ceiling) as c:
            original = await self._original(c, selected.sender_identity_digest, selected.body_command_id)
            if original is None:
                result = BodyRefused(state="refused")
            else:
                body, receipt, origin = original
                require(
                    selector(body, selected.body_ref) == selected
                    and (selected.body_ref is None or selected.body_ref == receipt.body_ref)
                )
                for key in (
                    "request",
                    "request_revision",
                    "policy_ref",
                    "policy_digest",
                    "policy_publication_ref",
                    "policy_publication_digest",
                    "assessment_version_digest",
                    "context_query_digest",
                ):
                    require(getattr(a, key) == getattr(origin.access, key))
                result = BodyFound(state="found", selector_digest=digest(selected), receipt=receipt)
            alive(ceiling)
        alive(ceiling)
        return result

    async def preserve_proven(
        self, grant: SystemGrant, body: RequestBodyCommand, deadline: datetime
    ) -> AuthenticatedBodyReceipt:
        receipt = await self.preserve(grant, body, deadline)
        alive(deadline, grant.ceiling(), self.keys.valid_until)
        return AuthenticatedBodyReceipt(receipt, _mint=_COMMITTED)

    async def recover_proven(
        self, grant: SystemGrant, selected: RequestBodyReceiptSelector, deadline: datetime
    ) -> AuthenticatedBodyReceipt:
        result = await self.read_receipt(grant, selected, deadline)
        alive(deadline, grant.ceiling(), self.keys.valid_until)
        require(isinstance(result, BodyFound), "uncertain")
        assert isinstance(result, BodyFound)
        return AuthenticatedBodyReceipt(result.receipt, _mint=_COMMITTED)

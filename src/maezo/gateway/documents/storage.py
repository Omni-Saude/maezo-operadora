"""PHI-only document byte custody: AES-GCM with bound associated data.

Same scheme as `gateway/communications/content.py` — key id + 12-byte nonce + AES-GCM
whose associated data binds every non-secret fact of the admission — because two PHI
planes with two constructions is a second scheme to qualify, not extra safety. Raw
bytes never leave this module and the PHI application that serves them; they never
enter the General-zone document service (`gateway/documents/service.py`).

Key residency, roles and retention are independently qualified deployment inputs; this
code does not invent them and holds no key material of its own.
"""

import hashlib
import os
from datetime import UTC, datetime
from typing import Any

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from maezo.gateway.external_cases.models import ExternalCaseError
from maezo.portal.contracts.intake import Closed, ResourceRef
from maezo.portal.contracts.models import HumanPrincipal
from maezo.portal.engine.profile import canonicalize

#: Associated data of a stored document. Every field here is re-derived from the durable
#: row at read time, so a ciphertext moved between uploads, resources or keys fails to open.
DOCUMENT_AAD = (
    "tenant",
    "environment",
    "resource_kind",
    "resource_ref",
    "upload_ref",
    "document_type_ref",
    "command_id",
    "content_sha256",
    "key_id",
)

#: Associated data of a sealed response admission (exact-request, not a broad message).
RESPONSE_AAD = (
    "tenant",
    "environment",
    "case_ref",
    "request_ref",
    "response_ref",
    "command_id",
    "admitted_digest",
    "key_id",
)

MAX_DOCUMENT_BYTES = 262_144


def now() -> datetime:
    return datetime.now(UTC)


def fingerprint(value: object) -> str:
    return hashlib.sha256(canonicalize(value)).hexdigest()


def principal_digest(principal: HumanPrincipal) -> str:
    """The same server-side identity fingerprint the communication plane publishes.

    Deliberately the canonical shape of `communications.models.identity`, so a creator
    recorded here and a recipient recorded there are comparable without a second
    identity construction.
    """
    return fingerprint(
        {
            "tenant": principal.tenant,
            "issuer": principal.issuer,
            "subject": principal.subject,
            "principal_ref": principal.principal_ref,
        }
    )


def content_digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


class DocumentScope(Closed):
    tenant: ResourceRef
    environment: ResourceRef


class PhiDocumentKeys:
    """Deployment-supplied PHI key set; identical discipline to `PhiContentKeys`."""

    def __init__(
        self,
        *,
        scope: DocumentScope,
        active_key_id: str,
        keys: dict[str, bytes],
        valid_until: datetime,
    ) -> None:
        if active_key_id not in keys or any(
            not key_id or type(key) is not bytes or len(key) != 32 for key_id, key in keys.items()
        ):
            raise ExternalCaseError("unavailable")
        self.scope, self.active_key_id, self.valid_until = scope, active_key_id, valid_until
        self._keys = {name: AESGCM(key) for name, key in keys.items()}

    def cipher(self, key_id: str) -> AESGCM:
        if datetime.now(UTC) >= self.valid_until:
            raise ExternalCaseError("denied")
        cipher = self._keys.get(key_id)
        if cipher is None:
            raise ExternalCaseError("unavailable")
        return cipher

    def seal(self, key_id: str, raw: bytes, aad: dict[str, Any]) -> tuple[bytes, bytes]:
        nonce = os.urandom(12)
        return nonce, self.cipher(key_id).encrypt(nonce, raw, canonicalize(aad))

    def unseal(self, key_id: str, nonce: bytes, ciphertext: bytes, aad: dict[str, Any]) -> bytes:
        try:
            return self.cipher(key_id).decrypt(bytes(nonce), bytes(ciphertext), canonicalize(aad))
        except Exception:
            # A wrong key, a moved row or a tampered byte is a dependency fault, never a
            # partial disclosure: refuse without distinguishing the causes.
            raise ExternalCaseError("unavailable") from None


def bounded_document(raw: bytes) -> bytes:
    if not 1 <= len(raw) <= MAX_DOCUMENT_BYTES:
        raise ExternalCaseError("unavailable")
    return raw


__all__ = [
    "DOCUMENT_AAD",
    "MAX_DOCUMENT_BYTES",
    "RESPONSE_AAD",
    "DocumentScope",
    "PhiDocumentKeys",
    "bounded_document",
    "content_digest",
    "fingerprint",
    "now",
    "principal_digest",
]

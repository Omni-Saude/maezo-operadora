"""Dedicated tenant/environment/workload human-command credential references (D4/D5).

References only: key material must remain in the gateway's future KMS/cofre adapter.
There is deliberately no agent view, generic secret accessor, admin/OIDC/A2A partition,
credential loading from environment, or signing fallback here. Reference registration
is not attestation that a real key has exclusive use; production activation requires a
verified provider adapter and separately reviewed key provisioning.
"""

import hashlib
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Literal, Never

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from maezo.portal.contracts.models import OpaqueRef

from .errors import GatewayRefusalError
from .models import Closed, Scope


class DedicatedHumanCredential(Closed):
    scope: Scope
    key_id: OpaqueRef
    purpose: Literal["human-command"]

    @property
    def key_ref(self) -> str:
        # Dedicated namespace; never accept arbitrary OIDC/admin/A2A/PHI secret locators.
        return (
            f"human-command/{self.scope.environment}/{self.scope.tenant}/"
            f"{self.scope.workload_ref}/{self.key_id}"
        )


class HumanCommandCredentialPartition:
    """Non-secret reference registry isolated from CredentialVault/AgentCredentialView."""

    def __init__(self, scope: Scope) -> None:
        self._scope = Scope.model_validate(scope)
        self._credential: DedicatedHumanCredential | None = None

    def install(self, credential: DedicatedHumanCredential) -> None:
        credential = DedicatedHumanCredential.model_validate(credential)
        if credential.scope != self._scope:
            raise GatewayRefusalError("credential_scope_mismatch")
        self._credential = credential

    def for_workload(self, scope: Scope) -> DedicatedHumanCredential:
        if scope != self._scope or self._credential is None:
            raise GatewayRefusalError("credential_scope_mismatch")
        return self._credential


# E03 distinct managed-key handles. These are supplied by qualified composition;
# no environment/private-byte fallback or cross-purpose key conversion exists.


@dataclass(frozen=True, slots=True, repr=False)
class AssignmentSigningLease:
    scope: Scope
    key_id: str
    purpose: Literal["human-assignment-read", "human-authority", "human-staff-assignment-source"]
    audience: str
    fingerprint: str
    not_before: datetime
    not_after: datetime
    max_envelope_seconds: int
    _key: Ed25519PrivateKey = field(repr=False)
    _live: Callable[[], None] = field(repr=False)

    def guard(self) -> datetime:
        self._live()
        now = datetime.now(UTC)
        actual = hashlib.sha256(
            self._key.public_key().public_bytes(Encoding.DER, PublicFormat.SubjectPublicKeyInfo)
        ).hexdigest()
        if (
            actual != self.fingerprint
            or not self.not_before <= now < self.not_after
            or self.max_envelope_seconds <= 0
        ):
            raise GatewayRefusalError("production_capabilities_unavailable")
        return now

    def sign(self, value: bytes) -> bytes:
        self.guard()
        signature = self._key.sign(value)
        self.guard()
        return signature

    def __reduce__(self) -> Never:
        raise TypeError("assignment signing lease cannot be serialized")

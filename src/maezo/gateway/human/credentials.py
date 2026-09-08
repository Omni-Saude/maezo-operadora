"""Dedicated tenant/environment/workload human-command credential references (D4/D5).

References only: key material must remain in the gateway's future KMS/cofre adapter.
There is deliberately no agent view, generic secret accessor, admin/OIDC/A2A partition,
credential loading from environment, or signing fallback here. Reference registration
is not attestation that a real key has exclusive use; production activation requires a
verified provider adapter and separately reviewed key provisioning.
"""

from typing import Literal

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

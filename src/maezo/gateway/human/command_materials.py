"""Concrete command-plane key provisioning (inventory #15, #16) from deployment materials.

`credentials.py` deliberately ships references only, and states that production
activation requires "a verified provider adapter and separately reviewed key
provisioning". This module is that adapter: it installs the dedicated
`human-command` credential into its partition and mints the purpose-separated
`AssignmentSigningLease`, both from the attested material bundle.

ADR-0049 D5: keys are isolated per tenant/environment in the vault; the OIDC secret,
`PHI_HMAC_KEY` and the A2A key are not reachable from here — this module imports no
credential vault, no settings secret and no environment reader.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from maezo.portal.engine.profile import SigningContext

from .credentials import AssignmentSigningLease, DedicatedHumanCredential, HumanCommandCredentialPartition
from .errors import GatewayRefusalError
from .models import Scope
from .production_materials import HumanMaterials, fingerprint
from .read_materials import MaterialLifetime
from .transport import PartitionedEd25519Signer


def unavailable() -> GatewayRefusalError:
    return GatewayRefusalError("production_capabilities_unavailable")


@dataclass(frozen=True, repr=False)
class CommandCredentials:
    """The installed command partition and the signer bound to it."""

    partition: HumanCommandCredentialPartition
    credential: DedicatedHumanCredential
    signer: PartitionedEd25519Signer
    endpoint: str
    timeout_seconds: int


def install_command_credential(materials: HumanMaterials, lifetime: MaterialLifetime) -> CommandCredentials:
    """#15 — install the dedicated `human-command` credential and its Ed25519 signer.

    The partition refuses a credential from another scope by construction; installing
    is therefore the only way a signer can ever be produced for this workload.
    """
    lifetime.check()
    manifest = materials.manifest
    scope = manifest.scope
    designation = manifest.key("human-command")
    key = materials.private_key("human-command")
    if not isinstance(key, Ed25519PrivateKey) or fingerprint(key.public_key()) != designation.fingerprint:
        raise unavailable()
    if designation.workload_ref != scope.workload_ref:
        raise unavailable()
    credential = DedicatedHumanCredential(scope=scope, key_id=designation.key_id, purpose="human-command")
    partition = HumanCommandCredentialPartition(scope)
    partition.install(credential)
    context = SigningContext(
        tenant=scope.tenant,
        audience=designation.audience,
        workload_ref=scope.workload_ref,
        key_id=designation.key_id,
        max_lifetime_seconds=designation.max_envelope_seconds,
    )
    signer = PartitionedEd25519Signer(
        partition=partition,
        credential=credential,
        context=context,
        private_key=key,
        valid_from=manifest.issued_at,
        valid_until=materials.not_after,
        envelope_seconds=designation.max_envelope_seconds,
    )
    return CommandCredentials(
        partition=partition,
        credential=credential,
        signer=signer,
        endpoint=manifest.command_endpoint,
        timeout_seconds=manifest.command_surface.timeout_seconds,
    )


def assignment_scope(materials: HumanMaterials) -> Scope:
    """The assignment read plane runs as its own workload, never as the command one."""
    manifest = materials.manifest
    scope = Scope(
        tenant=manifest.scope.tenant,
        environment=manifest.scope.environment,
        workload_ref=manifest.assignment_workload_ref,
    )
    if scope.workload_ref == manifest.scope.workload_ref:
        raise unavailable()
    return scope


def assignment_signing_lease(materials: HumanMaterials, lifetime: MaterialLifetime) -> AssignmentSigningLease:
    """#16 — the `human-assignment-read` lease, guarded on every signature."""
    lifetime.check()
    manifest = materials.manifest
    designation = manifest.key("human-assignment-read")
    key = materials.private_key("human-assignment-read")
    if not isinstance(key, Ed25519PrivateKey) or fingerprint(key.public_key()) != designation.fingerprint:
        raise unavailable()
    scope = assignment_scope(materials)
    if designation.workload_ref != scope.workload_ref:
        raise unavailable()
    not_after: datetime = materials.not_after
    return AssignmentSigningLease(
        scope=scope,
        key_id=designation.key_id,
        purpose="human-assignment-read",
        audience=designation.audience,
        fingerprint=designation.fingerprint,
        not_before=manifest.issued_at,
        not_after=not_after,
        max_envelope_seconds=designation.max_envelope_seconds,
        _key=key,
        _live=_guarded(lifetime, not_after),
    )


def _guarded(lifetime: MaterialLifetime, not_after: datetime):  # type: ignore[no-untyped-def]
    live = lifetime.bounded(not_after)

    def check() -> None:
        try:
            live()
        except Exception:
            raise unavailable() from None

    return check

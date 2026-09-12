"""Concrete Q2 read-plane providers (inventory #1, #2, #3) over deployment materials.

These are the production implementations of `ReadDeploymentAdmission`,
`ReadCredentialProvider` and `QueueCursorKeyProvider`. Every lease is minted from the
attested material bundle, is bound to one scope/purpose/key identity, and carries a
live check that fails closed once the composition is closed or the attested window
has passed. No key is derived from a constant and no lease outlives its material.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Literal

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from .models import Scope
from .production_materials import HumanMaterials, Purpose, fingerprint
from .read_credentials import (
    CursorKeyLease,
    CursorKeySet,
    QueueCursorKeyProvider,
    ReadAdmissionLease,
    ReadCredentialProvider,
    ReadDeploymentAdmission,
    ReadSigningLease,
    unavailable,
)
from .read_profile import Requester

#: The Q2 read plane signs with exactly one key for both of its purposes; the
#: purposes remain distinct on the wire and are checked per acquisition.
READ_PURPOSES = ("portal-task-read", "portal-read-publication")


class MaterialLifetime:
    """One composition's revocable lifetime. Closing it invalidates every issued lease.

    This is the live check the read ports call on every use: a lease is not a decided
    fact, it is a claim that must still hold when it is exercised.
    """

    def __init__(self, *, clock: Callable[[], datetime] = lambda: datetime.now(UTC)) -> None:
        self._open = True
        self._clock = clock

    def close(self) -> None:
        self._open = False

    def check(self) -> None:
        if not self._open:
            raise unavailable()

    def bounded(self, not_after: datetime) -> Callable[[], None]:
        def live() -> None:
            self.check()
            if self._clock() >= not_after:
                raise unavailable()

        return live


class MaterialReadAdmission(ReadDeploymentAdmission):
    """#1 — the deployed read installation's attested capability, re-verified per call."""

    def __init__(self, materials: HumanMaterials, lifetime: MaterialLifetime) -> None:
        self._materials = materials
        self._lifetime = lifetime
        self.scope = materials.manifest.scope

    async def current(self, scope: Scope, engine_name: str) -> ReadAdmissionLease:
        record = self._materials.admission
        self._lifetime.check()
        if (
            Scope.model_validate(scope) != self.scope
            or record.scope != self.scope
            or engine_name != self._materials.manifest.engine_name
            or record.engine_name != engine_name
        ):
            raise unavailable()
        return ReadAdmissionLease(record=record, live=self._lifetime.bounded(record.valid_until))


class MaterialReadCredentials(ReadCredentialProvider):
    """#2 — the Q2 signing lease; never the command or assignment key."""

    def __init__(self, materials: HumanMaterials, lifetime: MaterialLifetime) -> None:
        manifest = materials.manifest
        designation = manifest.key("portal-task-read")
        key = materials.private_key("portal-task-read")
        if not isinstance(key, Ed25519PrivateKey) or fingerprint(key.public_key()) != (
            designation.fingerprint
        ):
            raise unavailable()
        self.scope = manifest.scope
        self._designation = designation
        self._key = key
        self._lifetime = lifetime
        self._requester = Requester(
            issuer=manifest.scope.workload_ref,
            key_id=designation.key_id,
            public_key_sha256=designation.fingerprint,
            peer_spki_sha256=manifest.read_surface.server_spki_sha256,
        )
        self._not_before = manifest.issued_at
        self._not_after = manifest.valid_until

    async def acquire(self, scope: Scope, purpose: str, key_id: str) -> ReadSigningLease:
        self._lifetime.check()
        if (
            Scope.model_validate(scope) != self.scope
            or key_id != self._designation.key_id
            or purpose not in READ_PURPOSES
        ):
            raise unavailable()
        # Narrowed by the membership test above; the wire purpose is never free text.
        wire_purpose: Literal["portal-task-read", "portal-read-publication"] = (
            "portal-task-read" if purpose == "portal-task-read" else "portal-read-publication"
        )
        return ReadSigningLease(
            scope=self.scope,
            purpose=wire_purpose,
            requester=self._requester,
            audience=self._designation.audience,
            not_before=self._not_before,
            not_after=self._not_after,
            max_envelope_seconds=self._designation.max_envelope_seconds,
            generation=1,
            _key=self._key,
            _live=self._lifetime.bounded(self._not_after),
        )


class MaterialCursorKeys(QueueCursorKeyProvider):
    """#3 — the AEAD queue-cursor key set, with its rotation cohort."""

    def __init__(self, materials: HumanMaterials, lifetime: MaterialLifetime) -> None:
        manifest = materials.manifest
        bundle = materials.cursor_keys
        self.scope = manifest.scope
        self._lifetime = lifetime
        # The custody asks for the CURRENT cursor key by its own id and refuses a
        # set whose current lease does not carry it.
        self._key_id = manifest.current_cursor().key_id
        live = lifetime.bounded(bundle.not_after)
        leases = tuple(
            CursorKeyLease(
                scope=manifest.scope,
                key_id=designation.key_id,
                key_tag=bytes.fromhex(designation.key_tag),
                not_before=bundle.not_before,
                not_after=bundle.not_after,
                generation=designation.generation,
                _key_material=materials.cursor_material[designation.key_id],
                _live=live,
            )
            for designation in manifest.cursor_keys
        )
        current = [
            lease
            for lease, designation in zip(leases, manifest.cursor_keys, strict=True)
            if designation.current
        ]
        if len(current) != 1:
            raise unavailable()
        self._set = CursorKeySet(current=current[0], accepted=leases, live=live)

    async def acquire(self, scope: Scope, key_id: str) -> CursorKeySet:
        self._lifetime.check()
        if Scope.model_validate(scope) != self.scope or key_id != self._key_id:
            raise unavailable()
        self._set.require_current(datetime.now(UTC))
        return self._set


def read_providers(
    materials: HumanMaterials, lifetime: MaterialLifetime
) -> tuple[MaterialReadAdmission, MaterialReadCredentials, MaterialCursorKeys]:
    return (
        MaterialReadAdmission(materials, lifetime),
        MaterialReadCredentials(materials, lifetime),
        MaterialCursorKeys(materials, lifetime),
    )


def purpose_key_ids(materials: HumanMaterials) -> dict[Purpose, str]:
    return {key.purpose: key.key_id for key in materials.manifest.keys}

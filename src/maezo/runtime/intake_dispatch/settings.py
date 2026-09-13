"""Fail-closed configuration for `python -m maezo.runtime.intake_dispatch`.

Every field that could enable an effect defaults to `None` and is REFUSED by `materials()` with a
named error. There is deliberately no inference from deployment mode, hostname or database name,
and no default that would let a misconfigured daemon start and report healthy while dispatching
nothing (or, worse, dispatching against the wrong installation).

Two protected materials are required, both owner-installed regular files read through
`protected_bytes` (`gateway/intake/native_authority.py:670`: absolute path, `O_NOFOLLOW`, regular
file, owner-only mode, size bound, optional digest):

* the AUTH lifecycle installation (`AuthLifecycleConfiguration`, `native_authority.py:793`) —
  the same file the portal's own composition loads, so the daemon can never talk to a different
  identity source, native database or protected store than the admission it is draining;
* the **definition pin** (`Definition`, `gateway/human/auth_profile.py:84`) — which deployed
  `SP-OP-AUTH-001` this installation starts. It is a DEPLOYMENT fact (definition id, deployment
  id, artefact digests), not a business rule, and it has no other source in the repository: it is
  published as no head kind (`InputKind`, `auth_profile.py:22`) and carried by no other config.
  Pinning it here is what makes a wrong-deployment start impossible rather than merely unlikely:
  `bind_receipt` refuses any receipt whose `definition` differs from the command's
  (`gateway/human/auth_transport.py:208`), so a stale pin fails closed at the receipt, never
  silently starts the wrong process. Its sha256 is REQUIRED — an unpinned pin is not a pin.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Final

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from maezo.gateway.human.auth_profile import Definition
from maezo.gateway.intake.native_authority import protected_bytes

#: Idle sweep interval. The drain window is NOT open-ended: an admitted row may only be dispatched
#: while its `authorization_until` still stands (`native_store.py:461`), which is the admitting
#: session's own ceiling. A slow poll therefore does not merely add latency, it expires work.
DEFAULT_POLL_INTERVAL_S: Final[float] = 2.0

#: Rows read per sweep. The sweep is sequential and dispatches at most one row per business key,
#: so this bounds the read, not the concurrency.
DEFAULT_BATCH_SIZE: Final[int] = 16

#: Heartbeat file the liveness probe stats (same mechanism, and same reason, as
#: `a2a/outbox_relay.py:105`: the runtime image has no `procps`, so `pgrep` restart-loops the pod).
DEFAULT_HEARTBEAT_PATH: Final[str] = "/tmp/maezo-intake-dispatch.heartbeat"


class IntakeDispatchRefusalError(RuntimeError):
    """The daemon refused to compose. Nothing was read, claimed or dispatched."""


@dataclass(frozen=True, slots=True, repr=False)
class IntakeDispatchMaterials:
    """The validated, owner-installed inputs the composition root needs. No secrets in `repr`."""

    tenant: str
    lifecycle_path: Path
    identity_writer_url: SecretStr
    definition: Definition

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"IntakeDispatchMaterials(tenant={self.tenant!r}, lifecycle_path={self.lifecycle_path!r})"


class IntakeDispatchSettings(BaseSettings):
    """Env-driven config. Mirrors `WorkerRuntimeSettings`' Field/alias/BaseSettings shape."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore", populate_by_name=True)

    tenant: str | None = Field(default=None, alias="MAEZO_INTAKE_DISPATCH_TENANT")
    lifecycle_path: str | None = Field(default=None, alias="MAEZO_INTAKE_DISPATCH_LIFECYCLE_PATH")
    identity_writer_url: SecretStr | None = Field(
        default=None, alias="MAEZO_INTAKE_DISPATCH_IDENTITY_WRITER_URL", repr=False
    )
    definition_path: str | None = Field(default=None, alias="MAEZO_INTAKE_DISPATCH_DEFINITION_PATH")
    definition_digest: str | None = Field(default=None, alias="MAEZO_INTAKE_DISPATCH_DEFINITION_DIGEST")

    poll_interval_s: float = Field(
        default=DEFAULT_POLL_INTERVAL_S, alias="MAEZO_INTAKE_DISPATCH_POLL_INTERVAL_S"
    )
    batch_size: int = Field(default=DEFAULT_BATCH_SIZE, alias="MAEZO_INTAKE_DISPATCH_BATCH_SIZE")
    heartbeat_path: str = Field(default=DEFAULT_HEARTBEAT_PATH, alias="MAEZO_INTAKE_DISPATCH_HEARTBEAT_PATH")

    def materials(self) -> IntakeDispatchMaterials:
        """Validate everything an effect depends on, or refuse by name. Never partially."""
        if not self.tenant:
            raise IntakeDispatchRefusalError(
                "intake dispatch: MAEZO_INTAKE_DISPATCH_TENANT is required — the outbox, the "
                "protected store and the native installation are all tenant-scoped and none of "
                "them may be inferred."
            )
        if not self.lifecycle_path:
            raise IntakeDispatchRefusalError(
                "intake dispatch: MAEZO_INTAKE_DISPATCH_LIFECYCLE_PATH is required — without the "
                "AUTH lifecycle installation there is no protected store to drain and no native "
                "installation to dispatch to."
            )
        # `len()` on a `SecretStr` never extracts the secret — the effect-chokepoint fence
        # (§8.3) reserves `get_secret_value()` for the gateway identity composition seam, and an
        # emptiness check has no business reading a DSN anyway. The DSN's SHAPE is validated where
        # the engine is built (`native_authority._engine`), which refuses any non-asyncpg or
        # query-bearing URL.
        if self.identity_writer_url is None or len(self.identity_writer_url) == 0:
            raise IntakeDispatchRefusalError(
                "intake dispatch: MAEZO_INTAKE_DISPATCH_IDENTITY_WRITER_URL is required — the "
                "identity source lifecycle is composed with every one of its role-bound engines "
                "or with none."
            )
        if not self.definition_path or not self.definition_digest:
            raise IntakeDispatchRefusalError(
                "intake dispatch: MAEZO_INTAKE_DISPATCH_DEFINITION_PATH and "
                "MAEZO_INTAKE_DISPATCH_DEFINITION_DIGEST are both required — the deployed "
                "SP-OP-AUTH-001 definition is pinned material, and an unpinned pin is not a pin."
            )
        if self.batch_size < 1 or self.poll_interval_s <= 0:
            raise IntakeDispatchRefusalError(
                "intake dispatch: batch size must be >= 1 and the poll interval > 0 — a daemon "
                "that never sweeps lets every admitted row expire unnoticed."
            )
        definition = Definition.model_validate_json(
            protected_bytes(Path(self.definition_path), self.definition_digest), strict=True
        )
        return IntakeDispatchMaterials(
            tenant=self.tenant,
            lifecycle_path=Path(self.lifecycle_path),
            identity_writer_url=self.identity_writer_url,
            definition=definition,
        )

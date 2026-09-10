"""Existing committed admission lineage and cursor-only PostgreSQL recovery.

Trace: E04 recovery v1. LEFT JOIN preserves unqualified candidates so they fail
closed instead of becoming absence. No intake body or native effect is read.
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import text

from maezo.gateway.external_cases.postgres import transaction
from maezo.gateway.human.read_profile import parse_model
from maezo.portal.contracts.intake import Closed
from maezo.portal.contracts.intake_recovery import IntakeRecoveryItem
from maezo.portal.contracts.models import HumanPrincipal, OpaqueRef
from maezo.portal.engine.profile import canonicalize

from .models import IntakeError
from .native_store import PostgresAuthDispatchStore
from .postgres import PostgresIntakeStore

PAGE_SIZE = 50


class RecoveryScope(Closed):
    """Qualified deployment selector, not proof of historical storage scope."""

    tenant: OpaqueRef
    environment: OpaqueRef


def stable_actor(principal: HumanPrincipal) -> tuple[str, str, str, str]:
    return principal.tenant, principal.issuer, principal.subject, principal.principal_ref


def actor_digest(principal: HumanPrincipal) -> str:
    import hashlib

    return hashlib.sha256(canonicalize(list(stable_actor(principal)))).hexdigest()


def instant(value: Any) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise IntakeError()
    return value


@dataclass(frozen=True, slots=True, repr=False)
class RecoveryCandidate:
    item: IntakeRecoveryItem
    created_at: datetime


@dataclass(frozen=True, slots=True, repr=False)
class RecoveryScan:
    candidates: tuple[RecoveryCandidate, ...]
    upper_bound: datetime
    cursor_until: datetime | None = None


# Immutable request/actor columns only. Native admitted_digest intentionally is
# NOT equated with intake.request_digest: they are different approved codecs.
_CANDIDATES = """
SELECT i.tenant,i.principal_ref,i.command_id,i.intake_ref,i.request_digest,i.created_at,
 e.command_id AS event_command,e.principal_ref AS event_principal,e.request_digest AS event_digest,
 o.operation AS native_operation,o.command_id AS native_command,o.principal_ref AS native_principal,
 o.admission_ref AS native_admission,o.resource_ref AS native_resource,
 n.key_id AS identity_key,n.nonce AS identity_nonce,n.ciphertext AS identity_ciphertext
FROM portal_intake.intake i
LEFT JOIN portal_intake.admission_event e ON e.tenant=i.tenant AND e.intake_ref=i.intake_ref
LEFT JOIN portal_intake.native_outbox o ON o.tenant=i.tenant AND o.command_id=i.command_id
LEFT JOIN portal_intake.native_identity n ON n.tenant=i.tenant AND n.command_id=i.command_id
WHERE i.tenant=:tenant AND i.principal_ref=:principal
"""


@dataclass(frozen=True, slots=True, repr=False)
class PostgresIntakeRecoveryStore:
    admission: PostgresIntakeStore = field(repr=False)
    identity_store: PostgresAuthDispatchStore = field(repr=False)
    scope: RecoveryScope
    clock: Callable[[], datetime] = field(default=lambda: datetime.now(UTC), repr=False)

    def __post_init__(self) -> None:
        if (
            type(self.admission) is not PostgresIntakeStore
            or type(self.identity_store) is not PostgresAuthDispatchStore
            or self.admission.native_dispatch is not self.identity_store
            or self.admission.engine is not self.identity_store.engine
            or self.admission.tenant != self.identity_store.tenant
            or self.scope.tenant != self.admission.tenant
            or not self.scope.tenant
        ):
            raise IntakeError()

    def _params(self, principal: HumanPrincipal) -> dict[str, Any]:
        if principal.tenant != self.scope.tenant:
            raise IntakeError("operation_forbidden")
        return dict(
            tenant=self.scope.tenant,
            environment=self.scope.environment,
            principal=principal.principal_ref,
            actor=actor_digest(principal),
            membership=str(principal.membership_revision),
            page_size=PAGE_SIZE,
        )

    def _candidate(self, row: Any, principal: HumanPrincipal) -> RecoveryCandidate:
        try:
            if row["tenant"] != principal.tenant or row["principal_ref"] != principal.principal_ref:
                raise IntakeError("operation_forbidden")
            if (
                row["event_command"] != row["command_id"]
                or row["event_principal"] != row["principal_ref"]
                or row["event_digest"] != row["request_digest"]
                or row["native_operation"] != "auth.start"
                or row["native_command"] != row["command_id"]
                or row["native_principal"] != row["principal_ref"]
                or row["native_admission"] != row["intake_ref"]
                or row["native_resource"] != row["intake_ref"]
                or row["identity_key"] is None
                or row["identity_nonce"] is None
                or row["identity_ciphertext"] is None
            ):
                raise IntakeError()
            original = parse_model(
                HumanPrincipal,
                self.identity_store.unseal(
                    "identity",
                    row["command_id"],
                    row["identity_key"],
                    bytes(row["identity_nonce"]),
                    bytes(row["identity_ciphertext"]),
                ),
            )
            if stable_actor(original) != stable_actor(principal):
                raise IntakeError("operation_forbidden")
            return RecoveryCandidate(
                IntakeRecoveryItem(command_id=row["command_id"], intake_ref=row["intake_ref"]),
                instant(row["created_at"]),
            )
        except IntakeError:
            raise
        except Exception:
            raise IntakeError() from None

    async def command(self, principal: HumanPrincipal, command_id: str) -> RecoveryCandidate | None:
        params = dict(self._params(principal), command=command_id)
        try:
            async with transaction(self.admission.engine, self.admission.seconds) as connection:
                row = (
                    (await connection.execute(text(_CANDIDATES + " AND i.command_id=:command"), params))
                    .mappings()
                    .one_or_none()
                )
                result = None if row is None else self._candidate(row, principal)
            return result
        except IntakeError:
            raise
        except Exception:
            raise IntakeError() from None

    async def scan(self, principal: HumanPrincipal, cursor_ref: str | None) -> RecoveryScan:
        params = self._params(principal)
        try:
            async with transaction(self.admission.engine, self.admission.seconds) as connection:
                boundary = ""
                until = None
                if cursor_ref is not None:
                    row = (
                        (
                            await connection.execute(
                                text(
                                    "SELECT tenant,environment,actor_digest,membership_revision,page_size,"
                                    "upper_bound,after_created_at,after_intake_ref,valid_until "
                                    "FROM portal_intake.recovery_cursor WHERE tenant=:tenant "
                                    "AND environment=:environment AND cursor_ref=:cursor"
                                ),
                                dict(params, cursor=cursor_ref),
                            )
                        )
                        .mappings()
                        .one_or_none()
                    )
                    if row is None or any(
                        (
                            row["tenant"] != params["tenant"],
                            row["environment"] != params["environment"],
                            row["actor_digest"] != params["actor"],
                            row["membership_revision"] != params["membership"],
                            row["page_size"] != PAGE_SIZE,
                        )
                    ):
                        raise IntakeError("operation_forbidden")
                    until = instant(row["valid_until"])
                    if self.clock() >= until:
                        raise IntakeError("operation_forbidden")
                    upper = instant(row["upper_bound"])
                    after_created = instant(row["after_created_at"])
                    if after_created > upper:
                        raise IntakeError()
                    params.update(after_created=after_created, after_intake=row["after_intake_ref"])
                    boundary = (
                        ' AND (i.created_at,i.intake_ref COLLATE "C") '
                        '< (:after_created,CAST(:after_intake AS text) COLLATE "C")'
                    )
                else:
                    upper = instant((await connection.execute(text("SELECT clock_timestamp()"))).scalar_one())
                params["upper"] = upper
                rows = (
                    (
                        await connection.execute(
                            text(
                                _CANDIDATES
                                + " AND i.created_at<=:upper"
                                + boundary
                                + ' ORDER BY i.created_at DESC,i.intake_ref COLLATE "C" DESC LIMIT 51'
                            ),
                            params,
                        )
                    )
                    .mappings()
                    .all()
                )
                candidates = tuple(self._candidate(row, principal) for row in rows)
                if len(candidates) > PAGE_SIZE + 1:
                    raise IntakeError()
                result = RecoveryScan(candidates, upper, until)
            if until is not None and self.clock() >= until:
                raise IntakeError("operation_forbidden")
            return result
        except IntakeError:
            raise
        except Exception:
            raise IntakeError() from None

    async def cursor(self, principal: HumanPrincipal, scan: RecoveryScan, deadline: datetime) -> str:
        if len(scan.candidates) != PAGE_SIZE + 1 or self.clock() >= instant(deadline):
            raise IntakeError("operation_forbidden")
        if scan.cursor_until is not None and deadline > scan.cursor_until:
            raise IntakeError("operation_forbidden")
        last = scan.candidates[PAGE_SIZE - 1]
        reference = uuid4().hex
        params = dict(
            self._params(principal),
            cursor=reference,
            upper=scan.upper_bound,
            after_created=last.created_at,
            after_intake=last.item.intake_ref,
            until=deadline,
        )
        try:
            async with transaction(self.admission.engine, self.admission.seconds) as connection:
                await connection.execute(
                    text(
                        "INSERT INTO portal_intake.recovery_cursor "
                        "(tenant,environment,cursor_ref,actor_digest,membership_revision,page_size,"
                        "upper_bound,after_created_at,after_intake_ref,valid_until) VALUES "
                        "(:tenant,:environment,:cursor,:actor,:membership,:page_size,:upper,"
                        ":after_created,:after_intake,:until)"
                    ),
                    params,
                )
                if self.clock() >= deadline:
                    raise IntakeError("operation_forbidden")
            if self.clock() >= deadline:
                raise IntakeError("operation_forbidden")
            return reference
        except IntakeError:
            raise
        except Exception:
            raise IntakeError() from None

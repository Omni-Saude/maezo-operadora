"""The drain engine: `portal_intake.native_outbox` -> the audited AUTH start/response seam.

=================================================================================================
What this is
=================================================================================================
`portal_intake.native_outbox` (`gateway/intake/native_schema.sql:3`) is written by the admission
transaction (`PostgresAuthDispatchStore.stage_intake`, `gateway/intake/native_store.py:584`) and
was, until this module, drained by NOTHING: `AuthIntakeComponents.dispatch_prepared_start` /
`dispatch_prepared_documents` (`gateway/intake/native_composition.py:50`, `:87`) had no caller
anywhere in `src/`. An admitted intake therefore sat at `state='admitted'` for ever — the browser
got its `IntakeReceipt`, and no process was ever started. This engine is the missing drainer.

=================================================================================================
The receipt IS the outcome. The transport result never is.
=================================================================================================
Every disposition this engine reports is read back from the durable, decrypted, command-bound
receipt (`PostgresAuthDispatchStore.completed`, `native_store.py:833`, which re-runs
`bind_receipt`) — never from the fact that a call returned, and never from an HTTP status. Three
consequences, all deliberate:

* A dispatch that raises AFTER the native commit is still reported `started`, because the receipt
  is there. The daemon dispatches head-less (no browser session, hence `caller=None`), and the
  sanctioned seam's final *disclosure* read demands a live caller
  (`PostgresAuthEffectAuthorizationSource.current`, `gateway/intake/native_authority.py:546`:
  `if read and caller is None: raise`). So on the happy path `dispatch_prepared_start` raises
  `AuthUnavailableError` after `store.reconcile` has already committed the receipt. Reporting that
  as a failure would be a lie about a process that IS running — and a retry loop over an effect
  that already happened. See `DISCLOSURE_FINDING` below.
* A dispatch that returns without a durable receipt is reported `unavailable`, never `started`.
* Nothing is ever inferred from `ProcessInstance` alone: it carries `state="UNKNOWN"`
  (`gateway/intake/native_dispatch.py:280`) precisely because the engine's answer to "did it
  start" is the receipt.

=================================================================================================
Never twice, and never two for one business key
=================================================================================================
Two independent fences, both BEFORE any send:

1. per command: `completed(command)` is consulted first; a command that already carries a receipt
   is reported `already_executed` and no send is attempted. The store's own state machine
   (`claim`, `native_store.py:385`, refusing `state in ('executed','rejected')` and live leases)
   is the durable half of the same guarantee.
2. per business key: a sweep dispatches AT MOST ONE item per business-key domain — the guide
   identity for `auth.start` (owner decision #16(a): the single business key is
   `AUTH-{tenant}-{numero_guia_tiss}`, one instance per guia), the case for
   `auth.documents.respond`. An `auth.start` row whose guide identity cannot be established is
   NOT dispatched (`unavailable`), because an item outside every key domain cannot be deduplicated
   against its siblings.

This engine derives no business-key TEXT. The one composer is `auth_business_key`
(`tools/process_business_keys.py:92`), called inside the sanctioned start path
(`native_composition.py:108`); re-deriving the key here would duplicate a business rule in two
places and let them disagree. The engine serialises on the key's sole variable — the guide
identity — and records, then sanity-checks, the key the engine actually reported.

=================================================================================================
Two upstream fences this daemon runs INTO, deliberately, rather than around
=================================================================================================
* **The portal start is inert until the facts source publishes `numero_guia_tiss`.**
  `dispatch_prepared_start` reads it off the published start facts and refuses with
  `AuthIntakeGuideNumberUnavailableError` when it is absent (`native_composition.py:43-61`), which
  is every case today: `StartFacts` is a closed model that does not declare the field. So this
  daemon's start path fails closed BEFORE any engine effect, and reports the refusal under its own
  name (`guide_number_unpublished`) so "nothing is draining" can never be a mystery. Publishing
  the number belongs to the facts source (WP-J1-02 / the AMH facts contract), not here — deriving
  a TISS guia number from the opaque `guide_identity_ref` would fabricate clinical identity.
* **The portal door still bypasses the dedup posture** (V15 L0 finding on #388):
  `start_process_idempotent` short-circuits `HumanIntakeProvenance` straight into `start_human`
  (`tools/mcp_cibseven/transport.py:1637-1646`) BEFORE `start_dedup_posture`,
  `_require_strict_gate_seams`, the durable dedup claim and `find_active_instance`; `start_human`
  itself performs no existence check (`gateway/intake/native_dispatch.py:322`). `SP-OP-AUTH-001`
  is `EXCLUSIVE` (`transport.py:1259`), so once the guide number IS published, the portal channel
  would take no claim and read none — two live instances per guia. This daemon does not reach that
  door (the fence above refuses first) and does not patch the security core it does not own; the
  requirement is recorded in the PR body with the two-starts-one-instance engine test it needs.

=================================================================================================
Fail-closed, everywhere
=================================================================================================
An unknown `operation` is `unsupported` and is never dispatched (the column has a CHECK today;
a future kind must teach this engine what it means, not be guessed at). An expired
`authorization_until` is `expired` and is never dispatched — the admission's authority window is
closed, `mark_sending` would deny it (`native_store.py:461`), and a daemon must not spin on it.
A live lease belongs to another drainer: `leased`. No disposition is invented: this engine writes
NOTHING to the outbox itself — every state transition is the store's, inside the audited seam.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Final, Literal, Protocol

import structlog

from maezo.gateway.human.auth_profile import (
    EffectCommand,
    HumanDocumentCommand,
    HumanStartCommand,
    NativeEffectReceipt,
    StartFacts,
)
from maezo.gateway.intake.native_dispatch import AuthIntakeGuideNumberUnavailableError
from maezo.gateway.intake.native_source_lifecycle import AuthCallerBinding
from maezo.runtime.dependency_failures import PROGRAMMING_ERRORS
from maezo.tools.mcp_cibseven.transport import ProcessInstance
from maezo.tools.process_business_keys import (
    AUTH_BUSINESS_KEY_PREFIX,
    LegacyAuthIntakeBusinessKeyError,
    refuse_legacy_auth_intake_business_key,
)

logger = structlog.get_logger(__name__)

#: The two operations `portal_intake.native_outbox.operation` may hold
#: (`native_schema.sql:5` CHECK). Anything else is `unsupported`, never guessed.
OPERATIONS: Final[frozenset[str]] = frozenset({"auth.start", "auth.documents.respond"})

#: Outbox states this engine will hand to the sanctioned seam. `admitted`/`claimed` are the
#: forward path; `sending`/`reconciling` are the crash-recovery path, where the seam performs the
#: receipt-first lookup itself (`native_dispatch.py:120-150`) before any replay. `executed` and
#: `rejected` are terminal and are never claimed (`native_store.py:394`).
DRAINABLE_STATES: Final[frozenset[str]] = frozenset({"admitted", "claimed", "sending", "reconciling"})

#: Outbox states that mean "a previous send may already be on the wire". Reaching the seam with
#: one of these makes `claim` return `reconcile_first=True` (`native_store.py:402`).
RECOVERY_STATES: Final[frozenset[str]] = frozenset({"sending", "reconciling"})

#: Head-less recovery is structurally unavailable today, and this engine says so instead of
#: pretending. `AuthDispatcher._dispatch`'s receipt-first branch opens with
#: `authority.current(command, read=True, caller=caller)` (`native_dispatch.py:121`), and the
#: production authority refuses every `read=True` without a live browser caller
#: (`native_authority.py:546`). A daemon has no browser session, so a row left in
#: `sending`/`reconciling` by a crash cannot be reconciled from here: the seam fails closed BEFORE
#: any effect (no double-send risk), and this engine reports `awaiting_receipt` rather than
#: inventing a decision the audited seam declined to make. The narrowest remedy — NOT applied
#: here, because it belongs to the security core this WP does not own — is to let `read=True`
#: fall back to the original sealed admission identity via `current_original` /
#: `actor_for_original` (`native_source_lifecycle.py:880`, `:916`) exactly as the effect path
#: already does, which grants strictly less authority than the write path it mirrors.
DISCLOSURE_FINDING: Final[str] = (
    "head-less read authority absent: native_authority.py:546 refuses read=True with caller=None"
)

#: Disposition of ONE outbox row in ONE sweep.
Disposition = Literal[
    "started",  # a durable receipt now exists and reports a fresh native start
    "existing",  # a durable receipt exists and reports the native instance already existed
    "correlated",  # a durable receipt exists for an `auth.documents.respond` command
    "already_executed",  # a receipt existed BEFORE this sweep; nothing was sent
    "awaiting_receipt",  # a send may be on the wire; no receipt yet (see DISCLOSURE_FINDING)
    "expired",  # the admission's authority window closed; never dispatched
    "leased",  # another drainer holds a live lease
    "deferred",  # a sibling of the same business key was dispatched in this sweep
    "unsupported",  # unknown operation kind: fail-closed, never dispatched
    "unavailable",  # a dependency refused; retried on the next sweep
]

#: Dispositions that prove the effect happened (a command-bound receipt was read back).
EXECUTED: Final[frozenset[str]] = frozenset({"started", "existing", "correlated", "already_executed"})


@dataclass(frozen=True, slots=True, repr=False)
class PendingDispatch:
    """One `portal_intake.native_outbox` row, projected to what a drain decision needs.

    Carries no payload, no principal-identifying value beyond the opaque refs the outbox itself
    stores, and never the command: the command is assembled by `PreparedCommandSource` under the
    published-source rules, not reconstructed from this projection.
    """

    command_id: str
    admission_ref: str
    resource_ref: str
    operation: str
    state: str
    authorization_until: datetime
    lease_until: datetime | None
    #: `portal_intake.intake.guide_identity_ref` for `auth.start` (the business-key domain);
    #: `None` for `auth.documents.respond`, and `None` for a start row whose intake row is
    #: missing — which is a refusal, not a default (see `_domain`).
    guide_identity_ref: str | None = None

    def __repr__(self) -> str:  # pragma: no cover - debugging aid, refs only
        return f"PendingDispatch(command_id={self.command_id!r}, operation={self.operation!r})"


@dataclass(frozen=True, slots=True, repr=False)
class PreparedDispatch:
    """An assembled, source-bound command, ready for the sanctioned seam.

    `facts` is present exactly for `HumanStartCommand` — `dispatch_prepared_start` re-derives the
    fourteen-variable projection from it and refuses unless the digest matches the command
    (`native_composition.py:54-58`), so the pair must travel together.
    """

    command: EffectCommand
    facts: StartFacts | None = None

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"PreparedDispatch(command_id={self.command.command_id!r})"


class CommandUnassembledError(RuntimeError):
    """A `PreparedCommandSource` refused. `token` is the bounded reason a report may carry.

    The vocabulary is the port's, not the adapter's, so a report can name WHY a row did not drain
    (`start_facts_unpublished`, `document_command_source_absent`, ...) without this engine learning
    anything about Postgres or published heads.
    """

    def __init__(self, token: str) -> None:
        super().__init__(token)
        self.token = token


@dataclass(frozen=True, slots=True)
class SeamResult:
    """What the sanctioned seam said. Evidence, not a verdict — the receipt is the verdict."""

    #: The business key the engine reported for a start (`ProcessInstance.business_key`).
    business_key: str | None = None
    #: A bounded token when the seam refused before returning. Never an exception message.
    refusal: str | None = None


@dataclass(frozen=True, slots=True)
class ItemOutcome:
    """What one row did, in refs and digests only — never a payload, never a person."""

    command_id: str
    operation: str
    disposition: Disposition
    #: `NativeEffectReceipt.receipt_ref` when a durable receipt was read back.
    receipt_ref: str | None = None
    #: `NativeEffectReceipt.case_ref` / `process_instance_id` — the evidence bundle's engine anchor.
    case_ref: str | None = None
    process_instance_id: str | None = None
    #: The business key the engine actually reported for a start (never re-derived here).
    business_key: str | None = None
    #: A bounded token for why a dependency refused. Never an exception message (they can carry
    #: query text); always one of a small vocabulary.
    reason: str | None = None

    @property
    def executed(self) -> bool:
        return self.disposition in EXECUTED


@dataclass(frozen=True, slots=True)
class DrainReport:
    """What ONE sweep did. Counts plus per-row outcomes; no payload ever enters this object."""

    outcomes: tuple[ItemOutcome, ...] = ()

    @property
    def scanned(self) -> int:
        return len(self.outcomes)

    @property
    def executed(self) -> int:
        return sum(1 for outcome in self.outcomes if outcome.executed)

    @property
    def dispatched(self) -> int:
        """Rows this sweep actually handed to the seam and that now carry a fresh receipt."""
        return sum(1 for outcome in self.outcomes if outcome.disposition in ("started", "correlated"))

    def counts(self) -> Mapping[str, int]:
        tally: dict[str, int] = {}
        for outcome in self.outcomes:
            tally[outcome.disposition] = tally.get(outcome.disposition, 0) + 1
        return tally

    @property
    def idle(self) -> bool:
        """Nothing this sweep could act on — the loop may sleep."""
        return self.dispatched == 0


class PendingIntakeSource(Protocol):
    """The outbox read surface. Ordering is the implementation's contract, not this engine's."""

    async def pending(self, *, limit: int) -> tuple[PendingDispatch, ...]: ...


class PreparedCommandSource(Protocol):
    """Assembles the immutable command for one admitted row from its published sources."""

    async def prepared(self, item: PendingDispatch) -> PreparedDispatch: ...


class DispatchTarget(Protocol):
    """The sanctioned seam — satisfied by `AuthIntakeComponents` (`native_composition.py:44`)."""

    async def dispatch_prepared_start(
        self,
        command: HumanStartCommand,
        facts: StartFacts,
        *,
        caller: AuthCallerBinding | None = None,
    ) -> ProcessInstance: ...

    async def dispatch_prepared_documents(
        self, command: HumanDocumentCommand, *, caller: AuthCallerBinding | None = None
    ) -> NativeEffectReceipt: ...


class ReceiptAuthority(Protocol):
    """The durable receipt read — satisfied by `PostgresAuthDispatchStore` (`native_store.py:833`)."""

    async def completed(self, command: EffectCommand) -> NativeEffectReceipt | None: ...


class IntakeDispatchService:
    """Drains the AUTH intake outbox through the audited seam, once per `drain_once`."""

    def __init__(
        self,
        *,
        pending: PendingIntakeSource,
        commands: PreparedCommandSource,
        target: DispatchTarget,
        receipts: ReceiptAuthority,
        batch_size: int = 16,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        if batch_size < 1:
            raise ValueError("intake dispatch: batch_size must be >= 1")
        self.pending, self.commands, self.target, self.receipts = pending, commands, target, receipts
        self.batch_size, self.clock = batch_size, clock

    @staticmethod
    def _domain(item: PendingDispatch) -> tuple[str, str] | None:
        """The business-key domain this row belongs to, or `None` when it has none.

        `auth.start`: the guide identity — the one variable in `AUTH-{tenant}-{numero_guia_tiss}`
        (owner decision #16(a)). `auth.documents.respond`: the case. A start row with no guide
        identity has no domain and is therefore not dispatchable (a row that cannot be compared
        with its siblings cannot be deduplicated against them).
        """
        if item.operation == "auth.start":
            return None if item.guide_identity_ref is None else ("auth.start", item.guide_identity_ref)
        if item.operation == "auth.documents.respond":
            return ("auth.documents.respond", item.resource_ref)
        return None

    def _drainable(self, item: PendingDispatch) -> ItemOutcome | None:
        """Pre-send refusals, in the order that costs the least and reveals the most."""
        if item.operation not in OPERATIONS:
            # Never guessed: a new kind must teach this engine what it means.
            return self._refuse(item, "unsupported", "unknown_operation")
        if item.state not in DRAINABLE_STATES:
            return self._refuse(item, "unavailable", "state_not_drainable")
        now = self.clock()
        if now >= item.authorization_until:
            # The admitting session's authority window is closed. `mark_sending` would deny
            # (`native_store.py:461`); spinning on it would be a retry loop with no legal end.
            return self._refuse(item, "expired", "authorization_window_closed")
        if item.lease_until is not None and now < item.lease_until:
            return self._refuse(item, "leased", "lease_held")
        if self._domain(item) is None:
            return self._refuse(item, "unavailable", "business_key_domain_unresolved")
        return None

    @staticmethod
    def _refuse(item: PendingDispatch, disposition: Disposition, reason: str) -> ItemOutcome:
        return ItemOutcome(
            command_id=item.command_id,
            operation=item.operation,
            disposition=disposition,
            reason=reason,
        )

    @staticmethod
    def _from_receipt(
        item: PendingDispatch,
        receipt: NativeEffectReceipt,
        *,
        disposition: Disposition,
        business_key: str | None = None,
    ) -> ItemOutcome:
        return ItemOutcome(
            command_id=item.command_id,
            operation=item.operation,
            disposition=disposition,
            receipt_ref=receipt.receipt_ref,
            case_ref=receipt.case_ref,
            process_instance_id=receipt.process_instance_id,
            business_key=business_key,
        )

    async def _send(self, item: PendingDispatch, prepared: PreparedDispatch) -> SeamResult:
        """Hand the command to the sanctioned seam; report what it said, decide nothing.

        Swallows nothing silently: a refusal becomes a bounded token and the caller then asks the
        RECEIPT what actually happened. That order is the whole point — see the module docstring.
        The returned key is evidence only; it is never used to decide anything.
        """
        command = prepared.command
        try:
            if isinstance(command, HumanStartCommand):
                if prepared.facts is None:
                    raise ValueError("start command assembled without its facts")
                instance = await self.target.dispatch_prepared_start(command, prepared.facts)
                return SeamResult(business_key=instance.business_key)
            if isinstance(command, HumanDocumentCommand):
                await self.target.dispatch_prepared_documents(command)
                return SeamResult()
            raise ValueError("unsupported command type")
        except AuthIntakeGuideNumberUnavailableError:
            # The one refusal an operator must be able to read off a log line without a debugger:
            # the published facts carry no `numero_guia_tiss`, so the contractual business key
            # `AUTH-{tenant}-{numero_guia_tiss}` cannot be composed and the portal start refuses
            # BEFORE any engine effect (`native_composition.py:58-60`, WP-J1-11 / owner decision
            # #16(a)). It is not a fault of this daemon and it is not retryable by it: the facts
            # source must publish the number. Reported as its own token so a silent "nothing
            # drains" is impossible.
            logger.warning(
                "intake_dispatch_guide_number_unpublished",
                command_id=item.command_id,
                intake_ref=item.resource_ref,
            )
            return SeamResult(refusal="guide_number_unpublished")
        except PROGRAMMING_ERRORS:
            # A derived signature, a violated state contract, a stub port: a seam BUG must stop
            # the daemon loudly — the boundary allowlist of the broad-except fence records this
            # clause — never turn into one more silent `seam_refused`.
            raise
        except Exception as exc:
            # The boundary contract (allowlisted in the fence): EVERY remaining failure mode —
            # refused authority, a closed window, a transport error, and (on the happy path) the
            # head-less disclosure read — leads to the same next step, which is reading the durable
            # receipt. Nothing here invents an outcome from the failure; only the exception TYPE is
            # logged, messages can carry query text.
            logger.info(
                "intake_dispatch_seam_refused",
                command_id=item.command_id,
                operation=item.operation,
                state=item.state,
                error=type(exc).__name__,
            )
            return SeamResult(refusal="seam_refused")

    @staticmethod
    def _business_key_anomaly(business_key: str | None) -> str | None:
        """Evidence check on the key the ENGINE reported. Composes nothing.

        `auth_business_key` (`tools/process_business_keys.py:92`) is the one composer, and it is
        not called here: this engine has no `numero_guia_tiss` of its own and inventing one to
        "verify" a key would be exactly the second idempotency domain #16(a) closed. So the check
        is the honest one available to an observer — the key must be in the contractual family and
        must not be the legacy `AUTHI-{guide_identity_ref}` form
        (`refuse_legacy_auth_intake_business_key`, same module). An anomaly is RECORDED, never used
        to hide an effect that already happened.
        """
        if business_key is None:
            return None
        try:
            refuse_legacy_auth_intake_business_key(business_key)
        except LegacyAuthIntakeBusinessKeyError:
            return "legacy_business_key"
        return None if business_key.startswith(AUTH_BUSINESS_KEY_PREFIX) else "foreign_business_key"

    async def _handle(self, item: PendingDispatch) -> ItemOutcome:
        refusal = self._drainable(item)
        if refusal is not None:
            return refusal
        try:
            prepared = await self.commands.prepared(item)
        except CommandUnassembledError as unassembled:
            logger.info(
                "intake_dispatch_assembly_refused",
                command_id=item.command_id,
                operation=item.operation,
                token=unassembled.token,
            )
            return self._refuse(item, "unavailable", unassembled.token)
        except PROGRAMMING_ERRORS:
            # Same boundary rule as `_send` (allowlisted in the broad-except fence): an assembly
            # BUG propagates and stops the daemon; only a dependency refusal becomes a token.
            raise
        except Exception as exc:
            logger.info(
                "intake_dispatch_assembly_refused",
                command_id=item.command_id,
                operation=item.operation,
                error=type(exc).__name__,
            )
            return self._refuse(item, "unavailable", "command_unassembled")
        try:
            settled = await self.receipts.completed(prepared.command)
        except PROGRAMMING_ERRORS:
            # A receipt read cannot be allowed to swallow a bug either: `PostgresAuthDispatchStore.
            # completed` translates every internal failure it owns into `AuthUnavailableError`
            # (RuntimeError) precisely so this boundary can tell a refusal from a defect.
            raise
        except Exception as exc:
            logger.info(
                "intake_dispatch_receipt_unavailable",
                command_id=item.command_id,
                error=type(exc).__name__,
            )
            return self._refuse(item, "unavailable", "receipt_unreadable")
        if settled is not None:
            # A receipt already existed: this row is done, and nothing is sent. The durable
            # receipt is the only thing that may ever say so.
            return self._from_receipt(item, settled, disposition="already_executed")

        seam = await self._send(item, prepared)

        try:
            receipt = await self.receipts.completed(prepared.command)
        except PROGRAMMING_ERRORS:
            # A bug here must never be reported as `awaiting_receipt`: that disposition means "a
            # send may be on the wire", and a defect is not evidence of one.
            raise
        except Exception as exc:
            logger.info(
                "intake_dispatch_receipt_unavailable_after_send",
                command_id=item.command_id,
                error=type(exc).__name__,
            )
            return self._refuse(item, "awaiting_receipt", "receipt_unreadable")
        if receipt is None:
            # No durable receipt: the effect is NOT proven. A refusal that happened BEFORE any
            # effect is reported as itself (`unavailable`, retryable next sweep); only a send that
            # may already be on the wire is `awaiting_receipt`.
            if seam.refusal == "guide_number_unpublished":
                return self._refuse(item, "unavailable", seam.refusal)
            reason = DISCLOSURE_FINDING if item.state in RECOVERY_STATES else "no_receipt_after_dispatch"
            return self._refuse(item, "awaiting_receipt", reason)
        # The receipt's own `outcome` decides, and its model already forbids the wrong pairing
        # (`auth_profile.py:530-543`): a start receipt is `started|existing`, a document receipt is
        # `documents_correlated`. Anything else is a receipt this engine will not interpret.
        disposition: Disposition
        if receipt.operation == "auth.start" and receipt.outcome in ("started", "existing"):
            disposition = "started" if receipt.outcome == "started" else "existing"
        elif receipt.operation == "auth.documents.respond" and receipt.outcome == "documents_correlated":
            disposition = "correlated"
        else:  # pragma: no cover - forbidden by NativeEffectReceipt.complete
            return self._refuse(item, "unavailable", "receipt_shape_unrecognised")
        anomaly = self._business_key_anomaly(seam.business_key)
        outcome = ItemOutcome(
            command_id=item.command_id,
            operation=item.operation,
            disposition=disposition,
            receipt_ref=receipt.receipt_ref,
            case_ref=receipt.case_ref,
            process_instance_id=receipt.process_instance_id,
            business_key=seam.business_key,
            reason=anomaly,
        )
        if anomaly is not None:
            # The effect is real and is reported as such; the key it was filed under is not the
            # contractual one, which is a two-domain defect an operator must see immediately.
            logger.error(
                "intake_dispatch_business_key_anomaly",
                command_id=item.command_id,
                anomaly=anomaly,
                process_instance_id=receipt.process_instance_id,
            )
        logger.info(
            "intake_dispatch_executed",
            command_id=item.command_id,
            operation=item.operation,
            disposition=outcome.disposition,
            receipt_ref=outcome.receipt_ref,
            case_ref=outcome.case_ref,
            process_instance_id=outcome.process_instance_id,
            business_key=outcome.business_key,
        )
        return outcome

    async def drain_once(self) -> DrainReport:
        """One sweep: read the outbox, dispatch at most one row per business-key domain.

        Sequential by construction. Concurrency here would buy nothing (the store serialises every
        row with `pg_advisory_xact_lock` + a lease) and would cost the per-key fence its meaning.
        """
        items = await self.pending.pending(limit=self.batch_size)
        outcomes: list[ItemOutcome] = []
        claimed: set[tuple[str, str]] = set()
        for item in items:
            domain = self._domain(item)
            if domain is not None and domain in claimed:
                # A sibling of the same business key already went out in this sweep. Its receipt
                # decides what this row's assembly will even be allowed to be; acting on both now
                # is exactly the double-start the key exists to prevent.
                outcomes.append(self._refuse(item, "deferred", "business_key_busy"))
                continue
            outcome = await self._handle(item)
            if domain is not None and outcome.disposition not in ("leased", "deferred"):
                claimed.add(domain)
            outcomes.append(outcome)
        report = DrainReport(tuple(outcomes))
        # No `**mapping` on a logger call: the field NAMES must stay static and visible to the
        # key-scrubber sweep (`test_logger_kwarg_unpacks_are_pinned…`). `counts()` is keyed by the
        # closed `Disposition` Literal, so the shape is written out in full — one field per
        # disposition, zero when absent — which also keeps the log line fixed-width for dashboards.
        counts = report.counts()
        logger.info(
            "intake_dispatch_drained",
            scanned=report.scanned,
            started=counts.get("started", 0),
            existing=counts.get("existing", 0),
            correlated=counts.get("correlated", 0),
            already_executed=counts.get("already_executed", 0),
            awaiting_receipt=counts.get("awaiting_receipt", 0),
            expired=counts.get("expired", 0),
            leased=counts.get("leased", 0),
            deferred=counts.get("deferred", 0),
            unsupported=counts.get("unsupported", 0),
            unavailable=counts.get("unavailable", 0),
        )
        return report


async def run_dispatch_loop(
    service: IntakeDispatchService,
    *,
    poll_interval_s: float,
    stop_event: asyncio.Event | None = None,
    max_sweeps: int | None = None,
    heartbeat: Callable[[], None] | None = None,
) -> list[DrainReport]:
    """Sweep until `stop_event` (or `max_sweeps`). Sleeps only when a sweep dispatched nothing.

    A sweep that dispatched something is followed immediately by another: a full batch means
    backlog. Errors are NOT swallowed here — a database failure ends the process, the same
    fail-closed posture `run_relay_loop` takes (`a2a/outbox_relay.py:388`); per-row refusals are
    already dispositions, not exceptions.
    """
    reports: list[DrainReport] = []
    sweeps = 0
    while True:
        if stop_event is not None and stop_event.is_set():
            break
        report = await service.drain_once()
        reports.append(report)
        if heartbeat is not None:
            heartbeat()
        sweeps += 1
        if max_sweeps is not None and sweeps >= max_sweeps:
            break
        if report.idle:
            if stop_event is not None:
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(stop_event.wait(), timeout=poll_interval_s)
            else:
                await asyncio.sleep(poll_interval_s)
    return reports


@dataclass(frozen=True, slots=True)
class EvidenceBundle:
    """The J1-DESIGN §3 row-2 evidence for one sweep, in refs only (no payload, no PHI).

    Written by the daemon to its log as one structured line per executed row; the engine-side
    halves (`ACT_HI_PROCINST.BUSINESS_KEY_`, `MZO_AUTH_EFFECT_RECEIPT outcome=started`) are the
    integration proof, not something a Python process may assert about itself.
    """

    executed: tuple[ItemOutcome, ...] = field(default_factory=tuple)

    @classmethod
    def of(cls, report: DrainReport) -> EvidenceBundle:
        return cls(tuple(outcome for outcome in report.outcomes if outcome.executed))

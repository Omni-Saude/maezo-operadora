"""What the intake dispatch daemon must be true about, proven without a database or an engine.

The engine under test is `IntakeDispatchService`, driven through its four ports by in-memory
fakes. Every command, receipt and process instance here is a REAL closed model
(`HumanStartCommand`, `NativeEffectReceipt`, `ProcessInstance`) built by the helpers below, so the
engine's `isinstance` dispatch, the receipt's own `complete` validator and `bind_receipt`-shaped
field agreement are exercised rather than stubbed around.

The four properties the adversarial review asks for, and where each is proven:

* **receipt authority / no HTTP-202-as-execution** — `test_a_successful_transport_call_without_a
  _durable_receipt_is_never_reported_started`, `test_a_dispatch_that_raises_after_the_receipt_
  commits_is_reported_started`.
* **idempotency under crash** — `test_a_row_with_an_existing_receipt_is_never_sent_again`,
  `test_a_sending_row_whose_receipt_appears_is_reported_started_without_a_second_send`,
  `test_a_sending_row_with_no_receipt_reports_the_head_less_recovery_finding`.
* **one business key, one start** — `test_two_rows_of_one_guide_never_both_dispatch_in_a_sweep`,
  `test_a_start_row_without_a_guide_identity_is_never_dispatched`,
  `test_a_legacy_business_key_is_recorded_as_an_anomaly`.
* **fail closed** — unknown operation, closed authorization window, live lease, assembly refusal,
  unpublished guide number.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from maezo.gateway.human.auth_profile import (
    Actor,
    AuditIntent,
    Definition,
    HumanStartCommand,
    NativeEffectReceipt,
    Pin,
    Scope,
    StartFacts,
)
from maezo.gateway.human.auth_transport import AuthUnavailableError
from maezo.gateway.human.read_profile import ArtifactPin, SourceProvenance, digest
from maezo.gateway.intake.native_dispatch import AuthIntakeGuideNumberUnavailableError
from maezo.runtime.intake_dispatch.service import (
    DISCLOSURE_FINDING,
    CommandUnassembledError,
    EvidenceBundle,
    IntakeDispatchService,
    ItemOutcome,
    PendingDispatch,
    PreparedDispatch,
    run_dispatch_loop,
)
from maezo.tools.mcp_cibseven.transport import ProcessInstance, StartOutcome

HASH = "a" * 64
NOW = datetime(2026, 9, 12, 12, 0, tzinfo=UTC)


def source() -> SourceProvenance:
    return SourceProvenance(
        publisher_ref="publisher",
        source_ref="source",
        source_revision=1,
        source_digest=HASH,
        receipt_ref="source-receipt",
        observed_at=NOW - timedelta(seconds=5),
        valid_until=NOW + timedelta(seconds=600),
    )


def scope() -> Scope:
    return Scope(
        tenant="amh",
        environment="test",
        engine_name="engine",
        database_incarnation="incarnation",
        installation_ref="installation",
        installation_revision=1,
    )


def facts(*, intake_ref: str = "intake", guide: str = "guide") -> StartFacts:
    return StartFacts(
        facts_ref="facts",
        intake_ref=intake_ref,
        guide_identity_ref=guide,
        beneficiary_pseudo_id="pseudo",
        provider_ref="provider",
        procedure_code="40101010",
        category="ambulatorial",
        character="eletivo",
        claimed_amount_cents=12345,
        document_refs=(),
        requer_autorizacao=True,
        beneficiario_ativo=True,
        carencia_cumprida=True,
        documentacao_completa=True,
        missing_requirement_codes=(),
        documentary_assessment_ref="assessment",
        request_digest=HASH,
        factual_sources=(source(),),
        policy_artifacts=(ArtifactPin(artifact_ref="policy", digest=HASH),),
    )


def command(*, command_id: str = "command", intake_ref: str = "intake", guide: str = "guide"):
    pins = tuple(
        Pin(kind=kind, resource_ref=ref, head_generation=1, source=source(), payload_digest=HASH)
        for kind, ref in [
            ("actor", "principal"),
            ("document_policy", "assessment"),
            ("guide", guide),
            ("resource_authority", "authority"),
            ("start_facts", "facts"),
        ]
    )
    return HumanStartCommand(
        schema="human-auth-start.v1",
        scope=scope(),
        workload_ref="auth-sender",
        actor=Actor(
            principal_ref="principal",
            issuer="https://identity.example",
            subject="subject",
            membership_revision=1,
            audience="provider",
        ),
        intake_ref=intake_ref,
        command_id=command_id,
        admission=AuditIntent(
            intent_ref=intake_ref,
            admitted_command_id=command_id,
            admitted_digest=HASH,
            source=source(),
        ),
        guide_identity_ref=guide,
        definition=Definition(
            process_key="SP-OP-AUTH-001",
            definition_id="definition",
            definition_digest=HASH,
            deployment_id="deployment",
            input_profile="portal-auth-intake.v1",
            profile_digest=HASH,
        ),
        input_pins=pins,
        start_facts_ref="facts",
        start_facts_digest=HASH,
        projected_variables_digest=HASH,
    )


def receipt(c: HumanStartCommand, *, outcome: str = "started") -> NativeEffectReceipt:
    return NativeEffectReceipt(
        schema="human-auth-effect-receipt.v1",
        scope=c.scope,
        receipt_ref="receipt-" + c.command_id,
        command_id=c.command_id,
        command_digest=digest(c),
        operation="auth.start",
        actor_principal_ref=c.actor.principal_ref,
        admission_ref=c.admission.intent_ref,
        admitted_digest=c.admission.admitted_digest,
        intake_ref=c.intake_ref,
        case_ref="case-" + c.command_id,
        process_instance_id="instance-" + c.command_id,
        definition=c.definition,
        guide_identity_ref=c.guide_identity_ref,
        request_ref=None,
        occurrence_generation=None,
        subscription_id=None,
        document_set_digest=None,
        outcome=outcome,
        committed_at=NOW,
    )


def item(
    *,
    command_id: str = "command",
    intake_ref: str = "intake",
    guide: str | None = "guide",
    operation: str = "auth.start",
    state: str = "admitted",
    authorization_until: datetime | None = None,
    lease_until: datetime | None = None,
) -> PendingDispatch:
    return PendingDispatch(
        command_id=command_id,
        admission_ref=intake_ref,
        resource_ref=intake_ref,
        operation=operation,
        state=state,
        authorization_until=authorization_until or NOW + timedelta(seconds=300),
        lease_until=lease_until,
        guide_identity_ref=guide,
    )


def instance(business_key: str = "AUTH-amh-12345") -> ProcessInstance:
    return ProcessInstance(
        instance_id="instance-command",
        process_key="SP-OP-AUTH-001",
        business_key=business_key,
        state="UNKNOWN",
        already_existed=False,
        start_outcome=StartOutcome.STARTED,
    )


class FakePending:
    def __init__(self, items: tuple[PendingDispatch, ...]) -> None:
        self.items, self.limits = items, []

    async def pending(self, *, limit: int) -> tuple[PendingDispatch, ...]:
        self.limits.append(limit)
        return self.items[:limit]


class FakeCommands:
    """Assembles a real command per row; optionally refuses with a token."""

    def __init__(self, *, refuse: str | None = None) -> None:
        self.refuse, self.asked = refuse, []

    async def prepared(self, pending: PendingDispatch) -> PreparedDispatch:
        self.asked.append(pending.command_id)
        if self.refuse is not None:
            raise CommandUnassembledError(self.refuse)
        return PreparedDispatch(
            command=command(
                command_id=pending.command_id,
                intake_ref=pending.resource_ref,
                guide=pending.guide_identity_ref or "guide",
            ),
            facts=facts(intake_ref=pending.resource_ref, guide=pending.guide_identity_ref or "guide"),
        )


class FakeTarget:
    """The seam. `raises` reproduces the head-less disclosure refusal; `sent` records every call."""

    def __init__(self, *, raises: BaseException | None = None, key: str = "AUTH-amh-12345") -> None:
        self.raises, self.key, self.sent = raises, key, []

    async def dispatch_prepared_start(self, cmd: HumanStartCommand, start_facts: StartFacts, **_: Any):
        self.sent.append(cmd.command_id)
        if self.raises is not None:
            raise self.raises
        return instance(self.key)

    async def dispatch_prepared_documents(self, cmd: Any, **_: Any) -> NativeEffectReceipt:
        self.sent.append(cmd.command_id)
        raise AssertionError("the document path must not be reachable in these tests")


class FakeReceipts:
    """`completed` answers from a script: one entry per call, per command_id."""

    def __init__(self, answers: dict[str, list[NativeEffectReceipt | None]] | None = None) -> None:
        self.answers = answers or {}
        self.calls: list[str] = []

    async def completed(self, cmd: HumanStartCommand) -> NativeEffectReceipt | None:
        self.calls.append(cmd.command_id)
        script = self.answers.get(cmd.command_id, [])
        return script.pop(0) if script else None

    async def settled(self, cmd: HumanStartCommand) -> NativeEffectReceipt | None:
        """The pre-send read: the drain's FIRST receipt call, before any send."""
        return await self.completed(cmd)


class StoreShapedReceipts:
    """The REAL `PostgresAuthDispatchStore` receipt semantics, replayed on a row model.

    `completed` is the real adapter verbatim: it compares the sealed command for EVERY state
    (`native_store.py`, "if self._command(row) != command") and a fresh `admitted` row carries no
    sealed command — `stage_intake`'s INSERT writes none — so `_command` unseals a NULL triple and
    `completed`'s own boundary translates the failure into `AuthUnavailableError`
    (`native_store.py`, "except Exception: raise AuthUnavailableError() from None"). That raise is
    what the real authority answers for every row the daemon has not sent yet. `settled` is the
    pre-send read AFTER the repair: it answers `None` for a row that cannot carry a receipt yet
    and behaves exactly like `completed` for every sealed row — including failing closed when a
    sealed row cannot prove its own receipt.
    """

    def __init__(self) -> None:
        self.sealed: set[str] = set()
        self.executed: dict[str, NativeEffectReceipt] = {}
        self.corrupt: set[str] = set()
        self.completed_calls: list[str] = []
        self.settled_calls: list[str] = []

    def seal(self, command_id: str) -> None:
        """`prepare`: the row now carries a sealed command (no receipt yet)."""
        self.sealed.add(command_id)

    def commit(self, cmd: HumanStartCommand, r: NativeEffectReceipt) -> None:
        """The engine effect committed its durable receipt; the row is `executed`."""
        self.seal(cmd.command_id)
        self.executed[cmd.command_id] = r

    def _readable(self, command_id: str) -> None:
        if command_id in self.corrupt:
            raise AuthUnavailableError()
        if command_id not in self.sealed:
            # The real adapter's translation of the NULL-command unseal.
            raise AuthUnavailableError()

    async def completed(self, cmd: HumanStartCommand) -> NativeEffectReceipt | None:
        self.completed_calls.append(cmd.command_id)
        self._readable(cmd.command_id)
        return self.executed.get(cmd.command_id)

    async def settled(self, cmd: HumanStartCommand) -> NativeEffectReceipt | None:
        self.settled_calls.append(cmd.command_id)
        if cmd.command_id in self.corrupt:
            raise AuthUnavailableError()
        if cmd.command_id not in self.sealed:
            # Nothing is sealed yet: no receipt can exist (schema: `state='executed'` coincide com
            # `receipt_digest IS NOT NULL`, e executado exige comando selado). Responder None e'
            # o fato, nao um fallback.
            return None
        return self.executed.get(cmd.command_id)


class StoreBackedTarget:
    """The seam as production composes it: `prepare` seals the row, the effect commits a receipt.

    (`dispatch_prepared_start` chama `native_store.prepare` antes do transporte; o recibo dura'el
    e' commitado dentro do proprio seam. Quando `_send` retorna, a linha esta' selada e executada.)
    """

    def __init__(self, store: StoreShapedReceipts) -> None:
        self.store, self.sent = store, []

    async def dispatch_prepared_start(self, cmd: HumanStartCommand, start_facts: StartFacts, **_: Any):
        self.sent.append(cmd.command_id)
        self.store.commit(cmd, receipt(cmd))
        return instance()

    async def dispatch_prepared_documents(self, cmd: Any, **_: Any) -> NativeEffectReceipt:
        self.sent.append(cmd.command_id)
        raise AssertionError("the document path must not be reachable in these tests")


def build(
    items: tuple[PendingDispatch, ...],
    *,
    commands: FakeCommands | None = None,
    target: FakeTarget | None = None,
    receipts: FakeReceipts | None = None,
    batch_size: int = 16,
) -> tuple[IntakeDispatchService, FakePending, FakeCommands, FakeTarget, FakeReceipts]:
    pending = FakePending(items)
    commands = commands or FakeCommands()
    target = target or FakeTarget()
    receipts = receipts or FakeReceipts()
    service = IntakeDispatchService(
        pending=pending,
        commands=commands,
        target=target,
        receipts=receipts,
        batch_size=batch_size,
        clock=lambda: NOW,
    )
    return service, pending, commands, target, receipts


def only(report: Any) -> ItemOutcome:
    assert len(report.outcomes) == 1, report.outcomes
    return report.outcomes[0]


# --------------------------------------------------------------------------------------------
# receipt authority: the transport result never decides
# --------------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_successful_transport_call_without_a_durable_receipt_is_never_reported_started():
    """HTTP-202-as-execution, refused: the seam returned a ProcessInstance and nothing else."""
    service, _p, _c, target, _r = build((item(),), receipts=FakeReceipts({"command": [None, None]}))
    outcome = only(await service.drain_once())
    assert target.sent == ["command"]
    assert outcome.disposition == "awaiting_receipt"
    assert outcome.reason == "no_receipt_after_dispatch"
    assert not outcome.executed


@pytest.mark.asyncio
async def test_a_dispatch_that_raises_after_the_receipt_commits_is_reported_started():
    """The head-less happy path: the disclosure read refuses, the receipt is already durable."""
    c = command()
    service, _p, _cmds, target, receipts = build(
        (item(),),
        target=FakeTarget(raises=RuntimeError("disclosure read needs a caller")),
        receipts=FakeReceipts({"command": [None, receipt(c)]}),
    )
    outcome = only(await service.drain_once())
    assert target.sent == ["command"]
    assert outcome.disposition == "started"
    assert outcome.executed
    assert (outcome.receipt_ref, outcome.case_ref, outcome.process_instance_id) == (
        "receipt-command",
        "case-command",
        "instance-command",
    )
    # Consulted before AND after the send: that ordering is the whole guarantee.
    assert receipts.calls == ["command", "command"]


@pytest.mark.asyncio
async def test_an_existing_native_instance_is_reported_existing_not_started():
    c = command()
    service, *_ = build((item(),), receipts=FakeReceipts({"command": [None, receipt(c, outcome="existing")]}))
    outcome = only(await service.drain_once())
    assert (outcome.disposition, outcome.executed) == ("existing", True)


@pytest.mark.asyncio
async def test_a_fresh_admitted_row_drains_instead_of_refusing_a_receipt_it_cannot_have():
    """MAJOR-1: the ONLY real `ReceiptAuthority` cannot answer the pre-send read for a fresh row.

    `PostgresAuthDispatchStore.completed` compares the sealed command for every state, and a fresh
    `admitted` row carries none — the seal (`prepare`) happens only inside the send this read
    precedes. Replayed here with the real adapter's semantics (unsealed -> `AuthUnavailableError`),
    the drain must still dispatch: the pre-send read answers the receipt question without
    demanding a seal that cannot exist yet, and the strict post-send read still guards the send.
    """
    store_receipts = StoreShapedReceipts()
    target = StoreBackedTarget(store_receipts)
    service, *_ = build((item(),), target=target, receipts=store_receipts)
    outcome = only(await service.drain_once())
    assert target.sent == ["command"]
    assert outcome.disposition == "started"
    assert outcome.executed
    # The pre-send read answered before the send; the strict `completed` still read after it.
    assert store_receipts.settled_calls == ["command"]
    assert store_receipts.completed_calls == ["command"]


@pytest.mark.asyncio
async def test_a_sealed_row_that_cannot_prove_its_receipt_still_fails_closed():
    """Genuine receipt unreadability is not the fresh-row case: it still refuses, never sends."""
    store_receipts = StoreShapedReceipts()
    store_receipts.seal("command")
    store_receipts.corrupt.add("command")
    target = StoreBackedTarget(store_receipts)
    service, *_ = build((item(),), target=target, receipts=store_receipts)
    outcome = only(await service.drain_once())
    assert target.sent == []
    assert (outcome.disposition, outcome.reason) == ("unavailable", "receipt_unreadable")
    assert not outcome.executed


# --------------------------------------------------------------------------------------------
# idempotency, including under crash
# --------------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_row_with_an_existing_receipt_is_never_sent_again():
    c = command()
    service, _p, _cmds, target, _r = build((item(),), receipts=FakeReceipts({"command": [receipt(c)]}))
    outcome = only(await service.drain_once())
    assert target.sent == []
    assert outcome.disposition == "already_executed"
    assert outcome.executed


@pytest.mark.asyncio
async def test_a_sending_row_whose_receipt_appears_is_reported_started_without_a_second_send():
    """Crash after the send: the seam's own receipt-first branch reconciles, nothing is replayed."""
    c = command()
    service, _p, _cmds, target, _r = build(
        (item(state="sending"),), receipts=FakeReceipts({"command": [None, receipt(c)]})
    )
    outcome = only(await service.drain_once())
    assert outcome.disposition == "started"
    # The seam was entered exactly once; whether it replayed is ITS decision, and it reconciled.
    assert target.sent == ["command"]


@pytest.mark.asyncio
async def test_a_sending_row_with_no_receipt_reports_the_head_less_recovery_finding():
    """No receipt after a possible send is never `started`, and says exactly what is missing."""
    service, *_ = build((item(state="reconciling"),))
    outcome = only(await service.drain_once())
    assert outcome.disposition == "awaiting_receipt"
    assert outcome.reason == DISCLOSURE_FINDING
    assert "caller=None" in DISCLOSURE_FINDING


# --------------------------------------------------------------------------------------------
# one business key, one start
# --------------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_two_rows_of_one_guide_never_both_dispatch_in_a_sweep():
    c = command()
    service, _p, _cmds, target, _r = build(
        (item(command_id="first"), item(command_id="second", intake_ref="intake-2")),
        receipts=FakeReceipts({"first": [None, receipt(c)]}),
    )
    report = await service.drain_once()
    assert [outcome.disposition for outcome in report.outcomes] == ["started", "deferred"]
    assert target.sent == ["first"]
    assert report.outcomes[1].reason == "business_key_busy"


@pytest.mark.asyncio
async def test_two_rows_of_different_guides_both_dispatch_in_one_sweep():
    service, _p, _cmds, target, _r = build(
        (item(command_id="first"), item(command_id="second", intake_ref="intake-2", guide="guide-2")),
    )
    await service.drain_once()
    assert target.sent == ["first", "second"]


@pytest.mark.asyncio
async def test_a_start_row_without_a_guide_identity_is_never_dispatched():
    """No guide identity means no business-key domain, so no way to deduplicate against siblings."""
    service, _p, _cmds, target, _r = build((item(guide=None),))
    outcome = only(await service.drain_once())
    assert (outcome.disposition, outcome.reason) == ("unavailable", "business_key_domain_unresolved")
    assert target.sent == []


@pytest.mark.asyncio
async def test_a_legacy_business_key_is_recorded_as_an_anomaly_without_hiding_the_effect():
    c = command()
    service, *_rest = build(
        (item(),),
        target=FakeTarget(key="AUTHI-guide"),
        receipts=FakeReceipts({"command": [None, receipt(c)]}),
    )
    outcome = only(await service.drain_once())
    assert outcome.disposition == "started"  # the effect happened and is reported truthfully
    assert outcome.reason == "legacy_business_key"
    assert outcome.business_key == "AUTHI-guide"


@pytest.mark.asyncio
async def test_a_business_key_outside_the_auth_family_is_recorded_as_an_anomaly():
    c = command()
    service, *_rest = build(
        (item(),), target=FakeTarget(key="ESC-amh-1"), receipts=FakeReceipts({"command": [None, receipt(c)]})
    )
    assert only(await service.drain_once()).reason == "foreign_business_key"


# --------------------------------------------------------------------------------------------
# fail closed
# --------------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_an_unknown_operation_is_refused_and_never_dispatched():
    service, _p, commands, target, _r = build((item(operation="auth.something.new"),))
    outcome = only(await service.drain_once())
    assert (outcome.disposition, outcome.reason) == ("unsupported", "unknown_operation")
    assert target.sent == [] and commands.asked == []


@pytest.mark.asyncio
async def test_a_closed_authorization_window_is_refused_and_never_dispatched():
    service, _p, commands, target, _r = build((item(authorization_until=NOW - timedelta(seconds=1)),))
    outcome = only(await service.drain_once())
    assert (outcome.disposition, outcome.reason) == ("expired", "authorization_window_closed")
    assert target.sent == [] and commands.asked == []


@pytest.mark.asyncio
async def test_a_live_lease_belongs_to_another_drainer():
    service, _p, _cmds, target, _r = build((item(lease_until=NOW + timedelta(seconds=10)),))
    outcome = only(await service.drain_once())
    assert (outcome.disposition, outcome.reason) == ("leased", "lease_held")
    assert target.sent == []


@pytest.mark.asyncio
async def test_an_expired_lease_is_drainable_again():
    service, _p, _cmds, target, _r = build((item(state="claimed", lease_until=NOW - timedelta(seconds=1)),))
    await service.drain_once()
    assert target.sent == ["command"]


@pytest.mark.asyncio
async def test_a_terminal_state_is_never_claimed():
    service, _p, _cmds, target, _r = build((item(state="executed"),))
    outcome = only(await service.drain_once())
    assert (outcome.disposition, outcome.reason) == ("unavailable", "state_not_drainable")
    assert target.sent == []


@pytest.mark.asyncio
async def test_an_assembly_refusal_surfaces_its_own_token():
    service, _p, _cmds, target, _r = build((item(),), commands=FakeCommands(refuse="start_facts_unpublished"))
    outcome = only(await service.drain_once())
    assert (outcome.disposition, outcome.reason) == ("unavailable", "start_facts_unpublished")
    assert target.sent == []


@pytest.mark.asyncio
async def test_an_unpublished_guide_number_is_unavailable_not_awaiting_receipt():
    """The refusal happens BEFORE any effect, so the row is retryable, not uncertain."""
    service, *_rest = build((item(),), target=FakeTarget(raises=AuthIntakeGuideNumberUnavailableError()))
    outcome = only(await service.drain_once())
    assert (outcome.disposition, outcome.reason) == ("unavailable", "guide_number_unpublished")


@pytest.mark.asyncio
async def test_a_receipt_read_that_refuses_before_the_send_never_dispatches():
    class Refusing(FakeReceipts):
        async def completed(self, cmd: HumanStartCommand) -> NativeEffectReceipt | None:
            raise RuntimeError("protected store unavailable")

    service, _p, _cmds, target, _r = build((item(),), receipts=Refusing())
    outcome = only(await service.drain_once())
    assert (outcome.disposition, outcome.reason) == ("unavailable", "receipt_unreadable")
    assert target.sent == []


def test_a_batch_size_below_one_is_refused_at_construction():
    with pytest.raises(ValueError):
        build((), batch_size=0)


# --------------------------------------------------------------------------------------------
# drain order, report shape, loop
# --------------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rows_are_handled_in_the_order_the_source_returned_them():
    rows = tuple(item(command_id=f"c{n}", intake_ref=f"intake-{n}", guide=f"guide-{n}") for n in range(4))
    service, _p, commands, target, _r = build(rows)
    await service.drain_once()
    assert commands.asked == ["c0", "c1", "c2", "c3"]
    assert target.sent == ["c0", "c1", "c2", "c3"]


@pytest.mark.asyncio
async def test_the_batch_size_bounds_the_read():
    rows = tuple(item(command_id=f"c{n}", intake_ref=f"i{n}", guide=f"g{n}") for n in range(5))
    service, pending, _cmds, target, _r = build(rows, batch_size=2)
    await service.drain_once()
    assert pending.limits == [2] and target.sent == ["c0", "c1"]


@pytest.mark.asyncio
async def test_the_report_counts_every_disposition_and_names_the_executed_rows():
    c = command()
    rows = (
        item(command_id="ok"),
        item(command_id="late", intake_ref="i2", guide="g2", authorization_until=NOW - timedelta(seconds=1)),
    )
    service, *_rest = build(rows, receipts=FakeReceipts({"ok": [None, receipt(c)]}))
    report = await service.drain_once()
    assert report.scanned == 2
    assert dict(report.counts()) == {"started": 1, "expired": 1}
    assert report.executed == 1 and report.dispatched == 1 and report.idle is False
    bundle = EvidenceBundle.of(report)
    assert [outcome.command_id for outcome in bundle.executed] == ["ok"]


@pytest.mark.asyncio
async def test_a_sweep_that_dispatched_nothing_is_idle():
    service, *_rest = build((item(lease_until=NOW + timedelta(seconds=10)),))
    assert (await service.drain_once()).idle is True


@pytest.mark.asyncio
async def test_the_loop_stops_after_max_sweeps_and_touches_the_heartbeat_each_time():
    beats: list[int] = []
    service, *_rest = build((item(),))
    reports = await run_dispatch_loop(
        service, poll_interval_s=0.01, max_sweeps=3, heartbeat=lambda: beats.append(1)
    )
    assert len(reports) == 3 and len(beats) == 3


@pytest.mark.asyncio
async def test_the_loop_stops_when_the_stop_event_is_set():
    service, *_rest = build(())
    stop = asyncio.Event()
    stop.set()
    assert await run_dispatch_loop(service, poll_interval_s=0.01, stop_event=stop) == []


@pytest.mark.asyncio
async def test_a_database_failure_in_the_loop_ends_the_process_rather_than_being_swallowed():
    class Broken(FakePending):
        async def pending(self, *, limit: int) -> tuple[PendingDispatch, ...]:
            raise RuntimeError("protected store gone")

    service = IntakeDispatchService(
        pending=Broken(()),
        commands=FakeCommands(),
        target=FakeTarget(),
        receipts=FakeReceipts(),
        clock=lambda: NOW,
    )
    with pytest.raises(RuntimeError):
        await run_dispatch_loop(service, poll_interval_s=0.01, max_sweeps=1)

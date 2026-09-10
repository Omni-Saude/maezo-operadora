"""Real producer/store/admission paths with synthetic source/SQL/native boundaries only."""

import base64
import copy
import json
from contextlib import asynccontextmanager
from datetime import timedelta
from types import SimpleNamespace

import pytest
from tests.unit.gateway.document_requests import test_bridge as f

from maezo.gateway.communications import models as clock_module
from maezo.gateway.document_requests import admission as admission_module
from maezo.gateway.document_requests import postgres as store_module
from maezo.gateway.document_requests import producer as producer_module
from maezo.gateway.document_requests.admission import AdmissionLifetime, SystemAdmission
from maezo.gateway.document_requests.completion import CompletionOutcome
from maezo.gateway.document_requests.models import (
    DeliveryRecord,
    ExistingDelivery,
    PreserveBodyBinding,
    ProducerContext,
    RequestBodyCommand,
    RequestDeliveryCommand,
    RequestInboxReceipt,
    SystemGrant,
    SystemPublication,
    require,
    selector,
)
from maezo.gateway.document_requests.producer import (
    DocumentRequestPolicySource,
    DocumentRequestProducer,
    NoticePlan,
    ProducerJournal,
)
from maezo.gateway.external_cases.models import ExternalCaseError
from maezo.gateway.human.auth_profile import DocumentPolicy, InputPublication, PublicationReceipt
from maezo.gateway.human.read_profile import ArtifactPin, digest, parse_model, wire
from maezo.gateway.intake.native_store import PostgresAuthDispatchStore
from maezo.portal.engine.profile import canonicalize


def receipt_fixture():
    b = f.body()
    delivery = DeliveryRecord(
        recipient_identity_digest=f.HASH,
        sender_identity_digest=b.sender_identity_digest,
        origin_command_id=f.REF,
        origin_command_digest=f.HASH,
        body_command_id=b.command_id,
        message_command_id=b.message_command_id,
        message_request_digest=f.HASH,
        communication_ref=f.REF,
        body_ref=f.REF,
        body_request_digest=f.HASH,
        body_authority_receipt_ref=f.REF,
        body_authority_digest=f.HASH,
        message_authority_receipt_ref=f.REF,
        message_authority_digest=f.HASH,
        notice_request_revision="0",
        notice_policy_digest=f.HASH,
        notice_assessment_version_digest=f.HASH,
        inbox_available_at=f.NOW,
    )
    receipt = RequestInboxReceipt(
        schema="auth-document-request-inbox-receipt.v1",
        command_id=f.REF,
        command_digest=f.HASH,
        request=b.request,
        request_revision="0",
        policy_ref=f.REF,
        policy_digest=f.HASH,
        assessment_version_digest=f.HASH,
        deliveries=(delivery,),
        committed_at=f.NOW,
    )
    raw = f.grant(b).model_dump(by_alias=True)
    raw["access"].update(operation="read_request_receipt", command_id=f.REF, request_digest=f.HASH)
    raw["body_bindings"] = (
        ExistingDelivery(mode="existing", recipient_identity_digest=f.HASH, delivery=delivery).model_dump(),
    )
    return receipt, SystemGrant.model_validate(raw)


@pytest.mark.asyncio
@pytest.mark.parametrize("boundary", ["authorize", "acquire"])
@pytest.mark.parametrize("cap", ["publication", "identity"])
@pytest.mark.parametrize("late", ["not_prepared", "exchange", "acknowledge", "exception"])
async def test_actual_admission_caps_survive_all_final_producer_returns(monkeypatch, boundary, cap, late):
    clock = [f.NOW]
    monkeypatch.setattr(clock_module, "now", lambda: clock[0])
    receipt, grant = receipt_fixture()
    end = f.NOW + timedelta(seconds=1)
    long = f.NOW + timedelta(minutes=1)
    publication = SystemPublication(
        publication_ref=f.REF,
        access=grant.access,
        expected_revision="0",
        source_receipt_ref=grant.source.receipt_ref,
        source_digest=digest(grant.source),
        valid_until=end if cap == "publication" else long,
        grant=grant,
    )
    phase = ["authorize"]
    connection = SimpleNamespace(in_transaction=lambda: True)

    @asynccontextmanager
    async def transaction(engine, seconds):
        yield connection

    async def one(c, sql, params):
        if "authority_head" in sql:
            selected = (
                publication if boundary == phase[0] else publication.model_copy(update={"valid_until": long})
            )
            return dict(payload=canonicalize(wire(selected)), revision="1", publication_ref=f.REF)
        if "FROM portal_document_request.system_identity" in sql:
            return dict(
                revoked=False,
                operations=[grant.access.operation],
                rolsuper=False,
                rolcreaterole=False,
                rolcreatedb=False,
                rolreplication=False,
                rolbypassrls=False,
                identity_payload=canonicalize(wire(grant.access.producer)),
                valid_until=end if cap == "identity" and boundary == phase[0] else long,
            )
        if "has_table_privilege" in sql:
            return dict(writable=False)
        raise AssertionError(sql)

    monkeypatch.setattr(admission_module, "transaction", transaction)
    monkeypatch.setattr(admission_module, "one", one)
    admission = SystemAdmission(object(), grant.access.scope)
    lifetime = AdmissionLifetime()
    actual = await admission.authorize(grant.access, lifetime=lifetime)
    phase[0] = "acquire"
    async with admission.acquire(actual, long, lifetime=lifetime):
        pass
    # A new longer observation cannot renew either retained original cap.
    lifetime.intersect(long)
    events = []

    class Journal:
        def current(self):
            pass

        async def read(self, *args):
            if late == "not_prepared":
                clock[0] = end
                return None
            return SimpleNamespace(
                payload={
                    "body": base64.b64encode(b"original").decode(),
                    "binding": base64.b64encode(b"binding").decode(),
                }
            )

        async def claim_send(self, *args):
            return False

        async def acknowledge(self, *args):
            events.append("committed_receipt_journaled")
            clock[0] = end

    class Completion:
        async def exchange(self, prepared, *, recovery, before_send):
            assert recovery
            before_send()
            events.append("original_completion_lookup")
            if late in {"exchange", "exception"}:
                clock[0] = end
            if late == "exception":
                raise RuntimeError("synthetic uncertain exchange")
            return (
                CompletionOutcome("committed", b"{}")
                if late == "acknowledge"
                else CompletionOutcome("not_observed", None)
            )

    worker = object.__new__(DocumentRequestProducer)
    worker.journal, worker.completion = Journal(), Completion()
    with pytest.raises(ExternalCaseError):
        await worker._finish(f.REF, receipt, actual, None, lifetime)
    if late == "acknowledge":
        assert events == ["original_completion_lookup", "committed_receipt_journaled"]
    # No shared lifetime: a separate current receipt-read invocation remains independent.
    independent = AdmissionLifetime()
    independent.intersect(long)
    independent.current()


class VersionedMetadata(f.MetadataDatabase):
    """Keep the real store's (request, revision) key, unlike the one-version baseline fixture."""

    async def execute(self, sql, p):
        if str(sql).startswith("INSERT INTO portal_document_request.request_definition_revision "):
            self.tables["definition"].setdefault((p["request"], p["revision"]), dict(p))
            return f.Rows([])
        return await super().execute(sql, p)

    async def one(self, c, sql, p):
        if sql.startswith("SELECT stable_definition_digest"):
            row = self.tables["definition"].get((p["request"], p["revision"]))
            return None if row is None else dict(stable_definition_digest=row["stable"])
        return await super().one(c, sql, p)


def make_policy(revision=0, source_revision=1, added=False):
    r = f.request()
    until = f.NOW + timedelta(minutes=5)
    return DocumentPolicy(
        assessment_ref=f.REF,
        resource_kind="case",
        resource_ref=r.case_ref,
        request_ref=r.request_ref,
        request_revision=revision,
        policy=ArtifactPin(artifact_ref="policy", digest=f.HASH),
        policy_revision=0,
        recipient_principal_refs=(f.REF, f.OTHER) if added else (f.REF,),
        required_codes=("required",),
        missing_codes=("required",),
        submitted_response_digest=None,
        effective_document_refs=(),
        document_set_digest=digest([]),
        complete=False,
        source=f.source(until).model_copy(update={"source_revision": source_revision}),
        valid_until=until,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "history",
    [
        "growing",
        "disjoint",
        "overlap_conflict",
        "historical_denied",
        "historical_expired",
        "missing_predecessor",
    ],
)
async def test_public_successors_w4_w5_and_exact_uncertain_recovery_use_real_stores(monkeypatch, history):
    monkeypatch.setattr(clock_module, "now", lambda: f.NOW)
    phi_db, body, content = f.phi(monkeypatch)
    metadata = VersionedMetadata(phi_db.contents)
    inbox = store_module.RequestInboxStore(
        SimpleNamespace(scope=body.request.communication_scope(), acquire=metadata.acquire)
    )
    monkeypatch.setattr(store_module, "one", metadata.one)
    db = f.JournalDatabase()
    protected = PostgresAuthDispatchStore(db, tenant=f.producer().tenant, key_id=f.REF, key=b"j" * 32)
    journal = ProducerJournal(protected, f.producer(), lambda: None)
    monkeypatch.setattr(producer_module, "transaction", db.transaction)
    monkeypatch.setattr(producer_module, "ProducerAcquisition", lambda value: value)
    acquired = object()  # Synthetic selected acquisition seam; no native authority is qualified here.
    policies = [make_policy(), make_policy(source_revision=2), make_policy(1, 3, True)]
    if history != "growing":
        policies[2] = policies[2].model_copy(update={"recipient_principal_refs": (f.OTHER,)})
    policies.extend((make_policy(2, 4, True), make_policy(2, 5, True)))
    all_recipients = (f.recipient(), f.recipient("b" * 64).model_copy(update={"principal_ref": f.OTHER}))
    historical_commands = []
    selected = [0]
    current_policy = [policies[0]]
    publications = {}
    receipts = {}
    source_calls = []

    def observation():
        policy = current_policy[0]
        context = ProducerContext(
            **body.request.model_dump(),
            request_revision=str(policy.request_revision),
            binding_revision="1",
            occurrence_state="bound",
            policy_ref=policy.assessment_ref,
            policy_digest=digest(policy),
        )
        query = canonicalize({"native_observation": str(selected[0])})
        result = canonicalize({"query_digest": producer_module.sha(query), "context": wire(context)})
        return SimpleNamespace(context=context, query=query, result=result, current=lambda: None)

    class Context:
        async def observe(self, value):
            assert value is acquired
            return observation()

    class Source(DocumentRequestPolicySource):
        async def policy(self, observed, original):
            raise AssertionError("successors must not call initial policy")

        async def successor(self, observed, predecessor, invocation_ref, original):
            source_calls.append((invocation_ref, predecessor.command_id))
            policy = policies[selected[0]]
            publication = InputPublication(
                schema="human-auth-input-publication.v1",
                scope=body.request.scope,
                workload_ref=f.REF,
                publication_id=str(selected[0] + 1) * 32,
                kind="document_policy",
                resource_ref=f.REF,
                expected_generation=selected[0],
                source=policy.source,
                state="active",
                payload=policy,
                payload_digest=digest(policy),
                valid_until=policy.valid_until,
            )
            if original is not None:
                assert original == publication
            publications[publication.publication_id] = publication
            return SimpleNamespace(publication=publication)

        async def notice(self, observed, policy, receipt):
            recipients = tuple(
                r for r in all_recipients if r.principal_ref in policy.recipient_principal_refs
            )
            commands = []
            for command_id in historical_commands or [previous[0]]:
                predecessor = await journal.read(command_id, "delivery")
                commands.append(parse_model(RequestDeliveryCommand, predecessor.payload["command"]))
            return NoticePlan(template=body.template, recipients=recipients, prior_commands=tuple(commands))

    class Publisher:
        async def publish(self, snapshot):
            publication = snapshot.publication
            old = current_policy[0]
            new = publication.payload
            # This synthetic native/source boundary enforces W4's stable fields and W5's revision.
            stable = ("policy", "policy_revision", "recipient_principal_refs", "required_codes")
            require(
                new.request_revision > old.request_revision
                or (
                    new.request_revision == old.request_revision
                    and all(getattr(old, k) == getattr(new, k) for k in stable)
                ),
                "conflict",
            )
            current_policy[0] = new
            receipt = PublicationReceipt(
                schema="human-auth-input-receipt.v1",
                scope=body.request.scope,
                publication_id=publication.publication_id,
                request_digest=digest(publication),
                kind="document_policy",
                resource_ref=f.REF,
                previous_generation=publication.expected_generation,
                head_generation=publication.expected_generation + 1,
                state="active",
                payload_digest=digest(new),
                committed_at=f.NOW,
            )
            receipts.setdefault(publication.publication_id, receipt)
            assert receipts[publication.publication_id] == receipt
            return receipt

    class NoCompletion:
        def prepare(self, *args):
            raise AssertionError("successor cannot allocate native completion")

        async def exchange(self, *args, **kwargs):
            raise AssertionError("successor cannot resend original completion")

    worker = object.__new__(DocumentRequestProducer)
    worker.producer, worker.journal, worker.context, worker.source = (
        f.producer(),
        journal,
        Context(),
        Source(),
    )
    worker.policy_publisher, worker.content, worker.inbox, worker.completion = (
        Publisher(),
        content,
        inbox,
        NoCompletion(),
    )
    grant_calls = []

    async def admitted(access, *, phi, lifetime):
        grant_calls.append(access.operation)
        policy = current_policy[0]
        recipients = tuple(r for r in all_recipients if r.principal_ref in policy.recipient_principal_refs)
        if access.operation in {"preserve_request_body", "read_request_body_receipt"}:
            candidates = [
                journal._decode(row, journal._params(row["command"], row["kind"])).payload
                for row in db.rows.values()
                if row["kind"].startswith("body-") and not row["kind"].endswith("-receipt")
            ]
            b = next(
                parse_model(RequestBodyCommand, x["command"])
                for x in candidates
                if x["command"]["command_id"] == access.command_id
            )
            bindings = (
                (selector(b),)
                if access.operation == "read_request_body_receipt"
                else (
                    PreserveBodyBinding(
                        recipient_identity_digest=b.recipient_identity_digest,
                        outer_command_id=b.outer_command_id,
                        body_command_id=b.command_id,
                        message_command_id=b.message_command_id,
                        body_ref=None,
                        body_request_digest=digest(b),
                    ),
                )
            )
        else:
            command = parse_model(
                RequestDeliveryCommand, (await journal.read(access.command_id, "delivery")).payload["command"]
            )
            if access.operation == "publish_request_inbox":
                bindings = command.deliveries
            else:
                original = parse_model(
                    RequestInboxReceipt,
                    json.loads(metadata.tables["command"][access.command_id]["receipt"]),
                )
                bindings = tuple(
                    ExistingDelivery(
                        mode="existing", recipient_identity_digest=r.recipient_identity_digest, delivery=r
                    )
                    for r in original.deliveries
                )
                recipients = tuple(
                    r
                    for r in all_recipients
                    if r.identity_digest in {e.recipient_identity_digest for e in bindings}
                )
        grant = SystemGrant(
            access=access,
            authority_receipt_ref=f.REF,
            authority_digest=f.HASH,
            source=policy.source,
            valid_until=policy.valid_until,
            policy_valid_until=policy.valid_until,
            template=body.template,
            recipients=recipients,
            body_bindings=bindings,
        )
        lifetime.intersect(grant.ceiling())
        return grant

    worker._grant = admitted
    # Seed initial work through the real planner/delivery/stores, without a native completion.
    previous = [f.REF]
    initial = InputPublication(
        schema="human-auth-input-publication.v1",
        scope=body.request.scope,
        workload_ref=f.REF,
        publication_id="1" * 32,
        kind="document_policy",
        resource_ref=f.REF,
        expected_generation=0,
        source=policies[0].source,
        state="active",
        payload=policies[0],
        payload_digest=digest(policies[0]),
        valid_until=policies[0].valid_until,
    )

    async def initial_notice(observed, policy, receipt):
        return NoticePlan(template=body.template, recipients=(f.recipient(),), prior_commands=())

    old_notice = worker.source.notice
    worker.source.notice = initial_notice
    first = await worker._plan(f.REF, observation(), acquired, SimpleNamespace(publication=initial), None)
    worker.source.notice = old_notice
    original_delivery = first.inbox.deliveries[0]
    await journal.seal(f.REF, "completion", {"body": "b3JpZ2luYWw=", "binding": "YmluZGluZw=="})
    await journal.claim_send(f.REF, "completion")
    original_rows = copy.deepcopy(db.rows)
    assert len(phi_db.contents) == len(metadata.tables["message"]) == 1
    # W4: changed assessment/source at the SAME request revision, no second notice.
    selected[0] = 1
    second = await worker.successor(acquired, f.REF, "assessment_refresh_0001")
    assert second.inbox.request_revision == first.inbox.request_revision == "0"
    assert second.inbox.assessment_version_digest != first.inbox.assessment_version_digest
    assert second.inbox.deliveries == (original_delivery,)
    assert len(phi_db.contents) == len(metadata.tables["message"]) == 1
    retry = await worker.successor(acquired, f.REF, "assessment_refresh_0001")
    assert retry.inbox == second.inbox and len(source_calls) == 1
    assert (
        await worker.recover_successor(body.request.request_ref, "assessment_refresh_0001")
    ).inbox == second.inbox
    # W5: next authorized revision adds only B's FIRST notice, A keeps old authority/version.
    previous[0] = second.inbox.command_id
    selected[0] = 2
    third = await worker.successor(acquired, previous[0], "recipient_addition_0002")
    assert third.inbox.request_revision == "1"
    if history == "growing":
        assert third.inbox.deliveries[0] == original_delivery
    else:
        assert len(third.inbox.deliveries) == 1
        assert third.inbox.deliveries[0].recipient_identity_digest == "b" * 64
    assert len(phi_db.contents) == len(metadata.tables["message"]) == 2
    assert third.inbox.deliveries[-1].notice_request_revision == "1"
    assert len(metadata.tables["definition"]) == 2 and len(metadata.tables["version"]) == 3
    assert (
        await worker.recover_successor(body.request.request_ref, "recipient_addition_0002")
    ).inbox == third.inbox
    assert all(db.rows[key] == value for key, value in original_rows.items())
    assert (await journal.read(f.REF, "completion")).state == "possibly_sent"
    assert all(x.completion.status == "not_prepared" for x in (first, second, third))
    assert "read_request_receipt" in grant_calls and grant_calls.count("preserve_request_body") == 2
    if history != "growing":
        # O1[A], O2[B], then a separately authorized O3[A+B]. No single
        # predecessor covers both immutable notices; each historical read is current.
        previous[0] = third.inbox.command_id
        historical_commands[:] = [first.inbox.command_id, third.inbox.command_id]
        selected[0] = 3
        before_union = copy.deepcopy(metadata.tables)
        before_bodies = copy.deepcopy(phi_db.contents)
        before_read = worker._read_inbox
        historical_reads = []

        async def read_history(command, lifetime):
            historical_reads.append(command.command_id)
            if command.command_id == first.inbox.command_id:
                if history == "historical_denied":
                    raise ExternalCaseError("forbidden")
                if history == "historical_expired":
                    lifetime.intersect(f.NOW)
            return await before_read(command, lifetime)

        worker._read_inbox = read_history
        if history == "missing_predecessor":
            historical_commands[:] = [first.inbox.command_id]
        if history in {"historical_denied", "historical_expired", "missing_predecessor"}:
            with pytest.raises(ExternalCaseError):
                await worker.successor(acquired, previous[0], "disjoint_union_denied_06")
            assert metadata.tables == before_union and phi_db.contents == before_bodies
            assert (await journal.read(f.REF, "completion")).state == "possibly_sent"
            return
        union = await worker.successor(acquired, previous[0], "disjoint_union_0006")
        assert first.inbox.command_id in historical_reads
        assert third.inbox.command_id in historical_reads
        assert union.inbox.deliveries == (original_delivery, third.inbox.deliveries[0])
        assert all(
            isinstance(x, ExistingDelivery)
            for x in parse_model(
                RequestDeliveryCommand,
                (await journal.read(union.inbox.command_id, "delivery")).payload["command"],
            ).deliveries
        )
        assert phi_db.contents == before_bodies
        for table in ("message", "delivery"):
            assert metadata.tables[table] == before_union[table]
        # O4 selects overlapping histories A, B and A+B. Exact duplicate records
        # are one notice each, never an invitation to create replacement messages.
        previous[0] = union.inbox.command_id
        historical_commands.append(union.inbox.command_id)
        selected[0] = 4
        before_overlap = copy.deepcopy(metadata.tables)
        if history == "overlap_conflict":
            original_row = metadata.tables["command"][union.inbox.command_id]
            tampered = json.loads(original_row["receipt"])
            tampered["deliveries"][0]["notice_policy_digest"] = "c" * 64
            original_row["receipt"] = canonicalize(tampered)
            before_overlap = copy.deepcopy(metadata.tables)
            with pytest.raises(ExternalCaseError):
                await worker.successor(acquired, previous[0], "conflicting_history_07")
            assert metadata.tables == before_overlap and phi_db.contents == before_bodies
            return
        overlap = await worker.successor(acquired, previous[0], "overlapping_history_07")
        assert overlap.inbox.deliveries == union.inbox.deliveries
        assert phi_db.contents == before_bodies
        for table in ("message", "delivery"):
            assert metadata.tables[table] == before_overlap[table]
        assert (
            await worker.recover_successor(body.request.request_ref, "overlapping_history_07")
        ).inbox == overlap.inbox
        assert all(db.rows[key] == value for key, value in original_rows.items())
        assert (await journal.read(f.REF, "completion")).state == "possibly_sent"
        third = overlap
        historical_commands.append(overlap.inbox.command_id)

    # Unknown predecessor or conflicting invocation never yields a replacement publication.
    count = len(publications)
    with pytest.raises(ExternalCaseError):
        await worker.successor(acquired, "z" * 32, "unknown_predecessor_01")
    with pytest.raises(ExternalCaseError):
        await worker.successor(acquired, third.inbox.command_id, "assessment_refresh_0001")
    assert len(publications) == count

    # A separately acknowledged W3/W4 successor can lose its outer ACK without
    # clearing either that command or the predecessor's uncertain completion.
    previous[0] = third.inbox.command_id
    original_publish = inbox.publish
    published = []

    async def commit_then_lose(*args, **kwargs):
        value = await original_publish(*args, **kwargs)
        published.append(value)
        raise ExternalCaseError("uncertain")

    inbox.publish = commit_then_lose
    with pytest.raises(ExternalCaseError):
        await worker.successor(acquired, previous[0], "successor_lost_ack_0003")
    inbox.publish = original_publish
    before_recovery = copy.deepcopy(metadata.tables)
    recovered = await worker.recover_successor(body.request.request_ref, "successor_lost_ack_0003")
    assert recovered.inbox == published[0] and metadata.tables == before_recovery
    assert recovered.inbox.deliveries == third.inbox.deliveries
    assert len(phi_db.contents) == 2
    assert (await journal.read(f.REF, "completion")).state == "possibly_sent"

    # Historical read denial refuses before source successor selection or seal.
    original_grant = worker._grant

    async def denied(access, *, phi, lifetime):
        raise ExternalCaseError("forbidden")

    worker._grant = denied
    before_denial = copy.deepcopy(db.rows)
    calls_before_denial = len(source_calls)
    with pytest.raises(ExternalCaseError):
        await worker.successor(acquired, previous[0], "unauthorized_successor_04")
    assert db.rows == before_denial and len(source_calls) == calls_before_denial
    worker._grant = original_grant

    # Source absence must stay unavailable: initial policy is not a fallback.
    worker.source.successor = DocumentRequestPolicySource.successor.__get__(worker.source)
    with pytest.raises(ExternalCaseError):
        await worker.successor(acquired, previous[0], "source_unavailable_0005")
    assert metadata.tables == before_recovery and len(phi_db.contents) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "method,args",
    [
        ("run", (object(),)),
        ("recover", (f.REF,)),
        ("successor", (object(), f.REF, f.OTHER)),
        ("recover_successor", (f.REF, f.OTHER)),
    ],
)
async def test_actual_installed_wrapper_keeps_original_deadline_after_public_handoff(
    monkeypatch, method, args
):
    from maezo.gateway.document_requests import composition
    from maezo.gateway.document_requests.producer import ProducerResult

    clock = [f.NOW]
    monkeypatch.setattr(clock_module, "now", lambda: clock[0])
    receipt, grant = receipt_fixture()
    result = ProducerResult(receipt, CompletionOutcome("not_prepared", None), f.NOW + timedelta(seconds=1))
    events = []

    async def roles(placement):
        events.append("actual_role_verification_seam")

    async def dispatch(*values):
        assert values == args
        events.append("worker_returned_original_result")
        return result

    def placement_live():
        events.append("placement_callback_after_await")
        clock[0] += timedelta(seconds=2)

    monkeypatch.setattr(composition, "verify_database_roles", roles)
    worker = SimpleNamespace(**{method: dispatch})
    installed = composition.InstalledProducer(worker, SimpleNamespace(live=placement_live))
    with pytest.raises(ExternalCaseError):
        await getattr(installed, method)(*args)
    assert events == [
        "actual_role_verification_seam",
        "worker_returned_original_result",
        "placement_callback_after_await",
    ]
    clock[0] = f.NOW
    object.__setattr__(installed, "placement", SimpleNamespace(live=lambda: None))
    assert await getattr(installed, method)(*args) is result

"""Offline dispatch failure/race seams. Native uniqueness requires separate engine proof."""

import copy
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import timedelta
from types import SimpleNamespace

import pytest

from maezo.gateway.human.auth_profile import HumanReceiptQuery, NativeReceiptLookup
from maezo.gateway.human.auth_transport import AuthUnavailableError
from maezo.gateway.human.read_profile import digest
from maezo.gateway.intake.native_dispatch import AuthDispatcher, AuthEffectLease
from maezo.gateway.intake.native_store import DispatchClaim, PostgresAuthDispatchStore
from tests.unit.gateway.intake.native.test_wire_transport import NOW, command, effect_cap, receipt


async def checkpoint():
    return None


class SQLSeam:
    def __init__(self, row, clock):
        self.row = row
        self.clock = clock
        self.fail_ack = False
        self.expire_after_update = False
        self.expire_at_release = False
        self.events = []

    @asynccontextmanager
    async def transaction(self, engine, seconds):
        old = copy.deepcopy(self.row)
        try:
            yield self
        except BaseException:
            self.row = old
            raise
        if self.expire_at_release:
            self.clock[0] += timedelta(seconds=30)
        if self.fail_ack:
            raise RuntimeError("PRIVATE_COMMIT_CANARY")

    async def execute(self, sql, values):
        query = str(sql)
        self.events.append(query)
        if query.startswith("UPDATE portal_intake.intake"):
            return SimpleNamespace()
        if query.startswith("UPDATE"):
            assert self.row["revision"] == values["revision"]
            if "generation=generation+1" in query:
                self.row.update(
                    generation=self.row["generation"] + 1,
                    revision=self.row["revision"] + 1,
                    owner_digest=values["owner"],
                    lease_until=values["until"],
                    state=values["state"],
                )
            else:
                self.row.update(state="sending", revision=self.row["revision"] + 1)
            if self.expire_after_update:
                self.clock[0] += timedelta(seconds=30)
        return SimpleNamespace(mappings=lambda: SimpleNamespace(one=lambda: copy.deepcopy(self.row)))


def sql_setup(monkeypatch, state="admitted"):
    now = [NOW]
    store = PostgresAuthDispatchStore(
        None, tenant="tenant", key_id="key", key=b"k" * 32, clock=lambda: now[0]
    )
    c = command()
    nonce, ciphertext = store.seal("command", c.command_id, c)
    row = dict(
        command_id=c.command_id,
        operation="auth.start",
        command_digest=digest(c),
        command_nonce=nonce,
        command_ciphertext=ciphertext,
        key_id="key",
        state=state,
        generation=0,
        revision=0,
        owner_digest=None,
        lease_until=None,
        authorization_until=NOW + timedelta(seconds=60),
    )
    db = SQLSeam(row, now)
    monkeypatch.setattr("maezo.gateway.intake.native_store.transaction", db.transaction)
    return store, db, now


@pytest.mark.asyncio
async def test_claim_live_owner_blocks_competing_sender(monkeypatch):
    store, db, _ = sql_setup(monkeypatch)
    claim = await store.claim("command")
    assert claim.generation == 1 and not claim.reconcile_first
    with pytest.raises(AuthUnavailableError):
        await store.claim("command")
    sending = await store.mark_sending(claim, lambda: None, checkpoint=checkpoint)
    assert sending.revision == 2 and db.row["state"] == "sending"
    with pytest.raises(AuthUnavailableError):
        await store.mark_sending(claim, lambda: None, checkpoint=checkpoint)


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["sending", "reconciling"])
async def test_expired_possible_send_reclaims_only_reconciling(monkeypatch, state):
    store, db, _ = sql_setup(monkeypatch, state)
    claim = await store.claim("command")
    assert claim.reconcile_first and db.row["state"] == "reconciling"
    assert digest(claim.command) == db.row["command_digest"]


@pytest.mark.asyncio
async def test_lost_marker_ack_preserves_possible_send_and_same_command(monkeypatch):
    store, db, now = sql_setup(monkeypatch)
    claim = await store.claim("command")
    db.fail_ack = True
    with pytest.raises(AuthUnavailableError) as error:
        await store.mark_sending(claim, lambda: None, checkpoint=checkpoint)
    assert "PRIVATE" not in str(error.value) and db.row["state"] == "sending"
    db.fail_ack = False
    now[0] += timedelta(seconds=16)
    next_claim = await store.claim("command")
    assert next_claim.generation == 2 and next_claim.reconcile_first and next_claim.command == claim.command


@pytest.mark.asyncio
async def test_expiry_after_last_marker_sql_rolls_back(monkeypatch):
    store, db, _ = sql_setup(monkeypatch)
    claim = await store.claim("command")
    db.expire_after_update = True
    with pytest.raises(AuthUnavailableError):
        await store.mark_sending(claim, lambda: None, checkpoint=checkpoint)
    assert db.row["state"] == "claimed"


@pytest.mark.asyncio
async def test_expiry_during_marker_commit_never_emits(monkeypatch):
    store, db, _ = sql_setup(monkeypatch)
    claim = await store.claim("command")
    db.expire_at_release = True
    with pytest.raises(AuthUnavailableError):
        await store.mark_sending(claim, lambda: None, checkpoint=checkpoint)
    assert db.row["state"] == "sending"  # committed uncertainty, not permission to send


@pytest.mark.asyncio
async def test_stale_generation_cannot_begin_send(monkeypatch):
    store, db, now = sql_setup(monkeypatch)
    old = await store.claim("command")
    now[0] += timedelta(seconds=16)
    new = await store.claim("command")
    with pytest.raises(AuthUnavailableError):
        await store.mark_sending(old, lambda: None, checkpoint=checkpoint)
    assert db.row["generation"] == new.generation and db.row["state"] == "claimed"


class DispatchStore:
    def __init__(self, c, reconcile):
        self.c = c
        self.reconcile_first = reconcile
        self.events = []
        self.result = None

    async def prove(self, c):
        assert c == self.c
        self.events.append("prove")

    async def completed(self, c):
        return self.result

    async def claim(self, command_id):
        self.events.append("claim")
        return DispatchClaim(self.c, 1, 1, "owner", NOW + timedelta(seconds=15), self.reconcile_first)

    async def mark_sending(self, claim, current, *, checkpoint):
        await checkpoint()
        current()
        self.events.append("sending")
        return replace(claim, revision=2)

    async def reconcile(self, c, r):
        self.events.append("reconcile")
        self.result = r


class Authority:
    def __init__(self):
        self.deny_effect = False
        self.deny_read = False

    async def current(self, c, *, read, caller=None):
        if (read and self.deny_read) or (not read and self.deny_effect):
            raise AuthUnavailableError()
        return AuthEffectLease(
            c.scope,
            c.actor,
            digest(c),
            "auth.receipt.read" if read else "auth.start",
            c.intake_ref,
            NOW + timedelta(seconds=60),
            lambda: None,
            checkpoint,
            None if read else effect_cap(c),
        )


class Client:
    def __init__(self, c, existing=None):
        self.c = c
        self.existing = existing
        self.sent = []
        self.fail = False

    async def execute(self, request, **kwargs):
        self.sent.append(request)
        if self.fail:
            raise AuthUnavailableError()
        if isinstance(request, HumanReceiptQuery):
            return NativeReceiptLookup(
                schema="human-auth-receipt-lookup.v1",
                scope=request.scope,
                query_id=request.query_id,
                query_digest=digest(request),
                observed_at=NOW,
                status="committed" if self.existing else "absent",
                receipt=self.existing,
            )
        assert request == self.c
        return receipt(self.c)


def dispatch_setup(reconcile=True, existing=None):
    c = command()
    s = DispatchStore(c, reconcile)
    client = Client(c, existing)
    authority = Authority()
    return (
        c,
        s,
        client,
        authority,
        AuthDispatcher(store=s, client=client, authority=authority, clock=lambda: NOW),
    )


@pytest.mark.asyncio
async def test_uncertain_dispatch_queries_before_identical_replay():
    c, s, client, _, d = dispatch_setup()
    result = await d._dispatch(c)
    assert isinstance(client.sent[0], HumanReceiptQuery) and client.sent[1] == c
    assert s.events == ["prove", "claim", "sending", "reconcile"] and result == receipt(c)


@pytest.mark.asyncio
async def test_committed_receipt_recovery_never_emits_effect():
    c, s, client, authority, d = dispatch_setup(existing=receipt())
    authority.deny_effect = True
    assert await d._dispatch(c) == receipt(c)
    assert len(client.sent) == 1 and "sending" not in s.events


@pytest.mark.asyncio
async def test_absence_does_not_renew_changed_effect_authority():
    c, s, client, authority, d = dispatch_setup()
    authority.deny_effect = True
    with pytest.raises(AuthUnavailableError):
        await d._dispatch(c)
    assert len(client.sent) == 1 and s.events == ["prove", "claim"]


@pytest.mark.asyncio
async def test_receipt_query_failure_never_replays():
    c, s, client, _, d = dispatch_setup()
    client.fail = True
    with pytest.raises(AuthUnavailableError):
        await d._dispatch(c)
    assert len(client.sent) == 1 and "sending" not in s.events


@pytest.mark.asyncio
async def test_completed_gateway_replay_requires_current_read_and_no_native_effect():
    c, s, client, authority, d = dispatch_setup(False)
    assert await d._dispatch(c) == receipt(c)
    assert await d._dispatch(c) == receipt(c)
    assert client.sent == [c]
    authority.deny_read = True
    with pytest.raises(AuthUnavailableError):
        await d._dispatch(c)
    assert client.sent == [c]


@pytest.mark.asyncio
async def test_authentic_receipt_is_persisted_before_reader_disclosure_is_denied():
    c, store, client, authority, dispatcher = dispatch_setup(False)
    authority.deny_read = True
    with pytest.raises(AuthUnavailableError):
        await dispatcher._dispatch(c)
    assert client.sent == [c]
    assert store.result == receipt(c)
    assert store.events == ["prove", "claim", "sending", "reconcile"]


@pytest.mark.asyncio
async def test_original_admission_ceiling_cannot_be_renewed_by_dispatch_claim(monkeypatch):
    store, db, now = sql_setup(monkeypatch)
    claim = await store.claim("command")
    db.row["authorization_until"] = NOW + timedelta(seconds=1)
    now[0] += timedelta(seconds=2)
    with pytest.raises(AuthUnavailableError):
        await store.mark_sending(claim, lambda: None, checkpoint=checkpoint)
    assert db.row["state"] == "claimed"


@pytest.mark.asyncio
async def test_transactional_intake_native_outbox_failure_rolls_back_original_admission(monkeypatch):
    from maezo.gateway.intake.models import IntakeError
    from maezo.gateway.intake.postgres import PostgresIntakeStore
    from tests.unit.gateway.intake.test_postgres import Database, inputs

    class DB(Database):
        async def execute(self, sql, values):
            if "INSERT INTO portal_intake.native_outbox" in str(sql):
                raise RuntimeError("PRIVATE_OUTBOX_FAILURE")
            return await super().execute(sql, values)

    db = DB()
    monkeypatch.setattr("maezo.gateway.intake.postgres.transaction", db.transaction)
    native = PostgresAuthDispatchStore(None, tenant="synthetic-tenant", key_id="native-key", key=b"k" * 32)
    store = PostgresIntakeStore(
        None, tenant="synthetic-tenant", key_id="intake-key", encryption_key=b"i" * 32, native_dispatch=native
    )
    with pytest.raises(IntakeError):
        await store.admit(*inputs())
    assert db.rows == db.events == []


@pytest.mark.asyncio
async def test_start_chokepoint_refuses_human_provenance_on_agent_transport():
    from maezo.gateway.intake.native_dispatch import HumanIntakeProvenance
    from maezo.tools.mcp_cibseven.transport import start_process_idempotent

    c = command()
    provenance = HumanIntakeProvenance(
        kind="human_intake",
        scope=c.scope,
        actor=c.actor,
        intake_ref=c.intake_ref,
        command_id=c.command_id,
        admission=c.admission,
        guide_identity_ref=c.guide_identity_ref,
        definition=c.definition,
        input_pins=c.input_pins,
        start_facts_ref=c.start_facts_ref,
        start_facts_digest=c.start_facts_digest,
        projected_variables_digest=c.projected_variables_digest,
    )

    class Agent:
        async def find_active_instance(self, **kwargs):
            raise AssertionError("legacy lookup called")

        async def start_process_instance(self, **kwargs):
            raise AssertionError("legacy effect called")

    with pytest.raises(AuthUnavailableError):
        await start_process_idempotent(
            Agent(),
            process_key="SP-OP-AUTH-001",
            # WP-J1-11: chave CONTRATUAL. A recusa aqui e' pela PROVENIENCIA/transporte
            # (proveniencia humana num transporte de agente), nao pela forma da chave — usar a
            # chave legada aqui mascarava qual das duas guardas estava provando o quê.
            business_key="AUTH-tenant-GUIA-001",
            variables={},
            audit_sink=object(),
            provenance=provenance,
        )


@pytest.mark.asyncio
async def test_real_human_start_chokepoint_uses_durable_dispatch_and_receipt_only():
    from maezo.gateway.intake.native_dispatch import HumanIntakeProvenance, HumanIntakeStartTransport
    from maezo.tools.mcp_cibseven.transport import StartOutcome, start_process_idempotent

    c, s, client, _, d = dispatch_setup(False)
    transport = HumanIntakeStartTransport(
        dispatcher=d, workload_ref=c.workload_ref, projection=lambda values: c.projected_variables_digest
    )
    provenance = HumanIntakeProvenance(
        kind="human_intake",
        scope=c.scope,
        actor=c.actor,
        intake_ref=c.intake_ref,
        command_id=c.command_id,
        admission=c.admission,
        guide_identity_ref=c.guide_identity_ref,
        definition=c.definition,
        input_pins=c.input_pins,
        start_facts_ref=c.start_facts_ref,
        start_facts_digest=c.start_facts_digest,
        projected_variables_digest=c.projected_variables_digest,
    )
    result = await start_process_idempotent(
        transport,
        process_key="SP-OP-AUTH-001",
        # WP-J1-11 / decisao do dono #16: o canal do portal usa a MESMA chave contratual do
        # canal de agente (`AUTH-{tenant_id}-{numero_guia_tiss}`). Antes era
        # `"AUTHI-" + guide_identity_ref` — um SEGUNDO dominio de idempotencia por guia.
        business_key="AUTH-tenant-GUIA-001",
        variables={},
        audit_sink=s,
        provenance=provenance,
    )
    assert (
        result.instance_id == "instance"
        and result.state == "UNKNOWN"
        and result.start_outcome == StartOutcome.STARTED
    )
    assert client.sent == [c] and s.events == ["prove", "prove", "claim", "sending", "reconcile"]

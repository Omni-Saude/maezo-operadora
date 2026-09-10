"""Synthetic SQL/session/source seams; no PostgreSQL or authority qualification."""

import copy
import hashlib
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from maezo.gateway.intake.models import AdmissionGrant, IntakeError, request_bytes
from maezo.gateway.intake.native_composition import AuthIntakeComponents
from maezo.gateway.intake.native_store import PostgresAuthDispatchStore
from maezo.gateway.intake.postgres import PostgresIntakeStore
from maezo.gateway.intake.recovery import IntakeRecoveryService
from maezo.gateway.intake.recovery_composition import compose_intake_recovery
from maezo.gateway.intake.recovery_store import PostgresIntakeRecoveryStore, RecoveryScope
from maezo.portal.contracts.intake import AuthIntakeSubmission
from maezo.portal.contracts.models import HumanPrincipal

NOW = datetime.now(UTC)
PRINCIPAL = HumanPrincipal(
    schema_version=1,
    principal_ref="test-principal",
    issuer="https://idp.test",
    subject="test-subject",
    tenant="test-tenant",
    membership_revision=1,
    memberships=(),
    session_ref="original-session",
    authenticated_at=NOW,
    subject_bindings=(),
)
REF, OTHER = "a" * 32, "b" * 32


class SQL:
    def __init__(self, clock):
        self.clock = clock
        self.intakes, self.events, self.outbox, self.identities, self.cursors = {}, {}, {}, {}, {}
        self.queries = []
        self.lose_ack = False
        self.on_release = lambda: None

    @asynccontextmanager
    async def transaction(self, engine, seconds):
        before = copy.deepcopy((self.intakes, self.events, self.outbox, self.identities, self.cursors))
        try:
            yield self
        except BaseException:
            self.intakes, self.events, self.outbox, self.identities, self.cursors = before
            raise
        self.on_release()
        if self.lose_ack:
            self.lose_ack = False
            raise RuntimeError("PRIVATE_LOST_COMMIT_ACK")

    async def execute(self, statement, values=None):
        query, v = str(statement), values or {}
        self.queries.append((query, dict(v)))
        rows = []
        scalar = None
        if query == "SELECT clock_timestamp()":
            scalar = self.clock[0]
        elif "SELECT i.tenant" in query:
            for i in self.intakes.values():
                if i["tenant"] != v["tenant"] or i["principal_ref"] != v["principal"]:
                    continue
                if "command" in v and i["command_id"] != v["command"]:
                    continue
                if "upper" in v and i["created_at"] > v["upper"]:
                    continue
                if "after_created" in v and (i["created_at"], i["intake_ref"]) >= (
                    v["after_created"],
                    v["after_intake"],
                ):
                    continue
                key = (i["tenant"], i["command_id"])
                event, outbox, ident = (
                    self.events.get(key, {}),
                    self.outbox.get(key, {}),
                    self.identities.get(key, {}),
                )
                rows.append(
                    dict(
                        i,
                        event_command=event.get("command"),
                        event_principal=event.get("principal"),
                        event_digest=event.get("digest"),
                        native_operation=outbox.get("operation"),
                        native_command=outbox.get("command"),
                        native_principal=outbox.get("principal"),
                        native_admission=outbox.get("admission"),
                        native_resource=outbox.get("resource"),
                        identity_key=ident.get("key"),
                        identity_nonce=ident.get("nonce"),
                        identity_ciphertext=ident.get("ciphertext"),
                    )
                )
            rows.sort(key=lambda row: (row["created_at"], row["intake_ref"]), reverse=True)
            rows = rows[:51] if "LIMIT 51" in query else rows
        elif "FROM portal_intake.recovery_cursor" in query:
            row = self.cursors.get(v["cursor"])
            if row and row["tenant"] == v["tenant"] and row["environment"] == v["environment"]:
                rows = [dict(row)]
        elif "INSERT INTO portal_intake.recovery_cursor" in query:
            self.cursors[v["cursor"]] = dict(
                tenant=v["tenant"],
                environment=v["environment"],
                actor_digest=v["actor"],
                membership_revision=v["membership"],
                page_size=v["page_size"],
                upper_bound=v["upper"],
                after_created_at=v["after_created"],
                after_intake_ref=v["after_intake"],
                valid_until=v["until"],
            )
        elif "SELECT * FROM portal_intake.intake" in query:
            rows = [
                r
                for r in self.intakes.values()
                if r["tenant"] == v["tenant"]
                and r["principal_ref"] == v["principal"]
                and r["command_id"] == v["command"]
            ]
        elif "SELECT intake_ref FROM portal_intake.intake" in query:
            rows = [
                r
                for r in self.intakes.values()
                if r["tenant"] == v["tenant"] and r["guide_identity_ref"] == v["guide"]
            ]
        elif "INSERT INTO portal_intake.intake " in query:
            self.intakes[(v["tenant"], v["command"])] = dict(
                tenant=v["tenant"],
                principal_ref=v["principal"],
                command_id=v["command"],
                intake_ref=v["intake"],
                guide_identity_ref=v["guide"],
                request_digest=v["digest"],
                created_at=self.clock[0],
                revision=0,
                disposition="admitted",
                case_ref=None,
                start_receipt_ref=None,
            )
        elif "INSERT INTO portal_intake.admission_event" in query:
            self.events[(v["tenant"], v["command"])] = dict(v)
        elif "portal_intake.native_outbox(" in query:
            self.outbox[(v["tenant"], v["command"])] = dict(v, operation="auth.start")
        elif "portal_intake.native_identity(" in query:
            self.identities[(v["tenant"], v["command"])] = dict(v)
        elif "pg_advisory" not in query and "portal_intake.native_audit(" not in query:
            raise AssertionError(query)
        return SimpleNamespace(
            mappings=lambda: SimpleNamespace(one_or_none=lambda: rows[0] if rows else None, all=lambda: rows),
            scalar_one=lambda: scalar,
            first=lambda: rows[0] if rows else None,
        )


class Sessions:
    def __init__(self, principal):
        self.principal = principal
        self.settings = SimpleNamespace(tenant=principal.tenant)
        self.until = NOW + timedelta(minutes=1)
        self.calls = 0
        self.on_resolve = lambda: None

    async def resolve(self, secret):
        self.calls += 1
        self.on_resolve()
        if secret != "s" * 43:
            raise ValueError("PRIVATE_SESSION")
        return SimpleNamespace(
            principal=self.principal,
            record=SimpleNamespace(expires_at=self.until),
            membership=SimpleNamespace(reviewed_until=self.until),
        )


class ReadAuthority:
    def __init__(self):
        self.until = NOW + timedelta(seconds=30)
        self.calls = []
        self.on_read = lambda: None
        self.deny = set()

    async def read(self, principal, intake_ref):
        self.calls.append((principal, intake_ref))
        self.on_read()
        if intake_ref in self.deny:
            raise IntakeError("operation_forbidden")
        return self.until


@pytest.fixture
def setup(monkeypatch):
    clock = [NOW]
    db = SQL(clock)
    engine = SimpleNamespace()
    native = PostgresAuthDispatchStore(engine, tenant=PRINCIPAL.tenant, key_id="native-key", key=b"x" * 32)
    intake = PostgresIntakeStore(
        engine, tenant=PRINCIPAL.tenant, key_id="intake-key", encryption_key=b"y" * 32, native_dispatch=native
    )
    store = PostgresIntakeRecoveryStore(
        intake, native, RecoveryScope(tenant=PRINCIPAL.tenant, environment="test"), lambda: clock[0]
    )
    sessions, authority = Sessions(PRINCIPAL), ReadAuthority()
    service = IntakeRecoveryService(sessions, authority, store, clock=lambda: clock[0])
    monkeypatch.setattr("maezo.gateway.intake.recovery_store.transaction", db.transaction)
    monkeypatch.setattr("maezo.gateway.intake.postgres.transaction", db.transaction)
    return SimpleNamespace(
        db=db,
        native=native,
        intake=intake,
        store=store,
        sessions=sessions,
        authority=authority,
        service=service,
        clock=clock,
    )


def add(h, index=0, *, principal=PRINCIPAL, state="admitted"):
    command, intake_ref = f"{index:032x}", f"{index + 1000:032x}"
    key = (principal.tenant, command)
    h.db.intakes[key] = dict(
        tenant=principal.tenant,
        principal_ref=principal.principal_ref,
        command_id=command,
        intake_ref=intake_ref,
        request_digest="a" * 64,
        created_at=NOW - timedelta(seconds=index),
        disposition=state,
    )
    h.db.events[key] = dict(command=command, principal=principal.principal_ref, digest="a" * 64)
    h.db.outbox[key] = dict(
        operation="auth.start",
        command=command,
        principal=principal.principal_ref,
        admission=intake_ref,
        resource=intake_ref,
        digest="b" * 64,
    )
    nonce, cipher = h.native.seal("identity", command, principal)
    h.db.identities[key] = dict(key="native-key", nonce=nonce, ciphertext=cipher)
    return command, intake_ref


async def discover(h, cursor=None):
    return await h.service.discover_frozen("s" * 43, cursor, freeze=lambda value: value)


async def observe(h, command):
    return await h.service.observe_frozen("s" * 43, command, freeze=lambda value: value)


@pytest.mark.asyncio
async def test_real_admission_commit_lost_ack_is_recoverable_without_payload(setup):
    h = setup
    request = AuthIntakeSubmission(
        command_id=REF,
        beneficiary_ref=REF,
        provider_ref=REF,
        guide_ref=REF,
        codigo_procedimento_tuss="PRIVATE_CLINICAL_CANARY",
        categoria_procedimento="consulta",
        carater_atendimento="eletivo",
        valor_estimado_centavos="999",
        document_refs=(),
    )
    grant = AdmissionGrant(
        principal=PRINCIPAL,
        request_digest=hashlib.sha256(request_bytes(request)).hexdigest(),
        guide_identity_ref=OTHER,
        authority_receipt_ref=OTHER,
        authority_digest="a" * 64,
        valid_until=NOW + timedelta(minutes=1),
    )
    h.db.lose_ack = True
    with pytest.raises(IntakeError):
        await h.intake.admit(grant, request)
    h.db.queries.clear()
    found = await observe(h, REF)
    assert found.observation == "observed"
    assert found.intake_ref == (await discover(h)).items[0].intake_ref
    assert "PRIVATE" not in found.model_dump_json()
    assert all(not q.lstrip().startswith(("INSERT", "UPDATE", "DELETE")) for q, _ in h.db.queries)
    assert all("i.ciphertext" not in q and "SELECT *" not in q for q, _ in h.db.queries)


@pytest.mark.asyncio
async def test_all_states_and_renewed_stable_actor_remain_discoverable(setup):
    h = setup
    for index, state in enumerate(("admitted", "dispatching", "reconciling", "started", "rejected")):
        add(h, index, state=state)
    h.sessions.principal = PRINCIPAL.model_copy(
        update={"membership_revision": 2, "session_ref": "renewed-session"}
    )
    page = await discover(h)
    assert len(page.items) == 5 and page.scope == "actor_admissions"
    assert all(set(i.model_dump()) == {"command_id", "intake_ref"} for i in page.items)
    assert len(h.authority.calls) == 10
    assert all(principal.membership_revision == 2 for principal, _ in h.authority.calls)
    assert all("disposition" not in q for q, _ in h.db.queries)


@pytest.mark.asyncio
@pytest.mark.parametrize("field,value", [("subject", "other"), ("issuer", "https://other.test")])
async def test_reused_principal_ref_does_not_replace_original_actor(setup, field, value):
    h = setup
    command, _ = add(h)
    h.sessions.principal = PRINCIPAL.model_copy(update={field: value})
    with pytest.raises(IntakeError, match="operation_forbidden"):
        await observe(h, command)
    with pytest.raises(IntakeError):
        await discover(h)


@pytest.mark.asyncio
async def test_other_principal_or_tenant_cannot_observe_same_command(setup):
    h = setup
    other = PRINCIPAL.model_copy(update={"principal_ref": "other"})
    command, _ = add(h, principal=other)
    assert (await observe(h, command)).observation == "not_observed"
    assert (await discover(h)).items == ()
    h.sessions.principal = PRINCIPAL.model_copy(update={"tenant": "other-tenant"})
    with pytest.raises(IntakeError, match="authentication_unavailable"):
        await observe(h, command)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "component",
    ["events", "outbox", "identities", "key", "ciphertext", "event_digest", "operation", "admission"],
)
async def test_unqualified_matching_row_never_becomes_absence(setup, component):
    h = setup
    command, _ = add(h)
    key = (PRINCIPAL.tenant, command)
    if component in {"events", "outbox", "identities"}:
        getattr(h.db, component).pop(key)
    elif component in {"key", "ciphertext"}:
        h.db.identities[key][component] = "other-key" if component == "key" else b"invalid"
    elif component == "event_digest":
        h.db.events[key]["digest"] = "c" * 64
    else:
        h.db.outbox[key][component] = "wrong"
    for action in (lambda: observe(h, command), lambda: discover(h)):
        with pytest.raises(IntakeError, match="dependency_unavailable"):
            await action()


@pytest.mark.asyncio
async def test_real_keyset_pages_and_cursors_do_not_filter_terminal_rows(setup):
    h = setup
    for i in range(53):
        add(h, i, state="started" if i % 2 else "reconciling")
    first = await discover(h)
    assert len(first.items) == 50 and first.next_cursor is not None
    assert len(h.authority.calls) == 102  # including the lookahead row, twice
    second = await discover(h, first.next_cursor)
    assert len(second.items) == 3 and second.next_cursor is None
    assert not {i.command_id for i in first.items} & {i.command_id for i in second.items}
    assert all(not q.lstrip().startswith(("UPDATE", "DELETE")) for q, _ in h.db.queries)
    assert all("recovery_cursor" in q for q, _ in h.db.queries if q.startswith("INSERT"))


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["actor", "membership", "expiry", "environment"])
async def test_cursor_is_not_transferable_authority(setup, change):
    h = setup
    for i in range(51):
        add(h, i)
    cursor = (await discover(h)).next_cursor
    if change == "actor":
        h.sessions.principal = PRINCIPAL.model_copy(update={"subject": "other"})
    elif change == "membership":
        h.sessions.principal = PRINCIPAL.model_copy(update={"membership_revision": 2})
    elif change == "expiry":
        h.clock[0] = h.authority.until
    else:
        h.db.cursors[cursor]["environment"] = "other"
    with pytest.raises(IntakeError, match="operation_forbidden"):
        await discover(h, cursor)


@pytest.mark.asyncio
async def test_late_commit_is_visible_on_new_observation_not_absence_proof(setup):
    h = setup
    assert (await observe(h, f"{0:032x}")).observation == "not_observed"
    assert (await discover(h)).items == ()
    command, intake = add(h, 0)
    assert (await observe(h, command)).intake_ref == intake
    assert (await discover(h)).items[0].command_id == command
    assert h.db.cursors == {}


@pytest.mark.asyncio
async def test_denied_lookahead_refuses_entire_page(setup):
    h = setup
    for i in range(51):
        _, intake = add(h, i)
    h.authority.deny.add(intake)
    with pytest.raises(IntakeError, match="operation_forbidden"):
        await discover(h)
    assert h.db.cursors == {}


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["store_release", "final_authority", "final_session", "serialization"])
async def test_original_deadline_after_every_final_io(setup, phase):
    h = setup
    command, _ = add(h)
    if phase == "store_release":
        h.db.on_release = lambda: h.clock.__setitem__(0, h.sessions.until)
    elif phase == "final_authority":

        def late_authority():
            if len(h.authority.calls) == 2:
                old = h.authority.until
                h.authority.until += timedelta(seconds=60)
                h.clock[0] = old

        h.authority.on_read = late_authority
    elif phase == "final_session":

        def late_session():
            if h.sessions.calls == 3:
                h.clock[0] = h.authority.until

        h.sessions.on_resolve = late_session

    def freeze(value):
        raw = value.model_dump_json()
        if phase == "serialization":
            h.clock[0] = h.authority.until
        return raw

    with pytest.raises(IntakeError):
        await h.service.observe_frozen("s" * 43, command, freeze=freeze)


def test_composition_reuses_actual_store_and_authority(setup):
    h = setup
    components = AuthIntakeComponents(h.intake, h.native, SimpleNamespace(), SimpleNamespace(), h.authority)
    factory = compose_intake_recovery(components, environment="test")
    built = factory(h.sessions)
    assert built.authority is h.authority and built.store.admission is h.intake
    assert built.store.identity_store is h.native
    h.intake.native_dispatch = None
    with pytest.raises(IntakeError):
        factory(h.sessions)
    with pytest.raises(IntakeError):
        compose_intake_recovery(components, environment="test")


@pytest.mark.asyncio
async def test_unavailable_authority_never_returns_partial_page(setup):
    h = setup
    add(h)

    def unavailable():
        raise RuntimeError("PRIVATE_AUTHORITY_CANARY")

    h.authority.on_read = unavailable
    with pytest.raises(IntakeError) as error:
        await discover(h)
    assert error.value.code == "dependency_unavailable"


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["cursor_release", "page_serialization", "membership_change"])
async def test_page_final_currentness(setup, phase):
    h = setup
    for i in range(51):
        add(h, i)
    if phase == "cursor_release":

        def release():
            if h.db.cursors:
                h.clock[0] = h.authority.until

        h.db.on_release = release
    elif phase == "membership_change":

        def changed():
            if h.sessions.calls == 2:
                h.sessions.principal = PRINCIPAL.model_copy(update={"membership_revision": 2})

        h.sessions.on_resolve = changed

    def freeze(value):
        result = value.model_dump_json()
        if phase == "page_serialization":
            h.clock[0] = h.authority.until
        return result

    with pytest.raises(IntakeError):
        await h.service.discover_frozen("s" * 43, None, freeze=freeze)


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["final_resource", "final_session", "last_session", "early_resource"])
async def test_cursor_retains_every_final_read_ceiling_after_renewal(setup, phase):
    h = setup
    for index in range(51):
        add(h, index)
    shortened = NOW + timedelta(seconds=10)
    if phase in {"final_resource", "early_resource"}:

        def shorten_resource():
            if phase == "final_resource" and len(h.authority.calls) > 51:
                h.authority.until = shortened
            elif phase == "early_resource":
                h.authority.until = shortened if len(h.authority.calls) == 1 else NOW + timedelta(seconds=60)

        h.authority.on_read = shorten_resource
    else:

        def shorten_session():
            if h.sessions.calls >= (2 if phase == "final_session" else 3):
                h.sessions.until = shortened

        h.sessions.on_resolve = shorten_session
    first = await discover(h)
    assert first.next_cursor is not None
    assert h.db.cursors[first.next_cursor]["valid_until"] == shortened
    assert len(h.authority.calls) == 102 and h.sessions.calls == 3
    h.authority.on_read = h.sessions.on_resolve = lambda: None
    h.authority.until = h.sessions.until = NOW + timedelta(seconds=60)
    h.clock[0] = NOW + timedelta(seconds=20)
    with pytest.raises(IntakeError, match="operation_forbidden"):
        await discover(h, first.next_cursor)
    assert len(h.db.cursors) == 1
    assert all(not query.lstrip().startswith(("UPDATE", "DELETE")) for query, _ in h.db.queries)
    assert all("recovery_cursor" in query for query, _ in h.db.queries if query.startswith("INSERT"))


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["cursor_commit", "serialization"])
async def test_shortened_final_ceiling_survives_cursor_io_and_serialization(setup, phase):
    h = setup
    for index in range(51):
        add(h, index)
    shortened = NOW + timedelta(seconds=10)

    def shorten_session():
        if h.sessions.calls == 3:
            h.sessions.until = shortened

    h.sessions.on_resolve = shorten_session

    def expire_and_renew():
        h.clock[0] = shortened
        h.sessions.until = h.authority.until = NOW + timedelta(seconds=60)

    if phase == "cursor_commit":
        h.db.on_release = lambda: expire_and_renew() if h.db.cursors else None

    def freeze(value):
        raw = value.model_dump_json()
        if phase == "serialization":
            expire_and_renew()
        return raw

    with pytest.raises(IntakeError, match="operation_forbidden"):
        await h.service.discover_frozen("s" * 43, None, freeze=freeze)
    assert len(h.db.cursors) == 1  # Technical committed cursor is not a delivered response.
    assert next(iter(h.db.cursors.values()))["valid_until"] == shortened

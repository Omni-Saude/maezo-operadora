"""Unit transaction seams only: none of these assertions qualify PostgreSQL locks/ACLs."""

from contextlib import asynccontextmanager
from datetime import timedelta
from types import SimpleNamespace

import pytest
from tests.unit.gateway.external_cases.test_models import scenario

from maezo.gateway.external_cases.models import ExternalCaseError, Scope, parse
from maezo.gateway.external_cases.postgres import PostgresExternalSourceImporter, validate_locked_session
from maezo.portal.api.records import MembershipRecord, SessionRecord
from maezo.portal.contracts.models import HumanPrincipal, MembershipBinding, SubjectBinding
from maezo.portal.engine.profile import canonicalize


class Result:
    def __init__(self, value):
        self.value = value

    def mappings(self):
        return self

    def one(self):
        return self.value

    def scalar_one(self):
        return self.value


class Connection:
    def __init__(self, engine):
        self.engine = engine
        self.active = False

    def in_transaction(self):
        return self.active

    @property
    def is_active(self):
        return self.active

    async def begin(self):
        self.active = True
        return self

    async def commit(self):
        if self.engine.fail_commit:
            raise RuntimeError("synthetic-provider-private-diagnostic")
        self.engine.commits += 1
        self.active = False

    async def rollback(self):
        self.engine.rollbacks += 1
        self.active = False

    async def close(self):
        self.active = False

    async def invalidate(self):
        self.active = False

    async def execute(self, sql, params=None):
        assert self.active
        sql = str(sql)
        self.engine.calls.append((sql, params, self))
        if "lock_external_ingress_authority" in sql:
            return Result(self.engine.row)
        if "set_config" in sql:
            return Result(None)
        if self.engine.on_write is not None:
            self.engine.on_write()
        return Result(self.engine.receipt)


class Engine:
    dialect = SimpleNamespace(name="postgresql")
    echo = False
    sync_engine = SimpleNamespace(hide_parameters=True)

    def __init__(self, s):
        self.calls = []
        self.commits = 0
        self.rollbacks = 0
        self.fail_commit = False
        self.on_write = None
        self.receipt = canonicalize(s["ingress"])
        self.row = {
            "scope": s["scope"],
            "designation_digest": s["authority"].designation_digest,
            "canonical_designation": canonicalize(s["bundle"]),
            "installation_receipt": canonicalize(s["installation"]),
            "authority_revision": 7,
            "revoked_fingerprints": [],
            "historical_ingress_receipt": None,
        }

    async def connect(self):
        return Connection(self)

    @asynccontextmanager
    async def begin(self):
        connection = Connection(self)
        connection.active = True
        try:
            yield connection
            if self.fail_commit:
                raise RuntimeError("synthetic-provider-private-diagnostic")
            self.commits += 1
        except BaseException:
            self.rollbacks += 1
            raise
        finally:
            connection.active = False


def importer(s, engine, clock=None):
    return PostgresExternalSourceImporter(
        engine,
        parse(Scope, canonicalize(s["scope"])),
        s["root"].public_key(),
        clock=clock or (lambda: s["now"]),
    )


@pytest.mark.asyncio
async def test_signature_under_same_transaction_and_return_after_commit():
    s = scenario()
    engine = Engine(s)
    receipt = await importer(s, engine).accept_source(
        canonicalize(s["packet"]), "ingress-test", expected_revision=None, expected_digest=None
    )
    assert receipt.canonical() == engine.receipt and engine.commits == 1
    assert len({id(c[2]) for c in engine.calls}) == 1
    assert "lock_external_ingress_authority" in engine.calls[1][0]
    assert "accept_external_case_source" in engine.calls[2][0]
    assert not engine.calls[0][2].active


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "attack", ["signature", "revoked", "authority_absent", "aggregate_overflow", "expiry_at_commit"]
)
async def test_no_accept_on_bad_or_stale_locked_authority(attack):
    s = scenario()
    engine = Engine(s)
    clock = [s["now"]]
    if attack == "signature":
        s["packet"]["ownership_proof"]["signature"] = "AA"
    if attack == "revoked":
        engine.row["revoked_fingerprints"] = [s["pin"]]
    if attack == "authority_absent":
        engine.row["canonical_designation"] = None
    if attack == "aggregate_overflow":
        engine.row["revoked_fingerprints"] = [f"{i:064x}" for i in range(1100)]
    if attack == "expiry_at_commit":
        engine.on_write = lambda: clock.__setitem__(0, s["now"] + timedelta(minutes=3))
    with pytest.raises(ExternalCaseError):
        await importer(s, engine, lambda: clock[0]).accept_source(
            canonicalize(s["packet"]), "ingress-test", expected_revision=None, expected_digest=None
        )
    assert engine.commits == 0 and engine.rollbacks == 1
    if attack != "expiry_at_commit":
        assert len(engine.calls) == 2


@pytest.mark.asyncio
async def test_lost_commit_ack_is_uncertainty_without_driver_cause_or_notes():
    s = scenario()
    engine = Engine(s)
    engine.fail_commit = True
    with pytest.raises(ExternalCaseError) as error:
        await importer(s, engine).accept_source(
            canonicalize(s["packet"]), "ingress-test", expected_revision=None, expected_digest=None
        )
    assert error.value.code == "uncertain" and "private" not in str(error.value)
    assert error.value.__cause__ is None and error.value.__context__ is None


@pytest.mark.asyncio
async def test_exact_historical_retry_after_expiry_does_not_reappend():
    s = scenario()
    engine = Engine(s)
    engine.row["historical_ingress_receipt"] = engine.receipt
    engine.row["revoked_fingerprints"] = [s["pin"]]
    result = await importer(s, engine, lambda: s["now"] + timedelta(hours=1)).accept_source(
        canonicalize(s["packet"]), "ingress-test", expected_revision=None, expected_digest=None
    )
    assert result.canonical() == engine.receipt and len(engine.calls) == 2 and engine.commits == 1


def identity_rows():
    s = scenario()
    now = s["now"]
    memberships = (MembershipBinding(membership_ref="membership-test", roles=("beneficiary",), groups=()),)
    bindings = (SubjectBinding(kind="beneficiary", resource_ref="beneficiary-test"),)
    member = MembershipRecord(
        tenant="tenant-test",
        issuer="https://issuer.test",
        subject="subject-test",
        principal_ref="principal-test",
        revision=2,
        audience="beneficiary",
        memberships=memberships,
        subject_bindings=bindings,
        reviewed_until=now + timedelta(minutes=1),
    )
    session = SessionRecord(
        secret_hash="a" * 64,
        session_ref="session-test",
        csrf_token="csrf-test",
        issuer="https://issuer.test",
        subject="subject-test",
        principal_ref="principal-test",
        membership_revision=2,
        authenticated_at=now - timedelta(minutes=1),
        expires_at=now + timedelta(minutes=1),
    )
    principal = HumanPrincipal(
        schema_version=1,
        principal_ref="principal-test",
        issuer="https://issuer.test",
        subject="subject-test",
        tenant="tenant-test",
        membership_revision=2,
        memberships=memberships,
        session_ref="session-test",
        authenticated_at=session.authenticated_at,
        subject_bindings=bindings,
    )
    row = dict(
        session_payload=session.model_dump_json(),
        membership_payload=member.model_dump_json(),
        session_tenant="tenant-test",
        membership_tenant="tenant-test",
        secret_hash="a" * 64,
        expires_at=session.expires_at,
        issuer="https://issuer.test",
        subject="subject-test",
        principal_ref="principal-test",
    )
    return s, row, principal


def test_locked_identity_validation_is_pure_and_exact():
    s, row, principal = identity_rows()
    result = validate_locked_session(
        row,
        tenant="tenant-test",
        issuer="https://issuer.test",
        secret_hash="a" * 64,
        expected=principal,
        now=s["now"],
    )
    assert result.resolved.principal == principal


@pytest.mark.parametrize(
    "column",
    [
        "session_tenant",
        "membership_tenant",
        "secret_hash",
        "issuer",
        "subject",
        "principal_ref",
        "expires_at",
    ],
)
def test_locked_index_payload_mismatch_refuses_without_resolver_reentry(column):
    s, row, principal = identity_rows()
    row[column] = "mismatch"
    with pytest.raises(ExternalCaseError):
        validate_locked_session(
            row,
            tenant="tenant-test",
            issuer="https://issuer.test",
            secret_hash="a" * 64,
            expected=principal,
            now=s["now"],
        )


def test_locked_membership_revision_change_refuses_without_delete_or_self_wait():
    s, row, principal = identity_rows()
    member = MembershipRecord.model_validate_json(row["membership_payload"]).model_copy(
        update={"revision": 3}
    )
    row["membership_payload"] = member.model_dump_json()
    with pytest.raises(ExternalCaseError):
        validate_locked_session(
            row,
            tenant="tenant-test",
            issuer="https://issuer.test",
            secret_hash="a" * 64,
            expected=principal,
            now=s["now"],
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("attack", [None, "digest", "expiry", "missing_native"])
async def test_finalization_consumes_locked_identity_and_returns_only_original_frozen_bytes(attack):
    from maezo.gateway.external_cases.models import instant
    from maezo.gateway.external_cases.postgres import PostgresExternalFinalizationSessionLease
    from maezo.portal.api.auth import digest as secret_digest

    s, row, principal = identity_rows()
    secret = "a" * 43
    session = SessionRecord.model_validate_json(row["session_payload"]).model_copy(
        update={"secret_hash": secret_digest(secret)}
    )
    row.update(secret_hash=secret_digest(secret), session_payload=session.model_dump_json())
    engine = Engine(s)
    engine.receipt = row
    clock = [s["now"]]
    public = canonicalize(
        {
            "schema": "portal-external-case-page.v1",
            "audience": "beneficiary",
            "items": [],
            "next_cursor": None,
            "freshness": {
                "observed_at": instant(s["now"]),
                "valid_until": instant(s["now"] + timedelta(seconds=5)),
            },
        }
    )
    calls = []

    class Native:
        async def finalize(self, **request):
            assert engine.calls[-1][2].active
            assert request["principal"] == principal
            calls.append(request)
            if attack == "expiry":
                clock[0] += timedelta(seconds=6)
            return canonicalize(
                {
                    "schema": "portal-external-case-finalized.v1",
                    "public_projection_digest": "0" * 64
                    if attack == "digest"
                    else request["public_projection_digest"],
                    "valid_until": instant(s["now"] + timedelta(seconds=5)),
                }
            )

    lease = PostgresExternalFinalizationSessionLease(
        engine, "tenant-test", "https://issuer.test", clock=lambda: clock[0]
    )
    if attack is None:
        result = await lease.finalize_frozen(
            secret, principal, continuity_proof="synthetic-proof", frozen_projection=public, native=Native()
        )
        assert result is public and engine.commits == 1 and len(calls) == 1
        assert not engine.calls[-1][2].active
    else:
        with pytest.raises(ExternalCaseError):
            await lease.finalize_frozen(
                secret,
                principal,
                continuity_proof="synthetic-proof",
                frozen_projection=public,
                native=None if attack == "missing_native" else Native(),
            )
        assert engine.commits == 0


def test_locked_payload_duplicate_fields_are_not_resolved_by_last_value():
    s, row, principal = identity_rows()
    row["session_payload"] = '{"subject":"other",' + row["session_payload"][1:]
    with pytest.raises(ExternalCaseError):
        validate_locked_session(
            row,
            tenant="tenant-test",
            issuer="https://issuer.test",
            secret_hash="a" * 64,
            expected=principal,
            now=s["now"],
        )


@pytest.mark.asyncio
async def test_sql_parameter_logging_must_be_disabled_before_private_capture():
    s = scenario()
    engine = Engine(s)
    engine.echo = True
    with pytest.raises(ExternalCaseError):
        await importer(s, engine).accept_source(
            canonicalize(s["packet"]), "ingress-test", expected_revision=None, expected_digest=None
        )
    assert engine.calls == []


@pytest.mark.asyncio
async def test_pure_identity_snapshot_is_not_an_active_database_lease():
    s, row, principal = identity_rows()
    pure = validate_locked_session(
        row,
        tenant="tenant-test",
        issuer="https://issuer.test",
        secret_hash="a" * 64,
        expected=principal,
        now=s["now"],
    )
    with pytest.raises(ExternalCaseError, match="unavailable"):
        pure.current(s["now"])


@pytest.mark.asyncio
async def test_cancelled_import_awaits_rollback_before_connection_reuse():
    import asyncio

    s = scenario()
    engine = Engine(s)

    def cancel():
        raise asyncio.CancelledError()

    engine.on_write = cancel
    with pytest.raises(asyncio.CancelledError):
        await importer(s, engine).accept_source(
            canonicalize(s["packet"]), "ingress-test", expected_revision=None, expected_digest=None
        )
    assert engine.rollbacks == 1 and not engine.calls[-1][2].active


@pytest.mark.asyncio
async def test_returned_ingress_must_match_source_coordinates_before_commit():
    s = scenario()
    engine = Engine(s)
    changed = dict(s["ingress"], source_ref="other-source")
    engine.receipt = canonicalize(changed)
    with pytest.raises(ExternalCaseError):
        await importer(s, engine).accept_source(
            canonicalize(s["packet"]), "ingress-test", expected_revision=None, expected_digest=None
        )
    assert engine.commits == 0 and engine.rollbacks == 1


@pytest.mark.asyncio
async def test_ingress_rolls_back_if_original_packet_cannot_fit_required_publication_envelope():
    from maezo.gateway.external_cases.models import digest

    s = scenario()
    source = s["packet"]["statement"]
    index = 0
    while len(canonicalize(s["packet"])) < 64800:
        source["owners"].append(
            {"kind": "beneficiary", "resource_ref": "owner" + str(index).zfill(4) + "x" * 245}
        )
        index += 1
    s["packet"]["ownership_proof"] = s["proof"](source, "ownership")
    raw = canonicalize(s["packet"])
    assert len(raw) <= 65536
    s["ingress"].update(source_digest=digest(s["packet"]), request_digest=digest(s["packet"]))
    engine = Engine(s)
    with pytest.raises(ExternalCaseError):
        await importer(s, engine).accept_source(
            raw, "ingress-test", expected_revision=None, expected_digest=None
        )
    assert engine.commits == 0 and engine.rollbacks == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "sqlstate,classification",
    [
        ("P7E01", "invalid"),
        ("P7E02", "conflict"),
        ("P7E03", "unavailable"),
        ("P7E04", "denied"),
        ("unknown", "uncertain"),
    ],
)
async def test_closed_sql_error_codes_preserve_caller_contract_without_provider_context(
    sqlstate, classification
):
    s = scenario()
    engine = Engine(s)
    provider = RuntimeError("synthetic-private-provider-detail")
    provider.orig = SimpleNamespace(sqlstate=sqlstate)
    provider.add_note("synthetic-private-provider-note")

    def fail():
        raise provider

    engine.on_write = fail
    with pytest.raises(ExternalCaseError) as error:
        await importer(s, engine).accept_source(
            canonicalize(s["packet"]), "ingress-test", expected_revision=None, expected_digest=None
        )
    assert error.value.code == classification
    assert error.value.__context__ is None and error.value.__cause__ is None
    assert not hasattr(error.value, "__notes__") and "private" not in str(error.value)
    assert engine.commits == 0 and engine.rollbacks == 1

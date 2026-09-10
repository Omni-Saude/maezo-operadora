"""Offline transaction seam controls; explicitly not PostgreSQL/runtime proof."""

import copy
import hashlib
import json
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from tests.unit.portal.test_human_session import membership
from tests.unit.portal.test_intake_documents import REF, REF2, submission

from maezo.gateway.intake.models import AdmissionGrant, IntakeError, request_bytes
from maezo.gateway.intake.postgres import PostgresIntakeStore
from maezo.portal.contracts.intake import AuthIntakeSubmission
from maezo.portal.contracts.models import HumanPrincipal
from maezo.portal.engine.profile import canonicalize


class Database:
    def __init__(self):
        self.rows = []
        self.events = []
        self.fail_event = False
        self.lose_ack = False
        self.locks = []

    @asynccontextmanager
    async def transaction(self, engine, seconds):
        before = copy.deepcopy((self.rows, self.events))
        try:
            yield self
        except Exception:
            self.rows, self.events = before
            raise
        if self.lose_ack:
            raise RuntimeError("PRIVATE_COMMIT_FAILURE_CANARY")

    async def execute(self, sql, values):
        query = str(sql)
        row = None
        if "pg_advisory" in query:
            self.locks.append(values["key"])
        elif query.startswith("SELECT"):
            for candidate in self.rows:
                if all(
                    candidate[key] == values[key]
                    for key in values
                    if key in {"tenant", "principal", "command", "guide"} and ":" + key in query
                ):
                    row = dict(
                        candidate,
                        intake_ref=candidate["intake"],
                        command_id=candidate["command"],
                        guide_identity_ref=candidate["guide"],
                        request_digest=candidate["digest"],
                        revision=0,
                        disposition="admitted",
                        case_ref=None,
                        start_receipt_ref=None,
                    )
        elif "INSERT INTO portal_intake.admission_event" in query:
            if self.fail_event:
                raise RuntimeError("PRIVATE_AUDIT_FAILURE_CANARY")
            self.events.append(dict(values))
        elif "INSERT INTO portal_intake.intake" in query:
            self.rows.append(dict(values))
        return SimpleNamespace(first=lambda: row, mappings=lambda: SimpleNamespace(one_or_none=lambda: row))


def inputs():
    request = AuthIntakeSubmission.model_validate_json(json.dumps(submission()))
    principal = HumanPrincipal(
        schema_version=1,
        principal_ref="synthetic-principal",
        issuer="https://idp.example.test",
        subject="synthetic-subject",
        tenant="synthetic-tenant",
        membership_revision=1,
        memberships=membership().memberships,
        session_ref="synthetic-session",
        authenticated_at=datetime.now(UTC),
        subject_bindings=(),
    )
    grant = AdmissionGrant(
        principal=principal,
        request_digest=hashlib.sha256(request_bytes(request)).hexdigest(),
        guide_identity_ref=REF,
        authority_receipt_ref=REF2,
        authority_digest="a" * 64,
        valid_until=datetime.now(UTC) + timedelta(minutes=1),
    )
    return grant, request


@pytest.mark.asyncio
async def test_commit_retry_and_encrypted_payload(monkeypatch):
    db = Database()
    monkeypatch.setattr("maezo.gateway.intake.postgres.transaction", db.transaction)
    store = PostgresIntakeStore(
        None, tenant="synthetic-tenant", key_id="synthetic-key", encryption_key=b"a" * 32
    )
    grant, request = inputs()
    receipt = await store.admit(grant, request)
    again = await store.admit(grant, request)
    assert receipt == again and receipt.disposition == "admitted"
    assert len(db.rows) == len(db.events) == 1
    row = db.rows[0]
    aad = {key: value for key, value in row.items() if key not in {"nonce", "ciphertext"}}
    assert AESGCM(b"a" * 32).decrypt(row["nonce"], row["ciphertext"], canonicalize(aad)) == request_bytes(
        request
    )
    assert b"900719925474099312345" not in row["ciphertext"]
    assert len(db.locks) == 4 and db.locks[:2] == sorted(db.locks[:2])


@pytest.mark.asyncio
async def test_audit_failure_rolls_back_admission_and_sanitizes(monkeypatch):
    db = Database()
    db.fail_event = True
    monkeypatch.setattr("maezo.gateway.intake.postgres.transaction", db.transaction)
    store = PostgresIntakeStore(
        None, tenant="synthetic-tenant", key_id="synthetic-key", encryption_key=b"a" * 32
    )
    with pytest.raises(IntakeError) as error:
        await store.admit(*inputs())
    assert db.rows == db.events == []
    assert "PRIVATE" not in str(error.value)


@pytest.mark.asyncio
async def test_unknown_commit_recovery_never_creates_second_intake(monkeypatch):
    db = Database()
    db.lose_ack = True
    monkeypatch.setattr("maezo.gateway.intake.postgres.transaction", db.transaction)
    store = PostgresIntakeStore(
        None, tenant="synthetic-tenant", key_id="synthetic-key", encryption_key=b"a" * 32
    )
    grant, request = inputs()
    with pytest.raises(IntakeError):
        await store.admit(grant, request)
    assert len(db.rows) == len(db.events) == 1
    db.lose_ack = False
    result = await store.admit(grant, request)
    assert result.intake_ref == db.rows[0]["intake"]
    assert len(db.rows) == len(db.events) == 1


@pytest.mark.asyncio
async def test_changed_payload_and_new_command_same_guide_conflict(monkeypatch):
    db = Database()
    monkeypatch.setattr("maezo.gateway.intake.postgres.transaction", db.transaction)
    store = PostgresIntakeStore(
        None, tenant="synthetic-tenant", key_id="synthetic-key", encryption_key=b"a" * 32
    )
    grant, request = inputs()
    await store.admit(grant, request)
    for changes in ({"valor_estimado_centavos": "1"}, {"command_id": REF2}):
        changed = AuthIntakeSubmission.model_validate(dict(request.model_dump(), **changes))
        new_grant = AdmissionGrant.model_validate(
            dict(grant.model_dump(), request_digest=hashlib.sha256(request_bytes(changed)).hexdigest())
        )
        with pytest.raises(IntakeError) as error:
            await store.admit(new_grant, changed)
        assert error.value.code == "conflict"
    assert len(db.rows) == len(db.events) == 1

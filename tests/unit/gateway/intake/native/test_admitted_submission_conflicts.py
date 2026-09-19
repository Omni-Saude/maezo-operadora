"""`admitted_submission`'s conflict fences are CONFLICTS, not dependency outages.

The reader raises `ExternalCaseError("conflict")` on both fences it owns — attested digest vs
plaintext, and sealed submission vs the command queried — but `ExternalCaseError(ValueError)` is
not an `IntakeError`, so the residual broad clause translated both into
`IntakeError("dependency_unavailable")`: a tampered or swapped row read as a database incident and
was retried forever, while the conflict the store meant to surface was unobservable. `admit` in
the SAME module translates `ExternalCaseError` by code; the reader does the same for the one code
it can produce.

Offline: the real store over a scripted SQL seam, no database.
"""

import asyncio
import hashlib
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from maezo.gateway.intake.models import IntakeError, request_bytes
from maezo.gateway.intake.postgres import PostgresIntakeStore
from maezo.portal.contracts.intake import AuthIntakeSubmission
from maezo.portal.engine.profile import canonicalize

NOW = datetime(2026, 9, 19, 12, 0)
KEY = b"k" * 32
PRINCIPAL = "principal-000000001"
COMMAND = "command-0000000001"
OTHER_COMMAND = "command-0000000002"
REF = "reference-00000001"


def submission(command_id: str = COMMAND) -> AuthIntakeSubmission:
    return AuthIntakeSubmission(
        command_id=command_id,
        beneficiary_ref=REF,
        provider_ref=REF,
        guide_ref=REF,
        codigo_procedimento_tuss="40101010",
        categoria_procedimento="consulta",
        carater_atendimento="eletivo",
        valor_estimado_centavos="1",
        document_refs=(),
    )


def sealed_row(request: AuthIntakeSubmission, *, attested_digest: str | None = None) -> dict:
    """The row the reader reads: sealed plaintext + the digest the admission attested.

    `attested_digest` builds the tampered-plaintext case: the AAD and the row both carry the
    digest the admission ATTESTED, so decryption succeeds and the digest fence — not the cipher —
    is what must refuse.
    """
    store = PostgresIntakeStore(None, tenant="tenant", key_id="key", encryption_key=KEY)
    raw = request_bytes(request)
    digest = attested_digest if attested_digest is not None else hashlib.sha256(raw).hexdigest()
    row = {
        "tenant": "tenant",
        "principal_ref": PRINCIPAL,
        "command_id": COMMAND,
        "intake_ref": "intake-0000000001",
        "guide_identity_ref": "guide-00000000001",
        "request_digest": digest,
        "authority_receipt_ref": "authority-0000001",
        "authority_digest": "a" * 64,
        "key_id": "key",
    }
    aad = canonicalize(
        {
            "tenant": row["tenant"],
            "principal": row["principal_ref"],
            "command": row["command_id"],
            "intake": row["intake_ref"],
            "guide": row["guide_identity_ref"],
            "digest": row["request_digest"],
            "authority_ref": row["authority_receipt_ref"],
            "authority_digest": row["authority_digest"],
            "key_id": row["key_id"],
        }
    )
    nonce = bytes(12)
    row["nonce"] = nonce
    row["ciphertext"] = store._cipher.encrypt(nonce, raw, aad)
    return row


def mount(monkeypatch, row: dict) -> None:
    class Seam:
        @asynccontextmanager
        async def transaction(self, engine, seconds):
            yield self

        async def execute(self, sql, values):
            return SimpleNamespace(mappings=lambda: SimpleNamespace(one=lambda: dict(row)))

    monkeypatch.setattr("maezo.gateway.intake.postgres.transaction", Seam().transaction)


def store() -> PostgresIntakeStore:
    return PostgresIntakeStore(None, tenant="tenant", key_id="key", encryption_key=KEY)


@pytest.mark.asyncio
async def test_a_valid_row_still_reads_back(monkeypatch):
    """Control: the AAD round-trip the two fences guard is intact."""
    mount(monkeypatch, sealed_row(submission()))
    read = await store().admitted_submission(PRINCIPAL, COMMAND)
    assert (read.command_id, read.guide_ref) == (COMMAND, REF)


@pytest.mark.asyncio
async def test_a_tampered_plaintext_is_conflict_not_a_dependency_outage(monkeypatch):
    """Fence 1: the plaintext does not meet the digest the admission attested."""
    mount(monkeypatch, sealed_row(submission(), attested_digest="f" * 64))
    with pytest.raises(IntakeError) as error:
        await store().admitted_submission(PRINCIPAL, COMMAND)
    assert error.value.code == "conflict", error.value.code


@pytest.mark.asyncio
async def test_a_submission_sealed_for_another_command_is_conflict_not_an_outage(monkeypatch):
    """Fence 2: the sealed plaintext belongs to a command this row does not admit."""
    mount(monkeypatch, sealed_row(submission(OTHER_COMMAND)))
    with pytest.raises(IntakeError) as error:
        await store().admitted_submission(PRINCIPAL, COMMAND)
    assert error.value.code == "conflict", error.value.code


@pytest.mark.asyncio
async def test_an_unreadable_row_stays_a_dependency_outage(monkeypatch):
    """The residual clause keeps its job: anything the store cannot prove stays fail-closed."""
    row = sealed_row(submission())
    row["key_id"] = "rotated-key-000001"
    mount(monkeypatch, row)
    with pytest.raises(IntakeError) as error:
        await store().admitted_submission(PRINCIPAL, COMMAND)
    assert error.value.code == "dependency_unavailable", error.value.code

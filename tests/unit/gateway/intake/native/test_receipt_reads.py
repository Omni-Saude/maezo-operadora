"""The two receipt reads the drain depends on, against the REAL `PostgresAuthDispatchStore`.

Two reads, two contracts (`native_store.py`):

* `completed(command)` — the strict read: it compares the row's SEALED command for every state, so
  a row that carries no sealed command yet can only answer `AuthUnavailableError` (unsealing a
  NULL triple cannot succeed and the store refuses to guess). This is the contract the engine's
  own dispatch path consumes (`native_dispatch.py:156`) and it must NOT loosen.
* `settled(command)` — the drain's pre-send read (`runtime/intake_dispatch/service.py`): answers
  `None` for a row that cannot carry a receipt yet — unsealed ⇒ not executed ⇒ `receipt_digest`
  NULL, all three schema CHECKs (`native_schema.sql`) — and behaves exactly like `completed` for
  every sealed row, failing closed on any drift it can prove.

Offline like `test_dispatch.py`: the real store over a scripted SQL seam, no database.
"""

from copy import deepcopy
from datetime import timedelta

import pytest

from maezo.gateway.human.auth_transport import AuthUnavailableError
from maezo.gateway.human.read_profile import digest
from maezo.gateway.intake.native_store import PostgresAuthDispatchStore
from tests.unit.gateway.intake.native.test_dispatch import SQLSeam
from tests.unit.gateway.intake.native.test_wire_transport import NOW, command, receipt


def sealed_row(monkeypatch, *, state: str = "admitted", with_receipt: bool = False, corrupt: bool = False):
    """A REAL store over ONE scripted row; the command is sealed into it only on request."""
    now = [NOW]
    store = PostgresAuthDispatchStore(None, tenant="tenant", key_id="key", key=b"k" * 32, clock=lambda: now[0])
    c = command()
    row = dict(
        command_id=c.command_id,
        operation="auth.start",
        command_digest=None,
        command_nonce=None,
        command_ciphertext=None,
        key_id="key",
        state=state,
        generation=0,
        revision=0,
        owner_digest=None,
        lease_until=None,
        authorization_until=NOW + timedelta(seconds=60),
        receipt_digest=None,
        receipt_nonce=None,
        receipt_ciphertext=None,
    )
    if state != "admitted" or with_receipt:
        nonce, ciphertext = store.seal("command", c.command_id, c)
        row.update(command_digest=digest(c), command_nonce=nonce, command_ciphertext=ciphertext)
    if with_receipt:
        r = receipt(c)
        r_nonce, r_ciphertext = store.seal("receipt", c.command_id, r)
        if corrupt:
            r_ciphertext = bytes([r_ciphertext[0] ^ 0xFF]) + r_ciphertext[1:]
        row.update(
            state="executed",
            receipt_digest=digest(r),
            receipt_nonce=r_nonce,
            receipt_ciphertext=r_ciphertext,
        )
    db = SQLSeam(row, now)
    monkeypatch.setattr("maezo.gateway.intake.native_store.transaction", db.transaction)
    return store, db, c


@pytest.mark.asyncio
async def test_completed_fails_closed_for_a_row_nothing_sealed_yet(monkeypatch):
    """The landed strict contract, unpinned until now: an unsealed row is NOT a readable `None`."""
    store, _db, c = sealed_row(monkeypatch)
    with pytest.raises(AuthUnavailableError):
        await store.completed(c)


@pytest.mark.asyncio
async def test_settled_answers_none_for_a_row_nothing_sealed_yet(monkeypatch):
    """The drain's pre-send read: a fresh `admitted` row cannot carry a receipt — answer the fact."""
    store, _db, c = sealed_row(monkeypatch)
    assert await store.settled(c) is None


@pytest.mark.asyncio
async def test_settled_agrees_with_completed_on_a_sealed_row_without_receipt(monkeypatch):
    store, _db, c = sealed_row(monkeypatch, state="claimed")
    assert await store.settled(c) is None
    assert await store.completed(c) is None


@pytest.mark.asyncio
async def test_settled_returns_the_bound_receipt_of_an_executed_row(monkeypatch):
    store, _db, c = sealed_row(monkeypatch, with_receipt=True)
    settled, completed = await store.settled(c), await store.completed(c)
    assert settled is not None and settled == completed
    assert (settled.command_id, settled.outcome) == (c.command_id, "started")


@pytest.mark.asyncio
async def test_settled_fails_closed_when_the_sealed_command_is_not_the_one_presented(monkeypatch):
    store, _db, c = sealed_row(monkeypatch, state="claimed")
    other = command().model_copy(update={"command_id": "command-2"})
    assert other != c
    with pytest.raises(AuthUnavailableError):
        await store.settled(other)
    with pytest.raises(AuthUnavailableError):
        await store.completed(other)


@pytest.mark.asyncio
async def test_settled_fails_closed_when_the_receipt_ciphertext_is_corrupt(monkeypatch):
    store, _db, c = sealed_row(monkeypatch, with_receipt=True, corrupt=True)
    with pytest.raises(AuthUnavailableError):
        await store.settled(c)
    with pytest.raises(AuthUnavailableError):
        await store.completed(c)


@pytest.mark.asyncio
async def test_the_row_lock_is_taken_by_both_reads(monkeypatch):
    """Both reads serialise on `SELECT ... FOR UPDATE` — a receipt answer is never a race guess."""
    store, db, c = sealed_row(monkeypatch, state="claimed")
    await store.settled(c)
    await store.completed(c)
    assert len(db.events) == 2
    assert all("FOR UPDATE" in event for event in db.events)


@pytest.mark.asyncio
async def test_the_reads_never_leak_exception_text(monkeypatch):
    store, _db, c = sealed_row(monkeypatch, state="claimed")
    other = command().model_copy(update={"command_id": "command-2"})
    with pytest.raises(AuthUnavailableError) as error:
        await store.settled(other)
    assert "conflict" not in str(error.value)
    assert deepcopy(str(error.value)) == "human_auth_dependency_unavailable"

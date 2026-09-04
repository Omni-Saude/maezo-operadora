"""LIVE-Postgres proof: the Helena WEBHOOK DISPATCH PATH persists + resumes durable conversation
state across invocations and receiver restarts (T4b — closing the T3.4/F4 boundary #165 left in
`dispatch.py`).

Tier 2 (`@pytest.mark.integration`), placed HERE (not under `tests/integration/`) for the SAME
reason as `tests/unit/runtime/agent_runtime/test_checkpoint_live_pg.py`: it needs a REAL Postgres
but explicitly NOT the CIB Seven engine. The LLM + DMN + engine are Fakes (deterministic, no
network); the CHECKPOINTER is a REAL `AsyncPostgresSaver` — so this proves the dispatch path's
durable-persistence wiring, not Helena's business logic (that is covered by the mock-only suite).

Proves what the in-memory unit suite cannot:
  1. Two sequential `HelenaDispatcher.dispatch(...)` calls with the SAME conversation identity
     accumulate checkpoint history on ONE thread in real Postgres (turn 2 resumes turn 1).
  2. RESTART-RESUME: after `aclose()` (simulated receiver death) a FRESH checkpointer on the same
     DB still recovers the persisted turn-2 state.
  3. A DIFFERENT identity gets its own independent thread.
  4. PHI (LGPD): every `thread_id` in the checkpoint tables is a hashed `wa:{tenant}:{phone_hash}`
     id — never a raw phone number (SELECT proof).

Skips LOUDLY (never errors, never fakes) when no Postgres is reachable — see `_dsn` below for
why the default now points at the compose stack (gap LIVE-SUITES-SILENT-SKIP-AUDIT).
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator
from typing import Any

import pytest

from maezo.gateway.pseudonymizer import Pseudonymizer
from maezo.platform.webhooks.whatsapp.dispatch import HelenaDispatcher, InboundMessage
from maezo.platform.webhooks.whatsapp.security import hash_phone
from maezo.runtime.checkpoint import Checkpointer, checkpoint_thread_config
from maezo.tools.mcp_cibseven.transport import FakeCibSevenTransport
from maezo.tools.workers.dmn_transport import FakeDmnTransport
from tests.support.audit_fakes import FakeStartAuditSink

pytestmark = pytest.mark.integration

_TENANT = "amh"
_PHONE_A = "5511999990001"
_PHONE_B = "5511999990002"


def _dsn() -> str:
    """`MAEZO_TEST_CHECKPOINT_DATABASE_URL` wins; otherwise the compose Postgres.

    Gap LIVE-SUITES-SILENT-SKIP-AUDIT (2026-09-04): the fallback used to be
    `postgresql://ckpt:ckpt@localhost:5663/ckpt` — "a FREE, dedicated Postgres (deliberately not
    the compose stack's 5432/5433)" whose role, database AND port nothing in this repo has ever
    created or published, so this file's single test reported "COULD NOT VERIFY" in every
    environment that has ever run it (a default nobody serves is a silent skip, not a proof). Now
    it mirrors `tests/integration/conftest.py::_audit_pg_dsn`: the local compose stack
    (`${MAEZO_PG_HOST_PORT:-5433}`) or a CI job that pins `MAEZO_PG_HOST_PORT=5432`. Note this is
    the SAME variable and now the SAME default as
    `tests/unit/runtime/agent_runtime/test_checkpoint_live_pg.py`, i.e. the two suites can share
    one `checkpoints` table — which is why the PHI proof below is expressed over the threads THIS
    test creates plus a raw-phone scan of the whole table, never over "every row must match this
    file's own naming convention".
    """
    explicit = os.environ.get("MAEZO_TEST_CHECKPOINT_DATABASE_URL")
    if explicit:
        return explicit
    port = os.environ.get("MAEZO_PG_HOST_PORT", "5433")
    return f"postgresql://maezo:maezo@localhost:{port}/maezo"


async def _postgres_reachable(dsn: str) -> bool:
    import asyncpg  # type: ignore[import-untyped]

    from maezo.gateway.audit_postgres import normalize_dsn

    try:
        conn = await asyncio.wait_for(asyncpg.connect(normalize_dsn(dsn)), timeout=2.0)
    except Exception:  # noqa: BLE001 — any connection failure means "skip", not "error"
        return False
    await conn.close()
    return True


@pytest.fixture(scope="module")
def pg_dsn() -> str:
    dsn = _dsn()
    if not asyncio.run(_postgres_reachable(dsn)):
        pytest.skip(
            f"COULD NOT VERIFY: Postgres not reachable at {dsn!r} (override with "
            "MAEZO_TEST_CHECKPOINT_DATABASE_URL / MAEZO_PG_HOST_PORT). Bring the project's own "
            "stack up with `docker compose --profile core up -d postgres` and re-run this file; "
            "the dispatch path provisions the checkpoint tables via the saver's own awaited "
            "setup() — no migrations needed."
        )
    return dsn


def _known_thread_ids() -> tuple[str, str]:
    """The exact two `wa:{tenant}:{phone_hash}` thread ids this file's test will write.

    Deterministic on purpose: `_PHONE_A`/`_PHONE_B` are fixed constants and `Pseudonymizer()`'s
    bare constructor uses the non-secret, deterministic dev/CI fallback key
    (`pseudonymizer.py::_DEV_FALLBACK_ROOT`) — the SAME key `_dispatcher()` below injects into
    every `HelenaDispatcher` this file builds — so `hash_phone` over them returns the SAME two
    ids on every invocation of this process, this file, forever. That determinism is exactly why
    `_clean_known_threads` (below) must own their lifecycle independently of the test body: see
    its docstring.
    """
    p = Pseudonymizer()
    return (
        f"wa:{_TENANT}:{hash_phone(_PHONE_A, _TENANT, p)}",
        f"wa:{_TENANT}:{hash_phone(_PHONE_B, _TENANT, p)}",
    )


async def _delete_known_threads(dsn: str) -> None:
    ck = await Checkpointer.connect_and_setup(dsn)
    try:
        saver = ck.saver
        assert saver is not None, "Checkpointer.connect_and_setup always attaches a saver"
        for tid in _known_thread_ids():
            await saver.adelete_thread(tid)
    finally:
        await ck.aclose()


@pytest.fixture(autouse=True)
async def _clean_known_threads(pg_dsn: str) -> AsyncIterator[None]:
    """Own this suite's two deterministic thread ids end-to-end — not the test body.

    Root cause fixed here (gap LIVE-SUITES-SILENT-SKIP-AUDIT REVISE #2, gatekeeper probes F1-F4,
    2026-09-04): `_PHONE_A`/`_PHONE_B` are FIXED constants, so `_known_thread_ids()` is the SAME
    two thread ids on every run of this file (see its docstring). The test's own cleanup
    (`adelete_thread` in the RESTART-RESUME block) sits AFTER the first `try/finally` — a failure
    anywhere before it (including inside `_assert_no_raw_phone_in_checkpoint_tables`) hits that
    `finally`'s `ck.aclose()` and the exception propagates straight OUT of the test function, so
    the second block's cleanup is never reached and both threads survive in `checkpoints`. On the
    NEXT run, `pre_existing_threads` (computed inside the test, before it writes anything) then
    already contains both ids, so `written_here` in `_assert_no_raw_phone_in_checkpoint_tables`
    comes back empty and `assert written_here` fails FOREVER — a false-RED, not a real one: F4
    proved `main`'s whole-table `assert thread_ids` self-heals on the identical dirty DB, because
    it does not scope to a delta.

    An `autouse` fixture's teardown, unlike the test's own nested `try/finally`, ALWAYS runs —
    red or green. Deleting both known ids BEFORE the test makes `pre_existing_threads` a TRUE
    baseline regardless of what a previous run left behind, and deleting them again AFTER (in the
    `finally` below) leaves `checkpoints` clean of this suite's rows either way. Neither the
    GLOBAL raw-phone claim nor the DELTA convention claim inside the test changes one byte: this
    fixture only clears the starting line, it proves nothing itself.
    """
    await _delete_known_threads(pg_dsn)
    try:
        yield
    finally:
        await _delete_known_threads(pg_dsn)


class _FakeInference:
    def __init__(self, responses: list[str]) -> None:
        self._responses = list(responses)

    async def generate(
        self,
        prompt: str,
        *,
        phi: bool = False,
        agent_id: str | None = None,
        tenant_id: str | None = None,
    ) -> str:
        return self._responses.pop(0) if self._responses else ""


class _FakeWhatsAppClient:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []

    async def send_message(self, to: str, text: str) -> dict[str, Any]:
        self.sent.append((to, text))
        return {"messages": [{"id": "wamid.reply"}]}


def _dispatcher(checkpointer: Checkpointer, *, turns: int) -> HelenaDispatcher:
    responses: list[str] = []
    for _ in range(turns):
        responses.append('{"intent": "information", "population": "none", "psychosocial_risk": false}')
        responses.append("resposta")
    dmn = FakeDmnTransport()
    dmn.register("triage_redflag_adult", [{"red_flag": False, "conduta": "CONTINUE"}] * turns)
    return HelenaDispatcher(
        tenant_id=_TENANT,
        inference=_FakeInference(responses),  # type: ignore[arg-type]
        dmn=dmn,
        cibseven=FakeCibSevenTransport(),
        whatsapp_client=_FakeWhatsAppClient(),  # type: ignore[arg-type]
        pseudonymizer=Pseudonymizer(),
        audit_sink=FakeStartAuditSink(),
        checkpointer=checkpointer,
    )


async def test_dispatch_multi_turn_persist_resume_restart_and_phi(pg_dsn: str) -> None:
    ck = await Checkpointer.connect_and_setup(pg_dsn)
    conv_a: str | None = None
    conv_b: str | None = None
    # Threads that already existed before this test wrote anything (the saver's tables are shared
    # with `tests/unit/runtime/agent_runtime/test_checkpoint_live_pg.py`, which resolves the SAME
    # `MAEZO_TEST_CHECKPOINT_DATABASE_URL` / compose default — see `_dsn`). The convention check
    # below is scoped to the DELTA, so a foreign row can never turn this test red for a claim it
    # is not making; the raw-phone scan stays global, because "no raw phone anywhere in this
    # table" is a claim that holds for every writer.
    pre_existing_threads = await _thread_ids(pg_dsn)
    try:
        dispatcher = _dispatcher(ck, turns=6)

        # TURN 1 (identity A) — persists a checkpoint on a hashed thread.
        r1 = await dispatcher.dispatch(InboundMessage(from_number=_PHONE_A, text="oi", message_id="m1"))
        conv_a = r1["conversation_id"]
        cfg_a = checkpoint_thread_config(conv_a)
        assert conv_a.startswith("wa:amh:")
        assert _PHONE_A not in conv_a  # hashed, never raw
        assert await ck.saver.aget_tuple(cfg_a) is not None  # type: ignore[union-attr]
        hist1 = [c async for c in ck.saver.alist(cfg_a)]  # type: ignore[union-attr]

        # TURN 2 (identity A) — SAME thread, resumes; checkpoint history grows.
        r2 = await dispatcher.dispatch(InboundMessage(from_number=_PHONE_A, text="de novo", message_id="m2"))
        assert r2["conversation_id"] == conv_a
        hist2 = [c async for c in ck.saver.alist(cfg_a)]  # type: ignore[union-attr]
        assert len(hist2) > len(hist1), "second turn did not accumulate durable checkpoint history"

        # TURN 3 (identity B) — independent, fresh thread.
        r3 = await dispatcher.dispatch(InboundMessage(from_number=_PHONE_B, text="oi", message_id="m3"))
        conv_b = r3["conversation_id"]
        assert conv_b != conv_a
        hist_b = [c async for c in ck.saver.alist(checkpoint_thread_config(conv_b))]  # type: ignore[union-attr]
        assert len(hist_b) < len(hist2)  # fresh identity, not A's 2-turn history

        # PHI (LGPD) SELECT proof, read back out of Postgres (never from the objects in hand).
        await _assert_no_raw_phone_in_checkpoint_tables(pg_dsn, pre_existing_threads)
    finally:
        await ck.aclose()  # simulate receiver death — pool torn down

    # RESTART-RESUME: a FRESH checkpointer on the SAME DB recovers turn-2 state.
    ck2 = await Checkpointer.connect_and_setup(pg_dsn)
    try:
        assert conv_a is not None
        cfg_a = checkpoint_thread_config(conv_a)
        recovered = await ck2.saver.aget_tuple(cfg_a)  # type: ignore[union-attr]
        assert recovered is not None, "conversation state did not survive receiver restart"
        assert recovered.config["configurable"]["thread_id"] == conv_a
    finally:
        # Clean up both threads so re-runs stay isolated.
        for conv in (conv_a, conv_b):
            if conv is not None:
                await ck2.saver.adelete_thread(conv)  # type: ignore[union-attr]
        await ck2.aclose()


async def _thread_ids(dsn: str) -> set[str]:
    """Every distinct `thread_id` currently persisted, read straight out of Postgres."""
    import asyncpg  # type: ignore[import-untyped]

    from maezo.gateway.audit_postgres import normalize_dsn

    conn = await asyncpg.connect(normalize_dsn(dsn))
    try:
        rows = await conn.fetch("SELECT DISTINCT thread_id FROM checkpoints")
    finally:
        await conn.close()
    return {r["thread_id"] for r in rows}


async def _assert_no_raw_phone_in_checkpoint_tables(dsn: str, pre_existing: set[str]) -> None:
    """Two SELECT-backed claims, deliberately scoped differently.

    1. GLOBAL — no raw phone number appears in ANY `thread_id`, whoever wrote it. This is the LGPD
       claim itself and it holds for every writer, so it is asserted over the whole table.
    2. DELTA — every thread this dispatch path created during this test follows the hashed
       `wa:{tenant}:{phone_hash}` convention. Scoping this one to the rows that appeared since the
       test started is what keeps it honest: the table is shared with
       `tests/unit/runtime/agent_runtime/test_checkpoint_live_pg.py` (same env var, same default
       DSN since gap LIVE-SUITES-SILENT-SKIP-AUDIT), and a sibling suite's thread is not evidence
       about THIS path. Before the scoping, a foreign row would have failed this test for a claim
       it never made — and, worse, an ADDITIONAL non-conventional thread written by the dispatch
       path itself would still be caught, because it lands in the delta.
    """
    persisted = await _thread_ids(dsn)
    for tid in persisted:
        assert _PHONE_A not in tid and _PHONE_B not in tid, f"RAW PHONE in checkpoint thread_id: {tid!r}"
    written_here = persisted - pre_existing
    assert written_here, "expected at least one checkpoint thread written by this test"
    for tid in written_here:
        assert tid.startswith("wa:amh:"), f"non-convention thread_id leaked: {tid!r}"
